#!/usr/bin/env python3
"""aclnn_py harness 信任门的纯确定性单测；不 build、不访问 NPU。"""

import ast
import json
import os
import tempfile
import unittest
from unittest import mock

import content_address
import run_workflow
import verify_aclnn_harness as H


def _variant(nullable):
    return {
        "symbol": "Reduce",
        "slot_contract": [
            {"name": "self", "role": "in", "nullable": False},
            {"name": "dim", "role": "attr", "nullable": False, "ctype": "int64"},
            {"name": "keepDim", "role": "attr", "nullable": False, "ctype": "bool"},
            {"name": "valuesOut", "role": "out", "nullable": False},
            {"name": "indicesOut", "role": "out", "nullable": nullable},
        ],
    }


def _case(cid, dtype, nullable, size):
    slots = [
        {"role": "in", "name": "self", "input_idx": 0},
        {"role": "attr", "name": "dim", "ctype": "int64", "value": 0},
        {"role": "attr", "name": "keepDim", "ctype": "bool", "value": False},
        {"role": "out", "name": "valuesOut", "output_idx": 0},
        ({"role": "out_null", "name": "indicesOut"} if nullable
         else {"role": "out", "name": "indicesOut", "output_idx": 1}),
    ]
    outputs = [{
        "name": "valuesOut",
        "role": "value",
        "out_shape": [size],
        "policy": {"kind": "torch_allclose"},
        "golden_path": f"{cid}/golden_0.npy",
    }]
    if not nullable:
        outputs.append({
            "name": "indicesOut",
            "role": "index",
            "out_shape": [size],
            "policy": {"kind": "exact"},
            "golden_path": f"{cid}/golden_1.npy",
        })
    return {
        "id": cid,
        "inputs": [{
            "name": "self", "dtype": dtype, "shape": [size],
            "path": f"{cid}/x1.npy",
        }],
        "expected": {"outputs": outputs},
        "aclnn_call": {"symbol": "Reduce", "slots": slots},
    }


def _fixtures():
    preflight = {
        "status": "READY_WAIT_NPU_TRUST_GATE",
        "bindings": {"spec_sha256": "unused", "pr_head_sha": "a" * 40},
        "variants": [_variant(True), _variant(False)],
    }
    caseset = {
        "op": "Reduce",
        "dtype_required": ["float32", "float16"],
        "cases": [
            _case("f32_scalar_large", "float32", True, 64),
            _case("f32_scalar_small", "float32", True, 1),
            _case("f16_multi_small", "float16", False, 1),
            _case("f32_multi_large", "float32", False, 128),
        ],
    }
    return caseset, preflight


def _execution_fixture():
    return {
        "config": {
            "target": "remote",
            "op_subdir": "experimental/reduce",
            "vendor_name": "customize",
            "base_repo": "https://example.invalid/ops.git",
            "pr_ref": "a" * 40,
            "head_sha": "a" * 40,
            "soc": "ascend-test",
            "snake_op": "reduce",
            "device": 0,
            "build_args": "--pkg --ops=reduce",
            "symbols": ["Reduce"],
            "reuse_build": True,
        },
        "target_digest": "b" * 64,
        "runtime": {
            "toolkit": "/opt/toolkit",
            "toolkit_version": "test",
        },
    }


def _build_provenance_fixture():
    execution = _execution_fixture()
    cfg = execution["config"]
    return {
        "head_sha": cfg["head_sha"],
        "pr_ref": cfg["pr_ref"],
        "base_repo": cfg["base_repo"],
        "op_subdir": cfg["op_subdir"],
        "snake_op": cfg["snake_op"],
        "soc": cfg["soc"],
        "vendor_name": cfg["vendor_name"],
        "build_args": cfg["build_args"],
        "symbols": cfg["symbols"],
        "toolkit": execution["runtime"]["toolkit"],
        "toolkit_version": execution["runtime"]["toolkit_version"],
        "build_reused": True,
        "stamp_mismatch_rebuilt": False,
        "so_digest_unavailable": False,
    }


class SelectionTest(unittest.TestCase):
    def test_minimum_witness_covers_dtype_variants_attrs_and_multi_output(self):
        caseset, preflight = _fixtures()
        selected, coverage = H.select_cases(caseset, preflight)
        self.assertEqual(
            [case["id"] for case in selected],
            ["f16_multi_small", "f32_scalar_small"])
        self.assertEqual(coverage["selected_count"], 2)
        self.assertEqual(coverage["full_case_count"], 4)
        self.assertIn("capability:scalar_attr", coverage["covered"])
        self.assertIn("capability:multi_output", coverage["covered"])
        self.assertIn("dtype:float32", coverage["covered"])
        self.assertIn("dtype:float16", coverage["covered"])

    def test_dtype_required_gap_does_not_override_actual_cases(self):
        caseset, preflight = _fixtures()
        caseset["dtype_required"].append("int32")
        _, coverage = H.select_cases(caseset, preflight)
        self.assertNotIn("dtype:int32", coverage["required"])

    def test_declared_tested_dtype_must_exist_in_actual_cases(self):
        caseset, preflight = _fixtures()
        caseset["dtype_tested"] = ["float32", "int32"]
        with self.assertRaisesRegex(ValueError, "实际输入 dtype"):
            H.select_cases(caseset, preflight)

    def test_slot_order_must_bind_unique_preflight_variant(self):
        caseset, preflight = _fixtures()
        slots = caseset["cases"][0]["aclnn_call"]["slots"]
        slots[0], slots[1] = slots[1], slots[0]
        with self.assertRaisesRegex(ValueError, "无法唯一绑定"):
            H.select_cases(caseset, preflight)


class ReceiptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.spec = {"op": "Reduce", "runner_form": "aclnn_py"}
        self.caseset, self.preflight = _fixtures()
        selected, coverage = H.select_cases(self.caseset, self.preflight)
        self.selected = selected
        os.makedirs(os.path.join(self.root, "work"))
        self.ops_root = os.path.join(self.root, "ops")
        os.makedirs(os.path.join(self.ops_root, "Reduce"))
        with open(os.path.join(
                self.ops_root, "Reduce", "golden.py"), "wb") as out:
            out.write(b"# bound golden source\n")
        for case in self.caseset["cases"]:
            case_dir = os.path.join(self.root, "work", case["id"])
            os.makedirs(case_dir)
            with open(os.path.join(case_dir, "x1.npy"), "wb") as out:
                out.write(("input:" + case["id"]).encode())
            for expected in case["expected"]["outputs"]:
                path = os.path.join(
                    self.root, "work", expected["golden_path"])
                with open(path, "wb") as out:
                    out.write(("golden:" + expected["golden_path"]).encode())
        self.execution = _execution_fixture()
        self.build_provenance = _build_provenance_fixture()
        checks = []
        for case in selected:
            outputs = []
            for index, expected in enumerate(case["expected"]["outputs"]):
                out_rel = (
                    f"aclnn_trust_out/{case['id']}/out_{index}.bin")
                out_path = os.path.join(self.root, "work", out_rel)
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                with open(out_path, "wb") as out:
                    out.write(("out:" + out_rel).encode())
                golden_path = os.path.join(
                    self.root, "work", expected["golden_path"])
                outputs.append({
                    "index": index,
                    "name": expected["name"],
                    "role": expected["role"],
                    "policy_kind": expected["policy"]["kind"],
                    "result": "pass",
                    "golden_path": expected["golden_path"],
                    "out_path": out_rel,
                    "golden_sha256": H._file_sha(golden_path),
                    "out_sha256": H._file_sha(out_path),
                })
            checks.append({
                "case_id": case["id"], "result": "pass",
                "outputs": outputs,
            })
        with mock.patch.dict(
                os.environ, {"OPRUNWAY_OPS_DIR": self.ops_root}):
            bindings = H._receipt_bindings(
                self.root, self.spec, self.caseset, self.preflight,
                selected, self.execution)
        self.payload = {
            "schema": H._SCHEMA,
            "schema_version": 1,
            "status": H._STATUS_TRUSTED,
            "scope": "harness-only",
            "acceptance_verdict": None,
            "bindings": bindings,
            "coverage": coverage,
            "checks": checks,
            "build_provenance": self.build_provenance,
        }
        content_address.write_artifact(
            self.root, "work/aclnn_preflight.json",
            H._PREFLIGHT_DOMAIN, self.preflight)
        self._write_receipt()

    def _write_receipt(self):
        content_address.write_artifact(
            self.root, "work/aclnn_harness_trust.json",
            H._TRUST_DOMAIN, self.payload)

    def _validate(self, caseset=None):
        with mock.patch.dict(
                os.environ, {"OPRUNWAY_OPS_DIR": self.ops_root}), \
                mock.patch.object(
                    H, "_current_execution_binding",
                    return_value=self.execution):
            return H.validate_receipt(
                self.root, "work/aclnn_harness_trust.json",
                self.spec, caseset or self.caseset)

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_receipt_is_reusable_for_same_full_caseset(self):
        receipt = self._validate()
        self.assertEqual(receipt["status"], H._STATUS_TRUSTED)

    def test_caseset_drift_is_rejected(self):
        changed = json.loads(json.dumps(self.caseset))
        changed["cases"][0]["inputs"][0]["shape"] = [65]
        with self.assertRaisesRegex(ValueError, "caseset_sha256 已漂移"):
            self._validate(changed)

    def test_resealed_empty_output_checks_are_rejected(self):
        self.payload["checks"][0]["outputs"] = []
        self._write_receipt()
        with self.assertRaisesRegex(ValueError, "非空 array"):
            self._validate()

    def test_selected_input_byte_drift_is_rejected(self):
        case = self.selected[0]
        path = os.path.join(self.root, "work", case["inputs"][0]["path"])
        with open(path, "ab") as out:
            out.write(b"tamper")
        with self.assertRaisesRegex(ValueError, "selected_data 已漂移"):
            self._validate()

    def test_resealed_coverage_drift_is_rejected(self):
        self.payload["coverage"]["selection_rule"] = "forged"
        self._write_receipt()
        with self.assertRaisesRegex(ValueError, "coverage"):
            self._validate()

    def test_current_execution_environment_drift_is_rejected(self):
        changed = json.loads(json.dumps(self.execution))
        changed["runtime"]["toolkit_version"] = "other"
        with mock.patch.dict(
                os.environ, {"OPRUNWAY_OPS_DIR": self.ops_root}), \
                mock.patch.object(
                    H, "_current_execution_binding", return_value=changed):
            with self.assertRaisesRegex(ValueError, "execution 已漂移"):
                H.validate_receipt(
                    self.root, "work/aclnn_harness_trust.json",
                    self.spec, self.caseset)

    def test_resealed_output_bytes_drift_is_rejected(self):
        out_rel = self.payload["checks"][0]["outputs"][0]["out_path"]
        with open(os.path.join(self.root, "work", out_rel), "ab") as out:
            out.write(b"tamper")
        with self.assertRaisesRegex(ValueError, "实际字节已漂移"):
            self._validate()


