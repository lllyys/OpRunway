"""全部判据实现与注册表。

判据分五层，每层对应 spec §7.1。这里只放实现，判据该用在哪条要素上
由 references/taskdoc-elements.json 的 checks 字段决定——骨架是唯一
可编辑对象，加判据要先进骨架。
"""

import json
import re
from collections import namedtuple
from pathlib import Path

REFERENCES = Path(__file__).resolve().parents[1] / "references"

Finding = namedtuple("Finding", "rule element section line message")

LAYERS = {
    "L0": {"nonempty", "section_present", "no_placeholder", "no_inline_link",
           "table_header_matches", "enum_value"},
    "L1": {"signature_matches_param_table", "range_matches_distribution",
           "range_matches_algorithm", "models_covered_by_perf_table",
           "dtypes_covered_by_threshold_table", "thresholds_match_standard",
           "params_covered_by_generation_table"},
    "L2": {"no_unbounded_range", "no_vague_word", "perf_criterion_has_comparator",
           "error_column_not_uniform", "per_dtype_threshold",
           "env_lists_third_party_versions", "random_strategy_when_signaled"},
    "L3": {"human_reply_recorded"},
    "L4": {"deliverables_complete", "pr_target_is_a_path"},
}

# 图片引用不是隐藏跳转链接。模板 §8 固定内容里就有 ![环境截图](./pics/x.png)。
_INLINE_LINK = re.compile(r"(?<!!)\[[^\]]+\]\([^)]+\)")


def _vocab():
    return json.loads((REFERENCES / "dtype-vocab.json").read_text(encoding="utf-8"))


def _vague():
    return json.loads((REFERENCES / "vague-words.json").read_text(encoding="utf-8"))


def _section_text(doc, number):
    section = doc.sections.get(number)
    return "\n".join(section.lines) if section else ""


def _element_lines(doc, element):
    """返回 (行号, 文本)，跳过表格分隔行与空行。"""
    section = doc.sections.get(element["section"])
    if not section:
        return []
    out = []
    for offset, line in enumerate(section.lines):
        text = line.strip()
        if text and not re.match(r"^\|[\s:|-]+\|$", text):
            out.append((section.start_line + 1 + offset, text))
    return out


def check_section_present(doc, key, element, context):
    number = element["section"]
    if number in doc.sections:
        return []
    return [Finding("section_present", key, number, 1,
                    f"§{number} 缺失 · {element['name']}无处声明 · "
                    f"{element['failure']}")]


def check_nonempty(doc, key, element, context):
    section = doc.sections.get(element["section"])
    if not section:
        return []
    body = [l for l in section.lines if l.strip()]
    if body:
        return []
    return [Finding("nonempty", key, element["section"], section.start_line,
                    f"§{element['section']} {element['name']} 为空 · "
                    f"{element['failure']}")]


def check_no_placeholder(doc, key, element, context):
    tokens = _vague()["placeholders"]
    out = []
    for line, text in _element_lines(doc, element):
        for token in tokens:
            if token in text:
                out.append(Finding(
                    "no_placeholder", key, element["section"], line,
                    f"残留占位符「{token}」：{text[:50]}"))
                break
    return out


def check_no_inline_link(doc, key, element, context):
    out = []
    for line, text in _element_lines(doc, element):
        if _INLINE_LINK.search(text):
            out.append(Finding(
                "no_inline_link", key, element["section"], line,
                "链接要裸露，写成「说明：https://...」而不是 [说明](https://...)"))
    return out


def check_table_header_matches(doc, key, element, context):
    expected = context.get("expected_header", {}).get(element["section"])
    section = doc.sections.get(element["section"])
    if not expected or not section or not section.tables:
        return []
    header = section.tables[0].header
    if header == expected:
        return []
    return [Finding("table_header_matches", key, element["section"],
                    section.tables[0].start_line,
                    f"表头应为 {expected}，实际 {header}")]


