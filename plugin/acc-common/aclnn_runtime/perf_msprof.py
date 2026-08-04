"""perf_msprof — kernel-only 性能采集（MSTX 测量窗 + 窗内 kernel 累加），**op-中立、字段驱动**。

⚠ **本模块先按 `dev-doc/oprunway-torch-baseline-design.md` §9.7（2026-07-24）返工，又据
2026-07-26 cannbot 精确对标真机探针更正采集入口**：

* **A（采集入口·已更正）**：旧探针只证明 `torch_npu.npu.mstx.range_start()` 在 msprof CLI
  下静默返回 `rid=0`，以及 CANN Python `mstx` 模块会挂；它**没有测试**参考仓实际使用的
  `ctypes.CDLL("libms_tools_ext.so").mstxRangeStartA(...)`。2026-07-26 在同一 A3 /
  CANN 9.0.1 / torch_npu 2.10 容器用 cannbot 同款 C API 真机复验：
  `range_id=1`，`msprof_tx_*.csv` 与 `task_time_*.csv` 均产出，参考仓原始解析器得到
  `144.481 us/call`、10 个 kernel、`parse_error=null`。
  → **live custom 与 baseline 统一走 ctypes-MSTX + msprof CLI + CSV**；`measure_side`
  显式钉 `ROUTE_CSV`，即使 PROF 目录同时带 db 也不再误选数值 `TASK.taskType`。
  DB 解析保留用于历史工件/离线诊断，**不再是 live collector 主路**。
* **B（kernel 白名单两套）**：CSV 路线（`task_time.kernel_type` / `op_summary.Task Type`）=
  :data:`CSV_DEVICE_KERNEL_TYPES`；**db 路线（`TASK.taskType`）是 `KERNEL_AIVEC`/`KERNEL_MIX_AIV`/…**
  （:data:`DB_DEVICE_KERNEL_TYPES` + `KERNEL_` 家族规则），用 CSV 那套**一个都匹配不上 → 静默得 0 us**。
  CSV 窗内真机观察到的 `PROFILER_TRACE_EX` 是 msprof 自身控制任务，列入
  :data:`CSV_CONTROL_TASK_TYPES` 后只留痕、不计时；其余未知类型仍 fail-closed。
  → 窗内出现**任何未分类的 taskType 一律 fail-closed**（:data:`ERR_UNKNOWN_TASK_TYPE`，并把观察到的
  类型直方图带进 detail），**绝不让空结果冒充「没有 kernel」**。
* **B′（`TASK.taskType` 可能是数值枚举 id）**——2026-07-24 a3 真机 dogfood 实测补充：
  CANN 9.0.1 + torch_npu 2.10 的 profiler db 里 `TASK.taskType` **不是字符串而是数值 id**
  （custom 侧 `15/17/19/20/24`、baseline 侧 `10~33`），拿 `KERNEL_*` 前缀去比**一个都不中**
  → 双边 46/46 `unknown_task_type_in_window`，db 路线整个不可用（MSTX 窗本身两侧都成立，卡的只是归类）。
  → **先 join db 自带的字典表**把 id 解回名字（:func:`task_type_dictionary`：专用枚举表 →
  **有 schema 外键证据时**才轮到 `STRING_IDS`），拿不到字典表才退到外部传入的**带结构化 provenance**
  映射（:data:`ENV_TASK_TYPE_MAP`）。
  **解不出的 id 一律照 unknown 处理 → fail-closed**（显示成 `taskType_id:<id>`，永不可能误撞白名单），
  并把 `unresolved_task_type_ids` 带进 detail 供下一轮取字典。
* **B″（字典解析本身能被污染 → 三道闸，2026-07-24 审计高危 #2/#3）**：id→名的解析一旦解错，
  产出的就不是「拿不到数」而是**看着合法、实则编造的 us 数字**——对验收工具这比拿不到数严重得多。
  故 :func:`task_type_dictionary` 只认**有据可查**的字典：
  1. **专用枚举表**（表名按 :data:`_TASK_TYPE_DICT_TABLE_RE` 通用探测）＝ db 为 taskType 专设的表，可信；
  2. **`STRING_IDS` 是通用字符串池、默认不认**——只有 db schema 里**声明了外键**
     （`PRAGMA foreign_key_list(TASK)` 有 `taskType → STRING_IDS`）才证明「taskType 的确以它为字典」，
     才允许兜底；且该 id **不得与 kernel-name / API-name / MSTX-message 池重合**
     （:func:`string_id_name_pool`：同一个数字既被当名字引用又当类型 id，根本分不清 → 不认）。
     **没有外键证据 → 一律保持 unresolved（fail-closed），绝不靠「长得像枚举」的正则外形放行**
     （光看外形时，`STRING_IDS[15]="KERNEL_STALE_NAME"` 这种纯 ID 碰撞就能造出假的 kernel-only 数字）。
  3. **外部 override**（:data:`ENV_TASK_TYPE_MAP`）＝ 人手写的，闸最紧：`provenance` 须**结构化**
     且 :data:`OVERRIDE_PROVENANCE_FIELDS` 逐字段必填（db / CANN 版本 / torch_npu 版本 / 采集命令 /
     采集日期），占位串（空、纯符号、`TODO`/`占位`…）一律拒；映射值**只许落在版本化受控枚举**
     :data:`CONTROLLED_TASK_TYPE_NAMES` 内，**未知 `KERNEL_*` 不许靠前缀放行**；任一条不合格 → **整份拒收**。
  另：**多来源之间冲突即拒**（同一 id 被两个来源解成不同名字 → 该 id 直接丢弃、不做「后者覆盖前者」）。
  「凭什么信这份字典」全程记进 `task_type_dict_provenance`，随 detail 出证。
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
* **F（CSV 时间戳两个坑）**：`Task Start Time(us)` 带**尾随 tab**（解析器统一 strip）；
  19 位十进制经 float 有亚微秒量级精度损失。live 路线仍按 cannbot 使用 CSV，但 MSTX range
  在首个被测 task 前开启、末次同步后关闭，不以边界 wall duration 计时；缺窗/歧义仍 fail-closed。

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
import re
import signal
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

# ── 常量（单一真源）────────────────────────────────────────────────────────────────

#: 解析路线。live collector 显式使用 :data:`ROUTE_CSV`（双侧统一 msprof CLI，2026-07-26
#: 真机坐实）；:data:`ROUTE_DB` 仅保留给历史工件/离线诊断。未显式指定 route 的公共解析函数
#: 仍保持「db 可得则优先」的向后兼容行为，live 调用点不得依赖该默认值。
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

# —— §9.7 B′：`TASK.taskType` 的**数值枚举 id** → 名字（实测 CANN 9.0.1 + torch_npu 2.10 就是数值）——
#: `TASK` 表里放类型的列，**优先本来就是字符串的那列**，最后才落到可能为数值 id 的 `taskType`。
_TASK_TYPE_COLUMNS = ("taskTypeName", "taskTypeStr", "taskType")
#: db 自带字典表的**通用**表名探测（不写死某个版本的表名）：`TASK_TYPE` / `TASK_TYPES` / `ENUM_TASK_TYPE`…
_TASK_TYPE_DICT_TABLE_RE = re.compile(r"^(ENUM_)?TASK_?TYPES?$", re.IGNORECASE)
#: 从**通用**字符串池（`STRING_IDS`）取值时的**外形**闸：只收长得像类型枚举的全大写 token
#: （`KERNEL_AIVEC` / `MEMCPY_ASYNC` ✓；`aclnnMedian_Median_Median` / `preload_stack_16KB` ✗）。
#: ⚠ **外形只是最后一道、绝非许可证**——它证明不了「这个数字是 taskType 的字典 key」（审计高危 #2：
#: `STRING_IDS[15]="KERNEL_STALE_NAME"` 纯 ID 碰撞就能骗过外形闸、造出假 us）。用它前必须先过
#: :func:`string_ids_is_task_type_dictionary` 的**外键证据**闸。
_TASK_TYPE_TOKEN_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
#: 解不出的数值 id 的显示前缀——**故意不像任何真类型名**，保证永不可能误撞白名单。
UNRESOLVED_TASK_TYPE_PREFIX = "taskType_id:"
#: 字典来源标签（进 detail，便于说清「这次是靠哪张表解出来的」）。
DICT_SOURCE_TABLE = "db_task_type_table"
DICT_SOURCE_STRING_IDS = "db_string_ids"
DICT_SOURCE_ENV = "env_override"

# —— 受控枚举（审计高危 #3）：字典**只许解出**这些名字，未知 `KERNEL_*` 不靠前缀放行 ——
#: 受控枚举的版本号。**扩这份枚举必须带真机实测依据并升版本**，且升版本要连带更新
#: :data:`CONTROLLED_TASK_TYPE_NAMES_PROVENANCE`——否则就是拿「猜的名字」换「假的性能数」。
TASK_TYPE_ENUM_VERSION = "2026-07-24.1"
CONTROLLED_TASK_TYPE_NAMES_PROVENANCE = (
    "受控枚举依据 = §9.7 B 实测坐实的 db 路线计算 kernel 类型（KERNEL_AIVEC / KERNEL_MIX_AIV）"
    "+ 搬运类型集合。"
    "只有落在这份枚举里的名字才允许由**外部 override** 解出——手写映射的拼写错误"
    "（如 KERNEL_TYPO）会让 fail-closed 变成假性能数字，故一律拒。")

#: 外部映射的环境变量（指向一份 JSON 文件）。仓里**不写死**任何 id→名对照：
#: 2026-07-24 的 dogfood 只观察到 id（15/17/19/20/24、10~33）、**没取到对应名字**，
#: 编一份映射就是造数据（本仓最忌，且「解错」比「解不出」更坏）。需要时经本变量传入，
#: JSON 必须自带**结构化** `provenance`（:data:`OVERRIDE_PROVENANCE_FIELDS` 逐字段必填），否则整份拒收。
ENV_TASK_TYPE_MAP = "OPRUNWAY_PERF_TASK_TYPE_MAP"
#: override 的 `provenance` **必填结构化字段**——一句散文（"实测占位"）核不了，也拦不住手误。
OVERRIDE_PROVENANCE_FIELDS = ("db", "cann_version", "torch_npu_version",
                              "collect_command", "collected_at")
#: 必须含版本号数字的字段 / 必须是 `YYYY-MM-DD` 真实日期的字段。
_PROV_VERSION_FIELDS = frozenset(("cann_version", "torch_npu_version"))
_PROV_DATE_FIELD = "collected_at"
#: 字段最短长度；版本号本来就可能很短（`2.1`）→ 单独放宽，别把合法输入误拒。
_PROV_MIN_LEN = 4
_PROV_VERSION_MIN_LEN = 3
_PROV_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: 占位串识别（整串命中 / 子串命中 两套）——占位 provenance 等于没有 provenance。
_PLACEHOLDER_EXACT = frozenset((
    "todo", "tbd", "na", "n/a", "none", "null", "nil", "unknown", "unspecified",
    "placeholder", "example", "sample", "dummy", "foo", "bar", "test", "x", "xx",
    "待补", "待定", "待填", "未知", "无", "略", "占位", "同上", "见上", "实测", "临时",
))
_PLACEHOLDER_SUBSTR = ("占位", "待补", "待填", "待确认", "待核", "placeholder",
                       "todo", "tbd", "fixme", "xxx", "???", "填这里", "fill me", "your_")
#: 「有内容」的最低要求：至少一个字母 / 数字 / 汉字（纯符号 `---` 之类不算）。
_MEANINGFUL_RE = re.compile(r"[0-9A-Za-z一-鿿]")
#: 内置 id→名映射：**故意为空**，理由见 :data:`DB_TASK_TYPE_ID_NAMES_PROVENANCE`。
DB_TASK_TYPE_ID_NAMES = {}
DB_TASK_TYPE_ID_NAMES_PROVENANCE = (
    "空 · 未实测：2026-07-24 a3 dogfood 只观察到数值 id（custom 15/17/19/20/24、baseline 10~33），"
    "**没有取到 id→名对照**。编一份映射就是造数据，且「解错」比「解不出」更坏 → 这里不放任何默认值；"
    f"字典优先从 db 的**专用枚举表**解（{DICT_SOURCE_TABLE}），"
    f"通用字符串池（{DICT_SOURCE_STRING_IDS}）**只在 schema 声明了外键时**才认，"
    f"实在拿不到再经 {ENV_TASK_TYPE_MAP} 传入带结构化 provenance 的映射。")

#: 异步搬运类型——**一律不计入** kernel-only 时间。⚠ §9.7 📌 **未验证**（Level0 下没见过 memcpy TASK 行）。
DEVICE_MEMCPY_TYPE = "MEMCPY_ASYNC"
CSV_MEMCPY_TYPES = frozenset((DEVICE_MEMCPY_TYPE, "MEMSET"))
DB_MEMCPY_TYPES = frozenset((DEVICE_MEMCPY_TYPE, "KERNEL_MEMCPY", "MEMSET_ASYNC", "MEMSET"))
#: msprof CLI 自身的控制任务，不是算子计算、也不是数据搬运。2026-07-26 双边真机 CSV
#: 的 MSTX 窗内各观察到 1 条 `PROFILER_TRACE_EX`；参考仓会因不在 accepted_types 中自然跳过。
#: 我方不泛化成「跳过所有未知」，只允许这一个有真机证据的受控值。
CSV_CONTROL_TASK_TYPES = frozenset(("PROFILER_TRACE_EX",))

#: **受控枚举**（版本 :data:`TASK_TYPE_ENUM_VERSION`）：外部 override 的映射值只许落在这里面。
#: 计算类只有 :data:`DB_DEVICE_KERNEL_TYPES` 那两个实测坐实的，搬运类是 :data:`DB_MEMCPY_TYPES`——
#: 别的 `KERNEL_*` 一律拒（拒了只是继续 fail-closed；放行则可能把非计算 task 计成 kernel-only 耗时）。
CONTROLLED_TASK_TYPE_NAMES = frozenset(DB_DEVICE_KERNEL_TYPES | DB_MEMCPY_TYPES)

#: 类型分类结果。
KIND_COMPUTE = "compute"
KIND_MEMCPY = "memcpy"
KIND_CONTROL = "control"
KIND_UNKNOWN = "unknown"

#: 本模块产出的计时口径（双边必须同为它，否则 perf_compare 判 BLOCKED_INCOMPARABLE_TIMING_SCOPE）。
TIMING_SCOPE = "kernel_only"

DEFAULT_WARMUP = 5
DEFAULT_REPEAT = 20

# —— 采集配置（`--ai-core` 必须显式关，且双边同配置）——
# 2026-07-26 真机探针坐实参考仓路径：custom / baseline 都走 msprof CLI + ctypes MSTX + CSV。
# `torch_npu_profiler` 常量仅用于识别历史工件，不再由 live collector 产生。
COLLECTOR_MSPROF_CLI = "msprof_cli"
COLLECTOR_TORCH_PROFILER = "torch_npu_profiler"
#: `--ai-core=on`（msprof 默认）会让数字虚高 2.0~3.75×（§9.7 C 实测）→ 一律显式关。
AI_CORE_PROFILING = "off"
PROFILER_LEVEL = "Level0"
KERNEL_ACCOUNTING = "median_x_launches"
#: msprof CLI 固定参数。`--ai-core=off` 是 §9.7 C 的硬要求，**不得删**。
#:
#: 🔖 **这是对参考仓 msprof 命令的一处「有据偏离」，不是口径遗漏**（2026-07-25 读码复核）：
#: 参考仓 cannbot-ops-input `skills/operator-evaluation/scripts/perf_msprof.py:624-627` 的命令是
#: `msprof --output=… --task-time=on --ascendcl=on --msproftx=on <python> <wrapper>`——前三项与我们**逐字同**，
#: 差别只在它**不显式关 ai-core**（它的做法是「不请求 `--aic-metrics`」，而 `--ai-core` 默认就是 on，
#: 于是 AI Core 采样照样开着）。我们多带 `--ai-core=off`，依据是真机实测
#: （`dev-doc/oprunway-torch-baseline-design.md` §9.7 C，2026-07-24 a3 容器）：默认 on 让 Sort(MIX_AIV)
#: 单 kernel 虚高 **3.75×**（192.46 → 51.29 us）、每次调用 kernel 总和虚高 **2.0×**（308.9 → 153.2 us）；
#: 关掉后 msprof 与 torch_npu profiler 三路吻合（150~159 us/call）。
#: ⛔ 别为「与参考仓逐字一致」把这个参数删掉——删了拿到的不是「更 faithful 的数」，是虚高数倍的假数。
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
MARKER_RUNTIME_PROVENANCE = "__OPRUNWAY_PERF_RUNTIME_PROVENANCE__"

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


# ── taskType 数值枚举 id 的解析（§9.7 B′）─────────────────────────────────────────

def _numeric_id(value):
    """值是不是「纯数值 id」→ 归一成 `str(int)`；不是则 None（`KERNEL_AIVEC` 这类名字走这条）。"""
    if isinstance(value, bool):
        return None
    try:
        return str(int(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _two_column_map(conn, table):
    """两列字典表 → `{"<id>": "<name>"}`。**列名不写死**：优先 id/name 惯例列，否则取前两列。"""
    cols = _db_columns(conn, table)
    if len(cols) < 2:
        return {}
    id_col = next((c for c in ("id", "typeId", "taskType", "value") if c in cols), cols[0])
    name_col = next((c for c in ("name", "typeName", "value", "desc", "description")
                     if c in cols and c != id_col),
                    next((c for c in cols if c != id_col), None))
    if name_col is None:
        return {}
    try:
        cur = conn.execute(f"SELECT {id_col}, {name_col} FROM {table}")
        rows = cur.fetchall()
    except sqlite3.Error:
        return {}
    out = {}
    for key, val in rows:
        norm = _numeric_id(key)
        if norm is None or val is None or str(val).strip() == "":
            continue
        out.setdefault(norm, str(val).strip())
    return out


def _foreign_key_targets(conn, table):
    """`PRAGMA foreign_key_list(<table>)` → `{来源列小写: {(目标表小写, 目标列小写)}}`；无声明 → `{}`。

    这是**唯一**能证明「某列以某张表为字典」的 schema 级证据（sqlite 把 `REFERENCES` 原样记下来）。
    """
    try:
        rows = conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
    except sqlite3.Error:
        return {}
    out = {}
    for row in rows or ():
        # PRAGMA 行 = (id, seq, table, from, to, on_update, on_delete, match)
        if len(row) < 5 or row[3] is None:
            continue
        target_table = str(row[2] or "").strip().lower()
        target_col = str(row[4] or "").strip().lower()
        out.setdefault(str(row[3]).strip().lower(), set()).add((target_table, target_col))
    return out


def string_ids_is_task_type_dictionary(conn, tables, type_col):
    """`TASK.<type_col>` 是否**有据可查地**以 `STRING_IDS` 为字典 → `(bool, 依据文本)`。

    审计高危 #2：`STRING_IDS` 是 db 的**通用**字符串池（kernel 名 / API 名 / MSTX 消息都在里面），
    「取出来的字符串长得像类型枚举」**证明不了** `TASK.taskType` 是它的外键——纯 ID 碰撞
    （`taskType=15` 恰好撞上 `STRING_IDS[15]="KERNEL_STALE_NAME"`）就能造出**看似合法的假 us**。
    故这里只认 **schema 声明的外键**；拿不到证据 → `(False, …)` → 整条 `STRING_IDS` 兜底不启用，
    数值 id 保持 unresolved（fail-closed）。**宁可拿不到性能数，也不产假的。**
    """
    if TABLE_STRING_IDS not in (tables or ()) or not type_col:
        return False, f"{TABLE_STRING_IDS} 表不存在或 taskType 列未定位"
    fks = _foreign_key_targets(conn, TABLE_TASK)
    targets = fks.get(str(type_col).strip().lower(), set())
    for target_table, target_col in targets:
        if target_table == TABLE_STRING_IDS.lower():
            col = target_col or "(隐式主键)"
            return True, (f"schema 外键证据：PRAGMA foreign_key_list({TABLE_TASK}) 声明 "
                          f"{TABLE_TASK}.{type_col} → {TABLE_STRING_IDS}.{col}")
    return False, (f"无外键证据：{TABLE_TASK}.{type_col} 未声明指向 {TABLE_STRING_IDS} 的外键"
                   f"（通用字符串池不当 taskType 字典用）")


def string_id_name_pool(conn, tables):
    """被当作**名字 / 消息**引用的 string id 集合（归一成 `str(int)`）。

    同一个数字既被 kernel 名 / API 名 / MSTX 消息引用、又出现在 `TASK.taskType` 里 → 这个数字到底是
    「类型枚举 id」还是「字符串 id」根本分不清，**一律不拿它解 taskType**（审计高危 #2 要求的最低拒收项）。
    """
    pool = set()
    for table, cols in ((TABLE_COMPUTE_TASK_INFO, _CTI_NAME_COLUMNS),
                        (TABLE_CANN_API, ("name", "apiName", "type")),
                        (TABLE_MSTX, ("message",))):
        if table not in (tables or ()):
            continue
        available = set(_db_columns(conn, table))
        for col in cols:
            if col not in available:
                continue
            try:
                rows = conn.execute(f"SELECT DISTINCT {col} FROM {table}").fetchall()
            except sqlite3.Error:
                continue
            for row in rows:
                norm = _numeric_id(row[0])
                if norm is not None:
                    pool.add(norm)
    return pool


def _is_placeholder(text):
    """是不是「等于没写」的占位串（空 / 纯符号 / TODO / 占位 / 待补 …）。"""
    low = str(text or "").strip().lower()
    if not low or low in _PLACEHOLDER_EXACT:
        return True
    if any(tok in low for tok in _PLACEHOLDER_SUBSTR):
        return True
    return not _MEANINGFUL_RE.search(low)


def validate_override_provenance(prov):
    """override 的 `provenance` 结构化校验 → **不合格原因列表**（空列表 = 通过）。

    审计高危 #3：原来只要求「任意非空字符串」，于是 `"provenance": "实测占位"` 就能放行一份手打映射，
    把 fail-closed 变成**假性能数字**。现在 :data:`OVERRIDE_PROVENANCE_FIELDS` **逐字段必填**、
    占位串拒、版本字段须含数字、日期须是 `YYYY-MM-DD` 真实日期。
    """
    if not isinstance(prov, dict):
        return [f"provenance 须是对象且含 {list(OVERRIDE_PROVENANCE_FIELDS)}"
                f"（一句散文核不了、也拦不住手误）"]
    problems = []
    for field in OVERRIDE_PROVENANCE_FIELDS:
        raw = prov.get(field)
        if not isinstance(raw, str) or not raw.strip():
            problems.append(f"{field}：缺失或非字符串")
            continue
        text = raw.strip()
        floor = _PROV_VERSION_MIN_LEN if field in _PROV_VERSION_FIELDS else _PROV_MIN_LEN
        if len(text) < floor or _is_placeholder(text):
            problems.append(f"{field}：像占位 / 太短（{text[:40]!r}）")
        elif field in _PROV_VERSION_FIELDS and not re.search(r"\d", text):
            problems.append(f"{field}：不含版本号数字（{text[:40]!r}）")
        elif field == _PROV_DATE_FIELD and not _is_real_date(text):
            problems.append(f"{field}：须是 YYYY-MM-DD 的真实日期（{text[:40]!r}）")
    return problems


def _is_real_date(text):
    if not _PROV_DATE_RE.match(str(text).strip()):
        return False
    try:
        date.fromisoformat(str(text).strip())
        return True
    except ValueError:
        return False


def load_task_type_overrides(env=None):
    """读 :data:`ENV_TASK_TYPE_MAP` 指的 JSON → `(mapping, note)`。未设 → `({}, None)`。

    结构（`provenance` **必须是结构化对象**，字段见 :data:`OVERRIDE_PROVENANCE_FIELDS`）::

        {"provenance": {"db": "PROF_000_.../ascend_pytorch_profiler_1.db (sha256 ab12…)",
                        "cann_version": "9.0.1",
                        "torch_npu_version": "2.10.0",
                        "collect_command": "msprof --task-time=on --ai-core=off ./run.sh",
                        "collected_at": "2026-07-24"},
         "map": {"15": "KERNEL_AIVEC", "17": "KERNEL_MIX_AIV"}}

    两道闸（审计高危 #3）：
      · **provenance 逐字段必填 + 拒占位**（:func:`validate_override_provenance`）；
      · **映射值只许落在受控枚举** :data:`CONTROLLED_TASK_TYPE_NAMES`（版本
        :data:`TASK_TYPE_ENUM_VERSION`）——未知 `KERNEL_*` **不靠前缀放行**（`KERNEL_TYPO` 这种手误
        原来直接产 us）。
    **任一条不合格 → 整份拒收**（不做「丢坏的、留好的」，免得手误被静默吞掉），只回 note；
    采集侧照旧 fail-closed，绝不因此放行。
    """
    env = os.environ if env is None else env
    path = env.get(ENV_TASK_TYPE_MAP)
    if not path:
        return {}, None
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as ex:
        return {}, f"{ENV_TASK_TYPE_MAP} 读不了 / 非法 JSON，已拒收（{type(ex).__name__}）"
    if not isinstance(doc, dict) or not isinstance(doc.get("map"), dict) or not doc["map"]:
        return {}, (f"{ENV_TASK_TYPE_MAP} 结构不对（须 {{'provenance': {{…}}, 'map': {{非空}}}}），已拒收")
    problems = validate_override_provenance(doc.get("provenance"))
    if problems:
        return {}, (f"{ENV_TASK_TYPE_MAP} 的 provenance 不合格 → 整份拒收："
                    f"{'；'.join(problems[:6])}"
                    f"（必填 {list(OVERRIDE_PROVENANCE_FIELDS)}）")
    mapping, bad = {}, []
    for key, val in doc["map"].items():
        norm = _numeric_id(key)
        if norm is None:
            bad.append(f"{key!r}：不是数值 taskType id")
            continue
        name = val.strip() if isinstance(val, str) else ""
        if name not in CONTROLLED_TASK_TYPE_NAMES:
            bad.append(f"{key}→{val!r}：不在受控枚举内")
            continue
        if norm in mapping and mapping[norm] != name:
            bad.append(f"{key}：同一份 map 里自相矛盾")
            continue
        mapping[norm] = name
    if bad:
        return {}, (f"{ENV_TASK_TYPE_MAP} 的 map 有不合格项 → 整份拒收：{'；'.join(bad[:6])}"
                    f"（受控枚举 v{TASK_TYPE_ENUM_VERSION}="
                    f"{sorted(CONTROLLED_TASK_TYPE_NAMES)}；未知 KERNEL_* 不靠前缀放行）")
    prov = doc["provenance"]
    summary = " · ".join(f"{f}={prov[f].strip()[:40]}" for f in OVERRIDE_PROVENANCE_FIELDS)
    return mapping, (f"taskType 映射来自 {ENV_TASK_TYPE_MAP}"
                     f"（结构化 provenance: {summary}；受控枚举 v{TASK_TYPE_ENUM_VERSION}）")


def _dict_provenance(**kwargs):
    """字典 provenance 骨架——「凭什么信这份字典」全记在这，随 detail 出证。"""
    base = {"sources": [], "considered": [], "evidence": {}, "rejected": [], "conflicts": [],
            "enum_version": TASK_TYPE_ENUM_VERSION}
    base.update(kwargs)
    return base


def task_type_dictionary(conn, tables, env=None, *, type_col=None):
    """taskType 数值 id → 名字的字典 → `(mapping, provenance)`。**每个来源都要拿得出依据**。

    来源（可信度从高到低）：
      1. **db 专用枚举表**（表名按 :data:`_TASK_TYPE_DICT_TABLE_RE` 通用探测）——db 为 taskType 专设，
         首方数据，可信；
      2. **`STRING_IDS`**（通用字符串池）——**只在 schema 声明了外键时**才启用
         （:func:`string_ids_is_task_type_dictionary`），且 id 不得与 kernel/API 名池重合
         （:func:`string_id_name_pool`）、取出的字符串还须过外形闸；
      3. **外部 override**（:data:`ENV_TASK_TYPE_MAP`）——须过结构化 provenance + 受控枚举双闸。

    **合并规则：冲突即拒，绝不「后者覆盖前者」也绝不「先到先得」**——同一个 id 被两个来源解成不同名字，
    说明至少有一个是错的，该 id 直接丢弃（→ unresolved → 上层 fail-closed）。
    一个都拿不到 → `({}, provenance)`，数值 id 全部解不出 → 上层 fail-closed（**绝不猜**）。
    """
    prov = _dict_provenance()
    per_source = {}

    # 1) db 专用枚举表
    prov["considered"].append(DICT_SOURCE_TABLE)
    table_map, table_names = {}, []
    for table in sorted(tables or ()):
        if not _TASK_TYPE_DICT_TABLE_RE.match(str(table)):
            continue
        found = _two_column_map(conn, table)
        if not found:
            continue
        table_names.append(str(table))
        for key, val in found.items():
            if key in table_map and table_map[key] != val:
                prov["conflicts"].append({"id": key, "source": DICT_SOURCE_TABLE,
                                          "names": sorted({table_map[key], val})})
                table_map[key] = None                     # 同类来源内部打架 → 该 id 作废
            else:
                table_map.setdefault(key, val)
    table_map = {k: v for k, v in table_map.items() if v}
    if table_names:
        prov["evidence"][DICT_SOURCE_TABLE] = f"db 专用 taskType 枚举表：{', '.join(table_names)}"
    if table_map:
        per_source[DICT_SOURCE_TABLE] = table_map

    # 2) STRING_IDS —— 默认不认，须有 schema 外键证据（审计高危 #2）
    prov["considered"].append(DICT_SOURCE_STRING_IDS)
    ok, why = string_ids_is_task_type_dictionary(conn, tables, type_col)
    prov["evidence"][DICT_SOURCE_STRING_IDS] = why
    if ok:
        name_pool = string_id_name_pool(conn, tables)
        picked = {}
        for key, val in (_string_ids(conn, tables) or {}).items():
            norm = _numeric_id(key)
            if norm is None or val is None:
                continue
            text = str(val).strip()
            if norm in name_pool:
                prov["rejected"].append({"id": norm, "name": text,
                                         "source": DICT_SOURCE_STRING_IDS,
                                         "reason": "与 kernel/API 名池的 string id 重合，分不清是类型还是名字"})
                continue
            if not _TASK_TYPE_TOKEN_RE.match(text):
                continue                                  # 不像类型枚举 → 静默略过（解不出 → fail-closed）
            picked[norm] = text
        if picked:
            per_source[DICT_SOURCE_STRING_IDS] = picked

    # 3) 外部 override
    prov["considered"].append(DICT_SOURCE_ENV)
    override, note = load_task_type_overrides(env)
    if note:
        prov["evidence"][DICT_SOURCE_ENV] = note
    if override:
        per_source[DICT_SOURCE_ENV] = override

    # 合并：冲突即拒
    mapping, seen = {}, {}
    for source in (DICT_SOURCE_TABLE, DICT_SOURCE_STRING_IDS, DICT_SOURCE_ENV):
        for key, val in (per_source.get(source) or {}).items():
            seen.setdefault(key, []).append((source, val))
    for key, entries in seen.items():
        names = {val for _src, val in entries}
        if len(names) > 1:
            prov["conflicts"].append({"id": key, "names": sorted(names),
                                      "sources": [src for src, _v in entries],
                                      "reason": "多来源解出不同名字 → 该 id 作废（不做后者覆盖前者）"})
            continue
        mapping[key] = entries[0][1]
    used = {src for key in mapping for src, _v in seen[key]}
    prov["sources"] = [s for s in (DICT_SOURCE_TABLE, DICT_SOURCE_STRING_IDS, DICT_SOURCE_ENV)
                       if s in used]
    return mapping, prov


def resolve_task_type(value, mapping=None):
    """`TASK.taskType` 原始值 → `(显示文本, 是否解出名字, 原始数值 id|None)`。

    · 本来就是名字（`KERNEL_AIVEC`）→ 原样、`resolved=True`；
    · 数值 id 且字典查得到 → 名字、`resolved=True`、带回原 id；
    · 数值 id 查不到 → `taskType_id:<id>`、`resolved=False`——**绝不拿字面数字去比白名单**，
      上层照 unknown 处理 → fail-closed。
    """
    raw = _numeric_id(value)
    if raw is None:
        return str(value).strip(), True, None
    name = (mapping or {}).get(raw)
    if name:
        return str(name).strip(), True, raw
    return f"{UNRESOLVED_TASK_TYPE_PREFIX}{raw}", False, raw


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

    §9.7 B′：taskType 列**可能是数值枚举 id**，故一律过 :func:`resolve_task_type`——解得出记名字
    （`type_resolved=True`），解不出记 `taskType_id:<id>`（`type_resolved=False`）交上层 fail-closed。
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
        type_col = next((c for c in _TASK_TYPE_COLUMNS if c in tcols), None)
        if type_col is None or not ({"globalTaskId", "startNs", "endNs"} <= tcols):
            return None, ERR_NO_TASK_TABLE
        name_col = next((c for c in _CTI_NAME_COLUMNS if c in ccols), None)
        if name_col is None or "globalTaskId" not in ccols:
            return None, ERR_NO_TASK_TABLE
        dev_col = next((c for c in _DEVICE_COLUMNS if c in tcols), None)
        strings = _string_ids(conn, tables)
        type_dict, dict_prov = task_type_dictionary(conn, tables, type_col=type_col)
        dict_sources = list(dict_prov.get("sources") or ())
        sel = ["t.startNs", "t.endNs", f"t.{type_col}", f"c.{name_col}"]
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
            text, resolved, type_id = resolve_task_type(row[2], type_dict)
            out.append({"name": str(_resolve(row[3], strings)),
                        "type": text, "type_resolved": resolved, "type_id": type_id,
                        "type_dict_sources": list(dict_sources),
                        "type_dict_provenance": dict_prov,
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
    """解析 MSTX 测量窗 → `(window|None, err|None)`。

    显式 `route` 严格服从；未指定时为历史调用保持 db 优先。live collector 必须传
    :data:`ROUTE_CSV`，不得依赖默认选择。

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
    """kernel/task 类型 → `compute` / `memcpy` / `control` / `unknown`。

    未知类型一律 `unknown` → 上层 fail-closed，**绝不当成「没有 kernel」静默得 0 us**
    （原设计拿 CSV 那套白名单去比 db 的 `KERNEL_AIVEC`，一个都不中、静默 0，正是这条要堵的）。
    """
    text = str(task_type or "").strip()
    if route == ROUTE_DB:
        # §9.7 B′：没解成名字的数值 id（`15` / `taskType_id:15`）**绝不按字面比白名单** → unknown。
        if text.startswith(UNRESOLVED_TASK_TYPE_PREFIX) or _numeric_id(text) is not None:
            return KIND_UNKNOWN
        if text in DB_MEMCPY_TYPES:
            return KIND_MEMCPY
        if text in DB_DEVICE_KERNEL_TYPES or text.startswith(DB_DEVICE_KERNEL_PREFIX):
            return KIND_COMPUTE
        return KIND_UNKNOWN
    if text in CSV_MEMCPY_TYPES:
        return KIND_MEMCPY
    if text in CSV_DEVICE_KERNEL_TYPES:
        return KIND_COMPUTE
    if text in CSV_CONTROL_TASK_TYPES:
        return KIND_CONTROL
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
             "route": route, "observed_task_types": {}, "window_wall_us": None,
             "unresolved_task_type_ids": [], "task_type_dict_sources": [],
             "task_type_dict_provenance": {}, "error": None}
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
    in_window, observed, unresolved, dict_sources = [], {}, set(), []
    dict_prov = {}
    for row in rows:
        if not _in_window(row, measurement_window):
            continue
        # 多卡（实测 device_count=16）：窗与 task 行的 device 必须对得上；行没带 device 的不排除。
        row_dev = row.get("device_id")
        if window_dev and row_dev and row_dev != window_dev:
            continue
        in_window.append(row)
        observed[row.get("type") or ""] = observed.get(row.get("type") or "", 0) + 1
        # §9.7 B′：数值 taskType 没解出名字 → 记下 id，供下一轮补字典（detail 里带给人看）。
        if row.get("type_resolved") is False and row.get("type_id") is not None:
            unresolved.add(str(row["type_id"]))
        for src in row.get("type_dict_sources") or ():
            if src not in dict_sources:
                dict_sources.append(src)
        # 「凭什么信这份字典」——db 级信息，取第一份即可（同一 db 的所有行共用同一个 provenance）。
        if not dict_prov and row.get("type_dict_provenance"):
            dict_prov = row["type_dict_provenance"]
    empty["observed_task_types"] = observed
    empty["unresolved_task_type_ids"] = sorted(unresolved, key=lambda s: (len(s), s))
    empty["task_type_dict_sources"] = dict_sources
    empty["task_type_dict_provenance"] = dict_prov

    compute, memcpy, control, unknown = [], [], [], []
    for row in in_window:
        kind = classify_task_type(row.get("type"), route)
        (compute if kind == KIND_COMPUTE else
         memcpy if kind == KIND_MEMCPY else
         control if kind == KIND_CONTROL else unknown).append(row)
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


