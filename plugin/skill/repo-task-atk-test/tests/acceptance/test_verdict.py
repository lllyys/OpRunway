import sys
import unittest
from pathlib import Path

from _paths import REFERENCES, SCRIPTS

sys.path.insert(0, str(SCRIPTS))

from _policy import load_policy
from verdict import (
    GateFailure,
    check_builtin_comparator,
    check_interface,
    case_passed,
    check_builtin_evidence,
    conclusion_kind,
    check_artifact_integrity,
    check_interface,
    check_standard_not_tampered,
    summarize,
)


class VerdictTest(unittest.TestCase):
    def interface(self):
        return {
            "interface_mode": "aclnn",
            "execution_backend": "pyaclnn",
            "baseline_backend": "cpu",
            "baseline_api": "torch.median",
            "candidate_symbol": "aclnnMedian",
            "mode_source": "任务书",
        }

    def test_missing_judged_output_is_not_passed(self):
        case = {"passed": True, "outputs": {"output_0.pt": True}}
        self.assertFalse(case_passed(case, ["output_9.pt"]))

    def test_interface_requires_observed_backend(self):
        with self.assertRaises(GateFailure):
            check_interface(self.interface(), {"backends": []}, load_policy())

    def test_interface_rejects_mismatched_backend(self):
        # roll 那轮 manifest 声明 aclnn、实测 pyaclnn，这条门禁抓到了真问题，
        # 必须保留。区别只是现在声明侧来自 S1 派生，不一致就只可能是真跑错了。
        with self.assertRaises(GateFailure):
            check_interface(self.interface(), {"backends": ["cpu", "npu"]},
                            load_policy())

    def test_interface_accepts_matching_backend(self):
        check_interface(self.interface(), {"backends": ["cpu", "pyaclnn"]},
                        load_policy())

    def test_artifact_integrity_accepts_matching_inputs(self):
        coverage = {
            "case_file_sha256": "case",
            "must_cover_sha256": "must",
        }
        results = {
            "task": "accuracy",
            "case_file_sha256": "case",
            "cases": [{"id": "0"}],
            "excluded_cases": [],
            "case_count": 1,
        }
        check_artifact_integrity(coverage, results)

    def test_artifact_integrity_rejects_performance_report(self):
        with self.assertRaises(GateFailure):
            check_artifact_integrity(
                {"case_file_sha256": "case", "must_cover_sha256": "must"},
                {"task": "performance"},
            )

    def test_artifact_integrity_rejects_sha_mismatch(self):
        with self.assertRaises(GateFailure):
            check_artifact_integrity(
                {"case_file_sha256": "case-a", "must_cover_sha256": "must"},
                {"task": "accuracy", "case_file_sha256": "case-b"},
            )

    def test_standard_must_cover_every_case(self):
        results = {
            "standard_acc_summary": {
                "total_cases": 2,
                "present_cases": 1,
                "consistent": False,
                "value": "default",
            }
        }
        with self.assertRaises(GateFailure):
            check_standard_not_tampered(results, load_policy())

    def test_non_finite_input_stays_in_denominator(self):
        results = {
            "cases": [
                {"id": "1", "partition": "must", "passed": True,
                 "non_finite_input": True},
                {"id": "2", "partition": "must", "passed": False,
                 "cause": "unknown"},
            ]
        }
        stats = summarize(results, conclusion_causes={"operator_defect", "accuracy_gap"})
        self.assertEqual(stats["must"]["total"], 2)
        self.assertEqual(stats["must"]["pass_rate"], 0.5)


class ConclusionKindTest(unittest.TestCase):
    """两种结论的证据强度不同，措辞不能混用。"""

    def test_torch_baseline_is_an_accuracy_conclusion(self):
        self.assertEqual("accuracy_vs_framework",
                         conclusion_kind({"baseline_kind": "torch"}))

    def test_builtin_baseline_is_a_regression_conclusion(self):
        self.assertEqual("regression_vs_builtin",
                         conclusion_kind({"baseline_kind": "cann_builtin"}))

    def test_absent_baseline_kind_behaves_like_torch(self):
        # 老的 interface.json 没有这个键，不能因此改判。
        self.assertEqual("accuracy_vs_framework", conclusion_kind({}))


