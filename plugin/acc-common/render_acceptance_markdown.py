#!/usr/bin/env python3
"""把确定性 JSON 产物渲染为中文 Markdown；只展示，不重新裁决。"""

from __future__ import annotations

import argparse
import json
import os

import dut_source
# 只为复用 `_find_source_facts`（来源对照物的发现规则）。两处各写一份查找规则的话，
# 报告说的 facts 和三级门校的 facts 可能根本不是同一份文件。
# 无循环导入：`validate_acceptance_state` 不 import 本模块；它的 numpy 是惰性 import，
# 本模块（stdlib-only 的纯渲染器）不会因此被拖上 numpy 依赖。
import validate_acceptance_state as gate


def _load(root, name):
    with open(os.path.join(root, name), encoding="utf-8") as src:
        return json.load(src)


def _cell(value):
    if value is None:
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _pct(value):
    return "—" if value is None else f"{float(value) * 100:.2f}%"


def _n(value):
    """列表长度；不是 list 就渲染 `?`。

    ⚠ **不能退成 0**：把「收据没记清单」渲染成「0 项未提交改动」，就是凭空把 dirty 说小了。
    """
    return len(value) if isinstance(value, list) else "?"


def _gap_line(gap):
    if not isinstance(gap, dict):
        return f"- {_cell(gap)}"
    title = gap.get("issue", gap.get("kind"))
    detail = gap.get("impact", gap.get("reason"))
    line = f"- `{_cell(title)}`：{_cell(detail)}"
    if gap.get("pr_fact") is not None:
        line += f"（PR 事实：{_cell(gap['pr_fact'])}）"
    shown = {"issue", "kind", "impact", "reason", "pr_fact"}
    extra = {key: value for key, value in gap.items()
             if key not in shown and value is not None}
    if extra:
        line += "；补充：" + _cell(json.dumps(
            extra, ensure_ascii=False, sort_keys=True))
    return line


def _gap_items(value):
    if value is None or value == []:
        return []
    return value if isinstance(value, list) else [value]


# ---- 「来源与 provenance」节的措辞 -----------------------------------------------
# **全部落常量，不在 f-string 里就地手写**。理由不是洁癖：这一节的每句话都是对外的
# **provenance 强度声明**，措辞被顺手改软（「无法证明」→「未验证」、「未知」→「clean」）
# 等于悄悄抬高报告的可信度，而这种改动在 diff 里长得跟润色一模一样。集中成常量后，
# 测试可以直接引用常量断言，措辞一漂移就当场红。
PROV_HEADING = "## 来源与 provenance"
PROV_NO_RECEIPT = (
    "本轮 runner mode 的证据里没有 vendor build receipt，"
    "本报告不对被测来源作任何 provenance 断言。")
PROV_BAD_ANCHOR = (
    "⚠ vendor build receipt 的来源锚不合法：{ex}——本节不作任何 provenance 断言。")
PROV_UNKNOWN_KIND = (
    "⚠ vendor build receipt 声明的来源通路 `{kind}` 本渲染器尚无对应的强度陈述"
    "——本节不作任何 provenance 断言。")
