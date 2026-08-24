"""交接契约的形状、消费面、派生视图与两侧往返回归测试。"""

import copy
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _paths import REFERENCES, SCRIPTS, add_tests_to_path, is_nested_source

if not is_nested_source():
    raise unittest.SkipTest("交接契约往返测试需要生成侧与验收侧量具同时存在")

HANDOFF_PATH = REFERENCES / "handoff.md"
HANDOFF_SEAL_PATH = REFERENCES / "handoff-seal.md"
SEAL_SCRIPT = SCRIPTS / "seal_bundle.py"
CHECK_SCRIPT = SCRIPTS / "check_bundle.py"
CHECK_KEYS = {"integrity", "task_doc", "atk_version", "interface"}
INTERFACE_ACCESS = re.compile(
    r"\binterface(?:\.get\(\s*['\"]([^'\"]+)['\"]|"
    r"\[\s*['\"]([^'\"]+)['\"]\s*\])"
)
CONTRACT_FINGERPRINTS = {
    1: "9753a1f8dfb9f49c8b65e19500e42490b271ef1bbc377d4079bd9d09d2a139c6",
}

sys.path.insert(0, str(SCRIPTS))
add_tests_to_path()

import _contracts  # noqa: E402
import _handoff_contract  # noqa: E402
import check_bundle  # noqa: E402
import render_views  # noqa: E402
import seal_bundle  # noqa: E402
from _case_utils import file_sha256  # noqa: E402
from _decl_fixture import make_bundle, read_json, write_json  # noqa: E402


def write_contract(path, payload):
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def normalized_sha256(payload):
    normalized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def section_fence(text, title):
    section = text.split(f"## {title}\n", 1)[1]
    section = section.split("\n## ", 1)[0]
    match = re.search(r"^```[^\n]*\n(.*?)\n```$", section, re.MULTILINE | re.DOTALL)
    if not match:
        raise AssertionError(f"{title} 后没有围栏代码块")
    return match.group(1)


