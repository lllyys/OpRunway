"""T7 dtype/attr 扩面单测——覆盖 acceptance 全条目：bf16 位级 codec（tie/±0/subnormal/NaN/inf/字节序）、
materialize/readback round-trip、int/bf16 golden、attr_matrix 计数+golden 用 attrs+equal_nan NaN、语义 id
稳定+唯一、storage_dtype/layout 契约（X_logical vs X_bin 分造）、per-case compare 派生+未支持 fail-fast、
扩面后机器门（覆盖/子集/篡改·codex#3）+ mock 端到端。

跑: cd plugin/acc-common && python3 -m unittest test_gen_cases_dtype_attr -v
⚠ 真机（真 NPU）上 int/bf16 数值校验本轮不做——本文件只证「流水线能造/收发/裁 int/bf16」，非「被验收」。
"""
import json, os, subprocess, sys, tempfile, shutil, unittest
from unittest import mock
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


def _find_case(cs, *, dtype=None, dim="精度", regular=True, exclude_na=True):
    """§1 覆盖-预算重写后 case id 变——按属性找 case（不硬编码 id）。
    regular=True → 只取常规网格/白名单（tags 含「常规」或「白名单」），排除 §1.4 特殊场景
    （inf/nan/empty——那些 torch 与 np 参考在边界值上可不同、且空 case 无 golden）。"""
    for c in cs["cases"]:
        if dtype is not None and c["inputs"][0]["dtype"] != dtype:
            continue
        if dim is not None and dim not in c.get("dims", []):
            continue
        if exclude_na and c.get("expected", {}).get("compare") == "na":
            continue
        if regular and not (set(c.get("tags", [])) & {"常规", "白名单"}):
            continue
        return c
    raise AssertionError(f"未找到 case: dtype={dtype} dim={dim} regular={regular}")


def _symlink_supported():
    """本平台能否真建软链（无开发者模式的 Windows 不能）——不能则跳过软链用例，不假绿。"""
    if not hasattr(os, "symlink"):
        return False
    try:
        with tempfile.TemporaryDirectory() as d:
            os.symlink(os.path.join(d, "target"), os.path.join(d, "link"))
        return True
    except (OSError, NotImplementedError, AttributeError):
        return False


# ===== golden 去引擎化（ADR 0011）：elementwise 通路不含内置 golden 值、按算子从 <ops_root>/<op>/golden.py 加载。 =====
# 共享 fixture 建临时 ops_root（拷 samples/golden 的 4 算子）+ 设 OPRUNWAY_OPS_DIR，令本模块 gen_cases 调用能
# 加载到 golden（缺则 fail-closed）；子进程继承 os.environ。假算子测试用 _place_golden(_GOLDEN_ROOT, op, body) 另落。
import _golden_fixture as _gf
_place_golden = _gf.place_golden
_GOLDEN_ROOT = None


def setUpModule():
    global _GOLDEN_ROOT
    _GOLDEN_ROOT = _gf.install()