def parse_host_transfer_evidence(prof_dir, case, *, repeat, materializations=2,
                                 db_path=None, route=None):
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
    db_path = (db_path or find_profiler_db(prof_dir)) if route in (None, ROUTE_DB) else None
    if db_path is not None:
        total = count_db_api_calls(db_path, "aclrtMemcpy")
        if total is not None:
            evidence["source"] = ROUTE_DB
    if route == ROUTE_DB and total is None:
        evidence["note"] = "指定 db 路线但无 CANN_API 搬运证据 → hybrid 未判"
        return evidence
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
        if measurement.get("unresolved_task_type_ids"):
            # §9.7 B′：数值枚举 id 没解出名字——把 id 与字典来源都带出来，下一轮好补字典。
            detail["unresolved_task_type_ids"] = measurement["unresolved_task_type_ids"]
            detail["task_type_dict_sources"] = measurement.get("task_type_dict_sources") or []
            # 审计高危 #2/#3：把「每个字典来源凭什么信 / 拒了什么 / 哪些 id 多来源打架」一并出证，
            # 免得「解不出」看起来像工具没本事——它常常是**证据不足、主动 fail-closed**。
            if measurement.get("task_type_dict_provenance"):
                detail["task_type_dict_provenance"] = measurement["task_type_dict_provenance"]
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
        if detail.get("unresolved_task_type_ids"):
            detail["note"] += (f"；其中 {len(detail['unresolved_task_type_ids'])} 个是**数值枚举 id**"
                               f"（db 里没找到**有据可查**的字典，已认下的来源="
                               f"{detail.get('task_type_dict_sources') or '无'}；"
                               f"依据/拒收原因见 task_type_dict_provenance）——"
                               f"可经 {ENV_TASK_TYPE_MAP} 传入映射，但须带结构化 provenance"
                               f"（{list(OVERRIDE_PROVENANCE_FIELDS)}）且值落在受控枚举 "
                               f"v{TASK_TYPE_ENUM_VERSION} 内（§9.7 B′/B″）")
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
    `collector` 保持不入旧契约比对键以兼容历史工件；live collector 自 2026-07-26 起双边统一
    `msprof_cli`，新产物不存在采集入口分裂。
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


