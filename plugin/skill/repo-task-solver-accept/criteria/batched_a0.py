# -*- coding: utf-8 -*-
"""batched_a0.py —— A0 抽样通路的判定侧驱动（HT-2；消费 repo-task-solver-case-gen
的 A0 批量条目）。

A0 语义（HT-2 卡 + case-gen 的 gen_data_cholesky.build_batched_contents /
derive_sample_map / gen_batched_case）：每 batch 用例只造 k=min(5,batch) 个代表
内容矩阵，sample_map 把全部 batch 个槽位映射到 k 个内容（每内容首槽即 rep_slot）；
被测方对展开后的整批输入出整批输出。判定按卡面口径两层先后：

1. 余槽 bit-wise 一致性（check_consistency）：同一内容的全部槽位，被测 out32 与
   rep_slot 输出逐位相等（tobytes 级比对，NaN 位型不同也算失配）；infoArray
   （info_kind="array"）逐槽相等；标量 info（potrsBatched 族，仅报参数错）不逐槽。
   失配 → case 数值 FAIL（不是 error verdict——DUT 对同输入出不同输出本身即缺陷，
   且「代表槽代言全批」的 A0 前提被破坏）；槽位号证据落 first_fail_index（最小
   失配槽位）与 diagnostics.a0_mismatches（逐条 content_idx/slot/ref_slot/field），
   error 恒 None（仓库约定 error 非空 = 证据问题不判精度，此处是真实缺陷）；
2. 代表槽逐内容精度判定（judge_representatives）：按 rep_slot 切出 k 个代表输出，
   与 k 个内容数组配成 batch=k 子批，复用 batched_parallel.judge_parallel
   （verdict 唯一裁决实现零改动——AGENTS.md §2 机械门纪律；s2-A1 起为一段式
   残差判定）。子批 verdict 的序号域是内容下标 0..k-1（first_fail_index/
   worst_index 同），不是全批槽位号。

ratio_cpu 消费口径：A0 条目的 ratio_cpu 是 k 值列表，prep_failed 内容记 null——
此处转 NaN 进子批（float64 (k,)）。s2-A1 一段式下每个内容必进阈值公式，NaN 基线
的内容由有限性校验（thresholds._check_ratio_cpu）抛错 → 该内容 error verdict
（「不可裁/证据问题」，不判精度），与全量通路「残差基线不可用」同向 fail-closed；
prep 计数由 index 的 ratio_cpu_prep_failed 披露。ratio_cpu_status 原样透传：
全 prep_failed 时子批走「残差基线不可用」error 语义，与全量通路同口径。

结构问题（out32 非三维、batch 声明不符、info 分型不符、缺 sample_map 等）沿用
verdict 约定出 error verdict（fail_count=None），上层不得当精度 FAIL 上报。

HT-9 info 契约条目（case_purpose=="info"，B3）：每批量算子 1 个混合 case——
*potrfBatched 族 1~2 个代表内容构造非正定（k_expected 为逐内容 k 值列表，正定
内容记 0），*potrsBatched 族标量 info 仅参数错（k_expected=-1）。judge_a0 对该类
条目分流到 _judge_a0_info_inner：只比 info，不进一致性/残差两层（非正定内容的
out32 无意义；info 契约与数值精度各出独立结论，HT-12）——infoArray 分型把
k_expected 经 sample_map 展开到全批槽位逐槽核对（infoArray 逐矩阵独立写入是全批
语义），标量分型直接比对。结构问题同款 error verdict（fail-closed）。

无 sample_map 的旧包不进本模块：消费方（stream_check / accept_run / render_verify）
按条目 ``materialize == "gen" and "sample_map" in entry`` 分流，旧包走全遍历
兼容分支。
"""

import numpy as np

try:  # criteria 作为包被导入时
    from . import batched_parallel, verdict
except ImportError:  # criteria 目录直接挂 sys.path 时（scripts 消费方）
    import batched_parallel
    import verdict


class A0StructureError(ValueError):
    """A0 case/被测输出结构问题（judge_a0 收敛为 error verdict）。"""


