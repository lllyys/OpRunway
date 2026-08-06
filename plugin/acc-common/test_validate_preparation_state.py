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
        self.snapshot_sha = hashlib.sha256(b"task document\n").hexdigest()
        self.spec = {
            "op": "FixtureOp",
            "params": [],
            "golden": {
                "authorization": {"kind": "oracle_method"},
                "taskdoc_snapshot": {"sha256": self.snapshot_sha},
            },
        }
        self.spec_sha = hashlib.sha256(
            content_address.canonical_json_bytes(self.spec)).hexdigest()
        with open(os.path.join(self.root, "spec.json"), "w", encoding="utf-8") as out:
            json.dump(self.spec, out)
        self.golden = os.path.join(self.root, "golden.py")
        self.golden_contract = {
            "source": "single_api",
            "method_kind": "numpy_cpu",
            "authorization": {
                "kind": "oracle_method",
                "cite": "task_doc.snapshot.md:1",
                "quote": "task document",
            },
            "taskdoc_snapshot": {"sha256": self.snapshot_sha},
        }
        golden_bytes = (
            "GOLDEN_CONTRACT = "
            + repr(self.golden_contract)
            + "\ndef golden_fn(x): return x\n"
        ).encode()
        with open(self.golden, "wb") as out:
            out.write(golden_bytes)
        self.golden_sha = hashlib.sha256(golden_bytes).hexdigest()
        self.golden_contract_sha = hashlib.sha256(
            content_address.canonical_json_bytes(
                self.golden_contract)).hexdigest()
        os.makedirs(os.path.join(self.root, "work"))
        self.source_snapshot = os.path.join(
            self.root, "work", "task_doc.snapshot.md")
        with open(self.source_snapshot, "wb") as out:
            out.write(b"task document\n")
        self.snapshot = os.path.join(self.root, "task_doc.snapshot.md")
        with open(self.snapshot, "wb") as out:
            out.write(b"task document\n")
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
            self.root, "work/source_facts.json", VPS._SOURCE_DOMAIN, source)
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
                "numpy_stream_pin": VPS._current_numpy_stream_pin(),
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
                "contract_sha256": self.golden_contract_sha,
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
            self.root, "spec.json", "case_plan.json", golden_path=self.golden,
            source_rel="work/source_facts.json")

    def test_reusable_when_all_bindings_match(self):
        receipt = self._evaluate()
        self.assertEqual(receipt["status"], "REUSABLE")
        self.assertTrue(receipt["reusable"])
        self.assertIsNone(receipt["acceptance_verdict"])
        self.assertEqual(
            receipt["bindings"]["source_taskdoc_snapshot_sha256"],
            self.snapshot_sha)
        self.assertEqual(
            receipt["bindings"]["ops_taskdoc_snapshot_sha256"],
            self.snapshot_sha)
        self.assertEqual(
            receipt["bindings"]["golden_contract_taskdoc_snapshot_sha256"],
            self.snapshot_sha)

    def test_source_exists_but_effective_ops_snapshot_missing_is_miss(self):
        os.remove(self.snapshot)
        receipt = self._evaluate()
        self.assertEqual(receipt["status"], "MISS")
        self.assertIn("ops_taskdoc_snapshot", {
            item["name"] for item in receipt["checks"]
            if item["status"] == "MISS"})

    def test_effective_ops_snapshot_sha_conflict_is_blocked(self):
        with open(self.snapshot, "wb") as out:
            out.write(b"different task document\n")
        receipt = self._evaluate()
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("四方摘要", " ".join(
            item["reason"] for item in receipt["checks"]))

    def test_effective_ops_snapshot_symlink_is_blocked(self):
        outside = os.path.join(self.root, "outside.snapshot.md")
        with open(outside, "wb") as out:
            out.write(b"task document\n")
        os.remove(self.snapshot)
        os.symlink(outside, self.snapshot)
        receipt = self._evaluate()
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("符号链接", " ".join(
            item["reason"] for item in receipt["checks"]))

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
            self.root, "work/source_facts.json", VPS._SOURCE_DOMAIN, source)
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

    def _rewrite_plan(self):
        """改完 plan 载重后重算 ledger_digest 并落盘（不然会先被篡改门拦下）。"""
        payload = dict(self.plan)
        payload.pop("ledger_digest", None)
        self.plan["ledger_digest"] = content_address.content_digest(
            VPS._CASE_PLAN_DOMAIN, payload)
        self._write_json("case_plan.json", self.plan)

    def _stream_check(self, receipt):
        return next(item for item in receipt["checks"]
                    if item["name"] == "case_data_stream")

    def test_numpy_stream_drift_is_miss(self):
        """B-1：gen_cases 一个字节没改，但 numpy 换了大版本 → 同一 case_id 会产不同 .npy。
        逻辑摘要那几项全 PASS，所以必须由独立一项把它逮住。"""
        self.plan["planner_binding"]["numpy_stream_pin"] = "0.1"
        self._rewrite_plan()
        receipt = self._evaluate()
        self.assertEqual(receipt["status"], "MISS")
        check = self._stream_check(receipt)
        self.assertEqual(check["status"], "MISS")
        self.assertIn("0.1", check["reason"])
        # 反证：这一轮不能是被别的检查项顺带拦住的——规划逻辑侧仍旧全绿。
        self.assertEqual(
            "PASS",
            next(item["status"] for item in receipt["checks"]
                 if item["name"] == "case_planner"))

    def test_missing_numpy_stream_pin_is_miss_not_reusable(self):
        """老账本没记随机流身份 → 无从证明数据可复现，不许当 REUSABLE 放行。"""
        self.plan["planner_binding"].pop("numpy_stream_pin")
        self._rewrite_plan()
        receipt = self._evaluate()
        self.assertEqual(receipt["status"], "MISS")
        check = self._stream_check(receipt)
        self.assertEqual(check["status"], "MISS")
        # ⚠ 「没记」与「记了但不符」的处置不同（前者重做取材，后者对齐 numpy 版本），
        # 所以 reason 必须分得开——只断言 MISS 的话，把缺键悄悄并进「不符」分支也测不出来。
        self.assertIn("未记录", check["reason"])

    def test_blank_numpy_stream_pin_is_blocked_not_miss(self):
        """键**在**、值却非法 → 账本损坏 → BLOCKED，不是 MISS。

        MISS 的语义是「重跑一次准备就好」（老工件正常过期）；
        而键在值坏说明这份账本被改过或写坏了，重跑救不了「账本不可信」。
        两者混成一个状态，就会让「被改过的账本」看起来只是「有点旧」。
        """
        """空串不是「记了」：否则一份被裁剪的账本能靠空串跟另一份空串对上。"""
        self.plan["planner_binding"]["numpy_stream_pin"] = ""
        self._rewrite_plan()
        receipt = self._evaluate()
        self.assertEqual(receipt["status"], "BLOCKED")
        check = self._stream_check(receipt)
        self.assertEqual(check["status"], "BLOCKED")
        self.assertIn("形态非法", check["reason"])

    def test_unknown_current_numpy_stream_is_blocked_not_crash(self):
        """核不了 ≠ 核过了。当前流身份取不到时判 BLOCKED，且不许抛出去。"""
        with mock.patch.object(VPS, "_current_numpy_stream_pin",
                               side_effect=ValueError("版本号解析不出两段")):
            receipt = self._evaluate()
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertEqual(self._stream_check(receipt)["status"], "BLOCKED")
        self.assertNotIn("numpy_stream_pin", receipt["bindings"])

    def test_reusable_receipt_records_numpy_stream_pin(self):
        receipt = self._evaluate()
        self.assertEqual(receipt["status"], "REUSABLE")
        self.assertRegex(receipt["bindings"]["numpy_stream_pin"], r"^\d+\.\d+\.")

    def test_tampered_source_is_blocked_not_cache_miss(self):
        path = os.path.join(self.root, "work", "source_facts.json")
        artifact = json.load(open(path, encoding="utf-8"))
        artifact["payload"]["tampered"] = True
        self._write_json("work/source_facts.json", artifact)
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
            os.path.join(self.root, "work", "source_facts.json"), encoding="utf-8"))
        artifact["payload"]["producer"]["logic_sha256"] = "0" * 64
        content_address.write_artifact(
            self.root, "work/source_facts.json", VPS._SOURCE_DOMAIN,
            artifact["payload"])
        receipt = self._evaluate()
        self.assertEqual(receipt["status"], "MISS")
        self.assertIn("source_producer", {
            item["name"] for item in receipt["checks"]
            if item["status"] == "MISS"})

    def test_taskdoc_snapshot_drift_is_miss(self):
        with open(self.source_snapshot, "ab") as out:
            out.write(b"changed\n")
        receipt = self._evaluate()
        self.assertEqual(receipt["status"], "MISS")
        self.assertIn("taskdoc_snapshot", {
            item["name"] for item in receipt["checks"]
            if item["status"] == "MISS"})

    def test_snapshot_default_follows_source_directory(self):
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
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("spec_taskdoc_anchor", {
            item["name"] for item in receipt["checks"]
            if item["status"] == "BLOCKED"})

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
            os.path.join(self.root, "work", "source_facts.json"), encoding="utf-8"))
        del artifact["payload"]["pr"]
        content_address.write_artifact(
            self.root, "work/source_facts.json", VPS._SOURCE_DOMAIN,
            artifact["payload"])
        receipt = self._evaluate()
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("pr", " ".join(
            item["reason"] for item in receipt["checks"]))


