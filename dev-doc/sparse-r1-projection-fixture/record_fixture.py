#!/usr/bin/env python3
"""录制 role 投影矩阵 fixture 的现状输出（钉板本体 fixture.json）。

对 facts/ 下每个合成 gen_csv.py 依次执行并记录：

1. `package.py check --facts facts/<name>.py --print-header`：退出码、stdout、stderr 全文。
2. 把该文件拷贝为 out/<name>/gen_csv.py（out/<name>/ 每次先清空重建），跑
   `package.py render --facts out/<name>/gen_csv.py`：退出码、stdout、stderr；
   成功时记 CSV 表头行原文与五件派生物的 SHA-256。
3. 在本进程内用 package.py 的加载器直接调用模板 generate(FACTS)：成功记 axes/blocks/
   pairs/rows 摘要，失败记异常类型、底层 cause 类型与消息首行——这条通路能把
   「校验拒但生成器接受」（陷阱 5）这类口径分叉显式钉住。

全部结果写 fixture.json。录制保证幂等：不写绝对路径（本 fixture 目录一律替换成
`<FIXTURE>`，解释器路径替换成 `<PYTHON>`）、不写时间戳；两次运行 fixture.json
逐字节一致。

用法：
    python3 record_fixture.py                    # 录制全部 fixture
    python3 record_fixture.py g1_profile ...     # 只录制指定项
    python3 record_fixture.py --refresh-common   # 先把 facts/*.py 的通用代码区
                                                 # 替换为当前模板的通用代码区，再录制。
                                                 # 模板（重构后）变更时先跑这个。

比对约定：ProjectionIR 重构的「无行为变化」判据是 fixture.json 的 "results" 子树
逐字节一致；"_meta.recorded_against" 里的代码哈希允许（也应该）随重构变化。
"""

import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent
WORKTREE = FIXTURE_DIR.parents[1]
SKILL_ROOT = WORKTREE / "plugin" / "skill" / "repo-task-blas-case-gen"
PACKAGE = SKILL_ROOT / "scripts" / "package.py"
TEMPLATE = SKILL_ROOT / "assets" / "template" / "gen_csv.py"
FACTS_DIR = FIXTURE_DIR / "facts"
OUT_DIR = FIXTURE_DIR / "out"
RESULT_PATH = FIXTURE_DIR / "fixture.json"
COMMON_MARKER = "# ===== 通用代码区"
DERIVED_NAMES = (
    "{op}_test.csv", "README.md", "gpu_baseline.csv",
    "verify_accuracy.py", "verify_performance.py",
)


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _normalize(text):
    text = text.replace(str(FIXTURE_DIR), "<FIXTURE>")
    lines = []
    for line in text.splitlines():
        if line.startswith("解释器："):
            line = "解释器：<PYTHON>"
        lines.append(line)
    return "\n".join(lines)


def _load_package_module():
    spec = importlib.util.spec_from_file_location("_projfix_package", PACKAGE)
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def _run(arguments):
    proc = subprocess.run(
        [sys.executable, str(PACKAGE), *arguments],
        capture_output=True, text=True, cwd=FIXTURE_DIR, timeout=600,
    )
    return {
        "exit": proc.returncode,
        "stdout": _normalize(proc.stdout),
        "stderr": _normalize(proc.stderr),
    }


def _inprocess_generate(package_module, facts_path):
    """在本进程用模板 generate 直接跑 FACTS，钉住生成器自身的口径。"""
    facts = package_module._load_python_facts(facts_path)
    generator = package_module._load_generator()
    try:
        generated = generator.generate(facts)
    except Exception as exc:  # 现状快照：任何异常都如实记录
        cause = exc.__cause__
        return {
            "status": "exception",
            "exception_type": type(exc).__name__,
            "cause_type": type(cause).__name__ if cause is not None else None,
            "message_first_line": str(exc).splitlines()[0] if str(exc) else "",
        }
    report = generated["report"]
    return {
        "status": "ok",
        "axes": report["axes"],
        "blocks": report["blocks"],
        "pairs_covered": report["pairs_covered"],
        "pairs_total": report["pairs_total"],
        "pairs_infeasible": len(report["pairs_infeasible"]),
        "rows_dropped": report["rows_dropped"],
        "header_columns": len(generated["header"]),
        "total_rows": len(generated["rows"]),
    }


