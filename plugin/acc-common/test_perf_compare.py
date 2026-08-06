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
    """小shape 例外逻辑直测（例外由「小shape」tag 与实测 us 驱动，不按 numel 自动免测）。"""
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
        # ⚠ 经 `_spec_fixture` 读：`gpu_demo.spec.json` 已于 2026-08-06 删掉历史沿用的
        #   `case_target: 50`（gen_cases 缺省值的化石），预算由测试侧注入（夹具值、非依据）。
        import _spec_fixture
        spec = _spec_fixture.load(spec_path)
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
        self.assertTrue(all("ratio" in row for row in r["per_case"]),
                        "所有性能 case（包括小 numel）都应以真实双边计时计算 ratio")

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


class SmallCaseMeasuredTest(unittest.TestCase):
    """小 numel 与大 shape 走同一真实性能判定；不存在自动免测。"""
    def test_small_case_without_baseline_is_blocked(self):
        cs = _caseset([("t0", ["性能", "常规"], [16])])
        r = pc.perf_compare(_spec(1.0), cs, _ev({"t0": (1.5, "kernel_only")}), _bl({}))
        row = r["per_case"][0]
        self.assertFalse(row["达标"])
        self.assertTrue(row.get("blocked"))
        self.assertNotIn("trivial", row)
        self.assertEqual(r["summary"]["status"], "blocked")

    def test_small_case_with_baseline_is_scored(self):
        cs = _caseset([("t0", ["性能", "常规"], [1])])
        r = pc.perf_compare(_spec(1.0), cs, _ev({"t0": (1.0, "kernel_only")}), _bl({"t0": 2.0}))
        row = r["per_case"][0]
        self.assertNotIn("trivial", row)
        self.assertEqual(row["ratio"], 2.0)
        self.assertTrue(row["达标"])
        self.assertEqual(r["summary"]["cases_scored"], 1)


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

class ScaledCaseMeasuredTest(unittest.TestCase):
    """移除 trivial 后，cost_scaled 不再触发额外免测或自动通过，仍按实际双边证据判定。"""

    @staticmethod
    def _cs(cid, shape, scaled):
        cs = _caseset([(cid, ["常规"], shape)])
        cs["cases"][0]["expected"] = {"golden_path": f"{cid}/golden.npy"}
        if scaled:
            cs["cases"][0]["expected"]["cost_scaled"] = {
                "from": [1024, 1024], "to": list(shape), "reason": "golden 规模预算"}
        return cs

    def test_scaled_case_without_measurement_is_blocked(self):
        r = pc.perf_compare(_spec(1.0), self._cs("s0", [8, 8], True), _ev({}), _bl({}))
        row = next(x for x in r["per_case"] if x["case_id"] == "s0")
        self.assertFalse(row["达标"], row)
        self.assertTrue(row.get("blocked"), row)
        self.assertNotIn("trivial", row)

    def test_scaled_small_case_with_measurement_is_scored(self):
        r = pc.perf_compare(
            _spec(1.0), self._cs("s0", [8, 8], True),
            _ev({"s0": (1.0, "kernel_only")}), _bl({"s0": 2.0}))
        row = next(x for x in r["per_case"] if x["case_id"] == "s0")
        self.assertTrue(row["达标"], row)
        self.assertEqual(row["ratio"], 2.0)
        self.assertNotIn("trivial", row)


