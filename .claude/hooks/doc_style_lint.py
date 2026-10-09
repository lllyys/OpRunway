#!/usr/bin/env python3
"""skill 文档的行文、结构与引用检查脚本。

规则正文在 `.claude/rules/zh-writing.md`（ZH-xx）与 `.claude/rules/skill-authoring.md`
（SA-xx）；每条规则的外部出处与存量标定数据在
`docs/development/skill-authoring-basis.md`。本文件只存词表、正则与阈值。

扫描范围：`skill/<name>/SKILL.md`、`skill/<name>/references/*.md`、`docs/guide/*.md`、
`docs/install.md`。规则分三类：

| 类别 | 规则 | 退出码 |
| --- | --- | --- |
| 全仓拦截 | ZH-S4、ZH-W5、ZH-P2、SA-05 引用 | 命中即退 1 |
| 按 skill 启用 | ZH-P1、ZH-P3、ZH-W1、ZH-W2、ZH-W3、ZH-T2、SA-01、SA-02、SA-05 孤儿、SA-12 | 只对 `MIGRATED` 里的 skill 退 1，其余计数 |
| 报数 | ZH-S1、ZH-S2、ZH-T1、SA-06 | 只打印计数 |

用法：

    python3 .claude/hooks/doc_style_lint.py            # 扫全仓
    python3 .claude/hooks/doc_style_lint.py <文件…>     # 只扫指定文件
    python3 .claude/hooks/doc_style_lint.py --fix      # 只自动修半角标点
    python3 .claude/hooks/doc_style_lint.py --baseline # 输出各规则计数

退出码：0 无拦截项；1 有拦截项。换词由人做，机器换出来的词往往比原文更糟。
"""

import argparse
import json
import pathlib
import re
import sys
import unicodedata

# --------------------------------------------------------------- 词表与规则

# 按新规则改写完、可以对新规则拦截的 skill。迁移一个加一个。
MIGRATED = {"repo-task-case-gen"}

LEGACY = {"ZH-S4", "ZH-W5", "ZH-P2", "SA-05"}
REPORT_ONLY = {"ZH-S1", "ZH-S2", "ZH-T1", "SA-06"}

# 每个词都给替代写法：只禁不给替换，下一个人只会换一个同样差的词。
BANNED = {
    "ZH-W5": {
        "why": "拟人、第一人称、填充词、模糊词或将来时",
        "words": {
            "它想": "写按什么规则做什么，或省略主语",
            "它认为": "写判断条件本身，如「`passed` 为假时」",
            "它知道": "写读的是哪份数据",
            "它记得": "写状态存在哪个文件",
            "它打算": "写接下来执行什么",
            "它的灵魂": "用名词短语命名这一节",
            "它自己读懂": "写解析的输入与依据",
            "它会停下来问": "写「需人工指定」并列出待定项",
            "我们的": "写具体是谁的，如「本包的」「待验收工程的」",
            "我们会": "写实际行为主体",
            "我们还": "写具体是谁",
            "不是我们": "写具体是谁",
            "简单地": "删掉",
            "轻松": "删掉，或给出实际耗时或步数",
            "显然": "删掉，或写出为什么成立",
            "众所周知": "删掉，或给出处",
            "一下就": "删掉，或给出实际耗时",
            "适当": "给出具体取值或计算方式",
            "必要时": "写明「必要」的判定条件",
            "视情况": "把「哪些情况」和「分别怎么办」列出来",
            "酌情": "写明判断权交给谁、依据是什么",
            "尽可能": "写出必须达到的下限",
            "原则上": "写明是硬性要求还是可协商",
            "根据情况": "把情况和对应处理列成表",
            "将会": "改成现在时",
            "即将": "写明版本或日期，或删掉",
        },
    },
    "ZH-W1": {
        "why": "自造词或低频书面词，零上下文读者要猜含义",
        "words": {
            "判据表": "检查表",
            "判据": "写具体：完成条件、失败条件、判断依据",
            "口径": "标准、统计方式或计算方法",
            "跑测侧": "写 skill 名 `repo-task-atk-accept`",
            "生成侧": "写 skill 名 `repo-task-case-gen`",
            "跑测": "测试执行",
            "自带件": "任务自带测试件",
            "剖面": "写 `backend` 的取值",
            "量具": "检查脚本",
            "落点": "位置、输出目录",
            "形态": "写具体：接口类型、用例结构",
            "契约": "约定",
            "硬闸": "强制检查",
            "闸": "检查",
            "归位": "移入对应目录",
            "扫除": "删除",
            "交付面": "用例包根目录",
            "兜底": "默认处理",
            "现场": "中间文件、运行目录",
            "造数": "生成输入数据",
            "原件": "原始文件",
            "去向": "下一步、处理方式",
            "整类": "全部、同一类",
            "体检": "预检查",
            "裁决": "判定",
            "探针": "探测脚本",
            "载体": "位置、存放方式",
            "路由器": "入口文件",
            "落盘": "写入文件",
            "回填": "补填",
            "播报": "输出、告知用户",
            "闭环": "完整流程",
            "一档": "一个规模档",
        },
    },
    "ZH-W3": {
        "why": "口语",
        "words": {
            "写死": "固定值",
            "对不上": "不一致",
            "东西": "写具体名称",
            "名字": "名称",
            "照样": "仍然",
            "本来就": "删去，或写原因",
            "连带": "同时",
            "忠实": "原样、一致",
            "一刀切": "统一处理",
            "硬凑": "强行凑数",
            "碰不对": "重新运行无效",
            "打穿": "使……失效",
            "坑": "问题",
            "一行都不": "完全不",
            "一个字都不": "完全不",
            "瞎猜": "推测",
            "翻车": "失败",
        },
    },
    "ZH-T2": {
        "why": "开发史叙事：运行时文档只写当前做法",
        "words": {
            "曾按": "移入 skill 的 CLAUDE.md 或 docs/development，原位只留结论",
            "曾把": "同上",
            "曾经": "同上",
            "起因是": "同上",
            "上一版": "同上",
            "改回去": "同上",
            "当初": "同上",
        },
    },
}

