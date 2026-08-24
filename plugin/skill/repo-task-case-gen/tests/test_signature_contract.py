"""check_signature_contract.py 的回归测试。

真机事故（median，2026-08-16）：签名抓错了出处，少了 `dim`，契约跟着少一个
入参。基线侧的 `check_baseline_binding` 是子集判定，少写不报；适配器门禁只
看该不该写适配器。于是 S2 三道门禁全过、冻结、构建，直到 S3 按声明顺序绑
pyaclnn 才炸，白跑一整轮构建。

这里锁住的就是那个缺口：aclnn 侧的入参集合与顺序必须机械核对，不靠 agent
声称「我照着签名写的」。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _paths import SCRIPTS

SCRIPT = SCRIPTS / "check_signature_contract.py"
sys.path.insert(0, str(SCRIPT.parent))

import check_signature_contract as gate  # noqa: E402


def alignment(names, adapter_required=False, **extra):
    rows = [{"c_name": f"c_{name}", "yaml_key": name} for name in names]
    payload = {"aclnn": {"inputs": rows},
               "aclnn_adapter": {"required": adapter_required, "reasons": []},
               "signature_source": "/proj/op_host/op_api/aclnn_median.h"}
    payload.update(extra)
    return payload


def parameters(*names):
    return {name: {"element_kind": "tensor", "runtime_container": "single"}
            for name in names}


class MissingParameterTest(unittest.TestCase):
    """median 的原始形态：签名少一个入参，契约跟着少，全链路无人说话。"""

    def test_missing_input_is_rejected(self):
        report, problems = gate.judge(parameters("input", "keepdim"),
                                      alignment(["input", "dim", "keepdim"]))
        self.assertEqual(["dim"], report["missing"])
        self.assertTrue(problems)
        self.assertIn("dim", problems[0])

    def test_complete_contract_passes(self):
        report, problems = gate.judge(parameters("input", "dim", "keepdim"),
                                      alignment(["input", "dim", "keepdim"]))
        self.assertEqual([], problems)
        self.assertEqual([], report["missing"])
        self.assertTrue(report["order_ok"])


class OrderTest(unittest.TestCase):
    """pyaclnn 按声明顺序逐个 convert，顺序错了是静默错绑——跑得出数，比对必错。"""

    def test_swapped_order_is_rejected(self):
        _, problems = gate.judge(parameters("input", "keepdim", "dim"),
                                 alignment(["input", "dim", "keepdim"]))
        self.assertTrue(problems)
        self.assertIn("顺序", problems[0])

    def test_extra_param_does_not_shift_the_order_check(self):
        # 基线独有的参数由 aclnn 侧适配器丢掉，不占位置，剩下的顺序仍然对。
        _, problems = gate.judge(parameters("input", "out_dtype", "dim", "keepdim"),
                                 alignment(["input", "dim", "keepdim"],
                                           adapter_required=True))
        self.assertEqual([], problems)


class ExtraParameterTest(unittest.TestCase):
    def test_extra_without_adapter_is_rejected(self):
        _, problems = gate.judge(parameters("input", "dim", "keepdim", "stray"),
                                 alignment(["input", "dim", "keepdim"]))
        self.assertTrue(problems)
        self.assertIn("stray", problems[0])

    def test_extra_with_adapter_is_accepted(self):
        report, problems = gate.judge(parameters("input", "dim", "keepdim", "stray"),
                                      alignment(["input", "dim", "keepdim"],
                                                adapter_required=True))
        self.assertEqual([], problems)
        self.assertEqual(["stray"], report["extra"])


class ChannelAndOmittedTest(unittest.TestCase):
    """不进 aclnn 调用的两类参数不参与对齐，否则会把合法契约判成多参数。"""

    def test_method_inputs_channel_is_out_of_scope(self):
        params = parameters("input", "dim", "keepdim")
        params["method_inputs.receiver"] = {"element_kind": "tensor",
                                            "runtime_container": "single"}
        report, problems = gate.judge(params, alignment(["input", "dim", "keepdim"]))
        self.assertEqual([], problems)
        self.assertNotIn("receiver", report["contract_inputs"])

    def test_channel_field_is_honoured_like_the_qualified_key(self):
        params = parameters("input", "dim", "keepdim")
        params["receiver"] = {"element_kind": "tensor", "runtime_container": "single",
                              "channel": "tensor_input"}
        _, problems = gate.judge(params, alignment(["input", "dim", "keepdim"]))
        self.assertEqual([], problems)

    def test_omitted_placeholder_is_out_of_scope(self):
        params = parameters("input", "dim", "keepdim")
        params["placeholder"] = {"element_kind": "attr", "runtime_container": "single",
                                 "omitted": True}
        _, problems = gate.judge(params, alignment(["input", "dim", "keepdim"]))
        self.assertEqual([], problems)


class UndecidableTest(unittest.TestCase):
    """「判不了」必须与「通过」分开——位置配对不可信时不能放行。"""

    def test_missing_yaml_key_is_not_a_pass(self):
        broken = alignment(["input", "dim"])
        broken["aclnn"]["inputs"][1].pop("yaml_key")
        with self.assertRaises(gate.ContractMismatch) as ctx:
            gate.judge(parameters("input", "dim"), broken)
        self.assertIn("yaml_key", str(ctx.exception))

    def test_missing_aclnn_block_is_not_a_pass(self):
        with self.assertRaises(gate.ContractMismatch):
            gate.judge(parameters("input"), {"aclnn": {}})


class CliTest(unittest.TestCase):
    def _run(self, params, align):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            decl = tmp / "decl.json"
            decl.write_text(json.dumps({"parameters": params}), encoding="utf-8")
            align_path = tmp / "signature_alignment.json"
            align_path.write_text(json.dumps(align), encoding="utf-8")
            out = tmp / "signature_contract.json"
            done = subprocess.run(
                [sys.executable, str(SCRIPT), "-d", str(decl),
                 "-a", str(align_path), "-o", str(out)],
                capture_output=True, text=True, timeout=60)
            report = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
            return done, report

    def test_pass_writes_report_and_exits_zero(self):
        done, report = self._run(parameters("input", "dim", "keepdim"),
                                 alignment(["input", "dim", "keepdim"]))
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("pass", report["verdict"])
        self.assertEqual("/proj/op_host/op_api/aclnn_median.h", report["signature_source"])

    def test_missing_parameter_exits_two(self):
        done, report = self._run(parameters("input", "keepdim"),
                                 alignment(["input", "dim", "keepdim"]))
        self.assertEqual(2, done.returncode)
        self.assertEqual("fail", report["verdict"])
        self.assertIn("dim", done.stderr)

    def test_unreadable_input_exits_three(self):
        done = subprocess.run(
            [sys.executable, str(SCRIPT), "-d", "/tmp/no-such-decl.json",
             "-a", "/tmp/no-such-align.json", "-o", "/tmp/out.json"],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(3, done.returncode)


if __name__ == "__main__":
    unittest.main()
