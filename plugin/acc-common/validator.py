"""Task 2 · validator — spec + caseset + evidence.json -> verdict.json（确定性裁决）。

ADR 0007：裁决只从这里出。ADR 0005：精度三层口径、放行只看 acceptance。职责：
1) 契约校验——evidence 与 caseset **一一对应**（无缺/多/重复），否则整体 fail（防空 evidence 假通过）。
2) 口径以 **spec 为权威**——由 `spec_standard` + case 的 compare_dtype 用 `precision_policy.threshold_for/
   tolerance_policy_id` **复算 canonical policy**，要求 spec-canonical / caseset.expected / evidence.precision
   **三处结构化 policy 全等**（standard + tolerance_policy_id + 结构化 policy + threshold digest 向后兼容）。
   仅比 caseset↔evidence 不够——两侧**同步放宽**即可绕过（finding #6），故锚回 spec。
   acceptance 层另做结构化校验（有 acceptance_policy → 三处一致 + acceptance_metrics 必填；无 →
   拒绝 evidence 私带额外 acceptance 口径，finding #7）。
3) 三层 pass 同出（canonical 字段名）：`catlass_compare_pass` / `standard_profile_pass` /
   `acceptance_precision_pass`；**放行只看 acceptance**；acceptance 过 & standard(平台底线) 不过
   → 该 case `risk=true`、overall=`passed_with_risk`（人工 CP）。ecosystem_mere_mare 单标杆不过
   → `uncertain`（NOT_SETTLED，不自动 fail）。**standard 或 acceptance 任一 uncertain → 至少 needs_review**
   （finding #9，不被 acceptance pass 吞掉）。
4) 按 case dims 只裁相关维度；性能维交 Task 3 perf_compare（此处 na）。
overall 优先级：`contract/fail > needs_review(uncertain) > passed_with_risk > pass`。
（注：`blocked` 由**门/编排层**裁定，validator **不产出** blocked——finding #11。）

judge_* 入口做 metric **schema 校验**（计数=非负整数、numel=正整数、MERE/MARE=有限非负浮点）：
非法/缺失/坏类型一律收敛到 fail（不进正常 pass、不抛异常崩溃，finding #8）。
顶层坏 JSON（cases/evidence 非列表、case 缺 id 等）→ 收敛 contract_problems + overall=fail，不下标崩溃（finding #10）。

**纯 stdlib**（judge 只做纯算术；误差分布复算在采集层 repo_adapter，本文件不 import numpy）。
"""
import json, math, sys
import precision_policy


# ------------------------------------------------------ metric schema 校验 ---
def _is_nonneg_int(x):
    return isinstance(x, int) and not isinstance(x, bool) and x >= 0


def _is_pos_int(x):
    return isinstance(x, int) and not isinstance(x, bool) and x > 0


def _is_finite_nonneg_num(x):
    return (isinstance(x, (int, float)) and not isinstance(x, bool)
            and math.isfinite(x) and x >= 0)


# ------------------------------------------------------------ 纯算术 judge ---
def judge_ascendoptest(policy, metrics):
    """AscendOpTest 默认：坏点占比门——`bad_count <= numel * error_rate` 才过。
    schema：bad_count 非负整数、numel 正整数、error_rate 有限非负；任一非法 → fail（finding #8）。"""
    if not isinstance(metrics, dict) or "bad_count" not in metrics or "numel" not in metrics:
        got = sorted(metrics) if isinstance(metrics, dict) else type(metrics).__name__
        return "fail", f"metrics 缺 bad_count/numel（{got}）"
    bad, n = metrics["bad_count"], metrics["numel"]
    if not _is_nonneg_int(bad):
        return "fail", f"bad_count 非法（须非负整数）: {bad!r}"
    if not _is_pos_int(n):
        return "fail", f"numel 非法（须正整数，防空输出假通过）: {n!r}"
    err = policy.get("error_rate")
    if not _is_finite_nonneg_num(err):
        return "fail", f"policy.error_rate 非法（须有限非负）: {err!r}"
    return ("pass" if bad <= n * err else "fail"), f"bad_count={bad} vs numel*error_rate={n * err}"


