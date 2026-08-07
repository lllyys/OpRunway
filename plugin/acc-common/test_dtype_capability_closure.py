"""dtype 能力闭环单测（2026-08-06 · uint32 + complex64）。

跑: cd plugin/acc-common && python3 -m pytest -q test_dtype_capability_closure.py

这批测试守的是**一条纪律**，不是两个 dtype：
「一个 dtype 要么四层齐备（生成 / 收发 / 阈值 / 复算），要么明着挡住；
 **不许出现某一层说支持、另一层当场拒**」——那正是 `precision_policy` 里
complex 长注释反复警告的「声明与实现不一致比缺能力更坏」。

四层是：
  ① 生成层 `gen_cases._NATIVE`                        —— 造得出输入 / 落得了盘
  ② 收发层 `repo_adapter.SUPPORTED_NP_BY_FORM[form]`  —— 真机这条通路收发得了
     （其真机兑现处是 `cpp_extension_driver._TORCH_DTYPES`，两张表必须同步）
  ③ 阈值层 `precision_policy.threshold_for`           —— 出得了 policy
  ④ 复算层 `precision_policy.compute_metrics`         —— 算得出 metrics

⚠ 本文件**不**测「哪条通路能出验收裁决」：那是准入表 `run_workflow._ACCEPTANCE_RUNNER_FORMS`
   的事，与能力表是两个问题（AGENTS.md §4.1）。本轮一个字没动它，见
   `AdmissionTableUntouchedTest`。
"""
import os
import shutil
import tempfile
import unittest

import numpy as np

import cpp_extension_driver as CED
import gen_cases as GC
import precision_policy as P
import repo_adapter as RA
import _golden_fixture as _gf
import _spec_fixture as SF

setUpModule = _gf.install
tearDownModule = _gf.uninstall

_HERE = os.path.dirname(os.path.abspath(__file__))
_SIGN_FX = os.path.join(_HERE, "test_fixtures", "sign_dtype.spec.json")

#: 本轮新接入的两个 dtype。列在一处，省得三张表各写一份字面量。
_NEW_DTYPES = ("uint32", "complex64")


# ============================================================ ① 四层对账 =======
class FourLayerClosureTest(unittest.TestCase):
    """新 dtype 必须**每一层**都在。缺任何一层就是本文件存在的那个病。"""

    def test_generation_layer(self):
        for dt in _NEW_DTYPES:
            self.assertIn(dt, GC._NATIVE, dt)
            self.assertIn(dt, GC.generatable_dtypes(), dt)

    def test_transport_layer_cpp_extension(self):
        table = RA.supported_np("cpp_extension")
        for dt in _NEW_DTYPES:
            self.assertIn(dt, table, dt)

    def test_driver_table_matches_capability_table(self):
        """收发能力表 ↔ 驱动 dtype 表**双向**相等。

        单向包含不够：能力表多一条 = 真机当场拒（声明不兑现）；驱动表多一条 = 一个
        没进过能力门的 dtype 能被真机悄悄收下（绕过生成期的双层校验）。两个方向都是病。"""
        self.assertEqual(set(RA.supported_np("cpp_extension")), set(CED._TORCH_DTYPES))

    def test_threshold_layer(self):
        # uint32 是整型 → §1.1 有效标准恒 EXACT，policy 与 dtype 无关；仍须能取到 AOT 那一行。
        self.assertEqual(P.effective_standard("ascendoptest_default", "uint32"), P.EXACT)
        self.assertEqual(P.threshold_for("ascendoptest_default", "uint32")["kind"],
                         "ascendoptest_default")
        # complex64 两条标准都要出得了 policy，且**逐字等于 float32 的那一份**
        # （用户 2026-08-06：复数沿用 float32，容差不单列）。
        self.assertEqual(P.threshold_for("ascendoptest_default", "complex64")["kind"],
                         "ascendoptest_default")
        ta = P.threshold_for("torch_allclose", "complex64")
        self.assertEqual((ta["rtol"], ta["atol"]), (2 ** -13, 1e-3))     # = float32 那一行
        for std in ("ascendoptest_default", "torch_allclose"):
            self.assertEqual(P.threshold_for(std, "complex64"),
                             P.threshold_for(std, "float32"), std)

    def test_compute_layer(self):
        for dt in _NEW_DTYPES:
            self.assertIn(dt, P.SUPPORTED_COMPUTE_DTYPES, dt)
            arr = np.array([1, 2, 3], dtype=getattr(np, dt))
            m = P.compute_metrics(arr, arr, P.threshold_for(P.EXACT, dt))
            self.assertEqual(m["exact_mismatch"], 0, dt)

    def test_other_forms_not_silently_widened(self):
        """`cpp` / `aclnn_py` **没有**跟着放开——本轮只实测了 cpp_extension 那条载体路径。

        这条不是洁癖：能力表的全部价值就是「写在里面的都实测过」。顺手给另外两条通路
        也加上，等于用一次实测给三条通路背书。"""
        for form in ("cpp", "aclnn_py"):
            table = RA.supported_np(form)
            for dt in _NEW_DTYPES:
                self.assertNotIn(dt, table, f"{form}:{dt}")

    def test_complex128_still_fail_closed_everywhere(self):
        """complex128 四层**一层都没进**——缺的是真机实证，不是实现。别顺手补齐它。"""
        self.assertNotIn("complex128", GC._NATIVE)
        self.assertNotIn("complex128", RA.supported_np("cpp_extension"))
        self.assertNotIn("complex128", CED._TORCH_DTYPES)
        self.assertNotIn("complex128", P.SUPPORTED_COMPUTE_DTYPES)
        for std in ("ascendoptest_default", "torch_allclose"):
            with self.assertRaises(ValueError, msg=std):
                P.threshold_for(std, "complex128")


