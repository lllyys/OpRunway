"""验收入口文本一致性门：防止真机通路、源码身份门和历史结果口径回退。

只读、stdlib、fail-closed。它不解析 Markdown 语义，只守最容易造成错误执行或错误宣称的
少量硬约束；历史记录允许保留，但不得在活跃入口里重新宣称旧 Median PASS。
"""
import argparse
import os
import re
import sys


ENTRYPOINTS = (
    "AGENTS.md",
    "agents/op-acceptance.md",
    "agents/acc-verify-rootcause.md",
    "commands/op-acceptance.md",
    "skills/acceptance-workflow/SKILL.md",
    "skills/acc-runner/SKILL.md",
    "workflows/development-guide.md",
    "workflows/task-prompts.md",
)

# 仓根 `AGENTS.md`（**唯一仓规源**）不在 plugin 树内，但它带着 `run_workflow.py` 主入口命令块，
# 与上面那批同属「照着抄就会去执行」的文本，必须同门守。相对**仓根**解析（= `dirname(plugin_root)`）。
REPO_ROOT_ENTRYPOINTS = ("AGENTS.md",)

SOURCE_GATE_FILES = (
    "agents/acc-verify-rootcause.md",
    "skills/acceptance-workflow/SKILL.md",
)

SOURCE_GATE_TOKENS = (
    "SOURCE_ACQUIRED",
    "HEAD_VERIFIED",
    "BUILD_VERIFIED",
    "WORKFLOW_STARTED",
    "set -Eeuo pipefail",
)

_STALE_PASS = re.compile(r"Median.{0,160}(?:56/56|60/60).{0,80}PASS", re.IGNORECASE)
_HISTORICAL_MARKERS = ("历史", "旧", "不得沿用", "不再", "取代", "失效")

# ── CP-D 调用模板必带 `--source-facts` ──────────────────────────────────────
# `run_workflow.run()` 在**验收通路**上把 `--source-facts` 定为必填（缺席即 SystemExit，且拒在
# `os.makedirs` / staging / Task1 之前）。仓内文档里的调用模板一旦漏了它，照抄的那条正式验收
# 链**一个产物都产不出来**——代码里的门是真的，文档里的入口却已不可用。这类漂移不会被 pytest
# 抓到（NL 文本不进解释器），所以在这里机械守住。
#
# ⚠ 只认**调用模板**，不认散文提及。判据两条，命中任一即算模板：
#   ① 出现 `--out`（真正可照抄执行的完整命令，一定带落点）；
#   ② 出现 `--mode <占位符>`（CP-D dispatch 的缩写式；`<` 表示后面跟的是占位符而非具体值）。
# 故意**不**匹配的（真实存在、且不该被要求带 `--source-facts`）：
#   · `` `run_workflow.py --mode` `` 这类只点名 flag 的表头单元格（`--mode` 后面没有占位符）；
#   · `spec.runner_form` 派生表里的 `--mode cpp_extension` / `--mode new_example`（具体值、非模板）；
#   · `run_workflow.py --gpu-baseline <…>`（讲另一个 flag，不是 CP-D 主命令）；
#   · 纯引用式提及（「`run_workflow.py` 已内嵌此门」）。
_RUN_WORKFLOW = "run_workflow.py"
# 必须是**独立参数**：`--source-facts-note` / `--source-factsX` 这类同前缀假 token 不得顶替，
# 否则「写个像那么回事的词」就能骗过本门。`--out` 同理。
_SOURCE_FACTS_FLAG = re.compile(r"--source-facts(?=$|[\s=`'\"，。）)])")
_OUT_FLAG = re.compile(r"--out(?=$|[\s=`'\"，。）)])")
_MODE_PLACEHOLDER = re.compile(r"--mode\s+<")
# 明确跑非验收通路的完整命令：`run_workflow` 对 mock / catlass* 不要求 `--source-facts`
# （那条路物理上不产 acceptance.json / verdict.json，也没有来源锚要对账）。
# ⚠ **刻意不拿 `--allow-experimental-form` 当豁免依据**：解释「非验收通路不受此强制」的散文里
#   就带着这个词，拿它豁免等于给自己开后门——在真模板旁边写一句说明，本门就对整行闭嘴了。
#   代价是：真要在这批文件里写一条 `--allow-experimental-form` 的开发级完整命令，本门会**过严**地
#   也要求它带 `--source-facts`。宁可过严（吵一句）也不留后门（静默放行），要改就连测试一起改。
_NON_ACCEPTANCE_MODE = re.compile(r"--mode\s+(?:mock|catlass_mock|catlass)\b")