def judge_mere_mare(policy, metrics):
    """生态 MERE/MARE（proposed/NOT_SETTLED）：`MERE<Th 且 MARE<max_ratio*Th` 才过；
    不过 → **uncertain**（单标杆失败非终判，ATK 双标杆本轮 out-of-scope）。
    schema：mere/mare 有限非负、threshold 有限正、max_ratio 有限非负；非法 → fail（finding #8）。"""
    if not isinstance(metrics, dict) or "mere" not in metrics or "mare" not in metrics:
        got = sorted(metrics) if isinstance(metrics, dict) else type(metrics).__name__
        return "fail", f"metrics 缺 mere/mare（{got}）"
    mere, mare = metrics["mere"], metrics["mare"]
    if not _is_finite_nonneg_num(mere) or not _is_finite_nonneg_num(mare):
        return "fail", f"MERE/MARE 非法（须有限非负）: mere={mere!r} mare={mare!r}"
    th, ratio = policy.get("threshold"), policy.get("max_ratio")
    if not (_is_finite_nonneg_num(th) and th > 0) or not _is_finite_nonneg_num(ratio):
        return "fail", f"policy.threshold/max_ratio 非法: th={th!r} ratio={ratio!r}"
    ok = (mere < th) and (mare < ratio * th)
    why = f"MERE={mere}<{th} 且 MARE={mare}<{ratio * th}（NOT_SETTLED）"
    return ("pass" if ok else "uncertain"), why


def judge_exact(policy, metrics):
    """exact：`exact_mismatch <= max_mismatch(0)` 才过。
    schema：exact_mismatch 非负整数、numel 正整数、max_mismatch 非负整数；非法 → fail（finding #8）。"""
    if not isinstance(metrics, dict) or "exact_mismatch" not in metrics:
        got = sorted(metrics) if isinstance(metrics, dict) else type(metrics).__name__
        return "fail", f"metrics 缺 exact_mismatch（{got}）"
    mism = metrics["exact_mismatch"]
    if not _is_nonneg_int(mism):
        return "fail", f"exact_mismatch 非法（须非负整数）: {mism!r}"
    n = metrics.get("numel")
    if not _is_pos_int(n):
        return "fail", f"numel 非法（须正整数，防空输出假通过）: {n!r}"
    mm = policy.get("max_mismatch", 0)
    if not _is_nonneg_int(mm):
        return "fail", f"policy.max_mismatch 非法（须非负整数）: {mm!r}"
    return ("pass" if mism <= mm else "fail"), f"exact_mismatch={mism}"


_JUDGES = {precision_policy.ASCENDOPTEST_DEFAULT: judge_ascendoptest,
           precision_policy.ECOSYSTEM_MERE_MARE: judge_mere_mare,
           precision_policy.EXACT: judge_exact}


def _judge_by_policy(policy, metrics):
    kind = policy.get("kind") if isinstance(policy, dict) else None
    if kind == precision_policy.BEHAVIORAL:
        return "na", "行为型：无数值 golden，精度维度 na"
    j = _JUDGES.get(kind)
    if j is None:
        return "fail", f"未知/缺失 policy.kind={kind!r}"
    return j(policy, metrics)


# ------------------------------------------------ 三处一致（spec 权威 canonical）---
def _case_compare_dtype(exp):
    """取 case 的比对 dtype：优先 expected.compare_dtype，退回 tolerance_policy_id 的 `standard:dtype` 后缀。"""
    cd = exp.get("compare_dtype")
    if cd:
        return cd
    tpid = exp.get("tolerance_policy_id")
    if isinstance(tpid, str) and ":" in tpid:
        return tpid.split(":", 1)[1]
    return None


