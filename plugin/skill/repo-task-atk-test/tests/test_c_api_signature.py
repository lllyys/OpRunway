"""c_api 调用序列表的解析、分类与命令行回归测试。"""

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
ALIGN = SCRIPTS / "align_signatures.py"

STRSM_HEADER = textwrap.dedent("""
    extern "C" {
    aclblasStatus_t aclblasStrsmBatched(
        aclblasHandle_t handle,
        aclblasSideMode_t side,
        aclblasFillMode_t uplo,
        aclblasOperation_t transa,
        aclblasDiagType_t diag,
        int64_t m,
        int64_t n,
        const float* alpha,
        const float* const aArray[],
        int64_t lda,
        float* const bArray[],
        int64_t ldb,
        int64_t batchCount);
    }
""")

SAXPY_DECL = (
    "aclblasStatus_t aclblasSaxpy(aclblasHandle_t handle, int n, "
    "const float* alpha, float* x, int incx, float* y, int incy);"
)


def load_module():
    sys.path.insert(0, str(SCRIPTS))
    import _c_api_signature
    return _c_api_signature


class StrsmSequenceTest(unittest.TestCase):
    def test_full_declaration_builds_the_v1_table(self):
        mod = load_module()
        declaration = mod.parse_declaration(STRSM_HEADER)
        self.assertEqual(declaration["symbol"], "aclblasStrsmBatched")
        self.assertEqual(declaration["return_type"], "aclblasStatus_t")
        self.assertTrue(declaration["extern_c"])
        self.assertEqual(len(declaration["parameters"]), 13)

        enums = {
            "aclblasSideMode_t": {"ACLBLAS_SIDE_LEFT": 0, "ACLBLAS_SIDE_RIGHT": 1},
            "aclblasFillMode_t": {"ACLBLAS_FILL_UPPER": 0, "ACLBLAS_FILL_LOWER": 1},
            "aclblasOperation_t": {"ACLBLAS_OP_N": 0, "ACLBLAS_OP_T": 1},
            "aclblasDiagType_t": {"ACLBLAS_DIAG_NON_UNIT": 0, "ACLBLAS_DIAG_UNIT": 1},
        }
        args = mod.classify(
            declaration["parameters"],
            {"alpha": "项目公开 API 文档说明 alpha 是主机侧标量"},
            ("bArray", "项目公开 API 文档说明 bArray 原地写回"),
            {"name": "handle", "type": "aclblasHandle_t",
             "rule": "首个以 Handle_t 结尾的类型"},
            enum_types=enums,
        )
        by_name = {item["name"]: item for item in args}
        for name in ("side", "uplo", "transa", "diag"):
            self.assertEqual(by_name[name]["class"], "enum")
            self.assertEqual(by_name[name]["ctype"], "int32")
            self.assertTrue(by_name[name]["enum_values"])
        self.assertEqual(by_name["alpha"]["class"], "host_scalar")
        self.assertIn("公开 API 文档", by_name["alpha"]["source"])
        self.assertEqual(by_name["aArray"]["class"], "device_ptr_array")
        self.assertEqual(by_name["bArray"]["class"], "device_ptr_array")
        self.assertEqual(by_name["lda"]["class"], "layout_param")
        self.assertEqual(by_name["ldb"]["class"], "layout_param")
        for name in ("m", "n", "batchCount"):
            self.assertEqual(by_name[name]["class"], "dim")

        table = mod.build_sequence(
            declaration,
            args,
            output=("bArray", "项目公开 API 文档说明 bArray 原地写回"),
            context={"shape": "opaque_functions", "type": "aclblasHandle_t",
                     "rule": "头文件声明 Create/Destroy"},
        )
        self.assertFalse(table["mangled"])
        self.assertEqual(table["exported_name"], "aclblasStrsmBatched")
        self.assertEqual(table["sequence"][0]["step"], "context")
        self.assertEqual(table["sequence"][1]["step"], "execute")
        self.assertTrue(table["sequence"][1]["timed"])
        self.assertEqual(table["sequence"][1]["status_ok"], 0)
        self.assertEqual(table["output"]["in_place"], "bArray")
        self.assertIsNone(table["layout"]["order"])
        self.assertIsNone(table["layout"]["confirmed_by"])


