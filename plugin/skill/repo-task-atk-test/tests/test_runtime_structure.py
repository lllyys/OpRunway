import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import validate_cases


class RuntimeStructureTest(unittest.TestCase):
    def test_flat_inputs_pass(self):
        cases = [{
            "id": "0",
            "inputs": [
                {"name": "input", "type": "tensor", "dtype": "fp32"},
                {"name": "dims", "type": "attr", "dtype": "int", "range_values": [0, 1]},
            ],
        }]
        failures, notes = [], []

        validate_cases.check_runtime_input_structure(cases, failures, notes)

        self.assertEqual([], failures)
        self.assertTrue(any("P12 通过" in note for note in notes))

    def test_valid_attr_tuple_group_passes(self):
        cases = [{
            "id": "0",
            "inputs": [[
                {"name": "dims", "type": "attr_tuple", "dtype": "int", "range_values": 0},
                {"name": "dims", "type": "attr_tuple", "dtype": "int", "range_values": 1},
            ]],
        }]
        failures, notes = [], []

        validate_cases.check_runtime_input_structure(cases, failures, notes)

        self.assertEqual([], failures)
        self.assertTrue(any("P12 通过" in note for note in notes))

    def test_group_members_cannot_use_flat_attr_type(self):
        cases = [{
            "id": "0",
            "inputs": [[
                {"name": "dims", "type": "attr", "dtype": "int", "range_values": 0},
                {"name": "dims", "type": "attr", "dtype": "int", "range_values": 1},
            ]],
        }]
        failures, notes = [], []

        validate_cases.check_runtime_input_structure(cases, failures, notes)

        self.assertEqual([], notes)
        self.assertIn("group_type_mismatch", failures[0])

    def test_null_attr_fails(self):
        cases = [{
            "id": "7",
            "inputs": [
                {"name": "input", "type": "tensor", "dtype": "fp32"},
                {"name": "dims", "type": "attr", "dtype": "int", "range_values": None},
            ],
        }]
        failures, notes = [], []

        validate_cases.check_runtime_input_structure(cases, failures, notes)

        self.assertEqual([], notes)
        self.assertEqual(1, len(failures))
        self.assertIn("json_null_range_values", failures[0])

    def test_default_token_flat_attr_passes(self):
        cases = [{
            "id": "8",
            "inputs": [{
                "name": "dims", "type": "attr", "dtype": "int",
                "range_values": "default",
            }],
        }]
        failures, notes = [], []

        validate_cases.check_runtime_input_structure(cases, failures, notes)

        self.assertEqual([], failures)
        self.assertTrue(any("P12 通过" in note for note in notes))

    def test_empty_group_fails(self):
        failures, notes = [], []
        validate_cases.check_runtime_input_structure(
            [{"id": "9", "inputs": [[]]}], failures, notes)
        self.assertEqual([], notes)
        self.assertIn("empty_group", failures[0])

    def test_mixed_attr_dtypes_fail_for_pyaclnn(self):
        cases = [{
            "id": "10",
            "inputs": [[
                {"name": "dims", "type": "attr_tuple", "dtype": "int", "range_values": 0},
                {"name": "dims", "type": "attr_tuple", "dtype": "bool", "range_values": True},
            ]],
        }]
        failures, notes = [], []

        validate_cases.check_runtime_input_structure(cases, failures, notes)

        self.assertEqual([], notes)
        self.assertIn("heterogeneous_attr_dtypes", failures[0])


class ValidateReportDigestTest(unittest.TestCase):
    def test_report_binds_to_the_exact_case_file_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design = root / "med.yaml"
            design.write_text("generate: med_constraint\n", encoding="utf-8")
            plugin = root / "med_constraint.py"
            plugin.write_text("# --skip-static 不导入该文件\n", encoding="utf-8")
            must_cover = root / "med_materialized.json"
            must_cover.write_text(
                json.dumps(
                    {
                        "dims": {"dtype": ["fp32"]},
                        "axes": ["dtype"],
                        "extract": {"dtype": {"from": "input_dtype", "index": 0}},
                        "combos": [{"dtype": "fp32"}],
                    }
                ),
                encoding="utf-8",
            )
            cases = root / "all_med.json"
            cases.write_text(
                json.dumps(
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
                    }
                ),
                encoding="utf-8",
            )
            report = root / "validate.json"

            done = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "validate_cases.py"),
                    "-y",
                    str(design),
                    "-p",
                    str(plugin),
                    "-m",
                    str(must_cover),
                    "-j",
                    str(cases),
                    "-o",
                    str(report),
                    "--skip-static",
                    "--backend",
                    "torch",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(0, done.returncode, done.stderr)
            payload = json.loads(report.read_text(encoding="utf-8"))
            expected = hashlib.sha256(cases.read_bytes()).hexdigest()
            self.assertEqual(expected, payload["case_file_sha256"])


if __name__ == "__main__":
    unittest.main()
