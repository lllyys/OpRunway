#!/usr/bin/env python3
"""generated golden 的受控调用上下文契约测试。

本文件只测通用 ABI 与内容绑定，不调用任何真实算子：

* legacy ``golden_fn(inputs, attrs)`` 不收到新参数，也不在 caseset 增加新字段；
* 显式声明的新 ABI 只允许 keyword-only ``case_context``；
* logical BF16 与真实 FP32 即使都以 NumPy ``float32`` 载体进入 golden，仍可由
  受控的逐输入 logical dtype 区分；
* schema / mode / 函数签名不匹配全部 fail-closed；
* 新 ABI 的 contract、逐 case context 与 digest 随 caseset 落账。

跑：``python3 -m unittest test_golden_invocation_context -v``
"""

import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

import numpy as np

import content_address
import cann_version as CV
import cpp_extension_adapter as A
import cpp_extension_codegen as CODEGEN
import cpp_extension_driver as DRIVER
import check_golden as CHECK
import gen_cases as GC
import precision_policy as P
import validate_acceptance_state as VAS
import vendor_build_receipt as VBR
from test_current_receipt_fixtures import vendor_build_receipt
from test_gen_cases_multi_input import _binary_spec
from test_validate_cpp_extension_receipt import (
    _symbol_identity,
    source_facts_payload,
)


_BASE_CONTRACT = {
    "source": "single_api",
    "method_kind": "torch_cpu",
    "authorization": {"kind": "impl_reference"},
}

_INVOCATION = {
    "schema": "oprunway.golden_invocation",
    "schema_version": 1,
    "mode": "keyword_case_context_v1",
}


def _spec(op, dtypes=("float32", "bfloat16")):
    """只驱动生成层的最小字段化 spec；不借 runner form 缺省。"""
    return {
        "op": op,
        "runner_form": "cpp",
        "verify_mode": "numerical",
        "params_source": "fixture",
        "params": [
            {"name": "self", "io": "in", "dtype": list(dtypes)},
            {"name": "out", "io": "out", "dtype": list(dtypes)},
        ],
        "precision": {
            "oracle": "ascendoptest",
            "standard": "ascendoptest_default",
            "case_target": 2,
            # 本夹具的 BF16 输出是数值输出；阈值口径显式进入既有受控路径。
            "bf16_lossy": True,
        },
        "perf": {
            "baseline": "tbe",
            "target_ratio": 0.95,
            "case_source": "precision_cases",
            "shape_classification": {
                "metric": "sum_input_bytes",
                "small_max_bytes": 262144,
                "hardware": "Atlas A3",
            },
        },
    }


def _write_golden(root, op, body, contract=None):
    op_dir = os.path.join(root, op)
    os.makedirs(op_dir, exist_ok=True)
    with open(os.path.join(op_dir, "golden.py"), "w", encoding="utf-8") as dst:
        dst.write(
            "import numpy as np\n"
            "GOLDEN_SOURCE = 'torch fixture'\n"
            "GOLDEN_PROVENANCE = 'logical dtype context fixture'\n"
        )
        if contract is not None:
            dst.write(f"GOLDEN_CONTRACT = {contract!r}\n")
        dst.write(body)


class GoldenInvocationContractTest(unittest.TestCase):
    def test_invocation_schema_and_mode_are_closed_vocabularies(self):
        P.validate_golden_contract({**_BASE_CONTRACT, "invocation": _INVOCATION})
        invalid = (
            {**_INVOCATION, "schema": "oprunway.golden_context"},
            {**_INVOCATION, "schema_version": 2},
            {**_INVOCATION, "schema_version": True},
            {**_INVOCATION, "mode": "positional_context_v1"},
            {**_INVOCATION, "extra": "ignored"},
            {"schema": _INVOCATION["schema"], "schema_version": 1},
        )
        for invocation in invalid:
            with self.subTest(invocation=invocation), self.assertRaises(ValueError):
                P.validate_golden_contract(
                    {**_BASE_CONTRACT, "invocation": invocation})
        with self.assertRaises(ValueError):
            P.validate_golden_contract({**_BASE_CONTRACT, "invocation": None})

    def test_declared_abi_requires_exact_keyword_only_context_parameter(self):
        contract = {**_BASE_CONTRACT, "invocation": _INVOCATION}

        def valid(inputs, attrs, *, case_context):
            return inputs[0]

        self.assertTrue(P.validate_golden_fn_contract(valid, contract))

        def missing(inputs, attrs):
            return inputs[0]

        def positional(inputs, attrs, case_context):
            return inputs[0]

        def catch_all(inputs, attrs, **kwargs):
            return inputs[0]

        namespace = {}
        exec(
            "def positional_only(inputs, attrs, /, *, case_context):\n"
            "    return inputs[0]\n",
            namespace,
        )
        positional_only = namespace["positional_only"]

        for fn in (missing, positional, catch_all, positional_only):
            with self.subTest(fn=fn.__name__), self.assertRaises(ValueError):
                P.validate_golden_fn_contract(fn, contract)