class AdmissionTableUntouchedTest(unittest.TestCase):
    """能力表 ≠ 准入表（AGENTS.md §4.1）：本轮扩的是前者，后者一个字没动。"""

    def test_acceptance_runner_forms_unchanged(self):
        import run_workflow as RW
        self.assertEqual(RW._ACCEPTANCE_RUNNER_FORMS, frozenset({"cpp_extension"}))


# ====================================================== ② 生成层：真造得出来 ===
class GenerationLayerTest(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(20260806)

    def test_uint32_varied_is_unsigned_grid(self):
        x = GC._make_varied(self.rng, (6, 6), "uint32")
        self.assertEqual(x.dtype, np.uint32)
        self.assertTrue((x <= 100).all())                     # 整型值域夹在 [.., min(100,max)]
        f = x.reshape(-1)
        # 无符号 dtype 没有负数这一支 → 锚点是 (0,1,3) 而非 (-2,0,3)。
        self.assertEqual([int(f[0]), int(f[1]), int(f[2])], [0, 1, 3])

    def test_uint32_pairint_no_overflow(self):
        ref = GC._make_varied(self.rng, (8,), "uint32")
        b = GC._make_pairint((8,), "uint32", ref)
        self.assertEqual(b.dtype, np.uint32)
        np.testing.assert_array_equal(b[:4], ref[:4])         # 前半相等
        np.testing.assert_array_equal(b[4:], ref[4:] + 5)     # 后半 +5，无回绕

    def test_complex64_varied_imag_is_not_degenerate(self):
        """⭐ 复数造数的核心不变式：**虚部不能恒 0**。

        `float 造完 astype(complex64)` 会产出虚部全 0 的数组——用例跑得通、覆盖账记着
        「complex64 已覆盖」，而虚部通路一次都没被压过。这条就是钉死那种假覆盖。"""
        x = GC._make_varied(self.rng, (8, 8), "complex64")
        self.assertEqual(x.dtype, np.complex64)
        self.assertGreater(int(np.count_nonzero(x.imag)), 0)
        # 不止「有非零」，还要**大部分**非零（防「只有锚点位有虚部」这种擦边实现）
        self.assertGreater(int(np.count_nonzero(x.imag)), x.size // 2)
        self.assertGreater(int(np.count_nonzero(x.real)), x.size // 2)

    def test_complex64_varied_covers_component_sign_combos(self):
        """实/虚分量的符号组合要真出现（复数没有序，能钉的就是分量符号）。"""
        f = GC._make_varied(self.rng, (8, 8), "complex64").reshape(-1)
        combos = {(int(np.sign(v.real)), int(np.sign(v.imag))) for v in f[:3]}
        self.assertEqual(combos, {(-1, 0), (0, 1), (1, -1)})

    def test_complex64_pairhalf_changes_both_components(self):
        """二元 exact 类第二输入：后半必须**两个分量都变**（丢虚部就是假覆盖）。"""
        a = GC._make_varied(self.rng, (8,), "complex64")
        b = GC._make_pairhalf((8,), "complex64", a)
        self.assertEqual(b.dtype, np.complex64)
        np.testing.assert_array_equal(b[:4], a[:4])
        self.assertTrue((b[4:].real != a[4:].real).all())
        self.assertTrue((b[4:].imag != a[4:].imag).all())

    def test_complex_unsupported_generators_fail_closed(self):
        """口径定不下来的几处**明着挡住**，不许悄悄产一份猜出来的数据。"""
        with self.assertRaises(ValueError):                   # §1.4 非有限特殊值
            GC._build_value_special(self.rng, 1, (4, 4), "complex64", "inf")
        with self.assertRaises(ValueError):                   # nan_pair
            GC._make_nanpair(self.rng, (4, 4), "complex64", {})
        with self.assertRaises(ValueError):                   # 跨容差边界的 pairfar
            GC._make_pairfar(self.rng, (4,), "complex64",
                             GC._make_varied(self.rng, (4,), "complex64"), {"rtol": 1e-3})
        for profile in ("nan", "tie"):                        # value_profile
            with self.assertRaises(ValueError, msg=profile):
                GC._make_value_profile(self.rng, (4, 4), "complex64", profile)

    def test_storage_roundtrip(self):
        """落盘/读回：native 路径不做值 cast，round-trip 逐位还原。"""
        for dt in _NEW_DTYPES:
            logical = GC._make_varied(self.rng, (4, 4), dt)
            meta = {"dtype": dt}
            phys = RA.materialize_input(logical, meta)
            self.assertEqual(str(phys.dtype), dt, dt)
            np.testing.assert_array_equal(RA.readback_output(phys, meta), logical)


# ================================================ ③ 端到端：真产出一份 caseset ==
_IDENTITY_GOLDEN = "def golden_fn(inputs, attrs):\n    return inputs[0].copy()\n"


class EndToEndCasesetTest(unittest.TestCase):
    """用一个**纯搬运**假算子（golden = 原样返回）实跑 gen_cases，证三种 dtype 一起产得出用例。

    选纯搬运是因为它对 dtype 无偏见（不做算术 → 任何 dtype 的 golden 都成立），
    正好把「能不能造/能不能判」这件事与「某个算子的语义」解耦。op-中立，非按算子身份。"""

    OP = "DtypeClosureProbe"
    DTYPES = ["float32", "uint32", "complex64"]

    @classmethod
    def setUpClass(cls):
        cls.ops_root = os.path.realpath(tempfile.mkdtemp(prefix="dtcl_ops_"))
        cls.work = tempfile.mkdtemp(prefix="dtcl_work_")
        cls.old_ops_dir = os.environ.get("OPRUNWAY_OPS_DIR")
        try:
            spec = SF.load(_SIGN_FX)
            spec["op"] = cls.OP
            spec["runner_form"] = "cpp_extension"      # uint32/complex64 只在这条通路实测过
            # cpp_extension 要求 spec 显式声明调用形态（不许下游兜默认值）。本假算子单输入单输出、
            # 无 attr → 无条件变体一条即可。
            spec["call_variants"] = [{"when": {"always": True}, "symbol": cls.OP,
                                      "active_attrs": [], "active_outputs": ["out"]}]
            for p in spec["params"]:
                p["dtype"] = list(cls.DTYPES)
            _gf.place_golden(cls.ops_root, cls.OP, body=_IDENTITY_GOLDEN)
            os.environ["OPRUNWAY_OPS_DIR"] = cls.ops_root
            cls.spec = spec
            cls.cs = GC.gen_cases(spec, cls.work)
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "old_ops_dir", "__unset__") != "__unset__":
            if cls.old_ops_dir is None:
                os.environ.pop("OPRUNWAY_OPS_DIR", None)
            else:
                os.environ["OPRUNWAY_OPS_DIR"] = cls.old_ops_dir
            cls.old_ops_dir = "__unset__"
        for attr in ("ops_root", "work"):
            d = getattr(cls, attr, None)
            if d:
                shutil.rmtree(d, ignore_errors=True)
                setattr(cls, attr, None)

    def _by_dtype(self, dt):
        return [c for c in self.cs["cases"] if c["inputs"][0]["dtype"] == dt]

    def _graded(self, dt):
        """该 dtype 的**带精度判据**的 case（排除空 Tensor 那条——它只挂功能维，standard='na'）。"""
        out = [c for c in self._by_dtype(dt) if c["expected"].get("standard") != "na"]
        self.assertTrue(out, dt)
        return out

    def test_every_dtype_actually_emitted_cases(self):
        """声明了三种 dtype 就得三种都真有用例——「声明覆盖 ≠ 形成覆盖」。"""
        for dt in self.DTYPES:
            self.assertTrue(self._by_dtype(dt), dt)

    def test_uint32_cases_use_exact_standard(self):
        for c in self._graded("uint32"):
            self.assertEqual(c["expected"]["standard"], P.EXACT)
            self.assertEqual(c["expected"]["compare"], "exact_equal")

    def test_complex64_cases_carry_complex_policy_and_goldens(self):
        cases = self._graded("complex64")
        for c in cases:
            self.assertEqual(c["expected"]["compare_dtype"], "complex64")
            self.assertEqual(c["expected"]["policy"]["kind"], "ascendoptest_default")
        # golden 真的是复数、且虚部非退化（至少一条非空用例上成立）
        nonempty = [c for c in cases if int(np.prod(c["expected"]["out_shape"])) >= 8]
        self.assertTrue(nonempty)
        g = np.load(os.path.join(self.work, nonempty[0]["expected"]["golden_path"]))
        self.assertEqual(g.dtype, np.complex64)
        self.assertGreater(int(np.count_nonzero(g.imag)), 0)

    def test_complex64_gets_no_nonfinite_special_cases(self):
        """§1.4 非有限特殊值对复数是**声明式收窄**：一条都不产（float32 那边照产，对照组）。"""
        def kinds(dt):
            return {c["expected"]["case_origin"] for c in self._by_dtype(dt)
                    if c["expected"]["case_origin"].startswith("special:")}
        self.assertTrue({"special:inf", "special:ninf", "special:nan"} <= kinds("float32"))
        self.assertFalse(kinds("complex64") & {"special:inf", "special:ninf", "special:nan"})
        self.assertFalse(kinds("uint32") & {"special:inf", "special:ninf", "special:nan"})

    def test_metrics_recomputable_for_every_case(self):
        """⭐ 闭环那一环：每条 case 的 golden 都要能被 `compute_metrics` 真算出 metrics。

        「能生成、算不出来」正是 finding #9 记的那种病——生成层放行了一个复算层挡着的 dtype，
        问题要到真机跑完回来比对时才炸。这里当场就问一遍。"""
        for c in self.cs["cases"]:
            exp = c["expected"]
            g = np.load(os.path.join(self.work, exp["golden_path"]))
            if g.size == 0:
                continue                                  # 空 Tensor 用例只挂功能维
            m = P.compute_metrics(g, g, exp["policy"])    # 自比 → 任何口径下都必须零误差
            self.assertEqual(m.get("bad_count", m.get("exact_mismatch", m.get("mismatch"))), 0,
                             f"{c['id']} {exp['policy']['kind']}")


# ============================== ④ 复数比对口径（统一：实虚分量各按 float32 判）==
def _bad(metrics):
    """从 metrics 里取「不合格元素数」——三档的字段名不同，判据形状相同。"""
    for k in ("bad_count", "mismatch", "exact_mismatch"):
        if k in metrics:
            return metrics[k]
    raise AssertionError(f"metrics 没有不合格计数字段：{sorted(metrics)}")


# ---- 独立参考实现（**手写一份，刻意不 import 被测函数**）----
# 用途：证明「复数 = 该 policy 的 float32 规则逐分量跑两遍」这件事**在算法层面**成立。
# ⚠ 为什么不能只拿生产的 float32 路径当 oracle：统一之后复数与实数调的是**同一份**代码
#   （`_aot_valid_float` / `_allclose_close_mask`），改坏那一份两边会**一起**变，差分照样绿。
#   所以这里另写一份独立参考；下面还有一条用生产 float32 路径做的对照（那条钉的是「同一段代码」）。
def _ref_replace_inf(a):
    a = np.asarray(a).copy()
    m = np.finfo(a.dtype).max                      # 按**原生分量 dtype**（complex64 → float32）
    a[np.isposinf(a)] = m
    a[np.isneginf(a)] = -m
    return a


def _ref_aot_valid(o, g, tol, eps):
    """AscendOpTest `compare_default` 的浮点支（本仓 float32 走的那一条）。"""
    o64 = _ref_replace_inf(o).astype(np.float64)
    g64 = _ref_replace_inf(g).astype(np.float64)
    diff = np.abs(o64 - g64)
    maxmin = np.maximum(np.abs(g64), np.abs(o64)) + eps
    with np.errstate(invalid="ignore"):
        rel = diff / maxmin
    valid = np.where(np.abs(g64) >= 1, rel <= tol, diff <= tol)      # where，不是 or
    return valid | (np.isnan(o64) & np.isnan(g64))


def _ref_allclose_valid(o, g, rtol, atol, equal_nan):
    """torch_allclose 的实数四象限（本仓 float32 走的那一条）。"""
    o64 = np.asarray(o).astype(np.float64)
    g64 = np.asarray(g).astype(np.float64)
    with np.errstate(invalid="ignore"):          # |inf-inf| 与 rtol=0 时的 0*inf，都只落在非有限位
        diff = np.abs(o64 - g64)
        close = (np.isfinite(o64) & np.isfinite(g64)) & (diff <= atol + rtol * np.abs(g64))
    close = close | (np.isinf(o64) & np.isinf(g64) & (np.signbit(o64) == np.signbit(g64)))
    if equal_nan:
        close = close | (np.isnan(o64) & np.isnan(g64))
    return close


def _ref_exact_valid(o, g):
    o = np.asarray(o)
    g = np.asarray(g)
    return (o == g) | (np.isnan(o) & np.isnan(g))


#: 判别力挑过的分量取值：0.99/1.79 那一对专治「`where` 被写成 `or`」；
#: ±inf 压 `_replace_inf` 的分量级 finfo；nan 压 both-NaN 的**逐分量**放行支。
_PARTS = [0.0, 0.05, 0.5, 0.99, 1.0, 1.79, 2.0, -3.0, np.nan, np.inf, -np.inf]


def _complex_grid():
    return np.array([complex(r, i) for r in _PARTS for i in _PARTS], dtype=np.complex64)


class ComplexMetricsSemanticsTest(unittest.TestCase):
    """复数的**判别性**用例——不是「能跑通」，是「口径确实是『实虚分量各按 float32 判』」。

    口径来源：用户 2026-08-06「complex64 的标准就沿用 float32 的，虚部和实部都沿用 float32 的标准」。
    因此本类里**不再有**「三档口径互不相同」那类断言——那些的前提已经没了（见文件末的记账）。
    现在每一档复数走的都是它自己的 float32 规则跑两遍，容差也是 float32 那一行。

    🔴 **本类同时是那条已知差异的见证**：`torch_allclose` 因此**与 torch 本身不一致**
    （`torch.isclose` 对复数用**模长**）。见 `test_torch_allclose_is_per_component_not_magnitude`。
    """

    def test_torch_allclose_is_per_component_not_magnitude(self):
        """🔴 **口径 = 分量各判，不是模长 —— 并且这正是与 torch 的已知差异所在。**

        判别构造直接取自上一轮真机差分探针（a3 容器 `oprunway_prov`，torch 2.10.0）：
          · `o=0` vs `g=0.8+0.8j`、rtol=0/atol=1：模长 1.131 > 1 → **torch 判 not close**；
            分量各判 0.8<=1 且 0.8<=1 → 本实现判 **close**（mismatch=0）。
          · `g=0.001+10j` / `o=0.05+10j`、rtol=0.1/atol=0：模长容差 ≈1.0（**torch 判 close**），
            实部容差只有 rtol*|g.real| = 1e-4，实部差 0.049 → 本实现判 **不 close**（mismatch=1）。
        两条方向相反，合起来钉死「既不是模长、也不是恰好等价」。
        ⚠ 这两条断言就是那条差异的可执行记录：24576 对差分实测里 16 处不一致。
          **有意为之**（用户定的统一口径），别为了「对齐 torch」把它改回模长。"""
        pol = {"kind": P.TORCH_ALLCLOSE, "rtol": 0.0, "atol": 1.0, "equal_nan": True}
        o = np.array([0 + 0j], dtype=np.complex64)
        g = np.array([0.8 + 0.8j], dtype=np.complex64)
        self.assertEqual(P.compute_metrics(o, g, pol)["mismatch"], 0)
        pol_rel = {"kind": P.TORCH_ALLCLOSE, "rtol": 0.1, "atol": 0.0, "equal_nan": True}
        g2 = np.array([0.001 + 10j], dtype=np.complex64)
        o2 = np.array([0.05 + 10j], dtype=np.complex64)
        self.assertEqual(P.compute_metrics(o2, g2, pol_rel)["mismatch"], 1)

    def test_torch_allclose_complex_inf_and_nan(self):
        """inf/NaN 也不另立规则：`_allclose_close_mask` 的四象限在**每个分量上**各跑一遍。"""
        pol = {"kind": P.TORCH_ALLCLOSE, "rtol": 0.0, "atol": 1e-3, "equal_nan": True}
        inf = complex(np.inf, 0.0)
        o = np.array([inf, inf, complex(np.nan, 1.0)], dtype=np.complex64)
        g = np.array([inf, complex(-np.inf, 0.0), complex(np.nan, 1.0)], dtype=np.complex64)
        # 实部：同号 inf 相等 / 异号 inf 失配 / both-NaN 按 equal_nan 相等（虚部三例都相等）
        self.assertEqual(P.compute_metrics(o, g, pol)["mismatch"], 1)
        pol_no_nan = dict(pol, equal_nan=False)
        self.assertEqual(P.compute_metrics(o, g, pol_no_nan)["mismatch"], 2)

    def test_ascendoptest_uses_per_component_not_magnitude(self):
        """AOT 档同样是实虚**分量各判**（= 它自己的 float32 规则跑两遍）。

        判别构造：tol=0.1，golden=0.5+0.5j（两分量绝对值都 <1 → 走绝对误差）；
        actual 每个分量各差 0.08 → 分量各判**过**（0.08 <= 0.1），模长差 0.113 > 0.1 会**不过**。
        判过 = 口径确实是分量各判。"""
        pol = P.threshold_for("ascendoptest_default", "complex64")
        pol = dict(pol, tolerance=0.1, error_rate=0.0)
        g = np.array([0.5 + 0.5j], dtype=np.complex64)
        o = np.array([0.58 + 0.58j], dtype=np.complex64)
        self.assertEqual(P.compute_metrics(o, g, pol)["bad_count"], 0)
        # 单分量超界 → 该元素整体不合格（实部合格且虚部合格才算过）
        o2 = np.array([0.5 + 0.7j], dtype=np.complex64)
        self.assertEqual(P.compute_metrics(o2, g, pol)["bad_count"], 1)

    def test_every_kind_uses_the_same_complex_algorithm(self):
        """⭐ 口径**单一**：三档在同一份数据上必须给出同一个「分量各判」的答案形状。

        这条替掉了旧的「三档互不相同」那组断言（前提已随统一口径消失）。构造一个
        **模长过、分量不过**的元素：`g=0+0j`、`o=0.6+0.6j`、容差 0.8。
          · 模长 0.849 > 0.8 → 若谁还按模长判，会判**不过**；
          · 分量各判 0.6 <= 0.8 → 三档都必须判**过**。
        再把虚部单独推出界（`o2=0.6+0.9j`）→ 三档都必须判**不过**。"""
        g = np.array([0 + 0j], dtype=np.complex64)
        ok = np.array([0.6 + 0.6j], dtype=np.complex64)
        bad = np.array([0.6 + 0.9j], dtype=np.complex64)
        pols = (dict(P.threshold_for("ascendoptest_default", "complex64"),
                     tolerance=0.8, error_rate=0.0),
                {"kind": P.TORCH_ALLCLOSE, "rtol": 0.0, "atol": 0.8, "equal_nan": True})
        for pol in pols:
            k = pol["kind"]
            self.assertEqual(_bad(P.compute_metrics(ok, g, pol)), 0, k)
            self.assertEqual(_bad(P.compute_metrics(bad, g, pol)), 1, k)

    def test_ascendoptest_complex_rel_abs_switch_per_component(self):
        """`|golden 分量| >= 1` 用相对误差、否则用绝对误差 —— **逐分量各判一次**（照抄参考实现）。"""
        pol = dict(P.threshold_for("ascendoptest_default", "complex64"),
                   tolerance=0.1, error_rate=0.0)
        # 实部 2.0（>=1 → 相对）差 0.15 → rel≈0.0698 过；虚部 0.5（<1 → 绝对）差 0.15 → 不过
        g = np.array([2.0 + 0.5j], dtype=np.complex64)
        o = np.array([2.15 + 0.65j], dtype=np.complex64)
        self.assertEqual(P.compute_metrics(o, g, pol)["bad_count"], 1)
        # 只动实部（相对误差内）→ 过
        o2 = np.array([2.15 + 0.5j], dtype=np.complex64)
        self.assertEqual(P.compute_metrics(o2, g, pol)["bad_count"], 0)

    def test_ascendoptest_complex_switch_is_where_not_or(self):
        """⭐ 那个开关必须是 `where(|g|>=1, rel, abs)`，**不是** `rel or abs`（二者只在一小段值域上不同）。

        mutation 实测逼出来的：把 `where` 写成 `rel_ok | atol_ok` 时，寻常取值和寻常容差都看不出差别，
        差分网格也照样绿。判别构造要求 `|g|<1`（→ 该走绝对误差）却又 `rel` 恰好过：
          tol=0.5、g.real=0.99（<1）、o.real=1.79 → diff=0.8 > tol（绝对**不过**），
          rel=0.8/1.79≈0.447 <= tol（相对**过**）。照参考实现该判**不合格**；写成 `or` 会判合格。
        """
        pol = dict(P.threshold_for("ascendoptest_default", "complex64"),
                   tolerance=0.5, error_rate=0.0)
        g = np.array([0.99 + 0j], dtype=np.complex64)
        o = np.array([1.79 + 0j], dtype=np.complex64)
        self.assertEqual(P.compute_metrics(o, g, pol)["bad_count"], 1)
        # 开关的另一侧：|g.real|>=1 时用相对误差 —— 绝对误差同样不过，但这次该判**合格**。
        g2 = np.array([1.2 + 0j], dtype=np.complex64)
        o2 = np.array([2.0 + 0j], dtype=np.complex64)
        self.assertEqual(P.compute_metrics(o2, g2, pol)["bad_count"], 0)

    def test_complex_is_float32_rule_applied_to_each_component(self):
        """⭐ 差分（**独立手写参考**）：一片值网格上，复数判定 == 该 policy 的 float32 规则
        分别作用于实部与虚部再取交。

        覆盖三档 × 多组容差；参考实现见文件上方 `_ref_*`（刻意不 import 被测函数，
        否则改坏共用实现两边一起变、差分恒绿）。
        ⚠ 值集与容差**按判别力挑过**：0.99/1.79 + tol=0.5 那一对专门把
          `where(|g|>=1, rel, abs)` 与 `rel or abs` 分开（mutation 实测逼出来的）；
          ±inf 压分量级 `_replace_inf`；nan 压**逐分量** both-NaN。"""
        vals = _complex_grid()
        aot = P.threshold_for("ascendoptest_default", "complex64")
        ta = P.threshold_for("torch_allclose", "complex64")
        cases = []
        for tol in (aot["tolerance"], 0.5):
            pol = dict(aot, tolerance=tol)
            cases.append((pol, lambda a, b, p=pol: _ref_aot_valid(a, b, p["tolerance"], p["eps"])))
        for rtol, atol, en in ((ta["rtol"], ta["atol"], True), (0.0, 1.0, True), (0.1, 0.0, False)):
            pol = {"kind": P.TORCH_ALLCLOSE, "rtol": rtol, "atol": atol, "equal_nan": en}
            cases.append((pol, lambda a, b, p=pol: _ref_allclose_valid(
                a, b, p["rtol"], p["atol"], p["equal_nan"])))
        cases.append((P.threshold_for(P.EXACT, "complex64"),
                      lambda a, b: _ref_exact_valid(a, b)))
        for shift in range(0, vals.size, 7):
            o = np.roll(vals, shift)
            for pol, ref in cases:
                valid = ref(o.real, vals.real) & ref(o.imag, vals.imag)
                self.assertEqual(_bad(P.compute_metrics(o, vals, pol)),
                                 int(np.count_nonzero(~valid)), f"{pol} shift={shift}")

    def test_complex_goes_through_the_same_code_as_real_float32(self):
        """⭐ 「沿用」不止是算法长得一样，而是**同一条生产路径**：把分量单独喂给
        `compute_metrics` 的 float32 路径，逐元素结论必须与复数路径一致。

        上一条用独立参考钉算法，这一条用生产的 float32 路径钉「复数分支没有偷偷分叉」
        —— 两条缺一不可（只有前者，复数分支自己抄一份写对了也过；只有后者，共用实现
        被改坏时两边一起错也过）。"""
        vals = _complex_grid()

        def f32_bad_flags(comp_o, comp_g, pol):
            return np.array([bool(_bad(P.compute_metrics(np.array([a], np.float32),
                                                        np.array([b], np.float32), pol)))
                             for a, b in zip(comp_o, comp_g)])

        for std in ("ascendoptest_default", "torch_allclose", P.EXACT):
            pol_c = P.threshold_for(std, "complex64")
            pol_f = P.threshold_for(std, "float32")
            self.assertEqual(pol_c, pol_f, std)        # 容差/常量逐字同源（不是碰巧相等）
            for shift in (1, 17, 60):
                o = np.roll(vals, shift)
                want = (f32_bad_flags(o.real, vals.real, pol_f)
                        | f32_bad_flags(o.imag, vals.imag, pol_f))
                self.assertEqual(_bad(P.compute_metrics(o, vals, pol_c)),
                                 int(np.count_nonzero(want)), f"{std} shift={shift}")

    def test_exact_complex_is_per_component_nan_tolerant(self):
        pol = P.threshold_for(P.EXACT, "complex64")
        nn = complex(np.nan, np.nan)
        o = np.array([nn, complex(np.nan, 1.0), complex(np.nan, 1.0)], dtype=np.complex64)
        g = np.array([nn, complex(np.nan, 1.0), complex(np.nan, 2.0)], dtype=np.complex64)
        # 前两个：分量逐一 NaN-容忍相等 → 不算失配（否则「原样搬运一个 NaN」会假 FAIL）
        # 第三个：实部同为 NaN 但虚部 1 vs 2 → **仍算失配**（float32 exact 逐分量跑两遍就是这个结果）
        self.assertEqual(P.compute_metrics(o, g, pol)["exact_mismatch"], 1)

    def test_ascendoptest_complex_both_nan_is_per_component(self):
        """🔴 both-NaN 放行支**逐分量各判**——这是与 AscendOpTest `compare_complex` 的有意偏离。

        参考实现取 `isnan(o) & isnan(g)` 于**复数整体**（任一分量 NaN 即 True），再 OR 进两个分量：
        于是 `nan+1j` vs `nan+9j` 的虚部差异会被实部的 NaN 一起放过（判合格）。
        「沿用 float32」意味着虚部那次判定是一次**独立的 float32 判定**，NaN 放行支自然也独立
        → 这里必须判**不合格**。⚠ 别以「对齐参考仓」为由改回整体取。"""
        pol = dict(P.threshold_for("ascendoptest_default", "complex64"), error_rate=0.0)
        o = np.array([complex(np.nan, 1.0)], dtype=np.complex64)
        g = np.array([complex(np.nan, 9.0)], dtype=np.complex64)
        self.assertEqual(P.compute_metrics(o, g, pol)["bad_count"], 1)
        # 对照：两个分量都是 NaN 对 → 逐分量都命中放行支 → 合格
        nn = np.array([complex(np.nan, np.nan)], dtype=np.complex64)
        self.assertEqual(P.compute_metrics(nn, nn, pol)["bad_count"], 0)

    def test_ascendoptest_complex_replaces_inf_per_component(self):
        """🔴 `±inf → ±finfo(float32).max` 也照 float32 那条做，且是**按分量**做。

        参考实现的 `replace_inf` 对复数是空操作，本仓有意偏离（分量是 float32 → 与真跑 float32
        同一件事）。可观测后果：同号 inf 对 inf 被换成同一个有限值 → diff=0 → 合格；
        而 `inf` vs 有限值仍是巨大误差 → 不合格。"""
        pol = dict(P.threshold_for("ascendoptest_default", "complex64"), error_rate=0.0)
        inf = np.array([complex(np.inf, -np.inf)], dtype=np.complex64)
        self.assertEqual(P.compute_metrics(inf, inf, pol)["bad_count"], 0)
        # 诊断量不再被 inf 污染成 inf（`_replace_inf` 之后 diff 有限）
        self.assertTrue(np.isfinite(P.compute_metrics(inf, inf, pol)["max_abs_err"]))
        far = np.array([complex(1.0, -np.inf)], dtype=np.complex64)
        self.assertEqual(P.compute_metrics(far, inf, pol)["bad_count"], 1)

    def test_mere_mare_and_index_consistency_stay_fail_closed(self):
        """两条对复数**没有口径**的判据必须挡住，不许靠 astype(float64) 丢虚部糊弄过去。"""
        c = np.array([1 + 2j], dtype=np.complex64)
        mm = dict(P.threshold_for("ecosystem_mere_mare", "float32"))
        with self.assertRaises(ValueError):
            P.compute_metrics(c, c, mm)
        with self.assertRaises(ValueError):                  # 取阈值那步本来也没有 complex 行
            P.threshold_for("ecosystem_mere_mare", "complex64")
        idx_pol = {"kind": P.INDEX_VALUE_CONSISTENCY, "value_rtol": 1e-3, "value_atol": 1e-3}
        idx = np.array([[0], [1]], dtype=np.int64)
        src = np.array([[1 + 1j, 2 + 2j], [3 + 3j, 4 + 4j]], dtype=np.complex64)
        with self.assertRaises(ValueError):
            P.compute_metrics(idx, idx, idx_pol,
                              gather_ctx={"source": src, "dim": 1, "keepdim": True})

    def test_complex_never_silently_loses_imaginary_part(self):
        """⭐ 总闸：实部相同、虚部差很多的两份数据，**任何**受支持的复数口径都不许判「零误差」。

        旧洞的形状就是这个——`astype(np.float64)` 把虚部扔了，于是 bad_count=0 假通过。"""
        g = np.array([1.0 + 0.0j, 2.0 + 0.0j], dtype=np.complex64)
        o = np.array([1.0 + 9.0j, 2.0 + 9.0j], dtype=np.complex64)
        for std in ("ascendoptest_default", "torch_allclose", P.EXACT):
            pol = P.threshold_for(std, "complex64")
            m = P.compute_metrics(o, g, pol)
            bad = m.get("bad_count", m.get("mismatch", m.get("exact_mismatch")))
            self.assertEqual(bad, 2, std)


if __name__ == "__main__":
    unittest.main()
