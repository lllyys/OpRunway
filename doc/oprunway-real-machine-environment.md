# OpRunway 真机环境入口

> 本文跟踪可共享、可复核的环境能力与使用纪律。实际 SSH alias、容器名和远端路径放在仓根 `.oprunway/real-machine.env`；该文件已被 `.gitignore` 忽略。脱敏字段模板见 `.oprunway/real-machine.env.example`。

## 1 · 读取本地机器配置

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

它们不是 `run_workflow.py` 的完整运行配置。算子相关的 PR head、op 子目录、被测仓、vendor 名等必须每次从任务书、`pr_facts.json` 和 spec 重新派生，不能固化在机器 profile 中。

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
| Median 最新结果 | 精度 60/60 PASS；性能 custom 50/50、baseline 48/50 有效 |

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

以下命令只展示探测项；实际 host/container 从忽略文件取：

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

## 5 · 进入容器后的流水线变量

外层机器 profile 只负责找到执行环境。进入容器后，再从远端 runtime env 加载本轮真实变量；至少包括：

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

- build、pytest、用例生成、验收和 profiler compute 全在远程 NPU 环境执行，本地只编辑、维护 Git 与知识记录。
- clone、checkout、build、真机跑测、删除/覆盖远端目录前仍须用户确认；本文件不构成长期授权。
- `.oprunway/real-machine.env` 可以保存实际 alias/path，但不得保存凭据。
- 需要新增机器时，先扩展 `.env.example` 的字段，再在本地忽略文件填实际值；不要把私有默认值写进 Python、shell、spec 或 tracked 文档。
