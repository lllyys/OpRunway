"""doc_style_lint 的判据测试。

纯逻辑，能用字符串跑完，按仓根 CLAUDE.md 的分界线该补测试。
重点在**误报**：判据误报就会被忽略，被忽略的门禁等于没有。
"""

import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import doc_style_lint as lint             # noqa: E402


def prose(text, name="SKILL.md"):
    return lint.check_prose(pathlib.Path(name), text)


def rules(findings):
    return sorted(f[2] for f in findings)


# ------------------------------------------------------------------ 命中


@pytest.mark.parametrize("line, rule", [
    ("## 报告怎么读", "ZH-S4"),
    ("## 什么时候该翻源码", "ZH-S4"),
    ("### --soc 怎么定", "ZH-S4"),
    ("## 它比 ATK 强在哪", "ZH-S4"),
    ("## 它的灵魂：不许自己想办法", "ZH-S4"),
    ("找不到我们的 so。", "ZH-W5"),
    ("环境不满足时适当处理。", "ZH-W5"),
    ("必要时回到上一步。", "ZH-W5"),
    ("这个字段将会被移除。", "ZH-W5"),
    ("简单地执行一遍就行。", "ZH-W5"),
    ("显然这里无需处理。", "ZH-W5"),
    ("在 Ascend910 新机(CANN 9.0.0)上实测。", "ZH-P2"),
    # 新规则
    ("出口判据是退出码 0。", "ZH-W1"),
    ("npu 剖面下受限的字段。", "ZH-W1"),
    ("重跑碰不对。", "ZH-W3"),
    ("脚本跑完再看。", "ZH-W3"),
    ("曾按 backend 一刀切处理。", "ZH-T2"),
    ("进S1之前先检查。", "ZH-P1"),
    ("退出码 0 。", "ZH-P3"),
    ("读 `a.py` ，再继续。", "ZH-P3"),
    ("不要手工搬这些文件。", "ZH-S2"),
])
def test_catches(line, rule):
    assert rule in rules(prose(line)), f"漏判：{line}"


def test_longest_word_wins():
    """「跑测侧」只报一次 ZH-W1，不再叠报「跑测」与「跑」。"""
    found = prose("交给跑测侧处理。")
    assert rules(found) == ["ZH-W1"], found


def test_synonym_from_glossary(tmp_path):
    glossary = tmp_path / "zh-writing.md"
    glossary.write_text("# 中文\n\n## 术语表\n\n| 术语 | 定义 | 禁用同义词 |\n"
                        "| --- | --- | --- |\n| 标杆 | 参考实现 | 基线、baseline |\n"
                        "| 用例包 | 目录 | — |\n\n## 其他\n\n| a | b | 不收 |\n",
                        encoding="utf-8")
    syn = lint.load_synonyms(glossary)
    assert syn == {"基线": "标杆", "baseline": "标杆"}
    found = lint.check_prose(pathlib.Path("SKILL.md"), "基线是 torch 接口。", syn)
    assert "ZH-W2" in rules(found)


def test_long_sentence_joined_across_hard_wraps():
    text = ("这是一句被硬换行拆开的很长的句子它在源文件里分成了三行但读者读到的仍然是\n"
            "一整句话所以检查时必须先把段落拼回去再按句号断句否则每一行都不超过阈值\n"
            "而整句早就超过了六十个字的上限。\n")
    assert "ZH-S1" in rules(prose(text))


def test_bold_density_per_section():
    text = "## 节\n\n**一** **二** **三** **四**\n\n## 下一节\n\n**五**\n"
    found = [f for f in prose(text) if f[2] == "ZH-T1"]
    assert [f[1] for f in found] == [1]


@pytest.mark.parametrize("path, rule, want", [
    ("skill/repo-task-case-gen/SKILL.md", "ZH-W1", True),
    ("skill/cann-env-setup/SKILL.md", "ZH-W1", False),
    ("skill/cann-env-setup/SKILL.md", "ZH-W5", True),
    ("docs/guide/x.md", "ZH-W1", False),
    ("skill/repo-task-case-gen/SKILL.md", "ZH-S1", False),
])
def test_enforcement_by_migration(path, rule, want, monkeypatch):
    monkeypatch.setattr(lint, "MIGRATED", {"repo-task-case-gen"})
    assert lint.enforced(pathlib.Path(path), rule) is want


