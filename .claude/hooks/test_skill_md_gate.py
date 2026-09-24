"""skill-md-gate 的拦截逻辑测试。纯逻辑，用假转录跑。"""

import json
import pathlib
import subprocess
import sys

import pytest

HOOK = pathlib.Path(__file__).parent / "skill-md-gate.py"
sys.path.insert(0, str(pathlib.Path(__file__).parent))

import importlib.util

_spec = importlib.util.spec_from_file_location("skill_md_gate", HOOK)
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


def transcript(tmp_path, read_paths):
    """造一份只含 Read 调用的假转录。"""
    lines = []
    for target in read_paths:
        lines.append(json.dumps({"message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {"file_path": target}}]}}))
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


BOTH = ["/repo/.claude/rules/skill-authoring.md", "/repo/.claude/rules/zh-writing.md"]


def test_new_file_without_rules_is_denied(tmp_path):
    assert gate.should_deny("skill/x/SKILL.md", transcript(tmp_path, []), exists=False)


def test_new_file_with_one_rule_read_is_denied(tmp_path):
    assert gate.should_deny("skill/x/SKILL.md", transcript(tmp_path, BOTH[:1]), exists=False)


def test_new_file_with_both_rules_read_passes(tmp_path):
    assert not gate.should_deny("skill/x/SKILL.md", transcript(tmp_path, BOTH), exists=False)


def test_existing_file_is_never_denied(tmp_path):
    """改存量不拦：读那份文件本身就会触发 paths 载入。"""
    assert not gate.should_deny("skill/x/SKILL.md", transcript(tmp_path, []), exists=True)


def test_mentioning_rule_path_in_text_is_not_a_read(tmp_path):
    """仓根 CLAUDE.md 里就写着这两个路径，按字符串匹配会永远判成读过。"""
    path = tmp_path / "t.jsonl"
    path.write_text(json.dumps({"message": {"content": [
        {"type": "text", "text": "见 .claude/rules/skill-authoring.md 与 zh-writing.md"}]}}),
        encoding="utf-8")
    assert gate.should_deny("skill/x/SKILL.md", str(path), exists=False)


def test_missing_transcript_does_not_block(tmp_path):
    """拿不到转录时放行，检查脚本不该挡住正常编辑。"""
    assert not gate.should_deny("skill/x/SKILL.md", str(tmp_path / "none.jsonl"), exists=False)


def run(payload):
    out = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                         capture_output=True, text=True)
    return json.loads(out.stdout) if out.stdout.strip() else {}


def test_pre_tool_use_emits_deny(tmp_path):
    got = run({"hook_event_name": "PreToolUse", "transcript_path": transcript(tmp_path, []),
               "tool_input": {"file_path": "skill/newop/SKILL.md"}})
    assert got["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert ".claude/rules/skill-authoring.md" in got["hookSpecificOutput"]["permissionDecisionReason"]


def test_post_tool_use_reports_instead_of_denying(tmp_path):
    got = run({"hook_event_name": "PostToolUse",
               "tool_input": {"file_path": "plugin/skill/repo-task-case-gen/SKILL.md"}})
    assert "permissionDecision" not in got["hookSpecificOutput"]
    assert "载入预算" in got["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize("path", ["README.md", "docs/guide/x.md", "skill/x/scripts/a.py"])
def test_non_skill_docs_are_ignored(path):
    assert run({"hook_event_name": "PreToolUse", "tool_input": {"file_path": path}}) == {}
