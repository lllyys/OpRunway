"""从任务书的固定章节读取接口声明与参数 dtype。"""

import re

from _case_utils import file_sha256
from _dtype_vocab import DTYPE_SOURCE_ALIASES
from _signature import parameter_names as _parameter_names
from _taskdoc_parser import ParseError, parse


class TaskDocError(Exception):
    """任务书缺少生成用例所需的章节或表格内容。"""


def load(path):
    """读取任务书，把章节结构错误改成可操作的提示。"""
    try:
        return parse(path)
    except ParseError as exc:
        raise TaskDocError(
            f"任务书章节结构解析不出：{exc}；"
            "用 repo-task-doc-write 补齐编号章节再来") from exc


def signature_block(doc):
    """返回 §2.3 第一个含 GetWorkspaceSize 的代码块原文。"""
    section = doc.section("2.3")
    if section is None:
        raise TaskDocError(
            "任务书缺 §2.3 接口定义的代码块，生成侧无法取签名；"
            "用 repo-task-doc-write 补齐 §2.3 再来")
    for block in section.code_blocks:
        if "GetWorkspaceSize" in block:
            return block
    raise TaskDocError(
        "任务书 §2.3 接口定义没有含 GetWorkspaceSize 的代码块，"
        "生成侧无法取签名；用 repo-task-doc-write 补齐 §2.3 再来")


def parameter_names(doc):
    """返回 §2.3 声明里的业务参数名，保持原顺序。"""
    names = _parameter_names(signature_block(doc))
    if not names:
        raise TaskDocError(
            "任务书 §2.3 的 GetWorkspaceSize 声明解析不出参数，"
            "生成侧无法取签名；用 repo-task-doc-write 修好声明再来")
    return names


def _parameter_table(section, required=("参数名", "dtype类型")):
    if not section.tables:
        raise TaskDocError(
            "任务书 §2.4 缺参数表，生成侧无法读取 dtype；"
            "用 repo-task-doc-write 补齐 §2.4 参数表再来")

    for table in section.tables:
        if all(column in table.header for column in required):
            return table

    table = max(
        section.tables,
        key=lambda candidate: sum(column in candidate.header for column in required))
    missing = [column for column in required if column not in table.header]
    columns = "、".join(f"「{column}」" for column in missing)
    raise TaskDocError(
        f"任务书 §2.4 参数表缺 {columns} 列，生成侧无法读取 dtype；"
        "用 repo-task-doc-write 补齐表头再来")


def param_dtypes(doc):
    """按 §2.4 表头读取每个参数的 dtype 列表。"""
    section = doc.section("2.4")
    if section is None:
        raise TaskDocError(
            "任务书缺 §2.4 参数说明，生成侧无法读取 dtype；"
            "用 repo-task-doc-write 补齐 §2.4 再来")

    table = _parameter_table(section)
    name_index = table.header.index("参数名")
    dtype_index = table.header.index("dtype类型")
    last_index = max(name_index, dtype_index)
    result = {}
    for row_number, row in enumerate(table.rows, table.start_line + 2):
        if len(row) <= last_index:
            raise TaskDocError(
                f"任务书 §2.4 参数表第 {row_number} 行列数不够，"
                "生成侧无法读取 dtype；用 repo-task-doc-write 修好该行再来")
        name = row[name_index].strip()
        raw_dtype = row[dtype_index].strip()
        if not raw_dtype or raw_dtype == "-":
            continue
        tokens = [token.strip() for token in re.split(r"[、，,/｜]", raw_dtype)]
        tokens = [token for token in tokens if token]
        if name and tokens:
            result[name] = tokens
    return result


def tensor_dtype_text(doc):
    """拼出 §2.4 所有张量参数的 dtype 列原文。"""
    advice = (
        "任务书 §2.4 的 dtype 列写成『所有类型』之类的描述而非具体名字时，"
        "先用 repo-task-doc-write 补齐。")
    section = doc.section("2.4")
    if section is None:
        raise TaskDocError(
            "任务书缺 §2.4 参数说明，生成侧无法读取张量 dtype；" + advice)

    try:
        table = _parameter_table(
            section, ("参数名", "数据类型", "dtype类型"))
    except TaskDocError as exc:
        raise TaskDocError(f"{exc} {advice}") from exc

    name_index = table.header.index("参数名")
    data_type_index = table.header.index("数据类型")
    dtype_index = table.header.index("dtype类型")
    last_index = max(name_index, data_type_index, dtype_index)
    values = []
    tensor_rows = 0
    for row_number, row in enumerate(table.rows, table.start_line + 2):
        if len(row) <= last_index:
            raise TaskDocError(
                f"任务书 §2.4 参数表第 {row_number} 行列数不够，"
                f"生成侧无法读取张量 dtype；{advice}")
        if row[data_type_index].strip() != "tensor":
            continue
        tensor_rows += 1
        values.append(row[dtype_index].strip())

    if not tensor_rows:
        raise TaskDocError(
            "任务书 §2.4 没有张量参数，dtype 轴无从取；" + advice)
    return " ".join(values)


def dtype_inventory(doc):
    """返回任务书 dtype 的规范键与词表未收录项，均去重保序。"""
    aliases = {}
    for canonical, source_names in DTYPE_SOURCE_ALIASES.items():
        aliases[canonical.casefold()] = canonical
        for source_name in source_names:
            aliases[source_name.casefold()] = canonical

    canonical, unknown = [], []
    for tokens in param_dtypes(doc).values():
        for token in tokens:
            mapped = aliases.get(token.casefold())
            if mapped is None:
                if token not in unknown:
                    unknown.append(token)
            elif mapped not in canonical:
                canonical.append(mapped)
    return canonical, unknown


def sha256(path):
    """返回任务书原始字节的 sha256。"""
    return file_sha256(path)
