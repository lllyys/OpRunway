"""从 CP-F 确定性 JSON 逐字渲染中文精度重测报告；不重判。"""

import json
import os

import content_address
import precision_retest_contract as contract


def render(directive, base_acceptance, verdict, result, receipt):
    overall = verdict.get("overall") if isinstance(verdict, dict) else {}
    counts = overall.get("counts") if isinstance(overall, dict) else {}
    gate = result.get("gate") if isinstance(result, dict) else {}
    lines = [
        "# 精度重测报告",
        "",
        "> 本报告是首次验收后的追加证据，不覆盖基础 `acceptance.json`；性能未重测。",
        "",
        "## 基础验收",
        "",
        f"- 基础总体裁决：`{base_acceptance.get('overall')}`。",
        "- 基础 `acceptance.json` 保持不变：`true`。",
        "",
        "## 人工指令",
        "",
        f"- directive：`{directive.get('directive_id')}`。",
        f"- attempt 类型：`{directive.get('attempt_kind')}`。",
        f"- 原始指令：{directive.get('human_instruction')}",
        f"- case：`{', '.join(directive.get('case_ids') or [])}`。",
        "",
        "## 确定性结果",
        "",
        f"- policy source：`{result.get('policy_source')}`。",
        f"- validator 精度裁决：`{result.get('precision_verdict')}`。",
        f"- Task 2 证据门：`{'PASSED' if gate.get('passed') else 'FAILED'}`。",
        f"- counts：`{json.dumps(counts, ensure_ascii=False, sort_keys=True)}`。",
        f"- 需要人工处置：`{str(bool(result.get('requires_human_cp'))).lower()}`。",
        "",
        "## 性能",
        "",
        "- 本轮未调用性能 collector 或 `perf_compare.py`。",
        f"- 性能来源：`{result.get('perf_source')}`。",
        "- 首次性能裁决未被修改。",
        "",
        "## 收据",
        "",
        f"- lifecycle：`{receipt.get('lifecycle')}`。",
        f"- completed_at：`{receipt.get('completed_at')}`。",
        f"- receipt acceptance verdict：`{receipt.get('acceptance_verdict')}`。",
        "",
    ]
    if not gate.get("passed"):
        lines.extend([
            "## 门错误",
            "",
            "```json",
            json.dumps(gate.get("errors") or {}, ensure_ascii=False,
                       sort_keys=True, indent=2),
            "```",
            "",
        ])
    return "\n".join(lines)


def render_directory(attempt_dir, receipt_artifact=None):
    root = os.path.realpath(os.fspath(attempt_dir))
    directive_artifact = contract.load_strict_json(
        os.path.join(root, "directive.json"), "directive")
    if receipt_artifact is None:
        receipt_artifact = contract.load_strict_json(
            os.path.join(root, "attempt.receipt.json"), "attempt receipt")
    directive = directive_artifact["payload"]
    receipt = receipt_artifact["payload"]
    verdict = contract.load_strict_json(
        os.path.join(root, "verdict.json"), "verdict")
    result = contract.load_strict_json(
        os.path.join(root, "retest_acceptance.json"), "retest acceptance")
    base_path = directive["base_artifacts"]["acceptance"]["path"]
    base_acceptance = contract.load_strict_json(
        base_path, "base acceptance")
    text = render(directive, base_acceptance, verdict, result, receipt)
    target = content_address.safe_path(root, "精度重测报告.md")
    import tempfile
    fd, tmp = tempfile.mkstemp(
        prefix="precision-retest-report.tmp.", dir=root)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as out:
        out.write(text)
        out.flush()
        os.fsync(out.fileno())
    os.replace(tmp, target)
    return target
