#!/usr/bin/env python3
"""skill 文档的行文与引用完整性量具。

扫 `skill/<name>/SKILL.md` 与 `skill/<name>/references/*.md`。两类判据：

- **行文**：拟人、第一人称、疑问句标题、填充词、模糊词、将来时、半角标点。
  判据取自 Kubernetes 文档风格指南与 Google 开发者文档风格指南的明令禁止项，
  逐条在本仓存量上标定过命中量与误报，误报高的候选已砍掉（记录见
  `.claude/rules/doc-style.md`）。
- **引用完整性**：`](#锚点)` 与 `xx.md「章节名」` 两种跨文档引用都必须解析得到。
  这一条是改标题时的安全网——本仓有 64 个锚点与 32 处文字引用，
  改一个标题不查引用，断的地方 agent 要跑到那一步才发现。

用法：

    python3 .claude/hooks/doc_style_lint.py            # 扫全仓，列出所有问题
    python3 .claude/hooks/doc_style_lint.py <文件…>     # 只扫指定文件
    python3 .claude/hooks/doc_style_lint.py --fix      # 自动修可自动修的那类
    python3 .claude/hooks/doc_style_lint.py --baseline # 输出当前计数，供棘轮用

退出码：0 无问题；1 有问题。`--fix` 只动半角标点，其余一律人工改——
换个词是判断题，机器换出来的词往往比原文更糟。
"""

import argparse
import json
import pathlib
import re
import sys
import unicodedata

# --------------------------------------------------------------- 判据

# 禁用词一律「禁一个给一个替代」。只禁不给替换，下一个人只会换一个同样差的词
# ——这条纪律沿用 repo-task-doc-write 的 vague-words.json。
BANNED = {
    "anthropomorphism": {
        "why": "拟人：把工具写成有意图的主体，读者要先解析角色才读得到机制",
        "words": {
            "它想": "写它按什么规则做什么，或直接省略主语",
            "它认为": "写判据本身，如「`passed` 为假时」",
            "它知道": "写它读的是哪份数据",
            "它记得": "写状态存在哪个文件",
            "它打算": "写接下来执行什么",
            "它的灵魂": "用名词短语命名这一节，如「忠实执行约束」",
            "它自己读懂": "写解析的输入与依据",
            "它会停下来问": "写「需人工指定」并列出待定项",
        },
    },
    "first_person": {
        "why": "第一人称：reference 是被查阅的，「我们」的指代在查阅现场不成立",
        "words": {
            "我们的": "写具体是谁的，如「本包的」「待验收工程的」",
            "我们会": "写实际行为主体",
            "我们还": "写具体是谁",
            "不是我们": "写具体是谁",
        },
    },
    "filler": {
        "why": "填充词：不携带信息，且「简单」对卡住的读者是反效果",
        "words": {
            "简单地": "删掉",
            "轻松": "删掉，或给出实际耗时/步数",
            "显然": "删掉，或写出为什么成立",
            "众所周知": "删掉，或给出处",
            "一下就": "删掉，或给出实际耗时",
        },
    },
    "vague": {
        "why": "模糊指令：执行者要猜。skill-style.md 已写明禁止，此前无检查器",
        "words": {
            "适当": "给出具体取值或计算方式",
            "必要时": "写明「必要」的判定条件",
            "视情况": "把「哪些情况」和「分别怎么办」列出来",
            "酌情": "写明判断权交给谁、依据是什么",
            "尽可能": "写出必须达到的下限",
            "原则上": "写明是硬要求还是可协商",
            "根据情况": "把情况和对应处理列成表",
        },
    },
    "future_tense": {
        "why": "将来时：文档描述的是当前行为，将来时会过期",
        "words": {
            "将会": "改成现在时，如「会」或直接陈述",
            "即将": "写明版本或日期，或删掉",
        },
    },
}

# 疑问句式标题。Diátaxis 的 reference 要求「采用标准模式」：同一批文档的章节
# 名要能横向对齐，疑问句每份问法都不同，对不齐也扫不动。
HEADING_BAD = re.compile(
    r"(？|\?$|怎么|什么时候|要不要|该不该|多久|哪些时候|是什么意思)")
# 标题里的「它」一律判：读者常常从目录直接跳进某一节，代词在那里没有先行词。
HEADING_ANTHRO = re.compile(r"它")

CJK = r"[一-鿿]"
CJK_ONE = re.compile(CJK)
HALFWIDTH = re.compile(rf"(?<={CJK})[,;:?!()]|[,;:?!()](?={CJK})")
FULL = {",": "，", ";": "；", ":": "：", "?": "？", "!": "！",
        "(": "（", ")": "）"}


# 有一类文件长在 references/ 下，却不是给人读的行文，而是**照抄用的样例与模板**：
# 它们的字面内容就是契约，被解析器按表头取列、被门禁当成「什么叫合格」的定义。
# 按行文规范改一个标点，doc-write 的 9 个测试当场红——实测于 2026-09-04。
#
# 判据是「这份文件的字面内容会不会被程序消费」，不是文件名好不好看。
SKIP = re.compile(r"(golden-[\w-]+|[\w-]+-template|[\w-]+-example)\.md$")


