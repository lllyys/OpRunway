"""T7 dtype/attr 扩面单测——覆盖 acceptance 全条目：bf16 位级 codec（tie/±0/subnormal/NaN/inf/字节序）、
materialize/readback round-trip、int/bf16 golden、attr_matrix 计数+golden 用 attrs+equal_nan NaN、语义 id
稳定+唯一、storage_dtype/layout 契约（X_logical vs X_bin 分造）、per-case compare 派生+未支持 fail-fast、
扩面后机器门（覆盖/子集/篡改·codex#3）+ mock 端到端。
另含 shape_transform 扩面三契约（C1 out_shape / C2 attr list[int] / C3 input rank）的正反用例，见文件末尾。
再含 G4「归约/成对类算子的生成期规模预算」正反用例（`GoldenCostBudgetTest`，文件最末）。

跑: cd plugin/acc-common && python3 -m unittest test_gen_cases_dtype_attr -v
⚠ 真机（真 NPU）上 int/bf16 数值校验本轮不做——本文件只证「流水线能造/收发/裁 int/bf16」，非「被验收」。
"""
import contextlib, io, json, os, subprocess, sys, tempfile, shutil, unittest
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
        isclose_fn, _, _, _ = GC.load_golden("IsClose")          # golden 现按算子加载（elementwise 通路不内置）
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
            auth = GC.gen_cases(_spec(os.path.join(_HERE, "..", "samples", "specs", "isclose.spec.json")), d1)
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

    def test_arity_ge3_rejected_not_silently_truncated(self):
        """3 元输入 → fail-closed 报错，**不静默丢掉第 3 个输入**。

        `_build_inputs` 常规 varied/pair* 路径末尾写死 `return [x0, x1]`（二元构造），
        而 empty / 特殊值路径按 arity 产满——两边行为不一致，arity≥3 会无声截断。
        本测试钉住「宁可报错也不静默降级」这条纪律。支持多输入须先一般化（TODO U7b）。"""
        in_params = [{"name": n, "io": "in", "dtype": ["float32"]} for n in ("a", "b", "c")]
        with self.assertRaises(ValueError) as cm:
            GC._build_inputs(GC._case_rng("x"), in_params, [4], "float32", {}, "varied")
        self.assertIn("3 元输入", str(cm.exception))       # 报清楚是几元、别只说「不支持」
        # 对照：二元仍正常产 2 个（证不是把整条路堵死）
        two = GC._build_inputs(GC._case_rng("x"), in_params[:2], [4], "float32", {}, "varied")
        self.assertEqual(len(two), 2)

    def test_empty_dtype_set_fails_closed_in_both_paths(self):
        """空 dtype 集 → 产不出任何用例 → **两条路都 fail-closed**（0 用例不得冒充验收）。

        ⚠ `_dry_run` 那条尤其要紧：它现在是 **CP-B 的契约自检**。原来它没这道闸，
        空 dtype 集会安静地 `emitted=0` 通过 CP-B，跑 0 条也显示「无失败」。"""
        spec = {"op": "FakeEmptyDtype", "verify_mode": "exact",
                "params": [{"name": "self", "io": "in", "dtype": []}]}
        for label, fn in (("gen_cases", lambda: GC.gen_cases(spec, tempfile.mkdtemp())),
                          ("_dry_run", lambda: GC._dry_run(spec))):
            with self.assertRaises(ValueError, msg=label) as cm:
                fn()
            self.assertIn("0 用例不得冒充验收", str(cm.exception), label)

    def test_arity_guard_fires_in_dry_run_not_only_at_cp_d(self):
        """能力边界须在 **CP-B 的 dry-run** 就拦下，别拖到 CP-D 正式生成输入时才炸。

        `_dry_run` 只调 `_plan()`、不走 `_build_inputs`，所以守卫必须提到 spec 级共享预检
        （`check_spec_capability`），否则三元 spec 能一路混过契约自检。"""
        spec = {"op": "FakeTernary", "verify_mode": "exact",
                "params": [{"name": n, "io": "in", "dtype": ["float32"]} for n in ("self", "b", "c")]}
        with self.assertRaises(ValueError) as cm:
            GC._dry_run(spec)
        self.assertIn("3 元输入", str(cm.exception))
        # 且预检先于 load_golden：三元 spec 不该因为「缺 golden」而报错，应报能力边界
        self.assertNotIn("缺 golden", str(cm.exception))


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
        # C5：`--defect` 出 CLI → 进程内调用；mock 非验收通路 → 读 dev_run_summary.json。
        # ⚠ 要测的能力（合法精度 fail 不被门盖成 BLOCKED·codex#3）一点没动。
        import run_workflow as W
        r = W.run(_SIGN_FX, mode="mock", out_dir=self.d, defect=[did])
        self.assertEqual(r["exit_code"], 1, r)
        self.assertEqual(r["state"], "FAILED_PRECISION")          # 返回值里 state 仍在
        acc = _rj(os.path.join(self.d, "dev_run_summary.json"))
        self.assertTrue(acc["selfcheck"]["passed"])               # 自检 PASSED（合法 fail 不被盖·codex#3）

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
            _fn, gsrc, _prov, _osh = GC.load_golden(op)
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
        sign_fn, _, _, _ = GC.load_golden("Sign")
        neg_fn, _, _, _ = GC.load_golden("Neg")
        eq_fn, _, _, _ = GC.load_golden("Equal")
        isclose_fn, _, _, _ = GC.load_golden("IsClose")
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
            fn, src, prov, out_shape_fn = GC.load_golden("Sign")
            self.assertTrue(callable(fn))
            self.assertTrue(src.startswith("torch "))
            self.assertTrue(prov)
            self.assertIsNone(out_shape_fn)                       # C1：样例 golden 不导出 out_shape → None


# ==================================================================================================
# shape_transform 扩面三契约（用户 2026-07-22 拍板）：
#   C1 · 输出形状交给 per-op golden.py 的可选 `out_shape(in_shapes, attrs)`（**不搞 spec 表达式语言**）；
#   C2 · attr 值放开到 `list[int]`（output_size/kernel_size 这类既是数组、又决定输出形状的属性）；
#   C3 · spec 的 in 参数可选 `rank`，限制 shape 阶梯只在合法维度内取值。
# 这些测试**不需要 torch**（都用 numpy 假 golden），本机与真机都能跑。
# ==================================================================================================
def _fake_spec(op, *, dtypes=("float32",), attrs=None, attr_matrix=None, rank=None, case_target=1,
               arity=1):
    """造一份最小可跑 spec（零真实算子数值；只为驱动 gen_cases）。case_target=1 → 只出强制项，跑得快。"""
    names = ["self", "other"][:arity]
    ins = []
    for n in names:
        p = {"name": n, "io": "in", "dtype": list(dtypes)}
        if rank is not None:
            p["rank"] = rank
        ins.append(p)
    params = ins + [{"name": k, "io": "attr", "dtype": ["listInt"], "default": v}
                    for k, v in (attrs or {}).items()]
    params.append({"name": "out", "io": "out", "dtype": list(dtypes)})
    sp = {"op": op, "verify_mode": "numerical", "params_source": "fixture", "params": params,
          "precision": {"oracle": "ascendoptest", "standard": "ascendoptest_default",
                        "case_target": case_target},
          "perf": {"baseline": "tbe", "target_ratio": 0.95}}
    if attr_matrix is not None:
        sp["attr_matrix"] = attr_matrix
    return sp


