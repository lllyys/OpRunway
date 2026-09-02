#!/usr/bin/env python3
"""检查 BLAS 验收环境与契约，并从任务包证据生成最终结论。"""

import argparse
import csv
from datetime import datetime
import hashlib
import importlib.util
import json
import os
import platform
import shlex
from pathlib import Path
import re
import shutil
import subprocess
import sys


ENVIRONMENT_EXIT = 3
CONTRACT_EXIT = 2
INSUFFICIENT_EXIT = 2
FAILURE_EXIT = 1
# 任务包只需要两件：恰好一个 <op>_test.csv（契约用例集）和 gpu_baseline.csv（GPU 基线）。
# 其余文件（gen_csv.py、README、包内 verify 脚本）accept 一概不读，量具由 accept 自己渲染。
RUNTIME_DIR = "runtime"
PERF_THRESHOLD = 0.8
# 由 test/frame 基类读取的列，不要求 harness 的 param.h 显式读取。
BASE_COLUMNS = frozenset({
    "case_name", "description", "expect_result", "random_seed",
    "mere_threshold", "mare_multiplier",
})
INTEGER_RE = re.compile(r"^[+-]?\d+$")
ACCURACY_STATUSES = {
    "PASS",
    "FAIL",
    "SKIP",
    "TIMEOUT",
    "CRASH",
    "MISSING",
}
PERFORMANCE_STATUSES = {"通过", "不通过", "NO_REF", "证据不足"}
PERFORMANCE_CASE_STATUSES = {
    "PASS", "FAIL", "NO_REF", "NO_KERNEL", "CRASH", "TIMEOUT", "MISSING",
}
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


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
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
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
            # 期望集不筛命名前缀：精度集 = 除 TC_PF_ 外全部有效数据行（P0 修正）。
            if name and is_performance == performance:
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


def _package_csv(package):
    """任务包里恰好一个 <op>_test.csv；返回 (op, 路径)。"""
    matches = sorted(Path(package).glob("*_test.csv"))
    if len(matches) != 1:
        raise ValueError(f"任务包须恰好含一个 <op>_test.csv，找到 {len(matches)} 个")
    path = matches[0]
    return path.name[: -len("_test.csv")], path


def _csv_header(path):
    with Path(path).open(encoding="utf-8", newline="") as stream:
        for raw in stream:
            stripped = raw.strip()
            # 与其它读取器同一口径（全局词法规则）：行首含前导空白后为 # 即注释行。
            if stripped and not stripped.startswith("#"):
                return next(csv.reader([raw]))
    raise ValueError(f"{path} 没有表头")


def _read_csv_rows(path):
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
            rows.append(dict(zip(header, values)))
    return header or [], rows


def _family_from_deployed(repo, deployed):
    """test/<family>/<op>/<arch>/<op>_test.csv 取 <family>；test/<op>/<arch>/… 时首段就是它。"""
    parts = deployed.resolve().relative_to((repo / "test").resolve()).parts
    return parts[0]


def _normalize_key_value(value):
    text = str(value).strip()
    return int(text) if INTEGER_RE.fullmatch(text) else text


def _load_baseline(path, csv_header):
    """读 gpu_baseline.csv。键列 = 表头去掉 id/gpu_ms，再去掉 CSV 表头没有或整列为空的列。

    返回 (meta 行, 键列, 原始行, {键: gpu_ms})；gpu_ms 为空或非数值记 None（不可比）。
    """
    meta = []
    data = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            meta.append(stripped)
        else:
            data.append(raw)
    if not data:
        raise ValueError("gpu_baseline.csv 缺表头")
    rows = list(csv.DictReader(data))
    header = next(csv.reader([data[0]]))
    keys = [column for column in header if column not in ("id", "gpu_ms")]
    keys = [
        column for column in keys
        if column in csv_header and any((row.get(column) or "").strip() for row in rows)
    ]
    references = {}
    for row in rows:
        key = tuple(_normalize_key_value(row.get(column, "")) for column in keys)
        raw_ms = (row.get("gpu_ms") or "").strip()
        try:
            references[key] = float(raw_ms) if raw_ms else None
        except ValueError:
            references[key] = None
    return meta, keys, rows, references


def _write_normalized_baseline(path, op, meta, keys, rows):
    """按键列投影后的基线副本，表头严格为 id,<keys>,gpu_ms，供渲染出的性能脚本读取。"""
    with Path(path).open("w", encoding="utf-8", newline="") as stream:
        for line in meta:
            if "=" in line:
                stream.write(line + "\n")
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["id", *keys, "gpu_ms"])
        for index, row in enumerate(rows, 1):
            identifier = (row.get("id") or "").strip() or f"{op}-base-{index:03d}"
            writer.writerow([identifier, *(row.get(k, "") for k in keys), row.get("gpu_ms", "")])


def _comparable_pf_names(csv_path, keys, references):
    """任务包 CSV 里有可比 GPU 基线的 TC_PF_ 用例名（性能期望集），以及没有基线被忽略的名单。"""
    _, rows = _read_csv_rows(csv_path)
    expected = []
    ignored = []
    for row in rows:
        name = row.get("case_name", "")
        if not name.startswith("TC_PF_"):
            continue
        key = tuple(_normalize_key_value(row.get(column, "")) for column in keys)
        if references.get(key) is None:
            ignored.append(name)
        else:
            expected.append(name)
    return expected, ignored


