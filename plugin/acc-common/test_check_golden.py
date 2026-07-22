"""`check_golden.py` 的**退出码契约**回归（批 6）。

为什么盯退出码而不是账本字段：`acc-runner-dev:gen_golden` 与 CP-B 编排**按退出码分三路**
（0=进 dry-run / 2=进但标「需人核」/ 1=停在 CP-B 摆 blocked_reason），手册与两处编排文本都照这三态写。
退出码一旦漂（比如 tier 3 从 2 变成 1），**tier 3 的算子会被当成 blocked 挡死**——
而账本 JSON 看上去一切正常，没人会发现。所以这里锁的是那三个数字。

跑: cd plugin/acc-common && python3 -m unittest test_check_golden -v
⚠ 本文件**不校 golden 数值**（同 test_samples_golden_contract）——只校来源契约链的判读。
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import check_golden
import precision_policy

_HERE = os.path.dirname(os.path.abspath(__file__))
_SAMPLES = os.path.join(_HERE, "..", "samples", "golden")

# 合成一个「任务书给了公式、需自拼多步」的 golden —— tier 3 路径的最小复现。
_FORMULA_GOLDEN = '''\
GOLDEN_SOURCE = "analytical analytical_ref"
GOLDEN_PROVENANCE = "第三档（tier 3）·任务书给公式、自拼多步 → 必须人核"
GOLDEN_CONTRACT = {
    "source": "multistep", "method_kind": "numpy_cpu", "method": "按公式自拼",
    "authorization": {"kind": "formula",
                      "cite": "task_doc.snapshot.md:1",
                      "quote": "y = x"},
    "taskdoc_snapshot": {"sha256": "__SHA__"},
}
def golden_fn(inputs, attrs):
    return inputs[0]
'''


class CheckGoldenExitCodeTest(unittest.TestCase):
    """三态退出码 + 每态的账本关键字段。用真样例（IsClose 带真快照）+ 最小合成件。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="oprunway_gold_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self._old = os.environ.get("OPRUNWAY_OPS_DIR")
        os.environ["OPRUNWAY_OPS_DIR"] = self.root
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._old is None:
            os.environ.pop("OPRUNWAY_OPS_DIR", None)
        else:
            os.environ["OPRUNWAY_OPS_DIR"] = self._old

    def _place_sample(self, op):
        dst = os.path.join(self.root, op)
        shutil.copytree(os.path.join(_SAMPLES, op), dst,
                        ignore=shutil.ignore_patterns("__pycache__"))
        return dst

    def _place_formula(self, op="FakeFormula", snapshot_text="y = x\n"):
        d = os.path.join(self.root, op)
        os.makedirs(d)
        snap = os.path.join(d, "task_doc.snapshot.md")
        with io.open(snap, "w", encoding="utf-8", newline="") as fh:
            fh.write(snapshot_text)
        with io.open(snap, "rb") as fh:
            sha = hashlib.sha256(fh.read()).hexdigest()
        with io.open(os.path.join(d, "golden.py"), "w", encoding="utf-8") as fh:
            fh.write(_FORMULA_GOLDEN.replace("__SHA__", sha))
        return d

    def _run_main(self, *argv):
        """跑 main() 并把 stdout 的账本取回，返 (exit_code, ledger)。"""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = check_golden.main(list(argv))
        return rc, json.loads(buf.getvalue())

    # ── 0：可往下走 ────────────────────────────────────────────────────────
    def test_tier1_authorized_exits_0(self):
        """IsClose 样例带真快照 + 逐字引文 → tier 1、授权核实通过、exit 0。"""
        self._place_sample("IsClose")
        rc, led = self._run_main("IsClose")
        self.assertEqual(rc, 0, led)
        self.assertEqual(led["tier"], 1)
        self.assertTrue(led["authorized"])
        self.assertIsNone(led["blocked_reason"])

    # ── 2：要人核，非失败 ──────────────────────────────────────────────────
    def test_tier3_formula_exits_2_not_1(self):
        """公式自拼 → tier 3 + needs_human_review，**退出码 2**。

        若这里变成 1，编排会把它当 blocked 挡死——tier 3 是「要人核」不是「不许跑」。"""
        self._place_formula()
        rc, led = self._run_main("FakeFormula")
        self.assertEqual(rc, 2, led)
        self.assertEqual(led["tier"], 3)
        self.assertTrue(led["needs_human_review"])
        self.assertIsNone(led["blocked_reason"])

    # ── 1：停下 ────────────────────────────────────────────────────────────
    def test_tampered_snapshot_is_tier4_blocked(self):
        """快照被改一个字节 → 指纹不符 → tier 4 · unverifiable_authorization · exit 1。

        **假授权不降级**（不是悄悄退回 tier 2），这是 R12 那道闸的全部意义。"""
        d = self._place_sample("IsClose")
        with io.open(os.path.join(d, "task_doc.snapshot.md"), "a", encoding="utf-8") as fh:
            fh.write("\n多余一行\n")
        rc, led = self._run_main("IsClose")
        self.assertEqual(rc, 1, led)
        self.assertEqual(led["tier"], 4)
        self.assertEqual(led["blocked_reason"], "unverifiable_authorization")
        self.assertFalse(led["authorized"])
        self.assertIn("指纹", led["authorization_reason"] or "")

    def test_missing_contract_exits_1(self):
        """Sign 样例没有 GOLDEN_CONTRACT → 派生不出档位 → exit 1（加载虽不阻塞，验收要写）。"""
        self._place_sample("Sign")
        rc, led = self._run_main("Sign")
        self.assertEqual(rc, 1, led)
        self.assertIsNone(led["tier"])
        self.assertIn("GOLDEN_CONTRACT", led["error"])

    def test_missing_file_exits_1_with_pointer(self):
        """算子目录里根本没有 golden.py → exit 1，且报错要指向产出者（不然人不知道找谁）。"""
        rc, led = self._run_main("NeverExisted")
        self.assertEqual(rc, 1, led)
        self.assertIn("gen_golden", led["error"])

    def test_bad_vocab_exits_1_before_reading_snapshot(self):
        """词表拼错 → 第一层就 fail-closed，**不进授权核实**（授权字段应保持 None）。

        三层分立的意义就在这：早拦、报准，别让「拼错」伪装成「授权核不过」。"""
        d = self._place_formula(op="BadVocab")
        p = os.path.join(d, "golden.py")
        with io.open(p, encoding="utf-8") as fh:
            s = fh.read().replace('"multistep"', '"multi_step"')
        with io.open(p, "w", encoding="utf-8") as fh:
            fh.write(s)
        rc, led = self._run_main("BadVocab")
        self.assertEqual(rc, 1, led)
        self.assertFalse(led["contract_ok"])
        self.assertIn("词表", led["error"])
        self.assertIsNone(led["authorized"], "词表没过就不该已经去核授权")


