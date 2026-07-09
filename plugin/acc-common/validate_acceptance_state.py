"""机器可校验验收门（P0）——三级门，只认结构化机读证据，不认 md/LOG 文字。

把「验证-才-信」从纪律变成**代码硬门**：由 run_workflow / op-acceptance 在出裁决前强制跑，
任一 stage `FAILED` → 不推进、不生成裁决。核心防「跑子集报 100%」：evidence/perf 必须覆盖
caseset 全部用例、id 一一对应、每 (dtype,shape) 计数不缺。

**门是完整性门**：只保证证据可信+完整（不重判精度/性能 pass-fail，那是 validator/perf_compare 的活）。
**抗坏输入**：坏/缺字段的产物 → 累计成 error、判 FAILED，绝不崩溃、绝不静默放过。

用法: python3 validate_acceptance_state.py --stage task1|task2|task3 --dir <reports 产物目录>
只读、stdlib、零硬编码。打印累积 error（非 fail-fast）+ 末行 `STATUS: PASSED|FAILED`，exit 0/1。
"""
import argparse, hashlib, json, math, os, sys
from collections import Counter

# T6/T8 扩枚举：exception=小shape例外(合法放行需交叉校验)；
# blocked_wait_gpu_benchmark=缺外部 GPU 标杆正规挂起；blocked_incomparable_timing_scope=双边口径不可比。
_PERF_STATUS = {"ok", "no_perf_cases", "blocked", "fail",
                "exception", "blocked_wait_gpu_benchmark", "blocked_incomparable_timing_scope"}
_VERDICT_ENUM = {"pass", "fail", "needs_review", "passed_with_risk"}  # validator overall.verdict 合法枚举


def _is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _load(d, name):
    p = os.path.join(d, name)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return "__BAD__"


def _case_key(c, errs=None):
    """用例的 (dtype, shape) 键——覆盖计数用；抗坏字段。
    shape 必须是 list/tuple——shape:null 时 `list(None)` 会 TypeError 崩（finding #15），
    此处显式校验：非法记 error 并退回占位 '?'。"""
    ins = c.get("inputs") if isinstance(c, dict) else None
    if not isinstance(ins, list) or not ins or not isinstance(ins[0], dict):
        return "?"
    shape = ins[0].get("shape", [])
    if not isinstance(shape, (list, tuple)):
        if errs is not None:
            errs.append(f"{c.get('id', '?')}: inputs[0].shape 非 list/tuple（{shape!r}）")
        return "?"
    return f"{ins[0].get('dtype', '?')}{list(shape)}"


def _ids_from_evidence(ev_list, errs):
    ids = []
    for i, e in enumerate(ev_list or []):
        if not isinstance(e, dict) or not e.get("case_id"):
            errs.append(f"evidence[{i}] 缺 case_id（证据不完整）")
            continue
        ids.append(e["case_id"])
    if len(ids) != len(set(ids)):
        errs.append(f"evidence 有重复 case_id: {[k for k, v in Counter(ids).items() if v > 1]}")
    return ids


def gate_task1(d, errs):
    """用例集自洽 + （有 evidence 时）id 一一对应，专防跑子集。"""
    cs = _load(d, "caseset.json")
    if not isinstance(cs, dict):
        errs.append("缺/坏 caseset.json（Task1 未产用例）")
        return
    cases = cs.get("cases")
    if not isinstance(cases, list) or not cases:
        errs.append("caseset 无用例或 cases 非列表")
        return
    ids = []
    for i, c in enumerate(cases):
        if not isinstance(c, dict):
            errs.append(f"case[{i}] 非对象")
            continue
        cid = c.get("id")
        if not cid:
            errs.append(f"case[{i}] 缺 id")
            continue
        ids.append(cid)
        if not c.get("inputs"):
            errs.append(f"{cid}: 无 inputs")
        exp = c.get("expected") if isinstance(c.get("expected"), dict) else {}
        if not exp.get("golden_path"):
            errs.append(f"{cid}: 无 golden_path")
        if exp.get("threshold") is None:
            errs.append(f"{cid}: 缺 expected.threshold")
        # T5 结构化口径必填（缺 → 无法做三处一致的防放宽门）
        if not exp.get("standard"):
            errs.append(f"{cid}: 缺 expected.standard（精度标准未声明）")
        if not exp.get("tolerance_policy_id"):
            errs.append(f"{cid}: 缺 expected.tolerance_policy_id")
        if not isinstance(exp.get("policy"), dict):
            errs.append(f"{cid}: 缺结构化 expected.policy")
        if not c.get("dims"):
            errs.append(f"{cid}: 无 dims（功能/精度/性能维度）")
    dup = [k for k, v in Counter(ids).items() if v > 1]
    if dup:
        errs.append(f"caseset 有重复 case_id: {dup}")
    cov = Counter(_case_key(c, errs) for c in cases if isinstance(c, dict))
    print(f"  用例数={len(cases)} | (dtype,shape) 覆盖={dict(cov)}")
    ev = _load(d, "evidence.json")  # 有 evidence（已跑）→ id 必须一一对应、不许子集
    if isinstance(ev, dict):
        eids = _ids_from_evidence(ev.get("evidence"), errs)
        miss, extra = set(ids) - set(eids), set(eids) - set(ids)
        if miss:
            errs.append(f"⚠跑子集：evidence 缺 {sorted(miss)}（caseset 有、实跑无）")
        if extra:
            errs.append(f"evidence 多出 {sorted(extra)}（caseset 无）")
    elif ev == "__BAD__":
        errs.append("evidence.json 解析失败（坏 JSON）")


