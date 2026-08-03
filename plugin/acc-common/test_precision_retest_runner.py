"""CP-F Task-2-only runner 的纯文件/契约测试；不启动真实 NPU adapter。"""

import copy
import hashlib
import os
import tempfile
import unittest
from unittest import mock

import precision_retest_runner as R
import precision_retest_contract as C


class SelectedCasesetTest(unittest.TestCase):
    def test_preserves_manifest_order_and_original_case_content(self):
        base = {
            "op": "AnyOp",
            "cases": [
                {"id": "a", "inputs": [{"name": "x", "path": "a/x.npy"}]},
                {"id": "b", "inputs": [{"name": "x", "path": "b/x.npy"}]},
            ],
            "dtype_required": ["float32"],
        }
        original = copy.deepcopy(base)
        subset = R.selected_caseset(base, ["b", "a"])
        self.assertEqual([c["id"] for c in subset["cases"]], ["b", "a"])
        self.assertEqual(base, original)
        self.assertEqual(subset["precision_retest_scope"]["base_case_count"], 2)

    def test_rejects_missing_and_duplicate_ids(self):
        base = {"cases": [{"id": "a", "inputs": []}]}
        for selected in (["missing"], ["a", "a"], []):
            with self.subTest(selected=selected):
                with self.assertRaises(R.RetestExecutionError):
                    R.selected_caseset(base, selected)

    def test_runner_uses_workflow_runner_form_mapping(self):
        self.assertEqual(R.run_workflow._resolve_mode(
            {"runner_form": "cpp"}, None), "new_example")
        self.assertEqual(R.run_workflow._resolve_mode(
            {"runner_form": "aclnn_py"}, None), "aclnn_py")


class AttemptImmutabilityTest(unittest.TestCase):
    def test_completed_attempt_cannot_be_executed_again(self):
        with tempfile.TemporaryDirectory() as root:
            attempt = os.path.join(root, "0001")
            os.mkdir(attempt)
            receipt = os.path.join(attempt, "attempt.receipt.json")
            with open(receipt, "w", encoding="utf-8") as out:
                out.write("{}")
            with self.assertRaisesRegex(
                    R.RetestExecutionError, "不可变历史"):
                R.execute_precision_attempt(attempt, root)

    def test_receipt_symlink_also_blocks_execution(self):
        with tempfile.TemporaryDirectory() as root:
            attempt = os.path.join(root, "0001")
            os.mkdir(attempt)
            target = os.path.join(attempt, "target")
            with open(target, "w", encoding="utf-8") as out:
                out.write("{}")
            os.symlink(target, os.path.join(attempt, "attempt.receipt.json"))
            with self.assertRaisesRegex(
                    R.RetestExecutionError, "不可变历史"):
                R.execute_precision_attempt(attempt, root)

    def test_concurrent_execute_owner_is_blocked(self):
        with tempfile.TemporaryDirectory() as root:
            attempt = os.path.join(root, "0001")
            os.mkdir(attempt)
            with open(os.path.join(attempt, ".execute.lock"),
                      "w", encoding="utf-8") as out:
                out.write('{"status":"running"}')
            with self.assertRaisesRegex(
                    R.RetestExecutionError, "另一 execution owner"):
                R.execute_precision_attempt(attempt, root)

    def test_attempt_entry_symlink_is_rejected_before_manifest_read(self):
        with tempfile.TemporaryDirectory() as root:
            target = os.path.join(root, "real")
            os.mkdir(target)
            link = os.path.join(root, "attempt")
            os.symlink(target, link)
            with self.assertRaisesRegex(
                    R.RetestExecutionError, "本身不得为符号链接"):
                R.execute_precision_attempt(link, root)


