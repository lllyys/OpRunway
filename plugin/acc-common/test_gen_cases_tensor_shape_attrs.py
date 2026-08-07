#!/usr/bin/env python3
"""N7 生产接缝：rank0 / atomic attrs / cyclic axes / layout 的确定性测试。"""

import copy
import hashlib
import os
import tempfile
import unittest
from unittest import mock

import numpy as np

import gen_cases as GC
import content_address
import cpp_extension_adapter as CEA
import tensor_shape_attrs as TSA
import test_gen_cases_multi_input as N6


class Rank0PlannerSeamTest(unittest.TestCase):
    def test_rank0_empty_singleton_have_distinct_tags_and_classes(self):
        self.assertEqual(GC._shape_tag(()), "rank0")
        self.assertEqual(GC._shape_tag((0,)), "0")
        self.assertEqual(GC._shape_tag((1,)), "1")
        self.assertEqual(GC._shape_class(()), GC.SHAPE_CLASS_RANK0)
        self.assertEqual(GC._shape_class((0,)), GC.SHAPE_CLASS_EMPTY)
        self.assertEqual(GC._shape_class((1,)), GC.SHAPE_CLASS_ALL_UNIT)

    def test_explicit_rank_zero_to_eight_has_a_witness_per_rank(self):
        ranks = GC._allowed_ranks([{
            "name": "x", "io": "in", "rank": list(range(9)),
        }])
        self.assertEqual(ranks, frozenset(range(9)))
        regular, _large = GC._shape_ladder(ranks)
        witnessed = {len(shape) for shape in regular}
        self.assertEqual(witnessed, set(range(9)))
        self.assertIn((), regular)

    def test_unconstrained_legacy_shape_ladder_does_not_gain_rank0(self):
        regular, _large = GC._shape_ladder(None)
        self.assertNotIn((), regular)
        self.assertNotIn(0, {len(shape) for shape in regular})


class ExplicitAttrTypeSeamTest(unittest.TestCase):
    def test_empty_int_array_is_valid_only_with_explicit_type(self):
        param = {
            "name": "dims", "io": "attr", "dtype": ["int64"],
            "default": [], "attr_type": "int_array",
        }
        self.assertEqual(GC._check_param_attr_value(param, [], "dims"), [])
        self.assertEqual(GC._attr_ctype(param, []), "int_array")
        with self.assertRaisesRegex(ValueError, "空数组"):
            GC._check_attr_value([], "legacy_dims")

    def test_declared_scalar_and_host_scalar_cannot_smuggle_int_array(self):
        scalar = {
            "name": "axis", "io": "attr", "dtype": ["int64"],
            "default": 0, "attr_type": "scalar",
        }
        with self.assertRaisesRegex(ValueError, "scalar"):
            GC._check_param_attr_value(scalar, [], "axis")
        host_scalar = {
            "name": "other", "io": "attr", "dtype": ["float32"],
            "kind": "scalar", "binding": "host_scalar", "default": 1.0,
            "attr_type": "int_array",
        }
        with self.assertRaisesRegex(ValueError, "host_scalar.*int_array"):
            GC._attr_ctype(host_scalar, [])

    def test_empty_array_survives_case_call_contract(self):
        spec = {
            "params": [
                {"name": "x", "io": "in", "dtype": ["float32"]},
                {"name": "dims", "io": "attr", "dtype": ["int64"],
                 "default": [], "attr_type": "int_array"},
                {"name": "y", "io": "out", "dtype": ["float32"]},
            ],
        }
        variant = {
            "symbol": "Fake", "active_attrs": ["dims"],
            "active_outputs": ["y"], "attrs": {}, "when": {},
        }
        call = GC._build_aclnn_call(
            spec, variant, {"dims": []}, ["y"], "case_empty_dims")
        self.assertEqual(call["slots"][1], {
            "role": "attr", "name": "dims", "ctype": "int_array", "value": [],
        })


def _source_parse(label):
    digest = hashlib.sha256(label.encode("utf-8")).hexdigest()
    return {
        "source": label,
        "source_sha256": digest,
        "cite": f"fixture:{label}",
        "quote": f"{label} authoritative row",
        "interpretation": f"parse {label} as one atomic row",
    }