class RunGateTest(unittest.TestCase):
    def test_successful_gate_writes_a_revalidatable_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            spec = {"op": "Reduce", "runner_form": "aclnn_py"}
            caseset, preflight = _fixtures()
            preflight["bindings"]["spec_sha256"] = H._sha(spec)
            selected, _ = H.select_cases(caseset, preflight)
            work = os.path.join(root, "work")
            ops_root = os.path.join(root, "ops")
            os.makedirs(os.path.join(ops_root, "Reduce"))
            with open(os.path.join(
                    ops_root, "Reduce", "golden.py"), "wb") as out:
                out.write(b"# golden source\n")
            with open(os.path.join(root, "spec.json"), "w",
                      encoding="utf-8") as out:
                json.dump(spec, out)
            with open(os.path.join(root, "caseset.json"), "w",
                      encoding="utf-8") as out:
                json.dump(caseset, out)
            content_address.write_artifact(
                root, "work/aclnn_preflight.json",
                H._PREFLIGHT_DOMAIN, preflight)

            evidence = []
            for case in selected:
                case_dir = os.path.join(work, case["id"])
                os.makedirs(case_dir)
                with open(os.path.join(case_dir, "x1.npy"), "wb") as out:
                    out.write(("input:" + case["id"]).encode())
                output_evidence = []
                for index, expected in enumerate(
                        case["expected"]["outputs"]):
                    golden_path = os.path.join(
                        work, expected["golden_path"])
                    with open(golden_path, "wb") as out:
                        out.write(("golden:" + case["id"] +
                                   f":{index}").encode())
                    out_rel = (
                        f"aclnn_trust_out/{case['id']}/out_{index}.bin")
                    out_path = os.path.join(work, out_rel)
                    os.makedirs(os.path.dirname(out_path), exist_ok=True)
                    with open(out_path, "wb") as out:
                        out.write(("actual:" + case["id"] +
                                   f":{index}").encode())
                    policy = expected["policy"]
                    metrics = (
                        {"exact_mismatch": 0, "numel": 1}
                        if policy["kind"] == "exact"
                        else {"mismatch": 0, "numel": 1,
                              "max_abs_err": 0.0, "max_rel_err": 0.0}
                    )
                    output_evidence.append({
                        "name": expected["name"],
                        "role": expected["role"],
                        "policy": policy,
                        "metrics": metrics,
                        "golden_path": expected["golden_path"],
                        "out_path": out_rel,
                        "provenance": {
                            "golden_sha256": H._file_sha(golden_path),
                            "out_sha256": H._file_sha(out_path),
                        },
                    })
                evidence.append({
                    "case_id": case["id"],
                    "status": "ok",
                    "precision": {"outputs": output_evidence},
                })

            execution = _execution_fixture()
            cfg = {
                "head_sha": "a" * 40,
                "ops_root": "/unused/ops",
                "op_subdir": "experimental/reduce",
            }
            with mock.patch.dict(os.environ, {
                    "OPRUNWAY_OPS_DIR": ops_root,
                    "OPRUNWAY_ACLNN_REAL": "1",
                    }), \
                    mock.patch.object(
                        H.aclnn_adapter, "_aclnn_cfg",
                        return_value=cfg), \
                    mock.patch.object(
                        H, "_execution_binding",
                        return_value=execution), \
                    mock.patch.object(
                        H.aclnn_adapter, "find_aclnn_project",
                        return_value="/unused/project"), \
                    mock.patch.object(
                        H.aclnn_adapter, "_run_aclnn_real",
                        return_value=_build_provenance_fixture()), \
                    mock.patch.object(
                        H.repo_adapter, "build_multi_output_evidence",
                        return_value=evidence):
                payload = H.run_gate(
                    root, "spec.json", "caseset.json",
                    "work/aclnn_preflight.json",
                    "work/aclnn_harness_trust.json")
                self.assertEqual(payload["status"], H._STATUS_TRUSTED)
                with mock.patch.object(
                        H, "_current_execution_binding",
                        return_value=execution):
                    reused = H.validate_receipt(
                        root, "work/aclnn_harness_trust.json",
                        spec, caseset)
            self.assertEqual(reused["coverage"]["selected_count"], 2)


_LOCAL_BINDINGS = {
    "dut_source": "local_checkout",
    "local_root_digest": "c" * 64,
}

# `bindings` 整个 key 缺席（区别于「值是 None」）的哨兵。
_ABSENT = object()


