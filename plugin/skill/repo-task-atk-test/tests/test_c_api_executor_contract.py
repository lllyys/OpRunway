"""c_api 执行器样例跨技能数据边界的文本契约。"""

import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
EXECUTOR = SKILL_ROOT / "assets" / "example" / "c_api_executor.py"
LOAD_LINE = (
    "[c_api_executor] loaded_library=<absolute realpath> "
    "exported_name=<resolved name>"
)


class CApiExecutorContractTest(unittest.TestCase):
    def setUp(self):
        self.text = EXECUTOR.read_text(encoding="utf-8")

    def test_sample_pins_the_runtime_load_contract(self):
        self.assertIn(LOAD_LINE, self.text)
        self.assertIn('@register("example_c_api")', self.text)

    def test_sample_does_not_import_generation_side_parser(self):
        self.assertNotIn("import _c_api_signature", self.text)
        self.assertNotIn("from _c_api_signature", self.text)

    def test_sample_names_both_real_machine_reports(self):
        self.assertIn("aclblas-spike-report.md", self.text)
        self.assertIn("aclblas-formal-chain-report.md", self.text)

    def test_sample_marks_the_npu_purity_boundary(self):
        self.assertIn("# NPU 分支禁止 torch 计算算子", self.text)

    def test_sample_names_the_mangled_name_handoff_variable(self):
        self.assertIn("ATK_C_API_EXPORTED_NAME", self.text)


if __name__ == "__main__":
    unittest.main()
