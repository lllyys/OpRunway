#!/usr/bin/env python3
"""Export the developer adapter example and its fixed protocol header."""
import argparse
import hashlib
import re
from pathlib import Path
import shutil
import tarfile

ROOT = Path(__file__).resolve().parents[2]

def copy_implementation(source, destination):
    source = Path(source).resolve()
    pending = ["LICENSE", "include/cann_ops_solver.h", "include/cann_ops_solver_common.h",
               "src/sgetrf/sgetrf_host.cpp", "src/sgetrf/sgetrf_kernel.cpp",
               "src/utils/aclsolver_auxiliary.cpp"]
    copied = {}
    while pending:
        relative = pending.pop()
        if relative in copied:
            continue
        path = (source / relative).resolve()
        path.relative_to(source)
        data = path.read_bytes()
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        copied[relative] = hashlib.sha256(data).hexdigest()
        if path.suffix in (".h", ".hpp", ".cpp"):
            for include in re.findall(r'^\s*#include\s*"([^"\n]+)"', data.decode(), re.M):
                candidates = [path.parent / include, source / "include" / include]
                found = next((p.resolve() for p in candidates if p.is_file()), None)
                if found:
                    pending.append(str(found.relative_to(source)))
    shutil.copytree(ROOT / "assets/source-sample", destination, dirs_exist_ok=True)
    (destination / "SOURCE.md").write_text(
        "# Source\n\nReal sgetrf implementation and dependencies copied unchanged from the supplied ops-solver project.\n"
        "Original copyright notices and LICENSE are retained. Build files and adapter are sample additions.\n\n"
        + "\n".join("- `" + name + "`: `" + digest + "`" for name, digest in sorted(copied.items())) + "\n")


def export(destination, source_dir=None):
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError(destination)
    shutil.copytree(ROOT / "assets/unified-sample", destination,
                    ignore=shutil.ignore_patterns("._*", "__pycache__", "*.pyc"))
    operator_test = destination / "test/sgetrf"
    shutil.copy2(ROOT / "assets/adapter/solver_adapter.h", operator_test / "solver_adapter.h")
    if source_dir is not None:
        copy_implementation(source_dir, destination)
    archive = destination.with_name(destination.name + ".tar.gz")
    with tarfile.open(archive, "w:gz") as stream:
        stream.add(destination, arcname=destination.name)
    archive.with_name(archive.name + ".sha256").write_text(
        hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name + "\n")
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("--source-dir", help="supplied ops-solver source; include real sgetrf and minimal build")
    args = parser.parse_args()
    print(export(args.out, args.source_dir))
