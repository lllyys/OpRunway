"""seal_bundle.py 的交接包发现、配对与封印回归测试。"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
SCRIPT = SCRIPTS / "seal_bundle.py"
sys.path.insert(0, str(SCRIPTS))

from _case_utils import file_sha256  # noqa: E402


EXCLUDED = [
    "evidence/timeline.jsonl",
    "evidence/repro.sh",
    "evidence/bundle.json",
    "evidence/env.json",
    "evidence/env.sh",
]
FACET_PATHS = (
    "yaml",
    "case_json",
    "must_cover",
    "frozen_dir",
    "coverage_report",
    "freeze_report",
    "validate_report",
    "adapter_report",
)


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_seal(work):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "-C", str(work)],
        capture_output=True,
        text=True,
        timeout=30,
    )


def make_bundle(work):
    """造一份最小但完整的 S1 + S2 交接包。"""
    work = Path(work)
    evidence = work / "evidence"
    evidence.mkdir(parents=True)

    (evidence / "constraints.md").write_text("# 约束\n", encoding="utf-8")
    write_json(
        evidence / "interface.json",
        {
            "interface_mode": "aclnn",
            "candidate_symbol": "aclnnMedian",
            "baseline_api": "torch.median",
            "baseline_kind": "torch",
            "task_doc": {"name": "median.md", "sha256": "d" * 64},
        },
    )
    write_json(
        evidence / "env.json",
        {
            "fingerprint": {"atk": "7.3.0"},
            "selected_python": "/opt/python/bin/python3",
            "phase_supported": "仅 Phase A",
        },
    )
    (evidence / "env.sh").write_text("export DEVICE_LIST=''\n", encoding="utf-8")
    write_json(
        evidence / "signature_alignment.json",
        {
            "baseline_adapter": {"required": False},
            "aclnn_adapter": {"required": False},
        },
    )
    write_json(
        evidence / "signature_contract.json",
        {"verdict": "pass", "problems": []},
    )
    (evidence / "timeline.jsonl").write_text("", encoding="utf-8")

    write_json(work / "med_decl.json", {"parameters": {"input": {}}})
    (work / "med_materialize.py").write_text("# materialize\n", encoding="utf-8")
    write_json(work / "must_cover.json", {"combos": [{"dtype": "fp32"}]})
    write_json(
        work / "med_materialized.json",
        {"combos": [{"dtype": "fp32", "shape": [2]}]},
    )
    (work / "med.yaml").write_text("name: torch.median\n", encoding="utf-8")
    (work / "med_constraint.py").write_text("# constraint\n", encoding="utf-8")

    case_json = work / "result" / "med" / "json" / "all_med.json"
    write_json(
        case_json,
        {
            "cases": [
                {
                    "id": "0",
                    "inputs": [
                        {
                            "name": "input",
                            "type": "tensor",
                            "dtype": "fp32",
                            "shape": [2],
                            "range_values": [-1, 1],
                        }
                    ],
                }
            ]
        },
    )
    frozen_input = work / "frozen_med" / "0" / "input.bin"
    frozen_input.parent.mkdir(parents=True)
    frozen_input.write_bytes(b"frozen input")

    pyc = work / "__pycache__" / "x.pyc"
    pyc.parent.mkdir()
    pyc.write_bytes(b"timestamped bytecode")

    case_digest = file_sha256(case_json)
    must_cover_digest = file_sha256(work / "must_cover.json")
    write_json(
        evidence / "coverage.json",
        {
            "case_file_sha256": case_digest,
            "must_cover_sha256": must_cover_digest,
            "must_cover_total": 1,
            "must_cover_hit": 1,
            "missing": [],
        },
    )
    write_json(
        evidence / "validate.json",
        {"case_file_sha256": case_digest, "failures": []},
    )
    write_json(
        evidence / "adapter_binding.json",
        {
            "case_file_sha256": case_digest,
            "verdicts": {},
            "problems": [],
        },
    )
    write_json(
        evidence / "frozen_inputs.json",
        {
            "case_json_sha256": case_digest,
            "frozen_dir": str(work / "frozen_med"),
            "inputs": {"0": {"sha256": file_sha256(frozen_input)}},
            "unmaterialized_ids": [],
            "constant_input_cases": {},
            "constant_input_check": "enforced",
            "baseline_failed_cases": [],
        },
    )


class SealBundleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        make_bundle(self.work)
        self.manifest = self.work / "evidence" / "bundle.json"

    def assert_refused_without_manifest(self, expected_code):
        done = run_seal(self.work)
        self.assertEqual(expected_code, done.returncode, done.stderr)
        self.assertFalse(self.manifest.exists(), "失败不得新写 bundle.json")
        return done

    def test_complete_bundle_is_sealed_with_all_required_metadata(self):
        done = run_seal(self.work)
        self.assertEqual(0, done.returncode, done.stderr)

        manifest = read_json(self.manifest)
        self.assertEqual(EXCLUDED, manifest["excluded"])
        self.assertEqual(["__pycache__"], manifest["ignored_dirs"])
        self.assertEqual("aclnnMedian", manifest["operator"])
        self.assertEqual("median.md", manifest["task_doc"]["name"])
        self.assertEqual("7.3.0", manifest["atk"]["version"])
        self.assertEqual("/opt/python/bin/python3", manifest["atk"]["python"])
        self.assertEqual(1, len(manifest["facets"]))

        facet = manifest["facets"][0]
        self.assertEqual("med", facet["name"])
        for key in FACET_PATHS:
            with self.subTest(path=key):
                self.assertNotIn("\\", facet[key])
                self.assertTrue((self.work / facet[key]).exists(), facet[key])

        expected_files = {}
        for path in self.work.rglob("*"):
            relative = path.relative_to(self.work).as_posix()
            if not path.is_file() or "__pycache__" in path.relative_to(self.work).parts:
                continue
            if relative in EXCLUDED:
                continue
            expected_files[relative] = file_sha256(path)
        self.assertEqual(dict(sorted(expected_files.items())), manifest["files"])
        self.assertEqual(sorted(manifest["files"]), list(manifest["files"]))
        self.assertFalse(any("__pycache__" in key for key in manifest["files"]))

        sealed_at = datetime.fromisoformat(manifest["sealed_at"])
        self.assertIsNotNone(sealed_at.utcoffset())

    def test_repeated_seals_only_change_the_timestamp(self):
        first = run_seal(self.work)
        self.assertEqual(0, first.returncode, first.stderr)
        left = read_json(self.manifest)
        self.manifest.unlink()

        second = run_seal(self.work)
        self.assertEqual(0, second.returncode, second.stderr)
        right = read_json(self.manifest)
        left.pop("sealed_at")
        right.pop("sealed_at")
        self.assertEqual(left, right)

    def test_missing_must_cover_is_listed(self):
        (self.work / "must_cover.json").unlink()
        done = self.assert_refused_without_manifest(2)
        self.assertIn("must_cover.json", done.stderr)

    def test_failed_coverage_blocks_the_seal(self):
        path = self.work / "evidence" / "coverage.json"
        report = read_json(path)
        report["missing"] = [{"dtype": "fp16"}]
        report["must_cover_hit"] = 0
        write_json(path, report)
        done = self.assert_refused_without_manifest(2)
        self.assertIn("覆盖", done.stderr)

    def test_failed_baseline_materialization_blocks_the_seal(self):
        path = self.work / "evidence" / "frozen_inputs.json"
        report = read_json(path)
        report["baseline_failed_cases"] = ["0"]
        write_json(path, report)
        self.assert_refused_without_manifest(2)

    def test_two_coverage_reports_for_one_facet_are_rejected(self):
        shutil.copyfile(
            self.work / "evidence" / "coverage.json",
            self.work / "evidence" / "coverage_copy.json",
        )
        done = self.assert_refused_without_manifest(2)
        self.assertIn("多份", done.stderr)

    def test_yaml_without_its_case_json_is_rejected(self):
        (self.work / "other.yaml").write_text("name: other\n", encoding="utf-8")
        done = self.assert_refused_without_manifest(2)
        self.assertIn("result/other/json/all_other.json", done.stderr)

    def test_missing_task_doc_points_to_derive_interface(self):
        path = self.work / "evidence" / "interface.json"
        payload = read_json(path)
        payload.pop("task_doc")
        write_json(path, payload)
        done = self.assert_refused_without_manifest(2)
        self.assertIn("derive_interface", done.stderr)

    def test_missing_evidence_directory_is_structure_error(self):
        with tempfile.TemporaryDirectory() as empty:
            done = run_seal(empty)
            self.assertEqual(3, done.returncode, done.stderr)
            self.assertFalse(Path(empty, "evidence", "bundle.json").exists())

    def test_malformed_interface_is_structure_error(self):
        (self.work / "evidence" / "interface.json").write_text(
            "{不是 JSON", encoding="utf-8"
        )
        self.assert_refused_without_manifest(3)

    def test_failure_does_not_replace_an_existing_manifest(self):
        self.manifest.write_bytes(b"existing manifest")
        path = self.work / "evidence" / "coverage.json"
        report = read_json(path)
        report["missing"] = [{"dtype": "fp16"}]
        write_json(path, report)

        done = run_seal(self.work)
        self.assertEqual(2, done.returncode, done.stderr)
        self.assertEqual(b"existing manifest", self.manifest.read_bytes())

    def test_help_is_nonempty_and_exits_zero(self):
        done = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertTrue(done.stdout.strip())


if __name__ == "__main__":
    unittest.main()