def check_enum_value(doc, key, element, context, vocabulary=None):
    allowed = _vocab().get(vocabulary, [])
    section = doc.sections.get(element["section"])
    if not allowed or not section:
        return []
    if element["form"] == "table_column":
        return _enum_in_column(section, key, element, allowed, vocabulary)
    text = "\n".join(section.lines)
    if any(value in text for value in allowed):
        return []
    return [Finding("enum_value", key, element["section"], section.start_line,
                    f"{element['name']} 必须取 {allowed} 之一")]


def _enum_in_column(section, key, element, allowed, vocabulary):
    if not section.tables:
        return []
    table = section.tables[0]
    if element["name"] not in table.header:
        return []
    index = table.header.index(element["name"])
    out = []
    for offset, row in enumerate(table.rows):
        if index >= len(row):
            continue
        cell = row[index]
        values = [v.strip() for v in re.split(r"[、,，/]", cell) if v.strip()]
        bad = [v for v in values if v not in allowed]
        if bad:
            out.append(Finding(
                "enum_value", key, element["section"],
                table.start_line + 2 + offset,
                f"{element['name']} 取值 {bad} 不在词表 {vocabulary} 内，"
                f"合法值：{allowed}"))
    return out


CHECKS = {
    "section_present": check_section_present,
    "nonempty": check_nonempty,
    "no_placeholder": check_no_placeholder,
    "no_inline_link": check_no_inline_link,
    "table_header_matches": check_table_header_matches,
    "enum_value": check_enum_value,
}


def _param_table(doc):
    section = doc.sections.get("2.4")
    return section.tables[0] if section and section.tables else None


def _table_missing(rule, key, element, section_number, doc, note):
    """required 的表格缺失本身就是判红，不是悄悄 return []。

    L1/L2 一批判据靠 §2.4 参数表或 §3.5 生成规则表才算得出结论，表格
    整个被换成一句话时，旧写法一律 `if not table: return []`——表越
    关键、依赖它的判据越多，静默跳过的判据就越多，五层能全 0。
    """
    section = doc.sections.get(section_number)
    return [Finding(rule, key, section_number,
                    section.start_line if section else 1,
                    f"§{section_number} 缺必需的表格，{note} · "
                    f"{element['failure']}")]


def _column(table, name):
    if not table or name not in table.header:
        return {}
    index = table.header.index(name)
    key = table.header.index("参数名") if "参数名" in table.header else 0
    out = {}
    for offset, row in enumerate(table.rows):
        if max(index, key) >= len(row):
            continue
        out[row[key]] = (row[index], offset)
    return out


def _normalize(name):
    return name.replace("_", "").lower()


def check_signature_matches_param_table(doc, key, element, context):
    import _signature
    section = doc.sections.get("2.3")
    if not section:
        return []
    table = _param_table(doc)
    if not table:
        return _table_missing("signature_matches_param_table", key, element,
                              "2.4", doc, "签名无法与参数表核对")
    declared = _signature.parameter_names("\n".join(section.code_blocks))
    if not declared:
        return [Finding("signature_matches_param_table", key, "2.3",
                        section.start_line,
                        "§2.3 解析不出参数名，接口定义要写成代码块")]
    listed = [row[0] for row in table.rows if row and row[0]]
    out = []
    listed_normal = {_normalize(n): n for n in listed}
    for name in declared:
        if name in listed:
            continue
        alias = listed_normal.get(_normalize(name))
        if alias:
            out.append(Finding(
                "signature_matches_param_table", key, "2.4", table.start_line,
                f"参数「{name}」在 §2.4 写成「{alias}」，命名风格不一致 · "
                f"§2.4 参数名要与 §2.3 逐字一致"))
        else:
            out.append(Finding(
                "signature_matches_param_table", key, "2.4", table.start_line,
                f"§2.3 声明了「{name}」，§2.4 参数表没有这一行 · "
                f"{element['failure']}"))
    declared_normal = {_normalize(n) for n in declared}
    for name in listed:
        if _normalize(name) not in declared_normal:
            out.append(Finding(
                "signature_matches_param_table", key, "2.4", table.start_line,
                f"§2.4 有「{name}」但 §2.3 签名里没有 · 多余参数或签名不全"))
    return out


_INTERVAL = re.compile(r"[\[(]\s*(-?[\d.eE+-]+|-∞)\s*,\s*(-?[\d.eE+-]+|∞)\s*[\])]")