# 「跑」单字作动词是口语；「跑测」由 ZH-W1 处理，这里跳过
RUN_VERB = re.compile(r"(?<=[一-鿿])跑(?!测)|跑(?=[一-鿿])(?!测)")

HEADING_BAD = re.compile(
    r"(？|\?$|怎么|什么时候|要不要|该不该|多久|哪些时候|是什么意思)")
# 读者常从目录跳进某一节，标题里的「它」没有先行词
HEADING_ANTHRO = re.compile(r"它")

CJK = r"[一-鿿]"
CJK_ONE = re.compile(CJK)
HALFWIDTH = re.compile(rf"(?<={CJK})[,;:?!()]|[,;:?!()](?={CJK})")
FULL = {",": "，", ";": "；", ":": "：", "?": "？", "!": "！",
        "(": "（", ")": "）"}
CJK_ASCII = re.compile(rf"{CJK}[A-Za-z0-9]|[A-Za-z0-9]{CJK}")
ASCII_SPACE_PUNCT = re.compile(r"[A-Za-z0-9] +[，。；：！？、）]")
NEGATION = re.compile(r"不要|别[再在把让去用]|不用|不许|禁止|绝不|永远不|切勿")

SENTENCE_MAX = 60        # ZH-S1：存量 >40 字 229 句、>60 字 28 句
BOLD_PER_SECTION = 3     # ZH-T1
REF_TOC_LINES = 100      # SA-06

GLOSSARY = pathlib.Path(__file__).resolve().parents[1] / "rules" / "zh-writing.md"

# 字面内容会被程序消费的样例与模板，不判行文
SKIP = re.compile(r"(golden-[\w-]+|[\w-]+-template|[\w-]+-example)\.md$")


def skipped(path):
    """样例与模板不判行文：它们的字面内容是约定。"""
    return bool(SKIP.search(pathlib.Path(path).name))


def load_synonyms(path=GLOSSARY):
    """从 zh-writing.md 的术语表读禁用同义词，返回 {同义词: 术语}。"""
    if not path.is_file():
        return {}
    out, in_table = {}, False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            in_table = "术语表" in line
            continue
        if not in_table or not line.startswith("|") or "---" in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or cells[0] == "术语":
            continue
        for syn in re.split(r"[、，]", cells[2]):
            syn = syn.strip().strip("`")
            if syn and syn not in "—-":
                out[syn] = cells[0].strip("`")
    return out


