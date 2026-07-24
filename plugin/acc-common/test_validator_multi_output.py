"""validator 多输出逐输出判定 + AND 折叠单测（WI-A2/A5 · torch 对标 median 见证）。

跑: cd plugin/acc-common && python3 test_validator_multi_output.py

覆盖：
  · judge_torch_allclose：mismatch==0→pass、>0→fail、schema 非法→fail；
  · 多输出折叠：value pass + index pass → 精度 pass；任一 fail → fail；
  · 三处口径对齐：caseset/evidence policy 被放宽 → 契约 fail（不静默放过）；
  · **单输出向后兼容**：无 outputs 字段的 legacy case 走原路径、判定不变。

R2-L1 审计负向回归（本轮新增，逐条对应 finding）：
  · 严重#1 —— caseset+evidence 同步**删掉一个输出**、或整个删掉 `outputs` **伪装 legacy** → 必须被逮；
    走不走多输出路径由 **spec** 决定，不由 caseset 自选。
  · 高#2   —— spec 的 `acceptance_policy` 在多输出路径必须**生效**（不再被忽略成 standard）。
  · 中#6   —— 逐输出 `threshold` digest 也要对账（旧实现只核 standard/policy/tpid）。
  · 中#8   —— 合法的「两个 value 输出」算子可判（身份主键=name，不是 role）。
op-中立：全部据 spec/caseset 字段驱动（out_role/policy.kind），无算子名分支。
"""
import copy
import unittest

import precision_policy as P
import validator as V


