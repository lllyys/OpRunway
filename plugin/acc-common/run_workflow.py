"""OpRunway 顶层编排（Layer 2 薄壳的本地驱动版）——串 Task 1→2→3。

Task 1 gen_cases → Task 2 repo_adapter + validator → Task 3 perf_compare。
stage 间只经 JSON/数据文件交接。CC/Codex/Antigravity 的薄壳只需换调用方式，核心不动。

用法：python run_workflow.py <spec.json> [--mode new_example|aclnn_py|cpp_extension|mock] [--out <dir>]
              [--source-facts <source_facts.json>]
验收通路开跑前须先建立 spec 变更收据（`spec_change_gate.py --spec … --out … --init --reason … --by …`），
之后每次改 spec 都要 `--update`；否则本模块在进 Task1 之前与写验收产物之前两处都会拒（§spec 变更门）。
省略 `--mode` 时据 `spec.runner_form` 唯一派生；
`mock` 仅本地用例链自检、精度按构造必过、非验收。
`--source-facts` 在**验收通路上必给**（缺席即拒跑）：它是三级门与 vendor build receipt 对账的
来源对照物，且会连同 `spec.json` / `golden.py` 一起 staging 进 `--out`，让验收产物目录自带
「这一轮到底验的是什么」——CP-F 因此不再需要手工 staging（详见 `_STAGED_FILES` 上方的病历）。

⚠ **验收裁决只有真机通路产得出来**（C5，用户 2026-07-22 拍板）。mock 的「NPU 输出」= `golden.copy()`
（精度按构造必过）、性能是 `_mock_us(numel)` 编的假数 + `perf_compare.mock_baseline` 的假基线——它跑出来的
「PASS」什么都不证明。历史上它却写出与真验收**同名同形**的 `acceptance.json` / `verdict.json`，那才是真危害。
现在：**非验收通路物理上不写这两个文件**，改写 `dev_run_summary.json` / `dev_precision_check.json`，
两者都带 `evidence_grade="development"` + 一句 `NON-ACCEPTANCE` 注脚（字段名照
`catlass_adapter.run_catlass_mock` 的既有口径，不另发明）。**注脚按真实原因取串**，
见 `_non_acceptance_note`——一句 mock 措辞套所有非验收产物会把真机跑说成假数，那是凭空的假话。
mock 通路本体**保留**（测试与本地演示照用），拔掉的只是「产验收裁决」这条出口。
"""
import argparse, glob, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_cases, repo_adapter, validator, perf_compare  # noqa: E402
import cpp_extension_adapter  # noqa: E402
import repro_artifacts  # noqa: E402
import render_acceptance_markdown  # noqa: E402
import validate_acceptance_state as gate  # noqa: E402
import source_facts_lookup  # noqa: E402
import spec_change_gate  # noqa: E402
import verify_aclnn_harness  # noqa: E402
import content_address  # noqa: E402
import perf_mode  # noqa: E402

# —— C5 · 验收 / 非验收两套产物的口径（唯一定义处）——————————————————————————
_DEV_GRADE = "development"              # 照 catlass_adapter.run_catlass_mock
_ACCEPTANCE_GRADE = "acceptance_candidate"   # 照 catlass_adapter.run_catlass 的真机等级
# —— 非验收产物的注脚：**按真实原因取串** ——————————————————————————————————————
# 病历（2026-08-06，aclnnRoll 试跑）：原先一句 mock 措辞套所有非验收产物，于是
# `--allow-experimental-form` 下那一轮**真机**跑被标成「NPU 输出 = golden.copy()、性能是编的假数」
# ——一句凭空的假话。读报告的人会以为这轮压根没上过真机，从而错判失败归因。
# **措辞错方向与判定错方向一样贵**：本仓不许把假数说成真机数据，同样不许把真机数据说成假数。
_NOTE_MOCK = (
    "NON-ACCEPTANCE (mock evidence)：mock 的「NPU 输出」= golden.copy()（精度按构造必过）、"
    "性能是按元素数编的假数 + 假基线 —— 本产物只证管路接通，非 NPU 验收，不得作为验收结论引用")
_NOTE_FORM = (
    "NON-ACCEPTANCE (非准入 runner_form)：数据来自真机、是真实测量值，但该 runner_form 当前"
    "不用于出验收裁决（见 AGENTS.md §4）—— 本产物只作开发级证据，不得作为验收结论引用")
_NOTE_OTHER = (
    "NON-ACCEPTANCE：本轮不产验收裁决（mode 不在验收通路，或 adapter 自报 evidence_grade="
    "development）—— 本产物只作开发级证据，不得作为验收结论引用")
#: **兼容别名**（`test_perf_compare` 拿它与 `perf_compare._NON_ACCEPTANCE_NOTE` 对标记词）。
#: ⚠ 生产代码一律走 `_non_acceptance_note()`，别再拿这一个串去套所有非验收情形——
#:   那正是上面那条病历的成因。
_NON_ACCEPTANCE_NOTE = _NOTE_MOCK
#: 「证据是编出来的」那一类 mode：evidence 由构造保证必过，与真机测量不是一回事。
#: ⚠ 本表**只用于选措辞**，不参与任何 pass/fail 判定（那是 `_acceptance_capable` 的事）。
#:   漏登记一个 mock 家族的 mode，后果是落到 `_NOTE_OTHER`——一句不声称数据真假的中性话，
#:   而**不是**把假数说成真机数据。失败方向刻意选在「少说」这边。
_MOCK_MODES = frozenset({"mock", "catlass_mock"})
# 非验收产物名：与验收产物 acceptance.json / verdict.json **物理隔离**（不同名 → 不可能被下游按老路径读走当裁决）
_DEV_SUMMARY_FILE = "dev_run_summary.json"     # ← 取代 acceptance.json
_DEV_VERDICT_FILE = "dev_precision_check.json"  # ← 取代 verdict.json
_ACCEPTANCE_FILES = ("acceptance.json", "verdict.json")
_DEV_FILES = (_DEV_SUMMARY_FILE, _DEV_VERDICT_FILE)
#: 人读交付物：`render_acceptance_markdown.write_report` 落进报告目录的三份 Markdown。
#: ⚠ 那边是**字面量**、没有可 import 的常量，这里只能照抄——`test_run_workflow_source_staging`
#:   里有一条**漂移哨**直接从那个模块的落点反查这份清单，改了名而这里没同步会红。
_REPORT_MD_FILES = ("验收报告.md", "精度失败明细.md", "性能失败明细.md")
#: 「本轮结论」的**全部消费面**：精度裁决 + 性能裁决 + 非验收那一套同位产物 + 人读报告 + 小 shape 仿真图。
#: 见 `_invalidate_stale_results`——它们在 `run()` 的**第一行**被作废，早于任何可能早退的校验。
#: ⚠ **两套产物一起清、不按 is_acceptance 二选一**：`is_acceptance` 在下游还会被 adapter 自报的
#:   evidence_grade 降级（见「只降不升」那处），按降级前的值二选一时，上一轮真机的
#:   acceptance.json 会与本轮 dev_* 并存——正是这套机制要堵的洞。
_RESULT_FILES = _ACCEPTANCE_FILES + _DEV_FILES + ("perf_report.json",) + _REPORT_MD_FILES
#: 同上，只是要按通配清（T6 小 shape 仿真图，防 stale SVG 让「有图」门误过；codex H7）。
_RESULT_GLOBS = ("perf_sim_*.svg",)

# —— CP-E 自证材料 staging：验收产物目录必须自带「这一轮到底验的是什么」——————————————————
# 病历（两条，同一个根因）：
#   ① **CP-F 跑不起来**。`precision_retest_contract` 要求 `base_artifacts.spec` 落在报告目录**之内**，
#      并把 golden 授权链锚成 `dirname(spec)/golden.py`。真机布局里 spec 在 `plugin/samples/specs/`、
#      golden 在 `<ops_root>/<op>/`，两头都不满足 → 每次跑 CP-F 都得先手工 staging。
#   ② **三级门缺对照物**。`validate_acceptance_state._gate_build_receipt_source_binding` 在
#      找不到 `source_facts.json` 时，PR 通路沿用旧行为不阻断——「收据自称 pull_request，而
#      source_facts 其实说的是 local」这种伪装**查不出来**，因为压根没有对照物。
# 两条是同一件事的两面：**验收产物目录没有自带足够的自证材料**。所以一次补齐、只用一套机制——
# 验收通路开跑前把三份**输入原件**按字节复制进 `--out`，并把 `source_facts` 的落点**显式**指给三级门。
#
# ⚠ 只对**验收通路**做（`is_acceptance`）。非验收通路物理上不产 acceptance.json，CP-F 也无从消费；
#   给它 staging 只会在 dev 目录里留下一份长得像验收输入的东西。按能力分流，非按算子/仓形态。
_STAGED_SPEC_FILE = "spec.json"
_STAGED_GOLDEN_FILE = "golden.py"
_STAGED_SOURCE_FACTS_FILE = "source_facts.json"
#: 三份 staging 产物的**统一清单**：开跑时先整体清掉上轮残留，再按本轮重新落。
#: ⚠ 清理与落盘必须共用这一份清单——漏清一项，下一轮就可能拿上一轮的 spec/golden 去配本轮的 caseset。
_STAGED_FILES = (_STAGED_SPEC_FILE, _STAGED_GOLDEN_FILE, _STAGED_SOURCE_FACTS_FILE)

# —— spec 变更门（`spec_change_gate`）在本模块的两处落点 ————————————————————————————
# 病历（2026-08-06，aclnnRoll 试跑）：跑不通就改 spec——`runner_form` 从 cpp_extension 改成 cpp、
# dtype 从任务书要求的 8 种砍到 3 种。门都工作正常、没出假 PASS，塌的是**没有任何机制记录
# 「spec 被改过、谁改的、为什么」**，于是「范围一路缩小」全程没有一处产物提到过。
#
# ⚠ 落点与准入门（`_ACCEPTANCE_RUNNER_FORMS`）**逐字同口径**：入口 + 出口两道，
#   理由也一样——只拦入口拦不住（`repo_adapter.py` 那个 `catlass_mock` 后门就是现成先例）。
#   出口那道跑在**每一处写验收产物之前**（verdict.json 与 acceptance.json 各一次），
#   堵的是「入口过了之后 spec 被换掉」——那会让产物目录里的裁决与实际驱动执行的 spec 对不上。
#
# ⚠ **被校对象恒为 `spec_path` 原件**，绝不是 `<out>/spec.json`：
#   ① 入口门跑在 staging **之前**，那时目录里躺的是**上一轮**的副本——校它等于换了 spec 也照过；
#   ② 出口门时副本虽是本轮的，但它由本函数自己写出，拿来当被校对象是自己给自己作证。
#
# ⚠ 只对**验收通路**生效（`is_acceptance`）。mock / `--allow-experimental-form` 下的
#   cpp / aclnn_py 物理上不产 acceptance.json，改 spec 缩范围在那条路上不产生假验收结论。
_SPEC_GATE_ENTRY = "① 入口门（进 Task1 之前）"
_SPEC_GATE_EXIT = "② 出口门（写验收产物之前）"
# 可能产验收裁决的**真机通路**集合：new_example（cpp runner v1）+ aclnn_py（ctypes-aclnn runner form，
# torch 对标 median 见证）。两者都产真 NPU 证据（evidence_grade=acceptance_candidate）。按**能力/形态**扩，
# 非按算子身份——aclnn_py 无 per-op runner 源、op 工程即 DUT（蓝图 §6）。
_REAL_MACHINE_MODES = frozenset({"new_example", "aclnn_py", "cpp_extension"})
_REAL_MACHINE_MODE = "new_example"      # new_example 专属预检（_ne_cfg）用；aclnn_py 有自己的 _aclnn_cfg
_RUNNER_FORM_TO_MODE = {
    "cpp": "new_example",
    "aclnn_py": "aclnn_py",
    "cpp_extension": "cpp_extension",
}