def skill_of(path):
    """文件所属 skill 名；不在 skill/<name>/ 下返回 None。"""
    parts = pathlib.Path(path).parts
    for i, part in enumerate(parts[:-1]):
        if part == "skill" and i + 1 < len(parts) - 1:
            return parts[i + 1]
    return None


def enforced(path, rule):
    """这条命中是否计入退出码。"""
    if rule in REPORT_ONLY:
        return False
    if rule in LEGACY:
        return True
    return skill_of(path) in MIGRATED


# --------------------------------------------------------------- 工具


def blank_code(text):
    """把代码块、行内代码、链接目标抹成等长空白，行号不变。"""
    def hollow(match):
        return "".join("\n" if c == "\n" else " " for c in match.group(0))

    text = re.sub(r"```.*?```", hollow, text, flags=re.S)
    text = re.sub(r"`[^`\n]*`", hollow, text)
    text = re.sub(r"\]\([^)\n]*\)", hollow, text)      # 链接目标
    # 用 [ \t] 不用 \s：\s 含换行，会跨行吞掉空行，其后行号全错
    text = re.sub(r"^[ \t]{4,}\S.*$", hollow, text, flags=re.M)  # 缩进代码
    return text


def slug(title):
    """GitHub 风格的标题锚点：小写、去标点、空格转连字符。"""
    text = unicodedata.normalize("NFKC", title).strip().lower()
    # 保留下划线：GitHub 的 slug 保留它
    text = re.sub(r"[`*\[\]()<>~]", "", text)
    text = re.sub(r"[^\w一-鿿\s-]", "", text)
    return re.sub(r"\s+", "-", text).strip("-")


def headings(text):
    """返回 [(行号, 级别, 原文标题)]，跳过围栏块里的 `#`，保留行内代码。"""
    def hollow(match):
        return "".join("\n" if c == "\n" else " " for c in match.group(0))

    fenced_only = re.sub(r"```.*?```", hollow, text, flags=re.S)
    out = []
    for i, line in enumerate(fenced_only.splitlines(), 1):
        m = re.match(r"^(#{1,6})\s+(.*?)\s*$", line)
        if m:
            out.append((i, len(m.group(1)), m.group(2)))
    return out


def word_hits(line, synonyms):
    """按词长从长到短匹配，已匹配的片段遮住，短词不重复命中。"""
    table = []
    for rule, spec in BANNED.items():
        for word, fix in spec["words"].items():
            table.append((word, rule, f"「{word}」 · {spec['why']}", fix))
    for syn, term in synonyms.items():
        table.append((syn, "ZH-W2", f"「{syn}」 · 术语表的禁用同义词", f"改为「{term}」"))
    table.sort(key=lambda row: -len(row[0]))
    hits = []
    for word, rule, what, fix in table:
        if word in line:
            hits.append((rule, what, fix))
            line = line.replace(word, "\x01" * len(word))
    if RUN_VERB.search(line):
        hits.append(("ZH-W3", "「跑」 · 口语", "运行、执行"))
    return hits


def paragraphs(clean):
    """把硬换行的段落拼回整段，表格按单元格拆。返回 [(起始行号, 文本)]。"""
    out, buf, start = [], [], 0
    for i, line in enumerate(clean.splitlines() + [""], 1):
        s = line.strip()
        is_item = re.match(r"^([-*]|\d+\.)\s", s)
        if not s or s.startswith(("#", "|", ">")) or is_item:
            if buf:
                out.append((start, "".join(buf)))
                buf = []
            if s.startswith("|"):
                out += [(i, c) for c in s.strip("|").split("|")]
            elif is_item:
                buf, start = [s], i
            continue
        if not buf:
            start = i
        buf.append(s)
    return out


def sentence_length(sentence):
    sentence = re.sub(r"\s+", "", sentence)
    return len(CJK_ONE.findall(sentence)) + len(re.findall(r"[A-Za-z0-9_.]+", sentence))


# --------------------------------------------------------------- 检查