class GeneratedGoldenContextTest(unittest.TestCase):
    def setUp(self):
        self.ops_root = os.path.realpath(tempfile.mkdtemp(prefix="golden_context_ops_"))
        self.work_roots = []
        self.env = mock.patch.dict(
            os.environ, {"OPRUNWAY_OPS_DIR": self.ops_root})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.ops_root, ignore_errors=True)
        for path in self.work_roots:
            shutil.rmtree(path, ignore_errors=True)

    def work(self):
        path = tempfile.mkdtemp(prefix="golden_context_work_")
        self.work_roots.append(path)
        return path

    def test_logical_bf16_and_real_fp32_are_distinguishable_on_same_carrier(self):
        op = "LogicalDtypeWitness"
        contract = {**_BASE_CONTRACT, "invocation": _INVOCATION}
        _write_golden(
            self.ops_root,
            op,
            "def golden_fn(inputs, attrs, *, case_context):\n"
            "    x = inputs[0]\n"
            "    if x.dtype != np.float32:\n"
            "        raise AssertionError(f'expected shared float32 carrier, got {x.dtype}')\n"
            "    logical = case_context['inputs'][0]['logical_dtype']\n"
            "    marker = {'bfloat16': 5.0, 'float32': 3.0}[logical]\n"
            "    return np.full(x.shape, marker, dtype=np.float32)\n",
            contract,
        )
        work = self.work()
        caseset = GC.gen_cases(_spec(op), work)

        by_dtype = {}
        for case in caseset["cases"]:
            dtype = case["inputs"][0]["dtype"]
            context = case["golden_case_context"]
            self.assertEqual(
                context,
                {
                    "schema": "oprunway.golden_case_context",
                    "schema_version": 1,
                    "inputs": [
                        {"index": 0, "name": "self", "logical_dtype": dtype},
                    ],
                },
            )
            golden_path = case["expected"].get("golden_path")
            if golden_path and np.prod(case["inputs"][0]["shape"], dtype=np.int64):
                by_dtype.setdefault(dtype, np.load(os.path.join(work, golden_path)))

        self.assertEqual(set(by_dtype), {"float32", "bfloat16"})
        self.assertTrue(np.all(by_dtype["float32"] == np.float32(3.0)))
        self.assertTrue(np.all(by_dtype["bfloat16"] == np.float32(5.0)))

        receipt = caseset["golden_invocation_receipt"]
        self.assertEqual(receipt["invocation"], _INVOCATION)
        self.assertEqual(
            receipt["invocation_sha256"],
            content_address.content_digest(
                "oprunway/golden-invocation-contract/v1", _INVOCATION),
        )
        self.assertEqual(receipt["case_count"], len(caseset["cases"]))
        self.assertEqual(len(receipt["case_contexts"]), len(caseset["cases"]))

        dry = GC._build_dry_run_ledger(_spec(op))
        self.assertEqual(dry["golden_dependency"]["status"], "loaded")
        self.assertEqual(
            dry["golden_dependency"]["contract_sha256"],
            hashlib.sha256(
                content_address.canonical_json_bytes(contract)).hexdigest(),
        )
        self.assertIn("gen_cases.py", dry["planner_binding"]["logic_files"])
        self.assertIn("precision_policy.py", dry["planner_binding"]["logic_files"])

    def test_legacy_two_argument_golden_keeps_legacy_payload_shape(self):
        op = "LegacyGoldenWitness"
        _write_golden(
            self.ops_root,
            op,
            "def golden_fn(inputs, attrs):\n"
            "    return np.negative(inputs[0])\n",
            _BASE_CONTRACT,
        )
        caseset = GC.gen_cases(_spec(op, dtypes=("float32",)), self.work())
        self.assertNotIn("golden_invocation_receipt", caseset)
        self.assertTrue(caseset["cases"])
        self.assertTrue(all(
            "golden_case_context" not in case for case in caseset["cases"]
        ))

    def test_load_rejects_declared_context_with_mismatched_signature(self):
        contract = {**_BASE_CONTRACT, "invocation": _INVOCATION}
        bodies = {
            "MissingContext": (
                "def golden_fn(inputs, attrs):\n    return inputs[0]\n"
            ),
            "PositionalContext": (
                "def golden_fn(inputs, attrs, case_context):\n    return inputs[0]\n"
            ),
            "CatchAllContext": (
                "def golden_fn(inputs, attrs, **kwargs):\n    return inputs[0]\n"
            ),
        }
        for op, body in bodies.items():
            _write_golden(self.ops_root, op, body, contract)
            with self.subTest(op=op), self.assertRaises(ValueError):
                GC.load_golden(op)

    def test_check_golden_mirrors_the_load_time_abi_gate(self):
        op = "StaticCheckMissingContext"
        contract = {**_BASE_CONTRACT, "invocation": _INVOCATION}
        _write_golden(
            self.ops_root, op,
            "def golden_fn(inputs, attrs):\n    return inputs[0]\n",
            contract,
        )
        ledger = CHECK.check(op)
        self.assertFalse(ledger["contract_ok"])
        self.assertIn("[调用ABI]", ledger["error"])

    def test_context_is_ordered_by_spec_input_identity_not_numpy_dtype(self):
        in_params = [
            {"name": "lhs", "io": "in"},
            {"name": "rhs", "io": "in"},
        ]
        context = GC._golden_case_context(
            in_params, ["bfloat16", "float32"], where="fixture")
        self.assertEqual(
            context["inputs"],
            [
                {"index": 0, "name": "lhs", "logical_dtype": "bfloat16"},
                {"index": 1, "name": "rhs", "logical_dtype": "float32"},
            ],
        )
        with self.assertRaises(ValueError):
            GC._golden_case_context(
                list(reversed(in_params)), ["bfloat16"], where="mismatch")


