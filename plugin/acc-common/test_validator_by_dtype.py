"""validator 的逐 dtype 精度聚合块 `accuracy_summary` 单测（L3 · 对标 cannbot，2026-07-25）。

跑: cd plugin/acc-common && python3 test_validator_by_dtype.py

被测对象是 `validator.py` 新增的**纯只读派生块**（对标参考仓 cannbot-ops-input
`skills/operator-evaluation/scripts/accuracy.py:624-694` 的 `by_dtype` + `overall_pass_rate`）。

覆盖：
  · 多 dtype 混合 → 分桶计数 / pass_rate / 总体 overall_pass_rate 正确；
  · **errored 计入分母（total）、不计入 executed**（对标 cannbot `accuracy.py:648`/`:656-659`/`:667-671`）；
  · 每 dtype 回显的 rtol/atol 与 `precision_policy.threshold_for` 给的**逐字一致**（不另算一套容差）；
  · dtype 键按**输出 dtype**（有意偏离 cannbot 的输入 dtype 口径，见 validator 该节抬头 ①）；
  · 多输出算子按 **value-role 输出的 dtype** 归桶、一条 case 只占一格（index 的 int64 不另开桶）；
  · **新增块不影响既有裁决**：聚合内部炸掉时，除 `accuracy_summary` 外 verdict 逐字段与正常跑一致。

⚠ 关于 errored 的**诚实边界**（对应任务要求「分不出来的要明说」）：我方目前**没有**与 cannbot 一一对应
  的「kernel 崩」独立信号。可分辨的只有证据侧三种：evidence 缺此 case / `status != "ok"` / `precision.metrics`
  缺失（golden 没读成、误差没复算）。故本文件测的是**这三类**；「跑了但口径/身份契约不符」在已成型的
  per_case 行上与「真数值错」不可分辨，当前一并计入 `failed`（保守、不抬高 pass_rate），见
  `test_contract_failure_counted_as_failed_documented`——那条是把**当前边界钉住**，不是宣称它已分开。

op-中立：全部见证 spec 按字段声明驱动（io/out_role/call_variants），无算子名分支（律令#0）。
"""
import copy
import unittest

import precision_policy as P
import validator as V


_OP = "DemoElementwise"                 # 见证用假算子名——只作「跑通用通路」的输入，不是优化目标
_TOL_SRC = "dtype_table"


# ------------------------------------------------------------- 单输出见证件 ---
def _spec():
    """单输出 elementwise 见证 spec（torch 对标）：一个 in、一个 out，dtype 允许集含 float/int 两类。"""
    return {
        "op": _OP, "verify_mode": "numerical",
        "precision": {"oracle": "torch", "standard": "torch_allclose", "tolerance_source": _TOL_SRC},
        "params": [
            {"name": "x", "io": "in", "dtype": ["float32", "float16", "int32"]},
            {"name": "y", "io": "out", "dtype": ["float32", "float16", "int32"]},
        ],
    }


def _canon(spec, dtype):
    """据 spec 复算该 dtype 的 canonical（与 validator/gen_cases **同源**）→ (eff_std, policy, tpid, digest)。"""
    std = P.select_standard(spec)
    eff = P.effective_standard(std, dtype, None)     # int32 → exact；float → 沿用 torch_allclose
    pol = P.threshold_for(eff, dtype, (spec.get("precision") or {}).get("tolerance_source"))
    return eff, pol, P.tolerance_policy_id(eff, dtype), P.threshold_digest(pol)


def _metric_key(policy):
    """metric key 随 policy.kind：exact→exact_mismatch，其余（torch_allclose / index）→mismatch。"""
    return "exact_mismatch" if policy["kind"] == P.EXACT else "mismatch"


def _case(spec, cid, dtype, dims):
    eff, pol, tpid, dig = _canon(spec, dtype)
    return {"id": cid, "dims": list(dims),
            "inputs": [{"name": "x", "shape": [2, 2], "dtype": dtype, "path": f"{cid}/x.npy"}],
            "attrs": {},
            "expected": {"verify_mode": "numerical", "compare_dtype": dtype, "standard": eff,
                         "tolerance_policy_id": tpid, "policy": pol, "threshold": dig}}


def _ev(spec, cid, dtype, mismatch=0, status="ok", metrics=True):
    eff, pol, tpid, dig = _canon(spec, dtype)
    prec = {"standard": eff, "tolerance_policy_id": tpid, "policy": pol, "threshold": dig}
    if metrics:                                       # metrics=False 模拟「golden 读不了 / 误差没复算」
        prec["metrics"] = {_metric_key(pol): mismatch, "numel": 4}
    return {"case_id": cid, "status": status, "precision": prec}


