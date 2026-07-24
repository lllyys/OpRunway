"""真机配置 + 传输层单测（stdlib unittest）——不依赖真机/ssh。

跑: python3 test_ne_transport.py   或   python3 -m unittest test_ne_transport -v

守的契约：
- `_ne_cfg` **无私有默认值**：机器名 / 远端路径 / 被测仓路径缺失即报错，绝不用某台私有机兜底
  （否则别人拿到插件默认连一台不存在的机器、找不存在的路径）；
- 两种传输模式：`OPRUNWAY_TARGET=local`（本机直连，无 ssh/scp）/ `remote`（默认，ssh）；
  remote 缺 `OPRUNWAY_SSH_HOST` 报错，local 不需要 host；
- 传输原语 `_copy_to`/`_copy_from`/`_shell` 在 local（host=None）模式走本机 cp/bash、不碰 ssh。
- **输出形状（契约 C1）**：`run_new_example` 不再硬拒「输出 ≠ 输入广播形状」——算子在 `golden.py` 声明了
  `out_shape()`（caseset 记 `expected.out_shape` + `out_shape_source="golden.out_shape"`）时按声明形状
  分配/校验输出、manifest 补一组输入维度；**未声明时旧硬校验原样保留**（漂移照拒）。
- **manifest attr 编码**：`list[int]` → 逗号连接的**单** token（契约 C2）；空数组/含空白/None 一律 fail-closed。
"""
import inspect, json, os, shutil, subprocess, tempfile, unittest
from unittest import mock

import numpy as np
import gen_cases as GC
import precision_policy as PP
import repo_adapter as R
import _golden_fixture as _gf   # golden 去引擎化：沙盒 ops_root 放 golden.py 供 gen_cases 加载（ADR 0011）

_HERE = os.path.dirname(os.path.abspath(__file__))
_SIGN_SPEC = os.path.join(_HERE, "..", "samples", "specs", "sign.spec.json")
_REAL_SHELL = R._shell   # 原始 _shell（任何 patch 之前捕获）：fake_shell 让"部署步"委托真跑本机 bash

