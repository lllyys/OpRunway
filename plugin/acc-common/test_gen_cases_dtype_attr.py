"""T7 dtype/attr 扩面单测——覆盖 acceptance 全条目：bf16 位级 codec（tie/±0/subnormal/NaN/inf/字节序）、
materialize/readback round-trip、int/bf16 golden、attr_matrix 计数+golden 用 attrs+equal_nan NaN、语义 id
稳定+唯一、storage_dtype/layout 契约（X_logical vs X_bin 分造）、per-case compare 派生+未支持 fail-fast、
扩面后机器门（覆盖/子集/篡改·codex#3）+ mock 端到端。

跑: cd plugin/acc-common && python3 -m unittest test_gen_cases_dtype_attr -v
⚠ 真机（真 NPU）上 int/bf16 数值校验本轮不做——本文件只证「流水线能造/收发/裁 int/bf16」，非「被验收」。
"""
import json, os, subprocess, sys, tempfile, shutil, unittest
import numpy as np

import gen_cases as GC
import repo_adapter as RA
import validator as V
import precision_policy as P
import validate_acceptance_state as G

_HERE = os.path.dirname(os.path.abspath(__file__))
_SIGN_FX = os.path.join(_HERE, "test_fixtures", "sign_dtype.spec.json")
_ISCLOSE_FX = os.path.join(_HERE, "test_fixtures", "isclose_attr.spec.json")


