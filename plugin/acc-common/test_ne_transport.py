"""真机配置 + 传输层单测（stdlib unittest）——不依赖真机/ssh。

跑: python3 test_ne_transport.py   或   python3 -m unittest test_ne_transport -v

守的契约：
- `_ne_cfg` **无私有默认值**：机器名 / 远端路径 / 被测仓路径缺失即报错，绝不用某台私有机兜底
  （否则别人拿到插件默认连一台不存在的机器、找不存在的路径）；
- 两种传输模式：`OPRUNWAY_TARGET=local`（本机直连，无 ssh/scp）/ `remote`（默认，ssh）；
  remote 缺 `OPRUNWAY_SSH_HOST` 报错，local 不需要 host；
- 传输原语 `_copy_to`/`_copy_from`/`_shell` 在 local（host=None）模式走本机 cp/bash、不碰 ssh。
"""
import inspect, json, os, shutil, subprocess, tempfile, unittest
from unittest import mock

import numpy as np
import gen_cases as GC
import repo_adapter as R

_HERE = os.path.dirname(os.path.abspath(__file__))
_SIGN_SPEC = os.path.join(_HERE, "..", "..", "samples", "specs", "sign.spec.json")
_REAL_SHELL = R._shell   # 原始 _shell（任何 patch 之前捕获）：fake_shell 让"部署步"委托真跑本机 bash

# 一份"齐全"的 remote env（各用例按需删字段来触发缺失报错）
_FULL_REMOTE = {
    "OPRUNWAY_TARGET": "remote",
    "OPRUNWAY_SSH_HOST": "somehost",
    "OPRUNWAY_REMOTE_DIR": "/remote/run",
    "OPRUNWAY_OPS_REPO": "/remote/ops-repo",
    "OPRUNWAY_OPP": "/remote/opp",
}


def _cfg(env):
    with mock.patch.dict(os.environ, env, clear=True):
        return R._ne_cfg()


class NeCfgNoPrivateDefaultsTest(unittest.TestCase):
    def test_full_remote_ok(self):
        c = _cfg(_FULL_REMOTE)
        self.assertEqual(c["target"], "remote")
        self.assertEqual(c["host"], "somehost")
        self.assertEqual(c["ops"], "/remote/ops-repo")

    def test_missing_each_required_field_raises(self):
        for drop in ("OPRUNWAY_REMOTE_DIR", "OPRUNWAY_OPS_REPO", "OPRUNWAY_OPP"):
            env = dict(_FULL_REMOTE); env.pop(drop)
            with self.assertRaises(ValueError, msg=f"缺 {drop} 未报错"):
                _cfg(env)

    def test_error_message_leaks_no_private_default(self):
        """报错信息里绝不出现某台私有机的名字/路径。"""
        env = dict(_FULL_REMOTE); env.pop("OPRUNWAY_OPS_REPO")
        with self.assertRaises(ValueError) as cm:
            _cfg(env)
        msg = str(cm.exception)
        for leak in ("ascend-a3", "ascend-a5", "/home/lys", "ops-math", "oprunway_run", "oprunway_opp"):
            self.assertNotIn(leak, msg, f"报错泄露私有默认 {leak!r}")

    def test_source_has_no_private_defaults(self):
        """源码层面：_ne_cfg 不含任何私有机名/路径作默认。"""
        src = inspect.getsource(R._ne_cfg)
        for leak in ("ascend-a3", "/home/lys/ops-math", "/home/lys/oprunway_run", "/home/lys/oprunway_opp"):
            self.assertNotIn(leak, src, f"_ne_cfg 仍含私有默认 {leak!r}")

    def test_soc_vendor_setenv_keep_generic_defaults(self):
        """soc/vendor/setenv 是昇腾通用约定、非私有机名，允许保留默认。"""
        c = _cfg(_FULL_REMOTE)
        self.assertEqual(c["soc"], "ascend910_93")
        self.assertEqual(c["vendor"], "oprunway")
        self.assertTrue(c["setenv"].endswith("set_env.sh"))


class NeCfgTransportModeTest(unittest.TestCase):
    def test_remote_requires_host(self):
        env = dict(_FULL_REMOTE); env.pop("OPRUNWAY_SSH_HOST")
        with self.assertRaises(ValueError):
            _cfg(env)

    def test_local_does_not_require_host(self):
        env = {"OPRUNWAY_TARGET": "local",
               "OPRUNWAY_REMOTE_DIR": "/work", "OPRUNWAY_OPS_REPO": "/ops", "OPRUNWAY_OPP": "/opp"}
        c = _cfg(env)
        self.assertEqual(c["target"], "local")
        self.assertIsNone(c["host"])

    def test_default_target_is_remote(self):
        env = dict(_FULL_REMOTE); env.pop("OPRUNWAY_TARGET")   # 不设 → remote
        self.assertEqual(_cfg(env)["target"], "remote")

    def test_invalid_target_rejected(self):
        env = dict(_FULL_REMOTE); env["OPRUNWAY_TARGET"] = "cloud"
        with self.assertRaises(ValueError):
            _cfg(env)


