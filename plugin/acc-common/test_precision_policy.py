"""精度口径 SSOT 单测（T5）——precision_policy 复算 + validator 纯算术 judge + 端到端 PASSED_WITH_RISK。

跑: cd plugin/acc-common && python3 test_precision_policy.py
覆盖（对齐 plan Acceptance #1/#2/#4）：
  · AscendOpTest 逐 dtype tol+error_rate；坏点边界 floor(n*error_rate)+1（非单点）；|e|≥1 rel/<1 abs 共用 tol；
    inf/NaN 语义；denom max(|e|,|o|)+1e-9。
  · MERE=平均/MARE=最大**不对调** + 10× 规则 + Th 表(2^-k) + denom |g|+1e-7；NOT_SETTLED；单标杆不过→uncertain（非 fail）。
  · exact：mismatch<=0；未支持 dtype fail-fast；select_standard 向后兼容映射。
  · PASSED_WITH_RISK 路径（acceptance_policy 宽于 standard）→ run_workflow 退出码 2 + requires_human_cp。
"""
import copy, hashlib, json, os, subprocess, sys, tempfile, shutil, unittest
import numpy as np

import precision_policy as P
import validator as V
import _golden_fixture as _gf
setUpModule = _gf.install        # golden 去引擎化：gen_cases/run_workflow 需 <ops_root>/<op>/golden.py（ADR 0011）
tearDownModule = _gf.uninstall

_HERE = os.path.dirname(os.path.abspath(__file__))


class AscendOpTestDefaultTest(unittest.TestCase):
    def test_table_per_dtype_tol_and_error_rate(self):
        cases = {"float32": (1e-4, 1e-4), "float16": (1e-3, 1e-3), "int32": (1e-4, 1e-4),
                 "uint8": (1, 0.01), "int8": (1, 1e-3)}
        for dt, (tol, err) in cases.items():
            pol = P.threshold_for("ascendoptest_default", dt)
            self.assertEqual(pol["kind"], "ascendoptest_default")
            self.assertEqual(pol["tolerance"], tol, dt)
            self.assertEqual(pol["error_rate"], err, dt)      # error_rate 是第 2 位、逐 dtype 变
            self.assertEqual(pol["eps"], 1e-9)
            self.assertFalse(pol["not_settled"])
            self.assertEqual(P.tolerance_policy_id("ascendoptest_default", dt),
                             f"ascendoptest_default:{dt}")
            self.assertEqual(P.threshold_digest(pol), tol)     # digest = tolerance

    def test_bad_point_boundary_not_single(self):
        """坏点门 bad_count<=numel*error_rate：floor(n*err) 过、floor(n*err)+1 不过（非单点边界）。"""
        n = 2000
        golden = np.ones(n, dtype=np.float16)
        pol = P.threshold_for("ascendoptest_default", "float16")  # error_rate 1e-3 → 阈 2.0
        k_pass = int(np.floor(n * pol["error_rate"]))             # 2
        k_fail = k_pass + 1                                       # 3 = defect 注入数
        for k, exp in ((k_pass, "pass"), (k_fail, "fail")):
            out = golden.copy(); out[:k] += 10
            m = P.compute_metrics(out, golden, pol)
            self.assertEqual(m["bad_count"], k)
            self.assertEqual(m["numel"], n)
            self.assertEqual(V.judge_ascendoptest(pol, m)[0], exp, f"k={k}")

    def test_rel_ge1_abs_lt1_shared_tol(self):
        """|e|≥1 用相对、|e|<1 用绝对、共用同一 tolerance。"""
        pol = {"kind": "ascendoptest_default", "tolerance": 0.1, "error_rate": 0.0, "eps": 1e-9}
        golden = np.array([2.0, 0.5], dtype=np.float32)
        out = np.array([2.15, 0.65], dtype=np.float32)   # 2.0:rel 0.15/2.15=0.07<=0.1 过；0.5:abs 0.15>0.1 坏
        m = P.compute_metrics(out, golden, pol)
        self.assertEqual(m["bad_count"], 1)

    def test_denominator_maxmin_plus_1e9(self):
        """相对误差分母 = max(|e|,|o|)+1e-9。"""
        pol = {"kind": "ascendoptest_default", "tolerance": 1e-9, "error_rate": 0.0, "eps": 1e-9}
        golden = np.array([1.0], dtype=np.float32)
        out = np.array([3.0], dtype=np.float32)          # rel = 2/(max(1,3)+1e-9) ≈ 0.6667
        m = P.compute_metrics(out, golden, pol)
        self.assertAlmostEqual(m["max_rel_err"], 2.0 / (3.0 + 1e-9), places=6)

    def test_inf_and_nan_semantics(self):
        """inf→±finfo.max、NaN==NaN 视为通过。"""
        pol = {"kind": "ascendoptest_default", "tolerance": 1e-3, "error_rate": 0.0, "eps": 1e-9}
        golden = np.array([np.inf, -np.inf, np.nan], dtype=np.float32)
        out = np.array([np.inf, -np.inf, np.nan], dtype=np.float32)
        self.assertEqual(P.compute_metrics(out, golden, pol)["bad_count"], 0)
        out2 = np.array([np.inf, -np.inf, 5.0], dtype=np.float32)  # 第3位 nan vs 5.0 → 坏
        self.assertEqual(P.compute_metrics(out2, golden, pol)["bad_count"], 1)


class MereMareTest(unittest.TestCase):
    def test_mere_mean_mare_max_not_swapped(self):
        """MERE=平均、MARE=最大——务必不对调。"""
        pol = P.threshold_for("ecosystem_mere_mare", "float32")
        golden = np.array([1.0, 1.0, 1.0, 1.0], dtype=np.float32)
        out = np.array([1.1, 1.0, 1.0, 1.0], dtype=np.float32)   # rel = [0.1,0,0,0]
        m = P.compute_metrics(out, golden, pol)
        self.assertAlmostEqual(m["mare"], 0.1, places=5)         # 最大
        self.assertAlmostEqual(m["mere"], 0.025, places=5)       # 平均
        self.assertGreater(m["mare"], m["mere"])                 # 不对调铁证

    def test_th_table_2_pow_minus_k(self):
        exp = {"float16": 10, "bfloat16": 7, "float32": 13, "hfloat32": 11,
               "fp8_e4m3": 3, "fp8_e5m2": 2}
        self.assertEqual(P._MM_TH_EXP, exp)                      # 全 6 dtype Th 指数正确
        # 受支持 dtype 经 threshold_for 出 Th
        self.assertAlmostEqual(P.threshold_for("ecosystem_mere_mare", "float16")["threshold"],
                               2.0 ** -10)
        self.assertAlmostEqual(P.threshold_for("ecosystem_mere_mare", "float32")["threshold"],
                               2.0 ** -13)

    def test_10x_rule_and_not_settled(self):
        pol = P.threshold_for("ecosystem_mere_mare", "float32")
        th = pol["threshold"]
        self.assertEqual(pol["max_ratio"], 10)
        self.assertEqual(pol["eps"], 1e-7)
        self.assertTrue(pol["not_settled"])
        # MERE<Th 且 MARE<10Th → pass
        self.assertEqual(V.judge_mere_mare(pol, {"mere": 0.5 * th, "mare": 5 * th})[0], "pass")
        # MARE≥10Th → 不过 → uncertain（NOT_SETTLED，非 fail）
        self.assertEqual(V.judge_mere_mare(pol, {"mere": 0.5 * th, "mare": 15 * th})[0], "uncertain")
        # MERE≥Th → 不过 → uncertain
        self.assertEqual(V.judge_mere_mare(pol, {"mere": 2 * th, "mare": 5 * th})[0], "uncertain")

    def test_denom_eps_1e7(self):
        pol = P.threshold_for("ecosystem_mere_mare", "float32")
        golden = np.array([0.0], dtype=np.float32)
        out = np.array([1e-7], dtype=np.float32)                # rel = 1e-7/(0+1e-7) = 1.0
        self.assertAlmostEqual(P.compute_metrics(out, golden, pol)["mare"], 1.0, places=4)


class ExactTest(unittest.TestCase):
    def test_exact_metric_and_judge(self):
        pol = P.threshold_for("exact", "bool")
        self.assertEqual(P.tolerance_policy_id("exact", "bool"), "exact")
        golden = np.array([True, False, True, True])
        self.assertEqual(P.compute_metrics(golden.copy(), golden, pol)["exact_mismatch"], 0)
        self.assertEqual(V.judge_exact(pol, {"exact_mismatch": 0, "numel": 4})[0], "pass")
        out = golden.copy(); out[0] = ~out[0]
        m = P.compute_metrics(out, golden, pol)
        self.assertEqual(m["exact_mismatch"], 1)
        self.assertEqual(V.judge_exact(pol, m)[0], "fail")


