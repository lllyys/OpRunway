# 用例包契约（ATK 链路）

**改 `repo-task-case-gen` 或 `repo-task-atk-accept` 且动到下表任一项时读这份。**
只改单侧内部逻辑、或在开发别的 skill，用不到。

本文只管 ATK 链路的用例包。ops-blas 链路走六件包，契约另有一份：
[blas-case-package-contract.md](blas-case-package-contract.md)。

这份不放在任何一个 skill 目录里，因为契约不属于任何一侧：放进生成侧，改
`run_atk.py` 的人看不到；两边各放一份，两份必然漂。

## 使用态不强制串联

契约约束的是开发，不是使用。两个 skill 单独都能用：

- 生成侧吃社区**原样**的任务书，不要求由 `repo-task-doc-write` 产出
- 跑测侧吃任何符合下面结构的目录，不要求由生成侧产出
- 两侧的 `SKILL.md` 都不写「先去跑另一个 skill」这种前置——它们各自吃文件，不吃执行顺序

`repo-task-doc-write` 与这两个没有契约，改它不影响它们。

## 用例包结构

用例包是 `<输出目录>/<op>/`——输出目录由用户在入口指定，算子名当子目录名做隔离。
它是个普通目录，不做 SHA256 封印，不做只读锁：

```text
<输出目录>/<op>/
├── cases.json           精度全量
├── facts.json           算子事实表，每项带 source
├── perf/cases.json      性能子集，生成侧抽好
├── perf/manifest.json   逐条规模档；cross_dtype 还带配对表
├── inputs/<id>/input.bin  冻结的输入张量，两侧执行器都读它
├── golden/              标杆输出 + manifest.json（基线是谁见 baseline_dir）
├── function_<op>.py     有才放。跑测侧也读它，所以在根，不进 gen/
├── kit/                 采纳自带件时才有。整棵树原样搬来，function_<op>.py 靠它
├── kit_fixes/           自带件在装机 ATK 上跑不起来时才有。修复件与 run.sh，原件不动
└── gen/                 生成侧专用，跑测侧一行都不读
    ├── <op>.yaml            用例设计
    ├── <op>_constraint.py   有才放
    ├── env.gen.json         生成侧环境指纹，**冻 golden 时的 atk / torch 版本**
    └── atk.log              生成过程的日志
```

**根目录就是交付面。** 分两层不是为了好看：跑测侧 A1 做的是
`cp -r <用例包>/* input/`，无差别全搬。混在一层时 `atk case` 的副产物
`result/`（下面是 xlsx，**长得就是一份验收报告**）会跟着进 `input/`，
被当成结论。生成期这些文件都在工作目录里，S4 冻结成功后由
`freeze_golden.py` 一次性归位并扫除 `result/`、`__pycache__/`。

归位后重跑 S2/S3 仍然可用：`gen_cases.py` 的 YAML 与约束器查找是
「工作目录优先，其次 `gen/`」。

## 十四项契约

由生成侧定、跑测侧读。**改任一项两侧都会动**：

