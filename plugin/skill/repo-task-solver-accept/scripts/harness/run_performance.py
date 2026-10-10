#!/usr/bin/env python3
"""Collect every package case with msprof op, then judge against its frozen GPU baseline."""
import argparse
import gzip
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "criteria"))
import performance
import exec_case
import op_abi
import run_harness


read_kernel_samples = performance.read_kernel_samples


def make_executor(directory, executor, kernel):
    profile = directory / "profile"
    options = ["--output=" + str(profile), "--kernel-name=" + kernel["symbol"],
               "--launch-count=" + str(performance.SAMPLES * kernel["launches_per_call"]),
               "--warm-up=5", "--replay-mode=kernel", "--aic-metrics=BasicInfo"]
    wrapper = directory / "profile_executor"
    wrapper.write_text("#!/usr/bin/env python3\nimport os,shlex,sys\n"
                       + "app=shlex.join([" + repr(str(executor)) + ",sys.argv[1]])\n"
                       + "os.execvp('msprof',['msprof','op','--application='+app]+"
                       + repr(options) + ")\n")
    wrapper.chmod(0o755)
    return wrapper, options


def collect_case(case, plan, directory, mods, executor, args, provenance):
    abi = op_abi.require_exec(case["op"])
    record = {"case_id": case["case_id"], "case": performance.identity(case),
              "status": "ERROR", "collector": "msprof op", "timing_scope": "kernel_only",
              "target": args.target, "device": args.device, "soc": provenance.get("soc"), "layout": args.layout,
              "abi": abi.abi, "plan": plan, "kernels": []}
    # Probe recipes exercise the existing Host sample, never a formal Cholesky PASS.
    prepared = (run_harness.probe_case(mods, case["op"], case["n"], case.get("batch", 1), case["seed"])
                if case.get("case_purpose") == "probe" else dict(case))
    _, inputs, _ = run_harness.prepare_case(mods, prepared, abi)
    spec = run_harness.build_spec(abi, case, performance.SAMPLES, args.device, args.layout)
    for i, kernel in enumerate(plan):
        work = directory / f"kernel-{i:03}"
        work.mkdir(parents=True)
        wrapper, options = make_executor(work, executor, kernel)
        execution = exec_case.run_case(wrapper, work / "execution", spec, inputs,
                                       args.repo, args.ascend_home, timeout=args.timeout)
        det = exec_case.rerun_record(execution["result"])
        library = exec_case.loaded_lib_record(execution["result"].get("lib_path"), provenance)
        facts = {"kind": execution["kind"], "completed": det["runs_completed"],
                 "consistent": det["consistent"], "library_match": library.get("match"),
                 "library_sha256": library.get("sha256")}
        if (facts["kind"] != "ok" or facts["completed"] != performance.SAMPLES
                or facts["consistent"] is not True or facts["library_match"] is not True):
            raise ValueError("profiled execution failed/incomplete; inspect case execution artifacts")
        (work / "execution-result.json").write_text(json.dumps(execution["result"], indent=2) + "\n")
        values, sources = read_kernel_samples(work / "profile", kernel, args.device)
        record["kernels"].append({"kernel": kernel, "samples_us": values,
                                  "csv": sources, "profile_dir": str(Path(directory.name) / work.name / "profile"),
                                  "execution": facts, "profiler_options": options})
        # Raw timing CSVs remain; successful matrix binaries need not accumulate.
        exec_case.clear_stale_outputs(work / "execution")
        for path in (work / "execution/in").glob("*.bin"):
            path.unlink()
    record["status"] = "COLLECTED"
    return record


