"""任务书自带用例集 —— 识别、接口映射 IR、规范化、materializer 规格与 golden 包装层生成。

Layer 1 确定性脚本（工具中立、可移植）：纯 stdlib（json/hashlib/ast/re），**不 import numpy/torch**。
本模块只产**数据与文本**：规范化后的 caseset、造数用的 `materialize_plan` 规格、以及一份 `golden.py`
包装层源码；真正造数（numpy）与真正跑 golden 都在下游、在容器里。

—— 为什么需要这一支 ——
有些任务书**自带**一套用例（`self_test_case/<op>/`：cases JSON + golden .py + prototype JSON）。
这套用例是任务书的一部分，因此是**验收权威**（AGENTS.md 5.8）；不用它而另起炉灶铺正交网格，
等于把任务书点名的测试点换掉。但它与本仓 spec 之间隔着一层 API：

    任务书 prototype/cases:  self / out / "float" / ksize_x + ksize_y / sigmaX / sigmaY
    本仓 spec           :  src  / dst / "float32" / ksize(数组)     / sigma_x / sigma_y / border_type

两边**符号名逐字不同、attr 结构也不同**（两个标量 vs 一个数组）。所以不能要求「跨 API 层名字相同」，
必须有一份**接口映射 IR**，按 **role + 顺序 + 数量 + canonical dtype + 组合结构**去对账。
这份 IR 是本模块最重要的产出，也是「泛化优先」在这里的落点：

  · 判据只有 io 角色、位置序、受控 dtype 词表、受控轴后缀词表、外部参考 API 的**已知签名**；
  · **没有** `if op == "<某算子>"`；换一个同形态的任务书自带用例集，工具零改即可跑；
  · 任何一条对不上（数量不等 / 名字与顺序都对不上 / 后缀不在词表）→ `identity_mismatch`，fail-closed。

—— 结局受控词表 ——
`DISCOVERY_OUTCOMES`，无 ok 兜底；`recognized` 之外全部不产 caseset。

用法::

    python3 taskdoc_caseset.py --links <taskdoc_links.json> --spec <spec.json> --out <dir>

退出码：0 recognized / 2 其它结局（未识别、字段不支持、歧义、身份不匹配）/ 1 参数或 IO 错。
"""
import argparse
import ast
import hashlib
import json
import math
import os
import posixpath
import re
import sys

import content_address

SCHEMA = "oprunway.taskdoc_caseset"
SCHEMA_VERSION = 1

# ── 结局受控词表 ────────────────────────────────────────────────────────────────
DISCOVERY_OUTCOMES = frozenset({
    "recognized",                   # 认出成套件、且语义字段全部受控
    "unsupported_case_field",       # 语义字段出现受控词表外的取值/形态
    "unsupported_input_source",     # 输入来源本轮不支持（data_path 钉死输入 / 无 value_range）
    "caseset_ambiguous",            # 多个目录同时成套件且无法按目标 op 收敛到一个
    "identity_mismatch",            # 认出了套件，但它与 spec 的接口对不上（含 op 身份不符）
    "not_a_caseset_dir",            # 探了目录，但没有一个构成成套件
    "taskdoc_caseset_not_probed",   # 根本没探到可列的目录（相对链接没 base / 目录没列成）
})

# ── canonical dtype（allowlist；认不出就是错，不静默兜底） ──────────────────────
# 左边是 AscendOpTest 用例 JSON / op prototype 里的写法，右边是本仓 spec 的 canonical 名。
# ⚠ `"float"` 在 AscendOpTest 口径里就是 float32（不是 C 的 double）——这条是本表存在的主要理由。
CANONICAL_DTYPES = {
    "float": "float32",
    "float32": "float32",
    "float16": "float16",
    "bfloat16": "bfloat16",
    "double": "float64",
    "float64": "float64",
    "int8": "int8",
    "uint8": "uint8",
    "int16": "int16",
    "uint16": "uint16",
    "int32": "int32",
    "uint32": "uint32",
    "int64": "int64",
    "uint64": "uint64",
    "bool": "bool",
}

# canonical dtype → 家族。造数分布、attr 组合类型、dtype 兼容都按家族判，不按具体位宽。
DTYPE_FAMILY = {
    "float16": "floating", "bfloat16": "floating", "float32": "floating", "float64": "floating",
    "int8": "integer", "uint8": "integer", "int16": "integer", "uint16": "integer",
    "int32": "integer", "uint32": "integer", "int64": "integer", "uint64": "integer",
    "bool": "boolean",
}

# 用例里 input/output 的 param_type：**严校**。
# 为什么它算语义字段：`dynamic`（动态输入列表）会改变 arity——按 required 处理就会把
# 「一串张量」当成「一个张量」，整个位置序映射跟着错位。认不出就停。
PARAM_TYPES = frozenset({"required", "optional"})

# 元数据字段的 format 白名单：**只告警不阻断**（它不参与本模块的任何映射判断）。
FORMAT_ALLOWLIST = frozenset({"ND", "NCHW", "NHWC", "NCDHW", "NDHWC"})

# attr 的声明类型 → (家族, python 类型)。allowlist。
ATTR_TYPE_FAMILY = {
    "int": "integer", "float": "floating", "bool": "boolean", "str": "string",
    "list_int": "integer_array", "list_float": "floating_array",
    "list_bool": "boolean_array", "list_str": "string_array",
}

# attr 组合形态受控词表（mapping_ir.attrs[].compose）。
COMPOSE_KINDS = frozenset({"scalar", "int_array", "float_array", "bool_array", "str_array"})
# 家族 → 数组组合形态。分成两张表是因为「取值家族」和「组合形态」是两件事：
# 前者管「这个值合不合法」，后者是写进 IR 给下游读的形态标签。
FAMILY_TO_COMPOSE = {
    "integer": "int_array", "floating": "float_array",
    "boolean": "bool_array", "string": "str_array",
}

# ── 轴后缀受控词表 ──────────────────────────────────────────────────────────────
# 任务书把一个数组 attr 拆成若干标量 attr 时，用的是「同名前缀 + 轴后缀」这套**命名约定**
# （`ksize` → `ksize_x` / `ksize_y`）。要把它们拼回数组，就得知道**哪个后缀排前面**。
# 这里给出受控的轴序表；判据是「后缀集合是某一条轴序的子集」，元素次序取该轴序里的出现次序。
#
# ⚠ 这不是算子特判，是**命名约定**的词表：任何用同一套后缀的算子都走同一条路。
# ⚠ 必须保证**唯一**匹配：若一个后缀集合同时是两条轴序的子集且得到不同次序 → 歧义 → fail-closed。
#    因此这张表刻意**不放** ("w","h",…) 这类与 ("n","c","h","w") 互相冲突的重复轴序；
#    新形态出现时**显式扩表**（并复核唯一性），而不是让工具去猜。
AXIS_SUFFIX_ORDERS = (
    ("x", "y", "z", "w"),                          # 笛卡尔轴序（OpenCV/几何惯例：x 在前）
    ("0", "1", "2", "3", "4", "5", "6", "7"),      # 纯数字下标
    ("n", "c", "h", "w"),                          # NCHW 维度序（含 h/w 两轴子集）
)

# ── 外部参考 API 的已知签名与省略默认值 ─────────────────────────────────────────
# 用途：任务书自带 golden **调了某个外部参考 API 却省略了某些形参**时，spec 里对应的那个 attr
# 就没有任何 case 提供取值。这时正确的默认值不是「spec 写了什么」，而是
# **「那个被指定的 golden 实际跑起来是什么语义」**——省略即取该 API 的文档默认值。
#
# ⚠ 这张表按**外部 API** 组织，不按算子组织：任何调 `cv2.GaussianBlur` 的任务书 golden 都共用它。
# ⚠ 认不出的 API / 认得出但省略的形参不在 `omittable_defaults` 里 → **不产默认值**（fail-closed），
#    绝不「按 0 / 按 spec 的 default 猜一个」。
# ⚠ `method_kind` 取自 `precision_policy.GOLDEN_METHOD_KIND` 受控词表（本模块不 import 它，
#    以免 Layer 1 之间产生不必要的耦合；值须与那份词表逐字一致）。
REFERENCE_APIS = {
    "cv2.GaussianBlur": {
        # OpenCV Python 绑定的位置形参序：
        #   cv2.GaussianBlur(src, ksize, sigmaX[, dst[, sigmaY[, borderType]]]) -> dst
        "positional_order": ("src", "ksize", "sigmaX", "dst", "sigmaY", "borderType"),
        "omittable_defaults": {
            "sigmaY": {"value": 0.0, "symbol": "0",
                       "note": "OpenCV 文档：sigmaY<=0 时取 sigmaX"},
            "borderType": {"value": 4, "symbol": "cv2.BORDER_DEFAULT",
                           "note": "cv2.BORDER_DEFAULT == cv2.BORDER_REFLECT_101 == 4"},
        },
        "method_kind": "opencv_cpu",
        "doc": "OpenCV 4.x cv::GaussianBlur(src, dst, ksize, sigmaX, sigmaY=0, borderType=BORDER_DEFAULT)",
    },
}

# import 根模块 → golden 的 method_kind（allowlist）。numpy 是胶水、不单独定性。
_METHOD_KIND_BY_IMPORT_ROOT = {
    "cv2": "opencv_cpu",
    "torch": "torch_cpu",
    "numpy": "numpy_cpu",
}
_GLUE_IMPORT_ROOTS = frozenset({"numpy", "math", "os", "sys", "json", "typing", "collections", "itertools"})

# ── materializer 规格 ───────────────────────────────────────────────────────────
MATERIALIZER_VERSION = 1
_SEED_DOMAIN = b"oprunway-taskdoc-case-seed-v1\0"
# 造数分布受控词表：按 canonical dtype 家族选，认不出的家族 fail-closed。
DISTRIBUTION_BY_FAMILY = {
    "floating": "uniform_float",
    "integer": "uniform_int",
    "boolean": "uniform_bool",
}

