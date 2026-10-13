#!/usr/bin/env python3
"""Shared solver build, precision, performance and rejudge entrypoint."""
import argparse
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
COMMON = HERE / "common"
HARNESS = COMMON / "repo-task-solver-accept/scripts/harness"
GEN = COMMON / "repo-task-solver-case-gen/scripts"


def command(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("build", "precision", "performance", "rejudge"))
    parser.add_argument("--package", type=Path)
    args, rest = parser.parse_known_args(argv)
    script = {"build": "build_dut.py", "precision": "run_harness.py",
              "performance": "run_performance.py", "rejudge": "run_harness.py"}[args.mode]
    cmd = [sys.executable, str(HARNESS / script)]
    if args.mode in ("precision", "performance"):
        if args.package is None:
            parser.error("--package is required")
        cmd += ["--canonical" if args.mode == "precision" else "--package",
                str(args.package / "canonical_cases.json" if args.mode == "precision" else args.package)]
    if args.mode != "build":
        cmd += ["--gen-dir", str(GEN)]
    return cmd + rest


if __name__ == "__main__":
    sys.exit(subprocess.call(command(sys.argv[1:])))
