"""解析每一侧的算子库。

社区算子与 CANN 内置基本都同名，ATK 按算子名逐级搜库
（atk/tasks/backends/lib_interface/acl_wrapper.py:524-575）。
搜错一次两轮就是同一份实现，报告 100% 通过而什么都没验。
这里把「搜」换成「解析好再钉死」。
"""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _paths import SKILL_ROOT

sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import _opp_library  # noqa: E402


def temp_dir(case):
    """用例结束时自动清理的临时目录。不用 enterContext：真机解释器可能是 3.9。"""
    holder = tempfile.TemporaryDirectory()
    case.addCleanup(holder.cleanup)
    return Path(holder.name)


class BuiltinCandidatesTest(unittest.TestCase):
    def test_candidate_list_matches_atk(self):
        # 名单与顺序都照抄 acl_wrapper.py:565-573，错一个就可能挑到别的库。
        got = _opp_library.builtin_candidates("/opp")
        self.assertEqual([
            "/opp/../aarch64-linux/lib64/libopapi_math.so",
            "/opp/../aarch64-linux/lib64/libopapi_nn.so",
            "/opp/../aarch64-linux/lib64/libopapi_cv.so",
            "/opp/../aarch64-linux/lib64/libopapi_transformer.so",
            "/opp/../aarch64-linux/lib64/libopapi.so",
            "/opp/../x86_64-linux/lib64/libopapi_math.so",
            "/opp/../x86_64-linux/lib64/libopapi_nn.so",
            "/opp/../x86_64-linux/lib64/libopapi_cv.so",
            "/opp/../x86_64-linux/lib64/libopapi_transformer.so",
            "/opp/../x86_64-linux/lib64/libopapi.so",
            "/opp/lib64/libopapi_math.so",
            "/opp/lib64/libopapi_nn.so",
            "/opp/lib64/libopapi_cv.so",
            "/opp/lib64/libopapi_transformer.so",
            "/opp/lib64/libopapi.so",
        ], got)


class ResolveBuiltinTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir(self)
        for name in ("libopapi_math.so", "libopapi_nn.so"):
            path = self.tmp / "lib64" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fake")

    def test_picks_the_library_that_has_the_function(self):
        def probe(path, func):
            return path.endswith("libopapi_nn.so")
        got = _opp_library.resolve_builtin("aclnnBernoulliGetWorkspaceSize",
                                           str(self.tmp), probe=probe)
        self.assertTrue(got.endswith("lib64/libopapi_nn.so"))

    def test_skips_files_that_do_not_exist(self):
        def probe(path, func):
            return True
        got = _opp_library.resolve_builtin("aclnnFooGetWorkspaceSize",
                                           str(self.tmp), probe=probe)
        # 名单里 math 排在 nn 前面，两个都存在时取 math。
        self.assertTrue(got.endswith("lib64/libopapi_math.so"))

    def test_no_library_carries_the_function(self):
        with self.assertRaises(_opp_library.LibraryNotFound) as ctx:
            _opp_library.resolve_builtin("aclnnFooGetWorkspaceSize",
                                         str(self.tmp), probe=lambda p, f: False)
        self.assertIn("aclnnFooGetWorkspaceSize", str(ctx.exception))


class FingerprintTest(unittest.TestCase):
    def test_fingerprint_carries_path_sha_and_side(self):
        tmp = temp_dir(self)
        lib = tmp / "libcust_opapi.so"
        lib.write_bytes(b"vendor")
        got = _opp_library.fingerprint(str(lib), "candidate")
        self.assertEqual("candidate", got["side"])
        self.assertEqual(str(lib), got["path"])
        self.assertEqual(hashlib.sha256(b"vendor").hexdigest(), got["sha256"])


class CliTest(unittest.TestCase):
    def test_candidate_side_refuses_a_library_without_the_operator(self):
        # 指到不含该算子的 .so 时，不核就要等第二轮绑定阶段才 AttributeError，
        # 而那时一轮跑测已经排上了。
        tmp = temp_dir(self)
        lib = tmp / "libcust_opapi.so"
        lib.write_bytes(b"not a real library")
        proc = subprocess.run(
            [sys.executable, str(SKILL_ROOT / "scripts" / "resolve_opp_library.py"),
             "--op", "aclnnBernoulli", "--side", "candidate",
             "--library", str(lib), "-o", str(tmp / "fp.json")],
            capture_output=True, text=True)
        self.assertEqual(3, proc.returncode)
        self.assertIn("aclnnBernoulliGetWorkspaceSize", proc.stderr)

    def test_fingerprint_shape_is_what_downstream_gates_read(self):
        # 真机上 candidate 侧要一个真带该算子的 .so，本机造不出来；
        # 产物形状这里直接从 fingerprint() 验，CLI 那一层由上一条覆盖。
        tmp = temp_dir(self)
        lib = tmp / "libcust_opapi.so"
        lib.write_bytes(b"vendor")
        got = _opp_library.fingerprint(str(lib), "candidate")
        self.assertEqual({"side", "path", "sha256"}, set(got))
        self.assertEqual("candidate", got["side"])

    def test_candidate_side_without_library_is_refused(self):
        proc = subprocess.run(
            [sys.executable, str(SKILL_ROOT / "scripts" / "resolve_opp_library.py"),
             "--op", "aclnnBernoulli", "--side", "candidate", "-o", "/dev/null"],
            capture_output=True, text=True)
        self.assertEqual(3, proc.returncode)
        self.assertIn("--library", proc.stderr)


if __name__ == "__main__":
    unittest.main()
