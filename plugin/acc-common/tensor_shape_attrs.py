"""张量结构、数组属性与物理布局的确定性契约。

本模块只做 JSON 形状的校验、归一化和收据生成，不 import numpy/torch、不执行算子。
它解决的是几类过去容易被 Python 真值或连续化操作悄悄抹平的结构事实：

* ``shape=[]`` 是 rank-0 scalar，``shape=[0, ...]`` 才是 empty tensor；
* ``int_array`` 由调用方显式声明，数组值可以是 ``[]``，不得靠“默认值非空”猜类型；
* 有长度关系的属性只能以 atomic row 输入，关系例外必须带来源解析记录；
* 负轴按 rank 做 cyclic normalization，重复轴政策由调用方显式选择；
* ND 是受控 format；logical shape 与 strides/storage_offset/base storage 共同构成物理布局，
  input/output 的 role 分开记。观察到的布局必须与冻结收据逐字段一致，不能静默 contiguous。

这里没有任何算子名或算子分支。调用方只能把 taskdoc/spec/contract-IR 已解析出的结构事实交进来；
本模块不猜任务书语义，也不替生成器决定哪些结构必须覆盖。
"""

from __future__ import annotations

import hashlib
import json


class TensorShapeAttrError(ValueError):
    """输入不满足张量结构/属性/布局确定性契约。"""


ATTR_SCALAR = "scalar"
ATTR_INT_ARRAY = "int_array"
ATTR_TYPES = frozenset({ATTR_SCALAR, ATTR_INT_ARRAY})

CYCLIC_DUPLICATES_ALLOW = "allow"
CYCLIC_DUPLICATES_REJECT = "reject_after_normalization"
CYCLIC_DUPLICATE_POLICIES = frozenset({
    CYCLIC_DUPLICATES_ALLOW,
    CYCLIC_DUPLICATES_REJECT,
})

CONSTRAINT_EQUAL_LENGTH = "equal_length"
CONSTRAINT_RELATIONS = frozenset({CONSTRAINT_EQUAL_LENGTH})

TENSOR_FORMAT_ND = "nd"
TENSOR_FORMATS = frozenset({TENSOR_FORMAT_ND})
LAYOUT_ROLE_INPUT = "input"
LAYOUT_ROLE_OUTPUT = "output"
LAYOUT_ROLES = frozenset({LAYOUT_ROLE_INPUT, LAYOUT_ROLE_OUTPUT})
LAYOUT_CONTIGUOUS = "contiguous"
LAYOUT_NONCONTIGUOUS = "noncontiguous"
LAYOUT_KINDS = frozenset({LAYOUT_CONTIGUOUS, LAYOUT_NONCONTIGUOUS})
LAYOUT_CAPABILITY_CONTIGUOUS = "contiguous_strides"
LAYOUT_CAPABILITY_SPAN_SEPARABLE = "span_separable_non_overlapping"
LAYOUT_CAPABILITIES = frozenset({
    LAYOUT_CAPABILITY_CONTIGUOUS,
    LAYOUT_CAPABILITY_SPAN_SEPARABLE,
})

SHAPE_RANK0 = "rank0_scalar"
SHAPE_EMPTY = "empty_tensor"
SHAPE_NONEMPTY = "nonempty_tensor"

SHAPE_RECEIPT_SCHEMA = "oprunway.tensor_shape"
LAYOUT_RECEIPT_SCHEMA = "oprunway.tensor_layout"
ATOMIC_ROWS_SCHEMA = "oprunway.atomic_attr_rows"
RECEIPT_SCHEMA_VERSION = 1

_LAYOUT_DECLARATION_KEYS = frozenset({
    "case_id",
    "tensor_name",
    "tensor_index",
    "role",
    "format",
    "logical_shape",
    "layout_kind",
    "layout_capability",
    "strides",
    "storage_offset",
    "base_storage_numel",
})
_LAYOUT_RECEIPT_DERIVED_KEYS = frozenset({
    "schema",
    "schema_version",
    "rank",
    "numel",
    "structural_kind",
    "minimum_base_storage_numel",
})
_LAYOUT_RECEIPT_KEYS = _LAYOUT_DECLARATION_KEYS | _LAYOUT_RECEIPT_DERIVED_KEYS
_SOURCE_PARSE_KEYS = frozenset({
    "source", "source_sha256", "cite", "quote", "interpretation",
})
_SOURCE_BINDING_KEYS = frozenset({
    "compose_kind",
    "spec_sha256",
    "taskdoc_snapshot_sha256",
    "source_facts_sha256",
})
_COMPOSE_KINDS = frozenset({"spec_taskdoc_compose"})
_ATOMIC_ROW_KEYS = frozenset({"id", "attrs", "source_parse"})
_ATOMIC_ROW_APPLICABILITY_KEY = "applicability"
_ATOMIC_APPLICABILITY_KEYS = frozenset({
    "rank_domain", "required_nonempty_attrs", "source_parse",
})
_ATOMIC_RANK_DOMAIN_KEYS = frozenset({"input", "allowed_ranks"})
_MAX_PROFILE_RANK = 8
_SHA256_HEX = frozenset("0123456789abcdef")


def _plain_int(value, where, *, minimum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        raise TensorShapeAttrError(f"{where} 须为整数，得 {value!r}")
    if minimum is not None and value < minimum:
        raise TensorShapeAttrError(f"{where} 须 >= {minimum}，得 {value}")
    return int(value)


def _object(value, where):
    if not isinstance(value, dict):
        raise TensorShapeAttrError(f"{where} 须为 object，得 {type(value).__name__}")
    bad_keys = [key for key in value if not isinstance(key, str)]
    if bad_keys:
        raise TensorShapeAttrError(
            f"{where} 的键须为字符串，得 {[repr(key) for key in bad_keys]}")
    return value


def _exact_keys(value, expected, where):
    got = set(value)
    if got != set(expected):
        raise TensorShapeAttrError(
            f"{where} 键集合须恰为 {sorted(expected)}，"
            f"缺少={sorted(set(expected) - got)}，多出={sorted(got - set(expected))}")


def _nonempty_string(value, where):
    if not isinstance(value, str) or not value.strip():
        raise TensorShapeAttrError(f"{where} 须为非空字符串")
    return value


def _sha256(value, where):
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in _SHA256_HEX for char in value)):
        raise TensorShapeAttrError(f"{where} 须为 64 位小写 sha256")
    return value


