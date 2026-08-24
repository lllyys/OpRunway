import json
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook


PLUGIN_ROOT = Path(__file__).resolve().parent.parent
CASE_GEN_ROOT = PLUGIN_ROOT / "skill" / "repo-task-case-gen"
ATK_ACCEPT_ROOT = PLUGIN_ROOT / "skill" / "repo-task-atk-accept"
CASE_GEN_SCRIPTS = CASE_GEN_ROOT / "scripts"
ATK_ACCEPT_SCRIPTS = ATK_ACCEPT_ROOT / "scripts"


class CliChainTest(unittest.TestCase):
    def run_script(self, scripts, name, *args):
        command = [sys.executable, str(scripts / name), *map(str, args)]
        return subprocess.run(command, check=True, capture_output=True, text=True)

    def test_minimal_accuracy_chain(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            yaml_file = root / "op.yaml"
            constraint = root / "op_constraint.py"
            function_plugin = root / "function_op.py"
            must_cover = root / "must_cover.json"
            cases = root / "cases.json"
            coverage = root / "coverage.json"
            interface = root / "interface.json"
            task_doc = root / "task_doc.md"
            opapi_library = root / "libcust_opapi.so"
            opapi_binding = root / "opapi_binding.json"
            smoke_log = root / "smoke.log"
            env_fingerprint = root / "env.json"
            vendor_root = root / "vendors" / "candidate"
            soc_binding = root / "soc_binding.json"
            report = root / "accuracy.xlsx"
            results = root / "results.json"
            verdict = root / "verdict.json"

            yaml_file.write_text(
                "name: torch.add\n"
                "aclnn_name: aclnnAdd\n"
                "dtype_numbers: 1\n"
                "extra_numbers: 0\n"
                "inputs:\n"
                "  - name: input\n"
                "    dtypes:\n"
                "      values: [fp32]\n",
                encoding="utf-8",
            )
            constraint.write_text(
                "class Generator:\n"
                "    def generate(self):\n"
                "        return []\n"
                "    def _get_case_numbers(self):\n"
                "        return 1\n",
                encoding="utf-8",
            )
            function_plugin.write_text("# execution adapter\n", encoding="utf-8")
            must_cover.write_text(
                json.dumps({
                    "axes": ["dtype"],
                    "dims": {"dtype": ["fp32"]},
                    "coverage_policy": {
                        "strategy": "anchored_interactions",
                        "baseline": {"dtype": "fp32"},
                        "interaction_groups": [],
                        "targeted": [],
                        "max_cases": 1,
                    },
                    "infeasible": [],
                    "extract": {"dtype": {"from": "input_dtype", "index": 0}},
                    "combos": [{"dtype": "fp32"}],
                }),
                encoding="utf-8",
            )
            cases.write_text(
                json.dumps({
                    "cases": [{
                        "id": "1",
                        "name": "torch.add",
                        "inputs": [{
                            "type": "tensor",
                            "dtype": "fp32",
                            "shape": [2],
                        }],
                        "standard": {"acc": "default"},
                    }]
                }),
                encoding="utf-8",
            )
            opapi_library.write_bytes(b"candidate op api")
            smoke_log.write_text(
                "import aclnnAddGetWorkspaceSize from "
                f"{opapi_library.resolve()} success!\n"
                "自动进行参数校验，头文件路径是：/tmp/vendor，"
                "算子一段式名称是：aclnnAddGetWorkspaceSize\n",
                encoding="utf-8",
            )
            opapi_binding.write_text(
                json.dumps({
                    "schema_version": 1,
                    "aclnn_name": "aclnnAdd",
                    "precheck": {
                        "library": str(opapi_library.resolve()),
                        "sha256": hashlib.sha256(opapi_library.read_bytes()).hexdigest(),
                        "required_symbols": [
                            "aclnnAddGetWorkspaceSize", "aclnnAdd",
                        ],
                        "environment": {
                            "ATK_CUSTOM_OPP_PATH": str(opapi_library.resolve()),
                        },
                    },
                    "runtime": {
                        "symbol": "aclnnAddGetWorkspaceSize",
                        "loaded_library": str(opapi_library.resolve()),
                        "runtime_log_verified": True,
                        "signature_check_verified": True,
                        "log": str(smoke_log.resolve()),
                        "log_sha256": hashlib.sha256(
                            smoke_log.read_bytes()).hexdigest(),
                    },
                    "failures": [],
                }),
                encoding="utf-8",
            )
            env_fingerprint.write_text(
                json.dumps({
                    "devices": {
                        "selected": 0,
                        "selected_name": "Ascend910B1",
                        "build_soc": "ascend910b",
                    },
                }),
                encoding="utf-8",
            )
            soc_config = (
                vendor_root
                / "op_impl/ai_core/tbe/kernel/config/ascend910b"
            )
            soc_kernel = (
                vendor_root
                / "op_impl/ai_core/tbe/kernel/ascend910b/add"
            )
            soc_config.mkdir(parents=True)
            soc_kernel.mkdir(parents=True)
            (soc_config / "binary_info_config.json").write_text(
                "{}", encoding="utf-8")
            (soc_kernel / "Add.o").write_bytes(b"kernel")
            (soc_kernel / "Add.json").write_text("{}", encoding="utf-8")

            self.run_script(
                CASE_GEN_SCRIPTS,
                "check_coverage.py",
                "-m", must_cover,
                "-j", cases,
                "-o", coverage,
            )
            self.run_script(
                ATK_ACCEPT_SCRIPTS,
                "check_soc_binding.py",
                "--env", env_fingerprint,
                "--vendor-root", vendor_root,
                "-o", soc_binding,
            )
            task_doc.write_text("add 算子开发任务书：逐元素加法。", encoding="utf-8")
            self.run_script(
                CASE_GEN_SCRIPTS,
                "derive_interface.py",
                "--mode", "aclnn",
                "--candidate", "aclnnAdd",
                "--baseline", "torch.add",
                "--mode-source", "测试任务书",
                "--task-doc", task_doc,
                "-o", interface,
            )

            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "statistic"
            sheet.append([
                "编号",
                "cpu_0_精度通过",
                "cpu_0_精度详情",
                "pyaclnn_0_精度通过",
            ])
            sheet.append(["1", "True", "", "-"])
            workbook.save(report)

            self.run_script(
                ATK_ACCEPT_SCRIPTS,
                "parse_atk_report.py",
                "-i", report,
                "-c", cases,
                "-o", results,
            )
            # 精度通过却没有性能产物时必须拒绝裁决：无对比基线也要采集绝对耗时。
            refused = subprocess.run(
                [sys.executable, str(ATK_ACCEPT_SCRIPTS / "verdict.py"),
                 "--interface", str(interface), "-c", str(coverage),
                 "-r", str(results), "--op", "add",
                 "--env", str(env_fingerprint), "-o", str(verdict)],
                capture_output=True, text=True)
            self.assertEqual(2, refused.returncode)
            self.assertIn("performance_device", refused.stderr)

            perf_results = root / "performance_results.json"
            perf_results.write_text(json.dumps({
                "task": "performance",
                "source": str(root / "performance.xlsx"),
                "cases": [{"id": "1", "device_us": {"pyaclnn_0": 21.0}}],
            }), encoding="utf-8")

            self.run_script(
                ATK_ACCEPT_SCRIPTS,
                "verdict.py",
                "--interface", interface,
                "-c", coverage,
                "-r", results,
                "--perf-results", perf_results,
                "--op", "add",
                "--env", env_fingerprint,
                "-o", verdict,
            )

            value = json.loads(verdict.read_text(encoding="utf-8"))
            self.assertEqual("通过", value["conclusion"])
            self.assertEqual(1.0, value["partitions"]["must"]["pass_rate"])
            self.assertEqual("pyaclnn", value["interface"]["execution_backend"])
            self.assertEqual("aclnnAdd", value["interface"]["candidate_symbol"])
            self.assertEqual(0, value["env"]["devices"]["selected"])
            # 性能状态是脚本推导出来的，不是报告作者写的
            self.assertEqual("未执行(无基线)", value["performance"]["status"])
            self.assertEqual(1, value["performance"]["measured_cases"])


if __name__ == "__main__":
    unittest.main()
