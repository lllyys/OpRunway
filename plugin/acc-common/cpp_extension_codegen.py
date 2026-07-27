#!/usr/bin/env python3
"""字段驱动生成官方 torch_npu C++ Extension 适配层。

本模块只生成源码，不 build、不 import torch、不访问 NPU。生成形态严格取自 Ascend/op-plugin
`examples/cpp_extension_base`：

* ``torch_npu.utils.cpp_extension.NpuExtension`` + ``BuildExtension``；
* ``#include "npu_cpp_extension.h"``；
* ``EXEC_NPU_CMD_EXT(aclnnXxx, ...)``；
* ``TORCH_LIBRARY`` / ``TORCH_LIBRARY_IMPL(..., PrivateUse1, ...)``；
* Python 侧由 ``torch.ops.load_library`` 加载独立共享库。

不复制旧 ``pytorch_npu_helper.hpp``，不含算子身份分派。符号、参数顺序、属性类型与输出活动集全部
来自 spec 的 ``params`` / ``call_variants``；域外类型 fail-closed。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path


class CppExtensionCodegenError(ValueError):
    pass


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ATTR_CPP_TYPES = {
    "bool": "bool",
    "int64": "int64_t",
    "int32": "int64_t",  # Torch schema 的 int 对应 C++ int64_t；ACLNN 转换层再按签名处理。
    "float32": "double",
    "float64": "double",
}
_ATTR_SCHEMA_TYPES = {
    "bool": "bool",
    "int64": "int",
    "int32": "int",
    "float32": "float",
    "float64": "float",
}


def _ident(value, where):
    if not isinstance(value, str) or not _IDENT_RE.fullmatch(value):
        raise CppExtensionCodegenError(f"{where}={value!r} 不是合法 C/C++ 标识符")
    return value


def _load(path):
    with open(path, encoding="utf-8") as fh:
        value = json.load(fh)
    if not isinstance(value, dict):
        raise CppExtensionCodegenError("spec 须为 JSON object")
    return value


def _canonical_digest(spec):
    raw = json.dumps(spec, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _text_sha256(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _attr_type(param):
    dtypes = param.get("dtype")
    if not isinstance(dtypes, list) or len(dtypes) != 1 or dtypes[0] not in _ATTR_CPP_TYPES:
        raise CppExtensionCodegenError(
            f"attr {param.get('name')!r} dtype 须为单值 {sorted(_ATTR_CPP_TYPES)}，得 {dtypes!r}")
    return _ATTR_CPP_TYPES[dtypes[0]], _ATTR_SCHEMA_TYPES[dtypes[0]]


def _contract(spec):
    if spec.get("runner_form") != "cpp_extension":
        raise CppExtensionCodegenError(
            f"cpp_extension_codegen 只接受 runner_form='cpp_extension'，得 {spec.get('runner_form')!r}")
    params = spec.get("params")
    variants = spec.get("call_variants")
    if not isinstance(params, list) or not params:
        raise CppExtensionCodegenError("spec.params 须为非空列表")
    if not isinstance(variants, list) or not variants:
        raise CppExtensionCodegenError("runner_form=cpp_extension 时 call_variants 须为非空列表")

    seen = set()
    rows = []
    for i, p in enumerate(params):
        if not isinstance(p, dict):
            raise CppExtensionCodegenError(f"params[{i}] 须为 object")
        name = _ident(p.get("name"), f"params[{i}].name")
        if name in seen:
            raise CppExtensionCodegenError(f"params 含重名 {name!r}")
        seen.add(name)
        io = p.get("io")
        if io not in ("in", "attr", "out"):
            raise CppExtensionCodegenError(f"params[{i}].io={io!r} 不在 in/attr/out")
        row = {"name": name, "io": io}
        if io == "attr":
            row["cpp_type"], row["schema_type"] = _attr_type(p)
        rows.append(row)

    in_names = [p["name"] for p in rows if p["io"] == "in"]
    out_names = [p["name"] for p in rows if p["io"] == "out"]
    if not in_names or not out_names:
        raise CppExtensionCodegenError("cpp_extension 至少须有一个 in 和一个 out")

    normalized = []
    for i, v in enumerate(variants):
        if not isinstance(v, dict):
            raise CppExtensionCodegenError(f"call_variants[{i}] 须为 object")
        symbol = _ident(v.get("symbol"), f"call_variants[{i}].symbol")
        if symbol.startswith("aclnn"):
            raise CppExtensionCodegenError(
                f"call_variants[{i}].symbol 须为不带 aclnn 前缀的基名，得 {symbol!r}")
        active = v.get("active_outputs")
        active_attrs = v.get("active_attrs")
        if not isinstance(active, list) or not active:
            raise CppExtensionCodegenError(f"call_variants[{i}].active_outputs 须为非空列表")
        attr_names = [p["name"] for p in rows if p["io"] == "attr"]
        if not isinstance(active_attrs, list):
            raise CppExtensionCodegenError(
                f"call_variants[{i}].active_attrs 须为列表；扩展 ABI 不猜测属性槽")
        if (len(active_attrs) != len(set(active_attrs))
                or any(x not in attr_names for x in active_attrs)):
            raise CppExtensionCodegenError(
                f"call_variants[{i}].active_attrs={active_attrs!r} "
                f"须为 attr 名集 {attr_names!r} 的无重复子集")
        if len(active) != len(set(active)) or any(x not in out_names for x in active):
            raise CppExtensionCodegenError(
                f"call_variants[{i}].active_outputs={active!r} 须为 out 名集 {out_names!r} 的无重复子集")
        normalized.append({
            "index": i,
            "symbol": symbol,
            "active_attrs": list(active_attrs),
            "active_outputs": list(active),
            "entrypoint": f"invoke_v{i}",
        })
    return rows, normalized


def _cpp_arg(param, active):
    name, io = param["name"], param["io"]
    if io == "in":
        return f"const at::Tensor& {name}"
    if io == "attr":
        return f"{param['cpp_type']} {name}"
    if name in active:
        return f"const at::Tensor& {name}"
    return f"const c10::optional<at::Tensor>& {name}"


def _schema_arg(param, active):
    name, io = param["name"], param["io"]
    if io == "in":
        return f"Tensor {name}"
    if io == "attr":
        return f"{param['schema_type']} {name}"
    if name in active:
        return f"Tensor {name}"
    # 不给 optional 参数写 schema default：若后面还有 active output，Torch schema 不允许
    # “带默认值参数在无默认值参数之前”。调用端始终按 spec.params 顺序显式传 None。
    return f"Tensor? {name}"


def _variant_params(params, variant):
    active_attrs = set(variant["active_attrs"])
    return [p for p in params if p["io"] != "attr" or p["name"] in active_attrs]


def _render_cpp(namespace, params, variants):
    functions = []
    schemas = []
    impls = []
    first_input = next(p["name"] for p in params if p["io"] == "in")
    for v in variants:
        variant_params = _variant_params(params, v)
        active = set(v["active_outputs"])
        args = ", ".join(_cpp_arg(p, active) for p in variant_params)
        call_args = ", ".join(p["name"] for p in variant_params)
        returned = ", ".join(v["active_outputs"])
        functions.append(
            f"""std::vector<at::Tensor> {v['entrypoint']}({args})
{{
    const c10::OptionalDeviceGuard device_guard(device_of({first_input}));
    EXEC_NPU_CMD_EXT(aclnn{v['symbol']}, {call_args});
    return {{{returned}}};
}}""")
        schema_args = ", ".join(_schema_arg(p, active) for p in variant_params)
        schemas.append(f'    m.def("{v["entrypoint"]}({schema_args}) -> Tensor[]");')
        impls.append(f'    m.impl("{v["entrypoint"]}", &{v["entrypoint"]});')
    return f"""// Generated by OpRunway. Do not hand-edit.
