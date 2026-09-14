"""复现包里自带件那一半的分区逻辑。

**破坏时的表现是「拿到包的人一条都跑不了，而包里没有任何迹象说明为什么」**
——原件在验收机上跑不起来才有 `fixed/`，两份混在一起或漏搬一份都不报错。
真机上翻出来一次要重出整个复现包。
"""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import make_repro  # noqa: E402


def _pkg(tmp_path, *, fixed=None, entry=True):
    pkg = tmp_path / "pkg"
    kit = pkg / "kit" / "aclsparseFoo_testCase"
    kit.mkdir(parents=True)
    (kit / "function_foo.py").write_text("# 原件\n", encoding="utf-8")
    if entry:
        (kit / "run_accuracy_atk.sh").write_text("atk task -c cases.json\n", encoding="utf-8")
    if fixed is not None:
        fx = pkg / "kit_fixes"
        fx.mkdir()
        for name, text in fixed.items():
            (fx / name).write_text(text, encoding="utf-8")
    return pkg


FACTS = {"adopted_files": {"function_foo.py": "abc123"}}


def test_original_is_byte_identical(tmp_path):
    """原件必须原样搬。它的 sha256 是「跑的是任务方交付的那份」的唯一凭据。"""
    pkg = _pkg(tmp_path, fixed={"run.sh": "bash\n"})
    make_repro._copy_op_testcase(pkg, tmp_path / "out", FACTS)
    src = pkg / "kit" / "aclsparseFoo_testCase" / "function_foo.py"
    dst = (tmp_path / "out" / "op-testcase" / "original"
           / "aclsparseFoo_testCase" / "function_foo.py")
    assert dst.read_bytes() == src.read_bytes()


def test_fixed_lands_in_its_own_dir(tmp_path):
    pkg = _pkg(tmp_path, fixed={"run.sh": "bash\n", "adapt_cases.py": "# x\n"})
    got = make_repro._copy_op_testcase(pkg, tmp_path / "out", FACTS)
    root = tmp_path / "out" / "op-testcase"
    assert got["has_fixed"] and got["run"]
    assert (root / "fixed" / "adapt_cases.py").exists()
    # 修复件不许混进原件那一半，否则指纹比对全部对不上。
    assert not (root / "original" / "adapt_cases.py").exists()


def test_no_fixes_means_no_fixed_dir(tmp_path):
    got = make_repro._copy_op_testcase(_pkg(tmp_path), tmp_path / "out", FACTS)
    assert got["has_fixed"] is False and got["run"] is False
    assert not (tmp_path / "out" / "op-testcase" / "fixed").exists()


def test_run_sh_absence_is_reported_not_guessed(tmp_path):
    """跑测侧只搬运，不构造命令。没有 run.sh 就如实说没有。"""
    got = make_repro._copy_op_testcase(pkg := _pkg(tmp_path, fixed={"adapt_cases.py": "# x\n"}),
                                       tmp_path / "out", FACTS)
    assert got["has_fixed"] is True and got["run"] is False
    assert (pkg / "kit_fixes" / "run.sh").exists() is False


def test_manifest_records_adopted_fingerprints(tmp_path):
    make_repro._copy_op_testcase(_pkg(tmp_path), tmp_path / "out", FACTS)
    text = (tmp_path / "out" / "op-testcase" / "original" / "MANIFEST.md").read_text(
        encoding="utf-8")
    assert "function_foo.py" in text and "abc123" in text


def test_no_kit_means_no_op_testcase(tmp_path):
    """自产用例包没有 kit/，这一半整个不生成，不留空目录。"""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    assert make_repro._copy_op_testcase(pkg, tmp_path / "out", FACTS) is None
    assert not (tmp_path / "out" / "op-testcase").exists()


@pytest.mark.parametrize("entry", [True, False])
def test_entry_detected_by_content_not_name(tmp_path, entry):
    got = make_repro._copy_op_testcase(_pkg(tmp_path, entry=entry), tmp_path / "out", FACTS)
    assert bool(got["entry"]) is entry
