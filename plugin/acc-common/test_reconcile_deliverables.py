#!/usr/bin/env python3
"""必选交付件 ↔ PR 实际交付物对账单测。

见证任务书、见证清单直接复用 `test_validate_taskdoc_input` 的 fixture：
对账消费的就是 CP-B0 那份工件，两边共用一套见证才能保证接缝没错位。
"""

import copy
import hashlib
import json
import os
import tempfile
import unittest
from unittest import mock

import content_address
import reconcile_deliverables as RD
import test_validate_taskdoc_input as TVI
import validate_taskdoc_input as VTI


_PR_FACTS = {
    "pr_url": "https://gitcode.com/cann/ops-nn/merge_requests/1234",
    "head_sha": "0" * 40,
    "source_repo": "cann/ops-nn",
    "changed_files": [
        "math/witness_op/op_host/op_api/aclnn_witness_op.h",
        "math/witness_op/op_host/witness_op_def.cpp",
        "math/witness_op/op_kernel/witness_op.cpp",
    ],
    "key_files": {
        "math/witness_op/op_host/op_api/aclnn_witness_op.h":
            "aclnnStatus aclnnWitnessOpGetWorkspaceSize(const aclTensor *x);",
    },
}


class ReconcileDeliverablesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self._write_taskdoc(TVI._TASKDOC)
        self._write_pr_facts(_PR_FACTS)
        self.validation = self._write_validation(self._validation_payload())

    # --- fixture builders -------------------------------------------------

    def _write_taskdoc(self, text):
        raw = text.encode("utf-8")
        with open(os.path.join(self.root, "task_doc.md"), "wb") as out:
            out.write(raw)
        payload = {"taskdoc": {"bytes_sha256": hashlib.sha256(raw).hexdigest()}}
        content_address.write_artifact(self.root, "source_facts.json",
                                       VTI._SOURCE_DOMAIN, payload)
        self.digest = content_address.content_digest(VTI._SOURCE_DOMAIN, payload)

    def _write_pr_facts(self, facts, name="pr_facts.json"):
        raw = json.dumps(facts, ensure_ascii=False).encode("utf-8")
        with open(os.path.join(self.root, name), "wb") as out:
            out.write(raw)
        self.pr_facts_sha256 = hashlib.sha256(raw).hexdigest()
        return name

    def _items(self):
        items = [{"id": item_id, "status": "satisfied",
                  "quotes": [{"text": text}]}
                 for item_id, text in TVI._MUST_QUOTES.items()]
        for item_id, text in TVI._NOT_APPLICABLE_QUOTES.items():
            items.append({
                "id": item_id, "status": "not_applicable", "applicable": False,
                "quotes": [{"text": text}],
                "rationale": f"任务书原文已排除该场景：{text}",
            })
        items.append({"id": "extra_precision_requirement", "status": "missing",
                      "rationale": "任务书未声明额外精度要求，按 workflow 标准"})
        return items

    def _validation_payload(self, **overrides):
        payload = {
            "schema": VTI._VALIDATION_SCHEMA,
            "schema_version": 1,
            "op": "witness_op",
            "source_facts_digest": self.digest,
            "perf_required": True,
            "perf_evidence": [{"text": "性能要求为相对基线不劣化"}],
            "deliverables": copy.deepcopy(TVI._DELIVERABLES),
            "items": self._items(),
            "decisions": [],
        }
        payload.update(overrides)
        return payload

    def _write_validation(self, payload, name="taskdoc_validation.json"):
        with open(os.path.join(self.root, name), "w", encoding="utf-8") as out:
            json.dump(payload, out, ensure_ascii=False)
        return name

    def _bindings(self):
        receipt = VTI.evaluate(self.root, self.validation)
        self.assertNotEqual(receipt["status"], "BLOCKED", receipt["errors"])
        return receipt["bindings"]

    def _write_mapping(self, mappings, name="deliverable_mapping.json",
                       **overrides):
        bindings = self._bindings()
        payload = {
            "schema": RD._MAPPING_SCHEMA,
            "schema_version": 1,
            "taskdoc_bytes_sha256": bindings["taskdoc_bytes_sha256"],
            "taskdoc_validation_digest": bindings["validation_digest"],
            "pr_facts_sha256": self.pr_facts_sha256,
            "mappings": mappings,
        }
        payload.update(overrides)
        with open(os.path.join(self.root, name), "w", encoding="utf-8") as out:
            json.dump(payload, out, ensure_ascii=False)
        return name

    def _evaluate(self, **kwargs):
        kwargs.setdefault("validation_rel", self.validation)
        return RD.evaluate(self.root, **kwargs)

    def _present(self, **fields):
        entry = {"id": "aclnn_layer", "disposition": "present"}
        entry.update(fields)
        return [entry]

    # --- no mapping = fail-closed ----------------------------------------

    def test_without_a_mapping_every_required_deliverable_is_a_gap(self):
        result = self._evaluate()
        self.assertEqual(result["status"], "GAPS")
        self.assertEqual([gap["id"] for gap in result["gaps"]], ["aclnn_layer"])
        self.assertEqual(result["gaps"][0]["reason"], "unmapped")
        self.assertEqual(result["covered"], [])

    def test_optional_deliverables_never_block_the_verdict(self):
        self._write_mapping(self._present(
            paths=["math/witness_op/op_host/op_api/aclnn_witness_op.h"]))
        result = self._evaluate()
        self.assertEqual(result["status"], "RECONCILED", result["errors"])
        self.assertEqual([entry["id"] for entry in result["optional_findings"]],
                         ["torch_wrapper"])
        self.assertEqual(result["optional_findings"][0]["reason"], "unmapped")

    def test_no_verdict_is_ever_produced(self):
        for label in ("no-mapping", "mapped"):
            with self.subTest(label=label):
                if label == "mapped":
                    self._write_mapping(self._present(
                        paths=["math/witness_op/op_kernel/witness_op.cpp"]))
                result = self._evaluate()
                self.assertIsNone(result["acceptance_verdict"])

    # --- evidence verification -------------------------------------------

    def test_exact_changed_file_is_accepted(self):
        self._write_mapping(self._present(
            paths=["math/witness_op/op_kernel/witness_op.cpp"]))
        result = self._evaluate()
        self.assertEqual(result["status"], "RECONCILED", result["errors"])
        self.assertEqual(result["covered"][0]["evidence"][0]["match"], "file")

    def test_directory_prefix_is_accepted_as_a_home(self):
        self._write_mapping(self._present(paths=["math/witness_op/op_host"]))
        result = self._evaluate()
        self.assertEqual(result["status"], "RECONCILED", result["errors"])
        self.assertEqual(result["covered"][0]["evidence"][0]["match"],
                         "directory")

    def test_verbatim_symbol_in_a_key_file_is_accepted(self):
        self._write_mapping(self._present(
            symbols=["aclnnWitnessOpGetWorkspaceSize"]))
        result = self._evaluate()
        self.assertEqual(result["status"], "RECONCILED", result["errors"])
        self.assertEqual(result["covered"][0]["evidence"][0]["key_file"],
                         "math/witness_op/op_host/op_api/aclnn_witness_op.h")

    def test_a_path_the_pr_does_not_contain_is_a_gap_not_a_pass(self):
        self._write_mapping(self._present(
            paths=["opencv/adapters/gaussian_blur_adapter.cpp"]))
        result = self._evaluate()
        self.assertEqual(result["status"], "GAPS")
        self.assertEqual(result["gaps"][0]["reason"], "evidence_not_found")
        self.assertIn("gaussian_blur_adapter.cpp", result["gaps"][0]["detail"])

    def test_a_symbol_absent_from_key_files_is_a_gap(self):
        self._write_mapping(self._present(symbols=["aclnnSomethingElse"]))
        result = self._evaluate()
        self.assertEqual(result["status"], "GAPS")
        self.assertEqual(result["gaps"][0]["reason"], "evidence_not_found")

    def test_partly_verified_evidence_still_counts_as_a_gap(self):
        self._write_mapping(self._present(
            paths=["math/witness_op/op_kernel/witness_op.cpp",
                   "math/witness_op/op_host/nope.cpp"]))
        result = self._evaluate()
        self.assertEqual(result["status"], "GAPS")
        self.assertEqual(result["gaps"][0]["reason"], "evidence_not_found")

    def test_no_fuzzy_name_matching_on_the_deliverable_name(self):
        """清单里叫 ACLNN 接口层、PR 里有 aclnn_witness_op.h，也不算自动归宿。"""
        result = self._evaluate()
        self.assertEqual(result["gaps"][0]["reason"], "unmapped")

    # --- explicit non-coverage --------------------------------------------

    def test_absent_disposition_becomes_a_structured_gap(self):
        self._write_mapping([{"id": "aclnn_layer", "disposition": "absent",
                              "rationale": "通读改动文件，本 PR 未交付该层"}])
        result = self._evaluate()
        self.assertEqual(result["status"], "GAPS")
        self.assertEqual(result["gaps"][0]["reason"], "missing_in_pr")
        self.assertEqual(result["gaps"][0]["rationale"],
                         "通读改动文件，本 PR 未交付该层")

    def test_uncertain_disposition_becomes_a_structured_gap(self):
        self._write_mapping([{"id": "aclnn_layer", "disposition": "uncertain",
                              "rationale": "描述太笼统，认不出对应哪些文件"}])
        result = self._evaluate()
        self.assertEqual(result["status"], "GAPS")
        self.assertEqual(result["gaps"][0]["reason"], "undetermined")

    def test_present_without_any_home_is_blocked(self):
        self._write_mapping(self._present())
        result = self._evaluate()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("paths", result["errors"][0])

    def test_non_present_disposition_needs_a_rationale(self):
        self._write_mapping([{"id": "aclnn_layer", "disposition": "absent"}])
        result = self._evaluate()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("rationale", result["errors"][0])

    def test_too_short_a_symbol_is_blocked(self):
        self._write_mapping(self._present(symbols=["op"]))
        result = self._evaluate()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("过短", result["errors"][0])

    # --- inventory completeness ------------------------------------------

    def test_incomplete_inventory_can_never_be_reconciled(self):
        items = self._items()
        for item in items:
            if item["id"] == "delivery_scope":
                item.clear()
                item.update({"id": "delivery_scope", "status": "ambiguous",
                             "rationale": "只写了要做什么，没划范围边界"})
        self.validation = self._write_validation(
            self._validation_payload(items=items, deliverables=[]))
        result = self._evaluate()
        self.assertEqual(result["status"], "GAPS")
        self.assertFalse(result["inventory_complete"])
        self.assertEqual(result["gaps"][0]["reason"], "inventory_incomplete")

    def test_blocked_taskdoc_validation_blocks_the_reconciliation(self):
        self.validation = self._write_validation(
            self._validation_payload(op=""))
        result = self._evaluate()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("BLOCKED", result["errors"][0])

    # --- binding drift ----------------------------------------------------

    def test_mapping_bound_to_another_pr_is_blocked(self):
        self._write_mapping(self._present(
            paths=["math/witness_op/op_kernel/witness_op.cpp"]))
        stale = dict(_PR_FACTS,
                     changed_files=_PR_FACTS["changed_files"] + ["extra.cpp"])
        self._write_pr_facts(stale)
        result = self._evaluate()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("pr_facts_sha256", result["errors"][0])

    def test_mapping_bound_to_another_taskdoc_is_blocked(self):
        self._write_mapping(self._present(
            paths=["math/witness_op/op_kernel/witness_op.cpp"]),
            taskdoc_bytes_sha256="1" * 64)
        result = self._evaluate()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("taskdoc_bytes_sha256", result["errors"][0])

    def test_mapping_for_an_unknown_deliverable_is_blocked(self):
        self._write_mapping([{"id": "invented", "disposition": "absent",
                              "rationale": "凭空多出来的条目"}])
        result = self._evaluate()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("invented", result["errors"][0])

    def test_duplicate_mapping_is_blocked(self):
        entry = {"id": "aclnn_layer", "disposition": "absent",
                 "rationale": "未交付"}
        self._write_mapping([entry, dict(entry)])
        result = self._evaluate()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("重复指认", result["errors"][0])

    # --- PR side unusable -------------------------------------------------

    def test_empty_changed_files_never_reads_as_covered(self):
        self._write_pr_facts(dict(_PR_FACTS, changed_files=[]))
        result = self._evaluate()
        self.assertEqual(result["status"], "GAPS")
        self.assertEqual(result["gaps"][0]["reason"], "pr_evidence_unavailable")

    def test_pr_side_blocked_flag_is_propagated_as_a_gap(self):
        self._write_pr_facts(dict(_PR_FACTS, blocked="missing_head_sha"))
        result = self._evaluate()
        self.assertEqual(result["status"], "GAPS")
        self.assertEqual(result["gaps"][0]["reason"], "pr_side_blocked")

    def test_missing_pr_facts_is_blocked(self):
        os.unlink(os.path.join(self.root, "pr_facts.json"))
        result = self._evaluate()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("pr_facts", result["errors"][0])

    def test_duplicate_json_keys_in_pr_facts_are_blocked(self):
        with open(os.path.join(self.root, "pr_facts.json"), "w",
                  encoding="utf-8") as out:
            out.write('{"changed_files": [], "changed_files": ["a"]}')
        result = self._evaluate()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("重复键", result["errors"][0])

    # --- CLI --------------------------------------------------------------

    def test_exit_codes_split_gaps_from_blocked(self):
        self.assertEqual(RD._exit_code("RECONCILED"), 0)
        self.assertEqual(RD._exit_code("GAPS"), 2)
        self.assertEqual(RD._exit_code("BLOCKED"), 1)

    def test_main_writes_the_artifact_and_returns_gaps(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(RD._DRY_RUN_ENV, None)
            code = RD.main(["--root", self.root, "--validation", self.validation,
                            "--out", "deliverable_reconciliation.json"])
        self.assertEqual(code, 2)
        artifact = content_address.read_artifact(
            self.root, "deliverable_reconciliation.json",
            RD._RECONCILIATION_DOMAIN)
        self.assertEqual(artifact["status"], "GAPS")
        self.assertIsNone(artifact["acceptance_verdict"])

    def test_dry_run_does_not_write_the_artifact(self):
        with mock.patch.dict(os.environ, {RD._DRY_RUN_ENV: "1"}):
            code = RD.main(["--root", self.root, "--validation", self.validation,
                            "--out", "deliverable_reconciliation.json"])
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(os.path.join(
            self.root, "deliverable_reconciliation.json")))


if __name__ == "__main__":
    unittest.main()
