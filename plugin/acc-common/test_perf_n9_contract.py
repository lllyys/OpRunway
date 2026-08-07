import copy
import unittest

import perf_evidence_contract as C
from aclnn_runtime import perf_msprof as P


def _custom():
    samples = [1.0, 2.0, 3.0, 4.0]
    return {
        "behavior": "npu", "scope": "kernel_only", "execution_path": "device_kernel",
        "us": 2.5,
        "collection": {"warmup": 5, "repeat": 4, "timing_scope": "kernel_only",
                       "kernel_accounting": "median_x_launches"},
        "breakdown": [{
            "kernel_name": "k", "kernel_type": "AI_CORE", "execution_path": "device_kernel",
            "launches_per_invocation": 1, "repeat": 4, "sample_count": 4,
            "discarded_prefix_count": 0, "samples_us": samples,
            "median_launch_us": 2.5, "invocation_us": 2.5,
        }],
    }


def _identity():
    return {
        "schema": C.EXECUTION_IDENTITY_SCHEMA, "schema_version": 1,
        "device_index": 2, "device_name": "Ascend", "soc": "ascend910_93",
        "cann_version": "9.0.1", "cann_observation_sha256": "1" * 64,
        "dut_library_sha256": "2" * 64, "dut_symbol_identity_sha256": "3" * 64,
    }


def _auth(ground="gpu_comparison"):
    return {"taskdoc_requirement": ground, "taskdoc_snapshot_sha256": "a" * 64,
            "cite": "task_doc.snapshot.md:1", "quote": "性能"}


def _gap(requirement_type="gpu_comparison"):
    return {"kind": C.PERFORMANCE_GAP_KIND, "dimension": "performance",
            "status": "unvalidated", "requirement_type": requirement_type,
            "cite": "task_doc.snapshot.md:8", "quote": "与 GPU 比较",
            "taskdoc_snapshot_sha256": "a" * 64, "reason": "本轮只采 NPU msprof"}


class SamplingReceiptTest(unittest.TestCase):
    def test_samples_recompute_exact_measurement(self):
        receipt = C.build_sampling_receipt(
            _custom(), case_id="case-0", warmup=5, repeat=4)
        self.assertEqual(2.5, receipt["payload"]["npu_us"])
        self.assertEqual(C.canonical_sha(receipt["payload"]), receipt["sha256"])

    def test_sample_median_count_and_total_mutations_fail(self):
        for mutate in (
                lambda x: x["breakdown"][0]["samples_us"].append(9.0),
                lambda x: x["breakdown"][0].__setitem__("median_launch_us", 9.0),
                lambda x: x.__setitem__("us", 9.0)):
            bad = copy.deepcopy(_custom())
            mutate(bad)
            with self.assertRaises(C.PerfEvidenceContractError):
                C.build_sampling_receipt(
                    bad, case_id="case-0", warmup=5, repeat=4)

        receipt = C.build_sampling_receipt(
            _custom(), case_id="case-0", warmup=5, repeat=4)
        bad_receipt = copy.deepcopy(receipt)
        bad_receipt["payload"]["npu_us"] = 99.0
        with self.assertRaises(C.PerfEvidenceContractError):
            C.validate_sampling_receipt(
                _custom(), bad_receipt, case_id="case-0", warmup=5, repeat=4)

    def test_sampling_config_rejects_implicit_and_hollow_values(self):
        for warmup, repeat in ((True, 20), ("5", 20), (0, 20), (5, 1), (5, -2)):
            with self.assertRaises(C.PerfEvidenceContractError):
                C.validate_sampling_config(warmup, repeat)

    def test_live_aggregation_preserves_samples_and_measure_only_receipt(self):
        rows = [{"name": "k", "type": "AI_CORE", "duration_us": value}
                for value in (1.0, 2.0, 3.0, 4.0)]
        breakdown, error = P.repeated_breakdown(rows, repeat=4)
        self.assertIsNone(error)
        custom = _custom()
        custom["breakdown"] = breakdown
        custom["collection"] = P.collection_config(
            collector=P.COLLECTOR_MSPROF_CLI, warmup=5, repeat=4)
        record = P.build_measure_only_record("case-0", custom)
        mapped = P.build_custom_perf_map([record])
        self.assertEqual(record["sampling_receipt"]["payload"]["npu_us"], 2.5)
        self.assertEqual(mapped["case-0"]["sampling_receipt_sha256"],
                         record["sampling_receipt"]["sha256"])


class ExecutionIdentityTest(unittest.TestCase):
    def test_expected_identity_is_derived_only_from_precision_receipt(self):
        receipt = {
            "runtime": {"soc": "ascend910_93", "cann_version": "9.0.1",
                        "cann": {"status": "observed", "normalized": "9.0.1"}},
            "vendor": {"library_sha256": "2" * 64,
                       "symbol_identity": {"workspace": "x", "executor": "y"}},
        }
        expected = C.expected_execution_identity(receipt, device_index=2)
        self.assertEqual(expected["device_index"], 2)
        self.assertEqual(expected["dut_library_sha256"], "2" * 64)
        self.assertEqual(expected["cann_observation_sha256"],
                         C.canonical_sha(receipt["runtime"]["cann"]))

    def test_exact_identity_is_bound(self):
        actual = _identity()
        expected = {key: value for key, value in actual.items() if key != "device_name"}
        self.assertEqual(actual, C.validate_execution_identity(actual, expected=expected))

    def test_device_cann_soc_and_dut_mutations_fail(self):
        actual = _identity()
        expected = {key: value for key, value in actual.items() if key != "device_name"}
        for key, value in (("device_index", 3), ("soc", "other"), ("cann_version", "8.0"),
                           ("dut_library_sha256", "4" * 64)):
            bad = copy.deepcopy(actual)
            bad[key] = value
            with self.assertRaises(C.PerfEvidenceContractError):
                C.validate_execution_identity(bad, expected=expected)

    def test_unknown_actual_hardware_is_not_evidence(self):
        bad = _identity()
        bad["device_name"] = "unknown"
        with self.assertRaises(C.PerfEvidenceContractError):
            C.validate_execution_identity(bad)


class RequirementGapTest(unittest.TestCase):
    def test_gpu_authorization_requires_unvalidated_performance_gap(self):
        with self.assertRaises(C.PerfEvidenceContractError):
            C.validate_measure_only_requirement_gaps({"task_pr_gaps": []}, _auth())
        self.assertEqual([_gap()], C.validate_measure_only_requirement_gaps(
            {"task_pr_gaps": [_gap()]}, _auth()))

    def test_resource_gap_neither_satisfies_nor_enters_verdict(self):
        resource = {"kind": "resource_requirement", "dimension": "resource"}
        with self.assertRaisesRegex(C.PerfEvidenceContractError, "资源"):
            C.validate_measure_only_requirement_gaps(
                {"task_pr_gaps": [resource, _gap()]}, _auth())

    def test_gap_snapshot_must_match_authorization(self):
        bad = _gap()
        bad["taskdoc_snapshot_sha256"] = "b" * 64
        with self.assertRaises(C.PerfEvidenceContractError):
            C.validate_measure_only_requirement_gaps({"task_pr_gaps": [bad]}, _auth())


if __name__ == "__main__":
    unittest.main()