def check_prose(path, text, synonyms=None):
    if skipped(path):
        return []
    if synonyms is None:
        synonyms = load_synonyms()
    findings = []
    clean = blank_code(text)
    bold_in_section, section_line = 0, 1

    def close_section():
        if bold_in_section > BOLD_PER_SECTION:
            findings.append((path, section_line, "ZH-T1",
                             f"这一节加粗 {bold_in_section} 处",
                             f"加粗只留红线，每节不超过 {BOLD_PER_SECTION} 处"))

    raw_lines = text.splitlines()
    for i, line in enumerate(clean.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            close_section()
            bold_in_section, section_line = 0, i
            title = stripped.lstrip("#").strip()
            if HEADING_BAD.search(title):
                findings.append((path, i, "ZH-S4",
                                 f"标题用了疑问句式：「{title}」",
                                 "改成名词短语，如「失败归因」「性能子集抽样」"))
            if HEADING_ANTHRO.search(title):
                findings.append((path, i, "ZH-S4",
                                 f"标题里出现拟人主语：「{title}」",
                                 "改成名词短语，主语交给正文"))
            continue
        # 引用块常是贴出来的上游原文或社区回复，不按本仓行文判
        if stripped.startswith(">"):
            continue
        bold_in_section += len(re.findall(r"\*\*[^*]+\*\*", line))
        # 「」里是引述的原话与报错，不改
        # 替换成空「」而不是删掉，否则两侧的英文与中文会被拼到一起误报 ZH-P1
        line = re.sub(r"「[^」]*」", "「」", line)
        for rule, what, fix in word_hits(line, synonyms):
            findings.append((path, i, rule, what, fix))
        if HALFWIDTH.search(line):
            findings.append((path, i, "ZH-P2",
                             "中文语境里用了半角标点", "改成全角（--fix 可自动改）"))
        link_text_free = re.sub(r"\[[^\]]*\]", "", line)
        if CJK_ASCII.search(link_text_free):
            findings.append((path, i, "ZH-P1", "中文与英文或数字之间没有空格",
                             "中间加一个半角空格"))
        # 抹空白会在行内代码处留下空格；替换成单个字符，保留代码两侧原有的空格
        squeezed = re.sub(r"`[^`\n]*`|\]\([^)\n]*\)|「[^」]*」", "X", raw_lines[i - 1])
        if stripped and ASCII_SPACE_PUNCT.search(squeezed):
            findings.append((path, i, "ZH-P3", "英文或数字与全角标点之间有空格",
                             "删掉空格"))
        if NEGATION.search(line):
            findings.append((path, i, "ZH-S2", "否定式指令",
                             "改写为要做什么；红线保留否定并给替代"))
    close_section()

    for start, para in paragraphs(clean):
        para = re.sub(r"「[^」]*」", "「」", para)
        for sentence in re.split(r"[。！？；]", para):
            n = sentence_length(sentence)
            if n > SENTENCE_MAX:
                findings.append((path, start, "ZH-S1", f"单句 {n} 字",
                                 f"拆成不超过 {SENTENCE_MAX} 字的短句"))
    return findings


def _frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, flags=re.S)
    if not m:
        return {}
    fields, key = {}, None
    for line in m.group(1).splitlines():
        top = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
        if top:
            key, value = top.group(1), top.group(2).strip()
            fields[key] = "" if value in (">-", ">", "|", "|-") else value
        elif key and line.startswith((" ", "\t")):
            fields[key] = (fields[key] + " " + line.strip()).strip()
    return fields


def check_structure(skill_md):
    """SA-01 name、SA-02 description、SA-05 孤儿 reference、SA-06 目录、SA-12 行号引用。"""
    skill_dir = skill_md.parent
    text = skill_md.read_text(encoding="utf-8")
    fields = _frontmatter(text)
    findings = []
    name = fields.get("name", "")
    if (not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name) or len(name) > 64
            or name != skill_dir.name):
        findings.append((skill_md, 1, "SA-01", f"name「{name}」不合规",
                         f"小写字母、数字、单个连字符，≤64，与目录名「{skill_dir.name}」相同"))
    desc = fields.get("description", "")
    if not desc or len(desc) > 1024:
        findings.append((skill_md, 1, "SA-02", f"description 长 {len(desc)} 字符",
                         "非空且不超过 1024 字符"))
    for ref in sorted((skill_dir / "references").glob("*.md")):
        # 孤儿检查是新规则，SA-05 的引用检查全仓拦截，所以只对已迁移 skill 发出
        if skill_dir.name in MIGRATED and f"references/{ref.name}" not in text:
            findings.append((skill_md, 1, "SA-05", f"references/{ref.name} 没有从 SKILL.md 链出",
                             "在用得上的那一步加链接，或删掉这份文件"))
        lines = ref.read_text(encoding="utf-8").splitlines()
        if len(lines) > REF_TOC_LINES and not any(
                re.match(r"^\s*[-*]\s*\[", l) or "目录" in l for l in lines[:20]):
            findings.append((ref, 1, "SA-06", f"{len(lines)} 行，开头没有目录",
                             "开头列出各节"))
    fenced_free = re.sub(r"```.*?```", "", text, flags=re.S)
    for i, line in enumerate(fenced_free.splitlines(), 1):
        if re.search(r"[\w-]+\.py:\d+", line):
            findings.append((skill_md, i, "SA-12", "引用了「脚本名:行号」",
                             "要 agent 知道的写进脚本输出或 references"))
    return findings


