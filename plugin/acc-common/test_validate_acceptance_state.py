"""验收门单测（stdlib unittest）——核心证明：门能挡住「跑子集报 100%」与 fail/blocked。

跑: python3 -m unittest test_validate_acceptance_state -v   （在 acc-common/ 下）
"""
import copy
import json, os, subprocess, sys, tempfile, shutil, unittest
import numpy as np
import precision_policy
import validate_acceptance_state as G
import validator as V              # C1 输出形状对账 / C4 dtype 冲突裁决在 validator，与本门配套钉死
import _golden_fixture as _gf
setUpModule = _gf.install        # golden 去引擎化：gen_cases/run_workflow 需 <ops_root>/<op>/golden.py（ADR 0011）
tearDownModule = _gf.uninstall


def _w(d, name, obj):
    with open(os.path.join(d, name), "w", encoding="utf-8") as f:
        json.dump(obj, f)


# T5：结构化口径（standard + tolerance_policy_id + 结构化 policy + threshold digest）
_POL32 = {"kind": "ascendoptest_default", "tolerance": 0.0001, "error_rate": 0.0001,
          "eps": 1e-9, "legacy": 0.1, "not_settled": False}
_POL16 = {"kind": "ascendoptest_default", "tolerance": 0.001, "error_rate": 0.001,
          "eps": 1e-9, "legacy": 0.1, "not_settled": False}
# golden_source="numpy reference" → oracle_source_from_golden 首 token=numpy → "analytical_ref"（Q9 门校两侧须一致）。
_EXP = {
    "x_000": {"golden_path": "g0.npy", "threshold": 0.0001, "standard": "ascendoptest_default",
              "tolerance_policy_id": "ascendoptest_default:float32", "policy": _POL32,
              "golden_source": "numpy reference"},
    "x_001": {"golden_path": "g1.npy", "threshold": 0.001, "standard": "ascendoptest_default",
              "tolerance_policy_id": "ascendoptest_default:float16", "policy": _POL16,
              "golden_source": "numpy reference"},
}
CASESET = {"op": "X", "cases": [
    {"id": "x_000", "dims": ["func"], "inputs": [{"name": "a", "shape": [16], "dtype": "float32"}],
     "expected": dict(_EXP["x_000"])},
    {"id": "x_001", "dims": ["func"], "inputs": [{"name": "a", "shape": [16], "dtype": "float16"}],
     "expected": dict(_EXP["x_001"])},
]}


# A 方案：gate_task2 现按 provenance 读磁盘产物、校 sha、依 caseset policy 重算 metrics 并比对。
# 故 _ev 须**真落盘** golden.npy/out.npy 到 <d>/work/<cid>/（门解析根），并以真实重算值 + sha256 构 evidence。
_DT = {"x_000": np.float32, "x_001": np.float16}


def _mkprod(d, cid, golden, out):
    """落盘 <d>/work/<cid>/golden.npy + out.npy（门 gate_task2 从 <d>/work 解析产物）；返回 (golden_sha, out_sha)。"""
    cdir = os.path.join(d, "work", cid)
    os.makedirs(cdir, exist_ok=True)
    gp, op = os.path.join(cdir, "golden.npy"), os.path.join(cdir, "out.npy")
    np.save(gp, golden)
    np.save(op, out)
    return G._sha256(gp), G._sha256(op)


def _prec_for(d, i, corrupt_out=0):
    """构与磁盘产物自洽的 evidence.precision：out 默认 = golden 副本（完美 mock，bad_count=0）；
    corrupt_out=k → out 真有 k 个坏点（真实 bad_count≥k），metrics 用真实重算值、provenance 用真实 sha。"""
    exp = _EXP[i]
    dt = _DT[i]
    golden = np.arange(16, dtype=dt)
    out = golden.copy()
    for k in range(corrupt_out):
        out[k] = out[k] + dt(10)                       # 制造真实坏点（out ≠ golden）
    g_sha, o_sha = _mkprod(d, i, golden, out)
    metrics = precision_policy.compute_metrics(out, golden, exp["policy"])
    # Q9：oracle_source 须与 caseset.expected.golden_source(=numpy…) 映射的 analytical_ref 一致（门校）。
    return {"standard": exp["standard"], "tolerance_policy_id": exp["tolerance_policy_id"],
            "policy": dict(exp["policy"]), "threshold": exp["threshold"], "metrics": metrics,
            "oracle_source": "analytical_ref",
            "golden_path": f"{i}/golden.npy", "out_path": f"{i}/out.npy",
            "provenance": {"golden_sha256": g_sha, "out_sha256": o_sha, "numel": 16}}


def _ev(d, ids, mutate=None, corrupt=None):
    """据 caseset.expected 构与磁盘产物一致的 evidence（含 provenance + 真实重算 metrics）；
    mutate(id)->dict 覆盖单例 precision（造口径不一致）；corrupt={id:k} 让 out 真有 k 个坏点。"""
    corrupt = corrupt or {}
    out = []
    for i in ids:
        prec = _prec_for(d, i, corrupt_out=corrupt.get(i, 0))
        if mutate:
            prec.update(mutate(i))
        out.append({"case_id": i, "status": "pass", "precision": prec})
    return {"op": "X", "evidence": out}


def _vd(v, fail=0, unc=0, cp=0):
    return {"op": "X", "overall": {"verdict": v,
            "counts": {"fail": fail, "uncertain": unc, "contract_problems": cp}}}


def _pr(status, scope="kernel_only", blocked=False):
    # per_case 行带 达标/blocked，使 summary 计数与行级一致（gate_task3 现校验计数一致性）。
    return {"op": "X", "per_case": [{"case_id": "x_000", "scope": scope,
            "blocked": blocked, "达标": not blocked}],
            "summary": {"status": status, "perf_cases": 1, "达标": (0 if blocked else 1),
                        "blocked": (1 if blocked else 0)}}


def _perf_cs(ids):
    """gate_task3 per_case 对齐用的 caseset：dims 含「性能」的用例集（防跑性能子集/伪造 summary）。"""
    return {"op": "X", "cases": [
        {"id": i, "dims": ["性能"], "tags": ["性能"],
         "inputs": [{"name": "a", "shape": [1024, 1024], "dtype": "float32"}]} for i in ids]}


def _perf_ev(ids):
    """性能用例 evidence（gate_task3 对齐只核 case_id 是否有真实证据）。"""
    return {"op": "X", "evidence": [
        {"case_id": i, "status": "ok", "perf": {"us": 1.5, "scope": "kernel_only"}} for i in ids]}


class GateTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _errs(self, stage):
        errs = []
        G._GATES[stage](self.d, errs)
        return errs

    # --- task1 ---
    def test_task1_ok(self):
        _w(self.d, "caseset.json", CASESET)
        self.assertEqual(self._errs("task1"), [])

    def test_task1_missing_caseset_fails(self):
        self.assertTrue(self._errs("task1"))

    def test_task1_dup_id_fails(self):
        cs = json.loads(json.dumps(CASESET))
        cs["cases"][1]["id"] = "x_000"
        _w(self.d, "caseset.json", cs)
        self.assertTrue(any("重复" in e for e in self._errs("task1")))

    # --- task1 · Q7 dtype 覆盖门（声明且覆盖→pass / 声明未覆盖无gap→BLOCKED / 未声明→不BLOCK） ---
    def test_task1_dtype_covered_ok(self):
        """dtype_required 全被 dtype_tested 覆盖 → 门放行。"""
        cs = json.loads(json.dumps(CASESET))
        cs["dtype_required"] = ["float32", "float16"]
        cs["dtype_tested"] = ["float32", "float16"]
        _w(self.d, "caseset.json", cs)
        self.assertEqual(self._errs("task1"), [])

    def test_task1_dtype_uncovered_no_gap_fails(self):
        """任务书要 bfloat16/int32 但未实测、task_pr_gaps 无 dtype_deferred → 静默收窄 → BLOCKED。"""
        cs = json.loads(json.dumps(CASESET))
        cs["dtype_required"] = ["float32", "float16", "bfloat16", "int32"]
        cs["dtype_tested"] = ["float32", "float16"]
        cs["task_pr_gaps"] = []
        _w(self.d, "caseset.json", cs)
        self.assertTrue(any("dtype 覆盖不足" in e for e in self._errs("task1")))

    def test_task1_dtype_uncovered_with_gap_ok(self):
        """缺的 dtype 均有 task_pr_gaps.dtype_deferred 显式挂账 → 非静默收窄 → 门放行。"""
        cs = json.loads(json.dumps(CASESET))
        cs["dtype_required"] = ["float32", "float16", "bfloat16", "int32"]
        cs["dtype_tested"] = ["float32", "float16"]
        cs["task_pr_gaps"] = [{"kind": "dtype_deferred", "dtypes": ["bfloat16", "int32"],
                               "reason": "runner 未支持·Track C"}]
        _w(self.d, "caseset.json", cs)
        self.assertEqual(self._errs("task1"), [])

    def test_task1_dtype_partial_gap_fails(self):
        """缺 2 个只挂账 1 个 → 另一个仍静默收窄 → BLOCKED。"""
        cs = json.loads(json.dumps(CASESET))
        cs["dtype_required"] = ["float32", "float16", "bfloat16", "int32"]
        cs["dtype_tested"] = ["float32", "float16"]
        cs["task_pr_gaps"] = [{"kind": "dtype_deferred", "dtypes": ["bfloat16"]}]  # 漏 int32
        _w(self.d, "caseset.json", cs)
        errs = self._errs("task1")
        self.assertTrue(any("dtype 覆盖不足" in e and "int32" in e for e in errs))

    def test_task1_dtype_needs_user_not_blocked(self):
        """dtype_required=needs_user（全集未知）→ 覆盖门未行使、不 BLOCK（不谎报也不硬崩）。"""
        cs = json.loads(json.dumps(CASESET))
        cs["dtype_required"] = "needs_user"
        _w(self.d, "caseset.json", cs)
        self.assertEqual(self._errs("task1"), [])

    def test_task1_dtype_undeclared_not_blocked(self):
        """dtype_required 未声明（legacy）→ 不 BLOCK（避免一刀切炸掉现有 spec）。"""
        _w(self.d, "caseset.json", CASESET)   # 无 dtype_required 字段
        self.assertEqual(self._errs("task1"), [])

    def test_task1_dtype_tested_missing_uses_actual_cases(self):
        """新语义：dtype_tested 缺失 → 门用**真实 cases** 判覆盖（不因缺自报字段而阻塞）。
        CASESET 真实 cases = {float32, float16} 覆盖 required → 放行。"""
        cs = json.loads(json.dumps(CASESET))
        cs["dtype_required"] = ["float32", "float16"]
        # 不给 dtype_tested → 门从真实 cases 归并实测集
        _w(self.d, "caseset.json", cs)
        self.assertEqual(self._errs("task1"), [])

    def test_task1_dtype_tested_mismatch_actual_fails(self):
        """新语义（防「跑子集报全」·gate-must-check-the-effective-object）：dtype_tested 自报与真实 cases
        dtype 集不符 → error。真实 cases={float32,float16}，自报灌 4 种冒充全覆盖 → 门抓不符。"""
        cs = json.loads(json.dumps(CASESET))
        cs["dtype_required"] = ["float32", "float16", "bfloat16", "int32"]
        cs["dtype_tested"] = ["float32", "float16", "bfloat16", "int32"]   # 灌满冒充全覆盖
        _w(self.d, "caseset.json", cs)
        self.assertTrue(any("dtype_tested" in e and "不符" in e for e in self._errs("task1")))

    def test_task1_dtype_bad_type_errors_not_crash(self):
        """抗坏输入（codex#1）：case 的 dtype 为非字符串(dict) → 记 error、不 TypeError 崩（canon 抗坏输入门不崩）。"""
        cs = json.loads(json.dumps(CASESET))
        cs["cases"][0]["inputs"][0]["dtype"] = {"bad": 1}
        cs["dtype_required"] = ["float32", "float16"]
        _w(self.d, "caseset.json", cs)
        self.assertTrue(any("非字符串" in e for e in self._errs("task1")))

    def test_task1_delete_required_still_crosschecks_tested(self):
        """codex#2：删掉 dtype_required 不能同时绕过 dtype_tested 对账——tested 自报与真实不符仍 error。"""
        cs = json.loads(json.dumps(CASESET))
        cs.pop("dtype_required", None)                                     # 无 required（legacy 宽容）
        cs["dtype_tested"] = ["float32", "float16", "bfloat16", "int32"]   # 但灌满冒充，真实=fp32/fp16
        _w(self.d, "caseset.json", cs)
        self.assertTrue(any("不符" in e for e in self._errs("task1")))

    # --- task2 (核心：防跑子集) ---
    def test_task2_full_ok(self):
        _w(self.d, "caseset.json", CASESET)
        _w(self.d, "evidence.json", _ev(self.d, ["x_000", "x_001"]))
        _w(self.d, "verdict.json", _vd("pass"))
        self.assertEqual(self._errs("task2"), [])

    def test_task2_subset_fails(self):
        """跑子集报 100%：caseset 2 例、evidence 只 1 例 → 必 FAIL。"""
        _w(self.d, "caseset.json", CASESET)
        _w(self.d, "evidence.json", _ev(self.d, ["x_000"]))
        _w(self.d, "verdict.json", _vd("pass"))
        self.assertTrue(any("跑子集" in e for e in self._errs("task2")))

    def test_task2_legit_fail_not_blocked(self):
        """合法精度 fail（证据完整）→ 门不挡：真因由 verdict 表达，不该被门盖成 BLOCKED。"""
        _w(self.d, "caseset.json", CASESET)
        _w(self.d, "evidence.json", _ev(self.d, ["x_000", "x_001"]))
        _w(self.d, "verdict.json", _vd("fail", fail=1))
        self.assertEqual(self._errs("task2"), [])

    def test_task2_needs_review_not_blocked(self):
        _w(self.d, "caseset.json", CASESET)
        _w(self.d, "evidence.json", _ev(self.d, ["x_000", "x_001"]))
        _w(self.d, "verdict.json", _vd("needs_review", unc=1))
        self.assertEqual(self._errs("task2"), [])

    def test_task2_contract_problems_fails(self):
        """validator 标契约破损 → 证据不可信 → 门挡。"""
        _w(self.d, "caseset.json", CASESET)
        _w(self.d, "evidence.json", _ev(self.d, ["x_000", "x_001"]))
        _w(self.d, "verdict.json", _vd("pass", cp=2))
        self.assertTrue(any("契约" in e for e in self._errs("task2")))

    def test_task2_threshold_mismatch_fails(self):
        """adapter 偷偷放宽阈值 digest → 必 FAIL。"""
        _w(self.d, "caseset.json", CASESET)
        _w(self.d, "evidence.json", _ev(self.d, ["x_000", "x_001"],
                                        mutate=lambda i: {"threshold": 0.1}))
        _w(self.d, "verdict.json", _vd("pass"))
        self.assertTrue(any("防放宽" in e and "threshold" in e for e in self._errs("task2")))

    def test_task2_policy_mismatch_fails(self):
        """三处不一致：evidence 结构化 policy 被放宽（error_rate 抬高）→ 必 FAIL。"""
        _w(self.d, "caseset.json", CASESET)
        looser = {"kind": "ascendoptest_default", "tolerance": 0.0001, "error_rate": 0.5,
                  "eps": 1e-9, "legacy": 0.1, "not_settled": False}
        _w(self.d, "evidence.json", _ev(self.d, ["x_000", "x_001"],
                                        mutate=lambda i: {"policy": looser} if i == "x_000" else {}))
        _w(self.d, "verdict.json", _vd("pass"))
        self.assertTrue(any("policy" in e and "防放宽" in e for e in self._errs("task2")))

    def test_task2_missing_tolerance_policy_id_fails(self):
        """evidence 缺 tolerance_policy_id（口径不可追溯）→ 必 FAIL。"""
        _w(self.d, "caseset.json", CASESET)
        ev = _ev(self.d, ["x_000", "x_001"])
        del ev["evidence"][0]["precision"]["tolerance_policy_id"]
        _w(self.d, "evidence.json", ev)
        _w(self.d, "verdict.json", _vd("pass"))
        self.assertTrue(any("tolerance_policy_id" in e for e in self._errs("task2")))

    def test_task2_caseset_missing_tolerance_policy_id_fails(self):
        """finding #12/#16：caseset expected 缺 tolerance_policy_id → 三处一致门失效 → 必 FAIL。"""
        cs = json.loads(json.dumps(CASESET))
        del cs["cases"][0]["expected"]["tolerance_policy_id"]
        _w(self.d, "caseset.json", cs)
        _w(self.d, "evidence.json", _ev(self.d, ["x_000", "x_001"]))
        _w(self.d, "verdict.json", _vd("pass"))
        self.assertTrue(any("caseset expected 缺 tolerance_policy_id" in e for e in self._errs("task2")))

    def test_task2_caseset_missing_policy_fails(self):
        """finding #12/#16：caseset expected 缺结构化 policy → 必 FAIL。"""
        cs = json.loads(json.dumps(CASESET))
        del cs["cases"][0]["expected"]["policy"]
        _w(self.d, "caseset.json", cs)
        _w(self.d, "evidence.json", _ev(self.d, ["x_000", "x_001"]))
        _w(self.d, "verdict.json", _vd("pass"))
        self.assertTrue(any("caseset expected 缺 policy" in e for e in self._errs("task2")))

    def test_task2_three_way_inconsistent_fails(self):
        """finding #12/#16：三处不一致（evidence policy 放宽 error_rate）→ 必 FAIL。"""
        _w(self.d, "caseset.json", CASESET)
        looser = dict(_POL32); looser["error_rate"] = 0.9
        _w(self.d, "evidence.json", _ev(self.d, ["x_000", "x_001"],
                                        mutate=lambda i: {"policy": looser} if i == "x_000" else {}))
        _w(self.d, "verdict.json", _vd("pass"))
        self.assertTrue(any("防放宽" in e and "policy" in e for e in self._errs("task2")))

    def test_task2_bad_verdict_enum_fails(self):
        """finding #14：overall.verdict 非合法枚举 → 必 FAIL。"""
        _w(self.d, "caseset.json", CASESET)
        _w(self.d, "evidence.json", _ev(self.d, ["x_000", "x_001"]))
        _w(self.d, "verdict.json", {"op": "X", "overall": {"verdict": "weird",
            "counts": {"fail": 0, "uncertain": 0, "contract_problems": 0}}})
        self.assertTrue(any("非法" in e for e in self._errs("task2")))

    def test_task2_counts_non_int_fails(self):
        """finding #14：counts.fail 非整数 → 必 FAIL。"""
        _w(self.d, "caseset.json", CASESET)
        _w(self.d, "evidence.json", _ev(self.d, ["x_000", "x_001"]))
        _w(self.d, "verdict.json", {"op": "X", "overall": {"verdict": "pass",
            "counts": {"fail": "0", "uncertain": 0, "contract_problems": 0}}})
        self.assertTrue(any("非整数" in e for e in self._errs("task2")))

    def test_task2_nonlist_cases_fails(self):
        """finding #13：cases 非列表 → 直接 FAILED（不静默兜成空列表放过）。"""
        _w(self.d, "caseset.json", {"op": "X", "cases": None})
        _w(self.d, "evidence.json", _ev(self.d, ["x_000", "x_001"]))
        _w(self.d, "verdict.json", _vd("pass"))
        self.assertTrue(any("非列表" in e for e in self._errs("task2")))

    # --- task2 · A 方案 evidence↔产物 provenance 绑定负例（钉死）---
    def test_task2_provenance_forged_bad_count_recompute_fails(self):
        """篡改 evidence.metrics.bad_count=0 而产物真有坏点 → 依 caseset policy 重算不符 → FAILED。"""
        _w(self.d, "caseset.json", CASESET)
        ev = _ev(self.d, ["x_000", "x_001"], corrupt={"x_000": 1})  # 产物真有 1 坏点（真实 bad_count≥1）
        ev["evidence"][0]["precision"]["metrics"]["bad_count"] = 0   # 伪造自报 bad_count=0
        _w(self.d, "evidence.json", ev)
        _w(self.d, "verdict.json", _vd("pass"))
        self.assertTrue(any("bad_count" in e and "重算" in e for e in self._errs("task2")))

    def test_task2_provenance_tampered_out_bytes_sha_fails(self):
        """篡改 out.npy 磁盘字节而 provenance.sha/metrics 不动 → sha 不符 → FAILED（重算前先挡）。"""
        _w(self.d, "caseset.json", CASESET)
        _w(self.d, "evidence.json", _ev(self.d, ["x_000", "x_001"]))
        _w(self.d, "verdict.json", _vd("pass"))
        p = os.path.join(self.d, "work", "x_000", "out.npy")
        a = np.load(p); a[0] = a[0] + np.float32(9); np.save(p, a)   # 改字节、不更新 provenance
        self.assertTrue(any("sha256" in e and "篡改" in e for e in self._errs("task2")))

    def test_task2_provenance_selfconsistent_forgery_passes_known_boundary(self):
        """⚠ A 的**已知边界**（诚实钉死，不假装防住）：自洽伪造——攻击者不跑 NPU，把 out 写成 golden 的副本，
        provenance.sha 与 metrics 全部自洽 → bad_count=0 是「真的」（确从产物算出），门**放行**。
        A 只绑定「metrics↔产物」，**不绑定**「产物↔一次真 NPU 跑测」；后者须 OPRUNWAY_DONE 哨兵 /
        raw log hash / msprof 输出绑定（本轮不做）。故此处必然放行，断言之以固化边界。"""
        _w(self.d, "caseset.json", CASESET)
        _w(self.d, "evidence.json", _ev(self.d, ["x_000", "x_001"]))  # out=golden 副本、sha/metrics 全自洽
        _w(self.d, "verdict.json", _vd("pass"))
        self.assertEqual(self._errs("task2"), [])   # 门放行 —— 已知边界，非「已防伪造」

    def test_task2_provenance_deleted_golden_fails(self):
        """删除 golden.npy → 产物缺失 → FAILED（mock 也不放宽）。"""
        _w(self.d, "caseset.json", CASESET)
        _w(self.d, "evidence.json", _ev(self.d, ["x_000", "x_001"]))
        _w(self.d, "verdict.json", _vd("pass"))
        os.remove(os.path.join(self.d, "work", "x_000", "golden.npy"))
        self.assertTrue(any("golden 产物缺失" in e for e in self._errs("task2")))

    def test_task2_provenance_deleted_out_fails(self):
        """删除 out.npy → 产物缺失 → FAILED。"""
        _w(self.d, "caseset.json", CASESET)
        _w(self.d, "evidence.json", _ev(self.d, ["x_000", "x_001"]))
        _w(self.d, "verdict.json", _vd("pass"))
        os.remove(os.path.join(self.d, "work", "x_001", "out.npy"))
        self.assertTrue(any("out 产物缺失" in e for e in self._errs("task2")))

    def test_task2_provenance_missing_field_fails(self):
        """provenance 整体缺失 → FAILED（无从校验 metrics 真伪）。"""
        _w(self.d, "caseset.json", CASESET)
        ev = _ev(self.d, ["x_000", "x_001"])
        del ev["evidence"][0]["precision"]["provenance"]
        _w(self.d, "evidence.json", ev)
        _w(self.d, "verdict.json", _vd("pass"))
        self.assertTrue(any("缺 provenance" in e for e in self._errs("task2")))

    def test_task2_provenance_numpy_unavailable_fails_not_skip(self):
        """模拟 numpy 不可用（sys.modules['numpy']=None 令 import 抛 ImportError）→ 门 FAILED、
        **不是静默 skip**（否则留「删掉 numpy 即绕过」后门）。"""
        _w(self.d, "caseset.json", CASESET)
        _w(self.d, "evidence.json", _ev(self.d, ["x_000", "x_001"]))
        _w(self.d, "verdict.json", _vd("pass"))
        saved = sys.modules.get("numpy", np)
        sys.modules["numpy"] = None
        try:
            errs = self._errs("task2")
        finally:
            sys.modules["numpy"] = saved
        self.assertTrue(any("numpy" in e and "FAILED" in e for e in errs))

    def test_task2_provenance_path_escape_dotdot_fails(self):
        """pv-1：evidence.out_path 含 `..`（realpath 到 <d> 内、work 外的文件）→ 路径逃逸 → FAILED。
        旧洞：`_pinned_product` base 误用 realpath(<d>)（非 <d>/work）→ `../evil.npy` realpath 到 <d>/evil.npy，
        commonpath([<d>,<d>/evil.npy])==<d> 通过 → 读到 work 外文件。修后：显式拒 `..` + 根落 <d>/work。"""
        _w(self.d, "caseset.json", CASESET)
        # 攻击者在 <d> 内、work 外放 evil.npy（内容与合法 out 同 → sha 会自洽，旧洞下会被读进重算）
        np.save(os.path.join(self.d, "evil.npy"), np.arange(16, dtype=np.float32))
        ev = _ev(self.d, ["x_000", "x_001"],
                 mutate=lambda i: {"out_path": "../evil.npy"} if i == "x_000" else {})
        _w(self.d, "evidence.json", ev)
        _w(self.d, "verdict.json", _vd("pass"))
        errs = self._errs("task2")
        self.assertTrue(any("x_000" in e and "逃逸" in e for e in errs))

    def test_task2_provenance_path_escape_golden_dotdot_fails(self):
        """pv-1 姊妹：golden_path 含 `..` 亦 FAILED（out/golden 两侧都钉死在 <d>/work）。"""
        _w(self.d, "caseset.json", CASESET)
        np.save(os.path.join(self.d, "evil_g.npy"), np.arange(16, dtype=np.float32))
        ev = _ev(self.d, ["x_000", "x_001"],
                 mutate=lambda i: {"golden_path": "x_000/../../evil_g.npy"} if i == "x_000" else {})
        _w(self.d, "evidence.json", ev)
        _w(self.d, "verdict.json", _vd("pass"))
        errs = self._errs("task2")
        self.assertTrue(any("x_000" in e and "逃逸" in e for e in errs))

    def test_task2_provenance_broken_numpy_non_importerror_fails_not_crash(self):
        """pv-5：破损/伪 numpy 抛**非 ImportError**（RuntimeError）→ 门判 FAILED、**不 traceback 崩溃**。
        旧洞：`_gate_precision_provenance` 只 `except ImportError` + `import precision_policy` 在 try 外 →
        非 ImportError 穿透 gate_task2→main（无 try）→ 门崩溃，违反模块「抗坏输入…绝不崩溃」契约。"""
        _w(self.d, "caseset.json", CASESET)
        _w(self.d, "evidence.json", _ev(self.d, ["x_000", "x_001"]))
        _w(self.d, "verdict.json", _vd("pass"))

        class _BoomFinder:  # 伪 numpy：re-import 时抛 RuntimeError（模拟破损 numpy）
            def find_spec(self, name, path, target=None):
                if name == "numpy":
                    raise RuntimeError("boom: 伪 numpy 抛非 ImportError")
                return None

        saved = sys.modules.pop("numpy", None)     # 摘掉已加载 numpy → 下次 import 走 meta_path
        boom = _BoomFinder()
        sys.meta_path.insert(0, boom)
        try:
            errs = self._errs("task2")             # 不应抛异常（旧代码会 traceback 崩）
        finally:
            try:
                sys.meta_path.remove(boom)
            except ValueError:
                pass
            if saved is not None:
                sys.modules["numpy"] = saved
        self.assertTrue(any("FAILED" in e for e in errs))

    # --- task2 · Q9 oracle_source 门校（防伪造 evidence 篡改 oracle_source）---
    def test_task2_oracle_source_consistent_ok(self):
        """oracle_source(analytical_ref) == 据 caseset golden_source(numpy…) 映射值 → 门放行。"""
        _w(self.d, "caseset.json", CASESET)
        _w(self.d, "evidence.json", _ev(self.d, ["x_000", "x_001"]))
        _w(self.d, "verdict.json", _vd("pass"))
        self.assertEqual(self._errs("task2"), [])   # 冗余于 full_ok，钉死一致路径不误挡

    def test_task2_oracle_source_mismatch_fails(self):
        """伪造 evidence.oracle_source=torch_ref，但 caseset golden_source=numpy(→analytical_ref) → 不符 → FAILED。"""
        _w(self.d, "caseset.json", CASESET)
        _w(self.d, "evidence.json", _ev(self.d, ["x_000", "x_001"],
                                        mutate=lambda i: {"oracle_source": "torch_ref"} if i == "x_000" else {}))
        _w(self.d, "verdict.json", _vd("pass"))
        self.assertTrue(any("oracle_source" in e and "≠" in e for e in self._errs("task2")))

    def test_task2_oracle_source_missing_fails(self):
        """evidence 缺 oracle_source → 证据不完整 → FAILED（fail-closed，不静默）。"""
        _w(self.d, "caseset.json", CASESET)
        ev = _ev(self.d, ["x_000", "x_001"])
        del ev["evidence"][0]["precision"]["oracle_source"]
        _w(self.d, "evidence.json", ev)
        _w(self.d, "verdict.json", _vd("pass"))
        self.assertTrue(any("缺 precision.oracle_source" in e for e in self._errs("task2")))

    def test_task2_oracle_source_illegal_enum_fails(self):
        """evidence.oracle_source 不属六枚举 → FAILED。"""
        _w(self.d, "caseset.json", CASESET)
        _w(self.d, "evidence.json", _ev(self.d, ["x_000", "x_001"],
                                        mutate=lambda i: {"oracle_source": "bogus_ref"} if i == "x_000" else {}))
        _w(self.d, "verdict.json", _vd("pass"))
        self.assertTrue(any("oracle_source" in e and "非法" in e for e in self._errs("task2")))

    def test_task2_caseset_missing_golden_source_fails(self):
        """caseset expected 缺 golden_source → 无法核 oracle_source 真伪 → FAILED（防篡改门失效）。"""
        cs = json.loads(json.dumps(CASESET))
        del cs["cases"][0]["expected"]["golden_source"]
        _w(self.d, "caseset.json", cs)
        _w(self.d, "evidence.json", _ev(self.d, ["x_000", "x_001"]))
        _w(self.d, "verdict.json", _vd("pass"))
        self.assertTrue(any("缺 golden_source" in e for e in self._errs("task2")))

    def test_task2_unmappable_golden_source_fails(self):
        """caseset golden_source 前缀无法映射 oracle_source（fail-closed）→ FAILED。"""
        cs = json.loads(json.dumps(CASESET))
        cs["cases"][0]["expected"]["golden_source"] = "scipy something"  # 未知前缀
        _w(self.d, "caseset.json", cs)
        _w(self.d, "evidence.json", _ev(self.d, ["x_000", "x_001"]))
        _w(self.d, "verdict.json", _vd("pass"))
        self.assertTrue(any("无法映射 oracle_source" in e for e in self._errs("task2")))

    def test_task1_null_shape_no_crash(self):
        """finding #15：inputs[0].shape 为 null → 不崩、记 error（list(None) 会 TypeError）。"""
        cs = json.loads(json.dumps(CASESET))
        cs["cases"][0]["inputs"][0]["shape"] = None
        _w(self.d, "caseset.json", cs)
        errs = self._errs("task1")          # 不抛异常
        self.assertTrue(any("shape" in e for e in errs))

    # --- task3 ---
    def test_task3_ok(self):
        _w(self.d, "caseset.json", _perf_cs(["x_000"]))
        _w(self.d, "evidence.json", _perf_ev(["x_000"]))
        _w(self.d, "perf_report.json", _pr("ok"))
        self.assertEqual(self._errs("task3"), [])

    def test_task3_blocked_fails(self):
        _w(self.d, "perf_report.json", _pr("blocked"))
        self.assertTrue(self._errs("task3"))

    # --- task3 per_case 对齐（补 T5 门延后 finding：防跑性能子集 + 伪造 summary） ---
    def test_task3_perf_subset_fails(self):
        """跑性能子集：caseset 2 个性能用例、perf_report 只 1 个 → 必 FAIL（防跑子集）。"""
        _w(self.d, "caseset.json", _perf_cs(["p0", "p1"]))
        _w(self.d, "evidence.json", _perf_ev(["p0", "p1"]))
        _w(self.d, "perf_report.json", {"op": "X",
            "per_case": [{"case_id": "p0", "scope": "kernel_only", "blocked": False, "达标": True}],
            "summary": {"status": "ok", "perf_cases": 1, "达标": 1, "blocked": 0}})
        self.assertTrue(any("性能子集" in e for e in self._errs("task3")))

    def test_task3_forged_summary_fails(self):
        """伪造 summary=ok：per_case 实际未达标、summary 谎报 达标=1 → 计数不一致 → 必 FAIL。"""
        _w(self.d, "caseset.json", _perf_cs(["p0"]))
        _w(self.d, "evidence.json", _perf_ev(["p0"]))
        _w(self.d, "perf_report.json", {"op": "X",
            "per_case": [{"case_id": "p0", "scope": "kernel_only", "blocked": False, "达标": False}],
            "summary": {"status": "ok", "perf_cases": 1, "达标": 1, "blocked": 0}})
        self.assertTrue(any("不一致" in e for e in self._errs("task3")))

    def test_task3_perf_evidence_missing_fails(self):
        """伪造 per_case 但性能用例无 evidence（未实跑）→ 必 FAIL。"""
        _w(self.d, "caseset.json", _perf_cs(["p0"]))
        _w(self.d, "evidence.json", _perf_ev([]))    # 空证据
        _w(self.d, "perf_report.json", {"op": "X",
            "per_case": [{"case_id": "p0", "scope": "kernel_only", "blocked": False, "达标": True}],
            "summary": {"status": "ok", "perf_cases": 1, "达标": 1, "blocked": 0}})
        self.assertTrue(any("性能证据缺失" in e for e in self._errs("task3")))

    def test_task3_wrong_scope_fails(self):
        """性能不是 kernel-only（混入 e2e 墙钟）→ 必 FAIL。"""
        _w(self.d, "perf_report.json", _pr("ok", scope="e2e"))
        self.assertTrue(any("kernel_only" in e for e in self._errs("task3")))

    # --- 抗坏输入：门必须判 FAILED、绝不崩溃/静默放过 ---
    def test_task1_missing_id_no_crash(self):
        cs = json.loads(json.dumps(CASESET))
        del cs["cases"][0]["id"]
        _w(self.d, "caseset.json", cs)
        self.assertTrue(any("缺 id" in e for e in self._errs("task1")))

    def test_task1_missing_threshold(self):
        cs = json.loads(json.dumps(CASESET))
        del cs["cases"][0]["expected"]["threshold"]
        _w(self.d, "caseset.json", cs)
        self.assertTrue(any("threshold" in e for e in self._errs("task1")))

    def test_task1_empty_inputs(self):
        cs = json.loads(json.dumps(CASESET))
        cs["cases"][0]["inputs"] = []
        _w(self.d, "caseset.json", cs)
        self.assertTrue(any("无 inputs" in e for e in self._errs("task1")))

    def test_task1_bad_json_no_crash(self):
        with open(os.path.join(self.d, "caseset.json"), "w", encoding="utf-8") as f:
            f.write("{bad json")
        self.assertTrue(self._errs("task1"))  # FAILED 而非抛异常

    def test_task2_missing_precision_fails(self):
        _w(self.d, "caseset.json", CASESET)
        _w(self.d, "evidence.json", {"op": "X", "evidence": [
            {"case_id": "x_000"},  # 缺 precision
            {"case_id": "x_001", "precision": {"threshold": 0.001}}]})
        _w(self.d, "verdict.json", _vd("pass"))
        self.assertTrue(any("precision" in e for e in self._errs("task2")))

    def test_task2_missing_verdict_overall_fails(self):
        _w(self.d, "caseset.json", CASESET)
        _w(self.d, "evidence.json", _ev(self.d, ["x_000", "x_001"]))
        _w(self.d, "verdict.json", {"op": "X"})  # 无 overall
        self.assertTrue(any("verdict.overall" in e for e in self._errs("task2")))

    def test_task3_missing_summary_fails(self):
        _w(self.d, "perf_report.json", {"op": "X", "per_case": []})
        self.assertTrue(any("summary" in e for e in self._errs("task3")))

    def test_task3_missing_scope_fails(self):
        _w(self.d, "perf_report.json", {"op": "X", "summary": {"status": "ok", "perf_cases": 1, "达标": 1},
                                        "per_case": [{"case_id": "x_000", "blocked": False}]})  # 缺 scope
        self.assertTrue(any("kernel_only" in e for e in self._errs("task3")))

    def test_task3_bad_status_fails(self):
        _w(self.d, "perf_report.json", _pr("weird"))
        self.assertTrue(any("非法" in e for e in self._errs("task3")))


