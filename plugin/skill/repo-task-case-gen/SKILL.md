---
name: repo-task-case-gen
description: >-
  按社区算子任务书生成 ATK 测试用例并冻结 golden，产出交给跑测侧的用例包；
  任务自带跑测件时改为采纳它们并补齐用例包缺的那几项。
  当用户给出算子任务书、要为算子设计测试用例、要生成 ATK YAML 或用例 JSON、
  或者说「任务自带了跑测脚本」时使用；
  已经有用例包要在 NPU 上编译跑测时改用 repo-task-atk-accept。
---

# ATK 用例生成

任务书 → 算子事实表 → ATK YAML → 用例 JSON → golden → 用例包。

## 入口参数

| 参数 | 含义 | 取值约束 | 初值推断 |
| --- | --- | --- | --- |
| `任务书` | 社区算子任务书 | 任意 markdown 结构，不要求编号章节 | 用户消息给出 |
| `自带件目录` | 任务方交付的跑测件 | 含用例清单与执行器插件；**只在走 S2′ 时要** | 任务包里与任务书同级的 `*_testCase/` 一类目录；没有就走 S2/S3 |
| `op` | 算子名 | aclnn 接口去掉 `aclnn` 前缀，如 `Roll` | 从任务书标题或接口名解析 |
| `工程目录` | 算子工程根 | 含 `docs/aclnn*.md` 的目录 | 用户给出；没给就按任务书点名的仓名在本机找一次（`find`，本地检索不违反「事实只从本地取」），找不到就跳过第 2 跳 |
| `输出目录` | 用例包的落点根目录 | 用户给的路径，**不建在算子工程里** | 用户消息给出；**没给就问，不要猜一个** |
| `工作目录` | 所有相对路径的基准 | `<输出目录>/<op>/`，一个算子一个目录 | 进 S0 前 `mkdir -p` 建立 |
| `<python>` | 跑量具的解释器 | 能 `import atk` | `probe_env.py` 写进 `env.gen.json` 的 `python` |

三条路径纪律：

| 纪律 | 破了会怎样 |
| --- | --- |
| 一个算子一个 `<op>/` 子目录 | `cases.json`、`golden/` 是固定文件名，混目录后一个盖掉前一个 |
| 每条命令自带 `cd <工作目录> &&` | shell 不继承上一条的 `cd`，产物落到会话启动目录 |
| 重跑就地覆盖，要留上一轮先改名 | `freeze_golden.py` 先删整个 `golden/` 再冻 |

## 前置检查

```bash
mkdir -p <输出目录>/<op> && cd <输出目录>/<op> && <python> <skill>/scripts/probe_env.py -o env.gen.json
```

| 退出码 | 含义 | 去向 |
| --- | --- | --- |
| 0 | atk 与 torch 可用 | 进 S1 |
| 2 | atk 或 torch 缺失 | 停止，输出 `阻塞·未生成 @S0`，把 `env.gen.json` 的 `missing` 列给用户 |

生成侧只要 CPU 版 torch 与 atk，**不需要 NPU 和 CANN**。例外是 S1 把 `accuracy.kind`
填成 `builtin`，那时 S4 要在 NPU 上跑内置实现。

## 主流程

每一步的命令与判据在「命令在哪」那一列指的文件里，**执行到那一步再读**。

| 阶段 | 做什么 | 命令在哪 | 出口判据 |
| --- | --- | --- | --- |
| S1 事实表 | 读任务书与工程 `docs/aclnn*.md`，填 `facts.json` | [interface-facts.md](references/interface-facts.md) | `check_facts.py` 退 0 |
| S2 用例设计 | 写 `<op>.yaml` | [yaml-authoring.md](references/yaml-authoring.md) | `gen_cases.py --dry-run` 退 0 |
| S2 覆盖轴 | dtype、shape、attr 三条轴怎么铺 | [case-strategy.md](references/case-strategy.md) | 同上 |
| S2 规模档 | 按**字节数**分档，阈值只在这一处定 | [size-bands.md](references/size-bands.md) | 同上 |
| S2 精度阈值 | 只从标准里取，不凭记忆写 rtol/atol | [precision-standard.md](references/precision-standard.md) | 同上 |
| S2 插件 | 判要不要写、怎么写约束器与执行器 | [plugin-authoring.md](references/plugin-authoring.md) | 同上 |
| S3 生成用例 | 正式跑 `atk case`，同时抽性能子集 | [generate-and-freeze.md](references/generate-and-freeze.md) | 用例数 ≥ 100 |
| S2′ 采纳自带件 | **可选**。沿用任务方那批用例时替代 S2 与 S3 | [kit-adoption.md](references/kit-adoption.md) | `adopt_kit.py` 退 0 |
| S4 冻结 golden | 跑 CPU 标杆存盘，收尾归位；`accuracy.kind=builtin` 时另见 [builtin-baseline.md](references/builtin-baseline.md) | [generate-and-freeze.md](references/generate-and-freeze.md) | `freeze_golden.py` 退 0 |