def gate_task2(d, errs):
    """精度证据**完整性**门：全覆盖(防子集) + precision 必填 + 阈值三处一致(防放宽) + 无契约问题。
    注：精度 pass/fail 本身由 validator 判、**此门不重判**——合法的精度 fail 不该被门当 BLOCKED。"""
    cs, ev, vd = _load(d, "caseset.json"), _load(d, "evidence.json"), _load(d, "verdict.json")
    if not (isinstance(cs, dict) and isinstance(ev, dict) and isinstance(vd, dict)):
        errs.append("缺/坏 caseset/evidence/verdict.json（Task2 未跑全）")
        return
    # finding #13：cases/evidence 非列表或空 → 直接 FAILED（不静默兜成空列表放过）。
    cases = cs.get("cases")
    if not isinstance(cases, list) or not cases:
        errs.append("caseset.cases 缺失/非列表/空（Task2 无用例可核）")
        return
    ev_list = ev.get("evidence")
    if not isinstance(ev_list, list) or not ev_list:
        errs.append("evidence.evidence 缺失/非列表/空（Task2 无证据可核）")
        return
    # ID 用 Counter 校验（重复不被 set 折叠）。
    cid_list = [c["id"] for c in cases if isinstance(c, dict) and c.get("id")]
    cid_dups = [k for k, v in Counter(cid_list).items() if v > 1]
    if cid_dups:
        errs.append(f"caseset 有重复 case_id: {cid_dups}")
    cids = set(cid_list)
    eids = set(_ids_from_evidence(ev_list, errs))  # 内部已用 Counter 报 evidence 重复
    if cids != eids:
        errs.append(f"⚠跑子集：caseset id != evidence id（缺 {sorted(cids - eids)} 多 {sorted(eids - cids)}）")
    # verdict 自身完整性 + 契约问题（finding #14：verdict 枚举 + counts 必填整数）
    ov = vd.get("overall")
    if not isinstance(ov, dict) or "verdict" not in ov:
        errs.append("verdict.overall.verdict 缺失（validator 产物不完整）")
        ov = {}
    elif ov.get("verdict") not in _VERDICT_ENUM:
        errs.append(f"verdict.overall.verdict={ov.get('verdict')!r} 非法（须属 {sorted(_VERDICT_ENUM)}）")
    counts = ov.get("counts") if isinstance(ov.get("counts"), dict) else None
    if counts is None:
        errs.append("verdict.overall.counts 缺失")
    else:
        for k in ("contract_problems", "fail", "uncertain"):
            if not _is_int(counts.get(k)):
                errs.append(f"verdict.overall.counts.{k} 缺失或非整数: {counts.get(k)!r}")
        if _is_int(counts.get("contract_problems")) and counts["contract_problems"]:
            errs.append(f"契约问题 {counts['contract_problems']} 条（validator 标 evidence↔caseset 契约破损）")
    # precision 必填 + **口径三处一致（policy 化）**——防 adapter 偷偷放宽阈值/漏采精度假通过。
    # T5：由「标量 threshold 相等」升级为「tolerance_policy_id + 结构化 policy 一致」（保留 threshold digest）。
    exp_by_id = {c["id"]: (c.get("expected") or {})
                 for c in cases if isinstance(c, dict) and c.get("id")}
    for e in ev_list:
        if not isinstance(e, dict) or not e.get("case_id"):
            continue
        cid = e["case_id"]
        prec = e.get("precision")
        if not isinstance(prec, dict):
            errs.append(f"{cid}: evidence 缺 precision（证据不完整、不可信）")
            continue
        if prec.get("threshold") is None:
            errs.append(f"{cid}: evidence 缺 precision.threshold（证据不完整、不可信）")
        if not prec.get("tolerance_policy_id"):
            errs.append(f"{cid}: evidence 缺 precision.tolerance_policy_id（口径不可追溯）")
        if not isinstance(prec.get("policy"), dict):
            errs.append(f"{cid}: evidence 缺结构化 precision.policy")
        exp = exp_by_id.get(cid)
        if exp is None:
            continue  # caseset 无此 case（多余）已在上文报
        # finding #12：三处一致改为「任一侧缺字段即 error」（旧「双非 None 才比」会放过缺字段假通过）。
        # caseset expected 侧四字段必填且类型正确 + 与 evidence 全等（防放宽）。
        _types = {"threshold": (int, float), "tolerance_policy_id": str, "standard": str, "policy": dict}
        for key in ("threshold", "tolerance_policy_id", "standard", "policy"):
            ce, ee = exp.get(key), prec.get(key)
            if ce is None:
                errs.append(f"{cid}: caseset expected 缺 {key}（无法做三处一致、防放宽门失效）")
                continue
            if not isinstance(ce, _types[key]) or isinstance(ce, bool):
                errs.append(f"{cid}: caseset expected.{key} 类型错（{type(ce).__name__}，须 {_types[key]}）")
                continue
            if ee is None:
                errs.append(f"{cid}: evidence precision 缺 {key}（无法做三处一致、防放宽门失效）")
                continue
            if ce != ee:
                errs.append(f"{cid}: evidence {key}={ee} ≠ caseset {ce}（防放宽假通过）")
    print(f"  精度裁决={ov.get('verdict')}(validator 判) | 证据覆盖={'一致' if cids == eids else '不一致'}")