def _canonical(spec_standard, cdtype):
    """按 spec 权威 standard + case dtype 复算 canonical (policy, tpid)；不可复算 → 抛 ValueError。"""
    tpid = precision_policy.tolerance_policy_id(spec_standard, cdtype)
    pol = precision_policy.threshold_for(spec_standard, cdtype)
    return pol, tpid


def _precision_contract(eff_standard, exp, ev_prec):
    """口径三处一致（spec 权威 + T7 per-case 有效标准）——防 caseset+evidence 同步放宽（finding #6/#7）。返回 (ok, why)。

    以 **eff_standard**（据 spec + case dtype/compare 复算，int→EXACT 不可绕过）+ case dtype 复算 canonical policy，
    要求 canonical / caseset / evidence 三处 standard + tolerance_policy_id + 结构化 policy + threshold digest **全等**；
    acceptance 另校验。
    """
    if not isinstance(ev_prec, dict):
        return False, "evidence 缺 precision（非对象）"
    if not isinstance(exp.get("policy"), dict):
        return False, "caseset.expected 缺结构化 policy"
    if not isinstance(ev_prec.get("policy"), dict):
        return False, "evidence.precision 缺结构化 policy"
    cdtype = _case_compare_dtype(exp)
    try:
        canon_pol, canon_tpid = _canonical(eff_standard, cdtype)
    except (ValueError, KeyError) as ex:
        return False, f"无法据 spec 复算 canonical（standard={eff_standard} dtype={cdtype}）：{ex}"
    canon_digest = precision_policy.threshold_digest(canon_pol)
    for side, obj in (("caseset", exp), ("evidence", ev_prec)):
        if obj.get("standard") != eff_standard:
            return False, f"{side}.standard={obj.get('standard')} ≠ 有效标准 {eff_standard}"
        if obj.get("tolerance_policy_id") != canon_tpid:
            return False, (f"{side}.tolerance_policy_id={obj.get('tolerance_policy_id')} "
                           f"≠ spec-canonical {canon_tpid}")
        if obj.get("policy") != canon_pol:
            return False, f"{side}.policy 与 spec-canonical 不一致（放宽/漏字段/多字段）"
        if obj.get("threshold") != canon_digest:
            return False, (f"{side}.threshold(digest)={obj.get('threshold')} "
                           f"≠ spec-canonical {canon_digest}")
    # acceptance 层结构化校验（finding #7）
    exp_acc = exp.get("acceptance_policy")
    ev_acc = ev_prec.get("acceptance_policy")
    if exp_acc is not None:
        if ev_acc != exp_acc:
            return False, "acceptance_policy 三处不一致（evidence ≠ caseset）"
        if exp.get("acceptance_tolerance_policy_id") != ev_prec.get("acceptance_tolerance_policy_id"):
            return False, "acceptance_tolerance_policy_id 不一致"
        if not isinstance(ev_prec.get("acceptance_metrics"), dict):
            return False, "acceptance_policy 存在但 evidence 缺 acceptance_metrics（必填）"
    else:
        for k in ("acceptance_policy", "acceptance_tolerance_policy_id", "acceptance_metrics"):
            if ev_prec.get(k) is not None:
                return False, f"caseset 无 acceptance 但 evidence 私带 {k}（额外口径，拒绝）"
    return True, ""


# ------------------------------------------------------- 空 per_case 的骨架 ---
def _empty_row(cid):
    return {"case_id": cid, "功能": "na", "精度": "na", "性能": "na",
            "catlass_compare_pass": "na", "standard_profile_pass": "na",
            "acceptance_precision_pass": "na", "risk": False,
            "判据": "", "evidence_ref": cid}


