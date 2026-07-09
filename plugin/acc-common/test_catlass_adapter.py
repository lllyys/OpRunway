"""P3 catlass adapter 单测（stdlib unittest）——真实 fixture 驱动，不依赖真机/NPU。

跑: python3 test_catlass_adapter.py          （在 acc-common/ 下，全绿即过）
或: python3 -m unittest test_catlass_adapter -v

覆盖 task 自测清单：arch 探测（env/environment.json/缺失 fail-fast·不猜3510/非法拒）、example 选择 +
CMake arch 注入拼装、三件套数据流（X_logical vs X_bin 分两份·禁共用 reshape）、log parser（success/
failed/崩溃/空日志·坏输入不崩）、msprof kernel-only 解析（真实 CSV·非 kernel-only 拒·按列名）、7 方法签名齐全、
mock 端到端 + defect 翻 FAIL、外部 GPU 基线校验（scope 不符/缺用例 blocked）、profile 命中门、下游门兼容。
"""
import inspect, json, os, shutil, tempfile, unittest
import numpy as np

import catlass_adapter as A
import catlass_parse as P

_HERE = os.path.dirname(os.path.abspath(__file__))
_TD = os.path.join(_HERE, "testdata")


def _read(name):
    with open(os.path.join(_TD, name), encoding="utf-8") as f:
        return f.read()


def _demo_spec():
    return {"op": "CatlassBasicMatmul", "verify_mode": "numerical",
            "precision": {"threshold": 1e-3, "dtype": "float32"},
            "params": [{"name": "A", "io": "in", "dtype": ["float32"], "layout": "RowMajor"},
                       {"name": "B", "io": "in", "dtype": ["float32"], "layout": "RowMajor"},
                       {"name": "C", "io": "out", "dtype": ["float32"], "layout": "RowMajor"}],
            "cases": {"functional": [[16, 16, 16], [8, 12, 20]], "perf": [[64, 64, 64]]}}


class ArchDetectTest(unittest.TestCase):
    """子任务①：arch 运行时探测——零硬编码、探不到诚实报错、绝不猜 3510。"""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self._saved = os.environ.pop("OPRUNWAY_CATLASS_ARCH", None)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)
        os.environ.pop("OPRUNWAY_CATLASS_ARCH", None)
        if self._saved is not None:
            os.environ["OPRUNWAY_CATLASS_ARCH"] = self._saved

    def test_env(self):
        os.environ["OPRUNWAY_CATLASS_ARCH"] = "3510"
        self.assertEqual(A._catlass_arch(self.d)[0], "3510")

    def test_explicit_beats_env(self):
        os.environ["OPRUNWAY_CATLASS_ARCH"] = "3510"
        self.assertEqual(A._catlass_arch(self.d, explicit="2201")[0], "2201")

    def test_environment_json_catlass_arch(self):
        json.dump({"catlass_arch": "2201"}, open(os.path.join(self.d, "environment.json"), "w"))
        arch, src = A._catlass_arch(self.d)
        self.assertEqual(arch, "2201")
        self.assertIn("environment.json", src)

    def test_environment_json_soc_mapping(self):
        json.dump({"soc": "Ascend950PR_9579"}, open(os.path.join(self.d, "environment.json"), "w"))
        self.assertEqual(A._catlass_arch(self.d)[0], "3510")

    def test_missing_fails_no_guess(self):
        # 无 env、无 environment.json → 必须 raise，绝不默认/猜 3510
        with self.assertRaises(ValueError):
            A._catlass_arch(self.d)

    def test_unknown_soc_no_guess(self):
        json.dump({"soc": "TotallyUnknownChip"}, open(os.path.join(self.d, "environment.json"), "w"))
        with self.assertRaises(ValueError):
            A._catlass_arch(self.d)

    def test_illegal_arch_rejected(self):
        os.environ["OPRUNWAY_CATLASS_ARCH"] = "9999"
        with self.assertRaises(ValueError):
            A._catlass_arch(self.d)

    def test_no_default_fallback_arch(self):
        # production 路径无默认/兜底 arch（codex #7：白名单枚举 + SOC→arch 映射 + 报错信息里的字面允许，
        # 但禁 env/dict get 的 arch 默认参数、禁 return 字面 arch）。
        import re as _re
        src = inspect.getsource(A)
        forbidden = [
            _re.compile(r"""get\(\s*["'][^"']*["']\s*,\s*["'](?:2201|3510)"""),  # get(..., "3510")
            _re.compile(r"""\breturn\s+["'](?:2201|3510)["']"""),                # return "3510"
        ]
        for rx in forbidden:
            self.assertIsNone(rx.search(src), f"发现默认/兜底 arch 字面：{rx.pattern}")


