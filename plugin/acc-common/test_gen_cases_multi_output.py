"""多输出契约 + value_profile + Median golden 单测（WI-A3/A6/D1 · torch 对标 median 见证）。

跑: cd plugin/acc-common && python3 -m unittest test_gen_cases_multi_output -v
   （机制/向后兼容/value_profile/out_shape 全用 **numpy 假 golden 或纯函数**、**不需 torch**；
     真 Median golden.py 的 golden_fn 需 torch → skipUnless；在装了 torch 的机器/venv 上跑。）

覆盖（全部 op-中立、据 spec/caseset 字段驱动，**无算子名分支**）：
  · gen_cases 多输出契约：golden_fn 返回 tuple → expected.outputs[]（逐输出 golden_{k}.npy + out_shape + 判据契约）；
    全局 case 只出 value（outputs 长度 1）、by-dim 出 value+index（长度 2）——同算子两 arity 据 dim 是否 present；
  · 逐输出契约 == derive_output_contracts(spec) canonical（契约自检：spec↔caseset 一致，放宽即被逮）；
  · validator 多输出折叠端到端（value pass+index pass→pass；篡改→fail）；
  · value_profile：_make_value_profile nan/tie 产对 + spec 驱动产 nan/tie 用例；
  · **单输出向后兼容硬约束**：假单输出算子仍产 legacy expected（无 outputs 字段）；现有 4 算子 dry-run 无回归；
  · Median golden.py：out_shape 全局/按维/keepdim/负 dim/越界；golden_fn 双输出（skipUnless torch）。
"""
import importlib.util, json, os, shutil, tempfile, unittest
import numpy as np

import gen_cases as GC
import precision_policy as P
import validator as V
import _golden_fixture as _gf

_HERE = os.path.dirname(os.path.abspath(__file__))
_MEDIAN_GOLDEN = os.path.join(_HERE, "..", "samples", "golden", "Median", "golden.py")
_MEDIAN_SPEC = os.path.join(_HERE, "..", "samples", "specs", "median.spec.json")

_GOLDEN_ROOT = None


def setUpModule():
    global _GOLDEN_ROOT
    _GOLDEN_ROOT = _gf.install()


def tearDownModule():
    _gf.uninstall()


def _has_torch():
    try:
        import torch  # noqa: F401
        return True
    except Exception:
        return False


# ── numpy 假 median golden（不需 torch）：双输出，lower-middle 下中位（对齐 torch 偶数语义）。据字段分派 ──
_FAKE_MEDIAN_BODY = '''
def out_shape(in_shapes, attrs):
    shp = tuple(int(d) for d in in_shapes[0])
    dim = attrs.get("dim")
    if dim is None:
        return ()
    d = dim if dim >= 0 else dim + len(shp)
    if not (0 <= d < len(shp)):
        raise ValueError("fake median: dim 越界")
    if attrs.get("keepdim"):
        return shp[:d] + (1,) + shp[d + 1:]
    return shp[:d] + shp[d + 1:]

def golden_fn(inputs, attrs):
    x = np.asarray(inputs[0]); dim = attrs.get("dim")
    if dim is None:
        xs = np.sort(x, axis=None)
        return xs[(x.size - 1) // 2]
    d = dim if dim >= 0 else dim + x.ndim
    order = np.argsort(x, axis=d, kind="stable")
    mid = (x.shape[d] - 1) // 2
    vi = np.take(order, [mid], axis=d)          # 列表形式保 axis d（size 1）→ 形状稳定，避 scalar-take 的维度歧义
    vv = np.take_along_axis(x, vi, axis=d)
    if not attrs.get("keepdim"):
        vv = np.squeeze(vv, axis=d); vi = np.squeeze(vi, axis=d)
    return (vv, vi.astype(np.int64))                 # 不 ascontiguousarray：它会把 0-d 提成 (1,)（rank1 归约=标量）
'''

_FAKE_SINGLE_BODY = '''
def golden_fn(inputs, attrs):
    return np.negative(np.asarray(inputs[0]))
'''


# 字段驱动的调用变体表（与 median.spec.json 同形）：dim=null → 全局 API、只落地 values；
# dim 有值 → by-dim API、落地 values+indices。**输出集由此声明**，不再由 golden 返回几个反推。
_FAKE_VARIANTS = [
    {"when": {"attr": "dim", "is_null": True},
     "symbol": "FakeGlobal", "active_attrs": [], "active_outputs": ["values"]},
    {"when": {"attr": "dim", "is_null": False},
     "symbol": "FakeDim", "active_attrs": ["dim", "keepdim"], "active_outputs": ["values", "indices"]},
]


def _fake_median_spec(op="MedMulti", dtypes=("float32", "int32"),
                      dim_vals=(None, 0, -1), value_profiles=None, case_target=24,
                      call_variants=_FAKE_VARIANTS, ranks=(1, 2, 3), operator_class=None):
    # dim ∈ {null(全局), 0(first), -1(last)} 对任意 rank≥1 恒有效；middle=rank//2 需 per-rank 解析（scale 阶段）。
    matrix = [{"dim": d, "keepdim": False} for d in dim_vals] + [{"dim": 0, "keepdim": True}]
    prec = {"oracle": "torch", "standard": "torch_allclose", "tolerance_source": "dtype_table",
            "case_target": case_target}
    if value_profiles:
        prec["value_profiles"] = list(value_profiles)
    spec = {
        "op": op, "repo": "t", "runner_form": "cpp",
        "verify_mode": "numerical", "generalize": True,
        "allow_empty_tensor": False, "attr_matrix": matrix, "precision": prec,
        "params": [
            {"name": "self", "io": "in", "dtype": list(dtypes), "rank": list(ranks)},
            {"name": "dim", "io": "attr", "dtype": ["int64"], "default": None},
            {"name": "keepdim", "io": "attr", "dtype": ["bool"], "default": False},
            {"name": "values", "io": "out", "dtype": ["<from_input>"], "out_role": "value"},
            {"name": "indices", "io": "out", "dtype": ["int64"], "out_role": "index",
             "index_of": "values", "gather_from": "self"},   # finding #7：gather 源必由 spec 锚定
        ],
    }
    if operator_class is not None:      # OC：不传 = 整字段省略 = 现行为（向后兼容口径）
        spec["operator_class"] = operator_class
    if call_variants is not None:
        spec["call_variants"] = json.loads(json.dumps(call_variants))   # 深拷贝，测试间互不污染
    return spec


def _gen(spec, op_body, op=None):
    """place 假 golden + 跑 gen_cases，返回 (caseset, work_dir)。work_dir 由调用方 addCleanup 清。"""
    op = op or spec["op"]
    _gf.place_golden(_gf.root(), op, body=op_body)
    work = tempfile.mkdtemp(prefix=f"mo_{op}_")
    cs = GC.gen_cases(spec, work)
    return cs, work


def _load(work, rel):
    return np.load(os.path.join(work, rel))


class MultiOutputContractTest(unittest.TestCase):
    """gen_cases 多输出契约（numpy 假 median，不需 torch）。"""

    def setUp(self):
        self.spec = _fake_median_spec(op="MedMulti", dtypes=("float32", "int32"))
        self.cs, self.work = _gen(self.spec, _FAKE_MEDIAN_BODY)
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)

    def test_outputs_length_varies_global_vs_bydim(self):
        """全局(dim=None) 单输出、by-dim 双输出——同算子两 arity 据 dim 是否 present（据字段、非算子名）。"""
        lens = {len(c["expected"]["outputs"]) for c in self.cs["cases"]}
        self.assertEqual(lens, {1, 2}, lens)
        for c in self.cs["cases"]:
            n = len(c["expected"]["outputs"])
            self.assertEqual(n, 1 if c["attrs"].get("dim") is None else 2, c["id"])

    def test_per_output_roles_and_files(self):
        byd = next(c for c in self.cs["cases"] if len(c["expected"]["outputs"]) == 2)
        outs = byd["expected"]["outputs"]
        self.assertEqual([o["role"] for o in outs], ["value", "index"])
        v, idx = outs
        self.assertEqual(v["compare"], "torch_allclose")
        self.assertEqual(idx["compare"], "index_value_consistency")
        self.assertEqual(idx["compare_dtype"], "int64")
        self.assertEqual(idx["index_of"], "values")
        # 逐输出 golden_{k}.npy 落盘、dtype 对（value=输入 dtype、index=int64）
        av, ai = _load(self.work, v["golden_path"]), _load(self.work, idx["golden_path"])
        self.assertTrue(v["golden_path"].endswith("golden_0.npy"))
        self.assertTrue(idx["golden_path"].endswith("golden_1.npy"))
        self.assertEqual(ai.dtype, np.int64)
        self.assertEqual(list(av.shape), v["out_shape"])
        self.assertEqual(list(ai.shape), idx["out_shape"])

    def test_int_value_uses_exact(self):
        """int median 的 value 输出走 exact（effective_standard int→exact），据 dtype 字段、非算子名。"""
        c = next(c for c in self.cs["cases"]
                 if c["inputs"][0]["dtype"] == "int32" and len(c["expected"]["outputs"]) == 2)
        v = c["expected"]["outputs"][0]
        self.assertEqual(v["standard"], "exact")
        self.assertEqual(v["policy"]["kind"], "exact")

    def test_caseset_outputs_match_canonical(self):
        """契约自检：caseset 逐输出 standard/policy/tpid == derive_output_contracts(spec) canonical（放宽即被逮）。"""
        for c in self.cs["cases"]:
            in_dt = c["inputs"][0]["dtype"]
            cts = P.derive_output_contracts(self.spec, [("self", in_dt)], "torch_allclose", "dtype_table")
            for k, o in enumerate(c["expected"]["outputs"]):
                ct = cts[k]
                self.assertEqual(o["standard"], ct["standard"], (c["id"], k))
                self.assertEqual(o["policy"], ct["policy"], (c["id"], k))
                self.assertEqual(o["tolerance_policy_id"], ct["tolerance_policy_id"], (c["id"], k))

    def test_keepdim_out_shape(self):
        kd = next(c for c in self.cs["cases"]
                  if len(c["expected"]["outputs"]) == 2 and c["attrs"].get("keepdim"))
        in_shp = kd["inputs"][0]["shape"]
        d = kd["attrs"]["dim"]
        d = d if d >= 0 else d + len(in_shp)
        expect = in_shp[:d] + [1] + in_shp[d + 1:]
        self.assertEqual(kd["expected"]["outputs"][0]["out_shape"], expect, kd["id"])

    def test_validator_multi_output_roundtrip_pass(self):
        """据 caseset 造匹配 evidence（metrics 全 pass）→ validator 折叠 → 精度 pass；篡改 value → fail。"""
        byd = next(c for c in self.cs["cases"]
                   if len(c["expected"]["outputs"]) == 2 and "精度" in c["dims"])
        spec, caseset, ev = self._bundle(byd, value_mismatch=0, index_mismatch=0)
        v = V.validate(spec, caseset, ev)
        self.assertEqual(v["overall"]["verdict"], "pass", v)
        # 篡改：value mismatch>0 → fail
        spec, caseset, ev = self._bundle(byd, value_mismatch=1, index_mismatch=0)
        self.assertEqual(V.validate(spec, caseset, ev)["overall"]["verdict"], "fail")

    def _bundle(self, case, value_mismatch, index_mismatch):
        """把单个多输出 case 包成 (spec, caseset, evidence)：evidence 逐输出 metrics 据 policy.kind 造 mismatch。"""
        caseset = {"op": self.spec["op"], "cases": [case]}
        ev_outs = []
        for o in case["expected"]["outputs"]:
            mis = index_mismatch if o["role"] == "index" else value_mismatch
            key = "exact_mismatch" if o["policy"]["kind"] == "exact" else "mismatch"
            numel = int(np.prod(o["out_shape"])) or 1
            # name/index/threshold 一并带上（严重#1 修复后 evidence 逐输出身份 + digest 都要与 spec 派生对齐）
            ev_outs.append({"name": o["name"], "index": o.get("index"),
                            "role": o["role"], "standard": o["standard"],
                            "tolerance_policy_id": o["tolerance_policy_id"], "policy": o["policy"],
                            "threshold": o.get("threshold"),
                            "metrics": {key: mis, "numel": numel}})
        ev = {"op": self.spec["op"], "evidence": [
            {"case_id": case["id"], "status": "ok", "precision": {"outputs": ev_outs}}]}
        return self.spec, caseset, ev


