"""解析直接调用的 C 接口，并生成 c_api 调用序列表。"""

import ast
import re


class CApiSignatureError(ValueError):
    """C 接口声明不足以生成可靠调用序列。"""


_IDENTIFIER = re.compile(r"[A-Za-z_]\w*")
_INTEGER_CTYPES = {
    "char": "int8",
    "signed char": "int8",
    "unsigned char": "uint8",
    "short": "int16",
    "short int": "int16",
    "unsigned short": "uint16",
    "unsigned short int": "uint16",
    "int": "int32",
    "signed": "int32",
    "signed int": "int32",
    "unsigned": "uint32",
    "unsigned int": "uint32",
    "long": "long",
    "unsigned long": "ulong",
    "long long": "int64",
    "unsigned long long": "uint64",
    "int8_t": "int8",
    "uint8_t": "uint8",
    "int16_t": "int16",
    "uint16_t": "uint16",
    "int32_t": "int32",
    "uint32_t": "uint32",
    "int64_t": "int64",
    "uint64_t": "uint64",
    "size_t": "size_t",
    "ssize_t": "ssize_t",
    "ptrdiff_t": "ptrdiff_t",
}
_ARITHMETIC_TYPES = set(_INTEGER_CTYPES) | {
    "bool", "_Bool", "float", "double", "long double", "half", "__fp16",
    "float16_t", "bfloat16_t",
}
_POINTER_CTYPE = {
    "bool": "bool",
    "_Bool": "bool",
    "float": "float32",
    "double": "float64",
    "long double": "long_double",
    "half": "float16",
    "__fp16": "float16",
    "float16_t": "float16",
    "bfloat16_t": "bfloat16",
    **_INTEGER_CTYPES,
}
_LAYOUT_NAME = re.compile(r"(?i)^(?:lda|ldb|ldc|incx|incy|stride\w*)$")
_AMBIGUOUS_SCALAR_NAME = {"alpha", "beta"}
_TYPE_QUALIFIERS = {"const", "volatile", "restrict", "__restrict", "__restrict__"}
_PUBLIC_ACL_ALIASES = {"aclrtStream": "void*"}
_DEFAULT_INITIALIZER = re.compile(r"\s*=\s*[^=;]+$")
_STRUCT_FIELD_CTYPES = {
    "size_t": "c_size_t",
    "bool": "c_bool",
    "_Bool": "c_bool",
    "int": "c_int32",
    "signed": "c_int32",
    "signed int": "c_int32",
    "int32_t": "c_int32",
    "long long": "c_int64",
    "signed long long": "c_int64",
    "int64_t": "c_int64",
    "float": "c_float",
    "double": "c_double",
}


def _without_comments(text):
    """移除注释并保留换行，便于后续按原位置判断 extern C。"""
    def replace(match):
        value = match.group(0)
        return "".join("\n" if char == "\n" else " " for char in value)

    clean = re.sub(r"//[^\n]*|/\*.*?\*/", replace, text, flags=re.S)
    # 条件编译行不属于函数声明；清空内容但保留位置和换行。
    return re.sub(r"(?m)^[ \t]*\#.*$", replace, clean)


def _normalise_space(text):
    text = re.sub(r"\s+", " ", text.strip())
    text = re.sub(r"\s*::\s*", "::", text)
    text = re.sub(r"\s*<\s*", "<", text)
    text = re.sub(r"\s*>\s*", ">", text)
    return text


def _matching(text, start, opener="(", closer=")"):
    depth = 0
    quote = None
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return index
    return None


