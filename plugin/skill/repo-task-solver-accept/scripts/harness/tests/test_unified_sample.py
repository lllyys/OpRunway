"""Unified protocol geometry and standalone delivery behavior."""
from pathlib import Path
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


def test_developer_export_contains_only_adapter_contract(tmp_path):
    dest = tmp_path/"sample"
    archive = export_solver_sample.export(dest)
    expected = {"test/sgetrf/README.md", "test/sgetrf/sgetrf_adapter.cpp", "test/sgetrf/solver_adapter.h"}
    assert {str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file()} == expected
    assert (dest/"test/sgetrf/solver_adapter.h").read_bytes() == (export_solver_sample.ROOT/"assets/adapter/solver_adapter.h").read_bytes()
    assert (dest/"test/sgetrf/sgetrf_adapter.cpp").read_bytes() == (export_solver_sample.ROOT/"assets/unified-sample/test/sgetrf/sgetrf_adapter.cpp").read_bytes()
    import hashlib
    import tarfile
    assert archive.with_name(archive.name+".sha256").read_text().split()[0] == hashlib.sha256(archive.read_bytes()).hexdigest()
    with tarfile.open(archive) as stream:
        assert {str(Path(p.name).relative_to(dest.name)) for p in stream.getmembers() if p.isfile()} == expected
