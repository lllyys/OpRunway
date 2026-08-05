#!/usr/bin/env python3
"""`make_vendor_build_receipt` 的契约测试 —— 产出方必须与三处消费者逐字对得上。

这份收据的全部价值是「机器可核」：它声称 vendor `.so` 构建自某一份被测源码。
所以本文件盯的不是「脚本能不能跑通」，而是**每一条声称背后到底有没有校验**：

  · `build.returncode: 0` 是实跑来的，不是抄进 JSON 的一句自报（人手写那份收据正是这么来的）；
  · `--build-cwd` 真的就是被指纹的那棵树（本地通路唯一能核的一环，PR 通路没有对照物）；
  · 产出的收据能原样穿过 adapter / 判别式 / 三级门，改一个字节就被三级门拦下。

铁律是 fail-open 最贵：任何「校验没开火却照样产出收据」都算红。因此负路用例一律
同时断言**收据文件不存在**——报了错却留下半份收据，下游照样拿得到。

不打真网络、不真 build（`--` 后面挂的是 `python -c`）、不 import torch/numpy。
"""

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

import content_address as ca
import dut_source as ds
import fetch_source as fs
import make_vendor_build_receipt as mk


_DOMAIN = "oprunway/source-facts/v1"


def _sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as src:
        h.update(src.read())
    return h.hexdigest()


def _ok_build(library, marker=None):
    """一条**真会被执行**、且**真会产出 `--library`** 的成功构建命令。

    用 `sys.executable` 而不是 `true`/`sh`：跨平台可用，且能让「构建脚本确实在
    `--build-cwd` 下跑过」变成一条可断言的事实（marker 落在哪个目录说明 cwd 是哪个）。

    ⚠ 它**必须真的写出 `--library`**：产出方要求那个 `.so` 在 build 前后发生变化，
    否则收据声称的「构建出的产物」与这次构建毫无因果关系（`-- /usr/bin/true` 配一个
    预先存在的 CANN 内置 ELF 就能产出完整收据）。夹具里让 build 自己写出那个文件，
    正是真机上 `build.sh … && *.run --install-path=…` 的形状。
    """
    body = (f"import os; open({library!r}, 'wb')"
            f".write(b'\\x7fELF-built-' + os.urandom(8))")
    if marker:
        body += f"; open({marker!r}, 'w').close()"
    return [sys.executable, "-c", body]


def _noop_build():
    """跑得通但**什么产物都不留**的构建——用于「build 成功却没有 `.so`」这类负路。"""
    return [sys.executable, "-c", "pass"]


def _fail_build(code=3):
    return [sys.executable, "-c", f"raise SystemExit({code})"]


