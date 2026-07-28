"""perf_msprof 离线单测 —— 解析 msprof 输出 / 中位数聚合 / 行为分类 / speedup / scope 校验 / 精度先筛。

**全部无 CANN / torch / msprof 依赖**：用真实 CSV 列名造小 fixture（task_time_*.csv / msprof_tx_*.csv /
api_statistic_*.csv 三件套，落进 `<prof>/mindstudio_profiler_output/`），逐条压判据。
真机采集（`measure_side`/`collect`）只测其 **gate**：未设 `OPRUNWAY_ACLNN_REAL=1` 即 fail-closed。
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest import mock

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

    def test_retryable_evidence_failure_is_narrow(self):
        missing = {"behavior": "execution_failed",
                   "detail": {"returncode": 0, "parse_error": "mstx_range_not_found"}}
        dut_failed = {"behavior": "execution_failed",
                      "detail": {"returncode": 1, "parse_error": "mstx_range_not_found"}}
        unknown = {"behavior": "execution_failed",
                   "detail": {"returncode": 0, "parse_error": "unknown_task_type_in_window"}}
        self.assertTrue(PM._retryable_evidence_failure(missing))
        self.assertFalse(PM._retryable_evidence_failure(dut_failed))
        self.assertFalse(PM._retryable_evidence_failure(unknown))

    def test_measure_side_retries_in_fresh_attempt_directories(self):
        calls = []
        failed = {"behavior": "execution_failed", "us": None, "scope": None,
                  "detail": {"returncode": 0, "parse_error": "no_mstx_csv"}}
        passed = {"behavior": "npu", "us": 1.5, "scope": "kernel_only",
                  "detail": {"returncode": 0}}

        def fake_once(**kwargs):
            calls.append(str(kwargs["scratch_dir"]))
            return failed.copy() if len(calls) == 1 else passed.copy()

        with mock.patch.object(PM, "_measure_side_once", side_effect=fake_once), \
             mock.patch.dict(os.environ, {"OPRUNWAY_PERF_EVIDENCE_RETRIES": "1"}):
            got = PM.measure_side(
                side="custom", case={"id": "c0"}, caseset_path="cases.json",
                work_dir="work", cfg_extra={}, warmup=5, repeat=20, device=0,
                scratch_dir="/tmp/perf", detect_hybrid=False)
        self.assertEqual(calls, ["/tmp/perf/attempt-1", "/tmp/perf/attempt-2"])
        self.assertEqual(got["behavior"], "npu")
        self.assertEqual(got["detail"]["attempt_count"], 2)
        self.assertEqual(got["detail"]["attempts"][0]["parse_error"], "no_mstx_csv")

    def test_measure_side_does_not_retry_execution_error(self):
        failed = {"behavior": "execution_failed", "us": None, "scope": None,
                  "detail": {"returncode": 9, "parse_error": None}}
        with mock.patch.object(PM, "_measure_side_once", return_value=failed) as once, \
             mock.patch.dict(os.environ, {"OPRUNWAY_PERF_EVIDENCE_RETRIES": "3"}):
            got = PM.measure_side(
                side="custom", case={"id": "c0"}, caseset_path="cases.json",
                work_dir="work", cfg_extra={}, warmup=5, repeat=20, device=0,
                scratch_dir="/tmp/perf", detect_hybrid=False)
        self.assertEqual(once.call_count, 1)
        self.assertEqual(got["detail"]["attempt_count"], 1)

    def test_explicit_csv_route_ignores_numeric_task_type_db(self):
        """live collector 钉 CSV：同目录即使有不可解的数值 taskType db，也必须读字符串 CSV。"""
        with tempfile.TemporaryDirectory() as d:
            _write_db(d, tasks=_numeric_tasks(15, count=3), mstx=[(0, 1_000_000, "R")])
            _write_prof(
                d,
                task_time=(
                    _kernel_rows("csv_kernel", "MIX_AIV", [4, 5, 6], start=100)
                    + _kernel_rows("N/A", "PROFILER_TRACE_EX", [1], start=500)
                ),
                mstx=[{"message": "R", "Device_id": "0",
                       "Device Start_time(us)": 0, "Device End_time(us)": 1000}],
            )
            win, err = PM.parse_measurement_window(d, "R", route=PM.ROUTE_CSV)
            self.assertIsNone(err)
            self.assertEqual(win["route"], PM.ROUTE_CSV)
            m = PM.parse_kernel_measurement(
                d, repeat=3, measurement_window=win, route=PM.ROUTE_CSV)
            self.assertIsNone(m["error"], m)
            self.assertEqual(m["us"], 5.0)
            self.assertEqual(m["observed_task_types"],
                             {"MIX_AIV": 3, "PROFILER_TRACE_EX": 1})

    def test_csv_control_type_is_narrowly_allowlisted(self):
        self.assertEqual(
            PM.classify_task_type("PROFILER_TRACE_EX", PM.ROUTE_CSV),
            PM.KIND_CONTROL)
        self.assertEqual(
            PM.classify_task_type("UNSEEN_PROFILER_CONTROL", PM.ROUTE_CSV),
            PM.KIND_UNKNOWN)


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


def _write_db(root, *, tasks, mstx=None, string_ids=None, enum_table=None,
              string_ids_fk=False, name="ascend_pytorch_profiler_1.db"):
    """造一份 profiler db（`TASK ⋈ COMPUTE_TASK_INFO` + `MSTX_EVENTS` + 可选字典表）→ 返回目录。

    `tasks` = `[(globalTaskId, startNs, endNs, taskType, kernelName)]`，`taskType` **可为数值 id**
    （真机实测就是数值：CANN 9.0.1 + torch_npu 2.10）。

    `string_ids_fk=True` → 在 schema 里**声明** `TASK.taskType → STRING_IDS(id)` 外键，
    这是「taskType 的确以 STRING_IDS 为字典」的**唯一**可查证据（审计高危 #2）。
    """
    path = os.path.join(root, name)
    conn = sqlite3.connect(path)
    if string_ids is not None:
        conn.execute("CREATE TABLE STRING_IDS (id INTEGER PRIMARY KEY, value TEXT)")
        for key, val in string_ids.items():
            conn.execute("INSERT INTO STRING_IDS VALUES (?,?)", (key, val))
    fk = ", FOREIGN KEY(taskType) REFERENCES STRING_IDS(id)" if string_ids_fk else ""
    conn.execute("CREATE TABLE TASK (globalTaskId INTEGER, startNs INTEGER, endNs INTEGER, "
                 f"taskType, deviceId INTEGER{fk})")
    conn.execute("CREATE TABLE COMPUTE_TASK_INFO (globalTaskId INTEGER, name)")
    conn.execute("CREATE TABLE MSTX_EVENTS (startNs INTEGER, endNs INTEGER, message TEXT)")
    for gid, start, end, ktype, kname in tasks:
        conn.execute("INSERT INTO TASK VALUES (?,?,?,?,?)", (gid, start, end, ktype, 0))
        conn.execute("INSERT INTO COMPUTE_TASK_INFO VALUES (?,?)", (gid, kname))
    for start, end, msg in (mstx or []):
        conn.execute("INSERT INTO MSTX_EVENTS VALUES (?,?,?)", (start, end, msg))
    if enum_table is not None:
        conn.execute("CREATE TABLE ENUM_TASK_TYPE (id INTEGER, name TEXT)")
        for key, val in enum_table.items():
            conn.execute("INSERT INTO ENUM_TASK_TYPE VALUES (?,?)", (key, val))
    conn.commit()
    conn.close()
    return root


def _good_provenance(**over):
    """一份**结构化且逐字段填实**的 override provenance（审计高危 #3 的合格样本）。"""
    doc = {"db": "PROF_000_20260724/ascend_pytorch_profiler_1.db (sha256 ab12cd34)",
           "cann_version": "9.0.1",
           "torch_npu_version": "2.10.0",
           "collect_command": "msprof --task-time=on --ascendcl=on --ai-core=off ./run.sh",
           "collected_at": "2026-07-24"}
    doc.update(over)
    return doc


