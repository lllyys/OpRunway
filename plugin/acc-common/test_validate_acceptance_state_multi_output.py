"""验收门 · 多输出契约（`expected.outputs[]` / `precision.outputs[]`）单测（契约审计 High#1）。

证明四件事：
  ① 合法多输出 caseset + evidence **过门全绿**（task1/task2 零 error）——此前门只认 legacy 顶层
     `expected.golden_path/policy/threshold`，8 条合法多输出 case 会刷出 task1 40 条 + task2 64 条错误；
  ② 篡改**某一个输出**的 policy（放宽容差）→ 逐输出三处一致门逮住；
  ③ **缺一个输出**（evidence 少报 indices / caseset 少声明）→ 逮住（跑子集下沉到输出粒度）；
  ④ legacy 单输出（现有 4 算子）**仍原路过门**——向后兼容硬约束。

跑: python3 -m unittest test_validate_acceptance_state_multi_output -v   （在 acc-common/ 下）
"""
import copy
import hashlib
import json
import os
import shutil
import tempfile
import unittest

import numpy as np

import precision_policy
import validate_acceptance_state as G

# ── 判据口径（op-中立；门只校「三处一致 + 数字确实从产物算出」，canonical 派生是 validator 的活）──────
_VAL_POL = {"kind": "torch_allclose", "rtol": 1e-3, "atol": 1e-5, "equal_nan": True}
_IDX_POL = {"kind": "index_value_consistency", "gather_from": "self",
            "value_rtol": 1e-3, "value_atol": 1e-5}
_VAL_TPID = "torch_allclose:float32"
_GOLDEN_SOURCE = "torch reference"          # → oracle_source_from_golden = "torch_ref"
_ORACLE = "torch_ref"
_OUT_ROOT = "aclnn_out"                     # aclnn_py 的产物根（相对 work_dir；evidence 里已带这一层）


def _w(d, name, obj):
    with open(os.path.join(d, name), "w", encoding="utf-8") as f:
        json.dump(obj, f)


def _sha_bytes(b):
    return hashlib.sha256(b).hexdigest()


def _outs_meta(cid, with_index):
    """caseset expected.outputs[]（by-dim case 双输出 values+indices；全局 case 单输出 values）。"""
    items = [{"role": "value", "golden_path": f"{cid}/golden_0.npy", "golden_tier": 1,
              "out_shape": [3] if with_index else [], "out_shape_source": "golden.out_shape",
              "compare": "torch_allclose", "compare_dtype": "float32",
              "standard": "torch_allclose", "tolerance_policy_id": _VAL_TPID,
              "policy": dict(_VAL_POL),
              "threshold": list(precision_policy.threshold_digest(_VAL_POL))}]
    if with_index:
        items.append({"role": "index", "golden_path": f"{cid}/golden_1.npy", "golden_tier": 1,
                      "out_shape": [3], "out_shape_source": "golden.out_shape",
                      "compare": "index_value_consistency", "compare_dtype": "int64",
                      "standard": "torch_allclose", "tolerance_policy_id": None,
                      "policy": dict(_IDX_POL),
                      "threshold": list(precision_policy.threshold_digest(_IDX_POL)),
                      "index_of": "values"})
    return items


def _make_caseset():
    """两条 case：m_000 = by-dim 双输出（3×4 沿 dim=1 归约）；m_001 = 全局单输出（0-d）。"""
    cases = []
    for cid, attrs, with_index, shape in (("m_000", {"dim": 1, "keepdim": False}, True, [3, 4]),
                                          ("m_001", {"dim": None, "keepdim": False}, False, [4])):
        cases.append({
            "id": cid, "dims": ["功能", "精度"], "tags": ["功能"],
            "inputs": [{"name": "self", "shape": shape, "dtype": "float32", "path": f"{cid}/x1.npy"}],
            "attrs": attrs,
            "expected": {"golden_source": _GOLDEN_SOURCE, "golden_tier": 1, "verify_mode": "numerical",
                         "outputs": _outs_meta(cid, with_index),
                         "case_origin": "test", "rule_ref": "test"},
        })
    return {"op": "M", "cases": cases}


