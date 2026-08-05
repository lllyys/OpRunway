import json
import os
import tempfile
import unittest

import render_acceptance_markdown as R

PR_HEAD = "a" * 40
LOCAL_DIGEST = "b" * 64
OTHER_DIGEST = "c" * 64
LOCAL_GIT_HEAD = "d" * 40


def _receipt(source):
    """最小 cpp_extension 收据：本节只读 `vendor.build_receipt.source`。"""
    return {"vendor": {"build_receipt": {"source": source}}}


def _docs(receipt):
    """一份「全绿、无失败明细」的最小产物集；只有 evidence 的收据随用例变。"""
    return {
        "acceptance.json": {
            "op": "X", "overall": "PASS", "state": "PASSED",
            "precision_verdict": "pass", "perf_status": "ok",
            "repo_mode": "cpp_extension", "gate": {"passed": True},
        },
        "verdict.json": {
            "op": "X", "standard": "s",
            "accuracy_summary": {"total": 1, "passed": 1, "failed": 0,
                                 "overall_pass_rate": 1.0, "by_dtype": []},
            "per_case": [{"case_id": "a", "精度": "pass", "判据": "ok"}],
        },
        "perf_report.json": {
            "summary": {"status": "ok", "planned_cases": 1, "perf_cases": 1,
                        "cases_scored": 1, "达标": 1},
            "by_shape_class": [], "non_passing_cases": [],
        },
        "evidence.json": {"cpp_extension_receipt": receipt},
        "caseset.json": {"op": "X", "task_pr_gaps": []},
    }


def _write_docs(root, docs, source_facts=None, source_facts_raw=None):
    """落盘产物；`source_facts` 按 content_address envelope 写（外层带 `payload`），
    因为 `_find_source_facts` 会优先解包 `payload`——夹具形状必须与真产物一致。"""
    for name, value in docs.items():
        with open(os.path.join(root, name), "w", encoding="utf-8") as out:
            json.dump(value, out)
    path = os.path.join(root, "source_facts.json")
    if source_facts is not None:
        with open(path, "w", encoding="utf-8") as out:
            json.dump({"payload": source_facts}, out)
    elif source_facts_raw is not None:
        with open(path, "w", encoding="utf-8") as out:
            out.write(source_facts_raw)


def _local_facts(root_digest=LOCAL_DIGEST, git=None, op_subdir="ops/x"):
    facts = {
        "dut_source": "local_checkout",
        "local_checkout": {"root_digest": root_digest, "op_subdir": op_subdir},
    }
    if git is not None:
        facts["local_checkout"]["git"] = git
    return facts