def _bundle(entries):
    """造 (spec, caseset, evidence)。entries = [(cid, dtype, kw)]，kw 支持：
    `mismatch` / `status` / `metrics`（透传 `_ev`）、`dims`（缺省 功能+精度）、
    `drop_evidence=True`（只进 caseset、不进 evidence = 这条根本没跑）。"""
    spec = _spec()
    cases, evs = [], []
    for cid, dtype, kw in entries:
        kw = dict(kw)
        dims = kw.pop("dims", ("功能", "精度"))
        drop = kw.pop("drop_evidence", False)
        cases.append(_case(spec, cid, dtype, dims))
        if not drop:
            evs.append(_ev(spec, cid, dtype, **kw))
    return spec, {"op": _OP, "cases": cases}, {"op": _OP, "evidence": evs}


def _by_dtype(summary):
    return {r["dtype"]: r for r in summary["by_dtype"]}


# ------------------------------------------------------------- 多输出见证件 ---
def _multi_spec():
    """多输出（value + index）见证 spec：reduce 类的 values/indices 形态，按 out_role/call_variants 驱动。"""
    return {
        "op": "DemoReduce", "verify_mode": "numerical",
        "precision": {"oracle": "torch", "standard": "torch_allclose", "tolerance_source": _TOL_SRC},
        "params": [
            {"name": "self", "io": "in", "dtype": ["float32", "float16"]},
            {"name": "dim", "io": "attr", "dtype": ["int64"]},
            {"name": "keepdim", "io": "attr", "dtype": ["bool"]},
            {"name": "values", "io": "out", "out_role": "value", "dtype": ["<from_input>"]},
            {"name": "indices", "io": "out", "out_role": "index", "index_of": "values",
             "gather_from": "self", "dtype": ["int64"]},
        ],
        "call_variants": [
            {"when": {"attr": "dim", "is_null": True}, "symbol": "DemoReduce",
             "active_attrs": [], "active_outputs": ["values"]},
            {"when": {"attr": "dim", "is_null": False}, "symbol": "DemoReduceDim",
             "active_attrs": ["dim", "keepdim"], "active_outputs": ["values", "indices"]},
        ],
    }


def _multi_bundle(in_dtype="float32", value_mismatch=0, index_mismatch=0, attrs=None, active=None):
    """造多输出 (spec, caseset, evidence)——三件**全部据 spec 派生**，保证「合法产物」这条基线不作弊。"""
    spec = _multi_spec()
    attrs = {"dim": 0, "keepdim": False} if attrs is None else attrs
    cts = P.derive_output_contracts(spec, [("self", in_dtype)], P.TORCH_ALLCLOSE, _TOL_SRC)
    by_name = {c["name"]: c for c in cts}
    names = active if active is not None else [c["name"] for c in cts]
    outs, ev_outs = [], []
    for k, n in enumerate(names):
        c = by_name[n]
        item = {"index": k, "name": c["name"], "role": c["role"], "standard": c["standard"],
                "tolerance_policy_id": c["tolerance_policy_id"], "policy": c["policy"],
                "threshold": P.threshold_digest(c["policy"])}
        mis = index_mismatch if c["role"] == P.OUT_ROLE_INDEX else value_mismatch
        outs.append(item)
        ev_outs.append({**item, "metrics": {_metric_key(c["policy"]): mis, "numel": 4}})
    case = {"id": "m1", "dims": ["功能", "精度"],
            "inputs": [{"name": "self", "shape": [2, 2], "dtype": in_dtype, "path": "m1/x.npy"}],
            "attrs": attrs, "expected": {"verify_mode": "numerical", "outputs": outs}}
    return (spec, {"op": spec["op"], "cases": [case]},
            {"op": spec["op"], "evidence": [
                {"case_id": "m1", "status": "ok", "precision": {"outputs": ev_outs}}]})


