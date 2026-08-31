#!/usr/bin/env python3
"""构建并运行 CSV 驱动的精度验收集，写出机器可读结果。"""

# ===== 渲染常量区开始 =====
OP = "sasum"
FAMILY = "asum"
CSV_NAME = "sasum_test.csv"
PACKAGE_CSV_SHA256 = "781669a4ce9062ee8d2eca3733ce7717a4948ed2b0cf3a8f517ca99a86a3d559"
GENERATOR_VERSION = int("1")
ACCURACY_FILTER = "*TC_*:-*TC_PF_*"
# ===== 渲染常量区结束 =====

import argparse
import csv
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


ENVIRONMENT_EXIT = 3
TEST_FAILURE_EXIT = 1
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _timestamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _arch_for_soc(soc):
    value = soc.lower()
    if value.startswith("ascend910b") or value.startswith("ascend910_93"):
        return "arch22"
    if value.startswith("ascend950"):
        return "arch35"
    if value.startswith("ascend310p"):
        return "arch20"
    return None


def _unique_existing(candidates):
    result = []
    seen = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen or not candidate.is_file():
            continue
        seen.add(resolved)
        result.append(resolved)
    return result


def _find_source_csv(repo, arch):
    test_root = repo / "test"
    candidates = [test_root / OP / arch / CSV_NAME]
    candidates.extend(sorted(test_root.glob(f"*/{OP}/{arch}/{CSV_NAME}")))
    if len(OP) > 1:
        candidates.append(test_root / OP[1:] / arch / CSV_NAME)
    matches = _unique_existing(candidates)
    return matches[0] if matches else None


def _find_binary(repo):
    test_root = repo / "build" / "test"
    name = f"{OP}_test"
    candidates = [test_root / OP / name]
    candidates.extend(sorted(test_root.glob(f"*/{OP}/{name}")))
    if len(OP) > 1:
        candidates.append(test_root / OP[1:] / name)
    matches = _unique_existing(candidates)
    return matches[0] if matches else None


def _read_csv_cases(path, prefix):
    header = None
    rows = []
    with Path(path).open(encoding="utf-8", newline="") as stream:
        for raw in stream:
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            values = next(csv.reader([raw]))
            if header is None:
                header = values
                continue
            row = dict(zip(header, values))
            name = row.get("case_name", "")
            if name.startswith(prefix):
                rows.append(name)
    return rows


def _atomic_json(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, target)


def _base_result(args, arch, started):
    return {
        "run_id": args.run_id,
        "op": OP,
        "family": FAMILY,
        "soc": args.soc,
        "arch": arch,
        "device": args.device,
        "repo": str(args.repo.resolve()),
        "binary": None,
        "binary_sha256": None,
        "csv_path": None,
        "csv_sha256": None,
        "package_csv_sha256": PACKAGE_CSV_SHA256,
        "gtest_filter": None,
        "cases": [],
        "summary": {},
        "exit_code": None,
        "started": started,
        "finished": None,
    }


def _environment_error(payload, out_path, reason, message):
    payload["summary"] = {
        "expected": 0,
        "pass": 0,
        "fail": 0,
        "skip": 0,
        "timeout": 0,
        "crash": 0,
        "missing": 0,
        "reason": reason,
        "message": message,
    }
    payload["exit_code"] = ENVIRONMENT_EXIT
    payload["finished"] = _timestamp()
    _atomic_json(out_path, payload)
    print(f"{reason}: {message}", file=sys.stderr)
    print(f"result: {out_path}")
    return ENVIRONMENT_EXIT


def _run_build(args, log_path):
    command = [
        "bash",
        "build.sh",
        f"--soc={args.soc}",
        f"--ops={OP}",
        f"--device={args.device}",
    ]
    try:
        result = subprocess.run(
            command,
            cwd=str(args.repo),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=args.build_timeout,
            check=False,
        )
        output = result.stdout
        return_code = result.returncode
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        output += "\nBUILD TIMEOUT\n"
        return_code = -1
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(output)
    return return_code


def _read_nonempty_lines(path):
    try:
        return [line.strip() for line in Path(path).read_text().splitlines() if line.strip()]
    except OSError:
        return []


def _check_build_lists(repo):
    test_build = repo / "build" / "test"
    built = _read_nonempty_lines(test_build / "built_tests.list")
    skipped = _read_nonempty_lines(test_build / "skipped_tests.list")
    if any(line.split("|", 1)[0] == OP for line in skipped):
        return "OP_SKIPPED", "skipped_tests.list 标记该算子为跳过"
    if OP not in built and f"{OP}_test" not in built:
        return "BUILD_FAILED", "built_tests.list 不含目标算子"
    return None, None


