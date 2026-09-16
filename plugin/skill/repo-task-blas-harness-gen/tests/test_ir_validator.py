#!/usr/bin/env python3
"""ir_validator 执行测试：正例 CLEAN、七负例逐字匹配 expected、跨段不变量有牙。"""

import copy
import json
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
SKILL = Path(__file__).resolve().parents[1]
D = SKILL / "tests" / "ir-instances"
sys.path.insert(0, str(SKILL / "scripts"))

import ir_validator as iv  # noqa: E402
import package_loader as pl  # noqa: E402


def _load(name):
    return json.loads((D / name).read_text(encoding="utf-8"))


def _run_negative(facts):
    """按 §0 层序执行：pre_ir 预筛 → param_gate → ast_gate → signature_table。"""
    pl.check_contract_prescreen(facts)
    iv.validate_facts(facts)
    for i, p in enumerate(facts["params"]):
        for key in ("len", "inc", "rows", "cols", "ld"):
            if key in p:
                iv.translate_shape_expr(p[key])
    iv.check_signature(facts["golden"]["symbol"])


class TestPositives(unittest.TestCase):
    def test_ir_instances_clean(self):
        for name in ("sasum.json", "sgemm.json"):
            iv.validate_ir(_load(name))

    def test_sgemm_facts_draft_clean_through_all_gates(self):
        facts = _load("sgemm-facts-draft.json")
        facts.pop("_provenance", None)
        _run_negative(facts)  # 全层通过即不抛


class TestCompilerEquality(unittest.TestCase):
    def test_sasum_compiled_equals_instance(self):
        import contract
        import package_loader as pl2
        facts, specs, _h = pl2.load_package(
            SKILL.parent / "repo-task-blas-case-gen" / "assets" / "example" / "sasum")
        self.assertEqual(
            contract.compile_contract({"facts": facts, "column_specs": specs}),
            _load("sasum.json"))

    def test_sgemm_compiled_equals_instance(self):
        import contract
        facts = _load("sgemm-facts-draft.json"); facts.pop("_provenance", None)
        specs = _load("sgemm-column-specs-draft.json")
        self.assertEqual(
            contract.compile_contract({"facts": facts, "column_specs": specs}),
            _load("sgemm.json"))


class TestTightenedGates(unittest.TestCase):
    def test_bool_as_ast_int_rejected(self):
        ir = _load("sasum.json")
        ir["buffers"][1]["span_ast"] = ["int", True]
        with self.assertRaises(iv.ContractReject):
            iv.validate_ir(ir)

    def test_eq_str_without_enum_ref_rejected(self):
        ir = _load("sgemm.json")
        bad = ["eq", ["str", "N"], ["str", "N"]]
        ir["params"][7]["shape"]["rows_ast"][1] = bad
        with self.assertRaises(iv.ContractReject):
            iv.validate_ir(ir)

    def test_duplicate_buffer_rejected(self):
        ir = _load("sasum.json")
        ir["buffers"].append(dict(ir["buffers"][0]))
        with self.assertRaises(iv.ContractReject):
            iv.validate_ir(ir)

    def test_abi_kind_in_ir_rejected(self):
        ir = _load("sasum.json")
        ir["columns"][0]["kind"] = "id"
        with self.assertRaises(iv.ContractReject):
            iv.validate_ir(ir)

    def test_double_violation_ast_gate_hits_first(self):
        """F-01 门序回归：坏 shape 表达式 + 表外 symbol 并存时，编译器必须先报
        ast_gate（param_gate→ast_gate→signature_table 首错顺序）。"""
        import contract
        facts = json.loads((D / "negative-3.json").read_text("utf-8"))
        facts["golden"]["symbol"] = "cblas_sdot"  # 叠加第二违规
        specs = [{"name": "x", "kind": "dim", "source": "n"}]
        with self.assertRaises(iv.ContractReject) as ctx:
            contract.compile_contract({"facts": facts, "column_specs": specs})
        self.assertEqual(ctx.exception.payload["gate"], "ast_gate")

    def test_missing_vector_len_rejected(self):
        facts = json.loads((D / "negative-2.json").read_text("utf-8"))
        facts["returns"] = "aclblasStatus_t"  # 还原 N2 的翻轴，专测缺 len
        del facts["params"][2]["len"]
        with self.assertRaises(iv.ContractReject) as ctx:
            iv.validate_facts(facts)
        self.assertEqual(ctx.exception.payload["field_path"], "params[2].len")


