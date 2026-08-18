import sys
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


if __name__ == "__main__":
    unittest.main()
