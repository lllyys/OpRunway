"""验收入口文本一致性门：防止真机通路、源码身份门和历史结果口径回退。

只读、stdlib、fail-closed。它不解析 Markdown 语义，只守最容易造成错误执行或错误宣称的
少量硬约束；历史记录允许保留，但不得在活跃入口里重新宣称旧 Median PASS。

当前守四类：
  ① 「两条真机通路」这类口径回退；
  ② 活跃入口里重新宣称旧 Median PASS；
  ③ `run_workflow.py` 调用模板漏 `--source-facts`（验收通路上缺席即拒跑）；
  ④ **已退役 runner form / mode 的派生关系与调用**（`cpp` / `aclnn_py` → `--mode new_example`
     / `--mode aclnn_py`，以及已删除的逃生阀 `--allow-experimental-form`）。
③④ 的共同点是：文本照抄下去**做得下去、跑不起来**——代价是整轮昂贵准备白做。
"""
import argparse
import os
import re
import sys


ENTRYPOINTS = (
    "AGENTS.md",
    "agents/op-acceptance.md",
    "agents/acc-verify-rootcause.md",
    "agents/acc-runner-dev.md",
    "agents/acc-spec-extractor.md",
    "commands/op-acceptance.md",
    "skills/acceptance-workflow/SKILL.md",
    "skills/acc-runner/SKILL.md",
    "skills/acc-spec/SKILL.md",
    "acc-common/spec_schema_template.jsonc",
    "workflows/development-guide.md",
    "workflows/task-prompts.md",
)
# ⚠ 后 4 项是 2026-08-06 补进来的，理由是**它们同样会让 agent 去生成或执行一条通路**：
#   `acc-spec/SKILL.md` + `acc-spec-extractor.md` + `spec_schema_template.jsonc` 决定
#   `spec.runner_form` 抽成什么值，`acc-runner-dev.md` 决定 CP-C 派哪个 dispatch_mode。
#   本门原先只守「跑」那一端，抽 spec 那端漏在外面：spec skill 指示可选 `aclnn_py`、workflow
#   照着往下派、最后在 `_resolve_mode` 被拒——昂贵准备全白做。**产 form 的地方和用 form 的地方
#   必须同门守**，只守一端等于没守。

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
# ⚠ **豁免只认「显式写死跑 mock / catlass*」这一种形式**，不认任何散文里的说明词。
#   历史上这里另有一句：刻意不拿 `--allow-experimental-form` 当豁免依据——解释「非验收通路不受
#   此强制」的散文里就带着那个词，拿它豁免等于给自己开后门（在真模板旁边写一句说明，本门就对
#   整行闭嘴了）。那个 flag 已于 2026-08-06 随通路收敛删除，但这条判据的**取舍照旧成立**：
#   宁可过严（吵一句）也不留后门（静默放行）。下一个逃生阀若以别的名字回来，同样不得当豁免依据。
_NON_ACCEPTANCE_MODE = re.compile(r"--mode\s+(?:mock|catlass_mock|catlass)\b")

# ── 已退役 runner form / mode 不得在活跃编排文本里被派生或调用 ────────────────────
# 2026-08-06 通路收敛后，`_RUNNER_FORM_TO_MODE` 只剩 `cpp_extension` 一条，逃生阀
# `--allow-experimental-form` 一并删除。但**代码收敛不会自动收敛 NL 文本**：仓内当时仍写着
# 「`cpp` → `--mode new_example`、`aclnn_py` → `--mode aclnn_py`」「跑不了就加 `--allow-experimental-form`」，
# 而 agent 是**照着这些活跃指令**去抽 spec、配环境、跑 CP-C 的——一路做完昂贵准备，最后在
# `_resolve_mode` 撞门。这正是本轮要消灭的那条「准备得下去、最后跑不了」的死路，所以在这里机械守住。
#
# 守的是**派生关系与调用**，不是「提到这两个词」：能力表、受控词表、历史归因照旧要写得出来。
_RETIRED_FORM = r"(?<![\w-])(?:cpp|aclnn_py)(?![\w-])"        # `cpp_extension` 被 lookahead 排除
_RETIRED_MODE = r"(?<![\w-])(?:new_example|aclnn_py)(?![\w-])"

