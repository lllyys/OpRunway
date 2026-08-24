"""接线字段的受控改写：用例语义未变才算数。

真机实测（roll，2026-08-15）：aclnn 适配器接线要改 YAML 重跑 atk case，
与「S2 后冻结」纪律冲突，skill 没给出口，agent 手工自证了十次。

这些用例锁的是判据本身——「只有接线字段变了」怎么判。
"""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from datetime import datetime
from pathlib import Path

from _paths import SKILL_ROOT, entry_pages

sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import rewire_adapter as rewire  # noqa: E402

SCRIPT = SKILL_ROOT / "scripts" / "rewire_adapter.py"
CHECK_BUNDLE = SKILL_ROOT / "scripts" / "check_bundle.py"

try:
    import yaml  # noqa: F401
except ImportError:
    HAS_PYYAML = False
else:
    HAS_PYYAML = True


def case(case_id, dtype="fp32", **wiring):
    return dict({"id": case_id,
                 "inputs": [{"name": "x", "type": "tensor", "dtype": dtype,
                             "shape": [4], "range_values": [-5, 5]}]},
                **wiring)


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


class ParseSetTest(unittest.TestCase):
    def test_parses_key_value(self):
        self.assertEqual({"aclnn_api_type": "my_exec"},
                         rewire.parse_set(["aclnn_api_type=my_exec"]))

    def test_rejects_non_wiring_keys(self):
        # generate 换了生成器就换了用例本身，那不是接线改写，是重新设计。
        with self.assertRaises(ValueError) as caught:
            rewire.parse_set(["generate=other"])
        self.assertIn("generate", str(caught.exception))

    def test_rejects_missing_value(self):
        with self.assertRaises(ValueError):
            rewire.parse_set(["aclnn_api_type"])


class DiffCasesTest(unittest.TestCase):
    def test_wiring_only_change_is_clean(self):
        old = [case(0), case(1)]
        new = [case(0, aclnn_api_type="my_exec"),
               case(1, aclnn_api_type="my_exec")]
        self.assertEqual([], rewire.diff_cases(old, new))

    def test_changed_dtype_is_reported(self):
        problems = rewire.diff_cases([case(0)], [case(0, dtype="fp16")])
        self.assertEqual(1, len(problems))
        self.assertIn("0", problems[0])

    def test_changed_case_count_is_reported(self):
        problems = rewire.diff_cases([case(0), case(1)], [case(0)])
        self.assertTrue(problems)
        self.assertIn("条数", problems[0])

    def test_changed_case_ids_are_reported(self):
        problems = rewire.diff_cases([case(0)], [case(9)])
        self.assertTrue(problems)

    def test_strip_wiring_does_not_mutate_the_input(self):
        original = case(0, aclnn_api_type="my_exec")
        rewire.strip_wiring(original)
        self.assertIn("aclnn_api_type", original)


