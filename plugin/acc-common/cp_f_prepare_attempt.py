#!/usr/bin/env python3
"""CP-F F2：冻结人工 directive、基础验收工件和原 case 输入。

本入口不执行 NPU，不产 verdict/acceptance，也不修改首次验收产物。
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import precision_retest_contract as contract  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="准备 CP-F 精度重测 attempt（只冻结工件，不执行 NPU）")
    parser.add_argument("--directive", required=True,
                        help="已由人工确认的 directive JSON")
    parser.add_argument("--reports-dir", required=True,
                        help="首次验收产物的**受信容纳根**——不是「就是那个报告目录」。"
                             "它只做一件事：directive.base_artifacts 里的五个绝对路径"
                             "必须逐个落在它之内（containment 校验，安全边界）；"
                             "真正的报告目录由 caseset.json 所在目录派生，attempt 也落在那里。"
                             "CP-E 已把 spec.json / golden.py / source_facts.json staging 进"
                             "验收 `--out`，所以直接把那个 `--out` 传进来即可，"
                             "无需再手工搬 spec 与 golden。")
    parser.add_argument("--execution-identity", required=True,
                        help="本轮 SoC/toolkit/vendor ELF/golden source 身份 JSON")
    args = parser.parse_args(argv)
    try:
        directive = contract.load_strict_json(args.directive, "directive")
        identity = contract.load_strict_json(
            args.execution_identity, "execution identity")
        result = contract.materialize_attempt(
            directive, args.reports_dir, identity)
    except (OSError, contract.RetestContractError) as ex:
        print(f"[CP-F prepare] BLOCKED: {ex}", file=sys.stderr)
        return 1
    print(json.dumps({
        "status": "prepared",
        "attempt": result["attempt"],
        "attempt_dir": result["attempt_dir"],
        "acceptance_verdict": None,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
