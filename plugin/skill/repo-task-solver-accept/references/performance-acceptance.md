# 性能验收流程

本流程将逐 case 的 msprof op 采集、GPU 基线匹配、任务书阈值判定和性能汇总
连在一起。公式为 NPU 平均单次 kernel 耗时 ≤ GPU avg_ms / 0.35。
`criteria/performance.py` 是该判定的唯一实现；性能结论与总体验收结论分别记录。

## 范围与准入

- 任务包 `canonical_cases.json` 的全部普通 case 是性能全集，info 契约例不采性能。
  缺基线行、matched=false 或 avg_ms 无效的 case 保留为证据不足，不缩小全集。
  当前单矩阵包包含 source=std 的精度补充例，其中无 GPU 基线者也会报告缺项。
- 原始 GPU 来源是 bench_result.json，实际判定只读取包内冻结 perf_baseline.json，
  用 case_id 与 bench_key 匹配；canonical 和基线的 manifest 摘要必须一致。
- 正式任务性能要求 A0 已核实目标为 950PR、构建 SOC 属 ascend950、列主序、Device ABI。
  `--target` 填现场核验结果，不用于把 A3 数据标作 950PR。工具同时核对构建 SOC 与实际
  适配的布局和 ABI；缺条件时不判正式性能通过。
- 当前 Cholesky 实现尚未交付，执行器仅支持已核 Host 行主序入口。对未交付接口或
  column_major 请求明确停止采集并逐例记录缺项。判定链可用不意味着已在950PR全量实跑。
- 求解/求逆的因子准备不计入被测接口耗时。适配必须先解决准备与目标调用隔离；
  同名准备 kernel 无法区分时，不得把不完整的 kernel 清单拿来验收。

## 第一步：构建与 kernel 清单

先按 harness-run.md 从实际交付源码构建，得到 build.json，不能使用 skip-build 证据。
根据已核真实调用链填写 kernel-map.json。算子默认清单在 operators 中，规格特有
清单通过 cases 按 case_id 覆盖；同一 case 的目标 kernel 必须全部列出：

```json
{
  "operators": {
    "spotrf": [
      {"symbol": "实际编译符号", "op_name": "实际CSV中的Op Name", "launches_per_call": 1, "target_only": true}
    ]
  },
  "cases": {}
}
```

symbol 是 msprof op 的 kernel-name 参数，op_name 用于再次核对 CSV。
launches_per_call 是一次接口调用中该 kernel 的执行次数，必须是已核整数；
target_only=true 表示清单已排除因子准备等非目标调用，不是自动发现结果。
符号和次数随实现或 shape 改变时，重新核对清单；上面的示例文字不能原样执行。

## 第二步：逐 case 采集并判定

在 accept skill 根目录执行，实际环境先 source CANN 的 set_env.sh：

```bash
python3 scripts/harness/run_performance.py \
  --package /absolute/path/to/task-package \
  --repo /absolute/path/to/delivered-ops-solver \
  --gen-dir /absolute/path/to/repo-task-solver-case-gen/scripts \
  --provenance /absolute/path/to/build.json \
  --kernel-map /absolute/path/to/kernel-map.json \
  --device 0 --target 950PR --layout column_major \
  --out /absolute/path/to/new-performance-run
```

工具固定使用 `msprof op`，不调用普通 msprof 采集流程。一次只采一条 case，
每种目标 kernel 单独采集30次接口调用对应的全部 launch。使用 kernel replay
（profiler 重放同一 kernel 的采集模式）、工具预热5次；30次是本工具协议，
不是任务书额外规定。输入由同一生成器构造，每轮按共享执行器恢复输入。

`OpBasicInfo*.csv` 必须包含 Op Name、Device Id 和 Task Duration(us)。每个
kernel 必须恰有30×launches_per_call条正有限耗时；错误kernel/device、漏采、
重复导出、执行失败、复跑未完成、加载库与构建不符，都记证据不足。
多 kernel 的单次接口 kernel 总耗时 = 所有必需 kernel 的采样耗时之和 / 30，
不会无权平均各 kernel 后低估多次 launch 成本。Host分配、搬运、构建、输入准备
不在kernel-only计时范围内；完整API延迟是另一口径。

输出目录必须不存在。`--case-id ID` 可重复指定调试子集；未选case仍保留在全集为
证据不足，子集不能产生全量性能PASS。每完成一例就更新报告；失败原始现场保留。

## 第三步：汇总与复核

| 文件/字段 | 内容 |
| --- | --- |
| performance-measurements.json | 包摘要、构建信息、kernel清单、规格、逐kernel执行记录与原始采样索引 |
| performance-report.json | 逐case GPU均值、NPU均值、门限、比值、状态和缺项原因 |
| case-*/kernel-*/profile/ | msprof op 原始输出，包含OpBasicInfo CSV |
| case-*/kernel-*/execution-result.json | 执行件状态与完成轮数；执行失败时另保留执行目录现场 |
| performance_verdict | PASS、FAIL或INSUFFICIENT（证据不足） |
| overall_acceptance | NOT_DETERMINED，性能项不能代替总体验收 |

有可裁超限case则汇总FAIL；同时仍列出证据不足数和complete=false。
没有超限但存在缺项则INSUFFICIENT；非空全集全部达标才PASS。
退出码：0=性能PASS，1=存在性能FAIL，2=证据不足或输入契约错误。

离线重判时保留整个采集目录，CSV路径相对measurements文件定位，不只拷贝JSON：

```bash
python3 scripts/harness/run_performance.py \
  --package /absolute/path/to/task-package \
  --measurements /absolute/path/to/perf-run/performance-measurements.json \
  --out /absolute/path/to/new-rejudged-report
```

重判重新读取CSV，不信任JSON中的旧PASS或平均值；包摘要、规格、kernel清单、
构建SOC和加载库记录不一致时拒绝对应结论。GPU基线更新后，旧包证据不得直接
套在新包上，需按新的包身份重新组织验收证据。

将同份证据并入族级报告：

```bash
python3 scripts/accept_run.py --package /absolute/path/to/task-package \
  --dut-out /absolute/path/to/dut-output \
  --performance-evidence /absolute/path/to/perf-run/performance-measurements.json \
  --report /absolute/path/to/acceptance.json
```

未显式传参时，备用入口查找 dut-out/performance-measurements.json。
旧 perf.json 数字字典及 verify_perf.py 的比值结果仍仅作参考，不升级为正式性能证据。
