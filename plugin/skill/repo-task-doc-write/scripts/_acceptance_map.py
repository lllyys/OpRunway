"""反填验收侧 S1 的约束表。

产物 evidence/acceptance_map.json 是交给验收 agent 的接力棒：验收 skill 的
references/intake.md#验收约束表 要填的那张表，这里提前填好。

某一行填不出来就写「待确认」，L4 会据此判红——任务书没写清的东西，
验收阶段一样要停下来问，早三个阶段发现代价低得多。
"""

import json
import re
from pathlib import Path

FIELDS = ("算子", "输入", "非连续 Tensor", "输出", "功能范围", "语义形态",
          "错误语义", "精度", "性能", "环境", "提交", "构建", "PR 目录")

PENDING = "待确认"

REFERENCES = Path(__file__).resolve().parents[1] / "references"


def _placeholder_tokens():
    return json.loads(
        (REFERENCES / "vague-words.json").read_text(
            encoding="utf-8"))["placeholders"]


def _is_residue(value):
    """取到的值本身就是没填的占位词，不能当真实取值填进接力棒。

    正常情况下解析层已经把 HTML 注释剥空，正则捞不到东西。这里防的是
    第二条路：没包在注释里、直接手打在正文里的占位词（如「待填写」）。
    """
    return any(token in value for token in _placeholder_tokens())


def _text(doc, number):
    section = doc.sections.get(number)
    return "\n".join(section.lines).strip() if section else ""


def _param_rows(doc):
    section = doc.sections.get("2.4")
    if not section or not section.tables:
        return []
    return section.tables[0].rows


def _by_direction(doc, wanted):
    rows = _param_rows(doc)
    section = doc.sections.get("2.4")
    header = section.tables[0].header if section and section.tables else []
    if "输入／输出/属性" not in header:
        return []
    index = header.index("输入／输出/属性")
    return [row[0] for row in rows
            if index < len(row) and wanted in row[index]]


def build(doc, spine, context):
    """返回 {约束表字段: 取值或「待确认」}。"""
    out = {}
    out["算子"] = doc.title or PENDING
    inputs = _by_direction(doc, "输入")
    out["输入"] = "、".join(inputs) if inputs else PENDING
    outputs = _by_direction(doc, "输出")
    out["输出"] = "、".join(outputs) if outputs else PENDING

    constraint = _text(doc, "2.5")
    hit = re.search(r"非连续\s*Tensor[^\n|]*[|：:]\s*([^\n|]+)", constraint)
    value = hit.group(1).strip() if hit else ""
    out["非连续 Tensor"] = value if value and not _is_residue(value) else PENDING

    out["功能范围"] = _text(doc, "2.1")[:200] or PENDING
    out["语义形态"] = "不构造（任务书未要求）"

    section = doc.sections.get("2.4")
    header = section.tables[0].header if section and section.tables else []
    if "异常行为" in header:
        index = header.index("异常行为")
        errors = [f"{row[0]}: {row[index]}" for row in _param_rows(doc)
                  if index < len(row) and row[index].strip() not in ("", "-")]
        out["错误语义"] = "；".join(errors) if errors else PENDING
    else:
        out["错误语义"] = PENDING

    out["精度"] = _text(doc, "3.2")[:300] or PENDING
    out["性能"] = _text(doc, "3.3")[:300] or PENDING
    out["环境"] = _text(doc, "3.1") or PENDING
    out["提交"] = _text(doc, "2.2") or PENDING
    # 「构建」在 intake.md 里指公开构建命令，社区模板没有承载它的章节。
    # 这不是任务书写漏了：构建入口在算子工程自带的构建脚本里，验收侧到工程
    # 目录读就够了，任务书重复一遍反而会与工程漂移。写成事实陈述而不是
    # 「待确认」，否则 L4 会把一个本就不该由任务书回答的问题判成缺口。
    out["构建"] = "任务书不声明构建命令，构建入口以算子工程自带的构建脚本为准"
    out["PR 目录"] = _text(doc, "5") or PENDING
    return out
