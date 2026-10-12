#!/usr/bin/env python3
"""Export the standalone developer sample, sharing canonical execution sources."""
import argparse
import hashlib
from pathlib import Path
import shutil
import tarfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def export(destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError(destination)
    shutil.copytree(ROOT / "assets/adapter-sample", destination,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))
    (destination / "include").mkdir()
    shutil.copy2(ROOT / "assets/adapter/solver_adapter.h", destination / "include/solver_adapter.h")
    common = destination / "test/common"
    common.mkdir()
    for name in ("adapter_exec.cpp", "executor_io.hpp", "proc.py"):
        shutil.copy2(HERE / name, common / name)
    archive = destination.with_name(destination.name + ".tar.gz")
    with tarfile.open(archive, "w:gz") as stream:
        stream.add(destination, arcname=destination.name)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_name(archive.name + ".sha256").write_text(digest + "  " + archive.name + "\n")
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="new sample directory")
    print(export(parser.parse_args().out))
