#!/usr/bin/env python3
"""cpp_extension_driver 的纯静态 helper 测试；不 import torch、不 build。"""

import json
import math
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

import cpp_extension_driver as D
import repo_adapter as RA


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def item(self):
        return self._value


class _Mask:
    def __init__(self, values):
        self.values = list(values)

    def sum(self):
        return _ScalarResult(sum(self.values))

    def __and__(self, other):
        return _Mask(a and b for a, b in zip(self.values, other.values))


class _Tensor:
    def __init__(self, shape, value, dtype):
        self.shape = tuple(shape)
        count = 1
        for dim in shape:
            count *= dim
        self.values = [value for _ in range(count)]
        self.dtype = dtype
        self.moved_to_npu = False

    def npu(self):
        self.moved_to_npu = True
        return self

    def detach(self):
        return self

    def contiguous(self):
        return self

    def cpu(self):
        return self

    def numel(self):
        return len(self.values)

    def __eq__(self, other):
        return _Mask(value == other for value in self.values)

    @property
    def real(self):
        return _Tensor.from_values(self.shape, [value.real for value in self.values], "float32")

    @property
    def imag(self):
        return _Tensor.from_values(self.shape, [value.imag for value in self.values], "float32")

    @classmethod
    def from_values(cls, shape, values, dtype):
        obj = cls.__new__(cls)
        obj.shape = tuple(shape)
        obj.values = list(values)
        obj.dtype = dtype
        obj.moved_to_npu = False
        return obj


class _FakeOp:
    _schema = "fake::invoke_v0(Tensor out) -> Tensor[]"

    def __init__(self, write_value=None):
        self.default = self
        self.write_value = write_value

    def __call__(self, output):
        if self.write_value is not None:
            output.values = [self.write_value for _ in output.values]
        return [output]


def _fake_torch(write_value=None):
    module = types.ModuleType("torch")
    for name in D._TORCH_DTYPES.values():
        setattr(module, name, name)
    module.full_calls = []

    def full(shape, value, *, dtype, device):
        module.full_calls.append((tuple(shape), value, dtype, device))
        return _Tensor(shape, value, dtype)

    module.full = full
    module.isnan = lambda tensor: _Mask(
        math.isnan(value) for value in tensor.values)
    op = _FakeOp(write_value)
    module.ops = types.SimpleNamespace(
        load_library=lambda _path: None,
        oprunway_witness=types.SimpleNamespace(invoke_v0=op),
    )
    module.npu = types.SimpleNamespace(synchronize=lambda: None)
    return module


