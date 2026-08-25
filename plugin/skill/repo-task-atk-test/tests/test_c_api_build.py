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

    def test_link_library_cmake_injection_is_rejected_verbatim(self):
        malicious = ")\nexecute_process(COMMAND ignored)\n("
        text = self.fixture_cmake().replace(
            "tiling_api register", f'tiling_api "{malicious}" register')
        self.cmake.write_text(text, encoding="utf-8")
        done = self.run_renderer()
        self.assertEqual(2, done.returncode)
        self.assertIn(malicious, done.stderr + done.stdout)
        self.assertFalse((self.outdir / "CMakeLists.txt").exists())


class CMakeSourceDirTest(unittest.TestCase):
    CMAKE = """
        set(TRSM_SOURCES
            ${CMAKE_CURRENT_SOURCE_DIR}/trsm_test.cpp
            ${CMAKE_SOURCE_DIR}/op_host/trsm_host.cpp
            ${CMAKE_SOURCE_DIR}/op_kernel/trsm_kernel.cpp)
        add_executable(trsm_test ${TRSM_SOURCES})
        target_include_directories(trsm_test PRIVATE
            ${CMAKE_SOURCE_DIR}/op_kernel
            ${CMAKE_SOURCE_DIR}/../../include)
        target_link_libraries(trsm_test PRIVATE tiling_api dl)
        target_compile_options(trsm_test PRIVATE
            "$<$<COMPILE_LANGUAGE:ASC>:--npu-arch=dav-2201>")
    """

    def _project(self, temp_dir, with_op_root_cmake):
        project = Path(temp_dir) / "ops-blas"
        op_dir = project / "experimental" / "aclblasTrsmBatched"
        for relative in ("op_host/trsm_host.cpp", "op_kernel/trsm_kernel.cpp",
                         "test/trsm_test.cpp"):
            path = op_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("// stub\n", encoding="utf-8")
        (project / "include").mkdir()
        cmake = op_dir / "test" / "CMakeLists.txt"
        cmake.write_text(self.CMAKE, encoding="utf-8")
        if with_op_root_cmake:
            (op_dir / "CMakeLists.txt").write_text(
                "project(trsm LANGUAGES ASC CXX)\nadd_subdirectory(test)\n",
                encoding="utf-8")
        env = Path(temp_dir) / "env.json"
        env.write_text(
            json.dumps({"operator_project": {"path": str(project)}}),
            encoding="utf-8")
        return project, op_dir, cmake, env

    def _render(self, temp_dir, with_op_root_cmake):
        project, op_dir, cmake, env = self._project(
            temp_dir, with_op_root_cmake)
        out = Path(temp_dir) / "c_api_build"
        done = subprocess.run(
            [sys.executable, str(SCRIPT), "--env", str(env),
             "--op-dir", str(op_dir), "--project-root", str(project),
             "--build-cmake", str(cmake), "-o", str(out)],
            capture_output=True, text=True, timeout=60)
        report = json.loads(
            (out / "render_report.json").read_text(encoding="utf-8"))
        return done, report, op_dir

    def test_op_root_cmake_makes_the_op_dir_the_cmake_source_dir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            done, report, op_dir = self._render(temp_dir, True)
            self.assertEqual(0, done.returncode, done.stderr + done.stdout)
            self.assertEqual(str(op_dir.resolve()), report["cmake_source_dir"])
            self.assertEqual(
                [str((op_dir / "op_host/trsm_host.cpp").resolve()),
                 str((op_dir / "op_kernel/trsm_kernel.cpp").resolve())],
                report["sources"])
            self.assertIn(
                str((op_dir / "op_kernel").resolve()), report["includes"])

    def test_without_op_root_cmake_the_project_root_still_applies(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            done, report, _ = self._render(temp_dir, False)
            self.assertEqual(2, done.returncode)
            self.assertIn("越过待验收算子工程目录", report["failures"][0])


if __name__ == "__main__":
    unittest.main()
