"""复测契约测试（plan F4/W3）：伪造量具 JSON + 假工作目录，不碰真机。

覆盖四类契约：加载/校验往返（load_retest_context + 轮次有效性表）、verdict.json
的三态条件输出矩阵（无痕迹 / 仅中断无效 / 有有效轮）、waive 与 retest-preflight
的 CLI 边界（未知 case、语法错、坏 JSON 轮、轮号空洞、旧首轮缺锚双线拒绝）、
intermediate/ 发布事务的中断恢复。所有文件都在临时目录里手工铺设。
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
from unittest import mock

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
TESTS_DIR = Path(__file__).resolve().parent
for _path in (SCRIPTS, TESTS_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import retest_fold  # noqa: E402
# 跨 lane 往返（本文件末尾那个类）借用集成测试的真渲染脚手架，不另铺一套假 repo：
# 那边的 RetestIntegrationCase 是纯夹具类（无 test_ 方法），继承它不会重跑它的用例。
import test_retest_integration as integration  # noqa: E402


def _load_accept():
    """按路径加载 accept.py，模块名取独一无二的，避免与其它 skill 同名模块相撞。"""
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
FAMILY = "ger"
SOC = "ascend910b3"
RUN = "sger-20260917-0001"
PF = ("TC_PF_1001", "TC_PF_1002", "TC_PF_1003")
BIN_SHA = "a" * 64
DEP_SHA = "c" * 64
PKG_CSV_SHA_FAKE = "d" * 64
VERIFIER_SHA = "e" * 64
TS = "2026-09-17T00:00:00+08:00"

# 默认三条 PF：与早期常量文本等价；需要更多用例（如截断测试）时传 pf 参数。
DEFAULT_PF = (("TC_PF_1001", 16, 16), ("TC_PF_1002", 32, 32), ("TC_PF_1003", 64, 64))


def _csv_text(pf):
    lines = [
        "case_name,description,expect_result,random_seed,m,n",
        "TC_L0_001,acc,PASS,1,8,8",
    ]
    for name, m, n in pf:
        lines.append(f"{name},perf,PASS,1,{m},{n}")
    return "\n".join(lines) + "\n"


def _baseline_text(pf, gpu_ms="10.0"):
    lines = ["id,m,n,gpu_ms"]
    for index, (_, m, n) in enumerate(pf, 1):
        lines.append(f"sger-base-{index:03d},{m},{n},{gpu_ms}")
    return "\n".join(lines) + "\n"


def _pf_case(name, status, ratio=None, kernel_us=None):
    return {
        "name": name,
        "status": status,
        "kernel_us": kernel_us,
        "samples": [kernel_us] if kernel_us is not None else [],
        "launches": [1] if kernel_us is not None else [],
        "gpu_ms": 10.0,
        "ratio": ratio,
        "spread": 0.0 if kernel_us is not None else None,
        "verdict": status,
        "warnings": [],
    }


def _passed(name, ratio=1.2, kernel_us=8.0):
    return _pf_case(name, "PASS", ratio, kernel_us)


def _failed(name, ratio=0.5, kernel_us=20.0):
    return _pf_case(name, "FAIL", ratio, kernel_us)


def _perf_summary(cases):
    counts = {
        status: sum(item["status"] == status for item in cases)
        for status in ("PASS", "FAIL", "NO_REF", "NO_KERNEL", "CRASH", "TIMEOUT", "MISSING")
    }
    insufficient = counts["NO_KERNEL"] + counts["CRASH"] + counts["TIMEOUT"] + counts["MISSING"]
    if insufficient:
        status, exit_code = "证据不足", 2
    elif counts["FAIL"]:
        status, exit_code = "不通过", 1
    elif not (counts["PASS"] or counts["FAIL"]):
        status, exit_code = "NO_REF", 0
    else:
        status, exit_code = "通过", 0
    summary = {
        "expected": len(cases),
        "pass": counts["PASS"],
        "fail": counts["FAIL"],
        "no_ref": counts["NO_REF"],
        "no_kernel": counts["NO_KERNEL"],
        "crash": counts["CRASH"],
        "timeout": counts["TIMEOUT"],
        "missing": counts["MISSING"],
        "status": status,
        "timing_scope": "kernel",
        "threshold": 0.8,
        "scope_caveat": False,
    }
    return summary, exit_code


class RetestContractCase(unittest.TestCase):
    """公共脚手架：铺一个 A5 可闭合的假工作目录，再按需叠复测轮。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # macOS 下 /var 是 /private/var 的符号链接；verdict 会 resolve 输入路径，
        # 夹具先 resolve 才能与 check.json/JSON 里的字符串逐字相等。
        self.root = Path(self._tmp.name).resolve()
        self._old_cwd = os.getcwd()
        self.addCleanup(os.chdir, self._old_cwd)

    # ---- 工作目录铺设 ----

    def build_workdir(self, pf_statuses=None, anchors=True, first_device=0,
                      pf=None, baseline_gpu_ms="10.0", first_override=None,
                      profile="blas"):
        self.pf = list(pf or DEFAULT_PF)
        pf_names = [name for name, _, _ in self.pf]
        pf_statuses = pf_statuses or {
            name: ("PASS" if index == 0 else "FAIL")
            for index, name in enumerate(pf_names)
        }
        csv_text = _csv_text(self.pf)
        baseline_text = _baseline_text(self.pf, gpu_ms=baseline_gpu_ms)
        workdir = self.root / "work"
        runtime = workdir / "runtime"
        results = runtime / "results"
        results.mkdir(parents=True)
        self.pkg = self.root / "pkg"
        self.repo = self.root / "repo"
        self.pkg.mkdir()
        self.repo.mkdir()
        (self.pkg / f"{OP}_test.csv").write_text(csv_text, encoding="utf-8")
        (self.pkg / "gpu_baseline.csv").write_text(baseline_text, encoding="utf-8")
        (runtime / f"{OP}_test.csv").write_text(csv_text, encoding="utf-8")
        (runtime / "gpu_baseline.csv").write_text(baseline_text, encoding="utf-8")
        csv_sha = accept._sha256(self.pkg / f"{OP}_test.csv")
        baseline_sha = accept._sha256(self.pkg / "gpu_baseline.csv")
        self.normalized_sha = accept._sha256(runtime / "gpu_baseline.csv")
        manifest = {
            "op": OP,
            "family": FAMILY,
            "harness_profile": profile,
            "perf_key": ["m", "n"],
            "threshold": 0.8,
            "calls_per_case": 1,
            "package": str(self.pkg),
            "package_csv": f"{OP}_test.csv",
            "package_csv_sha256": csv_sha,
            "baseline_sha256": baseline_sha,
            "normalized_baseline_sha256": self.normalized_sha,
            "scripts": {},
            "generated_at": TS,
        }
        accept._atomic_json(runtime / "manifest.json", manifest)
        check = {
            "command": "check",
            "package": str(self.pkg),
            "repo": str(self.repo),
            "soc": SOC,
            "device": first_device,
            "device_pool": None if first_device != "auto" else [0, 5],
            "calls_per_case": 1,
            "op": OP,
            "family": FAMILY,
            "package_csv_sha256": csv_sha,
            "baseline_sha256": baseline_sha,
            "checks": {},
            "errors": [],
            "warnings": [],
            "exit_code": 0,
            "generated_at": TS,
        }
        check["evidence_id"] = accept._evidence_id(check)
        accept._atomic_json(workdir / "check.json", check)
        accuracy = {
            "run_id": RUN,
            "op": OP,
            "family": FAMILY,
            "soc": SOC,
            "arch": "arch22",
            # 设备字段跟着首轮走：auto 首轮的工作目录里 A3/A4 都是 auto 口径，
            # 否则 A5 的 auto 定卡闭合检查会在精度侧就判证据不足。
            "device": first_device,
            "device_pool": None if first_device != "auto" else [0, 5],
            "repo": str(self.repo),
            "binary_sha256": BIN_SHA,
            "csv_path": "/x/deploy.csv",
            "csv_sha256": DEP_SHA,
            "package_csv_sha256": PKG_CSV_SHA_FAKE,
            "cases": [{
                "name": "TC_L0_001", "gtest_name": "S.T/1",
                "status": "PASS", "ms": 1.0, "message": None,
            }],
            "summary": {
                "expected": 1, "pass": 1, "fail": 0, "skip": 0,
                "timeout": 0, "crash": 0, "missing": 0,
            },
            "exit_code": 0,
            "started": TS,
            "finished": TS,
        }
        if first_device == "auto":
            accuracy["device_resolved"] = 5
            accuracy["npu_gate"] = {"final": {"status": "IDLE", "device": 5}}
        accept._atomic_json(results / f"accuracy_{RUN}.json", accuracy)
        cases = []
        for name in pf_names:
            status = pf_statuses[name]
            if status == "PASS":
                cases.append(_passed(name))
            elif status == "FAIL":
                cases.append(_failed(name))
            else:
                cases.append(_pf_case(name, status))
        summary, exit_code = _perf_summary(cases)
        first = {
            "run_id": RUN,
            "op": OP,
            "family": FAMILY,
            "soc": SOC,
            "arch": "arch22",
            "device": first_device,
            "device_pool": None if first_device != "auto" else [0, 5],
            "repo": str(self.repo),
            "binary": "/x/sger_test",
            "binary_sha256": BIN_SHA,
            "csv_path": "/x/deploy.csv",
            "csv_sha256": DEP_SHA,
            "package_csv_sha256": PKG_CSV_SHA_FAKE,
            "calls_per_case": 1,
            "ignored_no_ref": [],
            "gpu_baseline_path": "gpu_baseline.csv",
            "baseline_warnings": [],
            "msprof": "/x/msprof",
            "gtest_filter": "f",
            "device_resolved": 5 if first_device == "auto" else first_device,
            "cases": cases,
            "summary": summary,
            "exit_code": exit_code,
            "started": TS,
            "finished": TS,
        }
        if first_device == "auto":
            first["npu_gate"] = {"final": {"status": "IDLE", "device": 5}}
        if anchors:
            first["normalized_baseline_sha256"] = self.normalized_sha
            first["verifier_sha256"] = VERIFIER_SHA
        if first_override:
            first.update(first_override)
        accept._atomic_json(results / f"performance_{RUN}.json", first)
        self.workdir = workdir
        self.results = results
        return workdir

    # ---- 复测轮铺设 ----

    def write_measure_round(self, number, cases, requested=None, device=0, warmup=0,
                            overrides=None, drop=()):
        requested = requested if requested is not None else [c["name"] for c in cases]
        payload = {
            "schema_version": 1,
            "base_run_id": RUN,
            "round": number,
            "kind": "measure",
            "op": OP,
            "family": FAMILY,
            "soc": SOC,
            "repo": str(self.repo),
            "started": TS,
            "finished": TS,
            "requested_cases": requested,
            "cases": [{**c, "warmup_exit": c.get("warmup_exit")} for c in cases],
            "warmup": warmup,
            "device_requested": device,
            "device_resolved": device,
            "binary_sha256": BIN_SHA,
            "csv_sha256": DEP_SHA,
            "normalized_baseline_sha256": self.normalized_sha,
            "threshold": 0.8,
            "calls_per_case": 1,
            "verifier_sha256": VERIFIER_SHA,
            "argv": ["verify_performance.py", "--run-id", f"{RUN}-retest-{number}"],
        }
        payload.update(overrides or {})
        for key in drop:
            payload.pop(key, None)
        path = self.results / f"performance_{RUN}-retest-{number}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def write_stage_dir(self, number):
        (self.results / f"{RUN}-retest-{number}" / "performance").mkdir(parents=True)

    def write_prof_dirs(self, number, cards, case="TC_PF_1002", repeat=1):
        """铺该轮的 PROF 落点：<阶段目录>/performance/prof/<case>/r<N>/PROF_*/device_<卡>。"""
        stage = self.results / f"{RUN}-retest-{number}" / "performance"
        for index, card in enumerate(cards, 1):
            target = (stage / "prof" / case / f"r{repeat}"
                      / f"PROF_{index:06d}_x" / f"device_{card}")
            target.mkdir(parents=True, exist_ok=True)
        return stage

    def write_waive_round(self, number, waivers):
        payload = {
            "schema_version": 1,
            "base_run_id": RUN,
            "round": number,
            "kind": "waive",
            "op": OP,
            "family": FAMILY,
            "soc": SOC,
            "repo": str(self.repo),
            "started": TS,
            "finished": TS,
            "waivers": waivers,
        }
        path = self.results / f"performance_{RUN}-retest-{number}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    # ---- 运行 ----

    def run_cli(self, argv, cwd=None):
        os.chdir(cwd or self.workdir)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = accept.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def run_verdict(self, out=None, device="0", device_pool=None):
        out = Path(out) if out else self.root / "verdict-out"
        argv = [
            "verdict", "--package", str(self.pkg), "--repo", str(self.repo),
            "--soc", SOC, "--device", device, "--run-id", RUN, "--out", str(out),
        ]
        if device_pool is not None:
            argv += ["--device-pool", device_pool]
        code, stdout, stderr = self.run_cli(argv)
        verdict = json.loads((out / "intermediate" / "verdict.json").read_text(encoding="utf-8"))
        report = (out / "report" / "report.md").read_text(encoding="utf-8")
        return code, verdict, report, stderr, out

    def case_row(self, verdict, name):
        for row in verdict["performance"]["case_rows"]:
            if row["name"] == name:
                return row
        raise AssertionError(f"case_rows 缺 {name}")

    def _assert_diag_only(self, verdict, fragment):
        """无效轮口径：retest 段只剩诊断两键，且原因里含给定片段。"""
        retest = verdict["performance"]["retest"]
        self.assertEqual(set(retest), {"interrupted_rounds", "invalid_rounds"})
        reasons = "；".join(
            reason for item in retest["invalid_rounds"] for reason in item["reasons"]
        )
        self.assertIn(fragment, reasons)


