import json
import os
import tempfile
import unittest

import content_address
import render_acceptance_markdown as R

PR_HEAD = "a" * 40
LOCAL_DIGEST = "b" * 64
OTHER_DIGEST = "c" * 64
LOCAL_GIT_HEAD = "d" * 40
_FACTS_DOMAIN = "oprunway/source-facts/v1"


def _receipt(source, **build_receipt_overrides):
    """最小 cpp_extension 收据。

    默认带 `VERIFIED v1` 的收据壳：provenance 节的强度断言以「收据已核验」为前提，
    没有这三个字段它连锚都不看（见 `_provenance_section` 的分支 ②）。
    """
    br = {"schema": "oprunway.vendor_build_receipt", "schema_version": 1,
          "status": "VERIFIED", "source": source}
    br.update(build_receipt_overrides)
    return {"vendor": {"build_receipt": br}}


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
    """落盘产物；`source_facts` 按**真** content_address envelope 写。

    ⚠ 夹具必须走 `make_artifact` 而不是手拼 `{"payload": …}`：`_find_source_facts` 会复算
    digest，手拼信封没有 digest 就会被判 `__BAD__`——那样这些用例测的其实是「读不出」分支，
    看着绿、覆盖的却不是它们声称覆盖的路径。
    """
    for name, value in docs.items():
        with open(os.path.join(root, name), "w", encoding="utf-8") as out:
            json.dump(value, out)
    path = os.path.join(root, "source_facts.json")
    if source_facts is not None:
        with open(path, "w", encoding="utf-8") as out:
            json.dump(content_address.make_artifact(_FACTS_DOMAIN, source_facts), out)
    elif source_facts_raw is not None:
        with open(path, "w", encoding="utf-8") as out:
            out.write(source_facts_raw)


def _local_facts(root_digest=LOCAL_DIGEST, git=None, op_subdir="ops/x",
                 completeness=None):
    """⚠ 用共享的**完整契约** payload：渲染器复用三级门的 `_find_source_facts`，
    而那道门会拿 `validate_preparation_state._validate_source_payload` 校这份对照物。
    只塞一个 `root_digest` 的最小 payload 会被判 `__BAD__`，用例就测不到它想测的分支。
    """
    from test_validate_cpp_extension_receipt import source_facts_payload
    return source_facts_payload(
        dut_source="local_checkout", root_digest=root_digest,
        git=git, completeness=completeness, op_subdir=op_subdir)


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
        # 这份收据没有 `repo_source`（老收据形态）→ 源码仓那一行必须标「强度未知」。
        self.assertIn(R.PROV_REPO_ROW.format(
            repo=R._code_cell("cann/ops-nn"), strength=R.PROV_REPO_SOURCE_ABSENT), text)
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

    def test_tampered_source_facts_envelope_is_not_trusted(self):
        """⭐ payload 被改、digest 没跟着改 → 整份不可信，退「未知」而不是照采信。

        对照物的可信度是本地锚的全部依据；不复算 digest 的话，随手编一份最小 JSON
        就能给一份可能 dirty 的 checkout 发 clean 证明。
        """
        with tempfile.TemporaryDirectory() as root:
            _write_docs(root, _docs(_receipt(
                {"dut_source": "local_checkout", "repo": "cann/ops-nn",
                 "local_root_digest": LOCAL_DIGEST})),
                source_facts=_local_facts(git={
                    "head_sha": LOCAL_GIT_HEAD, "dirty": True,
                    "dirty_files": ["a.cpp"], "dirty_files_in_op_subdir": ["a.cpp"]}))
            path = os.path.join(root, "source_facts.json")
            with open(path, encoding="utf-8") as src:
                doc = json.load(src)
            doc["payload"]["local_checkout"]["git"]["dirty"] = False   # 洗白，digest 不动
            doc["payload"]["local_checkout"]["git"]["dirty_files"] = []
            with open(path, "w", encoding="utf-8") as out:
                json.dump(doc, out)
            text = R.render(root)
        self.assertIn(R.PROV_DIRTY_UNKNOWN, text)
        self.assertNotIn(R.PROV_DIRTY_CLEAN, text)

    def test_complete_facts_without_git_key_render_not_a_git_repo(self):
        text = self._render(
            _receipt({"dut_source": "local_checkout", "repo": "cann/ops-nn",
                      "local_root_digest": LOCAL_DIGEST}),
            source_facts=_local_facts())
        self.assertIn(R.PROV_DIRTY_NOT_GIT, text)


    def test_clean_worktree_still_renders_clean(self):
        """收紧不能误伤正例：dirty=false + 空清单 = 真的干净。"""
        text = self._render(
            _receipt({"dut_source": "local_checkout", "repo": "cann/ops-nn",
                      "local_root_digest": LOCAL_DIGEST}),
            source_facts=_local_facts(git={
                "head_sha": LOCAL_GIT_HEAD, "dirty": False,
                "dirty_files": [], "dirty_files_in_op_subdir": []}))
        self.assertIn(R.PROV_DIRTY_CLEAN, text)

    def test_unverified_build_receipt_makes_no_provenance_claim(self):
        """⭐ 锚形态合法 ≠ 收据可信：没 VERIFIED 就不能出「可证明验的就是…」这类强度断言。"""
        for label, override in (
                ("status 非 VERIFIED", {"status": "PENDING"}),
                ("schema 不对", {"schema": "something.else"}),
                ("schema_version 不对", {"schema_version": 2}),
        ):
            with self.subTest(label):
                text = self._render(_receipt(
                    {"repo": "cann/ops-nn", "pr_head_sha": PR_HEAD}, **override))
                self.assertIn("本节不作任何 provenance 断言", text)
                self.assertNotIn(f"| PR head | `{PR_HEAD}` |", text)
                self.assertNotIn(PR_HEAD, text)
                self.assertIn("## 精度汇总", text)      # 报告本体照出