def _invocation_segments(line):
    """按 `run_workflow.py` 出现处切段，每段 = 一次调用到下一次调用（或行尾）。

    整行子串搜索的漏洞：同一逻辑行上写两条命令、只有后一条带 `--source-facts` 时，
    整行搜索会认为两条都合格。切段后各查各的。
    """
    return line.split(_RUN_WORKFLOW)[1:]


def _logical_lines(text):
    """把 shell 续行（行尾 `\\`）折成一条逻辑行，返回 (起始行号, 逻辑行) 列表。

    仓根 `AGENTS.md` 的主入口是多行 bash 块，`run_workflow.py`、`--out` 与 `--source-facts`
    分散在三行上；不折行就会把一条完整命令误判成三条残片。
    """
    out = []
    buf, start = None, None
    for lineno, line in enumerate(text.splitlines(), 1):
        if buf is None:
            buf, start = line, lineno
        else:
            buf = buf + " " + line.strip()
        if buf.rstrip().endswith("\\"):
            buf = buf.rstrip()[:-1]      # 去掉续行符本身，继续吃下一行
            continue
        out.append((start, buf))
        buf, start = None, None
    if buf is not None:                   # 文件以续行符结尾：照样收进来，不吞
        out.append((start, buf))
    return out


def _check_source_facts_flag(rel, text, errors):
    for lineno, line in _logical_lines(text):
        for seg in _invocation_segments(line):
            if not (_OUT_FLAG.search(seg) or _MODE_PLACEHOLDER.search(seg)):
                continue                  # 散文提及，不是调用模板
            # 明确写死跑 mock / catlass* 的完整命令才豁免。带占位符的仍是 CP-D 模板，不豁免——
            # 否则「模板里顺手提一句 mock」就能让本门失效。
            if _NON_ACCEPTANCE_MODE.search(seg) and not _MODE_PLACEHOLDER.search(seg):
                continue
            if not _SOURCE_FACTS_FLAG.search(seg):
                errors.append(
                    f"{rel}:{lineno}: run_workflow.py 调用模板缺 --source-facts"
                    f"（验收通路必填，缺席即拒跑；路径 = CP-A fetch_source.py --out 那份 source_facts.json）")


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeError) as ex:
        raise RuntimeError(f"读取失败 {path}: {ex}") from ex


def collect(plugin_root, repo_root=None):
    """`repo_root` 缺省 = `dirname(plugin_root)`（仓布局见仓根 AGENTS.md §6）。

    仓根 `AGENTS.md` 与 plugin 树里那批一样**必读必存在**：读不到就记 error（fail-closed），
    不做“文件在才校”的可选处理——那等于谁删掉谁就免检。
    """
    if repo_root is None:
        repo_root = os.path.dirname(os.path.abspath(plugin_root))
    errors = []
    texts = {}
    for rel in ENTRYPOINTS:
        path = os.path.join(plugin_root, rel)
        try:
            texts[rel] = _read(path)
        except RuntimeError as ex:
            errors.append(str(ex))
    for rel in REPO_ROOT_ENTRYPOINTS:
        path = os.path.join(repo_root, rel)
        try:
            texts[f"<仓根>/{rel}"] = _read(path)
        except RuntimeError as ex:
            errors.append(str(ex))

    for rel, text in texts.items():
        if "两条真机通路" in text:
            errors.append(f"{rel}: 仍写“两条真机通路”")
        for lineno, line in enumerate(text.splitlines(), 1):
            if _STALE_PASS.search(line) and not any(x in line for x in _HISTORICAL_MARKERS):
                errors.append(f"{rel}:{lineno}: 活跃文本仍宣称旧 Median PASS")
        _check_source_facts_flag(rel, text, errors)

    for rel in SOURCE_GATE_FILES:
        text = texts.get(rel, "")
        for token in SOURCE_GATE_TOKENS:
            if token not in text:
                errors.append(f"{rel}: 缺源码执行门 token {token!r}")
    return errors


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="验收入口文本一致性门")
    ap.add_argument("--plugin-root", default=os.path.dirname(here))
    args = ap.parse_args(argv)
    try:
        errors = collect(args.plugin_root)
    except Exception as ex:  # 门本身不得 traceback 后被误读成通过
        errors = [f"门执行失败: {ex}"]
    for error in errors:
        print(f"  ✗ {error}")
    print(f"STATUS: {'SYNCED' if not errors else 'DRIFT'}")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