def _generation_table(doc):
    section = doc.sections.get("3.5")
    if not section:
        return None
    for table in section.tables:
        if "参数名" in table.header:
            return table
    return None


def check_range_matches_distribution(doc, key, element, context):
    table, generation = _param_table(doc), _generation_table(doc)
    if not table or not generation:
        missing = "2.4" if not table else "3.5"
        return _table_missing("range_matches_distribution", key, element,
                              missing, doc, "值域与生成规则无法核对一致性")
    ranges = _column(table, "值域范围")
    dist = _column(generation, "Tensor值域分布")
    attrs = _column(generation, "Attr 覆盖规则")
    out = []
    for name, (declared, offset) in ranges.items():
        # 一个参数只会在两列之一给规则，另一列填「-」。「-」是真值，
        # 用 `dist or attrs` 取会一路短路成「-」，于是 Attr 参数（Tensor 列
        # 恒为「-」）的值域从来没被核对过——D05 就是这么漏网的。
        tensor_rule = dist.get(name, ("", 0))[0].strip()
        attr_rule = attrs.get(name, ("", 0))[0].strip()
        rule = tensor_rule if tensor_rule not in ("", "-") else attr_rule
        if not rule or rule == "-":
            continue
        if "∞" in declared and rule not in ("-", ""):
            out.append(Finding(
                "range_matches_distribution", key, "2.4",
                table.start_line + 2 + offset,
                f"「{name}」§2.4 值域写 {declared}，§3.5 却给了具体生成规则"
                f"「{rule}」 · 两处必须一致"))
            continue
        bound = _INTERVAL.search(declared)
        if not bound:
            continue
        # 生成规则一句话里常见两组括号：分布参数（如「(0, 1)正态分布」）与
        # 截断边界（如「截断至 [-4, 4]」）。只取第一组会把分布参数误认成
        # 边界，所以要看全部候选，命中任意一组就算一致。
        rule_bounds = list(_INTERVAL.finditer(rule))
        if rule_bounds and not any(
                b.groups() == bound.groups() for b in rule_bounds):
            out.append(Finding(
                "range_matches_distribution", key, "2.4",
                table.start_line + 2 + offset,
                f"「{name}」§2.4 值域 {bound.group(0)} 与 §3.5 生成规则里的边界 "
                f"{[b.group(0) for b in rule_bounds]} 都不一致"))
    return out


def check_range_matches_algorithm(doc, key, element, context):
    """§2.1 算法说明里写了某个参数取某值，该值必须落在 §2.4 声明的值域内。"""
    algorithm = _section_text(doc, "2.1")
    if not algorithm:
        return []
    table = _param_table(doc)
    if not table:
        return _table_missing("range_matches_algorithm", key, element,
                              "2.4", doc, "算法说明里的取值无法核对值域")
    out = []
    for name, (declared, offset) in _column(table, "值域范围").items():
        bound = _INTERVAL.search(declared)
        if not bound:
            continue
        for hit in re.finditer(
                rf"{re.escape(name)}\s*(?:按|取|为|=)\s*(-?\d+)", algorithm):
            value = int(hit.group(1))
            low, high = bound.group(1), bound.group(2)
            open_low = declared.strip().startswith("(")
            if low not in ("-∞",) and (
                    value < float(low) or (open_low and value == float(low))):
                out.append(Finding(
                    "range_matches_algorithm", key, "2.4",
                    table.start_line + 2 + offset,
                    f"§2.1 说「{name}」可取 {value}，不在 §2.4 声明的 "
                    f"{declared} 内"))
    return out


# \b 在 Python re 里认 CJK 字符为词字符，"基于910B3的性能" 里「于」和「9」
# 之间就没有边界，\b91… 直接匹配不到。改成显式排除 ASCII 字母数字邻居。
_MODEL = re.compile(r"(?<![0-9A-Za-z])91\d[A-Z]\d?(?![0-9A-Za-z])")


def _models(text):
    return set(_MODEL.findall(text))