class RenderAcceptanceMarkdownTest(unittest.TestCase):
    def test_renders_structured_and_legacy_text_gaps(self):
        self.assertEqual(
            R._gap_items("单条自由文本"),
            ["单条自由文本"],
        )
        self.assertEqual(
            R._gap_line("缺少无 dim 的全局 overload"),
            "- 缺少无 dim 的全局 overload",
        )
        self.assertEqual(
            R._gap_line({
                "issue": "dtype_deferred",
                "impact": "暂缓",
                "pr_fact": "op_def 不支持",
            }),
            "- `dtype_deferred`：暂缓（PR 事实：op_def 不支持）",
        )
        self.assertEqual(
            R._gap_line({
                "kind": "dtype_deferred",
                "dtypes": ["int32"],
                "reason": "runner 未支持",
            }),
            '- `dtype_deferred`：runner 未支持；补充：{"dtypes": ["int32"]}',
        )

    def test_renders_existing_verdict_without_rejudging(self):
        with tempfile.TemporaryDirectory() as root:
            docs = {
                "acceptance.json": {
                    "op": "X", "overall": "FAIL(精度)", "state": "FAILED_PRECISION",
                    "precision_verdict": "fail", "perf_status": "skipped_precision_gate",
                    "repo_mode": "cpp_extension", "gate": {"passed": True},
                },
                "verdict.json": {
                    "op": "X", "standard": "ascendoptest_default",
                    "accuracy_summary": {
                        "total": 2, "passed": 1, "failed": 1,
                        "overall_pass_rate": 0.5,
                        "by_dtype": [{"dtype": "float32", "count": 2, "passed": 1,
                                      "failed": 1, "uncertain": 0, "pass_rate": 0.5}],
                    },
                    "overall": {"counts": {"total": 2, "fail": 1}},
                    "per_case": [
                        {"case_id": "a", "精度": "pass", "判据": "ok"},
                        {"case_id": "b", "精度": "fail", "判据": "mismatch=1"},
                    ],
                },
                "perf_report.json": {
                    "summary": {"status": "skipped_precision_gate", "planned_cases": 1,
                                "perf_cases": 0, "cases_scored": 0, "达标": 0},
                    "by_shape_class": [{"class": "small", "planned_cases": 1,
                                        "cases": 0, "cases_scored": 0, "达标": 0}],
                    "non_passing_cases": [],
                },
                "evidence.json": {"cpp_extension_receipt": {}},
                "caseset.json": {"op": "X", "task_pr_gaps": []},
            }
            for name, value in docs.items():
                with open(os.path.join(root, name), "w", encoding="utf-8") as out:
                    json.dump(value, out)
            path = R.write_report(root)
            with open(path, encoding="utf-8") as src:
                text = src.read()
            self.assertIn("# X 算子验收报告", text)
            self.assertIn("`FAIL(精度)`", text)
            self.assertIn("| `float32` | 2 | 1 | 1 |", text)
            self.assertIn("[精度失败明细.md](精度失败明细.md)", text)
            self.assertNotIn("./repro/show_case.sh b", text)
            self.assertIn("./repro/audit_case.sh 1", text)
            self.assertIn("性能未执行", text)
            # 空收据（本用例的 evidence 就是 `{"cpp_extension_receipt": {}}`）走
            # 「无收据」分支——没有这条断言，该分支无人覆盖。
            self.assertIn("不对被测来源作任何 provenance 断言", text)
            detail = os.path.join(root, "精度失败明细.md")
            self.assertTrue(os.path.isfile(detail))
            with open(detail, encoding="utf-8") as src:
                detail_text = src.read()
            self.assertIn("./repro/review.sh show 1", detail_text)
            self.assertIn("./repro/audit_case.sh 1", detail_text)
            self.assertIn("`b`", detail_text)
            self.assertFalse(os.path.exists(os.path.join(root, "性能失败明细.md")))

    def test_splits_performance_non_passing_detail(self):
        with tempfile.TemporaryDirectory() as root:
            docs = {
                "acceptance.json": {
                    "op": "X", "overall": "FAIL(性能)", "state": "FAILED_PERFORMANCE",
                    "precision_verdict": "pass", "perf_status": "failed",
                    "repo_mode": "cpp_extension", "gate": {"passed": True},
                },
                "verdict.json": {
                    "op": "X", "standard": "s",
                    "accuracy_summary": {"total": 1, "passed": 1, "failed": 0,
                                         "overall_pass_rate": 1.0, "by_dtype": []},
                    "per_case": [{"case_id": "p0", "精度": "pass", "判据": "ok"}],
                },
                "perf_report.json": {
                    "summary": {"status": "failed", "planned_cases": 1,
                                "perf_cases": 1, "cases_scored": 1, "达标": 0},
                    "by_shape_class": [],
                    "non_passing_cases": [{
                        "case_id": "p0", "outcome": "failed", "reason": "ratio below threshold",
                        "dtype": "float16", "shape_class": "large",
                        "inputs": [{"name": "self", "shape": [128, 128]}],
                        "ratio": 0.5, "target_ratio": 1.0,
                        "custom": {"us": 4.0}, "baseline": {"us": 2.0},
                    }],
                },
                "evidence.json": {"cpp_extension_receipt": {}},
                "caseset.json": {
                    "op": "X", "task_pr_gaps": [],
                    "cases": [{
                        "id": "p0",
                        "inputs": [{"name": "self", "shape": [128, 128],
                                    "dtype": "float16"}],
                        "attrs": {"dim": 0, "keepDim": False},
                        "aclnn_call": {"symbol": "ExampleDim"},
                    }],
                },
            }
            for name, value in docs.items():
                with open(os.path.join(root, name), "w", encoding="utf-8") as out:
                    json.dump(value, out)
            path = R.write_report(root)
            with open(path, encoding="utf-8") as src:
                text = src.read()
            self.assertIn("[性能失败明细.md](性能失败明细.md)", text)
            self.assertNotIn("ratio below threshold", text)
            self.assertIn("不对被测来源作任何 provenance 断言", text)
            detail = os.path.join(root, "性能失败明细.md")
            with open(detail, encoding="utf-8") as src:
                detail_text = src.read()
            self.assertIn("ratio below threshold", detail_text)
            self.assertIn("`p0`", detail_text)
            self.assertIn("`[[128, 128]]`", detail_text)
            self.assertIn('属性：`{"dim": 0, "keepDim": false}`', detail_text)
            self.assertIn("DUT 接口：`ExampleDim`", detail_text)
            self.assertIn("要求阈值：`1.0`", detail_text)
            self.assertIn("缺单 case 性能重放能力", detail_text)
            self.assertFalse(os.path.exists(os.path.join(root, "精度失败明细.md")))