def _names_match(name, titles):
    """引用名命中标题：全等，或是标题在分隔符处的前缀。"""
    bare = lambda s: s.replace("`", "").strip()
    name = bare(name)
    plain = {bare(t) for t in titles}
    if name in plain:
        return True
    return any(t.startswith(name) and t[len(name)] in "：:，,（(「 —-"
               for t in plain if len(t) > len(name))


def check_xrefs(files):
    """跨文档引用必须解析得到。改标题时这是唯一的安全网。"""
    titles, texts = {}, {}
    for p in files:
        texts[p] = p.read_text(encoding="utf-8")
        titles[p] = {t for _, _, t in headings(texts[p])}

    findings = []
    for p, text in texts.items():
        for i, line in enumerate(text.splitlines(), 1):
            # ① 「见 xx.md「章节名」」
            for target, name in re.findall(r"([\w.-]+\.md)「([^」]+)」", line):
                dest = (p.parent / target)
                if not dest.exists():
                    dest = p.parent / "references" / target
                if not dest.exists() or dest not in titles:
                    continue
                if not _names_match(name, titles[dest]):
                    findings.append((p, i, "SA-05",
                                     f"{target} 里没有「{name}」这一节",
                                     f"改引用，或把该节名改回；现有节名："
                                     f"{'、'.join(sorted(titles[dest]))[:120]}"))
            # ② 「见 [标题](xx.md) 末尾的「章节名」」，中间不超过 20 个字
            for target, name in re.findall(
                    r"\]\(([\w./-]+\.md)\)[^「」]{0,20}「([^」]+)」", line):
                dest = (p.parent / target)
                if not dest.exists() or dest not in titles:
                    continue
                if not _names_match(name, titles[dest]):
                    findings.append((p, i, "SA-05",
                                     f"{target} 里没有「{name}」这一节",
                                     f"改引用，或把该节名改回；现有节名："
                                     f"{'、'.join(sorted(titles[dest]))[:120]}"))
            # ③ markdown 锚点
            for link in re.findall(r"\]\(([^)\s]*#[^)\s]*)\)", line):
                target, _, anchor = link.partition("#")
                dest = p if not target else (p.parent / target)
                if dest not in titles:
                    continue
                if anchor and anchor not in {slug(t) for t in titles[dest]}:
                    findings.append((p, i, "SA-05",
                                     f"锚点 #{anchor} 在 {dest.name} 里解析不到",
                                     "核对目标标题，或改锚点"))
    return findings


def fix_punct(text):
    """只改半角标点。换词由人做。"""
    def swap(match):
        return FULL[match.group(0)]
    saved = []

    def stash(m):
        saved.append(m.group(0))
        return f"\x00{len(saved) - 1}\x00"

    protected = re.sub(r"```.*?```", stash, text, flags=re.S)
    # 引用块与检查同一标准跳过（2026-09-10 实测 --fix 曾改写豁免的 `>` 行）
    protected = re.sub(r"(?m)^[ \t]*>.*$", stash, protected)
    protected = re.sub(r"`[^`\n]*`", stash, protected)
    protected = re.sub(r"\]\([^)\n]*\)", stash, protected)
    protected = _pair_parens(protected)
    protected = HALFWIDTH.sub(swap, protected)
    return re.sub(r"\x00(\d+)\x00", lambda m: saved[int(m.group(1))], protected)


