#!/usr/bin/env python3
"""cmatinv_batched accuracy and kernel performance tests."""
import argparse
import csv
import json
import math
from pathlib import Path
import shlex
import statistics
import sys

import numpy as np
from scipy.linalg import lapack

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
import proc

KERNEL = "_Z22cmatinv_batched_kernelPhS_S_S_S_S_S_"
OP_NAME = "cmatinv_batched_kernel"
SAMPLES = 30


def make_input(n, batch, seed):
    rng = np.random.default_rng(seed)
    data = np.empty((batch, n, n), dtype=np.complex64)
    idx = np.arange(n)
    for slot in range(batch):
        a = rng.uniform(-1, 1, (n, n)) + 1j * rng.uniform(-1, 1, (n, n))
        off = np.abs(a).sum(axis=1) - np.abs(np.diag(a))
        a[idx, idx] = (off + 1 + rng.uniform(0, 1, n)) * np.exp(1j * rng.uniform(0, 2*np.pi, n))
        data[slot] = a
    return data


def residual(a, inverse):
    a = a.astype(np.complex128)
    inverse = inverse.astype(np.complex128)
    if not np.isfinite(a).all() or not np.isfinite(inverse).all():
        raise ValueError("non-finite matrix")
    denominator = len(a) * np.linalg.norm(a, 1) * np.linalg.norm(inverse, 1) * 2**-24
    if not math.isfinite(denominator) or denominator <= 0:
        raise ValueError("invalid residual denominator")
    value = float(np.linalg.norm(np.eye(len(a)) - a @ inverse, 1) / denominator)
    if not math.isfinite(value):
        raise ValueError("non-finite residual")
    return value


def reference_ratios(data):
    ratios = []
    for a in data:
        lu, piv, info = lapack.cgetrf(a)
        if info:
            raise ValueError(f"CPU factorization info={info}")
        inverse, info = lapack.cgetri(lu, piv)
        if info:
            raise ValueError(f"CPU inversion info={info}")
        ratios.append(residual(a, inverse))
    return ratios


def judge_accuracy(data, output, cpu, info):
    if output.shape != data.shape or len(cpu) != len(data):
        raise ValueError("output/reference shape mismatch")
    mean = statistics.mean(cpu)
    rows = []
    for slot, (a, inverse, ref) in enumerate(zip(data, output, cpu)):
        threshold = max(5 * ref, 3 * mean)
        row = {"slot": slot, "cpu_ratio": ref, "threshold": threshold, "ratio": None, "pass": False}
        try:
            row["ratio"] = residual(a, inverse)
            row["pass"] = row["ratio"] <= threshold
        except ValueError as exc:
            row["error"] = str(exc)
        rows.append(row)
    passed = info == [0] and all(row["pass"] for row in rows)
    return {"status": "PASS" if passed else "FAIL", "info": info,
            "cpu_ratio_mean": mean, "matrices": rows}


def execute(args, data, directory, input_file, profiling):
    directory.mkdir()
    runs = SAMPLES if profiling else 5
    spec = {"op": "cmatinv_batched", "abi": "host", "call_shape": "outofplace_a_ainv",
            "dtype": "complex64", "layout": "row_major", "info_kind": "scalar",
            "device_id": args.device, "n": args.n, "batch": args.batch, "nrhs": 0,
            "lda": args.n, "ldb": args.n, "input_kind": "matrix", "runs": runs,
            "in_a": input_file, "out_a": directory / "inverse.bin",
            "out_info": directory / "info.bin", "result": directory / "execution.txt"}
    spec_file = directory / "case.spec"
    spec_file.write_text("".join(f"{key}={value}\n" for key, value in spec.items()))
    command = [str(Path(args.executor).resolve(strict=True)), str(spec_file)]
    if profiling:
        command = ["msprof", "op", "--application=" + shlex.join(command),
                   "--output=" + str(directory / "profile"), "--kernel-name=" + args.kernel_name,
                   "--launch-count=30", "--warm-up=5", "--replay-mode=kernel", "--aic-metrics=BasicInfo"]
    result = proc.run(command, timeout=args.timeout, log_path=directory / "run.log")
    if result["timed_out"] or result["returncode"]:
        raise ValueError(f"execution failed; see {directory / 'run.log'}")
    # msprof can return zero even when its application fails.
    facts = dict(line.split("=", 1) for line in (directory / "execution.txt").read_text().splitlines())
    if (facts.get("status") != "ok" or facts.get("exit") != "0"
            or facts.get("rerun_runs_completed") != str(runs) or facts.get("rerun_consistent") != "1"):
        raise ValueError(f"execution incomplete or inconsistent; see {directory / 'execution.txt'}")
    if (directory / "inverse.bin").stat().st_size != data.nbytes or (directory / "info.bin").stat().st_size != 4:
        raise ValueError("output byte count mismatch")
    output = np.fromfile(directory / "inverse.bin", dtype=np.complex64).reshape(data.shape)
    info = np.fromfile(directory / "info.bin", dtype=np.int32).tolist()
    return output, info


