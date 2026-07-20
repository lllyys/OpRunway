"""Task 3 · gpu_baseline — 外部 GPU 标杆 JSON 解析+校验（consumer 侧，T8）。

`parse_gpu_baseline(path, caseset) -> (baseline|None, parse_report)`：
- 按 canon 字段契约（gpu_baseline_contract.json）逐字段**严格 isinstance** 校验（gb-4）；device 用**正向白名单**
  （device_type=='gpu' 或 nvidia/amd 等型号命中才放行，未命中即 hard error；ascend/npu/tpu/cpu 黑名单兜底，gb-2）；
  timing_scope ∈ 3 枚举；unit→us 归一（换算不过早 round、断言 us>0，gb-6）；data_transfer_included 与 scope 自洽；
- 按 case_id + **完整输入签名**(case_fingerprint = sorted inputs(name,dtype,shape)+sorted attrs)交叉核对，
  签名不符→hard error（防拿别 shape 的 GPU 数字冒充可比，覆盖二元/广播）；坏 attrs/shape/inputs 记 hard error 且**绝不 raise**（gb-3）；
- 集合语义：须**恰好覆盖**全部性能维用例——缺→hard error(该行 blocked)、多→hard error(拒 extra)；
- 计时 policy 风险：warmup/iters 先过 finite+非负（NaN/Inf/负 → hard error，gb-8），有限但 <policy_min / statistic∉{median,p50} → warn；
- 结构化 parse_report：issue={code,severity,case_id,field,message} 列表；
  **任一 hard error → baseline=None（阻断对比、不静默）；warn → 放行并记录**；
  report['blocked_status'] 携专门挂起码（混合 scope→incomparable，其它硬错→invalid，非「缺标杆」，gb-9）；
- 读文件设大小/条目数/字符串长度上限，超限 hard error（gb-7，抗 DoS）。**绝不 raise 崩溃**。

真数据未到：枚举串/字段编码 provisional，按 contract_version 迭代（open）。
"""
import hashlib, json, math, os, sys

_SCOPES = {"kernel_only", "device_e2e_no_h2d_d2h", "host_e2e_with_h2d_d2h"}
_UNIT_TO_US = {"ns": 0.001, "us": 1.0, "ms": 1000.0, "s": 1000000.0}
_STAT_ENUM = {"median", "p50", "mean", "min", "max"}
_STAT_PREFER = {"median", "p50"}
_SCOPE_TRANSFER = {"kernel_only": False, "device_e2e_no_h2d_d2h": False, "host_e2e_with_h2d_d2h": True}
# gb-2：正向 GPU 白名单（device_type=='gpu' 或型号命中才放行）+ 兜底非 GPU 黑名单。
_GPU_ALLOW = ("nvidia", "geforce", "rtx", "gtx", "tesla", "quadro", "titan",
              "a100", "a800", "a40", "a30", "a10", "h100", "h800", "h200", "v100",
              "l40", "l4", "amd", "radeon", "instinct", "mi300", "mi250", "mi210", "mi100")
_NON_GPU_DENY = ("ascend", "npu", "昇腾", "华为", "kunpeng", "鲲鹏", "tpu",
                 "cpu", "xeon", "epyc", "core i", "ryzen")  # 仅收明确非 GPU 词（不含歧义的 arm）
# gb-7：抗 DoS 上限（外部不可信文件）。
_MAX_FILE_BYTES = 8 * 1024 * 1024
_MAX_ENTRIES = 100000
_MAX_STR_LEN = 4096


def _finite_pos(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0


def _is_num(x):
    """number（int/float，拒 bool）。"""
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _valid_shape(shape):
    """shape 须 list 且元素为非负 int（拒 bool、拒非标量）——gb-4。"""
    return isinstance(shape, list) and all(
        isinstance(d, int) and not isinstance(d, bool) and d >= 0 for d in shape)


# §trivial-met（评审 #2 / codex #4）：退化 case（numel<阈值）perf 无意义、免测 → GPU 标杆**无需覆盖**
# （真机 GPU 逐 trivial case 给数不现实）。阈值固定 4096，同 perf_compare/门口径。
_GPU_TRIVIAL_MAX_NUMEL = 4096


def _bcast_numel(inputs):
    """全部输入 broadcast 输出 numel（右对齐、1 广播、冲突/坏维→None）。供 trivial 判定，与 perf_compare 同规范。"""
    shapes = []
    for it in (inputs or []):
        if not isinstance(it, dict) or not isinstance(it.get("shape"), list):
            return None
        shapes.append(it["shape"])
    if not shapes:
        return None
    out_rev, maxlen = [], max((len(s) for s in shapes), default=0)
    for i in range(maxlen):
        dim = 1
        for s in shapes:
            if i >= len(s):
                continue
            dd = s[len(s) - 1 - i]
            if not isinstance(dd, int) or isinstance(dd, bool) or dd < 0:
                return None
            if dd == 1:
                continue
            if dim == 1:
                dim = dd
            elif dim != dd:
                return None
        out_rev.append(dim)
    n = 1
    for dd in out_rev:
        n *= dd
    return n


def _contract_version():
    """从同目录 gpu_baseline_contract.json 读 contract_version；缺文件/坏 JSON → 'unknown'。"""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gpu_baseline_contract.json")
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f).get("contract_version", "unknown")
    except (OSError, json.JSONDecodeError):
        return "unknown"


