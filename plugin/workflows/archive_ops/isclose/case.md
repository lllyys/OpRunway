# archive case · IsClose

| 字段 | 值 |
|---|---|
| `op` | `IsClose` |
| `taskdoc` | **from-scratch example（无社区任务）** —— pipeline 首建于此算子 |
| `pr` | **无（demo 算子）** —— 无对应社区任务 PR |
| `verdict` | **`pass`**（精度 + 性能皆过） |
| `evidence_path` | `doc/oprunway-acceptance-evidence.md`（已验证案例台账） |
| `real_machine` | `a3`（真 Ascend910/A3 NPU 端到端） |
| `caveat` | ⚠ **无社区任务 PR**——provenance 是 from-scratch example、非「验收了某社区交付」；作 pipeline 冒烟/回归锚点用，不代表验收了一个真实社区任务 |

## 裁决摘要

- **精度**：真 NPU out vs numpy golden，`verify_mode=exact`（bool 输出逐位精确）→ 过。
- **性能**：msprof kernel-only vs 真内置 `aclnnIsClose` TBE 基线 → 达标。
- **门**：三级完整性门（防跑子集/防放宽/防混 e2e）通过。

## 关联制品（symlink，相对拓扑）

- spec：[`isclose.spec.json`](./isclose.spec.json)（**内联真实副本**，源 `samples/specs/isclose.spec.json`；真样例已迁出运行时路径，不再 symlink）
- runner：[`oprunway_isclose_runner.cpp`](./oprunway_isclose_runner.cpp) → `../../../../samples/runners/oprunway_isclose_runner.cpp`

> `ls -l` 应见箭头（→）；`readlink` 可解析。加新算子时对照本卡的三件套（spec + gen_cases 注册 golden + runner）。
