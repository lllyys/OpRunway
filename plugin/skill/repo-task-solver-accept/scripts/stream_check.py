#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stream_check.py —— 流式判定驱动（数据形态裁定 2026-09-23：不落盘，生成即测）。

逐 case 在同进程内完成：现场生成输入与 golden → 现场算 ratio_cpu（CPU 同精度链）→
产生被测输出（当前为 sim 通路；真实 NPU 被测经适配器接入，见 DUT 适配点注释）→
criteria.judge 判定 → 只留判定记录，数组即弃。全程不写数组文件（--dump 例外，调试用）。

确定性与追溯（supplement 23i）：显式 seed + 报告记录环境版本 + 生成与判定同进程闭环。
判定语义与包通路完全同源：build_case_arrays / run_chain / perturb_out32 / judge 均为
既有模块的同一实现，无平行副本。

S3 批量 A0 抽样通路（S3 spec §4/§6 + HT-2，D3 卡 A0 化）：批量四算子（*Batched）
每 case 只造 k=min(5,batch) 个代表内容矩阵（gen_data_cholesky.build_batched_contents，
内容/摆放/填充三条流全部从 case seed 派生），ratio_cpu 逐内容计算（k 值列表，
prep_failed 记 None）；全批输入经 expand_sampled_rows 现场展开喂 sim DUT（DUT 适配
点契约不变：「输入数组 → {out32, info, status}」）；判定经 criteria/batched_a0.
judge_a0 两层先后——先余槽 bit-wise 一致性（同内容槽位 out32+info 逐位比对，失配
数值 FAIL 报槽位号），后 rep_slot 逐内容一段式残差判定（batched_parallel.
judge_parallel 复用，--jobs>1 结果与串行逐位相同）。--perturb-index 透传逐矩阵扰动，序号域为
全批槽位号——命中非 rep 槽即触发一致性失配负例（报告 a0_mismatches 带槽位号证据）。
记录按批量 schema 落 batch/fail_count/first_fail_index/worst_index 与最差矩阵摘要、
mode=a0 与 sampled_contents，ratio_cpu 落 k 值摘要（prep_failed 不入统计）。

HT-8 info 契约用例：canonical 在册 case 之外，每单算子另派生 3 个 info 变体
（gen_data_cholesky.derive_info_cases，与 gen 侧同一选择规则同一随机流）；本工具
在 canonical case 之后追加跑这些 info 用例——现场构造（build_info_arrays，构造性
自检 fail-closed）、无 ratio（只比 info）、sim 产 info（正例=k_expected，扰动非
none 产错误 info，sim_dut.info_dut_value 同一实现）、判定走 verdict 的
case_purpose 分流支路（_judge_info_inner，只比 info==k_expected，HT-12 独立结论）。
info 用例不受 --select/--limit 节流（构造开销小、逐算子仅 3 条），受 --ops/--max-n
约束；记录带 case_purpose:"info" 与 info 比对结果，无 residual 字段。

两处 info 派生的基座按册顶层 package_scope 取（口径函数 criteria/render_verify.
info_require_s1）：full 册取全部精度条目，其余取值与无该字段的散册取 s1 子集——
与包 index 侧和包内 verify 渲染副本同一条规则，三方派生出同一个 id 集合。

HT-9 批量 info 契约用例：每批量算子另派生 1 个混合 case
（derive_batched_info_cases：*potrfBatched 族 1~2 个代表内容非正定、k_expected
逐内容 k 值列表；*potrsBatched 族标量仅参数错、k_expected=-1）——现场构造
（build_batched_info_arrays 逐内容自检 fail-closed）+ sample_map 展开，sim 产
infoArray/标量 info（正例逐槽=逐内容 k_expected 展开，扰动非 none 逐内容产错误
info，info_dut_value 同一实现；out32 占位全零不消费），判定走 judge_a0 的
case_purpose 分流（_judge_a0_info_inner：infoArray 全批逐槽核对 / 标量直接比对）。
追加派生同样不受 --select/--limit 节流，受 --ops/--max-n 约束。

