from pathlib import Path

from scripts import log_extract


def test_extract_filters_keywords(tmp_path: Path) -> None:
    log = tmp_path / "x.log"
    log.write_text(
        "INFO: starting\n"
        "ERROR: undefined symbol foo\n"
        "warning: skipping\n"
        "linker failed\n"
        "INFO: done\n"
        "exit=1\n",
        encoding="utf-8",
    )
    lines = log_extract.extract_errors(log)
    assert any("ERROR" in line for line in lines)
    assert any("failed" in line for line in lines)
    assert any("exit=1" in line for line in lines)
    assert all("warning: skipping" not in line for line in lines)  # not a target keyword
    assert all("INFO: starting" not in line for line in lines)


def test_extract_truncates_long_logs(tmp_path: Path) -> None:
    log = tmp_path / "long.log"
    log.write_text("ERROR: line\n" * 500, encoding="utf-8")
    lines = log_extract.extract_errors(log, max_lines=100)
    assert len(lines) == 100


def test_extract_missing_log_returns_placeholder() -> None:
    lines = log_extract.extract_errors(Path("/does/not/exist"))
    assert lines == ["(log file not found)"]


def test_extract_empty_log_returns_placeholder(tmp_path: Path) -> None:
    log = tmp_path / "empty.log"
    log.write_text("INFO: build completed successfully\nWARNING: minor issue\n", encoding="utf-8")
    lines = log_extract.extract_errors(log)
    assert lines == ["(no error keywords matched in log)"]


def test_format_as_code_block_caps_output() -> None:
    fenced = log_extract.format_as_code_block(["ERROR: a", "exit=1"])
    assert fenced.startswith("```")
    assert fenced.endswith("```")
    assert "ERROR: a" in fenced
