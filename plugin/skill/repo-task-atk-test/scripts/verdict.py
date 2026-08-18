"""校验验收证据并生成精度裁决。

输入：接口事实（S1 派生一次的 interface.json）、coverage 和 accuracy results。
输出：verdict.json。
退出码：0 完成；2 拒绝裁决。

以前这些输入要先汇总进一份 manifest：manifest 43 个键里绝大多数是转抄
（env 来自 env.json、policy 来自政策文件、覆盖数字来自 coverage 报告），
真正独有的只有产物摘要。而 coverage.json 和 accuracy results 各自在
生成时就已经把用到的 case 文件摘要记了一遍——两边一致就证明二者说的是
同一份用例集，不需要再引入第三份转抄记录当裁判。

manifest 的 4 个接口参数（interface-mode/execution-backend/baseline-api/
mode-source）已改由 derive_interface.py 在 S1 一次性派生，见 interface.json；
mode A/B/C 已删除——本流程结构上只可能是「组合由枚举表钉死」，覆盖保证
由 check_coverage.py 的必测集命中率证明，不由自我声明的字母证明。
"""

import argparse
import json
import os
import sys

from _case_utils import load_json
from _policy import PolicyError, load_policy
import _stage_card


class GateFailure(Exception):
    """裁决门禁失败。"""


def check_judged_outputs(judged_outputs, excluded_outputs_reason):
    """限制判定输出时要求排除依据。"""
    if judged_outputs and not excluded_outputs_reason:
        raise GateFailure(
            f"只判定 {judged_outputs}，但没有传 --excluded-outputs-reason。\n"
            "  → 写明被排除的输出为什么不适合作判据（例如基线接口未规定并列时的下标选择），"
            "报告中要一并披露。"
        )


def check_interface(interface, results, policy):
    """接口事实来自 S1 派生，这里只核它与真机跑测对不对得上。

    以前这些字段是 agent 在 S3 手工重新声明的，于是「声明填错」和
    「真的跑错后端」两类问题混在一起。现在声明侧是推导的，对不上
    就只可能是真跑错了。
    """
    mode = interface.get("interface_mode")
    interface_modes = policy["interface_modes"]
    if mode not in interface_modes:
        raise GateFailure(
            f"[INTERFACE_MODE] interface_mode = {mode!r} 不认识，"
            f"可选：{'/'.join(sorted(interface_modes))}。"
        )
    if not interface_modes[mode]["acceptance_enabled"]:
        raise GateFailure(f"[INTERFACE_DISABLED] 接口模式 {mode} 尚未开放验收。")
    if not interface.get("baseline_api"):
        raise GateFailure("[BASELINE_API] interface.json 缺少 baseline_api。")
    if not interface.get("mode_source"):
        raise GateFailure("[MODE_SOURCE] interface.json 缺少接口模式依据。")

    actual = results.get("backends") or []
    if not actual:
        raise GateFailure("[INTERFACE_EVIDENCE] 报告中没有可识别的实际后端。")
    execution_backend = interface.get("execution_backend")
    if execution_backend not in actual:
        raise GateFailure(
            f"[INTERFACE_BACKEND] 本轮应在 {execution_backend} 上执行，报告实测 {actual}。\n"
            "  → 接口后端由 S1 派生，对不上说明跑测命令的 -b 用错了后端，不是声明写错。"
        )
    baseline_backend = interface.get("baseline_backend")
    if baseline_backend and baseline_backend not in actual:
        raise GateFailure(
            f"[BASELINE_BACKEND] 缺少基线节点 {baseline_backend!r} 的结果，无从比对。"
        )


def conclusion_kind(interface):
    """这一轮能下哪一种结论。

    真值来自 CANN 内置实现时只能说「与内置逐位一致」，不能说「精度达标」——
    内置本身没有被这轮验收检验过，它只是对照物。两种结论的证据强度不同，
    措辞混用会把回归比对的结果读成精度结论。
    """
    if interface.get("baseline_kind") == "cann_builtin":
        return "regression_vs_builtin"
    return "accuracy_vs_framework"


