#!/usr/bin/env python3
"""检查 BLAS 验收环境与契约，并从任务包证据生成最终结论。"""

import argparse
import csv
from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


ENVIRONMENT_EXIT = 3
CONTRACT_EXIT = 2
INSUFFICIENT_EXIT = 2
FAILURE_EXIT = 1
PACKAGE_FILES = (
    "gen_csv.py",
    "verify_accuracy.py",
    "verify_performance.py",
    "README.md",
    "gpu_baseline.csv",
)
ACCURACY_STATUSES = {
    "PASS",
    "FAIL",
    "SKIP",
    "TIMEOUT",
    "CRASH",
    "MISSING",
}
PERFORMANCE_STATUSES = {"通过", "不通过", "NO_REF", "证据不足"}


def _timestamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _atomic_json(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, target)


def _atomic_text(path, text):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, target)


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


def _unique_files(candidates):
    result = []
    seen = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen or not candidate.is_file():
            continue
        seen.add(resolved)
        result.append(resolved)
    return result


def _source_csv_matches(repo, op, arch):
    test_root = repo / "test"
    csv_name = f"{op}_test.csv"
    candidates = [test_root / op / arch / csv_name]
    candidates.extend(sorted(test_root.glob(f"*/{op}/{arch}/{csv_name}")))
    if len(op) > 1:
        candidates.append(test_root / op[1:] / arch / csv_name)
    return _unique_files(candidates)


def _read_csv_case_names(path, performance):
    header = None
    names = []
    with Path(path).open(encoding="utf-8", newline="") as stream:
        for raw in stream:
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            values = next(csv.reader([raw]))
            if header is None:
                header = values
                if "case_name" not in header:
                    raise ValueError("CSV 表头缺 case_name")
                continue
            if len(values) != len(header):
                raise ValueError("CSV 行列数与表头不一致")
            name = dict(zip(header, values)).get("case_name", "")
            is_performance = name.startswith("TC_PF_")
            if name.startswith("TC_") and is_performance == performance:
                names.append(name)
    if header is None:
        raise ValueError("CSV 没有表头")
    return names


def _load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path} 读不出来：{exc}") from exc


def _case_gen_module_path():
    skill_root = Path(__file__).resolve().parents[2]
    return skill_root / "repo-task-blas-case-gen" / "scripts" / "package.py"