class RepoSourceStrengthTest(unittest.TestCase):
    """「源码仓」一行必须同时呈现**强度**：事实派生 / 操作者自报 / 强度未知。

    实测逮到的 fail-open：两轮真机跑出的报告里，`https://gitcode.com/cann/ops-nn.git`
    （`repo_source=local_checkout.git.remote_url`，事实派生）与 `cann/ops-nn`
    （去 git 那轮，`repo_source=operator`，树里根本没有仓名证据）**同权并列**，
    审核员读不出后者只是一句自报。收据记得很老实，是渲染层把强度吞了。
    """

    def _render(self, source):
        with tempfile.TemporaryDirectory() as root:
            _write_docs(root, _docs(_receipt(source)))
            return R.render(root)

    def _repo_line(self, text):
        """取「源码仓」那一行；断言它**存在且唯一**——强度被拆到别处也算吞掉了。"""
        rows = [line for line in text.splitlines() if line.startswith("| 源码仓 |")]
        self.assertEqual(len(rows), 1, text)
        return rows[0]

    # 三种已知取值各一 -------------------------------------------------------------
    def test_pr_derived_repo_says_where_it_came_from(self):
        row = self._repo_line(self._render(
            {"repo": "cann/ops-nn", "repo_source": "pr.source_repo",
             "pr_head_sha": PR_HEAD}))
        self.assertEqual(row, R.PROV_REPO_ROW.format(
            repo=R._code_cell("cann/ops-nn"), strength=R.PROV_REPO_SOURCE_LABEL["pr.source_repo"]))
        self.assertIn("事实派生", row)
        self.assertIn("`pr.source_repo`", row)

    def test_local_remote_url_derived_repo_says_where_it_came_from(self):
        row = self._repo_line(self._render(
            {"dut_source": "local_checkout",
             "repo": "https://gitcode.com/cann/ops-nn.git",
             "repo_source": "local_checkout.git.remote_url",
             "local_root_digest": LOCAL_DIGEST}))
        self.assertEqual(row, R.PROV_REPO_ROW.format(
            repo=R._code_cell("https://gitcode.com/cann/ops-nn.git"),
            strength=R.PROV_REPO_SOURCE_LABEL["local_checkout.git.remote_url"]))
        self.assertIn("事实派生", row)
        self.assertIn("`local_checkout.git.remote_url`", row)

    def test_operator_reported_repo_is_marked_as_self_reported(self):
        """⭐ 去 git 那轮的真实形态：树里没有仓名证据，仓名是构建时手给的。"""
        row = self._repo_line(self._render(
            {"dut_source": "local_checkout", "repo": "cann/ops-nn",
             "repo_source": "operator", "local_root_digest": LOCAL_DIGEST}))
        self.assertEqual(row, R.PROV_REPO_ROW.format(
            repo=R._code_cell("cann/ops-nn"), strength=R.PROV_REPO_SOURCE_LABEL["operator"]))
        self.assertIn("操作者自报", row)
        self.assertIn("无机器可核依据", row)
        # 自报绝不能读起来像派生。
        self.assertNotIn("事实派生", row)

    # 缺席与未知 -------------------------------------------------------------------
    def test_absent_repo_source_is_unknown_strength_not_derived(self):
        """⭐ 老收据没有这个键：缺席 = 不知道，不是「大概是派生的」，也不是 operator。"""
        row = self._repo_line(self._render(
            {"dut_source": "local_checkout", "repo": "cann/ops-nn",
             "local_root_digest": LOCAL_DIGEST}))
        self.assertEqual(row, R.PROV_REPO_ROW.format(
            repo=R._code_cell("cann/ops-nn"), strength=R.PROV_REPO_SOURCE_ABSENT))
        self.assertIn("强度未知", row)
        self.assertNotIn("事实派生", row.replace("缺席不等于事实派生", ""))
        self.assertNotIn("操作者自报", row)

    def test_unknown_repo_source_value_fails_closed_to_unknown(self):
        """⭐ 词表外取值不猜属于哪一种——静默归类等于凭空给它发一张强度证明。"""
        row = self._repo_line(self._render(
            {"repo": "cann/ops-nn", "repo_source": "probably_the_pr_i_guess",
             "pr_head_sha": PR_HEAD}))
        self.assertEqual(row, R.PROV_REPO_ROW.format(
            repo=R._code_cell("cann/ops-nn"), strength=R.PROV_REPO_SOURCE_UNKNOWN.format(
                value='"probably_the_pr_i_guess"')))
        self.assertIn("强度未知", row)
        self.assertNotIn("事实派生", row)
        self.assertNotIn("操作者自报", row)

    def test_null_repo_source_is_unknown_and_distinguishable_from_absent(self):
        """`repo_source: null` 是「记了个空」，与「没这个键」不同形，但同样退未知。"""
        row = self._repo_line(self._render(
            {"repo": "cann/ops-nn", "repo_source": None, "pr_head_sha": PR_HEAD}))
        self.assertEqual(row, R.PROV_REPO_ROW.format(
            repo=R._code_cell("cann/ops-nn"),
            strength=R.PROV_REPO_SOURCE_UNKNOWN.format(value="null")))
        self.assertNotEqual(R.PROV_REPO_SOURCE_UNKNOWN.format(value="null"),
                            R.PROV_REPO_SOURCE_ABSENT)

    def test_derived_origin_from_the_other_path_fails_closed(self):
        """⭐ PR 通路声称派生自本地 git remote——`derive_repo` 派生不出这种组合，
        只可能来自手改收据；「事实派生」四个字没有出处，退未知。"""
        row = self._repo_line(self._render(
            {"repo": "cann/ops-nn", "repo_source": "local_checkout.git.remote_url",
             "pr_head_sha": PR_HEAD}))
        self.assertEqual(row, R.PROV_REPO_ROW.format(
            repo=R._code_cell("cann/ops-nn"), strength=R.PROV_REPO_SOURCE_MISMATCH.format(
                value='"local_checkout.git.remote_url"', kind="pull_request")))
        self.assertNotIn("事实派生", row)

    def test_no_repo_row_at_all_when_receipt_is_unverified(self):
        """收据不可信时整节都不作断言——那种情况下连源码仓一行都不该出现。

        没有这条，「渲染源码仓但不渲染强度」可以靠删掉整行伪装成通过。
        """
        with tempfile.TemporaryDirectory() as root:
            _write_docs(root, _docs(_receipt(
                {"repo": "cann/ops-nn", "repo_source": "pr.source_repo",
                 "pr_head_sha": PR_HEAD}, status="PENDING")))
            text = R.render(root)
        self.assertNotIn("| 源码仓 |", text)
        self.assertIn("本节不作任何 provenance 断言", text)


