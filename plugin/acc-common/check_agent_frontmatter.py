"""agent 结构合规校验（P1 编排升级配套）——把「薄 primary + 3 subagent」的**统一命名契约**
落成机器可校验的 dev/CI meta-lint，避免 subagent「单轮 / 禁内部循环 / 不自行判定」等纪律**只靠散文门**。

本脚本与 `check_manifest_sync.py` 同类：**只读、stdlib、抗坏输入**，**不进 `run_workflow.py` 的判定/执行链**、
不违 ADR 0007（判定唯一归确定性脚本链 validator/perf_compare/acceptance gate；本 lint 只管「结构合规」）。
（注：与 `check_manifest_sync.py` 各自内含一个受限 frontmatter 解析器——两者语义已分叉；将来若要统一，
应抽一个同目录共享 parser 并给两脚本共用坏输入用例。此为 codex 审的 LOW 项，暂不动稳定的 check_manifest_sync。）

校验内容（逐字对齐命名契约，不认别名）：
  - `op-acceptance.md`（primary）：frontmatter `mode: primary` + `skills` **恰为** `[acceptance-workflow]`
    + `agents` **恰为** 3 个 child（`acc-spec-extractor` / `acc-runner-dev` / `acc-verify-rootcause`，
    不含自己、不重复、无多缺）。
  - 3 个 subagent：frontmatter `mode: subagent` + 正文含其 `dispatch_mode` 两个取值
    （extract_spec/refine_spec、gen_runner/verify_runner、run_npu/rootcause）+ 禁用短语存在
    （正文含「单轮」且含「不自行判定」或等价）。
  - `plugin/agents/*.md` 里**任何**文件 frontmatter 坏（未闭合 / 缺 frontmatter / 畸形行 / 不可读）→ 判 FAIL，
    不静默放过（契约外的多余 agent 也解析，只是不做结构字段校验）。

用法: python3 check_agent_frontmatter.py [--plugin-root <plugin 根>]
默认 plugin 根 = 本脚本(acc-common)的上一级。打印逐项结果 + `STATUS: PASS|FAIL`，exit 0/1。
缺 frontmatter / 坏 yaml / 缺字段 / 缺文件 / 不可读 → 记 error 判 FAIL、**不崩溃**。
"""
import argparse, glob, os, sys


# ── 命名契约（唯一事实源，改契约先改这里）─────────────────────────────
PRIMARY = "op-acceptance"                 # 薄编排器（mode: primary）
PRIMARY_SKILLS = ["acceptance-workflow"]  # primary 只挂 workflow skill（原子 skill 下沉 subagent）
# subagent 契约表（单一事实源：name + dispatch_mode 两取值；CHILDREN 由它派生，避免分离维护漂移）
SUBAGENTS = [
    {"name": "acc-spec-extractor", "dispatch": ["extract_spec", "refine_spec"]},
    {"name": "acc-runner-dev", "dispatch": ["gen_runner", "verify_runner"]},
    {"name": "acc-verify-rootcause", "dispatch": ["run_npu", "rootcause"]},
]
CHILDREN = [s["name"] for s in SUBAGENTS]          # 3 个 child subagent（派生）
SUB_DISPATCH = {s["name"]: s["dispatch"] for s in SUBAGENTS}
SINGLE_ROUND = "单轮"                               # 禁用短语之一（单轮，禁内部循环）
# 「不自行判定」及其等价说法（命中任一即可）——broad matcher「不自行判」同时覆盖「不自行判 pass/fail」
NO_JUDGE_ALTS = ["不自行判", "不自行 judge", "不自行宣告", "不自主判定"]

# 三种缺失/坏输入用不同 sentinel 表达（供调用方精确报错、不互相吞）：
#   fm=None, err=None            → 文件缺失
#   fm=None, err=<str>           → 文件不可读（OSError）
#   fm={} 或 dict, err=<str>     → frontmatter 坏（缺/未闭合/畸形行）
#   fm=dict, err=None            → 正常


