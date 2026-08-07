#!/usr/bin/env python3
"""N5 · 正式 caseset/golden 由 workflow 生成，附带自测只作 reference。

这里钉的是**权威选择**，不是某个算子：

* 显式 `reference_case_material_role=reference_only` 只能与显式
  `case_source=generated` 组合；
* spec 不得绑 taskdoc caseset，运行时也不得把它喂进 planner；
* 修改一份不进 spec/planner 的附带 JSON，正式 caseset 及全部输入/golden
  字节必须不变。这条是「参考材料不决定 case_target/case/golden」的反例门。

跑：`cd plugin/acc-common && python3 -m unittest test_generated_case_authority -v`
"""
import copy
import hashlib
import json
import os
import tempfile
import unittest

import _golden_fixture as GF
import _spec_fixture as SF
import content_address
import gen_cases as GC


_HERE = os.path.dirname(os.path.abspath(__file__))
_SIGN_SPEC = os.path.join(_HERE, "..", "samples", "specs", "sign.spec.json")


def setUpModule():
    GF.install()


def tearDownModule():
    GF.uninstall()


def _spec():
    spec = SF.load(_SIGN_SPEC, case_target=12)
    spec["precision"]["case_source"] = "generated"
    spec["precision"]["reference_case_material_role"] = "reference_only"
    return spec


def _tree_digest(root):
    """测试产物整树摘要：相对路径 + 字节，排序后确定性汇总。"""
    h = hashlib.sha256()
    for base, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(base, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            with open(path, "rb") as fh:
                raw = fh.read()
            h.update(len(rel.encode("utf-8")).to_bytes(8, "big"))
            h.update(rel.encode("utf-8"))
            h.update(len(raw).to_bytes(8, "big"))
            h.update(raw)
    return h.hexdigest()


class ReferenceCaseMaterialPolicyTest(unittest.TestCase):
    def test_explicit_generated_reference_only_is_accepted_and_ledgered(self):
        spec = _spec()
        self.assertEqual(GC._case_source(spec), "generated")
        self.assertEqual(GC._reference_case_material_role(spec), "reference_only")
        source, payload, digest = GC._resolve_taskdoc_inputs(spec, None)
        self.assertEqual((source, payload, digest), ("generated", None, None))
        ledger = GC._build_dry_run_ledger(copy.deepcopy(spec))
        self.assertEqual(ledger["planning"]["case_source"], "generated")
        self.assertEqual(
            ledger["planning"]["reference_case_material_role"], "reference_only")

    def test_role_requires_explicit_generated_not_legacy_default(self):
        spec = _spec()
        spec["precision"].pop("case_source")
        with self.assertRaisesRegex(ValueError, "显式.*case_source='generated'"):
            GC._case_source(spec)

    def test_role_conflicts_with_taskdoc_source(self):
        spec = _spec()
        spec["precision"]["case_source"] = "taskdoc"
        with self.assertRaisesRegex(ValueError, "冲突"):
            GC._case_source(spec)

    def test_role_vocabulary_fails_closed_for_null_bool_and_unknown(self):
        for bad in (None, False, "consume_if_present"):
            with self.subTest(bad=bad):
                spec = _spec()
                spec["precision"]["reference_case_material_role"] = bad
                with self.assertRaisesRegex(ValueError, "受控词表"):
                    GC._case_source(spec)

    def test_reference_only_forbids_even_null_taskdoc_binding(self):
        spec = _spec()
        spec["precision"]["taskdoc_caseset"] = {"sha256": None}
        with self.assertRaisesRegex(ValueError, "禁止声明.*taskdoc_caseset"):
            GC._case_source(spec)

    def test_runtime_taskdoc_payload_is_rejected_before_file_loading(self):
        spec = _spec()
        missing = "/definitely/not/read/taskdoc_caseset.json"
        with self.assertRaisesRegex(ValueError, "不得进 planner"):
            GC._resolve_taskdoc_inputs(spec, missing)

    def test_changing_reference_json_does_not_change_formal_cases_or_golden_bytes(self):
        """附带 JSON 存在且真改字节，但没有任何参数把它喂给 planner。

        两次在同一 work_dir 物化，比 caseset canonical bytes + 全部 `.npy`
        整树字节摘要；任一变化都说明 reference 材料泄进了正式用例链。
        """
        spec = _spec()
        with tempfile.TemporaryDirectory(prefix="oprunway_n5_reference_") as root:
            attachment = os.path.join(root, "source_selftest_cases.json")
            work = os.path.join(root, "formal-work")
            with open(attachment, "w", encoding="utf-8") as fh:
                json.dump({"cases": [{"shape": [1], "dtype": "float16"}]}, fh)
            first = GC.gen_cases(copy.deepcopy(spec), work)
            first_json = content_address.canonical_json_bytes(first)
            first_tree = _tree_digest(work)

            with open(attachment, "w", encoding="utf-8") as fh:
                json.dump({"cases": [{"shape": [65535], "dtype": "int64"}],
                           "case_target": 999999}, fh)
            second = GC.gen_cases(copy.deepcopy(spec), work)
            self.assertEqual(content_address.canonical_json_bytes(second), first_json)
            self.assertEqual(_tree_digest(work), first_tree)
            self.assertEqual(second["case_source"], "generated")
            self.assertEqual(second["reference_case_material_role"], "reference_only")
            self.assertEqual(second["requested_target"], spec["precision"]["case_target"])


if __name__ == "__main__":
    unittest.main()
