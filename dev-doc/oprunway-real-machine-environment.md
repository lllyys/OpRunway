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
- `OPRUNWAY_MACHINE_PROTECTED_ROOTS`：逗号分隔的远端只读保护根。真实值只在 ignored env 中保存。

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
| Median 最新结果 | `cpp_extension` torch-parity 精度 1152 例：1101 PASS、51 FAIL；`gate.passed=true`，确定性裁决 `FAIL(精度)`；上一轮 1344-case 结果仅作历史记录 |

注意：

- 版本是“最近一次验证快照”，不是永久保证。新 session 开始真机工作前必须重新探测。
- 远端 runtime env 和 CANN setenv 的实际路径从忽略文件读取，不写入跟踪文档。
- 共享机上只使用用户态工作根和用户态 vendor 目录，不写共享 CANN 的 `opp/vendors`。
- profiler 产物体积较大；默认解析后清理，仅诊断需要时才显式保留。

## 3 · 950 环境：最近一次验证状态

最近完整验证日期：**2026-08-03**（本轮重探，取代 2026-07-02 快照）；开始新任务前仍须重新探测。

| 项 | 已验证事实 |
|---|---|
| 目标硬件 | **8× Ascend950PR**，本轮探测时全部 idle、无进程占用 |
| 宿主 OS | openEuler 24.03 (LTS-SP3)，**x86_64** |
| 执行形态 | **容器**（2026-07-02 记录的“host 用户态、无 Docker 权限”已失效） |
| Docker | 18.09.0，storage driver overlay2；当前账号在 `docker` 组内，**无免密 sudo** |
| 容器镜像 | `ascendhub/cann:9.0.0-950-ubuntu22.04-py3.11`（探测时已在本地，无需拉取） |
| 容器 OS / Python | Ubuntu 22.04.5 / Python 3.11.15 |
| CANN | 9.0.0（`V100R001C10SPC001B250`，arch x86_64） |
| 驱动 / npu-smi | npu-smi 25.7.rc1 |
| 编译工具链 | gcc/g++ 11.4.0、cmake 3.22.1、make 4.3、msprof 可用；**ninja 缺失** |
| Python 包 | **numpy 1.26.4** · scipy 1.17.1 · torch 2.10.0+cpu · torch_npu 2.10.0 · **cv2 4.11.0** · pytest 9.1.1 · protobuf 3.20.0 |
| NPU 可用性硬证据 | 容器内 `acl.init() -> 0`、`acl.rt.set_device(7) -> 0`；`torch.randn(3,4).npu()` 实算返回 `device='npu:0'` |

### 3.1 建容器时的两个已知坑

1. **`/dev/devmm_svm` 在本机不存在**，照抄别的容器的设备清单会导致
   `error gathering device information ... no such file or directory`。实际存在的只有
   `davinci0..7`、`davinci_manager`、`hisi_hdc`。
2. **不加 `--privileged` 时容器内 `npu-smi` 报 `dcmi model initialized failed ... ret is -8020`**
   （伴随 `DrvMngGetConsoleLogLevel failed. (ret=4)`）。补 `--privileged` 并挂
   `/usr/local/Ascend/driver/tools` 后恢复正常。

### 3.2 磁盘：只有一处能放大件

| 分区 | 容量 | 探测时剩余 | 结论 |
|---|---|---|---|
| `/home` | 10 G | **396 K** | 已满，不可用 |
| `/`（`/mnt/<user>` 落在这） | 70 G | 6.7 G | 太紧，撑不住算子 build |
| Docker 数据卷 | 1.7 T | **1.3 T** | **唯一可放大件处**；工作区建在其下并挂进容器 |
| `/tmp` | tmpfs 378 G | 376 G | 够大但 RAM 支撑、重启即失；宿主内存 754 G |

工作区实际路径只记在 ignored env 的 `OPRUNWAY_A5_WORKDIR`。

### 3.3 网络

本机**无直连外网**，必须经反向隧道（本地代理端口 → 远端回环端口，见
仓根 `CLAUDE.md` 的 `autossh` 写法）。容器以 `--network host` 启动，故容器内直接用
远端回环地址即可，不必走 `docker0` 网关。

隧道会静默失效：端口仍在监听、但转发不到任何地方，表现为 `curl` 超时返回 `000`。
判据是**端到端实测**（如在容器内真的 `pip download` 一个包），不能只看端口是否 LISTEN。

### 3.4 numpy 与 OpenCV 的版本耦合（golden 侧）

三条互相咬合的约束，装包顺序错了会来回返工：

1. **被测仓可能要求 `numpy<2.0`**（ops-cv 的 `requirements.txt` 即如此）。CANN 镜像自带的是
   numpy 2.x，需显式降级。
2. **`pip install opencv-python-headless` 默认给 5.0.x**。当任务书以 OpenCV 4.x 的
   `modules/imgproc` 为对标参考时，装 5.0 属于无理由偏离验收基准，应显式约束到 `<5`。
3. **较新的 cv2 4.x 轮子声明 `numpy>=2`**（实测 4.14.0.94 即是）。因此不能先装 cv2 再降 numpy，
   要把两者放进同一条 `pip install` 让解析器回溯——在 `numpy==1.26.4` 下会落到 **cv2 4.11.0**。

实测结论：cv2 **4.11.0 与 4.14.0 对 fp32 GaussianBlur 的输出逐位相同**，border 常量也一致，
所以 4.x 内部的小版本差异对本类算子的真值不构成风险。

已核实的 cv2 行为坑：**`cv2.GaussianBlur` 对 `[H,W,1]` 单通道输入会把最后一维 squeeze 掉**
（`(256,128,1) -> (256,128)`），而 NPU 侧输出与输入严格同 shape。golden 必须显式补回该维，
否则精度比对会因 shape 不匹配整条失败，且**只在 C=1 时触发**。

另有一条 950 侧的 dtype 事实：`torch_npu` 会给出
`Device do not support double dtype now, dtype cast replace with float` 警告——
**float64 在该硬件上被静默降为 float32**。凡是用 torch 侧构造 fp64 中间量的做法都要挂账。

---

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
6. 展开 `OPRUNWAY_MACHINE_PROTECTED_ROOTS`，确认本轮新工作根不等于其中任一根、也不位于其子目录。

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
- `OPRUNWAY_MACHINE_PROTECTED_ROOTS` 中每个目录及其子目录均为只读保留现场；新 session 不得在其中
  生成文件、覆盖、移动、删除或复用为工作目录。需要调查时默认只读，任何变更须由用户针对具体目录重新授权。
- clone、checkout、build、真机跑测、删除/覆盖远端目录前仍须用户确认；本文件不构成长期授权。
- `.oprunway/real-machine.env` 可以保存实际 alias/path，但不得保存凭据。
- 需要新增机器时，先扩展 `.env.example` 的字段，再在本地忽略文件填实际值；不要把私有默认值写进 Python、shell、spec 或 tracked 文档。