HT-10 确定性复跑：--rerun N（N≥2 启用）对每个**精度用例**（合法 SPD 口径；info
契约用例是构造性非正定探针，不参与）把 DUT 在同一输入上重复执行 N 次，第 2..N 次
全部与第 1 次比对（两两一致的线性等价式）——out32/info/status 三键 bit-wise 一致
（字节域比较，NaN 按位等同、不走 == 语义）。结论独立于数值判定（HT-12 口径）：
逐 case 落 determinism 子记录（runs/consistent/first_diff），summary 落 det_pass/
det_fail，确定性失配与数值 FAIL 同样令退出码 1。sim 通路本身无随机性，正例恒
det PASS；确定性 FAIL 的意义在真实 NPU 适配器接入后暴露非确定实现。

用法：
    stream_check.py --canonical <canonical_cases.json> --gen-dir <case-gen 的 scripts 目录>
                    --report <out.json> [--ops spotrf,...] [--select s1|all]
                    [--max-n N] [--limit K] [--perturb none|scale|zero|nan|imag|conj]
                    [--perturb-index i] [--jobs N] [--rerun N]
                    [--dump <case_id> ...] [--dump-dir <目录>]

准入断言（以新为准裁定 2026-10-09）：--canonical 同目录下存在 cases/index.json
（即包根布局）时，其顶层 ratio_basis 必须是 "A32-f64"，否则拒收退 2——缺字段即旧
A64 基，固化 ratio_cpu_mean 与现行残差实现不同基。不做旧包兼容、不在读侧重算。
散册 canonical 冒烟没有 index，不在准入面内。