class ExampleSelectAndCmakeTest(unittest.TestCase):
    """子任务②：example / harness 选择 + CMake arch 注入命令拼装。"""

    def test_profile_indexed_by_arch(self):
        p3510 = A.catlass_profile("3510")
        self.assertEqual(p3510["example"], "43_ascend950_basic_matmul")
        self.assertEqual(p3510["dtype"], "float32")
        self.assertEqual(p3510["archtag"], "Ascend950")
        p2201 = A.catlass_profile("2201")
        self.assertEqual(p2201["example"], "00_basic_matmul")
        self.assertEqual(p2201["dtype"], "float16")

    def test_cmake_arch_option(self):
        self.assertEqual(A.cmake_arch_option("3510"), "-DCATLASS_ARCH=3510")
        self.assertEqual(A.cmake_arch_option("2201"), "-DCATLASS_ARCH=2201")
        with self.assertRaises(ValueError):
            A.cmake_arch_option("9999")

    def test_build_command(self):
        argv, disp = A.build_command("3510")
        self.assertIn("scripts/build.sh", disp)
        self.assertIn("-DCATLASS_ARCH=3510", disp)
        self.assertIn("oprunway_catlass_basic_matmul_950", disp)
        self.assertEqual(argv[0], "bash")

    def test_unknown_arch_profile_raises(self):
        with self.assertRaises(ValueError):
            A.catlass_profile("2201x")


class LayoutByteContractTest(unittest.TestCase):
    """子任务③：三件套数据流——X_logical 喂 golden、X_bin 摆物理字节，分两份造、禁共用 reshape。"""

    def test_split_two_independent(self):
        arr = np.arange(6, dtype=np.float32).reshape(2, 3)
        x_logical, x_bin = A.split_logical_physical(arr, "RowMajor")
        # X_logical 是独立副本，非同一 buffer 的别名（禁共用 reshape 的核心保证）
        self.assertIsNot(x_logical, arr)
        self.assertFalse(np.shares_memory(x_logical, arr))
        arr[0, 0] = -999.0
        self.assertEqual(x_logical[0, 0], 0.0)  # 改原数组不影响已造的 X_logical

    def test_rowmajor_bytes_equal_c_order(self):
        arr = np.arange(6, dtype=np.float32).reshape(2, 3)
        _, x_bin = A.split_logical_physical(arr, "RowMajor")
        self.assertEqual(x_bin, arr.tobytes(order="C"))

    def test_columnmajor_bytes_differ_from_logical(self):
        # 非对称矩阵：ColumnMajor 物理字节（F 序）必须 ≠ 逻辑行主序字节 → 证明两者独立摆放
        arr = np.arange(6, dtype=np.float32).reshape(2, 3)
        x_logical, x_bin = A.split_logical_physical(arr, "ColumnMajor")
        self.assertEqual(x_bin, arr.tobytes(order="F"))
        self.assertNotEqual(x_bin, x_logical.tobytes(order="C"))

    def test_unknown_layout_raises(self):
        with self.assertRaises(ValueError):
            A.split_logical_physical(np.zeros(3, dtype=np.float32), "Nonsense")

    def test_materialize_writes_bin_and_manifest(self):
        d = tempfile.mkdtemp()
        try:
            os.environ["OPRUNWAY_CATLASS_ARCH"] = "3510"
            cs = A.build_matmul_caseset(_demo_spec(), d)
            ctx = A.discover(cs, d)
            case = cs["cases"][0]  # 16x16x16
            line = A.materialize_case(case, ctx)
            parts = line.split()
            self.assertEqual(parts[0], case["id"])
            self.assertEqual(parts[1], "float32")
            self.assertEqual(parts[2:], ["16", "16", "16"])  # m n k
            a_bin = os.path.join(d, case["id"], "A.bin")
            self.assertTrue(os.path.exists(a_bin))
            self.assertEqual(os.path.getsize(a_bin), 16 * 16 * 4)  # m*k*fp32
        finally:
            os.environ.pop("OPRUNWAY_CATLASS_ARCH", None)
            shutil.rmtree(d, ignore_errors=True)


