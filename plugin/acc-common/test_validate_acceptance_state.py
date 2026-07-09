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


# ===== T6/T8 perf 包新增（自包含，与上方 GateTest 无耦合，便于与主树 T5 干净合并）=====
class GateTask3PerfPackageTest(unittest.TestCase):
    """gate_task3 的小shape例外门(T6) + 挂起/不可比态(T8)。"""
    def setUp(self):
        self.d = tempfile.mkdtemp()

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
        r = self._run("specs/sign.spec.json", "--perf-slow", "sign_005,sign_006")
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


if __name__ == "__main__":
    unittest.main()