class CopyCaseFilesTest(unittest.TestCase):
    def test_copies_inputs_and_single_multi_output_goldens(self):
        with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as target:
            paths = ("c/x.npy", "c/g.npy", "c/g0.npy", "c/g1.npy")
            for relative in paths:
                path = os.path.join(base, relative)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as out:
                    out.write(relative.encode())
            subset = {"cases": [
                {"id": "c", "inputs": [{"name": "x", "path": "c/x.npy"}],
                 "expected": {
                     "golden_path": "c/g.npy",
                     "outputs": [
                         {"golden_path": "c/g0.npy"},
                         {"golden_path": "c/g1.npy"},
                     ],
                 }},
            ]}
            copied = R.copy_selected_case_files(subset, base, target)
            self.assertEqual(set(copied), set(paths))
            for relative in paths:
                with open(os.path.join(target, relative), "rb") as src:
                    self.assertEqual(src.read(), relative.encode())

    def test_rejects_escape_symlink_and_empty_file_plan(self):
        with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as target:
            with self.assertRaises(R.RetestExecutionError):
                R.copy_selected_case_files(
                    {"cases": [{"id": "c", "inputs": [{"path": "../x"}]}]},
                    base, target)
            outside = os.path.join(target, "outside")
            with open(outside, "wb") as out:
                out.write(b"x")
            os.symlink(outside, os.path.join(base, "link"))
            with self.assertRaises(R.RetestExecutionError):
                R.copy_selected_case_files(
                    {"cases": [{"id": "c", "inputs": [{"path": "link"}]}]},
                    base, target)
            with self.assertRaises(R.RetestExecutionError):
                R.copy_selected_case_files(
                    {"cases": [{"id": "c", "inputs": []}]}, base, target)


class ExecutionCasesetPreparationTest(unittest.TestCase):
    def _fixture(self, base_work):
        case_dir = os.path.join(base_work, "c")
        os.mkdir(case_dir)
        for name, payload in (("x.npy", b"input"), ("g.npy", b"golden")):
            with open(os.path.join(case_dir, name), "wb") as out:
                out.write(payload)
        case = {
            "id": "c",
            "inputs": [{"name": "x", "dtype": "float32",
                        "path": "c/x.npy"}],
            "expected": {
                "standard": "ascendoptest_default",
                "compare_dtype": "float32",
                "compare": "rel_err",
                "golden_path": "c/g.npy",
                "policy": {"kind": "ascendoptest_default",
                           "tolerance": 0.0001, "error_rate": 0.0001},
                "acceptance_policy": {"kind": "ascendoptest_default",
                                      "tolerance": 0.0001,
                                      "error_rate": 0.0001},
            },
        }
        caseset = {"op": "AnyOp", "cases": [case]}
        manifest = {
            "planned_case_ids": ["c"],
            "case_bindings": C.build_case_bindings(
                caseset, base_work, ["c"]),
        }
        spec = {"precision": {"acceptance_policy": {
            "standard": "ascendoptest_default",
            "tolerance": 0.001,
            "error_rate": 0.005,
        }}}
        return caseset, manifest, spec

    def test_relaxed_policy_is_applied_after_original_binding_gate(self):
        with tempfile.TemporaryDirectory() as base_work, \
                tempfile.TemporaryDirectory() as attempt_work:
            caseset, manifest, spec = self._fixture(base_work)
            rebound, copied = R.prepare_execution_caseset(
                caseset, manifest, spec, "relaxed_rerun",
                base_work, attempt_work)
            self.assertEqual(copied, ["c/x.npy", "c/g.npy"])
            policy = rebound["cases"][0]["expected"]["acceptance_policy"]
            self.assertEqual(policy["tolerance"], 0.001)
            self.assertEqual(policy["error_rate"], 0.005)
            self.assertNotEqual(
                R.content_address.content_digest(
                    "oprunway/precision-retest-case/v1",
                    rebound["cases"][0]),
                manifest["case_bindings"]["c"]["case_digest"],
            )

    def test_original_case_drift_remains_blocked(self):
        with tempfile.TemporaryDirectory() as base_work, \
                tempfile.TemporaryDirectory() as attempt_work:
            caseset, manifest, spec = self._fixture(base_work)
            caseset["cases"][0]["inputs"][0]["dtype"] = "float16"
            with self.assertRaisesRegex(R.RetestExecutionError,
                                        "case 结构已漂移"):
                R.prepare_execution_caseset(
                    caseset, manifest, spec, "relaxed_rerun",
                    base_work, attempt_work)