class GoldenTest(unittest.TestCase):
    def test_matmul_golden_matches_numpy_f32(self):
        a = np.random.default_rng(0).uniform(-2, 2, (8, 5)).astype(np.float32)
        b = np.random.default_rng(1).uniform(-2, 2, (5, 7)).astype(np.float32)
        g = A.golden_catlass_matmul(a, b, "float32")
        self.assertEqual(g.shape, (8, 7))
        np.testing.assert_allclose(g, a.astype(np.float32) @ b.astype(np.float32), rtol=1e-6)


class RawLogParseTest(unittest.TestCase):
    """子任务④：raw log → 结构化信号（不判定），4 场景 + 坏输入不崩。"""

    def test_success(self):
        r = P.parse_raw_log(_read("catlass_compare_success.log"))
        self.assertEqual(r["cases"], {"catlassbasicmatmul_000": "ok",
                                      "catlassbasicmatmul_001": "ok",
                                      "catlassbasicmatmul_002": "ok"})
        self.assertEqual(r["failed_count"], 0)
        self.assertFalse(r["crashed"])
        self.assertTrue(r["done"])

    def test_failed(self):
        r = P.parse_raw_log(_read("catlass_compare_failed.log"))
        self.assertEqual(r["cases"]["catlassbasicmatmul_001"], "fail")
        self.assertEqual(r["failed_count"], 1)
        self.assertEqual(r["success_count"], 2)
        self.assertFalse(r["crashed"])

    def test_crash(self):
        r = P.parse_raw_log(_read("catlass_crash.log"))
        self.assertTrue(r["crashed"])       # 命中崩溃信号 + 缺收尾哨兵
        self.assertFalse(r["done"])

    def test_empty(self):
        r = P.parse_raw_log(_read("catlass_empty.log"))
        self.assertEqual(r["cases"], {})
        self.assertEqual(r["sentinel_total"], 0)
        self.assertFalse(r["crashed"])

    def test_bad_input_no_crash(self):
        for bad in (None, b"\xff\xfe rubbish", "no sentinels here"):
            r = P.parse_raw_log(bad)          # 不抛
            self.assertIn("cases", r)


