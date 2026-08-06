#!/usr/bin/env python3
"""cpp_extension adapter 的纯确定性契约测试；不 build、不加载 torch/NPU。"""

import copy
import json
import os
import tempfile
import unittest
from unittest import mock

import cpp_extension_adapter as A
import cpp_extension_codegen as C


def _spec():
    return {
        "op": "Witness",
        "runner_form": "cpp_extension",
        "params": [
            {"name": "self", "io": "in", "dtype": ["float32"]},
            {"name": "dim", "io": "attr", "dtype": ["int64"], "default": 0},
            {"name": "valuesOut", "io": "out", "dtype": ["<from_input>"]},
            {"name": "indicesOut", "io": "out", "dtype": ["int32"]},
        ],
        "call_variants": [
            {"symbol": "Witness", "active_attrs": [],
             "active_outputs": ["valuesOut"]},
            {"symbol": "WitnessDim", "active_attrs": ["dim"],
             "active_outputs": ["valuesOut", "indicesOut"]},
        ],
        "perf": {
            "baseline": "torch_npu",
            "warmup": 5,
            "repeat": 20,
            "torch_baseline": {
                "api": "torch.witness",
                "positional": ["self"],
                "keyword": {},
            },
        },
    }


def _caseset():
    return {
        "op": "Witness",
        "cases": [
            {
                "id": "c0",
                "aclnn_call": {
                    "symbol": "Witness",
                    "slots": [
                        {"role": "in", "name": "self", "input_idx": 0},
                        {"role": "out", "name": "valuesOut", "output_idx": 0},
                        {"role": "out_null", "name": "indicesOut"},
                    ],
                },
            },
            {
                "id": "c1",
                "aclnn_call": {
                    "symbol": "WitnessDim",
                    "slots": [
                        {"role": "in", "name": "self", "input_idx": 0},
                        {"role": "attr", "name": "dim", "ctype": "int64", "value": 0},
                        {"role": "out", "name": "valuesOut", "output_idx": 0},
                        {"role": "out", "name": "indicesOut", "output_idx": 1},
                    ],
                },
            },
        ],
    }


