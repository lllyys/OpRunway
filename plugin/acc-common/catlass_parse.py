"""P3 catlass adapter · 解析层（stdlib-only、纯解析不判定）。

只把真机 raw log / msprof CSV 解析成结构化字段，**pass/fail 一律不归这里**
（裁决交 validator / perf_compare，ADR 0007；catlass `Compare success.` 只是仓内 smoke 信号）。

- parse_raw_log()   : 数哨兵、逐 case ok/FAIL、识崩溃/空日志 —— 抗缺行、坏输入不崩（子任务④）。
- parse_msprof_csv(): 按**列名** `Task Duration(us)` 解析 OpBasicInfo.csv（不写死第 3 列），取
                      median + p90/min；非 kernel-only 口径（op_summary/含 H2D/D2H）显式拒（子任务⑤）。
- profile_hit_gate(): 运行后 profile 命中门 —— 解析真 CSV 断言存在 kernel 行；符号未预知时回填实测名。

raw log **只存 hash/path、不作判定依据**：本模块产的是「测到什么」，不是「过没过」。
"""
import csv, hashlib, io, os, re, sys

# catlass 自带 example 的 smoke 哨兵；我们的 runner 逐 case 前缀 [OPRUNWAY_CASE <cid>]。
_CASE_RE = re.compile(r"\[OPRUNWAY_CASE\s+([A-Za-z0-9_.-]+)\]")
_OK_RE = re.compile(r"Compare\s+success", re.IGNORECASE)
_FAIL_RE = re.compile(r"Compare\s+failed", re.IGNORECASE)
# 崩溃信号（进程异常终止 → 日志可能截断、哨兵缺失，绝不能被当成「跑完」）。
_CRASH_RES = [re.compile(p, re.IGNORECASE) for p in (
    r"segmentation fault", r"core dumped", r"\baborted\b", r"terminate called",
    r"double free", r"std::bad_alloc", r"ACL_ERROR", r"EE[0-9]{4}", r"aclrt\w+ failed")]
# runner 跑完的收尾哨兵（缺 → 疑截断/崩溃）。
_DONE_RE = re.compile(r"OPRUNWAY_CATLASS_DONE")


def _norm(s):
    """列名归一：小写 + 去所有空白，容忍 `Task Duration(us)` / `task duration (us)` 等写法。"""
    return re.sub(r"\s+", "", (s or "").strip().lower())


def sha256_of(text):
    """raw log/CSV 的内容指纹（入 artifact_manifest；raw 本身不作判定依据）。"""
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def parse_raw_log(text):
    """raw 跑测日志 → 结构化信号（不判 pass/fail）。抗缺行/空日志/崩溃截断。

    返回 {cases:{cid:"ok"/"fail"}, success_count, failed_count, crashed, done,
          sentinel_total, raw_len, sha256}。
    - 逐 case：行内含 [OPRUNWAY_CASE cid] 且同段出现 Compare success/failed → 记该 cid。
    - crashed=True：命中崩溃信号，或有 case 段但缺收尾哨兵（截断）。
    - 空/坏输入：不崩，success=failed=0、crashed 由信号定。
    """
    text = text if isinstance(text, str) else (text or b"").decode("utf-8", "replace")
    cases, success_count, failed_count = {}, 0, 0
    crashed = any(rx.search(text) for rx in _CRASH_RES)
    cur = None
    for line in text.splitlines():
        m = _CASE_RE.search(line)
        if m:
            cur = m.group(1)
        if _OK_RE.search(line):
            success_count += 1
            if cur:
                cases[cur] = "ok"
        elif _FAIL_RE.search(line):
            failed_count += 1
            if cur:
                cases[cur] = "fail"
    done = bool(_DONE_RE.search(text))
    # 有 case 段却没跑完收尾 → 视作截断/崩溃（诚实：宁可标可疑，不当成功）
    if cases and not done:
        crashed = True
    return {"cases": cases, "success_count": success_count, "failed_count": failed_count,
            "crashed": crashed, "done": done,
            "sentinel_total": success_count + failed_count,
            "raw_len": len(text), "sha256": sha256_of(text)}


# 非 kernel-only 口径的判据列（op_summary / --application e2e：含主机侧/搬运耗时）。
_E2E_MARKERS = ("hostduration", "h2d", "d2h", "memcpy", "aclrtduration",
                "e2eduration", "hostduration(us)")
# kernel-only 时长列（OpBasicInfo.csv，msprof op）。
_KERNEL_DUR_KEYS = ("taskduration(us)", "taskduration", "aicoreduration(us)")
# kernel/op 名列。
_NAME_KEYS = ("opname", "kernelname", "op_name", "name", "optype")


def _read_rows(text_or_path):
    """读 CSV 文本或路径 → (header列表, dict行列表)。空/无 header → ([], [])。"""
    s = text_or_path or ""
    if "\n" not in s and len(s) < 4096 and os.path.exists(s):  # 无换行且是真实文件 → 当路径读
        try:
            with open(s, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            return [], []
    else:
        text = s
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if r and any(c.strip() for c in r)]
    if not rows:
        return [], []
    header = rows[0]
    dict_rows = []
    for r in rows[1:]:
        dict_rows.append({header[i]: (r[i] if i < len(r) else "") for i in range(len(header))})
    return header, dict_rows


