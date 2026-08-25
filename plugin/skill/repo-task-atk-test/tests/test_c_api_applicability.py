"""c_api 接口适用性门禁的命令行回归测试。"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "check_c_api_applicability.py"


class CApiApplicabilityTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp = Path(self.temp_dir.name)
        self.project = self.temp / "ops-blas"
        self.op_dir = self.project / "experimental" / "aclblasSaxpy"
        self.arch_dir = self.op_dir / "arch22"
        self.arch_dir.mkdir(parents=True)
        self.header = self.project / "include" / "aclblas.h"
        self.header.parent.mkdir()
        self.header.write_text(
            'extern "C" int aclblasSaxpy(fooHandle_t handle, int n, '
            'const float* alpha, float* x, float* y);\n',
            encoding="utf-8",
        )
        self.impl = self.arch_dir / "aclblas_saxpy.cpp"
        self.impl.write_text(
            "int aclblasSaxpy(fooHandle_t handle, int n, const float* alpha, "
            "float* x, float* y) { return 0; }\n",
            encoding="utf-8",
        )
        self.op_host = self.op_dir / "op_host"
        self.op_kernel = self.op_dir / "op_kernel"
        self.op_host.mkdir()
        self.op_kernel.mkdir()
        self.host_impl = self.op_host / "aclblas_saxpy.cpp"
        self.host_impl.write_text(
            "int aclblasSaxpy(fooHandle_t handle) { return 0; }\n",
            encoding="utf-8",
        )
        self.build_cmake = self.op_dir / "test" / "CMakeLists.txt"
        self.build_cmake.parent.mkdir()
        self.build_cmake.write_text(
            'target_compile_options(saxpy PRIVATE '
            '"$<$<COMPILE_LANGUAGE:ASC>:--npu-arch=dav-2201>")\n',
            encoding="utf-8",
        )
        self.env = self.temp / "env.json"
        self.env.write_text(json.dumps({
            "operator_project": {"path": str(self.project)},
            "devices": {"selected_name": "Ascend910B1"},
        }), encoding="utf-8")
        self.output = self.temp / "applicability.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_gate(self, *extra, header=None, arch_dir="arch22"):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--env", str(self.env),
             "--candidate", "aclblasSaxpy", "--header",
             str(header or self.header), "--op-dir", str(self.op_dir),
             "--layout", "arch_dirs",
             "--arch-dir", arch_dir, "--arch-dir-basis",
             "ops-blas 根 CMakeLists 将本机型号映射到该目录",
             "-o", str(self.output), *extra],
            capture_output=True, text=True, timeout=30,
        )

    def run_experimental(self, *extra):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--env", str(self.env),
             "--candidate", "aclblasSaxpy", "--header", str(self.header),
             "--op-dir", str(self.op_dir), "--layout", "experimental",
             "--build-cmake", str(self.build_cmake),
             "-o", str(self.output), *extra],
            capture_output=True, text=True, timeout=30,
        )

    def report(self):
        return json.loads(self.output.read_text(encoding="utf-8"))

    def test_green_path_records_declaration_probe_and_arch_file(self):
        done = self.run_gate()
        self.assertEqual(0, done.returncode, done.stderr + done.stdout)
        report = self.report()
        self.assertEqual("arch_dirs", report["layout"])
        self.assertTrue(report["checks"]["declaration"]["passed"])
        self.assertTrue(report["checks"]["representability"]["passed"])
        self.assertIn("存在性启发式", report["note"])
        self.assertIn("S3 导出函数名门禁", report["note"])
        self.assertTrue(report["checks"]["arch_implementation"]["passed"])
        self.assertTrue(report["declaration"]["extern_c"])
        self.assertFalse(report["declaration"]["mangled"])
        self.assertEqual(str(self.impl.resolve()), report["matched_file"])
        alpha = next(item for item in report["parameter_probe"]
                     if item["name"] == "alpha")
        self.assertTrue(alpha["requires_explicit_decision"])

    def test_missing_declaration_exits_two(self):
        self.header.write_text("int anotherApi(int x);\n", encoding="utf-8")
        done = self.run_gate()
        self.assertEqual(2, done.returncode)
        self.assertIn("aclblasSaxpy", "\n".join(self.report()["failures"]))

    def test_descriptor_parameter_names_the_closed_tier_failure(self):
        self.header.write_text(
            'extern "C" int aclblasSaxpy(fooHandle_t handle, '
            'fooDescriptor_t descriptor, float* out);\n', encoding="utf-8")
        done = self.run_gate()
        self.assertEqual(2, done.returncode)
        message = "\n".join(self.report()["failures"])
        self.assertIn("descriptor", message)
        self.assertIn("v1", message)

    def test_missing_arch_dir_is_a_structural_block(self):
        done = self.run_gate(arch_dir="arch35")
        self.assertEqual(3, done.returncode)
        self.assertIn("arch35", "\n".join(self.report()["failures"]))

    def test_comment_only_match_does_not_count_as_implementation(self):
        self.impl.write_text("// aclblasSaxpy(handle);\n", encoding="utf-8")
        done = self.run_gate()
        self.assertEqual(3, done.returncode)

    def test_unverified_soc_arch_pair_requires_acknowledgement(self):
        arch35 = self.op_dir / "arch35"
        arch35.mkdir()
        (arch35 / "saxpy.cpp").write_text(
            "int aclblasSaxpy() { return 0; }\n", encoding="utf-8")
        done = self.run_gate(arch_dir="arch35")
        self.assertEqual(2, done.returncode)
        self.assertIn("无法判定，需用户确认", "\n".join(self.report()["pending"]))

        allowed = self.run_gate("--allow-pending", arch_dir="arch35")
        self.assertEqual(0, allowed.returncode, allowed.stderr + allowed.stdout)
        self.assertTrue(self.report()["pending"])

    def test_unknown_soc_is_pending_instead_of_an_input_failure(self):
        payload = json.loads(self.env.read_text(encoding="utf-8"))
        payload["devices"]["selected_name"] = "FutureDevice42"
        self.env.write_text(json.dumps(payload), encoding="utf-8")
        done = self.run_gate()
        self.assertEqual(2, done.returncode)
        self.assertIn("无法判定，需用户确认", "\n".join(self.report()["pending"]))
        self.assertEqual([], self.report()["failures"])

    def test_header_outside_project_is_rejected(self):
        stray = self.temp / "other" / "aclblas.h"
        stray.parent.mkdir()
        stray.write_text(self.header.read_text(encoding="utf-8"), encoding="utf-8")
        done = self.run_gate(header=stray)
        self.assertEqual(2, done.returncode)
        self.assertIn("工程目录", "\n".join(self.report()["failures"]))

    def test_blank_arch_basis_is_rejected(self):
        done = subprocess.run(
            [sys.executable, str(SCRIPT), "--env", str(self.env),
             "--candidate", "aclblasSaxpy", "--header", str(self.header),
             "--op-dir", str(self.op_dir), "--layout", "arch_dirs",
             "--arch-dir", "arch22",
             "--arch-dir-basis", "   ", "-o", str(self.output)],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(2, done.returncode)
        self.assertIn("arch-dir-basis", "\n".join(self.report()["failures"]))

    def test_experimental_layout_records_verified_npu_arch(self):
        done = self.run_experimental()
        self.assertEqual(0, done.returncode, done.stderr + done.stdout)
        report = self.report()
        self.assertEqual("experimental", report["layout"])
        self.assertEqual("dav-2201", report["npu_arch"])
        self.assertEqual(str(self.host_impl.resolve()), report["matched_file"])
        self.assertTrue(report["checks"]["arch_implementation"]["passed"])

    def test_experimental_layout_requires_both_implementation_dirs(self):
        self.op_kernel.rmdir()
        done = self.run_experimental()
        self.assertEqual(3, done.returncode)
        report = self.report()
        self.assertFalse(report["checks"]["arch_implementation"]["passed"])
        self.assertIn("op_kernel", "\n".join(report["failures"]))

    def test_experimental_layout_requires_npu_arch_in_named_cmake(self):
        self.build_cmake.write_text("add_library(saxpy SHARED x.cpp)\n",
                                    encoding="utf-8")
        done = self.run_experimental()
        self.assertEqual(2, done.returncode)
        failures = "\n".join(self.report()["failures"])
        self.assertIn("npu-arch", failures)
        self.assertIn(str(self.build_cmake.resolve()), failures)

    def test_experimental_layout_rejects_conflicting_npu_arch_values(self):
        self.build_cmake.write_text(
            "--npu-arch=dav-2201\n--npu-arch=dav-9999\n",
            encoding="utf-8",
        )
        done = self.run_experimental()
        self.assertEqual(2, done.returncode)
        failures = "\n".join(self.report()["failures"])
        self.assertIn("互相冲突", failures)
        self.assertIn(str(self.build_cmake.resolve()), failures)

    def test_unverified_soc_npu_arch_pair_requires_acknowledgement(self):
        self.build_cmake.write_text("--npu-arch=dav-9999\n", encoding="utf-8")
        done = self.run_experimental()
        self.assertEqual(2, done.returncode)
        self.assertIn("无法判定，需用户确认", "\n".join(self.report()["pending"]))

        allowed = self.run_experimental("--allow-pending")
        self.assertEqual(0, allowed.returncode, allowed.stderr + allowed.stdout)
        self.assertTrue(self.report()["pending"])

    def test_experimental_layout_rejects_arch_dir(self):
        done = self.run_experimental("--arch-dir", "arch22")
        self.assertEqual(2, done.returncode)
        self.assertIn("--arch-dir", "\n".join(self.report()["failures"]))
        self.assertIn("按 SKILL.md 的量具规则停止本轮并记录", done.stdout)


if __name__ == "__main__":
    unittest.main()
