#!/usr/bin/env python3
"""aclnn_py harness 信任门的纯确定性单测；不 build、不访问 NPU。"""

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

    def test_dut_source_logic_is_bound_by_the_receipt(self):
        # 判别式是本门的判定依赖；不进 logic_files 就意味着「把 of() 改成未知取值
        # 缺省 pull_request」之后，旧收据照样 revalidate 通过。
        self.assertIn("dut_source.py", H._LOGIC_FILES)
        self.assertIn("dut_source.py", H._logic_hashes())

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


if __name__ == "__main__":
    unittest.main()