class SaxpyAmbiguityTest(unittest.TestCase):
    def _classify(self, host_scalars=None, device_pointers=None):
        mod = load_module()
        declaration = mod.parse_declaration(SAXPY_DECL)
        return mod.classify(
            declaration["parameters"],
            host_scalars or {},
            ("y", "项目公开 API 文档说明 y 原地写回"),
            {"name": "handle", "type": "aclblasHandle_t",
             "rule": "首个以 Handle_t 结尾的类型"},
            device_pointer_names=device_pointers or {},
        )

    def test_non_const_x_and_y_remain_device_pointers(self):
        args = self._classify(
            host_scalars={"alpha": "项目公开 API 文档说明 alpha 是主机侧标量"})
        by_name = {item["name"]: item for item in args}
        self.assertEqual(by_name["alpha"]["class"], "host_scalar")
        self.assertEqual(by_name["x"]["class"], "device_ptr")
        self.assertEqual(by_name["y"]["class"], "device_ptr")
        self.assertEqual(by_name["incx"]["class"], "layout_param")
        self.assertEqual(by_name["incy"]["class"], "layout_param")

    def test_alpha_requires_an_explicit_decision(self):
        mod = load_module()
        with self.assertRaises(mod.CApiSignatureError) as caught:
            self._classify()
        self.assertIn("alpha", str(caught.exception))
        self.assertIn("--host-scalar", str(caught.exception))
        self.assertIn("--device-pointer", str(caught.exception))

    def test_device_pointer_override_records_its_justification(self):
        args = self._classify(
            device_pointers={"alpha": "项目公开 API 文档说明 alpha 位于设备内存"})
        alpha = next(item for item in args if item["name"] == "alpha")
        self.assertEqual(alpha["class"], "device_ptr")
        self.assertIn("设备内存", alpha["source"])


class MangledDeclarationTest(unittest.TestCase):
    def test_complex_declaration_outside_extern_c_is_marked_mangled(self):
        mod = load_module()
        declaration = mod.parse_declaration(textwrap.dedent("""
            aclblasStatus_t aclblasCtrsmBatched(
                aclblasHandle_t handle, const std::complex<float>* alpha,
                const std::complex<float>* const aArray[],
                std::complex<float>* const bArray[], int64_t batchCount);
        """))
        self.assertFalse(declaration["extern_c"])
        self.assertEqual(declaration["parameters"][1]["base_type"],
                         "std::complex<float>")
        args = mod.classify(
            declaration["parameters"],
            {},
            ("bArray", "项目公开 API 文档说明 bArray 原地写回"),
            {"name": "handle", "type": "aclblasHandle_t", "rule": "显式指定"},
            device_pointer_names={
                "alpha": "项目公开 API 文档说明 alpha 位于设备内存"},
        )
        table = mod.build_sequence(
            declaration, args,
            output=("bArray", "项目公开 API 文档说明 bArray 原地写回"),
            context={"shape": "opaque_functions", "type": "aclblasHandle_t",
                     "rule": "显式指定"},
        )
        self.assertTrue(table["mangled"])
        self.assertIsNone(table["exported_name"])

    def test_complex_host_scalar_is_rejected_by_the_executor_closed_set(self):
        mod = load_module()
        declaration = mod.parse_declaration(textwrap.dedent("""
            int aclblasCtrsmBatched(
                aclblasHandle_t handle, const std::complex<float>* alpha,
                std::complex<float>* const bArray[]);
        """))
        with self.assertRaises(mod.CApiSignatureError) as caught:
            mod.classify(
                declaration["parameters"],
                {"alpha": "任务书说明 alpha 是主机侧标量"},
                ("bArray", "任务书说明 bArray 原地写回"),
                {"name": "handle", "type": "aclblasHandle_t", "rule": "显式指定"},
            )
        self.assertIn("v1 未开放复数主机标量", str(caught.exception))


