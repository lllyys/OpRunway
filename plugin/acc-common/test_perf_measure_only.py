#!/usr/bin/env python3
"""`perf.mode=measure_only`（AGENTS.md §5.10 · 只测不比）单测。

本文件的**首要断言**不是「measure_only 能跑通」，而是：

    measure_only 是「不做对比」，**不是**「不做测量」——
    只要有一条性能 case 缺真实 `npu_us`，验收门就必须 BLOCKED。

`FailClosedSelfProofTest` 逐条钉死这一点（含「伪造一份看起来干净的 measured 报告」这种绕法）。
另有 `RatioGatedUnaffectedTest` 钉死缺省档（字段不存在 = ratio_gated）行为与改动前一致。

跑: python3 -m unittest test_perf_measure_only -v   （在 acc-common/ 下）
"""
import copy
import json
import os
import shutil
import tempfile
import unittest

import gen_cases as GC
import perf_compare as PC
import perf_mode as PM
import run_workflow as W
import validate_acceptance_state as G
import _golden_fixture as _gf

setUpModule = _gf.install
tearDownModule = _gf.uninstall

_HERE = os.path.dirname(os.path.abspath(__file__))
_SPECS = os.path.join(_HERE, "..", "samples", "specs")


def _w(d, name, obj):
    with open(os.path.join(d, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


# ————————————————— 契约层：perf.mode 解析 —————————————————
class PerfModeContractTest(unittest.TestCase):
    def test_absent_field_is_ratio_gated(self):
        self.assertEqual(PM.resolve_spec_mode({}), PM.MODE_RATIO_GATED)
        self.assertEqual(PM.resolve_spec_mode({"perf": {"baseline": "tbe"}}),
                         PM.MODE_RATIO_GATED)
        self.assertEqual(PM.resolve_spec_mode({"perf": {"mode": "ratio_gated"}}),
                         PM.MODE_RATIO_GATED)

    def test_measure_only_is_recognized(self):
        self.assertEqual(PM.resolve_spec_mode({"perf": {"mode": "measure_only"}}),
                         PM.MODE_MEASURE_ONLY)
        self.assertTrue(PM.is_measure_only(PM.MODE_MEASURE_ONLY))

    def test_unknown_mode_fails_closed(self):
        for bad in ("MEASURE_ONLY", "measure", "", 1, True, [], {}):
            with self.subTest(mode=bad), self.assertRaises(ValueError):
                PM.resolve_spec_mode({"perf": {"mode": bad}})

    def test_measure_only_forbids_any_comparison_config(self):
        """「只测不比」+「对照物/阈值/基线采集配置」= 自相矛盾 → 报错，绝不忽略多余字段。"""
        for key, value in (("baseline", "torch_npu"), ("baseline", None),
                           ("target_ratio", 1.0), ("target_ratio", None),
                           ("small_shape_exception", {"when_us_below": 10,
                                                      "abs_gap_us_within": 3}),
                           ("torch_baseline", {"api": "torch.median"}),
                           ("aclnn_baseline", {"library": "cann_builtin_libopapi"})):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "自相矛盾"):
                PM.resolve_spec_mode({"perf": {"mode": "measure_only", key: value}})

    def test_policy_mode_reads_caseset_ledger(self):
        self.assertEqual(PM.policy_mode(None), PM.MODE_RATIO_GATED)
        self.assertEqual(PM.policy_mode({}), PM.MODE_RATIO_GATED)
        self.assertEqual(PM.policy_mode({"mode": "measure_only"}), PM.MODE_MEASURE_ONLY)
        with self.assertRaises(ValueError):
            PM.policy_mode({"mode": "whatever"})


# ————————————————— Task1：账本落 mode + 大小 shape 边界来源 —————————————————
def _policy_spec(mode=None, hardware="Atlas A3", limit=262144, source=None):
    perf = {"case_source": "precision_cases",
            "shape_classification": {"metric": "sum_input_bytes",
                                     "small_max_bytes": limit, "hardware": hardware}}
    if mode is not None:
        perf["mode"] = mode
    if source is not None:
        perf["shape_classification"]["source"] = source
    return {"perf": perf}


class PerfCasePolicyLedgerTest(unittest.TestCase):
    def test_ratio_gated_ledger_has_no_new_keys(self):
        """缺省档产出的账本**一个新字段都不多**——既有 spec 的 caseset 逐字节不变。"""
        policy = GC._perf_case_policy(_policy_spec())
        self.assertNotIn("mode", policy)
        self.assertNotIn("source", policy["shape_classification"])
        self.assertEqual(
            policy["shape_classification"],
            {"metric": "sum_input_bytes", "small_max_bytes": 262144,
             "boundary": "small_if_input_bytes_lte_limit", "hardware": "Atlas A3"})
        # 显式写 ratio_gated 与省略等价，同样不多写字段。
        self.assertNotIn("mode", GC._perf_case_policy(_policy_spec(mode="ratio_gated")))

    def test_measure_only_is_recorded_in_ledger(self):
        policy = GC._perf_case_policy(_policy_spec(mode="measure_only"))
        self.assertEqual(policy["mode"], "measure_only")

    def test_unknown_hardware_still_fails_without_explicit_spec_supplied(self):
        with self.assertRaisesRegex(ValueError, "尚无受控大小 shape profile"):
            GC._perf_case_policy(_policy_spec(hardware="Ascend 950PR"))

    def test_spec_supplied_unlocks_hardware_without_controlled_profile(self):
        policy = GC._perf_case_policy(
            _policy_spec(hardware="Ascend 950PR", limit=262144, source="spec_supplied"))
        self.assertEqual(policy["shape_classification"]["source"], "spec_supplied")
        self.assertEqual(policy["shape_classification"]["hardware"], "Ascend 950PR")

    def test_spec_supplied_cannot_override_known_hardware_profile(self):
        """表里有该硬件时，spec 改不动我们已核定的事实——`spec_supplied` 不是宽档开关。"""
        with self.assertRaises(ValueError):
            GC._perf_case_policy(
                _policy_spec(hardware="Atlas A3", limit=1048576, source="spec_supplied"))

    def test_unknown_source_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "source"):
            GC._perf_case_policy(_policy_spec(source="whatever"))


