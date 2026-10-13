"""Fixed adapter integration behavior; executed on the remote test environment."""
from pathlib import Path
import json
import numpy as np
import pytest
import exec_case
import run_harness
import op_abi

def test_adapter_compile_records_source_and_forces_skill_header(tmp_path, monkeypatch):
    repo=tmp_path/'repo';(repo/'include').mkdir(parents=True)
    (repo/'include/cann_ops_solver.h').write_text('/* delivered header */')
    adapter=tmp_path/'adapter.cpp';adapter.write_text('/* delivered source */')
    binary=tmp_path/'out/executor'
    calls=[]
    def compile(cmd, **kw):
        calls.append(cmd);binary.write_bytes(b'executable')
        return {'timed_out':False,'returncode':0,'output':''}
    monkeypatch.setattr(exec_case.proc,'run',compile)
    result=exec_case.compile_adapter(repo,tmp_path/'cann',binary,adapter,['cmatinv_batched'])
    assert '-include' in calls[0]
    assert str(exec_case.HERE/'adapter_exec.cpp') in calls[0]
    assert result['adapter_sha256']==exec_case.sha256_file(adapter)
    assert (binary.with_name('executor.sources')/'adapter.cpp').read_bytes()==adapter.read_bytes()
    assert result['sha256']==exec_case.sha256_file(binary)

def test_adapter_requires_one_operator(tmp_path):
    source=tmp_path/'a.cpp';source.write_text('')
    with pytest.raises(exec_case.ExecError,match='one operator'):
        exec_case.compile_adapter(tmp_path,tmp_path,tmp_path/'out',source,['spotrf','spotri'])

def test_adapter_factor_preparation_preserves_judge_input():
    class Gen:
        @staticmethod
        def validate_case(c): pass
        @staticmethod
        def build_case_arrays(c):
            return {'A32':np.array([[4,2],[2,3]],dtype=np.float32),'B32':np.ones((2,1),np.float32)}
    case={'case_id':'solve','op':'spotrs','n':2,'nrhs':1,'uplo':'L'}
    arrays,inputs,_=run_harness.prepare_case({'gen':Gen},case,op_abi.get('spotrs'),adapter=True)
    np.testing.assert_array_equal(arrays['A32'],[[4,2],[2,3]])
    np.testing.assert_allclose(inputs['in_a']@inputs['in_a'].T,arrays['A32'],rtol=1e-6)
    assert not np.shares_memory(inputs['in_a'],arrays['A32'])
    with pytest.raises(run_harness.ContractError,match='awaiting_delivery'):
        run_harness.prepare_case({'gen':Gen},case,op_abi.get('spotrs'))


def test_performance_adapter_admits_registered_awaiting_operator(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import run_performance
    class ReachedPreparation(Exception): pass
    def prepare(*args, **kwargs):
        assert kwargs['adapter'] is True
        raise ReachedPreparation
    monkeypatch.setattr(run_harness, 'prepare_case', prepare)
    args=SimpleNamespace(adapter='delivered.cpp', target='950', device=0, layout='column_major')
    case={'case_id':'solve', 'op':'spotrs', 'n':2, 'nrhs':1, 'dtype':'float32', 'uplo':'L'}
    with pytest.raises(ReachedPreparation):
        run_performance.collect_case(case, [], tmp_path, {}, None, args, {'soc':'ascend950'})
    args.adapter=None
    with pytest.raises(op_abi.AbiError):
        run_performance.collect_case(case, [], tmp_path, {}, None, args, {})


def test_adapter_spec_native_leading_dimension_and_input_kind():
    abi=op_abi.get('spotrs')
    case={'op':'spotrs','n':8,'nrhs':2,'uplo':'L'}
    assert run_harness.build_spec(abi,case,5,0,'column_major')['ldb']==8
    assert run_harness.build_spec(abi,case,5,0,'row_major')['ldb']==2
    assert run_harness.build_spec(abi,case,5,0)['input_kind']=='cholesky_factor'
    case.update(case_purpose='info',info_probe='bad_param_uplo',ldb=12)
    spec=run_harness.build_spec(abi,case,5,0,'column_major')
    assert spec['ldb']==12 and spec['input_kind']=='matrix' and spec['uplo']=='?'
    case.update(lda=0,ldb=0)
    spec=run_harness.build_spec(abi,case,5,0,'column_major')
    assert spec['lda']==0 and spec['ldb']==0


@pytest.mark.parametrize("op", sorted(op_abi.LUQR_OPS))
def test_luqr_transport_matches_generated_arrays(op):
    import gen_data_luqr as gen
    case = dict(case_id="small", op=op, n=4, seed=17, dtype="complex64" if op[0]=="c" else "float32")
    if op.endswith("batched"):
        case.update(batch=3, lda=4, sample_map=gen.derive_sample_map(17,3))
    elif op.endswith(("getrf", "geqrf", "gqr", "mqr", "gels")):
        case.update(m=6)
    if op.endswith(("gqr", "mqr")):
        case.update(k=2, side="RIGHT", trans="N")
    if op.endswith("gels"):
        case.update(nrhs=2, ldda=9, lddb=10, lddx=7)
    abi=op_abi.get_adapter(op)
    arrays,inputs,_=run_harness.prepare_case({"luqr_gen":gen},case,abi,adapter=True)
    spec=run_harness.build_spec(abi,case,3,0,"column_major")
    batch=case.get("batch",1)
    assert inputs["in_a"].size == batch*spec["a_rows"]*spec["a_cols"]
    assert inputs.get("in_b",np.empty(0)).size == batch*spec["b_rows"]*spec["b_cols"]
    assert inputs.get("in_ipiv",np.empty(0)).size == batch*spec["in_ipiv_count"]
    assert inputs.get("in_tau",np.empty(0)).size == batch*spec["in_tau_count"]
    if "getri" in op:
        np.testing.assert_array_equal(inputs["in_a"],arrays["LU32"])
    if op.endswith("gels"):
        assert (spec["lda"],spec["ldb"],spec["ldx"]) == (9,10,7)
        assert spec["out32_shape"] == "4,2"
    if op.endswith("mqr"):
        assert (spec["a_rows"],spec["a_cols"]) == (4,2)
        np.testing.assert_array_equal(inputs["in_b"],arrays["C32"])


def test_auxiliary_output_errors_and_evidence(tmp_path):
    import evidence
    (tmp_path/"out").mkdir()
    spec=dict(batch=1,info_kind="scalar",dtype="complex64",out_tau_count=2,out_ipiv_count=2)
    ipiv=np.array([1,2],np.int32); tau=np.array([1+2j,3+4j],np.complex64)
    ipiv.tofile(tmp_path/"out/ipiv.bin");tau.tofile(tmp_path/"out/tau.bin")
    extras,errors=exec_case.read_extras(tmp_path,spec)
    assert not errors
    dut=dict(out32=np.eye(2,dtype=np.complex64),info=0,status="ok",**extras)
    evidence.save_dut_npz(tmp_path/"dut.npz",dut)
    loaded=evidence.load_dut_npz(tmp_path/"dut.npz")
    np.testing.assert_array_equal(loaded["tau"],tau)
    np.testing.assert_array_equal(loaded["ipiv"],ipiv)
    (tmp_path/"out/tau.bin").write_bytes(b"bad")
    extras,errors=exec_case.read_extras(tmp_path,spec)
    assert "tau" not in extras and errors
    assert "out/tau.bin" in exec_case.clear_stale_outputs(tmp_path)