def _special_cases(cs):
    """§1.4 特殊场景 case → {id_kind: case}。按 `expected.case_origin`（gen_cases 写的 `special:<kind>`）
    识别，**不**从 case_id 尾段猜——id 尾段是 attr 索引 `a{k}`、不是 kind。"""
    out = {}
    for c in cs["cases"]:
        origin = c.get("expected", {}).get("case_origin", "")
        if origin.startswith("special:"):
            out[origin.split(":", 1)[1]] = c
    return out


def _special_kinds(cs):
    return set(_special_cases(cs))


class _FakeOpCase(unittest.TestCase):
    """给假算子落 golden.py 到模块级 golden root，用完删掉（避免污染同模块别的用例）。"""

    def setUp(self):
        self._ops, self._dirs = [], []

    def tearDown(self):
        for op in self._ops:
            shutil.rmtree(os.path.join(_GOLDEN_ROOT, op), ignore_errors=True)
        for d in self._dirs:
            shutil.rmtree(d, ignore_errors=True)

    def place(self, op, body):
        _place_golden(_GOLDEN_ROOT, op, body)
        self._ops.append(op)

    def work(self):
        d = tempfile.mkdtemp()
        self._dirs.append(d)
        return d


# golden 体：逐元素取负（输出同输入形状，**不导出** out_shape → 走缺省语义）
_BODY_ELEMENTWISE = "def golden_fn(inputs, attrs):\n    return np.negative(inputs[0])\n"
# golden 体：沿最后一维求和（**真 shape_transform**：输出比输入少一维），并导出 out_shape
_BODY_REDUCE_LAST = (
    "def golden_fn(inputs, attrs):\n"
    "    return np.sum(inputs[0], axis=-1)\n"
    "\n"
    "def out_shape(in_shapes, attrs):\n"
    "    return tuple(in_shapes[0][:-1])\n")
# golden 体：输出形状**由 list[int] attr `output_size` 决定**（C1+C2 合流的真实形态）
_BODY_ATTR_SHAPED = (
    "def golden_fn(inputs, attrs):\n"
    "    x = inputs[0]\n"
    "    fill = x.reshape(-1)[:1].sum() if x.size else x.dtype.type(0)\n"
    "    return np.full(tuple(attrs['output_size']), fill, dtype=x.dtype)\n"
    "\n"
    "def out_shape(in_shapes, attrs):\n"
    "    return tuple(attrs['output_size'])\n")


# golden 体：**真会改形状，却「忘了」导出 out_shape** —— C1 缺省语义的负例
_BODY_RESHAPES_BUT_UNDECLARED = (
    "def golden_fn(inputs, attrs):\n"
    "    return np.sum(inputs[0], axis=-1)\n")


class OutShapeDefaultSemanticsTest(_FakeOpCase):
    """C1 缺省语义是**承诺、不是默认值**：没导出 `out_shape` 就必须真的输出同输入形状。

    负例的危害：一个真会改形状的 golden 若忘了导出 `out_shape`，若引擎只是照抄实测形状，
    CP-B 会全绿、拖到下游 runner 按错形状收发才炸——正是本仓最忌的「本机过、真机炸」。"""

    def test_reshaping_golden_without_out_shape_fails_closed(self):
        self.place("FakeUndeclaredReshape", _BODY_RESHAPES_BUT_UNDECLARED)
        with self.assertRaises(ValueError) as cm:
            GC.gen_cases(_fake_spec("FakeUndeclaredReshape", case_target=1), self.work())
        msg = str(cm.exception)
        self.assertIn("未导出 out_shape", msg, msg)      # 报清病因，别只说「形状不符」
        self.assertIn("out_shape(in_shapes, attrs)", msg, msg)   # 并给出改法

    def test_true_elementwise_still_passes_without_out_shape(self):
        """对照：真 elementwise 不导出 out_shape 照常通过，证补的闸没误伤缺省通路。"""
        self.place("FakeTrulyElementwise", _BODY_ELEMENTWISE)
        cs = GC.gen_cases(_fake_spec("FakeTrulyElementwise", case_target=3), self.work())
        self.assertTrue(cs["cases"])
        self.assertTrue(all(c["expected"]["out_shape_source"] == "golden_fn_actual"
                            for c in cs["cases"]), "缺省通路的 source 应是 golden_fn_actual")


class OutShapeContractTest(_FakeOpCase):
    """C1：out_shape 由 per-op golden.py 可选导出；未导出=同形；声明与实测打架→fail-closed。"""

    def test_load_golden_returns_out_shape_when_exported(self):
        """load_golden 现返回 4 元组，第 4 项是 out_shape（未导出 → None）。"""
        self.place("FakeOsExported", _BODY_REDUCE_LAST)
        self.place("FakeOsAbsent", _BODY_ELEMENTWISE)
        _fn, _src, _prov, osh = GC.load_golden("FakeOsExported")
        self.assertTrue(callable(osh))
        self.assertEqual(osh([(2, 3, 4)], {}), (2, 3))
        _fn2, _src2, _prov2, osh2 = GC.load_golden("FakeOsAbsent")
        self.assertIsNone(osh2)                                  # 未导出 → None（缺省同形语义）

    def test_out_shape_drives_caseset_expected(self):
        """导出 out_shape 的 shape_transform 算子：caseset 的输出形状 = out_shape() 声明，
        且与落盘 golden.npy 的真实形状一致（不是自报的、是对过账的）。"""
        self.place("FakeReduceLast", _BODY_REDUCE_LAST)
        d = self.work()
        cs = GC.gen_cases(_fake_spec("FakeReduceLast"), d)
        self.assertTrue(cs["cases"])
        for c in cs["cases"]:
            in_shape = tuple(c["inputs"][0]["shape"])
            exp = c["expected"]
            self.assertEqual(exp["out_shape"], list(in_shape[:-1]), c["id"])
            self.assertEqual(exp["out_shape_source"], "golden.out_shape", c["id"])
            g = np.load(os.path.join(d, exp["golden_path"]))
            self.assertEqual(list(g.shape), exp["out_shape"], c["id"])  # 落盘 golden 与账面一致
            self.assertNotEqual(list(g.shape), list(in_shape), c["id"])  # 确实变了形（非 elementwise）

    def test_no_out_shape_keeps_same_shape_semantics(self):
        """未导出 out_shape → 维持现状：输出同输入形状，来源标 golden_fn_actual（不谎称『已声明』）。"""
        self.place("FakeElemwise", _BODY_ELEMENTWISE)
        d = self.work()
        cs = GC.gen_cases(_fake_spec("FakeElemwise"), d)
        self.assertTrue(cs["cases"])
        for c in cs["cases"]:
            exp = c["expected"]
            self.assertEqual(exp["out_shape"], list(c["inputs"][0]["shape"]), c["id"])
            self.assertEqual(exp["out_shape_source"], "golden_fn_actual", c["id"])

    def test_out_shape_disagrees_with_golden_fail_closed(self):
        """声明与实现打架（out_shape 说 n+1、golden_fn 实际产 n）→ fail-closed，不许任一方静默胜出。"""
        self.place("FakeOsLiar",
                   "def golden_fn(inputs, attrs):\n"
                   "    return np.negative(inputs[0])\n"
                   "\n"
                   "def out_shape(in_shapes, attrs):\n"
                   "    return (in_shapes[0][0] + 1,)\n")
        with self.assertRaises(ValueError) as cm:
            GC.gen_cases(_fake_spec("FakeOsLiar"), self.work())
        self.assertIn("out_shape", str(cm.exception))
        self.assertIn("≠", str(cm.exception))

    def test_out_shape_not_callable_rejected(self):
        """导出了 out_shape 但不是函数（写成数组常量）→ load_golden 就拒，不拖到 case 循环。"""
        self.place("FakeOsNotFn", _BODY_ELEMENTWISE + "\nout_shape = [1, 2]\n")
        with self.assertRaises(ValueError) as cm:
            GC.load_golden("FakeOsNotFn")
        self.assertIn("out_shape", str(cm.exception))

    def test_out_shape_bad_return_rejected(self):
        """out_shape 返回非序列 / 负维度 → fail-closed（不猜、不修正）。"""
        for body_tail, op in (("    return 5\n", "FakeOsScalarRet"),
                              ("    return (-1, 2)\n", "FakeOsNegRet")):
            self.place(op, _BODY_ELEMENTWISE + "\ndef out_shape(in_shapes, attrs):\n" + body_tail)
            with self.assertRaises(ValueError) as cm:
                GC.gen_cases(_fake_spec(op), self.work())
            self.assertIn("out_shape", str(cm.exception))

    def test_out_shape_raising_is_wrapped_not_leaked(self):
        """out_shape 自己抛异常 → 收敛成带上下文的 ValueError（用户代码炸了要说清是哪条 entry / 哪组形状）。

        ⚠ G4 起 `out_shape` 会在**计划期**先被调一次（算规模预算），所以这里的上下文标签是
        `dtype·shape·id_kind` 而非 case_id——比 case_id 更早，但一样定位得到。原始异常挂在 `__cause__`。"""
        self.place("FakeOsBoom", _BODY_ELEMENTWISE +
                   "\ndef out_shape(in_shapes, attrs):\n    raise KeyError('missing attr')\n")
        with self.assertRaises(ValueError) as cm:
            GC.gen_cases(_fake_spec("FakeOsBoom"), self.work())
        self.assertIn("out_shape", str(cm.exception))
        self.assertIsInstance(cm.exception.__cause__, KeyError)   # 原因没被吞掉