# ————————————————— Task3：perf_compare 只测不比 —————————————————
def _mo_spec():
    return {"op": "X", "perf": {"mode": "measure_only", "case_source": "precision_cases",
                                "shape_classification": {
                                    "metric": "sum_input_bytes", "small_max_bytes": 262144,
                                    "hardware": "Atlas A3"}}}


def _mo_caseset():
    def case(cid, cls, tag):
        return {"id": cid, "dims": ["精度", "性能"], "tags": [tag],
                "inputs": [{"name": "a", "shape": [16], "dtype": "float32"}],
                "perf_shape_classification": {
                    "class": cls, "input_bytes": 64, "metric": "sum_input_bytes",
                    "small_max_bytes": 262144,
                    "boundary": "small_if_input_bytes_lte_limit",
                    "hardware": "Atlas A3"}}
    return {"op": "X",
            "cases": [case("s0", "small", "小shape"), case("b0", "large", "大shape")],
            "perf_case_policy": {
                "mode": "measure_only",
                "case_source": "precision_cases",
                "shape_classification": {"metric": "sum_input_bytes", "small_max_bytes": 262144,
                                         "boundary": "small_if_input_bytes_lte_limit",
                                         "hardware": "Atlas A3"},
                "counts": {"small": 1, "large": 1}}}


def _mo_evidence(s0_us=1.0, b0_us=3.0, scope="kernel_only"):
    return {"op": "X", "evidence": [
        {"case_id": "s0", "perf": {"us": s0_us, "scope": scope}},
        {"case_id": "b0", "perf": {"us": b0_us, "scope": scope}}]}