def _split_params(text):
    """按顶层逗号切参数，模板参数和数组括号内的逗号不参与切分。"""
    parts = []
    start = 0
    round_depth = square_depth = angle_depth = brace_depth = 0
    for index, char in enumerate(text):
        if char == "(":
            round_depth += 1
        elif char == ")":
            round_depth -= 1
        elif char == "[":
            square_depth += 1
        elif char == "]":
            square_depth -= 1
        elif char == "<":
            angle_depth += 1
        elif char == ">" and angle_depth:
            angle_depth -= 1
        elif char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth -= 1
        elif char == "," and not any(
                (round_depth, square_depth, angle_depth, brace_depth)):
            parts.append(text[start:index].strip())
            start = index + 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _parse_param(raw, position):
    text = raw.strip()
    if not text:
        raise CApiSignatureError(f"第 {position + 1} 个参数为空")
    if text == "void":
        return None

    # 默认值不属于 C 调用 ABI；仅容忍不含嵌套表达式的常见写法。
    text = re.sub(r"\s*=\s*(?:nullptr|NULL|[-+]?\d+)\s*$", "", text)
    match = re.search(r"([A-Za-z_]\w*)\s*((?:\[[^\]]*\]\s*)*)$", text)
    if not match:
        raise CApiSignatureError(f"第 {position + 1} 个参数解析不出参数名：{raw!r}")
    name = match.group(1)
    array_suffix = match.group(2) or ""
    type_text = text[:match.start()].strip()
    if not type_text:
        raise CApiSignatureError(f"参数 {name} 解析不出类型：{raw!r}")

    pointer_depth = type_text.count("*")
    array_depth = array_suffix.count("[")
    before_pointer = type_text.split("*", 1)[0]
    is_const = "const" in before_pointer.split()
    base_words = [word for word in re.split(r"\s+", type_text.replace("*", " "))
                  if word and word not in _TYPE_QUALIFIERS]
    base_type = _normalise_space(" ".join(base_words))
    if not base_type:
        raise CApiSignatureError(f"参数 {name} 解析不出基础类型：{raw!r}")

    suffix = "[]" * array_depth
    return {
        "position": position,
        "c_declaration": _normalise_space(raw),
        "name": name,
        "c_type": _normalise_space(type_text) + suffix,
        "base_type": base_type,
        "pointer_depth": pointer_depth,
        "array_depth": array_depth,
        "is_const": is_const,
        "is_pointer": bool(pointer_depth or array_depth),
        "is_pointer_array": pointer_depth >= 2 or bool(array_depth and pointer_depth),
    }


def _declaration_span(text, candidate_name=None):
    clean = _without_comments(text)
    name_pattern = (re.escape(candidate_name) if candidate_name
                    else r"[A-Za-z_]\w*")
    pattern = re.compile(rf"\b({name_pattern})\s*\(")
    matches = []
    for match in pattern.finditer(clean):
        open_paren = clean.find("(", match.start())
        close_paren = _matching(clean, open_paren)
        if close_paren is None:
            continue
        after = close_paren + 1
        while after < len(clean) and clean[after].isspace():
            after += 1
        # 容忍尾部的 noexcept，但不把函数定义当公开声明。
        if clean.startswith("noexcept", after):
            after += len("noexcept")
            while after < len(clean) and clean[after].isspace():
                after += 1
        if after >= len(clean) or clean[after] != ";":
            continue
        previous = max(clean.rfind(";", 0, match.start()),
                       clean.rfind("{", 0, match.start()),
                       clean.rfind("}", 0, match.start()))
        start = previous + 1
        prefix = clean[start:match.start()].strip()
        if not prefix or prefix.startswith("#"):
            continue
        matches.append((start, match.start(), open_paren, close_paren, after,
                        match.group(1)))
    if not matches:
        wanted = candidate_name or "给定文本"
        raise CApiSignatureError(f"找不到 {wanted} 的函数声明")
    if candidate_name and len(matches) > 1:
        declarations = {clean[start:end + 1].strip()
                        for start, _, _, _, end, _ in matches}
        if len(declarations) > 1:
            raise CApiSignatureError(f"{candidate_name} 有多份不一致的声明")
    if not candidate_name and len(matches) > 1:
        names = [item[-1] for item in matches]
        raise CApiSignatureError(f"给定文本含多个函数声明，请明确接口名：{names}")
    return clean, matches[0]


