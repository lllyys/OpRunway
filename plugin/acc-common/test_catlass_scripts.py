"""catlass 真机脚本对抗门 · 沙盒实跑测试（补 codex 17 条 CONFIRMED 漏洞）。

被测：catlass/{stage_into_catlass.sh, run_on_catlass_npu.sh, verify_catlass_build.py}。
纪律：一律 **/tmp 沙盒**——造假 catlass 副本、PATH 前置假 bisheng/msprof/build.sh/timeout/md5sum，**绝不碰真机、真 catlass**。
断言的是「防护成立」（拒绝 + 零副作用 + 不假通过），**不**为了让测试过而削弱防护。

覆盖负例（逐条对应 finding）：
  stage:  #1 无 opt-in 零副作用 / #2 相对·symlink dir 拒 / #3 revert 拒删无 stamp 同名用户目录 /
          #4 orphan sentinel revert 拒且用户行不丢 / #5 伪造 sentinel 不误报幂等 / #6 runner=.. 拒
  run_on: #7 无 opt-in 零副作用 / #8 假 BIN exit7 不报 DONE + perf 无 CSV 不报 DONE / #9 REMOTE_DIR symlink·..·越界 拒 /
          #10 cid 注入拒且 run 根外 CANARY 仍在 / #11 SETENV symlink·相对 拒且 evil 未 source / #12 WARMUP 非整数 拒
  verify: #15 注释掉/if(FALSE)/伪根 FAILED、happy PASSED / #17 CMakeLists.txt 为目录 干净 FAILED 不崩栈
"""
import os
import shutil
import subprocess
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_CAT = os.path.join(_HERE, "catlass")
STAGE = os.path.join(_CAT, "stage_into_catlass.sh")
RUNON = os.path.join(_CAT, "run_on_catlass_npu.sh")
VERIFY = os.path.join(_CAT, "verify_catlass_build.py")
RUNNER = "oprunway_catlass_basic_matmul_950_runner.cpp"
HARNESS = "oprunway_catlass_basic_matmul_950"

_TIMEOUT_SHIM = '#!/bin/bash\n[ "$1" = "--" ] && shift\nshift\nexec "$@"\n'   # timeout [--] DUR CMD...
_MD5_SHIM = '#!/bin/bash\necho "deadbeef  $1"\n'
_BISHENG_SHIM = "#!/bin/bash\necho bisheng 1.0\n"
_GIT_SHIM = "#!/bin/bash\necho abc1234\n"
_MSPROF_HIT = (
    "#!/bin/bash\n"
    'out=""; for a in "$@"; do case "$a" in --output=*) out="${a#--output=}";; esac; done\n'
    '[ -n "$out" ] && { mkdir -p "$out"; printf \'Op Name,Task Duration(us)\\nKKK,12.3\\n\' > "$out/OpBasicInfo.csv"; }\n'
)
_MSPROF_NOCSV = "#!/bin/bash\ntrue\n"


def _w(path, content, mode=0o755):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    os.chmod(path, mode)


