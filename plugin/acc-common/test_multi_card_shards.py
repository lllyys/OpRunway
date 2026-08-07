import copy
import os
import tempfile
import unittest

import cann_version
import cpp_extension_adapter
import multi_card_shards as S
from test_gen_cases_tensor_shape_attrs import _atomic_caseset


class MultiCardShardTest(unittest.TestCase):
    def setUp(self):
        self.spec={"op":"X"}; self.source={"digest":"a"*64}
        self.caseset={"cases":[{"id":f"c{i}","inputs":[{"shape":[2]}]} for i in range(7)],"emitted":7,"requested_target":7}
        self.manifest=S.build_manifest(self.spec,self.caseset,self.source,[4,6,7])

    def test_round_robin_is_deterministic_and_closed(self):
        self.assertEqual(self.manifest,S.build_manifest(self.spec,self.caseset,self.source,[4,6,7]))
        self.assertEqual([x["case_ids"] for x in self.manifest["shards"]],
                         [["c0","c3","c6"],["c1","c4"],["c2","c5"]])

    def test_missing_duplicate_unknown_and_binding_drift_fail(self):
        for mutate in (
            lambda x:x["shards"][1]["case_ids"].pop(),
            lambda x:x["shards"][1]["case_ids"].append("c0"),
            lambda x:x["shards"][1]["case_ids"].append("outside"),
            lambda x:x["bindings"].__setitem__("spec_sha256","0"*64)):
            value=copy.deepcopy(self.manifest); mutate(value)
            with self.assertRaises(S.ShardContractError):
                S.validate_manifest(value,self.spec,self.caseset,self.source)

    def _results(self):
        rows=[]
        for shard in self.manifest["shards"]:
            subset=S.subset_caseset(self.caseset,shard["case_ids"],manifest=self.manifest,
                                    shard=shard,work_dir=f"/tmp/{shard['shard_id']}")
            receipt={
                     "schema":"oprunway.cpp_extension_receipt",
                     "schema_version":cann_version.RECEIPT_SCHEMA_VERSION,
                     "status":"VERIFIED","shard":shard["shard_id"],
                     "runtime":{"soc":"A3","cann_version":"9.0.1"},
                     "vendor":{"library_sha256":"b"*64,"symbol_identity":{"id":"x"},
                               "build_receipt_sha256":"c"*64}}
            env={"evidence":[{"case_id":cid,"precision":{"status":"ok"}} for cid in shard["case_ids"]],
                 "cpp_extension_receipt":receipt,
                 "output_receipt":{"schema":"output","shard":shard["shard_id"]}}
            identity={"device_id":shard["device_id"],"current_device":shard["device_id"],
                      "pre_execution_device_smoke":{
                          "schema":"oprunway.multi_card_pre_execution_device_smoke",
                          "schema_version":S.VERSION,"device_id":shard["device_id"],
                          "current_device":shard["device_id"],
                          "work_dir_initially_absent":True,
                          "successful_output_case_ids":self.manifest["common_smoke_case_ids"],
                          "common_smoke_case_ids":self.manifest["common_smoke_case_ids"],
                          "common_smoke_caseset_sha256":S.digest(
                              subset["common_smoke_caseset_template"]),
                          "dut_library_sha256":"b"*64,
                          "symbol_identity_sha256":S.digest({"id":"x"}),
                          "soc":"A3","cann_version":"9.0.1",
                          "source_provenance_sha256":"c"*64},
                      "soc":"A3","cann_version":"9.0.1",
                      "dut_library_sha256":"b"*64,
                      "dut_symbol_identity_sha256":S.digest({"id":"x"}),
                      "source_provenance_sha256":"c"*64}
            rows.append(S.build_result(self.manifest,shard,subset,env,identity))
        return rows

    def test_merge_restores_case_order(self):
        got=S.merge_results(self.manifest,list(reversed(self._results())),
                            self.spec,self.caseset,self.source)
        self.assertEqual([x["case_id"] for x in got["evidence"]],[f"c{i}" for i in range(7)])
        envelopes=[]
        for result in self._results():
            envelopes.append({"runner_form":"cpp_extension","evidence_grade":"acceptance",
                              "op":"X","performance_collected":False,"repo_mode":"x",
                              "source_provenance":{},"task_scope":"precision_only",
                              "evidence":result["evidence"],
                              "cpp_extension_receipt":result["receipts"]["cpp_extension"]})
        envelope=S.assemble_envelope(self.manifest,got,envelopes)
        self.assertEqual(envelope["evidence"],got["evidence"])
        self.assertNotIn("cpp_extension_receipt",envelope)
        bad=copy.deepcopy(envelopes); bad[1]["repo_mode"]="drift"
        with self.assertRaises(S.ShardContractError): S.assemble_envelope(self.manifest,got,bad)

    def test_subset_is_bound_and_rebuilds_denominator(self):
        shard=self.manifest["shards"][0]
        got=S.subset_caseset(self.caseset,shard["case_ids"],manifest=self.manifest,
                             shard=shard,work_dir="/tmp/shard")
        self.assertEqual(got["emitted"],3); self.assertEqual(got["pool_max"],3)
        self.assertEqual(got["shard_projection"]["parent_caseset_sha256"],
                         self.manifest["bindings"]["caseset_sha256"])
        self.assertIs(S.validate_subset_caseset(got,self.manifest,shard),got)

    def test_merge_rejects_duplicate_missing_and_identity_drift(self):
        rows=self._results()
        def merge(value):
            return S.merge_results(self.manifest,value,self.spec,self.caseset,self.source)
        with self.assertRaises(S.ShardContractError): merge(rows[:-1])
        bad=copy.deepcopy(rows); bad[1]["shard_id"]=bad[0]["shard_id"]
        with self.assertRaises(S.ShardContractError): merge(bad)
        bad=copy.deepcopy(rows); bad[1]["execution_identity"]["soc"]="other"
        with self.assertRaises(S.ShardContractError): merge(bad)
        bad=copy.deepcopy(rows); bad[1]["receipts"]["output"]["case_ids"]=[]
        with self.assertRaises(S.ShardContractError): merge(bad)
        bad=copy.deepcopy(rows); bad[1]["execution_identity"]["current_device"]=99
        with self.assertRaises(S.ShardContractError): merge(bad)
        bad=copy.deepcopy(rows); bad[1]["evidence"][0]["precision"]["status"]="tampered"
        with self.assertRaises(S.ShardContractError): merge(bad)
        bad=copy.deepcopy(rows); bad[1]["evidence"][0]["case_id"]="c0"
        bad[1]["evidence_sha256"]=S.digest(bad[1]["evidence"])
        with self.assertRaises(S.ShardContractError): merge(bad)

    def test_empty_shard_unknown_partition_and_parent_projection_drift_rejected(self):
        with self.assertRaises(S.ShardContractError):
            S.build_manifest(self.spec,{"cases":[{"id":"only"}]},self.source,[1,2])
        bad=copy.deepcopy(self.manifest); bad["partition"]="custom"
        with self.assertRaises(S.ShardContractError):
            S.validate_manifest(bad,self.spec,self.caseset,self.source)
        bad=copy.deepcopy(self.manifest)
        bad["shards"][0]["case_ids"][0],bad["shards"][1]["case_ids"][0] = (
            bad["shards"][1]["case_ids"][0],bad["shards"][0]["case_ids"][0])
        with self.assertRaises(S.ShardContractError):
            S.validate_manifest(bad,self.spec,self.caseset,self.source)
        rows=self._results(); rows[0]["shard_caseset"]["cases"][0]["id"]="forged"
        rows[0]["shard_caseset_sha256"]=S.digest(rows[0]["shard_caseset"])
        with self.assertRaises(S.ShardContractError):
            S.merge_results(self.manifest,rows,self.spec,self.caseset,self.source)

    def test_materialize_rejects_asset_escape(self):
        parent=copy.deepcopy(self.caseset); parent["work_dir"]="/tmp"
        parent["cases"][0]["inputs"]=[{"path":"../escape.npy"}]
        shard=self.manifest["shards"][0]
        projected=S.subset_caseset(parent,shard["case_ids"],manifest=self.manifest,
                                   shard=shard,work_dir="/tmp/shard")
        with tempfile.TemporaryDirectory() as out:
            with self.assertRaises(S.ShardContractError):
                S.materialize_case_assets(parent,projected,out)

    def test_atomic_profiles_are_affinity_groups(self):
        cases={"cases":[{"id":cid,"inputs":[{"shape":[2]}]}
                          for cid in ("p0-a","p0-b","p1-a","p1-b")],
               "atomic_attr_ledger":{"cells":[
                   {"case_id":"p0-a","profile_id":"p0"},{"case_id":"p0-b","profile_id":"p0"},
                   {"case_id":"p1-a","profile_id":"p1"},{"case_id":"p1-b","profile_id":"p1"}]}}
        got=S.build_manifest(self.spec,cases,self.source,[4,6])
        self.assertEqual(got["partition"],"atomic_profile_affinity_round_robin_v1")
        self.assertEqual(got["shards"][0]["case_ids"],["p0-a","p0-b"])
        self.assertEqual(got["shards"][1]["case_ids"],["p1-a","p1-b"])
        self.assertEqual(got["common_smoke_case_ids"], ["p0-a"])
        shard = got["shards"][0]
        projected = S.subset_caseset(
            cases, shard["case_ids"], manifest=got, shard=shard,
            work_dir="/tmp/atomic-smoke", require_empty=False)
        smoke = projected["common_smoke_caseset_template"]
        self.assertEqual([row["id"] for row in smoke["cases"]], ["p0-a"])
        self.assertEqual(smoke["execution_scope"], "environment_identity_smoke")
        self.assertEqual(smoke["atomic_attr_ledger"]["planned"], 1)
        self.assertEqual(smoke["atomic_attr_ledger"]["structure_denominator_total"], 1)

    def test_real_atomic_contract_smoke_projection_is_one_cell_and_valid(self):
        cases = _atomic_caseset()
        # 真实 schema fixture 的 tensor profile 非空，可作为环境 smoke。
        manifest = S.build_manifest(self.spec, cases, self.source, [4, 6])
        shard = manifest["shards"][0]
        projected = S.subset_caseset(
            cases, shard["case_ids"], manifest=manifest, shard=shard,
            work_dir="/tmp/atomic-real-smoke", require_empty=False)
        smoke = projected["common_smoke_caseset_template"]
        ledger = smoke["atomic_attr_ledger"]
        self.assertEqual((ledger["profiles"], ledger["atomic_rows"],
                          ledger["independent_combinations"], ledger["emitted"]),
                         (1, 1, 1, 1))
        cpp_extension_adapter.validate_caseset_tensor_shape_attr_contract(smoke)
        for key in ("profiles", "atomic_rows", "independent_combinations"):
            bad = copy.deepcopy(smoke)
            bad["atomic_attr_ledger"][key] = 2
            bad["atomic_attr_ledger_sha256"] = S.digest(bad["atomic_attr_ledger"])
            with self.subTest(key=key), self.assertRaises(
                    cpp_extension_adapter.CppExtensionAdapterError):
                cpp_extension_adapter.validate_caseset_tensor_shape_attr_contract(bad)

    def test_stochastic_formal_witness_is_not_split(self):
        cases={"cases":[
            {"id":"f0","stochastic":{"purpose":"boundary_exact"}},
            {"id":"f1","stochastic":{"purpose":"rng_consumption_precondition"}},
            {"id":"s0","inputs":[{"shape":[2]}],"stochastic":{"purpose":"structural_boundary_exact"}},
            {"id":"s1","inputs":[{"shape":[2]}],"stochastic":{"purpose":"structural_boundary_exact"}}]}
        got=S.build_manifest(self.spec,cases,self.source,[4,6])
        self.assertEqual(got["partition"],"stochastic_formal_witness_affinity_v1")
        self.assertEqual(got["shards"][0]["case_ids"],["f0","f1","s0"])
        self.assertEqual(got["shards"][1]["case_ids"],["s1"])


if __name__ == "__main__": unittest.main()