class PerfReportAggregateTest(unittest.TestCase):
    """M3 · cannbot 报告三件套：`by_dtype` / `overall_speedup` / `cases_above_threshold`+`cases_scored`
    （+ `custom_only_by_dtype`）。对标
    `repos/cannbot-ops-input/skills/operator-evaluation/scripts/performance.py`
    的 `summarize_latency`(34-59) / `build_performance_report`(98-112) / `count_speedup_above`(80-95)
    / `summarize_custom_only_latency`(62-77)。

    本类钉两件事：① 聚合口径与 cannbot 一致；② **聚合没碰裁决**——凡断言里同时出现
    `达标`/`status` 的，都是在守「只读增量」这条线。
    """

    _BIG = [128, 128]

    @classmethod
    def _cs(cls, rows):
        """rows: [(cid, dtype)] → 只含性能维、统一大 shape 的 caseset。
        dtype 传 None = 把 `inputs[0].dtype` 抠掉，用来造「取不到 dtype」的行（应归 unknown 桶）。"""
        cs = _caseset([(cid, ["性能", "大shape"], cls._BIG) for cid, _ in rows])
        for case, (_, dt) in zip(cs["cases"], rows):
            if dt is None:
                case["inputs"][0].pop("dtype")
            else:
                case["inputs"][0]["dtype"] = dt
        return cs

    def test_by_dtype_median_is_statistics_median_not_lower_middle(self):
        """偶数个样本取**中间两数的平均**（= cannbot `median_us` 用的 statistics.median）。
        样本 [1,2,3,4]：取下中位会得 2.0，正确答案是 2.5——本断言就是冲这个差来的。"""
        cs = self._cs([("c0", "float32"), ("c1", "float32"), ("c2", "float32"), ("c3", "float32")])
        ev = _ev({"c0": (1.0, "kernel_only"), "c1": (2.0, "kernel_only"),
                  "c2": (3.0, "kernel_only"), "c3": (4.0, "kernel_only")})
        r = pc.perf_compare(_spec(0.5), cs, ev,
                            _bl({"c0": 10.0, "c1": 10.0, "c2": 10.0, "c3": 10.0}))
        self.assertEqual(r["by_dtype"], [{"dtype": "float32", "count": 4,
                                          "npu_us": 2.5, "baseline_us": 10.0, "speedup": 4.0}])
        self.assertEqual(r["overall_speedup"], 4.0)
        self.assertEqual(r["summary"]["status"], "ok")           # 裁决不受聚合影响

    def test_by_dtype_groups_sorted_and_unknown_bucket(self):
        """按 dtype 分组、按 dtype 名排序（产物稳定可 diff）；取不到 dtype 的行归 unknown、**不丢行**。"""
        cs = self._cs([("a", "float16"), ("b", "float32"), ("c", None)])
        ev = _ev({"a": (2.0, "kernel_only"), "b": (2.0, "kernel_only"), "c": (2.0, "kernel_only")})
        r = pc.perf_compare(_spec(0.5), cs, ev, _bl({"a": 4.0, "b": 4.0, "c": 4.0}))
        self.assertEqual([g["dtype"] for g in r["by_dtype"]], ["float16", "float32", "unknown"])
        self.assertEqual([g["count"] for g in r["by_dtype"]], [1, 1, 1])
        self.assertEqual(r["summary"]["cases_scored"], 3)        # 3 行都进了分母，一行没丢

    def test_overall_speedup_is_count_weighted_not_mean_of_speedups(self):
        """Σ(baseline median×count)/Σ(npu median×count)——**按 count 加权**，不是各 dtype speedup 求平均。"""
        cs = self._cs([("h0", "float16"), ("h1", "float16"), ("h2", "float16"), ("f0", "float32")])
        ev = _ev({"h0": (2.0, "kernel_only"), "h1": (2.0, "kernel_only"),
                  "h2": (2.0, "kernel_only"), "f0": (1.0, "kernel_only")})
        r = pc.perf_compare(_spec(0.5), cs, ev, _bl({"h0": 4.0, "h1": 4.0, "h2": 4.0, "f0": 8.0}))
        by = {g["dtype"]: g for g in r["by_dtype"]}
        self.assertEqual((by["float16"]["count"], by["float16"]["speedup"]), (3, 2.0))
        self.assertEqual((by["float32"]["count"], by["float32"]["speedup"]), (1, 8.0))
        # (4×3 + 8×1)/(2×3 + 1×1) = 20/7 ≈ 2.857；写成「speedup 求平均」会得 (2+8)/2 = 5。
        self.assertAlmostEqual(r["overall_speedup"], 20.0 / 7.0, places=12)
        self.assertNotAlmostEqual(r["overall_speedup"], 5.0, places=3)

    def test_ratio_equal_threshold_meets_hard_gate_but_not_above_threshold(self):
        """⚠ 两把尺子的口径差（蓝本 L1 裁决）——同一个 case 两个答案，且**两个都对**：
        硬门 `raw >= tgt` 判**达标**；展示口径 `raw > tgt`（cannbot count_speedup_above）**不计入**。
        谁要是「顺手统一」成一把尺子，本条会当场炸。"""
        cs = self._cs([("eq", "float32")])
        r = pc.perf_compare(_spec(0.95), cs, _ev({"eq": (10000, "kernel_only")}), _bl({"eq": 9500}))
        row = r["per_case"][0]
        self.assertEqual(row["ratio"], 0.95)
        self.assertTrue(row["达标"], "硬门 >= 必须仍判达标（这一条一个字都不许动）")
        self.assertEqual(r["summary"]["达标"], 1)
        self.assertEqual(r["summary"]["status"], "ok")
        self.assertEqual(r["summary"]["cases_scored"], 1)
        self.assertEqual(r["summary"]["cases_above_threshold"], 0, "严格 > 下恰等阈值不计入")

    def test_above_threshold_uses_raw_ratio_not_rounded_display_value(self):
        """严格 `>` 比的是重算的**原始比**，不是 `row["ratio"]` 那个 round 过的展示值。
        hi: raw=0.9504>0.95 → 计入（若误用 round 后的 0.95，严格 > 会漏计）；
        lo: raw=0.9496<0.95 → 不达标也不计入（round 后同样是 0.95，不许被救活，pc-2 同型）。"""
        cs = self._cs([("hi", "float32"), ("lo", "float32")])
        ev = _ev({"hi": (10000, "kernel_only"), "lo": (10000, "kernel_only")})
        r = pc.perf_compare(_spec(0.95), cs, ev, _bl({"hi": 9504, "lo": 9496}))
        by_id = {x["case_id"]: x for x in r["per_case"]}
        self.assertEqual((by_id["hi"]["ratio"], by_id["lo"]["ratio"]), (0.95, 0.95))  # 展示值都是 0.95
        self.assertTrue(by_id["hi"]["达标"])
        self.assertFalse(by_id["lo"]["达标"])
        self.assertEqual(r["summary"]["cases_scored"], 2)
        self.assertEqual(r["summary"]["cases_above_threshold"], 1)

    def test_small_measured_and_missing_baseline_rows_aggregate_correctly(self):
        """没有可比测量的行**不进** by_dtype/cases_scored（塞进 median 就是编数字）；
        小 shape 只要双边量到就正常进入聚合；
        「量到了但根本没基线」的行走 custom_only_by_dtype——只报绝对时延、**不硬算 speedup**
        （cannbot `summarize_custom_only_latency` 同款纪律）。"""
        cs = _caseset([("t0", ["性能", "常规"], [16]),            # 小 shape：仍须真实比较
                       ("m0", ["性能", "大shape"], [128, 128]),   # 有 npu 计时、无基线条目
                       ("g0", ["性能", "大shape"], [128, 128])])
        ev = _ev({"t0": (1.5, "kernel_only"), "m0": (4.0, "kernel_only"), "g0": (2.0, "kernel_only")})
        r = pc.perf_compare(_spec(0.5), cs, ev, _bl({"t0": 3.0, "g0": 4.0}))
        self.assertEqual(r["summary"]["status"], "blocked")       # 裁决照旧（m0 缺基线）
        self.assertEqual(r["summary"]["cases_scored"], 2)
        self.assertEqual(r["by_dtype"], [{"dtype": "float32", "count": 2,
                                          "npu_us": 1.75, "baseline_us": 3.5, "speedup": 2.0}])
        # 这一行**没有** speedup 键：无基线时不许推出加速比
        self.assertEqual(r["custom_only_by_dtype"],
                         [{"dtype": "float32", "count": 1, "npu_us": 4.0,
                           "comparison": "no_npu_baseline"}])

    def test_size_classes_are_reported_without_changing_verdict(self):
        cs = _caseset([("s0", ["性能", "小shape"], [16]),
                       ("b0", ["性能", "大shape"], [128, 128])])
        for case, cls, nbytes in zip(cs["cases"], ("small", "large"), (64, 65536)):
            case["perf_shape_classification"] = {
                "class": cls, "input_bytes": nbytes, "metric": "sum_input_bytes",
                "small_max_bytes": 262144, "boundary": "small_if_input_bytes_lte_limit",
                "hardware": "Atlas A3"}
        cs["perf_case_policy"] = {
            "case_source": "precision_cases",
            "shape_classification": {"metric": "sum_input_bytes", "small_max_bytes": 262144,
                                     "boundary": "small_if_input_bytes_lte_limit",
                                     "hardware": "Atlas A3"},
            "counts": {"small": 1, "large": 1}}
        r = pc.perf_compare(
            _spec(1.0), cs,
            _ev({"s0": (2.0, "kernel_only"), "b0": (4.0, "kernel_only")}),
            _bl({"s0": 2.0, "b0": 8.0}))
        self.assertEqual(r["summary"]["status"], "ok")
        self.assertEqual(r["summary"]["达标"], 2)
        self.assertEqual(
            r["by_shape_class"],
            [{"class": "small", "cases": 1, "planned_cases": 1,
              "cases_scored": 1, "达标": 1, "blocked": 0,
              "npu_us": 2.0, "baseline_us": 2.0, "speedup": 1.0},
             {"class": "large", "cases": 1, "planned_cases": 1,
              "cases_scored": 1, "达标": 1, "blocked": 0,
              "npu_us": 4.0, "baseline_us": 8.0, "speedup": 2.0}])
        self.assertEqual(
            r["shape_overall"],
            {"class": "overall", "cases": 2, "planned_cases": 2, "cases_scored": 2,
             "达标": 2, "blocked": 0, "npu_us": 3.0, "baseline_us": 5.0,
             "speedup": 5.0 / 3.0})
        self.assertTrue(r["shape_report_complete"])

    def test_declared_shape_policy_never_silently_drops_unclassified_case(self):
        cs = _caseset([("s0", ["性能", "小shape"], [16]),
                       ("lost", ["性能"], [128, 128])])
        cs["perf_case_policy"] = {
            "case_source": "precision_cases",
            "shape_classification": {"metric": "sum_input_bytes", "small_max_bytes": 262144,
                                     "boundary": "small_if_input_bytes_lte_limit",
                                     "hardware": "Atlas A3"},
            "counts": {"small": 1, "large": 1}}
        cs["cases"][0]["perf_shape_classification"] = {
            "class": "small", "input_bytes": 64, "metric": "sum_input_bytes",
            "small_max_bytes": 262144, "boundary": "small_if_input_bytes_lte_limit",
            "hardware": "Atlas A3"}
        r = pc.perf_compare(
            _spec(1.0), cs,
            _ev({"s0": (2.0, "kernel_only"), "lost": (4.0, "kernel_only")}),
            _bl({"s0": 2.0, "lost": 4.0}))
        self.assertEqual(r["summary"]["status"], "ok")  # 只读汇总不重判
        self.assertFalse(r["shape_report_complete"])
        self.assertTrue(any("lost" in x for x in r["shape_report_problems"]))
        self.assertEqual(r["shape_overall"]["planned_cases"], 1)  # 漏行被显式报告，不冒充全量

    def test_partial_manual_classification_without_policy_produces_no_shape_report(self):
        cs = _caseset([("tagged", ["性能"], [16]), ("untagged", ["性能"], [32])])
        cs["cases"][0]["perf_shape_classification"] = {
            "class": "small", "input_bytes": 64, "metric": "sum_input_bytes",
            "small_max_bytes": 262144, "boundary": "small_if_input_bytes_lte_limit",
            "hardware": "Atlas A3"}
        r = pc.perf_compare(
            _spec(1.0), cs,
            _ev({"tagged": (2.0, "kernel_only"), "untagged": (3.0, "kernel_only")}),
            _bl({"tagged": 2.0, "untagged": 3.0}))
        self.assertNotIn("by_shape_class", r)
        self.assertNotIn("shape_overall", r)

    def test_waiting_for_baseline_still_reports_known_shape_and_npu_time(self):
        cs = _caseset([("s0", ["精度", "性能", "小shape"], [16])])
        cs["cases"][0]["perf_shape_classification"] = {
            "class": "small", "input_bytes": 64, "metric": "sum_input_bytes",
            "small_max_bytes": 262144, "boundary": "small_if_input_bytes_lte_limit",
            "hardware": "Atlas A3"}
        cs["perf_case_policy"] = {
            "case_source": "precision_cases",
            "shape_classification": {"metric": "sum_input_bytes", "small_max_bytes": 262144,
                                     "boundary": "small_if_input_bytes_lte_limit",
                                     "hardware": "Atlas A3"},
            "counts": {"small": 1, "large": 0}}
        r = pc.perf_compare(
            _spec(1.0, baseline="gpu_external"), cs,
            _ev({"s0": (4.0, "kernel_only")}), None, expect_source="gpu_external")
        self.assertEqual(r["summary"]["status"], "blocked_wait_gpu_benchmark")
        self.assertEqual(r["shape_overall"]["planned_cases"], 1)
        self.assertEqual(r["shape_overall"]["npu_us"], 4.0)
        self.assertIsNone(r["shape_overall"]["baseline_us"])
        self.assertIsNone(r["shape_overall"]["speedup"])

    def test_partial_baseline_keeps_all_known_npu_times_in_shape_report(self):
        cs = _caseset([("s0", ["精度", "性能"], [16]),
                       ("s1", ["精度", "性能"], [32])])
        for case, nbytes in zip(cs["cases"], (64, 128)):
            case["perf_shape_classification"] = {
                "class": "small", "input_bytes": nbytes, "metric": "sum_input_bytes",
                "small_max_bytes": 262144, "boundary": "small_if_input_bytes_lte_limit",
                "hardware": "Atlas A3"}
        cs["perf_case_policy"] = {
            "case_source": "precision_cases",
            "shape_classification": {"metric": "sum_input_bytes", "small_max_bytes": 262144,
                                     "boundary": "small_if_input_bytes_lte_limit",
                                     "hardware": "Atlas A3"},
            "counts": {"small": 2, "large": 0}}
        r = pc.perf_compare(
            _spec(1.0), cs,
            _ev({"s0": (2.0, "kernel_only"), "s1": (10.0, "kernel_only")}),
            _bl({"s0": 4.0}))
        self.assertEqual(r["summary"]["status"], "blocked")
        by_id = {row["case_id"]: row for row in r["per_case"]}
        self.assertEqual(by_id["s1"]["npu_us"], 10.0)
        self.assertEqual(by_id["s1"]["npu_scope"], "kernel_only")
        self.assertEqual(r["shape_overall"]["npu_us"], 6.0)
        self.assertEqual(r["shape_overall"]["baseline_us"], 4.0)
        self.assertEqual(r["shape_overall"]["cases_scored"], 1)
        self.assertEqual(r["shape_overall"]["blocked"], 1)

    def test_no_comparable_row_yields_empty_aggregate_not_fabricated_numbers(self):
        """一行可比测量都没有 → by_dtype 空、overall_speedup None（分母为 0 绝不编数）、计数为 0、不炸。
        另钉：有基线只是 scope 不可比 ≠ 无基线，不许混进 custom_only（那标签会撒谎）。"""
        cs = self._cs([("p", "float32")])
        r = pc.perf_compare(_spec(0.95), cs, _ev({"p": (1.5, "device_e2e_no_h2d_d2h")}),
                            _bl({"p": 1.2}, scope="kernel_only"))
        self.assertEqual(r["summary"]["status"], "blocked_incomparable_timing_scope")
        self.assertEqual(r["by_dtype"], [])
        self.assertIsNone(r["overall_speedup"])
        self.assertEqual((r["summary"]["cases_scored"], r["summary"]["cases_above_threshold"]), (0, 0))
        self.assertNotIn("custom_only_by_dtype", r)

    def test_early_exits_carry_no_aggregate_fields(self):
        """四个提前出口（invalid / no_perf_cases / invalid_config / 缺基线挂起）**一个新键都不加**——
        它们按定义没有可比测量，挂空聚合会让「没数据」看起来像「数据是 0」。下游读这些键须当可选。"""
        cs = self._cs([("p", "float32")])
        ev, bl = _ev({"p": (1.5, "kernel_only")}), _bl({"p": 1.2})
        no_perf_cs = {"op": "Sign", "cases": [dict(cs["cases"][0], dims=["功能"])]}
        for label, args in (("invalid", (_spec(0.95), {"cases": "x"}, ev, bl)),
                            ("no_perf_cases", (_spec(0.95), no_perf_cs, ev, bl)),
                            ("invalid_config", ({"op": "Sign", "perf": {"baseline": "tbe"}}, cs, ev, bl)),
                            ("blocked_wait", (_spec(0.95), cs, ev, None))):
            r = pc.perf_compare(*args)
            for key in ("by_dtype", "overall_speedup", "custom_only_by_dtype"):
                self.assertNotIn(key, r, f"{label}/{key}")
            for key in ("cases_above_threshold", "cases_scored"):
                self.assertNotIn(key, r["summary"], f"{label}/{key}")

    def test_summary_adds_aggregate_and_non_passing_counts_without_losing_canonical_keys(self):
        """既有四个裁决键的名和值不动；展示层另带 scored 与未通过明细计数。"""
        cs = self._cs([("p", "float32")])
        r = pc.perf_compare(_spec(0.5), cs, _ev({"p": (1.0, "kernel_only")}), _bl({"p": 2.0}))
        self.assertEqual(set(r["summary"]),
                         {"perf_cases", "达标", "blocked", "status",
                          "cases_above_threshold", "cases_scored",
                          "non_passing", "failed", "exceptions"})
        self.assertEqual((r["summary"]["perf_cases"], r["summary"]["达标"],
                          r["summary"]["blocked"], r["summary"]["status"]), (1, 1, 0, "ok"))
        self.assertEqual(r["non_passing_cases"], [])

    def test_failed_case_is_explicit_with_dtype_shape_class_and_both_sides(self):
        cs = self._cs([("slow", "bfloat16")])
        cs["cases"][0]["perf_shape_classification"] = {
            "class": "large", "input_bytes": 2 * 128 * 128,
            "metric": "sum_input_bytes", "small_max_bytes": 262144,
            "boundary": "small_if_input_bytes_lte_limit", "hardware": "Atlas A3"}
        r = pc.perf_compare(
            _spec(1.0), cs,
            {"op": "Sign", "evidence": [{
                "case_id": "slow",
                "perf": {"us": 10.0, "scope": "kernel_only",
                         "behavior": "npu", "execution_path": "device_kernel"},
            }]},
            _bl({"slow": 8.0}))
        self.assertEqual(r["summary"]["status"], "fail")
        self.assertEqual(
            (r["summary"]["non_passing"], r["summary"]["failed"], r["summary"]["exceptions"]),
            (1, 1, 0))
        item = r["non_passing_cases"][0]
        self.assertEqual(
            (item["case_id"], item["dtype"], item["shape_class"], item["input_bytes"]),
            ("slow", "bfloat16", "large", 32768))
        self.assertEqual(item["inputs"], [{"name": "self", "shape": [128, 128]}])
        self.assertEqual(item["outcome"], "failed")
        self.assertEqual((item["custom"]["behavior"], item["custom"]["us"]), ("npu", 10.0))
        self.assertEqual(item["baseline"]["us"], 8.0)
        self.assertIn("target_ratio", item["reason"])

    def test_baseline_execution_failure_reason_is_not_lost_as_plain_missing(self):
        cs = self._cs([("unsupported", "bfloat16")])
        ev = {"op": "Sign", "evidence": [{
            "case_id": "unsupported",
            "perf": {"us": 5.0, "scope": "kernel_only",
                     "behavior": "npu", "execution_path": "device_kernel"},
        }]}
        baseline = _bl({})
        baseline["excluded"] = [{
            "case_id": "unsupported",
            "behavior": "execution_failed",
            "reason": "torch_npu returned ACL error 161002",
        }]
        r = pc.perf_compare(_spec(1.0), cs, ev, baseline)
        self.assertEqual(r["summary"]["status"], "blocked")
        item = r["non_passing_cases"][0]
        self.assertEqual(item["outcome"], "blocked")
        self.assertEqual(item["baseline"]["behavior"], "execution_failed")
        self.assertIn("161002", item["baseline"]["reason"])

    def test_waiting_baseline_cases_are_all_recorded_as_blocked(self):
        cs = self._cs([("p0", "float16"), ("p1", "float32")])
        ev = _ev({"p0": (1.0, "kernel_only"), "p1": (2.0, "kernel_only")})
        r = pc.perf_compare(
            _spec(1.0, baseline="gpu_external"), cs, ev, None,
            expect_source="gpu_external")
        self.assertEqual(r["summary"]["status"], "blocked_wait_gpu_benchmark")
        self.assertEqual(r["summary"]["non_passing"], 2)
        self.assertEqual(
            {item["case_id"] for item in r["non_passing_cases"]}, {"p0", "p1"})
        self.assertTrue(all(item["outcome"] == "blocked" for item in r["non_passing_cases"]))

    def test_aggregate_failure_degrades_without_touching_the_verdict(self):
        """只读报表塌了 → 整块不出 + notes 记一笔，**裁决一字不变**
        （绝不允许「一块好看的统计」把结论炸掉或改写；与 validator L3 聚合同一条纪律）。"""
        def boom(*a, **k):
            raise RuntimeError("boom")
        cs = self._cs([("p", "float32")])
        orig, pc._report_aggregate = pc._report_aggregate, boom
        try:
            r = pc.perf_compare(_spec(0.5), cs, _ev({"p": (1.0, "kernel_only")}), _bl({"p": 2.0}))
        finally:
            pc._report_aggregate = orig
        self.assertEqual(r["summary"]["status"], "ok")
        self.assertEqual((r["summary"]["perf_cases"], r["summary"]["达标"]), (1, 1))
        self.assertNotIn("by_dtype", r)
        self.assertNotIn("overall_speedup", r)
        self.assertNotIn("cases_scored", r["summary"])
        self.assertTrue(any("报告聚合块" in n for n in r["notes"]), r["notes"])

    def test_exception_rows_are_scored_but_never_above_threshold(self):
        """小shape 例外行是**真量到的可比测量** → 进 cases_scored/by_dtype；但它按定义 ratio<阈，
        既不计入 cases_above_threshold，`达标` 也仍是 False（例外绝不偷偷置 True）。"""
        cs = _caseset([("s0", ["性能", "小shape"], [8192])])
        r = pc.perf_compare(_spec(1.0, _EXC), cs, _ev({"s0": (1.5, "kernel_only")}), _bl({"s0": 1.2}))
        self.assertEqual(r["summary"]["status"], "exception")
        self.assertFalse(r["per_case"][0]["达标"])
        self.assertEqual(r["summary"]["cases_scored"], 1)
        self.assertEqual(r["summary"]["cases_above_threshold"], 0)
        self.assertEqual(r["by_dtype"][0]["count"], 1)

    def test_mock_baseline_aggregate_still_carries_non_acceptance_stamp(self):
        """假基线照样能算出 by_dtype/overall_speedup——同一份报告上的 NON-ACCEPTANCE 戳必须还在，
        否则这些聚合数会被读成真性能结论（C5）。"""
        cs = self._cs([("b0", "float32")])
        ev = _ev({"b0": (2.0, "kernel_only")})
        r = pc.perf_compare(_spec(1.0), cs, ev, pc.mock_baseline(_spec(), ev))
        self.assertIsNotNone(r["overall_speedup"])
        self.assertEqual(r.get("evidence_grade"), "development")
        self.assertIn("NON-ACCEPTANCE", r.get("acceptance_note", ""))

    def test_median_helper_matches_statistics_median_and_survives_empty(self):
        """`_median` 就是 statistics.median（cannbot median_us 同款）；空集返回 None、**不抛**——
        只读报告字段绝不允许把整份 perf_report 炸掉。"""
        import statistics
        self.assertEqual(pc._median([1, 2, 3, 4]), statistics.median([1, 2, 3, 4]))
        self.assertEqual(pc._median([3.0]), 3.0)
        self.assertIsNone(pc._median([]))

    def test_case_dtype_fallback_never_drops_a_row(self):
        """坏/缺 dtype → 'unknown'（照 cannbot 兜底），而不是异常、也不是悄悄丢行。"""
        for bad in (None, "x", {}, {"inputs": []}, {"inputs": "x"}, {"inputs": [{"dtype": ""}]},
                    {"inputs": [{"dtype": 7}]}):
            self.assertEqual(pc._case_dtype(bad), "unknown", repr(bad))
        self.assertEqual(pc._case_dtype({"inputs": [{"dtype": "bfloat16"}]}), "bfloat16")