def skipped(path):
    """样例与模板不判行文：它们的字面内容是契约。"""
    return bool(SKIP.search(pathlib.Path(path).name))


# --------------------------------------------------------------- 工具


def blank_code(text):
    """把代码块与行内代码抹成等长空白，行号不变。

    判据不管代码里写了什么：`os.path.join(a,b)` 里的半角逗号是对的，
    `它自己` 出现在一段贴出来的日志里也不是行文问题。
    """
    def hollow(match):
        return "".join("\n" if c == "\n" else " " for c in match.group(0))

    text = re.sub(r"```.*?```", hollow, text, flags=re.S)
    text = re.sub(r"`[^`\n]*`", hollow, text)
    text = re.sub(r"\]\([^)\n]*\)", hollow, text)      # 链接目标
    # 用 [ \t] 不用 \s：\s 含换行，`\s{4,}` 会跨行吞掉空行把多行折成一行，
    # 于是它之后每一条发现的行号都是错的。
    text = re.sub(r"^[ \t]{4,}\S.*$", hollow, text, flags=re.M)  # 缩进代码
    return text


def slug(title):
    """GitHub 风格的标题锚点：小写、去标点、空格转连字符。"""
    text = unicodedata.normalize("NFKC", title).strip().lower()
    # 不删下划线：GitHub 的 slug 保留它，删了 `#edge_cases` 会被误判成断链
    text = re.sub(r"[`*\[\]()<>~]", "", text)
    text = re.sub(r"[^\w一-鿿\s-]", "", text)
    return re.sub(r"\s+", "-", text).strip("-")


def headings(text):
    """返回 [(行号, 级别, 原文标题)]，跳过围栏块里的 `#`。

    **只去围栏块，不去行内代码**：标题里的行内代码是标题的一部分，
    抹掉它会让「整类 dtype 全挂，报错是 `EZ1001`」变成半截，
    于是指向它的引用永远判成断链。
    """
    def hollow(match):
        return "".join("\n" if c == "\n" else " " for c in match.group(0))

    fenced_only = re.sub(r"```.*?```", hollow, text, flags=re.S)
    out = []
    for i, line in enumerate(fenced_only.splitlines(), 1):
        m = re.match(r"^(#{1,6})\s+(.*?)\s*$", line)
        if m:
            out.append((i, len(m.group(1)), m.group(2)))
    return out


# --------------------------------------------------------------- 检查


def check_prose(path, text):
    if skipped(path):
        return []
    findings = []
    clean = blank_code(text)
    lines = clean.splitlines()

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if HEADING_BAD.search(title):
                findings.append((path, i, "heading_question",
                                 f"标题用了疑问句式：「{title}」",
                                 "改成名词短语，如「失败归因」「性能子集抽样」"))
            if HEADING_ANTHRO.search(title):
                findings.append((path, i, "heading_anthro",
                                 f"标题里出现拟人主语：「{title}」",
                                 "改成名词短语，主语交给正文"))
            continue
        # 引用块常常是贴出来的上游原文或社区回复，不按本仓行文判
        if stripped.startswith(">"):
            continue
        # 「」里同理：本仓大量用它引述社区回复与报错原话，
        # 例如「我们会修 / PR 进行中」是维护者的原话，改它就是篡改样例。
        line = re.sub(r"「[^」]*」", "", line)
        for rule, spec in BANNED.items():
            for word, fix in spec["words"].items():
                if word in line:
                    findings.append((path, i, rule,
                                     f"「{word}」 · {spec['why']}", fix))
        if HALFWIDTH.search(line):
            findings.append((path, i, "halfwidth_punct",
                             "中文语境里用了半角标点", "改成全角（--fix 可自动改）"))
    return findings