# ==================================================== ① 多 dtype 混合分桶 ====
class MixedDtypeBucketTest(unittest.TestCase):
    """多 dtype 混合时逐桶计数 + pass_rate + 总体 overall_pass_rate。"""

    def setUp(self):
        # float32 两条（一过一数值错）、float16 一条过、int32 一条过（int → 有效标准 exact）
        self.v = V.validate(*_bundle([("c1", "float32", {}),
                                      ("c2", "float32", {"mismatch": 3}),
                                      ("c3", "float16", {}),
                                      ("c4", "int32", {})]))
        self.s = self.v["accuracy_summary"]

    def test_buckets_sorted_by_dtype(self):
        """桶按 dtype 名排序（对标 cannbot accuracy.py:689 的 sorted(by_dtype.items())）。"""
        self.assertEqual([r["dtype"] for r in self.s["by_dtype"]], ["float16", "float32", "int32"])

    def test_per_dtype_counts_and_pass_rate(self):
        b = _by_dtype(self.s)
        self.assertEqual((b["float32"]["count"], b["float32"]["passed"], b["float32"]["failed"],
                          b["float32"]["errored"]), (2, 1, 1, 0))
        self.assertEqual(b["float32"]["pass_rate"], 0.5)
        self.assertEqual((b["float16"]["count"], b["float16"]["passed"]), (1, 1))
        self.assertEqual(b["float16"]["pass_rate"], 1.0)
        self.assertEqual((b["int32"]["count"], b["int32"]["passed"]), (1, 1))

    def test_overall_totals(self):
        """total=全部 case、executed=passed+failed、overall_pass_rate=passed/total（cannbot :667-688）。"""
        self.assertEqual(self.s["total"], 4)
        self.assertEqual(self.s["executed"], 4)
        self.assertEqual((self.s["passed"], self.s["failed"], self.s["errored"]), (3, 1, 0))
        self.assertEqual(self.s["overall_pass_rate"], 0.75)

    def test_bucket_counts_sum_to_count(self):
        """不变式：每桶 passed+failed+errored+uncertain+na == count；各桶 count 之和 == total。"""
        tot = 0
        for r in self.s["by_dtype"]:
            self.assertEqual(sum(r[k] for k in V._ACC_BUCKETS), r["count"], r)
            tot += r["count"]
        self.assertEqual(tot, self.s["total"])

    def test_dtype_key_is_output_dtype_not_input(self):
        """dtype 键 = **spec 派生的输出 dtype**（有意偏离 cannbot 的输入 dtype 口径）。

        见证：float→bool 的非同型算子（IsClose/Equal 形态）——输入 float32、输出恒 bool。
        按 cannbot 的 `case["dtype"]` 会记成 float32 桶并回显一份用不上的 float 容差；我方记 bool。"""
        spec = {"op": "DemoCmp", "verify_mode": "exact",
                "precision": {"standard": "exact"},
                "params": [{"name": "x", "io": "in", "dtype": ["float32"]},
                           {"name": "y", "io": "out", "dtype": ["bool"]}]}
        eff, pol, tpid, dig = _canon(spec, "bool")
        exp = {"verify_mode": "exact", "compare_dtype": "bool", "standard": eff,
               "tolerance_policy_id": tpid, "policy": pol, "threshold": dig}
        case = {"id": "b1", "dims": ["功能", "精度"], "attrs": {},
                "inputs": [{"name": "x", "shape": [2, 2], "dtype": "float32", "path": "b1/x.npy"}],
                "expected": exp}
        ev = {"case_id": "b1", "status": "ok",
              "precision": {"standard": eff, "tolerance_policy_id": tpid, "policy": pol,
                            "threshold": dig, "metrics": {"exact_mismatch": 0, "numel": 4}}}
        v = V.validate(spec, {"op": "DemoCmp", "cases": [case]},
                       {"op": "DemoCmp", "evidence": [ev]})
        self.assertEqual(v["overall"]["verdict"], "pass", v)
        self.assertEqual([r["dtype"] for r in v["accuracy_summary"]["by_dtype"]], ["bool"])


