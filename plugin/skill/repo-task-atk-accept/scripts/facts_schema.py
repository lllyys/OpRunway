#!/usr/bin/env python3
"""`facts.json` 的字段表：跑测侧读哪些字段、谁读、缺了会怎样。

**这份是唯一真相。** 字段散在四个脚本的十八个读取点上，此前没有任何一处能一眼
看全，想知道要填什么只能去 grep 源码——实测一轮为此读了三个脚本的源码。
`tests/test_facts_schema.py` 盯着两边一致：脚本里新读一个字段而这里没登记，
测试就红。

用法：

    python3 facts_schema.py            # 打成表
    python3 facts_schema.py --json     # 机器读

`facts.json` 由生成侧产出；走自带件路时由跑测侧 A1 从任务书写出来。
"""

import argparse
import json
import sys

# 字段 -> (谁读, 必填, 取值, 缺了会怎样)
# **「缺了会怎样」比「是什么」重要**：这些字段缺失时脚本几乎都不报错，
# 只是结论悄悄降级，所以这一列写的是降级成什么样。
FIELDS = {
    "aclnn_name": (
        "run_cxx.py、verdict.py", True, "算子名，逐字与任务书一致",
        "各脚本的 --op 对不上，跑测命令退 3"),
    "backend": (
        "run_atk.py、run_cxx.py、verdict.py", True, "aclnn（缺省）或 npu",
        "剖面选错，被测节点整列读不到，通过率算成 0 而日志没有报错"),
    "op": (
        "verdict.py", False, "报告标题用的算子名，缺省取 aclnn_name",
        "报告标题为空"),
    "group_attr": (
        "run_atk.py、verdict.py", False,
        "分档轴的参数名。有张量输入时不用填，缺省按 dtype 分",
        "纯 attr 用例塌成一档：冒烟只抽得出一条，拦不住整类失败"),
    "group_labels": (
        "verdict.py", False, "分档轴的取值顺序，报告按它排",
        "报告里按字典序排"),
    "params": (
        "run_atk.py、verdict.py", False, "算子签名的参数表",
        "非连续轮切不出布局；报告的覆盖边界写不出参数维度"),
    "accuracy": (
        "verdict.py", False, "精度标准声明，`acc` 是标准名",
        "报告里精度标准栏写 default，与实际用的比对器可能不符"),
    "non_contiguous": (
        "run_atk.py、verdict.py、make_repro.py", True,
        "`required` 照任务书参数表的「非连续Tensor」列填，**不是自己判**；"
        "为真时 `ratio` 定这一轮混多少非连续（缺省 0.4）。"
        "npu 剖面下 ATK 不接管输入构造，布局写在用例属性里，"
        "用 `attr` 指属性名、`noncontiguous_values` 指哪些取值算非连续",
        "填 false 时非连续布局一条都没测，而报告只会说「本轮用例全是连续布局」，"
        "看不出是任务书没要求还是漏填了。**实测两个独立会话各错一次**"),
    "smoke": (
        "run_atk.py", False,
        "`axes` 列出冒烟除 dtype 外还要覆盖的判据轴（attr 名），每个取值各抽一条",
        "冒烟只按 dtype 抽样，特定取值上的整批同因问题要到全量轮才暴露——"
        "实测一次 beta=0 注入 NaN 的判据就这么漏过去，多付两轮全量"),
    "performance": (
        "verdict.py", True,
        "`kind` 四选一（none/builtin/cross_dtype/threshold）、"
        "`criterion` 填任务书原话、`pairs` 只在 cross_dtype 下用、"
        "`sampling` 填任务书规定的 `{warmup, samples, source}`",
        "kind 不在四种里时 A4 退 3；criterion 缺了门槛抠不出来，"
        "结论降级成待人工判定；sampling 缺了外部量测件停下来问，"
        "**不会替你猜一个次数**。次数之后那半句同样是验收口径："
        "任务书通常还规定报告哪两个统计量（实测一例「报告耗时中位数及90%分位耗时」），"
        "量测件按 `kernel_trace.summarize` 出这两个数，判据见 references/external-perf.md"),
    "kit": (
        "verdict.py", False,
        "自带件路专用：`cases` 指本轮实际跑的用例清单（相对 input/ 或绝对路径），"
        "`baseline` 是标杆节点名",
        "精度分档塌成一档、失败归因没有分档轴、复现包挑不出用例，"
        "而通过率照常算得出来"),
    "adopted_files": (
        "make_repro.py", False, "自带件原件的 sha256",
        "复现包证明不了跑的是任务方交付的那一份"),
    "kit_fixes": (
        "make_repro.py", False, "每条修复的一句话摘要",
        "报告不说改过什么，结论追不回来源"),
}


def as_table():
    rows = ["| 字段 | 谁读 | 必填 | 取值 | 缺了会怎样 |",
            "| --- | --- | --- | --- | --- |"]
    for name, (who, required, value, missing) in FIELDS.items():
        rows.append(f"| `{name}` | {who} | {'是' if required else '否'} "
                    f"| {value} | {missing} |")
    return "\n".join(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="按 JSON 打，给脚本读")
    args = parser.parse_args()
    if args.json:
        json.dump({k: {"readers": v[0], "required": v[1], "value": v[2],
                       "missing": v[3]} for k, v in FIELDS.items()},
                  sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print(as_table())
    return 0


if __name__ == "__main__":
    sys.exit(main())
