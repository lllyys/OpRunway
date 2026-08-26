---
name: repo-task-case-gen
description: >-
  按社区算子任务书生成 ATK 测试用例并冻结 golden，产出交给跑测侧的用例包。
  当用户给出算子任务书、要为算子设计测试用例、要生成 ATK YAML 或用例 JSON 时使用；
  已经有用例包要在 NPU 上编译跑测时改用 repo-task-atk-accept。
---

# ATK 用例生成

任务书 → 算子事实表 → ATK YAML → 用例 JSON → golden → 用例包。

## 入口参数

| 参数 | 含义 | 取值约束 | 初值推断 |
| --- | --- | --- | --- |
| `任务书` | 社区算子任务书 | 任意 markdown 结构，不要求编号章节 | 用户消息给出 |
| `op` | 算子名 | aclnn 接口去掉 `aclnn` 前缀，如 `Roll` | 从任务书标题或接口名解析 |
| `工程目录` | 算子工程根 | 含 `docs/aclnn*.md` 的目录 | 用户给出；没有就走推断，见 interface-facts.md |
| `工作目录` | 所有相对路径的基准 | `atk-case-<op>/`，建在工作区下，不建在工程里 | 进 S1 前 `mkdir -p` 建立 |
| `<python>` | 跑量具的解释器 | 能 `import atk` | `probe_env.py` 写进 `env.json` 的 `python` |

**每条命令自己带 `cd <工作目录> &&`。** shell 每次调用都从会话启动目录重新开始，上一条的 `cd`
不留到下一条。少写这一句，`-o env.json` 就落到启动目录，下一条命令报文件不存在。

## 前置检查

```bash
mkdir -p atk-case-<op> && cd atk-case-<op> && <python> <skill>/scripts/probe_env.py -o env.json
```

| 退出码 | 含义 | 去向 |
| --- | --- | --- |
| 0 | atk 与 torch 可用 | 进 S1 |
| 2 | atk 或 torch 缺失 | 停止，输出 `阻塞·未生成 @S0`，把 `env.json` 的 `missing` 列给用户 |

生成侧只要 CPU 版 torch 与 atk，**不需要 NPU 和 CANN**。

## 主流程

| 阶段 | 做什么 | 产物 | 出口判据 |
| --- | --- | --- | --- |
| S1 事实表 | 读任务书与工程 `docs/aclnn*.md`，填 `facts.json` | `facts.json` | `check_facts.py` 退出码 0 |
| S2 用例设计 | 写 `<op>.yaml`，需要时写 `<op>_constraint.py` / `function_<op>.py` | yaml + 插件 | `gen_cases.py --dry-run` 退出码 0 |
| S3 生成用例 | 正式跑 `atk case` | `cases.json` | 用例数 ≥ 100 |
| S4 冻结 golden | 跑 CPU 标杆存盘 | `golden/` | `freeze_golden.py` 退出码 0 |

### S1 事实表

读 [interface-facts.md](references/interface-facts.md)，按三跳来源规则填 `facts.json`。

三跳：**任务书任意位置 → 工程 `docs/aclnn<Op>.md` → 基线接口泛化推断**。每个字段都要带
`source`，取值只能是 `taskdoc` / `opdoc` / `inferred`。

```bash
cd <工作目录> && <python> <skill>/scripts/check_facts.py facts.json
```

退出码 2 是字段缺失或 dtype 不在 ATK 词表里，照它打印的字段名补；退出码 3 是 JSON 语法错。

**约束只从公开接口面取**：工程的 `docs/aclnn*.md`、`README.md`、`op_api/*.h` 的函数声明、
任务书。**不读 `op_kernel/`、`op_host/*_tiling.cpp` 与内部断言**——用被测算子自己的断言生成用例，
写错的断言永远测不出来。

### S2 用例设计

先读 [yaml-authoring.md](references/yaml-authoring.md) 与 [case-strategy.md](references/case-strategy.md)，
再写 `<op>.yaml`。精度标准取值只从 [precision-standard.md](references/precision-standard.md) 拿，
不凭记忆写 rtol/atol。

要不要写 Python 插件，按 [plugin-authoring.md](references/plugin-authoring.md) 的判据表决定。
判据只有三条，都不命中就不写，`generate` 留 `default`。

```bash
cd <工作目录> && <python> <skill>/scripts/gen_cases.py --dry-run
```