| 契约项 | 跑测侧谁在读 | 破坏后的表现 |
| --- | --- | --- |
| `cases.json` 这个文件名（全量与 `perf/` 子集同名） | `run_atk.py` 的性能轮与隔离复验子集 | ATK 拿用例文件基名当 golden 子目录名（`atk/tasks/result_process.py:67`），改名后报「标杆输出为空」 |
| `golden/` 目录布局 `<根>/<backend>/<用例文件基名>/<id>/` | `run_atk.py` 的 `accuracy_load` | 同上。**不报错，只是一条都匹配不上** |
| `facts.json` 的 `performance.kind` | `run_atk.py` 的 `_perf_kind` 决定性能跑几轮、`verdict.py:67` 选裁决分支 | 取值不在 `none`/`builtin`/`cross_dtype`/`threshold` 里时 `run_atk.py` 退 3 停在 A5（`verdict.py` 那侧仍是静默退回 `none`） |
| `facts.json` 的 `non_contiguous.required` | `run_atk.py` 的 `_non_contiguous` 决定精度轮加不加 `--slice_input non_contiguous` | 缺字段或读不到时静默按 `false` 走，非连续场景零覆盖且不报错 |
| `function_<op>.py` 这个命名 | `run_atk.py` 在**用例包根**（golden 的父目录）下 `glob("function_*.py")` | 自动挂不上，CPU 标杆执行器找不到。**跑测侧 2026-08-27 起 CWD 不再是用例包目录**，所以它按 golden 的父目录找，不按 CWD 找 |
| `golden/manifest.json` 的 `baseline_dir` | `run_atk.py` 的 `_load_node` 定 `accuracy_load` 节点的 backend 与名字；`verdict.py` 的 `_acc_baseline` 写进报告 | 加载节点与冻结节点不同名，golden **一条都匹配不上**；写死成 `cpu` 时内置基线的用例包会拿 `cpu_builtin/` 里的废数据比对，全错且不报错 |
| `kind=cross_dtype` 时 `perf/cases.json` 里的用例**按 shape 成对** | `verdict.py` 的 `_cross_dtype_lines` 逐对算加速比 | 生成侧不成对时两条比的不是同一个活儿，加速比里混着 shape 效应（同 dtype 内不同 shape 耗时差几个量级）。**不报错**，照样打出「不劣化/劣化」的结论，只是那个结论没有意义 |
| `perf/manifest.json` 的 `bands` 与 `pairs` | `verdict.py` 的 `_band_rows` 出分规模档的加速比表 | 缺文件时**退回按 dtype 分组取中位数相除**，报告里会写明「做不了分规模档的加速比」。档位是生成侧 `size_band` 的字节门槛算的，**跑测侧不许重算**——两套阈值不一致真机上出过事 |
| `inputs/<用例 id>/input.bin` 这个布局（**用例入参全是 attr 时不适用**，见下） | 跑测侧 `run_cxx.py`；ATK 侧走 `atk task --input_data` 时也是它 | 缺目录时 `run_cxx.py` 退 3 并指回生成侧。**布局必须摊平成 `<id>/input.bin`**——ATK 自己存盘会多一层 save_name（`input/cases/<id>/`），照那个布局放 `--input_data` 一条都读不到。**目录名也钉死在包根的 `inputs/`**：两侧消费者都按这个默认值找，`golden/manifest.json` 里**不记** `inputs_dir`——记一个可变路径进来等于宣称它可以不叫 `inputs/`，而写 `inputs.t` 却让消费者去找 `inputs/` 时两边都不报错，只是一条都读不到 |
| `facts.json` 的 `backend` | `run_atk.py` 的 `_profile` 选执行剖面（被测节点用哪个 ATK 后端、跳不跳 A2.6／A3.5）；`verdict.py` 据它标注两阶段「不适用」 | 取值不在 `aclnn`／`npu` 里时 `run_atk.py` 退 3。**填错剖面才是坏的那种**：`npu` 误填成 `aclnn` 时 ATK 起 pyaclnn 节点绑不上符号，报表里被测节点那几列压根不存在，`_case_verdicts` 一条都判不出来，**通过率算成 0 而日志里没有报错**。反向填错则被测列名前缀对不上，`_device_times` 把被测那列当成标杆，性能数悄悄错位 |
| `facts.json` 的 `adopted_files` 与 `kit_fixes` | `make_repro.py` 生成 `op-testcase/original/MANIFEST.md`，并把 `kit_fixes` 逐行写进复现包 README | 采纳自带件时 golden 是任务方手写实现算的，那份换一个字节标杆就换一批。缺 `adopted_files` 时复测的人分不清手上这份与验收那轮是不是同一个；改过自带件却不写 `kit_fixes` 时，改动过的文件看起来像原件，**结论追不回来源** |
| `kit_fixes/` 这个目录名 | `make_repro.py` 把 `kit/` 搬成复现包的 `op-testcase/original/`、`kit_fixes/` 搬成 `op-testcase/fixed/`，并按 `fixed/run.sh` 在不在决定 README 写哪条命令 | 修复件不在这个目录名下时复现包只带原件，而原件在验收机上跑不起来——**拿到包的人一条都跑不了，且包里没有任何迹象说明为什么**。跑测侧只搬运不构造命令，`run.sh` 缺了就退化成「跑法见目录说明」 |
| `kit/` 这一层与 `function_<op>.py` 的依赖 | `run_atk.py` 在用例包根 `glob("function_*.py")` 拿到入口，入口自己把 `kit/<自带件目录>` 挂进 `sys.path` | 采纳自带件的包里，`function_<op>.py` **只有 import 语句**，算子语义在 `kit/` 底下。只搬入口不搬 `kit/` 时 A1 的 `cp -r` 之后 import 失败，ATK 静默走内置路径，测的不是那个钩子。**拆平 `kit/` 也不行**——原件里有 `parents[1] / "common"` 这类相对引用 |
| `gen/` 这一层 | 跑测侧 A1 从 `input/gen/env.gen.json` 读冻 golden 那台机的指纹，写进验收报告的环境栏 | 平铺回根目录时报告的环境栏拿不到指纹，A1 按「未知」走且不报错；更糟的是 `atk case` 的 `result/` 跟着 `cp -r` 进 `input/`，那底下的 xlsx 长得就是一份验收报告 |
| `env.gen.json` 这个文件名 | **没人按文件名读**，约束是反向的：跑测侧 A1 的 `probe_env.py` 往工作目录写 `env.json` | 生成侧也叫 `env.json` 时，A1 的 `cp -r` 之后立刻被跑测机的指纹覆盖。冻 golden 用的哪个 ATK 版本**就此丢失且无痕**，跨版本 golden 漂移查不出来 |

