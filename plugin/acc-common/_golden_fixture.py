"""共享测试 fixture（**非** test_*.py、不被 unittest 收集）：golden 去引擎化（ADR 0011）后，`gen_cases`
按算子从 `<ops_root>/<op>/golden.py` 加载 golden（引擎零内置、缺则 fail-closed）。

各测试文件的 `setUpModule = install` / `tearDownModule = uninstall` 即可——`install` 建一个临时 ops_root、
拷 4 份 `samples/golden/<op>/golden.py` 进去、设 `OPRUNWAY_OPS_DIR` 指向它；子进程（run_workflow）不传 env=、
继承 `os.environ` 即得同一 root。假算子测试用 `place_golden(root, op, body=...)` 另落。
"""
import os, shutil, tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_SAMPLES_GOLDEN = os.path.join(_HERE, "..", "..", "samples", "golden")
_root = None
_old = None
_refs = 0


def place_golden(ops_root, op, body=None, source="numpy fake", provenance="test fixture"):
    """在 `<ops_root>/<op>/golden.py` 落 golden（body=golden_fn 源码；缺省从 samples/golden 拷该算子）。"""
    d = os.path.join(ops_root, op)
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, "golden.py")
    if body is None:
        shutil.copy(os.path.join(_SAMPLES_GOLDEN, op, "golden.py"), dst)
    else:
        with open(dst, "w", encoding="utf-8") as f:
            f.write(f"GOLDEN_SOURCE = {source!r}\nGOLDEN_PROVENANCE = {provenance!r}\nimport numpy as np\n{body}")
    return dst


def install():
    """建 golden root（若未建）+ 拷 4 算子 golden + 设 OPRUNWAY_OPS_DIR。返回 root 路径。"""
    global _root, _old, _refs
    if _root is None:
        _root = os.path.realpath(tempfile.mkdtemp(prefix="oprunway_golden_root_"))
        for op in ("IsClose", "Sign", "Equal", "Neg"):
            place_golden(_root, op)
        _old = os.environ.get("OPRUNWAY_OPS_DIR")
    os.environ["OPRUNWAY_OPS_DIR"] = _root
    _refs += 1
    return _root


def uninstall():
    """还原 OPRUNWAY_OPS_DIR + 清 golden root（ref 归零时）。"""
    global _root, _old, _refs
    _refs -= 1
    if _refs <= 0 and _root is not None:
        if _old is None:
            os.environ.pop("OPRUNWAY_OPS_DIR", None)
        else:
            os.environ["OPRUNWAY_OPS_DIR"] = _old
        shutil.rmtree(_root, ignore_errors=True)
        _root, _old, _refs = None, None, 0


def root():
    """当前 golden root（供假算子测试 place_golden(root(), fakeop)）。"""
    return _root
