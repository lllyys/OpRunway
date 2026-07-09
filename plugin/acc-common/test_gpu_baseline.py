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


if __name__ == "__main__":
    unittest.main()