def _spec(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _wj(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def _rj(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _u32_as_f32(u):
    return np.array([u], dtype=np.uint32).view(np.float32).astype(np.float32)


# ============================================================ bf16 位级 codec ===
class Bf16CodecTest(unittest.TestCase):
    def test_round_half_to_even_down(self):
        # 1.00390625 = 0x3F808000 = bf16 0x3F80(even LSB) 与 0x3F81(odd) 的中点 → 舍向 even 0x3F80
        v = np.array([1.00390625], np.float32)
        self.assertEqual(int(GC._f32_to_bf16_uint16(v)[0]), 0x3F80)

    def test_round_half_to_even_up(self):
        # 0x3F818000 = bf16 0x3F81(odd) 与 0x3F82(even) 的中点 → 舍向 even 0x3F82（向上进位）
        v = _u32_as_f32(0x3F818000)
        self.assertEqual(int(GC._f32_to_bf16_uint16(v)[0]), 0x3F82)

    def test_signed_zero(self):
        self.assertEqual(int(GC._f32_to_bf16_uint16(np.array([0.0], np.float32))[0]), 0x0000)
        self.assertEqual(int(GC._f32_to_bf16_uint16(np.array([-0.0], np.float32))[0]), 0x8000)

    def test_inf_preserved(self):
        self.assertEqual(int(GC._f32_to_bf16_uint16(np.array([np.inf], np.float32))[0]), 0x7F80)
        self.assertEqual(int(GC._f32_to_bf16_uint16(np.array([-np.inf], np.float32))[0]), 0xFF80)

    def test_overflow_rounds_to_inf(self):
        # 0x7F7F8000 = bf16 max(0x7F7F,odd) 与 inf(0x7F80,even) 的中点 → 进位溢为 inf
        self.assertEqual(int(GC._f32_to_bf16_uint16(_u32_as_f32(0x7F7F8000))[0]), 0x7F80)

    def test_nan_stays_quiet_nan_with_sign(self):
        for src, sign in ((np.nan, 0x0000), (-np.nan, 0x8000)):
            bf = int(GC._f32_to_bf16_uint16(np.array([src], np.float32))[0])
            self.assertEqual(bf & 0x7F80, 0x7F80, "exp 须全 1")
            self.assertNotEqual(bf & 0x007F, 0, "尾数须非 0（quiet NaN，防误成 inf）")
            self.assertTrue(np.isnan(GC._bf16_uint16_to_f32(np.array([bf], np.uint16))[0]))

    def test_subnormal_no_crash_finite(self):
        v = np.array([1e-40, -1e-41], np.float32)               # fp32 subnormal（bf16 指数域可容）
        out = GC._bf16_uint16_to_f32(GC._f32_to_bf16_uint16(v))
        self.assertTrue(np.all(np.isfinite(out)))

    def test_encode_idempotent_and_roundtrip_lossless_on_grid(self):
        rng = np.random.default_rng(7)
        v = rng.uniform(-100, 100, size=500).astype(np.float32)
        u = GC._f32_to_bf16_uint16(v)
        grid = GC._bf16_uint16_to_f32(u)                        # 落网格
        # 网格上的值再 encode 无损（低 16 位为 0，round bias 不进位）
        np.testing.assert_array_equal(GC._f32_to_bf16_uint16(grid), u)
        # decode∘encode 幂等
        np.testing.assert_array_equal(GC._bf16_uint16_to_f32(GC._f32_to_bf16_uint16(grid)), grid)

    def test_storage_is_uint16_littleendian_contiguous(self):
        u = GC._f32_to_bf16_uint16(np.array([1.0, -2.0], np.float32))
        self.assertEqual(u.dtype, np.uint16)
        self.assertTrue(u.flags["C_CONTIGUOUS"])                # 落盘前提：contiguous + host LE


# ===================================================== materialize / readback ===
class MaterializeReadbackTest(unittest.TestCase):
    """codex#9：物理收发纯函数 round-trip（证 bf16 uint16 收发正确，非靠 mock）。"""
    def test_roundtrip_each_dtype(self):
        rng = np.random.default_rng(11)
        for dtn in ("float32", "float16", "int16", "int32", "bfloat16"):
            meta = {"dtype": dtn}
            if dtn == "bfloat16":
                logical = GC._bf16_round(rng.uniform(-5, 5, size=24).astype(np.float32))
                storage_np = np.uint16
            elif P.is_integer_dtype(dtn):
                logical = rng.integers(-50, 50, size=24).astype(GC._NATIVE[dtn])
                storage_np = GC._NATIVE[dtn]
            else:
                logical = rng.uniform(-5, 5, size=24).astype(GC._NATIVE[dtn])
                storage_np = GC._NATIVE[dtn]
            phys = RA.materialize_input(logical, meta)
            self.assertEqual(phys.dtype, storage_np, dtn)
            back = RA.readback_output(phys, meta)
            np.testing.assert_array_equal(back, logical, err_msg=f"round-trip {dtn}")

    def test_bf16_phys_not_share_logical(self):
        logical = GC._bf16_round(np.linspace(-3, 3, 16).astype(np.float32))
        phys = RA.materialize_input(logical, {"dtype": "bfloat16"})
        self.assertFalse(np.shares_memory(phys, logical))       # X_bin 与 X_logical 不共内存

    def test_unknown_dtype_rejected(self):
        for fn in (RA.materialize_input, RA.readback_output):
            with self.assertRaises(ValueError):
                fn(np.zeros(3), {"dtype": "complex64"})


# ============================================================ gen_cases dtype ===
class GenCasesDtypeTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cs = GC.gen_cases(_spec(_SIGN_FX), self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _case(self, cid):
        return next(c for c in self.cs["cases"] if c["id"] == cid)

    def test_int_golden_native_and_exact_compare(self):
        for cid in ("sign_int16_16_varied", "sign_int32_16_varied"):
            c = self._case(cid)
            g = np.load(os.path.join(self.d, c["expected"]["golden_path"]))
            self.assertTrue(np.issubdtype(g.dtype, np.integer), cid)  # int golden 原生 int
            self.assertTrue(set(np.unique(g)).issubset({-1, 0, 1}), cid)  # sign∈{-1,0,1}
            self.assertEqual(c["expected"]["compare"], "exact_equal")
            self.assertEqual(c["expected"]["standard"], P.EXACT)

    def test_bf16_golden_fp32_on_grid_and_storage_uint16(self):
        c = self._case("sign_bfloat16_16_varied")
        g = np.load(os.path.join(self.d, c["expected"]["golden_path"]))
        self.assertEqual(g.dtype, np.float32)                   # golden = fp32-on-grid
        self.assertTrue(set(np.unique(g)).issubset({-1.0, 0.0, 1.0}))
        self.assertEqual(c["inputs"][0]["storage_dtype"], "uint16")
        self.assertEqual(c["inputs"][0]["dtype"], "bfloat16")   # 逻辑名保留

    def test_bf16_x_bin_physical_uint16_consistent_with_golden(self):
        """layout 字节契约（acceptance#6）：x{j}.npy 存 uint16 物理位模式；decode 后与 golden 一致（分造但同源）。"""
        c = self._case("sign_bfloat16_4x4_varied")
        x_bin = np.load(os.path.join(self.d, c["inputs"][0]["path"]))
        self.assertEqual(x_bin.dtype, np.uint16)                # 物理是 uint16、**非** fp32
        logical = GC._bf16_uint16_to_f32(x_bin)                 # decode 回逻辑
        g = np.load(os.path.join(self.d, c["expected"]["golden_path"]))
        np.testing.assert_array_equal(np.sign(logical), g)      # golden == sign(decode(X_bin))

    def test_native_dtype_no_storage_field(self):
        for cid in ("sign_float32_16_varied", "sign_int32_16_varied"):
            self.assertNotIn("storage_dtype", self._case(cid)["inputs"][0])  # 向后兼容：native 不带

    def test_case_origin_rule_ref_present(self):
        for c in self.cs["cases"]:
            self.assertTrue(c["expected"].get("case_origin"))
            self.assertTrue(c["expected"].get("rule_ref"))

    def test_unsupported_dtype_fail_fast(self):
        sp = _spec(_SIGN_FX); sp["params"][0]["dtype"] = ["complex64"]
        with self.assertRaises(ValueError):
            GC.gen_cases(sp, tempfile.mkdtemp())


# ================================================= 语义 id 稳定 + 唯一 =========
class SemanticIdTest(unittest.TestCase):
    def test_ids_unique_and_deterministic(self):
        d1, d2 = tempfile.mkdtemp(), tempfile.mkdtemp()
        try:
            ids1 = [c["id"] for c in GC.gen_cases(_spec(_SIGN_FX), d1)["cases"]]
            ids2 = [c["id"] for c in GC.gen_cases(_spec(_SIGN_FX), d2)["cases"]]
            self.assertEqual(len(ids1), len(set(ids1)))          # 唯一
            self.assertEqual(ids1, ids2)                          # 确定性稳定
        finally:
            shutil.rmtree(d1, ignore_errors=True); shutil.rmtree(d2, ignore_errors=True)

    def test_id_stable_across_dtype_expansion(self):
        """扩面（加 dtype）不打乱既有 dtype 的 id（弃索引 id 的核心收益·codex#12）。"""
        two = {"op": "Sign", "verify_mode": "numerical", "params_source": "fixture",
               "params": [{"name": "self", "io": "in", "dtype": ["float32", "float16"]},
                          {"name": "out", "io": "out", "dtype": ["float32", "float16"]}],
               "precision": {"oracle": "ascendoptest", "standard": "ascendoptest_default"},
               "perf": {"baseline": "tbe", "target_ratio": 0.95}}
        d1, d2 = tempfile.mkdtemp(), tempfile.mkdtemp()
        try:
            ids_two = {c["id"] for c in GC.gen_cases(two, d1)["cases"]}
            ids_five = {c["id"] for c in GC.gen_cases(_spec(_SIGN_FX), d2)["cases"]}
            self.assertTrue(ids_two.issubset(ids_five))          # fp32/fp16 id 原样保留
            self.assertIn("sign_float32_16_varied", ids_two)
        finally:
            shutil.rmtree(d1, ignore_errors=True); shutil.rmtree(d2, ignore_errors=True)

    def test_collision_guard_fail_fast(self):
        """finding #13：case_id 碰撞 **fail-fast**（不再静默追加 _2 改名——静默改名会让两条本应区分的
        plan entry 用同一 base 冒充覆盖）。"""
        seen = set()
        a = GC._mk_id("Sign", "float32", (16,), "varied", None, seen)
        self.assertEqual(a, "sign_float32_16_varied")
        with self.assertRaises(ValueError):                      # 二次同 base → 碰撞 raise
            GC._mk_id("Sign", "float32", (16,), "varied", None, seen)


# ============================================================ attr_matrix ======
class AttrMatrixTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.spec = _spec(_ISCLOSE_FX)
        self.cs = GC.gen_cases(self.spec, self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_exactly_len_attr_matrix_cases(self):
        attr_cases = [c for c in self.cs["cases"] if c["expected"]["case_origin"].startswith("attr_matrix")]
        self.assertEqual(len(attr_cases), len(self.spec["attr_matrix"]))  # 恰好 len(attr_matrix)

    def test_golden_uses_case_attrs(self):
        for c in self.cs["cases"]:
            if not c["expected"]["case_origin"].startswith("attr_matrix"):
                continue
            x1 = np.load(os.path.join(self.d, c["inputs"][0]["path"]))
            x2 = np.load(os.path.join(self.d, c["inputs"][1]["path"]))
            g = np.load(os.path.join(self.d, c["expected"]["golden_path"]))
            recomputed = GC.golden_isclose([x1, x2], c["attrs"])
            np.testing.assert_array_equal(recomputed, g)         # golden 用该 case 的 attrs 算

    def test_equal_nan_true_has_nan_data(self):
        found = False
        for c in self.cs["cases"]:
            if c["attrs"].get("equal_nan") is True and "attr矩阵" in c.get("tags", []):
                x1 = np.load(os.path.join(self.d, c["inputs"][0]["path"]))
                x2 = np.load(os.path.join(self.d, c["inputs"][1]["path"]))
                self.assertTrue(np.isnan(x1).any() or np.isnan(x2).any())
                g = np.load(os.path.join(self.d, c["expected"]["golden_path"]))
                self.assertTrue(g.any() and (~g).any())          # 覆盖 True/False
                found = True
        self.assertTrue(found, "attr_matrix 应含 equal_nan=True 的 NaN case")

    def test_no_attr_matrix_backward_compat(self):
        """缺省无 attr_matrix → 与权威 isclose.spec.json 用例数/id 一致（不引入 attr case）。"""
        d1, d2 = tempfile.mkdtemp(), tempfile.mkdtemp()
        try:
            auth = GC.gen_cases(_spec(os.path.join(_HERE, "specs", "isclose.spec.json")), d1)
            self.assertFalse(any(c["expected"]["case_origin"].startswith("attr_matrix")
                                 for c in auth["cases"]))
        finally:
            shutil.rmtree(d1, ignore_errors=True); shutil.rmtree(d2, ignore_errors=True)

    def test_int_near_far_both_hit(self):
        """codex#13：int IsClose near/far 整数网格 → golden 各命中 True/False。"""
        c = next(x for x in self.cs["cases"] if x["id"] == "isclose_int32_16_pairint")
        g = np.load(os.path.join(self.d, c["expected"]["golden_path"]))
        self.assertTrue(g.any() and (~g).any())


# ====================================== per-case compare / effective standard ===
class EffectiveStandardTest(unittest.TestCase):
    def test_int_forces_exact_non_bypassable(self):
        # 数值 spec + int cdtype → EXACT，即便 caseset 误标 compare=rel_err 也强制 EXACT（不可绕过）
        self.assertEqual(P.effective_standard("ascendoptest_default", "int32", "rel_err"), P.EXACT)
        self.assertEqual(P.effective_standard("ascendoptest_default", "int16", None), P.EXACT)

    def test_bf16_needs_exact_equal_else_spec(self):
        self.assertEqual(P.effective_standard("ascendoptest_default", "bfloat16", "exact_equal"), P.EXACT)
        # 误标非 exact_equal → 回落 spec 标准 → 下游 threshold_for(ascendoptest,bfloat16) fail-fast
        self.assertEqual(P.effective_standard("ascendoptest_default", "bfloat16", "rel_err"),
                         "ascendoptest_default")
        with self.assertRaises(ValueError):
            P.threshold_for("ascendoptest_default", "bfloat16")

    def test_fp_unchanged_backward_compat(self):
        self.assertEqual(P.effective_standard("ascendoptest_default", "float32", "rel_err"),
                         "ascendoptest_default")
        self.assertEqual(P.effective_standard("exact", "float32", "rel_err"), "exact")

    def test_gen_cases_bf16_lossy_op_fail_fast(self):
        """bf16 数值 + 输出非精确可表示的 op → gen_cases fail-fast（不假装支持）。"""
        saved = GC._BF16_EXACT_OPS
        GC._BF16_EXACT_OPS = frozenset()                         # 临时清空 → Sign 不再算精确可表示
        try:
            with self.assertRaises(ValueError):
                GC.gen_cases(_spec(_SIGN_FX), tempfile.mkdtemp())
        finally:
            GC._BF16_EXACT_OPS = saved


# ===================================== mock 端到端 + 扩面后机器门（含 codex#3） ==
def _run_pipeline(spec, work_root, defect=None):
    """gen → mock → validate → 三级机器门；返回 (caseset, evidence, verdict, dict(stage->errs))。"""
    work = os.path.join(work_root, "work")
    cs = GC.gen_cases(spec, work)
    _wj(os.path.join(work_root, "caseset.json"), cs)
    ev = RA.run_mock(cs, work, defect_cases=defect)
    _wj(os.path.join(work_root, "evidence.json"), ev)
    vd = V.validate(spec, cs, ev)
    _wj(os.path.join(work_root, "verdict.json"), vd)
    return cs, ev, vd


class MockGateExpandedTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _errs(self, stage):
        errs = []
        G._GATES[stage](self.d, errs)
        return errs

    def test_expanded_one_to_one_and_gates_pass(self):
        cs, ev, vd = _run_pipeline(_spec(_SIGN_FX), self.d)
        self.assertEqual({c["id"] for c in cs["cases"]},
                         {e["case_id"] for e in ev["evidence"]})   # id 一一对应
        self.assertEqual(vd["overall"]["verdict"], "pass")
        self.assertEqual(self._errs("task1"), [])                 # 扩面后门仍 PASSED
        self.assertEqual(self._errs("task2"), [])

    def test_isclose_attr_gates_pass(self):
        cs, ev, vd = _run_pipeline(_spec(_ISCLOSE_FX), self.d)
        self.assertEqual(vd["overall"]["verdict"], "pass")
        self.assertEqual(self._errs("task1"), [])
        self.assertEqual(self._errs("task2"), [])

    def test_defect_on_int_precision_fail_gate_not_blocked(self):
        """codex#3：int case 注 defect → validator FAIL(精度)，但门 task2 仍 PASSED（不被盖成 BLOCKED）。"""
        cs, ev, vd = _run_pipeline(_spec(_SIGN_FX), self.d, defect=["sign_int32_16_varied"])
        self.assertEqual(vd["overall"]["verdict"], "fail")        # 合法精度 fail
        self.assertEqual(self._errs("task2"), [])                 # 门不重判 verdict、不 BLOCK

    def test_defect_on_bf16_precision_fail(self):
        cs, ev, vd = _run_pipeline(_spec(_SIGN_FX), self.d, defect=["sign_bfloat16_16_varied"])
        self.assertEqual(vd["overall"]["verdict"], "fail")

    def test_subset_after_expansion_fails_gate_task1(self):
        """扩面后跑子集（删一条 evidence）→ gate_task1 判 FAILED（防跑子集）。"""
        cs, ev, vd = _run_pipeline(_spec(_SIGN_FX), self.d)
        ev["evidence"] = ev["evidence"][:-1]                      # 丢最后一条（int/bf16 类）
        _wj(os.path.join(self.d, "evidence.json"), ev)
        self.assertTrue(any("跑子集" in e for e in self._errs("task1")))
        self.assertTrue(any("跑子集" in e for e in self._errs("task2")))

    def test_tamper_threshold_fails_gate_task2(self):
        cs, ev, vd = _run_pipeline(_spec(_SIGN_FX), self.d)
        ev["evidence"][0]["precision"]["threshold"] = 0.5         # 偷偷放宽 digest
        _wj(os.path.join(self.d, "evidence.json"), ev)
        self.assertTrue(any("防放宽" in e and "threshold" in e for e in self._errs("task2")))

    def test_tamper_scope_fails_gate_task3(self):
        _run_pipeline(_spec(_SIGN_FX), self.d)
        pr = {"op": "Sign", "per_case": [{"case_id": "sign_float32_1024x1024_perf",
              "scope": "e2e", "blocked": False, "达标": True}],  # 混入 e2e 墙钟
              "summary": {"status": "ok", "perf_cases": 1, "达标": 1, "blocked": 0}}
        _wj(os.path.join(self.d, "perf_report.json"), pr)
        self.assertTrue(any("kernel_only" in e for e in self._errs("task3")))


# ============================================ 坏/storage 不符 dtype 被拒（run_mock）
class DtypeRejectTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_storage_dtype_mismatch_rejected_by_mock(self):
        """caseset 声明 storage_dtype=uint16（bf16）但 npy 落成 float32 → run_mock 拒（防契约漂移）。"""
        work = os.path.join(self.d, "work")
        cid = "sign_bfloat16_16_varied"
        os.makedirs(os.path.join(work, cid))
        np.save(os.path.join(work, cid, "x1.npy"), np.ones(16, np.float32))  # 错：应 uint16
        np.save(os.path.join(work, cid, "golden.npy"), np.ones(16, np.float32))
        cs = {"op": "Sign", "cases": [{"id": cid, "dims": ["功能"],
              "inputs": [{"name": "self", "shape": [16], "dtype": "bfloat16",
                          "storage_dtype": "uint16", "path": f"{cid}/x1.npy"}],
              "attrs": {}, "expected": {"golden_path": f"{cid}/golden.npy", "verify_mode": "numerical",
                        "standard": "exact", "compare": "exact_equal", "compare_dtype": "bfloat16",
                        "tolerance_policy_id": "exact",
                        "policy": {"kind": "exact", "max_mismatch": 0, "not_settled": False},
                        "threshold": 0}}]}
        with self.assertRaises(ValueError):
            RA.run_mock(cs, work)


# ===================================================== 子进程端到端（退出码） ====
class SubprocessE2ETest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _run(self, spec_path, *extra):
        return subprocess.run(
            [sys.executable, os.path.join(_HERE, "run_workflow.py"), spec_path,
             "--mode", "mock", "--out", self.d, *extra], capture_output=True, text=True)

    def test_fixture_clean_exit0(self):
        for fx in (_SIGN_FX, _ISCLOSE_FX):
            r = self._run(fx)
            self.assertEqual(r.returncode, 0, fx + "\n" + r.stdout + r.stderr)

    def test_fixture_int_defect_exit1_gate_passed(self):
        r = self._run(_SIGN_FX, "--defect", "sign_int32_16_varied")
        self.assertEqual(r.returncode, 1)
        acc = _rj(os.path.join(self.d, "acceptance.json"))
        self.assertEqual(acc["state"], "FAILED_PRECISION")
        self.assertTrue(acc["gate"]["passed"])                    # 门 PASSED（合法 fail 不被门盖·codex#3）

    def test_validator_stays_stdlib_only(self):
        code = ("import sys, validator; "
                "assert 'numpy' not in sys.modules, 'validator 拉入了 numpy'; print('OK')")
        r = subprocess.run([sys.executable, "-c", code], cwd=_HERE, capture_output=True, text=True)
        self.assertIn("OK", r.stdout, r.stderr)


# ============================================ 对抗式负例（gen_cases / repo_adapter）===
def _isclose_bf16_nan_spec():
    return {"op": "IsClose", "verify_mode": "exact",
            "params": [{"name": "self", "io": "in", "dtype": ["bfloat16", "float32"]},
                       {"name": "other", "io": "in", "dtype": ["bfloat16", "float32"]},
                       {"name": "rtol", "io": "attr", "dtype": ["double"], "default": 1e-05},
                       {"name": "atol", "io": "attr", "dtype": ["double"], "default": 1e-08},
                       {"name": "equal_nan", "io": "attr", "dtype": ["bool"], "default": False},
                       {"name": "out", "io": "out", "dtype": ["bool"]}],
            "precision": {"standard": "exact"},
            "attr_matrix": [{"equal_nan": True}, {"equal_nan": False}]}


class GenCasesSecurityNegativeTest(unittest.TestCase):
    """gen_cases 对抗式负例——每条对应一个已实跑复现的 exploit。"""

    def test_bf16_equal_nan_effective_not_fake_coverage(self):
        """finding #10：bf16 dtype0 + attr_matrix equal_nan **不再假覆盖**——走 nanpair(含 aligned-NaN)、
        两版 golden 有别；旧洞里 bf16 被排除出 nanpair → pairfar 无 NaN → 两版 golden 相等 → 假覆盖。"""
        d = tempfile.mkdtemp()
        try:
            cs = GC.gen_cases(_isclose_bf16_nan_spec(), d)      # 生成不报错即证 _assert_equal_nan_effective 通过
            attr_cases = [c for c in cs["cases"] if "attr矩阵" in c.get("tags", [])]
            self.assertTrue(attr_cases)
            for c in attr_cases:                                # 全走 nanpair（不是 pairfar）
                self.assertIn("nanpair", c["id"])
            c0 = attr_cases[0]
            a = GC._bf16_uint16_to_f32(np.load(os.path.join(d, c0["inputs"][0]["path"])))
            b = GC._bf16_uint16_to_f32(np.load(os.path.join(d, c0["inputs"][1]["path"])))
            self.assertTrue((np.isnan(a) & np.isnan(b)).any())  # 输入确含 aligned-NaN
            g_t = np.isclose(a, b, rtol=1e-5, atol=1e-8, equal_nan=True)
            g_f = np.isclose(a, b, rtol=1e-5, atol=1e-8, equal_nan=False)
            self.assertFalse(np.array_equal(g_t, g_f))          # equal_nan 翻转后 golden 有别（真覆盖）
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_attr_matrix_unknown_key_fail_fast(self):
        """finding #12：attr_matrix variant 含 spec 未声明 attr key（{foo:...}）→ fail-fast（防伪造覆盖）。"""
        sp = _isclose_bf16_nan_spec()
        sp["attr_matrix"] = [{"foo": 12345, "rtol": 1e-3}]
        with self.assertRaises(ValueError):
            GC.gen_cases(sp, tempfile.mkdtemp())

    def test_duplicate_dtype_fail_fast(self):
        """finding #13：spec dtype 集含重复项 → fail-fast（防 case_id 碰撞/静默改名冒充覆盖）。"""
        sp = _isclose_bf16_nan_spec(); del sp["attr_matrix"]
        sp["params"][0]["dtype"] = ["float32", "float32"]
        sp["params"][1]["dtype"] = ["float32", "float32"]
        with self.assertRaises(ValueError):
            GC.gen_cases(sp, tempfile.mkdtemp())

    def test_bf16_lossy_op_via_verify_mode_exact_fail_fast(self):
        """finding #14：lossy bf16 op（输出非 bool、不在 _BF16_EXACT_OPS）借 verify_mode=exact 想绕白名单
        → 白名单与「输出是否 bool」拆成两道独立校验 → fail-fast（不因 verify_mode=exact 短路豁免）。"""
        GC.GOLDEN["FakeLossyBf"] = ("fake", lambda inputs, attrs: np.negative(inputs[0]))
        try:
            sp = {"op": "FakeLossyBf", "verify_mode": "exact",
                  "params": [{"name": "self", "io": "in", "dtype": ["bfloat16"]},
                             {"name": "out", "io": "out", "dtype": ["bfloat16"]}],
                  "precision": {"standard": "exact"}}
            with self.assertRaises(ValueError):
                GC.gen_cases(sp, tempfile.mkdtemp())
        finally:
            GC.GOLDEN.pop("FakeLossyBf", None)

    def test_bool_golden_no_boundary_fail_fast_even_under_O(self):
        """finding #11：golden 未覆盖 True/False 的 exact bool op → 用 raise（非裸 assert）→ **python -O 下也拦**
        （裸 assert 会被 -O 剥离，静默产坏 caseset）。"""
        code = (
            "import sys, tempfile; sys.path.insert(0, %r);\n"
            "import numpy as np, gen_cases as gc;\n"
            "gc.GOLDEN['AllTrueB'] = ('fake', lambda i, a: np.ones_like(i[0], dtype=bool));\n"
            "sp = {'op':'AllTrueB','verify_mode':'exact','params':["
            "{'name':'self','io':'in','dtype':['float32']},"
            "{'name':'other','io':'in','dtype':['float32']},"
            "{'name':'out','io':'out','dtype':['bool']}],'precision':{'standard':'exact'}};\n"
            "\ntry:\n gc.gen_cases(sp, tempfile.mkdtemp()); print('NOT_BLOCKED')\n"
            "except ValueError: print('BLOCKED')\n" % _HERE)
        r = subprocess.run([sys.executable, "-O", "-c", code], capture_output=True, text=True)
        self.assertIn("BLOCKED", r.stdout, r.stdout + r.stderr)


class RepoAdapterSecurityNegativeTest(unittest.TestCase):
    """repo_adapter 对抗式负例——storage 伪造 / 值 cast 污染。"""

    def test_materialize_native_value_cast_rejected(self):
        """finding #9：materialize_input(uint16数组, dtype=float32) 旧洞会值 cast(100→100.0)污染字节 → 现拒。"""
        u = np.array([100, 200, 65535], dtype=np.uint16)
        with self.assertRaises(ValueError):
            RA.materialize_input(u, {"dtype": "float32"})

    def test_readback_native_value_cast_rejected(self):
        with self.assertRaises(ValueError):
            RA.readback_output(np.array([1, 2], dtype=np.uint16), {"dtype": "float32"})

    def test_run_mock_forged_storage_dtype_rejected(self):
        """finding #7：逻辑 dtype=bfloat16 但自声明 storage_dtype=float32（≠ 反推 uint16）→ run_mock 拒。"""
        d = tempfile.mkdtemp()
        try:
            work = os.path.join(d, "work"); cid = "sign_bfloat16_16_varied"
            os.makedirs(os.path.join(work, cid))
            np.save(os.path.join(work, cid, "x1.npy"), np.ones(16, np.float32))
            np.save(os.path.join(work, cid, "golden.npy"), np.ones(16, np.float32))
            cs = {"op": "Sign", "cases": [{"id": cid, "dims": ["功能"],
                  "inputs": [{"name": "self", "shape": [16], "dtype": "bfloat16",
                              "storage_dtype": "float32", "path": f"{cid}/x1.npy"}],
                  "attrs": {}, "expected": {"golden_path": f"{cid}/golden.npy",
                            "verify_mode": "numerical", "standard": "exact"}}]}
            with self.assertRaises(ValueError):
                RA.run_mock(cs, work)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_run_new_example_forged_storage_rejected_before_remote(self):
        """finding #6：逻辑 dtype=float32 但自声明 storage_dtype=uint16 + npy 落 uint16 → run_new_example
        在**任何远端调用之前**拒（旧洞：过校验后 materialize 值 cast 污染送真机的 x{j}.bin）。"""
        d = tempfile.mkdtemp()
        try:
            cid = "C1"; os.makedirs(os.path.join(d, cid))
            np.save(os.path.join(d, cid, "x1.npy"), np.array([100, 200, 300, 400], np.uint16))
            np.save(os.path.join(d, cid, "golden.npy"), np.ones(4, np.float32))
            cs = {"op": "Sign", "attr_order": [],
                  "cases": [{"id": cid, "dims": ["功能"], "attrs": {},
                             "inputs": [{"name": "self", "path": f"{cid}/x1.npy",
                                         "dtype": "float32", "storage_dtype": "uint16", "shape": [4]}],
                             "expected": {"golden_path": f"{cid}/golden.npy", "verify_mode": "numerical"}}]}
            import subprocess as _sp
            orig = _sp.run
            _sp.run = lambda *a, **k: (_ for _ in ()).throw(AssertionError("reached remote"))
            try:
                with self.assertRaises(ValueError):    # storage 伪造在输入校验期即拒，未到远端
                    RA.run_new_example(cs, d)
                self.assertFalse(os.path.exists(os.path.join(d, cid, "x1.bin")))  # 未写污染 bin
            finally:
                _sp.run = orig
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