class RunWorkflowExitTest(unittest.TestCase):
    """门做硬 blocker：CLI 退出码要能被 CI 当硬失败。"""
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.here = os.path.dirname(os.path.abspath(__file__))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _run(self, *extra):
        return subprocess.run(
            [sys.executable, os.path.join(self.here, "run_workflow.py"),
             os.path.join(self.here, "..", "samples", "specs", "isclose.spec.json"),
             "--mode", "mock", "--out", self.d, *extra],
            capture_output=True, text=True)

    def test_clean_exit_0(self):
        self.assertEqual(self._run().returncode, 0)

    def test_defect_exit_nonzero(self):
        # §1 覆盖-预算重写后 case id 变——从生成的 caseset 取真实 fp32 精度 case id 注缺陷（稳健、不硬编码）。
        import gen_cases
        spec_path = os.path.join(self.here, "..", "samples", "specs", "isclose.spec.json")
        cs = gen_cases.gen_cases(json.load(open(spec_path, encoding="utf-8")),
                                 os.path.join(self.d, "gen"))
        did = next(c["id"] for c in cs["cases"]
                   if "精度" in c["dims"] and c["expected"].get("compare") != "na")
        # ⚠ 不能用 CLI `--defect`（C5 已下架）：argparse 用法错恰好回 2，assertNotEqual(rc,0) 会**假绿**——
        # 一次都没跑到门却报绿，正是 C5 要消灭的那类东西。改进程内调用，并把断言收紧到 rc==1（真精度 FAIL）。
        import run_workflow as W
        r = W.run(spec_path, mode="mock", out_dir=self.d, defect=[did])
        self.assertEqual(r["exit_code"], 1, r)

    def test_failfast_skips_perf(self):
        # §精度门前置 + fail-fast（用户 2026-07-15）：任一精度挂 → 跳过 Task3 性能 → FAIL(精度)、exit 1、task3 门未跑。
        import gen_cases
        spec_path = os.path.join(self.here, "..", "samples", "specs", "isclose.spec.json")
        cs = gen_cases.gen_cases(json.load(open(spec_path, encoding="utf-8")),
                                 os.path.join(self.d, "gen"))
        did = next(c["id"] for c in cs["cases"]
                   if "精度" in c["dims"] and c["expected"].get("compare") != "na")
        import run_workflow as W
        r = W.run(spec_path, mode="mock", out_dir=self.d, defect=[did])
        self.assertEqual(r["exit_code"], 1, r)
        with open(os.path.join(self.d, "perf_report.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["summary"]["status"], "skipped_precision_gate")
        # C5：mock 是**非验收通路**，物理上不产 acceptance.json，改产 dev_run_summary.json
        # （overall→pipeline_result、gate.errors→selfcheck.errors；state 键刻意不写）。
        with open(os.path.join(self.d, "dev_run_summary.json"), encoding="utf-8") as f:
            acc = json.load(f)
        self.assertEqual(acc["pipeline_result"], "FAIL(精度)")
        self.assertNotIn("task3", acc["selfcheck"]["errors"])  # 精度未全过 → task3 门未纳入
        self.assertFalse(os.path.exists(os.path.join(self.d, "acceptance.json")),
                         "非验收通路绝不产 acceptance.json（C5）")


# ===== T6/T8 perf 包新增（自包含，与上方 GateTest 无耦合，便于与主树 T5 干净合并）=====
class GateTask3PerfPackageTest(unittest.TestCase):
    """gate_task3 的小shape例外门(T6) + 挂起/不可比态(T8)。"""
    def setUp(self):
        self.d = tempfile.mkdtemp()
        # gate_task3 现按 case 对齐 caseset/evidence（防跑子集）；本类 perf_report 均用 case_id x0。
        _w(self.d, "caseset.json", _perf_cs(["x0"]))
        _w(self.d, "evidence.json", _perf_ev(["x0"]))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _errs(self):
        errs = []
        G.gate_task3(self.d, errs)
        return errs

    def _exc_report(self, plot_file="perf_sim_x.svg", tamper_sha=False):
        import perf_sim_plot
        sim = {"op": "X", "when_us_below": 10, "abs_gap_us_within": 3,
               "points": [{"case_id": "x0", "numel": 64, "npu_us": 1.5, "baseline_us": 1.2,
                           "gap": 0.3, "within": 3, "conclusion": "c"}],
               "overall": "o"}
        svg = os.path.join(self.d, "perf_sim_x.svg")
        perf_sim_plot.render_svg(sim, svg)
        sha = perf_sim_plot.sha256_of(svg)
        report = {"op": "X", "per_case": [
            {"case_id": "x0", "scope": "kernel_only", "npu_us": 1.5,
             "baseline": {"source": "tbe", "us": 1.2}, "ratio": 0.8, "达标": False,
             "exception": "small_shape",
             "exception_detail": {"npu_us": 1.5, "baseline_us": 1.2, "gap": 0.3, "within": 3,
                                  "when_us_below": 10, "conclusion": "c"}}],
            "notes": [], "summary": {"perf_cases": 1, "达标": 0, "blocked": 0, "status": "exception"},
            "simulation": sim,
            "simulation_plot": {"file": plot_file, "sha256": ("deadbeef" if tamper_sha else sha)}}
        return report

    def test_exception_ok(self):
        _w(self.d, "perf_report.json", self._exc_report())
        self.assertEqual(self._errs(), [])

    def test_exception_missing_svg_fails(self):
        r = self._exc_report()
        _w(self.d, "perf_report.json", r)
        os.remove(os.path.join(self.d, "perf_sim_x.svg"))
        self.assertTrue(any("仿真图" in e for e in self._errs()))

    def test_exception_sha_mismatch_fails(self):
        _w(self.d, "perf_report.json", self._exc_report(tamper_sha=True))
        self.assertTrue(any("sha256" in e for e in self._errs()))

    def test_exception_simulation_mismatch_fails(self):
        r = self._exc_report()
        r["simulation"]["points"][0]["case_id"] = "other"   # 与例外行对不上
        _w(self.d, "perf_report.json", r)
        self.assertTrue(any("仿真图" in e for e in self._errs()))

    def test_exception_path_escape_fails(self):
        _w(self.d, "perf_report.json", self._exc_report(plot_file="../evil.svg"))
        self.assertTrue(any("路径逃逸" in e for e in self._errs()))

    # --- gt3-7：simulation_plot「有图强制」曾空心（可指任意文件+自填 sha）→ .svg 守卫 + 重算比对锚定 ---
    def test_gt3_7_file_points_to_caseset_fails(self):
        """file 指向 caseset.json（非 .svg，sha 填对该文件）→ 扩展名守卫先挡 → FAILED。"""
        r = self._exc_report(plot_file="caseset.json")
        # sha 填 caseset.json 自身（模拟作者把 sha 绑到可控非图文件，旧洞下会过）
        r["simulation_plot"]["sha256"] = G._sha256(os.path.join(self.d, "caseset.json"))
        _w(self.d, "perf_report.json", r)
        errs = self._errs()
        self.assertTrue(any("非 .svg" in e for e in errs))

    def test_gt3_7_doctored_svg_recompute_mismatch_fails(self):
        """.svg 文件被换成与 simulation 无关的内容、sha 与该文件自洽（过 sha 存在性检查）→
        门内用 simulation 重算 SVG 比对字节 → 不符 → FAILED（证明重算比对真正锚定数据，非仅扩展名）。"""
        r = self._exc_report()                      # 先渲染出合法 perf_sim_x.svg
        svg = os.path.join(self.d, "perf_sim_x.svg")
        with open(svg, "w", encoding="utf-8") as f:  # 换成伪造 SVG（合法 .svg 头但非本 simulation 所渲）
            f.write('<svg xmlns="http://www.w3.org/2000/svg"><text>forged</text></svg>')
        r["simulation_plot"]["sha256"] = G._sha256(svg)  # sha 与被换文件自洽 → 过存在性/sha 检查
        _w(self.d, "perf_report.json", r)
        errs = self._errs()
        self.assertTrue(any("与 simulation 数据不符" in e for e in errs))

    def test_gt3_7_normal_svg_passes(self):
        """正常 .svg（由本 simulation 渲染、sha 相符）→ 重算比对通过 → 门放行（合法路径不误挡）。"""
        _w(self.d, "perf_report.json", self._exc_report())
        self.assertEqual(self._errs(), [])

    def test_exception_wrong_scope_fails(self):
        r = self._exc_report()
        r["per_case"][0]["scope"] = "e2e"
        _w(self.d, "perf_report.json", r)
        self.assertTrue(any("kernel_only" in e for e in self._errs()))

    def test_wait_suspend_ok(self):
        _w(self.d, "perf_report.json", {"op": "X", "per_case": [
            {"case_id": "x0", "npu_us": 1.5, "npu_scope": "kernel_only", "达标": False, "blocked": False}],
            "summary": {"status": "blocked_wait_gpu_benchmark", "perf_cases": 1, "达标": 0, "blocked": 0}})
        self.assertEqual(self._errs(), [])          # 正规挂起、非门 FAILED

    def test_wait_missing_npu_fails(self):
        _w(self.d, "perf_report.json", {"op": "X", "per_case": [
            {"case_id": "x0", "npu_scope": "kernel_only", "达标": False, "blocked": False}],  # 缺 npu_us
            "summary": {"status": "blocked_wait_gpu_benchmark", "perf_cases": 1, "达标": 0, "blocked": 0}})
        self.assertTrue(any("npu_us" in e for e in self._errs()))

    def test_incomparable_fails(self):
        _w(self.d, "perf_report.json", {"op": "X", "per_case": [
            {"case_id": "x0", "达标": False, "blocked": True, "note": "scope 不符"}],
            "summary": {"status": "blocked_incomparable_timing_scope", "perf_cases": 1, "达标": 0, "blocked": 1}})
        self.assertTrue(any("不可比" in e for e in self._errs()))


class RunWorkflowPerfPackageTest(unittest.TestCase):
    """端到端子进程：小shape例外→exit2+PASSED_WITH_RISK；缺 GPU 标杆→BLOCKED_WAIT(非 fail)。"""
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.here = os.path.dirname(os.path.abspath(__file__))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _run(self, spec, *extra):
        return subprocess.run(
            [sys.executable, os.path.join(self.here, "run_workflow.py"),
             os.path.join(self.here, spec), "--mode", "mock", "--out", self.d, *extra],
            capture_output=True, text=True)

    def _gate(self, stage):
        return subprocess.run(
            [sys.executable, os.path.join(self.here, "validate_acceptance_state.py"),
             "--stage", stage, "--dir", self.d], capture_output=True, text=True)

    def _json(self, name):
        with open(os.path.join(self.d, name), encoding="utf-8") as f:
            return json.load(f)

    def test_perf_trivial_met_small_shapes(self):
        # §1 覆盖-预算重写 + trivial-met：§1 不再产「小shape」标签用例；小 shape 性能用例（numel<4096）
        #  改标 **trivial-met**（达标、免测），perf 达标由代表性大 shape（whitelist/bndhi, numel≥4096）主导。
        #  取代已被 trivial-met 取代的 small-shape-exception e2e 路径（该 exception 逻辑仍存 perf_compare、
        #  只是 §1 pipeline 不再触发；其直测覆盖见 test_perf_compare.SmallShapeExceptionTest）。
        r = self._run("../samples/specs/sign.spec.json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)   # 全 trivial + 大 shape 达标 → PASS
        acc = self._json("dev_run_summary.json")   # C5：mock 不产 acceptance.json
        self.assertEqual(acc["pipeline_result"], "PASS")   # state 键刻意不写（非验收通路无 canonical 状态）
        self.assertEqual(acc["repo_mode"], "mock")
        pr = self._json("perf_report.json")
        self.assertEqual(pr["summary"]["status"], "ok")
        trivial = [row for row in pr["per_case"] if row.get("trivial")]
        nontrivial = [row for row in pr["per_case"] if not row.get("trivial")]
        self.assertTrue(trivial, "应有 trivial-met 退化用例（小 shape numel<4096）")
        self.assertTrue(nontrivial, "应有非 trivial 大 shape 性能用例（whitelist/bndhi）")
        self.assertTrue(all(row.get("达标") for row in nontrivial), "大 shape 性能用例应达标")
        self.assertEqual(self._gate("task3").returncode, 0)     # trivial 门豁免 + 大 shape 完整 → 门过

    def test_gpu_wait_blocked_not_fail(self):
        r = self._run("testdata/gpu_demo.spec.json")            # spec.perf.baseline=gpu_external, 无 --gpu-baseline
        acc = self._json("dev_run_summary.json")               # C5：mock 不产 acceptance.json
        self.assertEqual(acc["pipeline_result"], "BLOCKED_WAIT_GPU_BENCHMARK")
        self.assertNotEqual(r.returncode, 0)                   # 非 PASS
        self.assertNotIn("PASS", acc["pipeline_result"])       # 缺 GPU 数据绝不显 PASS


# ===== gt3 CONFIRMED 绕过负例（gate_task3 零证据/wait 绕过/坏输入/空转/bool计数/空壳证据）=====
class GateTask3ConfirmedBypassTest(unittest.TestCase):
    """逐条钉死 codex 多维审 + 对抗复核坐实的 gate_task3 CONFIRMED 绕过。
    setUp 写「干净」性能 caseset+evidence（p0），各用例只改 perf_report 造对应绕过。"""
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.here = os.path.dirname(os.path.abspath(__file__))
        _w(self.d, "caseset.json", _perf_cs(["p0"]))
        _w(self.d, "evidence.json", _perf_ev(["p0"]))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _errs(self):
        errs = []
        G.gate_task3(self.d, errs)
        return errs

    def _cli(self):
        return subprocess.run(
            [sys.executable, os.path.join(self.here, "validate_acceptance_state.py"),
             "--stage", "task3", "--dir", self.d], capture_output=True, text=True)

    # gt3-1：status=ok + 全行 blocked=True + summary 自洽 → 门放行却零真实性能证据 → 必 FAILED。
    def test_gt3_1_zero_evidence_blocked_ok_fails(self):
        _w(self.d, "perf_report.json", {"op": "X",
            "per_case": [{"case_id": "p0", "blocked": True, "达标": False}],
            "summary": {"status": "ok", "perf_cases": 1, "达标": 0, "blocked": 1}})
        self.assertTrue(any("口径矛盾" in e and "blocked" in e for e in self._errs()))
        self.assertEqual(self._cli().returncode, 1)     # CLI 硬失败

    # gt3-2：blocked_wait_gpu_benchmark + 行标 blocked=True 但缺 npu_us → 挂起态不得豁免 NPU 证据 → FAILED。
    def test_gt3_2_wait_blocked_missing_npu_fails(self):
        _w(self.d, "perf_report.json", {"op": "X",
            "per_case": [{"case_id": "p0", "blocked": True, "npu_scope": "kernel_only", "达标": False}],
            "summary": {"status": "blocked_wait_gpu_benchmark", "perf_cases": 1, "达标": 0, "blocked": 1}})
        self.assertTrue(any("npu_us" in e for e in self._errs()))
        self.assertEqual(self._cli().returncode, 1)

    def test_gt3_2_wait_blocked_wrong_scope_fails(self):
        """挂起态 + blocked 行 npu_scope != kernel_only → blocked 不豁免 → FAILED。"""
        _w(self.d, "perf_report.json", {"op": "X",
            "per_case": [{"case_id": "p0", "blocked": True, "npu_us": 1.5,
                          "npu_scope": "e2e", "达标": False}],
            "summary": {"status": "blocked_wait_gpu_benchmark", "perf_cases": 1, "达标": 0, "blocked": 1}})
        self.assertTrue(any("npu_scope" in e for e in self._errs()))

    # gt3-3：性能 case 的 evidence 为空壳 {"case_id":...}（无 perf 载荷）→ 空壳证据 → FAILED。
    def test_gt3_3_hollow_evidence_fails(self):
        _w(self.d, "evidence.json", {"op": "X", "evidence": [{"case_id": "p0"}]})  # 空壳
        _w(self.d, "perf_report.json", {"op": "X",
            "per_case": [{"case_id": "p0", "scope": "kernel_only", "blocked": False, "达标": True}],
            "summary": {"status": "ok", "perf_cases": 1, "达标": 1, "blocked": 0}})
        self.assertTrue(any("空壳" in e or "性能证据缺失" in e for e in self._errs()))
        self.assertEqual(self._cli().returncode, 1)

    def test_gt3_3_perf_payload_present_passes(self):
        """evidence 带真实 perf 载荷（perf.us 有限正 + scope）→ 计入 → 门放行（不误挡合法）。"""
        _w(self.d, "perf_report.json", {"op": "X",
            "per_case": [{"case_id": "p0", "scope": "kernel_only", "blocked": False, "达标": True}],
            "summary": {"status": "ok", "perf_cases": 1, "达标": 1, "blocked": 0}})
        self.assertEqual(self._errs(), [])

    # gt3-4：status=ok + perf_cases=0 + per_case=[] → 空转伪 ok → 必 FAILED。
    def test_gt3_4_ok_empty_percase_fails(self):
        _w(self.d, "perf_report.json", {"op": "X", "per_case": [],
            "summary": {"status": "ok", "perf_cases": 0, "达标": 0, "blocked": 0}})
        errs = self._errs()
        self.assertTrue(any("per_case 为空" in e for e in errs))
        self.assertTrue(any("perf_cases=0" in e for e in errs))
        self.assertEqual(self._cli().returncode, 1)

    def test_gt3_4_ok_but_caseset_no_perf_dim_fails(self):
        """caseset 无「性能」dim 用例却 status=ok（防跑子集空转伪 ok）→ 口径矛盾 → FAILED。"""
        _w(self.d, "caseset.json", {"op": "X", "cases": [
            {"id": "f0", "dims": ["func"], "inputs": [{"name": "a", "shape": [16], "dtype": "float32"}]}]})
        _w(self.d, "perf_report.json", {"op": "X",
            "per_case": [{"case_id": "p0", "scope": "kernel_only", "blocked": False, "达标": True}],
            "summary": {"status": "ok", "perf_cases": 1, "达标": 1, "blocked": 0}})
        self.assertTrue(any("无「性能」dim 用例" in e for e in self._errs()))

    # gt3-6：坏输入不崩——summary.status 为 dict/list；per_case[].case_id 为 list/dict → FAILED 且不抛异常。
    def test_gt3_6_status_dict_no_crash(self):
        _w(self.d, "perf_report.json", {"op": "X",
            "per_case": [{"case_id": "p0", "scope": "kernel_only", "blocked": False, "达标": True}],
            "summary": {"status": {}, "perf_cases": 1, "达标": 1, "blocked": 0}})
        errs = self._errs()   # 不抛异常
        self.assertTrue(any("status 非字符串" in e for e in errs))

    def test_gt3_6_status_list_no_crash(self):
        _w(self.d, "perf_report.json", {"op": "X",
            "per_case": [{"case_id": "p0", "scope": "kernel_only", "blocked": False, "达标": True}],
            "summary": {"status": [], "perf_cases": 1, "达标": 1, "blocked": 0}})
        self.assertTrue(any("status 非字符串" in e for e in self._errs()))

    def test_gt3_6_case_id_list_no_crash(self):
        _w(self.d, "perf_report.json", {"op": "X",
            "per_case": [{"case_id": ["p0"], "scope": "kernel_only", "blocked": False, "达标": True}],
            "summary": {"status": "ok", "perf_cases": 1, "达标": 1, "blocked": 0}})
        errs = self._errs()   # 非空 list 的 case_id 旧代码会崩 Counter unhashable
        self.assertTrue(any("case_id" in e for e in errs))
        self.assertEqual(self._cli().returncode, 1)

    def test_gt3_6_case_id_dict_no_crash(self):
        _w(self.d, "perf_report.json", {"op": "X",
            "per_case": [{"case_id": {"k": 1}, "scope": "kernel_only", "blocked": False, "达标": True}],
            "summary": {"status": "ok", "perf_cases": 1, "达标": 1, "blocked": 0}})
        self.assertTrue(any("case_id" in e for e in self._errs()))

    def test_gt3_6_case_id_empty_list_no_crash(self):
        _w(self.d, "perf_report.json", {"op": "X",
            "per_case": [{"case_id": [], "scope": "kernel_only", "blocked": False, "达标": True}],
            "summary": {"status": "ok", "perf_cases": 1, "达标": 1, "blocked": 0}})
        self.assertTrue(any("case_id" in e for e in self._errs()))

    # gt3-8：bool==int 计数——summary.perf_cases=True；行级 达标="yes"（truthy 计入）→ 必 FAILED。
    def test_gt3_8_perf_cases_true_fails(self):
        _w(self.d, "perf_report.json", {"op": "X",
            "per_case": [{"case_id": "p0", "scope": "kernel_only", "blocked": False, "达标": True}],
            "summary": {"status": "ok", "perf_cases": True, "达标": 1, "blocked": 0}})
        self.assertTrue(any("perf_cases" in e and ("bool" in e or "整数" in e) for e in self._errs()))
        self.assertEqual(self._cli().returncode, 1)

    def test_gt3_8_daobiao_string_fails(self):
        _w(self.d, "perf_report.json", {"op": "X",
            "per_case": [{"case_id": "p0", "scope": "kernel_only", "blocked": False, "达标": "yes"}],
            "summary": {"status": "ok", "perf_cases": 1, "达标": 1, "blocked": 0}})
        self.assertTrue(any("达标 非 bool" in e for e in self._errs()))

    # 回归：合法 ok 路径不被上述任何强化误挡。
    def test_clean_ok_still_passes(self):
        _w(self.d, "perf_report.json", {"op": "X",
            "per_case": [{"case_id": "p0", "scope": "kernel_only", "blocked": False, "达标": True}],
            "summary": {"status": "ok", "perf_cases": 1, "达标": 1, "blocked": 0}})
        self.assertEqual(self._errs(), [])
        self.assertEqual(self._cli().returncode, 0)


class Cases50NaTrivialGateTest(unittest.TestCase):
    """§1 覆盖-预算 + Layer B/C 门集成：空 Tensor(na) 豁免 + 防伪造 na；trivial-met 豁免 + 防伪造 trivial。"""
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _na_cs(self, shape):
        return {"op": "X", "cases": [
            {"id": "e0", "dims": ["功能"], "tags": ["特殊"],
             "inputs": [{"name": "a", "shape": shape, "dtype": "float32"}],
             "expected": {"golden_path": "e0/golden.npy", "compare": "na", "standard": "na",
                          "verify_mode": "exact", "compare_dtype": None}}]}

    def test_task1_empty_na_ok(self):
        _w(self.d, "caseset.json", self._na_cs([0]))          # 真空 Tensor（某维=0）
        errs = []
        G.gate_task1(self.d, errs)
        self.assertEqual(errs, [], errs)                      # na 用例豁免精度字段完整性

    def test_task1_forged_na_errors(self):
        _w(self.d, "caseset.json", self._na_cs([16]))         # compare=na 但非空 Tensor
        errs = []
        G.gate_task1(self.d, errs)
        self.assertTrue(any("真空 Tensor" in e for e in errs), errs)  # codex #4：消息升级为「非严格真空」

    def _pr_rows(self, rows):
        return {"op": "X", "baseline_source": "tbe", "target_ratio": 0.95, "per_case": rows,
                "summary": {"status": "ok", "perf_cases": len(rows),
                            "达标": sum(1 for r in rows if r.get("达标")), "blocked": 0}}

    def test_task3_trivial_ok(self):
        _w(self.d, "caseset.json", {"op": "X", "cases": [
            {"id": "t0", "dims": ["性能"], "tags": ["常规"],
             "inputs": [{"name": "a", "shape": [16], "dtype": "float32"}]}]})
        _w(self.d, "evidence.json", _perf_ev(["t0"]))
        _w(self.d, "perf_report.json",
           self._pr_rows([{"case_id": "t0", "达标": True, "trivial": True, "numel": 16}]))
        errs = []
        G.gate_task3(self.d, errs)
        self.assertEqual(errs, [], errs)                      # trivial 行豁免 scope（numel<4096 复核通过）

    def test_task3_forged_trivial_errors(self):
        _w(self.d, "caseset.json", _perf_cs(["b0"]))          # shape [1024,1024]，numel≥4096
        _w(self.d, "evidence.json", _perf_ev(["b0"]))
        _w(self.d, "perf_report.json",
           self._pr_rows([{"case_id": "b0", "达标": True, "trivial": True}]))  # 大 case 谎报 trivial
        errs = []
        G.gate_task3(self.d, errs)
        self.assertTrue(any("trivial" in e and "numel" in e for e in errs), errs)


class DefaultModeIsRealMachineTest(unittest.TestCase):
    """U6a：`--mode` 默认已从 mock 翻为 new_example（真机通路）。钉死两点，防被悄悄改回危险的 mock 默认
    （mock 的「NPU 输出」= golden.copy()、精度按构造必过 → 默认 mock = 默认产出伪造 acceptance.json）：
    (1) run() 签名默认 == 'new_example'；
    (2) 不带 --mode + 无真机 OPRUNWAY_* 配置 → fail-closed 非零退出，且**绝不**落伪造 acceptance.json。"""
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.here = os.path.dirname(os.path.abspath(__file__))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_run_signature_default_is_new_example(self):
        import inspect
        import run_workflow as W
        self.assertEqual(inspect.signature(W.run).parameters["mode"].default, "new_example")

    def test_no_mode_without_realcfg_failclosed_no_forged_acceptance(self):
        # 清掉真机 OPRUNWAY_* 配置（其余 env 保留，与本用例无关）→ 不带 --mode 即走默认（应 = new_example）。
        env = {k: v for k, v in os.environ.items()
               if k not in ("OPRUNWAY_REMOTE_DIR", "OPRUNWAY_OPS_REPO", "OPRUNWAY_OPP",
                            "OPRUNWAY_OP_SRC", "OPRUNWAY_TARGET", "OPRUNWAY_SSH_HOST")}
        spec = os.path.join(self.here, "..", "samples", "specs", "isclose.spec.json")
        r = subprocess.run(
            [sys.executable, os.path.join(self.here, "run_workflow.py"), spec, "--out", self.d],
            capture_output=True, text=True, env=env)                          # 关键：不带 --mode
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)             # fail-closed（非零退出）
        self.assertFalse(os.path.exists(os.path.join(self.d, "acceptance.json")),
                         "默认走真机、缺配置时绝不产出伪造 acceptance.json")   # 不落半产物
        self.assertIn("--mode mock", r.stdout + r.stderr)                     # 指路提示存在（要本地自检加 mock）


# ===== C1 下游 · 输出形状对账 + C4 · dtype 冲突（用户 2026-07-22 拍板的两条）=====
def _prod(shape):
    n = 1
    for d in shape:
        n *= d
    return n


def _v_triple(out_shape=None, ev_out_shape=None, ev_prov_shape=None, numel=None,
              in_shape=(16,), spec_gaps=None, cs_gaps=None, dtype_required=None,
              dtype="float32", extra_exp=None):
    """构一份**据 spec 复算的诚实**三元组（spec/caseset/evidence 口径全等 canonical），只在需要处注入差异。

    默认（不传任何形状/gap）= 现行 elementwise 通路：caseset 不声明 out_shape、evidence 不自报形状，
    validator 行为与本轮改动前**完全一致**——用作「不误伤」的反事实对照。"""
    spec = {"op": "S", "verify_mode": "numerical",
            "params": [{"name": "self", "io": "in", "dtype": [dtype]},
                       {"name": "out", "io": "out", "dtype": [dtype]}],
            "precision": {"oracle": "ascendoptest", "standard": "ascendoptest_default"}}
    if dtype_required is not None:
        spec["dtype_required"] = dtype_required
    if spec_gaps is not None:
        spec["task_pr_gaps"] = spec_gaps
    eff = precision_policy.effective_standard("ascendoptest_default", dtype, "rel_err")
    pol = precision_policy.threshold_for(eff, dtype)
    tpid = precision_policy.tolerance_policy_id(eff, dtype)
    dig = precision_policy.threshold_digest(pol)
    exp = {"golden_path": "g.npy", "verify_mode": "numerical", "standard": eff,
           "compare_dtype": dtype, "compare": "rel_err", "tolerance_policy_id": tpid,
           "policy": pol, "threshold": dig}
    if out_shape is not None:
        exp["out_shape"] = list(out_shape)
    if extra_exp:
        exp.update(extra_exp)
    n = numel if numel is not None else _prod(out_shape if out_shape is not None else in_shape)
    prec = {"standard": eff, "tolerance_policy_id": tpid, "policy": pol, "threshold": dig,
            "metrics": {"bad_count": 0, "numel": n}}
    if ev_out_shape is not None:
        prec["out_shape"] = list(ev_out_shape)
    if ev_prov_shape is not None:
        prec["provenance"] = {"out_shape": list(ev_prov_shape)}
    acc = precision_policy.resolve_acceptance(spec, eff, dtype)
    if acc:
        exp["acceptance_policy"], exp["acceptance_tolerance_policy_id"] = acc
        prec["acceptance_policy"], prec["acceptance_tolerance_policy_id"] = acc
        prec["acceptance_metrics"] = dict(prec["metrics"])
    cs = {"op": "S", "cases": [{"id": "c0", "dims": ["功能", "精度"],
          "inputs": [{"name": "self", "shape": list(in_shape), "dtype": dtype}],
          "expected": exp}]}
    if cs_gaps is not None:
        cs["task_pr_gaps"] = cs_gaps
    ev = {"op": "S", "evidence": [{"case_id": "c0", "status": "ok", "precision": prec}]}
    return spec, cs, ev


class GoldenTierGateTest(unittest.TestCase):
    """批 5：golden 档位真正参与裁决路由。

    ⚠ **核心判断**：授权核不实 ≠ 精度 fail，也 ≠ needs_review。
    它意味着**这份真值本身来路不明** → 基于它的每一条精度判定都不成立。
    - 报成 `fail` 会让人去查算子——**查错方向**（算子可能好好的）。
    - 报成 `needs_review` 也不对——指标算得好好的，不确定的是真值本身。
    所以单列 `blocked_golden_unauthorized`，且**排在所有别的判定之前**：
    来路不明的真值下，「精度 fail」「性能未达」这些结论本身就不成立，不该被它们盖住。"""

    @staticmethod
    def _with_tier(tier):
        spec, cs, ev = _v_triple()
        cs["cases"][0]["expected"]["golden_tier"] = tier
        return V.validate(spec, cs, ev)

    def test_no_contract_is_backward_compatible(self):
        """`golden_tier=None`（未声明契约块）→ 不参与门，裁决与批 5 前一致。"""
        vd = self._with_tier(None)
        self.assertEqual(vd["overall"]["verdict"], "pass", vd["overall"])
        self.assertEqual(vd["overall"]["counts"]["golden_blocked"], 0)

    def test_tier1_verified_passes_clean(self):
        vd = self._with_tier({"tier": 1, "requires_human_review": False, "blocked_reason": None})
        self.assertEqual(vd["overall"]["verdict"], "pass", vd["overall"])

    def test_tier4_blocked_is_not_fail_and_not_needs_review(self):
        vd = self._with_tier({"tier": 4, "requires_human_review": True,
                              "blocked_reason": "unverifiable_authorization"})
        o = vd["overall"]
        self.assertEqual(o["verdict"], "blocked_golden_unauthorized")
        self.assertNotEqual(o["verdict"], "fail")           # 别让人去查算子
        self.assertNotEqual(o["verdict"], "needs_review")   # 指标没问题，真值才有问题
        self.assertEqual(o["counts"]["golden_blocked"], 1)
        self.assertEqual(o["golden_blocked"][0]["blocked_reason"], "unverifiable_authorization")

    def test_tier3_multistep_routes_to_human_cp(self):
        """tier 3（按公式自拼多步）→ 正当但必须人核（R5 末位档），与 passed_with_risk 同档。"""
        vd = self._with_tier({"tier": 3, "requires_human_review": True, "blocked_reason": None})
        o = vd["overall"]
        self.assertEqual(o["verdict"], "passed_with_risk")
        self.assertTrue(o["requires_human_cp"])
        self.assertEqual(len(o["golden_needs_human_review"]), 1)

    def test_blocked_takes_precedence_over_precision_fail(self):
        """**优先级**：真值来路不明时，精度 fail 这个结论本身不成立，不该盖住 blocked。"""
        spec, cs, ev = _v_triple()
        cs["cases"][0]["expected"]["golden_tier"] = {
            "tier": 4, "requires_human_review": True, "blocked_reason": "method_unavailable"}
        ev["evidence"][0]["precision"]["metrics"] = {"bad_count": 999, "numel": 16}   # 精度真的挂了
        vd = V.validate(spec, cs, ev)
        self.assertEqual(vd["overall"]["verdict"], "blocked_golden_unauthorized",
                         "blocked 必须盖过 fail —— 否则会把人引去查算子")

    def test_run_workflow_routes_blocked_to_its_own_state_not_fail_precision(self):
        """编排层：`blocked_golden_unauthorized` → 独立终态，**不落 FAIL(精度)**、不进 Task3 放行集。"""
        import run_workflow as W
        self.assertIn("BLOCKED_GOLDEN_UNAUTHORIZED", W._STATE_MAP)
        self.assertEqual(W._STATE_MAP["BLOCKED_GOLDEN_UNAUTHORIZED"], "BLOCKED_GOLDEN_UNAUTHORIZED")
        self.assertEqual(W._exit_code("BLOCKED_GOLDEN_UNAUTHORIZED"), 1)   # 非 0（不是干净通过）、非 2（不是挂人工）
        import inspect
        line = next(l for l in inspect.getsource(W.run).splitlines() if "precision_ok =" in l)
        self.assertNotIn("blocked_golden_unauthorized", line,
                         "blocked 不得进精度放行集——真值来路不明时性能对比也没意义")


class GoldenSpecAuthorityTest(unittest.TestCase):
    """批 4（硬约束 #5：判据只从 spec 派生）。

    背景实证（真管路，2026-07-23）：批 5 那道 BLOCKED 门吃的是 **caseset 的自声明**
    `expected.golden_tier.blocked_reason`——改 caseset 一行（blocked→null）即绕过。
    批 4 把判据锚拉回 `spec.golden`：validator 对账后**用 spec 重新派生**档位判门。

    ⚠ **残余边界（documented，非 bug）**：`authorization_verified`（读快照逐字核的结果）
       validator 纯函数复现不了，仍取 caseset。对账 `snapshot_sha` 把它钉到 spec，
       残余篡改面收窄到「真快照在场 + sha 对 + 引文不逐字」。见 `test_residual_boundary_documented`。"""

    _ANCHOR = {"source": "single_api", "method_kind": "torch_cpu",
               "authorization": {"kind": "oracle_method"},
               "taskdoc_snapshot": {"sha256": "a" * 64}}

    def _triple(self, spec_golden=_ANCHOR, tier_extra=None):
        """一份 tier-1 正例：spec.golden 锚 + caseset 与之对账一致 + 授权已核。"""
        spec, cs, ev = _v_triple()
        if spec_golden is not None:
            spec["golden"] = spec_golden
        tier = {"tier": 1, "requires_human_review": False, "blocked_reason": None,
                "authorization_verified": True, "source": "single_api",
                "method_kind": "torch_cpu", "authorization_kind": "oracle_method",
                "snapshot_sha": "a" * 64}
        if tier_extra:
            tier.update(tier_extra)
        cs["cases"][0]["expected"]["golden_tier"] = tier
        return spec, cs, ev

    def test_spec_authoritative_clean_pass(self):
        spec, cs, ev = self._triple()
        o = V.validate(spec, cs, ev)["overall"]
        self.assertEqual(o["verdict"], "pass", o)
        self.assertEqual(o["golden_judged_from"], "spec")

    def test_tampering_blocked_reason_is_ineffective(self):
        """**批 5 那个洞的回归**：真实档位是 blocked，攻击者把 caseset 的 blocked_reason 抹成 null。

        批 5：门信 caseset → pass（绕过）。批 4：validator 从 spec 重新派生，看 av 是真实的
        False → 仍 blocked。这条是整批的立身理由。"""
        # 真实态：授权没核过（av=False）。攻击者只抹掉 blocked_reason，忘了 av（或够不着 av 的语义）。
        spec, cs, ev = self._triple(tier_extra={
            "authorization_verified": False, "blocked_reason": None,   # ← 攻击者抹掉了
            "tier": 1, "requires_human_review": False})
        o = V.validate(spec, cs, ev)["overall"]
        self.assertEqual(o["verdict"], "blocked_golden_unauthorized",
                         "改 blocked_reason 必须无效——validator 从 spec 重新派生")

    def test_tampering_snapshot_sha_fails_closed(self):
        """改 caseset 的 snapshot_sha 想指向别的快照 → 对账不符 → **fail-closed（归 blocked：判据链不可信）**。"""
        spec, cs, ev = self._triple(tier_extra={"snapshot_sha": "d" * 64})
        r = V.validate(spec, cs, ev)
        self.assertEqual(r["overall"]["verdict"], "blocked_golden_unauthorized", r["overall"])
        self.assertTrue(any("判据锚不符" in p for p in r["contract_problems"]))

    def test_tampering_authorization_kind_fails_closed(self):
        """改 caseset 的 authorization_kind（想换判档路径）→ 与 spec 锚不符 → fail-closed（归 blocked）。"""
        spec, cs, ev = self._triple(tier_extra={"authorization_kind": "impl_reference"})
        self.assertEqual(V.validate(spec, cs, ev)["overall"]["verdict"], "blocked_golden_unauthorized")

    def test_real_unverified_authorization_blocks(self):
        """正常路径：授权真没核过（av=False）→ 重新派生 tier 4 → blocked。"""
        spec, cs, ev = self._triple(tier_extra={"authorization_verified": False})
        self.assertEqual(V.validate(spec, cs, ev)["overall"]["verdict"],
                         "blocked_golden_unauthorized")

    def test_non_bool_av_treated_as_unverified(self):
        """caseset 的 av 是非布尔（`"true"` / 1）→ 按未核实处理 + 记 problem（不 fail-open）。"""
        spec, cs, ev = self._triple(tier_extra={"authorization_verified": "true"})
        r = V.validate(spec, cs, ev)
        self.assertEqual(r["overall"]["verdict"], "blocked_golden_unauthorized")
        self.assertTrue(any("非布尔" in p for p in r["contract_problems"]))

    def test_legacy_no_spec_golden_is_backward_compatible(self):
        """无 spec.golden → 向后兼容（caseset 自声明），但 judged_from 显式标出 legacy。"""
        spec, cs, ev = self._triple(spec_golden=None)
        o = V.validate(spec, cs, ev)["overall"]
        self.assertEqual(o["verdict"], "pass", o)
        self.assertEqual(o["golden_judged_from"], "caseset_self_declared")

    # ── codex + 红队独立逮到的 Critical + 一圈 fail-open（重写后回归钉死）────────────
    def _blocking_spec(self):
        """spec.golden = builtin_tbe（不可跑 → derive 恒 tier4 blocked，与 av 无关）。
        诚实产物每条 case 都会带 blocked 的 golden_tier；攻击者想靠动 caseset 让门不触发。"""
        spec, cs, ev = self._triple(spec_golden={
            "source": "single_api", "method_kind": "builtin_tbe",
            "authorization": {"kind": "impl_reference"}})
        cs["cases"][0]["expected"]["golden_tier"] = {
            "tier": 4, "requires_human_review": True, "blocked_reason": "method_unavailable",
            "authorization_verified": False, "source": "single_api", "method_kind": "builtin_tbe",
            "authorization_kind": "impl_reference", "snapshot_sha": None}
        return spec, cs, ev

    def test_deleting_golden_tier_when_spec_present_blocks(self):
        """⭐ **codex Critical + 红队 real-bypass 的回归**：spec.golden 说本算子有 golden 判据（且恒 blocked），
        攻击者删掉 / 置空 caseset 的 golden_tier 想让收集空集合、门整个不触发。重写后必须 blocked。"""
        for label, mut in (("删除", lambda e: e.pop("golden_tier")),
                           ("置 None", lambda e: e.__setitem__("golden_tier", None)),
                           ("置字符串", lambda e: e.__setitem__("golden_tier", "n/a")),
                           ("置 0", lambda e: e.__setitem__("golden_tier", 0)),
                           ("置 []", lambda e: e.__setitem__("golden_tier", []))):
            spec, cs, ev = self._blocking_spec()
            mut(cs["cases"][0]["expected"])
            o = V.validate(spec, cs, ev)["overall"]
            self.assertEqual(o["verdict"], "blocked_golden_unauthorized",
                             f"{label} golden_tier 必须 fail-closed，不得让门静默不触发")
            self.assertNotEqual(o["golden_judged_from"], "caseset_self_declared",
                                f"{label}：spec.golden 在场，不该退成 legacy")

    def test_malformed_spec_golden_fails_closed(self):
        """spec.golden 键在场但畸形（None / 非 dict / 词表错 / oracle 缺合法 sha）→ blocked，**不降级信 caseset**。"""
        for sg in (None, "oops", [], {}, {"source": "bad", "method_kind": "torch_cpu",
                                          "authorization": {"kind": "oracle_method"}},
                   {"source": "single_api", "method_kind": "torch_cpu",
                    "authorization": {"kind": "oracle_method"}, "taskdoc_snapshot": {"sha256": "tooshort"}}):
            spec, cs, ev = self._triple()
            spec["golden"] = sg                       # 键在、值畸形（区别于「不设键」的 legacy）
            o = V.validate(spec, cs, ev)["overall"]
            self.assertEqual(o["verdict"], "blocked_golden_unauthorized", f"spec.golden={sg!r} 应 fail-closed")

    def test_inconsistent_av_across_cases_blocks(self):
        """多 case 声明同一 golden 但 authorization_verified 一真一假 → 核验结果只能有一个真相 → fail-closed。
        （codex High #2：pre-reconcile 去重会按顺序丢掉其中一个，让裁决受攻击者控制的 case 顺序影响。）"""
        spec, cs, ev = self._triple()
        c0 = cs["cases"][0]
        c1 = copy.deepcopy(c0); c1["id"] = "c1"
        c1["expected"]["golden_tier"] = dict(c0["expected"]["golden_tier"], authorization_verified=False)
        cs["cases"].append(c1)
        ev["evidence"].append(dict(ev["evidence"][0], case_id="c1"))
        self.assertEqual(V.validate(spec, cs, ev)["overall"]["verdict"], "blocked_golden_unauthorized")

    def test_validate_never_raises_on_malformed_golden(self):
        """`validate()` 是 total function：畸形 golden（非 dict authorization/taskdoc、不可哈希 tier 字段）
        一律返回裁决、绝不抛异常逃出（codex Medium #5/#6：AttributeError/TypeError 曾直接逃出 validate）。"""
        cases = [
            lambda s, c, e: s.__setitem__("golden", {"source": "single_api", "method_kind": "torch_cpu",
                "authorization": "notdict", "taskdoc_snapshot": {"sha256": "a" * 64}}),
            lambda s, c, e: s.__setitem__("golden", {"source": "single_api", "method_kind": "torch_cpu",
                "authorization": {"kind": "oracle_method"}, "taskdoc_snapshot": "notdict"}),
            lambda s, c, e: c["cases"][0]["expected"]["golden_tier"].__setitem__("tier", []),
            lambda s, c, e: c["cases"][0]["expected"]["golden_tier"].__setitem__("blocked_reason", {"x": 1}),
        ]
        for i, mut in enumerate(cases):
            spec, cs, ev = self._triple()
            mut(spec, cs, ev)
            try:
                V.validate(spec, cs, ev)
            except Exception as ex:                   # noqa: BLE001 — 正是本测试要证的
                self.fail(f"case {i}: validate 抛了 {type(ex).__name__}: {ex}（承诺 total，不得逃逸）")

    def test_residual_boundary_documented(self):
        """**残余边界的显式记录**：av=True + snapshot_sha 与 spec 一致 → pass。

        这是 documented 边界不是遗漏：validator 纯函数无法复现「读快照逐字核引文」，
        攻击者若能把 av 改 True **且** snapshot_sha 保持与 spec 一致（要求真快照在场、sha 对），
        就钻进了「引文不逐字」那条窄缝。把它钉成测试，是为了让任何人想收窄它时，
        这条 assert 会提醒他边界在哪、以及为什么当前停在这。"""
        spec, cs, ev = self._triple()   # av=True、sha 与 spec 一致
        self.assertEqual(V.validate(spec, cs, ev)["overall"]["verdict"], "pass",
                         "此为 documented 残余边界；要收窄须让 validator 读快照（口径 B）或引签名")


class ScaledCasesSurfacedInVerdictTest(unittest.TestCase):
    """G4 连带（2026-07-23）：**降过规模的 case 必须出现在裁决里**。

    不带出来的话，caseset 的 `golden_cost` 账本里明明记着「这条的目标规模没跑」，
    裁决与报告却只字不提 —— 下游据裁决写「已覆盖大 shape」就成了没根据的话。
    ⚠ 带出来 **≠ 判失败**：降规模是显式、有账的取舍；但**必须在结论里可见**，
    由人/门决定它够不够。"""

    _LEDGER = [{"case_id": "c0", "from": [1024, 1024], "to": [64, 128],
                "reason": "golden 规模预算"}]

    def test_ledger_is_transparently_carried_into_verdict(self):
        spec, cs, ev = _v_triple()
        cs["golden_cost"] = {"scaled_cases": list(self._LEDGER)}
        vd = V.validate(spec, cs, ev)
        self.assertEqual(vd["overall"]["scaled_cases"], self._LEDGER)
        self.assertEqual(vd["overall"]["counts"]["scaled"], 1)
        # 不改变裁决本身——降规模是取舍、不是失败
        self.assertEqual(vd["overall"]["verdict"], "pass", vd["overall"])

    def test_absent_ledger_yields_empty_not_error(self):
        """没账本（elementwise 常态）→ 空列表、计数 0，**行为零变更**。"""
        spec, cs, ev = _v_triple()
        vd = V.validate(spec, cs, ev)
        self.assertEqual(vd["overall"]["scaled_cases"], [])
        self.assertEqual(vd["overall"]["counts"]["scaled"], 0)

    def test_malformed_ledger_is_tolerated_not_fatal(self):
        """账本形状不对 → 视为无降规模，**不报错**。

        它是**透传的记账**、不是判定依据；为一份坏账本把整个裁决判死是过度反应。
        真正的判定（精度/性能）不依赖它。"""
        for bad in ({"scaled_cases": "nope"}, {"scaled_cases": None}, "not-a-dict", 123):
            spec, cs, ev = _v_triple()
            cs["golden_cost"] = bad
            vd = V.validate(spec, cs, ev)
            self.assertEqual(vd["overall"]["scaled_cases"], [], repr(bad))
            self.assertEqual(vd["overall"]["verdict"], "pass", repr(bad))


class OutShapeReconcileTest(unittest.TestCase):
    """C1 下游：caseset 声明显式输出形状（per-op golden.py `out_shape` 派生）后，validator 按它对账；
    NPU 实际输出形状/规模 ≠ 期望 → fail-closed 判失败并说清差异，**绝不静默 reshape / 广播凑合**。"""

    def _vd(self, **kw):
        return V.validate(*_v_triple(**kw))

    # --- 不误伤：缺省 elementwise 语义（未声明 out_shape）行为零变更 ---
    def test_no_declaration_unchanged(self):
        vd = self._vd()
        self.assertEqual(vd["overall"]["verdict"], "pass", vd)
        self.assertEqual(vd["overall"]["counts"]["contract_problems"], 0)

    def test_declared_shape_consistent_passes(self):
        """声明 out_shape=[2,8]（numel 16，与 metrics.numel 一致）+ evidence 自报同形 → 放行（不误挡）。"""
        vd = self._vd(out_shape=[2, 8], ev_out_shape=[2, 8], numel=16)
        self.assertEqual(vd["overall"]["verdict"], "pass", vd)

    # --- 核心负例：形状不符 → fail 且报清「实际 vs 期望」 ---
    def test_actual_shape_differs_fails_with_diff(self):
        vd = self._vd(out_shape=[2, 8], ev_out_shape=[8, 2], numel=16)
        self.assertEqual(vd["overall"]["verdict"], "fail", vd)
        why = vd["per_case"][0]["判据"]
        self.assertIn("[8, 2]", why)          # 实际
        self.assertIn("[2, 8]", why)          # 期望
        self.assertIn("形状", why)

    def test_provenance_layer_shape_differs_fails(self):
        """实际形状写在 evidence.precision.provenance 层同样对账（换个入口不放行）。"""
        vd = self._vd(out_shape=[4, 4], ev_prov_shape=[16], numel=16)
        self.assertEqual(vd["overall"]["verdict"], "fail", vd)
        self.assertIn("形状", vd["per_case"][0]["判据"])

    def test_evidence_two_layers_conflict_fails(self):
        """evidence 两层自报形状互相打架 → 拒（不静默择一）。"""
        vd = self._vd(out_shape=[4, 4], ev_out_shape=[4, 4], ev_prov_shape=[16], numel=16)
        self.assertEqual(vd["overall"]["verdict"], "fail", vd)
        self.assertIn("不一致", vd["per_case"][0]["判据"])

    def test_numel_mismatch_fails_even_without_actual_shape(self):
        """采集层还没产实际形状字段时，也要靠现成的 metrics.numel 抓到规模不符（本轮的主生效路径）。"""
        vd = self._vd(out_shape=[2, 8], numel=100)
        self.assertEqual(vd["overall"]["verdict"], "fail", vd)
        why = vd["per_case"][0]["判据"]
        self.assertIn("100", why)
        self.assertIn("16", why)

    def test_declared_shape_bad_type_fails(self):
        """out_shape 是坏值（含负数/非 int）→ 拒，不猜、不容忍。"""
        vd = self._vd(out_shape=[2, -8], numel=16)
        self.assertEqual(vd["overall"]["verdict"], "fail", vd)
        self.assertIn("非法", vd["per_case"][0]["判据"])

    def test_conflicting_expected_keys_fails(self):
        """caseset 同时写 out_shape 与 output_shape 且不一致 → 拒（不静默择一）。"""
        vd = self._vd(out_shape=[2, 8], numel=16, extra_exp={"output_shape": [16]})
        self.assertEqual(vd["overall"]["verdict"], "fail", vd)
        self.assertIn("矛盾", vd["per_case"][0]["判据"])

    def test_evidence_shape_without_declaration_checked_against_default(self):
        """caseset 未声明期望、evidence 却自报了实际形状 → 用缺省 elementwise 语义（输入形状）兜底对账，
        不符即拒——不让一个无人对账的自报形状溜过去。"""
        vd = self._vd(ev_out_shape=[4, 4], in_shape=(16,), numel=16)
        self.assertEqual(vd["overall"]["verdict"], "fail", vd)
        self.assertIn("[4, 4]", vd["per_case"][0]["判据"])

    def test_evidence_shape_matching_default_passes(self):
        vd = self._vd(ev_out_shape=[16], in_shape=(16,), numel=16)
        self.assertEqual(vd["overall"]["verdict"], "pass", vd)

    def test_case_level_declaration_also_reconciled(self):
        """期望形状落在 case 层（而非 expected 层）同样对账——字段落点未最终对齐，两处都收。"""
        spec, cs, ev = _v_triple(ev_out_shape=[8, 2], numel=16)
        cs["cases"][0]["out_shape"] = [2, 8]
        vd = V.validate(spec, cs, ev)
        self.assertEqual(vd["overall"]["verdict"], "fail", vd)
        self.assertIn("[8, 2]", vd["per_case"][0]["判据"])

    def test_case_and_expected_declarations_conflict_fails(self):
        spec, cs, ev = _v_triple(out_shape=[2, 8], numel=16)
        cs["cases"][0]["out_shape"] = [16]
        vd = V.validate(spec, cs, ev)
        self.assertEqual(vd["overall"]["verdict"], "fail", vd)
        self.assertIn("不一致", vd["per_case"][0]["判据"])


# --- C4 的合法 gap 样板：有据可查（任务书原文 + op_def 出处 + op_def 自报支持集）---
def _gap(dtypes=("bfloat16",), op_def_dtypes=("float32", "float16"),
         task_doc_ref="任务书 §2 数据类型：float32/float16/bfloat16",
         op_def_ref="<ops 仓>/<op>/op_def.py AICore().AddConfig(...) DataType 列", **over):
    g = {"kind": "dtype_unsupported_by_op_def", "dtypes": list(dtypes),
         "op_def_dtypes": list(op_def_dtypes),
         "task_doc_ref": task_doc_ref, "op_def_ref": op_def_ref}
    g.update(over)
    return g


# --- 第三类 `dtype_unsupported_on_target_hw` 的合法 gap 样板（im2col bool 撞出）：op_def **声明了** bool、
#     但目标硬件那支 aclnn 实现（A2/A3 非 regbase 分支 DTYPE_SUPPORT_LIST）**没有** bool。方向与 C4 相反：
#     op_def_dtypes **须含** gap dtype、impl_dtypes **须不含**。---
def _hwgap(dtypes=("bool",), op_def_dtypes=("float32", "float16", "bool"),
           impl_dtypes=("float32", "float16"),
           task_doc_ref="任务书 §2 数据类型：float32/float16/bool",
           op_def_ref="<ops>/im2col/im2col_def.cpp VALUE_DATA_TYPE_LIST 含 DT_BOOL",
           impl_ref="<ops>/im2col/aclnn_im2col.cpp:222-225 非 regbase 分支 DTYPE_SUPPORT_LIST",
           target_hw="Atlas A2/A3（非 regbase 分支）", **over):
    g = {"kind": "dtype_unsupported_on_target_hw", "dtypes": list(dtypes),
         "op_def_dtypes": list(op_def_dtypes), "impl_dtypes": list(impl_dtypes),
         "task_doc_ref": task_doc_ref, "op_def_ref": op_def_ref,
         "impl_ref": impl_ref, "target_hw": target_hw}
    g.update(over)
    return g


class DtypeConflictGapVerdictTest(unittest.TestCase):
    """C4：任务书要求、op_def 不支持的 dtype 差额 → 挂 task_pr_gaps、裁决落 passed_with_gaps。
    **反后门**：gap 无据 / 自相矛盾 / 想罩住「实现了但跑挂了」/ caseset 私塞 → 一律 contract fail。"""

    def test_valid_gap_yields_passed_with_gaps(self):
        g = _gap()
        vd = V.validate(*_v_triple(spec_gaps=[g], cs_gaps=[g],
                                   dtype_required=["float32", "float16", "bfloat16"]))
        self.assertEqual(vd["overall"]["verdict"], "passed_with_gaps", vd)
        self.assertEqual(vd["overall"]["counts"]["gaps"], 1)
        # 有据可查随裁决一起带出，人工复核能直接读到出处
        self.assertIn("task_doc_ref", vd["overall"]["gaps"][0])
        self.assertIn("op_def_ref", vd["overall"]["gaps"][0])

    def test_no_gap_stays_pass(self):
        vd = V.validate(*_v_triple(dtype_required=["float32"]))
        self.assertEqual(vd["overall"]["verdict"], "pass", vd)

    # --- 负例①：无据（缺任务书/op_def 出处）---
    def test_gap_missing_task_doc_ref_rejected(self):
        g = _gap(); g.pop("task_doc_ref")
        vd = V.validate(*_v_triple(spec_gaps=[g], cs_gaps=[g],
                                   dtype_required=["float32", "bfloat16"]))
        self.assertEqual(vd["overall"]["verdict"], "fail", vd)
        self.assertTrue(any("task_doc_ref" in p for p in vd["contract_problems"]), vd)

    def test_gap_missing_op_def_ref_rejected(self):
        g = _gap(op_def_ref="   ")               # 空白串不算出处
        vd = V.validate(*_v_triple(spec_gaps=[g], cs_gaps=[g],
                                   dtype_required=["float32", "bfloat16"]))
        self.assertEqual(vd["overall"]["verdict"], "fail", vd)
        self.assertTrue(any("op_def_ref" in p for p in vd["contract_problems"]), vd)

    # --- 负例②：自相矛盾（声称不支持却又列在自报 op_def_dtypes 里）---
    def test_gap_self_contradiction_rejected(self):
        g = _gap(dtypes=["bfloat16"], op_def_dtypes=["float32", "bfloat16"])
        vd = V.validate(*_v_triple(spec_gaps=[g], cs_gaps=[g],
                                   dtype_required=["float32", "bfloat16"]))
        self.assertEqual(vd["overall"]["verdict"], "fail", vd)
        self.assertTrue(any("自相矛盾" in p for p in vd["contract_problems"]), vd)

    # --- 负例③（最关键）：拿「没实现」罩住「实现了但跑挂了」---
    def test_gap_cannot_cover_a_dtype_that_actually_runs(self):
        """float32 明明有真实用例在跑，却挂「op_def 不支持 float32」想免检 → 拒。
        这就是「没实现」与「实现了但跑挂了」的判别式：后者一定有用例 + evidence。"""
        g = _gap(dtypes=["float32"], op_def_dtypes=["float16"])
        vd = V.validate(*_v_triple(spec_gaps=[g], cs_gaps=[g], dtype="float32",
                                   dtype_required=["float32", "float16"]))
        self.assertEqual(vd["overall"]["verdict"], "fail", vd)
        self.assertTrue(any("跑挂了" in p for p in vd["contract_problems"]), vd)

    # --- 负例④：给任务书没要求的 dtype 挂账 ---
    def test_gap_outside_dtype_required_rejected(self):
        g = _gap(dtypes=["complex64"])
        vd = V.validate(*_v_triple(spec_gaps=[g], cs_gaps=[g],
                                   dtype_required=["float32", "float16"]))
        self.assertEqual(vd["overall"]["verdict"], "fail", vd)
        self.assertTrue(any("dtype_required" in p for p in vd["contract_problems"]), vd)

    # --- 负例⑤：caseset 私塞 gap（权威在 spec）---
    def test_caseset_smuggled_gap_rejected(self):
        g = _gap()
        vd = V.validate(*_v_triple(cs_gaps=[g], dtype_required=["float32", "bfloat16"]))
        self.assertEqual(vd["overall"]["verdict"], "fail", vd)
        self.assertTrue(any("私改/私塞" in p for p in vd["contract_problems"]), vd)

    def test_spec_gap_dropped_by_caseset_rejected(self):
        """反向：spec 有 gap 而 caseset 透传时抹掉 → 同样拒（两侧必须逐条一致）。"""
        g = _gap()
        vd = V.validate(*_v_triple(spec_gaps=[g], cs_gaps=[],
                                   dtype_required=["float32", "bfloat16"]))
        self.assertEqual(vd["overall"]["verdict"], "fail", vd)

    def test_freetext_gaps_ignored_not_error(self):
        """历史的自由文本 task_pr_gaps（现有 sample spec 就是）原样忽略、不误报。"""
        txt = ["精度阈值待工具核实", "uint8 回绕语义未覆盖"]
        vd = V.validate(*_v_triple(spec_gaps=txt, cs_gaps=txt, dtype_required=["float32"]))
        self.assertEqual(vd["overall"]["verdict"], "pass", vd)

    # --- gap 不吞掉更严的终态 ---
    def test_gap_does_not_swallow_precision_fail(self):
        """同时有合法 gap 和真实精度 fail → 仍是 fail（gap 只在「其余全过」时才降级为 passed_with_gaps）。"""
        g = _gap()
        spec, cs, ev = _v_triple(spec_gaps=[g], cs_gaps=[g],
                                 dtype_required=["float32", "bfloat16"])
        ev["evidence"][0]["precision"]["metrics"]["bad_count"] = 999
        if "acceptance_metrics" in ev["evidence"][0]["precision"]:
            ev["evidence"][0]["precision"]["acceptance_metrics"]["bad_count"] = 999
        vd = V.validate(spec, cs, ev)
        self.assertEqual(vd["overall"]["verdict"], "fail", vd)


class GateDtypeConflictGapTest(unittest.TestCase):
    """门侧（task1 覆盖门 / task2 裁决交叉核验）对 C4 gap 的硬校。"""
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _errs(self, stage):
        errs = []
        G._GATES[stage](self.d, errs)
        return errs

    def _cs(self, gaps, required=("float32", "float16", "bfloat16")):
        cs = json.loads(json.dumps(CASESET))          # 真实用例 dtype = {float32, float16}
        cs["dtype_required"] = list(required)
        cs["task_pr_gaps"] = gaps
        return cs

    def test_valid_unsupported_gap_accounts_coverage(self):
        """bfloat16 任务书要、op_def 不支持、gap 有据 → 非静默收窄 → 覆盖门放行。"""
        _w(self.d, "caseset.json", self._cs([_gap()]))
        self.assertEqual(self._errs("task1"), [])

    def test_unsupported_gap_missing_refs_blocks(self):
        g = _gap(); g.pop("op_def_ref")
        _w(self.d, "caseset.json", self._cs([g]))
        errs = self._errs("task1")
        self.assertTrue(any("op_def_ref" in e for e in errs), errs)
        self.assertTrue(any("dtype 覆盖不足" in e for e in errs), errs)   # 无据 → 不计入挂账 → 仍 BLOCKED

    def test_unsupported_gap_self_contradiction_blocks(self):
        _w(self.d, "caseset.json", self._cs([_gap(op_def_dtypes=["float32", "bfloat16"])]))
        errs = self._errs("task1")
        self.assertTrue(any("自相矛盾" in e for e in errs), errs)

    def test_unsupported_gap_covering_tested_dtype_blocks(self):
        """挂「op_def 不支持 float16」但 float16 有真实用例在跑 → 拒（不许拿没实现当跑挂了的借口）。"""
        _w(self.d, "caseset.json", self._cs([_gap(dtypes=["float16"], op_def_dtypes=["float32"])]))
        errs = self._errs("task1")
        self.assertTrue(any("跑挂了" in e for e in errs), errs)

    def test_unsupported_gap_outside_required_blocks(self):
        _w(self.d, "caseset.json", self._cs([_gap(dtypes=["complex64"])],
                                            required=("float32", "float16")))
        errs = self._errs("task1")
        self.assertTrue(any("dtype_required" in e for e in errs), errs)

    def test_gap_checked_even_without_dtype_required(self):
        """删掉 dtype_required 不得连带绕过 gap 硬校（同 codex#2 对 dtype_tested 的教训）。"""
        cs = json.loads(json.dumps(CASESET))
        cs["task_pr_gaps"] = [_gap(dtypes=["float32"], op_def_dtypes=["float16"])]  # 谎称在跑的 dtype 不支持
        _w(self.d, "caseset.json", cs)
        errs = self._errs("task1")
        self.assertTrue(any("跑挂了" in e for e in errs), errs)

    def test_task2_accepts_passed_with_gaps_with_backing(self):
        _w(self.d, "caseset.json", self._cs([_gap()]))
        _w(self.d, "evidence.json", _ev(self.d, ["x_000", "x_001"]))
        _w(self.d, "verdict.json", _vd("passed_with_gaps"))
        self.assertEqual(self._errs("task2"), [])

    def test_task2_valid_gap_but_clean_pass_verdict_blocks(self):
        """方向② 同样钉在 C4：合法 gap 已被覆盖门认账，verdict 却是干净 pass（validator 被绕过/手改 verdict）
        → 门 fail-closed 判 FAILED，不放行。"""
        _w(self.d, "caseset.json", self._cs([_gap()]))
        _w(self.d, "evidence.json", _ev(self.d, ["x_000", "x_001"]))
        _w(self.d, "verdict.json", _vd("pass"))
        errs = self._errs("task2")
        self.assertTrue(any("干净 pass" in e and "fail-closed" in e for e in errs), errs)

    def test_task2_no_gap_clean_pass_not_flagged(self):
        """反向不误伤：无任何 finding gap 时干净 pass 是合法的，方向② 不得记 error。"""
        _w(self.d, "caseset.json", CASESET)                  # 无 task_pr_gaps
        _w(self.d, "evidence.json", _ev(self.d, ["x_000", "x_001"]))
        _w(self.d, "verdict.json", _vd("pass"))
        self.assertEqual(self._errs("task2"), [])

    def test_task2_passed_with_gaps_without_backing_fails(self):
        """手改 verdict.json 写 passed_with_gaps、caseset 却无合法 gap → 拒（裁决自称有 gap 却无据）。"""
        _w(self.d, "caseset.json", CASESET)                  # 无任何 task_pr_gaps
        _w(self.d, "evidence.json", _ev(self.d, ["x_000", "x_001"]))
        _w(self.d, "verdict.json", _vd("passed_with_gaps"))
        errs = self._errs("task2")
        self.assertTrue(any("无据" in e for e in errs), errs)

    def test_task2_passed_with_gaps_with_forged_gap_fails(self):
        """伪造 gap（缺出处）+ verdict 写 passed_with_gaps → 门仍拒。"""
        g = _gap(); g.pop("task_doc_ref")
        _w(self.d, "caseset.json", self._cs([g]))
        _w(self.d, "evidence.json", _ev(self.d, ["x_000", "x_001"]))
        _w(self.d, "verdict.json", _vd("passed_with_gaps"))
        errs = self._errs("task2")
        self.assertTrue(any("无据" in e for e in errs), errs)


class GateTargetHwGapTest(unittest.TestCase):
    """门侧（task1 覆盖门 / task2 裁决交叉核验）对第三类 `dtype_unsupported_on_target_hw` gap 的硬校。

    与 `GateDtypeConflictGapTest`（C4）镜像、**方向相反**：C4 证「op_def 压根没声明」，本 kind 证
    「op_def 声明了、但目标硬件那支实现没有」。反后门五道硬校缺一即拒（拒 = 不计入挂账 → 仍按静默收窄 BLOCKED）。"""
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _errs(self, stage):
        errs = []
        G._GATES[stage](self.d, errs)
        return errs

    def _cs(self, gaps, required=("float32", "float16", "bool")):
        cs = json.loads(json.dumps(CASESET))          # 真实用例 dtype = {float32, float16}
        cs["dtype_required"] = list(required)
        cs["task_pr_gaps"] = gaps
        return cs

    # --- 正例：合法 target_hw gap → 计入 unsupported → 覆盖门放行 ---
    def test_valid_target_hw_gap_accounts_coverage(self):
        """bool 任务书要、op_def 声明了、目标硬件那支没实现、gap 有据 → 非静默收窄 → 覆盖门放行。"""
        _w(self.d, "caseset.json", self._cs([_hwgap()]))
        self.assertEqual(self._errs("task1"), [])

    def test_task2_accepts_passed_with_gaps_with_backing(self):
        """裁决 passed_with_gaps 由合法 target_hw gap 撑住（与 C4 同桶，交叉核验放行）。"""
        _w(self.d, "caseset.json", self._cs([_hwgap()]))
        _w(self.d, "evidence.json", _ev(self.d, ["x_000", "x_001"]))
        _w(self.d, "verdict.json", _vd("passed_with_gaps"))
        self.assertEqual(self._errs("task2"), [])

    # --- 红队 fail-open 回归钉（2026-07-23）：合法 target_hw gap + validator 干净 pass → 门必 FAILED ---
    def test_task2_valid_gap_but_clean_pass_verdict_blocks(self):
        """最关键回归：caseset 有**结构合法**的 target_hw gap（覆盖门据此认账放行），但 `validator` 侧
        尚未识别该 kind → 对这条 gap 产**干净 `pass`**。gate_task2 方向② 必须逮住「有已挂账 finding gap
        却是最低档干净 pass」→ 记 error → FAILED（fail-closed BLOCKED）。绝不让『算子未实现任务书要求的
        dtype』被机读成干净通过 / exit0 / CI 自动合并。"""
        _w(self.d, "caseset.json", self._cs([_hwgap()]))
        _w(self.d, "evidence.json", _ev(self.d, ["x_000", "x_001"]))
        _w(self.d, "verdict.json", _vd("pass"))
        errs = self._errs("task2")
        self.assertTrue(any("干净 pass" in e and "fail-closed" in e for e in errs), errs)

    # --- 负例①：缺 impl_ref（目标硬件实现出处）→ 拒 + 仍 BLOCKED ---
    def test_missing_impl_ref_blocks(self):
        g = _hwgap(); g.pop("impl_ref")
        _w(self.d, "caseset.json", self._cs([g]))
        errs = self._errs("task1")
        self.assertTrue(any("impl_ref" in e for e in errs), errs)
        self.assertTrue(any("dtype 覆盖不足" in e for e in errs), errs)   # 无据 → 不计入挂账 → 仍 BLOCKED

    # --- 负例①b：缺 target_hw（哪支硬件）→ 拒 ---
    def test_missing_target_hw_blocks(self):
        g = _hwgap(target_hw="   ")               # 空白串不算出处
        _w(self.d, "caseset.json", self._cs([g]))
        errs = self._errs("task1")
        self.assertTrue(any("target_hw" in e for e in errs), errs)

    # --- 负例②：op_def_dtypes 不含 gap（op_def 其实没声明）→ 拒（该走 C4）---
    def test_op_def_not_declaring_gap_blocks(self):
        """bool 不在自报 op_def_dtypes 里 → 说明 op_def 压根没声明 → 属 C4，本 kind 拒。"""
        _w(self.d, "caseset.json",
           self._cs([_hwgap(op_def_dtypes=["float32", "float16"])]))   # 缺 bool
        errs = self._errs("task1")
        self.assertTrue(any("dtype_unsupported_by_op_def" in e and "没声明" in e for e in errs), errs)
        self.assertTrue(any("dtype 覆盖不足" in e for e in errs), errs)

    # --- 负例③：impl_dtypes 含 gap（目标硬件其实实现了）→ 拒（自相矛盾/伪造）---
    def test_impl_declaring_gap_blocks(self):
        _w(self.d, "caseset.json",
           self._cs([_hwgap(impl_dtypes=["float32", "float16", "bool"])]))   # bool 竟在 impl 支持集里
        errs = self._errs("task1")
        self.assertTrue(any("自相矛盾" in e for e in errs), errs)

    # --- 负例④（最关键）：gap dtype 有真实用例在跑 → 拒（属「实现了但跑挂了」）---
    def test_gap_covering_running_dtype_blocks(self):
        """挂「目标硬件不支持 float16」但 float16 有真实用例在跑 → 拒。
        判别式：没实现 → 造不出用例；实现了但跑挂 → 一定有用例 + evidence，须走精度/功能裁决。"""
        _w(self.d, "caseset.json",
           self._cs([_hwgap(dtypes=["float16"], op_def_dtypes=["float32", "float16"],
                            impl_dtypes=["float32"])],
                    required=("float32", "float16")))
        errs = self._errs("task1")
        self.assertTrue(any("跑挂了" in e for e in errs), errs)

    # --- 负例⑤：给任务书没要求的 dtype 挂账 ---
    def test_gap_outside_dtype_required_blocks(self):
        _w(self.d, "caseset.json",
           self._cs([_hwgap(dtypes=["complex64"],
                            op_def_dtypes=["float32", "float16", "complex64"],
                            impl_dtypes=["float32", "float16"])],
                    required=("float32", "float16")))
        errs = self._errs("task1")
        self.assertTrue(any("dtype_required" in e for e in errs), errs)

    def test_gap_checked_even_without_dtype_required(self):
        """删掉 dtype_required 不得连带绕过 target_hw gap 硬校（同 codex#2 对 dtype_tested 的教训）。"""
        cs = json.loads(json.dumps(CASESET))
        # 谎称在跑的 float16 目标硬件不支持——即便无 dtype_required，「跑挂了」硬校仍须行使
        cs["task_pr_gaps"] = [_hwgap(dtypes=["float16"], op_def_dtypes=["float32", "float16"],
                                     impl_dtypes=["float32"])]
        _w(self.d, "caseset.json", cs)
        errs = self._errs("task1")
        self.assertTrue(any("跑挂了" in e for e in errs), errs)

    def test_task2_forged_target_hw_gap_fails(self):
        """伪造 target_hw gap（缺 impl_ref）+ verdict 写 passed_with_gaps → 门仍拒（自称有 gap 却无据）。"""
        g = _hwgap(); g.pop("impl_ref")
        _w(self.d, "caseset.json", self._cs([g]))
        _w(self.d, "evidence.json", _ev(self.d, ["x_000", "x_001"]))
        _w(self.d, "verdict.json", _vd("passed_with_gaps"))
        errs = self._errs("task2")
        self.assertTrue(any("无据" in e for e in errs), errs)

    # --- 抗坏输入：畸形字段不得让门崩溃（validate total，与 C4 同规矩）---
    def test_malformed_fields_do_not_crash(self):
        """op_def_dtypes/impl_dtypes/各 ref 字段类型全错 → 门累计 error、判 BLOCKED，绝不抛异常逃出。"""
        bad = {"kind": "dtype_unsupported_on_target_hw", "dtypes": ["bool"],
               "op_def_dtypes": {"not": "a list"}, "impl_dtypes": 5,
               "task_doc_ref": 123, "op_def_ref": None, "impl_ref": ["x"], "target_hw": {}}
        _w(self.d, "caseset.json", self._cs([bad]))
        errs = self._errs("task1")                    # 不抛异常即证 total
        self.assertTrue(errs)                         # 畸形 → 拒 → 非空 error（走 BLOCKED）

    def test_malformed_dtypes_do_not_crash(self):
        """dtypes 本身非法（非 list / 元素非 str）→ 早退记 error、不崩、不计入挂账。"""
        for bad_dtypes in ("bool", [1, 2], [], None):
            g = {"kind": "dtype_unsupported_on_target_hw", "dtypes": bad_dtypes,
                 "op_def_dtypes": ["float32", "float16", "bool"], "impl_dtypes": ["float32", "float16"],
                 "task_doc_ref": "t", "op_def_ref": "o", "impl_ref": "i", "target_hw": "hw"}
            _w(self.d, "caseset.json", self._cs([g]))
            errs = self._errs("task1")                # 不抛异常即证 total
            self.assertTrue(any("dtypes" in e for e in errs), (bad_dtypes, errs))


class PassedWithGapsWiringTest(unittest.TestCase):
    """C4 最危险的拼接点：`passed_with_gaps` 从 validator 一路走到 `run_workflow` 的终态/退出码。

    **为什么单开一类**：这条链断过一次——validator 与门都认，`run_workflow` 不认，实测落成
    `state='PASSED'` / exit 0，「算子没实现任务书要求的 dtype」被机读成干净通过、CI 可自动合并。
    修完若无回归钉住，随时能再退化且**测试仍全绿**（因为别处覆盖不到这一段）。
    这里直接钉 `run_workflow` 的三个纯函数，不依赖真跑，稳定且快。"""

    def test_exit_code_is_2_not_0(self):
        """**最关键一条**：绝不能回 0——0 = 干净通过 = CI 可自动合并。"""
        import run_workflow as W
        self.assertEqual(W._exit_code("PASSED_WITH_GAPS"), 2)
        self.assertEqual(W._exit_code("PASSED_WITH_RISK"), 2)      # 对照：同档
        self.assertEqual(W._exit_code("PASS"), 0)                  # 对照：干净 PASS 才是 0

    def test_canonical_state_maps_through(self):
        """人读 overall → 机读 state 必须映射到同名终态，别掉进兜底分支。"""
        import run_workflow as W
        self.assertIn("PASSED_WITH_GAPS", W._STATE_MAP)
        self.assertEqual(W._STATE_MAP["PASSED_WITH_GAPS"], "PASSED_WITH_GAPS")

    def test_precision_ok_whitelist_includes_gaps(self):
        """`passed_with_gaps` 的精度本身是**全过**的 → 必须继续跑 Task3。

        漏掉它的后果不是「少跑性能」这么轻：归因会错成「spec 声明性能目标但无性能用例」，
        或在无性能要求时直接落 `PASS(无性能要求)`——两种都掩盖了真实结论。"""
        import inspect, run_workflow as W
        src = inspect.getsource(W.run)
        line = next(l for l in src.splitlines() if "precision_ok =" in l)
        for v in ("pass", "passed_with_risk", "passed_with_gaps"):
            self.assertIn(f'"{v}"', line, f"precision_ok 白名单漏了 {v}：{line.strip()}")


if __name__ == "__main__":
    unittest.main()