def _harness_sources(harness_dir):
    return sorted(
        path for path in Path(harness_dir).rglob("*")
        if path.suffix in {".h", ".hpp", ".cpp", ".cc"} and path.is_file()
    )


def _column_read_report(csv_header, harness_dir):
    """CSV 每个非基座列名是否在 harness 源码里以字符串字面量出现（没出现 = ReadMap 静默取默认值）。"""
    sources = _harness_sources(harness_dir)
    text = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in sources)
    not_read = [
        column for column in csv_header
        if column not in BASE_COLUMNS and f'"{column}"' not in text
    ]
    return {"sources": [str(path) for path in sources], "columns_not_read": not_read}


def _harness_files(harness_dir, op):
    """A2′ 只看三个文件有无：<op>_param.h、<op>_test.cpp、<op>_npu_wrapper.h。"""
    wanted = {
        "param.h": f"{op}_param.h",
        "test.cpp": f"{op}_test.cpp",
        "npu_wrapper.h": f"{op}_npu_wrapper.h",
    }
    return {
        label: sorted(str(path) for path in Path(harness_dir).rglob(name))
        for label, name in wanted.items()
    }


def _write_runtime(runtime, package, package_csv, op, family, keys, meta, rows, module,
                   calls_per_case, threshold, harness_profile=None):
    """把任意任务包变成运行时包：CSV 副本、规范化基线、渲染出的两个 verify 脚本、manifest。"""
    runtime = Path(runtime)
    runtime.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(package_csv, runtime / package_csv.name)
    baseline = runtime / "gpu_baseline.csv"
    _write_normalized_baseline(baseline, op, meta, keys, rows)
    csv_sha = _sha256(package_csv)
    scripts = module.render_runtime(
        op, family, keys, runtime, csv_sha, threshold,
        harness_profile=harness_profile or "blas",
    )
    manifest = {
        "op": op,
        "family": family,
        "harness_profile": harness_profile,
        "perf_key": keys,
        "threshold": threshold,
        "calls_per_case": calls_per_case,
        "package": str(package),
        "package_csv": package_csv.name,
        "package_csv_sha256": csv_sha,
        "baseline_sha256": _sha256(package / "gpu_baseline.csv"),
        "normalized_baseline_sha256": _sha256(baseline),
        "scripts": {path.name: _sha256(path) for path in scripts},
        "generated_at": _timestamp(),
    }
    _atomic_json(runtime / "manifest.json", manifest)
    return manifest


