"""逐 dtype 的任务书主表、保持集与回归扩展契约。

本模块只校 JSON 数据并产生内容摘要，不 import numpy/torch，也不按算子身份分支。
它把三种经常被混成一张 dtype 并集的事实分开：

* ``main_table_required``：任务书参数主表直接列出的需求；
* ``preservation_required``：任务书要求保持、再由受锚来源枚举出的既有类型；
* ``regression_extension``：保持集里未出现在主表、仍须单独回归/挂账的差集。

每个 dtype 的每个集合身份都必须有自己的来源证据。消费方应把 :func:`resolve`
返回的 receipt 与摘要原样贯穿 caseset/runner/三级门；本模块不把“有来源”误判成“已实测”。
"""

from __future__ import annotations

import hashlib
import json
import re


class DtypeRequirementSetsError(ValueError):
    """dtype requirement sets 结构或来源绑定不合法。"""


SCHEMA = "oprunway.dtype_requirement_sets"
SCHEMA_VERSION = 1
MAIN_TABLE_REQUIRED = "main_table_required"
PRESERVATION_REQUIRED = "preservation_required"
REGRESSION_EXTENSION = "regression_extension"
SET_NAMES = (
    MAIN_TABLE_REQUIRED,
    PRESERVATION_REQUIRED,
    REGRESSION_EXTENSION,
)
SOURCE_KINDS = frozenset({"taskdoc", "source_reference", "user"})

_RAW_KEYS = frozenset({"schema", "schema_version", *SET_NAMES, "members"})
_MEMBER_KEYS = frozenset({"dtype", "memberships", "evidence"})
_EVIDENCE_KEYS = frozenset({
    "membership", "source_kind", "source_sha256", "cite", "quote",
    "interpretation",
})
_SHA_CHARS = frozenset("0123456789abcdef")
_DTYPE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _object(value, where):
    if not isinstance(value, dict):
        raise DtypeRequirementSetsError(
            f"{where} 须为 object，得 {type(value).__name__}")
    if any(not isinstance(key, str) for key in value):
        raise DtypeRequirementSetsError(f"{where} 的键须全部为字符串")
    return value


def _exact_keys(value, expected, where):
    got = set(value)
    if got != set(expected):
        raise DtypeRequirementSetsError(
            f"{where} 键集合须恰为 {sorted(expected)}，"
            f"缺少={sorted(set(expected) - got)}，多出={sorted(got - set(expected))}")


def _text(value, where):
    if not isinstance(value, str) or not value.strip():
        raise DtypeRequirementSetsError(f"{where} 须为非空字符串")
    return value


def _sha(value, where):
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in _SHA_CHARS for char in value)):
        raise DtypeRequirementSetsError(f"{where} 须为 64 位小写 sha256")
    return value


def _dtype(value, where):
    if not isinstance(value, str) or not _DTYPE_RE.fullmatch(value):
        raise DtypeRequirementSetsError(
            f"{where}={value!r} 须为规范小写 dtype 标识")
    return value


