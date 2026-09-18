"""追问调度：批次缺口、条件三态、收尾兜底与记录契约。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "next_questions.py"
DECISIONS = SKILL_ROOT / "tests" / "fixtures" / "decisions_golden.json"
SIGNALS = json.loads(
    (SKILL_ROOT / "references" / "random-operator-signals.json")
    .read_text(encoding="utf-8"))["signal_keywords"]
SIGNAL = SIGNALS[0]


def run(decisions_path, *extra, env_spine=None, as_json=True):
    cmd = [sys.executable, str(SCRIPT), "--decisions", str(decisions_path)]
    cmd.extend(extra)
    if as_json:
        cmd.append("--json")
    env = dict(os.environ)
    if env_spine:
        env["TASKDOC_SPINE"] = str(env_spine)
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    payload = json.loads(result.stdout) if as_json and result.stdout else {}
    return result.returncode, payload, result.stdout


def write_json(data):
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(data, handle, ensure_ascii=False)
    handle.close()
    return handle.name


def write_text(text, suffix=".md"):
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=suffix, delete=False, encoding="utf-8")
    handle.write(text)
    handle.close()
    return handle.name


class NextQuestionsTest(unittest.TestCase):
    def test_complete_decisions_report_done(self):
        code, payload, _ = run(DECISIONS)
        self.assertEqual(0, code)
        self.assertEqual([], payload["pending"])
        self.assertTrue(payload["done"])

    def test_repeat_run_is_idempotent(self):
        _, first, _ = run(DECISIONS)
        _, second, _ = run(DECISIONS)
        self.assertEqual(first, second)

    def test_empty_decisions_start_at_batch_one(self):
        code, payload, _ = run(write_json({}))
        self.assertEqual(0, code)
        self.assertEqual(1, payload["next_batch"]["id"])
        self.assertEqual("前提", payload["next_batch"]["name"])
        self.assertFalse(payload["done"])

    def test_partial_decisions_skip_to_the_first_gap(self):
        full = json.loads(DECISIONS.read_text(encoding="utf-8"))
        for key in ("2.5.non_contiguous", "2.5.broadcast"):
            full.pop(key)
        code, payload, _ = run(write_json(full))
        self.assertEqual(7, payload["next_batch"]["id"])
        self.assertEqual(["2.5.non_contiguous", "2.5.broadcast"],
                         payload["next_batch"]["ask"])

    def test_payload_carries_failure_mode_for_each_question(self):
        _, payload, _ = run(write_json({}))
        for item in payload["next_batch"]["detail"]:
            with self.subTest(item=item["key"]):
                self.assertTrue(item["failure"])
                self.assertIn("agent_may_propose", item)

    # --- A6 兼容：既有字段语义 ---

    def test_json_keeps_legacy_fields(self):
        _, payload, _ = run(write_json({}))
        for field in ("done", "pending", "next_batch", "unbatched_pending"):
            self.assertIn(field, payload)

    def test_doc_and_candidates_are_mutually_exclusive(self):
        doc = write_text("x")
        cand = write_text("x")
        code, _, _ = run(write_json({}), "--doc", doc, "--candidates", cand)
        self.assertEqual(2, code)

    # --- A3 条件·随机（决策表逐行） ---

    def test_candidate_block_signal_makes_strategy_pending(self):
        cand = write_text("## 3.5.generation_rules\n生成规则 · §3.5 · x\n"
                          f"```candidate\n数据{SIGNAL}生成\n```\n")
        _, payload, _ = run(write_json({}), "--candidates", cand)
        self.assertEqual("applicable",
                         payload["conditions"]["random_signal_hit"]["state"])
        self.assertIn("3.2.random_strategy", payload["pending"])

    def test_heading_inside_fence_is_content_not_boundary(self):
        cand = write_text("## 3.5.generation_rules\n规则 · §3.5 · x\n"
                          f"```candidate\n## 说明\n数据{SIGNAL}生成\n```\n")
        code, payload, _ = run(write_json({}), "--candidates", cand)
        self.assertEqual(0, code)
        self.assertEqual("applicable",
                         payload["conditions"]["random_signal_hit"]["state"])

    def test_duplicate_key_is_rejected(self):
        cand = write_text("## 7.notes\n```candidate\nx\n```\n"
                          "## 7.notes\n```candidate\ny\n```\n")
        code, _, _ = run(write_json({}), "--candidates", cand)
        self.assertEqual(2, code)

    def test_two_blocks_in_one_section_rejected(self):
        cand = write_text("## 7.notes\n```candidate\nx\n```\n"
                          "```candidate\ny\n```\n")
        code, _, _ = run(write_json({}), "--candidates", cand)
        self.assertEqual(2, code)

    def test_unclosed_fence_rejected(self):
        cand = write_text("## 7.notes\n```candidate\nx\n")
        code, _, _ = run(write_json({}), "--candidates", cand)
        self.assertEqual(2, code)

    def test_detail_text_outside_candidate_block_is_ignored(self):
        cand = write_text(f"## 3.5.generation_rules\n说明 {SIGNAL} 不入面\n"
                          "```candidate\n均匀网格\n```\n")
        _, payload, _ = run(write_json({}), "--candidates", cand)
        self.assertEqual("unknown",
                         payload["conditions"]["random_signal_hit"]["state"])

    def test_premise_random_asks_strategy_without_signal(self):
        decisions = {"_premises": {"random_operator": {"value": "random"}}}
        _, payload, _ = run(write_json(decisions))
        self.assertEqual("applicable",
                         payload["conditions"]["random_signal_hit"]["state"])
        self.assertIn("3.2.random_strategy", payload["pending"])

    def test_premise_nonrandom_without_signal_not_applicable(self):
        decisions = {"_premises": {"random_operator": {"value": "nonrandom"}}}
        _, payload, _ = run(write_json(decisions))
        self.assertEqual("not_applicable",
                         payload["conditions"]["random_signal_hit"]["state"])
        self.assertNotIn("3.2.random_strategy", payload["pending"])

    def test_missing_premise_is_unknown_and_pending(self):
        _, payload, _ = run(write_json({}))
        self.assertEqual("unknown",
                         payload["conditions"]["random_signal_hit"]["state"])
        self.assertIn("3.2.random_strategy", payload["pending"])

    def test_confirmed_content_signal_hits_in_plain_mode(self):
        decisions = {"7.notes": {"human_reply": "准",
                                 "confirmed_content": f"含{SIGNAL}行为"}}
        _, payload, _ = run(write_json(decisions))
        self.assertEqual("applicable",
                         payload["conditions"]["random_signal_hit"]["state"])

    def test_answered_strategy_closes_condition(self):
        decisions = {"3.2.random_strategy": {"human_reply": "不涉及：非随机算子"},
                     "_premises": {"random_operator": {"value": "random"}}}
        _, payload, _ = run(write_json(decisions))
        self.assertEqual("closed",
                         payload["conditions"]["random_signal_hit"]["state"])
        self.assertNotIn("3.2.random_strategy", payload["pending"])

    # --- A4 条件·签名 ---

    @staticmethod
    def _sig_decisions(sig, base, extra=None):
        decisions = {
            "2.3.signature": {"human_reply": "准", "confirmed_content": sig},
            "2.1.baseline": {"human_reply": "准", "confirmed_content": base},
            "_premises": {"random_operator": {"value": "nonrandom"}},
        }
        decisions.update(extra or {})
        return decisions

    def test_identical_signatures_not_applicable(self):
        _, payload, _ = run(write_json(
            self._sig_decisions("def f(a, b):", "def f(a, b):")))
        state = payload["conditions"]["signature_differs_from_baseline"]
        self.assertEqual("not_applicable", state["state"])
        self.assertNotIn("3.5.param_mapping", payload["pending"])

    def test_reordered_signatures_applicable(self):
        _, payload, _ = run(write_json(
            self._sig_decisions("def f(a, b):", "def f(b, a):")))
        state = payload["conditions"]["signature_differs_from_baseline"]
        self.assertEqual("applicable", state["state"])
        self.assertIn("3.5.param_mapping", payload["pending"])

    def test_missing_confirmed_content_is_unknown(self):
        _, payload, _ = run(write_json(
            {"2.3.signature": {"human_reply": "确认 · 接口定义"}}))
        state = payload["conditions"]["signature_differs_from_baseline"]
        self.assertEqual("unknown", state["state"])
        self.assertIn("3.5.param_mapping", payload["pending"])

    def test_prose_confirmed_content_is_unknown(self):
        _, payload, _ = run(write_json(
            self._sig_decisions("同意上述接口", "def f(a):")))
        state = payload["conditions"]["signature_differs_from_baseline"]
        self.assertEqual("unknown", state["state"])

    def test_qualifier_only_c_params_stay_unknown_and_pending(self):
        # 终审反例：限定词当类型核会把不完整签名判成一致而跳过映射确认。
        _, payload, _ = run(write_json(self._sig_decisions(
            "void f(const aclTensor)", "void f(const aclTensor)")))
        state = payload["conditions"]["signature_differs_from_baseline"]
        self.assertEqual("unknown", state["state"])
        self.assertIn("3.5.param_mapping", payload["pending"])

    def test_answered_param_mapping_closes_condition(self):
        _, payload, _ = run(write_json(self._sig_decisions(
            "def f(a, b):", "def f(b, a):",
            {"3.5.param_mapping": {"human_reply": "对应关系已列"}})))
        state = payload["conditions"]["signature_differs_from_baseline"]
        self.assertEqual("closed", state["state"])
        self.assertNotIn("3.5.param_mapping", payload["pending"])

    # --- A5 记录契约 ---

    def test_note_only_record_stays_pending(self):
        decisions = {"1.background": {"note": "暂不决定"}}
        _, payload, _ = run(write_json(decisions))
        self.assertIn("1.background", payload["pending"])

    def test_superseded_record_reads_as_unanswered_for_both_readers(self):
        full = json.loads(DECISIONS.read_text(encoding="utf-8"))
        record = full.pop("1.background")
        full.setdefault("_superseded", []).append(
            {"key": "1.background", "record": record,
             "reason": "上游变更", "ts": 0})
        path = write_json(full)
        _, payload, _ = run(path)
        self.assertIn("1.background", payload["pending"])
        # 旧版读取逻辑（非空 human_reply 即已答）对该 key 同样读不到记录。
        reloaded = json.loads(Path(path).read_text(encoding="utf-8"))
        legacy_answered = bool(
            str((reloaded.get("1.background") or {})
                .get("human_reply", "")).strip())
        self.assertFalse(legacy_answered)

    # --- A2/D3 旧骨架收尾兜底 ---

    @staticmethod
    def _legacy_spine():
        element = {"section": "1", "form": "prose", "audience": "both",
                   "required": "always", "condition": None,
                   "decision_owner": "human", "agent_may_propose": True,
                   "checks": [], "vague_forbidden": False,
                   "checklist_items": [], "failure": "缺口说明"}
        return {"schema_version": 0, "template_version": 0,
                "sections": [], "check_registry": [],
                "question_batches": [
                    {"id": 1, "name": "旧批", "elements": ["1.batched"]}],
                "elements": {"1.batched": dict(element, name="批内项"),
                             "1.outside": dict(element, name="批外项")}}

    def test_legacy_spine_fallback_lists_detail_and_progresses(self):
        spine_path = write_json(self._legacy_spine())
        decisions_path = write_json(
            {"1.batched": {"human_reply": "准"},
             "_premises": {"random_operator": {"value": "nonrandom"}}})
        code, _, stdout = run(decisions_path, env_spine=spine_path,
                              as_json=False)
        self.assertEqual(0, code)
        self.assertIn("批外项", stdout)
        self.assertIn("不写会怎样", stdout)
        self.assertIn("重跑本命令", stdout)
        # 按指引记录批外项答复后重跑 → done。
        done_path = write_json(
            {"1.batched": {"human_reply": "准"},
             "1.outside": {"human_reply": "准"},
             "_premises": {"random_operator": {"value": "nonrandom"}}})
        code, payload, _ = run(done_path, env_spine=spine_path)
        self.assertEqual(0, code)
        self.assertTrue(payload["done"])

    # --- B4 全量总审 ---

    def test_all_lists_every_pending_batch_without_duplicates(self):
        _, payload, _ = run(write_json({}), "--all")
        self.assertIn("batches", payload)
        listed = [k for b in payload["batches"] for k in b["ask"]]
        self.assertEqual(sorted(set(listed)), sorted(listed))
        batched_pending = [k for k in payload["pending"]
                           if k not in payload["unbatched_pending"]]
        self.assertEqual(sorted(batched_pending), sorted(listed))
        for batch in payload["batches"]:
            for item in batch["detail"]:
                for field in ("key", "name", "section",
                              "agent_may_propose", "failure"):
                    self.assertIn(field, item)

    def test_all_text_mode_exits_zero(self):
        code, _, stdout = run(write_json({}), "--all", as_json=False)
        self.assertEqual(0, code)
        self.assertIn("第 1 批", stdout)


if __name__ == "__main__":
    unittest.main()