def _inside_extern_c(text, position, declaration_start):
    prefix = text[declaration_start:position]
    if re.search(r"\bextern\s*\"C\"\s+", prefix):
        return True
    for match in re.finditer(r"\bextern\s*\"C\"\s*\{", text):
        brace = text.find("{", match.start())
        end = _matching(text, brace, "{", "}")
        if end is not None and brace < position < end:
            return True
    return False


def parse_declaration(text, candidate_name=None):
    """解析一个函数声明；可从较大的头文件文本中按接口名选取。"""
    clean, span = _declaration_span(text, candidate_name)
    start, name_start, open_paren, close_paren, end, name = span
    prefix = clean[start:name_start].strip()
    prefix = re.sub(r"^extern\s*\"C\"\s*", "", prefix).strip()
    return_type = _normalise_space(prefix)
    if not return_type:
        raise CApiSignatureError(f"{name} 解析不出返回类型")
    raw_params = _split_params(clean[open_paren + 1:close_paren])
    params = []
    for position, raw in enumerate(raw_params):
        parsed = _parse_param(raw, position)
        if parsed is not None:
            params.append(parsed)
    return {
        "symbol": name,
        "return_type": return_type,
        "parameters": params,
        "extern_c": _inside_extern_c(clean, name_start, start),
        "declaration": _normalise_space(clean[start:end + 1]),
    }


def _integer_expression(node, known):
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if isinstance(node, ast.Name) and node.id in known:
        return known[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub, ast.Invert)):
        value = _integer_expression(node.operand, known)
        if isinstance(node.op, ast.UAdd):
            return value
        if isinstance(node.op, ast.USub):
            return -value
        return ~value
    operations = {
        ast.Add: lambda a, b: a + b,
        ast.Sub: lambda a, b: a - b,
        ast.Mult: lambda a, b: a * b,
        ast.FloorDiv: lambda a, b: a // b,
        ast.LShift: lambda a, b: a << b,
        ast.RShift: lambda a, b: a >> b,
        ast.BitOr: lambda a, b: a | b,
        ast.BitAnd: lambda a, b: a & b,
        ast.BitXor: lambda a, b: a ^ b,
    }
    if isinstance(node, ast.BinOp) and type(node.op) in operations:
        return operations[type(node.op)](
            _integer_expression(node.left, known),
            _integer_expression(node.right, known))
    raise ValueError("不是受支持的整数表达式")


def _parse_enum_body(body):
    values = {}
    for item in _split_params(body):
        item = item.strip()
        if not item or "=" not in item:
            continue
        name, expression = item.split("=", 1)
        name = name.strip()
        if not _IDENTIFIER.fullmatch(name):
            continue
        # C 整数后缀不影响这里记录的数学值。
        expression = re.sub(r"(?<=\d)[uUlL]+\b", "", expression.strip())
        try:
            tree = ast.parse(expression, mode="eval")
            values[name] = _integer_expression(tree.body, values)
        except (SyntaxError, TypeError, ValueError, ZeroDivisionError):
            continue
    return values


def scan_enum_typedefs(header_texts):
    """扫描 enum 定义和 typedef 别名，返回类型名到显式整数值的映射。"""
    if isinstance(header_texts, str):
        header_texts = [header_texts]
    result = {}
    tags = {}
    for text in header_texts:
        clean = _without_comments(text)
        pattern = re.compile(
            r"\b(?:(typedef)\s+)?enum(?:\s+([A-Za-z_]\w*))?\s*\{(.*?)\}"
            r"\s*([A-Za-z_]\w*)?\s*;", re.S)
        for match in pattern.finditer(clean):
            is_typedef, tag, body, alias = match.groups()
            values = _parse_enum_body(body)
            if tag:
                tags[tag] = values
                result[tag] = values
            if is_typedef and alias:
                result[alias] = values
        for tag, alias in re.findall(
                r"\btypedef\s+enum\s+([A-Za-z_]\w*)\s+([A-Za-z_]\w*)\s*;", clean):
            if tag in tags:
                result[alias] = tags[tag]
    return result