def _numeric_tasks(type_id, count=4, *, name="aclnnMedian_Median_Median", dur_ns=5_000):
    """同名 kernel 的 `count` 行 TASK（taskType 用**数值 id**），时间戳落在 [0, 1e6) ns 内。"""
    return [(i, i * 10_000, i * 10_000 + dur_ns, type_id, name) for i in range(count)]


class TestNumericTaskType(unittest.TestCase):
    """bug#7 · db 路线的 `TASK.taskType` 是**数值枚举 id** 时也得能归类；解不出仍 fail-closed。

    实测（2026-07-24 a3 容器，CANN 9.0.1 + torch_npu 2.10）：custom 侧 15/17/19/20/24、
    baseline 侧 10~33 —— 按字符串比 `KERNEL_*` 一个都不中，双边 46/46 `unknown_task_type_in_window`。
    """

    MSTX = [(0, 1_000_000, "R")]

    def _measure(self, d, repeat=4):
        win, err = PM.parse_measurement_window(d, "R")
        self.assertIsNone(err, f"MSTX 窗应解得出，得 {err}")
        self.assertEqual(win["route"], PM.ROUTE_DB)
        return PM.parse_kernel_measurement(d, repeat=repeat, measurement_window=win)

    def test_string_task_type_still_works(self):
        """向后兼容：taskType 本来就是字符串时，行为一字不变。"""
        with tempfile.TemporaryDirectory() as d:
            _write_db(d, tasks=_numeric_tasks("KERNEL_AIVEC"), mstx=self.MSTX)
            m = self._measure(d)
            self.assertIsNone(m["error"])
            self.assertEqual(m["us"], 5.0)
            self.assertEqual(m["execution_path"], PM.PATH_DEVICE_KERNEL)

    def test_numeric_id_resolved_via_enum_table(self):
        """db 自带的专用枚举表能把 id 解回名字 → 正常计入（性能通路恢复可用）。"""
        with tempfile.TemporaryDirectory() as d:
            _write_db(d, tasks=_numeric_tasks(15), mstx=self.MSTX,
                      enum_table={15: "KERNEL_AIVEC", 17: "KERNEL_MIX_AIV"})
            m = self._measure(d)
            self.assertIsNone(m["error"], m)
            self.assertEqual(m["us"], 5.0)
            self.assertEqual(list(m["observed_task_types"]), ["KERNEL_AIVEC"])
            self.assertEqual(m["unresolved_task_type_ids"], [])
            self.assertIn(PM.DICT_SOURCE_TABLE, m["task_type_dict_sources"])

    def test_string_ids_without_fk_evidence_is_fail_closed(self):
        """审计高危 #2：没有外键证据时，`STRING_IDS` **整条不启用** → 保持 unresolved。

        db 通用字符串池里恰好有个长得像类型枚举的串（纯 ID 碰撞），光凭外形闸就会把它当 taskType
        解出来 → 产出**看似合法实则编造的 us**。现在必须 fail-closed。
        """
        with tempfile.TemporaryDirectory() as d:
            _write_db(d, tasks=_numeric_tasks(19), mstx=self.MSTX,
                      string_ids={19: "KERNEL_MIX_AIV", 20: "MEMCPY_ASYNC"})
            m = self._measure(d)
            self.assertEqual(m["error"], PM.ERR_UNKNOWN_TASK_TYPE)
            self.assertIsNone(m["us"])
            self.assertEqual(m["unresolved_task_type_ids"], ["19"])
            self.assertNotIn(PM.DICT_SOURCE_STRING_IDS, m["task_type_dict_sources"])
            # 「凭什么不认」必须留证，别让 fail-closed 看起来像工具没本事
            self.assertIn("无外键证据",
                          m["task_type_dict_provenance"]["evidence"][PM.DICT_SOURCE_STRING_IDS])

    def test_id_collision_with_stale_kernel_name_is_fail_closed(self):
        """审计高危 #2 的**原始复现**：`taskType=15` + `STRING_IDS[15]="KERNEL_STALE_NAME"`。

        修前 → `error=None, us=5.0`（纯 ID 碰撞就生成了假的 kernel-only 性能数字）。
        修后 → 无外键证据 → unresolved → fail-closed。
        """
        with tempfile.TemporaryDirectory() as d:
            _write_db(d, tasks=_numeric_tasks(15), mstx=self.MSTX,
                      string_ids={15: "KERNEL_STALE_NAME"})
            m = self._measure(d)
            self.assertEqual(m["error"], PM.ERR_UNKNOWN_TASK_TYPE)
            self.assertIsNone(m["us"])
            self.assertEqual(m["unresolved_task_type_ids"], ["15"])

    def test_string_ids_with_fk_evidence_resolves(self):
        """有 schema 外键（`TASK.taskType → STRING_IDS.id`）＝ 有据可查 → 才允许兜底解析并计时。"""
        with tempfile.TemporaryDirectory() as d:
            _write_db(d, tasks=_numeric_tasks(19), mstx=self.MSTX, string_ids_fk=True,
                      string_ids={19: "KERNEL_MIX_AIV", 20: "MEMCPY_ASYNC"})
            m = self._measure(d)
            self.assertIsNone(m["error"], m)
            self.assertEqual(m["us"], 5.0)
            self.assertIn(PM.DICT_SOURCE_STRING_IDS, m["task_type_dict_sources"])
            evidence = m["task_type_dict_provenance"]["evidence"]
            self.assertIn("外键", evidence[PM.DICT_SOURCE_STRING_IDS])

    def test_string_ids_id_overlapping_name_pool_is_rejected(self):
        """即使有外键：该 id **同时**被 kernel 名引用 → 分不清是类型还是名字 → 拒（仍 fail-closed）。"""
        with tempfile.TemporaryDirectory() as d:
            # kernelName 也用 string id 15 → 15 落进 name 池
            tasks = [(i, i * 10_000, i * 10_000 + 5_000, 15, 15) for i in range(4)]
            _write_db(d, tasks=tasks, mstx=self.MSTX, string_ids_fk=True,
                      string_ids={15: "KERNEL_AIVEC"})
            m = self._measure(d)
            self.assertEqual(m["error"], PM.ERR_UNKNOWN_TASK_TYPE)
            self.assertIsNone(m["us"])
            reasons = [r["reason"] for r in m["task_type_dict_provenance"]["rejected"]]
            self.assertTrue(any("名池" in r for r in reasons), reasons)

    def test_memcpy_id_resolved_and_excluded(self):
        """解出来是搬运类 → 照旧**不计入** kernel-only（解析修好了，口径不许跟着松）。"""
        with tempfile.TemporaryDirectory() as d:
            _write_db(d, tasks=_numeric_tasks(20, name="cpy"), mstx=self.MSTX,
                      enum_table={20: PM.DEVICE_MEMCPY_TYPE})
            m = self._measure(d)
            self.assertIsNone(m["us"])
            self.assertEqual(m["execution_path"], PM.PATH_DEVICE_MEMCPY_ONLY)

    def test_unknown_id_is_fail_closed(self):
        """字典里没有这个 id → **仍 fail-closed**，绝不静默算 0 us；id 带进 detail 供下一轮补字典。"""
        with tempfile.TemporaryDirectory() as d:
            _write_db(d, tasks=_numeric_tasks(24), mstx=self.MSTX,
                      enum_table={15: "KERNEL_AIVEC"})
            m = self._measure(d)
            self.assertEqual(m["error"], PM.ERR_UNKNOWN_TASK_TYPE)
            self.assertIsNone(m["us"])
            self.assertEqual(m["unresolved_task_type_ids"], ["24"])
            behavior, detail = PM.classify_behavior(returncode=0, output="", measurement=m)
            self.assertEqual(behavior, PM.BEHAVIOR_FAILED)      # 不是 no_device_kernel_observed
            self.assertEqual(detail["unresolved_task_type_ids"], ["24"])
            self.assertIn(PM.ENV_TASK_TYPE_MAP, detail["note"])

    def test_no_dictionary_at_all_is_fail_closed(self):
        """db 里一张字典表都没有 → 数值 id 全解不出 → fail-closed（这正是真机 46/46 的现场）。"""
        with tempfile.TemporaryDirectory() as d:
            _write_db(d, tasks=_numeric_tasks(15) + _numeric_tasks(17, name="k2"), mstx=self.MSTX)
            m = self._measure(d)
            self.assertEqual(m["error"], PM.ERR_UNKNOWN_TASK_TYPE)
            self.assertEqual(m["unresolved_task_type_ids"], ["15", "17"])

    def test_string_ids_only_accepts_type_like_tokens(self):
        """`STRING_IDS` 是通用字符串池：kernel 名不得被当成 taskType 解出来（宁可解不出 → fail-closed）。"""
        with tempfile.TemporaryDirectory() as d:
            _write_db(d, tasks=_numeric_tasks(15), mstx=self.MSTX,
                      string_ids={15: "aclnnMedian_Median_Median", 16: "preload_stack_16KB"})
            m = self._measure(d)
            self.assertEqual(m["error"], PM.ERR_UNKNOWN_TASK_TYPE)
            self.assertEqual(m["unresolved_task_type_ids"], ["15"])

    def test_enum_table_used_when_string_ids_has_no_evidence(self):
        """通用字符串池没证据 → 压根不参与；db 的**专用**枚举表照常解析并计时（合法路径没被修没）。"""
        with tempfile.TemporaryDirectory() as d:
            _write_db(d, tasks=_numeric_tasks(15), mstx=self.MSTX,
                      enum_table={15: "KERNEL_AIVEC"}, string_ids={15: "MEMCPY_ASYNC"})
            m = self._measure(d)
            self.assertEqual(m["us"], 5.0)
            self.assertEqual(m["execution_path"], PM.PATH_DEVICE_KERNEL)
            self.assertEqual(m["task_type_dict_sources"], [PM.DICT_SOURCE_TABLE])

    def test_conflicting_sources_are_rejected(self):
        """多来源对同一 id 给出不同名字 → **冲突即拒**（不做「后者覆盖前者」），该 id 作废 → fail-closed。"""
        with tempfile.TemporaryDirectory() as d:
            _write_db(d, tasks=_numeric_tasks(15), mstx=self.MSTX, string_ids_fk=True,
                      enum_table={15: "KERNEL_AIVEC"}, string_ids={15: "MEMCPY_ASYNC"})
            m = self._measure(d)
            self.assertEqual(m["error"], PM.ERR_UNKNOWN_TASK_TYPE)
            self.assertIsNone(m["us"])
            conflicts = m["task_type_dict_provenance"]["conflicts"]
            self.assertEqual([c["id"] for c in conflicts], ["15"])
            self.assertEqual(conflicts[0]["names"], ["KERNEL_AIVEC", "MEMCPY_ASYNC"])

    def test_override_conflicting_with_db_table_is_rejected(self):
        """override 与 db 专用枚举表打架 → 同样作废该 id（人手写的不许覆盖 db 首方数据）。"""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "map.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"provenance": _good_provenance(), "map": {"15": "MEMCPY_ASYNC"}}, f)
            _write_db(d, tasks=_numeric_tasks(15), mstx=self.MSTX, enum_table={15: "KERNEL_AIVEC"})
            db = PM.find_profiler_db(d)
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                mapping, prov = PM.task_type_dictionary(
                    conn, PM._db_tables(conn), {PM.ENV_TASK_TYPE_MAP: path}, type_col="taskType")
            finally:
                conn.close()
            self.assertNotIn("15", mapping)
            self.assertEqual([c["id"] for c in prov["conflicts"]], ["15"])

    def test_classify_never_matches_bare_number(self):
        """裸数字 / 未解出的占位一律 unknown——绝不按字面去撞白名单。"""
        for raw in ("15", "24", PM.UNRESOLVED_TASK_TYPE_PREFIX + "15"):
            self.assertEqual(PM.classify_task_type(raw, PM.ROUTE_DB), PM.KIND_UNKNOWN, raw)
        self.assertEqual(PM.classify_task_type("KERNEL_AIVEC", PM.ROUTE_DB), PM.KIND_COMPUTE)


