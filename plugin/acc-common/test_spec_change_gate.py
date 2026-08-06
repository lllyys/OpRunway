#!/usr/bin/env python3
"""spec 变更门（`spec_change_gate.py`）+ 它在 `run_workflow` 里的两道门 + 非验收注脚分支。

被测的是 aclnnRoll 试跑暴露的两件事：

| 面 | 症状 | 本文件钉住的行为 |
|---|---|---|
| 没有机制阻止「改 spec 缩范围」 | `runner_form` 被改、dtype 从 8 种砍到 3 种，全程无人记过一句为什么 | 验收通路必须有一份 spec 变更收据，`spec_sha256` 由校验方**当场重算**核对；四条判据缺一即 BLOCKED |
| 一句 mock 措辞套所有非验收产物 | `--allow-experimental-form` 下的**真机**跑被标成「NPU 输出 = golden.copy()、性能是编的假数」 | 注脚按真实原因取串：mock / 非准入 form / 其它，各说各的 |

⚠ 本文件多处是**假门反向证明**：光断言「不过的场景确实不过」证不了门是真的——
一个恒抛异常的实现也能全绿。所以每一组负路旁边都配一条正路（合法收据必须放行），
且负路只在**改动那一个字段**上与正路不同。
"""

import contextlib
import inspect
import json
import os
import tempfile
import unittest
from unittest import mock

import content_address
import run_workflow as W
import spec_change_gate as S
#: ⚠ 以**模块**方式引入（不是 `from … import AcceptanceRunTest`）：把别人的 TestCase 类拉进
#: 本模块命名空间会让 unittest 把那一整批用例再跑一遍，红起来还查不清是谁的。
import test_run_workflow_source_staging as ST


def _write_spec_file(path, payload=None):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload if payload is not None else ST._spec(), fh)
    return path


def _reseal(out_dir, payload):
    """把 payload 重新封成一份**摘要自洽**的收据（模拟「攻击者会重算摘要」）。

    ⚠ 没有这一步，负路用例会全部落在「envelope 摘要不匹配」那一条上，
    根本测不到判据 ②③④——那才是真正要证的部分。
    """
    return content_address.write_artifact(out_dir, S.RECEIPT_REL, S.DOMAIN, payload)