class CppExtensionDriverStaticTest(unittest.TestCase):
    def test_canonical_digest_is_key_order_independent(self):
        self.assertEqual(D._canonical_sha({"a": 1, "b": 2}),
                         D._canonical_sha({"b": 2, "a": 1}))

    def test_safe_path_rejects_escape_and_absolute(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(D.DriverError):
                D._safe(td, "../x")
            with self.assertRaises(D.DriverError):
                D._safe(td, "/tmp/x")

    def test_vendor_path_is_explicit_and_must_exist(self):
        old = os.environ.get("OPRUNWAY_CPP_EXTENSION_VENDOR_LIBRARY")
        try:
            os.environ.pop("OPRUNWAY_CPP_EXTENSION_VENDOR_LIBRARY", None)
            with self.assertRaisesRegex(D.DriverError, "须指向"):
                D._require_env_path("OPRUNWAY_CPP_EXTENSION_VENDOR_LIBRARY")
        finally:
            if old is not None:
                os.environ["OPRUNWAY_CPP_EXTENSION_VENDOR_LIBRARY"] = old

    def test_vendor_build_receipt_binds_full_head_and_exact_elf(self):
        with tempfile.TemporaryDirectory() as td:
            vendor = os.path.join(td, "libcust_opapi.so")
            with open(vendor, "wb") as dst:
                dst.write(b"vendor")
            receipt_path = os.path.join(td, "build-receipt.json")
            receipt = {
                "schema": "oprunway.vendor_build_receipt",
                "schema_version": 1,
                "status": "VERIFIED",
                "source": {
                    "repo": "https://example.invalid/ops.git",
                    "pr_head_sha": "a" * 40,
                },
                "build": {
                    "argv": ["bash", "build.sh", "--ops=x"],
                    "cwd": "/work/ops",
                    "returncode": 0,
                },
                "artifact": {
                    "library_path": vendor,
                    "library_sha256": D._sha_file(vendor),
                },
            }
            with open(receipt_path, "w", encoding="utf-8") as dst:
                json.dump(receipt, dst)
            with mock.patch.dict(
                    os.environ,
                    {"OPRUNWAY_CPP_EXTENSION_VENDOR_BUILD_RECEIPT":
                     receipt_path}):
                self.assertEqual(
                    D._vendor_build_provenance(vendor), receipt)

    def test_vendor_build_receipt_rejects_short_head(self):
        with tempfile.TemporaryDirectory() as td:
            vendor = os.path.join(td, "lib.so")
            with open(vendor, "wb") as dst:
                dst.write(b"vendor")
            receipt_path = os.path.join(td, "receipt.json")
            with open(receipt_path, "w", encoding="utf-8") as dst:
                json.dump({
                    "schema": "oprunway.vendor_build_receipt",
                    "schema_version": 1,
                    "status": "VERIFIED",
                    "source": {"repo": "repo", "pr_head_sha": "a" * 7},
                    "build": {
                        "argv": ["build"], "cwd": "/work", "returncode": 0},
                    "artifact": {
                        "library_path": vendor,
                        "library_sha256": D._sha_file(vendor),
                    },
                }, dst)
            with mock.patch.dict(
                    os.environ,
                    {"OPRUNWAY_CPP_EXTENSION_VENDOR_BUILD_RECEIPT":
                     receipt_path}):
                # 收据自称 `gitcode_pr` 档（schema_version=1 的旧收据无 provenance_kind，
                # 按 PR 档解释），却只绑了个 7 位短 head——40 位是这一档的硬要求，短一位
                # 都不算绑上。断言串跟住 `vendor_build_receipt._validate_source` 的措辞。
                with self.assertRaisesRegex(
                        D.DriverError, "缺完整 PR head/source repo"):
                    D._vendor_build_provenance(vendor)

    def test_runtime_cann_probe_uses_acl_api_not_environment_claim(self):
        class Fn:
            argtypes = None
            restype = None

            def __call__(self, package, out):
                self.package = package
                ptr = D.ctypes.cast(out, D.ctypes.POINTER(D._AclCannPackageVersion))
                ptr.contents.version = b"9.0.1"
                return 0

        fn = Fn()
        acl = types.SimpleNamespace(aclsysGetCANNVersion=fn)
        with tempfile.TemporaryDirectory() as td:
            elf = os.path.join(td, "libascendcl.so")
            with open(elf, "wb") as dst:
                dst.write(b"runtime-elf")
            defining = {"path": elf, "sha256": D._sha_file(elf)}
            with mock.patch.object(D.ctypes, "CDLL", return_value=acl), \
                    mock.patch.object(D, "_defining_elf", return_value=defining), \
                    mock.patch.dict(os.environ, {
                        "CANN_VERSION": "1.2.3",
                        "ASCEND_TOOLKIT_VERSION": "4.5.6",
                    }):
                observed = D.probe_runtime_cann_version()
        self.assertEqual(fn.package, 0)
        self.assertEqual(observed["status"], "measured")
        self.assertEqual(observed["raw"], "9.0.1")
        self.assertEqual(observed["normalized"], "9.0.1")
        self.assertEqual(observed["probe"]["defining_elf"], defining)

    def test_runtime_cann_probe_failure_is_structured_unknown(self):
        with mock.patch.object(D.ctypes, "CDLL", side_effect=OSError("no acl")), \
                mock.patch.dict(os.environ, {"CANN_VERSION": "99.0.0"}):
            observed = D.probe_runtime_cann_version()
        self.assertEqual(observed["status"], "unknown")
        self.assertIsNone(observed["normalized"])
        self.assertEqual(observed["probe"]["returncode_source"], "not_called")
        self.assertIn("no acl", observed["error"])

    def _write_receipt(self, td, receipt):
        path = os.path.join(td, "receipt.json")
        with open(path, "w", encoding="utf-8") as dst:
            json.dump(receipt, dst)
        return path

    def _local_snapshot_receipt(self, vendor):
        """无 `.git` 的本地快照：没有 PR head 可绑，改绑仓根 + 子目录 scope + 两个 merkle。"""
        return {
            "schema": "oprunway.vendor_build_receipt",
            "schema_version": 2,
            "status": "VERIFIED",
            "degradations": ["pr_head_unbound"],
            "source": {
                "provenance_kind": "local_snapshot",
                "repo": "repos/ops-witness-local-snapshot",
                "pr_head_sha": None,
                "snapshot_subtree_scope": "witness_op",
                "snapshot_sha256": "c" * 64,
                "snapshot_subtree_sha256": "d" * 64,
            },
            "build": {"argv": ["bash", "build.sh"], "cwd": "/w", "returncode": 0},
            "artifact": {
                "library_path": vendor,
                "library_sha256": D._sha_file(vendor),
            },
        }

    def test_local_snapshot_receipt_is_accepted_with_explicit_degradation(self):
        with tempfile.TemporaryDirectory() as td:
            vendor = os.path.join(td, "lib.so")
            with open(vendor, "wb") as dst:
                dst.write(b"vendor")
            receipt = self._local_snapshot_receipt(vendor)
            path = self._write_receipt(td, receipt)
            with mock.patch.dict(
                    os.environ,
                    {"OPRUNWAY_CPP_EXTENSION_VENDOR_BUILD_RECEIPT": path}):
                self.assertEqual(D._vendor_build_provenance(vendor), receipt)
        summary = D.vendor_build_receipt.summarize(receipt)
        self.assertIsNone(summary["pr_head_sha"])
        self.assertEqual(summary["provenance_kind"], "local_snapshot")
        self.assertEqual(summary["degradations"], ["pr_head_unbound"])

    def test_local_snapshot_may_not_fabricate_a_pr_head(self):
        with tempfile.TemporaryDirectory() as td:
            vendor = os.path.join(td, "lib.so")
            with open(vendor, "wb") as dst:
                dst.write(b"vendor")
            receipt = self._local_snapshot_receipt(vendor)
            receipt["source"]["pr_head_sha"] = "e" * 40
            path = self._write_receipt(td, receipt)
            with mock.patch.dict(
                    os.environ,
                    {"OPRUNWAY_CPP_EXTENSION_VENDOR_BUILD_RECEIPT": path}):
                with self.assertRaisesRegex(D.DriverError, "捏造 PR head"):
                    D._vendor_build_provenance(vendor)

    def test_local_snapshot_must_account_the_degradation(self):
        with tempfile.TemporaryDirectory() as td:
            vendor = os.path.join(td, "lib.so")
            with open(vendor, "wb") as dst:
                dst.write(b"vendor")
            receipt = self._local_snapshot_receipt(vendor)
            del receipt["degradations"]
            path = self._write_receipt(td, receipt)
            with mock.patch.dict(
                    os.environ,
                    {"OPRUNWAY_CPP_EXTENSION_VENDOR_BUILD_RECEIPT": path}):
                with self.assertRaisesRegex(D.DriverError, "degradations"):
                    D._vendor_build_provenance(vendor)

    def test_perf_plan_must_bind_exact_caseset_and_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            caseset = {"op": "X", "cases": []}
            receipt = {
                "artifact": {"path": "cpp_extension/x.so", "sha256": "0" * 64},
                "load": {"namespace": "oprunway_x"},
            }
            plan = {
                "caseset_sha256": "f" * 64,
                "cpp_extension_receipt_sha256": D._canonical_sha(receipt),
            }
            for name, value in (
                    ("cpp_extension_caseset.json", caseset),
                    ("cpp_extension_receipt.json", receipt),
                    ("cpp_extension_perf_plan.json", plan)):
                with open(os.path.join(td, name), "w", encoding="utf-8") as dst:
                    json.dump(value, dst)
            with self.assertRaisesRegex(D.DriverError, "绑定漂移"):
                D.run_perf_only(td, td)


class OutputWrittenGateTest(unittest.TestCase):
    """行为型夹具：删掉 `_invoke_all` 中的哨兵检查调用，no-write 用例会重新 produced，本测试必红。"""

    def _invoke(self, *, dtype="uint8", shape=None, write_value=None):
        shape = [] if shape is None else shape
        torch = _fake_torch(write_value)
        case = {
            "id": "no_write_u8_scalar",
            "inputs": [],
            "expected": {"compare_dtype": dtype, "out_shape": shape},
        }
        row = {"case_id": case["id"], "entrypoint": "invoke_v0",
               "slots": [{"role": "out", "output_idx": 0}]}
        manifest = {
            "namespace": "oprunway_witness",
            "variants": [{"entrypoint": "invoke_v0"}],
        }
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
                sys.modules,
                {"torch": torch, "torch_npu": types.ModuleType("torch_npu")}), mock.patch.object(
                    D, "_dump_output",
                    side_effect=lambda _t, _n, tensor, logical_dtype, path: (
                        logical_dtype, list(tensor.shape))):
            _torch, _schemas, summary = D._invoke_all(
                td, td, manifest, {"cases": [row]}, {"cases": [case]}, "/tmp/fake.so")
            with open(os.path.join(td, "cpp_extension_out", "out_manifest.json"),
                      encoding="utf-8") as src:
                out_manifest = json.load(src)
        return torch, summary, out_manifest

    def test_sentinel_table_covers_every_supported_dtype_and_allocation_is_known(self):
        self.assertEqual(set(D._OUTPUT_SENTINELS), set(D._TORCH_DTYPES))
        torch = _fake_torch()
        for dtype in D._TORCH_DTYPES:
            with self.subTest(dtype=dtype):
                output = D._empty_output(
                    torch, {"name": "out", "compare_dtype": dtype, "out_shape": [2]})
                self.assertTrue(output.moved_to_npu)
                self.assertEqual(torch.full_calls[-1][3], "cpu")
        self.assertEqual(D._OUTPUT_SENTINELS["int16"][0], 0x5A5A)
        self.assertEqual(D._OUTPUT_SENTINELS["uint8"][0], 0x5A)

    def test_future_dtype_without_sentinel_fails_closed(self):
        torch = _fake_torch()
        torch.future = "future"
        with mock.patch.dict(D._TORCH_DTYPES, {"future": "future"}):
            with self.assertRaisesRegex(D.DriverError, "缺输出写入哨兵"):
                D._empty_output(
                    torch, {"name": "out", "compare_dtype": "future", "out_shape": []})

    def test_uint8_scalar_no_write_is_output_not_written_mutation_guard(self):
        torch, summary, manifest = self._invoke()
        self.assertEqual(torch.full_calls[0][1], 0x5A)
        self.assertEqual(summary["produced"], 0)
        self.assertEqual(summary["failed"], 1)
        failure = manifest["failed"][0]
        self.assertEqual(failure["error_kind"], D.FAILED_NOT_WRITTEN)
        self.assertEqual(failure["output_written_check"], D.OUTPUT_CHECK_FAILED_ALL_SENTINEL)
        self.assertEqual(failure["output_written_diagnostic"]["sentinel_hits"], 1)
        self.assertEqual(failure["output_written_diagnostic"]["hit_ratio"], 1.0)
        evidence = {}
        RA._copy_output_written_evidence(evidence, failure, produced=False)
        self.assertEqual(evidence["output_written_check"], "failed_all_sentinel")

    def test_normal_write_is_produced_and_check_passes(self):
        _torch, summary, manifest = self._invoke(write_value=7)
        self.assertEqual((summary["produced"], summary["failed"]), (1, 0))
        output = manifest["produced"][0]["outputs"][0]
        self.assertEqual(output["output_written_check"], D.OUTPUT_CHECK_PASSED)
        evidence = {}
        RA._copy_output_written_evidence(evidence, output, produced=True)
        self.assertEqual(evidence["output_written_check"], "passed")

    def test_bool_and_empty_skips_are_visible(self):
        _torch, _summary, bool_manifest = self._invoke(dtype="bool")
        bool_output = bool_manifest["produced"][0]["outputs"][0]
        self.assertEqual(bool_output["output_written_check"], D.OUTPUT_CHECK_SKIPPED_BOOL)
        bool_evidence = {}
        RA._copy_output_written_evidence(bool_evidence, bool_output, produced=True)
        self.assertEqual(bool_evidence["output_written_check"], "skipped_bool")

        _torch, _summary, empty_manifest = self._invoke(shape=[0])
        empty_output = empty_manifest["produced"][0]["outputs"][0]
        self.assertEqual(empty_output["output_written_check"], D.OUTPUT_CHECK_SKIPPED_EMPTY)
        empty_evidence = {}
        RA._copy_output_written_evidence(empty_evidence, empty_output, produced=True)
        self.assertEqual(empty_evidence["output_written_check"], "skipped_empty")


