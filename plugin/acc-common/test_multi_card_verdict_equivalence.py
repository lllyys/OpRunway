import copy
import unittest
from unittest import mock

import multi_card_verdict_equivalence as E


class MultiCardVerdictEquivalenceTest(unittest.TestCase):
    def _verdict(self):
        return {"status": "FAIL", "counts": {"pass": 1, "fail": 1}, "per_case": [
            {"case_id": "formal-a", "功能": "PASS", "精度": "PASS",
             "stochastic_predicate": {"seed": 7, "offset": 0}},
            {"case_id": "structural-b", "功能": "FAIL", "精度": "FAIL",
             "reason": "execution"},
        ]}

    def _manifest(self, partition="case_order_round_robin_v1"):
        return {"partition": partition, "case_order": ["formal-a", "structural-b"],
                "affinity": {"formal_case_ids": (["formal-a"] if "stochastic" in partition else [])},
                "shards": [{"shard_id": "s0", "case_ids": ["formal-a"]},
                           {"shard_id": "s1", "case_ids": ["structural-b"]}]}

    def _check(self, multi, single, manifest, *, multi_device=4, single_device=4,
               multi_sha="a" * 64, single_sha="a" * 64):
        spec, source = {"op": "Witness"}, {"source": "facts"}
        caseset = {"cases": [{"id": cid} for cid in manifest["case_order"]],
                   "emitted": 2, "requested_target": 2}
        if "stochastic" in manifest["partition"]:
            caseset["stochastic_contract"] = {"mode": "controlled"}
            caseset["cases"][0]["stochastic"] = {
                "purpose": "formal", "values": {"seed": 7, "offset": 0}}
        merged = {"shard_results": [], "execution_identity_common": {
            "soc": "A3", "cann_version": "9.0.1", "dut_library_sha256": "d" * 64,
            "dut_symbol_identity_sha256": E.multi_card_shards.digest({"id": "x"}),
            "source_provenance_sha256": "e" * 64}}
        if "stochastic" in manifest["partition"]:
            merged["shard_results"] = [{
                "case_ids": ["formal-a"],
                "execution_identity": {"device_id": multi_device,
                                       "current_device": multi_device},
                "receipts": {"cpp_extension": {
                    "stochastic_collection": {"status": "complete",
                                              "device": {"type": "npu", "index": multi_device}}}}}]
        single_evidence = {"evidence": []}
        for row in caseset["cases"]:
            evidence = {"case_id": row["id"]}
            if row.get("stochastic") is not None:
                evidence["stochastic"] = row["stochastic"]
            single_evidence["evidence"].append(evidence)
        merged["evidence"] = copy.deepcopy(single_evidence["evidence"])
        single_receipt = {
            "runtime": {"soc": "A3", "cann_version": "9.0.1"},
            "vendor": {"library_sha256": "d" * 64, "symbol_identity": {"id": "x"},
                       "build_receipt_sha256": "e" * 64}}
        if "stochastic" in manifest["partition"]:
            single_receipt["stochastic_collection"] = {
                "status": "complete", "device": {"type": "npu", "index": single_device}}
        single_evidence["cpp_extension_receipt"] = single_receipt
        receipt_sha = E.multi_card_shards.digest(single_receipt)
        for row in single_evidence["evidence"]:
            row["cpp_extension_receipt_sha256"] = receipt_sha
        merged["evidence"] = copy.deepcopy(single_evidence["evidence"])
        with mock.patch.object(E.multi_card_shards, "validate_manifest") as validate, \
                mock.patch.object(E.multi_card_shards, "merge_results", return_value=merged), \
                mock.patch.object(E.cpp_extension_adapter, "validate_receipt",
                                  return_value=single_receipt):
            result = E.build_equivalence(
                multi, single, manifest, spec, caseset, source, merged,
                single_evidence, single_receipt,
                "/formal-work",
                multi_sha=multi_sha, single_sha=single_sha, manifest_sha="c" * 64)
            validate.assert_called_once_with(manifest, spec, caseset, source)
            return result

    def test_complete_projection_detects_any_per_case_or_summary_drift(self):
        single = self._verdict()
        self.assertTrue(self._check(copy.deepcopy(single), single, self._manifest())["passed"])
        multi = copy.deepcopy(single); multi["per_case"][0]["stochastic_predicate"]["offset"] = 1
        self.assertFalse(self._check(multi, single, self._manifest())["passed"])
        multi = copy.deepcopy(single); multi["counts"]["fail"] = 0
        self.assertFalse(self._check(multi, single, self._manifest())["passed"])

    def test_complete_verdict_and_digest_are_mandatory(self):
        single = self._verdict()
        single["accuracy_summary"] = {"standard": "exact", "verify_mode": "full"}
        multi = copy.deepcopy(single)
        multi["accuracy_summary"]["verify_mode"] = "sampled"
        got = self._check(multi, single, self._manifest())
        self.assertFalse(got["complete_verdict_equal"])
        self.assertFalse(got["passed"])
        got = self._check(copy.deepcopy(single), single, self._manifest(),
                          multi_sha="a" * 64, single_sha="b" * 64)
        self.assertFalse(got["verdict_sha256_equal"])
        self.assertFalse(got["passed"])

    def test_controlled_stochastic_requires_formal_single_shard_and_never_votes(self):
        verdict = self._verdict()
        manifest = self._manifest("stochastic_formal_witness_affinity_v1")
        got = self._check(verdict, verdict, manifest)
        self.assertTrue(got["passed"])
        self.assertEqual(got["comparison_mode"], "controlled_stochastic_no_vote")
        manifest["shards"][1]["case_ids"].append("formal-a")
        self.assertFalse(self._check(verdict, verdict, manifest)["passed"])

    def test_controlled_stochastic_rejects_formal_collection_device_drift(self):
        verdict = self._verdict()
        manifest = self._manifest("stochastic_formal_witness_affinity_v1")
        got = self._check(verdict, verdict, manifest,
                          multi_device=4, single_device=1)
        self.assertFalse(got["passed"])
        self.assertFalse(got["formal_collection_device_equal"])


if __name__ == "__main__":
    unittest.main()