def _canonical_bytes(value, where):
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as ex:
        raise TensorShapeAttrError(f"{where} 不是 canonical JSON：{ex}") from ex


def _canonical_sha256(value, where):
    return hashlib.sha256(_canonical_bytes(value, where)).hexdigest()


def _strict_equal(left, right, where):
    """用 canonical JSON 比较，避免 ``True == 1`` / ``1.0 == 1`` 的 Python 宽松相等。"""
    return _canonical_bytes(left, f"{where}.left") == _canonical_bytes(
        right, f"{where}.right")


def normalize_shape(value, *, where="shape", max_rank=8):
    """校验 logical shape 并返回 tuple；空序列原样表示 rank 0。

    ``[]``/``()`` 绝不改写成 ``(1,)``。shape 里的 0 表示 empty tensor 的 extent，
    与 rank 0 是两件事。``max_rank=None`` 可用于只做结构校验而不施加运行时上限。
    """
    if not isinstance(value, (list, tuple)):
        raise TensorShapeAttrError(f"{where} 须为整数数组，得 {type(value).__name__}")
    dims = tuple(_plain_int(dim, f"{where}[{idx}]", minimum=0)
                 for idx, dim in enumerate(value))
    if max_rank is not None:
        limit = _plain_int(max_rank, "max_rank", minimum=0)
        if len(dims) > limit:
            raise TensorShapeAttrError(
                f"{where} rank={len(dims)} 超出受控上限 {limit}")
    return dims


def shape_numel(shape):
    """返回 logical numel；rank 0 的空乘积是 1。"""
    dims = normalize_shape(shape, max_rank=None)
    total = 1
    for dim in dims:
        total *= dim
    return total


def classify_shape(shape):
    """区分 rank0 scalar、empty tensor 与普通非空 tensor。"""
    dims = normalize_shape(shape, max_rank=None)
    if not dims:
        return SHAPE_RANK0
    if 0 in dims:
        return SHAPE_EMPTY
    return SHAPE_NONEMPTY


def make_shape_receipt(shape, *, where="shape", max_rank=8):
    """生成 JSON-safe 的 logical-shape 收据。"""
    dims = normalize_shape(shape, where=where, max_rank=max_rank)
    return {
        "schema": SHAPE_RECEIPT_SCHEMA,
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "logical_shape": list(dims),
        "rank": len(dims),
        "numel": shape_numel(dims),
        "structural_kind": classify_shape(dims),
    }


def assert_shape_identity(expected, actual, *, where="shape"):
    """要求两个 logical shape 逐维一致；专门堵住 ``()``→``(1,)`` 等价化。"""
    left = normalize_shape(expected, where=f"{where}.expected", max_rank=None)
    right = normalize_shape(actual, where=f"{where}.actual", max_rank=None)
    if left != right:
        raise TensorShapeAttrError(
            f"{where} logical shape 漂移：expected={left} (rank={len(left)})，"
            f"actual={right} (rank={len(right)})；rank0 不得用单元素 rank1 替代")
    return left


def normalize_int_array(value, *, where="value"):
    """校验 ``int_array`` 值；空数组合法，bool/异质/嵌套元素拒绝。"""
    if not isinstance(value, list):
        raise TensorShapeAttrError(
            f"{where} 声明为 {ATTR_INT_ARRAY}，值须为 list[int]（允许 []），"
            f"得 {type(value).__name__}")
    return [_plain_int(item, f"{where}[{idx}]") for idx, item in enumerate(value)]


def normalize_attr_value(value, *, attr_type, where="attr"):
    """按**显式** ``attr_type`` 校验值，不从值或 default 反推数组性。"""
    if attr_type not in ATTR_TYPES:
        raise TensorShapeAttrError(
            f"{where}.attr_type={attr_type!r} 不在受控词表 {sorted(ATTR_TYPES)}")
    if attr_type == ATTR_INT_ARRAY:
        return normalize_int_array(value, where=where)
    if isinstance(value, list):
        raise TensorShapeAttrError(
            f"{where} 声明为 {ATTR_SCALAR}，不得承载 list；数组属性须显式声明 "
            f"attr_type={ATTR_INT_ARRAY!r}")
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TensorShapeAttrError(
        f"{where} 标量值须为 null/bool/int/float/str，得 {type(value).__name__}")


def normalize_declared_attrs(attr_types, attrs, *, where="attrs", require_all=True):
    """按 ``{attr_name: attr_type}`` 声明校验一整行属性，返回独立 JSON 值副本。"""
    declarations = _object(attr_types, "attr_types")
    values = _object(attrs, where)
    for name, attr_type in declarations.items():
        _nonempty_string(name, "attr_types 的属性名")
        if attr_type not in ATTR_TYPES:
            raise TensorShapeAttrError(
                f"attr_types.{name}={attr_type!r} 不在受控词表 {sorted(ATTR_TYPES)}")
    unknown = set(values) - set(declarations)
    if unknown:
        raise TensorShapeAttrError(f"{where} 含未声明属性 {sorted(unknown)}")
    missing = set(declarations) - set(values)
    if require_all and missing:
        raise TensorShapeAttrError(
            f"{where} 缺属性 {sorted(missing)}；atomic row 必须一次给齐所有声明成员")
    return {
        name: normalize_attr_value(value, attr_type=declarations[name],
                                   where=f"{where}.{name}")
        for name, value in values.items()
    }


