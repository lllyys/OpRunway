# OpRunway 真机环境入口

> 本文跟踪可共享、可复核的环境能力与使用纪律。「远程连」形态下的实际 SSH alias、容器名和远端路径放在仓根 `.oprunway/real-machine.env`；该文件已被 `.gitignore` 忽略。脱敏字段模板见 `.oprunway/real-machine.env.example`。**「就地跑」形态不需要这份文件**（见 §1）。

## 1 · 两种执行形态：先认清自己在哪一种

| 形态 | 什么时候是它 | 要 `.oprunway/real-machine.env` 吗 |
|---|---|---|
| **远程连** | 会话在开发机上，得 SSH 到目标机/容器才够得着 NPU | **要**——SSH alias、容器名、远端工作根都在里面 |
| **就地跑** | 会话本身已在目标机（或其 NPU 容器）里，`npu-smi info` 在本机就能跑 | **不要** |

⚠ 这份文件是**「远程连」的连接元数据，不是跑验收的通用前置**。就地跑时它不存在完全正常：
**不得**以「缺 `.oprunway/real-machine.env` / 没有 SSH alias、容器名、远端工作目录」为由拒绝启动验收
（AGENTS.md §5.3）。保护根纪律与形态无关，两种形态都按 §6 执行。

### 1.1 远程连：先读本地机器配置

在仓根执行：

```bash
set -a
source .oprunway/real-machine.env
set +a
```

这份文件只保存编排元数据，不保存 token、密码、私钥或内网 IP。不要把它复制进报告、commit、PR 或 issue。

变量分两类：

- `OPRUNWAY_MACHINE_*`：当前 A2/A3 真机的 SSH、容器与工作目录元数据，供外层编排使用。
- `OPRUNWAY_A5_*`：950 真机的入口元数据。
- `OPRUNWAY_MACHINE_PROTECTED_ROOTS`：逗号分隔的远端只读保护根。真实值只在 ignored env 中保存。

它们不是 `run_workflow.py` 的完整运行配置。算子相关的 PR head、op 子目录、被测仓、vendor 名等必须每次从任务书、`pr_facts.json` 和 spec 重新派生，不能固化在机器 profile 中。

### 1.2 就地跑：不读它，直接给流水线变量

没有「怎么连过去」这一层——目标机就是本机。编排层只需两步：

1. **确认自己确实在目标环境里**：按 §4 做只读探测，命令去掉 `ssh` / `docker exec` 前缀，本机直接跑。
2. **直接设 §5 那组本轮流水线变量**，其中与形态相关的只有这几项：
   - `OPRUNWAY_TARGET=local` —— 传输层走本机 `bash` / `cp`，**不碰 ssh/scp**；
   - `OPRUNWAY_SSH_HOST` **不需要**——它只在 `OPRUNWAY_TARGET=remote` 时必填
     （`repo_adapter._ne_cfg` / `aclnn_adapter._aclnn_cfg`，`local` 模式忽略此项）；
   - `OPRUNWAY_REMOTE_DIR` **仍要给**——它是「工作根目录」，就地跑时就是本机上的那个目录，
     名字里的 `REMOTE` 是历史遗留，不代表必须远端；
   - `cpp_extension` 的 `OPRUNWAY_CPP_EXTENSION_DRIVER_JSON` 照给，只是 argv 里不带 `ssh` / `docker exec`
     前缀（`cpp_extension_adapter` 本身不内置 SSH/容器名，只执行编排层给的这串 argv）。

   其余变量（PR head、op 子目录、被测仓、vendor 名、SoC、setenv…）**与形态无关**，两种形态一样都得每轮从
   任务书 / `pr_facts.json` / spec 重新派生，不得因为「机器 profile 里有」就复用旧值。

## 2 · A2/A3 环境：最近一次验证状态

最近验证日期：2026-07-26。

| 项 | 已验证事实 |
|---|---|
| 目标硬件 | A2/A3 系任务使用；当前 SoC 配置为 `ascend910_93` |
| 执行形态 | SSH 进入目标机后，在专用容器内执行 build、pytest、用例生成和验收 |
| Python | 3.12.13 |
| numpy | 2.5.1 |
| torch | 2.10.0+cpu |
| torch_npu | 2.10.0 |
| pytest | 9.1.1 |
| jsonschema | 4.26.0 |
| 性能采集 | `msprof CLI + libms_tools_ext.so ctypes MSTX + task_time CSV` 已真机产出 kernel-only 数据 |
| Median 最新结果 | `cpp_extension` torch-parity 精度 1152 例：1101 PASS、51 FAIL；`gate.passed=true`，确定性裁决 `FAIL(精度)`；上一轮 1344-case 结果仅作历史记录 |

注意：

