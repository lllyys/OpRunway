import copy
import json
import os
import tempfile
import threading
import unittest
from unittest import mock

import content_address
import kernel_identity as K
import render_acceptance_markdown as R
import source_build_binding as SBB
import test_current_receipt_fixtures as F
import vendor_build_receipt as VBR

PR_HEAD = "a" * 40
SUBTREE_DIGEST = "b" * 64
WHOLE_DIGEST = "e" * 64
OTHER_DIGEST = "c" * 64
#: 只出现在 `source_facts` 里、绝不出现在收据里的值——用来见证「报告一个 facts 字段都不引用」。
FACTS_ONLY_MARKER = "1" * 64
_FACTS_DOMAIN = "oprunway/source-facts/v1"
#: `summarize` 会连 build 段一起校（没有成功的 build argv 就没有「这个 so 是这么来的」）。
_BUILD = {"argv": ["./build.sh"], "cwd": "/w", "returncode": 0,
          VBR.RETURNCODE_SOURCE_KEY: VBR.RETURNCODE_SOURCE_MEASURED}


def _receipt(source, schema_version=VBR.SCHEMA_VERSION_LEGACY, degradations=None,
             **build_receipt_overrides):
    """最小 cpp_extension 收据。

    默认带 `VERIFIED` 的收据壳：provenance 节的强度断言以「收据已核验」为前提，
    schema/status/version 三项之一不对，它连锚都不看（见 `_provenance_section` 的分支 ②）。
    """
    br = {"schema": VBR.SCHEMA, "schema_version": schema_version,
          "status": "VERIFIED", "source": source, "build": dict(_BUILD)}
    if degradations is not None:
        br["degradations"] = degradations
    br.update(build_receipt_overrides)
    return {"vendor": {"build_receipt": br}}


def _pr_source(**kw):
    """v1 老收据形态的 PR 来源（**不得**带 `provenance_kind` / `declared_source_form`）。"""
    source = {"repo": "cann/ops-nn", "pr_head_sha": PR_HEAD}
    source.update(kw)
    return source


def _snapshot_source(declared_source_form=VBR.FORM_LOCAL_SOURCE,
                     subtree=SUBTREE_DIGEST, scope="op", **kw):
    """v2 本地快照来源。`pr_head_sha` **显式 null** —— 本形态没有上游 commit。"""
    source = {
        "provenance_kind": VBR.PROVENANCE_LOCAL_SNAPSHOT,
        VBR.DECLARED_FORM_KEY: declared_source_form,
        "pr_head_sha": None,
        "repo": "cann/ops-nn",
        "snapshot_subtree_scope": scope,
        "snapshot_sha256": WHOLE_DIGEST,
        "snapshot_subtree_sha256": subtree,
    }
    source.update(kw)
    return source


def _snapshot_receipt(**kw):
    """声明即所得的本地快照收据（无降级）。"""
    return _receipt(_snapshot_source(**kw),
                    schema_version=VBR.SCHEMA_VERSION_IDENTITY_ROUTED, degradations=[])