class BuiltinEvidenceGateTest(unittest.TestCase):
    """内置真值那条路，取证不齐不许裁决。"""

    BUILTIN = {"baseline_kind": "cann_builtin"}
    SOURCE_OK = {"verdict": "ok"}

    def test_torch_baseline_is_not_gated(self):
        check_builtin_evidence({"baseline_kind": "torch"}, None, None)

    def test_missing_provenance_blocks_the_verdict(self):
        with self.assertRaises(GateFailure) as ctx:
            check_builtin_evidence(self.BUILTIN, None)
        self.assertIn("golden_provenance.json", str(ctx.exception))

    def test_counter_experiment_never_run_blocks_the_verdict(self):
        with self.assertRaises(GateFailure) as ctx:
            check_builtin_evidence(
                self.BUILTIN, {"counter_experiment": None}, self.SOURCE_OK)
        self.assertIn("没做反证实验", str(ctx.exception))

    def test_failed_counter_experiment_blocks_the_verdict(self):
        # 改坏了真值那条却没变 Fail：比对压根没接上，全绿毫无意义。
        with self.assertRaises(GateFailure) as ctx:
            check_builtin_evidence(
                self.BUILTIN,
                {"counter_experiment": {"case_id": "3", "detected": False}},
                self.SOURCE_OK)
        self.assertIn("没通过", str(ctx.exception))
        self.assertIn("3", str(ctx.exception))

    def test_full_evidence_passes(self):
        check_builtin_evidence(
            self.BUILTIN,
            {"counter_experiment": {"case_id": "3", "detected": True},
             "builtin_library": {"path": "/opp/lib64/a.so", "sha256": "x"}},
            self.SOURCE_OK)

    def test_missing_gate_conclusion_blocks_the_verdict(self):
        # 门禁写得再好，没人强制跑它就等于没有。这条锁住那个入口。
        with self.assertRaises(GateFailure) as ctx:
            check_builtin_evidence(
                self.BUILTIN,
                {"counter_experiment": {"case_id": "3", "detected": True}},
                None)
        self.assertIn("BUILTIN_SOURCE", str(ctx.exception))

    def test_failed_gate_conclusion_blocks_the_verdict(self):
        with self.assertRaises(GateFailure) as ctx:
            check_builtin_evidence(
                self.BUILTIN,
                {"counter_experiment": {"case_id": "3", "detected": True}},
                {"verdict": "refused"})
        self.assertIn("BUILTIN_SOURCE", str(ctx.exception))


class BuiltinTopologyEndToEndTest(unittest.TestCase):
    """S1 派生出的 baseline_backend，必须与两轮拓扑真跑出来的后端对得上。

    这条曾经必失败：cann_builtin 被强制写成 baseline_backend=npu，而
    builtin-baseline.md 的第二轮拓扑是 pyaclnn + cpu，报告里永远没有 npu，
    verdict 100% 拒绝出结论。只单测 conclusion_kind 抓不到——判据在
    check_interface 里，两者从没被放在一起测过。
    """

    # 第二轮实测到的后端：主节点跑待验收算子，cpu 节点从磁盘读真值
    ROUND_TWO_BACKENDS = ["pyaclnn", "cpu"]

    def _interface(self, baseline_backend):
        return {"interface_mode": "aclnn", "execution_backend": "pyaclnn",
                "candidate_symbol": "aclnnBernoulli",
                "baseline_api": "aclnnBernoulli", "mode_source": "任务书 §2",
                "baseline_kind": "cann_builtin",
                "baseline_backend": baseline_backend}

    def test_cpu_load_node_matches_the_real_topology(self):
        check_interface(self._interface("cpu"),
                        {"backends": self.ROUND_TWO_BACKENDS}, load_policy())

    def test_npu_baseline_would_never_be_found(self):
        with self.assertRaises(GateFailure) as ctx:
            check_interface(self._interface("npu"),
                            {"backends": self.ROUND_TWO_BACKENDS}, load_policy())
        self.assertIn("BASELINE_BACKEND", str(ctx.exception))

