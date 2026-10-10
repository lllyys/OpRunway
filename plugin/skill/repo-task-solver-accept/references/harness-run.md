# 共享 harness 运行协议

共享 harness 是 `scripts/harness/` 的构建、执行、判定编排和取证工具。
执行件对被测工程公开头编译，不调用开发者自带的判据脚本。一次 case 使用一个 C++
子进程；确定性复跑在同一进程、同一 stream 内逐轮恢复输入。

## 前置条件

| 核对项 | 准入条件 | 不满足时 |
| --- | --- | --- |
| 源码 | 实际交付版本、公开头、build.sh 和算子实现齐备 | 停止，不用另一算子的结构性演练替代 |
| 接口 | 实参顺序、返回值与已实现适配一致 | 取得交付契约再接入，不猜签名 |
| 输入归属 | Host 指针由算子内部搬运；Device 指针由调用方分配搬运 | 两口径不能混用 |
| 布局 | 当前执行器只支持已实现的行主序连续矩阵 | 列主序与非连续 leading dimension 未接入时拒绝 |
| 依赖准备 | 求解或求逆入口需要的因子由规定的准备链提供 | 不把原矩阵直接当作分解因子 |
| 状态输出 | 核对 info 是标量还是逐 batch 数组、返回值是否代表执行成功 | 不以全零模拟状态冒充真实接口输出 |
| 构建环境 | source 实际 CANN set_env.sh，确认目标 SOC、设备、工具链与可用容量 | 缺环境时停在构建之前 |
| 判定包 | canonical、manifest 和已回填 index 齐备，ratio_basis=A32-f64 | 不从运行现场新算的参考值补洞 |
| 工具分发 | accept 和 case-gen 两个 skill 同次分发 | 不依赖工作区开发文档或旧 session |

Cholesky 十算子的调用仍为 `awaiting_delivery`（尚无实际交付可绑定），请求运行时退 2。
Host 求逆通路可用于工程连接演练；其报告 `numeric=NOT_JUDGED` 表示没有该族的数值
判定卡。演练成功不能证明 Cholesky 通过，也不能替代该批真实算子完整样例。

## 用法

以下命令在 accept skill 根目录执行。先设置实际路径，路径可以含空格：

```bash
DUT_REPO=/absolute/path/to/delivered-ops-solver
RUN_DIR=/absolute/path/to/run-directory
GEN_DIR=/absolute/path/to/repo-task-solver-case-gen/scripts
TASK_PACKAGE=/absolute/path/to/operator-package
OP=spotrf
SOC=ascend950
DEVICE=0
mkdir -p "$RUN_DIR"
```

`OP` 是实际已接入的交付入口，`SOC` 与 `DEVICE` 必须来自环境核对。
上面的 `spotrf` 仅展示任务包参数；真实交付接入完成前不能执行该算子。

### 构建

```bash
python3 scripts/harness/build_dut.py --repo "$DUT_REPO" --ops "$OP" \
  --soc "$SOC" --device "$DEVICE" --out "$RUN_DIR/build.json"
```

| 退出码 | 含义 | 下一步 |
| --- | --- | --- |
| 0 | 构建成功且产物齐备 | 使用本次 build.json 继续执行 |
| 2 | 入口或构建参数错误 | 修正参数 |
| 3 | 构建失败、超时或产物缺失 | 查看构建取证和日志，修复后重建 |

构建只使用 `--ops` 与显式 `--soc`；不使用会清理现场的 `--run`，不使用联网打包的
`--pkg`。`--skip-build` 仅供排错，不能代替验收者从交付源码构建的证据。

### 任务包执行

```bash
python3 scripts/harness/run_harness.py --repo "$DUT_REPO" \
  --canonical "$TASK_PACKAGE/canonical_cases.json" --gen-dir "$GEN_DIR" \
  --provenance "$RUN_DIR/build.json" --device "$DEVICE" --rerun 5 \
  --work-dir "$RUN_DIR/work" --evidence-dir "$RUN_DIR/evidence" \
  --report "$RUN_DIR/report.json"
```

| 选项 | 作用 | 证据边界 |
| --- | --- | --- |
| --max-n、--limit、--ops | 筛选调试用例 | 子集运行不能宣称全量通过 |
| --rerun N | 同进程复跑 N 轮，逐字节比较输出与 info | 未跑满不判一致；确定性独立于数值 |
| --keep case_id | 成功 case 也导出现场 | 用于排错与独立重判 |
| --timeout | 限制单个执行子进程时间 | 超时终止进程组，保留已有日志与输出 |

包内 index 可以是 `cases/index.json` 或 gzip 文件 `cases/index.json.gz`。
后者的 manifest 摘要仍针对解压后的 JSON 内容。逐 case 的 `ratio_cpu` 是冻结的
CPU 残差参考值，`ratio_cpu_status` 表示参考链成功或准备失败；单矩阵的均值取 index
顶层对应算子，批量均值取该 case 的槽位加权值。批量同时使用该 case 固化的
`sample_map`（代表内容到批内槽位的映射）。输入现场生成，判定参考值不现场替换。

### 报告与退出码

| 退出码或字段 | 含义 |
| --- | --- |
| 0 | 本次所选 case 在已执行检查范围内通过，不代表正式验收通过 |
| 1 | 存在执行、数值、结构性、确定性或加载库身份问题 |
| 2 | 参数、任务包或未接入接口错误 |
| structural | 输出格式与有限性等结构检查，不是精度结论 |
| numeric | 判定卡给出的数值结论；NOT_JUDGED 表示未做数值判定 |
| determinism | 完成轮数、逐字节一致性与首个失配信息 |
| formal=PENDING_RULING | 尚未出具正式结论 |

性能通过 [performance-acceptance.md](performance-acceptance.md) 的独立逐 case 通路采集与判定，
只使用 msprof op，不把执行器 `run1_ms` 当作 kernel 时间。
族级覆盖、bufferSize 和内存证据缺失时必须在验收报告列出，不从成功 case 外推。

## 失败现场与独立重判

失败现场保存输入、实际输出、spec（执行参数文本）、执行日志、构建来源和原判定
上下文。失配轮与首轮分别保留；损坏输出保留原始字节。每次运行使用新目录，
通过后释放大数组，失败件完整导出后再清理重复的二进制现场。

```bash
python3 scripts/harness/run_harness.py --gen-dir "$GEN_DIR" \
  --rejudge /absolute/path/to/evidence-case --report "$RUN_DIR/rejudge.json"
```

重判不调用设备、不重新构造输入、不重算参考值。执行失败仍是执行失败；确定性
失配需比较已保存的输出字节，不能仅因首轮数值通过就退成功。原始证据不足时
报告缺项，不恢复成通过。

## 当前交付边界

共享工具和接入步骤可独立分发。该批 Cholesky 的真实算子完整样例，需要开发者交付
公开头与实现后才能生成；此处命令模板不冒充已运行的样例。大规格执行需先核算
输入、输出、参考数组、复跑副本和失败留证的内存及磁盘峰值，再在目标设备实测。
