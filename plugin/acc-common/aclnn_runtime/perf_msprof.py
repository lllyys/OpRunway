"""perf_msprof — kernel-only 性能采集（MSTX 测量窗 + 窗内 kernel 累加），**op-中立、字段驱动**。

⚠ **本模块按 `doc/oprunway-torch-baseline-design.md` §9.7（2026-07-24 a3 容器实测）返工**——
§9.7 推翻了原设计 3 条、补了 3 条防御，凡与设计 §3 组件⑤ 冲突**以 §9.7 为准**：

* **A（采集入口）**：msprof CLI 下 **Python 侧根本打不出 MSTX**（`torch_npu.npu.mstx.range_start()`
  被 `@_no_exception_func()` 吞异常、静默返回 rid=0；CANN 原生 `import mstx` 进程挂死）。
  **唯一成立**：`torch_npu.profiler.profile(experimental_config=_ExperimentalConfig(mstx=True))`，
  产物 `ascend_pytorch_profiler*.db` 的 `MSTX_EVENTS` 表配 `TASK`+`COMPUTE_TASK_INFO` 裁窗。
  → **baseline（torch）侧只走 torch_npu.profiler**；custom（ctypes runner）侧仍走 msprof CLI +
  CANN mstx C API（`libms_tools_ext.so`），**该侧 MSTX 尚未实测（§9.7「下一个待 de-risk」）**，
  打不出即 fail-closed（rid=0 直接抛），绝不静默拿整进程当窗。
* **B（kernel 白名单两套）**：CSV 路线（`task_time.kernel_type` / `op_summary.Task Type`）=
  :data:`CSV_DEVICE_KERNEL_TYPES`；**db 路线（`TASK.taskType`）是 `KERNEL_AIVEC`/`KERNEL_MIX_AIV`/…**
  （:data:`DB_DEVICE_KERNEL_TYPES` + `KERNEL_` 家族规则），用 CSV 那套**一个都匹配不上 → 静默得 0 us**。
  → 窗内出现**任何未分类的 taskType 一律 fail-closed**（:data:`ERR_UNKNOWN_TASK_TYPE`，并把观察到的
  类型直方图带进 detail），**绝不让空结果冒充「没有 kernel」**。
* **C（`--ai-core` 必须显式关）**：msprof 默认 `--ai-core=on` 让 Sort(MIX_AIV) 虚高 **3.75×**、
  每次调用总和虚高 **2.0×**；关掉后 msprof / torch_npu profiler 三路吻合（150~159 us/call）。
  → :data:`MSPROF_EXTRA_ARGS` 显式带 `--ai-core=off`，且**双边采集配置须一致**
  （:func:`check_collection_config`，不一致 → :data:`BLOCKED_INCOMPARABLE_COLLECTION_CONFIG`）。
* **D（MIX 类 kernel 在 `TASK` 表出现两次）**：实测 TASK 373 行 vs COMPUTE_TASK_INFO 312 行，多出 52 个
  无 name 的 `KERNEL_MIX_AIV` → db 查询**必须 `join COMPUTE_TASK_INFO on globalTaskId` 且丢弃 name 为
  NULL 的行**，否则翻倍。
* **E（MSTX range 的 wall duration 绝不能当性能数字）**：实测某窗 wall=141ms 而窗内 kernel 累加仅 1.5ms
  （差 90 倍，全是 profiler 启动 + 首次 kernel 加载）。range **只作裁剪边界**；wall 只以
  `window_wall_us` 记进 detail 供人看，**任何计时数都不得由它派生**。
* **F（CSV 时间戳两个坑）**：`Task Start Time(us)` 带**尾随 tab**、19 位十进制用 float 解析丢精度
  → **优先 db 路线（`startNs` 整数纳秒，窗内比较全程整数）**、次选 csv。

职责边界
--------
本模块**只产计时数与行为分类**，一律不下「性能达标」结论——裁决唯一归 `perf_compare.py`
（ADR 0007「判定只归确定性脚本链」）。产出经 `aclnn_adapter` 落成两份数据：
  · custom 侧 us → evidence `perf.{scope,us}`；
  · baseline 侧 us → `work/_torch_npu_baseline.json` → `repo_adapter.parse_torch_npu_baseline`
    → `perf_compare`（**perf_compare 判定逻辑零改、源无关**，只读 us + scope + ratio）。

计时口径（三条硬规矩，§9.7 ✅ 成立可照写）
----------------------------------------
1. **只累加 device 计算 kernel**：类型白名单**分路线两套**（见 B）。`MEMCPY_ASYNC` 一律不计入；
   ⚠ 该规则**当前是空转、未验证**（§9.7 📌：CANN 9.0.1 + torch_npu Level0 下 H2D/D2H 不产生 TASK 行，
   造的 WITH_MEMCPY 窗 taskType 分布与纯 device 窗完全一样）——规则留着但**别当已验证**。
   若整个测量窗内**只有** memcpy，单独记 `device_memcpy_only_us` + `execution_path="device_memcpy_only"`，
   **但不产 `us`**（行为归 `no_device_kernel_observed` → 不计时、不比、不冒充达标）。
2. **MSTX range 圈定测量窗**：解析严格限定在 range 内；**缺 MSTX 证据即 fail-closed**
   （:data:`ERR_WINDOW_REQUIRED`），**绝不靠 task 数反推窗口**（§9.7：MSTX 的失败是静默的，
   不 fail-closed 就会拿整进程 kernel 当测量窗）。
3. **稳态**：`warmup=5, repeat=20`（实测 warmup 窗 157.60 vs measure 窗 158.95，差 0.9%）；
   先 warmup、再**重新物化新鲜输入**，只把被测迭代包进 range。每 kernel 取 repeat 次**中位数** ×
   每次调用的启动数 = 单次调用耗时，多 kernel 求和；启动数 < repeat 的行 = 一次性 setup kernel
   （实测揪出 `preload_stack_16KB` count=1），按「每次调用都重复」规则**剔除**。

基线行为五分类（:data:`BEHAVIORS`）
--------------------------------
`npu` / `cpu_fallback` / `hybrid_host_device` / `execution_failed` / `no_device_kernel_observed`。
**只有 `npu` 侧才计时**（:data:`TIMED_BEHAVIORS`），其余只报行为、不计时、**不硬算比值**。
（`no_device_kernel_observed` 判得住：实测 CPU-only 窗 0 个计算 kernel vs device 窗 120 个。）

⚠ **hybrid 检测只作用于 baseline（torch）侧**：custom 侧走 ctypes runner，其 H2D/D2H 是
**runner form 的固有物化开销**，不是「算子一半跑在 host」。hybrid 证据源缺失时**不冒充「已判为非
hybrid」**：记 `available=False` + note；此方向漏判会让 baseline 偏小 → ratio(=baseline/custom) 偏小
→ 对被测**更严格**，不会造出假达标。

泛化（律令#0）
-------------
一切据**字段**驱动、**绝无按算子名分支**：
  · custom 侧调用 = 该 case **已解析好的** `aclnn_call`（spec `call_variants` → gen_cases 逐 case 解析）；
  · baseline 侧调用 = spec `perf.torch_baseline` 声明的 **slot-name → torch 形参**映射，缺失即 fail-closed。

环境（§9.7 环境更正）
--------------------
容器内 `torch.npu.device_count()=16`，**绝不假定单卡**——device 一律由 plan 显式给（缺即 fail-closed），
窗与 task 行按 device 交叉过滤。根盘仅剩 41G → **prof 产物解析完即删**（`OPRUNWAY_PERF_KEEP_PROF=1` 可留）。

真机 gate：一切实际采集须 `OPRUNWAY_ACLNN_REAL=1`（同 `aclnn_adapter`）。
纯解析 / 聚合 / 分类 / speedup / scope / 采集配置校验**无 CANN / torch / numpy 依赖，可离线单测**。
"""

from __future__ import annotations

import csv
import glob
import json
import os
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

# ── 常量（单一真源）────────────────────────────────────────────────────────────────

#: 解析路线。**优先 db**（§9.7 F：`startNs` 整数纳秒，无 csv 的尾随 tab / float 丢精度问题）。
ROUTE_DB = "profiler_db"
ROUTE_CSV = "msprof_csv"

#: **CSV 路线**的计算 kernel 白名单（`task_time.kernel_type` / `op_summary.Task Type`）。
#: `AI_CPU` 是 device 上的昇腾 kernel 类型，**不是** host-CPU 回退，故计入。
#: （§9.7 B 实测确认这套只对 CSV 路线成立。`MIX` 承前保留，未在 §9.7 实测中出现。）
CSV_DEVICE_KERNEL_TYPES = frozenset(
    ("AI_VECTOR_CORE", "AI_CORE", "MIX_AIC", "MIX_AIV", "MIX", "AI_CPU"))
#: **db 路线**的计算 kernel 白名单（`TASK.taskType`）——§9.7 B 实测坐实这两个字面值。
DB_DEVICE_KERNEL_TYPES = frozenset(("KERNEL_AIVEC", "KERNEL_MIX_AIV"))
#: db 路线的**家族规则**：`KERNEL_*` 同族皆算子 kernel（AIC / AICPU / MIX_AIC 等未逐个实测，
#: 但同族且不在非计算集内 → 计入；真出现没见过的族外类型会被判 unknown → fail-closed）。
DB_DEVICE_KERNEL_PREFIX = "KERNEL_"