def _read_agent(path):
    """解析 agent .md，返回 (fm, body, err)。解析器风格沿用 check_manifest_sync（flow/block list、注释、标量）。
    err 非 None 一律视作「本文件不合规」，调用方**先看 err**（在 fm is None 之前），避免把不可读误报成「缺文件」。"""
    if not os.path.exists(path):
        return None, "", None  # 文件缺失（err=None 区别于不可读）
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as ex:
        return None, "", f"读文件失败：{ex}"  # 不可读（err 非空）
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text, "缺 frontmatter（首行非 ---）"  # 明确报缺 frontmatter，而非静默当普通字符串
    fm, cur, end_idx, malformed = {}, None, None, []
    for i, ln in enumerate(lines[1:], start=1):  # i 与原始行号一致（lines[i]）
        if ln.strip() == "---":
            end_idx = i
            break
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        if not ln[:1].isspace():  # 顶层行（不缩进）
            if ":" in s:  # 顶层 key
                key, _, rest = s.partition(":")
                key, rest = key.strip(), rest.strip()
                if rest.startswith("[") and rest.endswith("]"):  # flow list
                    fm[key] = [x.strip().strip("'\"") for x in rest[1:-1].split(",") if x.strip()]
                    cur = None
                elif rest:  # 标量
                    fm[key] = rest.strip("'\"")
                    cur = None
                else:  # 期待后续 block list
                    fm[key] = []
                    cur = key
            else:  # 不缩进、非注释/空、又无冒号 → 畸形 frontmatter 行（不静默吞）
                malformed.append(f"L{i+1}:{s[:40]}")
                cur = None
        elif cur is not None and s.startswith("- "):  # block list 项
            fm[cur].append(s[2:].strip().strip("'\""))
        else:
            cur = None  # 缩进但非当前列表项 → 退出列表上下文
    if end_idx is None:  # frontmatter 未闭合 → 视作坏 yaml
        return fm, "", "frontmatter 未闭合（缺结束 ---）"
    if malformed:
        return fm, "\n".join(lines[end_idx + 1:]), "frontmatter 畸形行：" + "、".join(malformed)
    return fm, "\n".join(lines[end_idx + 1:]), None


def _as_list(v):
    """把 frontmatter 值规整为 list（非 list → 空 list，便于抗坏输入比较）。"""
    return v if isinstance(v, list) else []


def _dups(seq):
    """返回 seq 中重复出现的元素（去重后）。"""
    seen, dup = set(), []
    for x in seq:
        if x in seen and x not in dup:
            dup.append(x)
        seen.add(x)
    return dup


def _check_primary(fm, body, err, rec):
    """校验 primary（op-acceptance）：mode:primary + skills 恰为 [acceptance-workflow] + agents 恰为 3 child（不重复）。"""
    if err:  # 先看 err（不可读 / 缺 frontmatter / 畸形），别被 fm is None 的「缺文件」吞掉
        rec(False, f"{PRIMARY}.md 解析失败：{err}")
        return
    if fm is None:
        rec(False, f"缺 agents/{PRIMARY}.md（primary 编排器）")
        return
    mode = fm.get("mode")
    rec(mode == "primary", f"mode: {mode!r}（须 'primary'）")
    skills = _as_list(fm.get("skills"))
    rec(skills == PRIMARY_SKILLS,
        f"skills={skills}（须恰为 {PRIMARY_SKILLS}，原子 skill 下沉 subagent）")
    agents = _as_list(fm.get("agents"))
    dup = _dups(agents)
    want = set(CHILDREN)
    got = set(agents)
    # 恰为 3 child：数量对（防重复凑数）+ 集合相等（顺序无关）+ 无重复
    if len(agents) == len(CHILDREN) and got == want and not dup:
        rec(True, f"agents 恰为 3 child：{agents}")
    else:
        missing = sorted(want - got)
        extra = sorted(got - want)
        detail = []
        if missing:
            detail.append(f"缺 {missing}")
        if extra:
            detail.append(f"多 {extra}")
        if dup:
            detail.append(f"重复 {dup}")
        if PRIMARY in got:
            detail.append("含自己(child_agents 不应含 primary)")
        rec(False, f"agents={agents}（须恰为 3 child {CHILDREN}；{'；'.join(detail) or '数量不符'}）")