# err_threshold 的口径声明（AscendOpTest 的 `[rtol, atol]` 两元组）。
THRESHOLD_SCHEMA = {"name": "ascendoptest.err_threshold", "order": ["rtol", "atol"], "version": 1}

# ── golden 授权锚（与 precision_policy 的三层对齐） ─────────────────────────────
# 任务书**全文快照**的落盘名。授权锚（`GOLDEN_CONTRACT.authorization.cite`）按
# `task_doc.snapshot.md:<起>[-<止>]` 指行，`precision_policy.verify_authorization` 读**与 golden.py
# 同目录**的这份文件逐字比对——所以本模块产包装层时必须把它一并落在旁边，
# 否则包装层被搬到 `<ops_root>/<op>/` 后授权恒核不过，档位掉到 tier 4 blocked。
# ⚠ 下面三个常量是 `precision_policy.TASKDOC_SNAPSHOT_NAME` / `AUTHORIZATION_KIND` 的**本地镜像**：
#   本模块刻意不 import 那支（同 REFERENCE_APIS 的 method_kind 那条注释：避免 Layer 1 之间
#   产生不必要的耦合），代价是对齐靠人维护——故另有一条测试逐项盯着两边不许漂
#   （`test_taskdoc_caseset_golden_anchor.py::test_local_mirrors_match_precision_policy`）。
TASKDOC_SNAPSHOT_NAME = "task_doc.snapshot.md"
AUTHORIZATION_KINDS = ("oracle_method", "formula", "impl_reference", "none")
# 声称「任务书就真值口径/公式作了指定」的两档 → 必须留下可机核的锚（cite + quote + 快照文件）。
# 另两档（impl_reference / none）本就不构成授权，`verify_authorization` 直接放行、无需锚。
ANCHORED_AUTHORIZATION_KINDS = ("oracle_method", "formula")

_EXPECT_FUNC_RE = re.compile(r"^(?P<file>[A-Za-z_][A-Za-z0-9_]*\.py):(?P<func>[A-Za-z_][A-Za-z0-9_]*)$")


class CasesetError(ValueError):
    """带受控 `outcome` 的失败；`outcome=None` 表示参数/IO 层面的硬错（CLI 退出码 1）。"""

    def __init__(self, message, outcome=None):
        super().__init__(message)
        if outcome is not None and outcome not in DISCOVERY_OUTCOMES:
            raise ValueError("非受控 outcome: " + repr(outcome))
        self.outcome = outcome


def _fail(outcome, message):
    raise CasesetError(message, outcome=outcome)


def _norm_ident(name):
    """跨 API 层的标识符规范化：只抹**分隔符与大小写**，不抹任何字符内容。

    `sigmaX`(任务书) 与 `sigma_x`(spec) 是同一个东西，`ksize_x` 与 `ksizeX` 也是；
    但 `ksize` 与 `ksize_x` 规范化后仍不同——这正是数组组合那一步要处理的差异。
    """
    return re.sub(r"[_\-\s]", "", str(name)).lower()


def _json_loads_strict(text, where):
    """严格 JSON 解析：**显式拒 NaN / Infinity / -Infinity**。

    Python 的 `json` 默认接受这三个**非标准**字面量并给出 float('nan') 等值；
    它们一旦混进阈值或 value_range，后面所有比较都会静默变成 False（fail-open 的经典入口）。
    """
    def _reject(token):
        raise CasesetError(where + ": JSON 含非标准常量 " + str(token) + "（NaN/Infinity 一律拒绝）",
                           outcome="unsupported_case_field")
    try:
        return json.loads(text, parse_constant=_reject)
    except json.JSONDecodeError as ex:
        raise CasesetError(where + ": JSON 解析失败: " + str(ex), outcome="unsupported_case_field") from ex


def _finite_number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value)))


# ================================================================================
# 一 · 识别任务书自带用例集
# ================================================================================
def _read_local(work_dir, entry, where):
    """按 links 产物里的 local_path 读回文件，并**复核 sha256**（漂了就停）。"""
    rel = entry.get("local_path")
    if not rel:
        _fail("not_a_caseset_dir", where + ": 条目没有 local_path")
    path = content_address.safe_path(work_dir, rel.replace("/", os.sep))
    try:
        with open(path, "rb") as src:
            raw = src.read()
    except OSError as ex:
        raise CasesetError(where + ": 读取取材文件失败 " + repr(path) + ": " + str(ex)) from ex
    digest = hashlib.sha256(raw).hexdigest()
    if entry.get("sha256") and digest != entry["sha256"]:
        raise CasesetError(
            where + ": 取材文件内容与 taskdoc_links.json 记录的 sha256 不一致（recorded="
            + str(entry["sha256"]) + ", actual=" + digest + "）——取材目录被改过，拒绝继续。")
    return raw, digest


def _dir_children(links, dir_entry):
    """某个 `listed` 目录下**直接**的 `fetched` 子文件（按仓内坐标匹配，不靠命名约定）。"""
    resolved = dir_entry.get("resolved") or {}
    out = {}
    for link in links:
        if link.get("status") != "fetched":
            continue
        child = link.get("resolved") or {}
        if (child.get("owner"), child.get("repo"), child.get("ref")) != \
                (resolved.get("owner"), resolved.get("repo"), resolved.get("ref")):
            continue
        path = child.get("path") or ""
        if posixpath.dirname(path) != (resolved.get("path") or ""):
            continue
        out[posixpath.basename(path)] = link
    return out


def _looks_like_caseset_array(value):
    """识别条 ①：顶层数组，每项含 case_name / op_name / input_desc / output_desc。

    ⚠ 只判**形态**，不判内容对错——内容严校在第二阶段做，好让「这确实是一份用例集、
    只是有字段不支持」与「这压根不是用例集」两件事在结局词表上分得开。
    """
    if not isinstance(value, list) or not value:
        return False
    for item in value:
        if not isinstance(item, dict):
            return False
        for key in ("case_name", "op_name", "input_desc", "output_desc"):
            if key not in item:
                return False
    return True


def _looks_like_prototype_array(value):
    """算子 prototype：顶层数组，每项含 op + input_desc + output_desc，且**没有** case_name。"""
    if not isinstance(value, list) or not value:
        return False
    for item in value:
        if not isinstance(item, dict) or "case_name" in item:
            return False
        if "op" not in item or "input_desc" not in item or "output_desc" not in item:
            return False
    return True