class UpdateBundleTest(unittest.TestCase):
    def test_updates_changed_and_new_files_and_appends_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = root / "evidence"
            result = root / "result" / "x" / "json"
            evidence.mkdir()
            result.mkdir(parents=True)

            yaml_path = root / "x.yaml"
            case_path = result / "all_x.json"
            frozen_path = evidence / "frozen_inputs.json"
            added_path = result / "generation.log"
            record_path = evidence / "rewire.json"
            backup_path = root / "x.yaml.pre_rewire"
            yaml_path.write_text("name: x\n", encoding="utf-8")
            write_json(case_path, [case(0)])
            write_json(frozen_path, {"case_json_sha256": "old"})
            record_path.write_text("{}\n", encoding="utf-8")
            backup_path.write_text("name: x\n", encoding="utf-8")

            yaml_digest = rewire.file_sha256(yaml_path)
            old_case_digest = rewire.file_sha256(case_path)
            old_frozen_digest = rewire.file_sha256(frozen_path)
            bundle_path = evidence / "bundle.json"
            write_json(
                bundle_path,
                {
                    "files": {
                        "x.yaml": yaml_digest,
                        "result/x/json/all_x.json": old_case_digest,
                        "evidence/frozen_inputs.json": old_frozen_digest,
                    }
                },
            )

            write_json(case_path, [case(0, aclnn_api_type="executor")])
            write_json(frozen_path, {"case_json_sha256": "new"})
            added_path.write_text("regenerated\n", encoding="utf-8")
            record = {
                "at": "2026-08-20T12:00:00+00:00",
                "yaml": str(yaml_path),
                "patched": {"aclnn_api_type": {"from": None, "to": "executor"}},
                "record": str(record_path),
                "backup": str(backup_path),
            }
            touched = [yaml_path, case_path, frozen_path, added_path]

            rewire.update_bundle(bundle_path, root, touched, record)
            manifest = json.loads(bundle_path.read_text(encoding="utf-8"))
            self.assertEqual(yaml_digest, manifest["files"]["x.yaml"])
            self.assertEqual(
                rewire.file_sha256(case_path),
                manifest["files"]["result/x/json/all_x.json"],
            )
            self.assertEqual(
                rewire.file_sha256(frozen_path),
                manifest["files"]["evidence/frozen_inputs.json"],
            )
            self.assertEqual(
                rewire.file_sha256(added_path),
                manifest["files"]["result/x/json/generation.log"],
            )
            self.assertEqual(1, len(manifest["rewires"]))
            entry = manifest["rewires"][0]
            self.assertEqual("x.yaml", entry["yaml"])
            self.assertEqual(["aclnn_api_type"], entry["fields"])
            self.assertEqual("evidence/rewire.json", entry["record"])
            self.assertEqual("x.yaml.pre_rewire", entry["backup"])
            self.assertIsNotNone(datetime.fromisoformat(entry["at"]).utcoffset())
            self.assertEqual(3, len(entry["changes"]))
            changes = {item["file"]: item for item in entry["changes"]}
            self.assertEqual(
                old_case_digest,
                changes["result/x/json/all_x.json"]["before"],
            )
            self.assertEqual(
                old_frozen_digest,
                changes["evidence/frozen_inputs.json"]["before"],
            )
            self.assertIsNone(changes["result/x/json/generation.log"]["before"])
            self.assertNotIn("x.yaml", changes)

            rewire.update_bundle(bundle_path, root, touched, record)
            manifest = json.loads(bundle_path.read_text(encoding="utf-8"))
            self.assertEqual(2, len(manifest["rewires"]))

    def test_manifest_without_files_is_rejected_clearly(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle_path = root / "evidence" / "bundle.json"
            write_json(bundle_path, {"schema_version": 1})
            with self.assertRaisesRegex(ValueError, "files"):
                rewire.update_bundle(bundle_path, root, [], {})


@unittest.skipUnless(HAS_PYYAML, "当前解释器没有 PyYAML")
class BundleCliTest(unittest.TestCase):
    def make_fixture(self, temporary, new_cases, *, sealed=True):
        base = Path(temporary)
        root = base / "bundle"
        evidence = root / "evidence"
        case_path = root / "result" / "x" / "json" / "all_x.json"
        yaml_path = root / "x.yaml"
        task_doc = base / "task.md"
        env_path = evidence / "env.json"
        source_path = base / "new_cases.json"
        stub_path = base / "atk-stub"
        output_path = evidence / "rewire.json"

        evidence.mkdir(parents=True)
        case_path.parent.mkdir(parents=True)
        yaml_path.write_text(
            "name: x\naclnn_api_type: old_executor\n",
            encoding="utf-8",
        )
        write_json(case_path, [case(0)])
        write_json(source_path, new_cases)
        task_doc.write_text("# X 算子任务书\n", encoding="utf-8")
        write_json(env_path, {"fingerprint": {"atk": "1.2.3"}})
        stub_path.write_text(
            textwrap.dedent(
                f"""\
                #!{sys.executable}
                import shutil
                import sys
                from pathlib import Path

                if sys.argv[1:3] != ["case", "-f"]:
                    raise SystemExit(9)
                yaml_path = Path(sys.argv[3])
                target = (Path.cwd() / "result" / yaml_path.stem / "json"
                          / f"all_{{yaml_path.stem}}.json")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile({str(source_path)!r}, target)
                print(f"save case json file: {{target.resolve()}}")
                """
            ),
            encoding="utf-8",
        )
        stub_path.chmod(0o755)

        bundle_path = evidence / "bundle.json"
        if sealed:
            write_json(
                bundle_path,
                {
                    "schema_version": 1,
                    "operator": "x",
                    "sealed_at": "2026-08-20T10:00:00+00:00",
                    "task_doc": {
                        "name": task_doc.name,
                        "sha256": rewire.file_sha256(task_doc),
                    },
                    "generator": {
                        "skill": "repo-task-case-gen",
                        "phase_supported": "仅 Phase A",
                    },
                    "atk": {"version": "1.2.3", "python": sys.executable},
                    "interface": {
                        "interface_mode": "pytorch",
                        "candidate_symbol": "x",
                        "baseline_api": "torch.x",
                        "baseline_kind": "torch",
                    },
                    "facets": [],
                    "files": {
                        "x.yaml": rewire.file_sha256(yaml_path),
                        "result/x/json/all_x.json": rewire.file_sha256(case_path),
                    },
                    "excluded": [
                        "evidence/timeline.jsonl",
                        "evidence/repro.sh",
                        "evidence/bundle.json",
                        "evidence/env.json",
                        "evidence/env.sh",
                    ],
                    "ignored_dirs": ["__pycache__"],
                },
            )

        return {
            "root": root,
            "yaml": yaml_path,
            "case": case_path,
            "task_doc": task_doc,
            "env": env_path,
            "stub": stub_path,
            "output": output_path,
            "bundle": bundle_path,
        }

    def run_rewire(self, fixture, *, pass_bundle=True):
        command = [
            sys.executable,
            str(SCRIPT),
            "-f",
            str(fixture["yaml"]),
            "-j",
            str(fixture["case"]),
            "--atk-cli",
            str(fixture["stub"]),
            "--set",
            "aclnn_api_type=new_executor",
            "-o",
            str(fixture["output"]),
        ]
        if pass_bundle:
            command.extend(["--bundle", str(fixture["bundle"])])
        return subprocess.run(
            command,
            cwd=fixture["root"],
            capture_output=True,
            text=True,
            timeout=30,
            env=os.environ.copy(),
        )

    def test_wiring_only_rewrite_updates_bundle_and_intake_passes(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.make_fixture(
                temporary,
                [case(0, aclnn_api_type="new_executor")],
            )
            done = self.run_rewire(fixture)
            self.assertEqual(0, done.returncode, done.stderr)
            manifest = json.loads(fixture["bundle"].read_text(encoding="utf-8"))
            self.assertEqual(
                rewire.file_sha256(fixture["yaml"]),
                manifest["files"]["x.yaml"],
            )
            self.assertEqual(
                rewire.file_sha256(fixture["case"]),
                manifest["files"]["result/x/json/all_x.json"],
            )
            self.assertEqual(1, len(manifest["rewires"]))

            checked = subprocess.run(
                [
                    sys.executable,
                    str(CHECK_BUNDLE),
                    "-C",
                    str(fixture["root"]),
                    "--task-doc",
                    str(fixture["task_doc"]),
                    "--env",
                    str(fixture["env"]),
                ],
                cwd=fixture["root"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(0, checked.returncode, checked.stderr)

    def test_semantic_change_leaves_bundle_byte_for_byte_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.make_fixture(
                temporary,
                [case(0, dtype="fp16", aclnn_api_type="new_executor")],
            )
            before = fixture["bundle"].read_bytes()
            done = self.run_rewire(fixture)
            self.assertEqual(2, done.returncode, done.stderr)
            self.assertEqual(before, fixture["bundle"].read_bytes())

    def test_missing_default_bundle_uses_unsealed_compatibility_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.make_fixture(
                temporary,
                [case(0, aclnn_api_type="new_executor")],
                sealed=False,
            )
            done = self.run_rewire(fixture, pass_bundle=False)
            self.assertEqual(0, done.returncode, done.stderr)
            self.assertIn("未找到封印清单", done.stdout)
            self.assertTrue(fixture["output"].is_file())


class CliTest(unittest.TestCase):
    def test_non_wiring_key_exits_three(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "-f", "/nope.yaml",
             "-j", "/nope.json", "--atk-cli", "/nope",
             "--set", "generate=x", "-o", "/tmp/x.json"],
            capture_output=True, text=True)
        self.assertEqual(3, result.returncode)
        self.assertIn("generate", result.stderr)

    def test_missing_yaml_exits_three(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "-f", "/nope.yaml",
             "-j", "/nope.json", "--atk-cli", "/nope",
             "--set", "aclnn_api_type=x", "-o", "/tmp/x.json"],
            capture_output=True, text=True)
        self.assertEqual(3, result.returncode)


class DisclosureTest(unittest.TestCase):
    def test_skill_no_longer_disclaims_a_missing_script(self):
        # Plan B 写下这句话时脚本还不存在，附了免责说明。现在兑现了，
        # 免责说明留着就是假信息。
        paths = entry_pages()
        text = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        self.assertIn("rewire_adapter.py", text)
        self.assertNotIn("脚本未产出前", text)


if __name__ == "__main__":
    unittest.main()
