"""`finalize_clean_acceptance` 的单测。

⚠ **本文件长期只测纯函数、没覆盖 `finalize_directory()`——那正是两条 Critical 能藏那么久的原因。**
所以下半部分是**真实目录级**的反例（`FinalizeDirectoryTest`）：
① 上一轮的 PASS 在本轮被拒时必须已经不存在；② 不给 spec 收据 / 不给 source facts 一律拒绝写裁决。
纯函数测得再细，也证明不了这条旁路在**目录**上不产假 PASS。
"""

import contextlib
import copy
import json
import os
import tempfile
import unittest
from unittest import mock

import pytest

import content_address
import finalize_clean_acceptance as F
import run_workflow as W
import spec_change_gate as SCG
from test_validate_cpp_extension_receipt import source_facts_payload


def _docs():
    """一份**当前唯一能产验收裁决的形态**（`cpp_extension`）的干净夹具。

    ⚠ 2026-08-06 从 `aclnn_py` 换过来：通路收敛后 `aclnn_py` / `cpp` 已停止准入，
    `run_workflow._RUNNER_FORM_TO_MODE` 里没有它们的条目，finalize 会（正确地）拒绝为它们
    生成 acceptance.json。拿退役形态当「干净 PASS」的基准夹具，测的就不再是本模块的正题。
    退役形态被拒本身另有专测：`test_build_clean_acceptance_refuses_retired_forms`。
    """
    spec = {"op": "Median", "runner_form": "cpp_extension"}
    evidence = {
        "evidence_grade": "acceptance_candidate",
        "runner_source": "generated_official_cpp_extension",
        "runner_form": "cpp_extension",
        "repo_mode": "cpp_extension",
    }
    verdict = {
        "overall": {
            "verdict": "pass",
            "counts": {
                "fail": 0, "uncertain": 0, "risk": 0, "gaps": 0,
                "golden_blocked": 0, "contract_problems": 0,
            },
            "risk": [],
            "uncertain": [],
        },
        "catlass_compare_na": ["c0"],
    }
    perf = {
        "summary": {
            "status": "ok", "blocked": 0, "perf_cases": 2, "达标": 2,
            "cases_scored": 2, "non_passing": 0,
        }
    }
    return spec, evidence, verdict, perf


def test_build_clean_acceptance():
    acc = F.build_clean_acceptance(*_docs(), {})
    assert acc["overall"] == "PASS"
    assert acc["state"] == "PASSED"
    assert acc["exit_code"] == 0
    assert acc["gate"] == {"passed": True, "errors": {}}


def test_build_clean_acceptance_allows_receipt_gated_cpp_extension_source():
    spec, evidence, verdict, perf = copy.deepcopy(_docs())
    acc = F.build_clean_acceptance(spec, evidence, verdict, perf, {})
    assert acc["overall"] == "PASS"
    assert acc["repo_mode"] == "cpp_extension"


@pytest.mark.parametrize("retired", ["cpp", "aclnn_py"])
def test_build_clean_acceptance_refuses_retired_forms(retired):
    """⭐ 停止准入的形态**不得**由本入口补出一份 acceptance.json。

    本入口是「跳过 run_workflow 状态机、直接拼裁决」的近路，所以准入这件事必须在这里也守住；
    否则通路收敛只挡住了主链，近路照旧能产出一份长得一模一样的裁决。
    ⚠ 拒绝理由要说清是「这条通路不产裁决」，不能报成 runner_source 不匹配——
      后者会把人引去改 runner_source，而真正该做的是迁到 cpp_extension。
    """
    spec, evidence, verdict, perf = copy.deepcopy(_docs())
    spec["runner_form"] = retired
    evidence.update(runner_form=retired, runner_source="user", repo_mode=retired)
    with pytest.raises(F.FinalizeError) as ex:
        F.build_clean_acceptance(spec, evidence, verdict, perf, {})
    assert "不产验收裁决" in str(ex.value)
    assert "cpp_extension" in str(ex.value)