class NumpyStreamPinBranchTest(unittest.TestCase):
    """⭐ 随机流 pin 的五条判定分支各自判对——这是复用门上新加的一条判定链。

    背景：`_case_rng` 把 `SEED ^ hash(case_id)` 喂给 `np.random.default_rng`，
    所以「同一 case_id 产同一字节」只在同一条 numpy 随机流下成立。
    pin 就是用来当场逮住流漂的。

    ⚠ 三种「对不上」语义不同，判定必须分开（一锅炖成 MISS 是错的）：
      · 取不到当前流身份       → BLOCKED（无从核对，重做准备也一样取不到）
      · 账本**没有**这个键     → MISS  （老工件正常过期，重跑一次即可）
      · 账本**有**但形态不合法 → BLOCKED（账本损坏/被改过，重跑救不了）
    """

    def test_wellformed_pin_accepts_real_versions(self):
        for pin in ("1.26.4", "2.0.0", "1.18.3", "1.26.4.post1", "2.1.0rc1"):
            with self.subTest(pin=pin):
                self.assertTrue(VPS._is_wellformed_pin(pin))

    def test_malformed_pin_is_rejected(self):
        for pin in ("garbage", "1", "", "x.y.z", "..", "1.x"):
            with self.subTest(pin=pin):
                self.assertFalse(VPS._is_wellformed_pin(pin))

    def test_pin_is_exact_not_major_minor(self):
        """⭐ pin 必须是**完整版本**。

        初版按「主.次」收敛，被反例推翻：numpy **1.18.4** 在补丁版里改了
        `Generator.integers(high=2**32)` 的取值，相对 1.18.3 输出就变了，
        而两者的「主.次」pin 都是 `1.18`——那个粒度探测不到已经真实发生过的流变更。
        """
        import gen_cases
        self.assertEqual(gen_cases.numpy_stream_pin("1.18.3"), "1.18.3")
        self.assertEqual(gen_cases.numpy_stream_pin("1.18.4"), "1.18.4")
        self.assertNotEqual(gen_cases.numpy_stream_pin("1.18.3"),
                            gen_cases.numpy_stream_pin("1.18.4"))
        self.assertEqual(gen_cases._NUMPY_STREAM_PIN_GRANULARITY, "exact")

    def test_unparseable_version_fails_closed(self):
        import gen_cases
        for bad in ("garbage", "1", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    gen_cases.numpy_stream_pin(bad)


if __name__ == "__main__":
    unittest.main()