def test_structure_checks(tmp_path, monkeypatch):
    skill = tmp_path / "skill" / "demo-skill"
    (skill / "references").mkdir(parents=True)
    (skill / "references" / "used.md").write_text("# U\n", encoding="utf-8")
    (skill / "references" / "orphan.md").write_text("# O\n", encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "---\nname: Demo_Skill\ndescription: >-\n  做什么，何时用。\n---\n\n"
        "# D\n\n见 [u](references/used.md)。读 `gen.py:12`。\n", encoding="utf-8")
    monkeypatch.setattr(lint, "MIGRATED", {"demo-skill"})
    found = lint.check_structure(skill / "SKILL.md")
    assert sorted(f[2] for f in found) == ["SA-01", "SA-05", "SA-12"], found


def test_structure_passes_clean_skill(tmp_path):
    skill = tmp_path / "skill" / "demo-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: 做什么，何时用。\n---\n\n# D\n", encoding="utf-8")
    assert lint.check_structure(skill / "SKILL.md") == []


# ------------------------------------------------------------------ 误报


@pytest.mark.parametrize("text", [
    # 代码块里的一切都不判：日志、字典、路径
    "```\n它自己 source，我们的 so，适当\nos.path.join(a,b)\n```",
    # 行内代码里的半角标点是对的
    "调用 `f(a, b)` 之后读 `d[\"k\"]`。",
    # 链接目标里的括号与逗号不判
    "见 [说明](https://x.com/a(b),c) 一节。",
    # 引用块是贴出来的上游原文或社区回复，不按本仓行文判
    "> 请设置 export ASCEND_GLOBAL_LOG_LEVEL=1 再重试(见文档)",
    # 纯英文语境的半角标点是对的
    "Use `--devices 0,1` and set `mode=fast`.",
    # 名词短语标题不判
    "## 失败归因\n## 节点拓扑\n### 性能子集抽样",
    # 「它」不带认知动词时是正常的第三人称，不判
    "脚本读 facts.json，它按 kind 分支。",
    # 行内代码里的中英文相邻、自造词都不判
    "字段 `口径` 与 `S1前` 原样保留。",
    # 「」里是引述原话
    "维护者回复「重跑碰不对」。",
    # 中英文之间有空格、英文后直接跟全角标点
    "进 S1 之前先执行 probe_env.py。",
    # 「上一轮」指用户上一次运行，不是开发史
    "工作目录有上一轮的产物时询问用户。",
    # 行内代码与链接后面紧跟全角标点
    "见 [report-spec.md](references/report-spec.md)。",
    # 删掉「」内容后两侧会被拼成「md查」
    "按 atk-surface.md「查不到时的顺序」查找。",
    # 英文词与行内代码之间的空格是 ZH-P1 要求的，代码后紧跟全角标点
    "对应 Python `float`，C 类型。",
])
def test_no_false_positive(text):
    assert prose(text) == [], f"误报：{text!r}\n{prose(text)}"


def test_indent_code_does_not_shift_line_numbers():
    """`\\s{4,}` 会跨行吞空行，让其后每一条发现的行号都错位。"""
    text = "# 标题\n\n    缩进代码一行\n\n\n\n## 报告怎么读\n"
    found = prose(text)
    assert [f[1] for f in found] == [7], f"行号错位：{found}"


# ------------------------------------------------------------------ 引用


def test_xref_prefix_and_backtick(tmp_path):
    """`见 x.md「规模档」` 指向「规模档：字节数…」应算通过；反引号要归一化。"""
    skill = tmp_path / "skill" / "demo"
    (skill / "references").mkdir(parents=True)
    (skill / "references" / "x.md").write_text(
        "# X\n\n## 规模档：字节数，不是元素数\n\n## case_config.id 恒为 0\n",
        encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "# S\n\n见 x.md「规模档」与 x.md「`case_config.id` 恒为 0」。\n",
        encoding="utf-8")
    assert lint.check_xrefs(lint.collect(tmp_path)) == []


def test_xref_detects_real_break(tmp_path):
    skill = tmp_path / "skill" / "demo"
    (skill / "references").mkdir(parents=True)
    (skill / "references" / "x.md").write_text("# X\n\n## 存在的节\n", encoding="utf-8")
    (skill / "SKILL.md").write_text("# S\n\n见 x.md「不存在的节」。\n", encoding="utf-8")
    found = lint.check_xrefs(lint.collect(tmp_path))
    assert [f[2] for f in found] == ["SA-05"]