class FailOpenRegressionTest(unittest.TestCase):
    """2026-07-23 codex 审计逮到的 4 个 **fail-open**，逐个钉死。

    共同形状：**账本看着正常、退出码却是绿的**。这类洞不会让任何测试变红，
    只会让一个从没被真正检查过的 golden 一路走到 CP-D。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="oprunway_failopen_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self._old = os.environ.get("OPRUNWAY_OPS_DIR")
        os.environ["OPRUNWAY_OPS_DIR"] = self.root
        self.addCleanup(lambda: os.environ.__setitem__("OPRUNWAY_OPS_DIR", self._old)
                        if self._old is not None else os.environ.pop("OPRUNWAY_OPS_DIR", None))

    def _write(self, op, body):
        d = os.path.join(self.root, op)
        os.makedirs(d, exist_ok=True)
        with io.open(os.path.join(d, "golden.py"), "w", encoding="utf-8") as fh:
            fh.write(body)
        return d

    def _rc(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = check_golden.main(list(argv))
        return rc, json.loads(buf.getvalue())

    # ── ① Critical：SystemExit 穿过 `except Exception` → 假绿 ──────────────
    def test_golden_raising_systemexit_zero_is_not_green(self):
        """`golden.py` 里一句 `raise SystemExit(0)` 曾让**没有 GOLDEN_CONTRACT 的 golden 退出码 0**。

        `SystemExit` 不是 `Exception` 的子类，`except Exception` 挡不住它——它直达解释器。
        这是本脚本最不能出的错：检查器被被检查者一句话关掉了。"""
        self._write("EvilExit", "raise SystemExit(0)\n")
        rc, led = self._rc("EvilExit")
        self.assertEqual(rc, 1, led)
        self.assertIn("SystemExit", led["error"])

    def test_engine_load_systemexit_also_caught(self):
        """`--load` 那条路（`gen_cases.load_golden`）同样不能被 `SystemExit` 穿透。"""
        self._write("EvilExit2", "import sys\nsys.exit(0)\n")
        rc, led = self._rc("EvilExit2", "--load")
        self.assertEqual(rc, 1, led)

    # ── ② High：argparse 的默认 exit 2 与「需人核」撞车 ────────────────────
    def test_argparse_error_exits_1_not_2(self):
        """少打一个 `<Op>` 曾退出 2 = 编排读成「golden 没问题、只是要人核」→ 直接放行。

        参数错误必须落 1。用子进程测**真实进程退出码**，不是函数返回值。"""
        r = subprocess.run([sys.executable, os.path.join(_HERE, "check_golden.py")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("参数错误", r.stderr)

    # ── ③ High：路由键是 needs_human_review，不是 tier ─────────────────────
    def test_tier1_needing_review_exits_2(self):
        """`multistep + oracle_method` → `derive_golden_tier` 返 **(1, True, None)**。

        按 tier 路由会给 0，把「档位高但仍要人核」的 golden 静默放行。这条是审计里最深的一个：
        退出码的语义键**从来就不是档位数字**。"""
        self.assertEqual(
            precision_policy.derive_golden_tier(
                {"source": "multistep", "method_kind": "torch_cpu", "method": "x",
                 "authorization": {"kind": "oracle_method",
                                   "cite": "task_doc.snapshot.md:1", "quote": "q"},
                 "taskdoc_snapshot": {"sha256": "d"}}, True)[:2],
            (1, True), "前提变了：本测试假设 multistep+oracle 判 (tier 1, 需人核)")
        self.assertEqual(check_golden._exit_code(
            {"contract_ok": True, "tier": 1, "needs_human_review": True,
             "blocked_reason": None, "authorized": True}), 2)

    def test_contradictory_ledger_fails_closed(self):
        """账本自相矛盾（tier 1/2 却带 `blocked_reason`、或授权核不实）→ **按坏的算**。"""
        for led in ({"contract_ok": True, "tier": 2, "needs_human_review": False,
                     "blocked_reason": "method_unavailable", "authorized": True},
                    {"contract_ok": True, "tier": 1, "needs_human_review": False,
                     "blocked_reason": None, "authorized": False},
                    {"contract_ok": True, "tier": None, "needs_human_review": False,
                     "blocked_reason": None, "authorized": True},
                    {"contract_ok": True, "tier": 4, "needs_human_review": False,
                     "blocked_reason": None, "authorized": True}):
            self.assertEqual(check_golden._exit_code(led), 1, led)

    # ── ④ High：必需导出只查了 hasattr ─────────────────────────────────────
    def test_invalid_required_exports_rejected(self):
        """`golden_fn=None` / 空 `GOLDEN_SOURCE` / 不可调用 `out_shape` 曾能拿 exit 0。

        它们全会被 `gen_cases.load_golden` 拒——「自检说没事、CP-D 才炸」正是本脚本要消灭的。"""
        base = ('GOLDEN_SOURCE = "torch torch.x"\nGOLDEN_PROVENANCE = "p"\n'
                'def golden_fn(i, a): return i[0]\n')
        for op, body, why in (
                ("BadFn", base.replace("def golden_fn(i, a): return i[0]", "golden_fn = None"), "不可调用"),
                ("BadSrc", base.replace('GOLDEN_SOURCE = "torch torch.x"', 'GOLDEN_SOURCE = "   "'), "非空字符串"),
                ("BadShape", base + 'out_shape = "not a function"\n', "不可调用")):
            self._write(op, body)
            rc, led = self._rc(op)
            self.assertEqual(rc, 1, f"{op}: {led}")
            self.assertIn(why, led["error"], f"{op}: {led['error']}")

    # ── 后半程异常也必须账本化（原 never_raises 只覆盖到加载阶段）──────────
    def test_unreadable_snapshot_is_ledgered_not_raised(self):
        """快照不可读（`PermissionError`）曾把栈丢给调用方、账本全丢。

        原 `test_check_never_raises` 三个输入都在**加载阶段**就失败了，走不到授权层，
        所以那半程的异常泄漏它一个都发现不了。"""
        d = self._write("PermOp",
                        'GOLDEN_SOURCE = "torch torch.x"\nGOLDEN_PROVENANCE = "p"\n'
                        'def golden_fn(i, a): return i[0]\n'
                        'GOLDEN_CONTRACT = {"source": "single_api", "method_kind": "torch_cpu",\n'
                        '    "method": "torch.x",\n'
                        '    "authorization": {"kind": "oracle_method",\n'
                        '        "cite": "task_doc.snapshot.md:1", "quote": "q"},\n'
                        '    "taskdoc_snapshot": {"sha256": "d"}}\n')
        snap = os.path.join(d, "task_doc.snapshot.md")
        with io.open(snap, "w", encoding="utf-8") as fh:
            fh.write("q\n")
        os.chmod(snap, 0o000)
        self.addCleanup(lambda: os.chmod(snap, 0o644))
        if os.access(snap, os.R_OK):                  # root 无视权限位 → 该场景在此环境不可构造
            self.skipTest("当前用户可无视权限位读该文件（root?），本场景构造不出来")
        try:
            led = check_golden.check("PermOp")
        except Exception as ex:                       # noqa: BLE001 — 正是本测试要证的
            self.fail(f"check() 抛了 {type(ex).__name__}: {ex}（承诺是一律账本化）")
        self.assertTrue(led["error"], led)
        self.assertEqual(check_golden._exit_code(led), 1)

    def test_snapshot_symlink_rejected(self):
        """快照最终文件是软链 → 拒。`op_dir` 只逐段查**目录**，挡不住引文锚被指到 ops_root 之外。"""
        d = self._write("LinkOp",
                        'GOLDEN_SOURCE = "torch torch.x"\nGOLDEN_PROVENANCE = "p"\n'
                        'def golden_fn(i, a): return i[0]\n'
                        'GOLDEN_CONTRACT = {"source": "single_api", "method_kind": "torch_cpu",\n'
                        '    "method": "torch.x",\n'
                        '    "authorization": {"kind": "impl_reference"}}\n')
        outside = os.path.join(self.root, "elsewhere.md")
        with io.open(outside, "w", encoding="utf-8") as fh:
            fh.write("任意内容\n")
        os.symlink(outside, os.path.join(d, "task_doc.snapshot.md"))
        rc, led = self._rc("LinkOp")
        self.assertEqual(rc, 1, led)
        self.assertIn("符号链接", led["error"])


class CheckGoldenLedgerTest(unittest.TestCase):
    """账本里几个供编排/报告消费的字段，别悄悄改名。"""

    def test_ledger_keys_stable(self):
        led = check_golden.check("NeverExisted")
        for k in ("op", "contract_ok", "tier", "needs_human_review",
                  "blocked_reason", "authorized", "authorization_reason", "error"):
            self.assertIn(k, led, f"账本缺字段 {k}——编排/报告在读它")

    def test_check_never_raises(self):
        """`check()` 一律账本化，不抛——调用方要拿到完整上下文，不是一个栈。"""
        for op in ("NeverExisted", "../逃逸", ""):
            try:
                led = check_golden.check(op)
            except Exception as ex:               # noqa: BLE001 — 正是本测试要证的
                self.fail(f"check({op!r}) 抛了 {type(ex).__name__}: {ex}")
            self.assertTrue(led["error"], f"check({op!r}) 应把问题记进 error")


if __name__ == "__main__":
    unittest.main()
