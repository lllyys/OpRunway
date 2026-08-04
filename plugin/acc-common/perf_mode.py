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
import re

_HEX64 = re.compile(r"[0-9a-f]{64}\Z")

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

#: `measure_only` 下 `spec.perf` **允许出现**的字段全集（只与「采什么、怎么采、选哪些 case」有关）。
#: ⚠ 只靠上面那张 5 项 denylist 是 fail-open：拼错的判据字段（`target_ration`）、别的仓自造的
#: 比较项、将来新增的对照配置，都会因为「不在 denylist 里」被静默接受，而报告读者只会看到
#: 一个「只测不比」的口径。故改成**白名单**：词表之外一律 fail-closed。
#: `_` 开头的键是本仓通行的行内注释约定（无任何消费方读它），显式放行。
_MEASURE_ONLY_ALLOWED = frozenset({
    "mode", "measure_only_authorization",
    "case_source", "case_selection", "shape_classification",
    "warmup", "repeat", "side_timeout_s",
})

#: `measure_only` 唯一被授权的两种任务书性能要求（AGENTS.md §5.10 逐字两条）。
#: 任务书**要求比值裁决**却声明 measure_only，属越权放松，不在此列。
GROUND_NO_PERF_REQUIREMENT = "no_perf_requirement"
GROUND_GPU_COMPARISON = "gpu_comparison"
MEASURE_ONLY_GROUNDS = (GROUND_NO_PERF_REQUIREMENT, GROUND_GPU_COMPARISON)

#: 授权事实的必填字段。与 `golden.authorization` 同一套锚（cite + quote + 任务书快照指纹），
#: 让「本轮为什么可以只测不比」成为**可机核**的事实，而不是 spec 的一句自报。
_AUTH_REQUIRED = ("taskdoc_requirement", "cite", "quote", "taskdoc_snapshot_sha256")

#: 缺席哨兵：区分「字段根本没写」与「显式写了一个坏值」。
#: 两者用 `.get()` 都得到 `None`，于是「显式写错」会被当成「没声明」→ 静默降级为缺省档。
_ABSENT = object()

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


def measure_only_authorization(perf):
    """校 `perf.measure_only_authorization` → 归一化后的授权事实；缺/坏一律 fail-closed。

    ⚠ 这是 `measure_only` 这个**宽档**的唯一入口条件：口径能不能只测不比，由**任务书**说了算，
    不由待验 spec 自报一句 `mode` 说了算。授权必须逐条给出：
      · `taskdoc_requirement` —— 属 §5.10 两种被授权情形之一（受控词表，无缺省）；
      · `cite` / `quote`      —— 任务书里说这句话的**位置与逐字原文**；
      · `taskdoc_snapshot_sha256` —— 引文所依附的任务书快照指纹，使上面两项**可机核**。
        允许**显式** `null`（本轮没有 `task_doc.snapshot.md` 入库时的诚实表述，AGENTS.md 5.8：
        不填未经核实的 sha），但**不允许省略该键**——省略等于没人说过引文有没有锚。
        `null` 时授权仍成立，但 `taskdoc_snapshot_sha256: null` 会原样进 Task1 账本与报告，
        「本轮授权未绑快照」这件事因此是**可见的**，而不是消失的。
    """
    if not isinstance(perf, dict):
        raise PerfModeError("spec.perf 须为 object")
    auth = perf.get("measure_only_authorization", _ABSENT)
    if auth is _ABSENT:
        raise PerfModeError(
            "perf.mode='measure_only' 缺 perf.measure_only_authorization——"
            "「只测不比」是 AGENTS.md §5.10 授权的宽档，须显式绑定任务书性能要求事实"
            f"（taskdoc_requirement ∈ {list(MEASURE_ONLY_GROUNDS)} + cite + quote + "
            "taskdoc_snapshot_sha256），不接受 spec 自报即放宽。")
    if not isinstance(auth, dict):
        raise PerfModeError(
            f"perf.measure_only_authorization 须为 object，得 {type(auth).__name__}")
    missing = [k for k in _AUTH_REQUIRED if k not in auth]
    if missing:
        raise PerfModeError(f"perf.measure_only_authorization 缺必填字段 {missing}")
    extra = sorted(set(auth) - set(_AUTH_REQUIRED) - {k for k in auth if k.startswith("_")})
    if extra:
        raise PerfModeError(f"perf.measure_only_authorization 含未知字段 {extra}")
    ground = auth["taskdoc_requirement"]
    if ground not in MEASURE_ONLY_GROUNDS:
        raise PerfModeError(
            f"perf.measure_only_authorization.taskdoc_requirement={ground!r} 非受控值，"
            f"须属 {list(MEASURE_ONLY_GROUNDS)}——任务书若**要求比值裁决**，"
            "本轮就不得走 measure_only。")
    for key in ("cite", "quote"):
        value = auth[key]
        if not isinstance(value, str) or not value.strip():
            raise PerfModeError(
                f"perf.measure_only_authorization.{key} 须为非空字符串（实得 {value!r}）——"
                "无引文锚则授权不可核（fail-closed）")
    digest = auth["taskdoc_snapshot_sha256"]
    if digest is not None and (not isinstance(digest, str) or not _HEX64.fullmatch(digest)):
        raise PerfModeError(
            "perf.measure_only_authorization.taskdoc_snapshot_sha256 须为 64 位小写十六进制，"
            f"或**显式** null 表示本轮无任务书快照可绑（实得 {digest!r}）——"
            "绝不接受省略该键：省略 = 谁也不知道引文有没有锚")
    return {
        "taskdoc_requirement": ground,
        "cite": auth["cite"].strip(),
        "quote": auth["quote"],
        "taskdoc_snapshot_sha256": digest,
    }