@pytest.mark.parametrize("mutator", [
    lambda s, e, v, p: e.update(runner_source="builtin"),
    lambda s, e, v, p: v["overall"].update(verdict="needs_review"),
    lambda s, e, v, p: p["summary"].update(达标=1),
])
def test_build_clean_acceptance_refuses_non_clean(mutator):
    docs = list(copy.deepcopy(_docs()))
    mutator(*docs)
    with pytest.raises(F.FinalizeError):
        F.build_clean_acceptance(*docs, {})


def test_build_clean_acceptance_refuses_gate_error():
    with pytest.raises(F.FinalizeError):
        F.build_clean_acceptance(*_docs(), {"task3": ["missing"]})


# ── 目录级（`finalize_directory`）——两条 Critical 的正面战场 ──────────────────────────
_OP = "Widget"          # 中立见证名；本文件不含任何按算子身份的分支
_REASON = "夹具：本轮 spec 基线"
_BY = "lys"

#: 「上一轮跑完、结论 PASS」的现场。⚠ 刻意手写一份**漂亮的 PASS**：真跑出来的是 FAIL，
#: 看不出「旧裁决被当成本轮结果」的危害。
_PREVIOUS_VERDICTS = {
    "acceptance.json":
        '{"op": "Widget", "overall": "PASS", "state": "PASSED", "exit_code": 0}',
    "dev_run_summary.json": '{"pipeline_result": "PASS", "is_acceptance": false}',
    "dev_precision_check.json": '{"overall": {"verdict": "pass"}}',
    "验收报告.md": "# Widget 算子验收报告\n\n总体结论：**PASS**\n",
    "精度失败明细.md": "（上一轮的）\n",
    "性能失败明细.md": "（上一轮的）\n",
}


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _write_json(path, payload):
    return _write(path, json.dumps(payload, ensure_ascii=False))


class _Bed:
    """一套「封死后的编排链刚跑完」的报告目录 + 它的两份输入原件。

    刻意**不**打桩 spec 变更门与 source facts 可信性判据：收据是拿这份 spec 当场算出来的、
    envelope 是过完整契约的那一份，四条判据一条没绕。打桩的只有三级门（它要一整套真实
    caseset/evidence 才跑得动，与本文件要证的东西无关）。
    """

    def __init__(self, root):
        spec, evidence, verdict, perf = copy.deepcopy(_docs())
        spec["op"] = _OP
        self.root = root
        self.out = os.path.join(root, "reports", "widget")
        self.spec_path = _write_json(os.path.join(root, "orig", "the.spec.json"), spec)
        with open(self.spec_path, "rb") as fh:
            spec_bytes = fh.read()
        os.makedirs(self.out, exist_ok=True)
        # CP-E staging 副本：与原件**逐字节**相同（run_workflow 就是这么落的）。
        with open(os.path.join(self.out, W._STAGED_SPEC_FILE), "wb") as fh:
            fh.write(spec_bytes)
        _write_json(os.path.join(self.out, "evidence.json"), evidence)
        _write_json(os.path.join(self.out, "verdict.json"), verdict)
        _write_json(os.path.join(self.out, "perf_report.json"), perf)
        self.source_facts = _write_json(
            os.path.join(root, "fetch", "source_facts.json"),
            content_address.make_artifact(
                "oprunway/source-facts/v1", source_facts_payload()))
        SCG.init_receipt(self.spec_path, self.out, _REASON, _BY)

    def seed_previous_pass(self):
        for name, text in _PREVIOUS_VERDICTS.items():
            _write(os.path.join(self.out, name), text)

    def path(self, name):
        return os.path.join(self.out, name)

    def run(self):
        return F.finalize_directory(self.out, self.spec_path, self.source_facts)


@contextlib.contextmanager
def _stub_gates(errors=None):
    """替三级门装一个记录器。返回 `[(stage, dir, source_facts_path), …]`。"""
    seen = []

    def _make(stage):
        def _fn(d, errs, source_facts_path=None):
            seen.append((stage, d, source_facts_path))
            errs.extend((errors or {}).get(stage, []))
        return _fn

    with mock.patch.dict(F.gate._GATES,
                         {s: _make(s) for s in ("task1", "task2", "task3")}):
        yield seen