def check_consistency(card, dut_out, sample_map):
    """第 1 层：同内容槽位被测输出逐位比对（参照 = rep_slot 输出，位相等按内容
    传递）。out32 逐槽 tobytes 比对；infoArray（card.info_kind=="array"）逐槽取整
    比对；标量 info 跳过（potrs 族 info 仅报参数错，不逐矩阵，S3 spec §4）。

    返回失配清单 [{"content_idx", "slot", "ref_slot", "field"}]（slot 为全批槽位
    号），全一致返回 []。out32 非三维抛 A0StructureError（由 judge_a0 收敛为
    error verdict）。"""
    out32 = np.asarray(dut_out["out32"])
    if out32.ndim != 3:
        raise A0StructureError(f"被测 out32 应为 (batch, n, cols) 三维，得到 shape={out32.shape}")
    info_arr = None
    if getattr(card, "info_kind", "") == "array" and dut_out.get("info") is not None:
        info_arr = np.asarray(dut_out["info"])
    bad = []
    for e in sample_map:
        c, ref = int(e["content_idx"]), int(e["rep_slot"])
        for s in e["slots"]:
            s = int(s)
            if s == ref:
                continue
            if out32[s].tobytes() != out32[ref].tobytes():
                bad.append({"content_idx": c, "slot": s, "ref_slot": ref, "field": "out32"})
            if info_arr is not None and int(info_arr[s]) != int(info_arr[ref]):
                bad.append({"content_idx": c, "slot": s, "ref_slot": ref, "field": "info"})
    return bad


def judge_representatives(card, contents, sample_map, dut_out, ratio_cpu,
                          ratio_cpu_status, ratio_cpu_mean=None, extra=None, jobs=1):
    """第 2 层：rep_slot 切子批，复用 batched_parallel.judge_parallel 出代表槽
    逐内容 verdict（一段式残差判定原样，不加开关——HT-2 卡面口径）。

    - contents: A0 内容数组 {A64,A32[,B64,B32],golden64,golden32}，批维 k（现场
      重造产物，由消费方保证与包/种子一致——本模块只按形状校验）；
    - sample_map: 条目固化的槽位映射（k 项，顺序即内容下标）；
    - ratio_cpu: k 值列表（prep_failed 记 None，此处转 NaN）；
    - extra: 需透传子批的标量元数据（如 uplo），原样并入子批 case_arrays。
    子批 verdict 的序号域是内容下标 0..k-1。"""
    k = len(sample_map)
    reps = [int(e["rep_slot"]) for e in sample_map]
    if sorted(reps) != reps or len(set(reps)) != k:
        raise A0StructureError(f"sample_map 的 rep_slot 应升序且互异，得到 {reps}")
    for e in sample_map:
        if int(e["rep_slot"]) not in [int(x) for x in e["slots"]]:
            raise A0StructureError(
                f"内容 {e['content_idx']} 的槽位映射非法（rep_slot 不在其槽位集内）")

    sub_case = {}
    for key in verdict._BATCH_SLICED_KEYS:
        if key in contents:
            arr = np.asarray(contents[key])
            if arr.ndim != 3 or arr.shape[0] != k:
                raise A0StructureError(
                    f"内容数组 {key} 首维应为 k={k}，得到 shape={arr.shape}")
            sub_case[key] = arr
    if extra:
        sub_case.update(extra)
    sub_case["batch"] = k
    rarr = np.asarray([np.nan if v is None else float(v) for v in ratio_cpu],
                      dtype=np.float64)
    if rarr.shape != (k,):
        raise A0StructureError(f"ratio_cpu 应为 k={k} 值列表（逐内容），得到 shape={rarr.shape}")
    sub_case["ratio_cpu"] = rarr
    sub_case["ratio_cpu_status"] = ratio_cpu_status
    if ratio_cpu_mean is not None:
        sub_case["ratio_cpu_mean"] = float(ratio_cpu_mean)

    out32 = np.asarray(dut_out["out32"])
    for r in reps:
        if not 0 <= r < out32.shape[0]:
            raise A0StructureError(f"rep_slot {r} 超出被测批维 {out32.shape[0]}")
    sub_dut = {"out32": out32[reps], "status": dut_out.get("status")}
    if getattr(card, "info_kind", "") == "array":
        sub_dut["info"] = np.asarray(dut_out["info"])[reps]
    else:
        sub_dut["info"] = dut_out.get("info")
    return batched_parallel.judge_parallel(card, sub_case, sub_dut, jobs=jobs)


def _a0_error(msg, batch=None):
    """结构问题 error verdict（schema 同 verdict 批量 error verdict，fail_count=None）。"""
    return verdict._batched_error_verdict(msg, batch)