class _Base(unittest.TestCase):
    """公共夹具：一棵被测算子子树、一个装在子树**之外**的 vendor `.so`、一个收据落点。

    ⚠ `.so` 刻意不放进 `op_subdir`：`root_digest` 覆盖整个子树，把构建产物塞进去
    就等于让「被测源码身份」跟着构建产物变，夹具自己先制造出漂移。
    """

    OP_SUBDIR = "experimental/index/median"

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.repo = os.path.join(self.d, "ops-nn")
        self.out = os.path.join(self.d, "facts")
        self.libdir = os.path.join(self.d, "install")
        self.receipt_path = os.path.join(self.d, "receipt.json")
        os.makedirs(self.out, exist_ok=True)
        os.makedirs(self.libdir, exist_ok=True)
        self.task = os.path.join(self.d, "task.md")
        with open(self.task, "w", encoding="utf-8") as fh:
            fh.write("# Median 任务书\n")
        self.log = ""

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    # ---- 夹具构造 ---------------------------------------------------------------

    def _write(self, rel, text, root=None):
        full = os.path.join(root or self.repo, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)
        return full

    def _seed_op_tree(self, root=None):
        """一棵能被 `fetch_source` 认出 aclnn 两段式的最小被测子树。"""
        self._write(f"{self.OP_SUBDIR}/op_host/op_api/aclnn_median.h",
                    "aclnnStatus aclnnMedianGetWorkspaceSize(const aclTensor*, aclTensor*,"
                    " uint64_t*, aclOpExecutor**);\n", root)
        self._write(f"{self.OP_SUBDIR}/examples/test_aclnn_median.cpp",
                    "int main(){ aclnnMedianGetWorkspaceSize(x, y, &ws, &exe);"
                    " aclnnMedian(ws, exe, stream); }\n", root)
        self._write(f"{self.OP_SUBDIR}/op_host/median_def.cpp", "// op def\n", root)

    def _make_library(self, name="libcust_opapi.so", data=b"\x7fELF-vendor-bytes"):
        path = os.path.join(self.libdir, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    # ---- 调用与读回 -------------------------------------------------------------

    def _run(self, argv):
        """跑 `main()`，吞掉噪声输出（仍留在 `self.log` 里供断言），返回 `(rc, log)`。

        `finally` 里存 log 是为了让 `SystemExit`（argparse 的 `error()`）那条路
        也拿得到 stderr——负路的错误措辞正是要断言的东西。
        """
        buf = io.StringIO()
        try:
            with redirect_stdout(buf), redirect_stderr(buf):
                rc = mk.main(argv)
        finally:
            self.log = buf.getvalue()
        return rc, self.log

    def _argv(self, build_argv, source_facts=None, build_cwd=None, library=None, extra=()):
        return (["--source-facts", source_facts or os.path.join(self.out, "source_facts.json"),
                 "--build-cwd", build_cwd or self.repo,
                 "--library", library if library is not None else self.lib,
                 "--out", self.receipt_path]
                + list(extra) + ["--"] + list(build_argv))

    def _receipt(self):
        with open(self.receipt_path, encoding="utf-8") as src:
            return json.load(src)

    def _quiet_fetch(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            return fs.main(argv)

    def _payload_at(self, out_dir):
        return ca.read_artifact(out_dir, "source_facts.json", _DOMAIN)


@unittest.skipUnless(shutil.which("git"), "需要 git 可执行文件")
class LocalCheckoutReceiptTest(_Base):
    """本地来源通路：锚是 `root_digest`，且**唯一**能核「构建树 ↔ 指纹树」的通路。"""

    REMOTE = "https://gitcode.com/cann/ops-nn.git"

    def setUp(self):
        super().setUp()
        self._seed_op_tree()
        self._init_git()
        self.lib = self._make_library()
        rc = self._quiet_fetch(["--taskdoc", self.task, "--local-repo", self.repo,
                                "--op-subdir", self.OP_SUBDIR, "--base-ref", "master",
                                "--out", self.out])
        self.assertEqual(rc, 0, "夹具本身取材就没走完，后面的断言都不作数")
        self.payload = self._payload_at(self.out)
        self.assertEqual(self.payload["completeness"]["status"], "complete")

    def _git(self, *args):
        return subprocess.run(("git", "-C", self.repo) + args,
                              capture_output=True, text=True, check=True)

    def _init_git(self):
        self._git("init", "-q", "-b", "master")
        self._git("config", "user.email", "t@example.invalid")
        self._git("config", "user.name", "t")
        # remote 是 `derive_repo` 在本地通路的取值处；没有它就该要求显式 --repo（另有用例）
        self._git("remote", "add", "origin", self.REMOTE)
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "base")
        self._git("checkout", "-q", "-b", "feature")
        self._write(f"{self.OP_SUBDIR}/op_host/median_def.cpp", "// op def v2\n")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "change")

    # ---- 正路 -------------------------------------------------------------------

    def test_local_receipt_binds_source_build_and_artifact(self):
        """本地正路：三段（来源 / 构建 / 产物）各自锁到实测事实上。"""
        rc, log = self._run(self._argv(_ok_build(self.lib, "BUILD_RAN")))
        self.assertEqual(rc, 0)
        r = self._receipt()

        self.assertEqual(r["schema"], "oprunway.vendor_build_receipt")
        self.assertEqual(r["schema_version"], 1)
        self.assertEqual(r["status"], "VERIFIED")

        self.assertEqual(r["source"]["dut_source"], ds.LOCAL_CHECKOUT)
        # 锚**逐字**等于 source_facts 的值：产出方不许自己重算一份（两份实现迟早分叉）
        self.assertEqual(r["source"]["local_root_digest"],
                         self.payload["local_checkout"]["root_digest"])
        self.assertNotIn("pr_head_sha", r["source"], "两条通路的锚必须互斥出现")
        self.assertEqual(r["source"]["repo"], self.REMOTE)
        # repo 这一项的**强度**要如实记账：这里是从事实派生的，不是操作者自报的
        self.assertEqual(r["source"]["repo_source"], "local_checkout.git.remote_url")

        # ⭐ `build.returncode` 是**实测**的：marker 文件证明 `--` 后的 argv 真被执行、
        #    且真在 --build-cwd 下执行。人手写那份收据的 `0` 恰恰只是一句自报。
        self.assertEqual(r["build"]["returncode"], 0)
        self.assertEqual(r["build"]["argv"], _ok_build(self.lib, "BUILD_RAN"))
        self.assertEqual(r["build"]["cwd"], os.path.realpath(self.repo))
        self.assertTrue(os.path.isfile(os.path.join(self.repo, "BUILD_RAN")),
                        "build argv 没被真正执行（收据的 returncode 就成了自报）")

        self.assertEqual(r["artifact"]["library_sha256"], _sha_file(self.lib))
        self.assertEqual(r["artifact"]["library_path"], os.path.realpath(self.lib))
        # 夹具里 .so 是 setUp 先建、再被 build 覆盖的，所以这里必须是 True；
        # 「build 从无到有产出」那一支另有用例。
        self.assertIs(r["artifact"]["existed_before_build"], True)
        self.assertIn("构建树与 source_facts 指纹一致", log)
        self.assertIn("--library 在构建窗口内被改写", log)
        self.assertIn("build 没有改动被测子树", log)

    def test_library_produced_from_scratch_is_recorded_as_such(self):
        """`build 从无到有产出 .so` 与「覆盖了一份已存在的」必须在收据里分得开。

        后者更容易混进上一轮的残留，报告里值得看得见。
        """
        fresh = os.path.join(self.libdir, "libcust_opapi.fresh.so")
        rc, _ = self._run(self._argv(_ok_build(fresh), library=fresh))
        self.assertEqual(rc, 0)
        self.assertIs(self._receipt()["artifact"]["existed_before_build"], False)

    def test_library_path_is_recorded_as_realpath(self):
        """⭐ 记软链路径会让下游两个消费者中的一个必然对不上。

        driver 拿 `os.path.realpath(library_path)` 与实际加载的 ELF 比，adapter 拿它与
        `vendor.library_path` 做**字符串**全等比——只有记 realpath 两边才都过。
        """
        real = self._make_library("libcust_opapi.real.so", b"\x7fELF-real-bytes")
        linkdir = os.path.join(self.d, "symlinked")
        os.makedirs(linkdir, exist_ok=True)
        link = os.path.join(linkdir, "libcust_opapi.so")
        os.symlink(real, link)

        rc, _ = self._run(self._argv(_ok_build(link), library=link))
        self.assertEqual(rc, 0)
        r = self._receipt()
        self.assertEqual(r["artifact"]["library_path"], os.path.realpath(real))
        self.assertNotEqual(r["artifact"]["library_path"], link,
                            "记的是软链路径而不是目标")
        self.assertEqual(r["artifact"]["library_sha256"], _sha_file(real))

    # ---- 负路 -------------------------------------------------------------------

    def test_build_cwd_pointing_at_a_different_tree_is_fail_closed(self):
        """⭐ 本脚本最有价值的新增校验：build 的树 ≠ 被指纹的树 一律拒。

        `source_facts` 只记摘要不记仓根路径，所以「你 build 的是不是被测的那份字节」
        本来无人核——`--build-cwd` 指到另一份 checkout 上照样能产出一份看着完整的收据，
        而它声称的来源在那台机器上根本不存在。这是典型 fail-open，必须钉死。
        """
        other = os.path.join(self.d, "other-checkout")
        shutil.copytree(self.repo, other, symlinks=True)
        self._write(f"{self.OP_SUBDIR}/op_host/median_def.cpp",
                    "// 偷偷改过的实现\n", root=other)

        with self.assertRaises(mk.ReceiptError) as cm:
            self._run(self._argv(_ok_build(self.lib, "SHOULD_NOT_RUN"), build_cwd=other))
        self.assertIn("不是同一份字节", str(cm.exception))
        self.assertFalse(os.path.exists(self.receipt_path), "留下了一份来源说不清的收据")
        # 校验在 build **之前**：错的树不该先跑几十分钟构建再报错
        self.assertFalse(os.path.exists(os.path.join(other, "SHOULD_NOT_RUN")),
                         "构建树校验落在了 build 之后")

    def test_build_that_rewrites_the_op_subtree_is_fail_closed(self):
        """⭐ build 把被测子树改掉 → 拒。**没有下游会替你发现这件事**。

        产出方的 docstring 曾写着「构建后的漂移由下游接住：再跑一次 `fetch_source`
        就会得到不同的 root_digest，三级门当场报不等」——那句话是错的。编排层只在
        CP-A 取材跑一次 `fetch_source`，build 之后再没有任何一步重新取材；三级门读的是
        **同一份**落盘的 source_facts.json，拿旧锚比旧锚，永远相等。那个救援从不发生。
        """
        victim = os.path.join(self.repo, self.OP_SUBDIR, "op_host", "generated.cpp")
        drifting = _ok_build(self.lib)
        drifting[-1] += f"; open({victim!r}, 'w').write('// build 生成的源码\\n')"

        with self.assertRaises(mk.ReceiptError) as cm:
            self._run(self._argv(drifting))
        msg = str(cm.exception)
        self.assertIn("build 把被测子树改掉了", msg)
        self.assertIn("没有下游会发现", msg, "错误没点明「不会有人替你接住」")
        self.assertFalse(os.path.exists(self.receipt_path),
                         "收据声称构建自一份此刻已不存在的字节")

    def test_failed_build_produces_no_receipt(self):
        """⭐ 退出码非 0 → 不落盘。消费者三处都硬校 `returncode == 0`。"""
        with self.assertRaises(mk.ReceiptError) as cm:
            self._run(self._argv(_fail_build(3)))
        msg = str(cm.exception)
        self.assertIn("3", msg)
        self.assertIn("returncode", msg, "错误没点明消费者硬校的是哪一项")
        self.assertFalse(os.path.exists(self.receipt_path),
                         "产了一份注定被消费者拒的收据，把失败推迟到验收阶段")

    def test_failed_build_is_reported_before_the_missing_artifact(self):
        """build 失败**且**没产物时，先报退出码——「产物不存在」只是它的后果。

        先报后果会把人引去查 install，而真正该修的是构建。
        """
        missing = os.path.join(self.libdir, "never_installed.so")
        with self.assertRaises(mk.ReceiptError) as cm:
            self._run(self._argv(_fail_build(7), library=missing))
        msg = str(cm.exception)
        self.assertIn("7", msg)
        self.assertNotIn("install", msg, "把人引去查 install，而病根是构建失败")

    def test_missing_library_after_build_is_fail_closed(self):
        """build 成功但 `--library` 不存在：多半是 install 漏了，错误要说清怎么修。"""
        missing = os.path.join(self.libdir, "never_installed.so")
        with self.assertRaises(mk.ReceiptError) as cm:
            self._run(self._argv(_noop_build(), library=missing))
        self.assertIn("install", str(cm.exception))
        self.assertFalse(os.path.exists(self.receipt_path))

    def test_library_untouched_by_the_build_is_fail_closed(self):
        """⭐ 本轮最贵的那个洞：`--library` 与 build 之间没有任何因果绑定。

        `-- /usr/bin/true` 配上一个**预先存在**的 ELF（真机上顺手就能指到
        `/usr/local/Ascend/…/libcust_opapi.so` 那份 CANN 内置实现）——构建树校验过、
        退出码 0、产物 hash 得出来，adapter / 判别式 / 三级门**全过**。
        于是模块头说要堵的那句「加载一个 CANN 内置的同名实现也能跑完全套」，
        有了这份收据照样成立，只是多打一个 `--library`。
        """
        with self.assertRaises(mk.ReceiptError) as cm:
            self._run(self._argv(_noop_build()))          # 跑得通，但不碰 .so
        msg = str(cm.exception)
        self.assertIn("一个字节都没动", msg)
        self.assertIn("CANN 内置", msg, "错误没说清这条为什么要拦")
        self.assertFalse(os.path.exists(self.receipt_path),
                         "产出了一份与本次构建毫无因果关系的收据")

    def test_blocked_source_facts_is_rejected(self):
        """取材没走完（`completeness != complete`）→ 锚不可信，不产收据。"""
        self._write(f"{self.OP_SUBDIR}/op_host/median_def.cpp", "// 未提交的改动\n")
        blocked_out = os.path.join(self.d, "facts_blocked")
        rc = self._quiet_fetch(["--taskdoc", self.task, "--local-repo", self.repo,
                                "--op-subdir", self.OP_SUBDIR, "--out", blocked_out])
        self.assertEqual(rc, 3, "夹具没造出 blocked 的取材结果")
        self.assertEqual(self._payload_at(blocked_out)["completeness"]["status"], "blocked")

        with self.assertRaises(mk.ReceiptError) as cm:
            self._run(self._argv(
                _ok_build(self.lib), source_facts=os.path.join(blocked_out, "source_facts.json")))
        self.assertIn("complete", str(cm.exception))
        self.assertFalse(os.path.exists(self.receipt_path))

    def test_tampered_source_facts_envelope_is_rejected(self):
        """⭐ 手改锚而不重算 digest → 必须在这里就拦下。

        走的是 `content_address.read_artifact`（核 domain + 复算 digest），不是裸
        `json.load`。裸读的话这份伪造锚会一路进收据，直到三级门才报「锚不相等」——
        那时错误指向 build receipt，把人引到错的地方查。
        """
        path = os.path.join(self.out, "source_facts.json")
        with open(path, encoding="utf-8") as src:
            doc = json.load(src)
        doc["payload"]["local_checkout"]["root_digest"] = "d" * 64
        with open(path, "w", encoding="utf-8") as out:
            json.dump(doc, out, ensure_ascii=False)

        with self.assertRaises(mk.ReceiptError) as cm:
            self._run(self._argv(_ok_build(self.lib)))
        self.assertIn("envelope", str(cm.exception))
        self.assertFalse(os.path.exists(self.receipt_path))

    def test_weakened_digest_policy_is_rejected_before_recompute(self):
        """摘要策略不同则两个 root_digest 根本不可比——不许「重算一下看看等不等」。

        （这条不是 fail-open：策略不同重算出来必然不等、照样拒。它保证报的是
        「策略不可比」而不是「字节不同」，免得人去查一棵其实没被改过的树。）
        """
        policy = dict(fs.digest_policy())
        policy["excluded_segment_names"] = ["op_host"]          # 把源码整个排掉
        payload = json.loads(json.dumps(self.payload))
        payload["local_checkout"]["digest_policy"] = policy
        forged_out = os.path.join(self.d, "facts_forged_policy")
        os.makedirs(forged_out, exist_ok=True)
        ca.write_artifact(forged_out, "source_facts.json", _DOMAIN, payload)

        with self.assertRaises(mk.ReceiptError) as cm:
            self._run(self._argv(
                _ok_build(self.lib), source_facts=os.path.join(forged_out, "source_facts.json")))
        self.assertIn("digest_policy", str(cm.exception))
        self.assertFalse(os.path.exists(self.receipt_path))

    def test_undeducible_repo_requires_explicit_flag(self):
        """非 git 目录派生不出仓名 → 必须显式 `--repo`，不给就报错（不写 null 混过去）。

        `repo` 会被 CP-F 拿去与首轮 `runner_binding.base_source_repo` 做**逐字**比对，
        产一份 `repo: null` 的收据等于把那道门变成摆设。
        """
        plain = os.path.join(self.d, "plain-checkout")          # 没有 .git
        self._seed_op_tree(root=plain)
        plain_out = os.path.join(self.d, "facts_plain")
        rc = self._quiet_fetch(["--taskdoc", self.task, "--local-repo", plain,
                                "--op-subdir", self.OP_SUBDIR, "--out", plain_out])
        self.assertEqual(rc, 0)
        self.assertNotIn("git", self._payload_at(plain_out)["local_checkout"])
        facts = os.path.join(plain_out, "source_facts.json")

        with self.assertRaises(SystemExit):
            self._run(self._argv(_ok_build(self.lib), source_facts=facts, build_cwd=plain))
        self.assertIn("--repo", self.log)
        self.assertFalse(os.path.exists(self.receipt_path))

        rc, _ = self._run(self._argv(_ok_build(self.lib), source_facts=facts, build_cwd=plain,
                                     extra=["--repo", "cann/ops-nn"]))
        self.assertEqual(rc, 0)
        r = self._receipt()
        self.assertEqual(r["source"]["repo"], "cann/ops-nn")
        # 派生不出来时 `--repo` 是唯一来源 → 强度如实记成「操作者自报」
        self.assertEqual(r["source"]["repo_source"], "operator")

    def test_repo_override_conflicting_with_the_derived_value_needs_a_flag(self):
        """⭐ `--repo` 不许**静默**把「事实派生」换成「操作者自报」。

        `repo` 会被 CP-F 拿去与首轮 `runner_binding.base_source_repo` 做逐字比对。
        允许无条件覆盖，那道门比的就不再是事实，而是操作者当时随手写了什么。
        """
        with self.assertRaises(mk.ReceiptError) as cm:
            self._run(self._argv(_ok_build(self.lib), extra=["--repo", "cann/ops-nn"]))
        msg = str(cm.exception)
        self.assertIn("不一致", msg)
        self.assertIn("--allow-repo-override", msg, "没告诉人怎么合法地覆盖")
        self.assertFalse(os.path.exists(self.receipt_path))

        # 显式声明之后放行，且强度**必须**记成 operator（本地派生值是带 host/.git 的
        # remote URL，而 CP-F directive 那边通常写 owner/repo——这条覆盖是常规操作）
        rc, _ = self._run(self._argv(
            _ok_build(self.lib), extra=["--repo", "cann/ops-nn", "--allow-repo-override"]))
        self.assertEqual(rc, 0)
        r = self._receipt()
        self.assertEqual(r["source"]["repo"], "cann/ops-nn")
        self.assertEqual(r["source"]["repo_source"], "operator")

    def test_credential_bearing_remote_url_is_refused_without_echoing_it(self):
        """⭐ 带凭据的 remote URL 不许进收据，**报错也不许回显它**。

        `fetch_source.probe_local_git` 记的是 `git config --get remote.origin.url` 原值、
        全程无脱敏，所以 `https://user:token@host/…` 会一路进 source_facts → 收据
        `source.repo` → 终端 → `render_acceptance_markdown` 的「源码仓」一行 →
        人读的验收报告 .md。撞仓规 §2。
        """
        token = "s3cr3t-token-do-not-leak"
        self._git("remote", "set-url", "origin",
                  f"https://bot:{token}@gitcode.com/cann/ops-nn.git")
        leaky_out = os.path.join(self.d, "facts_leaky")
        rc = self._quiet_fetch(["--taskdoc", self.task, "--local-repo", self.repo,
                                "--op-subdir", self.OP_SUBDIR, "--base-ref", "master",
                                "--out", leaky_out])
        self.assertEqual(rc, 0)
        facts = os.path.join(leaky_out, "source_facts.json")

        with self.assertRaises(mk.ReceiptError) as cm:
            self._run(self._argv(_ok_build(self.lib), source_facts=facts))
        msg = str(cm.exception)
        self.assertIn("凭据", msg)
        self.assertNotIn(token, msg, "报错把凭据原样打了出来——报错本身成了第二处泄漏点")
        self.assertFalse(os.path.exists(self.receipt_path))

        # 给了不含凭据的 --repo 就能继续（凭据只是不许进收据，不是不许构建）
        rc, _ = self._run(self._argv(_ok_build(self.lib), source_facts=facts,
                                     extra=["--repo", "cann/ops-nn"]))
        self.assertEqual(rc, 0)
        r = self._receipt()
        self.assertEqual(r["source"]["repo"], "cann/ops-nn")
        self.assertNotIn(token, json.dumps(r, ensure_ascii=False), "凭据落进了收据")

    def test_ssh_style_remote_url_is_not_mistaken_for_a_credential(self):
        """`git@host:path` 的 `@` 前面是用户名、不含密钥——拦它会误伤全部 SSH remote。"""
        self._git("remote", "set-url", "origin", "git@gitcode.com:cann/ops-nn.git")
        out = os.path.join(self.d, "facts_ssh")
        self.assertEqual(0, self._quiet_fetch(
            ["--taskdoc", self.task, "--local-repo", self.repo, "--op-subdir", self.OP_SUBDIR,
             "--base-ref", "master", "--out", out]))
        rc, _ = self._run(self._argv(
            _ok_build(self.lib), source_facts=os.path.join(out, "source_facts.json")))
        self.assertEqual(rc, 0)
        self.assertEqual(self._receipt()["source"]["repo"], "git@gitcode.com:cann/ops-nn.git")

    def test_out_colliding_with_an_input_is_refused_before_the_build(self):
        """`--out` 撞上 `--library` / `--source-facts` → build 之前就拒。

        `--out == --library` 会把 JSON 原子替换掉被测 ELF（直接毁掉被测物）。
        """
        for target in (self.lib, os.path.join(self.out, "source_facts.json")):
            argv = self._argv(_ok_build(self.lib, "SHOULD_NOT_RUN"))
            argv[argv.index("--out") + 1] = target
            with self.assertRaises(SystemExit):
                self._run(argv)
            self.assertIn("同一个文件", self.log)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "SHOULD_NOT_RUN")),
                         "参数校验落在了 build 之后（build 动辄几十分钟）")

    def test_unwritable_out_is_refused_before_the_build(self):
        """⭐ `--out` 的可写性必须**前置**：跑完 build 才发现落点写不了 = 整轮重跑。

        本脚本又没有「只记录不执行」模式，所以那一轮真的救不回来。

        ⚠ 不用 `chmod 0o500` 造不可写：真机容器里跑测是 **root**，权限位对它没有约束，
        那样写出来的是一条在 CI 上永远绿的假用例。改用「父路径是个普通文件」——
        `os.makedirs` 对谁都会抛 `NotADirectoryError`。
        """
        not_a_dir = os.path.join(self.d, "this-is-a-file")
        with open(not_a_dir, "w", encoding="utf-8") as fh:
            fh.write("x")
        argv = self._argv(_ok_build(self.lib, "SHOULD_NOT_RUN"))
        argv[argv.index("--out") + 1] = os.path.join(not_a_dir, "receipt.json")

        with self.assertRaises(SystemExit):
            self._run(argv)
        self.assertIn("写不进去", self.log)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "SHOULD_NOT_RUN")),
                         "白跑了一轮 build 才发现 --out 落不下去")

    def test_relative_library_is_refused(self):
        """`--library` 相对路径按**调用方 cwd** 解析，不是 `--build-cwd` —— 静默错绑。

        `subprocess.run(cwd=…)` 不改变本进程 cwd，而写 `--library build_out/x.so` 的人
        几乎都以为它相对构建目录。与 `cpp_extension_driver._require_env_path` 同一口径。
        """
        with self.assertRaises(SystemExit):
            self._run(self._argv(_ok_build(self.lib), library="build_out/libcust_opapi.so"))
        self.assertIn("绝对路径", self.log)
        self.assertFalse(os.path.exists(self.receipt_path))

    def test_bad_build_cwd_reports_a_receipt_error_not_a_traceback(self):
        """⭐ `fetch_source` 抛的是**裸 `RuntimeError`**，而 `ReceiptError` 是它的子类。

        `except ReceiptError` 接不住父类，所以不在信任边界转换的话，`--build-cwd` 缺
        `op_subdir` 这种再普通不过的输入错，用户看到的是 Python traceback 而不是
        `[receipt] ✗ <人话>`。退出码仍是 1（不是 fail-open），但诊断契约是坏的：
        一个脚本一半错误报人话、另一半喷栈。
        """
        empty = os.path.join(self.d, "empty-tree")              # 没有 op_subdir
        os.makedirs(empty, exist_ok=True)
        with self.assertRaises(mk.ReceiptError) as cm:          # 裸 RuntimeError 不是它的实例
            self._run(self._argv(_ok_build(self.lib), build_cwd=empty))
        self.assertIn("重算被测子树摘要失败", str(cm.exception))
        self.assertFalse(os.path.exists(self.receipt_path))

    def test_source_facts_failing_the_gate_contract_is_rejected(self):
        """⭐ 产出方的收货标准必须与三级门**同一份**，不许比消费者松。

        内容寻址摘要**不具备真实性**——谁都能给任意 payload 重算一个自洽 envelope。
        只核 envelope 的话，一份 `reasons` 非空、key_files 没绑锚的 payload 也能在这里
        拿到 `status=VERIFIED` 的收据，直到验收阶段才被三级门拦下，白烧一轮 build+run。
        """
        forged = json.loads(json.dumps(self.payload))
        forged["completeness"] = {"status": "complete", "reasons": []}
        forged["key_files"] = [dict(forged["key_files"][0], ref="0" * 64)]   # 锚绑错
        forged_out = os.path.join(self.d, "facts_unbound")
        os.makedirs(forged_out, exist_ok=True)
        ca.write_artifact(forged_out, "source_facts.json", _DOMAIN, forged)

        with self.assertRaises(mk.ReceiptError) as cm:
            self._run(self._argv(
                _ok_build(self.lib),
                source_facts=os.path.join(forged_out, "source_facts.json")))
        self.assertIn("三级门的契约", str(cm.exception))
        self.assertFalse(os.path.exists(self.receipt_path))

        # 反证：同一份伪造 payload 在三级门那边也是被拒的（两边判据确实是同一个）
        import validate_acceptance_state as vas
        self.assertEqual("__BAD__", vas._find_source_facts(
            forged_out, os.path.join(forged_out, "source_facts.json")))

    def test_refuses_to_record_without_executing(self):
        """⭐ 不给 `-- <argv>` 就报错：schema v1 分辨不出 `returncode` 是实跑还是自报。

        存在「只记录不执行」模式的话，这份收据的全部意义（机器可核）就没了。
        """
        with self.assertRaises(SystemExit):
            self._run(["--source-facts", os.path.join(self.out, "source_facts.json"),
                       "--build-cwd", self.repo, "--library", self.lib,
                       "--out", self.receipt_path])
        self.assertIn("只记录不执行", self.log)
        self.assertFalse(os.path.exists(self.receipt_path))

    # ---- 产出方 ↔ 消费者契约 -----------------------------------------------------

    def test_receipt_passes_all_three_downstream_consumers(self):
        """⭐ 把「产出方 ↔ 消费者」的契约用机器钉住。

        这三处任何一处的必填集、字段名或摘要口径漂移，都会让真机上 build 完一整轮
        才在验收阶段炸——这条用例把那个反馈环缩短到本地一次 pytest。
        """
        import cpp_extension_adapter as cea
        import validate_acceptance_state as vas

        rc, _ = self._run(self._argv(_ok_build(self.lib)))
        self.assertEqual(rc, 0)
        r = self._receipt()

        # ① adapter：收据自身形态 + 与 vendor ELF 的绑定 + build_receipt_sha256
        vendor = {
            "library_path": r["artifact"]["library_path"],
            "library_sha256": r["artifact"]["library_sha256"],
            "symbols_owned": ["aclnnMedian"],
            "build_receipt": r,
            "build_receipt_sha256": cea._canonical_sha(r),
        }
        cea._validate_vendor_build_receipt(vendor)              # 不抛即 PASS

        # ② 判别式：带上 source_facts 的 kind 做一致性前置校验
        kind, field, value = ds.validate_build_receipt_source(
            r["source"], expected_kind=ds.of(self.payload, where="source_facts"))
        self.assertEqual((kind, field), (ds.LOCAL_CHECKOUT, "local_root_digest"))
        self.assertEqual(value, self.payload["local_checkout"]["root_digest"])

        # ③ 三级门：自动发现与显式指路两条入口都要过
        facts_path = os.path.join(self.out, "source_facts.json")
        for kwargs in ({}, {"source_facts_path": facts_path}):
            errs = []
            vas._gate_build_receipt_source_binding(self.out, r["source"], errs, **kwargs)
            self.assertEqual(errs, [], f"三级门在正例上报错（{kwargs}）")

    def test_three_level_gate_blocks_a_tampered_anchor(self):
        """⭐ 反向：锚被改 / 通路被伪装，三级门都必须拦。

        第二个 case 是文档里点名的那条绕过路径：声明 `pull_request` + 一个任意 40 位
        hex，本地锚的等值校验就整条不执行。收据看着齐全，绑定其实是空的。
        """
        import validate_acceptance_state as vas

        rc, _ = self._run(self._argv(_ok_build(self.lib)))
        self.assertEqual(rc, 0)
        r = self._receipt()

        forged = dict(r["source"], local_root_digest="d" * 64)
        errs = []
        vas._gate_build_receipt_source_binding(self.out, forged, errs)
        self.assertTrue(errs, "改掉本地锚竟然过了三级门")
        self.assertIn("不相等", " ".join(errs))

        disguised = {"repo": r["source"]["repo"], "pr_head_sha": "a" * 40}
        errs = []
        vas._gate_build_receipt_source_binding(self.out, disguised, errs)
        self.assertTrue(errs, "本地事实伪装成 PR 事实竟然过了三级门")
        self.assertIn("来源不一致", " ".join(errs))


