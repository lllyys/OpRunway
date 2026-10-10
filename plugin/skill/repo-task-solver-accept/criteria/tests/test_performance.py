"""Performance gate behavior; synthetic 950PR evidence is a unit-test fixture only."""
import copy
import json
from pathlib import Path
import sys
import pytest
import performance as perf

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import accept_run
import expectations as exp
sys.path.insert(0, str(SCRIPTS / "harness"))
import run_performance as runner


def fixture(tmp_path, count=2):
    package = tmp_path / "package"
    package.mkdir()
    cases = [{"case_id": f"case-{i}", "op": "spotrf", "n": 8, "uplo": "L",
              "lda": 8, "seed": 123 + i, "source": "cu",
              "bench_key": {"file": "spotrf/bench_result.json", "entry": i}}
             for i in range(count)]
    baseline = [{"case_id": c["case_id"], "bench_key": c["bench_key"],
                 "matched": True, "avg_ms": 0.35, "min_ms": 0.3} for c in cases]
    (package / "canonical_cases.json").write_text(json.dumps({"cases": cases}))
    (package / "perf_baseline.json").write_text(json.dumps(baseline))
    fp = {name: perf.sha256(package / name) for name in ("canonical_cases.json", "perf_baseline.json")}
    (package / "manifest.json").write_text(json.dumps({"operator": "spotrf", "fingerprint": fp}))
    contract = perf.load_contract(package)
    kernel = {"symbol": "target_symbol", "op_name": "target_kernel", "launches_per_call": 1, "target_only": True}
    records = [{"case_id": c["case_id"], "case": perf.identity(c), "status": "COLLECTED",
                "collector": "msprof op", "timing_scope": "kernel_only", "plan": [kernel],
                "target": "950PR", "device": 0, "soc": "ascend950", "layout": "column_major", "abi": "device",
                "kernels": [{"kernel": kernel, "samples_us": [1000.0] * 30,
                             "execution": {"kind": "ok", "completed": 30, "consistent": True,
                                           "library_match": True, "library_sha256": "a" * 64}}]} for c in cases]
    evidence = {"schema": perf.SCHEMA, "package_fingerprint": fp, "cases": records,
                "build": {"skip_build": False, "build": {"returncode": 0}, "soc": "ascend950",
                          "artifacts": {"libops_solver.so": {"sha256": "a" * 64}}},
                "kernel_map": {"operators": {"spotrf": [copy.deepcopy(kernel)]}}}
    return package, contract, evidence



def write_evidence(path, data):
    for record in data["cases"]:
        for i, kernel in enumerate(record["kernels"]):
            folder = Path(record["case_id"]) / f"kernel-{i}"
            kernel["profile_dir"] = str(folder)
            for j, value in enumerate(kernel["samples_us"]):
                target = path.parent / folder / f"{j:04}" / "OpBasicInfo_123.csv"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("Op Name,Task Duration(us),Device Id\n"
                                  + f"{kernel['kernel']['op_name']},{value},{record['device']}\n")
            _, sources = perf.read_kernel_samples(path.parent / folder, kernel["kernel"], record["device"])
            kernel["csv"] = sources
    path.write_text(json.dumps(data))

def test_exact_boundary_and_over_threshold(tmp_path):
    _, contract, data = fixture(tmp_path)
    assert perf.judge(contract, data)["performance_verdict"] == "PASS"
    data["cases"][1]["kernels"][0]["samples_us"] = [1000.01] * 30
    report = perf.judge(contract, data)
    assert report["performance_verdict"] == "FAIL"
    assert report["cases"][1]["npu_mean_ms"] > report["cases"][1]["limit_ms"]
    assert report["summary"] == {"expected": 2, "PASS": 1, "FAIL": 1, "INSUFFICIENT": 0, "complete": True}


def test_missing_baseline_does_not_reduce_universe(tmp_path):
    _, contract, data = fixture(tmp_path)
    contract["baseline"].pop("case-1")
    report = perf.judge(contract, data)
    assert report["summary"]["expected"] == 2
    assert report["summary"]["INSUFFICIENT"] == 1
    assert report["performance_verdict"] == "INSUFFICIENT"


@pytest.mark.parametrize("value", [None, True, 0, -1, float("nan"), float("inf")])
def test_invalid_gpu_baseline(tmp_path, value):
    _, contract, data = fixture(tmp_path)
    contract["baseline"]["case-0"]["avg_ms"] = value
    assert perf.judge(contract, data)["cases"][0]["status"] == "INSUFFICIENT"