class TestTaskTypeOverrideMap(unittest.TestCase):
    """外部 id→名映射（审计高危 #3）：**结构化 provenance 逐字段必填 + 值限受控枚举**，任一条不合格整份拒。

    原来只要 `provenance` 是任意非空串、映射值非空就收 → `{"provenance":"实测占位",
    "map":{"15":"KERNEL_TYPO"}}` 直接产出 `5.0 us`。拼写错误 / 未经核实的手工映射把 fail-closed
    变成**假性能数字**，比拿不到数严重得多。
    """

    def _write(self, d, doc):
        path = os.path.join(d, "map.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        return {PM.ENV_TASK_TYPE_MAP: path}

    def test_accepted_with_structured_provenance(self):
        with tempfile.TemporaryDirectory() as d:
            env = self._write(d, {"provenance": _good_provenance(),
                                  "map": {"15": "KERNEL_AIVEC"}})
            mapping, note = PM.load_task_type_overrides(env)
            self.assertEqual(mapping, {"15": "KERNEL_AIVEC"})
            self.assertIn("provenance", note)

    def test_rejected_without_provenance(self):
        with tempfile.TemporaryDirectory() as d:
            env = self._write(d, {"map": {"15": "KERNEL_AIVEC"}})
            mapping, note = PM.load_task_type_overrides(env)
            self.assertEqual(mapping, {})
            self.assertIn("provenance", note)

    def test_rejected_when_provenance_is_prose(self):
        """一句散文（哪怕看着像实测记录）核不了 → 拒；必须是结构化对象。"""
        with tempfile.TemporaryDirectory() as d:
            env = self._write(d, {"provenance": "a3 容器 CANN 9.0.1 + torch_npu 2.10（2026-07-24 实测）",
                                  "map": {"15": "KERNEL_AIVEC"}})
            mapping, note = PM.load_task_type_overrides(env)
            self.assertEqual(mapping, {})
            self.assertIn("provenance", note)

    def test_rejected_when_placeholder_provenance(self):
        """审计高危 #3 的**原始复现**：`{"provenance":"实测占位","map":{"15":"KERNEL_TYPO"}}` → 5.0 us。"""
        with tempfile.TemporaryDirectory() as d:
            env = self._write(d, {"provenance": "实测占位", "map": {"15": "KERNEL_TYPO"}})
            self.assertEqual(PM.load_task_type_overrides(env)[0], {})

    def test_rejected_when_any_field_missing(self):
        """结构化了但缺任一必填字段 → 拒（每个字段都点名在 note 里）。"""
        for field in PM.OVERRIDE_PROVENANCE_FIELDS:
            prov = _good_provenance()
            prov.pop(field)
            with tempfile.TemporaryDirectory() as d:
                env = self._write(d, {"provenance": prov, "map": {"15": "KERNEL_AIVEC"}})
                mapping, note = PM.load_task_type_overrides(env)
                self.assertEqual(mapping, {}, f"{field} 缺失却放行了")
                self.assertIn(field, note)

    def test_rejected_when_field_is_placeholder(self):
        """字段填了但是占位 / 太短 / 纯符号 → 等于没填 → 拒。"""
        for field, bad in (("db", "TODO"), ("cann_version", "待补"), ("torch_npu_version", "----"),
                           ("collect_command", "占位"), ("collected_at", "N/A")):
            with tempfile.TemporaryDirectory() as d:
                env = self._write(d, {"provenance": _good_provenance(**{field: bad}),
                                      "map": {"15": "KERNEL_AIVEC"}})
                self.assertEqual(PM.load_task_type_overrides(env)[0], {}, f"{field}={bad!r} 却放行了")

    def test_short_but_real_version_still_accepted(self):
        """闸别拧过头：`2.1` 这种合法短版本号照收（误拒也是成本，只是方向安全）。"""
        with tempfile.TemporaryDirectory() as d:
            env = self._write(d, {"provenance": _good_provenance(torch_npu_version="2.1"),
                                  "map": {"15": "KERNEL_AIVEC"}})
            self.assertEqual(PM.load_task_type_overrides(env)[0], {"15": "KERNEL_AIVEC"})

    def test_rejected_when_version_has_no_digit_or_date_is_fake(self):
        """版本字段须含版本号数字；采集日期须是 `YYYY-MM-DD` 的**真实**日期。"""
        for prov in (_good_provenance(cann_version="最新版"),
                     _good_provenance(torch_npu_version="latest"),
                     _good_provenance(collected_at="2026/07/24"),
                     _good_provenance(collected_at="2026-13-45")):
            with tempfile.TemporaryDirectory() as d:
                env = self._write(d, {"provenance": prov, "map": {"15": "KERNEL_AIVEC"}})
                self.assertEqual(PM.load_task_type_overrides(env)[0], {}, prov)

    def test_rejected_when_value_outside_controlled_enum(self):
        """映射值须落在**版本化受控枚举**内——未知 `KERNEL_*` 不许靠前缀放行（拼写错误就在这被逮）。"""
        for bad in ("KERNEL_TYPO", "KERNEL_AIC", "AI_CORE", "kernel_aivec", ""):
            with tempfile.TemporaryDirectory() as d:
                env = self._write(d, {"provenance": _good_provenance(), "map": {"15": bad}})
                mapping, note = PM.load_task_type_overrides(env)
                self.assertEqual(mapping, {}, f"{bad!r} 却放行了")
                self.assertIn("受控枚举", note)

    def test_one_bad_entry_rejects_whole_file(self):
        """一份 map 里混进一个不合格项 → **整份拒收**（不静默丢坏留好，免得手误被吞掉）。"""
        with tempfile.TemporaryDirectory() as d:
            env = self._write(d, {"provenance": _good_provenance(),
                                  "map": {"15": "KERNEL_AIVEC", "17": "KERNEL_TYPO"}})
            self.assertEqual(PM.load_task_type_overrides(env)[0], {})

    def test_controlled_enum_is_versioned_and_narrow(self):
        """受控枚举**版本化**、且只含实测坐实的名字（扩它必须带实测依据并升版本）。"""
        self.assertTrue(PM.TASK_TYPE_ENUM_VERSION)
        self.assertTrue(PM.CONTROLLED_TASK_TYPE_NAMES >= PM.DB_DEVICE_KERNEL_TYPES)
        self.assertNotIn("KERNEL_AIC", PM.CONTROLLED_TASK_TYPE_NAMES)
        self.assertIn("受控枚举", PM.CONTROLLED_TASK_TYPE_NAMES_PROVENANCE)

    def test_rejected_when_unreadable(self):
        env = {PM.ENV_TASK_TYPE_MAP: "/definitely/not/here.json"}
        self.assertEqual(PM.load_task_type_overrides(env)[0], {})

    def test_unset_is_silent(self):
        self.assertEqual(PM.load_task_type_overrides({}), ({}, None))

    def test_no_hardcoded_map_shipped(self):
        """仓里**不写死**任何 id→名对照——dogfood 只抓到 id、没抓到名字，编一份就是造数据。"""
        self.assertEqual(PM.DB_TASK_TYPE_ID_NAMES, {})
        self.assertIn("未实测", PM.DB_TASK_TYPE_ID_NAMES_PROVENANCE)

    def test_override_fills_gap_end_to_end(self):
        """db 无字典表 + **合格**的 override → 解得出、计得上（真机可用的兜底路径没被修没）。"""
        with tempfile.TemporaryDirectory() as d:
            env = self._write(d, {"provenance": _good_provenance(),
                                  "map": {"15": "KERNEL_AIVEC"}})
            _write_db(d, tasks=_numeric_tasks(15), mstx=[(0, 1_000_000, "R")])
            db = PM.find_profiler_db(d)
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                mapping, prov = PM.task_type_dictionary(conn, PM._db_tables(conn), env,
                                                        type_col="taskType")
            finally:
                conn.close()
            self.assertEqual(mapping.get("15"), "KERNEL_AIVEC")
            self.assertIn(PM.DICT_SOURCE_ENV, prov["sources"])
            self.assertEqual(prov["enum_version"], PM.TASK_TYPE_ENUM_VERSION)


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
        coll = PM.collection_config(collector=PM.COLLECTOR_MSPROF_CLI, warmup=5, repeat=20)
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

    def test_failure_reason_keeps_structured_error_and_relevant_output_excerpt(self):
        failed = {
            "behavior": PM.BEHAVIOR_FAILED, "us": None, "scope": None,
            "execution_path": None,
            "detail": {
                "returncode": 124,
                "note": "单侧采集超时",
                "parse_error": "measurement_window_required",
                "output_tail": "boilerplate\nRuntimeError: aclnnMedian failed ACL 161002\n"
                               "[OPRUNWAY_SIDE_TIMEOUT] exceeded 120s\n",
            },
        }
        reason = PM.side_failure_reason("baseline", failed)
        self.assertIn("behavior=execution_failed", reason)
        self.assertIn("returncode=124", reason)
        self.assertIn("measurement_window_required", reason)
        self.assertIn("161002", reason)
        self.assertIn("SIDE_TIMEOUT", reason)

        rec = PM.build_perf_record(
            "bad",
            {"behavior": PM.BEHAVIOR_NPU, "us": 5.0, "scope": "kernel_only",
             "execution_path": PM.PATH_DEVICE_KERNEL},
            failed)
        baseline = PM.build_baseline_document([rec])
        self.assertEqual(baseline["excluded"][0]["behavior"], PM.BEHAVIOR_FAILED)
        self.assertIn("161002", baseline["excluded"][0]["reason"])


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

    def test_unified_abi_global_variant_drops_semantically_absent_keyword_group(self):
        """统一 ABI 有 dim/keepDim 占位 slot 时，spec 可据语义 attr=null 整组省略 torch kwarg。"""
        mapping = {**self.MAP, "keyword_groups": [
            {"when": {"attr": "dim", "is_null": False}, "slots": ["dim", "keepdim"]},
        ]}
        call = {"symbol": "Median", "slots": [
            {"role": "in", "name": "self", "input_idx": 0},
            {"role": "attr", "name": "dim", "ctype": "int64", "value": 0},
            {"role": "attr", "name": "keepdim", "ctype": "bool", "value": False},
            {"role": "out", "name": "values", "output_idx": 0},
            {"role": "out_null", "name": "indices"}]}
        global_plan = PM.resolve_torch_baseline_plan(
            mapping, call, {"attrs": {"dim": None, "keepdim": False}})
        dim_plan = PM.resolve_torch_baseline_plan(
            mapping, call, {"attrs": {"dim": 0, "keepdim": False}})
        self.assertEqual(global_plan["keyword"], {})
        self.assertEqual(sorted(dim_plan["keyword"]), ["dim", "keepdim"])

    def test_keyword_group_requires_semantic_attr(self):
        mapping = {**self.MAP, "keyword_groups": [
            {"when": {"attr": "dim", "is_null": False}, "slots": ["dim"]},
        ]}
        call = {"slots": [
            {"role": "in", "name": "self", "input_idx": 0},
            {"role": "attr", "name": "dim", "value": 0}]}
        with self.assertRaises(PM.PerfCollectError):
            PM.resolve_torch_baseline_plan(mapping, call, {"attrs": {}})

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


class TestBaselineEnvironmentIsolation(unittest.TestCase):
    def test_removes_only_exact_dut_vendor_entries(self):
        root = "/work/vendor/customize_nn"
        env = {
            "ASCEND_CUSTOM_OPP_PATH": f"/other/opp:{root}:/keep/opp",
            "LD_LIBRARY_PATH": f"{root}/op_api/lib/:/system/lib:/similar/customize_nn/op_api/lib",
            "PYTHONPATH": f"{root}:/keep/python",
        }
        clean, audit = PM.baseline_env_without_dut(root, env)
        self.assertEqual(clean["ASCEND_CUSTOM_OPP_PATH"], "/other/opp:/keep/opp")
        self.assertEqual(
            clean["LD_LIBRARY_PATH"], "/system/lib:/similar/customize_nn/op_api/lib")
        self.assertEqual(clean["PYTHONPATH"], env["PYTHONPATH"])
        self.assertEqual(audit["removed"]["ASCEND_CUSTOM_OPP_PATH"], [root])
        self.assertEqual(audit["removed"]["LD_LIBRARY_PATH"], [f"{root}/op_api/lib/"])

    def test_requires_absolute_vendor_root(self):
        with self.assertRaises(PM.PerfCollectError):
            PM.baseline_env_without_dut("relative/vendor", {})


class TestAclnnBuiltinBaselinePlan(unittest.TestCase):
    """任务书点名 ACLNN baseline 时直接按 spec 变体调用，不经 torch 等价性证明。"""

    MAP = {"library": "cann_builtin_libopapi", "variants": [
        {"when": {"attr": "dim", "is_null": True},
         "symbol": "Median", "slots": ["self", "valuesOut"]},
        {"when": {"attr": "dim", "is_null": False},
         "symbol": "MedianDim",
         "slots": ["self", "dim", "keepDim", "valuesOut", "indicesOut"],
         "output_dtypes": {"indicesOut": "int64"}},
    ]}
    CALL = {"symbol": "Median", "slots": [
        {"role": "in", "name": "self", "input_idx": 0},
        {"role": "attr", "name": "dim", "ctype": "int64", "value": 0},
        {"role": "attr", "name": "keepDim", "ctype": "bool", "value": False},
        {"role": "out", "name": "valuesOut", "output_idx": 0},
        {"role": "out_null", "name": "indicesOut"}]}

    def test_global_maps_to_builtin_median_abi(self):
        plan = PM.resolve_aclnn_baseline_plan(
            self.MAP, self.CALL, {"id": "g", "attrs": {"dim": None}})
        self.assertEqual(plan["symbol"], "Median")
        self.assertEqual([s["name"] for s in plan["slots"]], ["self", "valuesOut"])

    def test_dim_maps_to_builtin_median_dim_abi(self):
        call = json.loads(json.dumps(self.CALL))
        call["slots"][-1]["role"] = "out"
        call["slots"][-1]["output_idx"] = 1
        plan = PM.resolve_aclnn_baseline_plan(
            self.MAP, call, {"id": "d", "attrs": {"dim": 0}})
        self.assertEqual(plan["symbol"], "MedianDim")
        self.assertEqual([s["name"] for s in plan["slots"]],
                         ["self", "dim", "keepDim", "valuesOut", "indicesOut"])
        self.assertEqual(plan["output_dtypes"], {"indicesOut": "int64"})

    def test_output_dtype_override_rejects_input_slot(self):
        bad = json.loads(json.dumps(self.MAP))
        bad["variants"][1]["output_dtypes"] = {"self": "float32"}
        call = json.loads(json.dumps(self.CALL))
        call["slots"][-1]["role"] = "out"
        call["slots"][-1]["output_idx"] = 1
        with self.assertRaisesRegex(PM.PerfCollectError, "只能覆盖"):
            PM.resolve_aclnn_baseline_plan(
                bad, call, {"id": "d", "attrs": {"dim": 0}})

    def test_uncontrolled_library_rejected(self):
        bad = dict(self.MAP, library="/tmp/libopapi.so")
        with self.assertRaises(PM.PerfCollectError):
            PM.resolve_aclnn_baseline_plan(bad, self.CALL, {"id": "g", "attrs": {"dim": None}})


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


class TestCppExtensionCollectorRoute(unittest.TestCase):
    def test_wrapper_uses_exact_elf_vendor_and_current_stream_mstx(self):
        wrapper = PM._CPP_EXTENSION_WRAPPER
        self.assertIn('torch.ops.load_library(artifact)', wrapper)
        self.assertIn('ctypes.CDLL(vendor_path, mode=ctypes.RTLD_GLOBAL)', wrapper)
        self.assertIn("D.materialize_invocation(", wrapper)
        self.assertIn("torch.npu.current_stream().npu_stream", wrapper)
        self.assertNotIn("time.perf_counter", wrapper)

    def test_collect_routes_cpp_extension_without_aclnn_dut_resolution(self):
        from unittest import mock

        captured = []

        def fake_measure_side(**kwargs):
            captured.append(kwargs)
            return {
                "behavior": PM.BEHAVIOR_NPU,
                "us": 1.0,
                "scope": PM.TIMING_SCOPE,
                "execution_path": PM.PATH_DEVICE_KERNEL,
                "collection": PM.collection_config(
                    collector=PM.COLLECTOR_MSPROF_CLI, warmup=1, repeat=2),
            }

        old = os.environ.get("OPRUNWAY_ACLNN_REAL")
        os.environ["OPRUNWAY_ACLNN_REAL"] = "1"
        try:
            with tempfile.TemporaryDirectory() as root:
                caseset_path = os.path.join(root, "caseset.json")
                with open(caseset_path, "w", encoding="utf-8") as out:
                    json.dump({"op": "X", "cases": [{"id": "c0"}]}, out)
                plan = {
                    "op": "X",
                    "custom_kind": "cpp_extension",
                    "device": 0,
                    "warmup": 1,
                    "repeat": 2,
                    "baseline": "torch_npu",
                    "torch_baseline": {
                        "api": "torch.x", "positional": [], "keyword": {}},
                    "cpp_extension": {"artifact": {"path": "x.so"}},
                    "cases": ["c0"],
                }
                with mock.patch.object(
                        PM, "resolve_plan_dut_lib",
                        side_effect=AssertionError("cpp_extension 不应解析 aclnn dut_lib")), \
                     mock.patch.object(PM, "measure_side", fake_measure_side):
                    doc = PM.collect(
                        caseset_path, root, plan, os.path.join(root, "out.json"))
        finally:
            os.environ.pop("OPRUNWAY_ACLNN_REAL", None)
            if old is not None:
                os.environ["OPRUNWAY_ACLNN_REAL"] = old
        custom = next(row for row in captured if row["side"] == "custom")
        self.assertEqual(custom["custom_kind"], "cpp_extension")
        self.assertEqual(
            custom["cfg_extra"]["cpp_extension"], plan["cpp_extension"])
        self.assertEqual(doc["custom_kind"], "cpp_extension")
        self.assertEqual(doc["custom_provenance"], plan["cpp_extension"])


class TestLiveCollectorAlignment(unittest.TestCase):
    """2026-07-26 真机结论：双边统一 cannbot 同款 msprof CLI + ctypes MSTX + CSV。"""

    def test_both_sides_use_msprof_cli(self):
        self.assertEqual(PM.collector_for("custom"), PM.COLLECTOR_MSPROF_CLI)
        self.assertEqual(PM.collector_for("baseline"), PM.COLLECTOR_MSPROF_CLI)
        with self.assertRaises(PM.PerfCollectError):
            PM.collector_for("other")

    def test_baseline_wrapper_uses_ctypes_mstx_not_torch_profiler(self):
        wrapper = PM._BASELINE_WRAPPER
        self.assertIn('ctypes.CDLL("libms_tools_ext.so")', wrapper)
        self.assertIn("mstx.mstxRangeStartA", wrapper)
        self.assertIn("torch.npu.current_stream().npu_stream", wrapper)
        self.assertNotIn("torch_npu.profiler.profile", wrapper)
        self.assertNotIn("torch_npu.npu.mstx.range_start", wrapper)

    def test_measure_side_pins_csv_route(self):
        from unittest import mock

        seen = {}

        def fake_window(prof_dir, range_name, route=None):
            seen["window_route"] = route
            return ({"route": PM.ROUTE_CSV, "device_id": "0",
                     "start_us": 0.0, "end_us": 100.0, "wall_us": 100.0,
                     "db_path": None}, None)

        def fake_measurement(prof_dir, *, repeat, measurement_window, route=None):
            seen["measurement_route"] = route
            return {"us": 5.0, "kernel_name": "k", "execution_path": PM.PATH_DEVICE_KERNEL,
                    "breakdown": [], "device_memcpy_only_us": None, "route": route,
                    "observed_task_types": {"AI_CORE": repeat}, "window_wall_us": 100.0,
                    "unresolved_task_type_ids": [], "task_type_dict_sources": [],
                    "task_type_dict_provenance": {}, "error": None}

        old = os.environ.get("OPRUNWAY_ACLNN_REAL")
        os.environ["OPRUNWAY_ACLNN_REAL"] = "1"
        try:
            with tempfile.TemporaryDirectory() as d:
                with mock.patch.object(
                        PM, "_run_msprof", return_value=(d, 0, "", ["msprof"])), \
                     mock.patch.object(PM, "parse_measurement_window", fake_window), \
                     mock.patch.object(PM, "parse_kernel_measurement", fake_measurement):
                    result = PM.measure_side(
                        side="baseline", case={"id": "c0"}, caseset_path="caseset.json",
                        work_dir=d, cfg_extra={}, warmup=1, repeat=3, device=0,
                        scratch_dir=d, detect_hybrid=False)
        finally:
            os.environ.pop("OPRUNWAY_ACLNN_REAL", None)
            if old is not None:
                os.environ["OPRUNWAY_ACLNN_REAL"] = old
        self.assertEqual(seen, {"window_route": PM.ROUTE_CSV,
                                "measurement_route": PM.ROUTE_CSV})
        self.assertEqual(result["behavior"], PM.BEHAVIOR_NPU)
        self.assertEqual(result["collection"]["collector"], PM.COLLECTOR_MSPROF_CLI)


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
                                           "env": "torch_npu under msprof_cli(ctypes_mstx,csv)",
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

    def test_aclnn_builtin_round_trip_preserves_library_provenance(self):
        import repo_adapter as RA
        lib = "/opt/ascend/lib64/libopapi.so"
        provenance = {"required_symbol_lib": {"path": lib, "sha256": "a" * 64},
                      "symbols": [
                          {"symbol": "aclnnMedian", "source": "required_symbol_lib",
                           "defining_lib": lib},
                          {"symbol": "aclnnMedianGetWorkspaceSize",
                           "source": "required_symbol_lib", "defining_lib": lib}]}
        rec = PM.build_perf_record(
            "c0",
            {"behavior": PM.BEHAVIOR_NPU, "us": 10.0, "scope": "kernel_only",
             "execution_path": PM.PATH_DEVICE_KERNEL},
            {"behavior": PM.BEHAVIOR_NPU, "us": 20.0, "scope": "kernel_only",
             "execution_path": PM.PATH_DEVICE_KERNEL,
             "runtime_provenance": provenance})
        doc = PM.build_baseline_document([rec], op="Median", source="aclnn_builtin")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "_aclnn_builtin_baseline.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(doc, f)
            baseline = RA.parse_aclnn_builtin_baseline(path)
        self.assertEqual(baseline["source"], "aclnn_builtin")
        self.assertEqual(baseline["per_case"][0]["runtime_provenance"], provenance)


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
            self._parse({"source": "torch_npu",
                         "scope": "host_e2e_with_h2d_d2h", "per_case": []})

    def test_duplicate_case_id_rejected(self):
        with self.assertRaises(ValueError):
            self._parse({"source": "torch_npu", "scope": "kernel_only",
                         "per_case": [{"case_id": "a", "us": 1.0},
                                      {"case_id": "a", "us": 2.0}]})

    def test_bad_us_dropped_with_note(self):
        bl = self._parse({"source": "torch_npu", "scope": "kernel_only",
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

    def test_emit_perf_plan_carries_direct_aclnn_baseline(self):
        import run_workflow as RW
        mapping = {"library": "cann_builtin_libopapi", "variants": [
            {"when": {"attr": "dim", "is_null": True},
             "symbol": "Median", "slots": ["self", "valuesOut"]}]}
        spec = {"op": "Median", "perf": {"baseline": "aclnn_builtin",
                                         "target_ratio": 1.0,
                                         "aclnn_baseline": mapping}}
        with tempfile.TemporaryDirectory() as work:
            path = RW._emit_perf_plan(spec, work)
            with open(path, encoding="utf-8") as f:
                plan = json.load(f)
        self.assertEqual(plan["baseline"], "aclnn_builtin")
        self.assertEqual(plan["aclnn_baseline"], mapping)
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
        self.assertIn('${OPRUNWAY_ACLNN_PERF_TIMEOUT:-1200}', s)
        self.assertIn("OPRUNWAY_ACLNN_PERF_DONE", s)
        self.assertIn("OPRUNWAY_ACLNN_PERF_FAIL", s)

    def test_perf_timeout_scales_with_selected_case_count(self):
        """整轮固定 1200s 会杀掉大 case 集；默认预算必须据实际选中数扩展。"""
        import aclnn_adapter as A
        self.assertEqual(A._default_perf_timeout_s(0), 1200)
        self.assertEqual(A._default_perf_timeout_s(20), 1200)
        self.assertEqual(A._default_perf_timeout_s(50), 3000)
        for bad in (True, -1, 1.5):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                A._default_perf_timeout_s(bad)

        cfg = {"setenv": "/opt/set_env.sh", "vendor_dir": "/home/u/vend",
               "vendor_name": "customize", "rroot": "/home/u/work", "device": "0",
               "soc": "ascend910_93", "snake_op": "median", "host": None}
        paths = {"rcases": "/home/u/work/aclnn_cases", "rout": "/home/u/work/aclnn_out"}
        self.assertIn(
            '${OPRUNWAY_ACLNN_PERF_TIMEOUT:-3000}',
            A._perf_script(cfg, paths, default_timeout_s=3000))


class TestCustomWrapperStrictAndClose(unittest.TestCase):
    """洞 2：性能通路的 runner 必须与精度通路**同口径**（严格档）且**跑完 close**。

    旧版 wrapper 是裸 `AclnnRunner(device=...)`（宽松档）且从不 close：
    极端情况「精度验的是 custom vendor、性能测的是 CANN 内置同名实现」——假 PASS 缺口的性能版本；
    外加逐 case 泄一条 stream。
    """

    #: 本次被测物（DUT）：build install 出来的那一个 libcust_opapi.so。
    _DUT = "/opt/vendors/customize_nn/op_api/lib/libcust_opapi.so"

    def test_wrapper_opens_strict_runner_by_default(self):
        w = PM._CUSTOM_WRAPPER
        self.assertIn('strict = CFG.get("strict_custom_vendor", True)', w)          # 缺省即严格
        self.assertNotIn('bool(CFG.get("strict_custom_vendor"', w)                  # 别宽松转真
        self.assertIn("if not isinstance(strict, bool):", w)                        # "false" 也拦
        self.assertIn("require_custom_vendor=strict", w)

    def test_wrapper_binds_declared_dut(self):
        """wrapper 必须把 DUT 一路带进 runner：只证明「来自某个 custom so」挡不住陈旧产物代跑。"""
        w = PM._CUSTOM_WRAPPER
        self.assertIn('dut_lib = CFG.get("dut_lib")', w)
        self.assertIn("dut_lib=dut_lib", w)
        self.assertIn("if strict and not dut_lib:", w)      # 严格档没 DUT → wrapper 自己先 fail-closed

    def test_wrapper_closes_runner(self):
        w = PM._CUSTOM_WRAPPER
        self.assertIn("with AclnnRunner(", w)          # 出 with 即 close（销毁自建 stream + reset）
        self.assertNotIn("runner = AclnnRunner(", w)   # 不再是裸建、无人回收的那份

    def _collect_with(self, plan_extra, *, dut=_DUT):
        """跑一趟 collect，但把 measure_side 换成桩——只看下发给 wrapper 的 cfg_extra。"""
        from unittest import mock
        captured = []

        def fake_measure_side(**kw):
            captured.append(kw)
            return {"behavior": PM.BEHAVIOR_NPU, "us": 1.0, "scope": "kernel_only",
                    "execution_path": PM.PATH_DEVICE_KERNEL}

        old = os.environ.get("OPRUNWAY_ACLNN_REAL")
        os.environ["OPRUNWAY_ACLNN_REAL"] = "1"
        try:
            with tempfile.TemporaryDirectory() as d:
                cs = os.path.join(d, "caseset.json")
                with open(cs, "w", encoding="utf-8") as f:
                    json.dump({"op": "Foo", "cases": [{"id": "c0"}]}, f)
                plan = {"op": "Foo", "device": 0, "baseline": "torch_npu",
                        "torch_baseline": {"api": "torch.foo", "positional": [], "keyword": {}},
                        "cases": ["c0"]}
                if dut:
                    plan["dut_lib"] = dut
                plan.update(plan_extra)
                with mock.patch.object(PM, "measure_side", fake_measure_side):
                    PM.collect(cs, d, plan, os.path.join(d, "out.json"))
        finally:
            os.environ.pop("OPRUNWAY_ACLNN_REAL", None)
            if old is not None:
                os.environ["OPRUNWAY_ACLNN_REAL"] = old
        return {kw["side"]: kw for kw in captured}

    def test_collect_sends_strict_flag_by_default(self):
        sides = self._collect_with({})
        self.assertIs(sides["custom"]["cfg_extra"]["strict_custom_vendor"], True)

    def test_collect_switch_can_relax_like_driver_flag(self):
        """同一开关：plan 的 `allow_builtin_symbols` = driver 的 `--allow-builtin-symbols`。"""
        sides = self._collect_with({"allow_builtin_symbols": True}, dut=None)
        self.assertIs(sides["custom"]["cfg_extra"]["strict_custom_vendor"], False)
        self.assertIsNone(sides["custom"]["cfg_extra"]["dut_lib"])   # 宽松档不要求 DUT

    def test_collect_flushes_case_progress(self):
        """整轮超时前也须留下已完成 case 的定位证据，不能只剩 PERF_FAIL。"""
        output = StringIO()
        with redirect_stdout(output):
            self._collect_with({})
        progress = json.loads(output.getvalue().strip())["oprunway_perf_progress"]
        self.assertEqual(progress["completed"], 1)
        self.assertEqual(progress["total"], 1)
        self.assertEqual(progress["case_id"], "c0")
        self.assertEqual(progress["custom_behavior"], PM.BEHAVIOR_NPU)
        self.assertEqual(progress["baseline_behavior"], PM.BEHAVIOR_NPU)


class TestPerfPlanDutDeclaration(unittest.TestCase):
    """DUT 从 perf plan 一路传到 wrapper 的 CFG（改动⑪ 的性能通路侧）。

    精度侧已钉死「符号必须由**本次 build 出的** libcust_opapi.so 定义」；性能侧若还停在
    「来自某个 custom vendor so」，环境里继承来的**上次**安装产物照样能代跑 → 性能数字
    根本不是被测物的。定不出 DUT 一律 fail-closed，**绝不默默退回宽松档**。
    """

    _collect_with = TestCustomWrapperStrictAndClose._collect_with
    _DUT = TestCustomWrapperStrictAndClose._DUT

    def test_dut_lib_reaches_wrapper_cfg(self):
        sides = self._collect_with({})
        self.assertEqual(sides["custom"]["cfg_extra"]["dut_lib"], self._DUT)

    def test_dut_vendor_root_derives_lib_path(self):
        """按 vendor **内容根**声明：DUT so = `<root>/op_api/lib/libcust_opapi.so`。"""
        sides = self._collect_with({"dut_vendor_root": "/opt/vendors/customize_nn"}, dut=None)
        self.assertEqual(sides["custom"]["cfg_extra"]["dut_lib"], self._DUT)

    def test_falls_back_to_adapter_vendor_fields(self):
        """plan 没给 dut_* 时，沿用 adapter 已知的 vendor 字段（与 `_ENV_PREAMBLE` 的 VC 同口径）。"""
        sides = self._collect_with({"vendor_dir": "/opt", "vendor_name": "customize"}, dut=None)
        self.assertEqual(sides["custom"]["cfg_extra"]["dut_lib"], self._DUT)

    def test_strict_without_any_dut_source_fails_closed(self):
        with self.assertRaises(PM.PerfCollectError) as ctx:
            self._collect_with({}, dut=None)
        msg = str(ctx.exception)
        self.assertIn("dut_lib", msg)
        self.assertIn("dut_vendor_root", msg)
        self.assertIn("allow_builtin_symbols", msg)          # 说清另一条合法出路（内置基线场景）

    def test_conflicting_dut_declarations_fail_closed(self):
        """两种声明指向不同文件 → fail-closed（唯一性判据复用 runner，绝不替调用方挑一个）。"""
        from aclnn_runtime.base import AclnnRunnerError
        with self.assertRaises(AclnnRunnerError) as ctx:
            self._collect_with({"dut_vendor_root": "/opt/vendors/other_nn"})
        self.assertIn("DUT 必须唯一", str(ctx.exception))

    def test_non_string_dut_field_fails_closed(self):
        with self.assertRaises(PM.PerfCollectError) as ctx:
            self._collect_with({"dut_lib": 42}, dut=None)
        self.assertIn("非空字符串", str(ctx.exception))

    def test_plan_bool_rejects_stringly_typed_switch(self):
        """`bool("false")` / `bool("0")` 都是 True——字符串写法必须 raise，绝不静默放行。"""
        for bad in ("false", "0", "true", 0, 1):
            with self.subTest(bad=bad):
                with self.assertRaises(PM.PerfCollectError):
                    PM.plan_bool({"allow_builtin_symbols": bad}, "allow_builtin_symbols")
        self.assertIs(PM.plan_bool({}, "allow_builtin_symbols"), False)          # 缺省即严格
        self.assertIs(PM.plan_bool({"allow_builtin_symbols": True},
                                   "allow_builtin_symbols"), True)

    def test_collect_rejects_stringly_typed_switch(self):
        """整条通路上也拦得住：plan 里写 "false" → collect 直接抛，不静默关掉严格档。"""
        for bad in ("false", "0"):
            with self.subTest(bad=bad):
                with self.assertRaises(PM.PerfCollectError) as ctx:
                    self._collect_with({"allow_builtin_symbols": bad})
                self.assertIn("假 PASS", str(ctx.exception))

    def test_same_switch_semantics_as_adapter(self):
        """与 `aclnn_adapter._plan_bool` 同口径（host 侧早失败、容器侧最后一道，两处一致）。"""
        import aclnn_adapter as A
        with self.assertRaises(ValueError):
            A._plan_bool({"allow_builtin_symbols": "false"}, "allow_builtin_symbols")
        with self.assertRaises(PM.PerfCollectError):
            PM.plan_bool({"allow_builtin_symbols": "false"}, "allow_builtin_symbols")


class TestSpecTargetRatio(unittest.TestCase):
    """spec 修正：median 的 target_ratio 依任务书「不劣化」= 1.0（非参考仓默认 0.6）。"""

    def test_median_target_ratio_is_one(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "samples", "specs", "median.spec.json")
        with open(path, encoding="utf-8") as f:
            spec = json.load(f)
        self.assertEqual(spec["perf"]["target_ratio"], 1.0)
        self.assertIn("不劣化", spec["perf"]["_target_ratio_note"])
        self.assertEqual(spec["perf"]["baseline"], "torch_npu")
        self.assertEqual(spec["perf"]["torch_baseline"]["api"], "torch.median")
        self.assertNotIn("aclnn_baseline", spec["perf"])
        self.assertEqual(spec["perf"]["case_source"], "precision_cases")
        self.assertEqual(spec["perf"]["shape_classification"]["small_max_bytes"], 262144)

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
