"""核对 c_api 动态库导出函数名和执行器实际加载路径。"""

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

import _stage_card


EXECUTOR_LOAD_PREFIX = "[c_api_executor] loaded_library="


class BindingError(ValueError):
    """动态库、调用序列表或执行器日志不满足绑定约定。"""


def _exported_text_names(text):
    names = []
    for line in text.splitlines():
        fields = line.strip().split()
        if len(fields) >= 2 and fields[-2] == "T":
            names.append(fields[-1])
    return names


def _write_report(path, report):
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _runtime_line(log_path):
    lines = [line for line in Path(log_path).read_text(
        encoding="utf-8", errors="replace").splitlines()
             if line.startswith(EXECUTOR_LOAD_PREFIX)]
    if not lines:
        raise BindingError(f"执行器日志缺少合同前缀 {EXECUTOR_LOAD_PREFIX!r}")
    if len(lines) != 1:
        raise BindingError(f"执行器日志含 {len(lines)} 条加载合同记录：{lines}")
    line = lines[0]
    rest = line[len(EXECUTOR_LOAD_PREFIX):]
    if " exported_name=" not in rest:
        raise BindingError(f"执行器加载合同记录格式不完整：{line}")
    loaded, exported = rest.rsplit(" exported_name=", 1)
    if not loaded or not exported:
        raise BindingError(f"执行器加载合同记录格式不完整：{line}")
    return line, loaded, exported


def main():
    parser = argparse.ArgumentParser(description="c_api 动态库绑定检查")
    parser.add_argument("--library", required=True, help="待检查共享库的绝对路径")
    parser.add_argument("--call-sequence", required=True,
                        help="align_signatures.py 生成的调用序列表")
    parser.add_argument("--nm-cmd", default="nm", help="nm 命令，可由测试替换")
    parser.add_argument("--executor-log", help="执行器运行日志")
    parser.add_argument("-o", "--output", required=True, help="绑定报告 JSON")
    args = parser.parse_args()
    _stage_card.announce(__file__)

    failures = []
    report = {
        "schema_version": 1,
        "library": args.library,
        "library_sha256": None,
        "plain_name": None,
        "mangled": None,
        "table_exported_name": None,
        "s2_exported_name_pending": False,
        "exported_text_names": [],
        "resolved_exported_name": None,
        "runtime_load": None,
        "failures": failures,
    }

    library = None
    table = None
    try:
        raw_library = Path(args.library).expanduser()
        if not raw_library.is_absolute():
            raise BindingError("--library 必须是绝对路径")
        library = raw_library.resolve(strict=True)
        if not library.is_file():
            raise BindingError(f"--library 不是普通文件：{library}")
        report["library"] = str(library)
        report["library_sha256"] = hashlib.sha256(library.read_bytes()).hexdigest()

        table = json.loads(Path(args.call_sequence).read_text(encoding="utf-8"))
        plain_name = table.get("symbol")
        mangled = table.get("mangled")
        if not isinstance(plain_name, str) or not plain_name:
            raise BindingError("调用序列表缺少非空 symbol 字段")
        if not isinstance(mangled, bool):
            raise BindingError("调用序列表的 mangled 字段必须是 bool")
        report["plain_name"] = plain_name
        report["mangled"] = mangled
        recorded = table.get("exported_name")
        if recorded is not None and (not isinstance(recorded, str) or not recorded):
            raise BindingError("调用序列表的 exported_name 必须是 null 或非空字符串")
        report["table_exported_name"] = recorded
        report["s2_exported_name_pending"] = recorded is None

        try:
            done = subprocess.run(
                [args.nm_cmd, "-D", str(library)], capture_output=True,
                text=True, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise BindingError(f"运行 {args.nm_cmd!r} 失败：{exc}") from exc
        if done.returncode != 0:
            detail = (done.stderr or done.stdout).strip()
            raise BindingError(
                f"{args.nm_cmd!r} -D 退出码为 {done.returncode}：{detail}")
        exported = _exported_text_names(done.stdout)
        report["exported_text_names"] = exported
        if mangled:
            matches = [name for name in exported if plain_name in name]
        else:
            matches = [name for name in exported if name == plain_name]
        if len(matches) != 1:
            seen = ", ".join(exported) if exported else "无 T 类型导出项"
            if not matches:
                raise BindingError(
                    f"找不到 {plain_name} 对应的 T 类型导出函数名；实际看到：{seen}")
            raise BindingError(
                f"{plain_name} 匹配到多份 T 类型导出函数名，无法唯一决定："
                + ", ".join(matches))
        resolved = matches[0]
        report["resolved_exported_name"] = resolved
        if recorded is not None and recorded != resolved:
            raise BindingError(
                f"调用序列表记录的导出函数名 {recorded!r} 与 nm -D 的 "
                f"{resolved!r} 不一致")
    except (BindingError, OSError, json.JSONDecodeError) as exc:
        failures.append(str(exc))

    if args.executor_log and report["resolved_exported_name"] is not None:
        runtime = {"passed": False, "line": None,
                   "loaded_library": None, "exported_name": None}
        report["runtime_load"] = runtime
        try:
            line, loaded, exported_name = _runtime_line(args.executor_log)
            runtime.update({"line": line, "loaded_library": loaded,
                            "exported_name": exported_name})
            if not Path(loaded).is_absolute():
                raise BindingError(f"执行器记录的库路径不是绝对路径：{line}")
            if os.path.realpath(loaded) != str(library):
                raise BindingError(
                    f"执行器加载的库路径不一致：{line}；应为 {library}")
            if exported_name != report["resolved_exported_name"]:
                raise BindingError(
                    f"执行器使用的导出函数名不一致：{line}；应为 "
                    f"{report['resolved_exported_name']}")
            runtime["passed"] = True
        except (BindingError, OSError) as exc:
            failures.append(str(exc))
    elif args.executor_log:
        report["runtime_load"] = {
            "passed": False, "line": None, "loaded_library": None,
            "exported_name": None,
        }

    _write_report(args.output, report)
    if failures:
        for failure in failures:
            print(f"✗ {failure}")
        return 2
    print(f"c_api 动态库绑定检查通过 → {args.output}")
    print(f"resolved_exported_name={report['resolved_exported_name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