def _perf_finite_pos(x):
    """有限正数（拒 bool/None/NaN/inf/≤0）——挂起态 NPU 侧 us 完整性用。"""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0


def _pinned_file(d, rel):
    """把 rel 钉死在 d 内的普通文件（codex M2）；绝对路径/`..` 逃逸/symlink/非文件 → None。"""
    if not isinstance(rel, str) or not rel or os.path.isabs(rel):
        return None
    joined = os.path.join(d, rel)
    if os.path.islink(joined):
        return None
    base = os.path.realpath(d)
    target = os.path.realpath(joined)
    try:
        if os.path.commonpath([base, target]) != base:
            return None
    except ValueError:
        return None
    return target if os.path.isfile(target) else None


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _gate_small_shape_exception(pr, d, errs):
    """小shape例外门（T6 H6/H7/M2）：simulation 完整 + 例外行↔simulation 集合/数值交叉一致
    + 落盘 SVG(路径钉死在 d 内、sha256 重算相符)。任一不满足 → 不可静默放行 → FAILED。"""
    tag = "小shape例外缺/对不上仿真图或分析·不可静默放行"
    sim = pr.get("simulation")
    if (not isinstance(sim, dict) or "when_us_below" not in sim or "abs_gap_us_within" not in sim
            or not isinstance(sim.get("points"), list) or not sim["points"]):
        errs.append(f"{tag}：simulation 缺失/不完整")
        return
    per = pr.get("per_case") if isinstance(pr.get("per_case"), list) else []
    exc_rows = {r["case_id"]: r for r in per
                if isinstance(r, dict) and r.get("case_id") and r.get("exception")}
    sim_pts = {p["case_id"]: p for p in sim["points"]
               if isinstance(p, dict) and p.get("case_id")}
    if set(exc_rows) != set(sim_pts):
        errs.append(f"{tag}：例外行 {sorted(exc_rows)} ≠ simulation 点 {sorted(sim_pts)}")
        return
    for cid, p in sim_pts.items():
        det = exc_rows[cid].get("exception_detail") or {}
        for k in ("npu_us", "baseline_us", "gap", "within"):
            if p.get(k) != det.get(k):
                errs.append(f"{tag}：{cid} simulation.{k}={p.get(k)} ≠ exception_detail.{k}={det.get(k)}")
    plot = pr.get("simulation_plot")
    if not isinstance(plot, dict) or not plot.get("file") or not plot.get("sha256"):
        errs.append(f"{tag}：缺 simulation_plot(file/sha256)")
        return
    target = _pinned_file(d, plot["file"])
    if target is None:
        errs.append(f"{tag}：simulation_plot 路径逃逸/非普通文件 {plot['file']!r}")
        return
    if _sha256(target) != plot["sha256"]:
        errs.append(f"{tag}：simulation_plot sha256 不符（stale/被替换）")