# ① 箭头式派生：`cpp` → `--mode new_example` / `aclnn_py→aclnn_py`（含裸箭头与反引号包裹）。
#    箭头左边留 24 字的缓冲，因为真实写法常带补语——「cpp runner v1 → `--mode new_example`」、
#    「`runner_form==aclnn_py`（torch 对标）→ `--mode aclnn_py`」。缓冲里禁 `|` 与换行：
#    表格单元格与跨行不得被串成一条派生（那会把「| `cpp` | （无）|」误判）。
_RETIRED_ARROW = re.compile(
    _RETIRED_FORM + r"[^|\n]{0,24}?(?:→|⇒|->|=>)\s*`?(?:--mode[= ]\s*)?`?" + _RETIRED_MODE)
# ② 列举式派生：「依次派生 `{new_example, aclnn_py, cpp_extension}`」。三个判据缺一不可：
#    · 只认 `new_example`——它**只可能是 mode**；`aclnn_py` 同时是 form 名，受控词表
#      `{cpp, aclnn_py, cpp_extension}` 合法且到处都在写，拿它当判据必然误报；
#    · 必须是**集合字面量**（紧跟 `{`/`(`/`[` 一类），否则「派生表**无条目**…显式 `--mode new_example` 也拒」
#      这种**否定句**会被判成派生；
#    · `派生` 与集合之间只给 20 字，隔太远就不是同一件事了。
#    ⚠ 反过来，`_REAL_MACHINE_MODES = {new_example, aclnn_py, cpp_extension}` 这类**代码常量引用**
#      同行不带 `派生`，照旧写得出来——那张表必须保留全部三项（入口门靠它认出绕行）。
_RETIRED_DERIVE_LIST = re.compile(
    r"派生[^。\n]{0,20}?[{（(\[][^}）)\]\n]*?(?<![\w-])new_example(?![\w-])")
# ③ 表格式派生：`| aclnn_py | aclnn_py |` —— 第二格**整格**就是退役 mode 才算。
#    停止准入后这一格该写成「（无）」，写着 mode 就是还在告诉 agent 有入口。
_RETIRED_TABLE_ROW = re.compile(
    r"\|\s*`?" + _RETIRED_FORM + r"`?[^|]*\|\s*`?" + _RETIRED_MODE + r"`?\s*\|")
# ④ 直接调用退役 mode / 逃生阀
_RETIRED_MODE_FLAG = re.compile(r"--mode[= ]\s*`?" + _RETIRED_MODE)
_EXPERIMENTAL_FLAG = re.compile(r"--allow[-_]experimental[-_]form(?![\w-])")

# ①②③ 无条件拒（那三种写法只可能是在教人怎么派生），④ 分两档：
#   · 落在 `run_workflow.py` 调用段里 = 可照抄执行的命令 → **无条件拒**，措辞救不了它；
#   · 散文里点名 → 须同逻辑行带退役声明，否则拒。
# ⚠ 这里的关键词豁免是**有意的次优解**，如实记账：它防的是「文本还在教人跑退役通路」，
#   防不住「写句『已拒』再照贴命令」。所以调用段那一档不给任何豁免——真正能被照抄执行的
#   那种写法必须硬拒。下一个人要加固，方向是收紧词表或改用下面的历史区，不是把这两档合并放宽。
_RETIRED_STATEMENT_MARKERS = (
    "⛔", "拒", "停止准入", "已删", "删除", "删掉", "无真机入口", "退役", "历史保留", "历史留档")
_FLAG_REMOVED_MARKERS = ("已删", "删除", "删掉", "退役", "别加回", "不得加回", "历史保留", "历史留档")

