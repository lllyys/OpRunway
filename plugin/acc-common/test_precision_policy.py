"""精度口径 SSOT 单测（T5）——precision_policy 复算 + validator 纯算术 judge + 端到端 PASSED_WITH_RISK。

跑: cd plugin/acc-common && python3 test_precision_policy.py
覆盖（对齐 plan Acceptance #1/#2/#4）：
  · AscendOpTest 逐 dtype tol+error_rate；坏点边界 floor(n*error_rate)+1（非单点）；|e|≥1 rel/<1 abs 共用 tol；
    inf/NaN 语义；denom max(|e|,|o|)+1e-9。
  · MERE=平均/MARE=最大**不对调** + 10× 规则 + Th 表(2^-k) + denom |g|+1e-7；NOT_SETTLED；单标杆不过→uncertain（非 fail）。
  · exact：mismatch<=0；未支持 dtype fail-fast；select_standard 向后兼容映射。
  · PASSED_WITH_RISK 路径（acceptance_policy 宽于 standard）→ run_workflow 退出码 2 + requires_human_cp。
"""
import json, os, subprocess, sys, tempfile, shutil, unittest
import numpy as np

import precision_policy as P
import validator as V

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


class ValidatorRiskTest(unittest.TestCase):
    """validator 层：acceptance 过 & standard 不过 → risk=true、overall=passed_with_risk。"""
    def _spec(self):
        return {"op": "X", "verify_mode": "numerical",
                "precision": {"oracle": "ascendoptest", "standard": "ascendoptest_default"}}

    def _pair(self, std_pol, acc_pol, metrics, acc_metrics):
        cid = "x_000"
        caseset = {"op": "X", "cases": [{
            "id": cid, "dims": ["功能", "精度"],
            "inputs": [{"name": "a", "shape": [16], "dtype": "float32"}],
            "expected": {"golden_path": "g.npy", "verify_mode": "numerical",
                         "standard": "ascendoptest_default",
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
        # T7：语义化稳定 id——Sign fp32 的 (16,) 用例 = sign_float32_16_varied（弃旧索引 sign_000）
        defect_id = "sign_float32_16_varied"
        r = subprocess.run([sys.executable, os.path.join(_HERE, "run_workflow.py"), spec_path,
                            "--mode", "mock", "--out", out, "--defect", defect_id],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        with open(os.path.join(out, "acceptance.json"), encoding="utf-8") as f:
            acc = json.load(f)
        self.assertEqual(acc["overall"], "PASSED_WITH_RISK")
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
                   "inputs": [{"name": "a", "shape": [16], "dtype": "float32"}], "expected": exp}]}
        evidence = {"op": "Sign", "evidence": [{"case_id": cid, "status": "ok",
                    "precision": {"standard": "ascendoptest_default",
                                  "tolerance_policy_id": "ascendoptest_default:float32",
                                  "policy": policy, "threshold": digest,
                                  "metrics": {"bad_count": 8, "numel": 16}}}]}
        return caseset, evidence

    def _spec(self):
        return {"op": "Sign", "verify_mode": "numerical",
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


if __name__ == "__main__":
    unittest.main()
