"""Audit regression tests; executed only in the remote test container."""
import gzip
import json
from pathlib import Path
import sys
import time
import numpy as np
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import exec_case
import proc
import run_harness
import op_abi
from test_harness_orchestration import mods, fake, _ctx, _spotrf_case, GEN_DIR, _sign_package


def test_missing_both_outputs_is_protocol_error(tmp_path):
    wrapper = tmp_path / 'empty'
    wrapper.write_text('#!/bin/sh\nexit 0\n')
    wrapper.chmod(0o755)
    rec = exec_case.run_case(wrapper, tmp_path / 'case',
                            {'dtype': 'float32', 'out32_shape': '1,1'},
                            {'in_a': np.ones((1, 1), dtype=np.float32)},
                            tmp_path, tmp_path)
    assert rec['kind'] == 'protocol_error'
    assert 'out32' in rec['three_key_error'] and 'info' in rec['three_key_error']


@pytest.mark.parametrize('count,tail', [(1,b''), (3,b''), (2,b'x')])
def test_info_exact_batch_bytes(tmp_path, count, tail):
    (tmp_path / 'out').mkdir()
    (tmp_path / 'out/info.bin').write_bytes(np.zeros(count, np.int32).tobytes() + tail)
    with pytest.raises(exec_case.ExecError):
        exec_case.read_info(tmp_path, {'info_kind': 'array', 'batch': 2})


def test_timeout_kills_term_ignoring_descendant(tmp_path, monkeypatch):
    monkeypatch.setattr(proc, 'KILL_GRACE_SECONDS', 0.2)
    script = tmp_path / 'tree.py'
    sentinel = tmp_path / 'escaped'
    script.write_text('import os, signal, time\n'
                      'if os.fork() == 0:\n'
                      ' signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                      ' time.sleep(1.5)\n'
                      f' open({str(sentinel)!r}, "w").write("escaped")\n'
                      'else:\n'
                      ' print("before timeout", flush=True)\n'
                      ' time.sleep(10)\n')
    rec = proc.run([sys.executable, script], timeout=0.3)
    time.sleep(1.5)
    assert rec['timed_out'] and 'before timeout' in rec['output']
    assert not sentinel.exists()


def test_partial_rerun_is_not_consistent():
    assert exec_case.rerun_record({'runs': 3, 'rerun_runs_completed': 1,
                                  'rerun_consistent': '1'})['consistent'] is None


def test_mismatch_rejudge_stays_failure(mods, tmp_path, fake, monkeypatch):
    monkeypatch.setenv('FAKE_MODE', 'rerun_mismatch')
    case = run_harness.probe_case(mods, 'cmatinv_batched', 3, 2, 77)
    rec = run_harness.run_one(mods, case, op_abi.get(case['op']),
                             _ctx(tmp_path, fake, 'structural', rerun=3))
    again, code = run_harness.rejudge(rec['evidence']['bundle'], GEN_DIR)
    assert code == 1
    assert again['determinism']['class'] == 'mismatch_confirmed'


def test_loaded_library_mismatch_retained_and_rejudge_fails(mods, tmp_path, fake):
    ctx = _ctx(tmp_path, fake, 'structural')
    ctx['provenance'] = {'artifacts': {'build/libops_solver.so': {'sha256': 'different'}}}
    case = run_harness.probe_case(mods, 'cmatinv_batched', 3, 2, 77)
    rec = run_harness.run_one(mods, case, op_abi.get(case['op']), ctx)
    assert rec['evidence'] and rec['exec']['loaded_lib']['match'] is False
    again, code = run_harness.rejudge(rec['evidence']['bundle'], GEN_DIR)
    assert code == 1 and again['rejudge'] == 'exec_failed'