def _is_arithmetic(base_type):
    return base_type in _ARITHMETIC_TYPES or bool(
        re.fullmatch(r"std::complex<(?:float|double)>", base_type))


def _pointee_ctype(base_type):
    if base_type == "std::complex<float>":
        return "complex64"
    if base_type == "std::complex<double>":
        return "complex128"
    return _POINTER_CTYPE.get(base_type)


def _decision_pair(value, flag):
    if isinstance(value, dict):
        if "name" in value:
            name = value.get("name")
            reason = value.get("source") or value.get("justification")
        elif len(value) == 1:
            name, reason = next(iter(value.items()))
        else:
            name = reason = None
    elif isinstance(value, (tuple, list)) and len(value) == 2:
        name, reason = value
    else:
        raise CApiSignatureError(f"{flag} 必须包含参数名和书面依据")
    if not name or not str(reason).strip():
        raise CApiSignatureError(f"{flag} 必须包含参数名和非空书面依据")
    return str(name), str(reason).strip()


def infer_context(params, context_type=None):
    """按显式类型或 Handle_t 后缀确定首个上下文参数。"""
    if context_type:
        candidates = [param for param in params if param["base_type"] == context_type]
        rule = "--context-type 显式指定"
    else:
        candidates = [param for param in params
                      if param["base_type"].lower().endswith("handle_t")]
        rule = "首个类型名以 Handle_t 结尾"
    if not candidates:
        detail = context_type or "以 Handle_t 结尾的类型"
        raise CApiSignatureError(f"找不到上下文参数：{detail}")
    selected = candidates[0]
    if selected["position"] != 0:
        raise CApiSignatureError(
            f"上下文参数 {selected['name']} 不在首位，v1 只接受首参数上下文约定")
    return {"name": selected["name"], "type": selected["base_type"], "rule": rule}


def _base_arg(param, arg_class, ctype, rule):
    return {
        "position": param["position"],
        "name": param["name"],
        "c_type": param["c_type"],
        "class": arg_class,
        "ctype": ctype,
        "rule": rule,
    }


def probe_classify(params, *, enum_types=None):
    """只探测 v1 可表达性，不要求主机标量、设备指针或输出决策。"""
    if isinstance(enum_types, (list, tuple, set)):
        enum_types = {name: {} for name in enum_types}
    enum_types = enum_types or {}
    context = next(
        (param for param in params
         if param["base_type"].lower().endswith("handle_t")), None)
    result = []
    for param in params:
        name = param["name"]
        base = param["base_type"]
        decision = False
        representable = True
        if context is not None and name == context["name"]:
            arg_class = "context"
            reason = "类型名以 Handle_t 结尾"
        elif base in enum_types:
            arg_class = "enum"
            reason = "类型匹配公开头文件中的 enum"
        elif not param["is_pointer"] and base in _INTEGER_CTYPES:
            arg_class = "layout_param" if _LAYOUT_NAME.fullmatch(name) else "dim"
            reason = "整数值参数属于 v1 已开放类别"
        elif ((base == "void" and param["is_pointer"])
              or "workspace" in name.lower() or "workspace" in base.lower()):
            arg_class = "workspace"
            reason = f"参数 {name} 分类为 workspace，但 v1 未开放该类别"
            representable = False
        elif (re.search(r"(?i)(?:descr|descriptor)", base)
              or (param["is_pointer"] and not _is_arithmetic(base))
              or (base.endswith("_t") and not param["is_pointer"])):
            arg_class = "descriptor"
            reason = f"参数 {name} 分类为 descriptor，但 v1 未开放该类别"
            representable = False
        elif param["is_pointer"] and _is_arithmetic(base):
            arg_class = ("device_ptr_array" if param["is_pointer_array"]
                         else "device_ptr")
            decision = name.lower() in _AMBIGUOUS_SCALAR_NAME
            reason = ("需显式决定主机标量或设备指针"
                      if decision else "算术或复数指针属于 v1 已开放类别")
        elif param["is_pointer"]:
            arg_class = "unknown_pointer"
            reason = f"参数 {name} 的指针类型无法分类：{param['c_type']}"
            representable = False
        else:
            arg_class = "unknown_value"
            reason = f"参数 {name} 的值类型无法分类：{param['c_type']}"
            representable = False
        result.append({
            "position": param["position"],
            "name": name,
            "c_type": param["c_type"],
            "class": arg_class,
            "representable": representable,
            "requires_explicit_decision": decision,
            "reason": reason,
        })
    return result


