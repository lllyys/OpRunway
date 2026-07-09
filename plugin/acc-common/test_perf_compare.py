"""perf_compare 单测（T6 小shape例外 + T8 GPU consumer）——stdlib unittest。

跑: python3 -m unittest test_perf_compare -v   （在 acc-common/ 下）
"""
import os, tempfile, unittest
import perf_compare as pc
import gen_cases
import gpu_baseline as gb


def _caseset(perf_cases, op="Sign"):
    """perf_cases: [(cid, tags, shape)] → 最小 caseset（只含性能维用例）。"""
    cases = [{"id": cid, "dims": ["性能"], "tags": tags,
              "inputs": [{"name": "self", "dtype": "float32", "shape": shape}], "attrs": {}}
             for cid, tags, shape in perf_cases]
    return {"op": op, "cases": cases}


def _ev(perf, op="Sign"):
    """perf: {cid: (us, scope)}"""
    return {"op": op, "evidence": [{"case_id": c, "perf": {"us": u, "scope": s}}
                                   for c, (u, s) in perf.items()]}


def _bl(per, scope="kernel_only", source="tbe"):
    """per: {cid: us} 或 {cid: {us, policy_risk}}"""
    rows = []
    for c, v in per.items():
        rows.append({"case_id": c, **v} if isinstance(v, dict) else {"case_id": c, "us": v})
    return {"source": source, "scope": scope, "per_case": rows}


def _spec(target=1.0, exc=None, baseline="tbe"):
    perf = {"baseline": baseline, "target_ratio": target}
    if exc is not None:
        perf["small_shape_exception"] = exc
    return {"op": "Sign", "perf": perf}


_EXC = {"when_us_below": 10, "abs_gap_us_within": 3}


