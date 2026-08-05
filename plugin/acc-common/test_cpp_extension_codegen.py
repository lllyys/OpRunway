import json
import tempfile
import unittest
from pathlib import Path

import cpp_extension_codegen as C


def _spec():
    return {
        "op": "Witness",
        "runner_form": "cpp_extension",
        "params": [
            {"name": "self", "io": "in", "dtype": ["float32"]},
            {"name": "dim", "io": "attr", "dtype": ["int64"], "default": 0},
            {"name": "keepDim", "io": "attr", "dtype": ["bool"], "default": False},
            {"name": "valuesOut", "io": "out", "dtype": ["<from_input>"]},
            {"name": "indicesOut", "io": "out", "dtype": ["int32"]},
        ],
        "call_variants": [
            {"symbol": "Witness", "active_attrs": [],
             "active_outputs": ["valuesOut"]},
            {"symbol": "WitnessDim", "active_attrs": ["dim", "keepDim"],
             "active_outputs": ["valuesOut", "indicesOut"]},
        ],
    }


class CppExtensionCodegenTest(unittest.TestCase):
    def test_generates_official_extension_shape(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = C.generate(_spec(), td)
            cpp = (Path(td) / "csrc" / "oprunway_extension.cpp").read_text()
            setup = (Path(td) / "setup.py").read_text()
            disk = json.loads((Path(td) / "extension_manifest.json").read_text())
        self.assertEqual(manifest, disk)
        self.assertIn('#include "npu_cpp_extension.h"', cpp)
        self.assertIn("EXEC_NPU_CMD_EXT(aclnnWitness,", cpp)
        self.assertIn("EXEC_NPU_CMD_EXT(aclnnWitnessDim,", cpp)
        self.assertIn("TORCH_LIBRARY(oprunway_", cpp)
        self.assertIn("PrivateUse1", cpp)
        self.assertIn("Tensor? indicesOut", cpp)
        self.assertIn(
            "invoke_v0(const at::Tensor& self, const at::Tensor& valuesOut",
            cpp)
        self.assertNotIn(
            "invoke_v0(const at::Tensor& self, int64_t dim", cpp)
        self.assertIn("NpuExtension(", setup)
        self.assertIn("BuildExtension.with_options(use_ninja=False)", setup)
        self.assertNotIn("pytorch_npu_helper", cpp + setup)
        self.assertEqual(len(manifest["files"]["cpp"]["sha256"]), 64)
        self.assertEqual(len(manifest["files"]["setup"]["sha256"]), 64)

    def test_is_operator_neutral(self):
        a = _spec()
        b = _spec()
        b["op"] = "Other"
        b["call_variants"][0]["symbol"] = "Other"
        b["call_variants"][1]["symbol"] = "OtherDim"
        with tempfile.TemporaryDirectory() as ta, tempfile.TemporaryDirectory() as tb:
            ma = C.generate(a, ta)
            mb = C.generate(b, tb)
            ca = (Path(ta) / "csrc" / "oprunway_extension.cpp").read_text()
            cb = (Path(tb) / "csrc" / "oprunway_extension.cpp").read_text()
        self.assertNotEqual(ma["namespace"], mb["namespace"])
        self.assertIn("aclnnOther", cb)
        self.assertNotIn("if (op", ca + cb)

    def test_rejects_wrong_form_and_prefixed_symbol(self):
        bad = _spec()
        bad["runner_form"] = "aclnn_py"
        with self.assertRaises(C.CppExtensionCodegenError):
            C.generate(bad, tempfile.mkdtemp())
        bad = _spec()
        bad["call_variants"][0]["symbol"] = "aclnnWitness"
        with self.assertRaises(C.CppExtensionCodegenError):
            C.generate(bad, tempfile.mkdtemp())

    def test_rejects_unknown_attr_type(self):
        bad = _spec()
        bad["params"][1]["dtype"] = ["str"]
        with self.assertRaises(C.CppExtensionCodegenError):
            C.generate(bad, tempfile.mkdtemp())

    def test_rejects_missing_active_attrs(self):
        bad = _spec()
        del bad["call_variants"][0]["active_attrs"]
        with self.assertRaises(C.CppExtensionCodegenError):
            C.generate(bad, tempfile.mkdtemp())

    def test_default_stage2_keeps_macro_and_records_degradation(self):
        """两处都没声明 stage2 形态 → 走历史宏，但 `stage2_form_unverified` 必须挂账。"""
        with tempfile.TemporaryDirectory() as td:
            manifest = C.generate(_spec(), td)
            cpp = (Path(td) / "csrc" / "oprunway_extension.cpp").read_text()
        self.assertEqual(manifest["degradations"], [C.DEGRADATION_STAGE2_UNVERIFIED])
        for variant in manifest["variants"]:
            self.assertEqual(variant["stage2_form"], C.STAGE2_STANDARD)
            self.assertEqual(variant["stage2_form_source"], C.STAGE2_SOURCE_DEFAULT)
            self.assertEqual(variant["dispatch"], C.DISPATCH_MACRO)
            self.assertEqual(variant["stage2_call_arity"], 4)
        self.assertIn("EXEC_NPU_CMD_EXT(aclnnWitness,", cpp)
        self.assertNotIn("ConvertToOpApiFunc", cpp)


def _array_attr_spec():
    """数组属性 + extended stage2 的通用见证（结构见证，不是算子特判）。"""
    return {
        "op": "ArrayWitness",
        "runner_form": "cpp_extension",
        "params": [
            {"name": "src", "io": "in", "dtype": ["float32"]},
            {"name": "ksize", "io": "attr", "dtype": ["int64"], "default": [5, 5]},
            {"name": "sigmaX", "io": "attr", "dtype": ["float64"], "default": 0.0},
            {"name": "dst", "io": "out", "dtype": ["<from_input>"]},
        ],
        "call_variants": [
            {"symbol": "ArrayWitness", "active_attrs": ["ksize", "sigmaX"],
             "active_outputs": ["dst"]},
        ],
    }


class CppExtensionArrayAttrTest(unittest.TestCase):
    def test_list_int_default_derives_int_array_capability(self):
        """数组性由**值结构**派生：spec 仍写 dtype:["int64"]，不新增 `int64[]` 这类词。"""
        with tempfile.TemporaryDirectory() as td:
            C.generate(_array_attr_spec(), td)
            cpp = (Path(td) / "csrc" / "oprunway_extension.cpp").read_text()
        self.assertIn("at::IntArrayRef ksize", cpp)
        self.assertIn("int[] ksize", cpp)
        self.assertIn("double sigmaX", cpp)      # 标量分支不受影响
        self.assertIn("float sigmaX", cpp)

    def test_malformed_list_default_fails_closed(self):
        for bad_default in ([], [1, True], ["5", "5"], [[5]]):
            spec = _array_attr_spec()
            spec["params"][1]["default"] = bad_default
            with self.subTest(default=bad_default):
                with self.assertRaises(C.CppExtensionCodegenError):
                    C.generate(spec, tempfile.mkdtemp())


class CppExtensionStage2DispatchTest(unittest.TestCase):
    def _preflight(self, spec, dispatch_form, raw=None):
        return {
            "status": "READY_WAIT_NPU_TRUST_GATE",
            "bindings": {"spec_sha256": C._canonical_digest(spec)},
            "signatures": [{
                "symbol": spec["call_variants"][0]["symbol"],
                "stage2_dispatch_form": dispatch_form,
                "stage2_form": raw if raw is not None else dispatch_form,
            }],
        }

    def test_extended_emits_hand_written_two_stage_not_macro(self):
        spec = _array_attr_spec()
        with tempfile.TemporaryDirectory() as td:
            manifest = C.generate(spec, td, self._preflight(spec, C.STAGE2_EXTENDED))
            cpp = (Path(td) / "csrc" / "oprunway_extension.cpp").read_text()
        variant = manifest["variants"][0]
        self.assertEqual(variant["stage2_form"], C.STAGE2_EXTENDED)
        self.assertEqual(variant["stage2_form_source"], C.STAGE2_SOURCE_PREFLIGHT)
        self.assertEqual(variant["dispatch"], C.DISPATCH_EXTENDED)
        # 框架三参 + 该变体的 4 个 stage1 实参 + stream
        self.assertEqual(variant["stage2_call_arity"], 8)
        self.assertEqual(manifest["degradations"], [])
        self.assertNotIn("EXEC_NPU_CMD_EXT(", cpp)
        # 执行段实参必须**复用**已转换的 stage1 实参，且个数与 arity 对得上
        self.assertEqual(cpp.count("std::get<"), 4)
        # 只许出现**实装 libtorch_npu.so 真导出**的 helper（官方 EXEC_NPU_CMD_V1_EXT 同一套）
        for helper in ("GetApiFunc(", "InitExecCommonCtx()", "GetAclStream()",
                       "SetExecConfig()", "ConvertTypes(", "ConvertToOpApiFunc(",
                       "InitExecSubTheadCtx(", "ReleaseConvertTypes(",
                       "UnInitExecCommonCtx()", "RunAclCall("):
            self.assertIn(helper, cpp)
        self.assertEqual(manifest["official_pattern"]["extended_stage2_helpers"],
                         ["GetApiFunc", "InitExecCommonCtx", "GetAclStream", "SetExecConfig",
                          "ConvertTypes", "ConvertToOpApiFunc", "call", "InitExecSubTheadCtx",
                          "ReleaseConvertTypes", "UnInitExecCommonCtx", "RunAclCall"])
        # workspace 自申请：官方 unsafe_empty_workspace 未导出，扩展里链不到
        self.assertIn("at::Tensor workspace_tensor;", cpp)
        self.assertIn("workspace_tensor.data_ptr()", cpp)
        self.assertIn("src.options().dtype(at::kByte)", cpp)   # buffer 落在首个输入的设备上
        self.assertIn("workspace_tensor, op_api_func", cpp)    # 按值捕获续命到执行结束
        # 真机实测不存在的符号一个都不许再冒出来（2026-08-05 编译报错的四个）
        for phantom in ("CaptureDeterministicSnapshot", "ApplyDeterministicSnapshot",
                        "GetWorkSpaceAddr", "ReleaseExecCommonCtx"):
            self.assertNotIn(phantom, cpp)
            self.assertNotIn(phantom, json.dumps(manifest, ensure_ascii=False))

    def test_preflight_must_bind_the_same_spec(self):
        spec = _array_attr_spec()
        stale = self._preflight(spec, C.STAGE2_EXTENDED)
        stale["bindings"]["spec_sha256"] = "0" * 64
        with self.assertRaisesRegex(C.CppExtensionCodegenError, "spec_sha256"):
            C.generate(spec, tempfile.mkdtemp(), stale)

    def test_non_dispatchable_stage2_fails_closed(self):
        spec = _array_attr_spec()
        for raw in ("absent", None, "weird"):
            with self.subTest(stage2_form=raw):
                with self.assertRaisesRegex(
                        C.CppExtensionCodegenError, "不可派发"):
                    C.generate(spec, tempfile.mkdtemp(),
                               self._preflight(spec, None, raw=raw))

    def test_spec_declaration_may_not_contradict_header(self):
        spec = _array_attr_spec()
        spec["call_variants"][0]["stage2_form"] = C.STAGE2_STANDARD
        with self.assertRaisesRegex(C.CppExtensionCodegenError, "以 header 为准"):
            C.generate(spec, tempfile.mkdtemp(),
                       self._preflight(spec, C.STAGE2_EXTENDED))

    def test_unknown_spec_declared_form_fails_closed(self):
        spec = _array_attr_spec()
        spec["call_variants"][0]["stage2_form"] = "four_args"
        with self.assertRaisesRegex(C.CppExtensionCodegenError, "非受控值"):
            C.generate(spec, tempfile.mkdtemp())


if __name__ == "__main__":
    unittest.main()