# kind → 人话标签。**这不是 kind→锚字段名 的映射**（那个只有 `dut_source` 说了算），
# 只是把已判定的 kind 翻译成带强度说明的一句话。
PROV_KIND_LABEL = {
    dut_source.PULL_REQUEST:
        "线上 PR（`pull_request`）——可证明「验的就是这个 PR 的这个 commit」",
    dut_source.LOCAL_CHECKOUT:
        "本地 checkout（`local_checkout`）——只能证明「验的就是这份字节」",
}
# 锚**字段名** → 表头文案。键取自 `dut_source` 的返回值，本文件不自己决定哪个通路用哪个锚。
PROV_ANCHOR_LABEL = {
    "pr_head_sha": "PR head",
    "local_root_digest": "子树摘要 root_digest",
}
# 本地通路的两条 caveat：**只依赖 kind**，与 source_facts 在不在无关。
# 它们陈述的是 root_digest 这个锚**本身**的能力边界，不是某一轮取材的结果，
# 所以对照物缺席时也一个字都不能少。
PROV_LOCAL_CAVEATS = (
    "- ⚠ 本地 checkout **无法证明**它对应任何具体 PR：`root_digest` 锚定的是"
    "「验的就是这份字节」，不是「验的就是线上某个 PR 的某个 commit」。",
    "- ⚠ 子树摘要只覆盖 `op_subdir`，仓级构建脚本、公共头文件、代码生成器都不在内——"
    "`root_digest` 相同**不等于** vendor `.so` 相同。",
)
PROV_DIRTY_ROW = "| worktree 干净度 | {value} |"
PROV_DIRTY_UNKNOWN = (
    "**未知**——报告目录内没有可对账的 source_facts.json；**不得据此认定 worktree clean**")
PROV_DIRTY_IGNORED = (
    "**未知**——⚠ 报告目录内的 source_facts.json 与本轮收据的来源锚不一致，已忽略；"
    "**不得据此认定 worktree clean**")
PROV_DIRTY_CLEAN = "clean——source_facts 记录 worktree 无未提交改动"
PROV_DIRTY_DIRTY = (
    "**dirty**——worktree 有 {n} 项未提交改动（被测子树内 {n_op} 项）；"
    "git head 不代表被测字节，provenance 只靠 root_digest")
PROV_DIRTY_NOT_GIT = (
    "不适用——source_facts 记录本地目录不是 git 仓，provenance 只靠 root_digest")
PROV_DIRTY_MALFORMED = (
    "**未知**——source_facts 里的 `git.dirty` 不是布尔值；**不得据此认定 worktree clean**")
PROV_GIT_HEAD_ROW = "| git head（**信息字段，非 provenance 锚**） | `{sha}` |"


def _local_rows(facts, receipt_identity):
    """本地通路的**事实行**（worktree 干净度、git head 信息字段）；返回表格行列表。

    与 caveat 的分工不能混：`PROV_LOCAL_CAVEATS` 只依赖 kind、恒成立；本函数产出的行依赖
    `source_facts` 这份**外部对照物**。对照物缺席只该让事实行退成「未知」，
    绝不该顺手把 caveat 也一起吞掉。

    采信 `source_facts` 的**唯一**前提：它的来源三元组与本轮收据的来源三元组逐字全等。
    不全等（摘要对不上、两条通路的事实键混装、锚形态不合法）一律**整份忽略**，
    绝不「挑能用的字段凑一凑」——一份来源对不上的 facts，它里面的 dirty/head_sha 描述的是
    **另一份 checkout**，拿来填这张表就是把无关事实冒充本轮 provenance。

    ⚠ 最贵的一条：**「没有对照物」= 未知，不是 clean**。把「查不到脏」渲染成「干净」，
    等于凭空给一份可能 dirty 的 checkout 发 provenance 合格证。真正的阻断在
    `validate_acceptance_state` 的三级门，本渲染器不重判、只如实标注强度。
    """
    if not isinstance(facts, dict):
        # `gate._find_source_facts` 三态：dict / None（没找到）/ "__BAD__"（找到但读不出）。
        # 后两态在本节里**同权**——都是「拿不到可对账的对照物」，强度一样，都退「未知」。
        return [PROV_DIRTY_ROW.format(value=PROV_DIRTY_UNKNOWN)]
    try:
        if dut_source.identity(facts, where="source_facts") != receipt_identity:
            return [PROV_DIRTY_ROW.format(value=PROV_DIRTY_IGNORED)]
    except dut_source.DutSourceError:
        # 锚读不出来 == 对不上：处置相同，整份忽略，不降格采信。
        return [PROV_DIRTY_ROW.format(value=PROV_DIRTY_IGNORED)]
    local = facts.get(dut_source.FACTS_KEY[dut_source.LOCAL_CHECKOUT])
    git = local.get("git") if isinstance(local, dict) else None
    if not isinstance(git, dict):
        return [PROV_DIRTY_ROW.format(value=PROV_DIRTY_NOT_GIT)]
    dirty = git.get("dirty")
    if dirty is True:
        rows = [PROV_DIRTY_ROW.format(value=PROV_DIRTY_DIRTY.format(
            n=_n(git.get("dirty_files")), n_op=_n(git.get("dirty_files_in_op_subdir"))))]
    elif dirty is False:
        rows = [PROV_DIRTY_ROW.format(value=PROV_DIRTY_CLEAN)]
    else:
        # `is True` / `is False` 而不是真值判断：缺字段、None、字符串 "false" 都不是
        # 「干净」的证据，只能是未知。
        rows = [PROV_DIRTY_ROW.format(value=PROV_DIRTY_MALFORMED)]
    head = git.get("head_sha")
    if isinstance(head, str) and head:
        # ⚠ 这里直取 `git.head_sha` 是**信息字段**（这份 checkout 当时停在哪个 commit），
        # 不是锚——锚永远由 `dut_source.identity` 给。worktree 可能 dirty，它与被测字节
        # 没有绑定关系，所以必须**原地**标注，不能让审核员把它当 PR head 读。
        rows.append(PROV_GIT_HEAD_ROW.format(sha=_cell(head)))
    return rows