class ReceiptLifecycleTest(unittest.TestCase):
    """`--init` / `--update` 的收据生命周期。"""

    @contextlib.contextmanager
    def _fixture(self):
        with tempfile.TemporaryDirectory() as root:
            out_dir = os.path.join(root, "reports", "widget")
            spec_path = _write_spec_file(os.path.join(root, "the.spec.json"))
            yield root, out_dir, spec_path

    def test_init_records_null_previous_and_validates(self):
        with self._fixture() as (_root, out_dir, spec_path):
            S.init_receipt(out_dir, spec_path, "首轮：按任务书抽出的初始 spec", "lys")
            payload = S.validate(out_dir, spec_path)
            self.assertIsNone(payload["previous_spec_sha256"])
            self.assertEqual(payload["spec_sha256"], S.spec_sha256(spec_path))
            self.assertEqual(payload["confirmed_by"], "lys")

    def test_init_refuses_to_overwrite_an_existing_receipt(self):
        """⭐ `--init` 会把 `previous_spec_sha256` 抹回 null = 清掉「发生过变更」的痕迹。"""
        with self._fixture() as (_root, out_dir, spec_path):
            S.init_receipt(out_dir, spec_path, "首轮", "lys")
            with self.assertRaisesRegex(S.SpecChangeError, r"已存在.*--update"):
                S.init_receipt(out_dir, spec_path, "再来一次", "lys")

    def test_update_chains_the_previous_hash(self):
        with self._fixture() as (_root, out_dir, spec_path):
            S.init_receipt(out_dir, spec_path, "首轮", "lys")
            first = S.spec_sha256(spec_path)
            _write_spec_file(spec_path, dict(ST._spec(), tolerance=0.9))   # 改一个字节
            S.update_receipt(out_dir, spec_path, "任务书澄清后放宽容差", "lys")
            payload = S.validate(out_dir, spec_path)
            self.assertEqual(payload["previous_spec_sha256"], first)
            self.assertEqual(payload["spec_sha256"], S.spec_sha256(spec_path))
            self.assertNotEqual(payload["previous_spec_sha256"], payload["spec_sha256"])

    def test_update_without_an_actual_change_is_refused(self):
        """spec 没变却 `--update` = 一份自称记了变更、其实什么都没变的收据。"""
        with self._fixture() as (_root, out_dir, spec_path):
            S.init_receipt(out_dir, spec_path, "首轮", "lys")
            with self.assertRaisesRegex(S.SpecChangeError, r"没有发生变更"):
                S.update_receipt(out_dir, spec_path, "随便写点", "lys")

    def test_update_without_an_existing_receipt_is_blocked(self):
        with self._fixture() as (_root, out_dir, spec_path):
            os.makedirs(out_dir)
            with self.assertRaises(S.SpecChangeBlocked):
                S.update_receipt(out_dir, spec_path, "凭空更新", "lys")

    def test_update_refuses_to_launder_a_broken_receipt(self):
        """坏收据不能靠 `--update` 洗白：读不通就停在读那一步。"""
        with self._fixture() as (_root, out_dir, spec_path):
            S.init_receipt(out_dir, spec_path, "首轮", "lys")
            with open(os.path.join(out_dir, S.RECEIPT_REL), "w", encoding="utf-8") as fh:
                fh.write("{ not json")
            _write_spec_file(spec_path, dict(ST._spec(), tolerance=0.9))
            with self.assertRaises(S.SpecChangeBlocked):
                S.update_receipt(out_dir, spec_path, "改了", "lys")

    def test_writer_side_rejects_placeholders_too(self):
        """⭐ 两端一致：只在读侧拦的话，坏收据能一路落盘，等跑完一轮真机才炸。"""
        with self._fixture() as (_root, out_dir, spec_path):
            for reason, by, label in (("扩 dtype 覆盖", "auto", "confirmed_by 占位符"),
                                      ("扩 dtype 覆盖", "   ", "confirmed_by 空白"),
                                      ("TODO", "lys", "change_reason 占位符"),
                                      ("---", "lys", "change_reason 纯标点")):
                with self.subTest(label):
                    with self.assertRaises(S.SpecChangeError):
                        S.init_receipt(out_dir, spec_path, reason, by)
                    self.assertFalse(os.path.exists(os.path.join(out_dir, S.RECEIPT_REL)),
                                     "被拒时不得落下半份收据")


