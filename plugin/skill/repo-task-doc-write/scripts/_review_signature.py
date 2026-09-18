"""任务书签名解析器（fail-closed 白名单）。

从一段文本里识别函数签名并解析参数名序列，供总审的签名一致性比对使用。
契约见 references/decisions-format.md「签名解析接口」节。

判定规则（白名单，无法证明完整一律判 incomplete）：

- 只认两种形态：恰一个 Python ``def`` 签名，或恰一个 C 风格函数原型。
- 全文找到两个及以上签名候选 → ``multiple``。
- 找不到签名、括号不配平、任一参数位解析不出合法标识符、签名含省略号
  （``...``）或问号占位符（``?`` / ``???``）、参数重名 → ``incomplete``。
- Python：注解与默认值允许；裸 ``*`` 与 ``/`` 是分隔符不计入 params；
  ``*name`` / ``**name`` 以 ``name`` 计入。
- C：以参数位末尾的标识符为参数名，跳过 ``const`` 等修饰词与指针星号；
  参数名不得是 C 关键字或内建类型词；名字之前必须有**类型核**——内建类型词
  或非关键字标识符，仅有 ``const``/``struct`` 这类限定词证明不了「类型+名字」
  俱全（``const aclTensor`` 与 ``struct Tensor`` 判 ``incomplete``）；
  ``void f(void)`` 视为零参 complete；空括号 ``f()`` 在 C 里是「参数未声明」
  而非零参，判 ``incomplete``。
- 配平右括号之后只允许空白与 ``;``/``{``（Python 为 ``:``）；多余右括号等
  尾随残片判 ``incomplete``。

status 非 ``complete`` 时 params 一律为空列表，不放行半解析结果。
"""

import ast
import re

_DEF_RE = re.compile(r"\bdef\s+[A-Za-z_]\w*\s*\(")
# C 原型候选：至少一个类型词 + 函数名 + 左括号（类型部分只允许同一行）。
_C_RE = re.compile(r"(?:\b[A-Za-z_]\w*\b[ \t\*]+)+\**[A-Za-z_]\w*\s*\(")
# 参数位只允许标识符、空白与指针星号（数组后缀先行剥除）。
_C_PARAM_CHARS = re.compile(r"^[A-Za-z_][\w \t\*]*$")
_C_NAME_AT_END = re.compile(r"([A-Za-z_]\w*)\s*$")
_C_KEYWORDS = frozenset({
    "const", "volatile", "restrict", "struct", "union", "enum",
    "unsigned", "signed", "int", "char", "short", "long",
    "float", "double", "void", "register", "static", "extern", "inline",
})
# 限定词：本身不构成类型核。「类型核」= 内建类型词或非关键字标识符。
_C_QUALIFIERS = frozenset({
    "const", "volatile", "restrict", "struct", "union", "enum",
    "register", "static", "extern", "inline",
})

_INCOMPLETE = {"status": "incomplete", "params": []}


def _close_paren(text, open_idx):
    """返回与 open_idx 处左括号配平的右括号下标，配不平返回 -1。"""
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _finish(params):
    if len(set(params)) != len(params):
        return dict(_INCOMPLETE)
    return {"status": "complete", "params": params}


def _parse_python(text, start, close):
    sig = text[start:close + 1]
    try:
        tree = ast.parse(sig + ":\n    pass")
    except SyntaxError:
        return dict(_INCOMPLETE)
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        return dict(_INCOMPLETE)
    a = tree.body[0].args
    params = [x.arg for x in a.posonlyargs + a.args]
    if a.vararg:
        params.append(a.vararg.arg)
    params.extend(x.arg for x in a.kwonlyargs)
    if a.kwarg:
        params.append(a.kwarg.arg)
    return _finish(params)


def _parse_c(text, open_idx, close):
    inner = text[open_idx + 1:close].strip()
    if inner == "void":
        return _finish([])
    if inner == "":
        return dict(_INCOMPLETE)
    params = []
    for part in inner.split(","):
        part = part.strip()
        # 剥除末尾数组后缀，如 int a[8]。
        part = re.sub(r"(\[[^\[\]]*\]\s*)+$", "", part).strip()
        if not _C_PARAM_CHARS.match(part):
            return dict(_INCOMPLETE)
        name_m = _C_NAME_AT_END.search(part)
        if name_m is None:
            return dict(_INCOMPLETE)
        name = name_m.group(1)
        if name in _C_KEYWORDS:
            return dict(_INCOMPLETE)
        # 名字之前必须有类型核：内建类型词或非关键字标识符。仅有限定词
        # （const aclTensor / struct Tensor / const *x）分不清「类型」还是
        # 「名字」缺席，fail-closed 判 incomplete。
        before = re.findall(r"[A-Za-z_]\w*", part[:name_m.start(1)])
        if not before or all(tok in _C_QUALIFIERS for tok in before):
            return dict(_INCOMPLETE)
        params.append(name)
    return _finish(params)


def parse_signature(text):
    """在 text 里找恰一个签名并解析，返回 {"status": ..., "params": [...]}."""
    if not isinstance(text, str) or not text.strip():
        return dict(_INCOMPLETE)
    defs = list(_DEF_RE.finditer(text))
    def_spans = [m.span() for m in defs]
    c_protos = [
        m for m in _C_RE.finditer(text)
        if not any(m.start() < e and s < m.end() for s, e in def_spans)
    ]
    candidates = [("py", m) for m in defs] + [("c", m) for m in c_protos]
    if not candidates:
        return dict(_INCOMPLETE)
    if len(candidates) >= 2:
        return {"status": "multiple", "params": []}
    kind, m = candidates[0]
    open_idx = m.end() - 1
    close = _close_paren(text, open_idx)
    if close < 0:
        return dict(_INCOMPLETE)
    if "..." in text[m.start():close + 1] or "?" in text[m.start():close + 1]:
        return dict(_INCOMPLETE)
    rest = text[close + 1:].lstrip()
    allowed = (":", "->") if kind == "py" else (";", "{")
    if rest and not rest.startswith(allowed):
        return dict(_INCOMPLETE)
    if kind == "py":
        return _parse_python(text, m.start(), close)
    return _parse_c(text, open_idx, close)
