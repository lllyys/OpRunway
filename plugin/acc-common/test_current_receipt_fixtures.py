"""测试专用的 current vendor receipt v3 fixture。

只复用生产模块公开契约常量与当前逻辑指纹；不得在产品代码加入 legacy 合成兼容。
"""

import os

import vendor_build_receipt as V


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


def vendor_build_receipt(
        vendor_path, vendor_sha, *, source_root="/work/ops",
        scope=DEFAULT_SCOPE, whole=DEFAULT_WHOLE, subtree=DEFAULT_SUBTREE,
        repo="repos/ops-current-snapshot"):
    anchor = content_anchor(scope, subtree)
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
            "argv": ["bash", "build.sh", "--ops=x"],
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
        "producer": {
            "tool": "vendor_build_receipt.py",
            "logic_sha256": V._sha256_file(os.path.abspath(V.__file__)),
        },
    }