def _normalize_source_parse(value, where):
    record = _object(value, where)
    _exact_keys(record, _SOURCE_PARSE_KEYS, where)
    return {
        "source": _nonempty_string(record["source"], f"{where}.source"),
        "source_sha256": _sha256(
            record["source_sha256"], f"{where}.source_sha256"),
        "cite": _nonempty_string(record["cite"], f"{where}.cite"),
        "quote": _nonempty_string(record["quote"], f"{where}.quote"),
        "interpretation": _nonempty_string(
            record["interpretation"], f"{where}.interpretation"),
    }


def _normalize_source_binding(value, where):
    binding = _object(value, where)
    _exact_keys(binding, _SOURCE_BINDING_KEYS, where)
    compose_kind = binding["compose_kind"]
    if compose_kind not in _COMPOSE_KINDS:
        raise TensorShapeAttrError(
            f"{where}.compose_kind={compose_kind!r} 不在受控词表 {sorted(_COMPOSE_KINDS)}")
    return {
        "compose_kind": compose_kind,
        "spec_sha256": _sha256(binding["spec_sha256"], f"{where}.spec_sha256"),
        "taskdoc_snapshot_sha256": _sha256(
            binding["taskdoc_snapshot_sha256"],
            f"{where}.taskdoc_snapshot_sha256"),
        "source_facts_sha256": _sha256(
            binding["source_facts_sha256"], f"{where}.source_facts_sha256"),
    }


def _normalize_atomic_applicability(value, attr_types, where):
    """规范化一条 atomic row 的 profile 适用谓词。

    谓词必须把 rank 绑定到**具名 tensor input**，把“非空”绑定到已声明的
    ``int_array`` attr，并自带来源解析锚。这里只校声明；具名 input 是否在某个
    profile 中唯一存在，由 :func:`evaluate_atomic_attr_applicability` 对每个 cell 实测。
    """
    record = _object(value, where)
    _exact_keys(record, _ATOMIC_APPLICABILITY_KEYS, where)
    rank_domain = _object(record["rank_domain"], f"{where}.rank_domain")
    _exact_keys(rank_domain, _ATOMIC_RANK_DOMAIN_KEYS, f"{where}.rank_domain")
    input_name = _nonempty_string(
        rank_domain["input"], f"{where}.rank_domain.input")
    ranks = rank_domain["allowed_ranks"]
    if not isinstance(ranks, list) or not ranks:
        raise TensorShapeAttrError(
            f"{where}.rank_domain.allowed_ranks 须为非空 rank 列表")
    normalized_ranks = []
    for idx, rank in enumerate(ranks):
        normalized = _plain_int(
            rank, f"{where}.rank_domain.allowed_ranks[{idx}]", minimum=0)
        if normalized > _MAX_PROFILE_RANK:
            raise TensorShapeAttrError(
                f"{where}.rank_domain.allowed_ranks[{idx}]={normalized} 超出"
                f"受控上限 {_MAX_PROFILE_RANK}")
        normalized_ranks.append(normalized)
    if len(normalized_ranks) != len(set(normalized_ranks)):
        raise TensorShapeAttrError(
            f"{where}.rank_domain.allowed_ranks 含重复 rank {normalized_ranks}")

    nonempty = record["required_nonempty_attrs"]
    if (not isinstance(nonempty, list)
            or any(not isinstance(name, str) or not name.strip() for name in nonempty)):
        raise TensorShapeAttrError(
            f"{where}.required_nonempty_attrs 须为属性名列表（可为空）")
    if len(nonempty) != len(set(nonempty)):
        raise TensorShapeAttrError(
            f"{where}.required_nonempty_attrs 含重复属性 {nonempty}")
    unknown = set(nonempty) - set(attr_types)
    if unknown:
        raise TensorShapeAttrError(
            f"{where}.required_nonempty_attrs 引用了未声明属性 {sorted(unknown)}")
    non_arrays = [name for name in nonempty
                  if attr_types[name] != ATTR_INT_ARRAY]
    if non_arrays:
        raise TensorShapeAttrError(
            f"{where}.required_nonempty_attrs={non_arrays} 不是显式 "
            f"{ATTR_INT_ARRAY}；不得从标量值猜非空数组语义")
    return {
        "rank_domain": {
            "input": input_name,
            "allowed_ranks": normalized_ranks,
        },
        "required_nonempty_attrs": list(nonempty),
        "source_parse": _normalize_source_parse(
            record["source_parse"], f"{where}.source_parse"),
    }