class GateJudgementTest(unittest.TestCase):
    """四条判据逐条钉住。**每条负路都只改那一个字段**，正路在 `test_a_valid_receipt_passes`。"""

    @contextlib.contextmanager
    def _confirmed(self):
        """一份**完全合法**的现场：spec + 已确认的收据。"""
        with tempfile.TemporaryDirectory() as root:
            out_dir = os.path.join(root, "reports", "widget")
            spec_path = _write_spec_file(os.path.join(root, "the.spec.json"))
            S.init_receipt(out_dir, spec_path, "首轮：按任务书抽出的初始 spec", "lys")
            yield out_dir, spec_path

    def test_a_valid_receipt_passes(self):
        """⭐ 正路。没有它，下面所有负路都可以由「恒 BLOCKED 的假门」满足。"""
        with self._confirmed() as (out_dir, spec_path):
            payload = S.validate(out_dir, spec_path)
            self.assertEqual(payload["schema"], S.SCHEMA)

    # —— 判据 ①：收据存在且 envelope 可信 ————————————————————————————————
    def test_missing_receipt_is_blocked_with_an_actionable_message(self):
        with tempfile.TemporaryDirectory() as root:
            out_dir = os.path.join(root, "reports")
            spec_path = _write_spec_file(os.path.join(root, "the.spec.json"))
            with self.assertRaises(S.SpecChangeBlocked) as cm:
                S.validate(out_dir, spec_path)
            msg = str(cm.exception)
            self.assertIn("--init", msg)        # 要讲清怎么办
            self.assertIn("--update", msg)      # 也要讲清「改过」该走哪条

    def test_tampered_envelope_is_blocked(self):
        """payload 被改而摘要没重算 → envelope 不自洽。"""
        with self._confirmed() as (out_dir, spec_path):
            path = os.path.join(out_dir, S.RECEIPT_REL)
            with open(path, encoding="utf-8") as fh:
                artifact = json.load(fh)
            artifact["payload"]["confirmed_by"] = "someone else"
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(artifact, fh)
            with self.assertRaisesRegex(S.SpecChangeBlocked, r"不可信"):
                S.validate(out_dir, spec_path)

    def test_a_receipt_sealed_under_another_domain_is_blocked(self):
        """域分离：别的工件（哪怕摘要完全自洽）不得冒充 spec 变更收据。"""
        with self._confirmed() as (out_dir, spec_path):
            payload = S.validate(out_dir, spec_path)
            content_address.write_artifact(
                out_dir, S.RECEIPT_REL, "oprunway/some-other-thing/v1", payload)
            with self.assertRaisesRegex(S.SpecChangeBlocked, r"不可信"):
                S.validate(out_dir, spec_path)

    def test_extra_or_missing_payload_keys_are_blocked(self):
        with self._confirmed() as (out_dir, spec_path):
            good = S.validate(out_dir, spec_path)
            for label, payload in (
                    ("多一个键", dict(good, approved=True)),
                    ("少一个键", {k: v for k, v in good.items() if k != "change_reason"})):
                with self.subTest(label):
                    _reseal(out_dir, payload)
                    with self.assertRaisesRegex(S.SpecChangeBlocked, r"字段必须严格等于"):
                        S.validate(out_dir, spec_path)

    def test_unknown_schema_version_is_blocked(self):
        with self._confirmed() as (out_dir, spec_path):
            _reseal(out_dir, dict(S.validate(out_dir, spec_path), schema_version=99))
            with self.assertRaisesRegex(S.SpecChangeBlocked, r"schema 不认"):
                S.validate(out_dir, spec_path)

    # —— 判据 ②：spec_sha256 == 当场重算（**不读自报**）————————————————————
    def test_one_changed_byte_without_updating_the_receipt_is_blocked(self):
        """⭐ 本门的主用途：「跑不通就改 spec」当场停下。"""
        with self._confirmed() as (out_dir, spec_path):
            _write_spec_file(spec_path, dict(ST._spec(), dtype_required=["float32"]))
            with self.assertRaises(S.SpecChangeBlocked) as cm:
                S.validate(out_dir, spec_path)
            self.assertIn("--update", str(cm.exception))

    def test_a_self_consistent_receipt_claiming_another_spec_is_blocked(self):
        """⭐ 「不读自报」的落点：收据摘要完全自洽、只是认领了**另一份** spec → 照拒。

        ⚠ 这条与上一条不是同一件事：上一条改的是 spec，这条改的是**收据自报的值**，
        且重新封了摘要。若实现改成「信收据里那个数」，上一条会红、这条也会红——
        两条一起把「当场重算」这件事钉死。
        """
        with self._confirmed() as (out_dir, spec_path):
            other = _write_spec_file(
                os.path.join(os.path.dirname(spec_path), "other.spec.json"),
                dict(ST._spec(), tolerance=0.5))
            _reseal(out_dir, dict(S.validate(out_dir, spec_path),
                                  spec_sha256=S.spec_sha256(other)))
            with self.assertRaisesRegex(S.SpecChangeBlocked, r"当场重算"):
                S.validate(out_dir, spec_path)

    def test_previous_hash_must_be_null_or_a_different_sha(self):
        with self._confirmed() as (out_dir, spec_path):
            good = S.validate(out_dir, spec_path)
            for label, previous in (("不是 sha256", "nope"),
                                    ("与当前同值", good["spec_sha256"])):
                with self.subTest(label):
                    _reseal(out_dir, dict(good, previous_spec_sha256=previous))
                    with self.assertRaises(S.SpecChangeBlocked):
                        S.validate(out_dir, spec_path)

    def test_a_missing_spec_file_is_blocked_not_silently_skipped(self):
        with self._confirmed() as (out_dir, spec_path):
            os.remove(spec_path)
            with self.assertRaisesRegex(S.SpecChangeBlocked, r"无法重算摘要"):
                S.validate(out_dir, spec_path)

    # —— 判据 ③：confirmed_by ——————————————————————————————————————
    def test_placeholder_confirmers_are_blocked(self):
        with self._confirmed() as (out_dir, spec_path):
            good = S.validate(out_dir, spec_path)
            for bad in ("", "   ", "auto", "AUTO", " Orchestrator ", "n/a", "TBD",
                        "---", "自动填充", "占位", "claude", None, 42):
                with self.subTest(confirmed_by=bad):
                    _reseal(out_dir, dict(good, confirmed_by=bad))
                    with self.assertRaisesRegex(S.SpecChangeBlocked, r"confirmed_by"):
                        S.validate(out_dir, spec_path)

    def test_a_real_looking_name_is_not_over_blocked(self):
        """反向：黑名单是**整串相等**判定，别把正常名字误伤成占位符。"""
        with self._confirmed() as (out_dir, spec_path):
            good = S.validate(out_dir, spec_path)
            for ok in ("lys", "lllyys", "张三", "Autodesk 张三", "ai-team 李四"):
                with self.subTest(confirmed_by=ok):
                    _reseal(out_dir, dict(good, confirmed_by=ok))
                    self.assertEqual(S.validate(out_dir, spec_path)["confirmed_by"], ok)

    # —— 判据 ④：change_reason（**初稿声明了却没校**的那一条）——————————————
    def test_placeholder_reasons_are_blocked(self):
        """⭐ codex 审出初稿「声明了判据 ④ 却没写校验」。删掉那个 raise，本用例必红。"""
        with self._confirmed() as (out_dir, spec_path):
            good = S.validate(out_dir, spec_path)
            for bad in ("", "  \n ", "TODO", "n/a", "无", "占位符", "…", None, []):
                with self.subTest(change_reason=bad):
                    _reseal(out_dir, dict(good, change_reason=bad))
                    with self.assertRaisesRegex(S.SpecChangeBlocked, r"change_reason"):
                        S.validate(out_dir, spec_path)

    def test_a_real_reason_is_not_over_blocked(self):
        with self._confirmed() as (out_dir, spec_path):
            good = S.validate(out_dir, spec_path)
            for ok in ("任务书 §3 澄清后放宽容差", "补 complex64 覆盖", "no reason given, see issue #12"):
                with self.subTest(change_reason=ok):
                    _reseal(out_dir, dict(good, change_reason=ok))
                    self.assertEqual(S.validate(out_dir, spec_path)["change_reason"], ok)


