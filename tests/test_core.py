from __future__ import annotations

import argparse
import dataclasses
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest.mock import patch


PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugin"
sys.path.insert(0, str(PLUGIN_ROOT))

from oprunway.atk import (
    _case_filter,
    _coverage,
    _coverage_projection,
    _execution_environment,
    _generated_case_projection,
    _normalize_rows,
    _profile_evidence,
    _raw_profile_evidence,
    _run_independent_phases,
    _saved_outputs,
    _settled_command_receipt,
    _task_case_bundle_evidence,
    _validate_cases,
    _workbook_rows,
    atk_preflight,
    generate_cases,
    run_cases,
)
from oprunway.build import (
    _cache_matches_request,
    _find_cache,
    _find_requested_cache,
    _select_package,
    _target_delivery,
    _vendor_root,
    build_operator,
)
from oprunway.cli import build_parser, main
from oprunway.contract import spec_digest, validate_spec
from oprunway.source import (
    build_input_anchor,
    build_input_snapshot,
    build_input_snapshot_anchor,
    content_anchor,
)
from oprunway.util import (
    CommandReceipt,
    WorkflowError,
    atomic_write_json,
    digest_json,
    load_json,
    run_command,
    sha256_file,
)
from oprunway.verdict import finalize
from oprunway.workflow import _copy_case_bundle, _stage_source, run_acceptance


def sample_spec(*, performance: str = "none") -> dict:
    return {
        "schema": "oprunway.acceptance_spec",
        "schema_version": 1,
        "operator": {
            "name": "Example",
            "aclnn_name": "Example",
            "op_type": "Example",
            "build_token": "example",
            "source_subdir": "math/example",
        },
        "task": {
            "taskdoc_sha256": "a" * 64,
            "hardware": ["ascend910b"],
            "dimensions": {"precision": True, "performance": performance},
            "precision": {"atk_accuracy": "single_bm"},
            "unvalidated_requirements": [],
            "required_cases": [{"inputs": [{"index": 0, "dtype": "fp32"}]}],
            "performance_required_cases": [0] if performance == "measure" else [],
        },
        "runner": {
            "form": "atk_aclnn",
            "atk_version": "26.5.14",
            "device": 0,
            "case_timeout_seconds": 60,
            "stage_timeout_seconds": 600,
            "workflow_timeout_seconds": 1200,
            "seed": 17,
        },
        "build": {"profile": "cann_ops_package_v1", "vendor_name": "oprunway", "jobs": 4},
    }


class ContractTests(unittest.TestCase):
    def test_plugin_manifest_and_active_entry_inventory(self):
        manifest = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "oprunway")
        self.assertEqual(manifest["version"], "1.0.0")
        self.assertEqual(
            [path.relative_to(PLUGIN_ROOT).as_posix() for path in (PLUGIN_ROOT / "agents").glob("*.md")],
            [],
        )
        self.assertEqual(
            [path.relative_to(PLUGIN_ROOT).as_posix() for path in (PLUGIN_ROOT / "commands").glob("*.md")],
            [],
        )
        self.assertEqual(
            [path.relative_to(PLUGIN_ROOT).as_posix() for path in (PLUGIN_ROOT / "skills").glob("*/SKILL.md")],
            ["skills/acceptance-workflow/SKILL.md"],
        )

    def test_gaussian_witness_pins_the_taskdoc_mixed_tolerance_values(self):
        witness = json.loads(
            (Path(__file__).resolve().parent / "witnesses" / "gaussian_blur" / "spec.json").read_text(
                encoding="utf-8"
            )
        )
        threshold = witness["task"]["precision"]["atk_accuracy"]["mixed_tolerance_bm"]
        self.assertEqual(threshold, {
            "fp32_rtol": 2**-10,
            "fp32_atol": 2**-16,
            "fp32_required_matched_ratio": 0.99,
            "fp32_max_abs_error_limit": 1e-2,
            "fp32_max_ulp_multiple": 32,
        })

    def test_explicit_bad_values_fail_closed(self):
        spec = sample_spec()
        spec["runner"]["stage_timeout_seconds"] = None
        with self.assertRaises(WorkflowError):
            validate_spec(spec)

    def test_runner_device_is_logical_zero_for_runtime_physical_mapping(self):
        spec = sample_spec()
        spec["runner"]["device"] = 1
        with self.assertRaises(WorkflowError) as raised:
            validate_spec(spec)
        self.assertEqual(raised.exception.code, "INVALID_SPEC")

    def test_task_case_bundle_contract_is_exact_and_fail_closed(self):
        spec = sample_spec()
        spec["task"]["case_bundle"] = {
            "schema": "oprunway.task_case_bundle",
            "schema_version": 1,
            "source_locator": "https://example.invalid/cases/",
            "case_count": 1,
            "expected_generated_case_count": 1,
            "generated_projection_sha256": "b" * 64,
            "files": [{"path": "cases.json", "sha256": "c" * 64}],
        }
        self.assertEqual(validate_spec(spec)["task"]["case_bundle"]["case_count"], 1)
        for mutation in ("extra", "bad_hash", "bad_count", "bad_path"):
            with self.subTest(mutation=mutation):
                invalid = json.loads(json.dumps(spec))
                if mutation == "extra":
                    invalid["task"]["case_bundle"]["unexpected"] = True
                elif mutation == "bad_hash":
                    invalid["task"]["case_bundle"]["files"][0]["sha256"] = "short"
                elif mutation == "bad_count":
                    invalid["task"]["case_bundle"]["expected_generated_case_count"] = 0
                else:
                    invalid["task"]["case_bundle"]["files"][0]["path"] = "../cases.json"
                with self.assertRaises(WorkflowError):
                    validate_spec(invalid)

    def test_future_safe_soc_token_does_not_require_code_change(self):
        spec = sample_spec()
        spec["task"]["hardware"] = ["ascend1234_next"]
        self.assertEqual(validate_spec(spec)["task"]["hardware"], ["ascend1234_next"])

    def test_non_finite_json_values_fail_closed(self):
        spec = sample_spec()
        spec["task"]["precision"]["atk_accuracy"] = {
            "mixed_tolerance_bm": {"fp32_rtol": float("nan")}
        }
        with self.assertRaises(WorkflowError) as raised:
            digest_json(validate_spec(spec))
        self.assertEqual(raised.exception.code, "INVALID_JSON_VALUE")

    def test_production_code_has_no_witness_operator_branch(self):
        forbidden = ("Bernoulli", "RemainderTensorTensor", "Roll", "GaussianBlur")
        for path in (PLUGIN_ROOT / "oprunway").glob("*.py"):
            if path.name.startswith("._"):
                continue
            text = path.read_text(encoding="utf-8")
            for name in forbidden:
                self.assertNotIn(name, text, f"witness-specific production code in {path}")