def check_builtin_comparator(interface, results):
    """内置真值这条路，真正送进 ATK 的比较器必须是 equal。

    S2 的覆盖门禁管的是 must_cover 里的 comparator，而 ATK 实际用的是 YAML
    的 standard.acc——两者之间没有任何绑定。于是可以拿 mixed_tolerance_bm
    跑完全程：浮点差异在容差内全判过，结论却盖上「与内置逐位一致」。
    那是一份内容为假的验收结论，从报告上还看不出来。

    这里判的是从真机报告逐用例读出来的那个值，不是声明。
    """
    if interface.get("baseline_kind") != "cann_builtin":
        return
    summary = results.get("standard_acc_summary")
    acc = summary.get("value") if isinstance(summary, dict) else None
    if acc != "equal":
        raise GateFailure(
            f"[BUILTIN_COMPARATOR] 真值来自 CANN 内置实现，但这一轮实际跑的"
            f"比较器是 {acc!r}。\n"
            "  → 这条路比的是「改动有没有改变输出」，任何一位不同都要抓出来，"
            "只能用 equal。\n"
            "     容差比较器会把差异吞掉，而结论仍会写成「与内置逐位一致」——"
            "那是假的。\n"
            "     改 YAML 的 standard.acc 后重跑，见 "
            "references/builtin-baseline.md#比较器。")


def check_builtin_evidence(interface, provenance, golden_source=None):
    """真值来自内置实现时，取证不齐不许裁决。

    这条路的报告全绿有两种可能：真的逐位一致，或者比对压根没接上。
    区分它们的唯一手段是反证实验（故意改坏一条真值，那条必须变 Fail），
    所以没做过、或做了没通过，都不出结论。
    """
    if interface.get("baseline_kind") != "cann_builtin":
        return
    if not provenance:
        raise GateFailure(
            "[BUILTIN_GOLDEN] 缺 evidence/golden_provenance.json，真值来历不可核验。\n"
            "  → 跑 capture_reference.py 取证，见 references/builtin-baseline.md。")
    if not golden_source:
        raise GateFailure(
            "[BUILTIN_SOURCE] 缺 evidence/golden_source.json，"
            "没有证据表明真值来历门禁跑过。\n"
            "  → 跑 check_golden_source.py：它是唯一核「第二轮到底加载了哪份库」"
            "的地方。\n"
            "     漏掉它，第二轮忘 source env.sh 而 ATK 按同名搜到内置库时，"
            "两轮跑的是同一份实现，\n"
            "     两份指纹各自诚实、反证实验照过，报告 100% 通过而什么都没验。")
    if golden_source.get("verdict") != "ok":
        raise GateFailure(
            f"[BUILTIN_SOURCE] 真值来历门禁的结论是 "
            f"{golden_source.get('verdict')!r}，不是 ok。\n"
            "  → 先让 check_golden_source.py 过，再回来裁决。")
    counter = provenance.get("counter_experiment")
    if not counter:
        raise GateFailure(
            "[BUILTIN_GOLDEN] 没做反证实验，比对拓扑未经验证。\n"
            "  → 全绿既可能是真的一致，也可能是 load 节点没生效。\n"
            "     跑 capture_reference.py --tamper，见 builtin-baseline.md#反证实验。")
    if not counter.get("detected"):
        raise GateFailure(
            f"[BUILTIN_GOLDEN] 反证实验没通过：改坏了用例 {counter.get('case_id')} "
            "的真值，它却没变 Fail。\n"
            "  → 比对拓扑没生效，这一轮的结论不成立。")