class MsprofParseTest(unittest.TestCase):
    """子任务⑤：msprof kernel-only 解析——按列名、取 median/p90/min、非 kernel-only 拒。"""

    def test_ok_median_p90_min(self):
        r = P.parse_msprof_csv(_read("catlass_msprof_OpBasicInfo.csv"))
        self.assertTrue(r["ok"])
        self.assertEqual(r["scope"], "kernel_only")
        self.assertEqual(r["count"], 5)
        self.assertAlmostEqual(r["median_us"], 42.10, places=2)  # 排序中位
        self.assertAlmostEqual(r["min_us"], 40.88, places=2)
        self.assertIn("oprunway_catlass_basic_matmul_950", r["kernels"])

    def test_kernel_name_filter(self):
        r = P.parse_msprof_csv(_read("catlass_msprof_OpBasicInfo.csv"),
                               kernel_name="oprunway_catlass_basic_matmul_950")
        self.assertEqual(r["count"], 5)
        r2 = P.parse_msprof_csv(_read("catlass_msprof_OpBasicInfo.csv"),
                                kernel_name="no_such_kernel")
        self.assertFalse(r2["ok"])  # 无匹配行

    def test_e2e_scope_rejected(self):
        r = P.parse_msprof_csv(_read("catlass_msprof_e2e.csv"))
        self.assertFalse(r["ok"])
        self.assertIn("kernel-only", r["reason"])

    def test_bad_header_rejected(self):
        r = P.parse_msprof_csv(_read("catlass_msprof_bad_header.csv"))
        self.assertFalse(r["ok"])
        self.assertIn("Task Duration", r["reason"])

    def test_empty_csv(self):
        r = P.parse_msprof_csv("")
        self.assertFalse(r["ok"])

    def test_profile_hit_gate(self):
        csv = _read("catlass_msprof_OpBasicInfo.csv")
        # 精确全名 → 命中且非 pending（加固后不再前缀匹配，避免过宽命中）
        g = P.profile_hit_gate(csv, expected_kernel="oprunway_catlass_basic_matmul_950")
        self.assertTrue(g["hit"])
        self.assertFalse(g["pending"])
        self.assertIn("oprunway_catlass_basic_matmul_950", g["matched"])
        # 前缀场景改走受控 regex（对抗门 #9：startswith 过宽已消除）
        g_re = P.profile_hit_gate(csv, kernel_regex="oprunway_catlass_basic_matmul.*")
        self.assertTrue(g_re["hit"])
        self.assertFalse(g_re["pending"])
        # 符号未预知：诚实标 pending，且**不**声称 hit（对抗门 #8：hit ⟺ ¬pending 不变量，
        # 禁「一边命中一边说未验证」的自相矛盾态）；实测 kernel 名回填 observed 供人核。
        g2 = P.profile_hit_gate(csv)
        self.assertFalse(g2["hit"])
        self.assertTrue(g2["pending"])
        self.assertIn("oprunway_catlass_basic_matmul_950", g2["observed_kernels"])


class SevenMethodsTest(unittest.TestCase):
    """repo-adapter.md 的 7 方法签名齐全（discover/build/materialize_case/run_correctness/
    run_perf/parse_results/collect_artifacts）。"""

    def test_all_present_and_callable(self):
        methods = ["discover", "build", "materialize_case", "run_correctness",
                   "run_perf", "parse_results", "collect_artifacts"]
        for m in methods:
            fn = getattr(A, m, None)
            self.assertTrue(callable(fn), f"缺 7 方法之一: {m}")

    def test_signatures(self):
        self.assertIn("caseset", inspect.signature(A.discover).parameters)
        self.assertIn("case", inspect.signature(A.materialize_case).parameters)
        self.assertIn("ctx", inspect.signature(A.materialize_case).parameters)
        self.assertIn("mode", inspect.signature(A.build).parameters)


