"""Standalone verify consumes shipped reference values across runtime environments."""
import gzip
import hashlib
import json
from types import SimpleNamespace
import numpy as np
import pytest
from test_render_info_scope import _load_rendered, _ALL


def _index(tmp_path, op, *, zipped=False, info=False):
    batched = op.endswith('Batched')
    row = {'case_id': 'c1', 'op': op, 'ratio_cpu_status': 'ok',
           'ratio_cpu': [7.0, 9.0] if batched else 7.0}
    if batched:
        row.update(ratio_cpu_mean=8.0, sample_map=[
            {'content_idx': 0, 'rep_slot': 0, 'slots': [0, 2]},
            {'content_idx': 1, 'rep_slot': 1, 'slots': [1]}])
    if info:
        row.update(case_purpose='info', k_expected=[1, 0] if batched else 1,
                   ratio_cpu=None, ratio_cpu_status=None)
    doc = {'ratio_basis': 'A32-f64', 'ratio_cpu_mean': {op: 11.0}, 'cases': [row]}
    (tmp_path / 'cases').mkdir(exist_ok=True)
    path = tmp_path / 'cases' / ('index.json.gz' if zipped else 'index.json')
    data = json.dumps(doc).encode()
    path.write_bytes(gzip.compress(data) if zipped else data)
    return doc, row, path


@pytest.mark.parametrize('op', _ALL)
@pytest.mark.parametrize('zipped', [False, True])
def test_reference_survives_environment_changes(tmp_path, op, zipped):
    mod = _load_rendered(tmp_path, op)
    _, entry, _ = _index(tmp_path, op, zipped=zipped)
    frozen = mod._load_frozen_index(tmp_path, [entry], batched=op.endswith('Batched'))['c1']
    arrays = {'ratio_cpu': 999.0, 'ratio_cpu_status': 'prep_failed', 'ratio_cpu_mean': 999.0}
    if op.endswith('Batched'): arrays['sample_map'] = entry['sample_map']
    mod._apply_frozen(arrays, frozen)
    assert arrays['ratio_cpu'] == entry['ratio_cpu']
    assert arrays['ratio_cpu_status'] == 'ok'
    if mod.USES_MEAN:
        assert arrays['ratio_cpu_mean'] == (8.0 if op.endswith('Batched') else 11.0)


def test_materialize_does_not_compute_local_ratio(tmp_path):
    mod = _load_rendered(tmp_path, 'spotrf')
    _, entry, _ = _index(tmp_path, 'spotrf')
    a = np.eye(2, dtype=np.float32)
    gen = SimpleNamespace(validate_case=lambda e: None,
                          build_case_arrays=lambda e: {'A32': a, 'uplo': 'L'})
    arrays, note = mod._materialize_case(gen, entry, {})
    mod._apply_frozen(arrays, mod._load_frozen_index(tmp_path, [entry])['c1'])
    v = mod.judge(arrays, {'out32': a * 1.000001, 'info': 0, 'status': 'ok'})
    assert note is None
    assert v['residual']['ratio_cpu'] == 7.0
    assert v['residual']['threshold'] == 35.0
    assert v['numeric'] == 'PASS'
    local = dict(arrays, ratio_cpu=0.0, ratio_cpu_mean=0.0)
    assert mod.judge(local, {'out32': a * 1.000001, 'info': 0, 'status': 'ok'})['numeric'] == 'FAIL'


@pytest.mark.parametrize('change', ['missing_file', 'old_basis', 'missing_basis',
    'missing_case', 'missing_ratio', 'missing_status', 'bad_status', 'nan',
    'negative', 'bool', 'string', 'missing_mean', 'bad_mean'])
