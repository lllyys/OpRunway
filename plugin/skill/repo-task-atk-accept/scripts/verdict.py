#!/usr/bin/env python3
"""汇总各阶段产物推出验收结论，写 verdict.json，再叫 render.py 与 make_repro.py。

结论由本脚本从数据推导，不由报告作者自己写。
**这里只算数、不排版**：report.md 与 index.html 都由 render.py 读 verdict.json 出，
两份报告同源，不会出现「HTML 说通过、md 说不通过」。

退出码 0 结论已产出（通过或不通过都算），3 缺产物。
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_repro  # noqa: E402
import render  # noqa: E402
import run_atk  # noqa: E402 - 只借 reject_lines，两处必须认同一套报错特征
import stage_clock  # noqa: E402 - 同目录量具，给 stage JSON 记阶段墙钟

# facts.json 的 performance.kind 说的是「任务书要求怎么比」，
# 与 _perf_status 推出来的「本轮比到哪一步」是两件事，报告里分开写。
PERF_LABEL = {
    "none": "任务书未给性能基线",
    "builtin": "对标 CANN 内置实现",
    "cross_dtype": "同一算子内跨 dtype 自比",
    "threshold": "对标任务书给的绝对指标",
}

# Device 耗时逐次波动通常在 5% 以内，判劣化要留余量。两档之间留一个「持平」
# 区间，是为了不把一次抖动写成结论：
NOT_WORSE = 1.05              # ≤ 1.05 倍算持平，落在抖动范围内
WORSE = 1.10                  # > 1.10 倍才判劣化；两档之间返回 unknown，建议重跑一轮
# 两轮能按用例配上对的最少条数。低于它只报 unknown，不出比值结论。
MIN_PAIRED = 3

BAND_ORDER = ("scalar", "small", "medium", "large")


def _load(path, required=True):
    file = Path(path)
    if not file.exists():
        if required:
            print(f"{path} 不存在，前面的阶段没跑完。", file=sys.stderr)
            return None
        return {}
    with open(file, encoding="utf-8") as handle:
        return json.load(handle)


# 精度表按什么分组。缺省按 dtype——那是「整类失败」最常暴露的轴（kernel 不支持
# 某个 dtype 时那一档整档挂）。纯 attr 用例没有张量，取不到 dtype，此时改按
# `facts.json` 的 `group_attr` 指的那个参数分。**不分组就等于放弃「整类失败」
# 这个发现手段**：200 条揉成一行时，Blocked-ELL 那 80 条全挂也只表现成通过率降。
_GROUP = {"attr": "", "labels": []}


def _group_key():
    """精度表那一列的表头。"""
    return _GROUP["attr"] or "dtype"


def _dtype_of(case):
    """用例的分组键。缺省取第一个张量参数的 dtype，取不到返回 `?`。

    张量列表参数（`type: tensors`）在用例里是一个 list，不是 dict。口径与
    `run_atk._case_dtype` 一致——两处不一致时报告里的分组会和跑测轮对不上。
    """
    if _GROUP["attr"]:
        return _attr_group(case)
    for item in case.get("inputs", []):
        if isinstance(item, list):
            item = item[0] if item else None
        if isinstance(item, dict) and item.get("type") in ("tensor", "tensors"):
            return item.get("dtype", "?")
    return "?"


def _attr_group(case):
    """按 `group_attr` 指的 attr 分组。有 `group_labels` 就翻成名字。

    翻不了时写 `<参数名>=<值>`，**不写 `?`**：分不出组和「这一组叫问号」
    是两回事，前者要回生成侧补 `group_labels`，后者无从判断。
    """
    attr = _GROUP["attr"]
    for item in case.get("inputs", []):
        if not isinstance(item, dict) or item.get("name") != attr:
            continue
        values = item.get("range_values")
        value = values[0] if isinstance(values, list) and values else item.get("value")
        labels = _GROUP["labels"]
        if isinstance(value, int) and 0 <= value < len(labels):
            return str(labels[value])
        return f"{attr}={value}"
    return f"{attr}=?"


def _group_failures(cases, failed_ids):
    """失败归因只分组到用例规格特征，不猜成因。"""
    by_id = {case["id"]: case for case in cases}
    groups = Counter()
    for ident in failed_ids:
        case = by_id.get(ident)
        groups[_dtype_of(case) if case else "?"] += 1
    return groups


def _acc_baseline(golden, facts=None):
    """标杆是谁。返回 (kind, 名字)。

    自带件路没有冻结 golden：标杆是自带件 nodes yaml 里那个非被测节点，**当场算**。
    有 `facts.kit` 就是这条路，名字取 `kit.baseline`。其余按用例包走 golden 的
    manifest，读不到再退老用例包的 torch/cpu_0。
    """
    kit = (facts or {}).get("kit") or {}
    if kit:
        return "kit", str(kit.get("baseline") or "自带件的标杆节点")
    try:
        with open(Path(golden) / "manifest.json", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError):
        return "torch", "cpu_0"
    return manifest.get("baseline", "torch"), manifest.get("baseline_dir", "cpu_0")


def _cases_of(pkg, facts):
    """本轮的用例清单，**必须与跑测那一轮吃的是同一份**。返回 (用例, 出处)。

    用例包那条在 `input/cases.json`。自带件路没有用例包，跑的是体检修复后的副本，
    位置由 `facts.json` 的 `kit.cases` 指出来（相对 `input/`，或绝对路径）。
    清单两种形状都认：JSON 数组，或 `{"cases": [...]}`。

    拿不到的后果不是报错而是**报告悄悄降级**：精度分档塌成一档、失败归因没有
    dtype、复现包挑不出用例，而通过率照常算得出来。
    """
    ref = (facts.get("kit") or {}).get("cases")
    if ref:
        path = Path(ref)
        if not path.is_absolute():
            path = Path(pkg) / path
        got = _load(path, required=False)
        if got is not None:
            cases = got.get("cases") if isinstance(got, dict) else got
            return list(cases or []), str(path)
        print(f"facts.kit.cases 指向 {path}，读不出来。精度分档会塌成一档。",
              file=sys.stderr)
    return _load(Path(pkg) / "cases.json", required=False) or [], "cases.json"


def _acc_summary(kind, name, facts, accuracy):
    """精度栏那一句。三条路的标杆来历不同，混着写会让读者去找不存在的目录。"""
    if not accuracy:
        return ""
    verdict_word = "达标" if accuracy.get("passed") else "不达标"
    if kind == "kit":
        return (f"标杆为自带件的 `{name}` 节点**当场算**，没有冻结 golden；"
                f"比对走自带件自己注册的比对器，精度标准取用例的 `standard.acc`。"
                f"ATK 判定精度{verdict_word}。")
    std = (facts.get("accuracy") or {}).get("acc", "default")
    return (f"标杆为生成侧冻结的 "
            f"{'**CANN 内置实现**' if kind == 'builtin' else 'CPU torch'} golden"
            f"（`{name}/`），比对走 ATK `accuracy_load`，精度标准 `{std}`。"
            f"ATK 判定精度{verdict_word}。")


def _acc_false_ids(accuracy, isolate, real_failed, cascade, unchecked):
    """精度不符的用例 id。**与执行失败是两件事**：这些跑起来了，只是算错了。

    ATK 报表里「failed cases」与「accuracy false cases」是两张表，正常不重叠；
    真重叠时按执行失败算，免得同一条在两处各记一次，通过数就成负的了。

    **隔离复验里发现的也要并进来。** 全量轮被连带打挂的用例在那一轮没有精度判定，
    要到隔离复验重跑时才第一次算出结果；只读全量轮的话，一条「重跑起来了但算错」
    会被漏成通过。实测 id 138、214 就是这么冒出来的。
    """
    found = (set(accuracy.get("accuracy_false_ids") or [])
             | set(isolate.get("accuracy_false") or []))
    return sorted(found - set(real_failed) - set(cascade) - set(unchecked))


def _by_dtype(cases, acc_false, real_failed, cascade, unchecked):
    """精度按 dtype 的通过情况。**分母是用例包里的用例，不是 ATK 报告的总数**——
    报告的总数只说明这一轮跑了多少条，用例包才是任务书要求覆盖的范围。

    「未通过」拆成三列，因为它们的归因完全不同：精度不符是算错了，执行失败是
    跑不起来，未判定是隔离复验没测到。揉成一列会让读者把三件事当成一件。

    **连带失败算通过，进分母。** 它们在隔离复验里以与全量相同的口径重跑并跑通了，
    那就是测过且通过，不是「没测到」。早先把它们踢出分母，是因为隔离复验不带
    `--slice_input`、与全量不同口径，重跑通过说明不了问题；口径对齐之后这个理由
    不成立了。只有轮数用尽、真的没测到的才是未判定。
    """
    real, casc, unck = set(real_failed), set(cascade), set(unchecked)
    acc_false = set(acc_false)

    buckets = defaultdict(lambda: dict(total=0, acc_false=0, exec_failed=0,
                                       cascade=0, unchecked=0))
    for case in cases:
        item = buckets[_dtype_of(case)]
        ident = case["id"]
        item["total"] += 1
        if ident in casc:
            item["cascade"] += 1      # 只做计数，不从分母里减
        if ident in unck:
            item["unchecked"] += 1
        elif ident in real:
            item["exec_failed"] += 1
        elif ident in acc_false:
            item["acc_false"] += 1

    rows = []
    for dtype in sorted(buckets):
        item = buckets[dtype]
        undetermined = item["unchecked"]
        denominator = item["total"] - undetermined
        matched = denominator - item["exec_failed"] - item["acc_false"]
        rows.append({
            "dtype": dtype,
            "total": item["total"],
            "matched": matched,
            "acc_false": item["acc_false"],
            "exec_failed": item["exec_failed"],
            "undetermined": undetermined,
            "rate": round(100.0 * matched / denominator, 1) if denominator else 0.0,
            "ok": matched == item["total"],
        })
    if len(rows) > 1:
        total = sum(r["total"] for r in rows)
        matched = sum(r["matched"] for r in rows)
        undetermined = sum(r["undetermined"] for r in rows)
        denominator = total - undetermined
        rows.append({
            "dtype": "合计", "total": total, "matched": matched,
            "acc_false": sum(r["acc_false"] for r in rows),
            "exec_failed": sum(r["exec_failed"] for r in rows),
            "undetermined": undetermined,
            "rate": round(100.0 * matched / denominator, 1) if denominator else 0.0,
            "ok": matched == total, "total_row": True,
        })
    return rows


def _median(values):
    ordered = sorted(values)
    return ordered[len(ordered) // 2] if ordered else None


def _speedup(ratio):
    """耗时比 -> 加速比。报告只出加速比一个数——两个互为倒数的数并排放着容易读反。"""
    return f"{1 / ratio:.2f}x" if ratio else "—"


def _verdict_ratio(ratio):
    if ratio <= NOT_WORSE:
        return "不劣化"
    if ratio > WORSE:
        return "劣化"
    return "unknown（落在噪声区间，建议重跑一轮确认）"


def _band_rows(samples):
    """把 (档位, 被测耗时, 基线耗时) 样本按档聚成行。基线为 None 时只出绝对耗时。

    **逐样本先算比值，再取中位数**，不是两组中位数相除。同一档内 shape 仍有
    差异，先除掉再统计才是纯粹的实现差异；两组中位数相除只在两组分布完全一致
    时才等价，而任何一条掉队都会让它悄悄失衡。

    某档样本少于 MIN_PAIRED 条就写 unknown，不出结论——档位窄是常事
    （生成侧的 large 常常抽不够），把 2 条样本的比值当结论比没有结论更糟。
    """
    buckets = defaultdict(list)
    for band, mine, base in samples:
        if base is None:
            buckets[band].append((mine, None, None))
        elif base:
            buckets[band].append((mine, base, mine / base))

    def row(band, items, total_row=False):
        item = {"band": band, "n": len(items), "mine": _median([m for m, _, _ in items]),
                "base": None, "speedup": None, "verdict": None}
        if total_row:
            item["total_row"] = True
        if items[0][1] is None:
            return item
        if len(items) < MIN_PAIRED:
            item.update(mine=None, verdict=f"unknown（样本不足 {MIN_PAIRED}）")
            return item
        ratio = _median([r for _, _, r in items])
        item.update(base=_median([b for _, b, _ in items]),
                    speedup=_speedup(ratio), verdict=_verdict_ratio(ratio))
        return item

    ordered = [b for b in BAND_ORDER if b in buckets] + \
              [b for b in sorted(buckets) if b not in BAND_ORDER]
    rows = [row(band, buckets[band]) for band in ordered]
    allitems = [r for items in buckets.values() for r in items]
    if len(ordered) > 1 and allitems and (allitems[0][1] is None
                                          or len(allitems) >= MIN_PAIRED):
        rows.append(row("合计", allitems, total_row=True))
    return rows


def _aclnn_times(report):
    times = {int(k): v for k, v in (report.get("device_times") or {}).items()}
    return {ident: entry["aclnn"] for ident, entry in times.items() if "aclnn" in entry}


def _dtype_groups(performance, cases):
    """按 dtype 把 aclnn 侧耗时分组，只在缺 perf/manifest.json 时用。"""
    by_id = {case["id"]: case for case in cases}
    groups = {}
    for ident, value in _aclnn_times(performance).items():
        case = by_id.get(ident)
        if case:
            groups.setdefault(_dtype_of(case), []).append(value)
    return groups


def _conclusive_pairs(facts, groups):
    """返回 (能出结论的对, 任务书要求的全部对)。任一侧样本少于 MIN_PAIRED 就出不了。"""
    pairs = (facts.get("performance") or {}).get("pairs", []) or []
    ok = [pair for pair in pairs
          if len(groups.get(pair[0], [])) >= MIN_PAIRED
          and len(groups.get(pair[1], [])) >= MIN_PAIRED]
    return ok, pairs


def _perf_status(facts, accuracy, performance, baseline, cases, perf_manifest=None,
                 external=None):
    """性能状态由数据推导。精度未过一律不评级——算错的算子跑得快没有意义。

    kind=builtin 时还要看基线轮**真的采到数据没有**。基线轮跑完但一条不成，
    说明内置实现根本跑不了这批用例，那是没有基线可比，不是已评级。

    kind=threshold 时看有没有外部性能结果：有且原话里抠得出数值门槛，就真判；
    否则维持「待人工判定」。**状态与报告正文由同一个函数算**
    （`_threshold_with_external`），分两处算迟早出现「总结论说达标、
    正文说不达标」。
    """
    kind = (facts.get("performance") or {}).get("kind", "none")
    if not accuracy:
        return "未执行(精度未裁决)", kind
    if not accuracy.get("passed"):
        # 已经采到的耗时不丢，但只当参考数据，不出评级结论。
        return ("未评级(精度未通过，仅留参考数据)" if performance
                else "未执行(精度未通过)"), kind
    if not performance:
        # 自带件路上 ATK 那轮整轮不跑，倍率只有外部工具采得到。
        # 有外部结果就照 threshold 判，别写成「未执行」。
        if kind == "threshold" and external:
            criterion = ((facts.get("performance") or {}).get("criterion")
                         or "任务书原话未记录")
            return _threshold_with_external(external, criterion).get(
                "status", f"待人工判定({criterion})"), kind
        return "未执行(未跑性能轮)", kind
    if kind == "none":
        return "未评级(无基线)", kind
    if kind == "threshold":
        criterion = (facts.get("performance") or {}).get("criterion") or "任务书原话未记录"
        if external:
            return _threshold_with_external(external, criterion).get(
                "status", f"待人工判定({criterion})"), kind
        # 没有外部结果时判不了：达标与否取决于标杆倍率或该机型的理论峰值，
        # 跑测侧两样都没有。但要求确实存在，不能跟 none 一样收进「未评级」——
        # 那读起来就是「任务书没提性能」。状态里带上原话，交给读报告的人判。
        return f"待人工判定({criterion})", kind
    if kind == "builtin":
        under_test = _aclnn_times(performance)
        base_times = _aclnn_times(baseline or {})
        if not base_times:
            return "未评级(基线轮无数据)", kind
        # 采到数据不等于能出结论：两轮要能按用例配上对，且样本够。
        # 少于阈值时 _builtin_perf 只会写 unknown，这里必须跟着说未评级，
        # 否则报告会出现「状态：已评级」和「结论 unknown」并排自相矛盾。
        paired = [i for i in under_test if i in base_times]
        if len(paired) < MIN_PAIRED:
            return f"未评级(配对样本 {len(paired)} 条，少于 {MIN_PAIRED})", kind
    if kind == "cross_dtype":
        # 同上：样本不足的对写成 unknown，一对都没成还报「已评级」的话，
        # overall 会写「通过」——那是把没验到的判成验过了。
        # **判据要和报告表格用同一条路**：有配对表就按配对数判，没有才退回 dtype 分组。
        meta = (perf_manifest or {}).get("pairs") or []
        if meta:
            times = _aclnn_times(performance)
            usable = [m for m in meta
                      if m.get("mine") in times and m.get("base") in times]
            if len(usable) < MIN_PAIRED:
                return (f"未评级(可比对数 {len(usable)}，少于 {MIN_PAIRED})"), kind
            return "已评级", kind
        ok, pairs = _conclusive_pairs(facts, _dtype_groups(performance, cases))
        if not pairs:
            return "未评级(facts.performance.pairs 为空)", kind
        if not ok:
            return f"未评级(0/{len(pairs)} 对 dtype 的样本够 {MIN_PAIRED} 条)", kind
    return "已评级", kind


def _no_baseline_notes(baseline):
    """基线轮拿不到耗时时，把「为什么拿不到」说清楚，三种去向不一样。"""
    head = ["任务书要求对标 CANN 内置实现，但基线轮没有产出耗时数据，"
            "性能结论 **unknown**。"]
    if not baseline:
        return head + ["基线轮还没跑。命令见 run-performance.md「kind = builtin」。"]
    total, failed = baseline.get("total", 0), baseline.get("failed", 0)
    if total and failed >= total:
        return head + [
            f"基线轮 {total} 条用例**全部执行失败**——内置实现跑不了这批用例。"
            "内置实现的参数校验通常比待验收实现窄（社区任务多半就是为此而来），"
            "报错码在 `work/evidence/performance_builtin.log` 里，捞法见 troubleshooting.md。",
            "**这不能算「待验收实现更快」。** 没有基线就是没有基线，结论是 unknown，"
            "把内置实现跑不了这件事写进报告备注即可。",
        ]
    return head + ["基线轮跑了但报告里没有 Device 耗时列，多半是任务被中途打断。"
                   "确认 `work/evidence/performance_builtin.log` 的结尾再重跑一次。"]


def _builtin_perf(under_test, baseline, facts, cases, perf_manifest):
    """kind=builtin：与 CANN 内置实现逐用例比 Device 耗时。

    比较只能覆盖内置实现支持的 dtype——社区任务新增的那几种它按定义就不支持。
    报告必须写明覆盖了哪些、漏了哪些，否则读者会以为结论覆盖全部 dtype。
    """
    ids = [i for i in under_test if i in baseline]
    if len(ids) < MIN_PAIRED:
        return [], "规模档", [
            f"与内置实现能配上对的用例少于 {MIN_PAIRED} 条，性能结论 unknown。"]

    by_id = {case["id"]: case for case in cases}
    covered = sorted({_dtype_of(by_id[i]) for i in ids if i in by_id})
    declared = sorted({d for param in facts.get("params", [])
                       if param.get("role") == "input"
                       for d in param.get("dtypes", [])
                       if d not in {"int", "attr_bool", "string"}})
    missing = [d for d in declared if d not in covered]

    bands = (perf_manifest or {}).get("bands") or {}
    if bands:
        rows = _band_rows([(bands.get(str(i), "?"), under_test[i], baseline[i])
                           for i in ids])
        label, notes = "规模档", []
    else:
        rows = _band_rows([("全部配对", under_test[i], baseline[i]) for i in ids])
        label = "范围"
        notes = ["`perf/manifest.json` 缺失（生成侧是旧版本），做不了分规模档的加速比。"
                 "回生成侧重跑 `gen_cases.py` 补出来。"]

    notes.append(f"比较覆盖的 dtype：{'、'.join(covered)}。")
    if missing:
        notes.append(f"**未覆盖：{'、'.join(missing)}** —— 内置实现不支持这几种，"
                     f"没有基线可比，它们的性能结论是 `unknown`。")
    return rows, label, notes


def _cross_dtype_perf(facts, performance, cases, perf_manifest):
    """kind=cross_dtype：同一算子内按 dtype 配对自比，按规模档出加速比。"""
    times = _aclnn_times(performance)
    meta = (perf_manifest or {}).get("pairs") or []
    if meta:
        # 逐对比值再按档聚合。配对关系来自生成侧的 perf/manifest.json，
        # 档位也是那边算好的——这边不重算，免得两套阈值。
        usable = [m for m in meta if m.get("mine") in times and m.get("base") in times]
        rows = _band_rows([(m.get("band", "?"), times[m["mine"]], times[m["base"]])
                           for m in usable])
        notes = []
        if dropped := len(meta) - len(usable):
            notes.append(f"**{dropped}/{len(meta)} 对没跑出耗时，整对剔除**——"
                         f"一对里缺一条就没法比，留着会让统计歪掉。"
                         f"性能结论不覆盖这些用例。")
        return rows, "规模档", notes

    groups = _dtype_groups(performance, cases)
    ok, all_pairs = _conclusive_pairs(facts, groups)
    rows = []
    for pair in all_pairs:
        mine_group, base_group = groups.get(pair[0], []), groups.get(pair[1], [])
        item = {"band": f"{pair[0]} / {pair[1]}", "n": min(len(mine_group), len(base_group)),
                "mine": None, "base": None, "speedup": None,
                "verdict": f"unknown（样本不足 {MIN_PAIRED} 条）"}
        if len(mine_group) >= MIN_PAIRED and len(base_group) >= MIN_PAIRED:
            mine, base = _median(mine_group), _median(base_group)
            ratio = mine / base if base else 0
            item.update(mine=mine, base=base, speedup=_speedup(ratio),
                        verdict=_verdict_ratio(ratio))
        rows.append(item)
    notes = ["`perf/manifest.json` 缺失（生成侧是旧版本），**退回按 dtype 分组取中位数"
             "相除**，做不了分规模档的加速比，也做不到逐对比值。"
             "回生成侧重跑 `gen_cases.py` 补出配对表。",
             "同一 dtype 内不同 shape 的耗时差几个量级，这里比的是各组中位数。"
             "shape 可比性靠生成侧的成对子集保证。"]
    if len(ok) < len(all_pairs):
        notes.append(f"**{len(all_pairs) - len(ok)}/{len(all_pairs)} 对没有结论**——"
                     f"任务书要求的这几对本轮未验到，不能当作已达标。")
    return rows, "dtype 对", notes


def _performance(facts, kind, status, performance, baseline, cases, perf_manifest,
                 external=None):
    """按性能形态算出表格行与说明。数据不足就 unknown，不猜。"""
    item = {"kind": kind, "label": PERF_LABEL.get(kind, kind), "status": status,
            "bands": [], "row_label": "规模档", "band_metric": "speedup",
            "notes": [], "headline": ""}
    if not performance:
        item["notes"] = ["ATK 性能轮未执行。"]
        # 任务方自带件路上 ATK 那轮**整轮不跑**：任务书的口径 ATK 表达不了
        # （分阶段耗时、中位数与 p90、Profiler 的 Kernel 总耗时），倍率只有
        # 外部工具采得到。这时把外部结果丢掉，报告会写「未执行」，
        # 而实际上性能测过了，结论还是不达标——**最糟的一种错**。
        if kind == "threshold" and external:
            criterion = (facts.get("performance") or {}).get("criterion") \
                or "任务书原话未记录"
            extra = _threshold_with_external(external, criterion)
            notes = item["notes"] + extra.pop("notes", [])
            item.update(extra)
            item["notes"] = notes
        return item

    under_test = _aclnn_times(performance)
    if not under_test:
        item["notes"] = ["ATK 那轮没采到 Device 耗时。"]
        # **外部结果不能跟着一起丢。** ATK 那轮采不到数（算子没发 kernel、
        # 报表列名对不上）与「性能没测过」是两回事，而任务书的口径本来就
        # 可能只有外部工具采得到。
        if kind == "threshold" and external:
            criterion = (facts.get("performance") or {}).get("criterion") \
                or "任务书原话未记录"
            extra = _threshold_with_external(external, criterion)
            notes = item["notes"] + extra.pop("notes", [])
            item.update(extra)
            item["notes"] = notes
        return item

    ordered = sorted(under_test.values())
    item["headline"] = (f"待验收实现 {len(ordered)} 条用例，Device 耗时中位数 "
                        f"{_median(ordered):.2f} us，"
                        f"区间 [{ordered[0]:.2f}, {ordered[-1]:.2f}] us。")
    item["notes"].append(item["headline"])

    if kind == "builtin":
        base_times = _aclnn_times(baseline) if baseline else {}
        if not base_times:
            item["notes"] += _no_baseline_notes(baseline)
        else:
            rows, label, notes = _builtin_perf(under_test, base_times, facts,
                                               cases, perf_manifest)
            item.update(bands=rows, row_label=label)
            item["notes"] += notes
    elif kind == "cross_dtype":
        rows, label, notes = _cross_dtype_perf(facts, performance, cases, perf_manifest)
        item.update(bands=rows, row_label=label)
        item["notes"] += notes
    else:
        # kind 是 none 或 threshold：都没有可比的第二组数据，但规模档仍然有意义
        # ——切分路径出问题时，大张量那一档的绝对耗时会自己露出来。
        # 有 bands 就出表，没有就算了。
        bands = (perf_manifest or {}).get("bands") or {}
        if bands:
            item.update(bands=_band_rows([(bands.get(str(i), "?"), t, None)
                                          for i, t in under_test.items()]),
                        band_metric="absolute")
        if kind == "threshold":
            criterion = (facts.get("performance") or {}).get("criterion") or "任务书原话未记录"
            if external:
                extra = _threshold_with_external(external, criterion)
                notes = item["notes"] + extra.pop("notes", [])
                item.update(extra)
                item["notes"] = notes
            else:
                # headline 是 HTML 报告里唯一会显示的那句，要求原话必须进得去，
                # 否则性能一节看起来和「任务书没要求」一模一样。
                item["headline"] = (f"任务书的性能要求：{criterion}。" + item["headline"]
                                    + "达标与否要靠外部性能结果或本机型的理论峰值，"
                                      "跑测侧不折算、不代判。")
                item["notes"].append(
                    f"任务书要求「{criterion}」。判它需要 ATK 采不到的数"
                    f"（分阶段耗时、中位数与 p90、标杆倍率），本轮**没有收到"
                    f"外部性能结果**，所以只给上面的实测耗时，不代判。"
                    f"补的办法见 references/external-perf.md。")
        elif bands:
            item["notes"].append("任务书没给性能基线，这里只出各规模档的绝对耗时，"
                                 "不出加速比，也不判达标。")
    return item


# verdict 真正读过的 stage 文件。**新增一处读取就要往这里加一行**，
# 否则 `_reconcile_stage()` 会把它报成「采了没用」。
CONSUMED_STAGE = frozenset((
    "install.json", "smoke.json", "accuracy.json", "performance.json",
    "performance_external.json", "kit_perf_exchange.json",
    "performance_builtin.json", "isolate.json", "tiling.json",
    "accuracy_cxx.json", "accuracy_cxx_noncontig.json", "env.json",
))

# 已经被上面某份加工过的中间产物，对账时不报。**只登记确实被加工进结论的**——
# 拿这张表消音一份没人读的产物，对账就白装了。
DERIVED_STAGE = frozenset((
    # collect_perf.py 的原始读数，加工结果是 performance_external.json
    "perf_p_table_raw.json",
))


def _reconcile_stage(stage):
    """出结论前对账：stage 下带数据却没被 verdict 读过的产物，列出来。

    **采了不用是最贵的一种浪费**：真机时间已经花掉，而报告的覆盖面还写错。
    实测一轮里 50 条性能采齐落盘却没进结论，报告写着「8 个场景」。

    只报不拦——多出来的产物可能是别的工具留下的中间文件，拦下来会把一次
    能出结论的验收变成没有结论。报告里如实列出，由人决定要不要接进来。
    """
    unconsumed = []
    for path in sorted(Path(stage).glob("*.json")):
        if path.name in CONSUMED_STAGE or path.name in DERIVED_STAGE:
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            continue
        # 「带数据」= 顶层有非空列表，或列表本身。只有几个标量的状态文件不算。
        rows = 0
        if isinstance(data, list):
            rows = len(data)
        elif isinstance(data, dict):
            rows = max((len(v) for v in data.values() if isinstance(v, list)),
                       default=0)
        if rows:
            unconsumed.append({"file": path.name, "rows": rows})
    return unconsumed


def _merge_kit_perf(external, kit_perf):
    """把自带件性能集并进外部性能结果，返回合并后的 external。

    **两个来源都是「一个 case×dtype 组合一个性能场景」，判据同一条，所以合成一批。**
    分开算会让报告只报其中一半：实测一轮里自带件 50 条采齐落了盘，而结论只用了
    判据表那 8 条，报告写着「8 个场景」，读者无从知道另外 50 条测过。

    每行带上 `source` 以便报告分组呈现。两边 id 撞车时以外部结果为准——判据表那批
    是任务书点名的场景，自带件是泛化集。
    """
    if not kit_perf or not (kit_perf.get("cases") or []):
        return external
    base = dict(external or {})
    rows = list(base.get("rows") or [])
    seen = {r.get("id") for r in rows}
    for row in rows:
        row.setdefault("source", base.get("source", ""))
    for case in kit_perf.get("cases") or []:
        if case.get("id") in seen:
            continue
        under, baseline = case.get("under_test_us"), case.get("baseline_us")
        ratio = (round(baseline / under, 4)
                 if under and baseline and under > 0 else None)
        row = {"id": case.get("id"), "label": case.get("id"), "stage": "",
               "under_test_us": under, "baseline_us": baseline,
               "ratio": ratio, "source": kit_perf.get("source", "自带件性能集")}
        # p90 与口径标记跟着数走。**丢掉它们等于把两批不同口径的数并成一批**，
        # 而报告上看不出哪几条不是任务书要的中位数。
        for key in ("p90_us", "min_us", "max_us", "samples",
                    "stat_kind", "stat_note"):
            if case.get(key) is not None:
                row[key] = case[key]
        row.setdefault("stat_kind", "median_p90")
        rows.append(row)
    with_base = [r["ratio"] for r in rows if r.get("ratio") is not None]
    base["rows"] = rows
    base["total"] = len(rows)
    base["with_baseline"] = len(with_base)
    if with_base:
        ordered = sorted(with_base)
        mid = len(ordered) // 2
        base["ratio_median"] = round(
            ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2, 4)
        base["ratio_min"] = round(min(with_base), 4)
        base["ratio_max"] = round(max(with_base), 4)
    sources = [s for s in (base.get("source"), kit_perf.get("source")) if s]
    base["source"] = " ＋ ".join(sources)
    base["sources"] = sources
    return base


def _perf_by_source(rows, criterion):
    """性能行按来源分组，每组给条数、倍率中位、最低、低于门槛的条数。"""
    threshold = _criterion_threshold(criterion)
    groups = {}
    for row in rows:
        groups.setdefault(row.get("source") or "未标来源", []).append(row)
    out = []
    for source, items in groups.items():
        ratios = sorted(r["ratio"] for r in items if r.get("ratio") is not None)
        if not ratios:
            out.append({"source": source, "total": len(items), "with_baseline": 0})
            continue
        mid = len(ratios) // 2
        below = [r["id"] for r in items
                 if r.get("ratio") is not None and threshold is not None
                 and r["ratio"] < threshold]
        out.append({
            "source": source,
            "total": len(items),
            "with_baseline": len(ratios),
            "ratio_median": round(
                ratios[mid] if len(ratios) % 2 else (ratios[mid - 1] + ratios[mid]) / 2, 4),
            "ratio_min": round(ratios[0], 4),
            "below_threshold": len(below),
            "below_ids": below[:20],
        })
    return out


# 逐场景表最多摆这么多行。**超出的不是丢掉，是让报告指向原始 JSON**：
# 任务书要求逐场景报告两个读数，版面塞不下时报告要说清完整数据在哪。
MAX_SCENARIO_ROWS = 60


def _perf_scenarios(rows, limit=MAX_SCENARIO_ROWS):
    """逐场景的两个读数，直接是报告要摆的形态。

    **任务书要求的是逐场景报告中位数与 90%分位**（「报告耗时中位数及90%分位
    耗时」），只出一个按来源分组的汇总等于没报。倍率一并带上，读者不必自己去除。

    `stat` 那一列标这条数的口径：任务书口径的写 `中位数`，退回自带件平均列的
    写 `平均值(非任务书口径)`——**两种口径不能在版面上长得一样**。
    """
    out = []
    for row in rows[:limit]:
        kind = row.get("stat_kind", "median_p90")
        out.append({
            "id": row.get("id"),
            "label": row.get("label") or row.get("id"),
            "source": row.get("source", ""),
            "median_us": row.get("under_test_us"),
            "p90_us": row.get("p90_us"),
            "baseline_us": row.get("baseline_us"),
            "ratio": row.get("ratio"),
            "samples": row.get("samples"),
            "stat": "中位数" if kind == "median_p90" else "平均值(非任务书口径)",
        })
    return out


def _threshold_with_external(external, criterion):
    """有外部性能结果时的 threshold 裁决。

    **本仓只做一件算术：把逐条倍率与任务书的门槛比。** 门槛从 `criterion`
    的原话里抠一个数出来；抠不到就不判。折算理论峰值、判 roofline 那类仍然
    不代判——那要机型的算力与带宽，跑测侧没有这两个数。

    倍率方向与任务书一致：**标杆耗时 / 待验收耗时，大于 1 表示更快**。
    """
    rows = external.get("rows") or []
    with_base = [r for r in rows if r.get("ratio") is not None]
    out = {"external_source": external.get("source", ""),
           "external_rows": rows,
           "external_summary": {k: external.get(k) for k in
                                ("total", "with_baseline", "ratio_median",
                                 "ratio_min", "ratio_max")},
           # 按来源分组的小结。**报告要说清每一批各测了多少条**：合成一个总数
           # 会让「判据表 8 条」和「泛化集 50 条」在报告上看起来像同一批。
           "external_by_source": _perf_by_source(rows, criterion),
           # 逐场景的中位数与 90%分位。任务书要的是这张表，按来源分组的汇总
           # 替代不了它。
           "external_scenarios": _perf_scenarios(rows),
           "external_scenarios_total": len(rows),
           "notes": []}
    # **口径不符的条目要在报告正文里说出来**，不能只藏在 JSON 字段里：
    # 平均值与中位数在同一张表上长得一样，读者无从分辨哪几条不是任务书要的数。
    off_spec = [r.get("id") for r in rows
                if r.get("stat_kind", "median_p90") != "median_p90"]
    if off_spec:
        out["stat_off_spec_ids"] = off_spec
        out["notes"].append(
            f"**{len(off_spec)} 条的读数不是任务书口径**：任务书要「耗时中位数及"
            f"90%分位耗时」，这几条的 Profiler 逐次读数认不出，只取到自带件汇总的"
            f"平均值（id {'、'.join(str(i) for i in off_spec[:12])}）。"
            f"逐场景表里标成「平均值(非任务书口径)」，倍率照算但不能当验收依据。")
    no_p90 = [r.get("id") for r in rows if r.get("p90_us") is None]
    if no_p90:
        out["notes"].append(
            f"{len(no_p90)} 条没有 90%分位读数（id "
            f"{'、'.join(str(i) for i in no_p90[:12])}）。任务书要求中位数与"
            f"90%分位并列报告，量测件按 `kernel_trace.summarize` 出数时两个都会有。")
    if len(rows) > MAX_SCENARIO_ROWS:
        out["notes"].append(
            f"逐场景表只摆前 {MAX_SCENARIO_ROWS} 条，共 {len(rows)} 条，"
            f"完整读数在 `work/stage/performance_external.json`。")
    if not with_base:
        out["headline"] = (f"任务书的性能要求：{criterion}。外部结果来自 "
                           f"{external.get('source', '未记录')}，"
                           f"但一条基线都没给，算不了倍率。")
        out["notes"].append("外部性能结果里没有 baseline_us，只有绝对耗时。"
                            "倍率型要求判不了，改报绝对值。")
        return out

    threshold = _criterion_threshold(criterion)
    ratios = [r["ratio"] for r in with_base]
    worst = min(ratios)
    out["notes"].append("倍率 = 标杆耗时 / 待验收耗时，逐条先算比值再取中位数。"
                        "**这批数与 ATK 那轮的绝对耗时口径不同，不要相除**——"
                        "ATK 采的是 Device 耗时，外部工具按任务书的调用范围采。")
    if threshold is None:
        out["headline"] = (f"任务书的性能要求：{criterion}。实测倍率中位数 "
                           f"{external.get('ratio_median')}，最低 {worst}"
                           f"（{len(with_base)}/{len(rows)} 条有基线）。"
                           f"**原话里没有可比的数值门槛，不代判。**")
        out["status"] = f"待人工判定({criterion})"
        return out

    failed = [r for r in with_base if r["ratio"] < threshold]
    out["threshold"] = threshold
    out["failed_ids"] = [r["id"] for r in failed]

    # 第二条门槛：算术平均。**逐条全过而均值不够时结论仍是不达标**，
    # 只判逐条等于只判了一半。
    mean_floor = _criterion_mean_threshold(criterion)
    mean = round(sum(ratios) / len(ratios), 4)
    out["ratio_mean"] = mean
    mean_bad = mean_floor is not None and mean < mean_floor
    if mean_floor is not None:
        out["mean_threshold"] = mean_floor
        out["notes"].append(
            f"算术平均 {mean}，要求不低于 {mean_floor}："
            f"{'未达到' if mean_bad else '达到'}。"
            f"**这是与逐条门槛并列的第二条**，逐条全过也不能省。")

    if mean_bad and not failed:
        out["status"] = f"不达标(逐条全过，但算术平均 {mean} 低于 {mean_floor})"
        out["headline"] = (f"逐条倍率全部达到 {threshold}，"
                           f"**但算术平均 {mean} 低于任务书要求的 {mean_floor}**。"
                           f"数据来自 {external.get('source', '未记录')}。")
        return out
    if failed:
        out["status"] = (f"不达标({len(failed)}/{len(with_base)} 条低于 {threshold}"
                         + (f"，且算术平均 {mean} 低于 {mean_floor}" if mean_bad else "")
                         + ")")
        out["headline"] = (f"任务书要求倍率不低于 {threshold}，"
                           f"**{len(failed)} 条没达到**，最低 {worst}。"
                           f"数据来自 {external.get('source', '未记录')}。")
        out["notes"].append("未达标用例的 id："
                            + "、".join(str(i) for i in out["failed_ids"][:20])
                            + ("…" if len(failed) > 20 else ""))
    else:
        out["status"] = (f"达标(全部 ≥ {threshold}"
                         + (f"，算术平均 {mean} ≥ {mean_floor}" if mean_floor else "")
                         + ")")
        out["headline"] = (f"任务书要求倍率不低于 {threshold}，"
                           f"{len(with_base)} 条全部达到，最低 {worst}、"
                           f"中位数 {external.get('ratio_median')}。"
                           f"数据来自 {external.get('source', '未记录')}。")
    return out


# 倍数标记：`倍`、`×`、`x`、`*`。**数字必须紧挨着它** —— `case×dtype` 这种
# 没有数字在前，不会命中。存量语料上标定过 12 条，含 5 种真实任务书写法。
_MULT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:倍|[×xX*])")
# 方向词。任务书两种口径：**性能**越大越好，**耗时**越小越好。
# 两种都要认，但门槛得换算到同一个方向上，否则达标与不达标正好反过来。
_SLOWER_SUBJECT = ("耗时", "时延", "延迟", "用时")
_SLOWER_COMPARE = ("不超过", "不高于", "不大于", "不多于", "低于", "小于",
                   "≤", "<=", "以内")


def _criterion_threshold(criterion):
    """从任务书原话里抠出逐条门槛，换算成性能倍率。抠不到返回 None，调用方不判。

    **认倍数写法，不认某一个字。** 实测五种真实写法：「0.25 倍」「> 0.25 ×
    GPU A100性能」「≥ 1.0 × 」「不低于 A100 的 1.0 倍」「性能倍率均须大于 0.25 倍」。
    只认「倍」一个字时，用 `×` 的那几份任务书一律抠不到，性能结论全部降级成
    待人工判定——实测撞过一次。

    **耗时口径自动折算。**「耗时不超过基线的 4 倍」说的是同一件事的倒数：
    倍率 = 标杆耗时 / 待验收耗时，所以门槛是 1/4。判据是「主语是耗时」且
    「比较词是不超过一类」两条同时成立，只中一条按性能口径走——**宁可按更常见的
    那一种，也不猜**。

    仍然**不做模糊匹配**：没有倍数写法就返回 None。抠错一个数比抠不到糟得多，
    前者给出的是看着像结论的错结论。百分比、roofline、「不劣于 X」都在此列。
    """
    text = str(criterion or "")
    hit = _MULT.search(text)
    if not hit:
        return None
    value = float(hit.group(1))
    if not value:
        return None
    window = text[max(0, hit.start() - 30):hit.end() + 10]
    if (any(w in window for w in _SLOWER_SUBJECT)
            and any(c in window for c in _SLOWER_COMPARE)):
        return 1.0 / value
    return value


def _criterion_mean_threshold(criterion):
    """有些任务书是**两条**门槛：逐条一个下限，再加一个算术平均下限。

    实测原话：「所有场景的性能倍率均须大于 0.25 倍，全部量化场景性能倍率的
    算术平均值须不低于 0.35 倍」。**只判前一条等于只判了一半**——逐条全过而
    均值不够时，报告会写「达标」，那是错的。

    判据是「平均」二字后面最近的那个倍数，倍数写法与 `_criterion_threshold`
    认同一套（`倍`、`×`、`x`、`*`）——两处认得不一样时，逐条门槛抠得到而均值
    抠不到，报告会漏掉一半要求且不报错。找不到「平均」就返回 None，
    表示这份要求只有逐条门槛。
    """
    text = str(criterion or "")
    at = max(text.find("平均"), text.find("均值"))
    if at < 0:
        return None
    hit = _MULT.search(text[at:])
    return float(hit.group(1)) if hit else None


def _from_root(path):
    """把跑测阶段按 CWD 记下的路径改写成相对验收现场根。绝对路径原样留。"""
    if not path:
        return "未产出"
    return str(path) if Path(path).is_absolute() else f"work/{path}"


def _env_summary(env, env_gen, device):
    """报告抬头那一行：机型、CANN、ATK 版本，以及跨版本 golden 的警告。"""
    soc = (env.get("soc") or {}).get("raw") or "未知机型"
    atk = (env.get("atk") or {}).get("version") or "未知"
    cann = env.get("cann_version") or Path(env.get("cann_home") or "?").name or "?"
    gen_atk = (env_gen.get("atk") or {}).get("version") or ""
    parts = [datetime.now().strftime("%Y-%m-%d %H:%M"), soc, f"CANN {cann}",
             f"ATK {atk}", f"卡 {device}"]
    line = " · ".join(parts)
    if gen_atk and gen_atk != atk:
        line += (f"｜⚠ golden 是在 ATK {gen_atk} 上冻的，与本机 {atk} 不同，"
                 f"结论要按跨版本打折")
    elif not gen_atk:
        line += "｜golden 的 ATK 版本未知（老用例包没带 env.gen.json）"
    return line


def _source_line(source):
    """部署那一节里「装的是哪一份源码」。装错算子目录时这一行是唯一的破绽。"""
    if not source or source.get("vcs") != "git":
        return "源码版本：不在 git 仓里，未核对"
    dirty = source.get("dirty_files") or 0
    tail = f"，工作区有 {dirty} 处未提交改动" if dirty else ""
    return (f"源码版本：`{source.get('commit', '?')}` "
            f"{source.get('date', '')} {source.get('subject', '')}"
            f"（分支 `{source.get('branch', '?')}`）{tail}")


def _exec_reject(accuracy, smoke):
    """执行失败里有没有**接口层参数校验拒绝**这一类。

    ATK 报表只说「执行失败」，不分是 aclnn 入口把参数挡回来了还是 kernel 崩了。
    两者的去向完全不同：前者八成是装错了算子目录、或这份实现根本没这个 dtype，
    后者才是要靠隔离复验测准的算子缺陷。不分开时报告只能笼统写成「精度不达标」，
    把人指向错的方向——实测踩过一次：全量四成用例全是 EZ1001 在接口层被拒，
    报告却呈现成一个普通的通过率。
    """
    lines = run_atk.reject_lines(accuracy.get("log") or "") if accuracy else []
    if not lines and smoke:
        lines = smoke.get("param_reject_lines") or []
    if not lines:
        return ""
    quoted = "；".join(f"`{line}`" for line in lines[:3])
    return ("执行失败里有**接口层参数校验拒绝**：aclnn 入口在 `GetWorkspaceSize` "
            "就把入参挡回来了，一条都没进 kernel，与「跑起来了但算错」不是一回事，"
            f"不要当成精度缺陷报出去。原文：{quoted}。"
            "先核「部署」那节的源码版本是不是待验收的那份——不同算子目录能实现"
            "同一套 aclnn 接口，装错目录时符号照样可见，表现就是整类 dtype 全挂。")


def _layout_stats(accuracy, failed_ids, acc_false_ids):
    """按内存布局把通过率分成两组，返回 {"contiguous": {...}, "noncontiguous": {...}}。

    分组来自 `stage/accuracy.json` 的 `layout` 字段（`run_atk.py` 落的）。
    **没有这个字段就不分组**：没有它就无从知道哪几条是非连续的，猜一个划分出来的
    两个通过率比不分组更糟——看报告的人会当成实测。
    """
    layout = (accuracy or {}).get("layout") or {}
    ids = {"contiguous": set(layout.get("contiguous_ids") or []),
           "noncontiguous": set(layout.get("noncontiguous_ids") or [])}
    if not ids["noncontiguous"]:
        return {}
    bad = set(failed_ids or []) | set(acc_false_ids or [])
    out = {}
    for key, group in ids.items():
        total = len(group)
        failed = len(group & bad)
        out[key] = {
            "total": total,
            "passed": total - failed,
            "exec_failed_ids": sorted(group & set(failed_ids or [])),
            "accuracy_false_ids": sorted(group & set(acc_false_ids or [])),
            "pass_rate": round((total - failed) / total * 100, 2) if total else 0.0,
        }
    return out


def _noncontig_note(facts, stats):
    """内存布局分组的说明。连续与非连续在同一轮里混跑，通过率分组报、不合并。

    合成一个数会把「连续下好好的、非连续下挂一片」这种最常见的形态抹平成一个中间数。

    **不替算子作者定责。** 非连续路径上 aclnn 会插入 CANN 内置的辅助算子
    （StridedSlice 等）做「非连续转连续」。只在这一组失败的用例，究竟是本算子
    传参不当还是内置算子自己越界，要看 mssanitizer 报的越界发生在哪个 kernel、
    那个 kernel 落在待验收算子包下还是 CANN 装机目录下。
    """
    if not (facts.get("non_contiguous") or {}).get("required"):
        # **不替任务书说话。** 这里只知道 facts 填了什么，不知道任务书要求什么——
        # 从 `required: false` 推出「任务书没要求」，填错时报告就在替一个漏填背书。
        # 实测两个独立会话各错一次，两次的任务书参数表里「非连续Tensor」列都写着支持。
        return ["`facts.non_contiguous.required` 填的是 false，本轮没有按布局分组。"
                "**这不等于任务书没要求**——该列的填法照任务书参数表的"
                "「非连续Tensor」一列，核完再看这一节的结论。"]
    if not stats:
        return ["**任务书要求支持非连续输入，但本轮结果里没有布局分组。** "
                "`stage/accuracy.json` 缺 `layout` 字段：要么 facts 的 "
                "`non_contiguous.ratio` 没填，要么这一轮是旧版 `run_atk.py` 跑的。"
                "补：填好 `non_contiguous` 后重跑 `run_atk.py --mode accuracy`。"]
    contig, noncontig = stats.get("contiguous", {}), stats.get("noncontiguous", {})
    note = (f"**布局分组**（同一轮混跑，通过率分开报）："
            f"连续 {contig.get('passed', 0)}/{contig.get('total', 0)} 通过"
            f"（{contig.get('pass_rate', 0.0)}%），"
            f"非连续 {noncontig.get('passed', 0)}/{noncontig.get('total', 0)} 通过"
            f"（{noncontig.get('pass_rate', 0.0)}%）。"
            "只在非连续组失败的用例，非连续路径会走 CANN 内置的辅助算子，"
            "定责要看 mssanitizer 报的越界 kernel 落在哪个目录下。")
    return [note]


def _accuracy_notes(isolate, cascade_ids, unchecked_ids, effective_total,
                    effective_rate, failed_ids):
    """精度表之外要说清楚的几段。都是「这个数为什么是这个数」，不是结论。"""
    notes = []
    if cascade_ids:
        aicore = isolate.get("aicore_evidence") or []
        notes.append(
            f"ATK 首轮报的「执行失败」里，有 {len(cascade_ids)} 条**根本没跑到执行**。"
            f"ATK 一个进程串行跑完整批，前面某一条触发 aicore 异常把 device context "
            f"打成错误态之后，排在它后面的用例连输入都搬不上卡，在初始化阶段就被挡回来"
            f"（`ACL stream synchronize failed`）——这不是它们算错或崩了，是压根没测。"
            f"隔离复验换新进程、以与全量相同的口径把它们重跑了一遍，"
            f"**{len(cascade_ids)} 条全部通过**，所以计入通过：分母 {effective_total}，"
            f"通过率 {effective_rate}%，执行失败 {len(failed_ids)} 条。")
        notes.append(
            ("把 device 打废的是 aicore 异常（日志命中 "
             + "、".join(f"`{w}`" for w in aicore) + "）。") if aicore
            else "把 device 打废的成因**未定**：隔离复验日志里没有 aicore 关键词，"
                 "不要写成 aicore 异常。")
    batch_only = isolate.get("batch_only") or []
    if batch_only:
        notes.append(
            f"上面那批里有 {len(batch_only)} 条是**批内污染**（id "
            + "、".join(str(i) for i in batch_only[:12])
            + ("…" if len(batch_only) > 12 else "")
            + "）：它们跑到了执行才挂，但各自单独跑一遍全部通过。"
            f"这说明挂它们的是同批里**前面某条自己没报错的**用例——算子越界写时，"
            f"写的那条算得对、也不报错，坏掉的显存要等后面某条读到才炸，"
            f"于是罪名落在受害者头上。**不要按这些 id 去报缺陷，作者复现不出来**；"
            f"要定位真正的越界者，得按批次顺序二分。")
    if unchecked_ids:
        notes.append(
            f"另有 {len(unchecked_ids)} 条执行失败**未做隔离复验**"
            f"（`--max-isolate` 截断，或轮数用尽）。它们既没被证明有缺陷，"
            f"也没被证明只是没跑到执行，本轮记 `unknown`。调大 `--max-isolate` 与 "
            f"`--max-isolate-rounds` 重跑隔离复验能把它们分清；轮数用尽说明真实"
            f"失败特别多，那本身就是个信号。")
    return notes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pkg", default="../input",
                        help="用例包目录，facts/cases/golden/perf 都从这里读")
    parser.add_argument("--stage", default="stage", help="各阶段 JSON 落在哪")
    parser.add_argument("--outdir", default="../report",
                        help="verdict.json、report.md、index.html 落在哪")
    parser.add_argument("--repro", default="../repro", help="最小复现包落在哪")
    parser.add_argument("--env-sh", default="evidence/env.sh")
    parser.add_argument("--devices", default="",
                        help="本轮占的卡号，只写进报告抬头。默认从 "
                             "stage/accuracy.json 读跑测那轮实际用的卡——"
                             "`--devices auto` 之后卡是脚本现挑的，人抄一遍必错")
    parser.add_argument("--no-repro", action="store_true",
                        help="只出报告，不生成最小复现包")
    args = parser.parse_args()

    pkg, stage = Path(args.pkg), Path(args.stage)
    facts = _load(pkg / "facts.json")
    install = _load(stage / "install.json")
    smoke = _load(stage / "smoke.json", required=False)
    if facts is None or install is None:
        return 3
    accuracy = _load(stage / "accuracy.json", required=False)
    # 连续与非连续在同一轮里混跑，布局分组在 accuracy.json 的 `layout` 字段里。
    # 分组统计在下面 `_layout_stats()` 算，两个通过率分开报、不合并。
    performance = _load(stage / "performance.json", required=False)
    # 外部性能结果。任务书的口径超出 ATK 能表达的范围时，倍率只能来自它。
    # 缺它时**不猜**，报告如实写「未收到」。
    external = _load(stage / "performance_external.json", required=False)
    # 自带件自己的性能集。**采了就要用**：它与判据表那批是同一种性能场景，
    # 判据同一条，分开算会让报告只报其中一半。
    kit_perf = _load(stage / "kit_perf_exchange.json", required=False)
    external = _merge_kit_perf(external, kit_perf)
    unconsumed = _reconcile_stage(stage)
    for item in unconsumed:
        print(f"对账      stage/{item['file']} 有 {item['rows']} 条数据，"
              f"本次结论没有读它", file=sys.stderr)
    baseline = _load(stage / "performance_builtin.json", required=False)
    isolate = _load(stage / "isolate.json", required=False)
    # A2.6 与 A3.5 的产物。**没有它们报告就漏掉最关键的一层**：存在同名内置 tiling 时
    # ATK 测的是 CANN 旧实现，独立执行器才用得上待验收那份，两者的失败集合不同。
    tiling = _load(stage / "tiling.json", required=False)
    cxx = _load(stage / "accuracy_cxx.json", required=False)
    # 独立执行器也分两种内存布局，与 ATK 那侧同名同规矩：连续轮在 accuracy_cxx.json，
    # 非连续轮在 accuracy_cxx_noncontig.json，两个通过率分开报、不合并。
    cxx_noncontig = _load(stage / "accuracy_cxx_noncontig.json", required=False)
    env = _load(stage / "env.json", required=False)
    # 生成侧 S4 收尾把它归进 gen/。老用例包平铺在根，两处都认——认不出来只是
    # 报告的环境栏写「未知」，不报错，跨版本 golden 漂移就此查不出来。
    env_gen = (_load(pkg / "gen" / "env.gen.json", required=False)
               or _load(pkg / "env.gen.json", required=False))
    # 执行剖面。生成侧判一次写进 facts，这里只读——两处各判一次必然漂。
    backend = str(facts.get("backend") or "aclnn")
    _GROUP["attr"] = str(facts.get("group_attr") or "")
    _GROUP["labels"] = list(facts.get("group_labels") or [])
    cases, cases_from = _cases_of(pkg, facts)
    perf_manifest = _load(pkg / "perf" / "manifest.json", required=False)

    if not args.devices:
        args.devices = ((accuracy or {}).get("device")
                        or (performance or {}).get("device") or "未记录")

    failed_ids = accuracy.get("failed_ids", []) if accuracy else []
    cascade_ids, unchecked_ids = [], []
    if isolate and isolate.get("checked"):
        # aicore 异常会让同批次后续用例连带失败。隔离复验过的才分得清真假；
        # not_checked 里的没判定过（--max-isolate 截断，或迭代轮数用尽），
        # 是 unknown，**不能算真实失败**。
        cascade_ids = isolate.get("cascade", [])
        unchecked_ids = isolate.get("not_checked", [])
        # 隔离复验里判成「跑起来了但算错」的，**归因是精度不符，不是执行失败**。
        # 它们在全量轮是被连带打挂的，那一轮没有精度判定，所以只会出现在这里。
        # 不摘出来的话，一条算错的用例会被报成执行失败，复现包和归因都指错方向。
        iso_wrong = set(isolate.get("accuracy_false") or [])
        # 单独复跑通过的那些：跑到执行才挂，但单独跑没事——挂它们的是前面某条
        # **自己没报错的**用例（算子越界写时，写的那条算得对也不报错）。
        # **不能算成缺陷**：报上去算子作者按 id 复现不出来。与 cascade 同列，
        # 但成因不同，报告里分开说。
        batch_only = set(isolate.get("batch_only") or [])
        known = set(cascade_ids) | set(unchecked_ids) | iso_wrong | batch_only
        failed_ids = [i for i in failed_ids if i not in known]
        cascade_ids = sorted(set(cascade_ids) | batch_only)
    groups = _group_failures(cases, failed_ids)
    perf_status, perf_kind = _perf_status(facts, accuracy, performance, baseline,
                                          cases, perf_manifest, external)
    acc_kind, acc_dir = _acc_baseline(pkg / "golden", facts)
    print(f"用例清单  {cases_from}（{len(cases)} 条）")

    # 分母只减掉真正没测到的（轮数用尽的 not_checked）。连带失败在隔离复验里
    # 以与全量相同的口径重跑通过了，分子分母都要算它——**只加分母不加分子，
    # 通过率会凭空掉下来**（实测这一轮 42/252 = 16.7%，而真实是 236/252）。
    total = accuracy.get("total", 0) if accuracy else 0
    matched = accuracy.get("matched", 0) if accuracy else 0
    effective_matched = matched + len(cascade_ids)
    effective_total = total - len(unchecked_ids)
    effective_rate = (round(100.0 * effective_matched / effective_total, 2)
                      if effective_total else 0.0)

    acc_false_ids = _acc_false_ids(accuracy or {}, isolate or {}, failed_ids, cascade_ids,
                                   unchecked_ids)
    # 布局分组要在总结论之前算出来：非连续是独立的验收维度，总结论要看它。
    layout_stats = _layout_stats(accuracy, failed_ids, acc_false_ids)
    nc_stats = layout_stats.get("noncontiguous") or {}
    nc_failed = bool(nc_stats) and nc_stats.get("pass_rate", 0.0) < 100.0

    # 总结论要同时看精度与性能。任务书给了性能基线却没验到，
    # 那一条要求就是没验，不能算全通过。
    if not accuracy:
        overall = "未裁决(精度未执行)"
    # 任务书要求支持非连续输入时，非连续是独立的验收维度。**两组要分开归因**：
    # 只写「非连续不达标」会在连续组也挂时把作者指向非连续路径，而真正的缺陷
    # 在算子主体上——实测 Median 两种布局挂的是同样 7 条。
    elif not accuracy.get("passed") and nc_failed and (
            (layout_stats.get("contiguous") or {}).get("pass_rate", 0.0) < 100.0):
        overall = "不通过(连续与非连续两种布局精度都不达标)"
    elif nc_failed and accuracy.get("passed"):
        overall = "不通过(仅非连续输入精度不达标)"
    elif not accuracy.get("passed") and effective_rate == 100.0 and not failed_ids:
        # 隔离复验把首轮那批全部重判成批内污染或连带失败——**逐条复跑都通过**，
        # 没有一条算错。写「精度不达标」与正文的 100% 直接打架，读报告的人
        # 只能二选一信。但也不能写「通过」：**有东西悄悄把设备状态搞坏了**，
        # 只是挂的不是它。所以据实写清楚，把判断留给人。
        overall = (f"不通过(批内污染 {len(cascade_ids)} 条，逐条复跑都通过，"
                   f"污染源未定位)")
    elif not accuracy.get("passed"):
        overall = "不通过(连续输入精度不达标)"
    elif perf_kind == "none" or perf_status == "已评级":
        overall = "通过"
    # 性能已经真判出来的，总结论要跟着判，不能一律收进「待定」。
    # 「待定」是留给判不了的（没有外部结果、原话里抠不出门槛），
    # 把判出来的不达标写成待定，读报告的人会以为还没测。
    elif perf_status.startswith("不达标"):
        overall = f"不通过(性能不达标：{perf_status[3:].strip('()')})"
    elif perf_status.startswith("达标"):
        overall = "通过"
    else:
        overall = f"精度通过·性能待定（{perf_status}）"

    by_dtype = _by_dtype(cases, acc_false_ids, failed_ids, cascade_ids,
                         unchecked_ids) if accuracy and cases else []
    # 复现包要装的是**这一轮没过的全部用例**：执行失败的和算错的都要能重跑。
    # 只装执行失败的话，一个「全部跑起来了但有几条算错」的算子——最常见的那种
    # 缺陷形态——复现包会是空的。
    repro_ids = sorted(set(failed_ids) | set(acc_false_ids))
    # 两处对账。**只报不改**：这里改数就是在掩盖上游的问题。
    mismatch = ""
    if accuracy and cases and len(cases) != total:
        mismatch = (f"用例包里有 {len(cases)} 条用例，ATK 报告的总数是 {total} 条。"
                    f"下表的分母是用例包，两者不等时以用例包为准，"
                    f"差额说明有用例没进这一轮。")
    table_matched = next((r["matched"] for r in by_dtype if r.get("total_row")),
                         by_dtype[0]["matched"] if by_dtype else None)
    if not mismatch and table_matched is not None and table_matched != effective_matched:
        # dtype 表是从逐用例 id 算的，effective_matched 是「全量轮通过 + 连带复跑
        # 通过」。两者对不上
        # 说明逐用例判定没取全——真机上撞过一次：accuracy_load 的精度判定挂在加载
        # 节点的列上，取错列时两张失败表都是空的，表会显示 100% 而总数说不达标。
        # **只报不改**，改数就是在掩盖它。
        mismatch = (f"逐用例判定算出通过 {table_matched} 条，全量轮加隔离复验"
                    f"复核出 {effective_matched} 条（ATK 汇总 {matched} + 连带复跑"
                    f"通过 {len(cascade_ids)}），两者不一致。下表按逐用例判定出，"
                    f"差额说明有用例的判定没取到，报告的失败清单与复现包可能不全。")

    perf = _performance(facts, perf_kind, perf_status, performance, baseline,
                        cases, perf_manifest, external)
    if accuracy and not accuracy.get("passed") and performance:
        perf["notes"].insert(0, "精度未通过，本轮性能**不做评级**。下面是已采集到的"
                                "耗时，只作缺陷修复后的对照参考，不构成达标或劣化结论。")

    shadowed = (tiling or {}).get("shadowed") or []
    # 独立执行器跳过的用例 = **没跑过**。它跳过整整一类 dtype 时，报告里那一行
    # 的 ✓ 只能来自 ATK 轮，而有竞争 tiling 时 ATK 轮测的不是待验收实现。
    # 实测 Roll：uint32 17 条全被跳过，报告仍给 uint32 打 100.0% ✓。
    _cases_by_id = {c["id"]: c for c in cases}
    _dtype_total = {}
    for case in cases:
        _name = _dtype_of(case)
        _dtype_total[_name] = _dtype_total.get(_name, 0) + 1

    def _cxx_round(data):
        """把独立执行器的一轮结果整理成报告块，返回 (块, 整类未覆盖的 dtype)。

        **跳过的用例 = 没跑过**，不是「跑了没问题」。整类 dtype 被跳光时，
        报告里那一行的 ✓ 只能来自 ATK 轮，而有竞争 tiling 时 ATK 轮测的
        不是待验收实现。实测 Roll：uint32 17 条全被跳过，报告仍打 100.0% ✓。
        """
        if not data:
            return None, []
        skipped = data.get("skipped") or []
        skipped_ids = {x["id"] for x in skipped if isinstance(x, dict) and "id" in x}
        per_dtype = {}
        for case_id in skipped_ids:
            case = _cases_by_id.get(case_id)
            if case is not None:
                key = _dtype_of(case)
                per_dtype[key] = per_dtype.get(key, 0) + 1
        uncovered = sorted(k for k, v in per_dtype.items()
                           if v >= _dtype_total.get(k, 0) > 0)
        round_total = data.get("total", 0)
        batch_only = data.get("batch_only") or []
        # **口径要与 ATK 那侧一致**：批内污染在单独复跑里通过了，不是缺陷，
        # 分子要算它。直接摆 `pass_rate` 会把改判前的原始数与 ATK 的修正后
        # 通过率并列，看上去独立执行器差一大截，其实差的全是已识别的污染。
        ok = data.get("matched", 0) + len(batch_only)
        return {
            "executed": True,
            # **scope 以 run_cxx 落的账为准。** 按「cxx 条数 >= ATK 条数」反推，
            # 一旦有用例被跳过就会把全量误标成「仅 ATK 判失败的」，读报告的人
            # 据此低估这一轮的证据力。老报告没有这个字段时才退回旧的推断。
            "scope": data.get("scope") or (
                "全量" if round_total >= total > 0 else "仅 ATK 判失败的"),
            "total": round_total,
            "passed": ok,
            "pass_rate": round(ok / round_total * 100, 2) if round_total else 0.0,
            "raw_pass_rate": data.get("pass_rate", 0.0),
            "batch_only_count": len(batch_only),
            "exec_failed_ids": data.get("exec_failed_ids") or [],
            "accuracy_false_ids": data.get("accuracy_false_ids") or [],
            "skipped_count": len(skipped),
            "skipped_ids": sorted(skipped_ids),
            # dtype -> [跳过数, 该 dtype 总数]。整类跳光的才进 uncovered_dtypes。
            "skipped_by_dtype": {k: [v, _dtype_total.get(k, 0)]
                                 for k, v in sorted(per_dtype.items())},
            "uncovered_dtypes": uncovered,
        }, uncovered

    cxx_block, cxx_uncovered = _cxx_round(cxx)
    cxx_nc_block, cxx_nc_uncovered = _cxx_round(cxx_noncontig)
    # 两轮的缺口取并集：任一轮没覆盖，那个布局下这个 dtype 就没测过。
    cxx_uncovered = sorted(set(cxx_uncovered) | set(cxx_nc_uncovered))
    if shadowed and cxx_uncovered:
        for row in by_dtype:
            if not row.get("total_row") and row.get("dtype") in cxx_uncovered:
                row["uncovered"] = True

    # **布局维度的缺口。** 任务书要求非连续时，连续与非连续都得在权威路径上
    # 跑过；少一轮，那个布局就只有 ATK 测过，而 ATK 测的是被抢走 tiling 的
    # 内置实现。实测 Roll 少的正是连续轮，报告上一个字都看不出来。
    nc_required = bool((facts.get("non_contiguous") or {}).get("required"))
    missing_layouts = []
    if shadowed:
        if not cxx_block:
            missing_layouts.append("连续")
        if nc_required and not cxx_nc_block:
            missing_layouts.append("非连续")

    # **覆盖缺口能翻掉「通过」。** 验收依据是独立执行器时，它没跑过的就是没测过；
    # 此时给「通过」等于拿 ATK 轮（测的是内置实现）的数当验收结论。
    # 已经是「不通过」的不动——缺口只会让结论更弱，不会让它变强。
    if (shadowed and (cxx_block or cxx_nc_block) and overall == "通过"
            and (cxx_uncovered or missing_layouts)):
        why = []
        if cxx_uncovered:
            why.append("未覆盖 " + "、".join(cxx_uncovered))
        if missing_layouts:
            why.append("未跑" + "与".join(missing_layouts) + "布局")
        overall = f"结论不可用(验收依据是独立执行器，但它{'；'.join(why)})"

    verdict = {
        # 自带件路的 facts 由 A1 从任务书写出来，只保证有 aclnn_name。
        "op": facts.get("op") or facts.get("aclnn_name"),
        "aclnn_name": facts.get("aclnn_name"),
        "overall": overall,
        "env": {"summary": _env_summary(env, env_gen, args.devices),
                "device": args.devices,
                "atk": (env.get("atk") or {}).get("version", ""),
                "atk_golden": (env_gen.get("atk") or {}).get("version", ""),
                "soc": (env.get("soc") or {}).get("raw", "")},
        "accuracy": {
            "executed": bool(accuracy),
            "total": accuracy.get("total", 0),
            "succeeded": accuracy.get("succeeded", 0),
            "failed": accuracy.get("failed", 0),
            "matched": accuracy.get("matched", 0),
            "pass_rate": accuracy.get("pass_rate", 0.0),
            "effective_total": effective_total,
            "effective_pass_rate": effective_rate,
            "passed": accuracy.get("passed", False),
            "exec_failed_ids": failed_ids,
            "accuracy_false_ids": acc_false_ids,
            "cascade_failed_ids": cascade_ids,
            "unchecked_failed_ids": unchecked_ids,
            "failed_by_dtype": dict(groups),
            "by_dtype": by_dtype,
            # 表头由分组轴定，render.py 照写。写死成 dtype 时纯 attr 用例的
            # 报告会说「dtype: csr」，读者以为 csr 是个 dtype。
            "group_by": _group_key(),
            "baseline": acc_kind,
            "baseline_dir": acc_dir,
            "count_mismatch": mismatch,
            "exec_reject": _exec_reject(accuracy or {}, smoke or {}),
            "summary": _acc_summary(acc_kind, acc_dir, facts, accuracy),
            "notes": (_accuracy_notes(isolate, cascade_ids, unchecked_ids,
                                      effective_total, effective_rate,
                                      failed_ids)
                      + _noncontig_note(facts, layout_stats)) if accuracy else [],
            # 布局分组随精度块走。**分组为空不等于没测非连续**，也可能是
            # facts 没要求——两者的区别在 required 上，别只看 by_layout 是否为空。
            "by_layout": layout_stats,
        },
        "accuracy_by_layout": {
            "required": bool((facts.get("non_contiguous") or {}).get("required")),
            "grouped": bool(layout_stats),
            "slice_ratio": ((accuracy or {}).get("layout") or {}).get("slice_ratio"),
            "attr": ((accuracy or {}).get("layout") or {}).get("attr"),
            **layout_stats,
        },
        # 采了没进结论的产物。**空列表才是正常**：非空说明真机时间花了而结论没用上。
        "unconsumed_stage": unconsumed,
        "performance": perf,
        # tiling 归属决定 A3 的结论能不能当验收依据；有竞争时以独立执行器为准。
        "executors": {
            # 剖面决定 A2.6 与 A3.5 做不做。**「不适用」与「没做」要分开记**：
            # 两者在报告上长得一样，处置却完全不同——前者是这条路上本来就没有
            # 这一层，后者是漏跑，要回去补。
            "profile": backend,
            "applicable": backend != "npu",
            "not_applicable_reason": ("非 aclnn 接口：没有两段式 C 接口，"
                                      "tiling 不存在同名竞争，C++ 执行器拼不出调用"
                                      if backend == "npu" else ""),
            "tiling_checked": bool(tiling),
            "shadowed_op_types": shadowed,
            "atk_tiling": "CANN 内置" if shadowed else "算子包自带",
            "cxx_tiling": "算子包自带",
            "verdict_basis": "独立执行器" if shadowed and cxx_block else "ATK",
            "cxx": cxx_block,
            "cxx_noncontig": cxx_nc_block,
            "missing_layouts": missing_layouts,
        },
        "deploy": {
            "op_dir": install.get("op_dir", ""),
            "target": install.get("target", ""),
            "vendor_dir": install.get("vendor_dir", ""),
            "symbol": install.get("symbol", ""),
            "source": install.get("source") or {},
            "source_line": _source_line(install.get("source")),
            "build_seconds": (install.get("build") or {}).get("elapsed_seconds"),
        },
        "inferred_facts": [p["name"] for p in facts.get("params", [])
                           if p.get("source") == "inferred"],
        # 证据链的路径**相对验收现场根**写，因为报告在 report/，而这些东西在
        # work/ 下——按 CWD 写的话读者从报告里点不开。
        "evidence": {
            "构建与安装日志": "work/evidence/",
            "精度报告": _from_root(accuracy.get("report") if accuracy else None),
            "性能报告": _from_root(performance.get("report") if performance else None),
            "阶段 JSON": f"work/{stage.name}/",
            "ATK 原始产出": "work/atk_output/",
        },
        "repro": {"dir": "repro/", "case_ids": repro_ids,
                  "failed_cases": len(repro_ids), "commands": []},
    }

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / "verdict.json"

    if args.no_repro:
        verdict["repro"]["commands"] = ["accuracy"]
    else:
        verdict["repro"]["commands"] = make_repro.build(
            pkg, verdict, args.repro, args.env_sh, pkg / "facts.json")

    with open(out, "w", encoding="utf-8") as handle:
        json.dump(stage_clock.stamp(verdict), handle, ensure_ascii=False, indent=2)
    md, page = render.write_all(verdict, outdir)

    print(f"总结论    {overall}")
    if accuracy:
        # **抬头报的是折算过连带失败之后的数**，与下面 dtype 表的合计行、
        # 与复现包的条数三处一致。报 ATK 那轮的原始数会和它们对不上——实测
        # 抬头 79/252、合计 213/252 同屏出现，读的人只能自己猜哪个算数。
        # 原始数没丢，`verdict.json` 的 `matched` / `pass_rate` 仍是它。
        print(f"精度      {effective_matched}/{effective_total} "
              f"通过率 {effective_rate}%")
        if cascade_ids:
            print(f"          （ATK 那轮 {accuracy.get('matched')}/"
                  f"{accuracy.get('total')}，隔离复验判出 {len(cascade_ids)} 条"
                  f"是批内连带、重跑全通过，已计入）")
        if unchecked_ids:
            print(f"          （另有 {len(unchecked_ids)} 条未判定，不进分母）")
        for row in by_dtype:
            print(f"  {row['dtype']:<12} {row['matched']}/{row['total']}  "
                  f"{row['rate']}%"
                  + ("  —未覆盖（独立执行器没跑过这一类）" if row.get("uncovered") else ""))
    print(f"性能      {perf_status}")
    print(f"写入      {out}、{md}、{page}")
    if not args.no_repro:
        print(f"复现包    {args.repro}（{len(repro_ids)} 条未通过用例："
              f"{len(failed_ids)} 条执行失败 + {len(acc_false_ids)} 条精度不符）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
