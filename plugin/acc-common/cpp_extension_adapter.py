#!/usr/bin/env python3
"""官方 torch_npu C++ Extension 的确定性准备、外部执行与收据验证。

本模块不内置 SSH、容器名或机器路径。真机编排器须通过
``OPRUNWAY_CPP_EXTENSION_DRIVER_JSON`` 提供 JSON argv；driver 接收
``--bundle`` 与 ``--work``，构建并加载独立 Extension、执行全量 caseset，
然后把输出和 ``cpp_extension_receipt.json`` 回传到 work。这里仅验证并组装
evidence，不判定 pass/fail。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import content_address
import cpp_extension_codegen


class CppExtensionAdapterError(RuntimeError):
    pass


_RECEIPT = "cpp_extension_receipt.json"
_PLAN = "cpp_extension_invocation_plan.json"
_BUNDLE = "cpp_extension"
_OUT = "cpp_extension_out"


def _canonical_sha(value):
    return hashlib.sha256(content_address.canonical_json_bytes(value)).hexdigest()


def _file_sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _strict_json(path):
    with open(path, encoding="utf-8") as src:
        value = json.load(
            src,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"非法 JSON 常量 {token}")))
    content_address.canonical_json_bytes(value)
    return value


def _safe(root, rel):
    if not isinstance(rel, str) or not rel or os.path.isabs(rel):
        raise CppExtensionAdapterError(f"相对路径非法: {rel!r}")
    root = os.path.realpath(root)
    path = os.path.realpath(os.path.join(root, rel))
    if path != root and not path.startswith(root + os.sep):
        raise CppExtensionAdapterError(f"路径逃出根目录: {rel!r}")
    return path


def _variant_map(manifest):
    result = {}
    for row in manifest.get("variants") or []:
        symbol = row.get("symbol")
        if not isinstance(symbol, str) or symbol in result:
            raise CppExtensionAdapterError(
                f"extension manifest variant symbol 缺失或重复: {symbol!r}")
        result[symbol] = row
    if not result:
        raise CppExtensionAdapterError("extension manifest 无 variants")
    return result


def build_invocation_plan(caseset, manifest):
    """把 caseset.aclnn_call 绑定到生成 Extension 的 entrypoint；不重推变体。"""
    variants = _variant_map(manifest)
    rows, seen = [], set()
    cases = caseset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise CppExtensionAdapterError("caseset.cases 须为非空列表")
    for case in cases:
        cid = case.get("id")
        if not isinstance(cid, str) or not cid or cid in seen:
            raise CppExtensionAdapterError(f"case id 缺失或重复: {cid!r}")
        seen.add(cid)
        call = case.get("aclnn_call")
        if not isinstance(call, dict):
            raise CppExtensionAdapterError(
                f"{cid}: cpp_extension case 缺 aclnn_call")
        symbol, slots = call.get("symbol"), call.get("slots")
        variant = variants.get(symbol)
        if variant is None:
            raise CppExtensionAdapterError(
                f"{cid}: aclnn_call.symbol={symbol!r} 未绑定生成 Extension variant")
        if not isinstance(slots, list) or not slots:
            raise CppExtensionAdapterError(f"{cid}: aclnn_call.slots 须为非空列表")
        active_outputs = []
        for index, slot in enumerate(slots):
            if not isinstance(slot, dict):
                raise CppExtensionAdapterError(f"{cid}: slots[{index}] 非 object")
            role, name = slot.get("role"), slot.get("name")
            if role not in ("in", "attr", "out", "out_null"):
                raise CppExtensionAdapterError(
                    f"{cid}: slots[{index}].role={role!r} 非受控词")
            if not isinstance(name, str) or not name:
                raise CppExtensionAdapterError(f"{cid}: slots[{index}].name 缺失")
            if role == "out":
                active_outputs.append(name)
        if active_outputs != variant.get("active_outputs"):
            raise CppExtensionAdapterError(
                f"{cid}: slots active outputs={active_outputs!r} 与 manifest "
                f"{variant.get('active_outputs')!r} 不同")
        rows.append({
            "case_id": cid,
            "symbol": symbol,
            "entrypoint": variant["entrypoint"],
            "slots": slots,
        })
    return {
        "schema": "oprunway.cpp_extension_invocation_plan",
        "schema_version": 1,
        "caseset_sha256": _canonical_sha(caseset),
        "manifest_sha256": _canonical_sha(manifest),
        "namespace": manifest["namespace"],
        "cases": rows,
    }


def prepare(spec, caseset, work):
    """生成 Extension bundle 与逐 case 调用计划；纯本地确定性准备，不 build。"""
    if spec.get("runner_form") != "cpp_extension":
        raise CppExtensionAdapterError("prepare 仅接受 runner_form=cpp_extension")
    work = os.path.abspath(work)
    bundle = os.path.join(work, _BUNDLE)
    manifest = cpp_extension_codegen.generate(spec, bundle)
    plan = build_invocation_plan(caseset, manifest)
    with open(os.path.join(work, _PLAN), "w", encoding="utf-8") as out:
        json.dump(plan, out, ensure_ascii=False, indent=2)
        out.write("\n")
    return manifest, plan


def _require_sha(label, value):
    if not isinstance(value, str) or len(value) != 64:
        raise CppExtensionAdapterError(f"{label} 须为 64 位 sha256")
    try:
        int(value, 16)
    except ValueError as ex:
        raise CppExtensionAdapterError(f"{label} 非十六进制 sha256") from ex


def validate_receipt(work, caseset):
    """验证外部 driver 回传的 build/load receipt 与当前输入、源码、ELF 精确绑定。"""
    work = os.path.abspath(work)
    bundle = os.path.join(work, _BUNDLE)
    manifest = _strict_json(os.path.join(bundle, "extension_manifest.json"))
    plan = _strict_json(os.path.join(work, _PLAN))
    receipt = _strict_json(os.path.join(work, _RECEIPT))
    if receipt.get("schema") != "oprunway.cpp_extension_receipt" \
            or receipt.get("schema_version") != 1 \
            or receipt.get("status") != "VERIFIED":
        raise CppExtensionAdapterError("cpp_extension receipt schema/status 非 VERIFIED v1")

    expected = {
        "caseset_sha256": _canonical_sha(caseset),
        "manifest_sha256": _canonical_sha(manifest),
        "invocation_plan_sha256": _canonical_sha(plan),
        "spec_sha256": manifest.get("spec_sha256"),
    }
    bindings = receipt.get("bindings")
    if not isinstance(bindings, dict):
        raise CppExtensionAdapterError("receipt.bindings 缺失")
    for key, value in expected.items():
        _require_sha(f"expected.{key}", value)
        if bindings.get(key) != value:
            raise CppExtensionAdapterError(
                f"receipt.bindings.{key} 漂移：期望 {value}，得 {bindings.get(key)!r}")

    for key, rec in (manifest.get("files") or {}).items():
        if not isinstance(rec, dict):
            raise CppExtensionAdapterError(f"manifest.files.{key} 非 object")
        path = _safe(bundle, rec.get("path"))
        if not os.path.isfile(path) or _file_sha(path) != rec.get("sha256"):
            raise CppExtensionAdapterError(f"生成源码 {key} 缺失或摘要漂移")

    runtime = receipt.get("runtime")
    required_runtime = ("torch_version", "torch_npu_version", "cann_version", "soc")
    if not isinstance(runtime, dict) or any(not runtime.get(k) for k in required_runtime):
        raise CppExtensionAdapterError(
            f"receipt.runtime 须完整包含 {required_runtime}")
    build = receipt.get("build")
    if not isinstance(build, dict) or not isinstance(build.get("argv"), list) \
            or not build["argv"] or build.get("returncode") != 0:
        raise CppExtensionAdapterError("receipt.build 须含成功的非空 argv")
    artifact = receipt.get("artifact")
    if not isinstance(artifact, dict):
        raise CppExtensionAdapterError("receipt.artifact 缺失")
    so_path = _safe(work, artifact.get("path"))
    _require_sha("receipt.artifact.sha256", artifact.get("sha256"))
    if not os.path.isfile(so_path) or _file_sha(so_path) != artifact["sha256"]:
        raise CppExtensionAdapterError("Extension ELF 缺失或摘要漂移")

    load = receipt.get("load")
    if not isinstance(load, dict) or load.get("success") is not True \
            or load.get("loader") != "torch.ops.load_library" \
            or load.get("namespace") != manifest.get("namespace"):
        raise CppExtensionAdapterError("Extension load receipt 不完整或 namespace/loader 漂移")
    schemas = load.get("schemas")
    wanted = {v["entrypoint"] for v in manifest["variants"]}
    if not isinstance(schemas, dict) or set(schemas) != wanted \
            or any(not isinstance(v, str) or not v for v in schemas.values()):
        raise CppExtensionAdapterError("Extension runtime schemas 与生成 entrypoints 不一致")
    vendor = receipt.get("vendor")
    if not isinstance(vendor, dict) or not vendor.get("library_path") \
            or not vendor.get("library_sha256") or not vendor.get("symbols_owned"):
        raise CppExtensionAdapterError("receipt.vendor 缺库路径/摘要/符号归属")
    _require_sha("receipt.vendor.library_sha256", vendor["library_sha256"])
    return receipt


def _driver_argv():
    raw = os.environ.get("OPRUNWAY_CPP_EXTENSION_DRIVER_JSON")
    if not raw:
        raise CppExtensionAdapterError(
            "缺 OPRUNWAY_CPP_EXTENSION_DRIVER_JSON；cpp_extension 不猜 SSH/container 入口")
    try:
        argv = json.loads(raw)
    except json.JSONDecodeError as ex:
        raise CppExtensionAdapterError("CPP Extension driver JSON 非法") from ex
    if not isinstance(argv, list) or not argv \
            or any(not isinstance(x, str) or not x for x in argv):
        raise CppExtensionAdapterError("CPP Extension driver 须为非空 JSON string argv")
    return argv


def run_cpp_extension(caseset, work, defect_cases=None):
    """执行显式外部 driver，验证 receipt 后复用确定性 evidence 组装。"""
    if defect_cases:
        raise CppExtensionAdapterError("cpp_extension 验收通路禁止 defect 注入")
    if os.environ.get("OPRUNWAY_CPP_EXTENSION_REAL") != "1":
        raise CppExtensionAdapterError(
            "真机路径未启用；须显式设 OPRUNWAY_CPP_EXTENSION_REAL=1")
    bundle = os.path.join(os.path.abspath(work), _BUNDLE)
    plan = os.path.join(os.path.abspath(work), _PLAN)
    if not os.path.isfile(os.path.join(bundle, "extension_manifest.json")) \
            or not os.path.isfile(plan):
        raise CppExtensionAdapterError("缺 prepare() 生成的 bundle/invocation plan")
    argv = _driver_argv() + ["--bundle", bundle, "--work", os.path.abspath(work)]
    result = subprocess.run(argv, check=False)
    if result.returncode != 0:
        raise CppExtensionAdapterError(
            f"CPP Extension 外部 driver 失败 rc={result.returncode}")
    receipt = validate_receipt(work, caseset)
    import repo_adapter as RA
    evidence = RA.build_multi_output_evidence(
        caseset, work, os.path.join(work, _OUT))
    digest = _canonical_sha(receipt)
    for row in evidence:
        row["cpp_extension_receipt_sha256"] = digest
    return {
        "op": caseset["op"],
        "repo_mode": "cpp_extension",
        "runner_form": "cpp_extension",
        "runner_source": "generated_official_cpp_extension",
        "runner_path": receipt["artifact"]["path"],
        "evidence_grade": "acceptance_candidate",
        "cpp_extension_receipt": receipt,
        "evidence": evidence,
    }


CPP_EXTENSION_MODES = {"cpp_extension": run_cpp_extension}