## 改契约的纪律

1. 两份 skill 的 `CLAUDE.md` 都读一遍，确认改动没和某一侧的红线冲突
2. 两侧都在真机上复跑，**不能只跑改动那一侧**
3. 改完更新上表，**并同步运行态那一侧的表述**——十四项在运行态各有归属，
   跑 skill 的 agent 读的是它们，不是本文件：

| 契约项 | 运行态在哪讲 |
| --- | --- |
| `cases.json` 文件名 | 跑测侧 `references/run-accuracy.md`、`troubleshooting.md` |
| `golden/` 布局 | 跑测侧 `references/troubleshooting.md` 的自查三步 |
| `non_contiguous.required` | 生成侧 `references/interface-facts.md`「用例形态」+ `case-strategy.md`「非连续张量在跑测期切」+ `check_facts.py` 的入参形态校验 |
| `performance.kind` | 生成侧 `references/interface-facts.md` 词表 + 跑测侧 `SKILL.md` 裁决表与 A5 退出码表 + `references/run-performance.md`「四种形态」 |
| `kind=threshold` 时的 `criterion` | 生成侧 `check_facts.py` 必填校验 + `references/interface-facts.md`「性能基线四形态」 + 跑测侧 `verdict.py` 的 `_perf_status` 与 `references/run-performance.md` |
| `performance.sampling` | 生成侧 `references/interface-facts.md`「采样口径」+ `check_facts._check_sampling` + 跑测侧 `references/external-perf.md` 与 `assets/perf_harness.py:_sampling` |
| `baseline_dir` | 生成侧 `SKILL.md` 的 S4 表 + `references/interface-facts.md`「精度基线两形态」 |
| `function_<op>.py` | 生成侧 `references/plugin-authoring.md` |
| `env.gen.json` | 生成侧 `SKILL.md` 的 S0 与用例包结构 + 跑测侧 `SKILL.md` 的 A1 |
| `inputs/<id>/input.bin` | 生成侧 `SKILL.md` 的 S4 与用例包结构 + 跑测侧 `SKILL.md` 的「执行剖面」（判产物不判剖面那张表）与 `references/standalone-executor.md`「输入从哪来」 |
| `gen/` 这层与根目录的交付面 | 生成侧 `SKILL.md` 的用例包结构与 S4「成功后脚本自己归位」 + 跑测侧 `SKILL.md` 的 A1（`env.gen.json` 在 `gen/` 下） |
| `facts.json` 的 `backend` | 生成侧 `references/interface-facts.md`「backend：这条用例包走哪个执行剖面」（**唯一一份判据**，`kit-adoption.md` 只链过去）+ 跑测侧 `SKILL.md` 的「执行剖面」+ `references/npu-profile.md` |
| `kit/` 这层与 `function_<op>.py` 的依赖 | 生成侧 `SKILL.md` 的 S2′ + `references/kit-adoption.md`「采纳做了什么」 |
| `adopted_files` 与 `kit_fixes` | 生成侧 `references/kit-adoption.md`「自带件不全」与「与跑测侧的两项衔接」 + 跑测侧 `SKILL.md` 的 A5 产物表 |
| cross_dtype 成对子集 | 生成侧 `references/case-strategy.md`「跨 dtype 比性能时子集要成对」+ `SKILL.md` 的 S3 |

