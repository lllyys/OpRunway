"""将失败用例的完整可复现规格从本轮用例 JSON 落盘。"""

import argparse
import json
from pathlib import Path

from _case_utils import iter_cases, load_json


def parse_ids(text):
    value = json.loads(text)
    if not isinstance(value, list):
        raise ValueError("--ids 必须是 JSON 数组")
    return [str(item) for item in value]


def main():
    parser = argparse.ArgumentParser(description="落盘失败用例的完整生成规格")
    parser.add_argument("-j", "--case-json", required=True)
    parser.add_argument("--ids", required=True, help="失败用例号 JSON 数组，如 '[0,2]'")
    parser.add_argument("--stage", required=True)
    parser.add_argument("--log", required=True, help="原始失败日志路径")
    parser.add_argument("-o", "--output", required=True)
    args = parser.parse_args()

    wanted = parse_ids(args.ids)
    by_id = {str(case.get("id")): case for case in iter_cases(load_json(args.case_json))}
    missing = [case_id for case_id in wanted if case_id not in by_id]
    if missing:
        raise SystemExit(f"失败用例号不在本轮用例集中：{missing}")

    report = {
        "schema_version": 1,
        "case_json": str(Path(args.case_json).resolve()),
        "stage": args.stage,
        "evidence": args.log,
        "ids": wanted,
        "cases": [by_id[case_id] for case_id in wanted],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已落盘 {len(wanted)} 条失败用例规格 → {output}")


if __name__ == "__main__":
    main()