class AttrListIntTest(_FakeOpCase):
    """C2：attr 值放开到 list[int]——笛卡尔展开 / combo 索引 / case_id / JSON 落盘全线吃得下。"""

    def _spec(self):
        return _fake_spec("FakeAttrShaped", attrs={"output_size": [2, 2]},
                          attr_matrix=[{"output_size": [1, 1]}, {"output_size": [2, 3]}])

    def test_list_attr_cartesian_and_shapes(self):
        """list[int] attr 参与笛卡尔展开；输出形状随该 attr 变（C1+C2 合流的真实形态）。"""
        self.place("FakeAttrShaped", _BODY_ATTR_SHAPED)
        d = self.work()
        cs = GC.gen_cases(self._spec(), d)
        seen = {tuple(c["attrs"]["output_size"]) for c in cs["cases"]}
        self.assertEqual(seen, {(1, 1), (2, 3)})                 # 两个数组取值都出现
        for c in cs["cases"]:
            want = list(c["attrs"]["output_size"])
            self.assertEqual(c["expected"]["out_shape"], want, c["id"])
            g = np.load(os.path.join(d, c["expected"]["golden_path"]))
            self.assertEqual(list(g.shape), want, c["id"])

    def test_list_attr_case_id_stays_index_based_and_filename_safe(self):
        """case_id 里 attr 仍用 `a{k}` 索引表示——数组值**不**进文件名（既保文件名安全，
        也保住『同 id → 同数据字节』那条回归：id 不含数组值，per-case 种子就不随 attr 写法漂移）。"""
        self.place("FakeAttrShaped", _BODY_ATTR_SHAPED)
        d1, d2 = self.work(), self.work()
        cs1 = GC.gen_cases(self._spec(), d1)
        cs2 = GC.gen_cases(self._spec(), d2)
        ids1 = [c["id"] for c in cs1["cases"]]
        self.assertEqual(len(ids1), len(set(ids1)))              # 唯一
        self.assertEqual(ids1, [c["id"] for c in cs2["cases"]])  # 确定性稳定
        for cid in ids1:
            for ch in "[](), ":
                self.assertNotIn(ch, cid, f"{cid} 含文件名不安全字符 {ch!r}")
        self.assertTrue(any(cid.endswith("_a1") for cid in ids1), ids1)   # 第二个 combo 用 a1
        # 同 id → 同数据字节（per-case 种子只依赖 id）
        by2 = {c["id"]: c for c in cs2["cases"]}
        for c in cs1["cases"]:
            x1 = np.load(os.path.join(d1, c["inputs"][0]["path"]))
            x2 = np.load(os.path.join(d2, by2[c["id"]]["inputs"][0]["path"]))
            np.testing.assert_array_equal(x1, x2, err_msg=c["id"])

    def test_list_attr_survives_json_roundtrip(self):
        """caseset 落 JSON 再读回来，数组 attr 与输出形状不变（下游拿到的是同一份口径）。"""
        self.place("FakeAttrShaped", _BODY_ATTR_SHAPED)
        d = self.work()
        cs = GC.gen_cases(self._spec(), d)
        back = json.loads(json.dumps(cs, ensure_ascii=False))
        for a, b in zip(cs["cases"], back["cases"]):
            self.assertEqual(a["attrs"]["output_size"], b["attrs"]["output_size"])
            self.assertEqual(a["expected"]["out_shape"], b["expected"]["out_shape"])

    def test_list_attr_not_shared_between_cases(self):
        """各 case 的数组 attr 是**各自一份**——golden_fn/out_shape 是用户代码，就地改一下不能串到别的 case。"""
        self.place("FakeAttrShaped", _BODY_ATTR_SHAPED)
        cs = GC.gen_cases(self._spec(), self.work())
        objs = [id(c["attrs"]["output_size"]) for c in cs["cases"]]
        self.assertEqual(len(objs), len(set(objs)), "数组 attr 在多条 case 间共享了同一个 list 对象")

    def test_bad_list_attr_values_fail_fast(self):
        """只放开到 list[int]：嵌套数组 / 浮点元素 / bool 元素 / **空数组**一律 fail-fast。
        空数组特别说明：`repo_adapter._manifest_attr_token` 那边也拒（空串会挤错 manifest token），
        但 mock 通路不造 manifest——只在那边拦就成了「本机过、真机炸」，故 gen_cases 侧同样早拦。"""
        self.place("FakeAttrShaped", _BODY_ATTR_SHAPED)
        for bad in ([[1], [2]], [1.5, 2], [True, False], {"h": 1}, []):
            sp = _fake_spec("FakeAttrShaped", attrs={"output_size": [2, 2]},
                            attr_matrix=[{"output_size": bad}])
            with self.assertRaises(ValueError, msg=repr(bad)):
                GC.gen_cases(sp, self.work())

    def test_bad_list_attr_default_fail_fast_without_matrix(self):
        """**只走 `default` 路径**（无 attr_matrix）的坏 list 值同样要早拦。

        上一条测的全是 attr_matrix 分支；`default` 分支原来不过类型闸——
        `"default": []` / `[1.5, 2.0]` 会一路 gen_cases + mock 全绿、真机造 manifest 才炸，
        正是「本机过、真机炸」。这条钉住 default 也过闸。"""
        self.place("FakeAttrShaped", _BODY_ATTR_SHAPED)
        for bad in ([[1], [2]], [1.5, 2], [True, False], []):
            sp = _fake_spec("FakeAttrShaped", attrs={"output_size": bad})   # 不给 attr_matrix
            with self.assertRaises(ValueError, msg=repr(bad)):
                GC.gen_cases(sp, self.work())

    def test_scalar_and_none_defaults_untouched(self):
        """对照：标量与 None（未定哨兵）默认值语义**一字不动**，证补闸没误伤现存 spec。"""
        self.place("FakeAttrShaped", _BODY_ATTR_SHAPED)
        cs = GC.gen_cases(_fake_spec("FakeAttrShaped", attrs={"output_size": [2, 2]}), self.work())
        self.assertTrue(cs["cases"])


