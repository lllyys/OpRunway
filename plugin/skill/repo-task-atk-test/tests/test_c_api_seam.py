"""c_api 生成侧与验收侧的拆分边界。"""

import ast
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
EXECUTOR = SKILL_ROOT / "assets" / "example" / "c_api_executor.py"
LOAD_PREFIX = "[c_api_executor] loaded_library="


def _imports(path):
    """静态读取模块导入，不执行被检文件。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level
            imported.add(prefix + (node.module or ""))
    return imported


class CApiSeamTest(unittest.TestCase):
    def test_acceptance_side_does_not_import_generation_side(self):
        forbidden = {"_c_api_signature", "align_signatures", "derive_interface"}
        for name in ("check_c_api_binding.py", "make_c_api_build.py"):
            with self.subTest(script=name):
                imported = {module.lstrip(".").split(".", 1)[0]
                            for module in _imports(SCRIPTS / name)}
                self.assertFalse(
                    imported & forbidden,
                    f"{name} 跨过拆分边界导入了生成侧模块",
                )

    def test_executor_does_not_import_skill_scripts(self):
        script_modules = {path.stem for path in SCRIPTS.glob("*.py")}
        imported = {module.lstrip(".").split(".", 1)[0]
                    for module in _imports(EXECUTOR)}
        self.assertFalse(
            imported & (script_modules | {"scripts"}),
            "c_api 执行器不得导入 skill 的 scripts 目录",
        )

    def test_generation_side_does_not_import_acceptance_side(self):
        forbidden = {"check_c_api_binding", "make_c_api_build"}
        imported = {module.lstrip(".").split(".", 1)[0]
                    for module in _imports(SCRIPTS / "_c_api_signature.py")}
        self.assertFalse(
            imported & forbidden,
            "_c_api_signature.py 跨过拆分边界导入了验收侧模块",
        )

    def test_executor_load_prefix_is_repeated_identically_on_both_sides(self):
        for path in (SCRIPTS / "check_c_api_binding.py", EXECUTOR):
            with self.subTest(path=path.name):
                self.assertIn(LOAD_PREFIX, path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