@pytest.mark.parametrize("field,value", [("matched", 1), ("matched", False), ("bench_key", None)])
def test_unmatched_or_wrong_source(tmp_path, field, value):
    _, contract, data = fixture(tmp_path)
    contract["baseline"]["case-0"][field] = value
    assert perf.judge(contract, data)["cases"][0]["status"] == "INSUFFICIENT"


@pytest.mark.parametrize("field,value", [("target", "A3"), ("soc", "ascend910_93"),
    ("layout", "row_major"), ("abi", "host"), ("timing_scope", "api_wall"), ("collector", "msprof")])
def test_wrong_scope_not_formal_pass(tmp_path, field, value):
    _, contract, data = fixture(tmp_path)
    data["cases"][0][field] = value
    assert perf.judge(contract, data)["cases"][0]["status"] == "INSUFFICIENT"


@pytest.mark.parametrize("values", [[1000.0] * 29, [1000.0] * 31, [True] * 30,
                                   [float("nan")] * 30, [0] * 30])
def test_bad_samples(tmp_path, values):
    _, contract, data = fixture(tmp_path)
    data["cases"][0]["kernels"][0]["samples_us"] = values
    assert perf.judge(contract, data)["cases"][0]["status"] == "INSUFFICIENT"


@pytest.mark.parametrize("key,value", [("kind", "op_error"), ("completed", 29),
                                      ("consistent", None), ("library_match", False)])
def test_failed_execution_not_performance_failure(tmp_path, key, value):
    _, contract, data = fixture(tmp_path)
    data["cases"][0]["kernels"][0]["execution"][key] = value
    assert perf.judge(contract, data)["cases"][0]["status"] == "INSUFFICIENT"


def test_multi_kernel_launches_are_summed(tmp_path):
    _, contract, data = fixture(tmp_path, 1)
    case = data["cases"][0]
    first = case["kernels"][0]
    first["samples_us"] = [100.0] * 30
    second = copy.deepcopy(first)
    second["kernel"] = {"symbol": "second", "op_name": "second", "launches_per_call": 2, "target_only": True}
    second["samples_us"] = [200.0] * 60
    case["plan"].append(second["kernel"])
    case["kernels"].append(second)
    data["kernel_map"]["operators"]["spotrf"] = copy.deepcopy(case["plan"])
    row = perf.judge(contract, data)["cases"][0]
    assert row["npu_mean_ms"] == pytest.approx(0.5)
    case["kernels"].pop()
    assert perf.judge(contract, data)["cases"][0]["status"] == "INSUFFICIENT"


def test_fail_and_incomplete_are_both_visible(tmp_path):
    _, contract, data = fixture(tmp_path)
    data["cases"][0]["kernels"][0]["samples_us"] = [2000.0] * 30
    data["cases"].pop()
    report = perf.judge(contract, data)
    assert report["performance_verdict"] == "FAIL"
    assert report["summary"]["INSUFFICIENT"] == 1 and not report["summary"]["complete"]


def test_import_recomputes_instead_of_trusting_pass(tmp_path):
    package, contract, data = fixture(tmp_path)
    data["performance_verdict"] = "PASS"
    data["cases"][0]["kernels"][0]["samples_us"] = [2000.0] * 30
    path = tmp_path / "evidence.json"
    write_evidence(path, data)
    dut = tmp_path / "dut"
    dut.mkdir()
    items = accept_run.perf_items(package, dut, "spotrf", list(contract["baseline"].values()), {}, path)
    formal = next(i for i in items if "正式门禁" in i["item"])
    assert formal["status"] == exp.ST_FAIL
    assert formal["evidence"]["summary"]["FAIL"] == 1


def test_old_perf_dictionary_cannot_pass_formal_gate(tmp_path):
    package, contract, _ = fixture(tmp_path)
    dut = tmp_path / "dut"
    dut.mkdir()
    (dut / "perf.json").write_text('{"case-0":0.00001}')
    items = accept_run.perf_items(package, dut, "spotrf", list(contract["baseline"].values()), {})
    assert next(i for i in items if "正式门禁" in i["item"])["status"] == exp.ST_NO_EVIDENCE


def test_wrong_package_and_duplicate_measurements_rejected(tmp_path):
    _, contract, data = fixture(tmp_path)
    data["package_fingerprint"] = {}
    with pytest.raises(ValueError, match="different package"):
        perf.judge(contract, data)
    data["package_fingerprint"] = contract["fingerprint"]
    data["cases"].append(data["cases"][0])
    with pytest.raises(ValueError, match="duplicate"):
        perf.judge(contract, data)