#: 异步搬运类型——**一律不计入** kernel-only 时间。⚠ §9.7 📌 **未验证**（Level0 下没见过 memcpy TASK 行）。
DEVICE_MEMCPY_TYPE = "MEMCPY_ASYNC"
CSV_MEMCPY_TYPES = frozenset((DEVICE_MEMCPY_TYPE, "MEMSET"))
DB_MEMCPY_TYPES = frozenset((DEVICE_MEMCPY_TYPE, "KERNEL_MEMCPY", "MEMSET_ASYNC", "MEMSET"))

#: 类型分类结果。
KIND_COMPUTE = "compute"
KIND_MEMCPY = "memcpy"
KIND_UNKNOWN = "unknown"

#: 本模块产出的计时口径（双边必须同为它，否则 perf_compare 判 BLOCKED_INCOMPARABLE_TIMING_SCOPE）。
TIMING_SCOPE = "kernel_only"

DEFAULT_WARMUP = 5
DEFAULT_REPEAT = 20

# —— 采集配置（§9.7 C：`--ai-core` 必须显式关，且双边同配置）——
COLLECTOR_MSPROF_CLI = "msprof_cli"
COLLECTOR_TORCH_PROFILER = "torch_npu_profiler"
#: `--ai-core=on`（msprof 默认）会让数字虚高 2.0~3.75×（§9.7 C 实测）→ 一律显式关。
AI_CORE_PROFILING = "off"
PROFILER_LEVEL = "Level0"
KERNEL_ACCOUNTING = "median_x_launches"
#: msprof CLI 固定参数。`--ai-core=off` 是 §9.7 C 的硬要求，**不得删**。
MSPROF_EXTRA_ARGS = ("--task-time=on", "--ascendcl=on", "--msproftx=on", "--ai-core=off")
#: 采集配置里**必须双边一致**的键（`collector` 不比：§9.7 C 实测关掉 ai-core 后 msprof/torch 三路吻合）。
COMPARED_COLLECTION_KEYS = ("ai_core", "profiler_level", "warmup", "repeat",
                            "timing_scope", "kernel_accounting")

# —— 行为五分类（只有 npu 计时）——
BEHAVIOR_NPU = "npu"
BEHAVIOR_CPU_FALLBACK = "cpu_fallback"
BEHAVIOR_HYBRID = "hybrid_host_device"
BEHAVIOR_FAILED = "execution_failed"
BEHAVIOR_NO_KERNEL = "no_device_kernel_observed"
BEHAVIORS = frozenset({BEHAVIOR_NPU, BEHAVIOR_CPU_FALLBACK, BEHAVIOR_HYBRID,
                       BEHAVIOR_FAILED, BEHAVIOR_NO_KERNEL})
#: **只有这一类才计时**；其余只报行为、不算 us、不算 speedup。
TIMED_BEHAVIORS = frozenset({BEHAVIOR_NPU})

#: 执行路径（比 behavior 细一层，用于可比性标注）。
PATH_DEVICE_KERNEL = "device_kernel"
PATH_DEVICE_MEMCPY_ONLY = "device_memcpy_only"

#: 双边可比性标注：两侧都是真 device 计算 kernel → fair；否则 indicative。
COMPARABILITY_FAIR = "fair"
COMPARABILITY_INDICATIVE = "indicative"

#: 双边 scope 不一致 → perf_compare 的挂起码（口径与 `perf_compare._VALID_SCOPES` 校验一致）。
BLOCKED_INCOMPARABLE_TIMING_SCOPE = "BLOCKED_INCOMPARABLE_TIMING_SCOPE"
#: 双边**采集配置**不一致（§9.7 C：ai-core 开关不同就能差 2×）→ 不可比，绝不算比值。
BLOCKED_INCOMPARABLE_COLLECTION_CONFIG = "BLOCKED_INCOMPARABLE_COLLECTION_CONFIG"
#: 精度先筛：未过精度的 case 不测性能（测了也无意义——算错的快不算快）。
SKIPPED_ACCURACY_FAILED = "skipped_accuracy_failed"

# 解析错误码（稳定字符串，供分类与单测断言；**不拼进用户可控内容**）。
ERR_NO_MSTX_CSV = "no_mstx_csv"
ERR_NO_PROF_DATA = "no_profiling_output"
ERR_MSTX_TABLE_MISSING = "mstx_table_missing"
ERR_MSTX_RANGE_NOT_FOUND = "mstx_range_not_found"
ERR_MSTX_RANGE_AMBIGUOUS = "multiple_mstx_ranges"
ERR_WINDOW_REQUIRED = "measurement_window_required"
ERR_NO_TASK_TIME_CSV = "no_task_time_csv"
ERR_NO_TASK_TABLE = "no_task_table_in_db"
ERR_NO_DEVICE_TASK = "no_repeated_device_execution_tasks"
ERR_UNKNOWN_TASK_TYPE = "unknown_task_type_in_window"
ERR_INCONSISTENT_SEQUENCE = "inconsistent_repeated_device_task_sequence"

# torch_npu 在算子无 NPU 实现、静默落到 host CPU 时打的告警。**退出 0 不是「跑在 device 上」的证据**
# ——task_time 里那些不相干的搬运 op 照样能被解析成一个「kernel 时间」，那是垃圾数。这两串是唯一可靠信号。
CPU_FALLBACK_MARKERS = ("npu_cpu_fallback", "fall back to run on the CPU")

# 采集侧 stdout 哨兵（wrapper 打，父进程解析）。
MARKER_OUTPUT_DEVICES = "__OPRUNWAY_PERF_OUTPUT_DEVICES__"
MARKER_PHASE = "__OPRUNWAY_PERF_PHASE__"
MARKER_PROF_DIR = "__OPRUNWAY_PERF_PROF_DIR__"

_MSTX_CSV_GLOB = "msprof_tx_*.csv"
_TASK_TIME_CSV_GLOB = "task_time_*.csv"
_API_STAT_CSV_GLOB = "api_statistic_*.csv"
#: torch_npu profiler（export_type=Db）产物；msprof CLI 亦可能产 db。
_DB_GLOBS = ("ascend_pytorch_profiler*.db", "msprof*.db", "ascend_profiler*.db")

# db 表 / 列名（§9.7 A/D 实测）。
TABLE_MSTX = "MSTX_EVENTS"
TABLE_TASK = "TASK"
TABLE_COMPUTE_TASK_INFO = "COMPUTE_TASK_INFO"
TABLE_STRING_IDS = "STRING_IDS"
TABLE_CANN_API = "CANN_API"
_CTI_NAME_COLUMNS = ("name", "opName", "kernelName", "opType")
_DEVICE_COLUMNS = ("deviceId", "device_id", "devId")


class PerfCollectError(RuntimeError):
    """性能采集不可继续（配置缺失 / gate 未开 / 采集端硬错）。一律 fail-closed，绝不返回编的数。"""


# ── 通用小工具 ──────────────────────────────────────────────────────────────────

def _as_float(value):
    """字符串 → float。`.strip()` 顺手吃掉 §9.7 F 的**尾随 tab**（`Task Start Time(us)` 带 `\\t`）。"""
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _as_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _first(row, keys):
    for key in keys:
        if key in row:
            value = row[key]
            if value is not None and str(value).strip() != "":
                return value
    return None


def _dev(value):
    text = str(value).strip() if value is not None else ""
    return text or None


# ── CSV 路线（msprof CLI 产物；§9.7 F：次选）────────────────────────────────────────

_CSV_NAME_KEYS = ("kernel_name", "Kernel Name", "Op Name", "OP Name", "Name")
_CSV_TYPE_KEYS = ("kernel_type", "Task Type", "task_type")
_CSV_START_KEYS = ("task_start(us)", "Task Start Time(us)", "Start Time(us)")
_CSV_STOP_KEYS = ("task_stop(us)", "Task End Time(us)", "End Time(us)")
_CSV_DUR_KEYS = ("task_time(us)", "Task Duration(us)", "Duration(us)")


def _read_rows(prof_dir, pattern):
    """读 `<prof_dir>/mindstudio_profiler_output/<pattern>`（找不到再全树递归）的全部行；无文件 → None。"""
    root = str(prof_dir)
    files = sorted(glob.glob(os.path.join(root, "mindstudio_profiler_output", pattern)))
    if not files:
        files = sorted(glob.glob(os.path.join(root, "**", pattern), recursive=True))
    if not files:
        return None
    rows = []
    for path in files:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                rows.extend(list(csv.DictReader(f)))
        except OSError:
            continue
    return rows


def normalize_csv_task_rows(rows):
    """csv task 行 → 归一行 `{"name","type","start","end","duration_us","unit","device_id"}`（unit=us）。"""
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        ktype = _first(row, _CSV_TYPE_KEYS)
        if ktype is None:
            continue
        start = _as_float(_first(row, _CSV_START_KEYS))
        stop = _as_float(_first(row, _CSV_STOP_KEYS))
        dur = _as_float(_first(row, _CSV_DUR_KEYS))
        if stop is None and start is not None and dur is not None:
            stop = start + dur
        if dur is None and start is not None and stop is not None:
            dur = stop - start
        name = _first(row, _CSV_NAME_KEYS)
        out.append({"name": str(name).strip() if name is not None else "unknown",
                    "type": str(ktype).strip(),
                    "start": start, "end": stop, "duration_us": dur, "unit": "us",
                    "device_id": _dev(_first(row, ("Device_id", "Device ID") + _DEVICE_COLUMNS))})
    return out


# ── db 路线（torch_npu profiler `ascend_pytorch_profiler*.db`；§9.7 A/D/F：优先）────────

def _connect_ro(db_path):
    """只读打开 sqlite（绝不写采集产物）。打不开 → None。"""
    try:
        return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None


def _db_tables(conn):
    try:
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
        return {row[0] for row in cur.fetchall()}
    except sqlite3.Error:
        return set()