def _normalize_constraint_groups(attr_types, groups, where):
    if not isinstance(groups, list) or not groups:
        raise TensorShapeAttrError(f"{where} 须为非空 constraint group 列表")
    normalized = []
    seen_ids = set()
    for group_idx, raw in enumerate(groups):
        item_where = f"{where}[{group_idx}]"
        group = _object(raw, item_where)
        _exact_keys(group, {"id", "members", "relation", "exceptions"}, item_where)
        group_id = _nonempty_string(group["id"], f"{item_where}.id")
        if group_id in seen_ids:
            raise TensorShapeAttrError(f"{item_where}.id={group_id!r} 重复")
        seen_ids.add(group_id)
        members = group["members"]
        if (not isinstance(members, list) or len(members) < 2
                or len(members) != len(set(members))
                or any(not isinstance(name, str) or not name for name in members)):
            raise TensorShapeAttrError(
                f"{item_where}.members 须为至少两个非空、无重复的属性名")
        unknown = set(members) - set(attr_types)
        if unknown:
            raise TensorShapeAttrError(
                f"{item_where}.members 引用了未声明属性 {sorted(unknown)}")
        non_arrays = [name for name in members if attr_types[name] != ATTR_INT_ARRAY]
        if non_arrays:
            raise TensorShapeAttrError(
                f"{item_where}.members={non_arrays} 不是显式 {ATTR_INT_ARRAY}；"
                "长度关系不得从标量/默认值猜")
        relation = group["relation"]
        if relation not in CONSTRAINT_RELATIONS:
            raise TensorShapeAttrError(
                f"{item_where}.relation={relation!r} 不在受控词表 "
                f"{sorted(CONSTRAINT_RELATIONS)}")
        exceptions = group["exceptions"]
        if not isinstance(exceptions, list):
            raise TensorShapeAttrError(f"{item_where}.exceptions 须为列表（没有例外就写 []）")
        normalized_exceptions = []
        seen_patterns = set()
        for exception_idx, raw_exception in enumerate(exceptions):
            exception_where = f"{item_where}.exceptions[{exception_idx}]"
            exception = _object(raw_exception, exception_where)
            _exact_keys(exception, {"lengths", "source_parse"}, exception_where)
            lengths = _object(exception["lengths"], f"{exception_where}.lengths")
            _exact_keys(lengths, set(members), f"{exception_where}.lengths")
            normalized_lengths = {
                name: _plain_int(lengths[name], f"{exception_where}.lengths.{name}", minimum=0)
                for name in members
            }
            pattern = tuple((name, normalized_lengths[name]) for name in members)
            if pattern in seen_patterns:
                raise TensorShapeAttrError(
                    f"{exception_where}.lengths 与前一条例外重复，来源裁决无法唯一")
            seen_patterns.add(pattern)
            normalized_exceptions.append({
                "lengths": normalized_lengths,
                "source_parse": _normalize_source_parse(
                    exception["source_parse"], f"{exception_where}.source_parse"),
            })
        normalized.append({
            "id": group_id,
            "members": list(members),
            "relation": relation,
            "exceptions": normalized_exceptions,
        })
    return normalized


def normalize_atomic_attr_rows(rows, *, attr_types, constraint_groups, source_binding,
                               where="atomic_attr_rows"):
    """校验关联属性的 atomic rows，并生成逐行 constraint ledger。

    本入口只接收带稳定 ``id`` 与来源记录的完整 row，不接收 per-attr domain，因此没有独立
    笛卡尔展开入口。返回值给整张表和逐 row 做内容寻址；生成器随后必须用
    :func:`bind_atomic_attr_row` 按 row id 绑定，不能拿各列重新组合。

    ``source_binding`` 同时锚定 staged spec、taskdoc snapshot 与 CP-A source facts；关系例外和
    每一行自己的 ``source_parse`` 另带 source digest + cite + quote。这里不替上游解释散文，
    但任何来源或表内容变化都会改变 digest，旧 ledger 不能继续冒充同一份 compose 结果。
    """
    declarations = _object(attr_types, "attr_types")
    # 先统一校一遍声明，即使 rows 为空也不能让坏类型藏过去。
    normalize_declared_attrs(declarations, {}, where="attr_types_probe", require_all=False)
    groups = _normalize_constraint_groups(declarations, constraint_groups,
                                          "constraint_groups")
    binding = _normalize_source_binding(source_binding, "source_binding")
    if not isinstance(rows, list) or not rows:
        raise TensorShapeAttrError(f"{where} 须为非空 atomic row 列表")
    normalized_rows = []
    ledger = []
    seen_row_ids = set()
    seen_attrs = {}
    for row_idx, raw in enumerate(rows):
        row_where = f"{where}[{row_idx}]"
        row = _object(raw, row_where)
        row_keys = set(_ATOMIC_ROW_KEYS)
        if _ATOMIC_ROW_APPLICABILITY_KEY in row:
            row_keys.add(_ATOMIC_ROW_APPLICABILITY_KEY)
        _exact_keys(row, row_keys, row_where)
        row_id = _nonempty_string(row["id"], f"{row_where}.id")
        if row_id in seen_row_ids:
            raise TensorShapeAttrError(f"{row_where}.id={row_id!r} 重复")
        seen_row_ids.add(row_id)
        attrs = normalize_declared_attrs(
            declarations, row["attrs"], where=f"{row_where}.attrs", require_all=True)
        attrs_sha = _canonical_sha256(attrs, f"{row_where}.attrs")
        if attrs_sha in seen_attrs:
            raise TensorShapeAttrError(
                f"{row_where}.attrs 与 row {seen_attrs[attrs_sha]!r} 重复；"
                "同一 atomic 组合不得靠换 id 重复计覆盖")
        seen_attrs[attrs_sha] = row_id
        normalized_row = {
            "id": row_id,
            "attrs": attrs,
            "source_parse": _normalize_source_parse(
                row["source_parse"], f"{row_where}.source_parse"),
        }
        if _ATOMIC_ROW_APPLICABILITY_KEY in row:
            applicability = _normalize_atomic_applicability(
                row[_ATOMIC_ROW_APPLICABILITY_KEY], declarations,
                f"{row_where}.applicability")
            normalized_row["applicability"] = applicability
            normalized_row["applicability_sha256"] = _canonical_sha256(
                applicability, f"{row_where}.applicability")
        normalized_row["row_sha256"] = _canonical_sha256(
            normalized_row, f"{row_where}.row")
        normalized_rows.append(normalized_row)
        for group in groups:
            lengths = {name: len(attrs[name]) for name in group["members"]}
            relation_ok = len(set(lengths.values())) == 1
            if relation_ok:
                ledger.append({
                    "row_index": row_idx,
                    "row_id": row_id,
                    "row_sha256": normalized_row["row_sha256"],
                    "group_id": group["id"],
                    "relation": group["relation"],
                    "lengths": lengths,
                    "resolution": "relation_satisfied",
                })
                continue
            matches = [exception for exception in group["exceptions"]
                       if exception["lengths"] == lengths]
            if len(matches) != 1:
                raise TensorShapeAttrError(
                    f"{row_where} 违反 constraint group {group['id']!r} 的 "
                    f"{group['relation']}：lengths={lengths}，且没有唯一的来源解析例外")
            ledger.append({
                "row_index": row_idx,
                "row_id": row_id,
                "row_sha256": normalized_row["row_sha256"],
                "group_id": group["id"],
                "relation": group["relation"],
                "lengths": lengths,
                "resolution": "source_parsed_exception",
                "source_parse": dict(matches[0]["source_parse"]),
            })
    result = {
        "schema": ATOMIC_ROWS_SCHEMA,
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "source_binding": binding,
        "attr_types": dict(declarations),
        "rows": normalized_rows,
        "constraint_groups": groups,
        "constraint_ledger": ledger,
    }
    result["sha256"] = _canonical_sha256(result, f"{where}.contract")
    return result