class RelaxedPolicyRebindTest(unittest.TestCase):
    def test_single_output_rebinds_only_acceptance_fields(self):
        caseset = {"cases": [{
            "id": "c",
            "inputs": [{"name": "x", "dtype": "float32"}],
            "expected": {
                "standard": "ascendoptest_default",
                "compare_dtype": "float32",
                "compare": "rel_err",
                "policy": {"kind": "ascendoptest_default",
                           "tolerance": 0.0001, "error_rate": 0.0001},
                "acceptance_policy": {"old": True},
                "acceptance_tolerance_policy_id": "old",
            },
        }]}
        spec = {
            "precision": {
                "acceptance_policy": {
                    "standard": "ascendoptest_default",
                    "error_rate": 0.01,
                },
            },
        }
        rebound = R.rebind_acceptance_policy(caseset, spec)
        expected = rebound["cases"][0]["expected"]
        self.assertEqual(expected["acceptance_policy"]["error_rate"], 0.01)
        self.assertEqual(
            expected["acceptance_tolerance_policy_id"],
            "ascendoptest_default:float32",
        )
        self.assertEqual(caseset["cases"][0]["expected"]["acceptance_policy"],
                         {"old": True})

    def test_exact_output_refuses_fake_relaxation(self):
        caseset = {"cases": [{
            "id": "c",
            "inputs": [{"name": "x", "dtype": "int32"}],
            "expected": {
                "standard": "exact", "compare_dtype": "int32",
                "compare": "exact_equal",
            },
        }]}
        spec = {"precision": {"acceptance_policy": {"error_rate": 0.1}}}
        with self.assertRaisesRegex(R.RetestExecutionError, "不生效"):
            R.rebind_acceptance_policy(caseset, spec)

    def test_multi_output_rebinds_value_and_index(self):
        value_policy = {
            "kind": "torch_allclose", "rtol": 2 ** -13,
            "atol": 1e-3, "equal_nan": True,
        }
        caseset = {"cases": [{
            "id": "c",
            "inputs": [{"name": "self", "dtype": "float32"}],
            "expected": {"outputs": [
                {
                    "name": "values", "role": "value",
                    "compare_dtype": "float32", "standard": "torch_allclose",
                    "tolerance_policy_id": "torch_allclose:float32",
                    "policy": value_policy,
                },
                {
                    "name": "indices", "role": "index", "compare_dtype": "int64",
                    "standard": "torch_allclose", "tolerance_policy_id": None,
                    "policy": {
                        "kind": "index_value_consistency",
                        "gather_from": "self",
                        "value_rtol": value_policy["rtol"],
                        "value_atol": value_policy["atol"],
                    },
                    "index_of": "values",
                },
            ]},
        }]}
        spec = {
            "precision": {
                "acceptance_policy": {
                    "standard": "torch_allclose",
                    "rtol": 0.01,
                    "atol": 0.02,
                },
            },
        }
        rebound = R.rebind_acceptance_policy(caseset, spec)
        outputs = rebound["cases"][0]["expected"]["outputs"]
        self.assertEqual(outputs[0]["acceptance_policy"]["rtol"], 0.01)
        self.assertEqual(outputs[0]["acceptance_policy"]["atol"], 0.02)
        self.assertEqual(
            outputs[1]["acceptance_policy"]["value_rtol"], 0.01)
        self.assertEqual(
            outputs[1]["acceptance_policy"]["value_atol"], 0.02)


