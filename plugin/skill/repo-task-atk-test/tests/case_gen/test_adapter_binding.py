"""适配器判定必须在 S2 完成，判据从用例集读。

真机实测（roll，2026-08-15）：semantic_review 在 S2 就预言了「dims 置空
需要 typed 空指针适配器」，agent 判断「S3 冒烟再说」。S3 才发现、才改 YAML，
而改 YAML 要重跑 atk case，撞上冻结纪律，只能手工自证十次。

预警成不成立不需要问 agent——用例集就在手上。
"""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _paths import SKILL_ROOT

sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import check_adapter_binding as gate  # noqa: E402

SCRIPT = SKILL_ROOT / "scripts" / "check_adapter_binding.py"


def case(case_id, inputs, **wiring):
    return dict({"id": case_id, "inputs": inputs}, **wiring)


def tensor(name, values="normal"):
    return {"name": name, "type": "tensor", "dtype": "fp32",
            "shape": [4], "range_values": values}


CLEAN = {"aclnn_adapter": {"required": False, "reasons": [],
                           "determinable": True},
         "baseline_adapter": {"required": False, "reasons": [],
                              "determinable": True},
         "semantic_review": []}


class WiringTest(unittest.TestCase):
    def test_default_is_assumed_when_the_case_omits_the_key(self):
        # ATK 的 CaseConfig 默认就是 function / aclnn_function，
        # 用例里没写这个键不等于「没绑」，等于绑了默认执行器。
        self.assertEqual({"aclnn_function"},
                         gate.wiring([case(0, [tensor("x")])],
                                     "aclnn_api_type"))

    def test_explicit_wiring_is_read_from_the_case(self):
        self.assertEqual(
            {"my_exec"},
            gate.wiring([case(0, [tensor("x")], aclnn_api_type="my_exec")],
                        "aclnn_api_type"))


class NulledParametersTest(unittest.TestCase):
    def test_null_token_counts(self):
        cases = [case(0, [tensor("x"), tensor("dims", "null")])]
        self.assertEqual({"dims": [0]}, gate.nulled_parameters(cases))

    def test_boxed_null_token_counts(self):
        cases = [case(7, [tensor("dims", ["null"])])]
        self.assertEqual({"dims": [7]}, gate.nulled_parameters(cases))

    def test_default_token_counts(self):
        cases = [case(2, [{"name": "n", "type": "attr", "dtype": "int64_t",
                           "range_values": "default"}])]
        self.assertEqual({"n": [2]}, gate.nulled_parameters(cases))

    def test_ordinary_values_do_not_count(self):
        self.assertEqual({}, gate.nulled_parameters([case(0, [tensor("x")])]))


class JudgeTest(unittest.TestCase):
    def test_clean_alignment_passes(self):
        report, problems = gate.judge(CLEAN, [case(0, [tensor("x")])])
        self.assertEqual([], problems)
        self.assertFalse(report["verdicts"]["aclnn_api_type"]["required"])

    def test_required_adapter_left_on_the_default_is_rejected(self):
        alignment = json.loads(json.dumps(CLEAN))
        alignment["aclnn_adapter"] = {
            "required": True, "reasons": ["出参不在末尾"], "determinable": True}
        _, problems = gate.judge(alignment, [case(0, [tensor("x")])])
        self.assertEqual(1, len(problems))
        self.assertIn("aclnn_api_type", problems[0])

    def test_required_adapter_that_is_wired_passes(self):
        alignment = json.loads(json.dumps(CLEAN))
        alignment["aclnn_adapter"] = {
            "required": True, "reasons": ["出参不在末尾"], "determinable": True}
        _, problems = gate.judge(
            alignment, [case(0, [tensor("x")], aclnn_api_type="my_exec")])
        self.assertEqual([], problems)

    def test_undeterminable_alignment_blocks(self):
        # 「没找到理由」不等于「不需要适配器」。
        alignment = json.loads(json.dumps(CLEAN))
        alignment["baseline_adapter"]["determinable"] = False
        _, problems = gate.judge(alignment, [case(0, [tensor("x")])])
        self.assertEqual(1, len(problems))
        self.assertIn("determinable", problems[0])

    def test_conditional_warning_that_the_case_set_triggers(self):
        alignment = json.loads(json.dumps(CLEAN))
        alignment["semantic_review"] = [
            {"parameter": "dims", "issue": "aclIntArray 是可空指针"}]
        report, problems = gate.judge(
            alignment, [case(0, [tensor("x"), tensor("dims", "null")])])
        self.assertTrue(report["reviews"][0]["triggered"])
        self.assertEqual([0], report["reviews"][0]["evidence_case_ids"])
        self.assertEqual(1, len(problems))
        self.assertIn("typed", problems[0])

    def test_conditional_warning_that_no_case_triggers_is_closed(self):
        # 不触发也要留痕：「已判定不触发」和「没判过」必须分得开。
        alignment = json.loads(json.dumps(CLEAN))
        alignment["semantic_review"] = [
            {"parameter": "dims", "issue": "aclIntArray 是可空指针"}]
        report, problems = gate.judge(
            alignment, [case(0, [tensor("x"), tensor("dims")])])
        self.assertFalse(report["reviews"][0]["triggered"])
        self.assertEqual([], problems)

    def test_non_parameter_warning_is_surfaced_but_does_not_block(self):
        # 重载数、变长签名这类预警脚本判不了。硬拦会把每一轮都堵死，
        # 所以只标记 requires_manual_review 并原样列出。
        alignment = json.loads(json.dumps(CLEAN))
        alignment["semantic_review"] = [
            {"parameter": None, "issue": "基线接口有 3 个 aten 重载"}]
        report, problems = gate.judge(alignment, [case(0, [tensor("x")])])
        self.assertEqual([], problems)
        self.assertTrue(report["reviews"][0]["requires_manual_review"])


class CliTest(unittest.TestCase):
    def _run(self, alignment, cases):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "align.json").write_text(
                json.dumps(alignment), encoding="utf-8")
            (tmp / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT),
                 "-j", str(tmp / "cases.json"),
                 "-a", str(tmp / "align.json"),
                 "-o", str(tmp / "out.json")],
                capture_output=True, text=True)
            report = json.loads((tmp / "out.json").read_text(encoding="utf-8")) \
                if (tmp / "out.json").exists() else None
            return result, report

    def test_pass_writes_a_report_and_exits_zero(self):
        result, report = self._run(CLEAN, [case(0, [tensor("x")])])
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("verdicts", report)

    def test_report_binds_to_the_exact_case_file_bytes(self):
        cases = [case(0, [tensor("x")])]
        result, report = self._run(CLEAN, cases)
        expected = hashlib.sha256(json.dumps(cases).encode()).hexdigest()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(expected, report["case_file_sha256"])

    def test_failure_exits_two_and_still_writes_the_report(self):
        alignment = json.loads(json.dumps(CLEAN))
        alignment["aclnn_adapter"] = {
            "required": True, "reasons": ["出参不在末尾"], "determinable": True}
        result, report = self._run(alignment, [case(0, [tensor("x")])])
        self.assertEqual(2, result.returncode)
        self.assertIsNotNone(report, "判不过也要留下证据，S5 证据链要引用它")

    def test_missing_input_exits_three(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "-j", "/nope.json",
             "-a", "/nope.json", "-o", "/tmp/x.json"],
            capture_output=True, text=True)
        self.assertEqual(3, result.returncode)


if __name__ == "__main__":
    unittest.main()