def validate_atomic_attr_contract(contract, *, expected_sha256,
                                  where="atomic_attr_contract"):
    """重建 atomic-row 表并按外部摘要校验，防 row/来源/ledger 自洽改写。"""
    value = _object(contract, where)
    expected_keys = {
        "schema", "schema_version", "source_binding", "attr_types", "rows",
        "constraint_groups", "constraint_ledger", "sha256",
    }
    _exact_keys(value, expected_keys, where)
    if value["schema"] != ATOMIC_ROWS_SCHEMA:
        raise TensorShapeAttrError(f"{where}.schema 非 {ATOMIC_ROWS_SCHEMA!r}")
    if type(value["schema_version"]) is not int or value["schema_version"] != RECEIPT_SCHEMA_VERSION:
        raise TensorShapeAttrError(
            f"{where}.schema_version 须为整数 {RECEIPT_SCHEMA_VERSION}")
    wanted_sha = _sha256(expected_sha256, f"{where}.expected_sha256")
    rows = value["rows"]
    if not isinstance(rows, list):
        raise TensorShapeAttrError(f"{where}.rows 须为列表")
    raw_rows = []
    for idx, row in enumerate(rows):
        item = _object(row, f"{where}.rows[{idx}]")
        has_applicability = ("applicability" in item
                             or "applicability_sha256" in item)
        expected_row_keys = set(_ATOMIC_ROW_KEYS) | {"row_sha256"}
        if has_applicability:
            expected_row_keys |= {"applicability", "applicability_sha256"}
        _exact_keys(item, expected_row_keys, f"{where}.rows[{idx}]")
        raw_row = {key: item[key] for key in _ATOMIC_ROW_KEYS}
        if has_applicability:
            raw_row["applicability"] = item["applicability"]
        raw_rows.append(raw_row)
    rebuilt = normalize_atomic_attr_rows(
        raw_rows,
        attr_types=value["attr_types"],
        constraint_groups=value["constraint_groups"],
        source_binding=value["source_binding"],
        where=f"{where}.recompute",
    )
    if not _strict_equal(value, rebuilt, where):
        raise TensorShapeAttrError(
            f"{where} 的 row/来源/constraint ledger 与确定性重算不一致")
    if value["sha256"] != wanted_sha:
        raise TensorShapeAttrError(
            f"{where}.sha256={value['sha256']!r} 与外部冻结摘要 {wanted_sha!r} 不一致")
    return rebuilt


def bind_atomic_attr_row(contract, row_id, attrs, *, expected_contract_sha256,
                         where="atomic_attr_binding"):
    """把生成 case 的 attrs 逐字绑定到权威 row id；同长度交叉组合在此失败。"""
    value = validate_atomic_attr_contract(
        contract, expected_sha256=expected_contract_sha256,
        where=f"{where}.contract")
    wanted_id = _nonempty_string(row_id, f"{where}.row_id")
    matches = [row for row in value["rows"] if row["id"] == wanted_id]
    if len(matches) != 1:
        raise TensorShapeAttrError(
            f"{where}.row_id={wanted_id!r} 未唯一绑定权威 atomic row")
    normalized = normalize_declared_attrs(
        value["attr_types"], attrs, where=f"{where}.attrs", require_all=True)
    row = matches[0]
    if not _strict_equal(normalized, row["attrs"], f"{where}.attrs"):
        raise TensorShapeAttrError(
            f"{where} attrs 与 row_id={wanted_id!r} 的权威组合不一致；"
            "不得把各属性列独立笛卡尔重组")
    return {
        "row_id": wanted_id,
        "row_sha256": row["row_sha256"],
        "atomic_rows_sha256": value["sha256"],
        "attrs": normalized,
    }


def evaluate_atomic_attr_applicability(
        contract, row_id, profile_inputs, *, expected_contract_sha256,
        where="atomic_attr_applicability"):
    """对一条 ``profile × atomic-row`` cell 机校可执行性。

    返回值只会是 ``executable`` 或 ``excluded``；excluded 带受控原因，供生成器
    把 cell 原样写进 full-denominator ledger。没有 ``applicability`` 的旧 row
    维持旧行为并返回固定的 executable receipt，且不会凭 profile 内容新增字段/摘要。
    """
    value = validate_atomic_attr_contract(
        contract, expected_sha256=expected_contract_sha256,
        where=f"{where}.contract")
    wanted_id = _nonempty_string(row_id, f"{where}.row_id")
    matches = [row for row in value["rows"] if row["id"] == wanted_id]
    if len(matches) != 1:
        raise TensorShapeAttrError(
            f"{where}.row_id={wanted_id!r} 未唯一绑定权威 atomic row")
    row = matches[0]
    applicability = row.get("applicability")
    if applicability is None:
        return {
            "status": "executable",
            "applicability_sha256": None,
            "rank_domain": None,
            "required_nonempty_attrs": [],
            "reasons": [],
        }

    if not isinstance(profile_inputs, list):
        raise TensorShapeAttrError(
            f"{where}.profile_inputs 须为 profile input 列表")
    input_name = applicability["rank_domain"]["input"]
    named = []
    for idx, raw_input in enumerate(profile_inputs):
        item = _object(raw_input, f"{where}.profile_inputs[{idx}]")
        if item.get("name") == input_name:
            named.append(item)
    if len(named) != 1 or named[0].get("kind") != "tensor":
        raise TensorShapeAttrError(
            f"{where}.rank_domain.input={input_name!r} 未在 profile_inputs 中"
            "唯一绑定 kind='tensor' 的具名输入")
    shape = normalize_shape(
        named[0].get("shape"), where=f"{where}.profile_inputs[{input_name}].shape",
        max_rank=_MAX_PROFILE_RANK)
    allowed = applicability["rank_domain"]["allowed_ranks"]
    actual_rank = len(shape)
    rank_matched = actual_rank in allowed
    rank_receipt = {
        "input": input_name,
        "allowed_ranks": list(allowed),
        "actual_rank": actual_rank,
        "matched": rank_matched,
    }
    reasons = []
    if not rank_matched:
        reasons.append({
            "kind": "rank_outside_domain",
            "input": input_name,
            "actual_rank": actual_rank,
            "allowed_ranks": list(allowed),
        })
    attr_receipts = []
    for name in applicability["required_nonempty_attrs"]:
        actual_length = len(row["attrs"][name])
        matched = actual_length > 0
        attr_receipts.append({
            "attr": name,
            "actual_length": actual_length,
            "matched": matched,
        })
        if not matched:
            reasons.append({"kind": "required_attr_empty", "attr": name})
    return {
        "status": "excluded" if reasons else "executable",
        "applicability_sha256": row["applicability_sha256"],
        "rank_domain": rank_receipt,
        "required_nonempty_attrs": attr_receipts,
        "reasons": reasons,
    }


