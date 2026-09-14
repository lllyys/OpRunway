"""doc_style_lint 的判据测试。

纯逻辑，能用字符串跑完，按仓根 CLAUDE.md 的分界线该补测试。
重点在**误报**：判据误报就会被忽略，被忽略的门禁等于没有。
"""

import pathlib
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
    ("## 报告怎么读", "heading_question"),
    ("## 什么时候该翻源码", "heading_question"),
    ("### --soc 怎么定", "heading_question"),
    ("## 它比 ATK 强在哪", "heading_anthro"),
    ("## 它的灵魂：不许自己想办法", "heading_anthro"),
    ("找不到我们的 so。", "first_person"),
    ("环境不满足时适当处理。", "vague"),
    ("必要时回到上一步。", "vague"),
    ("这个字段将会被移除。", "future_tense"),
    ("简单地跑一遍就行。", "filler"),
    ("显然这里不用管。", "filler"),
    ("在 Ascend910 新机(CANN 9.0.0)上实测。", "halfwidth_punct"),
])
def test_catches(line, rule):
    assert rule in rules(prose(line)), f"漏判：{line}"


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
    assert [f[2] for f in found] == ["xref_broken"]


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
    assert [f[2] for f in found] == ["anchor_broken"]


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
    assert [f[2] for f in found] == ["xref_broken"]


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