def _write_gate_root(root, bindings):
    """铺一个「刚够走到通路门」的根目录：spec/caseset/preflight 齐，work/ 空着即可。

    刻意只铺到这一步——通路门必须在任何真机配置读取、任何数据落盘之前触发，
    所以这些测试不需要 golden/输出字节也应当能把门打响。
    """
    spec = {"op": "Reduce", "runner_form": "aclnn_py"}
    caseset, preflight = _fixtures()
    if bindings is _ABSENT:
        preflight.pop("bindings", None)
    elif isinstance(bindings, dict):
        preflight["bindings"] = dict(bindings)
        preflight["bindings"]["spec_sha256"] = H._sha(spec)
    else:
        # 形态见证专用：畸形 bindings 原样落盘（spec_sha256 无处可挂），
        # 用来证明门在读任何字段之前就已经把形态判掉了。
        preflight["bindings"] = bindings
    os.makedirs(os.path.join(root, "work"), exist_ok=True)
    with open(os.path.join(root, "spec.json"), "w", encoding="utf-8") as out:
        json.dump(spec, out)
    with open(os.path.join(root, "caseset.json"), "w", encoding="utf-8") as out:
        json.dump(caseset, out)
    content_address.write_artifact(
        root, "work/aclnn_preflight.json", H._PREFLIGHT_DOMAIN, preflight)
    return spec, caseset


