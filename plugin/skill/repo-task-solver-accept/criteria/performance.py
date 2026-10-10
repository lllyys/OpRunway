"""Deterministic performance judgment for the October Cholesky task contract."""
import hashlib
import csv
import json
import math
from pathlib import Path

SCHEMA = "solver-performance-v1"
SAMPLES = 30
GPU_FACTOR = 0.35
IDENTITY = ("case_id", "op", "n", "nrhs", "batch", "uplo", "lda", "ldb", "seed", "dtype")
CHOL_OPS = {p + s for p in ("s", "c") for s in
            ("potrf", "potrs", "potri", "potrfBatched", "potrsBatched")}


def positive(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)


def identity(case):
    return {key: case[key] for key in IDENTITY if key in case}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def unique(rows, label):
    if not isinstance(rows, list):
        raise ValueError(label + " must be a list")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("case_id"), str):
            raise ValueError(label + " has an invalid case_id")
        cid = row["case_id"]
        if not cid or cid in result:
            raise ValueError(label + " has an empty or duplicate case_id: " + cid)
        result[cid] = row
    return result


def load_contract(package):
    """Performance does not need precision arrays or CPU residual references."""
    package = Path(package)
    manifest = json.loads((package / "manifest.json").read_text())
    if not isinstance(manifest, dict) or not isinstance(manifest.get("fingerprint"), dict):
        raise ValueError("manifest/fingerprint must be objects")
    if not isinstance(manifest.get("operator"), str):
        raise ValueError("manifest operator must be a string")
    fingerprints = {}
    for name in ("canonical_cases.json", "perf_baseline.json"):
        fingerprints[name] = sha256(package / name)
        if manifest.get("fingerprint", {}).get(name) != fingerprints[name]:
            raise ValueError("package fingerprint mismatch: " + name)
    doc = json.loads((package / "canonical_cases.json").read_text())
    if not isinstance(doc, dict):
        raise ValueError("canonical must be an object")
    all_cases = unique(doc["cases"], "canonical")
    cases = {cid: c for cid, c in all_cases.items() if c.get("case_purpose") != "info"}
    if not cases:
        raise ValueError("empty performance case universe")
    if any(c.get("op") != manifest.get("operator") for c in cases.values()):
        raise ValueError("canonical operator differs from manifest")
    baseline = unique(json.loads((package / "perf_baseline.json").read_text()), "baseline")
    if set(baseline) - set(all_cases):
        raise ValueError("baseline contains unknown case IDs")
    return {"operator": manifest["operator"], "cases": cases,
            "baseline": baseline, "fingerprint": fingerprints}


def validate_plan(plan):
    if not isinstance(plan, list) or not plan:
        raise ValueError("missing kernel plan")
    names, symbols = set(), set()
    for kernel in plan:
        if not isinstance(kernel, dict):
            raise ValueError("invalid kernel plan entry")
        symbol, name, count = (kernel.get(k) for k in
                               ("symbol", "op_name", "launches_per_call"))
        if not isinstance(symbol, str) or not symbol or not isinstance(name, str) or not name:
            raise ValueError("kernel symbol and op_name are required")
        if name in names or symbol in symbols:
            raise ValueError("duplicate kernel identity")
        if type(count) is not int or not 1 <= count <= 166:
            raise ValueError("launches_per_call must be an integer in [1,166]")
        # The adapter declaration excludes setup calls, including same-name setup kernels.
        if kernel.get("target_only") is not True:
            raise ValueError("kernel plan must establish target_only (no preparation launches)")
        names.add(name)
        symbols.add(symbol)
    return plan


def measurement_ms(case, record):
    if record.get("case") != identity(case):
        raise ValueError("measured shape/dtype recipe does not match canonical")
    if record.get("status") != "COLLECTED":
        raise ValueError(record.get("error", "case was not collected"))
    if record.get("timing_scope") != "kernel_only" or record.get("collector") != "msprof op":
        raise ValueError("requires msprof op kernel-only evidence")
    plan = validate_plan(record.get("plan"))
    kernels = record.get("kernels")
    if not isinstance(kernels, list) or len(kernels) != len(plan):
        raise ValueError("missing or extra kernel evidence")
    total_us = 0.0
    for expected, measured in zip(plan, kernels):
        if not isinstance(measured, dict):
            raise ValueError("kernel evidence must be an object")
        if measured.get("kernel") != expected:
            raise ValueError("kernel evidence does not match plan")
        execution = measured.get("execution", {})
        if not isinstance(execution, dict):
            raise ValueError("execution evidence must be an object")
        if (execution.get("kind") != "ok" or execution.get("completed") != SAMPLES
                or execution.get("consistent") is not True or execution.get("library_match") is not True):
            raise ValueError("incomplete/failed execution or mismatched build library")
        samples = measured.get("samples_us")
        if (not isinstance(samples, list) or len(samples) != SAMPLES * expected["launches_per_call"]
                or not all(positive(v) for v in samples)):
            raise ValueError("missing, extra or invalid kernel samples")
        # Sum all launches of every required kernel, not the unweighted mean of kernels.
        total_us += sum(samples)
    ms = total_us / SAMPLES / 1000
    if not positive(ms):
        raise ValueError("invalid aggregate timing")
    return ms



def resolve_plan(mapping, case):
    if (not isinstance(mapping, dict) or not isinstance(mapping.get("operators", {}), dict)
            or not isinstance(mapping.get("cases", {}), dict)):
        raise ValueError("kernel_map and its operators/cases must be objects")
    return validate_plan(mapping.get("cases", {}).get(case["case_id"],
                         mapping.get("operators", {}).get(case["op"])))


