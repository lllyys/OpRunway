"""Tests for reply_builder: pure template rendering, no IO."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import reply_builder


def test_pass_reply_contains_required_fields() -> None:
    body = reply_builder.build_pass_reply(
        repo="ops-transformer", op="grouped_matmul", soc="ascend950",
        fix_kind="env", fix_summary="ASCEND_GLOBAL_LOG_LEVEL=1",
    )
    assert "ops-transformer" in body
    assert "grouped_matmul" in body
    assert "ascend950" in body
    assert "env" in body
    assert "ASCEND_GLOBAL_LOG_LEVEL=1" in body
    assert "PASS" in body


def test_fail_reply_contains_required_fields() -> None:
    body = reply_builder.build_fail_reply(
        repo="ops-nn", op="quant_batch_matmul_v3", soc="ascend950",
        fix_kind="patch", fix_summary="op_kernel/foo.cpp:42",
        error_snippet="undefined reference to AscendC::HiFloat8Cast",
    )
    assert "ops-nn" in body
    assert "quant_batch_matmul_v3" in body
    assert "FAIL" in body
    assert "undefined reference to AscendC::HiFloat8Cast" in body


def test_fail_reply_truncates_long_snippet() -> None:
    long_snippet = "x" * 2000
    body = reply_builder.build_fail_reply(
        repo="ops-math", op="concat", soc="ascend950",
        fix_kind="env", fix_summary="KEY=VAL",
        error_snippet=long_snippet,
    )
    # Only 800 chars of snippet should appear
    assert "x" * 801 not in body
    assert "x" * 800 in body


def test_fail_reply_empty_snippet_uses_placeholder() -> None:
    body = reply_builder.build_fail_reply(
        repo="ops-cv", op="resize_bilinear_v2", soc="ascend950",
        fix_kind="upgrade", fix_summary="git pull",
        error_snippet="",
    )
    assert "无详细错误信息" in body