class CustomOppBindingTest(unittest.TestCase):
    """自定义算子符号来源必须由收据绑定，而不是由「谁 source 过 set_env.bash」决定。

    这批守的是一个**可复现性**缺陷：改动前 driver 从不设 `ASCEND_CUSTOM_OPP_PATH`，
    干净现场里 164 条 case 全部 `execution_failed`（`aclnnXxx ... not in libopapi.so`），
    而同一份 codegen 产物在人手动 source 过 vendor set_env.bash 的现场里却「跑通了」。
    成功依赖了一份没有被任何产物记录的环境状态 = 结果不可复现。
    """

    _ENV = "ASCEND_CUSTOM_OPP_PATH"

    def _lib(self, root, pkg="oprunway_witness"):
        """按 CANN 自定义算子包布局造一个见证 `.so`：<root>/vendors/<pkg>/op_api/lib/…"""
        pkg_dir = os.path.join(root, "vendors", pkg)
        lib_dir = os.path.join(pkg_dir, "op_api", "lib")
        os.makedirs(lib_dir)
        lib = os.path.join(lib_dir, "libcust_opapi.so")
        with open(lib, "wb") as dst:
            dst.write(b"vendor")
        return os.path.realpath(pkg_dir), os.path.realpath(lib)

    def test_binds_package_derived_from_receipt_bound_library(self):
        with tempfile.TemporaryDirectory() as td:
            pkg, lib = self._lib(td)
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop(self._ENV, None)
                self.assertEqual(D.bind_custom_opp_path(lib), pkg)
                self.assertEqual(os.environ[self._ENV], pkg)

    def test_layout_mismatch_is_fail_closed(self):
        """路径结构对不上不猜一个根出来——猜错就等于测在别的包上还说不出来。"""
        with tempfile.TemporaryDirectory() as td:
            stray = os.path.join(td, "lib.so")
            with open(stray, "wb") as dst:
                dst.write(b"vendor")
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop(self._ENV, None)
                with self.assertRaises(D.DriverError):
                    D.bind_custom_opp_path(stray)

    def test_conflicting_preexisting_value_is_fail_closed(self):
        """环境里已指着别的 vendor 包 → 拒，不覆盖也不追加（防跑在别人的符号上）。"""
        with tempfile.TemporaryDirectory() as td:
            _pkg, lib = self._lib(td)
            other, _ = self._lib(td, pkg="stale_other")
            with mock.patch.dict(os.environ, {self._ENV: other}):
                with self.assertRaisesRegex(D.DriverError, "不一致"):
                    D.bind_custom_opp_path(lib)

    def test_already_bound_to_same_package_is_accepted(self):
        """人先 source 过本轮 vendor 的 set_env.bash（值含尾随冒号）→ 同一个包，放行并归一化。"""
        with tempfile.TemporaryDirectory() as td:
            pkg, lib = self._lib(td)
            with mock.patch.dict(os.environ, {self._ENV: pkg + os.pathsep}):
                self.assertEqual(D.bind_custom_opp_path(lib), pkg)
                self.assertEqual(os.environ[self._ENV], pkg)


if __name__ == "__main__":
    unittest.main()
