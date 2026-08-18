"""生成前检查待验收算子 op_api 库，并可在冒烟后核对实际加载路径。"""

import argparse
import hashlib
import json
from pathlib import Path

from _opapi_binding import BindingError, inspect_library, verify_runtime_log
import _stage_card


def main():
    parser = argparse.ArgumentParser(description="待验收算子 op_api 精确绑定门禁")
    parser.add_argument("--library", required=True, help="待验收算子 libcust_opapi.so 绝对路径")
    parser.add_argument("--aclnn-name", required=True, help="YAML 的 aclnn_name")
    parser.add_argument("--atk-log", help="冒烟后的 ATK 日志；传入时核对实际加载路径")
    parser.add_argument("-o", "--output", default="opapi_binding.json")
    args = parser.parse_args()
    _stage_card.announce(__file__)

    failures = []
    report = {
        "schema_version": 1,
        "library": str(Path(args.library).expanduser()),
        "aclnn_name": args.aclnn_name,
        "precheck": None,
        "runtime": None,
        "failures": failures,
    }
    try:
        report["precheck"] = inspect_library(args.library, args.aclnn_name)
        if args.atk_log:
            log_path = Path(args.atk_log).expanduser().resolve(strict=True)
            log_bytes = log_path.read_bytes()
            log_text = log_bytes.decode("utf-8", errors="replace")
            report["runtime"] = verify_runtime_log(
                log_text, args.library, args.aclnn_name)
            report["runtime"].update({
                "log": str(log_path),
                "log_sha256": hashlib.sha256(log_bytes).hexdigest(),
            })
    except (BindingError, OSError) as exc:
        failures.append(str(exc))

    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if failures:
        for failure in failures:
            print(f"✗ {failure}")
        return 2
    print(f"待验收算子 op_api 绑定门禁通过 → {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
