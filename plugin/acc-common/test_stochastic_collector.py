import copy
import tempfile
import unittest
from unittest import mock

import gen_cases as G
import stochastic_collector as C
import stochastic_contract as S
from test_gen_cases_stochastic import _spec


def _device():
    return {
        "type": "npu", "index": 0, "soc": "witness-soc",
        "runtime_fingerprint": "a" * 64,
    }


def _sequences():
    n = 4096
    base = bytes([0, 1]) * (n // 2)
    seed = bytes([0, 0, 1, 1]) * (n // 4)
    offset = bytes([0, 1, 1, 0]) * (n // 4)
    return {
        "boundary_0": bytes(n),
        "boundary_1": bytes([1]) * n,
        "interior_0_oracle_precondition": base,
        "interior_0_repeat_0": base,
        "interior_0_repeat_1": base,
        "interior_0_alternate_seed": seed,
        "interior_0_alternate_offset": offset,
    }


def _caseset():
    with tempfile.TemporaryDirectory() as work, mock.patch.object(
            G, "load_golden", side_effect=AssertionError("不得加载 golden")):
        return G.gen_cases(_spec(), work)


class StochasticCollectorTest(unittest.TestCase):
    def test_precondition_and_formal_evidence_are_separate_and_valid(self):
        contract = _spec()["stochastic"]
        dut = _sequences()
        reference = {"interior_0_oracle_precondition": dut["interior_0_oracle_precondition"]}
        receipt, formal = C.collect(contract, _caseset(), dut, reference, _device())
        self.assertEqual(receipt["status"], "passed")
        self.assertEqual(receipt["evidence_grade"], "precondition")
        self.assertFalse(receipt["usable_for_verdict"])
        self.assertIsNotNone(formal)
        result = S.evaluate_formal_evidence(contract, receipt, formal)
        self.assertEqual(result["status"], "satisfied")

    def test_exact_mismatch_physically_withholds_formal_evidence(self):
        contract = _spec()["stochastic"]
        dut = _sequences()
        reference = {"interior_0_oracle_precondition": bytes(4096)}
        receipt, formal = C.collect(contract, _caseset(), dut, reference, _device())
        self.assertEqual(receipt["status"], "failed")
        self.assertIsNone(formal)
        self.assertEqual(S.precondition_gate(contract, receipt)["status"], "blocked")

    def test_ledger_role_value_and_sequence_mutations_are_rejected(self):
        contract = _spec()["stochastic"]
        dut = _sequences()
        reference = {"interior_0_oracle_precondition": dut["interior_0_oracle_precondition"]}
        mutations = []
        cs = _caseset(); cs["stochastic_ledger"]["plan_sha256"] = "f" * 64
        mutations.append((cs, dut, reference))
        cs = _caseset(); cs["cases"][0]["stochastic"]["role"] = "made_up"
        mutations.append((cs, dut, reference))
        cs = _caseset(); cs["cases"][0]["attrs"]["generator_seed"] += 1
        mutations.append((cs, dut, reference))
        bad_dut = copy.deepcopy(dut); bad_dut.pop("boundary_0")
        mutations.append((_caseset(), bad_dut, reference))
        mutations.append((_caseset(), dut, {}))
        for caseset, got_dut, got_reference in mutations:
            with self.subTest(caseset=caseset), self.assertRaises(C.StochasticCollectorError):
                C.collect(contract, caseset, got_dut, got_reference, _device())


if __name__ == "__main__":
    unittest.main()