def _check_subagent(name, fm, body, err, rec):
    """校验单个 subagent：mode:subagent + 正文含 dispatch_mode 两取值 + 禁用短语（单轮 + 不自行判定/等价）。"""
    if err:  # 先看 err
        rec(False, f"{name}.md 解析失败：{err}")
        return
    if fm is None:
        rec(False, f"缺 agents/{name}.md（child subagent）")
        return
    mode = fm.get("mode")
    rec(mode == "subagent", f"mode: {mode!r}（须 'subagent'）")
    # dispatch_mode 两个取值须在正文出现
    for dm in SUB_DISPATCH[name]:
        rec(dm in body, f"正文含 dispatch_mode 取值 {dm!r}")
    # 禁用短语：正文含「单轮」且含「不自行判定」或等价
    has_single = SINGLE_ROUND in body
    hit_alt = next((a for a in NO_JUDGE_ALTS if a in body), None)
    rec(has_single and hit_alt is not None,
        f"禁用短语：含「{SINGLE_ROUND}」={has_single}、含「不自行判定/等价」={hit_alt or '无'}")


def _stem(p):
    b = os.path.basename(p)
    return b[:-3] if b.endswith(".md") else b


def main(argv):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="agent frontmatter 结构合规 meta-lint（P1 命名契约）")
    ap.add_argument("--plugin-root", default=os.path.dirname(here), help="plugin 根（默认=acc-common 上一级）")
    a = ap.parse_args(argv)
    agents_dir = os.path.join(a.plugin_root, "agents")

    results = []  # (ok, msg) —— 汇总判 PASS/FAIL
    notes = []

    def make_rec(prefix):
        def rec(ok, msg):
            results.append((ok, f"{prefix} {msg}"))
        return rec

    if not os.path.isdir(agents_dir):
        print(f"  ✗ 缺 agents 目录：{agents_dir}")
        print("STATUS: FAIL")
        return 1

    # primary
    try:
        fm, body, err = _read_agent(os.path.join(agents_dir, PRIMARY + ".md"))
        _check_primary(fm, body, err, make_rec(f"[{PRIMARY}·primary]"))
    except Exception as ex:  # 兜底：任何意外都记 error、不崩溃
        results.append((False, f"[{PRIMARY}·primary] 校验异常：{ex}"))

    # 3 个 subagent
    for name in CHILDREN:
        try:
            fm, body, err = _read_agent(os.path.join(agents_dir, name + ".md"))
            _check_subagent(name, fm, body, err, make_rec(f"[{name}·subagent]"))
        except Exception as ex:
            results.append((False, f"[{name}·subagent] 校验异常：{ex}"))

    # 契约外的多余 agent 文件：仍解析 frontmatter，坏 → FAIL（不做结构字段校验，只查可解析）
    expected = set([PRIMARY] + CHILDREN)
    for p in sorted(glob.glob(os.path.join(agents_dir, "*.md"))):
        stem = _stem(p)
        if stem in expected:
            continue
        try:
            fm, body, err = _read_agent(p)
            if err:
                results.append((False, f"[{stem}·extra] frontmatter 坏（契约外文件也须可解析）：{err}"))
            else:
                notes.append(f"agents/{stem}.md 不在命名契约内（frontmatter 可解析，仅提示、不判 FAIL）")
        except Exception as ex:
            results.append((False, f"[{stem}·extra] 解析异常：{ex}"))

    # 打印逐项结果
    for ok, msg in results:
        print(f"  {'✓' if ok else '✗'} {msg}")
    for n in notes:
        print(f"  · 备注：{n}")

    ok = all(o for o, _ in results)
    print(f"STATUS: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
