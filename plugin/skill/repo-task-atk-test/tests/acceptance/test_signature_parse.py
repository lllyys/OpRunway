"""不依赖 torch / ATK 的 ACLNN 签名解析与来源白名单回归测试。"""

import json
import tempfile
import unittest
from pathlib import Path

from _paths import SKILL_ROOT


SCRIPTS = SKILL_ROOT / "scripts"

import sys

sys.path.insert(0, str(SCRIPTS))

import _signature_parse as signature_parse  # noqa: E402
from _opapi_binding import _symbol_prefix  # noqa: E402


TAIL_OUTPUT = (
    "aclnnStatus aclnnRollGetWorkspaceSize(const aclTensor *self, "
    "const aclIntArray *shifts, const aclIntArray *dims, aclTensor *out, "
    "uint64_t *workspaceSize, aclOpExecutor **executor)"
)

MULTILINE_TAIL_OUTPUT = """aclnnStatus aclnnRollGetWorkspaceSize(
    const aclTensor *self,
    const aclIntArray *shifts,
    const aclIntArray *dims,
    aclTensor *out,
    uint64_t *workspaceSize,
    aclOpExecutor **executor)"""


class ParseSignatureTest(unittest.TestCase):
    def test_multiline_declaration_keeps_every_business_parameter(self):
        block = f"```c\n{MULTILINE_TAIL_OUTPUT};\n```"
        declaration = signature_parse.taskdoc_workspace_signature(block)
        parsed = signature_parse.parse_signature(declaration)

        self.assertEqual(
            [row["c_name"] for row in parsed["parameters"]],
            ["self", "shifts", "dims", "out"],
        )
        self.assertEqual(len(parsed["trailing"]), 2)

    def test_signature_without_atk_trailing_is_rejected(self):
        with self.assertRaises(signature_parse.AlignError) as ctx:
            signature_parse.parse_signature(
                "aclnnStatus aclnnMatmul(const aclTensor *self)"
            )
        self.assertIn("GetWorkspaceSize", str(ctx.exception))


class RejectInstalledHeaderTest(unittest.TestCase):
    """装机同名头文件不是待验收 PR 的接口来源。"""

    def _env(self, tmp, cann_home):
        path = tmp / "env.json"
        path.write_text(
            json.dumps({"cann": {"ASCEND_TOOLKIT_HOME": str(cann_home)}}),
            encoding="utf-8",
        )
        return path

    def test_header_under_cann_toolkit_home_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            cann_home = tmp / "cann-9.0.0-beta.1"
            include = cann_home / "include" / "aclnnop"
            include.mkdir(parents=True)
            header = include / "aclnn_median.h"
            header.write_text("", encoding="utf-8")
            env = self._env(tmp, cann_home)
            with self.assertRaises(signature_parse.AlignError) as ctx:
                signature_parse.reject_installed_header(str(header), str(env))
            self.assertIn("装机", str(ctx.exception))

    def test_header_under_default_ascend_root_is_rejected_even_without_env(self):
        with self.assertRaises(signature_parse.AlignError):
            signature_parse.reject_installed_header(
                "/usr/local/Ascend/cann-9.0.0-beta.1/include/aclnnop/aclnn_median.h",
                None,
            )

    def test_header_under_operator_project_is_accepted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            cann_home = tmp / "cann-9.0.0-beta.1"
            cann_home.mkdir()
            project = tmp / "ops-nn-master-experimental-index-median"
            header_dir = project / "op_host" / "op_api"
            header_dir.mkdir(parents=True)
            header = header_dir / "aclnn_median.h"
            header.write_text("", encoding="utf-8")
            env = self._env(tmp, cann_home)
            signature_parse.reject_installed_header(str(header), str(env))

    def test_header_outside_any_known_cann_root_is_accepted_without_env(self):
        signature_parse.reject_installed_header("/tmp/whatever.h", None)


class RequireProjectSourceTest(unittest.TestCase):
    """签名来源白名单只接受 env.json 登记的工程树。"""

    def _env(self, tmp, project=None, cann_home=None):
        payload = {}
        if project is not None:
            payload["operator_project"] = {"path": str(project)}
        if cann_home is not None:
            payload["cann"] = {"ASCEND_TOOLKIT_HOME": str(cann_home)}
        path = tmp / "env.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_nonexistent_source_inside_project_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            project = tmp / "ops-nn-median"
            project.mkdir()
            env = self._env(tmp, project=project)
            with self.assertRaises(signature_parse.AlignError) as ctx:
                signature_parse.require_project_source(
                    str(project / "never_written.h"),
                    str(env),
                    "--signature-source",
                )
            self.assertIn("不存在", str(ctx.exception))

    def test_source_inside_project_is_accepted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            project = tmp / "ops-nn-median"
            (project / "op_host" / "op_api").mkdir(parents=True)
            header = project / "op_host" / "op_api" / "aclnn_median.h"
            header.write_text("", encoding="utf-8")
            env = self._env(tmp, project=project)
            self.assertEqual(
                signature_parse.require_project_source(
                    str(header), str(env), "--header"
                ),
                str(header.resolve()),
            )

    def test_other_vendor_dir_is_rejected_though_no_cann_root_matches(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            project = tmp / "ops-nn-median"
            project.mkdir()
            stray = tmp / "other-team-vendor" / "aclnn_median.h"
            stray.parent.mkdir()
            stray.write_text("", encoding="utf-8")
            env = self._env(tmp, project=project)
            signature_parse.reject_installed_header(str(stray), str(env))
            with self.assertRaises(signature_parse.AlignError) as ctx:
                signature_parse.require_project_source(str(stray), str(env), "--header")
            self.assertIn("待验收算子工程目录", str(ctx.exception))

    def test_missing_operator_project_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            env = self._env(tmp, cann_home=tmp / "cann")
            with self.assertRaises(signature_parse.AlignError) as ctx:
                signature_parse.require_project_source("/tmp/x.h", str(env), "--header")
            self.assertIn("--op-repo", str(ctx.exception))

    def test_unreadable_env_is_not_a_pass(self):
        with self.assertRaises(signature_parse.AlignError):
            signature_parse.require_project_source(
                "/tmp/x.h", "/tmp/no-such-env.json", "--header"
            )


class AclnnNameNormalisationTest(unittest.TestCase):
    def _header(self, root):
        header = root / "aclnn_roll.h"
        header.write_text(TAIL_OUTPUT + ";\n", encoding="utf-8")
        return header

    def test_bare_name_finds_the_prefixed_symbol(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            signature, _ = signature_parse.read_header_signature(
                self._header(Path(temp_dir)), "Roll"
            )
        self.assertIn("aclnnRollGetWorkspaceSize", signature)

    def test_prefixed_name_still_works(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            signature, _ = signature_parse.read_header_signature(
                self._header(Path(temp_dir)), "aclnnRoll"
            )
        self.assertIn("aclnnRollGetWorkspaceSize", signature)

    def test_both_spellings_agree_with_the_binding_gate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            header = self._header(Path(temp_dir))
            for spelling in ("Roll", "aclnnRoll"):
                with self.subTest(aclnn_name=spelling):
                    signature, _ = signature_parse.read_header_signature(
                        header, spelling
                    )
                    self.assertIn(
                        f"{_symbol_prefix(spelling)}GetWorkspaceSize", signature
                    )


if __name__ == "__main__":
    unittest.main()
