"""error_signature must be stable across timestamps, line numbers, abs paths."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from _error_sig import normalize, signature, first_error_line


def test_strip_timestamp() -> None:
    a = "2026-05-20T10:05:12 ERROR: undefined reference to X"
    b = "2026-05-21T11:00:00 ERROR: undefined reference to X"
    assert normalize(a) == normalize(b)


def test_strip_line_col() -> None:
    a = "ERROR: /a/b/foo.cpp:42:18: undefined reference to X"
    b = "ERROR: /a/b/foo.cpp:99:1: undefined reference to X"
    assert normalize(a) == normalize(b)


def test_strip_abs_path() -> None:
    a = "ERROR: /home/alice/cann/ops-transformer/op_kernel/foo.cpp: bad"
    b = "ERROR: /opt/build/ops-transformer/op_kernel/foo.cpp: bad"
    assert "ops-transformer/op_kernel/foo.cpp" in normalize(a)
    assert normalize(a) == normalize(b)


def test_signature_is_12_hex() -> None:
    sig = signature("ERROR: undefined reference to X")
    assert len(sig) == 12
    assert all(c in "0123456789abcdef" for c in sig)


def test_extract_first_error_line_from_log(tmp_path) -> None:
    log = tmp_path / "x.log"
    log.write_text(
        "INFO start\n"
        "2026-01-01T00:00:00 ERROR: undefined reference to Foo\n"
        "2026-01-01T00:00:01 ERROR: trailing noise\n",
        encoding="utf-8",
    )
    line = first_error_line(log)
    assert "undefined reference to Foo" in line
