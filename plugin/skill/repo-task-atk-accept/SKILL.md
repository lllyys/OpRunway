---
name: repo-task-atk-accept
description: >-
  在 NPU 上编译部署社区算子工程，用 repo-task-case-gen 产出的用例包跑精度与性能，给出验收结论。
  当用户给出算子工程与用例包、要跑精度性能测试、要判断算子能否验收通过时使用；
  只有任务书还没有用例包时改用 repo-task-case-gen。
---

# ATK 算子跑测验收

用例包 + 算子工程 → 编译部署 → 冒烟 → 精度 → 性能 → 结论。

## 入口参数

| 参数 | 含义 | 取值约束 | 初值推断 |
| --- | --- | --- | --- |
| `用例包` | 生成侧的 `atk-case-<op>/` | 含 `cases.json` 与 `golden/` | 用户给出 |
| `工程目录` | 待验收算子目录 | 含 `op_kernel/`、`op_host/` 与 `docs/` | 用户给出 |
| `母仓` | 算子所属的开源仓根 | 含 `build.sh` 与 `experimental/` | 用户给出；见 build-deploy.md「母仓对照」 |
| `工作目录` | 本轮验收现场 | `atk-verify-<op>/`，从用例包复制 | A1 第 1 步建立 |
| `<python>` | 跑量具的解释器 | 能 `import atk` 与 `torch_npu` | `probe_env.py` 写进 `env.json` |

**每条命令自己带 `cd <工作目录> &&`，且以 `source evidence/env.sh` 开头。**
`env.sh` 由 `probe_env.py` 生成，含 CANN 环境与自定义算子包路径。少了它，
构建能过但跑测会加载到 CANN 内置的同名算子，测的就不是待验收实现。

## 前置检查

```bash
mkdir -p atk-verify-<op> && cp -r <用例包>/* atk-verify-<op>/ && cd atk-verify-<op>
<python> <skill>/scripts/probe_env.py --op <op> -o env.json --write-env-sh evidence/env.sh
```

| 退出码 | 含义 | 去向 |
| --- | --- | --- |
| 0 | atk、torch_npu、CANN、NPU 全可用 | 进 A2 |
| 2 | 缺件 | 停止，输出 `阻塞·未验收 @A1`，列出 `env.json` 的 `missing` |

**在复制出来的副本里验收，不在原用例包里跑。** 原包要留着复验和给别的 PR 复用。

## 主流程

| 阶段 | 做什么 | 产物 | 出口判据 |
| --- | --- | --- | --- |
| A1 接收与环境 | 复制用例包，探环境，生成 `env.sh` | `env.json` `env.sh` | `probe_env.py` 退出码 0 |
| A2 编译安装 | 算子目录合进母仓、构建、装包、验证生效 | `install.json` | `build_install.py` 退出码 0 |
| A3 冒烟 | 抽 30 条跑一轮，确认调的是自定义算子 | `smoke/cases.json` `smoke_result.json` | 执行失败率 ≤ 20% |
| A4 精度 | 全量跑 `accuracy_load` 比对 golden，有失败则隔离复验 | `accuracy.json` `isolate.json` | 已裁决（通过或不通过都算过关） |
| A5 性能 | 按 `facts.json` 的性能形态跑 | `performance.json` | 状态非空 |
| A6 结论 | 汇总裁决与证据链 | `verdict.json` `report.md` | 三段齐全 |

**A2 或 A3 失败不得回生成侧重新出用例。** 那是部署问题，不是用例问题。

### A2 编译安装

先读 [build-deploy.md](references/build-deploy.md)。

```bash
cd <工作目录> && source evidence/env.sh && <python> <skill>/scripts/build_install.py \
    --op <op> --project <工程目录> --parent-repo <母仓> --soc <soc> -o install.json
```

`--soc` 从 `env.json` 的 `soc` 读，A3 机器是 `ascend910_93`，A2 是 `ascend910b`。

它做四件事：把工程目录同步进母仓对应位置、跑 `build.sh`、装 `build_out/*.run`、
再验证 `ASCEND_CUSTOM_OPP_PATH` 下确实有本算子的 `libcust_opapi.so` 符号。

| 退出码 | 含义 |
| --- | --- |
| 0 | 装好且符号可见 |
| 2 | 构建失败，日志尾部已打印 |
| 3 | 装好了但符号不可见——包名或 vendor 目录不对，见 build-deploy.md「装完了但符号找不到」 |

### A3 冒烟

```bash
cd <工作目录> && source evidence/env.sh
<python> <skill>/scripts/sample_smoke.py -i cases.json -o smoke -n 30
<python> <skill>/scripts/run_atk.py --mode smoke -c smoke/cases.json --golden golden -o smoke_result.json
```

**冒烟看的是执行成功数，不是精度通过数。**

| 冒烟结果 | 含义 | 去向 |
| --- | --- | --- |
| 执行失败率 > 20% | 部署或适配坏了 | 停在 A3，`run_atk.py` 退出码 2 |
| 执行失败率 ≤ 20% | 部署是好的，个别用例触发算子缺陷 | 进 A4 测准，再 `--mode isolate` 复验 |
| 只是精度不通过 | 真实发现 | 进 A4 |

**按失败率判，不按有没有失败判。** 部署坏了会让绝大多数用例都跑不起来；
只挂零星几条说明部署没问题，那几条是算子缺陷——正是要在 A4 测准的东西，
在这里拦死就永远出不了验收结论。

