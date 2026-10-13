"""Unified protocol geometry and standalone delivery behavior."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import numpy as np
import pytest
import export_solver_sample
import op_abi
import run_harness

OPS = sorted(set(op_abi._AWAITING) | op_abi.LUQR_OPS)

@pytest.mark.parametrize("op", OPS)
def test_all_26_transport_shapes(op):
    mods = run_harness.load_modules(export_solver_sample.ROOT.parent / "repo-task-solver-case-gen/scripts")
    c = dict(case_id="sample", op=op, n=4, k=2, seed=17, source="cu", lda=4, ldb=4,
             nrhs=2, batch=3 if "batched" in op.lower() else 1, uplo="L", side="RIGHT", trans="N")
    if op.endswith(("getrf", "geqrf", "gqr", "mqr", "gels")):
        c["m"] = 6
    if "potrs" in op and "Batched" in op:
        c["nrhs"] = 1
    if "batched" in op.lower():
        gen = mods["luqr_gen"] if op in op_abi.LUQR_OPS else mods["gen"]
        c["sample_map"] = gen.derive_sample_map(c["seed"], c["batch"])
    abi = op_abi.get_adapter(op, "host")
    _, inputs, _ = run_harness.prepare_case(mods, c, abi, adapter=True)
    spec = run_harness.build_spec(abi, c, 3, 0, "row_major")
    assert abi.abi == "host"
    assert op_abi.get_adapter(op).abi == "device"  # no shared registry mutation
    assert inputs["in_a"].size == c["batch"]*spec["a_rows"]*spec["a_cols"]
    assert inputs.get("in_b", np.empty(0)).size == c["batch"]*spec["b_rows"]*spec["b_cols"]
    assert spec["out_rows"]*spec["out_cols"] == np.prod(run_harness.out32_shape(abi,c)[-2:])


def test_standalone_export_and_entrypoints(tmp_path):
    dest = tmp_path/"sample"
    export_solver_sample.export(dest)
    assert not list(dest.rglob("SKILL.md"))
    assert all(p.name == "README.md" or p.name == "test" for p in dest.iterdir())
    runtime=dest/"test/common/repo-task-solver-accept"
    for source in (export_solver_sample.ROOT/"scripts/harness/run_harness.py",
                   export_solver_sample.ROOT/"criteria/verdict.py"):
        assert (runtime/source.relative_to(export_solver_sample.ROOT)).read_bytes()==source.read_bytes()
    for tool in ("run_harness.py", "run_performance.py", "build_dut.py"):
        subprocess.run([sys.executable,str(runtime/"scripts/harness"/tool),"--help"],check=True,capture_output=True)
    subprocess.run([sys.executable, "-c",
                    "import sys;sys.path.insert(0,sys.argv[1]);import run_harness;"
                    "mods=run_harness.load_modules(sys.argv[2]);"
                    "assert run_harness.has_criteria_card(mods, 'sgetrf')",
                    str(runtime/"scripts/harness"),
                    str(dest/"test/common/repo-task-solver-case-gen/scripts")],
                   check=True, capture_output=True)
    spec=importlib.util.spec_from_file_location("unified_entry",dest/"test/run_tests.py")
    entry=importlib.util.module_from_spec(spec);spec.loader.exec_module(entry)
    cmd=entry.command(["precision","--package","pkg","--repo","repo","--dut-abi","host"])
    assert "pkg/canonical_cases.json" in cmd and cmd[-2:]==["--dut-abi","host"]
