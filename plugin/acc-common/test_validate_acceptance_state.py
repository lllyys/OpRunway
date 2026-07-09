"""验收门单测（stdlib unittest）——核心证明：门能挡住「跑子集报 100%」与 fail/blocked。

跑: python3 -m unittest test_validate_acceptance_state -v   （在 acc-common/ 下）
"""
import json, os, subprocess, sys, tempfile, shutil, unittest
import numpy as np
import precision_policy
import validate_acceptance_state as G
import check_manifest_sync as C


def _w(d, name, obj):
    with open(os.path.join(d, name), "w", encoding="utf-8") as f:
        json.dump(obj, f)


# T5：结构化口径（standard + tolerance_policy_id + 结构化 policy + threshold digest）
_POL32 = {"kind": "ascendoptest_default", "tolerance": 0.0001, "error_rate": 0.0001,
          "eps": 1e-9, "legacy": 0.1, "not_settled": False}
_POL16 = {"kind": "ascendoptest_default", "tolerance": 0.001, "error_rate": 0.001,
          "eps": 1e-9, "legacy": 0.1, "not_settled": False}
_EXP = {
    "x_000": {"golden_path": "g0.npy", "threshold": 0.0001, "standard": "ascendoptest_default",
              "tolerance_policy_id": "ascendoptest_default:float32", "policy": _POL32},
    "x_001": {"golden_path": "g1.npy", "threshold": 0.001, "standard": "ascendoptest_default",
              "tolerance_policy_id": "ascendoptest_default:float16", "policy": _POL16},
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
    return {"standard": exp["standard"], "tolerance_policy_id": exp["tolerance_policy_id"],
            "policy": dict(exp["policy"]), "threshold": exp["threshold"], "metrics": metrics,
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


class FrontmatterParserTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _fm(self, text):
        p = os.path.join(self.d, "AGENTS.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return C._parse_frontmatter(p)

    def test_block_list(self):
        self.assertEqual(self._fm("---\nname: x\nskills:\n  - a\n  - b\n---\n")["skills"], ["a", "b"])

    def test_flow_list(self):
        self.assertEqual(self._fm("---\nname: x\nskills: [a, b]\n---\n")["skills"], ["a", "b"])

    def test_comment_skipped(self):
        self.assertEqual(self._fm("---\nname: x\n# c\nskills:\n  - a\n---\n")["skills"], ["a"])

    def test_scalar_not_list(self):
        self.assertEqual(self._fm("---\nname: op-x\ndescription: hi\n---\n")["name"], "op-x")

    def test_no_frontmatter(self):
        self.assertEqual(self._fm("# just markdown\n"), {})


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
             os.path.join(self.here, "specs", "isclose.spec.json"),
             "--mode", "mock", "--out", self.d, *extra],
            capture_output=True, text=True)

    def test_clean_exit_0(self):
        self.assertEqual(self._run().returncode, 0)

    def test_defect_exit_nonzero(self):
        # T7 语义化稳定 id：IsClose fp32 的 (16,) 用例 = isclose_float32_16_pairfar（弃旧索引 isclose_000）
        self.assertNotEqual(self._run("--defect", "isclose_float32_16_pairfar").returncode, 0)


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

    def test_perf_slow_passed_with_risk(self):
        # T7 语义化稳定 id：Sign 的两个小 shape 性能用例 = sign_float32_{64,256}_perfsmall（弃旧索引 sign_005/006）
        r = self._run("specs/sign.spec.json", "--perf-slow",
                      "sign_float32_64_perfsmall,sign_float32_256_perfsmall")
        self.assertEqual(r.returncode, 2)                       # PASSED_WITH_RISK
        acc = self._json("acceptance.json")
        self.assertEqual(acc["state"], "PASSED_WITH_RISK")
        self.assertEqual(acc["human_cp"]["status"], "pending")
        self.assertEqual(acc["repo_mode"], "mock")
        pr = self._json("perf_report.json")
        self.assertEqual(pr["summary"]["status"], "exception")
        self.assertTrue(os.path.exists(os.path.join(self.d, "perf_sim_sign.svg")))
        self.assertEqual(self._gate("task3").returncode, 0)     # 有图+一致 → 门过
        os.remove(os.path.join(self.d, "perf_sim_sign.svg"))
        self.assertEqual(self._gate("task3").returncode, 1)     # 删图 → 门 FAILED（不静默绕过）

    def test_gpu_wait_blocked_not_fail(self):
        r = self._run("testdata/gpu_demo.spec.json")            # spec.perf.baseline=gpu_external, 无 --gpu-baseline
        acc = self._json("acceptance.json")
        self.assertEqual(acc["state"], "BLOCKED_WAIT_GPU_BENCHMARK")
        self.assertNotEqual(r.returncode, 0)                   # 非 PASS
        self.assertNotIn("PASS", acc["overall"])               # 缺 GPU 数据绝不显 PASS


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


if __name__ == "__main__":
    unittest.main()