def _db_columns(conn, table):
    try:
        cur = conn.execute(f"PRAGMA table_info({table})")
        return [row[1] for row in cur.fetchall()]
    except sqlite3.Error:
        return []


def find_profiler_db(root):
    """在 `root` 下递归找含 MSTX/TASK 表的 profiler db；没有 → None。**不猜、不取无关 db**。"""
    cands = []
    for pat in _DB_GLOBS:
        cands.extend(glob.glob(os.path.join(str(root), "**", pat), recursive=True))
    for path in sorted(set(cands)):
        conn = _connect_ro(path)
        if conn is None:
            continue
        try:
            tables = _db_tables(conn)
        finally:
            conn.close()
        if TABLE_MSTX in tables or TABLE_TASK in tables:
            return path
    return None


def _string_ids(conn, tables):
    """`STRING_IDS`（id→字符串）映射；表不存在 → 空 dict。db 里 name 常是 string id。"""
    if TABLE_STRING_IDS not in tables:
        return {}
    cols = _db_columns(conn, TABLE_STRING_IDS)
    if len(cols) < 2:
        return {}
    id_col = "id" if "id" in cols else cols[0]
    val_col = "value" if "value" in cols else ("name" if "name" in cols else cols[1])
    try:
        cur = conn.execute(f"SELECT {id_col}, {val_col} FROM {TABLE_STRING_IDS}")
        return {row[0]: row[1] for row in cur.fetchall()}
    except sqlite3.Error:
        return {}


def _resolve(value, strings):
    """name 字段可能是 STRING_IDS 的 id → 能解就解，解不了原样返回（分组仍唯一，只是可读性差）。"""
    if isinstance(value, int) and value in strings:
        return strings[value]
    return value


def read_db_mstx_rows(db_path):
    """读 `MSTX_EVENTS` → `[{"message","start_ns","end_ns","device_id"}]`；表缺 → `(None, err)`。"""
    conn = _connect_ro(db_path)
    if conn is None:
        return None, ERR_NO_PROF_DATA
    try:
        tables = _db_tables(conn)
        if TABLE_MSTX not in tables:
            return None, ERR_MSTX_TABLE_MISSING
        cols = _db_columns(conn, TABLE_MSTX)
        if not ({"startNs", "endNs", "message"} <= set(cols)):
            return None, ERR_MSTX_TABLE_MISSING
        dev_col = next((c for c in _DEVICE_COLUMNS if c in cols), None)
        strings = _string_ids(conn, tables)
        sel = ["startNs", "endNs", "message"] + ([dev_col] if dev_col else [])
        cur = conn.execute(f"SELECT {', '.join(sel)} FROM {TABLE_MSTX}")
        out = []
        for row in cur.fetchall():
            out.append({"start_ns": row[0], "end_ns": row[1],
                        "message": _resolve(row[2], strings),
                        "device_id": _dev(row[3]) if dev_col else None})
        return out, None
    except sqlite3.Error:
        return None, ERR_NO_PROF_DATA
    finally:
        conn.close()


def read_db_task_rows(db_path):
    """读 `TASK ⋈ COMPUTE_TASK_INFO`（§9.7 D）→ 归一行（unit=ns）；表缺 → `(None, err)`。

    **必须 join `COMPUTE_TASK_INFO on globalTaskId` 且丢弃 name 为 NULL 的行**——MIX 类 kernel 在
    `TASK` 表出现两次（实测多出 52 个无 name 的 `KERNEL_MIX_AIV`），不去重就翻倍。
    """
    conn = _connect_ro(db_path)
    if conn is None:
        return None, ERR_NO_PROF_DATA
    try:
        tables = _db_tables(conn)
        if TABLE_TASK not in tables or TABLE_COMPUTE_TASK_INFO not in tables:
            return None, ERR_NO_TASK_TABLE
        tcols = set(_db_columns(conn, TABLE_TASK))
        ccols = _db_columns(conn, TABLE_COMPUTE_TASK_INFO)
        if not ({"globalTaskId", "startNs", "endNs", "taskType"} <= tcols):
            return None, ERR_NO_TASK_TABLE
        name_col = next((c for c in _CTI_NAME_COLUMNS if c in ccols), None)
        if name_col is None or "globalTaskId" not in ccols:
            return None, ERR_NO_TASK_TABLE
        dev_col = next((c for c in _DEVICE_COLUMNS if c in tcols), None)
        strings = _string_ids(conn, tables)
        sel = ["t.startNs", "t.endNs", "t.taskType", f"c.{name_col}"]
        if dev_col:
            sel.append(f"t.{dev_col}")
        sql = (f"SELECT {', '.join(sel)} FROM {TABLE_TASK} t "
               f"JOIN {TABLE_COMPUTE_TASK_INFO} c ON t.globalTaskId = c.globalTaskId "
               f"WHERE c.{name_col} IS NOT NULL")
        cur = conn.execute(sql)
        out = []
        for row in cur.fetchall():
            start, end = _as_int(row[0]), _as_int(row[1])
            if start is None or end is None:
                continue
            out.append({"name": str(_resolve(row[3], strings)),
                        "type": str(row[2]).strip(),
                        "start": start, "end": end,
                        "duration_us": (end - start) / 1000.0, "unit": "ns",
                        "device_id": _dev(row[4]) if dev_col else None})
        return out, None
    except sqlite3.Error:
        return None, ERR_NO_TASK_TABLE
    finally:
        conn.close()


def count_db_api_calls(db_path, api_name):
    """db 里某 AscendCL API 的调用次数（hybrid 证据的 db 路线）；表缺 → None（= 证据源不可用）。"""
    conn = _connect_ro(db_path)
    if conn is None:
        return None
    try:
        tables = _db_tables(conn)
        if TABLE_CANN_API not in tables:
            return None
        cols = _db_columns(conn, TABLE_CANN_API)
        name_col = next((c for c in ("name", "apiName", "type") if c in cols), None)
        if name_col is None:
            return None
        strings = _string_ids(conn, tables)
        cur = conn.execute(f"SELECT {name_col} FROM {TABLE_CANN_API}")
        return sum(1 for row in cur.fetchall() if str(_resolve(row[0], strings)) == api_name)
    except sqlite3.Error:
        return None
    finally:
        conn.close()


# ── 测量窗（MSTX）────────────────────────────────────────────────────────────────

def _window(range_name, device_id, *, route, start_ns=None, end_ns=None,
            start_us=None, end_us=None, source_rows=1, db_path=None):
    if start_us is None and start_ns is not None:
        start_us, end_us = start_ns / 1000.0, end_ns / 1000.0
    wall = (end_us - start_us) if (start_us is not None and end_us is not None) else None
    return {"range_name": str(range_name), "device_id": device_id, "route": route,
            "db_path": db_path, "start_ns": start_ns, "end_ns": end_ns,
            "start_us": start_us, "end_us": end_us,
            # ⚠ §9.7 E：wall **绝不是性能数字**（实测 wall 141ms vs 窗内 kernel 1.5ms，差 90 倍）。
            # 只作人读诊断；任何计时数都由窗内 kernel duration 累加得来。
            "wall_us": wall, "source_rows": source_rows}


def parse_measurement_window(prof_dir, range_name, route=None):
    """解析 MSTX 测量窗 → `(window|None, err|None)`。**优先 db 路线**（§9.7 A/F）。

    **缺 MSTX 证据一律 fail-closed**：没有 profiling 产物 / 没有 MSTX 表或 csv / 找不到该 range /
    找到多个不同的 range 都返回 err，**绝不靠 task 数反推窗口**（那是「没证据也给个数」，本仓最忌）。
    """
    db_path = find_profiler_db(prof_dir) if route in (None, ROUTE_DB) else None
    if db_path is not None:
        return _window_from_db(db_path, range_name)
    if route == ROUTE_DB:
        return None, ERR_NO_PROF_DATA
    return _window_from_csv(prof_dir, range_name)


def _window_from_db(db_path, range_name):
    rows, err = read_db_mstx_rows(db_path)
    if err is not None:
        return None, err
    matches = []
    for row in rows:
        if str(row.get("message") or "") != str(range_name):
            continue
        start, end = _as_int(row.get("start_ns")), _as_int(row.get("end_ns"))
        if start is None or end is None or end < start:
            continue
        matches.append((row.get("device_id"), start, end))
    if not matches:
        return None, ERR_MSTX_RANGE_NOT_FOUND
    if len(set(matches)) != 1:
        return None, ERR_MSTX_RANGE_AMBIGUOUS
    device_id, start, end = matches[0]
    return _window(range_name, device_id, route=ROUTE_DB, start_ns=start, end_ns=end,
                   source_rows=len(matches), db_path=db_path), None


def _window_from_csv(prof_dir, range_name):
    rows = _read_rows(prof_dir, _MSTX_CSV_GLOB)
    if rows is None:
        return None, ERR_NO_MSTX_CSV
    matches = []
    for row in rows:
        if str(row.get("message") or "") != str(range_name):
            continue
        start = _as_float(row.get("Device Start_time(us)") or row.get("Device Start Time(us)"))
        end = _as_float(row.get("Device End_time(us)") or row.get("Device End Time(us)"))
        if start is None or end is None or end < start:
            continue
        matches.append((_dev(row.get("Device_id")), start, end))
    if not matches:
        return None, ERR_MSTX_RANGE_NOT_FOUND
    if len(set(matches)) != 1:
        return None, ERR_MSTX_RANGE_AMBIGUOUS
    device_id, start, end = matches[0]
    return _window(range_name, device_id, route=ROUTE_CSV, start_us=start, end_us=end,
                   source_rows=len(matches)), None


