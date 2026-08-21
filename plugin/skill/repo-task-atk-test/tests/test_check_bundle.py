"""check_bundle.py 的交接包接收与接口一致性回归测试。"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from datetime import datetime
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
SCRIPT = SCRIPTS / "check_bundle.py"
SEAL_SCRIPT = SCRIPTS / "seal_bundle.py"
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TESTS))

from _case_utils import file_sha256  # noqa: E402
from test_seal_bundle import make_bundle, read_json, write_json  # noqa: E402


TASK_DOC = """# Roll 算子任务书

### 2.3 接口定义

```c
aclnnStatus aclnnRollGetWorkspaceSize(
    const aclTensor *self,
    const aclIntArray *shifts,
    const aclIntArray *dims,
    aclTensor *out,
    uint64_t *workspaceSize,
    aclOpExecutor **executor);
```

### 2.4 参数说明

| 参数名 | 数据类型 | dtype类型 |
| --- | --- | --- |
| self | tensor | FLOAT16 |
| shifts | list | int64 |
| dims | list | int64 |
"""

DEFAULT_PARAMS = (
    "const aclTensor *self, "
    "const aclIntArray *shifts, "
    "const aclIntArray *dims"
)


def header_signature(params=DEFAULT_PARAMS):
    return (
        "aclnnStatus aclnnRollGetWorkspaceSize("
        f"{params}, aclTensor *out, uint64_t *workspaceSize, "
        "aclOpExecutor **executor);\n"
    )


def write_stub_packages(root):
    """写出 align_signatures.py 需要的真实可导入桩包。"""
    files = {
        "torch/__init__.py": """
            def roll(input, shifts, dims=None):
                return input
        """,
        "atk/__init__.py": "",
        "atk/tasks/__init__.py": "",
        "atk/tasks/backends/__init__.py": "",
        "atk/tasks/backends/lib_interface/__init__.py": "",
        "atk/tasks/backends/lib_interface/acl_wrapper.py": """
            import ctypes

            class AclTensor(ctypes.Structure):
                pass

            class AclIntArray(ctypes.Structure):
                pass

            class OpExecutor(ctypes.Structure):
                pass

            CPP_TO_PYTHON_TYPE = {
                "aclTensor": AclTensor,
                "aclIntArray": AclIntArray,
                "aclOpExecutor": OpExecutor,
                "int8_t": ctypes.c_int8,
                "int64_t": ctypes.c_int64,
                "bool": ctypes.c_bool,
                "uint64_t": ctypes.c_uint64,
                "float": ctypes.c_float,
                "char*": ctypes.c_char_p,
            }
        """,
        "atk/tasks/backends/pyaclnn_backend.py": """
            import ctypes

            PYTYPE_TO_CTYPE = {
                "int": ctypes.c_int64,
                "int8_t": ctypes.c_int8,
                "int64_t": ctypes.c_int64,
                "bool": ctypes.c_bool,
                "attr_bool": ctypes.c_bool,
                "float": ctypes.c_float,
                "string": ctypes.c_char_p,
            }
        """,
    }
    for name, source in files.items():
        path = Path(root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source), encoding="utf-8")


class CheckBundleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.original = self.root / "original"
        self.copy = self.root / "copy"
        self.project = self.root / "operator_project"
        self.stubs = self.root / "stubs"
        self.task_doc = self.root / "task.md"
        self.header = self.project / "op_host" / "aclnn_roll.h"

        self.original.mkdir()
        self.header.parent.mkdir(parents=True)
        self.header.write_text(header_signature(), encoding="utf-8")
        self.task_doc.write_text(TASK_DOC, encoding="utf-8")
        write_stub_packages(self.stubs)

        make_bundle(self.original)
        interface_path = self.original / "evidence" / "interface.json"
        interface = read_json(interface_path)
        interface.update(
            {
                "interface_mode": "aclnn",
                "candidate_symbol": "aclnnRoll",
                "baseline_api": "torch.roll",
                "baseline_kind": "torch",
                "task_doc": {
                    "name": self.task_doc.name,
                    "sha256": file_sha256(self.task_doc),
                },
            }
        )
        write_json(interface_path, interface)

        write_json(
            self.original / "evidence" / "signature_alignment.json",
            {
                "aclnn": {
                    "inputs": [
                        {"c_name": "self", "c_type": "aclTensor"},
                        {"c_name": "shifts", "c_type": "aclIntArray"},
                        {"c_name": "dims", "c_type": "aclIntArray"},
                    ]
                },
                "baseline_adapter": {"required": False},
                "aclnn_adapter": {"required": False},
            },
        )
        env_path = self.original / "evidence" / "env.json"
        env = read_json(env_path)
        env["operator_project"] = {"path": str(self.project)}
        write_json(env_path, env)

        sealed = subprocess.run(
            [sys.executable, str(SEAL_SCRIPT), "-C", str(self.original)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(0, sealed.returncode, sealed.stderr)
        shutil.copytree(self.original, self.copy)
        self.env_path = self.copy / "evidence" / "env.json"
        self.intake_path = self.copy / "evidence" / "bundle_intake.json"

    def run_check(self, *extra, with_interface=True, baseline="torch.roll"):
        command = [
            sys.executable,
            str(SCRIPT),
            "-C",
            str(self.copy),
            "--task-doc",
            str(self.task_doc),
            "--env",
            str(self.env_path),
        ]
        if with_interface:
            command.extend(
                [
                    "--header",
                    str(self.header),
                    "--aclnn-name",
                    "Roll",
                ]
            )
            if baseline is not None:
                command.extend(["--baseline", baseline])
        command.extend(str(item) for item in extra)
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "PYTHONPATH": str(self.stubs)},
        )

    def intake(self):
        return read_json(self.intake_path)

    def rewrite_header(self, params):
        self.header.write_text(header_signature(params), encoding="utf-8")

    def test_unchanged_copy_passes_all_four_checks(self):
        done = self.run_check()
        self.assertEqual(0, done.returncode, done.stderr)
        report = self.intake()
        self.assertEqual("pass", report["verdict"])
        self.assertEqual(file_sha256(self.copy / "evidence" / "bundle.json"),
                         report["bundle_sha256"])
        self.assertIsNotNone(datetime.fromisoformat(report["checked_at"]).utcoffset())
        for key in ("integrity", "task_doc", "atk_version", "interface"):
            with self.subTest(check=key):
                self.assertTrue(report[key]["applicable"])
                self.assertTrue(report[key]["passed"])
                self.assertIsInstance(report[key]["evidence"], dict)

    def test_manifest_baseline_is_used_when_cli_baseline_is_omitted(self):
        done = self.run_check(baseline=None)
        self.assertEqual(0, done.returncode, done.stderr)
        item = self.intake()["interface"]
        self.assertTrue(item["applicable"])
        self.assertTrue(item["passed"])

    def test_manifest_baseline_overrides_a_different_cli_value(self):
        done = self.run_check(baseline="torch.not_the_manifest_baseline")
        self.assertEqual(0, done.returncode, done.stderr)
        item = self.intake()["interface"]
        self.assertTrue(item["passed"])
        self.assertIn("torch.not_the_manifest_baseline", item["evidence"]["note"])
        self.assertIn("torch.roll", item["evidence"]["note"])

    def test_cli_baseline_cannot_replace_a_missing_manifest_baseline(self):
        manifest_path = self.copy / "evidence" / "bundle.json"
        manifest = read_json(manifest_path)
        manifest["interface"].pop("baseline_api")
        write_json(manifest_path, manifest)
        done = self.run_check()
        self.assertEqual(0, done.returncode, done.stderr)
        item = self.intake()["interface"]
        self.assertFalse(item["applicable"])
        self.assertIn("interface.baseline_api", item["evidence"]["reason"])

    def test_changed_file_is_reported_as_mismatched(self):
        path = self.copy / "must_cover.json"
        path.write_bytes(path.read_bytes() + b"x")
        done = self.run_check()
        self.assertEqual(2, done.returncode, done.stderr)
        self.assertIn("must_cover.json",
                      self.intake()["integrity"]["evidence"]["mismatched"])

    def test_missing_manifest_file_is_reported(self):
        (self.copy / "frozen_med" / "0" / "input.bin").unlink()
        done = self.run_check()
        self.assertEqual(2, done.returncode, done.stderr)
        self.assertIn("frozen_med/0/input.bin",
                      self.intake()["integrity"]["evidence"]["missing"])

    def test_new_file_under_frozen_directory_is_rejected(self):
        (self.copy / "frozen_med" / "extra.bin").write_bytes(b"extra")
        done = self.run_check()
        self.assertEqual(2, done.returncode, done.stderr)
        self.assertIn("frozen_med/extra.bin",
                      self.intake()["integrity"]["evidence"]["unexpected"])

    def test_new_file_under_conclusion_is_allowed(self):
        path = self.copy / "conclusion" / "summary.md"
        path.parent.mkdir()
        path.write_text("结论\n", encoding="utf-8")
        done = self.run_check()
        self.assertEqual(0, done.returncode, done.stderr)

    def test_new_root_yaml_is_rejected(self):
        (self.copy / "other.yaml").write_text("name: other\n", encoding="utf-8")
        done = self.run_check()
        self.assertEqual(2, done.returncode, done.stderr)
        self.assertIn("other.yaml",
                      self.intake()["integrity"]["evidence"]["unexpected"])

    def test_new_evidence_json_is_allowed(self):
        write_json(self.copy / "evidence" / "soc_binding.json", {"soc": "stub"})
        done = self.run_check()
        self.assertEqual(0, done.returncode, done.stderr)

    def test_new_bytecode_under_ignored_directory_is_allowed(self):
        path = self.copy / "nested" / "__pycache__" / "generated.pyc"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"bytecode")
        done = self.run_check()
        self.assertEqual(0, done.returncode, done.stderr)

    def test_different_task_document_fails_its_check(self):
        other = self.root / "other.md"
        other.write_text(TASK_DOC + "\n不同内容\n", encoding="utf-8")
        done = self.run_check("--task-doc", other)
        self.assertEqual(2, done.returncode, done.stderr)
        self.assertFalse(self.intake()["task_doc"]["passed"])

    def test_different_atk_version_fails_its_check(self):
        env = read_json(self.env_path)
        env["fingerprint"]["atk"] = "8.0.0"
        write_json(self.env_path, env)
        done = self.run_check()
        self.assertEqual(2, done.returncode, done.stderr)
        self.assertFalse(self.intake()["atk_version"]["passed"])

    def test_task_document_and_atk_failures_are_both_reported(self):
        other = self.root / "other.md"
        other.write_text(TASK_DOC + "\n不同内容\n", encoding="utf-8")
        env = read_json(self.env_path)
        env["fingerprint"]["atk"] = "8.0.0"
        write_json(self.env_path, env)
        done = self.run_check("--task-doc", other)
        self.assertEqual(2, done.returncode, done.stderr)
        report = self.intake()
        self.assertFalse(report["task_doc"]["passed"])
        self.assertFalse(report["atk_version"]["passed"])

    def test_pr_header_extra_parameter_is_reported(self):
        self.rewrite_header(DEFAULT_PARAMS + ", int64_t axis")
        done = self.run_check()
        self.assertEqual(2, done.returncode, done.stderr)
        self.assertIn("axis", self.intake()["interface"]["evidence"]["extra"])
        self.assertIn("PR 接口与任务书 §2.3 不一致", done.stdout)

    def test_pr_header_missing_parameter_is_reported(self):
        self.rewrite_header("const aclTensor *self, const aclIntArray *shifts")
        done = self.run_check()
        self.assertEqual(2, done.returncode, done.stderr)
        self.assertIn("dims", self.intake()["interface"]["evidence"]["missing"])
        self.assertIn("PR 接口与任务书 §2.3 不一致", done.stdout)

    def test_pr_header_reordered_parameters_are_reported(self):
        self.rewrite_header(
            "const aclTensor *self, const aclIntArray *dims, "
            "const aclIntArray *shifts"
        )
        done = self.run_check()
        self.assertEqual(2, done.returncode, done.stderr)
        self.assertTrue(self.intake()["interface"]["evidence"]["reordered"])
        self.assertIn("PR 接口与任务书 §2.3 不一致", done.stdout)

    def test_pr_header_type_mismatch_is_reported(self):
        self.rewrite_header(
            "const aclTensor *self, const aclTensor *shifts, "
            "const aclIntArray *dims"
        )
        done = self.run_check()
        self.assertEqual(2, done.returncode, done.stderr)
        mismatches = self.intake()["interface"]["evidence"]["type_mismatch"]
        self.assertIn("shifts", [item["name"] for item in mismatches])
        self.assertIn("PR 接口与任务书 §2.3 不一致", done.stdout)

    def test_non_aclnn_mode_makes_interface_not_applicable(self):
        manifest_path = self.copy / "evidence" / "bundle.json"
        manifest = read_json(manifest_path)
        manifest["interface"]["interface_mode"] = "pytorch"
        write_json(manifest_path, manifest)
        done = self.run_check(with_interface=False)
        self.assertEqual(0, done.returncode, done.stderr)
        item = self.intake()["interface"]
        self.assertFalse(item["applicable"])
        self.assertIsNone(item["passed"])
        self.assertIn("pytorch", item["evidence"]["reason"])

    def test_aclnn_without_header_is_not_applicable_but_requests_rerun(self):
        done = self.run_check(with_interface=False)
        self.assertEqual(0, done.returncode, done.stderr)
        item = self.intake()["interface"]
        self.assertFalse(item["applicable"])
        self.assertIsNone(item["passed"])
        self.assertIn("未给头文件", item["evidence"]["reason"])
        self.assertIn("补跑", done.stdout)

    def test_missing_manifest_is_an_input_error(self):
        (self.copy / "evidence" / "bundle.json").unlink()
        done = self.run_check()
        self.assertEqual(3, done.returncode, done.stderr)

    def test_malformed_manifest_is_an_input_error(self):
        (self.copy / "evidence" / "bundle.json").write_text(
            "{不是 JSON", encoding="utf-8"
        )
        done = self.run_check()
        self.assertEqual(3, done.returncode, done.stderr)

    def test_unknown_manifest_schema_is_an_input_error(self):
        manifest_path = self.copy / "evidence" / "bundle.json"
        manifest = read_json(manifest_path)
        manifest["schema_version"] = 2
        write_json(manifest_path, manifest)
        done = self.run_check()
        self.assertEqual(3, done.returncode, done.stderr)
        self.assertIn("认识", done.stderr)
        self.assertIn("1", done.stderr)

    def test_unreadable_task_document_is_an_input_error(self):
        done = self.run_check("--task-doc", self.root / "missing.md")
        self.assertEqual(3, done.returncode, done.stderr)

    def test_unreadable_environment_is_an_input_error(self):
        self.env_path.write_text("{不是 JSON", encoding="utf-8")
        done = self.run_check()
        self.assertEqual(3, done.returncode, done.stderr)

    def test_rewire_record_and_updated_digest_are_accepted_and_copied(self):
        changed = self.copy / "must_cover.json"
        changed.write_text('{"rewired": true}\n', encoding="utf-8")
        manifest_path = self.copy / "evidence" / "bundle.json"
        manifest = read_json(manifest_path)
        before = manifest["files"]["must_cover.json"]
        after = file_sha256(changed)
        manifest["files"]["must_cover.json"] = after
        manifest["rewires"] = [
            {
                "file": "must_cover.json",
                "before": before,
                "after": after,
                "fields": ["api_type"],
                "at": "2026-08-20T12:00:00+00:00",
            }
        ]
        write_json(manifest_path, manifest)
        done = self.run_check()
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(1, len(self.intake()["evidence"]["rewires"]))

    def test_help_is_nonempty_and_exits_zero(self):
        done = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertTrue(done.stdout.strip())


if __name__ == "__main__":
    unittest.main()
