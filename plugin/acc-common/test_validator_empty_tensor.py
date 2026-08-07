"""空 tensor 精度裁决：结构闭环可通过，裸 ``numel=0`` 仍 fail-closed。"""

import copy
import json
import os
import tempfile
import unittest

import numpy as np

import precision_policy as P
import repo_adapter as RA
import validator as V


_SHA_A = "a" * 64
_SHA_B = "b" * 64


def _bundle(dtype="float32", standard=P.TORCH_ALLCLOSE):
    spec = {
        "op": "DemoEmptyTensor",
        "verify_mode": "numerical",
        "precision": {
            "oracle": "torch",
            "standard": standard,
            "tolerance_source": "dtype_table",
        },
        "params": [
            {"name": "x", "io": "in", "dtype": [dtype]},
            {"name": "out", "io": "out", "dtype": [dtype]},
        ],
    }
    effective = P.effective_standard(standard, dtype, None)
    policy = P.threshold_for(effective, dtype, "dtype_table")
    tpid = P.tolerance_policy_id(effective, dtype)
    threshold = P.threshold_digest(policy)
    metric_key = {
        P.EXACT: "exact_mismatch",
        P.ASCENDOPTEST_DEFAULT: "bad_count",
        P.TORCH_ALLCLOSE: "mismatch",
    }[policy["kind"]]
    case = {
        "id": "empty0",
        "dims": ["功能", "精度"],
        "inputs": [{
            "name": "x", "shape": [0, 3], "dtype": dtype,
            "path": "empty0/x1.npy",
        }],
        "attrs": {},
        "expected": {
            "verify_mode": "numerical",
            "compare_dtype": dtype,
            "standard": effective,
            "tolerance_policy_id": tpid,
            "policy": policy,
            "threshold": threshold,
            "out_shape": [0, 3],
        },
    }
    evidence = {
        "case_id": "empty0",
        "status": "ok",
        "precision": {
            "standard": effective,
            "tolerance_policy_id": tpid,
            "policy": policy,
            "threshold": threshold,
            "metrics": {metric_key: 0, "numel": 0},
            "out_shape": [0, 3],
            "provenance": {
                "golden_sha256": _SHA_A,
                "out_sha256": _SHA_B,
                "numel": 0,
            },
        },
        "output_written_check": "skipped_empty",
        "output_written_diagnostic": {
            "status": "skipped_empty",
            "output_index": 0,
            "output_name": "out",
            "dtype": dtype,
            "numel": 0,
            "sentinel_hits": None,
            "hit_ratio": None,
        },
    }
    return (spec, {"op": spec["op"], "cases": [case]},
            {"op": spec["op"], "evidence": [evidence]})


def _per(verdict):
    return verdict["per_case"][0]