dry-run 用 `dtype_numbers=1` 跑，只验证 YAML 与插件能组合出合法用例，60 秒超时。

### S3 生成用例

dry-run 过了再回填 `dtype_numbers`（算法见 case-strategy.md「用例规模」），然后：

```bash
cd <工作目录> && <python> <skill>/scripts/gen_cases.py
```

它跑 `atk case`，把结果收敛到 `cases.json`，打印用例数与 dtype 分布。

**用例数少于 100 就是 YAML 写窄了**，回 S2 加 `dim_values` 或 dtype，不要直接调大
`dtype_numbers` 硬凑。

**条数对不代表形态分布对。** 写了约束器就抽查一遍秩分布与轴的正负分布：

```bash
cd <工作目录> && <python> -c "
import json, collections
cases = json.load(open('cases.json'))
print('秩分布', collections.Counter(len(c['inputs'][0]['shape'] or []) for c in cases))
"
```

某一项只有一个取值就是约束器写死了——真机上撞过一次，200 条用例全落在同一种形态里，
见 [plugin-authoring.md](references/plugin-authoring.md)「case_config.id 在约束器里恒为 0」。

### S4 冻结 golden

```bash
cd <工作目录> && <python> <skill>/scripts/freeze_golden.py
```

它跑 `atk node --backend cpu task -c cases.json --task accuracy --save_data output`
——**只起 cpu 一个节点**，生成侧要的是标杆输出不是比对结论，所以不需要 NPU。
输出收进 `golden/`，成功条数写进 `golden/manifest.json`。

| 情形 | 去向 |
| --- | --- |
| 全部成功 | S4 通过，用例包完成 |
| 部分失败 | 失败的是**基线跑不出来**的用例，回 S2 修 YAML 或执行器，不要把它们剔掉了事 |
| 全部失败 | `name` 字段的 torch 接口名写错了，或执行器没注册上 |

CPU 标杆跑不出来的用例在 NPU 上也无法比对，留着只会变成假失败。

## 用例包

S4 通过后 `atk-case-<op>/` 就是交付物，直接交给 `repo-task-atk-accept`：

```text
atk-case-<op>/
├── facts.json           算子事实表，每项带 source
├── env.json             生成侧环境指纹
├── <op>.yaml            用例设计
├── <op>_constraint.py   有才放
├── function_<op>.py     有才放
├── cases.json           用例
└── golden/              CPU 标杆输出 + manifest.json
```

不做 SHA256 封印，也不做只读锁。用例包的可靠性由 S1–S4 的出口判据保证。

## 停止条件

以下情形停止并输出 `阻塞·未生成 @S<n>`，写明失败阶段、失败判据和解除阻塞需要什么：

- S0 `probe_env.py` 退出码非 0
- S1 任务书与工程文档都拿不到接口原型，且基线接口也推断不出参数顺序
- S2 dry-run 连续两次失败且第二次的报错与第一次不同类
- S4 golden 全部失败

**不要为了让命令通过而改小覆盖面、剔掉失败用例或跳过判据。**

## 不要去翻的地方

这四类探索在真机上验证过是白费的，直接用右边那列：

| 想知道 | 不要 | 用 |
| --- | --- | --- |
| YAML 某个键能不能填、怎么算 | grep ATK 源码 | [yaml-authoring.md](references/yaml-authoring.md)，缺了再看 ATK 的《用例设计文件说明》 |
| 约束器钩子的签名 | 猜或抄 ATK 自带 skill | [plugin-authoring.md](references/plugin-authoring.md)，那里的签名是从源码核过的 |
| 精度阈值是多少 | 凭记忆写进 YAML | 填 `acc: default`，阈值 ATK 自己读 |
| 一份能跑的 YAML 长什么样 | 从零推 | 抄 `assets/example/Roll.yaml` |

`atk case` 报错时**先看报错本身**，它通常直接点出是哪个键。
读源码是兜底，不是第一步。

## 参考资料

- [interface-facts.md](references/interface-facts.md) — 事实表字段与三跳来源规则
- [yaml-authoring.md](references/yaml-authoring.md) — ATK YAML 字段与写法
- [case-strategy.md](references/case-strategy.md) — 覆盖策略与用例规模
- [precision-standard.md](references/precision-standard.md) — 精度标准取值
- [plugin-authoring.md](references/plugin-authoring.md) — 约束器与执行器判据和模板
- `assets/example/` — Roll / IndexFillTensor / Median 三个算子的真机产物，可照抄