# —— 正式验收当前只走 cpp_extension ————————————————————————————————————————
# 它是**唯一跑通完整 torch_parity 矩阵**的通路（AGENTS.md §9：Median PR6429 1152 例、
# `gate.passed=true`）。另两条的真机成熟度未达同等水平：
#   · `aclnn_py`  —— 历史 Median 60/60 只证明**旧 caseset**；迁到 torch_parity + cpp_extension 后
#                    必须重跑，不得沿用旧 PASS；
#   · `cpp`（new_example）—— IsClose / Sign 坐实，但 dtype 闭环只到 fp32/fp16/bf16，
#                    int 等落 `DEFERRED_NP_BY_FORM`。
#
# ⚠ **保留映射 + 显式白名单门**，而不是直接从 `_RUNNER_FORM_TO_MODE` 里删两项：
#   删了报错会变成 KeyError，不说人话；白名单门能讲清「为什么堵、怎么绕」。
# ⚠ **能力表不动**：`repo_adapter.SUPPORTED_NP_BY_FORM` / `DEFERRED_NP_BY_FORM` 的
#   `aclnn_py` / `cpp` 条目是**能力表**不是准入表，删了将来想恢复要重新考证 dtype 支持面。
_ACCEPTANCE_RUNNER_FORMS = frozenset({"cpp_extension"})

# spec 省略 `runner_form` 时本模块认哪一种形态。**缺省必须落在准入集内**：缺省 `cpp` 的年代，
# 「spec 没写 runner_form」会派生出 `new_example`，正好撞上上面那道准入门——抽 spec 那层
# 一旦漏写字段，编排就在准入门前停摆，而它想要的其实就是当前唯一准入的那条通路。
# ⚠ 只有**键缺席**才吃缺省（统一走 `.get(k, _DEFAULT_RUNNER_FORM)`，不写 `or`）：
#   显式写成 null / "" 是一份写坏的 spec，应当照旧在 `_RUNNER_FORM_TO_MODE` 那里报「不受支持」，
#   不能被 `or` 悄悄兜成准入形态。
# ⚠ **值本身不在这里定义**（P5）：本模块只是缺省的**消费方**之一，之前在这里写死字面量，导致
#   gen_cases / repo_adapter / preflight_aclnn / finalize / CP-F 各自还留着 `"cpp"`——同一份 spec
#   被当成两种形态。真源现在唯一落在 `repo_adapter.DEFAULT_RUNNER_FORM`（选址理由见那边注释）。
#   下面这个别名只为让「缺省 ∈ 准入集」这条不变式与准入集**待在同一屏**，不是第二份定义。
_DEFAULT_RUNNER_FORM = repo_adapter.DEFAULT_RUNNER_FORM
assert _DEFAULT_RUNNER_FORM in _ACCEPTANCE_RUNNER_FORMS, (
    "缺省 runner_form 必须在准入集内，否则「spec 漏写字段」= 必然撞准入门")


def _spec_runner_form(spec):
    """读 spec 的 runner_form（唯一缺省口径）。本模块内三处判定必须同源，散开写必然漂移。

    ⚠ 委托给 `repo_adapter.spec_runner_form`：全仓（不只本模块）必须同源。"""
    return repo_adapter.spec_runner_form(spec)

# —— 验收通路的性能基线：**只认真数、禁 mock 兜底**（codex High#2）——————————————————————
# 病历：aclnn_py 的 evidence `perf.us=None`（采集端第二里程碑未接）、也不产 `_real_baseline.json`，
# 于是原来的 `else:` 一路落进 `perf_compare.mock_baseline()`——**mock 基线混进验收通路**。
# mock 基线 = 「NPU mock us × 1.08」编出来的数，拿它算出的 ratio 天然 ≥1、天然「达标」；
# 而 aclnn_py 是验收通路，会物理写出 acceptance.json——那就是一份**冒充达标**的验收裁决。
# 现在：验收通路缺真实基线一律挂起 `blocked_wait_real_baseline`（非 fail、非 pass），绝不兜底。
_BLOCKED_WAIT_REAL_BASELINE = "blocked_wait_real_baseline"
_BLOCKED_WAIT_REAL_BASELINE_STATE = "BLOCKED_WAIT_REAL_BASELINE"
# 真实基线的**来源 → 取数**登记表：按 `spec.perf.baseline` 这个**字段**分派（承律令#0，非按算子身份；
# median 只是当前唯一见证）。每项 = (work 下的产物文件名, 解析函数)。采集端把真数落成该文件本函数才认；
# 文件不在 = 采集端未接通 → 挂起。**新增来源在这里加一行即可，无需改判定逻辑。**
# 注：`tbe`（new_example 通路）不在此表——它的真基线由 `run_on_npu.sh` 直接落成 `_real_baseline.json`，
#     由下方更早的那个分支消费；此表只登记「需要专门解析器」的来源。
_REAL_BASELINE_SOURCES = {
    # torch 对标场景：torch_npu 上同算子的 kernel-only 耗时（真机内基线、非 GPU 外部数据）。
    "torch_npu": ("_torch_npu_baseline.json", lambda p: repo_adapter.parse_torch_npu_baseline(p)),
    # 任务书直接点名既有 ACLNN 实现：同机从 CANN 内置 libopapi.so 显式调用，不绕 torch 等价性证明。
    "aclnn_builtin": ("_aclnn_builtin_baseline.json",
                      lambda p: repo_adapter.parse_aclnn_builtin_baseline(p)),
}


# —— 性能采集计划：spec.perf → `work/_perf_plan.json`（采集端按字段读，**非按算子身份**）——————
# 为什么走文件：`repo_adapter.MODES[mode](caseset, work)` 的统一签名里没有 spec，而基线侧要跑的
# torch reference 只有 spec 说得清（`perf.torch_baseline` 的 slot-name → torch 形参映射）。
# 落成 work 下的一份数据，与 `_real_baseline.json` 同一种流法；不认识这份计划的 mode 一律无视它。
_PERF_PLAN_FILE = "_perf_plan.json"
#: 采集计划里**可透传的字段白名单**——只搬 spec.perf 里与「怎么采」有关的项，
#: 绝不把 `target_ratio` 这类**判据**字段带进采集端（判定归 perf_compare，采集端不许看见阈值）。
_PERF_PLAN_KEYS = ("warmup", "repeat", "torch_baseline", "aclnn_baseline", "op_dir")


def _emit_perf_plan(spec, work):
    """据 `spec.perf` 落 `work/_perf_plan.json`；不需要采集端配合 → 不落（= 本次不采性能）。

    两个触发条件（互斥）：
      · `ratio_gated` —— `perf.baseline` 在真实基线取数登记表里（当前 `torch_npu` / `aclnn_builtin`）；
      · `measure_only` —— 只要声明了 `perf`（§5.10）。采集端据 `mode` 只跑 custom 侧 msprof，
        **不采、不要、不等任何 baseline**；计划里因此**没有** `baseline` 键。
    ⚠ 这里**不**做「能不能采」的判断（那是采集端的事），也**不**写任何阈值——计划只回答「采什么、怎么采」。
    """
    perf = spec.get("perf") or {}
    mode = perf_mode.resolve_spec_mode(spec)
    measure_only = perf_mode.is_measure_only(mode)
    if not measure_only and perf.get("baseline") not in _REAL_BASELINE_SOURCES:
        return None
    plan = {k: perf[k] for k in _PERF_PLAN_KEYS if perf.get(k) is not None}
    plan["mode"] = mode
    if not measure_only:
        plan["baseline"] = perf["baseline"]
    plan["op"] = spec.get("op")
    path = os.path.join(work, _PERF_PLAN_FILE)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    if measure_only:
        print(f"[Task2 perf] 采集计划 → {_PERF_PLAN_FILE}（mode=measure_only：只采 NPU 侧 msprof "
              f"kernel-only 实测，无基线侧）")
        return path
    config_key = "torch_baseline" if plan["baseline"] == "torch_npu" else "aclnn_baseline"
    print(f"[Task2 perf] 采集计划 → {_PERF_PLAN_FILE}（baseline={plan['baseline']}，"
          f"{config_key}={'有' if plan.get(config_key) else '**缺**（采集端将 fail-closed）'}）")
    return path


def _real_baseline_or_blocked(spec, work):
    """验收通路取性能基线：**真数或挂起，二选一，没有第三条路**。返回 `(baseline|None, blocked_status|None)`。

    ⚠ 本函数**永远不会**返回 mock 基线——这正是它存在的理由。缺真数时返回 `(None, blocked_wait_real_baseline)`，
    由 perf_compare 落成正规挂起态、run_workflow 映射成 `BLOCKED_WAIT_REAL_BASELINE`（exit≠0）。
    """
    src = (spec.get("perf") or {}).get("baseline")
    entry = _REAL_BASELINE_SOURCES.get(src)
    if entry is None:
        print(f"[Task3] ⚠ 验收通路缺真实基线：spec.perf.baseline={src!r} 未在真实基线取数登记表 "
              f"{sorted(_REAL_BASELINE_SOURCES)} 中，且 work/_real_baseline.json 不存在 → 挂起"
              f"（**不 mock 兜底**：mock 基线在验收通路上等于冒充达标）")
        return None, _BLOCKED_WAIT_REAL_BASELINE
    fname, parse = entry
    path = os.path.join(work, fname)
    if not os.path.exists(path):
        print(f"[Task3] ⚠ 验收通路缺真实基线：{src} 采集端未接通（缺 work/{fname}）→ 挂起"
              f"（**不 mock 兜底**）")
        return None, _BLOCKED_WAIT_REAL_BASELINE
    return parse(path), None


def _acceptance_capable(mode):
    """本模式**是否可能**产出验收裁决。**fail-closed**：只有真机通路（_REAL_MACHINE_MODES）算数，
    其余（mock / catlass_mock / 日后新增的任何模式）默认一律按非验收对待——
    新增模式忘了登记时的失败方向是「少产一份裁决」，而不是「多产一份假裁决」。"""
    return mode in _REAL_MACHINE_MODES


def _non_acceptance_note(mode, is_experimental_form):
    """非验收产物的注脚，**按真实原因取串**（三个串的病历见它们的定义处）。

    | 情形 | 取哪一串 | 它声称了什么 |
    |---|---|---|
    | mode 属 mock 家族 | `_NOTE_MOCK` | 数据是编的（**只有这里能说这句话**） |
    | 真机跑、但 runner_form 未准入 | `_NOTE_FORM` | 数据是真的，堵的是「这条 form 不出裁决」 |
    | 其余（catlass 真机 / adapter 自报 development / 未登记的新 mode） | `_NOTE_OTHER` | 只说不产裁决，**不声称**数据真假 |

    ⚠ 顺序不能反：mock 家族即使同时是非准入 form，也必须落 `_NOTE_MOCK`——
    「数据是编的」是更强、更要紧的那句话，不能被「form 不准入」盖过去。
    """
    if mode in _MOCK_MODES:
        return _NOTE_MOCK
    if is_experimental_form:
        return _NOTE_FORM
    return _NOTE_OTHER


def _stamp_dev(obj, is_acceptance, grade, note=_NOTE_OTHER):
    """非验收通路的产物打 NON-ACCEPTANCE 戳（幂等；验收通路原样返回、一个字节不动）。

    perf_compare 已对「消费 mock 基线」的报告自己打过戳；这里补的是它覆盖不到的情形——
    比如精度 fail-fast 时那份根本没跑 perf_compare 的 `perf_report.json`，以及 mock 通路里
    baseline 来自外部 GPU 标杆（基线是真的、但 NPU 侧证据是 mock 的）那种混合情形。
    `setdefault` 保证不覆盖 perf_compare 已写的措辞。

    ⚠ `note` 缺省取**中性**的 `_NOTE_OTHER`，不是 mock 那一串：漏传实参时的失败方向应当是
    「少说一句」，而不是「凭空断言这轮数据是编的」。生产路径一律显式传 `_non_acceptance_note(...)`。
    """
    if is_acceptance or not isinstance(obj, dict):
        return obj
    obj.setdefault("evidence_grade", grade)
    obj.setdefault("acceptance_note", note)
    return obj