class LocalTransportPrimitiveTest(unittest.TestCase):
    """host=None（local）时，三原语走本机 cp/bash，不调 ssh/scp。"""

    def test_copy_to_local_creates_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "a.txt"); open(src, "w").write("payload")
            dst = os.path.join(d, "deep/sub/b.txt")     # 目标目录不存在
            R._copy_to(None, src, dst, timeout=10)
            self.assertEqual(open(dst).read(), "payload")

    def test_copy_from_local_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "out.bin"); open(src, "wb").write(b"\x01\x02")
            back = os.path.join(d, "pulled/out.bin")
            R._copy_from(None, src, back, timeout=10, check=True)
            self.assertEqual(open(back, "rb").read(), b"\x01\x02")

    def test_copy_from_local_missing_check_false_no_crash(self):
        with tempfile.TemporaryDirectory() as d:
            back = os.path.join(d, "x")
            R._copy_from(None, os.path.join(d, "nope"), back, timeout=10, check=False, quiet_stderr=True)
            self.assertFalse(os.path.exists(back))

    def test_copy_from_local_missing_check_true_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileNotFoundError):
                R._copy_from(None, os.path.join(d, "nope"), os.path.join(d, "x"), timeout=10, check=True)

    def test_shell_local_runs_on_host_machine(self):
        """local 模式 _shell 用本机 bash 跑，不经 ssh。"""
        r = R._shell(None, "echo OPRUNWAY_TEST_OK\n", timeout=10, check=True, capture=True)
        self.assertIn("OPRUNWAY_TEST_OK", r.stdout)

    def test_shell_remote_builds_ssh_argv(self):
        """remote 模式 _shell 的 argv 以 ssh 开头（用 mock 拦 subprocess，不真连）。"""
        with mock.patch("repo_adapter.subprocess.run") as m:
            R._shell("somehost", "echo hi\n", timeout=10, check=True)
            argv = m.call_args[0][0]
            self.assertEqual(argv[:2], ["ssh", "somehost"])

    def test_copy_to_remote_uses_scp(self):
        with mock.patch("repo_adapter.subprocess.run") as m:
            R._copy_to("somehost", "/local/f", "/remote/f", timeout=10)
            argv = m.call_args[0][0]
            self.assertEqual(argv[0], "scp")
            self.assertIn("somehost:/remote/f", argv)


# ══════════════════════════════════════════════════════════════════════════════
# 补 Med —— local 主流程沙盒 + 6 处替换点接线断言（现有 16 测只验配置/原语，未跑 run_new_example()）
# 都走 local（host=None），不碰真 NPU：仅**编排步**（跑 run_on_npu.sh 那次 _shell）被 stub 成
# 回双哨兵 + 伪造 out.bin；**部署（tar/_copy_to）与拉回（_copy_from）真跑本机 cp**，证 local 链路真通。
# ══════════════════════════════════════════════════════════════════════════════

def _mk_local_sandbox():
    """建 local 模式沙盒：work/rroot/ops/opp/home 五个互不相交的目录（均在同一 base 下作兄弟，
    commonpath=base≠任一，故两两不相交），用 gen_cases 造 **1 个 fp32 小 Sign case** 落进 work，
    另放一份 user runner（让 find_runner 命中"用户"来源、无 builtin 样例告警）。
    返回 dict(base, work, rroot, ops, opp, home, caseset, cid, env)。"""
    base = tempfile.mkdtemp()
    d = {k: os.path.join(base, k) for k in ("work", "rroot", "ops", "opp", "home")}
    for p in d.values():
        os.makedirs(p)
    # 1 个 fp32 小 case：从权威 sign.spec.json 起、把 dtype 收窄到 float32（避开 run_new_example 的
    # int/bf16 Track C 拦截），gen 到 scratch 再挑第一条搬进 work（保持 work 只含这一个 case 目录）。
    with open(_SIGN_SPEC, encoding="utf-8") as f:
        sp = json.load(f)
    for p in sp["params"]:
        p["dtype"] = ["float32"]
    scratch = os.path.join(base, "scratch")
    cs = GC.gen_cases(sp, scratch)
    c = cs["cases"][0]
    cid = c["id"]
    shutil.copytree(os.path.join(scratch, cid), os.path.join(d["work"], cid))
    caseset = {"op": cs["op"], "attr_order": cs.get("attr_order", []), "cases": [c]}
    # user runner：证 _copy_to 部署 runner，且避免 builtin_sample 告警噪声
    opdir = os.path.join(d["home"], ".oprunway", "ops", "Sign")
    os.makedirs(opdir)
    with open(os.path.join(opdir, "oprunway_sign_runner.cpp"), "w", encoding="utf-8") as f:
        f.write("// stub runner\n")
    d["base"], d["caseset"], d["cid"] = base, caseset, cid
    # OPRUNWAY_WORK_DIR=home → find_runner 的 ops_root 落 home/.oprunway/ops（不在插件树内）
    d["env"] = {"OPRUNWAY_TARGET": "local", "OPRUNWAY_REMOTE_DIR": d["rroot"],
                "OPRUNWAY_OPS_REPO": d["ops"], "OPRUNWAY_OPP": d["opp"],
                "OPRUNWAY_WORK_DIR": d["home"]}
    return d