def _provenance_section(receipt, source, facts):
    """渲染「## 来源与 provenance」节；返回行列表（含节标题与结尾空行）。

    为什么单独成节、而不是继续在「被测物与运行环境」表里占两行：两条来源通路的
    provenance **强度不等**（PR head 能证明「验的就是这个 PR 的这个 commit」，
    本地 root_digest 只能证明「验的就是这份字节」），强度差异得成段说清；
    而且本地通路根本没有 PR head，硬渲染那一行只会渲染出一个「—」，
    看上去像「这次没记」而不是「这条通路压根不存在这个锚」。

    三条分支，顺序不能换：

      ① `receipt` 为空（`aclnn_py` / `cpp` / 压根没收据）→ 只声明「不作任何断言」并 return。
         ⚠ **必须先判 `receipt` 真假，不能只看 `source` 空不空**：调用点的
         `build_receipt.get("source") or {}` 会把「压根没收据」和「有收据但 source 坏了」
         抹平成同一个 `{}`。后者本该走 ② 报「来源锚不合法」，被抹平后就成了看起来无害的
         「本轮没有收据」——一条来源不可信的证据链就此静默降级为「正常的无收据通路」。
      ② 来源锚不合法 → 渲染 ⚠ 并 return。**异常必须在这里 catch 掉、不能外抛**：
         外抛虽被 `run_workflow` 的 except 兜住不崩，但整份 `验收报告.md` 就不产了，
         审核员连「锚不合法」这条最该看见的话都看不到（只剩一个 JSON 错误文件）。
      ③ 锚合法 → 按 kind 渲染；本地通路额外挂事实行与两条 caveat。

    `facts` 只透传给 `_local_rows`，本函数不按字段名读它——读法只有一处，判别式只有一份。
    """
    lines = [PROV_HEADING, ""]
    if not receipt:
        return lines + [PROV_NO_RECEIPT, ""]
    try:
        kind, anchor_field, anchor_value = dut_source.validate_build_receipt_source(
            source, where="vendor build receipt.source")
    except dut_source.DutSourceError as ex:
        return lines + [PROV_BAD_ANCHOR.format(ex=_cell(ex)), ""]
    if kind not in PROV_KIND_LABEL:
        # 受控词表扩了而本节没跟上 → 宁可什么都不断言：让一条未知强度的来源借着
        # 「渲染成功」看起来和 PR 通路一样硬，比少渲染一节贵得多。
        return lines + [PROV_UNKNOWN_KIND.format(kind=_cell(kind)), ""]
    lines += [
        "| 项目 | 值 |",
        "|---|---|",
        f"| 被测来源 | {PROV_KIND_LABEL[kind]} |",
        # `repo` 已由 `validate_build_receipt_source` 校过必填非空；这里只取值展示，
        # 不参与任何来源判别。
        f"| 源码仓 | `{_cell(source.get('repo'))}` |",
        # 锚字段名与锚值都来自 `dut_source`，本文件不自选字段、不做 `a or b` 兜底。
        f"| {PROV_ANCHOR_LABEL.get(anchor_field, anchor_field)} | `{_cell(anchor_value)}` |",
    ]
    if kind == dut_source.LOCAL_CHECKOUT:
        lines += _local_rows(facts, (kind, anchor_field, anchor_value))
        lines.append("")
        lines += list(PROV_LOCAL_CAVEATS)
    lines.append("")
    return lines