def _judge_a0_info_inner(card, case_arrays, dut_out):
    """HT-9（B3）：批量 info 契约条目判定——只比 info，不进一致性/残差两层
    （非正定内容的 out32 无意义，B2 同款口径；info 契约与数值精度各出独立结论，
    HT-12）。

    - infoArray 分型（potrfBatched 族）：k_expected 为 k 值列表（逐代表内容，正定
      内容记 0），经 sample_map 展开到全批槽位逐槽核对——infoArray 逐矩阵独立写入
      的契约即全批语义；任一槽失配 → 数值 FAIL（fail_count=失配内容数，
      first_fail_index=最小失配槽位，diagnostics.info_mismatches 逐槽证据）；
    - 标量分型（potrsBatched 族）：k_expected 为标量（-1，仅参数错），直接比对。

    fail-closed（error verdict）：dut 非 dict、status 非 ok、out32 非三维、info
    分型不符、batch 声明不符、缺 sample_map、k_expected 缺失或形态不符。"""
    if not isinstance(dut_out, dict):
        return _a0_error(f"dut_out 必须是 dict，得到 {type(dut_out).__name__}")
    if dut_out.get("status") != "ok":
        return _a0_error(
            f"被测状态非 ok: {dut_out.get('status')!r}（prep/接口层失败不归目标）")
    out32 = dut_out.get("out32")
    if out32 is None:
        return _a0_error("dut_out 缺 out32")
    out32 = np.asarray(out32)
    if out32.ndim != 3:
        return _a0_error(
            f"批量卡 out32 应为 (batch, n, cols) 三维（batch=1 不得标量化），"
            f"得到 shape={out32.shape}")
    batch = int(out32.shape[0])
    try:  # 分型预检（shape/dtype/标量）；标量非 0 放行——info 契约条目本身要核对
        verdict._check_batched_info(card, dut_out.get("info"), batch,
                                    allow_nonzero=True)
    except ValueError as exc:
        return _a0_error(str(exc), batch)
    declared = case_arrays.get("batch")
    if declared is not None:
        try:
            declared = int(declared)
        except (TypeError, ValueError):
            return _a0_error(f"case_arrays.batch 不可解析: {declared!r}", batch)
        if declared != batch:
            return _a0_error(
                f"canonical batch={declared} 与被测 out32 首维 {batch} 不一致", batch)
    sample_map = case_arrays.get("sample_map")
    if not sample_map:
        return _a0_error("A0 条目缺 sample_map（重生成协议不符）", batch)

    def _verdict(numeric, fail_count, first_fail, diagnostics):
        return {
            "batch": batch,
            "fail_count": fail_count,
            "first_fail_index": first_fail,
            "worst_index": None,
            "worst": None,
            "diagnostics": diagnostics,
            "numeric": numeric,
            "formal": "PENDING_RULING",
            "flags": [],
            "error": None,
        }

    if getattr(card, "info_kind", "") == "array":
        k_expected = case_arrays.get("k_expected")
        if not isinstance(k_expected, (list, tuple)):
            return _a0_error(
                f"info 契约条目 k_expected 应为逐内容 k 值列表，得到 {k_expected!r}",
                batch)
        k = len(sample_map)
        if len(k_expected) != k:
            return _a0_error(
                f"k_expected 应为 k={k} 值列表（逐代表内容），得到 {len(k_expected)} 值",
                batch)
        try:
            k_exp = [int(x) for x in k_expected]
        except (TypeError, ValueError):
            return _a0_error(f"k_expected 含不可取整值: {k_expected!r}", batch)
        info_arr = np.asarray(dut_out.get("info"))
        mismatches = []
        for e in sample_map:
            exp_c = k_exp[int(e["content_idx"])]
            for s in e["slots"]:
                actual = int(info_arr[int(s)])
                if actual != exp_c:
                    mismatches.append({"slot": int(s),
                                       "content_idx": int(e["content_idx"]),
                                       "expected": exp_c, "actual": actual})
        if mismatches:
            return _verdict(
                "FAIL", len({m["content_idx"] for m in mismatches}),
                min(m["slot"] for m in mismatches),
                {"info_contract": True, "a0_contents": k,
                 "info_mismatches": mismatches})
        return _verdict(
            "PASS", 0, None,
            {"info_contract": True, "a0_contents": k,
             "non_posdef_contents": [c for c, kk in enumerate(k_exp) if kk > 0]})
    # 标量分型（potrsBatched 族）：仅参数错路径，k_expected=-1
    k_expected = case_arrays.get("k_expected")
    try:
        k_expected = int(k_expected)
    except (TypeError, ValueError):
        return _a0_error(
            f"info 契约条目 k_expected 应为可取整标量（-1），得到 {k_expected!r}",
            batch)
    actual = int(dut_out.get("info"))
    ok = actual == k_expected
    return _verdict(
        "PASS" if ok else "FAIL", 0 if ok else 1, None if ok else 0,
        {"info_contract": True, "expected": k_expected, "actual": actual})