class TestConditionalOutputMatrix(RetestContractCase):
    """spec §9 / plan §2：verdict.json 三态条件输出矩阵。"""

    def test_no_trace_no_new_fields(self):
        self.build_workdir()
        code, verdict, report, stderr, out = self.run_verdict()
        self.assertEqual(code, 1)
        self.assertEqual(verdict["verdict"], "不通过")
        performance = verdict["performance"]
        self.assertNotIn("retest", performance)
        self.assertNotIn("retest_warnings", performance)
        self.assertNotIn("retest_valid_rounds", performance)
        self.assertNotIn("复测", report)
        self.assertIn("<由验收 agent 填写", report)
        for row in performance["case_rows"]:
            self.assertNotIn("effective_status", row)
        leftovers = [p.name for p in out.glob("intermediate.*")]
        self.assertEqual(leftovers, [])

    def test_invalid_round_only_diagnostics(self):
        self.build_workdir()
        bad = self.results / f"performance_{RUN}-retest-1.json"
        bad.write_text("not-json", encoding="utf-8")
        code, verdict, report, stderr, _ = self.run_verdict()
        self.assertEqual(code, 1)
        self.assertEqual(verdict["verdict"], "不通过")
        retest = verdict["performance"]["retest"]
        self.assertEqual(set(retest), {"interrupted_rounds", "invalid_rounds"})
        self.assertEqual(retest["interrupted_rounds"], [])
        self.assertEqual(len(retest["invalid_rounds"]), 1)
        entry = retest["invalid_rounds"][0]
        self.assertEqual(entry["round"], 1)
        self.assertEqual(entry["note"], "该轮未生效")
        self.assertIn("不可解析", "；".join(entry["reasons"]))
        self.assertIn("该轮未生效", report)
        self.assertIn("该轮未生效", stderr)

    def test_interrupted_round_only_diagnostics(self):
        self.build_workdir()
        self.write_stage_dir(1)
        code, verdict, report, _, _ = self.run_verdict()
        self.assertEqual(code, 1)
        retest = verdict["performance"]["retest"]
        self.assertEqual(set(retest), {"interrupted_rounds", "invalid_rounds"})
        self.assertEqual(retest["interrupted_rounds"], [1])
        self.assertIn("中断轮", report)

    def test_diagnostic_bucket_keeps_verdict_identical(self):
        self.build_workdir()
        code_a, verdict_a, _, _, _ = self.run_verdict(out=self.root / "out-a")
        bad = self.results / f"performance_{RUN}-retest-1.json"
        bad.write_text("not-json", encoding="utf-8")
        code_b, verdict_b, _, _, _ = self.run_verdict(out=self.root / "out-b")
        self.assertEqual(code_a, code_b)
        for payload in (verdict_a, verdict_b):
            payload.pop("layout", None)
            payload["performance"].pop("retest", None)
            payload["performance"].pop("retest_warnings", None)
        self.assertEqual(verdict_a, verdict_b)

    def test_valid_round_full_output(self):
        self.build_workdir()
        round_path = self.write_measure_round(1, [
            _passed("TC_PF_1002", ratio=1.1, kernel_us=9.0),
            _passed("TC_PF_1003", ratio=1.05, kernel_us=9.5),
        ])
        code, verdict, report, _, out = self.run_verdict()
        self.assertEqual(code, 0)
        self.assertEqual(verdict["verdict"], "通过")
        performance = verdict["performance"]
        self.assertEqual(performance["base_status"], "通过")
        retest = performance["retest"]
        self.assertEqual(set(retest), {
            "fold_protocol_version", "inputs", "invalid_rounds",
            "interrupted_rounds", "waived", "pass_on_retest",
        })
        self.assertEqual(retest["fold_protocol_version"], retest_fold.FOLD_PROTOCOL_VERSION)
        self.assertEqual(retest["pass_on_retest"], 2)
        self.assertEqual(retest["invalid_rounds"], [])
        self.assertEqual(retest["interrupted_rounds"], [])
        self.assertEqual(retest["waived"], [])
        self.assertEqual(
            [(item["round"], item["kind"]) for item in retest["inputs"]],
            [(0, "measure"), (1, "measure")],
        )
        first_path = self.results / f"performance_{RUN}.json"
        self.assertEqual(retest["inputs"][0]["sha256"], accept._sha256(first_path))
        self.assertEqual(retest["inputs"][1]["sha256"], accept._sha256(round_path))
        row = self.case_row(verdict, "TC_PF_1002")
        self.assertEqual(row["effective_status"], "PASS")
        self.assertEqual(row["representative_round"], 1)
        self.assertEqual(row["measure_count"], 1)
        self.assertEqual(row["ratio"], 1.1)
        self.assertEqual(performance["retest_valid_rounds"], 1)
        self.assertIn("PASS(复测)：2", report)
        self.assertIn("复测史", report)
        # 复测轮 JSON 已归档进 intermediate/。
        self.assertTrue((out / "intermediate" / round_path.name).is_file())
        # rerun.sh 里复测轮命令只作注释性证据展示，不进入可执行步骤。
        rerun_text = (out / "repro" / "rerun.sh").read_text(encoding="utf-8")
        self.assertIn("# ---- 复测轮命令（注释性证据展示", rerun_text)
        self.assertIn(f"# round 1: verify_performance.py --run-id {RUN}-retest-1",
                      rerun_text)


