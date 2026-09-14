# 生成与冻结

对应 S3 与 S4，末尾是用例包的落盘布局。

## S3 生成用例

回填 `dtype_numbers`（算法见 case-strategy.md「用例规模」），然后：

```bash
cd <工作目录> && <python> <skill>/scripts/gen_cases.py
```

用户点名了每个 dtype 要几条就加 `--dtype-numbers N`，不用改 YAML：

```bash
cd <工作目录> && <python> <skill>/scripts/gen_cases.py --dtype-numbers 100
```

**和 `focus_dtypes` 同时在场时，焦点那几种不是 N 条**——N 进的是拆轮次的算式，
两轮 `-dt` 重算。用户要的是「焦点也恰好 N 条」就得清掉 `focus_dtypes`，二选一。

产物三件：`cases.json` 全量、`perf/cases.json` 分层抽样的性能子集、`perf/manifest.json`
逐条规模档与 `cross_dtype` 配对表。**抽样在生成侧做，跑测侧不重算档位**——分层用的规模
档是用例设计的知识，「某一档抽不够」也只有回 S2 改 YAML 才修得掉。

| 判据 | 去哪改 |
| --- | --- |
| 用例数 < 100 | **硬闸**，脚本直接拦。回 S2 加 `dim_values` 或 dtype，不要调大 `dtype_numbers` 硬凑。设计目标是 150–400，见 case-strategy.md「用例规模」 |
| 打印「抽不够」（某档 < 3 条） | 回 S2 调 `max_length` 与 `dim_values`，调大 `--perf-number` 没用 |
| 打印「条数提示」（> 500 条） | **不是返工判据**，脚本不拦。用户点名要这么多条就照跑，把提示里的 golden 体积估算原样转述一句 |

条数对不代表覆盖面对，跑一次量具：

```bash
cd <工作目录> && <python> <skill>/scripts/check_coverage.py
```

纯度量，退出码固定 0，不设阈值也不拦人。只有这四条构成返工判据，回 S2 自己修完继续走，
**不用问用户**：

| 判据 | 长什么样 | 去哪改 |
| --- | --- | --- |
| 轴退化 | 「轴退化」行点名某根轴，且这根轴**结构上本可以多值** | 约束器，见 plugin-authoring.md「`case_config.id` 在约束器里恒为 0」。文档写死长度的数组参数这类结构上就是单值的不返工，判法见 case-strategy.md「覆盖判据解读」。值域轴已由量具单列成「值域」行，不在这条里 |
| 秩缺档 | 「秩覆盖」行报「n–m 未测」 | 约束器显式写形状，见 case-strategy.md「高秩不靠抽样」 |
| `large` 为 0 | 「规模档」行报 `large×0` | S2 调 `max_length` 与 `dim_values`，见 case-strategy.md「规模档」 |
| 重点 dtype 缺规模档 | 「重点 dtype」行报「缺 large」这类 | 约束器里按 dtype 现算门槛显式构造，见 case-strategy.md「规模档按字节算」 |

**这四条之外的覆盖面观察不拿去问用户**，包括组合空缺、「值域」行、结构上就是单值的轴、
全量的规模配比——判读见 case-strategy.md「覆盖判据解读」，在交付简表的「遗留」行陈述
一句即可。

## S4 冻结 golden

```bash
cd <工作目录> && <python> <skill>/scripts/freeze_golden.py
```

跑几个节点由 `facts.json` 的 `accuracy.kind` 决定，命令是同一条：

| `accuracy.kind` | 起什么节点 | 基线是谁 | 要 NPU 吗 |
| --- | --- | --- | --- |
| `torch`（缺省） | 只起 cpu | YAML `name` 指的 torch 接口 | 不要 |
| `builtin` | pyaclnn + cpu | CANN 装机目录里的内置同名 aclnn 接口 | **要**，先 source `set_env.sh`，另见 [builtin-baseline.md](builtin-baseline.md) |

输出收进 `golden/`，成功条数与基线目录名写进 `golden/manifest.json` 的 `baseline_dir`
——跑测侧的 `accuracy_load` 读的是它。

**同时把输入张量冻进 `inputs/<用例 id>/input.bin`。** 不冻的话跑测侧要按
`torch.manual_seed(case_id)` 现生成，一旦两侧 atk 版本的生成逻辑不一致，golden 还是
老的、输入变成新的，**比对全错且不报错**。冻下来之后 ATK 与独立 C++ 执行器读同一份
字节。条数少于用例数时脚本打到 stderr——缺的那些用例跑测侧的 C++ 路径跑不了。

| 情形 | 去向 |
| --- | --- |
| 全部成功 | S4 通过，用例包完成 |
| 部分失败 | 失败的是基线跑不出来的用例，回 S2 修 YAML 或执行器；CPU 标杆跑不出来的在 NPU 上也无法比对，**不要剔掉了事** |
| 全部失败 | `name` 的 torch 接口名写错或执行器没注册上；`kind=builtin` 时还可能是没 source CANN |

**成功后脚本自己归位**，打印 `归位 …→ gen/` 与 `扫除 …` 两行：生成侧专用的四样
进 `gen/`，`atk case` 的副产物 `result/` 与 `__pycache__/` 删掉。**不要手工搬这些
文件**——脚本按上面的用例包结构一次做完，手工搬会漏掉扫除那一半。

## 用例包的落盘布局

S4 通过后 `<输出目录>/<op>/` 就是交付物，直接交给 `repo-task-atk-accept`：

```text
<输出目录>/<op>/
├── cases.json           精度全量
├── facts.json           算子事实表，每项带 source
├── perf/cases.json      性能子集，50 条分层抽样；cross_dtype 时 50 对共 100 条
├── perf/manifest.json   逐条规模档 `bands`；cross_dtype 还带配对表 `pairs`
├── inputs/<id>/input.bin  冻结的输入张量，跑测侧两种执行器读同一份
├── golden/              标杆输出 + manifest.json（基线目录见 baseline_dir），全量与子集共用
├── function_<op>.py     有才放。**跑测侧也读它**，所以留在根，不进 gen/
└── gen/                 生成侧专用，跑测侧一行都不读
    ├── <op>.yaml            用例设计
    ├── <op>_constraint.py   有才放
    ├── env.gen.json         生成侧环境指纹：冻 golden 时的 atk / torch 版本
    └── atk.log              生成过程的日志
```

**根目录那七样就是交付面，`gen/` 里的四样跑测侧一行都不读。** 生成期它们都在工作
目录里（`atk case` 和本 skill 的脚本都按 CWD 找），S4 冻结成功后 `freeze_golden.py`
一次性归位，并扫掉 `result/` 与 `__pycache__/` 这两个 `atk case` 的副产物。
`result/` 下是 `atk case` 写的 xlsx，**长得就是一份验收报告**——跑测侧 A1 的
`cp -r` 会把它原样搬进 `input/`，翻到的人会拿它当结论，所以不留。

归位只在冻结成功后做：失败时现场留在原地，失败提示里那句「查 atk.log」才指得对
地方。归位之后回头重跑 S2/S3 不受影响，`gen_cases.py` 工作目录找不到就去 `gen/` 找。

不做 SHA256 封印，也不做只读锁。可靠性由 S1–S4 的出口判据保证。
