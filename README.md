# OpRunway

**NPU 算子「验收（acceptance）」workspace。** 把下面这条三段式验收流水线，做成可复用的 workflow / agents / skills：

```
任务书 + PR ──①用例生成(ST)──▶ 测试用例集 ──②NPU跑测──▶ NPU 精度对比 + 性能数据
                                  │                                   │
                                  └──③ 同一份用例喂 GPU 标杆 ──────────┘
                                          ───▶ NPU↔GPU 性能对比报告
```

- **Task 1 · 用例生成(ST)**：PR + 算子任务书 → 覆盖功能/精度/性能的机读+人读用例集（整条流水线的脊柱）。
- **Task 2 · NPU 跑测**：用同一套用例，在算子工程上跑出 NPU 精度对比 + 性能数据。
- **Task 3 · 性能对比**：同一套用例喂 GPU 标杆 → NPU↔GPU 性能对比报告。

## 现状

**主干施工完毕 + 真机端到端验证通过**，但还不是「能对任意算子一键验收」的成品——见 [`doc/oprunway-todo.md`](doc/oprunway-todo.md)。

- **架构**：三层可移植设计。Layer 0 六份 JSON 契约 · Layer 1 确定性脚本（工具中立的「脑子」）· Layer 2 per-tool 薄壳（编排）。Stage 间只传 JSON。
- **已真机验证**：三个结构互不相同的算子（IsClose 二元/bool、Sign 一元/数值、Equal 二元/bool）在真昇腾 NPU 上跑通，**裁决全部正确**——精度 = 真 NPU 输出 vs numpy golden，性能 = msprof 真 kernel-only vs 真内置 TBE 基线，总体门同时卡精度+性能。
- **加一个算子** = `spec` + `gen_cases` 的 golden + `new_example/oprunway_<op>_runner.cpp` 三个文件。

## 快速开始

workflow 入口在 [`plugin/acc-common/run_workflow.py`](plugin/acc-common/run_workflow.py)，两种模式：

```bash
cd plugin/acc-common

# ① mock —— 本地、即时（numpy golden 当 NPU 输出），验证流水线本身，无需 NPU
python3 run_workflow.py specs/isclose.spec.json --out /tmp/run

# ② new_example —— 真 NPU（精度 + msprof 真性能 + 真 TBE 基线）
python3 run_workflow.py specs/sign.spec.json --mode new_example --out /tmp/run
```

- **换算子**：换 `specs/<op>.spec.json` 即可（现有 `isclose` / `sign` / `equal`）。
- **new_example 前提**：可达的昇腾 NPU 机器 + 目标算子仓。连接/路径/SOC/vendor **全部零硬编码**——脚本里的默认值（如 `/home/lys/...`）只是占位，**真实的机器名、路径等经 `OPRUNWAY_*` 环境变量传入、不写进仓**（避免把私有主机/路径固化进代码）：

  ```bash
  OPRUNWAY_SSH_HOST=<机器别名> \
  OPRUNWAY_REMOTE_DIR=<远端工作目录> \
  OPRUNWAY_OPS_REPO=<远端算子仓路径> \
  OPRUNWAY_OPP=<远端用户态 opp 路径> \
  OPRUNWAY_SOC=<soc 名，如 ascend910_93> \
  python3 run_workflow.py specs/sign.spec.json --mode new_example --out /tmp/run
  ```

  完整可覆盖项见 `repo_adapter._ne_cfg`。
- **平台/精度/性能口径从算子任务书推**，不要猜（见 TODO 里「硬约束」）。

## 目录

```
plugin/     workflow 实现（acc-common: 三层脚本 + specs + new_example runner；commands/skills/bridge）
doc/        设计与流水线（oprunway-design.md）、改动简表、TODO
canon/      bureau 决策/ADR（durable 知识，capture→compile→review 三态）
spec/       算子 spec 笔记
repos/      被测/参考算子仓（外部克隆，.gitignore 不入库）
```

## 约定（继承 cann-ops-test）

零硬编码（仓名/路径/SOC/阈值运行时探测或询问）· 零持久化配置（产物落 CWD 下 `reports/`）· 全程中文 · 副作用先确认 · 跑测多层判定 · 不凭空捏造（推断项显式标注）。

> 详细设计见 `doc/oprunway-design.md`；改动流水见 `doc/oprunway-changes-brief.md`；待办见 `doc/oprunway-todo.md`。