def side_failure_reason(label, side):
    """从采集侧结构化 detail 提炼失败原因；只做诊断转录，不做归因。"""
    side = side if isinstance(side, dict) else {}
    detail = side.get("detail") if isinstance(side.get("detail"), dict) else {}
    parts = [f"{label} 侧未产生可计时的 device kernel（behavior={side.get('behavior')}）"]
    if detail.get("returncode") not in (None, 0):
        parts.append(f"returncode={detail['returncode']}")
    if isinstance(detail.get("note"), str) and detail["note"].strip():
        parts.append(detail["note"].strip())
    if isinstance(detail.get("parse_error"), str) and detail["parse_error"].strip():
        parts.append(f"parse_error={detail['parse_error'].strip()}")
    tail = detail.get("output_tail")
    if isinstance(tail, str):
        signals = []
        for line in tail.splitlines():
            text = line.strip()
            lowered = text.lower()
            if text and any(token in lowered for token in (
                    "error", "failed", "traceback", "runtimeerror", "acl", "timeout")):
                signals.append(text)
        if signals:
            excerpt = " | ".join(signals[-3:])
            parts.append(f"error_excerpt={excerpt[-800:]}")
    return "；".join(parts)


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
            entry["note"] = side_failure_reason("custom", custom)
        out[cid] = entry
    for item in skipped or []:
        cid = item.get("case_id")
        if cid:
            out[cid] = {"scope": TIMING_SCOPE, "us": None,
                        "behavior": None, "execution_path": None,
                        "note": item.get("reason") or SKIPPED_ACCURACY_FAILED}
    return out