def normalize_cyclic_indices(values, rank, *, duplicate_policy,
                             where="cyclic_indices"):
    """把范围 ``[-rank, rank-1]`` 的轴号归一到 ``[0, rank-1]``。

    rank 0 只有空轴数组合法。重复轴是否有语义由接口契约决定，因此调用方必须显式给出
    ``allow`` 或 ``reject_after_normalization``，本模块不按算子身份猜。
    """
    dims = normalize_int_array(values, where=where)
    normalized_rank = _plain_int(rank, "rank", minimum=0)
    if duplicate_policy not in CYCLIC_DUPLICATE_POLICIES:
        raise TensorShapeAttrError(
            f"duplicate_policy={duplicate_policy!r} 不在受控词表 "
            f"{sorted(CYCLIC_DUPLICATE_POLICIES)}")
    if normalized_rank == 0:
        if dims:
            raise TensorShapeAttrError(
                f"{where}={dims} 不能作用于 rank 0；rank 0 只有空轴数组合法")
        normalized = []
    else:
        normalized = []
        for idx, value in enumerate(dims):
            if not (-normalized_rank <= value < normalized_rank):
                raise TensorShapeAttrError(
                    f"{where}[{idx}]={value} 越界；rank={normalized_rank} 时合法范围为 "
                    f"[-{normalized_rank}, {normalized_rank - 1}]")
            normalized.append(value % normalized_rank)
    if (duplicate_policy == CYCLIC_DUPLICATES_REJECT
            and len(normalized) != len(set(normalized))):
        raise TensorShapeAttrError(
            f"{where} 归一化后含重复轴 {normalized}，duplicate_policy={duplicate_policy}")
    return {
        "raw": list(dims),
        "normalized": normalized,
        "rank": normalized_rank,
        "had_negative": any(value < 0 for value in dims),
        "duplicate_policy": duplicate_policy,
    }


def contiguous_strides(shape):
    """返回 row-major 连续 strides；rank 0 返回空 tuple。"""
    dims = normalize_shape(shape, max_rank=None)
    result = [0] * len(dims)
    stride = 1
    for idx in range(len(dims) - 1, -1, -1):
        result[idx] = stride
        stride *= dims[idx]
    return tuple(result)


def derive_layout_declaration(shape, *, layout_kind, role, case_id, tensor_name,
                              tensor_index, where="layout"):
    """为 generated case 产生确定性 ND 布局声明。

    ``contiguous`` 使用 row-major stride、offset=0；``noncontiguous`` 使用两倍
    row-major stride + offset=1 的 span-separable gapped view。后者同时见证 stride 和
    storage_offset，且不改 logical shape/value。rank0、empty 或 numel=1 没有可观测的
    noncontiguous 视图，请求该布局时 fail-closed，不拿伪非连续用例计覆盖。

    需要 taskdoc 点名的特定 stride/offset 时，调用方应直接给
    :func:`make_layout_receipt` 传完整声明；本函数只是通用 generated witness。
    """
    dims = normalize_shape(shape, where=f"{where}.logical_shape")
    if layout_kind not in LAYOUT_KINDS:
        raise TensorShapeAttrError(
            f"{where}.layout_kind={layout_kind!r} 不在受控词表 {sorted(LAYOUT_KINDS)}")
    strides = contiguous_strides(dims)
    offset = 0
    capability = LAYOUT_CAPABILITY_CONTIGUOUS
    if layout_kind == LAYOUT_NONCONTIGUOUS:
        if not dims or 0 in dims or shape_numel(dims) <= 1:
            raise TensorShapeAttrError(
                f"{where} unsupported_layout：shape={list(dims)} 无法形成可观测的"
                " noncontiguous view；rank0/empty/singleton 不得冒充布局覆盖")
        strides = tuple(stride * 2 for stride in strides)
        offset = 1
        capability = LAYOUT_CAPABILITY_SPAN_SEPARABLE
    base = _minimum_base_storage_numel(dims, strides, offset)
    declaration = {
        "case_id": _nonempty_string(case_id, f"{where}.case_id"),
        "tensor_name": _nonempty_string(tensor_name, f"{where}.tensor_name"),
        "tensor_index": _plain_int(
            tensor_index, f"{where}.tensor_index", minimum=0),
        "role": role,
        "format": TENSOR_FORMAT_ND,
        "logical_shape": list(dims),
        "layout_kind": layout_kind,
        "layout_capability": capability,
        "strides": list(strides),
        "storage_offset": offset,
        "base_storage_numel": base,
    }
    # 回传前统一走完整声明门，防派生算法与校验词表漂移。
    normalized, _minimum = _layout_declaration(declaration, where)
    return normalized