class ProvenanceSectionTest(unittest.TestCase):
    """「## 来源与 provenance」节：强度如实标注，未知绝不升级为 clean。"""

    def _render(self, receipt, source_facts=None, source_facts_raw=None):
        with tempfile.TemporaryDirectory() as root:
            _write_docs(root, _docs(receipt),
                        source_facts=source_facts, source_facts_raw=source_facts_raw)
            path = R.write_report(root)
            self.assertTrue(os.path.isfile(path))
            with open(path, encoding="utf-8") as src:
                return src.read()

    def test_pull_request_receipt_renders_pr_head_in_own_section(self):
        text = self._render(_receipt({"repo": "cann/ops-nn", "pr_head_sha": PR_HEAD}))
        self.assertIn(R.PROV_HEADING, text)
        self.assertIn(f"| PR head | `{PR_HEAD}` |", text)
        self.assertIn("| 源码仓 | `cann/ops-nn` |", text)
        # provenance 节排在运行环境之前，且那两行确实从旧表里迁走了。
        self.assertLess(text.index(R.PROV_HEADING), text.index("## 被测物与运行环境"))
        env = text.split("## 被测物与运行环境", 1)[1]
        self.assertNotIn("| PR head |", env)
        self.assertNotIn("| 源码仓 |", env)
        self.assertIn("| SoC |", env)
        # PR 通路不该沾上本地通路的 caveat。
        for caveat in R.PROV_LOCAL_CAVEATS:
            self.assertNotIn(caveat, text)

    def test_local_receipt_without_source_facts_never_claims_clean(self):
        text = self._render(_receipt({
            "dut_source": "local_checkout", "repo": "cann/ops-nn",
            "local_root_digest": LOCAL_DIGEST}))
        self.assertIn(f"| 子树摘要 root_digest | `{LOCAL_DIGEST}` |", text)
        # caveat 只依赖 kind：没有 source_facts 也必须一条不少。
        for caveat in R.PROV_LOCAL_CAVEATS:
            self.assertIn(caveat, text)
        self.assertIn("不得据此认定 worktree clean", text)
        # 防 fail-open 的核心断言：除了那句「不得据此认定 worktree clean」的告警本身，
        # 报告里不许再出现任何 clean 措辞——把「查不到脏」写成「干净」是本节最贵的缺陷。
        self.assertNotIn("clean", text.replace(R.PROV_DIRTY_UNKNOWN, ""))
        self.assertNotIn(R.PROV_DIRTY_CLEAN, text)

    def test_local_receipt_with_matching_facts_renders_dirty(self):
        text = self._render(
            _receipt({"dut_source": "local_checkout", "repo": "cann/ops-nn",
                      "local_root_digest": LOCAL_DIGEST}),
            source_facts=_local_facts(git={
                "head_sha": LOCAL_GIT_HEAD, "dirty": True,
                "dirty_files": ["a.cpp", "b.cpp"], "dirty_files_in_op_subdir": ["a.cpp"]}))
        self.assertIn("**dirty**——worktree 有 2 项未提交改动（被测子树内 1 项）", text)
        self.assertNotIn(R.PROV_DIRTY_UNKNOWN, text)
        self.assertNotIn(R.PROV_DIRTY_IGNORED, text)
        # git head 可以渲染，但必须原地标成信息字段，不能被当 PR head 读。
        self.assertIn(f"| git head（**信息字段，非 provenance 锚**） | `{LOCAL_GIT_HEAD}` |", text)
        # caveat 不因为拿到了 facts 就消失。
        for caveat in R.PROV_LOCAL_CAVEATS:
            self.assertIn(caveat, text)

    def test_local_receipt_ignores_source_facts_with_mismatched_anchor(self):
        text = self._render(
            _receipt({"dut_source": "local_checkout", "repo": "cann/ops-nn",
                      "local_root_digest": LOCAL_DIGEST}),
            source_facts=_local_facts(root_digest=OTHER_DIGEST, op_subdir="ops/OTHER-SUBTREE",
                                      git={"head_sha": LOCAL_GIT_HEAD, "dirty": False,
                                           "dirty_files": [], "dirty_files_in_op_subdir": []}))
        self.assertIn("与本轮收据的来源锚不一致，已忽略", text)
        self.assertIn("不得据此认定 worktree clean", text)
        # 整份忽略 = 里面的值一个都不进报告（否则等于把另一份 checkout 的事实注进本轮 provenance）。
        self.assertNotIn(OTHER_DIGEST, text)
        self.assertNotIn(LOCAL_GIT_HEAD, text)
        self.assertNotIn("OTHER-SUBTREE", text)
        self.assertNotIn(R.PROV_DIRTY_CLEAN, text)
        self.assertIn(f"| 子树摘要 root_digest | `{LOCAL_DIGEST}` |", text)

    def test_malformed_receipt_source_still_renders_report_without_anchor(self):
        # `repo` 缺失 → `validate_build_receipt_source` 抛错；锚值虽在收据里，但校验没过，
        # 一个字都不该被当成 provenance 渲染出去。
        text = self._render(_receipt({"dut_source": "local_checkout",
                                      "local_root_digest": LOCAL_DIGEST}))
        self.assertIn("来源锚不合法", text)
        self.assertNotIn(LOCAL_DIGEST, text)
        self.assertNotIn("| 子树摘要 root_digest |", text)
        # 报告本体照出：异常被 catch 在节内，不能把整份 `验收报告.md` 拖没。
        self.assertIn("## 精度汇总", text)
        self.assertIn("## 被测物与运行环境", text)

    def test_unreadable_source_facts_falls_back_to_unknown(self):
        text = self._render(
            _receipt({"dut_source": "local_checkout", "repo": "cann/ops-nn",
                      "local_root_digest": LOCAL_DIGEST}),
            source_facts_raw="{ 这不是 JSON")
        self.assertIn(R.PROV_DIRTY_UNKNOWN, text)
        self.assertNotIn("clean", text.replace(R.PROV_DIRTY_UNKNOWN, ""))
        for caveat in R.PROV_LOCAL_CAVEATS:
            self.assertIn(caveat, text)