def test_recipe_mismatch_and_unproven_target_kernels(tmp_path):
    _, contract, data = fixture(tmp_path)
    data["cases"][0]["case"]["n"] = 16
    data["cases"][1]["plan"][0]["target_only"] = False
    assert perf.judge(contract, data)["summary"]["INSUFFICIENT"] == 2


def test_package_fingerprint_and_duplicate_baseline(tmp_path):
    package, _, _ = fixture(tmp_path)
    baseline = json.loads((package / "perf_baseline.json").read_text())
    baseline.append(baseline[0])
    (package / "perf_baseline.json").write_text(json.dumps(baseline))
    with pytest.raises(ValueError, match="fingerprint"):
        perf.load_contract(package)
    manifest = json.loads((package / "manifest.json").read_text())
    manifest["fingerprint"]["perf_baseline.json"] = perf.sha256(package / "perf_baseline.json")
    (package / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="duplicate"):
        perf.load_contract(package)


def test_rejudge_cli_emits_complete_report(tmp_path):
    package, _, data = fixture(tmp_path)
    path = tmp_path / "measurements.json"
    write_evidence(path, data)
    out = tmp_path / "judged"
    assert runner.main(["--package", str(package), "--measurements", str(path), "--out", str(out)]) == 0
    assert json.loads((out / "performance-report.json").read_text())["summary"]["PASS"] == 2
    assert runner.main(["--package", str(package), "--measurements", str(path), "--out", str(out)]) == 2


@pytest.mark.parametrize("value", [None, [], "broken"])
def test_malformed_kernel_and_execution(tmp_path, value):
    _, contract, data = fixture(tmp_path)
    data["cases"][0]["kernels"][0] = value
    data["cases"][1]["kernels"][0]["execution"] = value
    assert perf.judge(contract, data)["summary"]["INSUFFICIENT"] == 2


@pytest.mark.parametrize("field", ["plan", "soc", "library"])
def test_mixed_collection_provenance(tmp_path, field):
    _, contract, data = fixture(tmp_path)
    if field == "plan":
        data["kernel_map"]["operators"]["spotrf"][0]["launches_per_call"] = 2
    elif field == "soc":
        data["build"]["soc"] = "ascend910_93"
    else:
        data["build"]["artifacts"]["libops_solver.so"]["sha256"] = "b" * 64
    assert perf.judge(contract, data)["summary"]["INSUFFICIENT"] == 2


def test_rejudge_uses_raw_csv_and_detects_missing_csv(tmp_path):
    _, contract, data = fixture(tmp_path)
    path = tmp_path / "evidence.json"
    write_evidence(path, data)
    data["cases"][0]["kernels"][0]["samples_us"] = [0.00001] * 30
    path.write_text(json.dumps(data))
    raw = next((tmp_path / "case-0").rglob("OpBasicInfo*.csv"))
    raw.write_text(raw.read_text().replace(",1000.0,", ",100000.0,"))
    assert perf.judge(contract, perf.load_evidence(path))["cases"][0]["status"] == "FAIL"
    raw.unlink()
    assert perf.judge(contract, perf.load_evidence(path))["cases"][0]["status"] == "INSUFFICIENT"


@pytest.mark.parametrize("name", ["manifest.json", "canonical_cases.json"])
def test_bad_top_level_is_contract_error(tmp_path, name):
    package, _, _ = fixture(tmp_path)
    (package / name).write_text("null")
    with pytest.raises(ValueError):
        perf.load_contract(package)


@pytest.mark.parametrize("reference_status", [exp.ST_PENDING, exp.ST_NO_EVIDENCE])
def test_optional_reference_does_not_block_required_performance(reference_status):
    reference = exp.perf_reference_item("spotrf", reference_status, {})
    gate = exp.perf_formal_gate_item("spotrf", {"performance_verdict": "PASS"})
    _, counts = exp.family_conclusion([reference, gate])
    assert counts[exp.ST_PASS] == 1
    assert counts[exp.ST_PENDING] == counts[exp.ST_NO_EVIDENCE] == 0
    _, missing_counts = exp.family_conclusion([reference, exp.perf_formal_gate_item("spotrf")])
    assert missing_counts[exp.ST_NO_EVIDENCE] == 1