class MockEndToEndTest(unittest.TestCase):
    """mock 端到端跑穿 7 阶段 + evidence schema + defect 翻 FAIL + grade/NON-ACCEPTANCE。"""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        os.environ["OPRUNWAY_CATLASS_ARCH"] = "3510"
        self.cs = A.build_matmul_caseset(_demo_spec(), self.d)

    def tearDown(self):
        os.environ.pop("OPRUNWAY_CATLASS_ARCH", None)
        shutil.rmtree(self.d, ignore_errors=True)

    def test_evidence_schema(self):
        ev = A.run_catlass_mock(self.cs, self.d)
        self.assertEqual(ev["repo_mode"], "catlass_mock")
        self.assertEqual(ev["harness_kind"], "generated_harness")
        self.assertEqual(ev["evidence_grade"], "development")
        self.assertIn("NON-ACCEPTANCE", ev["acceptance_note"])
        self.assertEqual(len(ev["evidence"]), len(self.cs["cases"]))
        for e in ev["evidence"]:
            self.assertEqual(e["status"], "ok")
            # T5 结构化 precision（对齐 repo_adapter._precision_evidence）：standard/policy/metrics 全带
            self.assertEqual(e["precision"]["standard"], "ascendoptest_default")
            self.assertIsInstance(e["precision"]["policy"], dict)
            self.assertIsInstance(e["precision"]["metrics"], dict)
            self.assertEqual(e["perf"]["scope"], "kernel_only")
            self.assertIn("threshold", e["precision"])
            self.assertIn("tolerance_policy_id", e["precision"])

    def test_perfect_mock_precision_zero(self):
        ev = A.run_catlass_mock(self.cs, self.d)
        for e in ev["evidence"]:
            self.assertEqual(e["precision"]["metrics"]["bad_count"], 0)  # kernel out=golden → 0 坏点

    def test_perf_only_on_perf_cases(self):
        ev = A.run_catlass_mock(self.cs, self.d)
        perf_ids = {c["id"] for c in self.cs["cases"] if "性能" in c["dims"]}
        for e in ev["evidence"]:
            has_us = e["perf"].get("us") is not None
            self.assertEqual(has_us, e["case_id"] in perf_ids)

    def test_defect_injection_flips_fail(self):
        bad = self.cs["cases"][0]["id"]
        ev = A.run_catlass_mock(self.cs, self.d, defect_cases=[bad])
        by = {e["case_id"]: e for e in ev["evidence"]}
        self.assertGreater(by[bad]["precision"]["metrics"]["bad_count"], 0)  # 有坏点 → validator 会判 fail

    def test_artifact_manifest_written(self):
        A.run_catlass_mock(self.cs, self.d)
        with open(os.path.join(self.d, "artifact_manifest.json"), encoding="utf-8") as f:
            man = json.load(f)
        for k in ("arch", "harness_kind", "build_cmd", "kernel_symbol", "runner_sha256"):
            self.assertIn(k, man)
        self.assertEqual(man["harness_kind"], "generated_harness")


class RealPathStubTest(unittest.TestCase):
    """真机路径诚实留桩：默认 fail-fast（不误触 ssh），本地 materialize 仍先跑。"""

    def test_run_catlass_blocks_without_optin(self):
        d = tempfile.mkdtemp()
        try:
            os.environ["OPRUNWAY_CATLASS_ARCH"] = "3510"
            os.environ.pop("OPRUNWAY_CATLASS_REAL", None)
            cs = A.build_matmul_caseset(_demo_spec(), d)
            with self.assertRaises(RuntimeError) as cm:
                A.run_catlass(cs, d)
            self.assertIn("待 ascend-a5", str(cm.exception))
            # 但本地 materialize 已完成（manifest + A.bin 落盘）
            self.assertTrue(os.path.exists(os.path.join(d, "manifest.txt")))
        finally:
            os.environ.pop("OPRUNWAY_CATLASS_ARCH", None)
            shutil.rmtree(d, ignore_errors=True)


class ExternalBaselineTest(unittest.TestCase):
    """子任务⑥：外部 GPU 基线校验——ok / scope 不符 blocked / 缺性能用例 blocked / extras 告警。"""

    def test_ok_with_extras_warned(self):
        bl = A.load_external_baseline(os.path.join(_TD, "catlass_gpu_baseline.json"),
                                      ["catlassbasicmatmul_002"])
        self.assertEqual(bl["scope"], "kernel_only")
        self.assertEqual(bl["baseline_source"], "gpu_external")
        ids = {r["case_id"] for r in bl["per_case"]}
        self.assertEqual(ids, {"catlassbasicmatmul_002"})   # extras 忽略
        self.assertTrue(any("多出" in w for w in bl["warnings"]))

    def test_e2e_scope_blocked(self):
        with self.assertRaises(A.BaselineBlocked):
            A.load_external_baseline(os.path.join(_TD, "catlass_gpu_baseline_e2e.json"),
                                     ["catlassbasicmatmul_002"])

    def test_missing_perf_case_blocked(self):
        with self.assertRaises(A.BaselineBlocked) as cm:
            A.load_external_baseline(os.path.join(_TD, "catlass_gpu_baseline.json"),
                                     ["catlassbasicmatmul_002", "catlassbasicmatmul_999"])
        self.assertIn("缺性能用例", str(cm.exception))