def _perf_ids_from_caseset(cs, errs):
    """caseset 里 dims 含「性能」的 case IDs（含重复原样返回，供 Counter 全量比对查重）；抗坏字段。"""
    cases = cs.get("cases") if isinstance(cs, dict) else None
    if not isinstance(cases, list) or not cases:
        errs.append("缺/坏 caseset.json 或无用例（gate_task3 无法按 case 对齐性能证据、防跑子集）")
        return None
    ids = []
    for i, c in enumerate(cases):
        if not isinstance(c, dict):
            continue
        dims = c.get("dims")
        if isinstance(dims, list) and "性能" in dims:
            cid = c.get("id")
            if not cid:
                errs.append(f"caseset 性能 case[{i}] 缺 id")
                continue
            ids.append(cid)
    dup = [k for k, v in Counter(ids).items() if v > 1]
    if dup:
        errs.append(f"caseset 性能用例有重复 case_id: {dup}")
    return ids


def _gate_perf_case_alignment(pr, d, per, s, has_summary, errs):
    """per_case 与 caseset/evidence **按 case 对齐**（补 T5 门延后 finding）——防「跑性能子集 + 伪造
    summary=ok」蒙混：① caseset(dims 含「性能」)↔perf per_case 用 Counter 全量比对（拒缺/多/重复）；
    ② 性能 case 必须真有 evidence（拒伪造 per_case 未实跑）；③ summary 的 perf_cases/达标/blocked
    计数与 per_case 行级实际一致（拒伪造 summary）。此门只查完整性/一致性，不重判达标。"""
    per_ids = [r.get("case_id") for r in per if isinstance(r, dict) and r.get("case_id")]
    per_dups = [k for k, v in Counter(per_ids).items() if v > 1]
    if per_dups:
        errs.append(f"perf per_case 有重复 case_id: {per_dups}")
    cs = _load(d, "caseset.json")
    if cs == "__BAD__":
        errs.append("caseset.json 解析失败（无法做性能 per_case 对齐、防跑子集）")
    else:
        perf_ids = _perf_ids_from_caseset(cs, errs)  # cs=None 时内部记 error 并返回 None
        if perf_ids is not None:
            want, got = Counter(perf_ids), Counter(per_ids)
            miss = sorted((want - got).elements())
            extra = sorted((got - want).elements())
            if miss:
                errs.append(f"⚠跑性能子集：perf per_case 缺 {miss}（caseset 性能用例有、perf 无）")
            if extra:
                errs.append(f"perf per_case 多出 {extra}（caseset 无对应性能用例）")
            ev = _load(d, "evidence.json")
            if isinstance(ev, dict):
                eids = set(_ids_from_evidence(ev.get("evidence"), errs))
                ev_miss = sorted(cid for cid in set(perf_ids) if cid not in eids)
                if ev_miss:
                    errs.append(f"⚠性能证据缺失：evidence 无 {ev_miss}（性能用例未实跑/伪造 per_case）")
            elif ev == "__BAD__":
                errs.append("evidence.json 解析失败（无法核性能证据真实性）")
            elif ev is None:
                errs.append("缺 evidence.json（无法核性能证据真实性、防伪造 per_case）")
    # summary 计数须与 per_case 行级一致（防伪造 summary 蒙混）——summary 缺失已在上文报，跳过免噪。
    if has_summary:
        n_meet = sum(1 for r in per if isinstance(r, dict) and r.get("达标"))
        n_blocked = sum(1 for r in per if isinstance(r, dict) and r.get("blocked"))
        for key, actual in (("perf_cases", len(per)), ("达标", n_meet), ("blocked", n_blocked)):
            claimed = s.get(key)
            if claimed != actual:
                errs.append(f"summary.{key}={claimed!r} 与 per_case 行级实际 {actual} 不一致（伪造/漏计）")


