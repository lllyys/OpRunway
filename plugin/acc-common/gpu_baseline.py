"""Task 3 · gpu_baseline — 外部 GPU 标杆 JSON 解析+校验（consumer 侧，T8）。

`parse_gpu_baseline(path, caseset) -> (baseline|None, parse_report)`：
- 按 canon 字段契约（gpu_baseline_contract.json，15 项）逐字段校验；device 必须 GPU(非 NPU)；
  timing_scope ∈ 3 枚举；unit→us 归一；data_transfer_included 与 scope 自洽（codex M1）；
- 按 case_id + **完整输入签名**(case_fingerprint = sorted inputs(name,dtype,shape)+sorted attrs)交叉核对，
  签名不符→hard error（防拿别 shape 的 GPU 数字冒充可比，覆盖二元/广播，codex H5）；
- 集合语义：须**恰好覆盖**全部性能维用例——缺→hard error(该行 blocked)、多→hard error(拒 extra)（codex M2）；
- 计时 policy 风险：warmup<10 / iters<30 / statistic∉{median,p50} → severity=warn 的 policy_risk，
  不硬崩、但下游不允许干净 PASSED（升 PASSED_WITH_RISK，codex M6）；
- 结构化 parse_report：issue={code,severity,case_id,field,message} 列表（codex M5）；
  **任一 hard error → baseline=None（阻断对比、不静默）；warn → 放行并记录**；
- 输出内部 baseline dict 与 perf_compare 现有消费格式一致。**绝不 raise 崩溃**。

真数据未到：枚举串/字段编码 provisional，按 contract_version 迭代（open）。
"""
import hashlib, json, math, os, sys

_SCOPES = {"kernel_only", "device_e2e_no_h2d_d2h", "host_e2e_with_h2d_d2h"}
_UNIT_TO_US = {"ns": 0.001, "us": 1.0, "ms": 1000.0, "s": 1000000.0}
_STAT_ENUM = {"median", "p50", "mean", "min", "max"}
_STAT_PREFER = {"median", "p50"}
_SCOPE_TRANSFER = {"kernel_only": False, "device_e2e_no_h2d_d2h": False, "host_e2e_with_h2d_d2h": True}
_CLOCK_ALIASES = ("clock_power_state", "clock-power", "clock", "power_mode")


def _finite_pos(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0


def _contract_version():
    """从同目录 gpu_baseline_contract.json 读 contract_version；缺文件/坏 JSON → 'unknown'。"""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gpu_baseline_contract.json")
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f).get("contract_version", "unknown")
    except (OSError, json.JSONDecodeError):
        return "unknown"