def test_package_index_gzip_includes_info_and_preserves_baselines(mods, tmp_path):
    base = [_spotrf_case(n=n) for n in (4, 8, 16, 32)]
    info = mods['gen'].derive_info_cases(base, require_s1=False)
    for case in info:
        case.update(ratio_cpu=None, ratio_cpu_status=None)
    canonical = tmp_path / 'canonical_cases.json'
    canonical.write_text(json.dumps({'package_scope': 'full', 'cases': base}))
    (tmp_path / 'cases').mkdir()
    with gzip.open(tmp_path / 'cases/index.json.gz', 'wt') as f:
        json.dump({'ratio_basis': 'A32-f64', 'cases': base + info,
                   'ratio_cpu_mean': {'spotrf': 123}}, f)
    _sign_package(tmp_path)
    picked, mean = run_harness._pick_cases(mods, canonical, None, None, None)
    assert len(picked) == len(base) + len(info)
    assert picked[0]['ratio_cpu'] == 1.0 and picked[0]['ratio_cpu_mean'] == 123
    assert len([c for c in picked if c.get('info_probe')]) == 3
    assert mean == {'spotrf': 123}


def test_missing_index_rejected(mods, tmp_path):
    canonical = tmp_path / 'canonical_cases.json'
    canonical.write_text(json.dumps({'cases': [_spotrf_case()]}))
    with pytest.raises(run_harness.ContractError, match='index'):
        run_harness._pick_cases(mods, canonical, None, None, None)


def test_column_major_rejected(mods, tmp_path, fake):
    ctx = _ctx(tmp_path, fake, 'structural')
    ctx['layout'] = 'column_major'
    case = run_harness.probe_case(mods, 'cmatinv_batched', 3, 2, 77)
    with pytest.raises(run_harness.ContractError, match='column_major'):
        run_harness.run_one(mods, case, op_abi.get(case['op']), ctx)


def test_corrupt_output_retains_raw_bytes(mods, tmp_path, fake, monkeypatch):
    monkeypatch.setenv('FAKE_MODE', 'short_out')
    case = run_harness.probe_case(mods, 'cmatinv_batched', 3, 2, 77)
    rec = run_harness.run_one(mods, case, op_abi.get(case['op']),
                             _ctx(tmp_path, fake, 'structural'))
    bundle = Path(rec['evidence']['bundle'])
    assert rec['exec']['kind'] == 'protocol_error'
    assert (bundle / 'out32.raw.bin').stat().st_size == (2*3*3-1)*8
    assert (bundle / 'info.raw.bin').is_file()
    assert run_harness.rejudge(bundle, GEN_DIR)[1] == 1


def test_pass_removes_case_binary_files(mods, tmp_path, fake):
    case = run_harness.probe_case(mods, 'cmatinv_batched', 3, 2, 77)
    ctx = _ctx(tmp_path, fake, 'structural')
    rec = run_harness.run_one(mods, case, op_abi.get(case['op']), ctx)
    assert rec['evidence'] is None
    assert not list(Path(ctx['work_dir']).rglob('*.bin'))


def test_fresh_evidence_never_reuses_directory(tmp_path):
    import evidence
    first = evidence.fresh_dir(tmp_path / 'bundle')
    (first / 'old').write_text('old evidence')
    second = evidence.fresh_dir(tmp_path / 'bundle')
    assert first != second and not list(second.iterdir())
    assert (first / 'old').read_text() == 'old evidence'


def test_failed_build_writes_provenance_and_log(tmp_path, monkeypatch):
    import build_dut
    from test_harness_build import _fake_repo
    repo = _fake_repo(tmp_path)
    (repo / 'build.sh').write_text('echo failed-command-log; exit 7')
    monkeypatch.setattr(build_dut, 'environment_facts', lambda device: {})
    out = tmp_path / 'provenance.json'
    code = build_dut.main(['--repo', str(repo), '--ops', 'sgetri', '--soc', 'test',
                           '--out', str(out)])
    saved = json.loads(out.read_text())
    assert code == 3 and saved['status'] == 'build_failed'
    assert saved['build']['returncode'] == 7
    assert 'failed-command-log' in saved['build']['output']
    assert saved['source']


def test_tampered_gzip_baseline_is_rejected(mods, tmp_path):
    base = [_spotrf_case()]
    canonical = tmp_path / 'canonical_cases.json'
    canonical.write_text(json.dumps({'cases': base}))
    (tmp_path / 'cases').mkdir()
    path = tmp_path / 'cases/index.json.gz'
    payload = {'ratio_basis': 'A32-f64', 'cases': base}
    with gzip.open(path, 'wt') as f:
        json.dump(payload, f)
    _sign_package(tmp_path)
    payload['cases'][0]['ratio_cpu'] = 999
    with gzip.open(path, 'wt') as f:
        json.dump(payload, f)
    with pytest.raises(run_harness.ContractError, match='指纹'):
        run_harness._pick_cases(mods, canonical, None, None, None)