class ValueProfileTest(unittest.TestCase):
    """value_profile（借 generate_array special_values/tie，op-中立）。"""

    def test_make_value_profile_nan(self):
        rng = np.random.default_rng(0)
        a = GC._make_value_profile(rng, (4, 6), "float32", "nan")
        self.assertTrue(np.isnan(a).any())
        self.assertTrue(np.isfinite(a).any())          # 既含 nan 也含常规值

    def test_make_value_profile_tie(self):
        rng = np.random.default_rng(0)
        a = GC._make_value_profile(rng, (4, 6), "float32", "tie")
        _, cnt = np.unique(a, return_counts=True)
        self.assertGreater(int(cnt.max()), 1)           # 有并列（重复值）

    def test_nan_profile_rejects_integer(self):
        with self.assertRaises(ValueError):
            GC._make_value_profile(np.random.default_rng(0), (6,), "int32", "nan")

    def test_value_profiles_reader_validates(self):
        self.assertEqual(GC._value_profiles({}), [])
        self.assertEqual(GC._value_profiles({"precision": {"value_profiles": ["nan", "tie", "nan"]}}),
                         ["nan", "tie"])                # 去重保序
        with self.assertRaises(ValueError):
            GC._value_profiles({"precision": {"value_profiles": ["bogus"]}})

    def test_spec_driven_produces_nan_and_tie_cases(self):
        spec = _fake_median_spec(op="MedVP", dtypes=("float32",),
                                 value_profiles=("nan", "tie"), case_target=30)
        cs, work = _gen(spec, _FAKE_MEDIAN_BODY, op="MedVP")
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        nan_cases = [c for c in cs["cases"] if "vpnan" in c["id"]]
        tie_cases = [c for c in cs["cases"] if "vptie" in c["id"]]
        self.assertTrue(nan_cases, "无 nan value_profile 用例")
        self.assertTrue(tie_cases, "无 tie value_profile 用例")
        x = np.load(os.path.join(work, nan_cases[0]["inputs"][0]["path"]))
        self.assertTrue(np.isnan(x).any())
        xt = np.load(os.path.join(work, tie_cases[0]["inputs"][0]["path"]))
        _, cnt = np.unique(xt, return_counts=True)
        self.assertGreater(int(cnt.max()), 1)


class IndexGoldenDtypeTest(unittest.TestCase):
    """F2（a3 真机首跑实测的阻断级洞）：index golden 的落盘 dtype **按 spec 声明取**、不再恒 int64。

    旧洞机理：真机 actual 的 dtype 由 caseset `expected.outputs[k].compare_dtype`（= spec 声明，driver
    据它开输出 buffer）决定，而 golden 恒存 int64 → **声明 int32 indices 的算子两侧必然打架**，
    `compute_metrics` 的 index 分支「两侧下标 dtype 须一致」当场 fail-closed → 永远出不了裁决。
    全部据 `out_role`/`dtype` 字段驱动，无算子名分支。"""

    def _gen_idx(self, dtype_name, op, body=_FAKE_MEDIAN_BODY):
        spec = _fake_median_spec(op=op)
        idx_p = next(p for p in spec["params"] if p.get("out_role") == "index")
        idx_p["dtype"] = [dtype_name]                     # ← 唯一变量：spec 声明的 index dtype
        cs, work = _gen(spec, body, op=op)
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        return spec, cs, work

    def _bydim(self, cs):
        got = [c for c in cs["cases"] if len(c["expected"]["outputs"]) == 2]
        self.assertTrue(got, "用例集里无 by-dim 双输出 case（测试前提不成立）")
        return got

    def _ctx(self, work, case):
        """gather 上下文：源 = case 的 self 输入，dim/keepdim 取自 case attrs（确定、不靠形状反推）。"""
        return {"source": _load(work, case["inputs"][0]["path"]),
                "dim": case["attrs"]["dim"], "keepdim": bool(case["attrs"].get("keepdim"))}

    def test_int32_indices_generate_and_judge(self):
        """⭐ 核心：声明 int32 → golden 存 int32，且能跑通 index 判据（旧实现在此存 int64 → 判据必抛）。"""
        _, cs, work = self._gen_idx("int32", "MedIdx32")
        for c in self._bydim(cs):
            idx = c["expected"]["outputs"][1]
            self.assertEqual(idx["compare_dtype"], "int32", c["id"])
            arr = _load(work, idx["golden_path"])
            self.assertEqual(arr.dtype, np.int32, c["id"])
            # 真机按 compare_dtype 收 → actual 同为 int32；判据须真跑出 metrics（而非抛 dtype 不一致）
            m = P.compute_metrics(arr, arr, idx["policy"], gather_ctx=self._ctx(work, c))
            self.assertEqual(m["mismatch"], 0, c["id"])
            self.assertEqual(m["numel"], int(np.asarray(arr).size), c["id"])

    def test_int64_indices_still_work(self):
        """向后兼容：任意 spec 声明 int64 时 golden 仍为 int64、判据照跑。"""
        _, cs, work = self._gen_idx("int64", "MedIdx64")
        for c in self._bydim(cs):
            idx = c["expected"]["outputs"][1]
            self.assertEqual(idx["compare_dtype"], "int64", c["id"])
            arr = _load(work, idx["golden_path"])
            self.assertEqual(arr.dtype, np.int64, c["id"])
            m = P.compute_metrics(arr, arr, idx["policy"], gather_ctx=self._ctx(work, c))
            self.assertEqual(m["mismatch"], 0, c["id"])

    def test_value_output_dtype_unaffected(self):
        """index dtype 换成 int32 不得殃及 value 输出（value 仍按 compare dtype 落盘）。"""
        _, cs, work = self._gen_idx("int32", "MedIdx32v")
        for c in self._bydim(cs):
            v = c["expected"]["outputs"][0]
            self.assertEqual(_load(work, v["golden_path"]).dtype, np.dtype(v["compare_dtype"]), c["id"])

    def test_two_side_dtype_conflict_fail_closed(self):
        """两侧 dtype 冲突（实现返回了非声明 dtype）→ **fail-closed、不隐式归一**（静默通过是最坏结果）。"""
        _, cs, work = self._gen_idx("int32", "MedIdx32c")
        c = self._bydim(cs)[0]
        idx = c["expected"]["outputs"][1]
        golden = _load(work, idx["golden_path"])
        with self.assertRaises(ValueError) as cm:                 # actual 是 int64、golden 是 int32
            P.compute_metrics(golden.astype(np.int64), golden, idx["policy"], gather_ctx=self._ctx(work, c))
        self.assertIn("dtype 不一致", str(cm.exception))

    def test_out_of_declared_range_fail_closed_end_to_end(self):
        """下标超出声明 dtype 值域（int32 装不下）→ gen_cases 当场 fail-closed，绝不静默回绕。"""
        big_body = _FAKE_MEDIAN_BODY.replace("return (vv, vi.astype(np.int64))",
                                             "return (vv, (vi + 2**31).astype(np.int64))")
        with self.assertRaises(ValueError) as cm:
            self._gen_idx("int32", "MedIdxBig", body=big_body)
        self.assertIn("装不下", str(cm.exception))

    def test_index_golden_array_guards(self):
        """`_index_golden_array` 三道闸：值域、非整数 golden、非法声明 dtype（边界内不误伤）。"""
        w = "t: 输出#1(indices/index)"
        with self.assertRaises(ValueError) as cm:
            GC._index_golden_array(np.array([2 ** 31], np.int64), "int32", w)
        self.assertIn("装不下", str(cm.exception))
        with self.assertRaises(ValueError):
            GC._index_golden_array(np.array([-2 ** 31 - 1], np.int64), "int32", w)
        ok = GC._index_golden_array(np.array([2 ** 31 - 1], np.int64), "int32", w)   # 边界内不误伤
        self.assertEqual(ok.dtype, np.int32)
        self.assertEqual(int(ok[0]), 2 ** 31 - 1)
        for bad in (np.array([0.9], np.float32), np.array([True])):                  # 非整数下标一律拒
            with self.assertRaises(ValueError, msg=repr(bad)):
                GC._index_golden_array(bad, "int64", w)
        for bad_dt in ("float32", "bfloat16", "bogus"):                              # 声明 dtype 非整数/未知
            with self.assertRaises(ValueError, msg=bad_dt):
                GC._index_golden_array(np.array([0], np.int64), bad_dt, w)
        self.assertEqual(GC._index_golden_array(np.zeros((0,), np.int64), "int32", w).dtype, np.int32)