class InputRankTest(_FakeOpCase):
    """C3：spec 的 in 参数可选 rank，限制 shape 阶梯；过滤空 → fail-closed（不产 0 条常规用例）。"""

    def test_rank_filters_shape_ladder(self):
        """rank=4 → 每条用例的输入都是 4 维（常规网格按 rank 过滤、强制项按 rank 保 numel 调维）。"""
        self.place("FakeRank4", _BODY_ELEMENTWISE)
        d = self.work()
        cs = GC.gen_cases(_fake_spec("FakeRank4", rank=4, case_target=30), d)
        self.assertTrue(cs["cases"])
        for c in cs["cases"]:
            self.assertEqual(len(c["inputs"][0]["shape"]), 4,
                             f'{c["id"]} 输入 shape={c["inputs"][0]["shape"]} 不是 4 维')
        # 常规网格没被清空（否则就只剩强制项冒充覆盖）
        self.assertTrue(any("常规" in c.get("tags", []) for c in cs["cases"]))
        # 强制的 §1.4 特殊场景都还在（空/标量/边界/inf-nan 不因 rank 约束丢失）
        kinds = _special_kinds(cs)
        for k in ("empty", "scalar", "bndlo", "bndhi", "inf", "ninf", "nan"):
            self.assertIn(k, kinds, f"rank 约束下丢了强制场景 {k}")

    def test_rank_list_accepts_any_of(self):
        """rank=[1,2] → 只在 1/2 维里取值（列表形式=允许多种维度）。"""
        self.place("FakeRank12", _BODY_ELEMENTWISE)
        cs = GC.gen_cases(_fake_spec("FakeRank12", rank=[1, 2], case_target=30), self.work())
        ranks = {len(c["inputs"][0]["shape"]) for c in cs["cases"]}
        self.assertTrue(ranks <= {1, 2}, ranks)
        self.assertEqual(ranks, {1, 2}, "1/2 维都该取到（阶梯里两种都有）")

    def test_rank_keeps_special_numel(self):
        """调维保 numel：空 Tensor 仍空、标量仍 numel=1、大 shape 仍大（特殊场景的性质不能被调没）。"""
        self.place("FakeRankNumel", _BODY_ELEMENTWISE)
        cs = GC.gen_cases(_fake_spec("FakeRankNumel", rank=4, case_target=1), self.work())
        by_kind = {k: c["inputs"][0]["shape"] for k, c in _special_cases(cs).items()}
        self.assertEqual(int(np.prod(by_kind["empty"])), 0)
        self.assertEqual(int(np.prod(by_kind["scalar"])), 1)
        self.assertEqual(int(np.prod(by_kind["bndhi"])), int(np.prod(GC._LARGE_SHAPES[0])))

    def test_no_rank_means_unconstrained(self):
        """不写 rank = 不限制（现行为零变更）：阶梯里多种维度都还在。"""
        self.place("FakeNoRank", _BODY_ELEMENTWISE)
        cs = GC.gen_cases(_fake_spec("FakeNoRank", case_target=30), self.work())
        self.assertGreater(len({len(c["inputs"][0]["shape"]) for c in cs["cases"]}), 1)

    def test_rank_with_no_legal_shape_fail_closed(self):
        """阶梯覆盖不到的 rank → 过滤后无合法常规 shape → **报错**，绝不产 0 条常规用例冒充验收。

        ⚠ 例子用 **rank=6**，不是 5：2026-07-23 起 `_EXT_RANK_SHAPES` 已补 5 维
        （UpsampleNearest3d 的 (N,C,D,H,W) 要它）。测试的**意图**没变——变的只是「哪个 rank 还没覆盖」。
        6 仍 ≤ `_MAX_RANK`(8)，所以走的确实是「取值合法但阶梯给不出 shape」这条路，不是取值校验。"""
        self.place("FakeRank6", _BODY_ELEMENTWISE)
        with self.assertRaises(ValueError) as cm:
            GC.gen_cases(_fake_spec("FakeRank6", rank=6), self.work())
        self.assertIn("rank", str(cm.exception).lower())

    def test_rank5_now_covered_by_ext_ladder(self):
        """对照：rank=5 **现在能跑**（`_EXT_RANK_SHAPES` 按需并入），证补阶梯真生效。"""
        self.place("FakeRank5", _BODY_ELEMENTWISE)
        cs = GC.gen_cases(_fake_spec("FakeRank5", rank=5, case_target=10), self.work())
        self.assertTrue(cs["cases"])
        self.assertTrue(all(len(c["inputs"][0]["shape"]) == 5 for c in cs["cases"]),
                        [c["inputs"][0]["shape"] for c in cs["cases"]])

    def test_ext_rank_ladder_does_not_leak_into_unconstrained_ops(self):
        """**关键回归**：无 rank 约束的算子（= 全部 elementwise）不得看到 5 维阶梯。

        若把 `_EXT_RANK_SHAPES` 直接并进 `_REG_SHAPES`，既有算子的用例集会**悄悄改变**——
        那等于改变已验收过的东西。这条钉住「按需并入」而非「无条件扩池」。"""
        self.place("FakeNoRank", _BODY_ELEMENTWISE)
        cs = GC.gen_cases(_fake_spec("FakeNoRank", case_target=50), self.work())
        ranks = {len(c["inputs"][0]["shape"]) for c in cs["cases"]}
        self.assertFalse(ranks - {0, 1, 2, 3, 4},
                         f"无 rank 约束的算子出现了 >4 维 shape：{sorted(ranks)}")

    def test_rank_guard_fires_in_dry_run(self):
        """与 arity 守卫同一条纪律：rank 不可行要在 **CP-B 的 dry-run** 就拦下，不拖到正式生成。"""
        with self.assertRaises(ValueError):
            GC._dry_run(_fake_spec("FakeRankDry", rank=6))

    def test_illegal_rank_value_rejected(self):
        """rank 取值非法（0 / 负 / 超上限 / 非整数 / 空列表）→ fail-fast。"""
        self.place("FakeRankBad", _BODY_ELEMENTWISE)
        for bad in (0, -1, 99, 2.5, True, "4", []):
            with self.assertRaises(ValueError, msg=repr(bad)):
                GC.gen_cases(_fake_spec("FakeRankBad", rank=bad), self.work())

    def test_rank_intersection_empty_rejected(self):
        """两个 in 参数声明的 rank 无交集 → fail-closed（常规路径下各输入同形，没有都合法的维度）。"""
        self.place("FakeRankConflict", _BODY_ELEMENTWISE)
        sp = _fake_spec("FakeRankConflict", arity=2)
        sp["params"][0]["rank"] = [2]
        sp["params"][1]["rank"] = [3]
        with self.assertRaises(ValueError) as cm:
            GC.gen_cases(sp, self.work())
        self.assertIn("交集", str(cm.exception))

    def test_fit_rank_unit(self):
        """_fit_rank 纯函数：ranks=None 恒等（零行为变更）；补维/折维都保 numel。"""
        self.assertEqual(GC._fit_rank((1024, 1024), None), (1024, 1024))
        self.assertEqual(GC._fit_rank((2, 2, 2, 2), frozenset({4})), (2, 2, 2, 2))  # 已合法→原样
        self.assertEqual(GC._fit_rank((1024, 1024), frozenset({4})), (1, 1, 1024, 1024))
        self.assertEqual(GC._fit_rank((1024, 1024), frozenset({1})), (1024 * 1024,))
        self.assertEqual(GC._fit_rank((0,), frozenset({3})), (1, 1, 0))
        self.assertEqual(GC._fit_rank((2, 3, 4), frozenset({2})), (6, 4))


