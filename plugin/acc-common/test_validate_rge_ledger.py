#!/usr/bin/env python3
"""Hermetic artifact-projection and mutation tests for the R/G/E ledger."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import cann_version
import content_address
import cpp_extension_adapter
import cpp_extension_identity
import multi_card_shards
import multi_card_verdict_equivalence
import numpy as np
import precision_policy
import source_provenance
import vendor_build_receipt
import validate_rge_ledger as rge
import validator
from test_gen_cases_stochastic import _spec as stochastic_spec
from test_stochastic_adapter import _fixture as stochastic_fixture


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )


def _local_build_receipt(
    vendor_path: Path, vendor_sha: str, source_root: Path,
    subtree_sha: str, full_sha: str, content_anchor: dict,
) -> dict:
    scope = "experimental/math/fixture"
    size = vendor_path.stat().st_size
    return {
        "schema": "oprunway.vendor_build_receipt",
        "schema_version": 3,
        "status": "VERIFIED",
        "degradations": [],
        "source": {
            "provenance_kind": "local_snapshot",
            "declared_source_form": "local_source",
            "repo": str(source_root),
            "pr_head_sha": None,
            "snapshot_subtree_scope": scope,
            "snapshot_sha256": full_sha,
            "snapshot_subtree_sha256": subtree_sha,
            "content_anchor": copy.deepcopy(content_anchor),
            "transport": {"pr_head_sha": None, "repo": str(source_root)},
        },
        "build": {
            "argv": ["bash", "build.sh"],
            "cwd": str(source_root / scope),
            "returncode": 0,
            "returncode_source": "measured",
            "execution": {
                "started_at": "2026-08-07T00:00:00Z",
                "ended_at": "2026-08-07T00:00:01Z",
                "duration_s": 1.0,
                "library_path": str(vendor_path),
                "library_before": None,
                "library_after": {
                    "mtime_ns": 1, "size": size, "sha256": vendor_sha,
                },
            },
            "source_snapshot_digest": {
                "schema": "oprunway.source_snapshot_digest",
                "schema_version": 1,
                "taken_stage": "pre_build",
                "source_root": str(source_root),
                "subtree_scope": scope,
                "snapshot_sha256": full_sha,
                "snapshot_subtree_sha256": subtree_sha,
                "content_anchor": copy.deepcopy(content_anchor),
                "algorithm": {
                    "tool": "fetch_source.py",
                    "logic_sha256": _sha(
                        Path(vendor_build_receipt.__file__).with_name("fetch_source.py")
                    ),
                },
                "file_count": 2,
                "subtree_file_count": 1,
                "skipped_symlink_count": 0,
                "subtree_skipped_symlink_count": 0,
            },
            "tree_state_at_emit": {
                "snapshot_sha256": full_sha,
                "snapshot_subtree_sha256": subtree_sha,
                "matches_pre_build": True,
                "subtree_matches_pre_build": True,
            },
        },
        "artifact": {
            "library_path": str(vendor_path), "library_sha256": vendor_sha,
        },
        "producer": {
            "tool": "vendor_build_receipt.py",
            "logic_sha256": _sha(Path(vendor_build_receipt.__file__)),
        },
    }


def _symbol_identity(vendor_path: Path, vendor_sha: str, plan: dict) -> dict:
    definitions = [
        {
            **item,
            "resolved_via": cpp_extension_identity.RESOLVED_VIA,
            "defining_library": {"path": str(vendor_path), "sha256": vendor_sha},
        }
        for item in cpp_extension_identity.required_symbols(plan)
    ]
    return {
        "schema": cpp_extension_identity.SCHEMA,
        "schema_version": cpp_extension_identity.SCHEMA_VERSION,
        "library": {"path": str(vendor_path), "sha256": vendor_sha},
        "required_symbol_pairs": cpp_extension_identity.required_symbol_pairs(plan),
        "definitions": definitions,
    }


def _materialize_work(
    work: Path,
    spec: dict,
    caseset: dict,
    statuses: dict[str, str],
    vendor_path: Path,
    build_receipt: dict,
    cann_elf: Path,
) -> tuple[dict, dict]:
    work.mkdir(parents=True)
    bundle = work / "cpp_extension"
    bundle.mkdir()
    csrc = bundle / "csrc"
    csrc.mkdir()
    (csrc / "oprunway_extension.cpp").write_text(
        "return aclCreateTensorFunc(nullptr, 0, 0, nullptr, 0, nullptr, "
        "ACL_FORMAT_ND, nullptr);\n",
        encoding="utf-8",
    )
    extension = bundle / "fixture_extension.so"
    extension.write_bytes(b"fixture-extension")
    manifest = {
        "schema": "oprunway.cpp_extension_manifest",
        "schema_version": 1,
        "namespace": "oprunway_fixture",
        "spec_sha256": rge._canonical_sha256(spec),
        "variants": [{"entrypoint": "invoke_v0", "stage2_form": "standard"}],
        "tensor_acl_format": "nd",
        "tensor_acl_format_source": "spec_declared",
        "tensor_format_receipt": {
            "requested": "nd",
            "effective_acl_format": "ACL_FORMAT_ND",
            "source": "spec_declared",
        },
        "degradations": [],
    }
    plan = {
        "schema": "oprunway.cpp_extension_invocation_plan",
        "schema_version": 1,
        "cases": [
            {
                "case_id": case["id"], "symbol": "Fixture",
                "entrypoint": "invoke_v0", "slots": [],
            }
            for case in caseset["cases"]
        ],
    }
    _write_json(bundle / "extension_manifest.json", manifest)
    _write_json(work / "cpp_extension_invocation_plan.json", plan)
    _write_json(work / "caseset.json", caseset)
    _write_json(work / "cpp_extension_caseset.json", caseset)
    vendor_sha = _sha(vendor_path)
    observation = cann_version.normalize_observation("9.0.1")
    observation["probe"] = {
        "api": cann_version.PROBE_API,
        "package": cann_version.PROBE_PACKAGE,
        "returncode": 0,
        "returncode_source": cann_version.PROBE_RETURN_MEASURED,
        "defining_elf": {"path": str(cann_elf), "sha256": _sha(cann_elf)},
    }
    identity = _symbol_identity(vendor_path, vendor_sha, plan)
    symbols = [item["symbol"] for item in cpp_extension_identity.required_symbols(plan)]
    produced = [case_id for case_id, status in statuses.items() if status == "ok"]
    failed = [case_id for case_id, status in statuses.items()
              if status == "execution_failed"]
    receipt = {
        "schema": "oprunway.cpp_extension_receipt",
        "schema_version": cann_version.RECEIPT_SCHEMA_VERSION,
        "status": "VERIFIED",
        "bindings": {
            "caseset_sha256": rge._canonical_sha256(caseset),
            "manifest_sha256": rge._canonical_sha256(manifest),
            "invocation_plan_sha256": rge._canonical_sha256(plan),
            "spec_sha256": rge._canonical_sha256(spec),
        },
        "artifact": {
            "path": "cpp_extension/fixture_extension.so", "sha256": _sha(extension),
        },
        "load": {
            "success": True,
            "loader": "torch.ops.load_library",
            "namespace": "oprunway_fixture",
            "schemas": {"invoke_v0": "oprunway_fixture::invoke_v0() -> ()"},
        },
        "runtime": {
            "torch_version": "2.x", "torch_npu_version": "2.x",
            "cann_version": "9.0.1", "cann": observation,
            "soc": "ascend910_93",
            "ascend_custom_opp_path": str(vendor_path.parents[2]),
        },
        "build": {"argv": ["fixture-extension-build"], "returncode": 0},
        "vendor": {
            "library_path": str(vendor_path), "library_sha256": vendor_sha,
            "symbols_owned": symbols, "symbol_identity": identity,
            "build_receipt": build_receipt,
            "build_receipt_sha256": rge._canonical_sha256(build_receipt),
        },
        "invocation": cpp_extension_adapter.build_invocation_accounting(
            plan, produced_case_ids=produced, failed_case_ids=failed),
        "tensor_format_receipt": copy.deepcopy(manifest["tensor_format_receipt"]),
    }
    _write_json(work / "cpp_extension_receipt.json", receipt)
    receipt_sha = rge._canonical_sha256(receipt)
    rows = []
    for case in caseset["cases"]:
        case_id, status = case["id"], statuses[case["id"]]
        row = {
            "case_id": case_id, "status": status,
            "cpp_extension_receipt_sha256": receipt_sha,
        }
        if status == "execution_failed":
            row["error"] = "fixture execution failure"
        elif status == "ok":
            case_dir = work / case_id
            case_dir.mkdir()
            golden = np.arange(2, dtype=np.float32)
            out = golden.copy()
            golden_path = case_dir / "golden.npy"
            out_path = case_dir / "out.npy"
            np.save(golden_path, golden)
            np.save(out_path, out)
            expected = case["expected"]
            row.update(
                {
                    "output_written_check": "passed",
                    "precision": {
                        "standard": expected["standard"],
                        "tolerance_policy_id": expected["tolerance_policy_id"],
                        "policy": copy.deepcopy(expected["policy"]),
                        "threshold": expected["threshold"],
                        "metrics": precision_policy.compute_metrics(
                            out, golden, expected["policy"]
                        ),
                        "oracle_source": "analytical_ref",
                        "out_shape": [2],
                        "out_dtype": "float32",
                        "golden_path": f"{case_id}/golden.npy",
                        "out_path": f"{case_id}/out.npy",
                        "provenance": {
                            "golden_sha256": _sha(golden_path),
                            "out_sha256": _sha(out_path),
                            "numel": 2,
                            "out_shape": [2],
                        },
                    },
                }
            )
        rows.append(row)
    envelope = {
        "op": caseset["op"], "runner_form": "cpp_extension",
        "runner_source": "generated_official_cpp_extension",
        "source_provenance": vendor_build_receipt.summarize(build_receipt),
        "cpp_extension_receipt": receipt, "evidence": rows,
    }
    _write_json(work / "evidence.json", envelope)
    return receipt, envelope


def _materialize_n0_fixture(root: Path, ledger: dict) -> None:
    """Attach a real, fully replayable N0 static-gate bundle to ``ledger``."""

    operator = ledger["operators"][0]
    artifacts = operator["artifacts"]
    n0_root = root / "n0"
    op_root = n0_root / "fixture"
    (op_root / "logs").mkdir(parents=True)
    (op_root / "receipts").mkdir()
    (op_root / "work").mkdir()
    for relative, formal_name in (
        ("spec.json", "spec"),
        ("source_facts.json", "source_facts"),
        ("task_doc.md", "taskdoc_snapshot"),
    ):
        source = Path(artifacts[formal_name]["path"])
        (op_root / relative).write_bytes(source.read_bytes())
    _write_json(op_root / "taskdoc_validation.json", {"schema": "fixture.input"})
    spec = json.loads((op_root / "spec.json").read_text(encoding="utf-8"))
    facts = json.loads((op_root / "source_facts.json").read_text(encoding="utf-8"))
    spec_change = {
        "schema": "oprunway.spec_change_receipt",
        "schema_version": 1,
        "spec_sha256": _sha(op_root / "spec.json"),
        "previous_spec_sha256": "0" * 64,
        "change_reason": "hermetic fixture",
        "confirmed_by": "lys",
    }
    _write_json(op_root / "work/spec_change_receipt.json", spec_change)
    taskdoc_payload = {
        "status": "PASSED",
        "blocking_items": [],
        "errors": [],
        "bindings": {
            "taskdoc_bytes_sha256": _sha(op_root / "task_doc.md"),
            "source_facts_digest": facts["digest"],
        },
    }
    taskdoc_receipt = content_address.make_artifact(
        "oprunway/taskdoc-validation-receipt/v1", taskdoc_payload
    )
    _write_json(op_root / "taskdoc-validation-receipt.json", taskdoc_receipt)
    dry = {
        "schema": "oprunway.gen_cases.dry_run_ledger",
        "schema_version": 1,
        "spec_binding": {"op": spec["op"], "sha256": rge._canonical_sha256(spec)},
        "planner_binding": {
            "implementation": "gen_cases.py::_plan",
            "gen_cases_py_sha256": _sha(Path(rge.__file__).parent / "gen_cases.py"),
        },
        "planning": {"case_target": operator["required"]["case_target"]},
        "summary": {"emitted": operator["generated"]["case_count"]},
    }
    dry["ledger_digest"] = content_address.content_digest(
        "oprunway/case-plan/v1", dry
    )
    _write_json(op_root / "dry-run-ledger.json", dry)

    output_by_gate = {
        "validate_taskdoc_input": op_root / "taskdoc-validation-receipt.json",
        "gen_cases_dry_run": op_root / "dry-run-ledger.json",
        "spec_change_gate_check": op_root / "work/spec_change_receipt.json",
    }
    args_by_gate = {
        "validate_taskdoc_input": [
            "--root", str(op_root), "--out", "taskdoc-validation-receipt.json"
        ],
        "gen_cases_dry_run": [
            str(op_root / "spec.json"),
            "--dry-run",
            "--ledger-out",
            str(op_root / "dry-run-ledger.json"),
        ],
        "spec_change_gate_check": [
            "--spec",
            str(op_root / "spec.json"),
            "--out",
            str(op_root),
            "--check",
        ],
    }
    gates = {}
    for gate_name, script_name in rge._N0_GATE_SCRIPTS.items():
        log = op_root / "logs" / f"{gate_name}.log"
        rc = op_root / "logs" / f"{gate_name}.rc"
        log.write_text(f"{gate_name}: PASSED\n", encoding="utf-8")
        rc.write_text("0\n", encoding="ascii")
        output = output_by_gate[gate_name]
        execution = {
            "schema": "oprunway.n0_static_gate_execution",
            "schema_version": 1,
            "gate": gate_name,
            "command": [
                "/usr/bin/python3",
                str(root / "plugin/acc-common" / script_name),
                *args_by_gate[gate_name],
            ],
            "returncode": 0,
            "passed": True,
            "log": {"path": str(log), "sha256": _sha(log), "bytes": log.stat().st_size},
            "rc_artifact": {"path": str(rc), "sha256": _sha(rc)},
            "outputs": [
                {
                    "path": str(output),
                    "exists": True,
                    "sha256": _sha(output),
                    "bytes": output.stat().st_size,
                }
            ],
        }
        execution_path = op_root / "receipts" / f"{gate_name}.execution.json"
        _write_json(execution_path, execution)
        gates[gate_name] = {
            **execution,
            "execution_receipt": {
                "path": str(execution_path), "sha256": _sha(execution_path)
            },
        }
    inputs = {}
    for relative in (
        "spec.json",
        "source_facts.json",
        "task_doc.md",
        "taskdoc_validation.json",
        "work/spec_change_receipt.json",
    ):
        path = op_root / relative
        inputs[relative] = {
            "path": str(path), "sha256": _sha(path), "bytes": path.stat().st_size
        }
    operator_receipt = {
        "schema": "oprunway.n0_static_operator_receipt",
        "schema_version": 1,
        "operator": "fixture",
        "scope": "static_only_no_build_no_dut",
        "inputs": inputs,
        "gates": gates,
        "all_passed": True,
    }
    operator_receipt_path = op_root / "n0-static-receipt.json"
    _write_json(operator_receipt_path, operator_receipt)
    batch = {
        "schema": "oprunway.n0_static_gate_batch",
        "schema_version": 1,
        "scope": "static_only_no_build_no_dut",
        "plugin": {
            "root": str(root / "plugin"),
            "files": {
                filename: _sha(Path(rge.__file__).parent / filename)
                for filename in rge._N0_GATE_SCRIPTS.values()
            },
        },
        "operators": {
            "fixture": {
                "root": str(op_root),
                "receipt": str(operator_receipt_path),
                "receipt_sha256": _sha(operator_receipt_path),
                "all_passed": True,
                "gate_returncodes": {
                    gate_name: 0 for gate_name in rge._N0_GATE_NAMES
                },
            }
        },
        "all_passed": True,
    }
    batch_path = n0_root / "n0-static-batch-receipt.json"
    _write_json(batch_path, batch)
    ledger["supplemental_artifacts"].update(
        {
            "n0_static_batch": {"path": str(batch_path), "sha256": _sha(batch_path)},
            "n0_static_fixture": {
                "path": str(operator_receipt_path),
                "sha256": _sha(operator_receipt_path),
            },
        }
    )
    ledger["n0_static_gate_evidence"] = {
        "status": "VERIFIED",
        "batch_artifact": "n0_static_batch",
        "operators": [
            {
                "operator_id": "fixture",
                "batch_key": "fixture",
                "receipt_artifact": "n0_static_fixture",
            }
        ],
    }
    ledger["workflow_plan_evidence"][0]["evidence_refs"].extend(
        {"kind": "supplemental_artifact", "artifact": name}
        for name in ("n0_static_batch", "n0_static_fixture")
    )


def _fixture(root: Path, *, transport_variant: bool = False) -> dict:
    formal = root / "formal"
    formal.mkdir()
    taskdoc = formal / "task_doc.snapshot.md"
    taskdoc.write_text("fixture taskbook\n", encoding="utf-8")
    taskdoc_sha = _sha(taskdoc)
    subtree_sha = "2" * 64
    full_sha = "3" * 64
    content_anchor = {
        "schema": "oprunway.source_content_anchor",
        "schema_version": 1,
        "algorithm": "git_blob_manifest_sha256_v1",
        "scope": "experimental/math/fixture",
        "sha256": "4" * 64,
        "file_count": 1,
    }
    vendor_elf = (
        root / "vendor-root" / "vendors" / "fixture" / "op_api" / "lib"
        / "libcust_opapi.so"
    )
    vendor_elf.parent.mkdir(parents=True)
    vendor_elf.write_bytes(b"vendor")
    vendor_elf_sha = _sha(vendor_elf)
    cann_elf = root / "libascendcl.so"
    cann_elf.write_bytes(b"runtime-acl")
    cann_elf_sha = _sha(cann_elf)

    source_payload = {
        "completeness": {
            "status": "complete", "reasons": [],
            "transport_warnings": (
                ["transport_locator_unverified"]
                if transport_variant
                else ["local_snapshot_has_no_remote_locator"]
            ),
        },
        "contract_version": 2,
        "input_association": copy.deepcopy(rge._CALLER_INPUT_ASSOCIATION),
        "derived": {
            "op": "Fixture",
            "aclnn_entry": "aclnnFixture",
            "target_dir": "experimental/math/fixture",
            "aclnn_headers": ["experimental/math/fixture/op_host/fixture.h"],
            "interface_kind": "aclnn_2stage",
        },
        "pr": {
            "canonical_url": (
                "https://example.invalid/not-an-identity-root"
                if transport_variant else None
            ),
            "source_repo": None,
            "number": None,
            "head_repo": "unresolvable/fork" if transport_variant else None,
            "head_sha": "f" * 40 if transport_variant else None,
            "is_fork": None,
            "state": None,
            "provenance_kind": "gitcode_pr" if transport_variant else "local_snapshot",
            "snapshot_merkle_sha256": subtree_sha,
            "snapshot_scope": "experimental/math/fixture",
            "content_anchor": copy.deepcopy(content_anchor),
        },
        "taskdoc": {
            "bytes_sha256": taskdoc_sha, "snapshot_sha256": taskdoc_sha,
            "size": taskdoc.stat().st_size, "source_locator": "task_doc.snapshot.md",
        },
        "changed_files": ["experimental/math/fixture/op_host/fixture.h"],
        "key_files": [{
            "path": "experimental/math/fixture/op_host/fixture.h",
            "ref": "local_snapshot", "bytes_sha256": "2" * 64, "size": 9,
        }],
        "producer": {"tool": "fetch_source.py", "logic_sha256": "3" * 64},
    }
    source_facts = content_address.make_artifact(rge._SOURCE_FACTS_DOMAIN, source_payload)
    source_path = formal / "source_facts.json"
    _write_json(source_path, source_facts)

    perf_gap = {
        "kind": "performance_requirement_unvalidated",
        "dimension": "performance",
        "status": "unvalidated",
        "requirement_type": "no_regression",
        "taskdoc_snapshot_sha256": taskdoc_sha,
    }
    dtype_gap = {
        "kind": "dtype_unsupported_by_op_def",
        "dtypes": ["int64"],
        "op_def_dtypes": ["float32"],
        "task_doc_ref": "fixture taskbook dtype table",
        "op_def_ref": "fixture op_def dtype table",
    }
    spec = {
        "op": "Fixture",
        "runner_form": "cpp_extension",
        "declared_source_form": "local_source",
        "dtype_required": ["float32", "int64"],
        "hardware": ["Atlas A2", "Atlas A3", "Atlas A5"],
        "runtime_requirements": {"cann": {"kind": "not_declared"}},
        "params": [
            {"name": "x", "io": "in", "dtype": ["float32"]},
            {"name": "y", "io": "out", "dtype": ["float32"]},
        ],
        "verify_mode": "numerical",
        "precision": {
            "case_target": 3,
            "oracle": "ascendoptest",
            "standard": "ascendoptest_default",
        },
        "perf": {"mode": "measure_only"},
        "task_pr_gaps": [perf_gap, dtype_gap],
    }
    spec_path = formal / "spec.json"
    _write_json(spec_path, spec)
    case_ids = ["case0", "case1", "case2"]
    policy = precision_policy.threshold_for("ascendoptest_default", "float32")
    expected = {
        "verify_mode": "numerical",
        "compare_dtype": "float32",
        "standard": "ascendoptest_default",
        "tolerance_policy_id": "ascendoptest_default:float32",
        "policy": policy,
        "threshold": precision_policy.threshold_digest(policy),
        "golden_source": "numpy reference",
        "out_shape": [2],
    }
    caseset = {
        "op": "Fixture",
        "spec_ref": "Fixture",
        "dtype_required": ["float32", "int64"],
        "dtype_tested": ["float32"],
        "task_pr_gaps": [perf_gap, dtype_gap],
        "pool_max": 3,
        "requested_target": 3,
        "emitted": 3,
        "perf_case_policy": {
            "selection": {
                "selected_total": 2,
                "precision_total": 3,
                "selected_case_ids": case_ids[:2],
            }
        },
        "cases": [
            {
                "id": case_id,
                "dims": ["功能", "精度"],
                "inputs": [
                    {"name": "x", "dtype": "float32", "shape": [2]}
                ],
                "attrs": {},
                "expected": copy.deepcopy(expected),
            }
            for case_id in case_ids
        ],
    }
    caseset_path = formal / "caseset.json"
    _write_json(caseset_path, caseset)
    spec_contract_sha = rge._canonical_sha256(spec)
    caseset_contract_sha = rge._canonical_sha256(caseset)

    source_root = root / "local-source"
    build_receipt = _local_build_receipt(
        vendor_elf, vendor_elf_sha, source_root, subtree_sha, full_sha,
        content_anchor)
    build_path = root / "vendor-build-receipt.json"
    _write_json(build_path, build_receipt)
    formal_work = formal / "work"
    receipt, evidence = _materialize_work(
        formal_work, spec, caseset,
        {"case0": "ok", "case1": "ok", "case2": "execution_failed"},
        vendor_elf, build_receipt, cann_elf)
    invocation = receipt["invocation"]
    receipt_path = formal_work / "cpp_extension_receipt.json"
    evidence_path = formal / "evidence.json"
    _write_json(evidence_path, evidence)
    verdict = validator.validate(spec, caseset, evidence)
    verdict_path = formal / "verdict.json"
    _write_json(verdict_path, verdict)
    perf = {
        "op": "Fixture",
        "perf_mode": "measure_only",
        "per_case": [
            {"case_id": "case0", "npu_us": 1.0, "blocked": False},
            {"case_id": "case1", "npu_us": None, "blocked": True},
        ],
        "summary": {"perf_cases": 2, "measured": 1, "blocked": 1, "status": "blocked"},
    }
    perf_path = formal / "perf_report.json"
    _write_json(perf_path, perf)
    acceptance = {
        "op": "Fixture",
        "overall": "FAIL(精度)",
        "state": "FAILED_PRECISION",
        "exit_code": 1,
        "requires_human_cp": False,
        "repo_mode": "cpp_extension",
        "gate": {"passed": True, "errors": {}},
        "precision_verdict": "fail",
        "perf_status": "blocked",
        "perf_mode": "measure_only",
    }
    acceptance_path = formal / "acceptance.json"
    _write_json(acceptance_path, acceptance)
    gate1 = root / "gate-task1.log"
    gate2 = root / "gate-task2.log"
    gate1.write_text("=== 验收门 stage=task1 ===\nSTATUS: PASSED\n", encoding="utf-8")
    gate2.write_text("=== 验收门 stage=task2 ===\nSTATUS: PASSED\n", encoding="utf-8")

    artifact_paths = {
        "taskdoc_snapshot": taskdoc,
        "source_facts": source_path,
        "spec": spec_path,
        "caseset": caseset_path,
        "precision_evidence": evidence_path,
        "verdict": verdict_path,
        "perf_report": perf_path,
        "acceptance": acceptance_path,
        "extension_receipt": receipt_path,
        "vendor_build_receipt": build_path,
        "vendor_elf": vendor_elf,
        "gate_task1": gate1,
        "gate_task2": gate2,
    }
    artifacts = {
        name: {"path": str(path), "sha256": _sha(path)}
        for name, path in artifact_paths.items()
    }

    test_paths = sorted({path for values in rge._PLAN_REQUIRED_TESTS.values() for path in values})
    regression_log = root / "regression.log"
    regression_log.write_text(
        "".join(
            f"test_ok ({Path(path).stem}.Fixture.test_ok) ... ok\n" for path in test_paths
        )
        + f"\nRan {len(test_paths)} tests in 0.001s\n\nOK\n",
        encoding="utf-8",
    )
    regression_rc = root / "regression.rc"
    regression_rc.write_text("0\n", encoding="utf-8")
    online_taskdoc = root / "online-taskdoc.md"
    online_taskdoc.write_text("online fixture taskbook\n", encoding="utf-8")
    online_taskdoc_sha = _sha(online_taskdoc)
    online_manifest = {
        "repository": "fork/fixture",
        "ref": "1" * 40,
        "target_dir": "experimental/math/fixture",
        "sha256": "7" * 64,
        "file_count": 2,
    }
    online_payload = {
        "completeness": {"status": "complete", "reasons": [], "form_facts": []},
        "declared_source_form": "git_pr",
        "derived": {"target_dir": "experimental/math/fixture"},
        "pr": {
            "canonical_url": "https://gitcode.com/base/repo/merge_requests/1",
            "head_repo": "fork/fixture",
            "head_sha": "1" * 40,
            "head_target_manifest": online_manifest,
            "number": 1,
            "provenance_kind": "gitcode_pr",
        },
        "taskdoc": {"snapshot_sha256": online_taskdoc_sha},
    }
    online_source_path = root / "online-source.json"
    _write_json(
        online_source_path,
        content_address.make_artifact(rge._SOURCE_FACTS_DOMAIN, online_payload),
    )
    online_pr_path = root / "online-pr.json"
    _write_json(
        online_pr_path,
        {
            "pr_url": "https://gitcode.com/base/repo/merge_requests/1",
            "head": "feature",
            "head_sha": "1" * 40,
            "head_repo": "fork/fixture",
            "target_dir": "experimental/math/fixture",
            "head_target_manifest": online_manifest,
        },
    )
    online_log = root / "online.log"
    online_log.write_text("facts completeness=complete\n", encoding="utf-8")
    online_rc = root / "online.rc"
    online_rc.write_text("0\n", encoding="utf-8")
    multicard_root = root / "multicard-v4"
    multicard_root.mkdir()
    (multicard_root / "work").mkdir()
    manifest = multi_card_shards.build_manifest(
        spec, caseset, source_facts, [1, 2]
    )
    evidence_by_id = {row["case_id"]: row for row in evidence["evidence"]}
    shard_results = []
    shard_envelopes = []
    shard_inventory = []

    def inventory_item(path: Path, base: Path, role: str) -> dict:
        return {
            "role": role,
            "path": str(path.relative_to(base)),
            "sha256": _sha(path),
            "bytes": path.stat().st_size,
        }

    def recursive_rows(work: Path, prefix: str, base: Path) -> list[dict]:
        return [
            inventory_item(path, base, f"{prefix}.file:{path.relative_to(work)}")
            for path in sorted(work.rglob("*")) if path.is_file()
        ]

    for shard in manifest["shards"]:
        shard_root = multicard_root / shard["shard_id"]
        projected = multi_card_shards.subset_caseset(
            caseset,
            shard["case_ids"],
            manifest=manifest,
            shard=shard,
            work_dir=str(shard_root),
        )
        statuses = {
            case_id: evidence_by_id[case_id]["status"] for case_id in shard["case_ids"]
        }
        shard_receipt, shard_envelope = _materialize_work(
            shard_root, spec, projected, statuses,
            vendor_elf, build_receipt, cann_elf)
        smoke_root = multicard_root / f"{shard['shard_id']}.pre-smoke"
        smoke_caseset = copy.deepcopy(projected["common_smoke_caseset_template"])
        smoke_caseset["work_dir"] = str(smoke_root)
        smoke_statuses = {case["id"]: "ok" for case in smoke_caseset["cases"]}
        smoke_receipt, smoke_envelope = _materialize_work(
            smoke_root, spec, smoke_caseset, smoke_statuses,
            vendor_elf, build_receipt, cann_elf)
        for row in smoke_envelope["evidence"]:
            row["output_written_check"] = "passed"
        _write_json(smoke_root / "evidence.json", smoke_envelope)
        identity = {
            "device_id": shard["device_id"],
            "current_device": shard["device_id"],
            "soc": "ascend910_93",
            "cann_version": "9.0.1",
            "dut_library_sha256": vendor_elf_sha,
            "dut_symbol_identity_sha256": rge._canonical_sha256(
                shard_receipt["vendor"]["symbol_identity"]),
            "source_provenance_sha256": shard_receipt["vendor"]["build_receipt_sha256"],
            "pre_execution_device_smoke": {
                "schema": "oprunway.multi_card_pre_execution_device_smoke",
                "schema_version": 2,
                "device_id": shard["device_id"],
                "current_device": shard["device_id"],
                "work_dir_initially_absent": True,
                "successful_output_case_ids": manifest["common_smoke_case_ids"],
                "common_smoke_case_ids": manifest["common_smoke_case_ids"],
                "common_smoke_caseset_sha256": multi_card_shards.digest(
                    projected["common_smoke_caseset_template"]),
                "work_dir": str(smoke_root),
                "cpp_extension_receipt_sha256": rge._canonical_sha256(smoke_receipt),
                "dut_library_sha256": vendor_elf_sha,
                "symbol_identity_sha256": rge._canonical_sha256(
                    smoke_receipt["vendor"]["symbol_identity"]),
                "soc": "ascend910_93", "cann_version": "9.0.1",
                "source_provenance_sha256": smoke_receipt["vendor"][
                    "build_receipt_sha256"],
            },
        }
        result = multi_card_shards.build_result(
            manifest, shard, projected, shard_envelope, identity
        )
        shard_results.append(result)
        shard_envelopes.append(shard_envelope)
        _write_json(shard_root / "device_identity.json", identity)
        _write_json(shard_root / "shard_result.json", result)
        shard_inventory.append(
            {
                "shard_id": shard["shard_id"], "device_id": shard["device_id"],
                "formal_work": shard["shard_id"],
                "formal_artifacts": recursive_rows(
                    shard_root, f"{shard['shard_id']}.formal", multicard_root),
                "smoke_work": f"{shard['shard_id']}.pre-smoke",
                "smoke_artifacts": recursive_rows(
                    smoke_root, f"{shard['shard_id']}.smoke", multicard_root),
                "formal_receipt_sha256": _sha(
                    shard_root / "cpp_extension_receipt.json"),
                "smoke_receipt_sha256": _sha(
                    smoke_root / "cpp_extension_receipt.json"),
                "result_sha256": _sha(shard_root / "shard_result.json"),
                "execution_identity_sha256": _sha(
                    shard_root / "device_identity.json"),
            }
        )

    merged = multi_card_shards.merge_results(
        manifest, shard_results, spec, caseset, source_facts
    )
    for case_id in case_ids:
        for filename in ("golden.npy", "out.npy"):
            source = formal_work / case_id / filename
            if not source.is_file():
                continue
            target = multicard_root / "work" / case_id / filename
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(source.read_bytes())
    multi_envelope = multi_card_shards.assemble_envelope(
        manifest, merged, shard_envelopes
    )
    top_values = {
        "manifest.json": manifest,
        "multi_card_manifest.json": manifest,
        "merged.json": merged,
        "multi_card_merged_evidence.json": merged,
        "evidence.json": multi_envelope,
        "verdict.json": verdict,
    }
    top_paths = {}
    for name, value in top_values.items():
        path = multicard_root / name
        _write_json(path, value)
        top_paths[name] = path
    for name, value in (
        ("spec.json", spec), ("parent-caseset.json", caseset),
        ("caseset.json", caseset), ("source_facts.json", source_facts),
        ("vendor-build-receipt.json", build_receipt),
    ):
        path = multicard_root / name
        _write_json(path, value)
        top_paths[name] = path
    equivalence = multi_card_verdict_equivalence.build_equivalence(
        verdict,
        verdict,
        manifest,
        spec,
        caseset,
        source_facts,
        merged,
        evidence,
        receipt,
        str(formal_work),
        multi_sha=_sha(top_paths["verdict.json"]),
        single_sha=_sha(verdict_path),
        manifest_sha=_sha(top_paths["multi_card_manifest.json"]),
    )
    equivalence_path = multicard_root / "equivalence.json"
    _write_json(equivalence_path, equivalence)
    gate_log = multicard_root / "gate-task2.log"
    gate_log.write_text(
        "=== 验收门 stage=task2 ===\nSTATUS: PASSED\n", encoding="utf-8"
    )
    gate_rc = multicard_root / "gate-task2.rc"
    gate_rc.write_text("0\n", encoding="utf-8")
    top_paths.update(
        {
            "equivalence.json": equivalence_path,
            "gate-task2.log": gate_log,
            "gate-task2.rc": gate_rc,
        }
    )
    role_paths = {
        "parent_spec": "spec.json", "parent_caseset": "parent-caseset.json",
        "staged_caseset": "caseset.json", "source_facts": "source_facts.json",
        "vendor_build_receipt": "vendor-build-receipt.json",
        "manifest_alias": "manifest.json", "multi_manifest": "multi_card_manifest.json",
        "merged_alias": "merged.json", "merged_receipt": "multi_card_merged_evidence.json",
        "assembled_evidence": "evidence.json", "verdict": "verdict.json",
        "equivalence": "equivalence.json", "gate_log": "gate-task2.log",
        "gate_rc": "gate-task2.rc",
    }
    external_rows = []
    for shard in manifest["shards"]:
        for stage in ("formal", "smoke"):
            for role, path in (
                ("vendor_elf", vendor_elf), ("cann_defining_elf", cann_elf)):
                external_rows.append({
                    "role": f"{shard['shard_id']}.{stage}.{role}",
                    "path": str(path), "sha256": _sha(path),
                    "bytes": path.stat().st_size,
                })
    for role, path in (("vendor_elf", vendor_elf), ("cann_defining_elf", cann_elf)):
        external_rows.append({
            "role": f"single.{role}", "path": str(path), "sha256": _sha(path),
            "bytes": path.stat().st_size,
        })
    multicard_inventory = multicard_root / rge._MULTICARD_INVENTORY_FILENAME
    _write_json(
        multicard_inventory,
        {
            "schema": rge._MULTICARD_INVENTORY_SCHEMA,
            "schema_version": rge._MULTICARD_INVENTORY_VERSION,
            "artifact_root": str(multicard_root),
            "generated_from": {
                "task2_gate_replay_entry":
                    "validate_acceptance_state._gate_multi_card_receipt",
                "equivalence_replay_entry":
                    "multi_card_verdict_equivalence.build_equivalence",
                "inventory_scope": "precision_task2_transitive_closure",
            },
            "artifacts": [
                inventory_item(multicard_root / relative, multicard_root, role)
                for role, relative in sorted(role_paths.items())
            ],
            "top_work_artifacts": recursive_rows(
                multicard_root / "work", "top_work", multicard_root),
            "shards": shard_inventory,
            "single_root": str(formal),
            "single_root_artifacts": recursive_rows(
                formal, "single_root", formal
            ),
            "single_equivalence_inputs": [
                inventory_item(formal / relative, formal, role)
                for role, relative in sorted(
                    rge._MULTICARD_SINGLE_INPUT_PATHS.items()
                )
            ],
            "single_work": str(formal_work),
            "single_artifacts": recursive_rows(formal_work, "single", formal_work),
            "external_artifacts": external_rows,
        },
    )
    supplemental_paths = {
        "regression_log": regression_log,
        "regression_rc": regression_rc,
            "multicard_v4_inventory": multicard_inventory,
    }
    supplemental_artifacts = {
        name: {"path": str(path), "sha256": _sha(path)}
        for name, path in supplemental_paths.items()
    }

    phases = []
    for index in range(11):
        phase_id = f"N{index}"
        refs = [
            {"kind": "artifact", "operator_id": "fixture", "artifact": artifact}
            for artifact in sorted(rge._PLAN_ALL_OPERATOR_ARTIFACTS.get(phase_id, ()))
        ]
        if phase_id != "N8":
            refs.extend(
                {"kind": "artifact", "operator_id": "fixture", "artifact": artifact}
                for artifact in sorted(rge._PLAN_ANY_OPERATOR_ARTIFACTS.get(phase_id, ()))
            )
        refs.extend(
            {
                "kind": "repository_test",
                "path": path,
                "environment": "A3",
                "result": "PASSED",
                "log_artifact": "regression_log",
                "rc_artifact": "regression_rc",
            }
            for path in sorted(rge._PLAN_REQUIRED_TESTS.get(phase_id, ()))
        )
        phases.append(
            {
                "phase": phase_id,
                "title": f"phase {index}",
                "status": "VERIFIED",
                "evidence_refs": refs,
                "structured_gap_ids": [],
            }
        )
    phases[5].update(
        {
            "status": "VERIFIED_WITH_STRUCTURED_GAPS",
            "structured_gap_ids": ["fixture.dtype.missing"],
        }
    )
    phases[9].update(
        {
            "status": "VERIFIED_WITH_STRUCTURED_GAPS",
            "structured_gap_ids": ["fixture.performance.unvalidated"],
        }
    )
    phases[8].update(
        {
            "status": "PARTIALLY_VERIFIED",
            "structured_gap_ids": ["fixture.stochastic.not_exercised"],
        }
    )
    phases[10].update(
        {
            "status": "VERIFIED_WITH_STRUCTURED_GAPS",
            "structured_gap_ids": ["fixture.hardware.missing"],
        }
    )
    phases[10]["evidence_refs"].extend(
        {"kind": "supplemental_artifact", "artifact": name}
        for name in ("multicard_v4_inventory",)
    )

    ledger = {
        "schema": rge.SCHEMA,
        "schema_version": rge.SCHEMA_VERSION,
        "recorded_at": "2026-08-07",
        "authority": {
            "actual_taskbook_input_forms": ["local_file"],
            "runtime_cann": {
                "status": "MEASURED",
                "version": "9.0.1",
                "probe_api": "aclsysGetCANNVersion",
                "library_sha256": cann_elf_sha,
            },
        },
        "supplemental_artifacts": supplemental_artifacts,
        "multi_card_precision_equivalence": [
            {
                "operator_id": "fixture",
                "status": "VERIFIED",
                "execution_scope": "precision_only",
                "inventory_artifact": "multicard_v4_inventory",
                "partition": "case_order_round_robin_v1",
                "devices": [1, 2],
                "shard_sizes": [2, 1],
                "shard_produced": [1, 1],
                "shard_failed": [1, 0],
                "merged_total": 3,
                "merged_passed": 2,
                "merged_failed": 1,
                "single_verdict_sha256": artifacts["verdict"]["sha256"],
                "multi_verdict_sha256": artifacts["verdict"]["sha256"],
                "equivalent": True,
                "performance_collected": False,
                "artifact_refs": ["multicard_v4_inventory"],
            }
        ],
        "regression_evidence": [
            {
                "evidence_id": "fixture-regression",
                "result": "OK",
                "tests_run": len(test_paths),
                "skipped": 0,
                "artifact": "regression_log",
                "rc_artifact": "regression_rc",
            }
        ],
        "workflow_plan_gaps": [
            {
                "gap_id": "fixture.stochastic.not_exercised",
                "status": "NOT_EXERCISED",
                "reason": "hermetic deterministic fixture does not claim stochastic N8",
            }
        ],
        "workflow_plan_evidence": phases,
        "operators": [
            {
                "operator_id": "fixture",
                "api": "aclnnFixture",
                "formal_root": str(formal),
                "taskbook": {
                    "input_form": "local_file",
                    "snapshot_sha256": taskdoc_sha,
                },
                "source": {
                    "input_association": copy.deepcopy(
                        rge._CALLER_INPUT_ASSOCIATION
                    ),
                    "content_anchor": copy.deepcopy(content_anchor),
                    "envelope_sha256": source_facts["digest"],
                },
                "required": {
                    "dtypes": ["float32", "int64"],
                    "hardware": ["Atlas A2", "Atlas A3", "Atlas A5"],
                    "case_target": 3,
                    "structure_denominator_total": 3,
                    "structured_excluded": 0,
                    "performance_requires_comparison": True,
                },
                "generated": {
                    "case_count": 3,
                    "dtypes": ["float32"],
                    "missing_required_dtypes": ["int64"],
                    "spec_contract_sha256": spec_contract_sha,
                    "caseset_contract_sha256": caseset_contract_sha,
                },
                "executable": {
                    "planned": 3,
                    "produced": 2,
                    "failed": 1,
                    "invocation_excluded": 0,
                    "tested_hardware": ["Atlas A3"],
                    "cann_version": "9.0.1",
                },
                "precision": {
                    "total": 3,
                    "executed": 2,
                    "passed": 2,
                    "numerical_failed": 0,
                    "execution_errored": 1,
                    "uncertain": 0,
                    "not_applicable": 0,
                    "verdict": "FAIL",
                },
                "performance": {
                    "mode": "measure_only",
                    "perf_cases": 2,
                    "measured": 1,
                    "blocked": 1,
                    "status": "BLOCKED",
                },
                "gates": {
                    "task1": "PASSED",
                    "task2": "PASSED",
                    "task3": "NOT_COMPLETED_PRECISION_FAIL",
                },
                "acceptance": {
                    "result": "FAIL(精度)",
                    "state": "FAILED_PRECISION",
                    "exit_code": 1,
                    "gate_passed": True,
                    "human_cp": False,
                },
                "structured_gaps": [
                    {
                        "gap_id": "fixture.dtype.missing",
                        "kind": "dtype_unsupported",
                        "dimension": "coverage",
                        "status": "UNVALIDATED",
                        "source_kind": "formal_task_pr_gap",
                        "details": {"dtypes": ["int64"]},
                    },
                    {
                        "gap_id": "fixture.performance.unvalidated",
                        "kind": "performance_requirement_unvalidated",
                        "dimension": "performance",
                        "status": "UNVALIDATED",
                        "source_kind": "formal_task_pr_gap",
                        "details": {},
                    },
                    {
                        "gap_id": "fixture.hardware.missing",
                        "kind": "hardware_not_tested",
                        "dimension": "hardware",
                        "status": "UNVALIDATED",
                        "source_kind": "derived_coverage_limit",
                        "details": {"hardware": ["Atlas A2", "Atlas A5"]},
                    },
                ],
                "artifacts": artifacts,
            }
        ],
    }
    _materialize_n0_fixture(root, ledger)
    return ledger


class RgeLedgerTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.ledger = _fixture(self.root)

    def errors(self, mutation, *, verify_artifacts: bool = False) -> list[str]:
        ledger = copy.deepcopy(self.ledger)
        mutation(ledger)
        return rge.validation_errors(ledger, verify_artifacts=verify_artifacts)

    def rewrite_artifact(self, ledger, name: str, mutation) -> None:
        item = ledger["operators"][0]["artifacts"][name]
        path = Path(item["path"])
        value = json.loads(path.read_text(encoding="utf-8"))
        mutation(value)
        _write_json(path, value)
        item["sha256"] = _sha(path)

    def rewrite_multicard_artifact(self, ledger, basename: str, mutation) -> None:
        outer = ledger["supplemental_artifacts"]["multicard_v4_inventory"]
        inventory_path = Path(outer["path"])
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        item = next(
            row
            for row in inventory["artifacts"]
            if Path(row["path"]).name == basename
        )
        path = Path(inventory["artifact_root"]) / item["path"]
        value = json.loads(path.read_text(encoding="utf-8"))
        mutation(value)
        _write_json(path, value)
        item.update({"sha256": _sha(path), "bytes": path.stat().st_size})
        _write_json(inventory_path, inventory)
        outer["sha256"] = _sha(inventory_path)

    def rewrite_multicard_shard_bundle(self, ledger) -> None:
        """Coherently rewrite one full shard, while leaving an invalid load receipt."""

        outer = ledger["supplemental_artifacts"]["multicard_v4_inventory"]
        inventory_path = Path(outer["path"])
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        artifact_root = Path(inventory["artifact_root"])
        top = {Path(item["path"]).name: item for item in inventory["artifacts"]}
        manifest = json.loads(
            (artifact_root / top["multi_card_manifest.json"]["path"]).read_text(
                encoding="utf-8")
        )
        shard_inventory = inventory["shards"][0]
        items = {
            Path(item["path"]).name: item
            for item in shard_inventory["formal_artifacts"]
            if Path(item["path"]).name in {
                "caseset.json", "cpp_extension_caseset.json",
                "cpp_extension_invocation_plan.json", "cpp_extension_receipt.json",
                "device_identity.json", "shard_result.json", "evidence.json",
            }
        }
        values = {
            name: json.loads((artifact_root / item["path"]).read_text(encoding="utf-8"))
            for name, item in items.items()
            if name.endswith(".json")
        }
        values["cpp_extension_receipt.json"]["load"]["loader"] = \
            "coherent_but_untrusted_loader"
        values["evidence.json"]["cpp_extension_receipt"] = copy.deepcopy(
            values["cpp_extension_receipt.json"]
        )
        receipt_sha = rge._canonical_sha256(values["cpp_extension_receipt.json"])
        for row in values["evidence.json"]["evidence"]:
            row["cpp_extension_receipt_sha256"] = receipt_sha
        manifest_shard = next(
            row
            for row in manifest["shards"]
            if row["shard_id"] == shard_inventory["shard_id"]
        )
        values["shard_result.json"] = multi_card_shards.build_result(
            manifest,
            manifest_shard,
            values["caseset.json"],
            values["evidence.json"],
            values["device_identity.json"],
        )
        for name, value in values.items():
            item = items[name]
            path = artifact_root / item["path"]
            _write_json(path, value)
            item.update({"sha256": _sha(path), "bytes": path.stat().st_size})
        shard_inventory["formal_receipt_sha256"] = items[
            "cpp_extension_receipt.json"]["sha256"]
        shard_inventory["result_sha256"] = items["shard_result.json"]["sha256"]

        results = []
        envelopes = []
        for shard in inventory["shards"]:
            fixed = {
                Path(item["path"]).name: item for item in shard["formal_artifacts"]
                if Path(item["path"]).name in {"shard_result.json", "evidence.json"}
            }
            results.append(json.loads(
                (artifact_root / fixed["shard_result.json"]["path"]).read_text()))
            envelopes.append(json.loads(
                (artifact_root / fixed["evidence.json"]["path"]).read_text()))
        spec = json.loads((artifact_root / top["spec.json"]["path"]).read_text())
        caseset = json.loads((artifact_root / top["caseset.json"]["path"]).read_text())
        source = json.loads((artifact_root / top["source_facts.json"]["path"]).read_text())
        merged = multi_card_shards.merge_results(
            manifest, results, spec, caseset, source)
        envelope = multi_card_shards.assemble_envelope(manifest, merged, envelopes)
        for basename, value in (
            ("merged.json", merged),
            ("multi_card_merged_evidence.json", merged),
            ("evidence.json", envelope),
        ):
            item = top[basename]
            path = artifact_root / item["path"]
            _write_json(path, value)
            item.update({"sha256": _sha(path), "bytes": path.stat().st_size})
        single_work = Path(inventory["single_work"])
        single_evidence = json.loads(
            Path(ledger["operators"][0]["artifacts"]["precision_evidence"]["path"])
            .read_text())
        single_receipt = json.loads(
            Path(ledger["operators"][0]["artifacts"]["extension_receipt"]["path"])
            .read_text())
        verdict_item = top["verdict.json"]
        verdict = json.loads((artifact_root / verdict_item["path"]).read_text())
        equivalence = multi_card_verdict_equivalence.build_equivalence(
            verdict, verdict, manifest, spec, caseset, source, merged,
            single_evidence, single_receipt, str(single_work),
            multi_sha=verdict_item["sha256"],
            single_sha=ledger["operators"][0]["artifacts"]["verdict"]["sha256"],
            manifest_sha=top["multi_card_manifest.json"]["sha256"],
        )
        eq_item = top["equivalence.json"]
        eq_path = artifact_root / eq_item["path"]
        _write_json(eq_path, equivalence)
        eq_item.update({"sha256": _sha(eq_path), "bytes": eq_path.stat().st_size})
        _write_json(inventory_path, inventory)
        outer["sha256"] = _sha(inventory_path)

    def rewrite_n0_dry_bundle(self, ledger) -> None:
        """Coherently rewrite every hash layer around a false dry-run count."""

        receipt_item = ledger["supplemental_artifacts"]["n0_static_fixture"]
        receipt_path = Path(receipt_item["path"])
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        gate = receipt["gates"]["gen_cases_dry_run"]
        dry_path = Path(gate["outputs"][0]["path"])
        dry = json.loads(dry_path.read_text(encoding="utf-8"))
        dry["summary"]["emitted"] += 1
        core = dict(dry)
        core.pop("ledger_digest")
        dry["ledger_digest"] = content_address.content_digest(
            "oprunway/case-plan/v1", core
        )
        _write_json(dry_path, dry)
        gate["outputs"][0].update(
            {"sha256": _sha(dry_path), "bytes": dry_path.stat().st_size}
        )
        execution_path = Path(gate["execution_receipt"]["path"])
        execution = dict(gate)
        execution.pop("execution_receipt")
        _write_json(execution_path, execution)
        gate["execution_receipt"]["sha256"] = _sha(execution_path)
        _write_json(receipt_path, receipt)
        receipt_item["sha256"] = _sha(receipt_path)
        batch_item = ledger["supplemental_artifacts"]["n0_static_batch"]
        batch_path = Path(batch_item["path"])
        batch = json.loads(batch_path.read_text(encoding="utf-8"))
        batch["operators"]["fixture"]["receipt_sha256"] = _sha(receipt_path)
        _write_json(batch_path, batch)
        batch_item["sha256"] = _sha(batch_path)

    def rewrite_multicard_cpp_format(self, ledger) -> None:
        """Keep the v4 inventory coherent while changing the generated ACL enum."""

        outer = ledger["supplemental_artifacts"]["multicard_v4_inventory"]
        inventory_path = Path(outer["path"])
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        artifact_root = Path(inventory["artifact_root"])
        item = next(
            row
            for row in inventory["shards"][0]["formal_artifacts"]
            if row["path"].endswith("cpp_extension/csrc/oprunway_extension.cpp")
        )
        source_path = artifact_root / item["path"]
        source_path.write_text(
            source_path.read_text(encoding="utf-8").replace(
                "ACL_FORMAT_ND", "ACL_FORMAT_NCHW"
            ),
            encoding="utf-8",
        )
        item.update({"sha256": _sha(source_path), "bytes": source_path.stat().st_size})
        _write_json(inventory_path, inventory)
        outer["sha256"] = _sha(inventory_path)

    def assert_error(self, errors: list[str], fragment: str) -> None:
        self.assertTrue(
            any(fragment in error for error in errors),
            f"missing {fragment!r} in:\n" + "\n".join(errors),
        )

    def test_fixture_is_artifact_projected_and_hash_valid(self) -> None:
        self.assertEqual(rge.validation_errors(self.ledger), [])
        self.assertEqual(rge.validation_errors(self.ledger, verify_artifacts=True), [])

    def test_artifact_hash_mutation_is_rejected(self) -> None:
        errors = self.errors(
            lambda ledger: ledger["operators"][0]["artifacts"]["spec"].__setitem__(
                "sha256", "0" * 64
            ),
            verify_artifacts=True,
        )
        self.assert_error(errors, "measured")

    def test_coherent_caseset_and_sha_mutation_is_rejected_by_projection(self) -> None:
        def mutate(ledger) -> None:
            self.rewrite_artifact(
                ledger, "caseset", lambda value: value.__setitem__("emitted", 2)
            )

        self.assert_error(self.errors(mutate, verify_artifacts=True), "caseset.emitted")

    def test_coherent_verdict_and_sha_mutation_is_rejected_by_projection(self) -> None:
        def mutate(ledger) -> None:
            self.rewrite_artifact(
                ledger,
                "verdict",
                lambda value: value["accuracy_summary"].__setitem__("passed", 1),
            )

        self.assert_error(self.errors(mutate, verify_artifacts=True), "verdict projection drift")

    def test_coherent_source_and_build_anchor_rewrite_cannot_escape_execution(self) -> None:
        def mutate(ledger) -> None:
            def change(value) -> None:
                value["payload"]["pr"]["content_anchor"]["sha256"] = "a" * 64
                value["digest"] = content_address.content_digest(
                    rge._SOURCE_FACTS_DOMAIN, value["payload"]
                )

            self.rewrite_artifact(ledger, "source_facts", change)
            ledger["operators"][0]["source"]["envelope_sha256"] = json.loads(
                Path(ledger["operators"][0]["artifacts"]["source_facts"]["path"]).read_text()
            )["digest"]
            ledger["operators"][0]["source"]["content_anchor"]["sha256"] = "a" * 64
            self.rewrite_artifact(
                ledger,
                "vendor_build_receipt",
                lambda value: (
                    value["source"]["content_anchor"].__setitem__("sha256", "a" * 64),
                    value["build"]["source_snapshot_digest"]["content_anchor"].__setitem__(
                        "sha256", "a" * 64
                    ),
                ),
            )

        self.assert_error(
            self.errors(mutate, verify_artifacts=True), "external build receipt drift"
        )

    def test_generated_denominator_loss_is_rejected(self) -> None:
        errors = self.errors(
            lambda ledger: ledger["operators"][0]["generated"].__setitem__("case_count", 2)
        )
        self.assert_error(errors, "generated.case_count must equal required.case_target")

    def test_structure_denominator_loss_is_rejected(self) -> None:
        errors = self.errors(
            lambda ledger: ledger["operators"][0]["required"].__setitem__(
                "structure_denominator_total", 2
            )
        )
        self.assert_error(errors, "structure_denominator_total must equal")

    def test_missing_dtype_must_be_mirrored_by_gap(self) -> None:
        def mutate(ledger) -> None:
            gap = next(
                gap
                for gap in ledger["operators"][0]["structured_gaps"]
                if gap["kind"] == "dtype_unsupported"
            )
            gap["details"]["dtypes"] = []

        self.assert_error(self.errors(mutate), "must mirror missing dtypes")

    def test_executable_accounting_must_cover_every_planned_case(self) -> None:
        errors = self.errors(
            lambda ledger: ledger["operators"][0]["executable"].__setitem__("produced", 1)
        )
        self.assert_error(errors, "planned must equal produced + failed")

    def test_precision_partition_cannot_hide_execution_error(self) -> None:
        errors = self.errors(
            lambda ledger: ledger["operators"][0]["precision"].__setitem__(
                "execution_errored", 0
            )
        )
        self.assert_error(errors, "execution_errored must equal executable.failed")

    def test_measure_only_requirement_needs_structured_gap(self) -> None:
        def mutate(ledger) -> None:
            ledger["operators"][0]["structured_gaps"] = [
                gap
                for gap in ledger["operators"][0]["structured_gaps"]
                if gap["kind"] != "performance_requirement_unvalidated"
            ]

        self.assert_error(self.errors(mutate), "requires structured gap")

    def test_hardware_gap_must_equal_required_minus_tested(self) -> None:
        def mutate(ledger) -> None:
            gap = next(
                gap
                for gap in ledger["operators"][0]["structured_gaps"]
                if gap["kind"] == "hardware_not_tested"
            )
            gap["details"]["hardware"] = []

        self.assert_error(self.errors(mutate), "must mirror missing hardware")

    def test_gate_false_cannot_be_reported_as_pass(self) -> None:
        def mutate(ledger) -> None:
            ledger["operators"][0]["gates"]["task1"] = "FAILED"
            Path(ledger["operators"][0]["artifacts"]["gate_task1"]["path"]).write_text(
                "=== 验收门 stage=task1 ===\nSTATUS: FAILED\n", encoding="utf-8"
            )
            ledger["operators"][0]["acceptance"].update(
                {
                    "result": "PASS",
                    "state": "PASSED",
                    "exit_code": 0,
                    "gate_passed": False,
                }
            )
            self.rewrite_artifact(
                ledger,
                "acceptance",
                lambda value: value.update(
                    {
                        "overall": "PASS",
                        "state": "PASSED",
                        "exit_code": 0,
                        "gate": {"passed": False, "errors": {"task1": ["failed"]}},
                    }
                ),
            )

        self.assert_error(self.errors(mutate), "acceptance: expected")

    def test_failed_task_gate_cannot_keep_acceptance_gate_true(self) -> None:
        def mutate(ledger) -> None:
            operator = ledger["operators"][0]
            operator["gates"]["task1"] = "FAILED"
            Path(operator["artifacts"]["gate_task1"]["path"]).write_text(
                "=== 验收门 stage=task1 ===\nSTATUS: FAILED\n", encoding="utf-8"
            )

        self.assert_error(
            self.errors(mutate),
            "acceptance.gate_passed: expected False from task1/task2 gates",
        )

    def test_precision_pass_with_perf_blocked_must_be_blocked(self) -> None:
        def mutate(ledger) -> None:
            op = ledger["operators"][0]
            op["executable"].update({"produced": 3, "failed": 0})
            op["precision"].update(
                {"executed": 3, "passed": 3, "execution_errored": 0, "verdict": "PASS"}
            )
            op["gates"]["task3"] = "NOT_COMPLETED_PERF_BLOCKED"
            op["acceptance"].update(
                {"result": "PASS", "state": "PASSED", "exit_code": 0}
            )
            def receipt_change(value) -> None:
                inv = value["invocation"]
                inv.update({"produced": 3, "failed": 0, "failed_case_ids": []})
                for row in inv["case_records"]:
                    row["outcome"] = "produced"
            self.rewrite_artifact(ledger, "extension_receipt", receipt_change)
            receipt = json.loads(
                Path(op["artifacts"]["extension_receipt"]["path"]).read_text()
            )
            receipt_sha = rge._canonical_sha256(receipt)
            def evidence_change(value) -> None:
                value["cpp_extension_receipt"] = receipt
                for row in value["evidence"]:
                    row["status"] = "ok"
                    row["cpp_extension_receipt_sha256"] = receipt_sha
            self.rewrite_artifact(ledger, "precision_evidence", evidence_change)
            def verdict_change(value) -> None:
                value["accuracy_summary"].update(
                    {"executed": 3, "passed": 3, "errored": 0}
                )
                value["overall"]["verdict"] = "pass"
            self.rewrite_artifact(ledger, "verdict", verdict_change)
            self.rewrite_artifact(
                ledger,
                "acceptance",
                lambda value: value.update(
                    {
                        "overall": "PASS",
                        "state": "PASSED",
                        "exit_code": 0,
                        "precision_verdict": "pass",
                    }
                ),
            )

        self.assert_error(self.errors(mutate), "BLOCKED_PERF_MEASUREMENT_INCOMPLETE")

    def test_no_online_identity_authority_or_intake_is_required(self) -> None:
        self.assertNotIn("online_pr_source_support", self.ledger["authority"])
        self.assertNotIn("supplemental_online_pr_intakes", self.ledger)
        self.assertEqual(rge.validation_errors(self.ledger), [])

    def test_transport_locator_drift_does_not_change_content_authority(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            variant = _fixture(Path(raw), transport_variant=True)
            self.assertEqual(rge.validation_errors(variant), [])
            self.assertEqual(
                variant["operators"][0]["source"]["content_anchor"],
                self.ledger["operators"][0]["source"]["content_anchor"],
            )

    def test_content_anchor_shape_sha_and_count_are_hard(self) -> None:
        for key, value in (
            ("scope", 7),
            ("sha256", "f" * 63),
            ("file_count", 0),
        ):
            with self.subTest(key=key):
                errors = self.errors(
                    lambda ledger, k=key, v=value: ledger["operators"][0]
                    ["source"]["content_anchor"].__setitem__(k, v)
                )
                self.assert_error(errors, "content anchor")

    def test_current_ledger_rejects_vendor_v2_head_only_downgrade(self) -> None:
        def mutate(ledger) -> None:
            self.rewrite_artifact(
                ledger,
                "vendor_build_receipt",
                lambda value: value.update({"schema_version": 2}),
            )

        self.assert_error(
            self.errors(mutate), "requires current v3 content snapshot"
        )

    def test_multicard_partition_must_cover_single_card_denominator(self) -> None:
        errors = self.errors(
            lambda ledger: ledger["multi_card_precision_equivalence"][0].__setitem__(
                "shard_sizes", [1, 1]
            )
        )
        self.assert_error(errors, "shard_sizes must cover generated case_count")

    def test_multicard_cannot_claim_precision_equivalence_for_performance(self) -> None:
        errors = self.errors(
            lambda ledger: ledger["multi_card_precision_equivalence"][0].__setitem__(
                "performance_collected", True
            )
        )
        self.assert_error(errors, "cannot claim performance")

    def test_multicard_devices_are_reprojected_from_manifest(self) -> None:
        errors = self.errors(
            lambda ledger: ledger["multi_card_precision_equivalence"][0].__setitem__(
                "devices", [2, 1]
            )
        )
        self.assert_error(errors, "multi-card projection drift")

    def test_n0_coherent_dry_run_bundle_replacement_is_rejected(self) -> None:
        self.assert_error(
            self.errors(self.rewrite_n0_dry_bundle, verify_artifacts=True),
            "dry_run: semantic projection drift",
        )

    def test_n2_generated_cpp_enum_is_reprojected_from_v4_inventory(self) -> None:
        self.assert_error(
            self.errors(self.rewrite_multicard_cpp_format, verify_artifacts=True),
            "does not use ACL_FORMAT_ND",
        )

    def test_multicard_equivalence_cannot_be_rewritten_with_coherent_hashes(self) -> None:
        def mutate(ledger) -> None:
            self.rewrite_multicard_artifact(
                ledger,
                "equivalence.json",
                lambda value: value.__setitem__("passed", False),
            )

        self.assert_error(
            self.errors(mutate, verify_artifacts=True),
            "deterministic replay drift",
        )

    def test_coherent_on_disk_shard_bundle_replacement_is_rejected(self) -> None:
        self.assert_error(
            self.errors(
                self.rewrite_multicard_shard_bundle,
                verify_artifacts=True,
            ),
            "Extension load receipt",
        )

    def test_n0_through_n10_must_be_complete_and_ordered(self) -> None:
        errors = self.errors(lambda ledger: ledger["workflow_plan_evidence"].pop(5))
        self.assert_error(errors, "exactly ordered N0 through N10")

    def test_phase_specific_required_artifact_is_not_interchangeable(self) -> None:
        def mutate(ledger) -> None:
            refs = ledger["workflow_plan_evidence"][0]["evidence_refs"]
            refs[:] = [ref for ref in refs if ref.get("artifact") != "source_facts"]

        self.assert_error(self.errors(mutate), "required artifact 'source_facts'")

    def test_repository_test_must_bind_log_identity_and_rc(self) -> None:
        def mutate(ledger) -> None:
            ref = next(
                ref
                for ref in ledger["workflow_plan_evidence"][0]["evidence_refs"]
                if ref["kind"] == "repository_test"
            )
            ref["log_artifact"] = "online_log"

        self.assert_error(self.errors(mutate), "log does not identify")

    def test_partial_phase_must_name_machine_readable_gap(self) -> None:
        errors = self.errors(
            lambda ledger: ledger["workflow_plan_evidence"][10].__setitem__(
                "structured_gap_ids", []
            )
        )
        self.assert_error(errors, "status requires at least one structured gap")


class StochasticFormalProjectionTest(unittest.TestCase):
    def _projection_fixture(self, root: Path):
        spec = stochastic_spec()
        caseset, receipt = stochastic_fixture(str(root))
        validated = cpp_extension_adapter.validate_stochastic_collection(
            str(root), caseset, receipt)
        evidence = {
            "stochastic_collection": receipt["stochastic_collection"],
            "stochastic_formal_evidence": validated["formal"],
            "stochastic_evaluation": validated["evaluation"],
        }
        receipt_path = root / "cpp_extension_receipt.json"
        _write_json(receipt_path, receipt)
        formal_path = root / "stochastic_formal_evidence.json"
        artifacts = {
            "extension_receipt": {
                "path": str(receipt_path), "sha256": _sha(receipt_path),
            },
            "stochastic_formal_evidence": {
                "path": str(formal_path), "sha256": _sha(formal_path),
            },
        }
        return spec, caseset, evidence, receipt, artifacts, validated

    def test_registered_formal_is_adapter_replayed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = self._projection_fixture(Path(raw))
            errors = []
            rge._validate_stochastic_formal_projection(
                *fixture[:5], "stochastic", errors)
            self.assertEqual(errors, [])

    def test_coherent_registered_formal_replacement_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            spec, caseset, evidence, receipt, artifacts, validated = \
                self._projection_fixture(root)
            formal_path = Path(artifacts["stochastic_formal_evidence"]["path"])
            formal = json.loads(formal_path.read_text(encoding="utf-8"))
            formal["groups"][0]["seed"] = 999
            _write_json(formal_path, formal)
            artifacts["stochastic_formal_evidence"]["sha256"] = _sha(formal_path)
            formal_ref = receipt["stochastic_collection"]["formal_evidence"]
            formal_ref["sha256"] = _sha(formal_path)
            evidence["stochastic_collection"] = copy.deepcopy(
                receipt["stochastic_collection"])
            evidence["stochastic_formal_evidence"] = copy.deepcopy(formal)
            evidence["stochastic_evaluation"] = copy.deepcopy(validated["evaluation"])
            receipt_path = Path(artifacts["extension_receipt"]["path"])
            _write_json(receipt_path, receipt)
            artifacts["extension_receipt"]["sha256"] = _sha(receipt_path)
            errors = []
            rge._validate_stochastic_formal_projection(
                spec, caseset, evidence, receipt, artifacts, "stochastic", errors)
            self.assertTrue(
                any("adapter replay failed" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
