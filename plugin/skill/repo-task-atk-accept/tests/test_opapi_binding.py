import sys
import tempfile
import unittest
from pathlib import Path

from _paths import SCRIPTS


sys.path.insert(0, str(SCRIPTS))

from _opapi_binding import BindingError, inspect_library, verify_runtime_log  # noqa: E402


class _Library:
    pass


class OpApiBindingTest(unittest.TestCase):
    def test_exact_library_and_required_symbols_are_verified(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            library = Path(temp_dir) / "libcust_opapi.so"
            library.write_bytes(b"candidate")
            loaded = _Library()
            loaded.aclnnExampleGetWorkspaceSize = object()
            loaded.aclnnExample = object()

            report = inspect_library(
                library, "Example", loader=lambda _: loaded)

            self.assertEqual(str(library.resolve()), report["library"])
            self.assertEqual(
                ["aclnnExampleGetWorkspaceSize", "aclnnExample"],
                report["required_symbols"],
            )

    def test_missing_symbol_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            library = Path(temp_dir) / "libcust_opapi.so"
            library.write_bytes(b"candidate")
            loaded = _Library()
            loaded.aclnnExampleGetWorkspaceSize = object()

            with self.assertRaisesRegex(BindingError, "aclnnExample"):
                inspect_library(library, "Example", loader=lambda _: loaded)

    def test_runtime_log_must_name_the_exact_candidate_library(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            library = (Path(temp_dir) / "libcust_opapi.so").resolve()
            library.write_bytes(b"candidate")
            log = (
                "import aclnnExampleGetWorkspaceSize  from "
                f"{library} success!\n"
                "自动进行参数校验，头文件路径是：/tmp/vendor，"
                "算子一段式名称是：aclnnExampleGetWorkspaceSize\n"
            )

            match = verify_runtime_log(log, library, "Example")

            self.assertEqual(str(library), match["loaded_library"])
            self.assertTrue(match["signature_check_verified"])

    def test_runtime_without_signature_check_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            library = (Path(temp_dir) / "libcust_opapi.so").resolve()
            library.write_bytes(b"candidate")
            log = (
                "import aclnnExampleGetWorkspaceSize from "
                f"{library} success!\n"
            )

            with self.assertRaisesRegex(BindingError, "签名校验"):
                verify_runtime_log(log, library, "Example")

    def test_runtime_signature_check_accepts_return_type_prefix(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            library = (Path(temp_dir) / "libcust_opapi.so").resolve()
            library.write_bytes(b"candidate")
            log = (
                "import aclnnExampleGetWorkspaceSize from "
                f"{library} success!\n"
                "自动进行参数校验，头文件路径是：/tmp/vendor，"
                "算子一段式名称是：aclnnStatus aclnnExampleGetWorkspaceSize\n"
            )

            match = verify_runtime_log(log, library, "Example")

            self.assertTrue(match["signature_check_verified"])

    def test_runtime_system_fallback_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            library = (Path(temp_dir) / "libcust_opapi.so").resolve()
            library.write_bytes(b"candidate")
            log = (
                "import aclnnExampleGetWorkspaceSize from "
                "/opt/cann/lib64/libopapi_nn.so success!\n"
            )

            with self.assertRaisesRegex(BindingError, "待验收算子库"):
                verify_runtime_log(log, library, "Example")

    def test_signature_repair_by_dropping_an_argument_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            library = (Path(temp_dir) / "libcust_opapi.so").resolve()
            library.write_bytes(b"candidate")
            log = (
                "import aclnnExampleGetWorkspaceSize from "
                f"{library} success!\n"
                "自动进行参数校验，头文件路径是：/opt/cann，"
                "算子一段式名称是：aclnnExampleGetWorkspaceSize\n"
                "[WARNING] 参数数量不匹配：尝试用第一个输入参数作为输出\n"
            )

            with self.assertRaisesRegex(BindingError, "改写"):
                verify_runtime_log(log, library, "Example")

    def test_signature_repair_by_reordering_arguments_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            library = (Path(temp_dir) / "libcust_opapi.so").resolve()
            library.write_bytes(b"candidate")
            log = (
                "import aclnnExampleGetWorkspaceSize from "
                f"{library} success!\n"
                "自动进行参数校验，头文件路径是：/opt/cann，"
                "算子一段式名称是：aclnnExampleGetWorkspaceSize\n"
                "[WARNING] 参数类型不匹配：尝试调整输入参数的位置\n"
            )

            with self.assertRaisesRegex(BindingError, "改写"):
                verify_runtime_log(log, library, "Example")

    def test_generic_module_has_no_operator_names(self):
        source = (SCRIPTS / "_opapi_binding.py").read_text(encoding="utf-8")
        for operator_term in ("median", "roll", "bernoulli"):
            self.assertNotIn(operator_term, source.lower())


if __name__ == "__main__":
    unittest.main()