def test_anchor_keeps_underscore(tmp_path):
    """GitHub 的 slug 保留下划线；删掉会让 `#edge_cases` 永远判成断链。"""
    assert lint.slug("edge_cases") == "edge_cases"
    assert lint.slug("op_summary 解析") == "op_summary-解析"


def test_anchor_detects_real_break(tmp_path):
    skill = tmp_path / "skill" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "# S\n\n## 已实测与待实测边界\n\n- [待实测边界](#待实测边界)\n", encoding="utf-8")
    found = lint.check_xrefs(lint.collect(tmp_path))
    assert [f[2] for f in found] == ["SA-05"]


# ------------------------------------------------------------------ 自动修


def test_fix_punct_only_touches_cjk_context():
    src = "在 Ascend910 新机(CANN 9.0)上实测,用 `f(a, b)` 调用。"
    got = lint.fix_punct(src)
    assert got == "在 Ascend910 新机（CANN 9.0）上实测，用 `f(a, b)` 调用。"


def test_fix_punct_is_idempotent():
    src = "实测(两次),没问题。"
    assert lint.fix_punct(lint.fix_punct(src)) == lint.fix_punct(src)


def test_fix_punct_skips_blockquote():
    """量具与判据同口径：检查器跳过 `>` 行，--fix 也得跳过。

    2026-09-10 实测：--fix 把 docs/guide/issue-workflow.md 引用块里的半角标点
    改了，而同一行检查器判为豁免——同一份规范两个口径。
    """
    src = "> 起草侧读的是跑测台账(见 X),\n> 第二行(a)。\n\n正文(乙),照改。\n"
    got = lint.fix_punct(src)
    assert got == "> 起草侧读的是跑测台账(见 X),\n> 第二行(a)。\n\n正文（乙），照改。\n"


# ------------------------------------------------------------------ 豁免


@pytest.mark.parametrize("name", [
    "golden-task-doc.md", "task-doc-template.md", "aclnn-example.md",
])
def test_samples_and_templates_are_skipped(name):
    """样例与模板的字面内容是契约，被解析器按表头取列、被门禁当合格定义。

    2026-09-04 实测：按行文规范改 golden-task-doc.md 一个标点，
    doc-write 的 9 个测试当场红。
    """
    assert prose("环境不满足时适当处理(见上)。", name) == []


def test_normal_reference_is_not_skipped():
    assert prose("环境不满足时适当处理。", "run-accuracy.md") != []


def test_quoted_speech_is_skipped():
    """「」里是引述的社区回复与报错原话，改它等于篡改样例。"""
    assert prose('- 「我们会修 / PR 进行中」→ `pr_pending`') == []
    assert prose('这里我们会重跑一遍。') != []


def test_heading_keeps_inline_code(tmp_path):
    """标题里的行内代码是标题的一部分，抹掉它会让引用永远判成断链。"""
    skill = tmp_path / "skill" / "demo"
    (skill / "references").mkdir(parents=True)
    (skill / "references" / "x.md").write_text(
        "# X\n\n## 整类 dtype 全挂，报错是 `EZ1001`\n", encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "# S\n\n见 x.md「整类 dtype 全挂，报错是 `EZ1001`」。\n", encoding="utf-8")
    assert lint.check_xrefs(lint.collect(tmp_path)) == []


@pytest.mark.parametrize("src, want", [
    # markdown 强调符夹在括号与中文之间，单侧判据够不着 → 必须成对转
    ("在 **Ascend910 新机(CANN 9.0.0-beta.1)** 上实测。",
     "在 **Ascend910 新机（CANN 9.0.0-beta.1）** 上实测。"),
    ("这正是 P4(`repo_setup.py`)做的事。", "这正是 P4（`repo_setup.py`）做的事。"),
    ("或换其它镜像（NJU / BFSU / SJTU)。", "或换其它镜像（NJU / BFSU / SJTU）。"),
    # 纯英文语境不动
    ("Use func(a, b) here.", "Use func(a, b) here."),
])
def test_parens_convert_as_pairs(src, want):
    assert lint.fix_punct(src) == want