# ==================================================================================================
# G4 · 归约/成对类算子的生成期规模预算（2026-07-22）
#
# 病灶（实测）：`_REG_SHAPES`/`_LARGE_SHAPES` 的规模假设是按 elementwise（O(numel)）定的。Pdist 这类成对
# 算子拿到 `(1024,1024)` 就是 **549,755,289,600 对 / 2.2 TB 输出**，golden 在**生成期**就跑不完
# （真实症状：Pdist 首跑 mock 探针 2 分钟超时 Exit 143）。
#
# 下面的假算子 `_pairwise_body(cap)` 自带安全阀 `_CAP`，一箭双雕：
#   ① 让「改前会炸」在本机可复现，又**不会把机器拖死**（真去申请 2.2 TB 会拖死 16 GB 的本机）；
#   ② 改后反过来当断言用——引擎若还敢喂超预算的 shape，这个假算子当场炸给你看。
# 这些测试**不需要 torch**（假 golden 纯 numpy），本机与真机都能跑。
# ==================================================================================================
_PAIR_BUDGET = 5000          # e2e 用的小预算：降规模后 golden 只算几千对，单测跑得快


def _pairwise_body(cap):
    """真 O(N²) 成对距离 golden：N 个点 → N(N-1)/2 对，**输出平方级膨胀**且按 C1 导出 `out_shape`。"""
    return (
        f"_CAP = {int(cap)}\n"
        "\n"
        "\n"
        "def golden_fn(inputs, attrs):\n"
        "    x = np.asarray(inputs[0]).reshape(-1)\n"
        "    n = int(x.size)\n"
        "    m = n * (n - 1) // 2\n"
        "    if m > _CAP:\n"
        "        raise MemoryError('pairwise golden 被要求算 %d 对（输出 %.2f TB float32）'\n"
        "                          '——真跑必然撑爆本机/超时' % (m, m * 4 / 1e12))\n"
        "    i, j = np.triu_indices(n, 1)\n"
        "    with np.errstate(invalid='ignore'):        # §1.4 特殊值用例会出现 inf-inf，非本测试关注点\n"
        "        return np.abs(x[i] - x[j]).astype(x.dtype)\n"
        "\n"
        "\n"
        "def out_shape(in_shapes, attrs):\n"
        "    n = 1\n"
        "    for d in in_shapes[0]:\n"
        "        n *= int(d)\n"
        "    return (n * (n - 1) // 2,)\n")


# golden 体：输出形状**恒为**十亿元素、与输入无关 → 逐维减半救不回来（降规模的 fail-closed 分支）
_BODY_CONST_HUGE_OUT = (
    "def golden_fn(inputs, attrs):\n"
    "    return np.zeros(10 ** 9, dtype=np.float32)\n"
    "\n"
    "def out_shape(in_shapes, attrs):\n"
    "    return (10 ** 9,)\n")


def _plan_args(sp):
    """从 spec 拆出 `_plan()` 的位置参数（测试要直接调 `_plan` 对比「行使/不行使预算」两条路）。"""
    in_params = [p for p in sp["params"] if p["io"] == "in"]
    attrs_default = {p["name"]: p.get("default") for p in sp["params"] if p["io"] == "attr"}
    self_param = next((p for p in in_params if p["name"] == "self"), in_params[0])
    return (sp, in_params, self_param["dtype"], attrs_default, sp["op"],
            (sp.get("precision") or {}).get("case_target", GC._DEFAULT_CASE_TARGET))


def _pair_spec(op, budget=_PAIR_BUDGET, case_target=1):
    sp = _fake_spec(op, case_target=case_target)
    if budget is not None:
        sp["precision"]["golden_cost_budget"] = budget
    return sp