class SingleOutputBackwardCompatTest(unittest.TestCase):
    """单输出向后兼容硬约束：假单输出算子走 legacy expected（无 outputs 字段）；4 算子 dry-run 无回归。"""

    def test_single_output_op_stays_legacy(self):
        spec = {"op": "FakeNeg", "repo": "t", "runner_form": "cpp",
                "verify_mode": "exact", "generalize": True,
                "precision": {"oracle": "ascendoptest", "case_target": 8},
                "params": [{"name": "self", "io": "in", "dtype": ["float32"]},
                           {"name": "y", "io": "out", "dtype": ["float32"]}]}
        cs, work = _gen(spec, _FAKE_SINGLE_BODY, op="FakeNeg")
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        self.assertTrue(cs["cases"])
        for c in cs["cases"]:
            self.assertNotIn("outputs", c["expected"], f"{c['id']} 单输出算子不应有 outputs 字段（legacy 破坏）")
            self.assertIn("golden_path", c["expected"])   # legacy 单 golden 结构

    def test_existing_four_ops_dry_run(self):
        """现有 4 算子 dry-run（plan-only、不跑 golden、不需 torch）无回归——单输出通路计划稳定。"""
        for name, path in (("IsClose", "isclose"), ("Sign", "sign"),
                           ("Equal", "equal"), ("Neg", "neg")):
            with open(os.path.join(_HERE, "..", "samples", "specs", f"{path}.spec.json"), encoding="utf-8") as fh:
                spec = json.load(fh)
            import contextlib, io
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                GC._dry_run(spec)                         # 不抛即通过（单输出通路 plan 未破）
            self.assertIn("[dry-run]", buf.getvalue(), name)


class MedianGoldenOutShapeTest(unittest.TestCase):
    """真 Median golden.py 的 out_shape（纯函数、不需 torch）。"""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("median_golden_real", _MEDIAN_GOLDEN)
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def test_global_scalar(self):
        self.assertEqual(self.mod.out_shape([(4, 6)], {"dim": None, "keepDim": False}), ())

    def test_bydim_reduce(self):
        self.assertEqual(self.mod.out_shape([(4, 6)], {"dim": 0, "keepDim": False}), (6,))
        self.assertEqual(self.mod.out_shape([(4, 6)], {"dim": 1, "keepDim": False}), (4,))

    def test_keepdim(self):
        self.assertEqual(self.mod.out_shape([(4, 6)], {"dim": 0, "keepDim": True}), (1, 6))

    def test_negative_dim(self):
        self.assertEqual(self.mod.out_shape([(2, 3, 4)], {"dim": -1, "keepDim": False}), (2, 3))

    def test_invalid_dim_fail_closed(self):
        with self.assertRaises(ValueError):
            self.mod.out_shape([(4, 6)], {"dim": 5, "keepDim": False})

    def test_contract_block_verifies_tier1(self):
        """真任务书快照在算子目录 → GOLDEN_CONTRACT 授权可核、tier1。"""
        c = self.mod.GOLDEN_CONTRACT
        self.assertEqual(c["authorization"]["kind"], "oracle_method")
        snap = os.path.join(os.path.dirname(_MEDIAN_GOLDEN), "task_doc.snapshot.md")
        ok, why = P.verify_authorization(c, snap)
        self.assertTrue(ok, why)
        tier, needs_human, blocked = P.derive_golden_tier(c, ok)
        self.assertEqual(tier, 1, (tier, blocked))


@unittest.skipUnless(_has_torch(), "无 torch → 真 Median golden_fn fail-closed；本测试需 torch（精度验收在 NPU 机）")
class MedianGoldenFnTorchTest(unittest.TestCase):
    """真 Median golden.py 的 golden_fn（需 torch）：全局单输出、by-dim 双输出。"""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("median_golden_real2", _MEDIAN_GOLDEN)
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def test_bydim_returns_tuple(self):
        x = np.arange(24, dtype=np.float32).reshape(4, 6)
        r = self.mod.golden_fn([x], {"dim": 1, "keepDim": False})
        self.assertIsInstance(r, tuple)
        self.assertEqual(len(r), 2)
        vv, vi = r
        self.assertEqual(vv.shape, (4,))
        self.assertEqual(vi.shape, (4,))
        self.assertEqual(vi.dtype, np.int32)

    def test_global_returns_single(self):
        x = np.arange(24, dtype=np.float32).reshape(4, 6)
        r = self.mod.golden_fn([x], {"dim": None, "keepDim": False})
        self.assertNotIsInstance(r, tuple)
        self.assertEqual(np.asarray(r).shape, ())

    def test_real_spec_end_to_end_double_output(self):
        """真 median spec + 真 golden 全跑 → 全局单输出 + by-dim 双输出并存。"""
        _gf.place_golden(_gf.root(), "Median", body=None)   # 拷真 golden.py + 快照
        spec = json.load(open(_MEDIAN_SPEC))
        self_p = next(p for p in spec["params"] if p["name"] == "self")
        self_p["dtype"], self_p["rank"] = ["float32"], [1]
        matrix = spec["precision"]["torch_parity_matrix"]
        matrix["ranks"] = [1]
        matrix["shape_profiles"] = [{"name": "small", "leading_dim": 31}]
        spec["precision"]["case_target"] = 7                # 1 dtype×1 rank×1 shape×7 attrs
        work = tempfile.mkdtemp(prefix="mo_realmed_")
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        cs = GC.gen_cases(spec, work)
        lens = {len(c["expected"]["outputs"]) for c in cs["cases"]}
        self.assertEqual(lens, {1, 2}, lens)
        # tier1 随 case 走
        self.assertEqual(cs["cases"][0]["expected"]["golden_tier"]["tier"], 1)


