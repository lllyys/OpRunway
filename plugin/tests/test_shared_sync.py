"""两个平级验收 skill 的共享文件同步门禁。"""

import json
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parent.parent
CASE_GEN = PLUGIN_ROOT / "skill" / "repo-task-case-gen"
ATK_ACCEPT = PLUGIN_ROOT / "skill" / "repo-task-atk-accept"
SIDE_SPECIFIC_SAME_RELATIVE = {
    Path("SKILL.md"),
    Path("CLAUDE.md"),
}


def inventory(root):
    """返回需要跨目录防漂移的相对文件路径。"""
    return {
        path.relative_to(root)
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
        and path.relative_to(root) not in SIDE_SPECIFIC_SAME_RELATIVE
    }


def copy_hint(source, destination):
    return f"cp {source} {destination}"


class SharedSyncTest(unittest.TestCase):
    def test_side_specific_same_relative_files_exist_on_both_sides(self):
        failures = []
        for relative in sorted(SIDE_SPECIFIC_SAME_RELATIVE):
            for root in (CASE_GEN, ATK_ACCEPT):
                path = root / relative
                if not path.is_file():
                    failures.append(str(path))
        self.assertEqual(
            [], failures,
            "设计内不同的同相对路径文件必须两侧都存在：\n"
            + "\n".join(failures),
        )

    def test_same_relative_files_are_byte_identical(self):
        common = inventory(CASE_GEN) & inventory(ATK_ACCEPT)
        self.assertTrue(common, "两个 skill 没有任何同相对路径文件")
        failures = []
        for relative in sorted(common):
            source = CASE_GEN / relative
            destination = ATK_ACCEPT / relative
            if source.read_bytes() != destination.read_bytes():
                failures.append(copy_hint(source, destination))
        self.assertEqual(
            [], failures,
            "共享文件逐字节不同；确认生成侧为正确版本后执行：\n"
            + "\n".join(failures),
        )

    def test_every_shared_skeleton_entry_exists_on_both_sides(self):
        contracts = []
        for root in (CASE_GEN, ATK_ACCEPT):
            path = root / "references" / "artifact-contracts.json"
            contracts.append(json.loads(path.read_text(encoding="utf-8")))

        failures = []
        for table, directory in (("scripts", "scripts"),
                                 ("references", "references")):
            shared = {
                name
                for contract in contracts
                for name, spec in contract[table].items()
                if spec.get("skill") == "shared"
            }
            for name in sorted(shared):
                case_path = CASE_GEN / directory / name
                accept_path = ATK_ACCEPT / directory / name
                if not case_path.is_file():
                    failures.append(copy_hint(accept_path, case_path))
                if not accept_path.is_file():
                    failures.append(copy_hint(case_path, accept_path))
        self.assertEqual(
            [], failures,
            "骨架登记为 shared 的文件没有同时存在；确认来源后执行：\n"
            + "\n".join(failures),
        )

    def test_artifact_contracts_are_byte_identical(self):
        source = CASE_GEN / "references" / "artifact-contracts.json"
        destination = ATK_ACCEPT / "references" / "artifact-contracts.json"
        self.assertEqual(
            source.read_bytes(),
            destination.read_bytes(),
            "两份骨架不同步；确认生成侧为正确版本后执行：\n"
            + copy_hint(source, destination),
        )


if __name__ == "__main__":
    unittest.main()