class SmallShapeExceptionTest(unittest.TestCase):
    def test_hit_exception(self):
        cs = _caseset([("s0", ["性能", "小shape"], [64])])
        r = pc.perf_compare(_spec(1.0, _EXC), cs, _ev({"s0": (1.5, "kernel_only")}), _bl({"s0": 1.2}))
        self.assertEqual(r["summary"]["status"], "exception")
        row = r["per_case"][0]
        self.assertFalse(row["达标"])                      # 绝不偷偷置 True
        self.assertEqual(row["exception"], "small_shape")
        self.assertEqual(row["scope"], "kernel_only")       # 例外行仍带 kernel_only
        self.assertIn("simulation", r)
        self.assertEqual([p["case_id"] for p in r["simulation"]["points"]], ["s0"])

    def test_gap_over_tol_is_fail(self):
        cs = _caseset([("s0", ["性能", "小shape"], [64])])
        r = pc.perf_compare(_spec(1.0, _EXC), cs, _ev({"s0": (6.0, "kernel_only")}), _bl({"s0": 1.0}))
        self.assertEqual(r["summary"]["status"], "fail")   # gap=5>3
        self.assertNotIn("exception", r["per_case"][0])

    def test_threshold_boundary_strict(self):
        """max(npu,base)==when_us_below → `<` 严格 → 不命中例外 → fail。"""
        cs = _caseset([("s0", ["性能", "小shape"], [64])])
        r = pc.perf_compare(_spec(1.0, _EXC), cs, _ev({"s0": (10.0, "kernel_only")}), _bl({"s0": 8.0}))
        self.assertEqual(r["summary"]["status"], "fail")

    def test_non_smallshape_tag_not_exception(self):
        """非小shape-tag 但恰好 <阈 且 gap≤tol → 不误转例外。"""
        cs = _caseset([("s0", ["性能", "大shape"], [1024, 1024])])
        r = pc.perf_compare(_spec(1.0, _EXC), cs, _ev({"s0": (1.5, "kernel_only")}), _bl({"s0": 1.2}))
        self.assertEqual(r["summary"]["status"], "fail")
        self.assertNotIn("exception", r["per_case"][0])

    def test_mixed_pass_and_exception(self):
        cs = _caseset([("s0", ["性能", "小shape"], [64]), ("b0", ["性能", "大shape"], [1024, 1024])])
        r = pc.perf_compare(_spec(1.0, _EXC), cs,
                            _ev({"s0": (1.5, "kernel_only"), "b0": (2.0, "kernel_only")}),
                            _bl({"s0": 1.2, "b0": 3.0}))   # b0 ratio 1.5 达标
        self.assertEqual(r["summary"]["status"], "exception")

    def test_genuine_fail_beats_exception(self):
        cs = _caseset([("s0", ["性能", "小shape"], [64]), ("g0", ["性能", "大shape"], [1024, 1024])])
        r = pc.perf_compare(_spec(1.0, _EXC), cs,
                            _ev({"s0": (1.5, "kernel_only"), "g0": (6.0, "kernel_only")}),
                            _bl({"s0": 1.2, "g0": 1.0}))    # g0 genuine fail
        self.assertEqual(r["summary"]["status"], "fail")

    def test_scope_mismatch_incomparable(self):
        cs = _caseset([("s0", ["性能", "小shape"], [64])])
        r = pc.perf_compare(_spec(1.0, _EXC), cs, _ev({"s0": (1.5, "device_e2e_no_h2d_d2h")}),
                            _bl({"s0": 1.2}, scope="kernel_only"))
        self.assertEqual(r["summary"]["status"], "blocked_incomparable_timing_scope")

    def test_illegal_numbers_blocked(self):
        cs = _caseset([("z", ["性能", "小shape"], [64]), ("n", ["性能", "小shape"], [64]),
                       ("i", ["性能", "小shape"], [64]), ("u", ["性能", "小shape"], [64])])
        ev = _ev({"z": (1.5, "kernel_only"), "n": (1.5, "kernel_only"),
                  "i": (1.5, "kernel_only"), "u": (None, "kernel_only")})
        bl = _bl({"z": 0, "n": -1.0, "i": float("inf"), "u": 1.2})
        r = pc.perf_compare(_spec(1.0, _EXC), cs, ev, bl)
        self.assertEqual(r["summary"]["status"], "blocked")
        self.assertEqual(r["summary"]["blocked"], 4)       # 0/负/inf/None 全 blocked、不进例外

    def test_disabled_when_no_exception_declared(self):
        cs = _caseset([("s0", ["性能", "小shape"], [64])])
        r = pc.perf_compare(_spec(1.0, exc=None), cs, _ev({"s0": (1.5, "kernel_only")}), _bl({"s0": 1.2}))
        self.assertEqual(r["summary"]["status"], "fail")   # 无声明 → 例外禁用 → 未达标即 fail

    def test_parse_dict_legacy_missing(self):
        self.assertEqual(pc._parse_small_shape_exception(_spec(exc=_EXC))[0]["when_us_below"], 10)
        d, _ = pc._parse_small_shape_exception(_spec(exc="<10us 差 3us→仿真图"))
        self.assertEqual((d["when_us_below"], d["abs_gap_us_within"]), (10.0, 3.0))
        d2, note = pc._parse_small_shape_exception(_spec(exc="小shape特殊处理"))
        self.assertIsNone(d2)
        self.assertTrue(note)
        d3, note3 = pc._parse_small_shape_exception(_spec(exc={"when_us_below": 0}))  # 非法
        self.assertIsNone(d3)
        self.assertTrue(note3)
        self.assertEqual(pc._parse_small_shape_exception(_spec(exc=None)), (None, None))

    def test_svg_threshold_from_spec_not_hardcoded(self):
        """阈值零硬编码：换 when_us_below=2 → max(1.5,1.2)<2 仍命中；换 1 → 1.5≥1 不命中。"""
        cs = _caseset([("s0", ["性能", "小shape"], [64])])
        r2 = pc.perf_compare(_spec(1.0, {"when_us_below": 2, "abs_gap_us_within": 1}), cs,
                             _ev({"s0": (1.5, "kernel_only")}), _bl({"s0": 1.2}))
        self.assertEqual(r2["summary"]["status"], "exception")
        r1 = pc.perf_compare(_spec(1.0, {"when_us_below": 1, "abs_gap_us_within": 1}), cs,
                             _ev({"s0": (1.5, "kernel_only")}), _bl({"s0": 1.2}))
        self.assertEqual(r1["summary"]["status"], "fail")


