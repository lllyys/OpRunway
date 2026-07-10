"""清单同步校验（P0）——`AGENTS.md` frontmatter 的**注册面**与**磁盘实际文件**双向一致
（`agents/*.md`、`skills/*/SKILL.md`），**杜绝双写漂移**（cannbot 那种手抄两份清单的坑）。

⚠ 比对的另一侧是**文件系统**而非 `plugin.json`：Claude Code 靠约定目录自动发现 agent/skill。实测 `2.1.206`：
`plugin.json` 写 `"agents": ["./agents/x.md"]` → agent 全不注册（`Agents(0)`，插件仍加载、`plugin validate` 仍 ✔）；
写成 `["agents/x.md"]` 或 `"./agents/"` → 整个插件加载失败。故本脚本另设**反向门**：`plugin.json` 出现 `agents`
字段即 DRIFT。详见 `AGENTS.md` 的「注册面 vs 调度面」。

**fail-closed**：读不了 / 解析不了 / 语法不认识 / 值不合法 —— 一律 DRIFT + exit 1，绝不静默放行。
门宁可误报漂移，也不能因一个截断的 frontmatter 而假 SYNCED。

支持的 frontmatter 语法（**刻意收窄，其余一律拒**）：首行 `---`、必须有闭合 `---`；`key: 标量`；
`key:` + 后续 `  - item` 块列表；`key: [a, b]` 流列表（项不得含逗号）。标量/项可带成对引号。
允许空行与整行 `#` 注释。**不支持**：嵌套结构、块标量、行尾注释、重复 key、重复列表项。

用法: python3 check_manifest_sync.py [--plugin-root <plugin 根>]
默认 plugin 根 = 本脚本(acc-common)的上一级。只读、stdlib。打印 `STATUS: SYNCED|DRIFT`，exit 0/1。
"""
import argparse
import json
import os
import re
import sys

# 注册名白名单：拒路径分隔符 / `..` / 空白 —— 名字会被拼进 agents/<x>.md、skills/<x>/SKILL.md
_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class ManifestError(Exception):
    """清单读取/解析失败。一律 fail-closed 映射为 DRIFT，不让异常逃逸成 traceback。"""


def _unquote(raw, lineno):
    """成对引号去引；未配对的引号视为语法错误（防畸形值被 strip 成合法名字）。"""
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
        return raw[1:-1]
    if raw[:1] in "'\"" or raw[-1:] in "'\"":
        raise ManifestError(f"第 {lineno} 行：引号未配对 {raw!r}")
    return raw


def _parse_flow_list(rest, lineno):
    """`[a, b]` → ['a','b']。**不得**有空项（`[a,,b]`/`[a,]` 这类多余逗号）或重复项；
    项不得含逗号（含引号内 —— 未配对引号会在 `_unquote` 里被拒）。不做完整 YAML 词法。"""
    inner = rest[1:-1].strip()
    if not inner:
        return []
    items = []
    for raw in inner.split(","):
        s = raw.strip()
        if not s:
            raise ManifestError(f"第 {lineno} 行：流列表含空项（多余逗号）")
        item = _unquote(s, lineno)
        if item in items:
            raise ManifestError(f"第 {lineno} 行：流列表重复项 {item!r}")
        items.append(item)
    return items


def _parse_frontmatter(path):
    """解析 `AGENTS.md` frontmatter → dict。任何异常一律转 ManifestError（fail-closed）。"""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        raise ManifestError(f"缺 {os.path.basename(path)}（跨 CLI 单一事实源）")
    except (OSError, UnicodeError) as ex:
        raise ManifestError(f"读 {os.path.basename(path)} 失败：{ex}")

    if not lines or lines[0].strip() != "---":
        raise ManifestError("AGENTS.md 首行不是 `---`（缺 frontmatter）")

    out, cur, closed = {}, None, False
    for i, ln in enumerate(lines[1:], start=2):
        s = ln.strip()
        if s == "---":                       # 闭合定界符
            closed = True
            break
        if not s or s.startswith("#"):       # 空行 / 整行注释
            continue

        if not ln[:1].isspace():             # 顶层 key
            if ":" not in s:
                raise ManifestError(f"第 {i} 行无法识别（非 `key: …`）：{s!r}")
            key, _, rest = s.partition(":")
            key, rest = key.strip(), rest.strip()
            if not _KEY_RE.match(key):
                raise ManifestError(f"第 {i} 行 key 非法：{key!r}")
            if key in out:
                raise ManifestError(f"第 {i} 行重复 key：{key!r}")
            if rest.startswith("[") and rest.endswith("]"):
                out[key], cur = _parse_flow_list(rest, i), None
            elif rest:
                out[key], cur = _unquote(rest, i), None
            else:
                out[key], cur = [], key      # 期待后续块列表
        elif cur is not None and s.startswith("- "):
            item = _unquote(s[2:].strip(), i)
            if item in out[cur]:
                raise ManifestError(f"第 {i} 行 {cur} 重复项：{item!r}")
            out[cur].append(item)
        else:
            raise ManifestError(f"第 {i} 行无法识别（不支持的缩进/语法）：{s!r}")

    if not closed:
        raise ManifestError("AGENTS.md frontmatter 缺闭合 `---`（截断？）")
    return out