def check_case_set(coverage, results):
    """核对报告使用全量必测集，且用例号没有重复或串号。

    容量公式（dtype_numbers × max_dtype_count 是否覆盖得住必测集）不在这里查：
    plugin-authoring.md 的硬约束要求生成器覆写 `_get_case_numbers`，
    validate_cases.py 的 C2b 已经当场核验覆写生效——用例集因此结构上恒等于
    枚举表，容量公式不参与，查它就是在查一件已经被别的门禁保证了的事。
    """
    must_size = coverage.get("must_cover_total")
    if not must_size:
        raise GateFailure("coverage 缺 must_cover_total，无法判断本次是否跑了全量必测集。")

    executed_ids = [str(case.get("id")) for case in results.get("cases", [])]
    excluded_ids = [str(case.get("id")) for case in results.get("excluded_cases", [])]
    if len(executed_ids) != len(set(executed_ids)):
        raise GateFailure("[CASE_IDS] 精度结果存在重复用例号。")
    if len(excluded_ids) != len(set(excluded_ids)):
        raise GateFailure("[EXCLUDED_CASE_IDS] 无效用例记录存在重复用例号。")
    overlap = sorted(set(executed_ids) & set(excluded_ids))
    if overlap:
        raise GateFailure(f"[EXCLUDED_CASE_OVERLAP] 用例同时进入精度结果和剔除集：{overlap[:10]}。")

    accounted = len(executed_ids) + len(excluded_ids)
    if accounted < must_size:
        raise GateFailure(
            f"有效执行与已留痕剔除共 {accounted} 条，少于必测集 {must_size} 条。\n"
            "  → 检查是否误用了采样文件，或是否漏记了未执行用例。"
        )


def check_coverage(coverage):
    missing = coverage.get("missing", [])
    if missing:
        head = ", ".join(str(m) for m in missing[:5])
        more = f"（共 {len(missing)} 项）" if len(missing) > 5 else ""
        raise GateFailure(
            f"必测集存在未覆盖项：{head}{more}\n"
            "  → 回覆盖设计第三步补齐。覆盖不足时不下调要求，而是补用例。"
        )
    total, hit = coverage.get("must_cover_total"), coverage.get("must_cover_hit")
    if total and hit != total:
        raise GateFailure(f"必测集覆盖 {hit}/{total}，未达 100%，不能出结论。")


def check_artifact_integrity(coverage, results):
    """核对 coverage 与 results 说的是同一份用例集。

    两边各自在生成时独立记录了 case_file 的摘要，一致就证明二者一致，
    不需要引入第三份转抄记录（原 manifest.artifacts）当裁判。
    """
    if results.get("task") != "accuracy":
        raise GateFailure("[RESULT_TASK] 精度裁决只能消费 accuracy 报告。")

    case_digest = coverage.get("case_file_sha256")
    if not case_digest:
        raise GateFailure("[ARTIFACT_COVERAGE_CASE] coverage 缺少用例集摘要。")
    if results.get("case_file_sha256") != case_digest:
        raise GateFailure("[ARTIFACT_RESULTS] results 与 coverage 的用例集摘要不一致。")

    if not coverage.get("must_cover_sha256"):
        raise GateFailure("[ARTIFACT_COVERAGE_MUST] coverage 缺少必测集摘要。")

    if results.get("case_count") is not None and \
            len(results.get("cases", [])) + len(results.get("excluded_cases", [])) \
            != results.get("case_count"):
        raise GateFailure("[ARTIFACT_COUNT] results 自身的用例数量与其明细对不上。")


