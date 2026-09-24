"""复测集成测试（W1 合入后补跑）：用 case-gen 真渲染的量具产出真实轮次 JSON。

与 test_retest_contract.py 的伪造 JSON 不同，这里的证据链是真产物：
`accept check` 对假 repo 骨架真跑一遍（真 check.json、真 runtime 渲染、真 manifest），
渲染出的 `verify_performance.py` 以真模块跑首轮与复测轮——只在 subprocess 边界打桩
（`_run_process`/`_gtest_evidence`/`parse_op_summary`/`_list_tests`/`_resolve_device`/
`_resolve_msprof`），量具自己的 preflight、采样循环、计分、no-clobber 提交与复测
序列化全程真实执行。豁免轮由 `accept waive` 真产出。不碰真机、不起 NPU 进程。
"""

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import retest_fold  # noqa: E402


def _load_accept():
    name = "blas_accept_cli"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / "accept.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


accept = _load_accept()

OP = "sger"
FAMILY = "sger"  # 部署在 test/sger/arch22/ 下，family 取 test/ 后首段。
SOC = "ascend910b3"
RUN = "sger-20260917-1200"
PF = ("TC_PF_1001", "TC_PF_1002", "TC_PF_1003")
TS = "2026-09-17T00:00:00+08:00"

CSV_TEXT = (
    "case_name,description,expect_result,random_seed,m,n\n"
    "TC_L0_001,acc,PASS,1,8,8\n"
    "TC_PF_1001,perf,PASS,1,16,16\n"
    "TC_PF_1002,perf,PASS,1,32,32\n"
    "TC_PF_1003,perf,PASS,1,64,64\n"
)

# timing_scope=kernel：与 msprof kernel 口径同类，报告状态不带 scope caveat 后缀。
BASELINE_TEXT = (
    "# timing_scope=kernel\n"
    "id,m,n,gpu_ms\n"
    "sger-base-001,16,16,10.0\n"
    "sger-base-002,32,32,10.0\n"
    "sger-base-003,64,64,10.0\n"
)

# gpu_ms=10.0 → ratio = 10000 / kernel_us：8000µs→1.25(PASS)，20000µs→0.5(FAIL)。
FIRST_KERNELS = {"TC_PF_1001": 8000.0, "TC_PF_1002": 20000.0, "TC_PF_1003": 25000.0}

_MODULE_SEQ = [0]