def gate_task3(d, errs):
    """性能证据**完整性**门：summary 完整 + scope=kernel_only(防混 e2e，缺 scope 也不放过)
    + 非 blocked(可采集) + 有性能用例 + per_case 与 caseset/evidence 按 case 对齐(防跑子集/伪造 summary)。
    注：达标/未达标由 perf_compare 判、**此门不重判**。
    T6：status=exception → 强制有仿真图 + 交叉一致 + sha 校验（_gate_small_shape_exception）。
    T8：blocked_wait_gpu_benchmark=正规挂起(不计完整性 FAILED)但仍卡 NPU 侧完整性；
        blocked_incomparable_timing_scope=双边口径不可比→FAILED。安全护栏(codex H4)：门放行挂起
        只代表 NPU 证据完整，整体绝不显 PASS——那由 run_workflow 映射为 BLOCKED_* + 非零退出。"""
    pr = _load(d, "perf_report.json")
    if not isinstance(pr, dict):
        errs.append("缺/坏 perf_report.json（Task3 未跑）")
        return
    s = pr.get("summary")
    has_summary = isinstance(s, dict)
    if not has_summary:
        errs.append("perf_report 缺 summary（产物不完整）")
        s = {}
    st = s.get("status")
    wait = (st == "blocked_wait_gpu_benchmark")
    if st is None:
        errs.append("perf summary 缺 status")
    elif st not in _PERF_STATUS:
        errs.append(f"perf status={st!r} 非法（须属 {sorted(_PERF_STATUS)}）")
    elif st == "no_perf_cases":
        errs.append("无性能用例（任务书若声明性能目标→用例缺陷）")
    elif st == "blocked":
        errs.append(f"性能 blocked·无法采集：{pr.get('notes')}")
    elif st == "blocked_incomparable_timing_scope":
        errs.append(f"性能 timing_scope 不可比·NPU/基线口径不一致（不出结论）：{pr.get('notes')}")
    # blocked_wait_gpu_benchmark：正规挂起，不计完整性 error；NPU 侧完整性在下方 per_case 卡。
    per = pr.get("per_case") if isinstance(pr.get("per_case"), list) else []
    for i, r in enumerate(per):
        if not isinstance(r, dict) or not r.get("case_id"):
            errs.append(f"perf per_case[{i}] 缺 case_id")
            continue
        if r.get("blocked"):
            continue
        if wait:  # 挂起态：仍须 NPU 侧证据完整（npu_us 有限正 + npu_scope kernel_only）
            if not _perf_finite_pos(r.get("npu_us")):
                errs.append(f"{r['case_id']}: 挂起态缺/坏 npu_us（NPU 证据不完整）")
            if r.get("npu_scope") != "kernel_only":
                errs.append(f"{r['case_id']}: npu_scope={r.get('npu_scope')!r} ≠ kernel_only")
            continue
        if r.get("scope") != "kernel_only":  # 缺 scope(None) 也判失败
            errs.append(f"{r['case_id']}: scope={r.get('scope')!r} ≠ kernel_only（性能须 msprof op kernel-only）")
    # per_case 与 caseset/evidence 按 case 对齐（补 T5 门延后 finding）：防跑性能子集 + 伪造 summary=ok。
    _gate_perf_case_alignment(pr, d, per, s, has_summary, errs)
    if st == "exception":
        _gate_small_shape_exception(pr, d, errs)
    print(f"  性能 status={st}(perf_compare 判) | 达标 {s.get('达标')}/{s.get('perf_cases')}")


_GATES = {"task1": gate_task1, "task2": gate_task2, "task3": gate_task3}


def main(argv):
    ap = argparse.ArgumentParser(description="机器可校验验收门（三级，读 reports 产物 JSON）")
    ap.add_argument("--stage", required=True, choices=list(_GATES))
    ap.add_argument("--dir", required=True, help="run_workflow 的 --out 产物目录")
    a = ap.parse_args(argv)
    print(f"=== 验收门 stage={a.stage} dir={a.dir} ===")
    errs = []
    _GATES[a.stage](a.dir, errs)
    for e in errs:
        print(f"  ✗ {e}")
    passed = not errs
    print(f"STATUS: {'PASSED' if passed else 'FAILED'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