def _atomic_write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as out:
        out.write(text)
    os.replace(tmp, path)


def _precision_failure_detail(failed):
    lines = [
        "# 精度失败明细",
        "",
        "> 本文件由 `verdict.json` 确定性渲染，只展示既有裁决，不重新判断 pass/fail。",
        "",
        f"- 失败总数：**{len(failed)}**",
        "- 返回主报告：[验收报告.md](验收报告.md)",
        "- 审核主入口：`./repro/audit_case.sh <序号>`（一次显示接入、输入、接口、差异和阈值）",
        "",
        "| 序号 | case_id | 判据 | 查看用例 | 重放复现 |",
        "|---:|---|---|---|---|",
    ]
    for index, row in enumerate(failed, 1):
        case_id = row.get("case_id")
        lines.append(
            f"| {index} | `{_cell(case_id)}` | {_cell(row.get('判据'))} | "
            f"`./repro/review.sh show {index}` | `./repro/audit_case.sh {index}` |")
    lines += [
        "",
        "也可按 case_id 操作：",
        "",
        "- `./repro/show_case.sh <case_id>`：查看冻结输入、golden、policy 和原始 metrics。",
        "- `./repro/run_case.sh <case_id>`：在原验收环境重放。",
        "",
    ]
    return "\n".join(lines)


