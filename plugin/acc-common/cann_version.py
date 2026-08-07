#!/usr/bin/env python3
"""CANN **runtime** 版本契约：规范化、收据自洽校验与最低版本比较。

本模块只用 Python 标准库，不探环境、不读取 ``version.info``，也不负责调用
AscendCL。driver 把当前进程实际调用 ACL API 得到的原始字符串落成 observation；
adapter 与三级门再通过本模块复算。这里证明的始终只是 **runtime CANN**，不把它
包装成 vendor ELF 的 build-time CANN 版本。
"""

from __future__ import annotations

import re


class CannVersionError(ValueError):
    pass


RECEIPT_SCHEMA_VERSION = 2

REQUIREMENT_MINIMUM = "minimum"
REQUIREMENT_NOT_DECLARED = "not_declared"
REQUIREMENT_KINDS = (REQUIREMENT_MINIMUM, REQUIREMENT_NOT_DECLARED)

OBS_MEASURED = "measured"
OBS_UNKNOWN = "unknown"
OBS_INVALID = "invalid"
OBS_STATUSES = (OBS_MEASURED, OBS_UNKNOWN, OBS_INVALID)

EVAL_SATISFIED = "satisfied"
EVAL_NOT_DECLARED = "not_declared"
EVAL_UNKNOWN = "unknown"
EVAL_INVALID = "invalid"
EVAL_BELOW_MINIMUM = "below_minimum"
EVAL_AMBIGUOUS_SUFFIX = "ambiguous_suffix_at_minimum"

PROBE_API = "aclsysGetCANNVersion"
PROBE_PACKAGE = "ACL_PKG_NAME_CANN"
PROBE_RETURN_MEASURED = "measured"
PROBE_NOT_CALLED = "not_called"

_CANONICAL = re.compile(
    r"(?P<major>0|[1-9][0-9]*)\."
    r"(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)")
_OBSERVED = re.compile(
    r"v?(?P<major>0|[1-9][0-9]*)\."
    r"(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)"
    r"(?P<suffix>[-+._][0-9A-Za-z][0-9A-Za-z._+-]*)?")
_HEX64 = re.compile(r"[0-9a-f]{64}")


def _core(match):
    return tuple(int(match.group(name)) for name in ("major", "minor", "patch"))


def _canonical(core):
    return ".".join(str(part) for part in core)


def normalize_requirement(value):
    """校验并规范化 ``runtime_requirements.cann``。

    ``minimum`` 必须引用任务书快照且最低版本只收 canonical ``X.Y.Z``；
    ``not_declared`` 不允许夹带 minimum 或引用字段，避免把“无要求”写成半份要求。
    """
    if not isinstance(value, dict):
        raise CannVersionError("runtime_requirements.cann 须为 object")
    kind = value.get("kind")
    if kind not in REQUIREMENT_KINDS:
        raise CannVersionError(
            f"runtime_requirements.cann.kind={kind!r} 非受控值，须属 {list(REQUIREMENT_KINDS)}")
    if kind == REQUIREMENT_NOT_DECLARED:
        if set(value) != {"kind"}:
            raise CannVersionError(
                "CANN 版本未声明时只能写 {\"kind\":\"not_declared\"}，不得夹带最低版本/引用")
        return {"kind": kind}

    allowed = {"kind", "minimum_version", "cite", "quote", "taskdoc_snapshot_sha256"}
    extra = sorted(set(value) - allowed)
    if extra:
        raise CannVersionError(f"runtime_requirements.cann 含未知字段 {extra}")
    raw = value.get("minimum_version")
    if not isinstance(raw, str) or _CANONICAL.fullmatch(raw) is None:
        raise CannVersionError(
            f"CANN minimum_version={raw!r} 非 canonical X.Y.Z（不收 v 前缀、suffix 或前导零）")
    for key in ("cite", "quote"):
        item = value.get(key)
        if not isinstance(item, str) or not item.strip():
            raise CannVersionError(f"CANN minimum 要求缺非空 {key}")
    digest = value.get("taskdoc_snapshot_sha256")
    if not isinstance(digest, str) or _HEX64.fullmatch(digest) is None:
        raise CannVersionError("CANN minimum 要求的 taskdoc_snapshot_sha256 须为 64 位小写 sha256")
    return {
        "kind": kind,
        "minimum_version": raw,
        "cite": value["cite"],
        "quote": value["quote"],
        "taskdoc_snapshot_sha256": digest,
    }


def normalize_observation(raw):
    """把 ACL API 原始版本串规范化；全串不匹配即显式 ``invalid``。"""
    if not isinstance(raw, str) or not raw:
        return {
            "status": OBS_INVALID,
            "raw": raw,
            "normalized": None,
            "core": None,
            "suffix": None,
        }
    match = _OBSERVED.fullmatch(raw)
    if match is None:
        return {
            "status": OBS_INVALID,
            "raw": raw,
            "normalized": None,
            "core": None,
            "suffix": None,
        }
    core = _core(match)
    return {
        "status": OBS_MEASURED,
        "raw": raw,
        "normalized": _canonical(core),
        "core": list(core),
        "suffix": match.group("suffix"),
    }