- 版本是“最近一次验证快照”，不是永久保证。新 session 开始真机工作前必须重新探测。
- 远端 runtime env 和 CANN setenv 的实际路径从忽略文件读取，不写入跟踪文档。
- 共享机上只使用用户态工作根和用户态 vendor 目录，不写共享 CANN 的 `opp/vendors`。
- profiler 产物体积较大；默认解析后清理，仅诊断需要时才显式保留。

## 3 · 950 环境：最近一次验证状态

最近完整验证日期：2026-07-02；开始新任务前必须重新探测。

| 项 | 已验证事实 |
|---|---|
| 目标硬件 | 2× Ascend950PR，catlass arch 3510 |
| CANN | 9.0.0 |
| Python | 系统 Python 3.11 |
| 执行形态 | host 用户态编译/运行；当时无 Docker 权限 |
| 已验证样例 | catlass 950 basic matmul build/run，结果 Compare success |

A2/A3 与 950 没有主备关系。目标机必须由任务书“适配硬件”与 op_def `AddConfig` 双源核定；不一致时写入 `task_pr_gaps` 并停止猜测。

## 4 · 每次真机工作前的只读探测

以下命令按**远程连**形态写，只展示探测项；实际 host/container 从忽略文件取。
**就地跑**形态把 `ssh …` / `docker exec …` 外壳去掉、在本机直接执行同样的探测项即可，探测项与核对清单完全一致：

```bash
ssh "$OPRUNWAY_MACHINE_SSH_HOST" \
  "docker exec $OPRUNWAY_MACHINE_CONTAINER bash -lc \
  'python3 --version; npu-smi info; msprof --version'"
```

Python 包版本在容器内探测：

```bash
ssh "$OPRUNWAY_MACHINE_SSH_HOST" \
  "docker exec $OPRUNWAY_MACHINE_CONTAINER python3 -c \
  'import numpy, torch, torch_npu; print(numpy.__version__, torch.__version__, torch_npu.__version__)'"
```

探测后至少核对：

1. 任务书硬件与当前机器匹配；
2. SoC、CANN、Python、torch/torch_npu 与上次快照是否漂移；
3. runtime env、setenv、用户态 vendor 目录存在且不可被同组/其他用户写；
4. NPU 当前是否空闲；
5. PR head、op 子目录和 DUT build provenance 与本轮 `pr_facts.json` 一致。
6. `.oprunway/real-machine.env` 存在时，展开 `OPRUNWAY_MACHINE_PROTECTED_ROOTS`，确认本轮新工作根不等于其中任一根、
   也不位于其子目录；文件或该变量缺席只表示**当前未登记保护根**，不构成阻塞，也不等于授权清理任何目录。

## 5 · 目标环境内的流水线变量

外层机器 profile（如果有）只负责找到执行环境。进到目标环境（远程连是进容器，就地跑就是当前会话）后，
再加载本轮真实变量；至少包括：

- `OPRUNWAY_TARGET=local`
- `OPRUNWAY_REMOTE_DIR`
- `OPRUNWAY_ACLNN_OPS_DIR`
- `OPRUNWAY_ACLNN_OP_SUBDIR`
- `OPRUNWAY_ACLNN_PR_REF`
- `OPRUNWAY_ACLNN_PR_HEAD_SHA`
- `OPRUNWAY_ACLNN_BASE_REPO`
- `OPRUNWAY_ACLNN_VENDOR_DIR`
- `OPRUNWAY_ACLNN_VENDOR_NAME`
- `OPRUNWAY_ACLNN_SOC`
- `OPRUNWAY_NPU_DEVICE`
- `OPRUNWAY_SETENV`
- `OPRUNWAY_ACLNN_REAL=1`

其中 PR、op、repo 与 vendor 字段属于“本轮任务配置”，不得因为机器 profile 已存在就复用旧值。`OPRUNWAY_ACLNN_REUSE_BUILD=1` 也只允许在 provenance stamp 全部匹配时复用。

## 6 · 副作用与安全边界

- build、pytest、用例生成、验收和 profiler compute 全在 NPU 目标环境执行；没有 NPU 的开发机上只编辑、维护 Git 与知识记录。
- `OPRUNWAY_MACHINE_PROTECTED_ROOTS` 中每个目录及其子目录均为只读保留现场；新 session 不得在其中
  生成文件、覆盖、移动、删除或复用为工作目录。需要调查时默认只读，任何变更须由用户针对具体目录重新授权。
  该变量**未登记（文件不存在或没设它）不构成阻塞**，但也**不等于**任何目录可以随意写入或清理——
  未登记只是「本机没有登记过保留现场」，删除/覆盖照旧逐次征得用户确认。
- clone、checkout、build、真机跑测、删除/覆盖目标机目录前仍须用户确认；本文件不构成长期授权。
- `.oprunway/real-machine.env` 可以保存实际 alias/path，但不得保存凭据；它只服务「远程连」形态，
  就地跑时不需要它存在。
- 需要新增机器时，先扩展 `.env.example` 的字段，再在本地忽略文件填实际值；不要把私有默认值写进 Python、shell、spec 或 tracked 文档。