def _in_window(row, window):
    """归一行是否**完整落在**测量窗内（起止都在窗内；缺时间戳即不算，fail-closed）。

    db 路线全程用**整数纳秒**比较（§9.7 F：csv 的 19 位十进制经 float 会丢 ~0.25us）。
    """
    unit = row.get("unit", "us")
    if unit == "ns" and window.get("start_ns") is not None:
        lo, hi = window.get("start_ns"), window.get("end_ns")
    else:
        lo, hi = window.get("start_us"), window.get("end_us")
        if lo is None and window.get("start_ns") is not None:
            lo, hi = window["start_ns"] / 1000.0, window["end_ns"] / 1000.0
    start, stop = row.get("start"), row.get("end")
    if start is None or stop is None or lo is None or hi is None:
        return False
    return start >= lo and stop <= hi


# ── 类型分类（两套白名单，未知即 fail-closed）──────────────────────────────────────

def classify_task_type(task_type, route):
    """kernel/task 类型 → `compute` / `memcpy` / `unknown`。**两套枚举分路线**（§9.7 B）。

    未知类型一律 `unknown` → 上层 fail-closed，**绝不当成「没有 kernel」静默得 0 us**
    （原设计拿 CSV 那套白名单去比 db 的 `KERNEL_AIVEC`，一个都不中、静默 0，正是这条要堵的）。
    """
    text = str(task_type or "").strip()
    if route == ROUTE_DB:
        if text in DB_MEMCPY_TYPES:
            return KIND_MEMCPY
        if text in DB_DEVICE_KERNEL_TYPES or text.startswith(DB_DEVICE_KERNEL_PREFIX):
            return KIND_COMPUTE
        return KIND_UNKNOWN
    if text in CSV_MEMCPY_TYPES:
        return KIND_MEMCPY
    if text in CSV_DEVICE_KERNEL_TYPES:
        return KIND_COMPUTE
    return KIND_UNKNOWN


# ── 窗内聚合（中位数 × 每次调用启动数）────────────────────────────────────────────

def repeated_breakdown(rows, *, repeat, memcpy_only=False):
    """把**已筛好的窗内归一行**聚成「每次调用」的 per-kernel 明细 → `(breakdown, err)`。

    规则（承参考仓 + §9.7 ✅，逐条）：
      · 某 kernel 的启动数 `< repeat` → **一次性 setup kernel**，按「每次调用都重复」规则**剔除**
        （实测揪出 `preload_stack_16KB` count=1）；
      · 多出的零头（`len % repeat`）从**头部**丢弃（首轮可能含冷启动残留）；
      · `launches_per_invocation = len/repeat` 须为整数，否则序列不自洽 → err（不猜、不取整）；
      · 单次调用耗时 = **repeat 次中位数** × `launches_per_invocation`。
    """
    repeat = max(1, int(repeat))
    buckets: dict[tuple, list] = {}
    for row in rows:
        duration = row.get("duration_us")
        duration = _as_float(duration)
        if duration is None or duration <= 0:
            continue
        name = PATH_DEVICE_MEMCPY_ONLY if memcpy_only else (row.get("name") or "unknown")
        buckets.setdefault((name, row.get("type") or ""), []).append(duration)

    breakdown = []
    for (name, kernel_type), all_times in buckets.items():
        if repeat > 1 and len(all_times) < repeat:
            continue                                   # setup / import 期的一次性 kernel，不属每次调用
        extra = len(all_times) % repeat
        times = all_times[extra:] if repeat > 1 else all_times
        if not times:
            continue
        launches = len(times) / repeat
        if not float(launches).is_integer():
            return None, ERR_INCONSISTENT_SEQUENCE
        launches = int(launches)
        median_launch_us = float(statistics.median(times))
        breakdown.append({
            "kernel_name": name,
            "kernel_type": kernel_type,
            "execution_path": PATH_DEVICE_MEMCPY_ONLY if memcpy_only else PATH_DEVICE_KERNEL,
            "launches_per_invocation": launches,
            "median_launch_us": median_launch_us,
            "invocation_us": median_launch_us * launches,
        })
    breakdown.sort(key=lambda item: (item["kernel_name"], item["kernel_type"]))
    return breakdown, None


def load_task_rows(prof_dir, *, route=None, db_path=None):
    """按路线读 task 行 → `(归一行列表, err)`。db 优先（§9.7 F）。"""
    if route in (None, ROUTE_DB):
        db_path = db_path or find_profiler_db(prof_dir)
        if db_path is not None:
            return read_db_task_rows(db_path)
        if route == ROUTE_DB:
            return None, ERR_NO_PROF_DATA
    rows = _read_rows(prof_dir, _TASK_TIME_CSV_GLOB)
    if rows is None:
        return None, ERR_NO_TASK_TIME_CSV
    return normalize_csv_task_rows(rows), None


def parse_kernel_measurement(prof_dir, *, repeat, measurement_window, route=None):
    """窗内 task 行 → 一次调用的 kernel-only 耗时。

    返回 `{"us","kernel_name","execution_path","breakdown","device_memcpy_only_us",
           "route","observed_task_types","window_wall_us","error"}`。
    · 有计算 kernel → `us` = 各 kernel 单次调用耗时之和，`execution_path=device_kernel`；
    · 窗内出现**未分类 taskType** → `ERR_UNKNOWN_TASK_TYPE`（§9.7 B：绝不静默得 0 us），
      `observed_task_types` 带回类型直方图供下一轮 de-risk 归类；
    · **窗内只有 memcpy** → `us=None`、`device_memcpy_only_us` 记搬运耗时（⚠ 该分支 §9.7 📌 未验证）；
    · 什么都没有 / 缺窗 / 缺产物 → `error`（上层据此归 no_device_kernel_observed 或 execution_failed）。
    """
    empty = {"us": None, "kernel_name": None, "execution_path": None,
             "breakdown": [], "device_memcpy_only_us": None,
             "route": route, "observed_task_types": {}, "window_wall_us": None, "error": None}
    if measurement_window is None:
        return {**empty, "error": ERR_WINDOW_REQUIRED}
    route = route or measurement_window.get("route")
    empty["route"] = route
    empty["window_wall_us"] = measurement_window.get("wall_us")
    rows, err = load_task_rows(prof_dir, route=route,
                               db_path=measurement_window.get("db_path"))
    if err is not None:
        return {**empty, "error": err}
    route = route or (ROUTE_DB if measurement_window.get("db_path") else ROUTE_CSV)
    empty["route"] = route

    window_dev = measurement_window.get("device_id")
    in_window, observed = [], {}
    for row in rows:
        if not _in_window(row, measurement_window):
            continue
        # 多卡（实测 device_count=16）：窗与 task 行的 device 必须对得上；行没带 device 的不排除。
        row_dev = row.get("device_id")
        if window_dev and row_dev and row_dev != window_dev:
            continue
        in_window.append(row)
        observed[row.get("type") or ""] = observed.get(row.get("type") or "", 0) + 1
    empty["observed_task_types"] = observed

    compute, memcpy, unknown = [], [], []
    for row in in_window:
        kind = classify_task_type(row.get("type"), route)
        (compute if kind == KIND_COMPUTE else memcpy if kind == KIND_MEMCPY else unknown).append(row)
    if unknown:
        # §9.7 B：白名单没覆盖到的类型出现在窗里 = 口径缺口，**必须炸**，不许当 0 us 或「没 kernel」。
        return {**empty, "error": ERR_UNKNOWN_TASK_TYPE}

    if compute:
        breakdown, err = repeated_breakdown(compute, repeat=repeat)
        if err is not None:
            return {**empty, "error": err}
        if breakdown:
            total = sum(item["invocation_us"] for item in breakdown)
            name = breakdown[0]["kernel_name"] if len(breakdown) == 1 else "multiple_kernels"
            return {**empty, "us": float(total), "kernel_name": name,
                    "execution_path": PATH_DEVICE_KERNEL, "breakdown": breakdown}
        # 有计算 kernel 行、但全被「一次性 setup」规则剔光 = 没有「每次调用都跑」的 kernel。
        return {**empty, "error": ERR_NO_DEVICE_TASK}

    if memcpy:
        mem, err = repeated_breakdown(memcpy, repeat=repeat, memcpy_only=True)
        if err is not None:
            return {**empty, "error": err}
        if mem:
            return {**empty, "us": None, "kernel_name": PATH_DEVICE_MEMCPY_ONLY,
                    "execution_path": PATH_DEVICE_MEMCPY_ONLY, "breakdown": mem,
                    "device_memcpy_only_us": float(sum(i["invocation_us"] for i in mem))}
    return {**empty, "error": ERR_NO_DEVICE_TASK}


# ── host 搬运证据（hybrid 判定，**仅 baseline 侧用**）──────────────────────────────

def count_tensor_arguments(case):
    """数该 case 的张量参数个数（测量前一次性物化的 H2D 配额）。据 caseset `inputs[]` 字段，op-中立。"""
    inputs = case.get("inputs") if isinstance(case, dict) else None
    return len(inputs) if isinstance(inputs, list) else 0