def unknown_observation(probe, error):
    if not isinstance(error, str) or not error.strip():
        raise CannVersionError("unknown CANN observation 必须记录非空 error")
    return {
        "status": OBS_UNKNOWN,
        "raw": None,
        "normalized": None,
        "core": None,
        "suffix": None,
        "probe": probe,
        "error": error,
    }


def _validate_probe(probe, measured):
    if not isinstance(probe, dict):
        raise CannVersionError("CANN observation.probe 须为 object")
    if probe.get("api") != PROBE_API or probe.get("package") != PROBE_PACKAGE:
        raise CannVersionError(
            f"CANN probe 必须是 {PROBE_API}({PROBE_PACKAGE})")
    rc = probe.get("returncode")
    source = probe.get("returncode_source")
    if rc is None:
        if source != PROBE_NOT_CALLED:
            raise CannVersionError("CANN probe 未调用时 returncode_source 须为 not_called")
    elif (not isinstance(rc, int) or isinstance(rc, bool)
          or source != PROBE_RETURN_MEASURED):
        raise CannVersionError("CANN probe returncode 须为 API 实测整数并标 measured")
    elf = probe.get("defining_elf")
    if measured:
        if rc != 0:
            raise CannVersionError("measured CANN observation 的 probe returncode 必须为 0")
        if (not isinstance(elf, dict)
                or not isinstance(elf.get("path"), str) or not elf["path"].startswith("/")
                or not isinstance(elf.get("sha256"), str)
                or _HEX64.fullmatch(elf["sha256"]) is None):
            raise CannVersionError("measured CANN observation 缺绝对 defining ELF 路径/sha256")
    elif elf is not None:
        if (not isinstance(elf, dict)
                or not isinstance(elf.get("path"), str) or not elf["path"].startswith("/")
                or not isinstance(elf.get("sha256"), str)
                or _HEX64.fullmatch(elf["sha256"]) is None):
            raise CannVersionError("CANN probe.defining_elf 在场却不完整")


def validate_observation_record(value):
    """复算 observation 的 raw→normalized 映射并校 probe/ELF 元数据形态。"""
    if not isinstance(value, dict):
        raise CannVersionError("runtime.cann 须为 object")
    status = value.get("status")
    if status not in OBS_STATUSES:
        raise CannVersionError(
            f"runtime.cann.status={status!r} 非受控值，须属 {list(OBS_STATUSES)}")
    _validate_probe(value.get("probe"), status == OBS_MEASURED)
    if status == OBS_UNKNOWN:
        if any(value.get(key) is not None for key in ("raw", "normalized", "core", "suffix")):
            raise CannVersionError("unknown CANN observation 不得自报版本值")
        if not isinstance(value.get("error"), str) or not value["error"].strip():
            raise CannVersionError("unknown CANN observation 缺非空 error")
        return value
    recomputed = normalize_observation(value.get("raw"))
    for key in ("status", "raw", "normalized", "core", "suffix"):
        if value.get(key) != recomputed.get(key):
            raise CannVersionError(
                f"runtime.cann.{key} 与 raw 重新规范化结果不一致："
                f"记录 {value.get(key)!r}，重算 {recomputed.get(key)!r}")
    if status == OBS_INVALID:
        if not isinstance(value.get("error"), str) or not value["error"].strip():
            raise CannVersionError("invalid CANN observation 缺非空 error")
    elif "error" in value:
        raise CannVersionError("measured CANN observation 不得夹带 error")
    return value


def evaluate(requirement, observation):
    """比较任务书最低版本与 runtime 实测；返回机读结果，不自行写验收裁决。"""
    req = normalize_requirement(requirement)
    obs = validate_observation_record(observation)
    base = {
        "scope": "runtime_only",
        "required": req.get("minimum_version"),
        "measured": obs.get("normalized"),
        "raw": obs.get("raw"),
        "suffix": obs.get("suffix"),
    }
    if req["kind"] == REQUIREMENT_NOT_DECLARED:
        return {**base, "status": EVAL_NOT_DECLARED, "satisfied": None}
    if obs["status"] == OBS_UNKNOWN:
        return {**base, "status": EVAL_UNKNOWN, "satisfied": False}
    if obs["status"] == OBS_INVALID:
        return {**base, "status": EVAL_INVALID, "satisfied": False}
    required_core = _core(_CANONICAL.fullmatch(req["minimum_version"]))
    measured_core = tuple(obs["core"])
    if measured_core < required_core:
        return {**base, "status": EVAL_BELOW_MINIMUM, "satisfied": False}
    if measured_core == required_core and obs.get("suffix") is not None:
        return {**base, "status": EVAL_AMBIGUOUS_SUFFIX, "satisfied": False}
    return {**base, "status": EVAL_SATISFIED, "satisfied": True}
