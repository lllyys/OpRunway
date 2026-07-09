"""验收门单测（stdlib unittest）——核心证明：门能挡住「跑子集报 100%」与 fail/blocked。

跑: python3 -m unittest test_validate_acceptance_state -v   （在 acc-common/ 下）
"""
import json, os, subprocess, sys, tempfile, shutil, unittest
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


def _ev(ids, mutate=None):
    """据 caseset.expected 构一致的 evidence.precision（含 metrics）；mutate(id)->dict 覆盖单例（造不一致）。"""
    out = []
    for i in ids:
        exp = _EXP[i]
        prec = {"standard": exp["standard"], "tolerance_policy_id": exp["tolerance_policy_id"],
                "policy": dict(exp["policy"]), "threshold": exp["threshold"],
                "metrics": {"bad_count": 0, "numel": 16}}
        if mutate:
            prec.update(mutate(i))
        out.append({"case_id": i, "status": "pass", "precision": prec})
    return {"op": "X", "evidence": out}


def _vd(v, fail=0, unc=0, cp=0):
    return {"op": "X", "overall": {"verdict": v,
            "counts": {"fail": fail, "uncertain": unc, "contract_problems": cp}}}


def _pr(status, scope="kernel_only", blocked=False):
    return {"op": "X", "per_case": [{"case_id": "x_000", "scope": scope, "blocked": blocked}],
            "summary": {"status": status, "perf_cases": 1, "达标": 1}}


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
        _w(self.d, "evidence.json", _ev(["x_000", "x_001"]))
        _w(self.d, "verdict.json", _vd("pass"))
        self.assertEqual(self._errs("task2"), [])

    def test_task2_subset_fails(self):
        """跑子集报 100%：caseset 2 例、evidence 只 1 例 → 必 FAIL。"""
        _w(self.d, "caseset.json", CASESET)
        _w(self.d, "evidence.json", _ev(["x_000"]))
        _w(self.d, "verdict.json", _vd("pass"))
        self.assertTrue(any("跑子集" in e for e in self._errs("task2")))

    def test_task2_legit_fail_not_blocked(self):
        """合法精度 fail（证据完整）→ 门不挡：真因由 verdict 表达，不该被门盖成 BLOCKED。"""
        _w(self.d, "caseset.json", CASESET)
        _w(self.d, "evidence.json", _ev(["x_000", "x_001"]))
        _w(self.d, "verdict.json", _vd("fail", fail=1))
        self.assertEqual(self._errs("task2"), [])

    def test_task2_needs_review_not_blocked(self):
        _w(self.d, "caseset.json", CASESET)
        _w(self.d, "evidence.json", _ev(["x_000", "x_001"]))
        _w(self.d, "verdict.json", _vd("needs_review", unc=1))
        self.assertEqual(self._errs("task2"), [])

    def test_task2_contract_problems_fails(self):
        """validator 标契约破损 → 证据不可信 → 门挡。"""
        _w(self.d, "caseset.json", CASESET)
        _w(self.d, "evidence.json", _ev(["x_000", "x_001"]))
        _w(self.d, "verdict.json", _vd("pass", cp=2))
        self.assertTrue(any("契约" in e for e in self._errs("task2")))

    def test_task2_threshold_mismatch_fails(self):
        """adapter 偷偷放宽阈值 digest → 必 FAIL。"""
        _w(self.d, "caseset.json", CASESET)
        _w(self.d, "evidence.json", _ev(["x_000", "x_001"],
                                        mutate=lambda i: {"threshold": 0.1}))
        _w(self.d, "verdict.json", _vd("pass"))
        self.assertTrue(any("防放宽" in e and "threshold" in e for e in self._errs("task2")))

    def test_task2_policy_mismatch_fails(self):
        """三处不一致：evidence 结构化 policy 被放宽（error_rate 抬高）→ 必 FAIL。"""
        _w(self.d, "caseset.json", CASESET)
        looser = {"kind": "ascendoptest_default", "tolerance": 0.0001, "error_rate": 0.5,
                  "eps": 1e-9, "legacy": 0.1, "not_settled": False}
        _w(self.d, "evidence.json", _ev(["x_000", "x_001"],
                                        mutate=lambda i: {"policy": looser} if i == "x_000" else {}))
        _w(self.d, "verdict.json", _vd("pass"))
        self.assertTrue(any("policy" in e and "防放宽" in e for e in self._errs("task2")))

    def test_task2_missing_tolerance_policy_id_fails(self):
        """evidence 缺 tolerance_policy_id（口径不可追溯）→ 必 FAIL。"""
        _w(self.d, "caseset.json", CASESET)
        ev = _ev(["x_000", "x_001"])
        del ev["evidence"][0]["precision"]["tolerance_policy_id"]
        _w(self.d, "evidence.json", ev)
        _w(self.d, "verdict.json", _vd("pass"))
        self.assertTrue(any("tolerance_policy_id" in e for e in self._errs("task2")))

    def test_task2_caseset_missing_tolerance_policy_id_fails(self):
        """finding #12/#16：caseset expected 缺 tolerance_policy_id → 三处一致门失效 → 必 FAIL。"""
        cs = json.loads(json.dumps(CASESET))
        del cs["cases"][0]["expected"]["tolerance_policy_id"]
        _w(self.d, "caseset.json", cs)
        _w(self.d, "evidence.json", _ev(["x_000", "x_001"]))
        _w(self.d, "verdict.json", _vd("pass"))
        self.assertTrue(any("caseset expected 缺 tolerance_policy_id" in e for e in self._errs("task2")))

    def test_task2_caseset_missing_policy_fails(self):
        """finding #12/#16：caseset expected 缺结构化 policy → 必 FAIL。"""
        cs = json.loads(json.dumps(CASESET))
        del cs["cases"][0]["expected"]["policy"]
        _w(self.d, "caseset.json", cs)
        _w(self.d, "evidence.json", _ev(["x_000", "x_001"]))
        _w(self.d, "verdict.json", _vd("pass"))
        self.assertTrue(any("caseset expected 缺 policy" in e for e in self._errs("task2")))

    def test_task2_three_way_inconsistent_fails(self):
        """finding #12/#16：三处不一致（evidence policy 放宽 error_rate）→ 必 FAIL。"""
        _w(self.d, "caseset.json", CASESET)
        looser = dict(_POL32); looser["error_rate"] = 0.9
        _w(self.d, "evidence.json", _ev(["x_000", "x_001"],
                                        mutate=lambda i: {"policy": looser} if i == "x_000" else {}))
        _w(self.d, "verdict.json", _vd("pass"))
        self.assertTrue(any("防放宽" in e and "policy" in e for e in self._errs("task2")))

    def test_task2_bad_verdict_enum_fails(self):
        """finding #14：overall.verdict 非合法枚举 → 必 FAIL。"""
        _w(self.d, "caseset.json", CASESET)
        _w(self.d, "evidence.json", _ev(["x_000", "x_001"]))
        _w(self.d, "verdict.json", {"op": "X", "overall": {"verdict": "weird",
            "counts": {"fail": 0, "uncertain": 0, "contract_problems": 0}}})
        self.assertTrue(any("非法" in e for e in self._errs("task2")))

    def test_task2_counts_non_int_fails(self):
        """finding #14：counts.fail 非整数 → 必 FAIL。"""
        _w(self.d, "caseset.json", CASESET)
        _w(self.d, "evidence.json", _ev(["x_000", "x_001"]))
        _w(self.d, "verdict.json", {"op": "X", "overall": {"verdict": "pass",
            "counts": {"fail": "0", "uncertain": 0, "contract_problems": 0}}})
        self.assertTrue(any("非整数" in e for e in self._errs("task2")))

    def test_task2_nonlist_cases_fails(self):
        """finding #13：cases 非列表 → 直接 FAILED（不静默兜成空列表放过）。"""
        _w(self.d, "caseset.json", {"op": "X", "cases": None})
        _w(self.d, "evidence.json", _ev(["x_000", "x_001"]))
        _w(self.d, "verdict.json", _vd("pass"))
        self.assertTrue(any("非列表" in e for e in self._errs("task2")))

    def test_task1_null_shape_no_crash(self):
        """finding #15：inputs[0].shape 为 null → 不崩、记 error（list(None) 会 TypeError）。"""
        cs = json.loads(json.dumps(CASESET))
        cs["cases"][0]["inputs"][0]["shape"] = None
        _w(self.d, "caseset.json", cs)
        errs = self._errs("task1")          # 不抛异常
        self.assertTrue(any("shape" in e for e in errs))

    # --- task3 ---
    def test_task3_ok(self):
        _w(self.d, "perf_report.json", _pr("ok"))
        self.assertEqual(self._errs("task3"), [])

    def test_task3_blocked_fails(self):
        _w(self.d, "perf_report.json", _pr("blocked"))
        self.assertTrue(self._errs("task3"))

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
        _w(self.d, "evidence.json", _ev(["x_000", "x_001"]))
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
        self.assertNotEqual(self._run("--defect", "isclose_000").returncode, 0)


if __name__ == "__main__":
    unittest.main()