class CliTest(unittest.TestCase):
    """CLI 退出码：0 通过 / 1 BLOCKED / 2 用法错。"""

    def test_exit_codes(self):
        with tempfile.TemporaryDirectory() as root:
            out_dir = os.path.join(root, "reports")
            spec_path = _write_spec_file(os.path.join(root, "the.spec.json"))
            base = ["--out", out_dir, "--spec", spec_path]
            self.assertEqual(S.main(base + ["--check"]), 1, "没收据 → BLOCKED")
            self.assertEqual(
                S.main(base + ["--init", "--reason", "首轮", "--by", "lys"]), 0)
            self.assertEqual(S.main(base + ["--check"]), 0)
            self.assertEqual(
                S.main(base + ["--init", "--reason", "再来", "--by", "lys"]), 2,
                "重复 --init → 用法错")
            _write_spec_file(spec_path, dict(ST._spec(), tolerance=0.9))
            self.assertEqual(S.main(base + ["--check"]), 1, "spec 改了 → BLOCKED")
            self.assertEqual(
                S.main(base + ["--update", "--reason", "放宽容差", "--by", "lys"]), 0)
            self.assertEqual(S.main(base + ["--check"]), 0)

    def test_init_without_reason_or_by_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as root:
            spec_path = _write_spec_file(os.path.join(root, "the.spec.json"))
            for extra in (["--init"], ["--init", "--reason", "x"], ["--init", "--by", "lys"]):
                with self.subTest(extra=extra):
                    with self.assertRaises(SystemExit) as cm:
                        S.main(["--out", os.path.join(root, "reports"),
                                "--spec", spec_path] + extra)
                    self.assertEqual(cm.exception.code, 2)


class HonestScopeTest(unittest.TestCase):
    """⭐ 这道门**证不到**什么，必须写在文档里且不许被悄悄删掉。

    收据无密钥、编排层自己填 `--by` 就能过——把它描述成「用户已确认」的证明就是一句假话，
    而假话在本仓与 fail-open 同价。所以拿一条最轻的文本哨兵钉住那节记账。
    """

    def test_module_doc_states_what_it_cannot_prove(self):
        doc = S.__doc__ or ""
        self.assertIn("证不到", doc)
        self.assertIn("人确认身份不可证", doc)
        for claim in ("确认人身份", "删档重来"):
            self.assertIn(claim, doc, f"诚实边界少了一条：{claim}")