def _record_one(package_module, name):
    facts_path = FACTS_DIR / f"{name}.py"
    entry = {}

    check = _run(["check", "--facts", f"facts/{name}.py", "--print-header"])
    entry["check"] = check

    work = OUT_DIR / name
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    shutil.copyfile(facts_path, work / "gen_csv.py")
    render = _run(["render", "--facts", f"out/{name}/gen_csv.py"])
    entry["render"] = {
        "exit": render["exit"],
        "stdout": render["stdout"],
        "stderr": render["stderr"],
        "stderr_first_line": render["stderr"].splitlines()[0] if render["stderr"] else None,
    }

    if render["exit"] == 0:
        facts = package_module._load_python_facts(facts_path)
        op = facts["op"]
        derived = {}
        for pattern in DERIVED_NAMES:
            file_name = pattern.format(op=op)
            derived[file_name] = _sha256(work / file_name)
        entry["render"]["derived_sha256"] = derived
        csv_path = work / f"{op}_test.csv"
        with csv_path.open(encoding="utf-8") as stream:
            entry["render"]["csv_header_line"] = stream.readline().rstrip("\n")
        entry["render"]["csv_row_count"] = sum(
            1 for _ in csv_path.open(encoding="utf-8")) - 1

    entry["generate_inprocess"] = _inprocess_generate(package_module, facts_path)
    return entry


def _refresh_common():
    template_text = TEMPLATE.read_text(encoding="utf-8")
    marker = template_text.find(COMMON_MARKER)
    if marker < 0:
        raise SystemExit("模板缺通用代码区标记，无法刷新")
    common = template_text[marker:]
    for path in sorted(FACTS_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        local = text.find(COMMON_MARKER)
        if local < 0:
            raise SystemExit(f"{path.name} 缺通用代码区标记，无法刷新")
        refreshed = text[:local] + common
        if refreshed != text:
            path.write_text(refreshed, encoding="utf-8")
            print(f"refreshed 通用代码区: facts/{path.name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("names", nargs="*", help="只录制这些 fixture（默认全部）")
    parser.add_argument(
        "--refresh-common", action="store_true",
        help="录制前把 facts/*.py 的通用代码区替换为当前模板版本")
    args = parser.parse_args()

    if args.refresh_common:
        _refresh_common()

    all_names = sorted(path.stem for path in FACTS_DIR.glob("*.py"))
    names = args.names or all_names
    unknown = [name for name in names if name not in all_names]
    if unknown:
        raise SystemExit(f"facts/ 下没有这些 fixture：{unknown}")
    if args.names and RESULT_PATH.exists():
        data = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
        results = data.get("results", {})
    else:
        results = {}

    package_module = _load_package_module()
    for name in names:
        print(f"recording {name} ...", flush=True)
        results[name] = _record_one(package_module, name)

    data = {
        "_meta": {
            "description": (
                "role 投影矩阵 fixture 的现状快照（钉板）。results 是行为面；"
                "recorded_against 是录制时的代码与输入指纹，重构后允许变化。"
            ),
            "compare_rule": "无行为变化 = 前后两份 fixture.json 的 results 子树逐字节一致",
            "recorded_against": {
                "package_py_sha256": _sha256(PACKAGE),
                "template_common_sha256": hashlib.sha256(
                    TEMPLATE.read_text(encoding="utf-8")[
                        TEMPLATE.read_text(encoding="utf-8").find(COMMON_MARKER):
                    ].encode("utf-8")
                ).hexdigest(),
                "facts_sha256": {
                    path.stem: _sha256(path) for path in sorted(FACTS_DIR.glob("*.py"))
                },
            },
        },
        "results": {name: results[name] for name in sorted(results)},
    }
    RESULT_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {RESULT_PATH.relative_to(FIXTURE_DIR)} ({len(results)} fixtures)")


if __name__ == "__main__":
    main()
