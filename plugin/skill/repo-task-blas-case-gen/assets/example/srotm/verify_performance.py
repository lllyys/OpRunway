#!/usr/bin/env python3
"""用 msprof kernel 耗时验收 CSV 驱动的性能用例。"""

# ===== 渲染常量区开始 =====
OP = "srotm"
FAMILY = "rotm"
CSV_NAME = "srotm_test.csv"
PACKAGE_CSV_SHA256 = "f250d915f7912ab6453287ac61e03b3cecda1378bba19e47fa9070877160ddf8"
GENERATOR_VERSION = int("1")
PERF_KEY = [""]
PROFILE_ASSIGNS_JSON = '''{}'''
PERF_THRESHOLD = float("0.8")
PERF_FILTER = "*TC_PF_*"
# ===== 渲染常量区结束 =====

import argparse
import csv
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import statistics
import subprocess
import sys


ENVIRONMENT_EXIT = 3
FAILURE_EXIT = 1
INSUFFICIENT_EXIT = 2
REPEATS = 5

# 公开文档给出 op_summary；目录和列名需在目标环境 spike 后只改这些常量。
OP_SUMMARY_GLOB = "PROF_*/mindstudio_profiler_output/op_summary_*.csv"
OP_SUMMARY_COLUMNS = {
    "task_type": "Task Type",
    "duration_us": "Task Duration(us)",
}
KERNEL_TASK_TYPES = frozenset({"AI_CORE", "AI_VECTOR_CORE", "MIX_AIC", "MIX_AIV"})
INTEGER_RE = re.compile(r"^[+-]?\d+$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
PERF_KEY = [key for key in PERF_KEY if key]
PROFILE_ASSIGNS = json.loads(PROFILE_ASSIGNS_JSON)


class ProfileParseError(ValueError):
    """表示 msprof op_summary 结构无法按当前表驱动协议解析。"""


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


def _read_csv_rows(path, prefix):
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
            if len(values) != len(header):
                raise ValueError("CSV 行列数与表头不一致")
            row = dict(zip(header, values))
            if row.get("case_name", "").startswith(prefix):
                rows.append(row)
    return rows


def _selected_rows(csv_path, args):
    rows = _read_csv_rows(csv_path, "TC_PF_")
    if args.case:
        requested = set(args.case)
        rows = [row for row in rows if row["case_name"] in requested]
    if args.filter:
        rows = [row for row in rows if args.filter in row["case_name"]]
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
        "gpu_baseline_path": None,
        "msprof": None,
        "gtest_filter": None,
        "cases": [],
        "summary": {},
        "exit_code": None,
        "started": started,
        "finished": None,
    }