def test_fix_punct_leaves_no_mixed_pairs():
    src = "在 **新机(CANN 9.0)** 上,镜像(NJU)可用。"
    got = lint.fix_punct(src)
    assert "(" not in got.replace("`", "") or "）" not in got, got
    assert got.count("（") == got.count("）")


def test_xref_detects_break_when_words_sit_between_link_and_name(tmp_path):
    """`见 [标题](x.md) 末尾的「出了问题」`——链接与节名隔着字，相邻模式抓不到。

    真机上就是这么断的：`docs/install.md` 指着 `community-task.md` 一个不存在的
    「出了问题」节，装不上的人被指到空处。
    """
    skill = tmp_path / "skill" / "demo"
    (skill / "references").mkdir(parents=True)
    (skill / "references" / "x.md").write_text(
        "# X\n\n## 常见问题\n", encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "# S\n\n装不上见 [使用指导](references/x.md) 末尾的「出了问题」。\n",
        encoding="utf-8")
    found = lint.check_xrefs(lint.collect(tmp_path))
    assert [f[2] for f in found] == ["SA-05"]


def test_quoted_phrase_after_a_link_is_not_a_section_name(tmp_path):
    """`见 [X](x.md)，判据是「不可跳过」`——「」是引语不是节名，不许误报。"""
    skill = tmp_path / "skill" / "demo"
    (skill / "references").mkdir(parents=True)
    (skill / "references" / "x.md").write_text(
        "# X\n\n## 常见问题\n", encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "# S\n\n见 [X](references/x.md)，这一步在流程里标着不可跳过，"
        "跳过则结论不成立，理由那一节写着「不可跳过」。\n",
        encoding="utf-8")
    assert lint.check_xrefs(lint.collect(tmp_path)) == []


# ------------------------------------------------------------------ 规则与实现一致

RULES_DIR = pathlib.Path(__file__).resolve().parents[1] / "rules"
BUDGET_RULE = "SA-04"          # 由 skill_budget_lint.py 实现，不在本模块


def declared_rules():
    """解析两份规则文件的规则表，返回 {编号: 机检列的取值}。"""
    out = {}
    for name in ("skill-authoring.md", "zh-writing.md"):
        for line in (RULES_DIR / name).read_text(encoding="utf-8").splitlines():
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 4 or not re.fullmatch(r"(SA|ZH)-[A-Z]?\d+", cells[0]):
                continue
            out[cells[0]] = cells[-1]
    return out


def implemented_rules():
    """lint 实际会输出的编号。"""
    ids = set(lint.BANNED) | lint.LEGACY | lint.REPORT_ONLY
    ids |= {"ZH-W2", "ZH-P1", "ZH-P3", "SA-01", "SA-02", "SA-05", "SA-12"}
    return ids


def test_every_declared_machine_rule_is_implemented():
    """规则表标「是」或「报数」的编号，lint 必须认识；否则规范说管而机器不管。"""
    declared = declared_rules()
    implemented = implemented_rules() | {BUDGET_RULE}
    missing = sorted(rule for rule, mode in declared.items()
                     if ("是" in mode or "报数" in mode) and rule not in implemented)
    assert not missing, f"规则表声明要机检但 lint 没实现：{missing}"


def test_every_implemented_rule_is_declared():
    """lint 会输出的编号，规则文件里必须有一行，否则报错指向不存在的规则。"""
    declared = set(declared_rules())
    extra = sorted(implemented_rules() - declared)
    assert not extra, f"lint 会报但规则文件没写：{extra}"


def test_report_only_column_matches_implementation():
    """标「报数」的必须在 REPORT_ONLY 里，标「是」的必须不在。"""
    wrong = []
    for rule, mode in declared_rules().items():
        if rule == BUDGET_RULE:
            continue
        if "报数" in mode and rule not in lint.REPORT_ONLY:
            wrong.append(f"{rule} 标报数但会拦截")
        if mode.startswith("是") and rule in lint.REPORT_ONLY:
            wrong.append(f"{rule} 标是但只计数")
    assert not wrong, wrong


def test_glossary_is_the_only_synonym_source():
    """禁用同义词只在术语表里定义，不在 BANNED 词表里重复。"""
    syn = set(lint.load_synonyms(RULES_DIR / "zh-writing.md"))
    dup = [w for spec in lint.BANNED.values() for w in spec["words"] if w in syn]
    assert not dup, f"同义词在两处定义：{dup}"