# ── 历史区：退役机制的描述留着（有参考价值），但要让 agent 一眼看出「不要照做」──────
# 区块内豁免上面的退役规则；代价是必须挂横幅，且**未闭合的区块一律不豁免**（fail-closed）。
# ⚠ `--source-facts` 那道门**不随历史区豁免**：区块里别放完整的 `run_workflow.py` 调用模板，
#   要讲机制就用散文讲。理由是历史区一旦能藏可照抄的命令，它就成了绕开主门的新入口。
_REGION_BEGIN = "<!-- oprunway:retired-begin -->"
_REGION_END = "<!-- oprunway:retired-end -->"
_REGION_BANNER = "⛔ 历史留档 · 不得 dispatch · 不要照做"


def _retired_region_lines(rel, text, errors):
    """返回落在历史区内的行号集合；区块本身不合法时记 error 且**不给豁免**。"""
    inside = set()
    lines = text.splitlines()
    open_at = None
    for lineno, line in enumerate(lines, 1):
        has_begin = _REGION_BEGIN in line
        has_end = _REGION_END in line
        if has_begin and has_end:
            errors.append(f"{rel}:{lineno}: 历史区起止标记写在同一行（区块边界必须自明）")
            continue
        if has_begin:
            if open_at is not None:
                errors.append(f"{rel}:{lineno}: 历史区未闭合就再次开启（上一处起于第 {open_at} 行）")
            open_at = lineno
            continue
        if has_end:
            if open_at is None:
                errors.append(f"{rel}:{lineno}: 历史区收尾标记没有对应的开启标记")
                continue
            body = "\n".join(lines[open_at:lineno - 1])
            if _REGION_BANNER not in body:
                errors.append(f"{rel}:{open_at}: 历史区缺横幅 {_REGION_BANNER!r}（没横幅就不算历史区）")
            else:
                inside.update(range(open_at, lineno + 1))
            open_at = None
    if open_at is not None:
        errors.append(
            f"{rel}:{open_at}: 历史区开启后没有闭合"
            f"（fail-closed：未闭合区块不豁免任何规则，否则开一个标记就能让整个文件免检）")
    return inside


def _check_retired_dispatch(rel, text, errors):
    inside = _retired_region_lines(rel, text, errors)
    for lineno, line in _logical_lines(text):
        if lineno in inside:
            continue
        for pattern, what in (
            (_RETIRED_ARROW, "退役 runner form → 退役 --mode 的派生关系"),
            (_RETIRED_DERIVE_LIST, "把退役 mode 列进 `--mode` 派生集合"),
            (_RETIRED_TABLE_ROW, "派生表里仍给退役 form 配了一个 --mode（该格应为「（无）」）"),
        ):
            if pattern.search(line):
                errors.append(
                    f"{rel}:{lineno}: 活跃文本仍写{what}"
                    f"（`cpp` / `aclnn_py` 2026-08-06 停止准入，`_RUNNER_FORM_TO_MODE` 只剩 `cpp_extension`；"
                    f"要留机制描述就整段挪进历史区 {_REGION_BEGIN}…{_REGION_END}）")
        segments = _invocation_segments(line)
        for pattern, markers, what in (
            (_RETIRED_MODE_FLAG, _RETIRED_STATEMENT_MARKERS, "退役 mode 的 `--mode` 调用"),
            (_EXPERIMENTAL_FLAG, _FLAG_REMOVED_MARKERS, "已删除的逃生阀 `--allow-experimental-form`"),
        ):
            if not pattern.search(line):
                continue
            if any(pattern.search(seg) for seg in segments):
                errors.append(
                    f"{rel}:{lineno}: `run_workflow.py` 调用段里出现{what}"
                    f"（可照抄执行的命令一律拒，措辞不豁免）")
            elif not any(m in line for m in markers):
                errors.append(
                    f"{rel}:{lineno}: 活跃文本仍指示{what}"
                    f"（要么写明它已停止准入/已删除，要么整段挪进历史区 {_REGION_BEGIN}…{_REGION_END}）")


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
        _check_retired_dispatch(rel, text, errors)

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