def parse_host_transfer_evidence(prof_dir, case, *, repeat, materializations=2, db_path=None):
    """找**每次调用都发生**的 host 搬运 → hybrid 判据。db 走 `CANN_API`，csv 走 `api_statistic`。

    张量参数按 `materializations` 次一次性物化计入配额（warmup 一次 + 测量前重新物化一次 = 2）；
    超出配额且 ≥ repeat 次的 `aclrtMemcpy` 才算「重复 host 搬运」。**本函数从不测 CPU 时间。**

    ⚠ 证据源都不在时 `available=False`——**不冒充「已判为非 hybrid」**。该方向漏判只会让 baseline
    偏小 → ratio(=baseline/custom) 偏小 → 对被测更严格，不会造出假达标。
    """
    allowance = count_tensor_arguments(case) * max(1, int(materializations))
    evidence = {"method": "repeated_host_transfer",
                "iterations": max(1, int(repeat)),
                "aclrt_memcpy_count": 0,
                "one_time_allowance": allowance,
                "repeated_host_transfer_count": 0,
                "api_statistic_found": False,
                "available": False,
                "source": None,
                "detected": False}
    total = None
    db_path = db_path or find_profiler_db(prof_dir)
    if db_path is not None:
        total = count_db_api_calls(db_path, "aclrtMemcpy")
        if total is not None:
            evidence["source"] = ROUTE_DB
    if total is None:
        rows = _read_rows(prof_dir, _API_STAT_CSV_GLOB)
        if rows is None:
            evidence["note"] = "无 api_statistic / CANN_API 证据源 → hybrid 未判（不当作已判为非 hybrid）"
            return evidence
        evidence["api_statistic_found"] = True
        evidence["source"] = ROUTE_CSV
        total = 0
        for row in rows:
            if row.get("API Name") != "aclrtMemcpy":
                continue
            value = _as_float(row.get("Count"))
            if value is not None:
                total += int(value)
    repeated = max(0, int(total) - allowance)
    evidence.update({"aclrt_memcpy_count": int(total), "available": True,
                     "repeated_host_transfer_count": repeated,
                     "detected": repeated >= max(1, int(repeat))})
    return evidence


def has_cpu_fallback(text):
    """输出里是否出现 torch_npu 的 host-CPU 回退告警（**唯一可靠信号**，退出 0 不算证据）。"""
    return bool(text) and any(marker in text for marker in CPU_FALLBACK_MARKERS)


# ── 行为分类（五分类）───────────────────────────────────────────────────────────────

def classify_behavior(*, returncode, output, measurement, host_transfer=None,
                      detect_hybrid=False):
    """把一次采集归入五分类之一 → `(behavior, detail)`。**只有 `npu` 才计时。**

    判定顺序（先验后信，承「fallback 哨兵优先于任何已解析的 kernel」）：
      1. `returncode != 0` → execution_failed（进程都没跑成，解析出的东西一律不可信）；
      2. 输出含 CPU-fallback 告警 → cpu_fallback（**先于**解析结果判）；
      3. 解析错误：`no_repeated_device_execution_tasks` → no_device_kernel_observed；
         其余（缺窗 / 缺产物 / **未知 taskType** / 序列不自洽）→ execution_failed（采集/口径失败，
         不是算子行为——§9.7 B 的白名单落空必须落在这一档，不许伪装成「没有 kernel」）；
      4. `execution_path == device_memcpy_only` → no_device_kernel_observed（+ 明细，不计时）；
      5. `detect_hybrid` 且检出重复 host 搬运 → hybrid_host_device（**device-only 计时不完整**，不计时）；
      6. 否则 → npu。
    """
    detail = {"returncode": int(returncode) if returncode is not None else None}
    if measurement is not None:
        detail["execution_path"] = measurement.get("execution_path")
        detail["parse_route"] = measurement.get("route")
        if measurement.get("observed_task_types"):
            detail["observed_task_types"] = measurement["observed_task_types"]
        if measurement.get("window_wall_us") is not None:
            # ⚠ §9.7 E：只作诊断，**绝不是**性能数字（实测 wall 与窗内 kernel 累加差 90 倍）。
            detail["window_wall_us_not_a_perf_number"] = measurement["window_wall_us"]
        if measurement.get("device_memcpy_only_us") is not None:
            detail["device_memcpy_only_us"] = measurement["device_memcpy_only_us"]
        if measurement.get("error"):
            detail["parse_error"] = measurement["error"]
    if host_transfer is not None:
        detail["host_transfer_evidence"] = host_transfer

    if returncode is not None and int(returncode) != 0:
        return BEHAVIOR_FAILED, detail
    if has_cpu_fallback(output):
        detail["cpu_fallback_marker"] = True
        return BEHAVIOR_CPU_FALLBACK, detail
    if measurement is None:
        return BEHAVIOR_FAILED, detail
    err = measurement.get("error")
    if err == ERR_NO_DEVICE_TASK:
        return BEHAVIOR_NO_KERNEL, detail
    if err == ERR_UNKNOWN_TASK_TYPE:
        detail["note"] = ("窗内出现未分类的 taskType → 计时口径有缺口（§9.7 B），"
                          "fail-closed 判采集失败；observed_task_types 待归类")
        return BEHAVIOR_FAILED, detail
    if err:
        return BEHAVIOR_FAILED, detail
    if measurement.get("execution_path") == PATH_DEVICE_MEMCPY_ONLY:
        detail["note"] = "窗内只有搬运类 task（纯 device-copy）——不计入 kernel-only 时间，只报行为"
        return BEHAVIOR_NO_KERNEL, detail
    if detect_hybrid and isinstance(host_transfer, dict):
        if host_transfer.get("detected"):
            detail["note"] = "同一次调用里既有重复 host 搬运又有 device kernel → device-only 计时不完整"
            return BEHAVIOR_HYBRID, detail
        if not host_transfer.get("available"):
            detail["hybrid_evidence_unavailable"] = True
    if measurement.get("us") is None:
        return BEHAVIOR_NO_KERNEL, detail
    return BEHAVIOR_NPU, detail


# ── 采集配置（§9.7 C：双边必须同配置）────────────────────────────────────────────

def collection_config(*, collector, warmup, repeat, ai_core=AI_CORE_PROFILING,
                      profiler_level=PROFILER_LEVEL):
    """一侧的采集配置指纹。`ai_core` 默认 `off`——开着能让数字虚高 2.0~3.75×（§9.7 C 实测）。"""
    return {"collector": collector, "ai_core": ai_core, "profiler_level": profiler_level,
            "warmup": int(warmup), "repeat": int(repeat),
            "timing_scope": TIMING_SCOPE, "kernel_accounting": KERNEL_ACCOUNTING}


def check_collection_config(custom_cfg, baseline_cfg):
    """双边采集配置是否可比 → `None`（可比）或 :data:`BLOCKED_INCOMPARABLE_COLLECTION_CONFIG`。

    比的是 :data:`COMPARED_COLLECTION_KEYS`（ai_core / level / warmup / repeat / scope / 口径）；
    `collector` **不比**——§9.7 C 实测关掉 ai-core 后 msprof CLI 与 torch_npu profiler 三路吻合
    （150~159 us/call），而 baseline 侧本就只能走 torch_npu.profiler（§9.7 A）。
    任一侧缺配置 → 不可比（fail-closed，缺证据不放行）。
    """
    if not isinstance(custom_cfg, dict) or not isinstance(baseline_cfg, dict):
        return BLOCKED_INCOMPARABLE_COLLECTION_CONFIG
    for key in COMPARED_COLLECTION_KEYS:
        if key not in custom_cfg or key not in baseline_cfg:
            return BLOCKED_INCOMPARABLE_COLLECTION_CONFIG
        if custom_cfg[key] != baseline_cfg[key]:
            return BLOCKED_INCOMPARABLE_COLLECTION_CONFIG
    return None


# ── 判据：speedup / 可比性 / scope ────────────────────────────────────────────────

def speedup(baseline_us, custom_us):
    """`speedup = baseline_us / custom_us`（>1 = custom 更快）。任一侧非有限正数 → None（不硬算）。"""
    for value in (baseline_us, custom_us):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if value != value or value in (float("inf"), float("-inf")) or value <= 0:  # NaN/inf/≤0
            return None
    return float(baseline_us) / float(custom_us)


def comparability(custom_path, baseline_path):
    """可比性标注：两侧都是真 device 计算 kernel → `fair`；否则 `indicative`（口径打折，标出来）。"""
    if custom_path == PATH_DEVICE_KERNEL and baseline_path == PATH_DEVICE_KERNEL:
        return COMPARABILITY_FAIR
    return COMPARABILITY_INDICATIVE


def check_timing_scope(custom_scope, baseline_scope):
    """双边 `timing_scope` 必须同为 `kernel_only`；否则 → `BLOCKED_INCOMPARABLE_TIMING_SCOPE`。

    返回 `None`（可比）或挂起码字符串。**任一侧缺失 / 非 kernel_only 都不可比**——
    `None == None` 也不放行（与 `perf_compare` 的 pc-4 同纪律）。
    """
    if custom_scope != TIMING_SCOPE or baseline_scope != TIMING_SCOPE:
        return BLOCKED_INCOMPARABLE_TIMING_SCOPE
    return None


# ── 精度先筛（只对已过精度的 case 测性能）──────────────────────────────────────────

def accuracy_pass_ids(evidence_list):
    """据 evidence 里**已有的** policy+metrics，用 `validator` 的**同一套 judge** 折出「精度过了的 case」。

    ⚠ 这里**不是**另起一套判定：调的就是 `validator._judge_by_policy`（validator 出裁决时用的那个），
    本模块只把它的结果当**排期过滤器**（决定哪些 case 值得上机测性能），最终裁决仍由 run_workflow 里的
    `validator.validate` 产出。多输出按 AND 折叠（任一输出不 pass → 该 case 不测性能）。
    单输出旧 evidence（`precision.metrics`+`policy` 在顶层）向后兼容。
    """
    import validator                                    # 同一套 judge，绝不复制判定逻辑

    passed = set()
    for item in evidence_list or []:
        if not isinstance(item, dict):
            continue
        cid = item.get("case_id")
        prec = item.get("precision")
        if not cid or not isinstance(prec, dict):
            continue
        outputs = prec.get("outputs")
        if not isinstance(outputs, list) or not outputs:
            outputs = [prec] if prec.get("policy") is not None else []
        if not outputs:
            continue
        states = []
        for out in outputs:
            if not isinstance(out, dict):
                states.append("fail")
                continue
            state, _ = validator._judge_by_policy(out.get("policy"), out.get("metrics"))
            states.append(state)
        if states and all(state in ("pass", "na") for state in states):
            passed.add(cid)
    return passed