class MeasureOnlyReportTest(unittest.TestCase):
    def test_report_carries_measurements_and_no_verdict_vocabulary(self):
        report = PC.perf_compare(_mo_spec(), _mo_caseset(), _mo_evidence(), None)
        self.assertEqual(report["perf_mode"], "measure_only")
        self.assertEqual(report["summary"]["status"], "measured")
        self.assertEqual(report["summary"]["measured"], 2)
        self.assertEqual(report["summary"]["blocked"], 0)
        self.assertEqual(report["baseline_source"], None)
        self.assertEqual(report["target_ratio"], None)
        # ★ 一个比值裁决词都不许出现
        for key in ("by_dtype", "overall_speedup", "non_passing_cases",
                    "by_shape_class", "shape_overall", "simulation"):
            self.assertNotIn(key, report)
        for key in ("达标", "cases_above_threshold", "cases_scored"):
            self.assertNotIn(key, report["summary"])
        for row in report["per_case"]:
            for key in ("ratio", "达标", "baseline", "exception"):
                self.assertNotIn(key, row)
        self.assertEqual({r["case_id"]: r["npu_us"] for r in report["per_case"]},
                         {"s0": 1.0, "b0": 3.0})
        self.assertTrue(report["measured_shape_complete"])
        self.assertEqual(
            {r["class"]: r["measured"] for r in report["measured_by_shape_class"]},
            {"small": 1, "large": 1})
        self.assertEqual(report["measured_shape_overall"]["npu_us"], 2.0)
        self.assertEqual(report["measured_by_dtype"],
                         [{"dtype": "float32", "count": 2, "npu_us": 2.0,
                           "comparison": "no_baseline_measured_only"}])
        self.assertTrue(any(PM.MEASURE_ONLY_NOTE == n for n in report["notes"]))

    def test_missing_measurement_becomes_blocked_not_measured(self):
        report = PC.perf_compare(_mo_spec(), _mo_caseset(), _mo_evidence(s0_us=None), None)
        self.assertEqual(report["summary"]["status"], "blocked")
        self.assertEqual(report["summary"]["blocked"], 1)
        self.assertEqual(report["summary"]["measured"], 1)

    def test_bad_scope_becomes_blocked(self):
        report = PC.perf_compare(_mo_spec(), _mo_caseset(), _mo_evidence(scope=None), None)
        self.assertEqual(report["summary"]["status"], "blocked")
        self.assertEqual(report["summary"]["blocked"], 2)

    def test_measure_only_refuses_to_consume_a_baseline(self):
        baseline = {"source": "tbe", "scope": "kernel_only",
                    "per_case": [{"case_id": "s0", "us": 1.0}]}
        report = PC.perf_compare(_mo_spec(), _mo_caseset(), _mo_evidence(), baseline)
        self.assertEqual(report["summary"]["status"], "invalid")

    def test_illegal_spec_becomes_invalid_report_not_crash(self):
        spec = _mo_spec()
        spec["perf"]["target_ratio"] = 1.0
        report = PC.perf_compare(spec, _mo_caseset(), _mo_evidence(), None)
        self.assertEqual(report["summary"]["status"], "invalid")


