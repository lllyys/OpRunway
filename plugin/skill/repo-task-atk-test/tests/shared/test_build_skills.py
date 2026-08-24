"""嵌套源展开成独立 skill 后的自足性测试。"""

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from _paths import REFERENCES, SCRIPTS, SKILL_ROOT

SCRIPT = SCRIPTS / "build_skills.py"
CONTRACTS = REFERENCES / "artifact-contracts.json"
PRODUCTS = {
    "case-gen": "repo-task-case-gen",
    "acceptance": "repo-task-atk-accept",
}
TEST_GROUPS = {"case-gen": "case_gen", "acceptance": "acceptance"}
LINK = re.compile(r"\[[^]]*\]\(([^)]+)\)")
PARENT_SEGMENT = re.compile(r"(?<!\.)\.\./")


def run_builder(root, out, *args):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "build_skills.py"),
         "--out", str(out), *args],
        cwd=root,
        capture_output=True,
        text=True,
    )


def source_copy(destination):
    return Path(shutil.copytree(
        SKILL_ROOT,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "dist"),
    ))


def tree_files(root):
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and ".pytest_cache" not in path.parts
    }


def local_imports(path, module_names):
    """独立解析本仓模块 import，不复用展开脚本的闭包检查。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module.split(".")[0]]
        for module in modules:
            if module in module_names:
                yield module


def failed_set(output):
    """把源目录与平铺产物中的 pytest failed node id 归一成同一口径。"""
    found = set()
    pattern = re.compile(r"^(?:FAILED|SUBFAILED(?:\([^)]*\))?)\s+(\S+)")
    for line in output.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        parts = match.group(1).split("::")
        parts[0] = Path(parts[0]).name
        found.add("::".join(parts))
    return found


@unittest.skipUnless(
    (SKILL_ROOT / "case-gen" / "SKILL.md").exists(),
    "展开测试只在嵌套源中运行，独立产物里不递归展开",
)
class BuildSkillsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.out = Path(cls.temporary.name) / "dist"
        cls.result = run_builder(SKILL_ROOT, cls.out)
        if cls.result.returncode != 0:
            raise AssertionError(
                f"初始展开失败（{cls.result.returncode}）：\n"
                f"{cls.result.stdout}{cls.result.stderr}"
            )
        cls.contracts = json.loads(CONTRACTS.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def product(self, side):
        return self.out / PRODUCTS[side]

    def test_skill_page_only_rewrites_parent_runtime_paths(self):
        for side in PRODUCTS:
            with self.subTest(side=side):
                source = (SKILL_ROOT / side / "SKILL.md").read_text(encoding="utf-8")
                expected = source.replace("../references/", "references/")
                expected = expected.replace("../scripts/", "scripts/")
                actual = (self.product(side) / "SKILL.md").read_text(encoding="utf-8")
                self.assertEqual(expected, actual)

    def test_runtime_markdown_has_no_parent_paths_and_links_resolve(self):
        for side in PRODUCTS:
            product = self.product(side)
            markdown = [product / "SKILL.md", *sorted(
                (product / "references").glob("*.md"))]
            for path in markdown:
                with self.subTest(side=side, path=path.name):
                    text = path.read_text(encoding="utf-8")
                    self.assertIsNone(PARENT_SEGMENT.search(text))
            skill_text = (product / "SKILL.md").read_text(encoding="utf-8")
            for target in LINK.findall(skill_text):
                target = target.strip().split(maxsplit=1)[0].strip("<>")
                if target.startswith(("#", "http://", "https://", "mailto:")):
                    continue
                link_path = target.split("#", 1)[0]
                with self.subTest(side=side, link=target):
                    self.assertTrue((product / link_path).exists(), target)

    def test_scripts_and_references_exactly_follow_the_skeleton(self):
        for side in PRODUCTS:
            expected_scripts = {
                name for name, spec in self.contracts["scripts"].items()
                if spec["skill"] in {side, "shared"}
            }
            expected_references = {
                name for name, spec in self.contracts["references"].items()
                if spec["skill"] in {side, "shared"}
            }
            product = self.product(side)
            with self.subTest(side=side, kind="scripts"):
                self.assertEqual(
                    expected_scripts,
                    {path.name for path in (product / "scripts").iterdir()
                     if path.is_file()},
                )
            with self.subTest(side=side, kind="references"):
                self.assertEqual(
                    expected_references,
                    {path.name for path in (product / "references").iterdir()
                     if path.is_file()},
                )

    def test_every_local_import_is_present_in_each_product(self):
        source_modules = {
            path.stem for path in (SKILL_ROOT / "scripts").glob("*.py")
        }
        for side in PRODUCTS:
            scripts = self.product(side) / "scripts"
            missing = []
            for path in scripts.glob("*.py"):
                for module in local_imports(path, source_modules):
                    if not (scripts / f"{module}.py").exists():
                        missing.append(f"{path.name} -> {module}.py")
            with self.subTest(side=side):
                self.assertEqual([], missing)

    def test_products_exclude_the_other_sides_private_files(self):
        for side in PRODUCTS:
            other = next(item for item in PRODUCTS if item != side)
            private_scripts = {
                name for name, spec in self.contracts["scripts"].items()
                if spec["skill"] == other
            }
            private_references = {
                name for name, spec in self.contracts["references"].items()
                if spec["skill"] == other
            }
            product = self.product(side)
            actual_scripts = {path.name for path in (product / "scripts").iterdir()}
            actual_references = {
                path.name for path in (product / "references").iterdir()
            }
            with self.subTest(side=side):
                self.assertTrue(private_scripts.isdisjoint(actual_scripts))
                self.assertTrue(private_references.isdisjoint(actual_references))

    def test_only_case_gen_product_carries_runtime_templates(self):
        source_assets = tree_files(SKILL_ROOT / "assets")
        self.assertTrue(source_assets)
        case_assets = self.product("case-gen") / "assets"
        self.assertEqual(source_assets, tree_files(case_assets))
        self.assertFalse((self.product("acceptance") / "assets").exists())

    def test_manifest_describes_and_hashes_every_materialized_file(self):
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=SKILL_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        for side in PRODUCTS:
            product = self.product(side)
            manifest = json.loads(
                (product / "MANIFEST.json").read_text(encoding="utf-8"))
            with self.subTest(side=side, field="metadata"):
                self.assertEqual(side, manifest["side"])
                self.assertEqual(commit, manifest["source_commit"])
                generated = datetime.fromisoformat(manifest["generated_at"])
                self.assertIsNotNone(generated.utcoffset())
            actual_files = tree_files(product) - {"MANIFEST.json"}
            self.assertEqual(actual_files, set(manifest["files"]))
            for relative, expected in manifest["files"].items():
                with self.subTest(side=side, file=relative):
                    actual = hashlib.sha256((product / relative).read_bytes()).hexdigest()
                    self.assertEqual(expected, actual)

    def test_tests_are_materialized_only_for_the_split_layout(self):
        split_layout = (SKILL_ROOT / "tests" / "shared").is_dir()
        for side in PRODUCTS:
            product = self.product(side)
            manifest = json.loads(
                (product / "MANIFEST.json").read_text(encoding="utf-8"))
            with self.subTest(side=side):
                if not split_layout:
                    self.assertEqual("absent", manifest["tests"])
                    self.assertFalse((product / "tests").exists())
                    continue
                self.assertEqual("present", manifest["tests"])
                group = SKILL_ROOT / "tests" / TEST_GROUPS[side]
                shared = SKILL_ROOT / "tests" / "shared"
                expected = {
                    path.name for path in (SKILL_ROOT / "tests").iterdir()
                    if path.is_file()
                }
                expected.update(
                    path.name for path in group.rglob("*")
                    if (path.is_file() and path.name != "__init__.py"
                        and "__pycache__" not in path.parts
                        and ".pytest_cache" not in path.parts))
                expected.update(
                    path.name for path in shared.rglob("*")
                    if (path.is_file() and path.name != "__init__.py"
                        and "__pycache__" not in path.parts
                        and ".pytest_cache" not in path.parts))
                actual = {path.name for path in (product / "tests").rglob("*")
                          if (path.is_file()
                              and "__pycache__" not in path.parts
                              and ".pytest_cache" not in path.parts)}
                self.assertEqual(expected, actual)

    def test_only_root_initializer_is_materialized(self):
        root_initializer = SKILL_ROOT / "tests" / "__init__.py"
        self.assertTrue(root_initializer.is_file())
        for side in PRODUCTS:
            product_tests = self.product(side) / "tests"
            initializers = list(product_tests.rglob("__init__.py"))
            with self.subTest(side=side):
                self.assertEqual([product_tests / "__init__.py"], initializers)
                self.assertEqual(
                    root_initializer.read_bytes(),
                    initializers[0].read_bytes(),
                )

    def test_materialized_tests_keep_the_same_failed_set(self):
        if not (SKILL_ROOT / "tests" / "shared").is_dir():
            self.skipTest("S9 测试分侧布局尚未合并，产物没有可运行的 tests/")
        for side in PRODUCTS:
            source_env = os.environ.copy()
            source_env["OPRUNWAY_TEST_SIDE"] = side
            source = subprocess.run(
                [sys.executable, "-m", "pytest",
                 str(SKILL_ROOT / "tests" / TEST_GROUPS[side]),
                 str(SKILL_ROOT / "tests" / "shared"),
                 "-q", "--import-mode=importlib",
                 f"--ignore={Path(__file__).resolve()}"],
                cwd=SKILL_ROOT,
                capture_output=True,
                text=True,
                env=source_env,
            )
            product_env = os.environ.copy()
            product_env.pop("OPRUNWAY_TEST_SIDE", None)
            product = subprocess.run(
                [sys.executable, "-m", "pytest", "tests/", "-q",
                 "--import-mode=importlib"],
                cwd=self.product(side),
                capture_output=True,
                text=True,
                env=product_env,
            )
            with self.subTest(side=side):
                self.assertEqual(
                    failed_set(source.stdout + source.stderr),
                    failed_set(product.stdout + product.stderr),
                    product.stdout + product.stderr,
                )

    def test_missing_shared_import_exits_two_and_names_the_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            copied = source_copy(Path(tmp) / "source")
            contracts_path = copied / "references" / "artifact-contracts.json"
            contracts = json.loads(contracts_path.read_text(encoding="utf-8"))
            contracts["scripts"]["_case_utils.py"]["skill"] = "acceptance"
            contracts_path.write_text(
                json.dumps(contracts, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            result = run_builder(copied, Path(tmp) / "out", "--side", "case-gen")
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("_case_utils.py", result.stderr)

    def test_missing_registered_source_exits_two_and_names_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            copied = source_copy(Path(tmp) / "source")
            missing = copied / "references" / "case-design.md"
            missing.unlink()
            result = run_builder(copied, Path(tmp) / "out", "--side", "case-gen")
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("case-design.md", result.stderr)

    def test_non_initializer_name_collision_still_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            copied = source_copy(Path(tmp) / "source")
            name = "test_flattening_collision.py"
            (copied / "tests" / "case_gen" / name).write_text(
                "# case-gen\n", encoding="utf-8")
            (copied / "tests" / "shared" / name).write_text(
                "# shared\n", encoding="utf-8")
            result = run_builder(copied, Path(tmp) / "out", "--side", "case-gen")
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn(name, result.stderr)

    def test_unusable_skeleton_exits_three(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            scripts = root / "scripts"
            scripts.mkdir(parents=True)
            shutil.copy2(SCRIPT, scripts / SCRIPT.name)
            result = run_builder(root, Path(tmp) / "out")
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        self.assertIn("骨架不可用", result.stderr)

    def test_side_build_cleans_only_the_selected_product(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            first = run_builder(SKILL_ROOT, out)
            self.assertEqual(0, first.returncode, first.stdout + first.stderr)
            stale = out / PRODUCTS["case-gen"] / "stale.txt"
            survivor = out / PRODUCTS["acceptance"] / "survivor.txt"
            stale.write_text("旧文件", encoding="utf-8")
            survivor.write_text("保留", encoding="utf-8")
            second = run_builder(SKILL_ROOT, out, "--side", "case-gen")
            self.assertEqual(0, second.returncode, second.stdout + second.stderr)
            self.assertFalse(stale.exists())
            self.assertEqual("保留", survivor.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