def _profile(profile_id, shape):
    return {
        "profile_id": profile_id,
        "inputs": [{
            "name": "x", "kind": "tensor", "binding": "device_tensor",
            "shape": list(shape), "dtype": "float32", "format": "nd",
        }],
        "output": {
            "name": "y", "kind": "tensor", "binding": "device_tensor",
            "shape": list(shape), "dtype": "float32", "format": "nd",
        },
        "relations": {
            "shape": {"broadcasted": False, "rank_mismatch": False,
                      "rank0_tensor": len(shape) == 0},
        },
    }


def _atomic_spec():
    spec = {
        "op": "WitnessAtomicAttrs",
        "runner_form": "cpp_extension",
        "verify_mode": "numerical",
        "operator_class": "structural",
        "params": [
            {"name": "x", "io": "in", "kind": "tensor",
             "binding": "device_tensor", "format": "nd", "dtype": ["float32"]},
            {"name": "shifts", "io": "attr", "dtype": ["int64"],
             "attr_type": "int_array", "default": []},
            {"name": "dims", "io": "attr", "dtype": ["int64"],
             "attr_type": "int_array", "default": []},
            {"name": "wrap", "io": "attr", "dtype": ["bool"],
             "attr_type": "scalar", "default": False},
            {"name": "y", "io": "out", "kind": "tensor",
             "binding": "device_tensor", "format": "nd", "dtype": ["float32"]},
        ],
        "precision": {"case_target": 8},
    }
    base_sha = hashlib.sha256(content_address.canonical_json_bytes(spec)).hexdigest()
    source_binding = {
        "compose_kind": "spec_taskdoc_compose",
        "spec_sha256": base_sha,
        "taskdoc_snapshot_sha256": "1" * 64,
        "source_facts_sha256": "2" * 64,
    }
    rows = [
        {"id": "empty_axes", "attrs": {"shifts": [], "dims": []},
         "source_parse": _source_parse("empty_axes")},
        {"id": "last_axis", "attrs": {"shifts": [2], "dims": [-1]},
         "source_parse": _source_parse("last_axis")},
    ]
    groups = [{
        "id": "paired_lengths", "members": ["shifts", "dims"],
        "relation": "equal_length", "exceptions": [],
    }]
    contract = TSA.normalize_atomic_attr_rows(
        rows,
        attr_types={"shifts": "int_array", "dims": "int_array"},
        constraint_groups=groups,
        source_binding=source_binding,
    )
    spec["tensor_shape_attrs"] = {
        "schema_version": 1,
        "atomic_attr_rows": {
            "attr_names": ["shifts", "dims"],
            "expected_sha256": contract["sha256"],
            "source_binding": source_binding,
            "constraint_groups": groups,
            "rows": rows,
        },
        "cyclic_indices": [{
            "attr": "dims", "rank_from_input": "x",
            "duplicate_policy": "reject_after_normalization",
        }],
    }
    return spec


def _atomic_bundle():
    profiles = [_profile("profile_a", (2, 3)), _profile("profile_b", (4, 5))]
    return {
        "schema_version": "multi_input.v1",
        "sha256": "3" * 64,
        "profiles": profiles,
        "coverage": {},
        "required_coverage": {},
    }


def _atomic_caseset(spec=None):
    spec = copy.deepcopy(spec or _atomic_spec())
    entries, meta = GC._multi_input_profile_plan(
        spec, _atomic_bundle(),
        {"shifts": [], "dims": [], "wrap": False}, 8)
    seen = set()
    cases = []
    for entry in entries:
        profile = copy.deepcopy(entry["input_profile"])
        cid = GC._mk_multi_input_id(
            spec["op"], profile, entry["attr_idx"], seen,
            contract_bindings=entry["contract_bindings"])
        cases.append({
            "id": cid,
            "attrs": copy.deepcopy(entry["attrs"]),
            "parameter_contract": profile,
            "inputs": [copy.deepcopy(item) for item in profile["inputs"]
                       if item["kind"] == "tensor"],
            "contract_bindings": copy.deepcopy(entry["contract_bindings"]),
        })
    ledger = copy.deepcopy(meta["atomic_attr_ledger"])
    return {
        "op": spec["op"],
        "cases": cases,
        "atomic_attr_ledger": ledger,
        "atomic_attr_ledger_sha256": CEA._canonical_sha(ledger),
    }