# —— §5.10 measure_only 的两个终态串（人读 overall）。**措辞红线**：不得出现任何会被读成
#    「性能通过 / 已达标」的字样——它就是「测了，没判」。前缀 PASS 指的是**精度维**裁决，
#    括号里逐字说明性能维只实测、未裁决。
_MEASURED_ONLY_OVERALL = "PASS(性能仅实测未裁决)"
_MEASURED_ONLY_STATE = "PASSED_PRECISION_PERF_MEASURED_ONLY"
_MEASURE_INCOMPLETE_OVERALL = "BLOCKED(measure_only 性能实测未完成)"
_MEASURE_INCOMPLETE_STATE = "BLOCKED_PERF_MEASUREMENT_INCOMPLETE"

# T6/T8：人读 overall → 机读 canonical 状态（task3 状态机词汇）。
_STATE_MAP = {
    "PASS": "PASSED", "PASS(无性能要求)": "PASSED",
    # §5.10：精度维定裁决、性能维只实测未裁决。**刻意不复用 `PASSED`**——机读方必须能一眼
    # 分出「性能比过阈值的通过」与「性能压根没判的通过」，否则这两件事在下游合流即失真。
    _MEASURED_ONLY_OVERALL: _MEASURED_ONLY_STATE,
    _MEASURE_INCOMPLETE_OVERALL: _MEASURE_INCOMPLETE_STATE,
    "FAIL(精度)": "FAILED_PRECISION", "NEEDS_REVIEW": "NEEDS_REVIEW",
    "PASSED_WITH_RISK": "PASSED_WITH_RISK",
    "PASSED_WITH_GAPS": "PASSED_WITH_GAPS",   # C4：精度全过但任务书要求的 dtype 有差额挂账
    "BLOCKED_GOLDEN_UNAUTHORIZED": "BLOCKED_GOLDEN_UNAUTHORIZED",  # 批 5：golden 授权核不实
    # 参考实现算不出真值（如通道数超 OpenCV CV_CN_MAX）→ 这批 case 的结论是**空白**。
    # 与 UNAUTHORIZED 分开：那是「真值来路不明」，这是「压根没有真值」，成因与处置都不同
    #（前者要人把授权补齐，后者要换参考实现或由人裁定这批 case 不在验收范围内）。
    "BLOCKED_GOLDEN_UNAVAILABLE": "BLOCKED_GOLDEN_UNAVAILABLE",

    "BLOCKED_WAIT_GPU_BENCHMARK": "BLOCKED_WAIT_GPU_BENCHMARK",
    # High#2：验收通路缺真实基线（采集端未接通）→ 正规挂起，**不是** fail、更**不是** pass。
    _BLOCKED_WAIT_REAL_BASELINE_STATE: _BLOCKED_WAIT_REAL_BASELINE_STATE,
    "BLOCKED_INCOMPARABLE_TIMING_SCOPE": "BLOCKED_INCOMPARABLE_TIMING_SCOPE",
    "BLOCKED_GPU_BASELINE_INVALID": "BLOCKED_GPU_BASELINE_INVALID",  # gb-9：标杆被判废（非缺标杆）
}

def _canonical_state(overall, ps):
    """人读 overall → 机读 canonical 状态（T6/T8）。门因不可比/挂起而 FAILED 时据 perf status 细化，
    避免笼统 BLOCKED(验收门未过) 掩盖 canonical 出口。"""
    if overall in _STATE_MAP:
        return _STATE_MAP[overall]
    st = ps.get("status")
    if st == "blocked_incomparable_timing_scope":
        return "BLOCKED_INCOMPARABLE_TIMING_SCOPE"
    if st == "blocked_gpu_baseline_invalid":       # gb-9：有硬错的标杆被判废 ≠ 缺标杆
        return "BLOCKED_GPU_BASELINE_INVALID"
    if st == "blocked_wait_gpu_benchmark":
        return "BLOCKED_WAIT_GPU_BENCHMARK"
    if st == _BLOCKED_WAIT_REAL_BASELINE:
        # High#2：门也可能因「挂起态下 NPU 侧计时缺失」而 FAILED（perf 采集端整条未接通时正是如此）。
        # 那种情况 overall 是笼统的 BLOCKED(验收门未过)，这里据 perf status 细化出机读 canonical 出口，
        # 免得「等真实基线」被读成「证据破损」。
        return _BLOCKED_WAIT_REAL_BASELINE_STATE
    if isinstance(overall, str) and overall.startswith("性能未达成"):
        return "FAILED_PERFORMANCE"
    if isinstance(overall, str) and overall.startswith("BLOCKED"):
        return "BLOCKED_EVIDENCE_INCOMPLETE"
    return "NEEDS_REVIEW"


def _exit_code(overall):
    """退出码枚举（T5；修 startswith('PASS') 潜伏 bug——PASSED_WITH_RISK 曾被误判为 0 干净退出）：
      0 = 干净 PASS / PASS(无性能要求)；
      2 = PASSED_WITH_RISK（requires_human_cp、CI 挂起转人工、非自动合并/非自动失败）；
      1 = 其余（FAIL 精度 / 性能未达 / BLOCKED_* / NEEDS_REVIEW）。"""
    if overall in ("PASS", "PASS(无性能要求)", _MEASURED_ONLY_OVERALL):
        return 0                  # §5.10：性能维按任务书口径本就不产裁决，阻塞它没有依据
    if overall in ("PASSED_WITH_RISK", "PASSED_WITH_GAPS"):
        return 2       # 挂起转人工——非自动失败、非干净 PASS。
                       # PASSED_WITH_RISK=任务书宽于平台底线；PASSED_WITH_GAPS=任务书要求的 dtype 算子没实现（C4）。
                       # ⚠ 后者**绝不能回 0**：那等于「算子没做到任务书要求」被 CI 读成干净通过、可自动合并。
    return 1


def _runner_source_allowed(mode, source):
    """真机 mode 与 runner provenance 的受控对应；cpp_extension 另由收据门证明生成物。"""
    expected = {
        "new_example": "user",
        "aclnn_py": "user",
        "cpp_extension": "generated_official_cpp_extension",
    }
    return source == expected.get(mode)


def _experimental_form_message(runner_form):
    return (
        f"runner_form={runner_form!r} 当前不用于正式验收。\n"
        f"原因：只有 cpp_extension 跑通过完整 torch_parity 矩阵（Median PR6429 1152 例，"
        f"gate.passed=true），cpp / aclnn_py 的真机成熟度未达同等水平（见 AGENTS.md §9）。\n"
        f"如需局部开发验证：加 --allow-experimental-form。该模式下**不产** "
        f"acceptance.json / verdict.json，只产带 evidence_grade=\"development\" 的非验收产物"
        f"（{_DEV_SUMMARY_FILE} / {_DEV_VERDICT_FILE}）。")


def _resolve_mode(spec, requested_mode, allow_experimental_form=False):
    """据 runner_form 派生真机 mode，并拒绝显式走错真机通路 / 用未准入的通路出裁决。

    mock/catlass 等显式逃生口保持原语义；这里只阻断会改变 DUT form/性能基线的两条真机通路错配，
    外加**准入白名单**（`_ACCEPTANCE_RUNNER_FORMS`）。
    """
    runner_form = _spec_runner_form(spec)
    expected = _RUNNER_FORM_TO_MODE.get(runner_form)
    if expected is None:
        raise SystemExit(
            f"spec.runner_form={runner_form!r} 不受支持，"
            f"supported={sorted(_RUNNER_FORM_TO_MODE)}")
    # 「走错真机通路」先判：它是**输入错**，比准入问题更贴近用户当下打错的那个字。
    if (requested_mode is not None and requested_mode in _REAL_MACHINE_MODES
            and requested_mode != expected):
        raise SystemExit(
            f"真机 mode 与 spec.runner_form 不匹配：runner_form={runner_form!r} "
            f"必须使用 mode={expected!r}，实际请求 {requested_mode!r}。"
            "拒绝走错 DUT/基线路径。")
    effective = expected if requested_mode is None else requested_mode
    # ① 入口门：正常调用路径在这里拦住。
    # ⚠ **只对真机通路生效**：显式请求 mock / catlass_mock 时不拦——那些 mode 物理上就不产
    #   acceptance.json / verdict.json，用它们做本地用例链自检与 runner_form 准入无关。
    #   门放在 `effective` 判定之后正是为此：早一步拦就把 mock 逃生口一起堵死了。
    if (effective in _REAL_MACHINE_MODES
            and runner_form not in _ACCEPTANCE_RUNNER_FORMS
            and not allow_experimental_form):
        raise SystemExit(_experimental_form_message(runner_form))
    return effective


def _assert_acceptance_form_allowed(spec, mode):
    """② 出口门：**写验收产物之前**再校一次。

    ⚠ 只拦入口是拦不住的，仓里已有先例——`repo_adapter.py` 的注释明写
    「`repo_adapter.py cs wd acceptance.json catlass_mock` 是绕开 catlass CLI 那两道守卫的
    现成后门」，所以那边**也是在出口再校一次**。本门照抄这个口径。

    **本门的实际覆盖范围**（别读大了）：一切经 `run()` 写验收产物的路径，
    含直接 `--mode aclnn_py`、以及绕开 CLI 直接调 `run(...)` 的进程内调用。
    **不覆盖**手写/外部工具伪造的 `acceptance.json`——那不是本门的对象，
    由三级门 `validate_acceptance_state` 按证据完整性另行把关。

    交叉校验**同时看 spec 与 mode**：出口处两者都在手上。只看 `runner_form` 是不够的——
    `_acceptance_capable()` 认的是 mode，将来若有非 `_REAL_MACHINE_MODES` 的 mode 被判成
    可产裁决，一个 `runner_form=cpp_extension` 的 spec 就能替另一种执行形态出裁决。
    所以这里要求二者**互相蕴含**：form 准入 ∧ mode 正是该 form 派生出的那一个。
    """
    runner_form = _spec_runner_form(spec)
    if runner_form in _ACCEPTANCE_RUNNER_FORMS:
        expected = _RUNNER_FORM_TO_MODE.get(runner_form)
        if mode == expected:
            return
        raise SystemExit(
            f"[出口门] 拒绝写验收产物：runner_form={runner_form!r} 派生的验收 mode 是 "
            f"{expected!r}，实际 mode={mode!r}。执行形态与 spec 声明不一致时产出的裁决，"
            f"说不清验的到底是哪条通路。")
    raise SystemExit(
        f"[出口门] 拒绝为 runner_form={runner_form!r}（mode={mode!r}）写验收产物。\n"
        + _experimental_form_message(runner_form))


def _read_regular_file(src, what):
    """按字节读出 `src`；不是普通文件即 fail-closed。"""
    if not os.path.isfile(src):
        raise SystemExit(f"[CP-E staging] {what} 不存在或不是普通文件：{src!r}")
    try:
        with open(src, "rb") as fh:
            return fh.read()
    except OSError as ex:
        raise SystemExit(f"[CP-E staging] 读取 {what} 失败：{src!r}：{ex}")


