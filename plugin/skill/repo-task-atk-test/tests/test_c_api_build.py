"""c_api 共享库构建文件渲染器的命令行回归测试。"""

import hashlib
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "make_c_api_build.py"


class CApiBuildRendererTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp = Path(self.temp_dir.name)
        self.project = self.temp / "ops-blas"
        self.op_dir = self.project / "experimental" / "aclblasTrsmBatched"
        self.test_dir = self.op_dir / "test"
        self.test_dir.mkdir(parents=True)
        for relative in ("op_host/trsm.cpp", "op_kernel/trsm_kernel.cpp",
                         "test/main.cpp", "include/aclblas.h"):
            path = self.op_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("// fixture\n", encoding="utf-8")
        (self.project / "include").mkdir()
        self.cmake = self.test_dir / "CMakeLists.txt"
        self.cmake.write_text(self.fixture_cmake(), encoding="utf-8")
        self.env = self.temp / "env.json"
        self.env.write_text(json.dumps({
            "operator_project": {"path": str(self.project)},
        }), encoding="utf-8")
        self.outdir = self.temp / "evidence" / "c_api_build"

    def tearDown(self):
        self.temp_dir.cleanup()

    def fixture_cmake(self, arch_line=None):
        if arch_line is None:
            arch_line = (
                'target_compile_options(aclblas_demo PRIVATE '
                '"$<$<COMPILE_LANGUAGE:ASC>:--npu-arch=dav-2201>")')
        return textwrap.dedent(f"""
            set(OP_SOURCES
                ../op_host/trsm.cpp
                ../op_kernel/trsm_kernel.cpp
                main.cpp
            )
            add_executable(aclblas_demo ${{OP_SOURCES}})
            target_include_directories(aclblas_demo PRIVATE
                ../include
                ${{PROJECT_SOURCE_DIR}}/include
            )
            target_link_libraries(aclblas_demo PRIVATE
                tiling_api register platform unified_dlog dl m graph_base
            )
            {arch_line}
        """)

    def run_renderer(self):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--env", str(self.env),
             "--op-dir", str(self.op_dir), "--project-root", str(self.project),
             "--build-cmake", str(self.cmake), "-o", str(self.outdir)],
            capture_output=True, text=True, timeout=30,
        )

    def test_renders_verified_shared_library_shape(self):
        done = self.run_renderer()
        self.assertEqual(0, done.returncode, done.stderr + done.stdout)
        generated = self.outdir / "CMakeLists.txt"
        text = generated.read_text(encoding="utf-8")
        self.assertIn("cmake_minimum_required(VERSION 3.16)", text)
        self.assertIn("find_package(ASC REQUIRED)", text)
        self.assertIn("project(aclblastrsmbatched_so LANGUAGES ASC CXX)", text)
        self.assertIn("add_library(aclblastrsmbatched_c_api SHARED", text)
        self.assertIn("POSITION_INDEPENDENT_CODE ON", text)
        self.assertIn("PROPERTIES LANGUAGE ASC", text)
        self.assertIn("--npu-arch=dav-2201", text)
        for library in ("tiling_api", "register", "platform", "unified_dlog",
                        "dl", "m", "graph_base"):
            self.assertIn(library, text)

        host = str((self.op_dir / "op_host/trsm.cpp").resolve())
        kernel = str((self.op_dir / "op_kernel/trsm_kernel.cpp").resolve())
        self.assertIn(host, text)
        self.assertIn(kernel, text)
        self.assertNotIn(str((self.test_dir / "main.cpp").resolve()), text)

        report = json.loads(
            (self.outdir / "render_report.json").read_text(encoding="utf-8"))
        self.assertEqual([host, kernel], report["sources"])
        self.assertEqual("libaclblastrsmbatched_c_api.so", report["library_name"])
        expected_hash = hashlib.sha256(generated.read_bytes()).hexdigest()
        self.assertEqual(expected_hash, report["generated_file_sha256"])
        self.assertTrue(all(Path(path).is_absolute() for path in report["includes"]))

    def test_missing_arch_flag_exits_two_and_names_the_fact(self):
        self.cmake.write_text(self.fixture_cmake(arch_line=""), encoding="utf-8")
        done = self.run_renderer()
        self.assertEqual(2, done.returncode)
        self.assertIn("npu-arch", done.stderr + done.stdout)
        self.assertFalse((self.outdir / "CMakeLists.txt").exists())

    def test_commented_arch_flag_does_not_supply_the_build_fact(self):
        self.cmake.write_text(
            self.fixture_cmake(arch_line="# --npu-arch=dav-2201"),
            encoding="utf-8")
        done = self.run_renderer()
        self.assertEqual(2, done.returncode)
        self.assertIn("npu-arch", done.stderr + done.stdout)


if __name__ == "__main__":
    unittest.main()