def build_baseline_document(records, *, op=None, warmup=DEFAULT_WARMUP, repeat=DEFAULT_REPEAT,
                            skipped=None, source="torch_npu"):
    """records → 真实基线文档（`repo_adapter.parse_device_baseline` 的输入）。

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
            item = {"case_id": cid, "us": float(baseline["us"]),
                    "env": f"{source} under msprof_cli(ctypes_mstx,csv)",
                    "execution_path": baseline.get("execution_path")}
            if baseline.get("runtime_provenance") is not None:
                item["runtime_provenance"] = baseline["runtime_provenance"]
            per_case.append(item)
        else:
            excluded.append({"case_id": cid, "behavior": behavior,
                             "reason": side_failure_reason("baseline", baseline)})
    for item in skipped or []:
        excluded.append({"case_id": item.get("case_id"),
                         "behavior": None,
                         "reason": item.get("reason") or SKIPPED_ACCURACY_FAILED})
    return {"source": source, "scope": TIMING_SCOPE, "op": op,
            "per_case": per_case, "excluded": excluded,
            "collection": {"tool": COLLECTOR_MSPROF_CLI,
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

def resolve_torch_baseline_plan(torch_baseline, call, case=None):
    """据 spec `perf.torch_baseline` 把该 case **已解析好的** `aclnn_call.slots` 翻成 torch 调用计划。

    契约（字段驱动、op-中立）::

        "torch_baseline": {"api": "torch.median",
                           "positional": ["self"],
                           "keyword": {"dim": "dim", "keepdim": "keepdim"}}

    · `positional` 列的是 **slot name**（与 aclnn 头签名同名），按列出顺序作 torch 位置参数；
      **缺任一即 fail-closed**（不猜、不重排）。
    · `keyword` 是 `slot name -> torch 形参名`；**该 case 没有这个 slot 就自然缺席**。
    · `keyword_groups` 可声明一组 keyword 只在某个语义 attr 满足条件时整体出现。例如统一 ACLNN
      ABI 即使全局变体仍携带 dim/keepDim 的占位 slot，也可按 case.attrs.dim==null 同时省略这组
      torch kwarg。规则完全由 spec 字段驱动，不按算子身份分支。
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
    keyword_by_slot = {}
    for name, kwarg in (torch_baseline.get("keyword") or {}).items():
        slot = by_name.get(name)
        if slot is None:
            continue                                    # 该变体没有这个属性 → torch 侧自然缺席
        keyword_by_slot[name] = (str(kwarg), slot)

    grouped = set()
    attrs = (case or {}).get("attrs") if isinstance(case, dict) else None
    groups = torch_baseline.get("keyword_groups") or []
    if not isinstance(groups, list):
        raise PerfCollectError("perf.torch_baseline.keyword_groups 须为数组")
    for pos, group in enumerate(groups):
        if not isinstance(group, dict):
            raise PerfCollectError(f"perf.torch_baseline.keyword_groups[{pos}] 须为对象")
        when = group.get("when")
        members = group.get("slots")
        if not isinstance(when, dict) or not isinstance(when.get("attr"), str) or not when["attr"]:
            raise PerfCollectError(
                f"perf.torch_baseline.keyword_groups[{pos}].when 须含非空 attr")
        if not isinstance(when.get("is_null"), bool):
            raise PerfCollectError(
                f"perf.torch_baseline.keyword_groups[{pos}].when.is_null 须为 JSON 布尔")
        if not isinstance(members, list) or not members or any(
                not isinstance(name, str) or not name for name in members):
            raise PerfCollectError(
                f"perf.torch_baseline.keyword_groups[{pos}].slots 须为非空 slot-name 数组")
        overlap = grouped.intersection(members)
        if overlap:
            raise PerfCollectError(
                f"perf.torch_baseline.keyword_groups 的 slot 重复归组：{sorted(overlap)}")
        unknown = [name for name in members if name not in (torch_baseline.get("keyword") or {})]
        if unknown:
            raise PerfCollectError(
                f"perf.torch_baseline.keyword_groups[{pos}] 引用了 keyword 未声明的 slot：{unknown}")
        if not isinstance(attrs, dict) or when["attr"] not in attrs:
            raise PerfCollectError(
                f"keyword_groups[{pos}] 要按 case.attrs.{when['attr']} 选变体，但该 case 缺此语义属性")
        matched = (attrs[when["attr"]] is None) == when["is_null"]
        grouped.update(members)
        if not matched:
            for name in members:
                keyword_by_slot.pop(name, None)

    keyword = {kwarg: slot for kwarg, slot in keyword_by_slot.values()}
    return {"api": api, "positional": positional, "keyword": keyword}