class MockBaselineTest(unittest.TestCase):
    def test_slow_cases_inject(self):
        ev = _ev({"a": (2.0, "kernel_only"), "b": (2.0, "kernel_only")})
        bl = pc.mock_baseline(_spec(), ev, slow_cases=["a"])
        by = {r["case_id"]: r for r in bl["per_case"]}
        self.assertEqual(by["a"]["us"], 1.6)               # 2.0*0.8
        self.assertIn("inj-slow", by["a"]["env"])
        self.assertEqual(by["b"]["us"], round(2.0 * 1.08, 3))


class GpuConsumerTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def _live_caseset(self, spec_path="testdata/gpu_demo.spec.json"):
        import json
        with open(spec_path, encoding="utf-8") as f:
            spec = json.load(f)
        wd = tempfile.mkdtemp()
        return spec, gen_cases.gen_cases(spec, wd), wd

    def _gpu_json(self, caseset, scope="kernel_only", value=5.0, dti=False, warmup=20, iters=50,
                  statistic="median"):
        import json
        pcs = [c for c in caseset["cases"] if "性能" in c["dims"]]
        cases = [{"case_id": c["id"], "device": "NVIDIA A100", "dtype": c["inputs"][0]["dtype"],
                  "shape": c["inputs"][0]["shape"], "attrs": c["attrs"], "inputs": c["inputs"],
                  "timing_scope": scope, "warmup": warmup, "iters": iters, "sync_policy": "s",
                  "statistic": statistic, "unit": "us", "value": value, "tool": "nsys",
                  "clock_power_state": "l", "data_transfer_included": dti} for c in pcs]
        tmp = os.path.join(self.d, "gpu_bl.json")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"cases": cases}, f)
        return tmp

    def test_wait_when_baseline_none(self):
        spec, cs, wd = self._live_caseset()
        import repo_adapter as ra
        ev = ra.run_mock(cs, wd)
        r = pc.perf_compare(spec, cs, ev, None, expect_source="gpu_external")
        self.assertEqual(r["summary"]["status"], "blocked_wait_gpu_benchmark")
        self.assertTrue(all("npu_us" in row and row.get("npu_scope") == "kernel_only"
                            for row in r["per_case"]))

    def test_gpu_align_and_report(self):
        spec, cs, wd = self._live_caseset()
        import repo_adapter as ra
        ev = ra.run_mock(cs, wd)
        bl, rep = gb.parse_gpu_baseline(self._gpu_json(cs), cs)
        self.assertEqual(rep["hard_errors"], 0)
        r = pc.perf_compare(spec, cs, ev, bl, expect_source="gpu_external")
        self.assertEqual(r["summary"]["status"], "ok")
        self.assertEqual(r["baseline_source"], "gpu_external")
        self.assertTrue(all("ratio" in row for row in r["per_case"]))

    def test_gpu_scope_mismatch_incomparable(self):
        spec, cs, wd = self._live_caseset()
        import repo_adapter as ra
        ev = ra.run_mock(cs, wd)
        bl, _ = gb.parse_gpu_baseline(self._gpu_json(cs, scope="host_e2e_with_h2d_d2h", dti=True), cs)
        r = pc.perf_compare(spec, cs, ev, bl, expect_source="gpu_external")
        self.assertEqual(r["summary"]["status"], "blocked_incomparable_timing_scope")

    def test_sub_policy_risk_flag(self):
        """消费 sub-policy(warmup<10) 基线且达标 → summary.risk 含 sub_policy_timing（codex M6）。"""
        cs = _caseset([("b0", ["性能", "大shape"], [1024, 1024])])
        ev = _ev({"b0": (2.0, "kernel_only")})
        bl = _bl({"b0": {"us": 3.0, "policy_risk": ["warmup=5<10"]}}, source="gpu_external")
        r = pc.perf_compare(_spec(0.5, baseline="gpu_external"), cs, ev, bl, expect_source="gpu_external")
        self.assertEqual(r["summary"]["status"], "ok")
        self.assertIn("sub_policy_timing", r["summary"].get("risk", []))