class RunWorkflowSpecChangeGateTest(unittest.TestCase):
    """两道门在编排里的实际行为（真机侧全用夹具替身，不访问 NPU）。"""

    def _facts(self, root):
        return ST._write_source_facts(os.path.join(root, "fetch", "source_facts.json"))

    def test_acceptance_run_without_a_receipt_is_blocked_before_side_effects(self):
        """⭐ 入口门。断言打在 `BLOCKED(spec 变更未确认)` 上——只断言「抛了 SystemExit」是假门。"""
        with tempfile.TemporaryDirectory() as root, ST._env(root):
            spec_path = _write_spec_file(os.path.join(root, "the.spec.json"))
            out_dir = os.path.join(root, "reports", "widget")
            with mock.patch.object(W.gen_cases, "gen_cases",
                                   side_effect=AssertionError("不该走到 Task1")):
                with self.assertRaisesRegex(SystemExit, r"BLOCKED\(spec 变更未确认\)"):
                    W.run(spec_path, mode="cpp_extension", out_dir=out_dir,
                          source_facts=self._facts(root))
            self.assertFalse(os.path.exists(out_dir),
                             "入口门必须在 makedirs 之前拒，不留半个产物目录")

    def test_a_confirmed_spec_runs_through_and_writes_the_verdict(self):
        """⭐ 正路：门放行时整条链照跑（否则上面那条负路可由「恒 BLOCKED」的假门满足）。"""
        with tempfile.TemporaryDirectory() as root, ST._env(root):
            out_dir = os.path.join(root, "reports", "widget")
            spec_path = _write_spec_file(os.path.join(root, "the.spec.json"))
            S.init_receipt(out_dir, spec_path, "首轮：按任务书抽出的初始 spec", "lys")
            with ST.AcceptanceRunTest()._stubbed([]):
                result = W.run(spec_path, mode="cpp_extension", out_dir=out_dir,
                               source_facts=self._facts(root))
            self.assertTrue(result["is_acceptance"])
            for name in W._ACCEPTANCE_FILES:
                self.assertTrue(os.path.isfile(os.path.join(out_dir, name)), name)

    def test_the_gate_hashes_the_original_spec_not_the_staged_copy(self):
        """⭐ 「别校到自己 staging 出来的副本上」——那等于自己给自己作证。

        现场：`--out` 里预置一份**内容不同**的 `spec.json`（上一轮 staging 的残留形态），
        收据认领的是**那一份**的摘要。校原件 → 不符 → **入口门**当场 BLOCKED（本用例期望）。

        ⚠ 断言必须钉在「**没走到 Task1**」上，不能只断言 SystemExit：若实现改成校
        `<out>/spec.json`，入口门此刻恰好对得上，整轮会跑到出口门才因原件不符而 BLOCKED——
        同样是一句 `BLOCKED(spec 变更未确认)`，只断言错误串的话这个变异抓不到。
        """
        with tempfile.TemporaryDirectory() as root, ST._env(root):
            out_dir = os.path.join(root, "reports", "widget")
            os.makedirs(out_dir)
            spec_path = _write_spec_file(os.path.join(root, "the.spec.json"))
            staged_like = _write_spec_file(os.path.join(out_dir, "spec.json"),
                                           dict(ST._spec(), tolerance=0.5))
            self.assertNotEqual(S.spec_sha256(spec_path), S.spec_sha256(staged_like))
            S.init_receipt(out_dir, staged_like, "认领的是副本那一份", "lys")
            with mock.patch.object(W.gen_cases, "gen_cases",
                                   side_effect=AssertionError("不该走到 Task1")):
                with self.assertRaisesRegex(SystemExit, r"BLOCKED\(spec 变更未确认\)"):
                    W.run(spec_path, mode="cpp_extension", out_dir=out_dir,
                          source_facts=self._facts(root))
            self.assertFalse(os.path.isfile(os.path.join(out_dir, "acceptance.json")))
            # 入口门在 staging 之前拒 → 本轮不该往 `--out` 里落任何自证材料
            self.assertFalse(os.path.exists(os.path.join(out_dir, "golden.py")))

    def test_exit_gate_catches_a_spec_rewritten_after_the_entry_gate(self):
        """⭐ 出口门 · 情形一：入口过了之后 spec 被换掉（收据没跟着动）。"""
        with tempfile.TemporaryDirectory() as root, ST._env(root):
            out_dir = os.path.join(root, "reports", "widget")
            spec_path = _write_spec_file(os.path.join(root, "the.spec.json"))
            S.init_receipt(out_dir, spec_path, "首轮", "lys")

            def _shrink_then_gen(spec, work):
                _write_spec_file(spec_path, dict(ST._spec(), dtype_required=["float32"]))
                return ST.AcceptanceRunTest._caseset(spec["op"])

            with ST.AcceptanceRunTest()._stubbed([]), \
                    mock.patch.object(W.gen_cases, "gen_cases",
                                      side_effect=_shrink_then_gen):
                with self.assertRaisesRegex(SystemExit, r"BLOCKED\(spec 变更未确认\)"):
                    W.run(spec_path, mode="cpp_extension", out_dir=out_dir,
                          source_facts=self._facts(root))
            for name in W._ACCEPTANCE_FILES:
                self.assertFalse(os.path.exists(os.path.join(out_dir, name)), name)

    def test_exit_gate_catches_a_receipt_refreshed_mid_run(self):
        """⭐ 出口门 · 情形二：跑到一半**既改 spec 又顺手更新收据**。

        这一条正是 `expect=` 那个参数存在的理由：出口只比 sha 的话，改 spec + 改收据的
        组合恰好自洽、门会放行。删掉 `expect` 比对，本用例必红。
        """
        with tempfile.TemporaryDirectory() as root, ST._env(root):
            out_dir = os.path.join(root, "reports", "widget")
            spec_path = _write_spec_file(os.path.join(root, "the.spec.json"))
            S.init_receipt(out_dir, spec_path, "首轮", "lys")

            def _shrink_and_reconfirm(spec, work):
                _write_spec_file(spec_path, dict(ST._spec(), dtype_required=["float32"]))
                S.update_receipt(out_dir, spec_path, "跑不通，先砍 dtype", "lys")
                return ST.AcceptanceRunTest._caseset(spec["op"])

            with ST.AcceptanceRunTest()._stubbed([]), \
                    mock.patch.object(W.gen_cases, "gen_cases",
                                      side_effect=_shrink_and_reconfirm):
                with self.assertRaisesRegex(SystemExit, r"执行途中被改写"):
                    W.run(spec_path, mode="cpp_extension", out_dir=out_dir,
                          source_facts=self._facts(root))
            for name in W._ACCEPTANCE_FILES:
                self.assertFalse(os.path.exists(os.path.join(out_dir, name)), name)

    def test_the_gate_is_wired_at_both_verdict_and_acceptance_write_points(self):
        """⭐ 两处出口都要有：`acceptance.json` 才是人和 CI 直接读的那一份。

        `verdict.json` 那一处会**先**触发，所以「acceptance.json 侧漏了一处」在行为上测不出来
        （删掉它，上面两条出口用例照样绿）。故这里退而求其次做静态核，口径同
        `test_run_workflow_mode` 对「缺省真源只有一份定义」的做法。

        ⚠ 如实说明这条的强度：它证的是**调用点还在**，不是「调用点在正确的位置」。
        真正的行为见证由上面两条出口用例给（它们打在 verdict.json 那一处）。
        """
        src = inspect.getsource(W.run)
        self.assertEqual(src.count("_assert_spec_change_confirmed("), 3,   # 入口 1 + 出口 2
                         "spec 变更门的调用点不是 3 处（入口 1 + 出口 2）")
        self.assertEqual(src.count("expect=spec_change"), 2,
                         "带 expect= 的出口核对不是 2 处")
        # 出口门必须与准入出口门成对出现：两者都守着同一批写盘动作
        self.assertEqual(src.count("_assert_acceptance_form_allowed("), 2)

    def test_mock_path_is_exempt(self):
        """非验收通路不受此门约束——卡死开发逃生通路只会逼人绕更远的路。"""
        with tempfile.TemporaryDirectory() as root, ST._env(root):
            spec_path = _write_spec_file(os.path.join(root, "the.spec.json"))
            with mock.patch.object(W.gen_cases, "gen_cases",
                                   side_effect=ST._Sentinel("到 Task1 了")):
                with self.assertRaises(ST._Sentinel):
                    W.run(spec_path, mode="mock", out_dir=os.path.join(root, "reports"))

    def test_experimental_forms_are_exempt(self):
        for form, mode in ST.ExperimentalFormBypassTest._FORMS:
            with self.subTest(form=form), tempfile.TemporaryDirectory() as root, \
                    ST._env(root):
                out_dir = os.path.join(root, "reports", form)
                with ST.ExperimentalFormBypassTest()._stubbed(mode, []):
                    result = W.run(ST._write_spec(root, ST._spec(runner_form=form)),
                                   out_dir=out_dir, allow_experimental_form=True)
                self.assertFalse(result["is_acceptance"])
                self.assertEqual(result["summary_file"], W._DEV_SUMMARY_FILE)


