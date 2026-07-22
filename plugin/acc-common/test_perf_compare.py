"""perf_compare 单测（T6 小shape例外 + T8 GPU consumer）——stdlib unittest。

跑: python3 -m unittest test_perf_compare -v   （在 acc-common/ 下）
"""
import os, tempfile, unittest
import perf_compare as pc
import gen_cases
import gpu_baseline as gb
import _golden_fixture as _gf
setUpModule = _gf.install        # golden 去引擎化：gen_cases 需 <ops_root>/<op>/golden.py（ADR 0011）
tearDownModule = _gf.uninstall


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
    """小shape 例外逻辑直测（该逻辑仍存于 perf_compare）。⚠ §1 覆盖-预算 pipeline 已不再产「小shape」标签用例，
    且 trivial-met 会截住 numel<4096 的退化 case——故本类 fixture **刻意用 numel≥4096（[8192]）+ 小 us**
    绕开 trivial-met、直测例外逻辑本身（例外由「小shape」tag 驱动、非实际 shape）。该 e2e 路径已被 trivial-met
    取代，见 test_validate_acceptance_state.test_perf_trivial_met_small_shapes。"""
    def test_hit_exception(self):
        cs = _caseset([("s0", ["性能", "小shape"], [8192])])
        r = pc.perf_compare(_spec(1.0, _EXC), cs, _ev({"s0": (1.5, "kernel_only")}), _bl({"s0": 1.2}))
        self.assertEqual(r["summary"]["status"], "exception")
        row = r["per_case"][0]
        self.assertFalse(row["达标"])                      # 绝不偷偷置 True
        self.assertEqual(row["exception"], "small_shape")
        self.assertEqual(row["scope"], "kernel_only")       # 例外行仍带 kernel_only
        self.assertIn("simulation", r)
        self.assertEqual([p["case_id"] for p in r["simulation"]["points"]], ["s0"])

    def test_gap_over_tol_is_fail(self):
        cs = _caseset([("s0", ["性能", "小shape"], [8192])])
        r = pc.perf_compare(_spec(1.0, _EXC), cs, _ev({"s0": (6.0, "kernel_only")}), _bl({"s0": 1.0}))
        self.assertEqual(r["summary"]["status"], "fail")   # gap=5>3
        self.assertNotIn("exception", r["per_case"][0])

    def test_threshold_boundary_strict(self):
        """max(npu,base)==when_us_below → `<` 严格 → 不命中例外 → fail。"""
        cs = _caseset([("s0", ["性能", "小shape"], [8192])])
        r = pc.perf_compare(_spec(1.0, _EXC), cs, _ev({"s0": (10.0, "kernel_only")}), _bl({"s0": 8.0}))
        self.assertEqual(r["summary"]["status"], "fail")

    def test_non_smallshape_tag_not_exception(self):
        """非小shape-tag 但恰好 <阈 且 gap≤tol → 不误转例外。"""
        cs = _caseset([("s0", ["性能", "大shape"], [1024, 1024])])
        r = pc.perf_compare(_spec(1.0, _EXC), cs, _ev({"s0": (1.5, "kernel_only")}), _bl({"s0": 1.2}))
        self.assertEqual(r["summary"]["status"], "fail")
        self.assertNotIn("exception", r["per_case"][0])

    def test_mixed_pass_and_exception(self):
        cs = _caseset([("s0", ["性能", "小shape"], [8192]), ("b0", ["性能", "大shape"], [1024, 1024])])
        r = pc.perf_compare(_spec(1.0, _EXC), cs,
                            _ev({"s0": (1.5, "kernel_only"), "b0": (2.0, "kernel_only")}),
                            _bl({"s0": 1.2, "b0": 3.0}))   # b0 ratio 1.5 达标
        self.assertEqual(r["summary"]["status"], "exception")

    def test_genuine_fail_beats_exception(self):
        cs = _caseset([("s0", ["性能", "小shape"], [8192]), ("g0", ["性能", "大shape"], [1024, 1024])])
        r = pc.perf_compare(_spec(1.0, _EXC), cs,
                            _ev({"s0": (1.5, "kernel_only"), "g0": (6.0, "kernel_only")}),
                            _bl({"s0": 1.2, "g0": 1.0}))    # g0 genuine fail
        self.assertEqual(r["summary"]["status"], "fail")

    def test_scope_mismatch_incomparable(self):
        cs = _caseset([("s0", ["性能", "小shape"], [8192])])
        r = pc.perf_compare(_spec(1.0, _EXC), cs, _ev({"s0": (1.5, "device_e2e_no_h2d_d2h")}),
                            _bl({"s0": 1.2}, scope="kernel_only"))
        self.assertEqual(r["summary"]["status"], "blocked_incomparable_timing_scope")

    def test_illegal_numbers_blocked(self):
        cs = _caseset([("z", ["性能", "小shape"], [8192]), ("n", ["性能", "小shape"], [8192]),
                       ("i", ["性能", "小shape"], [8192]), ("u", ["性能", "小shape"], [8192])])
        ev = _ev({"z": (1.5, "kernel_only"), "n": (1.5, "kernel_only"),
                  "i": (1.5, "kernel_only"), "u": (None, "kernel_only")})
        bl = _bl({"z": 0, "n": -1.0, "i": float("inf"), "u": 1.2})
        r = pc.perf_compare(_spec(1.0, _EXC), cs, ev, bl)
        self.assertEqual(r["summary"]["status"], "blocked")
        self.assertEqual(r["summary"]["blocked"], 4)       # 0/负/inf/None 全 blocked、不进例外

    def test_disabled_when_no_exception_declared(self):
        cs = _caseset([("s0", ["性能", "小shape"], [8192])])
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
        cs = _caseset([("s0", ["性能", "小shape"], [8192])])
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


