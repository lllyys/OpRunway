#!/usr/bin/env python3
"""spec 变更门：收据生命周期 + 六条判据 + `run_workflow` 的三处落点 + **本轮身份锚**。

本门要挡的动作只有一个：**跑不通就改 spec 缩范围**。aclnnRoll 那一轮 3 次字段收缩
（`runner_form` cpp_extension→cpp、dtype 8 种→3 种、用例数吃默认 50）全在它的射程内。

| 层 | 钉住什么 |
|---|---|
| `ReceiptLifecycleTest` | `--init` / `--update` 的写盘口径；`--init` 不得抹掉变更历史 |
| `GateCriteriaTest` | 判据 ①–④；⭐ 摘要**当场重算**、不读自报 |
| `RoundAnchorCriteriaTest` | ⭐⭐ 判据 ⑤/⑥（本轮身份锚）；含「拒绝消息不许把绕法当指引给出去」 |
| `HonestScopeTest` | ⭐ 门自己不许把「有人签了字」说成「用户确认过」 |
| `RunWorkflowEntryGateTest` | 入口门（进 Task1 之前）；⭐ 上一轮 staging 的副本不得替 spec 作证 |
| `RunWorkflowExitGateTest` | 出口门（写验收产物之前）——只拦入口拦不住 |
| `RoundIdentityTest` | ⭐⭐⭐ **一轮只许有一套 spec 身份**：原件 / 收据 / staged 副本三格同时被盯 |
| `GatePlacementDriftTest` | AST 漂移哨：门的位置、被校对象、以及**三处吃同一个入口锚** |

⭐ 2026-08-06 审修门 High 补的那一格（`RoundIdentityTest` + `RoundAnchorCriteriaTest`）：
入口与出口**各自都成立、合起来仍不成立**——两道门问的都是「此刻这两个值一致吗」，
所以「运行中把 spec 换成 B **且同时** `--update` 把收据也改成 B」两边全绿，
而这一轮实际按 A 执行。修法是在入口把 spec 字节冻结成进程内的 `entry_spec_sha256`，
之后原件、收据、staged 副本三者都必须仍等于它。

不访问 NPU：真机侧全用 `test_run_workflow_source_staging` 那套夹具替身。
⚠ 用 `import … as STG` 而非 `from … import`：把别的模块的 TestCase 拉进本模块命名空间会被
  重复收集一遍，测试数虚增、失败定位也乱。
"""

import ast
import inspect
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import run_workflow as W
import spec_change_gate as SCG
import test_run_workflow_source_staging as STG

_HERE = os.path.dirname(os.path.abspath(__file__))
_REASON = "任务书要求 8 种 dtype，本轮先按 7 种跑（complex64 造不出输入）"
_BY = "lys"


def _spec_file(root, payload=None):
    """写一份 spec 原件；`payload` 缺省是一份**合法**的 cpp_extension spec。"""
    path = os.path.join(root, "the.spec.json")
    with open(path, "w", encoding="utf-8") as out:
        json.dump(payload if payload is not None else STG._spec(), out)
    return path


def _narrowed_spec():
    """「跑不通就改 spec 缩范围」的见证形态：把 dtype 面砍窄。

    ⚠ 中立见证，不含任何按算子身份的分支——门看的只是**字节变了**，不理解改的是哪一栏。
    """
    return dict(STG._spec(), precision={"dtype_required": ["float32"]})


def _read_receipt(out_dir):
    with open(SCG.receipt_path(out_dir), encoding="utf-8") as fh:
        return json.load(fh)


