"""codegen.py 回归测试。承 CLAUDE.md #0：钉住「结构驱动、零 op 名分支、fail-closed」。"""
import copy
import json
import os
import unittest

import codegen

HERE = os.path.dirname(os.path.abspath(__file__))
FOREACH = os.path.join(HERE, "examples", "foreach_add_list.ir.json")


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class TestCodegen(unittest.TestCase):
    def setUp(self):
        self.ir = _load(FOREACH)

    def test_foreach_emits_expected_binding(self):
        code = codegen.generate(self.ir)
        # tensor_list 打包
        self.assertIn("aclCreateTensorList(x1_elems.data()", code)
        self.assertIn("aclCreateTensorList(y_elems.data()", code)
        # 列表长度不再 emit 未定义符号 N
        self.assertNotIn("/*n=*/N", code)
        # stage1 按 IR 位序 + 尾随 plumbing
        self.assertIn("aclnnForeachAddListGetWorkspaceSize(x1, x2, alpha, y, &workspaceSize, &executor)", code)
        # stage2 固定四参
        self.assertIn("aclnnForeachAddList(workspace, workspaceSize, executor, stream)", code)
        # 回读列表输出（消费 output_mapping：golden 列 0 ← aclnn 槽 y）
        self.assertIn('ReadbackToFileList(/*golden_col=*/0, /*aclnn_slot=*/"y")', code)

    def test_no_op_name_branch_in_codegen_source(self):
        # 元测试：codegen.py 源码里不得出现按算子名的分支（#0）
        with open(os.path.join(HERE, "codegen.py"), encoding="utf-8") as f:
            src = f.read()
        for banned in ['== "ForeachAddList"', "== 'ForeachAddList'", 'op == "', "op == '"]:
            self.assertNotIn(banned, src, f"codegen.py 出现按算子名分支：{banned}")

    def test_fail_closed_on_data_dependent_output(self):
        ir = copy.deepcopy(self.ir)
        ir["outputs"][0]["shape_materialization"] = {
            "modes": [{"mode": "host_oracle", "value_dependency": "max(reduce_max(self)+1, minlength)", "extent_readback": "count_from_oracle"}]
        }
        with self.assertRaises(codegen.FailClosed):
            codegen.generate(ir)

    def test_fail_closed_out_of_domain(self):
        ir = copy.deepcopy(self.ir)
        ir["applicability"] = {"in_domain": False, "out_of_domain_axes": ["opaque_handle"], "reason": "sparse handle"}
        with self.assertRaises(codegen.FailClosed):
            codegen.generate(ir)

    def test_fail_closed_on_fail_closed_provenance(self):
        ir = copy.deepcopy(self.ir)
        # 把某参 provenance 置成 needs_source + fail_closed
        ir["parameters"][0]["provenance"] = {"source": "header", "state": "needs_source", "fail_closed": True}
        with self.assertRaises(codegen.FailClosed):
            codegen.generate(ir)

    def _witness(self, name):
        p = os.path.join(HERE, "examples", name + ".ir.json")
        if not os.path.exists(p):
            self.skipTest(f"{name} 见证 IR 未在场")
        return _load(p)

    def test_axis_inout_inplace_sigmoid(self):
        # inout：单 selfRef 槽、从自身 buffer 回读（readback_binding 真消费）
        code = codegen.generate(self._witness("inplace_sigmoid"))
        self.assertIn("aclnnInplaceSigmoidGetWorkspaceSize(selfRef, &workspaceSize, &executor)", code)
        self.assertIn('ReadbackToFile(/*golden_col=*/0, /*aclnn_slot=*/"selfRef")', code)
        self.assertIn("inout，从自身 buffer 回读", code)

    def test_axis_multi_output_reversed_argmax(self):
        # 多输出反转**真消费**：golden 列 0 ← aclnn 槽 indices、列 1 ← 槽 out（反转 {0→1,1→0}）
        code = codegen.generate(self._witness("argmax"))
        self.assertIn("aclnnMaxDimGetWorkspaceSize(self, dim, keepdim, out, indices, &workspaceSize, &executor)", code)
        self.assertIn('ReadbackToFile(/*golden_col=*/0, /*aclnn_slot=*/"indices")', code)  # golden0 ← indices（反转）
        self.assertIn('ReadbackToFile(/*golden_col=*/1, /*aclnn_slot=*/"out")', code)       # golden1 ← out（反转）

    def test_axis_data_dependent_bincount_fail_closed(self):
        # data-dependent 输出：out 尺寸算不出 → fail-closed
        with self.assertRaises(codegen.FailClosed):
            codegen.generate(self._witness("bincount"))


if __name__ == "__main__":
    unittest.main()