class TestValidityChecks(RetestContractCase):
    """轮次有效性检查表：坏轮跳过 + 醒目告警，整体裁决不翻车。"""

    def test_binding_mismatch_invalidates(self):
        self.build_workdir()
        self.write_measure_round(
            1, [_passed("TC_PF_1002")], overrides={"csv_sha256": "f" * 64},
        )
        code, verdict, _, _, _ = self.run_verdict()
        self.assertEqual(code, 1)
        self.assertEqual(verdict["verdict"], "不通过")
        self._assert_diag_only(verdict, "csv_sha256")

    def test_device_auto_invalidates(self):
        """复测轮不接受 auto：卡号必须是显式物理卡号。"""
        self.build_workdir()
        self.write_measure_round(
            1, [_passed("TC_PF_1002")], overrides={"device_requested": "auto"},
        )
        code, verdict, _, _, _ = self.run_verdict()
        self.assertEqual(code, 1)
        self._assert_diag_only(verdict, "复测轮不接受 device_requested=auto")

    def test_device_not_a_card_number_invalidates(self):
        self.build_workdir()
        self.write_measure_round(
            1, [_passed("TC_PF_1002")], overrides={"device_resolved": -1},
        )
        code, verdict, _, _, _ = self.run_verdict()
        self.assertEqual(code, 1)
        self._assert_diag_only(verdict, "不是显式物理卡号")

    def test_rollcall_record_mismatch_invalidates(self):
        self.build_workdir()
        self.write_measure_round(
            1, [_passed("TC_PF_1002")],
            requested=["TC_PF_1002", "TC_PF_1003"],
        )
        code, verdict, _, _, _ = self.run_verdict()
        self.assertEqual(code, 1)
        self._assert_diag_only(verdict, "点名 case 缺记录")

    def test_unknown_requested_case_invalidates(self):
        self.build_workdir()
        self.write_measure_round(
            1, [_passed("TC_PF_1002"), _pf_case("TC_PF_9999", "MISSING")],
            requested=["TC_PF_1002", "TC_PF_9999"],
        )
        code, verdict, _, _, _ = self.run_verdict()
        self.assertEqual(code, 1)
        self._assert_diag_only(verdict, "点名不在性能期望集")

    def test_illegal_status_invalidates(self):
        self.build_workdir()
        self.write_measure_round(1, [_pf_case("TC_PF_1002", "SKIP")])
        code, verdict, _, _, _ = self.run_verdict()
        self.assertEqual(code, 1)
        self._assert_diag_only(verdict, "不在合法集合")

    def test_waive_round_missing_field_invalidates(self):
        """最小豁免轮字段集：缺 waivers 即无效。"""
        self.build_workdir()
        payload = {
            "schema_version": 1, "base_run_id": RUN, "round": 1, "kind": "waive",
            "op": OP, "family": FAMILY, "soc": SOC, "repo": str(self.repo),
            "started": TS, "finished": TS,
        }
        path = self.results / f"performance_{RUN}-retest-1.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        code, verdict, _, _, _ = self.run_verdict()
        self.assertEqual(code, 1)
        self._assert_diag_only(verdict, "waivers 须为列表")

    def test_round_field_mismatch_is_warning_by_filename(self):
        self.build_workdir()
        self.write_measure_round(
            1, [_passed("TC_PF_1002"), _passed("TC_PF_1003")],
            overrides={"round": 5},
        )
        code, verdict, report, _, _ = self.run_verdict()
        self.assertEqual(code, 0)
        warnings = "；".join(verdict["performance"]["retest_warnings"])
        self.assertIn("按文件名裁", warnings)
        row = self.case_row(verdict, "TC_PF_1002")
        self.assertEqual(row["representative_round"], 1)
        self.assertIn("按文件名裁", report)

    def test_round_number_gap_warns_but_folds(self):
        self.build_workdir()
        self.write_measure_round(1, [_passed("TC_PF_1002")])
        self.write_measure_round(3, [_passed("TC_PF_1003")])
        code, verdict, _, _, _ = self.run_verdict()
        self.assertEqual(code, 0)
        self.assertEqual(verdict["performance"]["retest"]["pass_on_retest"], 2)
        warnings = "；".join(verdict["performance"]["retest_warnings"])
        self.assertIn("空缺", warnings)