class NoTargetIsFailClosedTest(unittest.TestCase):
    """有性能用例要判、spec 却给不出 `target_ratio` → `invalid_config`，**绝不兜底套 0.95**。

    病历（aclnnRoll 试跑）：`perf_report.json` 实测 `target_ratio: 0.95`、`perf_cases: 47`、
    `达标: 28`、`failed: 19`、`status: "fail"`——那个 0.95 **任务书里根本没有**，是
    `_resolve_target_ratio` 在「未声明基线」时静默兜底套上去的。工具替任务书造了一条验收要求，
    报告读者还会以为那是任务书写的（律令 5.8「不捏造」）。

    ⚠ **本组钉的是 fail-closed 方向，不是「无目标也放行」**：任务书真没有性能要求时，
    §5.10 给的出路是 `perf.mode='measure_only'` + `measure_only_authorization`
    （ground + cite + quote + 快照指纹）——那是**带授权锚的显式宽档**。
    「spec 里什么都不写」拿不到同样的待遇：无授权的隐式宽档等于给 §5.10 那道门开一扇后门。
    """

    def _cs(self, ids):
        return _caseset([(cid, [], [4]) for cid in ids])

    # ── ① 解析器本身 ────────────────────────────────────────────────────────
    def test_resolver_refuses_to_fabricate_a_target(self):
        """`{}`（= perf 块缺席时调用方归一出来的形态）→ `(None, err)`，**不是** `(0.95, None)`。

        ⚠ 断言写成「tgt 不是 0.95 **且** err 非空」，不只断言 `tgt is None`：
        单看 `tgt is None` 的话，把返回值改成 `(None, None)` 也能过，而那正是「静默无目标」。"""
        tgt, err = pc._resolve_target_ratio({})
        self.assertIsNone(tgt)
        self.assertIsInstance(err, str)
        self.assertTrue(err.strip())
        self.assertNotEqual(tgt, 0.95)
        # 有目标 / 声明基线却缺阈 两格照旧
        self.assertEqual(pc._resolve_target_ratio({"target_ratio": 1.0}), (1.0, None))
        self.assertIsNone(pc._resolve_target_ratio({"baseline": "tbe"})[0])

    def test_the_refusal_names_both_legal_ways_out(self):
        """拒绝语必须同时给出两条出路，否则读到它的人会以为「无性能要求」这件事不再被支持。"""
        _, err = pc._resolve_target_ratio({})
        self.assertIn("target_ratio", err)
        self.assertIn("measure_only", err)
        self.assertIn("measure_only_authorization", err)
        self.assertIn("5.10", err)

    # ── ② 端到端 ───────────────────────────────────────────────────────────
    def test_no_perf_block_with_perf_cases_is_invalid_config(self):
        """spec 无 perf 块 + 有性能用例 + 有基线 → `invalid_config`，一条都不判达标。"""
        cs = self._cs(["p_fast", "p_slow"])
        r = pc.perf_compare({"op": "Sign"}, cs,
                            _ev({"p_fast": (1.0, "kernel_only"), "p_slow": (10.0, "kernel_only")}),
                            _bl({"p_fast": 2.0, "p_slow": 5.0}))
        self.assertEqual(r["summary"]["status"], "invalid_config")
        self.assertIsNone(r["target_ratio"])           # ★ 不得出现凭空的 0.95
        self.assertEqual(r["summary"]["达标"], 0)
        self.assertEqual(r["summary"]["blocked"], 2)
        self.assertTrue(all(row["blocked"] and row["达标"] is False for row in r["per_case"]))

    def test_empty_and_null_perf_blocks_take_the_same_exit(self):
        """`perf` 缺席 / `{}` 走同一出口；显式 `null` 由 perf_mode fail-closed 拦在更前面。

        ⚠ 显式 `null` **刻意不等价于缺席**（`perf_mode.resolve_spec_mode` 的 `_ABSENT` 哨兵口径）：
        「字段没写」与「写了个坏值」必须分得开，后者是一份该被看见的坏 spec。"""
        cs = self._cs(["p"])
        ev, bl = _ev({"p": (1.0, "kernel_only")}), _bl({"p": 2.0})
        for spec in ({"op": "Sign"}, {"op": "Sign", "perf": {}}):
            r = pc.perf_compare(spec, cs, ev, bl)
            self.assertEqual(r["summary"]["status"], "invalid_config", repr(spec))
            self.assertIsNone(r["target_ratio"], repr(spec))
        r_null = pc.perf_compare({"op": "Sign", "perf": None}, cs, ev, bl)
        self.assertEqual(r_null["summary"]["status"], "invalid")
        self.assertIsNone(r_null["target_ratio"])

    def test_no_case_is_reported_as_a_performance_failure(self):
        """`invalid_config` 的行是 `blocked`，`summary` 不得记出任何「未达标」计数。

        这条钉的是病历里最贵的那一格：19 条 `failed` 是**凭空目标**造出来的假失败。"""
        cs = self._cs(["p"])
        r = pc.perf_compare({"op": "Sign"}, cs, _ev({"p": (10.0, "kernel_only")}),
                            _bl({"p": 1.0}))          # ratio=0.1，旧口径下必是「未达标」
        self.assertEqual(r["summary"]["status"], "invalid_config")
        self.assertEqual(r["summary"]["达标"], 0)
        self.assertEqual(r["summary"]["blocked"], 1)
        self.assertEqual(r["summary"].get("failed", 0), 0)

    def test_zero_perf_cases_still_reaches_no_perf_cases(self):
        """⭐ 反向不变式：**没有性能用例**的 spec 不写 perf 块 → 照旧 `no_perf_cases`。

        没有这条，上面几条可由一个「无 perf 块一律拒」的过严实现满足，而那会把
        「PASS(无性能要求)」这条既有终态整条掐掉。收紧只针对「有用例要判却没有目标」。"""
        empty = {"op": "Sign", "cases": [{"id": "f0", "dims": ["功能"], "tags": [],
                                          "inputs": [{"name": "self", "dtype": "float32",
                                                      "shape": [4]}], "attrs": {}}]}
        r = pc.perf_compare({"op": "Sign"}, empty, {"op": "Sign", "evidence": []}, _bl({}))
        self.assertEqual(r["summary"]["status"], "no_perf_cases")
        self.assertIsNone(r["target_ratio"])           # 这里同样不得出现凭空的 0.95

    def test_measure_only_is_the_authorized_way_to_have_no_target(self):
        """⭐ 出路真的走得通：同一份「没有比值要求」的 caseset，按 §5.10 声明 + 授权后落 `measured`。

        没有这条，上面的 fail-closed 就成了「无性能要求 = 永远过不去」。"""
        cs = self._cs(["p"])
        spec = {"op": "Sign", "perf": {
            "mode": "measure_only",
            "measure_only_authorization": {
                "taskdoc_requirement": "no_perf_requirement",
                "cite": "任务书 §3 性能要求",
                "quote": "性能要求：无",
                # 本文件只测报告形态；现场快照核验由 acceptance gate 专测。
                "taskdoc_snapshot_sha256": "a" * 64}}}
        r = pc.perf_compare(spec, cs, _ev({"p": (1.25, "kernel_only")}), None)
        self.assertEqual(r["summary"]["status"], "measured")
        self.assertEqual(r["summary"]["measured"], 1)
        self.assertEqual(r["summary"]["blocked"], 0)
        self.assertIsNone(r["target_ratio"])
        # 「没有目标」不得长出任何达标口径的键
        self.assertNotIn("达标", r["summary"])
        self.assertNotIn("cases_above_threshold", r["summary"])

    def test_declared_baseline_without_target_still_invalid_config(self):
        """既有护栏未被放松：声明基线却缺 target_ratio 仍 `invalid_config`（拒静默套 0.95）。"""
        r = pc.perf_compare({"op": "Sign", "perf": {"baseline": "tbe"}}, self._cs(["p"]),
                            _ev({"p": (1.0, "kernel_only")}), _bl({"p": 2.0}))
        self.assertEqual(r["summary"]["status"], "invalid_config")

    # ── ③ 病历复现：aclnnRoll 的形状 ──────────────────────────────────────────
    def test_the_roll_pathology_produces_no_fabricated_target_and_no_fabricated_failures(self):
        """⭐ 病历复现：无 `perf` 块 + 一批性能用例（其中多条 ratio<0.95）→ 不得再造目标、不得再造失败。

        aclnnRoll 试跑的实测形状是 `target_ratio: 0.95` / `perf_cases: 47` / `达标: 28` /
        `failed: 19`。那 19 条「失败」全是**凭空目标**判出来的。这里用同样的形状复核：
        每条都判 `blocked`（无目标可判），`failed` 计数为 0，且**没有任何一行拿到过比值裁决**。

        ⚠ 不扫「报告文本里有没有 0.95」——拒绝语本身就逐字引了那个数（"这里刻意不再兜底套 0.95"），
        扫字符串会把**说明**误当成**判据**。要钉的是结构：目标为 `None`、没有 `ratio` 字段、
        没有一行 `达标 is True`。"""
        ids = [f"p{i}" for i in range(12)]
        cs = self._cs(ids)
        # 一半 ratio=2.0（旧口径下「达标」），一半 ratio=0.5（旧口径下「未达标」）——
        # 两侧都必须落进 blocked，而不是被分成 达标/failed 两堆。
        ev = _ev({cid: (1.0, "kernel_only") for cid in ids})
        bl = _bl({cid: (2.0 if i % 2 == 0 else 0.5) for i, cid in enumerate(ids)})
        r = pc.perf_compare({"op": "Sign"}, cs, ev, bl)
        self.assertEqual(r["summary"]["status"], "invalid_config")
        self.assertIsNone(r["target_ratio"])
        self.assertEqual(r["summary"]["blocked"], len(ids))
        self.assertEqual(r["summary"]["达标"], 0)
        self.assertEqual(r["summary"].get("failed", 0), 0)
        # `达标: 0` 只有在「blocked 覆盖全部性能用例」时才不会被读成「一条都没达标」——
        # 钉死这条同现关系，免得将来有人把 blocked 计数改小、只留下一个裸的 0。
        self.assertEqual(r["summary"]["blocked"], r["summary"]["perf_cases"])
        for row in r["per_case"]:
            self.assertTrue(row["blocked"], row)
            self.assertIs(row["达标"], False, row)
            self.assertNotIn("ratio", row)          # 一条比值都没算过
        for item in r["non_passing_cases"]:
            self.assertEqual(item["outcome"], "blocked", item)
            self.assertIsNone(item["target_ratio"], item)   # ★ 不得再出现凭空的 0.95

    def test_invalid_config_emits_no_scoring_counters_at_all(self):
        """`invalid_config` 下不得出现 `cases_scored` / `cases_above_threshold`。

        没有目标就没有「评分」这件事；落一个 `cases_scored: 0` 会与「采了数但没评上」混淆，
        而后者是另一种（真的采集失败的）故事。整块不出 > 出一堆 0（同 `_report_aggregate` 的纪律）。"""
        r = pc.perf_compare({"op": "Sign"}, self._cs(["p"]), _ev({"p": (1.0, "kernel_only")}),
                            _bl({"p": 2.0}))
        self.assertEqual(r["summary"]["status"], "invalid_config")
        for key in ("cases_scored", "cases_above_threshold"):
            self.assertNotIn(key, r["summary"])

    def test_zero_scored_cases_can_never_reach_status_ok(self):
        """⭐ 不变式：`cases_scored == 0` 与 `status == "ok"` 不可同时成立。

        规矩（AGENTS.md 与 op-acceptance 契约）：`cases_scored=0` 一律读作**性能未验证**，
        绝不能被读成「没什么可判所以过了」。实现上靠「每行不是有 ratio 就是 blocked、
        有 blocked 即 status=blocked」保证；这条测试把该结论钉在行为层，
        免得将来有人加一条「既无 ratio 也不 blocked」的行把不变式悄悄捅穿。"""
        cs = self._cs(["p0", "p1"])
        # 两条性能用例都缺 evidence → 全部 blocked → 一条可比测量都没有。
        r = pc.perf_compare(_spec(1.0), cs, {"op": "Sign", "evidence": []}, _bl({"p0": 1.0, "p1": 1.0}))
        self.assertEqual(r["summary"]["blocked"], 2)
        self.assertNotEqual(r["summary"]["status"], "ok")
        self.assertEqual(r["summary"].get("cases_scored", 0), 0)