def check_standard_not_tampered(results, policy):
    """拒绝缺失、非标准或被放松的精度标准。"""
    summary = results.get("standard_acc_summary")
    if not isinstance(summary, dict):
        raise GateFailure("[ACCURACY_STANDARD] results 缺少精度标准汇总。")
    if not summary.get("consistent"):
        raise GateFailure("[ACCURACY_STANDARD] 精度标准缺失或在用例之间不一致。")
    if summary.get("present_cases") != summary.get("total_cases"):
        raise GateFailure("[ACCURACY_STANDARD_COUNT] 部分用例缺少精度标准。")

    acc = summary.get("value")
    accuracy_policy = policy["accuracy"]
    allowed = set(accuracy_policy["allowed_comparators"])
    if isinstance(acc, str):
        if acc not in allowed:
            raise GateFailure(
                f"用例集使用了非标准比较器 {acc!r}，需人工复核其判定口径后方可出结论。"
            )
        return
    if isinstance(acc, dict):
        if len(acc) != 1:
            raise GateFailure("[ACCURACY_COMPARATOR] 每条用例只能声明一个精度比较器。")
        for name, params in acc.items():
            if name not in allowed:
                raise GateFailure(f"用例集注册了自定义比较器 {name!r}，需人工复核。")
            keys = list(params or {})
            bad = [k for k in keys
                   if k.startswith(tuple(accuracy_policy["forbidden_override_prefixes"]))
                   or k in accuracy_policy["forbidden_override_keys"]]
            if bad:
                raise GateFailure(
                    f"用例集的 standard.acc 携带阈值覆盖键 {bad}，会把精度标准调松。\n"
                    "  → 移除这些键后重新执行，或在报告中作为『用例自带放松阈值』的"
                    "符合性缺陷记录。"
                )
        return
    raise GateFailure("[ACCURACY_COMPARATOR] 精度比较器必须是字符串或单项对象。")


def case_passed(case, judged):
    """按整体或指定输出读取判定。"""
    if not judged:
        return bool(case.get("passed"))
    outs = case.get("outputs") or {}
    if any(name not in outs for name in judged):
        return False
    return all(bool(outs[name]) for name in judged)


def check_judged_output_presence(results, judged):
    if not judged:
        return
    for case in results.get("cases", []):
        outputs = case.get("outputs") or {}
        missing = [name for name in judged if name not in outputs]
        if missing:
            raise GateFailure(
                f"[JUDGED_OUTPUT] 用例 {case.get('id')} 缺少指定输出 {missing}。"
            )


def summarize(results, judged=None, conclusion_causes=()):
    """统计客观通过率，并单列失败归因。"""
    stats = {}
    for case in results.get("cases", []):
        part = case.get("partition", "must")
        bucket = stats.setdefault(part, {
            "total": 0, "passed": 0, "non_finite_inputs": 0,
            "failed_ids": [], "developer_failed_ids": [], "recheck_ids": [],
        })
        bucket["total"] += 1
        if case.get("non_finite_input"):
            bucket["non_finite_inputs"] += 1
        if case_passed(case, judged):
            bucket["passed"] += 1
        else:
            case_id = case.get("id")
            bucket["failed_ids"].append(case_id)
            cause = case.get("cause")
            if cause in conclusion_causes:
                bucket["developer_failed_ids"].append(case_id)
            else:
                bucket["recheck_ids"].append(case_id)
    for bucket in stats.values():
        bucket["pass_rate"] = (
            bucket["passed"] / bucket["total"]
            if bucket["total"]
            else 0.0
        )
    return stats


def recheck_summary(results, conclusion_causes):
    """汇总非开发者归因的失败。"""
    groups = [g for g in results.get("failure_groups", [])
              if g.get("cause") not in conclusion_causes]
    return [{k: g[k] for k in ("cause", "attribution", "hint", "count", "ids", "features")
             if k in g} for g in groups]


def uncovered_by_recheck(results, stats):
    """列出待复测的必测用例。"""
    ids = set()
    for part, bucket in stats.items():
        if part == "must":
            ids.update(str(i) for i in bucket.get("recheck_ids", []))
    return sorted(ids)


