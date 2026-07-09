"""P3 catlass adapter · 解析层（stdlib-only、纯解析不判定）——加固版（堵 15 条对抗门）。

只把真机 raw log / msprof CSV 解析成结构化字段，**pass/fail 一律不归这里**
（裁决交 validator / perf_compare，ADR 0007；catlass `Compare success.` 只是仓内 smoke 信号）。

本模块是 catlass 线 evidence 的**源头**，因此对「被喂伪造/污染输入」做了系统加固：

- parse_raw_log()    : 逐 case ok/FAIL、识崩溃/空日志/截断。**同一 cid 结果冲突/重复 → 记 error、不 last-wins**；
                       单行歧义（同含 success+failed）→ 记 error、不采信；哨兵**严格整行锚定**（`^…`），
                       拒行内嵌入；解析前剥离 ANSI/控制字符（含 CR）。
                       ⚠ **诚实边界**：只有在**同时**传入 `expected_case_ids`（精确全集）**与** `run_nonce`
                       （运行随机数、且 runner 真的打印了对应 `OPRUNWAY_RUN_NONCE` 行）时，本解析器才能防「整表伪造」；
                       两者**任一未传，本解析器无法防止有人贴一段自造的 `[OPRUNWAY_CASE …] Compare success` +
                       `OPRUNWAY_CATLASS_DONE` 文本冒充 N 个 case 全成功**——别把「解析没报错」当「跑对了」。
- parse_msprof_csv() : 按**列名**解析 msprof OpBasicInfo.csv（不写死列序）。kernel-only 口径**逐行**用
                       `Task Type` allowlist 显式过滤/拒绝（Host/H2D/D2H/Memcpy/AI_CPU/SDMA 等**不进统计**）；
                       e2e 判据列用**子串**判定（`H2D Duration(us)` 之类真实列名也拦得住）；重复列名 → error；
                       非法值（NaN/inf/≤0/字符串）**暴露并 fail-closed**；坏输入/超大字段/bytes **不崩、返回结构化 error**。
                       仅按 CONTENT 解析 TEXT，**不再**「看着像路径就自动读文件」（读文件走显式 `parse_msprof_path`）。
- profile_hit_gate() : 运行后 profile 命中门。要求**可信 kernel 名列 + expected kernel 精确/受控-regex 匹配**；
                       无名列或符号未预知 → `pending`/`block`，**绝不与 `hit=True` 并存**（不冒充已验证）。

raw log **只存 hash/path、不作判定依据**：本模块产的是「测到什么」，不是「过没过」。
"""
import argparse
import csv
import hashlib
import io
import math
import os
import re
import sys

# ─── 资源上限（DoS 防护，#14）───────────────────────────────────────────────
_MAX_LOG_BYTES = 20_000_000      # raw log 字节上限；超限截断 + 记 error
_MAX_CSV_BYTES = 20_000_000      # CSV 字节上限
_MAX_CSV_ROWS = 500_000          # CSV 数据行上限
_CSV_FIELD_LIMIT = 1_000_000     # 单字段字节上限（防超大引号字段撑爆内存）

# ─── raw log 哨兵（严格整行锚定 `^…`，拒行内嵌入，#3/#6）─────────────────────
# 我们的 runner 逐 case 前缀 `[OPRUNWAY_CASE <cid>] …`（同一行给出 Compare 结果）。
_CASE_LINE_RE = re.compile(r"^\s*\[OPRUNWAY_CASE\s+([A-Za-z0-9_.\-]+)\]\s*(.*)$")
# 行内是否命中 Compare 结果（在「已锚定的 case 行」的剩余文本里判）。
_OK_RE = re.compile(r"Compare\s+success", re.IGNORECASE)
_FAIL_RE = re.compile(r"Compare\s+failed", re.IGNORECASE)
# runner 收尾哨兵 —— 必须整行锚定在行首（拒 `blah OPRUNWAY_CATLASS_DONE` 冒充）。
_DONE_LINE_RE = re.compile(r"^\s*OPRUNWAY_CATLASS_DONE\b")
# 运行随机数哨兵（防整表伪造；需 runner 打印 `OPRUNWAY_RUN_NONCE <nonce>`）。
_NONCE_LINE_RE = re.compile(r"^\s*OPRUNWAY_RUN_NONCE\s+(\S+)\s*$")
# 崩溃信号（进程异常终止 → 日志可能截断、哨兵缺失，绝不能被当成「跑完」）；全文任意位置搜。
_CRASH_RES = [re.compile(p, re.IGNORECASE) for p in (
    r"segmentation fault", r"core dumped", r"\baborted\b", r"terminate called",
    r"double free", r"std::bad_alloc", r"ACL_ERROR", r"EE[0-9]{4}", r"aclrt\w+ failed")]

