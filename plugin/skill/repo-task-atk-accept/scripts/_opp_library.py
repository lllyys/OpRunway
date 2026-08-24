"""解析某个算子在某一侧对应的算子库文件。

ATK 自己是按算子名逐级搜的（acl_wrapper.py:524-575），而第一级
ATK_CUSTOM_OPP_PATH 路径存在就直接用、不校验里面有没有这个算子
（acl_wrapper.py:536-538）。社区算子与 CANN 内置基本都同名，
让它去搜就有搜错的机会，搜错就是两轮跑同一份实现。

所以这里把候选清单照抄过来自己解析，解析结果钉进 ATK_CUSTOM_OPP_PATH，
搜索路径不再参与决策。
"""

import ctypes
import hashlib
import os

# 照抄 acl_wrapper.py:565-573 的两张清单，顺序也照抄。
BUILTIN_SUBDIRS = ("../aarch64-linux/", "../x86_64-linux/", "")
BUILTIN_SO_NAMES = ("libopapi_math.so", "libopapi_nn.so", "libopapi_cv.so",
                    "libopapi_transformer.so", "libopapi.so")


class LibraryNotFound(Exception):
    """候选清单里没有一个库带这个算子。"""


def builtin_candidates(opp_path):
    """内置库的候选清单，顺序与 ATK 一致。"""
    return [os.path.join(opp_path, f"{sub}lib64/{name}")
            for sub in BUILTIN_SUBDIRS
            for name in BUILTIN_SO_NAMES]


def has_function(so_path, func_name):
    """库里有没有这个函数。

    照 ATK 的 check_interface_exists（acl_wrapper.py:508-521）写：装不上、
    装上了没这个函数，都算没有。它连裸 Exception 都吞，因为 dlopen 失败的
    形态不止 OSError 一种——这里跟着吞，否则真机上换一种失败就是崩栈退出，
    而不是接着试下一个候选库。
    """
    try:
        lib = ctypes.CDLL(so_path)
        getattr(lib, func_name)
        return True
    except Exception:
        return False


def resolve_builtin(func_name, opp_path, probe=has_function):
    """在内置候选清单里挑出真正带这个算子的那一个。"""
    tried = []
    for path in builtin_candidates(opp_path):
        if not os.path.exists(path):
            continue
        tried.append(path)
        if probe(path, func_name):
            return path
    raise LibraryNotFound(
        f"{opp_path} 下没有一个库带 {func_name}。\n"
        f"  已试过 {tried or '（候选清单里的文件一个都不存在）'}\n"
        "  → 确认 ASCEND_OPP_PATH 指对了，且这个算子确实是 CANN 内置算子。")


def fingerprint(path, side):
    """把一份库记成可核对的三元组。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return {"side": side, "path": os.path.abspath(path),
            "sha256": digest.hexdigest()}