def case_fingerprint(inputs, attrs):
    """完整输入签名：sorted inputs(name,dtype,shape) + sorted attrs 的稳定哈希。
    内部对坏类型全防御（attrs 非 dict→{}、shape 非 list→[]、inputs 非 list→[]）——绝不 raise（gb-3）。"""
    ins = []
    if isinstance(inputs, list):
        for i in inputs:
            if not isinstance(i, dict):
                continue
            shp = i.get("shape")
            shp = list(shp) if isinstance(shp, list) else []
            ins.append([str(i.get("name")), str(i.get("dtype")), shp])
    ins.sort(key=lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False, default=str))
    at = {str(k): v for k, v in attrs.items()} if isinstance(attrs, dict) else {}
    payload = json.dumps({"inputs": ins, "attrs": at}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _issue(issues, code, severity, case_id, field, message):
    issues.append({"code": code, "severity": severity, "case_id": case_id,
                   "field": field, "message": message})


def _need_str(val, field, cid, issues):
    """契约 type==string 严格校验（gb-4）：非 str/空/超长 → hard error，返回 None。"""
    if not isinstance(val, str) or not val:
        _issue(issues, "TYPE", "error", cid, field, f"{field}={val!r} 须非空字符串")
        return None
    if len(val) > _MAX_STR_LEN:
        _issue(issues, "TOO_LONG", "error", cid, field, f"{field} 字符串超长 {len(val)}>{_MAX_STR_LEN}")
        return None
    return val


def _check_device(device, device_type, cid, issues):
    """gb-2：正向白名单——device_type=='gpu' 或型号命中 GPU allowlist 才放行；
    黑名单命中（ascend/npu/tpu/cpu…）或未命中白名单 → hard error（防 CPU/TPU/NPU 冒充 GPU）。"""
    if not isinstance(device, str) or not device.strip():
        _issue(issues, "MISSING", "error", cid, "device", "缺 device（须非空字符串）")
        return
    dl = device.lower()
    if any(t in dl for t in _NON_GPU_DENY):                 # 兜底黑名单：显式非 GPU
        _issue(issues, "NOT_GPU", "error", cid, "device", f"device={device!r} 属非 GPU（黑名单命中）")
        return
    dt_ok = isinstance(device_type, str) and device_type.strip().lower() == "gpu"
    if dt_ok or any(t in dl for t in _GPU_ALLOW):           # 正向白名单
        return
    _issue(issues, "NOT_GPU", "error", cid, "device",
           f"device={device!r} 未命中 GPU 白名单（要求 device_type=='gpu' 或 nvidia/amd 等型号）")


def _check_numeric_policy(val, field, policy_min, cid, issues, risk):
    """gb-4/gb-8：number 严格 + finite + 非负（NaN/Inf/负/非 number → hard error）；有限但 <policy_min → warn。"""
    if not _is_num(val):
        _issue(issues, "TYPE", "error", cid, field, f"{field}={val!r} 须 number（拒 bool/字符串）")
        return
    if not math.isfinite(val) or val < 0:
        _issue(issues, "VALUE", "error", cid, field, f"{field}={val!r} 须有限非负（NaN/Inf/负 → 硬错）")
        return
    if val < policy_min:
        risk.append(f"{field}={val}<{policy_min}")


def parse_gpu_baseline(path, caseset):
    ver = _contract_version()
    issues = []
    report = {"contract_version": ver, "source": "gpu_external", "path": path,
              "entries_seen": 0, "hard_errors": 0, "warns": 0, "issues": issues}

    def _finish(baseline):
        report["hard_errors"] = sum(1 for i in issues if i["severity"] == "error")
        report["warns"] = sum(1 for i in issues if i["severity"] == "warn")
        if report["hard_errors"]:  # gb-9：区分挂起码——混合 scope→不可比；其它硬错→标杆被判废（非「缺标杆」）
            report["blocked_status"] = ("blocked_incomparable_timing_scope"
                                        if any(i["code"] == "MIXED_SCOPE" for i in issues)
                                        else "blocked_gpu_baseline_invalid")
        return (baseline if report["hard_errors"] == 0 else None), report

    # gb-7：读前限文件大小；载入绝不 raise
    try:
        if os.path.getsize(path) > _MAX_FILE_BYTES:
            _issue(issues, "FILE_TOO_BIG", "error", None, "file",
                   f"GPU 标杆文件超上限 {_MAX_FILE_BYTES}B")
            return _finish(None)
    except OSError as ex:
        _issue(issues, "FILE_LOAD", "error", None, "file", f"GPU 标杆文件缺/坏：{ex}")
        return _finish(None)
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError) as ex:
        _issue(issues, "FILE_LOAD", "error", None, "file", f"GPU 标杆文件缺/坏：{ex}")
        return _finish(None)
    if not isinstance(raw, dict):
        _issue(issues, "SHAPE", "error", None, "root", "GPU 标杆顶层须为对象")
        return _finish(None)
    if isinstance(raw.get("contract_version"), str) and raw["contract_version"]:
        report["contract_version"] = raw["contract_version"]
    entries = raw.get("cases", raw.get("per_case"))
    if not isinstance(entries, list) or not entries:
        _issue(issues, "SHAPE", "error", None, "cases", "GPU 标杆缺 cases 列表")
        return _finish(None)
    if len(entries) > _MAX_ENTRIES:  # gb-7
        _issue(issues, "TOO_MANY", "error", None, "cases", f"条目数超上限 {_MAX_ENTRIES}")
        return _finish(None)

    # caseset 性能维用例（§trivial-met：退化 case numel<阈值免测 → GPU 标杆**无需覆盖**、从 required 剔除；
    #  GPU 若给 trivial 数据也宽容忽略、不当 extra）。完整输入签名仅对 required（非 trivial）建。
    all_perf = {c["id"]: c for c in caseset.get("cases", [])
                if isinstance(c, dict) and c.get("id") and "性能" in (c.get("dims") or [])}
    trivial_ids = {cid for cid, c in all_perf.items()
                   if isinstance(_bcast_numel(c.get("inputs")), int)
                   and 0 < _bcast_numel(c.get("inputs")) < _GPU_TRIVIAL_MAX_NUMEL}
    perf_cases = {cid: c for cid, c in all_perf.items() if cid not in trivial_ids}
    perf_fp = {cid: case_fingerprint(c.get("inputs"), c.get("attrs"))
               for cid, c in perf_cases.items()}
    top = {k: raw.get(k) for k in ("device", "device_type", "tool", "timing_scope", "sync_policy",
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
        if len(cid) > _MAX_STR_LEN:
            _issue(issues, "TOO_LONG", "error", None, "case_id", "case_id 超长")
            continue
        if cid in seen:
            _issue(issues, "DUP", "error", cid, "case_id", "重复 case_id")
            continue
        seen.add(cid)

        def g(field, _ent=ent):
            v = _ent.get(field)
            return v if v is not None else top.get(field)

        if cid not in perf_cases:  # 多出 caseset 没有的 → 拒 extra（集合语义）
            if cid in trivial_ids:  # §trivial-met：GPU 给了 trivial case 数据 → 宽容忽略（不当 extra、不参与对比）
                continue
            _issue(issues, "EXTRA_CASE", "error", cid, "case_id", "caseset 无此性能用例（拒 extra）")
            continue

        _check_device(g("device"), g("device_type"), cid, issues)

        # dtype/shape/attrs/inputs 严格类型（gb-4/gb-3），并防 fingerprint 崩溃
        dtype = _need_str(ent.get("dtype"), "dtype", cid, issues)
        shape = ent.get("shape")
        if not _valid_shape(shape):
            _issue(issues, "TYPE", "error", cid, "shape", f"shape={shape!r} 须非负 int 数组（拒 bool/非标量）")
            shape = None
        attrs = ent.get("attrs")
        if attrs is not None and not isinstance(attrs, dict):
            _issue(issues, "TYPE", "error", cid, "attrs", f"attrs={attrs!r} 须 object")
            attrs = None

        gpu_inputs = ent.get("inputs")
        if gpu_inputs is not None:
            if not isinstance(gpu_inputs, list) or not all(isinstance(i, dict) for i in gpu_inputs):
                _issue(issues, "TYPE", "error", cid, "inputs", "inputs 须 dict 数组")
                gpu_inputs = None
            else:
                for i in gpu_inputs:
                    if not _valid_shape(i.get("shape")):
                        _issue(issues, "TYPE", "error", cid, "inputs",
                               f"input.shape={i.get('shape')!r} 非法（须非负 int 数组）")
                # gb-5：给了 inputs 时，顶层 dtype/shape 须与 inputs[0] 一致（否则矛盾也过）
                if gpu_inputs:
                    first = gpu_inputs[0]
                    if dtype is not None and first.get("dtype") is not None and first.get("dtype") != dtype:
                        _issue(issues, "INPUT_CONSISTENCY", "error", cid, "inputs/dtype",
                               f"inputs[0].dtype={first.get('dtype')!r} ≠ 顶层 dtype={dtype!r}")
                    if (shape is not None and isinstance(first.get("shape"), list)
                            and first.get("shape") != shape):
                        _issue(issues, "INPUT_CONSISTENCY", "error", cid, "inputs/shape",
                               f"inputs[0].shape={first.get('shape')!r} ≠ 顶层 shape={shape!r}")
        if gpu_inputs is None:  # 未给完整 inputs → 用 (dtype,shape) 拼单输入签名兜底
            gpu_inputs = [{"name": "self", "dtype": dtype, "shape": shape or []}]
        fp = case_fingerprint(gpu_inputs, attrs)
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
        # gb-6：换算不过早 round（防极小正值抹成 0），断言 us>0（防溢出/underflow）
        us = None
        if unit in _UNIT_TO_US and _finite_pos(value):
            us = float(value) * _UNIT_TO_US[unit]
            if not _finite_pos(us):
                _issue(issues, "VALUE", "error", cid, "value", f"换算后 us={us!r} 非有限正数（溢出/下溢）")
                us = None

        _need_str(g("tool"), "tool", cid, issues)
        _need_str(g("sync_policy"), "sync_policy", cid, issues)

        dti = g("data_transfer_included")
        if not isinstance(dti, bool):
            _issue(issues, "MISSING", "error", cid, "data_transfer_included", "缺/非 bool data_transfer_included")
        elif scope in _SCOPE_TRANSFER and dti != _SCOPE_TRANSFER[scope]:
            _issue(issues, "SCOPE_TRANSFER", "error", cid, "data_transfer_included",
                   f"data_transfer_included={dti} 与 timing_scope={scope} 不自洽")

        # 计时 policy：warmup/iters 先过 finite+非负硬错（gb-8），有限但 <min → warn（gb-4）
        risk = []
        _check_numeric_policy(g("warmup"), "warmup", 10, cid, issues, risk)
        _check_numeric_policy(g("iters"), "iters", 30, cid, issues, risk)
        stat = g("statistic")
        if stat not in _STAT_ENUM:
            _issue(issues, "ENUM", "error", cid, "statistic", f"statistic={stat!r} 非法")
        elif stat not in _STAT_PREFER:
            risk.append(f"statistic={stat}∉(median,p50)")
        for r in risk:
            _issue(issues, "POLICY_RISK", "warn", cid, "timing_policy", r)

        row = {"case_id": cid, "us": us, "env": f"{g('device')}/{g('tool')}"}
        if risk:
            row["policy_risk"] = risk
        per.append(row)

    # 集合语义：缺某性能 case → hard error（该行 blocked，不出局部 PASS）
    for cid in sorted(set(perf_cases) - seen):
        _issue(issues, "MISSING_CASE", "error", cid, "case_id", "GPU 标杆缺此性能用例（集合不完整）")

    if len(scopes) > 1:  # 混合 scope 无法定单一对比口径 → gb-9 挂起码走 incomparable
        _issue(issues, "MIXED_SCOPE", "error", None, "timing_scope",
               f"GPU 标杆各用例 timing_scope 不一致：{sorted(scopes)}")

    bscope = next(iter(scopes)) if len(scopes) == 1 else None
    baseline = {"source": "gpu_external", "scope": bscope, "contract_version": report["contract_version"],
                "per_case": per}
    return _finish(baseline)


def main(argv):
    """CLI：gpu_baseline.py <gpu_baseline.json> <caseset.json> [out_parse_report.json]"""
    with open(argv[1], encoding="utf-8") as f:
        caseset = json.load(f)
    baseline, report = parse_gpu_baseline(argv[0], caseset)  # argv[0]=GPU 标杆路径（parser 内部载入）
    out = argv[2] if len(argv) > 2 else "gpu_baseline_parse_report.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"[gpu_baseline] hard_errors={report['hard_errors']} warns={report['warns']} "
          f"baseline={'None' if baseline is None else len(baseline['per_case'])} -> {out}")


if __name__ == "__main__":
    main(sys.argv[1:])
