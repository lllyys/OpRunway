"""把产物契约骨架渲染成签入仓库的派生视图。

视图签入是为了让 agent 直接读到，渲染是为了它不会变成第二份手工同步的表。
`--check` 由 tests 与 CI 用，`--write` 由维护者用。

退出码：0 一致或已写入；2 不一致（此时跑 --write 重新生成）。
"""

import argparse
import sys
from pathlib import Path

from _contracts import (ContractError, load, render_card,
                        render_decision_points, render_gate_inventory)

REFERENCES = Path(__file__).resolve().parents[1] / "references"
DECISION_POINTS = REFERENCES / "decision-points.md"
GATE_INVENTORY = REFERENCES / "gate-inventory.md"

# 每份视图：签入路径 + 从骨架渲染它的函数。
VIEWS = ((DECISION_POINTS, render_decision_points),
         (GATE_INVENTORY, render_gate_inventory))


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

    if args.write:
        for path, render in VIEWS:
            path.write_text(render(data), encoding="utf-8")
            print(f"已生成 {path}")
        return 0

    stale = []
    for path, render in VIEWS:
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if current != render(data):
            stale.append(path)
    if stale:
        for path in stale:
            print(f"{path} 与骨架不同步，跑 render_views.py --write",
                  file=sys.stderr)
        return 2
    print("派生视图与骨架一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
