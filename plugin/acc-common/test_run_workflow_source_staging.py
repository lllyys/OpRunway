#!/usr/bin/env python3
"""CP-E 自证材料 staging + 「每次都传 `--source-facts`」的**编排层**门。

被测的是同一个缺口的两面：**验收产物目录没有自带足够的自证材料**。

| 面 | 症状 | 本文件钉住的行为 |
|---|---|---|
| CP-F staging 缺口 | base 产物里没有 `spec.json`，golden 授权链锚 `dirname(spec)/golden.py` 也不存在 → 跑 CP-F 必须手工搬文件 | 验收通路把 `spec.json` / `golden.py` 落进 `--out`，`precision_retest_contract` 的 containment 与 golden 锚天然成立 |
| 三级门残留伪装面 | `source_facts` 自动发现落空 + 收据自称 `pull_request` → 无对照物可查 | 验收通路 `--source-facts` **必填**（缺席拒跑），并把它 staging 后**显式**喂给每一级门 |
| 产物层 fail-open | 上面两道门都在**清残留之前**早退 → 复用 `--out` 时旧 PASS 原样留任，被下游当成这次的结果 | `run()` 第一行先作废上一轮的结论产物（`StaleResultInvalidationTest`） |
| 旁路误伤 | 「非 mock 即强制 source facts」会把 `cpp`/`aclnn_py` 的开发逃生通路卡死 | `ExperimentalFormBypassTest` 逐 form 钉住：不要 source facts、不 staging、不写裁决 |

不访问 NPU：真机 adapter / gen_cases / validator 全部以夹具替身注入，被测对象只有
`run_workflow` 自己那几步。
"""

import contextlib
import inspect
import json
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

import content_address
import precision_retest_contract as R
import render_acceptance_markdown as MD
import repo_adapter
import run_workflow as W
import spec_change_gate as SCG
import validate_acceptance_state as G
from test_validate_cpp_extension_receipt import source_facts_payload

_HERE = os.path.dirname(os.path.abspath(__file__))
#: 只读 golden 样例根；夹具从这里拷一份进临时 ops root（同 test_run_workflow_mode 的口径：
#: **不 mock `op_dir` / `load_golden`**，否则「真实的 `<ops_root>/<op>/golden.py` 查找」整条没被跑过）。
_SAMPLE_GOLDEN = os.path.join(_HERE, "..", "samples", "golden", "Sign", "golden.py")
_OP = "Widget"          # 中立见证名；本文件不含任何按算子身份的分支


def _spec(op=_OP, runner_form="cpp_extension"):
    return {"op": op, "runner_form": runner_form,
            "params": [{"name": "x", "dtype": ["float32"]}]}


@contextlib.contextmanager
def _env(root, *, with_golden=True):
    """一块临时 ops root（点名时带一份真 golden.py），并把 `OPRUNWAY_OPS_DIR` 指过去。"""
    ops = os.path.join(root, "ops")
    if with_golden:
        os.makedirs(os.path.join(ops, _OP), exist_ok=True)
        shutil.copyfile(_SAMPLE_GOLDEN, os.path.join(ops, _OP, "golden.py"))
    else:
        os.makedirs(ops, exist_ok=True)
    with mock.patch.dict(os.environ, {"OPRUNWAY_OPS_DIR": ops}):
        yield ops


def _write_spec(root, spec=None):
    path = os.path.join(root, "the.spec.json")
    with open(path, "w", encoding="utf-8") as out:
        json.dump(spec if spec is not None else _spec(), out)
    return path


