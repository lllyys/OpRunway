"""c_api 动态库导出函数与运行时加载路径门禁测试。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "check_c_api_binding.py"


class CApiBindingTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp = Path(self.temp_dir.name)
        self.library = self.temp / "libaclblas_c_api.so"
        self.library.write_bytes(b"test-library")
        self.table = self.temp / "call_sequence.json"
        self.output = self.temp / "binding.json"
        self.nm = self.temp / "fake_nm.py"

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_table(self, *, mangled=False, exported_name="aclblasSaxpy"):
        self.table.write_text(json.dumps({
            "symbol": "aclblasSaxpy",
            "mangled": mangled,
            "exported_name": exported_name,
        }), encoding="utf-8")

    def write_nm(self, output, returncode=0):
        self.nm.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            f"sys.stdout.write({output!r})\n"
            f"raise SystemExit({returncode})\n",
            encoding="utf-8",
        )
        os.chmod(self.nm, 0o755)

    def run_gate(self, *extra):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--library", str(self.library.resolve()),
             "--call-sequence", str(self.table), "--nm-cmd", str(self.nm),
             "-o", str(self.output), *extra],
            capture_output=True, text=True, timeout=30,
        )

    def report(self):
        return json.loads(self.output.read_text(encoding="utf-8"))

    def test_exact_name_passes_and_records_hash(self):
        self.write_table()
        self.write_nm("0000000000001000 T aclblasSaxpy\n")
        done = self.run_gate()
        self.assertEqual(0, done.returncode, done.stderr + done.stdout)
        report = self.report()
        self.assertEqual("aclblasSaxpy", report["resolved_exported_name"])
        self.assertEqual(64, len(report["library_sha256"]))

    def test_mangled_name_uses_one_plain_name_substring(self):
        self.write_table(mangled=True, exported_name=None)
        mangled = "_Z14aclblasSaxpyP11fooHandle_ti"
        self.write_nm(f"0000000000001000 T {mangled}\n")
        done = self.run_gate()
        self.assertEqual(0, done.returncode, done.stderr + done.stdout)
        report = self.report()
        self.assertEqual(mangled, report["resolved_exported_name"])
        self.assertTrue(report["s2_exported_name_pending"])

    def test_two_mangled_matches_are_ambiguous(self):
        self.write_table(mangled=True, exported_name=None)
        self.write_nm(
            "0000000000001000 T _Z14aclblasSaxpyP1A\n"
            "0000000000002000 T _Z14aclblasSaxpyP1B\n")
        done = self.run_gate()
        self.assertEqual(2, done.returncode)
        message = "\n".join(self.report()["failures"])
        self.assertIn("_Z14aclblasSaxpyP1A", message)
        self.assertIn("_Z14aclblasSaxpyP1B", message)

    def test_unmangled_name_does_not_accept_a_longer_name(self):
        self.write_table()
        self.write_nm("0000000000001000 T aclblasSaxpyExtra\n")
        done = self.run_gate()
        self.assertEqual(2, done.returncode)
        self.assertIsNone(self.report()["resolved_exported_name"])

    def test_executor_log_contract_passes(self):
        self.write_table()
        self.write_nm("0000000000001000 T aclblasSaxpy\n")
        log = self.temp / "executor.log"
        log.write_text(
            f"[c_api_executor] loaded_library={self.library.resolve()} "
            "exported_name=aclblasSaxpy\n", encoding="utf-8")
        done = self.run_gate("--executor-log", str(log))
        self.assertEqual(0, done.returncode, done.stderr + done.stdout)
        self.assertTrue(self.report()["runtime_load"]["passed"])

    def test_executor_log_library_mismatch_exits_two(self):
        self.write_table()
        self.write_nm("0000000000001000 T aclblasSaxpy\n")
        log = self.temp / "executor.log"
        log.write_text(
            "[c_api_executor] loaded_library=/tmp/other.so "
            "exported_name=aclblasSaxpy\n", encoding="utf-8")
        done = self.run_gate("--executor-log", str(log))
        self.assertEqual(2, done.returncode)
        self.assertIn("/tmp/other.so", "\n".join(self.report()["failures"]))

    def test_executor_log_exported_name_mismatch_exits_two(self):
        self.write_table()
        self.write_nm("0000000000001000 T aclblasSaxpy\n")
        log = self.temp / "executor.log"
        log.write_text(
            f"[c_api_executor] loaded_library={self.library.resolve()} "
            "exported_name=wrongName\n", encoding="utf-8")
        done = self.run_gate("--executor-log", str(log))
        self.assertEqual(2, done.returncode)
        self.assertIn("wrongName", "\n".join(self.report()["failures"]))

    def test_executor_log_missing_contract_line_exits_two(self):
        self.write_table()
        self.write_nm("0000000000001000 T aclblasSaxpy\n")
        log = self.temp / "executor.log"
        log.write_text("executor started\n", encoding="utf-8")
        done = self.run_gate("--executor-log", str(log))
        self.assertEqual(2, done.returncode)
        self.assertIn("[c_api_executor]", "\n".join(self.report()["failures"]))

    def test_contract_prefix_constant_is_exact(self):
        sys.path.insert(0, str(SKILL_ROOT / "scripts"))
        import check_c_api_binding
        self.assertEqual(
            "[c_api_executor] loaded_library=",
            check_c_api_binding.EXECUTOR_LOAD_PREFIX,
        )


if __name__ == "__main__":
    unittest.main()