class EmptyTensorStructuralVerdictTest(unittest.TestCase):
    def test_counter_policies_pass_only_with_full_structure_receipt(self):
        for dtype, standard in (
                ("float32", P.TORCH_ALLCLOSE),
                ("float32", P.ASCENDOPTEST_DEFAULT),
                ("int32", P.TORCH_ALLCLOSE)):  # int32 据 dtype 收紧为 exact
            with self.subTest(dtype=dtype, standard=standard):
                verdict = V.validate(*_bundle(dtype, standard))
                self.assertEqual(verdict["overall"]["verdict"], "pass", verdict)
                self.assertEqual(_per(verdict)["精度"], "pass", verdict)
                self.assertIn("empty-structural exact", _per(verdict)["判据"])

    def test_pure_judges_still_reject_bare_zero_numel(self):
        self.assertEqual(V.judge_exact(
            {"kind": P.EXACT, "max_mismatch": 0},
            {"exact_mismatch": 0, "numel": 0})[0], "fail")
        self.assertEqual(V.judge_torch_allclose(
            {"kind": P.TORCH_ALLCLOSE},
            {"mismatch": 0, "numel": 0})[0], "fail")
        self.assertEqual(V.judge_ascendoptest(
            {"kind": P.ASCENDOPTEST_DEFAULT, "error_rate": 0.0},
            {"bad_count": 0, "numel": 0})[0], "fail")

    def test_missing_driver_actual_shape_is_rejected(self):
        bundle = _bundle()
        del bundle[2]["evidence"][0]["precision"]["out_shape"]
        verdict = V.validate(*bundle)
        self.assertEqual(verdict["overall"]["verdict"], "fail", verdict)
        self.assertIn("缺 driver manifest 实际 out_shape", _per(verdict)["判据"])

    def test_actual_shape_mutation_is_rejected(self):
        bundle = _bundle()
        bundle[2]["evidence"][0]["precision"]["out_shape"] = [0, 1]
        verdict = V.validate(*bundle)
        self.assertEqual(verdict["overall"]["verdict"], "fail", verdict)
        self.assertIn("实际输出形状", _per(verdict)["判据"])

    def test_provenance_or_output_written_mutation_is_rejected(self):
        for mutate, needle in (
                (lambda e: e["precision"].pop("provenance"), "provenance.numel"),
                (lambda e: e["precision"]["provenance"].__setitem__("out_sha256", "bad"),
                 "out_sha256"),
                (lambda e: e.pop("output_written_diagnostic"),
                 "output_written_diagnostic"),
                (lambda e: e["output_written_diagnostic"].__setitem__("dtype", "float16"),
                 "写入诊断 dtype"),
        ):
            with self.subTest(needle=needle):
                bundle = _bundle()
                mutate(bundle[2]["evidence"][0])
                verdict = V.validate(*bundle)
                self.assertEqual(verdict["overall"]["verdict"], "fail", verdict)
                self.assertIn(needle, _per(verdict)["判据"])

    def test_nonzero_counter_or_forged_nonempty_shape_is_rejected(self):
        bundle = _bundle()
        bundle[2]["evidence"][0]["precision"]["metrics"]["mismatch"] = 1
        verdict = V.validate(*bundle)
        self.assertEqual(verdict["overall"]["verdict"], "fail", verdict)
        self.assertIn("mismatch 须严格", _per(verdict)["判据"])

        bundle = _bundle()
        bundle[1]["cases"][0]["expected"]["out_shape"] = [1, 3]
        bundle[2]["evidence"][0]["precision"]["out_shape"] = [1, 3]
        verdict = V.validate(*bundle)
        self.assertEqual(verdict["overall"]["verdict"], "fail", verdict)
        self.assertIn("输出形状对账", _per(verdict)["判据"])


class CppExtensionEmptyEvidenceIntegrationTest(unittest.TestCase):
    def test_driver_manifest_shape_flows_into_single_output_evidence(self):
        spec, caseset, _ = _bundle()
        case = caseset["cases"][0]
        case["expected"].update({
            "golden_path": "empty0/golden.npy",
            "golden_source": "torch torch.empty",
        })
        with tempfile.TemporaryDirectory() as work:
            out_dir = os.path.join(work, "cpp_extension_out")
            os.makedirs(os.path.join(work, "empty0"))
            os.makedirs(os.path.join(out_dir, "empty0"))
            np.save(os.path.join(work, "empty0", "golden.npy"),
                    np.empty((0, 3), dtype=np.float32))
            open(os.path.join(out_dir, "empty0", "out_0.bin"), "wb").close()
            manifest = {
                "produced": [{
                    "case_id": "empty0",
                    "outputs": [{
                        "index": 0,
                        "name": "out",
                        "role": "value",
                        "path": "empty0/out_0.bin",
                        "dtype": "float32",
                        "shape": [0, 3],
                        "output_written_check": "skipped_empty",
                        "output_written_diagnostic": {
                            "status": "skipped_empty",
                            "output_index": 0,
                            "output_name": "out",
                            "dtype": "float32",
                            "numel": 0,
                            "sentinel_hits": None,
                            "hit_ratio": None,
                        },
                    }],
                }],
                "failed": [],
            }
            with open(os.path.join(out_dir, "out_manifest.json"), "w", encoding="utf-8") as dst:
                json.dump(manifest, dst)
            evidence_rows = RA.build_multi_output_evidence(caseset, work, out_dir)
            precision = evidence_rows[0]["precision"]
            self.assertEqual(precision["out_shape"], [0, 3])
            self.assertEqual(precision["out_dtype"], "float32")
            verdict = V.validate(
                spec, caseset, {"op": spec["op"], "evidence": evidence_rows})
            self.assertEqual(verdict["overall"]["verdict"], "pass", verdict)


if __name__ == "__main__":
    unittest.main()