def check_models_covered_by_perf_table(doc, key, element, context):
    """§3.1 硬件型号与 §3.3 性能判据覆盖的型号必须是同一个集合。

    只查「§3.1 有但 §3.3 没有」是单向的：§3.1 写「A2 系列产品」这种不带
    具体型号的写法时，declared 集合是空的，单向差集永远算不出缺口。
    两边都可能是缺口的源头，所以用对称差——集合不同就是不一致，不管
    是哪边漏了。
    """
    hardware = _models(_section_text(doc, "3.1"))
    perf = _models(_section_text(doc, "3.3"))
    missing = sorted(hardware ^ perf)
    if not missing:
        return []
    section = doc.sections.get("3.3")
    return [Finding("models_covered_by_perf_table", key, "3.3",
                    section.start_line if section else 1,
                    f"§3.1 型号 {sorted(hardware)} 与 §3.3 性能要求覆盖的型号 "
                    f"{sorted(perf)} 对不上，缺 {missing} · {element['failure']}")]


def _tensor_dtypes(doc):
    table = _param_table(doc)
    if not table:
        return set()
    kinds = _column(table, "数据类型")
    out = set()
    for name, (dtype, _) in _column(table, "dtype类型").items():
        if kinds.get(name, ("", 0))[0] != "tensor":
            continue
        out.update(v.strip() for v in re.split(r"[、,，/]", dtype) if v.strip())
    return {d for d in out if d not in ("-", "int", "float", "bool")}


def check_dtypes_covered_by_threshold_table(doc, key, element, context):
    if not _param_table(doc):
        return _table_missing("dtypes_covered_by_threshold_table", key,
                              element, "2.4", doc, "dtype 覆盖范围无法核对")
    needed = _tensor_dtypes(doc)
    threshold = _section_text(doc, "3.2")
    missing = sorted(d for d in needed if d.upper() not in threshold.upper())
    if not missing:
        return []
    section = doc.sections.get("3.2")
    return [Finding("dtypes_covered_by_threshold_table", key, "3.2",
                    section.start_line if section else 1,
                    f"§2.4 出现的 dtype {missing} 在 §3.2 阈值表里没有 · "
                    f"{element['failure']}")]


_SUPERSCRIPT = str.maketrans({
    "⁻": "^-", "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
    "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
})


def _dtype_key(name):
    return re.sub(r"\s+", "", name).upper()


def _standard_thresholds():
    """按 dtype 键入 experimental_standard.md「阈值表」的 rtol/atol。

    标准表是「一 dtype 一行」，任务书 §3.2 是「一 dtype 一列」，转置过来
    才能逐 dtype 核对，而不是只问某个 token 在整份标准文档里出没出现过。
    """
    import _taskdoc_parser as taskdoc_parser
    lines = (REFERENCES / "experimental_standard.md").read_text(
        encoding="utf-8").translate(_SUPERSCRIPT).splitlines()
    out = {}
    for table in taskdoc_parser._tables(lines, 0):
        if not table.header or table.header[0] != "数据类型":
            continue
        if "rtol" not in table.header or "atol" not in table.header:
            continue
        rtol_index = table.header.index("rtol")
        atol_index = table.header.index("atol")
        for row in table.rows:
            if not row:
                continue
            rtol = re.search(r"2\^-\d+", row[rtol_index]) \
                if rtol_index < len(row) else None
            atol = re.search(r"2\^-\d+", row[atol_index]) \
                if atol_index < len(row) else None
            out[_dtype_key(row[0])] = {
                "rtol": rtol.group(0) if rtol else None,
                "atol": atol.group(0) if atol else None,
            }
    return out