def _atomic_plan(caseset, *, exclude_last=False):
    contract = CEA.validate_caseset_tensor_shape_attr_contract(caseset)
    by_id = {row["case_id"]: row["contract_bindings"]
             for row in contract["bindings"]["cases"]}
    ids = [case["id"] for case in caseset["cases"]]
    excluded_ids = set(ids[-1:] if exclude_last else [])
    rows = [{
        "case_id": cid,
        "contract_bindings_sha256": CEA._canonical_sha(by_id[cid]),
    } for cid in ids if cid not in excluded_ids]
    excluded = [{
        "case_id": cid,
        "reason": CEA.GOLDEN_UNAVAILABLE,
        "contract_bindings_sha256": CEA._canonical_sha(by_id[cid]),
    } for cid in ids if cid in excluded_ids]
    return {
        "cases": rows,
        "excluded": excluded,
        "tensor_shape_attr_bindings_sha256": contract["bindings_sha256"],
        "atomic_attr_ledger_sha256": contract["atomic_ledger_sha256"],
    }


class AtomicAttrProductionSeamTest(unittest.TestCase):
    def test_profile_atomic_independent_product_is_exact_and_rows_never_cross(self):
        spec = _atomic_spec()
        entries, meta = GC._multi_input_profile_plan(
            spec, _atomic_bundle(),
            {"shifts": [], "dims": [], "wrap": False}, 8)
        self.assertEqual(len(entries), 8)  # P=2 * A=2 * Q=2
        observed = {
            (tuple(entry["attrs"]["shifts"]), tuple(entry["attrs"]["dims"]))
            for entry in entries
        }
        self.assertEqual(observed, {((), ()), ((2,), (-1,))})
        ledger = meta["atomic_attr_ledger"]
        self.assertEqual(
            (ledger["profiles"], ledger["atomic_rows"],
             ledger["independent_combinations"], ledger["emitted"]),
            (2, 2, 2, 8),
        )
        self.assertEqual(len({
            (cell["profile_id"], cell["row_id"], cell["q_id"],
             cell["q_sha256"], cell["case_id"])
            for cell in ledger["cells"]
        }), 8)
        self.assertEqual({cell["q_id"] for cell in ledger["cells"]}, {"q0000", "q0001"})
        self.assertTrue(all(len(cell["row_sha256"]) == 64
                            for cell in ledger["cells"]))

    def test_negative_axis_is_normalized_against_named_input_but_raw_reaches_dut(self):
        spec = _atomic_spec()
        entries, _meta = GC._multi_input_profile_plan(
            spec, _atomic_bundle(),
            {"shifts": [], "dims": [], "wrap": False}, 8)
        negative = next(entry for entry in entries
                        if entry["attrs"]["dims"] == [-1])
        self.assertEqual(negative["attrs"]["dims"], [-1])
        cyclic = negative["contract_bindings"]["cyclic_indices"]["dims"]
        self.assertEqual(cyclic, {
            "raw": [-1], "normalized": [1], "rank": 2,
            "had_negative": True,
            "duplicate_policy": "reject_after_normalization",
            "input": {
                "name": "x", "index": 0, "shape": [2, 3],
                "shape_receipt_sha256": hashlib.sha256(
                    content_address.canonical_json_bytes(
                        TSA.make_shape_receipt((2, 3)))).hexdigest(),
            },
        })

    def test_cartesian_mutation_and_source_anchor_drift_fail_closed(self):
        spec = _atomic_spec()
        plan = GC._resolve_tensor_shape_attr_plan(spec)
        crossed = {"shifts": [], "dims": [-1]}
        with self.assertRaisesRegex(ValueError, "权威组合"):
            TSA.bind_atomic_attr_row(
                plan["atomic_contract"], "empty_axes", crossed,
                expected_contract_sha256=plan["atomic_contract"]["sha256"])
        drifted = copy.deepcopy(spec)
        drifted["op"] = "ChangedBaseSpec"
        with self.assertRaisesRegex(ValueError, "spec_sha256"):
            GC._resolve_tensor_shape_attr_plan(drifted)

    def test_atomic_plan_receipt_evidence_chain_and_excluded_union(self):
        caseset = _atomic_caseset()
        plan = _atomic_plan(caseset, exclude_last=True)
        contract = CEA.validate_invocation_tensor_shape_attr_contract(caseset, plan)
        receipt = {
            "tensor_shape_attr_bindings_sha256": contract["bindings_sha256"],
            "atomic_attr_ledger_sha256": contract["atomic_ledger_sha256"],
        }
        evidence = []
        for row in contract["bindings"]["cases"]:
            binding = row["contract_bindings"]
            evidence.append({
                "case_id": row["case_id"],
                "tensor_shape_attr_bindings_sha256": contract["bindings_sha256"],
                "atomic_attr_ledger_sha256": contract["atomic_ledger_sha256"],
                "contract_bindings": copy.deepcopy(binding),
                "contract_bindings_sha256": CEA._canonical_sha(binding),
            })
        errors = []
        import validate_acceptance_state as gate
        gate._gate_cpp_extension_tensor_shape_attrs(
            caseset, receipt, evidence, errors, plan=plan)
        self.assertEqual(errors, [])

        missing_excluded_digest = copy.deepcopy(plan)
        missing_excluded_digest["excluded"][0].pop("contract_bindings_sha256")
        with self.assertRaisesRegex(CEA.CppExtensionAdapterError, "binding digest"):
            CEA.validate_invocation_tensor_shape_attr_contract(
                caseset, missing_excluded_digest)

        forged_evidence = copy.deepcopy(evidence)
        forged_evidence[0]["contract_bindings"]["cyclic_indices"]["dims"][
            "duplicate_policy"] = "allow"
        forged_evidence[0]["contract_bindings_sha256"] = CEA._canonical_sha(
            forged_evidence[0]["contract_bindings"])
        errors = []
        gate._gate_cpp_extension_tensor_shape_attrs(
            caseset, receipt, forged_evidence, errors, plan=plan)
        self.assertTrue(any("原样镜像" in error for error in errors))

    def test_atomic_cell_duplication_and_staged_cyclic_rewrite_fail_closed(self):
        caseset = _atomic_caseset()
        duplicated = copy.deepcopy(caseset)
        duplicated["atomic_attr_ledger"]["cells"][0] = copy.deepcopy(
            duplicated["atomic_attr_ledger"]["cells"][1])
        duplicated["atomic_attr_ledger_sha256"] = CEA._canonical_sha(
            duplicated["atomic_attr_ledger"])
        with self.assertRaisesRegex(CEA.CppExtensionAdapterError, "atomic cells|identity"):
            CEA.validate_caseset_tensor_shape_attr_contract(duplicated)

        facts = {"taskdoc": {"snapshot_sha256": "1" * 64}}
        spec = _atomic_spec()
        atomic = spec["tensor_shape_attrs"]["atomic_attr_rows"]
        atomic["source_binding"]["source_facts_sha256"] = content_address.content_digest(
            "oprunway/source-facts/v1", facts)
        rebuilt = TSA.normalize_atomic_attr_rows(
            atomic["rows"],
            attr_types={"shifts": "int_array", "dims": "int_array"},
            constraint_groups=atomic["constraint_groups"],
            source_binding=atomic["source_binding"])
        atomic["expected_sha256"] = rebuilt["sha256"]
        forged = _atomic_caseset(spec)
        for case in forged["cases"]:
            case["contract_bindings"]["cyclic_indices"]["dims"][
                "duplicate_policy"] = "allow"
        # 对无重复轴的这些 rows，caseset 内重算仍自洽；必须由 staged spec 外锚拒绝。
        CEA.validate_caseset_tensor_shape_attr_contract(forged)
        errors = []
        import validate_acceptance_state as gate
        with mock.patch.object(
                gate.source_facts_lookup, "find_source_facts", return_value=facts):
            gate._gate_tensor_shape_attr_spec_authority(
                "/unused", forged, spec, errors)
        self.assertTrue(any("staged spec" in error and "cyclic" in error
                            for error in errors))