class CppExtensionAdapterContractTest(unittest.TestCase):
    def test_plan_binds_case_symbols_without_operator_dispatch(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = C.generate(_spec(), td)
        plan = A.build_invocation_plan(_caseset(), manifest)
        self.assertEqual(
            [row["entrypoint"] for row in plan["cases"]],
            ["invoke_v0", "invoke_v1"])
        self.assertEqual(plan["namespace"], manifest["namespace"])

    def test_same_symbol_variants_bind_by_active_slot_contract(self):
        spec = _spec()
        spec["call_variants"][1]["symbol"] = "Witness"
        caseset = _caseset()
        caseset["cases"][1]["aclnn_call"]["symbol"] = "Witness"
        with tempfile.TemporaryDirectory() as td:
            manifest = C.generate(spec, td)
        plan = A.build_invocation_plan(caseset, manifest)
        self.assertEqual(
            [row["entrypoint"] for row in plan["cases"]],
            ["invoke_v0", "invoke_v1"])

    def test_unknown_symbol_fails_closed(self):
        bad = copy.deepcopy(_caseset())
        bad["cases"][0]["aclnn_call"]["symbol"] = "Other"
        with tempfile.TemporaryDirectory() as td:
            manifest = C.generate(_spec(), td)
        with self.assertRaisesRegex(A.CppExtensionAdapterError, "未绑定"):
            A.build_invocation_plan(bad, manifest)

    def test_output_arity_mismatch_fails_closed(self):
        bad = copy.deepcopy(_caseset())
        bad["cases"][1]["aclnn_call"]["slots"][-1]["role"] = "out_null"
        with tempfile.TemporaryDirectory() as td:
            manifest = C.generate(_spec(), td)
        with self.assertRaisesRegex(A.CppExtensionAdapterError, "active outputs"):
            A.build_invocation_plan(bad, manifest)

    def test_prepare_requires_distinct_form(self):
        bad = _spec()
        bad["runner_form"] = "aclnn_py"
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(A.CppExtensionAdapterError, "cpp_extension"):
                A.prepare(bad, _caseset(), td)

    def test_prepare_snapshots_exact_caseset_for_remote_driver(self):
        with tempfile.TemporaryDirectory() as td:
            A.prepare(_spec(), _caseset(), td)
            path = os.path.join(td, "cpp_extension_caseset.json")
            with open(path, encoding="utf-8") as src:
                snapshot = json.load(src)
        self.assertEqual(snapshot, _caseset())

    def test_perf_plan_is_written_only_after_accuracy_filter(self):
        spec = _spec()
        spec["perf"] = {
            "baseline": "torch_npu",
            "torch_baseline": {
                "api": "torch.witness",
                "positional": ["self"],
                "keyword": {},
            },
            "warmup": 3,
            "repeat": 7,
        }
        caseset = _caseset()
        caseset["cases"][0]["dims"] = ["功能", "精度", "性能"]
        caseset["cases"][1]["dims"] = ["功能", "精度", "性能"]
        receipt = {
            "artifact": {"path": "cpp_extension/x.so", "sha256": "1" * 64},
            "load": {"namespace": "oprunway_test"},
            "bindings": {"invocation_plan_sha256": "3" * 64},
            "vendor": {
                "library_path": "/opt/vendor/lib.so",
                "library_sha256": "2" * 64,
                "symbols_owned": ["aclnnWitness"],
            },
        }
        with tempfile.TemporaryDirectory() as td:
            A.prepare(spec, caseset, td)
            with mock.patch(
                    "aclnn_runtime.perf_msprof.accuracy_pass_ids",
                    return_value={"c0", "c1"}), mock.patch(
                    "aclnn_runtime.perf_msprof.select_perf_cases",
                    return_value=(["c1"], [{"case_id": "c0",
                                           "reason": "skipped_accuracy_failed"}])), \
                    mock.patch.dict(
                        os.environ, {"OPRUNWAY_CPP_EXTENSION_DEVICE": "3"}):
                plan, skipped = A._write_perf_plan(
                    caseset, td, [{"case_id": "c1"}], receipt)
            with open(os.path.join(td, "cpp_extension_perf_plan.json"),
                      encoding="utf-8") as src:
                on_disk = json.load(src)
        self.assertEqual(plan, on_disk)
        self.assertEqual(plan["custom_kind"], "cpp_extension")
        self.assertEqual(plan["device"], 3)
        self.assertEqual(plan["cases"], ["c1"])
        self.assertEqual(skipped[0]["case_id"], "c0")

    def test_partial_accuracy_never_starts_perf_plan(self):
        spec = _spec()
        caseset = _caseset()
        for case in caseset["cases"]:
            case["dims"] = ["功能", "精度", "性能"]
        with tempfile.TemporaryDirectory() as td:
            A.prepare(spec, caseset, td)
            with mock.patch(
                    "aclnn_runtime.perf_msprof.accuracy_pass_ids",
                    return_value={"c0"}), mock.patch(
                    "aclnn_runtime.perf_msprof.select_perf_cases") as select:
                plan, skipped = A._write_perf_plan(
                    caseset, td, [{"case_id": "c0"}],
                    {"artifact": {}, "load": {}, "vendor": {}})
        self.assertIsNone(plan)
        self.assertEqual(
            skipped,
            [{"case_id": "c1", "reason": "skipped_precision_overall_gate"}])
        select.assert_not_called()

    def test_perf_plan_never_guesses_device(self):
        spec = _spec()
        spec["perf"] = {"baseline": "torch_npu"}
        with tempfile.TemporaryDirectory() as td:
            A.prepare(spec, _caseset(), td)
            with mock.patch(
                    "aclnn_runtime.perf_msprof.accuracy_pass_ids",
                    return_value={"c0", "c1"}), mock.patch(
                    "aclnn_runtime.perf_msprof.select_perf_cases",
                    return_value=(["c0"], [])), mock.patch.dict(
                        os.environ, {}, clear=True):
                with self.assertRaisesRegex(
                        A.CppExtensionAdapterError, "不猜 device"):
                    A._write_perf_plan(
                        _caseset(), td, [{"case_id": "c0"}],
                        {"artifact": {}, "load": {}, "vendor": {}})

    def test_perf_collection_must_be_complete_and_provenance_bound(self):
        cpp = {"artifact": {"path": "x.so", "sha256": "1" * 64}}
        plan = {
            "baseline": "torch_npu",
            "cases": ["c0", "c1"],
            "cpp_extension": cpp,
        }
        document = {
            "custom_kind": "cpp_extension",
            "baseline_source": "torch_npu",
            "custom_provenance": cpp,
            "records": [{"case_id": "c0"}, {"case_id": "c1"}],
            "collection_checkpoint": {
                "complete": True,
                "planned_case_ids": ["c0", "c1"],
            },
        }
        A._validate_perf_collection(plan, document)
        bad = copy.deepcopy(document)
        bad["collection_checkpoint"]["complete"] = False
        with self.assertRaisesRegex(
                A.CppExtensionAdapterError, "非完整本轮"):
            A._validate_perf_collection(plan, bad)

    def test_real_mode_fails_before_driver_without_explicit_gate(self):
        with self.assertRaisesRegex(A.CppExtensionAdapterError, "真机路径未启用"):
            A.run_cpp_extension(_caseset(), "/tmp/not-used")


def _measure_only_spec():
    """§5.10 只测不比：spec.perf 里不得有任何对照物/阈值字段，须带任务书授权。"""
    spec = _spec()
    spec["perf"] = {
        "mode": "measure_only",
        "warmup": 3,
        "repeat": 7,
        "measure_only_authorization": {
            "taskdoc_requirement": "gpu_comparison",
            "cite": "任务书 §6",
            "quote": "以 GPU 为参考，ratio ≥ 0.45×",
            # 本文件只测 adapter 透传；真实快照重算与 quote 定位由 acceptance gate 专测。
            "taskdoc_snapshot_sha256": "a" * 64,
        },
    }
    return spec


def _perf_receipt():
    return {
        "artifact": {"path": "cpp_extension/x.so", "sha256": "1" * 64},
        "load": {"namespace": "oprunway_test"},
        "bindings": {"invocation_plan_sha256": "3" * 64},
        "vendor": {
            "library_path": "/opt/vendor/lib.so",
            "library_sha256": "2" * 64,
            "symbols_owned": ["aclnnWitness"],
        },
    }


class CppExtensionMeasureOnlyPerfGateTest(unittest.TestCase):
    """`measure_only` 下精度部分失败仍要采到实测；`ratio_gated` 行为逐字不变。"""

    def _caseset_with_perf_dims(self):
        caseset = _caseset()
        for case in caseset["cases"]:
            case["dims"] = ["功能", "精度", "性能"]
        return caseset

    def test_measure_only_collects_the_passing_subset_and_keeps_the_denominator(self):
        caseset = self._caseset_with_perf_dims()
        # c0 精度可判但没过；c1 过了。分母（两条精度 case）必须原样落盘。
        evidence = [
            {"case_id": "c0", "precision": {"policy": {}, "metrics": {}}},
            {"case_id": "c1", "precision": {"policy": {}, "metrics": {}}},
        ]
        with tempfile.TemporaryDirectory() as td:
            A.prepare(_measure_only_spec(), caseset, td)
            with mock.patch(
                    "aclnn_runtime.perf_msprof.accuracy_pass_ids",
                    return_value={"c1"}), mock.patch.dict(
                        os.environ, {"OPRUNWAY_CPP_EXTENSION_DEVICE": "0"}):
                plan, skipped = A._write_perf_plan(
                    caseset, td, evidence, _perf_receipt())
        self.assertIsNotNone(plan)
        self.assertEqual(plan["cases"], ["c1"])
        self.assertEqual(plan["mode"], "measure_only")
        self.assertNotIn("baseline", plan)
        gate = plan["precision_gate"]
        self.assertFalse(gate["gate_passed"])
        self.assertEqual(gate["precision_case_total"], 2)
        self.assertEqual(gate["precision_not_passed"], ["c0"])
        self.assertEqual(
            skipped, [{"case_id": "c0", "reason": "skipped_accuracy_failed"}])

    def test_measure_only_distinguishes_unjudgeable_from_failed(self):
        """没跑出来（无精度块）和算错了是两回事，skipped 理由不许混成一个词。"""
        caseset = self._caseset_with_perf_dims()
        evidence = [{"case_id": "c1", "precision": {"policy": {}, "metrics": {}}}]
        with tempfile.TemporaryDirectory() as td:
            A.prepare(_measure_only_spec(), caseset, td)
            with mock.patch(
                    "aclnn_runtime.perf_msprof.accuracy_pass_ids",
                    return_value={"c1"}), mock.patch.dict(
                        os.environ, {"OPRUNWAY_CPP_EXTENSION_DEVICE": "0"}):
                _plan, skipped = A._write_perf_plan(
                    caseset, td, evidence, _perf_receipt())
        self.assertEqual(
            skipped,
            [{"case_id": "c0", "reason": A.SKIPPED_PRECISION_NOT_EVALUABLE}])

    def test_ratio_gated_still_refuses_to_collect_on_partial_accuracy(self):
        caseset = self._caseset_with_perf_dims()
        with tempfile.TemporaryDirectory() as td:
            A.prepare(_spec(), caseset, td)
            with mock.patch(
                    "aclnn_runtime.perf_msprof.accuracy_pass_ids",
                    return_value={"c0"}), mock.patch(
                    "aclnn_runtime.perf_msprof.select_perf_cases") as select:
                plan, skipped = A._write_perf_plan(
                    caseset, td, [{"case_id": "c0"}], _perf_receipt())
        self.assertIsNone(plan)
        self.assertEqual(
            skipped,
            [{"case_id": "c1", "reason": A.SKIPPED_PRECISION_OVERALL_GATE}])
        select.assert_not_called()

    def test_measure_only_template_carries_no_baseline_slot(self):
        with tempfile.TemporaryDirectory() as td:
            A.prepare(_measure_only_spec(), _caseset(), td)
            with open(os.path.join(td, "cpp_extension_perf_template.json"),
                      encoding="utf-8") as src:
                template = json.load(src)
        self.assertEqual(template["mode"], "measure_only")
        for forbidden in ("baseline", "torch_baseline", "aclnn_baseline"):
            self.assertNotIn(forbidden, template)
        self.assertEqual(
            template["measure_only_authorization"]["taskdoc_requirement"],
            "gpu_comparison")

    def test_measure_only_without_taskdoc_authorization_fails_closed(self):
        spec = _measure_only_spec()
        del spec["perf"]["measure_only_authorization"]
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(A.CppExtensionAdapterError, "口径非法"):
                A.prepare(spec, _caseset(), td)


if __name__ == "__main__":
    unittest.main()