# ====================================== ② errored：计入分母、不计入 executed ====
class ErroredBucketTest(unittest.TestCase):
    """errored 三种可分辨来源 + 「计入 total、不计入 executed」。对标 cannbot accuracy.py:648/:656-659/:671。"""

    def test_crashed_and_unreadable_golden_are_errored(self):
        # e1 = status≠ok 且无 metrics（kernel 崩那类）；e2 = 跑了但 metrics 缺（golden 读不了那类）
        v = V.validate(*_bundle([("e1", "float32", {"status": "error", "metrics": False}),
                                 ("e2", "float32", {"metrics": False}),
                                 ("e3", "float32", {})]))
        s = v["accuracy_summary"]
        b = _by_dtype(s)["float32"]
        self.assertEqual((b["count"], b["passed"], b["failed"], b["errored"]), (3, 1, 0, 2))
        # ⭐ 分母含 errored（3），executed 不含（只有那条真跑出可比结果的）
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["executed"], 1)
        self.assertAlmostEqual(s["overall_pass_rate"], 1 / 3)
        self.assertAlmostEqual(b["pass_rate"], 1 / 3)

    def test_missing_evidence_is_errored(self):
        """caseset 里有、evidence 里根本没有 → 这条压根没跑 → errored（不是 failed，也不许不计数）。"""
        v = V.validate(*_bundle([("m1", "float32", {"drop_evidence": True}),
                                 ("m2", "float32", {})]))
        b = _by_dtype(v["accuracy_summary"])["float32"]
        self.assertEqual((b["count"], b["passed"], b["failed"], b["errored"]), (2, 1, 0, 1))

    def test_contract_failure_counted_as_failed_documented(self):
        """**边界钉子**：口径契约不符（caseset 谎报 compare_dtype）当前落 `failed`、**分不出**是契约问题。

        证据侧 status=ok、metrics 齐全 → 按可分辨信号只能判「跑成了但没通过」。这条测的是**现状边界**，
        不是宣称我们已把契约类失败与真数值错分开；真要分开须在裁决路径打标记（本批零行为变更、不做）。"""
        spec, cs, ev = _bundle([("c1", "float32", {})])
        cs["cases"][0]["expected"]["compare_dtype"] = "float16"      # 谎报比对 dtype
        v = V.validate(spec, cs, ev)
        self.assertEqual(v["overall"]["verdict"], "fail")            # 裁决照旧逮住（新块不改判）
        b = _by_dtype(v["accuracy_summary"])["float32"]              # 桶键仍据 **spec 派生**的真 dtype
        self.assertEqual((b["passed"], b["failed"], b["errored"]), (0, 1, 0))


# ============================================== ③ 容差回显与 threshold_for 同源 ====
class ToleranceEchoTest(unittest.TestCase):
    """逐 dtype 回显的 rtol/atol 必须 == `precision_policy.threshold_for` 给的那份（不另算一套）。"""

    def setUp(self):
        self.s = V.validate(*_bundle([("c1", "float32", {}), ("c2", "float16", {}),
                                      ("c3", "int32", {})]))["accuracy_summary"]

    def test_float_tols_match_threshold_for(self):
        b = _by_dtype(self.s)
        for dt in ("float32", "float16"):
            pol = P.threshold_for(P.TORCH_ALLCLOSE, dt, _TOL_SRC)
            self.assertEqual(b[dt]["rtol"], pol["rtol"], dt)
            self.assertEqual(b[dt]["atol"], pol["atol"], dt)

    def test_exact_dtype_echoes_null(self):
        """int32 的有效标准是 exact（无 rtol/atol）→ 回显 null，**不拿别的阈值冒充**。"""
        b = _by_dtype(self.s)["int32"]
        self.assertIsNone(b["rtol"])
        self.assertIsNone(b["atol"])


# ================================================== ④ 多输出：按 value dtype 归桶 ====
class MultiOutputBucketTest(unittest.TestCase):
    """多输出（values+indices）：按 value-role 输出的 dtype 归桶，一条 case 只占一格。"""

    def test_index_dtype_does_not_open_a_bucket(self):
        v = V.validate(*_multi_bundle())
        self.assertEqual(v["overall"]["verdict"], "pass", v)
        s = v["accuracy_summary"]
        self.assertEqual([r["dtype"] for r in s["by_dtype"]], ["float32"])   # 无 int64 桶
        b = _by_dtype(s)["float32"]
        self.assertEqual((b["count"], b["passed"]), (1, 1))
        pol = P.threshold_for(P.TORCH_ALLCLOSE, "float32", _TOL_SRC)
        self.assertEqual((b["rtol"], b["atol"]), (pol["rtol"], pol["atol"]))

    def test_index_output_wrong_makes_the_case_failed(self):
        """index 输出错 → 整案 AND 折叠成 fail → 该 case 记 failed（我方比 cannbot 严：它只比 outputs[0]）。"""
        v = V.validate(*_multi_bundle(index_mismatch=1))
        b = _by_dtype(v["accuracy_summary"])["float32"]
        self.assertEqual((b["count"], b["passed"], b["failed"]), (1, 0, 1))

    def test_single_output_variant_same_bucket(self):
        """变体只落 values（dim=None）→ 仍归 value 的 dtype 桶，count 计 1。"""
        v = V.validate(*_multi_bundle(attrs={"dim": None, "keepdim": False}, active=["values"]))
        self.assertEqual(v["overall"]["verdict"], "pass", v)
        self.assertEqual([r["dtype"] for r in v["accuracy_summary"]["by_dtype"]], ["float32"])


