"""解析并记录某一侧的算子库文件。

输入：算子名、哪一侧、内置时的 opp 根目录或待验收侧的库文件。
输出：库指纹 JSON。
退出码：0 完成；3 无法判定。

两轮跑测各跑一次，把 path 钉进 ATK_CUSTOM_OPP_PATH，
再由 check_golden_source.py 核对两轮不是同一份。
"""

import argparse
import json
import os
import sys

from _opp_library import (LibraryNotFound, fingerprint, has_function,
                          resolve_builtin)
import _stage_card


def main():
    parser = argparse.ArgumentParser(description="解析并记录某一侧的算子库")
    parser.add_argument("--op", required=True, help="aclnn 接口名，如 aclnnBernoulli")
    parser.add_argument("--side", required=True, choices=("builtin", "candidate"))
    parser.add_argument("--library", help="--side candidate 时本轮 vendor 的 .so")
    parser.add_argument("--opp-path", default=os.environ.get("ASCEND_OPP_PATH"),
                        help="--side builtin 时的 ASCEND_OPP_PATH，默认读环境变量")
    parser.add_argument("-o", "--output", required=True)
    args = parser.parse_args()
    _stage_card.announce(__file__)

    if args.side == "candidate":
        if not args.library:
            print("--side candidate 必须给 --library：待验收算子那一侧的库路径"
                  "是 S3 装包的产物，猜不出来。", file=sys.stderr)
            return 3
        if not os.path.exists(args.library):
            print(f"{args.library} 不存在。\n"
                  "  → --library 要给本轮 vendor 装出来的那个 .so 的真实路径，"
                  "见 references/build-deploy.md。", file=sys.stderr)
            return 3
        # 指到一个不含这个算子的 .so 时，不核就要等第二轮绑定阶段才
        # AttributeError，而那时一轮跑测已经排上了。
        if not has_function(args.library, f"{args.op}GetWorkspaceSize"):
            print(f"{args.library} 里没有 {args.op}GetWorkspaceSize。\n"
                  "  → 要么 .so 选错了（本轮 vendor 装出来的是另一个文件），\n"
                  "     要么包没装上/装的不是这个算子。先确认 S3 的安装真的成功了。",
                  file=sys.stderr)
            return 3
        payload = fingerprint(args.library, "candidate")
    else:
        if not args.opp_path:
            print("--side builtin 需要 ASCEND_OPP_PATH：先 source CANN 的 "
                  "set_env.sh，或显式给 --opp-path。", file=sys.stderr)
            return 3
        try:
            path = resolve_builtin(f"{args.op}GetWorkspaceSize", args.opp_path)
        except LibraryNotFound as exc:
            print(str(exc), file=sys.stderr)
            return 3
        payload = fingerprint(path, "builtin")

    payload["op"] = args.op
    with open(args.output, "w", encoding="utf-8") as sink:
        json.dump(payload, sink, ensure_ascii=False, indent=2)
    print(f"{payload['side']} 侧算子库 {payload['path']}")
    print(f"  sha256 {payload['sha256']}")
    print(f"  跑测前 export ATK_CUSTOM_OPP_PATH={payload['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
