"""T1/T3 防绕过闸门：跑测后扫 run_state，产出「待 agent 处理」队列，写 postrun_actions.json。

问题：P5.5 FAQ 查询 / P5.7 自动续跑 / P6 探索 / UNCERTAIN 复核 都是 SKILL agent 节点，
直接 `python3 run_phase1_batched.py` 跑会全部绕过、却仍报「完成」。

闸门：跑完扫 run_state——只要有**失败**或**待复核**算子，就整轮标 `ACTION_REQUIRED` +
退出码 3（**不复用退出码 2**，2 已被 OpsResolutionError / argparse usage 占用），并在 SUMMARY 打水印。
SKILL 的 agent 消费 `postrun_actions.json`、把 FAQ/续跑/P6/复核 处理完后才算这一轮收尾。
"""
from __future__ import annotations

import json
from pathlib import Path

import state

# 失败类（需走 FAQ → 续跑 → P6）：直接复用 state 的单一来源，避免两处定义漂移。
_FAIL_STATUSES = state.FAIL_STATUSES

# 已结算、无需待办的状态：PASS（成功）+ 各类 SKIPPED（源码层无 examples / 运行层空退 / 用户跳过）。
_SETTLED_STATUSES = {"PASS", "SKIPPED_NO_ARTIFACT", "SKIPPED_NO_RUN_ARTIFACT", "SKIPPED_USER"}

# 整轮退出码：0=COMPLETE，3=ACTION_REQUIRED。刻意不用 2（OpsResolutionError / argparse 已占）。
EXIT_COMPLETE = 0
EXIT_ACTION_REQUIRED = 3


def build_postrun_actions(phase: str = "phase1") -> dict:
    """扫所有仓的 run_state，产出待办队列（纯读，不改 run_state）。

    - failed_ops：失败算子 —— 需走 FAQ 查询 → 自动续跑 → (确认后) P6 探索 → 上报。
    - uncertain_reviews：UNCERTAIN 算子 —— 需 agent 复核落成 PASS / RUN_PATTERN_FAIL（T3）。
    - incomplete_ops：**没真正跑完**的算子 —— PENDING / RUNNING / 未知状态。worker 崩了（EXEC_ERROR
      整仓无 ops_status）或被中断时，算子停在 PENDING；不入此队就会假装「完成」。
    （`PASS` / `SKIPPED_*` 已结算，不入任何队。）
    """
    data = state.load()
    failed_ops, uncertain_reviews, incomplete_ops = [], [], []
    for repo in sorted(data["repos"]):
        ops = data["repos"][repo]["ops"]
        for op, st in sorted(ops.items()):
            ph = st.get(phase, {})
            s = ph.get("status", "PENDING")
            item = {"repo": repo, "op": op, "status": s, "log_path": ph.get("log_path")}
            if s in _FAIL_STATUSES:
                failed_ops.append(item)
            elif s == "UNCERTAIN":
                uncertain_reviews.append(item)
            elif s not in _SETTLED_STATUSES:   # PENDING / RUNNING / 未知 → 这一轮没真正跑完
                incomplete_ops.append(item)
    return {"phase": phase, "failed_ops": failed_ops,
            "uncertain_reviews": uncertain_reviews, "incomplete_ops": incomplete_ops}


def run_completion(actions: dict) -> str:
    """三队列任一非空 → ACTION_REQUIRED（直跑 runner 不可静默声称完成）；全空 → COMPLETE。"""
    pending = bool(actions.get("failed_ops") or actions.get("uncertain_reviews")
                   or actions.get("incomplete_ops"))
    return "ACTION_REQUIRED" if pending else "COMPLETE"


def write_postrun_actions(actions: dict, completion: str) -> Path:
    """写 CWD/cann-ops-report/postrun_actions.json；ACTION_REQUIRED 时给 SUMMARY 打水印。"""
    out = state.REPORT_ROOT / "postrun_actions.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"completion": completion, **actions}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = state.REPORT_ROOT / "SUMMARY.md"
    if completion == "ACTION_REQUIRED" and summary.is_file():
        txt = summary.read_text(encoding="utf-8")
        if "ACTION_REQUIRED" not in txt:
            n_fail = len(actions.get("failed_ops", []))
            n_unc = len(actions.get("uncertain_reviews", []))
            n_inc = len(actions.get("incomplete_ops", []))
            banner = (f"> ⚠ **ACTION_REQUIRED**：本轮有 {n_fail} 个失败 + {n_unc} 个待复核 + "
                      f"{n_inc} 个未跑完算子待 agent 处理（见 `postrun_actions.json`），**尚未收尾**。\n\n")
            summary.write_text(banner + txt, encoding="utf-8")
    return out


def postrun_gate(phase: str = "phase1") -> tuple[str, Path, int]:
    """一站式：构建队列 → 写文件 + 水印 → 返回 (completion, path, exit_code)。"""
    actions = build_postrun_actions(phase)
    completion = run_completion(actions)
    path = write_postrun_actions(actions, completion)
    code = EXIT_ACTION_REQUIRED if completion == "ACTION_REQUIRED" else EXIT_COMPLETE
    return completion, path, code