def _verdict(op, vm, spec_standard, problems, per):
    fails = [p for p in per if p["功能"] == "fail" or p["精度"] == "fail"]
    # finding #9：standard 或 acceptance 任一 uncertain 都要计入 needs_review（不被 acceptance pass 吞）。
    unc_ids, seen = [], set()
    for p in per:
        if (p["精度"] == "uncertain" or p["standard_profile_pass"] == "uncertain"
                or p["acceptance_precision_pass"] == "uncertain"):
            if p["case_id"] not in seen:
                seen.add(p["case_id"]); unc_ids.append(p["case_id"])
    risks = [p["case_id"] for p in per if p.get("risk")]
    catlass_na = [p["case_id"] for p in per if p["catlass_compare_pass"] == "na"]
    if problems or fails:
        overall = "fail"
    elif unc_ids:
        overall = "needs_review"
    elif risks:
        overall = "passed_with_risk"
    else:
        overall = "pass"
    return {"op": op, "verify_mode": vm, "standard": spec_standard,
            "contract_problems": problems, "per_case": per,
            "catlass_compare_na": catlass_na,
            "overall": {"verdict": overall, "uncertain": unc_ids, "risk": risks,
                        "requires_human_cp": overall == "passed_with_risk",
                        "counts": {"total": len(per), "fail": len(fails),
                                   "uncertain": len(unc_ids), "risk": len(risks),
                                   "contract_problems": len(problems)}}}