def _performance_failure_detail(non_passing, caseset):
    case_by_id = {
        case.get("id"): case
        for case in (caseset.get("cases") or [])
        if isinstance(case, dict) and isinstance(case.get("id"), str)
    }
    lines = [
        "# 性能失败明细",
        "",
        "> 本文件由 `perf_report.json` 确定性渲染；blocked、exception 等未通过状态按原字段展示，不自行归因为 DUT 失败。",
        "",
        f"- 未通过总数：**{len(non_passing)}**",
        "- 返回主报告：[验收报告.md](验收报告.md)",
        "",
        "| 序号 | case_id | outcome | dtype | 输入 shape | shape 类别 | NPU us | baseline us | speedup | 阈值 |",
        "|---:|---|---|---|---|---|---:|---:|---:|---:|",
    ]
    for index, row in enumerate(non_passing, 1):
        custom = row.get("custom") or {}
        baseline = row.get("baseline") or {}
        ratio = row.get("ratio", row.get("speedup"))
        shapes = [
            inp.get("shape") for inp in (row.get("inputs") or [])
            if isinstance(inp, dict)
        ]
        lines.append(
            f"| {index} | `{_cell(row.get('case_id'))}` | `{_cell(row.get('outcome'))}` | "
            f"`{_cell(row.get('dtype'))}` | `{_cell(shapes)}` | "
            f"`{_cell(row.get('shape_class'))}` | "
            f"{_cell(custom.get('us', row.get('npu_us')))} | "
            f"{_cell(baseline.get('us', row.get('baseline_us')))} | "
            f"{_cell(ratio)} | {_cell(row.get('target_ratio'))} |")
    lines += ["", "## 逐 case 审核", ""]
    for index, row in enumerate(non_passing, 1):
        case_id = row.get("case_id")
        case = case_by_id.get(case_id) or {}
        custom = row.get("custom") or {}
        baseline = row.get("baseline") or {}
        call = case.get("aclnn_call") or case.get("invocation") or {}
        symbol = call.get("symbol") if isinstance(call, dict) else None
        repro = row.get("repro")
        if isinstance(repro, dict):
            repro = repro.get("command") or repro.get("script")
        repro_text = (
            f"`{_cell(repro)}`"
            if isinstance(repro, str) and repro.strip()
            else "**缺单 case 性能重放能力（本轮产物未记录可执行入口）**"
        )
        lines += [
            f"### {index}. `{_cell(case_id)}`",
            "",
            f"- 结果类别：`{_cell(row.get('outcome'))}`",
            f"- 输入：`{_cell(json.dumps(row.get('inputs') or [], ensure_ascii=False, sort_keys=True))}`",
            f"- 属性：`{_cell(json.dumps(case.get('attrs') or {}, ensure_ascii=False, sort_keys=True))}`",
            f"- DUT 接口：`{_cell(symbol or call or '未记录')}`",
            f"- custom：behavior=`{_cell(custom.get('behavior'))}`，"
            f"scope=`{_cell(custom.get('scope'))}`，us=`{_cell(custom.get('us'))}`",
            f"- baseline：behavior=`{_cell(baseline.get('behavior'))}`，"
            f"scope=`{_cell(baseline.get('scope'))}`，us=`{_cell(baseline.get('us'))}`",
            f"- 实测 speedup：`{_cell(row.get('ratio', row.get('speedup')))}`；"
            f"要求阈值：`{_cell(row.get('target_ratio'))}`",
            f"- 确定性原因：{_cell(row.get('reason') or row.get('note'))}",
            f"- 单 case 性能重放：{repro_text}",
            "",
        ]
    lines += [
        "复核时以同目录的 `perf_report.json`、`evidence.json` 和原始 profiler 证据为准；"
        "本文件不把缺 baseline、scope 不可比或环境异常静默改判为 DUT 失败。",
        "",
    ]
    return "\n".join(lines)


