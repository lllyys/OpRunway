"""把产物契约骨架渲染成签入仓库的派生视图。

视图签入是为了让 agent 直接读到，渲染是为了它不会变成第二份手工同步的表。
`--check` 由 tests 与 CI 用，`--write` 由维护者用。

退出码：0 一致或已写入；2 不一致（此时跑 --write 重新生成）。
"""

import argparse
import re
import sys
from pathlib import Path

import _handoff_contract
from _contracts import (ContractError, load, render_card,
                        render_decision_points, render_gate_inventory)

REFERENCES = Path(__file__).resolve().parents[1] / "references"
DECISION_POINTS = REFERENCES / "decision-points.md"
GATE_INVENTORY = REFERENCES / "gate-inventory.md"
HANDOFF = REFERENCES / "handoff.md"
HANDOFF_SEAL = REFERENCES / "handoff-seal.md"

# 每份视图：签入路径 + 从骨架渲染它的函数。
VIEWS = ((DECISION_POINTS, render_decision_points),
         (GATE_INVENTORY, render_gate_inventory))
FENCED_VIEWS = (
    (HANDOFF, "交接包目录", _handoff_contract.render_tree),
    (HANDOFF_SEAL, "封印清单", _handoff_contract.render_manifest_example),
)


class ViewError(RuntimeError):
    """签入视图缺少约定的标题或围栏代码块。"""


def _fenced_view_span(text, title):
    """定位指定二级标题后第一个围栏代码块的内容范围。"""
    heading = re.search(rf"^## {re.escape(title)}[ \t]*$", text, re.MULTILINE)
    if heading is None:
        raise ViewError(f"二级标题“{title}”不存在")
    section_start = heading.end()
    following = re.search(r"^## [^\n]+$", text[section_start:], re.MULTILINE)
    section_end = (
        section_start + following.start()
        if following is not None
        else len(text)
    )
    section = text[section_start:section_end]
    fence = re.search(
        r"^```[^\n]*\n(?P<body>.*?)\n```[ \t]*$",
        section,
        re.MULTILINE | re.DOTALL,
    )
    if fence is None:
        raise ViewError(f"二级标题“{title}”后没有围栏代码块")
    return (
        section_start + fence.start("body"),
        section_start + fence.end("body"),
    )


def fenced_view_content(text, title):
    """读取指定二级标题后的第一个围栏代码块内容。"""
    start, end = _fenced_view_span(text, title)
    return text[start:end]


def replace_fenced_view(text, title, rendered):
    """只替换指定二级标题后第一个围栏代码块的内容。"""
    start, end = _fenced_view_span(text, title)
    return text[:start] + rendered + text[end:]


def main():
    parser = argparse.ArgumentParser(description="渲染骨架的派生视图")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="核对是否与骨架一致")
    group.add_argument("--write", action="store_true", help="重新生成")
    group.add_argument("--cards", action="store_true",
                       help="按阶段顺序打印五张作战卡，供维护者核对渲染结果")
    args = parser.parse_args()

    try:
        data = load()
    except ContractError as exc:
        print(f"骨架不可用：{exc}", file=sys.stderr)
        return 2

    if args.cards:
        # 卡不签入任何文件，运行期由 mark_step.py 渲染。这条只给维护者：
        # 改完骨架想看五张卡长什么样，不必自己拼 python -c 调 render_card。
        for stage in data["stages"]:
            print(render_card(data, stage))
        return 0

    try:
        handoff_data = _handoff_contract.load()
    except _handoff_contract.HandoffContractError as exc:
        print(f"交接契约不可用：{exc}", file=sys.stderr)
        return 2

    if args.write:
        for path, render in VIEWS:
            if not path.is_file():
                continue
            path.write_text(render(data), encoding="utf-8")
            print(f"已生成 {path}")
        pending = {}
        try:
            for path, title, render in FENCED_VIEWS:
                if not path.is_file():
                    continue
                current = pending.get(path)
                if current is None:
                    current = path.read_text(encoding="utf-8")
                pending[path] = replace_fenced_view(
                    current,
                    title,
                    render(handoff_data),
                )
        except (OSError, ViewError) as exc:
            print(f"交接视图不可用：{exc}", file=sys.stderr)
            return 2
        for path, current in pending.items():
            path.write_text(current, encoding="utf-8")
            print(f"已生成 {path} 的交接契约视图")
        return 0

    stale = []
    for path, render in VIEWS:
        if not path.is_file():
            continue
        current = path.read_text(encoding="utf-8")
        if current != render(data):
            stale.append(path)
    try:
        for path, title, render in FENCED_VIEWS:
            if not path.is_file():
                continue
            current = path.read_text(encoding="utf-8")
            if fenced_view_content(current, title) != render(handoff_data):
                stale.append(path)
    except (OSError, ViewError) as exc:
        print(f"交接视图不可用：{exc}", file=sys.stderr)
        return 2
    if stale:
        for path in dict.fromkeys(stale):
            print(f"{path} 与骨架不同步，跑 render_views.py --write",
                  file=sys.stderr)
        return 2
    print("派生视图与骨架一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