def classify(params, host_scalar_names, output_name, context_param, *,
             enum_types=None, device_pointer_names=None):
    """分类执行参数；需要语义判断的指针必须带书面依据。"""
    if isinstance(enum_types, (list, tuple, set)):
        enum_types = {name: {} for name in enum_types}
    enum_types = enum_types or {}
    device_pointer_names = device_pointer_names or {}
    host_scalar_names = host_scalar_names or {}
    names = {param["name"] for param in params}
    output, output_reason = _decision_pair(output_name, "--output")
    if output not in names:
        raise CApiSignatureError(f"--output 指定了未知参数 {output}")
    unknown_host = sorted(set(host_scalar_names) - names)
    if unknown_host:
        raise CApiSignatureError(f"--host-scalar 指定了未知参数 {unknown_host[0]}")
    unknown_device = sorted(set(device_pointer_names) - names)
    if unknown_device:
        raise CApiSignatureError(f"--device-pointer 指定了未知参数 {unknown_device[0]}")
    conflict = sorted(set(host_scalar_names) & set(device_pointer_names))
    if conflict:
        raise CApiSignatureError(
            f"参数 {conflict[0]} 不能同时声明为 --host-scalar 和 --device-pointer")

    if isinstance(context_param, str):
        by_name = next((param for param in params if param["name"] == context_param), None)
        by_type = next((param for param in params
                        if param["base_type"] == context_param), None)
        selected = by_name or by_type
        if selected is None:
            raise CApiSignatureError(f"找不到上下文参数或类型 {context_param}")
        context_param = {
            "name": selected["name"], "type": selected["base_type"],
            "rule": "调用方显式指定上下文参数",
        }
    context_name = context_param.get("name")
    context_type = context_param.get("type")
    context_rule = context_param.get("rule")
    matching_context = [param for param in params if param["name"] == context_name]
    if not matching_context or matching_context[0]["base_type"] != context_type:
        raise CApiSignatureError(
            f"上下文声明 {context_name}:{context_type} 与函数参数不一致")

    classified = []
    for param in params:
        name = param["name"]
        base = param["base_type"]
        if name == context_name:
            item = _base_arg(param, "context", "void_p", context_rule)
        elif base in enum_types:
            item = _base_arg(param, "enum", "int32", "类型匹配公开头文件中的 enum")
            item["enum_values"] = dict(enum_types[base])
        elif name in host_scalar_names:
            if (not param["is_pointer"] or param["is_pointer_array"]
                    or not _is_arithmetic(base)):
                raise CApiSignatureError(
                    f"参数 {name} 不能用 --host-scalar：必须是指向算术类型的单层指针")
            item = _base_arg(param, "host_scalar", "void_p", "--host-scalar 显式声明")
            item["pointee_ctype"] = _pointee_ctype(base)
            item["source"] = str(host_scalar_names[name]).strip()
            if not item["source"]:
                raise CApiSignatureError(f"参数 {name} 的 --host-scalar 书面依据为空")
        elif name in device_pointer_names:
            if not param["is_pointer"] or not _is_arithmetic(base):
                raise CApiSignatureError(
                    f"参数 {name} 不能用 --device-pointer：必须指向算术或复数类型")
            arg_class = "device_ptr_array" if param["is_pointer_array"] else "device_ptr"
            item = _base_arg(param, arg_class, "void_p", "--device-pointer 显式声明")
            item["pointee_ctype"] = _pointee_ctype(base)
            item["source"] = str(device_pointer_names[name]).strip()
            if not item["source"]:
                raise CApiSignatureError(f"参数 {name} 的 --device-pointer 书面依据为空")
        elif not param["is_pointer"] and base in _INTEGER_CTYPES:
            arg_class = "layout_param" if _LAYOUT_NAME.fullmatch(name) else "dim"
            item = _base_arg(param, arg_class, _INTEGER_CTYPES[base],
                             "整数值参数按参数名区分布局量与维度量")
        elif ((base == "void" and param["is_pointer"])
              or "workspace" in name.lower() or "workspace" in base.lower()):
            item = _base_arg(param, "workspace", "void_p", "void 指针或名称含 workspace")
        elif (re.search(r"(?i)(?:descr|descriptor)", base)
              or (param["is_pointer"] and not _is_arithmetic(base))
              or (base.endswith("_t") and not param["is_pointer"])):
            item = _base_arg(param, "descriptor", "void_p", "非上下文的不透明类型")
        elif param["is_pointer"] and _is_arithmetic(base):
            if name.lower() in _AMBIGUOUS_SCALAR_NAME:
                raise CApiSignatureError(
                    f"参数 {name} 的 C 类型无法区分主机标量和设备指针；"
                    "必须用 --host-scalar 或 --device-pointer 给出书面依据")
            arg_class = "device_ptr_array" if param["is_pointer_array"] else "device_ptr"
            item = _base_arg(param, arg_class, "void_p", "其余算术或复数指针")
            item["pointee_ctype"] = _pointee_ctype(base)
        elif param["is_pointer"]:
            raise CApiSignatureError(f"参数 {name} 是无法分类的指针参数：{param['c_type']}")
        else:
            raise CApiSignatureError(f"参数 {name} 无法分类：{param['c_type']}")

        if item["class"] in {"descriptor", "workspace"}:
            raise CApiSignatureError(
                f"参数 {name} 分类为 {item['class']}，但 v1 sequence step tier "
                f"未开放 {item['class']}；不能生成调用序列")
        if name == output:
            item["output_source"] = output_reason
        classified.append(item)
    return classified