def _names_match(name, titles):
    """引用名命中标题：全等，或是标题在分隔符处的前缀。

    本仓大量写成 `见 case-strategy.md「规模档」`，而那一节的全名是
    「规模档：字节数，不是元素数」。要求全等会把这类正常引用判成断链，
    于是判据本身变成噪声，没人再看它。
    """
    # 反引号在两侧都可能有可能无（引用写 `case_config.id`、标题写 case_config.id），
    # 不归一化就是纯误报。
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
        clean_lines = text.splitlines()
        for i, line in enumerate(clean_lines, 1):
            # ① 「见 xx.md「章节名」」——本仓大量使用，改标题最容易断这类
            for target, name in re.findall(r"([\w.-]+\.md)「([^」]+)」", line):
                dest = (p.parent / target)
                if not dest.exists():
                    dest = p.parent / "references" / target
                if not dest.exists() or dest not in titles:
                    continue                       # 指到仓外或未扫的文件，不判
                if not _names_match(name, titles[dest]):
                    findings.append((p, i, "xref_broken",
                                     f"{target} 里没有「{name}」这一节",
                                     f"改引用，或把该节名改回；现有节名："
                                     f"{'、'.join(sorted(titles[dest]))[:120]}"))
            # ② 「见 [标题](xx.md) 末尾的「章节名」」——链接与节名之间隔着字，
            # ①的相邻模式抓不到。存量上跑过：67 份文档只命中一处，且是真断链。
            # 限制中间不超过 20 个字，跨得更远的多半是引号而不是节名。
            for target, name in re.findall(
                    r"\]\(([\w./-]+\.md)\)[^「」]{0,20}「([^」]+)」", line):
                dest = (p.parent / target)
                if not dest.exists() or dest not in titles:
                    continue
                if not _names_match(name, titles[dest]):
                    findings.append((p, i, "xref_broken",
                                     f"{target} 里没有「{name}」这一节",
                                     f"改引用，或把该节名改回；现有节名："
                                     f"{'、'.join(sorted(titles[dest]))[:120]}"))
            # ③ markdown 锚点
            for link in re.findall(r"\]\(([^)\s]*#[^)\s]*)\)", line):
                target, _, anchor = link.partition("#")
                dest = p if not target else (p.parent / target)
                if dest not in titles:
                    continue                       # 跨出扫描范围，不判
                if anchor and anchor not in {slug(t) for t in titles[dest]}:
                    findings.append((p, i, "anchor_broken",
                                     f"锚点 #{anchor} 在 {dest.name} 里解析不到",
                                     "核对目标标题，或改锚点"))
    return findings


def fix_punct(text):
    """只改半角标点。换词是判断题，不自动做。"""
    def swap(match):
        return FULL[match.group(0)]
    out, saved = [], []

    def stash(m):
        saved.append(m.group(0))
        return f"\x00{len(saved) - 1}\x00"

    protected = re.sub(r"```.*?```", stash, text, flags=re.S)
    # 引用块与检查器同口径跳过：常是贴来的上游原文与社区回复，改它就是篡改证据。
    # 此前只保护代码块/行内代码/链接目标，--fix 会改写检查器判为豁免的 `>` 行——
    # 量与判据不同口径，实测于 2026-09-10（docs/guide/issue-workflow.md）。
    protected = re.sub(r"(?m)^[ \t]*>.*$", stash, protected)
    protected = re.sub(r"`[^`\n]*`", stash, protected)
    protected = re.sub(r"\]\([^)\n]*\)", stash, protected)
    protected = _pair_parens(protected)
    protected = HALFWIDTH.sub(swap, protected)
    return re.sub(r"\x00(\d+)\x00", lambda m: saved[int(m.group(1))], protected)


def _pair_parens(text):
    """括号成对转换：一端够得着中文就两端一起转。

    单侧判据会造出 `新机（CANN 9.0.0-beta.1)` 这种中西文混搭——括号与中文之间
    夹着 markdown 的 `*` 或空格时，另一端的 CJK 判据够不着。实测于 2026-09-04：
    faq.md 一份就造出 8 处。
    """
    def one_line(line):
        # 半角与全角都当括号收：存量里已有 `（NJU / SJTU)` 这种一半一半的，
        # 只认半角的话修不动它。
        out, stack = list(line), []
        for i, ch in enumerate(line):
            if ch in "(（":
                stack.append(i)
            elif ch in ")）" and stack:
                start = stack.pop()
                span = line[start:i + 1]
                # 括号外侧跳过 markdown 强调符与空格再看是不是中文
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
    """扫描范围。`docs/guide/` 也在内——同一批人写、同一批人读，
    没有理由适用另一套标准。"""
    # `docs/install.md` 也在内：它和 guide/ 是同一批读者，而且断引用最容易发生在
    # 这里——2026-09-09 它指着一个不存在的「出了问题」节，因为不在扫描范围没被抓到。
    # README 不在内：它是门面，emoji 导航标题是另一套体裁。
    return (sorted(root.glob("skill/*/SKILL.md"))
            + sorted(root.glob("skill/*/references/*.md"))
            + sorted(root.glob("docs/guide/*.md"))
            + [f for f in [root / "docs" / "install.md"] if f.is_file()])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
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

    findings = []
    for p in targets:
        findings += check_prose(p, p.read_text(encoding="utf-8"))
    # 引用完整性要拿全仓的标题表来判，只扫子集会把「目标文件没被扫到」误判成断链
    findings += [f for f in check_xrefs(everything) if f[0] in set(targets)]

    if args.baseline:
        counts = {}
        for _, _, rule, _, _ in findings:
            counts[rule] = counts.get(rule, 0) + 1
        print(json.dumps(counts, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if not findings:
        print(f"行文与引用检查通过（{len(targets)} 份文档）")
        return 0

    by_file = {}
    for f in findings:
        by_file.setdefault(f[0], []).append(f)
    for p in sorted(by_file):
        print(f"\n{p.relative_to(root)}")
        for _, line, rule, what, fix in sorted(by_file[p], key=lambda x: x[1]):
            print(f"  {line:>4}  [{rule}] {what}")
            print(f"        → {fix}")
    print(f"\n共 {len(findings)} 处，涉及 {len(by_file)} 份文档")
    return 1


if __name__ == "__main__":
    sys.exit(main())
