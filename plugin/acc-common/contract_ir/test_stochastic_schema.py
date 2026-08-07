import copy
import json
import os
import unittest

from jsonschema import Draft202012Validator, ValidationError


HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as src:
        return json.load(src)


class StochasticIrSchemaTest(unittest.TestCase):
    def setUp(self):
        schema = _load("contract_ir.schema.v1.json")
        self.validator = Draft202012Validator(schema["$defs"]["stochastic_capability"])
        self.contract = _load(os.path.join(
            "..", "testdata", "stochastic_binary_witness.contract.json"))

    def test_capability_contract_is_schema_valid(self):
        self.validator.validate(self.contract)

    def test_unknown_field_and_bad_oracle_are_rejected(self):
        bad = copy.deepcopy(self.contract)
        bad["operator_name"] = "must-not-dispatch"
        with self.assertRaises(ValidationError):
            self.validator.validate(bad)
        bad = copy.deepcopy(self.contract)
        bad["oracle_precondition"]["callable"] = "builtins.eval"
        with self.assertRaises(ValidationError):
            self.validator.validate(bad)

    def test_acceptance_ir_can_reference_stochastic_capability(self):
        schema = _load("contract_ir.schema.v1.json")
        acceptance = schema["$defs"]["acceptance_predicate"]
        self.assertIn("statistical", acceptance["properties"]["kind"]["enum"])
        self.assertEqual(
            acceptance["properties"]["stochastic_ref"]["const"], "#/stochastic")


if __name__ == "__main__":
    unittest.main()
