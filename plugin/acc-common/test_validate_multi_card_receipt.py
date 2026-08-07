import copy
import hashlib
import json
import os
import tempfile
import unittest
from unittest import mock

import cann_version
import multi_card_shards as S
import validate_acceptance_state as V


class ValidateMultiCardReceiptTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.spec = {"op": "Witness"}; self.source = {"source": "facts"}
        self.cases = {"cases": [{"id": "c0", "inputs": [{"shape": [2]}]},
                                  {"id": "c1", "inputs": [{"shape": [2]}]}],
                      "emitted": 2, "requested_target": 2}
        self.manifest = S.build_manifest(self.spec, self.cases, self.source, [1, 2])
        self.results = []
        self.envelopes = []
        self.receipts = {}
        for shard in self.manifest["shards"]:
            work = os.path.join(self.d, shard["shard_id"])
            os.makedirs(work)
            projected = S.subset_caseset(
                self.cases, shard["case_ids"], manifest=self.manifest, shard=shard,
                work_dir=work, require_empty=False)
            receipt = {"schema": "oprunway.cpp_extension_receipt",
                       "schema_version": cann_version.RECEIPT_SCHEMA_VERSION,
                       "status": "VERIFIED", "id": shard["shard_id"],
                       "bindings": {"spec_sha256": S.digest(self.spec)},
                       "runtime": {"soc": "A3", "cann_version": "9.0.1"},
                       "artifact": {"path": "artifact.so",
                                    "sha256": hashlib.sha256(b"elf").hexdigest()},
                       "vendor": {"library_sha256": "a" * 64,
                                  "symbol_identity": {"id": "x"},
                                  "build_receipt_sha256": "b" * 64}}
            identity = {
                "device_id": shard["device_id"], "current_device": shard["device_id"],
                "pre_execution_device_smoke": {
                    "schema": "oprunway.multi_card_pre_execution_device_smoke",
                    "schema_version": S.VERSION, "device_id": shard["device_id"],
                    "current_device": shard["device_id"],
                    "work_dir_initially_absent": True,
                    "successful_output_case_ids": self.manifest["common_smoke_case_ids"],
                    "common_smoke_case_ids": self.manifest["common_smoke_case_ids"],
                    "common_smoke_caseset_sha256": S.digest(
                        projected["common_smoke_caseset_template"]),
                    "work_dir": work + ".pre-smoke",
                    "dut_library_sha256": "a" * 64,
                    "symbol_identity_sha256": S.digest({"id": "x"}),
                    "soc": "A3", "cann_version": "9.0.1",
                    "source_provenance_sha256": "b" * 64},
                "soc": "A3", "cann_version": "9.0.1", "dut_library_sha256": "a" * 64,
                "dut_symbol_identity_sha256": S.digest({"id": "x"}),
                "source_provenance_sha256": "b" * 64}
            receipt_sha = S.digest(receipt)
            identity["pre_execution_device_smoke"][
                "cpp_extension_receipt_sha256"] = receipt_sha
            env = {"evidence": [{"case_id": shard["case_ids"][0],
                                  "cpp_extension_receipt_sha256": receipt_sha}],
                   "cpp_extension_receipt": receipt}
            self.envelopes.append(env)
            self.receipts[work] = receipt
            smoke_work = work + ".pre-smoke"
            os.makedirs(smoke_work)
            self.receipts[smoke_work] = receipt
            smoke_env = {"evidence": [
                {"case_id": cid, "status": "ok", "output_written_check": "passed",
                 "precision": {"golden_path": f"{cid}/golden.npy",
                               "out_path": f"{cid}/out.npy",
                               "provenance": {
                                   "golden_sha256": hashlib.sha256(b"gold").hexdigest(),
                                   "out_sha256": hashlib.sha256(b"out").hexdigest()}}}
                for cid in self.manifest["common_smoke_case_ids"]]}
            for row in smoke_env["evidence"]:
                row["cpp_extension_receipt_sha256"] = receipt_sha
            with open(os.path.join(smoke_work, "evidence.json"), "w") as out:
                json.dump(smoke_env, out)
            os.makedirs(os.path.join(smoke_work, "cpp_extension"))
            for name, value in (
                    ("caseset.json", {**projected["common_smoke_caseset_template"],
                                      "work_dir": smoke_work}),
                    ("cpp_extension_caseset.json",
                     {**projected["common_smoke_caseset_template"], "work_dir": smoke_work}),
                    ("cpp_extension_invocation_plan.json", {"cases": []}),
                    ("cpp_extension_receipt.json", receipt)):
                with open(os.path.join(smoke_work, name), "w") as out:
                    json.dump(value, out)
            with open(os.path.join(smoke_work, "cpp_extension", "extension_manifest.json"), "w") as out:
                json.dump({}, out)
            with open(os.path.join(smoke_work, "artifact.so"), "wb") as out:
                out.write(b"elf")
            for cid in self.manifest["common_smoke_case_ids"]:
                os.makedirs(os.path.join(smoke_work, cid))
                with open(os.path.join(smoke_work, cid, "golden.npy"), "wb") as out:
                    out.write(b"gold")
                with open(os.path.join(smoke_work, cid, "out.npy"), "wb") as out:
                    out.write(b"out")
            with open(os.path.join(work, "evidence.json"), "w") as out:
                json.dump(env, out)
            with open(os.path.join(work, "cpp_extension_invocation_plan.json"), "w") as out:
                json.dump({"cases": []}, out)
            self.results.append(S.build_result(
                self.manifest, shard, projected, env, identity))
            os.makedirs(os.path.join(work, "cpp_extension"))
            for name, value in (
                    ("caseset.json", projected), ("cpp_extension_caseset.json", projected),
                    ("cpp_extension_receipt.json", receipt),
                    ("shard_result.json", self.results[-1]),
                    ("device_identity.json", identity)):
                with open(os.path.join(work, name), "w") as out:
                    json.dump(value, out)
            with open(os.path.join(work, "cpp_extension", "extension_manifest.json"), "w") as out:
                json.dump({}, out)
        self.merged = S.merge_results(
            self.manifest, self.results, self.spec, self.cases, self.source)
        self.top = S.assemble_envelope(self.manifest, self.merged, self.envelopes)
        for name, value in (("spec.json", self.spec), ("source_facts.json", self.source),
                            ("multi_card_manifest.json", self.manifest),
                            ("multi_card_merged_evidence.json", self.merged)):
            with open(os.path.join(self.d, name), "w", encoding="utf-8") as out:
                json.dump(value, out)

    def test_valid_parent_recomputed_receipt_passes(self):
        errs = []
        with mock.patch.object(
                V, "_gate_precision_work_dir",
                side_effect=lambda root, work, cases, envelope, spec, errs,
                source_facts_path=None, **kwargs: self.receipts[work]), \
                mock.patch.object(V.cpp_extension_adapter, "validate_receipt",
                                  side_effect=lambda work, cases: self.receipts[work]), \
                mock.patch.object(V, "_gate_cpp_extension_invocation_accounting"), \
                mock.patch.object(V.vendor_build_receipt, "validate_for_acceptance",
                                  return_value={"provenance_kind": "gitcode_pr"}), \
                mock.patch.object(V, "_gate_build_receipt_source_binding"), \
                mock.patch.object(V.cpp_extension_adapter,
                                  "validate_stochastic_collection", return_value=None), \
                mock.patch.object(V, "_gate_cann_runtime_requirement"), \
                mock.patch.object(V, "_gate_dtype_requirement_sets_authority"), \
                mock.patch.object(V, "_gate_tensor_shape_attr_spec_authority"), \
                mock.patch.object(V, "_gate_cpp_extension_layout"), \
                mock.patch.object(V, "_gate_cpp_extension_tensor_shape_attrs"), \
                mock.patch.object(V, "_gate_cpp_extension_stage2_evidence"):
            V._gate_multi_card_receipt(
                self.d, self.cases, self.top, errs, None)
        self.assertEqual(errs, [])

    def test_each_shard_must_pass_formal_receipt_semantics(self):
        errs = []
        def reject(root, work, cases, envelope, spec, errs, source_facts_path=None,
                   **kwargs):
            errs.append("vendor.symbol_identity 漂移")
        with mock.patch.object(V, "_gate_precision_work_dir", side_effect=reject):
            V._gate_multi_card_receipt(
                self.d, self.cases, self.top, errs, None)
        self.assertTrue(any("共享 precision work 门失败" in item for item in errs), errs)

    def test_projection_receipt_device_and_parent_mutations_are_blocked(self):
        mutations = []
        x = copy.deepcopy(self.merged); x["shard_results"][0]["device_id"] = 9; mutations.append(x)
        x = copy.deepcopy(self.merged); x["shard_results"][0]["shard_caseset"]["cases"] = []; mutations.append(x)
        x = copy.deepcopy(self.merged); x["shard_results"][0]["receipts"]["output"]["case_ids"] = []; mutations.append(x)
        for value in mutations:
            with self.subTest(value=value):
                with open(os.path.join(self.d, "multi_card_merged_evidence.json"), "w") as out:
                    json.dump(value, out)
                errs = []
                with mock.patch.object(
                        V, "_gate_precision_work_dir",
                        side_effect=lambda root, work, cases, envelope, spec, errs,
                        source_facts_path=None, **kwargs: self.receipts[work]), \
                        mock.patch.object(V.cpp_extension_adapter, "validate_receipt",
                                          side_effect=lambda work, cases: self.receipts[work]), \
                        mock.patch.object(V, "_gate_cpp_extension_invocation_accounting"), \
                        mock.patch.object(V.vendor_build_receipt, "validate_for_acceptance",
                                          return_value={"provenance_kind": "gitcode_pr"}), \
                        mock.patch.object(V, "_gate_build_receipt_source_binding"), \
                        mock.patch.object(V.cpp_extension_adapter,
                                          "validate_stochastic_collection", return_value=None), \
                        mock.patch.object(V, "_gate_cann_runtime_requirement"), \
                        mock.patch.object(V, "_gate_dtype_requirement_sets_authority"), \
                        mock.patch.object(V, "_gate_tensor_shape_attr_spec_authority"), \
                        mock.patch.object(V, "_gate_cpp_extension_layout"), \
                        mock.patch.object(V, "_gate_cpp_extension_tensor_shape_attrs"), \
                        mock.patch.object(V, "_gate_cpp_extension_stage2_evidence"):
                    V._gate_multi_card_receipt(
                        self.d, self.cases, self.top, errs, None)
                self.assertTrue(errs)

    def test_projection_work_dir_escape_is_blocked(self):
        bad = copy.deepcopy(self.merged)
        bad["shard_results"][0]["shard_caseset"]["work_dir"] = "/work/elsewhere"
        # Repair self-digests so the assertion reaches the semantic realpath gate.
        row = bad["shard_results"][0]
        row["shard_caseset_sha256"] = S.digest(row["shard_caseset"])
        with open(os.path.join(self.d, "multi_card_merged_evidence.json"), "w") as out:
            json.dump(bad, out)
        errs = []
        V._gate_multi_card_receipt(
            self.d, self.cases, self.top, errs, None)
        self.assertTrue(errs)

    def test_stochastic_smoke_transport_output_is_rehashed(self):
        work = tempfile.mkdtemp()
        rel = "cpp_extension_out/c0/out_0.bin"
        path = os.path.join(work, rel)
        os.makedirs(os.path.dirname(path))
        with open(path, "wb") as dst:
            dst.write(b"actual stochastic output")
        claimed = hashlib.sha256(b"actual stochastic output").hexdigest()
        rows = [{
            "case_id": "c0", "status": "ok",
            "precision": {"compare": "stochastic", "transport_outputs": [{
                "index": 0, "name": "out", "out_path": rel,
                "out_sha256": claimed,
            }]},
        }]
        errs = []
        self.assertEqual(V._gate_shard_precision_files(
            work, None, rows, errs, require_output=True), 1)
        self.assertEqual(errs, [])
        with open(path, "wb") as dst:
            dst.write(b"tampered")
        errs = []
        self.assertEqual(V._gate_shard_precision_files(
            work, None, rows, errs, require_output=True), 0)
        self.assertTrue(any("sha256" in item for item in errs), errs)


if __name__ == "__main__":
    unittest.main()
