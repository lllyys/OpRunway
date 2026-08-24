"""把嵌套源展开成两个可单独安装的 skill 目录。

脚本与 reference 清单只从 artifact-contracts.json 读取。测试按 S9
分侧布局一并物化；子目录的包初始化文件不参与平铺。

退出码：0 展开完成；2 源文件缺失、测试冲突或 import 闭包不全；
3 骨架缺失、损坏或结构不可用。
"""

import argparse
import ast
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = SOURCE_ROOT / "references" / "artifact-contracts.json"
PRODUCTS = {
    "case-gen": "repo-task-case-gen",
    "acceptance": "repo-task-atk-accept",
}
TEST_GROUPS = {"case-gen": "case_gen", "acceptance": "acceptance"}
VALID_OWNERS = frozenset({*PRODUCTS, "shared"})
IGNORED_PARTS = frozenset({"__pycache__", ".pytest_cache", "dist"})


class BuildFailure(RuntimeError):
    """展开失败，code 使用开发工具约定的 2 或 3。"""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def parser():
    result = argparse.ArgumentParser(
        description="把嵌套源展开成两个可单独安装的 skill 目录",
        epilog="退出码：0 完成；2 源或 import 闭包不完整；3 骨架不可用。",
    )
    result.add_argument("--out", required=True, type=Path, help="展开产物根目录")
    result.add_argument("--side", choices=tuple(PRODUCTS), help="只展开一侧")
    return result


def load_contracts():
    """读取并核骨架中本工具依赖的两张归属表。"""
    try:
        data = json.loads(CONTRACTS.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BuildFailure(3, f"骨架不可用：{CONTRACTS}：{exc}") from exc
    if not isinstance(data, dict):
        raise BuildFailure(3, "骨架不可用：顶层必须是 JSON 对象")
    for table_name in ("scripts", "references"):
        table = data.get(table_name)
        if not isinstance(table, dict):
            raise BuildFailure(3, f"骨架不可用：{table_name} 必须是对象")
        for name, spec in table.items():
            owner = spec.get("skill") if isinstance(spec, dict) else None
            if not isinstance(name, str) or owner not in VALID_OWNERS:
                raise BuildFailure(
                    3,
                    f"骨架不可用：{table_name}.{name} 的 skill={owner!r}",
                )
    return data


def selected_names(data, table, side):
    """从骨架取本侧加 shared 的文件名，保持骨架顺序。"""
    return [
        name for name, spec in data[table].items()
        if spec["skill"] in {side, "shared"}
    ]


def source_files(data, side):
    """预检一个产物的全部必需源文件，缺失统一按退出码 2 报。"""
    page = SOURCE_ROOT / side / "SKILL.md"
    scripts = [SOURCE_ROOT / "scripts" / name
               for name in selected_names(data, "scripts", side)]
    references = [SOURCE_ROOT / "references" / name
                  for name in selected_names(data, "references", side)]
    required = [page, *scripts, *references]
    missing = [path.relative_to(SOURCE_ROOT).as_posix()
               for path in required if not path.is_file()]

    shared_tests = SOURCE_ROOT / "tests" / "shared"
    if shared_tests.is_dir():
        group = SOURCE_ROOT / "tests" / TEST_GROUPS[side]
        if not group.is_dir():
            missing.append(group.relative_to(SOURCE_ROOT).as_posix() + "/")
    if missing:
        body = "\n".join(f"  - {name}" for name in missing)
        raise BuildFailure(2, f"源文件缺失：\n{body}")
    return page, scripts, references


def remove_target(path):
    """只清空本次明确选择的产物目录。"""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def copy_skill_page(source, destination):
    """只调整嵌套页到独立页所需的两类父路径。"""
    text = source.read_text(encoding="utf-8")
    text = text.replace("../references/", "references/")
    text = text.replace("../scripts/", "scripts/")
    destination.write_text(text, encoding="utf-8")


def copy_inventory(paths, destination):
    destination.mkdir(parents=True, exist_ok=True)
    for source in paths:
        shutil.copy2(source, destination / source.name)


def copy_assets(side, destination):
    """生成侧携带运行时模板；验收侧不需要这些生成输入。"""
    if side != "case-gen":
        return
    source = SOURCE_ROOT / "assets"
    if not source.is_dir():
        raise BuildFailure(2, "源文件缺失：\n  - assets/")
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns(*IGNORED_PARTS),
    )