def decide(stats, excluded_cases=()):
    """根据失败归因生成三档结论。"""
    must = stats.get("must", {"total": 0, "failed_ids": [],
                              "developer_failed_ids": [], "recheck_ids": []})
    extra = stats.get("extra", {"failed_ids": [], "developer_failed_ids": [],
                                "recheck_ids": []})

    if must["total"] == 0:
        return "不通过", "必测集没有产生任何有效判定，无法认定达标。"

    if must["developer_failed_ids"]:
        return "不通过", (
            f"必测集 {len(must['developer_failed_ids'])} 条未通过且初步归因为开发者"
            f"（用例号 {must['developer_failed_ids'][:10]}...），按 acc_pass=1 口径判定不达标。"
        )

    if extra.get("developer_failed_ids"):
        return "有条件通过", (
            f"必测集无归因开发者的失败；加严集 {len(extra['developer_failed_ids'])} 条未通过"
            f"（用例号 {extra['developer_failed_ids'][:10]}...）。这些输入仍在任务书声明的"
            "合法域内，缺陷成立，修复并回归后转为正式通过。"
        )

    recheck = must.get("recheck_ids", []) + extra.get("recheck_ids", [])
    if recheck:
        return "有条件通过", (
            f"无归因开发者的失败；另有 {len(recheck)} 条失败初步归因为环境 / 用例 / 连坐类"
            f"（用例号 {recheck[:10]}...），已列入待复测清单。"
            "这些条件本次未真正验到，由验收人员排查复测后转为正式通过。"
        )

    if excluded_cases:
        must_excluded = [case.get("id") for case in excluded_cases
                         if case.get("partition", "must") == "must"]
        return "有条件通过", (
            f"有效用例没有确认缺陷；另有 {len(excluded_cases)} 条无效用例被剔除。"
            f"必测覆盖缺口为 {must_excluded[:10]}。补齐有效数据后转为正式通过。"
        )

    return "通过", "必测集与加严集全部通过。"


def performance_status(conclusion, perf_results, baseline_source):
    """S4 的性能状态由数据推导，不由报告作者自己写。

    三态与触发条件出自 references/performance.md。verdict 以前不产出它，
    真机上 agent 翻遍量具后自己造了一份 conclusion/performance_status.json——
    一个硬门禁最终靠 agent 自证通过，这正是「判据机械化」要堵的口子。

    「无对比基线」不等于「不采集」：atk-cli.md 写明没有 NPU 基线时
    用 CPU 节点产出元数据，报告里的 device_perf(us) 就是待验收算子端的绝对耗时。
    所以精度通过却拿不出性能产物时拒绝裁决，而不是落一个空状态。
    """
    if conclusion not in ("通过", "有条件通过"):
        return {"status": "未执行(精度未通过)",
                "basis": f"精度裁决为「{conclusion}」，按 performance.md 不执行性能",
                "baseline_source": baseline_source}
    if not perf_results:
        raise GateFailure(
            "精度已通过，但没有性能产物，性能状态推导不出来。\n"
            "  → 没有对比基线也要跑一轮 performance_device：pyaclnn 用 CPU 节点\n"
            "    产出元数据，报告的 device_perf(us) 就是待验收算子端绝对耗时，供取用。\n"
            "  → parse_atk_report.py 省略 -c 产出 performance_results.json，\n"
            "    再用 --perf-results 传进来。")

    cases = perf_results.get("cases") or []
    measured = [case for case in cases if case.get("device_us")]
    if not measured:
        raise GateFailure(
            "性能产物里没有一条用例带 device_perf(us)，绝对耗时没有落盘。\n"
            "  → 确认这一轮跑的是 performance_device，且报告取自本轮日志。")
    failed = [case.get("id") for case in cases if case.get("passed") is False]
    record = {
        "status": "通过" if baseline_source else "未执行(无基线)",
        "basis": ("精度通过且性能已执行" if baseline_source
                  else "对比基线或绝对门槛均不可得；绝对耗时已采集落盘"),
        "baseline_source": baseline_source,
        "results_file": perf_results.get("source"),
        "measured_cases": len(measured),
        "failed_cases": failed,
    }
    return record