def _struct_block(header_text, struct_name):
    clean = _without_comments(header_text)
    pattern = re.compile(rf"\bstruct\s+{re.escape(struct_name)}\b")
    for match in pattern.finditer(clean):
        brace = clean.find("{", match.end())
        semicolon = clean.find(";", match.end())
        if brace == -1 or (semicolon != -1 and semicolon < brace):
            continue
        end = _matching(clean, brace, "{", "}")
        if end is not None:
            return clean[match.end():brace], clean[brace + 1:end]
    # 匿名 typedef struct 的别名也可以直接作为 struct_name 传入。
    for match in re.finditer(r"\btypedef\s+struct\s*\{", clean):
        brace = clean.find("{", match.start())
        end = _matching(clean, brace, "{", "}")
        if end is None:
            continue
        tail = re.match(r"\s*([A-Za-z_]\w*)\s*;", clean[end + 1:])
        if tail and tail.group(1) == struct_name:
            return "", clean[brace + 1:end]
    return None


def _typedef_aliases(header_text):
    """读取普通 typedef；只供 struct 字段还原基础 C 类型。"""
    aliases = {}
    clean = _without_comments(header_text)
    for match in re.finditer(
            r"\btypedef\s+([^;{}()]+?)\s+([A-Za-z_]\w*)\s*;", clean):
        target, alias = match.groups()
        aliases[alias] = _normalise_space(target.replace(" *", "*"))
    return aliases