class MockBaselineIsNonAcceptanceTest(unittest.TestCase):
    """C5：假基线比出来的「达标」绝不能读起来像真达标。

    `mock_baseline` 造的是 NPU mock us × 1.08 的编造数——它当分母算出的 ratio 天然 ≥1、天然「达标」。
    本类钉死：凡消费 mock 基线的产物（基线本身 + perf_report 的**每一个出口**）都带
    `evidence_grade=development` + `acceptance_note` 含 NON-ACCEPTANCE；而真基线/外部 GPU 标杆一个戳都不许多。
    """
    def _cs_ev(self):
        return (_caseset([("b0", ["性能", "大shape"], [1024, 1024])]),
                _ev({"b0": (2.0, "kernel_only")}))

    def _assert_stamped(self, obj, label):
        self.assertEqual(obj.get("evidence_grade"), "development", label)
        self.assertIn("NON-ACCEPTANCE", obj.get("acceptance_note", ""), label)

    def test_mock_baseline_itself_stamped(self):
        """baseline.json 落盘后一眼可辨是假基线（不必先读 perf_report 才知道）。"""
        bl = pc.mock_baseline(_spec(), _ev({"a": (2.0, "kernel_only")}))
        self.assertTrue(bl["mock"])
        self._assert_stamped(bl, "mock_baseline 自身")

    def test_report_stamped_and_met_is_not_real_met(self):
        """正常出口：mock 基线 → 全达标（1.08≥1.0），但报告带 NON-ACCEPTANCE 戳 + summary.baseline_mock。"""
        cs, ev = self._cs_ev()
        r = pc.perf_compare(_spec(1.0), cs, ev, pc.mock_baseline(_spec(), ev))
        self.assertEqual(r["summary"]["status"], "ok")
        self.assertTrue(r["per_case"][0]["达标"])          # 假基线下「达标」是必然结果，不是结论
        self.assertTrue(r["summary"]["baseline_mock"])
        self._assert_stamped(r, "正常出口")
        self.assertTrue(any("mock 基线" in n for n in r["notes"]))

    def test_every_exit_stamped(self):
        """**每一条 return 都得盖戳**——漏一个出口就留一条「假基线报告看起来像真的」的缝。
        覆盖 invalid(_precheck) / no_perf_cases / invalid_config / 正常 四个出口。"""
        cs, ev = self._cs_ev()
        mock_bl = pc.mock_baseline(_spec(), ev)
        no_perf_cs = {"op": "Sign", "cases": [dict(cs["cases"][0], dims=["功能"])]}
        for label, args in (
                ("invalid(坏 evidence)", (_spec(1.0), cs, {"evidence": "bad"}, mock_bl)),
                ("no_perf_cases", (_spec(1.0), no_perf_cs, ev, mock_bl)),
                ("invalid_config", ({"op": "Sign", "perf": {"baseline": "tbe"}}, cs, ev, mock_bl)),
                ("ok", (_spec(1.0), cs, ev, mock_bl))):
            self._assert_stamped(pc.perf_compare(*args), label)

    def test_real_baseline_not_stamped(self):
        """真基线（无 mock 标）→ 报告**一个戳都不多**（真机通路不受本改动影响）。"""
        cs, ev = self._cs_ev()
        r = pc.perf_compare(_spec(1.0), cs, ev, _bl({"b0": 3.0}))
        self.assertNotIn("evidence_grade", r)
        self.assertNotIn("acceptance_note", r)
        self.assertNotIn("baseline_mock", r["summary"])

    def test_stamp_helper_idempotent(self):
        """反复盖戳不叠加 notes（run_workflow 会再补一手 setdefault，须幂等）。"""
        cs, ev = self._cs_ev()
        mock_bl = pc.mock_baseline(_spec(), ev)
        r = pc.perf_compare(_spec(1.0), cs, ev, mock_bl)
        n1 = list(r["notes"])
        pc._mark_non_acceptance(r, mock_bl)
        self.assertEqual(r["notes"], n1)


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
        # §trivial-met：退化 case（numel<4096）标 trivial、无 ratio；非退化 case 有 ratio。二者其一。
        self.assertTrue(all("ratio" in row or row.get("trivial") for row in r["per_case"]))
        self.assertTrue(any("ratio" in row for row in r["per_case"]), "应有非退化 case 真判 ratio")

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

    def test_gb9_baseline_none_routes_blocked_status(self):
        """gb-9：baseline=None 但携 blocked_incomparable 挂起码 → 该状态（不落 wait）。"""
        cs = _caseset([("p", ["性能", "大shape"], [1024, 1024])])
        r = pc.perf_compare(_spec(0.95), cs, _ev({"p": (1.5, "kernel_only")}), None,
                            expect_source="gpu_external",
                            baseline_blocked_status="blocked_incomparable_timing_scope")
        self.assertEqual(r["summary"]["status"], "blocked_incomparable_timing_scope")
        r2 = pc.perf_compare(_spec(0.95), cs, _ev({"p": (1.5, "kernel_only")}), None,
                             expect_source="gpu_external",
                             baseline_blocked_status="blocked_gpu_baseline_invalid")
        self.assertEqual(r2["summary"]["status"], "blocked_gpu_baseline_invalid")