class ClosedTierTest(unittest.TestCase):
    def test_descriptor_parameter_is_rejected_with_its_name(self):
        mod = load_module()
        declaration = mod.parse_declaration(
            "int fooRun(fooHandle_t handle, fooDescr_t descriptor, float* out);")
        with self.assertRaises(mod.CApiSignatureError) as caught:
            mod.classify(
                declaration["parameters"], {},
                ("out", "项目公开 API 文档说明 out 原地写回"),
                {"name": "handle", "type": "fooHandle_t", "rule": "显式指定"},
            )
        message = str(caught.exception)
        self.assertIn("descriptor", message)
        self.assertIn("sequence step tier", message)
        self.assertIn("未开放", message)

    def test_output_must_be_a_device_pointer_class(self):
        mod = load_module()
        declaration = mod.parse_declaration(
            "int run(fooHandle_t handle, int n, float* out);")
        with self.assertRaises(mod.CApiSignatureError) as caught:
            mod.classify(
                declaration["parameters"], {},
                ("n", "任务书误把维度写成原地输出"),
                {"name": "handle", "type": "fooHandle_t", "rule": "显式指定"},
            )
        self.assertIn("dim", str(caught.exception))
        self.assertIn("n", str(caught.exception))


class PodStructTest(unittest.TestCase):
    def test_plain_fields_pass(self):
        mod = load_module()
        ok, reason = mod.is_trivial_pod_struct(
            "struct _aclblas_handle { void* stream; int device_id; };",
            "_aclblas_handle")
        self.assertTrue(ok, reason)

    def test_virtual_member_and_base_class_fail(self):
        mod = load_module()
        for header, expected in (
            ("struct Handle { virtual ~Handle(); void* stream; };", "virtual"),
            ("struct Handle : public Base { void* stream; };", "基类"),
        ):
            with self.subTest(header=header):
                ok, reason = mod.is_trivial_pod_struct(header, "Handle")
                self.assertFalse(ok)
                self.assertIn(expected, reason)

    def test_struct_context_embeds_closed_ctypes_fields(self):
        mod = load_module()
        header = textwrap.dedent("""
            typedef void* aclrtStream;
            struct _aclblas_handle {
                aclrtStream stream;
                void* workspace;
                size_t workspace_size;
                bool use_workspace;
                int32_t device_id;
                int64_t generation;
                float scale;
                double tolerance;
            };
        """)
        declaration = mod.parse_declaration(
            "int run(aclblasHandle_t handle, float* out);")
        args = mod.classify(
            declaration["parameters"], {},
            ("out", "项目公开 API 文档说明 out 原地写回"),
            {"name": "handle", "type": "aclblasHandle_t", "rule": "显式指定"},
        )
        table = mod.build_sequence(
            declaration, args,
            output=("out", "项目公开 API 文档说明 out 原地写回"),
            context={"shape": "struct_handle", "type": "aclblasHandle_t",
                     "rule": "公开 struct", "struct": "_aclblas_handle"},
            header_text=header,
        )
        self.assertEqual(table["sequence"][0]["struct"], {
            "name": "_aclblas_handle",
            "fields": [
                {"name": "stream", "ctype": "c_void_p"},
                {"name": "workspace", "ctype": "c_void_p"},
                {"name": "workspace_size", "ctype": "c_size_t"},
                {"name": "use_workspace", "ctype": "c_bool"},
                {"name": "device_id", "ctype": "c_int32"},
                {"name": "generation", "ctype": "c_int64"},
                {"name": "scale", "ctype": "c_float"},
                {"name": "tolerance", "ctype": "c_double"},
            ],
        })