class TestDeviceSwitch(RetestContractCase):
    """换卡复测：物理卡可变、device_compiled 按 profile 推导、PROF 落点核对。"""

    def _preflight(self):
        code, stdout, _ = self.run_cli(["retest-preflight", "--run-id", RUN])
        return code, json.loads(stdout)

    def test_derive_device_compiled_is_table_driven(self):
        """推导只查 DEVICE_BIND_MODES：编译期定卡域跟首轮，运行时定卡域恒 0。"""
        derive = accept._derive_device_compiled
        self.assertEqual(derive("blas", "auto"), (0, None))
        self.assertEqual(derive("blas", 6), (6, None))
        self.assertEqual(derive("sparse_frame", 6), (0, None))
        self.assertEqual(derive("sparse_frame", "auto"), (0, None))
        compiled, error = derive("mystery", 6)
        self.assertIsNone(compiled)
        self.assertIn("绑卡模式表", error)
        compiled, error = derive("blas", None)
        self.assertIsNone(compiled)
        self.assertIn("推不出编译逻辑卡号", error)

    def test_switched_device_round_folds_and_shows_card(self):
        """换卡轮照常折叠，复测史逐轮显示实际执行卡。"""
        self.build_workdir()
        self.write_measure_round(
            1, [_passed("TC_PF_1002"), _passed("TC_PF_1003")],
            device=6, overrides={"device_compiled": 0},
        )
        self.write_prof_dirs(1, [6], case="TC_PF_1002")
        self.write_prof_dirs(1, [6], case="TC_PF_1003")
        code, verdict, report, _, _ = self.run_verdict()
        self.assertEqual(code, 0)
        self.assertEqual(verdict["performance"]["retest"]["pass_on_retest"], 2)
        row = self.case_row(verdict, "TC_PF_1002")
        self.assertEqual(row["representative_round"], 1)
        self.assertEqual(
            [item["device_resolved"] for item in row["rounds"]], ["0", "6"],
        )
        self.assertIn("| TC_PF_1002 | 1 | measure | 6 | PASS", report)

    def test_device_compiled_mismatch_invalidates(self):
        """blas + 首轮显式卡 0 → 编译逻辑卡号 0；轮里写 3 即无效。"""
        self.build_workdir()
        self.write_measure_round(
            1, [_passed("TC_PF_1002")], device=6, overrides={"device_compiled": 3},
        )
        self.write_prof_dirs(1, [6])
        code, verdict, _, _, _ = self.run_verdict()
        self.assertEqual(code, 1)
        self._assert_diag_only(verdict, "不等于按 harness_profile 推导的 0")

    def test_reads_rounds_with_and_without_warmup(self):
        """V0：撤掉 warmup 消费后，带该字段的旧记录与不带的新记录都能读。

        中间态要能安全停留——量具还没改时产的是旧形态，改完产的是新形态，
        两种都不能让轮判无效。`drop` 掉 warmup 即模拟新量具的产物。"""
        self.build_workdir()
        self.write_measure_round(1, [_passed("TC_PF_1002")], device=0)
        self.write_measure_round(
            2, [_passed("TC_PF_1003")], device=0, drop=("warmup",),
        )
        _, verdict, _, _, _ = self.run_verdict()
        retest = verdict["performance"]["retest"]
        self.assertEqual(retest["invalid_rounds"], [])
        self.assertEqual(retest["pass_on_retest"], 2)

    def test_prof_landing_no_longer_checked(self):
        """落卡核对已撤除：阶段目录里出现别的卡的落点也不再使轮无效。

        这条断言的是「核对不在了」，不是「核对没报问题」——后者在常量改了而
        fixture 没跟着改时会假绿（glob 扫空走的是缺目录不告警的合法分支）。"""
        self.build_workdir()
        self.write_measure_round(
            1, [_passed("TC_PF_1002")], device=6, overrides={"device_compiled": 0},
        )
        self.write_prof_dirs(1, [0])
        _, verdict, _, _, _ = self.run_verdict()
        retest = verdict["performance"]["retest"]
        self.assertEqual(retest["invalid_rounds"], [])
        self.assertNotIn(
            "PROF 落点", "；".join(verdict["performance"]["retest_warnings"]),
        )
        self.assertFalse(hasattr(accept, "_prof_device_cards"))
        self.assertFalse(hasattr(accept, "PROF_DEVICE_GLOB"))

    def test_missing_prof_dir_keeps_round_valid(self):
        """合法单例终态不产生 PROF 目录，缺目录不改判该轮无效。"""
        self.build_workdir()
        self.write_measure_round(
            1, [_pf_case("TC_PF_1002", "TIMEOUT")], device=6,
            overrides={"device_compiled": 0},
        )
        self.write_stage_dir(1)
        _, verdict, _, _, _ = self.run_verdict()
        retest = verdict["performance"]["retest"]
        self.assertEqual(retest["invalid_rounds"], [])
        self.assertIn("fold_protocol_version", retest)
        self.assertEqual(self.case_row(verdict, "TC_PF_1002")["measure_count"], 1)
        # 缺口终态本就不产生 PROF 目录，不告警。
        self.assertNotIn(
            "未完成设备核对", "；".join(verdict["performance"]["retest_warnings"]),
        )

    def test_preflight_compiled_from_explicit_first_card(self):
        self.build_workdir(first_device=3)
        code, view = self._preflight()
        self.assertEqual(code, 0)
        self.assertEqual(view["device"], 3)
        self.assertEqual(view["device_compiled"], 3)
        self.assertIs(view["can_switch_device"], True)
        self.assertIn("默认卡", view["device_note"])

    def test_preflight_compiled_zero_when_first_auto(self):
        self.build_workdir(first_device="auto")
        _, view = self._preflight()
        self.assertEqual(view["device"], 5)
        self.assertEqual(view["device_compiled"], 0)
        self.assertIs(view["can_switch_device"], True)

    def test_preflight_sparse_frame_compiled_always_zero(self):
        self.build_workdir(first_device=3, profile="sparse_frame")
        _, view = self._preflight()
        self.assertEqual(view["device"], 3)
        self.assertEqual(view["device_compiled"], 0)

    def test_unknown_profile_refuses_switch(self):
        """未登记 profile：换卡轮判无效（推不出编译卡号），preflight 说不能换。"""
        self.build_workdir(profile="mystery")
        code, view = self._preflight()
        self.assertEqual(code, 0)
        self.assertIsNone(view["device_compiled"])
        self.assertIs(view["can_switch_device"], False)
        self.assertIn("拒绝换卡", view["device_note"])
        self.write_measure_round(
            1, [_passed("TC_PF_1002")], device=6, overrides={"device_compiled": 0},
        )
        self.write_prof_dirs(1, [6])
        code, verdict, _, _, _ = self.run_verdict()
        self.assertEqual(code, 1)
        self._assert_diag_only(verdict, "推不出编译逻辑卡号")

    def test_unknown_profile_same_card_round_stays_valid(self):
        """推不出编译卡号只挡换卡：首轮 auto→5 的同卡轮带 device_compiled 照样有效。

        量具按 preflight 的 needs_device_map 带了 --map-device，于是无条件写出
        device_compiled=0。判据看的是「卡有没有换」，不是「字段在不在」。"""
        self.build_workdir(first_device="auto", profile="mystery")
        _, view = self._preflight()
        self.assertIs(view["can_switch_device"], False)
        self.assertIs(view["needs_device_map"], True)
        self.assertEqual(view["device"], 5)
        self.write_measure_round(
            1, [_passed("TC_PF_1002"), _passed("TC_PF_1003")],
            device=5, overrides={"device_compiled": 0},
        )
        self.write_prof_dirs(1, [5], case="TC_PF_1002")
        self.write_prof_dirs(1, [5], case="TC_PF_1003")
        # 首轮 auto 的工作目录，A5 也要按 auto 口径跑（设备闭合检查在别处）。
        code, verdict, _, _, _ = self.run_verdict(device="auto", device_pool="0,5")
        self.assertEqual(code, 0)
        retest = verdict["performance"]["retest"]
        self.assertEqual(retest["invalid_rounds"], [])
        self.assertEqual(retest["pass_on_retest"], 2)

    def test_switch_without_compiled_field_invalidates(self):
        """换卡轮缺 device_compiled 不许漏网——判据是卡换没换，不是字段在不在。"""
        self.build_workdir(profile="mystery")
        self.write_measure_round(1, [_passed("TC_PF_1002")], device=6)
        self.write_prof_dirs(1, [6])
        code, verdict, _, _, _ = self.run_verdict()
        self.assertEqual(code, 1)
        self._assert_diag_only(verdict, "推不出编译逻辑卡号")

    def test_switch_without_compiled_field_invalidates_known_profile(self):
        self.build_workdir()
        self.write_measure_round(1, [_passed("TC_PF_1002")], device=6)
        self.write_prof_dirs(1, [6])
        code, verdict, _, _, _ = self.run_verdict()
        self.assertEqual(code, 1)
        self._assert_diag_only(verdict, "缺 device_compiled（应为 0）")

    def test_same_card_round_ignores_compiled_mismatch(self):
        """同卡轮不做推导校验：device_compiled 只作记录，不因它判无效。"""
        self.build_workdir()
        self.write_measure_round(
            1, [_passed("TC_PF_1002")], device=0, overrides={"device_compiled": 5},
        )
        self.write_prof_dirs(1, [0])
        code, verdict, _, _, _ = self.run_verdict()
        self.assertEqual(code, 1)  # TC_PF_1003 仍 FAIL
        self.assertEqual(verdict["performance"]["retest"]["invalid_rounds"], [])

    def test_compiled_device_over_upper_bound_blocks_switch(self):
        """首轮卡号超过量具映射串能表达的上界 → can_switch_device 为 false。"""
        self.assertEqual(accept.MAX_COMPILED_DEVICE, 7)
        compiled, error = accept._derive_device_compiled(
            "blas", accept.MAX_COMPILED_DEVICE + 1,
        )
        self.assertIsNone(compiled)
        self.assertIn("上界 7", error)
        self.build_workdir(first_device=8)
        code, view = self._preflight()
        self.assertEqual(code, 0)
        self.assertEqual(view["device"], 8)
        self.assertIsNone(view["device_compiled"])
        self.assertIs(view["can_switch_device"], False)

    def test_non_string_profile_does_not_crash(self):
        """manifest 里 harness_profile 是 list/dict：当未登记处理，同卡复测照跑。

        直接拿不可哈希值查绑卡模式表会抛 TypeError，把同卡复测一起带崩。"""
        for bad in ([], {}, 7, None):
            compiled, error = accept._derive_device_compiled(bad, 0)
            self.assertIsNone(compiled)
            self.assertIn("不在绑卡模式表", error)
        self.build_workdir()
        manifest_path = self.workdir / "runtime" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["harness_profile"] = []
        accept._atomic_json(manifest_path, manifest)
        self.write_measure_round(1, [_passed("TC_PF_1002")], device=0)
        self.write_prof_dirs(1, [0])
        code, view = self._preflight()
        self.assertEqual(code, 0)
        self.assertIs(view["can_switch_device"], False)
        code, verdict, _, _, _ = self.run_verdict()
        self.assertEqual(code, 1)  # TC_PF_1003 仍 FAIL
        self.assertEqual(verdict["performance"]["retest"]["invalid_rounds"], [])
        self.assertEqual(verdict["performance"]["retest"]["pass_on_retest"], 1)

    def test_unimplemented_bind_mode_refuses_switch(self):
        """绑卡模式表里写错模式名时换卡关掉，不静默落回编译期定卡。"""
        with mock.patch.dict(accept.DEVICE_BIND_MODES, {"blas": "compil_bound"}):
            compiled, error = accept._derive_device_compiled("blas", 3)
        self.assertIsNone(compiled)
        self.assertIn("未实现", error)

    def test_broken_manifest_does_not_crash_loader(self):
        """manifest 顶层类型不符：归 None 走既有拒绝输出，不抛 traceback。"""
        self.build_workdir()
        (self.workdir / "runtime" / "manifest.json").write_text(
            "[{}]", encoding="utf-8",
        )
        context = accept.load_retest_context(self.workdir, RUN)
        self.assertFalse(context["supported"])
        self.assertIsNone(context["harness_profile"])
        self.assertIs(context["can_switch_device"], False)
        code, view = self._preflight()
        self.assertEqual(code, 2)
        self.assertIn("顶层不是对象", "；".join(view["refusal_reasons"]))

    def test_scored_round_emits_landing_not_verified_warning(self):
        """计分轮出一条替代告警：设备号来自量具自报，实际落点未独立核对。"""
        self.build_workdir()
        self.write_measure_round(1, [_passed("TC_PF_1002")], device=0)
        code, verdict, report, _, _ = self.run_verdict()
        self.assertEqual(code, 1)
        retest = verdict["performance"]["retest"]
        self.assertEqual(retest["invalid_rounds"], [])
        warnings = "；".join(verdict["performance"]["retest_warnings"])
        self.assertIn("实际落点未独立核对", warnings)
        self.assertIn("实际落点未独立核对", report)