class SharedConstantDriftTest(unittest.TestCase):
    """跨模块常量漂移守卫（T4-④ 调查结论：不抽匹配逻辑，只钉共享常量）。

    perf_compare 与 gpu_baseline 各自独立定义 timing_scope 三元集；若两处不同步演化，
    会出现 gpu_baseline 判「baseline 合法」而 perf_compare 判 BLOCKED_INCOMPARABLE_SCOPE
    的自相矛盾。此测试断言两常量恒等，低成本兜住该漂移（无需重构 join 逻辑）。
    """

    def test_timing_scope_sets_identical_across_modules(self):
        self.assertEqual(
            pc._VALID_SCOPES, gb._SCOPES,
            "perf_compare._VALID_SCOPES 与 gpu_baseline._SCOPES 漂移了——"
            "timing_scope 枚举须单一事实、两处同步（改一处必改另一处）")

    def test_scope_transfer_keys_cover_valid_scopes(self):
        # gpu_baseline 的 H2D/D2H 判据表须恰好覆盖合法 scope 全集（漏 key→KeyError 假挂）
        self.assertEqual(set(gb._SCOPE_TRANSFER), gb._SCOPES,
                         "_SCOPE_TRANSFER 的 key 集须与 _SCOPES 恰好一致")


class TrivialMetTest(unittest.TestCase):
    """§trivial-met（用户 2026-07-15，评审 #2）：退化 case（numel<4096）达标、免测、无需基线/scope/us；
    perf 达标由代表性大 shape 主导。"""
    def test_trivial_case_met_without_baseline(self):
        cs = _caseset([("t0", ["性能", "常规"], [16])])       # numel 16 < 4096 → trivial
        r = pc.perf_compare(_spec(1.0), cs, _ev({"t0": (1.5, "kernel_only")}), _bl({}))  # 基线无 t0
        row = r["per_case"][0]
        self.assertTrue(row["达标"])
        self.assertTrue(row.get("trivial"))
        self.assertEqual(r["summary"]["status"], "ok")

    def test_large_case_not_trivial(self):
        cs = _caseset([("b0", ["性能", "大shape"], [128, 128])])  # numel 16384 ≥ 4096 → 真判
        r = pc.perf_compare(_spec(1.0), cs, _ev({"b0": (1.0, "kernel_only")}), _bl({"b0": 2.0}))
        row = r["per_case"][0]
        self.assertNotIn("trivial", row)
        self.assertIn("ratio", row)


