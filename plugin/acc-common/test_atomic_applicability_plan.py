#!/usr/bin/env python3
"""atomic row profile applicability 的 P×A×Q 完整分母与下游变异测试。"""

import copy
import hashlib
import json
import os
import tempfile
import unittest
from unittest import mock

import numpy as np

import content_address
import cpp_extension_adapter as A
import gen_cases as G
import tensor_shape_attrs as T
import repo_adapter
import validate_acceptance_state as V


SHA_TASKDOC = "a" * 64
SHA_FACTS = "b" * 64
SHA_SOURCE = "c" * 64


def _sha(value):
    return hashlib.sha256(content_address.canonical_json_bytes(value)).hexdigest()


def _source(quote):
    return {
        "source": "taskdoc_pr_compose",
        "source_sha256": SHA_SOURCE,
        "cite": "fixture/source.cpp:1-8",
        "quote": quote,
        "interpretation": "fixture 只表达按具名输入 rank 排除不可执行 cell",
    }


def _provenance():
    return {
        "state": "resolved",
        "sources": [{"kind": "taskbook", "cite": "fixture:taskbook"}],
    }


def _profile(profile_id, shape):
    return {
        "profile_id": profile_id,
        "inputs": [{
            "name": "x", "kind": "tensor", "binding": "device_tensor",
            "shape": list(shape), "dtype": "float32", "format": "nd",
        }],
    }


def _spec():
    relation = {"rule": "follows", "operand": "x", "provenance": _provenance()}
    spec = {
        "op": "AtomicApplicabilityWitness",
        "runner_form": "cpp_extension",
        "verify_mode": "numerical",
        "operator_class": "structural",
        "params_source": "fixture",
        "params": [
            {"name": "x", "io": "in", "kind": "tensor",
             "binding": "device_tensor", "format": "nd", "dtype": ["float32"]},
            {"name": "shifts", "io": "attr", "dtype": ["int64"],
             "default": [1], "attr_type": "int_array"},
            {"name": "dims", "io": "attr", "dtype": ["int64"],
             "default": [], "attr_type": "int_array"},
            {"name": "out", "io": "out", "kind": "tensor",
             "binding": "device_tensor", "format": "nd", "dtype": ["float32"],
             "dtype_relation": copy.deepcopy(relation)},
        ],
        "precision": {
            "oracle": "ascendoptest", "standard": "ascendoptest_default",
            "case_source": "generated", "case_target": 3,
        },
        "call_variants": [{
            "when": {"always": True}, "symbol": "AtomicApplicabilityWitness",
            "active_attrs": ["shifts", "dims"], "active_outputs": ["out"],
        }],
        "multi_input_contract": {
            "schema_version": "multi_input.v1",
            "output": {
                "name": "out", "kind": "tensor", "binding": "device_tensor",
                "format": "nd", "shape": copy.deepcopy(relation),
                "dtype": copy.deepcopy(relation),
            },
            "profiles": [_profile("rank0", []), _profile("rank2", [2, 3])],
            "required_coverage": {"rank0_tensor": 1},
        },
    }
    source_binding = {
        "compose_kind": "spec_taskdoc_compose",
        "spec_sha256": _sha(spec),
        "taskdoc_snapshot_sha256": SHA_TASKDOC,
        "source_facts_sha256": SHA_FACTS,
    }
    rows = [
        {"id": "flatten", "attrs": {"shifts": [1], "dims": []},
         "source_parse": _source("empty dims accepts one shift")},
        {"id": "axis", "attrs": {"shifts": [1], "dims": [-1]},
         "source_parse": _source("axis values are cyclic"),
         "applicability": {
             "rank_domain": {"input": "x", "allowed_ranks": [1, 2]},
             "required_nonempty_attrs": ["dims"],
             "source_parse": _source("non-empty dims require rank at least one"),
         }},
    ]
    groups = [{
        "id": "lengths", "members": ["shifts", "dims"],
        "relation": "equal_length",
        "exceptions": [{
            "lengths": {"shifts": 1, "dims": 0},
            "source_parse": _source("empty dims accepts one shift"),
        }],
    }]
    atomic = T.normalize_atomic_attr_rows(
        rows, attr_types={"shifts": "int_array", "dims": "int_array"},
        constraint_groups=groups, source_binding=source_binding)
    spec["tensor_shape_attrs"] = {
        "schema_version": 1,
        "atomic_attr_rows": {
            "attr_names": ["shifts", "dims"],
            "expected_sha256": atomic["sha256"],
            "source_binding": source_binding,
            "constraint_groups": groups,
            "rows": rows,
        },
        "cyclic_indices": [{
            "attr": "dims", "rank_from_input": "x", "duplicate_policy": "allow",
        }],
    }
    return spec


def _golden(inputs, attrs):
    value = np.asarray(inputs[0])
    dims = attrs["dims"]
    if dims:
        return np.roll(value, shift=tuple(attrs["shifts"]), axis=tuple(dims))
    return np.roll(value, shift=attrs["shifts"][0])


