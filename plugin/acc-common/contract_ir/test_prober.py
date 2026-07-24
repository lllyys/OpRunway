"""prober.py 回归测试。承 #0：零 op 名分支；prober 只抠机械签名、语义 fail-closed。

⚠ 需真仓 repos/ops-nn/foreach/foreach_add_list 在场（gitignore、本机有）。缺则 skip。"""
import json
import os
import unittest

import codegen
import prober

HERE = os.path.dirname(os.path.abspath(__file__))
FOREACH_DIR = os.path.abspath(os.path.join(HERE, "..", "..", "..", "repos", "ops-nn", "foreach", "foreach_add_list"))


@unittest.skipUnless(os.path.isdir(FOREACH_DIR), "repos/ops-nn 未在场")
class TestProber(unittest.TestCase):
    def setUp(self):
        self.ir = prober.probe(FOREACH_DIR)

    def test_signature_extracted(self):
        self.assertEqual(self.ir["aclnn_entry"]["exec_symbol"], "aclnnForeachAddList")
        self.assertEqual(self.ir["aclnn_entry"]["getworkspace_symbol"], "aclnnForeachAddListGetWorkspaceSize")
        self.assertEqual(self.ir["abi_signature"]["stage1"]["ordered_param_ids"], ["x1", "x2", "alpha", "out"])
        self.assertEqual(self.ir["abi_signature"]["stage1"]["trailing"], ["workspaceSize_out", "executor_out"])
        kinds = {p["logical_value_id"]: p["kind"] for p in self.ir["parameters"]}
        self.assertEqual(kinds, {"x1": "tensor_list", "x2": "tensor_list", "alpha": "tensor", "out": "tensor_list"})

    def test_target_from_addconfig(self):
        socs = self.ir["target_hardware"]["opdef_addconfig"]
        self.assertIn("ascend910b", socs)  # a3 在声明集内
        # 权威是任务书的告警必须在
        self.assertIn("可能选错", json.dumps(self.ir["target_hardware"], ensure_ascii=False))

    def test_schema_valid(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema 未装")
        with open(os.path.join(HERE, "contract_ir.schema.v1.json"), encoding="utf-8") as f:
            jsonschema.Draft202012Validator(json.load(f)).validate(self.ir)

    def test_semantic_fields_fail_closed(self):
        # prober 骨架的语义字段是 needs_source → codegen 必须 fail-closed（拒绝硬凑）
        with self.assertRaises(codegen.FailClosed):
            codegen.generate(self.ir)

    def test_no_op_name_branch_in_prober_source(self):
        with open(os.path.join(HERE, "prober.py"), encoding="utf-8") as f:
            src = f.read()
        for banned in ['== "ForeachAddList"', "== 'ForeachAddList'", 'op == "', "op == '"]:
            self.assertNotIn(banned, src)


if __name__ == "__main__":
    unittest.main()