def _materialize(d, caseset):
    """落盘产物并据真实字节组 evidence：
      · `<d>/work/<cid>/x1.npy`（输入，index gather 源）+ `golden_k.npy`
      · `<d>/work/aclnn_out/<cid>/out_k.bin`（driver 扁平 dump，out == golden → 完美一致）
    """
    ev = []
    for c in caseset["cases"]:
        cid = c["id"]
        cdir = os.path.join(d, "work", cid)
        odir = os.path.join(d, "work", _OUT_ROOT, cid)
        os.makedirs(cdir, exist_ok=True)
        os.makedirs(odir, exist_ok=True)
        shape = c["inputs"][0]["shape"]
        src = np.arange(int(np.prod(shape)), dtype=np.float32).reshape(shape) * 0.5
        np.save(os.path.join(cdir, "x1.npy"), src)
        outs_meta = c["expected"]["outputs"]
        by_dim = len(outs_meta) == 2
        if by_dim:
            idx = np.argmax(src, axis=1).astype(np.int64)
            goldens = [np.take_along_axis(src, idx[:, None], axis=1).reshape(3), idx]
        else:
            goldens = [np.asarray(src.max(), dtype=np.float32)]
        ev_outs = []
        for k, (g, meta) in enumerate(zip(goldens, outs_meta)):
            gp = os.path.join(cdir, f"golden_{k}.npy")
            np.save(gp, g)
            flat = np.ascontiguousarray(g)              # driver 扁平 dump（0-d → (1,)，与真实通路同）
            blob = flat.tobytes()
            with open(os.path.join(odir, f"out_{k}.bin"), "wb") as f:
                f.write(blob)
            with open(gp, "rb") as f:
                g_sha = _sha_bytes(f.read())
            kwargs = {}
            if meta["policy"]["kind"] == "index_value_consistency":
                kwargs["gather_ctx"] = {"source": src, "dim": 1, "keepdim": False}
            metrics = precision_policy.compute_metrics(
                np.asarray(flat).reshape(meta["out_shape"]), g, meta["policy"], **kwargs)
            item = {"role": meta["role"], "standard": meta["standard"],
                    "tolerance_policy_id": meta["tolerance_policy_id"],
                    "policy": copy.deepcopy(meta["policy"]), "threshold": copy.deepcopy(meta["threshold"]),
                    "metrics": metrics, "golden_path": meta["golden_path"],
                    "out_path": f"{_OUT_ROOT}/{cid}/out_{k}.bin",
                    "out_dtype": str(flat.dtype), "out_shape": list(flat.shape),
                    "provenance": {"golden_sha256": g_sha, "out_sha256": _sha_bytes(blob),
                                   "numel": int(np.asarray(g).size)}}
            if meta.get("index_of") is not None:
                item["index_of"] = meta["index_of"]
            ev_outs.append(item)
        ev.append({"case_id": cid, "status": "ok",
                   "precision": {"outputs": ev_outs, "oracle_source": _ORACLE, "not_settled": False},
                   "perf": {"scope": "kernel_only", "us": None}})
    return {"op": "M", "runner_form": "aclnn_py", "evidence": ev}


_VERDICT = {"op": "M", "overall": {"verdict": "pass",
                                   "counts": {"fail": 0, "uncertain": 0, "contract_problems": 0}}}


class MultiOutputGateTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="mo_gate_")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.cs = _make_caseset()
        self.ev = _materialize(self.d, self.cs)

    def _write(self, cs=None, ev=None):
        _w(self.d, "caseset.json", cs if cs is not None else self.cs)
        _w(self.d, "evidence.json", ev if ev is not None else self.ev)
        _w(self.d, "verdict.json", _VERDICT)

    def _errs(self, stage, cs=None, ev=None):
        self._write(cs, ev)
        errs = []
        G._GATES[stage](self.d, errs)
        return errs

    # ── ① 合法多输出：task1/task2 全绿 ──────────────────────────────────────────────
    def test_task1_multi_output_passes(self):
        self.assertEqual(self._errs("task1"), [])

    def test_task2_multi_output_passes(self):
        self.assertEqual(self._errs("task2"), [])

    def test_task2_index_output_metrics_are_recomputed_by_gate(self):
        """index 输出确实被门 gather 重算过：篡改 evidence 自报的 index metrics → 被逮。"""
        ev = copy.deepcopy(self.ev)
        idx_out = next(o for o in ev["evidence"][0]["precision"]["outputs"] if o["role"] == "index")
        idx_out["metrics"]["mismatch"] = 0 if idx_out["metrics"]["mismatch"] else 7
        errs = self._errs("task2", ev=ev)
        self.assertTrue(any("mismatch" in e for e in errs), errs)

    def test_task2_tampered_out_bin_bytes_caught(self):
        """改 out_1.bin（index）字节而 provenance 未同改 → sha256 不符被逮（.bin 也走 provenance 绑定）。"""
        p = os.path.join(self.d, "work", _OUT_ROOT, "m_000", "out_1.bin")
        with open(p, "r+b") as f:
            f.write(b"\x07")
        errs = self._errs("task2")
        self.assertTrue(any("sha256" in e for e in errs), errs)

    # ── ② 篡改某一个输出的 policy（放宽）→ 逐输出三处一致门逮住 ───────────────────────
    def test_task2_relaxed_policy_on_one_output_caught(self):
        ev = copy.deepcopy(self.ev)
        ev["evidence"][0]["precision"]["outputs"][0]["policy"]["rtol"] = 1.0   # 放宽 1000 倍
        errs = self._errs("task2", ev=ev)
        self.assertTrue(any("policy" in e and "防放宽" in e for e in errs), errs)

    def test_task2_relaxed_threshold_on_index_output_caught(self):
        ev = copy.deepcopy(self.ev)
        ev["evidence"][0]["precision"]["outputs"][1]["threshold"] = [1.0, 1.0]
        errs = self._errs("task2", ev=ev)
        self.assertTrue(any("threshold" in e for e in errs), errs)

    def test_task2_output_reorder_caught(self):
        """输出换序（values/indices 对调）→ role 不一致被逮（张冠李戴）。"""
        ev = copy.deepcopy(self.ev)
        ev["evidence"][0]["precision"]["outputs"].reverse()
        errs = self._errs("task2", ev=ev)
        self.assertTrue(any("role 不一致" in e for e in errs), errs)

    # ── ③ 缺一个输出 → fail-closed ────────────────────────────────────────────────
    def test_task2_missing_one_evidence_output_caught(self):
        ev = copy.deepcopy(self.ev)
        ev["evidence"][0]["precision"]["outputs"].pop()      # 少报 indices
        errs = self._errs("task2", ev=ev)
        self.assertTrue(any("长度" in e for e in errs), errs)

    def test_task2_empty_evidence_outputs_caught(self):
        ev = copy.deepcopy(self.ev)
        ev["evidence"][0]["precision"]["outputs"] = []
        errs = self._errs("task2", ev=ev)
        self.assertTrue(any("precision.outputs" in e for e in errs), errs)

    def test_task1_missing_output_field_caught(self):
        cs = copy.deepcopy(self.cs)
        del cs["cases"][0]["expected"]["outputs"][1]["policy"]
        errs = self._errs("task1", cs=cs)
        self.assertTrue(any("缺 expected.policy" in e for e in errs), errs)

    def test_task1_index_without_value_output_caught(self):
        cs = copy.deepcopy(self.cs)
        cs["cases"][0]["expected"]["outputs"] = [cs["cases"][0]["expected"]["outputs"][1]]
        errs = self._errs("task1", cs=cs)
        self.assertTrue(any("无被引的 value 输出" in e for e in errs), errs)

    def test_task1_duplicate_role_caught(self):
        cs = copy.deepcopy(self.cs)
        cs["cases"][0]["expected"]["outputs"][1]["role"] = "value"
        errs = self._errs("task1", cs=cs)
        self.assertTrue(any("role 重复" in e for e in errs), errs)

    def test_task1_outputs_and_legacy_fields_coexist_caught(self):
        cs = copy.deepcopy(self.cs)
        cs["cases"][0]["expected"]["golden_path"] = "m_000/golden_0.npy"
        errs = self._errs("task1", cs=cs)
        self.assertTrue(any("并存" in e for e in errs), errs)

    def test_task1_empty_outputs_list_caught(self):
        cs = copy.deepcopy(self.cs)
        cs["cases"][0]["expected"]["outputs"] = []
        errs = self._errs("task1", cs=cs)
        self.assertTrue(any("outputs 非列表或为空" in e for e in errs), errs)

    # ── 产物 provenance：路径逃逸 / 缺字段 / 字节数对不上 ──────────────────────────
    def test_task2_out_path_escape_caught(self):
        ev = copy.deepcopy(self.ev)
        ev["evidence"][0]["precision"]["outputs"][0]["out_path"] = "../../etc/passwd"
        errs = self._errs("task2", ev=ev)
        self.assertTrue(any("路径逃逸" in e for e in errs), errs)

    def test_task2_missing_provenance_caught(self):
        ev = copy.deepcopy(self.ev)
        del ev["evidence"][0]["precision"]["outputs"][0]["provenance"]
        errs = self._errs("task2", ev=ev)
        self.assertTrue(any("provenance" in e for e in errs), errs)

    def test_task2_wrong_out_dtype_caught(self):
        """自报 out_dtype 与磁盘字节数对不上 → 拒（不许随口改 dtype 重新解释字节）。"""
        ev = copy.deepcopy(self.ev)
        ev["evidence"][0]["precision"]["outputs"][0]["out_dtype"] = "float64"
        errs = self._errs("task2", ev=ev)
        self.assertTrue(any("字节数" in e for e in errs), errs)

    def test_task2_missing_golden_product_caught(self):
        os.remove(os.path.join(self.d, "work", "m_000", "golden_1.npy"))
        errs = self._errs("task2")
        self.assertTrue(any("golden 产物缺失" in e for e in errs), errs)


