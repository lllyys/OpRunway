#!/usr/bin/env python3
"""任务书输入校验器单测。"""

import json
import os
import tempfile
import unittest

import content_address
import validate_taskdoc_input as VTI


_TASKDOC = """# 见证任务书

算子名称 WitnessOp，位于 cann/ops-nn 仓，需求编号 REQ-0001。
本次交付新增 WitnessOp 正向实现，反向实现明确不在本次范围内。
需交付 aclnnWitnessOp 与 aclnnWitnessOpDim 两个 overload。
输入为单个 tensor，dtype 为 float32 与 float16，rank 取值 1 至 4，layout 为 ND。
属性 dim 为 int32，合法范围 -4 至 3，默认值为 0，不可为 null。
输出为单个 tensor，dtype 与输入一致，shape 规则与输入相同。
功能语义与 torch.witness_op 对齐，越界属性返回错误码。
真值来源为 CPU 上的 torch.witness_op，运行环境为 x86 CPU，不允许组合实现。
性能要求为相对基线不劣化。
性能基线为同机同卡的 torch_npu.witness_op，语义等价已由需求方确认。
性能口径为 kernel-only，统计量取中位数，warmup 5 次 repeat 20 次，达标公式为比值不小于 1.0。
目标硬件为 Atlas A3 单卡，功能、精度与性能均在该硬件验收。
交付工程形态为 ops-nn 算子工程，被测构建物为该工程构建出的 vendor 动态库，测试桥不属于交付。
必须交付的调用层级为 ACLNN 接口直调。
验收完成条件为全部用例精度通过且性能不劣化，不允许部分覆盖或延期项。
"""

_MUST_QUOTES = {
    "operator_identity": "算子名称 WitnessOp，位于 cann/ops-nn 仓，需求编号 REQ-0001",
    "delivery_scope": "本次交付新增 WitnessOp 正向实现，反向实现明确不在本次范围内",
    "api_and_overload": "需交付 aclnnWitnessOp 与 aclnnWitnessOpDim 两个 overload",
    "input_contract": "输入为单个 tensor，dtype 为 float32 与 float16，rank 取值 1 至 4，layout 为 ND",
    "attribute_contract": "属性 dim 为 int32，合法范围 -4 至 3，默认值为 0，不可为 null",
    "output_contract": "输出为单个 tensor，dtype 与输入一致，shape 规则与输入相同",
    "functional_semantics": "功能语义与 torch.witness_op 对齐，越界属性返回错误码",
    "golden_reference": "真值来源为 CPU 上的 torch.witness_op，运行环境为 x86 CPU，不允许组合实现",
    "performance_baseline": "性能基线为同机同卡的 torch_npu.witness_op，语义等价已由需求方确认",
    "performance_metric_scope": "性能口径为 kernel-only，统计量取中位数，warmup 5 次 repeat 20 次",
    "target_hardware": "目标硬件为 Atlas A3 单卡，功能、精度与性能均在该硬件验收",
    "deliverable_and_dut": "被测构建物为该工程构建出的 vendor 动态库，测试桥不属于交付",
    "integration_form": "必须交付的调用层级为 ACLNN 接口直调",
    "acceptance_completion_criteria": "验收完成条件为全部用例精度通过且性能不劣化，不允许部分覆盖或延期项",
}


class TaskdocValidationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        with open(os.path.join(self.root, "task_doc.md"), "w",
                  encoding="utf-8") as out:
            out.write(_TASKDOC)
        self.source_payload = {"taskdoc": {"bytes_sha256": "0" * 64}}
        content_address.write_artifact(
            self.root, "source_facts.json", VTI._SOURCE_DOMAIN,
            self.source_payload)
        self.digest = content_address.content_digest(
            VTI._SOURCE_DOMAIN, self.source_payload)

    # --- fixture builders -------------------------------------------------

    def _items(self):
        items = [{"id": item_id, "status": "satisfied",
                  "quotes": [{"text": text}]}
                 for item_id, text in _MUST_QUOTES.items()]
        items.append({
            "id": "special_semantics", "status": "not_applicable",
            "applicable": False,
            "rationale": "任务书语义为逐元素计算，不涉及 tie/NaN/空 tensor 场景",
        })
        items.append({
            "id": "extra_precision_requirement", "status": "missing",
            "rationale": "任务书未声明额外精度要求，按 workflow 标准",
        })
        items.append({
            "id": "dependencies_and_prerequisites", "status": "not_applicable",
            "applicable": False,
            "rationale": "本轮交付不依赖任务书之外的额外仓或授权环境",
        })
        items.append({
            "id": "failure_and_exception_rules", "status": "not_applicable",
            "applicable": False,
            "rationale": "任务书未声明不支持项或允许挂起项",
        })
        return items

    def _payload(self, **overrides):
        payload = {
            "schema": VTI._VALIDATION_SCHEMA,
            "schema_version": 1,
            "op": "witness_op",
            "source_facts_digest": self.digest,
            "perf_required": True,
            "perf_evidence": [{"text": "性能要求为相对基线不劣化"}],
            "items": self._items(),
            "decisions": [],
        }
        payload.update(overrides)
        return payload

    def _write(self, payload, name="taskdoc_validation.json"):
        with open(os.path.join(self.root, name), "w", encoding="utf-8") as out:
            json.dump(payload, out, ensure_ascii=False)
        return name

    def _evaluate(self, payload, **kwargs):
        return VTI.evaluate(self.root, self._write(payload), **kwargs)

    def _set_item(self, items, item_id, **fields):
        for item in items:
            if item["id"] == item_id:
                item.clear()
                item["id"] = item_id
                item.update(fields)
                return items
        raise AssertionError(f"fixture 缺少校验项 {item_id}")

    # --- contract itself --------------------------------------------------

    def test_shipped_contract_is_valid_and_covers_the_standard(self):
        contract = VTI.load_contract()
        ids = [item["id"] for item in contract["items"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 18)
        must = [item for item in contract["items"]
                if item["requirement"] == "must"]
        perf = [item for item in contract["items"]
                if item["requirement"] == "conditional_perf"]
        self.assertEqual(len(must), 12)
        self.assertEqual(len(perf), 2)
        for item in must:
            self.assertEqual(item["on_unsatisfied"],
                             {"missing": "stop", "ambiguous": "stop"})

    def test_contract_with_unknown_route_is_rejected(self):
        path = os.path.join(self.root, "bad_contract.json")
        contract = VTI.load_contract()
        contract["items"][0]["on_unsatisfied"]["missing"] = "ignore"
        with open(path, "w", encoding="utf-8") as out:
            json.dump(contract, out)
        with self.assertRaises(VTI.TaskdocValidationError):
            VTI.load_contract(path)

    # --- happy path -------------------------------------------------------

    def test_fully_specified_taskdoc_passes(self):
        receipt = self._evaluate(self._payload())
        self.assertEqual(receipt["status"], "PASSED", receipt["errors"])
        self.assertEqual(receipt["blocking_items"], [])
        self.assertEqual(receipt["pending_items"], [])
        self.assertEqual(receipt["acceptance_verdict"], None)
        self.assertEqual(receipt["scope"], "taskdoc-input-only")
        self.assertEqual(len(receipt["workflow_default_items"]), 1)
        self.assertEqual(receipt["workflow_default_items"][0]["id"],
                         "extra_precision_requirement")

    def test_receipt_never_carries_an_acceptance_verdict(self):
        items = self._set_item(self._items(), "target_hardware",
                               status="missing", rationale="任务书未写 SoC 型号")
        receipt = self._evaluate(self._payload(items=items))
        self.assertEqual(receipt["status"], "NEEDS_USER")
        self.assertEqual(receipt["acceptance_verdict"], None)

    # --- must items block -------------------------------------------------

    def test_missing_must_item_blocks(self):
        items = self._set_item(self._items(), "golden_reference",
                               status="missing",
                               rationale="任务书未指明真值来源 API")
        receipt = self._evaluate(self._payload(items=items))
        self.assertEqual(receipt["status"], "NEEDS_USER")
        self.assertEqual([entry["id"] for entry in receipt["blocking_items"]],
                         ["golden_reference"])
        self.assertEqual(receipt["blocking_items"][0]["route"], "stop")

    def test_ambiguous_must_item_blocks(self):
        items = self._set_item(self._items(), "input_contract",
                               status="ambiguous",
                               rationale="只写了「常用 dtype」，未量化枚举")
        receipt = self._evaluate(self._payload(items=items))
        self.assertEqual(receipt["status"], "NEEDS_USER")
        self.assertEqual([entry["id"] for entry in receipt["blocking_items"]],
                         ["input_contract"])

    def test_unsatisfied_item_without_rationale_is_blocked(self):
        items = self._set_item(self._items(), "input_contract",
                               status="missing")
        receipt = self._evaluate(self._payload(items=items))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("rationale", receipt["errors"][0])

    def test_must_item_cannot_be_declared_not_applicable(self):
        items = self._set_item(self._items(), "target_hardware",
                               status="not_applicable",
                               rationale="我觉得用不上")
        receipt = self._evaluate(self._payload(items=items))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("not_applicable", receipt["errors"][0])

    def test_must_item_cannot_be_declared_inapplicable(self):
        items = self._set_item(self._items(), "target_hardware",
                               status="missing", applicable=False,
                               rationale="我觉得用不上")
        receipt = self._evaluate(self._payload(items=items))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("恒适用", receipt["errors"][0])

    # --- quote enforcement ------------------------------------------------

    def test_fabricated_quote_is_blocked(self):
        items = self._set_item(
            self._items(), "golden_reference", status="satisfied",
            quotes=[{"text": "真值来源为 GPU 上的 cupy 参考实现，允许组合实现"}])
        receipt = self._evaluate(self._payload(items=items))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("未逐字出现在任务书原文", receipt["errors"][0])

    def test_quote_spanning_source_line_breaks_is_accepted(self):
        with open(os.path.join(self.root, "task_doc.md"), "w",
                  encoding="utf-8") as out:
            out.write(_TASKDOC.replace(
                "目标硬件为 Atlas A3 单卡，功能、精度与性能均在该硬件验收",
                "目标硬件为 Atlas A3 单卡，\n功能、精度与性能均在该硬件验收"))
        receipt = self._evaluate(self._payload())
        self.assertEqual(receipt["status"], "PASSED", receipt["errors"])

    def test_too_short_quote_is_blocked(self):
        items = self._set_item(self._items(), "integration_form",
                               status="satisfied", quotes=[{"text": "ACLNN"}])
        receipt = self._evaluate(self._payload(items=items))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("引用过短", receipt["errors"][0])

    def test_satisfied_without_quotes_is_blocked(self):
        items = self._set_item(self._items(), "integration_form",
                               status="satisfied")
        receipt = self._evaluate(self._payload(items=items))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("quotes", receipt["errors"][0])

    # --- coverage of the checklist ---------------------------------------

    def test_dropping_an_item_is_blocked(self):
        items = [item for item in self._items()
                 if item["id"] != "acceptance_completion_criteria"]
        receipt = self._evaluate(self._payload(items=items))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("acceptance_completion_criteria", receipt["errors"][0])

    def test_unknown_item_is_blocked(self):
        items = self._items()
        items.append({"id": "invented_item", "status": "satisfied",
                      "quotes": [{"text": "算子名称 WitnessOp"}]})
        receipt = self._evaluate(self._payload(items=items))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("invented_item", receipt["errors"][0])

    def test_duplicate_item_is_blocked(self):
        items = self._items()
        items.append(dict(items[0]))
        receipt = self._evaluate(self._payload(items=items))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("重复 id", receipt["errors"][0])

    # --- conditional items ------------------------------------------------

    def test_applicable_special_semantics_only_pends(self):
        items = self._set_item(
            self._items(), "special_semantics", status="missing",
            applicable=True,
            rationale="算子做归约取元素，存在 tie 场景但任务书未说明选哪个下标")
        receipt = self._evaluate(self._payload(items=items))
        self.assertEqual(receipt["status"], "PASSED_WITH_PENDING")
        self.assertEqual(receipt["blocking_items"], [])
        self.assertEqual([entry["id"] for entry in receipt["pending_items"]],
                         ["special_semantics"])

    def test_applicable_failure_rules_block(self):
        items = self._set_item(
            self._items(), "failure_and_exception_rules", status="ambiguous",
            applicable=True,
            rationale="提到「部分 dtype 暂不支持」但未说明如何处置")
        receipt = self._evaluate(self._payload(items=items))
        self.assertEqual(receipt["status"], "NEEDS_USER")
        self.assertEqual([entry["id"] for entry in receipt["blocking_items"]],
                         ["failure_and_exception_rules"])

    def test_conditional_item_without_applicable_flag_is_blocked(self):
        items = self._set_item(self._items(), "special_semantics",
                               status="missing", rationale="未说明")
        receipt = self._evaluate(self._payload(items=items))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("applicable", receipt["errors"][0])

    def test_not_applicable_without_rationale_is_blocked(self):
        items = self._set_item(self._items(), "special_semantics",
                               status="not_applicable", applicable=False)
        receipt = self._evaluate(self._payload(items=items))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("rationale", receipt["errors"][0])

    # --- performance applicability ---------------------------------------

    def test_perf_items_block_when_performance_is_required(self):
        items = self._set_item(self._items(), "performance_metric_scope",
                               status="missing",
                               rationale="任务书只写「不劣化」，未给统计量与达标公式")
        receipt = self._evaluate(self._payload(items=items))
        self.assertEqual(receipt["status"], "NEEDS_USER")
        self.assertEqual([entry["id"] for entry in receipt["blocking_items"]],
                         ["performance_metric_scope"])

    def test_no_performance_requirement_makes_perf_items_not_applicable(self):
        items = self._items()
        for item_id in ("performance_baseline", "performance_metric_scope"):
            self._set_item(items, item_id, status="not_applicable",
                           rationale="任务书未提出性能要求")
        receipt = self._evaluate(self._payload(
            items=items, perf_required=False, perf_evidence=None,
            perf_required_rationale="通读任务书未发现任何性能条款"))
        self.assertEqual(receipt["status"], "PASSED", receipt["errors"])
        self.assertIs(receipt["perf_required"], False)

    def test_perf_not_required_still_needs_a_rationale(self):
        items = self._items()
        for item_id in ("performance_baseline", "performance_metric_scope"):
            self._set_item(items, item_id, status="not_applicable",
                           rationale="任务书未提出性能要求")
        receipt = self._evaluate(self._payload(items=items,
                                               perf_required=False))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("perf_required_rationale", receipt["errors"][0])

    def test_perf_required_needs_a_taskdoc_quote(self):
        receipt = self._evaluate(self._payload(
            perf_evidence=[{"text": "该算子需要跑得比基线快一倍"}]))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("未逐字出现在任务书原文", receipt["errors"][0])

    def test_perf_applicability_must_agree_with_perf_required(self):
        items = self._set_item(self._items(), "performance_baseline",
                               status="not_applicable", applicable=False,
                               rationale="不想测性能")
        receipt = self._evaluate(self._payload(items=items))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("perf_required", receipt["errors"][0])

    # --- user decisions ---------------------------------------------------

    def test_supplied_decision_clears_the_block(self):
        items = self._set_item(self._items(), "target_hardware",
                               status="missing", rationale="任务书未写 SoC 型号")
        receipt = self._evaluate(self._payload(items=items, decisions=[{
            "id": "target_hardware", "action": "supplied",
            "value": "Atlas A3，功能/精度/性能均在 A3 验收", "source": "user"}]))
        self.assertEqual(receipt["status"], "PASSED", receipt["errors"])
        self.assertEqual(receipt["blocking_items"], [])
        self.assertEqual([entry["id"] for entry in receipt["decided_items"]],
                         ["target_hardware"])
        self.assertEqual(receipt["confirmed_constraints_candidates"], [{
            "key": "taskdoc:target_hardware",
            "value": "Atlas A3，功能/精度/性能均在 A3 验收",
            "source": "user"}])

    def test_waived_decision_clears_the_block(self):
        items = self._set_item(self._items(), "delivery_scope",
                               status="ambiguous", rationale="范围边界描述含糊")
        receipt = self._evaluate(self._payload(items=items, decisions=[{
            "id": "delivery_scope", "action": "waived",
            "rationale": "需求方口头确认本次只交付正向", "source": "user"}]))
        self.assertEqual(receipt["status"], "PASSED", receipt["errors"])
        self.assertEqual(receipt["confirmed_constraints_candidates"], [])
        self.assertEqual(receipt["decided_items"][0]["decision"]["action"],
                         "waived")

    def test_decision_on_a_satisfied_item_is_blocked(self):
        receipt = self._evaluate(self._payload(decisions=[{
            "id": "target_hardware", "action": "waived",
            "rationale": "反正跑得起来", "source": "user"}]))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("不在本轮阻断/待确认项内", receipt["errors"][0])

    def test_decision_must_declare_a_user_source(self):
        items = self._set_item(self._items(), "target_hardware",
                               status="missing", rationale="任务书未写 SoC 型号")
        receipt = self._evaluate(self._payload(items=items, decisions=[{
            "id": "target_hardware", "action": "supplied",
            "value": "A3", "source": "agent"}]))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("source", receipt["errors"][0])

    def test_supplied_decision_needs_a_value(self):
        items = self._set_item(self._items(), "target_hardware",
                               status="missing", rationale="任务书未写 SoC 型号")
        receipt = self._evaluate(self._payload(items=items, decisions=[{
            "id": "target_hardware", "action": "supplied", "source": "user"}]))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("value", receipt["errors"][0])

    def test_duplicate_decision_is_blocked(self):
        items = self._set_item(self._items(), "target_hardware",
                               status="missing", rationale="任务书未写 SoC 型号")
        decision = {"id": "target_hardware", "action": "supplied",
                    "value": "A3 单卡", "source": "user"}
        receipt = self._evaluate(self._payload(items=items,
                                               decisions=[decision, decision]))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("重复决策", receipt["errors"][0])

    # --- source binding ---------------------------------------------------

    def test_source_facts_drift_invalidates_the_round(self):
        content_address.write_artifact(
            self.root, "source_facts.json", VTI._SOURCE_DOMAIN,
            {"taskdoc": {"bytes_sha256": "1" * 64}})
        receipt = self._evaluate(self._payload())
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("摘要漂移", receipt["errors"][0])

    def test_missing_source_facts_is_blocked(self):
        os.unlink(os.path.join(self.root, "source_facts.json"))
        receipt = self._evaluate(self._payload())
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("source_facts", receipt["errors"][0])

    def test_missing_taskdoc_is_blocked(self):
        os.unlink(os.path.join(self.root, "task_doc.md"))
        receipt = self._evaluate(self._payload())
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("无法读取任务书原文", receipt["errors"][0])

    # --- CLI --------------------------------------------------------------

    def test_exit_codes_split_blocked_from_needs_user(self):
        self.assertEqual(VTI._exit_code("PASSED"), 0)
        self.assertEqual(VTI._exit_code("PASSED_WITH_PENDING"), 0)
        self.assertEqual(VTI._exit_code("NEEDS_USER"), 2)
        self.assertEqual(VTI._exit_code("BLOCKED"), 1)

    def test_main_writes_receipt_and_returns_needs_user(self):
        items = self._set_item(self._items(), "target_hardware",
                               status="missing", rationale="任务书未写 SoC 型号")
        name = self._write(self._payload(items=items))
        code = VTI.main(["--root", self.root, "--validation", name,
                         "--out", "taskdoc_validation_receipt.json"])
        self.assertEqual(code, 2)
        receipt = content_address.read_artifact(
            self.root, "taskdoc_validation_receipt.json", VTI._RECEIPT_DOMAIN)
        self.assertEqual(receipt["status"], "NEEDS_USER")

    def test_dry_run_does_not_write_the_receipt(self):
        name = self._write(self._payload())
        os.environ[VTI._DRY_RUN_ENV] = "1"
        self.addCleanup(os.environ.pop, VTI._DRY_RUN_ENV, None)
        code = VTI.main(["--root", self.root, "--validation", name,
                         "--out", "taskdoc_validation_receipt.json"])
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(
            os.path.join(self.root, "taskdoc_validation_receipt.json")))


if __name__ == "__main__":
    unittest.main()
