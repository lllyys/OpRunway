---
name: repo-task-atk-accept
description: >-
  在 NPU 上编译部署社区算子工程，跑精度与性能，给出验收结论。用例来自 repo-task-case-gen
  产出的用例包，或任务书自带的跑测件（sparse 系列如此）。当用户给出算子工程与其中一种
  用例、要跑精度性能测试、要判断算子能否验收通过时使用；两种用例都没有时改用
  repo-task-case-gen。
---

# ATK 算子跑测验收

用例包 + 算子工程 → 编译部署 → 精度 → 性能 → 结论。

## 入口参数

| 参数 | 含义 | 取值约束 | 初值推断 |
| --- | --- | --- | --- |
| `用例包` | 生成侧的 `<输出目录>/<op>/` | 含 `cases.json`、`perf/cases.json` 与 `golden/`（含 `manifest.json`） | 用户给出 |
| `自带跑测件` | 任务书带的 `*_testCase/` 一类目录 | 含用例清单、执行器插件与 ATK 的节点 yaml。**给了它就走自带件路，用例包不参与** | 任务包里与任务书同级的目录 |
| `工程目录` | 待验收算子目录 | 含 `op_kernel/`、`op_host/` 与 `docs/` | **用户点名，不许自己挑**；没给按 A0 列候选等回答 |
| `母仓` | 算子所属的开源仓根 | 含 `build.sh` 与 `experimental/` | 用户给出；工程目录自己就在仓里时不用给 |
| `输出目录` | 验收现场落在哪 | 已存在的可写目录，**不许落进仓里或算子工程里** | 没给按 A0 提候选，**等用户确认**再建 |
| `register_module` | 把算子注册进 torch dispatch 的那个 python 模块名 | **只在 `backend=npu` 时要**，A2 与每轮开跑前的探针都靠它 | 算子工程的 `README` 或 `examples/` 里找；**找不到就在 A0 一起问，不要猜**——猜错时报错指向模块名，不指向 A2 的参数 |

下面四个是派生值，不用问用户：`<现场>` = `<输出目录>/<op>-verify/`；
`<python>` 取 `env.json` 里那个能 `import atk` 的解释器；`<op>` **逐字等于**
`facts.json` 的 `aclnn_name`；`<device>` 不填，脚本 `auto` 现查空闲卡。

## 执行剖面

**先读 `input/facts.json` 的 `backend`，它决定后面三个阶段做不做。**
分流判据在生成侧，跑测侧只读不重判——两处各判一次必然漂。

```bash
<python> -c "import json;print(json.load(open('../input/facts.json')).get('backend','aclnn'))"
```

| `backend` | 算子长什么样 | 被测节点 | A2.6 tiling | A3.5 独立执行器 |
| --- | --- | --- | --- | --- |
| `aclnn`（缺省） | 两段式 C 接口 | `pyaclnn_0` | 做 | 做 |
| `npu` | 注册进 torch 的 dispatch | `npu_0` | **跳过** | **跳过** |

跳过的两阶段要在 A5 报告里写明「不适用」并给理由，留空看不出是没做还是没结论。

**这一轴只管上面这三件事。** 另外两件常被算到它头上，判据其实在别处：

| 事 | 判据 | 谁在判 |
| --- | --- | --- |
| 工程怎么编、装哪种包 | A2 探到的工程形态 | `build_install.py` 自己探 |
| `inputs/` 读不读、非连续怎么混 | 用例包里有没有这份产物 | `run_atk.py` 自己判：目录在就 `--input_data` 直读；不在且剖面是 `npu` 就按用例自带的编码走；不在且剖面是 `aclnn` 就退 3。非连续按 `facts.non_contiguous.ratio` 混进同一轮 |

`npu` 剖面还多要一个入口参数 `register_module`，见下。两条剖面的差异都在
[npu-profile.md](references/npu-profile.md)。

## 验收现场

```text
<输出目录>/<op>-verify/     ← 下称 <现场>
├── report/   结论三件
├── repro/    最小复现包
├── input/    输入副本，**只读语义**
└── work/     **所有命令的工作目录**
```