def resolve_spec_mode(spec):
    """`spec` → 性能口径。spec **没有** `perf` 键时返回缺省档（= 与改动前逐字节同行为）。

    ⚠ 「没有 perf 键」与「perf 键在但不是 object」是两件事：后者是显式坏配置，必须报错，
    不能顺着 `not isinstance(...)` 一起降级成缺省档（那是把坏 spec 判成合法严档 spec）。

    `measure_only` 下：白名单外的任何字段、任一对照物/阈值字段、缺任务书授权 → 抛 `PerfModeError`。
    """
    if not isinstance(spec, dict):
        raise PerfModeError(f"spec 须为 JSON object，得 {type(spec).__name__}")
    perf = spec.get("perf", _ABSENT)
    if perf is _ABSENT:
        return DEFAULT_MODE
    if not isinstance(perf, dict):
        raise PerfModeError(f"spec.perf 显式存在但不是 object（实得 {perf!r}）")
    mode = normalize(perf.get("mode"))
    if mode == MODE_MEASURE_ONLY:
        present = [k for k in _MEASURE_ONLY_FORBIDDEN if k in perf]
        if present:
            raise PerfModeError(
                f"perf.mode='measure_only' 与 perf.{present} 自相矛盾："
                "声明了「只测不比」就不能再声明对照物 / 阈值 / 基线采集配置。"
                "要做比值裁决请改回 mode='ratio_gated'（或整个省略 mode 字段）。")
        unknown = sorted(k for k in perf
                         if k not in _MEASURE_ONLY_ALLOWED and not k.startswith("_"))
        if unknown:
            raise PerfModeError(
                f"perf.mode='measure_only' 下出现词表外字段 {unknown}："
                f"本档只接受 {sorted(_MEASURE_ONLY_ALLOWED)}（以及 `_` 开头的注释键）。"
                "未知字段一律 fail-closed——拼错的判据字段不能靠「不在禁用表里」被默默接受。")
        measure_only_authorization(perf)
    return mode


def policy_mode(policy):
    """`caseset.perf_case_policy` → 性能口径（门与 perf_compare 的独立读取面）。

    ⚠ 门**只信这一份**：它是 Task1 落盘、由 gate_task1 校过的产物；perf_report 自报的
    `perf_mode` 只能被拿来**交叉核对**，不能当判据（否则伪造报告可自选宽档）。
    ⚠ 非 dict **不再**静默当缺省档：账本存在却是坏结构时降级成「历史严档」看似安全，
    实则把「产物坏了」这件事吞掉了。字段真正缺失时由调用方显式选缺省档。
    """
    if not isinstance(policy, dict):
        raise PerfModeError(
            f"caseset.perf_case_policy 须为 object，得 {type(policy).__name__}——"
            "字段真正缺失时须由调用方显式选择缺省档，不在此处静默降级")
    return normalize(policy.get("mode"))


def is_measure_only(mode):
    return mode == MODE_MEASURE_ONLY