NO_REF_SUMMARY = {
    "expected": 0, "pass": 0, "fail": 0, "no_ref": 0, "no_kernel": 0,
    "crash": 0, "timeout": 0, "missing": 0, "status": "NO_REF",
    "timing_scope": "kernel", "threshold": 0.8, "scope_caveat": False,
}


class TestAuditFixes(RetestContractCase):
    """C2 audit 九项修复的定向回归。"""

    def test_unhashable_status_round_invalid_no_crash(self):
        """P1-1：status 为 [] 等不可哈希值 → 该轮无效跳过，三入口不炸。"""
        self.build_workdir()
        bad_case = {
            **_pf_case("TC_PF_1002", "PASS", 1.1, 9.0),
            "status": [], "warmup_exit": None,
        }
        self.write_measure_round(
            1, [], overrides={"cases": [bad_case], "requested_cases": ["TC_PF_1002"]},
        )
        code, stdout, _ = self.run_cli(["retest-preflight", "--run-id", RUN])
        self.assertEqual(code, 0)
        view = json.loads(stdout)
        self.assertEqual([item["round"] for item in view["invalid_rounds"]], [1])
        self.assertIn(
            "不在合法集合",
            "；".join(view["invalid_rounds"][0]["reasons"]),
        )
        code, verdict, _, _, _ = self.run_verdict()
        self.assertEqual(code, 1)
        retest = verdict["performance"]["retest"]
        self.assertEqual(set(retest), {"interrupted_rounds", "invalid_rounds"})

    def test_empty_expected_set_keeps_no_ref(self):
        """P1-2：有 TC_PF 行但全无基线（期望集空）+ 合法空豁免轮 → NO_REF 分叉保留。"""
        self.build_workdir(
            baseline_gpu_ms="",
            first_override={
                "cases": [],
                "summary": dict(NO_REF_SUMMARY),
                "exit_code": 0,
                "ignored_no_ref": list(PF),
            },
        )
        self.write_waive_round(1, [])
        code, verdict, _, _, _ = self.run_verdict()
        self.assertEqual(code, 2)
        self.assertEqual(verdict["verdict"], "证据不足")
        performance = verdict["performance"]
        self.assertEqual(performance["base_status"], "NO_REF")
        self.assertEqual(performance["status"], "NO_REF")
        # 折叠照常执行（有效轮存在 → 全量输出），只是不改写汇总。
        self.assertIn("fold_protocol_version", performance["retest"])
        self.assertEqual(performance["retest"]["pass_on_retest"], 0)
        self.assertIn(
            "折叠不改写性能结论",
            "；".join(performance["retest_warnings"]),
        )

    def test_early_return_path_keeps_diagnostics(self):
        """P2-2：manifest 读取失败的提前返回路径也要挂中断/无效轮诊断。"""
        self.build_workdir()
        (self.workdir / "runtime" / "manifest.json").write_text(
            "not-json", encoding="utf-8",
        )
        (self.results / f"performance_{RUN}-retest-1.json").write_text(
            "broken", encoding="utf-8",
        )
        self.write_stage_dir(2)
        code, verdict, report, stderr, _ = self.run_verdict()
        self.assertEqual(code, 2)
        self.assertEqual(verdict["verdict"], "证据不足")
        retest = verdict["performance"]["retest"]
        self.assertEqual(set(retest), {"interrupted_rounds", "invalid_rounds"})
        self.assertEqual(retest["interrupted_rounds"], [2])
        self.assertEqual([item["round"] for item in retest["invalid_rounds"]], [1])
        self.assertIn(
            "无法重算性能期望集",
            "；".join(retest["invalid_rounds"][0]["reasons"]),
        )
        self.assertIn("该轮未生效", report)
        self.assertIn("中断轮", report)
        self.assertIn("该轮未生效", stderr)

    def test_truncation_exempts_retest_involved(self):
        """P2-3：第 31+ 位的复测涉入例进主表不受截断，纯首轮例照旧截断。"""
        pf = [(f"TC_PF_{2000 + index:04d}", 100 + index, 100 + index)
              for index in range(1, 36)]
        names = [name for name, _, _ in pf]
        self.build_workdir(pf=pf, pf_statuses={name: "FAIL" for name in names})
        last = names[-1]
        self.write_measure_round(1, [_passed(last)])
        code, verdict, report, _, _ = self.run_verdict()
        self.assertEqual(code, 1)
        row = self.case_row(verdict, last)
        self.assertEqual(row["effective_status"], "PASS")
        self.assertIn(f"| {last} | PASS | 1 |", report)
        self.assertIn("其余 4 条纯首轮例", report)


