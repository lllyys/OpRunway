#!/usr/bin/env python3
"""Export the unified developer sample with the acceptance runtime sources."""
import argparse
import hashlib
from pathlib import Path
import shutil
import tarfile

ROOT = Path(__file__).resolve().parents[2]


def export(destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError(destination)
    shutil.copytree(ROOT / "assets/unified-sample", destination,
                    ignore=shutil.ignore_patterns("._*", "__pycache__", "*.pyc"))
    common = destination / "test/common"
    for source in (ROOT / "scripts", ROOT / "criteria",
                   ROOT.parent / "repo-task-solver-case-gen/scripts"):
        target = common / source.parent.name / source.name
        for path in source.rglob("*"):
            relative = path.relative_to(source)
            if source == ROOT / "scripts" and relative.as_posix() not in {
                "accept_run.py", "expectations.py", "sim_dut.py",
                *{"harness/" + name for name in (
                    "adapter_exec.cpp", "harness_exec.cpp", "executor_io.hpp",
                    "build_dut.py", "exec_case.py", "evidence.py", "op_abi.py",
                    "proc.py", "run_harness.py", "run_performance.py")},
            }:
                continue
            if source.parent.name == "repo-task-solver-case-gen" and relative.name not in {
                "gen_data_cholesky.py", "gen_data_luqr.py", "fill_ratio_cpu.py", "reference_environment.py",
            }:
                continue
            if any(p.startswith("._") or p in ("tests", "__pycache__", ".pytest_cache") for p in relative.parts):
                continue
            if path.is_file() and path.suffix in (".py", ".cpp", ".hpp"):
                out = target / relative
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, out)
    header = common / ROOT.name / "assets/adapter/solver_adapter.h"
    header.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "assets/adapter/solver_adapter.h", header)
    shutil.copy2(header, destination / "test/solver_adapter.h")
    archive = destination.with_name(destination.name + ".tar.gz")
    with tarfile.open(archive, "w:gz") as stream:
        stream.add(destination, arcname=destination.name)
    archive.with_name(archive.name + ".sha256").write_text(
        hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name + "\n")
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    print(export(parser.parse_args().out))