def select_perf_cases(caseset, accuracy_pass_ids=None):
    """挑要测性能的 case → `(selected_ids, skipped)`。

    · 选取判据 = caseset 的 **`dims` 含「性能」** 字段（与 `perf_compare` 同口径，非按算子名）；
    · `accuracy_pass_ids` 给了就先筛：不在其中的记 `skipped_accuracy_failed`（算错的快不算快）；
      给 `None` = 未做精度前筛（调用方自负，记 `accuracy_filter="not_applied"`）。
    """
    cases = (caseset or {}).get("cases") or []
    selected, skipped = [], []
    for case in cases:
        if not isinstance(case, dict) or not case.get("id"):
            continue
        if "性能" not in (case.get("dims") or []):
            continue
        cid = case["id"]
        if accuracy_pass_ids is not None and cid not in accuracy_pass_ids:
            skipped.append({"case_id": cid, "reason": SKIPPED_ACCURACY_FAILED})
            continue
        selected.append(cid)
    return selected, skipped


# ── 记录组装 → evidence perf / _torch_npu_baseline.json ────────────────────────────

def build_perf_record(case_id, custom, baseline):
    """把一个 case 的双边采集结果合成一条记录（**只描述、不裁决**）。

    `custom` / `baseline` = `{"behavior","us","scope","execution_path","collection","detail"}`。
    产出含 `timing_scope_status` + `collection_status`（两道可比性闸）、`comparability`、
    `speedup`（**仅双边可计时且两道闸都过**时才算）。
    """
    record = {"case_id": case_id,
              "custom": dict(custom or {}),
              "baseline": dict(baseline or {})}
    c_timed = (custom or {}).get("behavior") in TIMED_BEHAVIORS
    b_timed = (baseline or {}).get("behavior") in TIMED_BEHAVIORS
    record["custom_timed"] = bool(c_timed)
    record["baseline_timed"] = bool(b_timed)
    if not (c_timed and b_timed):
        record["speedup"] = None
        record["comparability"] = None
        record["timing_scope_status"] = None
        record["collection_status"] = None
        record["note"] = ("双边未同时产生可计时的 device kernel → 只报行为、不算比值"
                          f"（custom={record['custom'].get('behavior')}, "
                          f"baseline={record['baseline'].get('behavior')}）")
        return record
    scope_status = check_timing_scope((custom or {}).get("scope"), (baseline or {}).get("scope"))
    record["timing_scope_status"] = scope_status
    if scope_status is not None:
        record["speedup"] = None
        record["comparability"] = None
        record["collection_status"] = None
        record["note"] = (f"{scope_status}: custom_scope={custom.get('scope')!r} "
                          f"baseline_scope={baseline.get('scope')!r}（双边须同为 {TIMING_SCOPE}）")
        return record
    coll_status = check_collection_config(custom.get("collection"), baseline.get("collection"))
    record["collection_status"] = coll_status
    if coll_status is not None:
        record["speedup"] = None
        record["comparability"] = None
        record["note"] = (f"{coll_status}: custom_collection={custom.get('collection')!r} "
                          f"baseline_collection={baseline.get('collection')!r}"
                          "（§9.7 C：ai-core 开关等不一致能差 2.0~3.75×，一律不比）")
        return record
    record["speedup"] = speedup(baseline.get("us"), custom.get("us"))
    record["comparability"] = comparability(custom.get("execution_path"),
                                            baseline.get("execution_path"))
    return record


def build_custom_perf_map(records, skipped=None):
    """records → `{case_id: {"scope","us","note",...}}`，供 evidence `perf` 字段。

    未计时的 case `us=None` + note 写明行为（**绝不填 0、不填估计值**）。
    """
    out = {}
    for record in records or []:
        cid = record.get("case_id")
        if not cid:
            continue
        custom = record.get("custom") or {}
        timed = custom.get("behavior") in TIMED_BEHAVIORS
        entry = {"scope": TIMING_SCOPE if timed else custom.get("scope") or TIMING_SCOPE,
                 "us": float(custom["us"]) if timed and custom.get("us") is not None else None,
                 "behavior": custom.get("behavior"),
                 "execution_path": custom.get("execution_path")}
        if not timed:
            entry["note"] = f"custom 侧未产生可计时的 device kernel（behavior={custom.get('behavior')}）"
        out[cid] = entry
    for item in skipped or []:
        cid = item.get("case_id")
        if cid:
            out[cid] = {"scope": TIMING_SCOPE, "us": None,
                        "behavior": None, "execution_path": None,
                        "note": item.get("reason") or SKIPPED_ACCURACY_FAILED}
    return out


def build_baseline_document(records, *, op=None, warmup=DEFAULT_WARMUP, repeat=DEFAULT_REPEAT,
                            skipped=None):
    """records → `_torch_npu_baseline.json` 文档（`repo_adapter.parse_torch_npu_baseline` 的输入）。

    **只有 baseline 行为 = `npu` 的 case 进 `per_case`**；其余进 `excluded`（带行为原因），
    于是 perf_compare 那边自然「缺基线 → blocked」，**不会拿非 device 数据冒充基线**。
    """
    per_case, excluded = [], []
    for record in records or []:
        cid = record.get("case_id")
        baseline = record.get("baseline") or {}
        behavior = baseline.get("behavior")
        if not cid:
            continue
        if behavior in TIMED_BEHAVIORS and baseline.get("us") is not None \
                and baseline.get("scope") == TIMING_SCOPE:
            per_case.append({"case_id": cid, "us": float(baseline["us"]),
                             "env": "torch_npu profiler(mstx)",
                             "execution_path": baseline.get("execution_path")})
        else:
            excluded.append({"case_id": cid, "behavior": behavior,
                             "reason": baseline.get("note") or "baseline 未产生可计时 device kernel"})
    for item in skipped or []:
        excluded.append({"case_id": item.get("case_id"),
                         "behavior": None,
                         "reason": item.get("reason") or SKIPPED_ACCURACY_FAILED})
    return {"source": "torch_npu", "scope": TIMING_SCOPE, "op": op,
            "per_case": per_case, "excluded": excluded,
            "collection": {"tool": COLLECTOR_TORCH_PROFILER,
                           "warmup": int(warmup), "repeat": int(repeat),
                           "ai_core": AI_CORE_PROFILING,
                           "profiler_level": PROFILER_LEVEL,
                           "kernel_types_csv_route": sorted(CSV_DEVICE_KERNEL_TYPES),
                           "kernel_types_db_route": sorted(DB_DEVICE_KERNEL_TYPES)
                           + [DB_DEVICE_KERNEL_PREFIX + "*"],
                           "memcpy_excluded": DEVICE_MEMCPY_TYPE,
                           "memcpy_rule_status": "未验证（§9.7 📌：Level0 下未见 memcpy TASK 行）",
                           "window": "mstx_range"}}


# ── baseline 侧 torch 调用计划（spec `perf.torch_baseline` 声明，slot-name 驱动）────────

def resolve_torch_baseline_plan(torch_baseline, call):
    """据 spec `perf.torch_baseline` 把该 case **已解析好的** `aclnn_call.slots` 翻成 torch 调用计划。

    契约（字段驱动、op-中立）::

        "torch_baseline": {"api": "torch.median",
                           "positional": ["self"],
                           "keyword": {"dim": "dim", "keepdim": "keepdim"}}

    · `positional` 列的是 **slot name**（与 aclnn 头签名同名），按列出顺序作 torch 位置参数；
      **缺任一即 fail-closed**（不猜、不重排）。
    · `keyword` 是 `slot name -> torch 形参名`；**该 case 没有这个 slot 就自然缺席**——
      变体（如全局 median 无 `dim`）由此自动跟随，**不得为此写任何算子分支**。
    · out / out_null slot 一律忽略（torch 侧输出是返回值）。

    返回 `{"api", "positional": [slot...], "keyword": {torch_kwarg: slot}}`（slot 为原始 slot dict）。
    """
    if not isinstance(torch_baseline, dict):
        raise PerfCollectError(
            "spec 缺 perf.torch_baseline —— torch_npu 基线的调用映射须由 spec 声明"
            "（{'api','positional','keyword'}），本模块不猜 torch 形参（fail-closed）")
    api = torch_baseline.get("api")
    if not isinstance(api, str) or not api.startswith("torch."):
        raise PerfCollectError(f"perf.torch_baseline.api 须是 'torch.*' 点路径，得 {api!r}")
    slots = (call or {}).get("slots") or []
    by_name = {}
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        role, name = slot.get("role"), slot.get("name")
        if role in ("in", "attr") and name:
            if name in by_name:
                raise PerfCollectError(f"aclnn_call.slots 有重名 slot {name!r} —— 映射不唯一，fail-closed")
            by_name[name] = slot
    positional = []
    for name in torch_baseline.get("positional") or []:
        slot = by_name.get(name)
        if slot is None:
            raise PerfCollectError(
                f"perf.torch_baseline.positional 要 slot {name!r}，但本 case 的 aclnn_call 没有它——fail-closed")
        positional.append(slot)
    keyword = {}
    for name, kwarg in (torch_baseline.get("keyword") or {}).items():
        slot = by_name.get(name)
        if slot is None:
            continue                                    # 该变体没有这个属性 → torch 侧自然缺席
        keyword[str(kwarg)] = slot
    return {"api": api, "positional": positional, "keyword": keyword}