class AclnnCallPerCaseTest(unittest.TestCase):
    """aclnn_py/cpp_extension → **逐 case** 解析 `aclnn_call`（op 级模板已废）。

    契约：case["aclnn_call"] = {"symbol", "slots":[{role,name,...}]}；attr slot 带已解析 value + ctype，
    非 active 的 out 写成 out_null。**绝不把 None 兜成标量默认值**；无匹配变体 → fail-closed。"""

    def test_real_median_spec_resolves_two_variants(self):
        """真 median spec：dim=null → 无标量槽 + 单输出（indices 走 out_null）；dim 有值 → 双 attr 槽 + 双输出。

        ⚠ 2026-07-25 订正：两个变体的 symbol **都是 `Median`**。PR6429 实测是「单符号 + 固定签名 +
        NULL 输出区分全局/按维」——DUT `.so` 根本不导出 `aclnnMedianDim`（那是 CANN 内置）。
        原版把 by-dim 断言成 `MedianDim`，会把「验的其实是内置、不是 PR」这条假 DUT 通道钉成期望值。
        本用例现同时作该回归的 pin：**by-dim 绝不许再路由到 DUT 未导出的符号**。
        （变体机制本身的通用性由 `test_gen_cases_attaches_per_case_call` 用假 spec 覆盖，此处只钉真 spec。）"""
        spec = json.load(open(_MEDIAN_SPEC))
        variants = GC._call_variants(spec)
        g = GC._select_call_variant(variants, {"dim": None, "keepDim": False}, "cid")
        self.assertEqual(g["symbol"], "Median")
        self.assertEqual(g["active_outputs"], ["valuesOut"])
        call_g = GC._build_aclnn_call(
            spec, g, {"dim": None, "keepDim": False}, ["valuesOut"], "cid")
        self.assertEqual(call_g, {"symbol": "Median", "slots": [
            {"role": "in", "name": "self", "input_idx": 0},
            {"role": "attr", "name": "dim", "ctype": "int64", "value": 0},
            {"role": "attr", "name": "keepDim", "ctype": "bool", "value": False},
            {"role": "out", "name": "valuesOut", "output_idx": 0},
            {"role": "out_null", "name": "indicesOut"},
        ]})
        d = GC._select_call_variant(variants, {"dim": 1, "keepDim": False}, "cid")
        self.assertEqual(d["symbol"], "Median")          # 单符号：与全局变体同符号，靠 indices 是否为空区分
        call_d = GC._build_aclnn_call(spec, d, {"dim": 1, "keepDim": False},
                                      ["valuesOut", "indicesOut"], "cid")
        # slots 顺序 = spec.params 顺序 = aclnn 签名顺序；每个 slot 带 name（供与 header 逐项对账）。
        self.assertEqual(call_d, {"symbol": "Median", "slots": [
            {"role": "in", "name": "self", "input_idx": 0},
            {"role": "attr", "name": "dim", "ctype": "int64", "value": 1},
            {"role": "attr", "name": "keepDim", "ctype": "bool", "value": False},
            {"role": "out", "name": "valuesOut", "output_idx": 0},
            {"role": "out", "name": "indicesOut", "output_idx": 1},
        ]})

    def test_gen_cases_attaches_per_case_call(self):
        spec = _fake_median_spec(op="MedTmpl", dtypes=("float32",))
        spec["runner_form"] = "aclnn_py"
        cs, work = _gen(spec, _FAKE_MEDIAN_BODY, op="MedTmpl")
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        self.assertNotIn("aclnn_call_template", cs, "op 级模板已被逐 case aclnn_call 替换")
        for c in cs["cases"]:
            call = c["aclnn_call"]
            self.assertTrue(all("name" in s for s in call["slots"]), c["id"])
            if c["attrs"]["dim"] is None:                 # 全局变体：无标量槽、indices 走 out_null
                self.assertEqual(call["symbol"], "FakeGlobal")
                self.assertEqual([s["role"] for s in call["slots"]], ["in", "out", "out_null"])
            else:
                self.assertEqual(call["symbol"], "FakeDim")
                self.assertEqual([s["role"] for s in call["slots"]],
                                 ["in", "attr", "attr", "out", "out"])
                vals = {s["name"]: s["value"] for s in call["slots"] if s["role"] == "attr"}
                self.assertEqual(vals["dim"], c["attrs"]["dim"])   # 逐 case 真值、非默认
                self.assertEqual(vals["keepdim"], c["attrs"]["keepdim"])
            # out slot 的 output_idx 必须精确指向本 case 的 expected.outputs[]
            outs = c["expected"]["outputs"]
            for s in call["slots"]:
                if s["role"] == "out":
                    self.assertEqual(outs[s["output_idx"]]["name"], s["name"], c["id"])

    def test_gen_cases_omits_call_by_default(self):
        """无 runner_form（含缺省=cpp）→ 不加 aclnn_call（向后兼容硬约束：现有 4 算子不破）。"""
        spec = _fake_median_spec(op="MedNoTmpl", dtypes=("float32",))
        cs, work = _gen(spec, _FAKE_MEDIAN_BODY, op="MedNoTmpl")
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        self.assertNotIn("aclnn_call_template", cs)
        self.assertTrue(all("aclnn_call" not in c for c in cs["cases"]))

    # ── 负向：变体缺失 / 无匹配 / None 兜底 / attr dtype 多候选 ────────────────────────
    def test_aclnn_py_without_call_variants_fail_closed(self):
        spec = _fake_median_spec(op="MedNoVar", dtypes=("float32",), call_variants=None)
        spec["runner_form"] = "aclnn_py"
        _gf.place_golden(_gf.root(), "MedNoVar", body=_FAKE_MEDIAN_BODY)
        work = tempfile.mkdtemp(prefix="mo_novar_")
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        with self.assertRaises(ValueError) as cm:
            GC.gen_cases(spec, work)
        self.assertIn("call_variants", str(cm.exception))

    def test_no_matching_variant_fail_closed(self):
        """attrs 落在所有 when 之外 → fail-closed，**绝不退默认**（原来的 dim=None→0 就是这么来的）。"""
        variants = GC._call_variants(_fake_median_spec(
            call_variants=[{"when": {"attr": "dim", "equals": 0}, "symbol": "S",
                            "active_outputs": ["values"]}]))
        with self.assertRaises(ValueError) as cm:
            GC._select_call_variant(variants, {"dim": 3, "keepdim": False}, "cX")
        self.assertIn("无匹配", str(cm.exception))

    def test_none_attr_never_silently_defaulted(self):
        """active_attrs 里的 attr 解析成 None → fail-closed（不许兜 0/False）。"""
        spec = _fake_median_spec(call_variants=[
            {"when": {"attr": "dim", "is_null": True}, "symbol": "S",
             "active_attrs": ["dim", "keepdim"], "active_outputs": ["values"]}])
        v = GC._call_variants(spec)[0]
        with self.assertRaises(ValueError) as cm:
            GC._build_aclnn_call(spec, v, {"dim": None, "keepdim": False}, ["values"], "cX")
        self.assertIn("None", str(cm.exception))

    def test_variant_can_declare_explicit_attr_value(self):
        """spec 里**显式声明**的 attrs 覆盖是合法的（人写死的声明 ≠ 代码兜的默认值）。"""
        spec = _fake_median_spec(call_variants=[
            {"when": {"attr": "dim", "is_null": True}, "symbol": "S",
             "attrs": {"dim": 0}, "active_outputs": ["values"]}])
        v = GC._call_variants(spec)[0]
        call = GC._build_aclnn_call(spec, v, {"dim": None, "keepdim": False}, ["values"], "cX")
        self.assertEqual([s for s in call["slots"] if s["name"] == "dim"],
                         [{"role": "attr", "name": "dim", "ctype": "int64", "value": 0}])

    def test_attr_ctype_fail_closed_on_unsupported(self):
        with self.assertRaises(ValueError):
            GC._attr_ctype({"name": "foo", "dtype": ["int8"]})

    def test_attr_ctype_rejects_multiple_candidates(self):
        """finding #5：多候选一律拒——即便首项合法（`["int64","int8"]` / `["float32","bogus"]`）。"""
        for dts in (["int64", "int8"], ["float32", "bogus"], []):
            with self.assertRaises(ValueError, msg=dts):
                GC._attr_ctype({"name": "foo", "dtype": dts})
        self.assertEqual(GC._attr_ctype({"name": "foo", "dtype": ["int64"]}), "int64")
        self.assertEqual(GC._attr_ctype({"name": "foo", "dtype": "bool"}), "bool")

    def test_call_variants_schema_fail_closed(self):
        base = _fake_median_spec()

        def bad(vs):
            s = dict(base)
            s["call_variants"] = vs
            return s
        cases = [
            [],                                                                    # 空表
            [{"symbol": "S", "active_outputs": ["values"]}],                       # 缺 when
            [{"when": {"attr": "dim", "is_null": True}, "active_outputs": ["values"]}],   # 缺 symbol
            [{"when": {"attr": "dim", "is_null": True}, "symbol": "S"}],           # 缺 active_outputs
            [{"when": {"attr": "nope", "is_null": True}, "symbol": "S", "active_outputs": ["values"]}],
            [{"when": {"attr": "dim", "is_null": True, "equals": 0}, "symbol": "S",
              "active_outputs": ["values"]}],                                      # 两个判据
            [{"when": {"attr": "dim", "is_null": True}, "symbol": "S",
              "active_outputs": ["values", "bogus"]}],                             # 非 spec out
            [{"when": {"attr": "dim", "is_null": True}, "symbol": "S",
              "active_outputs": ["indices", "values"]}],                           # 换序（非 spec 子序列）
            [{"when": {"attr": "dim", "is_null": True}, "symbol": "S",
              "active_outputs": ["values", "values"]}],                            # 重复
            [{"when": {"attr": "dim", "is_null": True}, "symbol": "S",
              "active_attrs": ["nope"], "active_outputs": ["values"]}],
            [{"when": {"attr": "dim", "is_null": True}, "symbol": "S",
              "attrs": {"nope": 1}, "active_outputs": ["values"]}],
        ]
        for vs in cases:
            with self.assertRaises(ValueError, msg=vs):
                GC._call_variants(bad(vs))

    def test_index_output_without_its_value_fail_closed(self):
        """index 落地、它 index_of 所引的 value 没落地 → 判据悬空 → fail-closed。"""
        spec = _fake_median_spec(call_variants=[
            {"when": {"always": True}, "symbol": "S", "active_outputs": ["indices"]}])
        v = GC._call_variants(spec)[0]
        with self.assertRaises(ValueError) as cm:
            GC._active_output_names(spec, v, "cX")
        self.assertIn("index_of", str(cm.exception))


class OutputIdentityBindingTest(unittest.TestCase):
    """finding #4：输出**数量与身份**严格绑 spec —— 缺输出 / 换序都必须被逮。"""

    def _spec_all_outputs(self, op, dtypes=("float32",)):
        """无变体表 → 落地集 = spec 全部 out 参数（by-dim 与全局都得给 2 个输出）。"""
        return _fake_median_spec(op=op, dtypes=dtypes, dim_vals=(0,), call_variants=None)

    def test_missing_output_is_rejected(self):
        """by-dim golden 漏掉 indices（只返回 values）→ 不再当「更短的前缀」收下，直接 fail-closed。"""
        body = _FAKE_MEDIAN_BODY.replace("return (vv, vi.astype(np.int64))", "return vv")
        _gf.place_golden(_gf.root(), "MedMissing", body=body)
        work = tempfile.mkdtemp(prefix="mo_missing_")
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        with self.assertRaises(ValueError) as cm:
            GC.gen_cases(self._spec_all_outputs("MedMissing"), work)
        self.assertIn("不接受更短的前缀", str(cm.exception))

    def test_outputs_carry_index_name_role_triplet(self):
        spec = _fake_median_spec(op="MedIdent", dtypes=("float32",))
        cs, work = _gen(spec, _FAKE_MEDIAN_BODY, op="MedIdent")
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        spec_names = [p["name"] for p in spec["params"] if p["io"] == "out"]
        for c in cs["cases"]:
            outs = c["expected"]["outputs"]
            self.assertEqual([o["index"] for o in outs], list(range(len(outs))), c["id"])
            names = [o["name"] for o in outs]
            self.assertEqual(names, [n for n in spec_names if n in names], c["id"])  # 保 spec 序
            for o in outs:                                # role 与 spec out_role 一致
                p = next(p for p in spec["params"] if p.get("name") == o["name"])
                self.assertEqual(o["role"], p["out_role"], (c["id"], o["name"]))

    def test_swapped_golden_order_is_detected(self):
        """golden 把 (values, indices) 换成 (indices, values) → **换序不再无声**。两条闸按 dtype 分工：

        · 浮点 value 落进 index 槽（float32 算子）→ **生成期当场 fail-closed**（F2 起 index golden 按
          spec 声明的整数 dtype 存，浮点「下标」一律拒；旧实现 `astype(int64)` 把中位值静默截成整数存下，
          只能靠下游看值反推）；
        · 两侧都是整数（int32 算子，dtype 上换序合法）→ 仍按 spec 声明的身份落盘，靠 (index,name,role)
          三元组 + 值本身可判别（旧断言原样保留）。"""
        body = _FAKE_MEDIAN_BODY.replace("return (vv, vi.astype(np.int64))",
                                         "return (vi.astype(np.int64), vv)")
        _gf.place_golden(_gf.root(), "MedSwap", body=body)
        work = tempfile.mkdtemp(prefix="mo_swap_")
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        with self.assertRaises(ValueError) as cm:            # float32：换序后 index 槽拿到浮点 → 当场拒
            GC.gen_cases(self._spec_all_outputs("MedSwap"), work)
        self.assertIn("非整数", str(cm.exception))
        # int32 算子：换序在 dtype 上合法 → 生成通过，但身份仍按 spec 落，值本身可判别。
        _gf.place_golden(_gf.root(), "MedSwapInt", body=body)
        work2 = tempfile.mkdtemp(prefix="mo_swap_int_")
        self.addCleanup(shutil.rmtree, work2, ignore_errors=True)
        cs = GC.gen_cases(self._spec_all_outputs("MedSwapInt", dtypes=("int32",)), work2)
        # 身份仍按 spec 声明落：outputs[0] 恒是 values（value 判据），outputs[1] 恒是 indices。
        # 于是换序后的 golden 值被存进了「名不副实」的槽 —— 下游按 (index,name,role) 三元组对账即可发现。
        for c in cs["cases"]:
            outs = c["expected"]["outputs"]
            self.assertEqual([(o["index"], o["name"], o["role"]) for o in outs],
                             [(0, "values", "value"), (1, "indices", "index")], c["id"])
        v0 = _load(work2, cs["cases"][0]["expected"]["outputs"][0]["golden_path"])
        i0 = _load(work2, cs["cases"][0]["expected"]["outputs"][1]["golden_path"])
        # values 槽里装的其实是下标（整数），indices 槽里装的是中位值 → 与正确产物不同，可判别。
        self.assertTrue(np.array_equal(v0, np.floor(v0)), "换序后 values 槽装的是整数下标")
        self.assertEqual(i0.dtype, np.int64)