# --------------------------------------------------------------------- 裁决 ---
def validate(spec, caseset, evidence):
    # finding #10：顶层最小 schema 校验——坏 JSON/类型错收敛 contract_problems + overall=fail，绝不下标崩溃。
    problems = []
    if not isinstance(spec, dict):
        return _verdict("?", None, None, ["spec 非对象（无法裁决）"], [])
    vm = spec.get("verify_mode")
    op = spec.get("op", "?")
    cases = caseset.get("cases") if isinstance(caseset, dict) else None
    ev_list = evidence.get("evidence") if isinstance(evidence, dict) else None
    if not isinstance(cases, list) or not cases:
        problems.append("caseset.cases 缺失/非列表/空（无用例可裁）")
    if not isinstance(ev_list, list):
        problems.append("evidence.evidence 缺失或非列表")
    if problems:                                     # 结构性致命 → 直接出 fail verdict（不崩）
        return _verdict(op, vm, None, problems, [])

    try:
        spec_standard = precision_policy.select_standard(spec)
    except ValueError as ex:
        spec_standard = None
        problems.append(f"spec 精度标准无法解析：{ex}")

    if vm not in ("exact", "numerical", "behavioral"):
        problems.append(f"spec.verify_mode={vm!r} 非法（仅 exact/numerical/behavioral）")

    # case_ids / ev_ids：逐项抗坏（缺 id / 非对象 → contract 问题，不崩）
    case_ids = []
    for i, c in enumerate(cases):
        if not isinstance(c, dict) or not c.get("id"):
            problems.append(f"caseset.cases[{i}] 非对象或缺 id")
            continue
        case_ids.append(c["id"])
    ev_by_id, ev_ids = {}, []
    for i, e in enumerate(ev_list):
        if not isinstance(e, dict) or not e.get("case_id"):
            problems.append(f"evidence[{i}] 非对象或缺 case_id")
            continue
        ev_ids.append(e["case_id"])
        ev_by_id[e["case_id"]] = e

    if len(case_ids) != len(set(case_ids)):
        problems.append("caseset 有重复 case_id")
    if len(ev_ids) != len(set(ev_ids)):
        problems.append("evidence 有重复 case_id")
    missing = set(case_ids) - set(ev_ids)
    extra = set(ev_ids) - set(case_ids)
    if missing:
        problems.append(f"evidence 缺 case: {sorted(missing)}")
    if extra:
        problems.append(f"evidence 有多余 case: {sorted(extra)}")

    per = []
    for c in cases:
        if not isinstance(c, dict) or not c.get("id"):
            continue                                 # 已在上文记 contract 问题
        cid = c["id"]
        dims = c.get("dims") or []
        exp = c.get("expected") if isinstance(c.get("expected"), dict) else {}
        row = _empty_row(cid)
        e = ev_by_id.get(cid)
        if e is None:
            row.update(功能="fail", 判据="evidence 缺此 case")
            per.append(row); continue
        ev_prec = e.get("precision") or {}
        # 2) 口径一致性（spec 权威）：verify_mode + 标准 + policy 三处一致
        if exp.get("verify_mode") != vm:
            row.update(功能="fail", 判据=f"case.verify_mode={exp.get('verify_mode')} ≠ spec {vm}")
            per.append(row); continue
        if spec_standard is None:
            row.update(功能="fail", 判据="spec 精度标准不可解析，无法据 spec 校验口径")
            per.append(row); continue
        # T7：per-case **有效标准**（int→EXACT 不可绕过；bf16 靠 compare=exact_equal 收紧；余=spec 标准）。
        # 据 cdtype+compare 复算，故 caseset 同步误标别的口径也过不了下方三处一致门（防放宽仍成立）。
        cdtype = _case_compare_dtype(exp)
        eff_std = precision_policy.effective_standard(spec_standard, cdtype, exp.get("compare"))
        if exp.get("standard") != eff_std:
            row.update(功能="fail",
                       判据=f"standard 与有效标准不一致 eff={eff_std}/case={exp.get('standard')}"
                            f"（spec={spec_standard} dtype={cdtype} compare={exp.get('compare')}）")
            per.append(row); continue
        ok, why = _precision_contract(eff_std, exp, ev_prec)
        if not ok:
            row.update(功能="fail", 判据=f"精度口径{why}")
            per.append(row); continue
        # 3) 按 dims 裁维度
        whys = []
        if "功能" in dims:
            row["功能"] = "pass" if e.get("status") == "ok" else "fail"
        if "精度" in dims:
            policy = exp["policy"]
            metrics = ev_prec.get("metrics")
            if not isinstance(metrics, dict):
                row.update(精度="fail", 判据="evidence 缺 precision.metrics（误差分布未复算）")
                per.append(row); continue
            std_state, std_why = _judge_by_policy(policy, metrics)
            acc_policy = exp.get("acceptance_policy")
            if acc_policy:
                acc_metrics = ev_prec.get("acceptance_metrics", metrics)
                acc_state, acc_why = _judge_by_policy(acc_policy, acc_metrics)
            else:                                   # 无 acceptance_policy → 继承 standard
                acc_state, acc_why = std_state, std_why
            row["catlass_compare_pass"] = "na"      # mock/new_example：仓内无 catlass smoke
            row["standard_profile_pass"] = std_state
            row["acceptance_precision_pass"] = acc_state
            row["精度"] = acc_state                 # 放行只看 acceptance
            row["risk"] = (acc_state == "pass" and std_state == "fail")
            whys.append(f"acceptance:{acc_why}" + (f" | standard:{std_why}" if acc_policy else ""))
            if row["risk"]:
                whys.append("⚠risk：acceptance 过但平台底线(standard) 不过 → 需人工 CP")
        row["判据"] = "；".join(whys) if whys else f"dims={dims}（性能交 perf_compare）"
        per.append(row)

    return _verdict(op, vm, spec_standard, problems, per)


def main(argv):
    out_path = argv[3]
    try:
        spec = json.load(open(argv[0], encoding="utf-8"))
        caseset = json.load(open(argv[1], encoding="utf-8"))
        evidence = json.load(open(argv[2], encoding="utf-8"))
        verdict = validate(spec, caseset, evidence)
    except Exception as ex:                          # finding #10：任何异常也要出 verdict.json（overall=fail）
        verdict = _verdict("?", None, None, [f"validator 载入/裁决异常：{type(ex).__name__}: {ex}"], [])
    json.dump(verdict, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    o = verdict["overall"]
    print(f"[validator] overall={o['verdict']} {o['counts']} -> {out_path}")
    return 0 if o["verdict"] != "fail" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