def _evidence_id(payload):
    identity = {
        key: payload.get(key)
        for key in (
            "command", "package", "repo", "op", "family", "soc", "device",
            "package_csv_sha256", "baseline_sha256", "calls_per_case",
        )
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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


def _detect_profile(repo, registry):
    """A1 域探测：按 registry 各 profile 的 entry_headers 在 <repo>/include 下探测。

    返回 (hits, probed)：hits 是 {profile键: 命中头文件路径}；probed 是
    {profile键: 探测过的头文件名列表}。恰一命中才可选定，0/多命中由调用方硬失败。"""
    hits = {}
    probed = {}
    for key in sorted(registry):
        headers = registry[key]["entry_headers"]
        probed[key] = list(headers)
        for name in headers:
            candidate = Path(repo) / "include" / name
            if candidate.is_file():
                hits[key] = str(candidate)
                break
    return hits, probed


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
        hard=True,
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
    # A1 入口头：registry 驱动的域探测（影子期已核对与旧硬编码判断一致）。
    try:
        registry = _load_case_gen()._harness_registry()
        hits, probed = _detect_profile(repo, registry)
    except (FileNotFoundError, ImportError) as exc:
        hits, probed = {}, {}
        record("harness_profile 探测", "缺失", f"registry 不可达：{exc}", hard=True)
    else:
        if len(hits) == 1:
            key = next(iter(hits))
            record("harness_profile 探测", "OK", f"{key}（{hits[key]}）")
        elif not hits:
            probed_text = "; ".join(
                f"{key}: {', '.join(names)}" for key, names in probed.items()
            )
            record(
                "harness_profile 探测", "缺失",
                f"0 命中，探测过 {probed_text}", hard=True,
            )
        else:
            record(
                "harness_profile 探测", "多命中",
                "、".join(sorted(hits)), hard=True,
            )
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
        # 有的仓（如 ops-sparse）构建不产 built_tests.list；清单缺失只提示，
        # 二进制有无由量具寻址器裁决。
        return {
            "status": "未编译",
            "hard": False,
            "detail": "build/test 无 built_tests.list（未编译，或该仓不产清单；以量具寻址为准）",
        }
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
    """A2：只以文件有无裁决；内容差异记 warnings，A5 据此判证据不足。同时生成运行时包。"""
    package = args.package.resolve()
    repo = args.repo.resolve()
    out_path = args.out.resolve() if args.out else Path.cwd() / "check.json"
    runtime = out_path.parent / RUNTIME_DIR
    errors = []
    warnings = []
    payload = {
        "command": "check",
        "package": str(package),
        "repo": str(repo),
        "soc": args.soc,
        "device": args.device,
        "calls_per_case": args.calls_per_case,
        "checks": {},
        "errors": errors,
        "warnings": warnings,
        "exit_code": None,
        "generated_at": _timestamp(),
    }

    def finish(code):
        payload["exit_code"] = code
        payload["evidence_id"] = _evidence_id(payload)
        _atomic_json(out_path, payload)
        for message in warnings:
            print(f"警告: {message}")
        for message in errors:
            print(message, file=sys.stderr)
        print(f"check.json: {out_path}")
        return code

    # 包：恰好一个 <op>_test.csv，加 gpu_baseline.csv
    try:
        op, package_csv = _package_csv(package)
    except ValueError as exc:
        errors.append(f"PACKAGE_INVALID: {exc}")
        return finish(CONTRACT_EXIT)
    baseline_path = package / "gpu_baseline.csv"
    if not baseline_path.is_file():
        errors.append("PACKAGE_INVALID: 任务包缺 gpu_baseline.csv")
        return finish(CONTRACT_EXIT)
    payload["op"] = op
    payload["package_csv_sha256"] = _sha256(package_csv)
    payload["baseline_sha256"] = _sha256(baseline_path)
    payload["checks"]["package"] = {"csv": str(package_csv), "baseline": str(baseline_path)}

    # 量具：同插件 case-gen 的模板
    try:
        module = _load_case_gen()
    except (FileNotFoundError, ImportError) as exc:
        errors.append(f"CASE_GEN_NOT_FOUND: {exc}")
        return finish(ENVIRONMENT_EXIT)

    # A1 域探测：恰一命中把 profile 键名记入 runtime manifest（探测只记录不裁决）。
    hits, probed = _detect_profile(repo, module._harness_registry())
    if len(hits) != 1:
        if not hits:
            probed_text = "; ".join(
                f"{key}: {', '.join(names)}" for key, names in probed.items()
            )
            errors.append(f"PROFILE_NOT_DETECTED: 0 命中，探测过 {probed_text}")
        else:
            errors.append("PROFILE_AMBIGUOUS: 多命中 " + "、".join(sorted(hits)))
        return finish(ENVIRONMENT_EXIT)
    harness_profile = next(iter(hits))
    payload["harness_profile"] = harness_profile
    payload["checks"]["profile"] = {"selected": harness_profile, "hits": hits}

    # 工程：部署 CSV 按三条规则恰好命中一份
    arch = _arch_for_soc(args.soc)
    if arch is None:
        errors.append(f"CSV_NOT_DEPLOYED: SoC {args.soc!r} 无 arch 映射")
        return finish(CONTRACT_EXIT)
    matches = _source_csv_matches(repo, op, arch)
    if len(matches) != 1:
        status = "CSV_NOT_DEPLOYED" if not matches else "CSV_AMBIGUOUS"
        payload["checks"]["csv"] = {
            "status": status, "arch": arch, "matches": [str(path) for path in matches],
        }
        errors.append(f"{status}: 三条源码目录规则命中 {len(matches)} 份部署 CSV")
        return finish(CONTRACT_EXIT)
    deployed = matches[0]
    payload["deployed_csv_sha256"] = _sha256(deployed)
    family = _family_from_deployed(repo, deployed)
    payload["family"] = family
    csv_status = "OK"
    payload["checks"]["csv"] = {
        "status": csv_status, "arch": arch, "deployed": str(deployed),
        "deployed_sha256": payload["deployed_csv_sha256"],
    }

    # 构建清单：有清单才核，未编译只提示
    build = _build_state(repo, op)
    payload["checks"]["build"] = build
    if build["hard"]:
        errors.append(f"{build['status']}: {build['detail']}")
    elif build["status"] == "未编译":
        warnings.append("未编译：A3 将调用 build.sh 生成构建清单")

    # harness：三个文件有无（A2′）+ CSV 列名是否被源码读取
    harness_dir = deployed.parent.parent
    csv_header = _csv_header(package_csv)
    files = _harness_files(harness_dir, op)
    missing = [label for label, paths in files.items() if not paths]
    payload["checks"]["harness"] = {"dir": str(harness_dir), "files": files, "missing": missing}
    if missing:
        warnings.append("HARNESS_FILE_MISSING: " + ", ".join(missing))
    columns = _column_read_report(csv_header, harness_dir)
    payload["checks"]["columns"] = columns
    if columns["columns_not_read"]:
        warnings.append("COLUMN_NOT_READ: " + ", ".join(columns["columns_not_read"]))

    # 基线与性能期望集
    try:
        meta, keys, rows, references = _load_baseline(baseline_path, csv_header)
    except ValueError as exc:
        errors.append(f"BASELINE_INVALID: {exc}")
        return finish(CONTRACT_EXIT)
    expected_pf, ignored_pf = _comparable_pf_names(package_csv, keys, references)
    payload["checks"]["perf"] = {
        "key": keys,
        "comparable_pf": len(expected_pf),
        "ignored_pf": len(ignored_pf),
        "baseline_rows": len(rows),
    }
    if ignored_pf:
        warnings.append(f"NO_REF: {len(ignored_pf)} 条 TC_PF_ 无可比基线，不进入性能期望集")
    if errors:
        return finish(CONTRACT_EXIT)

    # 运行时包：CSV 副本 + 规范化基线 + 渲染的两个 verify 脚本 + manifest
    payload["runtime"] = _write_runtime(
        runtime, package, package_csv, op, family, keys, meta, rows, module,
        args.calls_per_case, PERF_THRESHOLD, harness_profile,
    )
    print(f"包: {op}（family={family}）")
    print(f"CSV: {csv_status}")
    print(f"构建: {build['status']}")
    print(f"harness 缺件: {', '.join(missing) or '无'}")
    print(f"列名未读取: {', '.join(columns['columns_not_read']) or '无'}")
    print(f"性能期望集: {len(expected_pf)} 条（忽略 {len(ignored_pf)} 条无基线）")
    print(f"runtime: {runtime}")
    return finish(0)


def _accuracy_structure(payload, expected, identity):
    problems = []
    if not isinstance(payload, dict):
        return None, ["accuracy JSON 顶层不是对象"]
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
    counts = {
        status.lower(): sum(item.get("status") == status for item in records.values())
        for status in ACCURACY_STATUSES
    }
    for name, value in counts.items():
        if summary.get(name) != value:
            problems.append(
                f"accuracy summary.{name}={summary.get(name)!r}，按 cases 应为 {value}"
            )
    expected_exit = 0 if counts["pass"] == len(expected) else FAILURE_EXIT
    if payload.get("exit_code") != expected_exit:
        problems.append(
            f"accuracy exit_code={payload.get('exit_code')!r}，按 cases 应为 {expected_exit}"
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


def _performance_result(
    path,
    expected,
    accuracy_status,
    identity,
    deployed_sha,
    accuracy_binary_sha,
    total_pf=None,
):
    expected_count = len(expected)

    def _with_pf_evidence(result):
        # 证据先行：total_pf（CSV 的 TC_PF_ 行数）与 comparable_pf（可比集）分开记，
        # 「有没有性能要求」只能看 total_pf，不能拿可比集大小当代理。
        result.setdefault("total_pf", total_pf)
        result.setdefault("comparable_pf", expected_count)
        return result
    if accuracy_status == "精度不通过":
        return _with_pf_evidence({
            "status": "未执行(精度未通过)",
            "expected": expected_count,
            "timing_scope": None,
            "scope_caveat": False,
            "reason": "A3 首轮存在非 PASS 用例",
        })
    if accuracy_status != "精度通过":
        return _with_pf_evidence({
            "status": "证据不足",
            "expected": expected_count,
            "timing_scope": None,
            "scope_caveat": False,
            "reason": "精度证据不足，不能进入 A4",
        })
    # 「有没有性能要求」的判据是 CSV 的 TC_PF_ 行数（total_pf），
    # 不是可比集大小——新包 200 条 PF 而基线全空时可比集也是 0，
    # 拿可比集当代理会把 NO_REF 误判成「没有性能要求」。
    # 该判定不受性能 JSON 是否存在影响：无性能要求时杂散 JSON 只记录不改结论。
    if (total_pf or 0) == 0:
        reason = "部署 CSV 没有 TC_PF_ 用例"
        if path.is_file():
            reason += "；存在未预期的 performance JSON，已忽略（不参与裁决）"
        return _with_pf_evidence({
            "status": "通过",
            "base_status": "通过",
            "expected": 0,
            "timing_scope": None,
            "scope_caveat": False,
            "reason": reason,
        })
    if not path.is_file():
        if expected_count == 0:
            return _with_pf_evidence({
                "status": "NO_REF",
                "base_status": "NO_REF",
                "expected": 0,
                "timing_scope": None,
                "scope_caveat": False,
                "reason": f"{total_pf} 条 TC_PF_ 全无可比基线，证据不足以判 PASS/FAIL",
            })
        return _with_pf_evidence({
            "status": "证据不足",
            "expected": expected_count,
            "timing_scope": None,
            "scope_caveat": False,
            "reason": "缺 performance JSON",
        })
    try:
        payload = _load_json(path)
    except ValueError as exc:
        return _with_pf_evidence({
            "status": "证据不足",
            "expected": expected_count,
            "timing_scope": None,
            "scope_caveat": False,
            "reason": str(exc),
        })
    problems = []
    if not isinstance(payload, dict):
        problems.append("performance JSON 顶层不是对象")
        payload = {}
    for key, value in identity.items():
        if payload.get(key) != value:
            problems.append(
                f"performance JSON 的 {key}={payload.get(key)!r}，期望 {value!r}"
            )
    summary = payload.get("summary")
    cases = payload.get("cases")
    if not isinstance(summary, dict):
        problems.append("performance JSON 的 summary 不是对象")
        summary = {}
    if not isinstance(cases, list):
        problems.append("performance JSON 的 cases 不是列表")
        cases = []
    if not payload.get("binary_sha256"):
        problems.append("performance JSON 的 binary_sha256 为空")
    elif payload.get("binary_sha256") != accuracy_binary_sha:
        problems.append("A3/A4 binary_sha256 不一致")
    if not payload.get("csv_sha256"):
        problems.append("performance JSON 的 csv_sha256 为空")
    elif payload.get("csv_sha256") != deployed_sha:
        problems.append("performance JSON 的 csv_sha256 与部署 CSV 不一致")
    records = {}
    counts = {status: 0 for status in PERFORMANCE_CASE_STATUSES}
    for index, item in enumerate(cases):
        if not isinstance(item, dict):
            problems.append(f"performance cases[{index}] 不是对象")
            continue
        name = item.get("name")
        status = item.get("status")
        if not isinstance(name, str) or not name:
            problems.append(f"performance cases[{index}] 缺有效 name")
            continue
        if name in records:
            problems.append(f"performance cases 的 name 重复：{name}")
        records[name] = item
        if status not in PERFORMANCE_CASE_STATUSES:
            problems.append(f"performance cases[{index}] 的 status={status!r} 非法")
        else:
            counts[status] += 1
    missing = sorted(set(expected) - set(records))
    unknown = sorted(set(records) - set(expected))
    if missing:
        problems.append("performance cases 缺期望用例：" + ", ".join(missing))
    if unknown:
        problems.append("performance cases 含未知用例：" + ", ".join(unknown))
    if expected and not cases:
        problems.append("A4 零用例")
    summary_keys = {
        "pass": "PASS",
        "fail": "FAIL",
        "no_ref": "NO_REF",
        "no_kernel": "NO_KERNEL",
        "crash": "CRASH",
        "timeout": "TIMEOUT",
        "missing": "MISSING",
    }
    if summary.get("expected") != expected_count:
        problems.append(
            f"performance summary.expected={summary.get('expected')!r}，"
            f"期望 {expected_count}"
        )
    for key, status in summary_keys.items():
        if summary.get(key) != counts[status]:
            problems.append(
                f"performance summary.{key}={summary.get(key)!r}，"
                f"按 cases 应为 {counts[status]}"
            )
    insufficient = sum(counts[name] for name in ("NO_KERNEL", "CRASH", "TIMEOUT", "MISSING"))
    if insufficient:
        computed_status, expected_exit = "证据不足", INSUFFICIENT_EXIT
    elif counts["FAIL"]:
        computed_status, expected_exit = "不通过", FAILURE_EXIT
    elif not (counts["PASS"] or counts["FAIL"]):
        computed_status, expected_exit = "NO_REF", 0
    else:
        computed_status, expected_exit = "通过", 0
    base_status = summary.get("status")
    timing_scope = summary.get("timing_scope")
    scope_caveat = bool(summary.get("scope_caveat")) or timing_scope != "kernel"
    if base_status not in PERFORMANCE_STATUSES:
        problems.append("performance summary.status 非法")
    elif base_status != computed_status:
        problems.append(
            f"performance summary.status={base_status!r}，按 cases 应为 {computed_status!r}"
        )
    if payload.get("exit_code") != expected_exit:
        problems.append(
            f"performance exit_code={payload.get('exit_code')!r}，按 cases 应为 {expected_exit}"
        )
    if problems:
        base_status = "证据不足"
    reason = "；".join(problems) or summary.get("reason")
    display = base_status
    if scope_caveat:
        display += " (scope caveat)"
    return _with_pf_evidence({
        "status": display,
        "base_status": base_status,
        "expected": expected_count,
        "executed": len(records),
        "timing_scope": timing_scope,
        "scope_caveat": scope_caveat,
        "threshold": summary.get("threshold"),
        "reason": reason,
        "problems": problems,
    })


def _contract_summary(out_dir, expected):
    candidates = [Path.cwd() / "check.json", out_dir.parent / "check.json"]
    matches = _unique_files(candidates)
    if not matches:
        return {
            "status": "证据不足",
            "path": None,
            "errors": ["缺 check.json"],
        }
    if len(matches) > 1:
        return {
            "status": "证据不足",
            "path": None,
            "errors": ["找到多份 check.json，无法确定 A2 证据"],
        }
    path = matches[0]
    try:
        payload = _load_json(path)
    except ValueError as exc:
        return {"status": "证据不足", "path": str(path), "errors": [str(exc)]}
    problems = []
    if not isinstance(payload, dict):
        problems.append("check.json 顶层不是对象")
        payload = {}
    for key, value in expected.items():
        if payload.get(key) != value:
            problems.append(f"check.json 的 {key}={payload.get(key)!r}，期望 {value!r}")
    checks = payload.get("checks")
    if not isinstance(checks, dict):
        problems.append("check.json 的 checks 不是对象")
        checks = {}
    if payload.get("command") != "check":
        problems.append("check.json 的 command 不是 check")
    if payload.get("evidence_id") != _evidence_id(payload):
        problems.append("check.json 的 evidence_id 与内容不一致")
    columns = checks.get("columns") if isinstance(checks.get("columns"), dict) else {}
    if problems:
        status = "证据不足"
    else:
        status = "通过" if payload.get("exit_code") == 0 else "不通过"
    csv_check = checks.get("csv")
    harness = checks.get("harness") if isinstance(checks.get("harness"), dict) else {}
    return {
        "status": status,
        "path": str(path),
        "exit_code": payload.get("exit_code"),
        "evidence_id": payload.get("evidence_id"),
        "csv": csv_check.get("status") if isinstance(csv_check, dict) else None,
        "columns_not_read": columns.get("columns_not_read") or [],
        "harness_missing": harness.get("missing", []),
        "warnings": payload.get("warnings", []) if isinstance(payload.get("warnings"), list) else [],
        "errors": problems + (
            payload.get("errors", []) if isinstance(payload.get("errors"), list) else []
        ),
    }


def _overall_verdict(accuracy, performance, contract):
    if accuracy["status"] == "证据不足" or contract.get("status") == "证据不足":
        return "证据不足", INSUFFICIENT_EXIT
    if contract.get("status") == "不通过":
        return "不通过", FAILURE_EXIT
    performance_base = performance.get("base_status", performance["status"])
    if performance_base in {"证据不足", "NO_REF"}:
        return "证据不足", INSUFFICIENT_EXIT
    if accuracy["status"] == "精度不通过" or performance_base == "不通过":
        return "不通过", FAILURE_EXIT
    return "通过", 0


REPORT_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "assets" / "template" / "report.md"


def _accuracy_section(accuracy):
    lines = [
        f"- 状态：**{accuracy['status']}**"
        f"（{accuracy.get('pass')}/{accuracy.get('expected')} PASS）",
        f"- 执行：{accuracy.get('executed')} 条",
    ]
    if accuracy.get("attribution"):
        lines += ["", "| case_name | 首轮 | 复跑 | 归因 |", "| --- | --- | --- | --- |"]
        lines += [
            f"| {item['name']} | {item['initial_status']} | "
            f"{item['rerun_status']} | {item['attribution']} |"
            for item in accuracy["attribution"]
        ]
    else:
        lines.append("- 无首轮失败。")
    if accuracy.get("problems"):
        lines += ["", "证据问题："] + [f"- {x}" for x in accuracy["problems"]]
    return "\n".join(lines)


def _performance_section(performance):
    lines = [
        f"- 状态：**{performance['status']}**",
        f"- TC_PF_ 行数（total_pf）：{performance.get('total_pf')}",
        f"- 可比集（comparable_pf）：{performance.get('comparable_pf')}",
        f"- timing_scope：{performance.get('timing_scope')}",
    ]
    if performance.get("reason"):
        lines.append(f"- 说明：{performance['reason']}")
    if performance.get("problems"):
        lines += ["", "证据问题："] + [f"- {x}" for x in performance["problems"]]
    return "\n".join(lines)


def _write_layout(out_dir, payload, package, runtime, accuracy_path, rerun_path,
                  performance_path, args=None):
    """三类产物最小布局：report/（人读）、intermediate/（执行期 JSON）、repro/（最小可复现）。"""
    report_dir = out_dir / "report"
    inter_dir = out_dir / "intermediate"
    repro_dir = out_dir / "repro"
    for directory in (report_dir, inter_dir, repro_dir):
        directory.mkdir(parents=True, exist_ok=True)
    template = REPORT_TEMPLATE_PATH.read_text(encoding="utf-8")
    contract = payload.get("contract") or {}
    summary_lines = []
    if payload.get("verdict") != "通过":
        reasons = []
        if (payload.get("accuracy") or {}).get("status") not in (None, "精度通过"):
            reasons.append(f"精度 {payload['accuracy']['status']}")
        perf_base = (payload.get("performance") or {}).get("base_status") or (
            payload.get("performance") or {}
        ).get("status")
        if perf_base not in (None, "通过"):
            reasons.append(f"性能 {perf_base}")
        if contract.get("status") not in (None, "通过"):
            reasons.append(f"契约 {contract.get('status')}")
        summary_lines.append("- 结论原因：" + ("；".join(reasons) or "见证据问题"))
    summary_lines.append(f"- 契约（check.json）：{contract.get('status')}")
    for message in contract.get("warnings") or []:
        summary_lines.append(f"- 警告：{message}")
    for message in contract.get("errors") or []:
        summary_lines.append(f"- 契约错误：{message}")
    runtime_info = payload.get("runtime") or {}
    summary_lines.append(
        "- 运行时身份：op="
        f"{runtime_info.get('op')}，profile={runtime_info.get('harness_profile')}，"
        f"包 CSV {str(runtime_info.get('package_csv_sha256'))[:12]}…，"
        f"calls_per_case={runtime_info.get('calls_per_case')}"
    )
    values = {
        "OP": payload.get("op"),
        "VERDICT": payload.get("verdict"),
        "RUN_ID": payload.get("run_id"),
        "SOC": payload.get("soc"),
        "SUMMARY": "\n".join(summary_lines),
        "ACCURACY_SECTION": _accuracy_section(payload["accuracy"]),
        "PERFORMANCE_SECTION": _performance_section(payload["performance"]),
    }
    text = template
    for key, value in values.items():
        text = text.replace(f"@@{key}@@", str(value))
    _atomic_text(report_dir / "report.md", text)
    _atomic_json(inter_dir / "verdict.json", payload)
    for source in (accuracy_path, rerun_path, performance_path,
                   runtime / "manifest.json", Path.cwd() / "check.json",
                   out_dir.parent / "check.json"):
        if source and Path(source).is_file():
            shutil.copyfile(source, inter_dir / Path(source).name)
    # repro：六件副本（存在即拷）+ 用例清单含失败标注
    six = [
        "gen_csv.py", f"{payload['op']}_test.csv", "verify_accuracy.py",
        "verify_performance.py", "README.md", "gpu_baseline.csv",
    ]
    for name in six:
        source = Path(package) / name
        if source.is_file():
            shutil.copyfile(source, repro_dir / name)
    lines = ["case_name,block,status,executed"]
    accuracy = payload["accuracy"]
    performance = payload["performance"]
    accuracy_executed = accuracy.get("executed") or 0
    failed = {item["name"]: item for item in accuracy.get("attribution", [])}
    for record in accuracy.get("case_names", []):
        status = failed.get(record, {}).get("initial_status", "PASS")
        lines.append(f"{record},accuracy,{status},{'yes' if accuracy_executed else 'no'}")
    perf_sets = performance.get("case_sets") or {}
    perf_executed = performance.get("executed")
    for name in perf_sets.get("comparable", []):
        lines.append(
            f"{name},perf_comparable,{performance.get('base_status') or performance.get('status')},"
            f"{'yes' if perf_executed else 'no'}"
        )
    for name in perf_sets.get("no_ref", []):
        lines.append(f"{name},perf_no_ref,NO_REF,no")
    _atomic_text(repro_dir / "cases.csv", "\n".join(lines) + "\n")
    if args is not None:
        manifest_cpc = (payload.get("runtime") or {}).get("calls_per_case")
        run_id = payload.get("run_id")
        script = "\n".join([
            "#!/bin/sh",
            "# 由 accept verdict 生成：同参复跑本轮验收链。",
            "# 在原工作目录执行；换新 run-id 复跑时把 RUN_ID 改掉即可。",
            "set -e",
            f"WORKDIR={shlex.quote(str(Path.cwd()))}",
            f"ACCEPT={shlex.quote(str(Path(__file__).resolve()))}",
            f"RUN_ID={shlex.quote(str(run_id))}-rerun",
            'cd "$WORKDIR"',
            (
                f"python3 \"$ACCEPT\" check --package {shlex.quote(str(package))}"
                f" --repo {shlex.quote(str(args.repo))} --soc {shlex.quote(args.soc)}"
                f" --device {args.device} --calls-per-case {manifest_cpc}"
            ),
            'cd "$WORKDIR/runtime"',
            (
                f"python3 verify_accuracy.py --repo {shlex.quote(str(args.repo))}"
                f" --soc {shlex.quote(args.soc)} --device {args.device}"
                " --run-id \"$RUN_ID\""
            ),
            (
                f"python3 verify_performance.py --repo {shlex.quote(str(args.repo))}"
                f" --soc {shlex.quote(args.soc)} --device {args.device}"
                f" --run-id \"$RUN_ID\" --skip-build --calls-per-case {manifest_cpc}"
            ),
            'cd "$WORKDIR"',
            (
                f"python3 \"$ACCEPT\" verdict --package {shlex.quote(str(package))}"
                f" --repo {shlex.quote(str(args.repo))} --soc {shlex.quote(args.soc)}"
                f" --device {args.device} --run-id \"$RUN_ID\""
                f" --out {shlex.quote(str(out_dir))}-rerun"
            ),
            "",
        ])
        rerun_sh = repro_dir / "rerun.sh"
        _atomic_text(rerun_sh, script)
        rerun_sh.chmod(0o755)
        uname = platform.uname()
        fingerprint = {
            "generated_at": _timestamp(),
            "platform": {
                "system": uname.system, "release": uname.release,
                "machine": uname.machine, "node": uname.node,
            },
            "python": sys.version.split()[0],
            "ascend_env": {
                key: os.environ.get(key)
                for key in ("ASCEND_HOME", "ASCEND_TOOLKIT_HOME", "ASCEND_OPP_PATH")
            },
            "identity": {
                "run_id": run_id,
                "op": payload.get("op"),
                "soc": payload.get("soc"),
                "device": payload.get("device"),
                "binary_sha256": (payload.get("evidence") or {}).get("binary_sha256"),
                "csv_sha256": (payload.get("evidence") or {}).get("csv_sha256"),
                "calls_per_case": manifest_cpc,
            },
        }
        _atomic_json(repro_dir / "environment.json", fingerprint)
    return {"report": str(report_dir / "report.md"),
            "intermediate": str(inter_dir), "repro": str(repro_dir)}


def command_verdict(args):
    """A5：从 <工作目录>/runtime 的 manifest 与 results 出结论；期望集来自任务包 CSV 副本。"""
    if not RUN_ID_RE.fullmatch(args.run_id):
        print("证据不足: run-id 格式不合法", file=sys.stderr)
        return INSUFFICIENT_EXIT
    package = args.package.resolve()
    repo = args.repo.resolve()
    out_dir = args.out.resolve()
    runtime = Path.cwd() / RUNTIME_DIR
    results = runtime / "results"
    accuracy_path = results / f"accuracy_{args.run_id}.json"
    rerun_path = results / f"accuracy_{args.run_id}-rerun.json"
    performance_path = results / f"performance_{args.run_id}.json"
    manifest = {}
    try:
        manifest = _load_json(runtime / "manifest.json")
        accuracy_payload = _load_json(accuracy_path)
    except ValueError as exc:
        payload = {
            "run_id": args.run_id,
            "op": manifest.get("op", "unknown"),
            "soc": args.soc,
            "device": args.device,
            "runtime": manifest,
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
            "contract": {
                "status": "证据不足",
                "path": None,
                "errors": ["runtime/manifest.json 或精度 JSON 缺失，无法绑定 check.json"],
            },
            "evidence": {"accuracy_json": str(accuracy_path)},
            "verdict": "证据不足",
        }
        layout = _write_layout(
            out_dir, payload, package, runtime, accuracy_path, rerun_path,
            performance_path, args=args,
        )
        payload["layout"] = layout
        _atomic_json(out_dir / "intermediate" / "verdict.json", payload)
        print(f"证据不足: {exc}", file=sys.stderr)
        print(f"verdict.json: {out_dir / 'intermediate' / 'verdict.json'}")
        print(f"report.md: {out_dir / 'report' / 'report.md'}")
        return INSUFFICIENT_EXIT
    op = manifest["op"]
    family = manifest["family"]
    runtime_csv = runtime / manifest["package_csv"]
    evidence_problems = []
    expected = []
    performance_expected = []
    total_pf = None
    performance_ignored = []
    try:
        expected = _read_csv_case_names(runtime_csv, performance=False)
        total_pf = len(_read_csv_case_names(runtime_csv, performance=True))
        csv_header = _csv_header(runtime_csv)
        _, keys, _, references = _load_baseline(runtime / "gpu_baseline.csv", csv_header)
        performance_expected, performance_ignored = _comparable_pf_names(
            runtime_csv, keys, references
        )
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        evidence_problems.append(str(exc))
    deployed_sha = accuracy_payload.get("csv_sha256")
    identity = {
        "run_id": args.run_id,
        "op": op,
        "family": family,
        "soc": args.soc,
        "device": args.device,
        "repo": str(repo),
    }
    accuracy = _accuracy_result(
        accuracy_payload,
        expected,
        identity,
        rerun_path,
        deployed_sha,
    )
    accuracy["case_names"] = list(expected)
    accuracy["problems"].extend(evidence_problems)
    performance_sets = {
        "comparable": list(performance_expected),
        "no_ref": list(performance_ignored),
    }
    if evidence_problems:
        accuracy["status"] = "证据不足"
    performance = _performance_result(
        performance_path,
        performance_expected,
        accuracy["status"],
        # calls_per_case 以 manifest 为单一来源：性能证据里的值必须与之相等。
        {**identity, "calls_per_case": manifest.get("calls_per_case")},
        deployed_sha,
        accuracy_payload.get("binary_sha256"),
        total_pf=total_pf,
    )
    performance["case_sets"] = performance_sets
    contract_expected = {
        "package": str(package),
        "repo": str(repo),
        "op": op,
        "family": family,
        "soc": args.soc,
        "device": args.device,
        "package_csv_sha256": manifest.get("package_csv_sha256"),
        "baseline_sha256": manifest.get("baseline_sha256"),
        "calls_per_case": manifest.get("calls_per_case"),
    }
    contract = _contract_summary(out_dir, contract_expected)
    verdict, exit_code = _overall_verdict(accuracy, performance, contract)
    calls_per_case = None
    if performance_path.is_file():
        try:
            calls_per_case = _load_json(performance_path).get("calls_per_case")
        except ValueError:
            calls_per_case = None
    payload = {
        "run_id": args.run_id,
        "op": op,
        "soc": args.soc,
        "device": args.device,
        "runtime": manifest,
        "accuracy": accuracy,
        "performance": performance,
        "contract": contract,
        "evidence": {
            "accuracy_json": str(accuracy_path),
            "rerun_json": str(rerun_path) if rerun_path.is_file() else None,
            "performance_json": (
                str(performance_path) if performance_path.is_file() else None
            ),
            "deployed_csv": accuracy_payload.get("csv_path"),
            "binary_sha256": accuracy_payload.get("binary_sha256"),
            "csv_sha256": accuracy_payload.get("csv_sha256"),
            "calls_per_case": calls_per_case,
        },
        "verdict": verdict,
    }
    layout = _write_layout(
        out_dir, payload, package, runtime, accuracy_path, rerun_path,
        performance_path, args=args,
    )
    payload["layout"] = layout
    _atomic_json(out_dir / "intermediate" / "verdict.json", payload)
    print(f"精度: {accuracy['status']} ({accuracy['pass']}/{accuracy['expected']} PASS)")
    print(f"性能: {performance['status']}")
    print(f"结论: {verdict}")
    print(f"verdict.json: {out_dir / 'intermediate' / 'verdict.json'}")
    print(f"report.md: {out_dir / 'report' / 'report.md'}")
    return exit_code


def _add_common(parser):
    parser.add_argument(
        "--repo", required=True, type=Path,
        help="开发者算子工程根目录（域由 include 入口头按 registry 探测）",
    )
    parser.add_argument("--soc", required=True, help="目标 SoC，例如 ascend910b3")
    parser.add_argument("--device", type=int, default=0, help="编译期测试设备号，默认 0")


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    env_parser = subparsers.add_parser("env", help="检查本机验收环境")
    _add_common(env_parser)
    env_parser.add_argument("--out", type=Path, default=Path.cwd(), help="env.json 输出目录")
    env_parser.set_defaults(function=command_env)
    check_parser = subparsers.add_parser("check", help="检查任务包与工程契约，生成运行时包")
    _add_common(check_parser)
    check_parser.add_argument(
        "--package", required=True, type=Path,
        help="任务包目录：含恰好一个 <op>_test.csv 与 gpu_baseline.csv",
    )
    check_parser.add_argument(
        "--calls-per-case", type=int, default=1,
        help="harness 一条 gtest 用例调用被测接口的次数（固定 warm-up 一次则为 2）",
    )
    check_parser.add_argument("--out", type=Path, help="check.json 输出路径，默认当前目录")
    check_parser.set_defaults(function=command_check)
    verdict_parser = subparsers.add_parser("verdict", help="从运行证据生成结论")
    _add_common(verdict_parser)
    verdict_parser.add_argument("--package", required=True, type=Path, help="任务包目录")
    verdict_parser.add_argument("--run-id", required=True, help="精度与性能结果的运行标识")
    verdict_parser.add_argument("--out", required=True, type=Path, help="结论输出目录")
    verdict_parser.set_defaults(function=command_verdict)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    return args.function(args)


if __name__ == "__main__":
    sys.exit(main())