**默认走 S2／S3，从接口原型自己设计用例。** S2′ 是可选支路，合流在 S4：

```text
S1 事实表（backend 与精度基线形态在这里定死）
     ├── 默认 ──────────→ S2 用例设计 → S3 生成 ─┐
     └── 决定沿用任务方那批用例 ─→ S2′ 采纳自带件 ─┤
                                                  └──→ S4 冻结 golden
```

**任务方自带跑测件不改变默认路径。** 自带件是任务方按自己的口径写的，
沿用它等于沿用他们的覆盖范围；实测见过的一份漏掉了自己任务书要求的
边界值与非连续两类。判据：

| 情形 | 走哪条 |
| --- | --- |
| 任务书给了接口原型 | **S2／S3**，声明真张量，覆盖由本仓的用例设计负责 |
| 接口原型拿不到，只有自带件 | S2′，并在交付简表里写明覆盖范围随自带件 |

两条都跑也可以：自产的那份出验收结论，自带件那份证明开发者达到交付门槛。
跑测侧会把自带件的脚本一并打进复现包。

**上下文被 compact 之后读 `facts.json` 接着走，不要回头重读 references。** 那一份不到
1 KB，装着基线、执行器结论、`focus_dtypes`、精度与性能形态。

### 交付简表

S4 通过后给用户**一张表**。行是固定的，没有的行写「无」，不要增删行，也不要贴覆盖总览
原文或转述成「覆盖良好」这种散文：

```markdown
| 项 | 值 |
| --- | --- |
| 算子 | <op>（aclnn<Op>） |
| 基线 | <facts.baseline>（来源 <baseline_source>） |
| 精度全量 | <N> 条 |
| dtype 分布 | <dtype×条数，重点 dtype 标注「重点」> |
| 秩 | <实测区间>（facts 声明 <a>–<b>，<无缺档 / 缺 x>） |
| 规模档 | scalar×<n>、small×<n>、medium×<n>、large×<n>（量具报四档，照抄它的数） |
| 性能子集 | <N> 条<，含 <M> 对同 shape 镜像（cross_dtype）> |
| golden | <成功>/<总数>，<体积> |
| 产物 | <工作目录绝对路径> |
| 遗留 | <轴退化 / 组合空缺 N 处 / 推断项 等，没有就写「无」> |
```

「遗留」那行只陈述，不拿它去问用户要不要补。

## 停止条件

以下情形停止并输出 `阻塞·未生成 @S<n>`，写明失败阶段、失败判据和解除阻塞需要什么：

- S0 `probe_env.py` 退出码非 0
- S1 任务书与工程文档都拿不到接口原型，且基线接口也推断不出参数顺序
- S2 dry-run 连续两次失败且第二次的报错与第一次不同类
- S4 golden 全部失败

**不要为了让命令通过而改小覆盖面、剔掉失败用例或跳过判据。**

## 翻源码的触发条件

`references/` 覆盖的是常见范式，不是 ATK 的全部能力。遇到没写的先查
[atk-surface.md](references/atk-surface.md) 的能力清单定位：

| 处境 | 怎么做 |
| --- | --- |
| 清单里有，`references/` 写了用法 | 用 `references/`，翻源码是白费预算 |
| 清单里有，`references/` 标「未展开」 | 去读那个字段/类型的源码，读完 `--dry-run` 小规模验一次 |
| 清单里没有 | 按 atk-surface.md 的命令重查一次；确实没有就是 ATK 不支持，如实报告，不要绕开 |

这四类永远不用翻：

| 想知道 | 用 |
| --- | --- |
| 约束器钩子的签名 | [plugin-authoring.md](references/plugin-authoring.md)，签名是从源码核过的 |
| 精度阈值是多少 | 填 `acc: default`，阈值 ATK 自己读 |
| 一份能跑的 YAML 长什么样 | `cp <skill>/assets/skeleton.yaml <op>.yaml` |
| YAML 键名写错了会怎样 | 直接报错，`extra='forbid'`。拿不准就写进去跑一次 dry-run |

脚本只认单张量（`inputs[i]` 是 dict）和张量列表（`inputs[i]` 是 list[dict]）两种输入
形态。碰到第三种时脚本退出码非 0 并打出实际结构，此时在 `scripts/case_shape.py` 的
`tensor_items()` 加一个分支——**只改这一个文件**，三个脚本都从它取——重跑后用
`check_coverage.py` 复核三根轴是不是都有多个取值。**改了装好的 skill 目录就要说，并且
要回仓**，`~/.claude/skills/` 下的改动 reload 就丢。