def main():
    parser = argparse.ArgumentParser(description="验收判定引擎")
    parser.add_argument("--interface", required=True,
                        help="derive_interface.py 产出的 evidence/interface.json")
    parser.add_argument("-c", "--coverage", required=True)
    parser.add_argument("-r", "--results", required=True)
    parser.add_argument("--op", required=True, help="算子名，写入结论与复现包")
    parser.add_argument("--env", help="probe_env.py 产出的 env.json，写入结论供复现引用")
    parser.add_argument("--judged-outputs", help="逗号分隔，只判定这些输出")
    parser.add_argument("--excluded-outputs-reason", help="排除其余输出的依据")
    parser.add_argument("--perf-results",
                        help="parse_atk_report.py 产出的 performance_results.json；"
                             "精度通过时必给，性能状态由它推导")
    parser.add_argument("--perf-baseline-source",
                        help="性能基线或绝对门槛的依据原文（任务书章节 / 用户确认）；"
                             "不给即判定为无基线")
    parser.add_argument("--golden-provenance",
                        default="evidence/golden_provenance.json",
                        help="capture_reference.py 的取证；"
                             "baseline_kind 为 cann_builtin 时必须齐备才裁决")
    parser.add_argument("--golden-source",
                        default="evidence/golden_source.json",
                        help="check_golden_source.py 的判定结论；"
                             "baseline_kind 为 cann_builtin 时必须存在且为 ok")
    parser.add_argument("-o", "--output", help="verdict JSON 落盘路径")
    args = parser.parse_args()
    _stage_card.announce(__file__)

    interface, coverage, results = (
        load_json(args.interface),
        load_json(args.coverage),
        load_json(args.results),
    )
    env = load_json(args.env) if args.env else {}
    judged = [name.strip() for name in args.judged_outputs.split(",")
             if name.strip()] if args.judged_outputs else None

    try:
        policy = load_policy()
    except PolicyError as error:
        print(error, file=sys.stderr)
        return 2

    try:
        check_artifact_integrity(coverage, results)
        check_interface(interface, results, policy)
        check_judged_outputs(judged, args.excluded_outputs_reason)
        check_case_set(coverage, results)
        check_coverage(coverage)
        check_standard_not_tampered(results, policy)
        check_judged_output_presence(results, judged)
        check_builtin_comparator(interface, results)
        check_builtin_evidence(
            interface,
            load_json(args.golden_provenance)
            if os.path.exists(args.golden_provenance) else None,
            load_json(args.golden_source)
            if os.path.exists(args.golden_source) else None)
    except GateFailure as exc:
        print(f"[拒绝出具结论] {exc}", file=sys.stderr)
        return 2

    conclusion_causes = set(policy["conclusion_causes"])
    stats = summarize(results, judged, conclusion_causes)
    excluded_cases = results.get("excluded_cases") or []
    conclusion, reason = decide(stats, excluded_cases)
    recheck = recheck_summary(results, conclusion_causes)

    try:
        performance = performance_status(
            conclusion,
            load_json(args.perf_results) if args.perf_results else None,
            args.perf_baseline_source)
    except GateFailure as exc:
        print(f"[拒绝出具结论] {exc}", file=sys.stderr)
        return 2

    verdict = {
        "op": args.op,
        "conclusion": conclusion,
        "conclusion_kind": conclusion_kind(interface),
        "reason": reason,
        "partitions": stats,
        "performance": performance,
        "pending_recheck": recheck,
        "must_cases_pending_recheck": uncovered_by_recheck(results, stats),
        "excluded_cases": excluded_cases,
        "must_coverage_gap_ids": [
            case.get("id")
            for case in excluded_cases
            if case.get("partition", "must") == "must"
        ],
        "interface": {
            "mode": interface.get("interface_mode"),
            "execution_backend": interface.get("execution_backend"),
            "candidate_symbol": interface.get("candidate_symbol"),
            "baseline_api": interface.get("baseline_api"),
            "source": interface.get("mode_source"),
            "backends_observed": results.get("backends"),
        },
        "judged_outputs": judged,
        "excluded_outputs_reason": args.excluded_outputs_reason,
        "artifact_sha256": {
            "case_file": results.get("case_file_sha256"),
            "must_cover_file": coverage.get("must_cover_sha256"),
        },
        "env": env,
    }

    text = json.dumps(verdict, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