class BareAliasHandleTest(unittest.TestCase):
    """裸 void 指针句柄与独立命名的公开结构体也能生成上下文描述。"""

    HEADER = textwrap.dedent("""
        typedef void* aclblasHandle_t;
        struct _aclblas_handle {
            aclrtStream stream = nullptr;
            void* default_workspace = nullptr;
            size_t default_workspace_size = 0;
            bool use_user_workspace = false;
        };
        extern "C" int aclblasRun(aclblasHandle_t handle, float* out);
    """)

    def test_default_initializers_keep_the_struct_pod_eligible(self):
        mod = load_module()
        ok, reason, fields = mod._inspect_trivial_pod_struct(
            self.HEADER, "_aclblas_handle")
        self.assertTrue(ok, reason)
        self.assertEqual(fields, [
            {"name": "stream", "ctype": "c_void_p"},
            {"name": "default_workspace", "ctype": "c_void_p"},
            {"name": "default_workspace_size", "ctype": "c_size_t"},
            {"name": "use_user_workspace", "ctype": "c_bool"},
        ])

    def test_only_the_open_acl_stream_alias_is_known(self):
        mod = load_module()
        self.assertEqual({"aclrtStream": "void*"}, mod._PUBLIC_ACL_ALIASES)
        ok, reason, _ = mod._inspect_trivial_pod_struct(
            "struct H { aclrtEvent event; };", "H")
        self.assertFalse(ok)
        self.assertIn("aclrtEvent", reason)

    def test_bare_alias_requires_an_explicit_struct_name(self):
        mod = load_module()
        with self.assertRaises(mod.CApiSignatureError):
            mod.struct_name_for_context(self.HEADER, "aclblasHandle_t")
        self.assertEqual(
            "_aclblas_handle",
            mod.struct_name_for_context(
                self.HEADER, "aclblasHandle_t", explicit="_aclblas_handle"))

    def test_explicit_struct_must_exist_in_the_public_header(self):
        mod = load_module()
        with self.assertRaises(mod.CApiSignatureError) as caught:
            mod.struct_name_for_context(
                self.HEADER, "aclblasHandle_t", explicit="_missing_handle")
        self.assertIn("_missing_handle", str(caught.exception))

    def test_cli_records_the_struct_choice_and_justification(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            project = temp / "operator_project"
            project.mkdir()
            header = project / "aclblas_minimal.h"
            header.write_text(self.HEADER, encoding="utf-8")
            env = temp / "env.json"
            env.write_text(
                json.dumps({"operator_project": {"path": str(project)}}),
                encoding="utf-8")
            output = temp / "run_call_sequence.json"
            base = [
                sys.executable, str(ALIGN), "--call-convention", "c_api",
                "--env", str(env), "--header", str(header),
                "--candidate-name", "aclblasRun", "--baseline", "torch.add",
                "--output", "out=公开 API 文档说明 out 原地写回",
                "-o", str(output),
            ]
            refused = subprocess.run(
                base, capture_output=True, text=True, timeout=60)
            self.assertEqual(2, refused.returncode, refused.stderr)
            self.assertIn("aclblasHandle_t", refused.stderr)

            done = subprocess.run(
                base + [
                    "--context-struct",
                    "_aclblas_handle=任务书 §7.2 写明调用方构造公开结构体",
                ],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(0, done.returncode, done.stderr)
            table = json.loads(output.read_text(encoding="utf-8"))

        context = table["sequence"][0]
        self.assertEqual("struct_handle", context["shape"])
        self.assertEqual("_aclblas_handle", context["struct"]["name"])
        self.assertIn("任务书 §7.2", context["struct_source"])
        self.assertEqual(
            {"name": "stream", "ctype": "c_void_p"},
            context["struct"]["fields"][0])
        self.assertTrue(table["signature_source"].endswith("aclblas_minimal.h"))


class OpaqueContextFunctionsTest(unittest.TestCase):
    def test_context_records_create_optional_set_stream_and_destroy(self):
        mod = load_module()
        context = mod.context_shape(textwrap.dedent("""
            int aclblasCreate(aclblasHandle_t* handle);
            int aclblasSetStream(aclblasHandle_t handle, void* stream);
            int aclblasDestroy(aclblasHandle_t handle);
        """), "aclblasHandle_t")
        self.assertEqual(context["shape"], "opaque_functions")
        self.assertEqual(context["create"], "aclblasCreate")
        self.assertEqual(context["set_stream"], "aclblasSetStream")
        self.assertEqual(context["destroy"], "aclblasDestroy")

    def test_opaque_context_allows_missing_set_stream(self):
        mod = load_module()
        context = mod.context_shape(textwrap.dedent("""
            int aclblasCreate(aclblasHandle_t* handle);
            int aclblasDestroy(aclblasHandle_t handle);
        """), "aclblasHandle_t")
        self.assertIsNone(context["set_stream"])

    def test_create_with_an_extra_parameter_is_rejected_and_recorded(self):
        mod = load_module()
        context = mod.context_shape(textwrap.dedent("""
            struct aclblasHandle_t { void* stream; };
            int aclblasCreate(aclblasHandle_t* handle, int flags);
            int aclblasDestroy(aclblasHandle_t handle);
        """), "aclblasHandle_t")
        self.assertEqual("struct_handle", context["shape"])
        rejected = context["rejected_candidates"]
        self.assertTrue(any(item["name"] == "aclblasCreate" for item in rejected))
        self.assertTrue(any("1" in item["reason"] for item in rejected))


class ContextPointerShapeTest(unittest.TestCase):
    def test_by_value_struct_context_is_rejected(self):
        mod = load_module()
        header = textwrap.dedent("""
            struct PublicHandle_t { void* stream; };
            int publicRun(PublicHandle_t handle, float* out);
        """)
        declaration = mod.parse_declaration(header, "publicRun")
        with self.assertRaises(mod.CApiSignatureError) as caught:
            mod.infer_context(declaration["parameters"], header_text=header)
        message = str(caught.exception)
        self.assertIn("handle", message)
        self.assertIn("v1 未开放按值传递的上下文参数", message)

    def test_pointer_typedef_context_is_accepted(self):
        mod = load_module()
        header = textwrap.dedent("""
            typedef struct PublicHandleImpl* PublicHandle_t;
            int publicRun(PublicHandle_t handle, float* out);
        """)
        declaration = mod.parse_declaration(header, "publicRun")
        context = mod.infer_context(declaration["parameters"], header_text=header)
        self.assertEqual("handle", context["name"])


class CApiCliTest(unittest.TestCase):
    def test_cli_writes_a_call_sequence_table(self):
        header_text = textwrap.dedent("""
            typedef enum aclblasMode {
                ACLBLAS_MODE_A = 0,
                ACLBLAS_MODE_B = 2
            } aclblasMode_t;
            typedef struct aclblasHandleImpl* aclblasHandle_t;
            extern "C" {
            aclblasStatus_t aclblasCreate(aclblasHandle_t* handle);
            aclblasStatus_t aclblasDestroy(aclblasHandle_t handle);
            aclblasStatus_t aclblasSaxpy(
                aclblasHandle_t handle, aclblasMode_t mode, int n,
                const float* alpha, float* x, int incx, float* y, int incy);
            }
        """)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            project = temp / "operator_project"
            project.mkdir()
            header = project / "aclblas.h"
            header.write_text(header_text, encoding="utf-8")
            env = temp / "env.json"
            env.write_text(json.dumps({"operator_project": {"path": str(project)}}),
                           encoding="utf-8")
            output = temp / "saxpy_call_sequence.json"
            done = subprocess.run(
                [sys.executable, str(ALIGN),
                 "--call-convention", "c_api",
                 "--env", str(env), "--header", str(header),
                 "--candidate-name", "aclblasSaxpy",
                 "--baseline", "torch.add",
                 "--host-scalar", "alpha=项目公开 API 文档说明 alpha 是主机侧标量",
                 "--output", "y=项目公开 API 文档说明 y 原地写回",
                 "-o", str(output)],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            table = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(table["schema_version"], 1)
        self.assertEqual(table["symbol"], "aclblasSaxpy")
        self.assertEqual(table["exported_name"], "aclblasSaxpy")
        self.assertFalse(table["mangled"])
        self.assertEqual(table["baseline"], "torch.add")
        context = table["sequence"][0]
        self.assertEqual(context["shape"], "opaque_functions")
        self.assertIn("Create/Destroy", context["rule"])
        self.assertEqual(context["create"], "aclblasCreate")
        self.assertIsNone(context["set_stream"])
        self.assertEqual(context["destroy"], "aclblasDestroy")
        mode = next(item for item in table["sequence"][1]["args"]
                    if item["name"] == "mode")
        self.assertEqual(mode["enum_values"], {"ACLBLAS_MODE_A": 0,
                                                "ACLBLAS_MODE_B": 2})
        self.assertEqual(table["layout"], {"order": None, "confirmed_by": None})

    def test_cli_embeds_struct_fields_from_a_common_header(self):
        header_text = textwrap.dedent("""
            typedef struct PublicHandle {
                void* stream;
                size_t workspace_size;
                bool enabled;
            } PublicHandle_t;
            extern "C" int publicRun(PublicHandle_t* handle, float* out);
        """)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            project = temp / "operator_project"
            project.mkdir()
            header = project / "public.h"
            header.write_text(header_text, encoding="utf-8")
            env = temp / "env.json"
            env.write_text(json.dumps({"operator_project": {"path": str(project)}}),
                           encoding="utf-8")
            output = temp / "public_call_sequence.json"
            done = subprocess.run(
                [sys.executable, str(ALIGN), "--call-convention", "c_api",
                 "--env", str(env), "--header", str(header),
                 "--candidate-name", "publicRun", "--baseline", "torch.add",
                 "--output", "out=项目公开 API 文档说明 out 原地写回",
                 "-o", str(output)],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            context = json.loads(output.read_text(encoding="utf-8"))["sequence"][0]

        self.assertEqual(context["shape"], "struct_handle")
        self.assertEqual(context["struct"], {
            "name": "PublicHandle",
            "fields": [
                {"name": "stream", "ctype": "c_void_p"},
                {"name": "workspace_size", "ctype": "c_size_t"},
                {"name": "enabled", "ctype": "c_bool"},
            ],
        })


class LayoutCliTest(unittest.TestCase):
    HEADER = textwrap.dedent("""
        typedef struct HandleImpl* DemoHandle_t;
        int demoCreate(DemoHandle_t* handle);
        int demoDestroy(DemoHandle_t handle);
        extern "C" int demoRun(DemoHandle_t handle, float* out);
    """)

    def _run(self, *layout_args):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        temp = Path(temp_dir.name)
        project = temp / "operator_project"
        project.mkdir()
        header = project / "demo.h"
        header.write_text(self.HEADER, encoding="utf-8")
        env = temp / "env.json"
        env.write_text(json.dumps({"operator_project": {"path": str(project)}}),
                       encoding="utf-8")
        output = temp / "demo_call_sequence.json"
        done = subprocess.run(
            [sys.executable, str(ALIGN), "--call-convention", "c_api",
             "--env", str(env), "--header", str(header),
             "--candidate-name", "demoRun", "--baseline", "torch.clone",
             "--output", "out=任务书说明 out 原地写回",
             *layout_args, "-o", str(output)],
            capture_output=True, text=True, timeout=60)
        table = json.loads(output.read_text(encoding="utf-8")) if output.exists() else None
        return done, table

    def test_confirmed_layout_is_written_to_the_table(self):
        done, table = self._run(
            "--layout-order", "row_major",
            "--layout-source", "任务书 §4 明确采用行主序")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            {"order": "row_major", "confirmed_by": "任务书 §4 明确采用行主序"},
            table["layout"])

    def test_layout_flags_must_be_given_as_a_pair(self):
        for args in (("--layout-order", "row_major"),
                     ("--layout-source", "任务书 §4")):
            with self.subTest(args=args):
                done, _ = self._run(*args)
                self.assertEqual(2, done.returncode)
                self.assertIn("layout", done.stderr + done.stdout)

    def test_bad_layout_order_is_rejected(self):
        done, _ = self._run(
            "--layout-order", "diagonal", "--layout-source", "任务书 §4")
        self.assertEqual(2, done.returncode)
        self.assertIn("invalid choice", done.stderr)


if __name__ == "__main__":
    unittest.main()