def read_profile(directory, device, op_name=OP_NAME):
    values, seen = [], set()
    for path in sorted(directory.rglob("OpBasicInfo*.csv")):
        if path.parent in seen:
            raise ValueError("duplicate profiler export")
        seen.add(path.parent)
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        if len(rows) != 1 or rows[0]["Op Name"] != op_name or int(rows[0]["Device Id"]) != device:
            raise ValueError("unexpected profiler kernel/device/launch count")
        value = float(rows[0]["Task Duration(us)"])
        if not math.isfinite(value) or value <= 0:
            raise ValueError("invalid profiler duration")
        values.append(value / 1000)
    if len(values) != SAMPLES:
        raise ValueError(f"expected {SAMPLES} launches, got {len(values)}")
    return {"collector": "msprof op", "timing_scope": "kernel_only", "statistic": "mean",
            "samples_ms": values, "mean_ms": statistics.mean(values),
            "median_ms": statistics.median(values), "samples": len(values), "warm_up": 5}


def load_baseline(path, case):
    baseline = json.loads(Path(path).read_text())
    if baseline.get("case") != case or baseline.get("timing_scope") != "kernel_only" or baseline.get("statistic") != "mean":
        raise ValueError("GPU baseline case or timing scope/statistic mismatch")
    if not isinstance(baseline.get("reference"), str) or not baseline["reference"].strip():
        raise ValueError("GPU baseline requires reference source")
    for key in ("gpu_ms", "required_speedup"):
        if isinstance(baseline.get(key), bool) or not isinstance(baseline.get(key), (int, float)) or not math.isfinite(baseline[key]) or baseline[key] <= 0:
            raise ValueError(f"GPU baseline {key} must be finite and positive")
    return baseline


def compare_performance(timing, baseline):
    if baseline is None:
        return {**timing, "status": "MEASURED", "comparison": "NO_BASELINE"}
    speedup = baseline["gpu_ms"] / timing["mean_ms"]
    return {**timing, "baseline": baseline, "speedup": speedup,
            "status": "PASS" if speedup >= baseline["required_speedup"] else "FAIL"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executor", required=True)
    parser.add_argument("--out", required=True, help="new results directory")
    parser.add_argument("--mode", choices=("precision", "performance", "all"), default="all")
    parser.add_argument("--n", type=int, default=8)
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--seed", type=int, default=923000001)
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--baseline", help="GPU baseline JSON for the same case")
    parser.add_argument("--kernel-name", default=KERNEL)
    parser.add_argument("--op-name", default=OP_NAME, help="expected Op Name in profiler CSV")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args(argv)
    if not 1 <= args.n <= 256 or not 1 <= args.batch <= 3000 or args.seed < 0 or args.device < 0 or args.timeout <= 0:
        parser.error("require 1<=n<=256, 1<=batch<=3000, seed/device>=0, timeout>0")
    if args.mode == "precision" and args.baseline:
        parser.error("--baseline requires a performance test")
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    case = {"op": "cmatinv_batched", "n": args.n, "batch": args.batch, "seed": args.seed,
            "dtype": "complex64", "layout": "row_major"}
    report = {"case": case, "status": "ERROR"}
    try:
        baseline = load_baseline(args.baseline, case) if args.baseline else None
        data = make_input(args.n, args.batch, args.seed)
        cpu = reference_ratios(data)
        input_file = out / "input.bin"
        data.tofile(input_file)
        modes = ["precision", "performance"] if args.mode == "all" else [args.mode]
        for mode in modes:
            output, info = execute(args, data, out / mode, input_file, mode == "performance")
            accuracy = judge_accuracy(data, output, cpu, info)
            report[mode] = {"accuracy": accuracy}
            if mode == "performance":
                report[mode]["timing"] = compare_performance(read_profile(out / mode / "profile", args.device, args.op_name), baseline)
        failed = any(entry["accuracy"]["status"] == "FAIL" or entry.get("timing", {}).get("status") == "FAIL"
                     for entry in (report[mode] for mode in modes))
        report["status"] = "FAIL" if failed else ("MEASURED" if "performance" in modes and baseline is None else "PASS")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report["error"] = str(exc)
    (out / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps(report, indent=2, allow_nan=False))
    return {"PASS": 0, "MEASURED": 0, "FAIL": 1, "ERROR": 2}[report["status"]]


if __name__ == "__main__":
    sys.exit(main())
