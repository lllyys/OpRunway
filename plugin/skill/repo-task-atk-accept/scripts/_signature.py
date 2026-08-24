"""从接口定义代码块里抽参数名。

只认两种形态：C 风格的 aclnn 声明和 Python 的 def。
其他形态返回空列表，由门禁报「签名解析不出参数」而不是在这里猜。
"""

import re

# workspace 四件套是 aclnn 调用约定的固定部分，不是算子参数。
# 它们不该出现在 §2.4 参数表里，比对时要先摘掉。
BOILERPLATE = frozenset({"workspace", "workspaceSize", "executor", "stream"})

_C_PARAM = re.compile(r"([A-Za-z_]\w*)\s*(?:\[\s*\d*\s*\])?\s*$")
_PY_DEF = re.compile(r"^\s*def\s+\w+\s*\(", re.MULTILINE)


def _split_top_level(text):
    """按逗号切分，跳过括号与尖括号内部的逗号。"""
    depth, current, out = 0, [], []
    for ch in text:
        if ch in "([<":
            depth += 1
        elif ch in ")]>":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        out.append("".join(current))
    return out


def _python_names(code):
    start = _PY_DEF.search(code)
    if not start:
        return []
    body = code[start.end():]
    depth, end = 1, len(body)
    for index, ch in enumerate(body):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                end = index
                break
    names = []
    for chunk in _split_top_level(body[:end]):
        name = chunk.split(":")[0].split("=")[0].strip().lstrip("*")
        if name and name not in ("self", "cls"):
            names.append(name)
    return names


def _c_names(code):
    names, seen = [], set()
    for match in re.finditer(r"\b\w+\s*\(([^;{]*?)\)", code, re.DOTALL):
        for chunk in _split_top_level(match.group(1)):
            chunk = chunk.strip().rstrip(",")
            if not chunk:
                continue
            found = _C_PARAM.search(chunk.replace("*", " ").replace("&", " "))
            if not found:
                continue
            name = found.group(1)
            if name in BOILERPLATE or name in seen:
                continue
            seen.add(name)
            names.append(name)
    return names


def parameter_names(code):
    """返回参数名，保持声明顺序，已摘掉 workspace 四件套与重复项。"""
    if _PY_DEF.search(code):
        return [n for n in _python_names(code) if n not in BOILERPLATE]
    return _c_names(code)


def _normalize(name):
    return name.replace("_", "").lower()


def differs_from_baseline(baseline_names, actual_names):
    """§2.3 的参数顺序是否与 §2.1 标杆接口不一致——这是文档属性，不是
    decisions.json 的属性（spec §5.4：条件是「§2.3 参数序列与标杆接口
    不一致」）。

    aclnn 的 C 签名天然会多出 output、`xxxLen` 这类没有基线对应物的
    参数，直接比较两份完整列表几乎永远不相等，那样会让 3.5.param_mapping
    对每一份任务书都变必填，判据形同虚设。只取两边都出现的参数（按各自
    列表内的相对顺序），顺序不一致才算「不一致」——output、windowSizeLen
    这类单边参数会被两边同时过滤掉，不参与比较。

    任一边取不到参数名（如 §2.1 只有文字描述、没有代码块）时无法判断，
    按不冲突处理：宁可偶尔漏问，也不让一个永远打不开的条件变成对
    每份任务书都强加的必填项。
    """
    if not baseline_names or not actual_names:
        return False
    base_norm = [_normalize(n) for n in baseline_names]
    actual_norm = [_normalize(n) for n in actual_names]
    shared = set(base_norm) & set(actual_norm)
    base_common = [n for n in base_norm if n in shared]
    actual_common = [n for n in actual_norm if n in shared]
    return base_common != actual_common