def _median_spec():
    """见证用 median spec。`gather_from` 必填（finding #7）；`call_variants` 决定本 case 落哪些输出（严重#1）。"""
    return {
        "op": "Median", "verify_mode": "numerical",
        "precision": {"oracle": "torch", "standard": "torch_allclose", "tolerance_source": "dtype_table"},
        "params": [
            {"name": "self", "io": "in", "dtype": ["float32", "float16", "int32"]},
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


def _outputs_from_spec(spec, in_dtype, active=None):
    """据 spec 派生 canonical → 造 caseset.expected.outputs（与 canonical 全等，模拟 gen_cases 产出）。

    `active` = 本 case 落地的输出名序列（默认全部 out 参数）；条目带 `index/name/role` 三元身份 +
    `threshold` digest（finding #6：digest 也在对账范围内）。"""
    cts = P.derive_output_contracts(spec, [("self", in_dtype)], "torch_allclose", "dtype_table")
    accs = P.derive_acceptance_contracts(spec, cts)
    by_name = {c["name"]: (i, c) for i, c in enumerate(cts)}
    names = active if active is not None else [c["name"] for c in cts]
    outs = []
    for k, n in enumerate(names):
        i, c = by_name[n]
        item = {"index": k, "name": c["name"], "role": c["role"], "standard": c["standard"],
                "tolerance_policy_id": c["tolerance_policy_id"], "policy": c["policy"],
                "threshold": P.threshold_digest(c["policy"])}
        if accs is not None and accs[i] is not None:
            item["acceptance_policy"] = accs[i]["policy"]
            item["acceptance_tolerance_policy_id"] = accs[i]["tolerance_policy_id"]
        outs.append(item)
    return outs


def _metric_key(policy):
    """metric key 随 policy.kind：exact→exact_mismatch，torch_allclose/index→mismatch（各自 judge 的入口）。"""
    return "exact_mismatch" if policy["kind"] == "exact" else "mismatch"


def _median_bundle(in_dtype="float32", value_mismatch=0, index_mismatch=0,
                   tamper_caseset=None, attrs=None, active=None,
                   acc_value_mismatch=None, acc_index_mismatch=None, spec=None):
    """造 (spec, caseset, evidence) 三件：单个 median case（默认 by-dim 双输出）。

    `spec` 可外部给（如加了 `precision.acceptance_policy` 的变体）——三件仍**全部据该 spec 派生**，
    保证「合法产物」这条基线本身没作弊。"""
    spec = _median_spec() if spec is None else spec
    attrs = {"dim": 0, "keepdim": False} if attrs is None else attrs
    outs = _outputs_from_spec(spec, in_dtype, active)
    if tamper_caseset:
        tamper_caseset(outs)
    exp = {"verify_mode": "numerical", "outputs": outs}
    case = {"id": "c05", "dims": ["功能", "精度"],
            "inputs": [{"name": "self", "shape": [2, 3], "dtype": in_dtype, "path": "c05/x1.npy"}],
            "attrs": attrs, "expected": exp}
    caseset = {"op": "Median", "cases": [case]}
    ev_outs = []
    for o in outs:
        mis = index_mismatch if o["role"] == "index" else value_mismatch
        ev_o = {"index": o["index"], "name": o["name"], "role": o["role"], "standard": o["standard"],
                "tolerance_policy_id": o["tolerance_policy_id"], "policy": o["policy"],
                "threshold": o["threshold"],
                "metrics": {_metric_key(o["policy"]): mis, "numel": 3}}
        if "acceptance_policy" in o:
            amis = acc_index_mismatch if o["role"] == "index" else acc_value_mismatch
            amis = mis if amis is None else amis
            ev_o["acceptance_policy"] = o["acceptance_policy"]
            ev_o["acceptance_tolerance_policy_id"] = o["acceptance_tolerance_policy_id"]
            ev_o["acceptance_metrics"] = {_metric_key(o["acceptance_policy"]): amis, "numel": 3}
        ev_outs.append(ev_o)
    evidence = {"op": "Median", "evidence": [
        {"case_id": "c05", "status": "ok", "precision": {"outputs": ev_outs}}]}
    return spec, caseset, evidence


class JudgeTorchAllcloseTest(unittest.TestCase):
    def test_mismatch_zero_pass(self):
        self.assertEqual(V.judge_torch_allclose({}, {"mismatch": 0, "numel": 4})[0], "pass")

    def test_mismatch_positive_fail(self):
        self.assertEqual(V.judge_torch_allclose({}, {"mismatch": 1, "numel": 4})[0], "fail")

    def test_schema_bad_fail(self):
        for m in ({}, {"mismatch": -1, "numel": 4}, {"mismatch": 0, "numel": 0},
                  {"mismatch": True, "numel": 4}, None):
            self.assertEqual(V.judge_torch_allclose({}, m)[0], "fail", m)

    def test_registered_for_both_kinds(self):
        self.assertIs(V._JUDGES[P.TORCH_ALLCLOSE], V.judge_torch_allclose)
        self.assertIs(V._JUDGES[P.INDEX_VALUE_CONSISTENCY], V.judge_torch_allclose)


class MultiOutputFoldTest(unittest.TestCase):
    def _per(self, verdict):
        return verdict["per_case"][0]

    def test_value_pass_index_pass_overall_pass(self):
        v = V.validate(*_median_bundle())
        self.assertEqual(v["overall"]["verdict"], "pass", v)
        self.assertEqual(self._per(v)["精度"], "pass")
        self.assertEqual(self._per(v)["功能"], "pass")

    def test_value_fail_overall_fail(self):
        v = V.validate(*_median_bundle(value_mismatch=2))
        self.assertEqual(v["overall"]["verdict"], "fail")
        self.assertEqual(self._per(v)["精度"], "fail")

    def test_index_fail_overall_fail(self):
        v = V.validate(*_median_bundle(index_mismatch=1))
        self.assertEqual(v["overall"]["verdict"], "fail")
        self.assertEqual(self._per(v)["精度"], "fail")

    def test_int_median_value_exact(self):
        v = V.validate(*_median_bundle(in_dtype="int32"))
        self.assertEqual(v["overall"]["verdict"], "pass", v)

    def test_global_median_single_output_subset(self):
        """全局 median（dim=None）只出 value 输出——**由 spec 的 call_variants 声明**，不是 caseset 自己少报。"""
        v = V.validate(*_median_bundle(attrs={"dim": None, "keepdim": False}, active=["values"]))
        self.assertEqual(v["overall"]["verdict"], "pass", v)

    def test_relaxed_policy_caught(self):
        """caseset+evidence 同步放宽 value policy（rtol 撑大）→ 与 spec-canonical 不符 → 契约 fail。"""
        def tamper(outs):
            outs[0]["policy"] = dict(outs[0]["policy"], rtol=1.0)
        spec, caseset, evidence = _median_bundle(tamper_caseset=tamper)
        evidence["evidence"][0]["precision"]["outputs"][0]["policy"] = \
            dict(evidence["evidence"][0]["precision"]["outputs"][0]["policy"], rtol=1.0)
        v = V.validate(spec, caseset, evidence)
        self.assertEqual(v["overall"]["verdict"], "fail")
        self.assertIn("口径", self._per(v)["判据"])

    def test_evidence_outputs_length_mismatch_fail(self):
        spec, caseset, evidence = _median_bundle()
        evidence["evidence"][0]["precision"]["outputs"].pop()   # 删掉 index 输出 evidence
        v = V.validate(spec, caseset, evidence)
        self.assertEqual(v["overall"]["verdict"], "fail")

    def test_fabricated_role_rejected(self):
        """caseset 伪造一个 spec 未声明的角色 → 拒。"""
        def tamper(outs):
            outs.append(dict(outs[0], index=2, name="bogus", role="bogus"))
        spec, caseset, evidence = _median_bundle(tamper_caseset=tamper)
        bogus_ev = dict(evidence["evidence"][0]["precision"]["outputs"][0], name="bogus", role="bogus")
        evidence["evidence"][0]["precision"]["outputs"].append(bogus_ev)
        v = V.validate(spec, caseset, evidence)
        self.assertEqual(v["overall"]["verdict"], "fail")


# ================== 严重#1 · 门旁路负向回归：输出集由 spec 派生，caseset 无权自报 ==================
class ActiveOutputsAuthorityTest(unittest.TestCase):
    """⭐ 旧洞：本 case 该有哪些输出由 caseset 自报 → **删掉一个输出即假通过**。"""

    def _per(self, v):
        return v["per_case"][0]

    def test_dropping_one_output_on_both_sides_is_caught(self):
        """caseset **和** evidence 同步删掉 indices（攻击者两侧一起改）→ 必须 fail，不是 pass。"""
        spec, caseset, evidence = _median_bundle()
        caseset["cases"][0]["expected"]["outputs"].pop()
        evidence["evidence"][0]["precision"]["outputs"].pop()
        v = V.validate(spec, caseset, evidence)
        self.assertEqual(v["overall"]["verdict"], "fail", v)
        self.assertIn("spec 据本 case attrs 派生", self._per(v)["判据"])

    def test_deleting_outputs_to_fake_legacy_is_caught(self):
        """⭐ 旧洞：把 `outputs` 整个删掉伪装成 legacy 单输出 → 判据链整条消失却一路绿。现在直接拒。"""
        spec, caseset, evidence = _median_bundle()
        caseset["cases"][0]["expected"].pop("outputs")
        evidence["evidence"][0]["precision"].pop("outputs")
        # 补一份看似完整的 legacy 顶层口径（攻击者会这么做）
        pol = P.threshold_for("torch_allclose", "float32")
        caseset["cases"][0]["expected"].update(
            standard="torch_allclose", compare_dtype="float32",
            tolerance_policy_id="torch_allclose:float32", policy=pol,
            threshold=P.threshold_digest(pol))
        evidence["evidence"][0]["precision"].update(
            standard="torch_allclose", tolerance_policy_id="torch_allclose:float32",
            policy=pol, threshold=P.threshold_digest(pol),
            metrics={"mismatch": 0, "numel": 3})
        v = V.validate(spec, caseset, evidence)
        self.assertEqual(v["overall"]["verdict"], "fail", v)
        self.assertIn("outputs", self._per(v)["判据"])

    def test_adding_an_extra_output_is_caught(self):
        spec, caseset, evidence = _median_bundle(attrs={"dim": None, "keepdim": False},
                                                 active=["values"])
        # 变体声明只落 values，caseset/evidence 却多报一项
        extra = copy.deepcopy(caseset["cases"][0]["expected"]["outputs"][0])
        extra["index"] = 1
        caseset["cases"][0]["expected"]["outputs"].append(extra)
        evidence["evidence"][0]["precision"]["outputs"].append(
            copy.deepcopy(evidence["evidence"][0]["precision"]["outputs"][0]))
        v = V.validate(spec, caseset, evidence)
        self.assertEqual(v["overall"]["verdict"], "fail", v)

    def test_reordering_outputs_is_caught(self):
        """身份/顺序被动过 → 拒（name 逐位对齐 spec 派生序）。"""
        spec, caseset, evidence = _median_bundle()
        caseset["cases"][0]["expected"]["outputs"].reverse()
        evidence["evidence"][0]["precision"]["outputs"].reverse()
        v = V.validate(spec, caseset, evidence)
        self.assertEqual(v["overall"]["verdict"], "fail", v)

    def test_single_output_spec_cannot_smuggle_outputs_list(self):
        """反向伪装：spec 是单输出契约，caseset 私带 outputs[] 想换判据通路 → 拒。"""
        spec = {"op": "Sign", "verify_mode": "exact", "precision": {"oracle": "ascendoptest"},
                "params": [{"name": "x", "io": "in", "dtype": ["float32"]},
                           {"name": "y", "io": "out", "dtype": ["float32"]}]}
        pol = P.threshold_for("exact", "float32")
        case = {"id": "c01", "dims": ["功能", "精度"],
                "inputs": [{"name": "x", "shape": [4], "dtype": "float32", "path": "c01/x1.npy"}],
                "attrs": {}, "expected": {"verify_mode": "exact", "outputs": [
                    {"index": 0, "name": "y", "role": "value", "standard": "exact",
                     "tolerance_policy_id": "exact", "policy": pol,
                     "threshold": P.threshold_digest(pol)}]}}
        caseset = {"op": "Sign", "cases": [case]}
        evidence = {"op": "Sign", "evidence": [{"case_id": "c01", "status": "ok", "precision": {
            "outputs": [{"index": 0, "name": "y", "role": "value", "standard": "exact",
                         "tolerance_policy_id": "exact", "policy": pol,
                         "threshold": P.threshold_digest(pol),
                         "metrics": {"exact_mismatch": 0, "numel": 4}}]}}]}
        v = V.validate(spec, caseset, evidence)
        self.assertEqual(v["overall"]["verdict"], "fail", v)
        self.assertIn("私带 outputs[]", v["per_case"][0]["判据"])


# ================== 高#2 · 多输出 acceptance_policy 必须生效 ==================
class MultiOutputAcceptanceTest(unittest.TestCase):
    def _per(self, v):
        return v["per_case"][0]

    @staticmethod
    def _acc_spec():
        spec = _median_spec()
        spec["precision"]["acceptance_policy"] = {"standard": "torch_allclose"}
        return spec

    def test_acceptance_declared_and_honored(self):
        """spec 声明 acceptance → 逐输出按 acceptance 判；acceptance 不过 → 精度 fail（旧实现整个忽略）。"""
        v = V.validate(*_median_bundle(spec=self._acc_spec()))          # acceptance 全过
        self.assertEqual(v["overall"]["verdict"], "pass", v)
        # acceptance 侧有失配（standard 侧仍 0）→ 放行只看 acceptance → 精度 fail
        v2 = V.validate(*_median_bundle(spec=self._acc_spec(), acc_value_mismatch=3))
        self.assertEqual(v2["overall"]["verdict"], "fail", v2)
        self.assertEqual(self._per(v2)["acceptance_precision_pass"], "fail")
        self.assertEqual(self._per(v2)["standard_profile_pass"], "pass")

    def test_acceptance_declared_but_caseset_silent_is_caught(self):
        """⭐ 旧洞：多输出路径直接令 acceptance=standard → spec 声明了更严口径也照样按平台 standard 放行。

        现在 spec 声明了、caseset/evidence 却不带 → fail-closed（不是「当没声明」放行）。"""
        _, caseset, evidence = _median_bundle()                # 先按无 acceptance 造合法三件
        spec = self._acc_spec()                                # spec 声明 acceptance
        v = V.validate(spec, caseset, evidence)
        self.assertEqual(v["overall"]["verdict"], "fail", v)
        self.assertIn("acceptance", self._per(v)["判据"])

    def test_acceptance_injected_when_spec_silent_is_caught(self):
        """spec 没声明 acceptance，caseset/evidence 私带 → 拒（防 T5 洞在多输出层重演）。"""
        spec, caseset, evidence = _median_bundle()
        loose = {"kind": "torch_allclose", "rtol": 1.0, "atol": 1.0, "equal_nan": True}
        caseset["cases"][0]["expected"]["outputs"][0]["acceptance_policy"] = loose
        evidence["evidence"][0]["precision"]["outputs"][0]["acceptance_policy"] = loose
        v = V.validate(spec, caseset, evidence)
        self.assertEqual(v["overall"]["verdict"], "fail", v)
        self.assertIn("私带", self._per(v)["判据"])


# ================== 中#6 · 逐输出 threshold digest 必须对账 ==================
class OutputDigestContractTest(unittest.TestCase):
    def test_tampered_digest_on_both_sides_is_caught(self):
        """⭐ 旧洞：多输出不核 threshold → 两侧同步填任意 digest 也过。"""
        spec, caseset, evidence = _median_bundle()
        caseset["cases"][0]["expected"]["outputs"][0]["threshold"] = [9.0, 9.0]
        evidence["evidence"][0]["precision"]["outputs"][0]["threshold"] = [9.0, 9.0]
        v = V.validate(spec, caseset, evidence)
        self.assertEqual(v["overall"]["verdict"], "fail", v)
        self.assertIn("threshold", v["per_case"][0]["判据"])

    def test_json_roundtrip_digest_still_matches(self):
        """digest 落 JSON 再读回来（list）仍与 canonical 相等——旧的 tuple 返回值在这里必挂（finding #6）。"""
        import json as _json
        spec, caseset, evidence = _median_bundle()
        caseset = _json.loads(_json.dumps(caseset))
        evidence = _json.loads(_json.dumps(evidence))
        v = V.validate(spec, caseset, evidence)
        self.assertEqual(v["overall"]["verdict"], "pass", v)


# ================== 中#8 · 两个 value 输出的算子必须可判 ==================
class TwoValueOutputsJudgeTest(unittest.TestCase):
    SPEC = {"op": "TwoVal", "verify_mode": "numerical",
            "precision": {"oracle": "torch", "standard": "torch_allclose",
                          "tolerance_source": "dtype_table"},
            "params": [{"name": "self", "io": "in", "dtype": ["float32"]},
                       {"name": "out_a", "io": "out", "out_role": "value", "dtype": ["<from_input>"]},
                       {"name": "out_b", "io": "out", "out_role": "value", "dtype": ["float32"]}]}

    def _bundle(self, mism_a=0, mism_b=0):
        spec = copy.deepcopy(self.SPEC)
        cts = P.derive_output_contracts(spec, [("self", "float32")], "torch_allclose", "dtype_table")
        outs = [{"index": k, "name": c["name"], "role": c["role"], "standard": c["standard"],
                 "tolerance_policy_id": c["tolerance_policy_id"], "policy": c["policy"],
                 "threshold": P.threshold_digest(c["policy"])} for k, c in enumerate(cts)]
        case = {"id": "t0", "dims": ["功能", "精度"],
                "inputs": [{"name": "self", "shape": [3], "dtype": "float32", "path": "t0/x1.npy"}],
                "attrs": {}, "expected": {"verify_mode": "numerical", "outputs": outs}}
        ev_outs = [dict(o, metrics={"mismatch": m, "numel": 3})
                   for o, m in zip(outs, (mism_a, mism_b))]
        return (spec, {"op": "TwoVal", "cases": [case]},
                {"op": "TwoVal", "evidence": [{"case_id": "t0", "status": "ok",
                                               "precision": {"outputs": ev_outs}}]})

    def test_two_values_all_pass(self):
        """⭐ 旧洞：按 role 建唯一映射 → 合法的「两个 value」算子被直接拒。"""
        v = V.validate(*self._bundle())
        self.assertEqual(v["overall"]["verdict"], "pass", v)

    def test_second_value_fail_folds_to_fail(self):
        v = V.validate(*self._bundle(mism_b=1))
        self.assertEqual(v["overall"]["verdict"], "fail", v)
        self.assertIn("out_b", v["per_case"][0]["判据"])


class SingleOutputBackwardCompatTest(unittest.TestCase):
    """现有 4 算子（单输出 elementwise）无 outputs 字段 → 走 legacy 路径、判定完全不变。"""

    def _sign_bundle(self, mismatch=0):
        spec = {"op": "Sign", "verify_mode": "exact",
                "precision": {"oracle": "ascendoptest"},
                "params": [{"name": "x", "io": "in", "dtype": ["float32"]},
                           {"name": "y", "io": "out", "dtype": ["float32"]}]}
        pol = P.threshold_for("exact", "float32")
        exp = {"verify_mode": "exact", "standard": "exact", "compare_dtype": "float32",
               "compare": "exact_equal", "tolerance_policy_id": "exact", "policy": pol,
               "threshold": P.threshold_digest(pol)}
        case = {"id": "c01", "dims": ["功能", "精度"],
                "inputs": [{"name": "x", "shape": [4], "dtype": "float32", "path": "c01/x1.npy"}],
                "attrs": {}, "expected": exp}
        caseset = {"op": "Sign", "cases": [case]}
        evidence = {"op": "Sign", "evidence": [{"case_id": "c01", "status": "ok",
                    "precision": {"standard": "exact", "tolerance_policy_id": "exact",
                                  "policy": pol, "threshold": P.threshold_digest(pol),
                                  "oracle_source": "analytical_ref",
                                  "metrics": {"exact_mismatch": mismatch, "numel": 4}}}]}
        return spec, caseset, evidence

    def test_single_output_pass(self):
        v = V.validate(*self._sign_bundle(mismatch=0))
        self.assertEqual(v["overall"]["verdict"], "pass", v)

    def test_single_output_fail(self):
        v = V.validate(*self._sign_bundle(mismatch=1))
        self.assertEqual(v["overall"]["verdict"], "fail")


if __name__ == "__main__":
    unittest.main()
