import copy
import hashlib
import json
import os
import tempfile
import unittest
from unittest import mock

import cpp_extension_adapter as A
import gen_cases
import stochastic_collector as C
import stochastic_contract as S
from test_gen_cases_stochastic import _spec
from test_stochastic_collector import _device, _sequences


def _dump(root, name, value):
    path = os.path.join(root, name)
    with open(path, "w", encoding="utf-8") as out:
        json.dump(value, out, ensure_ascii=False, indent=2)
        out.write("\n")
    with open(path, "rb") as src:
        return {"path": name, "sha256": hashlib.sha256(src.read()).hexdigest()}


def _fixture(root):
    spec = _spec()
    with mock.patch.object(gen_cases, "load_golden", side_effect=AssertionError):
        caseset = gen_cases.gen_cases(spec, root)
    seq = _sequences()
    pre, formal = C.collect(
        spec["stochastic"], caseset, seq,
        {"interior_0_oracle_precondition": seq["interior_0_oracle_precondition"]},
        _device())
    pre_ref = _dump(root, "stochastic_precondition.json", pre)
    formal_ref = _dump(root, "stochastic_formal_evidence.json", formal)
    manifest = {
        "schema": "oprunway.stochastic_reference_execution", "schema_version": 1,
        "status": "complete", "device": _device(),
        "reference_method": "same_device_npu",
        "reference_callable": "torch.Tensor.binary_sample_",
        "custom_opp_path_present": False, "dut_vendor_env_present": False,
        "records": [],
    }
    manifest_ref = _dump(root, "stochastic_reference_execution.json", manifest)
    contract = S.normalize_contract(spec["stochastic"])
    plan = S.build_case_plan(contract)
    collection = {
        "schema": "oprunway.cpp_extension_stochastic_collection", "schema_version": 1,
        "contract_sha256": S.canonical_sha256(contract),
        "plan_sha256": S.canonical_sha256(plan), "status": "complete",
        "device": _device(),
        "reference_execution": {
            "runner": "isolated_subprocess_without_dut_vendor_env",
            "manifest_path": manifest_ref["path"],
            "manifest_sha256": manifest_ref["sha256"],
        },
        "precondition": pre_ref, "formal_evidence": formal_ref,
    }
    return caseset, {"stochastic_collection": collection}


class StochasticAdapterTest(unittest.TestCase):
    def test_complete_collection_recomputed(self):
        with tempfile.TemporaryDirectory() as root:
            caseset, receipt = _fixture(root)
            result = A.validate_stochastic_collection(root, caseset, receipt)
            self.assertEqual(result["evaluation"]["status"], "satisfied")

    def test_digest_device_and_formal_mutations_rejected(self):
        for mutate in (
                lambda r: r["stochastic_collection"].update(plan_sha256="f" * 64),
                lambda r: r["stochastic_collection"]["device"].update(index=1),
                lambda r: r["stochastic_collection"]["formal_evidence"].update(sha256="f" * 64)):
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as root:
                caseset, receipt = _fixture(root)
                mutate(receipt)
                with self.assertRaises(A.CppExtensionAdapterError):
                    A.validate_stochastic_collection(root, caseset, receipt)


if __name__ == "__main__":
    unittest.main()