def _write_source_facts(path, **kw):
    """写一份**过完整契约**的 `source_facts.json` envelope（复用三级门单测的同一份 payload）。

    ⚠ 别手拼最小 JSON：`source_facts_lookup.find_source_facts` 会复算 digest 并跑
    `validate_preparation_state._validate_source_payload`，最小 payload 一律 `__BAD__`，
    那样用例就全落在「对照物不可信」那条分支上，测不到它想测的东西。
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as out:
        json.dump(content_address.make_artifact(
            "oprunway/source-facts/v1", source_facts_payload(**kw)), out)
    return path


def _confirm_spec(spec_path, out_dir):
    """满足 **spec 变更门**：给 `out_dir` 落一份对得上**当前** spec 的显式声明收据。

    ⚠ 这不是给门放水：收据里那串摘要是拿这份 spec 当场算出来的，四条判据一条没绕。
      本文件测的是 staging 与来源锚，变更门本身在 `test_spec_change_gate.py` 专测
      （含「不落收据就 BLOCKED」的反面见证）。
    """
    if not SCG.check(spec_path, out_dir):       # 已有一份对得上的收据 → 什么都不做
        return spec_path
    write = SCG.update_receipt if os.path.lexists(SCG.receipt_path(out_dir)) else SCG.init_receipt
    write(spec_path, out_dir, "夹具：本轮 spec 基线", "lys")
    return spec_path


class _Sentinel(RuntimeError):
    """夹具用：证明控制流**走到了**某一步（而不是被更早的门拦掉）。"""


class SourceFactsMandatoryTest(unittest.TestCase):
    """验收通路缺 `--source-facts` → 拒跑，且**在任何副作用之前**拒。"""

    def test_acceptance_run_without_source_facts_is_refused_before_side_effects(self):
        """⭐ task #20 的主门。

        ⚠ 断言必须打在 `--source-facts` 这几个字上：验收通路上少了它，后面还有一堆别的门
        （缺 golden、gen_cases 缺件……）也会抛 SystemExit。只断言「抛了 SystemExit」是**假门**
        ——把这道必填门整个删掉，测试照样绿。
        """
        with tempfile.TemporaryDirectory() as root, _env(root):
            spec_path = _write_spec(root)
            out_dir = os.path.join(root, "reports", "widget")
            with self.assertRaisesRegex(SystemExit, r"--source-facts"):
                W.run(spec_path, mode="cpp_extension", out_dir=out_dir)
            self.assertFalse(os.path.exists(out_dir),
                             "必填门必须在 makedirs 之前拒，不留半个产物目录")

    def test_refusal_happens_before_the_source_form_is_even_known(self):
        """⭐ 「缺 `--source-facts` 时 **PR 通路也 BLOCKED**」在编排层是怎么成立的。

        这道门**根本不看**取源形态——它在读任何 build receipt 之前就拒了，因此对
        `git_pr` 与 `local_source` 一视同仁。这比「按通路分别拦」更强：编排层不存在
        「先判成 PR、再放过」的那一步。
        （门那一侧的对偶见证在 `test_validate_cpp_extension_receipt.py`：显式路径指不到文件
        时 PR 通路同样阻断。两边合起来才封住整条路。）

        ⚠ 本条原名 `..._before_the_dut_source_is_even_known`，用的是已被合并裁定删除的
          `dut_source` 词表；断言一个字没动，只把措辞换到主干的 `declared_source_form`
          / `provenance_kind` 上。
        """
        with tempfile.TemporaryDirectory() as root, _env(root):
            spec_path = _write_spec(root)
            with mock.patch.object(W.gen_cases, "gen_cases",
                                   side_effect=_Sentinel("不该走到 Task1")):
                with self.assertRaisesRegex(SystemExit, r"--source-facts"):
                    W.run(spec_path, mode="cpp_extension",
                          out_dir=os.path.join(root, "reports"))

    def test_non_acceptance_path_does_not_require_it(self):
        """非验收通路（mock）不受影响——它物理上不产验收裁决，也没有来源锚要对账。

        用 sentinel 证明控制流**穿过**了必填门到达 Task1；若门误伤 mock，这里会拿到 SystemExit。
        """
        with tempfile.TemporaryDirectory() as root, _env(root):
            spec_path = _write_spec(root)
            with mock.patch.object(W.gen_cases, "gen_cases",
                                   side_effect=_Sentinel("到 Task1 了")):
                with self.assertRaises(_Sentinel):
                    W.run(spec_path, mode="mock",
                          out_dir=os.path.join(root, "reports"))

    def test_empty_string_is_not_a_way_to_opt_out(self):
        """空串（空环境变量展开出来的常见形态）不得被当成「给了」而滑过对照物校验。"""
        with tempfile.TemporaryDirectory() as root, _env(root):
            spec_path = _write_spec(root)
            with mock.patch.object(W.gen_cases, "gen_cases",
                                   side_effect=_Sentinel("不该走到 Task1")):
                with self.assertRaisesRegex(SystemExit, r"不是可信的 source_facts"):
                    W.run(spec_path, mode="cpp_extension",
                          out_dir=os.path.join(root, "reports"), source_facts="")


class StagingInputsTest(unittest.TestCase):
    """三份输入原件的读取/落盘口径。"""

    def test_staged_copies_are_byte_identical(self):
        with tempfile.TemporaryDirectory() as root, _env(root) as ops:
            spec_path = _write_spec(root)
            facts_path = _write_source_facts(os.path.join(root, "fetch", "source_facts.json"))
            out_dir = os.path.join(root, "reports")
            os.makedirs(out_dir)
            payloads = W._read_acceptance_inputs(spec_path, _spec(), facts_path)
            staged = W._write_staged_inputs(out_dir, payloads)
            self.assertEqual(staged, os.path.join(out_dir, "source_facts.json"))
            for name, origin in (
                    ("spec.json", spec_path),
                    ("golden.py", os.path.join(ops, _OP, "golden.py")),
                    ("source_facts.json", facts_path)):
                with open(os.path.join(out_dir, name), "rb") as got, \
                        open(origin, "rb") as want:
                    self.assertEqual(got.read(), want.read(), name)

    def test_blocked_source_facts_is_refused(self):
        """`completeness=blocked` 的取材事实是 fetch_source 亲手产的、digest 完全正确，
        但仓规写死它只供诊断。拿它当来源锚 = 「不完整证据被静默升级为可裁决」。"""
        with tempfile.TemporaryDirectory() as root, _env(root):
            facts = _write_source_facts(
                os.path.join(root, "source_facts.json"),
                completeness={"status": "blocked", "reasons": ["taskdoc_unreadable"]})
            with self.assertRaisesRegex(SystemExit, r"不是可信的 source_facts"):
                W._read_acceptance_inputs(_write_spec(root), _spec(), facts)

    def test_missing_golden_is_refused_with_an_actionable_message(self):
        with tempfile.TemporaryDirectory() as root, _env(root, with_golden=False):
            facts = _write_source_facts(os.path.join(root, "source_facts.json"))
            with self.assertRaisesRegex(SystemExit, r"golden\.py"):
                W._read_acceptance_inputs(_write_spec(root), _spec(), facts)

    def test_symlinked_golden_is_refused(self):
        """与 `gen_cases.load_golden` 同一条守卫（防换靶）；口径不许在两处分叉。"""
        with tempfile.TemporaryDirectory() as root, _env(root, with_golden=False) as ops:
            real = os.path.join(root, "elsewhere.py")
            shutil.copyfile(_SAMPLE_GOLDEN, real)
            os.makedirs(os.path.join(ops, _OP))
            os.symlink(real, os.path.join(ops, _OP, "golden.py"))
            facts = _write_source_facts(os.path.join(root, "source_facts.json"))
            with self.assertRaisesRegex(SystemExit, r"符号链接"):
                W._read_acceptance_inputs(_write_spec(root), _spec(), facts)

    def test_spec_rewritten_between_parse_and_staging_is_refused(self):
        """⭐ spec 也有 `golden.py` 那条 TOCTOU：`run()` 解析一次、staging 又按字节读一次。

        中间被换掉的话，报告目录里那份 `spec.json` 是 B、实际驱动本轮执行的是 A，
        而 CP-F 的 `base_artifacts.spec` 就锚在这份副本上。
        """
        with tempfile.TemporaryDirectory() as root, _env(root):
            facts = _write_source_facts(os.path.join(root, "source_facts.json"))
            spec_path = _write_spec(root)                 # 盘上是 A
            parsed = _spec()                              # `run()` 解析到的也是 A
            _write_spec(root, dict(_spec(), tolerance=0.9))   # 中途换成 B
            with self.assertRaisesRegex(SystemExit, r"解析与 staging 之间被改写"):
                W._read_acceptance_inputs(spec_path, parsed, facts)

    def test_write_rejects_an_incomplete_payload_set(self):
        """少落一项 = 「清了却没重落」，那份残缺的自证材料比没有更坏。"""
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(SystemExit, r"键集合"):
                W._write_staged_inputs(root, {"spec.json": b"{}"})

    def test_write_refuses_to_follow_a_symlink_out_of_the_report_dir(self):
        """⭐ 落点是软链 → 拒绝跟随，绝不把自证材料写出 `--out`。

        与下面 `AcceptanceRunTest` 里那条「悬空软链先被清掉」是**两道**独立防线：
        清残留是检查、这里是打开，中间存在换靶窗口，只有 `O_NOFOLLOW` 才真的关上。
        """
        with tempfile.TemporaryDirectory() as root:
            out_dir = os.path.join(root, "reports")
            os.makedirs(out_dir)
            outside = os.path.join(root, "outside.py")
            os.symlink(outside, os.path.join(out_dir, "golden.py"))
            payloads = {name: b"x" for name in W._STAGED_FILES}
            with self.assertRaisesRegex(SystemExit, r"软链"):
                W._write_staged_inputs(out_dir, payloads)
            self.assertFalse(os.path.exists(outside), "绝不能写到报告目录之外")


class AcceptanceRunTest(unittest.TestCase):
    """把 `run()` 整条跑一遍（真机侧全用夹具替身），观察 staging 与门的实参。"""

    @staticmethod
    def _caseset(op=_OP):
        return {"op": op, "cases": [{
            "id": "c0", "dims": ["功能"],
            "inputs": [{"name": "x", "path": "c0/x.npy",
                        "dtype": "float32", "shape": [1]}],
            "expected": {"golden_path": "c0/g.npy"},
        }]}

    @staticmethod
    def _evidence():
        return {"op": _OP, "evidence_grade": "acceptance_candidate",
                "runner_source": "generated_official_cpp_extension",
                "evidence": [{"case_id": "c0"}]}

    @staticmethod
    def _verdict():
        return {"overall": {"verdict": "fail", "counts": {"total": 1, "fail": 1},
                            "risk": [], "uncertain": []}}

    @contextlib.contextmanager
    def _stubbed(self, calls):
        """把真机侧全部换成夹具，并记录每一级门收到的 `source_facts_path`。"""
        def _gate(name):
            def _fn(d, errs, source_facts_path=None):
                calls.append((name, d, source_facts_path))
            return _fn
        with mock.patch.object(
                    W.gen_cases, "gen_cases",
                    # ⚠ 形参必须跟住 `run_workflow` 真实的调用点
                    # （`gen_cases(spec, work, taskdoc_caseset=...)`）：夹具签名对不上时
                    # 报的是 TypeError，长得像被测代码坏了，实际只是替身没跟上。
                    side_effect=lambda spec, work, taskdoc_caseset=None:
                        self._caseset(spec["op"])), \
                mock.patch.object(W.cpp_extension_adapter, "prepare",
                                  return_value=(None, None)), \
                mock.patch.dict(W.repo_adapter.MODES,
                                {"cpp_extension": lambda cs, wd: self._evidence()},
                                clear=False), \
                mock.patch.object(W.validator, "validate",
                                  side_effect=lambda *a, **k: self._verdict()), \
                mock.patch.object(W.repro_artifacts, "generate_cpp_extension",
                                  return_value={"case_count": 1}), \
                mock.patch.object(W.render_acceptance_markdown, "write_report",
                                  return_value="验收报告.md"), \
                mock.patch.dict(G._GATES,
                                {name: _gate(name) for name in G._GATES},
                                clear=False):
            yield

    def _run(self, root, *, source_facts, out_dir=None, calls=None):
        out_dir = out_dir or os.path.join(root, "reports", "widget")
        calls = calls if calls is not None else []
        spec_path = _confirm_spec(_write_spec(root), out_dir)
        with self._stubbed(calls):
            result = W.run(spec_path, mode="cpp_extension",
                           out_dir=out_dir, source_facts=source_facts)
        return out_dir, result, calls

    def test_every_gate_stage_gets_the_staged_path_explicitly(self):
        """⭐ task #20 的落点：门不再走自动发现，「找不到对照物」在验收链上不可达。

        ⚠ 断言的是**逐级都拿到那条确切路径**，不是「传了个非 None」——后者在把实参改回
        `source_facts_path=None`（即恢复自动发现）时不会红。
        """
        with tempfile.TemporaryDirectory() as root, _env(root):
            facts = _write_source_facts(os.path.join(root, "fetch", "source_facts.json"))
            out_dir, result, calls = self._run(root, source_facts=facts)
            staged = os.path.join(out_dir, "source_facts.json")
            self.assertEqual([name for name, _, _ in calls], ["task1", "task2"])
            for name, gate_dir, path in calls:
                self.assertEqual(path, staged, f"{name} 未拿到 staging 出来的对照物")
                self.assertEqual(gate_dir, out_dir)
            self.assertEqual(result["overall"], "FAIL(精度)")
            self.assertTrue(os.path.isfile(os.path.join(out_dir, "acceptance.json")))

    def test_run_stages_all_three_inputs(self):
        with tempfile.TemporaryDirectory() as root, _env(root) as ops:
            facts = _write_source_facts(os.path.join(root, "fetch", "source_facts.json"))
            out_dir, _, _ = self._run(root, source_facts=facts)
            for name in W._STAGED_FILES:
                self.assertTrue(os.path.isfile(os.path.join(out_dir, name)), name)
            with open(os.path.join(out_dir, "golden.py"), "rb") as got, \
                    open(os.path.join(ops, _OP, "golden.py"), "rb") as want:
                self.assertEqual(got.read(), want.read())

    def test_source_facts_may_point_at_the_previous_runs_staged_copy(self):
        """⭐ 复跑时最自然的写法：`--source-facts <out>/source_facts.json`。

        清残留与落副本是**同一批文件名**。若实现改成「边清边拷」，第二轮会先把对照物删掉、
        再报一句「指不到文件」——把一次正常复跑变成假 BLOCKED。所以三份原件必须**先读进内存**。
        """
        with tempfile.TemporaryDirectory() as root, _env(root):
            out_dir = os.path.join(root, "reports", "widget")
            first = _write_source_facts(os.path.join(root, "fetch", "source_facts.json"))
            self._run(root, source_facts=first, out_dir=out_dir)
            staged = os.path.join(out_dir, "source_facts.json")
            with open(staged, "rb") as fh:
                before = fh.read()
            self._run(root, source_facts=staged, out_dir=out_dir)   # 指向上一轮的副本
            with open(staged, "rb") as fh:
                self.assertEqual(fh.read(), before)

    def test_dangling_symlink_at_a_staged_name_is_cleared_not_written_through(self):
        """⭐ 悬空软链：`os.path.exists` 返回 False，用它清残留等于把这条链留着。

        留着的后果不是「多个文件」，是**本轮的自证材料被写到 `--out` 之外**（`open` 跟随软链）。
        """
        with tempfile.TemporaryDirectory() as root, _env(root):
            out_dir = os.path.join(root, "reports", "widget")
            # init 只接受无历史报告根；本用例要测的是 **init 之后、staging 之前**被塞进的软链。
            spec_path = _confirm_spec(_write_spec(root), out_dir)
            outside = os.path.join(root, "escaped.py")
            os.symlink(outside, os.path.join(out_dir, "golden.py"))   # 目标尚不存在
            facts = _write_source_facts(os.path.join(root, "fetch", "source_facts.json"))
            with self._stubbed([]):
                W.run(spec_path, mode="cpp_extension",
                      out_dir=out_dir, source_facts=facts)
            self.assertFalse(os.path.exists(outside), "绝不能写出报告目录")
            staged = os.path.join(out_dir, "golden.py")
            self.assertFalse(os.path.islink(staged))
            self.assertTrue(os.path.isfile(staged))

    def test_golden_swapped_between_staging_and_task1_is_fail_closed(self):
        """⭐ staging 读一次、`gen_cases` 从原路径又读一次；中间被换掉必须停。

        否则报告目录里那份 `golden.py` 是 A、真正算出 golden `.npy` 的是 B，
        而 CP-F 会拿 A 去填 `golden_source_sha256`——一份说不清来源的 provenance。
        """
        with tempfile.TemporaryDirectory() as root, _env(root) as ops:
            facts = _write_source_facts(os.path.join(root, "fetch", "source_facts.json"))
            out_dir = os.path.join(root, "reports", "widget")

            # 形参跟住真实调用点，理由同 `_stubbed`。
            def _swap_then_gen(spec, work, taskdoc_caseset=None):
                with open(os.path.join(ops, _OP, "golden.py"), "ab") as fh:
                    fh.write(b"\n# swapped mid-run\n")
                return self._caseset(spec["op"])

            calls = []
            spec_path = _confirm_spec(_write_spec(root), out_dir)
            with self._stubbed(calls), \
                    mock.patch.object(W.gen_cases, "gen_cases",
                                      side_effect=_swap_then_gen):
                with self.assertRaisesRegex(SystemExit, r"staging 与 Task1 之间被改写"):
                    W.run(spec_path, mode="cpp_extension",
                          out_dir=out_dir, source_facts=facts)
            self.assertEqual(calls, [], "已 fail-closed，不该再跑到三级门")
            self.assertFalse(os.path.isfile(os.path.join(out_dir, "acceptance.json")))

    def test_stale_staged_copies_are_cleared_on_a_non_acceptance_rerun(self):
        """上一轮验收 staging 的三份副本，不得在下一轮 mock 跑完后原样留下。

        CP-F 与事后复跑的三级门**就是按文件名**去读它们的 → 留着就等于「拿上一轮的
        spec/golden/来源事实，配这一轮的 caseset 与裁决」。
        """
        with tempfile.TemporaryDirectory() as root, _env(root):
            out_dir = os.path.join(root, "reports", "widget")
            facts = _write_source_facts(os.path.join(root, "fetch", "source_facts.json"))
            self._run(root, source_facts=facts, out_dir=out_dir)
            for name in W._STAGED_FILES:
                self.assertTrue(os.path.isfile(os.path.join(out_dir, name)))
            with mock.patch.object(W.gen_cases, "gen_cases",
                                   side_effect=_Sentinel("到 Task1 了")):
                with self.assertRaises(_Sentinel):
                    W.run(_write_spec(root), mode="mock", out_dir=out_dir)
            for name in W._STAGED_FILES:
                self.assertFalse(os.path.isfile(os.path.join(out_dir, name)), name)


class StaleResultInvalidationTest(unittest.TestCase):
    """⭐ **产物层 fail-open**：`run()` 早退时，`--out` 里上一轮的结论必须已经不可消费。

    病历（2026-08-06 审修门 High）：`--source-facts` 必填门与 staging 的可信性校验都在清残留
    **之前**早退。于是「`--out` 里有上一轮的 PASS → 换 spec / 换 DUT 重跑但漏传或传错
    `--source-facts`」时，新进程非零退出，**上一轮的 acceptance.json / 验收报告.md 原样躺着**——
    任何按文件名读结果的脚本、报告或人，都会把旧 PASS 当成这次的结论。
    """

    #: 上一轮跑完留下的**结论面**。⚠ 这里刻意手写而非「跑一遍再复用」：要模拟的正是
    #: 「目录里躺着一份漂亮的 PASS」，而夹具跑出来的是 FAIL(精度)，看不出误读的危害。
    _PREVIOUS_RESULTS = {
        "acceptance.json":
            '{"op": "Widget", "overall": "PASS", "state": "PASSED", "exit_code": 0}',
        "verdict.json": '{"overall": {"verdict": "pass", "counts": {"total": 8, "fail": 0}}}',
        "perf_report.json": '{"summary": {"status": "ok", "perf_cases": 8, "\\u8fbe\\u6807": 8}}',
        "dev_run_summary.json": '{"pipeline_result": "PASS", "is_acceptance": false}',
        "dev_precision_check.json": '{"overall": {"verdict": "pass"}}',
        "验收报告.md": "# Widget 算子验收报告\n\n总体结论：**PASS**\n",
        "精度失败明细.md": "（上一轮的）\n",
        "性能失败明细.md": "（上一轮的）\n",
        "perf_sim_widget.svg": "<svg/>",
    }
    #: 上一轮的**证据件**：本机制刻意**不清**它们（单独存在推不出任何裁决，且是早退现场的诊断材料）。
    _PREVIOUS_EVIDENCE = {
        "caseset.json": '{"op": "Widget", "cases": [{"id": "old"}]}',
        "evidence.json": '{"op": "Widget", "evidence": [{"case_id": "old"}]}',
    }

    def _seed_previous_run(self, out_dir):
        """把一个「上一轮跑完、结论 PASS」的报告目录摆出来（含上一轮 staging 的三份输入副本）。"""
        os.makedirs(out_dir, exist_ok=True)
        for name, text in {**self._PREVIOUS_RESULTS, **self._PREVIOUS_EVIDENCE}.items():
            with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
                fh.write(text)
        for name in W._STAGED_FILES:
            with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
                fh.write("（上一轮 staging 的输入副本）")

    def _refusals(self, root):
        """本轮已知的早退触发点。

        ⚠ 这张表**不是**「穷举证明」——恰恰相反，它证明的是**不需要穷举**：作废发生在 `run()`
        第一行，与早退落在哪一处无关。所以表里刻意混进两条与 `source_facts` 毫无关系的
        （准入门、spec 不是 object），钉住这一点；把作废挪回中段的话，它们同样会红。
        """
        blocked = _write_source_facts(
            os.path.join(root, "blocked", "source_facts.json"),
            completeness={"status": "blocked", "reasons": ["taskdoc_unreadable"]})
        return (
            ("漏传 --source-facts", _spec(), {"mode": "cpp_extension"}, r"--source-facts"),
            ("空串不是「给了」", _spec(),
             {"mode": "cpp_extension", "source_facts": ""}, r"不是可信的 source_facts"),
            ("路径指不到", _spec(),
             {"mode": "cpp_extension", "source_facts": os.path.join(root, "nope.json")},
             r"不是可信的 source_facts"),
            ("取材事实 completeness=blocked", _spec(),
             {"mode": "cpp_extension", "source_facts": blocked}, r"不是可信的 source_facts"),
            ("准入门（与 source_facts 无关）", _spec(runner_form="cpp"),
             {"source_facts": blocked}, r"已停止准入"),
            ("spec 不是 JSON object（更早，连 mode 都还没派生）", [],
             {"mode": "cpp_extension"}, r"JSON object"),
        )

    def test_previous_pass_is_invalidated_on_every_early_refusal(self):
        """⭐ 主门：早退 ≠ 旧裁决留任。

        ⚠ 断言分两层，缺一不可：
          ① 确实是**为了那个原因**被拒（regex）——否则「随便抛个 SystemExit 就算数」是假门；
          ② 上一轮的结论产物**一件不剩**。
        """
        with tempfile.TemporaryDirectory() as shared, _env(shared):
            for label, spec, kwargs, pattern in self._refusals(shared):
                with self.subTest(label):
                    out_dir = os.path.join(shared, "reports", re.sub(r"\W+", "_", label))
                    self._seed_previous_run(out_dir)
                    spec_path = _write_spec(shared, spec)
                    with self.assertRaisesRegex(SystemExit, pattern):
                        W.run(spec_path, out_dir=out_dir, **kwargs)
                    for name in self._PREVIOUS_RESULTS:
                        self.assertFalse(
                            os.path.lexists(os.path.join(out_dir, name)),
                            f"{label}：上一轮的 {name} 还在——下游会把旧 PASS 当成这次的结果")

    def test_the_invalidated_dir_is_actually_unconsumable_by_the_real_gate(self):
        """⭐ 「文件没了」只是手段，要证的是**目录不再产得出结论**：真三级门（不打桩）复核。

        证据件（caseset/evidence）刻意留着，所以这条卡住的确实是**裁决缺失**、
        而不是「目录被清空了所以什么都跑不动」。
        """
        with tempfile.TemporaryDirectory() as root, _env(root):
            out_dir = os.path.join(root, "reports", "widget")
            self._seed_previous_run(out_dir)
            with self.assertRaisesRegex(SystemExit, r"--source-facts"):
                W.run(_write_spec(root), mode="cpp_extension", out_dir=out_dir)
            errs = []
            G._GATES["task2"](out_dir, errs)
            self.assertTrue(errs, "旧裁决被作废后，task2 门必须拒绝出结论")
            self.assertTrue(any("verdict.json" in e for e in errs), errs)

    def test_evidence_and_staged_inputs_survive_the_invalidation(self):
        """作废的**边界**：只作废结论面。

        · 证据件留着 —— 早退现场还得能诊断，且它们单独存在推不出任何裁决；
        · 上一轮 staging 的三份**输入**副本留着 —— `--source-facts <out>/source_facts.json`
          是复跑时最自然的写法，提前清掉会把一次正常复跑变成「指不到文件」的假 BLOCKED。
        """
        with tempfile.TemporaryDirectory() as root, _env(root):
            out_dir = os.path.join(root, "reports", "widget")
            self._seed_previous_run(out_dir)
            with self.assertRaisesRegex(SystemExit, r"--source-facts"):
                W.run(_write_spec(root), mode="cpp_extension", out_dir=out_dir)
            for name in tuple(self._PREVIOUS_EVIDENCE) + W._STAGED_FILES:
                self.assertTrue(os.path.isfile(os.path.join(out_dir, name)), name)

    def test_a_fresh_out_dir_is_untouched_and_still_refused_before_makedirs(self):
        """作废只删不建：`--out` 不存在时它是纯 no-op，「拒跑不留半个产物目录」一个字没松。"""
        with tempfile.TemporaryDirectory() as root, _env(root):
            out_dir = os.path.join(root, "reports", "widget")
            with self.assertRaisesRegex(SystemExit, r"--source-facts"):
                W.run(_write_spec(root), mode="cpp_extension", out_dir=out_dir)
            self.assertFalse(os.path.exists(out_dir))

    def test_a_dangling_symlink_named_like_a_verdict_is_removed_not_followed(self):
        """悬空软链占着裁决的名字：`os.path.exists` 看不见它，`open` 却会跟着它写出报告目录。"""
        with tempfile.TemporaryDirectory() as root, _env(root):
            out_dir = os.path.join(root, "reports", "widget")
            os.makedirs(out_dir)
            outside = os.path.join(root, "escaped.json")
            os.symlink(outside, os.path.join(out_dir, "acceptance.json"))
            with self.assertRaisesRegex(SystemExit, r"--source-facts"):
                W.run(_write_spec(root), mode="cpp_extension", out_dir=out_dir)
            self.assertFalse(os.path.lexists(os.path.join(out_dir, "acceptance.json")))
            self.assertFalse(os.path.exists(outside), "只删链接本身，不碰目标")

    def test_unremovable_stale_verdict_fails_closed(self):
        """清不掉就不开跑。

        ⚠ 这道门**能**和**不能**保证什么，说清楚（2026-08-06 codex 审修门自核）：
        · 能保证：本轮绝不会在一个「旧裁决还在」的目录上继续跑 —— 那次复跑对该目录**零影响**，
          与「这次复跑压根没发生」不可分辨，因此不存在「新旧产物混在一起」这种更坏的现场；
        · **不能**保证：把删不掉的旧 PASS 变没（父目录只读时物理上就删不掉）。要覆盖这一格，
          得上「run-id / 完成收据 + 所有消费者核对收据」那套协议，是跨模块契约变更，不在本批。

        ⚠ 用 `os.remove` 抛错来造这个场景，而不是拿目录占名：后者那个 `acceptance.json`
        本来就不是一份可解析的裁决，证不到「**有效**的旧 PASS 仍在原地」这句要害。
        """
        with tempfile.TemporaryDirectory() as root, _env(root):
            out_dir = os.path.join(root, "reports", "widget")
            self._seed_previous_run(out_dir)          # 一份可解析、合法的旧 PASS
            with mock.patch.object(os, "remove", side_effect=PermissionError("只读目录")):
                with self.assertRaisesRegex(SystemExit, r"清不掉上一轮的结论产物"):
                    W.run(_write_spec(root), mode="cpp_extension", out_dir=out_dir)
            with open(os.path.join(out_dir, "acceptance.json"), encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["overall"], "PASS")   # 旧的还在（如实记账）
            self.assertFalse(os.path.exists(os.path.join(out_dir, "work")),
                             "本轮对该目录必须零影响")

    def test_a_directory_squatting_a_verdict_name_also_fails_closed(self):
        """同一道门的真实成因版：不打桩，靠「目录占着裁决的名字」让 `os.remove` 真失败。"""
        with tempfile.TemporaryDirectory() as root, _env(root):
            out_dir = os.path.join(root, "reports", "widget")
            os.makedirs(os.path.join(out_dir, "acceptance.json"))
            with self.assertRaisesRegex(SystemExit, r"清不掉上一轮的结论产物"):
                W.run(_write_spec(root), mode="cpp_extension", out_dir=out_dir)

    def test_result_file_list_covers_the_markdown_the_renderer_actually_writes(self):
        """⭐ 漂移哨：`_REPORT_MD_FILES` 是照抄 `render_acceptance_markdown` 的字面量。

        那边改名 / 多产一份人读报告而这里没同步 → 旧报告会在早退后留任。反向也钉住
        （列了渲染器根本不产的名字 = 这份清单在自欺）。

        ⚠ 这是**启发式**哨兵，不是证明（2026-08-06 codex 审修门自核）：它只看得见
        `report_root, "x.md"` 这种落点形态。渲染器若改用常量/辅助函数另产一份报告，
        它抓不到。所以另加一条**非空**断言——正则一个都匹配不上时（大改写），哨兵自己先红，
        而不是悄悄变成一句空话。**真正的根治**是让渲染器导出唯一的报告清单常量、生产侧直接
        引用，那要改 `render_acceptance_markdown.py`，不在本批的文件范围内。
        """
        src = inspect.getsource(MD)
        # 同时吃 `os.path.join(report_root, "x.md")` 与 `Path(report_root, "x.md")`。
        joined = set(re.findall(r'report_root\s*,\s*"([^"]+\.md)"', src))
        self.assertTrue(joined, "漂移哨已失效：渲染器里一个 .md 落点都没匹配到")
        default_name = inspect.signature(MD.write_report).parameters["filename"].default
        self.assertEqual(set(W._REPORT_MD_FILES), joined | {default_name})
        self.assertTrue(set(W._REPORT_MD_FILES) <= set(W._RESULT_FILES))


class NonAdmittedFormBypassTest(unittest.TestCase):
    """⭐ 非验收旁路**不只看 mode**：`is_acceptance` 是「真机 mode **且** 准入 form」的合取。

    若哪天有人把它简化成「非 mock 即强制 source_facts」，正式验收与 mock 两边的测试都还会绿，
    而非准入 form 那一格会在跑起来之前就被错误卡死——本类就是钉那一格。

    ⚠ **夹具 2026-08-06 换过一次**（原类名 `ExperimentalFormBypassTest`）。原来走的是
    `cpp` / `aclnn_py` + `--allow-experimental-form`；通路收敛后那两种 form 已无真机入口、
    逃生阀也删了，所以这里改用 `mock.patch.dict` 把**派生表**临时接回一条来走完整条 run()。
    **准入集 `_ACCEPTANCE_RUNNER_FORMS` 一个字没动**——正因如此，本类断言的
    「不产 acceptance.json / 不 staging」才仍然是生产行为，而不是夹具造出来的假象。
    """

    #: (spec.runner_form, 该 form 历史上派生出的 mode)。⚠ 两条都必须留着：只测一条时，另一条被卡死不会红。
    _FORMS = (("cpp", "new_example"), ("aclnn_py", "aclnn_py"))

    @staticmethod
    def _evidence(mode):
        # runner_source 按 `_runner_source_allowed` 的受控对应给，免得跑出 BLOCKED 掩盖真正要看的东西。
        # ⚠ evidence_grade 刻意报 **acceptance_candidate**（真 adapter 就是这么报的）：
        #   即便证据自称验收级，非准入 form 也**物理上不产** acceptance.json——这才是要钉的。
        return {"op": _OP, "evidence_grade": "acceptance_candidate",
                "runner_source": "user", "mode": mode,
                "evidence": [{"case_id": "c0"}]}

    @contextlib.contextmanager
    def _stubbed(self, mode, calls, form=None):
        def _gate(name):
            def _fn(d, errs, source_facts_path=None):
                calls.append((name, d, source_facts_path))
            return _fn
        # 只把**派生表**临时接回一条（见类 docstring）；准入集不动，出口门与
        # `is_acceptance` 的判定照旧按生产口径走。
        derivation = {form: mode} if form is not None else {}
        with mock.patch.object(
                    W.gen_cases, "gen_cases",
                    # 形参跟住真实调用点，理由同 AcceptanceRunTest._stubbed。
                    side_effect=lambda spec, work, taskdoc_caseset=None:
                        AcceptanceRunTest._caseset(spec["op"])), \
                mock.patch.dict(W._RUNNER_FORM_TO_MODE, derivation, clear=False), \
                mock.patch.dict(W.repo_adapter.MODES,
                                {mode: lambda cs, wd: self._evidence(mode)}, clear=False), \
                mock.patch.object(W.repo_adapter, "_ne_cfg", return_value={}), \
                mock.patch.object(
                    W.verify_aclnn_harness, "validate_receipt",
                    return_value={"status": "OK",
                                  "coverage": {"selected_count": 1, "full_case_count": 1},
                                  "bindings": {"golden_source": {"sha256": "a" * 64}}}), \
                mock.patch.object(W.validator, "validate",
                                  side_effect=lambda *a, **k: AcceptanceRunTest._verdict()), \
                mock.patch.dict(G._GATES, {name: _gate(name) for name in G._GATES},
                                clear=False):
            yield

    def test_non_admitted_forms_run_without_source_facts_and_produce_no_verdict(self):
        for form, mode in self._FORMS:
            with self.subTest(form=form), tempfile.TemporaryDirectory() as root, _env(root):
                out_dir = os.path.join(root, "reports", form)
                calls = []
                with self._stubbed(mode, calls, form=form):
                    result = W.run(_write_spec(root, _spec(runner_form=form)),
                                   out_dir=out_dir)
                # ① 不要求 source_facts：跑到底了（被必填门卡死的话这里是 SystemExit）。
                self.assertFalse(result["is_acceptance"])
                self.assertEqual(result["summary_file"], W._DEV_SUMMARY_FILE)
                # ② 不产 staging（那是验收目录的自证材料，dev 目录里放一份只会长得像验收输入）。
                for name in W._STAGED_FILES:
                    self.assertFalse(os.path.exists(os.path.join(out_dir, name)), name)
                # ③ 不写验收裁决——即便上面 evidence 自称 acceptance_candidate。
                for name in W._ACCEPTANCE_FILES:
                    self.assertFalse(os.path.exists(os.path.join(out_dir, name)), name)
                for name in W._DEV_FILES:
                    self.assertTrue(os.path.isfile(os.path.join(out_dir, name)), name)
                # ④ 只跑管路自检那一级，且**不**编造 staged 对照物路径。
                self.assertEqual([name for name, _, _ in calls], ["task1"])
                self.assertEqual([p for _, _, p in calls], [None])

    def test_real_machine_dev_artifacts_are_never_labelled_as_mock(self):
        """⭐ 真机跑不许被标成「mock evidence」。

        病历（2026-08-06，aclnnRoll 试跑）：一句 mock 措辞套所有非验收产物，于是当时那条
        非准入 form 通路上的一轮**真机**跑的产物上写着「NPU 输出 = golden.copy()、
        性能是编的假数」——一句凭空的假话，读报告的人会以为压根没上过真机。
        措辞选串的单测在 `test_run_workflow_mode.NonAcceptanceNoteTest`，这里钉的是**落盘产物**。

        ⚠ 断言范围刻意只到 `dev_run_summary.json` / `dev_precision_check.json`，不含
          `perf_report.json`。两个理由，都与本门无关：
          ① 本夹具精度判 fail → Task3 被 fail-fast 跳过，那份 perf 报告这一轮压根没走 perf_compare；
          ② 精度通过的场景下，无 `_real_baseline.json` 的夹具里基线**确实**是
             `perf_compare.mock_baseline`，那句 mock 措辞是**实话**（真机上 `run_on_npu.sh`
             会落真基线）。要求产物对一件真事闭嘴，同样是失真。
        """
        for form, mode in self._FORMS:
            with self.subTest(form=form), tempfile.TemporaryDirectory() as root, _env(root):
                out_dir = os.path.join(root, "reports", form)
                with self._stubbed(mode, [], form=form):
                    W.run(_write_spec(root, _spec(runner_form=form)), out_dir=out_dir)
                for name in W._DEV_FILES:
                    with open(os.path.join(out_dir, name), encoding="utf-8") as fh:
                        text = fh.read()
                    self.assertIn(W._NOTE_FORM, text, name)
                    for word in ("mock", "golden.copy()", "假数"):
                        self.assertNotIn(word, text.casefold(), f"{name} 把真机跑说成了假数")

    def test_they_are_refused_outright_by_the_production_derivation_table(self):
        """⭐ 反面见证：上面两条靠的是**夹具接回派生表**，生产路径上这两种 form 压根跑不起来。

        没有这一条，上面两个用例就可能被读成「非准入 form 现在还能跑」——那正好是
        2026-08-06 通路收敛要消灭的读法。
        """
        for form, mode in self._FORMS:
            with self.subTest(form=form), tempfile.TemporaryDirectory() as root, _env(root):
                # 注意：**不传 form=**，即不接回派生表 —— 走的就是生产口径。
                with self._stubbed(mode, []):
                    with self.assertRaisesRegex(SystemExit, r"已停止准入"):
                        W.run(_write_spec(root, _spec(runner_form=form)),
                              out_dir=os.path.join(root, "reports", form))


class CpFClosureTest(unittest.TestCase):
    """⭐ task #16：staging 之后，CP-F 对 base 产物的两条硬要求天然成立。"""

    def test_staged_report_dir_satisfies_containment_and_golden_anchor(self):
        """`base_artifacts` 五项全落报告目录内，且 golden 锚 `dirname(spec)/golden.py` 就在那。

        ⚠ 这里**不放宽** `verify_base_artifacts` 的 containment（那是安全边界）——
        证的是「CP-E 落了副本之后，原本要手工 staging 的两件事自动满足了」。
        """
        with tempfile.TemporaryDirectory() as root, _env(root):
            facts = _write_source_facts(os.path.join(root, "fetch", "source_facts.json"))
            calls = []
            out_dir = os.path.join(root, "reports", "widget")
            spec_path = _confirm_spec(_write_spec(root), out_dir)
            with AcceptanceRunTest()._stubbed(calls):
                W.run(spec_path, mode="cpp_extension",
                      out_dir=out_dir, source_facts=facts)
            directive = _cp_f_directive(out_dir)
            verified = R.verify_base_artifacts(directive, out_dir)
            self.assertEqual(set(verified), set(R.BASE_ARTIFACTS))
            golden_anchor = os.path.join(
                os.path.dirname(verified["spec"]["path"]), "golden.py")
            self.assertTrue(os.path.isfile(golden_anchor),
                            "golden 授权链锚必须落在 base spec 同目录")
            self.assertEqual(len(R.sha256_file(golden_anchor)), 64)

    def test_spec_original_outside_the_report_dir_would_still_be_rejected(self):
        """反面见证：containment 没被放宽——填**原件**路径（报告目录之外）照旧 BLOCKED。

        没有这条，上一个用例就证明不了「是 staging 起的作用」，而不是「门本来就不管」。
        """
        with tempfile.TemporaryDirectory() as root, _env(root):
            facts = _write_source_facts(os.path.join(root, "fetch", "source_facts.json"))
            out_dir = os.path.join(root, "reports", "widget")
            spec_path = _confirm_spec(_write_spec(root), out_dir)
            with AcceptanceRunTest()._stubbed([]):
                W.run(spec_path, mode="cpp_extension",
                      out_dir=out_dir, source_facts=facts)
            directive = _cp_f_directive(out_dir)
            original = os.path.join(root, "the.spec.json")
            directive["base_artifacts"]["spec"] = {
                "path": original, "sha256": R.sha256_file(original)}
            with self.assertRaisesRegex(R.RetestContractError, "逃逸"):
                R.verify_base_artifacts(directive, out_dir)


def _cp_f_directive(out_dir):
    """一份**结构合法**的 confirmed directive，`base_artifacts` 指向报告目录里的五件产物。"""
    return {
        "schema_version": R.SCHEMA_VERSION,
        "directive_id": "human-staging-001",
        "directive_status": "confirmed",
        "attempt_kind": "same_policy_rerun",
        "case_ids": ["c0"],
        "base_artifacts": {
            name: {"path": os.path.join(out_dir, f"{name}.json"),
                   "sha256": R.sha256_file(os.path.join(out_dir, f"{name}.json"))}
            for name in R.BASE_ARTIFACTS
        },
        "source_identity": {
            "repo": "o/r", "pr_head_sha": "d" * 40,
            "build_receipt_sha256": "b" * 64, "runner_form": "cpp_extension",
        },
        "human_instruction": "复测失败 case",
        "confirmed_by": "lys",
        "confirmed_at": "2026-08-05T12:00:00Z",
        "precision_override": None,
    }


if __name__ == "__main__":
    unittest.main()
