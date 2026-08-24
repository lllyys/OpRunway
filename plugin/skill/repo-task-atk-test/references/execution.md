# 环境、部署与执行门禁

## 目录

- 环境
- 工作目录
- 无效用例
- 部署
- 冻结输入
- 阶段归属
- 中止

## 环境

运行：

```bash
<python> scripts/probe_env.py [--device N] -o evidence/env.json --env-sh evidence/env.sh
```

使用 `selected_python` 和 `atk_cli`，不要使用裸 `atk`。

`--env-sh` 产出的 `evidence/env.sh` 是此后每条命令的前缀载体：

```bash
source evidence/env.sh && cd <绝对工作目录> && "$ATK_PYTHON" scripts/<脚本>.py ...
```

它固化 CANN 环境、`ATK_PYTHON`、`ATK_CLI`、`ATK_DEVICE` 和 `ATK_SKILL_DIR`。

每个 Bash 都是新 shell，不 source 就等于没加载环境。

量具命令不要接 `| grep`/`| tail` 再读 `$?`：拿到的是管道尾那个命令的退出码，
不是量具的。要看退出码就先落地全部输出（`... > log 2>&1; echo $?`），
再从落地的文件里筛要看的部分。

`ATK_SKILL_DIR` 是量具目录的绝对路径，不要用 `find` 现找。

用户指定 device 时直接记录。

未指定时选择一张健康候选卡，再用正式冒烟验证。

设备错误发生在 `aclrtSetDevice` 前，不进入适配器修正；适配器错误发生在待验收算子的接口名绑定或 ABI 校验阶段。

设备错误先清理残留 `atk node` 进程并查看 `npu-smi`，同卡重试仍失败时停止。

## 工作目录

进入 S2 前建立阶段时间线，每进一个阶段打一次点：

```bash
<python> scripts/mark_step.py 2 用例生成 -o evidence/timeline.jsonl
<python> scripts/mark_step.py --summary -o evidence/timeline.jsonl
```

```text
work/<operator>/
├── *.yaml
├── *_constraint.py
├── cases/
├── evidence/
├── conclusion/
└── delivery/
```

命令逐条追加到 `evidence/repro.sh`。

接口分面各自固化 YAML、用例、报告和结论；部署产物可以共享。

## 无效用例

单条用例的输入字节预算由 `freeze_inputs.py` 在冻结时顺带核（默认 2GiB，
`--budget-bytes` 可改），不再单独跑一个脚本——它本来就要把每条用例的输入
落盘，字节数它最清楚，原地核对比留到 S3/S4 才发现更省。

只剔除原始错误明确表明参数或 attr 无法形成调用的用例。

环境、绑定、合法输入错误和原因不明的失败不得剔除。

记录 `id`、`stage`、`cause`、原始 `reason` 和本轮日志路径。

运行期发现无效用例时加入黑名单，不改用例 JSON，不重新运行 `atk case`。

## 部署门禁

只用公开构建方式，并为本轮生成新的 `.run` 包。

安装本轮包，加载其 `set_env.bash`，再运行 `check_soc_binding.py`，落盘 `-o evidence/soc_binding.json`。

pyaclnn 必须指向本轮 vendor 内准确的 `.so`：

```bash
export ATK_CUSTOM_OPP_PATH=<absolute-candidate-library>
```

预检和冒烟后都运行 `check_opapi_binding.py`。

冒烟启用 `--cpp_func_signature_type_path`。

标准绑定不匹配时，按 [plugin-authoring.md] 的运行时契约提供最小适配器；不得用猜测修正。

ATK CLI 退出码为 0 不代表冒烟通过。

必须确认报告成功数为 1、失败数为 0、精度结果存在。

加载路径必须属于本轮安装的 vendor。

## 冻结输入

S2 末尾把张量物化到磁盘，执行期各轮复用同一份输入：

```bash
<python> scripts/freeze_inputs.py -j <case-json> --atk-cli <atk-cli> \
  -p <function-plugin> -d frozen/ -o evidence/frozen_inputs.json
```

CPU 基线接口名与 torch 不一致时必须传 `-p`，否则物化在基线调用处失败。

执行期用 ATK 的 `--input_data frozen/` 消费该目录。

冻结时顺带核三件事，任一不过退出码同为 2：

- 整张只有一个取值的张量测不出错（错的置换与对的置换逐元素相等）——
  这属于取值范围的设计问题，改 decl 的 `range` 后重生成，不是待验收算子的缺陷
- 单条用例的输入是否超字节预算（默认 2GiB，`--budget-bytes` 可改）
- 基线插件是否真的跑通——冻结用的 CPU 单节点就是它，这里不核就会推迟到
  S3 冒烟甚至 S4 全量才发现，且已执行的部署成本连带作废

## 阶段归属

阶段顺序由 SKILL.md 的阶段表定义，本文件只提供各阶段的执行细节。

`probe_env.py` 和工作目录属于 S1 出口。

无效用例记录属于 S2 出口。

部署门禁和冒烟属于 S3 出口。

全量精度、非连续轮和裁决属于 S4。

非连续轮复用同一用例和冻结输入，只改变 `--slice_input non_contiguous`。

长任务（全量精度、性能）必须在单条前台命令里带足执行超时并跑完，
继续使用 `run_atk_task.py` 的 `--stall-timeout` 看门狗；
不得把跑测放到后台再等待通知。无头执行环境里会话一旦不再发工具调用就会退出，
等待唤醒等于中途退场。真机案例：S4 全量 79 条跑到 65 条时会话退场，
无裁决、无报告。

## 中止

标准构建、安装、SoC/op_api/ABI 绑定或第二条冒烟失败时停止。

保留命令、退出码、日志和已绑定用例规格。

不要修改待验收算子源码。

不要阅读待验收算子实现来补充失败原因——归因结论只能落在可观测证据上。
接口声明、README 和构建脚本不在此列，读它们排查绑定和构建问题是正常的。

可以读 ATK 源码理解失败机制，但结论必须落在可观测证据上，不能只引源码。