# ANSI CSI/OSC 转义序列；剥离后再做整行锚定，防「ANSI 夹在 Compare 与 success 之间破坏匹配」。
_ANSI_RE = re.compile(r"\x1b\[[0-9;:<=>?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
# 不可见控制字符（保留 \t=0x09、\n=0x0a；剥掉 \r=0x0d 等 → CR 无法夹带整行伪造哨兵）。
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


# ─── msprof CSV 列名字典（归一后匹配）────────────────────────────────────────
# 非 kernel-only 口径的判据**列名**（子串命中即拒；op_summary / --application e2e：含主机侧/搬运耗时）。
_E2E_HEADER_MARKERS = ("hostduration", "h2d", "d2h", "memcpy", "aclrtduration", "e2e")
# kernel-only 时长列（OpBasicInfo.csv，msprof op）。
_KERNEL_DUR_KEYS = ("taskduration(us)", "taskduration", "aicoreduration(us)", "aicoreduration")
# kernel/op 名列。
_NAME_KEYS = ("opname", "kernelname", "op_name", "name", "optype", "op_type")
# task-type 列（逐行 kernel-only 过滤的判据）。
_TASKTYPE_KEYS = ("tasktype", "task_type")
# op-type 列（辅助判据）。
_OPTYPE_KEYS = ("optype", "op_type")
# kernel-only 允许的 task-type 值（**allowlist**，归一后比对；不在表内的行一律 block、不进统计，#7）。
_KERNEL_TASK_TYPES = {
    "ai_core", "aicore", "aic", "ai_vector_core", "aivectorcore", "aiv",
    "mix_aic", "mix_aiv", "mixaic", "mixaiv", "mix", "vector", "cube", "ffts", "ffts+",
}
# 明确非 kernel 的名字/类型子串（belt-and-suspenders：即便 task-type 说是 AI_CORE，名字含这些也 block）。
_NONKERNEL_SUBSTR = ("memcpy", "h2d", "d2h", "sdma", "aicpu", "ai_cpu", "hccl",
                      "hcom", "notify", "communication", "pcie")


def _coerce_text(x):
    """入口类型归一：str 原样、bytes→utf-8(replace)、None→""、其余 → (None, 类型错误串)。

    返回 (text_or_None, err_or_None)。非 str/bytes/None → text=None、err 说明（不抛，#13）。
    """
    if x is None:
        return "", None
    if isinstance(x, str):
        return x, None
    if isinstance(x, (bytes, bytearray)):
        return bytes(x).decode("utf-8", "replace"), None
    return None, f"输入类型非法（期望 str/bytes，得 {type(x).__name__}）"


def _clean_text(text):
    """剥 ANSI 转义 + 不可见控制字符（保留 \\t\\n；\\r 一并剥掉）→ 供严格整行锚定。"""
    return _CTRL_RE.sub("", _ANSI_RE.sub("", text))


def sha256_of(text):
    """raw log/CSV 的内容指纹（入 artifact_manifest；raw 本身不作判定依据）。接受 str/bytes。"""
    if isinstance(text, (bytes, bytearray)):
        data = bytes(text)
    else:
        data = ("" if text is None else str(text)).encode("utf-8", "replace")
    return hashlib.sha256(data).hexdigest()


def parse_raw_log(text, expected_case_ids=None, run_nonce=None):
    """raw 跑测日志 → 结构化信号（**不判 pass/fail**）。抗缺行/空日志/崩溃截断/伪造。

    参数（均**可选**、默认 None，向后兼容）：
    - expected_case_ids：给定时要求日志里的 case **精确等于该全集、每 case 恰一次**；未知/重复/冲突/缺失 → error。
    - run_nonce        ：给定时要求日志里出现 `OPRUNWAY_RUN_NONCE <nonce>` 且与之相符；缺失/不符 → error。

    ⚠ **诚实边界（不得假装防住）**：只有在**同时**传入 `expected_case_ids` 与 `run_nonce`（且 runner 确实打印了
    对应 nonce 行）时，本函数才能抵御「整表伪造」。**两者任一为 None，本解析器无法防止**有人贴一段自造的
    `[OPRUNWAY_CASE cN] Compare success` + `OPRUNWAY_CATLASS_DONE` 文本冒充 N 个 case 全成功——
    这种情况下「解析没报错」**不等于**「真的跑对了」，上游必须靠 nonce/全集自行兜底。

    返回 dict，键（含既有键，向后兼容）：
      cases{cid:"ok"/"fail"}、success_count、failed_count、crashed、done、truncated、
      errors[]（结构化错误码，空=解析层未发现异常，**≠ 通过**）、conflicts[]（结果冲突/重复的 cid）、
      ambiguous_lines（同行同含 success+failed 的行号）、unknown_cids/missing_cids、
      expected_match（仅传 expected_case_ids 时有意义）、nonce_ok（仅传 run_nonce 时有意义）、
      case_seen[]、sentinel_total、raw_len、sha256。
    """
    text, type_err = _coerce_text(text)
    errors, conflicts, ambiguous_lines = [], [], []
    if type_err is not None:
        # bytes 已在 _coerce_text 里 decode；到这里只有真正非 str/bytes/None。
        return {"cases": {}, "success_count": 0, "failed_count": 0, "crashed": False,
                "done": False, "truncated": False, "errors": [type_err], "conflicts": [],
                "ambiguous_lines": [], "unknown_cids": [], "missing_cids": [],
                "expected_match": None, "nonce_ok": None, "case_seen": [],
                "sentinel_total": 0, "raw_len": 0, "sha256": sha256_of("")}

    raw_len = len(text)
    sha = sha256_of(text)

    # #14 DoS：字节超限 → 截断头部再解析，记 error（20MB+ 的 smoke 日志已属异常）。
    if raw_len > _MAX_LOG_BYTES:
        text = text[:_MAX_LOG_BYTES]
        errors.append("log_too_large_truncated")

    cleaned = _clean_text(text)

    # #5 空/纯空白 → 显式 error，绝不静默当「跑完无异常」。
    if not cleaned.strip():
        return {"cases": {}, "success_count": 0, "failed_count": 0, "crashed": False,
                "done": False, "truncated": False, "errors": errors + ["empty_log"],
                "conflicts": [], "ambiguous_lines": [], "unknown_cids": [], "missing_cids": [],
                "expected_match": None, "nonce_ok": None, "case_seen": [],
                "sentinel_total": 0, "raw_len": raw_len, "sha256": sha}

    crashed = any(rx.search(cleaned) for rx in _CRASH_RES)

    # 逐行严格锚定：每个 cid 的结果收进 list（**不 last-wins**），最后判冲突/重复（#1）。
    results = {}          # cid -> ["ok"/"fail", ...]（一 case 多结果=可疑）
    case_seen = []        # 出现过 [OPRUNWAY_CASE …] 的 cid（含 running 行）→ 判截断用（#4）
    success_count = failed_count = 0
    nonces = []
    done = False

    for lineno, line in enumerate(cleaned.split("\n"), 1):
        if _DONE_LINE_RE.match(line):
            done = True
        mn = _NONCE_LINE_RE.match(line)
        if mn:
            nonces.append(mn.group(1))
        m = _CASE_LINE_RE.match(line)
        if not m:
            continue
        cid, rest = m.group(1), m.group(2)
        if cid not in case_seen:
            case_seen.append(cid)
        has_ok = bool(_OK_RE.search(rest))
        has_fail = bool(_FAIL_RE.search(rest))
        # #2 单行歧义：同含 success+failed → 标歧义、记 error、**不采信该行**。
        if has_ok and has_fail:
            ambiguous_lines.append(lineno)
            errors.append(f"ambiguous_line:{lineno}")
            continue
        if has_ok:
            success_count += 1
            results.setdefault(cid, []).append("ok")
        elif has_fail:
            failed_count += 1
            results.setdefault(cid, []).append("fail")
        # 无结果（running/info 行）：只记 case_seen，不产结果。

    # #1 结算：单结果 → 采信；≥2 结果或冲突 → 记 conflict + error，**绝不进 cases**。
    cases = {}
    for cid, res in results.items():
        uniq = set(res)
        if len(res) >= 2 or len(uniq) >= 2:
            conflicts.append(cid)
            errors.append(f"conflicting_result:{cid}:{res}")
            continue
        cases[cid] = res[0]

    # #4 截断：出现过 case 段却无收尾 DONE → 截断/崩溃（诚实：宁可标可疑，不当成功）。
    truncated = False
    if case_seen and not done:
        truncated = True
        crashed = True
        errors.append("truncated_no_done")

    # #3 全集校验（仅在给定 expected_case_ids 时）。
    unknown_cids, missing_cids, expected_match = [], [], None
    if expected_case_ids is not None:
        expected = set(map(str, expected_case_ids))
        observed = set(results.keys())            # 出现过结果的 cid（含冲突的）
        clean_ok = set(cases.keys())              # 唯一结果、可采信的 cid
        unknown_cids = sorted(observed - expected)
        missing_cids = sorted(expected - clean_ok)
        if unknown_cids:
            errors.append(f"unknown_case_ids:{unknown_cids}")
        if missing_cids:
            errors.append(f"missing_case_ids:{missing_cids}")
        expected_match = (not unknown_cids and not missing_cids and not conflicts)

    # #3 nonce 校验（仅在给定 run_nonce 时）。
    nonce_ok = None
    if run_nonce is not None:
        want = str(run_nonce)
        nonce_ok = bool(nonces) and set(nonces) == {want}
        if not nonce_ok:
            errors.append(f"run_nonce_mismatch:want={want}:got={sorted(set(nonces))}")

    return {"cases": cases, "success_count": success_count, "failed_count": failed_count,
            "crashed": crashed, "done": done, "truncated": truncated,
            "errors": errors, "conflicts": sorted(conflicts),
            "ambiguous_lines": ambiguous_lines,
            "unknown_cids": unknown_cids, "missing_cids": missing_cids,
            "expected_match": expected_match, "nonce_ok": nonce_ok,
            "case_seen": list(case_seen),
            "sentinel_total": success_count + failed_count,
            "raw_len": raw_len, "sha256": sha}


def _norm(s):
    """列名归一：小写 + 去所有空白（保留下划线），容忍 `Task Duration(us)` / `task duration (us)`。"""
    return re.sub(r"\s+", "", (s or "").strip().lower())


def _parse_csv_rows(text):
    """CSV 文本 → (header, dict_rows, err)。坏解析/超大字段 **不崩**、以 err 返回（#13/#14）。"""
    try:
        old = csv.field_size_limit(_CSV_FIELD_LIMIT)
    except (OverflowError, ValueError):
        old = None
    try:
        reader = csv.reader(io.StringIO(text))
        raw_rows = []
        for r in reader:
            if len(raw_rows) > _MAX_CSV_ROWS:
                raw_rows = None  # 触发下方超限分支
                break
            if r and any((c or "").strip() for c in r):
                raw_rows.append(r)
    except (csv.Error, ValueError) as e:
        return [], [], f"CSV 解析失败：{e}"
    finally:
        if old is not None:
            try:
                csv.field_size_limit(old)
            except (OverflowError, ValueError):
                pass
    if raw_rows is None:
        return [], [], f"CSV 行数超上限（>{_MAX_CSV_ROWS}）"
    if not raw_rows:
        return [], [], None
    header = raw_rows[0]
    dict_rows = []
    for r in raw_rows[1:]:
        dict_rows.append({i: (r[i] if i < len(r) else "") for i in range(len(header))})
    return header, dict_rows, None


def _find_col(header, keys):
    """在 header 里按归一名找列，返回**列索引**（找不到 None）。"""
    for i, h in enumerate(header):
        if _norm(h) in keys:
            return i
    return None


def _is_kernel_row(row, header, tt_idx, name_idx, ot_idx):
    """逐行判「是否 kernel-only」（#7）：task-type 在 allowlist 内、且名字/类型不含明确非 kernel 子串。

    返回 (is_kernel, reason)。tt_idx=None（无 task-type 列）→ (True, "no_task_type_col")：
    无法逐行判、退回「靠表头 e2e 拒 + 名字过滤」，reason 标注弱保证。
    """
    # 名字/optype 命中明确非 kernel 子串 → 直接 block（即便 task-type 声称 AI_CORE）。
    for idx in (name_idx, ot_idx):
        if idx is not None:
            v = _norm(row.get(idx, ""))
            if any(sub in v for sub in _NONKERNEL_SUBSTR):
                return False, f"nonkernel_name:{row.get(idx, '')}"
    if tt_idx is None:
        return True, "no_task_type_col"
    tt = _norm(row.get(tt_idx, ""))
    if tt in _KERNEL_TASK_TYPES:
        return True, "allow"
    return False, f"blocked_task_type:{row.get(tt_idx, '')}"


def _blank_result(reason, scope=None, **extra):
    base = {"ok": False, "reason": reason, "scope": scope, "count": 0,
            "median_us": None, "p90_us": None, "min_us": None,
            "durations": [], "kernels": [], "blocked_rows": 0,
            "invalid_count": 0, "invalid_rows": []}
    base.update(extra)
    return base


def parse_msprof_text(text, require_scope="kernel_only", kernel_name=None, kernel_regex=None):
    """按**列名**解析 msprof OpBasicInfo.csv **文本**（不写死列序）——kernel-only 逐行过滤 + 非法值暴露。

    - require_scope=="kernel_only"：① 表头含 e2e 判据列（**子串**：Host/H2D/D2H/Memcpy/…）→ 整表拒；
      ② 逐行按 `Task Type` allowlist 过滤，Host/H2D/D2H/Memcpy/AI_CPU/SDMA 等行 **block、不进统计**（`blocked_rows`）。
    - kernel_name 给定：**精确等值**匹配（非 startswith，#9）；kernel_regex 给定：`re.fullmatch` 受控匹配。
    - 重复列名（归一后）→ error（#10）；候选行含 NaN/inf/≤0/字符串 → `ok=False` 且 `invalid_rows` 暴露污染（#11）。
    - 坏 header / 空行 / 缺时长列 / 坏 CSV / 超大字段 → `ok=False + reason`，**绝不崩**。

    返回 {ok, reason, scope, count, median_us, p90_us, min_us, durations, kernels,
          blocked_rows, invalid_count, invalid_rows}。
    """
    text, type_err = _coerce_text(text)
    if type_err is not None:
        return _blank_result(type_err)

    if len(text) > _MAX_CSV_BYTES:
        return _blank_result(f"CSV 超过字节上限（>{_MAX_CSV_BYTES}）")

    text = _clean_text(text)
    header, rows, err = _parse_csv_rows(text)
    if err is not None:
        return _blank_result(err)
    if not header:
        return _blank_result("空 CSV 或无表头")

    # #10 重复列名（归一后）→ error（防伪造第二个 Task Duration(us) 改取值）。
    norm_names = [_norm(h) for h in header]
    dups = sorted({n for n in norm_names if n and norm_names.count(n) > 1})
    if dups:
        return _blank_result(f"表头存在重复列名 {dups}（拒歧义取值）")

    norm_set = set(norm_names)
    if require_scope == "kernel_only":
        # #7a e2e 列判定改**子串**（`H2D Duration(us)`→`h2dduration(us)` 也拦得住）。
        hit_e2e = sorted({m for m in _E2E_HEADER_MARKERS
                          if any(m in n for n in norm_set)})
        if hit_e2e:
            return _blank_result(
                f"非 kernel-only 口径：CSV 含 e2e 判据列 {hit_e2e}（拒混 H2D/D2H，ADR 0006）",
                scope="e2e_suspected")

    dur_idx = _find_col(header, _KERNEL_DUR_KEYS)
    if dur_idx is None:
        return _blank_result(f"缺时长列 Task Duration(us)（表头={header}）")
    name_idx = _find_col(header, _NAME_KEYS)
    tt_idx = _find_col(header, _TASKTYPE_KEYS)
    ot_idx = _find_col(header, _OPTYPE_KEYS)

    durations, kernels, invalid_rows = [], [], []
    blocked_rows = 0
    for r in rows:
        nm = (r.get(name_idx, "") if name_idx is not None else "").strip()
        # #9 kernel 过滤：精确等值 / 受控 regex（拒 startswith 误命中）。
        if kernel_regex is not None:
            if not re.fullmatch(kernel_regex, nm):
                continue
        elif kernel_name is not None:
            if nm != kernel_name:
                continue
        # #7b 逐行 kernel-only 过滤：非 allowlist 的 task-type / 非 kernel 名字 → block。
        if require_scope == "kernel_only":
            is_k, _why = _is_kernel_row(r, header, tt_idx, name_idx, ot_idx)
            if not is_k:
                blocked_rows += 1
                continue
        raw = (r.get(dur_idx, "") or "").strip().replace(",", "")
        if raw == "":
            continue  # 空 cell = 结构性缺口，跳过（不算污染）
        # #11 非法值：present 但非数 / NaN / inf / ≤0 → 记 invalid、暴露污染。
        try:
            v = float(raw)
        except (ValueError, TypeError):
            invalid_rows.append({"name": nm, "value": raw, "why": "non_numeric"})
            continue
        if math.isnan(v):
            invalid_rows.append({"name": nm, "value": raw, "why": "nan"})
            continue
        if math.isinf(v):
            invalid_rows.append({"name": nm, "value": raw, "why": "inf"})
            continue
        if v <= 0:
            invalid_rows.append({"name": nm, "value": raw, "why": "non_positive"})
            continue
        durations.append(v)
        if nm:
            kernels.append(nm)

    invalid_count = len(invalid_rows)
    # #11 fail-closed：候选行里出现非法值 → 判 ok=False、reason 暴露（污染的 profile 不可信）。
    if invalid_count:
        return _blank_result(
            f"候选行含非法时长值 {invalid_count} 处（NaN/inf/≤0/非数）→ profile 不可信",
            scope="kernel_only", blocked_rows=blocked_rows,
            invalid_count=invalid_count, invalid_rows=invalid_rows,
            kernels=sorted(set(kernels)))

    if not durations:
        reason = "无有效时长行（kernel_name 不匹配 / 全被 kernel-only 过滤 / 空表）"
        if blocked_rows:
            reason += f"；blocked_rows={blocked_rows}"
        return _blank_result(reason, scope="kernel_only", blocked_rows=blocked_rows,
                             kernels=sorted(set(kernels)))

    s = sorted(durations)
    n = len(s)
    median = s[n // 2] if n % 2 else round((s[n // 2 - 1] + s[n // 2]) / 2, 6)
    p90 = s[min(n - 1, int(round(0.9 * (n - 1))))]
    return {"ok": True, "reason": "", "scope": "kernel_only", "count": n,
            "median_us": round(median, 6), "p90_us": round(p90, 6),
            "min_us": round(s[0], 6), "durations": s, "kernels": sorted(set(kernels)),
            "blocked_rows": blocked_rows, "invalid_count": 0, "invalid_rows": []}


def parse_msprof_path(path, root=None, require_scope="kernel_only",
                      kernel_name=None, kernel_regex=None):
    """**显式**从磁盘读 msprof CSV 再解析（与 text 入口分离，#12）。

    - 只在这里读文件；`parse_msprof_text` **绝不**「看着像路径就自动读」。
    - root 给定：解析后的绝对路径必须落在 realpath(root) 之内，否则拒（防目录穿越/任意读）。
    - 读文件错误**保留真实原因**（不静默折叠成空 CSV）。
    """
    if not isinstance(path, str) or not path:
        return _blank_result("path 必须为非空字符串")
    real = os.path.realpath(path)
    if root is not None:
        root_real = os.path.realpath(root)
        if not (real == root_real or real.startswith(root_real + os.sep)):
            return _blank_result(f"路径越界：{path} 不在 root={root} 之内（拒任意本地文件读取）")
    try:
        size = os.path.getsize(real)
    except OSError as e:
        return _blank_result(f"读文件失败：{e}")
    if size > _MAX_CSV_BYTES:
        return _blank_result(f"CSV 文件过大（{size}B > {_MAX_CSV_BYTES}）")
    try:
        with open(real, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as e:
        return _blank_result(f"读文件失败：{e}")
    return parse_msprof_text(text, require_scope=require_scope,
                             kernel_name=kernel_name, kernel_regex=kernel_regex)


def parse_msprof_csv(text, require_scope="kernel_only", kernel_name=None, kernel_regex=None):
    """向后兼容入口：把入参当 **CSV 文本** 解析（**不**自动读路径，#12）。

    历史签名保留（`parse_msprof_csv(text, require_scope=…, kernel_name=…)`）；新增可选 `kernel_regex`。
    需要读磁盘文件请显式用 `parse_msprof_path(path, root=…)`。
    """
    return parse_msprof_text(text, require_scope=require_scope,
                             kernel_name=kernel_name, kernel_regex=kernel_regex)


def profile_hit_gate(text, expected_kernel=None, kernel_regex=None):
    """运行后 profile 命中门（加固版，#8/#9）：解析真 CSV、断言 expected kernel 确实以 kernel-only 口径跑过。

    **不变量**：`hit=True` ⟺ `pending=False`（绝不「一边说命中、一边说待确认」冒充已验证）。
    规则：
    - CSV 解析失败 → hit=False、pending=False、reason。
    - **无可信 kernel 名列**（拿不到符号名）→ hit=False、**pending=True**、reason 说明「无法归属命中」（#8：
      光有正数时长、没名列，**不得** hit=True）。
    - 有名列、给了 expected_kernel/kernel_regex → **精确/受控** 匹配：命中 → hit=True、pending=False；否则 hit=False。
    - 有名列但 expected_kernel 未预知（None）→ 回填实测名、**pending=True、hit=False**（诚实：符号未验，真机才落实）。

    返回 {hit, observed_kernels, matched, reason, pending, blocked}。
    """
    parsed = parse_msprof_text(text, require_scope="kernel_only", kernel_name=None)
    if not parsed["ok"]:
        return {"hit": False, "observed_kernels": parsed.get("kernels", []),
                "matched": None, "reason": parsed["reason"], "pending": False,
                "blocked": True}
    observed = parsed["kernels"]
    if not observed:
        # 有时长行但拿不到 kernel 名（无名列）→ 无法归属，命中不成立、标 pending（#8）。
        return {"hit": False, "observed_kernels": [], "matched": None,
                "reason": "有时长行但无可信 kernel 名列 → 无法归属命中（拒仅凭正数时长判 hit）",
                "pending": True, "blocked": True}
    if expected_kernel is None and kernel_regex is None:
        # 符号未预知 → 回填实测名，但**不冒充已验证**：hit=False、pending=True。
        return {"hit": False, "observed_kernels": observed, "matched": None,
                "reason": "符号未预知：已回填实测 kernel 名，待真机确认（pending，未判 hit）",
                "pending": True, "blocked": False}
    if kernel_regex is not None:
        matched = [k for k in observed if re.fullmatch(kernel_regex, k)]
        label = f"regex={kernel_regex}"
    else:
        matched = [k for k in observed if k == expected_kernel]  # #9 精确等值
        label = expected_kernel
    return {"hit": bool(matched), "observed_kernels": observed,
            "matched": matched or None,
            "reason": ("命中" if matched else f"未命中 {label}（实测 {observed}）"),
            "pending": False, "blocked": not matched}


def main(argv):
    """CLI：argparse + 结构化中文错误 + 非零返回（#15）。"""
    p = argparse.ArgumentParser(
        prog="catlass_parse.py",
        description="解析 catlass 真机 raw log 或 msprof CSV（纯解析、不判 pass/fail）。")
    p.add_argument("path", help="raw log 或 CSV 文件路径")
    p.add_argument("--csv", action="store_true", help="按 msprof CSV 解析（默认按 raw log）")
    p.add_argument("--kernel", default=None, help="CSV：只取该 kernel（精确等值）")
    p.add_argument("--expected-kernel", default=None, help="CSV：profile 命中门 expected kernel")
    p.add_argument("--root", default=None, help="CSV：限定读文件的根目录（防任意读）")
    try:
        args = p.parse_args(argv)
    except SystemExit as e:  # argparse 缺参/错参 → 已打印用法，透传非零码（修 IndexError）
        raise SystemExit(e.code if isinstance(e.code, int) else 2)

    if args.csv:
        res = parse_msprof_path(args.path, root=args.root, kernel_name=args.kernel)
        print(res)
        if args.expected_kernel is not None:
            try:
                with open(os.path.realpath(args.path), encoding="utf-8", errors="replace") as f:
                    print(profile_hit_gate(f.read(), expected_kernel=args.expected_kernel))
            except OSError as e:
                print(f"错误：读文件失败：{e}", file=sys.stderr)
                return 2
        return 0 if res.get("ok") else 1

    try:
        with open(args.path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as e:
        print(f"错误：读文件失败：{e}", file=sys.stderr)
        return 2
    res = parse_raw_log(text)
    print(res)
    return 1 if res.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
