"""§3.1 三方软件检查：GPU 对标条件化后的适用性矩阵与防误豁免。

2026-09-17 cgeru 走查实证的判据缺陷修复（defect-map D14）：驱动/CUDA 版本
要求只在 §3.1/§3.3 出现真实 GPU 对标用途时生效；否定陈述不触发；torch 提及
要求保持原样。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
import _checks  # noqa: E402
import _taskdoc_parser as parser  # noqa: E402

SPINE = json.loads((SKILL_ROOT / "references" / "taskdoc-elements.json")
                   .read_text(encoding="utf-8"))
ELEMENT = SPINE["elements"]["3.1.third_party"]

TORCH_FULL = "torch 2.4.0、torch_npu 2.4.0"
TORCH_NA = "torch、torch_npu 不涉及（本任务不依赖 torch），golden 由 cblas 生成"
GPU_LINE = "性能对标 GPU A100 标杆"
GPU_FULL = GPU_LINE + "，CUDA 12.4、驱动 550.54"
NO_GPU = "本任务无 GPU 对标"


def _scaffold_text():
    workdir = Path(tempfile.mkdtemp())
    out = workdir / "EnvCheckCase_task_doc.md"
    subprocess.run([sys.executable,
                    str(SKILL_ROOT / "scripts" / "make_taskdoc.py"),
                    "--op", "EnvCheckCase", "--out", str(out)],
                   check=True, capture_output=True)
    return out.read_text(encoding="utf-8")


SCAFFOLD = None


def build_doc(sec31, sec33):
    global SCAFFOLD
    if SCAFFOLD is None:
        SCAFFOLD = _scaffold_text()
    text = SCAFFOLD.replace("### 3.1 软硬件环境要求",
                            f"### 3.1 软硬件环境要求\n\n{sec31}\n")
    text = text.replace("### 3.3 性能要求",
                        f"### 3.3 性能要求\n\n{sec33}\n")
    case = Path(tempfile.mkdtemp()) / "EnvCheckCase_task_doc.md"
    case.write_text(text, encoding="utf-8")
    return parser.parse(case)


def run_check(sec31, sec33):
    doc = build_doc(sec31, sec33)
    return _checks.check_env_lists_third_party_versions(
        doc, "3.1.third_party", ELEMENT, {})


class EnvCheckMatrixTest(unittest.TestCase):
    def assert_red(self, findings, fragment):
        self.assertTrue(findings, "预期判红却放行")
        self.assertIn(fragment, findings[0].message)

    # --- 适用性矩阵 ---

    def test_no_torch_no_gpu_red_for_torch(self):
        self.assert_red(run_check("CANN 9.1.0，无深度学习框架依赖", NO_GPU),
                        "torch")

    def test_torch_na_no_gpu_passes(self):
        self.assertEqual([], run_check(TORCH_NA, NO_GPU))

    def test_no_torch_with_gpu_red_for_torch(self):
        self.assert_red(run_check("CANN 9.1.0", GPU_FULL), "torch")

    def test_torch_gpu_missing_driver_red(self):
        self.assert_red(
            run_check(TORCH_FULL + "，" + GPU_LINE + "，CUDA 12.4", "性能达标"),
            "驱动/CUDA")

    def test_torch_gpu_missing_cuda_red(self):
        self.assert_red(
            run_check(TORCH_FULL + "，" + GPU_LINE + "，驱动 550.54", "性能达标"),
            "驱动/CUDA")

    def test_torch_gpu_missing_both_red(self):
        self.assert_red(run_check(TORCH_FULL + "，" + GPU_LINE, "性能达标"),
                        "驱动/CUDA")

    def test_torch_gpu_both_versions_pass(self):
        self.assertEqual([], run_check(TORCH_FULL + "，" + GPU_FULL, "性能达标"))

    def test_torch_no_gpu_passes_without_cuda(self):
        self.assertEqual([], run_check(TORCH_FULL, NO_GPU))

    # --- 防误豁免与漏放（适用性 = 声明契约，fail-closed） ---

    def test_unrelated_bushiji_without_torch_still_red(self):
        self.assert_red(run_check("内存要求不涉及，CANN 9.1.0", NO_GPU), "torch")

    def test_waiver_phrase_exempts(self):
        self.assertEqual([], run_check(TORCH_NA, "GPU 对标：不涉及"))

    def test_free_form_negation_does_not_exempt(self):
        self.assert_red(run_check(TORCH_FULL, "不使用 GPU 标杆"), "驱动/CUDA")

    def test_h100_token_triggers(self):
        self.assert_red(run_check(TORCH_FULL, "性能对标 H100 标杆"), "驱动/CUDA")

    def test_lowercase_gpu_token_triggers(self):
        self.assert_red(run_check(TORCH_FULL, "gpu 基线耗时见附表"), "驱动/CUDA")

    def test_comparison_sentence_triggers(self):
        self.assert_red(run_check(TORCH_FULL, "性能不低于GPU标杆的0.8倍"),
                        "驱动/CUDA")

    def test_gpu_baseline_in_33_triggers_requirement_in_31(self):
        self.assert_red(run_check(TORCH_FULL, GPU_LINE + "耗时 524us"),
                        "驱动/CUDA")

    def test_non_blas_task_with_gpu_baseline_unchanged(self):
        self.assertEqual(
            [], run_check(TORCH_FULL + "，" + GPU_FULL,
                          "910B3 不高于 A100 标杆的 1.25 倍"))


if __name__ == "__main__":
    unittest.main()
