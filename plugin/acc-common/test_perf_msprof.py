"""perf_msprof 离线单测 —— 解析 msprof 输出 / 中位数聚合 / 行为分类 / speedup / scope 校验 / 精度先筛。

**全部无 CANN / torch / msprof 依赖**：用真实 CSV 列名造小 fixture（task_time_*.csv / msprof_tx_*.csv /
api_statistic_*.csv 三件套，落进 `<prof>/mindstudio_profiler_output/`），逐条压判据。
真机采集（`measure_side`/`collect`）只测其 **gate**：未设 `OPRUNWAY_ACLNN_REAL=1` 即 fail-closed。
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from aclnn_runtime import perf_msprof as PM  # noqa: E402


def _write_prof(root, *, task_time=None, mstx=None, api_stat=None):
    """造一个 msprof PROF 目录（只落用到的三张表）。返回 prof_dir。"""
    out = os.path.join(root, "mindstudio_profiler_output")
    os.makedirs(out, exist_ok=True)

    def dump(name, header, rows):
        with open(os.path.join(out, name), "w", encoding="utf-8") as f:
            f.write(",".join(header) + "\n")
            for r in rows:
                f.write(",".join(str(r.get(h, "")) for h in header) + "\n")

    if task_time is not None:
        dump("task_time_1_1.csv",
             ["kernel_name", "kernel_type", "task_time(us)", "task_start(us)", "task_stop(us)"],
             task_time)
    if mstx is not None:
        dump("msprof_tx_1_1.csv",
             ["message", "Device_id", "Device Start_time(us)", "Device End_time(us)"], mstx)
    if api_stat is not None:
        dump("api_statistic_1_1.csv", ["API Name", "Count"], api_stat)
    return root


def _kernel_rows(name, ktype, durations, *, start=100.0, step=10.0):
    """造一串同名 kernel 的 task_time 行（起止落在 [start, start+len*step] 内）。"""
    rows = []
    t = start
    for d in durations:
        rows.append({"kernel_name": name, "kernel_type": ktype, "task_time(us)": d,
                     "task_start(us)": t, "task_stop(us)": t + d})
        t += step
    return rows


class TestMeasurementWindow(unittest.TestCase):
    """MSTX 测量窗：缺证据一律 fail-closed，绝不靠 task 数猜。"""

    def test_window_parsed(self):
        with tempfile.TemporaryDirectory() as d:
            _write_prof(d, mstx=[{"message": "R", "Device_id": "0",
                                  "Device Start_time(us)": 10, "Device End_time(us)": 90}])
            win, err = PM.parse_measurement_window(d, "R")
            self.assertIsNone(err)
            self.assertEqual((win["start_us"], win["end_us"]), (10.0, 90.0))

    def test_no_mstx_csv_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            _write_prof(d, task_time=_kernel_rows("k", "AI_CORE", [1, 1]))
            win, err = PM.parse_measurement_window(d, "R")
            self.assertIsNone(win)
            self.assertEqual(err, PM.ERR_NO_MSTX_CSV)

    def test_range_not_found(self):
        with tempfile.TemporaryDirectory() as d:
            _write_prof(d, mstx=[{"message": "other", "Device_id": "0",
                                  "Device Start_time(us)": 1, "Device End_time(us)": 2}])
            self.assertEqual(PM.parse_measurement_window(d, "R")[1], PM.ERR_MSTX_RANGE_NOT_FOUND)

    def test_ambiguous_range_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            _write_prof(d, mstx=[{"message": "R", "Device_id": "0",
                                  "Device Start_time(us)": 1, "Device End_time(us)": 2},
                                 {"message": "R", "Device_id": "1",
                                  "Device Start_time(us)": 5, "Device End_time(us)": 9}])
            self.assertEqual(PM.parse_measurement_window(d, "R")[1], PM.ERR_MSTX_RANGE_AMBIGUOUS)

    def test_kernel_measurement_requires_window(self):
        with tempfile.TemporaryDirectory() as d:
            _write_prof(d, task_time=_kernel_rows("k", "AI_CORE", [1] * 4))
            m = PM.parse_kernel_measurement(d, repeat=4, measurement_window=None)
            self.assertEqual(m["error"], PM.ERR_WINDOW_REQUIRED)
            self.assertIsNone(m["us"])


class TestKernelAggregation(unittest.TestCase):
    """中位数聚合 · 一次性 setup 剔除 · 多 kernel 求和 · memcpy 绝不计入。"""

    WIN = {"start_us": 0.0, "end_us": 10_000.0}

    def _prof(self, d, rows):
        _write_prof(d, task_time=rows,
                    mstx=[{"message": "R", "Device_id": "0",
                           "Device Start_time(us)": 0, "Device End_time(us)": 10_000}])
        return d

    def test_median_times_launches(self):
        """每 kernel 取 repeat 次中位数 × 每次调用启动数。4 次调用 × 每次 2 launch = 8 行。"""
        rows = _kernel_rows("k", "AI_CORE", [10, 12, 10, 12, 10, 12, 10, 100])
        with tempfile.TemporaryDirectory() as d:
            m = PM.parse_kernel_measurement(self._prof(d, rows), repeat=4,
                                            measurement_window=self.WIN)
            self.assertIsNone(m["error"])
            self.assertEqual(m["execution_path"], PM.PATH_DEVICE_KERNEL)
            self.assertEqual(m["breakdown"][0]["launches_per_invocation"], 2)
            self.assertEqual(m["breakdown"][0]["median_launch_us"], 11.0)   # median(10,12,...,100)
            self.assertEqual(m["us"], 22.0)

    def test_setup_kernel_dropped(self):
        """启动数 < repeat 的 kernel = 一次性 setup，按「每次调用都重复」规则剔除。"""
        rows = (_kernel_rows("main", "AI_VECTOR_CORE", [5] * 4, start=0)
                + _kernel_rows("setup", "AI_CORE", [999], start=500))
        with tempfile.TemporaryDirectory() as d:
            m = PM.parse_kernel_measurement(self._prof(d, rows), repeat=4,
                                            measurement_window=self.WIN)
            self.assertEqual([b["kernel_name"] for b in m["breakdown"]], ["main"])
            self.assertEqual(m["us"], 5.0)

    def test_multi_kernel_sum(self):
        rows = (_kernel_rows("a", "AI_CORE", [3] * 4, start=0)
                + _kernel_rows("b", "MIX_AIC", [7] * 4, start=500))
        with tempfile.TemporaryDirectory() as d:
            m = PM.parse_kernel_measurement(self._prof(d, rows), repeat=4,
                                            measurement_window=self.WIN)
            self.assertEqual(m["us"], 10.0)
            self.assertEqual(m["kernel_name"], "multiple_kernels")

    def test_memcpy_never_added_to_compute(self):
        """MEMCPY_ASYNC 一律不计入——有计算 kernel 时它必须完全不进和。"""
        rows = (_kernel_rows("a", "AI_CORE", [3] * 4, start=0)
                + _kernel_rows("cpy", PM.DEVICE_MEMCPY_TYPE, [50] * 4, start=500))
        with tempfile.TemporaryDirectory() as d:
            m = PM.parse_kernel_measurement(self._prof(d, rows), repeat=4,
                                            measurement_window=self.WIN)
            self.assertEqual(m["us"], 3.0)
            self.assertEqual([b["kernel_name"] for b in m["breakdown"]], ["a"])

    def test_memcpy_only_is_not_timed(self):
        """纯 device-copy → 单独记 device_memcpy_only，**不产 us**（不冒充计算耗时）。"""
        rows = _kernel_rows("cpy", PM.DEVICE_MEMCPY_TYPE, [4] * 4)
        with tempfile.TemporaryDirectory() as d:
            m = PM.parse_kernel_measurement(self._prof(d, rows), repeat=4,
                                            measurement_window=self.WIN)
            self.assertIsNone(m["us"])
            self.assertEqual(m["execution_path"], PM.PATH_DEVICE_MEMCPY_ONLY)
            self.assertEqual(m["device_memcpy_only_us"], 4.0)

    def test_rows_outside_window_excluded(self):
        """窗外的 task 行一律不算——解析严格限定在 MSTX range 内。"""
        rows = _kernel_rows("k", "AI_CORE", [5] * 4, start=0)
        rows += _kernel_rows("k", "AI_CORE", [500] * 4, start=90_000)
        with tempfile.TemporaryDirectory() as d:
            _write_prof(d, task_time=rows,
                        mstx=[{"message": "R", "Device_id": "0",
                               "Device Start_time(us)": 0, "Device End_time(us)": 1000}])
            win, _ = PM.parse_measurement_window(d, "R")
            m = PM.parse_kernel_measurement(d, repeat=4, measurement_window=win)
            self.assertEqual(m["us"], 5.0)

    def test_no_device_task(self):
        with tempfile.TemporaryDirectory() as d:
            m = PM.parse_kernel_measurement(self._prof(d, []), repeat=4,
                                            measurement_window=self.WIN)
            self.assertEqual(m["error"], PM.ERR_NO_DEVICE_TASK)

    def test_inconsistent_sequence_rejected(self):
        """启动数不是 repeat 的整数倍 → 序列不自洽，报错（不取整、不猜）。"""
        rows = _kernel_rows("k", "AI_CORE", [1] * 5)
        with tempfile.TemporaryDirectory() as d:
            m = PM.parse_kernel_measurement(self._prof(d, rows), repeat=2,
                                            measurement_window=self.WIN)
            # 5 行、repeat=2 → 丢 1 个零头后 4 行 = 每次 2 launch，合法
            self.assertEqual(m["us"], 2.0)
            rows7 = _kernel_rows("k", "AI_CORE", [1] * 7)
            m2 = PM.parse_kernel_measurement(self._prof(d, rows7), repeat=3,
                                             measurement_window=self.WIN)
            self.assertEqual(m2["us"], 2.0)   # 7 → 丢 1 → 6 行 / 3 = 每次 2 launch


class TestBehaviorClassification(unittest.TestCase):
    """五分类：只有 npu 计时，其余只报行为。"""

    OK = {"us": 12.0, "kernel_name": "k", "execution_path": PM.PATH_DEVICE_KERNEL,
          "breakdown": [], "device_memcpy_only_us": None, "error": None}

    def test_npu(self):
        b, _ = PM.classify_behavior(returncode=0, output="", measurement=self.OK)
        self.assertEqual(b, PM.BEHAVIOR_NPU)

    def test_execution_failed_on_nonzero_rc(self):
        b, _ = PM.classify_behavior(returncode=3, output="", measurement=self.OK)
        self.assertEqual(b, PM.BEHAVIOR_FAILED)

    def test_cpu_fallback_beats_parsed_kernel(self):
        """回退告警**先于**任何已解析的 kernel——回退时解析出的耗时是垃圾数。"""
        for marker in PM.CPU_FALLBACK_MARKERS:
            b, detail = PM.classify_behavior(returncode=0, output=f"W: {marker} !",
                                             measurement=self.OK)
            self.assertEqual(b, PM.BEHAVIOR_CPU_FALLBACK)
            self.assertTrue(detail["cpu_fallback_marker"])

    def test_no_device_task_maps_to_no_kernel(self):
        m = {**self.OK, "us": None, "execution_path": None, "error": PM.ERR_NO_DEVICE_TASK}
        b, _ = PM.classify_behavior(returncode=0, output="", measurement=m)
        self.assertEqual(b, PM.BEHAVIOR_NO_KERNEL)

    def test_collection_error_is_execution_failed(self):
        m = {**self.OK, "us": None, "error": PM.ERR_WINDOW_REQUIRED}
        b, _ = PM.classify_behavior(returncode=0, output="", measurement=m)
        self.assertEqual(b, PM.BEHAVIOR_FAILED)

    def test_memcpy_only_not_timed(self):
        m = {**self.OK, "us": None, "execution_path": PM.PATH_DEVICE_MEMCPY_ONLY,
             "device_memcpy_only_us": 4.0}
        b, detail = PM.classify_behavior(returncode=0, output="", measurement=m)
        self.assertEqual(b, PM.BEHAVIOR_NO_KERNEL)
        self.assertEqual(detail["device_memcpy_only_us"], 4.0)

    def test_hybrid_only_when_requested(self):
        ht = {"detected": True}
        b_base, _ = PM.classify_behavior(returncode=0, output="", measurement=self.OK,
                                         host_transfer=ht, detect_hybrid=True)
        self.assertEqual(b_base, PM.BEHAVIOR_HYBRID)
        # custom 侧（detect_hybrid=False）：ctypes runner 的 H2D/D2H 是 form 固有开销，不判 hybrid
        b_custom, _ = PM.classify_behavior(returncode=0, output="", measurement=self.OK,
                                           host_transfer=ht, detect_hybrid=False)
        self.assertEqual(b_custom, PM.BEHAVIOR_NPU)

    def test_only_npu_is_timed(self):
        self.assertEqual(PM.TIMED_BEHAVIORS, frozenset({PM.BEHAVIOR_NPU}))
        self.assertEqual(len(PM.BEHAVIORS), 5)


class TestHostTransferEvidence(unittest.TestCase):
    def test_one_time_materialization_allowance(self):
        case = {"id": "c0", "inputs": [{"name": "self"}]}
        with tempfile.TemporaryDirectory() as d:
            _write_prof(d, api_stat=[{"API Name": "aclrtMemcpy", "Count": 2}])
            ev = PM.parse_host_transfer_evidence(d, case, repeat=20, materializations=2)
            self.assertEqual(ev["one_time_allowance"], 2)
            self.assertFalse(ev["detected"])          # 2 次 = 恰好两轮物化配额，非重复搬运

    def test_repeated_transfer_detected(self):
        case = {"id": "c0", "inputs": [{"name": "self"}]}
        with tempfile.TemporaryDirectory() as d:
            _write_prof(d, api_stat=[{"API Name": "aclrtMemcpy", "Count": 42}])
            ev = PM.parse_host_transfer_evidence(d, case, repeat=20, materializations=2)
            self.assertEqual(ev["repeated_host_transfer_count"], 40)
            self.assertTrue(ev["detected"])


class TestVerdictInputs(unittest.TestCase):
    """speedup / 可比性 / timing_scope 闸。"""

    def test_speedup(self):
        self.assertEqual(PM.speedup(20.0, 10.0), 2.0)
        for bad in (0, -1, None, True, float("nan"), float("inf"), "10"):
            self.assertIsNone(PM.speedup(bad, 10.0), bad)
            self.assertIsNone(PM.speedup(10.0, bad), bad)

    def test_comparability(self):
        self.assertEqual(PM.comparability(PM.PATH_DEVICE_KERNEL, PM.PATH_DEVICE_KERNEL),
                         PM.COMPARABILITY_FAIR)
        self.assertEqual(PM.comparability(PM.PATH_DEVICE_KERNEL, PM.PATH_DEVICE_MEMCPY_ONLY),
                         PM.COMPARABILITY_INDICATIVE)
        self.assertEqual(PM.comparability(None, None), PM.COMPARABILITY_INDICATIVE)

    def test_timing_scope_gate(self):
        self.assertIsNone(PM.check_timing_scope("kernel_only", "kernel_only"))
        self.assertEqual(PM.check_timing_scope("kernel_only", "host_e2e_with_h2d_d2h"),
                         PM.BLOCKED_INCOMPARABLE_TIMING_SCOPE)
        self.assertEqual(PM.check_timing_scope(None, None),      # None==None 也不放行
                         PM.BLOCKED_INCOMPARABLE_TIMING_SCOPE)

    def test_record_with_both_timed(self):
        # §9.7 C 采集配置闸：双边须同配置才算比值（缺配置 = 不可比）→ 显式给同一份指纹
        coll = PM.collection_config(collector=PM.COLLECTOR_TORCH_PROFILER, warmup=5, repeat=20)
        rec = PM.build_perf_record(
            "c0",
            {"behavior": PM.BEHAVIOR_NPU, "us": 10.0, "scope": "kernel_only",
             "execution_path": PM.PATH_DEVICE_KERNEL, "collection": dict(coll)},
            {"behavior": PM.BEHAVIOR_NPU, "us": 25.0, "scope": "kernel_only",
             "execution_path": PM.PATH_DEVICE_KERNEL, "collection": dict(coll)})
        self.assertEqual(rec["speedup"], 2.5)
        self.assertEqual(rec["comparability"], PM.COMPARABILITY_FAIR)
        self.assertIsNone(rec["timing_scope_status"])

    def test_record_blocks_on_scope_mismatch(self):
        rec = PM.build_perf_record(
            "c0",
            {"behavior": PM.BEHAVIOR_NPU, "us": 10.0, "scope": "kernel_only",
             "execution_path": PM.PATH_DEVICE_KERNEL},
            {"behavior": PM.BEHAVIOR_NPU, "us": 25.0, "scope": "device_e2e_no_h2d_d2h",
             "execution_path": PM.PATH_DEVICE_KERNEL})
        self.assertEqual(rec["timing_scope_status"], PM.BLOCKED_INCOMPARABLE_TIMING_SCOPE)
        self.assertIsNone(rec["speedup"])

    def test_record_no_ratio_when_baseline_not_npu(self):
        """基线非 npu 侧 → 只报行为、**不硬算比值**。"""
        for behavior in (PM.BEHAVIOR_CPU_FALLBACK, PM.BEHAVIOR_HYBRID,
                         PM.BEHAVIOR_FAILED, PM.BEHAVIOR_NO_KERNEL):
            rec = PM.build_perf_record(
                "c0",
                {"behavior": PM.BEHAVIOR_NPU, "us": 10.0, "scope": "kernel_only",
                 "execution_path": PM.PATH_DEVICE_KERNEL},
                {"behavior": behavior, "us": None, "scope": None})
            self.assertIsNone(rec["speedup"], behavior)
            self.assertFalse(rec["baseline_timed"])


class TestAccuracyPrefilter(unittest.TestCase):
    """精度先筛：只对已过精度的 case 测性能，其余记 skipped_accuracy_failed。"""

    def _ev(self, cid, mismatch):
        return {"case_id": cid, "precision": {"outputs": [
            {"policy": {"kind": "torch_allclose", "rtol": 1e-3, "atol": 1e-3},
             "metrics": {"mismatch": mismatch, "numel": 8}}]}}

    def test_pass_ids(self):
        ids = PM.accuracy_pass_ids([self._ev("ok", 0), self._ev("bad", 3)])
        self.assertEqual(ids, {"ok"})

    def test_multi_output_and_fold(self):
        ev = [{"case_id": "c0", "precision": {"outputs": [
            {"policy": {"kind": "torch_allclose"}, "metrics": {"mismatch": 0, "numel": 4}},
            {"policy": {"kind": "index_value_consistency"},
             "metrics": {"mismatch": 1, "numel": 4}}]}}]
        self.assertEqual(PM.accuracy_pass_ids(ev), set())   # 任一输出 fail → 整 case 不测性能

    def test_select_perf_cases_filters(self):
        caseset = {"cases": [{"id": "p_ok", "dims": ["性能"]},
                             {"id": "p_bad", "dims": ["性能"]},
                             {"id": "acc_only", "dims": ["精度"]}]}
        selected, skipped = PM.select_perf_cases(caseset, {"p_ok"})
        self.assertEqual(selected, ["p_ok"])
        self.assertEqual(skipped, [{"case_id": "p_bad", "reason": PM.SKIPPED_ACCURACY_FAILED}])

    def test_select_without_filter(self):
        caseset = {"cases": [{"id": "a", "dims": ["性能"]}, {"id": "b", "dims": ["精度"]}]}
        selected, skipped = PM.select_perf_cases(caseset, None)
        self.assertEqual((selected, skipped), (["a"], []))


class TestDocumentBuilders(unittest.TestCase):
    """产物组装：只有 npu 基线进 per_case；未计时一律 us=None。"""

    def _rec(self, cid, custom_behavior, baseline_behavior, custom_us=10.0, baseline_us=20.0):
        return PM.build_perf_record(
            cid,
            {"behavior": custom_behavior, "us": custom_us, "scope": "kernel_only",
             "execution_path": PM.PATH_DEVICE_KERNEL},
            {"behavior": baseline_behavior, "us": baseline_us,
             "scope": "kernel_only" if baseline_behavior == PM.BEHAVIOR_NPU else None,
             "execution_path": PM.PATH_DEVICE_KERNEL})

    def test_baseline_document_excludes_non_npu(self):
        recs = [self._rec("a", PM.BEHAVIOR_NPU, PM.BEHAVIOR_NPU),
                self._rec("b", PM.BEHAVIOR_NPU, PM.BEHAVIOR_CPU_FALLBACK)]
        doc = PM.build_baseline_document(recs, op="Median",
                                         skipped=[{"case_id": "c",
                                                   "reason": PM.SKIPPED_ACCURACY_FAILED}])
        self.assertEqual([r["case_id"] for r in doc["per_case"]], ["a"])
        self.assertEqual(doc["scope"], "kernel_only")
        excluded = {e["case_id"] for e in doc["excluded"]}
        self.assertEqual(excluded, {"b", "c"})

    def test_custom_map_us_none_when_untimed(self):
        recs = [self._rec("a", PM.BEHAVIOR_NPU, PM.BEHAVIOR_NPU),
                self._rec("b", PM.BEHAVIOR_NO_KERNEL, PM.BEHAVIOR_NPU, custom_us=None)]
        m = PM.build_custom_perf_map(recs, skipped=[{"case_id": "c",
                                                     "reason": PM.SKIPPED_ACCURACY_FAILED}])
        self.assertEqual(m["a"]["us"], 10.0)
        self.assertIsNone(m["b"]["us"])
        self.assertIsNone(m["c"]["us"])
        self.assertEqual(m["c"]["note"], PM.SKIPPED_ACCURACY_FAILED)
        for entry in m.values():
            self.assertEqual(entry["scope"], "kernel_only")


class TestTorchBaselinePlan(unittest.TestCase):
    """baseline 侧 torch 调用：spec 声明的 slot-name 映射驱动，变体自动跟随 case。"""

    MAP = {"api": "torch.median", "positional": ["self"],
           "keyword": {"dim": "dim", "keepdim": "keepdim"}}

    def test_dim_variant(self):
        call = {"symbol": "MedianDim", "slots": [
            {"role": "in", "name": "self", "input_idx": 0},
            {"role": "attr", "name": "dim", "ctype": "int64", "value": 1},
            {"role": "attr", "name": "keepdim", "ctype": "bool", "value": False},
            {"role": "out", "name": "values", "output_idx": 0},
            {"role": "out", "name": "indices", "output_idx": 1}]}
        plan = PM.resolve_torch_baseline_plan(self.MAP, call)
        self.assertEqual(plan["api"], "torch.median")
        self.assertEqual([s["name"] for s in plan["positional"]], ["self"])
        self.assertEqual(sorted(plan["keyword"]), ["dim", "keepdim"])

    def test_global_variant_drops_absent_attrs(self):
        """全局变体没有 dim/keepdim slot → 对应 kwarg 自然缺席（不塞默认、不写算子分支）。"""
        call = {"symbol": "Median", "slots": [
            {"role": "in", "name": "self", "input_idx": 0},
            {"role": "out", "name": "values", "output_idx": 0},
            {"role": "out_null", "name": "indices"}]}
        plan = PM.resolve_torch_baseline_plan(self.MAP, call)
        self.assertEqual(plan["keyword"], {})

    def test_missing_map_is_fail_closed(self):
        with self.assertRaises(PM.PerfCollectError):
            PM.resolve_torch_baseline_plan(None, {"slots": []})

    def test_non_torch_api_rejected(self):
        with self.assertRaises(PM.PerfCollectError):
            PM.resolve_torch_baseline_plan({"api": "numpy.median"}, {"slots": []})

    def test_missing_positional_slot_is_fail_closed(self):
        call = {"slots": [{"role": "attr", "name": "dim", "value": 0}]}
        with self.assertRaises(PM.PerfCollectError):
            PM.resolve_torch_baseline_plan(self.MAP, call)


class TestRealGate(unittest.TestCase):
    """真机采集 gate：未显式开 OPRUNWAY_ACLNN_REAL=1 一律 fail-closed。"""

    def test_collect_gated(self):
        old = os.environ.pop("OPRUNWAY_ACLNN_REAL", None)
        try:
            with self.assertRaises(PM.PerfCollectError):
                PM.collect("/nonexistent/caseset.json", "/tmp", {"cases": []}, "/tmp/out.json")
            with self.assertRaises(PM.PerfCollectError):
                PM.measure_side(side="custom", case={"id": "c0"}, caseset_path="x",
                                work_dir="y", cfg_extra={}, warmup=1, repeat=1, device=0,
                                scratch_dir="/tmp", detect_hybrid=False)
        finally:
            if old is not None:
                os.environ["OPRUNWAY_ACLNN_REAL"] = old


class TestBaselineDocRoundTrip(unittest.TestCase):
    """`build_baseline_document` → `repo_adapter.parse_torch_npu_baseline` 端到端契约对齐。"""

    def test_round_trip(self):
        import repo_adapter as RA
        recs = [PM.build_perf_record(
            "c0",
            {"behavior": PM.BEHAVIOR_NPU, "us": 10.0, "scope": "kernel_only",
             "execution_path": PM.PATH_DEVICE_KERNEL},
            {"behavior": PM.BEHAVIOR_NPU, "us": 20.0, "scope": "kernel_only",
             "execution_path": PM.PATH_DEVICE_KERNEL})]
        doc = PM.build_baseline_document(recs, op="Median")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "_torch_npu_baseline.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump(doc, f)
            bl = RA.parse_torch_npu_baseline(p)
        self.assertEqual(bl["scope"], "kernel_only")
        self.assertEqual(bl["source"], "torch_npu")
        self.assertEqual(bl["per_case"], [{"case_id": "c0", "us": 20.0,
                                           "env": "torch_npu profiler(mstx)",
                                           "execution_path": PM.PATH_DEVICE_KERNEL}])

    def test_empty_baseline_is_legal_and_blocks_downstream(self):
        """一条有效基线都没采到 = 合法结果 → per_case 空 → perf_compare 逐 case blocked（非达标）。"""
        import repo_adapter as RA
        import perf_compare
        doc = PM.build_baseline_document(
            [PM.build_perf_record("c0",
                                  {"behavior": PM.BEHAVIOR_NPU, "us": 10.0,
                                   "scope": "kernel_only",
                                   "execution_path": PM.PATH_DEVICE_KERNEL},
                                  {"behavior": PM.BEHAVIOR_CPU_FALLBACK, "us": None,
                                   "scope": None})], op="Median")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "_torch_npu_baseline.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump(doc, f)
            bl = RA.parse_torch_npu_baseline(p)
        self.assertEqual(bl["per_case"], [])
        spec = {"op": "Median", "perf": {"baseline": "torch_npu", "target_ratio": 1.0}}
        caseset = {"cases": [{"id": "c0", "dims": ["性能"],
                              "inputs": [{"shape": [128, 128]}]}]}
        evidence = {"evidence": [{"case_id": "c0",
                                  "perf": {"scope": "kernel_only", "us": 10.0}}]}
        report = perf_compare.perf_compare(spec, caseset, evidence, bl)
        self.assertEqual(report["summary"]["status"], "blocked")
        self.assertEqual(report["summary"]["达标"], 0)


class TestParseBaselineFailClosed(unittest.TestCase):
    def _parse(self, doc):
        import repo_adapter as RA
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "b.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump(doc, f)
            return RA.parse_torch_npu_baseline(p)

    def test_wrong_scope_rejected(self):
        with self.assertRaises(ValueError):
            self._parse({"scope": "host_e2e_with_h2d_d2h", "per_case": []})

    def test_duplicate_case_id_rejected(self):
        with self.assertRaises(ValueError):
            self._parse({"scope": "kernel_only",
                         "per_case": [{"case_id": "a", "us": 1.0},
                                      {"case_id": "a", "us": 2.0}]})

    def test_bad_us_dropped_with_note(self):
        bl = self._parse({"scope": "kernel_only",
                          "per_case": [{"case_id": "a", "us": 0},
                                       {"case_id": "b", "us": -3},
                                       {"case_id": "c", "us": 5.0}]})
        self.assertEqual([r["case_id"] for r in bl["per_case"]], ["c"])
        self.assertEqual(len(bl["notes"]), 2)


class TestWiring(unittest.TestCase):
    """接线：spec.perf → _perf_plan.json → adapter gate；evidence.perf 回填口径。"""

    def test_emit_perf_plan_only_for_registered_source(self):
        import run_workflow as RW
        with tempfile.TemporaryDirectory() as work:
            self.assertIsNone(RW._emit_perf_plan({"op": "X", "perf": {"baseline": "tbe"}}, work))
            self.assertFalse(os.path.exists(os.path.join(work, "_perf_plan.json")))
            self.assertIsNone(RW._emit_perf_plan({"op": "X"}, work))

    def test_emit_perf_plan_carries_collection_fields_only(self):
        """计划只回答「采什么、怎么采」；**阈值（判据）绝不进采集端**。"""
        import run_workflow as RW
        spec = {"op": "Median", "perf": {"baseline": "torch_npu", "target_ratio": 1.0,
                                         "warmup": 5, "repeat": 20,
                                         "torch_baseline": {"api": "torch.median"}}}
        with tempfile.TemporaryDirectory() as work:
            path = RW._emit_perf_plan(spec, work)
            with open(path, encoding="utf-8") as f:
                plan = json.load(f)
        self.assertEqual(plan["baseline"], "torch_npu")
        self.assertEqual((plan["warmup"], plan["repeat"]), (5, 20))
        self.assertEqual(plan["torch_baseline"]["api"], "torch.median")
        self.assertNotIn("target_ratio", plan)

    def test_perf_enabled_gate(self):
        import aclnn_adapter as A
        old_real = os.environ.pop("OPRUNWAY_ACLNN_REAL", None)
        old_perf = os.environ.pop("OPRUNWAY_ACLNN_PERF", None)
        try:
            self.assertFalse(A._perf_enabled({"baseline": "torch_npu"}))   # 真机 gate 未开
            os.environ["OPRUNWAY_ACLNN_REAL"] = "1"
            self.assertFalse(A._perf_enabled(None))                        # 无计划
            self.assertTrue(A._perf_enabled({"baseline": "torch_npu"}))
            os.environ["OPRUNWAY_ACLNN_PERF"] = "0"
            self.assertFalse(A._perf_enabled({"baseline": "torch_npu"}))   # 显式关
        finally:
            os.environ.pop("OPRUNWAY_ACLNN_PERF", None)
            os.environ.pop("OPRUNWAY_ACLNN_REAL", None)
            if old_real is not None:
                os.environ["OPRUNWAY_ACLNN_REAL"] = old_real
            if old_perf is not None:
                os.environ["OPRUNWAY_ACLNN_PERF"] = old_perf

    def test_perf_entry_defaults_to_none_us(self):
        """没采到一律 us=None + note（绝不填 0/估计值），scope 恒 kernel_only。"""
        import repo_adapter as RA
        entry = RA._perf_entry("c0", None)
        self.assertIsNone(entry["us"])
        self.assertEqual(entry["scope"], "kernel_only")
        self.assertIn("note", entry)
        filled = RA._perf_entry("c0", {"c0": {"scope": "kernel_only", "us": 7.5,
                                              "behavior": "npu",
                                              "execution_path": "device_kernel"}})
        self.assertEqual(filled["us"], 7.5)
        self.assertEqual(filled["behavior"], "npu")

    def test_runtime_files_include_perf_module(self):
        """perf_msprof 必须随 aclnn_runtime 一起部署，否则容器里 `python -m` 找不到它。"""
        import aclnn_adapter as A
        self.assertIn("perf_msprof.py", A._RUNTIME_FILES)

    def test_perf_script_env_and_gate(self):
        """采集脚本：与 exec 同一套运行时 env + 容器侧显式带上真机 gate + 独立哨兵（可解耦归因）。"""
        import aclnn_adapter as A
        cfg = {"setenv": "/opt/set_env.sh", "vendor_dir": "/home/u/vend",
               "vendor_name": "customize", "rroot": "/home/u/work", "device": "0",
               "soc": "ascend910_93", "snake_op": "median", "host": None}
        paths = {"rcases": "/home/u/work/aclnn_cases", "rout": "/home/u/work/aclnn_out"}
        s = A._perf_script(cfg, paths)
        self.assertIn('VC="$VROOT/vendors/customize_nn"', s)
        # `:-` 是 shell 修复带来的 set -u 安全展开（变量未定义时不炸）
        self.assertIn('export ASCEND_CUSTOM_OPP_PATH="$VC:${ASCEND_CUSTOM_OPP_PATH:-}"', s)
        self.assertIn("export OPRUNWAY_ACLNN_REAL=1", s)
        self.assertIn("python -m aclnn_runtime.perf_msprof", s)
        self.assertIn("OPRUNWAY_ACLNN_PERF_DONE", s)
        self.assertIn("OPRUNWAY_ACLNN_PERF_FAIL", s)


class TestSpecTargetRatio(unittest.TestCase):
    """spec 修正：median 的 target_ratio 依任务书「不劣化」= 1.0（非参考仓默认 0.6）。"""

    def test_median_target_ratio_is_one(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "samples", "specs", "median.spec.json")
        with open(path, encoding="utf-8") as f:
            spec = json.load(f)
        self.assertEqual(spec["perf"]["target_ratio"], 1.0)
        self.assertIn("不劣化", spec["perf"]["_target_ratio_note"])
        # 采集端要的映射也在 spec 里（缺了采集端 fail-closed）
        self.assertEqual(spec["perf"]["torch_baseline"]["api"], "torch.median")

    def test_target_ratio_reaches_perf_compare(self):
        """1.0 真的会把「比基线慢」判成不达标（不是写在注释里的口号）。"""
        import perf_compare
        spec = {"op": "Median", "perf": {"baseline": "torch_npu", "target_ratio": 1.0}}
        caseset = {"cases": [{"id": "c0", "dims": ["性能"], "inputs": [{"shape": [256, 256]}]}]}
        evidence = {"evidence": [{"case_id": "c0", "perf": {"scope": "kernel_only", "us": 20.0}}]}
        baseline = {"source": "torch_npu", "scope": "kernel_only",
                    "per_case": [{"case_id": "c0", "us": 12.0}]}      # 基线更快 → ratio 0.6
        report = perf_compare.perf_compare(spec, caseset, evidence, baseline)
        self.assertEqual(report["per_case"][0]["ratio"], 0.6)
        self.assertFalse(report["per_case"][0]["达标"])                # 0.6 阈下会误判成达标
        self.assertEqual(report["summary"]["status"], "fail")


if __name__ == "__main__":
    unittest.main(verbosity=2)