class GoldenInvocationReceiptBindingTest(unittest.TestCase):
    def _caseset(self):
        contexts = [
            GC._golden_case_context(
                [{"name": "self", "io": "in"}], [dtype], where=case_id)
            for case_id, dtype in (("c0", "float32"), ("c1", "bfloat16"))
        ]
        cases = [
            {
                "id": case_id,
                "inputs": [{"name": "self", "dtype": dtype}],
                "golden_case_context": context,
            }
            for (case_id, dtype), context in zip(
                (("c0", "float32"), ("c1", "bfloat16")), contexts)
        ]
        contract = {**_BASE_CONTRACT, "invocation": _INVOCATION}
        return {
            "op": "Witness",
            "golden_invocation_receipt": GC._build_golden_invocation_receipt(
                contract, cases),
            "cases": cases,
        }

    def test_receipt_digest_changes_with_logical_dtype(self):
        caseset = self._caseset()
        original = caseset["golden_invocation_receipt"]["case_contexts_sha256"]
        mismatched = copy.deepcopy(caseset)
        mismatched["cases"][1]["golden_case_context"]["inputs"][0][
            "logical_dtype"
        ] = "float32"
        with self.assertRaises(ValueError):
            GC._build_golden_invocation_receipt(
                {**_BASE_CONTRACT, "invocation": _INVOCATION},
                mismatched["cases"],
            )

        changed = copy.deepcopy(mismatched)
        changed["cases"][1]["inputs"][0]["dtype"] = "float32"
        rebuilt = GC._build_golden_invocation_receipt(
            {**_BASE_CONTRACT, "invocation": _INVOCATION}, changed["cases"])
        self.assertNotEqual(original, rebuilt["case_contexts_sha256"])

    def test_logical_dtype_uses_one_closed_vocabulary_at_every_boundary(self):
        caseset = self._caseset()
        bad = copy.deepcopy(caseset)
        bad["cases"][1]["inputs"][0]["dtype"] = "float128"
        bad["cases"][1]["golden_case_context"]["inputs"][0][
            "logical_dtype"
        ] = "float128"
        with self.assertRaises(ValueError):
            GC._build_golden_invocation_receipt(
                {**_BASE_CONTRACT, "invocation": _INVOCATION}, bad["cases"])
        with self.assertRaises(ValueError):
            P.validate_golden_case_context(
                bad["cases"][1]["golden_case_context"],
                expected_inputs=bad["cases"][1]["inputs"],
            )

    def test_cpp_extension_revalidates_context_against_frozen_case_inputs(self):
        caseset = self._caseset()
        self.assertEqual(
            A.validate_caseset_golden_invocation(caseset),
            caseset["golden_invocation_receipt"],
        )
        changed = copy.deepcopy(caseset)
        changed["cases"][1]["inputs"][0]["dtype"] = "float32"
        with self.assertRaises(A.CppExtensionAdapterError):
            A.validate_caseset_golden_invocation(changed)

        malformed = copy.deepcopy(caseset)
        malformed["cases"][1]["inputs"][0] = "not-an-input-object"
        with self.assertRaises(A.CppExtensionAdapterError):
            A.validate_caseset_golden_invocation(malformed)

        phantom = {"op": "Witness", "cases": copy.deepcopy(caseset["cases"])}
        with self.assertRaises(A.CppExtensionAdapterError):
            A.validate_caseset_golden_invocation(phantom)