class SourcePathGateTest(unittest.TestCase):
    """`local_checkout` 在本门是**显式挂账的 BLOCK**，不是「大概能跑」。

    `aclnn_adapter` 只能按 PR ref 在容器内重新取源 build，构建端没有可与
    `local_checkout.root_digest` 对账的锚——放行等于让 vendor `.so` 与被测字节
    失去机器可核的对应关系。
    """

    def test_run_gate_blocks_local_before_touching_real_machine_config(self):
        with tempfile.TemporaryDirectory() as root:
            _write_gate_root(root, _LOCAL_BINDINGS)
            with mock.patch.dict(
                    os.environ, {"OPRUNWAY_ACLNN_REAL": "1"}), \
                    mock.patch.object(
                        H.aclnn_adapter, "_aclnn_cfg",
                        side_effect=AssertionError(
                            "本地通路不得走到 _aclnn_cfg()")):
                with self.assertRaisesRegex(
                        ValueError, "尚未接入 dut_source=local_checkout"):
                    H.run_gate(
                        root, "spec.json", "caseset.json",
                        "work/aclnn_preflight.json",
                        "work/aclnn_harness_trust.json")

    def test_validate_receipt_blocks_local_before_touching_real_machine_config(self):
        with tempfile.TemporaryDirectory() as root:
            spec, caseset = _write_gate_root(root, _LOCAL_BINDINGS)
            content_address.write_artifact(
                root, "work/aclnn_harness_trust.json", H._TRUST_DOMAIN, {
                    "schema": H._SCHEMA,
                    "schema_version": 1,
                    "status": H._STATUS_TRUSTED,
                    "scope": "harness-only",
                    "acceptance_verdict": None,
                    "bindings": {},
                    "coverage": {},
                    "checks": [],
                    "build_provenance": {},
                })
            with mock.patch.object(
                    H.aclnn_adapter, "_aclnn_cfg",
                    side_effect=AssertionError("本地通路不得走到 _aclnn_cfg()")):
                with self.assertRaisesRegex(
                        ValueError, "尚未接入 dut_source=local_checkout"):
                    H.validate_receipt(
                        root, "work/aclnn_harness_trust.json", spec, caseset)

    def test_build_provenance_is_itself_path_gated(self):
        # run_gate 与 validate_receipt 的共同必经点也要挡，否则两处入口门只要
        # 有人挪动顺序就整条失效。
        with self.assertRaisesRegex(
                ValueError, "尚未接入 dut_source=local_checkout"):
            H._validate_build_provenance(
                _build_provenance_fixture(), _execution_fixture(),
                {"bindings": dict(_LOCAL_BINDINGS)})

    def test_unknown_dut_source_value_is_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "受控词表"):
            H._validate_build_provenance(
                _build_provenance_fixture(), _execution_fixture(),
                {"bindings": {"dut_source": "local"}})   # 拼错

    def test_malformed_bindings_is_fail_closed(self):
        """`bindings` 形态不对 = 来源判不出来，不许 `or {}` 抹平成「缺席即 PR」。

        `dut_source.of()` 的「缺席即 pull_request」只为**旧收据的空 object** 而设；
        把 None / list / 字符串一并抹成 `{}`，等于让「根本没有来源声明」冒充
        「明确声明 pull_request」——本地通路的 preflight 只要 bindings 丢了形态，
        这道门就会当场放行。
        """
        for label, preflight in (
                ("缺席", {}),
                ("None", {"bindings": None}),
                ("list", {"bindings": []}),
                ("字符串", {"bindings": "pull_request"}),
        ):
            with self.subTest(label):
                with self.assertRaisesRegex(
                        ValueError, "bindings 缺失或不是 JSON object"):
                    H._validate_build_provenance(
                        _build_provenance_fixture(), _execution_fixture(),
                        preflight)

    def test_empty_bindings_object_still_reads_as_pull_request(self):
        """空 object 是**合法**形态，必须继续走向后兼容的「缺席即 pull_request」。

        钉住这条边界，免得下一轮把「形态硬化」顺手收紧成「bindings 必须非空」——
        那会让本门接入之前产出的旧 PR 通路收据一夜之间全部失效。
        """
        self.assertEqual(
            H._require_pull_request_path({"bindings": {}}), "pull_request")

    def test_validate_receipt_blocks_malformed_bindings_before_real_machine_config(self):
        """CP-D 复核入口是 `bindings` 的第一次触碰——门必须赶在读真机配置之前打响。"""
        with tempfile.TemporaryDirectory() as root:
            spec = {"op": "Reduce", "runner_form": "aclnn_py"}
            caseset, preflight = _fixtures()
            preflight["bindings"] = None
            os.makedirs(os.path.join(root, "work"), exist_ok=True)
            content_address.write_artifact(
                root, "work/aclnn_preflight.json", H._PREFLIGHT_DOMAIN, preflight)
            content_address.write_artifact(
                root, "work/aclnn_harness_trust.json", H._TRUST_DOMAIN, {
                    "schema": H._SCHEMA, "schema_version": 1,
                    "status": H._STATUS_TRUSTED, "scope": "harness-only",
                    "acceptance_verdict": None, "bindings": {},
                    "coverage": {}, "checks": [], "build_provenance": {},
                })
            with mock.patch.object(
                    H.aclnn_adapter, "_aclnn_cfg",
                    side_effect=AssertionError("bindings 畸形时不得走到 _aclnn_cfg()")):
                with self.assertRaisesRegex(
                        ValueError, "bindings 缺失或不是 JSON object"):
                    H.validate_receipt(
                        root, "work/aclnn_harness_trust.json", spec, caseset)

    def test_run_gate_blocks_malformed_bindings_at_the_spec_binding_check(self):
        """`run_gate` 的 spec 绑定核对排在通路门**之前**，是 `bindings` 最早的触碰点。

        它必须走同一套显式形态校验：`or {}` 之下 None / `[]` 会报「与当前 spec 不绑定」
        （fail-closed，但把「形态判不出来」说成「绑错了 spec」），非空字符串 / 非空 list
        则让 `.get` 直接抛 AttributeError——裸 traceback，不在调用方的收敛清单里。
        断言收在 ValueError 上，AttributeError 逃逸时本用例即红。
        """
        for label, bindings in (
                ("缺席", _ABSENT),
                ("None", None),
                ("空 list", []),
                ("非空 list", ["pull_request"]),
                ("字符串", "pull_request"),
        ):
            with self.subTest(label):
                with tempfile.TemporaryDirectory() as root:
                    _write_gate_root(root, bindings)
                    with mock.patch.dict(
                            os.environ, {"OPRUNWAY_ACLNN_REAL": "1"}), \
                            mock.patch.object(
                                H.aclnn_adapter, "_aclnn_cfg",
                                side_effect=AssertionError(
                                    "bindings 畸形时不得走到 _aclnn_cfg()")):
                        with self.assertRaisesRegex(
                                ValueError, "bindings 缺失或不是 JSON object"):
                            H.run_gate(
                                root, "spec.json", "caseset.json",
                                "work/aclnn_preflight.json",
                                "work/aclnn_harness_trust.json")

    def test_preflight_without_40hex_pr_head_is_blocked(self):
        """CP-C0 没绑定合法 PR head 就没有可交叉核的锚——不能靠 cfg 形态偶然兜住。"""
        with tempfile.TemporaryDirectory() as root:
            _write_gate_root(root, {"pr_head_sha": None})
            cfg = {"head_sha": None, "ops_root": "/unused/ops",
                   "op_subdir": "experimental/reduce"}
            with mock.patch.dict(
                    os.environ, {"OPRUNWAY_ACLNN_REAL": "1"}), \
                    mock.patch.object(
                        H.aclnn_adapter, "_aclnn_cfg", return_value=cfg), \
                    mock.patch.object(
                        H, "_execution_binding",
                        return_value=_execution_fixture()):
                with self.assertRaisesRegex(
                        ValueError, "未绑定 40 位 PR head"):
                    H.run_gate(
                        root, "spec.json", "caseset.json",
                        "work/aclnn_preflight.json",
                        "work/aclnn_harness_trust.json")