def _resolve_field_type(type_text, aliases):
    value = _normalise_space(type_text)
    seen = set()
    while value not in seen and (value in aliases or value in _PUBLIC_ACL_ALIASES):
        seen.add(value)
        target = aliases[value] if value in aliases else _PUBLIC_ACL_ALIASES[value]
        value = _normalise_space(target)
    return value


def _inspect_trivial_pod_struct(header_text, struct_name):
    """一次解析完成 POD 检查和 ctypes 字段描述。"""
    block = _struct_block(header_text, struct_name)
    if block is None:
        return False, f"找不到 struct {struct_name} 的完整定义", []
    prefix, body = block
    if ":" in prefix:
        return False, f"struct {struct_name} 含基类声明", []
    if re.search(r"\bvirtual\b", body):
        return False, f"struct {struct_name} 含 virtual 成员", []
    if "{" in body or "}" in body:
        return False, f"struct {struct_name} 含嵌套类型或成员实现", []
    fields = [part.strip() for part in body.split(";") if part.strip()]
    if not fields:
        return False, f"struct {struct_name} 没有普通字段", []
    aliases = _typedef_aliases(header_text)
    described = []
    for field in fields:
        field = _DEFAULT_INITIALIZER.sub("", field).strip()
        if re.search(r"\b(?:public|protected|private)\s*:", field):
            return False, f"struct {struct_name} 含访问控制段", []
        if "(" in field or ")" in field:
            return False, f"struct {struct_name} 含函数成员", []
        if re.match(r"\s*(?:using|typedef|static_assert)\b", field):
            return False, f"struct {struct_name} 不只包含普通字段", []
        if any(token in field for token in (",", "[", "]", ":")):
            return False, f"struct {struct_name} 的字段形态未开放：{field!r}", []
        match = re.fullmatch(r"(.+?)\s*([A-Za-z_]\w*)", field)
        if not match:
            return False, f"struct {struct_name} 的字段无法解析：{field!r}", []
        type_text, name = match.groups()
        type_words = [word for word in re.split(r"\s+", type_text.replace("*", " * "))
                      if word and word not in _TYPE_QUALIFIERS]
        resolved = _resolve_field_type(" ".join(type_words).replace(" *", "*"), aliases)
        if "*" in resolved:
            ctype = "c_void_p"
        else:
            ctype = _STRUCT_FIELD_CTYPES.get(resolved)
        if ctype is None:
            return (False,
                    f"struct {struct_name} 的字段 {name} 使用未开放类型 {resolved}",
                    [])
        described.append({"name": name, "ctype": ctype})
    return True, f"struct {struct_name} 只包含普通字段", described


def is_trivial_pod_struct(header_text, struct_name):
    """保守检查 struct 是否只有执行器可构造的普通字段。"""
    ok, reason, _ = _inspect_trivial_pod_struct(header_text, struct_name)
    return ok, reason


def _context_functions(header_text, context_type):
    """按上下文出参与首参数识别公开的生命周期函数。"""
    found = {"create": [], "set_stream": [], "destroy": []}
    clean = _without_comments(header_text)
    function_pattern = re.compile(r"\b([A-Za-z_]\w*)\s*\(([^;{}]*)\)\s*;", re.S)
    for match in function_pattern.finditer(clean):
        name, raw_params = match.groups()
        params = []
        try:
            for position, raw in enumerate(_split_params(raw_params)):
                parsed = _parse_param(raw, position)
                if parsed is not None:
                    params.append(parsed)
        except CApiSignatureError:
            continue
        if not params:
            continue
        first = params[0]
        out_context = any(
            param["base_type"] == context_type and param["is_pointer"]
            for param in params)
        if re.search(r"(?i)Create$", name) and out_context:
            found["create"].append(name)
        elif (re.search(r"(?i)SetStream$", name)
              and first["base_type"] == context_type):
            found["set_stream"].append(name)
        elif (re.search(r"(?i)Destroy$", name)
              and first["base_type"] == context_type):
            found["destroy"].append(name)
    result = {}
    for role, names in found.items():
        names = list(dict.fromkeys(names))
        if len(names) > 1:
            raise CApiSignatureError(
                f"上下文 {role} 函数有多个匹配：{', '.join(names)}")
        result[role] = names[0] if names else None
    return result


