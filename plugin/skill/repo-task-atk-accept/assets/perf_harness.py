#!/usr/bin/env python3
"""性能量测骨架：任务方没给性能脚本，或给的那份口径不合任务书时照抄改。

**通用的部分已经写好，算子相关的只有一个函数。** 改 `build_call` 就够了，
别的不要动——预热与采样的循环、计时范围、统计量、输出格式四件事一旦各写各的，
两轮跑出来的数就没法比。

产出直接是 `collect_perf.py` 的交换格式，跑完这样接上：

    python3 <skill>/scripts/perf_lock.py --stage stage --who "外部量测" --devices 0 -- \\
        python3 perf_harness.py --cases <用例包>/perf/cases.json \\
        --baseline <标杆表>.json --device 0 -o /tmp/ext.json
    python3 <skill>/scripts/collect_perf.py --from /tmp/ext.json \\
        -c ../input/cases.json --facts ../input/facts.json \\
        -o stage/performance_external.json

**外层那把锁不要省。** 性能采集期间本现场并发第二个 NPU 任务时，两轮的数互相
污染而退出码都是 0，判据见 references/external-perf.md「现场级排他锁」。

四条口径在下面，改之前先核任务书：

| 口径 | 取值 | 为什么 |
| --- | --- | --- |
| 预热 | 10 次 | 排除首次编译与首次搬运 |
| 采样 | 30 次 | **任务书通常写成硬性下限**，低于它的数不能当验收依据 |
| 计时范围 | 一次 `invoke()` 的两端 | 描述符与 workspace 在循环外建好，只量调用本身 |
| 统计量 | 中位数与 p90 | **不用平均值**：首次调用与偶发抖动是离群点，平均值被它们拉走 |

**跑之前先核任务书写的次数。** 10/30 是常见值，不是本骨架定的——实测一例写着
「NPU 侧每个 case 至少预热 10 次、正式采样 30 次」。任务书要求更多就调大。

第一次预热顺带计时当探针，开跑就打印这条要跑多久，长场景不至于跑到一半才发现。
`--budget-s` 与 `--early-stop-factor` 能减次数，**默认都关**：它们是摸底用的
（先看量级再决定要不要跑完整轮），开了之后产出打 `off_spec` 标记，
`collect_perf.py` 拒收。
"""

import argparse
import json
import math
import re
import statistics
import sys
from pathlib import Path

WARMUP = 10
SAMPLES = 30
# 上面两个是**上限**，不是固定值。它们按微秒级算子定：那种量级下 40 次调用不到
# 一秒，采够了 p90 才稳。算子单次几十秒时同一个口径就是每个场景半小时以上，
# 实测一例：单次 400 ms 的场景 16 秒，同一批里单次 ~40 s 的场景 27 分钟起。
MIN_SAMPLES = 3            # 再少 p90 没有意义
# **下面两条默认关闭，因为减采样次数会让数不符合验收口径。**
# 任务书那类文档通常把次数写成硬性下限（实测一例：「NPU 侧每个 case 至少预热
# 10 次、正式采样 30 次」），低于它的数不能当验收依据。两条只在**摸底**时开：
# 想先知道量级、要不要继续跑完整轮。开了之后产出会打上 `off_spec` 标记，
# `collect_perf.py` 认这个标记并拒绝收编。
BUDGET_S = 0               # 每条用例的采样墙钟预算（秒），0 = 不限
STOP_FACTOR = 0            # 与基线差这么多倍就只采最少次数，0 = 关掉

# 哪几条不跑满。**两条同时成立才算**，缺一条都要跑满：
#
#   ① 已经明显不达标：探针倍率比任务书门槛还低一个数量级（SETTLED_MARGIN）
#   ② 这些明显不达标的用例，按口径跑完还要很久（ASK_AFTER_S）
#
# 为什么是合取。只看②会把「算子本来就要跑 20 分钟但性能达标」也算进来——那是正常
# 验收，必须采满；只看①会为一条 10 秒就跑完的用例省时间，省下的还不够记一笔。
# 合取之后剩下的才是同一件事：**结论已经没有悬念，而把它测精确还要付很多时间。**
#
# **这一条不问用户。** 跳过的是采样次数，不是验收场景——那几条照样出「不达标」的
# 结论，只是数来自探针而不是完整采样。验收范围没缩，所以没有要用户决定的东西。
#
# 门槛从任务书原话里抠，抠不到就不问——判据不猜，与 verdict.py 同一条规矩。
# 倍率方向也与它一致：**标杆耗时 / 待验收耗时，大于门槛才达标。**
#
# 两个常数都是**制定值不是测量值**：
#   SETTLED_MARGIN=10 —— 差一个数量级，再采多少次也翻不过来。实测一例倍率
#     0.0007 对门槛 0.25，差了 346 倍，远在这条线之外
#   ASK_AFTER_S=600 —— 自带件性能集 50 条分片并行实测 2 分 41 秒（正常轮的量级），
#     任务书判据表 8 场景按口径 1 小时以上（失控的量级）；整链冷启动 15 分钟，
#     性能一步超过整链一半即失衡
SETTLED_MARGIN = 10
ASK_AFTER_S = 600


