#!/usr/bin/env python3
"""体检任务方自带件与装机 ATK 的兼容性。只报，不修。

自带件是「用例 + 插件 + 脚本」从**作者机搬到验收机**。跨这道边界只有三样
东西会变，本量具覆盖前两样，第三样只有冒烟抓得到：

| 真因类 | 表现 | 本量具 |
| --- | --- | --- |
| ATK 版本错配 | schema 字段类型变了、args/kwargs 分流约定变了 | 查（A、D） |
| 自带件内部不自洽 | 用例里的名字在自带件插件里解析不到，或解析到恒假分支 | 查（B、C、E） |
| 宿主环境假设不成立 | 作者机有 MKL / x86 / GPU，验收机没有 | **查不到**，靠冒烟 |

**判据是「解析」不是「匹配已知症状」**：名字拿去查装机那一版的注册表，
用例拿去过装机那一版的 schema。换算子、换 ATK 版本都成立，
不需要预先知道哪个字段会坏。

退出码：0 没发现不兼容；4 发现不兼容；3 输入缺失或读不出。
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

# 修法只有两种，见 references/kit-adoption.md。每条发现都归到其中一种。
FIX_CASE = "改用例字段"      # 落在 kit_fixes/ 的 adapt 脚本里
FIX_DERIVE = "派生覆盖"      # 落在 kit_fixes/ 的插件入口里

# 跑测本身会不会把这条打出来。**这一列决定本量具什么时候跑**：
# 会暴露的那些，冒烟一轮就报出来了，先跑比先查便宜；查不到的那些
# （判据恒假是唯一一类）跑测永远静默通过，只有静态解析看得见。
LOUD = "冒烟会暴露"
SILENT = "**跑测查不到**"

findings: list[tuple[str, str, str, str, str]] = []


def report(code, criterion, evidence, fix, seen=LOUD):
    findings.append((code, criterion, evidence, fix, seen))


def _load_cases(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("cases", data)
    if not isinstance(data, list):
        raise ValueError(f"{path} 不是用例数组")
    return data


# ---------------------------------------------------------------- A schema

def check_schema(cases):
    """拿装机 ATK 自己的 CaseConfig 校验，报错原样透出。

    不写死字段名：ATK 换版本改了哪个字段，这里就报哪个字段。
    """
    try:
        from atk.configs.case_config import CaseConfig
    except Exception as exc:  # noqa: BLE001
        report("A", "装机 ATK 的 CaseConfig 导入失败",
               f"{type(exc).__name__}: {exc}", "先修环境，本项未查")
        return
    seen = set()
    for case in cases:
        try:
            CaseConfig(**case)
        except Exception as exc:  # noqa: BLE001
            first = str(exc).strip().splitlines()
            msg = " / ".join(line.strip() for line in first[:4])
            if msg in seen:
                continue
            seen.add(msg)
            report("A", "用例过不了装机 ATK 的 CaseConfig 校验",
                   f"{case.get('name', case.get('id'))}: {msg}"
                   + _accepts(CaseConfig, first), FIX_CASE)


def _accepts(model, error_lines):
    """装机那一版对出错字段收什么类型。

    **ATK 的报错只说不合法，不说合法的是什么**，少了这句就得去
    site-packages 里读 `case_config.py`——实测一轮为此读了五个文件。
    字段名从 pydantic 的位置行取（形如 `outputs.str`），取不到就不加。
    """
    fields = getattr(model, "model_fields", None)
    if not fields:
        return ""
    out = []
    for line in error_lines:
        name = line.strip().split(".")[0]
        if name in fields and name not in out:
            out.append(name)
    if not out:
        return ""
    return "；装机 CaseConfig 收：" + "、".join(
        f"{n}: {getattr(fields[n], 'annotation', '?')}" for n in out)


# ------------------------------------------------------- B 名字解析到注册表

def _registry_names(reg):
    """ATK 的 Registry 换过取名接口，逐个试，全不通就返回空。"""
    for attr in ("get_registered_names", "get_register_keys", "keys"):
        getter = getattr(reg, attr, None)
        if callable(getter):
            try:
                return sorted(getter())
            except Exception:  # noqa: BLE001
                continue
    got = getattr(reg, "_obj_map", None)
    return sorted(got) if isinstance(got, dict) else []


def _load_plugin(plugin: Path):
    """照 ATK 的 `-p` 那样载入自带件，**载整个目录，不是单个文件**。

    只 import `--plugin` 指的那一个文件时，注册在别的文件里的东西一律看不见。
    实测：执行器在 `function_*.py`、比对器在 `accuracy_*.py` 的自带件，单文件载入
    后比对器注册表里只剩装机自带的四个，自带件那个名字整个不出现——于是「解析
    不到」照报，而报出来的可选名单里没有正确答案。执行器与比对器分文件写是常见
    做法，按单文件载就会把跑得通的 kit 报成不兼容。

    载入口用 ATK 自己的两个工厂（见 `atk/tasks/main.py:43-44` 的进口）：跑起来
    之后做同一件事的就是这两个，用它们才不会与 ATK 的实际行为分叉。
    """
    from atk.tasks.post_process import AccuracyFactory
    from atk.tasks.task_plugins_register import ApiExecuteFactory, init_task_registry

    init_task_registry()  # 先内置后自带件：自带件的派生类要找得到它继承的基类
    root = plugin if plugin.is_dir() else plugin.parent
    sys.path.insert(0, str(root))
    ApiExecuteFactory.init_plugin_path(str(root))
    AccuracyFactory.init_plugin_path(str(root))


def check_registries(cases, plugin: Path | None):
    """用例里指向注册表的名字，载入自带件之后必须解析得到。

    覆盖 api_type 与 standard.acc。解析不到时把注册表里实际有什么一并打出来——
    ATK 的报错只说找不到，不说有什么，那一步要靠人去翻源码。
    """
    try:
        # `register` 本身就是执行器的 Registry 实例，不是装饰器函数。
        from atk.tasks.api_execute import register as API_REGISTRY
        from atk.tasks.post_process import ACCURACY_REGISTRY
    except Exception as exc:  # noqa: BLE001
        report("B", "装机 ATK 的注册表导入失败",
               f"{type(exc).__name__}: {exc}", "先修环境，本项未查")
        return
    if plugin is not None:
        try:
            _load_plugin(plugin)
        except Exception as exc:  # noqa: BLE001
            report("B", "自带件插件载不进来，注册表是空的",
                   f"{plugin.name}: {type(exc).__name__}: {exc}", FIX_DERIVE)
            return

    apis = _registry_names(API_REGISTRY)
    accs = _registry_names(ACCURACY_REGISTRY)
    if not apis and not accs:
        report("B", "装机 ATK 的 Registry 取不出名字，本项未查",
               f"试过 get_registered_names / get_register_keys / keys / _obj_map，"
               f"实际有：{[a for a in dir(API_REGISTRY) if not a.startswith('__')]}",
               "改本量具的 _registry_names")
        return
    for label, reg_names, pick in (
        ("api_type", apis, lambda c: c.get("api_type")),
        ("standard.acc", accs, lambda c: _acc_name(c)),
    ):
        used = {pick(c) for c in cases}
        for name in sorted(n for n in used if isinstance(n, str)):
            if not name:
                report("B", f"用例的 {label} 是空串",
                       f"注册表里有：{reg_names}", FIX_CASE)
            elif name not in reg_names:
                report("B", f"用例的 {label} = {name!r} 在注册表里解析不到",
                       f"注册表里有：{reg_names}", FIX_CASE)


def _acc_name(case):
    std = case.get("standard")
    if not isinstance(std, dict):
        return None
    acc = std.get("acc")
    if isinstance(acc, dict):
        return acc.get("value")
    return acc


# ------------------------------------------------------------ C 输出个数

def check_outputs(cases, kit: Path):
    """`outputs` 写成 dict 时，**总数不能从键推**。

    dict 的含义是「第 N 个输出的属性是……」，没写进去的输出照样存在。
    总数只有执行器知道：数它 invoke/__call__ 里 return 的元组长度。
    """
    dicts = [c for c in cases if isinstance(c.get("outputs"), dict)]
    if not dicts:
        return
    keys = sorted({k for c in dicts for k in c["outputs"]})
    hint = _return_arity(kit)
    report("C", "用例的 outputs 是 dict，装机 ATK 只收 str/int",
           f"dict 里声明了第 {keys} 个输出的属性；总数要数执行器的 return 元组"
           + (f"，扫到的候选：{hint}" if hint else "，没扫到，手工数"),
           FIX_CASE)


def _return_arity(kit: Path):
    """扫自带件里 return 多元组的行，给出候选长度。只作提示，不作判定。"""
    out = []
    for path in sorted(kit.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple):
                out.append(f"{path.name}:{node.lineno} 返回 {len(node.value.elts)} 个")
    return "；".join(out[:6])


# --------------------------------------------------------- D 调用约定

def check_calling_convention(cases, kit: Path):
    """ATK 按 name 分流：**无名进 args，有名进 kwargs**。

    自带件的执行器如果只读 input_data.args 而用例的输入全带 name，
    它拿到 0 个参数。报错长成解包错误，指向执行器，不指向这条约定。
    """
    named = any(isinstance(i, dict) and i.get("name")
                for c in cases for i in c.get("inputs", []))
    if not named:
        return
    for path in sorted(kit.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "input_data.args" not in text:
            continue
        # 只认对同一个对象的 kwargs 回退。执行器自己的 call_kwargs 之类
        # 与这条约定无关，拿 "kwargs" 子串判会漏报。
        if "input_data.kwargs" in text:
            continue
        report("D", "用例的输入带 name（ATK 分流进 kwargs），执行器只读 args",
               f"{path.name} 用了 input_data.args 且没有 kwargs 回退", FIX_CASE)


# ----------------------------------------------------------- E 恒假分支

_EQ = re.compile(r"""(?P<lhs>[\w.]*(?:case_config|case)\.[\w.]*name)\s*==\s*"""
                 r"""(?P<q>['"])(?P<lit>[^'"]+)(?P=q)""")


def check_dead_branches(cases, kit: Path):
    """按用例名分派的判据，字面量一个都不匹配就是恒假分支。

    恒假分支不报错，只是整条分支从不执行——表现是走了另一条路径的结论，
    与「实现不对」难以区分。
    """
    names = {c.get("name") for c in cases}
    for path in sorted(kit.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in _EQ.finditer(text):
            lit = m.group("lit")
            if lit in names:
                continue
            line = text[:m.start()].count("\n") + 1
            report("E", "按用例名分派的判据恒假",
                   f"{path.name}:{line} 判 {m.group('lhs')} == {lit!r}，"
                   f"而交付的用例名形如 {sorted(n for n in names if n)[:1]}",
                   FIX_DERIVE, SILENT)


# ----------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--kit", required=True, help="自带件目录")
    ap.add_argument("--cases", required=True, help="自带件的用例文件")
    ap.add_argument("--plugin", help="import 即注册的入口 .py，不给则跳过 B")
    ns = ap.parse_args()

    kit, cases_path = Path(ns.kit), Path(ns.cases)
    if not kit.is_dir():
        print(f"自带件目录不存在：{kit}", file=sys.stderr)
        return 3
    try:
        cases = _load_cases(cases_path)
    except Exception as exc:  # noqa: BLE001
        print(f"用例读不出：{cases_path}：{exc}", file=sys.stderr)
        return 3

    check_schema(cases)
    check_registries(cases, Path(ns.plugin) if ns.plugin else None)
    check_outputs(cases, kit)
    check_calling_convention(cases, kit)
    check_dead_branches(cases, kit)

    print(f"体检 {kit.name}：{len(cases)} 条用例")
    if not findings:
        print("没发现不兼容。")
    else:
        print(f"发现 {len(findings)} 处不兼容：\n")
        print("| 类 | 冒烟看得见吗 | 判据 | 证据 | 修法 |")
        print("| --- | --- | --- | --- | --- |")
        for code, criterion, evidence, fix, seen in findings:
            print(f"| {code} | {seen} | {criterion} | {evidence} | {fix} |")
        print()
        print(f"{FIX_CASE}：写进 kit_fixes/ 的 adapt 脚本，产出用例副本。")
        print(f"{FIX_DERIVE}：写进 kit_fixes/ 的插件入口，派生子类覆盖。")
        print("**两种修法都不改自带件原件**——原件的 sha256 要进 facts.adopted_files。")
    print()
    if not ns.plugin:
        print("没给 --plugin：注册表里只有 ATK 内置那些，自带件自己注册的名字看不到，"
              "B 类可能误报。给上入口再跑一遍。")
    quiet = [f for f in findings if f[4] == SILENT]
    if quiet:
        print(f"\n**{len(quiet)} 处冒烟看不见**，跑通了也仍然要修——"
              f"判据恒假时精度轮照常通过，结论是错的。")
    print("本量具查不到宿主环境假设（作者机有 MKL / x86 / GPU 而验收机没有）。"
          "过了之后必须拿 2 条用例冒烟。")
    return 4 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