#include <torch/library.h>
#include <torch/extension.h>
#include "npu_cpp_extension.h"

{os.linesep.join(functions)}

TORCH_LIBRARY({namespace}, m)
{{
{os.linesep.join(schemas)}
}}

TORCH_LIBRARY_IMPL({namespace}, PrivateUse1, m)
{{
{os.linesep.join(impls)}
}}
"""


def _render_setup(module_name):
    return f"""# Generated by OpRunway. Do not hand-edit.
import glob
import os

import torch_npu
from setuptools import setup
from torch.utils.cpp_extension import BuildExtension
from torch_npu.utils.cpp_extension import NpuExtension

ROOT = os.path.dirname(os.path.realpath(__file__))
TORCH_NPU_ROOT = os.path.dirname(os.path.abspath(torch_npu.__file__))

setup(
    name={module_name!r},
    version="1.0",
    ext_modules=[
        NpuExtension(
            name={module_name!r},
            sources=glob.glob(os.path.join(ROOT, "csrc", "*.cpp")),
            extra_compile_args=[
                "-I" + os.path.join(TORCH_NPU_ROOT, "include", "third_party", "acl", "inc"),
                "-I" + os.path.join(TORCH_NPU_ROOT, "include", "third_party", "op-plugin"),
                "-I" + os.path.join(
                    TORCH_NPU_ROOT, "include", "third_party", "op-plugin", "op_plugin", "include"),
            ],
        )
    ],
    cmdclass={{"build_ext": BuildExtension.with_options(use_ninja=False)}},
)
"""


def generate(spec, out_dir):
    params, variants = _contract(spec)
    digest = _canonical_digest(spec)
    namespace = f"oprunway_{digest[:16]}"
    module_name = f"{namespace}_lib"
    out = Path(out_dir)
    csrc = out / "csrc"
    csrc.mkdir(parents=True, exist_ok=True)

    cpp = _render_cpp(namespace, params, variants)
    setup = _render_setup(module_name)
    (csrc / "oprunway_extension.cpp").write_text(cpp, encoding="utf-8")
    (out / "setup.py").write_text(setup, encoding="utf-8")

    manifest = {
        "schema_version": 1,
        "runner_form": "cpp_extension",
        "spec_sha256": digest,
        "namespace": namespace,
        "module_name": module_name,
        "official_pattern": {
            "source": "Ascend/op-plugin examples/cpp_extension_base",
            "ascend_pytorch_master_commit": "c255c0003f1ddff0e34190e417dc29b1c6f566a3",
            "op_plugin_gitlink_commit": "ab6984979cb97ab9a2e48b19332e313203ea7c3c",
            "header": "npu_cpp_extension.h",
            "macro": "EXEC_NPU_CMD_EXT",
            "builder": "torch_npu.utils.cpp_extension.NpuExtension",
            "loader": "torch.ops.load_library",
        },
        "variants": variants,
        "files": {
            "cpp": {
                "path": "csrc/oprunway_extension.cpp",
                "sha256": _text_sha256(cpp),
            },
            "setup": {
                "path": "setup.py",
                "sha256": _text_sha256(setup),
            },
        },
    }
    (out / "extension_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description="生成官方 torch_npu C++ Extension 适配层（不 build）")
    ap.add_argument("spec")
    ap.add_argument("--out", required=True)
    ns = ap.parse_args(argv)
    print(json.dumps(generate(_load(ns.spec), ns.out), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