# ————————————————— ★ 验收门：fail-closed 自证 —————————————————
class FailClosedSelfProofTest(unittest.TestCase):
    """本类是本次改动的**首要验收标准**：measure_only 绝不放行「没有实测」的性能维。"""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cs = _mo_caseset()
        _w(self.d, "caseset.json", self.cs)
        _w(self.d, "evidence.json", _mo_evidence())
        self.report = PC.perf_compare(_mo_spec(), self.cs, _mo_evidence(), None)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _errs(self, report=None):
        _w(self.d, "perf_report.json", report if report is not None else self.report)
        errs = []
        G.gate_task3(self.d, errs)
        return errs

    def test_full_measurement_passes(self):
        """基线对照：两条 case 都有真实 npu_us + kernel_only → 门放行（否则下面的负例没意义）。"""
        self.assertEqual(self._errs(), [])

    def test_one_case_without_npu_us_is_blocked(self):
        """★ 缺一条实测 → BLOCKED。"""
        _w(self.d, "evidence.json", _mo_evidence(s0_us=None))
        report = PC.perf_compare(_mo_spec(), self.cs, _mo_evidence(s0_us=None), None)
        errs = self._errs(report)
        self.assertTrue(errs)
        self.assertTrue(any("blocked" in e for e in errs), errs)
        self.assertTrue(any("不做对比" in e for e in errs), errs)

    def test_all_cases_without_npu_us_is_blocked(self):
        """★ 一条 msprof 数据都没有 → 绝不放行（这是最容易做成 fail-open 的形态）。"""
        ev = _mo_evidence(s0_us=None, b0_us=None)
        _w(self.d, "evidence.json", ev)
        errs = self._errs(PC.perf_compare(_mo_spec(), self.cs, ev, None))
        self.assertTrue(errs)

    def test_forged_measured_status_with_null_npu_us_is_blocked(self):
        """★ 手搓一份「status=measured 但 npu_us=None」的干净报告 → 仍 BLOCKED。"""
        ev = _mo_evidence(s0_us=None, b0_us=None)
        _w(self.d, "evidence.json", ev)
        forged = copy.deepcopy(self.report)
        for row in forged["per_case"]:
            row["npu_us"] = None
            row["blocked"] = False
        forged["summary"]["measured"] = 2
        errs = self._errs(forged)
        self.assertTrue(any("npu_us" in e for e in errs), errs)

    def test_forged_empty_per_case_is_blocked(self):
        """★ 直接把性能行删光 → 「跑子集」告警 + status 自相矛盾，不得放行。"""
        forged = copy.deepcopy(self.report)
        forged["per_case"] = []
        forged["summary"] = {"perf_cases": 0, "measured": 0, "blocked": 0, "status": "measured"}
        errs = self._errs(forged)
        self.assertTrue(errs)
        self.assertTrue(any("per_case 为空" in e for e in errs), errs)

    def test_hollow_evidence_payload_is_blocked(self):
        """★ evidence 只剩 case_id 空壳（报告自报有数）→ 双向绑定把它挡下。"""
        _w(self.d, "evidence.json", {"op": "X", "evidence": [
            {"case_id": "s0"}, {"case_id": "b0"}]})
        errs = self._errs()
        self.assertTrue(any("空壳" in e or "evidence.perf.us" in e for e in errs), errs)

    def test_measured_status_on_ratio_gated_caseset_is_rejected(self):
        """★ 用 measured 这个宽档 status 去绕 ratio 通路的达标核对 → 拒绝。"""
        cs = copy.deepcopy(self.cs)
        cs["perf_case_policy"].pop("mode")
        _w(self.d, "caseset.json", cs)
        errs = self._errs()
        self.assertTrue(any("不是 measure_only" in e for e in errs), errs)

    def test_ratio_verdict_smuggled_into_measure_only_report_is_rejected(self):
        forged = copy.deepcopy(self.report)
        forged["target_ratio"] = 1.0
        forged["per_case"][0]["ratio"] = 2.0
        forged["per_case"][0]["达标"] = True
        errs = self._errs(forged)
        self.assertTrue(any("target_ratio" in e for e in errs), errs)
        self.assertTrue(any("ratio" in e for e in errs), errs)

    def test_wrong_scope_is_rejected(self):
        ev = _mo_evidence(scope="host_e2e_with_h2d_d2h")
        _w(self.d, "evidence.json", ev)
        errs = self._errs(PC.perf_compare(_mo_spec(), self.cs, ev, None))
        self.assertTrue(errs)

    def test_baseline_artifact_present_is_rejected(self):
        _w(self.d, "baseline.json", {"source": "tbe", "scope": "kernel_only", "per_case": []})
        errs = self._errs()
        self.assertTrue(any("baseline.json" in e for e in errs), errs)

    def test_tampered_shape_bucket_is_rejected(self):
        forged = copy.deepcopy(self.report)
        forged["measured_by_shape_class"][0]["measured"] = 5
        errs = self._errs(forged)
        self.assertTrue(any("measured_by_shape_class" in e for e in errs), errs)


# ————————————————— 端到端：run_workflow 终态 + 缺省档零影响 —————————————————
def _spec_file(d, name, mutate):
    with open(os.path.join(_SPECS, "isclose.spec.json"), encoding="utf-8") as f:
        spec = json.load(f)
    mutate(spec)
    path = os.path.join(d, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False)
    return path


def _to_measure_only(spec):
    perf = spec["perf"]
    for key in ("baseline", "target_ratio", "small_shape_exception"):
        perf.pop(key, None)
    perf["mode"] = "measure_only"