def _fake_orch_shell(work_dir, rroot, cid, n=1):
    """repo_adapter._shell 的 side_effect：
      · **编排步**（脚本含 run_on_npu.sh）→ 不跑真 NPU：伪造 out.bin（= golden 字节，完美 NPU）落
        rroot/cases/<cid>/ 供拉回，并回 returncode=0 + 双哨兵（OPRUNWAY_DONE / OPRUNWAY_NPU_DONE）；
      · **部署步**（rm/mkdir/tar 那次）→ 委托 _REAL_SHELL 真跑本机 bash（证 local 部署链路真通）。"""
    def fake(host, script, *, input=None, timeout, check, capture=False):
        if "run_on_npu.sh" in script:
            g = np.load(os.path.join(work_dir, cid, "golden.npy")).astype(np.float32)
            od = os.path.join(rroot, "cases", cid)
            os.makedirs(od, exist_ok=True)
            g.tofile(os.path.join(od, "out.bin"))
            return subprocess.CompletedProcess(
                [], 0, stdout=f"OPRUNWAY_DONE total={n} ok={n} fail=0\nOPRUNWAY_NPU_DONE\n", stderr="")
        return _REAL_SHELL(host, script, timeout=timeout, check=check, capture=capture)
    return fake


class LocalRunNewExampleFlowTest(unittest.TestCase):
    """组 A · local 主流程沙盒：部署(tar/_copy_to)与拉回(_copy_from)真跑本机 cp，仅编排步 stub。
    证 local 部署→执行→拉回**全链**真能走通，而非只测了原语。"""

    def setUp(self):
        self.sb = _mk_local_sandbox()

    def tearDown(self):
        shutil.rmtree(self.sb["base"], ignore_errors=True)

    def test_local_deploy_execute_pullback_roundtrip(self):
        sb = self.sb
        with mock.patch.dict(os.environ, sb["env"]), \
             mock.patch("repo_adapter._shell",
                        side_effect=_fake_orch_shell(sb["work"], sb["rroot"], sb["cid"])):
            res = R.run_new_example(sb["caseset"], sb["work"])
        cid = sb["cid"]
        # ① 用例 bin 真被部署到 rroot/cases/<cid>/x1.bin（真 tar + 本机 cp，非 mock）
        self.assertTrue(os.path.exists(os.path.join(sb["rroot"], "cases", cid, "x1.bin")),
                        "用例 bin 未部署到 rroot/cases")
        # runner 也经 _copy_to 落到 rroot（部署链路一并证）
        self.assertTrue(os.path.exists(os.path.join(sb["rroot"], "oprunway_sign_runner.cpp")),
                        "runner 未部署到 rroot")
        # ② 假 out.bin 能被拉回本地 work_dir/<cid>/out.bin（真本机 cp）
        self.assertTrue(os.path.exists(os.path.join(sb["work"], cid, "out.bin")),
                        "out.bin 未被拉回本地")
        # 全链真跑通 → evidence 成形、精度 bad_count=0（完美 NPU = golden）
        self.assertEqual(res["repo_mode"], "new_example")
        self.assertEqual(res["runner_source"], "user")
        self.assertEqual(len(res["evidence"]), 1)
        self.assertEqual(res["evidence"][0]["precision"]["metrics"]["bad_count"], 0)