def _names(fm, key):
    """取注册面列表并校验每项是合法注册名（拒 `..`、斜杠、空白）。"""
    val = fm.get(key)
    if not isinstance(val, list) or not val:
        raise ManifestError(f"AGENTS.md 未声明 {key}（或非列表）")
    for n in val:
        if not _NAME_RE.match(n) or n in (".", ".."):
            raise ManifestError(f"AGENTS.md {key} 含非法名：{n!r}")
    return val


def _within(root, path):
    """符号链接解析后仍须落在 plugin 根内（防用仓外文件满足门）。"""
    r = os.path.realpath(root)
    p = os.path.realpath(path)
    return p == r or p.startswith(r + os.sep)


def _disk_agents(root):
    """`agents/` 下**真实文件** `*.md` 的 stem 集合（目录/断链/仓外软链均不计）。
    Claude Code 靠约定目录自动发现 agent，故这批文件是自动发现的**候选集**——比 `plugin.json` 的声明
    更接近实际加载对象（但不保证每个文件都能被成功解析/注册）。"""
    d = os.path.join(root, "agents")
    if not os.path.isdir(d):
        return set()
    try:
        out = set()
        for name in os.listdir(d):
            p = os.path.join(d, name)
            if name.endswith(".md") and os.path.isfile(p) and _within(root, p):
                out.add(name[:-3])
        return out
    except OSError as ex:
        raise ManifestError(f"扫 agents/ 失败：{ex}")


def _disk_skills(root):
    """`skills/<name>/SKILL.md` 的 `<name>` 集合（同为约定目录自动发现）。"""
    d = os.path.join(root, "skills")
    if not os.path.isdir(d):
        return set()
    try:
        out = set()
        for name in os.listdir(d):
            p = os.path.join(d, name, "SKILL.md")
            if os.path.isfile(p) and _within(root, p):
                out.add(name)
        return out
    except OSError as ex:
        raise ManifestError(f"扫 skills/ 失败：{ex}")


def _diff(label, declared, disk):
    """注册面 ↔ 磁盘 双向集合比对：漏登记（有文件没声明）与多登记（声明了没文件）都算漂移。"""
    if declared == disk:
        return None
    return (f"AGENTS.md {label} 与磁盘不一致（双写漂移）："
            f"声明了但缺文件 {sorted(declared - disk)}；有文件但未登记 {sorted(disk - declared)}")


def _check_plugin_json(root):
    """反向门：`plugin.json` 必须存在、是 JSON 对象、且**不得**声明 `agents`。"""
    p = os.path.join(root, ".claude-plugin", "plugin.json")
    try:
        with open(p, encoding="utf-8") as f:
            pj = json.load(f)
    except FileNotFoundError:
        raise ManifestError("缺 .claude-plugin/plugin.json")
    except (json.JSONDecodeError, OSError, UnicodeError) as ex:
        raise ManifestError(f"plugin.json 读取/解析失败：{ex}")
    if not isinstance(pj, dict):
        raise ManifestError(f"plugin.json 顶层须是 JSON 对象，得 {type(pj).__name__}")
    if "agents" in pj:
        raise ManifestError("plugin.json 声明了 agents 字段 → 实测 2.1.206 下 agent 全不注册"
                            "（插件仍加载、plugin validate 仍 ✔，但 Agents(0)）；"
                            "删掉该字段、靠 agents/ 目录自动发现")


def _collect(root):
    """跑完所有检查，返回 (errs, fm)。每项独立 try，好让一次跑出全部漂移而非只报第一条。"""
    errs, fm = [], {}
    try:
        fm = _parse_frontmatter(os.path.join(root, "AGENTS.md"))
    except ManifestError as ex:
        return [str(ex)], {}

    for req in ("name", "description"):
        if not fm.get(req):
            errs.append(f"AGENTS.md frontmatter 缺 {req}")

    for label, disk_fn in (("agents", _disk_agents), ("skills", _disk_skills)):
        try:
            declared = set(_names(fm, label))
            d = _diff(label, declared, disk_fn(root))
            if d:
                errs.append(d)
        except ManifestError as ex:
            errs.append(str(ex))

    try:
        _check_plugin_json(root)
    except ManifestError as ex:
        errs.append(str(ex))
    return errs, fm


def main(argv):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(
        description="AGENTS.md ↔ 文件系统 同步校验（plugin.json 仅校验「不得声明 agents」）")
    ap.add_argument("--plugin-root", default=os.path.dirname(here))
    a = ap.parse_args(argv)

    errs, fm = _collect(a.plugin_root)
    for e in errs:
        print(f"  ✗ {e}")
    if not errs:
        print(f"  AGENTS.md(name={fm.get('name')}): agents={fm.get('agents')} skills={fm.get('skills')} "
              f"↔ 磁盘一致（plugin.json 未声明 agents ✓）")
    print(f"STATUS: {'SYNCED' if not errs else 'DRIFT'}")
    return 0 if not errs else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