class TestNegatives(unittest.TestCase):
    # N1 走 pre_ir（loader 预筛），其异常不携五元组载荷：断言停机码与 observed 子串，
    # 非逐字五元组匹配；N2-N7 逐字匹配。
    def test_seven_negatives_exact_payload(self):
        expected = _load("expected.json")
        self.assertEqual(len(expected), 7)
        for i in range(1, 8):
            facts = _load(f"negative-{i}.json")
            exp = expected[f"negative-{i}"]
            if exp["gate"] == "pre_ir":
                with self.assertRaises(pl.LoaderReject) as ctx:
                    _run_negative(facts)
                self.assertEqual(ctx.exception.code, exp["code"], i)
                self.assertIn(str(exp["observed"]), ctx.exception.reason)
            else:
                with self.assertRaises(iv.ContractReject) as ctx:
                    _run_negative(facts)
                self.assertEqual(ctx.exception.payload, exp, f"negative-{i}")


class TestCrossSectionInvariants(unittest.TestCase):
    def setUp(self):
        self.ir = _load("sasum.json")

    def test_buffer_dtype_mismatch_rejected(self):
        self.ir["buffers"][0]["dtype"] = "float16"
        with self.assertRaises(iv.ContractReject) as ctx:
            iv.validate_ir(self.ir)
        self.assertIn("buffers[0].dtype", ctx.exception.payload["field_path"])

    def test_missing_readback_rejected(self):
        self.ir["movement"] = [m for m in self.ir["movement"]
                               if m["stage"] != "readback"]
        with self.assertRaises(iv.ContractReject) as ctx:
            iv.validate_ir(self.ir)
        self.assertIn("movement", ctx.exception.payload["field_path"])

    def test_smoke_args_length_rejected(self):
        self.ir["smoke"] = copy.deepcopy(self.ir["smoke"])
        self.ir["smoke"]["null_handle"]["args"].append(None)
        with self.assertRaises(iv.ContractReject):
            iv.validate_ir(self.ir)

    def test_checks_divergence_from_table_rejected(self):
        self.ir["status_plan"]["checks"] = self.ir["status_plan"]["checks"][:-1]
        with self.assertRaises(iv.ContractReject) as ctx:
            iv.validate_ir(self.ir)
        self.assertEqual(ctx.exception.payload["gate"], "signature_table")


class TestFailClosedTypes(unittest.TestCase):
    """F-04：畸形 IR 一律 ContractReject（不放 Type/Value/KeyError 逃逸）。"""

    def _reject(self, mutate):
        ir = _load("sger.json")
        mutate(ir)
        with self.assertRaises(iv.ContractReject):
            iv.validate_ir(ir)

    def test_params_none(self):
        self._reject(lambda ir: ir.__setitem__("params", None))

    def test_columns_entry_none(self):
        self._reject(lambda ir: ir["columns"].__setitem__(0, None))

    def test_buffers_entry_none(self):
        self._reject(lambda ir: ir["buffers"].__setitem__(0, None))

    def test_movement_entry_none(self):
        self._reject(lambda ir: ir["movement"].__setitem__(0, None))

    def test_golden_ret_none(self):
        self._reject(lambda ir: ir["golden"].__setitem__("ret", None))

    def test_golden_args_none(self):
        self._reject(lambda ir: ir["golden"].__setitem__("args", None))

    def test_verify_not_pair(self):
        self._reject(lambda ir: ir.__setitem__("verify", [["full"]]))

    def test_status_plan_extra_key(self):
        self._reject(lambda ir: ir["status_plan"].__setitem__("evil", 1))

    def test_buffers_extra_key(self):
        self._reject(lambda ir: ir["buffers"][0].__setitem__("evil", 1))

    def test_movement_extra_key(self):
        self._reject(lambda ir: ir["movement"][0].__setitem__("evil", 1))

    def test_smoke_extra_key(self):
        def m(ir):
            if ir["smoke"] is None:
                ir["smoke"] = {"null_handle": {"expect": "ACLBLAS_STATUS_HANDLE_IS_NULLPTR",
                                               "args": [None] * len(ir["params"])}}
            ir["smoke"]["evil"] = 1
        self._reject(m)


if __name__ == "__main__":
    unittest.main()