def _canonical_sha(value):
    try:
        raw = json.dumps(
            value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as ex:
        raise DtypeRequirementSetsError(f"契约不是 canonical JSON：{ex}") from ex
    return hashlib.sha256(raw).hexdigest()


def _dtype_set(value, where):
    if not isinstance(value, list):
        raise DtypeRequirementSetsError(f"{where} 须为 dtype 列表")
    normalized = [_dtype(item, f"{where}[{idx}]")
                  for idx, item in enumerate(value)]
    if len(normalized) != len(set(normalized)):
        raise DtypeRequirementSetsError(f"{where} 含重复 dtype {normalized}")
    if normalized != sorted(normalized):
        raise DtypeRequirementSetsError(
            f"{where} 须按 dtype 字典序排序；集合顺序不得成为未记账轴")
    return normalized


def _membership_list(value, where):
    if not isinstance(value, list) or not value:
        raise DtypeRequirementSetsError(f"{where} 须为非空集合身份列表")
    unknown = [name for name in value if name not in SET_NAMES]
    if unknown:
        raise DtypeRequirementSetsError(
            f"{where} 含未知集合身份 {unknown}，受控词表={list(SET_NAMES)}")
    if len(value) != len(set(value)):
        raise DtypeRequirementSetsError(f"{where} 含重复集合身份 {value}")
    canonical = [name for name in SET_NAMES if name in value]
    if value != canonical:
        raise DtypeRequirementSetsError(
            f"{where} 顺序须遵循 {list(SET_NAMES)}")
    return list(value)


def _evidence(value, memberships, where):
    item = _object(value, where)
    _exact_keys(item, _EVIDENCE_KEYS, where)
    membership = item["membership"]
    if membership not in memberships:
        raise DtypeRequirementSetsError(
            f"{where}.membership={membership!r} 未绑定该 dtype 的 memberships={memberships}")
    kind = item["source_kind"]
    if kind not in SOURCE_KINDS:
        raise DtypeRequirementSetsError(
            f"{where}.source_kind={kind!r} 不在受控词表 {sorted(SOURCE_KINDS)}")
    return {
        "membership": membership,
        "source_kind": kind,
        "source_sha256": _sha(item["source_sha256"], f"{where}.source_sha256"),
        "cite": _text(item["cite"], f"{where}.cite"),
        "quote": _text(item["quote"], f"{where}.quote"),
        "interpretation": _text(item["interpretation"], f"{where}.interpretation"),
    }


def resolve(contract):
    """校验 raw contract，返回带 required union 与内容摘要的 canonical receipt。"""
    raw = _object(contract, "dtype_requirement_sets")
    _exact_keys(raw, _RAW_KEYS, "dtype_requirement_sets")
    if raw.get("schema") != SCHEMA:
        raise DtypeRequirementSetsError(
            f"dtype_requirement_sets.schema 必须为 {SCHEMA!r}")
    if type(raw.get("schema_version")) is not int or raw["schema_version"] != SCHEMA_VERSION:
        raise DtypeRequirementSetsError(
            f"dtype_requirement_sets.schema_version 须为整数 {SCHEMA_VERSION}")
    declared = {
        name: _dtype_set(raw[name], f"dtype_requirement_sets.{name}")
        for name in SET_NAMES
    }
    if not declared[MAIN_TABLE_REQUIRED]:
        raise DtypeRequirementSetsError("main_table_required 不得为空")
    if not declared[PRESERVATION_REQUIRED]:
        raise DtypeRequirementSetsError("preservation_required 不得为空")

    members_raw = raw["members"]
    if not isinstance(members_raw, list) or not members_raw:
        raise DtypeRequirementSetsError("dtype_requirement_sets.members 须为非空列表")
    members = []
    seen = set()
    projection = {name: [] for name in SET_NAMES}
    for idx, value in enumerate(members_raw):
        where = f"dtype_requirement_sets.members[{idx}]"
        item = _object(value, where)
        _exact_keys(item, _MEMBER_KEYS, where)
        dtype = _dtype(item["dtype"], f"{where}.dtype")
        if dtype in seen:
            raise DtypeRequirementSetsError(f"{where}.dtype={dtype!r} 重复")
        seen.add(dtype)
        memberships = _membership_list(item["memberships"], f"{where}.memberships")
        evidence_raw = item["evidence"]
        if not isinstance(evidence_raw, list) or not evidence_raw:
            raise DtypeRequirementSetsError(f"{where}.evidence 须为非空列表")
        evidence = [
            _evidence(entry, memberships, f"{where}.evidence[{eidx}]")
            for eidx, entry in enumerate(evidence_raw)
        ]
        for membership in memberships:
            if not any(entry["membership"] == membership for entry in evidence):
                raise DtypeRequirementSetsError(
                    f"{where}.evidence 缺 membership={membership!r} 的独立来源")
            projection[membership].append(dtype)
        members.append({
            "dtype": dtype,
            "memberships": memberships,
            "evidence": evidence,
        })
    member_order = [item["dtype"] for item in members]
    if member_order != sorted(member_order):
        raise DtypeRequirementSetsError(
            "dtype_requirement_sets.members 须按 dtype 字典序排序")
    for name in SET_NAMES:
        if projection[name] != declared[name]:
            raise DtypeRequirementSetsError(
                f"dtype_requirement_sets.{name}={declared[name]} 与逐 dtype members 投影 "
                f"{projection[name]} 不一致")
    required_regression = sorted(
        set(declared[PRESERVATION_REQUIRED]) - set(declared[MAIN_TABLE_REQUIRED]))
    if declared[REGRESSION_EXTENSION] != required_regression:
        raise DtypeRequirementSetsError(
            "regression_extension 必须逐字等于 preservation_required - "
            f"main_table_required={required_regression}，实际={declared[REGRESSION_EXTENSION]}")

    body = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        **declared,
        "members": members,
        "required_dtypes": sorted(
            set(declared[MAIN_TABLE_REQUIRED])
            | set(declared[PRESERVATION_REQUIRED])),
    }
    return {**body, "sha256": _canonical_sha(body)}


def validate_receipt(receipt, *, expected_sha256):
    """重建 receipt 并与外部冻结摘要对账，防整套来源同步改写后自证。"""
    value = _object(receipt, "dtype_requirement_sets_receipt")
    expected_keys = set(_RAW_KEYS) | {"required_dtypes", "sha256"}
    _exact_keys(value, expected_keys, "dtype_requirement_sets_receipt")
    raw = {key: value[key] for key in _RAW_KEYS}
    rebuilt = resolve(raw)
    if value != rebuilt:
        raise DtypeRequirementSetsError(
            "dtype_requirement_sets_receipt 与确定性重算不一致")
    wanted = _sha(expected_sha256, "expected_sha256")
    if rebuilt["sha256"] != wanted:
        raise DtypeRequirementSetsError(
            f"dtype requirement sets 摘要 {rebuilt['sha256']} 与外部冻结摘要 "
            f"{wanted} 不一致")
    return rebuilt


def from_spec(spec):
    """读取可选 ``spec.dtype_requirement_sets``；缺席保持 legacy 通路。"""
    value = _object(spec, "spec")
    if "dtype_requirement_sets" not in value:
        return None
    return resolve(value["dtype_requirement_sets"])


def required_dtypes(contract):
    """返回主表∪保持集的规范全集。"""
    return list(resolve(contract)["required_dtypes"])
