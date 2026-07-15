"""gpu_baseline 单测（T8 consumer 侧解析+校验）——stdlib unittest。

caseset 由 gen_cases 运行时产、baseline 程序化构造（防 fixture 漂移，codex M3）。
跑: python3 -m unittest test_gpu_baseline -v   （在 acc-common/ 下）
"""
import json, os, tempfile, unittest
import gen_cases
import gpu_baseline as gb

_HERE = os.path.dirname(os.path.abspath(__file__))


def _entry(c, **over):
    e = {"case_id": c["id"], "device": "NVIDIA A100", "dtype": c["inputs"][0]["dtype"],
         "shape": c["inputs"][0]["shape"], "attrs": c["attrs"], "inputs": c["inputs"],
         "timing_scope": "kernel_only", "warmup": 20, "iters": 50, "sync_policy": "s",
         "statistic": "median", "unit": "us", "value": 5.0, "tool": "nsys",
         "clock_power_state": "l", "data_transfer_included": False}
    e.update(over)
    return e


class GpuBaselineTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        with open(os.path.join(_HERE, "testdata", "gpu_demo.spec.json"), encoding="utf-8") as f:
            spec = json.load(f)
        self.cs = gen_cases.gen_cases(spec, self.d)
        self.pcs = [c for c in self.cs["cases"] if "性能" in c["dims"]]

    def _write(self, cases, **top):
        p = os.path.join(self.d, "gpu.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"cases": cases, **top}, f)
        return p

    def _parse(self, cases, **top):
        return gb.parse_gpu_baseline(self._write(cases, **top), self.cs)

    def test_legal(self):
        bl, rep = self._parse([_entry(c) for c in self.pcs])
        self.assertEqual(rep["hard_errors"], 0)
        self.assertIsNotNone(bl)
        self.assertEqual(bl["source"], "gpu_external")
        self.assertEqual(bl["scope"], "kernel_only")
        self.assertEqual(bl["per_case"][0]["us"], 5.0)
        self.assertTrue(rep["contract_version"])

    def test_unit_ms_to_us(self):
        bl, rep = self._parse([_entry(c, unit="ms", value=5.0) for c in self.pcs])
        self.assertEqual(rep["hard_errors"], 0)
        self.assertEqual(bl["per_case"][0]["us"], 5000.0)      # 5ms = 5000us

    def test_unit_ns_to_us(self):
        bl, _ = self._parse([_entry(c, unit="ns", value=2000.0) for c in self.pcs])
        self.assertEqual(bl["per_case"][0]["us"], 2.0)         # 2000ns = 2us

    def test_non_gpu_device(self):
        _, rep = self._parse([_entry(c, device="Ascend950PR") for c in self.pcs])
        self.assertTrue(any(i["code"] == "NOT_GPU" for i in rep["issues"]))

    def test_missing_field(self):
        _, rep = self._parse([_entry(c, timing_scope=None) for c in self.pcs])
        self.assertTrue(rep["hard_errors"] >= 1)

    def test_illegal_enum(self):
        _, rep = self._parse([_entry(c, statistic="bogus") for c in self.pcs])
        self.assertTrue(any(i["code"] == "ENUM" and i["field"] == "statistic" for i in rep["issues"]))

    def test_fingerprint_mismatch(self):
        bad = [_entry(c, inputs=[{"name": "self", "dtype": "float32", "shape": [7, 7]}]) for c in self.pcs]
        bl, rep = self._parse(bad)
        self.assertIsNone(bl)                                  # hard error → 阻断
        self.assertTrue(any(i["code"] == "FINGERPRINT" for i in rep["issues"]))

    def test_missing_case(self):
        _, rep = self._parse([])                               # 覆盖不全
        self.assertTrue(any(i["code"] in ("SHAPE", "MISSING_CASE") for i in rep["issues"]))

    def test_extra_case(self):
        entries = [_entry(c) for c in self.pcs] + [_entry(self.pcs[0], case_id="not_in_caseset")]
        _, rep = self._parse(entries)
        self.assertTrue(any(i["code"] == "EXTRA_CASE" for i in rep["issues"]))

    def test_policy_risk_warn_not_hard(self):
        bl, rep = self._parse([_entry(c, warmup=5, iters=10, statistic="mean") for c in self.pcs])
        self.assertEqual(rep["hard_errors"], 0)
        self.assertTrue(rep["warns"] >= 1)
        self.assertIn("policy_risk", bl["per_case"][0])

    def test_scope_transfer_mismatch(self):
        _, rep = self._parse([_entry(c, timing_scope="kernel_only", data_transfer_included=True)
                              for c in self.pcs])
        self.assertTrue(any(i["code"] == "SCOPE_TRANSFER" for i in rep["issues"]))

    def test_missing_file(self):
        bl, rep = gb.parse_gpu_baseline(os.path.join(self.d, "nope.json"), self.cs)
        self.assertIsNone(bl)
        self.assertTrue(any(i["code"] == "FILE_LOAD" for i in rep["issues"]))

    def test_never_raises_on_garbage(self):
        p = os.path.join(self.d, "bad.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write("{not json")
        bl, rep = gb.parse_gpu_baseline(p, self.cs)            # 绝不 raise
        self.assertIsNone(bl)

    def test_mock_example_no_drift(self):
        """人读示例 testdata/mock_gpu_baseline.json 应与 live gpu_demo caseset 签名一致（防漂移提醒）。"""
        p = os.path.join(_HERE, "testdata", "mock_gpu_baseline.json")
        bl, rep = gb.parse_gpu_baseline(p, self.cs)
        self.assertEqual(rep["hard_errors"], 0,
                         f"mock_gpu_baseline.json 与 live caseset 漂移，请更新：{rep['issues']}")

    # ---- CONFIRMED 真 bug 负例（gb-2/3/4/8/9），钉死防回归 ----

    def _synth_cs(self, n=2):
        """程序化 n 个性能用例的 caseset（用于需 ≥2 用例的场景，如混合 scope）。
        shape 取**非 trivial**（numel≥4096）——否则 §trivial-met 会把它们从 GPU 标杆 required 剔除、不参与 scope 校验。"""
        cases = [{"id": f"p{i}", "dims": ["性能"], "tags": [],
                  "inputs": [{"name": "self", "dtype": "float32", "shape": [8192 + i]}], "attrs": {}}
                 for i in range(n)]
        return {"op": "Sign", "cases": cases}

    def _parse_cs(self, cases, cs, **top):
        p = os.path.join(self.d, "gpu2.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"cases": cases, **top}, f)
        return gb.parse_gpu_baseline(p, cs)

    def test_gb2_non_gpu_whitelist_rejects(self):
        """gb-2：Intel Xeon CPU / Google TPU v4 / 昇腾910B / Huawei Kunpeng → hard error；NVIDIA A100 → 通过。"""
        for dev in ("Intel Xeon CPU", "Google TPU v4", "昇腾910B", "Huawei Kunpeng 920"):
            _, rep = self._parse([_entry(c, device=dev) for c in self.pcs])
            self.assertTrue(any(i["code"] == "NOT_GPU" for i in rep["issues"]), dev)
            self.assertGreaterEqual(rep["hard_errors"], 1, dev)
        bl, rep = self._parse([_entry(c, device="NVIDIA A100-SXM4-80GB") for c in self.pcs])
        self.assertEqual(rep["hard_errors"], 0)
        self.assertIsNotNone(bl)

    def test_gb2_device_type_escape_hatch(self):
        """型号串不认，但 device_type=='gpu' → 白名单放行。"""
        bl, rep = self._parse([_entry(c, device="Custom Accel X", device_type="gpu") for c in self.pcs])
        self.assertEqual(rep["hard_errors"], 0, rep["issues"])
        self.assertIsNotNone(bl)

    def test_gb3_bad_attrs_shape_no_crash(self):
        """gb-3：attrs 为 str、shape 为 int → hard error 且绝不抛异常。"""
        _, rep = self._parse([_entry(c, attrs="notadict") for c in self.pcs])   # attrs 为 str
        self.assertGreaterEqual(rep["hard_errors"], 1)
        self.assertTrue(any(i["field"] == "attrs" for i in rep["issues"]))
        _, rep2 = self._parse([_entry(c, shape=5) for c in self.pcs])           # shape 为 int
        self.assertGreaterEqual(rep2["hard_errors"], 1)
        self.assertTrue(any(i["field"] == "shape" for i in rep2["issues"]))

    def test_gb4_strict_types_hard_error(self):
        """gb-4：tool=123 / dtype=456 / warmup='x' → hard error。"""
        for over, field in (({"tool": 123}, "tool"), ({"dtype": 456}, "dtype"), ({"warmup": "x"}, "warmup")):
            _, rep = self._parse([_entry(c, **over) for c in self.pcs])
            self.assertTrue(any(i["severity"] == "error" and i["field"] == field for i in rep["issues"]),
                            f"{field} 应 hard error：{rep['issues']}")

    def test_gb8_nan_neg_hard_error(self):
        """gb-8：warmup=NaN / iters=-1 → hard error（不再只 warn 放行）。"""
        _, rep = self._parse([_entry(c, warmup=float("nan")) for c in self.pcs])
        self.assertTrue(any(i["field"] == "warmup" and i["severity"] == "error" for i in rep["issues"]))
        _, rep2 = self._parse([_entry(c, iters=-1) for c in self.pcs])
        self.assertTrue(any(i["field"] == "iters" and i["severity"] == "error" for i in rep2["issues"]))

    def test_gb9_mixed_scope_blocked_status_incomparable(self):
        """gb-9：GPU 标杆内部混合 scope → blocked_status=incomparable（不是缺标杆 wait）。"""
        cs = self._synth_cs(2)
        pcs = cs["cases"]
        entries = [_entry(pcs[0]),
                   _entry(pcs[1], timing_scope="host_e2e_with_h2d_d2h", data_transfer_included=True)]
        bl, rep = self._parse_cs(entries, cs)
        self.assertIsNone(bl)
        self.assertTrue(any(i["code"] == "MIXED_SCOPE" for i in rep["issues"]))
        self.assertEqual(rep["blocked_status"], "blocked_incomparable_timing_scope")

    def test_gb9_other_hard_error_not_wait(self):
        """gb-9：非 scope 硬错（fingerprint）→ blocked_status=invalid（≠缺标杆 wait）。"""
        bad = [_entry(c, inputs=[{"name": "self", "dtype": "float32", "shape": [7, 7]}]) for c in self.pcs]
        _, rep = self._parse(bad)
        self.assertEqual(rep.get("blocked_status"), "blocked_gpu_baseline_invalid")


if __name__ == "__main__":
    unittest.main()