class MeasureOnlyWorkflowTest(unittest.TestCase):
    """走 mock 通路（非验收，物理不产 acceptance.json）验证编排层终态与措辞。"""

    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_measure_only_run_reaches_measured_terminal_state(self):
        path = _spec_file(self.d, "mo.spec.json", _to_measure_only)
        res = W.run(path, mode="mock", out_dir=os.path.join(self.d, "out"))
        self.assertEqual(res["overall"], "PASS(性能仅实测未裁决)")
        self.assertEqual(res["state"], "PASSED_PRECISION_PERF_MEASURED_ONLY")
        self.assertEqual(res["exit_code"], 0)
        self.assertTrue(res["gate"]["passed"], res["gate"])
        with open(os.path.join(self.d, "out", "perf_report.json"), encoding="utf-8") as f:
            perf = json.load(f)
        self.assertEqual(perf["summary"]["status"], "measured")
        self.assertGreater(perf["summary"]["perf_cases"], 0)
        self.assertEqual(perf["summary"]["measured"], perf["summary"]["perf_cases"])
        # 措辞红线：终态串不许出现「达标」；报告里除了那句「不产任何性能达标结论」的**免责声明**
        # 之外，不得有任何达标/比值**字段**（免责声明本身当然可以提这两个字）。
        self.assertNotIn("达标", res["overall"])

        def _keys(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield key
                    yield from _keys(value)
            elif isinstance(node, list):
                for item in node:
                    yield from _keys(item)
        forbidden = {"达标", "ratio", "target_ratio_met", "speedup",
                     "cases_above_threshold", "baseline_us", "overall_speedup"}
        self.assertEqual(forbidden.intersection(_keys(perf)), set())
        self.assertEqual(perf["perf_mode"], "measure_only")
        self.assertIn(PM.MEASURE_ONLY_NOTE, perf["notes"])
        # 采集计划已落，且**没有** baseline 键。
        with open(os.path.join(self.d, "out", "work", "_perf_plan.json"),
                  encoding="utf-8") as f:
            plan = json.load(f)
        self.assertEqual(plan["mode"], "measure_only")
        self.assertNotIn("baseline", plan)
        self.assertFalse(os.path.exists(os.path.join(self.d, "out", "baseline.json")))

    def test_contradictory_spec_is_rejected_before_any_side_effect(self):
        def mutate(spec):
            _to_measure_only(spec)
            spec["perf"]["baseline"] = "tbe"
        path = _spec_file(self.d, "bad.spec.json", mutate)
        out = os.path.join(self.d, "never")
        with self.assertRaises(SystemExit):
            W.run(path, mode="mock", out_dir=out)
        self.assertFalse(os.path.exists(out), "配置自相矛盾须停在零副作用处")

    def test_gpu_baseline_argument_is_refused_under_measure_only(self):
        path = _spec_file(self.d, "mo2.spec.json", _to_measure_only)
        with self.assertRaisesRegex(SystemExit, "自相矛盾"):
            W.run(path, mode="mock", out_dir=os.path.join(self.d, "out2"),
                  gpu_baseline=os.path.join(self.d, "nope.json"))


class RatioGatedUnaffectedTest(unittest.TestCase):
    """缺省档（`perf.mode` 字段不存在）行为与改动前一致——既有 spec 零影响。"""

    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_default_branch_still_produces_ratio_report(self):
        out = os.path.join(self.d, "out")
        res = W.run(os.path.join(_SPECS, "isclose.spec.json"), mode="mock", out_dir=out)
        self.assertEqual(res["exit_code"], 0)
        with open(os.path.join(out, "perf_report.json"), encoding="utf-8") as f:
            perf = json.load(f)
        self.assertNotIn("perf_mode", perf)
        self.assertIn("达标", perf["summary"])
        self.assertIn("by_shape_class", perf)
        self.assertEqual(perf["target_ratio"], 0.95)
        # 缺省档的 caseset 账本不含 mode / source 两个新字段。
        with open(os.path.join(out, "caseset.json"), encoding="utf-8") as f:
            cs = json.load(f)
        self.assertNotIn("mode", cs["perf_case_policy"])
        self.assertNotIn("source", cs["perf_case_policy"]["shape_classification"])


if __name__ == "__main__":
    unittest.main()
