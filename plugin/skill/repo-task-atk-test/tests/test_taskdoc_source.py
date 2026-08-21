"""生成侧只从任务书读取签名、参数 dtype 与原始文件摘要。"""

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
DOC_WRITE_ROOT = SKILL_ROOT.parents[0] / "repo-task-doc-write"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from _taskdoc import (TaskDocError, dtype_inventory, load, parameter_names,  # noqa: E402
                      param_dtypes, sha256, signature_block,
                      tensor_dtype_text)

GOLDEN = DOC_WRITE_ROOT / "references" / "golden-task-doc.md"
NO_HEADINGS = DOC_WRITE_ROOT / "tests" / "fixtures" / "defects" / "no_headings.md"


class GoldenTaskDocTest(unittest.TestCase):
    def setUp(self):
        if not GOLDEN.is_file():
            self.skipTest("doc-write 黄金任务书不在当前发布切片")
        self.doc = load(GOLDEN)

    def test_signature_block_comes_from_section_2_3(self):
        self.assertIn(
            "aclnnSlidingTileAttentionGetWorkspaceSize",
            signature_block(self.doc))

    def test_parameter_names_keep_business_declaration_order(self):
        self.assertEqual(
            parameter_names(self.doc),
            ["q", "k", "v", "output", "windowSize", "windowSizeLen",
             "textLength", "hasText", "seqShape"])
        for boilerplate in ("workspace", "workspaceSize", "executor", "stream"):
            self.assertNotIn(boilerplate, parameter_names(self.doc))

    def test_parameter_dtypes_come_from_named_table_columns(self):
        found = param_dtypes(self.doc)
        self.assertEqual(found["q"], ["FLOAT16", "BFLOAT16"])
        self.assertEqual(found["windowSizeLen"], ["int"])
        self.assertNotIn("seqShape", found)

    def test_dtype_inventory_maps_aliases_and_keeps_unknown_tokens(self):
        canonical, unknown = dtype_inventory(self.doc)
        self.assertEqual(canonical, ["fp16", "bf16", "bool"])
        self.assertEqual(unknown, ["int"])

    def test_tensor_dtype_text_excludes_attribute_type_names(self):
        text = tensor_dtype_text(self.doc)
        self.assertIn("FLOAT16", text)
        self.assertIn("BFLOAT16", text)
        for attribute_type in ("bool", "int", "string"):
            self.assertNotIn(attribute_type, text.casefold())


class TaskDocErrorTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _load_text(self, text):
        path = Path(self._tmp.name) / "task.md"
        path.write_text(text, encoding="utf-8")
        return load(path)

    def test_load_reports_missing_numbered_sections(self):
        if not NO_HEADINGS.is_file():
            self.skipTest("doc-write 反向 fixture 不在当前发布切片")
        with self.assertRaises(TaskDocError) as caught:
            load(NO_HEADINGS)
        self.assertIn("章节", str(caught.exception))
        self.assertIn("repo-task-doc-write", str(caught.exception))

    def test_signature_block_reports_missing_section_2_3(self):
        doc = self._load_text("# 测试任务书\n\n### 2.4 参数说明\n\n正文\n")
        with self.assertRaises(TaskDocError) as caught:
            signature_block(doc)
        self.assertIn("§2.3", str(caught.exception))
        self.assertIn("repo-task-doc-write", str(caught.exception))

    def test_signature_block_requires_get_workspace_size(self):
        doc = self._load_text(
            "# 测试任务书\n\n### 2.3 接口定义\n\n"
            "```\naclnnStatus aclnnExample(void);\n```\n")
        with self.assertRaises(TaskDocError) as caught:
            signature_block(doc)
        self.assertIn("GetWorkspaceSize", str(caught.exception))
        self.assertIn("repo-task-doc-write", str(caught.exception))

    def test_parameter_names_reports_an_unparseable_declaration(self):
        doc = self._load_text(
            "# 测试任务书\n\n### 2.3 接口定义\n\n"
            "```\nGetWorkspaceSize\n```\n")
        with self.assertRaises(TaskDocError) as caught:
            parameter_names(doc)
        self.assertIn("§2.3", str(caught.exception))
        self.assertIn("repo-task-doc-write", str(caught.exception))

    def test_param_dtypes_reports_missing_section_2_4(self):
        doc = self._load_text(
            "# 测试任务书\n\n### 2.3 接口定义\n\n"
            "```\naclnnStatus aclnnExampleGetWorkspaceSize(void);\n```\n")
        with self.assertRaises(TaskDocError) as caught:
            param_dtypes(doc)
        self.assertIn("§2.4", str(caught.exception))
        self.assertIn("repo-task-doc-write", str(caught.exception))

    def test_param_dtypes_reports_a_missing_table(self):
        doc = self._load_text("# 测试任务书\n\n### 2.4 参数说明\n\n正文\n")
        with self.assertRaises(TaskDocError) as caught:
            param_dtypes(doc)
        self.assertIn("§2.4", str(caught.exception))
        self.assertIn("repo-task-doc-write", str(caught.exception))

    def test_param_dtypes_names_the_missing_dtype_column(self):
        doc = self._load_text(
            "# 测试任务书\n\n### 2.4 参数说明\n\n"
            "| 参数名 | 描述 |\n| --- | --- |\n| q | 输入 |\n")
        with self.assertRaises(TaskDocError) as caught:
            param_dtypes(doc)
        self.assertIn("dtype类型", str(caught.exception))

    def test_param_dtypes_names_the_missing_parameter_column(self):
        doc = self._load_text(
            "# 测试任务书\n\n### 2.4 参数说明\n\n"
            "| 描述 | dtype类型 |\n| --- | --- |\n| 输入 | FLOAT16 |\n")
        with self.assertRaises(TaskDocError) as caught:
            param_dtypes(doc)
        self.assertIn("参数名", str(caught.exception))

    def test_tensor_dtype_text_names_each_missing_column(self):
        columns = ("参数名", "数据类型", "dtype类型")
        for missing in columns:
            with self.subTest(column=missing):
                present = [column for column in columns if column != missing]
                doc = self._load_text(
                    "# 测试任务书\n\n### 2.4 参数说明\n\n"
                    f"| {' | '.join(present)} |\n"
                    f"| {' | '.join('---' for _ in present)} |\n"
                    f"| {' | '.join('q' for _ in present)} |\n")
                with self.assertRaises(TaskDocError) as caught:
                    tensor_dtype_text(doc)
                self.assertIn(missing, str(caught.exception))
                self.assertIn("repo-task-doc-write", str(caught.exception))

    def test_tensor_dtype_text_requires_at_least_one_tensor_parameter(self):
        doc = self._load_text(
            "# 测试任务书\n\n### 2.4 参数说明\n\n"
            "| 参数名 | 数据类型 | dtype类型 |\n"
            "| --- | --- | --- |\n"
            "| count | scalar | int |\n")
        with self.assertRaises(TaskDocError) as caught:
            tensor_dtype_text(doc)
        message = str(caught.exception)
        self.assertIn("没有张量参数", message)
        self.assertIn("所有类型", message)
        self.assertIn("repo-task-doc-write", message)


class TaskDocDtypeParsingTest(unittest.TestCase):
    def test_all_supported_separators_are_split_and_empty_values_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "task.md"
            path.write_text(
                "# 测试任务书\n\n### 2.4 参数说明\n\n"
                "| dtype类型 | 描述 | 参数名 |\n"
                "| --- | --- | --- |\n"
                "| FLOAT16、BFLOAT16 | 输入 | q |\n"
                "| INT8，UINT8 | 输入 | k |\n"
                "| INT16, UINT16 | 输入 | v |\n"
                "| FP32 / FP64 | 输入 | x |\n"
                "| BOOL ｜ bool | 属性 | flag |\n"
                "| - | 属性 | none |\n"
                "| | 属性 | blank |\n",
                encoding="utf-8")
            doc = load(path)

        self.assertEqual(param_dtypes(doc)["q"], ["FLOAT16", "BFLOAT16"])
        self.assertEqual(param_dtypes(doc)["k"], ["INT8", "UINT8"])
        self.assertEqual(param_dtypes(doc)["v"], ["INT16", "UINT16"])
        self.assertEqual(param_dtypes(doc)["x"], ["FP32", "FP64"])
        self.assertEqual(param_dtypes(doc)["flag"], ["BOOL", "bool"])
        self.assertNotIn("none", param_dtypes(doc))
        self.assertNotIn("blank", param_dtypes(doc))
        self.assertEqual(
            dtype_inventory(doc),
            (["fp16", "bf16", "int8", "uint8", "int16", "uint16",
              "fp32", "fp64", "bool"], []))


class TaskDocSha256Test(unittest.TestCase):
    def test_sha256_uses_the_original_file_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "task.md"
            original = b"\x00task-doc\n"
            path.write_bytes(original)
            first = sha256(path)
            self.assertEqual(first, hashlib.sha256(original).hexdigest())

            changed = original + b"!"
            path.write_bytes(changed)
            self.assertEqual(sha256(path), hashlib.sha256(changed).hexdigest())
            self.assertNotEqual(first, sha256(path))


if __name__ == "__main__":
    unittest.main()