class GoldenInvocationDriverErrorTest(unittest.TestCase):
    def test_malformed_golden_receipt_is_not_misclassified_as_layout_error(self):
        with tempfile.TemporaryDirectory() as root:
            bundle = os.path.join(root, "bundle")
            work = os.path.join(root, "work")
            os.makedirs(bundle)
            os.makedirs(work)
            caseset = {
                "op": "Witness",
                "golden_invocation_receipt": {"schema": "bad"},
                "cases": [{"id": "c0", "inputs": []}],
            }
            manifest = {"namespace": "oprunway_witness", "spec_sha256": "a" * 64}
            plan = {
                "caseset_sha256": DRIVER._canonical_sha(caseset),
                "manifest_sha256": DRIVER._canonical_sha(manifest),
                "namespace": manifest["namespace"],
                "cases": [],
            }
            for path, value in (
                (os.path.join(bundle, "extension_manifest.json"), manifest),
                (os.path.join(work, "cpp_extension_invocation_plan.json"), plan),
                (os.path.join(work, "cpp_extension_caseset.json"), caseset),
            ):
                with open(path, "w", encoding="utf-8") as dst:
                    json.dump(value, dst)
            with self.assertRaises(DRIVER.DriverError) as caught:
                DRIVER.run(bundle, work)
            self.assertIs(type(caught.exception), DRIVER.DriverError)
            self.assertIn("golden invocation", str(caught.exception))


def _write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as dst:
        json.dump(value, dst, ensure_ascii=False, sort_keys=True)