def _docs(receipt):
    """一份「全绿、无失败明细」的最小产物集；只有 evidence 的收据随用例变。"""
    return {
        "acceptance.json": {
            "op": "X", "overall": "PASS", "state": "PASSED",
            "artifact_contract": "historical_read_only",
            "execution_identity": {"public_op": "X", "kernel_op_type": "InternalX",
                                   "resolution": "explicit_spec_binding"},
            "precision_verdict": "pass", "perf_status": "ok",
            "repo_mode": "cpp_extension", "gate": {"passed": True, "errors": {}},
        },
        "verdict.json": {
            "op": "X", "standard": "s",
            "accuracy_summary": {"total": 1, "passed": 1, "failed": 0,
                                 "errored": 0, "uncertain": 0, "na": 0,
                                 "overall_pass_rate": 1.0,
                                 "by_dtype": [{"dtype": "float32", "count": 1,
                                               "passed": 1, "failed": 0,
                                               "errored": 0, "uncertain": 0,
                                               "na": 0}],
                                 "report": {
                                     "overall": {"total": 1, "passed": 1,
                                                 "failed": 0, "needs_review": 0,
                                                 "na": 0},
                                     "by_dtype": [{"dtype": "float32", "total": 1,
                                                   "passed": 1, "failed": 0,
                                                   "needs_review": 0, "na": 0}]}},
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

    ⚠ 夹具必须走 `make_artifact` 而不是手拼 `{"payload": …}`：
    `source_facts_lookup.find_source_facts` 会复算 digest，手拼信封没有 digest 就会被判
    UNTRUSTED——那样这些用例测的其实是「读不出」分支，看着绿、覆盖的却不是它们声称覆盖的路径。
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


def _snapshot_facts(snapshot_merkle=SUBTREE_DIGEST, snapshot_scope="op",
                    completeness=None):
    """⚠ 用共享的**完整契约** payload：渲染器与三级门共用
    `source_facts_lookup.find_source_facts`，而它会拿
    `validate_preparation_state._validate_source_payload` 校这份对照物。
    只塞一个摘要的最小 payload 会被判 UNTRUSTED，用例就测不到它想测的分支。
    """
    from test_validate_cpp_extension_receipt import source_facts_payload
    return source_facts_payload(
        provenance_kind="local_snapshot", snapshot_merkle=snapshot_merkle,
        snapshot_scope=snapshot_scope, completeness=completeness)


class RenderAcceptanceMarkdownTest(unittest.TestCase):
    def _current_docs(self, root):
        vendor_path = os.path.join(
            root, "vendors", "fixture", "op_api", "lib", "libcust_opapi.so")
        build_receipt = F.vendor_build_receipt(
            vendor_path, "a" * 64, scope="op", subtree=SUBTREE_DIGEST,
            anchor=F.content_anchor("op", SUBTREE_DIGEST))
        facts = _snapshot_facts()
        fact = facts["derived"]["kernel_identity"]
        spec = {"op": "X", "runner_form": "cpp_extension",
                "execution": K.spec_execution(fact)}
        docs = _docs({"vendor": {"build_receipt": build_receipt}})
        docs["acceptance.json"].pop("artifact_contract")
        docs["acceptance.json"]["execution_identity"] = K.resolve(spec, facts)
        docs["spec.json"] = spec
        return docs, facts

    def test_orphan_pre_execution_payload_blocks_renderer(self):
        with tempfile.TemporaryDirectory() as root:
            _write_docs(root, _docs(_receipt(_pr_source())))
            with open(os.path.join(root, "vendor_build_attempt.json"),
                      "w", encoding="utf-8") as out:
                out.write("{}")
            with self.assertRaisesRegex(R.acceptance_artifacts.FormalAcceptanceError,
                                        "incomplete"):
                R.write_report(root, allow_historical_read_only=True)
            self.assertFalse(os.path.lexists(os.path.join(root, "验收报告.md")))

    def test_renderer_lock_covers_terminal_check_through_physical_write(self):
        with tempfile.TemporaryDirectory() as root:
            _write_docs(root, _docs(_receipt(_pr_source())))
            rendering = threading.Event()
            release = threading.Event()
            terminal_entered = threading.Event()
            errors = []
            real_render = R._render_locked

            def paused_render(*args, **kwargs):
                rendering.set()
                release.wait(3)
                return real_render(*args, **kwargs)

            def write_report():
                try:
                    R.write_report(root, allow_historical_read_only=True)
                except Exception as ex:  # pragma: no cover - 断言队列负责暴露
                    errors.append(ex)

            def commit_terminal_payload():
                with R.acceptance_artifacts.artifact_transaction(root):
                    terminal_entered.set()
                    with open(os.path.join(root, "vendor_build_attempt.json"),
                              "w", encoding="utf-8") as out:
                        out.write("{}")

            with mock.patch.object(R, "_render_locked", side_effect=paused_render):
                renderer = threading.Thread(target=write_report)
                terminal = threading.Thread(target=commit_terminal_payload)
                renderer.start()
                self.assertTrue(rendering.wait(2))
                terminal.start()
                self.assertFalse(terminal_entered.wait(0.2),
                                 "terminal finalizer 不得插入 renderer check→write 窗口")
                release.set()
                renderer.join(3); terminal.join(3)
            self.assertEqual(errors, [])
            self.assertTrue(os.path.isfile(os.path.join(
                root, R.HISTORICAL_REPORT_FILENAME)))
            self.assertTrue(terminal_entered.is_set())

    def test_renderer_root_swap_cannot_redirect_final_markdown_write(self):
        with tempfile.TemporaryDirectory() as parent:
            root = os.path.join(parent, "report")
            os.mkdir(root)
            _write_docs(root, _docs(_receipt(_pr_source())))
            rendering = threading.Event()
            release = threading.Event()
            errors = []
            real_render = R._render_locked

            def paused_render(*args, **kwargs):
                rendering.set()
                release.wait(3)
                return real_render(*args, **kwargs)

            def write_report():
                try:
                    R.write_report(root, allow_historical_read_only=True)
                except Exception as ex:
                    errors.append(ex)

            with mock.patch.object(R, "_render_locked", side_effect=paused_render):
                renderer = threading.Thread(target=write_report)
                renderer.start()
                self.assertTrue(rendering.wait(2))
                displaced = root + ".displaced"
                os.rename(root, displaced)
                os.mkdir(root)
                release.set()
                renderer.join(3)
            self.assertEqual(len(errors), 1)
            self.assertRegex(str(errors[0]), "替换")
            self.assertFalse(os.path.lexists(os.path.join(
                root, R.HISTORICAL_REPORT_FILENAME)))

    def test_renderer_rejects_work_source_facts_symlink_to_external_valid_file(self):
        with tempfile.TemporaryDirectory() as parent:
            root = os.path.join(parent, "report")
            external = os.path.join(parent, "external")
            os.mkdir(root)
            os.mkdir(external)
            docs, facts = self._current_docs(root)
            _write_docs(root, docs)
            _write_docs(external, {}, source_facts=facts)
            os.symlink(external, os.path.join(root, "work"))
            with self.assertRaisesRegex(
                    R.acceptance_artifacts.FormalAcceptanceError,
                    "source_facts|符号链接|no-follow|可信"):
                R.render(root)

    def test_external_explicit_source_facts_symlink_swap_never_renders(self):
        with tempfile.TemporaryDirectory() as parent:
            root = os.path.join(parent, "report")
            os.mkdir(root)
            docs, facts = self._current_docs(root)
            _write_docs(root, docs)
            valid = os.path.join(parent, "valid-facts.json")
            _write_docs(parent, {}, source_facts=facts)
            os.replace(os.path.join(parent, "source_facts.json"), valid)
            candidate = os.path.join(parent, "candidate.json")
            with open(candidate, "w", encoding="utf-8") as out:
                out.write("{}")
            stop = threading.Event()

            def swap_candidate():
                index = 0
                while not stop.is_set():
                    alias = os.path.join(parent, f"alias-{index}")
                    os.symlink(valid, alias)
                    os.replace(alias, candidate)
                    regular = os.path.join(parent, f"regular-{index}")
                    with open(regular, "w", encoding="utf-8") as out:
                        out.write("{}")
                    os.replace(regular, candidate)
                    index += 1

            swapper = threading.Thread(target=swap_candidate)
            swapper.start()
            accepted = False
            try:
                for _ in range(1500):
                    try:
                        R.render(root, source_facts_path=candidate)
                    except (OSError, R.acceptance_artifacts.FormalAcceptanceError,
                            R.acceptance_artifacts.ArtifactNameConflictError):
                        continue
                    accepted = True
                    break
            finally:
                stop.set()
                swapper.join(3)
            self.assertFalse(
                accepted, "外部 explicit facts 换成 symlink 的竞态不得成功渲染")

    def test_renders_public_and_internal_operator_identities_separately(self):
        with tempfile.TemporaryDirectory() as root:
            _write_docs(root, _docs(_snapshot_receipt()))
            text = R.render(root, allow_historical_read_only=True)
        self.assertIn("| 公开任务/API 身份 | `X` |", text)
        self.assertIn("| 内部 kernel op type | `InternalX` |", text)

    def test_current_renderer_rejects_missing_source_kernel_identity(self):
        with tempfile.TemporaryDirectory() as root:
            docs, facts = self._current_docs(root)
            del facts["derived"]["kernel_identity"]
            _write_docs(root, docs, source_facts=facts)
            with self.assertRaisesRegex(RuntimeError, "kernel identity"):
                R.render(root)

    def test_current_renderer_rejects_acceptance_identity_drift(self):
        with tempfile.TemporaryDirectory() as root:
            docs, facts = self._current_docs(root)
            docs["acceptance.json"]["execution_identity"]["kernel_op_type"] = "Forged"
            _write_docs(root, docs, source_facts=facts)
            with self.assertRaisesRegex(RuntimeError, "execution_identity"):
                R.render(root)

    def test_current_renderer_accepts_exact_source_and_build_anchor(self):
        with tempfile.TemporaryDirectory() as root:
            docs, facts = self._current_docs(root)
            _write_docs(root, docs, source_facts=facts)
            text = R.render(root)
        self.assertIn("# X 算子验收报告", text)
        self.assertNotIn("历史验收报告", text)

    def test_historical_flag_does_not_rename_a_current_report(self):
        with tempfile.TemporaryDirectory() as root:
            docs, facts = self._current_docs(root)
            _write_docs(root, docs, source_facts=facts)
            path = R.write_report(root, allow_historical_read_only=True)
        self.assertEqual("验收报告.md", os.path.basename(path))

    def test_current_renderer_rejects_coherently_rewritten_build_anchors(self):
        """source + snapshot digest 一起改，也不能逃过 CP-A facts 对账。"""
        with tempfile.TemporaryDirectory() as root:
            docs, facts = self._current_docs(root)
            br = docs["evidence.json"]["cpp_extension_receipt"]["vendor"][
                "build_receipt"]
            forged = F.content_anchor("other-scope", OTHER_DIGEST)
            br["source"]["content_anchor"] = copy.deepcopy(forged)
            br["build"]["source_snapshot_digest"]["content_anchor"] = copy.deepcopy(
                forged)
            _write_docs(root, docs, source_facts=facts)
            with self.assertRaisesRegex(RuntimeError, "content_anchor"):
                R.render(root)

    def test_current_renderer_rejects_source_and_snapshot_anchor_drift(self):
        """receipt source 与 build 前 snapshot 也必须彼此逐字一致。"""
        with tempfile.TemporaryDirectory() as root:
            docs, facts = self._current_docs(root)
            br = docs["evidence.json"]["cpp_extension_receipt"]["vendor"][
                "build_receipt"]
            br["build"]["source_snapshot_digest"]["content_anchor"] = \
                F.content_anchor("other-scope", OTHER_DIGEST)
            _write_docs(root, docs, source_facts=facts)
            with self.assertRaisesRegex(RuntimeError, "content_anchor"):
                R.render(root)

    def test_current_v3_receipt_rejects_missing_facts_even_in_historical_mode(self):
        with tempfile.TemporaryDirectory() as root:
            docs, _facts = self._current_docs(root)
            _write_docs(root, docs)
            with self.assertRaisesRegex(RuntimeError, "source_facts"):
                R.render(root, allow_historical_read_only=True)

    def test_current_v3_receipt_rejects_untrusted_facts(self):
        with tempfile.TemporaryDirectory() as root:
            docs, _facts = self._current_docs(root)
            _write_docs(root, docs, source_facts_raw="{not-json")
            with self.assertRaisesRegex(RuntimeError, "source_facts"):
                R.render(root, allow_historical_read_only=True)

    def test_tampered_caller_marker_cannot_downgrade_to_historical(self):
        with tempfile.TemporaryDirectory() as root:
            docs = _docs(_snapshot_receipt())
            _write_docs(root, docs, source_facts=_snapshot_facts())
            path = os.path.join(root, "source_facts.json")
            with open(path, encoding="utf-8") as src:
                envelope = json.load(src)
            envelope["digest"] = "0" * 64
            with open(path, "w", encoding="utf-8") as out:
                json.dump(envelope, out)
            with self.assertRaisesRegex(RuntimeError, "current.*source_facts"):
                R.render(root, allow_historical_read_only=True)

    def test_caller_trusted_marker_rejects_legacy_receipt_even_in_historical_mode(self):
        with tempfile.TemporaryDirectory() as root:
            facts = _snapshot_facts()
            fact = facts["derived"]["kernel_identity"]
            spec = {"op": "X", "runner_form": "cpp_extension",
                    "execution": K.spec_execution(fact)}
            docs = _docs(_snapshot_receipt())
            docs["acceptance.json"].pop("artifact_contract")
            docs["acceptance.json"]["execution_identity"] = K.resolve(spec, facts)
            docs["spec.json"] = spec
            br = docs["evidence.json"]["cpp_extension_receipt"]["vendor"][
                "build_receipt"]
            br.setdefault("build", {}).setdefault(
                "source_snapshot_digest", {})["kernel_identity"] = fact
            br[VBR.TARGET_KERNEL_DELIVERY_KEY] = {
                "request": {"expected_op_type": "X"}}
            _write_docs(root, docs, source_facts=facts)
            with self.assertRaisesRegex(RuntimeError, "current.*receipt|receipt.*current"):
                R.render(root, allow_historical_read_only=True)

    def test_current_acceptance_binding_rejects_legacy_inputs_in_historical_mode(self):
        with tempfile.TemporaryDirectory() as root:
            docs = _docs(_snapshot_receipt())
            docs["acceptance.json"].pop("artifact_contract")
            docs["acceptance.json"]["execution_identity"]["source_binding"] = {
                "schema": K.SPEC_BINDING_SCHEMA,
                "schema_version": K.SCHEMA_VERSION,
                "identity_sha256": "a" * 64,
                "candidate": {},
            }
            _write_docs(root, docs)
            with self.assertRaisesRegex(RuntimeError, "current"):
                R.render(root, allow_historical_read_only=True)

    def test_malformed_acceptance_binding_marker_cannot_downgrade(self):
        with tempfile.TemporaryDirectory() as root:
            docs = _docs(_snapshot_receipt())
            docs["acceptance.json"].pop("artifact_contract")
            docs["acceptance.json"]["execution_identity"]["source_binding"] = {
                "schema": "tampered"}
            _write_docs(root, docs)
            with self.assertRaisesRegex(RuntimeError, "current"):
                R.render(root, allow_historical_read_only=True)

    def test_current_acceptance_marker_cannot_fall_back_when_receipt_is_removed(self):
        with tempfile.TemporaryDirectory() as root:
            docs, facts = self._current_docs(root)
            docs["evidence.json"]["cpp_extension_receipt"] = {}
            _write_docs(root, docs, source_facts=facts)
            with self.assertRaisesRegex(RuntimeError, "current|receipt|binding"):
                R.render(root)

    def test_caller_trusted_facts_prevent_coherent_identity_downgrade(self):
        with tempfile.TemporaryDirectory() as root:
            docs, facts = self._current_docs(root)
            docs["evidence.json"]["cpp_extension_receipt"]["vendor"][
                "build_receipt"]["schema_version"] = 2
            docs["acceptance.json"].pop("execution_identity")
            docs["acceptance.json"]["artifact_contract"] = "historical_read_only"
            facts["derived"].pop("kernel_identity")
            _write_docs(root, docs, source_facts=facts)
            with self.assertRaisesRegex(RuntimeError, "current|receipt|binding"):
                R.render(root)

    def test_fully_coherent_downgrade_cannot_self_authorize_historical_mode(self):
        with tempfile.TemporaryDirectory() as root:
            docs, _ = self._current_docs(root)
            docs["evidence.json"]["cpp_extension_receipt"]["vendor"][
                "build_receipt"]["schema_version"] = 2
            docs["acceptance.json"].pop("execution_identity")
            docs["acceptance.json"]["artifact_contract"] = "historical_read_only"
            docs.pop("spec.json")
            _write_docs(root, docs)
            with self.assertRaisesRegex(RuntimeError, "调用方必须显式"):
                R.render(root)

    def test_explicit_historical_mode_uses_distinct_filename_and_banner(self):
        with tempfile.TemporaryDirectory() as root:
            _write_docs(root, _docs(_snapshot_receipt()))
            path = R.write_report(root, allow_historical_read_only=True)
            self.assertEqual(os.path.basename(path), R.HISTORICAL_REPORT_FILENAME)
            self.assertFalse(os.path.exists(os.path.join(root, "验收报告.md")))
            with open(path, encoding="utf-8") as src:
                text = src.read()
            self.assertIn("历史验收报告（只读）", text)
            self.assertIn("不是 current 正式验收报告", text)

    def test_renders_runtime_cann_requirement_probe_and_scope(self):
        observation = {
            "status": "measured",
            "raw": "v8.5.1-rc1",
            "normalized": "8.5.1",
            "core": [8, 5, 1],
            "suffix": "-rc1",
            "probe": {
                "api": "aclsysGetCANNVersion",
                "package": "ACL_PKG_NAME_CANN",
                "returncode": 0,
                "returncode_source": "measured",
                "defining_elf": {"path": "/opt/cann/libascendcl.so", "sha256": "d" * 64},
            },
        }
        receipt = _receipt(_pr_source())
        receipt["runtime"] = {"cann_version": "8.5.1", "cann": observation}
        spec = {
            "runtime_requirements": {
                "cann": {
                    "kind": "minimum",
                    "minimum_version": "8.5.0",
                    "cite": "task_doc.md:1",
                    "quote": "CANN 8.5.0 及以上",
                    "taskdoc_snapshot_sha256": "e" * 64,
                }
            }
        }
        with tempfile.TemporaryDirectory() as root:
            _write_docs(root, _docs(receipt))
            with open(os.path.join(root, "spec.json"), "w", encoding="utf-8") as out:
                json.dump(spec, out)
            text = R.render(root, allow_historical_read_only=True)

        self.assertIn("| CANN runtime 原始值 | `v8.5.1-rc1` |", text)
        self.assertIn("| CANN runtime 规范化 | `8.5.1` |", text)
        self.assertIn("| 任务书最低 CANN runtime | `8.5.0` |", text)
        self.assertIn("| CANN runtime 版本门 | `satisfied` |", text)
        self.assertIn("aclsysGetCANNVersion(ACL_PKG_NAME_CANN), rc=0/measured", text)
        self.assertIn("/opt/cann/libascendcl.so sha256=" + "d" * 64, text)
        self.assertIn("仅证明本进程实际调用的 runtime；不证明 vendor build-time CANN", text)

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
                    "artifact_contract": "historical_read_only",
                    "precision_verdict": "fail", "perf_status": "skipped_precision_gate",
                    "repo_mode": "cpp_extension", "gate": {"passed": True, "errors": {}},
                },
                "verdict.json": {
                    "op": "X", "standard": "ascendoptest_default",
                    "accuracy_summary": {
                        "total": 2, "passed": 1, "failed": 1,
                        "overall_pass_rate": 0.5,
                        "by_dtype": [{"dtype": "float32", "count": 2, "passed": 1,
                                      "failed": 1, "uncertain": 0, "pass_rate": 0.5}],
                        "report": {
                            "overall": {"total": 2, "passed": 1, "failed": 1,
                                        "needs_review": 0, "na": 0},
                            "by_dtype": [{"dtype": "float32", "total": 2,
                                          "passed": 1, "failed": 1,
                                          "needs_review": 0, "na": 0}],
                        },
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
            path = R.write_report(root, allow_historical_read_only=True)
            with open(path, encoding="utf-8") as src:
                text = src.read()
            self.assertIn("# X 算子历史验收报告（只读）", text)
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
            self.assertIn("./repro/show_case.sh b", detail_text)
            self.assertIn("./repro/run_case.sh b", detail_text)
            self.assertIn("`b`", detail_text)
            self.assertFalse(os.path.exists(os.path.join(root, "性能失败明细.md")))

    def test_precision_report_uses_canonical_report_view_and_separates_na(self):
        """renderer 只消费 validator 的 report 投影，不把 raw NA 全叫成失败。"""
        with tempfile.TemporaryDirectory() as root:
            per_case = [
                {"case_id": f"pass-{i}", "功能": "pass", "精度": "pass", "判据": "ok"}
                for i in range(33)
            ] + [
                {"case_id": f"fail-{i}", "功能": "fail", "精度": "na",
                 "判据": "execution failed"}
                for i in range(71)
            ] + [
                {"case_id": f"na-{i}", "功能": "pass", "精度": "na",
                 "判据": "expected exception has no numeric axis"}
                for i in range(25)
            ]
            docs = _docs({})
            docs["acceptance.json"].update({
                "overall": "FAIL(精度)", "state": "FAILED_PRECISION",
                "precision_verdict": "fail", "perf_status": "skipped_precision_gate",
            })
            docs["verdict.json"] = {
                "op": "X", "standard": "ascendoptest_default",
                # raw 视图故意保留正式现场的口径差异；报告不得读它。
                "accuracy_summary": {
                    "total": 129, "passed": 33, "failed": 0, "errored": 71,
                    "uncertain": 0, "na": 25, "overall_pass_rate": 33 / 129,
                    "by_dtype": [{"dtype": "WRONG_RAW", "count": 129, "passed": 33,
                                  "failed": 0, "uncertain": 0, "na": 25,
                                  "pass_rate": 33 / 129}],
                    "report": {
                        "overall": {"total": 104, "passed": 33, "failed": 71,
                                    "needs_review": 0, "na": 25},
                        "by_dtype": [
                            {"dtype": "int32", "total": 52, "passed": 17,
                             "failed": 35, "needs_review": 0, "na": 13},
                            {"dtype": "int64", "total": 52, "passed": 16,
                             "failed": 36, "needs_review": 0, "na": 12},
                        ],
                    },
                },
                "overall": {"counts": {"total": 129, "fail": 71}},
                "per_case": per_case,
            }
            docs["perf_report.json"] = {
                "summary": {"status": "skipped_precision_gate", "planned_cases": 0,
                            "perf_cases": 0, "cases_scored": 0, "达标": 0},
                "by_shape_class": [], "non_passing_cases": [],
            }
            _write_docs(root, docs)

            path = R.write_report(root, allow_historical_read_only=True)
            with open(path, encoding="utf-8") as src:
                text = src.read()
            self.assertIn(
                "全量：129 条；可判：104 条（通过 33，失败 71，待复核 0）；NA：25 条。",
                text)
            self.assertIn("| `int32` | 52 | 17 | 35 | 0 | 13 |", text)
            self.assertIn("| `int64` | 52 | 16 | 36 | 0 | 12 |", text)
            self.assertNotIn("WRONG_RAW", text)
            self.assertNotIn("33/129 通过", text)
            self.assertIn("## 精度失败/待复核/NA 明细", text)
            self.assertIn("失败 **71** 条；待复核 **0** 条；NA **25** 条", text)

            with open(os.path.join(root, "精度失败明细.md"), encoding="utf-8") as src:
                detail = src.read()
            self.assertIn("# 精度失败/待复核/NA 明细", detail)
            self.assertIn("失败：**71**；待复核：**0**；NA：**25**", detail)
            self.assertNotIn("失败总数：**96**", detail)
            na_line = next(line for line in detail.splitlines() if "`na-0`" in line)
            self.assertIn("./repro/show_case.sh na-0", na_line)
            self.assertIn("./repro/run_case.sh na-0", na_line)
            self.assertNotIn("audit_case.sh", na_line)

    def test_precision_report_projection_is_required_and_self_consistent(self):
        mutations = {
            "missing report": lambda summary: summary.pop("report"),
            "wrong report type": lambda summary: summary.__setitem__("report", []),
            "drifted overall": lambda summary: summary["report"]["overall"].__setitem__(
                "failed", 1),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as root:
                docs = _docs(_receipt(_pr_source()))
                mutate(docs["verdict.json"]["accuracy_summary"])
                _write_docs(root, docs)
                with self.assertRaisesRegex(
                        R.acceptance_artifacts.FormalAcceptanceError,
                        "accuracy_summary.report|精度报表"):
                    R.write_report(root, allow_historical_read_only=True)
                self.assertFalse(os.path.lexists(os.path.join(root, "验收报告.md")))

    def test_refuses_to_render_gate_failed_or_blocked_acceptance_as_formal_report(self):
        """renderer 是可单独调用的入口，不能绕过 workflow 的正式发布门。"""
        candidates = (
            {
                "op": "X", "overall": "BLOCKED(验收门未过)",
                "state": "BLOCKED_EVIDENCE_INCOMPLETE",
                "gate": {"passed": False, "errors": {"task2": ["missing"]}},
            },
            {
                "op": "X", "overall": "BLOCKED_WAIT_GPU_BENCHMARK",
                "state": "BLOCKED_WAIT_GPU_BENCHMARK",
                "gate": {"passed": True, "errors": {}},
            },
            {
                "op": "X", "overall": "NEEDS_REVIEW", "state": "NEEDS_REVIEW",
                "gate": {"passed": True, "errors": {}},
            },
            {
                "op": "X", "overall": "PASS", "state": "PASSED",
                "gate": {"passed": True, "errors": {"task2": ["missing"]}},
            },
            {
                "op": "X", "overall": "BLOCKED(验收门未过)", "state": "PASSED",
                "gate": {"passed": True, "errors": {}},
            },
            {
                "op": "X", "overall": "PASS", "state": "FAILED_PRECISION",
                "gate": {"passed": True, "errors": {}},
            },
            {
                "op": "X", "overall": "NOT_A_REAL_OVERALL", "state": "PASSED",
                "gate": {"passed": True, "errors": {}},
            },
        )
        for acceptance in candidates:
            with self.subTest(state=acceptance["state"]), tempfile.TemporaryDirectory() as root:
                with open(os.path.join(root, "acceptance.json"), "w", encoding="utf-8") as out:
                    json.dump(acceptance, out)
                with self.assertRaisesRegex(RuntimeError, r"正式验收"):
                    R.render(root)
                with self.assertRaisesRegex(RuntimeError, r"正式验收"):
                    R.write_report(root)
                for name in ("验收报告.md", "精度失败明细.md", "性能失败明细.md"):
                    self.assertFalse(os.path.exists(os.path.join(root, name)), name)

    def test_splits_performance_non_passing_detail(self):
        with tempfile.TemporaryDirectory() as root:
            docs = {
                "acceptance.json": {
                    "op": "X", "overall": "性能未达成(failed)", "state": "FAILED_PERFORMANCE",
                    "artifact_contract": "historical_read_only",
                    "precision_verdict": "pass", "perf_status": "failed",
                    "repo_mode": "cpp_extension", "gate": {"passed": True, "errors": {}},
                },
                "verdict.json": {
                    "op": "X", "standard": "s",
                    "accuracy_summary": {"total": 1, "passed": 1, "failed": 0,
                                         "overall_pass_rate": 1.0, "by_dtype": [],
                                         "report": {
                                             "overall": {"total": 1, "passed": 1,
                                                         "failed": 0,
                                                         "needs_review": 0, "na": 0},
                                             "by_dtype": [{"dtype": "float16", "total": 1,
                                                           "passed": 1, "failed": 0,
                                                           "needs_review": 0, "na": 0}]}},
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
            path = R.write_report(root, allow_historical_read_only=True)
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
    """「## 来源与 provenance」节：强度如实标注，「没有对照物」绝不升级为「已核」。"""

    def _render(self, receipt, source_facts=None, source_facts_raw=None):
        with tempfile.TemporaryDirectory() as root:
            docs = _docs(receipt)
            if (isinstance(source_facts, dict)
                    and (source_facts.get("completeness") or {}).get("status")
                    == "complete"):
                fact = (source_facts.get("derived") or {}).get("kernel_identity")
                try:
                    candidate = K.validate(fact) if isinstance(fact, dict) else None
                except K.KernelIdentityError:
                    candidate = None
                if candidate is not None:
                    spec = {"op": "X", "runner_form": "cpp_extension",
                            "execution": K.spec_execution(fact)}
                    docs["acceptance.json"].pop("artifact_contract")
                    docs["acceptance.json"]["execution_identity"] = K.resolve(
                        spec, source_facts, require_explicit=True)
                    docs["spec.json"] = spec
                    br = docs["evidence.json"]["cpp_extension_receipt"]["vendor"][
                        "build_receipt"]
                    br.setdefault("build", {}).setdefault(
                        "source_snapshot_digest", {})["kernel_identity"] = fact
                    br[VBR.TARGET_KERNEL_DELIVERY_KEY] = {
                        "request": {
                            "expected_op_type": candidate["kernel_op_type"]}}
            _write_docs(root, docs,
                        source_facts=source_facts, source_facts_raw=source_facts_raw)
            path = R.write_report(
                root, allow_historical_read_only=("spec.json" not in docs))
            self.assertTrue(os.path.isfile(path))
            with open(path, encoding="utf-8") as src:
                return src.read()

    def test_pull_request_receipt_renders_pr_head_in_own_section(self):
        text = self._render(_receipt(_pr_source()))
        self.assertIn(R.PROV_HEADING, text)
        self.assertIn(f"| PR head | `{PR_HEAD}` |", text)
        # 这份收据没有 `repo_source`（老收据形态）→ 源码仓那一行必须标「强度未知」。
        self.assertIn(R.PROV_REPO_ROW.format(
            repo=R._code_cell("cann/ops-nn"), strength=R.PROV_REPO_SOURCE_ABSENT), text)
        # v1 老收据没有 `declared_source_form` → 声明形态必须写「未声明」，
        # 不得被洗成 `git_pr`（那是替收据编一条它没说过的话）。
        self.assertIn(R.PROV_FORM_ROW.format(value=R.PROV_FORM_UNDECLARED), text)
        # provenance 节排在运行环境之前，且那两行确实从旧表里迁走了。
        self.assertLess(text.index(R.PROV_HEADING), text.index("## 被测物与运行环境"))
        env = text.split("## 被测物与运行环境", 1)[1]
        self.assertNotIn("| PR head |", env)
        self.assertNotIn("| 源码仓 |", env)
        self.assertIn("| SoC |", env)
        # PR 通路不该沾上本地通路的 caveat，也不该出现子树摘要的覆盖范围行。
        for caveat in R.PROV_LOCAL_CAVEATS:
            self.assertNotIn(caveat, text)
        self.assertNotIn("| 摘要覆盖范围 |", text)

    def test_local_snapshot_receipt_renders_subtree_digest_and_scope(self):
        """⭐ 本地快照通路：锚是子树摘要，且**必须**同时给出覆盖范围。

        只印一个 64 位 hex 会让读的人以为它覆盖了整份被测代码——而它只覆盖
        `snapshot_subtree_scope` 那一段。
        """
        text = self._render(_snapshot_receipt())
        self.assertIn(f"| 子树摘要 snapshot_subtree_sha256 | `{SUBTREE_DIGEST}` |", text)
        self.assertIn(R.PROV_SCOPE_ROW.format(scope=R._code_cell("op")), text)
        self.assertIn(R.PROV_FORM_ROW.format(
            value=R.PROV_FORM_LABEL[VBR.FORM_LOCAL_SOURCE]), text)
        # 声明即所得 = 无降级：不得凭空挂一条降级账。
        self.assertNotIn("| 降级挂账 |", text)
        # caveat 只依赖 kind：没有 source_facts 也必须一条不少。
        for caveat in R.PROV_LOCAL_CAVEATS:
            self.assertIn(caveat, text)
        # PR 通路的锚行不该出现（本形态压根没有 PR head，硬渲染一个「—」会读成「这次没记」）。
        self.assertNotIn("| PR head |", text)

    def test_whole_tree_scope_is_spelled_out_not_rendered_as_empty(self):
        """⭐ `snapshot_subtree_scope` 是空串（= 整棵树）时必须**明说**。

        渲染成一个空格子的话，读起来像「这次没记范围」，而它其实是一个合法的显式值。
        """
        text = self._render(_snapshot_receipt(scope=""))
        self.assertIn(R.PROV_SCOPE_ROW.format(scope=R.PROV_SCOPE_WHOLE_TREE), text)
        self.assertNotIn("| 摘要覆盖范围 |  |", text)

    def test_degraded_snapshot_receipt_shows_the_degradation_on_the_first_layer(self):
        """⭐ 「本来要测 PR、只拿到一份快照」必须在报告第一层看得见。

        它与「本轮本来就是本地源码」在锚上长得一模一样（都只有子树摘要），
        分得开两者的只有 `declared_source_form` + 降级台账这两行。
        """
        text = self._render(_receipt(
            _snapshot_source(declared_source_form=VBR.FORM_GIT_PR),
            schema_version=VBR.SCHEMA_VERSION_IDENTITY_ROUTED,
            degradations=[VBR.DEGRADATION_PR_HEAD_UNBOUND]))
        self.assertIn(R.PROV_DEGRADATION_ROW.format(
            items=VBR.DEGRADATION_PR_HEAD_UNBOUND), text)
        self.assertIn(R.PROV_FORM_ROW.format(
            value=R.PROV_FORM_LABEL[VBR.FORM_GIT_PR]), text)
        self.assertIn("本该绑上游 commit 却没绑", text)

    def test_local_receipt_without_source_facts_never_claims_corroboration(self):
        """⭐ 本节最贵的一条：**「没有对照物」= 未经印证，不是「已核」**。"""
        text = self._render(_snapshot_receipt())
        self.assertIn(R.PROV_FACTS_ABSENT, text)
        self.assertNotIn(R.PROV_FACTS_FOUND, text)
        self.assertIn("未经第二方印证", text)

    def test_matching_source_facts_is_reported_as_found_but_never_quoted(self):
        """⭐ 有对照物只报「找到了」，**一个字段值都不引用**。

        一份来源对不上的 facts，它里面的字段描述的是**另一份取材**。只要渲染器一个值
        都不取，「把无关事实冒充本轮 provenance」这一整类缺陷就在结构上不存在；
        锚是否逐字一致由三级门裁定，本节不重判。
        """
        with tempfile.TemporaryDirectory() as root:
            docs, facts = RenderAcceptanceMarkdownTest()._current_docs(root)
            _write_docs(root, docs, source_facts=facts)
            text = R.render(root)
        self.assertIn(R.PROV_FACTS_FOUND, text)
        self.assertNotIn(R.PROV_FACTS_ABSENT, text)
        self.assertIn("由验收门裁定", text)
        # facts 独有的字段值一个都不许进报告。
        self.assertNotIn(FACTS_ONLY_MARKER, text)

    def test_source_facts_with_a_mismatched_anchor_is_rejected_for_current_report(self):
        """caller-trusted current facts 一旦出现，就不能退回 legacy 展示路径。"""
        with tempfile.TemporaryDirectory() as root:
            docs, facts = RenderAcceptanceMarkdownTest()._current_docs(root)
            br = docs["evidence.json"]["cpp_extension_receipt"]["vendor"][
                "build_receipt"]
            forged = F.content_anchor("OTHER-SUBTREE", OTHER_DIGEST)
            br["source"]["content_anchor"] = copy.deepcopy(forged)
            br["build"]["source_snapshot_digest"]["content_anchor"] = forged
            _write_docs(root, docs, source_facts=facts)
            with self.assertRaisesRegex(RuntimeError, "content_anchor"):
                R.render(root)

    def test_credential_repo_never_reaches_the_report(self):
        """⭐ 报告是凭据真正**泄漏出去**的那一步——它是给人看、会被转发的 .md。

        源头（`fetch_source` 扣留 remote_url）堵住的是新产的取材事实；已经躺在既有
        reports 目录里的老收据、外部构建驱动产的收据、手改的收据，全都从**读侧**进来。
        渲染器用 `url_credentials`（判别式唯一实现）当场拦下，整节退成「拒绝渲染」，
        一个 token 字节都不进 .md，**且不回显原值**。
        """
        token = "gk_LEAKED_TOKEN_9f3a"
        text = self._render(_snapshot_receipt(
            repo=f"https://bot:{token}@gitcode.com/cann/ops-nn.git",
            repo_source="snapshot.source_root"))
        self.assertNotIn(token, text, "凭据被渲染进了人读的验收报告")
        self.assertIn(R.PROV_CREDENTIAL_REPO, text)
        self.assertNotIn("| 源码仓 |", text)
        self.assertNotIn(SUBTREE_DIGEST, text, "校验没过就不该有任何 provenance 断言")
        # 报告本体照出：整节退化不能把整份 `验收报告.md` 拖没。
        self.assertIn("## 精度汇总", text)

    def test_ssh_style_remote_is_not_mistaken_for_a_credential(self):
        """判过头与判不到同样是坏门：`git@host:path` 的 `@` 前面是用户名、不含任何密钥。"""
        text = self._render(_snapshot_receipt(repo="git@gitcode.com:cann/ops-nn.git"))
        self.assertIn(R._code_cell("git@gitcode.com:cann/ops-nn.git"), text)
        self.assertNotIn(R.PROV_CREDENTIAL_REPO, text)

    def test_malformed_receipt_source_still_renders_report_without_anchor(self):
        # `repo` 缺失 → `summarize` 抛错；锚值虽在收据里，但校验没过，
        # 一个字都不该被当成 provenance 渲染出去。
        source = _snapshot_source()
        del source["repo"]
        text = self._render(_receipt(source, schema_version=VBR.SCHEMA_VERSION_IDENTITY_ROUTED,
                                     degradations=[]))
        self.assertIn("来源锚不合法", text)
        self.assertNotIn(SUBTREE_DIGEST, text)
        self.assertNotIn("| 子树摘要 snapshot_subtree_sha256 |", text)
        # 报告本体照出：异常被 catch 在节内，不能把整份 `验收报告.md` 拖没。
        self.assertIn("## 精度汇总", text)
        self.assertIn("## 被测物与运行环境", text)

    def test_synthesised_pr_head_on_a_snapshot_receipt_is_refused(self):
        """⭐ 本地快照档合成一个 40 位 hex 当 head = 捏造 PR head（AGENTS.md 5.8）。

        渲染器不自己判这一条，而是共用 `vendor_build_receipt`——判据只有一份。
        """
        text = self._render(_receipt(_snapshot_source(pr_head_sha=PR_HEAD),
                                     schema_version=VBR.SCHEMA_VERSION_IDENTITY_ROUTED,
                                     degradations=[]))
        self.assertIn("来源锚不合法", text)
        # 合成的那个 hex 只以「被拒绝的实得值」出现在报错里（那是要给人看的），
        # **绝不能**作为一条 provenance 断言被渲染成锚。
        self.assertNotIn(f"| PR head | `{PR_HEAD}` |", text)
        self.assertNotIn(SUBTREE_DIGEST, text)

    def test_unreadable_source_facts_cannot_downgrade_to_historical(self):
        with self.assertRaisesRegex(RuntimeError, "current.*source_facts"):
            self._render(_snapshot_receipt(), source_facts_raw="{ 这不是 JSON")

    def test_tampered_source_facts_envelope_is_rejected_not_downgraded(self):
        """⭐ payload 被改、digest 没跟着改 → 整份不可信且禁止降级。

        不复算 digest 的话，随手编一份最小 JSON 就能冒充一份「已过取材契约」的对照物。
        """
        with tempfile.TemporaryDirectory() as root:
            _write_docs(root, _docs(_snapshot_receipt()),
                        source_facts=_snapshot_facts())
            path = os.path.join(root, "source_facts.json")
            with open(path, encoding="utf-8") as src:
                doc = json.load(src)
            doc["payload"]["pr"]["snapshot_scope"] = "tampered"   # digest 不动
            with open(path, "w", encoding="utf-8") as out:
                json.dump(doc, out)
            with self.assertRaisesRegex(RuntimeError, "current.*source_facts"):
                R.render(root, allow_historical_read_only=True)

    def test_incomplete_current_source_facts_is_rejected_not_downgraded(self):
        """⭐ `completeness != complete` 的取材产物只供诊断，不是可采信的对照物。

        它是 fetch_source 亲手产的、digest 完全正确——只有跑完整契约才拦得住；
        既然 payload 已是 current，就不得借 historical 开关继续。
        """
        with self.assertRaisesRegex(RuntimeError, "current.*source_facts"):
            self._render(
                _snapshot_receipt(),
                source_facts=_snapshot_facts(completeness={
                    "status": "blocked", "reasons": ["missing_key_files"],
                    "form_facts": []}))

    def test_unverified_build_receipt_makes_no_provenance_claim(self):
        """⭐ 锚形态合法 ≠ 收据可信：没 VERIFIED 就不能出「可证明验的就是…」这类强度断言。"""
        for label, override in (
                ("status 非 VERIFIED", {"status": "PENDING"}),
                ("schema 不对", {"schema": "something.else"}),
                ("schema_version 不受支持", {"schema_version": 99}),
        ):
            with self.subTest(label):
                text = self._render(_receipt(_pr_source(), **override))
                self.assertIn("本节不作任何 provenance 断言", text)
                self.assertNotIn(f"| PR head | `{PR_HEAD}` |", text)
                self.assertNotIn(PR_HEAD, text)
                self.assertIn("## 精度汇总", text)      # 报告本体照出

    def test_declared_returncode_receipt_makes_no_provenance_claim(self):
        """⭐ 自报的 returncode 不是构建证据——收据整份不可信，本节就不该背书。

        这一条靠的是渲染器走 `vendor_build_receipt.summarize`（连 build 段一起校），
        而不是只看 `source` 里那几个字段。
        """
        receipt = _receipt(_pr_source())
        receipt["vendor"]["build_receipt"]["build"][VBR.RETURNCODE_SOURCE_KEY] = \
            VBR.RETURNCODE_SOURCE_DECLARED
        text = self._render(receipt)
        self.assertIn("来源锚不合法", text)
        self.assertNotIn(PR_HEAD, text)
        self.assertIn("## 精度汇总", text)


class RepoSourceStrengthTest(unittest.TestCase):
    """「源码仓」一行必须同时呈现**强度**：事实派生 / 操作者自报 / 强度未知。

    实测逮到的 fail-open：真机跑出的报告里，一个从取材事实派生的仓名与一个操作者
    `--repo` 手给的仓名**同权并列**，审核员读不出后者只是一句自报。收据记得很老实，
    是渲染层把强度吞了。

    ⚠ 当前**没有任何产出方**写 `repo_source`（`vendor_build_receipt.produce_receipt`
    不写这个键），所以正常路径上恒走「缺席 = 强度未知」那一档。这几条用例钉住的是
    「缺席绝不被洗成事实派生」，以及将来补上这个键时的归类纪律。
    """

    def _render(self, source, **kw):
        with tempfile.TemporaryDirectory() as root:
            _write_docs(root, _docs(_receipt(source, **kw)))
            return R.render(root, allow_historical_read_only=True)

    def _repo_line(self, text):
        """取「源码仓」那一行；断言它**存在且唯一**——强度被拆到别处也算吞掉了。"""
        rows = [line for line in text.splitlines() if line.startswith("| 源码仓 |")]
        self.assertEqual(len(rows), 1, text)
        return rows[0]

    # 三种已知取值各一 -------------------------------------------------------------
    def test_pr_derived_repo_says_where_it_came_from(self):
        row = self._repo_line(self._render(
            _pr_source(repo_source="pr.source_repo")))
        self.assertEqual(row, R.PROV_REPO_ROW.format(
            repo=R._code_cell("cann/ops-nn"),
            strength=R.PROV_REPO_SOURCE_LABEL["pr.source_repo"]))
        self.assertIn("事实派生", row)
        self.assertIn("`pr.source_repo`", row)

    def test_snapshot_root_derived_repo_says_where_it_came_from(self):
        row = self._repo_line(self._render(
            _snapshot_source(repo_source="snapshot.source_root"),
            schema_version=VBR.SCHEMA_VERSION_IDENTITY_ROUTED, degradations=[]))
        self.assertEqual(row, R.PROV_REPO_ROW.format(
            repo=R._code_cell("cann/ops-nn"),
            strength=R.PROV_REPO_SOURCE_LABEL["snapshot.source_root"]))
        self.assertIn("事实派生", row)
        self.assertIn("`snapshot_digest.source_root`", row)

    def test_operator_reported_repo_is_marked_as_self_reported(self):
        """⭐ 快照树里根本没有仓名证据时的真实形态：仓名是构建时手给的。"""
        row = self._repo_line(self._render(
            _snapshot_source(repo_source="operator"),
            schema_version=VBR.SCHEMA_VERSION_IDENTITY_ROUTED, degradations=[]))
        self.assertEqual(row, R.PROV_REPO_ROW.format(
            repo=R._code_cell("cann/ops-nn"),
            strength=R.PROV_REPO_SOURCE_LABEL["operator"]))
        self.assertIn("操作者自报", row)
        self.assertIn("无机器可核依据", row)
        # 自报绝不能读起来像派生。
        self.assertNotIn("事实派生", row)

    # 缺席与未知 -------------------------------------------------------------------
    def test_absent_repo_source_is_unknown_strength_not_derived(self):
        """⭐ 当前所有产出方都不写这个键：缺席 = 不知道，不是「大概是派生的」，也不是 operator。"""
        row = self._repo_line(self._render(
            _snapshot_source(), schema_version=VBR.SCHEMA_VERSION_IDENTITY_ROUTED, degradations=[]))
        self.assertEqual(row, R.PROV_REPO_ROW.format(
            repo=R._code_cell("cann/ops-nn"), strength=R.PROV_REPO_SOURCE_ABSENT))
        self.assertIn("强度未知", row)
        self.assertNotIn("事实派生", row.replace("缺席不等于事实派生", ""))
        self.assertNotIn("操作者自报", row)

    def test_unknown_repo_source_value_fails_closed_to_unknown(self):
        """⭐ 词表外取值不猜属于哪一种——静默归类等于凭空给它发一张强度证明。"""
        row = self._repo_line(self._render(
            _pr_source(repo_source="probably_the_pr_i_guess")))
        self.assertEqual(row, R.PROV_REPO_ROW.format(
            repo=R._code_cell("cann/ops-nn"), strength=R.PROV_REPO_SOURCE_UNKNOWN.format(
                value='"probably_the_pr_i_guess"')))
        self.assertIn("强度未知", row)
        self.assertNotIn("事实派生", row)
        self.assertNotIn("操作者自报", row)

    def test_null_repo_source_is_unknown_and_distinguishable_from_absent(self):
        """`repo_source: null` 是「记了个空」，与「没这个键」不同形，但同样退未知。

        ⚠ 顺带钉住一条会炸的写法：按 kind 查派生来源表时若 `.get()` 落空返回 `None`，
        两个 `None` 一撞就会走进「事实派生」分支，然后 KeyError 炸掉整节。
        """
        row = self._repo_line(self._render(_pr_source(repo_source=None)))
        self.assertEqual(row, R.PROV_REPO_ROW.format(
            repo=R._code_cell("cann/ops-nn"),
            strength=R.PROV_REPO_SOURCE_UNKNOWN.format(value="null")))
        self.assertNotEqual(R.PROV_REPO_SOURCE_UNKNOWN.format(value="null"),
                            R.PROV_REPO_SOURCE_ABSENT)

    def test_derived_origin_from_the_other_path_fails_closed(self):
        """⭐ PR 通路声称派生自本地快照树根——这种组合派生不出来，只可能来自手改收据；
        「事实派生」四个字没有出处，退未知。"""
        row = self._repo_line(self._render(
            _pr_source(repo_source="snapshot.source_root")))
        self.assertEqual(row, R.PROV_REPO_ROW.format(
            repo=R._code_cell("cann/ops-nn"), strength=R.PROV_REPO_SOURCE_MISMATCH.format(
                value='"snapshot.source_root"', kind=VBR.PROVENANCE_GIT_PR)))
        self.assertNotIn("事实派生", row)

    def test_no_repo_row_at_all_when_receipt_is_unverified(self):
        """收据不可信时整节都不作断言——那种情况下连源码仓一行都不该出现。

        没有这条，「渲染源码仓但不渲染强度」可以靠删掉整行伪装成通过。
        """
        with tempfile.TemporaryDirectory() as root:
            _write_docs(root, _docs(_receipt(
                _pr_source(repo_source="pr.source_repo"), status="PENDING")))
            text = R.render(root, allow_historical_read_only=True)
        self.assertNotIn("| 源码仓 |", text)
        self.assertIn("本节不作任何 provenance 断言", text)


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


class SourceFactsDiscoveryIsSharedTest(unittest.TestCase):
    """⭐ 钉住「渲染器和三级门用的是**同一份**来源对照物文档校验规则」。

    只把 `_find_source_facts` 改名成公开名是不够的：这条纪律要防的是**将来**有人在
    某一侧另写一份查找规则（或多加一档 fallback 路径）。那时报告陈述的对照物就不是
    门校过的那一份文件——报告说「已找到」、门校的是另一份，两边都「自洽」，谁也发现不了。

    做法：把 `source_facts_lookup.validate_source_facts_document` 换成桩，看两侧是否都观察得到。
    任一侧改成自建实现、或改成 `from source_facts_lookup import validate_source_facts_document`
    （import 时就绑死了函数对象、换桩换不掉），本用例即红。
    """

    def test_both_the_gate_and_the_renderer_share_document_validation(self):
        import source_facts_lookup
        import validate_acceptance_state as vas
        receipt = _snapshot_receipt()
        summary = VBR.summarize(receipt["vendor"]["build_receipt"])
        calls = []
        original = source_facts_lookup.validate_source_facts_document

        def stub(doc):
            calls.append(doc)
            return source_facts_lookup.SOURCE_FACTS_UNTRUSTED

        source_facts_lookup.validate_source_facts_document = stub
        try:
            with tempfile.TemporaryDirectory() as root:
                _write_docs(root, _docs(receipt), source_facts_raw="{}")
                text = R.render(root, allow_historical_read_only=True)
                self.assertEqual(
                    1, len(calls),
                    "渲染器没走 source_facts_lookup.validate_source_facts_document")

                errs = []
                vas._gate_build_receipt_source_binding(root, summary, errs)
                self.assertEqual(
                    2, len(calls),
                    "三级门没走 source_facts_lookup.validate_source_facts_document")
        finally:
            source_facts_lookup.validate_source_facts_document = original

        # 桩返回 UNTRUSTED：两侧都必须按「拿不到可对账的对照物」处置，不能当已核。
        self.assertIn(R.PROV_FACTS_ABSENT, text)
        self.assertNotIn(R.PROV_FACTS_FOUND, text)
        self.assertTrue(errs, "对照物不可信时三级门必须记 error，不能静默放行")

    def test_renderer_and_gate_share_current_source_build_binding_helper(self):
        import validate_acceptance_state as vas

        calls = []
        original = SBB.validate_current

        def recording(*args, **kwargs):
            calls.append((args, kwargs))
            return original(*args, **kwargs)

        with tempfile.TemporaryDirectory() as root, \
                mock.patch.object(SBB, "validate_current", side_effect=recording):
            docs, facts = RenderAcceptanceMarkdownTest()._current_docs(root)
            _write_docs(root, docs, source_facts=facts)
            R.render(root)
            br = docs["evidence.json"]["cpp_extension_receipt"]["vendor"][
                "build_receipt"]
            errs = []
            vas._gate_build_receipt_source_binding(
                root, VBR.summarize(br), errs, build_receipt=br)
            self.assertEqual([], errs)

        self.assertEqual(2, len(calls), "renderer 与三级门必须各调同一 helper 一次")
