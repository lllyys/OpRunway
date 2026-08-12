from __future__ import annotations

import ctypes
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugin"
sys.path.insert(0, str(PLUGIN_ROOT))

from oprunway.atk import _coverage_projection, _generated_case_projection, generate_cases
from oprunway.contract import validate_spec
from oprunway.util import sha256_file


WITNESSES = Path(__file__).resolve().parent / "witnesses"
TASKDOC_NAMES = {
    "bernoulli": "aclnnBernoulli_task_doc.md",
    "remainder": "aclnnRemainderTensorTensor_task_doc.md",
    "roll": "aclnnRoll_task_doc.md",
    "gaussian_blur": "GaussianBlur_task_doc.md",
}
GAUSSIAN_CASES_ENV = "OPRUNWAY_GAUSSIAN_BLUR_CASES_ROOT"


class WitnessInputTests(unittest.TestCase):
    def test_all_four_witnesses_are_declarative_and_valid(self):
        self.assertEqual({path.name for path in WITNESSES.iterdir() if path.is_dir()}, set(TASKDOC_NAMES))
        for name in TASKDOC_NAMES:
            root = WITNESSES / name
            spec = validate_spec(json.loads((root / "spec.json").read_text(encoding="utf-8")))
            self.assertTrue((root / "design.yaml").is_file())
            self.assertTrue((root / "generator.py").is_file())
            for path in root.iterdir():
                if path.is_file():
                    text = path.read_text(encoding="utf-8")
                    self.assertNotIn("/Users/", text)
                    self.assertNotIn("/home/", text)

    def test_local_task_documents_match_bound_hashes_when_available(self):
        taskdocs_value = os.environ.get("OPRUNWAY_TASKDOC_ROOT")
        if not taskdocs_value:
            self.skipTest("OPRUNWAY_TASKDOC_ROOT is not set")
        taskdocs = Path(taskdocs_value)
        if not taskdocs.is_dir():
            self.fail("OPRUNWAY_TASKDOC_ROOT is not a directory")
        for name, filename in TASKDOC_NAMES.items():
            spec = json.loads((WITNESSES / name / "spec.json").read_text(encoding="utf-8"))
            self.assertEqual(sha256_file(taskdocs / filename), spec["task"]["taskdoc_sha256"])

    @unittest.skipUnless(os.environ.get(GAUSSIAN_CASES_ENV), "requires official GaussianBlur cases")
    def test_gaussian_official_case_bundle_matches_bound_hashes(self):
        root = Path(os.environ[GAUSSIAN_CASES_ENV])
        spec = json.loads((WITNESSES / "gaussian_blur" / "spec.json").read_text(encoding="utf-8"))
        bundle = spec["task"]["case_bundle"]
        self.assertEqual(bundle["case_count"], 169)
        self.assertEqual(bundle["expected_generated_case_count"], 170)
        self.assertEqual(
            {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()},
            {item["path"] for item in bundle["files"]},
        )
        for item in bundle["files"]:
            self.assertEqual(sha256_file(root / item["path"]), item["sha256"])

    @unittest.skipUnless(os.environ.get("OPRUNWAY_ATK_BIN"), "requires prepared ATK target environment")
    def test_bernoulli_reference_restores_declared_dtype_after_benchmark_promotion(self):
        plugin_path = WITNESSES / "bernoulli" / "execution_plugin.py"
        module_spec = importlib.util.spec_from_file_location("oprunway_bernoulli_witness_test", plugin_path)
        self.assertIsNotNone(module_spec)
        self.assertIsNotNone(module_spec.loader)
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        for declared, expected in (("bf16", module.torch.bfloat16), ("fp16", module.torch.float16)):
            task_result = SimpleNamespace(
                case_config=SimpleNamespace(
                    name="torch.bernoulli", outputs=None,
                    inputs=[SimpleNamespace(dtype=declared)],
                ),
                backend="cpu", name="cpu_0", device_id=0,
            )
            reference = module.BernoulliReference(task_result)
            promoted = module.InputDataset(kwargs={
                "self": module.torch.zeros((4, 4), dtype=module.torch.float32),
                "prob": 0.0, "seed": 0, "offset": 0,
            })
            self.assertEqual(reference(promoted).dtype, expected)

    @unittest.skipUnless(os.environ.get("OPRUNWAY_ATK_BIN"), "requires prepared ATK target environment")
    def test_roll_uses_bound_marker_when_atk_omits_empty_dims_argument(self):
        plugin_path = WITNESSES / "roll" / "execution_plugin.py"
        module_spec = importlib.util.spec_from_file_location("oprunway_roll_witness_test", plugin_path)
        self.assertIsNotNone(module_spec)
        self.assertIsNotNone(module_spec.loader)
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        marker = SimpleNamespace(
            name="__oprunway_empty_tuple__", type="attr_tuple", required=True,
            dtype="int", shape=None, range_values="default",
        )
        case_config = SimpleNamespace(inputs=[SimpleNamespace(), [SimpleNamespace()], [marker]])
        tensor = module.torch.arange(6).reshape(2, 3)
        prepared = module._prepared([tensor, (1,)], case_config)
        self.assertEqual(prepared[1:], ([1], []))
        prepared_null = module._prepared([tensor, (1,), (None,)], case_config)
        self.assertEqual(prepared_null[1:], ([1], []))
        with self.assertRaises(ValueError):
            module._prepared([tensor, (1,), (0,)], case_config)
        wrong = SimpleNamespace(inputs=[SimpleNamespace(), [SimpleNamespace()], [
            SimpleNamespace(
                name="different", type="attr_tuple", required=True,
                dtype="int", shape=None, range_values="default",
            )
        ]])
        with self.assertRaises(ValueError):
            module._prepared([tensor, (1,)], wrong)
        with self.assertRaises(ValueError):
            module._prepared([tensor, (1,), (None,)], wrong)
        with self.assertRaises(ValueError):
            module._prepared([tensor, (1,), ()], wrong)
        pointer = module._empty_int_array()
        try:
            self.assertTrue(pointer)
            self.assertEqual(type(pointer).__name__, "LP_AclIntArray")
        finally:
            module.nnopbase.aclrt_destroy_arg(pointer)

        class Backend:
            def __init__(self):
                self.input_indexes = []
                self.output_indexes = []
                self.output_cache = []

            def convert_input_data(self, value, index):
                self.input_indexes.append(index)
                return [value]

            def convert_output_data(self, value, index):
                self.output_indexes.append(index)
                return [value]

        backend = Backend()
        output = object()
        input_args, output_packages = module._empty_roll_arguments(
            backend, [output], tensor, [1]
        )
        empty_pointer = input_args[2]
        try:
            self.assertEqual(backend.input_indexes, [0, 1])
            self.assertEqual(backend.output_indexes, [0])
            self.assertEqual(type(empty_pointer).__name__, "LP_AclIntArray")
            self.assertIs(input_args[-1], output)
            self.assertEqual(output_packages, [output])
        finally:
            module.nnopbase.aclrt_destroy_arg(empty_pointer)

        aclnn = module.RollAclnn.__new__(module.RollAclnn)
        aclnn.backend = Backend()
        aclnn.task_result = SimpleNamespace(
            case_config=case_config,
            output_info_list=[output],
        )
        with patch.object(
            module.AclnnBaseApi, "init_by_input_data",
            side_effect=AssertionError("empty marker must bypass generic conversion"),
        ):
            routed_args, routed_outputs = aclnn.init_by_input_data(
                module.InputDataset(args=[tensor, (1,)])
            )
        routed_pointer = routed_args[2]
        try:
            self.assertEqual(aclnn.backend.input_indexes, [0, 1])
            self.assertEqual(len(routed_args), 4)
            self.assertIs(routed_args[0], tensor)
            self.assertEqual(routed_args[1], [1])
            self.assertEqual(type(routed_pointer).__name__, "LP_AclIntArray")
            self.assertIs(routed_args[3], output)
            self.assertEqual(routed_outputs, [output])
        finally:
            module.nnopbase.aclrt_destroy_arg(routed_pointer)

        aclnn.task_result = SimpleNamespace(case_config=wrong, output_info_list=[output])
        with patch.object(
            module.AclnnBaseApi, "init_by_input_data", return_value=("generic", "path")
        ) as generic:
            self.assertEqual(
                aclnn.init_by_input_data(module.InputDataset(args=[tensor, (1,), (0,)])),
                ("generic", "path"),
            )
        generic.assert_called_once()

        class FailingBackend(Backend):
            def convert_input_data(self, value, index):
                self.input_indexes.append(index)
                return [f"input-{index}"]

            def convert_output_data(self, value, index):
                self.output_cache.append(value)
                raise RuntimeError("synthetic output conversion failure")

        failing = FailingBackend()
        destroyed = []
        original_destroy = module.nnopbase.aclrt_destroy_arg
        with patch.object(module.nnopbase, "aclrt_destroy_arg", side_effect=destroyed.append):
            with self.assertRaisesRegex(RuntimeError, "synthetic output conversion failure"):
                module._empty_roll_arguments(failing, [output], tensor, [1])
        self.assertEqual(len(destroyed), 3)
        self.assertEqual(destroyed[1:], ["input-1", "input-0"])
        self.assertEqual(type(destroyed[0]).__name__, "LP_AclIntArray")
        self.assertEqual(failing.output_cache, [])
        original_destroy(destroyed[0])

    @unittest.skipUnless(os.environ.get("OPRUNWAY_ATK_BIN"), "requires prepared ATK target environment")
    def test_gaussian_stage2_binding_bypasses_atk_four_argument_cache(self):
        plugin_path = WITNESSES / "gaussian_blur" / "execution_plugin.py"
        module_spec = importlib.util.spec_from_file_location("oprunway_gaussian_witness_test", plugin_path)
        self.assertIsNotNone(module_spec)
        self.assertIsNotNone(module_spec.loader)
        module = importlib.util.module_from_spec(module_spec)
        with patch.dict(sys.modules, {"cv2": SimpleNamespace()}):
            module_spec.loader.exec_module(module)

        tensor_pointer = ctypes.POINTER(module.acl_wrapper.AclTensor)()
        int_array_pointer = ctypes.POINTER(module.acl_wrapper.AclIntArray)()
        raw_args = [
            tensor_pointer,
            int_array_pointer,
            ctypes.c_double(1.5),
            ctypes.c_double(1.5),
            ctypes.c_int64(1),
            tensor_pointer,
        ]
        wide_stream = 0x123456789ABC
        observed_streams = []
        callback_type = ctypes.CFUNCTYPE(ctypes.c_int, *module._GAUSSIAN_BLUR_STAGE2_TYPES)

        @callback_type
        def stage2(_workspace, _size, _executor, _src, _ksize, _sigma_x, _sigma_y, _border, _dst, stream):
            observed_streams.append(stream)
            return 0

        def forbidden_bind(*_args, **_kwargs):
            raise AssertionError("ATK's name-keyed binding cache must not be reused")

        library = SimpleNamespace(aclnnGaussianBlur=stage2)
        manager = SimpleNamespace(
            lib_name="fresh-vendor",
            get_lib=lambda name: library if name == "fresh-vendor" else None,
            bind_function=forbidden_bind,
        )
        with patch.object(module.acl_wrapper, "aclnn", manager):
            function = module._bind_stage2(raw_args)
            result = function(
                ctypes.c_void_p(),
                ctypes.c_uint64(0),
                ctypes.POINTER(module.OpExecutor)(),
                *raw_args,
                ctypes.c_void_p(wide_stream),
            )
            self.assertEqual(result.value, module.AclnnStatus.ACLNN_SUCCESS)
            self.assertEqual(observed_streams, [wide_stream])
            with self.assertRaisesRegex(TypeError, "exactly six"):
                module._bind_stage2(raw_args[:-1])
            with self.assertRaisesRegex(TypeError, "argument 2"):
                module._bind_stage2([*raw_args[:2], ctypes.c_float(1.5), *raw_args[3:]])

            class Backend:
                def __init__(self):
                    self.workspace = ctypes.c_void_p()
                    self.workspace_size = ctypes.c_uint64(0)
                    self.executor = ctypes.POINTER(module.OpExecutor)()
                    self.stream = ctypes.c_void_p(wide_stream)
                    self.workspace_called = False
                    self.input_args = [
                        module.AclTensorStruct(tensor_pointer, 0, None, 0),
                        int_array_pointer,
                        ctypes.c_double(1.5),
                        ctypes.c_double(1.5),
                        ctypes.c_int64(1),
                        module.AclTensorStruct(tensor_pointer, 0, None, 0),
                    ]

                def aclnn_x_get_workspace_size(self):
                    self.workspace_called = True

            aclnn = module.GaussianBlurAclnn.__new__(module.GaussianBlurAclnn)
            aclnn.backend = Backend()
            aclnn()
            self.assertTrue(aclnn.backend.workspace_called)
            self.assertEqual(observed_streams, [wide_stream, wide_stream])

        null_manager = SimpleNamespace(
            lib_name="null-vendor",
            get_lib=lambda _name: SimpleNamespace(aclnnGaussianBlur=ctypes.c_void_p()),
            bind_function=forbidden_bind,
        )
        with patch.object(module.acl_wrapper, "aclnn", null_manager):
            with self.assertRaisesRegex(AttributeError, "null aclnnGaussianBlur"):
                module._bind_stage2(raw_args)

        observed = []

        def golden(image, kx, ky, sigma_x, sigma_y):
            observed.append((image.shape, kx, ky, sigma_x, sigma_y))
            return [image + 1.0]

        module._OFFICIAL_GOLDEN = golden
        image = module.np.zeros((2, 3, 1025), dtype=module.np.float32)
        output = module._official_reference(image, 5, 3, 1.2, 0.8)
        self.assertEqual(
            observed,
            [
                ((2, 3, 512), 5, 3, 1.2, 0.8),
                ((2, 3, 512), 5, 3, 1.2, 0.8),
                ((2, 3, 1), 5, 3, 1.2, 0.8),
            ],
        )
        self.assertEqual(output.shape, image.shape)
        self.assertTrue(module.np.array_equal(output, image + 1.0))

        observed.clear()
        planar = module.np.zeros((4, 7), dtype=module.np.float32)
        self.assertTrue(module.np.array_equal(
            module._official_reference(planar, 3, 3, 0.0, 0.0),
            planar + 1.0,
        ))
        self.assertEqual(observed, [((4, 7), 3, 3, 0.0, 0.0)])

    @unittest.skipUnless(os.environ.get("OPRUNWAY_ATK_BIN"), "requires prepared ATK target environment")
    def test_atk_generates_complete_cases_for_all_witnesses(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            for name in TASKDOC_NAMES:
                root = WITNESSES / name
                spec = json.loads((root / "spec.json").read_text(encoding="utf-8"))
                receipt = generate_cases(
                    spec=spec,
                    atk_bin=os.environ["OPRUNWAY_ATK_BIN"],
                    design_path=root / "design.yaml",
                    generator_path=root / "generator.py",
                    task_cases_root=(
                        os.environ.get(GAUSSIAN_CASES_ENV) if name == "gaussian_blur" else None
                    ),
                    work_dir=out / f"{name}-work",
                    out_path=out / f"{name}.json",
                )
                self.assertEqual(receipt["status"], "VERIFIED")
                self.assertEqual(
                    len(receipt["cases"]["required_case_ids"]),
                    len(spec["task"]["required_cases"]),
                )
                cases = json.loads(Path(receipt["cases"]["path"]).read_text(encoding="utf-8"))
                if name == "gaussian_blur":
                    bundle = spec["task"]["case_bundle"]
                    self.assertEqual(len(cases), 170)
                    by_name = {case["name"]: case for case in cases}
                    self.assertEqual(len(by_name), 170)
                    self.assertEqual(
                        set(by_name),
                        {f"Test_{index:03d}" for index in range(1, 170)} | {"Taskdoc_S1"},
                    )
                    self.assertEqual(by_name["Taskdoc_S1"]["inputs"][0]["shape"], [1024, 1024])
                    self.assertEqual(
                        _generated_case_projection(cases),
                        bundle["generated_projection_sha256"],
                    )
                    self.assertTrue(all(
                        isinstance(case["inputs"][1], list) and len(case["inputs"][1]) == 2
                        for case in cases
                    ))
                if name == "roll":
                    self.assertTrue(all(
                        isinstance(case["inputs"][1], list) and isinstance(case["inputs"][2], list)
                        for case in cases
                    ))
                    projections = [_coverage_projection(case["inputs"][2]) for case in cases]
                    self.assertTrue(all(projection is not None for projection in projections))
                    logical_dims = [projection[2] for projection in projections]
                    self.assertEqual({len(values) for values in logical_dims}, {0, 1, 2})
                    sentinel_inputs = [
                        case["inputs"][2] for case, values in zip(cases, logical_dims) if values == []
                    ]
                    self.assertTrue(all(len(values) == 1 for values in sentinel_inputs))
                    sentinels = [values[0] for values in sentinel_inputs]
                    self.assertEqual(len(sentinels), 2)
                    self.assertTrue(all(
                        marker["name"] == "__oprunway_empty_tuple__"
                        and marker["type"] == "attr_tuple"
                        and marker["required"] is True
                        and marker["dtype"] == "int"
                        and marker["shape"] is None
                        and marker["range_values"] == "default"
                        for marker in sentinels
                    ))


if __name__ == "__main__":
    unittest.main()
