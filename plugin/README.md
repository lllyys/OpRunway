# 社区算子任务 Skill 仓

三个平级的 Claude Code skill，覆盖社区算子任务从任务书到验收结论的链路。

| skill | 输入 | 输出 |
| --- | --- | --- |
| `repo-task-case-gen` | 社区算子任务书（任意结构）+ 算子工程 | 带冻结 golden 的 ATK 用例包 |
| `repo-task-atk-accept` | 用例包 + 算子工程 + 母仓 | 精度与性能验收结论 |
| `repo-task-doc-write` | 需求 | 可验收的算子任务书 |

前两个组成一条完整链路，第三个独立使用，与前两个无耦合。

## 一条链路长什么样

```bash
# 生成侧：任务书 → 用例包（不需要 NPU）
mkdir -p atk-case-Roll && cd atk-case-Roll
python <skill>/repo-task-case-gen/scripts/probe_env.py -o env.json
#   agent 读任务书与工程 docs/aclnn*.md，写 facts.json 与 Roll.yaml
python <skill>/repo-task-case-gen/scripts/check_facts.py facts.json
python <skill>/repo-task-case-gen/scripts/gen_cases.py --dry-run
python <skill>/repo-task-case-gen/scripts/gen_cases.py
python <skill>/repo-task-case-gen/scripts/freeze_golden.py

# 跑测侧：用例包 + 工程 → 结论（需要 NPU）
cp -r atk-case-Roll atk-verify-Roll && cd atk-verify-Roll
python <skill>/repo-task-atk-accept/scripts/probe_env.py --op Roll -o env.json --write-env-sh evidence/env.sh
source evidence/env.sh
python <skill>/repo-task-atk-accept/scripts/build_install.py --op Roll \
    --project <算子工程> --parent-repo <母仓> --soc ascend910_93 -o install.json
source evidence/env.sh
python <skill>/repo-task-atk-accept/scripts/sample_smoke.py -i cases.json -o smoke -n 30
python <skill>/repo-task-atk-accept/scripts/run_atk.py --mode smoke -c smoke/cases.json -o smoke_result.json
python <skill>/repo-task-atk-accept/scripts/run_atk.py --mode accuracy -c cases.json -o accuracy.json
python <skill>/repo-task-atk-accept/scripts/run_atk.py --mode performance -c cases.json -o performance.json
python <skill>/repo-task-atk-accept/scripts/verdict.py -o verdict.json --report report.md
```

实际使用时不必手敲这些——把 `SKILL.md` 交给 agent，它按阶段自己走。

## 设计要点

**任务书不做结构限定。** 社区任务书没有统一模板：有的带参数表，有的只有一句
「支持所有走入 aicore 的数据类型」。事实提取由 agent 做，脚本只校验结果，
所以换个写法不会挂。

事实来源按三跳降级，每项都记来源：

```text
任务书任意位置  →  工程 docs/aclnn<Op>.md  →  基线接口泛化推断
   taskdoc              opdoc                    inferred
```

**不拿算子实现当判据。** 工程的 `docs/`、`README.md`、`op_api/*.h` 声明可读，
`op_kernel/`、`op_host/*_tiling.cpp` 与内部断言不可读。用被测算子自己的断言
生成用例，写错的断言永远测不出来。

**golden 冻结。** 生成侧跑一轮 CPU 标杆存盘，跑测侧用 ATK 原生的
`accuracy_load` 读盘比对。同一套用例反复验收多个 PR 时基准不漂。

**结论从数据推导。** `verdict.py` 从各阶段产物算总结论，报告作者只写证据链。

## 安装

本仓是 Claude Code plugin，`.claude-plugin/plugin.json` 已登记三个 skill。
把仓库目录加进 Claude Code 的 plugin 路径即可。

ATK 是只读 submodule：

```bash
git submodule update --init --recursive
```

## 运行环境

| 侧 | 需要 |
| --- | --- |
| 生成侧 | Python + atk + CPU 版 torch，**不需要 NPU** |
| 跑测侧 | 上述 + torch_npu + CANN 工具链 + 健康 NPU |

## 文档

- 使用者说明：`docs/skills/<skill 名>/`
- 开发规则：各 skill 目录的 `CLAUDE.md`
- 行文规范：`.claude/rules/skill-style.md`
- 架构演进：`docs/development/architecture-log.md`
