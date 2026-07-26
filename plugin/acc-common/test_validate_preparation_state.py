#!/usr/bin/env python3
"""非真机准备状态复用校验器单测。"""

import hashlib
import json
import os
import tempfile
import unittest
from unittest import mock

import content_address
import validate_preparation_state as VPS


class PreparationStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.spec = {"op": "AnyOp", "params": []}
        self.spec_sha = hashlib.sha256(
            content_address.canonical_json_bytes(self.spec)).hexdigest()
        with open(os.path.join(self.root, "spec.json"), "w", encoding="utf-8") as out:
            json.dump(self.spec, out)
        self.golden = os.path.join(self.root, "golden.py")
        with open(self.golden, "wb") as out:
            out.write(b"def golden_fn(x): return x\n")
        self.golden_sha = hashlib.sha256(
            b"def golden_fn(x): return x\n").hexdigest()
        self.snapshot = os.path.join(self.root, "task_doc.snapshot.md")
        with open(self.snapshot, "wb") as out:
            out.write(b"task document\n")
        self.snapshot_sha = hashlib.sha256(b"task document\n").hexdigest()
        self.head_sha = "a" * 40
        self.key_sha = hashlib.sha256(b"header").hexdigest()
        source = {
            "contract_version": 1,
            "taskdoc": {
                "source_locator": "<local-file>",
                "bytes_sha256": self.snapshot_sha,
                "snapshot_sha256": self.snapshot_sha,
                "size": len(b"task document\n"),
            },
            "pr": {
                "canonical_url": "https://gitcode.com/cann/ops-nn/pull/1",
                "source_repo": "cann/ops-nn",
                "number": 1,
                "head_sha": self.head_sha,
                "head_repo": "contributor/ops-nn",
                "is_fork": True,
                "state": "open",
            },
            "changed_files": ["experimental/index/op/op_host/op_api/aclnn_op.h"],
            "key_files": [{
                "path": "experimental/index/op/op_host/op_api/aclnn_op.h",
                "ref": self.head_sha,
                "bytes_sha256": self.key_sha,
                "size": len(b"header"),
            }],
            "derived": {
                "op": "op",
                "target_dir": "experimental/index/op",
                "aclnn_headers": [
                    "experimental/index/op/op_host/op_api/aclnn_op.h"],
                "interface_kind": "aclnn_2stage",
                "aclnn_entry": "aclnnOp",
            },
            "completeness": {"status": "complete", "reasons": []},
            "producer": {
                "tool": "fetch_source.py",
                "logic_sha256": VPS._file_sha256(
                    os.path.join(os.path.dirname(VPS.__file__),
                                 "fetch_source.py")),
            },
        }
        content_address.write_artifact(
            self.root, "source_facts.json", VPS._SOURCE_DOMAIN, source)
        self.source_digest = content_address.content_digest(
            VPS._SOURCE_DOMAIN, source)
        self._write_json("correspondence.json", {
            "status": "confirmed",
            "source_facts_digest": self.source_digest,
        })
        self.correspondence_sha = hashlib.sha256(
            content_address.canonical_json_bytes({
                "status": "confirmed",
                "source_facts_digest": self.source_digest,
            })).hexdigest()
        planner = os.path.join(os.path.dirname(VPS.__file__), "gen_cases.py")
        planner_sha = VPS._file_sha256(planner)
        planner_logic = {
            filename: VPS._file_sha256(
                os.path.join(os.path.dirname(VPS.__file__), filename))
            for filename in VPS._PLANNER_DEPENDENCIES
        }
        self.plan = {
            "schema": VPS._LEDGER_SCHEMA,
            "schema_version": 1,
            "spec_binding": {"sha256": self.spec_sha},
            "planner_binding": {
                "gen_cases_py_sha256": planner_sha,
                "logic_files": planner_logic,
            },
            "preparation_inputs": {
                "source_facts_digest": self.source_digest,
                "correspondence_sha256": self.correspondence_sha,
            },
            "planning": {
                "case_target": 1,
                "runner_form": "aclnn_py",
            },
            "summary": {
                "emitted": 1,
                "pool_max": 1,
                "forced_total": 1,
                "forced_special": 0,
                "by_dtype": {"float32": 1},
                "shapes": ["1"],
                "id_kinds": {"wl0": 1},
            },
            "coverage": {
                "strength": "fixture",
                "golden_cost": {},
                "dropped_combo_classes": [],
                "unpaired_combo_classes": {},
            },
            "determinism": {
                "case_id": "fixture",
                "equal": True,
            },
            "golden_dependency": {
                "status": "loaded",
                "bytes_sha256": self.golden_sha,
                "contract_sha256": hashlib.sha256(b"null").hexdigest(),
            },
        }
        self.plan["ledger_digest"] = content_address.content_digest(
            VPS._CASE_PLAN_DOMAIN, self.plan)
        self._write_json("case_plan.json", self.plan)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_json(self, rel, value):
        with open(os.path.join(self.root, rel), "w", encoding="utf-8") as out:
            json.dump(value, out)

    def _evaluate(self):
        return VPS.evaluate(
            self.root, "spec.json", "case_plan.json", golden_path=self.golden)

    def test_reusable_when_all_bindings_match(self):
        receipt = self._evaluate()
        self.assertEqual(receipt["status"], "REUSABLE")
        self.assertTrue(receipt["reusable"])
        self.assertIsNone(receipt["acceptance_verdict"])

    def test_source_change_requires_correspondence_reconfirmation(self):
        source = {
            "contract_version": 1,
            "taskdoc": {
                "source_locator": "<local-file>",
                "bytes_sha256": self.snapshot_sha,
                "snapshot_sha256": self.snapshot_sha,
                "size": len(b"task document\n"),
            },
            "pr": {
                "canonical_url": "https://gitcode.com/cann/ops-nn/pull/1",
                "source_repo": "cann/ops-nn",
                "number": 1,
                "head_sha": self.head_sha,
                "head_repo": "contributor/ops-nn",
                "is_fork": True,
                "state": "open",
            },
            "changed_files": ["experimental/index/op/op_host/op_api/aclnn_op.h"],
            "key_files": [{
                "path": "experimental/index/op/op_host/op_api/aclnn_op.h",
                "ref": self.head_sha,
                "bytes_sha256": self.key_sha,
                "size": len(b"header"),
            }],
            "derived": {
                "op": "op",
                "target_dir": "experimental/index/op",
                "aclnn_headers": [
                    "experimental/index/op/op_host/op_api/aclnn_op.h"],
                "interface_kind": "aclnn_2stage",
                "aclnn_entry": "aclnnOp",
            },
            "completeness": {"status": "complete", "reasons": []},
            "changed": True,
            "producer": {
                "tool": "fetch_source.py",
                "logic_sha256": VPS._file_sha256(
                    os.path.join(os.path.dirname(VPS.__file__),
                                 "fetch_source.py")),
            },
        }
        content_address.write_artifact(
            self.root, "source_facts.json", VPS._SOURCE_DOMAIN, source)
        receipt = self._evaluate()
        self.assertEqual(receipt["status"], "MISS")
        self.assertIn("correspondence", {
            item["name"] for item in receipt["checks"]
            if item["status"] == "MISS"})

    def test_confirmed_constraint_change_invalidates_case_plan(self):
        self._write_json("correspondence.json", {
            "status": "confirmed",
            "source_facts_digest": self.source_digest,
            "confirmed_constraints": [
                {"key": "dtype_required", "value": ["float32"], "source": "user"}
            ],
        })
        receipt = self._evaluate()
        self.assertEqual(receipt["status"], "MISS")
        self.assertIn("case_plan_inputs", {
            item["name"] for item in receipt["checks"]
            if item["status"] == "MISS"})

    def test_spec_planner_and_golden_drift_are_miss(self):
        changed = dict(self.spec, runner_form="aclnn_py")
        self._write_json("spec.json", changed)
        with open(self.golden, "ab") as out:
            out.write(b"# changed\n")
        self.plan["planner_binding"]["gen_cases_py_sha256"] = "0" * 64
        payload = dict(self.plan)
        payload.pop("ledger_digest")
        self.plan["ledger_digest"] = content_address.content_digest(
            VPS._CASE_PLAN_DOMAIN, payload)
        self._write_json("case_plan.json", self.plan)
        receipt = self._evaluate()
        misses = {item["name"] for item in receipt["checks"]
                  if item["status"] == "MISS"}
        self.assertTrue({"case_plan_spec", "case_planner", "golden"} <= misses)

    def test_planner_dependency_drift_is_miss(self):
        real_hash = VPS._file_sha256

        def changed(path):
            if path.endswith("repo_adapter.py"):
                return "0" * 64
            return real_hash(path)

        with mock.patch.object(VPS, "_file_sha256", side_effect=changed):
            receipt = self._evaluate()
        self.assertEqual(receipt["status"], "MISS")
        self.assertIn("case_planner", {
            item["name"] for item in receipt["checks"]
            if item["status"] == "MISS"})

    def test_tampered_source_is_blocked_not_cache_miss(self):
        path = os.path.join(self.root, "source_facts.json")
        artifact = json.load(open(path, encoding="utf-8"))
        artifact["payload"]["tampered"] = True
        self._write_json("source_facts.json", artifact)
        receipt = self._evaluate()
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertFalse(receipt["reusable"])

    def test_missing_golden_binding_never_reusable(self):
        self.plan["golden_dependency"] = {
            "status": "missing", "bytes_sha256": None, "contract_sha256": None}
        payload = dict(self.plan)
        payload.pop("ledger_digest")
        self.plan["ledger_digest"] = content_address.content_digest(
            VPS._CASE_PLAN_DOMAIN, payload)
        self._write_json("case_plan.json", self.plan)
        self.assertEqual(self._evaluate()["status"], "MISS")

    def test_case_plan_tampering_is_blocked(self):
        self.plan["spec_binding"]["sha256"] = "0" * 64
        self._write_json("case_plan.json", self.plan)
        receipt = self._evaluate()
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("ledger_digest", " ".join(
            item["reason"] for item in receipt["checks"]))

    def test_source_producer_logic_drift_is_miss(self):
        artifact = json.load(open(
            os.path.join(self.root, "source_facts.json"), encoding="utf-8"))
        artifact["payload"]["producer"]["logic_sha256"] = "0" * 64
        content_address.write_artifact(
            self.root, "source_facts.json", VPS._SOURCE_DOMAIN,
            artifact["payload"])
        receipt = self._evaluate()
        self.assertEqual(receipt["status"], "MISS")
        self.assertIn("source_producer", {
            item["name"] for item in receipt["checks"]
            if item["status"] == "MISS"})

    def test_taskdoc_snapshot_drift_is_miss(self):
        with open(self.snapshot, "ab") as out:
            out.write(b"changed\n")
        receipt = self._evaluate()
        self.assertEqual(receipt["status"], "MISS")
        self.assertIn("taskdoc_snapshot", {
            item["name"] for item in receipt["checks"]
            if item["status"] == "MISS"})

    def test_snapshot_default_follows_source_directory(self):
        os.makedirs(os.path.join(self.root, "work"))
        os.replace(
            os.path.join(self.root, "source_facts.json"),
            os.path.join(self.root, "work", "source_facts.json"))
        os.replace(
            os.path.join(self.root, "task_doc.snapshot.md"),
            os.path.join(self.root, "work", "task_doc.snapshot.md"))
        receipt = VPS.evaluate(
            self.root, "spec.json", "case_plan.json",
            golden_path=self.golden, source_rel="work/source_facts.json")
        self.assertEqual(receipt["status"], "REUSABLE")

    def test_spec_taskdoc_anchor_must_match_source(self):
        self.spec["golden"] = {
            "authorization": {"kind": "oracle_method"},
            "taskdoc_snapshot": {"sha256": "0" * 64},
        }
        self._write_json("spec.json", self.spec)
        self.plan["spec_binding"]["sha256"] = hashlib.sha256(
            content_address.canonical_json_bytes(self.spec)).hexdigest()
        payload = dict(self.plan)
        payload.pop("ledger_digest")
        self.plan["ledger_digest"] = content_address.content_digest(
            VPS._CASE_PLAN_DOMAIN, payload)
        self._write_json("case_plan.json", self.plan)
        receipt = self._evaluate()
        self.assertEqual(receipt["status"], "MISS")
        self.assertIn("spec_taskdoc_anchor", {
            item["name"] for item in receipt["checks"]
            if item["status"] == "MISS"})

    def test_malformed_nested_spec_is_blocked_not_crash(self):
        self.spec["golden"] = []
        self._write_json("spec.json", self.spec)
        receipt = self._evaluate()
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("spec.golden", " ".join(
            item["reason"] for item in receipt["checks"]))

    def test_malformed_plan_binding_is_blocked_not_crash(self):
        self.plan["spec_binding"] = []
        payload = dict(self.plan)
        payload.pop("ledger_digest")
        self.plan["ledger_digest"] = content_address.content_digest(
            VPS._CASE_PLAN_DOMAIN, payload)
        self._write_json("case_plan.json", self.plan)
        receipt = self._evaluate()
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("spec_binding", " ".join(
            item["reason"] for item in receipt["checks"]))

    def test_incomplete_plan_payload_is_blocked(self):
        del self.plan["coverage"]
        payload = dict(self.plan)
        payload.pop("ledger_digest")
        self.plan["ledger_digest"] = content_address.content_digest(
            VPS._CASE_PLAN_DOMAIN, payload)
        self._write_json("case_plan.json", self.plan)
        receipt = self._evaluate()
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("coverage", " ".join(
            item["reason"] for item in receipt["checks"]))

    def test_incomplete_source_payload_is_blocked(self):
        artifact = json.load(open(
            os.path.join(self.root, "source_facts.json"), encoding="utf-8"))
        del artifact["payload"]["pr"]
        content_address.write_artifact(
            self.root, "source_facts.json", VPS._SOURCE_DOMAIN,
            artifact["payload"])
        receipt = self._evaluate()
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("pr", " ".join(
            item["reason"] for item in receipt["checks"]))


if __name__ == "__main__":
    unittest.main()
