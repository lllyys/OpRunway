#!/usr/bin/env python3
"""任务书输入校验器单测。"""

import copy
import hashlib
import json
import os
import tempfile
import unittest
from unittest import mock

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
本算子为逐元素计算，不涉及并列值选择或索引输出。
真值来源为 CPU 上的 torch.witness_op，运行环境为 x86 CPU，不允许组合实现。
性能要求为相对基线不劣化。
性能基线为同机同卡的 torch_npu.witness_op，语义等价已由需求方确认。
性能口径为 kernel-only，统计量取中位数，warmup 5 次 repeat 20 次，达标公式为比值不小于 1.0。
目标硬件为 Atlas A3 单卡，功能、精度与性能均在该硬件验收。
交付工程形态为 ops-nn 算子工程，被测构建物为该工程构建出的 vendor 动态库，测试桥不属于交付。
接入形态要求交付 ACLNN 接口直调层。
交付件清单：ACLNN 接口层必须交付；Torch 封装为可选交付项。
构建与运行只依赖 CANN toolkit 本身，不引入外部库或额外仓。
本次交付不设置不支持项、挂起项或已知限制。
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
    "integration_form": "接入形态要求交付 ACLNN 接口直调层",
    "acceptance_completion_criteria": "验收完成条件为全部用例精度通过且性能不劣化，不允许部分覆盖或延期项",
}

# 见证任务书里恰有两处受控定性标记：「必须交付」与「可选」。清单必须逐处覆盖，
# 否则 delivery_scope 不得判 satisfied。
_DELIVERABLES = [
    {"id": "aclnn_layer", "name": "ACLNN 接口层", "requirement": "required",
     "quotes": [{"text": "ACLNN 接口层必须交付"}]},
    {"id": "torch_wrapper", "name": "Torch 封装", "requirement": "optional",
     "quotes": [{"text": "Torch 封装为可选交付项"}]},
]

_NOT_APPLICABLE_QUOTES = {
    "special_semantics": "本算子为逐元素计算，不涉及并列值选择或索引输出",
    "dependencies_and_prerequisites": "构建与运行只依赖 CANN toolkit 本身，不引入外部库或额外仓",
    "failure_and_exception_rules": "本次交付不设置不支持项、挂起项或已知限制",
}

# 18 项的完整期望路由表。逐项锁死，改动任何一项的 requirement 或路由都会在这里被抓住。
_EXPECTED = {
    "operator_identity": ("must", "stop", "stop"),
    "delivery_scope": ("must", "stop", "stop"),
    "api_and_overload": ("must", "stop", "stop"),
    "input_contract": ("must", "stop", "stop"),
    "attribute_contract": ("must", "stop", "stop"),
    "output_contract": ("must", "stop", "stop"),
    "functional_semantics": ("must", "stop", "stop"),
    "special_semantics": ("conditional", "list_pending", "list_pending"),
    "golden_reference": ("must", "stop", "stop"),
    "extra_precision_requirement": ("optional", "use_workflow_default", "stop"),
    "performance_baseline": ("conditional_perf", "stop", "stop"),
    "performance_metric_scope": ("conditional_perf", "stop", "stop"),
    "target_hardware": ("must", "stop", "stop"),
    "deliverable_and_dut": ("must", "stop", "stop"),
    "integration_form": ("must", "stop", "stop"),
    "dependencies_and_prerequisites": ("conditional", "stop", "stop"),
    "failure_and_exception_rules": ("conditional", "stop", "stop"),
    "acceptance_completion_criteria": ("must", "stop", "stop"),
}


class TaskdocValidationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self._write_taskdoc(_TASKDOC)

    # --- fixture builders -------------------------------------------------

    def _write_taskdoc(self, text):
        """写任务书并把事实包刷到同一份字节上（两者必须始终同源）。"""
        raw = text.encode("utf-8")
        with open(os.path.join(self.root, "task_doc.md"), "wb") as out:
            out.write(raw)
        self.source_payload = {
            "taskdoc": {"bytes_sha256": hashlib.sha256(raw).hexdigest()}}
        content_address.write_artifact(
            self.root, "source_facts.json", VTI._SOURCE_DOMAIN,
            self.source_payload)
        self.digest = content_address.content_digest(
            VTI._SOURCE_DOMAIN, self.source_payload)

    def _items(self):
        items = [{"id": item_id, "status": "satisfied",
                  "quotes": [{"text": text}]}
                 for item_id, text in _MUST_QUOTES.items()]
        for item_id, text in _NOT_APPLICABLE_QUOTES.items():
            items.append({
                "id": item_id, "status": "not_applicable", "applicable": False,
                "quotes": [{"text": text}],
                "rationale": f"任务书原文已排除该场景：{text}",
            })
        items.append({
            "id": "extra_precision_requirement", "status": "missing",
            "rationale": "任务书未声明额外精度要求，按 workflow 标准",
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
            "deliverables": copy.deepcopy(_DELIVERABLES),
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

    def _decision(self, item_id, action, status, **extra):
        decision = {"id": item_id, "action": action, "source": "user",
                    "resolved_status": status}
        decision.update(extra)
        return decision

    # --- the shipped contract --------------------------------------------

    def test_shipped_contract_matches_the_standard_item_by_item(self):
        contract = VTI.load_contract()
        by_id = {item["id"]: item for item in contract["items"]}
        self.assertEqual(sorted(by_id), sorted(_EXPECTED))
        for item_id, (requirement, on_missing, on_ambiguous) in _EXPECTED.items():
            spec = by_id[item_id]
            self.assertEqual(spec["requirement"], requirement, item_id)
            self.assertEqual(spec["on_unsatisfied"]["missing"], on_missing, item_id)
            self.assertEqual(spec["on_unsatisfied"]["ambiguous"], on_ambiguous,
                             item_id)

    def test_shipped_contract_forbids_waiving_a_blocking_item(self):
        contract = VTI.load_contract()
        self.assertEqual(contract["resolution_actions_by_route"]["stop"],
                         ["supplied"])

    def test_contract_with_unknown_route_is_rejected(self):
        contract = VTI.load_contract()
        contract["items"][0]["on_unsatisfied"]["missing"] = "ignore"
        with self.assertRaises(VTI.TaskdocValidationError):
            VTI.load_contract(self._write_contract(contract))

    def test_contract_allowing_waiver_of_blocking_items_is_rejected(self):
        contract = VTI.load_contract()
        contract["resolution_actions_by_route"]["stop"] = ["supplied", "waived"]
        with self.assertRaises(VTI.TaskdocValidationError):
            VTI.load_contract(self._write_contract(contract))

    def _write_contract(self, contract, name="alt_contract.json"):
        path = os.path.join(self.root, name)
        with open(path, "w", encoding="utf-8") as out:
            json.dump(contract, out, ensure_ascii=False)
        return path

    def test_relaxed_contract_is_not_reachable_from_the_cli(self):
        relaxed = VTI.load_contract()
        relaxed["items"] = relaxed["items"][:1]
        path = self._write_contract(relaxed)
        name = self._write(self._payload())
        with self.assertRaises(SystemExit):
            VTI.main(["--root", self.root, "--validation", name,
                      "--contract", path])

    # --- happy path -------------------------------------------------------

    def test_fully_specified_taskdoc_passes(self):
        receipt = self._evaluate(self._payload())
        self.assertEqual(receipt["status"], "PASSED", receipt["errors"])
        self.assertEqual(receipt["blocking_items"], [])
        self.assertEqual(receipt["pending_items"], [])
        self.assertEqual(len(receipt["workflow_default_items"]), 1)
        self.assertEqual(receipt["workflow_default_items"][0]["id"],
                         "extra_precision_requirement")

    def test_receipt_binds_taskdoc_contract_and_validation(self):
        receipt = self._evaluate(self._payload())
        bindings = receipt["bindings"]
        self.assertEqual(bindings["taskdoc_bytes_sha256"],
                         self.source_payload["taskdoc"]["bytes_sha256"])
        self.assertEqual(bindings["source_facts_digest"], self.digest)
        self.assertEqual(len(bindings["validation_digest"]), 64)
        self.assertEqual(len(bindings["contract_digest"]), 64)

    def test_acceptance_verdict_is_null_on_every_status(self):
        cases = {}
        cases["PASSED"] = self._payload()
        pending = self._set_item(
            self._items(), "special_semantics", status="missing",
            applicable=True, rationale="存在 tie 场景但任务书未说明选哪个下标")
        cases["PASSED_WITH_PENDING"] = self._payload(items=pending)
        blocked_user = self._set_item(self._items(), "target_hardware",
                                      status="missing",
                                      rationale="任务书未写 SoC 型号")
        cases["NEEDS_USER"] = self._payload(items=blocked_user)
        cases["BLOCKED"] = self._payload(op="")
        for expected, payload in cases.items():
            with self.subTest(status=expected):
                receipt = self._evaluate(payload)
                self.assertEqual(receipt["status"], expected, receipt["errors"])
                self.assertIsNone(receipt["acceptance_verdict"])

    # --- must items block -------------------------------------------------

    def test_missing_must_item_blocks(self):
        items = self._set_item(self._items(), "golden_reference",
                               status="missing",
                               rationale="任务书未指明真值来源 API")
        receipt = self._evaluate(self._payload(items=items))
        self.assertEqual(receipt["status"], "NEEDS_USER")
        self.assertEqual([entry["id"] for entry in receipt["blocking_items"]],
                         ["golden_reference"])

    def test_ambiguous_must_item_blocks(self):
        items = self._set_item(self._items(), "input_contract",
                               status="ambiguous",
                               rationale="只写了「常用 dtype」，未量化枚举")
        receipt = self._evaluate(self._payload(items=items))
        self.assertEqual(receipt["status"], "NEEDS_USER")
        self.assertEqual([entry["id"] for entry in receipt["blocking_items"]],
                         ["input_contract"])

    def test_ambiguous_optional_item_blocks(self):
        items = self._set_item(self._items(), "extra_precision_requirement",
                               status="ambiguous",
                               rationale="只说「精度要求更高」，没有给数值")
        receipt = self._evaluate(self._payload(items=items))
        self.assertEqual(receipt["status"], "NEEDS_USER")
        self.assertEqual([entry["id"] for entry in receipt["blocking_items"]],
                         ["extra_precision_requirement"])

    def test_unsatisfied_item_without_rationale_is_blocked(self):
        items = self._set_item(self._items(), "input_contract", status="missing")
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
        self._write_taskdoc(_TASKDOC.replace(
            "目标硬件为 Atlas A3 单卡，功能、精度与性能均在该硬件验收",
            "目标硬件为 Atlas A3 单卡，\n功能、精度与性能均在该硬件验收"))
        receipt = self._evaluate(self._payload())
        self.assertEqual(receipt["status"], "PASSED", receipt["errors"])

    def test_normalization_keeps_ascii_token_boundaries(self):
        self.assertEqual(VTI._normalize("裁决\n口径"), "裁决口径")
        self.assertNotEqual(VTI._normalize("rank 1 23"), VTI._normalize("rank 12 3"))

    def test_reusing_one_quote_across_items_is_blocked(self):
        items = self._set_item(
            self._items(), "integration_form", status="satisfied",
            quotes=[{"text": _MUST_QUOTES["target_hardware"]}])
        receipt = self._evaluate(self._payload(items=items))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("复用同一条引用", receipt["errors"][0])

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

    def test_non_string_ids_are_blocked_not_crashed(self):
        for bad in ([], {}, None, 7, ""):
            with self.subTest(bad=bad):
                items = self._items()
                items[0] = dict(items[0], id=bad)
                receipt = self._evaluate(self._payload(items=items))
                self.assertEqual(receipt["status"], "BLOCKED")
                self.assertIn("id", receipt["errors"][0])

    def test_duplicate_json_keys_are_blocked(self):
        path = os.path.join(self.root, "dup.json")
        with open(path, "w", encoding="utf-8") as out:
            out.write('{"schema": "oprunway.taskdoc_validation", '
                      '"schema_version": 1, "op": "a", "op": "b"}')
        receipt = VTI.evaluate(self.root, "dup.json")
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("重复键", receipt["errors"][0])

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

    def test_applicable_dependencies_block(self):
        items = self._set_item(
            self._items(), "dependencies_and_prerequisites", status="missing",
            applicable=True, rationale="提到需要外部参考数据但没说从哪来")
        receipt = self._evaluate(self._payload(items=items))
        self.assertEqual(receipt["status"], "NEEDS_USER")
        self.assertEqual([entry["id"] for entry in receipt["blocking_items"]],
                         ["dependencies_and_prerequisites"])

    def test_conditional_item_without_applicable_flag_is_blocked(self):
        items = self._set_item(self._items(), "special_semantics",
                               status="missing", rationale="未说明")
        receipt = self._evaluate(self._payload(items=items))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("applicable", receipt["errors"][0])

    def test_not_applicable_without_rationale_is_blocked(self):
        items = self._set_item(self._items(), "special_semantics",
                               status="not_applicable", applicable=False,
                               quotes=[{"text": _NOT_APPLICABLE_QUOTES[
                                   "special_semantics"]}])
        receipt = self._evaluate(self._payload(items=items))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("rationale", receipt["errors"][0])

    def test_not_applicable_without_a_taskdoc_quote_is_blocked(self):
        items = self._set_item(self._items(), "special_semantics",
                               status="not_applicable", applicable=False,
                               rationale="我判断这个算子不会有并列值")
        receipt = self._evaluate(self._payload(items=items))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("quotes", receipt["errors"][0])

    # --- deliverable inventory -------------------------------------------

    def test_shipped_contract_declares_the_deliverable_inventory(self):
        contract = VTI.load_contract()
        inventory = contract["deliverable_inventory"]
        by_id = {item["id"] for item in contract["items"]}
        self.assertIn(inventory["owner_item"], by_id)
        self.assertEqual(sorted(inventory["markers"]), ["optional", "required"])
        self.assertTrue(inventory["markers"]["required"])
        self.assertTrue(inventory["markers"]["optional"])

    def test_contract_without_a_deliverable_inventory_is_rejected(self):
        contract = VTI.load_contract()
        del contract["deliverable_inventory"]
        with self.assertRaises(VTI.TaskdocValidationError):
            VTI.load_contract(self._write_contract(contract))

    def test_contract_with_an_unknown_inventory_owner_is_rejected(self):
        contract = VTI.load_contract()
        contract["deliverable_inventory"]["owner_item"] = "not_an_item"
        with self.assertRaises(VTI.TaskdocValidationError):
            VTI.load_contract(self._write_contract(contract))

    def test_contract_with_a_marker_in_both_classes_is_rejected(self):
        contract = VTI.load_contract()
        contract["deliverable_inventory"]["markers"]["optional"].append(
            contract["deliverable_inventory"]["markers"]["required"][0])
        with self.assertRaises(VTI.TaskdocValidationError):
            VTI.load_contract(self._write_contract(contract))

    def test_overlapping_markers_of_one_class_count_as_a_single_site(self):
        sites = VTI._scan_modality_sites(
            "ACLNN 接口层必须交付",
            {"required": ["必须交付", "须交付"], "optional": ["可选"]})
        self.assertEqual([site["marker"] for site in sites], ["必须交付"])

    def test_marker_containment_across_classes_keeps_both_sites(self):
        sites = VTI._scan_modality_sites(
            "该件为非可选项", {"required": ["非可选"], "optional": ["可选"]})
        self.assertEqual(sorted(site["marker"] for site in sites),
                         ["可选", "非可选"])

    def test_receipt_carries_the_machine_readable_inventory(self):
        receipt = self._evaluate(self._payload())
        self.assertEqual(receipt["status"], "PASSED", receipt["errors"])
        inventory = receipt["deliverable_inventory"]
        self.assertTrue(inventory["complete"])
        self.assertEqual(inventory["uncovered_sites"], [])
        self.assertEqual(inventory["required_ids"], ["aclnn_layer"])
        self.assertEqual(inventory["optional_ids"], ["torch_wrapper"])
        self.assertEqual([entry["id"] for entry in receipt["deliverables"]],
                         ["aclnn_layer", "torch_wrapper"])

    def test_missing_deliverables_key_is_blocked(self):
        payload = self._payload()
        del payload["deliverables"]
        receipt = self._evaluate(payload)
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("deliverables", receipt["errors"][0])

    def test_a_required_marker_left_out_of_the_inventory_is_blocked(self):
        """本任务的见证缺口：摘一句沾边原文就判 satisfied，漏掉的必选件无人发现。"""
        payload = self._payload(deliverables=[
            entry for entry in copy.deepcopy(_DELIVERABLES)
            if entry["id"] != "aclnn_layer"])
        receipt = self._evaluate(payload)
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("交付定性标记", receipt["errors"][0])
        self.assertIn("必须交付", receipt["errors"][0])

    def test_an_optional_marker_left_out_of_the_inventory_is_blocked(self):
        payload = self._payload(deliverables=[
            entry for entry in copy.deepcopy(_DELIVERABLES)
            if entry["id"] != "torch_wrapper"])
        receipt = self._evaluate(payload)
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("可选", receipt["errors"][0])

    def test_labelling_a_required_deliverable_optional_is_blocked(self):
        deliverables = copy.deepcopy(_DELIVERABLES)
        deliverables[0]["requirement"] = "optional"
        receipt = self._evaluate(self._payload(deliverables=deliverables))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("requirement=optional", receipt["errors"][0])

    def test_uncovered_marker_is_recorded_when_the_owner_is_not_satisfied(self):
        """owner 未判 satisfied 时不额外阻断，但绝不把「没查清」记成「已覆盖」。"""
        items = self._set_item(self._items(), "delivery_scope",
                               status="ambiguous",
                               rationale="只写了要做什么，没划出范围边界")
        receipt = self._evaluate(self._payload(items=items, deliverables=[]))
        self.assertEqual(receipt["status"], "NEEDS_USER")
        inventory = receipt["deliverable_inventory"]
        self.assertFalse(inventory["complete"])
        self.assertEqual(
            sorted(site["marker"] for site in inventory["uncovered_sites"]),
            ["可选", "必须交付"])

    def test_fabricated_deliverable_quote_is_blocked(self):
        deliverables = copy.deepcopy(_DELIVERABLES)
        deliverables[0]["quotes"] = [{"text": "CUDA 参考实现必须交付"}]
        receipt = self._evaluate(self._payload(deliverables=deliverables))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("未逐字出现在任务书原文", receipt["errors"][0])

    def test_deliverable_quote_cannot_be_stolen_from_another_item(self):
        deliverables = copy.deepcopy(_DELIVERABLES)
        deliverables[0]["quotes"] = [{"text": _MUST_QUOTES["target_hardware"]}]
        receipt = self._evaluate(self._payload(deliverables=deliverables))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("复用同一条引用", receipt["errors"][0])

    def test_duplicate_deliverable_id_is_blocked(self):
        deliverables = copy.deepcopy(_DELIVERABLES)
        deliverables.append(dict(deliverables[0]))
        receipt = self._evaluate(self._payload(deliverables=deliverables))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("重复 id", receipt["errors"][0])

    def test_deliverable_without_a_name_is_blocked(self):
        deliverables = copy.deepcopy(_DELIVERABLES)
        del deliverables[0]["name"]
        receipt = self._evaluate(self._payload(deliverables=deliverables))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("name", receipt["errors"][0])

    def test_exemption_can_cover_a_marker_that_is_not_a_deliverable(self):
        self._write_taskdoc(_TASKDOC + "调用流程允许可选的预处理步骤。\n")
        payload = self._payload(deliverable_scan_exemptions=[{
            "quote": {"text": "调用流程允许可选的预处理步骤"},
            "rationale": "这处「可选」修饰的是调用流程里的一步，不是交付件"}])
        receipt = self._evaluate(payload)
        self.assertEqual(receipt["status"], "PASSED", receipt["errors"])
        self.assertEqual(
            receipt["deliverable_inventory"]["exemptions"][0]["rationale"],
            "这处「可选」修饰的是调用流程里的一步，不是交付件")

    def test_exemption_without_a_rationale_is_blocked(self):
        self._write_taskdoc(_TASKDOC + "调用流程允许可选的预处理步骤。\n")
        payload = self._payload(deliverable_scan_exemptions=[{
            "quote": {"text": "调用流程允许可选的预处理步骤"}}])
        receipt = self._evaluate(payload)
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("rationale", receipt["errors"][0])

    def test_exempting_a_required_marker_never_passes_on_free_text_alone(self):
        """★ audit#53：一段自由文本 rationale 就把一处**必选**标记豁免掉 = 门交给被审查方。

        必选标记的豁免**不计入覆盖**：该处仍留在 uncovered，于是 owner 判 satisfied 时照旧
        结构性阻断（BLOCKED），owner 未判 satisfied 时落 complete=false → NEEDS_USER。
        无论哪条路，都不会再出现「一句话豁免掉一处必选」这种静默放行。
        """
        exemption = [{"quote": {"text": "ACLNN 接口层必须交付"},
                      "rationale": "我认为这不是交付件"}]
        deliverables = [entry for entry in copy.deepcopy(_DELIVERABLES)
                        if entry["id"] != "aclnn_layer"]
        receipt = self._evaluate(self._payload(
            deliverables=deliverables, deliverable_scan_exemptions=exemption))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("必须交付", receipt["errors"][0])

        items = self._set_item(self._items(), "delivery_scope",
                               status="ambiguous", rationale="范围没划清")
        receipt = self._evaluate(self._payload(
            items=items, deliverables=deliverables,
            deliverable_scan_exemptions=exemption))
        self.assertEqual(receipt["status"], "NEEDS_USER", receipt["errors"])
        inventory = receipt["deliverable_inventory"]
        self.assertFalse(inventory["complete"])
        self.assertTrue(inventory["exemptions"][0]["requires_user_confirmation"])

    def test_incomplete_inventory_can_never_reach_passed(self):
        """★ audit#52：owner 判 ambiguous 再用一条 supplied 决策消化掉，以前能落成 PASSED。"""
        items = self._set_item(self._items(), "delivery_scope",
                               status="ambiguous",
                               rationale="只写了要做什么，没划出范围边界")
        payload = self._payload(
            items=items, deliverables=[],
            decisions=[{"id": "delivery_scope", "action": "supplied",
                        "resolved_status": "ambiguous", "source": "user",
                        "value": "本次交付范围以 PR 实际改动为准",
                        "rationale": "由需求方口头确认"}])
        receipt = self._evaluate(payload)
        self.assertEqual(receipt["status"], "NEEDS_USER")
        self.assertFalse(receipt["deliverable_inventory"]["complete"])
        self.assertTrue(any(
            item.get("status") == "deliverable_inventory_incomplete"
            for item in receipt["blocking_items"]), receipt["blocking_items"])

    def test_zero_marker_hits_is_pending_not_complete(self):
        """★ audit#56：受控词表零命中 ≠ 清单已穷尽——须由人显式承认穷尽性。"""
        self._write_taskdoc(_TASKDOC.replace(
            "交付件清单：ACLNN 接口层必须交付；Torch 封装为可选交付项。\n", ""))
        payload = self._payload(deliverables=[])
        receipt = self._evaluate(payload)
        self.assertEqual(receipt["status"], "NEEDS_USER")
        inventory = receipt["deliverable_inventory"]
        self.assertTrue(inventory["zero_marker_sites"])
        self.assertFalse(inventory["complete"])

    def test_zero_marker_hits_with_a_bound_user_ack_is_complete(self):
        text = _TASKDOC.replace(
            "交付件清单：ACLNN 接口层必须交付；Torch 封装为可选交付项。\n", "")
        self._write_taskdoc(text)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        payload = self._payload(
            deliverables=[],
            deliverable_inventory_exhaustive={
                "taskdoc_bytes_sha256": digest, "source": "user",
                "rationale": "逐段读过，任务书未用受控词表之外的写法声明交付义务"})
        receipt = self._evaluate(payload)
        self.assertEqual(receipt["status"], "PASSED", receipt["errors"])
        self.assertTrue(receipt["deliverable_inventory"]["complete"])

    def test_exhaustiveness_ack_bound_to_another_taskdoc_is_blocked(self):
        text = _TASKDOC.replace(
            "交付件清单：ACLNN 接口层必须交付；Torch 封装为可选交付项。\n", "")
        self._write_taskdoc(text)
        payload = self._payload(
            deliverables=[],
            deliverable_inventory_exhaustive={
                "taskdoc_bytes_sha256": "1" * 64, "source": "user",
                "rationale": "从上一份任务书搬过来的承认"})
        receipt = self._evaluate(payload)
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("deliverable_inventory_exhaustive", receipt["errors"][0])

    def test_a_quote_appearing_twice_must_pin_its_occurrence(self):
        """★ audit#55：同一句原文出现多次时，引一次就覆盖 N 处 = 不受限的多对多覆盖。"""
        self._write_taskdoc(_TASKDOC + "ACLNN 接口层必须交付。\n")
        receipt = self._evaluate(self._payload())
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("occurrence", receipt["errors"][0])

    def test_a_quote_with_both_modalities_cannot_downgrade_a_required_item(self):
        """★ audit#54：一句「A 必选、B 可选」以前整段跳过一致性检查。"""
        self._write_taskdoc(
            _TASKDOC + "适配层必须交付，性能报告为可选交付项。\n")
        deliverables = copy.deepcopy(_DELIVERABLES)
        deliverables.append({
            "id": "adapter_layer", "name": "适配层", "requirement": "optional",
            "quotes": [{"text": "适配层必须交付，性能报告为可选交付项"}]})
        receipt = self._evaluate(self._payload(deliverables=deliverables))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("required", receipt["errors"][0])

    def test_an_unexempted_extra_marker_blocks(self):
        self._write_taskdoc(_TASKDOC + "调用流程允许可选的预处理步骤。\n")
        receipt = self._evaluate(self._payload())
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("交付定性标记", receipt["errors"][0])

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
            items=items, perf_required=False, perf_evidence=[],
            perf_required_rationale="通读任务书未发现任何性能条款"))
        self.assertEqual(receipt["status"], "PASSED", receipt["errors"])
        self.assertIs(receipt["perf_required"], False)

    def test_stale_perf_evidence_with_perf_not_required_is_blocked(self):
        items = self._items()
        for item_id in ("performance_baseline", "performance_metric_scope"):
            self._set_item(items, item_id, status="not_applicable",
                           rationale="任务书未提出性能要求")
        receipt = self._evaluate(self._payload(
            items=items, perf_required=False,
            perf_required_rationale="声称没有性能条款"))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("perf_evidence", receipt["errors"][0])

    def test_perf_not_required_still_needs_a_rationale(self):
        items = self._items()
        for item_id in ("performance_baseline", "performance_metric_scope"):
            self._set_item(items, item_id, status="not_applicable",
                           rationale="任务书未提出性能要求")
        receipt = self._evaluate(self._payload(items=items, perf_required=False,
                                               perf_evidence=[]))
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
        receipt = self._evaluate(self._payload(
            items=items,
            decisions=[self._decision(
                "target_hardware", "supplied", "missing",
                value="Atlas A3，功能/精度/性能均在 A3 验收")]))
        self.assertEqual(receipt["status"], "PASSED", receipt["errors"])
        self.assertEqual([entry["id"] for entry in receipt["decided_items"]],
                         ["target_hardware"])
        self.assertEqual(receipt["confirmed_constraints_candidates"], [{
            "key": "taskdoc:target_hardware",
            "value": "Atlas A3，功能/精度/性能均在 A3 验收",
            "source": "user"}])

    def test_blocking_item_cannot_be_waived(self):
        items = self._set_item(self._items(), "golden_reference",
                               status="missing", rationale="任务书未指明真值来源")
        receipt = self._evaluate(self._payload(
            items=items,
            decisions=[self._decision("golden_reference", "waived", "missing",
                                      rationale="先跑起来再说")]))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("只接受", receipt["errors"][0])

    def test_pending_item_can_be_waived(self):
        items = self._set_item(
            self._items(), "special_semantics", status="missing",
            applicable=True, rationale="存在 tie 场景但任务书未说明")
        receipt = self._evaluate(self._payload(
            items=items,
            decisions=[self._decision("special_semantics", "waived", "missing",
                                      rationale="需求方确认 tie 取任意合法下标")]))
        self.assertEqual(receipt["status"], "PASSED", receipt["errors"])
        self.assertEqual(receipt["decided_items"][0]["decision"]["action"],
                         "waived")

    def test_decision_on_a_satisfied_item_is_blocked(self):
        receipt = self._evaluate(self._payload(decisions=[
            self._decision("target_hardware", "supplied", "missing",
                           value="A3")]))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("不在本轮阻断/待确认项内", receipt["errors"][0])

    def test_decision_from_a_stale_round_is_blocked(self):
        items = self._set_item(self._items(), "target_hardware",
                               status="ambiguous", rationale="只写了「昇腾」")
        receipt = self._evaluate(self._payload(
            items=items,
            decisions=[self._decision("target_hardware", "supplied", "missing",
                                      value="A3 单卡")]))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("resolved_status", receipt["errors"][0])

    def test_decision_must_declare_a_user_source(self):
        items = self._set_item(self._items(), "target_hardware",
                               status="missing", rationale="任务书未写 SoC 型号")
        decision = self._decision("target_hardware", "supplied", "missing",
                                  value="A3")
        decision["source"] = "agent"
        receipt = self._evaluate(self._payload(items=items,
                                               decisions=[decision]))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("source", receipt["errors"][0])

    def test_supplied_decision_needs_a_value(self):
        items = self._set_item(self._items(), "target_hardware",
                               status="missing", rationale="任务书未写 SoC 型号")
        receipt = self._evaluate(self._payload(
            items=items,
            decisions=[self._decision("target_hardware", "supplied", "missing")]))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("value", receipt["errors"][0])

    def test_duplicate_decision_is_blocked(self):
        items = self._set_item(self._items(), "target_hardware",
                               status="missing", rationale="任务书未写 SoC 型号")
        decision = self._decision("target_hardware", "supplied", "missing",
                                  value="A3 单卡")
        receipt = self._evaluate(self._payload(items=items,
                                               decisions=[decision,
                                                          dict(decision)]))
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

    def test_swapped_taskdoc_with_stale_facts_is_blocked(self):
        payload = self._payload()
        name = self._write(payload)
        with open(os.path.join(self.root, "task_doc.md"), "a",
                  encoding="utf-8") as out:
            out.write("\n目标硬件改为 Atlas A5 单卡。\n")
        receipt = VTI.evaluate(self.root, name)
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("任务书字节与事实包不符", receipt["errors"][0])

    def test_source_facts_without_taskdoc_sha_is_blocked(self):
        content_address.write_artifact(
            self.root, "source_facts.json", VTI._SOURCE_DOMAIN, {"taskdoc": {}})
        digest = content_address.content_digest(VTI._SOURCE_DOMAIN,
                                                {"taskdoc": {}})
        receipt = self._evaluate(self._payload(source_facts_digest=digest))
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("bytes_sha256", receipt["errors"][0])

    def test_malformed_taskdoc_facts_are_blocked_not_crashed(self):
        for bad in ([], "sha", 7, None):
            with self.subTest(bad=bad):
                content_address.write_artifact(
                    self.root, "source_facts.json", VTI._SOURCE_DOMAIN,
                    {"taskdoc": bad})
                digest = content_address.content_digest(
                    VTI._SOURCE_DOMAIN, {"taskdoc": bad})
                receipt = self._evaluate(
                    self._payload(source_facts_digest=digest))
                self.assertEqual(receipt["status"], "BLOCKED")
                self.assertIn("source_facts.taskdoc", receipt["errors"][0])

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
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(VTI._DRY_RUN_ENV, None)
            code = VTI.main(["--root", self.root, "--validation", name,
                             "--out", "taskdoc_validation_receipt.json"])
        self.assertEqual(code, 2)
        receipt = content_address.read_artifact(
            self.root, "taskdoc_validation_receipt.json", VTI._RECEIPT_DOMAIN)
        self.assertEqual(receipt["status"], "NEEDS_USER")
        self.assertIsNone(receipt["acceptance_verdict"])

    def test_dry_run_does_not_write_the_receipt(self):
        name = self._write(self._payload())
        with mock.patch.dict(os.environ, {VTI._DRY_RUN_ENV: "1"}):
            code = VTI.main(["--root", self.root, "--validation", name,
                             "--out", "taskdoc_validation_receipt.json"])
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(
            os.path.join(self.root, "taskdoc_validation_receipt.json")))


if __name__ == "__main__":
    unittest.main()
