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
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import retest_fold  # noqa: E402


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
                      pf=None, baseline_gpu_ms="10.0", first_override=None):
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
            "harness_profile": "blas",
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
            "device": 0,
            "device_pool": None,
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
            "device": 0,
            "device_pool": None,
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

    def run_verdict(self, out=None):
        out = Path(out) if out else self.root / "verdict-out"
        code, stdout, stderr = self.run_cli([
            "verdict", "--package", str(self.pkg), "--repo", str(self.repo),
            "--soc", SOC, "--device", "0", "--run-id", RUN, "--out", str(out),
        ])
        verdict = json.loads((out / "intermediate" / "verdict.json").read_text(encoding="utf-8"))
        report = (out / "report" / "report.md").read_text(encoding="utf-8")
        return code, verdict, report, stderr, out

    def case_row(self, verdict, name):
        for row in verdict["performance"]["case_rows"]:
            if row["name"] == name:
                return row
        raise AssertionError(f"case_rows 缺 {name}")


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

    def _assert_diag_only(self, verdict, fragment):
        retest = verdict["performance"]["retest"]
        self.assertEqual(set(retest), {"interrupted_rounds", "invalid_rounds"})
        reasons = "；".join(
            reason for item in retest["invalid_rounds"] for reason in item["reasons"]
        )
        self.assertIn(fragment, reasons)

    def test_binding_mismatch_invalidates(self):
        self.build_workdir()
        self.write_measure_round(
            1, [_passed("TC_PF_1002")], overrides={"csv_sha256": "f" * 64},
        )
        code, verdict, _, _, _ = self.run_verdict()
        self.assertEqual(code, 1)
        self.assertEqual(verdict["verdict"], "不通过")
        self._assert_diag_only(verdict, "csv_sha256")

    def test_device_mismatch_invalidates(self):
        self.build_workdir()
        self.write_measure_round(1, [_passed("TC_PF_1002")], device=3)
        code, verdict, _, _, _ = self.run_verdict()
        self.assertEqual(code, 1)
        self._assert_diag_only(verdict, "不等于首轮锚定物理卡")

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


if __name__ == "__main__":
    unittest.main()