class RunWorkflowNonAcceptanceSurfaceTest(unittest.TestCase):
    """C5 · run_workflow 侧的**入口面**回归（放这里是因为本轮只有本测试文件归本改动所有）。

    只测不需要真跑 pipeline 的部分——`--defect` 是否真从 CLI 上消失、注入夹具会不会误伤验收通路、
    非验收产物名是否与验收产物物理隔离。端到端那半（mock 跑完产 dev_run_summary.json 而非 acceptance.json）
    要 golden，本机无 torch 跑不了，留给 a3 容器。"""
    _HERE = os.path.dirname(os.path.abspath(__file__))

    def test_defect_flag_removed_from_cli(self):
        """`--defect` 已不是 CLI 参数：argparse 直接拒（退出码 2 = argparse 用法错）。
        ⚠ 别因为「调试方便」把它加回来——回归测试请走进程内 `run_workflow.run(..., defect=[...])`。"""
        import subprocess, sys as _sys
        r = subprocess.run([_sys.executable, os.path.join(self._HERE, "run_workflow.py"),
                            "nonexistent.spec.json", "--mode", "mock", "--defect", "x"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("--defect", r.stderr)          # argparse 的 "unrecognized arguments: --defect"

    def test_defect_fixture_still_reachable_in_process(self):
        """夹具本身**保留**：`run()` 仍收 defect 形参（证明「validator 真会 fail」的回归能力没被删掉）。"""
        import inspect
        import run_workflow as W
        self.assertIn("defect", inspect.signature(W.run).parameters)

    def test_injection_fixtures_rejected_on_acceptance_path(self):
        """注入夹具作用于验收通路 → fail-closed 直接拒跑（不靠下游「反正会忽略」的沉默）。"""
        import run_workflow as W
        for kw in ({"defect": ["c0"]}, {"perf_slow": ["c0"]}):
            with self.assertRaises(SystemExit):
                W.run("nonexistent.spec.json", mode="new_example", **kw)

    def test_dev_artifact_names_physically_disjoint(self):
        """非验收产物名与验收产物名不得重合——同名就等于下游按老路径能读走当裁决。"""
        import run_workflow as W
        self.assertFalse(set(W._DEV_FILES) & set(W._ACCEPTANCE_FILES))
        self.assertEqual(W._ACCEPTANCE_FILES, ("acceptance.json", "verdict.json"))

    def test_acceptance_capable_is_fail_closed(self):
        """只有真机通路算验收；mock / catlass_mock / 没登记过的新模式一律非验收。"""
        import run_workflow as W
        self.assertTrue(W._acceptance_capable("new_example"))
        for m in ("mock", "catlass_mock", "catlass", "some_future_mode"):
            self.assertFalse(W._acceptance_capable(m), m)

    def test_stamp_dev_marks_only_non_acceptance(self):
        import run_workflow as W
        dev = W._stamp_dev({"summary": {}}, False, "development")
        self.assertEqual(dev["evidence_grade"], "development")
        self.assertIn("NON-ACCEPTANCE", dev["acceptance_note"])
        acc = W._stamp_dev({"summary": {}}, True, "acceptance_candidate")
        self.assertNotIn("acceptance_note", acc)     # 验收通路一个字节不动
        # 幂等 + 不覆盖 perf_compare 已写的措辞
        pre = {"acceptance_note": "已有措辞", "evidence_grade": "development"}
        self.assertEqual(W._stamp_dev(pre, False, "development")["acceptance_note"], "已有措辞")

    def test_dev_and_acceptance_notes_share_the_marker(self):
        """两处的戳用同一个标记词（catlass_adapter 已有口径），别各写各的。"""
        import run_workflow as W
        for note in (W._NON_ACCEPTANCE_NOTE, pc._NON_ACCEPTANCE_NOTE):
            self.assertIn("NON-ACCEPTANCE (mock evidence)", note)
        self.assertEqual(W._DEV_GRADE, pc._DEV_GRADE)

class ScaledCaseNotTrivialMetTest(unittest.TestCase):
    """G4 连带闸：被 golden 规模预算**降过规模**的 case，不得走 trivial-met 冒充达标。

    trivial-met 的正当性是「这个 case 本来就小、perf 没意义」；
    降规模 case 是「它本来很大、我们没按目标规模跑」——**没测却算过**，两者性质完全不同。
    不拦的话：G4 把大 shape 降到阈值以下 → 这里判达标 → 报告显示性能通过，而目标规模一次没跑。"""

    @staticmethod
    def _cs(cid, shape, scaled):
        cs = _caseset([(cid, ["常规"], shape)])
        cs["cases"][0]["expected"] = {"golden_path": f"{cid}/golden.npy"}
        if scaled:
            cs["cases"][0]["expected"]["cost_scaled"] = {
                "from": [1024, 1024], "to": list(shape), "reason": "golden 规模预算"}
        return cs

    def test_scaled_small_case_is_blocked_not_trivial_met(self):
        r = pc.perf_compare(_spec(1.0), self._cs("s0", [8, 8], True), _ev({}), _bl({}))
        row = next(x for x in r["per_case"] if x["case_id"] == "s0")
        self.assertFalse(row["达标"], row)
        self.assertTrue(row.get("blocked"), row)
        self.assertNotIn("trivial", row)
        self.assertIn("cost_scaled", row)

    def test_genuinely_small_case_still_trivial_met(self):
        """对照：**没被降过规模**的小 case 仍走 trivial-met，证补的闸没矫枉过正。"""
        r = pc.perf_compare(_spec(1.0), self._cs("s0", [8, 8], False), _ev({}), _bl({}))
        row = next(x for x in r["per_case"] if x["case_id"] == "s0")
        self.assertTrue(row["达标"], row)
        self.assertTrue(row.get("trivial"), row)


if __name__ == "__main__":
    unittest.main()