# ================================================== ⑤ na 档：合法地没有精度维 ====
class NaBucketTest(unittest.TestCase):
    def test_perf_only_case_goes_to_na_and_stays_in_denominator(self):
        """纯性能 case（dims=['性能']）没有精度维 → 记 na（不冒充 passed），且**仍在分母里**。"""
        v = V.validate(*_bundle([("p1", "float32", {"dims": ["性能"]}), ("p2", "float32", {})]))
        s = v["accuracy_summary"]
        b = _by_dtype(s)["float32"]
        self.assertEqual((b["count"], b["passed"], b["na"], b["failed"], b["errored"]), (2, 1, 1, 0, 0))
        self.assertEqual((s["total"], s["executed"], s["na"]), (2, 1, 1))
        self.assertEqual(s["overall_pass_rate"], 0.5)


# ============================== ⑥ 只读性：新增块绝不影响裁决 + 字段形状恒定 ====
class ReadOnlyGuaranteeTest(unittest.TestCase):
    def _entries(self):
        return [("c1", "float32", {}), ("c2", "float16", {"mismatch": 1}),
                ("c3", "int32", {"status": "error", "metrics": False})]

    def test_summary_crash_leaves_verdict_byte_identical(self):
        """聚合块内部炸掉 → 出空块，**verdict 其余部分逐字段与正常跑完全一致**（PASS/FAIL、逐 case 结论）。"""
        spec, cs, ev = _bundle(self._entries())
        base = V.validate(copy.deepcopy(spec), copy.deepcopy(cs), copy.deepcopy(ev))

        def _boom(*_a, **_kw):
            raise RuntimeError("聚合块内部炸了（模拟）")

        orig = V._acc_dtype_and_tol
        V._acc_dtype_and_tol = _boom
        try:
            got = V.validate(copy.deepcopy(spec), copy.deepcopy(cs), copy.deepcopy(ev))
        finally:
            V._acc_dtype_and_tol = orig
        self.assertEqual(got["accuracy_summary"], V._empty_accuracy_summary())
        self.assertEqual({k: x for k, x in got.items() if k != "accuracy_summary"},
                         {k: x for k, x in base.items() if k != "accuracy_summary"})

    def test_verdict_and_per_case_conclusions_unchanged(self):
        """同一份输入下的既有裁决口径原样：整体 fail、逐 case 三层结论与精度结论逐条如常。"""
        v = V.validate(*_bundle(self._entries()))
        self.assertEqual(v["overall"]["verdict"], "fail")
        rows = {r["case_id"]: r for r in v["per_case"]}
        self.assertEqual(rows["c1"]["精度"], "pass")
        self.assertEqual(rows["c1"]["acceptance_precision_pass"], "pass")
        self.assertEqual(rows["c2"]["精度"], "fail")
        self.assertEqual(rows["c3"]["功能"], "fail")            # status≠ok
        self.assertEqual(rows["c3"]["精度"], "fail")            # 缺 metrics
        for r in v["per_case"]:                                 # 内部标记不得漏进产物
            self.assertNotIn("_prec_expected", r)

    def test_summary_shape_is_constant_across_paths(self):
        """字段形状恒定：正常裁决与结构性早退（无用例）产出的 `accuracy_summary` 键集一致。"""
        ok = V.validate(*_bundle([("c1", "float32", {})]))["accuracy_summary"]
        dead = V.validate({"op": _OP}, {"op": _OP, "cases": []},
                          {"op": _OP, "evidence": []})["accuracy_summary"]
        self.assertEqual(sorted(ok), sorted(V._empty_accuracy_summary()))
        self.assertEqual(dead, V._empty_accuracy_summary())

    def test_bad_case_falls_into_unknown_bucket_not_crash(self):
        """dtype 派生不出来（inputs 被抹）→ 归显式 `unknown` 桶，既不猜也不让裁决崩。"""
        spec, cs, ev = _bundle([("c1", "float32", {})])
        cs["cases"][0].pop("inputs")
        v = V.validate(spec, cs, ev)
        self.assertEqual(v["overall"]["verdict"], "fail")       # 裁决自己会逮（IO schema 不符）
        self.assertEqual([r["dtype"] for r in v["accuracy_summary"]["by_dtype"]], [V._ACC_UNKNOWN_DTYPE])


if __name__ == "__main__":
    unittest.main(verbosity=2)