| 纪律 | 违反的后果 |
| --- | --- |
| 每条命令带 `cd <现场>/work &&` 并 `source evidence/env.sh` | 构建能过，跑测加载到 CANN 内置同名算子，测的不是待验收实现 |
| 跑测命令全部带 `--op <op>` | 跑错现场不报错，白跑一轮 |
| 用例包一律 `../input/` 前缀，产物一律写 `stage/` | 产物散落，`verdict.py` 读不到 |
| 卡号不填 | `--devices` 与 `--isolate-devices` 都默认 `auto`，开跑前现查，并发**最多 4 张**（机器公用）。抄 `env.json` 的卡号会撞上别人后来占的卡，见 troubleshooting.md「共卡」 |

并行验收多个算子时才显式给卡号，取 `env.json` 的 `npu.free_devices`
（**不是 `device_ids`**）。被占退 3，见 troubleshooting.md「共卡」。

## A0 入口对齐（不可跳过）

用户没给工程目录时列候选，不要替他选：

```bash
find <母仓> -type d -name "<snake>" -not -path "*/build*"   # snake = 算子名转下划线
git -C <母仓> log --oneline -3 -- <每个候选>
```

**候选只有一个也要确认** —— 算子名与目录名常常不同，唯一候选一样可能是实现同一套
接口的另一个算子；装错时 A2 判不出来，要跑到 A5 才表现成「整类 dtype 不达标」。

输出目录没给时提一个候选（`<母仓同级>/verify/`），确认前不 `mkdir`。

| 情形 | 去向 |
| --- | --- |
| 两个路径都拿到用户确认 | 进 A1 |
| 任一没拿到 | 停下，输出 `待确认·未验收 @A0`，把候选表给用户 |

**这一步只跑上面两条命令，不打开任何文件。** 任务书正文留到 A1 写 `facts.json`
时读，自带件的执行器留到 A2.5 冒烟失败时读，性能判据表留到 A4 读——**提前读进来
的到那一步已经隔了几万 token，判据反而不在手边，等于读两遍。**

## 主流程

每一步的命令、退出码与判据在「命令在哪」那一列指的文件里，**执行到那一步再读**。
**走自带件路时 A1 起整条改看 [kit-acceptance.md](references/kit-acceptance.md)**，
下表只用来看阶段顺序与出口判据。

| 阶段 | 做什么 | 命令在哪 | 出口判据 |
| --- | --- | --- | --- |
| A0 入口对齐 | 工程目录与输出目录**由用户点名，不许自己挑** | 本文 A0 | 用户明确回答 |
| A1 接收与环境 | 建分区、复制输入、探环境 | [intake.md](references/intake.md) | `probe_env.py` 退 0 |
| 口径播报 | **把本轮口径打印给用户，打完直接进 A2，不等回答** | [intake.md](references/intake.md) | 表已打印 |
| A2 编译安装 | 合进母仓、构建、装包、验证生效 | [build-deploy.md](references/build-deploy.md) | `build_install.py` 退 0 |
| A2.4 自带件体检 | 静态判得死的不兼容一次列全。**只在自带件路上做** | [kit-acceptance.md](references/kit-acceptance.md) | `kit_lint.py` 退 0 |
| A2.5 冒烟 | 每档各一条先跑起来 | [build-deploy.md](references/build-deploy.md)；自带件路改看 kit-acceptance.md | `--mode smoke` 退 0 |
| A2.6 tiling 归属 | 查 ATK 用的是谁的 tiling。**`backend=npu` 时跳过** | [run-accuracy.md](references/run-accuracy.md) | 已判定（0 或 1 都算过关） |
| A3 精度 | 全量比对，两种布局混在这一轮 | [run-accuracy.md](references/run-accuracy.md) | `stage/accuracy.json` 已落 |
| A3.1 隔离复验 | **有失败就必须做**，分出批内污染与真实缺陷 | [run-isolate.md](references/run-isolate.md) | 已裁决 |
| A3.5 独立执行器 | 换执行器与 tiling 再跑。**`backend=npu` 时跳过** | [standalone-executor.md](references/standalone-executor.md) | 已裁决 |
| A4 性能 | 按 `performance.kind` 出倍率，先逐条核任务书口径 ATK 表达不表达得了 | [run-performance.md](references/run-performance.md) | 状态非空 |
| A4.5 外部量测 | **只在上一步核出「有一条不能」时做**：换外部量测件采数，收编回来一起裁决 | [external-perf.md](references/external-perf.md) | `collect_perf.py` 退 0 |
| A5 结论 | 汇总裁决、出报告与复现包 | 本文 A5 | `verdict.py` 退 0 |

**A2 失败不得回生成侧重新出用例** —— 那是部署问题，不是用例问题。