class FailFastAndRoutingTest(unittest.TestCase):
    def test_unsupported_dtype_fail_fast(self):
        for std, dt in (("ascendoptest_default", "bfloat16"),   # 在表内但不可复算
                        ("ascendoptest_default", "complex64"),
                        ("ecosystem_mere_mare", "fp8_e4m3"),
                        ("ecosystem_mere_mare", "bfloat16")):
            with self.assertRaises(ValueError, msg=f"{std}:{dt}"):
                P.threshold_for(std, dt)
        with self.assertRaises(ValueError):                     # 完全未知 dtype
            P.threshold_for("ascendoptest_default", "float8")

    def test_select_standard_backward_compat(self):
        self.assertEqual(P.select_standard({"verify_mode": "exact",
                         "precision": {"oracle": "ascendoptest"}}), "exact")
        self.assertEqual(P.select_standard({"verify_mode": "numerical",
                         "precision": {"oracle": "ascendoptest"}}), "ascendoptest_default")
        self.assertEqual(P.select_standard({"verify_mode": "numerical",
                         "precision": {"oracle": "mere_mare"}}), "ecosystem_mere_mare")
        self.assertEqual(P.select_standard({"verify_mode": "numerical",
                         "precision": {"standard": "ecosystem_mere_mare"}}), "ecosystem_mere_mare")

    def test_select_standard_unknown_oracle_fail_closed(self):
        """Q9-Part B：numerical + 非白名单 oracle（如 scipy/std_exact「与 python 一致」类）→ 显式 raise，
        堵 class C 静默降级为 ascendoptest_default。白名单只含 {ascendoptest, none, 缺省}。
        （torch 已明确映射到 torch_allclose——见 test_select_standard_torch_maps_allclose，不在此 raise 集。）"""
        for orc in ("scipy", "std_exact", "numpy-f32-matmul"):
            with self.assertRaises(ValueError, msg=orc):
                P.select_standard({"verify_mode": "numerical", "precision": {"oracle": orc}})
        # 白名单成员仍放行
        self.assertEqual(P.select_standard({"verify_mode": "numerical",
                         "precision": {"oracle": "none"}}), "ascendoptest_default")
        self.assertEqual(P.select_standard({"verify_mode": "numerical",
                         "precision": {}}), "ascendoptest_default")  # 缺省 oracle → 默认
        # 显式 standard 优先，绕过 oracle 白名单（catlass spec 正是此路）
        self.assertEqual(P.select_standard({"verify_mode": "numerical",
                         "precision": {"oracle": "numpy-f32-matmul",
                                       "standard": "ascendoptest_default"}}), "ascendoptest_default")

    def test_oracle_source_from_golden_maps_by_prefix(self):
        """golden_source 据实映射到 canonical 六枚举。**首 token = 六枚举之一 → 直接用**（多仓多算子：golden.py
        直接声明 cpu_ref/catlass_existing_ref/task_spec_expected/external_ref/torch_ref/analytical_ref）；
        backend 简写 torch→torch_ref、numpy→analytical_ref；识别不出 → fail-closed（不默认 cpu_ref）。"""
        # backend 简写（elementwise 内置样例沿用）
        self.assertEqual(P.oracle_source_from_golden("torch torch.isclose"), "torch_ref")
        self.assertEqual(P.oracle_source_from_golden("torch torch.sign"), "torch_ref")
        self.assertEqual(P.oracle_source_from_golden("numpy np.isclose"), "analytical_ref")
        self.assertEqual(P.oracle_source_from_golden(
            "numpy f32 matmul（A.f32@B.f32 再回落 dtype）"), "analytical_ref")
        # **六枚举直接声明**（多仓多算子——别的仓的 golden 来自 cpu_ref/仓自带/任务书期望等，现都能声明）
        for enum in P.ORACLE_SOURCES:
            self.assertEqual(P.oracle_source_from_golden(f"{enum} src/xxx.cc"), enum, enum)
            self.assertEqual(P.oracle_source_from_golden(enum), enum, f"{enum} 单 token")
        # 识别不出 → fail-closed（不默认 cpu_ref）；near-miss（无下划线/别名）也不放行
        for bad in (None, "", "scipy foo", "unknown", "cpuref x", "cpu ref"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                P.oracle_source_from_golden(bad)


class ValidatorRiskTest(unittest.TestCase):
    """validator 层：acceptance 过 & standard 不过 → risk=true、overall=passed_with_risk。"""
    def _spec(self):
        # spec **声明** acceptance（任务书宽于平台底线的合法 risk 路径）+ 完整 IO 矩阵（validator 据此派生 cdtype）。
        return {"op": "X", "verify_mode": "numerical",
                "params": [{"name": "self", "io": "in", "dtype": ["float32"]},
                           {"name": "out", "io": "out", "dtype": ["float32"]}],
                "precision": {"oracle": "ascendoptest", "standard": "ascendoptest_default",
                              "acceptance_policy": {"standard": "ascendoptest_default", "error_rate": 0.1}}}

    def _pair(self, std_pol, acc_pol, metrics, acc_metrics):
        cid = "x_000"
        caseset = {"op": "X", "cases": [{
            "id": cid, "dims": ["功能", "精度"],
            "inputs": [{"name": "self", "shape": [16], "dtype": "float32"}],
            "expected": {"golden_path": "g.npy", "verify_mode": "numerical",
                         "standard": "ascendoptest_default", "compare_dtype": "float32",
                         "tolerance_policy_id": "ascendoptest_default:float32",
                         "policy": std_pol, "threshold": std_pol["tolerance"],
                         "acceptance_policy": acc_pol,
                         "acceptance_tolerance_policy_id": "ascendoptest_default:float32"}}]}
        evidence = {"op": "X", "evidence": [{
            "case_id": cid, "status": "ok",
            "precision": {"standard": "ascendoptest_default",
                          "tolerance_policy_id": "ascendoptest_default:float32",
                          "policy": std_pol, "threshold": std_pol["tolerance"],
                          "acceptance_policy": acc_pol,
                          "acceptance_tolerance_policy_id": "ascendoptest_default:float32",
                          "metrics": metrics, "acceptance_metrics": acc_metrics}}]}
        return caseset, evidence

    def test_risk_when_acceptance_passes_standard_fails(self):
        # canonical policy 完整（== threshold_for 输出）——validator 会据 spec 复算 canonical 三处比对，
        # 手搓残缺 policy 会被 finding #6 的 canonical 校验判契约 fail，故此处必须用 threshold_for。
        std_pol = P.threshold_for("ascendoptest_default", "float32")   # canonical，error_rate 1e-4
        acc_pol = dict(std_pol); acc_pol["error_rate"] = 0.1           # 任务书放宽 error_rate
        # 1 坏点：standard 1>16*1e-4 fail；acceptance 1<=16*0.1 pass → risk
        caseset, evidence = self._pair(std_pol, acc_pol,
                                       {"bad_count": 1, "numel": 16}, {"bad_count": 1, "numel": 16})
        vd = V.validate(self._spec(), caseset, evidence)
        row = vd["per_case"][0]
        self.assertEqual(row["standard_profile_pass"], "fail")
        self.assertEqual(row["acceptance_precision_pass"], "pass")
        self.assertTrue(row["risk"])
        self.assertEqual(row["精度"], "pass")                   # 放行只看 acceptance
        self.assertEqual(vd["overall"]["verdict"], "passed_with_risk")
        self.assertTrue(vd["overall"]["requires_human_cp"])


class PassedWithRiskE2ETest(unittest.TestCase):
    """端到端：acceptance_policy 宽于 standard 的 spec + defect → run_workflow 退出码 2 + requires_human_cp。"""
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_run_workflow_exit_2(self):
        spec = {"op": "Sign", "repo": "ops-math", "hardware": ["Atlas A2"],
                "reference": {"type": "tbe", "ref": "内置 TBE Sign"},
                "change": {"kind": "add_dtype"}, "params_source": "derived_from_reference",
                "params": [{"name": "self", "io": "in", "dtype": ["float32"]},
                           {"name": "out", "io": "out", "dtype": ["float32"]}],
                "generalize": True, "verify_mode": "numerical",
                "precision": {"oracle": "ascendoptest", "standard": "ascendoptest_default",
                              "acceptance_policy": {"standard": "ascendoptest_default",
                                                    "error_rate": 0.1}},
                "perf": {"baseline": "tbe", "target_ratio": 1.0}}
        spec_path = os.path.join(self.d, "sign_risk.spec.json")
        with open(spec_path, "w", encoding="utf-8") as f:
            json.dump(spec, f, ensure_ascii=False)
        out = os.path.join(self.d, "run")
        # §1 覆盖-预算重写后 case id 变——从**生成的 caseset** 取真实 fp32 精度 case id（稳健、不硬编码）。
        # ⚠ 取 numel≥16 的 case：risk 需「1 坏点超 standard 阈但落 acceptance error_rate=0.1 内」→ numel*0.1≥1
        #   即 numel≥10；scalar/边界(numel=1) 会让 1 坏点也超 acceptance → 变纯 fail、非 risk。
        import gen_cases
        cs = gen_cases.gen_cases(spec, os.path.join(self.d, "gen"))
        defect_id = next(c["id"] for c in cs["cases"]
                         if "精度" in c["dims"] and c["inputs"][0]["dtype"] == "float32"
                         and c["expected"].get("compare") != "na"
                         and int(np.prod(c["inputs"][0]["shape"])) >= 16)
        # C5：`--defect` 已移出 CLI（降级为测试专用夹具）→ 改进程内调用；
        # mock 是非验收通路、物理上不产 acceptance.json → 读 dev_run_summary.json（overall→pipeline_result）。
        # ⚠ 本测试要测的能力（PASSED_WITH_RISK → exit 2 + 挂人工 CP）一点没变，只是入口与产物名换了。
        import run_workflow as W
        r = W.run(spec_path, mode="mock", out_dir=out, defect=[defect_id])
        self.assertEqual(r["exit_code"], 2, r)
        with open(os.path.join(out, "dev_run_summary.json"), encoding="utf-8") as f:
            acc = json.load(f)
        self.assertEqual(acc["pipeline_result"], "PASSED_WITH_RISK")
        self.assertTrue(acc["requires_human_cp"])
        self.assertIn(defect_id, acc["three_layer"]["risk_cases"])


class ComputeMetricsGuardTest(unittest.TestCase):
    """finding #1/#2：compute_metrics 入口 flatten + size 不等 fail-fast + 未支持 dtype fail-fast。"""
    def test_size_mismatch_fail_fast(self):
        pol = P.threshold_for("ascendoptest_default", "float32")
        with self.assertRaises(ValueError):
            P.compute_metrics(np.ones(4, np.float32), np.ones(5, np.float32), pol)

    def test_complex_dtype_fail_fast(self):
        """complex 若静默 astype(float64) 会丢虚部返 0 误差假通过 → 必 fail-fast。"""
        pol = P.threshold_for("ascendoptest_default", "float32")
        c = np.array([1 + 2j, 3 + 4j], dtype=np.complex64)
        with self.assertRaises(ValueError):
            P.compute_metrics(c, c, pol)

    def test_mere_mare_complex_fail_fast(self):
        pol = P.threshold_for("ecosystem_mere_mare", "float32")
        c = np.array([1 + 2j], dtype=np.complex128)
        with self.assertRaises(ValueError):
            P.compute_metrics(c, c, pol)

    def test_exact_nan_aligned_equal(self):
        """§1.4 NaN 特殊场景：EXACT 分支同位 NaN 视为相等（bf16/int Neg 的 neg(NaN)=NaN 不假 fail）。
        NaN!=NaN=True 若误计 mismatch，则本用例 exact_mismatch=2 而非 0。"""
        pol = {"kind": P.EXACT, "max_mismatch": 0}   # compute_metrics EXACT 分支只读 kind
        g = np.array([np.nan, 1.0, np.nan, -2.0], dtype=np.float32)
        out = g.copy()                               # 同位 NaN + 其余相等 → 0 mismatch
        m = P.compute_metrics(out, g, pol)
        self.assertEqual(m["exact_mismatch"], 0, "同位 NaN 应视为相等（exact_mismatch=0）")
        # 反例：一处 NaN 变实数 → 该位真 mismatch
        bad = g.copy(); bad[0] = 3.0
        self.assertEqual(P.compute_metrics(bad, g, pol)["exact_mismatch"], 1)

    def test_flatten_multidim(self):
        pol = P.threshold_for("ascendoptest_default", "float32")
        g = np.ones((4, 4), np.float32)
        self.assertEqual(P.compute_metrics(g.copy(), g, pol)["numel"], 16)

    def test_both_nan_not_pollute_diagnostics(self):
        """finding #5：both-NaN 通过但不污染 max_abs/max_rel；显式返回 nan_pair_count。"""
        pol = P.threshold_for("ascendoptest_default", "float32")
        g = np.array([1.0, np.nan, 2.0], dtype=np.float32)
        o = np.array([1.0, np.nan, 2.0], dtype=np.float32)
        m = P.compute_metrics(o, g, pol)
        self.assertEqual(m["bad_count"], 0)
        self.assertEqual(m["nan_pair_count"], 1)
        self.assertTrue(np.isfinite(m["max_abs_err"]))   # 不被 nan 污染
        self.assertTrue(np.isfinite(m["max_rel_err"]))


class ComputeMetricsOutDtypeGuardTest(unittest.TestCase):
    """pv-4：compute_metrics 从前只校 golden 侧 dtype，**从不校 out 侧** → out=complex 静默丢虚部返
    bad_count=0；exact 下 out=uint8 与 golden=bool 跨型 `!=` 值相等返 exact_mismatch=0。双侧同校 + 严等后堵住。
    合法产物两侧本就同 dtype（四算子实测 fp32/fp32·fp16/fp16·bool/bool），故严等不误伤。"""

    def test_numerical_out_complex_golden_real_fail_fast(self):
        """数值口径：out=complex64（真部==golden、虚部非零）、golden=float32 → 必 ValueError（旧洞：
        `_replace_inf(o).astype(float64)` 静默丢虚部 → bad_count=0 假通过）。"""
        pol = P.threshold_for("ascendoptest_default", "float32")
        g = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        o = np.array([1 + 1000j, 2 + 1000j, 3 + 1000j], dtype=np.complex64)  # 真部==golden、虚部=1000j
        with self.assertRaises(ValueError):
            P.compute_metrics(o, g, pol)

    def test_numerical_out_golden_dtype_mismatch_fail_fast(self):
        """数值口径：out/golden 均受支持但 dtype 不一致（float64 vs float32）→ ValueError（防错位 dtype 化归丢信息）。"""
        pol = P.threshold_for("ascendoptest_default", "float32")
        g = np.array([1.0, 2.0], dtype=np.float32)
        o = np.array([1.0, 2.0], dtype=np.float64)
        with self.assertRaises(ValueError):
            P.compute_metrics(o, g, pol)

    def test_mere_mare_out_complex_fail_fast(self):
        pol = P.threshold_for("ecosystem_mere_mare", "float32")
        g = np.array([1.0], dtype=np.float32)
        o = np.array([1 + 5j], dtype=np.complex64)
        with self.assertRaises(ValueError):
            P.compute_metrics(o, g, pol)

    def test_exact_out_uint8_golden_bool_cross_type_fail_fast(self):
        """exact：out=uint8[1,0,1]（跨型值等）、golden=bool[T,F,T] → 必 ValueError（旧洞：
        numpy `uint8 != bool` 值提升后相等 → exact_mismatch=0 假通过）。"""
        pol = P.threshold_for("exact", "bool")
        g = np.array([True, False, True])
        o = np.array([1, 0, 1], dtype=np.uint8)
        with self.assertRaises(ValueError):
            P.compute_metrics(o, g, pol)

    def test_exact_out_uint8_value2_golden_bool_not_equivalent(self):
        """exact：out=uint8 含值 2（非 {0,1}）vs bool golden → 不判等价（跨型即 ValueError，不给 exact_mismatch=0）。"""
        pol = P.threshold_for("exact", "bool")
        g = np.array([True, False, True])
        o = np.array([2, 0, 1], dtype=np.uint8)
        with self.assertRaises(ValueError):
            P.compute_metrics(o, g, pol)

    def test_legit_same_dtype_still_works(self):
        """合法回归（严等不误伤）：bool/bool exact、fp32/fp32 数值、int8/int8 数值、同型 complex exact 逐位比 均正常。"""
        pex = P.threshold_for("exact", "bool")
        gb = np.array([True, False, True])
        self.assertEqual(P.compute_metrics(gb.copy(), gb, pex)["exact_mismatch"], 0)
        p32 = P.threshold_for("ascendoptest_default", "float32")
        g32 = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        self.assertEqual(P.compute_metrics(g32.copy(), g32, p32)["bad_count"], 0)
        p8 = P.threshold_for("ascendoptest_default", "int8")
        g8 = np.array([1, -2, 3], dtype=np.int8)
        self.assertEqual(P.compute_metrics(g8.copy(), g8, p8)["bad_count"], 0)
        gc = np.array([1 + 2j, 3 + 4j], dtype=np.complex64)   # exact 同型 complex：逐位比无损、不误挡
        self.assertEqual(P.compute_metrics(gc.copy(), gc, pex)["exact_mismatch"], 0)


class AscendOpTestIntegerTest(unittest.TestCase):
    """finding #3：整数按 **原 dtype** 复刻 compare.py（保留溢出回绕），非 float64 近似。"""
    def test_int8_overflow_faithful_to_compare_py(self):
        # compare.py: result = np.abs(output - expect) 在原 int8 下算 → 127-(-128) 回绕 = -1 → abs=1
        #   （若误转 float64 则 diff=255 → 会判坏）。tolerance(int8)=1 → 该点通过，bad_count=0。
        pol = P.threshold_for("ascendoptest_default", "int8")
        out = np.array([127], dtype=np.int8)
        golden = np.array([-128], dtype=np.int8)
        m = P.compute_metrics(out, golden, pol)
        self.assertEqual(m["bad_count"], 0)      # 复刻 compare.py 溢出语义（非 float64）
        self.assertEqual(m["numel"], 1)
        self.assertEqual(m["nan_pair_count"], 0)

    def test_int32_exact_match_pass(self):
        pol = P.threshold_for("ascendoptest_default", "int32")
        g = np.array([1000, -3, 0, 7], dtype=np.int32)
        self.assertEqual(P.compute_metrics(g.copy(), g, pol)["bad_count"], 0)


class JudgeSchemaTest(unittest.TestCase):
    """finding #8：judge_* 遇坏 metric（负计数/0 numel/字符串/缺字段）→ fail、不 pass、不崩。"""
    def test_ascendoptest_bad_metrics_fail_not_crash(self):
        pol = P.threshold_for("ascendoptest_default", "float32")
        for m in ({"bad_count": -1, "numel": 16}, {"bad_count": 0, "numel": 0},
                  {"bad_count": "x", "numel": 16}, {"bad_count": 1.5, "numel": 16},
                  {"bad_count": True, "numel": 16}, "notadict", {"numel": 16}):
            self.assertEqual(V.judge_ascendoptest(pol, m)[0], "fail", m)

    def test_exact_bad_metrics_fail(self):
        pol = P.threshold_for("exact", "bool")
        for m in ({"exact_mismatch": -1, "numel": 4}, {"exact_mismatch": 0, "numel": 0},
                  {"exact_mismatch": "x", "numel": 4}, {"exact_mismatch": 0}):
            self.assertEqual(V.judge_exact(pol, m)[0], "fail", m)

    def test_mere_mare_bad_metrics_fail(self):
        pol = P.threshold_for("ecosystem_mere_mare", "float32")
        for m in ({"mere": -1.0, "mare": 0.0}, {"mere": float("nan"), "mare": 0.0},
                  {"mere": float("inf"), "mare": 0.0}, {"mere": "x", "mare": 0.0}, {"mare": 0.0}):
            self.assertEqual(V.judge_mere_mare(pol, m)[0], "fail", m)


class SpecAuthoritativeTest(unittest.TestCase):
    """finding #6：spec 严格但 caseset+evidence **同步放宽** → validator 据 spec 复算 canonical 判契约 fail。"""
    def _cs_ev(self, policy, digest):
        cid = "sign_000"
        exp = {"golden_path": "g.npy", "verify_mode": "numerical", "standard": "ascendoptest_default",
               "compare_dtype": "float32", "tolerance_policy_id": "ascendoptest_default:float32",
               "policy": policy, "threshold": digest}
        caseset = {"op": "Sign", "cases": [{"id": cid, "dims": ["功能", "精度"],
                   "inputs": [{"name": "self", "shape": [16], "dtype": "float32"}], "expected": exp}]}
        evidence = {"op": "Sign", "evidence": [{"case_id": cid, "status": "ok",
                    "precision": {"standard": "ascendoptest_default",
                                  "tolerance_policy_id": "ascendoptest_default:float32",
                                  "policy": policy, "threshold": digest,
                                  "metrics": {"bad_count": 8, "numel": 16}}}]}
        return caseset, evidence

    def _spec(self):
        return {"op": "Sign", "verify_mode": "numerical",
                "params": [{"name": "self", "io": "in", "dtype": ["float32"]},
                           {"name": "out", "io": "out", "dtype": ["float32"]}],
                "precision": {"oracle": "ascendoptest", "standard": "ascendoptest_default"}}

    def test_sync_loosen_caught_by_spec_canonical(self):
        loose = P.threshold_for("ascendoptest_default", "float32")
        loose["error_rate"] = 0.5                        # caseset+evidence 同步抬高（放宽）
        digest = P.threshold_digest(loose)               # tolerance 不变 → digest 仍等 canonical
        caseset, evidence = self._cs_ev(loose, digest)
        vd = V.validate(self._spec(), caseset, evidence)
        self.assertEqual(vd["overall"]["verdict"], "fail")
        self.assertIn("canonical", vd["per_case"][0]["判据"])

    def test_canonical_match_passes(self):
        canon = P.threshold_for("ascendoptest_default", "float32")   # 未放宽
        caseset, evidence = self._cs_ev(canon, P.threshold_digest(canon))
        vd = V.validate(self._spec(), caseset, evidence)
        # 8 坏点 > 16*1e-4 → 精度 fail（但不是契约 fail，说明 canonical 一致、进了 judge）
        self.assertEqual(vd["overall"]["counts"]["contract_problems"], 0)
        self.assertEqual(vd["per_case"][0]["精度"], "fail")


class ValidatorRobustTest(unittest.TestCase):
    """finding #10：坏顶层 JSON（cases/evidence 非列表、case 缺 id）→ contract fail、不崩。"""
    def test_bad_toplevel_no_crash(self):
        spec = {"op": "X", "verify_mode": "numerical", "precision": {"oracle": "ascendoptest"}}
        for cs, ev in (({"cases": None}, {"evidence": []}),
                       ({"cases": [{"dims": []}]}, {"evidence": []}),  # case 缺 id
                       ({"cases": [{"id": "a"}]}, {"evidence": "notalist"})):
            vd = V.validate(spec, cs, ev)
            self.assertEqual(vd["overall"]["verdict"], "fail", (cs, ev))
            self.assertTrue(vd["contract_problems"])


class ValidatorStdlibOnlyTest(unittest.TestCase):
    def test_validator_module_does_not_pull_numpy(self):
        """validator 保持 stdlib-only：全新子进程 import validator 后 numpy 不在 sys.modules。"""
        code = ("import sys; import validator; "
                "assert 'numpy' not in sys.modules, 'validator 拉入了 numpy'; print('OK')")
        r = subprocess.run([sys.executable, "-c", code], cwd=_HERE, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("OK", r.stdout)


# ==================================================== effective-standard-security ===
def _sign_spec(dtype="float32", acc_spec=None):
    """Sign 型完整 IO 矩阵 spec（validator 据此派生 cdtype；同 dtype elementwise，out dtype==in dtype）。"""
    spec = {"op": "Sign", "verify_mode": "numerical",
            "params": [{"name": "self", "io": "in", "dtype": [dtype]},
                       {"name": "out", "io": "out", "dtype": [dtype]}],
            "precision": {"oracle": "ascendoptest", "standard": "ascendoptest_default"}}
    if acc_spec is not None:
        spec["precision"]["acceptance_policy"] = acc_spec
    return spec


def _honest_triple(dtype="float32", metrics=None, acc_spec=None):
    """据 spec 复算的**诚实**三元组（spec/caseset/evidence 全等 canonical）——反事实对照的基准。
    dtype=int* → 有效标准 EXACT（同 gen_cases）。metrics 缺省无坏点（pass）。"""
    spec = _sign_spec(dtype, acc_spec)
    eff = P.effective_standard("ascendoptest_default", dtype,
                               "exact_equal" if P.is_integer_dtype(dtype) else "rel_err")
    pol = P.threshold_for(eff, dtype)
    tpid = P.tolerance_policy_id(eff, dtype)
    dig = P.threshold_digest(pol)
    compare = "exact_equal" if eff == P.EXACT else "rel_err"
    if metrics is None:
        metrics = ({"exact_mismatch": 0, "numel": 16} if eff == P.EXACT
                   else {"bad_count": 0, "numel": 16})
    exp = {"golden_path": "g.npy", "verify_mode": "numerical", "standard": eff,
           "compare_dtype": dtype, "compare": compare, "tolerance_policy_id": tpid,
           "policy": pol, "threshold": dig}
    prec = {"standard": eff, "tolerance_policy_id": tpid, "policy": pol, "threshold": dig,
            "metrics": metrics}
    acc = P.resolve_acceptance(spec, eff, dtype)
    if acc:
        exp["acceptance_policy"], exp["acceptance_tolerance_policy_id"] = acc
        prec["acceptance_policy"], prec["acceptance_tolerance_policy_id"] = acc
        prec["acceptance_metrics"] = metrics
    caseset = {"op": "Sign", "cases": [{"id": "c0", "dims": ["功能", "精度"],
               "inputs": [{"name": "self", "shape": [16], "dtype": dtype}], "expected": exp}]}
    evidence = {"op": "Sign", "evidence": [{"case_id": "c0", "status": "ok", "precision": prec}]}
    return spec, caseset, evidence


class EffectiveStandardSecurityTest(unittest.TestCase):
    """对抗式负例（effective-standard-security）：凡决定「怎么判」的 dtype/口径必须**据 spec 派生**；
    caseset/evidence 谎报 → contract fail。每条对应一个已实跑复现的 exploit。"""

    def test_counterfactual_honest_paths_unchanged(self):
        """反事实对照：诚实 caseset/evidence 裁决**不因加严而误伤**——0 坏点 pass、超阈 fail（非 contract fail）。"""
        spec, cs, ev = _honest_triple("float32", {"bad_count": 0, "numel": 16})
        vd = V.validate(spec, cs, ev)
        self.assertEqual(vd["overall"]["counts"]["contract_problems"], 0)
        self.assertEqual(vd["per_case"][0]["精度"], "pass")
        spec, cs, ev = _honest_triple("float32", {"bad_count": 8, "numel": 16})
        vd = V.validate(spec, cs, ev)                      # 8>16*1e-4 → 精度 fail（是判定 fail 非契约 fail）
        self.assertEqual(vd["overall"]["counts"]["contract_problems"], 0)
        self.assertEqual(vd["per_case"][0]["精度"], "fail")
        # 诚实 int32：有效标准 EXACT，exact_mismatch=1 → fail（无契约问题）
        spec, cs, ev = _honest_triple("int32", {"exact_mismatch": 1, "numel": 16})
        vd = V.validate(spec, cs, ev)
        self.assertEqual(vd["overall"]["counts"]["contract_problems"], 0)
        self.assertEqual(vd["per_case"][0]["精度"], "fail")

    def test_dtype_lie_float32_as_float16_caught(self):
        """exploit A：真 float32（1/1000 坏点该按 float32 门 fail），caseset 谎报 compare_dtype='float16'
        （门松 10×）→ validator 据 spec 派生 float32、强制 compare_dtype 相符 → contract fail（不进 judge）。"""
        spec, cs, ev = _honest_triple("float32", {"bad_count": 1, "numel": 1000})
        cs["cases"][0]["expected"]["compare_dtype"] = "float16"     # 谎报更松 dtype
        vd = V.validate(spec, cs, ev)
        self.assertEqual(vd["overall"]["verdict"], "fail")
        self.assertIn("compare_dtype", vd["per_case"][0]["判据"])
        self.assertNotEqual(vd["per_case"][0]["精度"], "pass")

    def test_int_lie_as_float32_bypasses_exact_caught(self):
        """exploit B：真 int32（应强制 EXACT），caseset 谎报 compare_dtype='float32' 绕过整型判定 →
        validator 据 spec 派生 int32 → compare_dtype 不符 → contract fail（整型→EXACT 基于真实输出 dtype）。"""
        spec, cs, ev = _honest_triple("int32", {"exact_mismatch": 1, "numel": 10000})
        cs["cases"][0]["expected"]["compare_dtype"] = "float32"     # 谎报 float32 想走 AOT 数值口径
        vd = V.validate(spec, cs, ev)
        self.assertEqual(vd["overall"]["verdict"], "fail")
        self.assertIn("compare_dtype", vd["per_case"][0]["判据"])

    def test_acceptance_injection_when_spec_silent_caught(self):
        """exploit C：spec 未 pin acceptance，caseset+evidence **同步注入** error_rate=1.0 的 acceptance →
        validator 据 spec 复算 canonical acceptance=None → 拒绝私带 → contract fail（堵 T5 洞在 acceptance 层重演）。"""
        spec, cs, ev = _honest_triple("float32", {"bad_count": 50, "numel": 1000})  # 无 acc（spec 未声明）
        inj = {"kind": "ascendoptest_default", "tolerance": 1e-4, "error_rate": 1.0,
               "eps": 1e-9, "legacy": 0.1, "not_settled": False}
        cs["cases"][0]["expected"]["acceptance_policy"] = inj
        cs["cases"][0]["expected"]["acceptance_tolerance_policy_id"] = "ascendoptest_default:float32"
        ep = ev["evidence"][0]["precision"]
        ep["acceptance_policy"] = inj
        ep["acceptance_tolerance_policy_id"] = "ascendoptest_default:float32"
        ep["acceptance_metrics"] = {"bad_count": 50, "numel": 1000}
        vd = V.validate(spec, cs, ev)
        self.assertEqual(vd["overall"]["verdict"], "fail")
        self.assertIn("acceptance", vd["per_case"][0]["判据"])
        self.assertNotEqual(vd["overall"]["verdict"], "passed_with_risk")

    def test_acceptance_canonical_loosen_caught(self):
        """spec **声明** acceptance(error_rate=0.1)，但 caseset+evidence 把 acceptance 同步放宽到 1.0 →
        validator 据 spec 复算 canonical acceptance → 三处不等 → contract fail（acceptance 也据 spec 复算）。"""
        spec, cs, ev = _honest_triple(
            "float32", {"bad_count": 50, "numel": 1000},
            acc_spec={"standard": "ascendoptest_default", "error_rate": 0.1})
        loose = dict(cs["cases"][0]["expected"]["acceptance_policy"]); loose["error_rate"] = 1.0
        cs["cases"][0]["expected"]["acceptance_policy"] = loose
        ev["evidence"][0]["precision"]["acceptance_policy"] = loose
        vd = V.validate(spec, cs, ev)
        self.assertEqual(vd["overall"]["verdict"], "fail")
        self.assertIn("acceptance_policy", vd["per_case"][0]["判据"])

    def test_dims_empty_numerical_caught(self):
        """exploit D：numerical case dims=[] 抹掉精度维 + 坏 evidence(999/1000) → dims 受控词表拒空 → contract fail。"""
        spec, cs, ev = _honest_triple("float32", {"bad_count": 999, "numel": 1000})
        cs["cases"][0]["dims"] = []
        ev["evidence"][0]["status"] = "bad"
        vd = V.validate(spec, cs, ev)
        self.assertEqual(vd["overall"]["verdict"], "fail")
        self.assertIn("dims", vd["per_case"][0]["判据"])

    def test_dims_unknown_token_caught(self):
        """dims 含受控词表外 token → contract fail（防伪造维度）。"""
        spec, cs, ev = _honest_triple("float32")
        cs["cases"][0]["dims"] = ["功能", "精度", "玄学"]
        vd = V.validate(spec, cs, ev)
        self.assertEqual(vd["overall"]["verdict"], "fail")

    def test_numerical_case_missing_precision_dim_caught(self):
        """数值 case（非纯性能）dims 缺「精度」→ contract fail（数值 case 不可漏裁精度）。"""
        spec, cs, ev = _honest_triple("float32")
        cs["cases"][0]["dims"] = ["功能"]              # 有功能无精度、又非纯性能
        vd = V.validate(spec, cs, ev)
        self.assertEqual(vd["overall"]["verdict"], "fail")

    def test_op_impersonation_caught(self):
        """exploit #6：另一算子的真通过 caseset+evidence 冒充（op 名不一致）→ 算子身份三处锚定 → contract fail。"""
        spec, cs, ev = _honest_triple("float32")
        cs["op"] = "Equal"                             # caseset 冒充成另一算子
        ev["op"] = "Equal"
        vd = V.validate(spec, cs, ev)
        self.assertEqual(vd["overall"]["verdict"], "fail")
        self.assertTrue(any("身份" in p for p in vd["contract_problems"]))

    def test_input_dtype_not_in_spec_set_caught(self):
        """case 输入 dtype 不在 spec 允许集（IO schema）→ 派生输出 dtype 失败 → contract fail。"""
        spec, cs, ev = _honest_triple("float32")       # spec 只允许 float32
        cs["cases"][0]["inputs"][0]["dtype"] = "float64"   # 越出允许集
        vd = V.validate(spec, cs, ev)
        self.assertEqual(vd["overall"]["verdict"], "fail")

    def test_attr_not_declared_in_spec_caught(self):
        """case.attrs 含 spec 未声明的 attr key → IO schema 不符 → contract fail。"""
        spec, cs, ev = _honest_triple("float32")
        cs["cases"][0]["attrs"] = {"bogus": 12345}
        vd = V.validate(spec, cs, ev)
        self.assertEqual(vd["overall"]["verdict"], "fail")

    def test_spec_without_io_matrix_refuses_precision(self):
        """spec 无 params IO 矩阵 → 无从据 spec 派生 cdtype → 拒绝以 caseset 自声明代替 → contract fail
        （凡决定怎么判的 dtype 必须从 spec 派生；无 spec 锚点则不放行）。"""
        spec, cs, ev = _honest_triple("float32")
        del spec["params"]
        vd = V.validate(spec, cs, ev)
        self.assertEqual(vd["overall"]["verdict"], "fail")


class GoldenTierDerivationTest(unittest.TestCase):
    """golden 档位派生表（批 1）——用户 2026-07-22 裁定 R2/R3/R4/R5/R9 的机器落点。

    最吃重的是 test_exhaustive_*：**穷举全部输入组合**，堵住「派生表自称全覆盖、实则留未定义态」
    这个只靠读表看不出来的洞（上一轮批判点名项）。
    """

    def _g(self, source, method_kind, auth_kind):
        return {"source": source, "method_kind": method_kind,
                "authorization": {"kind": auth_kind, "cite": "", "quote": ""}}

    def test_exhaustive_cartesian_always_returns_valid_triple(self):
        """4 source × 6 method_kind × 4 auth × 2 verified = 192 组，逐组必须有合法返回、无未定义态。"""
        n = 0
        for src in P.GOLDEN_SOURCE_KIND:
            for mk in P.GOLDEN_METHOD_KIND:
                for ak in P.AUTHORIZATION_KIND:
                    for ver in (True, False):
                        n += 1
                        tier, human, reason = P.derive_golden_tier(self._g(src, mk, ak), ver)
                        with self.subTest(src=src, method=mk, auth=ak, verified=ver):
                            self.assertIn(tier, (1, 2, 3, 4))
                            self.assertIsInstance(human, bool)
                            self.assertTrue(reason is None or reason in P.GOLDEN_BLOCKED_REASON)
                            # tier==4 ⟺ 有 blocked_reason（双向，防「挡住了却不说为什么」/「说了却没挡」）
                            self.assertEqual(tier == 4, reason is not None)
                            # requires_human_review 的唯一算法，全组合恒成立
                            self.assertEqual(human, (tier >= 3 or src == "multistep"))
        self.assertEqual(n, 192)

    @staticmethod
    def _expected(src, mk, ak, verified):
        """**独立**重写的期望矩阵——照用户 R2/R3/R4/R5 裁决直述，**不调 derive_golden_tier**。

        上一版穷举测试只断言「返回值在合法值域内」，删掉任意一条派生规则、让那些组合统统落进兜底
        tier 4，测试照样绿——它只能证明「没有未定义态」，证明不了「派生得对」（codex 审出）。
        本矩阵与实现各写一遍、逐组比对，才抓得住规则被删 / 被前面的分支遮蔽 / 顺序写反。
        """
        runnable = mk in ("torch_cpu", "numpy_cpu")
        if src == "needs_user" or mk == "needs_user":
            tier, reason = 4, "needs_user"
        elif ak in ("oracle_method", "formula") and not verified:
            tier, reason = 4, "unverifiable_authorization"
        elif not runnable:
            tier, reason = 4, "method_unavailable"
        elif ak == "oracle_method" and src in ("single_api", "multistep"):
            tier, reason = 1, None
        elif ak == "formula" and src == "multistep":
            tier, reason = 3, None
        elif ak in ("formula", "impl_reference", "none") and src == "single_api":
            tier, reason = 2, None
        elif ak in ("impl_reference", "none") and src == "multistep":
            tier, reason = 4, "unverifiable_authorization"
        else:                                   # external_method 等一切剩余组合
            tier, reason = 4, "unverifiable_authorization"
        return tier, (tier >= 3 or src == "multistep"), reason

    def test_exhaustive_matches_independent_expected_matrix(self):
        """192 组逐组与独立期望矩阵**精确相等**——这条才真正锁住派生规则本身。"""
        for src in P.GOLDEN_SOURCE_KIND:
            for mk in P.GOLDEN_METHOD_KIND:
                for ak in P.AUTHORIZATION_KIND:
                    for ver in (True, False):
                        with self.subTest(src=src, method=mk, auth=ak, verified=ver):
                            self.assertEqual(P.derive_golden_tier(self._g(src, mk, ak), ver),
                                             self._expected(src, mk, ak, ver))

    def test_authorization_verified_must_be_strict_bool(self):
        """安全边界参数不做真值性转换——否则 "false" / 1 这类 truthy 值会把未核授权抬进 tier 1（fail-open）。"""
        g = self._g("single_api", "torch_cpu", "oracle_method")
        for bad in ("false", "no", "", 0, 1, None, [], {}):
            with self.subTest(bad=bad):
                with self.assertRaises(TypeError):
                    P.derive_golden_tier(g, bad)
        # 真布尔才放行，且 False 必须挡住
        self.assertEqual(P.derive_golden_tier(g, True)[0], 1)
        self.assertEqual(P.derive_golden_tier(g, False), (4, True, "unverifiable_authorization"))

    def test_malformed_container_types_do_not_crash(self):
        """g 或 authorization 不是 dict（agent 产坏了）→ 按未定处理落 tier 4，不许抛别的异常。"""
        for g in (None, [], "wat", 42, {"authorization": "not-a-dict"}, {"authorization": None}):
            with self.subTest(g=g):
                tier, _, reason = P.derive_golden_tier(g, True)
                self.assertEqual(tier, 4)
                self.assertIn(reason, P.GOLDEN_BLOCKED_REASON)

    def test_exhaustive_unknown_inputs_still_blocked(self):
        """词表外的垃圾输入（含 None / 空 dict）也必须落 tier 4，绝不放行。"""
        for g in ({}, {"source": "wat"}, {"source": None, "method_kind": "torch_cpu"},
                  {"source": "single_api", "method_kind": "torch_cpu", "authorization": {"kind": "bogus"}}):
            for ver in (True, False):
                tier, _, reason = P.derive_golden_tier(g, ver)
                with self.subTest(g=g, verified=ver):
                    self.assertEqual(tier, 4)
                    self.assertIsNotNone(reason)
        self.assertEqual(P.derive_golden_tier(None, True)[0], 4)

    def test_rule_needs_user_sentinel(self):
        for g in (self._g("needs_user", "torch_cpu", "none"),
                  self._g("single_api", "needs_user", "none")):
            self.assertEqual(P.derive_golden_tier(g, True), (4, True, "needs_user"))

    def test_forged_authorization_is_blocked_not_downgraded(self):
        """**核心防线**：声称任务书授权却核不实 → 直接 blocked，不许降级成 tier 2/3 照跑。

        若假授权只降档，R2（禁 PR 来源）与 R4（指定了但跑不了要 fail-closed）就等于没设。
        """
        for ak in ("oracle_method", "formula"):
            for src in ("single_api", "multistep"):
                g = self._g(src, "torch_cpu", ak)
                self.assertEqual(P.derive_golden_tier(g, False),
                                 (4, True, "unverifiable_authorization"))
                # 同一份输入，授权核实后才允许进 1/2/3
                self.assertLess(P.derive_golden_tier(g, True)[0], 4)

    def test_rule_method_unavailable_does_not_fall_back(self):
        """R4：任务书指定了但本环境跑不起来 → blocked，**不自动回落**第二档（torch/numpy）。"""
        for mk in ("builtin_tbe", "gpu_lib", "other_external"):
            g = self._g("single_api", mk, "oracle_method")
            self.assertEqual(P.derive_golden_tier(g, True), (4, True, "method_unavailable"))
        # external_method 形态同样出局（走穷举兜底）
        self.assertEqual(P.derive_golden_tier(self._g("external_method", "gpu_lib", "oracle_method"), True)[0], 4)

    def test_rule_tier1_taskdoc_specified(self):
        """第一档：任务书就真值口径作出指定 + 本环境跑得起来。单 API 不人核、多步自拼仍人核。"""
        self.assertEqual(P.derive_golden_tier(self._g("single_api", "torch_cpu", "oracle_method"), True),
                         (1, False, None))
        self.assertEqual(P.derive_golden_tier(self._g("multistep", "torch_cpu", "oracle_method"), True),
                         (1, True, None))

    def test_rule_tier3_formula_multistep_requires_human(self):
        """R5 末位档：任务书给公式、自拼多步 → tier 3 且必须人核（catlass 系 LaTeX 任务书归此）。"""
        self.assertEqual(P.derive_golden_tier(self._g("multistep", "numpy_cpu", "formula"), True),
                         (3, True, None))

    def test_rule_tier2_single_api_fallback_no_human(self):
        """R3 第二档 / R5 一级：没有任务书授权，回落 CPU 现成 API 单调 → 正当、**不人核**。

        **Sign 归此**：其任务书只说「参考内置 TBE」(= impl_reference，不构成 golden 授权)，
        一字未提 torch/numpy/公式 → 回落 torch.sign 是对的；样例里写「任务书指定纯重写」才是错的。
        """
        for ak in ("none", "impl_reference"):
            self.assertEqual(P.derive_golden_tier(self._g("single_api", "torch_cpu", ak), True),
                             (2, False, None))
        # 公式恰好等于一个现成 API → 同档
        self.assertEqual(P.derive_golden_tier(self._g("single_api", "torch_cpu", "formula"), True),
                         (2, False, None))

    def test_rule_multistep_without_authorization_is_fabrication(self):
        """无授权却要自拼多步 = 凭空捏造 → blocked。"""
        for ak in ("none", "impl_reference"):
            self.assertEqual(P.derive_golden_tier(self._g("multistep", "numpy_cpu", ak), True),
                             (4, True, "unverifiable_authorization"))

    def test_producible_subset_excludes_repo_and_pr_refs(self):
        """R2：可产集里**没有** cpu_ref（含「PR 的 CPU 参考」）与 catlass_existing_ref 的格子；
        但 canonical 六枚举定义本身不动（改它须走 bureau review）。"""
        self.assertNotIn("cpu_ref", P.PRODUCIBLE_ORACLE_SOURCES)
        self.assertNotIn("catlass_existing_ref", P.PRODUCIBLE_ORACLE_SOURCES)
        self.assertTrue(set(P.PRODUCIBLE_ORACLE_SOURCES) < set(P.ORACLE_SOURCES))
        self.assertEqual(len(P.ORACLE_SOURCES), 6)          # canonical 契约未被本批改动

    def test_single_unknown_sentinel_across_vocabularies(self):
        """未定哨兵全篇只有 needs_user 一个——不许再冒出 undetermined/unknown/TBD 第二套词汇。"""
        for vocab in (P.GOLDEN_SOURCE_KIND, P.GOLDEN_METHOD_KIND):
            self.assertIn("needs_user", vocab)
            for bad in ("undetermined", "unknown", "tbd", "TBD"):
                self.assertNotIn(bad, vocab)


class VerifyAuthorizationTest(unittest.TestCase):
    """任务书授权锚核验（R12：全文快照入库 → 锚才可机器核）。"""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="oprunway_auth_")
        self.snap = os.path.join(self.d, P.TASKDOC_SNAPSHOT_NAME)
        body = ("# IsClose 任务书\n"                       # 1
                "\n"                                        # 2
                "实现方式从原来比较二进制的实现方式，更改成和cpu一致的比较逻辑值的实现方式\n"   # 3
                "\n"                                        # 4
                "精度需满足 AscendOpTest 工具默认阈值\n")   # 5
        with open(self.snap, "w", encoding="utf-8") as fh:
            fh.write(body)
        self.sha = hashlib.sha256(body.encode("utf-8")).hexdigest()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _g(self, kind="oracle_method", cite="task_doc.snapshot.md:3", quote="更改成和cpu一致的比较逻辑值", sha=None):
        return {"taskdoc_snapshot": {"sha256": self.sha if sha is None else sha},
                "authorization": {"kind": kind, "cite": cite, "quote": quote}}

    def test_happy_path(self):
        ok, reason = P.verify_authorization(self._g(), self.snap)
        self.assertTrue(ok, reason)
        self.assertIsNone(reason)

    def test_line_range_form(self):
        self.assertTrue(P.verify_authorization(
            self._g(cite="task_doc.snapshot.md:1-5", quote="AscendOpTest"), self.snap)[0])

    def test_no_anchor_needed_for_non_authorizing_kinds(self):
        """impl_reference / none 本就不构成授权 → 无需锚，直接放行（档位另由派生表按无授权处理）。"""
        for kind in ("impl_reference", "none"):
            self.assertEqual(P.verify_authorization({"authorization": {"kind": kind}}, None), (True, None))

    def test_quote_not_in_cited_range_is_rejected(self):
        """引了一句原文里没有的话 → 拒。这是本函数的主要价值。"""
        ok, reason = P.verify_authorization(self._g(quote="任务书指定纯重写为 torch.sign"), self.snap)
        self.assertFalse(ok)
        self.assertIn("逐字子串", reason)

    def test_quote_in_file_but_wrong_line_is_rejected(self):
        """引文确在文中、但不在 cite 指的行区间 → 拒（防「行号随手写」）。"""
        ok, _ = P.verify_authorization(self._g(cite="task_doc.snapshot.md:5", quote="比较逻辑值"), self.snap)
        self.assertFalse(ok)

    def test_snapshot_hash_mismatch_is_rejected(self):
        ok, reason = P.verify_authorization(self._g(sha="0" * 64), self.snap)
        self.assertFalse(ok)
        self.assertIn("指纹不符", reason)

    def test_missing_snapshot_is_rejected(self):
        ok, _ = P.verify_authorization(self._g(), os.path.join(self.d, "nope.md"))
        self.assertFalse(ok)

    def test_cite_pointing_outside_taskdoc_is_rejected(self):
        """R2 落到机器上的那一刀：PR / 仓内文件连被引用的资格都没有。"""
        for bad in ("pr_facts.json:12", "../pr_facts.json:12", "repos/foo/impl.py:3",
                    "task_doc.md:3", "/abs/task_doc.snapshot.md:3"):
            ok, reason = P.verify_authorization(self._g(cite=bad), self.snap)
            with self.subTest(cite=bad):
                self.assertFalse(ok)
                self.assertIn("格式非法", reason)

    def test_out_of_range_and_empty_quote_rejected(self):
        self.assertFalse(P.verify_authorization(self._g(cite="task_doc.snapshot.md:99"), self.snap)[0])
        self.assertFalse(P.verify_authorization(self._g(quote="   "), self.snap)[0])

    def test_malformed_container_types_are_fail_closed_not_crash(self):
        """与 derive_golden_tier 对称的容器类型防御：坏类型必须返 (False, 原因)，**不许抛 AttributeError**
        ——异常在调用方可能被 except 吞成放行，那就成了 fail-open。"""
        for g in (None, [], "wat", 42,
                  {"authorization": "not-a-dict"}, {"authorization": None},
                  {"authorization": {"kind": "oracle_method"}, "taskdoc_snapshot": "not-a-dict"}):
            with self.subTest(g=g):
                ok, reason = P.verify_authorization(g, self.snap)
                self.assertFalse(ok)
                self.assertTrue(reason)

    def test_missing_snapshot_fingerprint_is_fail_closed(self):
        g = self._g()
        del g["taskdoc_snapshot"]
        ok, reason = P.verify_authorization(g, self.snap)
        self.assertFalse(ok)
        self.assertIn("快照指纹", reason)


class GoldenContractStdlibTest(unittest.TestCase):
    def test_import_does_not_pull_numpy(self):
        """批 1 新增的顶层 import 必须全是 stdlib——validator 靠 `import precision_policy` 保持 stdlib-only。"""
        code = ("import sys; sys.modules.pop('numpy', None); import precision_policy; "
                "print('numpy' in sys.modules)")
        out = subprocess.run([sys.executable, "-c", code], cwd=_HERE,
                             capture_output=True, text=True, check=True)
        self.assertEqual(out.stdout.strip(), "False")


def _median_spec(tol_src="dtype_table", in_dtypes=("float32", "float16", "bfloat16", "int32")):
    """见证用 median spec（多输出 values+indices；op-中立字段驱动，非按算子名）。

    ⚠ `indices` 必带 `gather_from`（审计 finding #7）：index 判据的 gather 源只能由 spec 锚定，
    不得取「caseset 的第一个输入」。`call_variants` 声明「dim=null → 只落 values」——本 case
    该有哪些输出由 **spec** 说了算（审计严重#1）。"""
    return {
        "op": "Median", "verify_mode": "numerical",
        "precision": {"oracle": "torch", "standard": "torch_allclose", "tolerance_source": tol_src},
        "params": [
            {"name": "self", "io": "in", "dtype": list(in_dtypes)},
            {"name": "dim", "io": "attr", "dtype": ["int64"]},
            {"name": "keepdim", "io": "attr", "dtype": ["bool"]},
            {"name": "values", "io": "out", "out_role": "value", "dtype": ["<from_input>"]},
            {"name": "indices", "io": "out", "out_role": "index", "index_of": "values",
             "gather_from": "self", "dtype": ["int64"]},
        ],
        "call_variants": [
            {"when": {"attr": "dim", "is_null": True}, "symbol": "Median",
             "active_attrs": [], "active_outputs": ["values"]},
            {"when": {"attr": "dim", "is_null": False}, "symbol": "MedianDim",
             "active_attrs": ["dim", "keepdim"], "active_outputs": ["values", "indices"]},
        ],
    }


class TorchAllcloseStandardTest(unittest.TestCase):
    """WI-A1：torch_allclose standard 接入——select/threshold_for/digest/compute_metrics 语义。"""

    def test_select_standard_torch_maps_allclose(self):
        # oracle=torch（无显式 standard）→ torch_allclose（不再 raise）
        self.assertEqual(P.select_standard({"verify_mode": "numerical",
                         "precision": {"oracle": "torch"}}), P.TORCH_ALLCLOSE)
        # 显式 standard=torch_allclose 优先放行
        self.assertEqual(P.select_standard({"verify_mode": "numerical",
                         "precision": {"standard": "torch_allclose"}}), P.TORCH_ALLCLOSE)

    def test_threshold_for_dtype_table_values(self):
        """dtype_table 逐 dtype (rtol, atol)——抄自参考仓 accuracy.py:47-54（存 atol,rtol，本表转成 rtol,atol）。"""
        exp = {"float16": (2 ** -10, 9e-2), "bfloat16": (2 ** -7, 1e-1),
               "float32": (2 ** -13, 1e-3), "float64": (2 ** -30, 1e-6)}
        for dt, (rtol, atol) in exp.items():
            pol = P.threshold_for("torch_allclose", dt)   # 缺省 tolerance_source=dtype_table
            self.assertEqual(pol["kind"], "torch_allclose", dt)
            self.assertAlmostEqual(pol["rtol"], rtol, places=12, msg=dt)
            self.assertAlmostEqual(pol["atol"], atol, places=12, msg=dt)
            self.assertIs(pol["equal_nan"], True, dt)

    def test_threshold_for_torch_default_and_taskdoc(self):
        pd = P.threshold_for("torch_allclose", "float32", "torch_default")
        self.assertAlmostEqual(pd["rtol"], 1e-5); self.assertAlmostEqual(pd["atol"], 1e-8)
        pt = P.threshold_for("torch_allclose", "float32", "taskdoc", taskdoc_tol=(3e-4, 5e-3))
        self.assertAlmostEqual(pt["rtol"], 3e-4); self.assertAlmostEqual(pt["atol"], 5e-3)
        with self.assertRaises(ValueError):   # taskdoc 缺 taskdoc_tol → fail-closed
            P.threshold_for("torch_allclose", "float32", "taskdoc")

    def test_threshold_for_dtype_table_rejects_int(self):
        with self.assertRaises(ValueError):   # 整型应走 exact，不在 dtype_table
            P.threshold_for("torch_allclose", "int32")

    def test_threshold_digest_torch_allclose(self):
        """finding #6：digest 必须是 **JSON-native list**（tuple 落 JSON 变 list → 往返后恒不等）。"""
        pol = P.threshold_for("torch_allclose", "float32")
        self.assertEqual(P.threshold_digest(pol), [pol["rtol"], pol["atol"]])
        ipol = {"kind": "index_value_consistency", "gather_from": "self",
                "value_rtol": 0.1, "value_atol": 0.2}
        self.assertEqual(P.threshold_digest(ipol), [0.1, 0.2])

    def test_threshold_digest_json_roundtrip_stable(self):
        """负向（finding #6）：落盘 JSON 再读回来必须与内存值**相等**——旧的 tuple 返回值在这里必挂。"""
        for pol in (P.threshold_for("torch_allclose", "float32"),
                    P.threshold_for("torch_allclose", "float16"),
                    P.threshold_for("exact", "int32"),
                    P.threshold_for("ascendoptest_default", "float32"),
                    {"kind": "index_value_consistency", "gather_from": "self",
                     "value_rtol": 0.1, "value_atol": 0.2}):
            d = P.threshold_digest(pol)
            self.assertEqual(json.loads(json.dumps(d)), d, pol.get("kind"))

    def test_compute_metrics_allclose_semantics(self):
        pol = P.threshold_for("torch_allclose", "float32")   # rtol 2^-13, atol 1e-3
        g = np.array([1.0, 100.0, 0.0], dtype=np.float32)
        o_ok = np.array([1.0005, 100.01, 0.0005], dtype=np.float32)
        self.assertEqual(P.compute_metrics(o_ok, g, pol)["mismatch"], 0)
        o_bad = np.array([1.5, 100.0, 0.0], dtype=np.float32)   # elem0 diff 0.5 ≫ tol
        self.assertEqual(P.compute_metrics(o_bad, g, pol)["mismatch"], 1)

    def test_compute_metrics_allclose_equal_nan(self):
        pol = P.threshold_for("torch_allclose", "float32")   # equal_nan True
        g = np.array([np.nan, 1.0], dtype=np.float32)
        self.assertEqual(P.compute_metrics(np.array([np.nan, 1.0], np.float32), g, pol)["mismatch"], 0)
        # 单侧 NaN（数值 vs NaN）必须记 mismatch（不能被 nan>thr=False 吞掉）
        self.assertEqual(P.compute_metrics(np.array([2.0, 1.0], np.float32), g, pol)["mismatch"], 1)

    def test_compute_metrics_dtype_mismatch_fail_fast(self):
        pol = P.threshold_for("torch_allclose", "float32")
        with self.assertRaises(ValueError):
            P.compute_metrics(np.ones(3, np.float32), np.ones(3, np.float64), pol)


class IndexValueConsistencyTest(unittest.TestCase):
    """WI-A4：index_value_consistency——gather(self,idx) 一致性判据 + 多输出契约派生。"""

    def test_derive_output_contracts_median_float(self):
        spec = _median_spec()
        cts = P.derive_output_contracts(spec, [("self", "float32")], "torch_allclose", "dtype_table")
        self.assertEqual([c["role"] for c in cts], ["value", "index"])
        v, idx = cts
        self.assertEqual(v["standard"], "torch_allclose")
        self.assertEqual(v["policy"]["kind"], "torch_allclose")
        self.assertEqual(v["tolerance_policy_id"], "torch_allclose:float32")
        self.assertEqual(idx["policy"]["kind"], "index_value_consistency")
        self.assertEqual(idx["policy"]["gather_from"], "self")
        # index 的 value 容差 == 所引 value 输出的 rtol/atol
        self.assertAlmostEqual(idx["policy"]["value_rtol"], v["policy"]["rtol"])
        self.assertAlmostEqual(idx["policy"]["value_atol"], v["policy"]["atol"])
        self.assertEqual(idx["standard"], "torch_allclose")

    def test_derive_output_contracts_median_int_value_exact(self):
        """int32 → value 输出走 EXACT（int→exact），index 的 value 容差 (0,0)。"""
        spec = _median_spec()
        cts = P.derive_output_contracts(spec, [("self", "int32")], "torch_allclose", "dtype_table")
        v, idx = cts
        self.assertEqual(v["standard"], "exact")
        self.assertEqual(v["policy"]["kind"], "exact")
        self.assertEqual((idx["policy"]["value_rtol"], idx["policy"]["value_atol"]), (0.0, 0.0))
        self.assertEqual(idx["standard"], "exact")

    def test_index_consistency_tie_pass(self):
        """tie 输入两组不同 index、gather 值一致 → mismatch 0（合法 tie，不是 bug）。"""
        self_arr = np.array([[3.0, 1.0, 3.0, 1.0]], dtype=np.float32)   # dim=1；median(偶4)=1.0，tie pos1/pos3
        pol = {"kind": "index_value_consistency", "gather_from": "self",
               "value_rtol": 2 ** -13, "value_atol": 1e-3}
        ctx = {"source": self_arr, "dim": 1, "keepdim": False}
        m = P.compute_metrics(np.array([3], np.int64), np.array([1], np.int64), pol, ctx)
        self.assertEqual(m["mismatch"], 0)
        self.assertEqual(m["numel"], 1)

    def test_index_consistency_value_diff_fail(self):
        """index 指向值不同（真下标 bug）→ mismatch>0。"""
        self_arr = np.array([[3.0, 1.0, 3.0, 1.0]], dtype=np.float32)
        pol = {"kind": "index_value_consistency", "gather_from": "self",
               "value_rtol": 2 ** -13, "value_atol": 1e-3}
        ctx = {"source": self_arr, "dim": 1, "keepdim": False}
        m = P.compute_metrics(np.array([0], np.int64), np.array([1], np.int64), pol, ctx)  # self[0,0]=3 vs golden 1
        self.assertEqual(m["mismatch"], 1)

    def test_index_consistency_keepdim(self):
        self_arr = np.array([[5.0, 2.0, 5.0]], dtype=np.float32)       # dim=1 keepdim → idx shape (1,1)
        pol = {"kind": "index_value_consistency", "gather_from": "self",
               "value_rtol": 2 ** -13, "value_atol": 1e-3}
        ctx = {"source": self_arr, "dim": 1, "keepdim": True}
        m = P.compute_metrics(np.array([[2]], np.int64), np.array([[0]], np.int64), pol, ctx)  # 5.0 vs 5.0 tie
        self.assertEqual(m["mismatch"], 0)

    def test_index_consistency_needs_gather_ctx(self):
        pol = {"kind": "index_value_consistency", "gather_from": "self",
               "value_rtol": 0.0, "value_atol": 0.0}
        with self.assertRaises(ValueError):
            P.compute_metrics(np.array([0], np.int64), np.array([0], np.int64), pol)


# ============================== 审计负向回归（R2-L1：9 条 finding 的「被逮住」证据）==============
class InfSemanticsTest(unittest.TestCase):
    """finding #3：inf 四象限——value / index 两条路径**复用同一实现**、语义与 torch.allclose 一致。

    旧洞两条互相矛盾：value 路径 `_replace_inf` 把 ±inf 换成 ±finfo.max（→ `finfo.max` 冒充 `+inf`
    被判相等）；index 路径不换（→ `inf-inf=NaN` 把**同号 inf** 判成失配）。"""

    POL = {"kind": "torch_allclose", "rtol": 1e-3, "atol": 1e-5, "equal_nan": True}

    def _mism(self, o, g):
        return P.compute_metrics(np.array(o, np.float32), np.array(g, np.float32), self.POL)["mismatch"]

    def test_same_sign_inf_equal(self):
        self.assertEqual(self._mism([np.inf], [np.inf]), 0)
        self.assertEqual(self._mism([-np.inf], [-np.inf]), 0)

    def test_opposite_sign_inf_mismatch(self):
        self.assertEqual(self._mism([np.inf], [-np.inf]), 1)
        self.assertEqual(self._mism([-np.inf], [np.inf]), 1)

    def test_finite_max_is_not_inf(self):
        """⭐ 核心负向：`actual=finfo.max, golden=+inf` **必须失配**（旧 `_replace_inf` 判它相等）。"""
        fmax = float(np.finfo(np.float32).max)
        self.assertEqual(self._mism([fmax], [np.inf]), 1)
        self.assertEqual(self._mism([np.inf], [fmax]), 1)

    def test_finite_pair_uses_allclose_formula(self):
        self.assertEqual(self._mism([1.0], [1.0]), 0)
        self.assertEqual(self._mism([2.0], [1.0]), 1)

    def test_nan_only_by_equal_nan(self):
        self.assertEqual(self._mism([np.nan], [np.nan]), 0)                  # equal_nan=True
        self.assertEqual(self._mism([np.nan], [1.0]), 1)
        self.assertEqual(self._mism([1.0], [np.nan]), 1)
        strict = dict(self.POL, equal_nan=False)
        m = P.compute_metrics(np.array([np.nan], np.float32), np.array([np.nan], np.float32), strict)
        self.assertEqual(m["mismatch"], 1)
        # NaN vs inf 也必须失配（别被任何一侧的特判吞掉）
        self.assertEqual(self._mism([np.nan], [np.inf]), 1)

    def test_index_path_shares_same_inf_semantics(self):
        """index 路径的 gather 值命中 inf 时，判定必须与 value 路径**逐条相同**（旧洞方向相反）。"""
        pol = {"kind": "index_value_consistency", "gather_from": "self",
               "value_rtol": 1e-3, "value_atol": 1e-5}
        src = np.array([[np.inf, np.inf, 1.0]], dtype=np.float32)
        ctx = {"source": src, "dim": 1, "keepdim": False}
        # 两个下标都指向 +inf → gather 后同号 inf → 相等（旧实现在此判失配）
        m = P.compute_metrics(np.array([0], np.int64), np.array([1], np.int64), pol, ctx)
        self.assertEqual(m["mismatch"], 0)
        # 一侧 inf、一侧有限 → 失配
        m2 = P.compute_metrics(np.array([0], np.int64), np.array([2], np.int64), pol, ctx)
        self.assertEqual(m2["mismatch"], 1)


class IndexGuardTest(unittest.TestCase):
    """finding #4：index 的类型闸 + 逐元素越界闸（旧洞：astype(intp) 静默截断浮点、负下标被回绕）。"""

    POL = {"kind": "index_value_consistency", "gather_from": "self",
           "value_rtol": 1e-3, "value_atol": 1e-5}
    SRC = np.array([[3.0, 1.0, 4.0]], dtype=np.float32)
    CTX = {"source": SRC, "dim": 1, "keepdim": False}

    def _run(self, a, g):
        return P.compute_metrics(a, g, self.POL, self.CTX)

    def test_negative_index_rejected(self):
        """⭐ 旧洞：actual index=-1 被 take_along_axis 回绕成最后一个元素 → mismatch=0 假通过。"""
        with self.assertRaises(ValueError) as cm:
            self._run(np.array([-1], np.int64), np.array([2], np.int64))
        self.assertIn("越界", str(cm.exception))

    def test_out_of_range_index_rejected(self):
        with self.assertRaises(ValueError):
            self._run(np.array([3], np.int64), np.array([0], np.int64))

    def test_float_index_rejected(self):
        """⭐ 旧洞：`[0.9]` 被 astype(intp) 静默截成 `[0]`。"""
        with self.assertRaises(ValueError) as cm:
            self._run(np.array([0.9], np.float32), np.array([0], np.int64))
        self.assertIn("非整数", str(cm.exception))

    def test_bool_index_rejected(self):
        with self.assertRaises(ValueError):
            self._run(np.array([True], np.bool_), np.array([1], np.int64))

    def test_cross_integer_dtype_rejected(self):
        with self.assertRaises(ValueError):
            self._run(np.array([0], np.int32), np.array([0], np.int64))

    def test_legit_index_still_works(self):
        self.assertEqual(self._run(np.array([2], np.int64), np.array([2], np.int64))["mismatch"], 0)


class ToleranceFailClosedTest(unittest.TestCase):
    """finding #5：容差源与容差值一律受控——只有 `None` 落缺省，rtol/atol 须非 bool、有限、非负。"""

    def test_only_none_falls_back_to_default_source(self):
        self.assertEqual(P.threshold_for("torch_allclose", "float32")["rtol"], 2 ** -13)
        for bad in ("", False, 0, "dtype-table", [], {}):
            with self.assertRaises(ValueError, msg=repr(bad)):
                P.threshold_for("torch_allclose", "float32", bad)

    def test_taskdoc_rtol_inf_rejected(self):
        """⭐ 旧洞：`rtol=inf` 能生成 canonical policy → `|o-g| <= atol + inf*|g|` 恒真、判据整条废掉。"""
        with self.assertRaises(ValueError) as cm:
            P.threshold_for("torch_allclose", "float32", "taskdoc", taskdoc_tol=(float("inf"), 1e-8))
        self.assertIn("有限", str(cm.exception))

    def test_taskdoc_bad_values_rejected(self):
        for bad in ((float("nan"), 1e-8), (1e-5, float("inf")), (-1e-5, 1e-8), (1e-5, -1.0),
                    (True, 1e-8), (1e-5, None), ("1e-5", 1e-8)):
            with self.assertRaises(ValueError, msg=repr(bad)):
                P.threshold_for("torch_allclose", "float32", "taskdoc", taskdoc_tol=bad)

    def test_taskdoc_valid_values_accepted(self):
        pol = P.threshold_for("torch_allclose", "float32", "taskdoc", taskdoc_tol=(0.0, 0.0))
        self.assertEqual((pol["rtol"], pol["atol"]), (0.0, 0.0))

    def test_compute_metrics_rejects_inf_policy_tolerance(self):
        """policy 直接被塞 inf（绕开 threshold_for）也必须在复算入口被拒。"""
        pol = {"kind": "torch_allclose", "rtol": float("inf"), "atol": 1e-8, "equal_nan": True}
        with self.assertRaises(ValueError):
            P.compute_metrics(np.array([9e9], np.float32), np.array([1.0], np.float32), pol)

    def test_acceptance_override_values_are_checked(self):
        """同族残留：acceptance 的覆盖字段旧写法原样搬运 → `{"tolerance": inf}` 能造出恒真阈值。

        acceptance 允许**放宽**（由 risk/passed_with_risk 如实上报），但不许放宽成 inf/NaN/负数。"""
        base = {"verify_mode": "numerical", "precision": {"oracle": "ascendoptest"}}
        for bad in (float("inf"), float("nan"), -1.0, True, "0.1", None):
            spec = copy.deepcopy(base)
            spec["precision"]["acceptance_policy"] = {"standard": "ascendoptest_default", "tolerance": bad}
            with self.assertRaises(ValueError, msg=repr(bad)):
                P.resolve_acceptance(spec, "ascendoptest_default", "float32")
        ok = copy.deepcopy(base)
        ok["precision"]["acceptance_policy"] = {"standard": "ascendoptest_default", "error_rate": 0.1}
        pol, tpid = P.resolve_acceptance(ok, "ascendoptest_default", "float32")
        self.assertEqual(pol["error_rate"], 0.1)
        self.assertEqual(tpid, "ascendoptest_default:float32")

    def test_malformed_precision_block_is_fail_closed_not_crash(self):
        """`spec.precision` 是字符串时，旧写法 `(spec.get("precision") or {}).get(...)` 抛 AttributeError
        （逃出 validator 的 `except (ValueError, KeyError)`）。现在统一收敛成 ValueError。"""
        spec = {"verify_mode": "numerical", "precision": "oops"}
        with self.assertRaises(ValueError):
            P.resolve_acceptance(spec, "ascendoptest_default", "float32")
        with self.assertRaises(ValueError):
            P.derive_acceptance_contracts(spec, [])


class ComplexPolicyConsistencyTest(unittest.TestCase):
    """finding #9：不留「能生成、算不出来」的 policy——complex 要么实现、要么不出 policy。"""

    def test_complex_not_in_torch_allclose_table(self):
        for dt in ("complex64", "complex128"):
            with self.assertRaises(ValueError, msg=dt):
                P.threshold_for("torch_allclose", dt)

    def test_complex_ascendoptest_fails_fast_not_produce_dead_policy(self):
        """`_AOT_TABLE` 保留 complex 快照作 provenance，但 threshold_for 当场 fail-fast，不产出死 policy。"""
        for dt in ("complex64", "complex128"):
            with self.assertRaises(ValueError, msg=dt):
                P.threshold_for("ascendoptest_default", dt)
            self.assertNotIn(dt, P.SUPPORTED_COMPUTE_DTYPES)


class GatherFromAnchorTest(unittest.TestCase):
    """finding #7：gather 源由 spec 的必填 `gather_from` 锚定，不随 case.inputs 顺序漂移。"""

    def _two_input_spec(self, gather_from="a"):
        idx = {"name": "indices", "io": "out", "out_role": "index", "index_of": "values",
               "dtype": ["int64"]}
        if gather_from is not None:
            idx["gather_from"] = gather_from
        return {"op": "TwoIn", "verify_mode": "numerical",
                "precision": {"oracle": "torch", "standard": "torch_allclose"},
                "params": [{"name": "a", "io": "in", "dtype": ["float32"]},
                           {"name": "b", "io": "in", "dtype": ["float32"]},
                           {"name": "values", "io": "out", "out_role": "value",
                            "dtype": ["<from_input>"]}, idx]}

    def test_missing_gather_from_rejected(self):
        with self.assertRaises(ValueError) as cm:
            P.derive_output_contracts(self._two_input_spec(gather_from=None),
                                      [("a", "float32"), ("b", "float32")], "torch_allclose")
        self.assertIn("gather_from", str(cm.exception))

    def test_gather_from_must_point_at_named_input(self):
        with self.assertRaises(ValueError):
            P.derive_output_contracts(self._two_input_spec("nope"),
                                      [("a", "float32"), ("b", "float32")], "torch_allclose")

    def test_reordering_case_inputs_cannot_move_gather_source(self):
        """⭐ 旧洞：gather 源取「case 的第一个输入」→ 调 case.inputs 顺序即换掉 canonical。现在换序直接拒。"""
        spec = self._two_input_spec("a")
        cts = P.derive_output_contracts(spec, [("a", "float32"), ("b", "float32")], "torch_allclose")
        self.assertEqual(cts[1]["policy"]["gather_from"], "a")
        with self.assertRaises(ValueError) as cm:                 # 换序 → 拒（不是「换个 gather 源照跑」）
            P.derive_output_contracts(spec, [("b", "float32"), ("a", "float32")], "torch_allclose")
        self.assertIn("同一序同一身份", str(cm.exception))

    def test_incomplete_or_duplicate_case_inputs_rejected(self):
        spec = self._two_input_spec("a")
        for bad in ([("a", "float32")], [("a", "float32"), ("a", "float32")],
                    [("a", "float32"), ("b", "float32"), ("b", "float32")]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                P.derive_output_contracts(spec, bad, "torch_allclose")


class TwoValueOutputsTest(unittest.TestCase):
    """finding #8：身份主键是 **name**，role 只决定判据类型 → 合法的「两个 value 输出」必须可判。"""

    SPEC = {"op": "TwoVal", "verify_mode": "numerical",
            "precision": {"oracle": "torch", "standard": "torch_allclose",
                          "tolerance_source": "dtype_table"},
            "params": [{"name": "self", "io": "in", "dtype": ["float32"]},
                       {"name": "out_a", "io": "out", "out_role": "value", "dtype": ["<from_input>"]},
                       {"name": "out_b", "io": "out", "out_role": "value", "dtype": ["float32"]}]}

    def test_two_value_contracts_derivable(self):
        cts = P.derive_output_contracts(self.SPEC, [("self", "float32")], "torch_allclose", "dtype_table")
        self.assertEqual([c["name"] for c in cts], ["out_a", "out_b"])
        self.assertEqual([c["role"] for c in cts], ["value", "value"])
        self.assertEqual([c["policy"]["kind"] for c in cts], ["torch_allclose", "torch_allclose"])

    def test_duplicate_output_names_rejected(self):
        spec = json.loads(json.dumps(self.SPEC))
        spec["params"][2]["name"] = "out_a"
        with self.assertRaises(ValueError) as cm:
            P.derive_output_contracts(spec, [("self", "float32")], "torch_allclose", "dtype_table")
        self.assertIn("重复", str(cm.exception))


class ActiveOutputsFromSpecTest(unittest.TestCase):
    """严重#1 的派生侧：本 case 该有哪些输出，只由 spec × attrs 决定（caseset 无权参与）。"""

    def test_variant_selects_output_set(self):
        spec = _median_spec()
        self.assertEqual(P.active_output_names(spec, {"dim": 1, "keepdim": False}),
                         ["values", "indices"])
        self.assertEqual(P.active_output_names(spec, {"dim": None, "keepdim": False}), ["values"])

    def test_no_matching_variant_fail_closed(self):
        spec = _median_spec()
        spec["call_variants"] = [{"when": {"attr": "dim", "equals": 0}, "symbol": "S",
                                  "active_outputs": ["values", "indices"]}]
        with self.assertRaises(ValueError):
            P.active_output_names(spec, {"dim": 7})

    def test_no_variants_means_all_spec_outputs(self):
        spec = _median_spec()
        spec.pop("call_variants")
        self.assertEqual(P.active_output_names(spec, {"dim": 1}), ["values", "indices"])

    def test_index_without_its_value_is_fail_closed(self):
        spec = _median_spec()
        spec["call_variants"] = [{"when": {"always": True}, "symbol": "S",
                                  "active_outputs": ["indices"]}]
        with self.assertRaises(ValueError) as cm:
            P.active_output_names(spec, {"dim": 1})
        self.assertIn("index_value_consistency", str(cm.exception))

    def test_uses_output_contract_is_spec_driven(self):
        self.assertTrue(P.uses_output_contract(_median_spec()))
        legacy = {"params": [{"name": "x", "io": "in", "dtype": ["float32"]},
                             {"name": "y", "io": "out", "dtype": ["float32"]}]}
        self.assertFalse(P.uses_output_contract(legacy))
        # `out_role: ""` 是写坏的 spec —— 必须仍走多输出路径、在受控词表上炸掉，不许悄悄退回 legacy
        broken = {"params": [{"name": "x", "io": "in", "dtype": ["float32"]},
                             {"name": "y", "io": "out", "out_role": "", "dtype": ["float32"]}]}
        self.assertTrue(P.uses_output_contract(broken))


class MultiOutputAcceptanceContractTest(unittest.TestCase):
    """finding #2 的派生侧：多输出 acceptance canonical 逐输出复算，取不出容差就 fail-closed。"""

    def test_none_when_spec_silent(self):
        spec = _median_spec()
        cts = P.derive_output_contracts(spec, [("self", "float32")], "torch_allclose", "dtype_table")
        self.assertIsNone(P.derive_acceptance_contracts(spec, cts))

    def test_acceptance_applied_per_output_and_index_inherits(self):
        spec = _median_spec()
        spec["precision"]["acceptance_policy"] = {"standard": "torch_allclose"}
        cts = P.derive_output_contracts(spec, [("self", "float32")], "torch_allclose", "dtype_table")
        accs = P.derive_acceptance_contracts(spec, cts)
        self.assertEqual(len(accs), 2)
        self.assertEqual(accs[0]["policy"]["kind"], "torch_allclose")
        self.assertEqual(accs[1]["policy"]["kind"], "index_value_consistency")
        # index 的 acceptance 容差 == 所引 value 输出的 acceptance 容差（不是 standard 层的）
        self.assertAlmostEqual(accs[1]["policy"]["value_rtol"], accs[0]["policy"]["rtol"])

    def test_unsupported_acceptance_kind_for_index_fail_closed(self):
        """acceptance 底是 ascendoptest_default → index 取不出 (rtol,atol) → **拒**，绝不静默退回 standard。"""
        spec = _median_spec()
        spec["precision"]["acceptance_policy"] = {"standard": "ascendoptest_default"}
        cts = P.derive_output_contracts(spec, [("self", "float32")], "torch_allclose", "dtype_table")
        with self.assertRaises(ValueError):
            P.derive_acceptance_contracts(spec, cts)

    def test_int_value_output_inherits_standard(self):
        """int32 → 有效标准 exact（阈值已是 0，没有可放宽的 acceptance）→ 该输出 acceptance 继承 standard。"""
        spec = _median_spec()
        spec["precision"]["acceptance_policy"] = {"standard": "torch_allclose"}
        cts = P.derive_output_contracts(spec, [("self", "int32")], "torch_allclose", "dtype_table")
        accs = P.derive_acceptance_contracts(spec, cts)
        self.assertEqual(accs, [None, None])


if __name__ == "__main__":
    unittest.main()
