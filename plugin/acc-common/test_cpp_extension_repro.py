import os
import io
import unittest
from unittest import mock

import cpp_extension_repro as R


class SelectRepresentativesTest(unittest.TestCase):
    def test_prepares_exact_vendor_runtime_paths_without_duplicates(self):
        vendor = "/work/vendor/vendors/customize_nn/op_api/lib/libcust_opapi.so"
        with mock.patch.dict(os.environ, {
                "ASCEND_CUSTOM_OPP_PATH": "/old",
                "LD_LIBRARY_PATH": "/old/lib",
                "ASCEND_TOOLKIT_HOME": "",
        }, clear=False):
            self.assertEqual(
                R._prepare_vendor_runtime_env(vendor),
                "/work/vendor/vendors/customize_nn",
            )
            self.assertEqual(
                os.environ["ASCEND_CUSTOM_OPP_PATH"],
                "/work/vendor/vendors/customize_nn:/old",
            )
            self.assertEqual(
                os.environ["LD_LIBRARY_PATH"],
                "/work/vendor/vendors/customize_nn/op_api/lib:/old/lib",
            )
            R._prepare_vendor_runtime_env(vendor)
            self.assertEqual(
                os.environ["ASCEND_CUSTOM_OPP_PATH"].count(
                    "/work/vendor/vendors/customize_nn"), 1)

    def test_rejects_vendor_outside_expected_layout(self):
        with self.assertRaisesRegex(R.ReproError, "vendor-root"):
            R._prepare_vendor_runtime_env("/tmp/libcust_opapi.so")

    def test_human_summary_exposes_actual_and_golden_failure(self):
        result = {
            "out_dir": "/tmp/repro",
            "results": [{
                "case_id": "x",
                "torch_integration": {
                    "status": "PASS（已注册、已实际调用并返回输出）",
                    "entrypoint": "torch.ops.ns.invoke_v1",
                    "schema": "ns::invoke_v1(Tensor self) -> Tensor",
                    "artifact_sha256": "abc",
                },
                "call": {
                    "extension": "torch.ops.ns.invoke_v1",
                    "dut_interface": "aclnnMedian",
                    "slots": [
                        {"role": "in", "name": "self", "input_idx": 0},
                        {"role": "attr", "name": "dim", "ctype": "int64",
                         "value": 0},
                        {"role": "out", "name": "index", "output_idx": 0},
                    ],
                },
                "inputs": [
                    {"name": "self", "dtype": "float16", "shape": [16, 4]},
                ],
                "attrs": {"dim": 0, "keepDim": False},
                "golden_interface": "torch torch.median",
                "output_contracts": [
                    {"name": "index", "role": "index", "dtype": "int32",
                     "shape": [4]},
                ],
                "outputs": [
                    {"name": "value", "role": "value", "state": "pass"},
                    {
                        "name": "index", "role": "index", "state": "fail",
                        "reason": "mismatch=1/numel=1",
                        "policy": {
                            "kind": "index_value_consistency",
                            "value_rtol": 0.004,
                            "value_atol": 0.004,
                        },
                        "metrics": {
                            "mismatch": 1,
                            "numel": 1,
                            "invalid_index_count": 1,
                        },
                        "actual_sample": [2147483647],
                        "golden_sample": [17],
                        "sample_limit": 8,
                    },
                ],
            }],
        }
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            R._print_human_summary(result)
        text = out.getvalue()
        self.assertIn("1. Torch 接入", text)
        self.assertIn("已注册、已实际调用并返回输出", text)
        self.assertIn("2. 输入与调用参数", text)
        self.assertIn("3. Golden 与本次测试接口", text)
        self.assertIn("Golden 接口: torch torch.median", text)
        self.assertIn("4. 输出差异与阈值", text)
        self.assertIn("mismatch 必须为 0", text)
        self.assertIn("5. 本次复现结论", text)
        self.assertIn("本次 Extension 接口: torch.ops.ns.invoke_v1", text)
        self.assertIn("本次 DUT 接口: aclnnMedian", text)
        self.assertIn("self: dtype=float16, shape=[16, 4]", text)
        self.assertIn("dim=0, keepDim=false", text)
        self.assertIn("0. self (input[0])", text)
        self.assertIn("1. dim (int64=0)", text)
        self.assertIn("index: role=index, dtype=int32, shape=[4]", text)
        self.assertIn("失败输出: index (role=index)", text)
        self.assertIn("失败判据: mismatch=1/numel=1", text)
        self.assertIn("actual 前 8 项: [2147483647]", text)
        self.assertIn("golden 前 8 项: [17]", text)
        self.assertIn("/tmp/repro/repro_summary.json", text)

    def test_vendor_handle_is_retained_and_symbols_are_resolved(self):
        handle = object()

        class FakeCtypes:
            RTLD_GLOBAL = 123

            @staticmethod
            def CDLL(path, mode):
                self.assertEqual((path, mode), ("/vendor.so", 123))
                return type("Handle", (), {"aclnnMedian": object()})()

        result = R._bind_vendor_before_torch(
            FakeCtypes, "/vendor.so", ["aclnnMedian"])
        self.assertTrue(hasattr(result, "aclnnMedian"))

    def test_vendor_binding_rejects_missing_symbol(self):
        class FakeCtypes:
            RTLD_GLOBAL = 123

            @staticmethod
            def CDLL(_path, mode):
                del mode
                return object()

        with self.assertRaisesRegex(R.ReproError, "缺 DUT symbols"):
            R._bind_vendor_before_torch(
                FakeCtypes, "/vendor.so", ["aclnnMedian"])

    def test_groups_by_dtype_and_failed_roles(self):
        caseset = {"cases": [
            {"id": "a", "inputs": [{"dtype": "float16"}]},
            {"id": "b", "inputs": [{"dtype": "float16"}]},
            {"id": "c", "inputs": [{"dtype": "float32"}]},
            {"id": "d", "inputs": [{"dtype": "float32"}]},
        ]}
        evidence = {"evidence": [
            {"case_id": "a", "precision": {"outputs": [
                {"role": "index", "policy": {"kind": "exact"},
                 "metrics": {"mismatch": 1, "numel": 1}}]}},
            {"case_id": "b", "precision": {"outputs": [
                {"role": "index", "policy": {"kind": "exact"},
                 "metrics": {"mismatch": 1, "numel": 1}}]}},
            {"case_id": "c", "precision": {"outputs": [
                {"role": "value", "policy": {"kind": "exact"},
                 "metrics": {"mismatch": 1, "numel": 1}}]}},
            {"case_id": "d", "precision": {"outputs": [
                {"role": "value", "policy": {"kind": "exact"},
                 "metrics": {"mismatch": 1, "numel": 1}},
                {"role": "index", "policy": {"kind": "exact"},
                 "metrics": {"mismatch": 1, "numel": 1}}]}},
        ]}
        verdict = {"per_case": [
            {"case_id": "a", "精度": "fail"},
            {"case_id": "b", "精度": "fail"},
            {"case_id": "c", "精度": "fail"},
            {"case_id": "d", "精度": "fail"},
        ]}
        self.assertEqual(
            R.select_representatives(caseset, evidence, verdict),
            ["a", "c", "d"],
        )

    def test_rejects_unaligned_report(self):
        with self.assertRaisesRegex(R.ReproError, "无法对齐"):
            R.select_representatives(
                {"cases": [{"id": "a", "inputs": [{"dtype": "float16"}]}]},
                {"evidence": []},
                {"per_case": [{"case_id": "a", "精度": "fail"}]},
            )

    @mock.patch.object(R, "_load")
    @mock.patch.object(R, "_resolve_cases", return_value=["a"])
    @mock.patch.object(R, "reproduce", side_effect=RuntimeError("executor is nullptr"))
    def test_main_distinguishes_execution_error_from_precision_failure(
            self, _reproduce, _resolve, load):
        load.side_effect = [
            {"cases": [{"id": "a"}]},
            {"evidence": []},
            {"per_case": [{"case_id": "a", "精度": "fail"}]},
        ]
        self.assertEqual(
            R.main(["--report-root", "/tmp/report", "--case-id", "a"]), 2)