def test_single_info_constructs_contract_variant(mods):
    base = [_spotrf_case(n=n) for n in (4, 8, 16, 32)]
    case = mods['gen'].derive_info_cases(base, require_s1=False)[0]
    arrays, inputs, smap = run_harness.prepare_case(mods, case, op_abi.get('spotrf'))
    assert 'golden32' not in arrays and smap is None
    assert np.array_equal(inputs['in_a'], arrays['A32'])
    assert case['info_probe'] == 'non_posdef'


def test_rejudge_preserves_explicit_structural_mode(mods, tmp_path, fake):
    case = _spotrf_case(n=4)
    ctx = _ctx(tmp_path, fake, 'structural', keep=[case['case_id']])
    rec = run_harness.run_one(mods, case, op_abi.get('spotrf'), ctx)
    again, code = run_harness.rejudge(rec['evidence']['bundle'], GEN_DIR)
    assert code == 0
    assert again['verdict']['structural'] == 'PASS'
    assert again['verdict']['numeric'] == 'NOT_JUDGED'


def test_interrupt_terminates_and_reaps_process_group(tmp_path, monkeypatch):
    monkeypatch.setattr(proc, 'KILL_GRACE_SECONDS', 0.2)
    script = tmp_path / 'interrupt_tree.py'
    sentinel = tmp_path / 'escaped_interrupt'
    script.write_text('import os, signal, time\n'
                      'if os.fork() == 0:\n'
                      ' signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                      ' time.sleep(1.5)\n'
                      f' open({str(sentinel)!r}, "w").write("escaped")\n'
                      'else: time.sleep(10)\n')
    original = proc.subprocess.Popen
    children = []
    def interrupted_popen(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        wait = child.wait
        first = True
        def interrupt_once(*args, **kwargs):
            nonlocal first
            if first:
                first = False
                time.sleep(0.3)
                raise KeyboardInterrupt()
            return wait(*args, **kwargs)
        child.wait = interrupt_once
        return child
    monkeypatch.setattr(proc.subprocess, 'Popen', interrupted_popen)
    with pytest.raises(KeyboardInterrupt):
        proc.run([sys.executable, script], timeout=10)
    assert children[0].returncode is not None
    time.sleep(1.5)
    assert not sentinel.exists()


def test_prepare_consumes_frozen_sample_map_and_rejects_mismatch():
    from types import SimpleNamespace
    frozen = [{'rep_slot': 0, 'slots': [0, 1]}]
    consumed = []
    gen = SimpleNamespace(validate_case=lambda c: None,
                          build_batched_contents=lambda c: {'A32': np.zeros((1,2,2))},
                          derive_sample_map=lambda seed, batch: frozen.copy(),
                          expand_sampled_rows=lambda contents, mapping, start, end:
                              (consumed.append(mapping) or {'A32': np.zeros((2,2,2))}))
    case = {'case_id':'batch', 'op':'spotrfBatched', 'n':2, 'batch':2,
            'seed':1, 'sample_map':frozen}
    _, _, used = run_harness.prepare_case({'gen':gen}, case, op_abi.get('spotrfBatched'))
    assert used is frozen and consumed[0] is frozen
    case['sample_map'] = [{'rep_slot': 1, 'slots': [0, 1]}]
    with pytest.raises(run_harness.ContractError, match='sample_map'):
        run_harness.prepare_case({'gen':gen}, case, op_abi.get('spotrfBatched'))


def test_harness_rejects_missing_required_frozen_mean(mods, tmp_path):
    case = _spotrf_case()
    canonical = tmp_path / 'canonical_cases.json'
    canonical.write_text(json.dumps({'cases':[case]}))
    (tmp_path / 'cases').mkdir()
    # The per-case mean must not substitute for a missing single-op top-level mean.
    (tmp_path / 'cases/index.json').write_text(json.dumps(
        {'ratio_basis':'A32-f64', 'cases':[case]}))
    _sign_package(tmp_path)
    with pytest.raises(run_harness.ContractError, match='ratio_cpu_mean'):
        run_harness._pick_cases(mods, canonical, None, None, None)