def test_invalid_reference_is_contract_error(tmp_path, change):
    mod = _load_rendered(tmp_path, 'spotrf')
    doc, entry, path = _index(tmp_path, 'spotrf')
    expected = dict(entry)
    if change == 'missing_file': path.unlink()
    else:
        if change == 'old_basis': doc['ratio_basis'] = 'A64-f64'
        elif change == 'missing_basis': del doc['ratio_basis']
        elif change == 'missing_case': doc['cases'] = []
        elif change == 'missing_ratio': del entry['ratio_cpu']
        elif change == 'missing_status': del entry['ratio_cpu_status']
        elif change == 'bad_status': entry['ratio_cpu_status'] = 'not_computed'
        elif change == 'nan': entry['ratio_cpu'] = float('nan')
        elif change == 'negative': entry['ratio_cpu'] = -1
        elif change == 'bool': entry['ratio_cpu'] = True
        elif change == 'string': entry['ratio_cpu'] = '1'
        elif change == 'missing_mean': del doc['ratio_cpu_mean']
        elif change == 'bad_mean': doc['ratio_cpu_mean']['spotrf'] = []
        path.write_text(json.dumps(doc))
    with pytest.raises(ValueError): mod._load_frozen_index(tmp_path, [expected])


@pytest.mark.parametrize('op', ['spotrf', 'spotrfBatched', 'spotrsBatched'])
def test_info_uses_frozen_metadata_without_mean(tmp_path, op):
    mod = _load_rendered(tmp_path, op)
    doc, entry, path = _index(tmp_path, op, info=True)
    doc.pop('ratio_cpu_mean')
    entry.pop('ratio_cpu_mean', None)
    path.write_text(json.dumps(doc))
    frozen = mod._load_frozen_index(tmp_path, [entry], batched=op.endswith('Batched'))['c1']
    assert frozen['ratio_cpu'] is None
    assert frozen['ratio_cpu_status'] is None
    assert 'ratio_cpu_mean' not in frozen
    if op.endswith('Batched'): assert frozen['sample_map'] == entry['sample_map']


def test_sample_map_mismatch_fails(tmp_path):
    mod = _load_rendered(tmp_path, 'spotrfBatched')
    _, entry, _ = _index(tmp_path, 'spotrfBatched')
    frozen = mod._load_frozen_index(tmp_path, [entry], batched=True)['c1']
    with pytest.raises(ValueError, match='sample_map'):
        mod._apply_frozen({'sample_map': []}, frozen)


def test_accept_gzip_fingerprint_hashes_plaintext(tmp_path):
    import accept_run
    _, _, path = _index(tmp_path, 'spotrf', zipped=True)
    perf = tmp_path / 'perf_baseline.json'
    perf.write_text('{}')
    fp = {'cases/index.json': hashlib.sha256(gzip.decompress(path.read_bytes())).hexdigest(),
          'perf_baseline.json': hashlib.sha256(perf.read_bytes()).hexdigest()}
    blocking, warnings = accept_run.check_fingerprints(tmp_path, {'fingerprint': fp})
    assert blocking == {}
    assert warnings == []


def test_accept_batched_mean_is_case_value(tmp_path, monkeypatch):
    import accept_run
    entry = {'case_id': 'batch-case', 'op': 'spotrfBatched', 'ratio_cpu_mean': 8.0}
    index = {'cases': [entry], 'ratio_cpu_mean': {'spotrfBatched': 999.0}}
    seen = []
    monkeypatch.setattr(accept_run, 'load_case_arrays', lambda *args: ({}, None))
    monkeypatch.setattr(accept_run, 'load_dut_out', lambda *args: ({}, None))
    def judge(card, arrays, dut, jobs):
        seen.append(arrays['ratio_cpu_mean'])
        return {'numeric': 'PASS', 'flags': []}
    monkeypatch.setattr(accept_run.batched_parallel, 'judge_parallel', judge)
    monkeypatch.setattr(accept_run.exp, 'accuracy_item_from_verdict', lambda cid, v: v)
    accept_run.accuracy_items(tmp_path, tmp_path, 'spotrfBatched', index, [], {})
    assert seen == [8.0]


@pytest.mark.parametrize('field', ['ratio_cpu', 'ratio_cpu_status', 'ratio_cpu_mean'])
def test_accept_rejects_missing_frozen_field(tmp_path, field):
    import accept_run
    doc, row, path = _index(tmp_path, 'spotrf')
    if field == 'ratio_cpu_mean': doc.pop(field)
    else: row.pop(field)
    path.write_text(json.dumps(doc))
    (tmp_path / 'manifest.json').write_text(json.dumps({'operator': 'spotrf'}))
    with pytest.raises(SystemExit) as exc:
        accept_run.build_report(tmp_path, tmp_path)
    assert exc.value.code == 2