class RetestIntegrationCase(unittest.TestCase):
    """脚手架：真 check 铺工作目录，真渲染量具在 subprocess 边界打桩后真跑。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self._old_cwd = os.getcwd()
        self.addCleanup(os.chdir, self._old_cwd)

    # ---- 真实铺设 ----

    def build_real_workdir(self):
        self.workdir = self.root / "work"
        self.workdir.mkdir()
        self.pkg = self.root / "pkg"
        self.pkg.mkdir()
        (self.pkg / f"{OP}_test.csv").write_text(CSV_TEXT, encoding="utf-8")
        (self.pkg / "gpu_baseline.csv").write_text(BASELINE_TEXT, encoding="utf-8")
        self.repo = self.root / "repo"
        (self.repo / "test" / "frame").mkdir(parents=True)
        (self.repo / "test" / "frame" / "csv_loader.h").write_text(
            "// stub\n", encoding="utf-8",
        )
        (self.repo / "include").mkdir()
        (self.repo / "include" / "cann_ops_blas.h").write_text(
            "// blas entry\n", encoding="utf-8",
        )
        (self.repo / "build.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        deployed_dir = self.repo / "test" / OP / "arch22"
        deployed_dir.mkdir(parents=True)
        (deployed_dir / f"{OP}_test.csv").write_text(CSV_TEXT, encoding="utf-8")
        binary = self.repo / "build" / "test" / f"{OP}_test"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"#!/bin/sh\n# fake gtest binary\n")
        binary.chmod(0o755)
        code, stdout, stderr = self.run_cli([
            "check", "--package", str(self.pkg), "--repo", str(self.repo),
            "--soc", SOC, "--device", "0", "--calls-per-case", "1",
        ])
        self.assertEqual(code, 0, f"真实 accept check 应通过：{stdout}\n{stderr}")
        self.runtime = self.workdir / "runtime"
        self.results = self.runtime / "results"
        self.assertTrue((self.runtime / "verify_performance.py").is_file())

    def load_gauge(self, kernels, mapped=PF):
        """按路径导入渲染出的量具，只在 subprocess 边界打桩。

        kernels：case_name → 单次采样 kernel 总时长（µs），parse_op_summary 桩按
        阶段目录名回查；mapped：--gtest_list_tests 桩返回映射的 case 集合，
        点名在期望集内而不在映射内的 case 走量具真实的 MISSING 占位路径。"""
        _MODULE_SEQ[0] += 1
        name = f"blas_gauge_{_MODULE_SEQ[0]}"
        spec = importlib.util.spec_from_file_location(
            name, self.runtime / "verify_performance.py",
        )
        gauge = importlib.util.module_from_spec(spec)
        sys.modules[name] = gauge
        self.addCleanup(sys.modules.pop, name, None)
        spec.loader.exec_module(gauge)
        mapped_set = set(mapped)

        def fake_resolve_device(requested, pool):
            probe = {
                "status": "IDLE", "command": "stub", "detail": "目标卡空闲",
                "output": "No process in device", "device": requested,
            }
            return requested, {
                "requested": requested, "pool": pool,
                "attempts": [probe], "final": probe, "resolved": requested,
            }

        def fake_list_tests(binary, timeout, expected, env=None):
            mapping = {
                case: f"Suite/Suite.CsvDriven/{case}"
                for case in sorted(expected) if case in mapped_set
            }
            return mapping, None, None

        def fake_parse_op_summary(output_dir, launch_limit=None):
            case_name = Path(output_dir).parent.name
            return kernels[case_name], 1

        gauge._resolve_device = fake_resolve_device
        gauge._list_tests = fake_list_tests
        gauge._resolve_msprof = lambda override: Path("/stub/msprof")
        # 桩掉可用性探测与空间检查:两者都打真实文件系统,属真机边界。
        gauge._msopprof_usable = lambda binary: (True, "")
        gauge._check_free_space = lambda output_dir, launch_count: None
        gauge._run_process = lambda command, timeout, cwd=None, env=None: (0, "stub", None)
        gauge._gtest_evidence = lambda path, gtest_name: (True, "桩:证据合格")
        gauge.parse_op_summary = fake_parse_op_summary
        return gauge

    def run_gauge(self, gauge, run_id, cases=()):
        argv = [
            "--repo", str(self.repo), "--soc", SOC, "--device", "0",
            "--run-id", run_id, "--skip-build", "--calls-per-case", "1",
        ]
        for case in cases:
            argv += ["--case", case]
        os.chdir(self.workdir)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = gauge.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def run_first_round(self):
        gauge = self.load_gauge(FIRST_KERNELS)
        code, stdout, stderr = self.run_gauge(gauge, RUN)
        self.assertEqual(code, 1, f"首轮应为不通过(1002/1003 FAIL)：{stderr}")
        self.first_path = self.results / f"performance_{RUN}.json"
        self.first = json.loads(self.first_path.read_text(encoding="utf-8"))
        self.write_accuracy()
        return self.first

    def write_accuracy(self):
        """精度 JSON 按首轮真值回填哈希（A3/A4 一致性检查的另一半，精度侧不在本 lane）。"""
        accuracy = {
            "run_id": RUN, "op": OP, "family": FAMILY, "soc": SOC, "arch": "arch22",
            "device": 0, "device_pool": None, "repo": str(self.repo.resolve()),
            "binary_sha256": self.first["binary_sha256"],
            "csv_path": self.first["csv_path"],
            "csv_sha256": self.first["csv_sha256"],
            "cases": [{
                "name": "TC_L0_001", "gtest_name": "Suite/Suite.CsvDriven/TC_L0_001",
                "status": "PASS", "ms": 1.0, "message": None,
            }],
            "summary": {"expected": 1, "pass": 1, "fail": 0, "skip": 0,
                        "timeout": 0, "crash": 0, "missing": 0},
            "exit_code": 0, "started": TS, "finished": TS,
        }
        accept._atomic_json(self.results / f"accuracy_{RUN}.json", accuracy)

    # ---- accept 侧运行 ----

    def run_cli(self, argv, cwd=None):
        os.chdir(cwd or self.workdir)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = accept.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def run_verdict(self):
        out = self.root / "verdict-out"
        code, stdout, stderr = self.run_cli([
            "verdict", "--package", str(self.pkg), "--repo", str(self.repo),
            "--soc", SOC, "--device", "0", "--run-id", RUN, "--out", str(out),
        ])
        verdict = json.loads(
            (out / "intermediate" / "verdict.json").read_text(encoding="utf-8")
        )
        report = (out / "report" / "report.md").read_text(encoding="utf-8")
        return code, verdict, report, stderr

    def case_row(self, verdict, name):
        for row in verdict["performance"]["case_rows"]:
            if row["name"] == name:
                return row
        raise AssertionError(f"case_rows 缺 {name}")


class TestRealFirstRound(RetestIntegrationCase):
    def test_anchors_and_preflight_on_real_artifacts(self):
        """真渲染量具首轮：锚字段实算落盘，preflight 以真产物判定支持。"""
        self.build_real_workdir()
        first = self.run_first_round()
        self.assertEqual(
            first["normalized_baseline_sha256"],
            accept._sha256(self.runtime / "gpu_baseline.csv"),
        )
        self.assertEqual(
            first["verifier_sha256"],
            accept._sha256(self.runtime / "verify_performance.py"),
        )
        # 预热设计已整体撤除：顶层 warmup 与逐例 warmup_exit 都不再存在。
        self.assertNotIn("warmup", first)
        for record in first["cases"]:
            self.assertNotIn("warmup_exit", record)
        code, stdout, _ = self.run_cli(["retest-preflight", "--run-id", RUN])
        self.assertEqual(code, 0)
        view = json.loads(stdout)
        self.assertTrue(view["supported"])
        self.assertEqual(view["next_round"], 1)
        self.assertEqual(view["device"], 0)
        self.assertIs(view["needs_device_map"], False)
        self.assertEqual(view["expected_cases"], 3)

    def test_no_retest_verdict_baseline(self):
        """零复测：真产物走完整 A5，无复测字段（条件输出矩阵第一态的真值版）。"""
        self.build_real_workdir()
        self.run_first_round()
        code, verdict, report, _ = self.run_verdict()
        self.assertEqual(code, 1)
        self.assertEqual(verdict["verdict"], "不通过")
        self.assertEqual(verdict["performance"]["problems"], [])
        self.assertNotIn("retest", verdict["performance"])
        self.assertNotIn("复测", report)


class TestRealMeasureRound(RetestIntegrationCase):
    def test_measure_round_with_missing_folds(self):
        """真复测轮（PASS + 真 MISSING 占位）→ 加载/校验/折叠全链真值往返。"""
        self.build_real_workdir()
        self.run_first_round()
        # 1002 复测 PASS；1003 点名但被 --gtest_list_tests 桩排除 → 量具真实
        # MISSING 占位路径（每个点名恰好一条记录）。
        gauge = self.load_gauge({"TC_PF_1002": 9000.0}, mapped=("TC_PF_1002",))
        code, _, stderr = self.run_gauge(
            gauge, f"{RUN}-retest-1", cases=("TC_PF_1002", "TC_PF_1003"),
        )
        self.assertEqual(code, 2, f"MISSING → 证据不足退 2：{stderr}")
        round_path = self.results / f"performance_{RUN}-retest-1.json"
        payload = json.loads(round_path.read_text(encoding="utf-8"))
        # 复测轮 schema：身份、点名、设备、绑定、argv 无条件序列化。
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["base_run_id"], RUN)
        self.assertEqual(payload["round"], 1)
        self.assertEqual(payload["kind"], "measure")
        self.assertEqual(payload["requested_cases"], ["TC_PF_1002", "TC_PF_1003"])
        self.assertEqual(payload["device_requested"], 0)
        self.assertEqual(payload["threshold"], 0.8)
        self.assertNotIn("warmup", payload)
        by_name = {record["name"]: record for record in payload["cases"]}
        self.assertEqual(by_name["TC_PF_1002"]["status"], "PASS")
        self.assertNotIn("warmup_exit", by_name["TC_PF_1002"])
        self.assertEqual(by_name["TC_PF_1003"]["status"], "MISSING")
        # 加载层：真产物过全部有效性检查。
        context = accept.load_retest_context(self.workdir, RUN)
        self.assertTrue(context["supported"])
        self.assertEqual([item["round"] for item in context["valid_rounds"]], [1])
        self.assertEqual(context["invalid_rounds"], [])
        # 折叠：1002 → PASS(轮 1)；1003 首轮 FAIL 优先于 MISSING 缺口（规则 3）。
        code, verdict, report, _ = self.run_verdict()
        self.assertEqual(code, 1)
        self.assertEqual(verdict["performance"]["base_status"], "不通过")
        retest = verdict["performance"]["retest"]
        self.assertEqual(retest["fold_protocol_version"],
                         retest_fold.FOLD_PROTOCOL_VERSION)
        self.assertEqual(retest["pass_on_retest"], 1)
        self.assertEqual(retest["inputs"][1]["sha256"], accept._sha256(round_path))
        row = self.case_row(verdict, "TC_PF_1002")
        self.assertEqual(row["effective_status"], "PASS")
        self.assertEqual(row["representative_round"], 1)
        self.assertAlmostEqual(row["ratio"], 10.0 / 9.0, places=6)
        row = self.case_row(verdict, "TC_PF_1003")
        self.assertEqual(row["effective_status"], "FAIL")
        self.assertEqual(row["representative_round"], 0)
        self.assertEqual(row["measure_count"], 1)
        self.assertIn("PASS(复测)：1", report)

    def test_unknown_case_zero_side_effects(self):
        """未知点名：量具 preflight 真拒绝（退 2），不占轮号、无阶段目录。"""
        self.build_real_workdir()
        self.run_first_round()
        gauge = self.load_gauge({})
        os.chdir(self.workdir)
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                gauge.main([
                    "--repo", str(self.repo), "--soc", SOC, "--device", "0",
                    "--run-id", f"{RUN}-retest-1", "--case", "TC_PF_9999",
                    "--skip-build", "--calls-per-case", "1",
                ])
        self.assertEqual(ctx.exception.code, 2)
        self.assertFalse((self.results / f"performance_{RUN}-retest-1.json").exists())
        self.assertFalse((self.results / f"{RUN}-retest-1").exists())
        code, stdout, _ = self.run_cli(["retest-preflight", "--run-id", RUN])
        self.assertEqual(json.loads(stdout)["next_round"], 1)

    def test_run_id_exists_refused_hash_unchanged(self):
        """证据保护：同复测 run-id 重跑被拒（退 3），已有轮次 JSON 哈希不变。"""
        self.build_real_workdir()
        self.run_first_round()
        gauge = self.load_gauge({"TC_PF_1002": 9000.0}, mapped=("TC_PF_1002",))
        code, _, _ = self.run_gauge(gauge, f"{RUN}-retest-1", cases=("TC_PF_1002",))
        self.assertEqual(code, 0)
        round_path = self.results / f"performance_{RUN}-retest-1.json"
        before = accept._sha256(round_path)
        gauge2 = self.load_gauge({"TC_PF_1002": 1.0}, mapped=("TC_PF_1002",))
        code, _, stderr = self.run_gauge(gauge2, f"{RUN}-retest-1", cases=("TC_PF_1002",))
        self.assertEqual(code, 3)
        self.assertIn("RUN_ID_EXISTS", stderr)
        self.assertEqual(accept._sha256(round_path), before)


class TestRealWaiveAndTamper(RetestIntegrationCase):
    def test_waive_after_real_round_passes_verdict(self):
        """真测量轮 + accept 真豁免轮 → 通过；WAIVED 参考轮指向真 MISSING 测量。"""
        self.build_real_workdir()
        self.run_first_round()
        gauge = self.load_gauge({"TC_PF_1002": 9000.0}, mapped=("TC_PF_1002",))
        self.run_gauge(gauge, f"{RUN}-retest-1", cases=("TC_PF_1002", "TC_PF_1003"))
        code, _, _ = self.run_cli([
            "waive", "--run-id", RUN, "--waive", "TC_PF_1003", "上游缺陷已立项",
        ])
        self.assertEqual(code, 0)
        code, verdict, report, _ = self.run_verdict()
        self.assertEqual(code, 0)
        self.assertEqual(verdict["verdict"], "通过")
        self.assertEqual(verdict["performance"]["base_status"], "通过")
        retest = verdict["performance"]["retest"]
        self.assertEqual(retest["waived"], [
            {"case": "TC_PF_1003", "round": 2, "reason": "上游缺陷已立项"},
        ])
        self.assertEqual(retest["pass_on_retest"], 1)
        self.assertEqual(verdict["performance"]["retest_valid_rounds"], 2)
        row = self.case_row(verdict, "TC_PF_1003")
        self.assertEqual(row["effective_status"], "WAIVED")
        self.assertEqual(row["representative_round"], 2)
        self.assertEqual(row["reference_round"], 1)
        self.assertIsNone(row["ratio"])
        self.assertIn("上游缺陷已立项", report)
        # rerun.sh 注释性证据展示含真量具 argv 与豁免轮标注。
        rerun_text = (self.root / "verdict-out" / "repro" / "rerun.sh").read_text(
            encoding="utf-8",
        )
        self.assertIn("复测轮命令", rerun_text)
        self.assertIn(f"--run-id {RUN}-retest-1", rerun_text)
        self.assertIn("# round 2: waive", rerun_text)

    def test_tampered_binding_round_invalidated(self):
        """伪造绑定不符轮（真轮副本改 csv_sha256）→ 无效跳过，其余轮照常折叠。"""
        self.build_real_workdir()
        self.run_first_round()
        gauge = self.load_gauge({"TC_PF_1002": 9000.0}, mapped=("TC_PF_1002",))
        self.run_gauge(gauge, f"{RUN}-retest-1", cases=("TC_PF_1002",))
        genuine = self.results / f"performance_{RUN}-retest-1.json"
        tampered = json.loads(genuine.read_text(encoding="utf-8"))
        tampered["round"] = 2
        tampered["csv_sha256"] = "f" * 64
        (self.results / f"performance_{RUN}-retest-2.json").write_text(
            json.dumps(tampered, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        code, verdict, report, stderr = self.run_verdict()
        self.assertEqual(code, 1)  # 1003 仍 FAIL。
        retest = verdict["performance"]["retest"]
        self.assertEqual("fold_protocol_version" in retest, True)
        self.assertEqual([item["round"] for item in retest["invalid_rounds"]], [2])
        reasons = "；".join(retest["invalid_rounds"][0]["reasons"])
        self.assertIn("csv_sha256", reasons)
        self.assertEqual(
            [(item["round"], item["kind"]) for item in retest["inputs"]],
            [(0, "measure"), (1, "measure")],
        )
        self.assertEqual(self.case_row(verdict, "TC_PF_1002")["effective_status"], "PASS")
        self.assertIn("该轮未生效", report)
        self.assertIn("该轮未生效", stderr)


if __name__ == "__main__":
    unittest.main()