class CliTests(unittest.TestCase):
    def test_only_accept_is_public(self):
        parser = build_parser()
        subparsers = next(action for action in parser._actions if action.dest == "command")
        self.assertEqual(set(subparsers.choices), {"accept"})
        accept = subparsers.choices["accept"]
        atk = next(action for action in accept._actions if action.dest == "atk_bin")
        self.assertEqual(atk.default, "atk")
        physical = next(action for action in accept._actions if action.dest == "physical_device")
        self.assertTrue(physical.required)
        self.assertEqual(physical.type("2"), 2)
        for invalid in ("-1", "256", "not-an-integer"):
            with self.subTest(invalid=invalid), self.assertRaises(argparse.ArgumentTypeError):
                physical.type(invalid)

    def test_dirty_session_is_not_modified(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            (source / "math" / "example").mkdir(parents=True)
            (source / "math" / "example" / "op.cpp").write_text("x", encoding="utf-8")
            session = root / "existing"
            session.mkdir()
            marker = session / "keep"
            marker.write_text("unchanged", encoding="utf-8")
            taskdoc = root / "task.md"
            taskdoc.write_text("task", encoding="utf-8")
            design = root / "design.yaml"
            design.write_text("name: example\n", encoding="utf-8")
            spec_path = root / "spec.json"
            atomic_write_json(spec_path, sample_spec())
            rc = main([
                "accept", "--spec", str(spec_path), "--taskdoc", str(taskdoc),
                "--source-root", str(source), "--design", str(design),
                "--atk-bin", str(marker), "--target-soc", "ascend910b",
                "--session-dir", str(session), "--physical-device", "2",
            ])
            self.assertEqual(rc, 2)
            self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")
            self.assertEqual(sorted(path.name for path in session.iterdir()), ["keep"])


class AnchorTests(unittest.TestCase):
    def test_anchor_is_deterministic_and_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scope = root / "math" / "example"
            scope.mkdir(parents=True)
            (scope / "kernel.cpp").write_text("one", encoding="utf-8")
            first = content_anchor(root, "math/example")
            (scope / "._kernel.cpp").write_bytes(b"AppleDouble metadata")
            (scope / ".DS_Store").write_bytes(b"Finder metadata")
            macos = scope / "__MACOSX"
            macos.mkdir()
            (macos / "ignored").write_bytes(b"metadata")
            second = content_anchor(root, "math/example")
            self.assertEqual(first, second)
            os.symlink(scope / "kernel.cpp", scope / "alias.cpp")
            with self.assertRaises(WorkflowError):
                content_anchor(root, "math/example")

    def test_staging_omits_transport_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "build.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (source / "._build.sh").write_bytes(b"AppleDouble")
            (source / ".DS_Store").write_bytes(b"Finder")
            (source / "__MACOSX").mkdir()
            (source / ".git").mkdir()
            (source / ".git" / "config").write_text("metadata", encoding="utf-8")
            nested_git = source / "third_party" / "opbase" / ".git"
            nested_git.mkdir(parents=True)
            (nested_git / "index").write_text("nested metadata", encoding="utf-8")
            (source / "build").mkdir()
            (source / "build" / "old.o").write_bytes(b"old")
            scope = source / "math" / "example"
            scope.mkdir(parents=True)
            (scope / "kernel.cpp").write_text("kernel", encoding="utf-8")
            (scope / "__pycache__").mkdir()
            (scope / "__pycache__" / "cache.pyc").write_bytes(b"cache")
            before = build_input_anchor(source)
            operator_before = content_anchor(source, "math/example")
            _stage_source(source.resolve(), root / "staged")
            self.assertTrue((root / "staged" / "build.sh").is_file())
            self.assertFalse((root / "staged" / "._build.sh").exists())
            self.assertFalse((root / "staged" / ".DS_Store").exists())
            self.assertFalse((root / "staged" / "__MACOSX").exists())
            self.assertFalse((root / "staged" / ".git").exists())
            self.assertFalse((root / "staged" / "third_party" / "opbase" / ".git").exists())
            self.assertFalse((root / "staged" / "build").exists())
            self.assertFalse((root / "staged" / "math" / "example" / "__pycache__").exists())
            self.assertEqual(before, build_input_anchor(root / "staged"))
            self.assertEqual(operator_before, content_anchor(root / "staged", "math/example"))

            snapshot = build_input_snapshot(source)
            (nested_git / "index").write_text("build-updated metadata", encoding="utf-8")
            self.assertEqual(snapshot, build_input_snapshot(source))

    def test_post_build_snapshot_allows_new_outputs_but_not_input_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            build_script = source / "build.sh"
            build_script.write_text("#!/bin/sh\n", encoding="utf-8")
            before = build_input_snapshot(source)
            generated = source / "third_party" / "downloaded" / "dependency.h"
            generated.parent.mkdir(parents=True)
            generated.write_text("generated", encoding="utf-8")
            after_addition = build_input_snapshot(source)
            self.assertTrue(all(after_addition.get(path) == value for path, value in before.items()))
            addition_anchor = build_input_snapshot_anchor(after_addition)
            generated.write_text("different generated bytes", encoding="utf-8")
            self.assertNotEqual(addition_anchor, build_input_anchor(source))
            build_script.write_text("#!/bin/sh\n# changed\n", encoding="utf-8")
            after_mutation = build_input_snapshot(source)
            self.assertFalse(all(after_mutation.get(path) == value for path, value in before.items()))

    def test_vendor_root_is_derived_from_fresh_elf_layout(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = Path(temporary).resolve()
            library = install / "vendors" / "candidate" / "op_api" / "lib" / "libcust_opapi.so"
            library.parent.mkdir(parents=True)
            library.write_bytes(b"elf")
            self.assertEqual(_vendor_root(install, library), install / "vendors" / "candidate")

    def test_identical_cpack_copies_share_one_package_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            nested = root / "build_out" / "_CPack_Packages" / "candidate.run"
            final = root / "build_out" / "candidate.run"
            nested.parent.mkdir(parents=True)
            final.parent.mkdir(parents=True, exist_ok=True)
            nested.write_bytes(b"same package")
            final.write_bytes(b"same package")
            selected, copies = _select_package([nested, final], root)
            self.assertEqual(selected, final)
            self.assertEqual(len(copies), 2)
            nested.write_bytes(b"different package")
            with self.assertRaises(WorkflowError):
                _select_package([nested, final], root)

    def test_target_delivery_requires_exact_soc_and_real_kernel_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = Path(temporary)
            tbe = install / "vendors" / "v" / "op_impl" / "ai_core" / "tbe"
            wrong = tbe / "config" / "ascend910b" / "aic-ascend910b-ops-info.json"
            wrong.parent.mkdir(parents=True)
            atomic_write_json(wrong, {"Example": {"opFile": {"value": "example"},
                                                       "opInterface": {"value": "example"}}})
            vendor_root = install / "vendors" / "v"
            with self.assertRaises(WorkflowError):
                _target_delivery(install, vendor_root, "ascend910_93", "example", "Example")

            info = tbe / "config" / "ascend910_93" / "aic-ascend910_93-ops-info.json"
            info.parent.mkdir(parents=True)
            atomic_write_json(
                info,
                {
                    "Example": {
                        "opFile": {"value": "example"},
                        "opInterface": {"value": "example"},
                    }
                },
            )
            kernel = tbe / "kernel"
            binary = kernel / "ascend910_93" / "example" / "Example.o"
            metadata = kernel / "ascend910_93" / "example" / "Example.json"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"kernel")
            metadata.write_text("{}", encoding="utf-8")
            config = kernel / "config" / "ascend910_93" / "binary_info_config.json"
            config.parent.mkdir(parents=True)
            atomic_write_json(config, {"Example": {"binaryList": [{
                "binPath": "ascend910_93/example/Example.o",
                "jsonPath": "ascend910_93/example/Example.json",
            }]}})
            evidence = _target_delivery(install, vendor_root, "ascend910_93", "example", "Example")
            self.assertEqual(evidence[0]["requested_soc"], "ascend910_93")
            self.assertEqual(evidence[0]["delivery_soc"], "ascend910_93")
            self.assertEqual(len(evidence[0]["kernel_files"]), 2)
            wrong_name = info.with_name("aic-wrong-ops-info.json")
            info.rename(wrong_name)
            with self.assertRaises(WorkflowError):
                _target_delivery(install, vendor_root, "ascend910_93", "example", "Example")
            wrong_name.rename(info)
            binary.unlink()
            with self.assertRaises(WorkflowError):
                _target_delivery(install, vendor_root, "ascend910_93", "example", "Example")

    def test_cmake_cache_requires_exact_target_assignments(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            cache = source / "build" / "CMakeCache.txt"
            cache.parent.mkdir()
            cache.write_text(
                "ASCEND_COMPUTE_UNIT:STRING=ascend910_93\n"
                "ASCEND_OP_NAME:STRING=example\n"
                "OP_CACHE_example_ascend910_93:INTERNAL=Example\n"
                "VENDOR_NAME:STRING=oprunway\n"
                "vendor_name:STRING=customize\n",
                encoding="utf-8",
            )
            self.assertEqual(
                _find_cache(source, {}, "ascend910_93", "example", "Example", "oprunway"),
                cache.resolve(),
            )
            requested, values = _find_requested_cache(
                source, {}, "ascend910_93", "example", "oprunway"
            )
            self.assertEqual(requested, cache.resolve())
            self.assertEqual(values["ASCEND_OP_NAME"], "example")
            self.assertTrue(
                _cache_matches_request(values, "ascend910_93", "example", "oprunway")
            )
            self.assertFalse(
                _cache_matches_request(values, "ascend910b", "example", "oprunway")
            )
            cache.write_text(cache.read_text(encoding="utf-8").replace("Example", "Other"), encoding="utf-8")
            with self.assertRaises(WorkflowError):
                _find_cache(source, {}, "ascend910_93", "example", "Example", "oprunway")

    def test_build_rejects_shared_build_input_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            scope = source / "math" / "example"
            scope.mkdir(parents=True)
            (scope / "kernel.cpp").write_text("kernel", encoding="utf-8")
            build_script = source / "build.sh"
            build_script.write_text("#!/bin/sh\n", encoding="utf-8")
            spec = sample_spec()
            facts = {
                "schema": "oprunway.source_facts", "schema_version": 1,
                "status": "READY", "spec_sha256": spec_digest(spec),
                "target": {"requested_soc": "ascend910b"},
                "source": {
                    "content_anchor": content_anchor(source, "math/example"),
                    "build_input_anchor": build_input_anchor(source),
                },
            }
            facts_path = root / "facts.json"
            atomic_write_json(facts_path, facts)
            install = root / "install"
            package = source / "build_out" / "fresh.run"
            cache = source / "build" / "CMakeCache.txt"
            library = install / "vendors" / "v" / "op_api" / "lib" / "libdut.so"
            nm_log = root / "nm.log"
            calls = 0

            def fake_run(argv, *, cwd, timeout_seconds, stdout_path, stderr_path, env=None):
                nonlocal calls
                calls += 1
                Path(stdout_path).write_text("ok\n", encoding="utf-8")
                Path(stderr_path).write_text("", encoding="utf-8")
                if calls == 1:
                    package.parent.mkdir()
                    package.write_bytes(b"package")
                    cache.parent.mkdir()
                    cache.write_text("cache", encoding="utf-8")
                    build_script.write_text("#!/bin/sh\n# mutated\n", encoding="utf-8")
                else:
                    library.parent.mkdir(parents=True)
                    library.write_bytes(b"elf")
                    nm_log.write_text("symbols", encoding="utf-8")
                return CommandReceipt(
                    argv=list(argv), cwd=str(cwd), started_at="2026-08-10T00:00:00+00:00",
                    elapsed_seconds=0.1, returncode=0, timed_out=False,
                    stdout_path=str(Path(stdout_path)), stderr_path=str(Path(stderr_path)),
                    stdout_sha256=sha256_file(stdout_path), stderr_sha256=sha256_file(stderr_path),
                    processes_drained=True,
                )

            with patch("oprunway.build.run_command", side_effect=fake_run), \
                    patch("oprunway.build._discover_library", return_value=(library, nm_log)), \
                    patch("oprunway.build._find_requested_cache", return_value=(cache, {
                        "ASCEND_COMPUTE_UNIT": "ascend910b",
                        "ASCEND_OP_NAME": "example",
                        "VENDOR_NAME": "oprunway",
                        "OP_CACHE_example_ascend910b": "Example",
                    })), \
                    patch("oprunway.build._target_delivery", return_value=[{"verified": True}]):
                with self.assertRaises(WorkflowError) as raised:
                    build_operator(
                        spec=spec, facts_path=facts_path, source_root=source,
                        install_root=install, target_soc="ascend910b",
                        out_path=root / "build.json", evidence_dir=root / "evidence",
                    )
            self.assertEqual(raised.exception.code, "SOURCE_MUTATED")

    def test_build_records_missing_requested_target_delivery_for_deterministic_verdict(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            scope = source / "math" / "example"
            scope.mkdir(parents=True)
            (scope / "kernel.cpp").write_text("kernel", encoding="utf-8")
            build_script = source / "build.sh"
            build_script.write_text("#!/bin/sh\n", encoding="utf-8")
            spec = sample_spec()
            facts = {
                "schema": "oprunway.source_facts", "schema_version": 1,
                "status": "READY", "spec_sha256": spec_digest(spec),
                "target": {"requested_soc": "ascend910b"},
                "source": {
                    "content_anchor": content_anchor(source, "math/example"),
                    "build_input_anchor": build_input_anchor(source),
                },
            }
            facts_path = root / "facts.json"
            atomic_write_json(facts_path, facts)
            install = root / "install"
            package = source / "build_out" / "fresh.run"
            cache = source / "build" / "CMakeCache.txt"
            library = install / "vendors" / "v" / "op_api" / "lib" / "libdut.so"
            nm_log = root / "nm.log"
            calls = 0

            def fake_run(argv, *, cwd, timeout_seconds, stdout_path, stderr_path, env=None):
                nonlocal calls
                calls += 1
                Path(stdout_path).write_text("ok\n", encoding="utf-8")
                Path(stderr_path).write_text("", encoding="utf-8")
                if calls == 1:
                    package.parent.mkdir()
                    package.write_bytes(b"package")
                    cache.parent.mkdir()
                    cache.write_text(
                        "ASCEND_COMPUTE_UNIT:STRING=ascend910b\n"
                        "ASCEND_OP_NAME:STRING=example\n"
                        "VENDOR_NAME:STRING=oprunway\n",
                        encoding="utf-8",
                    )
                else:
                    library.parent.mkdir(parents=True)
                    library.write_bytes(b"elf")
                    nm_log.write_text(
                        "0000 T aclnnExampleGetWorkspaceSize\n0000 T aclnnExample\n",
                        encoding="utf-8",
                    )
                return CommandReceipt(
                    argv=list(argv), cwd=str(cwd), started_at="2026-08-10T00:00:00+00:00",
                    elapsed_seconds=0.1, returncode=0, timed_out=False,
                    stdout_path=str(Path(stdout_path)), stderr_path=str(Path(stderr_path)),
                    stdout_sha256=sha256_file(stdout_path), stderr_sha256=sha256_file(stderr_path),
                    processes_drained=True,
                )

            values = {
                "ASCEND_COMPUTE_UNIT": "ascend910b",
                "ASCEND_OP_NAME": "example",
                "VENDOR_NAME": "oprunway",
            }
            missing = WorkflowError(
                "TARGET_KERNEL_MISSING",
                "expected one exact ascend910b ops-info binding example/Example, found 0",
            )
            with patch("oprunway.build.run_command", side_effect=fake_run), \
                    patch("oprunway.build._discover_library", return_value=(library, nm_log)), \
                    patch("oprunway.build._find_requested_cache", return_value=(cache, values)), \
                    patch("oprunway.build._target_delivery", side_effect=missing):
                receipt = build_operator(
                    spec=spec, facts_path=facts_path, source_root=source,
                    install_root=install, target_soc="ascend910b",
                    out_path=root / "build.json", evidence_dir=root / "evidence",
                )
            self.assertEqual(receipt["status"], "TARGET_DELIVERY_MISSING")
            self.assertEqual(receipt["target_delivery"], [])
            self.assertEqual(receipt["target_binding"]["actual"], None)
            self.assertEqual(receipt["target_delivery_error"]["code"], "TARGET_KERNEL_MISSING")


class AtkNormalizationTests(unittest.TestCase):
    def test_atk_white_list_uses_json_array_syntax(self):
        self.assertEqual(_case_filter([3, 7]), "[3,7]")

    def test_performance_phase_runs_after_accuracy_phase_reports_an_error(self):
        events = []

        def accuracy():
            events.append("accuracy")
            return {"error": "incomplete"}

        def performance():
            events.append("performance")
            return {"status": "complete"}

        results = _run_independent_phases(accuracy, performance)
        self.assertEqual(events, ["accuracy", "performance"])
        self.assertEqual(results, ({"error": "incomplete"}, {"status": "complete"}))

    def _run_incomplete_execution(
        self, *, accuracy_error=True, accuracy_start_error=False, profile_error=False,
        profile_os_error=False, raw_profile_failure=False, loaded_error=False,
        accuracy_settle_error=False,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec = sample_spec(performance="measure")
            spec_sha = spec_digest(spec)
            caseset = root / "cases.json"
            atomic_write_json(caseset, [{
                "id": 0, "aclnn_name": "Example", "inputs": [{"dtype": "fp32"}],
                "standard": {"acc": "single_bm"},
            }])
            atk = root / "atk"
            atk.write_bytes(b"atk")
            library = root / "libcust_opapi.so"
            library.write_bytes(b"fresh-elf")
            execution_plugin = root / "execution_plugin.py"
            execution_plugin.write_text("# bound execution plugin\n", encoding="utf-8")
            staged = root / "staged"
            install = root / "install"
            vendor_root = install / "vendors" / "oprunway"
            staged.mkdir()
            vendor_root.mkdir(parents=True)
            case_receipt = root / "case-receipt.json"
            build_receipt = root / "build-receipt.json"
            out = root / "execution.json"
            device_environment = {"ASCEND_RT_VISIBLE_DEVICES": "2"}
            atomic_write_json(case_receipt, {
                "spec_sha256": spec_sha,
                "atk": {"path": str(atk), "version": "26.5.14", "sha256": sha256_file(atk)},
                "cases": {
                    "path": str(caseset), "sha256": sha256_file(caseset), "ids": [0],
                    "required_case_ids": [0], "performance_case_ids": [0],
                },
            })
            delivery = [{"path": "target", "sha256": "d" * 64}]
            atomic_write_json(build_receipt, {
                "spec_sha256": spec_sha,
                "staged_source_root": str(staged),
                "post_build_input_anchor": {"sha256": "b" * 64},
                "target": {"soc": "ascend910b", "build_token": "example"},
                "vendor": {
                    "package_install_root": str(install),
                    "custom_opp_root": str(vendor_root),
                    "library_path": str(library),
                    "library_sha256": sha256_file(library),
                },
                "target_delivery": delivery,
            })
            commands = []

            def fake_run(argv, *, cwd, timeout_seconds, stdout_path, stderr_path, env=None):
                self.assertEqual(env["ASCEND_RT_VISIBLE_DEVICES"], "2")
                destination = Path(stdout_path).parent
                destination.mkdir(parents=True, exist_ok=True)
                is_accuracy = "accuracy" in argv
                if is_accuracy and accuracy_start_error:
                    raise WorkflowError("COMMAND_START_FAILED", "synthetic command start failure")
                Path(stdout_path).write_text("stdout\n", encoding="utf-8")
                Path(stderr_path).write_text("stderr\n", encoding="utf-8")
                if not is_accuracy or not accuracy_error:
                    workbook = destination / "atk_output" / "report.xlsx"
                    workbook.parent.mkdir(parents=True)
                    workbook.write_bytes(b"performance workbook")
                if not is_accuracy and raw_profile_failure:
                    profile_root = destination / "atk_output" / "job" / "pyaclnn_0" / "0"
                    profile_root.mkdir(parents=True, exist_ok=True)
                    (profile_root / "op_statistic_case.csv").write_text(
                        "OP Type,Total Time(us)\nExample,2.5\n", encoding="utf-8"
                    )
                    (profile_root / "op_summary_case.csv").write_text(
                        "bad\nvalue\n", encoding="utf-8"
                    )
                receipt = CommandReceipt(
                    argv=list(argv), cwd=str(cwd), started_at="start",
                    elapsed_seconds=0.1,
                    returncode=1 if is_accuracy and accuracy_error else 0,
                    timed_out=False, stdout_path=str(stdout_path), stderr_path=str(stderr_path),
                    stdout_sha256=sha256_file(stdout_path), stderr_sha256=sha256_file(stderr_path),
                    processes_drained=True,
                    environment={"ASCEND_RT_VISIBLE_DEVICES": "2"},
                )
                commands.append(receipt)
                return receipt

            profile_result = [
                {"case_id": 0, "kind": "op_statistic", "sha256": "1" * 64},
                {"case_id": 0, "kind": "op_summary", "sha256": "2" * 64},
            ]
            loaded_result = {
                "library_path": str(library), "library_sha256": sha256_file(library),
                "log_path": str(root / "load.log"), "log_sha256": "3" * 64,
                "symbol": "aclnnExampleGetWorkspaceSize",
            }
            profile_patcher = (
                patch("oprunway.atk._profile_evidence", side_effect=WorkflowError(
                    "ATK_PROFILE_INCOMPLETE", "missing profile"
                )) if profile_error else patch(
                    "oprunway.atk._profile_evidence", side_effect=OSError("profile I/O failure")
                ) if profile_os_error else patch(
                    "oprunway.atk._profile_evidence", wraps=_profile_evidence
                ) if raw_profile_failure else patch(
                    "oprunway.atk._profile_evidence", return_value=profile_result
                )
            )
            loaded_patcher = (
                patch("oprunway.atk._loaded_library", side_effect=WorkflowError(
                    "LOADED_LIBRARY_MISMATCH", "wrong library"
                )) if loaded_error else patch(
                    "oprunway.atk._loaded_library", return_value=loaded_result
                )
            )

            def settle_command(command):
                settle_command.calls.append("accuracy" if "accuracy" in command.argv else "performance")
                if accuracy_settle_error and "accuracy" in command.argv:
                    raise WorkflowError("ATK_LOG_UNSTABLE", "synthetic accuracy log drift")
                if accuracy_settle_error:
                    return dataclasses.replace(command, stdout_sha256="e" * 64)
                return command

            settle_command.calls = []

            performance_rows = [{
                "编号": 0, "pyaclnn_0_Device性能（us）": 2.5,
                "运行结果": "SUCCESS", "失败原因": None, "用例json信息": "{}",
            }]

            with patch("oprunway.atk.build_input_anchor", return_value={"sha256": "b" * 64}), \
                    patch("oprunway.atk._vendor_root", return_value=vendor_root), \
                    patch("oprunway.atk._target_delivery", return_value=delivery), \
                    patch("oprunway.atk.atk_preflight", return_value={
                        "path": str(atk), "version": "26.5.14", "sha256": sha256_file(atk),
                    }), patch("oprunway.atk.run_command", side_effect=fake_run), \
                    patch("oprunway.atk._settled_command_receipt", side_effect=settle_command), \
                    patch("oprunway.atk._workbook_rows", return_value=(
                        performance_rows, {"总用例数": 1}
                    )), profile_patcher, loaded_patcher:
                with self.assertRaises(WorkflowError) as raised:
                    run_cases(
                        spec=spec, atk_bin=atk, case_receipt_path=case_receipt,
                        build_receipt_path=build_receipt, work_dir=root / "run", out_path=out,
                        execution_plugin=execution_plugin,
                        physical_device=2,
                        runtime_environment=device_environment,
                    )
            return raised.exception.code, load_json(out) if out.exists() else None, len(commands)

    def test_incomplete_accuracy_still_records_attributable_performance(self):
        code, receipt, command_count = self._run_incomplete_execution()
        self.assertEqual(code, "ATK_EXECUTION_FAILED")
        self.assertEqual(command_count, 2)
        self.assertEqual(receipt["status"], "INCOMPLETE")
        self.assertIsNone(receipt["phase_errors"]["performance"])
        self.assertIsNone(receipt["phase_errors"]["evidence"])
        self.assertTrue(receipt["performance_evidence"]["attributable"])
        self.assertEqual(len(receipt["performance_evidence"]["profiles"]), 2)
        self.assertIsNotNone(receipt["loaded_vendor"])
        self.assertIsNotNone(receipt["execution_plugin"])

    def test_profile_error_is_recorded_without_masking_accuracy_error(self):
        code, receipt, command_count = self._run_incomplete_execution(profile_error=True)
        self.assertEqual(code, "ATK_EXECUTION_FAILED")
        self.assertEqual(command_count, 2)
        self.assertEqual(receipt["phase_errors"]["performance"]["code"], "ATK_PROFILE_INCOMPLETE")
        self.assertFalse(receipt["performance_evidence"]["attributable"])
        self.assertEqual(receipt["performance_evidence"]["profiles"], [])

    def test_identity_error_makes_partial_performance_non_attributable(self):
        code, receipt, command_count = self._run_incomplete_execution(loaded_error=True)
        self.assertEqual(code, "ATK_EXECUTION_FAILED")
        self.assertEqual(command_count, 2)
        self.assertEqual(receipt["phase_errors"]["evidence"]["code"], "LOADED_LIBRARY_MISMATCH")
        self.assertFalse(receipt["performance_evidence"]["attributable"])
        self.assertIsNone(receipt["loaded_vendor"])

    def test_performance_profile_error_is_primary_when_accuracy_succeeds(self):
        code, receipt, command_count = self._run_incomplete_execution(
            accuracy_error=False, profile_error=True
        )
        self.assertEqual(code, "ATK_PROFILE_INCOMPLETE")
        self.assertEqual(command_count, 2)
        self.assertIsNone(receipt["phase_errors"]["accuracy"])
        self.assertEqual(receipt["phase_errors"]["performance"]["code"], code)

    def test_profile_os_error_does_not_mask_accuracy_or_skip_receipt(self):
        code, receipt, command_count = self._run_incomplete_execution(profile_os_error=True)
        self.assertEqual(code, "ATK_EXECUTION_FAILED")
        self.assertEqual(command_count, 2)
        self.assertEqual(receipt["phase_errors"]["performance"]["code"], "ATK_PROFILE_INVALID")

    def test_raw_profiles_are_bound_when_validation_fails(self):
        code, receipt, _ = self._run_incomplete_execution(raw_profile_failure=True)
        self.assertEqual(code, "ATK_EXECUTION_FAILED")
        evidence = receipt["performance_evidence"]
        self.assertFalse(evidence["attributable"])
        self.assertEqual(evidence["profiles"], [])
        self.assertEqual(
            {item["kind"] for item in evidence["diagnostic_profiles"]},
            {"op_statistic", "op_summary"},
        )
        self.assertTrue(all(item["sha256"] for item in evidence["diagnostic_profiles"]))

    def test_raw_profile_inventory_preserves_good_files_after_io_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "job" / "pyaclnn_0" / "0"
            root.mkdir(parents=True)
            good = root / "op_statistic_case.csv"
            bad = root / "op_summary_case.csv"
            good.write_text("good\n", encoding="utf-8")
            bad.write_text("bad\n", encoding="utf-8")
            real_sha256_file = sha256_file

            def fail_one(path):
                if Path(path) == bad:
                    raise OSError("synthetic disappearing profile")
                return real_sha256_file(path)

            with patch("oprunway.atk.sha256_file", side_effect=fail_one):
                evidence, error = _raw_profile_evidence(Path(temporary), [0])
            self.assertEqual([(item["kind"], item["path"]) for item in evidence], [
                ("op_statistic", str(good)),
            ])
            self.assertIsNotNone(error)
            self.assertEqual(error.code, "ATK_PROFILE_INVALID")

    def test_accuracy_start_error_still_runs_performance_and_writes_receipt(self):
        code, receipt, command_count = self._run_incomplete_execution(
            accuracy_start_error=True
        )
        self.assertEqual(code, "COMMAND_START_FAILED")
        self.assertEqual(command_count, 1)
        self.assertIsNone(receipt["commands"]["accuracy"])
        self.assertIsNotNone(receipt["commands"]["performance"])
        self.assertTrue(receipt["performance_evidence"]["attributable"])

    def test_accuracy_log_settle_error_does_not_skip_performance_settlement(self):
        code, receipt, command_count = self._run_incomplete_execution(
            accuracy_settle_error=True
        )
        self.assertEqual(code, "ATK_EXECUTION_FAILED")
        self.assertEqual(command_count, 2)
        self.assertEqual(receipt["phase_errors"]["evidence"]["code"], "ATK_LOG_UNSTABLE")
        self.assertIsNotNone(receipt["commands"]["performance"])
        self.assertEqual(receipt["commands"]["performance"]["stdout_sha256"], "e" * 64)

    def test_command_receipt_hashes_logs_after_delayed_flush(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stdout = root / "stdout.log"
            stderr = root / "stderr.log"
            stdout.write_text("stdout\n", encoding="utf-8")
            stderr.write_text("early\n", encoding="utf-8")
            command = CommandReceipt(
                argv=["atk"], cwd=str(root), started_at="start", elapsed_seconds=1.0,
                returncode=-15, timed_out=True,
                stdout_path=str(stdout), stderr_path=str(stderr),
                stdout_sha256=sha256_file(stdout), stderr_sha256=sha256_file(stderr),
                processes_drained=True,
            )
            ticks = iter((0.0, 0.25, 0.5, 1.0, 2.5, 3.0))

            def delayed_flush(_seconds):
                if stderr.read_text(encoding="utf-8") == "early\n":
                    stderr.write_text("early\nlate\n", encoding="utf-8")

            with patch("oprunway.atk.time.monotonic", side_effect=lambda: next(ticks)), \
                    patch("oprunway.atk.time.sleep", side_effect=delayed_flush):
                settled = _settled_command_receipt(command)
            self.assertEqual(settled.stderr_sha256, sha256_file(stderr))
            self.assertNotEqual(settled.stderr_sha256, command.stderr_sha256)

    def test_xlsx_report_parser_has_no_third_party_dependency(self):
        with tempfile.TemporaryDirectory() as temporary:
            workbook = Path(temporary) / "report.xlsx"
            workbook_xml = """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <sheets><sheet name="statistic" sheetId="1" r:id="rId1"/>
 <sheet name="summary" sheetId="2" r:id="rId2"/></sheets></workbook>"""
            relationships = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Target="worksheets/sheet1.xml"/>
 <Relationship Id="rId2" Target="worksheets/sheet2.xml"/>
</Relationships>"""
            statistic = """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
 <row r="1"><c r="A1" t="inlineStr"><is><t>编号</t></is></c>
 <c r="B1" t="inlineStr"><is><t>cpu_0_精度通过</t></is></c>
 <c r="C1" t="inlineStr"><is><t>运行结果</t></is></c></row>
 <row r="2"><c r="A2"><v>7</v></c><c r="B2" t="b"><v>1</v></c>
 <c r="C2" t="s"><v>0</v></c></row>
</sheetData></worksheet>"""
            summary = """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
 <row r="1"><c r="A1" t="inlineStr"><is><t>总用例数</t></is></c></row>
 <row r="2"><c r="A2"><v>1</v></c></row>
</sheetData></worksheet>"""
            shared = """<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
 <si><t>SUCCESS</t></si></sst>"""
            with zipfile.ZipFile(workbook, "w") as archive:
                archive.writestr("xl/workbook.xml", workbook_xml)
                archive.writestr("xl/_rels/workbook.xml.rels", relationships)
                archive.writestr("xl/worksheets/sheet1.xml", statistic)
                archive.writestr("xl/worksheets/sheet2.xml", summary)
                archive.writestr("xl/sharedStrings.xml", shared)
            rows, result = _workbook_rows(workbook)
            self.assertEqual(rows, [{"编号": 7, "cpu_0_精度通过": True, "运行结果": "SUCCESS"}])
            self.assertEqual(result, {"总用例数": 1})

            negative = Path(temporary) / "negative-shared-index.xlsx"
            negative_statistic = statistic.replace('t="s"><v>0</v>', 't="s"><v>-1</v>')
            with zipfile.ZipFile(negative, "w") as archive:
                archive.writestr("xl/workbook.xml", workbook_xml)
                archive.writestr("xl/_rels/workbook.xml.rels", relationships)
                archive.writestr("xl/worksheets/sheet1.xml", negative_statistic)
                archive.writestr("xl/worksheets/sheet2.xml", summary)
                archive.writestr("xl/sharedStrings.xml", shared)
            with self.assertRaises(WorkflowError) as bad_index:
                _workbook_rows(negative)
            self.assertEqual(bad_index.exception.code, "ATK_REPORT_INVALID")

            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(workbook, "a") as archive:
                    archive.writestr("xl/workbook.xml", workbook_xml)
            with self.assertRaises(WorkflowError) as duplicate:
                _workbook_rows(workbook)
            self.assertEqual(duplicate.exception.code, "ATK_REPORT_INVALID")

    def test_casegen_rehashes_atk_immediately_before_command(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            atk = root / "atk"
            atk.write_bytes(b"first")
            design = root / "design.yaml"
            design.write_text("design", encoding="utf-8")
            old_sha = sha256_file(atk)

            def swapped_preflight(*_args, **_kwargs):
                atk.write_bytes(b"second")
                return {"path": str(atk), "version": "26.5.14", "sha256": old_sha}

            with patch("oprunway.atk.atk_preflight", side_effect=swapped_preflight), \
                    patch("oprunway.atk.run_command") as command:
                with self.assertRaises(WorkflowError) as raised:
                    generate_cases(
                        spec=sample_spec(), atk_bin=atk, design_path=design,
                        work_dir=root / "casegen", out_path=root / "receipt.json",
                    )
            self.assertEqual(raised.exception.code, "ATK_IDENTITY_DRIFT")
            command.assert_not_called()

    def test_execution_environment_pins_fresh_vendor_library(self):
        library = Path("/session/install/vendors/v/op_api/lib/libcust_opapi.so")
        with patch.dict(
            os.environ,
            {
                "LD_LIBRARY_PATH": "/system/lib",
                "PYTHONPATH": "/cann/python/site-packages",
                "ASCEND_RT_VISIBLE_DEVICES": "7",
            },
            clear=False,
        ):
            env = _execution_environment(
                library, "/session/install/vendors/v", "/session/inputs/task-cases",
                {"ASCEND_RT_VISIBLE_DEVICES": "3"},
            )
        self.assertEqual(env["ATK_CUSTOM_OPP_PATH"], str(library))
        self.assertEqual(env["ASCEND_CUSTOM_OPP_PATH"], "/session/install/vendors/v")
        self.assertEqual(env["LD_LIBRARY_PATH"], f"{library.parent}:/system/lib")
        self.assertEqual(env["PYTHONPATH"], "/cann/python/site-packages")
        self.assertEqual(env["OPRUNWAY_TASK_CASES_ROOT"], "/session/inputs/task-cases")
        self.assertEqual(env["ASCEND_RT_VISIBLE_DEVICES"], "3")

    def test_required_cases_are_matched_to_distinct_generated_cases(self):
        cases = [
            {"id": 1, "inputs": [{"dtype": "fp32", "shape": [2], "range_values": [-1, 1]}]},
            {"id": 2, "inputs": [{"dtype": "int32", "shape": [2], "range_values": [-1, 1]}]},
        ]
        required = [
            {"inputs": [{"index": 0, "dtype": "fp32"}]},
            {"inputs": [{"index": 0, "dtype": "int32", "shape": [2]}]},
        ]
        self.assertEqual(_coverage(cases, required), [1, 2])
        with self.assertRaises(WorkflowError):
            _coverage(cases[:1], required)

        overlapping = [
            {"inputs": [{"index": 0, "shape": [2]}]},
            {"inputs": [{"index": 0, "dtype": "fp32"}]},
        ]
        self.assertEqual(_coverage(cases, overlapping), [2, 1])

    def test_required_cases_support_tuple_attributes_without_weakening_tensors(self):
        cases = [
            {
                "id": 3,
                "inputs": [
                    {"dtype": "fp32", "shape": [2], "range_values": [-1, 1]},
                    [
                        {"dtype": "int", "range_values": 1},
                        {"dtype": "int", "range_values": -2},
                    ],
                ],
            },
            {
                "id": 4,
                "inputs": [
                    {"dtype": "fp32", "shape": [], "range_values": 0},
                    [],
                ],
            },
        ]
        required = [
            {"inputs": [{"index": 1, "dtype": "int", "value": [1, -2]}]},
            {"inputs": [{"index": 1, "value": []}]},
        ]
        self.assertEqual(_coverage(cases, required), [3, 4])
        with self.assertRaises(WorkflowError):
            _coverage(cases, [{"inputs": [{"index": 1, "shape": [2]}]}])
        malformed = [{"id": 5, "inputs": [[{"dtype": "int"}]]}]
        with self.assertRaises(WorkflowError):
            _coverage(malformed, [{"inputs": [{"index": 0, "value": [None]}]}])

        sentinel = [{
            "name": "__oprunway_empty_tuple__", "type": "attr_tuple",
            "required": True, "dtype": "int", "shape": None,
            "range_values": "default",
        }]
        self.assertEqual(_coverage_projection(sentinel), (None, None, [], True))
        self.assertEqual(
            _coverage([{"id": 6, "inputs": [sentinel]}], [
                {"inputs": [{"index": 0, "value": []}]},
            ]),
            [6],
        )
        sentinel[0]["name"] = "different"
        with self.assertRaises(WorkflowError):
            _coverage([{"id": 6, "inputs": [sentinel]}], [
                {"inputs": [{"index": 0, "value": []}]},
            ])

    def test_caseset_precision_comparator_must_match_spec(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cases.json"
            atomic_write_json(path, [{
                "id": 0,
                "aclnn_name": "Example",
                "inputs": [{"dtype": "fp32"}],
                "standard": {"acc": "single_bm", "perf": "not_key"},
            }])
            cases, ids = _validate_cases(path, "Example", "single_bm")
            self.assertEqual((len(cases), ids), (1, [0]))
            with self.assertRaises(WorkflowError):
                _validate_cases(path, "Example", {"mixed_tolerance_bm": {}})

    def test_preflight_uses_public_cli_without_environment_layout_assumption(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bin_dir = root / "system-bin"
            bin_dir.mkdir(parents=True)
            implementation = root / "atk-real"
            implementation.write_text(
                "#!/bin/sh\n"
                "[ \"$1\" = --version ] || exit 99\n"
                "printf '%s\\n' 26.5.14\n",
                encoding="utf-8",
            )
            implementation.chmod(0o755)
            atk = bin_dir / "atk"
            os.symlink(implementation, atk)
            receipt = atk_preflight(atk, "26.5.14", root / "evidence")
            self.assertEqual(receipt["version"], "26.5.14")
            self.assertEqual(receipt["sha256"], sha256_file(implementation))
            self.assertEqual(Path(receipt["probe"]["argv"][0]).parts[-2:], ("system-bin", "atk"))
            self.assertEqual(receipt["probe"]["argv"][1:], ["--version"])

    def test_preflight_resolves_atk_from_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            atk = root / "atk"
            atk.write_text(
                "#!/bin/sh\n[ \"$1\" = --version ] || exit 99\nprintf '%s\\n' 26.5.14\n",
                encoding="utf-8",
            )
            atk.chmod(0o755)
            with patch.dict(os.environ, {"PATH": str(root)}):
                receipt = atk_preflight("atk", "26.5.14", root / "evidence")
            self.assertEqual(receipt["path"], str(atk))

    def test_casegen_runs_without_execution_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            atk = root / "atk"
            atk.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = --version ]; then printf '%s\\n' 26.5.14; exit 0; fi\n"
                "mkdir -p result/generated/json\n"
                "printf '%s\\n' '[{\"id\":1,\"aclnn_name\":\"Example\",\"standard\":{\"acc\":\"single_bm\"},\"inputs\":[{\"dtype\":\"fp32\"}]}]' "
                "> result/generated/json/cases.json\n",
                encoding="utf-8",
            )
            atk.chmod(0o755)
            design = root / "design.yaml"
            design.write_text("operator: Example\n", encoding="utf-8")
            receipt = generate_cases(
                spec=sample_spec(),
                atk_bin=atk,
                design_path=design,
                work_dir=root / "casegen",
                out_path=root / "receipt.json",
            )
            self.assertEqual(receipt["cases"]["ids"], [1])

    def test_casegen_binds_and_propagates_exact_task_case_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle_root = root / "bundle"
            bundle_root.mkdir()
            bundle_file = bundle_root / "cases.json"
            bundle_file.write_text("{}\n", encoding="utf-8")
            projection = [{
                "name": "BoundCase",
                "inputs": [{"dtype": "fp32", "shape": [2], "value": [-1, 1]}],
            }]
            spec = sample_spec()
            spec["task"]["case_bundle"] = {
                "schema": "oprunway.task_case_bundle",
                "schema_version": 1,
                "source_locator": "https://example.invalid/cases/",
                "case_count": 1,
                "expected_generated_case_count": 1,
                "generated_projection_sha256": digest_json(projection),
                "files": [{"path": "cases.json", "sha256": sha256_file(bundle_file)}],
            }
            atk = root / "atk"
            atk.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = --version ]; then printf '%s\\n' 26.5.14; exit 0; fi\n"
                f"[ \"$OPRUNWAY_TASK_CASES_ROOT\" = {str(bundle_root)!r} ] || exit 97\n"
                "mkdir -p result/generated/json\n"
                "printf '%s\\n' '[{\"id\":1,\"name\":\"BoundCase\","
                "\"aclnn_name\":\"Example\",\"standard\":{\"acc\":\"single_bm\"},"
                "\"inputs\":[{\"dtype\":\"fp32\",\"shape\":[2],"
                "\"range_values\":[-1,1]}]}]' > result/generated/json/cases.json\n",
                encoding="utf-8",
            )
            atk.chmod(0o755)
            design = root / "design.yaml"
            design.write_text("operator: Example\n", encoding="utf-8")
            receipt = generate_cases(
                spec=spec,
                atk_bin=atk,
                design_path=design,
                task_cases_root=bundle_root,
                work_dir=root / "casegen",
                out_path=root / "receipt.json",
            )
            self.assertEqual(receipt["task_case_bundle"]["case_count"], 1)
            self.assertEqual(
                receipt["task_case_bundle"],
                _task_case_bundle_evidence(validate_spec(spec), bundle_root),
            )
            generated = json.loads(Path(receipt["cases"]["path"]).read_text(encoding="utf-8"))
            self.assertEqual(_generated_case_projection(generated), digest_json(projection))
            with self.assertRaises(WorkflowError):
                _generated_case_projection([generated[0], dict(generated[0], id=2)])

            copied = root / "copied"
            self.assertEqual(
                _copy_case_bundle(bundle_root, copied, spec["task"]["case_bundle"]),
                copied.resolve(),
            )
            (bundle_root / "extra.txt").write_text("extra", encoding="utf-8")
            with self.assertRaises(WorkflowError) as raised:
                _copy_case_bundle(bundle_root, root / "rejected", spec["task"]["case_bundle"])
            self.assertEqual(raised.exception.code, "TASK_CASE_BUNDLE_MISMATCH")

    def test_denominator_accuracy_and_performance_are_explicit(self):
        rows = [
            {
                "编号": 7,
                "pyaclnn_0_Device性能（us）": 2.5,
                "cpu_0_精度通过": True,
                "运行结果": "SUCCESS",
                "失败原因": None,
                "用例json信息": "{}",
            }
        ]
        result = _normalize_rows(rows, [7], require_performance=True)
        self.assertTrue(result["performance_complete"])
        self.assertEqual(result["accuracy_failed"], 0)
        self.assertEqual(result["accuracy_failed_ids"], [])
        with self.assertRaises(WorkflowError):
            _normalize_rows(rows, [7, 8], require_performance=True)

    def test_any_missing_accuracy_column_prevents_a_pass(self):
        rows = [
            {
                "编号": 7, "cpu_0_精度通过": True,
                "pyaclnn_0_精度通过": None, "运行结果": "SUCCESS",
            },
            {
                "编号": 8, "cpu_0_精度通过": True,
                "pyaclnn_0_精度通过": False, "运行结果": "SUCCESS",
            },
        ]
        result = _normalize_rows(rows, [7, 8], require_performance=False)
        self.assertEqual(result["accuracy_missing_ids"], [7])

    def test_globally_empty_accuracy_column_is_not_a_comparator(self):
        rows = [{
            "编号": 7,
            "cpu_0_精度通过": True,
            "pyaclnn_0_精度通过": None,
            "运行结果": "SUCCESS",
        }]
        result = _normalize_rows(rows, [7], require_performance=False)
        self.assertEqual(result["accuracy_missing_ids"], [])
        self.assertEqual(result["accuracy_failed_ids"], [])

    def test_accuracy_run_ignores_incidental_device_timing_columns(self):
        rows = [{
            "编号": 7,
            "cpu_0_精度通过": True,
            "pyaclnn_0_Device性能（us）": "not-collected",
            "运行结果": "SUCCESS",
        }]
        result = _normalize_rows(rows, [7], require_performance=False)
        self.assertEqual(result["rows"][0]["device_time_us"], None)

    def test_failed_performance_execution_is_not_complete(self):
        rows = [
            {
                "编号": 7,
                "pyaclnn_0_Device性能（us）": 2.5,
                "运行结果": "FAILED",
                "失败原因": "runtime error",
                "用例json信息": "{}",
            }
        ]
        result = _normalize_rows(rows, [7], require_performance=True)
        self.assertFalse(result["performance_complete"])
        self.assertEqual(result["performance_execution_failed_ids"], [7])

    def test_saved_output_requires_dut_and_reference_for_every_case(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for backend in ("pyaclnn_0", "cpu_0"):
                path = root / "job" / "output" / backend / "cases" / "4" / "output_0.pt"
                path.parent.mkdir(parents=True)
                path.write_bytes(backend.encode("ascii"))
            evidence = _saved_outputs(root, [4])
            self.assertEqual({item["owner"] for item in evidence}, {"dut", "reference"})
            (root / "job" / "output" / "cpu_0" / "cases" / "4" / "output_0.pt").unlink()
            with self.assertRaises(WorkflowError):
                _saved_outputs(root, [4])

    def test_saved_output_must_not_be_empty(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for backend in ("pyaclnn_0", "cpu_0"):
                path = root / backend / "4" / "output.bin"
                path.parent.mkdir(parents=True)
                path.write_bytes(b"" if backend.startswith("pyaclnn") else b"reference")
            with self.assertRaises(WorkflowError):
                _saved_outputs(root, [4])

    def test_expected_error_denominator_can_require_no_tensor_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(_saved_outputs(Path(temporary), []), [])

    def test_failed_case_can_lack_saved_npu_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cpu = root / "cpu_0" / "0" / "output.bin"
            cpu.parent.mkdir(parents=True)
            cpu.write_bytes(b"reference-only")
            evidence = _saved_outputs(root, [0], required_ids=[])
            self.assertEqual(
                [(item["owner"], item["case_id"]) for item in evidence],
                [("reference", 0)],
            )

    def test_non_required_empty_output_placeholder_is_ignored(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            placeholder = root / "pyaclnn_0" / "0" / "output.bin"
            placeholder.parent.mkdir(parents=True)
            placeholder.write_bytes(b"")
            self.assertEqual(_saved_outputs(root, [0], required_ids=[]), [])

    def test_raw_profile_requires_both_kernel_csv_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = root / "profile" / "pyaclnn_0" / "cases" / "4" / "mindstudio_profiler_output"
            profile.mkdir(parents=True)
            (profile / "op_statistic_1.csv").write_text(
                "OP Type,Total Time(us)\nExample,2.5\n", encoding="utf-8"
            )
            (profile / "op_summary_1.csv").write_text(
                "OP Type,Task Duration(us)\nExample,2.5\n", encoding="utf-8"
            )
            evidence = _profile_evidence(root, [4])
            self.assertEqual({item["kind"] for item in evidence}, {"op_statistic", "op_summary"})
            duplicate = profile / "op_summary_2.csv"
            duplicate.write_text(
                "OP Type,Task Duration(us)\nExample,3.5\n", encoding="utf-8"
            )
            with self.assertRaises(WorkflowError):
                _profile_evidence(root, [4])
            duplicate.unlink()
            (profile / "op_summary_1.csv").write_text(
                "OP Type,Task Duration(us)\nExample,not-a-number\n", encoding="utf-8"
            )
            with self.assertRaises(WorkflowError):
                _profile_evidence(root, [4])


class TimeoutTests(unittest.TestCase):
    def test_command_is_not_started_when_process_isolation_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch("oprunway.util._require_command_isolation", side_effect=WorkflowError(
                    "COMMAND_ISOLATION_UNAVAILABLE", "synthetic missing pidfd"
                )), patch("oprunway.util.subprocess.Popen") as popen:
            root = Path(temporary)
            with self.assertRaises(WorkflowError) as raised:
                run_command(
                    [sys.executable, "-c", "pass"], cwd=root, timeout_seconds=1,
                    stdout_path=root / "stdout.log", stderr_path=root / "stderr.log",
                )
            self.assertEqual(raised.exception.code, "COMMAND_ISOLATION_UNAVAILABLE")
            popen.assert_not_called()

    def test_command_start_and_timeout_validation_are_typed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(WorkflowError) as timeout_error:
                run_command(
                    ["missing-command"], cwd=root, timeout_seconds=0,
                    stdout_path=root / "out.log", stderr_path=root / "err.log",
                )
            self.assertEqual(timeout_error.exception.code, "INVALID_TIMEOUT")
            with self.assertRaises(WorkflowError) as start_error:
                run_command(
                    [str(root / "missing-command")], cwd=root, timeout_seconds=1,
                    stdout_path=root / "out.log", stderr_path=root / "err.log",
                )
            self.assertEqual(start_error.exception.code, "COMMAND_START_FAILED")

    def test_outer_timeout_is_enforced(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt = run_command(
                [sys.executable, "-c", "import time; time.sleep(5)"],
                cwd=root,
                timeout_seconds=1,
                stdout_path=root / "stdout.log",
                stderr_path=root / "stderr.log",
            )
            self.assertTrue(receipt.timed_out)
            self.assertLess(receipt.elapsed_seconds, 5)

    def test_command_receipt_binds_safe_device_environment_projection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = os.environ.copy()
            environment["ASCEND_RT_VISIBLE_DEVICES"] = "7"
            receipt = run_command(
                [
                    sys.executable, "-c",
                    "import os; "
                    "assert os.environ['ASCEND_RT_VISIBLE_DEVICES'] == '7'; "
                    "print('child-env-ok')",
                ], cwd=root, timeout_seconds=5,
                stdout_path=root / "stdout.log", stderr_path=root / "stderr.log",
                env=environment,
            )
            self.assertEqual(receipt.environment, {"ASCEND_RT_VISIBLE_DEVICES": "7"})
            self.assertNotIn("OPRUNWAY_COMMAND_TOKEN", receipt.environment)
            self.assertEqual(
                (root / "stdout.log").read_text(encoding="utf-8").strip(),
                "child-env-ok",
            )

    def test_timeout_drains_worker_that_detaches_into_a_new_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ready = root / "worker.ready"
            worker = root / "worker.py"
            worker.write_text(
                "import pathlib, signal, sys, time\n"
                "def term(_signum, _frame):\n"
                "    sys.stderr.write('detached-worker-term\\n')\n"
                "    sys.stderr.flush()\n"
                "signal.signal(signal.SIGTERM, term)\n"
                "pathlib.Path(sys.argv[1]).write_text('ready')\n"
                "while True:\n"
                "    time.sleep(1)\n",
                encoding="utf-8",
            )
            script = root / "detach.py"
            script.write_text(
                "import pathlib, subprocess, sys, time\n"
                f"child = subprocess.Popen([sys.executable, {str(worker)!r}, {str(ready)!r}], "
                "start_new_session=True)\n"
                f"ready = pathlib.Path({str(ready)!r})\n"
                "while not ready.exists():\n"
                "    time.sleep(0.01)\n"
                "print(child.pid, flush=True)\n"
                "time.sleep(30)\n",
                encoding="utf-8",
            )
            stdout = root / "stdout.log"
            receipt = run_command(
                [sys.executable, str(script)], cwd=root, timeout_seconds=1,
                stdout_path=stdout, stderr_path=root / "stderr.log",
            )
            child_pid = int(stdout.read_text(encoding="utf-8").strip())
            status = Path(f"/proc/{child_pid}/status")
            self.assertTrue(receipt.timed_out)
            self.assertTrue(receipt.processes_drained)
            self.assertTrue(
                not status.exists() or "\nState:\tZ" in status.read_text(encoding="utf-8"),
                "detached command worker is still consuming compute",
            )
            self.assertIn("detached-worker-term", (root / "stderr.log").read_text(encoding="utf-8"))
            self.assertEqual(receipt.stdout_sha256, sha256_file(stdout))
            self.assertEqual(receipt.stderr_sha256, sha256_file(root / "stderr.log"))


class WorkflowTests(unittest.TestCase):
    def test_unsupported_target_stops_before_atk_and_keeps_clean_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            scope = source / "math" / "example"
            scope.mkdir(parents=True)
            (scope / "kernel.cpp").write_text("kernel", encoding="utf-8")
            taskdoc = root / "task.md"
            taskdoc.write_text("A3 only", encoding="utf-8")
            design = root / "design.yaml"
            design.write_text("name: torch.abs\n", encoding="utf-8")
            spec = sample_spec()
            spec["task"]["taskdoc_sha256"] = sha256_file(taskdoc)
            session = root / "session"
            result = run_acceptance(
                spec=spec,
                taskdoc_path=taskdoc,
                source_root=source,
                design_path=design,
                atk_bin=root / "missing-atk",
                target_soc="ascend950",
                session_dir=session,
                physical_device=2,
            )
            self.assertEqual(result["acceptance"]["verdict"]["status"], "UNSUPPORTED")
            self.assertTrue((session / "receipts" / "workflow.json").is_file())
            self.assertTrue((session / "reports" / "acceptance.json").is_file())
            self.assertTrue((session / "reports" / "acceptance.md").is_file())
            self.assertFalse((session / "reports" / "acceptance.pending.json").exists())
            self.assertFalse((session / "staging").exists())
            with self.assertRaises(WorkflowError):
                run_acceptance(
                    spec=spec, taskdoc_path=taskdoc, source_root=source,
                    design_path=design, atk_bin=root / "missing-atk",
                    target_soc="ascend950", session_dir=session, physical_device=2,
                )

    def test_unexpected_dependency_error_is_recorded_as_plugin_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            scope = source / "math" / "example"
            scope.mkdir(parents=True)
            (scope / "op.cpp").write_text("x", encoding="utf-8")
            taskdoc = root / "task.md"
            taskdoc.write_text("task", encoding="utf-8")
            design = root / "design.yaml"
            design.write_text("design", encoding="utf-8")
            spec = sample_spec()
            spec["task"]["taskdoc_sha256"] = sha256_file(taskdoc)
            session = root / "session"
            with patch("oprunway.workflow.generate_cases", side_effect=ValueError("third-party crash")):
                with self.assertRaises(WorkflowError) as raised:
                    run_acceptance(
                        spec=spec,
                        taskdoc_path=taskdoc,
                        source_root=source,
                        design_path=design,
                        atk_bin=design,
                        target_soc="ascend910b",
                        session_dir=session,
                        physical_device=2,
                    )
            self.assertEqual(raised.exception.code, "UNEXPECTED_PLUGIN_ERROR")
            receipt = json.loads((session / "receipts" / "workflow.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["error"]["code"], "UNEXPECTED_PLUGIN_ERROR")
            self.assertEqual(receipt["failed_stage"], "atk_casegen")
            self.assertFalse((session / "reports" / "acceptance.json").exists())

    def test_non_dut_result_is_not_published_as_formal_acceptance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            scope = source / "math" / "example"
            scope.mkdir(parents=True)
            (scope / "op.cpp").write_text("x", encoding="utf-8")
            taskdoc = root / "task.md"
            taskdoc.write_text("task", encoding="utf-8")
            design = root / "design.yaml"
            design.write_text("design", encoding="utf-8")
            spec = sample_spec()
            spec["task"]["taskdoc_sha256"] = sha256_file(taskdoc)
            session = root / "session"
            plugin_result = {
                "verdict": {"status": "PLUGIN_ERROR", "reason_code": "TEST", "message": "test"}
            }
            with patch("oprunway.workflow.generate_cases"), \
                    patch("oprunway.workflow.build_operator"), \
                    patch("oprunway.workflow.run_cases") as execution, \
                    patch("oprunway.workflow.finalize", return_value=plugin_result):
                with self.assertRaises(WorkflowError) as raised:
                    run_acceptance(
                        spec=spec, taskdoc_path=taskdoc, source_root=source,
                        design_path=design, atk_bin=design, target_soc="ascend910b",
                        session_dir=session, physical_device=2,
                    )
            workflow = json.loads((session / "receipts" / "workflow.json").read_text(encoding="utf-8"))
            self.assertEqual(
                raised.exception.code, "NON_DUT_EXECUTION_FAILURE", msg=workflow["error"]
            )
            self.assertEqual(workflow["status"], "ERROR")
            self.assertFalse((session / "reports" / "acceptance.json").exists())
            self.assertEqual(execution.call_args.kwargs["physical_device"], 2)
            self.assertEqual(
                execution.call_args.kwargs["runtime_environment"],
                {"ASCEND_RT_VISIBLE_DEVICES": "2"},
            )

    def test_missing_target_delivery_skips_execution_and_publishes_dut_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            scope = source / "math" / "example"
            scope.mkdir(parents=True)
            (scope / "op.cpp").write_text("x", encoding="utf-8")
            taskdoc = root / "task.md"
            taskdoc.write_text("task", encoding="utf-8")
            design = root / "design.yaml"
            design.write_text("design", encoding="utf-8")
            spec = sample_spec()
            spec["task"]["taskdoc_sha256"] = sha256_file(taskdoc)
            session = root / "session"
            dut_fail = {
                "verdict": {
                    "status": "DUT_FAIL",
                    "reason_code": "TARGET_DELIVERY_MISSING",
                    "message": "missing target",
                }
            }
            def fake_finalize(**kwargs):
                atomic_write_json(kwargs["out_path"], dut_fail)
                Path(kwargs["out_path"]).with_suffix(".md").write_text(
                    "# deterministic DUT_FAIL\n", encoding="utf-8"
                )
                return dut_fail

            with patch("oprunway.workflow.generate_cases"), \
                    patch(
                        "oprunway.workflow.build_operator",
                        return_value={"status": "TARGET_DELIVERY_MISSING"},
                    ), patch("oprunway.workflow.run_cases") as execution, \
                    patch("oprunway.workflow.finalize", side_effect=fake_finalize) as finalizer:
                result = run_acceptance(
                    spec=spec, taskdoc_path=taskdoc, source_root=source,
                    design_path=design, atk_bin=design, target_soc="ascend910b",
                    session_dir=session, physical_device=2,
                )
            self.assertEqual(result["acceptance"]["verdict"]["status"], "DUT_FAIL")
            execution.assert_not_called()
            self.assertIsNone(finalizer.call_args.kwargs.get("execution_receipt_path"))


class VerdictTests(unittest.TestCase):
    def _write_chain(
        self, root: Path, *, execution_failed: int, accuracy_failed: int,
        expected_error_failure: bool = False, performance: str = "none",
    ) -> tuple:
        spec = sample_spec(performance=performance)
        spec_sha = spec_digest(spec)
        install = root / "install"
        library = install / "vendors" / "v" / "op_api" / "lib" / "libdut.so"
        library.parent.mkdir(parents=True)
        library.write_bytes(b"fresh-vendor-elf")
        tbe = install / "vendors" / "v" / "op_impl" / "ai_core" / "tbe"
        ops_info = tbe / "config" / "ascend910b" / "aic-ascend910b-ops-info.json"
        ops_info.parent.mkdir(parents=True)
        atomic_write_json(ops_info, {"Example": {
            "opFile": {"value": "example"}, "opInterface": {"value": "example"},
        }})
        kernel_binary = tbe / "kernel" / "ascend910b" / "example" / "Example.o"
        kernel_json = tbe / "kernel" / "ascend910b" / "example" / "Example.json"
        kernel_binary.parent.mkdir(parents=True)
        kernel_binary.write_bytes(b"kernel")
        kernel_json.write_text("{}", encoding="utf-8")
        binary_config = tbe / "kernel" / "config" / "ascend910b" / "binary_info_config.json"
        binary_config.parent.mkdir(parents=True)
        atomic_write_json(binary_config, {"Example": {"binaryList": [{
            "binPath": "ascend910b/example/Example.o",
            "jsonPath": "ascend910b/example/Example.json",
        }]}})
        vendor_root = install / "vendors" / "v"
        target_delivery = _target_delivery(install, vendor_root, "ascend910b", "example", "Example")
        package = root / "fresh-package.run"
        package.write_bytes(b"package")
        cache = root / "CMakeCache.txt"
        cache.write_text(
            "ASCEND_COMPUTE_UNIT:STRING=ascend910b\n"
            "ASCEND_OP_NAME:STRING=example\n"
            "VENDOR_NAME:STRING=oprunway\n"
            "OP_CACHE_example_ascend910b:INTERNAL=Example\n",
            encoding="utf-8",
        )
        nm_log = root / "nm.log"
        symbols = ["aclnnExampleGetWorkspaceSize", "aclnnExample"]
        nm_log.write_text("\n".join(f"00000000 T {symbol}" for symbol in symbols) + "\n", encoding="utf-8")
        atk = root / "atk"
        atk.write_bytes(b"prepared-atk")

        def command_receipt(
            name: str, *, argv: list[str] | None = None, stdout_text: str | None = None,
            cwd: Path | None = None, stdout_path: Path | None = None,
            stderr_path: Path | None = None, environment: dict[str, str] | None = None,
        ) -> dict:
            stdout = stdout_path or root / f"{name}.stdout.log"
            stderr = stderr_path or root / f"{name}.stderr.log"
            stdout.parent.mkdir(parents=True, exist_ok=True)
            stderr.parent.mkdir(parents=True, exist_ok=True)
            stdout.write_text(stdout_text if stdout_text is not None else f"{name} ok\n", encoding="utf-8")
            stderr.write_text("", encoding="utf-8")
            return {
                "argv": argv if argv is not None else [name],
                "cwd": str(cwd or root), "started_at": "2026-08-10T00:00:00+00:00",
                "elapsed_seconds": 1.0, "returncode": 0, "timed_out": False,
                "stdout_path": str(stdout), "stderr_path": str(stderr),
                "stdout_sha256": sha256_file(stdout), "stderr_sha256": sha256_file(stderr),
                "processes_drained": True, "environment": environment or {},
            }

        anchor = {"schema": "oprunway.source_content_anchor", "schema_version": 1, "sha256": "b" * 64}
        staged_source = root / "staged-source"
        staged_source.mkdir()
        (staged_source / "build.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        build_anchor = build_input_anchor(staged_source)
        facts = {
            "schema": "oprunway.source_facts", "schema_version": 1, "status": "READY",
            "spec_sha256": spec_sha,
            "source": {"content_anchor": anchor, "build_input_anchor": build_anchor},
            "target": {
                "requested_soc": "ascend910b",
                "declared_hardware": ["ascend910b"],
                "supported": True,
            },
        }
        build = {
            "schema": "oprunway.build_receipt", "schema_version": 1, "status": "VERIFIED",
            "spec_sha256": spec_sha, "source_content_anchor": anchor,
            "build_input_anchor": build_anchor,
            "staged_source_root": str(staged_source),
            "post_build_input_anchor": build_input_anchor(staged_source),
            "target": {"soc": "ascend910b", "build_token": "example"},
            "build": command_receipt("build"),
            "install": command_receipt("install"),
            "package": {
                "path": str(package), "size": package.stat().st_size,
                "sha256": sha256_file(package), "equivalent_paths": [],
            },
            "cmake_cache": {"path": str(cache), "sha256": sha256_file(cache)},
            "target_binding": {
                "key": "OP_CACHE_example_ascend910b", "expected": "Example", "actual": "Example",
            },
            "vendor": {
                "package_install_root": str(install),
                "custom_opp_root": str(vendor_root),
                "library_path": str(library),
                "library_sha256": sha256_file(library),
                "symbols": symbols,
                "nm_log_path": str(nm_log),
                "nm_log_sha256": sha256_file(nm_log),
            },
            "target_delivery": target_delivery,
            "target_delivery_error": None,
        }
        caseset = root / "generated-cases.json"
        atomic_write_json(
            caseset,
            [
                {
                    "id": 0, "name": "case-0", "aclnn_name": "Example",
                    "standard": {"acc": "single_bm"}, "inputs": [{"dtype": "fp32"}],
                },
                {
                    "id": 1, "name": "case-1", "aclnn_name": "Example",
                    "standard": {"acc": "single_bm"}, "inputs": [{"dtype": "fp32"}],
                },
            ],
        )
        cases_atk = {
            "path": str(atk), "version": "26.5.14", "sha256": sha256_file(atk),
            "probe": command_receipt("case-atk-probe"),
        }
        execution_atk = {
            "path": str(atk), "version": "26.5.14", "sha256": sha256_file(atk),
            "probe": command_receipt("execution-atk-probe"),
        }
        cases = {
            "schema": "oprunway.atk_case_receipt", "schema_version": 1, "status": "VERIFIED",
            "spec_sha256": spec_sha, "atk": cases_atk,
            "command": command_receipt("casegen"),
            "cases": {
                "path": str(caseset), "sha256": sha256_file(caseset),
                "count": 2, "ids": [0, 1],
                "required_case_ids": [0],
                "performance_case_ids": [0] if performance == "measure" else [],
            },
        }
        paths = [
            root / name for name in
            ("facts.json", "build.json", "cases.json", "execution.json")
        ]
        for path, value in zip(paths[:3], (facts, build, cases)):
            atomic_write_json(path, value)
        execution_work = root / "execution-work"
        execution_work.mkdir()
        workbook = root / "accuracy.xlsx"
        workbook.write_bytes(b"workbook-evidence")
        performance_workbook = None
        profiles = []
        if performance == "measure":
            performance_path = root / "performance.xlsx"
            performance_path.write_bytes(b"performance-workbook-evidence")
            performance_workbook = {
                "path": str(performance_path), "sha256": sha256_file(performance_path),
                "summary": {"总用例数": 1},
            }
            for kind in ("op_statistic", "op_summary"):
                profile = root / f"{kind}-0.csv"
                profile.write_text(f"{kind}\n", encoding="utf-8")
                profiles.append({
                    "case_id": 0, "kind": kind, "path": str(profile),
                    "size": profile.stat().st_size, "sha256": sha256_file(profile),
                })
        load_log = root / "loaded-library.log"
        load_log.write_text("loaded fresh vendor", encoding="utf-8")
        normal_ids = [0] if expected_error_failure else [0, 1]
        outputs = []
        for owner in ("dut", "reference"):
            for case_id in normal_ids:
                output = root / f"{owner}-{case_id}.bin"
                output.write_bytes(f"{owner}-{case_id}".encode("ascii"))
                outputs.append(
                    {
                        "owner": owner, "case_id": case_id, "path": str(output),
                        "size": output.stat().st_size, "sha256": sha256_file(output),
                    }
                )
        command_environment = {"ASCEND_RT_VISIBLE_DEVICES": "2"}

        def execution_command(task: str) -> dict:
            performance_command = task == "performance_device"
            destination = execution_work / ("performance" if performance_command else "accuracy")
            argv = [
                str(atk.resolve()), "aclnn", str(caseset.resolve()),
                "--devices", "0", "--task", task,
                "--output", str(destination), "--timeout", "60",
                "--concurrency", "1", "--save_data",
                "profile:bin" if performance_command else "output:bin",
            ]
            if performance_command:
                argv.extend(["--white_list", "[0]"])
            return command_receipt(
                task, argv=argv, cwd=execution_work,
                stdout_path=destination / "atk-run.stdout.log",
                stderr_path=destination / "atk-run.stderr.log",
                environment=command_environment,
            )

        execution = {
            "schema": "oprunway.atk_execution_receipt", "schema_version": 1, "status": "COMPLETE",
            "work_dir": str(execution_work),
            "spec_sha256": spec_sha,
            "case_receipt_sha256": sha256_file(paths[2]),
            "build_receipt_sha256": sha256_file(paths[1]),
            "device": {
                "physical_device": 2,
                "logical_device": 0,
                "runtime_environment": {"ASCEND_RT_VISIBLE_DEVICES": "2"},
            },
            "atk": execution_atk,
            "commands": {
                "accuracy": execution_command("accuracy"),
                "performance": (
                    execution_command("performance_device")
                    if performance == "measure" else None
                ),
            },
            "loaded_vendor": {
                "library_sha256": sha256_file(library),
                "log_path": str(load_log), "log_sha256": sha256_file(load_log),
            },
            "outputs": outputs,
            "profiles": profiles,
            "report": {
                "path": str(workbook), "sha256": sha256_file(workbook),
                "count": 2, "execution_failed": execution_failed,
                "execution_failed_ids": [0] if execution_failed else [],
                "accuracy_failed": accuracy_failed, "accuracy_missing": 0,
                "accuracy_failed_ids": [1] if accuracy_failed else [],
                "accuracy_missing_ids": [],
                "expected_error_case_ids": [1] if expected_error_failure else [],
                "performance_complete": True,
                "performance_missing": 0,
                "performance_execution_failed": 0,
                "performance_execution_failed_ids": [],
                "performance_case_ids": [0] if performance == "measure" else [],
                "performance_rows": ([{
                    "id": 0, "execution_status": "SUCCESS", "device_time_us": 2.5,
                    "accuracy_passed": None, "failure_reason": None, "case_json": "{}",
                }] if performance == "measure" else []),
                "performance_workbook": performance_workbook,
            },
        }
        atomic_write_json(paths[3], execution)
        return spec, paths

    def test_complete_numerical_mismatch_is_dut_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec, paths = self._write_chain(root, execution_failed=0, accuracy_failed=1)
            result = finalize(
                spec=spec, facts_path=paths[0], build_receipt_path=paths[1],
                case_receipt_path=paths[2], execution_receipt_path=paths[3],
                requested_physical_device=2,
                out_path=root / "acceptance.json",
            )
            self.assertEqual(result["verdict"]["status"], "DUT_FAIL")

    def test_missing_taskdoc_required_target_delivery_is_deterministic_dut_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec, paths = self._write_chain(root, execution_failed=0, accuracy_failed=0)
            build = json.loads(paths[1].read_text(encoding="utf-8"))
            install = Path(build["vendor"]["package_install_root"])
            ops_info = install / build["target_delivery"][0]["ops_info"]["path"]
            ops_info.unlink()
            with self.assertRaises(WorkflowError) as missing:
                _target_delivery(
                    install,
                    Path(build["vendor"]["custom_opp_root"]),
                    "ascend910b",
                    "example",
                    "Example",
                )
            self.assertEqual(missing.exception.code, "TARGET_KERNEL_MISSING")
            build["status"] = "TARGET_DELIVERY_MISSING"
            build["target_delivery"] = []
            build["target_delivery_error"] = {
                "code": missing.exception.code,
                "message": str(missing.exception),
            }
            atomic_write_json(paths[1], build)
            result = finalize(
                spec=spec,
                facts_path=paths[0],
                build_receipt_path=paths[1],
                case_receipt_path=paths[2],
                requested_physical_device=2,
                out_path=root / "acceptance.json",
            )
            self.assertEqual(result["verdict"]["status"], "DUT_FAIL")
            self.assertEqual(result["verdict"]["reason_code"], "TARGET_DELIVERY_MISSING")
            with self.assertRaises(WorkflowError) as contaminated:
                finalize(
                    spec=spec,
                    facts_path=paths[0],
                    build_receipt_path=paths[1],
                    case_receipt_path=paths[2],
                    execution_receipt_path=paths[3],
                    requested_physical_device=2,
                    out_path=root / "rejected.json",
                )
            self.assertEqual(contaminated.exception.code, "EVIDENCE_MISMATCH")

            facts = json.loads(paths[0].read_text(encoding="utf-8"))
            facts["target"]["requested_soc"] = "ascend950"
            facts["target"]["supported"] = False
            atomic_write_json(paths[0], facts)
            with self.assertRaises(WorkflowError) as target_drift:
                finalize(
                    spec=spec,
                    facts_path=paths[0],
                    build_receipt_path=paths[1],
                    case_receipt_path=paths[2],
                    requested_physical_device=2,
                    out_path=root / "target-drift.json",
                )
            self.assertEqual(target_drift.exception.code, "EVIDENCE_MISMATCH")

    def test_fresh_build_chain_fields_are_mandatory(self):
        missing_paths = [
            ("build",), ("install",), ("package",), ("cmake_cache",),
            ("staged_source_root",), ("post_build_input_anchor",),
            ("vendor", "symbols"), ("vendor", "nm_log_path"),
        ]
        for missing_path in missing_paths:
            with self.subTest(missing_path=missing_path), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                spec, paths = self._write_chain(root, execution_failed=0, accuracy_failed=0)
                build = json.loads(paths[1].read_text(encoding="utf-8"))
                if len(missing_path) == 1:
                    del build[missing_path[0]]
                else:
                    del build[missing_path[0]][missing_path[1]]
                atomic_write_json(paths[1], build)
                execution = json.loads(paths[3].read_text(encoding="utf-8"))
                execution["build_receipt_sha256"] = sha256_file(paths[1])
                atomic_write_json(paths[3], execution)
                with self.assertRaises(WorkflowError):
                    finalize(
                        spec=spec, facts_path=paths[0], build_receipt_path=paths[1],
                        case_receipt_path=paths[2], execution_receipt_path=paths[3],
                        requested_physical_device=2,
                        out_path=root / "acceptance.json",
                    )

    def test_execution_command_logs_are_replayed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec, paths = self._write_chain(root, execution_failed=0, accuracy_failed=0)
            execution = json.loads(paths[3].read_text(encoding="utf-8"))
            Path(execution["commands"]["accuracy"]["stderr_path"]).write_text(
                "late worker flush\n", encoding="utf-8"
            )
            with self.assertRaises(WorkflowError) as raised:
                finalize(
                    spec=spec, facts_path=paths[0], build_receipt_path=paths[1],
                    case_receipt_path=paths[2], execution_receipt_path=paths[3],
                    requested_physical_device=2,
                    out_path=root / "acceptance.json",
                )
            self.assertEqual(raised.exception.code, "EVIDENCE_DRIFT")

    def test_explicit_device_binding_and_atk_command_contract_are_replayed(self):
        mutations = (
            "binding_physical", "binding_logical", "binding_environment",
            "wrong_executable", "argv_logical_device", "wrong_task", "wrong_cases",
            "wrong_output", "wrong_timeout", "wrong_concurrency", "wrong_save_data",
            "wrong_environment",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                spec, paths = self._write_chain(root, execution_failed=0, accuracy_failed=0)
                execution = load_json(paths[3])
                if mutation == "binding_physical":
                    execution["device"]["physical_device"] = 1
                    atomic_write_json(paths[3], execution)
                elif mutation == "binding_logical":
                    execution["device"]["logical_device"] = 1
                    atomic_write_json(paths[3], execution)
                elif mutation == "binding_environment":
                    execution["device"]["runtime_environment"][
                        "ASCEND_RT_VISIBLE_DEVICES"
                    ] = "1"
                    atomic_write_json(paths[3], execution)
                elif mutation == "wrong_environment":
                    execution["commands"]["accuracy"]["environment"][
                        "ASCEND_RT_VISIBLE_DEVICES"
                    ] = "1"
                    atomic_write_json(paths[3], execution)
                else:
                    argv = execution["commands"]["accuracy"]["argv"]
                    if mutation == "argv_logical_device":
                        argv[argv.index("--devices") + 1] = "1"
                    elif mutation == "wrong_executable":
                        argv[0] = str(root / "wrong-atk")
                    elif mutation == "wrong_task":
                        argv[argv.index("--task") + 1] = "performance_device"
                    elif mutation == "wrong_cases":
                        argv[2] = str(root / "wrong-cases.json")
                    elif mutation == "wrong_output":
                        argv[argv.index("--output") + 1] = str(root / "wrong-output")
                    elif mutation == "wrong_timeout":
                        argv[argv.index("--timeout") + 1] = "61"
                    elif mutation == "wrong_concurrency":
                        argv[argv.index("--concurrency") + 1] = "2"
                    else:
                        argv[argv.index("--save_data") + 1] = "profile:bin"
                    atomic_write_json(paths[3], execution)
                with self.assertRaises(WorkflowError) as raised:
                    finalize(
                        spec=spec, facts_path=paths[0], build_receipt_path=paths[1],
                        case_receipt_path=paths[2], execution_receipt_path=paths[3],
                        requested_physical_device=2,
                        out_path=root / "acceptance.json",
                    )
                self.assertEqual(raised.exception.code, "EVIDENCE_MISMATCH")

    def test_performance_white_list_command_contract_is_replayed(self):
        for mutation in ("missing", "wrong", "extra"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                spec, paths = self._write_chain(
                    root, execution_failed=0, accuracy_failed=0, performance="measure"
                )
                execution = load_json(paths[3])
                argv = execution["commands"]["performance"]["argv"]
                index = argv.index("--white_list")
                if mutation == "missing":
                    del argv[index:index + 2]
                elif mutation == "wrong":
                    argv[index + 1] = "[1]"
                else:
                    argv.extend(["--white_list", "[0]"])
                atomic_write_json(paths[3], execution)
                with self.assertRaises(WorkflowError) as raised:
                    finalize(
                        spec=spec, facts_path=paths[0], build_receipt_path=paths[1],
                        case_receipt_path=paths[2], execution_receipt_path=paths[3],
                        requested_physical_device=2,
                        out_path=root / "acceptance.json",
                    )
                self.assertEqual(raised.exception.code, "EVIDENCE_MISMATCH")

    def test_execution_plugin_file_identity_is_replayed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec, paths = self._write_chain(root, execution_failed=0, accuracy_failed=0)
            plugin = root / "execution-plugin.py"
            plugin.write_text("# immutable execution adapter\n", encoding="utf-8")
            execution = load_json(paths[3])
            execution["execution_plugin"] = {
                "path": str(plugin), "sha256": sha256_file(plugin),
            }
            execution["commands"]["accuracy"]["argv"].extend([
                "--plugin", str(plugin.resolve()),
            ])
            atomic_write_json(paths[3], execution)
            plugin.write_text("# drifted execution adapter\n", encoding="utf-8")
            with self.assertRaises(WorkflowError) as raised:
                finalize(
                    spec=spec, facts_path=paths[0], build_receipt_path=paths[1],
                    case_receipt_path=paths[2], execution_receipt_path=paths[3],
                    requested_physical_device=2,
                    out_path=root / "acceptance.json",
                )
            self.assertEqual(raised.exception.code, "EVIDENCE_DRIFT")

    def test_execution_plugin_command_argument_is_replayed_exactly(self):
        for mutation in ("missing", "wrong", "extra"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                spec, paths = self._write_chain(root, execution_failed=0, accuracy_failed=0)
                plugin = root / "execution-plugin.py"
                plugin.write_text("# immutable execution adapter\n", encoding="utf-8")
                execution = load_json(paths[3])
                execution["execution_plugin"] = {
                    "path": str(plugin), "sha256": sha256_file(plugin),
                }
                argv = execution["commands"]["accuracy"]["argv"]
                argv.extend(["--plugin", str(plugin.resolve())])
                index = argv.index("--plugin")
                if mutation == "missing":
                    del argv[index:index + 2]
                elif mutation == "wrong":
                    argv[index + 1] = str((root / "wrong-plugin.py").resolve())
                else:
                    argv.extend(["--plugin", str(plugin.resolve())])
                atomic_write_json(paths[3], execution)
                with self.assertRaises(WorkflowError) as raised:
                    finalize(
                        spec=spec, facts_path=paths[0], build_receipt_path=paths[1],
                        case_receipt_path=paths[2], execution_receipt_path=paths[3],
                        requested_physical_device=2,
                        out_path=root / "acceptance.json",
                    )
                self.assertEqual(raised.exception.code, "EVIDENCE_MISMATCH")

    def test_execution_command_requires_process_drain_proof(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec, paths = self._write_chain(root, execution_failed=0, accuracy_failed=0)
            execution = json.loads(paths[3].read_text(encoding="utf-8"))
            del execution["commands"]["accuracy"]["processes_drained"]
            atomic_write_json(paths[3], execution)
            with self.assertRaises(WorkflowError) as raised:
                finalize(
                    spec=spec, facts_path=paths[0], build_receipt_path=paths[1],
                    case_receipt_path=paths[2], execution_receipt_path=paths[3],
                    requested_physical_device=2,
                    out_path=root / "acceptance.json",
                )
            self.assertEqual(raised.exception.code, "EVIDENCE_INCOMPLETE")

    def test_requested_physical_device_is_bound_to_finalizer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec, paths = self._write_chain(root, execution_failed=0, accuracy_failed=0)
            with self.assertRaises(WorkflowError) as raised:
                finalize(
                    spec=spec, facts_path=paths[0], build_receipt_path=paths[1],
                    case_receipt_path=paths[2], execution_receipt_path=paths[3],
                    requested_physical_device=1, out_path=root / "acceptance.json",
                )
            self.assertEqual(raised.exception.code, "EVIDENCE_MISMATCH")

    def test_casegen_command_logs_and_process_drain_are_replayed(self):
        for mutation in ("late_log", "missing_drain"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                spec, paths = self._write_chain(root, execution_failed=0, accuracy_failed=0)
                cases = json.loads(paths[2].read_text(encoding="utf-8"))
                if mutation == "late_log":
                    Path(cases["command"]["stderr_path"]).write_text(
                        "late casegen worker flush\n", encoding="utf-8"
                    )
                    expected = "EVIDENCE_DRIFT"
                else:
                    del cases["command"]["processes_drained"]
                    atomic_write_json(paths[2], cases)
                    execution = json.loads(paths[3].read_text(encoding="utf-8"))
                    execution["case_receipt_sha256"] = sha256_file(paths[2])
                    atomic_write_json(paths[3], execution)
                    expected = "EVIDENCE_INCOMPLETE"
                with self.assertRaises(WorkflowError) as raised:
                    finalize(
                        spec=spec, facts_path=paths[0], build_receipt_path=paths[1],
                        case_receipt_path=paths[2], execution_receipt_path=paths[3],
                        requested_physical_device=2,
                        out_path=root / "acceptance.json",
                    )
                self.assertEqual(raised.exception.code, expected)

    def test_post_build_input_drift_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec, paths = self._write_chain(root, execution_failed=0, accuracy_failed=0)
            build = json.loads(paths[1].read_text(encoding="utf-8"))
            staged_source = Path(build["staged_source_root"])
            generated = staged_source / "third_party" / "dependency.h"
            generated.parent.mkdir()
            generated.write_text("late mutation", encoding="utf-8")
            with self.assertRaises(WorkflowError) as raised:
                finalize(
                    spec=spec, facts_path=paths[0], build_receipt_path=paths[1],
                    case_receipt_path=paths[2], execution_receipt_path=paths[3],
                    requested_physical_device=2,
                    out_path=root / "acceptance.json",
                )
            self.assertEqual(raised.exception.code, "EVIDENCE_DRIFT")

    def test_fresh_elf_and_target_delivery_must_share_vendor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec, paths = self._write_chain(root, execution_failed=0, accuracy_failed=0)
            build = json.loads(paths[1].read_text(encoding="utf-8"))
            install = Path(build["vendor"]["package_install_root"])
            other_vendor = install / "vendors" / "other"
            original_vendor = install / "vendors" / "v"
            shutil.copytree(original_vendor / "op_impl", other_vendor / "op_impl")
            build["vendor"]["custom_opp_root"] = str(other_vendor)
            build["target_delivery"] = _target_delivery(
                install, other_vendor, "ascend910b", "example", "Example"
            )
            atomic_write_json(paths[1], build)
            execution = json.loads(paths[3].read_text(encoding="utf-8"))
            execution["build_receipt_sha256"] = sha256_file(paths[1])
            atomic_write_json(paths[3], execution)
            with self.assertRaises(WorkflowError) as raised:
                finalize(
                    spec=spec, facts_path=paths[0], build_receipt_path=paths[1],
                    case_receipt_path=paths[2], execution_receipt_path=paths[3],
                    requested_physical_device=2,
                    out_path=root / "acceptance.json",
                )
            self.assertEqual(raised.exception.code, "EVIDENCE_MISMATCH")

    def test_execution_failure_is_not_dut_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec, paths = self._write_chain(root, execution_failed=1, accuracy_failed=0)
            result = finalize(
                spec=spec, facts_path=paths[0], build_receipt_path=paths[1],
                case_receipt_path=paths[2], execution_receipt_path=paths[3],
                requested_physical_device=2,
                out_path=root / "acceptance.json",
            )
            self.assertEqual(result["verdict"]["status"], "PLUGIN_ERROR")

    def test_accuracy_execution_failure_keeps_performance_evidence_without_dut_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec, paths = self._write_chain(
                root, execution_failed=1, accuracy_failed=0, performance="measure"
            )
            result = finalize(
                spec=spec, facts_path=paths[0], build_receipt_path=paths[1],
                case_receipt_path=paths[2], execution_receipt_path=paths[3],
                requested_physical_device=2,
                out_path=root / "acceptance.json",
            )
            self.assertEqual(result["verdict"]["status"], "PLUGIN_ERROR")
            self.assertEqual(result["verdict"]["reason_code"], "FAILURE_NOT_ATTRIBUTED_TO_DUT")

    def test_performance_execution_failure_is_evidence_not_a_dut_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec, paths = self._write_chain(
                root, execution_failed=0, accuracy_failed=0, performance="measure"
            )
            execution = json.loads(paths[3].read_text(encoding="utf-8"))
            execution["report"].update({
                "performance_complete": False,
                "performance_missing": 1,
                "performance_execution_failed": 1,
                "performance_execution_failed_ids": [0],
                "performance_rows": [{
                    "id": 0, "execution_status": "FAILED", "device_time_us": None,
                    "accuracy_passed": None, "failure_reason": "runtime error", "case_json": "{}",
                }],
            })
            execution["profiles"] = []
            atomic_write_json(paths[3], execution)
            result = finalize(
                spec=spec, facts_path=paths[0], build_receipt_path=paths[1],
                case_receipt_path=paths[2], execution_receipt_path=paths[3],
                requested_physical_device=2,
                out_path=root / "acceptance.json",
            )
            self.assertEqual(result["verdict"]["status"], "PLUGIN_ERROR")

    def test_finalizer_rejects_duplicate_profiler_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec, paths = self._write_chain(
                root, execution_failed=0, accuracy_failed=0, performance="measure"
            )
            execution = json.loads(paths[3].read_text(encoding="utf-8"))
            execution["profiles"].append(dict(execution["profiles"][0]))
            atomic_write_json(paths[3], execution)
            with self.assertRaises(WorkflowError) as raised:
                finalize(
                    spec=spec, facts_path=paths[0], build_receipt_path=paths[1],
                    case_receipt_path=paths[2], execution_receipt_path=paths[3],
                    requested_physical_device=2,
                    out_path=root / "acceptance.json",
                )
            self.assertEqual(raised.exception.code, "EVIDENCE_INCOMPLETE")

    def test_expected_error_mismatch_is_not_direct_dut_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec, paths = self._write_chain(
                root, execution_failed=0, accuracy_failed=1, expected_error_failure=True
            )
            result = finalize(
                spec=spec, facts_path=paths[0], build_receipt_path=paths[1],
                case_receipt_path=paths[2], execution_receipt_path=paths[3],
                requested_physical_device=2,
                out_path=root / "acceptance.json",
            )
            self.assertEqual(result["verdict"]["status"], "PLUGIN_ERROR")
            self.assertEqual(result["verdict"]["reason_code"], "EXPECTED_ERROR_MISMATCH_NOT_ATTRIBUTED")

    def test_atk_identity_drift_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec, paths = self._write_chain(root, execution_failed=0, accuracy_failed=0)
            execution = json.loads(paths[3].read_text(encoding="utf-8"))
            execution["atk"]["sha256"] = "d" * 64
            atomic_write_json(paths[3], execution)
            with self.assertRaises(WorkflowError):
                finalize(
                    spec=spec, facts_path=paths[0], build_receipt_path=paths[1],
                    case_receipt_path=paths[2], execution_receipt_path=paths[3],
                    requested_physical_device=2,
                    out_path=root / "acceptance.json",
                )


if __name__ == "__main__":
    unittest.main()