def _is_contiguous(shape, strides):
    # PyTorch 把空 tensor 视为 contiguous；因此空 tensor 不能拿来证明非连续 transport。
    if 0 in shape:
        return True
    expected = 1
    for size, stride in zip(reversed(shape), reversed(strides)):
        if size > 1 and stride != expected:
            return False
        expected *= size
    return True


def _assert_span_separable_non_overlapping(shape, strides, where):
    """校验本 v1 materializer 明确支持的 span-separable 非重叠布局子集。

    这不是任意正 stride view 的完整无碰撞判据。例如 ``shape=[2,3], strides=[3,2]``
    虽然地址唯一，却不是 span-separable。本版本对这类布局报 ``unsupported_layout``，
    绝不把“当前不会物化”错写成“实得内部重叠”。
    """
    axes = sorted((stride, size) for size, stride in zip(shape, strides) if size > 1)
    span = 1
    for stride, size in axes:
        if stride < span:
            raise TensorShapeAttrError(
                f"{where} unsupported_layout：strides={list(strides)}、shape={list(shape)} "
                f"不满足 capability={LAYOUT_CAPABILITY_SPAN_SEPARABLE!r}；"
                "这不等于断言它一定内部重叠，域外布局须扩能力后再验收")
        span += (size - 1) * stride


def _minimum_base_storage_numel(shape, strides, storage_offset):
    if 0 in shape:
        # 空 view 不访问元素；offset 可以位于 storage 尾后一格。
        return storage_offset
    return storage_offset + sum((size - 1) * stride
                                for size, stride in zip(shape, strides)) + 1


def _layout_declaration(value, where):
    layout = _object(value, where)
    _exact_keys(layout, _LAYOUT_DECLARATION_KEYS, where)
    case_id = _nonempty_string(layout["case_id"], f"{where}.case_id")
    tensor_name = _nonempty_string(layout["tensor_name"], f"{where}.tensor_name")
    tensor_index = _plain_int(layout["tensor_index"], f"{where}.tensor_index", minimum=0)
    role = layout["role"]
    if role not in LAYOUT_ROLES:
        raise TensorShapeAttrError(
            f"{where}.role={role!r} 不在受控词表 {sorted(LAYOUT_ROLES)}")
    tensor_format = layout["format"]
    if tensor_format not in TENSOR_FORMATS:
        raise TensorShapeAttrError(
            f"{where}.format={tensor_format!r} 不在受控词表 {sorted(TENSOR_FORMATS)}")
    shape = normalize_shape(layout["logical_shape"], where=f"{where}.logical_shape")
    raw_strides = layout["strides"]
    if not isinstance(raw_strides, (list, tuple)):
        raise TensorShapeAttrError(f"{where}.strides 须为非负整数数组")
    strides = tuple(_plain_int(value, f"{where}.strides[{idx}]", minimum=0)
                    for idx, value in enumerate(raw_strides))
    if len(strides) != len(shape):
        raise TensorShapeAttrError(
            f"{where}.strides 长度 {len(strides)} != logical rank {len(shape)}")
    storage_offset = _plain_int(layout["storage_offset"],
                                f"{where}.storage_offset", minimum=0)
    base_storage_numel = _plain_int(layout["base_storage_numel"],
                                    f"{where}.base_storage_numel", minimum=0)
    actual_contiguous = _is_contiguous(shape, strides)
    actual_kind = LAYOUT_CONTIGUOUS if actual_contiguous else LAYOUT_NONCONTIGUOUS
    declared_kind = layout["layout_kind"]
    if declared_kind not in LAYOUT_KINDS:
        raise TensorShapeAttrError(
            f"{where}.layout_kind={declared_kind!r} 不在受控词表 {sorted(LAYOUT_KINDS)}")
    if declared_kind != actual_kind:
        raise TensorShapeAttrError(
            f"{where}.layout_kind={declared_kind!r} 与 shape/strides 实得 {actual_kind!r} 不一致")
    capability = layout["layout_capability"]
    if capability not in LAYOUT_CAPABILITIES:
        raise TensorShapeAttrError(
            f"{where}.layout_capability={capability!r} 不在受控词表 "
            f"{sorted(LAYOUT_CAPABILITIES)}")
    expected_capability = (LAYOUT_CAPABILITY_CONTIGUOUS
                           if actual_kind == LAYOUT_CONTIGUOUS
                           else LAYOUT_CAPABILITY_SPAN_SEPARABLE)
    if capability != expected_capability:
        raise TensorShapeAttrError(
            f"{where}.layout_capability={capability!r} 与 layout_kind={actual_kind!r} "
            f"要求的 {expected_capability!r} 不一致")
    if 0 not in shape and actual_kind == LAYOUT_NONCONTIGUOUS:
        _assert_span_separable_non_overlapping(shape, strides, where)
    minimum = _minimum_base_storage_numel(shape, strides, storage_offset)
    if base_storage_numel < minimum:
        raise TensorShapeAttrError(
            f"{where}.base_storage_numel={base_storage_numel} 容不下 view："
            f"minimum={minimum}（shape={list(shape)}, strides={list(strides)}, "
            f"storage_offset={storage_offset}）")
    return {
        "case_id": case_id,
        "tensor_name": tensor_name,
        "tensor_index": tensor_index,
        "role": role,
        "format": tensor_format,
        "logical_shape": list(shape),
        "layout_kind": actual_kind,
        "layout_capability": capability,
        "strides": list(strides),
        "storage_offset": storage_offset,
        "base_storage_numel": base_storage_numel,
    }, minimum


