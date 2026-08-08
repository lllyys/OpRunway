#!/usr/bin/env python3
"""Current ``source_facts`` ↔ vendor build receipt 内容锚的唯一交叉校验。

本模块只校冻结 JSON 中的内容身份，不读真机 source root/ELF：因此既可供
三级门使用，也可供产物搬移后的 standalone renderer 使用。
"""

import source_provenance
import vendor_build_receipt


class SourceBuildBindingError(ValueError):
    """Current source/build 内容身份不闭合。"""


def validate_current(facts, build_receipt, *, summary=None):
    """严格校验 current caller-trusted facts 与 v3 build receipt，返回归一化摘要。"""
    if not isinstance(facts, dict):
        raise SourceBuildBindingError(
            "current 绑定缺可信 source_facts，无法与 vendor build receipt 对账")
    if (not isinstance(build_receipt, dict)
            or build_receipt.get("schema") != vendor_build_receipt.SCHEMA
            or build_receipt.get("schema_version") != vendor_build_receipt.SCHEMA_VERSION
            or build_receipt.get("status") != "VERIFIED"):
        raise SourceBuildBindingError(
            "current source/build 绑定只接受 VERIFIED vendor build receipt v3")
    try:
        source_provenance.caller_trusted_association(facts, allow_legacy=False)
        normalized = (vendor_build_receipt.summarize(build_receipt)
                      if summary is None else summary)
        vendor_build_receipt.validate_current_snapshot_anchor_binding(
            build_receipt, summary=normalized)
    except (source_provenance.ProvenanceError,
            vendor_build_receipt.VendorBuildReceiptError) as ex:
        raise SourceBuildBindingError(str(ex)) from ex

    facts_pr = facts.get("pr")
    expected = facts_pr.get("content_anchor") if isinstance(facts_pr, dict) else None
    actual = normalized.get("content_anchor") if isinstance(normalized, dict) else None
    if not isinstance(expected, dict) or actual != expected:
        raise SourceBuildBindingError(
            "source_facts.pr.content_anchor 与 vendor build receipt "
            "source.content_anchor 未逐字一致")
    return normalized