class LocalRowsSecondLineOfDefenceTest(unittest.TestCase):
    """`_local_rows` 自己那层形态判定——**第二道防线**，所以只能直调来见证。

    这些畸形 payload 走不到渲染层：`_find_source_facts` 已经拿
    `validate_preparation_state._validate_source_payload` 把它们判成 `__BAD__` 了
    （contract 那层同样校 `completeness`、`dirty ↔ dirty_files`）。
    早一层拦住是好事，但**不能因此把渲染层这几条删掉**：渲染层的职责是
    「拿到什么都不许说成 clean」，它不该依赖上游一定筛干净。
    这里直调 `_local_rows` 就是为了让第二道防线有独立见证，不被第一道遮住。
    """

    IDENT = ("local_checkout", "local_root_digest", LOCAL_DIGEST)

    def _rows(self, **kw):
        return "\n".join(R._local_rows(_local_facts(**kw), self.IDENT))

    def test_incomplete_source_facts_is_unknown_not_non_git(self):
        """⭐ 残缺事实包里 `git` 键缺席，含义是「不知道」，不是「不是 git 仓」。"""
        text = self._rows(completeness={
            "status": "blocked", "reasons": ["dirty_worktree_not_allowed"]})
        self.assertIn(R.PROV_DIRTY_INCOMPLETE, text)
        self.assertNotIn(R.PROV_DIRTY_NOT_GIT, text)
        self.assertNotIn(R.PROV_DIRTY_CLEAN, text)

    def test_dirty_true_with_empty_list_never_renders_zero_changes(self):
        """⭐ 「有 0 项未提交改动」读起来像没事，实则自相矛盾——只能退「未知」。"""
        text = self._rows(git={"dirty": True, "dirty_files": [],
                               "dirty_files_in_op_subdir": []})
        self.assertIn(R.PROV_DIRTY_MALFORMED, text)
        self.assertNotIn("0 项未提交改动", text)
        self.assertNotIn(R.PROV_DIRTY_CLEAN, text)

    def test_dirty_false_with_nonempty_list_is_not_clean(self):
        text = self._rows(git={"dirty": False, "dirty_files": ["a.cpp"],
                               "dirty_files_in_op_subdir": []})
        self.assertIn(R.PROV_DIRTY_MALFORMED, text)
        self.assertNotIn(R.PROV_DIRTY_CLEAN, text)

    def test_non_string_dirty_entry_is_not_counted(self):
        """⭐ `dirty_files=[null]` 被 `len()` 数成 1 → 「有 1 项未提交改动」，数字是编的。"""
        text = self._rows(git={"dirty": True, "dirty_files": [None],
                               "dirty_files_in_op_subdir": []})
        self.assertIn(R.PROV_DIRTY_MALFORMED, text)
        self.assertNotIn("1 项未提交改动", text)

    def test_in_op_subdir_must_be_a_subset(self):
        """子树清单不是总清单的子集时，「被测子树内 N 项」可以大于总数——拒绝给结论。"""
        text = self._rows(git={"dirty": True, "dirty_files": ["a.cpp"],
                               "dirty_files_in_op_subdir": ["a.cpp", "b.cpp"]})
        self.assertIn(R.PROV_DIRTY_MALFORMED, text)

    def test_git_null_is_malformed_not_non_git(self):
        facts = _local_facts()
        facts["local_checkout"]["git"] = None
        text = "\n".join(R._local_rows(facts, self.IDENT))
        self.assertIn(R.PROV_DIRTY_MALFORMED, text)
        self.assertNotIn(R.PROV_DIRTY_NOT_GIT, text)