class BuiltinComparatorGateTest(unittest.TestCase):
    """跑完之后再核一遍：真正送进 ATK 的比较器是不是 equal。

    S2 的门禁管 must_cover 的 comparator，ATK 用的是 YAML 的 standard.acc，
    两者没有绑定。不在这里兜一道，就能拿容差比较器跑完全程，
    再盖上「与内置逐位一致」的结论。
    """

    BUILTIN = {"baseline_kind": "cann_builtin"}

    def _results(self, acc):
        return {"standard_acc_summary": {"value": acc, "consistent": True}}

    def test_equal_passes(self):
        check_builtin_comparator(self.BUILTIN, self._results("equal"))

    def test_mixed_tolerance_is_refused(self):
        with self.assertRaises(GateFailure) as ctx:
            check_builtin_comparator(self.BUILTIN,
                                     self._results("mixed_tolerance_bm"))
        self.assertIn("BUILTIN_COMPARATOR", str(ctx.exception))

    def test_missing_summary_is_refused(self):
        with self.assertRaises(GateFailure):
            check_builtin_comparator(self.BUILTIN, {})

    def test_torch_baseline_is_not_gated(self):
        check_builtin_comparator({"baseline_kind": "torch"},
                                 self._results("mixed_tolerance_bm"))


if __name__ == "__main__":
    unittest.main()


class VerdictKeysAreDocumentedTest(unittest.TestCase):
    """报告用得到的键必须在 reference 里列出，否则只能翻 JSON 找。

    真机现象：agent 连开三次 python 去打印 verdict.json 的顶层字段，
    才知道报告该引用哪几项。键名是脚本产出的事实，列一次就不用再翻。
    """

    REFERENCE = REFERENCES / "reporting.md"

    def test_documented_keys_all_exist_in_the_verdict(self):
        text = self.REFERENCE.read_text(encoding="utf-8")
        source = (SCRIPTS / "verdict.py").read_text(encoding="utf-8")
        for key in ("conclusion", "reason", "partitions", "performance",
                    "pending_recheck", "must_cases_pending_recheck",
                    "excluded_cases", "must_coverage_gap_ids", "interface"):
            with self.subTest(key=key):
                self.assertIn(f"`{key}`", text)
                self.assertIn(f'"{key}"', source)


class PerformanceStatusTest(unittest.TestCase):
    """性能状态由脚本推导，不由报告作者自己写。

    真机事故（roll，2026-08-16）：S4 门禁要求「性能状态非空」，
    而没有任何量具产出它。agent 翻遍 verdict.py / make_repro.py / references
    之后自己造了一份 conclusion/performance_status.json，
    并且整轮没有采集任何绝对耗时——用户事后才发现性能数据缺失。
    """

    PERF = {"source": "/x/perf.xlsx",
            "cases": [{"id": "0", "device_us": {"pyaclnn_0": 12.5}},
                      {"id": "1", "device_us": {"pyaclnn_0": 30.0}}]}

    def test_accuracy_failure_short_circuits(self):
        from verdict import performance_status

        record = performance_status("不通过", None, None)
        self.assertEqual("未执行(精度未通过)", record["status"])

    def test_no_baseline_still_requires_the_absolute_numbers(self):
        from verdict import performance_status

        record = performance_status("通过", self.PERF, None)
        self.assertEqual("未执行(无基线)", record["status"])
        self.assertEqual(2, record["measured_cases"])

    def test_baseline_source_makes_it_a_pass(self):
        from verdict import performance_status

        record = performance_status("通过", self.PERF, "任务书 §4 性能要求")
        self.assertEqual("通过", record["status"])

    def test_passing_accuracy_without_perf_results_refuses_the_verdict(self):
        from verdict import performance_status

        with self.assertRaises(GateFailure) as caught:
            performance_status("通过", None, None)
        self.assertIn("performance_device", str(caught.exception))

    def test_perf_report_without_device_us_refuses_the_verdict(self):
        # 「跑过性能」不等于「落下了绝对耗时」，只有后者对测试人员有用。
        from verdict import performance_status

        with self.assertRaises(GateFailure):
            performance_status("通过", {"cases": [{"id": "0"}]}, None)

    def test_status_is_always_one_of_the_three(self):
        from verdict import performance_status

        allowed = {"通过", "未执行(精度未通过)", "未执行(无基线)"}
        for conclusion, perf, source in (
                ("不通过", None, None),
                ("有条件通过", self.PERF, None),
                ("通过", self.PERF, "任务书"),
        ):
            with self.subTest(conclusion=conclusion):
                self.assertIn(
                    performance_status(conclusion, perf, source)["status"],
                    allowed)