class FinalizeDirectoryTest(unittest.TestCase):

    @contextlib.contextmanager
    def _bed(self):
        with tempfile.TemporaryDirectory() as root:
            yield _Bed(root)

    # —— 正面：收紧之后这条旁路仍然能对**合法**目录出裁决 ——————————————————————
    def test_clean_hardened_directory_still_finalizes(self):
        with self._bed() as bed, _stub_gates() as seen:
            acc = bed.run()
            self.assertEqual(acc["overall"], "PASS")
            self.assertEqual(acc["op"], _OP)
            with open(bed.path("acceptance.json"), encoding="utf-8") as fh:
                self.assertEqual(json.load(fh), acc)
            self.assertEqual([s for s, _, _ in seen], ["task1", "task2", "task3"])

    def test_every_gate_stage_gets_the_source_facts_path_explicitly(self):
        """⭐ Critical ② 的一半：门**看着调了**不等于门核了东西。

        PR 通路的收据在「找不到 source facts」那条分支是直接返回、不报错的；不显式指路，
        这条旁路上的来源对账物理上就不存在。断言必须打在**实参值**上，
        只断言「门被调了三次」是假门——把这个 kwarg 删掉照样绿。
        """
        with self._bed() as bed, _stub_gates() as seen:
            bed.run()
            for stage, _, path in seen:
                self.assertEqual(path, bed.source_facts, stage)

    def test_the_legacy_ops_spec_path_is_no_longer_consulted(self):
        """⭐ 旧版读 `<dir>/ops/<op>/<op>.spec.json`——那既不是 CP-E staging 的落点，
        也不是 spec 变更门校的那份原件。这里在旧落点埋一份**退役形态**的 spec：
        旧代码会读到它并拒绝，现在必须完全无视它、按 staging 那份出裁决。
        """
        with self._bed() as bed, _stub_gates():
            _write_json(bed.path(os.path.join("ops", _OP, f"{_OP}.spec.json")),
                        {"op": _OP, "runner_form": "aclnn_py"})
            self.assertEqual(bed.run()["overall"], "PASS")

    # —— Critical ①：拒绝时旧 PASS 必须已经不在 ————————————————————————————
    def _refusals(self):
        """本轮已知的拒绝点，每条附「必须点到的那句话」。

        ⚠ 这张表不是穷举证明——恰恰相反：作废发生在 `finalize_directory` 第一行，
        与拒绝落在哪一处无关。所以表里刻意混进了从最早（spec 收据）到最晚（三级门、
        精度非干净）的各个位置；把作废挪到中段的话，靠前的几条会立刻红。
        """
        return (
            ("没有 spec 变更收据", lambda b: os.remove(SCG.receipt_path(b.out)),
             r"BLOCKED\(spec 变更未确认\)", None),
            ("spec 原件改过但收据没更新",
             lambda b: _write_json(b.spec_path, {"op": _OP, "runner_form": "cpp_extension",
                                                 "dtype": ["float32"]}),
             r"spec 已变更但收据未更新", None),
            ("source facts 指不到", lambda b: os.remove(b.source_facts),
             r"不是可信的 source_facts", None),
            ("staging 副本被换掉",
             lambda b: _write_json(b.path(W._STAGED_SPEC_FILE),
                                   {"op": _OP, "runner_form": "cpp_extension", "x": 1}),
             r"不是同一份字节", None),
            ("目录里根本没有 CP-E staging 的 spec.json",
             lambda b: os.remove(b.path(W._STAGED_SPEC_FILE)),
             r"没有 CP-E staging", None),
            ("证据件缺失", lambda b: os.remove(b.path("evidence.json")),
             r"缺/坏 JSON", None),
            ("三级门有 error", lambda b: None, r"验收门未过", {"task2": ["boom"]}),
            ("精度不是干净 pass",
             lambda b: _write_json(b.path("verdict.json"),
                                   {"overall": {"verdict": "needs_review"}}),
             r"精度 verdict 不是干净 pass", None),
        )

    def test_previous_pass_is_invalidated_on_every_refusal(self):
        """⭐ Critical ① 主门：**拒绝 ≠ 旧裁决留任**。

        断言分两层，缺一不可：① 确实是为了那个原因被拒（regex）——否则「随便抛个
        FinalizeError 就算数」是假门；② 上一轮的裁决面一件不剩。
        """
        for label, sabotage, pattern, gate_errors in self._refusals():
            with self.subTest(label), self._bed() as bed:
                bed.seed_previous_pass()
                sabotage(bed)
                with _stub_gates(gate_errors):
                    with self.assertRaisesRegex(F.FinalizeError, pattern):
                        bed.run()
                for name in _PREVIOUS_VERDICTS:
                    self.assertFalse(
                        os.path.lexists(bed.path(name)),
                        f"{label}：上一轮的 {name} 还在——下游会把旧 PASS 当成这次的结果")

    # —— Critical ②：spec 变更门在这条旁路上也是**两处、缺一不可** ————————————
    def test_the_spec_receipt_is_checked_before_the_evidence_is_even_read(self):
        """⭐ 入口门：spec 没被署名确认过，就**一步都别往下走**。

        断言打在「三级门一次都没被调用」上，而不只是「抛了异常」：出口门也会拦同一件事，
        只断言异常的话，把入口门整个删掉照样绿——那正是「只拦出口」的假安全感。
        """
        with self._bed() as bed:
            os.remove(SCG.receipt_path(bed.out))
            with _stub_gates() as seen:
                with self.assertRaisesRegex(F.FinalizeError, r"BLOCKED\(spec 变更未确认\)"):
                    bed.run()
            self.assertEqual(seen, [], "spec 未确认就不该再去跑三级门")

    def test_the_spec_receipt_is_rechecked_before_the_verdict_is_written(self):
        """⭐ 出口门：入口过了之后 spec 原件仍可能被换掉——写盘前必须再校一次。

        夹具在三级门执行期间改写 spec 原件（模拟「入口之后被换」）。只有入口门的话，
        这一轮会带着一份**没人确认过的 spec** 写出 PASS。
        """
        with self._bed() as bed:
            def _swap(d, errs, source_facts_path=None):
                _write_json(bed.spec_path,
                            {"op": _OP, "runner_form": "cpp_extension", "swapped": True})

            with mock.patch.dict(F.gate._GATES,
                                 {s: _swap for s in ("task1", "task2", "task3")}):
                with self.assertRaisesRegex(F.FinalizeError, r"BLOCKED\(spec 变更未确认\)"):
                    bed.run()
            self.assertFalse(os.path.lexists(bed.path("acceptance.json")),
                             "入口之后被换掉的 spec 不得留下一份裁决")

    def test_the_inputs_survive_the_invalidation(self):
        """作废的**边界**：只清最终裁决。`verdict.json` / `perf_report.json` 是本入口的
        **输入**，跟着清掉的话，本来能 finalize 的目录会被自己清成「缺件」。"""
        with self._bed() as bed:
            bed.seed_previous_pass()
            os.remove(SCG.receipt_path(bed.out))
            with _stub_gates():
                with self.assertRaises(F.FinalizeError):
                    bed.run()
            for name in ("verdict.json", "perf_report.json", "evidence.json",
                         W._STAGED_SPEC_FILE):
                self.assertTrue(os.path.isfile(bed.path(name)), name)

    def test_invalidation_runs_before_anything_is_even_read(self):
        """作废必须早于**读取**，不只是早于校验：把目录砸到读不出任何 JSON，旧 PASS 照样得没。"""
        with self._bed() as bed:
            bed.seed_previous_pass()
            for name in ("evidence.json", "verdict.json", "perf_report.json",
                         W._STAGED_SPEC_FILE):
                os.remove(bed.path(name))
            with _stub_gates():
                with self.assertRaises(F.FinalizeError):
                    bed.run()
            for name in _PREVIOUS_VERDICTS:
                self.assertFalse(os.path.lexists(bed.path(name)), name)

    def test_unremovable_stale_verdict_fails_closed(self):
        """清不掉就不往下走：留着它 = 本轮一旦被拒，旧裁决会被下游当成这次的结果。"""
        with self._bed() as bed:
            os.makedirs(bed.path("acceptance.json"))       # 目录占着裁决的名字，remove 必失败
            with _stub_gates() as seen:
                with self.assertRaisesRegex(F.FinalizeError, r"清不掉上一轮的结论产物"):
                    bed.run()
            self.assertEqual(seen, [], "清不掉就该当场停，不该再去跑门")

    def test_a_dangling_symlink_named_like_the_verdict_is_removed_not_followed(self):
        """悬空软链占着 `acceptance.json`：`os.path.exists` 看不见它，写盘却会跟着它逃出报告目录。"""
        with self._bed() as bed:
            outside = os.path.join(bed.root, "escaped.json")
            os.symlink(outside, bed.path("acceptance.json"))
            with _stub_gates():
                bed.run()
            self.assertFalse(os.path.islink(bed.path("acceptance.json")))
            self.assertFalse(os.path.exists(outside), "裁决不得被写出报告目录")

    # —— Critical ②：CLI 层面「不给就拒」——————————————————————————————————
    def test_cli_requires_both_spec_and_source_facts(self):
        """⭐ 旧命令行（只有 `--dir`）必须**直接失败**，不是拿缺省值继续跑。

        它们是两道门的物理入口：没有 `--spec` 就没有 spec 变更收据校验与 staging 等值校验，
        没有 `--source-facts` 就没有来源对账。给缺省 = 把门变成摆设。
        """
        with self._bed() as bed:
            for argv in ([f"--dir={bed.out}"],
                         [f"--dir={bed.out}", f"--spec={bed.spec_path}"],
                         [f"--dir={bed.out}", f"--source-facts={bed.source_facts}"]):
                with self.subTest(argv=argv), _stub_gates():
                    with self.assertRaises(SystemExit) as raised:
                        F.main(argv)
                    self.assertEqual(raised.exception.code, 2)

    def test_cli_happy_path_and_refusal_exit_codes(self):
        with self._bed() as bed, _stub_gates():
            argv = [f"--dir={bed.out}", f"--spec={bed.spec_path}",
                    f"--source-facts={bed.source_facts}"]
            self.assertEqual(F.main(argv), 0)
            os.remove(SCG.receipt_path(bed.out))
            self.assertEqual(F.main(argv), 1)
            self.assertFalse(os.path.lexists(bed.path("acceptance.json")),
                             "被拒的那一轮不得留下上一次写成的 PASS")