class LayoutGenerationSeamTest(unittest.TestCase):
    @staticmethod
    def _input_param():
        return {"name": "x", "io": "in", "kind": "tensor",
                "binding": "device_tensor", "format": "nd",
                "dtype": ["float32"]}

    @staticmethod
    def _binding(case_id="layout_case", shape=(2, 3), kind="noncontiguous"):
        declaration = TSA.derive_layout_declaration(
            shape, layout_kind=kind, role="input", case_id=case_id,
            tensor_name="x", tensor_index=0)
        receipt = TSA.make_layout_receipt(
            declaration, expected_shape=shape, expected_role="input",
            expected_case_id=case_id, expected_tensor_name="x",
            expected_tensor_index=0)
        return {
            "requirement_id": "x_noncontiguous",
            "layout_receipt": receipt,
            "layout_receipt_sha256": TSA.layout_receipt_sha256(receipt),
        }

    def test_noncontiguous_input_is_saved_as_base_storage_and_reconstructs_values(self):
        logical = np.arange(6, dtype=np.float32).reshape(2, 3)
        with tempfile.TemporaryDirectory() as work:
            item = GC._save_case_tensor_inputs(
                work, "layout_case", [logical],
                [self._input_param()],
                ["float32"], layout_bindings={0: self._binding()},
            )[0]
            self.assertNotIn("path", item)
            self.assertEqual(item["storage_representation"], "base_storage_v1")
            self.assertEqual(item["layout_requirement_id"], "x_noncontiguous")
            self.assertEqual(
                (item["kind"], item["binding"], item["format"]),
                ("tensor", "device_tensor", "nd"))
            base = np.load(os.path.join(work, "x1_base.npy"), allow_pickle=False)
            receipt = item["layout_receipt"]
            view = np.ndarray(
                shape=tuple(receipt["logical_shape"]), dtype=base.dtype,
                buffer=base,
                offset=receipt["storage_offset"] * base.dtype.itemsize,
                strides=tuple(s * base.dtype.itemsize for s in receipt["strides"]),
            )
            self.assertEqual(view.strides, (24, 8))
            np.testing.assert_array_equal(view, logical)

    def test_layout_receipt_mutation_rank_default_and_fake_noncontiguous_fail(self):
        logical = np.arange(6, dtype=np.float32).reshape(2, 3)
        binding = self._binding()
        bad = copy.deepcopy(binding)
        bad["layout_receipt"]["strides"] = [3, 1]
        with tempfile.TemporaryDirectory() as work:
            with self.assertRaisesRegex(ValueError, "摘要漂移"):
                GC._save_case_tensor_inputs(
                    work, "layout_case", [logical],
                    [self._input_param()],
                    ["float32"], layout_bindings={0: bad})
            with self.assertRaisesRegex(ValueError, "format.*nd"):
                GC._save_case_tensor_inputs(
                    work, "layout_case", [logical],
                    [self._input_param()],
                    ["float32"],
                    tensor_contracts=[{
                        "name": "x", "kind": "tensor", "binding": "device_tensor",
                        "format": "torch_npu_rank_default", "shape": [2, 3],
                    }],
                    layout_bindings={0: binding})
        with self.assertRaisesRegex(ValueError, "unsupported_layout"):
            TSA.derive_layout_declaration(
                (), layout_kind="noncontiguous", role="input",
                case_id="rank0", tensor_name="x", tensor_index=0)

    def test_requirement_tracker_skips_fake_rank0_and_emits_external_ledger(self):
        spec = {
            "op": "LayoutWitness",
            "params": [
                {"name": "x", "io": "in", "format": "nd", "dtype": ["float32"]},
                {"name": "y", "io": "out", "format": "nd", "dtype": ["float32"]},
            ],
            "tensor_shape_attrs": {
                "schema_version": 1,
                "layout_requirements": [
                    {"id": "x_nc", "role": "input", "tensor_name": "x",
                     "layout_kind": "noncontiguous",
                     "selector": {"kind": "first_eligible"}},
                    {"id": "y_c", "role": "output", "tensor_name": "y",
                     "layout_kind": "contiguous",
                     "selector": {"kind": "first_eligible"}},
                ],
            },
        }
        plan = GC._resolve_tensor_shape_attr_plan(spec)
        tracker = GC._LayoutRequirementTracker(plan["layout_requirements"])
        rank0 = {"input_profile": _profile("rank0", ())}
        self.assertIsNone(tracker.claim(
            role="input", case_id="c0", entry=rank0, tensor_name="x",
            tensor_index=0, shape=(), tensor_format="nd"))
        regular = {"input_profile": _profile("regular", (2, 3))}
        x_binding = tracker.claim(
            role="input", case_id="c1", entry=regular, tensor_name="x",
            tensor_index=0, shape=(2, 3), tensor_format="nd")
        y_binding = tracker.claim(
            role="output", case_id="c1", entry=regular, tensor_name="y",
            tensor_index=0, shape=(2, 3), tensor_format="nd")
        self.assertEqual(x_binding["requirement_id"], "x_nc")
        self.assertEqual(y_binding["requirement_id"], "y_c")
        ledger, digest = tracker.finish()
        self.assertEqual(digest, hashlib.sha256(
            content_address.canonical_json_bytes(ledger)).hexdigest())
        self.assertEqual(ledger["cases"], [{
            "case_id": "c1",
            "inputs": [{
                "requirement_id": "x_nc", "tensor_index": 0, "name": "x",
                "layout_receipt_sha256": x_binding["layout_receipt_sha256"],
            }],
            "outputs": [{
                "requirement_id": "y_c", "tensor_index": 0, "name": "y",
                "layout_receipt_sha256": y_binding["layout_receipt_sha256"],
            }],
        }])

    def test_requirement_schema_and_unsatisfied_selector_fail_closed(self):
        spec = {
            "op": "LayoutWitness",
            "params": [
                {"name": "x", "io": "in", "format": "nd", "dtype": ["float32"]},
                {"name": "y", "io": "out", "format": "nd", "dtype": ["float32"]},
            ],
            "tensor_shape_attrs": {
                "schema_version": 1,
                "layout_requirements": [{
                    "id": "x_nc", "role": "input", "tensor_name": "x",
                    "layout_kind": "noncontiguous",
                    "selector": {"kind": "profile_id", "profile_id": "missing"},
                }],
            },
        }
        tracker = GC._LayoutRequirementTracker(
            GC._resolve_tensor_shape_attr_plan(spec)["layout_requirements"])
        with self.assertRaisesRegex(ValueError, "未找到合法见证"):
            tracker.finish()
        bad = copy.deepcopy(spec)
        bad["params"][0]["format"] = "torch_npu_rank_default"
        with self.assertRaisesRegex(ValueError, "format='nd'"):
            GC._resolve_tensor_shape_attr_plan(bad)

    def test_generated_layout_caseset_binds_storage_slots_and_external_ledger(self):
        spec = N6._binary_spec()
        spec["tensor_shape_attrs"] = {
            "schema_version": 1,
            "layout_requirements": [
                {"id": "left_nc", "role": "input", "tensor_name": "left",
                 "layout_kind": "noncontiguous",
                 "selector": {"kind": "profile_id", "profile_id": "rank_mismatch"}},
                {"id": "out_nc", "role": "output", "tensor_name": "out",
                 "layout_kind": "noncontiguous",
                 "selector": {"kind": "profile_id", "profile_id": "rank_mismatch"}},
            ],
        }
        with tempfile.TemporaryDirectory() as work, mock.patch.object(
                GC, "load_golden", return_value=N6._golden(N6._binary_golden)):
            caseset = GC.gen_cases(spec, work)
            case = next(row for row in caseset["cases"]
                        if row["parameter_contract"]["profile_id"] == "rank_mismatch")
            left = case["inputs"][0]
            self.assertNotIn("path", left)
            self.assertTrue(os.path.isfile(os.path.join(work, left["base_storage_path"])))
            slots = {slot["name"]: slot for slot in case["aclnn_call"]["slots"]}
            self.assertEqual(
                {key: slots["left"][key] for key in (
                    "kind", "binding", "shape", "dtype", "format",
                    "storage_representation", "layout_requirement_id",
                    "layout_receipt_sha256")},
                {key: left[key] for key in (
                    "kind", "binding", "shape", "dtype", "format",
                    "storage_representation", "layout_requirement_id",
                    "layout_receipt_sha256")})
            output = case["expected"]
            self.assertEqual(slots["out"]["layout_receipt_sha256"],
                             output["layout_receipt_sha256"])
            self.assertEqual(slots["out"]["shape"], output["out_shape"])
            validated = CEA.validate_caseset_layout_contract(caseset)
            self.assertEqual(validated["sha256"], caseset["layout_ledger_sha256"])


if __name__ == "__main__":
    unittest.main()