**`docs/` 下的文件 agent 跑 skill 时读不到。** 只改本文件不改上面那些位置，
开发者看得懂，执行的 agent 照旧按老规矩来。

第 2 条是硬的：前两项被破坏时跑测侧不报错，只是 golden 一条都匹配不上，
而报错文本指向用例包，不指向你改的那行。只跑单侧看不出任何异常。

## 内置 aclnn 基线：两侧都通了，但没跑过真实的待验收算子

`facts.json` 的 `accuracy.kind=builtin` 让生成侧把 golden 冻成**内置 aclnn 实现**
的输出（`golden/pyaclnn_builtin/`），跑测侧按 `baseline_dir` 找到它做 `accuracy_load`。
Bernoulli 在真机上跑通：生成 140 条、golden 140/140、两轮独立冻结逐位相同。

**还没验过的一段**：跑测侧的验证是把 `ATK_CUSTOM_OPP_PATH` 指向装机目录的
`libopapi.so`，也就是「内置实现对内置实现」，证的是 golden 找得到、比得上。
**没有跑过真正编译出来的待验收算子**——那要先走 A2 把 bernoulli 工程构建装包。
第一次拿它验真算子时，把结果补进两侧的 `CLAUDE.md`。

改这条契约时的额外注意：`--backend aclnn` 的待验收节点在 ATK 里叫 `pyaclnn_0`，
所以基线目录不能取这个名字，生成侧用 `-n builtin` 冻成 `pyaclnn_builtin`。
重名时 ATK 会把加载节点改名成 `pyaclnn_0_1`（`atk/configs/nodes_config.py:149`），
表现是「标杆输出为空」，而报错文本指向用例包，不指向节点命名。

## attr 编码的用例上失效的两项

判据是**用例形态**，不是 `backend`。曾按 `backend=npu` 一刀切，起因是自带件的
npu 用例恰好全是 attr 编码；自产的 npu 用例包声明真张量，这两项照常适用。

| 用例形态 | 判据 |
| --- | --- |
| 真张量声明 | `facts.json` 的 `params[]` 里有 `role: input` 且 `atk_type` 是 `tensor`／`tensors` |
| attr 编码 | 入参全是 attr，张量由执行器插件按其中的 seed 现造 |

后一种形态下这两项**不适用**，不是被破坏：

| 契约项 | 为什么不适用 | 两侧各怎么做 |
| --- | --- | --- |
| `inputs/<用例 id>/input.bin` | ATK 的 `--input_data` 读的是张量字节，喂不进 attr 编码的用例 | 生成侧冻不出来；跑测侧 `run_atk.py` 的 `main()` **按目录在不在判**，不按剖面判——`aclnn` 剖面缺目录退 3，`npu` 剖面缺目录按用例自带编码走，目录在则两个剖面都 `--input_data` 直读 |
| `non_contiguous.required` 为真的那一路 | `--slice_input` 在 ATK 后端基类里对已载入的输入张量做（`atk/tasks/backends/backend.py:160`），切不到执行器现造的那份。填 `true` 会多跑一整轮，两轮字节完全相同 | 生成侧填 `false`；`check_facts.py` 在**两个剖面上**都拦「入参全是 attr 却填 true」 |

**代价是「两侧输入是不是同一批」这个问题换了个答案。** 真张量声明的用例靠冻字节，
attr 编码靠用例把造数 seed 编码进参数——重跑必定同字节，但这一条没有量具核过。
ATK 有 `accuracy_dc`（确定性计算）任务可以核它，本链路**尚未接入**。

## 归因边界

跑测侧报错时先分清归哪一侧，改错侧比不改更糟：

| 现象 | 归哪一侧 |
| --- | --- |
| 参数数量不匹配、dtype 不在 ATK 词表 | 生成侧，改 `<op>.yaml` 与 `facts.json` |
| 「标杆输出为空」 | 契约被破坏，两侧对一遍上表 |
| 精度不符、aicore 异常 | 哪一侧都不是，是被测算子的缺陷，照实报 |

另有两条反向红线，各自写在自己那侧的 `CLAUDE.md` 里，不在这里复制：
生成侧「不为让跑测通过而改窄用例」，跑测侧「不就地 patch 用例包」。
