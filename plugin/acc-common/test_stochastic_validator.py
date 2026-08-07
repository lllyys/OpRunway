import copy
import tempfile
import unittest
from unittest import mock

import gen_cases
import stochastic_collector as C
import stochastic_contract as S
import validator
from test_gen_cases_stochastic import _spec
from test_stochastic_collector import _device, _sequences


def _bundle():
    spec = _spec()
    with tempfile.TemporaryDirectory() as work, mock.patch.object(
            gen_cases, "load_golden", side_effect=AssertionError("不得加载 golden")):
        caseset = gen_cases.gen_cases(spec, work)
    seq = _sequences()
    pre, formal = C.collect(
        spec["stochastic"], caseset, seq,
        {"interior_0_oracle_precondition": seq["interior_0_oracle_precondition"]},
        _device())
    evaluation = S.evaluate_formal_evidence(spec["stochastic"], pre, formal)
    rows = [{
        "case_id": case["id"], "status": "ok",
        "stochastic": copy.deepcopy(case["stochastic"]),
        "precision": {
            "compare": "stochastic",
            "out_shape": case["expected"]["out_shape"],
            "out_dtype": case["expected"]["compare_dtype"],
        },
    } for case in caseset["cases"]]
    evidence = {
        "op": spec["op"], "evidence": rows,
        "stochastic_formal_evidence": formal,
        "stochastic_evaluation": evaluation,
    }
    return spec, caseset, evidence


class StochasticValidatorTest(unittest.TestCase):
    def test_satisfied_formal_statistics_pass(self):
        spec, caseset, evidence = _bundle()
        verdict = validator.validate(spec, caseset, evidence)
        self.assertEqual(verdict["overall"]["verdict"], "pass")
        self.assertTrue(all(row["精度"] == "pass" for row in verdict["per_case"]))

    def test_only_formal_failed_becomes_precision_fail(self):
        spec, caseset, evidence = _bundle()
        evidence["stochastic_evaluation"] = {
            **evidence["stochastic_evaluation"], "status": "failed", "satisfied": False}
        verdict = validator.validate(spec, caseset, evidence)
        self.assertEqual(verdict["overall"]["verdict"], "fail")
        self.assertTrue(all(row["精度"] == "fail" for row in verdict["per_case"]))

    def test_missing_formal_and_role_drift_fail_closed_without_precision_fail(self):
        spec, caseset, evidence = _bundle()
        evidence.pop("stochastic_formal_evidence")
        evidence.pop("stochastic_evaluation")
        evidence["evidence"][0]["stochastic"]["role"] = "drift"
        verdict = validator.validate(spec, caseset, evidence)
        self.assertEqual(verdict["overall"]["verdict"], "fail")
        self.assertTrue(verdict["overall"]["counts"]["contract_problems"])
        self.assertTrue(all(row["精度"] != "fail" for row in verdict["per_case"]))


if __name__ == "__main__":
    unittest.main()