class _Sandbox:
    """一次性 /tmp 沙盒：假 catlass 工作副本 + 假工具 PATH + run 目录。"""

    def __init__(self, bin_exit=0, msprof=_MSPROF_HIT, kernel="hh", harness="hh"):
        self.root = tempfile.mkdtemp(prefix="oprunway_catlass_test_")
        self.kernel = kernel
        self.harness = harness
        self.cat = os.path.join(self.root, "catlass")
        self.fakebin = os.path.join(self.root, "fakebin")
        self.run = os.path.join(self.root, "run")
        os.makedirs(os.path.join(self.cat, "scripts"))
        os.makedirs(os.path.join(self.cat, "examples"))
        os.makedirs(os.path.join(self.cat, "output", "bin"))
        os.makedirs(self.fakebin)
        _w(os.path.join(self.cat, "examples", "CMakeLists.txt"),
           "add_subdirectory(00_basic_matmul)\n", 0o644)
        # build.sh 产出一个恒 exit=<bin_exit> 的假 BIN（成功时打印完成信号）
        if bin_exit == 0:
            body = (f'echo "[OPRUNWAY_CATLASS] harness={harness} cases=1"\n'
                    'echo "OPRUNWAY_CATLASS_DONE total=1 ok=1 fail=0"\nexit 0\n')
        else:
            body = (f'echo "[OPRUNWAY_CATLASS] harness={harness} cases=1"\n'
                    f'echo boom\nexit {bin_exit}\n')
        _w(os.path.join(self.cat, "scripts", "build.sh"),
           "#!/bin/bash\nmkdir -p output/bin\n"
           f"cat > output/bin/{harness} <<'BIN'\n#!/bin/bash\n{body}BIN\n"
           f"chmod +x output/bin/{harness}\n")
        _w(os.path.join(self.fakebin, "timeout"), _TIMEOUT_SHIM)
        _w(os.path.join(self.fakebin, "md5sum"), _MD5_SHIM)
        _w(os.path.join(self.fakebin, "msprof"), msprof.replace("KKK", kernel))
        _w(os.path.join(self.fakebin, "bisheng"), _BISHENG_SHIM)
        _w(os.path.join(self.fakebin, "git"), _GIT_SHIM)
        # run 目录（cases + manifest + perfcases）
        cdir = os.path.join(self.run, "cases", "c1")
        os.makedirs(cdir)
        _w(os.path.join(cdir, "A.bin"), "x", 0o644)
        _w(os.path.join(cdir, "B.bin"), "x", 0o644)
        _w(os.path.join(self.run, "cases", "manifest.txt"), "c1 float32 8 8 8\n", 0o644)
        _w(os.path.join(self.run, "perfcases_list.txt"), "c1\n", 0o644)

    def env(self, **over):
        e = dict(os.environ)
        e["PATH"] = self.fakebin + os.pathsep + e.get("PATH", "")
        base = {
            "OPRUNWAY_CATLASS_REAL": "1",
            "OPRUNWAY_CATLASS_DIR": self.cat,
            "OPRUNWAY_ARCH": "3510",
            "OPRUNWAY_HARNESS": self.harness,
            "OPRUNWAY_RUNNER": RUNNER,
            "OPRUNWAY_KERNEL": self.kernel,
            "OPRUNWAY_REMOTE_DIR": self.run,
            "OPRUNWAY_REMOTE_ROOT": self.root,
            "OPRUNWAY_WARMUP": "1",
            "OPRUNWAY_TIMEOUT": "10",
            "OPRUNWAY_SETENV": "/dev/null",
            "OPRUNWAY_TEMPLATE_DIR": _CAT,
        }
        base.update(over)
        for k, v in base.items():
            if v is None:
                e.pop(k, None)
            else:
                e[k] = v
        return e

    def cmake_bytes(self):
        with open(os.path.join(self.cat, "examples", "CMakeLists.txt"), "rb") as f:
            return f.read()

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


def _run(argv, env):
    return subprocess.run(argv, env=env, capture_output=True, text=True)