def _read_acceptance_inputs(spec_path, spec, source_facts_path):
    """校验并**读出**本轮验收三份输入原件的字节，返回 `{staged 文件名: bytes}`。

    落哪三份、为什么是这三份，见 `_STAGED_FILES` 上方那段病历。这里只讲落地口径：

    | 原件 | 从哪来 | 谁消费 staged 副本 |
    |---|---|---|
    | `spec.json` | 本次 `run_workflow <spec>` 的实参 | CP-F `base_artifacts.spec`（须落报告目录内） |
    | `golden.py` | `<ops_root>/<op>/golden.py`（`gen_cases.load_golden` 同一处） | CP-F 的 golden 授权链锚 `dirname(spec)/golden.py` |
    | `source_facts.json` | 调用方 `--source-facts` | 三级门 `_gate_build_receipt_source_binding` 的来源对照物 |

    ⚠ **先读进内存、后写盘，不是直接 `copyfile`**。两个理由，都是实打实会踩的：
      ① 清残留与落副本是**同一批文件名**。若边删边拷，`--source-facts <out>/source_facts.json`
         （复跑时最自然的写法，指的正是上一轮 staging 出来的那份）会在删完之后再也读不到，
         报出来的还是一句「指不到文件」，把一次正常复跑变成假 BLOCKED；
      ② 三份原件全部读通过才动 `--out`，避免「spec 拷好了、golden 缺失」这种半 staging 现场。

    ⚠ **`source_facts` 先验后拷，不是拷了算数**。校验直接复用三级门用的那一份
    `source_facts_lookup.find_source_facts`（它连 envelope digest、`completeness=complete`、
    两条通路必填集一起校）——**另写一份判据必然分叉**，那时门与 staging 会对同一份文件给出两种结论。
    先验的收益是把「source_facts 不可信」这类失败挪到**跑 NPU 之前**，而不是等一整轮真机跑完才在门上炸。

    ⚠ **staging 不产生新的信任**：三份都是原件的字节副本，能被篡改的面与原件相同。它们各自仍要过
    下游的对账（spec ↔ cpp_extension receipt 的 `spec_sha256`、source_facts ↔ build receipt 来源锚）。
    别把「报告目录里有这份文件」当成「这份文件已被核过」。

    ⚠⚠ **`golden.py` 这一份的绑定强度明显弱于另外两份，如实记账，别读成已封**
    （2026-08-05 审修门 High；**问题本身是既有的**，staging 只是把它从「CP-F 压根跑不起来」
    变成「CP-F 跑得起来，但这一格 provenance 不可信」）：

    | 副本 | 被什么绑住 |
    |---|---|
    | `spec.json` | 首轮 cpp_extension receipt 的 `bindings.spec_sha256`（真机产、逐条 evidence 引其摘要）→ 换了必被 CP-F 的 `base_cpp_extension_spec_sha256_mismatch` 抓到 |
    | `source_facts.json` | 首轮 vendor build receipt 的来源锚 → 换了必被三级门抓到 |
    | `golden.py` | **只被本轮的两件事约束**：① staging 与 Task1 之间没被换（`_assert_staged_golden_matches_task1`）；② 它就是本轮算出 golden `.npy` 的那份源码。**首轮验收产物里没有任何字段记过它的摘要**（cpp_extension receipt / evidence 都没有这个字段） |

    所以：**首轮跑完之后**有人改写 `<报告目录>/golden.py`，CP-F 的
    `execution_provenance.golden_source_sha256` 会跟着变，而没有对照物能说它变了。
    真实影响面要说清楚——被冻结用于复测的 golden **值**（`caseset` 里那些 `.npy`）由
    `build_case_bindings` 逐字节哈希、`caseset` 本身又被 receipt 的 `caseset_sha256` 绑住，
    **裁决用的真值动不了**；失真的是「这些真值是哪份源码算的」这一格 provenance。
    要真绑住，得把 golden 摘要写进**首轮真机工件**（cpp_extension receipt 的 `bindings`），
    再由三级门核「实际消费的 golden 字节 == 记录摘要」——那是真机工件 schema 变更，
    另立批次并需用户确认，不在本批范围内。
    """
    op = spec.get("op")
    if not isinstance(op, str) or not op:
        raise SystemExit("[CP-E staging] spec.op 缺失或非非空字符串，无法定位 golden.py")
    # 与三级门同一份判据（见 docstring）：UNTRUSTED 覆盖「显式路径指不到」「envelope 不自洽」
    # 「completeness 非 complete」等全部情形，一律拒。
    # 第一个实参传 `None`：显式路径下门根本不看它，写个假目录只会让读的人以为这里有自动发现。
    if (source_facts_lookup.find_source_facts(None, source_facts_path)
            == source_facts_lookup.SOURCE_FACTS_UNTRUSTED):
        raise SystemExit(
            f"[CP-E staging] --source-facts 指向的文件不是可信的 source_facts.json："
            f"{source_facts_path!r}\n"
            f"  → 它必须是 fetch_source.py 落的内容寻址 envelope，且 "
            f"completeness.status=complete、reasons=[]。\n"
            f"  → blocked/半成品的取材事实只供诊断，不能当验收的来源锚（fail-closed）。")
    try:
        golden_src = os.path.join(repo_adapter.op_dir(op), "golden.py")
    except ValueError as ex:
        raise SystemExit(f"[CP-E staging] 无法定位 <ops_root>/{op}/：{ex}")
    if os.path.islink(golden_src):
        # 与 `gen_cases.load_golden` 同一条守卫（防换靶），口径别在两处分叉。
        raise SystemExit(f"[CP-E staging] golden.py 是符号链接，拒绝（防换靶）：{golden_src!r}")
    # spec 的**读后复核**（口径照抄下方 `_assert_staged_golden_matches_task1`，2026-08-06 审修门 Medium）：
    # `run()` 先 `json.load` 解析出 `spec` 驱动整轮执行，这里再按字节读一次落副本——两次之间被改写的话，
    # 报告目录里那份 `spec.json` 与**实际驱动本轮执行的**不是同一份，而 CP-F 的 `base_artifacts.spec`
    # 正是锚在这份副本上。golden 那一格已经这么封了，spec 这一格漏着没道理。
    # ⚠ 比的是**解析结果**而非字节：纯格式化（缩进/键序）不该把一次正常跑判死；真正要挡的是内容变了。
    spec_bytes = _read_regular_file(spec_path, "spec")
    try:
        restaged = json.loads(spec_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as ex:
        raise SystemExit(
            f"[CP-E staging] spec 在解析与 staging 之间变得不可解析：{spec_path!r}：{ex}")
    if restaged != spec:
        raise SystemExit(
            f"[CP-E staging] spec 在解析与 staging 之间被改写：{spec_path!r}\n"
            f"  → 报告目录里那份副本与实际驱动本轮执行的不是同一份，"
            f"CP-F 的 base spec 锚就落在这份副本上 —— fail-closed，不产任何产物。")
    return {
        _STAGED_SPEC_FILE: spec_bytes,
        _STAGED_GOLDEN_FILE: _read_regular_file(golden_src, "golden.py"),
        _STAGED_SOURCE_FACTS_FILE: _read_regular_file(
            source_facts_path, "source_facts.json"),
    }


def _write_staged_inputs(out_dir, payloads):
    """把 `_read_acceptance_inputs` 读出的字节落进 `--out`，返回 staged `source_facts.json` 路径。

    键集合必须**恰好**是 `_STAGED_FILES`：少一项就是「清了却没重落」，那份残缺的自证材料
    比没有更坏（CP-F 会拿到一个看着齐全其实缺件的目录）。
    """
    if set(payloads) != set(_STAGED_FILES):
        raise SystemExit(
            f"[CP-E staging] 内部错误：待落盘副本键集合 {sorted(payloads)} "
            f"≠ {sorted(_STAGED_FILES)}")
    for name in _STAGED_FILES:
        path = os.path.join(out_dir, name)
        # ⚠ `O_NOFOLLOW`：落点那一层若是软链一律 fail-closed，绝不跟着写出 `--out`。
        # 上游的清残留已用 `lexists` 挡掉悬空软链，但那是**检查**、这是**打开**，中间存在换靶窗口；
        # 只有在 open 这一步拒绝解引用才真的关上。`O_EXCL` 不用——清残留后正常路径本就不存在，
        # 但一次失败重跑留下的半成品不该让整条链卡死，允许覆盖普通文件。
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags, 0o644)
        except OSError as ex:
            raise SystemExit(
                f"[CP-E staging] 打开 {name} 失败（落点是软链则拒绝跟随）：{path!r}：{ex}")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(payloads[name])
        except OSError as ex:
            raise SystemExit(f"[CP-E staging] 写入 {name} 失败：{path!r}：{ex}")
    print(f"[CP-E staging] 已把 {', '.join(_STAGED_FILES)} 落进 {out_dir}/"
          f"（CP-F 的 base spec/golden 锚 + 三级门的来源对照物）")
    return os.path.join(out_dir, _STAGED_SOURCE_FACTS_FILE)


def _assert_staged_golden_matches_task1(spec, payloads):
    """Task1 跑完后复核：`<ops_root>/<op>/golden.py` 与 staging 时读到的字节仍逐字相同。

    ⚠ **为什么非做不可**：staging 读一次、`gen_cases.load_golden` 又从**原路径**读一次。
    两次之间被换掉的话，报告目录里那份 `golden.py` 是 A、真正算出 golden `.npy` 的是 B——
    而 CP-F 会拿 A 去填 `golden_source_sha256`，三级门也不会发现。这不是理论洞：
    `gen_cases.load_golden` 自己的 docstring 就记着同一类 TOCTOU。
    这里做的是**读后复核**（不是消灭窗口）：窗口从「整轮 staging→Task1」收缩到
    「本函数读盘的一瞬」，且换过靶必被抓到，除非攻击者再换回来。

    非验收通路（`payloads is None`）跳过：那条路不 staging，也没有 CP-F 会去读的副本。
    """
    if payloads is None:
        return
    golden_src = os.path.join(repo_adapter.op_dir(spec["op"]), "golden.py")
    if (os.path.islink(golden_src)
            or _read_regular_file(golden_src, "golden.py") != payloads[_STAGED_GOLDEN_FILE]):
        raise SystemExit(
            f"[CP-E staging] golden.py 在 staging 与 Task1 之间被改写：{golden_src!r}\n"
            f"  → 报告目录里那份副本与实际算出 golden 的源码不是同一份，"
            f"整轮证据的 golden 来源说不清 —— fail-closed，不产任何产物。")


def _invalidate_stale_results(out_dir):
    """**任何校验之前**先让 `--out` 里上一轮的结论不可消费。返回被清掉的文件名（已排序）。

    ⚠ 病历（2026-08-06 审修门 High · **产物层 fail-open**）：清残留原先在 `run()` 中段，而
    `--source-facts` 必填门、`_read_acceptance_inputs` 的可信性校验都在它**之前**早退。于是
    「`--out` 里有上一轮的 PASS → 换 spec / 换 DUT 重跑，但漏传或传错 `--source-facts`」时，
    新进程非零退出，**上一轮的 `acceptance.json` / `验收报告.md` 原封不动躺在那儿**。
    本仓下游（三级门、CP-F、渲染器、以及人）**就是按文件名**读裁决的 —— 旧 PASS 会被当成这次的结果。
    这与 `_ACCEPTANCE_FILES` / `_DEV_FILES` 被刻意拆成两条产物路径是**同一个病**：
    同名同形的产物迟早会被当成验收结论用掉。

    **为什么放在第一行、而不是去穷举「所有可能早退的点」**：穷举不了（今天是 `--source-facts`，
    明天新加一道参数门就又漏一处），能穷举得了也脆。本函数只**删除**、不产生任何新事实，
    失败方向是「少一份裁决」而非「多一份假裁决」，所以可以无条件前置——`run()` 一旦被调用，
    `--out` 里上一轮的结论就**必定**作废，不管本轮是跑成、跑挂，还是在参数校验就被拒。

    **为什么不是「事务目录 + 成功后原子发布」**（审修门给的另一个方向）：`--out` 在本仓**不是**
    单进程独占的输出坑。cpp_extension 真机通路的外部 driver 会在两次编排之间往同一个报告目录/
    `work/` 回写收据；`--source-facts <out>/source_facts.json`（复跑时最自然的写法）更是把它
    当**输入**读。改成「跑完才发布」会把这两条既有通路一起弄断，代价远大于收益。

    ⚠ **不清 `caseset.json` / `evidence.json` 这类证据件**：它们单独存在推不出任何裁决
    （三级门 task2 读 `verdict.json`、CP-F 的 `BASE_ARTIFACTS` 要求五件齐全、渲染器读
    `acceptance.json`），清掉反而毁了早退现场的诊断价值。不变式是
    **「没有裁决件、没有人读报告 = 没有可消费的结论」**。
    ⚠ **也不清 `_STAGED_FILES`**：那三份是**输入**副本，清早了会把「复跑时 `--source-facts`
    指向上一轮的副本」变成假 BLOCKED。它们的清理点在读完原件之后，见 `run()` 里那段。
    """
    victims = [n for n in _RESULT_FILES if os.path.lexists(os.path.join(out_dir, n))]
    for pattern in _RESULT_GLOBS:
        victims += [os.path.basename(p)
                    for p in glob.glob(os.path.join(out_dir, pattern))]
    removed = []
    for name in victims:
        path = os.path.join(out_dir, name)
        try:
            # 软链只删链接本身、不碰目标——正是要的（同下方清 staging 残留的口径）。
            os.remove(path)
        except OSError as ex:
            raise SystemExit(
                f"[产物隔离] 清不掉上一轮的结论产物：{path!r}：{ex}\n"
                f"  → 清不掉就不开跑：留着它 = 本轮一旦中途拒跑，旧裁决会被下游当成这次的结果。")
        removed.append(name)
    if removed:
        print(f"[产物隔离] 已作废 {out_dir}/ 里上一轮的结论产物："
              f"{', '.join(sorted(removed))}（本轮无论跑成还是拒跑，都不会留下可被误读成"
              f"本次结果的旧裁决）")
    return sorted(removed)


