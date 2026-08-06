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

**本模块还是 `spec.change.kind`（改动类别）的受控词表处**（2026-08-05 加）。放这里不是为了图省事：
用户 2026-08-05 的口径把「改动属哪一类」直接接到了「本轮性能怎么取证」上（见 `derive_mode`），
两者同源同一句话；分成两个模块只会让词表和它唯一的消费方隔一层、迟早漂。
⚠ `change.kind` 此前只是**文档字段、无代码消费方**（写错没人管）；本模块给了它受控词表与
fail-closed 校验，`gen_cases` 在计划期调一次，让写错的 kind 当场炸而不是安静地不起作用。
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

#: `measure_only` 被授权的三种情形（AGENTS.md §5.10 逐字三条）。
#: 前两条是**任务书条款**支线，第三条是**本轮改动类别**支线（用户 2026-08-05 明示）——两者不同源，
#: 见 `derive_mode` 的分支说明。三条**授权强度完全相同**：都要 cite + quote + taskdoc_snapshot_sha256 锚。
GROUND_NO_PERF_REQUIREMENT = "no_perf_requirement"
GROUND_GPU_COMPARISON = "gpu_comparison"
#: 「对原算子新增 dtype 支持 / 扩展 shape·rank / 开发新算子」三类改动统一按「无性能对比要求」办
#: （用户 2026-08-05 逐字口径）。
#: ⚠ **它不是「任务书没写比值条款」的同义词**——恰恰相反，它唯一非冗余的适用面就是
#:   「任务书写了比值条款、但本轮改动属这三类」。故这里**刻意不设**「与比值条款互斥即 fail-closed」
#:   的护栏（那会把这个 ground 掐死）。处置方式与 §5.10 对 GPU 条款的处置**逐字同形**：
#:   原条款强制记进 `task_pr_gaps` 标「未验收」，本轮仍只产 msprof 绝对耗时，
#:   **禁止取 baseline、禁止算 ratio、禁止任何达标宣称**——是「换取证方式」，不是「条款作废」。
GROUND_CHANGE_CLASS_NO_PERF_COMPARISON = "change_class_no_perf_comparison"
MEASURE_ONLY_GROUNDS = (GROUND_NO_PERF_REQUIREMENT, GROUND_GPU_COMPARISON,
                        GROUND_CHANGE_CLASS_NO_PERF_COMPARISON)

#: `spec.change.kind` 受控词表。取自 `skills/acc-spec/references/taskdoc-to-spec.md` 的既有七值，
#: **本轮新增 `extend_shape`**（用户口径里的「扩展 shape/rank」原先在词表里没有格子，只能被硬塞进
#: `semantic`——那会让 `derive_mode` 的改动类别支线判不出来）。
#: ⚠ 词表外取值 → fail-closed；**整个 `change` 字段省略 = 未声明**（catlass demo spec 就没有这个键，
#:   它不是从任务书 ↔ PR 对应来的），未声明时 `derive_mode` 的改动类别支线**不出结论**，不兜任何默认。
CHANGE_KIND_REWRITE_TBE = "rewrite_tbe"
CHANGE_KIND_ADD_DTYPE = "add_dtype"
CHANGE_KIND_EXTEND_SHAPE = "extend_shape"
CHANGE_KIND_ALIGN_DTYPE = "align_dtype"
CHANGE_KIND_SEMANTIC = "semantic"
CHANGE_KIND_NEW_OP = "new_op"
CHANGE_KIND_GPU_PORT = "gpu_port"
CHANGE_KIND_BUGFIX = "bugfix"
CHANGE_KINDS = (CHANGE_KIND_REWRITE_TBE, CHANGE_KIND_ADD_DTYPE, CHANGE_KIND_EXTEND_SHAPE,
                CHANGE_KIND_ALIGN_DTYPE, CHANGE_KIND_SEMANTIC, CHANGE_KIND_NEW_OP,
                CHANGE_KIND_GPU_PORT, CHANGE_KIND_BUGFIX)

#: 用户 2026-08-05 口径里「统一认为无性能对比要求」的**改动类别**三项（第四项是任务书条款，不在此表）。
NO_PERF_COMPARISON_CHANGE_KINDS = (CHANGE_KIND_ADD_DTYPE, CHANGE_KIND_EXTEND_SHAPE,
                                   CHANGE_KIND_NEW_OP)

#: 这三类改动的接入形态：官方 C++ Extension（`torch.ops` 桥）。见 `derive_runner_form`。
RUNNER_FORM_CPP_EXTENSION = "cpp_extension"