def resolve_aclnn_baseline_plan(aclnn_baseline, call, case):
    """把 spec 的内置 ACLNN baseline 变体解析成一次确定调用。

    契约完全字段驱动，不按算子名分派::

        {"library": "cann_builtin_libopapi",
         "variants": [
           {"when": {"attr": "dim", "is_null": true},
            "symbol": "Median", "slots": ["self", "valuesOut"]},
           {"when": {"attr": "dim", "is_null": false},
            "symbol": "MedianDim",
            "slots": ["self", "dim", "keepDim", "valuesOut", "indicesOut"],
            "output_dtypes": {"indicesOut": "int64"}}
         ]}

    ``slots`` 从该 case 已解析的 ``aclnn_call.slots`` 按名字选择并重排，因而可以表达“DUT
    统一接口、任务书基线是两个既有 ACLNN 接口”这类 ABI 差异。匹配必须恰好一条，缺/重名/多匹配
    都 fail-closed；不做 torch 等价性推断。
    """
    if not isinstance(aclnn_baseline, dict):
        raise PerfCollectError("spec 缺 perf.aclnn_baseline——任务书 ACLNN 基线须显式声明调用变体")
    if aclnn_baseline.get("library") != "cann_builtin_libopapi":
        raise PerfCollectError(
            "perf.aclnn_baseline.library 当前只接受受控值 'cann_builtin_libopapi'，"
            "由 ASCEND_TOOLKIT_HOME 解析本机 CANN libopapi.so；不接受任意路径")
    variants = aclnn_baseline.get("variants")
    if not isinstance(variants, list) or not variants:
        raise PerfCollectError("perf.aclnn_baseline.variants 须为非空数组")
    attrs = case.get("attrs") or {}

    def matches(variant):
        when = variant.get("when")
        if not isinstance(when, dict) or not isinstance(when.get("attr"), str):
            raise PerfCollectError("aclnn baseline variant.when 须声明 attr")
        name = when["attr"]
        if name not in attrs:
            raise PerfCollectError(
                f"{case.get('id')}: aclnn baseline variant.when 引用的 attr {name!r} 不在 case.attrs 中")
        value = attrs.get(name)
        if "is_null" in when:
            flag = when["is_null"]
            if not isinstance(flag, bool):
                raise PerfCollectError("aclnn baseline variant.when.is_null 须为 JSON 布尔")
            return (value is None) == flag
        if "equals" in when:
            return value == when["equals"]
        raise PerfCollectError("aclnn baseline variant.when 只支持 is_null 或 equals")

    if any(not isinstance(v, dict) for v in variants):
        raise PerfCollectError("perf.aclnn_baseline.variants 每项都须为 object")
    selected = [v for v in variants if matches(v)]
    if len(selected) != 1:
        raise PerfCollectError(
            f"{case.get('id')}: aclnn baseline 变体须恰好匹配一条，实际 {len(selected)} 条")
    variant = selected[0]
    symbol = variant.get("symbol")
    names = variant.get("slots")
    output_dtypes = variant.get("output_dtypes") or {}
    if not isinstance(symbol, str) or not symbol or symbol.startswith("aclnn"):
        raise PerfCollectError("aclnn baseline variant.symbol 须为不带 aclnn 前缀的非空基名")
    if not isinstance(names, list) or not names or any(not isinstance(n, str) or not n for n in names):
        raise PerfCollectError("aclnn baseline variant.slots 须为非空 slot-name 数组")
    if not isinstance(output_dtypes, dict) or any(
            not isinstance(name, str) or not name
            or not isinstance(dtype, str) or not dtype
            for name, dtype in output_dtypes.items()):
        raise PerfCollectError(
            "aclnn baseline variant.output_dtypes 须为 {output_slot: logical_dtype} 对象")
    source_slots = call.get("slots") or []
    by_name = {}
    for slot in source_slots:
        name = slot.get("name") if isinstance(slot, dict) else None
        if not name:
            raise PerfCollectError("aclnn_call.slots 存在缺 name 条目")
        if name in by_name:
            raise PerfCollectError(f"aclnn_call.slots 有重名 {name!r}，基线映射不唯一")
        by_name[name] = slot
    missing = [name for name in names if name not in by_name]
    if missing:
        raise PerfCollectError(f"aclnn baseline 要求的 slots {missing} 不在本 case aclnn_call 中")
    bad_overrides = [
        name for name in output_dtypes
        if name not in names or by_name[name].get("role") != "out"
    ]
    if bad_overrides:
        raise PerfCollectError(
            "aclnn baseline output_dtypes 只能覆盖本变体选中的非空 out slot，"
            f"非法项={bad_overrides}")
    return {"library": aclnn_baseline["library"], "symbol": symbol,
            "slots": [by_name[name] for name in names],
            "output_dtypes": dict(output_dtypes)}


def cann_builtin_libopapi():
    """解析当前真机 CANN 的内置 libopapi.so；路径只来自 toolkit 环境，不进 spec。"""
    cann = os.environ.get("ASCEND_TOOLKIT_HOME")
    if not cann:
        raise PerfCollectError("ASCEND_TOOLKIT_HOME 未设置，无法解析 CANN 内置 libopapi.so")
    path = os.path.realpath(os.path.join(cann, "lib64", "libopapi.so"))
    if not os.path.isfile(path):
        raise PerfCollectError(f"CANN 内置 libopapi.so 不存在: {path}")
    return path


# ══ 以下为真机采集（gated：OPRUNWAY_ACLNN_REAL=1）══════════════════════════════════

def _require_real_gate():
    if os.environ.get("OPRUNWAY_ACLNN_REAL") != "1":
        raise PerfCollectError(
            "真机性能采集未启用——须 OPRUNWAY_ACLNN_REAL=1（同 aclnn_adapter 的真机 gate）。"
            "离线只提供解析 / 聚合 / 分类 / speedup / scope / 采集配置校验（可单测）。")


def plan_bool(plan, key, default=False):
    """perf plan 的**严格布尔**取值：只认真正的 JSON `true` / `false`（fail-closed）。

    与 `aclnn_adapter._plan_bool` **同口径**（两侧隔着 ssh：host 侧的 adapter 早失败一次，
    容器侧这份是最后一道——采集端不能因为 plan 在传输/手写环节被改坏就把硬门放开）。
    `bool("false")` / `bool("0")` 都是 **True**：字段被误写成字符串，`allow_builtin_symbols`
    就悄悄打开、关掉 custom-vendor provenance 硬门，于是「精度验 custom vendor、性能却测到
    CANN 内置同名实现」的假 PASS 从这里溜进来。字段缺省 → `default`；字段在但不是 `bool` → 立刻抛。
    **调用方拿到的已是真 bool，下游不得再 `bool(...)` 二次解释。**
    """
    if key not in plan:
        return default
    value = plan[key]
    if not isinstance(value, bool):
        raise PerfCollectError(
            f"perf plan 的 {key} 须为 JSON 布尔（true / false），得 {value!r}"
            f"（{type(value).__name__}）——字符串 \"false\" / \"0\" 一律不接受："
            f"宽松真值会把严格档悄悄关掉（=放行 CANN 内置同名实现，正是本项目要防的假 PASS）")
    return value


def _plan_str(plan, key):
    """perf plan 里的可选路径字段：缺省 → None；给了必须是**非空字符串**（否则 fail-closed）。"""
    if key not in plan or plan[key] is None:
        return None
    value = plan[key]
    if not isinstance(value, str) or not value.strip():
        raise PerfCollectError(
            f"perf plan 的 {key} 须为非空字符串路径，得 {value!r}（{type(value).__name__}）——fail-closed")
    return value.strip()


def resolve_plan_dut_lib(plan, *, strict):
    """据 perf plan 定出**本次 DUT** 的 `libcust_opapi.so` 绝对路径；严格档下定不出即 fail-closed。

    来源链（**只认 plan 显式给的字段，绝不从容器 env 猜**——`ASCEND_CUSTOM_OPP_PATH` /
    `LD_LIBRARY_PATH` 里可能继承着上次安装的陈旧 vendor，拿它当 DUT 正是要防的假 PASS）：

      1. `dut_lib` —— 本次 build install 出的 `.so` 绝对路径；
      2. `dut_vendor_root` —— vendor **内容根** → `<root>/op_api/lib/libcust_opapi.so`；
      3. `vendor_dir` + `vendor_name` —— aclnn_adapter cfg 的已知字段，与其 `_ENV_PREAMBLE` 里
         `VC="$VROOT/vendors/<name>_nn"` **同口径** → 内容根 → 同 2。
         （只在 1/2 都没给时才用，不与 1/2 交叉校验：显式声明优先，避免制造假冲突。）

    1 与 2 都给且指向不同文件 → fail-closed，判据复用 `aclnn_runner._resolve_dut_lib`（DUT 唯一性
    只此一处解释）。严格档定不出 → 抛错并说清 plan 该补什么；**绝不默默退回宽松档**。
    宽松档（`allow_builtin_symbols=true`，跑 CANN 内置算子的基线场景）定不出 → 返回 None，正常。
    """
    from .aclnn_runner import _resolve_dut_lib          # 纯路径推导，无 CANN 依赖

    dut_lib = _plan_str(plan, "dut_lib")
    vendor_root = _plan_str(plan, "dut_vendor_root")
    if not dut_lib and not vendor_root:
        vendor_dir = _plan_str(plan, "vendor_dir")
        vendor_name = _plan_str(plan, "vendor_name")
        if vendor_dir and vendor_name:
            vendor_root = str(Path(vendor_dir) / "vendors" / f"{vendor_name}_nn")
    resolved = _resolve_dut_lib(dut_lib, vendor_root)
    if strict and not resolved:
        raise PerfCollectError(
            "严格档（perf plan 未开 allow_builtin_symbols）定不出本次 DUT——plan 须给 "
            "dut_lib=<.../op_api/lib/libcust_opapi.so>，或 dut_vendor_root=<本次 install 的 vendor "
            "内容根>，或 aclnn_adapter 的 vendor_dir + vendor_name。没有它，性能侧只能证明符号「来自"
            "某个 custom vendor so」——环境里继承来的**上次**安装产物同样满足 → 旧产物代跑、报假 PASS。"
            "确要测 CANN 内置算子（无 PR 的基线场景）才在 plan 里写 allow_builtin_symbols: true。")
    return resolved


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
# ⚠ 洞 2：runner **与精度通路同口径走严格档**（require_custom_vendor），否则会出现
#   「精度验的是 custom vendor、性能测的是 CANN 内置同名实现」——同一个假 PASS 缺口的性能通路版本。
#   开关 = cfg 的 strict_custom_vendor（缺省 True；plan 的 allow_builtin_symbols 才关得掉，
#   对应 aclnn_driver 的 --allow-builtin-symbols）。跑完 close()，别泄 stream/device 上下文。
# ⚠ 严格档还必须带 cfg 的 dut_lib（= 本次 build install 出的 libcust_opapi.so 绝对路径，由 perf plan
#   一路传下来）：只证明「来自某个 custom so」拦不住环境里继承来的**上次**安装产物代跑（runner 改动⑪）。
#   两个开关都**只认真 bool / 非空串**，绝不 bool(...) 宽松转真——"false" 会把硬门悄悄关掉。
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
strict = CFG.get("strict_custom_vendor", True)
if not isinstance(strict, bool):
    raise RuntimeError("cfg strict_custom_vendor 须为 JSON 布尔（true/false），得 %r——"
                       "字符串 \"false\"/\"0\" 一律不接受（宽松真值会把严格档悄悄关掉）" % (strict,))