def tearDownModule():
    _gf.uninstall()


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
        for dtype in ("int16", "int32"):
            c = _find_case(self.cs, dtype=dtype)               # §1 后按 dtype 找（非硬编码 id）
            g = np.load(os.path.join(self.d, c["expected"]["golden_path"]))
            self.assertTrue(np.issubdtype(g.dtype, np.integer), dtype)  # int golden 原生 int
            self.assertTrue(set(np.unique(g)).issubset({-1, 0, 1}), dtype)  # sign∈{-1,0,1}
            self.assertEqual(c["expected"]["compare"], "exact_equal")
            self.assertEqual(c["expected"]["standard"], P.EXACT)

    def test_bf16_golden_fp32_on_grid_and_storage_uint16(self):
        c = _find_case(self.cs, dtype="bfloat16")
        g = np.load(os.path.join(self.d, c["expected"]["golden_path"]))
        self.assertEqual(g.dtype, np.float32)                   # golden = fp32-on-grid
        self.assertTrue(set(np.unique(g)).issubset({-1.0, 0.0, 1.0}))
        self.assertEqual(c["inputs"][0]["storage_dtype"], "uint16")
        self.assertEqual(c["inputs"][0]["dtype"], "bfloat16")   # 逻辑名保留

    def test_bf16_x_bin_physical_uint16_consistent_with_golden(self):
        """layout 字节契约（acceptance#6）：x{j}.npy 存 uint16 物理位模式；decode 后与 golden 一致（分造但同源）。"""
        c = _find_case(self.cs, dtype="bfloat16")               # 常规 case：有限值，torch.sign==np.sign
        x_bin = np.load(os.path.join(self.d, c["inputs"][0]["path"]))
        self.assertEqual(x_bin.dtype, np.uint16)                # 物理是 uint16、**非** fp32
        logical = GC._bf16_uint16_to_f32(x_bin)                 # decode 回逻辑
        g = np.load(os.path.join(self.d, c["expected"]["golden_path"]))
        np.testing.assert_array_equal(np.sign(logical), g)      # golden == sign(decode(X_bin))

    def test_native_dtype_no_storage_field(self):
        for dtype in ("float32", "int32"):
            self.assertNotIn("storage_dtype", _find_case(self.cs, dtype=dtype)["inputs"][0])  # native 不带

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

    def test_data_stable_per_id_across_dtype_expansion(self):
        """§1 per-case 独立种子（评审 #7）：同 case_id 在不同 dtype 集下**数据字节一致**（数据只依赖稳定 id、
        与 dtype 集/target/采样解耦）。注：§1 覆盖-预算下**哪些 id 被 emit** 随预算采样变，故不再断言「id 集
        ⊆ 扩面后集」；新不变式是「同 id → 同数据」。"""
        two = {"op": "Sign", "verify_mode": "numerical", "params_source": "fixture",
               "params": [{"name": "self", "io": "in", "dtype": ["float32", "float16"]},
                          {"name": "out", "io": "out", "dtype": ["float32", "float16"]}],
               "precision": {"oracle": "ascendoptest", "standard": "ascendoptest_default"},
               "perf": {"baseline": "tbe", "target_ratio": 0.95}}
        d1, d2 = tempfile.mkdtemp(), tempfile.mkdtemp()
        try:
            c2 = {c["id"]: c for c in GC.gen_cases(two, d1)["cases"]}
            c5 = {c["id"]: c for c in GC.gen_cases(_spec(_SIGN_FX), d2)["cases"]}
            common = set(c2) & set(c5)
            self.assertTrue(common, "两次生成应有共同 case_id（forced 特殊/白名单稳定）")
            for cid in sorted(common):
                x2 = np.load(os.path.join(d1, c2[cid]["inputs"][0]["path"]))
                x5 = np.load(os.path.join(d2, c5[cid]["inputs"][0]["path"]))
                np.testing.assert_array_equal(x2, x5, err_msg=f"{cid} 数据应稳定（per-case 种子）")
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

    def test_attr_cartesian_covers_equal_nan(self):
        """§1.3 attr 作正交轴（笛卡尔展开，取值集从 attr_matrix 派生）——equal_nan T/F 都出现在用例里。
        取代旧「恰好 len(attr_matrix) 条单代表点 case」（attr 不再坍缩到单点）。"""
        vals = {c["attrs"].get("equal_nan") for c in self.cs["cases"] if "equal_nan" in c["attrs"]}
        self.assertIn(True, vals)
        self.assertIn(False, vals)

    def test_golden_uses_case_attrs(self):
        isclose_fn, _, _ = GC.load_golden("IsClose")             # golden 现按算子加载（elementwise 通路不内置）
        for c in self.cs["cases"]:
            if not c["expected"]["case_origin"].startswith("attr_matrix"):
                continue
            x1 = np.load(os.path.join(self.d, c["inputs"][0]["path"]))
            x2 = np.load(os.path.join(self.d, c["inputs"][1]["path"]))
            g = np.load(os.path.join(self.d, c["expected"]["golden_path"]))
            recomputed = isclose_fn([x1, x2], c["attrs"])
            np.testing.assert_array_equal(recomputed, g)         # golden 用该 case 的 attrs 算

    def test_nan_special_and_equal_nan_covered(self):
        """§1.4 NaN 特殊场景 + §1.3 equal_nan 覆盖（两条**独立**覆盖）。
        ⚠ 偏离（见报告）：§1 不再把 equal_nan=True 与 aligned-NaN 数据**交叉**在同一 case（旧 nanpair 行为）——
        equal_nan 由 attr 笛卡尔覆盖、NaN 由 §1.4 特殊场景覆盖，二者不再强制交叉。"""
        en = {c["attrs"].get("equal_nan") for c in self.cs["cases"] if "equal_nan" in c["attrs"]}
        self.assertTrue({True, False}.issubset(en), "equal_nan T/F 应都覆盖")
        nan_cases = [c for c in self.cs["cases"]
                     if not P.is_integer_dtype(c["inputs"][0]["dtype"])
                     and c["expected"].get("compare") != "na"
                     and "nan" in c["id"].split("_")]
        self.assertTrue(nan_cases, "应有 §1.4 NaN 特殊场景用例")
        x1 = np.load(os.path.join(self.d, nan_cases[0]["inputs"][0]["path"]))
        self.assertTrue(np.isnan(x1).any(), "NaN 特殊场景输入应含 NaN")

    def test_no_attr_matrix_backward_compat(self):
        """缺省无 attr_matrix → 与权威 isclose.spec.json 用例数/id 一致（不引入 attr case）。"""
        d1, d2 = tempfile.mkdtemp(), tempfile.mkdtemp()
        try:
            auth = GC.gen_cases(_spec(os.path.join(_HERE, "..", "..", "samples", "specs", "isclose.spec.json")), d1)
            self.assertFalse(any(c["expected"]["case_origin"].startswith("attr_matrix")
                                 for c in auth["cases"]))
        finally:
            shutil.rmtree(d1, ignore_errors=True); shutil.rmtree(d2, ignore_errors=True)

    def test_int_near_far_both_hit(self):
        """codex#13：int IsClose 整数网格 → golden 各命中 True/False（exact bool 边界覆盖）。"""
        c = _find_case(self.cs, dtype="int32", dim="精度")      # §1 后按 dtype 找（非硬编码 id）
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
        did = _find_case(GC.gen_cases(_spec(_SIGN_FX), tempfile.mkdtemp()), dtype="int32")["id"]
        cs, ev, vd = _run_pipeline(_spec(_SIGN_FX), self.d, defect=[did])
        self.assertEqual(vd["overall"]["verdict"], "fail")        # 合法精度 fail
        self.assertEqual(self._errs("task2"), [])                 # 门不重判 verdict、不 BLOCK

    def test_defect_on_bf16_precision_fail(self):
        did = _find_case(GC.gen_cases(_spec(_SIGN_FX), tempfile.mkdtemp()), dtype="bfloat16")["id"]
        cs, ev, vd = _run_pipeline(_spec(_SIGN_FX), self.d, defect=[did])
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
        # 篡改一条**非 na**（有 threshold）精度证据（§1 后 evidence[0] 可能是空 Tensor na 用例、无 threshold）
        idx = next(i for i, e in enumerate(ev["evidence"])
                   if isinstance(e.get("precision"), dict) and not e["precision"].get("na")
                   and "threshold" in e["precision"])
        ev["evidence"][idx]["precision"]["threshold"] = 0.5      # 偷偷放宽 digest
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
        did = _find_case(GC.gen_cases(_spec(_SIGN_FX), tempfile.mkdtemp()), dtype="int32")["id"]
        r = self._run(_SIGN_FX, "--defect", did)
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

    def test_bf16_equal_nan_covered_cartesian(self):
        """§1.3：bf16 IsClose 的 equal_nan 由 attr 笛卡尔覆盖（T/F 都出现）。
        ⚠ 偏离（见报告）：§1 不再走 nanpair 把 equal_nan=True 与 aligned-NaN **交叉**证「真起作用」（旧 finding #10）——
        equal_nan 结构性覆盖 + NaN §1.4 特殊场景分别覆盖，`_assert_equal_nan_effective` 不再触发（新 §1 不产 nanpair）。"""
        d = tempfile.mkdtemp()
        try:
            cs = GC.gen_cases(_isclose_bf16_nan_spec(), d)
            en = {c["attrs"].get("equal_nan") for c in cs["cases"] if "equal_nan" in c["attrs"]}
            self.assertTrue({True, False}.issubset(en), "equal_nan T/F 应都覆盖")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_attr_matrix_unknown_key_fail_fast(self):
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
        _place_golden(_GOLDEN_ROOT, "FakeLossyBf",
                      "def golden_fn(inputs, attrs):\n    return np.negative(inputs[0])\n")
        try:
            sp = {"op": "FakeLossyBf", "verify_mode": "exact",
                  "params": [{"name": "self", "io": "in", "dtype": ["bfloat16"]},
                             {"name": "out", "io": "out", "dtype": ["bfloat16"]}],
                  "precision": {"standard": "exact"}}
            with self.assertRaises(ValueError):
                GC.gen_cases(sp, tempfile.mkdtemp())
        finally:
            shutil.rmtree(os.path.join(_GOLDEN_ROOT, "FakeLossyBf"), ignore_errors=True)

    def test_bool_golden_no_boundary_fail_fast_even_under_O(self):
        """finding #11：golden 未覆盖 True/False 的 exact bool op → 用 raise（非裸 assert）→ **python -O 下也拦**
        （裸 assert 会被 -O 剥离，静默产坏 caseset）。"""
        ops = os.path.realpath(tempfile.mkdtemp())
        _place_golden(ops, "AllTrueB",
                      "def golden_fn(inputs, attrs):\n    return np.ones_like(inputs[0], dtype=bool)\n")
        try:
            code = (
                "import sys, tempfile; sys.path.insert(0, %r);\n"
                "import gen_cases as gc;\n"
                "sp = {'op':'AllTrueB','verify_mode':'exact','params':["
                "{'name':'self','io':'in','dtype':['float32']},"
                "{'name':'other','io':'in','dtype':['float32']},"
                "{'name':'out','io':'out','dtype':['bool']}],'precision':{'standard':'exact'}};\n"
                "\ntry:\n gc.gen_cases(sp, tempfile.mkdtemp()); print('NOT_BLOCKED')\n"
                "except ValueError: print('BLOCKED')\n" % _HERE)
            env = dict(os.environ, OPRUNWAY_OPS_DIR=ops)     # 子进程从落点加载 AllTrueB golden.py
            r = subprocess.run([sys.executable, "-O", "-c", code],
                               capture_output=True, text=True, env=env)
            self.assertIn("BLOCKED", r.stdout, r.stdout + r.stderr)
        finally:
            shutil.rmtree(ops, ignore_errors=True)


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
        在**任何远端调用之前**拒（旧洞：过校验后 materialize 值 cast 污染送真机的 x{j}.bin）。

        ⚠ 回归门要点（Med#1）：base 分支去私有默认后 `_ne_cfg()` 是 run_new_example 第一行，缺 env 即抛
        「缺 OPRUNWAY_*」——若本测试不设 env，ValueError 会钉在缺 env、**早于** storage 校验，防线假绿。
        故此处灌一份齐全的 local env（且 rroot/ops/opp 与 work_dir 不相交，避开新加的 _contains 守卫），
        让 _ne_cfg 放行、真正走到 storage 伪造校验，再用 assertRaisesRegex 把 ValueError 钉在 storage 路径
        （消息含 "storage"/"反推"）——env 缺失消息不含这些词，一旦 env 灌注回归就会红。"""
        d = tempfile.mkdtemp()      # work_dir：放 caseset 输入（x1.npy/golden.npy）
        e = tempfile.mkdtemp()      # env scratch：rroot/ops/opp/work_root，须与 d 不相交（避开 _contains 守卫）
        try:
            cid = "C1"; os.makedirs(os.path.join(d, cid))
            np.save(os.path.join(d, cid, "x1.npy"), np.array([100, 200, 300, 400], np.uint16))
            np.save(os.path.join(d, cid, "golden.npy"), np.ones(4, np.float32))
            cs = {"op": "Sign", "attr_order": [],
                  "cases": [{"id": cid, "dims": ["功能"], "attrs": {},
                             "inputs": [{"name": "self", "path": f"{cid}/x1.npy",
                                         "dtype": "float32", "storage_dtype": "uint16", "shape": [4]}],
                             "expected": {"golden_path": f"{cid}/golden.npy", "verify_mode": "numerical"}}]}
            env = {"OPRUNWAY_TARGET": "local",                       # 本机模式：_ne_cfg 不要求 SSH_HOST
                   "OPRUNWAY_REMOTE_DIR": os.path.join(e, "rroot"),  # 与 d 不相交 → 过 _contains 守卫
                   "OPRUNWAY_OPS_REPO": os.path.join(e, "ops"),
                   "OPRUNWAY_OPP": os.path.join(e, "opp"),
                   "OPRUNWAY_OP_SRC": "experimental/math/is_close",  # provenance：op 源子路径（必填），让 _ne_cfg 放行、走到 storage 校验
                   "OPRUNWAY_WORK_DIR": e}   # user_root→e，令 ops_root 落 e/.oprunway（非插件目录内）
            # fallback 已退役（2026-07-20）：放一份 user Sign runner，让 find_runner 命中 user、走到 storage 伪造校验
            # （否则 find_runner 先抛「缺 runner」、regex 不匹配、防线假红）
            _sopd = os.path.join(e, ".oprunway", "ops", "Sign"); os.makedirs(_sopd, exist_ok=True)
            with open(os.path.join(_sopd, "oprunway_sign_runner.cpp"), "w", encoding="utf-8") as _rf:
                _rf.write("// stub user runner\n")
            import subprocess as _sp
            orig = _sp.run
            _sp.run = lambda *a, **k: (_ for _ in ()).throw(AssertionError("reached remote"))
            try:
                with mock.patch.dict(os.environ, env, clear=True):
                    # ValueError 须来自 storage 伪造校验（含 "storage"/"反推"），而非缺 env（"缺 OPRUNWAY_*"）
                    with self.assertRaisesRegex(ValueError, "storage|反推"):
                        RA.run_new_example(cs, d)
                self.assertFalse(os.path.exists(os.path.join(d, cid, "x1.bin")))  # 未写污染 bin
            finally:
                _sp.run = orig
        finally:
            shutil.rmtree(d, ignore_errors=True)
            shutil.rmtree(e, ignore_errors=True)


class GoldenTorchPreferredTest(unittest.TestCase):
    """Q9-Part A：golden 固定用 torch(CPU) 单后端（确定性、**不回退 numpy**）。

    torch 缺失 → golden fail-closed 报错；故这些测试需要 torch（在装了 torch 的机器上跑，验收本就在 NPU 机）。
    无 torch 环境 → skip（不 mask，明示需 torch）。"""

    def _need_torch(self):
        try:
            import torch  # noqa: F401
        except Exception:
            self.skipTest("无 torch → golden fail-closed；本测试需在装了 torch 的机器上跑（精度验收在 NPU 机）")

    def test_golden_source_label_is_torch(self):
        # 加载器只读元数据、不跑 golden_fn，故不需 torch。4 算子 golden.py 的 GOLDEN_SOURCE 恒 "torch ..."。
        for op in ("IsClose", "Sign", "Equal", "Neg"):
            _fn, gsrc, _prov = GC.load_golden(op)
            self.assertTrue(gsrc.startswith("torch "), gsrc)

    def test_caseset_records_torch_source(self):
        """caseset.expected.golden_source 恒 "torch ..."、映到 torch_ref。"""
        self._need_torch()
        sp = {"op": "Sign", "verify_mode": "numerical",
              "params": [{"name": "self", "io": "in", "dtype": ["float32"]},
                         {"name": "out", "io": "out", "dtype": ["float32"]}],
              "precision": {"oracle": "ascendoptest", "standard": "ascendoptest_default"}}
        d = tempfile.mkdtemp()
        try:
            cs = GC.gen_cases(sp, d)
            for c in cs["cases"]:
                gsrc = c["expected"]["golden_source"]
                self.assertTrue(gsrc.startswith("torch "), gsrc)
                self.assertEqual(P.oracle_source_from_golden(gsrc), "torch_ref")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_torch_golden_values(self):
        """golden(torch) 在无边界值(NaN/Inf)的随机输入上与 numpy 参考逐值相同（精确逐元素算子）；
        边界(如 sign(NaN))torch 与 numpy 有意不同——torch 是选定的确定性后端、不与 numpy 比。
        另核 rtol/atol 非法 → fail-closed。golden 现按算子从 samples/golden 加载（经 load_golden）。"""
        self._need_torch()
        sign_fn, _, _ = GC.load_golden("Sign")
        neg_fn, _, _ = GC.load_golden("Neg")
        eq_fn, _, _ = GC.load_golden("Equal")
        isclose_fn, _, _ = GC.load_golden("IsClose")
        rng = np.random.default_rng(0)
        for dt in (np.float32, np.float16):
            a = rng.uniform(-5, 5, size=(4, 4)).astype(dt)
            b = rng.uniform(-5, 5, size=(4, 4)).astype(dt)
            np.testing.assert_array_equal(sign_fn([a], {}), np.sign(a))
            np.testing.assert_array_equal(neg_fn([a], {}), np.negative(a))
            np.testing.assert_array_equal(eq_fn([a, b], {}), np.equal(a, b))
            np.testing.assert_array_equal(
                isclose_fn([a, b], {"rtol": 1e-5, "atol": 1e-8, "equal_nan": False}),
                np.isclose(a, b, rtol=1e-5, atol=1e-8, equal_nan=False))
        with self.assertRaises(ValueError):                          # 负容差 fail-closed
            isclose_fn([a, b], {"rtol": -1e-5, "atol": 1e-8, "equal_nan": False})


class LoadGoldenTest(unittest.TestCase):
    """golden 加载器（ADR 0011 决策 1/6）：只查 <ops_root>/<op>/golden.py、缺则 fail-closed 不回退、
    **软链分两层拒**（最终文件 islink + 目录段逐段，见 repo_adapter._reject_symlink_segments）、缺元数据拒。"""

    def _ops(self, d):
        return mock.patch.dict(os.environ, {"OPRUNWAY_OPS_DIR": os.path.realpath(d)})

    def test_missing_golden_fail_closed(self):
        with tempfile.TemporaryDirectory() as d, self._ops(d):
            with self.assertRaises(ValueError) as cm:
                GC.load_golden("NoSuchOp")
            self.assertIn("不回退", str(cm.exception))              # 引擎不回退内置/样例

    def test_missing_metadata_rejected(self):
        with tempfile.TemporaryDirectory() as d, self._ops(d):
            opd = os.path.join(os.path.realpath(d), "BadOp"); os.makedirs(opd)
            with open(os.path.join(opd, "golden.py"), "w", encoding="utf-8") as f:
                f.write("def golden_fn(inputs, attrs):\n    return inputs[0]\n")   # 缺 GOLDEN_SOURCE/PROVENANCE
            with self.assertRaises(ValueError):
                GC.load_golden("BadOp")

    def test_symlink_golden_rejected(self):
        with tempfile.TemporaryDirectory() as d, self._ops(d):
            real = os.path.join(os.path.realpath(d), "elsewhere.py")
            with open(real, "w", encoding="utf-8") as f:
                f.write("GOLDEN_SOURCE='x'\nGOLDEN_PROVENANCE='x'\ndef golden_fn(i, a):\n    return i[0]\n")
            opd = os.path.join(os.path.realpath(d), "LinkOp"); os.makedirs(opd)
            os.symlink(real, os.path.join(opd, "golden.py"))
            with self.assertRaises(ValueError) as cm:
                GC.load_golden("LinkOp")
            self.assertIn("符号链接", str(cm.exception))

    @unittest.skipUnless(_symlink_supported(), "本平台不支持创建符号链接")
    def test_symlinked_op_directory_rejected(self):
        """**目录段**软链（旧洞）：golden.py 本身是真文件（最终组件 islink=False、旧检查放行），
        但 `<ops_root>/<op>` 目录是软链 → import 会静默跟随出去。逐段校验（repo_adapter.op_dir）必须拒。"""
        with tempfile.TemporaryDirectory() as d, self._ops(d):
            root = os.path.realpath(d)
            outside = os.path.join(root, "outside")
            os.makedirs(outside)
            with open(os.path.join(outside, "golden.py"), "w", encoding="utf-8") as f:
                f.write("GOLDEN_SOURCE='x'\nGOLDEN_PROVENANCE='x'\ndef golden_fn(i, a):\n    return i[0]\n")
            os.symlink(outside, os.path.join(root, "LinkDirOp"))
            through = os.path.join(root, "LinkDirOp", "golden.py")
            self.assertTrue(os.path.isfile(through))       # 跟随软链后确实能读到（洞真实存在）
            self.assertFalse(os.path.islink(through))      # 最终组件不是软链 → 旧检查挡不住
            with self.assertRaises(ValueError) as cm:
                GC.load_golden("LinkDirOp")
            self.assertIn("符号链接", str(cm.exception))

    def test_valid_golden_loads(self):
        with tempfile.TemporaryDirectory() as d, self._ops(d):
            _place_golden(os.path.realpath(d), "Sign")            # 从 samples/golden 拷
            fn, src, prov = GC.load_golden("Sign")
            self.assertTrue(callable(fn))
            self.assertTrue(src.startswith("torch "))
            self.assertTrue(prov)


if __name__ == "__main__":
    unittest.main()
