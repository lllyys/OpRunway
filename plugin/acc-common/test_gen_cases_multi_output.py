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
                      call_variants=_FAKE_VARIANTS, ranks=(1, 2, 3)):
    # dim ∈ {null(全局), 0(first), -1(last)} 对任意 rank≥1 恒有效；middle=rank//2 需 per-rank 解析（scale 阶段）。
    matrix = [{"dim": d, "keepdim": False} for d in dim_vals] + [{"dim": 0, "keepdim": True}]
    prec = {"oracle": "torch", "standard": "torch_allclose", "tolerance_source": "dtype_table",
            "case_target": case_target}
    if value_profiles:
        prec["value_profiles"] = list(value_profiles)
    spec = {
        "op": op, "repo": "t", "verify_mode": "numerical", "generalize": True,
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


class SingleOutputBackwardCompatTest(unittest.TestCase):
    """单输出向后兼容硬约束：假单输出算子走 legacy expected（无 outputs 字段）；4 算子 dry-run 无回归。"""

    def test_single_output_op_stays_legacy(self):
        spec = {"op": "FakeNeg", "repo": "t", "verify_mode": "exact", "generalize": True,
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
        self.assertEqual(self.mod.out_shape([(4, 6)], {"dim": None, "keepdim": False}), ())

    def test_bydim_reduce(self):
        self.assertEqual(self.mod.out_shape([(4, 6)], {"dim": 0, "keepdim": False}), (6,))
        self.assertEqual(self.mod.out_shape([(4, 6)], {"dim": 1, "keepdim": False}), (4,))

    def test_keepdim(self):
        self.assertEqual(self.mod.out_shape([(4, 6)], {"dim": 0, "keepdim": True}), (1, 6))

    def test_negative_dim(self):
        self.assertEqual(self.mod.out_shape([(2, 3, 4)], {"dim": -1, "keepdim": False}), (2, 3))

    def test_invalid_dim_fail_closed(self):
        with self.assertRaises(ValueError):
            self.mod.out_shape([(4, 6)], {"dim": 5, "keepdim": False})

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
        r = self.mod.golden_fn([x], {"dim": 1, "keepdim": False})
        self.assertIsInstance(r, tuple)
        self.assertEqual(len(r), 2)
        vv, vi = r
        self.assertEqual(vv.shape, (4,))
        self.assertEqual(vi.shape, (4,))
        self.assertEqual(vi.dtype, np.int64)

    def test_global_returns_single(self):
        x = np.arange(24, dtype=np.float32).reshape(4, 6)
        r = self.mod.golden_fn([x], {"dim": None, "keepdim": False})
        self.assertNotIsInstance(r, tuple)
        self.assertEqual(np.asarray(r).shape, ())

    def test_real_spec_end_to_end_double_output(self):
        """真 median spec + 真 golden 全跑 → 全局单输出 + by-dim 双输出并存。"""
        _gf.place_golden(_gf.root(), "Median", body=None)   # 拷真 golden.py + 快照
        spec = json.load(open(_MEDIAN_SPEC))
        spec["precision"]["case_target"] = 20               # 收敛测试规模
        work = tempfile.mkdtemp(prefix="mo_realmed_")
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        cs = GC.gen_cases(spec, work)
        lens = {len(c["expected"]["outputs"]) for c in cs["cases"]}
        self.assertEqual(lens, {1, 2}, lens)
        # tier1 随 case 走
        self.assertEqual(cs["cases"][0]["expected"]["golden_tier"]["tier"], 1)


class AclnnCallPerCaseTest(unittest.TestCase):
    """runner_form=="aclnn_py" → **逐 case** 解析出 `aclnn_call`（审计 finding #3；op 级模板已废）。

    契约：case["aclnn_call"] = {"symbol", "slots":[{role,name,...}]}；attr slot 带已解析 value + ctype，
    非 active 的 out 写成 out_null。**绝不把 None 兜成标量默认值**；无匹配变体 → fail-closed。"""

    def test_real_median_spec_resolves_two_variants(self):
        """真 median spec：dim=null → 全局符号 + 无标量槽 + 单输出；dim 有值 → by-dim 符号 + 双输出。"""
        spec = json.load(open(_MEDIAN_SPEC))
        variants = GC._call_variants(spec)
        g = GC._select_call_variant(variants, {"dim": None, "keepdim": False}, "cid")
        self.assertEqual(g["symbol"], "Median")
        self.assertEqual(g["active_outputs"], ["values"])
        call_g = GC._build_aclnn_call(spec, g, {"dim": None, "keepdim": False}, ["values"], "cid")
        self.assertEqual(call_g, {"symbol": "Median", "slots": [
            {"role": "in", "name": "self", "input_idx": 0},
            {"role": "out", "name": "values", "output_idx": 0},
            {"role": "out_null", "name": "indices"},
        ]})
        d = GC._select_call_variant(variants, {"dim": 1, "keepdim": False}, "cid")
        self.assertEqual(d["symbol"], "MedianDim")
        call_d = GC._build_aclnn_call(spec, d, {"dim": 1, "keepdim": False},
                                      ["values", "indices"], "cid")
        # slots 顺序 = spec.params 顺序 = aclnn 签名顺序；每个 slot 带 name（供与 header 逐项对账）。
        self.assertEqual(call_d, {"symbol": "MedianDim", "slots": [
            {"role": "in", "name": "self", "input_idx": 0},
            {"role": "attr", "name": "dim", "ctype": "int64", "value": 1},
            {"role": "attr", "name": "keepdim", "ctype": "bool", "value": False},
            {"role": "out", "name": "values", "output_idx": 0},
            {"role": "out", "name": "indices", "output_idx": 1},
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

    def _spec_all_outputs(self, op):
        """无变体表 → 落地集 = spec 全部 out 参数（by-dim 与全局都得给 2 个输出）。"""
        return _fake_median_spec(op=op, dtypes=("float32",), dim_vals=(0,), call_variants=None)

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
        """golden 把 (values, indices) 换成 (indices, values) → 形状/dtype 都合法，但 int 值进 value 判据
        会被 out_shape/契约对账逮住（换序不再无声）。"""
        body = _FAKE_MEDIAN_BODY.replace("return (vv, vi.astype(np.int64))",
                                         "return (vi.astype(np.int64), vv)")
        _gf.place_golden(_gf.root(), "MedSwap", body=body)
        work = tempfile.mkdtemp(prefix="mo_swap_")
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        spec = self._spec_all_outputs("MedSwap")
        cs = GC.gen_cases(spec, work)
        # 身份仍按 spec 声明落：outputs[0] 恒是 values（value 判据），outputs[1] 恒是 indices。
        # 于是换序后的 golden 值被存进了「名不副实」的槽 —— 下游按 (index,name,role) 三元组对账即可发现。
        for c in cs["cases"]:
            outs = c["expected"]["outputs"]
            self.assertEqual([(o["index"], o["name"], o["role"]) for o in outs],
                             [(0, "values", "value"), (1, "indices", "index")], c["id"])
        v0 = _load(work, cs["cases"][0]["expected"]["outputs"][0]["golden_path"])
        i0 = _load(work, cs["cases"][0]["expected"]["outputs"][1]["golden_path"])
        # values 槽里装的其实是下标（整数），indices 槽里装的是被截断的中位值 → 与正确产物不同，可判别。
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
        single = {"op": "RoleEmpty", "repo": "t", "verify_mode": "exact", "generalize": True,
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


if __name__ == "__main__":
    unittest.main()