同一个部署问题重复 180 遍不产生新信息，所以高失败率才拦。

执行失败按 [troubleshooting.md](references/troubleshooting.md) 定位，改的是部署不是用例。

### A4 精度

读 [run-accuracy.md](references/run-accuracy.md)。

```bash
cd <工作目录> && source evidence/env.sh && <python> <skill>/scripts/run_atk.py \
    --mode accuracy -c cases.json --golden golden -o accuracy.json
```

它拼 `atk node --backend aclnn ... node --backend cpu --task accuracy_load
--output_path golden ... task --task accuracy`，跑完解析报告写 `accuracy.json`。

**退出码 0 不等于精度通过。** 结论看 `accuracy.json` 的 `passed` 与 `pass_rate`。

**有执行失败就必须隔离复验，再进 A5。**

```bash
cd <工作目录> && source evidence/env.sh && <python> <skill>/scripts/run_atk.py \
    --mode isolate -c cases.json --golden golden --ids-from accuracy.json -o isolate.json
```

aicore 异常会把设备打到异常状态，同批次后面的用例跟着全挂。不隔离就会把
1 条真实失败报成 47 条——真机上 IndexFillTensor 就是这样：`accuracy.json` 里
47 条执行失败，逐条单独重跑后只有 1 条是真的，其余 46 条全过。

`isolate.json` 的 `real_failures` 才是真实失败，`verdict.py` 会自动读它。
`accuracy.json` 没有执行失败时跳过这一步。

### A5 性能

读 [run-performance.md](references/run-performance.md)。形态由 `facts.json` 的
`performance.kind` 决定，不由你选：

| kind | 跑什么 | 结论怎么写 |
| --- | --- | --- |
| `none` | 一轮 `performance_device` 采绝对耗时 | `未评级(无基线)` |
| `builtin` | 再跑一轮加 `--builtin-baseline` 的基线轮 | 逐用例比 Device 耗时中位数 |
| `cross_dtype` | 一轮跑测内按 `pairs` 分组对比 | 每对各出一个结论 |

```bash
cd <工作目录> && source evidence/env.sh
<python> <skill>/scripts/sample_smoke.py -i cases.json -o perf -n 50
<python> <skill>/scripts/run_atk.py --mode performance \
    -c perf/cases.json --golden golden --facts facts.json -o performance.json
```

**性能轮先抽样再跑**，全量 200 条要二十多分钟且多数是重复等价类。

**精度没通过就不评级性能。** 一个算错的算子跑得快没有意义。

已经跑过性能轮再发现精度不过时，采到的耗时不丢——`verdict.py` 会把状态写成
`未评级(精度未通过，仅留参考数据)`，报告里标明只作修复后的对照，不构成结论。

### A6 结论

```bash
cd <工作目录> && <python> <skill>/scripts/verdict.py -o verdict.json --report report.md
```

它读 `install.json`、`accuracy.json`、`performance.json` 与 `facts.json`，
推出总结论并写 `report.md`。**结论由它算，不由你写。**

## 工作边界

算子工程可以读，禁止的是**拿实现当验收依据**。

| 可读 | 不可读 |
| --- | --- |
| `docs/aclnn*.md`、`README.md` | `op_kernel/`、`op_host/*_tiling.cpp` |
| `op_api/*.h` 的函数声明 | kernel 计算逻辑、内部断言 |
| `CMakeLists.txt`、构建入口 | 实现里的报错字符串常量 |

精度失败时**不要**去 kernel 里找原因然后判定「这是预期行为」。
失败就是失败，归因写到「哪一组用例失败」为止，成因交给算子作者。

**不要去 CANN 装机目录找同名接口。** 社区算子与官方接口重名是常态，
装机目录下那份是另一份已发布代码，同名不代表同签名。

## 停止条件

以下情形停止并输出 `阻塞·未验收 @A<n>`，写明失败阶段、判据和解除阻塞需要什么：

- A1 `probe_env.py` 退出码非 0
- A2 构建失败，或装完符号不可见
- A3 冒烟执行失败率超过 20%（低于此不拦，精度不通过也不拦，都要进 A4 测准）
- 用例包缺 `cases.json`、`golden/` 或 `facts.json`

**不要强跑全量、改动待验收工程、或用人工结论绕过判据。**

## 不要去翻的地方

| 想知道 | 不要 | 用 |
| --- | --- | --- |
| 跑测为什么失败 | 翻控制台输出 | `evidence/<mode>.log` 与 `atk_output/*/log/atk.log`，捞法见 troubleshooting.md |
| 精度到底过没过 | 看退出码或控制台表格 | `accuracy.json` 的 `passed` |
| 算子为什么算错 | 读 `op_kernel/` | **不读**。归因到失败分组为止，成因交给算子作者 |
| 签名对不对 | `nm -D` 装机的 `libopapi.so` | `build_install.py` 已经核过符号；签名由 pyaclnn 自检 |

**失败先分层再动手**（环境 / 部署 / 接口适配 / 用例合法性 / 算子实现），
分层表在 [troubleshooting.md](references/troubleshooting.md) 开头。
改错层次比不改更糟。

## 参考资料

- [build-deploy.md](references/build-deploy.md) — 母仓构建、装包与生效验证
- [run-accuracy.md](references/run-accuracy.md) — 节点拓扑、accuracy_load 与报告字段
- [run-performance.md](references/run-performance.md) — 三种性能形态
- [troubleshooting.md](references/troubleshooting.md) — 部署、绑定与执行失败定位