def run(spec_path, mode=None, out_dir="reports/_run", defect=None, perf_slow=None,
        gpu_baseline=None, allow_experimental_form=False, source_facts=None,
        taskdoc_caseset=None):
    """跑一遍 Task1→2→3。

    `taskdoc_caseset` = 规范化任务书用例集（`taskdoc_caseset.json`）的路径，只在 spec 声明
    `precision.case_source='taskdoc'` 时需要；两向不匹配由 `gen_cases` fail-closed（编排层不做第二套判定）。

    ⚠ `defect` / `perf_slow` 是**测试专用夹具**（在 mock 里造坏点 / 造略慢基线，用来证明「validator 真会 fail、
    门不是假门」），**两个都不在 CLI 上暴露**（C5 拿掉 `--defect`；`--perf-slow` 同批理由、2026-07-22 补下架）
    ——只有 `test_*.py` 以 `import run_workflow` 的方式进程内调用得到。它们只对非验收通路有意义；
    若作用于验收通路，本函数直接 fail-closed 拒跑。

    ⚠ `source_facts` 在**验收通路上是必填**（缺席即拒跑，见下面那道门）。这不是多要一个可选参数，
    而是让三级门 `_gate_build_receipt_source_binding` 的最后一处残留伪装面（自动发现落空 +
    收据自称 `pull_request` → 无对照物可查）**在编排层被封死**：编排每次都显式指路，
    「缺席」本身就成了非法。非验收通路不要求它（那条路物理上不产验收裁决，也没有来源锚要对账）。

    ⚠ 验收通路另受 **spec 变更门**（`spec_change_gate`）约束：`<out>/work/spec_change_receipt.json`
    必须存在、其 `spec_sha256` 与**当场重算的 `spec_path` 字节**一致、且带非空非占位的
    `change_reason` / `confirmed_by`，否则 `BLOCKED(spec 变更未确认)`。落点见 `_SPEC_GATE_ENTRY`
    上方那段。⚠ 它证的是「spec 内容完整 + 有人显式署名声明过」，**不是**「用户确认过」。
    """
    # ★ **第一件事**：让 `--out` 里上一轮的结论立刻不可消费（详见 `_invalidate_stale_results`）。
    # 位置就是要在**所有**可能早退的校验之前——下面的 `--source-facts` 必填门、staging 的可信性
    # 校验、`_resolve_mode` 的准入门都会 raise SystemExit，早退时留着旧 PASS 就是产物层 fail-open。
    # ⚠ 它只删不建：`out_dir` 不存在时是纯 no-op，「必填门在 makedirs 之前拒、不留半个产物目录」
    #   这条既有不变式一个字没松。
    _invalidate_stale_results(out_dir)
    # 显式真机 mode + 注入夹具可在读取 spec 前拒绝，保留既有 fail-closed/无副作用顺序。
    if (defect or perf_slow) and mode is not None and _acceptance_capable(mode):
        raise SystemExit(
            f"defect / perf_slow 是测试专用注入夹具，禁止作用于验收通路 mode={mode!r}——拒绝执行。")
    spec = json.load(open(spec_path, encoding="utf-8"))
    if not isinstance(spec, dict):
        raise SystemExit("spec 须为 JSON object")
    # §5.10：性能口径在**跑任何东西之前**定死并 fail-closed 校验（measure_only 与
    # baseline/target_ratio 互斥）。放这么早是为了「配置自相矛盾」停在零副作用处。
    # ⚠ 排在 `_resolve_mode` 之前、`_invalidate_stale_results` 之后：前者是准入门（会 raise），
    #   两条都在零副作用处早退，谁先谁后不改变「不留半个产物目录」这条不变式；但
    #   `_invalidate_stale_results` 必须仍是函数体第一件事（见上方 ★）。
    try:
        perf_mode_name = perf_mode.resolve_spec_mode(spec)
    except ValueError as ex:
        raise SystemExit(f"spec.perf 配置非法：{ex}")
    measure_only = perf_mode.is_measure_only(perf_mode_name)
    if measure_only and gpu_baseline is not None:
        raise SystemExit(
            "perf.mode='measure_only' 与 --gpu-baseline 自相矛盾："
            "只测不比的口径下不消费任何外部标杆。要做 GPU 对比请改回 ratio_gated。")
    mode = _resolve_mode(spec, mode, allow_experimental_form=allow_experimental_form)
    if mode not in repo_adapter.MODES:  # 先校验，避免 Task1 已跑再 KeyError、留半产物
        raise SystemExit(f"unknown mode {mode!r}, supported={list(repo_adapter.MODES)}")
    if (defect or perf_slow) and _acceptance_capable(mode):
        # fail-closed：注入夹具 + 验收通路 = 「往验收证据里掺人造数据」。真机 adapter 现在只是忽略它们，
        # 但「被忽略」不是保证——这里直接拒跑，别指望下游的沉默。
        raise SystemExit(f"defect / perf_slow 是测试专用注入夹具，禁止作用于验收通路 mode={mode!r}——拒绝执行。")
    # 未准入的 runner_form 即使是真机通路，也**物理上不产验收裁决**——比照 mock 通路的口径，
    # 只产 dev_run_summary.json / dev_precision_check.json（`evidence_grade="development"`）。
    # 逃生阀之所以不做成「照产 acceptance.json 但打个标」：下游是**按文件名**读裁决的，
    # 同名同形的产物迟早会被当成验收结论用掉。
    # ⚠ 这两行**刻意提前到任何副作用之前**（原先在 makedirs 之后）：下面那道 `--source-facts`
    #   必填门是纯参数校验，必须在 `os.makedirs` / staging / Task1 之前拒，不留半个产物目录。
    is_experimental_form = _spec_runner_form(spec) not in _ACCEPTANCE_RUNNER_FORMS
    is_acceptance = _acceptance_capable(mode) and not is_experimental_form
    # ★ 验收通路：`source_facts` 必填。**不给缺省、不自动去猜路径**——「自动发现」正是要被消灭的
    #   那个状态：门找不到时 PR 通路沿用旧行为不阻断，于是「收据自称 PR、事实其实是 local」无从查证。
    #   编排每次都显式指路后，「没有对照物」这件事在验收通路上不再可能悄悄发生。
    if is_acceptance and source_facts is None:
        raise SystemExit(
            "[验收通路] 必须显式提供 --source-facts <fetch_source 产的 source_facts.json>。\n"
            "  原因：三级门要拿它与 vendor build receipt 的来源锚逐字对账。缺对照物时 PR 通路\n"
            "        会沿用旧行为放过，「收据自称 gitcode_pr、事实其实是 local_snapshot」这类\n"
            "        伪装就查不出来 —— 所以缺席一律拒跑，不是可选参数。\n"
            "  · 取材那一步（fetch_source.py --out <取材目录>）产的就是它；\n"
            "  · 本次会把它按字节 staging 进 --out，CP-F 与事后单独复跑三级门都直接消费该副本；\n"
            "  · 只想本地自检用例链（非验收）→ 显式加 --mode mock。")
    # CP-E 自证材料：**先读进内存再说**，落盘在下面清完残留之后（理由见 `_read_acceptance_inputs`）。
    # 放在 `os.makedirs` 之前 = 三份原件有任何问题都不留下半个产物目录。
    staged_payloads = (_read_acceptance_inputs(spec_path, spec, source_facts)
                       if is_acceptance else None)
    # ★ spec 变更门 · ① 入口门。仍在 `os.makedirs` 之前 = 未确认的 spec 变更**不留半个产物目录**。
    # ⚠ 排在 `--source-facts` 必填门与 staging 可信性校验**之后**：那两道说的是「你这条命令
    #   少给/给错了对照物」，离用户当下敲错的那个字更近；本门说的是「这份 spec 没人签过字」。
    #   三道都在零副作用处早退，先后不改变任何不变式，只影响先看到哪句话。
    # ⚠ 传的是 `spec_path`（原件），**不是** `<out>/spec.json`——理由见 `_SPEC_GATE_ENTRY` 上方。
    if is_acceptance:
        spec_change_gate.assert_confirmed(spec_path, out_dir, _SPEC_GATE_ENTRY)
    # U6a：默认已从 mock 翻为 new_example（真机通路）。mock 的「NPU 输出」= golden.copy()、精度按构造必过，
    # 默认指向它 = 默认产出一份与真验收同名同形的**伪造** acceptance.json（危险的默认）。翻真机后，缺真机
    # OPRUNWAY_* 配置时**在跑 Task1 之前**就 fail-closed 停下——绝不落半产物、绝不出「看起来对」的裁决，
    # 并明确指路（要本地自检 → --mode mock；要真机 → 把 OPRUNWAY_* 设好）。_ne_cfg 只读 env、无副作用、可重入
    # （run_new_example 内还会再校一次），此处仅提前把「缺配置」这类失败从 Task2 中段的 traceback 挪到最前、给清晰提示。
    if mode == "new_example":
        try:
            repo_adapter._ne_cfg()
        except ValueError as ex:
            raise SystemExit(
                f"[new_example] 真机跑测无法启动——真机配置缺失或无效：\n{ex}\n"
                f"  · 只想本地自检用例链（非验收）→ 显式加 --mode mock。\n"
                f"  · 要真机跑测 → 先按上面提示设好 OPRUNWAY_* 环境变量（真值不写进仓）。")
    os.makedirs(out_dir, exist_ok=True)
    work = os.path.join(out_dir, "work")
    def _dump(obj, name):
        p = os.path.join(out_dir, name)
        json.dump(obj, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        return p

    # 本轮非验收产物的注脚：**按真实原因取一次串**，全程复用同一个值。
    # ⚠ 它不看 `is_acceptance`，所以下面 adapter 自报 development 把 `is_acceptance` 降级之后，
    #   这句话依然成立（那种情形本来就落 `_NOTE_OTHER`，不会声称数据真假）。
    non_acceptance_note = _non_acceptance_note(mode, is_experimental_form)
    print(f"=== OpRunway workflow · {spec['op']} · mode={mode} ===")
    if is_experimental_form:
        print(f"=== ⚠ runner_form={_spec_runner_form(spec)!r} 非验收准入通路"
              f"（--allow-experimental-form）：本次不产 acceptance.json / verdict.json ===")
    if not is_acceptance:
        print(f"=== ⚠ {non_acceptance_note} ===")
    # 清上轮残留，防 stale 真基线被复用。`_torch_npu_baseline.json` / `perf_collect.json` / `_perf_plan.json`
    # 同理必清：本轮若性能没采成，留在 work 里的上一轮基线会被 `_real_baseline_or_blocked` 当成本轮真数读走
    # ——那正是「用旧数冒充这次达标」，比缺基线挂起坏得多。
    # ⚠ `work/spec_change_receipt.json` **刻意不在这张表里**：它是本轮的**输入侧凭证**（谁签的字、
    #   为什么改），不是上一轮采出来的数据。跟着清的话，每次复跑都得重 `--init`，而
    #   `previous_spec_sha256` 记的变更历史会在第一次复跑时蒸发——那正好毁掉这道门唯一的价值。
    #   它已在上面的入口门被逐字校过，留着不会让任何旧结论复活。
    for stale in ("_real_baseline.json", "perf_result.txt", "_torch_npu_baseline.json",
                  "_aclnn_builtin_baseline.json",
                  "perf_collect.json", "_perf_plan.json", "_aclnn_perf_plan_sent.json"):
        sp = os.path.join(work, stale)
        if os.path.exists(sp):
            os.remove(sp)
    # 上轮 staging 副本的清理点。**结论产物（`_RESULT_FILES`）不在这里**——它们已在 `run()` 第一行
    # 被 `_invalidate_stale_results` 作废，因为那几件是「早退时也必须已经不可消费」的东西。
    # ⚠ 这三份不能跟着提前清：`--source-facts <out>/source_facts.json`（复跑时最自然的写法）指的正是
    # 上一轮 staging 出来的那份，提前清掉会把一次正常复跑变成「指不到文件」的假 BLOCKED。
    # 所以顺序恒为 **先把三份原件读进内存（上面 `_read_acceptance_inputs`）→ 再清 → 再落副本**。
    # ⚠ **无条件清**（不按 is_acceptance 二选一）：同一个 out_dir 上一轮跑的是验收、这一轮跑 mock
    # （或换了另一份 spec / 另一份 golden）时，上轮 staging 的 spec.json / golden.py /
    # source_facts.json 会原样躺着，而 CP-F 与事后单独复跑的三级门**就是按文件名**去读它们的
    # → 「拿上一轮的 spec/golden/来源事实，去配这一轮的 caseset 与裁决」。同 acceptance.json 那条理由。
    # ⚠ 判存用 `lexists` 而**不是** `exists`（2026-08-05 审修门 Medium）：`exists` 对**悬空软链**
    # 返回 False，于是那条软链留了下来；下一步 `open(path, "w"/"wb")` 会**跟着它写到 `--out` 之外**。
    # 预置一条 `<out>/golden.py -> /任意/尚不存在的路径` 就能把本轮的自证材料（或裁决）写出报告目录。
    # `os.remove` 删的是链接本身，不碰目标——这正是要的。
    for stale in _STAGED_FILES:
        sp = os.path.join(out_dir, stale)
        if os.path.lexists(sp):
            os.remove(sp)
    # CP-E 自证材料落盘（只验收通路；消费方见 `_read_acceptance_inputs`）。
    # 位置刻意在**清残留之后、Task1 之前**：清在前才不会把本轮刚落的副本删掉。
    staged_source_facts = (_write_staged_inputs(out_dir, staged_payloads)
                           if staged_payloads is not None else None)
    # Task 1
    caseset = gen_cases.gen_cases(spec, work, taskdoc_caseset=taskdoc_caseset)
    _assert_staged_golden_matches_task1(spec, staged_payloads)
    _dump(caseset, "caseset.json")
    print(f"[Task1 gen_cases] {len(caseset['cases'])} 用例")
    _gu = (caseset.get("golden_unavailable") or {}).get("count", 0)
    if _gu:
        # 一等状态就要一等的可见度：无 golden 的 case 数必须**在编排层日志里出现**，
        # 不能只躺在 caseset 里等人翻。它们不进精度分母、也不进性能候选池，由门判 BLOCKED。
        print(f"[Task1 gen_cases] ⚠ golden_unavailable {_gu} 条（任务书用例，参考实现算不出 golden）"
              f"——已退出精度维与性能候选池，身份与输入仍保留，须按 BLOCKED 记账、不得当通过")
    if mode == "cpp_extension":
        cpp_extension_adapter.prepare(spec, caseset, work)
        print("[CP-C0 cpp_extension] 已生成官方 Extension bundle 与逐 case invocation plan；"
              "build/load/NPU 见证由显式外部 driver 回传收据后复核")
    # aclnn_py 无 per-op runner 源，因此 CP-C 由真机 harness 信任门接住。这里在正式 adapter
    # 启动前复核内容寻址收据与**本轮重新生成的完整 caseset**、当前 spec 及 harness 执行逻辑
    # 全部仍绑定；缺失/漂移一律停在 CP-C。收据只验证 harness，不改变或裁剪下方 Task2/Task3。
    if mode == "aclnn_py":
        try:
            trust = verify_aclnn_harness.validate_receipt(
                os.path.abspath(out_dir), "work/aclnn_harness_trust.json",
                spec, caseset)
        except (OSError, RuntimeError, TypeError, ValueError) as ex:
            # `content_address.ContentAddressError` 是 ValueError 子类；保持这里不额外 import，
            # 同时让错误收敛成清晰的 CP-C blocker，而非进 adapter 后才跑出半轮真机产物。
            raise SystemExit(
                "[aclnn_py] CP-C harness 真机信任门未通过或收据已漂移：\n"
                f"{ex}\n"
                "请先运行 verify_aclnn_harness.py 生成 "
                "work/aclnn_harness_trust.json；正式 Task2/Task3 未启动。")
        print("[CP-C harness trust] "
              f"{trust['status']} · 见证 {trust['coverage']['selected_count']}/"
              f"{trust['coverage']['full_case_count']}（正式用例仍全量执行）")
    _emit_perf_plan(spec, work)
    # Task 2
    # defect 只在测试夹具下非 None；平时**不传该 kwarg**，让 adapter 侧的签名怎么演化都不影响生产路径。
    evidence = (repo_adapter.MODES[mode](caseset, work, defect_cases=defect) if defect
                else repo_adapter.MODES[mode](caseset, work))
    if mode == "aclnn_py":
        # CP-F 后验重测必须能从首次 evidence 绑定实际生效的 build + golden source。
        # build provenance 来自 adapter 的真机已核事实；golden source 来自本轮刚通过的
        # harness trust receipt。这里只补 envelope provenance，不参与本轮 pass/fail。
        execution_provenance = evidence.get("execution_provenance")
        golden_source = ((trust.get("bindings") or {}).get("golden_source")
                         if isinstance(trust, dict) else None)
        if isinstance(execution_provenance, dict) and isinstance(golden_source, dict):
            execution_provenance["golden_source_sha256"] = golden_source.get("sha256")
            execution_provenance["build_receipt_sha256"] = content_address.content_digest(
                "oprunway/aclnn-build-provenance/v1",
                {k: v for k, v in execution_provenance.items()
                 if k != "build_receipt_sha256"})
    _dump(evidence, "evidence.json")
    # 证据等级：优先取 adapter **自报**的 evidence_grade（catlass_adapter 已有此字段）；缺失则按模式兜底。
    # 只降不升——adapter 说自己是 development，就按非验收办，绝不因为「模式看着像真机」把它抬回验收级。
    grade = evidence.get("evidence_grade") if isinstance(evidence, dict) else None
    if is_acceptance and isinstance(grade, str) and grade and grade != _ACCEPTANCE_GRADE:
        is_acceptance = False
        print(f"[非验收] adapter 自报 evidence_grade={grade!r} → 本次不产验收裁决")
    if not (isinstance(grade, str) and grade):
        grade = _ACCEPTANCE_GRADE if is_acceptance else _DEV_GRADE
    verdict = validator.validate(spec, caseset, evidence)
    if is_acceptance:
        _assert_acceptance_form_allowed(spec, mode)     # ② 出口门，见该函数的 ⚠
        # spec 变更门 · ② 出口门（**verdict.json 也是验收产物**，不能只守 acceptance.json）。
        # 堵的是「入口过了之后 spec 被换掉」：那样产物目录里的裁决与实际驱动执行的 spec 对不上。
        spec_change_gate.assert_confirmed(spec_path, out_dir, _SPEC_GATE_EXIT)
        _dump(verdict, "verdict.json")
    else:   # 非验收通路：精度判定照跑（管路自检要它），但**不写 verdict.json**——mock 下 out=golden.copy()，
            # 那份「pass」是构造出来的，落成验收裁决文件名就是伪证。
        verdict["evidence_grade"] = grade
        verdict["acceptance_note"] = non_acceptance_note
        _dump(verdict, _DEV_VERDICT_FILE)
    o = verdict["overall"]
    print(f"[Task2 run+validate] 裁决={o['verdict']} {o['counts']}")
    # 人工复现制品与验收裁决物理/语义解耦：只消费已落盘 Task2 事实，生成失败不改写 verdict。
    # 当前先接 cpp_extension backend；其它 runner form 待各自提供稳定的单 case replay 后再登记。
    if mode == "cpp_extension":
        try:
            repro = repro_artifacts.generate_cpp_extension(out_dir, caseset, verdict)
            print(f"[Task2 repro] 已生成 {repro['case_count']} 个逐 case 人工复现启动脚本")
        except (OSError, RuntimeError, TypeError, ValueError) as ex:
            _dump({
                "schema": "oprunway.repro_generation_error",
                "schema_version": 1,
                "backend": "cpp_extension",
                "error": f"{type(ex).__name__}: {ex}",
                "acceptance_verdict": None,
                "note": "复现制品生成失败，不改变验收裁决",
            }, "repro_generation_error.json")
            print(f"[Task2 repro] 生成失败（不改变验收裁决）：{type(ex).__name__}: {ex}")
    gpu_prov = None
    # §精度门前置 + fail-fast（用户 2026-07-15，评审 #4）：精度非全过（pass/passed_with_risk）→ **跳过 Task3 性能**、
    # 提前结束。**不 early-return**——照走下方统一 overall/门流程（gate/runner_source 优先级不变、prec==fail 自然
    # 落 FAIL(精度)），只是不跑 perf_compare、不把 task3 加入门。fail-fast 粒度=跑完精度再判（精度已在 Task2 全跑）。
    # passed_with_gaps（C4：任务书要求的 dtype 算子 op_def 不支持、差额挂 task_pr_gaps）**精度本身是全过的**，
    # 必须与 pass 同样继续跑 Task3——漏掉它会静默跳过性能、且归因错成「无性能用例」。
    precision_ok = o["verdict"] in ("pass", "passed_with_risk", "passed_with_gaps")
    # §5.10 · 第二层性能总门的 measure_only 分流（C3/C4）。
    # 病灶：上面这道 fail-fast 是为 **ratio_gated** 设计的——精度没全过时，比值裁决确实没有意义
    # （拿判错的输出去比耗时，比出来的「达标」是假的）。但 `measure_only` **本来就不产比值裁决**，
    # 它要的只是「这批 case 在真机上跑出来的 kernel 耗时」；一条 golden 算不出来或一条 DUT 精度失败，
    # 就把**整轮已经采到的 msprof 数据全部丢掉、产一份零数据 perf_report**，那不是严谨，是把
    # 已有的真实证据扔了（实测：taskdoc 169 条里只要有 1 条挂，性能维就归零）。
    # 分流后：`measure_only` 继续消费已采到的实测数据，**精度分母原样保留**——
    # 性能维在这一档本就不贡献 pass/fail，下面 overall 仍会按 `prec == "fail"` 落 FAIL(精度)，
    # 性能数据只是被如实记下来，绝不据此宣称精度或任务书整体通过。`ratio_gated` 行为逐字不变。
    perf_skipped_by_precision = (not precision_ok) and not measure_only
    # 批 5：`blocked_golden_unauthorized` **不在放行集**——真值来路不明时，连性能对比都没有意义
    #（拿一份不知对不对的 golden 判过的「精度通过」去支撑「性能达标」，是把无效结论往下传）。
    # 批 5：golden 授权核不实 → 直接 BLOCKED，且**排在所有别的判定之前**。
    # 来路不明的真值下，「精度 fail」「性能未达」这些结论都没有意义，不该被报成那些。
    if o["verdict"] == "blocked_golden_unauthorized":
        _gb = o.get("golden_blocked") or []
        _why = "; ".join(f"tier{t.get('tier')}:{t.get('blocked_reason')}" for t in _gb) or "?"
        print(f"[Task2] golden 授权核不实 → BLOCKED（{_why}）——"
              f"真值来路不明，基于它的精度判定不成立；跳过 Task3。")
    if perf_skipped_by_precision:
        report = {"op": spec["op"], "baseline_source": None, "target_ratio": None, "per_case": [],
                  "notes": [f"精度未全过（{o['verdict']}）→ 跳过性能测试（fail-fast，精度已全跑再判）"],
                  "summary": {"perf_cases": 0, "cases_scored": 0, "达标": 0, "blocked": 0,
                              "status": "skipped_precision_gate"}}
        report = perf_compare.attach_skipped_shape_plan(report, caseset)
        _dump(_stamp_dev(report, is_acceptance, grade, non_acceptance_note),
              "perf_report.json")
        print(f"[Task3 perf_compare] 跳过（精度={o['verdict']} 未全过 → fail-fast）")
    else:
        # Task 3（new_example 会写真基线 _real_baseline.json；否则 mock；T8：--gpu-baseline / spec gpu_external）
        real_bl = os.path.join(work, "_real_baseline.json")
        expect_gpu = (gpu_baseline is not None
                      or spec.get("perf", {}).get("baseline") in ("gpu", "gpu_external"))
        expect_source = "gpu_external" if expect_gpu else None
        baseline_blocked_status = None  # gb-9：标杆被判废时携专门挂起码（区分「口径不可比」vs「标杆无效」vs「缺标杆」）
        if measure_only:
            # §5.10 只测不比：**一条基线取数路径都不走**（不读 _real_baseline.json、不解析 GPU
            # 标杆、不落 mock、更不进 _real_baseline_or_blocked）。baseline 恒 None 是这条路的
            # **正常态**，不是「缺标杆」——所以必须排在下面所有取基线分支之前。
            baseline = None       # gpu_baseline 冲突已在函数入口 fail-closed 拒过（零副作用处）
        elif gpu_baseline is not None:  # T8：解析外部 GPU 标杆(consumer 侧)；hard error→baseline None→挂起(非 PASS)
            import gpu_baseline as gpubl
            baseline, parse_report = gpubl.parse_gpu_baseline(gpu_baseline, caseset)
            _dump(parse_report, "gpu_baseline_parse_report.json")
            if baseline is None:  # gb-9：别把「有硬错的 baseline=None」等同「缺标杆」——据 parse 落正确挂起码
                baseline_blocked_status = parse_report.get("blocked_status") or "blocked_gpu_baseline_invalid"
            gpu_prov = {"source": expect_source, "path": gpu_baseline,
                        "contract_version": parse_report.get("contract_version"),
                        "parse_report": "gpu_baseline_parse_report.json",
                        "hard_errors": parse_report.get("hard_errors", 0),
                        "blocked_status": baseline_blocked_status}
        elif os.path.exists(real_bl):
            baseline = json.load(open(real_bl, encoding="utf-8"))
        elif expect_gpu:  # 期待 GPU 标杆但没给 → 正规挂起（perf_compare 产 blocked_wait_gpu_benchmark）
            baseline = None
        elif not is_acceptance:
            # 非验收通路（mock / catlass_mock / 被 adapter 降级的任何一轮）：mock 基线仍可用——
            # 这条路**物理上不写** acceptance.json / verdict.json，且 perf_compare + _stamp_dev 会给
            # 报告打 NON-ACCEPTANCE 戳，「达标」不可能被当成验收结论。
            baseline = perf_compare.mock_baseline(spec, evidence, slow_cases=perf_slow)
        else:
            # ★ High#2：**验收通路禁 mock 兜底**。真数或挂起，二选一（详见 _real_baseline_or_blocked）。
            baseline, baseline_blocked_status = _real_baseline_or_blocked(spec, work)
        if baseline is not None:
            _dump(baseline, "baseline.json")
        report = perf_compare.perf_compare(spec, caseset, evidence, baseline, expect_source=expect_source,
                                           baseline_blocked_status=baseline_blocked_status)
        if not precision_ok:
            # §5.10 分流留痕（机读 + 人读各一份）：这份实测数据是在**精度未全过**的前提下采到的。
            # 措辞红线同 §5.10：只说「测了什么」，一个字都不许读成「精度或条款通过」。
            report["precision_gate"] = {
                "precision_verdict": o["verdict"],
                "decoupled_by": "perf.mode=measure_only",
                "note": "性能维在 measure_only 下不产裁决，故与精度裁决解耦；精度分母仍以 verdict.json 为准",
            }
            report.setdefault("notes", []).append(
                f"⚠ 精度维未全过（{o['verdict']}）。按 §5.10 measure_only 口径，性能维只实测不裁决、"
                "与精度裁决解耦，故本轮**继续**记录已采到的 NPU kernel-only 实测耗时；"
                "这**不表示**精度通过、更不表示任务书性能条款通过，失败明细见 verdict.json。")
        if report["summary"].get("status") == "exception":  # T6：例外态渲染仿真图，门循环前落盘+记 sha
            import perf_sim_plot
            svg_name = f"perf_sim_{spec['op'].lower()}.svg"
            svg_path = os.path.join(out_dir, svg_name)
            perf_sim_plot.render_svg(report["simulation"], svg_path)
            report["simulation_plot"] = {"file": svg_name, "sha256": perf_sim_plot.sha256_of(svg_path)}
        _dump(_stamp_dev(report, is_acceptance, grade, non_acceptance_note),
              "perf_report.json")
        print(f"[Task3 perf_compare] {report['summary']} (基线={report['baseline_source']})")
        if report.get("acceptance_note"):
            print(f"[Task3 perf_compare] ⚠ {report['acceptance_note']}")

    ps = report["summary"]
    # 验收门（硬 blocker）：三级机器门读**落盘产物**独立复核（防跑子集/放宽阈值/混 e2e）。
    # 无性能要求的算子不跑 task3 门（免因缺性能用例误挡）；精度未全过跳了 Task3 → 也不加 task3 门（评审 #4）。
    #
    # C5：非验收通路降级为**管路自检**，且只跑 task1（+task3）。两条理由，缺一不可：
    #   ① task2 门读 `verdict.json`，而该文件在非验收通路上物理不产 → 这级本来就无从跑起；
    #   ② 让 mock 跑穿一道叫「验收门」的东西再打印 STATUS: PASSED，本身就是危害源
    #      （dev-doc/oprunway-todo-plans.md #6 记的正是「mock 跑穿门被误当 NPU evidence」这条风险）。
    #   自检仍卡 caseset 自洽 / 跑子集 / perf 产物完整——CP-B 想要的那点自检价值一分没少。
    gate_stages = ["task1", "task2"] if is_acceptance else ["task1"]
    # measure_only 也必须挂 task3 门：万一它一条性能 case 都没产出，要的是门报 `no_perf_cases`
    # 并 BLOCKED，而不是「没有性能用例 → 静默跳过门 → 干净 PASS」（那正是 fail-open）。
    # ⚠ 条件仍是 `precision_ok`，**故意不因 measure_only 分流而放开**：精度没过时 overall 已经是
    #   FAIL(精度)，此时再挂 task3 门只会让「性能产物不完整」把结论改写成 BLOCKED(验收门未过)，
    #   把真正该被看见的精度失败盖掉。分流拿到的性能数据照样落盘可读，只是这一轮不参与门。
    if precision_ok and (ps.get("perf_cases", 0) > 0 or spec.get("perf", {}).get("baseline")
                         or measure_only):
        gate_stages.append("task3")
    gate_errs = {}
    for st in gate_stages:
        es = []
        # ★ **每次都显式传 `source_facts_path`**，不让门退回自动发现。
        # 验收通路上它必然是本轮 staging 出来的那份副本（`None` 只可能出现在非验收通路，
        # 而那条路只跑 task1/task3，两级门都不消费来源锚）。显式指路后，「找不到对照物」
        # 在验收通路上不再是一个可达状态——这正是要封掉的那处残留伪装面。
        gate._GATES[st](out_dir, es, source_facts_path=staged_source_facts)
        if es:
            gate_errs[st] = es
    gate_passed = not gate_errs
    gate_label = "验收门" if is_acceptance else "管路自检(非验收门)"
    print(f"[{gate_label}] {'/'.join(gate_stages)} → STATUS: {'PASSED' if gate_passed else 'FAILED'}"
          + ("" if gate_passed else f" · {gate_errs}"))

    # 总体口径：精度(放行看 acceptance) + 性能 + 验收门都要过（门 FAILED 一票否决，不出 pass）。
    # 精度 verdict ∈ {pass, fail, needs_review, passed_with_risk}；放行只看 acceptance（ADR 0005）。
    perf_pass = (ps.get("status") == "ok" and ps.get("blocked", 0) == 0
                 and ps.get("perf_cases", 0) == ps.get("达标", 0))
    # §5.10：measure_only 的「性能维完成」= 每条性能 case 都真有 msprof 实测，**与达标无关**
    # （这里没有达标这件事）。它只表示「性能维不阻挡 overall」，绝不表示「性能通过」。
    perf_measured_only = (measure_only
                          and ps.get("status") == perf_mode.STATUS_MEASURED
                          and ps.get("blocked", 0) == 0
                          and ps.get("perf_cases", 0) > 0
                          and ps.get("perf_cases", 0) == ps.get("measured", -1))
    ov = verdict["overall"]
    prec = ov["verdict"]
    requires_human_cp = False       # T6：PASSED_WITH_RISK 走人工 CP（挂起转人工，非自动合并/失败）
    # fail-closed：new_example（真机）模式 runner_source 必须为 "user"（引擎不回退插件样例，fallback 已退役
    # 2026-07-20，撤销 a7c8417 的「可以带样例」兜底）。runner 现是引擎的**输出**、非组件——只有「为本任务
    # 生成/用户放置的 runner」才合法。
    #   user           → 正常走后续裁决；
    #   其它/缺失/未知   → 无法确认跑的是谁的 runner（含伪造的 builtin_sample），一律 BLOCKED。
    # provenance 见 evidence.runner_source（repo_adapter.find_runner 写入，恒 "user"）。
    runner_source = evidence.get("runner_source")
    if not gate_passed:
        overall = "BLOCKED(验收门未过)" if is_acceptance else "BLOCKED(管路自检未过)"
    elif mode in _REAL_MACHINE_MODES and not _runner_source_allowed(mode, runner_source):
        overall = (
            f"BLOCKED(runner_source 与 mode={mode!r} 不匹配/缺失: {runner_source!r})")
    elif prec == "blocked_golden_unauthorized":
        # 批 5：真值来路不明 → 无从得出结论。**不能报成 FAIL(精度)**——那会让人去查算子、查错方向。
        # 排在 fail 之前：来路不明的真值下，「精度 fail」这个结论本身就不成立。
        overall = "BLOCKED_GOLDEN_UNAUTHORIZED"
    elif prec == "fail":
        overall = "FAIL(精度)"
    elif prec == "blocked_golden_unavailable":
        # 参考实现算不出真值的 case 存在、且**没有**任何真实精度失败 → 结论是**空白**，不是通过。
        # 排在 fail 之后：真查出来的缺陷优先报（两者并存时仍报 FAIL(精度)，名单照样进产物）。
        # ⚠ 也**不能**报成 pass：那等于拿「能判的那部分全过」替整份用例集背书。
        overall = "BLOCKED_GOLDEN_UNAVAILABLE"
    elif prec == "needs_review":
        overall = "NEEDS_REVIEW"
    elif not (perf_pass or perf_measured_only):          # 精度 pass/passed_with_risk，但性能有问题
        st = ps.get("status")
        if measure_only:
            # §5.10：这条路上没有「未达成」这回事（没设过目标）。唯一的失败形态是**实测没采全**。
            overall = _MEASURE_INCOMPLETE_OVERALL
        elif st == "exception":                          # T6 小shape例外：门已过(有图+交叉一致)→放行需人核
            overall, requires_human_cp = "PASSED_WITH_RISK", True
        elif st == "blocked_wait_gpu_benchmark":         # T8 缺外部 GPU 标杆：正规挂起、非 fail
            overall = "BLOCKED_WAIT_GPU_BENCHMARK"
        elif st == "blocked_incomparable_timing_scope":  # T8 双边口径不可比（含 GPU 标杆内部混合 scope，gb-9）
            overall = "BLOCKED_INCOMPARABLE_TIMING_SCOPE"
        elif st == "blocked_gpu_baseline_invalid":       # gb-9 外部 GPU 标杆有硬错被判废（≠缺标杆）
            overall = "BLOCKED_GPU_BASELINE_INVALID"
        elif st == _BLOCKED_WAIT_REAL_BASELINE:          # High#2 验收通路缺真实基线：正规挂起、非 fail 非 pass
            overall = _BLOCKED_WAIT_REAL_BASELINE_STATE
        elif ps.get("perf_cases"):
            overall = f"性能未达成({st})"
        elif spec.get("perf", {}).get("baseline"):
            overall = "BLOCKED(spec 声明性能目标但无性能用例)"
        elif prec == "passed_with_risk":            # 无性能要求 + 精度带风险 → 仍走人工 CP
            overall, requires_human_cp = "PASSED_WITH_RISK", True
        elif prec == "passed_with_gaps":            # 无性能要求 + dtype 挂账 → 人工 CP（**绝不落干净 PASS**）
            overall, requires_human_cp = "PASSED_WITH_GAPS", True
        else:
            overall = "PASS(无性能要求)"
    elif prec == "passed_with_risk":                     # 精度带风险(任务书宽于平台底线)、性能达标 → 人工 CP
        overall, requires_human_cp = "PASSED_WITH_RISK", True
    elif prec == "passed_with_gaps":                     # dtype 挂账、性能达标 → 人工 CP（C4）
        overall, requires_human_cp = "PASSED_WITH_GAPS", True
    elif perf_measured_only:      # §5.10：精度 pass，性能**只实测未裁决**——绝不落笼统的 "PASS"
        overall = _MEASURED_ONLY_OVERALL
    else:                                                # prec == pass 且性能达标
        overall = "PASS"
    state = _canonical_state(overall, ps)   # T6/T8：机读 canonical 状态（人读串仍 overall）
    exit_code = _exit_code(overall)         # T5：退出码枚举 0 干净 / 2 PASSED_WITH_RISK / 1 其余
    # 措辞红线（§5.10）：measure_only 下**一个「达标」字都不许打印**——那一栏根本没判过。
    perf_line = (f"性能实测 {ps.get('measured')}/{ps.get('perf_cases')}（未做标杆对比）"
                 if measure_only else f"性能达标 {ps.get('达标')}/{ps.get('perf_cases')}")
    print(f"[总体] 精度={prec} · 风险 {ov['counts'].get('risk', 0)} · {perf_line}"
          f"({ps.get('status')}) · {gate_label}={'PASSED' if gate_passed else 'FAILED'} → {overall}"
          + (" · requires_human_cp（挂起转人工）" if requires_human_cp else ""))

    # 门控后的**验收裁决**（区别于 raw verdict.json=validator 精度判定）：上游产物即下游输入。
    # T5 三层 pass 明细 + risk 说明；T6/T8 机读 state + 挂起证据(human_cp) + GPU 标杆 provenance。
    human_cp = None
    if requires_human_cp:  # T6：机器只产证据挂 pending，真正人工 CP 留会话 agent 形态（codex H3/D4）
        ev_files = ([f"perf_sim_{spec['op'].lower()}.svg", "perf_report.json#simulation"]
                    if ps.get("status") == "exception" else [])
        human_cp = {"status": "pending", "evidence": ev_files,
                    "note": "机器产证据挂 pending；真正人工 CP 由会话 agent(可 AskUserQuestion)补"}
    three_layer = {"catlass_compare_na": verdict.get("catlass_compare_na", []),
                   "risk_cases": ov.get("risk", []),
                   "uncertain_cases": ov.get("uncertain", []),
                   "note": "放行只看 acceptance_precision_pass；risk=acceptance 过但 standard 不过 → 人工 CP"}
    if is_acceptance:
        # ⚠ 验收通路的 acceptance.json **一个字段都没加**（本轮红线：真机通路不动）。证据等级另有出处：
        #   evidence.json 的 `evidence_grade`（repo_adapter 写）+ 本函数返回值 —— 且「acceptance.json 存在」
        #   本身已经等价于「这是验收级证据」，再塞一遍是冗余。
        acc = {"op": spec["op"], "overall": overall, "state": state, "exit_code": exit_code,
               "requires_human_cp": requires_human_cp, "repo_mode": mode,
               "gate": {"passed": gate_passed, "errors": gate_errs},
               "precision_verdict": prec, "perf_status": ps.get("status"),
               "three_layer": three_layer}
        if measure_only:
            # 只在 measure_only 下多写这两项（ratio_gated 的 acceptance.json 逐字节不变）。
            # 机读方据此知道「性能维本轮没有裁决」，而不是从 overall 字符串里猜。
            acc["perf_mode"] = perf_mode.MODE_MEASURE_ONLY
            acc["perf_note"] = perf_mode.MEASURE_ONLY_NOTE
        if human_cp is not None:
            acc["human_cp"] = human_cp
        if gpu_prov is not None:
            acc["gpu_baseline"] = gpu_prov
        _assert_acceptance_form_allowed(spec, mode)     # ② 出口门（acceptance.json 侧），见该函数的 ⚠
        # spec 变更门 · ② 出口门（acceptance.json 侧）。同准入门口径：两处产物各校一次。
        spec_change_gate.assert_confirmed(spec_path, out_dir, _SPEC_GATE_EXIT)
        final_file = _dump(acc, "acceptance.json")
        try:
            md_file = render_acceptance_markdown.write_report(out_dir)
            print(f"[Markdown 报告] {md_file}")
        except (OSError, RuntimeError, TypeError, ValueError, KeyError) as ex:
            _dump({
                "schema": "oprunway.markdown_report_error",
                "schema_version": 1,
                "error": f"{type(ex).__name__}: {ex}",
                "acceptance_verdict": None,
                "note": "Markdown 渲染失败，不改变 JSON 验收裁决",
            }, "markdown_report_error.json")
            print(f"[Markdown 报告] 生成失败（不改变 JSON 裁决）：{type(ex).__name__}: {ex}")
    else:
        # C5 非验收产物：**字段名也换掉**，不只是加个注脚。`overall` / `state` / `precision_verdict` 是验收裁决
        # 的词汇，留着就还能被 `acc["state"] == "PASSED"` 这类代码顺手当裁决读；换成 pipeline_* 后，任何想拿它
        # 冒充验收的地方都得先改代码——把「顺手误用」变成「明知故犯」。
        dev = {"op": spec["op"], "repo_mode": mode,
               "evidence_grade": grade, "acceptance_note": non_acceptance_note,
               "is_acceptance": False,
               "pipeline_result": overall,      # 人读串；**不是**验收裁决
               "exit_code": exit_code,
               "precision_check": prec,         # mock 下 out=golden.copy()，这个 "pass" 是构造出来的
               "perf_status": ps.get("status"),
               "requires_human_cp": requires_human_cp,
               **({"perf_mode": perf_mode.MODE_MEASURE_ONLY,
                   "perf_note": perf_mode.MEASURE_ONLY_NOTE} if measure_only else {}),
               "selfcheck": {"stages": gate_stages, "passed": gate_passed, "errors": gate_errs,
                             "note": "管路自检（caseset 自洽 / 防跑子集 / perf 产物完整），"
                                     "**非**验收门——验收门只对真机 evidence 有意义"},
               "three_layer": three_layer}
        if human_cp is not None:
            dev["human_cp"] = human_cp
        if gpu_prov is not None:
            dev["gpu_baseline"] = gpu_prov
        final_file = _dump(dev, _DEV_SUMMARY_FILE)
    print(f"--- 产物在 {out_dir}/ ---（本次总结: {os.path.basename(final_file)}）")
    if not is_acceptance:
        print(f"--- ⚠ {non_acceptance_note} ---")
    return {"verdict": verdict, "perf_report": report,
            "gate": {"passed": gate_passed, "errors": gate_errs}, "overall": overall,
            "state": state, "exit_code": exit_code, "requires_human_cp": requires_human_cp,
            # C5：进程内调用方据此分辨「这轮到底算不算验收」，别只看 overall 字符串。
            "is_acceptance": is_acceptance, "evidence_grade": grade,
            "summary_file": os.path.basename(final_file)}


def main():
    # C5：**`--defect` 与 `--perf-slow` 都已从 CLI 拿掉**（后者 2026-07-22 补，同批理由）。两者都靠 mock
    # 造假数——一个造坏点、一个把假基线调慢好触发小 shape 例外通道——唯一正当用途是回归测试
    # 「validator 真会 fail、门不是假门」，那个用途 `test_*.py` 直接
    # `import run_workflow; run_workflow.run(..., defect=[...], perf_slow=[...])` 就够了。
    # 挂在 CLI 上则等于对所有人开放「按需制造一份想要的结论」的入口，收益为零、风险实打实：
    # `--perf-slow` 能让本地跑出 `PASSED_WITH_RISK`(exit 2) 或「性能未达成」，那是一份**人造的**
    # 性能结论——mock 已不产 acceptance.json 削弱了它，但削弱的是「落成裁决文件」，**没削弱**终端
    # 输出/退出码/`baseline.json` 被人截图或抄进报告的那条路（本仓最不能容忍的「看起来对」）。
    # ⚠ 别因为「加回去方便调试/演示」就恢复它们：调试与演示请走进程内 API。
    ap = argparse.ArgumentParser(
        description="OpRunway Task1→2→3 编排。**正式验收裁决当前只由 cpp_extension 产出**"
                    "（唯一跑通完整 torch_parity 矩阵的通路）；cpp / aclnn_py 需 "
                    "--allow-experimental-form 才能跑，且只产 dev_run_summary.json / "
                    "dev_precision_check.json（均标 NON-ACCEPTANCE）。mock 等通路同理。"
                    "⚠ 验收通路还须先用 spec_change_gate.py 建立 spec 变更收据"
                    "（<out>/work/spec_change_receipt.json），否则进 Task1 之前即 "
                    "BLOCKED(spec 变更未确认)。")
    ap.add_argument("spec")
    ap.add_argument("--mode", default=None, choices=list(repo_adapter.MODES),
                    help="省略时据 spec.runner_form 派生：cpp→new_example、aclnn_py→aclnn_py、"
                         "cpp_extension→cpp_extension（spec 未写 runner_form 时按 cpp_extension "
                         "派生 —— 缺省跟着唯一准入形态走）。三条都是真机通路，但**只有 "
                         "cpp_extension 准入正式验收**。mock 仅本地用例链自检、精度按构造必过、**非验收**")
    ap.add_argument("--out", default="reports/_run")
    ap.add_argument("--source-facts", default=None, metavar="PATH",
                    help="fetch_source.py 产的 source_facts.json。**验收通路必给、缺席即拒跑**："
                         "三级门要拿它与 vendor build receipt 的来源锚逐字对账，没有对照物时"
                         "「收据自称 gitcode_pr、事实其实是 local_snapshot」查不出来。"
                         "本次会把它按字节 staging 进 --out（连同 spec.json / golden.py），"
                         "CP-F 与事后单独复跑三级门都直接消费该副本。非验收通路（mock 等）不需要")
    ap.add_argument("--gpu-baseline", default=None, help="外部 GPU 标杆 JSON（Task3 consumer 侧对比）")
    ap.add_argument("--allow-experimental-form", action="store_true",
                    help="允许用非准入的 runner_form（cpp / aclnn_py）跑局部开发验证。"
                         "该路径**物理上不产** acceptance.json / verdict.json，"
                         "只产带 evidence_grade=\"development\" 的非验收产物")
    ap.add_argument("--taskdoc-caseset", default=None,
                    help="规范化任务书用例集 taskdoc_caseset.json；"
                         "仅 spec.precision.case_source='taskdoc' 时需要（两向不匹配由 gen_cases fail-closed）")
    a = ap.parse_args()
    result = run(a.spec, a.mode, a.out, gpu_baseline=a.gpu_baseline,
                 allow_experimental_form=a.allow_experimental_form,
                 source_facts=a.source_facts,
                 taskdoc_caseset=a.taskdoc_caseset)
    # CLI 退出码：0 干净 PASS / 2 PASSED_WITH_RISK(挂起转人工) / 1 其余（门未过/精度fail/性能未达/BLOCKED/needs_review）
    sys.exit(result["exit_code"])


if __name__ == "__main__":
    main()