class WorkflowHardGateTest(unittest.TestCase):
    def test_aclnn_py_cannot_enter_adapter_without_trust_receipt(self):
        caseset, _ = _fixtures()
        with tempfile.TemporaryDirectory() as td:
            spec_path = os.path.join(td, "spec.json")
            out_dir = os.path.join(td, "report")
            with open(spec_path, "w", encoding="utf-8") as out:
                json.dump({"op": "Reduce", "runner_form": "aclnn_py"}, out)
            with mock.patch.object(
                    run_workflow.gen_cases, "gen_cases", return_value=caseset), \
                    mock.patch.dict(
                        run_workflow.repo_adapter.MODES,
                        {"aclnn_py": mock.Mock(side_effect=AssertionError(
                            "adapter 不应在信任门前启动"))},
                        clear=False):
                with self.assertRaisesRegex(SystemExit, "CP-C harness 真机信任门"):
                    # allow_experimental_form=True：aclnn_py 已被验收准入白名单
                    # （run_workflow._ACCEPTANCE_RUNNER_FORMS）挡在正式验收外，而本用例证的是
                    # **CP-C harness 信任门**——没有收据就进不了 adapter。那道门只长在 aclnn_py 上，
                    # 把夹具换成 cpp_extension 等于换掉被测对象（它走的是构建收据门，不是这道）。
                    # 不关掉准入门，run() 会先被准入门 SystemExit——同是 SystemExit，
                    # 断言的正则却再也打不到信任门，用例静默变成"测准入门"。
                    # ⚠ 这不是给准入门放水：准入门本身由
                    #   test_run_workflow_mode.py::AcceptanceFormGateTest 专测（含出口门）。
                    run_workflow.run(
                        spec_path, mode="aclnn_py", out_dir=out_dir,
                        allow_experimental_form=True)


_ACC_COMMON = os.path.dirname(os.path.abspath(H.__file__))


def _source_of(rel):
    with open(os.path.join(_ACC_COMMON, *rel.split("/")), encoding="utf-8") as src:
        return src.read()


def _first_party_rel(module_name):
    """本仓模块名 → `_LOGIC_FILES` 里那种相对路径；不是本仓模块（stdlib/三方）返回 None。"""
    parts = module_name.split(".")
    for candidate in (parts + ["__init__.py"], parts[:-1] + [parts[-1] + ".py"]):
        rel = "/".join(candidate)
        if os.path.isfile(os.path.join(_ACC_COMMON, *candidate)):
            return rel
    return None


def _direct_imports(rel):
    """AST 取某文件**直接** import（含函数体内惰性 import）的本仓模块 → 相对路径集合。

    ⚠ `from <包> import <子模块>` 必须把**子模块**也解析出来：只解析 `<包>` 会落到
    `<包>/__init__.py`，而真正执行判定的那份 `<包>/<子模块>.py` 就漏出哈希覆盖了。
    """
    found = set()
    for node in ast.walk(ast.parse(_source_of(rel))):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
        else:
            continue
        for name in names:
            target = _first_party_rel(name)
            if target is not None:
                found.add(target)
    return found


def _relative_imports(rel):
    """某文件里的相对 import（`from . import x`）源文；acc-common 是扁平布局，本不该有。

    单列出来是因为 `_direct_imports` **解析不了**它们：静默跳过 = 一条依赖悄悄不进哈希覆盖。
    """
    lines = _source_of(rel).splitlines()
    return [lines[node.lineno - 1].strip()
            for node in ast.walk(ast.parse(_source_of(rel)))
            if isinstance(node, ast.ImportFrom) and node.level]