def _environment_error(payload, out_path, reason, message):
    payload["summary"] = {
        "expected": len(payload["cases"]),
        "status": "证据不足",
        "timing_scope": "unknown",
        "threshold": PERF_THRESHOLD,
        "scope_caveat": True,
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
        full_name = suite + stripped
        if "/TC_" not in full_name:
            continue
        case_name = full_name.rsplit("/", 1)[-1]
        if case_name in mapping:
            return None, f"case_name {case_name!r} 映射重复", "DUPLICATE_CASE"
        mapping[case_name] = full_name
    return mapping, None, None


def _normalize_key_value(value):
    text = str(value).strip()
    return int(text) if INTEGER_RE.fullmatch(text) else text


def _key_for_row(row):
    missing = [key for key in PERF_KEY if key != "profile" and key not in row]
    if missing:
        raise ValueError("缺少性能键：" + ", ".join(missing))
    values = []
    for key in PERF_KEY:
        if key != "profile" or key in row:
            values.append(_normalize_key_value(row[key]))
            continue
        matches = [
            name
            for name, assign in PROFILE_ASSIGNS.items()
            if all(row.get(enum_name) == enum_value for enum_name, enum_value in assign.items())
        ]
        if len(matches) != 1:
            raise ValueError(f"性能行无法唯一映射 profile：{matches}")
        values.append(matches[0])
    return tuple(values)


def _load_gpu_baseline(path):
    metadata = {}
    data_lines = []
    with Path(path).open(encoding="utf-8", newline="") as stream:
        for raw in stream:
            stripped = raw.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                key, separator, value = stripped[1:].strip().partition("=")
                if separator:
                    metadata[key.strip()] = value.strip()
                continue
            data_lines.append(raw)
    if not data_lines:
        raise ValueError("gpu_baseline.csv 缺表头")
    rows = list(csv.DictReader(data_lines))
    expected = ["id", *PERF_KEY, "gpu_ms"]
    if rows and list(rows[0]) != expected:
        raise ValueError("gpu_baseline.csv 表头与 PERF_KEY 不一致")
    if not rows and next(csv.reader([data_lines[0]])) != expected:
        raise ValueError("gpu_baseline.csv 表头与 PERF_KEY 不一致")
    references = {}
    for index, row in enumerate(rows, 2):
        key = _key_for_row(row)
        if key in references:
            raise ValueError(f"gpu_baseline.csv 第 {index} 行性能键重复")
        raw_gpu_ms = row.get("gpu_ms", "").strip()
        if not raw_gpu_ms:
            references[key] = None
            continue
        try:
            gpu_ms = float(raw_gpu_ms)
        except ValueError as exc:
            raise ValueError(f"gpu_baseline.csv 第 {index} 行 gpu_ms 非数值") from exc
        if gpu_ms <= 0:
            raise ValueError(f"gpu_baseline.csv 第 {index} 行 gpu_ms 必须大于 0")
        references[key] = gpu_ms
    return metadata, references


def _resolve_msprof(override):
    if override is not None:
        candidate = shutil.which(str(override))
        if candidate:
            return Path(candidate).resolve()
        path = Path(override).expanduser()
        return path.resolve() if path.is_file() and os.access(path, os.X_OK) else None
    found = shutil.which("msprof")
    if found:
        return Path(found).resolve()
    toolkit = os.environ.get("ASCEND_TOOLKIT_HOME")
    candidates = []
    if toolkit:
        candidates.append(Path(toolkit) / "tools" / "profiler" / "bin" / "msprof")
    candidates.append(
        Path("/usr/local/Ascend/ascend-toolkit/latest/tools/profiler/bin/msprof")
    )
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    return None


def parse_op_summary(output_dir):
    """返回指定 msprof 输出目录内 kernel duration 总和与 kernel 行数。"""
    files = sorted(Path(output_dir).glob(OP_SUMMARY_GLOB))
    kernel_us = 0.0
    launches = 0
    for path in files:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            columns = set(reader.fieldnames or [])
            required = set(OP_SUMMARY_COLUMNS.values())
            if not required.issubset(columns):
                missing = ", ".join(sorted(required - columns))
                raise ProfileParseError(f"{path.name} 缺列：{missing}")
            for row_index, row in enumerate(reader, 2):
                task_type = row[OP_SUMMARY_COLUMNS["task_type"]].strip()
                if task_type not in KERNEL_TASK_TYPES:
                    continue
                raw_duration = row[OP_SUMMARY_COLUMNS["duration_us"]].strip()
                try:
                    duration = float(raw_duration)
                except ValueError as exc:
                    raise ProfileParseError(
                        f"{path.name} 第 {row_index} 行 duration 非数值"
                    ) from exc
                if duration < 0:
                    raise ProfileParseError(
                        f"{path.name} 第 {row_index} 行 duration 为负"
                    )
                kernel_us += duration
                launches += 1
    return kernel_us, launches


def _run_process(command, timeout, cwd=None):
    try:
        result = subprocess.run(
            command,
            cwd=None if cwd is None else str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        return result.returncode, result.stdout, None
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return None, output, "TIMEOUT"
    except OSError as exc:
        return None, str(exc), "CRASH"


def _write_log(path, content):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def _case_record(row, gtest_name):
    return {
        "name": row["case_name"],
        "gtest_name": gtest_name,
        "status": None,
        "kernel_us": None,
        "samples": [],
        "launches": [],
        "gpu_ms": None,
        "ratio": None,
        "spread": None,
        "verdict": None,
        "message": "",
    }


def _with_scope_caveat(verdict, scope_caveat):
    return verdict + " (scope caveat)" if scope_caveat else verdict


def _measure_case(args, binary, msprof, row, gtest_name, references, profile_root):
    record = _case_record(row, gtest_name)
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", row["case_name"])
    warmup = [str(binary), f"--gtest_filter={gtest_name}"]
    code, output, problem = _run_process(warmup, args.timeout)
    _write_log(profile_root / safe_name / "warmup.log", output)
    if problem:
        record["status"] = problem
        record["verdict"] = problem
        record["message"] = "warm-up 未完成"
        return record
    if code != 0:
        record["status"] = "FAIL"
        record["verdict"] = "FAIL(warmup)"
        record["message"] = f"warm-up 退出码 {code}"
        return record

    application = shlex.join([str(binary), f"--gtest_filter={gtest_name}"])
    for repeat in range(1, args.repeats + 1):
        output_dir = profile_root / safe_name / f"r{repeat}"
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        command = [
            str(msprof),
            f"--application={application}",
            f"--output={output_dir}",
        ]
        code, output, problem = _run_process(command, args.timeout)
        _write_log(profile_root / safe_name / f"r{repeat}.log", output)
        if problem:
            record["status"] = problem
            record["verdict"] = problem
            record["message"] = f"第 {repeat} 次 msprof 未完成"
            return record
        if code != 0:
            record["status"] = "CRASH"
            record["verdict"] = "CRASH"
            record["message"] = f"第 {repeat} 次 msprof 退出码 {code}"
            return record
        # 采集只产 task_time 与 sqlite；op_summary 由 export 生成，必须显式导出。
        export = [str(msprof), "--export=on", f"--output={output_dir}"]
        code, output, problem = _run_process(export, args.timeout)
        _write_log(profile_root / safe_name / f"r{repeat}.export.log", output)
        if problem:
            record["status"] = problem
            record["verdict"] = problem
            record["message"] = f"第 {repeat} 次 msprof export 未完成"
            return record
        if code != 0:
            # export 是分析工具失败，不是被测算子崩溃：归入拿不到 kernel 证据。
            record["status"] = "NO_KERNEL"
            record["verdict"] = "NO_KERNEL"
            record["message"] = f"第 {repeat} 次 msprof export 退出码 {code}，未生成 op_summary"
            return record
        try:
            kernel_us, launches = parse_op_summary(output_dir)
        except (OSError, UnicodeError, csv.Error, ProfileParseError) as exc:
            record["status"] = "NO_KERNEL"
            record["verdict"] = "NO_KERNEL"
            record["message"] = f"第 {repeat} 次解析失败：{exc}"
            return record
        if launches == 0:
            record["status"] = "NO_KERNEL"
            record["verdict"] = "NO_KERNEL"
            record["message"] = f"第 {repeat} 次采样没有 kernel 行"
            return record
        record["samples"].append(kernel_us)
        record["launches"].append(launches)

    median_us = float(statistics.median(record["samples"]))
    if median_us <= 0:
        record["status"] = "NO_KERNEL"
        record["verdict"] = "NO_KERNEL"
        record["message"] = "kernel duration 中位数不大于 0"
        return record
    record["kernel_us"] = median_us
    record["spread"] = (
        max(record["samples"]) - min(record["samples"])
    ) / median_us
    reference = references.get(_key_for_row(row))
    if reference is None:
        record["status"] = "NO_REF"
        record["verdict"] = "NO_REF"
        return record
    record["gpu_ms"] = reference
    record["ratio"] = reference / (median_us / 1000.0)
    if record["ratio"] >= PERF_THRESHOLD:
        record["status"] = "PASS"
        record["verdict"] = "PASS"
    else:
        record["status"] = "FAIL"
        record["verdict"] = "FAIL"
    return record


def _summarize(cases, timing_scope):
    counts = {
        name: sum(case["status"] == name for case in cases)
        for name in ("PASS", "FAIL", "NO_REF", "NO_KERNEL", "CRASH", "TIMEOUT", "MISSING")
    }
    insufficient = counts["NO_KERNEL"] + counts["CRASH"]
    insufficient += counts["TIMEOUT"] + counts["MISSING"]
    if insufficient:
        status = "证据不足"
        exit_code = INSUFFICIENT_EXIT
    elif counts["FAIL"]:
        status = "不通过"
        exit_code = FAILURE_EXIT
    elif counts["NO_REF"] or not cases:
        status = "NO_REF"
        exit_code = 0
    else:
        status = "通过"
        exit_code = 0
    return {
        "expected": len(cases),
        "pass": counts["PASS"],
        "fail": counts["FAIL"],
        "no_ref": counts["NO_REF"],
        "no_kernel": counts["NO_KERNEL"],
        "crash": counts["CRASH"],
        "timeout": counts["TIMEOUT"],
        "missing": counts["MISSING"],
        "status": status,
        "timing_scope": timing_scope,
        "threshold": PERF_THRESHOLD,
        "scope_caveat": timing_scope != "kernel",
    }, exit_code


def _print_cases(cases, summary):
    print("case_name status kernel_us gpu_ms ratio spread verdict")
    for case in cases:
        values = [
            case["name"],
            case["status"],
            case["kernel_us"],
            case["gpu_ms"],
            case["ratio"],
            case["spread"],
            case["verdict"],
        ]
        print(" ".join("-" if value is None else str(value) for value in values))
    print(
        f"summary: {summary['status']}，PASS={summary['pass']}，FAIL={summary['fail']}，"
        f"NO_REF={summary['no_ref']}，证据不足="
        f"{summary['no_kernel'] + summary['crash'] + summary['timeout'] + summary['missing']}"
    )


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path, help="ops-blas 仓库根目录")
    parser.add_argument("--soc", required=True, help="目标 SoC，例如 ascend910b3")
    parser.add_argument("--device", type=int, default=0, help="编译期测试设备号，默认 0")
    parser.add_argument("--skip-build", action="store_true", help="复用上次编译产物")
    parser.add_argument("--build-timeout", type=int, default=1800, help="编译超时秒数")
    parser.add_argument("--timeout", type=int, default=3600, help="每个进程的超时秒数")
    parser.add_argument("--repeats", type=int, default=REPEATS, help="msprof 采样次数")
    parser.add_argument("--msprof", help="覆盖 msprof 可执行文件路径")
    parser.add_argument(
        "--keep-prof",
        action="store_true",
        default=True,
        help="保留 msprof 原始目录；当前默认保留",
    )
    parser.add_argument("--case", action="append", help="精确复跑 case_name，可重复")
    parser.add_argument("--filter", help="按 case_name 子串收窄性能集")
    parser.add_argument(
        "--run-id",
        default=datetime.now().strftime("%Y%m%dT%H%M%S"),
        help="结果运行标识",
    )
    parser.add_argument("--out", type=Path, help="结果 JSON 路径")
    return parser


def main(argv=None):
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = _parser()
    args = parser.parse_args(raw_argv)
    if args.build_timeout <= 0 or args.timeout <= 0 or args.repeats <= 0:
        parser.error("timeout 与 repeats 必须为正整数")
    results = Path(__file__).resolve().parent / "results"
    if not RUN_ID_RE.fullmatch(args.run_id):
        print("RUN_ID_INVALID: run-id 只能含字母、数字、点、下划线和连字符", file=sys.stderr)
        return ENVIRONMENT_EXIT
    out_path = args.out or results / f"performance_{args.run_id}.json"
    arch = _arch_for_soc(args.soc)
    payload = _base_result(args, arch, _timestamp())
    run_dir = results / args.run_id / "performance"
    try:
        run_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        return _environment_error(
            payload, out_path, "RUN_ID_EXISTS", f"运行目录已存在：{run_dir}"
        )
    if arch is None:
        return _environment_error(
            payload, out_path, "CSV_NOT_DEPLOYED", "SoC 无 arch 映射"
        )
    csv_path = _find_source_csv(args.repo, arch)
    if csv_path is None:
        return _environment_error(
            payload, out_path, "CSV_NOT_DEPLOYED", "找不到部署 CSV"
        )
    payload["csv_path"] = str(csv_path)
    payload["csv_sha256"] = _sha256(csv_path)
    if payload["csv_sha256"] != PACKAGE_CSV_SHA256:
        return _environment_error(
            payload, out_path, "CSV_MISMATCH", "CSV SHA-256 不一致"
        )
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
                payload, out_path, "BUILD_FAILED", f"见 {build_log}"
            )
        reason, message = _check_build_lists(args.repo)
        if reason:
            return _environment_error(payload, out_path, reason, message)
    binary = _find_binary(args.repo)
    if binary is None:
        return _environment_error(
            payload, out_path, "BINARY_NOT_FOUND", "找不到测试二进制"
        )
    payload["binary"] = str(binary)
    payload["binary_sha256"] = _sha256(binary)
    mapping, message, reason = _list_tests(binary, args.timeout)
    if reason:
        return _environment_error(payload, out_path, reason, message)
    try:
        expected_rows = _selected_rows(csv_path, args)
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        return _environment_error(payload, out_path, "CSV_INVALID", str(exc))
    payload["gtest_filter"] = ":".join(
        mapping[row["case_name"]]
        for row in expected_rows
        if row["case_name"] in mapping
    )
    baseline_path = Path(__file__).resolve().parent / "gpu_baseline.csv"
    payload["gpu_baseline_path"] = str(baseline_path)
    try:
        metadata, references = _load_gpu_baseline(baseline_path)
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        return _environment_error(payload, out_path, "BASELINE_INVALID", str(exc))
    timing_scope = metadata.get("timing_scope", "unspecified")
    mapped_rows = [row for row in expected_rows if row["case_name"] in mapping]
    msprof = _resolve_msprof(args.msprof) if mapped_rows else None
    if mapped_rows and msprof is None:
        return _environment_error(
            payload, out_path, "MSPROF_NOT_FOUND", "找不到可执行的 msprof"
        )
    payload["msprof"] = None if msprof is None else str(msprof)
    profile_root = run_dir / "prof"
    cases = []
    scope_caveat = timing_scope != "kernel"
    for row in expected_rows:
        name = row["case_name"]
        gtest_name = mapping.get(name)
        if gtest_name is None:
            record = _case_record(row, None)
            record["status"] = "MISSING"
            record["verdict"] = "MISSING"
            record["message"] = "部署 CSV 用例未出现在 --gtest_list_tests"
        else:
            record = _measure_case(
                args, binary, msprof, row, gtest_name, references, profile_root
            )
        record["verdict"] = _with_scope_caveat(record["verdict"], scope_caveat)
        cases.append(record)
    payload["cases"] = cases
    summary, exit_code = _summarize(cases, timing_scope)
    payload["summary"] = summary
    payload["exit_code"] = exit_code
    payload["finished"] = _timestamp()
    _atomic_json(out_path, payload)
    _print_cases(cases, summary)
    print(f"result: {out_path}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
