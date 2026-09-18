#!/usr/bin/env python3
"""算出还缺哪些拍板项，按批次输出下一步该问什么。

批次定义在骨架 question_batches，默认输出第一个有缺口的批；--all 一次输出全部
缺口批次（候选总审用）。记录格式（含保留 key 与有效拍板判据）见
references/decisions-format.md。

条件项按三态求值（适用 / 不适用 / 无法判断），已有效拍板的条件项直接闭合：
- 随机性：信号词表与门禁同源（random-operator-signals.json）；文本面命中或人工
  前提（_premises.random_operator）判随机即适用；前提缺失且文本面未命中判无法
  判断。适用与无法判断都进待问，不静默跳过。
- 签名一致性：用 _review_signature 严格解析两侧 confirmed_content；两侧完整且
  逐位相等判不适用，存在差异判适用，其余（缺内容、解析不完整、多稿）判无法判断。

文本面三模式（--doc 与 --candidates 互斥，同传退 2）：
  --doc         检查既有任务书：扫描 doc 全文（与门禁同径）
  --candidates  总审前：逐 key 最新内容（本轮候选块优先，其余取 confirmed_content）
  都不传        批次循环：全部 confirmed_content

退出码：0 正常（含仍有缺口），2 参数错误。骨架路径可用环境变量 TASKDOC_SPINE
覆盖（测试用）。
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review_signature  # noqa: E402  调度侧专用解析器，不碰门禁共享件

SKILL_ROOT = Path(__file__).resolve().parents[1]
SPINE = Path(os.environ.get("TASKDOC_SPINE")
             or SKILL_ROOT / "references" / "taskdoc-elements.json")
SIGNALS = SKILL_ROOT / "references" / "random-operator-signals.json"

def answered(record):
    return bool(record) and bool(str(record.get("human_reply", "")).strip())


class CandidatesError(ValueError):
    """总审文件畸形：显式报错退 2，不截取后继续。"""


def load_candidates(path):
    """总审文件 → {骨架key: 非空候选块内容}。逐行状态机解析：围栏内的 ``##``
    不当节界；key 重复、一节多块、围栏不闭合都是畸形输入。空块 = 未给候选。"""
    blocks, seen, key, in_fence, buf = {}, set(), None, False, []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if in_fence:
            if line.rstrip() == "```":
                in_fence = False
                if key in blocks:
                    raise CandidatesError(f"「{key}」一节出现多个候选块")
                blocks[key] = "\n".join(buf).strip("\n")
            else:
                buf.append(line)
            continue
        if line.startswith("## "):
            key = line[3:].strip()
            if key in seen:
                raise CandidatesError(f"key 重复：{key}")
            seen.add(key)
            continue
        if line.strip() == "```candidate":
            if key is None:
                raise CandidatesError("候选块出现在任何节之前")
            in_fence, buf = True, []
    if in_fence:
        raise CandidatesError("候选围栏未闭合")
    return {k: v for k, v in blocks.items() if v.strip()}


def scan_face(args, decisions):
    """有效文本面：随机信号扫描的输入。三模式，见文件头。"""
    if args.doc and Path(args.doc).is_file():
        return Path(args.doc).read_text(encoding="utf-8")
    confirmed = {k: v.get("confirmed_content")
                 for k, v in decisions.items()
                 if isinstance(v, dict) and v.get("confirmed_content")}
    if args.candidates:
        confirmed.update(load_candidates(args.candidates))
    return "\n".join(v for v in confirmed.values() if v)


def random_state(decisions, face):
    if answered(decisions.get("3.2.random_strategy")):
        return ("closed", "随机策略已有效拍板")
    signals = json.loads(SIGNALS.read_text(encoding="utf-8"))["signal_keywords"]
    premise = ((decisions.get("_premises") or {})
               .get("random_operator") or {}).get("value")
    if any(word in face for word in signals):
        return ("applicable", "文本面命中随机信号词")
    if premise == "random":
        return ("applicable", "人工前提判定为随机算子")
    if premise == "nonrandom":
        return ("not_applicable", "前提非随机且文本面未命中信号词")
    return ("unknown", "缺随机性前提：_premises.random_operator 未记录，"
                       "呈现本项时先问前提再复核")


def signature_state(decisions):
    if answered(decisions.get("3.5.param_mapping")):
        return ("closed", "参数映射已有效拍板")
    sides = {}
    for key in ("2.3.signature", "2.1.baseline"):
        content = (decisions.get(key) or {}).get("confirmed_content")
        if not content:
            return ("unknown", f"{key} 缺已确认内容（confirmed_content）")
        sides[key] = _review_signature.parse_signature(content)
    for key, parsed in sides.items():
        if parsed["status"] != "complete":
            return ("unknown", f"{key} 的确认内容解析为 {parsed['status']}")
    if sides["2.3.signature"]["params"] == sides["2.1.baseline"]["params"]:
        return ("not_applicable", "签名与基线参数序列完整一致")
    return ("applicable", "签名与基线存在参数差异")


def main(argv=None):
    ap = argparse.ArgumentParser(description="追问调度")
    ap.add_argument("--decisions", required=True)
    ap.add_argument("--doc", help="既有任务书 md，作为条件求值的文本面")
    ap.add_argument("--candidates", help="总审文件（evidence/review-round-N.md）")
    ap.add_argument("--all", action="store_true", help="输出全部缺口批次")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args(argv)
    if args.doc and args.candidates:
        print("--doc 与 --candidates 互斥：检查既有文档与总审前求值是两种模式，"
              "文本面只能取一个。", file=sys.stderr)
        return 2

    spine = json.loads(SPINE.read_text(encoding="utf-8"))
    elements = spine["elements"]
    path = Path(args.decisions)
    decisions = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    try:
        face = scan_face(args, decisions)
    except CandidatesError as error:
        print(f"总审文件畸形：{error}", file=sys.stderr)
        return 2
    conditions = {
        "random_signal_hit": random_state(decisions, face),
        "signature_differs_from_baseline": signature_state(decisions),
    }

    def needed(key):
        element = elements[key]
        if element["decision_owner"] != "human":
            return False
        if element["required"] == "conditional":
            state = conditions.get(element["condition"],
                                   ("unknown", "未登记的条件，按无法判断处理"))
            return state[0] in ("applicable", "unknown")
        return True

    def detail_of(key):
        element = elements[key]
        item = {"key": key, "name": element["name"],
                "section": element["section"],
                "agent_may_propose": element["agent_may_propose"],
                "failure": element["failure"]}
        if element["required"] == "conditional":
            state, reason = conditions.get(
                element["condition"], ("unknown", "未登记的条件"))
            item["condition"] = {"name": element["condition"],
                                 "state": state, "reason": reason}
        return item

    pending, open_batches = [], []
    for batch in spine["question_batches"]:
        ask = [k for k in batch["elements"]
               if needed(k) and not answered(decisions.get(k))]
        pending.extend(ask)
        if ask:
            open_batches.append({"id": batch["id"], "name": batch["name"],
                                 "ask": ask,
                                 "detail": [detail_of(k) for k in ask]})

    outside = [k for k in elements
               if needed(k) and not answered(decisions.get(k))
               and not any(k in b["elements"]
                           for b in spine["question_batches"])]
    pending.extend(outside)

    next_batch = open_batches[0] if open_batches else None
    payload = {"done": not pending, "pending": pending,
               "next_batch": next_batch, "unbatched_pending": outside,
               "conditions": {name: {"state": state, "reason": reason}
                              for name, (state, reason) in conditions.items()}}
    if args.all:
        payload["batches"] = open_batches

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if not pending:
        print("全部拍板项已有答复原话，可以落稿。")
        return 0

    def print_item(item):
        mark = "agent 可先给候选" if item["agent_may_propose"] else "只能问人"
        print(f"  §{item['section']} {item['name']}（{mark}）")
        print(f"      不写会怎样：{item['failure']}")
        if "condition" in item:
            cond = item["condition"]
            print(f"      条件：{cond['state']} · {cond['reason']}")

    shown = open_batches if args.all else open_batches[:1]
    print(f"还缺 {len(pending)} 项。")
    for batch in shown:
        print(f"第 {batch['id']} 批 {batch['name']}：")
        for item in batch["detail"]:
            print_item(item)
    if not args.all and len(open_batches) > 1:
        print(f"（后续还有 {len(open_batches) - 1} 个缺口批，--all 一次看全）")
    if outside:
        print("批次外缺口（按 key 记录答复后重跑本命令即可推进）：")
        for key in outside:
            print_item(detail_of(key))
    return 0


if __name__ == "__main__":
    sys.exit(main())
