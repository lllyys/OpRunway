"""OpRunway 顶层编排（Layer 2 薄壳的本地驱动版）——串 Task 1→2→3。

Task 1 gen_cases → Task 2 repo_adapter + validator → Task 3 perf_compare。
stage 间只经 JSON/数据文件交接。CC/Codex/Antigravity 的薄壳只需换调用方式，核心不动。

用法：python run_workflow.py <spec.json> [--mode new_example|mock] [--out <dir>]
默认 `--mode new_example`（真机通路，需 OPRUNWAY_* + NPU）；`mock` 仅本地用例链自检、精度按构造必过、非验收。

⚠ **验收裁决只有真机通路产得出来**（C5，用户 2026-07-22 拍板）。mock 的「NPU 输出」= `golden.copy()`
（精度按构造必过）、性能是 `_mock_us(numel)` 编的假数 + `perf_compare.mock_baseline` 的假基线——它跑出来的
「PASS」什么都不证明。历史上它却写出与真验收**同名同形**的 `acceptance.json` / `verdict.json`，那才是真危害。
现在：**非验收通路物理上不写这两个文件**，改写 `dev_run_summary.json` / `dev_precision_check.json`，
两者都带 `evidence_grade="development"` + `acceptance_note="NON-ACCEPTANCE (mock evidence)…"`
（字段名与措辞照 `catlass_adapter.run_catlass_mock` 的既有口径，不另发明）。
mock 通路本体**保留**（测试与本地演示照用），拔掉的只是「产验收裁决」这条出口。
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_cases, repo_adapter, validator, perf_compare  # noqa: E402
import validate_acceptance_state as gate  # noqa: E402

# —— C5 · 验收 / 非验收两套产物的口径（唯一定义处）——————————————————————————
_DEV_GRADE = "development"              # 照 catlass_adapter.run_catlass_mock
_ACCEPTANCE_GRADE = "acceptance_candidate"   # 照 catlass_adapter.run_catlass 的真机等级
_NON_ACCEPTANCE_NOTE = (
    "NON-ACCEPTANCE (mock evidence)：mock 的「NPU 输出」= golden.copy()（精度按构造必过）、"
    "性能是按元素数编的假数 + 假基线 —— 本产物只证管路接通，非 NPU 验收，不得作为验收结论引用")
# 非验收产物名：与验收产物 acceptance.json / verdict.json **物理隔离**（不同名 → 不可能被下游按老路径读走当裁决）
_DEV_SUMMARY_FILE = "dev_run_summary.json"     # ← 取代 acceptance.json
_DEV_VERDICT_FILE = "dev_precision_check.json"  # ← 取代 verdict.json
_ACCEPTANCE_FILES = ("acceptance.json", "verdict.json")
_DEV_FILES = (_DEV_SUMMARY_FILE, _DEV_VERDICT_FILE)
_REAL_MACHINE_MODE = "new_example"      # 唯一可能产验收裁决的通路（真 NPU 证据）


def _acceptance_capable(mode):
    """本模式**是否可能**产出验收裁决。**fail-closed**：只有真机通路算数，
    其余（mock / catlass_mock / 日后新增的任何模式）默认一律按非验收对待——
    新增模式忘了登记时的失败方向是「少产一份裁决」，而不是「多产一份假裁决」。"""
    return mode == _REAL_MACHINE_MODE


def _stamp_dev(obj, is_acceptance, grade):
    """非验收通路的产物打 NON-ACCEPTANCE 戳（幂等；验收通路原样返回、一个字节不动）。

    perf_compare 已对「消费 mock 基线」的报告自己打过戳；这里补的是它覆盖不到的情形——
    比如精度 fail-fast 时那份根本没跑 perf_compare 的 `perf_report.json`，以及 mock 通路里
    baseline 来自外部 GPU 标杆（基线是真的、但 NPU 侧证据是 mock 的）那种混合情形。
    `setdefault` 保证不覆盖 perf_compare 已写的措辞。"""
    if is_acceptance or not isinstance(obj, dict):
        return obj
    obj.setdefault("evidence_grade", grade)
    obj.setdefault("acceptance_note", _NON_ACCEPTANCE_NOTE)
    return obj


# T6/T8：人读 overall → 机读 canonical 状态（task3 状态机词汇）。
_STATE_MAP = {
    "PASS": "PASSED", "PASS(无性能要求)": "PASSED",
    "FAIL(精度)": "FAILED_PRECISION", "NEEDS_REVIEW": "NEEDS_REVIEW",
    "PASSED_WITH_RISK": "PASSED_WITH_RISK",
    "PASSED_WITH_GAPS": "PASSED_WITH_GAPS",   # C4：精度全过但任务书要求的 dtype 有差额挂账

    "BLOCKED_WAIT_GPU_BENCHMARK": "BLOCKED_WAIT_GPU_BENCHMARK",
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
    if overall in ("PASS", "PASS(无性能要求)"):
        return 0
    if overall in ("PASSED_WITH_RISK", "PASSED_WITH_GAPS"):
        return 2       # 挂起转人工——非自动失败、非干净 PASS。
                       # PASSED_WITH_RISK=任务书宽于平台底线；PASSED_WITH_GAPS=任务书要求的 dtype 算子没实现（C4）。
                       # ⚠ 后者**绝不能回 0**：那等于「算子没做到任务书要求」被 CI 读成干净通过、可自动合并。
    return 1


def run(spec_path, mode="new_example", out_dir="reports/_run", defect=None, perf_slow=None, gpu_baseline=None):
    """跑一遍 Task1→2→3。

    ⚠ `defect` / `perf_slow` 是**测试专用夹具**（在 mock 里造坏点 / 造略慢基线，用来证明「validator 真会 fail、
    门不是假门」），**两个都不在 CLI 上暴露**（C5 拿掉 `--defect`；`--perf-slow` 同批理由、2026-07-22 补下架）
    ——只有 `test_*.py` 以 `import run_workflow` 的方式进程内调用得到。它们只对非验收通路有意义；
    若作用于验收通路，本函数直接 fail-closed 拒跑。
    """
    if mode not in repo_adapter.MODES:  # 先校验，避免 Task1 已跑再 KeyError、留半产物
        raise SystemExit(f"unknown mode {mode!r}, supported={list(repo_adapter.MODES)}")
    if (defect or perf_slow) and _acceptance_capable(mode):
        # fail-closed：注入夹具 + 验收通路 = 「往验收证据里掺人造数据」。真机 adapter 现在只是忽略它们，
        # 但「被忽略」不是保证——这里直接拒跑，别指望下游的沉默。
        raise SystemExit(f"defect / perf_slow 是测试专用注入夹具，禁止作用于验收通路 mode={mode!r}——拒绝执行。")
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
    spec = json.load(open(spec_path, encoding="utf-8"))

    def _dump(obj, name):
        p = os.path.join(out_dir, name)
        json.dump(obj, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        return p

    is_acceptance = _acceptance_capable(mode)
    print(f"=== OpRunway workflow · {spec['op']} · mode={mode} ===")
    if not is_acceptance:
        print(f"=== ⚠ {_NON_ACCEPTANCE_NOTE} ===")
    for stale in ("_real_baseline.json", "perf_result.txt"):  # 清上轮残留，防 stale 真基线被复用
        sp = os.path.join(work, stale)
        if os.path.exists(sp):
            os.remove(sp)
    import glob  # T6：清上轮小shape仿真图，防 stale SVG 让「有图」门误过（codex H7）
    for old in glob.glob(os.path.join(out_dir, "perf_sim_*.svg")):
        os.remove(old)
    # C5：清掉**另一套**产物的上轮残留。同一个 out_dir 先跑真机、再跑 mock（或反过来）时，上轮的
    # acceptance.json / verdict.json 会原封不动躺在那儿，而下游（agent / 报告）是按文件名去读裁决的
    # → 「这次跑的是 mock，却读到上次真机的 acceptance.json」。宁可删掉重跑，也不留一份来源不明的裁决。
    # ⚠ **两套一起清，别按当前 is_acceptance 二选一**：is_acceptance 在下面还会被 adapter 自报的
    # evidence_grade **降级**（:169 那处「只降不升」）。按降级前的值二选一，降级发生时上一轮真机的
    # acceptance.json / verdict.json 会原样留下、与本轮 dev_* 并存——正是这段注释自己要堵的那个洞。
    # （现实暂不可达：run_new_example 恒报 acceptance_candidate。但这是潜伏洞，一行修掉不留。）
    for stale in _ACCEPTANCE_FILES + _DEV_FILES:
        sp = os.path.join(out_dir, stale)
        if os.path.exists(sp):
            os.remove(sp)
    # Task 1
    caseset = gen_cases.gen_cases(spec, work)
    _dump(caseset, "caseset.json")
    print(f"[Task1 gen_cases] {len(caseset['cases'])} 用例")
    # Task 2
    # defect 只在测试夹具下非 None；平时**不传该 kwarg**，让 adapter 侧的签名怎么演化都不影响生产路径。
    evidence = (repo_adapter.MODES[mode](caseset, work, defect_cases=defect) if defect
                else repo_adapter.MODES[mode](caseset, work))
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
        _dump(verdict, "verdict.json")
    else:   # 非验收通路：精度判定照跑（管路自检要它），但**不写 verdict.json**——mock 下 out=golden.copy()，
            # 那份「pass」是构造出来的，落成验收裁决文件名就是伪证。
        verdict["evidence_grade"] = grade
        verdict["acceptance_note"] = _NON_ACCEPTANCE_NOTE
        _dump(verdict, _DEV_VERDICT_FILE)
    o = verdict["overall"]
    print(f"[Task2 run+validate] 裁决={o['verdict']} {o['counts']}")
    gpu_prov = None
    # §精度门前置 + fail-fast（用户 2026-07-15，评审 #4）：精度非全过（pass/passed_with_risk）→ **跳过 Task3 性能**、
    # 提前结束。**不 early-return**——照走下方统一 overall/门流程（gate/runner_source 优先级不变、prec==fail 自然
    # 落 FAIL(精度)），只是不跑 perf_compare、不把 task3 加入门。fail-fast 粒度=跑完精度再判（精度已在 Task2 全跑）。
    # passed_with_gaps（C4：任务书要求的 dtype 算子 op_def 不支持、差额挂 task_pr_gaps）**精度本身是全过的**，
    # 必须与 pass 同样继续跑 Task3——漏掉它会静默跳过性能、且归因错成「无性能用例」。
    precision_ok = o["verdict"] in ("pass", "passed_with_risk", "passed_with_gaps")
    if not precision_ok:
        report = {"op": spec["op"], "baseline_source": None, "target_ratio": None, "per_case": [],
                  "notes": [f"精度未全过（{o['verdict']}）→ 跳过性能测试（fail-fast，精度已全跑再判）"],
                  "summary": {"perf_cases": 0, "达标": 0, "blocked": 0, "status": "skipped_precision_gate"}}
        _dump(_stamp_dev(report, is_acceptance, grade), "perf_report.json")
        print(f"[Task3 perf_compare] 跳过（精度={o['verdict']} 未全过 → fail-fast）")
    else:
        # Task 3（new_example 会写真基线 _real_baseline.json；否则 mock；T8：--gpu-baseline / spec gpu_external）
        real_bl = os.path.join(work, "_real_baseline.json")
        expect_gpu = (gpu_baseline is not None
                      or spec.get("perf", {}).get("baseline") in ("gpu", "gpu_external"))
        expect_source = "gpu_external" if expect_gpu else None
        baseline_blocked_status = None  # gb-9：标杆被判废时携专门挂起码（区分「口径不可比」vs「标杆无效」vs「缺标杆」）
        if gpu_baseline is not None:  # T8：解析外部 GPU 标杆(consumer 侧)；hard error→baseline None→挂起(非 PASS)
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
        else:
            baseline = perf_compare.mock_baseline(spec, evidence, slow_cases=perf_slow)
        if baseline is not None:
            _dump(baseline, "baseline.json")
        report = perf_compare.perf_compare(spec, caseset, evidence, baseline, expect_source=expect_source,
                                           baseline_blocked_status=baseline_blocked_status)
        if report["summary"].get("status") == "exception":  # T6：例外态渲染仿真图，门循环前落盘+记 sha
            import perf_sim_plot
            svg_name = f"perf_sim_{spec['op'].lower()}.svg"
            svg_path = os.path.join(out_dir, svg_name)
            perf_sim_plot.render_svg(report["simulation"], svg_path)
            report["simulation_plot"] = {"file": svg_name, "sha256": perf_sim_plot.sha256_of(svg_path)}
        _dump(_stamp_dev(report, is_acceptance, grade), "perf_report.json")
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
    #      （doc/oprunway-todo-plans.md #6 记的正是「mock 跑穿门被误当 NPU evidence」这条风险）。
    #   自检仍卡 caseset 自洽 / 跑子集 / perf 产物完整——CP-B 想要的那点自检价值一分没少。
    gate_stages = ["task1", "task2"] if is_acceptance else ["task1"]
    if precision_ok and (ps.get("perf_cases", 0) > 0 or spec.get("perf", {}).get("baseline")):
        gate_stages.append("task3")
    gate_errs = {}
    for st in gate_stages:
        es = []
        gate._GATES[st](out_dir, es)
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
    elif mode == "new_example" and runner_source != "user":
        overall = f"BLOCKED(runner_source 非 user/缺失: {runner_source!r})"
    elif prec == "fail":
        overall = "FAIL(精度)"
    elif prec == "needs_review":
        overall = "NEEDS_REVIEW"
    elif not perf_pass:                                  # 精度 pass/passed_with_risk，但性能有问题
        st = ps.get("status")
        if st == "exception":                            # T6 小shape例外：门已过(有图+交叉一致)→放行需人核
            overall, requires_human_cp = "PASSED_WITH_RISK", True
        elif st == "blocked_wait_gpu_benchmark":         # T8 缺外部 GPU 标杆：正规挂起、非 fail
            overall = "BLOCKED_WAIT_GPU_BENCHMARK"
        elif st == "blocked_incomparable_timing_scope":  # T8 双边口径不可比（含 GPU 标杆内部混合 scope，gb-9）
            overall = "BLOCKED_INCOMPARABLE_TIMING_SCOPE"
        elif st == "blocked_gpu_baseline_invalid":       # gb-9 外部 GPU 标杆有硬错被判废（≠缺标杆）
            overall = "BLOCKED_GPU_BASELINE_INVALID"
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
    else:                                                # prec == pass 且性能达标
        overall = "PASS"
    state = _canonical_state(overall, ps)   # T6/T8：机读 canonical 状态（人读串仍 overall）
    exit_code = _exit_code(overall)         # T5：退出码枚举 0 干净 / 2 PASSED_WITH_RISK / 1 其余
    print(f"[总体] 精度={prec} · 风险 {ov['counts'].get('risk', 0)} · 性能达标 {ps.get('达标')}/{ps.get('perf_cases')}"
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
        if human_cp is not None:
            acc["human_cp"] = human_cp
        if gpu_prov is not None:
            acc["gpu_baseline"] = gpu_prov
        final_file = _dump(acc, "acceptance.json")
    else:
        # C5 非验收产物：**字段名也换掉**，不只是加个注脚。`overall` / `state` / `precision_verdict` 是验收裁决
        # 的词汇，留着就还能被 `acc["state"] == "PASSED"` 这类代码顺手当裁决读；换成 pipeline_* 后，任何想拿它
        # 冒充验收的地方都得先改代码——把「顺手误用」变成「明知故犯」。
        dev = {"op": spec["op"], "repo_mode": mode,
               "evidence_grade": grade, "acceptance_note": _NON_ACCEPTANCE_NOTE,
               "is_acceptance": False,
               "pipeline_result": overall,      # 人读串；**不是**验收裁决
               "exit_code": exit_code,
               "precision_check": prec,         # mock 下 out=golden.copy()，这个 "pass" 是构造出来的
               "perf_status": ps.get("status"),
               "requires_human_cp": requires_human_cp,
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
        print(f"--- ⚠ {_NON_ACCEPTANCE_NOTE} ---")
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
        description="OpRunway Task1→2→3 编排。验收裁决(acceptance.json/verdict.json)只有真机通路 "
                    "new_example 产得出；mock 等非验收通路改产 dev_run_summary.json / "
                    "dev_precision_check.json（均标 NON-ACCEPTANCE）。")
    ap.add_argument("spec")
    ap.add_argument("--mode", default="new_example", choices=list(repo_adapter.MODES),
                    help="默认 new_example（真机通路，需 OPRUNWAY_* + NPU，是唯一产验收裁决的通路）；"
                         "mock 仅本地用例链自检、精度按构造必过、**非验收**")
    ap.add_argument("--out", default="reports/_run")
    ap.add_argument("--gpu-baseline", default=None, help="外部 GPU 标杆 JSON（Task3 consumer 侧对比）")
    a = ap.parse_args()
    result = run(a.spec, a.mode, a.out, gpu_baseline=a.gpu_baseline)
    # CLI 退出码：0 干净 PASS / 2 PASSED_WITH_RISK(挂起转人工) / 1 其余（门未过/精度fail/性能未达/BLOCKED/needs_review）
    sys.exit(result["exit_code"])


if __name__ == "__main__":
    main()