class OutRoleVocabTest(unittest.TestCase):
    """finding #6：out_role 触发门用 `in`、角色走受控词表、index_of 必指唯一具名 value。"""

    def _spec(self, **over):
        s = _fake_median_spec(op="RoleV", dtypes=("float32",))
        for p in s["params"]:
            if p["name"] in over:
                p.update(over[p["name"]])
        return s

    def test_trigger_gate_uses_key_presence(self):
        """单输出算子声明 `out_role: ""` → **不得**退回 legacy（真值判断会放它过去）。"""
        single = {"op": "RoleEmpty", "repo": "t", "runner_form": "cpp",
                  "verify_mode": "exact", "generalize": True,
                  "precision": {"oracle": "ascendoptest", "case_target": 4},
                  "params": [{"name": "self", "io": "in", "dtype": ["float32"]},
                             {"name": "y", "io": "out", "dtype": ["float32"], "out_role": ""}]}
        self.assertTrue(GC._uses_output_contract(single))
        no_role = json.loads(json.dumps(single))
        no_role["params"][1].pop("out_role")
        self.assertFalse(GC._uses_output_contract(no_role))     # 没声明才是 legacy

    def test_empty_and_unknown_role_rejected(self):
        for bad in ("", "bogus", None, "VALUE"):
            spec = self._spec(values={"out_role": bad})
            with self.assertRaises(ValueError, msg=bad):
                P.derive_output_contracts(spec, [("self", "float32")], "torch_allclose", "dtype_table")

    def test_missing_out_role_rejected(self):
        spec = self._spec()
        for p in spec["params"]:
            if p["name"] == "values":
                p.pop("out_role")
        with self.assertRaises(ValueError):
            P.derive_output_contracts(spec, [("self", "float32")], "torch_allclose", "dtype_table")

    def test_index_of_must_point_to_value(self):
        for ref in (None, "", "indices", "nope"):
            spec = self._spec(indices={"index_of": ref})
            with self.assertRaises(ValueError, msg=ref):
                P.derive_output_contracts(spec, [("self", "float32")], "torch_allclose", "dtype_table")

    def test_duplicate_or_missing_out_names_rejected(self):
        spec = self._spec(indices={"name": "values", "index_of": "values"})
        with self.assertRaises(ValueError):
            P.derive_output_contracts(spec, [("self", "float32")], "torch_allclose", "dtype_table")
        spec2 = self._spec(values={"name": ""})
        with self.assertRaises(ValueError):
            P.derive_output_contracts(spec2, [("self", "float32")], "torch_allclose", "dtype_table")


class ValueProfileCoverageTest(unittest.TestCase):
    """finding #8：代表 dtype 确定性选 + 找不到 fail-closed；补维后 tie 仍成立（逐轴核验）。"""

    def test_pick_vp_dtype_deterministic(self):
        self.assertEqual(GC._pick_vp_dtype(["int32", "float16", "float32"]), "float32")   # 按优先序
        self.assertEqual(GC._pick_vp_dtype(["int32", "bfloat16", "float16"]), "float16")
        self.assertEqual(GC._pick_vp_dtype(["bfloat16"]), "bfloat16")

    def test_pick_vp_dtype_fail_closed_without_float(self):
        with self.assertRaises(ValueError) as cm:
            GC._pick_vp_dtype(["int32", "int64"])
        self.assertIn("value_profiles", str(cm.exception))

    def test_non_float32_dtype_set_still_produces_profile_cases(self):
        """dtype 集无 float32（只有 float16）→ 不再静默产零条，改用 fp16 代表 dtype。"""
        spec = _fake_median_spec(op="MedVP16", dtypes=("float16",),
                                 value_profiles=("nan", "tie"), case_target=20)
        cs, work = _gen(spec, _FAKE_MEDIAN_BODY, op="MedVP16")
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        vp = [c for c in cs["cases"] if "vpnan" in c["id"] or "vptie" in c["id"]]
        self.assertTrue(vp, "非 float32 dtype 集下 value_profile 用例为零（假覆盖）")
        self.assertTrue(all(c["inputs"][0]["dtype"] == "float16" for c in vp))

    def test_int_only_dtype_set_fail_closed(self):
        spec = _fake_median_spec(op="MedVPInt", dtypes=("int32",),
                                 value_profiles=("tie",), case_target=12)
        _gf.place_golden(_gf.root(), "MedVPInt", body=_FAKE_MEDIAN_BODY)
        work = tempfile.mkdtemp(prefix="mo_vpint_")
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        with self.assertRaises(ValueError):
            GC.gen_cases(spec, work)

    def test_vp_shape_never_pads_leading_one(self):
        """`_fit_rank` 会补前导 1（(1,4,6)）让 dim=0 每切片只 1 元素；`_vp_shape` 不会。"""
        self.assertEqual(GC._fit_rank((4, 6), frozenset({3})), (1, 4, 6))     # 旧路径的病灶
        for ranks, want in ((None, (4, 6)), (frozenset({2}), (4, 6)),
                            (frozenset({3}), (4, 6, 4)), (frozenset({4}), (4, 6, 4, 6)),
                            (frozenset({1}), (4,))):
            self.assertEqual(GC._vp_shape(ranks), want, ranks)
            self.assertTrue(all(d >= 4 for d in GC._vp_shape(ranks)), ranks)

    def test_tie_holds_on_every_axis_after_rank_fit(self):
        """补维后（rank3/4）每个轴的**每条**切片仍有并列——逐轴核验，不只看全局有重复。"""
        rng = np.random.default_rng(0)
        for ranks in (frozenset({1}), frozenset({2}), frozenset({3}), frozenset({4})):
            shp = GC._vp_shape(ranks)
            a = GC._make_value_profile(rng, shp, "float32", "tie")
            for ax in range(a.ndim):
                m = np.moveaxis(a, ax, -1)
                s = np.sort(m, axis=-1)
                self.assertTrue(bool((np.diff(s, axis=-1) == 0).any(axis=-1).all()),
                                f"shape={shp} 轴 {ax} 有切片无并列")

    def test_tie_assert_catches_degenerate_shape(self):
        """人为造一个补了前导 1 的 tie 数组 → 逐轴核验必须当场逮住。"""
        bad = GC._fill_cyclic(list(GC._TIE_VALUES), (1, 4, 6), np.float32)
        with self.assertRaises(ValueError) as cm:
            GC._assert_tie_per_axis(bad, "float32")
        self.assertIn("轴 0", str(cm.exception))

    def test_tie_cases_in_generated_caseset_hold(self):
        spec = _fake_median_spec(op="MedTieR3", dtypes=("float32",), ranks=(3,),
                                 value_profiles=("tie",), case_target=16)
        cs, work = _gen(spec, _FAKE_MEDIAN_BODY, op="MedTieR3")
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        tie_cases = [c for c in cs["cases"] if "vptie" in c["id"]]
        self.assertTrue(tie_cases)
        for c in tie_cases:
            x = _load(work, c["inputs"][0]["path"])
            self.assertEqual(x.ndim, 3)
            self.assertTrue(all(d >= 4 for d in x.shape), x.shape)
            GC._assert_tie_per_axis(x, "float32")          # 不抛即每轴每切片都有并列


