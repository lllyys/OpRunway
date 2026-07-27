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


if __name__ == "__main__":
    unittest.main()