# 与 `verdict.py` 的 `_criterion_threshold` **必须认同一套写法**：两处不一致时，
# 这里说「结论已定，问要不要停」而报告说「待人工判定」，两边都言之凿凿。
# `tests/test_external_perf.py` 拿同一份语料把两处钉在一起。
_MULT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:倍|[×xX*])")
_SLOWER_SUBJECT = ("耗时", "时延", "延迟", "用时")
_SLOWER_COMPARE = ("不超过", "不高于", "不大于", "不多于", "低于", "小于",
                   "≤", "<=", "以内")


def criterion_threshold(criterion):
    """从任务书原话里抠出倍率门槛，换算成性能倍率。抠不到返回 None，调用方不判。

    认倍数写法不认某一个字：「0.25 倍」「> 0.25 × GPU A100性能」「≥ 1.0 ×」
    都要认得。耗时口径（「耗时不超过基线的 4 倍」）折算成 1/4——倍率的定义是
    标杆耗时 / 待验收耗时，两种口径互为倒数。

    没有倍数写法就返回 None，**不做模糊匹配**：百分比、roofline、「不劣于 X」
    这些抠不出可比的数，抠错一个比抠不到糟得多。
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


def settled_cases(rows, threshold, margin=SETTLED_MARGIN):
    """结论已经没有悬念的那几条：倍率比门槛还低 `margin` 倍。

    **门槛附近的一条都不算**。那些正是要采满才判得准的，把它们算进来就会
    去劝用户跳过最该跑的用例。
    """
    if not threshold:
        return []
    return [r for r in rows
            if r.get("perf_ratio") is not None
            and r["perf_ratio"] < threshold / margin]


def plan_sampling(probe_us, baseline_us, warmup, samples,
                  budget_us=BUDGET_S * 1e6, stop_factor=STOP_FACTOR):
    """按探针那一次的耗时决定实际采几次。返回 (warmup, samples, 理由)。

    **只减不增**：传进来的 warmup/samples 是上限，用户显式调大才会更多。

    两条判据各管一件事：
      - 预算：单次越贵，采样次数越少，保证一条用例不超过墙钟预算
      - 早停：与基线差 `stop_factor` 倍时结论已经定了，多采不会翻过来。
        任务书那类「耗时不超过基线 4 倍」的门槛，差 50 倍时早已越过一个数量级
    """
    reason = ""
    if not stop_factor and not budget_us:
        return warmup, samples, reason
    if stop_factor and baseline_us and probe_us > stop_factor * baseline_us:
        return (min(warmup, 1), min(samples, MIN_SAMPLES),
                f"与基线差 {probe_us / baseline_us:.0f} 倍，结论已定，只采最少次数")
    if budget_us and probe_us > 0:
        fits = int(budget_us // probe_us)
        if fits < samples:
            samples = max(MIN_SAMPLES, fits)
            warmup = min(warmup, 1 if probe_us > 1e6 else warmup)
            reason = f"单次 {probe_us / 1000:.1f} ms，按 {budget_us / 1e6:.0f} s 预算采 {samples} 次"
    return warmup, samples, reason


def percentile_at_least(values, ratio):
    """不高于返回值的采样占比达到 `ratio` 的最小观测值。

    判据取自任务书对 90%分位的定义原话：「即约90%的正式采样耗时不高于该值」。
    **不做线性插值**——插出来的数不是任何一次真实采样，而任务书要的是「采样
    耗时不高于该值」这件事本身。与 `scripts/kernel_trace.py` 同一套口径，
    `tests/test_kernel_trace.py` 拿同一份语料把两处钉在一起。
    """
    ordered = sorted(values)
    if not ordered:
        return None
    index = math.ceil(ratio * len(ordered)) - 1
    return ordered[min(max(index, 0), len(ordered) - 1)]


def summarize(values):
    """把逐次采样汇成任务书要求的两个读数：中位数与 90%分位。

    **平均值不出。** 任务书写的是「报告耗时中位数及90%分位耗时」，两个统计量
    里没有平均——首次调用与偶发抖动是离群点，平均被它们拉走。
    """
    ordered = sorted(values)
    return {"under_test_us": round(statistics.median(ordered), 3),
            "p90_us": round(percentile_at_least(ordered, 0.9), 3),
            "min_us": round(ordered[0], 3),
            "max_us": round(ordered[-1], 3)}


def build_call(case, device):
    """按一条用例造出可重复调用的闭包。**这是唯一要改的函数。**

    返回 `(invoke, teardown)`：`invoke()` 每次只做一次算子调用，
    `teardown()` 收尾（没有就返回 None）。

    **描述符、workspace、输出张量都要在这里建好，不能建在 invoke 里**——
    任务书要求「正式采样复用描述符/workspace/输出，排除首次编译和搬运」，
    建在 invoke 里量到的就是建立开销而不是算子耗时，而且数会偏大得看不出来。

    下面是形态示意，照着改成本算子的调用：

        import torch
        dense = _build_input(case).to(f"npu:{device}")   # 造数，循环外
        out = torch.empty(...,  device=f"npu:{device}")  # 输出，循环外

        def invoke():
            torch.ops.<ns>.<op>(dense, out, case["format"])

        return invoke, None
    """
    raise NotImplementedError(
        "改 build_call：按本算子的调用形态造闭包。描述符与 workspace 建在闭包外，"
        "invoke() 里只留一次算子调用。形态见本函数的 docstring。")


def _time_once(invoke, device):
    """量一次调用，返回微秒。用 NPU Event，不用墙钟。

    墙钟量到的是 host 侧的下发耗时，算子在 device 上还没跑完就返回了，
    **量出来的数比真实值小一到两个量级**，而且看不出异常。
    """
    import torch
    start, end = torch.npu.Event(enable_timing=True), torch.npu.Event(enable_timing=True)
    start.record()
    invoke()
    end.record()
    end.synchronize()
    return start.elapsed_time(end) * 1000.0      # ms -> us


def measure(case, device, warmup, samples,
            baseline_us=None, budget_us=BUDGET_S * 1e6, stop_factor=STOP_FACTOR,
            probe_only=False):
    """一条用例的耗时统计。返回 dict，键与交换格式对齐。

    第一次预热顺带计时当探针，用它定实际采几次——**探针那次偏慢**（首次编译与
    首次搬运都算在里面），所以按它估出来的次数偏保守，不会超预算。
    """
    invoke, teardown = build_call(case, device)
    probe_us = _time_once(invoke, device)
    print(f"    探针      单次 {probe_us / 1000:.1f} ms，按 {warmup} 预热 + "
          f"{samples} 采样估计这条要 {probe_us * (warmup + samples) / 1e6:.0f} 秒"
          + (f"，约为基线的 {probe_us / baseline_us:.0f} 倍" if baseline_us else ""))
    if probe_only:
        if teardown:
            teardown()
        return {"probe_us": round(probe_us, 3), "probe_only": True,
                "estimate_s": round(probe_us * (warmup + samples) / 1e6, 1),
                # 与 verdict.py 同向：标杆耗时 / 待验收耗时，大于门槛才达标。
                # 两处反着算的话，一边说达标另一边说不达标，而两边都言之凿凿。
                "perf_ratio": (round(baseline_us / probe_us, 6)
                               if baseline_us and probe_us else None),
                "warmup": warmup, "samples": samples}
    warmup, samples, reason = plan_sampling(probe_us, baseline_us, warmup, samples,
                                            budget_us, stop_factor)
    if reason:
        print(f"    **摸底口径**  {reason}。这条数不符合验收口径，不能当验收依据")
    for _ in range(max(warmup - 1, 0)):
        invoke()
    values = sorted(_time_once(invoke, device) for _ in range(samples))
    if teardown:
        teardown()
    row = {**summarize(values), "warmup": warmup, "samples": samples}
    if reason:
        # 标记跟着数走。**不打这个标记就等于把摸底数混进验收结论**，
        # 而报告上看不出它是少采了次数得来的。
        row["off_spec"] = True
        row["sampling_note"] = reason
    return row


def _baselines(path):
    """标杆表：`{"<用例 id>": 34.688}` 或 `{"<label>": 34.688}`。

    **标杆值不在这里采**，它来自任务方冻结的那张表或另一台机器。
    这里只负责按 id 对上号——对不上的那条不给 `baseline_us`，
    `collect_perf.py` 会把它算成「没有基线」，报告里如实写。
    """
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            return {str(k): float(v) for k, v in json.load(handle).items()}
    except (OSError, ValueError, TypeError) as exc:
        print(f"标杆表 {path} 读不出来：{exc}", file=sys.stderr)
        return {}


def _sampling(args):
    """本轮的采样口径与它的出处。返回 (warmup, samples, 出处)，定不下来返回 (None,…)。

    **口径必须有出处，不许有静默默认值。** 预热与采样次数是任务书规定的验收口径
    （实测一例写着「NPU 侧每个 case 至少预热 10 次、正式采样 30 次」），
    骨架件自己塞一个「看着合理」的数，就会让不合口径的数悄悄进报告。
    """
    if args.warmup is not None and args.samples is not None:
        return args.warmup, args.samples, "命令行显式给定"
    if args.facts:
        try:
            with open(args.facts, encoding="utf-8") as handle:
                spec = ((json.load(handle).get("performance") or {})
                        .get("sampling") or {})
        except (OSError, ValueError) as exc:
            print(f"{args.facts} 读不出来：{exc}", file=sys.stderr)
            return None, None, ""
        warmup, samples = spec.get("warmup"), spec.get("samples")
        if warmup is not None and samples is not None:
            return (args.warmup if args.warmup is not None else int(warmup),
                    args.samples if args.samples is not None else int(samples),
                    spec.get("source") or f"{args.facts} 的 performance.sampling")
    print("定不下采样口径。预热与采样次数是任务书规定的验收口径，本脚本不替你猜。\n"
          "  从任务书性能章节读出来，写进 facts.json：\n"
          '    "performance": {"sampling": {"warmup": 10, "samples": 30,\n'
          '                                 "source": "任务书 x.y 第 N 条原话"}}\n'
          "  再带 --facts <facts.json>；或直接 --warmup N --samples M。",
          file=sys.stderr)
    return None, None, ""


def _probe_report(rows, args, warmup, samples):
    """探针表 + 哪几条不必跑满。**照既定判据算，不问用户。**"""
    total_s = sum(r.get("estimate_s") or 0 for r in rows)
    print(f"\n探针      {len(rows)} 条，按 {warmup} 预热 + {samples} 采样跑完"
          f"估计 {total_s / 60:.1f} 分钟")
    for r in sorted((r for r in rows if r.get("estimate_s")),
                    key=lambda r: -r["estimate_s"]):
        ratio = r.get("perf_ratio")
        print(f"  {str(r.get('label') or r['id'])[:44]:<44}"
              f" {r['estimate_s']:>7.0f} s"
              + (f"  倍率 {ratio:.4g}" if ratio is not None else "  （无基线）"))

    threshold = criterion_threshold(args.criterion)
    if not threshold:
        print("\n任务书门槛抠不出来（要「N 倍」这种写法），不判，全部跑满。")
        return
    settled = settled_cases(rows, threshold)
    settled_s = sum(r.get("estimate_s") or 0 for r in settled)
    if not settled or settled_s <= args.ask_after_s:
        return
    print(f"\n下面 {len(settled)} 条**不跑满**：倍率比门槛 {threshold} 低一个数量级"
          f"以上，结论已经没有悬念，而把它们测精确还要 {settled_s / 60:.0f} 分钟。",
          file=sys.stderr)
    for r in settled:
        print(f"  {str(r.get('label') or r['id'])[:44]:<44}"
              f" 倍率 {r['perf_ratio']:.4g}  省 {r['estimate_s'] / 60:.0f} 分钟",
              file=sys.stderr)
    print("正式轮把这几条的 id 从用例子集里排除（换目录不换文件名，仍叫 cases.json），"
          "报告里它们写「不达标（未完整采样，附探针倍率）」。**不跑满不等于没结论**，"
          "缩的是采样精度不是验收场景。要全部采满就把 --ask-after-s 调大。",
          file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--cases", required=True, help="性能子集 cases.json")
    parser.add_argument("--baseline", default="", help="标杆表 JSON，id 或 label 到 us")
    parser.add_argument("--device", default="0", help="卡号，一个算子占一张")
    parser.add_argument("--facts", default="",
                        help="用例包的 facts.json，从 performance.sampling 取口径")
    parser.add_argument("--warmup", type=int, default=None,
                        help="预热次数。不给就从 --facts 取；**没有静默默认值**")
    parser.add_argument("--samples", type=int, default=None,
                        help="正式采样次数。同上")
    parser.add_argument("--budget-s", type=float, default=BUDGET_S,
                        help="**摸底用**。每条用例的采样墙钟预算，超了就减采样次数，"
                             "默认 0 = 不限。开了之后数不符合验收口径")
    parser.add_argument("--early-stop-factor", type=float, default=STOP_FACTOR,
                        help=f"**摸底用**。与基线差这么多倍就只采 {MIN_SAMPLES} 次，"
                             f"默认 0 = 关掉。开了之后数不符合验收口径")
    parser.add_argument("--source", default="perf_harness.py（本仓骨架）",
                        help="这批数谁采的，进报告")
    parser.add_argument("--probe-only", action="store_true",
                        help="每条只跑一次，出「单次耗时 / 估计总时长 / 与基线倍率」，"
                             "不采数。性能轮一律先跑它")
    parser.add_argument("--criterion", default="",
                        help="任务书性能要求的原话，用来抠门槛。不给就从 --facts 的 "
                             "performance.criterion 取")
    parser.add_argument("--ask-after-s", type=float, default=ASK_AFTER_S,
                        help=f"**已明显不达标**的那几条还要跑这么久就不跑满，"
                             f"默认 {ASK_AFTER_S} 秒。调大到超过总估时即为全部采满。"
                             f"只在探针模式下判")
    parser.add_argument("-o", "--out", required=True)
    args = parser.parse_args()

    if not args.criterion and args.facts:
        try:
            with open(args.facts, encoding="utf-8") as handle:
                args.criterion = ((json.load(handle).get("performance") or {})
                                  .get("criterion") or "")
        except (OSError, ValueError):
            pass
    warmup, samples, spec = _sampling(args)
    if warmup is None:
        return 3
    print(f"采样口径  预热 {warmup} 次 + 正式采样 {samples} 次（出处：{spec}）")

    with open(args.cases, encoding="utf-8") as handle:
        cases = json.load(handle)
    base = _baselines(args.baseline)

    rows, failed = [], []
    for case in cases:
        ident = case["id"]
        label = case.get("name") or ""
        hit = base.get(str(ident)) or base.get(label)
        try:
            row = measure(case, args.device, warmup, samples,
                          baseline_us=hit, budget_us=args.budget_s * 1e6,
                          stop_factor=args.early_stop_factor,
                          probe_only=args.probe_only)
        except NotImplementedError:
            raise
        except Exception as exc:                  # noqa: BLE001 - 一条挂了不拖垮整批
            failed.append((ident, f"{type(exc).__name__}: {exc}"))
            continue
        row["id"] = ident
        if label:
            row["label"] = label
        if hit:
            row["baseline_us"] = hit
        rows.append(row)
        print(f"  case {ident:<5} 中位数 {row['under_test_us']:>10.3f} us"
              f"  p90 {row['p90_us']:>10.3f} us"
              f"{'  基线 %.3f' % hit if hit else '  （无基线）'}")

    if args.probe_only:
        _probe_report(rows, args, warmup, samples)

    payload = {"source": args.source, "unit": "us", "cases": rows}
    if any(r.get("off_spec") for r in rows):
        payload["off_spec"] = True
        print("\n**这批是摸底数**：有用例的采样次数低于验收口径，`collect_perf.py` "
              "会拒收。要出验收结论就关掉 --budget-s 与 --early-stop-factor 重跑。",
              file=sys.stderr)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    print(f"\n采到      {len(rows)}/{len(cases)} 条，"
          f"{sum(1 for r in rows if 'baseline_us' in r)} 条对上了基线")
    if failed:
        print(f"跑挂      {len(failed)} 条：", file=sys.stderr)
        for ident, why in failed[:10]:
            print(f"  case {ident}  {why}", file=sys.stderr)
    print(f"写入      {args.out}")
    print(f"\n下一步    collect_perf.py --from {args.out} -o stage/performance_external.json")
    return 0 if rows else 2


if __name__ == "__main__":
    sys.exit(main())