class AdapterEvidenceFeedsGateTest(unittest.TestCase):
    """闭环：采集层 `repo_adapter.build_multi_output_evidence` 产的 evidence **原样**喂给门 → 全绿。

    专治契约审计 High#1 的另一半：`out_path` 必须能被门按 `<reports>/work` 这一根解析（adapter 侧已把
    out 根算进相对路径），且 `.bin` 的 dtype/shape 随 evidence 一起落下来，门才读得回二进制。
    不经 gen_cases（用手搓 caseset + out_manifest），避免与用例生成侧的并行改动耦合。"""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="mo_adapter_")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.work = os.path.join(self.d, "work")
        self.out_dir = os.path.join(self.work, _OUT_ROOT)

    def _build(self):
        import repo_adapter
        cid = "m_000"
        cdir, odir = os.path.join(self.work, cid), os.path.join(self.out_dir, cid)
        os.makedirs(cdir, exist_ok=True)
        os.makedirs(odir, exist_ok=True)
        src = (np.arange(12, dtype=np.float32).reshape(3, 4) * 0.25)
        np.save(os.path.join(cdir, "x1.npy"), src)
        idx = np.argmax(src, axis=1).astype(np.int64)
        vals = np.take_along_axis(src, idx[:, None], axis=1).reshape(3)
        produced = []
        for k, arr in enumerate((vals, idx)):
            np.save(os.path.join(cdir, f"golden_{k}.npy"), arr)
            flat = np.ascontiguousarray(arr)
            with open(os.path.join(odir, f"out_{k}.bin"), "wb") as f:
                f.write(flat.tobytes())
            produced.append({"index": k, "role": ("value" if k == 0 else "index"),
                             "path": f"{cid}/out_{k}.bin", "shape": list(flat.shape),
                             "dtype": str(flat.dtype), "nbytes": int(flat.nbytes)})
        with open(os.path.join(self.out_dir, "out_manifest.json"), "w", encoding="utf-8") as f:
            json.dump({"op": "M", "produced": [{"case_id": cid, "outputs": produced}]}, f)
        caseset = {"op": "M", "cases": [{
            "id": cid, "dims": ["功能", "精度"], "tags": ["功能"],
            "inputs": [{"name": "self", "shape": [3, 4], "dtype": "float32", "path": f"{cid}/x1.npy"}],
            "attrs": {"dim": 1, "keepdim": False},
            "expected": {"golden_source": _GOLDEN_SOURCE, "golden_tier": 1, "verify_mode": "numerical",
                         "case_origin": "test", "rule_ref": "test",
                         "outputs": _outs_meta(cid, True)}}]}
        ev = repo_adapter.build_multi_output_evidence(caseset, self.work, self.out_dir)
        return caseset, {"op": "M", "runner_form": "aclnn_py", "evidence": ev}

    def test_adapter_evidence_passes_gate(self):
        caseset, envelope = self._build()
        # out_path 必须是**相对 work_dir**（带上 aclnn_out 这一层），否则门找不到产物
        op0 = envelope["evidence"][0]["precision"]["outputs"][0]["out_path"]
        self.assertTrue(op0.startswith(_OUT_ROOT + "/"), op0)
        self.assertEqual(envelope["evidence"][0]["precision"]["outputs"][0]["out_dtype"], "float32")
        self.assertEqual(envelope["evidence"][0]["precision"]["outputs"][1]["out_dtype"], "int64")
        _w(self.d, "caseset.json", caseset)
        _w(self.d, "evidence.json", envelope)
        _w(self.d, "verdict.json", _VERDICT)
        for stage in ("task1", "task2"):
            errs = []
            G._GATES[stage](self.d, errs)
            self.assertEqual(errs, [], stage)


class LegacySingleOutputUntouchedTest(unittest.TestCase):
    """向后兼容硬约束：单输出 legacy 结构走**原路径**、判定链零变更（现有 4 算子不能破）。"""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="legacy_gate_")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def test_legacy_single_output_still_passes(self):
        import test_validate_acceptance_state as T
        _w(self.d, "caseset.json", T.CASESET)
        _w(self.d, "evidence.json", T._ev(self.d, ["x_000", "x_001"]))
        _w(self.d, "verdict.json", T._vd("pass"))
        for stage in ("task1", "task2"):
            errs = []
            G._GATES[stage](self.d, errs)
            self.assertEqual(errs, [], stage)

    def test_legacy_missing_golden_path_still_caught(self):
        import test_validate_acceptance_state as T
        cs = copy.deepcopy(T.CASESET)
        del cs["cases"][0]["expected"]["golden_path"]
        _w(self.d, "caseset.json", cs)
        errs = []
        G._GATES["task1"](self.d, errs)
        self.assertTrue(any("无 golden_path" in e for e in errs), errs)


if __name__ == "__main__":
    unittest.main()