def _ast_has_function(source, func_name, where):
    """用 `ast` 证明模块顶层确有 `def <func_name>`；**绝不 import 执行**。

    为什么必须 AST 而不是 import：这份 .py 来自任务书仓、是外部代码。
    「证明函数存在」这件事不值得付出「执行任意代码」的代价——识别阶段一律静态判定。
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as ex:
        _fail("unsupported_case_field", where + ": golden .py 语法错误: " + str(ex))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return tree, node
    return tree, None


def _parse_expect_func(raw, where):
    """`expect_func` 形如 `<file>.py:<func>`；**只允许同目录 basename**。

    拒 `..`、绝对路径、任何路径分隔符——否则一条任务书就能把读取范围引到取材目录之外。
    """
    if not isinstance(raw, str) or not raw.strip():
        _fail("unsupported_case_field", where + ": expect_func 缺失或非字符串")
    text = raw.strip()
    if "/" in text or "\\" in text or ".." in text or text.startswith("."):
        _fail("unsupported_case_field",
              where + ": expect_func 只允许同目录 basename（拒路径分隔符/../绝对路径），得 " + repr(text))
    m = _EXPECT_FUNC_RE.match(text)
    if not m:
        _fail("unsupported_case_field",
              where + ": expect_func 须形如 `<file>.py:<func>`，得 " + repr(text))
    return m.group("file"), m.group("func")


def _check_shape(raw, where):
    if not isinstance(raw, list):
        _fail("unsupported_case_field", where + ": shape 须为数组，得 " + repr(raw)[:80])
    dims = []
    for dim in raw:
        if isinstance(dim, bool) or not isinstance(dim, int) or dim < 0:
            _fail("unsupported_case_field",
                  where + ": shape 维度须为非负整数（拒 bool / 负数 / 动态 -1），得 " + repr(raw)[:80])
        dims.append(int(dim))
    return dims


def _check_dtype(raw, where):
    if raw not in CANONICAL_DTYPES:
        _fail("unsupported_case_field",
              where + ": data_type=" + repr(raw) + " 不在受控 dtype 词表 "
              + repr(sorted(CANONICAL_DTYPES)) + "（认不出不猜）")
    return CANONICAL_DTYPES[raw]


def _check_param_type(raw, where):
    if raw not in PARAM_TYPES:
        _fail("unsupported_case_field",
              where + ": param_type=" + repr(raw) + " 不在受控词表 " + repr(sorted(PARAM_TYPES))
              + "（`dynamic` 等会改变 arity，认不出即停）")
    return raw


def _check_value_range(raw, where):
    if raw is None or raw == []:
        _fail("unsupported_input_source",
              where + ": 输入既没有 data_path 也没有 value_range → 无法确定输入来源（不现造随机值）")
    if not isinstance(raw, list) or len(raw) != 2 or not all(_finite_number(v) for v in raw):
        _fail("unsupported_case_field",
              where + ": value_range 须为 2 个有限数，得 " + repr(raw)[:80])
    low, high = float(raw[0]), float(raw[1])
    if low > high:
        _fail("unsupported_case_field", where + ": value_range 下界大于上界: " + repr(raw))
    return [low, high]


def _check_err_threshold(raw, where):
    if not isinstance(raw, list) or len(raw) != 2:
        _fail("unsupported_case_field",
              where + ": err_threshold 须为长度 2 的数组（" + repr(THRESHOLD_SCHEMA["order"]) + "），得 " + repr(raw)[:80])
    vals = []
    for item in raw:
        if not _finite_number(item):
            _fail("unsupported_case_field",
                  where + ": err_threshold 须为有限数（显式拒 NaN/Inf），得 " + repr(raw)[:80])
        val = float(item)
        if val < 0:
            _fail("unsupported_case_field", where + ": err_threshold 须非负，得 " + repr(raw)[:80])
        vals.append(val)
    return vals


def _check_data_path(raw, where):
    """`data_path` 非空 → `unsupported_input_source`：**钉死输入本轮不支持**。

    这条必须硬拒。data_path 指的是「用这份预先落盘的数据跑」；本轮没有取这些数据的通路。
    若静默改成「按 value_range 现造随机值」，跑的就不是任务书那套用例了，
    而报告仍会声称「已按任务书自带用例验收」= 编造证据（AGENTS.md 5.8）。
    """
    if raw in (None, ""):
        return ""
    _fail("unsupported_input_source",
          where + ": data_path=" + repr(raw) + " 非空（钉死输入本轮不支持；不静默换成现造随机值）")


def _check_attr_value(value, family, where):
    """attr 取值须与它自己声明的 type 家族相符（bool 不当 int 用）。"""
    if family == "integer":
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif family == "floating":
        ok = _finite_number(value)
    elif family == "boolean":
        ok = isinstance(value, bool)
    elif family == "string":
        ok = isinstance(value, str)
    elif family.endswith("_array"):
        elem = family[:-len("_array")]
        ok = isinstance(value, list) and all(
            _attr_scalar_ok(v, elem) for v in value)
    else:
        ok = False
    if not ok:
        _fail("unsupported_case_field",
              where + ": attr 取值 " + repr(value)[:80] + " 与声明家族 " + family + " 不符")
    return value


def _attr_scalar_ok(value, family):
    if family == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if family == "floating":
        return _finite_number(value)
    if family == "boolean":
        return isinstance(value, bool)
    if family == "string":
        return isinstance(value, str)
    return False


def _validate_cases(raw_cases, where, warnings):
    """识别条 ③：语义字段严校 + 元数据保留告警。返回规范化后的中性用例结构。

    **严校范围**：`data_path` / `data_type` / `shape` / `param_type` / `attr` / `err_threshold`。
    **只告警不阻断**：`case_path` / `golden_path` / `format` —— 它们是元数据，不参与本模块任何判定；
    为它们停下会把一份好用的用例集挡在门外，而它们出错也不会让判定悄悄变松。
    """
    cases = []
    sig = None
    for idx, raw in enumerate(raw_cases):
        tag = where + " case[" + str(idx) + "]"
        name = raw.get("case_name")
        if not isinstance(name, str) or not name.strip():
            _fail("unsupported_case_field", tag + ": case_name 须为非空字符串")
        op_name = raw.get("op_name")
        if not isinstance(op_name, str) or not op_name.strip():
            _fail("unsupported_case_field", tag + ": op_name 须为非空字符串")
        for meta_key in ("case_path", "golden_path"):
            if raw.get(meta_key):
                warnings.append(tag + ": 元数据 " + meta_key + "=" + repr(raw[meta_key])
                                + " 非空，本模块不消费（保留、不阻断）")

        inputs = []
        for j, item in enumerate(raw.get("input_desc") or []):
            itag = tag + ".input_desc[" + str(j) + "]"
            if not isinstance(item, dict):
                _fail("unsupported_case_field", itag + ": 须为对象")
            _check_data_path(item.get("data_path"), itag)
            if item.get("format") not in FORMAT_ALLOWLIST:
                warnings.append(itag + ": format=" + repr(item.get("format")) + " 不在白名单（保留、不阻断）")
            inputs.append({
                "taskdoc_name": str(item.get("name") or ""),
                "raw_dtype": item.get("data_type"),
                "dtype": _check_dtype(item.get("data_type"), itag),
                "param_type": _check_param_type(item.get("param_type"), itag),
                "shape": _check_shape(item.get("shape"), itag),
                "value_range": _check_value_range(item.get("value_range"), itag),
                "format": item.get("format"),
            })
        if not inputs:
            _fail("unsupported_case_field", tag + ": input_desc 为空")

        outputs = []
        for j, item in enumerate(raw.get("output_desc") or []):
            otag = tag + ".output_desc[" + str(j) + "]"
            if not isinstance(item, dict):
                _fail("unsupported_case_field", otag + ": 须为对象")
            _check_data_path(item.get("data_path"), otag)
            if item.get("golden_path"):
                warnings.append(otag + ": 元数据 golden_path 非空，本模块不消费（保留、不阻断）")
            if item.get("format") not in FORMAT_ALLOWLIST:
                warnings.append(otag + ": format=" + repr(item.get("format")) + " 不在白名单（保留、不阻断）")
            outputs.append({
                "taskdoc_name": str(item.get("name") or ""),
                "raw_dtype": item.get("data_type"),
                "dtype": _check_dtype(item.get("data_type"), otag),
                "param_type": _check_param_type(item.get("param_type"), otag),
                "shape": _check_shape(item.get("shape"), otag),
                "err_threshold": _check_err_threshold(item.get("err_threshold"), otag),
                "format": item.get("format"),
            })
        if not outputs:
            _fail("unsupported_case_field", tag + ": output_desc 为空")

        attrs = []
        for j, item in enumerate(raw.get("attr_desc") or []):
            atag = tag + ".attr_desc[" + str(j) + "]"
            if not isinstance(item, dict):
                _fail("unsupported_case_field", atag + ": 须为对象")
            declared = item.get("type")
            if declared not in ATTR_TYPE_FAMILY:
                _fail("unsupported_case_field",
                      atag + ": attr type=" + repr(declared) + " 不在受控词表 "
                      + repr(sorted(ATTR_TYPE_FAMILY)))
            family = ATTR_TYPE_FAMILY[declared]
            if "value" not in item:
                _fail("unsupported_case_field", atag + ": attr 缺 value")
            attrs.append({
                "taskdoc_name": str(item.get("name") or ""),
                "declared_type": declared,
                "family": family,
                "param_type": _check_param_type(item.get("param_type"), atag),
                "value": _check_attr_value(item["value"], family, atag),
            })

        # 所有 case 的接口签名（名字序）必须一致 —— 否则一份 mapping_ir 描述不了这套用例。
        this_sig = (tuple(x["taskdoc_name"] for x in inputs),
                    tuple(x["taskdoc_name"] for x in outputs),
                    tuple(x["taskdoc_name"] for x in attrs))
        if sig is None:
            sig = this_sig
        elif this_sig != sig:
            _fail("unsupported_case_field",
                  tag + ": 该 case 的 input/output/attr 名字序与首个 case 不同（"
                  + repr(this_sig) + " != " + repr(sig) + "）——一套用例集须共用同一份接口签名")

        cases.append({"case_name": name, "op_name": op_name,
                      "inputs": inputs, "outputs": outputs, "attrs": attrs})
    return cases, sig


def _probe_dir(links, dir_entry, work_dir, warnings):
    """探一个 `listed` 目录是否构成成套件；不构成返回 None，构成但语义字段不支持则抛。"""
    children = _dir_children(links, dir_entry)
    if not children:
        return None
    where = "目录 " + str((dir_entry.get("resolved") or {}).get("path"))

    # 阶段一：形态识别（不构成成套件就静默跳过，别把别人的目录判成「坏用例集」）。
    cases_file = None
    cases_raw = None
    proto_file = None
    proto_raw = None
    for name, entry in sorted(children.items()):
        if not name.endswith(".json"):
            continue
        raw, _digest = _read_local(work_dir, entry, where + "/" + name)
        try:
            text = raw.decode("utf-8")
        except UnicodeError:
            continue        # 不是 UTF-8 文本 → 不可能是这套 JSON 用例集，静默跳过（不是错）
        value = _json_loads_strict(text, where + "/" + name)
        if _looks_like_caseset_array(value):
            if cases_file is not None:
                _fail("caseset_ambiguous",
                      where + ": 同一目录里有多份用例集 JSON（" + cases_file + " / " + name + "）")
            cases_file, cases_raw = name, value
        elif _looks_like_prototype_array(value):
            if proto_file is None:      # 多份 prototype 时只取排序第一份并告警（它不参与判定，只作交叉核对）
                proto_file, proto_raw = name, value
            else:
                warnings.append(where + ": 目录里有多份 prototype JSON，只用 " + proto_file)
    if cases_file is None:
        return None

    expect_raw = cases_raw[0].get("expect_func")
    golden_name, golden_func = _parse_expect_func(expect_raw, where)
    for other in cases_raw:
        if other.get("expect_func") != expect_raw:
            _fail("unsupported_case_field",
                  where + ": 同一套用例集里 expect_func 不一致（" + repr(expect_raw)
                  + " vs " + repr(other.get("expect_func")) + "）")
    if golden_name not in children:
        return None                      # expect_func 指的文件不在同目录 → 这不是一套自洽的用例集
    golden_bytes, golden_sha = _read_local(work_dir, children[golden_name], where + "/" + golden_name)
    try:
        golden_text = golden_bytes.decode("utf-8")
    except UnicodeError as ex:
        _fail("unsupported_case_field", where + "/" + golden_name + ": golden .py 不是 UTF-8: " + str(ex))
    tree, func_node = _ast_has_function(golden_text, golden_func, where + "/" + golden_name)
    if func_node is None:
        return None                      # AST 证不出该函数 → 不认（不 import、不猜）

    # 阶段二：语义字段严校（到这里已确认它**是**一套用例集，出错就得给准确结局）。
    cases, _sig = _validate_cases(cases_raw, where, warnings)

    source_files = {}
    for name, entry in sorted(children.items()):
        source_files[name] = entry.get("sha256")
    return {
        "source_dir": (dir_entry.get("resolved") or {}).get("path"),
        "source_dir_link_line": dir_entry.get("line"),
        "source_files": source_files,
        "cases_file": cases_file,
        "prototype_file": proto_file,
        "prototype": proto_raw,
        "expect_func": expect_raw,
        "golden": {"filename": golden_name, "sha256": golden_sha,
                   "text": golden_text, "function": golden_func, "ast": tree},
        "cases": cases,
        "op_name": cases[0]["op_name"],
    }


def discover(links_json, work_dir, target_op=None):
    """在 links 产物里找任务书自带用例集。

    识别三条（**缺一不认**）：
      ① 顶层数组 JSON，每项含 case_name / op_name / input_desc / output_desc；
      ② `expect_func` 形如 `<file>.py:<func>`，同目录真有该文件，且 **AST 可证**该函数存在
         （静态判定，绝不 import 执行外部代码）；
      ③ 语义字段全部落在受控词表内。

    返回 ``{"outcome": <受控词表>, ...}``；`recognized` 时带上 cases / golden / prototype 等。
    """
    if not isinstance(links_json, dict) or links_json.get("schema") != "oprunway.taskdoc_links":
        raise CasesetError("links 产物 schema 不是 oprunway.taskdoc_links")
    links = links_json.get("links") or []
    dirs = [l for l in links
            if l.get("kind") in ("gitcode_tree", "gitcode_relative") and l.get("status") == "listed"]
    if not dirs:
        return {"outcome": "taskdoc_caseset_not_probed",
                "reason": "taskdoc_links.json 里没有 status=listed 的目录条目（相对链接无 base / 目录未列成）",
                "warnings": []}

    warnings = []
    candidates = []
    for dir_entry in dirs:
        try:
            found = _probe_dir(links, dir_entry, work_dir, warnings)
        except CasesetError as ex:
            if ex.outcome is None:
                raise
            return {"outcome": ex.outcome, "reason": str(ex), "warnings": warnings}
        if found:
            candidates.append(found)
    if not candidates:
        return {"outcome": "not_a_caseset_dir",
                "reason": "探了 " + str(len(dirs)) + " 个目录，没有一个同时满足三条识别条件",
                "warnings": warnings}

    # 多目录同时成套件 → 先按 op_name 建 per-op 候选，再按目标 op 收敛。
    by_op = {}
    for cand in candidates:
        by_op.setdefault(_norm_ident(cand["op_name"]), []).append(cand)
    if target_op:
        key = _norm_ident(target_op)
        if key not in by_op:
            return {"outcome": "identity_mismatch",
                    "reason": "目标算子 " + str(target_op) + " 与探到的用例集 op_name "
                              + repr(sorted({c["op_name"] for c in candidates})) + " 都对不上",
                    "warnings": warnings}
        picked = by_op[key]
    elif len(by_op) == 1:
        picked = list(by_op.values())[0]
    else:
        return {"outcome": "caseset_ambiguous",
                "reason": "探到多个 op 的用例集 " + repr(sorted(by_op)) + "，且未给目标 op",
                "warnings": warnings}
    if len(picked) != 1:
        return {"outcome": "caseset_ambiguous",
                "reason": "同一个 op 有多个用例集目录 " + repr([c["source_dir"] for c in picked]),
                "warnings": warnings}

    result = dict(picked[0])
    result["outcome"] = "recognized"
    result["warnings"] = warnings
    return result


# ================================================================================
# 二 · 接口映射 IR
# ================================================================================
def _spec_params(spec):
    params = spec.get("params")
    if not isinstance(params, list) or not params:
        raise CasesetError("spec.params 缺失或不是数组")
    buckets = {"in": [], "out": [], "attr": []}
    for idx, item in enumerate(params):
        if not isinstance(item, dict):
            raise CasesetError("spec.params[" + str(idx) + "] 须为对象")
        io = item.get("io")
        if io not in buckets:
            raise CasesetError("spec.params[" + str(idx) + "].io=" + repr(io)
                               + " 不在受控词表 ['in','out','attr']")
        buckets[io].append(item)
    return buckets


def _spec_dtypes(param):
    raw = param.get("dtype")
    if isinstance(raw, str):
        raw = [raw]
    return [d for d in (raw or []) if isinstance(d, str)]


def _resolve_axis_order(remainders, where):
    """把一组轴后缀排成确定的次序；歧义或认不出一律 fail-closed。"""
    if len(set(remainders)) != len(remainders):
        _fail("identity_mismatch", where + ": 轴后缀重复 " + repr(remainders))
    orders = set()
    for table in AXIS_SUFFIX_ORDERS:
        if set(remainders) <= set(table):
            orders.add(tuple(s for s in table if s in remainders))
    if not orders:
        _fail("identity_mismatch",
              where + ": 轴后缀 " + repr(sorted(remainders)) + " 不在受控轴序表 "
              + repr(AXIS_SUFFIX_ORDERS) + " 内 —— 新命名约定须显式扩表，工具不猜次序")
    if len(orders) > 1:
        _fail("identity_mismatch",
              where + ": 轴后缀 " + repr(sorted(remainders)) + " 同时匹配多条轴序、次序不唯一 " + repr(sorted(orders)))
    return list(orders)[0]


def _import_alias_map(tree):
    """模块级 import 别名 → 全名；用于把 AST 里的 `cv.GaussianBlur` 解回 `cv2.GaussianBlur`。"""
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                aliases[alias.asname or alias.name] = node.module + "." + alias.name
    return aliases


def _import_roots(tree):
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def _dotted_name(node, aliases):
    """把 `ast.Call.func` 解析成全名字符串；解不出返回 None。"""
    parts = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    else:
        return None
    parts.reverse()
    head = aliases.get(parts[0], parts[0])
    return ".".join([head] + parts[1:])


def _reference_api_calls(tree, func_node):
    """在 golden 函数体里找**已知外部参考 API** 的调用 → [(api_name, supplied_params)]。"""
    aliases = _import_alias_map(tree)
    found = []
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted_name(node.func, aliases)
        if name not in REFERENCE_APIS:
            continue
        order = REFERENCE_APIS[name]["positional_order"]
        supplied = set(order[:len(node.args)])
        for kw in node.keywords:
            if kw.arg:
                supplied.add(kw.arg)
        found.append((name, supplied))
    return found


def _derive_golden_method_kind(tree, api_calls):
    """据 golden 的 import 与参考 API 调用派生 `(source, method_kind)`（受控词表，fail-closed）。"""
    roots = _import_roots(tree)
    kinds = {_METHOD_KIND_BY_IMPORT_ROOT[r] for r in roots if r in _METHOD_KIND_BY_IMPORT_ROOT}
    unknown = sorted(r for r in roots
                     if r not in _METHOD_KIND_BY_IMPORT_ROOT and r not in _GLUE_IMPORT_ROOTS)
    non_glue = sorted(kinds - {"numpy_cpu"})
    if unknown:
        method_kind = "other_external"   # 认不出的第三方依赖 → 不声称本环境跑得起来（下游据此判 blocked）
    elif len(non_glue) == 1:
        method_kind = non_glue[0]
    elif not non_glue and "numpy_cpu" in kinds:
        method_kind = "numpy_cpu"
    else:
        method_kind = "other_external"
    source = "single_api" if len(api_calls) == 1 else "multistep"
    return source, method_kind, unknown


def build_mapping_ir(prototype, cases, spec, golden=None):
    """按 **role + 顺序 + 数量 + canonical dtype + 组合结构** 对账，产接口映射 IR。

    为什么不能按名字逐字对：任务书的 prototype/cases 用的是**算子原型层**的符号
    （`self` / `out` / `"float"` / `ksize_x`+`ksize_y`），本仓 spec 用的是 **aclnn/op_def 层**的符号
    （`src` / `dst` / `"float32"` / `ksize` 数组）。两层本来就不同名，要求逐字相同等于把工具锁死在
    某一种命名习惯上。

    对账规则（全部是通用机制，无算子分支）：
      · inputs / outputs：**数量必须相等**，按**位置序**配对，canonical dtype 须落在 spec 声明的
        dtype 集合内（spec 写 `<from_input>` 的输出跟随输入 0）；
      · attrs：先做**规范化名精确匹配**（`sigmaX` ↔ `sigma_x`），
        再做**前缀 + 受控轴后缀的数组组合**（`ksize_x`,`ksize_y` → `ksize[0]`,`ksize[1]`），
        组合次序由 `AXIS_SUFFIX_ORDERS` 判定，认不出即停；
      · spec 有、任务书 case 没有的 attr：从**被指定 golden 的可执行语义**派生默认值——
        golden 调外部参考 API 时**省略**了哪个形参，就取那个 API 的文档默认值，
        `source="golden_call_omitted_param"` 并**绑 golden 的 sha256**；
      · 任务书 case 有、spec 没有的 attr → `identity_mismatch`（DUT 接口吃不下它，不静默丢弃）；
      · golden 函数的**位置形参**逐个绑到 input / attr（含数组元素下标），供包装层生成用；
        有一个绑不上就停（不按位置硬塞）。

    `prototype` 可为 None；给了就与 cases 交叉核对名字序（两源不一致 → `identity_mismatch`）。
    `golden` 形如 `{"filename","sha256","text","function","ast"}`；缺席时若存在「spec 有而 case 没有」
    的 attr → `identity_mismatch`（没有可信来源就不产默认值）。
    """
    if not cases:
        _fail("identity_mismatch", "用例集为空，无法建映射")
    buckets = _spec_params(spec)
    first = cases[0]

    # ── inputs / outputs：数量 + 位置序 + canonical dtype ──
    ir_inputs = _map_io(first["inputs"], buckets["in"], "in", None)
    input_dtypes = [x["dtype"] for x in first["inputs"]]
    ir_outputs = _map_io(first["outputs"], buckets["out"], "out", input_dtypes)

    # ── prototype 交叉核对（有就核，不作唯一依据） ──
    proto_note = None
    if prototype:
        proto_note = _cross_check_prototype(prototype, first, spec)

    # ── attrs ──
    td_attrs = list(first["attrs"])
    used = set()
    ir_attrs = []
    spec_attrs = list(buckets["attr"])
    by_norm = {}
    for idx, attr in enumerate(td_attrs):
        by_norm.setdefault(_norm_ident(attr["taskdoc_name"]), []).append(idx)

    # pass 1：规范化名精确匹配（`sigmaX` ↔ `sigma_x`）
    unbound = []
    for spec_attr in spec_attrs:
        key = _norm_ident(spec_attr.get("name"))
        hits = [i for i in by_norm.get(key, []) if i not in used]
        if len(hits) == 1:
            used.add(hits[0])
            ir_attrs.append({
                "taskdoc_names": [td_attrs[hits[0]]["taskdoc_name"]],
                "spec_name": spec_attr["name"],
                "compose": "scalar",
                "index": [hits[0]],
                "family": td_attrs[hits[0]]["family"],
            })
        elif len(hits) > 1:
            _fail("identity_mismatch",
                  "spec attr " + repr(spec_attr.get("name")) + " 规范化后同时匹配多个任务书 attr")
        else:
            unbound.append(spec_attr)

    # pass 2：前缀 + 受控轴后缀 → 数组组合。**按规范化名长度降序**处理，
    # 让更长的前缀先认领（否则 `size` 会把本该属于 `size_x` 的 attr 抢走），次序确定、可复现。
    still_unbound = []
    for spec_attr in sorted(unbound, key=lambda p: (-len(_norm_ident(p.get("name"))), str(p.get("name")))):
        prefix = _norm_ident(spec_attr.get("name"))
        cand = []
        for idx, attr in enumerate(td_attrs):
            if idx in used:
                continue
            norm = _norm_ident(attr["taskdoc_name"])
            if norm.startswith(prefix) and len(norm) > len(prefix):
                cand.append((norm[len(prefix):], idx))
        expect_len = None
        default = spec_attr.get("default")
        if isinstance(default, list):
            expect_len = len(default)
        if not cand or (len(cand) == 1 and expect_len is None):
            still_unbound.append(spec_attr)
            continue
        order = _resolve_axis_order([c[0] for c in cand],
                                   "spec attr " + repr(spec_attr.get("name")))
        if expect_len is not None and len(order) != expect_len:
            _fail("identity_mismatch",
                  "spec attr " + repr(spec_attr.get("name")) + " 的 default 长度 " + str(expect_len)
                  + " 与匹配到的任务书 attr 个数 " + str(len(order)) + " 不等")
        pos = {suffix: idx for suffix, idx in cand}
        indices = [pos[s] for s in order]
        families = {td_attrs[i]["family"] for i in indices}
        if len(families) != 1:
            _fail("identity_mismatch",
                  "spec attr " + repr(spec_attr.get("name")) + " 组合来源的 attr 家族不一致 " + repr(sorted(families)))
        family = list(families)[0]
        compose = FAMILY_TO_COMPOSE.get(family)
        if compose not in COMPOSE_KINDS:
            _fail("identity_mismatch",
                  "家族 " + repr(family) + " 没有对应的受控组合形态 " + repr(sorted(COMPOSE_KINDS)))
        used.update(indices)
        ir_attrs.append({
            "taskdoc_names": [td_attrs[i]["taskdoc_name"] for i in indices],
            "spec_name": spec_attr["name"],
            "compose": compose,
            "index": indices,
            "axis_suffixes": list(order),
            "family": family,
        })

    # pass 3：spec 有、任务书没有 → 从 golden 的可执行语义派生默认值
    defaults = []
    golden_source_kind, golden_method_kind, golden_unknown_imports = (None, None, [])
    api_calls = []
    if golden:
        api_calls = _reference_api_calls(golden["ast"], _golden_func_node(golden))
        golden_source_kind, golden_method_kind, golden_unknown_imports = _derive_golden_method_kind(
            golden["ast"], api_calls)
    for spec_attr in still_unbound:
        if not golden:
            _fail("identity_mismatch",
                  "spec attr " + repr(spec_attr.get("name")) + " 在任务书用例里没有对应项，"
                  "且没有 golden 可据以派生默认值 —— 不猜")
        entry = _derive_default_from_golden(spec_attr, api_calls, golden)
        defaults.append(entry)
        ir_attrs.append({
            "taskdoc_names": [],
            "spec_name": spec_attr["name"],
            "compose": "scalar",
            "index": [],
            "family": None,
            "from_default": True,
        })

    # pass 4：任务书有、spec 没有 → fail-closed
    leftover = [td_attrs[i]["taskdoc_name"] for i in range(len(td_attrs)) if i not in used]
    if leftover:
        _fail("identity_mismatch",
              "任务书用例里的 attr " + repr(leftover) + " 在 spec 里没有对应项 —— "
              "DUT 接口吃不下它，不静默丢弃")

    # ── golden 位置形参绑定（供包装层） ──
    golden_call = None
    if golden:
        golden_call = _bind_golden_signature(golden, first, ir_attrs)

    # dtype_map 只登记**这套用例里真的出现过**的写法 → canonical（例如 `"float"` → `"float32"`）。
    # 不把整张 `CANONICAL_DTYPES` 抄进产物：产物要能回答「这一轮做了哪些换算」，不是「工具都认识什么」。
    dtype_map = {}
    for case in cases:
        for item in list(case["inputs"]) + list(case["outputs"]):
            dtype_map[item["raw_dtype"]] = item["dtype"]

    return {
        "inputs": ir_inputs,
        "outputs": ir_outputs,
        "attrs": sorted(ir_attrs, key=lambda a: str(a["spec_name"])),
        "dtype_map": dtype_map,
        "defaults": defaults,
        "prototype_cross_check": proto_note,
        "golden_call": golden_call,
        "golden_contract_derivation": {
            "source": golden_source_kind,
            "method_kind": golden_method_kind,
            "reference_api_calls": sorted({name for name, _ in api_calls}),
            "unrecognized_imports": golden_unknown_imports,
        },
    }


def _map_io(td_items, spec_items, io, input_dtypes):
    if len(td_items) != len(spec_items):
        _fail("identity_mismatch",
              "io=" + io + " 数量不等：任务书 " + str(len(td_items)) + " 个 vs spec " + str(len(spec_items)) + " 个")
    out = []
    for idx, (td, sp) in enumerate(zip(td_items, spec_items)):
        allowed = _spec_dtypes(sp)
        if "<from_input>" in allowed:
            expect = input_dtypes[0] if input_dtypes else None
            if expect is not None and td["dtype"] != expect:
                _fail("identity_mismatch",
                      "io=" + io + "[" + str(idx) + "] spec 声明 <from_input>（跟随输入 0 的 "
                      + str(expect) + "），但用例给的是 " + td["dtype"])
        elif allowed and td["dtype"] not in allowed:
            _fail("identity_mismatch",
                  "io=" + io + "[" + str(idx) + "] 用例 dtype " + td["dtype"]
                  + " 不在 spec 声明的 " + repr(allowed) + " 内")
        out.append({"taskdoc_name": td["taskdoc_name"], "spec_name": sp.get("name"), "index": idx})
    return out


def _cross_check_prototype(prototype, first_case, spec):
    """prototype ↔ cases 交叉核对：名字序、数量。两源不一致就停（哪个对不该由工具猜）。"""
    if not isinstance(prototype, list) or not prototype:
        _fail("identity_mismatch", "prototype 形态不对（须为非空顶层数组）")
    entry = prototype[0]
    proto_in = [str(x.get("name")) for x in (entry.get("input_desc") or [])]
    proto_out = [str(x.get("name")) for x in (entry.get("output_desc") or [])]
    proto_attr = [str(x.get("name")) for x in (entry.get("attr") or [])]
    case_in = [x["taskdoc_name"] for x in first_case["inputs"]]
    case_out = [x["taskdoc_name"] for x in first_case["outputs"]]
    case_attr = [x["taskdoc_name"] for x in first_case["attrs"]]
    for label, a, b in (("input_desc", proto_in, case_in),
                        ("output_desc", proto_out, case_out),
                        ("attr", proto_attr, case_attr)):
        if a != b:
            _fail("identity_mismatch",
                  "prototype 与 cases 的 " + label + " 名字序不一致：" + repr(a) + " vs " + repr(b))
    op_in_proto = entry.get("op")
    note = {"prototype_op": op_in_proto, "matches_cases": True}
    if op_in_proto and _norm_ident(op_in_proto) != _norm_ident(first_case["op_name"]):
        _fail("identity_mismatch",
              "prototype.op=" + repr(op_in_proto) + " 与 cases.op_name=" + repr(first_case["op_name"]) + " 不一致")
    spec_op = spec.get("op")
    if spec_op and op_in_proto and _norm_ident(spec_op) != _norm_ident(op_in_proto):
        _fail("identity_mismatch",
              "spec.op=" + repr(spec_op) + " 与 prototype.op=" + repr(op_in_proto) + " 不一致")
    return note


def _golden_func_node(golden):
    for node in golden["ast"].body:
        if isinstance(node, ast.FunctionDef) and node.name == golden["function"]:
            return node
    _fail("identity_mismatch", "golden 里找不到函数 " + repr(golden["function"]))


def _derive_default_from_golden(spec_attr, api_calls, golden):
    """spec 有、任务书 case 没有的 attr → 取「被指定 golden 实际跑起来」的语义默认值。

    判据链条（每一环都可核）：
      golden 调了 `<api>` → `<api>` 的位置/关键字实参里**没有** `<param>` → 该 param 取 `<api>` 的文档默认值。
    因此这个默认值不是我们编的，而是**任务书指定的那份 golden 的可执行语义**；
    产物里记 `source="golden_call_omitted_param"` 并绑 golden 的 sha256，供逐字复核。
    """
    want = _norm_ident(spec_attr.get("name"))
    hits = []
    for api_name, supplied in api_calls:
        table = REFERENCE_APIS[api_name]
        for param, meta in table["omittable_defaults"].items():
            if param in supplied:
                continue
            if _norm_ident(param) == want:
                hits.append((api_name, param, meta))
    if not hits:
        _fail("identity_mismatch",
              "spec attr " + repr(spec_attr.get("name")) + " 在任务书用例里没有取值，"
              "也无法从 golden 的外部参考 API 省略形参派生默认值（已知 API: "
              + repr(sorted({n for n, _ in api_calls})) + "）—— 不猜默认值")
    if len({(p, repr(m["value"])) for _, p, m in hits}) > 1:
        _fail("identity_mismatch",
              "spec attr " + repr(spec_attr.get("name")) + " 能从多个外部 API 派生出不同默认值 " + repr(hits))
    api_name, param, meta = hits[0]
    entry = {
        "spec_name": spec_attr["name"],
        "value": meta["value"],
        "source": "golden_call_omitted_param",
        "reference_api": api_name,
        "omitted_param": param,
        "symbol": meta["symbol"],
        "note": meta["note"],
        "golden_filename": golden["filename"],
        "golden_sha256": golden["sha256"],
    }
    # spec 自己也写了 default 时**如实记冲突**：以 golden 语义为准（它才是本轮真值口径），
    # 但绝不把分歧藏起来 —— 这一条会原样进 caseset，供报告挂账。
    if "default" in spec_attr:
        entry["spec_default"] = spec_attr["default"]
        entry["conflicts_with_spec_default"] = (spec_attr["default"] != meta["value"])
    return entry


def _bind_golden_signature(golden, first_case, ir_attrs):
    """把 golden 的**位置形参**逐个绑到 input / attr（含数组元素下标）。

    绑不上就停：位置形参是 golden 唯一的调用面，一个绑不上就意味着我们并不真的知道该怎么调它，
    「按顺序硬塞」在这里等价于编造语义。
    """
    node = _golden_func_node(golden)
    if node.args.vararg or node.args.kwarg or node.args.kwonlyargs:
        _fail("identity_mismatch",
              "golden 函数 " + golden["function"] + " 含 *args/**kwargs/keyword-only 形参，本模块不支持")
    params = [a.arg for a in (list(node.args.posonlyargs) + list(node.args.args))]
    in_by_norm = {_norm_ident(x["taskdoc_name"]): i for i, x in enumerate(first_case["inputs"])}
    attr_slot = {}
    for attr in ir_attrs:
        for pos, td_name in enumerate(attr["taskdoc_names"]):
            attr_slot[_norm_ident(td_name)] = (attr, pos)
    positional = []
    for param in params:
        key = _norm_ident(param)
        if key in in_by_norm:
            positional.append({"param": param, "kind": "input", "index": in_by_norm[key]})
            continue
        if key in attr_slot:
            attr, pos = attr_slot[key]
            slot = {"param": param, "kind": "attr", "spec_name": attr["spec_name"],
                    "compose": attr["compose"]}
            if attr["compose"] != "scalar":
                slot["element_index"] = pos
            positional.append(slot)
            continue
        _fail("identity_mismatch",
              "golden 形参 " + repr(param) + " 绑不到任何任务书 input/attr —— 不按位置硬塞")
    return {"module_filename": golden["filename"], "function": golden["function"],
            "sha256": golden["sha256"], "positional": positional,
            "returns": "list_first_element"}


# ================================================================================
# 三 · 规范化 + materializer 规格
# ================================================================================
def case_seed(taskdoc_sha256, case_content_sha256):
    """确定性派生造数种子：``sha256(域前缀 ‖ 任务书摘要 ‖ 用例摘要)`` 的前 4 字节。

    绑任务书摘要是有意的：**换一版任务书 = 换一批随机数据**，避免「任务书改了但数据没变」
    这种看不见的漂移；绑用例摘要则保证同一份任务书里每条用例的数据互不相同且可复现。
    取值落在 ``[0, 2**32)``，正好是 numpy 合法 seed 域（下游 lane 直接喂 `default_rng` / `RandomState`）。
    """
    for label, val in (("taskdoc_sha256", taskdoc_sha256), ("case_content_sha256", case_content_sha256)):
        if not (isinstance(val, str) and len(val) == 64 and all(c in "0123456789abcdef" for c in val)):
            raise CasesetError("case_seed 的 " + label + " 须为小写 sha256 十六进制串，得 " + repr(val))
    digest = hashlib.sha256(_SEED_DOMAIN + taskdoc_sha256.encode("ascii")
                            + b"\0" + case_content_sha256.encode("ascii")).digest()
    return int.from_bytes(digest[:4], "big")


def materialize_plan(case):
    """产**造数规格**（纯数据，不造数）：dtype / shape / value_range / 分布 / seed / 生成器版本。

    本模块不 import numpy（Layer 1 纪律）；真正造数在下游 `gen_cases` 里做。
    这个规格是两边的契约：只要 `generator_version` 与 `seed` 不变，产出的数据就该逐位相同。
    """
    if not isinstance(case, dict) or "materialize_seed" not in case:
        raise CasesetError("materialize_plan 需要带 materialize_seed 的规范化 case")
    inputs = []
    for item in case["inputs"]:
        family = DTYPE_FAMILY.get(item["dtype"])
        dist = DISTRIBUTION_BY_FAMILY.get(family)
        if dist is None:
            raise CasesetError("dtype " + str(item["dtype"]) + " 的家族 " + str(family)
                               + " 没有受控造数分布 —— fail-closed，不猜")
        low, high = item["value_range"]
        plan = {"spec_name": item["spec_name"], "shape": list(item["shape"]),
                "dtype": item["dtype"], "distribution": dist}
        if dist == "uniform_int":
            if float(low) != int(low) or float(high) != int(high):
                raise CasesetError("整型输入 " + str(item["spec_name"]) + " 的 value_range "
                                   + repr(item["value_range"]) + " 不是整数边界 —— 不四舍五入")
            plan["low"] = int(low)
            plan["high_inclusive"] = int(high)
        elif dist == "uniform_bool":
            plan["low"] = 0
            plan["high_inclusive"] = 1
        else:
            plan["low"] = float(low)
            plan["high"] = float(high)
        inputs.append(plan)
    return {
        "generator_version": MATERIALIZER_VERSION,
        "seed": case["materialize_seed"],
        "seed_derivation": "sha256('oprunway-taskdoc-case-seed-v1\\0' ‖ taskdoc_sha256 ‖ '\\0' ‖ case content_sha256)[:4]",
        "inputs": inputs,
    }


def _compose_attr_value(ir_attr, td_attrs_by_name, defaults_by_name):
    if ir_attr.get("from_default"):
        return defaults_by_name[ir_attr["spec_name"]]["value"]
    values = [td_attrs_by_name[name]["value"] for name in ir_attr["taskdoc_names"]]
    if ir_attr["compose"] == "scalar":
        return values[0]
    return list(values)


def normalize(discovery, mapping_ir, spec, taskdoc_sha256):
    """把识别到的原始用例规范化成 `oprunway.taskdoc_caseset` 产物（不落盘，返回 dict）。

    `case_id` 由**语义内容**（inputs / attrs / outputs，**不含 case_name**）的摘要派生：
    这样两条内容完全相同、只是名字不同的用例会被认出来并**拒绝**——名字不构成区分度，
    留着它们只会让「覆盖了 N 条」这个数字虚高。
    """
    if discovery.get("outcome") != "recognized":
        raise CasesetError("normalize 只接受 outcome=recognized 的 discovery")
    defaults_by_name = {d["spec_name"]: d for d in mapping_ir["defaults"]}
    in_names = [x["spec_name"] for x in mapping_ir["inputs"]]
    out_names = [x["spec_name"] for x in mapping_ir["outputs"]]

    cases = []
    seen = {}
    for case in discovery["cases"]:
        td_attrs_by_name = {a["taskdoc_name"]: a for a in case["attrs"]}
        inputs = []
        for idx, item in enumerate(case["inputs"]):
            inputs.append({"spec_name": in_names[idx], "shape": list(item["shape"]),
                           "dtype": item["dtype"], "value_range": list(item["value_range"])})
        outputs = []
        for idx, item in enumerate(case["outputs"]):
            rtol, atol = item["err_threshold"]
            outputs.append({"spec_name": out_names[idx], "shape": list(item["shape"]),
                            "dtype": item["dtype"],
                            "err_threshold": {"rtol": rtol, "atol": atol, "raw": [rtol, atol]}})
        attrs = {}
        for ir_attr in mapping_ir["attrs"]:
            attrs[ir_attr["spec_name"]] = _compose_attr_value(ir_attr, td_attrs_by_name, defaults_by_name)

        payload = {"inputs": inputs, "attrs": attrs, "outputs": outputs}
        content_sha256 = content_address.content_digest("oprunway.taskdoc_caseset.case.v1", payload)
        case_id = "td_" + content_sha256[:16]
        if case_id in seen:
            _fail("unsupported_case_field",
                  "用例内容重复：" + repr(case["case_name"]) + " 与 " + repr(seen[case_id])
                  + " 的语义内容完全相同（case_id=" + case_id + "）—— 拒重复")
        seen[case_id] = case["case_name"]
        entry = {
            "case_id": case_id,
            "source_case_name": case["case_name"],
            "inputs": inputs,
            "attrs": attrs,
            "outputs": outputs,
            "content_sha256": content_sha256,
            "materialize_seed": case_seed(taskdoc_sha256, content_sha256),
        }
        entry["materialize"] = materialize_plan(entry)
        cases.append(entry)

    golden = discovery["golden"]
    derivation = mapping_ir["golden_contract_derivation"]
    return {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "taskdoc_sha256": taskdoc_sha256,
        "op": spec.get("op"),
        "source_op_name": discovery["op_name"],
        "source_dir": discovery["source_dir"],
        "source_files": discovery["source_files"],
        "cases_file": discovery["cases_file"],
        "prototype_file": discovery["prototype_file"],
        "expect_func": discovery["expect_func"],
        "mapping_ir": {k: v for k, v in mapping_ir.items()},
        "threshold_schema": dict(THRESHOLD_SCHEMA),
        "materializer": {
            "generator_version": MATERIALIZER_VERSION,
            "distributions": sorted(set(DISTRIBUTION_BY_FAMILY.values())),
            "seed_scope": "per_case",
            "note": "本模块只产规格；真正造数（numpy）在下游 gen_cases 里做（Layer 1 不 import numpy）。",
        },
        "golden_original": {"filename": golden["filename"], "sha256": golden["sha256"],
                            "function": golden["function"], "text": golden["text"]},
        "golden_contract_derived": {"source": derivation["source"], "method_kind": derivation["method_kind"]},
        "warnings": list(discovery.get("warnings") or []),
        "cases": cases,
    }


# ================================================================================
# 四 · golden 包装层生成
# ================================================================================
_WRAPPER_TEMPLATE = '''"""OpRunway 精度 golden 包装层 —— **自动生成，请勿手改**（改了就与任务书原件脱钩）。