def _load_case_gen():
    path = _case_gen_module_path()
    if not path.is_file():
        raise FileNotFoundError(
            "找不到同插件的 repo-task-blas-case-gen/scripts/package.py"
        )
    spec = importlib.util.spec_from_file_location("repo_task_blas_case_gen", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {path}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def _load_package_facts(package):
    module = _load_case_gen()
    path = package / "gen_csv.py"
    if not path.is_file():
        raise ValueError(f"任务包缺文件：{path.name}")
    try:
        facts = module._load_python_facts(path)
    except (OSError, UnicodeError, SyntaxError, ValueError) as exc:
        raise ValueError(f"gen_csv.py 的 FACTS 读不出来：{exc}") from exc
    problems = module.validate(facts)
    return module, facts, problems


def _status_line(label, status, detail):
    print(f"{status:<2} {label}: {detail}")


def _find_header(name):
    roots = [
        Path("/usr/include"),
        Path("/usr/local/include"),
        Path("/usr/include/x86_64-linux-gnu"),
        Path("/usr/include/aarch64-linux-gnu"),
        Path("/usr/include/openblas"),
        Path("/usr/local/include/openblas"),
        Path("/opt/homebrew/include"),
        Path("/opt/OpenBLAS/include"),
    ]
    for variable in ("OPENBLAS_HOME", "LAPACK_HOME"):
        value = os.environ.get(variable)
        if value:
            roots.append(Path(value) / "include")
    for variable in ("CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH"):
        for raw in os.environ.get(variable, "").split(os.pathsep):
            if raw:
                roots.append(Path(raw))
    for root in roots:
        candidate = root / name
        if candidate.is_file():
            return candidate.resolve()
    return None


def _find_cann():
    roots = []
    for variable in ("ASCEND_HOME", "ASCEND_TOOLKIT_HOME"):
        value = os.environ.get(variable)
        if value:
            roots.append(Path(value).expanduser())
    roots.append(Path("/usr/local/Ascend/ascend-toolkit/latest"))
    seen = set()
    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        set_env = resolved / "set_env.sh"
        if set_env.is_file():
            return resolved, set_env
    return None, None


def _find_msprof(cann_root):
    found = shutil.which("msprof")
    if found:
        return Path(found).resolve()
    if cann_root is not None:
        candidate = cann_root / "tools" / "profiler" / "bin" / "msprof"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    return None


def _npu_state(device):
    executable = shutil.which("npu-smi")
    if executable is None:
        return "缺失", "PATH 中无 npu-smi"
    try:
        result = subprocess.run(
            [executable, "info"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "未知", f"npu-smi 执行失败：{exc}"
    if result.returncode != 0:
        return "未知", f"npu-smi 退出码 {result.returncode}"
    output = result.stdout
    if re.search(rf"(?m)^\s*{device}\s+", output) is None:
        return "未知", f"输出中无法确认设备 {device}"
    process_lines = [
        line for line in output.splitlines()
        if re.search(r"\b\d{2,}\b", line)
        and re.search(r"process|python|pytest|gtest", line, re.IGNORECASE)
    ]
    if process_lines:
        return "未知", f"设备可见，但疑似有 {len(process_lines)} 条进程记录"
    return "OK", f"设备 {device} 可见，未解析到占用进程"


def command_env(args):
    repo = args.repo.resolve()
    out_path = args.out.resolve() / "env.json"
    checks = []
    hard_failures = []

    def record(name, status, detail, hard=False):
        item = {"name": name, "status": status, "detail": detail, "hard": hard}
        checks.append(item)
        _status_line(name, status, detail)
        if hard and status != "OK":
            hard_failures.append(name)

    python_ok = sys.version_info >= (3, 8)
    record(
        "python3 >= 3.8",
        "OK" if python_ok else "缺失",
        sys.version.split()[0],
    )
    for program in ("cmake", "g++"):
        path = shutil.which(program)
        record(program, "OK" if path else "缺失", path or "PATH 中未找到")
    build = repo / "build.sh"
    record(
        "build.sh",
        "OK" if build.is_file() and os.access(build, os.R_OK) else "缺失",
        str(build),
        hard=True,
    )
    frame = repo / "test" / "frame" / "csv_loader.h"
    record("csv_loader.h", "OK" if frame.is_file() else "缺失", str(frame), hard=True)
    header = repo / "include" / "cann_ops_blas.h"
    record("cann_ops_blas.h", "OK" if header.is_file() else "缺失", str(header), hard=True)
    cann_root, set_env = _find_cann()
    record(
        "CANN set_env.sh",
        "OK" if set_env else "缺失",
        str(set_env) if set_env else "ASCEND_HOME 与默认目录均未找到",
        hard=True,
    )
    msprof = _find_msprof(cann_root)
    record("msprof", "OK" if msprof else "缺失", str(msprof) if msprof else "未找到")
    npu_status, npu_detail = _npu_state(args.device)
    record("npu-smi", npu_status, npu_detail)
    for header_name in ("cblas.h", "lapacke.h"):
        found = _find_header(header_name)
        record(
            header_name,
            "OK" if found else "缺失",
            str(found) if found else "常见 include 路径中未找到",
        )
    exit_code = ENVIRONMENT_EXIT if hard_failures else 0
    payload = {
        "command": "env",
        "repo": str(repo),
        "soc": args.soc,
        "device": args.device,
        "checks": checks,
        "hard_failures": hard_failures,
        "exit_code": exit_code,
        "generated_at": _timestamp(),
    }
    _atomic_json(out_path, payload)
    print(f"env.json: {out_path}")
    return exit_code


def _strip_comments(source):
    without_blocks = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", without_blocks)


def _declaration_from_source(source, symbol):
    cleaned = _strip_comments(source)
    pattern = re.compile(
        rf"(?m)^[ \t]*(?P<returns>[A-Za-z_]\w*(?:[ \t*]+[A-Za-z_]\w*)*)"
        rf"[ \t]+{re.escape(symbol)}[ \t]*\("
    )
    for match in pattern.finditer(cleaned):
        open_index = cleaned.find("(", match.start())
        depth = 0
        close_index = None
        for index in range(open_index, len(cleaned)):
            character = cleaned[index]
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    close_index = index
                    break
        if close_index is None:
            continue
        tail = cleaned[close_index + 1:]
        if re.match(r"\s*;", tail) is None:
            continue
        return match.group("returns"), cleaned[open_index + 1:close_index]
    return None


def _split_parameters(text):
    if not text.strip() or text.strip() == "void":
        return []
    result = []
    start = 0
    depth = 0
    for index, character in enumerate(text):
        if character in "([":
            depth += 1
        elif character in ")]":
            depth -= 1
        elif character == "," and depth == 0:
            result.append(text[start:index].strip())
            start = index + 1
    result.append(text[start:].strip())
    return result


def _remove_parameter_name(declaration):
    pattern = re.compile(r"\b[A-Za-z_]\w*\s*((?:\[[^\]]*\]\s*)*)$")
    match = pattern.search(declaration)
    if match is None:
        return declaration.strip()
    suffix = match.group(1)
    return (declaration[:match.start()] + suffix).strip()


def _normalize_type(raw):
    text = re.sub(r"\[[^\]]*\]", "*", raw.strip())
    first_star = text.find("*")
    prefix = text if first_star < 0 else text[:first_star]
    suffix = "" if first_star < 0 else text[first_star:]
    if re.search(r"\bconst\b", prefix) and not prefix.lstrip().startswith("const "):
        prefix = re.sub(r"\bconst\b", "", prefix)
        prefix = "const " + prefix.strip()
    text = prefix + suffix
    return re.sub(r"\s+", "", text)


def _declaration_candidates(repo, facts):
    main = repo / "include" / "cann_ops_blas.h"
    candidates = [main]
    family_root = repo / "blas" / facts["family"] / facts["op"]
    if family_root.is_dir():
        candidates.extend(sorted(family_root.rglob("*.h")))
    include = repo / "include"
    if include.is_dir():
        candidates.extend(sorted(include.rglob("*.h")))
    return _unique_files(candidates)


def _compare_declaration(repo, facts):
    symbol = facts["symbol"]
    found = None
    found_path = None
    for path in _declaration_candidates(repo, facts):
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        declaration = _declaration_from_source(source, symbol)
        if declaration is not None:
            found = declaration
            found_path = path
            break
    expected_return = facts["returns"]
    expected_params = [param["ctype"] for param in facts["params"]]
    if found is None:
        return {
            "status": "DECL_NOT_FOUND",
            "symbol": symbol,
            "source": None,
            "expected_return": expected_return,
            "expected_params": expected_params,
            "actual_return": None,
            "actual_params": None,
            "mismatches": [f"DECL_NOT_FOUND: 找不到 {symbol} 的声明"],
        }
    actual_return, raw_params = found
    actual_params = [_remove_parameter_name(item) for item in _split_parameters(raw_params)]
    mismatches = []
    if _normalize_type(expected_return) != _normalize_type(actual_return):
        mismatches.append(
            f"returns: FACTS={expected_return!r}，声明={actual_return.strip()!r}"
        )
    limit = max(len(expected_params), len(actual_params))
    for index in range(limit):
        expected = expected_params[index] if index < len(expected_params) else None
        actual = actual_params[index] if index < len(actual_params) else None
        if expected is None or actual is None:
            mismatches.append(f"params[{index}]: FACTS={expected!r}，声明={actual!r}")
            continue
        if _normalize_type(expected) != _normalize_type(actual):
            name = facts["params"][index]["name"]
            mismatches.append(
                f"params[{index}]({name}): FACTS={expected!r}，声明={actual!r}"
            )
    return {
        "status": "MATCH" if not mismatches else "DECL_MISMATCH",
        "symbol": symbol,
        "source": str(found_path),
        "expected_return": expected_return,
        "expected_params": expected_params,
        "actual_return": actual_return.strip(),
        "actual_params": actual_params,
        "mismatches": mismatches,
    }


def _read_nonempty_lines(path):
    try:
        return [line.strip() for line in Path(path).read_text().splitlines() if line.strip()]
    except OSError:
        return []


def _build_state(repo, op):
    build_root = repo / "build" / "test"
    built_path = build_root / "built_tests.list"
    skipped_path = build_root / "skipped_tests.list"
    if not build_root.is_dir() or not built_path.is_file():
        return {"status": "未编译", "hard": False, "detail": "build/test 尚无清单"}
    built = _read_nonempty_lines(built_path)
    skipped = _read_nonempty_lines(skipped_path)
    skip_matches = [line for line in skipped if line.split("|", 1)[0] == op]
    if skip_matches:
        return {"status": "OP_SKIPPED", "hard": True, "detail": skip_matches[0]}
    if op not in built and f"{op}_test" not in built:
        return {
            "status": "NOT_BUILT",
            "hard": True,
            "detail": "built_tests.list 不含目标算子",
        }
    return {"status": "BUILT", "hard": False, "detail": "构建清单包含目标算子"}


def command_check(args):
    package = args.package.resolve()
    repo = args.repo.resolve()
    out_path = args.out.resolve() if args.out else Path.cwd() / "check.json"
    errors = []
    warnings = []
    payload = {
        "command": "check",
        "package": str(package),
        "repo": str(repo),
        "soc": args.soc,
        "device": args.device,
        "checks": {},
        "errors": errors,
        "warnings": warnings,
        "exit_code": None,
        "generated_at": _timestamp(),
    }
    try:
        module, facts, fact_problems = _load_package_facts(package)
    except FileNotFoundError as exc:
        print(f"CASE_GEN_NOT_FOUND: {exc}", file=sys.stderr)
        payload["errors"].append(f"CASE_GEN_NOT_FOUND: {exc}")
        payload["exit_code"] = ENVIRONMENT_EXIT
        _atomic_json(out_path, payload)
        print(f"check.json: {out_path}")
        return ENVIRONMENT_EXIT
    except (ImportError, ValueError, KeyError) as exc:
        errors.append(str(exc))
        facts = None
        module = None
        fact_problems = []
    if facts is None:
        payload["exit_code"] = CONTRACT_EXIT
        _atomic_json(out_path, payload)
        for message in errors:
            print(message, file=sys.stderr)
        print(f"check.json: {out_path}")
        return CONTRACT_EXIT
    payload["op"] = facts.get("op")
    payload["family"] = facts.get("family")
    payload["symbol"] = facts.get("symbol")
    payload["checks"]["facts"] = {
        "status": "OK" if not fact_problems else "INVALID",
        "header": module._header_columns(facts) if not fact_problems else None,
        "problems": fact_problems,
    }
    errors.extend(f"FACTS: {message}" for message in fact_problems)
    op = facts.get("op", "")
    required = [*PACKAGE_FILES, f"{op}_test.csv"]
    missing = [name for name in required if not (package / name).is_file()]
    payload["checks"]["six_files"] = {
        "status": "OK" if not missing else "MISSING",
        "files": required,
        "missing": missing,
    }
    errors.extend(f"任务包缺文件：{name}" for name in missing)
    declaration = _compare_declaration(repo, facts)
    payload["checks"]["declaration"] = declaration
    errors.extend(declaration["mismatches"])
    arch = _arch_for_soc(args.soc)
    csv_check = {"status": None, "arch": arch, "package": None, "deployed": None}
    package_csv = package / f"{op}_test.csv"
    if arch is None:
        csv_check["status"] = "CSV_NOT_DEPLOYED"
        errors.append(f"CSV_NOT_DEPLOYED: SoC {args.soc!r} 无 arch 映射")
    elif not package_csv.is_file():
        csv_check["status"] = "PACKAGE_CSV_MISSING"
    else:
        csv_check["package"] = {
            "path": str(package_csv),
            "sha256": _sha256(package_csv),
        }
        matches = _source_csv_matches(repo, op, arch)
        if not matches:
            csv_check["status"] = "CSV_NOT_DEPLOYED"
            errors.append("CSV_NOT_DEPLOYED: 三条源码目录规则均找不到部署 CSV")
        elif len(matches) > 1:
            csv_check["status"] = "CSV_AMBIGUOUS"
            csv_check["matches"] = [str(path) for path in matches]
            errors.append("CSV_AMBIGUOUS: 三条源码目录规则命中多份部署 CSV")
        else:
            deployed = matches[0]
            deployed_sha = _sha256(deployed)
            csv_check["deployed"] = {"path": str(deployed), "sha256": deployed_sha}
            if deployed_sha != csv_check["package"]["sha256"]:
                csv_check["status"] = "CSV_MISMATCH"
                errors.append("CSV_MISMATCH: 部署 CSV 与任务包 CSV 的 SHA-256 不一致")
            else:
                csv_check["status"] = "MATCH"
    payload["checks"]["csv"] = csv_check
    build = _build_state(repo, op)
    payload["checks"]["build"] = build
    if build["hard"]:
        errors.append(f"{build['status']}: {build['detail']}")
    elif build["status"] == "未编译":
        warnings.append("未编译：A3 将调用 build.sh 生成构建清单")
    exit_code = CONTRACT_EXIT if errors else 0
    payload["exit_code"] = exit_code
    _atomic_json(out_path, payload)
    print(f"FACTS: {payload['checks']['facts']['status']}")
    print(f"六件: {payload['checks']['six_files']['status']}")
    print(f"声明: {declaration['status']}")
    if declaration["source"]:
        print(f"声明来源: {declaration['source']}")
    print(f"CSV: {csv_check['status']}")
    print(f"构建: {build['status']}")
    for warning in warnings:
        print(f"警告: {warning}")
    for message in errors:
        print(message, file=sys.stderr)
    print(f"check.json: {out_path}")
    return exit_code


def _accuracy_structure(payload, expected, identity):
    problems = []
    required = ("run_id", "op", "soc", "device", "cases", "summary", "exit_code")
    for key in required:
        if key not in payload:
            problems.append(f"accuracy JSON 缺字段 {key}")
    if problems:
        return None, problems
    for key, value in identity.items():
        if payload.get(key) != value:
            problems.append(
                f"accuracy JSON 的 {key}={payload.get(key)!r}，期望 {value!r}"
            )
    cases = payload.get("cases")
    summary = payload.get("summary")
    if not isinstance(cases, list) or not isinstance(summary, dict):
        problems.append("accuracy JSON 的 cases/summary 类型错误")
        return None, problems
    if payload.get("exit_code") == ENVIRONMENT_EXIT:
        problems.append("accuracy JSON 记录环境失败 exit_code=3")
    if not payload.get("binary_sha256"):
        problems.append("accuracy JSON 的 binary_sha256 为空")
    if not payload.get("csv_sha256"):
        problems.append("accuracy JSON 的 csv_sha256 为空")
    records = {}
    duplicates = []
    for index, item in enumerate(cases):
        if not isinstance(item, dict):
            problems.append(f"accuracy cases[{index}] 不是对象")
            continue
        name = item.get("name")
        status = item.get("status")
        if not isinstance(name, str) or not name:
            problems.append(f"accuracy cases[{index}] 缺有效 name")
            continue
        if status not in ACCURACY_STATUSES:
            problems.append(f"accuracy cases[{index}] 的 status={status!r} 非法")
        if name in records:
            duplicates.append(name)
        records[name] = item
    if duplicates:
        problems.append("accuracy cases 有重复 name：" + ", ".join(sorted(set(duplicates))))
    expected_set = set(expected)
    actual_set = set(records)
    missing = sorted(expected_set - actual_set)
    unknown = sorted(actual_set - expected_set)
    if missing:
        problems.append("accuracy cases 缺期望用例：" + ", ".join(missing))
    if unknown:
        problems.append("accuracy cases 含未知用例：" + ", ".join(unknown))
    if not expected or not cases:
        problems.append("A3 零用例")
    if summary.get("expected") != len(expected):
        problems.append(
            "accuracy summary.expected="
            f"{summary.get('expected')!r}，期望 {len(expected)}"
        )
    return records, problems


def _load_rerun(path):
    if not path.is_file():
        return None, []
    payload = _load_json(path)
    cases = payload.get("cases")
    if not isinstance(cases, list):
        return None, ["rerun JSON 的 cases 不是列表"]
    records = {}
    problems = []
    for index, item in enumerate(cases):
        if not isinstance(item, dict):
            problems.append(f"rerun cases[{index}] 不是对象")
            continue
        name = item.get("name")
        status = item.get("status")
        if not isinstance(name, str) or status not in ACCURACY_STATUSES:
            problems.append(f"rerun cases[{index}] 的 name/status 非法")
            continue
        if name in records:
            problems.append(f"rerun cases 的 name 重复：{name}")
        records[name] = item
    return records, problems


def _attribution(records, rerun):
    result = []
    for name in sorted(records):
        initial = records[name].get("status")
        if initial == "PASS":
            continue
        rerun_status = None if rerun is None else rerun.get(name, {}).get("status")
        if rerun is None:
            attribution = "not_rerun"
        elif rerun_status == "PASS":
            attribution = "flaky"
        elif rerun_status == "FAIL":
            attribution = "reproduced"
        else:
            attribution = "not_reproduced"
        result.append(
            {
                "name": name,
                "initial_status": initial,
                "rerun_status": rerun_status,
                "attribution": attribution,
            }
        )
    return result


def _accuracy_result(payload, expected, identity, rerun_path, deployed_sha):
    records, problems = _accuracy_structure(payload, expected, identity)
    if payload.get("csv_sha256") and payload.get("csv_sha256") != deployed_sha:
        problems.append("accuracy JSON 的 csv_sha256 与部署 CSV 不一致")
    try:
        rerun, rerun_problems = _load_rerun(rerun_path)
    except ValueError as exc:
        rerun = None
        rerun_problems = [str(exc)]
    problems.extend(rerun_problems)
    if records is None:
        records = {}
    counts = {status.lower(): 0 for status in ACCURACY_STATUSES}
    for record in records.values():
        status = record.get("status")
        if status in ACCURACY_STATUSES:
            counts[status.lower()] += 1
    attribution = _attribution(records, rerun)
    if problems:
        status = "证据不足"
    elif counts["pass"] == len(expected):
        status = "精度通过"
    else:
        status = "精度不通过"
    return {
        "status": status,
        "expected": len(expected),
        "executed": len(records),
        "pass": counts["pass"],
        "fail": sum(value for key, value in counts.items() if key != "pass"),
        "counts": counts,
        "attribution": attribution,
        "problems": problems,
    }


def _performance_result(path, expected_count, accuracy_status):
    if accuracy_status == "精度不通过":
        return {
            "status": "未执行(精度未通过)",
            "expected": expected_count,
            "timing_scope": None,
            "scope_caveat": False,
            "reason": "A3 首轮存在非 PASS 用例",
        }
    if accuracy_status != "精度通过":
        return {
            "status": "证据不足",
            "expected": expected_count,
            "timing_scope": None,
            "scope_caveat": False,
            "reason": "精度证据不足，不能进入 A4",
        }
    if expected_count == 0 and not path.is_file():
        return {
            "status": "通过",
            "expected": 0,
            "timing_scope": None,
            "scope_caveat": False,
            "reason": "部署 CSV 没有 TC_PF_ 用例",
        }
    if not path.is_file():
        return {
            "status": "证据不足",
            "expected": expected_count,
            "timing_scope": None,
            "scope_caveat": False,
            "reason": "缺 performance JSON",
        }
    try:
        payload = _load_json(path)
    except ValueError as exc:
        return {
            "status": "证据不足",
            "expected": expected_count,
            "timing_scope": None,
            "scope_caveat": False,
            "reason": str(exc),
        }
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        return {
            "status": "证据不足",
            "expected": expected_count,
            "timing_scope": None,
            "scope_caveat": False,
            "reason": "performance JSON 缺 summary",
        }
    base_status = summary.get("status")
    timing_scope = summary.get("timing_scope")
    scope_caveat = bool(summary.get("scope_caveat")) or timing_scope != "kernel"
    if base_status not in PERFORMANCE_STATUSES:
        base_status = "证据不足"
        reason = "performance summary.status 非法"
    else:
        reason = summary.get("reason")
    display = base_status
    if scope_caveat:
        display += " (scope caveat)"
    return {
        "status": display,
        "base_status": base_status,
        "expected": expected_count,
        "executed": len(payload.get("cases", [])),
        "timing_scope": timing_scope,
        "scope_caveat": scope_caveat,
        "threshold": summary.get("threshold"),
        "reason": reason,
    }


def _contract_summary(out_dir):
    candidates = [Path.cwd() / "check.json", out_dir.parent / "check.json"]
    for path in _unique_files(candidates):
        try:
            payload = _load_json(path)
        except ValueError:
            continue
        return {
            "status": "通过" if payload.get("exit_code") == 0 else "不通过",
            "path": str(path),
            "exit_code": payload.get("exit_code"),
            "declaration": payload.get("checks", {}).get("declaration", {}).get("status"),
            "csv": payload.get("checks", {}).get("csv", {}).get("status"),
            "errors": payload.get("errors", []),
        }
    return {"status": "未提供", "path": None, "errors": []}


def _overall_verdict(accuracy, performance, contract):
    if accuracy["status"] == "证据不足":
        return "证据不足", INSUFFICIENT_EXIT
    if contract.get("status") == "不通过":
        return "不通过", FAILURE_EXIT
    performance_base = performance.get("base_status", performance["status"])
    if performance_base in {"证据不足", "NO_REF"}:
        return "证据不足", INSUFFICIENT_EXIT
    if accuracy["status"] == "精度不通过" or performance_base == "不通过":
        return "不通过", FAILURE_EXIT
    return "通过", 0


def _report_markdown(payload):
    accuracy = payload["accuracy"]
    performance = payload["performance"]
    contract = payload["contract"]
    lines = [
        f"# {payload['op']} 验收报告",
        "",
        "## 结论",
        "",
        f"**{payload['verdict']}**",
        "",
        "## 证据表",
        "",
        "| 维度 | 状态 | 证据 |",
        "| --- | --- | --- |",
        f"| 契约 | {contract['status']} | {contract.get('path') or '未提供 check.json'} |",
        (
            f"| 精度 | {accuracy['status']} | {accuracy['pass']}/"
            f"{accuracy['expected']} PASS |"
        ),
        (
            f"| 性能 | {performance['status']} | "
            f"timing_scope={performance.get('timing_scope')} |"
        ),
        "",
        "## 失败逐条",
        "",
    ]
    if accuracy["attribution"]:
        lines.extend([
            "| case_name | 首轮 | 复跑 | 归因 |",
            "| --- | --- | --- | --- |",
        ])
        for item in accuracy["attribution"]:
            lines.append(
                f"| {item['name']} | {item['initial_status']} | "
                f"{item['rerun_status']} | {item['attribution']} |"
            )
    else:
        lines.append("无首轮失败。")
    if accuracy["problems"]:
        lines.extend(["", "证据问题："])
        lines.extend(f"- {problem}" for problem in accuracy["problems"])
    lines.extend([
        "",
        "## 契约比对",
        "",
        f"- 声明：{contract.get('declaration')}",
        f"- CSV：{contract.get('csv')}",
    ])
    lines.extend(f"- {message}" for message in contract.get("errors", []))
    lines.extend([
        "",
        "## 审阅记录",
        "",
        "<A2′ 记录由 agent 填>",
        "",
    ])
    return "\n".join(lines)


def command_verdict(args):
    package = args.package.resolve()
    repo = args.repo.resolve()
    out_dir = args.out.resolve()
    accuracy_path = package / "results" / f"accuracy_{args.run_id}.json"
    rerun_path = package / "results" / f"accuracy_{args.run_id}-rerun.json"
    performance_path = package / "results" / f"performance_{args.run_id}.json"
    try:
        _, facts, fact_problems = _load_package_facts(package)
        if fact_problems:
            raise ValueError("FACTS 不合法：" + "；".join(fact_problems))
        accuracy_payload = _load_json(accuracy_path)
    except (FileNotFoundError, ImportError, KeyError, ValueError) as exc:
        op = "unknown"
        try:
            op = facts.get("op", "unknown")
        except UnboundLocalError:
            pass
        payload = {
            "run_id": args.run_id,
            "op": op,
            "soc": args.soc,
            "device": args.device,
            "accuracy": {
                "status": "证据不足",
                "expected": 0,
                "executed": 0,
                "pass": 0,
                "fail": 0,
                "attribution": [],
                "problems": [str(exc)],
            },
            "performance": {"status": "证据不足", "reason": "精度证据缺失"},
            "contract": _contract_summary(out_dir),
            "evidence": {"accuracy_json": str(accuracy_path)},
            "verdict": "证据不足",
        }
        _atomic_json(out_dir / "verdict.json", payload)
        _atomic_text(out_dir / "report.md", _report_markdown(payload))
        print(f"证据不足: {exc}", file=sys.stderr)
        print(f"verdict.json: {out_dir / 'verdict.json'}")
        print(f"report.md: {out_dir / 'report.md'}")
        return INSUFFICIENT_EXIT
    op = facts["op"]
    arch = _arch_for_soc(args.soc)
    evidence_problems = []
    deployed = None
    deployed_sha = None
    expected = []
    performance_expected = []
    package_csv = package / f"{op}_test.csv"
    if arch is None:
        evidence_problems.append(f"SoC {args.soc!r} 无 arch 映射")
    else:
        matches = _source_csv_matches(repo, op, arch)
        if len(matches) != 1:
            evidence_problems.append(f"部署 CSV 命中数为 {len(matches)}，期望 1")
        else:
            deployed = matches[0]
            deployed_sha = _sha256(deployed)
            if not package_csv.is_file():
                evidence_problems.append("任务包 CSV 不存在")
            elif _sha256(package_csv) != deployed_sha:
                evidence_problems.append("任务包 CSV 与部署 CSV 的 SHA-256 不一致")
            try:
                expected = _read_csv_case_names(deployed, performance=False)
                performance_expected = _read_csv_case_names(deployed, performance=True)
            except (OSError, UnicodeError, csv.Error, ValueError) as exc:
                evidence_problems.append(str(exc))
    identity = {
        "run_id": args.run_id,
        "op": op,
        "soc": args.soc,
        "device": args.device,
    }
    accuracy = _accuracy_result(
        accuracy_payload,
        expected,
        identity,
        rerun_path,
        deployed_sha,
    )
    accuracy["problems"].extend(evidence_problems)
    if evidence_problems:
        accuracy["status"] = "证据不足"
    performance = _performance_result(
        performance_path,
        len(performance_expected),
        accuracy["status"],
    )
    contract = _contract_summary(out_dir)
    verdict, exit_code = _overall_verdict(accuracy, performance, contract)
    payload = {
        "run_id": args.run_id,
        "op": op,
        "soc": args.soc,
        "device": args.device,
        "accuracy": accuracy,
        "performance": performance,
        "contract": contract,
        "evidence": {
            "accuracy_json": str(accuracy_path),
            "rerun_json": str(rerun_path) if rerun_path.is_file() else None,
            "performance_json": (
                str(performance_path) if performance_path.is_file() else None
            ),
            "deployed_csv": str(deployed) if deployed else None,
            "binary_sha256": accuracy_payload.get("binary_sha256"),
            "csv_sha256": accuracy_payload.get("csv_sha256"),
        },
        "verdict": verdict,
    }
    _atomic_json(out_dir / "verdict.json", payload)
    _atomic_text(out_dir / "report.md", _report_markdown(payload))
    print(f"精度: {accuracy['status']} ({accuracy['pass']}/{accuracy['expected']} PASS)")
    print(f"性能: {performance['status']}")
    print(f"结论: {verdict}")
    print(f"verdict.json: {out_dir / 'verdict.json'}")
    print(f"report.md: {out_dir / 'report.md'}")
    return exit_code


def _add_common(parser):
    parser.add_argument("--repo", required=True, type=Path, help="开发者 ops-blas 工程根目录")
    parser.add_argument("--soc", required=True, help="目标 SoC，例如 ascend910b3")
    parser.add_argument("--device", type=int, default=0, help="编译期测试设备号，默认 0")


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    env_parser = subparsers.add_parser("env", help="检查本机验收环境")
    _add_common(env_parser)
    env_parser.add_argument("--out", type=Path, default=Path.cwd(), help="env.json 输出目录")
    env_parser.set_defaults(function=command_env)
    check_parser = subparsers.add_parser("check", help="检查任务包与工程契约")
    _add_common(check_parser)
    check_parser.add_argument("--package", required=True, type=Path, help="六件任务包目录")
    check_parser.add_argument("--out", type=Path, help="check.json 输出路径")
    check_parser.set_defaults(function=command_check)
    verdict_parser = subparsers.add_parser("verdict", help="从运行证据生成结论")
    _add_common(verdict_parser)
    verdict_parser.add_argument("--package", required=True, type=Path, help="六件任务包目录")
    verdict_parser.add_argument("--run-id", required=True, help="精度与性能结果的运行标识")
    verdict_parser.add_argument("--out", required=True, type=Path, help="结论输出目录")
    verdict_parser.set_defaults(function=command_verdict)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    return args.function(args)


if __name__ == "__main__":
    sys.exit(main())