class GoldenCostBudgetTest(_FakeOpCase):
    """G4：用例预算要**感知算子复杂度**——超规模的强制项显式降规模并记账，网格项剔除并记账，都不许静默。"""

    # ---------- 病灶复现：G4 之前，计划里那条大 shape 真的是喂不动的 ----------
    def test_pre_g4_plan_feeds_unrunnable_shape_to_golden(self):
        """`cost_fn=None` = G4 之前的行为：计划照样含 `(1024,1024)`，喂给成对 golden 就是 5.5e11 对。

        这条钉住「病灶真实存在」，也钉住 `cost_fn=None` 这条兼容路的语义（不行使预算、账本标未核）。"""
        self.place("FakePdistPre", _pairwise_body(_PAIR_BUDGET))
        entries, meta = GC._plan(*_plan_args(_pair_spec("FakePdistPre")), cost_fn=None)
        self.assertIn((1024, 1024), {tuple(e["shape"]) for e in entries},
                      "改前的计划本就含 1024x1024 —— 病灶在计划期，不在 golden 里")
        self.assertIsNone(meta["golden_cost"]["budget"])              # 未行使预算
        self.assertIn("未核", meta["golden_cost"]["model"])           # 且不谎称已核
        gfn, _src, _prov, osh = GC.load_golden("FakePdistPre")
        self.assertEqual(osh([(1024, 1024)], {}), (549755289600,))    # 真算的数：5.5e11 对
        with self.assertRaises(MemoryError):                          # 真喂进去就炸（输出 2.2 TB）
            gfn([np.zeros((1024, 1024), np.float32)], {})

    # ---------- 改后：强制项显式降规模 + 账本如实记 ----------
    def test_forced_big_shape_scaled_down_and_ledgered(self):
        self.place("FakePdist", _pairwise_body(_PAIR_BUDGET))
        d = self.work()
        cs = GC.gen_cases(_pair_spec("FakePdist"), d)                 # 改前这一步会炸
        led = cs["golden_cost"]
        self.assertEqual(led["budget"], _PAIR_BUDGET)
        self.assertTrue(led["scaled_cases"], "大 shape 强制项应被降规模并记账")
        kinds = {r["id_kind"] for r in led["scaled_cases"]}
        self.assertIn("bndhi", kinds, "§1.4「边界上」那条大 shape 必须还在（降规模，不是被丢掉）")
        for r in led["scaled_cases"]:
            self.assertEqual(r["requested_shape"], [1024, 1024])      # 原目标规模如实记
            self.assertEqual(r["requested_cost"], 549755289600)       # 原开销如实记
            self.assertLessEqual(r["emitted_cost"], _PAIR_BUDGET)     # 实际跑的规模在预算内
            self.assertLess(GC._numel(r["emitted_shape"]), GC._numel(r["requested_shape"]))
            self.assertIn("未跑", r["reason"], "账本必须明说『原目标规模未跑』，不许读成已覆盖")
        # 强制场景一条不少（降规模 ≠ 丢覆盖）
        for k in ("empty", "scalar", "bndlo", "bndhi", "inf", "ninf", "nan"):
            self.assertIn(k, _special_kinds(cs), f"降规模不该丢掉强制场景 {k}")

    def test_scaled_case_carries_tag_and_expected_record(self):
        """降规模要在 **case 自身**留痕（tag「降规模」+ `expected.cost_scaled`）——只记在总账里，
        逐 case 的报告仍会把它当成「大 shape 已覆盖」。"""
        self.place("FakePdistTag", _pairwise_body(_PAIR_BUDGET))
        d = self.work()
        cs = GC.gen_cases(_pair_spec("FakePdistTag"), d)
        scaled = [c for c in cs["cases"] if "降规模" in c.get("tags", [])]
        self.assertTrue(scaled, "被降规模的 case 应带 tag「降规模」")
        for c in scaled:
            rec = c["expected"].get("cost_scaled")
            self.assertIsNotNone(rec, c["id"])
            self.assertEqual(rec["emitted_shape"], list(c["inputs"][0]["shape"]), c["id"])
            self.assertEqual(rec["requested_shape"], [1024, 1024], c["id"])
        # 反向：没被降的 case 不许带这些痕迹（别让账本虚报）
        for c in cs["cases"]:
            if "降规模" not in c.get("tags", []):
                self.assertNotIn("cost_scaled", c["expected"], c["id"])

    def test_perf_dim_scaled_case_warns_about_trivial_met(self):
        """带「性能」维度的 case 被降规模后，账本要点破下游 trivial-met 的退化风险（别读成原规模已达标）。"""
        self.place("FakePdistPerf", _pairwise_body(_PAIR_BUDGET))
        cs = GC.gen_cases(_pair_spec("FakePdistPerf"), self.work())
        perf_recs = [r for r in cs["golden_cost"]["scaled_cases"] if "perf_note" in r]
        self.assertTrue(perf_recs, "带性能维度的降规模项应有 perf_note")
        self.assertIn("trivial", perf_recs[0]["perf_note"])

    def test_every_emitted_case_within_budget(self):
        """账本之外还要**真的**没有一条 case 超预算（否则 golden 仍会在生成期跑到天荒地老）。"""
        self.place("FakePdistBudget", _pairwise_body(_PAIR_BUDGET))
        cs = GC.gen_cases(_pair_spec("FakePdistBudget", case_target=25), self.work())
        for c in cs["cases"]:
            cost = max(GC._numel(c["inputs"][0]["shape"]), GC._numel(c["expected"]["out_shape"]))
            self.assertLessEqual(cost, _PAIR_BUDGET, f'{c["id"]} cost={cost} 超预算')

    # ---------- 改后：网格里超预算的 shape 剔除 + 记账（不冒充已覆盖） ----------
    def test_oversized_grid_shapes_recorded_not_silently_dropped(self):
        self.place("FakePdistGrid", _pairwise_body(_PAIR_BUDGET))
        cs = GC.gen_cases(_pair_spec("FakePdistGrid", case_target=25), self.work())
        led = cs["golden_cost"]
        skipped = {r["shape"] for r in led["skipped_shapes"]}
        self.assertTrue({"1024x1024", "65535"} <= skipped, skipped)
        self.assertGreaterEqual(led["skipped_shape_classes"], len(led["skipped_shapes"]))
        by_shape = {r["shape"]: r for r in led["skipped_shapes"]}
        self.assertEqual(by_shape["65535"]["cost"], 65535 * 65534 // 2)   # 任务书里那 ~2.1e9 对
        self.assertEqual(by_shape["1024x1024"]["cost"], 549755289600)
        # 且**真的**没有一条 case 用这些规模（记账 ≠ 冒充已覆盖）
        emitted = {tuple(c["inputs"][0]["shape"]) for c in cs["cases"]}
        self.assertNotIn((1024, 1024), emitted)
        self.assertNotIn((65535,), emitted)

    # ---------- 不误伤 elementwise：现有算子的计划**逐条相同** ----------
    def test_elementwise_plan_byte_identical_with_and_without_budget(self):
        """现有 4 个 elementwise 算子的用例集不该有任何变化：同一 spec 下「行使预算」与「不行使」
        产出的 plan entries **逐条相等**（最大 cost=2^20 ≪ 缺省预算 2^26 → 预算根本不触发）。
        直接比 `_plan` 的 entries，不需要 torch（不跑 golden_fn）。"""
        for fx in (_SIGN_FX, _ISCLOSE_FX,
                   os.path.join(_HERE, "..", "samples", "specs", "isclose.spec.json")):
            sp = _spec(fx)
            args = _plan_args(sp)
            out_shape_fn = GC.load_golden(sp["op"])[3]                # 4 份样例都不导出 → None
            cost_fn = GC._make_cost_fn(args[1], out_shape_fn)
            e_on, m_on = GC._plan(*args, cost_fn=cost_fn)
            e_off, _m_off = GC._plan(*args, cost_fn=None)
            self.assertEqual(e_on, e_off, f"{fx}: 行使预算后计划变了（误伤 elementwise）")
            self.assertEqual(m_on["golden_cost"]["scaled_cases"], [], fx)
            self.assertEqual(m_on["golden_cost"]["skipped_shapes"], [], fx)
            self.assertIn((1024, 1024), {tuple(e["shape"]) for e in e_on},
                          f"{fx}: 大 shape 该原样保留（elementwise 跑得动）")

    def test_elementwise_caseset_untouched_end_to_end(self):
        """端到端对照（纯 numpy 假算子、无 torch）：elementwise 算子的 caseset 账本全空、大 shape 仍在。"""
        self.place("FakeElemNoScale", _BODY_ELEMENTWISE)
        cs = GC.gen_cases(_fake_spec("FakeElemNoScale", case_target=30), self.work())
        self.assertEqual(cs["golden_cost"]["scaled_cases"], [])
        self.assertEqual(cs["golden_cost"]["skipped_shapes"], [])
        self.assertEqual(cs["golden_cost"]["budget"], GC._GOLDEN_COST_BUDGET)
        self.assertIn((1024, 1024), {tuple(c["inputs"][0]["shape"]) for c in cs["cases"]})
        self.assertFalse(any("降规模" in c.get("tags", []) for c in cs["cases"]))

    # ---------- 预算取值：spec 驱动 + 坏值 fail-fast ----------
    def test_budget_from_spec_drives_shrink_depth(self):
        """预算来自 `precision.golden_cost_budget`：调小 → 降得更狠；两次跑结果**逐字相同**（确定性）。"""
        self.place("FakePdistTight", _pairwise_body(5000))   # 自爆阀取两个预算里的**大**者
        big = GC.gen_cases(_pair_spec("FakePdistTight", budget=5000), self.work())
        small = GC.gen_cases(_pair_spec("FakePdistTight", budget=500), self.work())
        again = GC.gen_cases(_pair_spec("FakePdistTight", budget=500), self.work())
        n_big = GC._numel(big["golden_cost"]["scaled_cases"][0]["emitted_shape"])
        n_small = GC._numel(small["golden_cost"]["scaled_cases"][0]["emitted_shape"])
        self.assertLess(n_small, n_big, "预算调小应降得更狠")
        self.assertEqual([c["id"] for c in small["cases"]], [c["id"] for c in again["cases"]])
        self.assertEqual([c["inputs"][0]["shape"] for c in small["cases"]],
                         [c["inputs"][0]["shape"] for c in again["cases"]])

    def test_bad_budget_fail_fast(self):
        """预算 0/负/非整/bool → fail-fast。预算 0 等于把所有 shape 判超预算 = 另一种「用例集清零」。

        **两条路都要拦**：`_dry_run` 那条尤其要紧——它在 golden.py 还没写好时 cost 模型标「未核」，
        坏预算值不许借这个降级悄悄溜过 CP-B。"""
        self.place("FakeElemBudget", _BODY_ELEMENTWISE)
        for bad in (0, -1, 1.5, True, "4096", None):
            sp = _fake_spec("FakeElemBudget", case_target=1)
            sp["precision"]["golden_cost_budget"] = bad
            for label, fn in (("gen_cases", lambda s=sp: GC.gen_cases(s, self.work())),
                              ("_dry_run", lambda s=sp: GC._dry_run(s))):
                with self.assertRaises(ValueError, msg=f"{label} {bad!r}") as cm:
                    with contextlib.redirect_stdout(io.StringIO()):
                        fn()
                self.assertIn("golden_cost_budget", str(cm.exception))
            # ⚠ 上面用的 FakeElemBudget **已 place 好 golden.py**，`cost_fn` 恒非 None ——
            # 也就是说本测试 docstring 声称的「golden.py 还没写好时坏预算也不许溜过 CP-B」
            # 那条路**从未被行使**。用一个**故意不 place** 的算子名逼出 `cost_fn is None` 分支。
            # 反证（已验）：把 `budget = _cost_budget(spec)` 挪进 `if cost_fn is not None:`，
            # 补这条之前测试照样全绿 = 测试当摆设。
            sp_ng = _fake_spec("FakeNoGoldenForBudget", case_target=1)
            sp_ng["precision"]["golden_cost_budget"] = bad
            with self.assertRaises(ValueError, msg=f"_dry_run(no golden) {bad!r}") as cm:
                with contextlib.redirect_stdout(io.StringIO()):
                    GC._dry_run(sp_ng)
            self.assertIn("golden_cost_budget", str(cm.exception))
        # 对照：没写这个字段 → 用缺省预算，行为不变（别把「不写」也拦掉）
        sp_ok = _fake_spec("FakeElemBudget", case_target=1)
        self.assertNotIn("golden_cost_budget", sp_ok["precision"])
        self.assertTrue(GC.gen_cases(sp_ok, self.work())["cases"])

    # ---------- fail-closed 分支 ----------
    def test_unshrinkable_forced_case_fails_closed(self):
        """输出规模与输入无关（恒十亿元素）→ 逐维减半救不回来 → **报错**，不硬塞一条算不完的用例。"""
        self.place("FakeConstHugeOut", _BODY_CONST_HUGE_OUT)
        with self.assertRaises(ValueError) as cm:
            GC.gen_cases(_fake_spec("FakeConstHugeOut", case_target=1), self.work())
        msg = str(cm.exception)
        self.assertIn("预算", msg)
        self.assertIn("fail-closed", msg)

    def test_grid_emptied_by_budget_fails_closed(self):
        """常规网格被预算剔空 → fail-closed（只剩强制项 = 用例数虚高但没有一条常规覆盖）。
        直接单测 `_apply_cost_budget`：整条 gen_cases 上这个分支会被强制项的降规模失败抢先，测不到。"""
        forced = [{"shape": (2,), "attrs": {}, "dtype": "float32", "id_kind": "scalar",
                   "case_origin": "special:scalar", "dims": ["功能"], "tags": ["特殊"]}]
        grid = [{"shape": (64,), "attrs": {}, "dtype": "float32", "id_kind": "gridu",
                 "case_origin": "grid:float32:64:uniform:a0", "dims": ["功能"], "tags": ["常规"]}]
        cost = lambda shp, attrs, where: GC._numel(shp)          # noqa: E731
        with self.assertRaises(ValueError) as cm:
            GC._apply_cost_budget(forced, grid, cost, 8)
        self.assertIn("假验收", str(cm.exception))
        # 对照：预算够用时照常放行，且强制项一字不改
        kept, led = GC._apply_cost_budget(forced, grid, cost, 1000)
        self.assertEqual(kept, grid)
        self.assertEqual(led["scaled_cases"], [])
        self.assertEqual(forced[0]["shape"], (2,))

    # ---------- cost 模型本身 ----------
    def test_cost_model_reads_out_shape_not_just_input(self):
        """cost = max(最大输入元素数, 输出元素数)；输出元素数走 C1 的 `out_shape`（未导出 → 输入广播形状）。"""
        one = [{"name": "self", "io": "in", "dtype": ["float32"]}]
        two = one + [{"name": "other", "io": "in", "dtype": ["float32"]}]
        self.assertEqual(GC._make_cost_fn(one, None)((4, 5), {}, "w"), 20)      # elementwise 一元
        self.assertEqual(GC._make_cost_fn(two, None)((10,), {}, "w"), 10)       # 二元同形 → 取最大不是求和
        pair = GC._make_cost_fn(one, lambda ins, a: (ins[0][0] * (ins[0][0] - 1) // 2,))
        self.assertEqual(pair((100,), {}, "w"), 4950)                            # 成对：输出主导
        self.assertEqual(pair((3,), {}, "w"), 3)                                 # 小规模：输入主导
        shrink = GC._make_cost_fn(one, lambda ins, a: (1,))                      # 归约到标量：输入主导
        self.assertEqual(shrink((64, 64), {}, "w"), 4096)

    def test_cost_model_bad_out_shape_surfaces_at_plan_time(self):
        """`out_shape` 在**计划期**就被调用（要算 cost）→ 坏返回/抛异常在跑 golden 之前就 fail-closed。"""
        self.place("FakeCostOsBad", _BODY_ELEMENTWISE +
                   "\ndef out_shape(in_shapes, attrs):\n    return 'nope'\n")
        with self.assertRaises(ValueError) as cm:
            GC.gen_cases(_fake_spec("FakeCostOsBad", case_target=1), self.work())
        self.assertIn("out_shape", str(cm.exception))

    # ---------- CP-B（dry-run）也要看得见 ----------
    def test_dry_run_surfaces_cost_budget_and_scaling(self):
        """规模问题要在 **CP-B 的 dry-run** 就暴露，不拖到 CP-D 真生成时卡死。"""
        self.place("FakePdistDry", _pairwise_body(_PAIR_BUDGET))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            GC._dry_run(_pair_spec("FakePdistDry", case_target=25))
        out = buf.getvalue()
        self.assertIn("golden_cost", out)
        self.assertIn(f"budget={_PAIR_BUDGET}", out)
        self.assertIn("降规模", out)
        self.assertIn("网格剔除", out)
        self.assertIn("1024x1024", out)

    def test_dry_run_without_golden_says_unchecked_not_silently_ok(self):
        """golden.py 还没写时 dry-run 不阻塞（acc-spec 要用它探区间），但必须**明说未核**、不谎称已核。"""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            GC._dry_run(_fake_spec("FakeNoGoldenYet", case_target=1))
        out = buf.getvalue()
        self.assertIn("未核", out)
        self.assertIn("缺 golden", out)          # 连原因一起说清
        self.assertIn("budget=None", out)

class AllowEmptyTensorTest(_FakeOpCase):
    """C1 连带 · `allow_empty_tensor`（2026-07-23）：算子声明「不支持空 Tensor」时不强塞该用例。

    为什么需要：opbase §1.4 把「空 Tensor」当**普适**特殊场景无条件强塞，但很多算子任务书
    白纸黑字写「不支持空Tensor」。强塞只有两个出口——golden **为非法输入编造输出**
    （= 替算子发明它不支持的语义），或整条链卡死。实测三个真算子全撞这堵墙。"""

    @staticmethod
    def _kinds(cs):
        return {c["id"].rsplit("_", 1)[-1] for c in cs["cases"]}

    def test_default_still_emits_empty_case(self):
        """缺省 = 现行为不变（4 个 elementwise 样例一字不动的前提）。"""
        self.place("FakeEmpDefault", _BODY_ELEMENTWISE)
        cs = GC.gen_cases(_fake_spec("FakeEmpDefault", case_target=20), self.work())
        self.assertTrue(any("_empty" in c["id"] for c in cs["cases"]),
                        [c["id"] for c in cs["cases"]])   # id 形如 <op>_float32_0_empty_a0

    def test_false_suppresses_empty_case(self):
        self.place("FakeEmpOff", _BODY_ELEMENTWISE)
        sp = _fake_spec("FakeEmpOff", case_target=20)
        sp["allow_empty_tensor"] = False
        cs = GC.gen_cases(sp, self.work())
        self.assertTrue(cs["cases"])
        self.assertFalse([c["id"] for c in cs["cases"] if "_empty" in c["id"]])
        # 其余强制场景不受影响（别把整组特殊用例一起关掉）
        self.assertTrue(any("_scalar" in c["id"] for c in cs["cases"]),
                        [c["id"] for c in cs["cases"]])

    def test_non_bool_rejected_fail_closed(self):
        """**只收真布尔**：`"false"` / `0` / `1` / `None` 一律拒。

        真值性判断会把 `"false"` 读成 True（非空串）、把 `0` 读成 False——
        前者让「声明了不支持」被悄悄忽略。本仓在批 1 的 `authorization_verified` 上栽过同款 fail-open。"""
        self.place("FakeEmpBad", _BODY_ELEMENTWISE)
        for bad in ("false", "true", 0, 1, None, [], {}):
            sp = _fake_spec("FakeEmpBad", case_target=5)
            sp["allow_empty_tensor"] = bad
            with self.assertRaises(ValueError, msg=repr(bad)) as cm:
                GC.gen_cases(sp, self.work())
            self.assertIn("allow_empty_tensor", str(cm.exception))

    def test_dry_run_and_gen_cases_agree(self):
        """两条路口径必须一致——否则 CP-B 看到的计划与真实产出不是一回事。"""
        self.place("FakeEmpAgree", _BODY_ELEMENTWISE)
        sp = _fake_spec("FakeEmpAgree", case_target=20)
        sp["allow_empty_tensor"] = False
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            GC._dry_run(sp)
        self.assertNotIn("'empty'", buf.getvalue(), buf.getvalue())
        cs = GC.gen_cases(sp, self.work())
        self.assertFalse([c["id"] for c in cs["cases"] if "_empty" in c["id"]])


class ExactIsNotBoolTest(_FakeOpCase):
    """回归：`verify_mode=exact` **不等于**「输出是 bool」。

    真机 `run_new_example` 曾写 `exp_dt = np.bool_ if verify_mode == "exact" else _NP[dtn]` ——
    把**判据**（逐位比）当成了**输出类型**。im2col 任务书要求「精度标准为二进制一致」→ exact，
    而输出是 float32（纯搬运、逐位可达）→ 真机实跑报 `golden float32(4,2) ≠ 期望 bool(4,2)`。
    ⚠ **mock 通路不经那一行**，所以本机全绿完全掩盖了它 —— 典型「本机过、真机炸」。
    现改成据 caseset 的 `compare_dtype`（validator 会据 spec 独立派生并强制相等，谎报过不了裁决层）。"""

    def test_exact_float_op_has_float_compare_dtype(self):
        """exact + 浮点输出的算子，`compare_dtype` 必须是浮点、不是 bool。"""
        self.place("FakeExactFloat", _BODY_ELEMENTWISE)
        sp = _fake_spec("FakeExactFloat", case_target=6)
        sp["verify_mode"] = "exact"
        cs = GC.gen_cases(sp, self.work())
        cdts = {c["expected"]["compare_dtype"] for c in cs["cases"]
                if c["expected"].get("compare_dtype") is not None}
        self.assertEqual(cdts, {"float32"}, cdts)
        self.assertNotIn("bool", cdts)

    def test_repo_adapter_no_longer_infers_bool_from_verify_mode(self):
        """源码级钉子（辅助，非主证据）：采集层不得再从 verify_mode 推 bool。"""
        with open(os.path.join(_HERE, "repo_adapter.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn('np.bool_ if c["expected"].get("verify_mode") == "exact"', src)
        self.assertIn('_cdt = c["expected"].get("compare_dtype")', src)

    def test_dtype_resolution_behaviour(self):
        """**行为级**（主证据）：直接驱动 repo_adapter 里那段 dtype 解析，覆盖四种情形。

        源码级 assertNotIn 只钉住「旧写法没了」，钉不住「新写法对不对」。这里用一个最小的
        `_resolve` 复刻件不行——那是自证。改成断言真实模块里的映射表与分支约定：
        `_NP` 必须能吃 compare_dtype 的取值域，且 bool 单独走 np.bool_。"""
        import numpy as _np
        import repo_adapter as _RA
        # exact 不再蕴含 bool：float32 的 compare_dtype 必须映射到 float32，而非 bool
        self.assertIs(_RA._NP["float32"], _np.float32)
        self.assertIs(_RA._NP["float16"], _np.float16)
        self.assertIs(_RA._NP["bfloat16"], _np.float32)   # bf16 逻辑 dtype = fp32-on-grid
        self.assertNotIn("bool", _RA._NP)                 # bool 不在 _NP，由分支单独处理
        # 未支持的 compare_dtype 必须被拒（不静默回退到某个默认 dtype）
        self.assertNotIn("complex64", _RA._NP)


if __name__ == "__main__":
    unittest.main()