class TestPublishFailureInjection(RetestContractCase):
    """发布事务故障注入：rename 失败复位、复位失败保留备份、下次启动恢复。"""

    def _verdict_argv(self, out):
        return [
            "verdict", "--package", str(self.pkg), "--repo", str(self.repo),
            "--soc", SOC, "--device", "0", "--run-id", RUN, "--out", str(out),
        ]

    def test_swap_rename_failure_restores_backup(self):
        self.build_workdir()
        code, _, _, _, out = self.run_verdict()
        before = (out / "intermediate" / "verdict.json").read_bytes()
        real_rename = os.rename

        def fake(src, dst):
            if Path(dst).name == "intermediate" and ".tmp-" in Path(src).name:
                raise OSError("注入：tmp→正式 rename 失败")
            return real_rename(src, dst)

        os.chdir(self.workdir)
        with mock.patch.object(accept.os, "rename", fake):
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(RuntimeError) as ctx:
                    accept.main(self._verdict_argv(out))
        self.assertIn("旧版本已复位", str(ctx.exception))
        self.assertEqual((out / "intermediate" / "verdict.json").read_bytes(), before)
        self.assertEqual([p.name for p in out.glob("intermediate.*")], [])

    def test_swap_double_failure_keeps_backup_then_recovers(self):
        self.build_workdir()
        code, _, _, _, out = self.run_verdict()
        real_rename = os.rename

        def fake(src, dst):
            src_name = Path(src).name
            if Path(dst).name == "intermediate" and (
                ".tmp-" in src_name or ".bak-" in src_name
            ):
                raise OSError("注入：指向正式路径的 rename 全部失败")
            return real_rename(src, dst)

        os.chdir(self.workdir)
        stderr = io.StringIO()
        with mock.patch.object(accept.os, "rename", fake):
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(stderr):
                with self.assertRaises(RuntimeError):
                    accept.main(self._verdict_argv(out))
        self.assertIn("手工复位", stderr.getvalue())
        self.assertFalse((out / "intermediate").exists())
        backups = [p.name for p in out.glob("intermediate.bak-*")]
        self.assertEqual(len(backups), 1, "复位失败必须保留备份")
        # 下次 verdict 启动：有备份无正式 → 先恢复再发布，收口无残留。
        code, verdict, _, _, out = self.run_verdict(out=out)
        self.assertEqual(code, 1)
        self.assertTrue((out / "intermediate" / "verdict.json").is_file())
        self.assertEqual([p.name for p in out.glob("intermediate.*")], [])


class TestWaiveCli(RetestContractCase):
    """waive 子命令：写出、校验边界与折叠效果。"""

    def _waive(self, *pairs):
        argv = ["waive", "--run-id", RUN]
        for case, reason in pairs:
            argv += ["--waive", case, reason]
        return self.run_cli(argv)

    def test_waive_writes_minimal_round_and_folds(self):
        self.build_workdir()
        code, stdout, _ = self._waive(("TC_PF_1002", "上游缺陷已立项"))
        self.assertEqual(code, 0)
        path = self.results / f"performance_{RUN}-retest-1.json"
        self.assertTrue(path.is_file())
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(set(payload), {
            "schema_version", "base_run_id", "round", "kind",
            "op", "family", "soc", "repo", "started", "finished", "waivers",
        })
        self.assertEqual(payload["kind"], "waive")
        self.assertEqual(payload["round"], 1)
        self.assertEqual(
            payload["waivers"],
            [{"case": "TC_PF_1002", "reason": "上游缺陷已立项"}],
        )
        code, verdict, report, _, _ = self.run_verdict()
        # 1003 仍 FAIL，豁免 1002 不改总结论。
        self.assertEqual(code, 1)
        row = self.case_row(verdict, "TC_PF_1002")
        self.assertEqual(row["effective_status"], "WAIVED")
        self.assertEqual(row["status"], "WAIVED")
        self.assertIsNone(row["ratio"])
        self.assertEqual(row["reference_round"], 0)
        retest = verdict["performance"]["retest"]
        self.assertEqual(retest["waived"], [
            {"case": "TC_PF_1002", "round": 1, "reason": "上游缺陷已立项"},
        ])
        self.assertIn("豁免（退出裁决分母", report)
        self.assertIn("上游缺陷已立项", report)
        # WAIVED 表带有效复测测量次数列（P2-3 配套）。
        self.assertIn(
            "| case_name | 豁免轮 | 理由 | 参考轮 | 参考状态 | 参考 ratio | 复测次数 |",
            report,
        )

    def test_full_waive_is_insufficient(self):
        self.build_workdir(pf_statuses={name: "FAIL" for name in PF})
        code, _, _ = self._waive(
            ("TC_PF_1001", "理由一"), ("TC_PF_1002", "理由二"), ("TC_PF_1003", "理由三"),
        )
        self.assertEqual(code, 0)
        code, verdict, _, stderr, _ = self.run_verdict()
        self.assertEqual(code, 2)
        self.assertEqual(verdict["performance"]["base_status"], "证据不足")
        self.assertEqual(verdict["verdict"], "证据不足")
        warnings = "；".join(verdict["performance"]["retest_warnings"])
        self.assertIn("豁免不能空转出通过", warnings)

    def test_waive_then_measure_revokes(self):
        self.build_workdir()
        self._waive(("TC_PF_1002", "暂缓"))
        self.write_measure_round(2, [
            _passed("TC_PF_1002"), _passed("TC_PF_1003"),
        ])
        code, verdict, _, _, _ = self.run_verdict()
        self.assertEqual(code, 0)
        row = self.case_row(verdict, "TC_PF_1002")
        self.assertEqual(row["effective_status"], "PASS")
        self.assertEqual(verdict["performance"]["retest"]["waived"], [])
        self.assertEqual(verdict["performance"]["retest"]["pass_on_retest"], 2)

    def test_waive_round_number_skips_interrupted(self):
        self.build_workdir()
        self.write_stage_dir(1)
        code, _, _ = self._waive(("TC_PF_1002", "理由"))
        self.assertEqual(code, 0)
        self.assertTrue(
            (self.results / f"performance_{RUN}-retest-2.json").is_file(),
            "中断轮占号，豁免轮应取 2",
        )

    def test_waive_unknown_case_rejected(self):
        self.build_workdir()
        code, _, stderr = self._waive(("TC_PF_9999", "理由"))
        self.assertEqual(code, 2)
        self.assertIn("不在性能期望集", stderr)
        self.assertEqual(list(self.results.glob(f"performance_{RUN}-retest-*.json")), [])

    def test_waive_empty_reason_rejected(self):
        self.build_workdir()
        code, _, stderr = self._waive(("TC_PF_1002", "  "))
        self.assertEqual(code, 2)
        self.assertIn("必填非空", stderr)
        self.assertEqual(list(self.results.glob(f"performance_{RUN}-retest-*.json")), [])

    def test_waive_duplicate_case_rejected(self):
        self.build_workdir()
        code, _, stderr = self._waive(("TC_PF_1002", "甲"), ("TC_PF_1002", "乙"))
        self.assertEqual(code, 2)
        self.assertIn("重复点名", stderr)

    def test_waive_arity_syntax_error(self):
        """--waive 是二元组（CASE REASON），少一个参数是 argparse 语法错。"""
        self.build_workdir()
        os.chdir(self.workdir)
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                accept.main(["waive", "--run-id", RUN, "--waive", "TC_PF_1002"])
        self.assertEqual(ctx.exception.code, 2)

    def test_waive_rejects_retest_suffixed_run_id(self):
        self.build_workdir()
        code, _, stderr = self.run_cli([
            "waive", "--run-id", f"{RUN}-retest-1", "--waive", "TC_PF_1002", "理由",
        ])
        self.assertEqual(code, 2)
        self.assertIn("base run-id", stderr)

    def test_waive_refused_without_anchor(self):
        self.build_workdir(anchors=False)
        code, _, stderr = self._waive(("TC_PF_1002", "理由"))
        self.assertEqual(code, 2)
        self.assertIn("不支持复测", stderr)
        self.assertEqual(list(self.results.glob(f"performance_{RUN}-retest-*.json")), [])

    def test_waive_identity_mismatch_rejected(self):
        """P2-5：check.json 与首轮锚身份不符 → 拒绝写轮，不落文件。"""
        self.build_workdir()
        check_path = self.workdir / "check.json"
        check = json.loads(check_path.read_text(encoding="utf-8"))
        check["op"] = "xger"
        check_path.write_text(json.dumps(check, ensure_ascii=False), encoding="utf-8")
        code, _, stderr = self._waive(("TC_PF_1002", "理由"))
        self.assertEqual(code, 2)
        self.assertIn("身份不一致", stderr)
        self.assertEqual(list(self.results.glob(f"performance_{RUN}-retest-*.json")), [])

    def test_waive_target_conflict_refused(self):
        """写入竞态：过期上下文给出的轮号已被占用 → 拒绝覆盖，既有文件不动。"""
        self.build_workdir()
        stale_context = accept.load_retest_context(self.workdir, RUN)
        target = self.results / f"performance_{RUN}-retest-1.json"
        target.write_text("{}", encoding="utf-8")
        with mock.patch.object(
            accept, "load_retest_context", return_value=stale_context,
        ):
            code, _, stderr = self._waive(("TC_PF_1002", "理由"))
        self.assertEqual(code, 2)
        self.assertIn("已存在", stderr)
        self.assertEqual(target.read_text(encoding="utf-8"), "{}")