def _col_index(header, keys):
    """在 header 里按归一名找列，返回原始列名（找不到 None）。"""
    norm_map = {_norm(h): h for h in header}
    for k in keys:
        if k in norm_map:
            return norm_map[k]
    return None


def parse_msprof_csv(text_or_path, require_scope="kernel_only", kernel_name=None):
    """按**列名**解析 msprof OpBasicInfo.csv（不写死第 3 列，补 codex #16）。

    返回 {ok, reason, scope, count, median_us, p90_us, min_us, durations, kernels}。
    - require_scope=kernel_only：若 CSV 出现 e2e 判据列（Host/H2D/D2H/…）→ ok=False、reason 说明拒因
      （防把含 H2D/D2H 的 e2e 口径混作 kernel-only，ADR 0006）。
    - kernel_name 给定：只取该 kernel（前缀匹配）行的时长；否则取全部数值行。
    - 坏 header / 空行 / 缺时长列：ok=False + reason，绝不崩。
    """
    header, rows = _read_rows(text_or_path)
    if not header:
        return {"ok": False, "reason": "空 CSV 或无表头", "scope": None,
                "count": 0, "median_us": None, "p90_us": None, "min_us": None,
                "durations": [], "kernels": []}
    norm_header = {_norm(h) for h in header}
    if require_scope == "kernel_only":
        hit_e2e = [m for m in _E2E_MARKERS if m in norm_header]
        if hit_e2e:
            return {"ok": False,
                    "reason": f"非 kernel-only 口径：CSV 含 e2e 列 {hit_e2e}（拒混 H2D/D2H）",
                    "scope": "e2e_suspected", "count": 0, "median_us": None,
                    "p90_us": None, "min_us": None, "durations": [], "kernels": []}
    dur_col = _col_index(header, _KERNEL_DUR_KEYS)
    if dur_col is None:
        return {"ok": False, "reason": f"缺时长列 Task Duration(us)（表头={header}）",
                "scope": None, "count": 0, "median_us": None, "p90_us": None,
                "min_us": None, "durations": [], "kernels": []}
    name_col = _col_index(header, _NAME_KEYS)
    durations, kernels = [], []
    for r in rows:
        nm = (r.get(name_col, "") if name_col else "").strip()
        if kernel_name and not nm.startswith(kernel_name):
            continue
        raw = (r.get(dur_col, "") or "").strip().replace(",", "")
        try:
            v = float(raw)
        except ValueError:
            continue
        if v > 0 and v == v and v != float("inf"):  # 有限正数（拒 NaN/inf/≤0）
            durations.append(v)
            if nm:
                kernels.append(nm)
    if not durations:
        return {"ok": False, "reason": "无有效时长行（可能 kernel_name 不匹配或全为非法值）",
                "scope": "kernel_only", "count": 0, "median_us": None, "p90_us": None,
                "min_us": None, "durations": [], "kernels": sorted(set(kernels))}
    s = sorted(durations)
    n = len(s)
    median = s[n // 2] if n % 2 else round((s[n // 2 - 1] + s[n // 2]) / 2, 6)
    p90 = s[min(n - 1, int(round(0.9 * (n - 1))))]
    return {"ok": True, "reason": "", "scope": "kernel_only", "count": n,
            "median_us": round(median, 6), "p90_us": round(p90, 6),
            "min_us": round(s[0], 6), "durations": s, "kernels": sorted(set(kernels))}


def profile_hit_gate(text_or_path, expected_kernel=None):
    """运行后 profile 命中门（补 codex #13）：解析真 CSV 断言存在 kernel 行。

    catlass 裸模板符号不可预设 → expected_kernel=None 时取 CSV 实测 kernel 名回填记录；
    给了 expected_kernel 则要求前缀命中。返回 {hit, observed_kernels, matched, reason, pending}。
    真机 CSV 未产出时（本轮）逻辑就绪、标 pending=True，不冒充已验证。
    """
    parsed = parse_msprof_csv(text_or_path, require_scope="kernel_only",
                              kernel_name=None)
    if not parsed["ok"]:
        return {"hit": False, "observed_kernels": parsed.get("kernels", []),
                "matched": None, "reason": parsed["reason"], "pending": False}
    observed = parsed["kernels"]
    if not observed:
        return {"hit": parsed["count"] > 0, "observed_kernels": [],
                "matched": None,
                "reason": "有时长行但无 kernel 名列 —— 命中存在、符号名待真机回填",
                "pending": True}
    if expected_kernel is None:
        return {"hit": True, "observed_kernels": observed, "matched": None,
                "reason": "符号未预知：回填实测 kernel 名", "pending": True}
    matched = [k for k in observed if k.startswith(expected_kernel)]
    return {"hit": bool(matched), "observed_kernels": observed,
            "matched": matched or None,
            "reason": ("命中" if matched else f"未命中 {expected_kernel}（实测 {observed}）"),
            "pending": False}


def main(argv):
    if not argv:
        raise SystemExit("用法: catlass_parse.py <log|csv> [--csv] [--kernel NAME]")
    path = argv[0]
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if "--csv" in argv:
        kn = argv[argv.index("--kernel") + 1] if "--kernel" in argv else None
        print(parse_msprof_csv(text, kernel_name=kn))
    else:
        print(parse_raw_log(text))


if __name__ == "__main__":
    main(sys.argv[1:])
