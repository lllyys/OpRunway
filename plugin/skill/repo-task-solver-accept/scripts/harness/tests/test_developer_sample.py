"""Developer sample accuracy, profiling and standalone export behavior."""
import importlib.util
import json
from pathlib import Path
import tarfile
from types import SimpleNamespace

import numpy as np
import pytest
import export_cmatinv_sample

SOURCE = export_cmatinv_sample.ROOT / "assets/adapter-sample/test/cmatinv_batched/run_tests.py"
spec = importlib.util.spec_from_file_location("developer_sample", SOURCE)
sample = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sample)


def case():
    return {"op":"cmatinv_batched","n":8,"batch":4,"seed":42,"dtype":"complex64","layout":"row_major"}


def test_accuracy_actual_inputs_and_failure_modes():
    data = sample.make_input(8,4,42)
    cpu = sample.reference_ratios(data)
    output = np.linalg.inv(data.astype(np.complex128)).astype(np.complex64)
    assert sample.judge_accuracy(data,output,cpu,[0])["status"] == "PASS"
    for bad in (output * 2, np.zeros_like(output), np.full_like(output,np.nan)):
        report = sample.judge_accuracy(data,bad,cpu,[0])
        assert report["status"] == "FAIL"
        assert any(not row["pass"] for row in report["matrices"])
    assert sample.judge_accuracy(data,output,cpu,[-999])["status"] == "FAIL"


def test_matching_baseline_and_mean_comparison(tmp_path):
    path=tmp_path/'baseline.json'
    baseline={"case":case(),"reference":"measured GPU source", "timing_scope":"kernel_only",
              "statistic":"mean","gpu_ms":0.7,"required_speedup":0.35}
    path.write_text(json.dumps(baseline))
    assert sample.load_baseline(path,case()) == baseline
    assert sample.compare_performance({"mean_ms":2},baseline)["status"] == "PASS"
    assert sample.compare_performance({"mean_ms":3},baseline)["status"] == "FAIL"
    assert sample.compare_performance({"mean_ms":2},None)["status"] == "MEASURED"
    for key,value in (("statistic","median"),("gpu_ms",0),("gpu_ms",True),("reference","")):
        path.write_text(json.dumps({**baseline,key:value}))
        with pytest.raises(ValueError): sample.load_baseline(path,case())
    path.write_text(json.dumps(baseline))
    with pytest.raises(ValueError): sample.load_baseline(path,{**case(),"n":16})


def test_profile_requires_thirty_valid_launches(tmp_path):
    for i in range(30):
        path=tmp_path/str(i);path.mkdir()
        (path/'OpBasicInfo.csv').write_text('Op Name,Device Id,Task Duration(us)\ncmatinv_batched_kernel,0,100\n')
    result=sample.read_profile(tmp_path,0)
    assert result['mean_ms']==0.1 and result['samples']==30
    with pytest.raises(ValueError):sample.read_profile(tmp_path,1)
    for path in tmp_path.rglob('OpBasicInfo.csv'):
        path.write_text(path.read_text().replace('cmatinv_batched_kernel','custom_kernel'))
    assert sample.read_profile(tmp_path,0,'custom_kernel')['samples']==30
    with pytest.raises(ValueError):sample.read_profile(tmp_path,0)
    (tmp_path/'0/OpBasicInfo.csv').write_text('Op Name,Device Id,Task Duration(us)\ncustom_kernel,0,nan\n')
    with pytest.raises(ValueError):sample.read_profile(tmp_path,0,'custom_kernel')


def test_profiler_zero_exit_does_not_hide_application_failure(tmp_path,monkeypatch):
    exe=tmp_path/'exe';exe.touch()
    args=SimpleNamespace(n=8,batch=4,device=0,executor=exe,kernel_name=sample.KERNEL,timeout=10)
    directory=tmp_path/'run'
    def failed(cmd,**kwargs):
        (directory/'execution.txt').write_text('status=error\nexit=14\nrerun_runs_completed=0\n')
        return {'timed_out':False,'returncode':0}
    monkeypatch.setattr(sample.proc,'run',failed)
    with pytest.raises(ValueError,match='incomplete'):
        sample.execute(args,sample.make_input(8,4,42),directory,tmp_path/'input.bin',True)


def test_export_is_standalone_without_internal_reports(tmp_path):
    out=tmp_path/'cmatinv-sample'
    archive=export_cmatinv_sample.export(out)
    for name in ('adapter_exec.cpp','executor_io.hpp','proc.py'):
        assert (out/'test/common'/name).read_bytes()==(export_cmatinv_sample.HERE/name).read_bytes()
    assert (out/'include/solver_adapter.h').is_file()
    assert (out/'test/cmatinv_batched/run_tests.py').is_file()
    text=(out/'run_sample.sh').read_text()
    assert 'SOLVER_ACCEPT_DIR' not in text
    with tarfile.open(archive) as stream:
        names=stream.getnames()
        assert not any('SKILL.md' in name or 'VALIDATION.md' in name or 'performance-example' in name for name in names)
    with pytest.raises(FileExistsError):export_cmatinv_sample.export(out)