class DownstreamCompatTest(unittest.TestCase):
    """集成：mock 产物喂当前 worktree 的 validator/perf_compare/validate_acceptance_state → 门可过。

    证明 adapter evidence schema 与既有确定性脚本链兼容（下游被并行任务改动时，此测试即整合面）。
    """

    def setUp(self):
        self.out = tempfile.mkdtemp()
        self.work = os.path.join(self.out, "work")
        os.makedirs(self.work, exist_ok=True)
        os.environ["OPRUNWAY_CATLASS_ARCH"] = "3510"

    def tearDown(self):
        os.environ.pop("OPRUNWAY_CATLASS_ARCH", None)
        shutil.rmtree(self.out, ignore_errors=True)

    def _dump(self, obj, name):
        json.dump(obj, open(os.path.join(self.out, name), "w", encoding="utf-8"),
                  ensure_ascii=False)

    def test_full_mock_pipeline_gates_pass(self):
        try:
            import validator
            import perf_compare
            import validate_acceptance_state as gate
        except Exception as e:  # 下游模块在并行改动中不可用 → 跳过（非本 adapter 失败）
            self.skipTest(f"下游模块暂不可用（并行改动）：{e}")
        with open(os.path.join(_HERE, "specs", "catlass_basic_matmul.spec.json"), encoding="utf-8") as f:
            spec = json.load(f)
        cs = A.build_matmul_caseset(spec, self.work)
        self._dump(cs, "caseset.json")
        ev = A.run_catlass_mock(cs, self.work)
        self._dump(ev, "evidence.json")
        verdict = validator.validate(spec, cs, ev)
        self._dump(verdict, "verdict.json")
        self.assertEqual(verdict["overall"]["verdict"], "pass")  # 完美 mock → 精度 pass
        # 外部 GPU 基线（性能分母走 gpu_external）
        perf_ids = [c["id"] for c in cs["cases"] if "性能" in c["dims"]]
        bl = A.load_external_baseline(os.path.join(_TD, "catlass_gpu_baseline.json"), perf_ids)
        self._dump(bl, "baseline.json")
        report = perf_compare.perf_compare(spec, cs, ev, bl)
        self._dump(report, "perf_report.json")
        # 三级门
        for st in ("task1", "task2", "task3"):
            errs = []
            gate._GATES[st](self.out, errs)
            self.assertEqual(errs, [], f"{st} 门有 error: {errs}")


class StaticBuildGateTest(unittest.TestCase):
    """静态构建门（Mac 可跑）：runner extern C 符号 / CMake 单行 / arch 注入 ∈ 白名单。"""

    def setUp(self):
        import importlib.util
        gate_path = os.path.join(_HERE, "catlass", "verify_catlass_build.py")
        spec = importlib.util.spec_from_file_location("verify_catlass_build", gate_path)
        self.gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.gate)

    def test_template_sources_pass_both_arches(self):
        self.assertEqual(self.gate.verify("3510"), [])
        self.assertEqual(self.gate.verify("2201"), [])

    def test_illegal_arch_fails(self):
        errs = self.gate.verify("9999")
        self.assertTrue(errs)

    def test_missing_add_subdirectory_flagged(self):
        d = tempfile.mkdtemp()
        try:
            # 合法 catlass 根特征（scripts/build.sh + examples/）——否则被 _is_catlass_root
            # 前置守卫（对抗门加固：堵伪 catlass 根蒙混）拦在前面，走不到 add_subdirectory 检测。
            os.makedirs(os.path.join(d, "scripts"))
            with open(os.path.join(d, "scripts", "build.sh"), "w") as f:
                f.write("#!/bin/bash\n# stub\n")
            os.makedirs(os.path.join(d, "examples"))
            with open(os.path.join(d, "examples", "CMakeLists.txt"), "w") as f:
                f.write("foreach(EXAMPLE ${EXAMPLE_LIST})\n  add_subdirectory(${EXAMPLE})\nendforeach()\n")
            errs = self.gate.verify("3510", catlass_dir=d)   # 合法根但未 stage → 应报缺 add_subdirectory
            self.assertTrue(any("add_subdirectory" in e for e in errs))
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