# 一份"齐全"的 remote env（各用例按需删字段来触发缺失报错）
_FULL_REMOTE = {
    "OPRUNWAY_TARGET": "remote",
    "OPRUNWAY_SSH_HOST": "somehost",
    "OPRUNWAY_REMOTE_DIR": "/remote/run",
    "OPRUNWAY_OPS_REPO": "/remote/ops-repo",
    "OPRUNWAY_OPP": "/remote/opp",
    "OPRUNWAY_OP_SRC": "experimental/math/is_close",   # provenance：被测 op 源子路径（必填）
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

    def test_vendor_suffix_default_empty_backward_compatible(self):
        """批 6b：未给 OPRUNWAY_VENDOR_SUFFIX → 空串（sentinel=沿用现行为·shell 走仓名正则），不报错。"""
        self.assertEqual(_cfg(_FULL_REMOTE)["vendor_suffix"], "")

    def test_vendor_suffix_passed_through(self):
        """给了 → 原样带进 cfg（供 env-export 显式导出，catlass/非 ops-<族> 仓靠它）。"""
        env = dict(_FULL_REMOTE, OPRUNWAY_VENDOR_SUFFIX="cv")
        self.assertEqual(_cfg(env)["vendor_suffix"], "cv")

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
        env = {"OPRUNWAY_TARGET": "local", "OPRUNWAY_REMOTE_DIR": "/work",
               "OPRUNWAY_OPS_REPO": "/ops", "OPRUNWAY_OPP": "/opp",
               "OPRUNWAY_OP_SRC": "experimental/math/is_close"}
        c = _cfg(env)
        self.assertEqual(c["target"], "local")
        self.assertIsNone(c["host"])

    def test_op_src_required(self):
        """provenance：OPRUNWAY_OP_SRC 缺失 → _ne_cfg fail-fast（绑源必填、防恒定空 hash 未绑源）。"""
        env = dict(_FULL_REMOTE); env.pop("OPRUNWAY_OP_SRC")
        with self.assertRaises(ValueError):
            _cfg(env)

    def test_op_src_unsafe_path_rejected(self):
        """provenance：OPRUNWAY_OP_SRC 前导 / 或含 .. 或非法字符 → 拒（防路径逃逸/注入）。"""
        for bad in ("/abs/path", "../escape", "math/is_close;rm -rf", "math/$(x)"):
            env = dict(_FULL_REMOTE); env["OPRUNWAY_OP_SRC"] = bad
            with self.assertRaises(ValueError):
                _cfg(env)

    def test_op_src_repo_root_or_bare_subtree_rejected(self):
        """provenance：`.`/`./`/裸子树根/`.`段/尾斜杠 → 拒。否则 run_on_npu.sh 会把 OPHASH 绑整仓、跳
        --experimental、且 provenance 非算子专属 → 跨算子复用异源 opp 假通过（与 OP_SRC 空同类洞、走另一门）。"""
        for bad in (".", "./", "experimental", "math", "./math/is_close", "math/is_close/",
                    "math//is_close", "experimental/./is_close", "experimental/../math/is_close"):
            env = dict(_FULL_REMOTE); env["OPRUNWAY_OP_SRC"] = bad
            with self.assertRaises(ValueError, msg=f"op_src={bad!r} 应被拒"):
                _cfg(env)

    def test_op_src_valid_nested_accepted(self):
        """provenance：合法嵌套算子源路径（≥2 段、canonical）不被误伤。"""
        for good in ("experimental/math/is_close", "math/is_close"):
            env = dict(_FULL_REMOTE); env["OPRUNWAY_OP_SRC"] = good
            self.assertEqual(_cfg(env)["op_src"], good)

    def test_opp_rebuild_default_off(self):
        """OPRUNWAY_OPP_REBUILD 缺省 = '0'（不授权重建）；显式 '1' 才透传授权。"""
        self.assertEqual(_cfg(dict(_FULL_REMOTE))["opp_rebuild"], "0")
        env = dict(_FULL_REMOTE); env["OPRUNWAY_OPP_REBUILD"] = "1"
        self.assertEqual(_cfg(env)["opp_rebuild"], "1")

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
    另放一份 user runner（让 find_runner 命中"用户"来源；fallback 已退役、无此 runner 则 find_runner 直接报错）。
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
    # 沙盒 ops_root（home/.oprunway/ops/Sign）放 golden.py（gen_cases 加载，ADR 0011）+ user runner（find_runner 命中）
    opdir = os.path.join(d["home"], ".oprunway", "ops", "Sign")
    os.makedirs(opdir)
    _gf.place_golden(os.path.join(d["home"], ".oprunway", "ops"), "Sign")
    with open(os.path.join(opdir, "oprunway_sign_runner.cpp"), "w", encoding="utf-8") as f:
        f.write("// stub runner\n")
    scratch = os.path.join(base, "scratch")
    with mock.patch.dict(os.environ, {"OPRUNWAY_WORK_DIR": d["home"]}):   # gen_cases 用沙盒 ops_root 加载 golden
        cs = GC.gen_cases(sp, scratch)
    # §1 后 cases[0] 是空 Tensor na 用例（§1.4 特殊场景排在前、无 metrics）——挑**常规网格**非 na 小 case，
    # 保证有真精度 metrics（transport roundtrip 要证 bad_count=0）。
    c = next(x for x in cs["cases"]
             if x["expected"].get("compare") != "na" and ("常规" in x.get("tags", [])))
    cid = c["id"]
    shutil.copytree(os.path.join(scratch, cid), os.path.join(d["work"], cid))
    caseset = {"op": cs["op"], "attr_order": cs.get("attr_order", []), "cases": [c]}
    d["base"], d["caseset"], d["cid"] = base, caseset, cid
    # OPRUNWAY_WORK_DIR=home → find_runner 的 ops_root 落 home/.oprunway/ops（不在插件树内）
    d["env"] = {"OPRUNWAY_TARGET": "local", "OPRUNWAY_REMOTE_DIR": d["rroot"],
                "OPRUNWAY_OPS_REPO": d["ops"], "OPRUNWAY_OPP": d["opp"],
                "OPRUNWAY_OP_SRC": "experimental/math/is_close",   # provenance：被测 op 源子路径（必填）
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
        # 批 6b：orchestrate 那次 _shell 的 script 确含 VENDOR_SUFFIX 的 export（证断头配置已接回）。
        orch = next(c.args[1] for c in m_shell.call_args_list if "run_on_npu.sh" in c.args[1])
        self.assertIn("export OPRUNWAY_VENDOR_SUFFIX=", orch,
                      "env-export 未导出 OPRUNWAY_VENDOR_SUFFIX——断头配置没接回")
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


# ══════════════════════════════════════════════════════════════════════════════
# 组 D · 契约 C1（输出形状）+ C2（list[int] attr）在 run_new_example 的落地。
# **不经 gen_cases / golden.py**（本机无 torch，那条路恒红）——caseset 全手搓，只测 repo_adapter 自己那段：
# 输入/输出形状分开算、golden 按「真正会被读回的输出形状」校、manifest 行格式、attr token 编码。
# ══════════════════════════════════════════════════════════════════════════════

def _mk_expected(cid, dtype="float32"):
    """手搓一份与 gen_cases 同构的 `expected`（数值 fp32 路径）。"""
    std = PP.effective_standard(PP.ASCENDOPTEST_DEFAULT, dtype, "rel_err")
    pol = PP.threshold_for(std, dtype)
    return {"golden_source": "numpy analytical", "golden_path": f"{cid}/golden.npy",
            "verify_mode": "approx", "standard": std, "compare": "rel_err",
            "compare_dtype": dtype, "tolerance_policy_id": PP.tolerance_policy_id(std, dtype),
            "policy": pol, "threshold": PP.threshold_digest(pol)}


def _mk_case(work, cid, in_shapes, out_shape, *, declared=False, attrs=None, golden_shape=None):
    """在 work 下落 x{j}.npy / golden.npy，返回一条 caseset case。

    `declared=True` → 标 `out_shape_source="golden.out_shape"`（算子自己声明了输出形状）；
    否则标 `golden_fn_actual`（gen_cases 从 golden 实测填的，elementwise 缺省语义）。
    `golden_shape` 可与 `out_shape` 不同 —— 专供「声明与 golden 打架应被拒」的负例。
    """
    os.makedirs(os.path.join(work, cid), exist_ok=True)
    inputs = []
    for j, s in enumerate(in_shapes):
        np.save(os.path.join(work, cid, f"x{j + 1}.npy"), np.zeros(s, dtype=np.float32))
        inputs.append({"name": f"x{j + 1}", "shape": list(s), "dtype": "float32",
                       "path": f"{cid}/x{j + 1}.npy"})
    np.save(os.path.join(work, cid, "golden.npy"),
            np.zeros(golden_shape if golden_shape is not None else out_shape, dtype=np.float32))
    exp = _mk_expected(cid)
    exp["out_shape"] = list(out_shape)
    exp["out_shape_source"] = "golden.out_shape" if declared else "golden_fn_actual"
    return {"id": cid, "dims": ["功能"], "tags": ["常规"], "inputs": inputs,
            "attrs": dict(attrs or {}), "expected": exp}


def _fake_orch(work_dir, rroot, cids):
    """编排步 stub：按 golden 字节伪造 out.bin（完美 NPU）+ 回双哨兵；部署步委托真跑本机 bash。"""
    def fake(host, script, *, input=None, timeout, check, capture=False):
        if "run_on_npu.sh" in script:
            for cid in cids:
                g = np.load(os.path.join(work_dir, cid, "golden.npy"))
                od = os.path.join(rroot, "cases", cid)
                os.makedirs(od, exist_ok=True)
                np.ascontiguousarray(g).tofile(os.path.join(od, "out.bin"))
            n = len(cids)
            return subprocess.CompletedProcess(
                [], 0, stdout=f"OPRUNWAY_DONE total={n} ok={n} fail=0\nOPRUNWAY_NPU_DONE\n", stderr="")
        return _REAL_SHELL(host, script, timeout=timeout, check=check, capture=capture)
    return fake


class _NeSandboxBase(unittest.TestCase):
    """local 沙盒基类（自身无用例）：五个互不相交的目录 + 一份 stub runner + local env。"""

    OP = "Foo"

    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.d = {k: os.path.join(self.base, k) for k in ("work", "rroot", "ops", "opp", "home")}
        for p in self.d.values():
            os.makedirs(p)
        opdir = os.path.join(self.d["home"], ".oprunway", "ops", self.OP)
        os.makedirs(opdir)
        with open(os.path.join(opdir, f"oprunway_{self.OP.lower()}_runner.cpp"), "w",
                  encoding="utf-8") as f:
            f.write("// stub runner\n")
        self.env = {"OPRUNWAY_TARGET": "local", "OPRUNWAY_REMOTE_DIR": self.d["rroot"],
                    "OPRUNWAY_OPS_REPO": self.d["ops"], "OPRUNWAY_OPP": self.d["opp"],
                    "OPRUNWAY_OP_SRC": "experimental/math/foo",
                    "OPRUNWAY_WORK_DIR": self.d["home"]}

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def _run(self, cases, attr_order=()):
        cs = {"op": self.OP, "attr_order": list(attr_order), "cases": cases}
        cids = [c["id"] for c in cases]
        with mock.patch.dict(os.environ, self.env), \
             mock.patch("repo_adapter._shell", side_effect=_fake_orch(self.d["work"], self.d["rroot"], cids)):
            return R.run_new_example(cs, self.d["work"])

    def _manifest(self):
        with open(os.path.join(self.d["work"], "manifest.txt"), encoding="utf-8") as f:
            return [ln for ln in f.read().splitlines() if ln.strip()]


class OutShapeContractTest(_NeSandboxBase):
    """C1：输出形状显式声明优先、缺省退回同形假设；两支的校验都在（放开 ≠ 不校）。"""

    def test_elementwise_manifest_stays_legacy_format(self):
        """回归（真机已验证过的格式）：算子未声明 out_shape → 行仍是 `cid dtype ndim dims…`、一组 dims。"""
        c = _mk_case(self.d["work"], "c1", [(2, 3)], (2, 3))
        res = self._run([c])
        self.assertEqual(self._manifest(), ["c1 float32 2 2 3"])
        self.assertEqual(res["evidence"][0]["precision"]["metrics"]["bad_count"], 0)

    def test_declared_out_shape_no_longer_hard_rejected(self):
        """核心：输出 (1,1,4,4) ≠ 输入 (1,1,2,2) —— 旧代码在 golden 校验处直接 raise，现应跑通。"""
        c = _mk_case(self.d["work"], "up1", [(1, 1, 2, 2)], (1, 1, 4, 4), declared=True)
        res = self._run([c])
        self.assertEqual(len(res["evidence"]), 1)
        self.assertEqual(res["evidence"][0]["status"], "ok")
        self.assertEqual(res["evidence"][0]["precision"]["metrics"]["numel"], 16)

    def test_declared_out_shape_extends_manifest_and_keeps_input_bytes(self):
        """扩展行 = `cid dtype [attr…] out_ndim o… in_ndim i…`；x1.bin 按**输入**形状落，不再广播到输出。"""
        c = _mk_case(self.d["work"], "up1", [(1, 1, 2, 2)], (1, 1, 4, 4), declared=True)
        self._run([c])
        self.assertEqual(self._manifest(), ["up1 float32 4 1 1 4 4 4 1 1 2 2"])
        self.assertEqual(os.path.getsize(os.path.join(self.d["work"], "up1", "x1.bin")),
                         4 * np.dtype(np.float32).itemsize)      # 输入 4 元素，不是输出的 16

    def test_manifest_format_is_per_caseset_not_per_case(self):
        """格式按整份 caseset 定：声明了 out_shape 的算子，**恰好同形**的那条 case 也走扩展行（口径不摇摆）。"""
        cases = [_mk_case(self.d["work"], "same", [(2, 2)], (2, 2), declared=True),
                 _mk_case(self.d["work"], "diff", [(2, 2)], (4, 4), declared=True)]
        self._run(cases)
        self.assertEqual(self._manifest(), ["same float32 2 2 2 2 2 2", "diff float32 2 4 4 2 2 2"])

    def test_undeclared_shape_drift_still_rejected(self):
        """未声明 out_shape 却出现「输出 ≠ 输入广播」→ 契约漂移，照拒（旧硬校验没被删掉）。"""
        c = _mk_case(self.d["work"], "bad", [(1, 1, 2, 2)], (1, 1, 4, 4), declared=False)
        with self.assertRaises(ValueError) as cm:
            self._run([c])
        self.assertIn("契约漂移", str(cm.exception))

    def test_golden_disagreeing_with_declared_shape_rejected(self):
        """声明 (1,1,4,4) 但 golden 落的是 (1,1,2,2) → 拒（metrics 不能拿错东西算）。"""
        c = _mk_case(self.d["work"], "bad", [(1, 1, 2, 2)], (1, 1, 4, 4), declared=True,
                     golden_shape=(1, 1, 2, 2))
        with self.assertRaises(ValueError) as cm:
            self._run([c])
        self.assertIn("golden", str(cm.exception))

    def test_inconsistent_duplicate_declarations_rejected(self):
        """同一条 case 在两处声明了不同的输出形状 → 拒挑一个信（fail-closed）。"""
        c = _mk_case(self.d["work"], "c1", [(2, 3)], (2, 3))
        c["out_shape"] = [3, 2]                     # 与 expected.out_shape 打架
        with self.assertRaises(ValueError) as cm:
            self._run([c])
        self.assertIn("不一致", str(cm.exception))

    def test_defect_injection_refused_on_real_path(self):
        """C5：真机是验收路径，绝不接受 defect 注入（进 _ne_cfg 之前就拒，故无需 env）。"""
        with self.assertRaises(ValueError) as cm:
            R.run_new_example({"op": self.OP, "cases": []}, self.d["work"], defect_cases=["x"])
        self.assertIn("defect", str(cm.exception))


class ManifestAttrTokenTest(unittest.TestCase):
    """C2：attr 值 → manifest **单** token 的编码；不可编码的一律 fail-closed（不产坏 manifest）。"""

    def test_scalar_tokens(self):
        self.assertEqual(R._manifest_attr_token(True, "equal_nan", "c1"), "1")
        self.assertEqual(R._manifest_attr_token(False, "equal_nan", "c1"), "0")
        self.assertEqual(R._manifest_attr_token(3, "n", "c1"), "3")
        self.assertEqual(R._manifest_attr_token(1e-05, "rtol", "c1"), "1e-05")

    def test_int_list_is_one_comma_joined_token(self):
        """`str([4, 4])` 会带空格、把一个 token 撑成两个 → 必须是 `4,4`。"""
        tok = R._manifest_attr_token([4, 4], "output_size", "c1")
        self.assertEqual(tok, "4,4")
        self.assertNotIn(" ", tok)

    def test_unencodable_attrs_rejected(self):
        for bad in ([], [1.5], [True], None, {"a": 1}, "a b", ""):
            with self.assertRaises(ValueError, msg=f"未拒 {bad!r}"):
                R._manifest_attr_token(bad, "k", "c1")


class ListAttrInManifestTest(_NeSandboxBase):
    """C2 端到端：list[int] attr 进 manifest 行（复用上面的 local 沙盒）。"""

    def test_list_attr_lands_as_single_token(self):
        c = _mk_case(self.d["work"], "up1", [(1, 1, 2, 2)], (1, 1, 4, 4), declared=True,
                     attrs={"output_size": [4, 4]})
        self._run([c], attr_order=["output_size"])
        self.assertEqual(self._manifest(), ["up1 float32 4,4 4 1 1 4 4 4 1 1 2 2"])

    def test_empty_list_attr_rejected_end_to_end(self):
        c = _mk_case(self.d["work"], "up1", [(1, 1, 2, 2)], (1, 1, 4, 4), declared=True,
                     attrs={"output_size": []})
        with self.assertRaises(ValueError):
            self._run([c], attr_order=["output_size"])

class VendorSuffixDerivationTest(unittest.TestCase):
    """`run_on_npu.sh` 的 vendor 目录后缀：**推导 + fail-closed**，不再写死 `_math`。

    为什么要改（2026-07-23）：算子仓 build.sh 产出的是 `${VENDOR}_<仓族>`——ops-math→`_math`、
    ops-cv→`_cv`。脚本里写死 `_math` 既违反本仓「零硬编码」约定，也让 **ops-cv 的算子
    （Upsample 系）真机跑必撞**。

    ⚠ 本类**真的执行 run_on_npu.sh**，不复制它的 sed 表达式自己算一遍——
    那样脚本哪怕忘了 `exit`，测试也照样绿（「脚本据此 fail-closed」就成了空口声称）。
    脚本无需真机即可跑到这一步：vendor 后缀解析在任何 ssh/编译动作之前。"""

    _SH = os.path.join(_HERE, "new_example", "run_on_npu.sh")

    def _run(self, ops_dirname, **extra):
        """用最小合法 env 起脚本，返回 CompletedProcess。ops 仓只建目录名、不需内容。"""
        d = tempfile.mkdtemp()
        ops = os.path.join(d, ops_dirname)
        os.makedirs(ops, exist_ok=True)
        env = dict(os.environ,
                   OPRUNWAY_OPS_REPO=ops, OPRUNWAY_OPP=os.path.join(d, "opp"),
                   OPRUNWAY_RUN_DIR=os.path.join(d, "run"), OPRUNWAY_SOC="ascend910b",
                   OPRUNWAY_VENDOR="oprunway", OPRUNWAY_RUNNER="r.cpp",
                   OPRUNWAY_OPNAME="Foo", OPRUNWAY_OP_SRC="experimental/math/foo")
        env.pop("OPRUNWAY_VENDOR_SUFFIX", None)
        env.update(extra)
        os.makedirs(env["OPRUNWAY_OPP"], exist_ok=True)
        os.makedirs(env["OPRUNWAY_RUN_DIR"], exist_ok=True)
        # ⚠ errors="replace"：脚本源码本身是正确 UTF-8（已按字节核过），
        # 但本机 bash 的 locale 可能把中文标点截断成坏字节——那是环境噪声，不是仓里的问题，
        # 不能让它把测试搞崩。断言只匹配 ASCII 变量名与中文关键词，不依赖标点完整。
        env.setdefault("LC_ALL", "C.UTF-8")
        return subprocess.run(["bash", self._SH], env=env, capture_output=True,
                              text=True, errors="replace")

    def test_undecidable_repo_name_fails_closed_with_actionable_message(self):
        """仓名推不出族名（如 `catlass`）→ **exit 3 停下**，并告诉用户该设哪个变量。"""
        r = self._run("catlass")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("推不出 vendor 目录后缀", r.stderr)
        self.assertIn("OPRUNWAY_VENDOR_SUFFIX", r.stderr)      # 给出可操作的出路

    def test_derivable_repo_names_pass_the_suffix_gate(self):
        """`ops-cv` / `ops-math` / `cann-ops-blas` 能推出族名 → **越过后缀闸**，
        停在后面的闸（op 源目录不存在）而不是这一道。证推导真生效、不是恰好都被拦下。"""
        for dirname in ("ops-cv", "ops-math", "cann-ops-blas"):
            r = self._run(dirname)
            self.assertNotIn("推不出 vendor 目录后缀", r.stderr, dirname)
            self.assertIn("op 源目录不存在", r.stderr, f"{dirname} 应走到下一道闸：{r.stderr[:200]}")

    def test_explicit_suffix_overrides_undecidable_name(self):
        """显式 `OPRUNWAY_VENDOR_SUFFIX` 能救推不出来的仓名（catlass 这类）。"""
        r = self._run("catlass", OPRUNWAY_VENDOR_SUFFIX="math")
        self.assertNotIn("推不出 vendor 目录后缀", r.stderr)

    def test_illegal_suffix_rejected(self):
        """后缀含非法字符 → fail-closed（防被拼进路径）。"""
        r = self._run("ops-math", OPRUNWAY_VENDOR_SUFFIX="../evil")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("含非法字符", r.stderr)

    def test_script_has_no_hardcoded_math_suffix(self):
        """源码级钉子（辅助，非主证据）：不得再出现写死的 `_math` vendor 路径。"""
        with open(self._SH, encoding="utf-8") as f:      # with：别留句柄（ResourceWarning）
            src = f.read()
        self.assertNotIn("vendors/${VEN}_math", src)
        self.assertIn("OPRUNWAY_VENDOR_SUFFIX", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