def save(out, contract, measurements):
    (out / "performance-measurements.json").write_text(json.dumps(measurements, indent=2) + "\n")
    report = performance.judge(contract, measurements)
    (out / "performance-report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--package", required=True)
    ap.add_argument("--out", required=True, help="new output directory")
    ap.add_argument("--measurements", help="rejudge saved measurements without running a device")
    ap.add_argument("--repo")
    ap.add_argument("--gen-dir")
    ap.add_argument("--provenance")
    ap.add_argument("--kernel-map", help="JSON {operators:{op:[kernels]}, cases:{case_id:[kernels]}}")
    ap.add_argument("--device", type=int, default=0)
    ap.add_argument("--target", default="unverified", help="A0 verified target model; formal task requires 950PR")
    ap.add_argument("--layout", choices=("row_major", "column_major"), default="column_major")
    ap.add_argument("--case-id", action="append", help="debug subset; omitted cases stay INSUFFICIENT")
    ap.add_argument("--timeout", type=int, default=1800)
    args = ap.parse_args(argv)
    out = Path(args.out).resolve()
    try:
        contract = performance.load_contract(args.package)
        out.mkdir(parents=True, exist_ok=False)
        if args.measurements:
            measurements = performance.load_evidence(args.measurements)
            # Save only the newly judged report; source CSV paths remain relative to the source evidence.
            report = performance.judge(contract, measurements)
            (out / "performance-report.json").write_text(json.dumps(report, indent=2) + "\n")
        else:
            if not all((args.repo, args.gen_dir, args.provenance, args.kernel_map)):
                raise ValueError("collection requires repo, gen-dir, provenance and kernel-map")
            if args.device < 0 or args.timeout <= 0:
                raise ValueError("invalid device or timeout")
            args.repo = str(Path(args.repo).resolve())
            args.ascend_home = os.environ.get("ASCEND_HOME_PATH")
            if not args.ascend_home:
                raise ValueError("source the CANN set_env.sh first")
            provenance = json.loads(Path(args.provenance).read_text())
            if provenance.get("skip_build") is not False or provenance.get("build", {}).get("returncode") != 0:
                raise ValueError("successful non-skipped DUT build provenance required")
            mapping = json.loads(Path(args.kernel_map).read_text())
            if not isinstance(mapping, dict):
                raise ValueError("kernel-map must be an object")
            selected = set(args.case_id or contract["cases"])
            if selected - set(contract["cases"]):
                raise ValueError("unknown selected case ID")
            measurements = {"schema": performance.SCHEMA, "package_fingerprint": contract["fingerprint"],
                            "build": provenance, "kernel_map": mapping, "cases": []}
            mods = run_harness.load_modules(args.gen_dir)
            index_paths = [p for p in (Path(args.package)/"cases/index.json", Path(args.package)/"cases/index.json.gz") if p.exists()]
            entries = {}
            if len(index_paths) == 1:
                path = index_paths[0]
                with (gzip.open(path, "rt") if path.suffix == ".gz" else path.open()) as f:
                    entries = performance.unique(json.load(f)["cases"], "input index")
            executors = {}
            for number, (cid, canonical) in enumerate(contract["cases"].items()):
                if cid not in selected:
                    continue
                record = {"case_id": cid, "case": performance.identity(canonical), "status": "ERROR"}
                try:
                    op_abi.require_exec(canonical["op"])
                    # Current adapter rejects column-major; never run row-major under a column-major label.
                    if args.layout != "row_major":
                        raise ValueError("column_major adapter awaiting actual delivery")
                    plan = performance.resolve_plan(mapping, canonical)
                    case = dict(canonical)
                    entry = entries.get(cid, {})
                    for key in performance.IDENTITY:
                        if key in entry and key in case and entry[key] != case[key]:
                            raise ValueError("input index differs from canonical: " + key)
                    if "sample_map" in entry:
                        case["sample_map"] = entry["sample_map"]
                    op = case["op"]
                    if op not in executors:
                        compiled = exec_case.compile_executor(args.repo, args.ascend_home,
                                   out / "executors" / op, ops=[op], timeout=args.timeout)
                        executors[op] = compiled["bin"]
                        measurements.setdefault("compiled", {})[op] = compiled
                    record = collect_case(case, plan, out / f"case-{number:05}", mods,
                                          executors[op], args, provenance)
                except (OSError, ValueError, TypeError, KeyError, op_abi.AbiError,
                        exec_case.ExecError, run_harness.ContractError) as exc:
                    record["error"] = str(exc)
                measurements["cases"].append(record)
                report = save(out, contract, measurements)
                print(cid, record["status"], flush=True)
            report = save(out, contract, measurements)
        print(json.dumps(report["summary"], indent=2))
        return {"PASS": 0, "FAIL": 1, "INSUFFICIENT": 2}[report["performance_verdict"]]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("performance contract error: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