def judge_a0(card, case_arrays, dut_out, jobs=1):
    """A0 批量条目顶层判定：① info 契约条目分流（HT-9，case_purpose=="info" →
    _judge_a0_info_inner 只比 info）；② 一致性核对（失配 → 数值 FAIL 带槽位号证据）；
    ③ 代表槽逐内容判定（复用一段式残差判定原样）。

    case_arrays 为 A0 条目（canonical case + gen_batched_case 增量合并后的 dict）：
    _BATCH_SLICED_KEYS 数组批维 k、ratio_cpu 为 k 值列表、batch 声明为 canonical
    批维（与被测 out32 首维核对）、sample_map 为 k 项槽位映射。dut_out 为被测
    全批输出 {"out32", "info", "status"}。返回 verdict dict（schema 同批量卡：
    一致性失配时 fail_count=失配内容数、first_fail_index=最小失配槽位、
    diagnostics.a0_mismatches 逐条证据；否则为 batch=k 子批 verdict 原样）。
    非批量卡调用抛 ValueError（judge_a0 只服务批量条目）。"""
    if not getattr(card, "batched", False):
        raise ValueError("judge_a0 只服务批量卡（card.batched=True）")
    if case_arrays.get("case_purpose") == "info":      # HT-9：info 契约独立支路
        return _judge_a0_info_inner(card, case_arrays, dut_out)
    sample_map = case_arrays.get("sample_map")
    if not sample_map:
        return _a0_error("A0 条目缺 sample_map（旧包应走全遍历兼容分支，不应进入 judge_a0）")

    if not isinstance(dut_out, dict):
        return _a0_error(f"dut_out 必须是 dict，得到 {type(dut_out).__name__}")
    if dut_out.get("status") != "ok":
        return _a0_error(
            f"被测状态非 ok: status={dut_out.get('status')!r}"
            "（prep/接口层失败不归目标，不判精度）")
    out32 = dut_out.get("out32")
    if out32 is None:
        return _a0_error("dut_out 缺 out32")
    out32 = np.asarray(out32)
    if out32.ndim != 3:
        return _a0_error(
            f"批量卡 out32 应为 (batch, n, cols) 三维（batch=1 不得标量化，"
            f"S3 spec §4），得到 shape={out32.shape}")
    batch = int(out32.shape[0])
    try:  # info 分型预检（shape/dtype/标量），细节口径与 verdict 批量校验同一实现
        verdict._check_batched_info(card, dut_out.get("info"), batch)
    except ValueError as exc:
        return _a0_error(str(exc), batch)

    declared = case_arrays.get("batch")
    if declared is not None:
        try:
            declared = int(declared)
        except (TypeError, ValueError):
            return _a0_error(f"case_arrays.batch 不可解析: {declared!r}", batch)
        if declared != batch:
            return _a0_error(
                f"canonical batch={declared} 与被测 out32 首维 {batch} 不一致", batch)

    k = len(sample_map)
    try:
        mismatches = check_consistency(card, dut_out, sample_map)
    except A0StructureError as exc:
        return _a0_error(str(exc), batch)
    if mismatches:
        # 卡面口径「失配 FAIL 报槽位号」：数值 FAIL（非 error verdict），槽位号
        # 证据落 first_fail_index 与 diagnostics.a0_mismatches（error 恒 None）。
        return {
            "batch": batch,
            "fail_count": len({m["content_idx"] for m in mismatches}),
            "first_fail_index": min(m["slot"] for m in mismatches),
            "worst_index": None,
            "worst": None,
            "diagnostics": {"a0_contents": k, "a0_mismatches": mismatches},
            "numeric": "FAIL",
            "formal": "PENDING_RULING",
            "flags": [],
            "error": None,
        }

    try:
        return judge_representatives(
            card, case_arrays, sample_map, dut_out,
            ratio_cpu=case_arrays.get("ratio_cpu"),
            ratio_cpu_status=case_arrays.get("ratio_cpu_status"),
            ratio_cpu_mean=case_arrays.get("ratio_cpu_mean"),
            extra={key: case_arrays[key] for key in ("uplo",) if key in case_arrays},
            jobs=jobs)
    except A0StructureError as exc:
        return _a0_error(str(exc), batch)
    except IndexError:
        return _a0_error("rep_slot/infoArray 下标超出被测批维（sample_map 与输出不符）", batch)