生成者：`plugin/acc-common/taskdoc_caseset.py`（schema {schema_version}）。
真值口径**不在本文件里**：本文件只是把本仓 `golden_fn(inputs, attrs)` 的调用面，
按 `taskdoc_caseset.json` 的 `mapping_ir` 变换成**任务书自带 golden 的位置参数**，
再逐字调用**冻结的原件**：

    {original_filename}  (sha256 {original_sha256})

原件按内容寻址只读放在同目录、**不改名、不改一个字节**；本包装层加载时复核它的 sha256，
对不上直接抛——「我们调的是不是任务书那份 golden」这件事必须机器可核，不能靠约定。

⚠ 通道轴还原（如 cv2 把 (H,W,1) 降成 (H,W)）**原件自己已经做过**；本包装层**绝不**重复处理、
   也不做任何抵消动作 —— 再 reshape 一次就等于改了任务书指定的真值语义。
⚠ 返回值：原件返回 list，本包装层取第 0 个元素（`returns={returns}`）。
"""
import hashlib
import importlib.util
import os

GOLDEN_SOURCE = {golden_source!r}
GOLDEN_PROVENANCE = {golden_provenance!r}
GOLDEN_CONTRACT = {golden_contract!r}

_ORIGINAL_FILENAME = {original_filename!r}
_ORIGINAL_SHA256 = {original_sha256!r}
_ORIGINAL_FUNCTION = {original_function!r}
# 逐字来自 taskdoc_caseset.json 的 mapping_ir.golden_call.positional。
_POSITIONAL = {positional!r}

_CACHE = []


def _load_original():
    """按内容寻址加载冻结原件：先核 sha256，再用 importlib 隔离 import（不污染 sys.path）。"""
    if _CACHE:
        return _CACHE[0]
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), _ORIGINAL_FILENAME)
    if os.path.islink(path):
        raise RuntimeError("任务书 golden 原件是符号链接，拒绝加载: " + path)
    with open(path, "rb") as src:
        raw = src.read()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != _ORIGINAL_SHA256:
        raise RuntimeError(
            "任务书 golden 原件 sha256 不匹配（recorded=" + _ORIGINAL_SHA256 + ", actual=" + actual
            + "）——原件被改过，拒绝用它产 golden。")
    spec = importlib.util.spec_from_file_location("oprunway_taskdoc_golden_original", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fn = getattr(module, _ORIGINAL_FUNCTION, None)
    if not callable(fn):
        raise RuntimeError("任务书 golden 原件里没有可调用的 " + _ORIGINAL_FUNCTION + ": " + path)
    _CACHE.append(fn)
    return fn


def golden_fn(inputs, attrs):
    """本仓调用面 → 任务书 golden 的位置参数 → 取其返回 list 的第 0 个元素。"""
    fn = _load_original()
    args = []
    for slot in _POSITIONAL:
        if slot["kind"] == "input":
            args.append(inputs[slot["index"]])
            continue
        value = attrs[slot["spec_name"]]
        if slot["compose"] != "scalar":
            value = value[slot["element_index"]]
        args.append(value)
    out = fn(*args)
    if not isinstance(out, (list, tuple)) or len(out) < 1:
        raise RuntimeError(
            "任务书 golden 应返回非空 list/tuple，得 " + type(out).__name__
            + "（本包装层按 mapping_ir 取第 0 个元素）")
    return out[0]
'''


def _read_taskdoc_snapshot(path, expected_sha256):
    """读任务书全文快照并核指纹，返回**原始字节**（不解码、不规范化）。

    ⚠ 必须逐字节：`verify_authorization` 按**行号 + 逐字子串**核引文，改一个字节行号就可能移位，
    而那时报出来的是「引文与出处对不上」——看起来像 agent 编造引文，真正的病因却查不出来
    （同 `fetch_source.write_taskdoc_snapshot` 的理由）。
    """
    if os.path.islink(path):
        raise CasesetError("任务书快照不得是符号链接（防换锚）: " + repr(path))
    if not os.path.isfile(path):
        raise CasesetError("任务书快照不存在: " + repr(path))
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as ex:
        raise CasesetError("任务书快照读取失败 " + repr(path) + ": " + str(ex)) from ex
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_sha256:
        raise CasesetError(
            "任务书快照指纹与本轮用例集不符（快照 " + actual + " ≠ taskdoc_links 记录的 "
            + str(expected_sha256) + "）——快照与用例集来自**两份不同的任务书**，"
            "引文锚会指到错误的行；请用与 taskdoc_links 同一次取材的快照。")
    return raw


def resolve_taskdoc_snapshot(explicit_path, work_dir):
    """定位任务书全文快照：显式给的优先，否则找 `<work_dir>/task_doc.snapshot.md`；都没有 → None。

    显式给了却不存在 = 配置错，当场抛（不悄悄退回探测）。返回 None 只表示「本轮没有快照可落」，
    授权是否因此不成立由 `render_golden_wrapper` 按 `authorization.kind` 判。
    """
    if explicit_path:
        path = os.path.abspath(explicit_path)
        if not os.path.isfile(path):
            raise CasesetError("--taskdoc-snapshot 指定的文件不存在: " + repr(path))
        return path
    guess = os.path.join(os.path.abspath(work_dir), TASKDOC_SNAPSHOT_NAME)
    return guess if os.path.isfile(guess) else None


def cross_check_spec_golden(spec, caseset):
    """`spec.golden` ↔ **从任务书 golden 源码派生**的契约，逐字段对账；不符即 fail-closed。

    为什么必须在**产包装层时**就对账：`validator._reconcile_golden` 会拿 `spec.golden` 当判据锚，
    与 caseset 里每条 case 的 `golden_tier` 逐字段核（source / method_kind / authorization_kind /
    snapshot_sha）。任何一项不一致 → 每条 case 各记一条 problem → 强制 blocked。
    那道对账发生在**真机跑完之后**，代价是一整轮跑测；而这里的判据在生成期就全都有了。

    ⚠ 对账的是「两个独立源」：`source` / `method_kind` 由 `_derive_golden_method_kind` 从任务书
    自带 golden 的 **AST** 派生，`spec.golden` 由人按任务书写——两边同意才算数。
    `authorization` 不在此列：它是「任务书这句话算不算真值口径指定」的 NL 判断，机器派生不出来，
    故以 spec 为唯一真源、原样写进包装层，其真伪另由 `verify_authorization` 读快照逐字核。
    """
    spec_golden = spec.get("golden") if isinstance(spec, dict) else None
    if spec_golden is None:                      # legacy：spec 没有判据锚 → validator 走 caseset 自声明档
        return None
    if not isinstance(spec_golden, dict):
        raise CasesetError("spec.golden 须为 object，得 " + type(spec_golden).__name__)
    derived = caseset["golden_contract_derived"]
    for field in ("source", "method_kind"):
        if spec_golden.get(field) != derived[field]:
            raise CasesetError(
                "spec.golden." + field + "=" + repr(spec_golden.get(field))
                + " ≠ 从任务书自带 golden 派生的 " + repr(derived[field])
                + "——两个独立源必须一致，否则 validator 的判据锚对账会把每条 case 判成 blocked。"
                " 请核任务书 golden 的实际实现后改 spec（不是改这里）。")
    snapshot = spec_golden.get("taskdoc_snapshot")
    declared = snapshot.get("sha256") if isinstance(snapshot, dict) else None
    declared = declared.strip().lower() if isinstance(declared, str) else None
    if declared != caseset["taskdoc_sha256"]:
        raise CasesetError(
            "spec.golden.taskdoc_snapshot.sha256=" + repr(declared) + " ≠ 本轮任务书 sha256 "
            + repr(caseset["taskdoc_sha256"]) + "——包装层的 GOLDEN_CONTRACT 逐字记录后者，"
            "两者不等即 validator 对账不过。请把本轮任务书快照指纹填进 spec.golden。")
    return spec_golden.get("authorization")


def render_golden_wrapper(caseset, out_path, authorization=None, taskdoc_snapshot=None):
    """产一份 `golden.py` 文本（并把冻结原件、任务书快照写到同目录），满足 `gen_cases.load_golden` 的硬要求。

    `gen_cases.load_golden` 强制导出 `golden_fn` + `GOLDEN_SOURCE` + `GOLDEN_PROVENANCE`
    （见 `gen_cases.py` 的属性检查），另可选 `GOLDEN_CONTRACT`——三者都在这里产。

    `authorization` 逐字取自 `spec.golden.authorization`（缺省 `{"kind": "none"}`）：
    「任务书这句话算不算真值口径指定」是 NL 判断，机器派生不出来，故由 spec 声明、这里原样落，
    真伪另由 `precision_policy.verify_authorization` 读快照逐字核（它才是那道闸）。

    `taskdoc_snapshot` 是任务书全文快照的**来源路径**；给了就按内容寻址复核指纹后落到包装层同目录。
    ⚠ 声称 `oracle_method` / `formula` 却没有快照 → 当场 fail-closed：包装层被搬到 `<ops_root>/<op>/`
    后 `verify_authorization` 读不到锚会恒返 False，`derive_golden_tier` 规则② 判 tier 4 blocked——
    与其把这条错留到真机跑完再炸，不如在生成期就拦住。
    """
    golden = caseset["golden_original"]
    derived = caseset["golden_contract_derived"]
    auth = dict(authorization or {"kind": "none"})
    kind = auth.get("kind")
    if kind not in AUTHORIZATION_KINDS:
        raise CasesetError("golden authorization.kind=" + repr(kind) + " 不在受控词表 "
                           + repr(AUTHORIZATION_KINDS))
    snapshot_bytes = None
    if taskdoc_snapshot:
        snapshot_bytes = _read_taskdoc_snapshot(taskdoc_snapshot, caseset["taskdoc_sha256"])
    if kind in ANCHORED_AUTHORIZATION_KINDS:
        for key in ("cite", "quote"):
            if not str(auth.get(key) or "").strip():
                raise CasesetError(
                    "golden authorization.kind=" + repr(kind) + " 声称任务书作了指定，但 " + key
                    + " 为空——引文锚不全则授权无从核实；若任务书其实只是「参考谁的实现」，"
                      "kind 应为 impl_reference（它不构成 golden 授权）。")
        if snapshot_bytes is None:
            raise CasesetError(
                "golden authorization.kind=" + repr(kind) + " 声称任务书作了指定，但没有任务书全文快照可落"
                "——引文锚（" + TASKDOC_SNAPSHOT_NAME + ":<行号>）必须能被 verify_authorization 逐字复核，"
                "缺快照即授权核不过、档位掉 tier 4。请用 --taskdoc-snapshot 指向 CP-A 落的快照"
                "（fetch_source.py 写在其 --out 目录下）。")
    contract = {
        "source": derived["source"],
        "method_kind": derived["method_kind"],
        "method": (caseset["mapping_ir"]["golden_contract_derivation"]["reference_api_calls"] or
                   [caseset["expect_func"]])[0],
        "authorization": auth,
        "taskdoc_snapshot": {"sha256": caseset["taskdoc_sha256"]},
    }
    provenance = (
        "任务书自带 self_test_case golden（" + str(caseset["source_dir"]) + "/"
        + str(golden["filename"]) + "，sha256 " + str(golden["sha256"])[:16]
        + "…），经 taskdoc_caseset.py 按 mapping_ir 生成的包装层逐字调用；"
        "任务书原文 sha256 " + str(caseset["taskdoc_sha256"])[:16] + "…")
    text = _WRAPPER_TEMPLATE.format(
        schema_version=SCHEMA_VERSION,
        original_filename=golden["filename"],
        original_sha256=golden["sha256"],
        original_function=golden["function"],
        returns=caseset["mapping_ir"]["golden_call"]["returns"],
        golden_source="task_spec_expected " + str(caseset["expect_func"]),
        golden_provenance=provenance,
        golden_contract=contract,
        positional=caseset["mapping_ir"]["golden_call"]["positional"],
    )
    out_dir = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(out_dir, exist_ok=True)
    # 冻结原件：原名、原字节，只读放在包装层旁边（包装层加载时复核 sha256）。
    original_path = content_address.safe_path(out_dir, golden["filename"])
    raw = golden["text"].encode("utf-8")
    actual = hashlib.sha256(raw).hexdigest()
    if actual != golden["sha256"]:
        raise CasesetError("冻结原件字节与记录的 sha256 不符（recorded=" + golden["sha256"]
                           + ", actual=" + actual + "）")
    with open(original_path, "w", encoding="utf-8", newline="") as dst:
        dst.write(golden["text"])
    with open(out_path, "w", encoding="utf-8", newline="") as dst:
        dst.write(text)
    # 授权锚：与包装层同目录、原名、**原字节**（`verify_authorization` 按行号逐字核，见上）。
    # 落在这里而不是工作区，是为了让锚随 golden 一起被搬到 `<ops_root>/<op>/`、随算子一起被复核。
    snapshot_path = None
    if snapshot_bytes is not None:
        snapshot_path = content_address.safe_path(out_dir, TASKDOC_SNAPSHOT_NAME)
        with open(snapshot_path, "wb") as dst:
            dst.write(snapshot_bytes)
    return {"wrapper": os.path.abspath(out_path), "original": original_path,
            "taskdoc_snapshot": snapshot_path}


# ================================================================================
# CLI
# ================================================================================
def _load_json_file(path, where):
    try:
        with open(path, "rb") as src:
            raw = src.read()
    except OSError as ex:
        raise CasesetError(where + " 读取失败 " + repr(path) + ": " + str(ex)) from ex
    return _json_loads_strict(raw.decode("utf-8"), where)


def build(links_path, spec_path, out_dir, taskdoc_snapshot=None):
    """全流程：读 links 产物 → discover → build_mapping_ir → normalize → 写产物 + golden 包装层。

    `taskdoc_snapshot`：任务书全文快照路径（缺省找 `<links 所在目录>/task_doc.snapshot.md`）。
    它是 golden 授权锚的载体，随包装层落到同目录，见 `render_golden_wrapper`。
    """
    links_json = _load_json_file(links_path, "taskdoc_links.json")
    spec = _load_json_file(spec_path, "spec.json")
    work_dir = os.path.dirname(os.path.abspath(links_path))
    snapshot = resolve_taskdoc_snapshot(taskdoc_snapshot, work_dir)
    discovery = discover(links_json, work_dir, target_op=spec.get("op"))
    if discovery["outcome"] != "recognized":
        return discovery, None, None
    try:
        mapping_ir = build_mapping_ir(discovery.get("prototype"), discovery["cases"], spec,
                                      golden=discovery["golden"])
        caseset = normalize(discovery, mapping_ir, spec, links_json["taskdoc_sha256"])
    except CasesetError as ex:
        if ex.outcome is None:
            raise
        return {"outcome": ex.outcome, "reason": str(ex),
                "warnings": discovery.get("warnings") or []}, None, None
    # 判据锚对账在**写产物之前**：不一致就别产一份注定被 validator 判 blocked 的 golden。
    authorization = cross_check_spec_golden(spec, caseset)
    os.makedirs(out_dir, exist_ok=True)
    artifact_path = content_address.atomic_write_json(out_dir, "taskdoc_caseset.json", caseset)
    golden_paths = render_golden_wrapper(caseset, os.path.join(out_dir, "golden", "golden.py"),
                                         authorization=authorization, taskdoc_snapshot=snapshot)
    return {"outcome": "recognized", "warnings": caseset["warnings"]}, caseset, {
        "caseset": artifact_path, **golden_paths}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="任务书自带用例集识别 + 接口映射 IR + 规范化 + golden 包装层生成")
    parser.add_argument("--links", required=True, help="taskdoc_links.py 产出的 taskdoc_links.json")
    parser.add_argument("--spec", required=True, help="本仓 spec.json")
    parser.add_argument("--out", required=True, help="产物目录")
    parser.add_argument("--taskdoc-snapshot", default=None, metavar="PATH",
                        help="任务书全文快照（CP-A 的 fetch_source.py 落在其 --out 目录下的 "
                             + TASKDOC_SNAPSHOT_NAME + "）。它是 golden 授权锚的载体，"
                             "会按指纹复核后随包装层落到同目录；省略则找 --links 所在目录")
    args = parser.parse_args(argv)
    try:
        result, caseset, paths = build(args.links, args.spec, args.out,
                                       taskdoc_snapshot=args.taskdoc_snapshot)
    except (CasesetError, content_address.ContentAddressError, OSError, UnicodeError) as ex:
        outcome = getattr(ex, "outcome", None)
        sys.stderr.write("[taskdoc_caseset] 失败: " + str(ex) + "\n")
        return 1 if outcome is None else 2
    for warn in result.get("warnings") or []:
        sys.stderr.write("[taskdoc_caseset] 告警: " + warn + "\n")
    if result["outcome"] != "recognized":
        sys.stderr.write("[taskdoc_caseset] outcome=" + result["outcome"] + ": "
                         + str(result.get("reason")) + "\n")
        return 2
    sys.stderr.write("[taskdoc_caseset] 规范化 " + str(len(caseset["cases"])) + " 条用例 → "
                     + paths["caseset"] + "\n")
    sys.stderr.write("[taskdoc_caseset] golden 包装层 " + paths["wrapper"]
                     + "（冻结原件 " + paths["original"] + "）\n")
    # 「锚落没落」必须显式可见：没落时后续任何 oracle_method/formula 声明都核不过，
    # 而那道失败要到 gen_cases 派档时才现形——这里先把话说在前面。
    if paths.get("taskdoc_snapshot"):
        sys.stderr.write("[taskdoc_caseset] 任务书授权锚 " + paths["taskdoc_snapshot"]
                         + "（sha256 " + caseset["taskdoc_sha256"] + "）\n")
    else:
        sys.stderr.write("[taskdoc_caseset] 告警: 未落 " + TASKDOC_SNAPSHOT_NAME
                         + "——授权锚不可机核，golden 只能按 impl_reference/none 档走"
                           "（用 --taskdoc-snapshot 指向 CP-A 的快照即可补上）\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