def render(report_root, source_facts_path=None):
    report_root = os.path.realpath(report_root)
    acceptance = _load(report_root, "acceptance.json")
    verdict = _load(report_root, "verdict.json")
    perf = _load(report_root, "perf_report.json")
    evidence = _load(report_root, "evidence.json")
    caseset = _load(report_root, "caseset.json")

    op = acceptance.get("op") or verdict.get("op") or caseset.get("op") or "?"
    accuracy = verdict.get("accuracy_summary") or {}
    counts = (verdict.get("overall") or {}).get("counts") or {}
    receipt = evidence.get("cpp_extension_receipt") or {}
    runtime = receipt.get("runtime") or {}
    vendor = receipt.get("vendor") or {}
    build_receipt = vendor.get("build_receipt") or {}
    source = build_receipt.get("source") or {}
    # 来源对照物：**复用**三级门的发现规则（显式路径 → `<报告目录>/` → `<报告目录>/work/`），
    # 不在这里另写一份——两处规则一旦分叉，报告陈述的 facts 就不是门校过的那一份了。
    # 返回三态：dict / None（没找到）/ "__BAD__"（找到但读不出）。
    # ⚠ 后两态在本渲染器里**同权**，都当「未知」，绝不当 clean（见 `_local_rows`）。
    facts = gate._find_source_facts(report_root, source_facts_path)

    lines = [
        f"# {op} 算子验收报告",
        "",
        "> 本报告由确定性 JSON 产物渲染，只展示既有裁决，不重新判断 pass/fail。",
        "",
        "## 验收结论",
        "",
        "| 项目 | 结果 |",
        "|---|---|",
        f"| 最终裁决 | `{_cell(acceptance.get('overall'))}` |",
        f"| 状态 | `{_cell(acceptance.get('state'))}` |",
        f"| 精度裁决 | `{_cell(acceptance.get('precision_verdict'))}` |",
        f"| 性能状态 | `{_cell(acceptance.get('perf_status'))}` |",
        f"| 验收门 | `{'PASSED' if (acceptance.get('gate') or {}).get('passed') else 'FAILED'}` |",
        f"| runner mode | `{_cell(acceptance.get('repo_mode'))}` |",
        "",
        "## 审核员快速操作",
        "",
        "进入本报告目录后：",
        "",
        "```bash",
        "./repro/audit_case.sh 1",
        "```",
        "",
        "`audit_case.sh` 直接完成单 case 重放，并按五段展示 Torch 接入、输入、接口、差异阈值和结论。",
        "",
    ]
    # 来源与 provenance 排在「被测物与运行环境」**之前**：先说清「验的是哪份源码、这个
    # 结论能替它担保到什么程度」，再列 ELF/SoC/CANN 这些运行环境事实。
    # `source` 从这里起只作为本节的入参，本文件别处不再按字段名直取它。
    lines += _provenance_section(receipt, source, facts)
    lines += [
        "## 被测物与运行环境",
        "",
        "| 项目 | 值 |",
        "|---|---|",
        f"| vendor ELF SHA256 | `{_cell(vendor.get('library_sha256'))}` |",
        f"| Extension ELF SHA256 | `{_cell((receipt.get('artifact') or {}).get('sha256'))}` |",
        f"| SoC | `{_cell(runtime.get('soc'))}` |",
        f"| CANN | `{_cell(runtime.get('cann_version'))}` |",
        f"| torch | `{_cell(runtime.get('torch_version'))}` |",
        f"| torch_npu | `{_cell(runtime.get('torch_npu_version'))}` |",
        "",
        "## 精度汇总",
        "",
        f"- 合计：{accuracy.get('passed', counts.get('total', 0) - counts.get('fail', 0))}/"
        f"{accuracy.get('total', counts.get('total', 0))} 通过；"
        f"失败 {accuracy.get('failed', counts.get('fail', 0))}；"
        f"通过率 {_pct(accuracy.get('overall_pass_rate'))}。",
        f"- 精度标准：`{_cell(verdict.get('standard'))}`。",
        "",
        "| dtype | 总数 | 通过 | 失败 | uncertain | 通过率 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in accuracy.get("by_dtype") or []:
        lines.append(
            f"| `{_cell(row.get('dtype'))}` | {row.get('count', 0)} | "
            f"{row.get('passed', 0)} | {row.get('failed', 0)} | "
            f"{row.get('uncertain', 0)} | {_pct(row.get('pass_rate'))} |")

    failed = [
        row for row in (verdict.get("per_case") or [])
        if row.get("精度") != "pass"
    ]
    lines += ["", "## 精度失败明细", ""]
    if failed:
        lines += [
            f"共 **{len(failed)}** 条，逐项判据和复现入口见 "
            "[精度失败明细.md](精度失败明细.md)。",
            "",
            "快速复核：`./repro/audit_case.sh 1`。",
        ]
    else:
        lines.append("无精度失败。")

    ps = perf.get("summary") or {}
    lines += [
        "",
        "## 性能汇总",
        "",
        f"- 状态：`{_cell(ps.get('status'))}`。",
        f"- 计划 case：{ps.get('planned_cases', ps.get('perf_cases', 0))}；"
        f"实际采集：{ps.get('perf_cases', 0)}；有效评分：{ps.get('cases_scored', 0)}；"
        f"达标：{ps.get('达标', 0)}。",
        "",
        "| shape 类别 | 计划 | 实采 | 有效评分 | 达标 | NPU us | baseline us | speedup |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in perf.get("by_shape_class") or []:
        lines.append(
            f"| `{_cell(row.get('class'))}` | {row.get('planned_cases', 0)} | "
            f"{row.get('cases', 0)} | {row.get('cases_scored', 0)} | "
            f"{row.get('达标', 0)} | {_cell(row.get('npu_us'))} | "
            f"{_cell(row.get('baseline_us'))} | {_cell(row.get('speedup'))} |")
    if ps.get("status") == "skipped_precision_gate":
        lines += ["", "> 精度门未通过，性能未执行；本报告不提供虚构加速比。"]
    perf_non_passing = perf.get("non_passing_cases") or []
    if perf_non_passing:
        lines += [
            "",
            f"性能未通过共 **{len(perf_non_passing)}** 条，逐项状态与原始原因见 "
            "[性能失败明细.md](性能失败明细.md)。",
        ]
    elif ps.get("perf_cases", 0):
        lines += ["", "无性能未通过 case。"]

    gaps = _gap_items(caseset.get("task_pr_gaps"))
    lines += ["", "## 任务书与 PR 差额", ""]
    if gaps:
        for gap in gaps:
            lines.append(_gap_line(gap))
    else:
        lines.append("- 无已记录差额。")

    lines += [
        "",
        "## 证据与人工复核入口",
        "",
        "- `acceptance.json`：最终确定性裁决。",
        "- `verdict.json`：逐 case 精度裁决与 dtype 汇总。",
        "- `精度失败明细.md`：存在精度失败时生成的逐项复现索引。",
        "- `evidence.json`：逐 case 实测 metrics 和构建/加载收据。",
        "- `perf_report.json`：性能计划、采集和大小 shape 汇总。",
        "- `性能失败明细.md`：存在性能未通过 case 时生成的逐项状态索引。",
        "- `caseset.json`：完整用例契约。",
        "- `repro/index.tsv`：全部 case 与启动脚本索引。",
        "- `repro/failed.tsv`：带编号的失败 case 清单。",
        "- `repro/review.sh`：审核员 list/show/run 快捷入口。",
        "- `repro/audit_case.sh`：审核员单 case 直接复现主入口。",
        "- `repro/show_case.sh`：查看具体用例内容。",
        "- `repro/run_case.sh`：重放指定用例。",
        "",
    ]
    return "\n".join(lines)


def write_report(report_root, filename="验收报告.md", source_facts_path=None):
    report_root = os.path.realpath(report_root)
    text = render(report_root, source_facts_path=source_facts_path)
    path = os.path.join(report_root, filename)
    _atomic_write(path, text)

    verdict = _load(report_root, "verdict.json")
    failed = [
        row for row in (verdict.get("per_case") or [])
        if row.get("精度") != "pass"
    ]
    precision_path = os.path.join(report_root, "精度失败明细.md")
    if failed:
        _atomic_write(precision_path, _precision_failure_detail(failed))
    elif os.path.exists(precision_path):
        os.unlink(precision_path)

    perf = _load(report_root, "perf_report.json")
    perf_non_passing = perf.get("non_passing_cases") or []
    performance_path = os.path.join(report_root, "性能失败明细.md")
    if perf_non_passing:
        _atomic_write(
            performance_path,
            _performance_failure_detail(
                perf_non_passing, _load(report_root, "caseset.json")))
    elif os.path.exists(performance_path):
        os.unlink(performance_path)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description="从确定性验收 JSON 渲染中文 Markdown 报告")
    parser.add_argument("report_root")
    parser.add_argument("--filename", default="验收报告.md")
    # 与 `validate_acceptance_state --source-facts` 同名同义：同一份对照物，
    # 门和报告必须能被指到同一个文件上，否则「门校过」与「报告写的」可以是两份东西。
    parser.add_argument("--source-facts", default=None, metavar="PATH",
                        help="显式指定 source_facts.json；不给则在报告目录与其 work/ 下自动发现")
    args = parser.parse_args(argv)
    print(write_report(args.report_root, args.filename, args.source_facts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
