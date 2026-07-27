"""性能 case 来源与 shape 大小分类契约单测。

运行位置：远程 NPU 容器；本文件只测确定性生成逻辑，不进行真机性能采集。
"""
import json
import os
import unittest
from unittest import mock

import gen_cases as GC


def _spec(limit=262144):
    return {"perf": {
        "case_source": "precision_cases",
        "shape_classification": {
            "metric": "sum_input_bytes",
            "small_max_bytes": limit,
            "hardware": "Atlas A3",
        },
    }}


def _case(cid, shape, dtype="float32", *, dims=None, storage_dtype=None):
    item = {"name": "self", "shape": list(shape), "dtype": dtype, "path": f"{cid}/x1.npy"}
    if storage_dtype is not None:
        item["storage_dtype"] = storage_dtype
    return {"id": cid, "dims": dims or ["功能", "精度", "性能"],
            "tags": ["常规"], "inputs": [item]}


class PerfShapeClassificationTest(unittest.TestCase):
    def test_256_kib_boundary_is_inclusive(self):
        cases = [
            _case("equal", [65536]),       # fp32: 65536 * 4 = 262144
            _case("over", [65537]),
            _case("bf16", [131072], "bfloat16", storage_dtype="uint16"),
        ]
        ledger = GC._classify_perf_cases(_spec(), cases)
        self.assertEqual(ledger["counts"], {"small": 2, "large": 1})
        self.assertEqual(cases[0]["perf_shape_classification"]["input_bytes"], 262144)
        self.assertEqual(cases[0]["perf_shape_classification"]["class"], "small")
        self.assertIn("小shape", cases[0]["tags"])
        self.assertEqual(cases[1]["perf_shape_classification"]["class"], "large")
        self.assertIn("大shape", cases[1]["tags"])
        self.assertEqual(cases[2]["perf_shape_classification"]["input_bytes"], 262144)
        self.assertEqual(ledger["selection"]["selected_case_ids"], ["equal", "over", "bf16"])
        self.assertEqual(
            ledger["selection"]["selected_by_dtype"],
            {"bfloat16": 1, "float32": 2})
        self.assertEqual(ledger["selection"]["selected_total"], 3)
        self.assertEqual(ledger["selection"]["precision_total"], 3)

    def test_multi_input_physical_bytes_are_summed(self):
        at_limit = _case("at_limit", [32768], "float32")
        at_limit["inputs"].append({
            "name": "other", "shape": [65536], "dtype": "bfloat16",
            "storage_dtype": "uint16", "path": "at_limit/x2.npy"})
        over = json.loads(json.dumps(at_limit))
        over["id"] = "over"
        over["inputs"][1]["shape"] = [65537]
        ledger = GC._classify_perf_cases(_spec(), [at_limit, over])
        self.assertEqual(
            [c["perf_shape_classification"]["input_bytes"] for c in (at_limit, over)],
            [262144, 262146])
        self.assertEqual(ledger["counts"], {"small": 1, "large": 1})
        self.assertEqual(
            ledger["selection"]["selected_by_dtype"],
            {"bfloat16+float32": 2})

    def test_non_perf_precision_case_is_unchanged(self):
        case = _case("precision_only", [8], dims=["功能", "精度"])
        before = dict(case)
        ledger = GC._classify_perf_cases(_spec(), [case])
        self.assertEqual(case, before)
        self.assertEqual(ledger["counts"], {"small": 0, "large": 0})
        self.assertEqual(ledger["selection"]["selected_case_ids"], [])
        self.assertEqual(ledger["selection"]["excluded_precision_case_ids"], ["precision_only"])

    def test_perf_case_must_also_be_precision_case(self):
        with self.assertRaisesRegex(ValueError, "dims 不含「精度」"):
            GC._classify_perf_cases(_spec(), [_case("bad", [8], dims=["性能"])])

    def test_bad_policy_fails_closed(self):
        bad_values = [
            {"case_source": "separate_cases",
             "shape_classification": {"metric": "sum_input_bytes",
                                      "small_max_bytes": 262144, "hardware": "Atlas A3"}},
            {"case_source": "precision_cases",
             "shape_classification": {"metric": "numel",
                                      "small_max_bytes": 262144, "hardware": "Atlas A3"}},
            {"case_source": "precision_cases",
             "shape_classification": {"metric": "sum_input_bytes",
                                      "small_max_bytes": 0, "hardware": "Atlas A3"}},
            {"case_source": "precision_cases",
             "shape_classification": {"metric": "sum_input_bytes",
                                      "small_max_bytes": 1048576, "hardware": "Atlas A3"}},
            {"case_source": "precision_cases",
             "shape_classification": {"metric": "sum_input_bytes",
                                      "small_max_bytes": 262144, "hardware": "A3"}},
        ]
        for perf in bad_values:
            with self.subTest(perf=perf), self.assertRaises(ValueError):
                GC._classify_perf_cases({"perf": perf}, [])

    def test_dry_run_checks_perf_policy_before_generation(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "samples", "specs", "median.spec.json")
        with open(path, encoding="utf-8") as fh:
            spec = json.load(fh)
        with mock.patch.object(GC, "load_golden", side_effect=ValueError("缺 golden: test")):
            ledger = GC._build_dry_run_ledger(spec)
        self.assertEqual(
            ledger["planning"]["perf_case_policy"]["shape_classification"]["small_max_bytes"],
            262144)
        spec["perf"].pop("case_source")
        with mock.patch.object(GC, "load_golden", side_effect=ValueError("缺 golden: test")):
            with self.assertRaisesRegex(ValueError, "通用规则"):
                GC._build_dry_run_ledger(spec)

    def test_perf_without_general_policy_fails_closed(self):
        for perf in (
                {"baseline": "tbe", "target_ratio": 1.0},
                {"baseline": "tbe", "target_ratio": 1.0,
                 "case_source": "precision_cases"},
                {"baseline": "tbe", "target_ratio": 1.0,
                 "shape_classification": {"metric": "sum_input_bytes",
                                          "small_max_bytes": 262144,
                                          "hardware": "Atlas A3"}}):
            with self.subTest(perf=perf), self.assertRaisesRegex(ValueError, "通用规则"):
                GC._classify_perf_cases({"perf": perf}, [])

    def test_all_sample_perf_specs_declare_general_policy(self):
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "samples", "specs")
        for name in sorted(os.listdir(root)):
            if not name.endswith(".spec.json"):
                continue
            with open(os.path.join(root, name), encoding="utf-8") as fh:
                spec = json.load(fh)
            if not isinstance(spec.get("perf"), dict):
                continue
            with self.subTest(spec=name):
                policy = GC._perf_case_policy(spec)
                self.assertEqual(policy["case_source"], "precision_cases")
                self.assertEqual(policy["shape_classification"]["metric"], "sum_input_bytes")

    def test_median_spec_uses_confirmed_torch_baseline_and_a3_boundary(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "samples", "specs", "median.spec.json")
        with open(path, encoding="utf-8") as fh:
            spec = json.load(fh)
        self.assertEqual(spec["perf"]["baseline"], "torch_npu")
        self.assertEqual(spec["perf"]["torch_baseline"]["api"], "torch.median")
        self.assertEqual(spec["perf"]["case_source"], "precision_cases")
        rule = spec["perf"]["shape_classification"]
        self.assertEqual(rule["metric"], "sum_input_bytes")
        self.assertEqual(rule["small_max_bytes"], 262144)
        self.assertEqual(rule["hardware"], "Atlas A3")


if __name__ == "__main__":
    unittest.main(verbosity=2)
