#!/usr/bin/env python3
"""Profile the verified cmatinv n8/b4 sample; report kernel timing, never a formal verdict."""
import argparse
import csv
import json
import math
import os
from pathlib import Path
import statistics
import sys

import exec_case
import run_harness

KERNEL = "_Z22cmatinv_batched_kernelPhS_S_S_S_S_S_"
OP_NAME = "cmatinv_batched_kernel"
SAMPLES = 30


def read_samples(root, device):
    """Read the measured CANN 9 OpBasicInfo CSV schema, one row per launch."""
    samples, seen = [], set()
    for path in sorted(Path(root).rglob("OpBasicInfo*.csv")):
        # Multiple exports of a launch must not count as independent samples.
        if path.parent in seen:
            raise ValueError("duplicate launch export: " + str(path.parent))
        seen.add(path.parent)
        with path.open(newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        if len(rows) != 1:
            raise ValueError("expected one launch row: " + str(path))
        row = rows[0]
        if row["Op Name"] != OP_NAME or int(row["Device Id"]) != device:
            raise ValueError("unexpected kernel or device: " + str(path))
        duration = float(row["Task Duration(us)"])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("invalid kernel duration: " + str(path))
        samples.append({"csv": str(path.relative_to(root)), "duration_us": duration})
    if len(samples) != SAMPLES:
        raise ValueError(f"expected {SAMPLES} launch samples, found {len(samples)}")
    return samples


def require_execution(report):
    summary = report["summary"]
    if (summary["total"] != 1 or summary["pass"] != 1
            or any(summary[k] != 0 for k in
                   ("fail", "error", "det_fail", "det_unknown", "lib_mismatch"))):
        raise ValueError("profiled application did not pass execution checks")
    case = report["cases"][0]
    if (case["exec"]["kind"] != "ok"
            or case["determinism"]["runs_completed"] != SAMPLES
            or case["determinism"]["consistent"] is not True):
        raise ValueError("profiled application did not complete 30 consistent runs")


def summarize(samples):
    values = [s["duration_us"] for s in samples]
    return {"count": len(values), "mean_ms": statistics.mean(values) / 1000,
            "median_ms": statistics.median(values) / 1000,
            "min_ms": min(values) / 1000, "max_ms": max(values) / 1000}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--adapter", help="developer cmatinv_batched_adapter.cpp")
    ap.add_argument("--repo", required=True, help="already built ops-solver source directory")
    ap.add_argument("--provenance", required=True, help="build.json from run_harness_sample.sh")
    ap.add_argument("--gen-dir", required=True)
    ap.add_argument("--out", required=True, help="new output directory; existing paths refused")
    ap.add_argument("--device", type=int, default=0)
    ap.add_argument("--kernel-name", default=KERNEL, help="exact profiler symbol for this sample")
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args(argv)
    out = Path(args.out).resolve()
    try:
        repo = Path(args.repo).resolve(strict=True)
        provenance = Path(args.provenance).resolve(strict=True)
        gen = Path(args.gen_dir).resolve(strict=True)
        if args.device < 0 or args.timeout <= 0:
            raise ValueError("device must be nonnegative and timeout positive")
        out.mkdir(parents=True, exist_ok=False)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    result = {"status": "ERROR", "performance_verdict": "NOT_EVALUATED",
              "reason": "No matched GPU baseline supplied for this sample",
              "timing_scope": "kernel_only", "collector": "msprof op BasicInfo",
              "sample": {"op": "cmatinv_batched", "n": 8, "batch": 4,
                         "dtype": "complex64", "seed": 923000001},
              "warm_up": 5, "requested_samples": SAMPLES,
              "kernel_symbol": args.kernel_name, "device": args.device}
    try:
        ascend_home = os.environ.get("ASCEND_HOME_PATH")
        if not ascend_home:
            raise ValueError("source the CANN set_env.sh first")
        compiled = (exec_case.compile_adapter(repo, ascend_home, out / "harness_exec",
            args.adapter, ["cmatinv_batched"], timeout=args.timeout) if args.adapter else
            exec_case.compile_executor(repo, ascend_home, out / "harness_exec",
            ops=["cmatinv_batched"], timeout=args.timeout))
        result["compile"] = compiled
        # The profiler is the harness's executor: one process group contains
        # profiler and DUT. No outer profiler wraps a nested harness process.
        wrapper = out / "profile_executor"
        profiler_args = ["--output=" + str(out / "profile"),
                         "--kernel-name=" + args.kernel_name, "--launch-count=30",
                         "--warm-up=5", "--replay-mode=kernel", "--aic-metrics=BasicInfo"]
        wrapper.write_text(
            "#!/usr/bin/env python3\nimport os,shlex,sys\n"
            + "app=shlex.join([" + repr(compiled["bin"]) + ",sys.argv[1]])\n"
            + "os.execvp('msprof',['msprof','op','--application='+app]+"
            + repr(profiler_args) + ")\n")
        wrapper.chmod(0o755)
        result["profiler_options"] = profiler_args
        app = ["--executor", str(wrapper), "--repo", str(repo),
               "--provenance", str(provenance), "--gen-dir", str(gen),
               "--probe-op", "cmatinv_batched", "--n", "8", "--batch", "4",
               "--seed", "923000001", "--rerun", str(SAMPLES),
               "--device", str(args.device), "--timeout", str(args.timeout),
               "--work-dir", str(out / "work"), "--evidence-dir", str(out / "evidence"),
               "--report", str(out / "execution.json")]
        # run_harness owns timeout and cancellation for the complete group.
        if run_harness.main(app) != 0:
            raise ValueError("profiled execution failed; see execution.json and work/*/*/exec.log")
        # msprof may return zero even when the application or data collection failed.
        require_execution(json.loads((out / "execution.json").read_text()))
        samples = read_samples(out / "profile", args.device)
        result.update(status="COLLECTED", samples=samples, statistics=summarize(samples))
    except (OSError, ValueError, KeyError, TypeError, IndexError, exec_case.ExecError) as exc:
        result["error"] = str(exc)
    (out / "performance.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: result[k] for k in ("status", "performance_verdict")}, indent=2))
    if "statistics" in result:
        print(json.dumps(result["statistics"], indent=2))
    return 0 if result["status"] == "COLLECTED" else 2


if __name__ == "__main__":
    sys.exit(main())