class InvalidationPrimitiveTest(unittest.TestCase):
    """共用原语的**清单**边界：两处共用动作，但清单必须不同。"""

    def test_final_verdict_set_is_a_strict_subset_that_excludes_the_inputs(self):
        self.assertTrue(set(W._FINAL_VERDICT_FILES) < set(W._RESULT_FILES))
        self.assertIn("acceptance.json", W._FINAL_VERDICT_FILES)
        self.assertTrue(set(W._REPORT_MD_FILES) <= set(W._FINAL_VERDICT_FILES))
        # 差集恰好是这条旁路的**输入**——多一件少一件都说明清单漂了。
        self.assertEqual(set(W._RESULT_FILES) - set(W._FINAL_VERDICT_FILES),
                         {"verdict.json", "perf_report.json"})

    def test_the_main_entrypoint_still_clears_its_own_full_set(self):
        """抽原语不得改动主入口的行为：它清的仍是 `_RESULT_FILES` + `_RESULT_GLOBS` 全集。"""
        with tempfile.TemporaryDirectory() as root:
            for name in W._RESULT_FILES:
                _write(os.path.join(root, name), "x")
            _write(os.path.join(root, "perf_sim_widget.svg"), "<svg/>")
            removed = W._invalidate_stale_results(root)
        self.assertEqual(set(removed),
                         set(W._RESULT_FILES) | {"perf_sim_widget.svg"})

    def test_the_shared_primitive_reports_what_it_removed(self):
        with tempfile.TemporaryDirectory() as root:
            _write(os.path.join(root, "acceptance.json"), "{}")
            removed = W.invalidate_results(root, W._FINAL_VERDICT_FILES,
                                           error_cls=F.FinalizeError)
        self.assertEqual(removed, ["acceptance.json"])