# ══ 以下为真机采集（gated：OPRUNWAY_ACLNN_REAL=1）══════════════════════════════════

def _require_real_gate():
    if os.environ.get("OPRUNWAY_ACLNN_REAL") != "1":
        raise PerfCollectError(
            "真机性能采集未启用——须 OPRUNWAY_ACLNN_REAL=1（同 aclnn_adapter 的真机 gate）。"
            "离线只提供解析 / 聚合 / 分类 / speedup / scope / 采集配置校验（可单测）。")


def range_name_for(case_id, side):
    """MSTX range 名（每 (case, side) 唯一；只含安全字符——case_id 已过 `_check_id`）。"""
    return f"oprunway_perf_{side}_{case_id}"


def runtime_root():
    """`aclnn_runtime` 包的父目录——wrapper 以脚本方式跑时 sys.path[0] 是脚本目录，必须显式补这个根。"""
    return str(Path(__file__).resolve().parent.parent)


_CUSTOM_WRAPPER = r'''# OpRunway perf wrapper · custom(ctypes-aclnn) —— 由 perf_msprof 生成，勿手改
# ⚠ §9.7「下一个待 de-risk」：ctypes runner 侧能否打出 MSTX **尚未实测**（Python/torch 侧才坐实）。
#   CANN mstx C API 在 tools/mstx/include/mstx/ms_tools_ext.h + lib64/mstx.so；打不出即 rid=0 → 直接抛，
#   **绝不静默拿整进程 kernel 当测量窗**（§9.7 A：MSTX 的失败是静默的）。
import ctypes, json, sys
from pathlib import Path

CFG = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
sys.path.insert(0, CFG["runtime_root"])

from aclnn_runtime import aclnn_driver as D
from aclnn_runtime.aclnn_runner import AclnnRunner

caseset = json.loads(Path(CFG["caseset"]).read_text(encoding="utf-8"))
case = next(c for c in caseset["cases"] if c["id"] == CFG["case_id"])
call = D._case_call(case)
resolver = D._SignatureResolver(op_dir=CFG.get("op_dir"))
signature = resolver.get(call["symbol"])
runner = AclnnRunner(device=int(CFG["device"]))
runner._ensure_init()   # 先建好 device/stream：warmup=0 时 MSTX 也得拿到真 stream，不能圈到 NULL 上

def invoke():
    # 每次调用重新组 slots = 重新物化新鲜输入（承 runner form 语义：H2D/D2H 属 runner 固有开销，
    # 由 kernel 类型白名单排除在 kernel-only 口径之外）。
    slots = D._build_slots(call, case, CFG["work_dir"])
    runner.run(call["symbol"], slots, signature=signature)

print("%sWARMUP_START" % CFG["marker_phase"], flush=True)
for _ in range(max(0, int(CFG["warmup"]))):
    invoke()
print("%sWARMUP_DONE" % CFG["marker_phase"], flush=True)

mstx = None
for lib in ("libms_tools_ext.so", "libmstx.so", "mstx.so"):
    try:
        mstx = ctypes.CDLL(lib)
        break
    except OSError:
        continue
if mstx is None:
    raise RuntimeError("device MSTX library not loadable (libms_tools_ext.so / mstx.so)")
mstx.mstxRangeStartA.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
mstx.mstxRangeStartA.restype = ctypes.c_uint64
mstx.mstxRangeEnd.argtypes = [ctypes.c_uint64]
mstx.mstxRangeEnd.restype = None
range_id = mstx.mstxRangeStartA(CFG["range_name"].encode("utf-8"),
                                ctypes.c_void_p(runner._stream.value if runner._stream else None))
if not range_id:
    raise RuntimeError("failed to start device MSTX measurement range (rid=0)")
print("%sMEASURE_START" % CFG["marker_phase"], flush=True)
try:
    for _ in range(max(1, int(CFG["repeat"]))):
        invoke()
finally:
    mstx.mstxRangeEnd(range_id)
print("%sMEASURE_DONE" % CFG["marker_phase"], flush=True)
print(CFG["marker_devices"] + json.dumps(["npu:%d" % int(CFG["device"])]), flush=True)
'''


_BASELINE_WRAPPER = r'''# OpRunway perf wrapper · baseline(torch_npu.profiler + MSTX) —— 由 perf_msprof 生成，勿手改
# §9.7 A：msprof CLI 下 Python 侧 MSTX 静默失败（range_start 恒 0、异常被 @_no_exception_func 吞），
#   CANN 原生 `import mstx` 进程挂死 → **基线侧唯一成立的采集入口是 torch_npu.profiler**
#   （experimental_config=_ExperimentalConfig(mstx=True)，导出 db 取 MSTX_EVENTS/TASK/COMPUTE_TASK_INFO）。
# §9.7 C：不开 ai_core detail（aic_metrics 保持默认 None）——开着能让数字虚高 2.0~3.75×。
import json, sys
from pathlib import Path

import numpy as np
import torch
import torch_npu  # noqa: F401

CFG = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
sys.path.insert(0, CFG["runtime_root"])

from aclnn_runtime import aclnn_driver as D
from aclnn_runtime import perf_msprof as P

caseset = json.loads(Path(CFG["caseset"]).read_text(encoding="utf-8"))
case = next(c for c in caseset["cases"] if c["id"] == CFG["case_id"])
call = D._case_call(case)
plan = P.resolve_torch_baseline_plan(CFG["torch_baseline"], call)
dev_index = int(CFG["device"])          # 多卡（实测 device_count=16）：device 由 plan 显式给，绝不假定 0
torch.npu.set_device(dev_index)
device = "npu:%d" % dev_index

def resolve_fn(api):
    value = torch
    for part in api.split(".")[1:]:
        value = getattr(value, part, None)
        if value is None:
            raise RuntimeError("torch API not found: %s" % api)
    if not callable(value):
        raise RuntimeError("torch API is not callable: %s" % api)
    return value

fn = resolve_fn(plan["api"])
inputs = case.get("inputs") or []

def to_tensor(slot):
    rec = inputs[int(slot["input_idx"])]
    arr, logical = D._load_input(CFG["work_dir"], rec)
    arr = np.ascontiguousarray(arr)
    if logical == "bfloat16":
        # 盘上是 bf16 的 uint16 位模式 → 按位重解释，**绝不做数值转换**（那会换掉被测数据）。
        t = torch.frombuffer(bytearray(arr.tobytes()), dtype=torch.bfloat16)
        t = t.reshape(tuple(int(d) for d in (rec.get("shape") or arr.shape)))
    else:
        t = torch.from_numpy(arr)
    return t.to(device)

def materialize():
    args = [to_tensor(s) if s.get("role") == "in" else s.get("value")
            for s in plan["positional"]]
    kwargs = {}
    for kwarg, slot in plan["keyword"].items():
        kwargs[kwarg] = (to_tensor(slot) if slot.get("role") == "in" else slot.get("value"))
    return args, kwargs

def invoke(args, kwargs):
    with torch.no_grad():
        out = fn(*args, **kwargs)
    torch.npu.synchronize()
    return out

prof_dir = CFG["prof_dir"]
Path(prof_dir).mkdir(parents=True, exist_ok=True)
exp_kwargs = {"mstx": True}
if hasattr(torch_npu.profiler, "ExportType"):
    exp_kwargs["export_type"] = torch_npu.profiler.ExportType.Db     # 产 ascend_pytorch_profiler*.db
if hasattr(torch_npu.profiler, "ProfilerLevel"):
    exp_kwargs["profiler_level"] = torch_npu.profiler.ProfilerLevel.Level0
experimental = torch_npu.profiler._ExperimentalConfig(**exp_kwargs)
activities = [torch_npu.profiler.ProfilerActivity.CPU, torch_npu.profiler.ProfilerActivity.NPU]

last = None
with torch_npu.profiler.profile(
        activities=activities,
        experimental_config=experimental,
        on_trace_ready=torch_npu.profiler.tensorboard_trace_handler(prof_dir)):
    print("%sWARMUP_START" % CFG["marker_phase"], flush=True)
    args, kwargs = materialize()
    for _ in range(max(0, int(CFG["warmup"]))):
        last = invoke(args, kwargs)
    print("%sWARMUP_DONE" % CFG["marker_phase"], flush=True)

    # 测量前**重新物化新鲜输入**：in-place / 有状态算子不得把 warmup 的改动带进被测窗。
    args, kwargs = materialize()
    torch.npu.synchronize()
    range_id = torch_npu.npu.mstx.range_start(CFG["range_name"], torch.npu.current_stream())
    if not range_id:
        # §9.7 A：mstx 的失败是**静默**的（rid=0）。缺窗即 fail-closed，绝不拿整进程 kernel 当测量窗。
        raise RuntimeError("mstx.range_start returned 0 —— MSTX 未生效，测量窗不可信")
    print("%sMEASURE_START" % CFG["marker_phase"], flush=True)
    try:
        for _ in range(max(1, int(CFG["repeat"]))):
            last = invoke(args, kwargs)
    finally:
        torch_npu.npu.mstx.range_end(range_id)
    print("%sMEASURE_DONE" % CFG["marker_phase"], flush=True)

def devices(value):
    if hasattr(value, "device"):
        return [str(value.device)]
    if isinstance(value, (list, tuple)):
        return [d for item in value for d in devices(item)]
    if isinstance(value, dict):
        return [d for item in value.values() for d in devices(item)]
    return []

print(CFG["marker_prof_dir"] + prof_dir, flush=True)
print(CFG["marker_devices"] + json.dumps(sorted(set(devices(last)))), flush=True)
'''


