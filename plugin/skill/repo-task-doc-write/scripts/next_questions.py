#!/usr/bin/env python3
"""算出还缺哪些拍板项，按批次输出下一步该问什么。

40 条要素里约 38 条要人拍板，逐条问 38 轮不现实。批次定义在骨架的
question_batches 里，每批 3-6 个相关项一次呈现。

只报「缺什么」，不替人回答。答复原话由 agent 记进 decisions.json。
"""

import argparse
import json
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SPINE = SKILL_ROOT / "references" / "taskdoc-elements.json"


def answered(record):
    return bool(record) and bool(str(record.get("human_reply", "")).strip())


def main(argv=None):
    ap = argparse.ArgumentParser(description="追问调度")
    ap.add_argument("--decisions", required=True)
    ap.add_argument("--doc", help="任务书 md，用于判断条件必填是否生效")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args(argv)

    spine = json.loads(SPINE.read_text(encoding="utf-8"))
    elements = spine["elements"]
    path = Path(args.decisions)
    decisions = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    signal_hit = False
    if args.doc and Path(args.doc).is_file():
        signals = json.loads((SKILL_ROOT / "references"
                              / "random-operator-signals.json").read_text(
                                  encoding="utf-8"))["signal_keywords"]
        text = Path(args.doc).read_text(encoding="utf-8")
        signal_hit = any(word in text for word in signals)

    def needed(key):
        element = elements[key]
        if element["decision_owner"] != "human":
            return False
        if element["required"] == "conditional":
            if element["condition"] == "random_signal_hit":
                return signal_hit
            return False
        return True

    pending, next_batch = [], None
    for batch in spine["question_batches"]:
        ask = [k for k in batch["elements"]
               if needed(k) and not answered(decisions.get(k))]
        pending.extend(ask)
        if ask and next_batch is None:
            next_batch = {
                "id": batch["id"], "name": batch["name"], "ask": ask,
                "detail": [{
                    "key": k,
                    "name": elements[k]["name"],
                    "section": elements[k]["section"],
                    "agent_may_propose": elements[k]["agent_may_propose"],
                    "failure": elements[k]["failure"],
                } for k in ask],
            }

    outside = [k for k in elements
               if needed(k) and not answered(decisions.get(k))
               and not any(k in b["elements"] for b in spine["question_batches"])]
    pending.extend(outside)

    payload = {"done": not pending, "pending": pending,
               "next_batch": next_batch,
               "unbatched_pending": outside}

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if not pending:
        print("全部拍板项已有答复原话，可以落稿。")
        return 0
    print(f"还缺 {len(pending)} 项。下一批：第 {next_batch['id']} 批 "
          f"{next_batch['name']}")
    for item in next_batch["detail"]:
        mark = "agent 可先给候选" if item["agent_may_propose"] else "只能问人"
        print(f"  §{item['section']} {item['name']}（{mark}）")
        print(f"      不写会怎样：{item['failure']}")
    if outside:
        print(f"\n批次外还缺：{outside}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