class CodeCellInjectionTest(unittest.TestCase):
    """⭐ 表格里的代码段必须挡住反引号破出。

    `源码仓` 那一格的值来自 vendor build receipt，而收据的 `repo` 只被校「非空字符串」——
    是**外部可控**的。写成 `` f"`{v}`" `` 时，值里一个反引号就能提前闭合代码段，
    后面的内容按 markdown 正常渲染：不可信的字符串就此变成能排版的内容。
    """

    @staticmethod
    def _longest_backtick_run(text):
        longest = run = 0
        for ch in text:
            run = run + 1 if ch == "`" else 0
            longest = max(longest, run)
        return longest

    def test_backticks_cannot_break_out_of_the_code_span(self):
        for payload in ("a`b", "`", "``", "a``b`c", "`lead", "trail`", "```x```"):
            with self.subTest(payload=payload):
                cell = R._code_cell(payload)
                fence = cell[:len(cell) - len(cell.lstrip("`"))]
                # 围栏必须严格长于内容里最长的连续反引号，否则内容能提前闭合它
                self.assertGreater(
                    len(fence), self._longest_backtick_run(payload),
                    f"围栏 {fence!r} 挡不住 {payload!r} 里的反引号")
                self.assertIn(payload, cell)          # 内容一个字符都没丢
                self.assertTrue(cell.endswith(fence))  # 首尾同长围栏

    def test_pipe_and_newlines_cannot_forge_table_rows(self):
        for payload in ("a|b", "a\nb", "a\r\nb", "a\rb"):
            with self.subTest(payload=repr(payload)):
                cell = R._cell(payload)
                self.assertNotIn("\n", cell)
                self.assertNotIn("\r", cell)
                if "|" in payload:
                    self.assertIn("\\|", cell)

    def test_repo_row_uses_the_hardened_code_cell(self):
        """⭐ 钉住调用点：`源码仓` 行必须走 `_code_cell`，不是自己包一对反引号。"""
        self.assertNotIn("`{repo}`", R.PROV_REPO_ROW)
        self.assertIn("{repo}", R.PROV_REPO_ROW)


if __name__ == "__main__":
    unittest.main()
