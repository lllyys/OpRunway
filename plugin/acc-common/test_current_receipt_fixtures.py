"""测试专用的 current vendor receipt v3 fixture。

只复用生产模块公开契约常量与当前逻辑指纹；不得在产品代码加入 legacy 合成兼容。
"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import vendor_build_receipt as V
import target_kernel_delivery as TK
import kernel_identity as K


DEFAULT_SCOPE = "witness_op"
DEFAULT_WHOLE = "c" * 64
DEFAULT_SUBTREE = "d" * 64


def content_anchor(scope=DEFAULT_SCOPE, sha256=DEFAULT_SUBTREE, file_count=1):
    return {
        "schema": "oprunway.source_content_anchor",
        "schema_version": 1,
        "algorithm": "git_blob_manifest_sha256_v1",
        "scope": scope,
        "sha256": sha256,
        "file_count": file_count,
    }


def _fixture_roots(vendor_path):
    """从已校验的 ``.../vendors/<vendor>`` 布局派生同一临时根内的资产。"""

    installed = Path(V.custom_opp_path(vendor_path)).resolve()
    Path(vendor_path).resolve().relative_to(installed)
    vendors_root = installed.parent
    if vendors_root.name != "vendors":
        raise RuntimeError(
            "current target-kernel fixture requires .../vendors/<vendor> layout")
    vendor_relative = installed.relative_to(vendors_root)
    if len(vendor_relative.parts) != 1:
        raise RuntimeError("fixture installed OPP must be one vendor below vendors root")
    fixture_root = vendors_root.parent
    package = fixture_root / "package" / "vendors" / vendor_relative
    source = fixture_root / "fixture-source" / vendor_relative
    return installed, package, source


def target_kernel_delivery_fixture(
        *, vendor_path, source_root, scope, requested_soc="ascend910_93",
        selected_op="x", expected_op_type="X", kernel_symbol="X_fixture"):
    """物化真实 package/install/ELF 资产并由生产 builder 生成 current closure。"""

    installed_root, package_root, _ = _fixture_roots(vendor_path)
    installed_opp = str(installed_root)
    package_opp = str(package_root)
    build_cwd = os.path.join(str(source_root), scope)
    cache = os.path.join(build_cwd, "build", "CMakeCache.txt")
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    with open(cache, "w", encoding="utf-8") as out:
        out.write(
            f"ASCEND_COMPUTE_UNIT:STRING={requested_soc}\n"
            f"ASCEND_OP_NAME:STRING={selected_op}\n")

    config_rel = os.path.join(
        "op_impl", "ai_core", "tbe", "config", requested_soc,
        f"aic-{requested_soc}-ops-info.json")
    metadata_rel = os.path.join(
        "op_impl", "ai_core", "tbe", "kernel", requested_soc,
        selected_op, kernel_symbol + ".json")
    object_rel = os.path.join(
        "op_impl", "ai_core", "tbe", "kernel", requested_soc,
        selected_op, kernel_symbol + ".o")
    config = {expected_op_type: {"opFile": {"value": selected_op}}}
    metadata = {"binList": [{
        "binPath": kernel_symbol + ".o",
        "kernelList": [{"kernelName": kernel_symbol}],
    }]}
    for root in (installed_opp, package_opp):
        for rel, value in ((config_rel, config), (metadata_rel, metadata)):
            path = os.path.join(root, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as out:
                json.dump(value, out, ensure_ascii=False, sort_keys=True)
                out.write("\n")

    compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if compiler is None:
        raise RuntimeError("current target-kernel fixture requires a C compiler")
    installed_object = os.path.join(installed_opp, object_rel)
    os.makedirs(os.path.dirname(installed_object), exist_ok=True)
    run = subprocess.run(
        [compiler, "-x", "c", "-c", "-o", installed_object, "-"],
        input=f"void {kernel_symbol}(void) {{}}\n",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if run.returncode != 0:
        raise RuntimeError(
            f"target-kernel fixture compile failed rc={run.returncode}: {run.stderr}")
    package_object = os.path.join(package_opp, object_rel)
    os.makedirs(os.path.dirname(package_object), exist_ok=True)
    shutil.copyfile(installed_object, package_object)
    argv = [
        "bash", "build.sh", f"--soc={requested_soc}", f"--ops={selected_op}"]
    closure = V.build_target_kernel_delivery_closure(
        requested_soc=requested_soc,
        selected_op=selected_op,
        expected_op_type=expected_op_type,
        installed_opp_root=installed_opp,
        package_opp_root=package_opp,
        cmake_cache_path=cache,
        build_argv=argv,
        build_cwd=build_cwd,
    )
    V.validate_target_kernel_delivery_closure(
        closure, build_argv=argv, live=True)
    return closure, argv


def vendor_build_receipt(
        vendor_path, vendor_sha, *, source_root=None,
        scope=DEFAULT_SCOPE, whole=DEFAULT_WHOLE, subtree=DEFAULT_SUBTREE,
        repo="repos/ops-current-snapshot", anchor=None):
    anchor = content_anchor(scope, subtree) if anchor is None else anchor
    try:
        installed_root, _, default_source_root = _fixture_roots(vendor_path)
        installed_opp = str(installed_root)
    except V.VendorBuildReceiptError:
        # 仅供“更早字段必须先报错”的负例；正式正例都使用标准 vendor 布局。
        installed_opp = os.path.dirname(vendor_path)
        default_source_root = Path(vendor_path).resolve().parent / "fixture-source"
    # 测试 helper 的所有真实写入都锁在 vendor 所属临时根内。保留参数只为旧测试
    # 调用兼容；不能让一个逻辑来源标签把 fixture 写到 /work、/local 等外部路径。
    source_root = str(default_source_root)
    def_rel = scope.rstrip("/") + "/op_host/x_def.cpp"
    def_path = os.path.join(source_root, *def_rel.split("/"))
    os.makedirs(os.path.dirname(def_path), exist_ok=True)
    with open(def_path, "w", encoding="utf-8") as out:
        out.write("OP_ADD(X);\n")
    identity = K.discover(
        {def_rel: "OP_ADD(X);\n"}, target_scope=scope, content_anchor=anchor)
    package_opp = "/package/vendors/oprunway_fixture"
    closure = {
        "schema": TK.SCHEMA,
        "schema_version": TK.SCHEMA_VERSION,
        "status": TK.STATUS,
        "request": {
            "requested_soc": "ascend910_93",
            "selected_op": "x",
            "expected_op_type": "X",
            "installed_opp_root": installed_opp,
            "package_opp_root": package_opp,
        },
        "build_selection": {
            "cmake_cache_path": os.path.join(source_root, "build", "CMakeCache.txt"),
            "cmake_cache_relative_path": "build/CMakeCache.txt",
            "cmake_cache_sha256": "1" * 64,
            "cache_values": {
                "ASCEND_COMPUTE_UNIT": "ascend910_93",
                "ASCEND_OP_NAME": "x",
            },
        },
        "target_config": {"files": [{
            "relative_path": "op_impl/ai_core/tbe/config/ascend910_93/aic-ascend910_93-ops-info.json",
            "installed_sha256": "2" * 64,
            "package_sha256": "2" * 64,
        }]},
        "kernel_assets": [{
            "metadata_relative_path": "op_impl/ai_core/tbe/kernel/ascend910_93/x/X_fixture.json",
            "metadata_installed_sha256": "3" * 64,
            "metadata_package_sha256": "3" * 64,
            "bins": [{
                "relative_path": "op_impl/ai_core/tbe/kernel/ascend910_93/x/X_fixture.o",
                "installed_sha256": "4" * 64,
                "package_sha256": "4" * 64,
                "kernel_names": ["X_fixture"],
                "global_symbols": ["X_fixture"],
            }],
        }],
        "package": {"status": "verified", "comparison": "exact_relative_path_sha256"},
        "target_manifest": {"files": [], "sha256": ""},
    }
    closure["target_manifest"]["files"] = sorted([
        {"path": closure["target_config"]["files"][0]["relative_path"],
         "sha256": "2" * 64},
        {"path": closure["kernel_assets"][0]["metadata_relative_path"],
         "sha256": "3" * 64},
        {"path": closure["kernel_assets"][0]["bins"][0]["relative_path"],
         "sha256": "4" * 64},
    ], key=lambda row: row["path"])
    closure["target_manifest"]["sha256"] = TK._canonical_sha(
        closure["target_manifest"]["files"])
    build_argv = ["bash", "build.sh", "--soc=ascend910_93", "--ops=x"]
    # 正例物化真实 ELF object/global symbol，并只用生产 builder 生成 VERIFIED closure。
    if os.path.isfile(vendor_path):
        closure, build_argv = target_kernel_delivery_fixture(
            vendor_path=vendor_path,
            source_root=source_root,
            scope=scope,
        )
    else:
        closure["closure_sha256"] = TK._canonical_sha(closure)
    return {
        "schema": V.SCHEMA,
        "schema_version": V.SCHEMA_VERSION,
        "status": "VERIFIED",
        "degradations": [],
        "source": {
            "provenance_kind": V.PROVENANCE_LOCAL_SNAPSHOT,
            "repo": repo,
            "pr_head_sha": None,
            "snapshot_subtree_scope": scope,
            "snapshot_sha256": whole,
            "snapshot_subtree_sha256": subtree,
            "content_anchor": anchor,
        },
        "build": {
            "argv": build_argv,
            "cwd": os.path.join(source_root, scope),
            "returncode": 0,
            "returncode_source": V.RETURNCODE_SOURCE_MEASURED,
            "execution": {
                "started_at": "2026-08-07T00:00:00Z",
                "ended_at": "2026-08-07T00:00:01Z",
                "duration_s": 1.0,
                "library_path": vendor_path,
                "library_before": None,
                "library_after": {
                    "mtime_ns": 1, "size": 6, "sha256": vendor_sha,
                },
            },
            "source_snapshot_digest": {
                "schema": V.SNAPSHOT_DIGEST_SCHEMA,
                "schema_version": V.SNAPSHOT_DIGEST_VERSION,
                "taken_stage": "pre_build",
                "source_root": source_root,
                "subtree_scope": scope,
                "snapshot_sha256": whole,
                "snapshot_subtree_sha256": subtree,
                "content_anchor": anchor,
                "kernel_identity": identity,
                "algorithm": {
                    "tool": "fetch_source.py",
                    "logic_sha256": "e" * 64,
                },
                "file_count": 2,
                "subtree_file_count": 1,
                "skipped_symlink_count": 0,
                "subtree_skipped_symlink_count": 0,
            },
            "tree_state_at_emit": {
                "snapshot_sha256": whole,
                "snapshot_subtree_sha256": subtree,
                "matches_pre_build": True,
                V.SUBTREE_GATE_KEY: True,
            },
        },
        "artifact": {
            "library_path": vendor_path,
            "library_sha256": vendor_sha,
        },
        V.TARGET_KERNEL_DELIVERY_KEY: closure,
        "producer": {
            "tool": "vendor_build_receipt.py",
            "logic_sha256": V._sha256_file(os.path.abspath(V.__file__)),
        },
    }
