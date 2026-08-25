"""不接触 NPU，直接执行 c_api 样例的表校验与 ctypes 装参。"""

import ctypes
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
EXECUTOR = SKILL_ROOT / "assets" / "example" / "c_api_executor.py"


class FakeInputDataset:
    def __init__(self, args=(), kwargs=None):
        self.args = args
        self.kwargs = kwargs or {}


class FakeBaseApi:
    def __init__(self, task_result):
        self.device = getattr(task_result, "device", "npu")
        self.device_id = getattr(task_result, "device_id", 0)


class FakeTensor:
    def __init__(self, pointer, children=()):
        self._pointer = pointer
        self._children = list(children)
        self.shape = (len(self._children),) if self._children else (2, 2)

    def data_ptr(self):
        return self._pointer

    def __getitem__(self, index):
        return self._children[index]


def valid_table():
    return {
        "schema_version": 1,
        "symbol": "demoRun",
        "exported_name": "demoRun",
        "mangled": False,
        "sequence": [
            {"step": "context", "shape": "struct_handle", "type": "DemoHandle_t",
             "struct": {"name": "DemoHandle", "fields": [
                 {"name": "stream", "ctype": "c_void_p"}]}},
            {"step": "execute", "call": "demoRun", "timed": True,
             "status_ok": 0, "args": [
                 {"position": 0, "name": "handle", "c_type": "DemoHandle_t",
                  "class": "context", "ctype": "void_p", "rule": "测试"},
                 {"position": 1, "name": "out", "c_type": "float*",
                  "class": "device_ptr", "ctype": "void_p", "rule": "测试"},
             ]},
        ],
        "output": {"in_place": "out"},
        "layout": {"order": "row_major", "confirmed_by": "任务书 §4"},
    }


def load_executor():
    torch = types.ModuleType("torch")
    torch.Tensor = type("Tensor", (), {})
    torch.npu = types.SimpleNamespace(
        current_stream=lambda: types.SimpleNamespace(npu_stream=987),
        set_device=lambda _device: None,
        synchronize=lambda: None,
    )
    modules = {
        "torch": torch,
        "atk": types.ModuleType("atk"),
        "atk.configs": types.ModuleType("atk.configs"),
        "atk.configs.dataset_config": types.ModuleType("atk.configs.dataset_config"),
        "atk.tasks": types.ModuleType("atk.tasks"),
        "atk.tasks.api_execute": types.ModuleType("atk.tasks.api_execute"),
        "atk.tasks.api_execute.base_api": types.ModuleType(
            "atk.tasks.api_execute.base_api"),
    }
    modules["atk.configs.dataset_config"].InputDataset = FakeInputDataset
    modules["atk.tasks.api_execute"].register = lambda _name: (lambda cls: cls)
    modules["atk.tasks.api_execute.base_api"].BaseApi = FakeBaseApi
    with mock.patch.dict(sys.modules, modules), mock.patch.object(
            sys, "dont_write_bytecode", True):
        spec = importlib.util.spec_from_file_location("c_api_executor_runtime_test", EXECUTOR)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class TableValidationTest(unittest.TestCase):
    def setUp(self):
        self.module = load_executor()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "table.json"

    def _load(self, table):
        self.path.write_text(json.dumps(table), encoding="utf-8")
        return self.module._load_table(self.path)

    def test_bad_schema_version_is_rejected(self):
        table = valid_table()
        table["schema_version"] = 2
        with self.assertRaisesRegex(RuntimeError, "schema_version"):
            self._load(table)

    def test_unknown_argument_class_is_rejected(self):
        table = valid_table()
        table["sequence"][1]["args"][1]["class"] = "descriptor"
        with self.assertRaisesRegex(RuntimeError, "未开放 class"):
            self._load(table)

    def test_null_layout_is_rejected(self):
        table = valid_table()
        table["layout"] = {"order": None, "confirmed_by": None}
        with self.assertRaisesRegex(RuntimeError, "layout.order"):
            self._load(table)


class RuntimeAssemblyTest(unittest.TestCase):
    def setUp(self):
        self.module = load_executor()
        self.instance = object.__new__(self.module.ExampleCApi)

    def test_struct_context_is_constructed_from_table_fields(self):
        self.instance._steps = {"context": valid_table()["sequence"][0]}
        pointer, keepalive, destroy = self.instance._context()
        self.assertIsInstance(pointer, ctypes.c_void_p)
        self.assertEqual(987, keepalive.stream)
        self.assertIsNone(destroy)

    def test_strsm_shaped_arguments_assemble_closed_ctypes(self):
        specs = [
            {"name": "handle", "class": "context"},
            {"name": "side", "class": "enum"},
            {"name": "m", "class": "dim", "ctype": "int64"},
            {"name": "alpha", "class": "host_scalar", "pointee_ctype": "float32"},
            {"name": "A", "class": "device_ptr"},
            {"name": "B", "class": "device_ptr_array"},
        ]
        values = {
            "side": 0, "m": 4, "alpha": 1.5,
            "A": FakeTensor(1001),
            "B": FakeTensor(0, [FakeTensor(2001), FakeTensor(2002)]),
        }
        self.instance._steps = {"execute": {"args": specs}}
        argtypes, arguments, keepalive = self.instance._call_args(
            values, ctypes.c_void_p(77))
        self.assertEqual(ctypes.c_void_p, argtypes[0])
        self.assertEqual(ctypes.c_int32, argtypes[1])
        self.assertEqual(ctypes.c_int64, argtypes[2])
        self.assertEqual(ctypes.POINTER(ctypes.c_float), argtypes[3])
        self.assertEqual(ctypes.c_void_p, argtypes[4])
        self.assertEqual(ctypes.POINTER(ctypes.c_void_p), argtypes[5])
        self.assertEqual(2, len(keepalive))
        self.assertEqual(1001, arguments[4].value)

    def test_mangled_name_requires_and_uses_the_handoff_variable(self):
        class Function:
            restype = None

        function = Function()
        library = types.SimpleNamespace()
        setattr(library, "_Z7demoRunPv", function)
        self.instance._library_path = "/tmp/libdemo.so"
        self.instance._table = {
            "symbol": "demoRun", "mangled": True, "exported_name": None}
        self.instance._library = None
        self.instance._function = None
        with mock.patch.object(self.module.ctypes, "CDLL", return_value=library):
            with mock.patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "check_c_api_binding.py"):
                    self.instance._load_function()
            self.instance._library = None
            self.instance._function = None
            with mock.patch.dict(
                    os.environ, {"ATK_C_API_EXPORTED_NAME": "_Z7demoRunPv"}, clear=True):
                self.assertIs(function, self.instance._load_function())


if __name__ == "__main__":
    unittest.main()