def prepare_roundtrip(root):
    original = root / "original"
    copy_path = root / "copy"
    task_doc = root / "task.md"
    original.mkdir()
    task_doc.write_text("# Median 算子任务书\n", encoding="utf-8")
    make_bundle(original)
    interface_path = original / "evidence" / "interface.json"
    interface = read_json(interface_path)
    interface["task_doc"] = {
        "name": task_doc.name,
        "sha256": file_sha256(task_doc),
    }
    write_json(interface_path, interface)
    sealed = subprocess.run(
        [sys.executable, str(SEAL_SCRIPT), "-C", str(original)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if sealed.returncode == 0:
        shutil.copytree(original, copy_path)
    return original, copy_path, task_doc, sealed


def run_check(copy_path, task_doc):
    return subprocess.run(
        [
            sys.executable,
            str(CHECK_SCRIPT),
            "-C",
            str(copy_path),
            "--task-doc",
            str(task_doc),
            "--env",
            str(copy_path / "evidence" / "env.json"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )


class HandoffContractShapeTest(unittest.TestCase):
    def test_checked_in_contract_loads(self):
        contract = _handoff_contract.load()
        self.assertEqual(1, contract["contract_version"])
        self.assertEqual(1, contract["schema_version"])

    def test_missing_interface_fields_is_rejected(self):
        contract = copy.deepcopy(_handoff_contract.load())
        del contract["manifest"]["interface_fields"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "contract.json"
            write_contract(path, contract)
            with self.assertRaisesRegex(
                _handoff_contract.HandoffContractError,
                "interface_fields",
            ):
                _handoff_contract.load(path)

    def test_wrong_top_level_type_is_rejected(self):
        contract = copy.deepcopy(_handoff_contract.load())
        contract["schema_version"] = "1"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "contract.json"
            write_contract(path, contract)
            with self.assertRaisesRegex(
                _handoff_contract.HandoffContractError,
                "schema_version",
            ):
                _handoff_contract.load(path)

    def test_non_string_interface_field_is_rejected(self):
        contract = copy.deepcopy(_handoff_contract.load())
        contract["manifest"]["interface_fields"][-1] = 7
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "contract.json"
            write_contract(path, contract)
            with self.assertRaisesRegex(
                _handoff_contract.HandoffContractError,
                "interface_fields",
            ):
                _handoff_contract.load(path)


class DerivedContractTest(unittest.TestCase):
    def test_both_sides_derive_their_constants_from_the_contract(self):
        contract = _handoff_contract.load()
        manifest = contract["manifest"]
        self.assertEqual(contract["schema_version"], seal_bundle.SCHEMA_VERSION)
        self.assertEqual(tuple(manifest["excluded"]), seal_bundle.EXCLUDED)
        self.assertEqual(tuple(manifest["ignored_dirs"]), seal_bundle.IGNORED_DIRS)
        self.assertEqual(
            tuple(manifest["interface_fields"]),
            seal_bundle.INTERFACE_FIELDS,
        )
        self.assertEqual(
            (contract["schema_version"],),
            check_bundle.SUPPORTED_SCHEMA_VERSIONS,
        )
        self.assertEqual(tuple(manifest["excluded"]), check_bundle.EXPECTED_EXCLUDED)
        self.assertEqual(
            tuple(manifest["ignored_dirs"]),
            check_bundle.EXPECTED_IGNORED_DIRS,
        )
        self.assertEqual(
            _handoff_contract.manifest_shapes(),
            check_bundle.EXPECTED_SHAPES,
        )
        self.assertIn(
            seal_bundle.SCHEMA_VERSION,
            check_bundle.SUPPORTED_SCHEMA_VERSIONS,
        )

    def test_interface_consumers_only_read_declared_fields(self):
        contract = _handoff_contract.load()
        fields = set(contract["manifest"]["interface_fields"])
        readers = {
            reader
            for scripts in contract["consumers"]["evidence/interface.json"].values()
            for reader in scripts
        }
        for reader in sorted(readers):
            source = (SCRIPTS / reader).read_text(encoding="utf-8")
            accessed = {
                left or right
                for left, right in INTERFACE_ACCESS.findall(source)
            }
            with self.subTest(reader=reader):
                self.assertLessEqual(accessed, fields)

    def test_every_acceptance_interface_reader_is_registered(self):
        contract = _handoff_contract.load()
        registered = {
            reader
            for scripts in contract["consumers"]["evidence/interface.json"].values()
            for reader in scripts
        }
        ownership = _contracts.load()["scripts"]
        discovered = set()
        for name, spec in ownership.items():
            if spec["skill"] != "acceptance" or name == "check_bundle.py":
                continue
            source = (SCRIPTS / name).read_text(encoding="utf-8")
            if "evidence/interface.json" in source or "interface.get(" in source:
                discovered.add(name)
        self.assertEqual(discovered, registered)

    def test_tree_contract_and_handoff_view_are_bidirectionally_equal(self):
        contract = _handoff_contract.load()
        rendered = _handoff_contract.render_tree(contract)
        checked_in = section_fence(HANDOFF_PATH.read_text(encoding="utf-8"), "交接包目录")
        self.assertEqual(rendered, checked_in)
        entries = contract["tree"]["entries"]
        self.assertEqual(len(entries) + 1, len(rendered.splitlines()))
        for entry in entries:
            label = entry["path"].removeprefix(entry.get("parent") or "")
            if entry["conditional"]:
                label = f"[{label}]"
            with self.subTest(path=entry["path"]):
                self.assertEqual(1, rendered.count(label))

    def test_manifest_example_is_the_checked_in_fenced_view(self):
        rendered = _handoff_contract.render_manifest_example()
        checked_in = section_fence(
            HANDOFF_SEAL_PATH.read_text(encoding="utf-8"), "封印清单")
        self.assertEqual(rendered, checked_in)
        example = json.loads(rendered)
        self.assertEqual(
            _handoff_contract.interface_fields(),
            tuple(example["interface"]),
        )

    def test_contract_version_and_normalized_fingerprint_are_locked(self):
        contract = _handoff_contract.load()
        version = contract["contract_version"]
        self.assertIn(
            version,
            CONTRACT_FINGERPRINTS,
            "改契约必须升 contract_version 并更新此指纹",
        )
        self.assertEqual(
            CONTRACT_FINGERPRINTS[version],
            normalized_sha256(contract),
            "改契约必须升 contract_version 并更新此指纹",
        )

    def test_exit_gate_report_names_match_seal_labels(self):
        text = HANDOFF_SEAL_PATH.read_text(encoding="utf-8")
        section = text.split("## 出口门字段表\n", 1)[1].split("\n## ", 1)[0]
        first_cells = []
        for line in section.splitlines():
            if not line.startswith("|") or line.startswith("| ---"):
                continue
            value = line.split("|", 2)[1].strip()
            if value and value not in {"报告", "冻结常量输入", "签名契约"}:
                first_cells.append(value)
        self.assertEqual(list(seal_bundle.REPORT_LABELS.values()), first_cells)


class FencedViewMechanicsTest(unittest.TestCase):
    def test_only_the_first_fence_after_the_named_heading_is_replaced(self):
        original = (
            "# 文档\n\n## 目标\n\n正文不动。\n\n```text\n旧内容\n```\n\n"
            "```json\n{\"later\": true}\n```\n\n## 下一节\n\n保持。\n"
        )
        updated = render_views.replace_fenced_view(original, "目标", "新内容")
        self.assertEqual("新内容", render_views.fenced_view_content(updated, "目标"))
        self.assertIn("正文不动。", updated)
        self.assertIn('```json\n{"later": true}\n```', updated)
        self.assertIn("## 下一节\n\n保持。", updated)

    def test_missing_named_heading_is_rejected(self):
        with self.assertRaisesRegex(render_views.ViewError, "不存在"):
            render_views.fenced_view_content("# 文档\n", "目标")


class HandoffRoundtripTest(unittest.TestCase):
    def test_seal_copy_and_intake_pass_with_seven_interface_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            original, copy_path, task_doc, sealed = prepare_roundtrip(Path(tmp))
            self.assertEqual(0, sealed.returncode, sealed.stderr)
            manifest = read_json(original / "evidence" / "bundle.json")
            self.assertEqual(
                _handoff_contract.interface_fields(),
                tuple(manifest["interface"]),
            )
            checked = run_check(copy_path, task_doc)
            self.assertEqual(0, checked.returncode, checked.stderr)
            intake = read_json(copy_path / "evidence" / "bundle_intake.json")
            self.assertEqual("pass", intake["verdict"])
            self.assertEqual(
                {
                    "verdict",
                    "bundle_sha256",
                    "checked_at",
                    "integrity",
                    "task_doc",
                    "atk_version",
                    "interface",
                    "evidence",
                },
                set(intake),
            )
            self.assertEqual(CHECK_KEYS, CHECK_KEYS & set(intake))

    def test_missing_mode_source_is_rejected_by_the_seal(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            make_bundle(work)
            path = work / "evidence" / "interface.json"
            interface = read_json(path)
            interface.pop("mode_source")
            write_json(path, interface)
            sealed = subprocess.run(
                [sys.executable, str(SEAL_SCRIPT), "-C", str(work)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(2, sealed.returncode, sealed.stderr)
            self.assertIn("mode_source", sealed.stderr)

    def test_missing_manifest_mode_source_is_rejected_by_intake(self):
        with tempfile.TemporaryDirectory() as tmp:
            original, copy_path, task_doc, sealed = prepare_roundtrip(Path(tmp))
            self.assertEqual(0, sealed.returncode, sealed.stderr)
            path = copy_path / "evidence" / "bundle.json"
            manifest = read_json(path)
            manifest["interface"].pop("mode_source")
            write_json(path, manifest)
            checked = run_check(copy_path, task_doc)
            self.assertEqual(2, checked.returncode, checked.stderr)
            report = read_json(copy_path / "evidence" / "bundle_intake.json")
            problems = report["integrity"]["evidence"]["manifest_problems"]
            self.assertTrue(any("mode_source" in problem for problem in problems))


if __name__ == "__main__":
    unittest.main()