# ============================================================ stage_into_catlass.sh
class StageTests(unittest.TestCase):
    def setUp(self):
        self.sb = _Sandbox()
        self.addCleanup(self.sb.cleanup)

    def _stage_env(self, **over):
        base = {
            "OPRUNWAY_CATLASS_REAL": "1",
            "OPRUNWAY_CATLASS_DIR": self.sb.cat,
            "OPRUNWAY_HARNESS": HARNESS,
            "OPRUNWAY_RUNNER": RUNNER,
            "OPRUNWAY_TEMPLATE_DIR": _CAT,
        }
        base.update(over)
        e = dict(os.environ)
        for k, v in base.items():
            if v is None:
                e.pop(k, None)
            else:
                e[k] = v
        return e

    def test_01_no_optin_zero_side_effect(self):
        """#1 未 opt-in → exit≠0 且零副作用（CMakeLists 字节不变、harness 目录未建）。"""
        before = self.sb.cmake_bytes()
        r = _run(["bash", STAGE], self._stage_env(OPRUNWAY_CATLASS_REAL=None))
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(before, self.sb.cmake_bytes())
        self.assertFalse(os.path.exists(os.path.join(self.sb.cat, "examples", HARNESS)))

    def test_02_relative_dir_rejected(self):
        r = _run(["bash", STAGE], self._stage_env(OPRUNWAY_CATLASS_DIR="catlass"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("绝对路径", r.stderr)

    def test_02_symlink_dir_rejected(self):
        link = os.path.join(self.sb.root, "catlink")
        os.symlink(self.sb.cat, link)
        r = _run(["bash", STAGE], self._stage_env(OPRUNWAY_CATLASS_DIR=link))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("symlink", r.stderr)

    def test_02_non_catlass_root_rejected(self):
        d = os.path.join(self.sb.root, "notcatlass")
        os.makedirs(os.path.join(d, "examples"))
        _w(os.path.join(d, "examples", "CMakeLists.txt"), "x\n", 0o644)
        r = _run(["bash", STAGE], self._stage_env(OPRUNWAY_CATLASS_DIR=d))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("build.sh", r.stderr)

    def test_06_runner_dotdot_rejected(self):
        r = _run(["bash", STAGE], self._stage_env(OPRUNWAY_RUNNER=".."))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("runner", r.stderr)

    def test_06_runner_hidden_rejected(self):
        r = _run(["bash", STAGE], self._stage_env(OPRUNWAY_RUNNER=".evil"))
        self.assertNotEqual(r.returncode, 0)

    def test_10_happy_stage_idempotent_revert(self):
        """happy：注入干净三行块 + stamp；再跑幂等不重复；revert 删块+目录、用户行保留。"""
        r = _run(["bash", STAGE], self._stage_env())
        self.assertEqual(r.returncode, 0, r.stderr)
        hdir = os.path.join(self.sb.cat, "examples", HARNESS)
        self.assertTrue(os.path.isfile(os.path.join(hdir, RUNNER)))
        self.assertTrue(os.path.isfile(os.path.join(hdir, ".oprunway_stamp")))
        cm = os.path.join(self.sb.cat, "examples", "CMakeLists.txt")
        with open(cm, encoding="utf-8") as f:
            txt = f.read()
        self.assertEqual(txt.count(f"add_subdirectory({HARNESS})"), 1)
        # 幂等
        r2 = _run(["bash", STAGE], self._stage_env())
        self.assertEqual(r2.returncode, 0)
        with open(cm, encoding="utf-8") as f:
            self.assertEqual(f.read().count(f"add_subdirectory({HARNESS})"), 1)
        # revert
        r3 = _run(["bash", STAGE, "--revert"], self._stage_env())
        self.assertEqual(r3.returncode, 0, r3.stderr)
        self.assertFalse(os.path.exists(hdir))
        with open(cm, encoding="utf-8") as f:
            after = f.read()
        self.assertIn("add_subdirectory(00_basic_matmul)", after)
        self.assertEqual(after.count(f"add_subdirectory({HARNESS})"), 0)

    def test_03_revert_refuses_unstamped_user_dir(self):
        """#3 同名用户目录但无 stamp → revert 拒删、目录仍在。"""
        hdir = os.path.join(self.sb.cat, "examples", HARNESS)
        os.makedirs(hdir)
        _w(os.path.join(hdir, "keep.txt"), "precious\n", 0o644)
        r = _run(["bash", STAGE, "--revert"], self._stage_env())
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(os.path.isfile(os.path.join(hdir, "keep.txt")))

    def test_04_orphan_sentinel_revert_refused_user_lines_kept(self):
        """#4 伪造 start sentinel 夹住用户内容 → revert 拒绝、用户行一字不少。"""
        cm = os.path.join(self.sb.cat, "examples", "CMakeLists.txt")
        _w(cm,
           "add_subdirectory(00_basic_matmul)\n"
           "USER_CRITICAL_1\n"
           f"# >>> OPRUNWAY_STAGE {HARNESS} >>>\n"
           "USER_CRITICAL_2\n"
           f"# <<< OPRUNWAY_STAGE {HARNESS} <<<\n"
           "USER_CRITICAL_3\n", 0o644)
        before = self.sb.cmake_bytes()
        r = _run(["bash", STAGE, "--revert"], self._stage_env())
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(before, self.sb.cmake_bytes())   # 一字不动

    def test_05_forged_sentinel_not_idempotent_misjudge(self):
        """#5 残留单行 sentinel（无干净块）→ stage 拒绝注入（不误报幂等静默漏注册）。"""
        cm = os.path.join(self.sb.cat, "examples", "CMakeLists.txt")
        _w(cm, f"add_subdirectory(00_basic_matmul)\n# >>> OPRUNWAY_STAGE {HARNESS} >>>\n", 0o644)
        r = _run(["bash", STAGE], self._stage_env())
        self.assertNotEqual(r.returncode, 0)


# ============================================================ run_on_catlass_npu.sh
class RunOnTests(unittest.TestCase):
    def test_07_no_optin_zero_side_effect(self):
        sb = _Sandbox()
        self.addCleanup(sb.cleanup)
        before = sb.cmake_bytes()
        r = _run(["bash", RUNON], sb.env(OPRUNWAY_CATLASS_REAL=None))
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(before, sb.cmake_bytes())
        self.assertFalse(os.path.exists(os.path.join(sb.cat, "examples", sb.harness)))
        self.assertNotIn("OPRUNWAY_NPU_DONE", r.stdout)

    def test_08_fake_bin_exit7_no_done(self):
        """#8（最严重）假 BIN exit 7 → 非零退出、不打印 OPRUNWAY_NPU_DONE。"""
        sb = _Sandbox(bin_exit=7)
        self.addCleanup(sb.cleanup)
        r = _run(["bash", RUNON], sb.env())
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("OPRUNWAY_NPU_DONE", r.stdout)
        self.assertIn("OPRUNWAY_NPU_FAILED", r.stderr)

    def test_08_msprof_no_csv_no_done(self):
        """#8 msprof 不产 CSV → perf 门拦住、不报 DONE。"""
        sb = _Sandbox(msprof=_MSPROF_NOCSV)
        self.addCleanup(sb.cleanup)
        r = _run(["bash", RUNON], sb.env())
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("OPRUNWAY_NPU_DONE", r.stdout)

    def test_08_msprof_wrong_kernel_no_done(self):
        """#8 CSV 未命中 kernel 符号 → 不报 DONE。"""
        sb = _Sandbox(kernel="hh")
        self.addCleanup(sb.cleanup)
        # msprof 产 CSV 但里面是别的 kernel 名
        _w(os.path.join(sb.fakebin, "msprof"),
           _MSPROF_HIT.replace("KKK", "SOME_OTHER_KERNEL"))
        r = _run(["bash", RUNON], sb.env(OPRUNWAY_KERNEL="hh"))
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("OPRUNWAY_NPU_DONE", r.stdout)

    def test_08_happy_prints_done(self):
        sb = _Sandbox()
        self.addCleanup(sb.cleanup)
        r = _run(["bash", RUNON], sb.env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("OPRUNWAY_NPU_DONE", r.stdout)
        self.assertTrue(os.path.isfile(os.path.join(sb.run, "c1.OpBasicInfo.csv")))

    def test_10_cid_injection_rejected(self):
        """#10 cid=x/../../CANARY → 拒绝，run 根外 CANARY 仍在。"""
        sb = _Sandbox()
        self.addCleanup(sb.cleanup)
        canary = os.path.join(sb.root, "CANARY.txt")
        _w(canary, "canary\n", 0o644)
        _w(os.path.join(sb.run, "perfcases_list.txt"), "c1\nc1/../../CANARY\n", 0o644)
        r = _run(["bash", RUNON], sb.env())
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(os.path.isfile(canary))
        self.assertNotIn("OPRUNWAY_NPU_DONE", r.stdout)

    def test_09_remote_dir_symlink_rejected(self):
        sb = _Sandbox()
        self.addCleanup(sb.cleanup)
        link = os.path.join(sb.root, "runlink")
        os.symlink(sb.run, link)
        r = _run(["bash", RUNON], sb.env(OPRUNWAY_REMOTE_DIR=link))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("symlink", r.stderr)

    def test_09_remote_dir_dotdot_rejected(self):
        sb = _Sandbox()
        self.addCleanup(sb.cleanup)
        r = _run(["bash", RUNON], sb.env(OPRUNWAY_REMOTE_DIR=sb.run + "/../run"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("..", r.stderr)

    def test_09_remote_dir_outside_root_rejected(self):
        sb = _Sandbox()
        self.addCleanup(sb.cleanup)
        outside = tempfile.mkdtemp(prefix="oprunway_outside_")
        self.addCleanup(lambda: shutil.rmtree(outside, ignore_errors=True))
        r = _run(["bash", RUNON], sb.env(OPRUNWAY_REMOTE_DIR=outside,
                                         OPRUNWAY_REMOTE_ROOT=sb.root))
        self.assertNotEqual(r.returncode, 0)

    def test_09_catlass_dir_symlink_rejected(self):
        sb = _Sandbox()
        self.addCleanup(sb.cleanup)
        link = os.path.join(sb.root, "catlink")
        os.symlink(sb.cat, link)
        r = _run(["bash", RUNON], sb.env(OPRUNWAY_CATLASS_DIR=link))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("symlink", r.stderr)

    def test_11_setenv_symlink_rejected_and_not_sourced(self):
        """#11 SETENV symlink → 拒绝且 evil 内容未被 source。"""
        sb = _Sandbox()
        self.addCleanup(sb.cleanup)
        evil = os.path.join(sb.root, "evil.sh")
        _w(evil, 'echo OPRUNWAY_EVIL_SOURCED_MARKER\n', 0o644)
        link = os.path.join(sb.root, "evil_link.sh")
        os.symlink(evil, link)
        r = _run(["bash", RUNON], sb.env(OPRUNWAY_SETENV=link))
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("OPRUNWAY_EVIL_SOURCED_MARKER", r.stdout + r.stderr)

    def test_11_setenv_relative_rejected(self):
        sb = _Sandbox()
        self.addCleanup(sb.cleanup)
        r = _run(["bash", RUNON], sb.env(OPRUNWAY_SETENV="rel/set_env.sh"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("绝对路径", r.stderr)

    def test_12_warmup_non_integer_rejected(self):
        sb = _Sandbox()
        self.addCleanup(sb.cleanup)
        r = _run(["bash", RUNON], sb.env(OPRUNWAY_WARMUP="3;rm -rf /"))
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("OPRUNWAY_NPU_DONE", r.stdout)


# ============================================================ verify_catlass_build.py
class VerifyTests(unittest.TestCase):
    def _mk_root(self, cmake_content, with_hdir=True):
        d = tempfile.mkdtemp(prefix="oprunway_verify_")
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        os.makedirs(os.path.join(d, "scripts"))
        os.makedirs(os.path.join(d, "examples"))
        _w(os.path.join(d, "scripts", "build.sh"), "#!/bin/bash\n", 0o644)
        _w(os.path.join(d, "examples", "CMakeLists.txt"), cmake_content, 0o644)
        if with_hdir:
            hd = os.path.join(d, "examples", HARNESS)
            os.makedirs(hd)
            _w(os.path.join(hd, "CMakeLists.txt"), "x\n", 0o644)
        return d

    def _verify(self, catlass_dir=None):
        argv = ["python3", VERIFY, "--arch", "3510"]
        if catlass_dir:
            argv += ["--catlass-dir", catlass_dir]
        return _run(argv, dict(os.environ))

    def test_template_self_check_passes(self):
        r = self._verify()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("PASSED", r.stdout)

    def test_15_happy_staged_passes(self):
        d = self._mk_root(f"add_subdirectory(00_basic_matmul)\nadd_subdirectory({HARNESS})\n")
        r = self._verify(d)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("PASSED", r.stdout)

    def test_15_commented_add_subdirectory_failed(self):
        d = self._mk_root(f"add_subdirectory(00_basic_matmul)\n# add_subdirectory({HARNESS})\n")
        r = self._verify(d)
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAILED", r.stdout)

    def test_15_if_false_wrapped_failed(self):
        d = self._mk_root(f"if(FALSE)\nadd_subdirectory({HARNESS})\nendif()\n")
        r = self._verify(d)
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAILED", r.stdout)

    def test_15_fake_root_failed(self):
        d = tempfile.mkdtemp(prefix="oprunway_fakeroot_")
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        os.makedirs(os.path.join(d, "examples", HARNESS))
        _w(os.path.join(d, "examples", HARNESS, "CMakeLists.txt"), "x\n", 0o644)
        _w(os.path.join(d, "examples", "CMakeLists.txt"),
           f"add_subdirectory({HARNESS})\n", 0o644)
        r = self._verify(d)
        self.assertEqual(r.returncode, 1)
        self.assertIn("非 catlass 根", r.stdout)

    def test_17_cmake_is_directory_clean_failed(self):
        """#17 examples/CMakeLists.txt 为目录 → 干净 FAILED，不崩栈（无 traceback）。"""
        d = tempfile.mkdtemp(prefix="oprunway_dircmake_")
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        os.makedirs(os.path.join(d, "scripts"))
        os.makedirs(os.path.join(d, "examples", "CMakeLists.txt"))   # 目录！
        os.makedirs(os.path.join(d, "examples", HARNESS))
        _w(os.path.join(d, "scripts", "build.sh"), "#!/bin/bash\n", 0o644)
        _w(os.path.join(d, "examples", HARNESS, "CMakeLists.txt"), "x\n", 0o644)
        r = self._verify(d)
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAILED", r.stdout)
        self.assertNotIn("Traceback", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