class UnpairedComboLedgerTest(unittest.TestCase):
    """零配对告警（bug#12 底线）：某 attr 取值 × 某 shape 结构类**从未同时出现**要被 dry-run 账本报出来。

    实测教训——任务书点名「归约轴上维度为 1」，而含长度-1 轴的 shape 只由特殊场景产、只配
    `attr_combos[0]`（`dim=None`），于是点名场景实跑 0 条且**全程无告警**，只能事后人肉核 caseset。
    """

    def _plan_meta(self, spec):
        in_params = [p for p in spec["params"] if p["io"] == "in"]
        attrs_default = {p["name"]: p.get("default") for p in spec["params"] if p["io"] == "attr"}
        dtypes = next(p for p in in_params if p["name"] == "self")["dtype"]
        target = GC._require_case_target(spec)          # 无缺省：spec 没写就该当场炸
        of = GC.load_golden(spec["op"]).out_shape
        _entries, meta = GC._plan(
            spec, in_params, dtypes, attrs_default, spec["op"], target,
            cost_fn=GC._make_cost_fn(in_params, of),
            empty_accepts=GC._make_empty_accepts(in_params, of, attrs_default))
        return meta

    def test_shape_class_is_structural(self):
        """shape 结构类按结构判、与算子无关：全 1 轴 / 含 1 轴 / 空 / 大 / 常规各归各的。"""
        self.assertEqual(GC._shape_class((1,)), GC.SHAPE_CLASS_ALL_UNIT)
        self.assertEqual(GC._shape_class((1, 1, 1)), GC.SHAPE_CLASS_ALL_UNIT)
        self.assertEqual(GC._shape_class((4, 1)), GC.SHAPE_CLASS_HAS_UNIT)
        self.assertEqual(GC._shape_class((0,)), GC.SHAPE_CLASS_EMPTY)
        self.assertEqual(GC._shape_class((3, 0, 4)), GC.SHAPE_CLASS_EMPTY)
        self.assertEqual(GC._shape_class((1024, 1024)), GC.SHAPE_CLASS_LARGE)
        self.assertEqual(GC._shape_class((4, 6)), GC.SHAPE_CLASS_REGULAR)
        self.assertEqual(GC._shape_class("broadcast"), GC.SHAPE_CLASS_BCAST)

    def test_zero_pairing_is_reported(self):
        """归约 attr 的具体取值从未配上「被指轴长度=1」的 shape → 必须出现在零配对账本里。

        ⚠ 断言口径随 finding #8 修正：轴型 attr 的配对类不再是「shape 结构类」（那会把
        `shape=(4,1), dim=0` 当成已配上单位轴 = 漏报），改成「归一化轴号 + rank + 被指轴长度档」。"""
        spec = _fake_median_spec(op="MedUnpaired", dtypes=("float32",), ranks=(1, 2, 3))
        _gf.place_golden(_gf.root(), "MedUnpaired", body=_FAKE_MEDIAN_BODY)
        led = self._plan_meta(spec)["unpaired_combo_classes"]
        self.assertGreater(led["count"], 0, "含长度-1 轴的 shape 只配 combo0，零配对必然存在")
        self.assertTrue(any("dim=0" in c and "被指轴长度=1" in c for c in led["classes"]),
                        f"应报出「dim=0 × 被指轴长度=1 从未配对」，实得 {led['classes']}")
        # 非轴 attr（keepdim 是 bool）仍走 shape 结构类口径，口径不串
        self.assertTrue(all("shape类=" in c for c in led["classes"] if c.startswith("keepdim=")),
                        f"非轴 attr 应沿用 shape 结构类口径，实得 {led['classes']}")
        self.assertLessEqual(len(led["classes"]), GC._UNPAIRED_CAP)   # 有上限，不炸账本

    def test_axis_attr_pairing_keys_on_pointed_axis_length(self):
        """finding #8 漏报复现：`shape=(4,1), dim=0` 的归约轴长度其实是 **4**，不该算「已配上单位轴」。

        旧口径按 shape 结构类（(4,1) 属 `has_unit_axis`）→ 认为 `dim=0 × 含单位轴` 已配对、count=0；
        新口径按「归一化轴号 + rank + 被指轴长度档」→ `dim=0 × 被指轴长度=1` 仍是缺口，必须报出来。"""
        entries = [{"shape": (4, 1), "attrs": {"dim": 0}},      # dim=0 指的轴长 4（不是 1）
                   {"shape": (1, 4), "attrs": {"dim": 1}}]      # 池子里确有「rank2·轴0 长度=1」的形状
        # 见证旧口径为什么会漏：两条 shape 的**结构类完全相同**，按结构类判就成了「已配对」
        self.assertEqual(GC._shape_class((4, 1)), GC._shape_class((1, 4)))
        led = GC._unpaired_combo_classes(entries, [("dim", [0, 1])])
        self.assertGreater(led["count"], 0, "dim=0 从未配上『被指轴长度=1』，不该是 0")
        self.assertTrue(any("dim=0" in c and "被指轴长度=1" in c for c in led["classes"]),
                        f"应报出「dim=0 × 被指轴长度=1」，实得 {led['classes']}")
        # 对照：真配上了就不该再报（别把已覆盖的说成缺口）
        entries.append({"shape": (1, 4), "attrs": {"dim": 0}})
        led2 = GC._unpaired_combo_classes(entries, [("dim", [0, 1])])
        self.assertFalse(any("dim=0" in c and "被指轴长度=1" in c for c in led2["classes"]),
                         f"dim=0 已配上长度-1 轴，不该再报，实得 {led2['classes']}")

    def test_axis_attr_pairing_never_reports_unrealizable(self):
        """反向（误报面）：某 rank 下已越界的轴值，绝不拿来做笛卡尔积——那是不可实现的「缺口」。"""
        entries = [{"shape": (4, 6), "attrs": {"dim": 0}},              # rank2：dim=3 越界
                   {"shape": (4, 6, 4, 1), "attrs": {"dim": 3}}]        # rank4：dim=3 合法
        led = GC._unpaired_combo_classes(entries, [("dim", [0, 3])])
        self.assertFalse(any("dim=3" in c and "rank2" in c for c in led["classes"]),
                         f"rank2 下 dim=3 越界，不可实现，不该报，实得 {led['classes']}")

    def test_repeated_index_value_is_not_treated_as_axes(self):
        """`stride=[2,2]` 这类几何参数**不是**轴集合（同一根轴不会被指两次）→ 仍走 shape 结构类口径。

        据结构判、不看字段名：不加这条，im2col 的 stride/dilation 会被误判成轴型 attr，
        告警变成读不懂的「轴2,2·被指轴长度=1,1」。"""
        self.assertIsNone(GC._norm_axes([2, 2], 4))          # 重复 → 非法轴集合
        self.assertIsNone(GC._norm_axes([0, -2], 2))         # 归一化后重复，同样非法
        self.assertEqual(GC._norm_axes([0, -1], 3), [0, 2])  # 正常多轴值照常归一化
        entries = [{"shape": (4, 6, 4, 6), "attrs": {"stride": [2, 2]}},
                   {"shape": (1, 1, 1, 1), "attrs": {"stride": [1, 1]}}]
        led = GC._unpaired_combo_classes(entries, [("stride", [[2, 2], [1, 1]])])
        self.assertTrue(all("shape类=" in c for c in led["classes"]), led["classes"])

    def test_non_axis_attr_keeps_shape_class_semantics(self):
        """普通非轴 attr（bool/float）口径不变：仍按 shape 结构类配对（回归护栏）。"""
        entries = [{"shape": (4, 6), "attrs": {"equal_nan": False}},
                   {"shape": (1, 1), "attrs": {"equal_nan": True}}]
        led = GC._unpaired_combo_classes(entries, [("equal_nan", [False, True])])
        self.assertTrue(all("shape类=" in c for c in led["classes"]), led["classes"])
        self.assertIn(f"equal_nan=False × shape类={GC.SHAPE_CLASS_ALL_UNIT}", led["classes"])

    def test_ledger_only_counts_classes_that_exist(self):
        """不报「不可能」：某 shape 结构类根本没出现过时，不该拿它去凑零配对（那不是缺口）。"""
        spec = _fake_median_spec(op="MedNoBcast", dtypes=("float32",), ranks=(1, 2, 3))
        _gf.place_golden(_gf.root(), "MedNoBcast", body=_FAKE_MEDIAN_BODY)
        led = self._plan_meta(spec)["unpaired_combo_classes"]
        self.assertFalse(any(GC.SHAPE_CLASS_BCAST in c for c in led["classes"]),
                         "该算子没有广播 shape，不该报它的零配对")

    def test_dry_run_prints_warning(self):
        """账本要真的打进 dry-run 输出（这条通路才是人/agent 实际看到的那一份）。"""
        import contextlib, io
        spec = _fake_median_spec(op="MedDryUnpaired", dtypes=("float32",), ranks=(1, 2, 3))
        _gf.place_golden(_gf.root(), "MedDryUnpaired", body=_FAKE_MEDIAN_BODY)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            GC._dry_run(spec)
        text = buf.getvalue()
        self.assertIn("unpaired_combo_classes", text)
        self.assertIn("shape_classes", text)

    def test_four_ops_ledger_is_low_noise(self):
        """现有 4 算子（无归约轴）零配对应当极少——告警得可读，否则等于没有。"""
        for path in ("isclose", "sign", "equal", "neg"):
            with open(os.path.join(_HERE, "..", "samples", "specs", f"{path}.spec.json"),
                      encoding="utf-8") as fh:
                spec = json.load(fh)
            led = self._plan_meta(spec)["unpaired_combo_classes"]
            self.assertLessEqual(led["count"], 4, f"{path} 零配对告警过多（{led['classes']}）")