dut_lib = CFG.get("dut_lib")
if strict and not dut_lib:
    raise RuntimeError("严格档缺 dut_lib——perf plan 须给 dut_lib / dut_vendor_root（或 adapter 的 "
                       "vendor_dir + vendor_name），否则证明不了测的是本次 build 出的 custom vendor 产物")
with AclnnRunner(device=int(CFG["device"]), require_custom_vendor=strict,
                 dut_lib=dut_lib) as runner:
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
# 出 with = runner.close()：销毁自建 stream + reset device 上下文（旧版从不 close，逐 case 泄一条 stream）。
print(CFG["marker_devices"] + json.dumps(["npu:%d" % int(CFG["device"])]), flush=True)
'''


_CPP_EXTENSION_WRAPPER = r'''# OpRunway perf wrapper · custom(official cpp_extension) —— 自动生成，勿手改
import ctypes, hashlib, json, os, sys
from pathlib import Path

import numpy as np
import torch
import torch_npu  # noqa: F401

CFG = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
sys.path.insert(0, CFG["runtime_root"])

import cpp_extension_driver as D

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

cpp = CFG.get("cpp_extension")
if not isinstance(cpp, dict):
    raise RuntimeError("cpp_extension custom wrapper 缺冻结配置")
work = os.path.realpath(CFG["work_dir"])
artifact_rec = cpp.get("artifact") or {}
artifact = D._safe(work, artifact_rec.get("path"))
if not os.path.isfile(artifact) or sha256(artifact) != artifact_rec.get("sha256"):
    raise RuntimeError("cpp_extension ELF 缺失或与精度阶段 receipt 漂移")
vendor = cpp.get("vendor") or {}
vendor_path = vendor.get("library_path")
if (not isinstance(vendor_path, str) or not os.path.isabs(vendor_path)
        or not os.path.isfile(vendor_path)
        or sha256(vendor_path) != vendor.get("library_sha256")):
    raise RuntimeError("cpp_extension vendor library 缺失或摘要漂移")
handle = ctypes.CDLL(vendor_path, mode=ctypes.RTLD_GLOBAL)
missing = [name for name in (vendor.get("symbols_owned") or [])
           if not isinstance(name, str) or not hasattr(handle, name)]
if missing:
    raise RuntimeError("cpp_extension vendor symbols 漂移: %r" % (missing,))

plan_path = D._safe(work, cpp.get("invocation_plan"))
invocation = D._load(plan_path)
if D._canonical_sha(invocation) != cpp.get("invocation_plan_sha256"):
    raise RuntimeError("cpp_extension invocation plan 摘要漂移")
row = next((item for item in invocation.get("cases") or []
            if item.get("case_id") == CFG["case_id"]), None)
if row is None:
    raise RuntimeError("cpp_extension invocation plan 缺 case_id=%s" % CFG["case_id"])
caseset = json.loads(Path(CFG["caseset"]).read_text(encoding="utf-8"))
case = next(c for c in caseset["cases"] if c["id"] == CFG["case_id"])

dev_index = int(CFG["device"])
torch.npu.set_device(dev_index)
torch.ops.load_library(artifact)
namespace = getattr(torch.ops, cpp["namespace"])
op = getattr(namespace, row["entrypoint"])

def materialize():
    args, outputs, _contracts = D.materialize_invocation(
        torch, np, work, case, row)
    return args, outputs

def invoke(args):
    with torch.no_grad():
        return op(*args)

print("%sWARMUP_START" % CFG["marker_phase"], flush=True)
args, outputs = materialize()
last = None
for _ in range(max(0, int(CFG["warmup"]))):
    last = invoke(args)
torch.npu.synchronize()
print("%sWARMUP_DONE" % CFG["marker_phase"], flush=True)

# 测量前重新物化输入和输出，避免原地/有状态语义继承 warmup。
args, outputs = materialize()
torch.npu.synchronize()
mstx = ctypes.CDLL("libms_tools_ext.so")
mstx.mstxRangeStartA.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
mstx.mstxRangeStartA.restype = ctypes.c_uint64
mstx.mstxRangeEnd.argtypes = [ctypes.c_uint64]
mstx.mstxRangeEnd.restype = None
stream_ptr = int(torch.npu.current_stream().npu_stream)
range_id = mstx.mstxRangeStartA(
    CFG["range_name"].encode("utf-8"), ctypes.c_void_p(stream_ptr))
if not range_id:
    raise RuntimeError("ctypes mstxRangeStartA returned 0 —— MSTX 未生效，测量窗不可信")
print("%sMEASURE_START" % CFG["marker_phase"], flush=True)
try:
    for _ in range(max(1, int(CFG["repeat"]))):
        last = invoke(args)
    torch.npu.synchronize()
finally:
    mstx.mstxRangeEnd(range_id)
print("%sMEASURE_DONE" % CFG["marker_phase"], flush=True)
print(CFG["marker_devices"] + json.dumps(["npu:%d" % dev_index]), flush=True)
'''


_ACLNN_BASELINE_WRAPPER = r'''# OpRunway perf wrapper · CANN 内置 ACLNN baseline —— 自动生成，勿手改
import ctypes, json, sys
from pathlib import Path

CFG = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
sys.path.insert(0, CFG["runtime_root"])

from aclnn_runtime import aclnn_driver as D
from aclnn_runtime import perf_msprof as P
from aclnn_runtime.aclnn_runner import AclnnRunner, AclnnSignature

caseset = json.loads(Path(CFG["caseset"]).read_text(encoding="utf-8"))
case = next(c for c in caseset["cases"] if c["id"] == CFG["case_id"])
call = D._case_call(case)
plan = P.resolve_aclnn_baseline_plan(CFG["aclnn_baseline"], call, case)
required_lib = P.cann_builtin_libopapi()

def materialize():
    all_slots = D._build_slots(call, case, CFG["work_dir"])
    by_name = {slot["name"]: slot for slot in all_slots}
    slots = []
    for spec in plan["slots"]:
        slot = dict(by_name[spec["name"]])
        if spec["name"] in plan["output_dtypes"]:
            if slot["kind"] != "out":
                raise RuntimeError(
                    "baseline output dtype override 只能用于非空 out slot: %s"
                    % spec["name"])
            slot["dtype"] = plan["output_dtypes"][spec["name"]]
        slots.append(slot)
    params = []
    for slot in slots:
        role = "out" if slot["kind"] == "out_null" else slot["kind"]
        params.append({"name": slot["name"], "role": role,
                       "ctype": "tensor" if role in ("in", "out") else slot.get("ctype"),
                       "const": True if role == "in" else False})
    return slots, AclnnSignature(op_name=plan["symbol"], params=params)

runner = AclnnRunner(device=int(CFG["device"]), required_symbol_lib=required_lib,
                     hash_symbol_libs=True)
try:
    runner._ensure_init()

    def invoke():
        slots, signature = materialize()
        runner.run(plan["symbol"], slots, signature=signature)

    print("%sWARMUP_START" % CFG["marker_phase"], flush=True)
    for _ in range(max(0, int(CFG["warmup"]))):
        invoke()
    print("%sWARMUP_DONE" % CFG["marker_phase"], flush=True)

    mstx = ctypes.CDLL("libms_tools_ext.so")
    mstx.mstxRangeStartA.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
    mstx.mstxRangeStartA.restype = ctypes.c_uint64
    mstx.mstxRangeEnd.argtypes = [ctypes.c_uint64]
    mstx.mstxRangeEnd.restype = None
    range_id = mstx.mstxRangeStartA(
        CFG["range_name"].encode("utf-8"),
        ctypes.c_void_p(runner._stream.value if runner._stream else None))
    if not range_id:
        raise RuntimeError("ctypes mstxRangeStartA returned 0 —— MSTX 未生效，测量窗不可信")
    print("%sMEASURE_START" % CFG["marker_phase"], flush=True)
    try:
        for _ in range(max(1, int(CFG["repeat"]))):
            invoke()
    finally:
        mstx.mstxRangeEnd(range_id)
    print("%sMEASURE_DONE" % CFG["marker_phase"], flush=True)
finally:
    runner.close(raise_on_error=True)

print(CFG["marker_devices"] + json.dumps(["npu:%d" % int(CFG["device"])]), flush=True)
print(CFG["marker_provenance"] + json.dumps(runner.runtime_provenance(), ensure_ascii=False),
      flush=True)
'''


_BASELINE_WRAPPER = r'''# OpRunway perf wrapper · baseline(msprof CLI + ctypes MSTX) —— 由 perf_msprof 生成，勿手改
# 2026-07-26 A3 / CANN 9.0.1 真机坐实：torch_npu 的 MSTX Python 包装会返回 rid=0，
# 但 cannbot 实际采用的 libms_tools_ext.so C API 在同一 msprof 进程中可产有效 device MSTX 窗。
import ctypes, json, sys
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
plan = P.resolve_torch_baseline_plan(CFG["torch_baseline"], call, case)
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
        return fn(*args, **kwargs)

last = None
print("%sWARMUP_START" % CFG["marker_phase"], flush=True)
args, kwargs = materialize()
for _ in range(max(0, int(CFG["warmup"]))):
    last = invoke(args, kwargs)
torch.npu.synchronize()
print("%sWARMUP_DONE" % CFG["marker_phase"], flush=True)

# 测量前**重新物化新鲜输入**：in-place / 有状态算子不得把 warmup 的改动带进被测窗。
args, kwargs = materialize()
torch.npu.synchronize()

mstx = ctypes.CDLL("libms_tools_ext.so")
mstx.mstxRangeStartA.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
mstx.mstxRangeStartA.restype = ctypes.c_uint64
mstx.mstxRangeEnd.argtypes = [ctypes.c_uint64]
mstx.mstxRangeEnd.restype = None
stream_ptr = int(torch.npu.current_stream().npu_stream)
range_id = mstx.mstxRangeStartA(
    CFG["range_name"].encode("utf-8"), ctypes.c_void_p(stream_ptr))
