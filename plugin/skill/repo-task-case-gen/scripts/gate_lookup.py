"""按量具名或门名查检查点，替代通读 gate-inventory.md。

19 道门全在 S2，清单全文约 3.8K token。写一件产物时真正相关的只有一两道，
把整份读进上下文，读到的 90% 与手上这件产物无关，而上下文是有限的。

卡已经告诉你这件产物由哪个量具校验，用那个名字查过来就是这里。

用法：
    gate_lookup.py                     列出全部门：门名、量具、检查几条
    gate_lookup.py make_yaml.py        某个量具下的全部门（完整条目）
    gate_lookup.py make_yaml.tuple_numbers   单独一道门
    gate_lookup.py --conditional       只列有前提的门（换一类算子时先看）

退出码：0 查到；2 名字对不上；3 骨架不可用。
"""

import argparse
import sys

from _contracts import ContractError, conditional_gates, load, render_gate


def match(gates, needle):
    """门名精确命中优先，其次按量具名归拢。

    两种查法共用一个位置参数：agent 手上拿到的可能是卡里的量具名，
    也可能是门禁报错里的门名，不该逼它先分辨自己拿的是哪一种。
    """
    if needle in gates:
        return [(needle, gates[needle])]
    script = needle if needle.endswith(".py") else needle + ".py"
    hits = [(gate_id, spec) for gate_id, spec in gates.items()
            if spec["script"] == script]
    if hits:
        return hits
    # 门名前缀（`make_yaml` 这种写法）是第三种常见输入。
    return [(gate_id, spec) for gate_id, spec in gates.items()
            if gate_id.startswith(needle)]


def summarize(gates):
    lines = [f"共 {len(gates)} 道检查点，按量具分组：", ""]
    by_script = {}
    for gate_id, spec in gates.items():
        by_script.setdefault(spec["script"], []).append((gate_id, spec))
    for script in sorted(by_script):
        lines.append(f"{script}")
        for gate_id, spec in by_script[script]:
            premise = "" if spec["premise"]["holds_when"] is None else "  有前提"
            lines.append(f"  {gate_id}　{len(spec['checks'])} 条{premise}")
        lines.append("")
    lines.append("查完整条目：gate_lookup.py <量具名或门名>")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="按量具名或门名查检查点，不必通读 gate-inventory.md")
    parser.add_argument("name", nargs="?",
                        help="量具名（make_yaml.py）或门名（make_yaml.tuple_numbers）")
    parser.add_argument("--conditional", action="store_true",
                        help="只列有前提的门，换一类算子时先看这几道")
    args = parser.parse_args()

    try:
        data = load()
    except ContractError as exc:
        print(f"骨架不可用：{exc}", file=sys.stderr)
        return 3
    gates = data.get("gate_inventory") or {}

    if args.conditional:
        hits = list(conditional_gates(data))
        if args.name:
            wanted = dict(match(gates, args.name))
            hits = [(g, s) for g, s in hits if g in wanted]
    elif args.name:
        hits = match(gates, args.name)
    else:
        print(summarize(gates))
        return 0

    if not hits:
        print(f"没有叫 {args.name!r} 的量具或门。不带参数跑一次可以看全部门名。",
              file=sys.stderr)
        return 2

    for gate_id, spec in hits:
        print("\n".join(render_gate(gate_id, spec)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
