"""seal_bundle.py 的交接包发现、配对与封印回归测试。"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from _paths import SKILL_ROOT, add_tests_to_path


SCRIPTS = SKILL_ROOT / "scripts"
SCRIPT = SCRIPTS / "seal_bundle.py"
sys.path.insert(0, str(SCRIPTS))

from _case_utils import file_sha256  # noqa: E402
from _handoff_contract import excluded  # noqa: E402

add_tests_to_path()
from _decl_fixture import make_bundle, read_json, write_json  # noqa: E402


EXCLUDED = list(excluded())
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



def run_seal(work):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "-C", str(work)],
        capture_output=True,
        text=True,
        timeout=30,
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