class LocalRunNewExampleWiringTest(unittest.TestCase):
    """组 B · 接线断言：mock 三原语跑一次 local run_new_example（成功哨兵），证 6 处 ssh/scp 替换点
    **确实走三原语**、且 local 模式每处 host=None（没漏出 ssh/scp）。"""

    def setUp(self):
        self.sb = _mk_local_sandbox()

    def tearDown(self):
        shutil.rmtree(self.sb["base"], ignore_errors=True)

    def test_replacement_points_route_through_primitives_with_host_none(self):
        sb = self.sb
        # _shell 用 side_effect（编排 stub + 部署委托真跑）；_copy_to/_copy_from 用 wraps=真函数
        # （既记录调用、又真本机 cp，让流程能走完到拉回/采集）。
        with mock.patch.dict(os.environ, sb["env"]), \
             mock.patch("repo_adapter._shell",
                        side_effect=_fake_orch_shell(sb["work"], sb["rroot"], sb["cid"])) as m_shell, \
             mock.patch("repo_adapter._copy_to", wraps=R._copy_to) as m_to, \
             mock.patch("repo_adapter._copy_from", wraps=R._copy_from) as m_from:
            R.run_new_example(sb["caseset"], sb["work"])
        # ── 按调用次数逐点验（弱断言"至少调用一次"抓不到"漏换某一处"，codex verify 指出）──
        # run_new_example 的 6 处传输替换点，按 1 个 case（cid）预期：
        #   _shell   ×2：① unpack（解 tar 到 rroot/cases）② orchestrate（跑 run_on_npu.sh）
        #   _copy_to ×3：① deploy tar → /tmp ② runner.cpp → rroot ③ run_on_npu.sh → rroot
        #   _copy_from ×2：① 该 case 的 out.bin ② perf_result.txt（1 个 case）
        n_cases = len(sb["caseset"]["cases"])
        self.assertEqual(m_shell.call_count, 2,
                         f"_shell 应 2 次(unpack+orchestrate)，实 {m_shell.call_count}——某处 shell 漏走三原语")
        self.assertEqual(m_to.call_count, 3,
                         f"_copy_to 应 3 次(tar+runner+npu_sh)，实 {m_to.call_count}——某处上传漏走三原语")
        self.assertEqual(m_from.call_count, n_cases + 1,
                         f"_copy_from 应 {n_cases + 1} 次(每 case out.bin + perf_result)，实 {m_from.call_count}")
        # 关键路径逐点验：_copy_to 的目标确含 runner 与 run_on_npu.sh；_copy_from 的源确含 out.bin
        to_targets = [c.args[2] for c in m_to.call_args_list]      # (host, local, remote)
        self.assertTrue(any(t.endswith("run_on_npu.sh") for t in to_targets), "run_on_npu.sh 未经 _copy_to 上传")
        self.assertTrue(any("runner" in os.path.basename(t) for t in to_targets), "runner.cpp 未经 _copy_to 上传")
        from_srcs = [c.args[1] for c in m_from.call_args_list]     # (host, remote, local)
        self.assertTrue(any(s.endswith("out.bin") for s in from_srcs), "out.bin 未经 _copy_from 拉回")
        # 每处 host（首位置实参）都是 None —— 证 local 模式没漏出 ssh/scp
        for m, label in ((m_shell, "_shell"), (m_to, "_copy_to"), (m_from, "_copy_from")):
            for call in m.call_args_list:
                self.assertIsNone(call.args[0],
                                  f"{label} 的 host 实参非 None（local 模式漏出 ssh/scp）")


class LocalIntersectionGuardTest(unittest.TestCase):
    """组 C · Med#2 守卫正向测试：local 模式下 rroot/ops/opp 与 work_dir 相交时 run_new_example raise
    （§部署会对 rroot/cases 执行 rm -rf，相交会静默删用户产物）。"""

    def setUp(self):
        self.sb = _mk_local_sandbox()

    def tearDown(self):
        shutil.rmtree(self.sb["base"], ignore_errors=True)

    def _expect_intersection_raise(self, override):
        sb = self.sb
        env = dict(sb["env"]); env.update(override)
        with mock.patch.dict(os.environ, env):
            with self.assertRaises(ValueError) as cm:
                R.run_new_example(sb["caseset"], sb["work"])
        self.assertIn("相交", str(cm.exception))

    def test_rroot_equal_work_dir_rejected(self):
        self._expect_intersection_raise({"OPRUNWAY_REMOTE_DIR": self.sb["work"]})

    def test_ops_under_work_dir_rejected(self):
        self._expect_intersection_raise(
            {"OPRUNWAY_OPS_REPO": os.path.join(self.sb["work"], "ops_sub")})

    def test_work_dir_under_rroot_rejected(self):
        # 反向：work_dir 落在 rroot 之内也须拒（双向不相交）。rroot=base，work=base/work → 相交。
        self._expect_intersection_raise({"OPRUNWAY_REMOTE_DIR": self.sb["base"]})


if __name__ == "__main__":
    unittest.main(verbosity=2)