def _pair_parens(text):
    """括号成对转换：一端够得着中文就两端一起转，避免半角全角混搭。"""
    def one_line(line):
        out, stack = list(line), []
        for i, ch in enumerate(line):
            if ch in "(（":
                stack.append(i)
            elif ch in ")）" and stack:
                start = stack.pop()
                span = line[start:i + 1]
                before = line[:start].rstrip("* \t")
                after = line[i + 1:].lstrip("* \t")
                near = (CJK_ONE.search(before[-1:]) or CJK_ONE.search(after[:1])
                        or CJK_ONE.search(span))
                mixed = (line[start] in "(（") and (line[i] in ")）") and (
                    (line[start] == "(") != (line[i] == ")"))
                if near or mixed:
                    out[start], out[i] = "（", "）"
        return "".join(out)

    return "\n".join(one_line(l) for l in text.split("\n"))


# --------------------------------------------------------------- 入口


def collect(root):
    """扫描范围。README 不在内：它是门面，emoji 导航标题是另一套体裁。"""
    # 双前缀并存：上游仓根在 skill/，本仓镜像在 plugin/skill/；两处不会同时非空
    return (sorted(root.glob("skill/*/SKILL.md"))
            + sorted(root.glob("plugin/skill/*/SKILL.md"))
            + sorted(root.glob("skill/*/references/*.md"))
            + sorted(root.glob("plugin/skill/*/references/*.md"))
            + sorted(root.glob("docs/guide/*.md"))
            + sorted(root.glob("plugin/docs/guide/*.md"))
            + [f for f in [root / "docs" / "install.md",
                           root / "plugin" / "docs" / "install.md"] if f.is_file()])


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", help="要扫的文件；省略则扫全仓")
    parser.add_argument("--fix", action="store_true", help="自动修半角标点")
    parser.add_argument("--baseline", action="store_true", help="只输出计数")
    parser.add_argument("--root", default=".", help="仓根")
    args = parser.parse_args()

    root = pathlib.Path(args.root).resolve()
    everything = collect(root)
    targets = ([pathlib.Path(p).resolve() for p in args.paths]
               if args.paths else everything)
    targets = [p for p in targets if p.exists()]

    if args.fix:
        changed = 0
        for p in targets:
            if skipped(p):
                continue
            src = p.read_text(encoding="utf-8")
            new = fix_punct(src)
            if new != src:
                p.write_text(new, encoding="utf-8")
                print(f"修了半角标点：{p.relative_to(root)}")
                changed += 1
        print(f"共改 {changed} 份")
        return 0

    synonyms = load_synonyms(root / ".claude" / "rules" / "zh-writing.md")
    findings = []
    for p in targets:
        findings += check_prose(p, p.read_text(encoding="utf-8"), synonyms)
    skill_mds = sorted({root / "skill" / s / "SKILL.md" for s in map(skill_of, targets) if s})
    for skill_md in skill_mds:
        if skill_md.is_file():
            findings += check_structure(skill_md)
    # 引用完整性拿全仓标题表判，只扫子集会把「目标文件没扫到」误判成断链
    findings += [f for f in check_xrefs(everything) if f[0] in set(targets)]

    if args.baseline:
        counts = {}
        for _, _, rule, _, _ in findings:
            counts[rule] = counts.get(rule, 0) + 1
        print(json.dumps(counts, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    blocking = [f for f in findings if enforced(f[0], f[2])]
    counted = [f for f in findings if not enforced(f[0], f[2])]

    by_file = {}
    for f in blocking:
        by_file.setdefault(f[0], []).append(f)
    for p in sorted(by_file):
        print(f"\n{p.relative_to(root)}")
        for _, line, rule, what, fix in sorted(by_file[p], key=lambda x: x[1]):
            print(f"  {line:>4}  [{rule}] {what}")
            print(f"        → {fix}")

    if counted:
        counts = {}
        for _, _, rule, _, _ in counted:
            counts[rule] = counts.get(rule, 0) + 1
        print("\n不计入退出码的命中（报数类规则，及未迁移 skill 的新规则）：")
        print("  " + "  ".join(f"{k}×{v}" for k, v in sorted(counts.items())))

    if not blocking:
        print(f"\n行文、结构与引用检查通过（{len(targets)} 份文档）")
        return 0
    print(f"\n共 {len(blocking)} 处拦截项，涉及 {len(by_file)} 份文档。规则见 "
          ".claude/rules/zh-writing.md 与 skill-authoring.md")
    return 1


if __name__ == "__main__":
    sys.exit(main())