class AttrAxisLengthTest(unittest.TestCase):
    """轴维度约束（bug#12 进一步）：spec 可声明「某 attr 指向的轴取哪些长度」→ **定向生成**。"""

    def _spec(self, op, lengths=(1,), attr="dim", **kw):
        kw.setdefault("ranks", (1, 2, 3))                # 调用方可覆盖（多 rank 用例要 (2,4)）
        spec = _fake_median_spec(op=op, dtypes=("float32",), **kw)
        spec["attr_axis_lengths"] = [{"attr": attr, "lengths": list(lengths)}]
        _gf.place_golden(_gf.root(), op, body=_FAKE_MEDIAN_BODY)
        return spec

    def _cases(self, spec):
        work = tempfile.mkdtemp(prefix="axlen_")
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        return GC.gen_cases(spec, work)

    def test_axis_length_cases_are_generated(self):
        """dim 指的轴长度恰为 1 的用例必须真的出现（且 dim=None 的全局变体自然缺席）。"""
        cs = self._cases(self._spec("MedAx1"))
        hits = [c for c in cs["cases"] if "ax0len1" in c["id"]]
        self.assertTrue(hits, "声明了 attr_axis_lengths 却一条定向用例都没有")
        for c in hits:
            dim = c["attrs"].get("dim")
            self.assertIsNotNone(dim, "全局变体（dim=None）没有『那根轴』，不该被造出来")
            shp = c["inputs"][0]["shape"]
            idx = dim if dim >= 0 else dim + len(shp)
            self.assertEqual(int(shp[idx]), 1,
                             f"{c['id']}: dim={dim} 指的轴长度应为 1，实得 shape={shp}")

    def test_multiple_lengths(self):
        cs = self._cases(self._spec("MedAx12", lengths=(1, 2)))
        got = set()
        for c in cs["cases"]:
            dim = c["attrs"].get("dim")
            if dim is None or "len" not in c["id"]:
                continue
            shp = c["inputs"][0]["shape"]
            got.add(int(shp[dim if dim >= 0 else dim + len(shp)]))
        self.assertEqual(got, {1, 2})

    def test_absent_field_changes_nothing(self):
        """**向后兼容硬约束**：不声明该字段 → plan entries 与账本口径一字不变（现有算子零影响）。"""
        spec = _fake_median_spec(op="MedNoAx", dtypes=("float32",), ranks=(1, 2, 3))
        _gf.place_golden(_gf.root(), "MedNoAx", body=_FAKE_MEDIAN_BODY)
        in_params = [p for p in spec["params"] if p["io"] == "in"]
        attrs_default = {p["name"]: p.get("default") for p in spec["params"] if p["io"] == "attr"}
        of = GC.load_golden("MedNoAx").out_shape
        entries, meta = GC._plan(spec, in_params, ["float32"], attrs_default, "MedNoAx", 24,
                                 cost_fn=GC._make_cost_fn(in_params, of),
                                 empty_accepts=GC._make_empty_accepts(in_params, of, attrs_default))
        self.assertEqual(meta["attr_axis_lengths"]["emitted"], 0)
        self.assertFalse(any("轴长度" in (e.get("tags") or []) for e in entries))

    def test_schema_fail_closed(self):
        """结构不对 / 未知 attr / 非正长度一律 fail-closed（声明了却产不出 = 假覆盖）。"""
        defaults = {"dim": None, "keepdim": False}
        for bad in ([], {"attr": "dim"}, [{"attr": "nope", "lengths": [1]}],
                    [{"attr": "dim", "lengths": []}], [{"attr": "dim", "lengths": [0]}],
                    [{"attr": "dim", "lengths": [-1]}], [{"attr": "dim", "lengths": [True]}]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                GC._attr_axis_lengths({"attr_axis_lengths": bad}, defaults)
        self.assertEqual(GC._attr_axis_lengths({}, defaults), [])

    def test_declared_but_unsatisfiable_fail_closed(self):
        """attr 取值全都不是轴下标（这里只留 dim=None）→ 一条都产不出 → 当场炸，不静默零覆盖。"""
        spec = self._spec("MedAxNone")
        spec["attr_matrix"] = [{"dim": None, "keepdim": False}]     # 只剩全局变体，没有任何轴下标
        with self.assertRaises(ValueError) as cm:
            self._cases(spec)
        self.assertIn("假覆盖", str(cm.exception))

    def _plan(self, spec, op, cost=True):
        """跑 `_plan` 拿 (entries, meta)。`cost=False` 时不带 cost_fn（等价 dry-run 的未核路径），
        用于「网格里存在该 rank 下越界的 attr 组合」这类**只看计划、不执行 golden** 的断言。"""
        in_params = [p for p in spec["params"] if p["io"] == "in"]
        attrs_default = {p["name"]: p.get("default") for p in spec["params"] if p["io"] == "attr"}
        dtypes = next(p for p in in_params if p["name"] == "self")["dtype"]
        of = GC.load_golden(op).out_shape
        return GC._plan(spec, in_params, dtypes, attrs_default, op, 24,
                        cost_fn=GC._make_cost_fn(in_params, of) if cost else None,
                        empty_accepts=GC._make_empty_accepts(in_params, of, attrs_default)
                        if cost else None)

    # ---------- finding #5：多 rank 下逐轴值挑 rank，别静默丢掉可生成的变体 ----------
    def test_multi_rank_picks_a_rank_that_fits_each_axis(self):
        """rank 允许 {2,4}、attr 含 dim=0 与 dim=3 → **dim=3 要落到 rank4**，不是被当越界静默跳过。

        原实现全局只挑一个基准 rank（离 rank2 最近 → 2），dim=3 直接进 skipped，而全局 emitted>0
        让 fail-closed 判据看不出来 —— 「部分轴变体静默缺失」。"""
        spec = self._spec("MedAxRank24", lengths=(1,), dim_vals=(None, 0, 3), ranks=(2, 4))
        entries, meta = self._plan(spec, "MedAxRank24", cost=False)
        led = meta["attr_axis_lengths"]
        by_val = {}
        for it in led["items"]:
            by_val.setdefault(it["attr_value"], set()).add(it["status"])
        self.assertEqual(by_val.get(0), {"emitted"}, f"dim=0 应产出，实得账本 {led['items']}")
        self.assertEqual(by_val.get(3), {"emitted"}, f"dim=3 应落到 rank4 产出，实得账本 {led['items']}")
        self.assertEqual(by_val.get(None), {"not_applicable"})   # 全局归约合法缺席
        self.assertEqual(led["skipped"], [], "逐轴值挑 rank 后不该再有静默缺失")
        hit3 = [e for e in entries if e.get("axis_lock", {}).get("axes") == [3]]
        self.assertTrue(hit3, "dim=3 的定向用例没生成")
        for e in hit3:
            self.assertEqual(len(e["shape"]), 4, f"dim=3 需要 rank≥4，实得 {e['shape']}")
            self.assertEqual(int(e["shape"][3]), 1)
        # dim=0 仍按「离 rank2 最近」落 rank2（确定性规则没被改坏）
        hit0 = [e for e in entries if e.get("axis_lock", {}).get("axes") == [0]]
        self.assertTrue(hit0 and all(len(e["shape"]) == 2 for e in hit0), [e["shape"] for e in hit0])
        # 见证 finding #5 的旧行为：全局基准 shape 只有 rank2，dim=3 在它上面必然越界 → 静默跳过
        self.assertEqual(len(GC._vp_shape((2, 4))), 2)
        self.assertIsNone(GC._axis_length_shape(GC._vp_shape((2, 4)), [3], 1))
        self.assertEqual(len(GC._axis_base_shape((2, 4), [3])), 4)   # 新：按轴值挑得下的 rank

    def test_axis_value_with_no_legal_rank_fails_closed(self):
        """轴取值在**所有**声明 rank 下都越界 → 逐项判据当场炸（不再靠总数非零蒙混过关）。"""
        spec = self._spec("MedAxNoRank", lengths=(1,), dim_vals=(None, 0, 5), ranks=(1, 2))
        with self.assertRaises(ValueError) as cm:
            self._plan(spec, "MedAxNoRank", cost=False)
        msg = str(cm.exception)
        self.assertIn("部分轴取值产不出", msg)
        self.assertIn("假覆盖", msg)

    def test_ledger_is_per_item_not_just_a_total(self):
        """账本逐 `(constraint, length, attr 组合)` 记状态——只报总数就是 finding #5 的病根。"""
        spec = self._spec("MedAxLedger", lengths=(1, 2), ranks=(1, 2, 3))
        _entries, meta = self._plan(spec, "MedAxLedger", cost=False)
        led = meta["attr_axis_lengths"]
        self.assertTrue(led["items"])
        self.assertTrue(all({"status", "attr", "length", "attr_idx", "attr_value"} <= set(it)
                            for it in led["items"]))
        self.assertEqual(led["emitted"], sum(1 for it in led["items"] if it["status"] == "emitted"))
        self.assertEqual({it["length"] for it in led["items"]}, {1, 2})

    # ---------- finding #4：轴长度约束维在 cost 降规模中锁定，且降完复验 ----------
    def test_locked_axis_survives_shrink(self):
        """降规模只许动**没被约束**的维：声明轴长 100 的 case，降完那根轴仍是 100。"""
        forced = [{"shape": (100, 6), "attrs": {}, "dtype": "float32", "id_kind": "ax0len100",
                   "case_origin": "axis_length:dim:100:a1", "dims": ["功能"], "tags": ["轴长度"],
                   "axis_lock": {"attr": "dim", "axes": [0], "length": 100, "rank": 2}}]
        grid = [{"shape": (16,), "attrs": {}, "dtype": "float32", "id_kind": "gridu",
                 "case_origin": "grid", "dims": ["功能"], "tags": ["常规"]}]
        cost = lambda shp, attrs, where: GC._numel(shp)          # noqa: E731
        _kept, led = GC._apply_cost_budget(forced, grid, cost, 400)
        self.assertEqual(forced[0]["shape"][0], 100, "被约束的轴不许降")
        self.assertLess(GC._numel(forced[0]["shape"]), 400)
        self.assertIn("降规模", forced[0]["tags"])                # 降了规模照常留痕
        self.assertEqual(led["scaled_cases"][0]["id_kind"], "ax0len100")
        GC._verify_axis_locks(forced)                            # 复验通过（身份与实际一致）

    def test_axis_length_vs_budget_conflict_fails_closed(self):
        """锁定后仍超预算 → **fail-closed** 明说「轴长度约束与 cost 预算冲突」，不静默降规模。"""
        forced = [{"shape": (100, 6), "attrs": {}, "dtype": "float32", "id_kind": "ax0len100",
                   "case_origin": "axis_length:dim:100:a1", "dims": ["功能"], "tags": ["轴长度"],
                   "axis_lock": {"attr": "dim", "axes": [0], "length": 100, "rank": 2}}]
        cost = lambda shp, attrs, where: GC._numel(shp)          # noqa: E731
        with self.assertRaises(ValueError) as cm:
            GC._apply_cost_budget(forced, [], cost, 50)
        msg = str(cm.exception)
        self.assertIn("轴长度约束与 golden 生成期规模预算冲突", msg)
        self.assertIn("假覆盖", msg)
        self.assertEqual(forced[0]["shape"], (100, 6), "炸了就不许留下被改过的形状")
        # 见证 finding #4 的旧行为：**不带 lock** 时同一条会被悄悄降成 (6,6)——约束的那根轴从 100 变成 6，
        # 而 id_kind/case_origin 仍写着 ax0len100 → 账本与 case ID 冒充覆盖了任务书边界。
        old, _c = GC._shrink_to_budget((100, 6), {}, cost, 50, "w")
        self.assertEqual(old, (6, 6))
        self.assertNotEqual(int(old[0]), 100)

    def test_shrunk_axis_length_case_raises_instead_of_lying(self):
        """兜底门：若哪天有人绕过锁把约束维改小，复验必须**抛**，而不是让 id/账本继续宣称覆盖。"""
        e = {"shape": (4, 3), "attrs": {}, "dtype": "float32", "id_kind": "ax0len100",
             "case_origin": "axis_length:dim:100:a1", "dims": ["功能"], "tags": ["轴长度", "降规模"],
             "axis_lock": {"attr": "dim", "axes": [0], "length": 100, "rank": 2}}
        with self.assertRaises(ValueError) as cm:
            GC._verify_axis_locks([e])
        msg = str(cm.exception)
        self.assertIn("假覆盖", msg)
        self.assertIn("ax0len100", msg)

    def test_end_to_end_budget_conflict_fails_closed(self):
        """整条 gen_cases 上也炸（不是只有单测里炸）：小预算 + 大轴长 → 冲突当场停。"""
        spec = self._spec("MedAxBudget", lengths=(100,), ranks=(1, 2, 3))
        spec["precision"]["golden_cost_budget"] = 50
        with self.assertRaises(ValueError) as cm:
            self._cases(spec)
        self.assertIn("轴长度约束与 golden 生成期规模预算冲突", str(cm.exception))

    def test_end_to_end_shrink_keeps_declared_axis_length(self):
        """预算够降但不够宽时：case 真的落盘，且**声明的轴长度在真实输入里成立**（不是嘴上覆盖）。"""
        spec = self._spec("MedAxKeep", lengths=(100,), ranks=(1, 2, 3))
        spec["precision"]["golden_cost_budget"] = 400
        cs = self._cases(spec)
        hits = [c for c in cs["cases"] if "ax0len100" in c["id"]]
        self.assertTrue(hits, "轴长度 case 没生成")
        for c in hits:
            dim = c["attrs"]["dim"]
            shp = c["inputs"][0]["shape"]
            idx = dim if dim >= 0 else dim + len(shp)
            self.assertEqual(int(shp[idx]), 100,
                             f"{c['id']}: 宣称覆盖轴长 100，实际 shape={shp} → 假覆盖")

    def test_directed_generation_closes_the_gap(self):
        """定向生成后，`dim` 取值 × 含长度-1 轴的 shape **不再零配对**（bug#12 的实质修复）。"""
        spec = self._spec("MedAxFix")
        in_params = [p for p in spec["params"] if p["io"] == "in"]
        attrs_default = {p["name"]: p.get("default") for p in spec["params"] if p["io"] == "attr"}
        of = GC.load_golden("MedAxFix").out_shape
        entries, _meta = GC._plan(spec, in_params, ["float32"], attrs_default, "MedAxFix", 24,
                                  cost_fn=GC._make_cost_fn(in_params, of),
                                  empty_accepts=GC._make_empty_accepts(in_params, of, attrs_default))
        paired = {(e["attrs"].get("dim"), GC._shape_class(e["shape"])) for e in entries}
        unit = {GC.SHAPE_CLASS_ALL_UNIT, GC.SHAPE_CLASS_HAS_UNIT}
        self.assertTrue(any(d == 0 and sc in unit for d, sc in paired),
                        f"dim=0 仍未配上任何含长度-1 轴的 shape：{sorted(paired, key=repr)}")


class OperatorClassSpecialsTest(unittest.TestCase):
    """OC · `spec.operator_class` → 特殊值口径分档（字段驱动、op-中立，**无算子名分支**）。

    修的是**导致合格 PR 被误判 FAIL 的流程缺陷**：引擎原先无条件给每个浮点 dtype 铺 inf/-inf/nan，
    median PR6429 的 6 条 fail 全是 NaN 用例——用**超出验收口径**的用例把合格 PR 判挂了。
    依据（参考仓 `Justbin/cannbot-ops-input`）：`design_contract.py:512` 只对 `floating_compute`
    调 `_validate_floating_rules`（:360-393，只有那一类强制 nan/pos_inf/neg_inf/mixed_inf）；
    `SKILL.md:252` 给结构/整型类列的是「极值、0/1/-1、**重复**、越界索引、广播、规约轴、饱和」。
    """
    _NONFINITE = {"inf", "ninf", "nan"}

    def _kinds(self, spec):
        """跑 plan（不落盘、不需 golden）→ (id_kind 集合, meta)。"""
        in_params = [p for p in spec["params"] if p["io"] == "in"]
        attrs_default = {p["name"]: p.get("default") for p in spec["params"] if p["io"] == "attr"}
        entries, meta = GC._plan(spec, in_params, in_params[0]["dtype"], attrs_default,
                                 spec["op"], spec["precision"]["case_target"])
        return {e["id_kind"] for e in entries}, meta

    # ── 词表与缺省语义 ───────────────────────────────────────────────────────────────
    def test_vocabulary_and_default(self):
        self.assertIsNone(GC._operator_class({}))                       # 未声明 → None
        for v in ("floating_compute", "integer_compute", "structural"):
            self.assertEqual(GC._operator_class({"operator_class": v}), v)
        self.assertTrue(GC._emits_nonfinite(None))                      # 未声明 = 现行为（照产）
        self.assertTrue(GC._emits_nonfinite("floating_compute"))
        self.assertFalse(GC._emits_nonfinite("structural"))
        self.assertFalse(GC._emits_nonfinite("integer_compute"))

    def test_unknown_class_fail_closed(self):
        for bad in ("bogus", "", "Structural", 1, True, ["structural"]):
            with self.assertRaises(ValueError, msg=f"{bad!r} 应 fail-closed") as cm:
                GC._operator_class({"operator_class": bad})
            self.assertIn("受控词表", str(cm.exception))

    def test_unknown_class_fail_closed_end_to_end(self):
        spec = _fake_median_spec(op="MedOCBad", dtypes=("float32",), operator_class="bogus")
        with self.assertRaises(ValueError) as cm:
            self._kinds(spec)
        self.assertIn("operator_class", str(cm.exception))

    # ── 分档：structural / integer_compute 不产 nan·inf；floating_compute 与缺省照产 ──────
    def test_structural_omits_nonfinite_specials(self):
        kinds, meta = self._kinds(_fake_median_spec(op="MedOCStruct", dtypes=("float32",),
                                                    operator_class="structural"))
        self.assertFalse(kinds & self._NONFINITE,
                         f"structural 算子仍产了非有限特殊场景 {sorted(kinds & self._NONFINITE)}")
        self.assertEqual(meta["operator_class"], "structural")
        self.assertFalse(meta["emits_nonfinite_specials"])

    def test_integer_compute_omits_nonfinite_specials(self):
        kinds, _ = self._kinds(_fake_median_spec(op="MedOCInt", dtypes=("float32",),
                                                 operator_class="integer_compute"))
        self.assertFalse(kinds & self._NONFINITE)

    def test_floating_compute_still_emits_nonfinite_specials(self):
        kinds, meta = self._kinds(_fake_median_spec(op="MedOCFloat", dtypes=("float32",),
                                                    operator_class="floating_compute"))
        self.assertEqual(self._NONFINITE, kinds & self._NONFINITE,
                         f"floating_compute 少了非有限特殊场景：{sorted(self._NONFINITE - kinds)}")
        self.assertTrue(meta["emits_nonfinite_specials"])

    def test_absent_field_keeps_current_behavior(self):
        """**向后兼容硬约束**：整字段省略 → 与改动前一致（照产 inf/-inf/nan）。"""
        kinds, meta = self._kinds(_fake_median_spec(op="MedOCNone", dtypes=("float32",)))
        self.assertEqual(self._NONFINITE, kinds & self._NONFINITE)
        self.assertIsNone(meta["operator_class"])
        self.assertTrue(meta["emits_nonfinite_specials"])

    def test_boundary_specials_kept_for_every_class(self):
        """只砍 inf/-inf/nan：标量 / 上下边界（scalar·bndlo·bndhi）**所有类别都保留**。"""
        for oc in (None, "floating_compute", "integer_compute", "structural"):
            kinds, _ = self._kinds(_fake_median_spec(op=f"MedOCB{oc or 'None'}", dtypes=("float32",),
                                                     operator_class=oc))
            self.assertTrue({"scalar", "bndlo", "bndhi"} <= kinds, f"{oc}: 边界特殊场景被误砍 {sorted(kinds)}")

    # ── value_profiles 与类别的自洽（nan 冲突 fail-closed；tie 全类别保留）───────────────
    def test_nan_profile_conflicts_with_non_floating_class(self):
        for oc in ("structural", "integer_compute"):
            with self.assertRaises(ValueError, msg=oc) as cm:
                GC._value_profiles({"operator_class": oc,
                                    "precision": {"value_profiles": ["nan", "tie"]}})
            msg = str(cm.exception)
            self.assertIn("nan", msg)
            self.assertIn("operator_class", msg)          # 报错点名怎么改（改类别 or 去掉 profile）

    def test_nan_profile_conflict_fail_closed_end_to_end(self):
        spec = _fake_median_spec(op="MedOCNanConf", dtypes=("float32",),
                                 operator_class="structural", value_profiles=("nan",))
        with self.assertRaises(ValueError) as cm:
            self._kinds(spec)
        self.assertIn("nan", str(cm.exception))

    def test_nan_profile_ok_for_floating_compute(self):
        self.assertEqual(GC._value_profiles({"operator_class": "floating_compute",
                                             "precision": {"value_profiles": ["nan", "tie"]}}),
                         ["nan", "tie"])
        self.assertEqual(GC._value_profiles({"precision": {"value_profiles": ["nan"]}}), ["nan"])

    def test_tie_profile_survives_in_every_class(self):
        for oc in (None, "floating_compute", "integer_compute", "structural"):
            self.assertEqual(GC._value_profiles({**({"operator_class": oc} if oc else {}),
                                                 "precision": {"value_profiles": ["tie"]}}), ["tie"])
            kinds, _ = self._kinds(_fake_median_spec(op=f"MedOCT{oc or 'None'}", dtypes=("float32",),
                                                     operator_class=oc, value_profiles=("tie",),
                                                     case_target=20))
            self.assertIn("vptie", kinds, f"{oc}: tie value_profile 用例丢了")

    # ── 端到端：结构类算子的输入里**真的**一个 NaN/Inf 都没有（不是嘴上不产）──────────────
    def test_structural_inputs_contain_no_nan_or_inf(self):
        spec = _fake_median_spec(op="MedOCE2E", dtypes=("float32",), operator_class="structural",
                                 value_profiles=("tie",), case_target=24)
        cs, work = _gen(spec, _FAKE_MEDIAN_BODY, op="MedOCE2E")
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        self.assertTrue(cs["cases"])
        self.assertEqual(cs["operator_class"], "structural")     # 声明了才落进 caseset（账本可追溯）
        self.assertFalse(cs["emits_nonfinite_specials"])
        for c in cs["cases"]:
            for item in c["inputs"]:
                x = np.load(os.path.join(work, item["path"]))
                if x.dtype.kind != "f" or x.size == 0:
                    continue
                self.assertFalse(bool(np.isnan(x).any()),
                                 f"{c['id']}: structural 算子输入含 NaN（超出验收口径 → 会误判合格 PR）")
                self.assertTrue(bool(np.isfinite(x).all()),
                                f"{c['id']}: structural 算子输入含 Inf（超出验收口径）")
        self.assertTrue(any("vptie" in c["id"] for c in cs["cases"]),
                        "legacy tie 用例应保留")

    def test_caseset_field_absent_when_class_undeclared(self):
        """未声明 → caseset **不出现** operator_class 键（现有算子 caseset 逐字节不变的前提）。"""
        spec = _fake_median_spec(op="MedOCNoKey", dtypes=("float32",), case_target=12)
        cs, work = _gen(spec, _FAKE_MEDIAN_BODY, op="MedOCNoKey")
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        self.assertNotIn("operator_class", cs)
        self.assertNotIn("emits_nonfinite_specials", cs)

    # ── 真 median spec 的口径守卫（本次判错的直接修正）────────────────────────────────
    def test_real_median_spec_is_structural_without_nan_profile(self):
        with open(_MEDIAN_SPEC, encoding="utf-8") as fh:
            spec = json.load(fh)
        self.assertEqual(spec.get("operator_class"), "structural")
        self.assertNotIn("nan", spec["precision"].get("value_profiles", []),
                         "median 是 structural 类，nan profile 会 fail-closed")
        self.assertEqual(spec["precision"].get("case_profile"), "torch_parity")
        self.assertIn("torch_parity_matrix", spec["precision"])

    def test_real_median_torch_parity_dry_run_renders(self):
        with open(_MEDIAN_SPEC, encoding="utf-8") as fh:
            spec = json.load(fh)
        ledger = GC._dry_run(spec)
        self.assertEqual(ledger["summary"]["emitted"], 1344)
        self.assertEqual(
            ledger["coverage"]["unpaired_combo_classes"],
            {"count": 0, "classes": [], "attr_values_never_emitted": []})


if __name__ == "__main__":
    unittest.main()