def _loaded_golden():
    return G.Golden(_golden, "fixture", "fixture", None, None)


class AtomicApplicabilityPlanTest(unittest.TestCase):
    def _generate(self):
        spec = _spec()
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        with mock.patch.object(G, "load_golden", return_value=_loaded_golden()):
            caseset = G.gen_cases(spec, work.name)
        return spec, caseset

    def test_full_denominator_distinguishes_executable_and_excluded(self):
        _spec_value, caseset = self._generate()
        self.assertEqual(len(caseset["cases"]), 3)
        ledger = caseset["atomic_attr_ledger"]
        self.assertEqual(ledger["schema_version"], 2)
        self.assertEqual(ledger["structure_denominator_total"], 4)
        self.assertEqual(
            (ledger["planned"], ledger["excluded"], ledger["emitted"]),
            (3, 1, 3),
        )
        self.assertEqual(len(ledger["cells"]), 3)
        self.assertEqual(len(ledger["excluded_cells"]), 1)
        excluded = ledger["excluded_cells"][0]
        self.assertEqual((excluded["profile_id"], excluded["row_id"]),
                         ("rank0", "axis"))
        self.assertEqual(excluded["applicability"]["status"], "excluded")
        self.assertEqual(
            [reason["kind"] for reason in excluded["applicability"]["reasons"]],
            ["rank_outside_domain"],
        )
        self.assertEqual(
            {cell["applicability"]["status"] for cell in ledger["cells"]},
            {"executable"},
        )
        self.assertEqual(
            len({cell["cell_id"] for cell in [*ledger["cells"],
                                               *ledger["excluded_cells"]]}),
            4,
        )
        A.validate_caseset_tensor_shape_attr_contract(caseset)

    def test_dry_run_and_generation_share_the_same_denominator(self):
        spec, caseset = self._generate()
        golden_root = tempfile.TemporaryDirectory()
        self.addCleanup(golden_root.cleanup)
        golden_path = os.path.join(golden_root.name, "golden.py")
        with open(golden_path, "w", encoding="utf-8") as dst:
            dst.write("# dependency bytes for dry-run fixture\n")
        with mock.patch.object(G, "load_golden", return_value=_loaded_golden()), \
                mock.patch.object(repo_adapter, "op_dir", return_value=golden_root.name):
            dry = G._build_dry_run_ledger(spec)
        self.assertEqual(
            dry["coverage"]["atomic_attr_ledger"],
            caseset["atomic_attr_ledger"],
        )
        self.assertEqual(dry["planning"]["case_target"], 3)

    def test_denominator_mutations_fail_closed_after_digest_rewrite(self):
        _spec_value, caseset = self._generate()
        mutations = []

        missing = copy.deepcopy(caseset)
        missing["atomic_attr_ledger"]["excluded_cells"] = []
        missing["atomic_attr_ledger"]["excluded"] = 0
        mutations.append((missing, "分母|P×A×Q|excluded"))

        duplicate = copy.deepcopy(caseset)
        duplicate["atomic_attr_ledger"]["excluded_cells"][0]["cell_id"] = \
            duplicate["atomic_attr_ledger"]["cells"][0]["cell_id"]
        mutations.append((duplicate, "cell_id|identity"))

        forged = copy.deepcopy(caseset)
        app = forged["atomic_attr_ledger"]["excluded_cells"][0]["applicability"]
        app["rank_domain"]["actual_rank"] = 7
        mutations.append((forged, "applicability|actual_rank|具名"))

        for mutated, message in mutations:
            mutated["atomic_attr_ledger_sha256"] = _sha(
                mutated["atomic_attr_ledger"])
            with self.subTest(message=message):
                with self.assertRaisesRegex(A.CppExtensionAdapterError, message):
                    A.validate_caseset_tensor_shape_attr_contract(mutated)

    def test_coherent_excluded_rank_rewrite_is_rejected_by_staged_spec(self):
        spec, caseset = self._generate()
        forged = copy.deepcopy(caseset)
        app = forged["atomic_attr_ledger"]["excluded_cells"][0]["applicability"]
        app["rank_domain"]["actual_rank"] = 7
        app["rank_domain"]["matched"] = False
        app["reasons"][0]["actual_rank"] = 7
        forged["atomic_attr_ledger_sha256"] = _sha(forged["atomic_attr_ledger"])
        # caseset 内部仍自洽；真正的外锚是 staged spec 的具名 profile shape。
        A.validate_caseset_tensor_shape_attr_contract(forged)
        facts = {"taskdoc": {"snapshot_sha256": SHA_TASKDOC}}
        errors = []
        with mock.patch.object(V.source_facts_lookup, "find_source_facts",
                               return_value=facts), \
                mock.patch.object(V.content_address, "content_digest",
                                  return_value=SHA_FACTS):
            V._gate_tensor_shape_attr_spec_authority(
                {}, forged, spec, errors, source_facts_path="fixture")
        self.assertTrue(any(
            "applicability" in error and "具名 profile" in error
            for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