def make_layout_receipt(layout, *, expected_shape, expected_role,
                        expected_case_id, expected_tensor_name, expected_tensor_index,
                        where="layout"):
    """绑定外部 case/slot/shape/role 后生成布局收据；不从待验声明反取身份。"""
    normalized, minimum = _layout_declaration(layout, where)
    assert_shape_identity(expected_shape, normalized["logical_shape"],
                          where=f"{where}.binding")
    if expected_role not in LAYOUT_ROLES:
        raise TensorShapeAttrError(
            f"expected_role={expected_role!r} 不在受控词表 {sorted(LAYOUT_ROLES)}")
    if normalized["role"] != expected_role:
        raise TensorShapeAttrError(
            f"{where}.role={normalized['role']!r} 与绑定角色 {expected_role!r} 不一致；"
            "input/output 布局必须分账")
    identity = {
        "case_id": _nonempty_string(expected_case_id, "expected_case_id"),
        "tensor_name": _nonempty_string(expected_tensor_name, "expected_tensor_name"),
        "tensor_index": _plain_int(
            expected_tensor_index, "expected_tensor_index", minimum=0),
    }
    drift = {key: {"expected": value, "declared": normalized[key]}
             for key, value in identity.items() if normalized[key] != value}
    if drift:
        raise TensorShapeAttrError(
            f"{where} tensor slot 身份漂移：{drift}；同 role/shape 的槽也不得互换")
    shape = tuple(normalized["logical_shape"])
    return {
        "schema": LAYOUT_RECEIPT_SCHEMA,
        "schema_version": RECEIPT_SCHEMA_VERSION,
        **normalized,
        "rank": len(shape),
        "numel": shape_numel(shape),
        "structural_kind": classify_shape(shape),
        "minimum_base_storage_numel": minimum,
    }


def layout_receipt_sha256(receipt, *, where="layout_receipt"):
    """布局收据的类型敏感 canonical JSON 摘要。"""
    return _canonical_sha256(receipt, where)


def validate_layout_receipt(receipt, *, expected_shape, expected_role,
                            expected_case_id, expected_tensor_name, expected_tensor_index,
                            where="layout_receipt"):
    """重算并逐字段校验布局收据，返回 canonical 副本。"""
    value = _object(receipt, where)
    _exact_keys(value, _LAYOUT_RECEIPT_KEYS, where)
    if value.get("schema") != LAYOUT_RECEIPT_SCHEMA:
        raise TensorShapeAttrError(
            f"{where}.schema={value.get('schema')!r}，预期 {LAYOUT_RECEIPT_SCHEMA!r}")
    if type(value.get("schema_version")) is not int or value.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        raise TensorShapeAttrError(
            f"{where}.schema_version 须为整数 {RECEIPT_SCHEMA_VERSION}，"
            f"得 {value.get('schema_version')!r}")
    for key in ("rank", "numel", "minimum_base_storage_numel"):
        _plain_int(value[key], f"{where}.{key}", minimum=0)
    if value["structural_kind"] not in {SHAPE_RANK0, SHAPE_EMPTY, SHAPE_NONEMPTY}:
        raise TensorShapeAttrError(
            f"{where}.structural_kind={value['structural_kind']!r} 非受控值")
    declaration = {key: value[key] for key in _LAYOUT_DECLARATION_KEYS}
    expected = make_layout_receipt(
        declaration, expected_shape=expected_shape, expected_role=expected_role,
        expected_case_id=expected_case_id,
        expected_tensor_name=expected_tensor_name,
        expected_tensor_index=expected_tensor_index,
        where=f"{where}.recompute")
    if not _strict_equal(value, expected, where):
        drift = [key for key in sorted(_LAYOUT_RECEIPT_KEYS)
                 if not _strict_equal(value.get(key), expected.get(key), f"{where}.{key}")]
        raise TensorShapeAttrError(
            f"{where} 派生字段/布局字段漂移：{drift}；收据必须由声明重算，不能自报")
    return expected


def assert_layout_preserved(expected_receipt, observed_layout, *, expected_shape,
                            expected_role, expected_case_id, expected_tensor_name,
                            expected_tensor_index, expected_receipt_sha256,
                            where="layout"):
    """按外部冻结身份/摘要比较运行时布局；整份自洽伪造也不能自证。"""
    expected_value = _object(expected_receipt, f"{where}.expected")
    wanted_sha = _sha256(expected_receipt_sha256, f"{where}.expected_receipt_sha256")
    actual_sha = layout_receipt_sha256(expected_value, where=f"{where}.expected")
    if actual_sha != wanted_sha:
        raise TensorShapeAttrError(
            f"{where}.expected receipt 摘要漂移：actual={actual_sha} expected={wanted_sha}")
    expected = validate_layout_receipt(
        expected_value, expected_shape=expected_shape, expected_role=expected_role,
        expected_case_id=expected_case_id,
        expected_tensor_name=expected_tensor_name,
        expected_tensor_index=expected_tensor_index,
        where=f"{where}.expected")
    observed_value = _object(observed_layout, f"{where}.observed")
    if set(observed_value) == set(_LAYOUT_RECEIPT_KEYS):
        observed = validate_layout_receipt(
            observed_value, expected_shape=expected_shape, expected_role=expected_role,
            expected_case_id=expected_case_id,
            expected_tensor_name=expected_tensor_name,
            expected_tensor_index=expected_tensor_index,
            where=f"{where}.observed")
    else:
        observed = make_layout_receipt(
            observed_value, expected_shape=expected_shape, expected_role=expected_role,
            expected_case_id=expected_case_id,
            expected_tensor_name=expected_tensor_name,
            expected_tensor_index=expected_tensor_index,
            where=f"{where}.observed")
    physical_keys = (
        "case_id", "tensor_name", "tensor_index", "role", "format", "logical_shape",
        "layout_kind", "layout_capability", "strides",
        "storage_offset", "base_storage_numel",
    )
    drift = {key: {"expected": expected[key], "observed": observed[key]}
             for key in physical_keys
             if not _strict_equal(expected[key], observed[key], f"{where}.{key}")}
    if drift:
        raise TensorShapeAttrError(
            f"{where} 物理布局未保持：{drift}；不得静默 contiguous/reshape/换 base storage")
    return observed