class TestRetestPreflight(RetestContractCase):
    def _preflight(self):
        code, stdout, stderr = self.run_cli(["retest-preflight", "--run-id", RUN])
        return code, json.loads(stdout), stderr

    def test_preflight_ok_explicit_device(self):
        self.build_workdir()
        code, view, _ = self._preflight()
        self.assertEqual(code, 0)
        self.assertTrue(view["supported"])
        self.assertEqual(view["next_round"], 1)
        self.assertEqual(view["device"], 0)
        self.assertIs(view["needs_device_map"], False)
        self.assertEqual(view["expected_cases"], 3)
        self.assertEqual(
            view["anchor_fields"],
            {"normalized_baseline_sha256": True, "verifier_sha256": True},
        )

    def test_preflight_counts_interrupted_round(self):
        self.build_workdir()
        self.write_stage_dir(1)
        code, view, _ = self._preflight()
        self.assertEqual(code, 0)
        self.assertEqual(view["next_round"], 2)
        self.assertEqual(view["interrupted_rounds"], [1])

    def test_preflight_auto_first_round_gives_resolved(self):
        self.build_workdir(first_device="auto")
        code, view, _ = self._preflight()
        self.assertEqual(code, 0)
        self.assertEqual(view["device"], 5)
        # 首轮 auto：复测量具须带 --map-device，把显式卡号映射为逻辑 0。
        self.assertIs(view["needs_device_map"], True)

    def test_missing_anchor_double_line_refusal(self):
        """旧首轮缺锚双线拒绝：preflight 退 2，A5 把已落盘的轮全判无效。"""
        self.build_workdir(anchors=False)
        code, view, stderr = self._preflight()
        self.assertEqual(code, 2)
        self.assertFalse(view["supported"])
        self.assertIn("不支持复测", "；".join(view["refusal_reasons"]))
        self.assertIn("拒绝复测", stderr)
        # 第二线：伪造一个形状完好的测量轮，A5 仍判无效并保持现行裁决。
        self.write_measure_round(1, [_passed("TC_PF_1002"), _passed("TC_PF_1003")])
        code, verdict, report, _, _ = self.run_verdict()
        self.assertEqual(code, 1)
        self.assertEqual(verdict["verdict"], "不通过")
        retest = verdict["performance"]["retest"]
        self.assertEqual(set(retest), {"interrupted_rounds", "invalid_rounds"})
        reasons = "；".join(retest["invalid_rounds"][0]["reasons"])
        self.assertIn("缺绑定锚字段", reasons)
        self.assertIn("该轮未生效", report)


class TestPublishTransaction(RetestContractCase):
    """intermediate/ 发布事务：临时树替换、stale 清除与中断恢复。"""

    def test_stale_intermediate_file_removed_foreign_root_kept(self):
        self.build_workdir()
        _, _, _, _, out = self.run_verdict()
        (out / "intermediate" / "stale.json").write_text("{}", encoding="utf-8")
        (out / "notes.txt").write_text("keep", encoding="utf-8")
        code, _, _, _, out = self.run_verdict(out=out)
        self.assertEqual(code, 1)
        self.assertFalse((out / "intermediate" / "stale.json").exists())
        self.assertTrue((out / "notes.txt").is_file())
        self.assertEqual([p.name for p in out.glob("intermediate.*")], [])

    def test_orphan_tmp_removed_on_next_run(self):
        self.build_workdir()
        out = self.root / "verdict-out"
        orphan = out / "intermediate.tmp-99999"
        orphan.mkdir(parents=True)
        (orphan / "junk.json").write_text("{}", encoding="utf-8")
        code, _, _, _, out = self.run_verdict(out=out)
        self.assertEqual(code, 1)
        self.assertFalse(orphan.exists())
        self.assertTrue((out / "intermediate" / "verdict.json").is_file())

    def test_backup_without_official_restored_then_replaced(self):
        self.build_workdir()
        code, _, _, _, out = self.run_verdict()
        official = out / "intermediate"
        backup = out / "intermediate.bak-20200101000000"
        os.rename(official, backup)
        self.assertFalse(official.exists())
        code, verdict, _, _, out = self.run_verdict(out=out)
        self.assertEqual(code, 1)
        self.assertTrue((official / "verdict.json").is_file())
        self.assertFalse(backup.exists())

    def test_backup_with_official_cleaned(self):
        self.build_workdir()
        code, _, _, _, out = self.run_verdict()
        leftover = out / "intermediate.bak-20200101000000"
        leftover.mkdir()
        (leftover / "old.json").write_text("{}", encoding="utf-8")
        code, _, _, _, out = self.run_verdict(out=out)
        self.assertEqual(code, 1)
        self.assertFalse(leftover.exists())


class TestNotesMerge(RetestContractCase):
    def test_notes_file_merged_into_report(self):
        self.build_workdir()
        (self.workdir / "verdict_notes.md").write_text(
            "wrapper 调用 2 次，calls_per_case=2 依据见 A2′。\n", encoding="utf-8",
        )
        _, _, report, _, _ = self.run_verdict()
        self.assertIn("wrapper 调用 2 次", report)
        self.assertNotIn("<由验收 agent 填写", report)


class TestLoaderRoundtrip(RetestContractCase):
    """加载/校验往返：load_retest_context 是三个入口共用的唯一加载层。"""

    def test_context_shape_and_classification(self):
        self.build_workdir()
        good = self.write_measure_round(1, [_passed("TC_PF_1002")])
        (self.results / f"performance_{RUN}-retest-2.json").write_text(
            "broken", encoding="utf-8",
        )
        self.write_stage_dir(3)
        context = accept.load_retest_context(self.workdir, RUN)
        self.assertTrue(context["supported"])
        self.assertEqual(context["identity"], {
            "op": OP, "family": FAMILY, "soc": SOC, "repo": str(self.repo),
        })
        self.assertEqual(context["expected"], list(PF))
        self.assertEqual(context["inventory"]["occupied"], [1, 2, 3])
        self.assertEqual(context["inventory"]["interrupted"], [3])
        self.assertEqual(context["inventory"]["next_round"], 4)
        self.assertEqual(
            [item["round"] for item in context["valid_rounds"]], [1],
        )
        self.assertEqual(context["valid_rounds"][0]["path"], str(good))
        self.assertEqual(
            [item["round"] for item in context["invalid_rounds"]], [2],
        )

    def test_split_retest_run_id_rightmost(self):
        self.assertEqual(accept._split_retest_run_id("a-retest-2"), ("a", 2))
        self.assertEqual(
            accept._split_retest_run_id("a-retest-2-retest-3"), ("a-retest-2", 3),
        )
        self.assertEqual(accept._split_retest_run_id("a-retest-0"), ("a-retest-0", None))
        self.assertEqual(accept._split_retest_run_id("plain"), ("plain", None))


