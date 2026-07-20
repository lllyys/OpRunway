# archive case · Sign

| 字段 | 值 |
|---|---|
| `op` | `Sign` |
| `taskdoc` | 社区任务【AscendC 实现 Sign】（gitcode `cann/ops-math`） |
| `pr` | **gitcode ops-math!2702**（已 API + `pr_facts.json` 双核为真且 merged） |
| `verdict` | **`precision-pass·perf-fail`**（精度过、**性能未达成**——⚠ 非 PASS） |
| `evidence_path` | `doc/oprunway-acceptance-evidence.md`（已验证案例台账） |
| `real_machine` | `a3`（真 Ascend910/A3 NPU 端到端 `new_example`） |
| `caveat` | 需确认自定义 kernel 构建优化口径与 TBE 对等、取稳态/warmup、多 shape 复测，才能 100% 把慢归到 PR 实现而非构建配置；`target_ratio` 按任务书『无劣化』= 1.0 |

## 裁决摘要（**已实测·性能未达成，明标非 PASS**）

- **精度**：5/5 过（`sign_000` NPU 输出 `[-1,0,1,-1,-1]` = golden；输出 ∈ {-1,0,1} 实为精确）。runner 清白、kernel 真跑。
- **性能**：`sign_004`（1024×1024）真机 kernel **9.68us** vs TBE 基线 **6.32us** → **ratio 0.653**（达标 0/1）。任务书要求「相比原 TBE 性能无劣化」= ratio ≥ 1.0，未达成。
- **关键洞**：**mock 全过、真机才暴露 Sign 慢** → 验收必须上真机；mock 只验流水线自洽。

## 关联制品（symlink，相对拓扑）

- spec：[`sign.spec.json`](./sign.spec.json)（**内联真实副本**，源 `samples/specs/sign.spec.json`；真样例已迁出运行时路径，不再 symlink）
- runner：[`oprunway_sign_runner.cpp`](./oprunway_sign_runner.cpp) → `../../../../samples/runners/oprunway_sign_runner.cpp`

> `ls -l` 应见箭头（→）；`readlink` 可解析。本卡是「精度过但性能 FAIL」的真实样本——存档以证「性能门真能拦、裁决不美化」。