#: 「任务书把 GPU 指定为性能标杆」的**结构信号**（ratio_gated 形态下）：`spec.perf.baseline` 的两个 GPU 值。
#: measure_only 下 `baseline` 必须缺席，故那时这一支只能读已锚定的 `measure_only_authorization`。
_GPU_BASELINE_VALUES = ("gpu", "gpu_external")

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
    """受控词表内原样返回；其余（含显式 `null`）一律 fail-closed。

    ⚠ **显式 `null` 不再等同于「字段缺失」**（审计 Medium#27）：两者用 `.get()` 都得到 `None`，
    旧实现把它们合并后返回 `DEFAULT_MODE`——于是一份写了 `"mode": null` 的坏 spec / 坏账本
    会被静默当成「没声明 → 缺省严档」，而**「产物里显式写了个坏值」这件事被吞掉了**。
    这与本模块 `_ABSENT` 哨兵要解决的是同一类问题（见 `measure_only_authorization` 的
    `taskdoc_snapshot_sha256`：显式 null 合法、省略键不合法——两者语义必须分得开）。

    「字段真正缺失 → 缺省档」的判断归调用方：调用方用 `_ABSENT` 哨兵区分缺键与坏值
    （见 `resolve_spec_mode` / `policy_mode`），本函数只负责「给我一个值，我判它合不合法」。
    """
    if not isinstance(mode, str) or mode not in MODES:
        raise PerfModeError(
            f"perf.mode={mode!r} 非受控值，须属 {list(MODES)}（字段**省略**才等于 {DEFAULT_MODE}；"
            "显式 null / 空串 / 未知字符串一律 fail-closed，不当成未声明)")
    return mode