def _write_receipt(out_dir, payload):
    """绕过 `spec_change_gate` 直接落一份收据（用来造判据的反例）。"""
    path = SCG.receipt_path(out_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as out:
        if isinstance(payload, str):
            out.write(payload)
        else:
            json.dump(payload, out, ensure_ascii=False)
    return path


def _good_receipt(spec_path):
    return {"schema": SCG.SCHEMA, "schema_version": SCG.SCHEMA_VERSION,
            "spec_sha256": SCG.spec_digest(spec_path),
            "previous_spec_sha256": None,
            "change_reason": _REASON, "confirmed_by": _BY}


class ReceiptLifecycleTest(unittest.TestCase):
    def test_init_records_the_current_digest_and_a_null_previous(self):
        with tempfile.TemporaryDirectory() as root:
            spec_path = _spec_file(root)
            out_dir = os.path.join(root, "reports")
            SCG.init_receipt(spec_path, out_dir, _REASON, _BY)
            got = _read_receipt(out_dir)
            self.assertEqual(got["spec_sha256"], SCG.spec_digest(spec_path))
            self.assertIsNone(got["previous_spec_sha256"])
            self.assertEqual((got["change_reason"], got["confirmed_by"]), (_REASON, _BY))
            self.assertEqual(got["schema"], SCG.SCHEMA)
            # 自产的收据必须当场过得了自己的门，否则这工具产的是一份废纸。
            self.assertEqual(SCG.check(spec_path, out_dir), [])

    def test_init_refuses_when_a_receipt_already_exists(self):
        """⭐ 否则「改完 spec 再 init 一次」= 把 `previous_spec_sha256` 抹回 null，变更历史一笔勾销。"""
        with tempfile.TemporaryDirectory() as root:
            spec_path = _spec_file(root)
            out_dir = os.path.join(root, "reports")
            SCG.init_receipt(spec_path, out_dir, _REASON, _BY)
            _spec_file(root, _narrowed_spec())
            with self.assertRaises(SystemExit) as cm:
                SCG.init_receipt(spec_path, out_dir, "缩一下范围", _BY)
            self.assertIn("--update", str(cm.exception))
            self.assertIsNone(_read_receipt(out_dir)["previous_spec_sha256"],
                              "被拒的 init 不得改动盘上的收据")

    def test_update_rotates_the_old_digest_into_previous(self):
        with tempfile.TemporaryDirectory() as root:
            spec_path = _spec_file(root)
            out_dir = os.path.join(root, "reports")
            SCG.init_receipt(spec_path, out_dir, _REASON, _BY)
            first = SCG.spec_digest(spec_path)
            _spec_file(root, _narrowed_spec())
            SCG.update_receipt(spec_path, out_dir, "dtype 砍到 1 种", "lllyys")
            got = _read_receipt(out_dir)
            self.assertEqual(got["previous_spec_sha256"], first)
            self.assertEqual(got["spec_sha256"], SCG.spec_digest(spec_path))
            self.assertNotEqual(got["spec_sha256"], first)
            self.assertEqual(got["confirmed_by"], "lllyys")

    def test_update_without_a_baseline_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(SystemExit, r"--init"):
                SCG.update_receipt(_spec_file(root), os.path.join(root, "reports"),
                                   _REASON, _BY)

    def test_update_on_a_corrupt_receipt_is_refused(self):
        """把一份坏收据静默改写成合格收据 = 用一次 update 洗掉「这里出过异常」。"""
        with tempfile.TemporaryDirectory() as root:
            spec_path = _spec_file(root)
            out_dir = os.path.join(root, "reports")
            _write_receipt(out_dir, "{ 这不是 JSON")
            with self.assertRaisesRegex(SystemExit, r"不是合法 JSON"):
                SCG.update_receipt(spec_path, out_dir, _REASON, _BY)

    def test_update_on_a_receipt_without_a_usable_digest_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            spec_path = _spec_file(root)
            out_dir = os.path.join(root, "reports")
            _write_receipt(out_dir, dict(_good_receipt(spec_path), spec_sha256="deadbeef"))
            with self.assertRaisesRegex(SystemExit, r"上一版是什么"):
                SCG.update_receipt(spec_path, out_dir, _REASON, _BY)

    def test_writers_refuse_placeholder_declarations(self):
        """本工具**不产**一份自己过不了门的收据（否则「写了就有」变成实质放行）。"""
        with tempfile.TemporaryDirectory() as root:
            spec_path = _spec_file(root)
            for i, bad in enumerate(("", "   ", "TBD", "auto", "自动填充", "N/A", "---", "claude")):
                with self.subTest(bad=bad):
                    out_dir = os.path.join(root, f"r-{i}")
                    with self.assertRaisesRegex(SystemExit, r"不合格"):
                        SCG.init_receipt(spec_path, out_dir, _REASON, bad)
                    with self.assertRaisesRegex(SystemExit, r"不合格"):
                        SCG.init_receipt(spec_path, out_dir, bad, _BY)
                    self.assertFalse(os.path.exists(SCG.receipt_path(out_dir)),
                                     "被拒的写入不得留下半份收据")

    def test_reinit_after_a_manual_delete_is_the_known_way_around_the_history(self):
        """如实记账：删掉收据再 `--init` 就能把历史清零——本门拦不住，别声称它拦得住。

        钉住它是为了让「已知破绽」有一处**可执行**的说明，而不是只写在注释里。
        """
        with tempfile.TemporaryDirectory() as root:
            spec_path = _spec_file(root)
            out_dir = os.path.join(root, "reports")
            SCG.init_receipt(spec_path, out_dir, _REASON, _BY)
            _spec_file(root, _narrowed_spec())
            os.remove(SCG.receipt_path(out_dir))
            SCG.init_receipt(spec_path, out_dir, "重新起个头", _BY)
            self.assertIsNone(_read_receipt(out_dir)["previous_spec_sha256"])
            self.assertEqual(SCG.check(spec_path, out_dir), [],
                             "这条路确实过得去——所以文档里不许把本门说成防篡改")


class GateCriteriaTest(unittest.TestCase):
    def test_missing_receipt_is_blocked(self):
        with tempfile.TemporaryDirectory() as root:
            problems = SCG.check(_spec_file(root), os.path.join(root, "reports"))
            self.assertTrue(any("收据不存在" in p for p in problems), problems)

    def test_changed_spec_without_an_update_is_blocked(self):
        """⭐ 主门：spec 改了一个字节、收据没更新 → 拒。"""
        with tempfile.TemporaryDirectory() as root:
            spec_path = _spec_file(root)
            out_dir = os.path.join(root, "reports")
            SCG.init_receipt(spec_path, out_dir, _REASON, _BY)
            self.assertEqual(SCG.check(spec_path, out_dir), [])
            with open(spec_path, "a", encoding="utf-8") as fh:
                fh.write(" ")                      # 一个字节
            problems = SCG.check(spec_path, out_dir)
            self.assertTrue(any("spec 已变更但收据未更新" in p for p in problems), problems)

    def test_updating_the_receipt_lets_the_changed_spec_through(self):
        with tempfile.TemporaryDirectory() as root:
            spec_path = _spec_file(root)
            out_dir = os.path.join(root, "reports")
            SCG.init_receipt(spec_path, out_dir, _REASON, _BY)
            _spec_file(root, _narrowed_spec())
            self.assertTrue(SCG.check(spec_path, out_dir))          # 先拒
            SCG.update_receipt(spec_path, out_dir, "dtype 收窄，见 gap 记账", _BY)
            self.assertEqual(SCG.check(spec_path, out_dir), [])      # 声明后放行

    def test_the_digest_is_recomputed_from_the_file_not_read_from_the_receipt(self):
        """⭐ 判据 ②「不读自报」：判定结果必须随**盘上字节**变化，而不是随收据里那串字符串变化。

        两个方向都钉：
          · 收据自报一串合法但错的摘要 → 拒（自报值不被当权威）；
          · spec 改了再改回来 → 又能过（说明每次都真的重算，没有缓存或一次性快照）。
        """
        with tempfile.TemporaryDirectory() as root:
            spec_path = _spec_file(root)
            out_dir = os.path.join(root, "reports")
            _write_receipt(out_dir, dict(_good_receipt(spec_path), spec_sha256="a" * 64))
            self.assertTrue(any("spec 已变更但收据未更新" in p
                                for p in SCG.check(spec_path, out_dir)))

            os.remove(SCG.receipt_path(out_dir))
            SCG.init_receipt(spec_path, out_dir, _REASON, _BY)
            with open(spec_path, "rb") as fh:
                original = fh.read()
            _spec_file(root, _narrowed_spec())
            self.assertTrue(SCG.check(spec_path, out_dir))
            with open(spec_path, "wb") as fh:                # 原样改回去
                fh.write(original)
            self.assertEqual(SCG.check(spec_path, out_dir), [])

    def test_placeholder_declarations_are_blocked(self):
        # ⚠ 连字符/下划线的排版变体（AUTO-FILL / AUTO_FILL）刻意在列：词表若靠穷举排版硬撑，
        #   一定漏——`_normalize` 把它们归一成空格版，词表才只需收一份。
        cases = ("", "   ", "n/a", "N/A", "TBD", "todo", "auto", "AUTO-FILL", "AUTO_FILL",
                 "auto-filled", "unknown", "待填", "自动填充", "占位符", "用户", "agent",
                 "orchestrator", "Claude", "codex", "---", "???")
        with tempfile.TemporaryDirectory() as root:
            spec_path = _spec_file(root)
            for i, bad in enumerate(cases):
                for field, no in (("confirmed_by", "③"), ("change_reason", "④")):
                    with self.subTest(field=field, bad=bad):
                        out_dir = os.path.join(root, f"{field}-{i}")
                        _write_receipt(out_dir, dict(_good_receipt(spec_path), **{field: bad}))
                        problems = SCG.check(spec_path, out_dir)
                        self.assertTrue(any(p.startswith(no) for p in problems), problems)

    def test_non_string_declarations_are_blocked(self):
        with tempfile.TemporaryDirectory() as root:
            spec_path = _spec_file(root)
            for i, bad in enumerate((None, 0, 1, True, [], {}, ["lys"])):
                with self.subTest(bad=bad):
                    out_dir = os.path.join(root, f"ns-{i}")
                    _write_receipt(out_dir, dict(_good_receipt(spec_path), confirmed_by=bad))
                    self.assertTrue(any("不是字符串" in p
                                        for p in SCG.check(spec_path, out_dir)))

    def test_malformed_receipt_shapes_are_blocked(self):
        """收据坏成什么样都不得静默放行（fail-closed 的常规体检）。"""
        with tempfile.TemporaryDirectory() as root:
            spec_path = _spec_file(root)
            good = _good_receipt(spec_path)
            shapes = {
                "不是 JSON": "{ nope",
                "不是 object": [good],
                "schema 不对": dict(good, schema="oprunway.something_else"),
                "版本不对": dict(good, schema_version=SCG.SCHEMA_VERSION + 1),
                "缺 previous 键": {k: v for k, v in good.items()
                                   if k != "previous_spec_sha256"},
                "previous 不是 sha": dict(good, previous_spec_sha256="nope"),
                "spec_sha256 不是 sha": dict(good, spec_sha256="NOPE"),
                "spec_sha256 大写": dict(good, spec_sha256=good["spec_sha256"].upper()),
                "缺 confirmed_by": {k: v for k, v in good.items() if k != "confirmed_by"},
                "缺 change_reason": {k: v for k, v in good.items() if k != "change_reason"},
            }
            for i, (label, payload) in enumerate(shapes.items()):
                with self.subTest(label):
                    out_dir = os.path.join(root, f"m-{i}")
                    _write_receipt(out_dir, payload)
                    self.assertTrue(SCG.check(spec_path, out_dir), f"{label} 竟然放行了")

    def test_symlinked_receipt_is_not_followed(self):
        """收据是判定依据；跟着软链走等于让门去读别处的文件。"""
        with tempfile.TemporaryDirectory() as root:
            spec_path = _spec_file(root)
            out_dir = os.path.join(root, "reports")
            elsewhere = os.path.join(root, "elsewhere.json")
            with open(elsewhere, "w", encoding="utf-8") as out:
                json.dump(_good_receipt(spec_path), out)
            os.makedirs(os.path.dirname(SCG.receipt_path(out_dir)))
            os.symlink(elsewhere, SCG.receipt_path(out_dir))
            self.assertTrue(any("符号链接" in p for p in SCG.check(spec_path, out_dir)))

    def test_unreadable_spec_is_blocked_not_skipped(self):
        with tempfile.TemporaryDirectory() as root:
            spec_path = _spec_file(root)
            out_dir = os.path.join(root, "reports")
            SCG.init_receipt(spec_path, out_dir, _REASON, _BY)
            os.remove(spec_path)
            self.assertTrue(any("无从重算摘要" in p
                                for p in SCG.check(spec_path, out_dir)))

    def test_assert_confirmed_raises_with_the_label_and_the_way_out(self):
        with tempfile.TemporaryDirectory() as root:
            spec_path = _spec_file(root)
            out_dir = os.path.join(root, "reports")
            with self.assertRaises(SystemExit) as cm:
                SCG.assert_confirmed(spec_path, out_dir, "① 入口门",
                                     expected_sha256=SCG.spec_digest(spec_path))
            msg = str(cm.exception)
            self.assertIn(SCG.BLOCKED_LABEL, msg)
            self.assertIn("--init", msg)          # 怎么建基线
            self.assertIn("--update", msg)        # 改过了怎么办
            self.assertIn("① 入口门", msg)         # 是哪一道门拦的

    def test_assert_confirmed_is_silent_when_everything_lines_up(self):
        with tempfile.TemporaryDirectory() as root:
            spec_path = _spec_file(root)
            out_dir = os.path.join(root, "reports")
            SCG.init_receipt(spec_path, out_dir, _REASON, _BY)
            SCG.assert_confirmed(spec_path, out_dir, "① 入口门",
                                 expected_sha256=SCG.spec_digest(spec_path))

    def test_assert_confirmed_refuses_to_run_without_a_round_anchor(self):
        """⭐ `expected_sha256` 是**必填关键字参数**，没有缺省。

        「忘了传锚」与「静默退回只校当前值」在验收链上是同一件事——正是本门 2026-08-06
        被绕过去的那个形状。做成必填后，漏传当场 `TypeError`；给成默认 `None` 的话，
        下一个人删掉一处实参，测试全绿而门已经废了。
        """
        with tempfile.TemporaryDirectory() as root:
            spec_path = _spec_file(root)
            out_dir = os.path.join(root, "reports")
            SCG.init_receipt(spec_path, out_dir, _REASON, _BY)
            with self.assertRaises(TypeError):
                SCG.assert_confirmed(spec_path, out_dir, "① 入口门")


class RoundAnchorCriteriaTest(unittest.TestCase):
    """⭐⭐ 判据 ⑤/⑥：本轮身份锚。

    ①–④ 只问「**此刻**这两个值一致吗」。于是「运行中把 spec 换成 B，**同时** `--update`
    把收据也改成 B」两边仍然自洽 —— 门全绿，而这一轮实际按 A 跑。⑤/⑥ 把两边分别钉到
    入口冻结的那个摘要上，才拦得住这种「各自成立、合起来不成立」的绕法。
    """

    def _round(self, root):
        """摆一个「入口已冻结」的现场：返回 `(spec_path, out_dir, 入口摘要)`。"""
        spec_path = _spec_file(root)
        out_dir = os.path.join(root, "reports")
        SCG.init_receipt(spec_path, out_dir, _REASON, _BY)
        return spec_path, out_dir, SCG.spec_digest(spec_path)

    def test_nothing_moved_still_passes(self):
        """反面见证：⑤/⑥ 拦的是**中途被换**，不是「凡是给了锚就拒」。"""
        with tempfile.TemporaryDirectory() as root:
            spec_path, out_dir, entry = self._round(root)
            self.assertEqual(SCG.check(spec_path, out_dir, entry), [])

    def test_spec_and_receipt_moved_together_is_still_refused(self):
        """⭐⭐ **本 finding 的核心判据**：两边一起改到 B，①–④ 一条都不响。

        中间那句断言就是反证——没有 ⑤/⑥ 的话（`expected_sha256=None`），这一轮**放行**。
        """
        with tempfile.TemporaryDirectory() as root:
            spec_path, out_dir, entry = self._round(root)
            _spec_file(root, _narrowed_spec())                       # 原件 A → B
            SCG.update_receipt(spec_path, out_dir, "跑不通，缩一下", _BY)   # 收据 A → B

            self.assertEqual(SCG.check(spec_path, out_dir), [],
                             "反证：只看「当前 spec ↔ 当前收据」的话，这一轮是放行的")

            problems = SCG.check(spec_path, out_dir, entry)
            self.assertTrue(any(p.startswith("⑤") for p in problems), problems)
            self.assertTrue(any(p.startswith("⑥") for p in problems), problems)

    def test_only_the_receipt_moved_is_refused_by_six(self):
        """只把收据指到别的 spec（原件一个字节没动）——⑤ 看不见，⑥ 必须响。"""
        with tempfile.TemporaryDirectory() as root:
            spec_path, out_dir, entry = self._round(root)
            other = os.path.join(root, "other.spec.json")
            with open(other, "w", encoding="utf-8") as fh:
                json.dump(_narrowed_spec(), fh)
            _write_receipt(out_dir, dict(_good_receipt(other),
                                         previous_spec_sha256=entry))
            problems = SCG.check(spec_path, out_dir, entry)
            self.assertFalse(any(p.startswith("⑤") for p in problems), problems)
            self.assertTrue(any(p.startswith("⑥") for p in problems), problems)

    def test_only_the_spec_moved_is_refused_by_five(self):
        with tempfile.TemporaryDirectory() as root:
            spec_path, out_dir, entry = self._round(root)
            _spec_file(root, _narrowed_spec())     # 只换原件，收据不动
            problems = SCG.check(spec_path, out_dir, entry)
            self.assertTrue(any(p.startswith("⑤") for p in problems), problems)
            self.assertFalse(any(p.startswith("⑥") for p in problems), problems)

    def test_the_anchor_message_does_not_teach_the_bypass(self):
        """⭐ ⑤/⑥ 的「怎么办」**不许**是 `--update`——那正是绕法本身。

        ①–④ 的出路才是 `--init` / `--update`；两类混着给一句话，等于门自己把绕法
        写成操作指引（本仓最不能容忍的那种「看着有、实际教人绕」的东西）。
        """
        with tempfile.TemporaryDirectory() as root:
            spec_path, out_dir, entry = self._round(root)
            _spec_file(root, _narrowed_spec())
            SCG.update_receipt(spec_path, out_dir, "跑不通，缩一下", _BY)
            msg = SCG.blocked_message(spec_path, out_dir, "② 出口门",
                                      SCG.check(spec_path, out_dir, entry))
            self.assertIn("别用 --update", msg)
            self.assertIn("重跑", msg)
            self.assertNotIn("--init", msg,
                             "⑤/⑥ 的现场不该给建基线的指引——收据早就有了")

    def test_the_ordinary_message_still_gives_the_ordinary_way_out(self):
        """反面见证：没有 ⑤/⑥ 时，①–④ 那套指引一个字没少。"""
        with tempfile.TemporaryDirectory() as root:
            msg = SCG.blocked_message(_spec_file(root), os.path.join(root, "reports"),
                                      "① 入口门", ["① 收据不存在"])
            self.assertIn("--init", msg)
            self.assertIn("--update", msg)
            self.assertNotIn("别用 --update", msg)

    def test_a_stale_receipt_at_the_entry_gate_still_gets_the_update_advice(self):
        """⭐ 分叉口径的要害一格：**⑥ 单独成立时 `--update` 仍是正当出路**。

        入口门那里 `expected == actual`（锚就是刚读到的这串字节），收据过期时只响 ②+⑥。
        那是最普通的「spec 改了、忘了记一笔」，操作者**就该**去 `--update`。
        若把「别用 --update」按 ⑤/⑥ 一刀切地甩出去，等于把一句正确的指引堵死——
        门变得没法用，人只会绕着它走。
        """
        with tempfile.TemporaryDirectory() as root:
            spec_path, out_dir, _ = self._round(root)
            _spec_file(root, _narrowed_spec())          # 开跑**之前**就改了 spec
            entry = SCG.spec_digest(spec_path)          # 入口锚 = 现在这份（B）
            problems = SCG.check(spec_path, out_dir, entry)
            self.assertFalse(any(p.startswith("⑤") for p in problems), problems)
            self.assertTrue(any(p.startswith("⑥") for p in problems), problems)
            msg = SCG.blocked_message(spec_path, out_dir, "① 入口门", problems)
            self.assertIn("--update", msg)
            self.assertNotIn("别用 --update", msg)

    def test_bytes_digest_and_spec_digest_agree_on_the_same_bytes(self):
        """入口锚走 `bytes_digest`（不碰盘），但算法必须与 ② 的 `spec_digest` 同一个——
        否则「同一份 spec」在两条判据里会得出两个值，门永远拒。"""
        with tempfile.TemporaryDirectory() as root:
            spec_path = _spec_file(root)
            with open(spec_path, "rb") as fh:
                self.assertEqual(SCG.bytes_digest(fh.read()), SCG.spec_digest(spec_path))


class HonestScopeTest(unittest.TestCase):
    """⭐ 这道门**证到哪一步**必须在产物与文档里说清楚。

    收据无密钥：编排层自己填一个像人名的字符串就能过。本批不解决它，但**不许**把放行
    描述成「用户已确认」——那就又多一道「看着有、实际拦不住」的门，正是本仓最不能容忍的东西。
    """

    def test_module_doc_states_what_the_gate_cannot_prove(self):
        doc = SCG.__doc__
        for must in ("不证", "用户确认过", "无密钥"):
            self.assertIn(must, doc, f"模块文档缺「{must}」这句话")

    def test_blocked_message_disclaims_the_stronger_claim(self):
        with tempfile.TemporaryDirectory() as root:
            msg = SCG.blocked_message(_spec_file(root), os.path.join(root, "reports"),
                                      "① 入口门", ["① 收据不存在"])
            self.assertIn("不证", msg)
            self.assertIn("用户确认过", msg)

    def test_cli_success_line_does_not_claim_user_confirmation(self):
        """放行时打印的那句话也不许升格——人读报告常常只抄这一行。"""
        with tempfile.TemporaryDirectory() as root:
            spec_path = _spec_file(root)
            out_dir = os.path.join(root, "reports")
            SCG.init_receipt(spec_path, out_dir, _REASON, _BY)
            proc = _cli("--spec", spec_path, "--out", out_dir, "--check")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("PASSED", proc.stdout)
            self.assertIn("用户确认过", proc.stdout)
            self.assertIn("不", proc.stdout.split("PASSED", 1)[1])

    def test_run_workflow_documents_the_gate_without_overstating_it(self):
        doc = W.run.__doc__
        self.assertIn("spec 变更未确认", doc)
        self.assertIn("不是", doc.split("spec 变更门", 1)[1])


def _cli(*argv):
    return subprocess.run(
        [sys.executable, os.path.join(_HERE, "spec_change_gate.py"), *argv],
        capture_output=True, text=True, check=False)


class CliTest(unittest.TestCase):
    def test_check_exit_codes_separate_blocked_from_usage_error(self):
        with tempfile.TemporaryDirectory() as root:
            spec_path = _spec_file(root)
            out_dir = os.path.join(root, "reports")
            blocked = _cli("--spec", spec_path, "--out", out_dir, "--check")
            self.assertEqual(blocked.returncode, SCG.EXIT_BLOCKED)
            self.assertIn(SCG.BLOCKED_LABEL, blocked.stderr)

            init = _cli("--spec", spec_path, "--out", out_dir, "--init",
                        "--reason", _REASON, "--by", _BY)
            self.assertEqual(init.returncode, 0, init.stderr)
            self.assertEqual(
                _cli("--spec", spec_path, "--out", out_dir, "--check").returncode, 0)

            _spec_file(root, _narrowed_spec())
            self.assertEqual(
                _cli("--spec", spec_path, "--out", out_dir, "--check").returncode,
                SCG.EXIT_BLOCKED)
            upd = _cli("--spec", spec_path, "--out", out_dir, "--update",
                       "--reason", "dtype 收窄", "--by", _BY)
            self.assertEqual(upd.returncode, 0, upd.stderr)
            self.assertEqual(
                _cli("--spec", spec_path, "--out", out_dir, "--check").returncode, 0)

    def test_init_and_update_require_reason_and_by(self):
        """⚠ 缺席一律拒——**不给缺省、更不自动填**：自动填出来的署名正是本门要拦的东西。"""
        with tempfile.TemporaryDirectory() as root:
            spec_path = _spec_file(root)
            out_dir = os.path.join(root, "reports")
            for action in ("--init", "--update"):
                for extra in ((), ("--reason", _REASON), ("--by", _BY)):
                    with self.subTest(action=action, extra=extra):
                        proc = _cli("--spec", spec_path, "--out", out_dir, action, *extra)
                        self.assertEqual(proc.returncode, 2, proc.stderr)
                        self.assertIn("不得自动填", proc.stderr)
            self.assertFalse(os.path.exists(SCG.receipt_path(out_dir)))

    def test_actions_are_mutually_exclusive_and_required(self):
        with tempfile.TemporaryDirectory() as root:
            spec_path = _spec_file(root)
            out_dir = os.path.join(root, "reports")
            self.assertEqual(_cli("--spec", spec_path, "--out", out_dir).returncode, 2)
            self.assertEqual(
                _cli("--spec", spec_path, "--out", out_dir, "--init", "--check",
                     "--reason", _REASON, "--by", _BY).returncode, 2)


class _Sentinel(RuntimeError):
    """夹具用：证明控制流**走到了** Task1（而不是被门拦掉）。"""


class RunWorkflowEntryGateTest(unittest.TestCase):
    """① 入口门：进 Task1 之前。"""

    def test_acceptance_run_without_a_receipt_is_blocked_before_task1(self):
        with tempfile.TemporaryDirectory() as root, STG._env(root):
            spec_path = _spec_file(root)
            facts = STG._write_source_facts(
                os.path.join(root, "fetch", "source_facts.json"))
            out_dir = os.path.join(root, "reports", "widget")
            with mock.patch.object(W.gen_cases, "gen_cases",
                                   side_effect=_Sentinel("不该走到 Task1")):
                with self.assertRaisesRegex(SystemExit, r"spec 变更未确认"):
                    W.run(spec_path, mode="cpp_extension", out_dir=out_dir,
                          source_facts=facts)
            self.assertFalse(os.path.exists(out_dir),
                             "门必须在 makedirs 之前拒，不留半个产物目录")

    def test_a_confirmed_spec_reaches_task1(self):
        """反面见证：门放行的证据只能是**控制流真的穿过去了**。"""
        with tempfile.TemporaryDirectory() as root, STG._env(root):
            out_dir = os.path.join(root, "reports", "widget")
            facts = STG._write_source_facts(
                os.path.join(root, "fetch", "source_facts.json"))
            spec_path = _spec_file(root)
            SCG.init_receipt(spec_path, out_dir, _REASON, _BY)
            with mock.patch.object(W.gen_cases, "gen_cases",
                                   side_effect=_Sentinel("到 Task1 了")):
                with self.assertRaises(_Sentinel):
                    W.run(spec_path, mode="cpp_extension", out_dir=out_dir,
                          source_facts=facts)

    def test_the_previous_runs_staged_copy_cannot_stand_in_for_the_spec(self):
        """⭐⭐ 门校的是 `--spec` **原件**，不是 `<out>/spec.json` 那份 staging 副本。

        入口门跑在 staging **之前**，那时报告目录里躺着的是**上一轮**的副本。若门去校它，
        「改了 spec 再跑一遍同一个 `--out`」会一路放行——本门就等于不存在。
        末尾那条断言正是反证：副本与旧收据仍然对得上。
        """
        with tempfile.TemporaryDirectory() as root, STG._env(root):
            out_dir = os.path.join(root, "reports", "widget")
            facts = STG._write_source_facts(
                os.path.join(root, "fetch", "source_facts.json"))
            spec_path = _spec_file(root)
            SCG.init_receipt(spec_path, out_dir, _REASON, _BY)
            with STG.AcceptanceRunTest()._stubbed([]):
                W.run(spec_path, mode="cpp_extension", out_dir=out_dir,
                      source_facts=facts)
            staged = os.path.join(out_dir, W._STAGED_SPEC_FILE)
            self.assertTrue(os.path.isfile(staged))

            _spec_file(root, _narrowed_spec())         # 缩范围，且**不**更新收据
            with STG.AcceptanceRunTest()._stubbed([]):
                with self.assertRaisesRegex(SystemExit, r"spec 变更未确认"):
                    W.run(spec_path, mode="cpp_extension", out_dir=out_dir,
                          source_facts=facts)
            self.assertEqual(SCG.spec_digest(staged), _read_receipt(out_dir)["spec_sha256"],
                             "反证：校 staging 副本的话，这一轮会被放行")

    def test_non_acceptance_paths_are_not_subject_to_the_gate(self):
        """显式非验收 mode 不受此门约束——它们物理上不产验收裁决。

        ⚠ 这不是开后门：那些路写的是 `dev_run_summary.json` / `dev_precision_check.json`，
        「改 spec 缩范围」在它们身上产不出一份冒充达标的验收结论。

        ⚠ 第二条夹具 2026-08-06 换过：原来是 `aclnn_py` + `--allow-experimental-form`，
        通路收敛后那条路整条不存在（退役形态在 `_resolve_mode` 当场被拒），改用
        「退役形态 + 显式 `--mode mock`」——同样是「非准入 form 走非验收 mode」这一格，
        且是**生产上真正可达**的写法。
        """
        cases = (("mock", STG._spec(), {"mode": "mock"}),
                 ("退役 form + 显式 mock", STG._spec(runner_form="aclnn_py"),
                  {"mode": "mock"}))
        for label, spec, kwargs in cases:
            with self.subTest(label), tempfile.TemporaryDirectory() as root, STG._env(root):
                spec_path = _spec_file(root, spec)
                with mock.patch.object(W.gen_cases, "gen_cases",
                                       side_effect=_Sentinel("到 Task1 了")):
                    with self.assertRaises(_Sentinel):
                        W.run(spec_path, out_dir=os.path.join(root, "reports"), **kwargs)


class RunWorkflowExitGateTest(unittest.TestCase):
    """② 出口门：写验收产物之前。只拦入口是拦不住的（照准入门的口径）。"""

    def test_spec_swapped_after_the_entry_gate_writes_no_verdict_at_all(self):
        """⭐ 入口门放行之后把 spec 换掉 → 一份验收产物都不许落地。

        换掉了还照写的话，报告目录里的裁决与实际驱动本轮执行的 spec 对不上，
        而 CP-F 的 `base_artifacts.spec` 就锚在那份副本上。
        """
        with tempfile.TemporaryDirectory() as root, STG._env(root):
            out_dir = os.path.join(root, "reports", "widget")
            facts = STG._write_source_facts(
                os.path.join(root, "fetch", "source_facts.json"))
            spec_path = _spec_file(root)
            SCG.init_receipt(spec_path, out_dir, _REASON, _BY)

            def _swap_then_gen(spec, work, taskdoc_caseset=None):
                # 形参跟住 `run_workflow` 真实的调用点（理由同 STG._stubbed 的 ⚠）。
                _spec_file(root, _narrowed_spec())
                return STG.AcceptanceRunTest._caseset(spec["op"])

            with STG.AcceptanceRunTest()._stubbed([]), \
                    mock.patch.object(W.gen_cases, "gen_cases",
                                      side_effect=_swap_then_gen):
                with self.assertRaisesRegex(SystemExit, r"spec 变更未确认"):
                    W.run(spec_path, mode="cpp_extension", out_dir=out_dir,
                          source_facts=facts)
            for name in W._ACCEPTANCE_FILES:
                self.assertFalse(os.path.exists(os.path.join(out_dir, name)), name)

    def test_the_same_run_passes_when_the_spec_stays_put(self):
        """反面见证：出口门拦的是**中途被换**，不是「凡是跑到这一步都拦」。"""
        with tempfile.TemporaryDirectory() as root, STG._env(root):
            out_dir = os.path.join(root, "reports", "widget")
            facts = STG._write_source_facts(
                os.path.join(root, "fetch", "source_facts.json"))
            spec_path = _spec_file(root)
            SCG.init_receipt(spec_path, out_dir, _REASON, _BY)
            with STG.AcceptanceRunTest()._stubbed([]):
                result = W.run(spec_path, mode="cpp_extension", out_dir=out_dir,
                               source_facts=facts)
            self.assertTrue(result["is_acceptance"])
            for name in W._ACCEPTANCE_FILES:
                self.assertTrue(os.path.isfile(os.path.join(out_dir, name)), name)


class RoundIdentityTest(unittest.TestCase):
    """⭐⭐ 一轮只许有**一套** spec 身份。

    入口门与出口门**各自都成立、合起来仍不成立**——因为它们锚的都是「此刻的值」，
    没有任何东西锚「这一轮到底按哪份 spec 跑的」。本类钉的就是那个锚
    （`run_workflow` 里的 `entry_spec_sha256`）。

    ⚠ 每条用例都配一句**反证**：把绕法做到底之后，「当前 spec ↔ 当前收据」确实是对得上的。
      没有那句反证，用例只证明「拒了」，证不到「拒的是这条绕法」——那就又是一道假门。
    """

    def _round(self, root):
        """摆好一轮验收：返回 `(spec_path, out_dir, source_facts)`。"""
        out_dir = os.path.join(root, "reports", "widget")
        facts = STG._write_source_facts(
            os.path.join(root, "fetch", "source_facts.json"))
        spec_path = _spec_file(root)
        SCG.init_receipt(spec_path, out_dir, _REASON, _BY)
        return spec_path, out_dir, facts

    def _assert_no_acceptance_artifacts(self, out_dir):
        for name in W._ACCEPTANCE_FILES:
            self.assertFalse(os.path.exists(os.path.join(out_dir, name)), name)

    def test_swapping_the_spec_and_the_receipt_together_mid_run_is_refused(self):
        """⭐⭐⭐ **本 finding 的核心**：中途把 spec 换成 B，**同时**把收据也 `--update` 到 B。

        这不是「门漏了一个分支」——入口那次（A ↔ A）和出口那次（B ↔ B）**各自都成立**，
        合起来却让一轮实际按 A 执行的跑写出一份声称 B 已确认的裁决。
        `test_spec_swapped_after_the_entry_gate_writes_no_verdict_at_all` 只换 spec、
        不更新收据，**覆盖不到**这个绕法。
        """
        with tempfile.TemporaryDirectory() as root, STG._env(root):
            spec_path, out_dir, facts = self._round(root)

            def _swap_both_then_gen(spec, work, taskdoc_caseset=None):
                # 形参跟住 `run_workflow` 真实的调用点（理由同 STG._stubbed 的 ⚠）。
                _spec_file(root, _narrowed_spec())                      # spec  A → B
                SCG.update_receipt(spec_path, out_dir,                  # 收据  A → B
                                   "跑不通，把 dtype 面砍窄", _BY)
                return STG.AcceptanceRunTest._caseset(spec["op"])

            with STG.AcceptanceRunTest()._stubbed([]), \
                    mock.patch.object(W.gen_cases, "gen_cases",
                                      side_effect=_swap_both_then_gen):
                with self.assertRaisesRegex(SystemExit, r"spec 变更未确认"):
                    W.run(spec_path, mode="cpp_extension", out_dir=out_dir,
                          source_facts=facts)
            self._assert_no_acceptance_artifacts(out_dir)
            # ⭐ 反证：绕法确实做成了——盘上「当前 spec ↔ 当前收据」现在完全对得上。
            #    拦住这一轮的只可能是入口冻结的那个摘要。
            self.assertEqual(SCG.check(spec_path, out_dir), [],
                             "反证：只校当前值的话，这一轮会被放行")

    def test_the_refusal_names_the_round_anchor_not_a_plain_mismatch(self):
        """⭐ 拒的**理由**必须是 ⑤/⑥，而不是碰巧被别的判据挡下。

        只断言「抛了 SystemExit」是假门：把入口锚整个删掉，②（当前 spec ↔ 当前收据）
        在这条绕法下照样是绿的，用例却可能因为别的原因继续红/绿，说明不了问题。
        """
        with tempfile.TemporaryDirectory() as root, STG._env(root):
            spec_path, out_dir, facts = self._round(root)

            def _swap_both_then_gen(spec, work, taskdoc_caseset=None):
                _spec_file(root, _narrowed_spec())
                SCG.update_receipt(spec_path, out_dir, "缩范围", _BY)
                return STG.AcceptanceRunTest._caseset(spec["op"])

            with STG.AcceptanceRunTest()._stubbed([]), \
                    mock.patch.object(W.gen_cases, "gen_cases",
                                      side_effect=_swap_both_then_gen):
                with self.assertRaises(SystemExit) as cm:
                    W.run(spec_path, mode="cpp_extension", out_dir=out_dir,
                          source_facts=facts)
            msg = str(cm.exception)
            self.assertIn("⑤", msg)
            self.assertIn("⑥", msg)
            self.assertIn("本轮入口", msg)
            self.assertIn("别用 --update", msg, "拒绝消息不许把绕法当操作指引给出去")

    def test_swapping_only_the_receipt_mid_run_is_refused(self):
        """收据被 `--update` 到另一份 spec，而原件没动。

        ⑤ 看不见（原件还是入口那份），②/⑥ 接住。钉它是因为「只改收据」是这条绕法的
        半步——半步也不许过，否则拼起来就是整步。
        """
        with tempfile.TemporaryDirectory() as root, STG._env(root):
            spec_path, out_dir, facts = self._round(root)
            other = os.path.join(root, "other.spec.json")
            with open(other, "w", encoding="utf-8") as fh:
                json.dump(_narrowed_spec(), fh)

            def _repoint_receipt_then_gen(spec, work, taskdoc_caseset=None):
                _write_receipt(out_dir, dict(_good_receipt(other),
                                             previous_spec_sha256=SCG.spec_digest(spec_path)))
                return STG.AcceptanceRunTest._caseset(spec["op"])

            with STG.AcceptanceRunTest()._stubbed([]), \
                    mock.patch.object(W.gen_cases, "gen_cases",
                                      side_effect=_repoint_receipt_then_gen):
                with self.assertRaisesRegex(SystemExit, r"spec 变更未确认"):
                    W.run(spec_path, mode="cpp_extension", out_dir=out_dir,
                          source_facts=facts)
            self._assert_no_acceptance_artifacts(out_dir)

    def test_deleting_the_receipt_and_re_initing_mid_run_is_refused(self):
        """⭐ 「删收据重 `--init`」这条**跨轮拦不住**的路，在**一轮之内**被锚关上了。

        `ReceiptLifecycleTest.test_reinit_after_a_manual_delete_is_the_known_way_around_the_history`
        如实记着：删掉收据再 `--init`，变更历史一笔勾销，收据机制本身查不出来。
        但那条路要在**本轮**生效，`--init` 写下的必然是**换进来那份** spec 的摘要——
        与入口冻结的对不上，⑤/⑥ 照样响。

        ⚠ 别把这条读成「删收据重 init 的洞补上了」：跨轮那一格**依旧开着**（模块文档里那句
          话一个字不用改）。这里只多说一句更窄的话：**一轮之内**换不了身份。
        """
        with tempfile.TemporaryDirectory() as root, STG._env(root):
            spec_path, out_dir, facts = self._round(root)

            def _reinit_then_gen(spec, work, taskdoc_caseset=None):
                _spec_file(root, _narrowed_spec())              # spec A → B
                os.remove(SCG.receipt_path(out_dir))            # 历史一笔勾销
                SCG.init_receipt(spec_path, out_dir, "重新起个头", _BY)
                return STG.AcceptanceRunTest._caseset(spec["op"])

            with STG.AcceptanceRunTest()._stubbed([]), \
                    mock.patch.object(W.gen_cases, "gen_cases",
                                      side_effect=_reinit_then_gen):
                with self.assertRaisesRegex(SystemExit, r"spec 变更未确认"):
                    W.run(spec_path, mode="cpp_extension", out_dir=out_dir,
                          source_facts=facts)
            self._assert_no_acceptance_artifacts(out_dir)
            # 反证：收据这会儿是一份**全新、合规、对得上当前 spec** 的基线。
            self.assertEqual(SCG.check(spec_path, out_dir), [])
            self.assertIsNone(_read_receipt(out_dir)["previous_spec_sha256"])

    def test_rewriting_the_staged_copy_mid_run_is_refused(self):
        """⭐ 第三格：原件与收据都不动，只把 `<out>/spec.json` 换掉。

        前两格（判据 ⑤/⑥）盯的是原件和收据，**看不见**这一手；而下游读的恰恰是这份副本
        （CP-F 的 `base_artifacts.spec` 就锚在它上面）。
        """
        with tempfile.TemporaryDirectory() as root, STG._env(root):
            spec_path, out_dir, facts = self._round(root)

            def _rewrite_staged_then_gen(spec, work, taskdoc_caseset=None):
                with open(os.path.join(out_dir, W._STAGED_SPEC_FILE),
                          "w", encoding="utf-8") as fh:
                    json.dump(_narrowed_spec(), fh)
                return STG.AcceptanceRunTest._caseset(spec["op"])

            with STG.AcceptanceRunTest()._stubbed([]), \
                    mock.patch.object(W.gen_cases, "gen_cases",
                                      side_effect=_rewrite_staged_then_gen):
                with self.assertRaisesRegex(SystemExit, r"staged spec 已偏离本轮入口"):
                    W.run(spec_path, mode="cpp_extension", out_dir=out_dir,
                          source_facts=facts)
            self._assert_no_acceptance_artifacts(out_dir)
            # ⭐ 反证：原件与收据全程没动，spec 变更门那三条判据一条都没响。
            self.assertEqual(SCG.check(spec_path, out_dir), [],
                             "反证：只盯原件与收据的话，这一轮会被放行")

    def test_the_staged_copy_check_refuses_a_symlink_instead_of_following_it(self):
        """副本被换成软链 = 让门去读别处的文件（口径同收据侧、同 staging 侧的 O_NOFOLLOW）。"""
        with tempfile.TemporaryDirectory() as root, STG._env(root):
            spec_path, out_dir, _ = self._round(root)
            os.makedirs(out_dir, exist_ok=True)
            elsewhere = os.path.join(root, "elsewhere.json")
            with open(elsewhere, "wb") as fh:
                fh.write(b"{}")
            staged = os.path.join(out_dir, W._STAGED_SPEC_FILE)
            os.symlink(elsewhere, staged)
            with self.assertRaisesRegex(SystemExit, r"符号链接"):
                W._assert_staged_spec_matches_entry(
                    out_dir, SCG.bytes_digest(b"{}"))

    def test_a_clean_round_still_writes_both_acceptance_artifacts(self):
        """反面见证：三格锚拦的是**中途被换**，不是「凡是跑到这一步都拦」。

        没有这条，把 `_assert_staged_spec_matches_entry` 写成「无条件 raise」也能让上面
        全绿——那是一道更坏的假门（把所有验收都拦死，看着很安全）。
        """
        with tempfile.TemporaryDirectory() as root, STG._env(root):
            spec_path, out_dir, facts = self._round(root)
            with STG.AcceptanceRunTest()._stubbed([]):
                result = W.run(spec_path, mode="cpp_extension", out_dir=out_dir,
                               source_facts=facts)
            self.assertTrue(result["is_acceptance"])
            for name in W._ACCEPTANCE_FILES:
                self.assertTrue(os.path.isfile(os.path.join(out_dir, name)), name)
            # staged 副本 == 入口那串字节 == 收据记的那串（一轮一套身份）
            staged = os.path.join(out_dir, W._STAGED_SPEC_FILE)
            self.assertEqual(SCG.spec_digest(staged), SCG.spec_digest(spec_path))
            self.assertEqual(SCG.spec_digest(staged), _read_receipt(out_dir)["spec_sha256"])


def _run_def():
    return ast.parse(inspect.getsource(W.run)).body[0]


def _gate_calls(node):
    return [n for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "assert_confirmed"]


def _staged_spec_checks(node):
    """本节点里的 `_assert_staged_spec_matches_entry(...)` 调用（出口门的第三格）。"""
    return [n for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "_assert_staged_spec_matches_entry"]


def _kwarg(call, name):
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _anchor_assignment():
    """`run()` 里给 `entry_spec_sha256` 赋值的那条语句（源码文本）。"""
    for stmt in ast.walk(_run_def()):
        if not isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            continue
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        if any(isinstance(t, ast.Name) and t.id == "entry_spec_sha256" for t in targets):
            return ast.unparse(stmt)
    return None


def _acceptance_dump(stmt):
    """本语句里是否 `_dump(..., "<验收产物名>")`；是则返回那个文件名。"""
    for node in ast.walk(stmt):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_dump"):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and arg.value in W._ACCEPTANCE_FILES:
                    return arg.value
    return None


def _statement_lists(node):
    for outer in ast.walk(node):
        for field in ("body", "orelse", "finalbody"):
            seq = getattr(outer, field, None)
            if isinstance(seq, list) and seq and all(isinstance(s, ast.stmt) for s in seq):
                yield seq


class GatePlacementDriftTest(unittest.TestCase):
    """⭐ 漂移哨：门的**位置**与**被校对象**都不许悄悄变。

    整条链的安全性全压在这两件事上，而它们都是「改一行就没了、测试却照样绿」的那种。
    """

    def test_every_acceptance_artifact_write_is_preceded_by_the_gate(self):
        guarded = set()
        for seq in _statement_lists(_run_def()):
            seen_gate = False
            for stmt in seq:
                if _gate_calls(stmt):
                    seen_gate = True
                name = _acceptance_dump(stmt)
                if name is not None:
                    self.assertTrue(
                        seen_gate,
                        f"写 {name} 之前没有 spec 变更门——只拦入口是拦不住的")
                    guarded.add(name)
        self.assertEqual(guarded, set(W._ACCEPTANCE_FILES),
                         "哨兵自己失效了：没在 run() 里找到全部验收产物的落点")

    def test_the_gate_always_hashes_the_spec_path_argument(self):
        """⭐ 被校对象恒为 `spec_path` 原件。

        改成 `<out>/spec.json` 就成了自己给自己作证：入口门那一处校的会是**上一轮**的副本。
        """
        calls = _gate_calls(_run_def())
        self.assertGreaterEqual(len(calls), 3,
                                "期望入口 1 处 + 出口 2 处（verdict.json / acceptance.json）")
        for call in calls:
            self.assertTrue(call.args, "第一个实参必须显式给出")
            self.assertIsInstance(call.args[0], ast.Name)
            self.assertEqual(call.args[0].id, "spec_path")

    def test_every_gate_call_carries_the_same_round_anchor(self):
        """⭐⭐ 三处门必须吃**同一个** `entry_spec_sha256`。

        少传一处，那一处就退回「只校此刻的两个值」——而这条链的安全性正是「三处都对齐到
        同一个入口摘要」撑起来的。`assert_confirmed` 已把它做成必填关键字参数（漏传即
        TypeError），本哨再钉住**传的是哪一个**：换成别的变量（比如当场重算一次）
        语法上照样跑得通，门却已经废了。
        """
        for call in _gate_calls(_run_def()):
            anchor = _kwarg(call, "expected_sha256")
            self.assertIsNotNone(anchor, "每一处门都必须显式给出 expected_sha256")
            self.assertIsInstance(anchor, ast.Name,
                                  "锚必须是那一个变量本身，不许在实参位现算")
            self.assertEqual(anchor.id, "entry_spec_sha256")

    def test_the_round_anchor_is_taken_from_the_bytes_already_read(self):
        """⭐⭐ 锚只能来自**已经读进内存的字节**，不许从盘上重读。

        从盘上重读（`spec_digest(spec_path)` / 再 `open` 一次 / 读某份落盘的锚文件）
        会把锚重新绑到一个运行期间可被换掉的东西上 —— 那正是本 finding 的病根，
        而且改成那样时**所有行为测试都还是绿的**（攻击者不会在测试里去改那份文件）。
        所以这一格只能由源码哨兵守。
        """
        src = _anchor_assignment()
        self.assertIsNotNone(src, "run() 里找不到 entry_spec_sha256 的赋值——哨兵已失效")
        self.assertIn("staged_payloads", src,
                      "锚必须取自 _read_acceptance_inputs 已读出的那串字节")
        for forbidden in ("spec_digest", "open(", "read"):
            self.assertNotIn(forbidden, src, f"锚不许从盘上取（出现了 {forbidden!r}）")

    def test_the_anchor_storage_decision_stays_written_down(self):
        """⭐ 「锚存在哪」的**理由**必须留在代码里，别只留结论。

        这条不是为了凑文档：`entry_spec_sha256` 看着就像一个「顺手持久化一下会更好」的值，
        而**落盘恰恰是错的**（能改 spec、能跑 `--update` 的那只手，也能改我们写下去的锚文件，
        加了等于没加，还多一份看着像证据的东西）。理由被删掉之后，下一个人几乎必然会去
        「补上持久化」。所以把这三条论据钉住：在内存、跨进程为什么仍成立、落盘为什么无效。
        """
        self.assertIn("内存", W._SPEC_ROUND_ANCHOR_NOTE)
        src = inspect.getsource(W)
        for must in ("跨进程", "落盘", "同一只手"):
            self.assertIn(must, src, f"锚的存放理由里缺「{must}」这一条论据")

    def test_every_acceptance_artifact_write_is_preceded_by_the_staged_copy_check(self):
        """⭐ 出口第三格也要有哨兵：`<out>/spec.json` 副本必须在写产物前复核过。

        原件与收据都不动、只把副本换掉时，判据 ⑤/⑥ 一条都不响——而下游（CP-F 的
        `base_artifacts.spec`、事后单独复跑的三级门）读的恰恰是那份副本。
        """
        guarded = set()
        for seq in _statement_lists(_run_def()):
            seen = False
            for stmt in seq:
                if _staged_spec_checks(stmt):
                    seen = True
                name = _acceptance_dump(stmt)
                if name is not None:
                    self.assertTrue(
                        seen, f"写 {name} 之前没有复核 staged spec 副本")
                    guarded.add(name)
        self.assertEqual(guarded, set(W._ACCEPTANCE_FILES),
                         "哨兵自己失效了：没在 run() 里找到全部验收产物的落点")

    def test_the_receipt_is_not_wiped_by_the_stale_cleanup(self):
        """收据是**输入侧凭证**，不是结论产物：跟着结论一起清 = 每次复跑都得重 init，
        `previous_spec_sha256` 记的变更历史第一次复跑就蒸发。"""
        self.assertNotIn(os.path.join(*SCG.RECEIPT_PARTS), W._RESULT_FILES)
        self.assertNotIn(SCG.RECEIPT_PARTS[-1], W._RESULT_FILES)
        self.assertNotIn(SCG.RECEIPT_PARTS[-1], inspect.getsource(W._invalidate_stale_results))

    def test_stale_cleanup_leaves_the_receipt_on_disk(self):
        """行为面（不只看源码）：跑一轮之后收据仍在原处、仍然过得了门。"""
        with tempfile.TemporaryDirectory() as root, STG._env(root):
            out_dir = os.path.join(root, "reports", "widget")
            facts = STG._write_source_facts(
                os.path.join(root, "fetch", "source_facts.json"))
            spec_path = _spec_file(root)
            SCG.init_receipt(spec_path, out_dir, _REASON, _BY)
            with STG.AcceptanceRunTest()._stubbed([]):
                W.run(spec_path, mode="cpp_extension", out_dir=out_dir, source_facts=facts)
            self.assertTrue(os.path.isfile(SCG.receipt_path(out_dir)))
            self.assertEqual(SCG.check(spec_path, out_dir), [])


if __name__ == "__main__":
    unittest.main()