def _list_tests(binary, timeout):
    try:
        result = subprocess.run(
            [str(binary), "--gtest_list_tests"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"无法列出 GTest：{exc}", "LIST_FAILED"
    if result.returncode != 0:
        message = result.stderr.strip() or f"退出码 {result.returncode}"
        return None, message, "LIST_FAILED"
    mapping = {}
    suite = None
    for raw in result.stdout.splitlines():
        content = raw.split("#", 1)[0].rstrip()
        stripped = content.strip()
        if not stripped:
            continue
        if not content[:1].isspace() and stripped.endswith("."):
            suite = stripped
            continue
        if suite is None or not content[:1].isspace():
            continue
        test_name = stripped
        full_name = suite + test_name
        if "/TC_" not in full_name:
            continue
        case_name = full_name.rsplit("/", 1)[-1]
        if case_name in mapping:
            return None, f"case_name {case_name!r} 映射重复", "DUPLICATE_CASE"
        mapping[case_name] = full_name
    return mapping, None, None


def _selected_cases(csv_path, args):
    names = _read_csv_cases(csv_path, "TC_")
    names = [name for name in names if not name.startswith("TC_PF_")]
    if args.case:
        requested = set(args.case)
        names = [name for name in names if name in requested]
    if args.filter:
        names = [name for name in names if args.filter in name]
    return names


def _time_ms(value):
    if isinstance(value, (int, float)):
        return float(value) * 1000.0
    if isinstance(value, str):
        text = value.strip()
        try:
            if text.endswith("ms"):
                return float(text[:-2])
            if text.endswith("s"):
                return float(text[:-1]) * 1000.0
            return float(text) * 1000.0
        except ValueError:
            return None
    return None


def _gtest_records(path):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    records = {}
    for suite in payload.get("testsuites", []):
        suite_name = suite.get("name", "")
        for test in suite.get("testsuite", []):
            test_name = test.get("name", "")
            full_name = f"{suite_name}.{test_name}" if suite_name else test_name
            if "/TC_" not in full_name:
                continue
            case_name = full_name.rsplit("/", 1)[-1]
            failures = test.get("failures", []) or []
            result = str(test.get("result", "")).upper()
            status = str(test.get("status", "")).upper()
            if result == "SKIPPED" or status in {"SKIPPED", "NOTRUN"}:
                verdict = "SKIP"
            elif failures:
                verdict = "FAIL"
            else:
                verdict = "PASS"
            messages = []
            for failure in failures:
                if isinstance(failure, dict):
                    messages.append(str(failure.get("failure", failure.get("message", ""))))
                else:
                    messages.append(str(failure))
            records[case_name] = {
                "status": verdict,
                "ms": _time_ms(test.get("time")),
                "message": "\n".join(item for item in messages if item),
            }
    return records


def _run_tests(binary, full_names, json_path, timeout):
    gtest_filter = ":".join(full_names)
    command = [
        str(binary),
        f"--gtest_filter={gtest_filter}",
        f"--gtest_output=json:{json_path}",
    ]
    try:
        result = subprocess.run(command, timeout=timeout, check=False)
        return result.returncode, False, gtest_filter
    except subprocess.TimeoutExpired:
        return -1, True, gtest_filter
    except OSError:
        return -1, False, gtest_filter


def _case_results(expected, mapping, records, timed_out, process_code):
    results = []
    for name in expected:
        gtest_name = mapping.get(name)
        record = records.get(name)
        if gtest_name is None:
            status, message = "MISSING", "GTest 列表中无该 case_name"
            ms = None
        elif record is not None:
            status = record["status"]
            message = record["message"]
            ms = record["ms"]
        elif timed_out:
            status, message, ms = "TIMEOUT", "进程超时且未见该用例结果", None
        elif process_code != 0:
            status, message, ms = "CRASH", "进程异常且未见该用例结果", None
        else:
            status, message, ms = "MISSING", "结果 JSON 中无该用例", None
        results.append(
            {
                "name": name,
                "gtest_name": gtest_name,
                "status": status,
                "ms": ms,
                "message": message,
            }
        )
    return results


def _summary(cases):
    counts = {key: 0 for key in ("pass", "fail", "skip", "timeout", "crash", "missing")}
    for case in cases:
        counts[case["status"].lower()] += 1
    return {"expected": len(cases), **counts}


def _print_table(cases, summary, out_path):
    print(f"{'case_name':<24} {'status':<8} ms")
    for case in cases:
        elapsed = "-" if case["ms"] is None else f"{case['ms']:.3f}"
        print(f"{case['name']:<24} {case['status']:<8} {elapsed}")
    print(
        "summary: expected={expected} pass={pass} fail={fail} skip={skip} "
        "timeout={timeout} crash={crash} missing={missing}".format(**summary)
    )
    print(f"result: {out_path}")


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path, help="ops-blas 仓库根目录")
    parser.add_argument("--soc", required=True, help="目标 SoC，例如 ascend910b3")
    parser.add_argument("--device", type=int, default=0, help="编译期测试设备号，默认 0")
    parser.add_argument("--skip-build", action="store_true", help="复用上次编译产物")
    parser.add_argument("--build-timeout", type=int, default=1800, help="编译超时秒数")
    parser.add_argument("--timeout", type=int, default=3600, help="测试与列举超时秒数")
    parser.add_argument("--case", action="append", help="精确复跑 case_name，可重复")
    parser.add_argument("--filter", help="按 case_name 子串收窄精度集")
    parser.add_argument(
        "--run-id",
        default=datetime.now().strftime("%Y%m%dT%H%M%S"),
        help="结果运行标识",
    )
    parser.add_argument("--out", type=Path, help="结果 JSON 路径")
    return parser


def main(argv=None):
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = _parser().parse_args(raw_argv)
    if args.build_timeout <= 0 or args.timeout <= 0:
        _parser().error("timeout 必须为正整数")
    script_results = Path(__file__).resolve().parent / "results"
    if not RUN_ID_RE.fullmatch(args.run_id):
        print("RUN_ID_INVALID: run-id 只能含字母、数字、点、下划线和连字符", file=sys.stderr)
        return ENVIRONMENT_EXIT
    out_path = args.out or script_results / f"accuracy_{args.run_id}.json"
    started = _timestamp()
    arch = _arch_for_soc(args.soc)
    payload = _base_result(args, arch, started)
    run_dir = script_results / args.run_id / "accuracy"
    try:
        run_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        return _environment_error(
            payload, out_path, "RUN_ID_EXISTS", f"运行目录已存在：{run_dir}"
        )
    if arch is None:
        return _environment_error(
            payload, out_path, "CSV_NOT_DEPLOYED", f"SoC {args.soc!r} 无 arch 映射"
        )
    csv_path = _find_source_csv(args.repo, arch)
    if csv_path is None:
        return _environment_error(
            payload, out_path, "CSV_NOT_DEPLOYED", "源码树中找不到部署 CSV"
        )
    payload["csv_path"] = str(csv_path)
    payload["csv_sha256"] = _sha256(csv_path)
    device_explicit = any(
        item == "--device" or item.startswith("--device=") for item in raw_argv
    )
    if args.skip_build:
        if device_explicit:
            print("设备号在编译期固定（-DTEST_DEVICE_ID），跳过编译时以上次编译为准")
    else:
        build_log = run_dir / "build.log"
        if _run_build(args, build_log) != 0:
            return _environment_error(
                payload, out_path, "BUILD_FAILED", f"构建失败，见 {build_log}"
            )
        reason, message = _check_build_lists(args.repo)
        if reason:
            return _environment_error(payload, out_path, reason, message)
    binary = _find_binary(args.repo)
    if binary is None:
        return _environment_error(
            payload, out_path, "BINARY_NOT_FOUND", "三条构建目录规则均无测试二进制"
        )
    payload["binary"] = str(binary)
    payload["binary_sha256"] = _sha256(binary)
    mapping, message, reason = _list_tests(binary, args.timeout)
    if reason:
        return _environment_error(payload, out_path, reason, message)
    # 期望集来自任务包自带的 CSV（契约），不是部署 CSV。
    expected = _selected_cases(Path(__file__).resolve().parent / CSV_NAME, args)
    full_names = [mapping[name] for name in expected if name in mapping]
    gtest_json = run_dir / "gtest.json"
    process_code, timed_out, gtest_filter = _run_tests(
        binary, full_names, gtest_json, args.timeout
    )
    payload["gtest_filter"] = gtest_filter
    records = _gtest_records(gtest_json)
    expected_records = [records[name] for name in expected if name in records]
    if process_code != 0 and expected_records and all(
        record.get("status") == "PASS" for record in expected_records
    ):
        records = {}
    cases = _case_results(expected, mapping, records, timed_out, process_code)
    summary = _summary(cases)
    exit_code = (
        0
        if summary["expected"] > 0 and summary["pass"] == summary["expected"]
        else TEST_FAILURE_EXIT
    )
    payload["cases"] = cases
    payload["summary"] = summary
    payload["exit_code"] = exit_code
    payload["finished"] = _timestamp()
    _atomic_json(out_path, payload)
    _print_table(cases, summary, out_path)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