class PullRequestReceiptTest(_Base):
    """PR 通路：锚是 `head_sha`、**不写** `dut_source` 键、**没有**构建树对照物。"""

    HEAD = "0290d61ac066f9f4e620a3714f5941e82dc4e72a"
    HEADER = "experimental/index/median/op_host/op_api/aclnn_median.h"

    def setUp(self):
        super().setUp()
        fs.write_source_facts(self.task, self._pr_facts(), self.out)
        self.payload = self._payload_at(self.out)
        self.assertEqual(self.payload["completeness"]["status"], "complete")
        self.lib = self._make_library()
        # 刻意指向一棵与被测毫无关系的树：PR 通路本来就核不了这一环，如实记账
        self.unrelated = os.path.join(self.d, "unrelated-tree")
        os.makedirs(self.unrelated, exist_ok=True)

    def _pr_facts(self):
        return {
            "pr_url": "https://gitcode.com/cann/ops-nn/pull/6429",
            "source_repo": "cann/ops-nn",
            "head_sha": self.HEAD,
            "head_repo": "contributor/ops-nn",
            "is_fork": True,
            "state": "opened",
            "changed_files": [self.HEADER],
            "key_files": {self.HEADER: "aclnnStatus aclnnMedianGetWorkspaceSize();"},
            "key_files_ref": {self.HEADER: self.HEAD},
            "aclnn_headers": [self.HEADER],
            "op": "median",
            "target_dir": "experimental/index/median",
            "interface_kind": "aclnn_2stage",
            "aclnn_entry": "aclnnMedian",
        }

    def test_pr_receipt_omits_dut_source_and_has_no_tree_check(self):
        rc, log = self._run(self._argv(_ok_build(self.lib), build_cwd=self.unrelated))
        self.assertEqual(rc, 0)
        r = self._receipt()

        # ⭐ 与 fetch_source 同口径：PR 通路**不写** dut_source 键。写了会改变既有
        #    PR 收据的字节形态，而缺席 == pull_request 正是那条向后兼容的兜底。
        self.assertNotIn("dut_source", r["source"])
        self.assertEqual(ds.of(r["source"], where="receipt.source"), ds.PULL_REQUEST)
        self.assertEqual(r["source"]["pr_head_sha"], self.HEAD)
        self.assertNotIn("local_root_digest", r["source"], "两条通路的锚必须互斥出现")
        self.assertEqual(r["source"]["repo"], "cann/ops-nn")
        self.assertEqual(r["source"]["repo_source"], "pr.source_repo")

        # ⭐ build_cwd 与被测毫无关系却照样产得出收据：PR 通路**没有**「构建树 ↔ 指纹树」
        #    这一环。别看到 assert_build_tree_matches_fingerprint 就以为两条通路都校了。
        #    ⚠ 构建**后**那次同样没有——PR 通路两次都没有对照物，如实挂账。
        self.assertIn("PR 通路无法核", log)
        self.assertNotIn("build 没有改动被测子树", log)
        for stage in ("构建前", "构建后"):
            self.assertIsNone(mk.assert_build_tree_matches_fingerprint(
                self.payload, ds.PULL_REQUEST, "/definitely/not/a/tree", stage=stage))

    def test_pr_receipt_passes_downstream_consumers(self):
        import cpp_extension_adapter as cea
        import validate_acceptance_state as vas

        rc, _ = self._run(self._argv(_ok_build(self.lib), build_cwd=self.unrelated))
        self.assertEqual(rc, 0)
        r = self._receipt()

        cea._validate_vendor_build_receipt({
            "library_path": r["artifact"]["library_path"],
            "library_sha256": r["artifact"]["library_sha256"],
            "symbols_owned": ["aclnnMedian"],
            "build_receipt": r,
            "build_receipt_sha256": cea._canonical_sha(r),
        })
        errs = []
        vas._gate_build_receipt_source_binding(
            self.out, r["source"], errs,
            source_facts_path=os.path.join(self.out, "source_facts.json"))
        self.assertEqual(errs, [])

        # PR 通路的锚值现在也在三级门里比（不再只比 kind）
        errs = []
        vas._gate_build_receipt_source_binding(
            self.out, dict(r["source"], pr_head_sha="b" * 40), errs,
            source_facts_path=os.path.join(self.out, "source_facts.json"))
        self.assertTrue(errs, "换掉 PR head 竟然过了三级门")


if __name__ == "__main__":
    unittest.main()