if not range_id:
    # 缺窗即 fail-closed，绝不拿整进程 kernel 当测量窗。
    raise RuntimeError("ctypes mstxRangeStartA returned 0 —— MSTX 未生效，测量窗不可信")
print("%sMEASURE_START" % CFG["marker_phase"], flush=True)
try:
    for _ in range(max(1, int(CFG["repeat"]))):
        last = invoke(args, kwargs)
    torch.npu.synchronize()
finally:
    mstx.mstxRangeEnd(range_id)
print("%sMEASURE_DONE" % CFG["marker_phase"], flush=True)

def devices(value):
    if hasattr(value, "device"):
        return [str(value.device)]
    if isinstance(value, (list, tuple)):
        return [d for item in value for d in devices(item)]
    if isinstance(value, dict):
        return [d for item in value.values() for d in devices(item)]
    return []

print(CFG["marker_devices"] + json.dumps(sorted(set(devices(last)))), flush=True)
'''


def _run_msprof(wrapper_path, cfg_path, out_dir, *, env=None, timeout_s=120):
    """跑一轮 msprof CLI（**必带 `--ai-core=off`**，§9.7 C）。返回 `(prof_dir|None, rc, output, cmd)`。

    ⚠ `--ai-core` 默认 on 会让 Sort(MIX_AIV) 虚高 3.75×、每次调用总和虚高 2.0×——这行参数不是可选项。
    """
    os.makedirs(out_dir, exist_ok=True)
    cmd = ["msprof", f"--output={out_dir}", *MSPROF_EXTRA_ARGS,
           sys.executable, str(wrapper_path), str(cfg_path)]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        start_new_session=True)
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate()
    output = (stdout or "") + (stderr or "")
    if timed_out:
        output += f"\n[OPRUNWAY_SIDE_TIMEOUT] msprof side exceeded {timeout_s}s\n"
        returncode = 124
    else:
        returncode = proc.returncode
    profs = sorted(Path(out_dir).glob("PROF_*"))
    return (str(profs[-1]) if profs else str(out_dir)), returncode, output, cmd


def baseline_env_without_dut(dut_vendor_root, env=None):
    """返回精确移除本次 DUT vendor 路径后的 baseline 子进程环境与审计信息。

    双边共享设备、输入和 profiler 口径，但 baseline 不能继承 custom OPP 覆盖层；否则系统
    ``torch_npu`` / CANN 内置 ACLNN 可能解析到 DUT 的同名内部 op。这里只移除 vendor
    ``set_env.bash`` 注入到 ``ASCEND_CUSTOM_OPP_PATH`` 与 ``LD_LIBRARY_PATH`` 的精确路径项，
    其它系统或用户路径原样保留。缺绝对 vendor 根时 fail-closed。
    """
    if not isinstance(dut_vendor_root, str) or not dut_vendor_root.strip():
        raise PerfCollectError("baseline 环境隔离缺 dut_vendor_root——无法精确移除 DUT OPP 路径")
    root = os.path.normpath(dut_vendor_root.strip())
    if not os.path.isabs(root):
        raise PerfCollectError(f"dut_vendor_root 须为绝对路径，得 {dut_vendor_root!r}")
    targets = {
        "ASCEND_CUSTOM_OPP_PATH": {root},
        "LD_LIBRARY_PATH": {os.path.normpath(os.path.join(root, "op_api", "lib"))},
    }
    clean = dict(os.environ if env is None else env)
    audit = {"dut_vendor_root": root, "removed": {}}
    for key, remove_set in targets.items():
        raw = clean.get(key)
        if raw is None:
            audit["removed"][key] = []
            continue
        kept, removed = [], []
        for entry in raw.split(os.pathsep):
            normalized = os.path.normpath(entry) if entry else entry
            if entry and normalized in remove_set:
                removed.append(entry)
            else:
                kept.append(entry)
        if kept:
            clean[key] = os.pathsep.join(kept)
        else:
            clean.pop(key, None)
        audit["removed"][key] = removed
    return clean, audit


def collector_for(side):
    """side → live 采集入口。双边统一 msprof CLI；未知 side fail-closed。"""
    if side not in ("custom", "baseline"):
        raise PerfCollectError(f"未知性能采集 side={side!r}")
    return COLLECTOR_MSPROF_CLI


def _keep_prof():
    """根盘只剩 41G（§9.7 环境更正）→ 解析完即删 prof 产物；`OPRUNWAY_PERF_KEEP_PROF=1` 可保留。"""
    return os.environ.get("OPRUNWAY_PERF_KEEP_PROF") == "1"


def _marker_json(output, marker):
    """取 wrapper 输出的最后一条 JSON marker；畸形则返回 None（调用方 fail-closed 处理）。"""
    found = None
    for line in (output or "").splitlines():
        if line.startswith(marker):
            try:
                found = json.loads(line[len(marker):])
            except json.JSONDecodeError:
                return None
    return found


def _measure_side_once(*, side, case, caseset_path, work_dir, cfg_extra, warmup, repeat, device,
                       scratch_dir, detect_hybrid, collector=None, baseline_kind="torch_npu",
                       side_timeout_s=120, custom_kind="aclnn_py"):
    """采集一侧（custom / baseline）一个 case → `{"behavior","us","scope","execution_path","collection",...}`。

    流程：生成 wrapper + cfg → 按 side 选采集入口跑 → 解析 MSTX 窗 → 窗内 kernel 聚合 → 五分类 → 清产物。
    任一步失败一律落成 behavior（`execution_failed` / `no_device_kernel_observed`），**绝不返回编的数**。
    """
    _require_real_gate()
    cid = case["id"]
    collector = collector or collector_for(side)
    range_name = range_name_for(cid, side)
    if side == "custom":
        if custom_kind == "aclnn_py":
            template = _CUSTOM_WRAPPER
        elif custom_kind == "cpp_extension":
            template = _CPP_EXTENSION_WRAPPER
        else:
            raise PerfCollectError(
                f"未知 custom_kind={custom_kind!r}——fail-closed")
    elif baseline_kind == "torch_npu":
        template = _BASELINE_WRAPPER
    elif baseline_kind == "aclnn_builtin":
        template = _ACLNN_BASELINE_WRAPPER
    else:
        raise PerfCollectError(f"未知 baseline_kind={baseline_kind!r}——fail-closed")
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
           "marker_prof_dir": MARKER_PROF_DIR,
           "marker_provenance": MARKER_RUNTIME_PROVENANCE}
    cfg.update(cfg_extra or {})
    cfg_path = side_dir / "_cfg.json"
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")

    if collector != COLLECTOR_MSPROF_CLI:
        raise PerfCollectError(
            f"live 性能采集只允许 {COLLECTOR_MSPROF_CLI!r}，得 collector={collector!r}")
    subprocess_env = None
    environment_isolation = None
    if side == "baseline" and cfg.get("exclude_dut_vendor_root") is not None:
        subprocess_env, environment_isolation = baseline_env_without_dut(
            cfg.get("exclude_dut_vendor_root"))
    prof_dir, returncode, output, command = _run_msprof(
        wrapper_path, cfg_path, prof_root, env=subprocess_env, timeout_s=side_timeout_s)
    measurement = None
    host_transfer = None
    if prof_dir is not None:
        # 2026-07-26 真机坐实 cannbot 同款 CSV 路线；显式钉 route，防 PROF 同时带 db 时
        # 又优先选到数值 TASK.taskType，重现 unknown_task_type_in_window。
        window, window_err = parse_measurement_window(
            prof_dir, range_name, route=ROUTE_CSV)
        if window_err is not None:
            measurement = {"us": None, "kernel_name": None, "execution_path": None,
                           "breakdown": [], "device_memcpy_only_us": None,
                           "route": ROUTE_CSV, "observed_task_types": {},
                           "window_wall_us": None,
                           "unresolved_task_type_ids": [], "task_type_dict_sources": [],
                           "error": window_err}
        else:
            measurement = parse_kernel_measurement(prof_dir, repeat=repeat,
                                                   measurement_window=window,
                                                   route=ROUTE_CSV)
        if detect_hybrid:
            host_transfer = parse_host_transfer_evidence(
                prof_dir, case, repeat=max(0, int(warmup)) + max(1, int(repeat)),
                route=ROUTE_CSV)
    behavior, detail = classify_behavior(returncode=returncode, output=output,
                                         measurement=measurement, host_transfer=host_transfer,
                                         detect_hybrid=detect_hybrid)
    detail["command"] = command
    detail["prof_dir"] = prof_dir
    detail["collector"] = collector
    detail["output_tail"] = (output or "")[-1500:]
    if environment_isolation is not None:
        detail["environment_isolation"] = environment_isolation
    if not _keep_prof():
        shutil.rmtree(prof_root, ignore_errors=True)     # 根盘仅剩 41G，别堆 profiling 产物
        detail["prof_dir_removed"] = True
    timed = behavior in TIMED_BEHAVIORS
    result = {"behavior": behavior,
              "us": (measurement or {}).get("us") if timed else None,
              "scope": TIMING_SCOPE if timed else None,
              "execution_path": (measurement or {}).get("execution_path"),
              "kernel_name": (measurement or {}).get("kernel_name"),
              "breakdown": (measurement or {}).get("breakdown") or [],
              "collection": collection_config(collector=collector, warmup=warmup, repeat=repeat),
              "detail": detail}
    if side == "baseline" and baseline_kind == "aclnn_builtin":
        provenance = _marker_json(output, MARKER_RUNTIME_PROVENANCE)
        if provenance is None:
            result["behavior"] = BEHAVIOR_FAILED
            result["us"] = None
            result["scope"] = None
            result["detail"]["note"] = (
                "CANN 内置 ACLNN baseline 缺 required_symbol_lib provenance marker——"
                "无法证明直接调用任务书指定库，fail-closed")
        else:
            result["runtime_provenance"] = provenance
    return result


_RETRYABLE_EVIDENCE_ERRORS = frozenset({
    ERR_NO_PROF_DATA,
    ERR_NO_MSTX_CSV,
    ERR_MSTX_RANGE_NOT_FOUND,
})


def _retryable_evidence_failure(result):
    """仅识别 profiler 证据缺失；DUT/基线执行错误和性能结果绝不重试。"""
    return (
        isinstance(result, dict)
        and result.get("behavior") == BEHAVIOR_FAILED
        and isinstance(result.get("detail"), dict)
        and result["detail"].get("returncode") == 0
        and result["detail"].get("parse_error") in _RETRYABLE_EVIDENCE_ERRORS
    )


def measure_side(*, side, case, caseset_path, work_dir, cfg_extra, warmup, repeat, device,
                 scratch_dir, detect_hybrid, collector=None, baseline_kind="torch_npu",
                 side_timeout_s=120, custom_kind="aclnn_py"):
    """采集一侧，且只对 profiler 证据缺失做有界重试。

    每次 attempt 使用独立目录；首次失败不会被吞掉，而是随最终结果保存在
    ``detail.attempts``。环境变量 ``OPRUNWAY_PERF_EVIDENCE_RETRIES`` 表示首次采集后的
    额外尝试数，默认 1，最大 3。非整数或越界值 fail-closed。
    """
    raw_retries = os.environ.get("OPRUNWAY_PERF_EVIDENCE_RETRIES", "1")
    try:
        retries = int(raw_retries)
    except ValueError as exc:
        raise PerfCollectError(
            "OPRUNWAY_PERF_EVIDENCE_RETRIES 必须是 0..3 的整数") from exc
    if retries < 0 or retries > 3:
        raise PerfCollectError("OPRUNWAY_PERF_EVIDENCE_RETRIES 必须是 0..3 的整数")

    attempts = []
    selected = None
    for index in range(retries + 1):
        attempt_root = Path(scratch_dir) / f"attempt-{index + 1}"
        result = _measure_side_once(
            side=side, case=case, caseset_path=caseset_path, work_dir=work_dir,
            cfg_extra=cfg_extra, warmup=warmup, repeat=repeat, device=device,
            scratch_dir=attempt_root, detect_hybrid=detect_hybrid, collector=collector,
            baseline_kind=baseline_kind, side_timeout_s=side_timeout_s,
            custom_kind=custom_kind)
        attempts.append({
            "attempt": index + 1,
            "behavior": result.get("behavior"),
            "us": result.get("us"),
            "scope": result.get("scope"),
            "returncode": (result.get("detail") or {}).get("returncode"),
            "parse_error": (result.get("detail") or {}).get("parse_error"),
            "prof_dir": (result.get("detail") or {}).get("prof_dir"),
            "prof_dir_removed": (result.get("detail") or {}).get("prof_dir_removed", False),
            "command": (result.get("detail") or {}).get("command"),
            "output_tail": (result.get("detail") or {}).get("output_tail"),
        })
        selected = result
        if not _retryable_evidence_failure(result):
            break

    detail = selected.setdefault("detail", {})
    detail["attempts"] = attempts
    detail["attempt_count"] = len(attempts)
    detail["selected_attempt"] = len(attempts)
    detail["retry_policy"] = (
        "profiler_evidence_missing_only; fresh_output_dir_per_attempt; "
        "first_non_retryable_result_selected")
    return selected


def _collect_document(*, op, warmup, repeat, device, side_timeout_s, baseline_kind,
                      custom_kind, custom_provenance,
                      records, skipped, planned_cases, complete):
    return {
        "op": op,
        "scope": TIMING_SCOPE,
        "warmup": warmup,
        "repeat": repeat,
        "device": device,
        "side_timeout_s": side_timeout_s,
        "collection": {
            "custom": collection_config(
                collector=collector_for("custom"), warmup=warmup, repeat=repeat),
            "baseline": collection_config(
                collector=collector_for("baseline"), warmup=warmup, repeat=repeat),
        },
        "baseline_source": baseline_kind,
        "custom_kind": custom_kind,
        "custom_provenance": custom_provenance,
        "records": records,
        "skipped": skipped,
        "collection_checkpoint": {
            "complete": bool(complete),
            "completed": len(records),
            "planned": len(planned_cases),
            "planned_case_ids": list(planned_cases),
        },
    }


def _write_collect_checkpoint(path, doc):
    """每 case 原子落盘；进程被终止时也保留已完成记录，且不会留下半截 JSON。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)


def collect(caseset_path, work_dir, plan, out_path, *, scratch_dir=None):
    """容器内主入口：按 plan 逐 case 采双边 → 落 `perf_collect.json`（**只有数与行为，无裁决**）。

    `plan`（由 OpRunway 侧据 spec 组好、随部署上送）::

        {"op": "<Op>", "warmup": 5, "repeat": 20, "device": 0,
         "op_dir": "<aclnn 头目录>",              # 可选，缺省走 driver 的 env 探测
         "allow_builtin_symbols": false,          # 可选，缺省 false = 严格档（同精度通路口径）
         "dut_lib": "<.../op_api/lib/libcust_opapi.so>",   # 本次 DUT；严格档必给其一
         "dut_vendor_root": "<vendor 内容根>",     #   （或退 adapter 的 vendor_dir + vendor_name）
         "baseline": "torch_npu" | "aclnn_builtin",
         "torch_baseline": {"api","positional","keyword"},       # baseline=torch_npu
         "aclnn_baseline": {"library","variants"},               # baseline=aclnn_builtin
         "cases": ["<case id>", ...],             # 已过精度先筛的 case
         "skipped": [{"case_id","reason"}]}

    ⚠ `device` **必须显式给**：容器内 `device_count()=16`（§9.7 环境更正），默认 0 就是在猜卡。
    ⚠ 严格档（缺省）**必须**能定出 DUT（见 :func:`resolve_plan_dut_lib`），定不出即 fail-closed
      ——性能侧与精度侧同口径钉死「测的就是本次 build 出的那个 so」，不许退回宽松档凑数。
    """
    _require_real_gate()
    caseset = json.loads(Path(caseset_path).read_text(encoding="utf-8"))
    by_id = {c["id"]: c for c in caseset.get("cases", []) if isinstance(c, dict) and c.get("id")}
    warmup = int(plan.get("warmup", DEFAULT_WARMUP))
    repeat = int(plan.get("repeat", DEFAULT_REPEAT))
    side_timeout_s = plan.get("side_timeout_s", 120)
    if (isinstance(side_timeout_s, bool) or not isinstance(side_timeout_s, int)
            or side_timeout_s < 30 or side_timeout_s > 3600):
        raise PerfCollectError(
            f"perf plan 的 side_timeout_s 须为 30..3600 秒整数，得 {side_timeout_s!r}")
    if plan.get("device") is None:
        raise PerfCollectError(
            "perf plan 缺 device —— 容器内 device_count=16（§9.7），采集卡号不许默认/猜（fail-closed）")
    device = int(plan["device"])
    # 洞 2：性能侧默认**严格档**，与精度通路（aclnn_driver 默认严格）同口径——否则可能
    # 「精度验 custom vendor、性能测 CANN 内置同名实现」。plan 的 allow_builtin_symbols
    # = aclnn_driver 的 --allow-builtin-symbols，同一开关同一语义。
    # ⚠ 取值走 plan_bool（只认真 bool）——**别退回 `bool(...)`**：plan 里写字符串 "false"/"0"
    # 会被判成开，硬门就这么静默关了。
    custom_kind = plan.get("custom_kind") or "aclnn_py"
    if custom_kind not in ("aclnn_py", "cpp_extension"):
        raise PerfCollectError(
            f"perf plan custom_kind 须为 aclnn_py/cpp_extension，得 {custom_kind!r}")
    strict_custom_vendor = not plan_bool(plan, "allow_builtin_symbols")
    # 严格档还得知道「本次该绑哪个 so」（runner 改动⑪）：DUT 从 plan 一路传到 wrapper 的 CFG，
    # 再进 AclnnRunner(dut_lib=...)。定不出即 fail-closed，绝不默默用宽松档。
    dut_lib = (resolve_plan_dut_lib(plan, strict=strict_custom_vendor)
               if custom_kind == "aclnn_py" else None)
    baseline_kind = plan.get("baseline")
    if baseline_kind not in ("torch_npu", "aclnn_builtin"):
        raise PerfCollectError(
            f"perf plan baseline 须为 torch_npu 或 aclnn_builtin，得 {baseline_kind!r}")
    torch_baseline = plan.get("torch_baseline")
    aclnn_baseline = plan.get("aclnn_baseline")
    scratch = scratch_dir or tempfile.mkdtemp(prefix="oprunway-perf-")
    records = []
    planned_cases = plan.get("cases") or []
    for completed, cid in enumerate(planned_cases, start=1):
        case = by_id.get(cid)
        if case is None:
            raise PerfCollectError(f"plan 里的 case_id={cid!r} 不在 caseset 中——fail-closed")
        custom_cfg = ({"op_dir": plan.get("op_dir"),
                       "strict_custom_vendor": strict_custom_vendor,
                       "dut_lib": dut_lib}
                      if custom_kind == "aclnn_py"
                      else {"cpp_extension": plan.get("cpp_extension")})
        custom = measure_side(side="custom", case=case, caseset_path=caseset_path,
                              work_dir=work_dir,
                              cfg_extra=custom_cfg,
                              warmup=warmup, repeat=repeat, device=device,
                              scratch_dir=scratch, detect_hybrid=False,
                              baseline_kind=baseline_kind, side_timeout_s=side_timeout_s,
                              custom_kind=custom_kind)
        baseline_cfg = ({"torch_baseline": torch_baseline}
                        if baseline_kind == "torch_npu"
                        else {"aclnn_baseline": aclnn_baseline})
        if dut_lib is not None:
            # libcust_opapi.so 固定在 <vendor-root>/op_api/lib/；由已唯一解析的 DUT so 反推，
            # 不从可能受污染的进程环境猜 vendor 根。宽松档无 DUT 时没有 custom 路径需要移除。
            baseline_cfg["exclude_dut_vendor_root"] = str(Path(dut_lib).resolve().parents[2])
        baseline = measure_side(side="baseline", case=case, caseset_path=caseset_path,
                                work_dir=work_dir,
                                cfg_extra=baseline_cfg,
                                warmup=warmup, repeat=repeat, device=device,
                                scratch_dir=scratch,
                                detect_hybrid=(baseline_kind == "torch_npu"),
                                baseline_kind=baseline_kind, side_timeout_s=side_timeout_s)
        records.append(build_perf_record(cid, custom, baseline))
        _write_collect_checkpoint(
            out_path,
            _collect_document(
                op=plan.get("op") or caseset.get("op"),
                warmup=warmup,
                repeat=repeat,
                device=device,
                side_timeout_s=side_timeout_s,
                baseline_kind=baseline_kind,
                custom_kind=custom_kind,
                custom_provenance=(plan.get("cpp_extension")
                                   if custom_kind == "cpp_extension" else None),
                records=records,
                skipped=plan.get("skipped") or [],
                planned_cases=planned_cases,
                complete=False,
            ),
        )
        # 整轮采集受硬超时保护；逐 case flush 进度后，即使整轮被杀，诊断日志也能指出
        # 最后完成的 case，而不是只剩 PERF_FAIL 哨兵。这里只报行为/进度，不做性能裁决。
        print(json.dumps({
            "oprunway_perf_progress": {
                "completed": completed,
                "total": len(planned_cases),
                "case_id": cid,
                "custom_behavior": custom.get("behavior"),
                "baseline_behavior": baseline.get("behavior"),
            }
        }, ensure_ascii=False), flush=True)
    doc = _collect_document(
        op=plan.get("op") or caseset.get("op"),
        warmup=warmup,
        repeat=repeat,
        device=device,
        side_timeout_s=side_timeout_s,
        baseline_kind=baseline_kind,
        custom_kind=custom_kind,
        custom_provenance=(plan.get("cpp_extension")
                           if custom_kind == "cpp_extension" else None),
        records=records,
        skipped=plan.get("skipped") or [],
        planned_cases=planned_cases,
        complete=True,
    )
    _write_collect_checkpoint(out_path, doc)
    return doc


def main(argv=None):
    parser = __import__("argparse").ArgumentParser(
        description="perf_msprof：容器内 kernel-only 性能采集（custom ctypes-aclnn vs spec 指定基线）")
    parser.add_argument("caseset", help="caseset.json 路径")
    parser.add_argument("plan", help="perf_plan.json 路径（op/warmup/repeat/device/baseline/cases）")
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
