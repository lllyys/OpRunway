"""make_must_cover.py：combos 由策略确定性生成，不再由模型手写。

去掉的那道断言是「combos 是否复现 coverage_policy 的投影」——模型两边都写，
把策略和 combos 一起编错时它照样通过，是循环的。改由本脚本从同一份策略算出
combos 之后，那道断言变成恒等式，留下的是两组独立度量：两两覆盖率和规模配比。
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

from _coverage_strategy import (  # noqa: E402
    CoveragePolicyError, _matching_rules, audit_coverage)
from _decl_fixture import project_for  # noqa: E402
from make_must_cover import build_combos  # noqa: E402

GOLDEN_TASK_DOC = (
    SKILL_ROOT.parent / "repo-task-doc-write" / "references" / "golden-task-doc.md")

DIMS = {
    "dtype": ["fp16", "fp32", "complex64"],
    "rank": [1, 2, 3, 4, 5, 6, 7, 8],
    "size_class": ["small", "medium", "large"],
    "shape_form": ["normal", "empty", "single_element", "unaligned_tail"],
    "contiguity": ["contiguous", "strided"],
    "op_axis_pos": ["first", "middle", "last"],
}
POLICY = {
    "strategy": "anchored_interactions",
    "baseline": {"dtype": "fp32", "rank": 2, "size_class": "small",
                 "shape_form": "normal", "contiguity": "contiguous",
                 "op_axis_pos": "last"},
    "interaction_groups": [],
    "targeted": [],
    "max_cases": 200,
}
INFEASIBLE = [{"rank": 1, "op_axis_pos": "first",
               "why": "一维张量的首轴就是末轴，不构成独立场景"}]


class MakeMustCoverTest(unittest.TestCase):
    def test_generated_combos_clear_the_gates(self):
        rows, _ = build_combos(DIMS, POLICY, INFEASIBLE)
        spec = {"dims": DIMS, "operator_class": "movement",
                "coverage_policy": POLICY, "infeasible": INFEASIBLE,
                "combos": [dict(r, coverage_tags=[]) for r in rows]}
        failures, report = audit_coverage(spec)
        self.assertEqual(report["pairwise"]["rate"], 1.0)
        self.assertFalse(failures, failures)

    def test_infeasible_rows_are_never_emitted(self):
        rows, _ = build_combos(DIMS, POLICY, INFEASIBLE)
        rules = [{k: v for k, v in r.items() if k != "why"} for r in INFEASIBLE]
        offenders = [r for r in rows if _matching_rules(r, rules)]
        self.assertEqual(offenders, [], offenders)

    def test_same_seed_is_deterministic(self):
        a, _ = build_combos(DIMS, POLICY, INFEASIBLE, seed=7)
        b, _ = build_combos(DIMS, POLICY, INFEASIBLE, seed=7)
        self.assertEqual(a, b)

    def test_interaction_group_is_expanded_on_top(self):
        policy = dict(POLICY, interaction_groups=[
            {"axes": ["size_class", "dtype"], "reason": "规模与 dtype 共同决定分支"}])
        rows, added = build_combos(DIMS, policy, INFEASIBLE)
        self.assertIn("size_class×dtype", added)
        pairs = {(r["size_class"], r["dtype"]) for r in rows}
        self.assertEqual(len(pairs), 3 * 3)

    def test_exceeding_max_cases_is_rejected(self):
        with self.assertRaises(CoveragePolicyError) as caught:
            build_combos(DIMS, dict(POLICY, max_cases=5), INFEASIBLE)
        self.assertIn("max_cases", str(caught.exception))

    def test_cli_writes_a_loadable_must_cover(self):
        with tempfile.TemporaryDirectory() as tmp:
            decl = Path(tmp) / "decl.json"
            out = Path(tmp) / "must_cover.json"
            decl.write_text(json.dumps(
                {"dims": DIMS, "operator_class": "movement",
                 "coverage_policy": POLICY,
                 "infeasible": INFEASIBLE, "axes": ["dtype", "rank"]},
                ensure_ascii=False), encoding="utf-8")
            done = subprocess.run(
                [sys.executable, str(SKILL_ROOT / "scripts" / "make_must_cover.py"),
                 "-d", str(decl), "-o", str(out), *project_for(tmp, DIMS)],
                capture_output=True, text=True, timeout=120)
            self.assertEqual(done.returncode, 0, done.stderr)
            spec = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(spec["axes"], ["dtype", "rank"])   # 透传字段保留
            self.assertTrue(spec["combos"])
            # 物化字段留空，由算子脚本填
            self.assertEqual(spec["combos"][0]["coverage_tags"], [])

    def test_dtype_binding_is_recorded_for_the_downstream_gate(self):
        """出处核对过之后要把这张表记进 must_cover，下游才换得了判据。

        浮点判据从「占比 ≥70%」换成「工程声明的浮点一种都不能漏」，靠的就是
        这份记录。不记，check_coverage 只能回落到比例门禁，钉死的 dtype 轴
        又会被逼着按 dtype 拆分面。
        """
        with tempfile.TemporaryDirectory() as tmp:
            decl = Path(tmp) / "decl.json"
            out = Path(tmp) / "must_cover.json"
            decl.write_text(json.dumps(
                {"dims": DIMS, "operator_class": "movement",
                 "coverage_policy": POLICY, "infeasible": INFEASIBLE},
                ensure_ascii=False), encoding="utf-8")
            done = subprocess.run(
                [sys.executable, str(SKILL_ROOT / "scripts" / "make_must_cover.py"),
                 "-d", str(decl), "-o", str(out), *project_for(tmp, DIMS)],
                capture_output=True, text=True, timeout=120)
            self.assertEqual(done.returncode, 0, done.stderr)
            spec = json.loads(out.read_text(encoding="utf-8"))
            binding = spec["dtype_binding"]
            self.assertTrue(binding["source"])
            self.assertEqual(binding["kind"], "project")
            for dtype in DIMS["dtype"]:
                self.assertIn(dtype, binding["declared_in_source"])

    def _run_with_task_doc(self, dtypes, task_doc_entry, *,
                           inside_project=False, source_text=None):
        if not GOLDEN_TASK_DOC.is_file():
            self.skipTest("doc-write 黄金任务书不在当前发布切片")
        with tempfile.TemporaryDirectory() as tmp:
            decl = Path(tmp) / "decl.json"
            out = Path(tmp) / "must_cover.json"
            interface = Path(tmp) / "interface.json"
            decl.write_text(json.dumps({
                "dims": {"dtype": dtypes},
                "coverage_policy": {
                    "strategy": "anchored_interactions",
                    "baseline": {"dtype": dtypes[0]},
                    "interaction_groups": [],
                    "targeted": [],
                    "max_cases": 20,
                },
            }), encoding="utf-8")
            payload = {"baseline_kind": "torch"}
            if task_doc_entry is not None:
                payload["task_doc"] = task_doc_entry
            interface.write_text(json.dumps(payload), encoding="utf-8")
            source = GOLDEN_TASK_DOC
            env_args = []
            if inside_project:
                project = Path(tmp) / "op_project"
                project.mkdir()
                source = project / "source.md"
                source.write_text(
                    source_text if source_text is not None
                    else GOLDEN_TASK_DOC.read_text(encoding="utf-8"),
                    encoding="utf-8")
                env = Path(tmp) / "env.json"
                env.write_text(json.dumps({
                    "operator_project": {"path": str(project)}}),
                    encoding="utf-8")
                env_args = ["--env", str(env)]
            done = subprocess.run(
                [sys.executable, str(SKILL_ROOT / "scripts" / "make_must_cover.py"),
                 "-d", str(decl), "-o", str(out),
                 "--dtype-source", str(source),
                 "--interface", str(interface), *env_args],
                capture_output=True, text=True, timeout=120)
            result = json.loads(out.read_text(encoding="utf-8")) \
                if out.is_file() else None
        return done, result

    @staticmethod
    def _golden_task_doc_entry():
        digest = hashlib.sha256(GOLDEN_TASK_DOC.read_bytes()).hexdigest()
        return {"name": GOLDEN_TASK_DOC.name, "sha256": digest}

    def test_task_doc_dtype_source_records_only_tensor_dtypes(self):
        done, spec = self._run_with_task_doc(
            ["fp16", "bf16"], self._golden_task_doc_entry())
        self.assertEqual(done.returncode, 0, done.stderr)
        binding = spec["dtype_binding"]
        self.assertEqual(binding["kind"], "taskdoc")
        self.assertEqual(binding["declared_in_source"], ["fp16", "bf16"])
        self.assertEqual(binding["excluded"], [])
        self.assertEqual(binding["sha256"], self._golden_task_doc_entry()["sha256"])

    def test_task_doc_dtype_source_rejects_an_omitted_dtype(self):
        done, _ = self._run_with_task_doc(
            ["fp16"], self._golden_task_doc_entry())
        self.assertEqual(done.returncode, 2)
        self.assertIn("bf16", done.stderr)
        self.assertIn("没声明", done.stderr)

    def test_task_doc_dtype_source_rejects_an_invented_dtype(self):
        done, _ = self._run_with_task_doc(
            ["fp16", "bf16", "fp32"], self._golden_task_doc_entry())
        self.assertEqual(done.returncode, 2)
        self.assertIn("fp32", done.stderr)
        self.assertIn("找不到", done.stderr)

    def test_task_doc_dtype_source_rejects_a_different_digest(self):
        entry = self._golden_task_doc_entry()
        entry["sha256"] = "0" * 64
        done, _ = self._run_with_task_doc(["fp16", "bf16"], entry)
        self.assertEqual(done.returncode, 2)
        self.assertIn("同一份", done.stderr)

    def test_task_doc_dtype_source_requires_interface_task_doc_metadata(self):
        done, _ = self._run_with_task_doc(["fp16", "bf16"], None)
        self.assertEqual(done.returncode, 2)
        self.assertIn("derive_interface", done.stderr)

    def test_stale_task_doc_inside_project_is_not_treated_as_a_readme(self):
        stale = GOLDEN_TASK_DOC.read_text(encoding="utf-8") + "\n<!-- stale -->\n"
        done, _ = self._run_with_task_doc(
            ["fp16", "bf16", "bool"], self._golden_task_doc_entry(),
            inside_project=True, source_text=stale)
        self.assertEqual(done.returncode, 2)
        self.assertIn("同一份", done.stderr)

    def test_plain_readme_inside_project_still_uses_project_mode(self):
        done, spec = self._run_with_task_doc(
            ["fp16", "bf16"], {"name": "task.md", "sha256": "0" * 64},
            inside_project=True,
            source_text="| 数据类型 | FLOAT16、BFLOAT16 |")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(spec["dtype_binding"]["kind"], "project")

    def test_structured_task_doc_inside_project_requires_task_doc_metadata(self):
        done, _ = self._run_with_task_doc(
            ["fp16", "bf16", "bool"], None, inside_project=True)
        self.assertEqual(done.returncode, 2)
        self.assertIn("derive_interface", done.stderr)

    def test_unknown_declaration_key_is_rejected(self):
        import subprocess
        import tempfile
        spec = {"dims": {"dtype": ["fp32", "fp16"]},
                "coverage_policy": {"strategy": "pairwise"},
                "typo_axes": ["dtype"]}
        with tempfile.TemporaryDirectory() as tmp:
            decl = Path(tmp) / "decl.json"
            decl.write_text(json.dumps(spec), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SKILL_ROOT / "scripts" / "make_must_cover.py"),
                 "-d", str(decl), "-o", str(Path(tmp) / "out.json")],
                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("typo_axes", result.stderr)

    def _reject(self, parameters, dims=None):
        spec = {"dims": dims or DIMS, "operator_class": "movement",
                "coverage_policy": POLICY, "infeasible": INFEASIBLE,
                "parameters": parameters}
        with tempfile.TemporaryDirectory() as tmp:
            decl = Path(tmp) / "decl.json"
            decl.write_text(json.dumps(spec, ensure_ascii=False),
                            encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SKILL_ROOT / "scripts" / "make_must_cover.py"),
                 "-d", str(decl), "-o", str(Path(tmp) / "out.json"),
                 *project_for(tmp, spec["dims"])],
                capture_output=True, text=True, timeout=120)

    def test_attr_array_dtype_outside_the_capability_domain_is_rejected(self):
        # roll 真机：这条以前要跑完 atk case 才由 C6 报出来。
        result = self._reject({
            "shifts": {"element_kind": "attr", "runtime_container": "tuple",
                       "dtype": "int64_t"}})
        self.assertEqual(2, result.returncode)
        self.assertIn("int64_t", result.stderr)

    def test_range_incompatible_with_a_declared_dtype_is_rejected(self):
        # roll 真机：这条以前要到冻结（S2 末尾）才由 freeze_inputs 报出来。
        result = self._reject(
            {"input": {"element_kind": "tensor", "runtime_container": "single",
                       "range": [-5, 5]}},
            dims=dict(DIMS, dtype=["fp32", "uint8", "uint32"]))
        self.assertEqual(2, result.returncode)
        self.assertIn("uint8", result.stderr)

    def test_a_compatible_declaration_still_passes(self):
        result = self._reject(
            {"input": {"element_kind": "tensor", "runtime_container": "single"},
             "shifts": {"element_kind": "attr", "runtime_container": "tuple",
                        "dtype": "int"}},
            dims=dict(DIMS, dtype=["fp32", "uint8"]))
        self.assertEqual(0, result.returncode, result.stderr)


class GroupExpansionKeepsRatioTest(unittest.TestCase):
    """交互组展开时规模轴必须轮转。

    真实事故：在 roll 上首次跑通固化策略时，两两覆盖打底的规模配比是对的，
    但 `shift_class × dims_class` 这个不含规模轴的交互组，29 行全部继承了
    baseline 的规模，把整体配比拉到 small 53% / medium 23% / large 24%，
    超出 ±12 容差。这正是旧设计「用例全堆在小规模」的同一个成因。
    """

    DIMS = dict(DIMS, mode=["m1", "m2", "m3"], flavor=["f1", "f2", "f3"])
    POLICY = dict(
        POLICY,
        baseline=dict(POLICY["baseline"], mode="m1", flavor="f1"),
        interaction_groups=[{"axes": ["mode", "flavor"],
                             "reason": "两者联合决定语义规范化行为"}],
        max_cases=400,
    )

    def test_group_without_size_axis_still_spreads_across_bands(self):
        rows, added = build_combos(self.DIMS, self.POLICY, [], operator_class="movement")
        self.assertGreater(added["mode×flavor"], 0)
        bands = {r["size_class"] for r in rows if r["mode"] != "m1" or r["flavor"] != "f1"}
        self.assertEqual(bands, {"small", "medium", "large"}, bands)

    def test_final_ratio_stays_within_tolerance(self):
        from collections import Counter
        from _coverage_strategy import SIZE_TARGET, SIZE_TOLERANCE
        rows, _ = build_combos(self.DIMS, self.POLICY, [], operator_class="movement")
        share = Counter(r["size_class"] for r in rows)
        for band, target in SIZE_TARGET.items():
            self.assertLessEqual(abs(share[band] / len(rows) - target), SIZE_TOLERANCE,
                                 f"{band}: {share[band]}/{len(rows)}")

    def test_pairwise_closure_reaches_full_coverage(self):
        # 贪心会漏个别轴对，补齐环节必须把它们闭合到 100%
        from _coverage_strategy import pairwise_report
        rows, _ = build_combos(self.DIMS, self.POLICY, [], operator_class="movement")
        self.assertEqual(pairwise_report(self.DIMS, rows, []) ["rate"], 1.0)


class AnchorRotationTest(unittest.TestCase):
    """展开与补齐必须轮换锚行，不能都从 rows[0] 复制。

    真实事故：交互组展开和两两补齐都写成 `row = dict(base_row)`，而
    base_row 恒为 rows[0]。组内没变的轴于是整体倒向那一行，实测把 uint8
    堆到 118 条里的 41 条（40%），其余 dtype 各 8-10 条。
    轴分布失衡不会被任何门禁拦下——搬运类不校验 dtype 配比。
    """

    DIMS = {
        "dtype": ["fp16", "bf16", "fp32", "int8", "uint8", "int32"],
        "rank": [1, 2, 3, 4],
        "size_class": ["small", "medium", "large"],
        "shape_form": ["normal", "unaligned_tail"],
        "op_axis_pos": ["first", "last"],
        "mode": ["a", "b", "c", "d"],
    }
    POLICY = {
        "strategy": "anchored_interactions",
        "baseline": {"dtype": "fp32", "rank": 2, "size_class": "small",
                     "shape_form": "normal", "op_axis_pos": "last", "mode": "a"},
        "interaction_groups": [{"axes": ["mode", "rank"], "reason": "联合决定分支"}],
        "targeted": [],
        "max_cases": 400,
    }

    def test_no_axis_value_takes_more_than_a_third(self):
        from collections import Counter
        rows, _ = build_combos(self.DIMS, self.POLICY, [], operator_class="movement")
        from _coverage_strategy import SIZE_AXIS
        for axis, values in self.DIMS.items():
            # 规模轴是刻意不均匀的（目标 40/30/30），由配比门禁单独核对
            if axis == SIZE_AXIS:
                continue
            share = Counter(row[axis] for row in rows)
            worst, count = share.most_common(1)[0]
            # 判据与轴的取值数无关：任何取值都不该超过均匀份额的 2 倍。
            # 原事故是 8 个 dtype 里某个占 40%，是均匀份额 12.5% 的 3.2 倍。
            ceiling = min(2.0 / len(values), 0.6)
            self.assertLess(
                count / len(rows), ceiling,
                f"{axis}={worst} 占 {count}/{len(rows)}"
                f"（上限 {ceiling:.0%}），锚行没有轮换")


if __name__ == "__main__":
    unittest.main()