class MeasureOnlyZeroCaseExitTest(unittest.TestCase):
    """`measure_only` 的**零性能用例**出口不得携带比值通路的词表。

    该出口复用了与 ratio 通路共享的 `_no_perf_cases`，历史上因此落了一个 `达标: 0`——
    而 `validate_acceptance_state._gate_measure_only_report` 明令 measure_only 的 summary
    **不得含** `达标` / `cases_above_threshold` / `cases_scored`。即 perf_compare 自己产出了一份
    过不了自己那道门的产物，且那个 `0` 正是 plan 点名的「会被读成『一条都没达标』」。

    ⚠ 这里钉的是**词表**，不是放行：本出口的 `status` 仍是 `no_perf_cases`，
    gate_task3 见它照旧记 error → BLOCKED。下面第二条测试把这一点一起钉住。
    """

    _AUTH = {"taskdoc_requirement": "no_perf_requirement",
             "cite": "任务书 §3 性能要求", "quote": "性能要求：无",
             # 本文件只测零 case 出口；现场快照核验由 acceptance gate 专测。
             "taskdoc_snapshot_sha256": "a" * 64}

    def _spec_mo(self):
        return {"op": "Sign", "perf": {"mode": "measure_only",
                                       "measure_only_authorization": dict(self._AUTH)}}

    def _caseset_without_perf_cases(self):
        return {"op": "Sign", "cases": [
            {"id": "f0", "dims": ["功能"], "tags": [],
             "inputs": [{"name": "self", "dtype": "float32", "shape": [4]}], "attrs": {}}]}

    def test_zero_perf_cases_report_has_no_verdict_vocabulary(self):
        r = pc.perf_compare(self._spec_mo(), self._caseset_without_perf_cases(),
                            {"op": "Sign", "evidence": []}, None)
        self.assertEqual(r["perf_mode"], "measure_only")
        self.assertEqual(r["summary"]["status"], "no_perf_cases")
        for key in ("达标", "cases_above_threshold", "cases_scored"):
            self.assertNotIn(key, r["summary"])
        # 本口径的计数词要在场且为 0（缺席会让门另记一条「measured=None 非整数计数」的噪声 error）。
        self.assertEqual(r["summary"]["measured"], 0)
        self.assertEqual(r["summary"]["perf_cases"], 0)
        self.assertIsNone(r["target_ratio"])
        self.assertIsNone(r["baseline_source"])

    def test_the_cleanup_does_not_turn_it_into_a_pass(self):
        """⭐ 反向不变式：去掉 `达标` 这个键**不等于**放行。

        `status` 必须仍是 `no_perf_cases`（gate_task3 见它即记 error），
        且**不得**变成 `measured`——后者是 run_workflow 判「性能维不阻挡 overall」的唯一钥匙。"""
        r = pc.perf_compare(self._spec_mo(), self._caseset_without_perf_cases(),
                            {"op": "Sign", "evidence": []}, None)
        self.assertNotEqual(r["summary"]["status"], "measured")
        self.assertEqual(r["summary"]["status"], "no_perf_cases")

    def test_ratio_gated_zero_case_exit_is_untouched(self):
        """⭐ ratio 通路的同一出口**一个字节不变**：`达标: 0` 照旧在场、没有 `measured` 键。

        本次只动 measure_only 分支，共享的 `_no_perf_cases` 本身没改。"""
        r = pc.perf_compare(_spec(1.0), self._caseset_without_perf_cases(),
                            {"op": "Sign", "evidence": []}, _bl({}))
        self.assertEqual(r["summary"]["status"], "no_perf_cases")
        self.assertEqual(r["summary"]["达标"], 0)
        self.assertNotIn("measured", r["summary"])


if __name__ == "__main__":
    unittest.main()
