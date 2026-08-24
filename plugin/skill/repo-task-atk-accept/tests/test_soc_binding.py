import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _paths import SCRIPTS


sys.path.insert(0, str(SCRIPTS))

from _soc_binding import (SocBindingError, infer_build_soc,  # noqa: E402
                          inspect_package, scan_build_log)


# 真机日志原样节选：构建族来自 env.json，算子名来自待验收算子工程。
UNSUPPORTED_LOG = """\
[2026-08-15 07:21:11] COMPUTE_UNIT: ascend910_93
[2026-08-15 07:21:18] -- [INFO] On [ascend910_93], [some_op_v2] not supported.
[2026-08-15 07:21:18] -- [WARNING] There is no operator support for ascend910_93.
[2026-08-15 07:21:20] -- Build binary success
"""


class SocBindingTest(unittest.TestCase):
    def test_device_name_maps_to_build_family(self):
        self.assertEqual("ascend910_93", infer_build_soc("Ascend910_9382"))
        self.assertEqual("ascend910b", infer_build_soc("Ascend910B1"))
        self.assertEqual("ascend310p", infer_build_soc("Ascend310P3"))

    def test_unknown_device_is_rejected(self):
        with self.assertRaisesRegex(SocBindingError, "无法映射"):
            infer_build_soc("FutureDevice42")

    def test_package_must_contain_selected_soc_kernel(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            vendor = Path(temp_dir) / "vendor"
            config = vendor / "op_impl/ai_core/tbe/kernel/config/ascend910_93"
            kernel = vendor / "op_impl/ai_core/tbe/kernel/ascend910_93/op"
            config.mkdir(parents=True)
            kernel.mkdir(parents=True)
            (config / "binary_info_config.json").write_text("{}", encoding="utf-8")
            (kernel / "Op.o").write_bytes(b"kernel")
            (kernel / "Op.json").write_text("{}", encoding="utf-8")

            report = inspect_package(vendor, "Ascend910_9382")

            self.assertEqual("ascend910_93", report["build_soc"])
            self.assertEqual(1, report["kernel_object_count"])

    def test_package_for_another_soc_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            vendor = Path(temp_dir) / "vendor"
            wrong = vendor / "op_impl/ai_core/tbe/kernel/config/ascend910b"
            wrong.mkdir(parents=True)
            (wrong / "binary_info_config.json").write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(SocBindingError, "ascend910_93"):
                inspect_package(vendor, "Ascend910_9382")

    def test_build_log_proves_the_operator_skips_this_soc(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log = Path(temp_dir) / "build.log"
            log.write_text(UNSUPPORTED_LOG, encoding="utf-8")
            hits = scan_build_log(log, "ascend910_93")
            self.assertEqual(2, len(hits))

    def test_another_soc_in_the_log_does_not_trip_the_gate(self):
        # 反例：日志里点名的是别的构建族，本机那一族没被拒，门禁不得生效。
        with tempfile.TemporaryDirectory() as temp_dir:
            log = Path(temp_dir) / "build.log"
            log.write_text(UNSUPPORTED_LOG, encoding="utf-8")
            self.assertEqual([], scan_build_log(log, "ascend910b"))

    def test_clean_build_log_passes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log = Path(temp_dir) / "build.log"
            log.write_text("[INFO] COMPUTE_UNIT: ascend910_93\nBuild binary success\n",
                           encoding="utf-8")
            self.assertEqual([], scan_build_log(log, "ascend910_93"))

    def _run_cli(self, *extra, log_text=UNSUPPORTED_LOG):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "env.json").write_text(json.dumps(
                {"devices": {"selected_name": "Ascend910_9382"}}), encoding="utf-8")
            (root / "build.log").write_text(log_text, encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(SCRIPTS / "check_soc_binding.py"),
                 "--env", str(root / "env.json"),
                 "--build-log", str(root / "build.log"),
                 "-o", str(root / "soc.json"), *extra],
                capture_output=True, text=True, check=False)
            report = json.loads((root / "soc.json").read_text(encoding="utf-8"))
            return proc, report

    def test_unsupported_soc_gets_its_own_exit_code(self):
        # 退出码 3 与「包不完整」的 2 分开：前者不可重试，后者可以修完再来。
        proc, report = self._run_cli()
        self.assertEqual(3, proc.returncode)
        self.assertIn("不得改用其他 SoC 重建", proc.stdout)
        self.assertEqual("ascend910_93", report["build_soc"])
        self.assertTrue(report["unsupported_hits"])

    def test_clean_build_log_exits_zero(self):
        proc, report = self._run_cli(log_text="Build binary success\n")
        self.assertEqual(0, proc.returncode)
        self.assertEqual([], report["unsupported_hits"])

    def test_generic_module_has_no_operator_names(self):
        source = (SCRIPTS / "_soc_binding.py").read_text(encoding="utf-8")
        for operator_term in ("median", "roll", "bernoulli"):
            self.assertNotIn(operator_term, source.lower())


if __name__ == "__main__":
    unittest.main()