class CppExtensionRetestBindingTest(unittest.TestCase):
    def _fixture(self):
        invocations = [{
            "case_id": "c", "symbol": "Any", "entrypoint": "invoke_v0",
            "slots": [{"role": "in", "name": "x"},
                      {"role": "out", "name": "y"}],
        }]
        generated_manifest = {
            "spec_sha256": "2" * 64,
            "namespace": "oprunway_test",
        }
        generated_manifest_sha = R.cpp_extension_adapter._canonical_sha(
            generated_manifest)
        manifest = {
            "runner_binding": {
                "schema": "oprunway.precision_retest.cpp_extension_binding",
                "schema_version": 1,
                "base_receipt_sha256": "a" * 64,
                "base_invocation_plan_sha256": "b" * 64,
                "base_manifest_sha256": generated_manifest_sha,
                "base_spec_sha256": "2" * 64,
                "base_namespace": "oprunway_test",
                "base_pr_head": "c" * 40,
                "base_build_receipt_sha256": "d" * 64,
                "base_vendor_build_argv": ["bash", "build.sh", "-f", "x"],
                "base_source_repo": "repo",
                "base_vendor_elf_sha256": "e" * 64,
                "base_soc": "A3",
                "base_toolkit": "9.0.1",
                "selected_invocations": invocations,
            },
            "execution_identity": {
                "vendor_elf_sha256": "e" * 64,
                "soc": "A3", "toolkit": "9.0.1",
            },
        }
        directive = {
            "source_identity": {
                "pr_head": "c" * 40,
                "build_receipt_sha256": "d" * 64,
            },
        }
        build_receipt = {
            "source": {"pr_head_sha": "c" * 40, "repo": "repo"},
            "build": {"argv": ["bash", "build.sh", "-f", "x"]},
        }
        receipt = {
            "bindings": {
                "manifest_sha256": generated_manifest_sha,
                "spec_sha256": "2" * 64,
                "invocation_plan_sha256": None,
            },
            "load": {"namespace": "oprunway_test"},
            "vendor": {
                "library_sha256": "e" * 64,
                "build_receipt": build_receipt,
                "build_receipt_sha256": "f" * 64,
            },
            "runtime": {"soc": "A3", "cann_version": "9.0.1"},
            "artifact": {"sha256": "1" * 64},
        }
        plan = {
            "cases": copy.deepcopy(invocations),
            "manifest_sha256": generated_manifest_sha,
            "namespace": "oprunway_test",
        }
        receipt["bindings"]["invocation_plan_sha256"] = (
            R.cpp_extension_adapter._canonical_sha(plan))
        return manifest, directive, plan, receipt, generated_manifest

    def test_fresh_receipt_binds_exact_base_vendor_sha_and_invocations(self):
        manifest, directive, plan, receipt, generated = self._fixture()
        got = R._validate_cpp_extension_fresh_receipt(
            receipt, manifest, directive, plan, generated)
        self.assertEqual(got["fresh_extension_elf_sha256"], "1" * 64)
        self.assertEqual(got["fresh_vendor_build_receipt_sha256"], "f" * 64)

    def test_same_pr_with_different_vendor_elf_is_blocked(self):
        manifest, directive, plan, receipt, generated = self._fixture()
        receipt["vendor"]["library_sha256"] = "9" * 64
        with self.assertRaisesRegex(
                R.RetestExecutionError, "vendor_elf 身份漂移"):
            R._validate_cpp_extension_fresh_receipt(
                receipt, manifest, directive, plan, generated)

    def test_changed_invocation_is_blocked(self):
        manifest, directive, plan, receipt, generated = self._fixture()
        plan["cases"][0]["entrypoint"] = "other"
        with self.assertRaisesRegex(
                R.RetestExecutionError, "invocation plan"):
            R._validate_cpp_extension_fresh_receipt(
                receipt, manifest, directive, plan, generated)

    def test_manifest_namespace_drift_is_blocked(self):
        manifest, directive, plan, receipt, generated = self._fixture()
        generated["namespace"] = "drifted"
        with self.assertRaisesRegex(
                R.RetestExecutionError, "manifest/spec/namespace"):
            R._validate_cpp_extension_fresh_receipt(
                receipt, manifest, directive, plan, generated)

    def test_precision_only_path_never_calls_perf_adapter(self):
        subset = {"op": "AnyOp", "cases": []}
        manifest, directive, plan, receipt, generated = self._fixture()
        with tempfile.TemporaryDirectory() as work, \
                mock.patch.object(R.cpp_extension_adapter, "prepare"), \
                mock.patch.object(
                    R, "_strict_work_json",
                    side_effect=[plan, generated]), \
                mock.patch.object(
                    R.cpp_extension_adapter,
                    "run_cpp_extension_precision_only",
                    return_value={
                        "cpp_extension_receipt": receipt,
                        "evidence": [],
                    }) as precision_only, \
                mock.patch.object(
                    R.cpp_extension_adapter, "run_cpp_extension") as full:
            got = R._run_cpp_extension_task2_only(
                {"runner_form": "cpp_extension"}, subset, work,
                manifest, directive)
        precision_only.assert_called_once()
        full.assert_not_called()
        self.assertFalse(got.get("performance_collected", False))


if __name__ == "__main__":
    unittest.main()