def local_imports(path, module_files):
    """返回脚本 import 的同目录本仓模块。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        raise BuildFailure(2, f"无法解析脚本 {path.name} 的 import：{exc}") from exc
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module.split(".")[0]]
        for module in modules:
            if module in module_files:
                yield module_files[module], node.lineno


def check_import_closure(destination):
    """确认产物里每个本仓 import 都有对应脚本。"""
    source_modules = {
        path.stem: path.name for path in (SOURCE_ROOT / "scripts").glob("*.py")
    }
    copied = {path.name for path in destination.glob("*.py")}
    missing = []
    for path in sorted(destination.glob("*.py")):
        for target, line in local_imports(path, source_modules):
            if target not in copied:
                missing.append(f"{path.name}:{line} -> {target}")
    if missing:
        body = "\n".join(f"  - {item}" for item in missing)
        raise BuildFailure(2, f"import 闭包不完整：\n{body}")


def test_sources(side):
    """按 S9 目标布局返回要平铺的测试源；未合并时返回 None。"""
    tests = SOURCE_ROOT / "tests"
    shared = tests / "shared"
    if not shared.is_dir():
        return None
    group = tests / TEST_GROUPS[side]
    roots = [path for path in tests.iterdir() if path.is_file()]
    nested = [path for base in (group, shared) for path in base.rglob("*")
              if (path.is_file() and path.name != "__init__.py"
                  and not IGNORED_PARTS.intersection(path.parts))]
    return [*roots, *nested]


def copy_tests(side, destination):
    """把本侧、shared 与根夹具平铺进产物 tests/。"""
    sources = test_sources(side)
    if sources is None:
        return "absent"
    by_name = {}
    for source in sources:
        previous = by_name.get(source.name)
        if previous is not None and previous != source:
            raise BuildFailure(
                2,
                f"测试平铺后重名：{previous} 与 {source} -> {source.name}",
            )
        by_name[source.name] = source
    destination.mkdir(parents=True, exist_ok=True)
    for name, source in sorted(by_name.items()):
        shutil.copy2(source, destination / name)
    return "present"


def source_commit():
    """取源提交；独立源码副本不在 Git 仓时按契约写 null。"""
    result = subprocess.run(
        ["git", "-C", str(SOURCE_ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(target, side, tests_status):
    """清单不登记自身，避免自引用摘要。"""
    files = {
        path.relative_to(target).as_posix(): sha256(path)
        for path in sorted(target.rglob("*"))
        if path.is_file() and path.name != "MANIFEST.json"
    }
    manifest = {
        "source_commit": source_commit(),
        "generated_at": datetime.now().astimezone().isoformat(),
        "side": side,
        "tests": tests_status,
        "files": files,
    }
    (target / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return len(files) + 1


def build_side(data, out, side):
    page, scripts, references = source_files(data, side)
    target = out / PRODUCTS[side]
    remove_target(target)
    target.mkdir(parents=True)
    copy_skill_page(page, target / "SKILL.md")
    copy_inventory(scripts, target / "scripts")
    copy_inventory(references, target / "references")
    copy_assets(side, target / "assets")
    check_import_closure(target / "scripts")
    tests_status = copy_tests(side, target / "tests")
    count = write_manifest(target, side, tests_status)
    print(f"已生成 {target}（{count} 个文件，tests={tests_status}）")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        data = load_contracts()
        sides = (args.side,) if args.side else tuple(PRODUCTS)
        args.out.mkdir(parents=True, exist_ok=True)
        for side in sides:
            build_side(data, args.out, side)
    except BuildFailure as exc:
        print(exc, file=sys.stderr)
        return exc.code
    return 0


if __name__ == "__main__":
    sys.exit(main())