def check_thresholds_match_standard(doc, key, element, context):
    """阈值数值与 experimental_standard.md 逐 dtype 核对，对不上就是抄错了。

    标准文档用 Unicode 上标写指数（如「2⁻⁹」），任务书用脱字符写法
    （如「2^-9」）。两种写法数值相同，比对前先把标准文档转写成脱字符
    形式，否则每一份写法正确的任务书都会被误判成抄错。

    只问「这个数值在标准里出没出现过」抓不出抄错列：标准表里本来就有
    1/2/3/4/6/9/10 这些指数，任何 dtype 填任何一档都能在全文里找到同一个
    token。必须按任务书表头声明的 dtype 名，去标准表里查这个 dtype 自己
    的那一档，再比对数值，才能抓出「FLOAT16 填成了 FLOAT8 的阈值」
    或「FLOAT16 与 BFLOAT16 两列对调」这类抄写错误。
    """
    section = doc.sections.get("3.2")
    if not section:
        return []
    standard = _standard_thresholds()
    out = []
    for table in section.tables:
        if not any("rtol" in c or "atol" in c for c in
                   [table.header[0]] + [r[0] for r in table.rows if r]):
            continue
        for row in table.rows:
            if not row or row[0] not in ("rtol", "atol"):
                continue
            metric = row[0]
            for index, cell in enumerate(row[1:], start=1):
                if index >= len(table.header):
                    continue
                dtype = table.header[index]
                number = re.search(r"2\^-\d+", cell)
                if not number:
                    continue
                expected = standard.get(_dtype_key(dtype), {}).get(metric)
                if expected and number.group(0) != expected:
                    out.append(Finding(
                        "thresholds_match_standard", key, "3.2",
                        table.start_line,
                        f"{dtype} 的 {metric} 写 {number.group(0)}，"
                        f"experimental_standard.md 里该 dtype 该项是 "
                        f"{expected} · 抄错了或标准变了"))
    return out


def check_params_covered_by_generation_table(doc, key, element, context):
    table, generation = _param_table(doc), _generation_table(doc)
    if not table or not generation:
        missing_section = "2.4" if not table else "3.5"
        return _table_missing("params_covered_by_generation_table", key,
                              element, missing_section, doc,
                              "参数覆盖范围无法核对")
    listed = {row[0] for row in table.rows if row and row[0]}
    covered = {row[0] for row in generation.rows if row and row[0]}
    missing = sorted(listed - covered)
    if not missing:
        return []
    return [Finding("params_covered_by_generation_table", key, "3.5",
                    generation.start_line,
                    f"§2.4 的参数 {missing} 在 §3.5 生成规则表里没有 · "
                    f"{element['failure']}")]


CHECKS.update({
    "signature_matches_param_table": check_signature_matches_param_table,
    "range_matches_distribution": check_range_matches_distribution,
    "range_matches_algorithm": check_range_matches_algorithm,
    "models_covered_by_perf_table": check_models_covered_by_perf_table,
    "dtypes_covered_by_threshold_table": check_dtypes_covered_by_threshold_table,
    "thresholds_match_standard": check_thresholds_match_standard,
    "params_covered_by_generation_table": check_params_covered_by_generation_table,
})


_UNBOUNDED = re.compile(r"\(\s*-?∞\s*,\s*∞\s*\)|\(-inf,\s*inf\)", re.IGNORECASE)
_COMPARATOR = re.compile(r"[≥≤><]=?|不低于|不高于|不超过|至少|不小于|不大于")
_NUMBER = re.compile(r"\d")


def check_no_unbounded_range(doc, key, element, context):
    table = _param_table(doc)
    if not table:
        return _table_missing("no_unbounded_range", key, element, "2.4",
                              doc, "无法核对值域是否无界")
    kinds = _column(table, "数据类型")
    out = []
    for name, (declared, offset) in _column(table, "值域范围").items():
        if not _UNBOUNDED.search(declared):
            continue
        if kinds.get(name, ("", 0))[0] != "tensor":
            continue
        out.append(Finding(
            "no_unbounded_range", key, "2.4", table.start_line + 2 + offset,
            f"「{name}」值域写 {declared}，生成不出数据 · "
            f"替代写法：`全值域（含 Inf/NaN：不测）` 或给出上下界如 `[-1e4, 1e4]`"))
    return out


def check_no_vague_word(doc, key, element, context):
    if not element["vague_forbidden"]:
        return []
    words = _vague()["words"]
    out = []
    for line, text in _element_lines(doc, element):
        for word, replacement in words.items():
            if word in text:
                out.append(Finding(
                    "no_vague_word", key, element["section"], line,
                    f"「{word}」没有边界 · 改成：{replacement}"))
    return out


