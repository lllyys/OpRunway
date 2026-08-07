"""cpp_extension 性能原始采集与最终 evidence 的独立绑定门测试。"""

import copy
import json
import os
import tempfile
import unittest

import cann_version
import perf_evidence_contract as P
import validate_acceptance_state as G


def _write(root, name, value):
    with open(os.path.join(root, name), "w", encoding="utf-8") as dst:
        json.dump(value, dst)


class CppExtensionPerfCollectionGateTest(unittest.TestCase):
    def _fixture(self):
        receipt = {
            "schema_version": cann_version.RECEIPT_SCHEMA_VERSION,
            "artifact": {"path": "cpp_extension/x.so", "sha256": "1" * 64},
            "load": {"namespace": "oprunway_x"},
            "bindings": {"invocation_plan_sha256": "3" * 64},
            "vendor": {
                "library_path": "/opt/vendor/lib.so",
                "library_sha256": "2" * 64,
                "symbols_owned": ["aclnnX"],
                "symbol_identity": {"workspace": "aclnnXGetWorkspaceSize",
                                    "stage2": "aclnnX"},
            },
            "runtime": {"soc": "ascend910_93", "cann_version": "9.0.1",
                        "cann": {"status": "observed", "normalized": "9.0.1"}},
        }
        provenance = {
            "artifact": receipt["artifact"],
            "namespace": receipt["load"]["namespace"],
            "invocation_plan": "cpp_extension_invocation_plan.json",
            "invocation_plan_sha256": "3" * 64,
            "vendor": receipt["vendor"],
        }
        identity = {
            **P.expected_execution_identity(receipt, device_index=0),
            "device_name": "Ascend A3",
        }
        evidence = {
            "runner_form": "cpp_extension",
            "cpp_extension_receipt": receipt,
            "evidence": [{
                "case_id": "c0",
                "perf": {"scope": "kernel_only", "us": 3.5,
                         "execution_identity_sha256": P.canonical_sha(identity)},
            }],
            "perf_collection": {
                "custom_kind": "cpp_extension",
                "custom_provenance": provenance,
                "device": 0,
                "records": [{
                    "case_id": "c0",
                    "custom": {"scope": "kernel_only", "us": 3.5,
                               "execution_identity": identity},
                }],
                "collection_checkpoint": {
                    "complete": True,
                    "planned_case_ids": ["c0"],
                },
            },
        }
        caseset = {
            "cases": [{"id": "c0", "dims": ["功能", "精度", "性能"]}],
        }
        return caseset, evidence

    def test_accepts_exact_collection_binding(self):
        caseset, evidence = self._fixture()
        with tempfile.TemporaryDirectory() as root:
            _write(root, "caseset.json", caseset)
            _write(root, "evidence.json", evidence)
            errors = []
            G._gate_cpp_extension_perf_collection(root, errors)
        self.assertEqual([], errors)

    def test_rejects_custom_latency_drift(self):
        caseset, evidence = self._fixture()
        bad = copy.deepcopy(evidence)
        bad["evidence"][0]["perf"]["us"] = 9.0
        with tempfile.TemporaryDirectory() as root:
            _write(root, "caseset.json", caseset)
            _write(root, "evidence.json", bad)
            errors = []
            G._gate_cpp_extension_perf_collection(root, errors)
        self.assertTrue(any("perf.us" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