def _run_msprof(wrapper_path, cfg_path, out_dir):
    """跑一轮 msprof CLI（**必带 `--ai-core=off`**，§9.7 C）。返回 `(prof_dir|None, rc, output, cmd)`。

    ⚠ `--ai-core` 默认 on 会让 Sort(MIX_AIV) 虚高 3.75×、每次调用总和虚高 2.0×——这行参数不是可选项。
    """
    os.makedirs(out_dir, exist_ok=True)
    cmd = ["msprof", f"--output={out_dir}", *MSPROF_EXTRA_ARGS,
           sys.executable, str(wrapper_path), str(cfg_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    output = (proc.stdout or "") + (proc.stderr or "")
    profs = sorted(Path(out_dir).glob("PROF_*"))
    return (str(profs[-1]) if profs else str(out_dir)), proc.returncode, output, cmd


def _run_torch_profiler(wrapper_path, cfg_path, out_dir):
    """直接跑 wrapper（profiling 由 wrapper 内的 `torch_npu.profiler` 负责，**不套 msprof CLI**）。"""
    os.makedirs(out_dir, exist_ok=True)
    cmd = [sys.executable, str(wrapper_path), str(cfg_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    output = (proc.stdout or "") + (proc.stderr or "")
    return str(out_dir), proc.returncode, output, cmd


def collector_for(side):
    """side → 采集入口。baseline 侧**只能** torch_npu.profiler（§9.7 A）；custom 侧走 msprof CLI。"""
    return COLLECTOR_MSPROF_CLI if side == "custom" else COLLECTOR_TORCH_PROFILER


def _keep_prof():
    """根盘只剩 41G（§9.7 环境更正）→ 解析完即删 prof 产物；`OPRUNWAY_PERF_KEEP_PROF=1` 可保留。"""
    return os.environ.get("OPRUNWAY_PERF_KEEP_PROF") == "1"


def measure_side(*, side, case, caseset_path, work_dir, cfg_extra, warmup, repeat, device,
                 scratch_dir, detect_hybrid, collector=None):
    """采集一侧（custom / baseline）一个 case → `{"behavior","us","scope","execution_path","collection",...}`。

    流程：生成 wrapper + cfg → 按 side 选采集入口跑 → 解析 MSTX 窗 → 窗内 kernel 聚合 → 五分类 → 清产物。
    任一步失败一律落成 behavior（`execution_failed` / `no_device_kernel_observed`），**绝不返回编的数**。
    """
    _require_real_gate()
    cid = case["id"]
    collector = collector or collector_for(side)
    range_name = range_name_for(cid, side)
    template = _CUSTOM_WRAPPER if side == "custom" else _BASELINE_WRAPPER
    side_dir = Path(scratch_dir) / f"{side}-{cid}"
    side_dir.mkdir(parents=True, exist_ok=True)
    wrapper_path = side_dir / "_wrapper.py"
    wrapper_path.write_text(template, encoding="utf-8")
    prof_root = side_dir / "prof"
    cfg = {"caseset": str(caseset_path), "case_id": cid, "work_dir": str(work_dir),
           "warmup": int(warmup), "repeat": int(repeat), "device": int(device),
           "range_name": range_name, "runtime_root": runtime_root(),
           "prof_dir": str(prof_root),
           "marker_phase": MARKER_PHASE, "marker_devices": MARKER_OUTPUT_DEVICES,
           "marker_prof_dir": MARKER_PROF_DIR}
    cfg.update(cfg_extra or {})
    cfg_path = side_dir / "_cfg.json"
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")

    if collector == COLLECTOR_MSPROF_CLI:
        prof_dir, returncode, output, command = _run_msprof(wrapper_path, cfg_path, prof_root)
    else:
        prof_dir, returncode, output, command = _run_torch_profiler(wrapper_path, cfg_path,
                                                                    prof_root)
    measurement = None
    host_transfer = None
    if prof_dir is not None:
        window, window_err = parse_measurement_window(prof_dir, range_name)
        if window_err is not None:
            measurement = {"us": None, "kernel_name": None, "execution_path": None,
                           "breakdown": [], "device_memcpy_only_us": None,
                           "route": None, "observed_task_types": {}, "window_wall_us": None,
                           "error": window_err}
        else:
            measurement = parse_kernel_measurement(prof_dir, repeat=repeat,
                                                   measurement_window=window)
        if detect_hybrid:
            host_transfer = parse_host_transfer_evidence(
                prof_dir, case, repeat=max(0, int(warmup)) + max(1, int(repeat)))
    behavior, detail = classify_behavior(returncode=returncode, output=output,
                                         measurement=measurement, host_transfer=host_transfer,
                                         detect_hybrid=detect_hybrid)
    detail["command"] = command
    detail["prof_dir"] = prof_dir
    detail["collector"] = collector
    detail["output_tail"] = (output or "")[-1500:]
    if not _keep_prof():
        shutil.rmtree(prof_root, ignore_errors=True)     # 根盘仅剩 41G，别堆 profiling 产物
        detail["prof_dir_removed"] = True
    timed = behavior in TIMED_BEHAVIORS
    return {"behavior": behavior,
            "us": (measurement or {}).get("us") if timed else None,
            "scope": TIMING_SCOPE if timed else None,
            "execution_path": (measurement or {}).get("execution_path"),
            "kernel_name": (measurement or {}).get("kernel_name"),
            "breakdown": (measurement or {}).get("breakdown") or [],
            "collection": collection_config(collector=collector, warmup=warmup, repeat=repeat),
            "detail": detail}


def collect(caseset_path, work_dir, plan, out_path, *, scratch_dir=None):
    """容器内主入口：按 plan 逐 case 采双边 → 落 `perf_collect.json`（**只有数与行为，无裁决**）。

    `plan`（由 OpRunway 侧据 spec 组好、随部署上送）::

        {"op": "<Op>", "warmup": 5, "repeat": 20, "device": 0,
         "op_dir": "<aclnn 头目录>",              # 可选，缺省走 driver 的 env 探测
         "torch_baseline": {"api","positional","keyword"},
         "cases": ["<case id>", ...],             # 已过精度先筛的 case
         "skipped": [{"case_id","reason"}]}

    ⚠ `device` **必须显式给**：容器内 `device_count()=16`（§9.7 环境更正），默认 0 就是在猜卡。
    """
    _require_real_gate()
    caseset = json.loads(Path(caseset_path).read_text(encoding="utf-8"))
    by_id = {c["id"]: c for c in caseset.get("cases", []) if isinstance(c, dict) and c.get("id")}
    warmup = int(plan.get("warmup", DEFAULT_WARMUP))
    repeat = int(plan.get("repeat", DEFAULT_REPEAT))
    if plan.get("device") is None:
        raise PerfCollectError(
            "perf plan 缺 device —— 容器内 device_count=16（§9.7），采集卡号不许默认/猜（fail-closed）")
    device = int(plan["device"])
    torch_baseline = plan.get("torch_baseline")
    scratch = scratch_dir or tempfile.mkdtemp(prefix="oprunway-perf-")
    records = []
    for cid in plan.get("cases") or []:
        case = by_id.get(cid)
        if case is None:
            raise PerfCollectError(f"plan 里的 case_id={cid!r} 不在 caseset 中——fail-closed")
        custom = measure_side(side="custom", case=case, caseset_path=caseset_path,
                              work_dir=work_dir,
                              cfg_extra={"op_dir": plan.get("op_dir")},
                              warmup=warmup, repeat=repeat, device=device,
                              scratch_dir=scratch, detect_hybrid=False)
        baseline = measure_side(side="baseline", case=case, caseset_path=caseset_path,
                                work_dir=work_dir,
                                cfg_extra={"torch_baseline": torch_baseline},
                                warmup=warmup, repeat=repeat, device=device,
                                scratch_dir=scratch, detect_hybrid=True)
        records.append(build_perf_record(cid, custom, baseline))
    doc = {"op": plan.get("op") or caseset.get("op"),
           "scope": TIMING_SCOPE, "warmup": warmup, "repeat": repeat, "device": device,
           "collection": {"custom": collection_config(collector=collector_for("custom"),
                                                      warmup=warmup, repeat=repeat),
                          "baseline": collection_config(collector=collector_for("baseline"),
                                                        warmup=warmup, repeat=repeat)},
           "records": records, "skipped": plan.get("skipped") or []}
    Path(out_path).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return doc


def main(argv=None):
    parser = __import__("argparse").ArgumentParser(
        description="perf_msprof：容器内 kernel-only 性能采集（custom ctypes-aclnn vs torch_npu 基线）")
    parser.add_argument("caseset", help="caseset.json 路径")
    parser.add_argument("plan", help="perf_plan.json 路径（op/warmup/repeat/device/torch_baseline/cases）")
    parser.add_argument("out", help="输出 perf_collect.json 路径")
    parser.add_argument("--work-dir", default=None, help="输入张量根目录（缺省 = caseset 所在目录）")
    args = parser.parse_args(argv)
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    work_dir = args.work_dir or str(Path(args.caseset).resolve().parent)
    doc = collect(args.caseset, work_dir, plan, args.out)
    print(json.dumps({"op": doc["op"], "records": len(doc["records"]),
                      "skipped": len(doc["skipped"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