class GoldenInvocationEndToEndBindingTest(unittest.TestCase):
    """真实走 planner/adapter/driver/正式门；只替换外部 ELF/NPU 执行。"""

    def _swap_first_profile_identity(self, caseset):
        """造一份内部摘要可重算、但与 staged spec 的 profile 顺序不一致的篡改。"""
        case = caseset["cases"][0]
        case["inputs"] = list(reversed(case["inputs"]))
        contract = case["parameter_contract"]
        tensors = [item for item in contract["inputs"] if item["kind"] == "tensor"]
        self.assertEqual(2, len(tensors))
        contract["inputs"] = list(reversed(contract["inputs"]))
        slots = case["aclnn_call"]["slots"]
        input_positions = [i for i, slot in enumerate(slots) if slot["role"] == "in"]
        swapped = list(reversed([slots[i] for i in input_positions]))
        for input_idx, (position, slot) in enumerate(zip(input_positions, swapped)):
            slot["input_idx"] = input_idx
            slots[position] = slot
        case["golden_case_context"]["inputs"] = [
            {"index": index, "name": item["name"], "logical_dtype": item["dtype"]}
            for index, item in enumerate(case["inputs"])
        ]
        caseset["golden_invocation_receipt"] = GC._build_golden_invocation_receipt(
            {**_BASE_CONTRACT, "invocation": _INVOCATION}, caseset["cases"])

    def _fixture(self, root, *, consistent_profile_tamper=False):
        work = os.path.join(root, "work")
        bundle = os.path.join(work, "cpp_extension")
        ops_root = os.path.join(root, "ops")
        os.makedirs(work)
        spec = _binary_spec()
        spec["runtime_requirements"] = {"cann": {"kind": "not_declared"}}
        spec["call_variants"][0]["stage2_form"] = "standard"
        _write_json(os.path.join(root, "spec.json"), spec)
        _write_golden(
            ops_root,
            spec["op"],
            "def golden_fn(inputs, attrs, *, case_context):\n"
            "    names = [row['name'] for row in case_context['inputs']]\n"
            "    if names != ['left', 'right']:\n"
            "        raise ValueError(f'unexpected input identity: {names}')\n"
            "    return np.add(inputs[0], inputs[1])\n",
            {**_BASE_CONTRACT, "invocation": _INVOCATION},
        )
        with mock.patch.dict(os.environ, {"OPRUNWAY_OPS_DIR": ops_root}):
            caseset = GC.gen_cases(spec, work)
        if consistent_profile_tamper:
            self._swap_first_profile_identity(caseset)

        manifest = CODEGEN.generate(spec, bundle)
        plan = A.build_invocation_plan(caseset, manifest)
        _write_json(os.path.join(work, "cpp_extension_caseset.json"), caseset)
        _write_json(os.path.join(work, "cpp_extension_invocation_plan.json"), plan)

        artifact = os.path.join(bundle, "oprunway_witness.so")
        with open(artifact, "wb") as dst:
            dst.write(b"extension")
        vendor_pkg = os.path.join(root, "vendor_root", "vendors", "witness")
        vendor = os.path.join(vendor_pkg, "op_api", "lib", "libcust_opapi.so")
        os.makedirs(os.path.dirname(vendor), exist_ok=True)
        with open(vendor, "wb") as dst:
            dst.write(b"vendor")
        vendor_sha = DRIVER._sha_file(vendor)
        build_receipt = vendor_build_receipt(
            vendor, vendor_sha, source_root=os.path.join(root, "source"),
            scope="witness_op", subtree="d" * 64)
        symbol_identity = _symbol_identity(vendor, vendor_sha, plan)
        vendor_binding = {
            "library_path": vendor,
            "library_sha256": vendor_sha,
            "symbols_owned": [row["symbol"] for row in symbol_identity["definitions"]],
            "symbol_identity": symbol_identity,
            "binding": "fixture external ELF boundary",
            "build_receipt": build_receipt,
            "build_receipt_sha256": DRIVER._canonical_sha(build_receipt),
            "source_provenance": VBR.summarize(build_receipt),
        }
        fake_torch = types.SimpleNamespace(__version__="2.fixture")
        fake_torch_npu = types.ModuleType("torch_npu")
        fake_torch_npu.__version__ = "2.fixture"
        invocation = A.build_invocation_accounting(
            plan,
            produced_case_ids=[row["case_id"] for row in plan["cases"]],
            failed_case_ids=[],
        )
        invocation["execution_isolation"] = {
            "schema": A.EXECUTION_ISOLATION_SCHEMA,
            "schema_version": A.EXECUTION_ISOLATION_VERSION,
            "mode": "subprocess_per_case_v1",
            "records": [
                {
                    "case_id": row["case_id"],
                    "launch_id": f"golden-invocation-fixture-{index}",
                    "isolation_mode": "subprocess_per_case_v1",
                    "termination_kind": "normal",
                    "returncode": 0,
                    "parent_pid": 1000 + index,
                    "child_pid": 1000 + index,
                    "outcome": "produced",
                    "call_status": {
                        "schema": "oprunway.cpp_extension_call_status",
                        "schema_version": 1,
                        "stage1_ret": 0,
                        "workspace_size": 0,
                        "executor_null": False,
                        "stage2_called": True,
                        "stage2_ret": 0,
                    },
                }
                for index, row in enumerate(plan["cases"])
            ],
        }
        schemas = {
            row["entrypoint"]: f"oprunway_witness::{row['entrypoint']}(...)"
            for row in manifest["variants"]
        }
        observation = CV.unknown_observation({
            "api": CV.PROBE_API,
            "package": CV.PROBE_PACKAGE,
            "returncode": 7,
            "returncode_source": CV.PROBE_RETURN_MEASURED,
            "defining_elf": None,
        }, "fixture: runtime requirement not declared")
        with mock.patch.object(
                DRIVER, "_bind_vendor",
                return_value=(object(), vendor_pkg, vendor_binding)), mock.patch.object(
                DRIVER, "_build",
                return_value=(["python", "setup.py", "build_ext", "--inplace"], artifact)), \
                mock.patch.object(
                    DRIVER, "_invoke_all",
                    return_value=(fake_torch, schemas, invocation)), mock.patch.object(
                    DRIVER, "probe_runtime_cann_version", return_value=observation), \
                mock.patch.dict(sys.modules, {"torch_npu": fake_torch_npu}), \
                mock.patch.dict(os.environ, {"OPRUNWAY_SOC": "AscendFixture"}):
            receipt = DRIVER.run(bundle, work)

        evidence = [
            {
                "case_id": row["case_id"],
                "status": "ok",
                "cpp_extension_receipt_sha256": A._canonical_sha(receipt),
            }
            for row in plan["cases"]
        ]
        A.bind_execution_isolation_evidence(
            evidence, receipt["execution_isolation"])
        A._bind_multi_input_evidence(caseset, evidence, receipt)
        envelope = {
            "runner_form": "cpp_extension",
            "cpp_extension_receipt": receipt,
            "evidence": evidence,
        }
        _write_json(
            os.path.join(root, "source_facts.json"),
            content_address.make_artifact(
                "oprunway/source-facts/v1",
                source_facts_payload(
                    snapshot_merkle="d" * 64, snapshot_scope="witness_op"),
            ),
        )
        return spec, caseset, manifest, plan, receipt, evidence, envelope

    def _formal_errors(self, root, caseset, envelope, evidence):
        errors = []
        VAS._gate_cpp_extension_receipt(
            root, caseset, envelope, evidence, errors)
        return errors

    def test_profile_to_formal_gate_binds_exact_multi_input_name_order(self):
        with tempfile.TemporaryDirectory() as root:
            _spec, caseset, _manifest, plan, receipt, evidence, envelope = (
                self._fixture(root))
            expected = [
                [(row["name"], row["logical_dtype"])
                 for row in case["golden_case_context"]["inputs"]]
                for case in caseset["cases"]
            ]
            self.assertEqual(
                expected,
                [[(item["name"], item["dtype"])
                  for item in case["parameter_contract"]["inputs"]
                  if item["kind"] == "tensor"]
                 for case in caseset["cases"]],
            )
            self.assertEqual(
                plan["golden_invocation_receipt_sha256"],
                receipt["bindings"]["golden_invocation_receipt_sha256"],
            )
            self.assertEqual(receipt, A.validate_receipt(
                os.path.join(root, "work"), caseset))
            self.assertTrue(all("parameter_contract_sha256" in row
                                for row in evidence))
            self.assertEqual([], self._formal_errors(
                root, caseset, envelope, evidence))

    def test_single_layer_mutations_are_rejected_at_each_consumer(self):
        with tempfile.TemporaryDirectory() as root:
            _spec, caseset, _manifest, plan, receipt, evidence, envelope = (
                self._fixture(root))
            bad_context = copy.deepcopy(caseset)
            rows = bad_context["cases"][0]["golden_case_context"]["inputs"]
            rows[0]["name"], rows[1]["name"] = rows[1]["name"], rows[0]["name"]
            with self.assertRaises(A.CppExtensionAdapterError):
                A.validate_caseset_golden_invocation(bad_context)

            bad_receipt = copy.deepcopy(receipt)
            bad_receipt["bindings"]["golden_invocation_receipt_sha256"] = "0" * 64
            _write_json(
                os.path.join(root, "work", "cpp_extension_receipt.json"),
                bad_receipt)
            with self.assertRaises(A.CppExtensionAdapterError):
                A.validate_receipt(os.path.join(root, "work"), caseset)
            _write_json(
                os.path.join(root, "work", "cpp_extension_receipt.json"), receipt)

            bad_evidence = copy.deepcopy(evidence)
            bad_evidence[0]["cpp_extension_receipt_sha256"] = "0" * 64
            self.assertTrue(self._formal_errors(
                root, caseset, envelope, bad_evidence))

    def test_shared_precision_work_gate_rechecks_multi_input_evidence_binding(self):
        with tempfile.TemporaryDirectory() as root:
            spec, caseset, _manifest, _plan, _receipt, evidence, envelope = (
                self._fixture(root))
            work = os.path.join(root, "work")
            bad_evidence = copy.deepcopy(evidence)
            del bad_evidence[0]["multi_input_case_binding_sha256"]
            bad_envelope = {**envelope, "evidence": bad_evidence}
            _write_json(os.path.join(work, "caseset.json"), caseset)
            _write_json(os.path.join(work, "evidence.json"), bad_envelope)
            errors = []
            VAS._gate_precision_work_dir(
                root, work, caseset, bad_envelope, spec, errors)
            self.assertTrue(any("multi_input" in item and "binding" in item
                                for item in errors), errors)

    def test_consistently_rehashed_profile_order_tamper_fails_static_manifest_gate(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(
                    A.CppExtensionAdapterError, "manifest.*顺序|顺序.*manifest"):
                self._fixture(root, consistent_profile_tamper=True)

if __name__ == "__main__":
    unittest.main()
