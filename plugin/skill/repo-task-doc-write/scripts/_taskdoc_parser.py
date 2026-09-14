"""按固定章节号与表格列反解任务书 md。

模板是稳定的，所以解析靠章节号而不是靠自然语言。解析不出章节结构时
抛 ParseError，由 check_taskdoc.py 转成退出码 3——那是结构问题，
不是内容问题，报错要分得开。
"""

import re
from collections import namedtuple
from pathlib import Path

Table = namedtuple("Table", "header rows start_line")
Section = namedtuple("Section", "number title start_line lines tables code_blocks")

_HEADING = re.compile(r"^(#{2,4})\s+([\d.]+)\s*(.*?)\s*$")
_SEPARATOR = re.compile(r"^\|[\s:|-]+\|$")
_FENCE = re.compile(r"```.*?```", re.DOTALL)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


class ParseError(Exception):
    """章节结构解析不出来。"""


class Doc:
    def __init__(self, path, title, sections):
        self.path = Path(path)
        self.title = title
        self.sections = sections

    def section(self, number):
        return self.sections.get(number)


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _tables(lines, offset):
    out, index = [], 0
    while index < len(lines):
        line = lines[index].strip()
        nxt = lines[index + 1].strip() if index + 1 < len(lines) else ""
        if line.startswith("|") and _SEPARATOR.match(nxt):
            header = _cells(line)
            rows, cursor = [], index + 2
            while cursor < len(lines) and lines[cursor].strip().startswith("|"):
                rows.append(_cells(lines[cursor]))
                cursor += 1
            out.append(Table(header, rows, offset + index))
            index = cursor
            continue
        index += 1
    return out


def _strip_html_comments(raw):
    """去掉 HTML 注释，行号不变——删掉的内容换成等量空行，不是删行。

    make_taskdoc.py 的脚手架占位符就是 `<!-- 待填写：... -->`，不剥掉的话
    check_nonempty 会把注释文本当成「已填写内容」。跳过代码块：签名代码块
    里出现 `<!--` 字样不该被当成注释解析。

    实现上先把 fenced code block 整体placeholder 掉，避免块内文本被误当
    普通文本处理，再对剩下的文本做注释替换，最后把代码块原样换回来——
    两步都是「等量替换」，全程不改变整体换行数，行号因此不漂移。
    """
    text = "\n".join(raw)

    blocks = []

    def _stash(match):
        blocks.append(match.group(0))
        return f"\x00FENCE{len(blocks) - 1}\x00"

    protected = _FENCE.sub(_stash, text)
    stripped = _COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), protected)
    for index, block in enumerate(blocks):
        stripped = stripped.replace(f"\x00FENCE{index}\x00", block)
    return stripped.split("\n")


def _code_blocks(lines):
    out, current, inside = [], [], False
    for line in lines:
        if line.strip().startswith("```"):
            if inside:
                out.append("\n".join(current))
                current = []
            inside = not inside
            continue
        if inside:
            current.append(line)
    if current:
        out.append("\n".join(current))
    return out


def parse(path):
    path = Path(path)
    raw = _strip_html_comments(path.read_text(encoding="utf-8").splitlines())
    title = next((l.lstrip("# ").strip() for l in raw if l.startswith("# ")), "")

    marks, inside_code = [], False
    for number, line in enumerate(raw, 1):
        if line.strip().startswith("```"):
            inside_code = not inside_code
            continue
        if inside_code:
            continue
        found = _HEADING.match(line)
        if found:
            marks.append((number, found.group(2).rstrip("."), found.group(3)))

    if not marks:
        raise ParseError(f"{path.name}: 没有解析出任何编号章节标题")

    sections = {}
    for index, (start, number, heading) in enumerate(marks):
        end = marks[index + 1][0] - 1 if index + 1 < len(marks) else len(raw)
        body = raw[start:end]
        sections[number] = Section(
            number=number, title=heading, start_line=start, lines=body,
            tables=_tables(body, start + 1), code_blocks=_code_blocks(body))
    return Doc(path, title, sections)