def check_perf_criterion_has_comparator(doc, key, element, context):
    text = _section_text(doc, "3.3")
    section = doc.sections.get("3.3")
    if not section:
        return []
    prose = "\n".join(l for l in section.lines if not l.strip().startswith("|"))
    if _COMPARATOR.search(prose) and _NUMBER.search(prose):
        return []
    return [Finding("perf_criterion_has_comparator", key, "3.3",
                    section.start_line,
                    "性能判据必须含比较符与数字，如「不低于标杆的 0.8 倍」 · "
                    f"{element['failure']}")]


def check_error_column_not_uniform(doc, key, element, context):
    table = _param_table(doc)
    if not table:
        return _table_missing("error_column_not_uniform", key, element,
                              "2.4", doc, "无法核对异常行为列")
    if "异常行为" not in table.header:
        return []
    index = table.header.index("异常行为")
    values = [row[index].strip() for row in table.rows
              if index < len(row) and row[index].strip() not in ("", "-")]
    if len(values) < 2 or len(set(values)) > 1:
        return []
    return [Finding("error_column_not_uniform", key, "2.4", table.start_line,
                    f"异常行为列 {len(values)} 行全是「{values[0]}」 · "
                    f"要对非法 dtype、非法 shape、非法值域分别写出具体触发条件"
                    f" · {element['failure']}")]


def check_per_dtype_threshold(doc, key, element, context):
    section = doc.sections.get("3.2")
    if not section:
        return []
    if any(len(t.header) >= 2 for t in section.tables):
        return []
    return [Finding("per_dtype_threshold", key, "3.2", section.start_line,
                    "§3.2 必须逐 dtype 给阈值表，不能只写一句「满足生态标准」 · "
                    f"{element['failure']}")]


_VERSIONED = re.compile(r"\d+\.\d+")
# 驱动/CUDA 版本号后面常跟点号版本串（535.104.05、12.2），关键词与数字之间
# 允许少量非数字字符（如「：」「 」），但数字本身不能被打断——见 task-doc-
# template.md §3.1 的填写要求：「性能对标 GPU 时还要写标杆环境的驱动与
# CUDA 版本」。
_DRIVER_VERSIONED = re.compile(r"驱动\D{0,6}\d+(?:\.\d+){1,3}")
_CUDA_VERSIONED = re.compile(r"CUDA\D{0,6}\d+(?:\.\d+){1,3}", re.IGNORECASE)


def check_env_lists_third_party_versions(doc, key, element, context):
    text = _section_text(doc, element["section"])
    if not text:
        return []
    if element["section"] == "3.1":
        section = doc.sections["3.1"]
        missing = [name for name in ("torch",) if name not in text.lower()]
        if missing:
            return [Finding("env_lists_third_party_versions", key, "3.1",
                            section.start_line,
                            f"§3.1 未写三方软件版本（缺 {missing}） · "
                            f"{element['failure']}")]
        if not (_DRIVER_VERSIONED.search(text)
                and _CUDA_VERSIONED.search(text)):
            return [Finding("env_lists_third_party_versions", key, "3.1",
                            section.start_line,
                            "§3.1 缺 GPU 标杆的驱动/CUDA 版本 · "
                            f"{element['failure']}")]
        return []
    if not _VERSIONED.search(text):
        section = doc.sections[element["section"]]
        return [Finding("env_lists_third_party_versions", key,
                        element["section"], section.start_line,
                        f"§{element['section']} 提到了环境但没有版本号 · "
                        f"{element['failure']}")]
    return []


_STRATEGIES = ("equal_vs_builtin_pinned_seed", "deterministic_boundary_only",
               "distribution_test", "self_consistency")


def check_random_strategy_when_signaled(doc, key, element, context):
    if not context.get("random_signal_hit"):
        return []
    text = _section_text(doc, "3.2")
    section = doc.sections.get("3.2")
    if any(s in text for s in _STRATEGIES) or "不涉及" in text:
        return []
    return [Finding("random_strategy_when_signaled", key, "3.2",
                    section.start_line if section else 1,
                    "正文命中随机数信号词，§3.2 必须给出对比判定策略 · "
                    f"四选一：{_STRATEGIES}，或写「不涉及」并说明理由 · "
                    f"{element['failure']}")]


