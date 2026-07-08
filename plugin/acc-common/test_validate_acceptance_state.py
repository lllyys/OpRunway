"""验收门单测（stdlib unittest）——核心证明：门能挡住「跑子集报 100%」与 fail/blocked。

跑: python3 -m unittest test_validate_acceptance_state -v   （在 acc-common/ 下）
"""
import json, os, subprocess, sys, tempfile, shutil, unittest
import validate_acceptance_state as G
import check_manifest_sync as C


def _w(d, name, obj):
    with open(os.path.join(d, name), "w", encoding="utf-8") as f:
        json.dump(obj, f)


CASESET = {"op": "X", "cases": [
    {"id": "x_000", "dims": ["func"], "inputs": [{"name": "a", "shape": [16], "dtype": "float32"}],
     "expected": {"golden_path": "g0.npy", "threshold": 0.001}},
    {"id": "x_001", "dims": ["func"], "inputs": [{"name": "a", "shape": [16], "dtype": "float16"}],
     "expected": {"golden_path": "g1.npy", "threshold": 0.001}},
]}


def _ev(ids, thr=0.001):
    return {"op": "X", "evidence": [{"case_id": i, "status": "pass",
            "precision": {"threshold": thr}} for i in ids]}


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
        """adapter 偷偷放宽阈值 → 必 FAIL。"""
        _w(self.d, "caseset.json", CASESET)
        _w(self.d, "evidence.json", _ev(["x_000", "x_001"], thr=0.1))
        _w(self.d, "verdict.json", _vd("pass"))
        self.assertTrue(any("阈值" in e for e in self._errs("task2")))

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