**要给 `atk` 加上面没写的执行参数前，先读
[atk-task-options.md](references/atk-task-options.md)** ——`--disable_id_seed`
与 `--input_data` 会让这一轮的输入与冻 golden 时不是同一批，比对全部无意义
**且不报错**。不加参数就跳过这一步。

失败先分层再动手（环境 / 部署 / 接口适配 / 用例合法性 / 算子实现），分层表在
[troubleshooting.md](references/troubleshooting.md) 开头。

## A5 结论

```bash
cd <现场>/work && <python> <skill>/scripts/verdict.py
```

路径全走默认值，读 `stage/` 下的 JSON 与 `input/facts.json`，一次出
`report/` 三件与 `repro/`。**结论由它算，不由你写**，报告手改了下次重跑就没了；
只改模板重出报告用 `render.py`，它一个数都不重算。

**汇报只说三样**：`report/index.html` 的路径、精度按分档轴的表、性能按规模档的表。
**同样不用阶段编号与场景编号**，用户不知道 `A4-2b` 与 `P-01` 指什么。

## 工作边界

算子工程可以读，禁止的是**拿实现当验收依据**。可读的是 `docs/`、`README.md`、
`op_api/*.h` 的声明与构建入口；不可读的是 `op_kernel/`、`*_tiling.cpp`、
kernel 计算逻辑与实现里的报错字符串。

精度失败时**不要**去 kernel 里找原因然后判定「这是预期行为」。归因写到
「哪一组用例失败」为止，成因交给算子作者。**也不要去 CANN 装机目录找同名接口**
—— 同名不代表同签名。

## 停止条件

以下情形停止并输出 `阻塞·未验收 @A<n>`，写明失败阶段、判据、解除阻塞需要什么：

- A0 两个路径没拿到确认——输出 `待确认·未验收 @A0`，等回答，不许自己选
- A1 `probe_env.py` 退非 0；A2 构建失败或符号不可见；A2.5 整类被接口层拒（退 3）
- A3 全量**全部**执行失败（只挂一部分不拦）
- A3 或 A4 任一轮加载到的 so 不在本轮 vendor 目录下
- 目标卡上已有别的 `atk` 进程（退 3），换卡或收掉再来
- 输入缺 `facts.json`，或用例包缺 `cases.json` 与 `golden/`

**不要强跑全量、改动待验收工程、或用人工结论绕过判据。**

## 不要去翻的地方

| 想知道 | 不要 | 用 |
| --- | --- | --- |
| 跑测为什么失败 | 翻控制台输出 | `work/evidence/<mode>.log` 与 `work/atk_output/*/log/atk.log` |
| 任务是慢还是崩了 | 干等，或调大超时 | **不用判**，日志 180s 不增长就杀掉并报出来 |
| 精度到底过没过 | 看退出码或控制台表格 | `stage/accuracy.json` 的 `passed` |
| 算子为什么算错 | 读 `op_kernel/` | **不读**，归因到失败分组为止 |
| 签名对不对 | `nm -D` 装机的 `libopapi.so` | `build_install.py` 已核过符号 |
| 装的是不是待验收那份 | 看符号在不在 | `stage/install.json` 的 `op_dir` 与 `source` |
| `facts.json` 要填什么 | grep 脚本源码 | 跑 `<skill>/scripts/facts_schema.py` |
| 装机 ATK 收什么字段、注册表里有什么 | 翻 `site-packages/atk/` | `kit_lint.py` 的输出，它已经 import 了装机那一版 |

**失败先分层再动手**（环境 / 部署 / 接口适配 / 用例合法性 / 算子实现），
分层表在 [troubleshooting.md](references/troubleshooting.md) 开头。

## 长轮起跑与等待

A2、A3、A3.1、A4 少则十几分钟，三件事：

1. **先播报再起跑**：跑什么、多久、**不用回应**。等待期间工具不回显，
   少这一句用户看到的就是十几分钟不动的终端
2. `nohup ... &` 起跑
3. 等待只用 `wait_for.py`，**不要 `sleep N` 或 `pgrep` 循环**。
   **产物与进程两个条件都给**：

```bash
<python> <skill>/scripts/wait_for.py --file stage/<mode>.json \
    --pattern "run_atk.py --mode <mode>" --progress-from evidence/<mode>.log
```

退出码与下一步由脚本自己打印。`--timeout` 是心跳间隔不是任务上限，缺省 120s，
**不要设 3600**——那是一小时不吭声，与卡死无法区分。
