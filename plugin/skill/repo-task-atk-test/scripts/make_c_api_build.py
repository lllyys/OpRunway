"""从算子自己的测试 CMakeLists.txt 渲染 c_api 共享库构建文件。"""

import argparse
import hashlib
import json
import re
import shlex
from pathlib import Path

import _stage_card


class BuildRenderError(ValueError):
    """测试构建文件不足以确定共享库构建参数。"""


_SCOPE = {"PRIVATE", "PUBLIC", "INTERFACE"}


def _require_project_source(path, env_path, flag):
    """要求路径真实存在，且解析后仍位于待验收算子工程目录内。"""
    # 这里刻意保留白名单检查的局部实现：生成侧与验收侧以后会拆成两个
    # 独立 skill，零共享文件，不能跨拆分边界复用模块。
    try:
        env = json.loads(Path(env_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildRenderError(f"读不出 {env_path}：{exc}") from exc
    project = (env.get("operator_project") or {}).get("path")
    if not project:
        raise BuildRenderError(
            f"{env_path} 里没有 operator_project.path，无法核对 {flag}")

    resolved = Path(path).expanduser().resolve()
    root = Path(project).expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise BuildRenderError(
            f"{flag} 指向的 {resolved} 不在待验收算子工程目录（{root}）下") from exc
    if not resolved.exists():
        raise BuildRenderError(f"{flag} 指向的 {resolved} 不存在")
    return resolved


def _without_comments(text):
    lines = []
    for line in text.splitlines():
        quote = None
        escaped = False
        kept = []
        for char in line:
            if escaped:
                kept.append(char)
                escaped = False
            elif char == "\\" and quote:
                kept.append(char)
                escaped = True
            elif char in {'"', "'"}:
                kept.append(char)
                quote = None if quote == char else (char if quote is None else quote)
            elif char == "#" and quote is None:
                break
            else:
                kept.append(char)
        lines.append("".join(kept))
    return "\n".join(lines)


def _calls(text):
    clean = _without_comments(text)
    pattern = re.compile(r"(?i)\b([A-Za-z_]\w*)\s*\(")
    for match in pattern.finditer(clean):
        depth = 0
        quote = None
        escaped = False
        start = clean.find("(", match.start())
        for index in range(start, len(clean)):
            char = clean[index]
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
            elif char in {'"', "'"}:
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    yield match.group(1).lower(), clean[start + 1:index]
                    break


def _tokens(body):
    lexer = shlex.shlex(body, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def _expand_tokens(tokens, variables, stack=()):
    expanded = []
    for token in tokens:
        whole = re.fullmatch(r"\$\{([A-Za-z_]\w*)\}", token)
        if whole and whole.group(1) in variables:
            name = whole.group(1)
            if name in stack:
                raise BuildRenderError(f"CMake 变量循环引用：{name}")
            expanded.extend(_expand_tokens(variables[name], variables, stack + (name,)))
        else:
            expanded.append(token)
    return expanded


def _replace_known_dirs(token, project_root, cmake_dir):
    replacements = {
        "${PROJECT_SOURCE_DIR}": str(project_root),
        "${CMAKE_SOURCE_DIR}": str(project_root),
        "${CMAKE_CURRENT_SOURCE_DIR}": str(cmake_dir),
        "${CMAKE_CURRENT_LIST_DIR}": str(cmake_dir),
    }
    value = token
    for name, path in replacements.items():
        value = value.replace(name, path)
    return value


def _resolve_local_path(token, project_root, cmake_dir):
    value = _replace_known_dirs(token, project_root, cmake_dir)
    if "${" in value or "$<" in value:
        return value
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = cmake_dir / path
    return str(path.resolve())


def _stable_unique(items):
    return list(dict.fromkeys(items))


def parse_build_facts(text, *, op_dir, project_root, cmake_path):
    """提取测试目标实际声明的源码、包含目录、链接库和 ASC 架构参数。"""
    calls = list(_calls(text))
    variables = {}
    for name, body in calls:
        if name == "set":
            values = _tokens(body)
            if values:
                variables[values[0]] = values[1:]

    build_tokens = []
    include_tokens = []
    library_tokens = []
    for name, body in calls:
        values = _expand_tokens(_tokens(body), variables)
        if name in {"add_executable", "add_library"} and len(values) > 1:
            build_tokens.extend(values[1:])
        elif name == "target_include_directories" and len(values) > 1:
            include_tokens.extend(value for value in values[1:] if value not in _SCOPE)
        elif name == "target_link_libraries" and len(values) > 1:
            library_tokens.extend(value for value in values[1:] if value not in _SCOPE)

    cmake_dir = cmake_path.parent
    project_resolved = project_root.resolve()
    op_resolved = op_dir.resolve()
    sources = []
    for token in build_tokens:
        if not token.endswith(".cpp"):
            continue
        resolved_text = _resolve_local_path(token, project_root, cmake_dir)
        if "${" in resolved_text or "$<" in resolved_text:
            continue
        resolved = Path(resolved_text)
        try:
            relative = resolved.relative_to(op_resolved)
        except ValueError:
            continue
        if (relative.parts and relative.parts[0] in {"op_host", "op_kernel"}
                and resolved.is_file()):
            sources.append(str(resolved))

    includes = _stable_unique(
        _resolve_local_path(token, project_root, cmake_dir)
        for token in include_tokens if token)
    libraries = _stable_unique(token for token in library_tokens if token)
    arch_flags = _stable_unique(re.findall(
        r"--npu-arch=([A-Za-z0-9_-]+)", _without_comments(text)))

    failures = []
    if not sources:
        failures.append("没有读到 op_host/ 或 op_kernel/ 下的 .cpp 源码")
    if not includes:
        failures.append("没有读到 target_include_directories 包含目录")
    if not libraries:
        failures.append("没有读到 target_link_libraries 链接库")
    if not arch_flags:
        failures.append("没有读到 --npu-arch=<值> 参数")
    elif len(arch_flags) > 1:
        failures.append(f"读到多份互相冲突的 --npu-arch 参数：{arch_flags}")
    if failures:
        raise BuildRenderError("；".join(failures))

    for source in sources:
        if not Path(source).is_relative_to(project_resolved):
            raise BuildRenderError(f"源码路径越过待验收算子工程目录：{source}")
    return {
        "sources": _stable_unique(sources),
        "includes": includes,
        "libraries": libraries,
        "npu_arch": arch_flags[0],
    }


def _cmake_quote(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_cmake(op_name, facts):
    """生成真机已验证过的 ASC+CXX 共享库包装形态。"""
    stem = re.sub(r"[^a-z0-9_]+", "_", op_name.lower()).strip("_")
    target = stem + "_c_api"
    source_lines = "\n".join(f"    {_cmake_quote(path)}" for path in facts["sources"])
    include_lines = "\n".join(f"    {_cmake_quote(path)}" for path in facts["includes"])
    library_lines = "\n".join(f"    {item}" for item in facts["libraries"])
    source_args = " ".join(_cmake_quote(path) for path in facts["sources"])
    text = f"""cmake_minimum_required(VERSION 3.16)
find_package(ASC REQUIRED)
project({stem}_so LANGUAGES ASC CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library({target} SHARED
{source_lines}
)
set_source_files_properties({source_args} PROPERTIES LANGUAGE ASC)
set_target_properties({target} PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories({target} PRIVATE
{include_lines}
)
target_link_libraries({target} PRIVATE
{library_lines}
)
target_compile_options({target} PRIVATE
    "$<$<COMPILE_LANGUAGE:ASC>:--npu-arch={facts['npu_arch']}>"
)
"""
    return target, text


def main():
    parser = argparse.ArgumentParser(description="渲染 c_api 共享库构建文件")
    parser.add_argument("--env", required=True, help="环境指纹 JSON")
    parser.add_argument("--op-dir", required=True, help="experimental 算子目录")
    parser.add_argument("--project-root", required=True, help="算子工程根目录")
    parser.add_argument("--build-cmake", required=True,
                        help="算子自己的 test/CMakeLists.txt")
    parser.add_argument("-o", "--output", required=True, help="构建文件输出目录")
    args = parser.parse_args()
    _stage_card.announce(__file__)

    outdir = Path(args.output).expanduser().resolve()
    failures = []
    report = {"schema_version": 1, "failures": failures}
    try:
        op_dir = _require_project_source(args.op_dir, args.env, "--op-dir")
        project_root = _require_project_source(
            args.project_root, args.env, "--project-root")
        cmake_path = _require_project_source(
            args.build_cmake, args.env, "--build-cmake")
        if not op_dir.is_dir() or not project_root.is_dir():
            raise BuildRenderError("--op-dir 与 --project-root 必须指向目录")
        if not cmake_path.is_file():
            raise BuildRenderError("--build-cmake 必须指向文件")
        facts = parse_build_facts(
            cmake_path.read_text(encoding="utf-8", errors="replace"),
            op_dir=op_dir, project_root=project_root, cmake_path=cmake_path)
        target, generated = render_cmake(op_dir.name, facts)
        outdir.mkdir(parents=True, exist_ok=True)
        generated_path = outdir / "CMakeLists.txt"
        generated_path.write_text(generated, encoding="utf-8")
        report.update({
            "operator": op_dir.name,
            "target": target,
            "library_name": f"lib{target}.so",
            "build_cmake": str(cmake_path),
            **facts,
            "generated_file": str(generated_path),
            "generated_file_sha256": hashlib.sha256(
                generated_path.read_bytes()).hexdigest(),
        })
    except (BuildRenderError, OSError) as exc:
        failures.append(str(exc))

    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "render_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if failures:
        for failure in failures:
            print(f"✗ {failure}")
        return 2
    print(f"c_api 共享库构建文件已渲染 → {outdir / 'CMakeLists.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