class NonAcceptanceNoteTest(unittest.TestCase):
    """⭐ 步骤 2：非验收注脚**按真实原因取串**。

    病历：一句 mock 措辞套所有非验收产物 → `--allow-experimental-form` 下的**真机**跑被标成
    「NPU 输出 = golden.copy()、性能是编的假数」。那是一句凭空的假话，会让读报告的人
    以为这轮压根没上过真机，从而把失败归因整个带偏。
    """

    def test_note_is_selected_by_the_real_reason(self):
        self.assertEqual(W._non_acceptance_note("mock", False), W._NOTE_MOCK)
        self.assertEqual(W._non_acceptance_note("catlass_mock", False), W._NOTE_MOCK)
        # mock ∧ 非准入 form：mock 更强、必须赢——「数据是编的」不能被「form 不准入」盖过去
        self.assertEqual(W._non_acceptance_note("mock", True), W._NOTE_MOCK)
        self.assertEqual(W._non_acceptance_note("new_example", True), W._NOTE_FORM)
        self.assertEqual(W._non_acceptance_note("aclnn_py", True), W._NOTE_FORM)
        # 其余（catlass 真机 / adapter 自报 development / 没登记过的新 mode）→ 中性话
        for mode in ("catlass", "cpp_extension", "some_future_mode"):
            self.assertEqual(W._non_acceptance_note(mode, False), W._NOTE_OTHER, mode)

    def test_every_note_keeps_the_shared_marker(self):
        """三串都得带 `NON-ACCEPTANCE`：catlass_adapter 的落盘守卫按这个词判。"""
        for note in (W._NOTE_MOCK, W._NOTE_FORM, W._NOTE_OTHER):
            self.assertIn("NON-ACCEPTANCE", note)

    def test_only_the_mock_note_claims_the_data_is_fabricated(self):
        """⭐ 反向：真机那两串里**不许**出现 mock 的措辞。"""
        for note in (W._NOTE_FORM, W._NOTE_OTHER):
            for forbidden in ("mock", "golden.copy()", "假数", "假基线"):
                self.assertNotIn(forbidden, note, f"{forbidden!r} 出现在非 mock 注脚里")

    def test_stamp_dev_defaults_to_the_neutral_note(self):
        """漏传实参时的失败方向应是「少说一句」，不是「凭空断言数据是编的」。"""
        stamped = W._stamp_dev({"summary": {}}, False, "development")
        self.assertEqual(stamped["acceptance_note"], W._NOTE_OTHER)

    def test_experimental_form_artifacts_never_say_mock(self):
        """⭐ 步骤 2 的验收口径：`--allow-experimental-form` 跑一次 →
        产物里不含「mock」「golden.copy()」字样。"""
        for form, mode in ST.ExperimentalFormBypassTest._FORMS:
            with self.subTest(form=form), tempfile.TemporaryDirectory() as root, \
                    ST._env(root):
                out_dir = os.path.join(root, "reports", form)
                with ST.ExperimentalFormBypassTest()._stubbed(mode, []):
                    W.run(ST._write_spec(root, ST._spec(runner_form=form)),
                          out_dir=out_dir, allow_experimental_form=True)
                for name in W._DEV_FILES + ("perf_report.json",):
                    path = os.path.join(out_dir, name)
                    self.assertTrue(os.path.isfile(path), name)
                    with open(path, encoding="utf-8") as fh:
                        obj = json.load(fh)
                    self.assertEqual(obj.get("acceptance_note"), W._NOTE_FORM, name)
                    text = json.dumps(obj, ensure_ascii=False)
                    for forbidden in ("mock", "golden.copy()"):
                        self.assertNotIn(forbidden, text, f"{name} 里出现了 {forbidden!r}")


if __name__ == "__main__":
    unittest.main()