class TestCrossLaneDeviceSwitch(integration.RetestIntegrationCase):
    """跨 lane 往返：case-gen 真渲染的量具跑出真轮 JSON，再由本侧加载/校验/折叠消费。

    手写 JSON 测不到的三件事只有在这里才拦得住：量具真实的重映射串、
    `device_compiled` 的条件序列化（只有 `--map-device` 才写），以及阶段目录里
    PROF 落点的真实层级与本侧扫描 glob 是否对得上。"""

    # 首轮物理卡。blas 是 compile_bound，编译逻辑卡号随首轮显式卡号，取非 0 才
    # 测得到「目标卡要顶到第 N 位」那条——首轮 0 时映射串退化成目标卡本身。
    CARD = "3"

    def run_cli(self, argv, cwd=None):
        """把 accept 侧命令行的 --device 统一改到 CARD（check 与 verdict 共用）。"""
        patched = list(argv)
        for index, item in enumerate(patched[:-1]):
            if item == "--device":
                patched[index + 1] = self.CARD
        return super().run_cli(patched, cwd)

    # ---- 量具侧 ----

    def run_gauge_argv(self, gauge, argv):
        os.chdir(self.workdir)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = gauge.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def retest_argv(self, run_id, case, device, extra=()):
        return [
            "--repo", str(self.repo), "--soc", integration.SOC,
            "--device", str(device), "--run-id", run_id, "--skip-build",
            "--calls-per-case", "1", "--case", case, *extra,
        ]

    def first_round_on_card(self):
        """首轮跑在 CARD 上（基类的 run_gauge 写死 --device 0，这里另起一条）。"""
        gauge = self.load_gauge(integration.FIRST_KERNELS)
        code, _, stderr = self.run_gauge_argv(gauge, [
            "--repo", str(self.repo), "--soc", integration.SOC,
            "--device", self.CARD, "--run-id", integration.RUN,
            "--skip-build", "--calls-per-case", "1",
        ])
        self.assertEqual(code, 1, f"首轮应为不通过（1002/1003 FAIL）：{stderr}")
        self.first_path = self.results / f"performance_{integration.RUN}.json"
        self.first = json.loads(self.first_path.read_text(encoding="utf-8"))
        self.assertEqual(self.first["device"], int(self.CARD))
        self.write_accuracy()
        path = self.results / f"accuracy_{integration.RUN}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["device"] = int(self.CARD)
        accept._atomic_json(path, payload)

    # ---- 产物回读 ----

    def round_payload(self, number):
        path = self.results / f"performance_{integration.RUN}-retest-{number}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def stage_case_dirs(self, number):
        """该轮阶段目录下量具真建的逐例采样目录 `prof/<case>/`。

        `r<N>/` 由 msprof 按 `--output` 自己建，subprocess 打桩后不存在，量具只
        在这一层留下 `r<N>.log`；落点由 plant_prof_dirs 照真实层级补齐。"""
        stage = self.results / f"{integration.RUN}-retest-{number}" / "performance"
        cases = sorted(path for path in stage.glob("prof/*") if path.is_dir())
        self.assertTrue(cases, f"量具应已建出逐例采样目录：{stage}")
        return cases

    def plant_prof_dirs(self, number, card, repeat=1):
        """补 msprof 的落点：`prof/<case>/r<N>/PROF_*/device_<卡>`。"""
        for index, case_dir in enumerate(self.stage_case_dirs(number), 1):
            (case_dir / f"r{repeat}" / f"PROF_{index:06d}_x"
             / f"device_{card}").mkdir(parents=True)
        return self.stage_case_dirs(number)

    def set_profile(self, profile):
        """改写真 manifest 的 harness_profile，模拟绑卡模式未登记的算子域。"""
        path = self.runtime / "manifest.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["harness_profile"] = profile
        accept._atomic_json(path, payload)

    # ---- 用例 ----

    def test_switch_with_nonzero_compiled_device(self):
        """首轮卡 3 → 换到卡 6：量具真写 device_compiled=3，本侧判有效并折叠。"""
        self.build_real_workdir()
        self.first_round_on_card()
        _, stdout, _ = self.run_cli(["retest-preflight", "--run-id", integration.RUN])
        view = json.loads(stdout)
        self.assertEqual(view["device"], 3)
        self.assertEqual(view["device_compiled"], 3)
        self.assertIs(view["can_switch_device"], True)
        gauge = self.load_gauge({"TC_PF_1002": 9000.0}, mapped=("TC_PF_1002",))
        # 编译逻辑卡号上界在两侧各有一份常量：本侧靠它把 can_switch_device 关掉，
        # 量具靠它报参数错误。两个数走开就会出现「这边说能换、量具随后拒绝」。
        self.assertEqual(accept.MAX_COMPILED_DEVICE, gauge.MAX_COMPILED_DEVICE)
        # 量具自己的构造函数：目标卡顶到第 3 位，前三位是占位卡。
        self.assertEqual(
            gauge._visible_devices_map(6, view["device_compiled"]), "0,1,2,6",
        )
        code, _, stderr = self.run_gauge_argv(gauge, self.retest_argv(
            f"{integration.RUN}-retest-1", "TC_PF_1002", 6,
            extra=("--map-device", "--compiled-device", str(view["device_compiled"])),
        ))
        self.assertEqual(code, 0, stderr)
        payload = self.round_payload(1)
        self.assertEqual(payload["device_requested"], 6)
        self.assertEqual(payload["device_resolved"], 6)
        self.assertEqual(payload["device_compiled"], 3)
        self.plant_prof_dirs(1, 6)
        context = accept.load_retest_context(self.workdir, integration.RUN)
        self.assertEqual([item["round"] for item in context["valid_rounds"]], [1])
        code, verdict, report, _ = self.run_verdict()
        self.assertEqual(verdict["performance"]["retest"]["pass_on_retest"], 1)
        self.assertEqual(self.case_row(verdict, "TC_PF_1002")["effective_status"], "PASS")
        self.assertIn("| TC_PF_1002 | 1 | measure | 6 |", report)

    def test_target_card_inside_placeholder_range(self):
        """目标卡 2 小于编译卡号 3：占位集合跳过目标卡，串仍把它顶到第 3 位。"""
        self.build_real_workdir()
        self.first_round_on_card()
        gauge = self.load_gauge({"TC_PF_1002": 9000.0}, mapped=("TC_PF_1002",))
        self.assertEqual(gauge._visible_devices_map(2, 3), "0,1,3,2")
        code, _, stderr = self.run_gauge_argv(gauge, self.retest_argv(
            f"{integration.RUN}-retest-1", "TC_PF_1002", 2,
            extra=("--map-device", "--compiled-device", "3"),
        ))
        self.assertEqual(code, 0, stderr)
        payload = self.round_payload(1)
        self.assertEqual(payload["device_resolved"], 2)
        self.assertEqual(payload["device_compiled"], 3)
        self.plant_prof_dirs(1, 2)
        context = accept.load_retest_context(self.workdir, integration.RUN)
        self.assertEqual([item["round"] for item in context["valid_rounds"]], [1])
        # 落卡核对已撤除：阶段目录里多出别的卡的落点也不再影响轮有效性。
        # 换卡是否真落在目标卡，从此只有量具自报的 device_resolved 一个来源。
        self.stage_case_dirs(1)[0].joinpath("r1", "PROF_000009_x", "device_5").mkdir(
            parents=True,
        )
        context = accept.load_retest_context(self.workdir, integration.RUN)
        self.assertEqual([item["round"] for item in context["valid_rounds"]], [1])
        self.assertEqual(context["invalid_rounds"], [])

    def test_unknown_profile_same_card_valid_switch_invalid(self):
        """未登记 profile 两路：同卡轮照常有效，换卡轮因推不出编译卡号判无效。"""
        self.build_real_workdir()
        self.first_round_on_card()
        self.set_profile("mystery")
        gauge = self.load_gauge({"TC_PF_1002": 9000.0}, mapped=("TC_PF_1002",))
        code, _, stderr = self.run_gauge_argv(gauge, self.retest_argv(
            f"{integration.RUN}-retest-1", "TC_PF_1002", self.CARD,
        ))
        self.assertEqual(code, 0, stderr)
        self.assertNotIn("device_compiled", self.round_payload(1))
        self.plant_prof_dirs(1, int(self.CARD))
        context = accept.load_retest_context(self.workdir, integration.RUN)
        self.assertIs(context["can_switch_device"], False)
        self.assertEqual([item["round"] for item in context["valid_rounds"]], [1])
        gauge2 = self.load_gauge({"TC_PF_1003": 9000.0}, mapped=("TC_PF_1003",))
        code, _, stderr = self.run_gauge_argv(gauge2, self.retest_argv(
            f"{integration.RUN}-retest-2", "TC_PF_1003", 6,
            extra=("--map-device", "--compiled-device", "0"),
        ))
        self.assertEqual(code, 0, stderr)
        self.plant_prof_dirs(2, 6)
        context = accept.load_retest_context(self.workdir, integration.RUN)
        self.assertEqual([item["round"] for item in context["valid_rounds"]], [1])
        self.assertEqual([item["round"] for item in context["invalid_rounds"]], [2])
        self.assertIn(
            "推不出编译逻辑卡号",
            "；".join(context["invalid_rounds"][0]["reasons"]),
        )

    def test_switch_without_map_device_is_caught(self):
        """真量具不带 --map-device 换卡时不写 device_compiled，本侧照样判无效。"""
        self.build_real_workdir()
        self.first_round_on_card()
        gauge = self.load_gauge({"TC_PF_1002": 9000.0}, mapped=("TC_PF_1002",))
        code, _, stderr = self.run_gauge_argv(gauge, self.retest_argv(
            f"{integration.RUN}-retest-1", "TC_PF_1002", 6,
        ))
        self.assertEqual(code, 0, stderr)
        payload = self.round_payload(1)
        self.assertNotIn("device_compiled", payload)
        self.assertEqual(payload["device_resolved"], 6)
        self.plant_prof_dirs(1, 6)
        context = accept.load_retest_context(self.workdir, integration.RUN)
        self.assertEqual(context["valid_rounds"], [])
        self.assertIn(
            "缺 device_compiled（应为 3）",
            "；".join(context["invalid_rounds"][0]["reasons"]),
        )


if __name__ == "__main__":
    unittest.main()
