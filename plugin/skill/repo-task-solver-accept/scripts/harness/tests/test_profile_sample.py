"""Behavioral checks for the profiled sample's data admission."""
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import profile_cmatinv_sample as profile


def make_csv(root, count=30, value="100", name=profile.OP_NAME, device=0):
    for n in range(count):
        folder = root / str(n)
        folder.mkdir(parents=True)
        (folder / "OpBasicInfo_123.csv").write_text(
            "Op Name,Task Duration(us),Device Id,\n"
            f"{name},{value},{device},\n")


def test_samples_and_units(tmp_path):
    make_csv(tmp_path)
    samples = profile.read_samples(tmp_path, 0)
    assert profile.summarize(samples) == {
        "count": 30, "mean_ms": 0.1, "median_ms": 0.1,
        "min_ms": 0.1, "max_ms": 0.1}


@pytest.mark.parametrize("value", ["nan", "inf", "0", "-1"])
def test_invalid_timing(tmp_path, value):
    make_csv(tmp_path, value=value)
    with pytest.raises(ValueError, match="duration"):
        profile.read_samples(tmp_path, 0)


@pytest.mark.parametrize("count", [0, 29, 31])
def test_missing_or_extra_launch(tmp_path, count):
    make_csv(tmp_path, count=count)
    with pytest.raises(ValueError, match="expected 30"):
        profile.read_samples(tmp_path, 0)


def test_duplicate_export(tmp_path):
    make_csv(tmp_path)
    (tmp_path / "0/OpBasicInfo_456.csv").write_text(
        (tmp_path / "0/OpBasicInfo_123.csv").read_text())
    with pytest.raises(ValueError, match="duplicate"):
        profile.read_samples(tmp_path, 0)


@pytest.mark.parametrize("kwargs", [{"name": "other_kernel"}, {"device": 1}])
def test_wrong_attribution(tmp_path, kwargs):
    make_csv(tmp_path, **kwargs)
    with pytest.raises(ValueError, match="unexpected"):
        profile.read_samples(tmp_path, 0)


def test_execution_failure_cannot_be_hidden_by_profiler_success():
    report = {"summary": {"total": 1, "pass": 0}}
    with pytest.raises(ValueError, match="execution"):
        profile.require_execution(report)


@pytest.mark.parametrize("mode", ["application_fail", "no_report", "no_csv"])
def test_zero_profiler_exit_is_not_sufficient(tmp_path, monkeypatch, mode):
    provenance = tmp_path / "build.json"
    provenance.write_text("{}")
    monkeypatch.setenv("ASCEND_HOME_PATH", str(tmp_path))
    monkeypatch.setattr(profile.exec_case, "compile_executor", lambda *a, **kw:
                        {"bin": str(tmp_path / "dummy")})
    def fake_main(args):
        if mode == "no_report":
            return 0
        summary = {"total": 1, "pass": 1, "fail": 0, "error": 0,
                   "det_fail": 0, "det_unknown": 0, "lib_mismatch": 0}
        if mode == "application_fail":
            summary["pass"], summary["error"] = 0, 1
        report = {"summary": summary, "cases": [{"exec": {"kind": "ok"},
                  "determinism": {"runs_completed": 30, "consistent": True}}]}
        Path(args[args.index("--report") + 1]).write_text(json.dumps(report))
        return 0
    monkeypatch.setattr(profile.run_harness, "main", fake_main)
    out = tmp_path / "output"
    assert profile.main(["--repo", str(tmp_path), "--provenance", str(provenance),
                         "--gen-dir", str(tmp_path), "--out", str(out)]) == 2
    result = json.loads((out / "performance.json").read_text())
    assert result["status"] == "ERROR"
    assert result["performance_verdict"] == "NOT_EVALUATED"
    assert "error" in result and "statistics" not in result
