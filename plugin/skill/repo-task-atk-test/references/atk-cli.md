# ATK 黑盒命令

## 目录

- 边界和版本
- 生成
- 冒烟与精度
- 性能
- 报告
- 停止

## 边界和版本

只使用环境指纹中的绝对 CLI。

命令以 `--help` 为准，不要猜。
`--help` 解释不了实际行为时可以读 ATK 源码确认，并在证据里留痕。

版本不是 `26.8.8` 时，只查看实际使用的 `case --help`、`node --help` 和 `task --help`。

帮助仍不足时报告“ATK 使用规则缺失”。

## 生成

有约束插件：

```bash
<atk-cli> case -f <yaml> -p <constraint>
```

无插件时省略 `-p`。

产物落在**当前工作目录**下：`result/<yaml 文件名>/json/all_<yaml 文件名>.json`。

目录名取自 YAML 的文件名，不是 `name`、`api` 或 `aclnn_name`。

日志里那一行叫 `save case json file:`，以它为准，不要自己拼路径。

`not get env ATK_TASK_OUTPUT_PATH` 是无害警告，产物路径与它无关。

本轮每个接口分面只运行一次 `atk case`。

## 执行后端

| 接口模式 | 后端 |
| --- | --- |
| pytorch | npu |
| aclnn | pyaclnn |
| kernel | kernel |
| triton | 未开放 |
| atb | 未开放 |

ACLNN 后端由 `evidence/interface.json` 固定，不能用别名代替。

## 最小冒烟

冒烟只执行一条未剔除用例；`-e` 是排他上界。

`-s/-e` 取 `evidence/frozen_inputs.json` 的 `smoke_range`，不要写死 `0 1`：

```bash
<python> scripts/run_atk_task.py -o evidence/smoke.log -- \
  <atk-cli> node -b <backend> --devices <device> \
  node -b <baseline_backend> task -c <case-json> -tk accuracy \
  -s <smoke_range[0]> -e <smoke_range[1]> \
  --input_data <frozen> --cpp_func_signature_type_path
```

`<baseline_backend>` 取 `evidence/interface.json` 的 `baseline_backend`，不要写死 `cpu`。

插件参数放在 `task` 后。

已有排除记录时使用 `-wl '[<case-id>]'`，不要再用 `-s/-e`。

pyaclnn 冒烟必须保留 `--cpp_func_signature_type_path`。

退出码 0 只表示调度结束。

读取本次日志的汇总表和报告路径。

执行成功数必须为 1。

执行失败数必须为 0。

精度结果必须存在。

op_api 任务还要确认加载路径属于本轮 vendor。

明确的无效参数或 attr 数据错误可记录该用例并最多再冒烟一条未剔除用例。

第二条冒烟仍失败时停止。

## 黑名单和全量精度

黑名单使用 JSON 数组字符串：`-bl '[0,2]'`。

黑名单只影响本次任务，不修改用例 JSON。

冒烟通过后执行全量：

```bash
<python> scripts/run_atk_task.py -o evidence/accuracy.log -- \
  <atk-cli> node -b <backend> --devices <device> \
  node -b <baseline_backend> task -c <case-json> -tk accuracy
```

存在排除记录时追加 `-bl`。

全量报告出现新无效用例时只更新排除记录，不重跑全量。

## 基线是 CANN 内置实现时

`interface.json` 的 `baseline_kind` 是 `cann_builtin` 时，跑测形态完全不同：
内置实现挂不上一个基线节点，真值要先跑一轮存盘、再用 `--task accuracy_load` 读回来。

整套命令、目录规则、三件必须取证的事和必做的反证实验都在 builtin-baseline.md，
本节不重复。**不要照本文上面那几条命令改一改就跑**，单节点建不起任务，
`-tk run` 也存不下输出。

## 性能

性能使用 `performance_device --fluctuation_check`。

pyaclnn 需要第二个节点产生输出元数据；没有 NPU 基线时使用 CPU 节点产出元数据。

不要用 `--bm_file` 代替该节点。

对比模式必须确认标杆后端和 device；不要用 CPU 作为性能标杆。

性能报告的 `device_perf(us)` 是各节点独立绝对值。

所有 ATK 任务通过 `run_atk_task.py` 拉起。

建任务失败和体外异常可能停在 `0/N`；脚本应按日志特征和停滞超时停止。

## 报告定位

生成期与执行期的产物在两个不同的地方，不要互相套用：

| 阶段 | 位置 |
| --- | --- |
| `atk case` | 当前工作目录下 `result/<yaml 文件名>/json/` |
| `atk node … task` | 当前工作目录下 `atk_output/<任务目录>/report/` |

任务目录名由 ATK 按用例名加时间戳生成，不要自己拼。

不要扫描旧的 `atk_output`。

读取本次日志最后一条 `save result excel file:`，确认路径存在。

## 停止

CLI 不兼容、模式未开放、后端未确认、第二条冒烟失败、报告路径未知或实际后端与 `interface.json` 不一致时停止。