def measure_only_authorization(perf):
    """校 `perf.measure_only_authorization` → 归一化后的授权事实；缺/坏一律 fail-closed。

    ⚠ 这是 `measure_only` 这个**宽档**的唯一入口条件：口径能不能只测不比，由**任务书 / 本轮改动类别**
    这两类可核事实说了算，不由待验 spec 自报一句 `mode` 说了算。授权必须逐条给出：
      · `taskdoc_requirement` —— 属 §5.10 三种被授权情形之一（受控词表，无缺省）。
        走 `change_class_no_perf_comparison` 时**还要**与 `spec.change.kind` 对得上——那道交叉核在
        `resolve_spec_mode`（本函数只拿得到 `perf`，看不见 `change`）；
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
            f"须属 {list(MEASURE_ONLY_GROUNDS)}——「只测不比」只有 §5.10 逐字列出的这三种情形，"
            "自造第四种即越权放松。")
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
    # 审计 Medium#27：只有**键缺失**才落缺省档；`"mode": null` 是显式坏值，交给 normalize 拒。
    raw_mode = perf.get("mode", _ABSENT)
    mode = DEFAULT_MODE if raw_mode is _ABSENT else normalize(raw_mode)
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
        auth = measure_only_authorization(perf)
        # 改动类别支线的**机器锚**：声称「本轮改动属那三类所以不比性能」，就必须真有那三类的
        # `change.kind`。缺这道核，这个 ground 就退化成一句谁都能写的自报（另两个 ground 的锚是
        # cite/quote 指向的任务书原文，人核得动；这一个的事实全在 spec 自己身上，必须机核）。
        if auth["taskdoc_requirement"] == GROUND_CHANGE_CLASS_NO_PERF_COMPARISON:
            kind = normalize_change_kind(spec)
            if kind not in NO_PERF_COMPARISON_CHANGE_KINDS:
                raise PerfModeError(
                    f"perf.measure_only_authorization.taskdoc_requirement="
                    f"'{GROUND_CHANGE_CLASS_NO_PERF_COMPARISON}' 要求 spec.change.kind 属 "
                    f"{list(NO_PERF_COMPARISON_CHANGE_KINDS)}，实得 {kind!r}——"
                    "「本轮改动属那三类」是可机核的事实，对不上就不是这个 ground 能授权的场景。")
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
    # 审计 Medium#27：与 `resolve_spec_mode` 同一口径——缺键才落缺省档，显式 null 是坏账本。
    raw_mode = policy.get("mode", _ABSENT)
    return DEFAULT_MODE if raw_mode is _ABSENT else normalize(raw_mode)


def is_measure_only(mode):
    return mode == MODE_MEASURE_ONLY


# ══════════════ 改动类别（`spec.change.kind`）→ 本轮口径的**派生建议** ══════════════
# ⚠ 下面三个函数产的都是**建议值**，不是门。真正的门仍是 `resolve_spec_mode` + `measure_only_authorization`
#   （spec 落盘的 `perf.mode` 与其授权事实）。分开的理由：派生跑在**产 spec 的那一侧**（acc-spec），
#   而门跑在**消费 spec 的那一侧**（gen_cases / run_workflow / 验收门）；把两者合成一个函数，
#   等于让待验 spec 自己决定自己该被怎么判。

def normalize_change_kind(spec):
    """`spec.change.kind` → 受控词表值；**整个 `change` 键省略 → `None`**（未声明，不兜默认）。

    `change` 在但不是 object、缺 `kind`、`kind` 非受控值 → 一律 fail-closed。
    ⚠ 「省略」与「写坏」必须分得开（同本模块 `_ABSENT` 哨兵要解决的那类问题）：省略是 catlass demo
    那种合法场景（无任务书 ↔ PR 对应），写坏则是一份该被看见的坏 spec。
    """
    if not isinstance(spec, dict):
        raise PerfModeError(f"spec 须为 JSON object，得 {type(spec).__name__}")
    change = spec.get("change", _ABSENT)
    if change is _ABSENT:
        return None
    if not isinstance(change, dict):
        raise PerfModeError(f"spec.change 显式存在但不是 object（实得 {change!r}）")
    kind = change.get("kind", _ABSENT)
    if kind is _ABSENT:
        raise PerfModeError(
            f"spec.change 存在却缺 kind——改动类别是受控词表 {list(CHANGE_KINDS)}，"
            "不许只写 note 就算交代过了（整个 change 键省略才是合法的『未声明』）。")
    if not isinstance(kind, str) or kind not in CHANGE_KINDS:
        raise PerfModeError(
            f"spec.change.kind={kind!r} 非受控值，须属 {list(CHANGE_KINDS)}——"
            "词表外一律 fail-closed，不自造第九类（`extend_shape` 已于 2026-08-05 补进词表，"
            "扩 shape/rank 别再硬塞 semantic）。")
    return kind


def derive_runner_form(spec):
    """改动类别 → 建议的 `runner_form`；不属那三类则返回 `None`（= 本规则不出结论，按既有通路走）。

    用户 2026-08-05 口径：新增 dtype / 扩 shape·rank / 新算子这三类改动「明确使用 torch 封装接入」，
    即官方 C++ Extension（`torch.ops` 桥）形态。

    ⚠ **接口预检（`preflight_aclnn`）只决定「支持 or BLOCKED」，绝不得据此静默改选 `aclnn_py`**：
      换通路会同时换掉 DUT 形态与性能对照物，那是验收口径的改动，不是工具的自适应。
    ⚠ `run_workflow` 仍**只从 `spec.runner_form` 派生 mode**（单一真源不变）；本函数是产 spec 那一侧
      的建议，落地方式是把它**写进 spec**，不是在编排层临时算一个。
    """
    kind = normalize_change_kind(spec)
    if kind in NO_PERF_COMPARISON_CHANGE_KINDS:
        return RUNNER_FORM_CPP_EXTENSION
    return None


def derive_mode(spec):
    """据**两条不同源**的支线派生本轮性能口径的建议值，返回可直接落进账本的 dict。

    用户 2026-08-05 逐字口径列了四类场景，但它们**不是同一个来源**——这一点必须在实现里分开，
    否则会写成「四类 change.kind」而把第四类判丢：

      · 支线 ①（**改动类别**，源 = `spec.change.kind`，结构字段、可机核）：
        `add_dtype` / `extend_shape` / `new_op` → ground `change_class_no_perf_comparison`；
      · 支线 ②（**任务书条款**，源 = 任务书，机器只能读到 spec 里对它的**已锚定转述**）：
        - `perf.measure_only_authorization.taskdoc_requirement` ∈ {no_perf_requirement, gpu_comparison}
          —— 带 cite/quote/快照指纹的任务书条款事实（`measure_only` 下 `baseline` 必须缺席，
          这是那时唯一能读到的条款面）；
        - `perf.baseline` ∈ {gpu, gpu_external} —— ratio_gated 形态下「任务书点名 GPU 标杆」的结构信号。
        ⚠ 支线 ② **刻意不认** `change_class_no_perf_comparison`：那是支线 ① 的事实，
          在这里认它就成了自证（自己声明 → 自己派生 → 自己通过）。

    两支**取并集**：任一支成立即建议 `measure_only`；都不成立即建议缺省严档 `ratio_gated`。

    两道护栏（都 fail-closed，方向相反）：
      · **建议值为 `measure_only` ⇒ spec 必须已有合法授权**（`measure_only_authorization` 校得过、
        且其 ground 落在本次派生出的 grounds 里）。缺授权即报错，而不是静默把一份 ratio_gated 的
        spec 降成宽档——`median.spec.json` 那种「`new_op` + 任务书逐字比值条款」正是要在这里**炸出来**，
        由人决定是补授权、还是这轮就该做比值裁决；
      · **spec 自称 `measure_only` ⇒ 两支至少判出一条 ground**。一条都判不出还自称宽档的，
        是没有事实支撑的自报，同样拒。

    返回：
      ``{"mode", "grounds", "change_class": {...}, "taskdoc_clause": {...}, "authorization": {...}|None}``
      —— `grounds` 按 `MEASURE_ONLY_GROUNDS` 的稳定顺序去重，可直接进 spec 生成账本。
    """
    if not isinstance(spec, dict):
        raise PerfModeError(f"spec 须为 JSON object，得 {type(spec).__name__}")
    perf = spec.get("perf")
    perf = perf if isinstance(perf, dict) else {}

    # ── 支线 ①：改动类别 ───────────────────────────────────────────────────────
    kind = normalize_change_kind(spec)
    change_ground = (GROUND_CHANGE_CLASS_NO_PERF_COMPARISON
                     if kind in NO_PERF_COMPARISON_CHANGE_KINDS else None)
    change_branch = {
        "source": "spec.change.kind",
        "kind": kind,
        "ground": change_ground,
        "why": ("改动类别属「新增 dtype / 扩 shape·rank / 新算子」→ 按用户 2026-08-05 口径不做性能对比"
                if change_ground else
                ("spec 未声明 change（本支线不出结论）" if kind is None else
                 f"change.kind={kind!r} 不属 {list(NO_PERF_COMPARISON_CHANGE_KINDS)}，本支线不出结论")),
    }

    # ── 支线 ②：任务书条款 ─────────────────────────────────────────────────────
    clause_ground, clause_signal = None, None
    declared = perf.get("measure_only_authorization")
    if isinstance(declared, dict):
        raw = declared.get("taskdoc_requirement")
        if raw in (GROUND_NO_PERF_REQUIREMENT, GROUND_GPU_COMPARISON):
            clause_ground, clause_signal = raw, "perf.measure_only_authorization.taskdoc_requirement"
    if clause_ground is None and perf.get("baseline") in _GPU_BASELINE_VALUES:
        clause_ground, clause_signal = GROUND_GPU_COMPARISON, "perf.baseline"
    clause_branch = {
        "source": "任务书（经 spec 已锚定的转述）",
        "signal": clause_signal,
        "ground": clause_ground,
        "why": (f"{clause_signal} 表明任务书的性能条款属 {clause_ground}" if clause_ground else
                "spec 里没有任何已锚定的任务书性能条款信号，本支线不出结论"),
    }

    grounds = [g for g in MEASURE_ONLY_GROUNDS if g in {change_ground, clause_ground}]
    result = {"mode": MODE_MEASURE_ONLY if grounds else DEFAULT_MODE,
              "grounds": grounds,
              "change_class": change_branch,
              "taskdoc_clause": clause_branch,
              "authorization": None}
    if not grounds:
        # 反向护栏：spec **自称** measure_only，两支却一条授权情形都判不出来 → 拒。
        # 典型是「授权里填了 change_class_no_perf_comparison，`change.kind` 却是 bugfix」：
        # 那份 spec 自己声明的宽档没有任何本 spec 事实支撑，静默返回「建议 ratio_gated」
        # 等于把一句站不住的自报当成了「派生没意见」。
        if perf.get("mode") == MODE_MEASURE_ONLY:
            raise PerfModeError(
                f"spec 声明 perf.mode='{MODE_MEASURE_ONLY}'，但两支都判不出任何被授权情形："
                f"改动类别支线 —— {change_branch['why']}；任务书条款支线 —— {clause_branch['why']}。"
                f"宽档必须有 {list(MEASURE_ONLY_GROUNDS)} 之一的事实支撑，自报不算。")
        return result
    # 护栏：建议 measure_only ⇒ 必须已有合法授权，且授权引的 ground 就在本次派生出来的这几条里。
    try:
        auth = measure_only_authorization(perf)      # 缺 / 坏 → 当场 fail-closed
    except PerfModeError as ex:
        raise PerfModeError(
            f"本轮事实派生出 mode='{MODE_MEASURE_ONLY}'（grounds={grounds}），但 spec 没有合法授权：\n"
            f"  {ex}\n"
            "  → 要么补齐 perf.measure_only_authorization（ground + cite + quote + 快照指纹），"
            "要么说明本轮为何仍应做比值裁决。**绝不静默降档**：一份写着 target_ratio 的 spec "
            "被悄悄按「只测不比」跑掉，报告读者无从知道那道阈值根本没生效。") from ex
    if auth["taskdoc_requirement"] not in grounds:
        raise PerfModeError(
            f"perf.measure_only_authorization.taskdoc_requirement="
            f"{auth['taskdoc_requirement']!r} 不在本轮可派生的授权情形 {grounds} 内——"
            "授权引的理由必须是**本 spec 事实真支持**的那一条，不许挑一个更好写的填上。")
    result["authorization"] = auth
    return result
