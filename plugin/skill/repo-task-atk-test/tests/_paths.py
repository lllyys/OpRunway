"""测试唯一的路径解析入口，兼容嵌套源与按侧展开的产物。"""

import re
import sys
from pathlib import Path


SIDE_NAMES = {
    "case-gen": "repo-task-case-gen",
    "acceptance": "repo-task-atk-accept",
}
ENTRY_PAGES = frozenset({
    "SKILL.md",
    "case-gen/SKILL.md",
    "acceptance/SKILL.md",
})


def skill_root(start=None):
    """向上寻找带契约骨架的 skill 根目录。"""
    path = Path(start or __file__).resolve()
    if path.is_file() or path.suffix:
        path = path.parent
    for candidate in (path, *path.parents):
        if (candidate / "references" / "artifact-contracts.json").is_file():
            return candidate
    raise FileNotFoundError(
        f"从 {path} 向上找不到 references/artifact-contracts.json")


SKILL_ROOT = skill_root()
SCRIPTS = SKILL_ROOT / "scripts"
REFERENCES = SKILL_ROOT / "references"
TESTS_ROOT = Path(__file__).resolve().parent


def is_nested_source(root=None):
    """返回当前目录是否含父入口和两个嵌套侧入口。"""
    root = Path(root or SKILL_ROOT)
    return all((root / side / "SKILL.md").is_file() for side in SIDE_NAMES)


def layout_side(root=None):
    """展开产物返回自身侧名；嵌套源返回 ``None``。"""
    root = Path(root or SKILL_ROOT)
    if is_nested_source(root):
        return None
    page = root / "SKILL.md"
    if not page.is_file():
        raise FileNotFoundError(f"展开产物缺入口页：{page}")
    actual = _frontmatter_name(page)
    for side, name in SIDE_NAMES.items():
        if actual == name:
            return side
    raise ValueError(f"展开产物入口 name 未登记：{actual}")


def _frontmatter_name(page):
    text = page.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    if len(parts) != 3 or parts[0].strip():
        raise ValueError(f"{page} 缺 YAML frontmatter")
    match = re.search(r"^name:\s*(\S+)\s*$", parts[1], flags=re.MULTILINE)
    if not match:
        raise ValueError(f"{page} frontmatter 缺 name")
    return match.group(1)


def side_page(side, root=None):
    """返回一侧入口；展开产物仅允许读取自身根入口。"""
    if side not in SIDE_NAMES:
        raise ValueError(f"未知测试侧：{side}")
    root = Path(root or SKILL_ROOT)
    if is_nested_source(root):
        return root / side / "SKILL.md"
    page = root / "SKILL.md"
    if not page.is_file():
        raise FileNotFoundError(f"展开产物缺入口页：{page}")
    actual = _frontmatter_name(page)
    expected = SIDE_NAMES[side]
    if actual != expected:
        raise ValueError(f"展开产物是 {actual}，不能读取 {expected} 入口")
    return page


def nested_side_page(side, root=None):
    """返回嵌套源的侧入口路径；调用方负责在展开产物中跳过。"""
    if side not in SIDE_NAMES:
        raise ValueError(f"未知测试侧：{side}")
    return Path(root or SKILL_ROOT) / side / "SKILL.md"


def entry_pages(root=None):
    """返回当前布局实际存在的入口页。"""
    root = Path(root or SKILL_ROOT)
    if is_nested_source(root):
        return [root / "SKILL.md",
                *(root / side / "SKILL.md" for side in SIDE_NAMES)]
    return [root / "SKILL.md"]


def side_page_for(case, side):
    """展开产物缺少所测侧时跳过该条测试。"""
    try:
        return side_page(side)
    except ValueError:
        case.skipTest(f"展开产物不含 {side} 侧入口")


def add_tests_to_path():
    """让子目录测试能导入 tests/ 根的共用夹具。"""
    value = str(TESTS_ROOT)
    if value not in sys.path:
        sys.path.insert(0, value)


def require_nested_source(case, reason):
    """展开产物中跳过只验证嵌套源边界的测试。"""
    if not is_nested_source():
        case.skipTest(f"仅嵌套源适用：{reason}")