class ConfirmedBugRegressionTest(unittest.TestCase):
    """钉死 codex CONFIRMED 真 bug 的负例（pc-1/2/3/4/7），防回归。"""

    def test_pc2_round_must_not_rescue_below_target(self):
        """pc-2：base=9496,npu=10000,tgt=0.95 → raw=0.9496<0.95 → 达标 False（不被 round 成 0.95 救活）。"""
        cs = _caseset([("p", ["性能", "大shape"], [1024, 1024])])
        r = pc.perf_compare(_spec(0.95), cs, _ev({"p": (10000, "kernel_only")}), _bl({"p": 9496}))
        row = r["per_case"][0]
        self.assertFalse(row["达标"])                 # 关键：不再假通过
        self.assertEqual(row["ratio"], 0.95)          # 展示字段仍 round（但不参与达标判定）
        self.assertEqual(r["summary"]["status"], "fail")
        self.assertEqual(r["summary"]["达标"], 0)

    def test_pc2_boundary_raw_equal_target_is_met(self):
        """raw 恰等 tgt → 达标 True（边界不误杀）。"""
        cs = _caseset([("p", ["性能", "大shape"], [1024, 1024])])
        r = pc.perf_compare(_spec(0.95), cs, _ev({"p": (10000, "kernel_only")}), _bl({"p": 9500}))
        self.assertTrue(r["per_case"][0]["达标"])     # 0.95>=0.95

    def test_pc3_illegal_target_ratio_never_all_pass(self):
        """pc-3：target_ratio=0/-1/True/'0.95'/NaN → invalid_config，绝不全达标。"""
        cs = _caseset([("p", ["性能", "大shape"], [1024, 1024])])
        ev = _ev({"p": (10000, "kernel_only")})
        bl = _bl({"p": 20000})                        # raw=2.0，若阈非法误当 0/True 会全达标
        for bad in (0, -1, True, "0.95", float("nan")):
            r = pc.perf_compare(_spec(bad), cs, ev, bl)
            self.assertEqual(r["summary"]["status"], "invalid_config", f"target_ratio={bad!r}")
            self.assertEqual(r["summary"]["达标"], 0, f"target_ratio={bad!r} 不得全达标")
            self.assertTrue(r["per_case"][0]["blocked"])

    def test_pc3_missing_target_with_baseline_is_blocked(self):
        """声明基线却缺 target_ratio → invalid_config（拒静默套 0.95）。"""
        cs = _caseset([("p", ["性能", "大shape"], [1024, 1024])])
        spec = {"op": "Sign", "perf": {"baseline": "tbe"}}   # 有 baseline、无 target_ratio
        r = pc.perf_compare(spec, cs, _ev({"p": (1.0, "kernel_only")}), _bl({"p": 2.0}))
        self.assertEqual(r["summary"]["status"], "invalid_config")

    def test_pc4_both_scope_none_incomparable(self):
        """pc-4：双边 scope 均 None → blocked_incomparable_timing_scope（None!=None 不再放行）。"""
        cs = _caseset([("p", ["性能", "大shape"], [1024, 1024])])
        r = pc.perf_compare(_spec(0.95), cs, _ev({"p": (1.5, None)}), _bl({"p": 1.2}, scope=None))
        self.assertEqual(r["summary"]["status"], "blocked_incomparable_timing_scope")
        self.assertTrue(r["per_case"][0]["blocked"])

    def test_pc4_missing_scope_key_no_crash(self):
        """evidence 条目 perf 缺 scope 键 → 判不可比、绝不 KeyError 崩溃。"""
        cs = _caseset([("p", ["性能", "大shape"], [1024, 1024])])
        ev = {"op": "Sign", "evidence": [{"case_id": "p", "perf": {"us": 1.5}}]}  # 无 scope 键
        r = pc.perf_compare(_spec(0.95), cs, ev, _bl({"p": 1.2}))
        self.assertEqual(r["summary"]["status"], "blocked_incomparable_timing_scope")

    def test_pc7_bad_containers_structured_invalid_no_crash(self):
        """pc-7：caseset/evidence/baseline 缺字段/非 list/非 dict → 结构化 invalid，不抛异常。"""
        cs = _caseset([("p", ["性能"], [8])])
        ev = _ev({"p": (1.5, "kernel_only")})
        bl = _bl({"p": 1.2})
        spec = _spec(0.95)
        for label, args in [
            ("caseset 缺 cases", (spec, {}, ev, bl)),
            ("caseset.cases 非 list", (spec, {"cases": "x"}, ev, bl)),
            ("evidence 非 dict", (spec, cs, "notadict", bl)),
            ("evidence 缺 evidence", (spec, cs, {}, bl)),
            ("baseline 非 dict", (spec, cs, ev, "notadict")),
            ("baseline 缺 per_case", (spec, cs, ev, {})),
            ("spec 缺 op", ({"perf": {"baseline": "tbe", "target_ratio": 0.95}}, cs, ev, bl)),
        ]:
            r = pc.perf_compare(*args)               # 不得抛异常
            self.assertEqual(r["summary"]["status"], "invalid", label)
            self.assertEqual(r["summary"]["达标"], 0, label)

    def test_pc7_bad_entry_degrades_to_blocked_no_crash(self):
        """条目级坏（evidence 条目缺 perf、baseline 行缺 us）→ 该 case blocked，不崩。"""
        cs = _caseset([("p", ["性能", "大shape"], [1024, 1024])])
        ev = {"op": "Sign", "evidence": [{"case_id": "p"}]}        # 无 perf 键
        bl = {"source": "tbe", "scope": "kernel_only", "per_case": [{"case_id": "p"}]}  # 无 us
        r = pc.perf_compare(_spec(0.95), cs, ev, bl)
        self.assertTrue(r["summary"]["status"].startswith("blocked"))
        self.assertTrue(r["per_case"][0]["blocked"])

    def _main_run(self, extra):
        """写 spec/caseset/evidence 到临时文件，跑 pc.main(...)，回读产物 report。"""
        import json
        cs = _caseset([("p", ["性能", "大shape"], [1024, 1024])])
        d = tempfile.mkdtemp()
        sp, cp, ep, op = (os.path.join(d, n) for n in ("spec.json", "cs.json", "ev.json", "out.json"))
        for path, obj in ((sp, _spec(0.95)), (cp, cs), (ep, _ev({"p": (1.5, "kernel_only")}))):
            with open(path, "w", encoding="utf-8") as f:
                json.dump(obj, f)
        pc.main([sp, cp, ep, *extra, "--out", op])
        with open(op, encoding="utf-8") as f:
            return json.load(f)

    def test_pc1_main_missing_baseline_not_ok(self):
        """pc-1：main() 缺基线且无 --mock → 不产生 status=ok（走挂起）。"""
        rep = self._main_run([])                      # 无 baseline、无 --mock
        self.assertNotEqual(rep["summary"]["status"], "ok")
        self.assertTrue(rep["summary"]["status"].startswith("blocked"))

    def test_pc1_main_mock_flag_marks_untrustworthy(self):
        """--mock 显式启用时，产物带 baseline_mock 标（不可当真通过）。"""
        rep = self._main_run(["--mock"])
        self.assertTrue(rep["summary"].get("baseline_mock"))


if __name__ == "__main__":
    unittest.main()
