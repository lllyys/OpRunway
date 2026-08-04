"""`spec.perf.mode` —— 性能维**口径**的唯一真源（stdlib-only、零依赖）。

背景（用户 2026-08-03 定的全局原则，已写进仓根 `AGENTS.md` §5.10）：

  任务书对性能没要求、或要求的是**与 GPU 比对** → **一律只用 msprof 采 NPU 实测性能**，
  不做 GPU 对比、不等 GPU 数据。

在此之前整套设计只有一个档：「测 NPU + 取 baseline → 算 ratio → 比 target_ratio → pass/fail」
（`spec.perf.baseline` 的受控词表 `{tbe, gpu_external, torch_npu, aclnn_builtin}` **四个全是对照物**）。
本模块把「只测不比」立成第二个档：

  · ``ratio_gated``   —— 历史行为，**缺省**（`perf.mode` 字段不存在即此档），一个字节都不变；
  · ``measure_only``  —— 只采 NPU kernel-only 实测耗时，**不采、不要、不等任何 baseline**，
                         不产 ratio、不产达标结论；性能维不贡献 pass/fail。

⚠ **`measure_only` 的含义是「不做对比」，不是「不做测量」**：每条性能 case 仍须有真实
`npu_us`，缺失即 blocked → 验收门 BLOCKED。任何让「一条 msprof 数据都没有」也能过门的实现
都是把它做成了 fail-open 开关，与初衷完全相反。

⚠ 缺省方向是**严档**（`ratio_gated` 要求 baseline + target_ratio），故「忘了声明 mode」的
失败方向是「多要一份证据」，不是「少判一道门」——fail-closed 方向正确。

四个消费方共用本模块，避免各写一份判定后漂移：
  · `gen_cases`               —— 把 mode 落进 `caseset.perf_case_policy`（门的独立读取面）；
  · `perf_compare`            —— 据 mode 走 ratio 判定 / 纯实测两条出口；
  · `run_workflow`            —— 据 mode 决定采集计划、是否取基线、终态映射；
  · `validate_acceptance_state` —— 据 caseset 落盘的 mode 复核 perf_report，不信报告自报。
"""

#: 受控词表。**别在这里加第三个值**而不同步四个消费方——门会 fail-closed 拒绝未知值。
MODE_RATIO_GATED = "ratio_gated"
MODE_MEASURE_ONLY = "measure_only"
MODES = (MODE_RATIO_GATED, MODE_MEASURE_ONLY)
#: 字段不存在 = 历史行为。
DEFAULT_MODE = MODE_RATIO_GATED

#: `measure_only` 下 perf_report 的 summary.status（"已实测、未做比值裁决"）。
#: 刻意**不叫** `ok`：`ok` 在本仓词表里意味着「逐 case 比过阈值且全达标」。
STATUS_MEASURED = "measured"

#: `measure_only` 下**必须不存在**的 `spec.perf` 字段。
#: 同时声明「只测不比」和一个对照物/阈值是自相矛盾的配置——一律 fail-closed 报错，
#: **不「忽略多余字段」**（静默忽略 = 让报告读者以为那个阈值生效过）。
#: `baseline` / `target_ratio` 是对照物与判据本身；`small_shape_exception` 是「NPU 与基线差多少
#: 算齐平」的容差（无基线即无从谈起）；`torch_baseline` / `aclnn_baseline` 是**怎么采基线**的配置。
_MEASURE_ONLY_FORBIDDEN = ("baseline", "target_ratio", "small_shape_exception",
                           "torch_baseline", "aclnn_baseline")

#: 报告/产物里逐字写明的口径声明（律令 5.8：没测的比值不能编）。
MEASURE_ONLY_NOTE = (
    "按 perf.mode=measure_only 口径：性能维只用 msprof 采 NPU kernel-only 实测耗时，"
    "**未做任何标杆对比**（无 baseline、无 ratio、无阈值），故本轮不产任何性能达标结论。")


class PerfModeError(ValueError):
    """perf.mode 配置非法。继承 ValueError，沿用各调用方既有的 ValueError 收敛路径。"""


def normalize(mode):
    """`None` → 缺省档；受控词表内原样返回；其余一律 fail-closed。"""
    if mode is None:
        return DEFAULT_MODE
    if not isinstance(mode, str) or mode not in MODES:
        raise PerfModeError(
            f"perf.mode={mode!r} 非受控值，须属 {list(MODES)}（字段省略 = {DEFAULT_MODE}）")
    return mode


def resolve_spec_mode(spec):
    """`spec` → 性能口径。spec 未声明 `perf` 时返回缺省档（= 与改动前逐字节同行为）。

    `measure_only` 下同时出现任一对照物/阈值字段 → 抛 `PerfModeError`（不忽略、不择一）。
    """
    perf = spec.get("perf") if isinstance(spec, dict) else None
    if not isinstance(perf, dict):
        return DEFAULT_MODE
    mode = normalize(perf.get("mode"))
    if mode == MODE_MEASURE_ONLY:
        present = [k for k in _MEASURE_ONLY_FORBIDDEN if k in perf]
        if present:
            raise PerfModeError(
                f"perf.mode='measure_only' 与 perf.{present} 自相矛盾："
                "声明了「只测不比」就不能再声明对照物 / 阈值 / 基线采集配置。"
                "要做比值裁决请改回 mode='ratio_gated'（或整个省略 mode 字段）。")
    return mode


def policy_mode(policy):
    """`caseset.perf_case_policy` → 性能口径（门与 perf_compare 的独立读取面）。

    ⚠ 门**只信这一份**：它是 Task1 落盘、由 gate_task1 校过的产物；perf_report 自报的
    `perf_mode` 只能被拿来**交叉核对**，不能当判据（否则伪造报告可自选宽档）。
    """
    if not isinstance(policy, dict):
        return DEFAULT_MODE
    return normalize(policy.get("mode"))


def is_measure_only(mode):
    return mode == MODE_MEASURE_ONLY
