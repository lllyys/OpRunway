#!/usr/bin/env python3
"""CP-F F3/F4：执行 same-policy Task-2-only 精度重测。"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import precision_retest_runner as runner  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="执行已准备的 CP-F same-policy 精度重测 attempt")
    parser.add_argument("--attempt-dir", required=True)
    parser.add_argument(
        "--attempts-root", required=True,
        help="外部可信的 base output/attempts 根；不得从 attempt manifest 自举")
    args = parser.parse_args(argv)
    try:
        result = runner.execute_precision_attempt(
            args.attempt_dir, args.attempts_root)
    except (OSError, ValueError, runner.RetestExecutionError) as ex:
        print(f"[CP-F execute] BLOCKED: {ex}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["gate"]["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