def _module_level_bindings(rel):
    """某文件里**自己定义**的模块级名字 → 绑定次数（def/class/赋值）；**不含** import 进来的。

    「不含 import」是关键：一个从别处再导出的名字，其判定逻辑住在**另一个**文件里，
    哈希本文件覆盖不到它。
    「计次数」也是关键：`def of(): …` 后面再跟一个 `from 别处 import of`，只看「有没有
    同名 def」会把它记成本地定义，而模块最终导出的其实是别处那个。
    """
    counts = {}

    def bump(name):
        counts[name] = counts.get(name, 0) + 1

    def bump_target(target):
        """解构也要计数：`of, _ = (…)` 一样是一次模块级绑定。"""
        if isinstance(target, ast.Name):
            bump(target.id)
        elif isinstance(target, ast.Starred):
            bump_target(target.value)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                bump_target(item)

    for node in ast.parse(_source_of(rel)).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bump(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                bump_target(target)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            bump(node.target.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bump(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bump(alias.asname or alias.name)
    return counts


def _attrs_read_off(rel, alias):
    """AST 取某文件里 `alias.<attr>` 形态读到的全部属性名。"""
    return {
        node.attr for node in ast.walk(ast.parse(_source_of(rel)))
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
        and node.value.id == alias
    }


def _bare_module_references(rel, alias):
    """某文件里**不是** `alias.<attr>` 形态的 `alias` 裸引用（起别名、传参、下标…）。

    为什么单挑这一种：`ds = dut_source_kind` 之后写 `ds.identity(...)`，是一句人畜无害的
    「图省事起个短名」，却让 `_attrs_read_off` 一个字都读不到——绑定面扫描当场失明，
    判定依赖悄悄长出去也不会有人知道。这是**会被无意写出来**的形态，值得单独拦。
    """
    tree = ast.parse(_source_of(rel))
    as_attr_value = {
        id(node.value) for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
    }
    return [f"line {node.lineno}" for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id == alias
            and id(node) not in as_attr_value]


# 能绕过**静态**依赖扫描的装载/取名手段。三道 AST 检查全部建立在「读代码就能看全依赖」
# 之上，这些一出现，那个前提就没了：`importlib.import_module("dut_source")` 让被扫的文件
# 里连 `import dut_source` 都不存在，`getattr(dut_source_kind, "identity")` 让属性扫描
# 看不见那次读取。本门与内核都不需要它们（实测零处使用），所以直接禁掉是免费的。
_DYNAMIC_LOADERS = ("__import__", "eval", "exec", "getattr", "importlib")


def _dynamic_loader_calls(rel):
    """AST 取某文件里用到的动态装载/动态取名手段（含 `importlib.*` 属性调用）。"""
    found = set()
    for node in ast.walk(ast.parse(_source_of(rel))):
        name = None
        if isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            name = node.value.id
        if name in _DYNAMIC_LOADERS:
            found.add(name)
    return found


def _import_statements(rel):
    """某文件里的**全部** import 语句源文（stdlib / 三方 / 本仓一律计入）。"""
    tree = ast.parse(_source_of(rel))
    lines = _source_of(rel).splitlines()
    return [lines[node.lineno - 1].strip() for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))]


class LogicBindingCoverageTest(unittest.TestCase):
    """`_LOGIC_FILES` 必须覆盖本门的**全部**判定依赖——这几道检查是机械的，不靠人记。

    背景：本门的来源判别依赖已从聚合模块 `dut_source.py` 收窄到内核 `dut_source_kind.py`
    （前者还装着 URL 凭据策略 / build receipt 锚校验 / `source_facts.json` 查找三类与本门
    判定无关的职责，绑它 = 那三类一动就作废昂贵的真机收据）。收窄的**代价**是可能欠绑定：
    以后有人让本门用上 `dut_source` 里的东西却忘了同步 `_LOGIC_FILES`，判定依赖就脱离了
    哈希覆盖 —— 那是真 fail-open。下面几道检查专堵这个：

    ① 本门**直接 import** 的本仓模块，必须全在 `_LOGIC_FILES` 里；
    ② 本门从 `dut_source_kind` 读到的名字，必须**恰好**是钉住的那一小组，且每个都**唯一
       定义在** `dut_source_kind.py` 自己文件里（不是从某个没被哈希的模块再导出的）；
    ③ 内核**一个 import 都不许有**（stdlib / 三方也不行）；
    ④ 本门与内核都不许用动态装载/动态取名、也不许给内核模块起别名（否则 ①②③ 的静态
       扫描直接被架空）。

    ⚠ **这几道检查挡的是「无意」与「顺手」，不是「蓄意」**。真正的哈希覆盖保证来自 ③：
    内核零 import ⇒ 它导出的任何东西都只由本文件字节决定 ⇒ 逐字节哈希它 == 覆盖它的
    全部判定语义。②里那条「绑定次数恰好 1」是**多一道保险**，不是保证的来源——即便有人
    在内核里用 `if True: of = …` 这种花样重绑，那份改动**照样在被哈希的文件里**，旧收据
    照样失效；语义走样另有 `SourcePathGateTest` 的行为测试兜。
    同理，`vars(__builtins__)["__import__"]` 这类刻意隐藏依赖的写法能绕过 ④ 的黑名单——
    但同一个人也能直接改 `_LOGIC_FILES` 或删掉本测试类。**没有任何自检能防住蓄意拆自己的门**，
    那一层由 push 前审修门（人）负责。把 ④ 换成「正向 AST 白名单」并不改变这个边界，
    却会把 `_require_pull_request_path` 的实现形态钉死，正当重构一改就红。故不采纳。

    ⚠ ① 只查**直接** import，**不查传递闭包**——这是如实挂账的既有张力，不是本轮遗漏：
    `_LOGIC_FILES` 从设计上就是「判定依赖的**策展**清单」而非 import 闭包。实测本门的
    一方传递闭包是 14 个模块（经 `repo_adapter → cpp_extension_adapter` 还会拉进
    `dut_source` / `fetch_source` / `catlass_adapter` / `validate_preparation_state`），
    而 `_LOGIC_FILES` 只列 8 个模块 + 5 份 `aclnn_runtime/`。改成「闭包 ⊆ `_LOGIC_FILES`」
    会让**未改动的现有代码当场变红**，且要把清单撑到 14 个模块——那正好是本轮要削减的
    过度绑定，方向相反。残留缺口如实记：**已哈希模块再去调一个未哈希 helper**，本检查
    抓不到。要封它得先重画 `_LOGIC_FILES` 的语义（策展清单 → 闭包），属于另一个议题。
    查直接 import 抓的是「**本门自己**开始调新东西了」这一步，也就是本轮收窄真正引入的
    那个风险面；它严格优于收窄前的状态（那时一道机械检查都没有）。
    """

    _SELF = "verify_aclnn_harness.py"
    _KERNEL = "dut_source_kind.py"
    # 本门从内核读到的名字，**钉死**。这不是为了防 fail-open（内核里的东西都被哈希），
    # 而是「绑定面变了就得有人重新审一遍」的强制触发点：多读一个名字 = 本门的判定依赖
    # 变了，必须当场复核 `_LOGIC_FILES` 是否仍然覆盖得住，而不是悄悄长出去。
    _KERNEL_SURFACE = {"of", "PULL_REQUEST"}

    def test_every_first_party_module_the_gate_imports_is_hashed(self):
        # 相对 import 先拦：`_direct_imports` 解析不了它们，静默跳过 = 依赖悄悄逃出覆盖。
        self.assertEqual(
            [], _relative_imports(self._SELF),
            "本门出现了相对 import —— acc-common 是扁平布局，本检查解析不了它，"
            "放过去等于一条依赖不进哈希覆盖。请改成绝对 import。")
        missing = sorted(_direct_imports(self._SELF) - set(H._LOGIC_FILES))
        self.assertEqual(
            [], missing,
            f"本门直接 import 了 {missing}，但它们不在 _LOGIC_FILES 里 —— "
            f"这些模块的逻辑改了，旧 harness 收据照样 revalidate 通过（fail-open）。"
            f"要么把它们加进 _LOGIC_FILES，要么别在本门里用。")

    def test_the_source_discriminator_is_defined_inside_a_hashed_file(self):
        self.assertIn(self._KERNEL, H._LOGIC_FILES)
        self.assertIn(self._KERNEL, H._logic_hashes())
        used = _attrs_read_off(self._SELF, "dut_source_kind")
        # 少 = 本门没在判别来源通路（假门，`local_checkout` 静默放行）；
        # 多 = 判定依赖悄悄长出去了，必须有人重新审绑定面。两个方向都拦。
        self.assertEqual(
            self._KERNEL_SURFACE, used,
            f"本门读到的内核名字变成 {sorted(used)}（钉住的是 "
            f"{sorted(self._KERNEL_SURFACE)}）—— 少了 = 来源通路门形同虚设；"
            f"多了 = 判定面扩张，请重新审定 _LOGIC_FILES 覆盖是否仍然足够，再更新本 pin。")
        bindings = _module_level_bindings(self._KERNEL)
        bad = sorted(n for n in used if bindings.get(n) != 1)
        self.assertEqual(
            [], bad,
            f"本门读的 {bad} 在 {self._KERNEL} 里不是**唯一的本地定义**（缺失、或被后面的 "
            f"import/赋值覆盖）—— 真正导出的对象可能来自别的文件，哈希 {self._KERNEL} "
            f"覆盖不到，等于绕开绑定。")

    def test_the_discriminator_kernel_imports_nothing_at_all(self):
        """内核零 import 是「哈希这一个文件 == 覆盖全部判定语义」的前提；破了就得重算绑定面。

        ⚠ 这里**不能**只查本仓模块。`from some_pkg import normalize_kind` 之后让 `of()`
        调它，来源归类逻辑就住进了一个既不在 `_LOGIC_FILES`、版本也不被任何收据钉住的
        三方包里——升个级就能悄悄改判定，旧收据照样 revalidate 通过。stdlib 同理
        （行为随解释器版本走）。所以是**一个 import 都不许有**。
        """
        self.assertEqual(
            [], _import_statements(self._KERNEL),
            f"{self._KERNEL} 出现了 import —— 内核的判定语义不再由本文件字节唯一决定。"
            f"要么把新依赖一起纳入 _LOGIC_FILES 并重算绑定面，要么别在内核里引入依赖。")

    def test_neither_the_gate_nor_the_kernel_can_dodge_the_static_scan(self):
        """动态装载/动态取名会架空上面三道静态检查，本门与内核一律禁用。"""
        for rel in (self._SELF, self._KERNEL):
            found = sorted(_dynamic_loader_calls(rel))
            self.assertEqual(
                [], found,
                f"{rel} 用了 {found} —— 静态 AST 扫描看不见这样引入的依赖/属性读取："
                f"`importlib.import_module(\"dut_source\")` 能让依赖扫描完全落空，"
                f"`getattr(dut_source_kind, …)` 能让绑定面扫描漏读。请改回静态写法。")

    def test_the_gate_never_aliases_the_kernel_module(self):
        """内核模块只准以 `dut_source_kind.<attr>` 出现；起了别名，绑定面扫描就失明。"""
        bare = _bare_module_references(self._SELF, "dut_source_kind")
        self.assertEqual(
            [], bare,
            f"本门在 {bare} 把 `dut_source_kind` 当值用了（起别名 / 传参 / 下标）——"
            f"之后经别名读到的属性，`_attrs_read_off` 一个都看不见，钉住的绑定面形同虚设。"
            f"请一律直接写 `dut_source_kind.<名字>`。")


class ReexportIdentityTest(unittest.TestCase):
    """聚合模块 `dut_source` 必须**再导出**内核对象，而不是复制一份同名的。

    复制一份的后果很隐蔽：`dut_source.DutSourceError` 与 `dut_source_kind.DutSourceError`
    成为两个不同的类，既有 10+ 处 `except dut_source.DutSourceError` 从此漏捕内核抛的异常，
    fail-closed 的门变成裸 traceback（甚至被更外层的宽 except 吞掉）。
    """

    def test_dut_source_reexports_the_very_same_objects(self):
        import dut_source
        import dut_source_kind
        for name in ("DutSourceError", "PULL_REQUEST", "LOCAL_CHECKOUT", "ALL", "of"):
            self.assertIs(
                getattr(dut_source, name), getattr(dut_source_kind, name),
                f"dut_source.{name} 与内核不是同一个对象")

    def test_the_kernel_error_is_caught_through_the_aggregate_name(self):
        import dut_source
        with self.assertRaises(dut_source.DutSourceError):
            dut_source.of({"dut_source": "made_up"}, where="t")


if __name__ == "__main__":
    unittest.main()