CHECKS.update({
    "no_unbounded_range": check_no_unbounded_range,
    "no_vague_word": check_no_vague_word,
    "perf_criterion_has_comparator": check_perf_criterion_has_comparator,
    "error_column_not_uniform": check_error_column_not_uniform,
    "per_dtype_threshold": check_per_dtype_threshold,
    "env_lists_third_party_versions": check_env_lists_third_party_versions,
    "random_strategy_when_signaled": check_random_strategy_when_signaled,
})


def check_human_reply_recorded(doc, key, element, context):
    """红线 A：human 拍板的要素必须有一段人的答复原话。"""
    if element["decision_owner"] != "human":
        return []
    record = context.get("decisions", {}).get(key)
    section = doc.sections.get(element["section"])
    line = section.start_line if section else 1
    if not record:
        return [Finding("human_reply_recorded", key, element["section"], line,
                        f"{key}（{element['name']}）在 decisions.json 里没有记录 · "
                        f"agent 推断不是事实，必须人拍板")]
    if not str(record.get("human_reply", "")).strip():
        return [Finding("human_reply_recorded", key, element["section"], line,
                        f"{key}（{element['name']}）的 human_reply 为空 · "
                        f"agent 声称已确认不算数")]
    return []


def check_deliverables_complete(doc, key, element, context):
    required = ("设计文档", "自测用例", "自测报告", "代码")
    text = _section_text(doc, "4")
    missing = [item for item in required if item not in text]
    if not missing:
        return []
    section = doc.sections.get("4")
    return [Finding("deliverables_complete", key, "4",
                    section.start_line if section else 1,
                    f"§4 交付件缺 {missing} · {element['failure']}")]


def check_pr_target_is_a_path(doc, key, element, context):
    text = _section_text(doc, "5")
    section = doc.sections.get("5")
    line = section.start_line if section else 1
    hit = re.search(r"https?://\S+", text)
    if not hit:
        return [Finding("pr_target_is_a_path", key, "5", line,
                        f"§5 没有裸链接的提交地址 · {element['failure']}")]
    tail = hit.group(0).rstrip("。 ，").split("//", 1)[-1]
    if tail.count("/") < 4:
        return [Finding("pr_target_is_a_path", key, "5", line,
                        f"§5 的地址 {hit.group(0)} 只到仓一级，要具体到目录 · "
                        f"{element['failure']}")]
    return []


CHECKS.update({
    "human_reply_recorded": check_human_reply_recorded,
    "deliverables_complete": check_deliverables_complete,
    "pr_target_is_a_path": check_pr_target_is_a_path,
})


def run_layer(doc, spine, layer, context):
    """跑一层判据。conditional 要素的条件不成立时整条跳过。

    骨架上写了名字却没有实现，必须炸而不是跳过：静默跳过的后果是骨架
    声明了一条判据、门禁从不跑它，而门禁照样退 0，看起来比没写这条判据
    还可信。「不属于本层」是另一回事——每条判据只归一层，别的层路过是常态。
    """
    wanted = LAYERS[layer]
    findings = []
    for key, element in spine["elements"].items():
        if element["required"] == "conditional" and not context.get(
                element["condition"]):
            continue
        for spec in element["checks"]:
            name, _, argument = spec.partition(":")
            if name not in CHECKS:
                raise KeyError(
                    f"{key} 声明了判据 {name}，但 _checks.CHECKS 里没有实现 · "
                    f"要么补实现，要么从 taskdoc-elements.json 里删掉")
            if name not in wanted:
                continue
            if argument and name == "enum_value":
                findings.extend(CHECKS[name](doc, key, element, context,
                                             vocabulary=argument))
            else:
                findings.extend(CHECKS[name](doc, key, element, context))
    return _dedupe(findings)


def _dedupe(findings):
    seen, out = set(), []
    for finding in findings:
        mark = (finding.rule, finding.section, finding.line, finding.message)
        if mark in seen:
            continue
        seen.add(mark)
        out.append(finding)
    return sorted(out, key=lambda f: (f.section, f.line, f.rule))