def context_shape(header_text, context_type, explicit_shape=None):
    """确定上下文构造形态，并记录命中的规则和公开函数名。"""
    functions = _context_functions(header_text, context_type)

    if explicit_shape:
        shape = explicit_shape
        rule = "--context-shape 显式指定"
    elif functions["create"] and functions["destroy"]:
        shape = "opaque_functions"
        rule = "头文件声明 Create/Destroy"
    else:
        shape = "struct_handle"
        rule = "未找到配对的 Create/Destroy，按公开 struct 构造"
    result = {"shape": shape, "type": context_type, "rule": rule}
    if shape == "opaque_functions":
        if not functions["create"] or not functions["destroy"]:
            raise CApiSignatureError(
                "opaque_functions 需要公开的 Create 与 Destroy 函数")
        result.update(functions)
    return result


def struct_name_for_context(header_text, context_type, explicit=None):
    """从 typedef 关系中找出上下文类型对应的 struct 名。"""
    clean = _without_comments(header_text)
    if explicit:
        if _struct_block(header_text, explicit) is not None:
            return explicit
        raise CApiSignatureError(
            f"--context-struct 指定的 {explicit} 在公开头文件里没有 struct 定义")
    patterns = (
        rf"\btypedef\s+struct\s+([A-Za-z_]\w*)\s*\{{.*?\}}\s*{re.escape(context_type)}\s*;",
        rf"\btypedef\s+struct\s+([A-Za-z_]\w*)\s*\*?\s*{re.escape(context_type)}\s*;",
    )
    for pattern in patterns:
        match = re.search(pattern, clean, re.S)
        if match:
            return match.group(1)
    if re.search(rf"\btypedef\s+struct\s*\{{.*?\}}\s*{re.escape(context_type)}\s*;",
                 clean, re.S):
        return context_type
    if re.search(rf"\bstruct\s+{re.escape(context_type)}\b", clean):
        return context_type
    raise CApiSignatureError(
        f"找不到上下文类型 {context_type} 对应的公开 struct 定义")


def build_sequence(declaration, args, *, output, context, baseline=None,
                   header_text=None):
    """组装 schema_version=1 的 context/execute 调用序列表。"""
    output_name, output_reason = _decision_pair(output, "--output")
    if output_name not in {item["name"] for item in args}:
        raise CApiSignatureError(f"--output 指定了未知参数 {output_name}")
    context_step = {"step": "context", **context}
    if context_step.get("shape") == "struct_handle":
        struct_value = context_step.get("struct")
        struct_name = (struct_value.get("name")
                       if isinstance(struct_value, dict) else struct_value)
        if not struct_name or header_text is None:
            raise CApiSignatureError(
                "struct_handle 需要公开 struct 名和完整头文件文本")
        ok, reason, fields = _inspect_trivial_pod_struct(header_text, struct_name)
        if not ok:
            raise CApiSignatureError(reason)
        context_step["struct"] = {"name": struct_name, "fields": fields}
        context_step["pod_check"] = reason
    execute = {
        "step": "execute",
        "call": declaration["symbol"],
        "timed": True,
        "args": args,
        "status_ok": 0,
    }
    mangled = not declaration["extern_c"]
    table = {
        "schema_version": 1,
        "symbol": declaration["symbol"],
        "exported_name": None if mangled else declaration["symbol"],
        "mangled": mangled,
        "return_type": declaration["return_type"],
        "sequence": [context_step, execute],
        "output": {"in_place": output_name, "source": output_reason},
        # 内存顺序必须由 S1 约束表确认；本轮不从接口声明猜测。
        "layout": {"order": None, "confirmed_by": None},
    }
    if baseline is not None:
        table["baseline"] = baseline
    return table