def case_fingerprint(inputs, attrs):
    """完整输入签名：sorted inputs(name,dtype,shape) + sorted attrs 的稳定哈希（codex H5）。"""
    ins = sorted([[str(i.get("name")), str(i.get("dtype")), list(i.get("shape") or [])]
                  for i in (inputs or []) if isinstance(i, dict)])
    at = {str(k): v for k, v in (attrs or {}).items()}
    payload = json.dumps({"inputs": ins, "attrs": at}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _issue(issues, code, severity, case_id, field, message):
    issues.append({"code": code, "severity": severity, "case_id": case_id,
                   "field": field, "message": message})


def parse_gpu_baseline(path, caseset):
    ver = _contract_version()
    issues = []
    report = {"contract_version": ver, "source": "gpu_external", "path": path,
              "entries_seen": 0, "hard_errors": 0, "warns": 0, "issues": issues}

    def _finish(baseline):
        report["hard_errors"] = sum(1 for i in issues if i["severity"] == "error")
        report["warns"] = sum(1 for i in issues if i["severity"] == "warn")
        return (baseline if report["hard_errors"] == 0 else None), report

    # 载入（绝不 raise）
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as ex:
        _issue(issues, "FILE_LOAD", "error", None, "file", f"GPU 标杆文件缺/坏：{ex}")
        return _finish(None)
    if not isinstance(raw, dict):
        _issue(issues, "SHAPE", "error", None, "root", "GPU 标杆顶层须为对象")
        return _finish(None)
    if raw.get("contract_version"):
        report["contract_version"] = raw["contract_version"]
    entries = raw.get("cases", raw.get("per_case"))
    if not isinstance(entries, list) or not entries:
        _issue(issues, "SHAPE", "error", None, "cases", "GPU 标杆缺 cases 列表")
        return _finish(None)

    # caseset 性能维用例 + 其完整输入签名
    perf_cases = {c["id"]: c for c in caseset.get("cases", [])
                  if isinstance(c, dict) and c.get("id") and "性能" in c.get("dims", [])}
    perf_fp = {cid: case_fingerprint(c.get("inputs"), c.get("attrs"))
               for cid, c in perf_cases.items()}
    top = {k: raw.get(k) for k in ("device", "tool", "timing_scope", "sync_policy",
                                   "statistic", "unit", "clock_power_state", "data_transfer_included")}

    report["entries_seen"] = len(entries)
    per, scopes, seen = [], set(), set()
    for idx, ent in enumerate(entries):
        if not isinstance(ent, dict):
            _issue(issues, "SHAPE", "error", None, f"cases[{idx}]", "条目非对象")
            continue
        cid = ent.get("case_id")
        if not cid or not isinstance(cid, str):
            _issue(issues, "MISSING", "error", None, "case_id", "条目缺 case_id")
            continue
        if cid in seen:
            _issue(issues, "DUP", "error", cid, "case_id", "重复 case_id")
            continue
        seen.add(cid)

        def g(field):
            v = ent.get(field)
            return v if v is not None else top.get(field)

        if cid not in perf_cases:  # 多出 caseset 没有的 → 拒 extra（集合语义 codex M2）
            _issue(issues, "EXTRA_CASE", "error", cid, "case_id", "caseset 无此性能用例（拒 extra）")
            continue

        device = g("device")
        if not device or not isinstance(device, str):
            _issue(issues, "MISSING", "error", cid, "device", "缺 device")
        elif any(t in device.lower() for t in ("ascend", "npu")):
            _issue(issues, "NOT_GPU", "error", cid, "device", f"device={device!r} 非 GPU（GPU 只作标杆）")

        dtype, shape = ent.get("dtype"), ent.get("shape")
        if not dtype:
            _issue(issues, "MISSING", "error", cid, "dtype", "缺 dtype")
        if not isinstance(shape, list):
            _issue(issues, "MISSING", "error", cid, "shape", "缺 shape(数组)")
        # 完整输入签名交叉核对（codex H5）
        gpu_inputs = ent.get("inputs")
        if not isinstance(gpu_inputs, list):  # 未给完整 inputs → 用 (dtype,shape) 拼单输入签名兜底
            gpu_inputs = [{"name": "self", "dtype": dtype, "shape": shape or []}]
        fp = case_fingerprint(gpu_inputs, ent.get("attrs"))
        if fp != perf_fp.get(cid):
            _issue(issues, "FINGERPRINT", "error", cid, "inputs/attrs",
                   f"完整输入签名不符 GPU={fp} ≠ caseset={perf_fp.get(cid)}（防拿别 shape 冒充可比）")

        scope = g("timing_scope")
        if scope not in _SCOPES:
            _issue(issues, "ENUM", "error", cid, "timing_scope", f"timing_scope={scope!r} 非法")
        else:
            scopes.add(scope)

        unit = g("unit")
        if unit not in _UNIT_TO_US:
            _issue(issues, "ENUM", "error", cid, "unit", f"unit={unit!r} 非法")
        value = ent.get("value")
        if not _finite_pos(value):
            _issue(issues, "VALUE", "error", cid, "value", f"value={value!r} 非有限正数")
        us = round(float(value) * _UNIT_TO_US[unit], 3) if (unit in _UNIT_TO_US and _finite_pos(value)) else None

        if not g("tool"):
            _issue(issues, "MISSING", "error", cid, "tool", "缺 tool")
        if not g("sync_policy"):
            _issue(issues, "MISSING", "error", cid, "sync_policy", "缺 sync_policy")

        dti = g("data_transfer_included")
        if not isinstance(dti, bool):
            _issue(issues, "MISSING", "error", cid, "data_transfer_included", "缺/非 bool data_transfer_included")
        elif scope in _SCOPE_TRANSFER and dti != _SCOPE_TRANSFER[scope]:
            _issue(issues, "SCOPE_TRANSFER", "error", cid, "data_transfer_included",
                   f"data_transfer_included={dti} 与 timing_scope={scope} 不自洽")

        # 计时 policy 风险（warn，不硬崩，下游升 PASSED_WITH_RISK）
        risk = []
        w, it, stat = g("warmup"), g("iters"), g("statistic")
        if not (isinstance(w, (int, float)) and not isinstance(w, bool) and w >= 10):
            risk.append(f"warmup={w}<10")
        if not (isinstance(it, (int, float)) and not isinstance(it, bool) and it >= 30):
            risk.append(f"iters={it}<30")
        if stat not in _STAT_ENUM:
            _issue(issues, "ENUM", "error", cid, "statistic", f"statistic={stat!r} 非法")
        elif stat not in _STAT_PREFER:
            risk.append(f"statistic={stat}∉(median,p50)")
        for r in risk:
            _issue(issues, "POLICY_RISK", "warn", cid, "timing_policy", r)

        row = {"case_id": cid, "us": us, "env": f"{device}/{g('tool')}"}
        if risk:
            row["policy_risk"] = risk
        per.append(row)

    # 集合语义：缺某性能 case → hard error（该行 blocked，不出局部 PASS）
    missing = sorted(set(perf_cases) - seen)
    for cid in missing:
        _issue(issues, "MISSING_CASE", "error", cid, "case_id", "GPU 标杆缺此性能用例（集合不完整）")

    if len(scopes) > 1:  # 混合 scope 无法定单一对比口径
        _issue(issues, "MIXED_SCOPE", "error", None, "timing_scope",
               f"GPU 标杆各用例 timing_scope 不一致：{sorted(scopes)}")

    bscope = next(iter(scopes)) if len(scopes) == 1 else None
    baseline = {"source": "gpu_external", "scope": bscope, "contract_version": report["contract_version"],
                "per_case": per}
    return _finish(baseline)


def main(argv):
    """CLI：gpu_baseline.py <gpu_baseline.json> <caseset.json> [out_parse_report.json]"""
    caseset = json.load(open(argv[1], encoding="utf-8"))
    baseline, report = parse_gpu_baseline(argv[0], caseset)  # argv[0]=GPU 标杆路径（parser 内部载入）
    out = argv[2] if len(argv) > 2 else "gpu_baseline_parse_report.json"
    json.dump(report, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[gpu_baseline] hard_errors={report['hard_errors']} warns={report['warns']} "
          f"baseline={'None' if baseline is None else len(baseline['per_case'])} -> {out}")


if __name__ == "__main__":
    main(sys.argv[1:])