def read_kernel_samples(root, kernel, device):
    root = Path(root)
    values, sources, seen = [], [], set()
    for path in sorted(root.rglob("OpBasicInfo*.csv")):
        if path.parent in seen:
            raise ValueError("duplicate profiler export for one launch")
        seen.add(path.parent)
        with path.open(newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        if len(rows) != 1:
            raise ValueError("expected one launch per OpBasicInfo CSV")
        row = rows[0]
        if row["Op Name"] != kernel["op_name"] or int(row["Device Id"]) != device:
            raise ValueError("unexpected kernel/device in profiler output")
        value = float(row["Task Duration(us)"])
        if not positive(value):
            raise ValueError("invalid kernel duration")
        values.append(value)
        sources.append(str(path.relative_to(root)))
    expected = SAMPLES * kernel["launches_per_call"]
    if len(values) != expected:
        raise ValueError(f"expected {expected} kernel samples, found {len(values)}")
    return values, sources


def load_evidence(path):
    """Re-read retained CSVs; derived JSON averages or samples are not authoritative."""
    path = Path(path)
    doc = json.loads(path.read_text())
    if not isinstance(doc, dict):
        raise ValueError("performance evidence must be an object")
    records = unique(doc.get("cases"), "measurements")
    for record in records.values():
        if record.get("status") != "COLLECTED":
            continue
        try:
            kernels = record.get("kernels")
            if not isinstance(kernels, list) or not kernels:
                raise ValueError("missing kernel evidence")
            for kernel in kernels:
                if not isinstance(kernel, dict):
                    raise ValueError("kernel evidence must be an object")
                spec = validate_plan([kernel.get("kernel")])[0]
                profile_dir = kernel.get("profile_dir")
                if not isinstance(profile_dir, str) or Path(profile_dir).is_absolute():
                    raise ValueError("profile_dir must be relative to measurement file")
                values, sources = read_kernel_samples(path.parent / profile_dir, spec, record["device"])
                if kernel.get("csv") != sources:
                    raise ValueError("raw CSV source list differs from recorded collection")
                kernel["samples_us"] = values
        except (OSError, ValueError, KeyError, TypeError) as exc:
            record.update(status="ERROR", error="raw sampling evidence: " + str(exc))
    return doc

def judge(contract, measurements):
    """Recompute decisions from raw samples; never trust imported PASS or averages."""
    if not isinstance(measurements, dict) or measurements.get("schema") != SCHEMA:
        raise ValueError("unsupported performance evidence schema")
    if measurements.get("package_fingerprint") != contract["fingerprint"]:
        raise ValueError("performance evidence belongs to a different package/baseline")
    build = measurements.get("build")
    if (not isinstance(build, dict) or build.get("skip_build") is not False
            or not isinstance(build.get("build"), dict) or build["build"].get("returncode") != 0
            or not isinstance(build.get("artifacts"), dict)):
        raise ValueError("successful non-skipped build evidence is required")
    allowed_libraries = {a.get("sha256") for a in build["artifacts"].values() if isinstance(a, dict)} - {None}
    if not allowed_libraries:
        raise ValueError("build evidence has no library fingerprints")
    records = unique(measurements.get("cases"), "measurements")
    if set(records) - set(contract["cases"]):
        raise ValueError("performance evidence contains unknown case IDs")
    rows = []
    for cid, case in contract["cases"].items():
        row = {"case_id": cid, "case": identity(case), "status": "INSUFFICIENT"}
        reasons = []
        baseline = contract["baseline"].get(cid)
        if not baseline or baseline.get("matched") is not True or not positive(baseline.get("avg_ms")):
            reasons.append("missing/unmatched/invalid GPU average baseline")
        elif baseline.get("bench_key") != case.get("bench_key") or not case.get("bench_key"):
            reasons.append("GPU source entry does not match canonical")
        else:
            row.update(gpu_ms=baseline["avg_ms"], limit_ms=baseline["avg_ms"] / GPU_FACTOR,
                       bench_key=baseline["bench_key"])
        record = records.get(cid)
        if record is None:
            reasons.append("no measurement for expected case")
        else:
            try:
                plan = resolve_plan(measurements.get("kernel_map"), case)
                if record.get("plan") != plan or record.get("soc") != build.get("soc"):
                    raise ValueError("measurement plan/build does not match collection provenance")
                row["npu_mean_ms"] = measurement_ms(case, record)
                if any(k["execution"].get("library_sha256") not in allowed_libraries for k in record["kernels"]):
                    raise ValueError("kernel execution library is not from the recorded build")
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                reasons.append(str(exc))
            if (record.get("target") != "950PR" or record.get("layout") != "column_major"
                    or not str(record.get("soc", "")).lower().startswith("ascend950")
                    or record.get("abi") != "device" or case.get("op") not in CHOL_OPS):
                reasons.append("task requires Cholesky on 950PR with column-major Device ABI")
        if not reasons:
            row["ratio_npu_gpu"] = row["npu_mean_ms"] / row["gpu_ms"]
            row["status"] = "PASS" if row["npu_mean_ms"] <= row["limit_ms"] else "FAIL"
        row["reasons"] = reasons
        rows.append(row)
    counts = {status: sum(row["status"] == status for row in rows)
              for status in ("PASS", "FAIL", "INSUFFICIENT")}
    verdict = "FAIL" if counts["FAIL"] else ("INSUFFICIENT" if counts["INSUFFICIENT"] else "PASS")
    return {"schema": SCHEMA, "operator": contract["operator"],
            "performance_verdict": verdict, "overall_acceptance": "NOT_DETERMINED",
            "rule": "NPU mean kernel ms <= GPU avg_ms / 0.35",
            "package_fingerprint": contract["fingerprint"], "cases": rows,
            "summary": {"expected": len(rows), **counts,
                        "complete": counts["INSUFFICIENT"] == 0}}