退出码：0 = 全部数值 PASS（且启用 --rerun 时确定性无失配）；1 = 存在数值 FAIL、
判定 error 或确定性复跑失配；2 = 输入/契约错误（含 ratio_basis 非 A32-f64）。
formal 恒 PENDING_RULING（阈值语义待裁），本工具不出具正式结论。
"""

import argparse
import json
import platform
import resource
import sys
import time
from pathlib import Path

import numpy as np

# ru_maxrss 单位：Linux 为 KB、macOS 为字节（2026-09-24 远程实证抓出的少报 1024 倍）
_RSS_DIV = 1024 if platform.system() == "Linux" else 1024 * 1024
TOOL = "stream_check.py"
# s2a1-r9：info 派生基座按册顶层 package_scope 显式传（2026-10-09）——full 册不再
#   整批略过 info 用例，与包 index 侧和 verify 渲染副本三方派生同一个 id 集合。
TOOL_VER = "s2a1-r9"  # 承 s2a1-r8（准入断言：读到包 index 只受理 A32-f64 基）
RATIO_BASIS = "A32-f64"  # 受理的唯一残差基（index 顶层 ratio_basis）

_HERE = Path(__file__).resolve().parent
_CRITERIA = _HERE.parent / "criteria"


class ContractError(RuntimeError):
    pass


def _load_modules(gen_dir):
    """装入同源实现：criteria（权威判定）与 case-gen（生成/ratio 链）。"""
    for p in (str(_CRITERIA), str(gen_dir)):
        if p not in sys.path:
            sys.path.insert(0, p)
    import verdict as verdict_mod            # noqa: E402
    import cards_cholesky as cards_mod       # noqa: E402
    import batched_parallel as bp_mod        # noqa: E402  (批量代表槽判定复用入口)
    import batched_a0 as a0_mod              # noqa: E402  (A0 两层判定驱动)
    import gen_data_cholesky as gen_mod      # noqa: E402
    import fill_ratio_cpu as ratio_mod       # noqa: E402
    if not (Path(gen_dir) / "gen_data_cholesky.py").is_file():
        raise ContractError(f"--gen-dir 里找不到 gen_data_cholesky.py: {gen_dir}")
    return verdict_mod, cards_mod, bp_mod, a0_mod, gen_mod, ratio_mod


def _sim_dut(golden32, mode, card, perturb_index=None):
    """sim 通路：复用 sim_dut.perturb_out32（同一实现，不复制逻辑）。

    info 按接口角色分型（S3 spec §4）：potrfBatched 族（card.info_kind="array"）给
    (batch,) int32 全 0 infoArray，potrsBatched 族与单矩阵卡给标量 0；perturb_index
    透传逐矩阵扰动（批量负例演练）。

    DUT 适配点：真实 NPU 被测接入时，以「输入数组 → {out32, info, status}」的适配器
    替换本函数（协议同包通路的被测输出三键；采样与性能不在本工具范围）。
    """
    sys.path.insert(0, str(_HERE))
    try:
        from sim_dut import perturb_out32
    finally:
        sys.path.remove(str(_HERE))
    out32 = perturb_out32(golden32, mode, matrix_index=perturb_index)
    if getattr(card, "batched", False) and card.info_kind == "array":
        info = np.zeros(np.asarray(golden32).shape[0], dtype=np.int32)
    else:
        info = 0
    return {"out32": out32, "info": info, "status": "ok"}


def _bitwise_same(a, b):
    """HT-10：两份数组的 bit-wise 同一判定——字节域比较，NaN 按位等同（不走
    == 语义），-0.0 与 0.0 视为不同。shape/dtype 先行，失序即 False。
    实现走 uint8 视图逐元素比较（array_equal），不用 tobytes——批量大 case
    全批 out32 达 GB 级，tobytes 会整块复制一份（内存翻倍，--rerun 下叠加）。"""
    aa, bb = np.asarray(a), np.asarray(b)
    if aa.shape != bb.shape or aa.dtype != bb.dtype:
        return False
    # atleast_1d：0-d 标量（如 info 的 int32）不允许 view 降 itemsize；
    # ascontiguousarray：view 要求连续（连续输入零拷贝，不额外复制大数组）
    aa = np.ascontiguousarray(np.atleast_1d(aa)).view(np.uint8)
    bb = np.ascontiguousarray(np.atleast_1d(bb)).view(np.uint8)
    return bool(np.array_equal(aa, bb))


def _rerun_compare(first, again):
    """HT-10：两次 DUT 输出比对——out32/info bit-wise、status 相等。
    一致返回 None；失配返回 {"key", "detail"}（调用方补 run 序号）。"""
    if first.get("status") != again.get("status"):
        return {"key": "status",
                "detail": f"{first.get('status')!r} != {again.get('status')!r}"}
    for key in ("out32", "info"):
        if not _bitwise_same(first[key], again[key]):
            return {"key": key, "detail": "字节域不一致（bit-wise）"}
    return None


def _ratio_summary(ratio_cpu):
    """A0 逐内容 ratio 的报告摘要（k 值；prep_failed(None) 不入统计，只报计数）。"""
    if ratio_cpu is None:
        return None
    vals = [float(x) for x in ratio_cpu if x is not None]
    out = {"count": len(vals), "total": len(ratio_cpu)}
    if vals:
        a = np.asarray(vals, dtype=np.float64)
        out.update({"min": float(a.min()), "median": float(np.median(a)),
                    "max": float(a.max())})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog=TOOL, description=__doc__.splitlines()[0])
    ap.add_argument("--canonical", required=True, help="canonical_cases.json 路径")
    ap.add_argument("--gen-dir", required=True,
                    help="repo-task-solver-case-gen 的 scripts 目录（提供生成与 ratio 链）")
    ap.add_argument("--report", required=True, help="判定记录 JSON 输出路径")
    ap.add_argument("--ops", default=None, help="逗号分隔算子子集；缺省取 canonical 全部")
    ap.add_argument("--select", choices=("s1", "all"), default="all",
                    help="case 选择；流式默认 all（不落盘，无容量约束）")
    ap.add_argument("--max-n", type=int, default=None, help="只跑 n ≤ 此值的 case")
    ap.add_argument("--limit", type=int, default=None, help="最多跑前 K 个（抽样/冒烟）")
    ap.add_argument("--perturb", default="none",
                    choices=("none", "scale", "zero", "nan", "imag", "conj"),
                    help="sim 扰动模式（负例演练用；none=正例）")
    ap.add_argument("--perturb-index", type=int, default=None,
                    help="批量 case 的逐矩阵扰动序号（0 起，透传 sim_dut；A0 下为"
                         "全批槽位号——命中非 rep 槽触发一致性失配负例，报告带槽位号证据）")
    ap.add_argument("--jobs", type=int, default=1,
                    help="批量卡逐矩阵判定的多进程分块数（缺省 1=串行；"
                         "分块结果与串行逐位相同，S3 并行裁定）")
    ap.add_argument("--rerun", type=int, default=1,
                    help="HT-10 确定性复跑次数（≥2 启用）：精度用例的 DUT 在同一"
                         "输入上重复执行 N 次，第 2..N 次与第 1 次逐位比对；"
                         "info 契约用例不参与（构造性非正定探针）")
    ap.add_argument("--dump", action="append", default=[],
                    help="指定 case_id 落盘其数组与被测输出（调试后门，可重复）")
    ap.add_argument("--dump-dir", default=None, help="--dump 的落盘目录")
    args = ap.parse_args(argv)

    try:
        verdict_mod, cards_mod, bp_mod, a0_mod, gen_mod, ratio_mod = _load_modules(args.gen_dir)
        with open(args.canonical, encoding="utf-8") as fh:
            canonical = json.load(fh)
        cases = canonical.get("cases")
        if not isinstance(cases, list):
            raise ContractError(f"{args.canonical}: 顶层缺 cases 数组")
        # HT-4 消费补全（s3-d3-r6）：包内 cases/index.json 顶层固化了算子级
        # ratio_cpu_mean（发包侧 build_package 写入）——canonical 与 cases/ 同目录
        # （包根布局）时读出，按算子注入 case_arrays，兜底双支 max(5r, 3m) 完整生效；
        # 无此文件（散册 canonical 冒烟）保持缺 mean 单支兼容口径。
        # 准入断言（以新为准裁定 2026-10-09）：读到包 index 就只受理 A32-f64 基；
        # 散册 canonical 冒烟没有 index，不在准入面内（无固化参考值可误用）。
        index_mean = None
        idx_path = Path(args.canonical).parent / "cases" / "index.json"
        if idx_path.is_file():
            with open(idx_path, encoding="utf-8") as fh:
                index_doc = json.load(fh) or {}
            basis = index_doc.get("ratio_basis")
            if basis != RATIO_BASIS:
                raise ContractError(
                    f"旧基包不受理：参考值基准非 {RATIO_BASIS}（以新为准裁定 "
                    f"2026-10-09），请使用更新版任务包（index.ratio_basis={basis!r}）")
            index_mean = index_doc.get("ratio_cpu_mean")
        ops = set(o.strip() for o in args.ops.split(",")) if args.ops else None
        picked = [c for c in cases
                  if (ops is None or c.get("op") in ops)
                  and (args.select == "all" or c.get("s1_subset") is True)
                  and (args.max_n is None or c.get("n", 0) <= args.max_n)]
        if args.limit:
            picked = picked[: args.limit]
        if not picked:
            raise ContractError("筛选后没有 case 可跑（检查 --ops/--select/--max-n）")
        if args.dump and not args.dump_dir:
            raise ContractError("--dump 需要同时给 --dump-dir")
        if args.rerun < 1:
            raise ContractError(f"--rerun 应 ≥1，得到 {args.rerun}")
        lapack = ratio_mod._lapack()
        # HT-8/HT-9：追加派生 info 契约用例（单算子 B2 三变体 + 批量 B3 混合 case；
        # canonical case 之后跑；不受 select/limit 节流——构造开销小；受 ops/max_n 约束）
        #
        # 派生基座按册顶层 package_scope 显式传（口径函数 render_verify.
        # info_require_s1，与包 index 侧和 verify 渲染副本同一条规则，不在此抄一份）：
        # 不传就吃 derive_info_cases 的 s1 默认，full 册的精度条目没有 s1_subset
        # 标记，info 用例会被整批静默略过，流式与包/verify 派生出不同 id 集合。
        import render_verify                       # criteria 已在 sys.path（_load_modules）
        require_s1 = render_verify.info_require_s1(canonical)
        by_id = {c["case_id"]: c for c in cases}
        picked = picked + [
            e for e in gen_mod.derive_info_cases(cases, require_s1=require_s1)
            if (ops is None or e.get("op") in ops)
            and (args.max_n is None or e.get("n", 0) <= args.max_n)]
        picked = picked + [
            e for e in gen_mod.derive_batched_info_cases(cases,
                                                         require_s1=require_s1)
            if (ops is None or e.get("op") in ops)
            and (args.max_n is None or e.get("n", 0) <= args.max_n)]
    except ContractError as exc:
        print(f"[{TOOL}] 输入/契约错误: {exc}", file=sys.stderr)
        return 2

    records, t0 = [], time.time()
    n_pass = n_fail = n_err = n_prep = 0
    n_det_pass = n_det_fail = 0                        # HT-10 确定性独立结论计数
    for k, case in enumerate(picked, 1):
        cid, op = case["case_id"], case["op"]
        tc = time.time()
        try:
            gen_mod.validate_case(case)
            card = cards_mod.get_card(op)
            batched = getattr(card, "batched", False)
            is_info = case.get("case_purpose") == "info"
            if is_info and batched:
                # HT-9：批量 info 契约用例——现场构造（build_batched_info_arrays
                # 逐内容自检 fail-closed）；无 ratio（只比 info，HT-4 mean 不计入）；
                # sim 产 infoArray/标量 info（正例逐槽=逐内容 k_expected 经
                # sample_map 展开，扰动非 none 逐内容产错误 info，info_dut_value
                # 同一实现；out32 占位全零不消费）；judge_a0 按 case_purpose 分流
                # （_judge_a0_info_inner，infoArray 全批逐槽核对/标量直接比对）。
                arrays = gen_mod.build_batched_info_arrays(case)
                sample_map = gen_mod.derive_sample_map(case["seed"], case["batch"])
                ratio_cpu, ratio_status, n_prep_flag = None, "info", False
                sys.path.insert(0, str(_HERE))
                try:
                    from sim_dut import info_array_values, info_dut_value
                finally:
                    sys.path.remove(str(_HERE))
                batch_n = int(case["batch"])
                cols = int(case["nrhs"]) if card.base_op.endswith("potrs") \
                    else int(case["n"])
                if card.info_kind == "array":
                    info_val = info_array_values(case["k_expected"], sample_map,
                                                 batch_n, args.perturb)
                else:
                    info_val = np.int64(info_dut_value(int(case["k_expected"]),
                                                       args.perturb))
                dut = {"out32": np.zeros((batch_n, int(case["n"]), cols),
                                         dtype=arrays["A32"].dtype),
                       "info": info_val, "status": "ok"}
                case_arrays = dict(arrays)
                case_arrays.update(case)
                case_arrays["sample_map"] = sample_map
                v = a0_mod.judge_a0(card, case_arrays, dut, jobs=1)
            elif is_info:
                # HT-8：info 契约用例——现场构造（构造性自检 fail-closed）；无 ratio
                # 可算（只比 info，HT-4 mean 也不计入）；sim 产 info（正例=
                # k_expected，扰动非 none 产错误 info，sim_dut.info_dut_value 同一
                # 实现）；verdict 按 case_purpose 分流出 info 独立结论（HT-12）。
                arrays = gen_mod.build_info_arrays(
                    case, gen_mod.build_case_arrays(by_id[case["base_case_id"]]))
                ratio_cpu, ratio_status, n_prep_flag = None, "info", False
                sys.path.insert(0, str(_HERE))
                try:
                    from sim_dut import info_dut_value
                finally:
                    sys.path.remove(str(_HERE))
                dut = {"out32": np.zeros_like(arrays["A32"]),
                       "info": np.int64(info_dut_value(int(case["k_expected"]),
                                                       args.perturb)),
                       "status": "ok"}
                case_arrays = dict(arrays)
                case_arrays.update(case)
                # info 用例均单矩阵卡：judge_parallel 直通 verdict.judge（分流支路）
                v = bp_mod.judge_parallel(card, case_arrays, dut, jobs=1)
            elif batched:
                # A0 抽样通路（HT-2）：k=min(5,batch) 个代表内容；ratio 逐内容；
                # 全批输入现场展开喂 sim DUT；判定 criteria/batched_a0 两层先后。
                contents = gen_mod.build_batched_contents(case)
                sample_map = gen_mod.derive_sample_map(case["seed"], case["batch"])
                k_contents = len(sample_map)
                ratio_cpu, pf = [], 0
                for i in range(k_contents):
                    sub_case = {"case_id": f"{cid}#c{i}", "op": card.base_op,
                                "uplo": case["uplo"]}
                    sub_arrays = {kk: contents[kk][i]
                                  for kk in ("A64", "A32", "B64", "B32") if kk in contents}
                    try:
                        ratio_cpu.append(
                            float(ratio_mod.run_chain(verdict_mod, lapack, sub_case, sub_arrays)))
                    except ratio_mod.PrepFailed:
                        ratio_cpu.append(None)
                        pf += 1
                ratio_status = "ok" if pf < k_contents else "prep_failed"
                n_prep_flag = pf > 0
                arrays = gen_mod.expand_sampled_rows(contents, sample_map, 0, int(case["batch"]))
            else:
                arrays = gen_mod.build_case_arrays(case)
                try:
                    ratio_cpu = ratio_mod.run_chain(verdict_mod, lapack, case, arrays)
                    ratio_status = "ok"
                    n_prep_flag = False
                except ratio_mod.PrepFailed:
                    ratio_cpu, ratio_status, n_prep_flag = None, "prep_failed", True
            det = None      # HT-10：info 契约用例不参与确定性复跑（构造性非正定探针）
            if not is_info:
                dut = _sim_dut(arrays["golden32"], args.perturb, card,
                               args.perturb_index)
                # HT-10：确定性复跑——同一输入重复执行，第 2..N 次与第 1 次逐位
                # 比对（out32/info/status，字节域）；结论独立于数值判定（HT-12），
                # 首个失配即止并记 run 序号与失配键。
                if args.rerun >= 2:
                    first_diff = None
                    for r in range(2, args.rerun + 1):
                        first_diff = _rerun_compare(
                            dut, _sim_dut(arrays["golden32"], args.perturb, card,
                                          args.perturb_index))
                        if first_diff is not None:
                            first_diff["run"] = r
                            break
                    det = {"runs": args.rerun, "consistent": first_diff is None,
                           "first_diff": first_diff}
                    if first_diff is None:
                        n_det_pass += 1
                    else:
                        n_det_fail += 1
                if batched:
                    # A0 条目：内容数组（批维 k）+ canonical batch 声明 + sample_map +
                    # 逐内容 ratio（k 值列表）；judge_a0 先一致性后代表槽（HT-2）。
                    case_arrays = dict(contents)
                    case_arrays.update(case)
                    case_arrays["sample_map"] = sample_map
                    case_arrays["ratio_cpu"] = ratio_cpu
                    case_arrays["ratio_cpu_status"] = ratio_status
                    v = a0_mod.judge_a0(card, case_arrays, dut, jobs=args.jobs)
                else:
                    case_arrays = dict(arrays)
                    case_arrays.update(case)
                    case_arrays["ratio_cpu"] = ratio_cpu
                    case_arrays["ratio_cpu_status"] = ratio_status
                    if index_mean and isinstance(index_mean, dict) and op in index_mean:
                        # 包内固化 mean 注入（s3-d3-r6）：兜底双支完整生效
                        case_arrays["ratio_cpu_mean"] = index_mean[op]
                    # 单矩阵卡该入口直通 verdict.judge（零漂移）；无包 index 时兜底走
                    # 缺 mean 单支兼容口径（verdict._run_residual）。
                    v = bp_mod.judge_parallel(card, case_arrays, dut, jobs=args.jobs)
            # is_info 的 arrays/dut/case_arrays/v 已在上方 info 支路构造
            rec = {
                "case_id": cid, "op": op, "n": case.get("n"), "uplo": case.get("uplo"),
                "numeric": v.get("numeric"), "formal": v.get("formal"),
                "flags": v.get("flags"), "error": v.get("error"),
            }
            if is_info:
                # HT-8/HT-9：info 独立结论（HT-12 口径）。单矩阵 verdict 带 info
                # 比对键；批量 verdict 走批量 schema（batch/fail_count/first_fail_index，
                # 比对明细在 diagnostics.info_contract）。
                rec.update({"case_purpose": "info",
                            "info_probe": case.get("info_probe"),
                            "k_expected": case.get("k_expected")})
                if batched:
                    rec.update({"mode": "info", "batch": v.get("batch"),
                                "fail_count": v.get("fail_count"),
                                "first_fail_index": v.get("first_fail_index"),
                                "diagnostics": v.get("diagnostics")})
                else:
                    rec["info"] = v.get("info")
            elif batched:
                worst = v.get("worst") or {}
                w_res = worst.get("residual") or {}
                rec.update({
                    "mode": "a0", "sampled_contents": k_contents,
                    "canonical_batch": case.get("batch"),
                    "batch": v.get("batch"),
                    "fail_count": v.get("fail_count"),
                    "first_fail_index": v.get("first_fail_index"),
                    "worst_index": v.get("worst_index"),
                    "worst": {
                        "residual": {kk: w_res.get(kk)
                                     for kk in ("ran", "ratio", "threshold")},
                    },
                    "diagnostics": v.get("diagnostics"),
                    "ratio_cpu": _ratio_summary(ratio_cpu),
                    "ratio_cpu_status": ratio_status,
                })
                if pf:
                    rec["ratio_cpu_prep_failed"] = pf
            else:
                rec.update({
                    "residual": {kk: v["residual"].get(kk)
                                 for kk in ("ran", "ratio", "threshold", "pass")},
                    "ratio_cpu": ratio_cpu, "ratio_cpu_status": ratio_status,
                })
            rec["seconds"] = round(time.time() - tc, 3)
            if det is not None:
                rec["determinism"] = det          # HT-10：独立结论（精度用例才有）
            if v.get("error"):
                n_err += 1
            elif v.get("numeric") == "PASS":
                n_pass += 1
            else:
                n_fail += 1
            if n_prep_flag:
                n_prep += 1
            if cid in args.dump:
                dd = Path(args.dump_dir); dd.mkdir(parents=True, exist_ok=True)
                np.savez(dd / f"{cid}.npz", **arrays)
                # info 形状感知落盘：infoArray（批量 potrf 族）保数组，标量保旧格式
                info_dump = (dut["info"] if isinstance(dut["info"], np.ndarray)
                             else np.int64(dut["info"]))
                np.savez(dd / f"{cid}.dut.npz", out32=dut["out32"], info=info_dump)
            records.append(rec)
            del arrays, case_arrays, dut                 # 即弃：流式的核心承诺
        except Exception as exc:                          # 单 case 失败不吞整轮
            n_err += 1
            records.append({"case_id": cid, "op": op, "error": f"{type(exc).__name__}: {exc}"})
        if k % 20 == 0 or k == len(picked):
            peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // _RSS_DIV
            print(f"[{TOOL}] {k}/{len(picked)} pass={n_pass} fail={n_fail} "
                  f"err={n_err} peak_rss={peak_mb}MB", file=sys.stderr)

    report = {
        "tool": {"name": TOOL, "ver": TOOL_VER},
        "params": {"canonical": str(args.canonical), "select": args.select,
                   "ops": sorted(ops) if ops else "all", "max_n": args.max_n,
                   "limit": args.limit, "perturb": args.perturb,
                   "perturb_index": args.perturb_index, "jobs": args.jobs,
                   "rerun": args.rerun},
        "env": {"python": sys.version.split()[0], "numpy": np.__version__},
        "summary": {"total": len(picked), "numeric_pass": n_pass, "numeric_fail": n_fail,
                    "error": n_err, "prep_failed": n_prep,
                    "det_pass": n_det_pass, "det_fail": n_det_fail,
                    "wall_seconds": round(time.time() - t0, 1),
                    "peak_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // _RSS_DIV},
        "formal": "PENDING_RULING",
        "cases": records,
    }
    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print(f"[{TOOL}] 完成 {len(picked)} case: pass={n_pass} fail={n_fail} err={n_err} "
          + (f"det_pass={n_det_pass} det_fail={n_det_fail} " if args.rerun >= 2 else "")
          + f"→ {out}")
    return 0 if (n_fail == 0 and n_err == 0 and n_det_fail == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
