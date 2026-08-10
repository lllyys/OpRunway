# OpRunway 真机环境入口

> 本文跟踪可共享、可复核的环境能力与使用纪律。「远程连」形态下的实际 SSH alias、容器名和远端路径放在仓根 `.oprunway/real-machine.env`；该文件已被 `.gitignore` 忽略。脱敏字段模板见 `.oprunway/real-machine.env.example`。**「就地跑」形态不需要这份文件**（见 §1）。

## 1 · 两种执行形态：先认清自己在哪一种

| 形态 | 什么时候是它 | 要 `.oprunway/real-machine.env` 吗 |
|---|---|---|
| **远程连** | 会话在开发机上，得 SSH 到目标机/容器才够得着 NPU | **要**——SSH alias、容器名、远端工作根都在里面 |
| **就地跑** | 会话本身已在目标机（或其 NPU 容器）里，`npu-smi info` 在本机就能跑 | **不要** |

⚠ 这份文件是**「远程连」的连接元数据，不是跑验收的通用前置**。就地跑时它不存在完全正常：
**不得**以「缺 `.oprunway/real-machine.env` / 没有 SSH alias、容器名、远端工作目录」为由拒绝启动验收
（见根 `AGENTS.md` 的 compute 与目标环境规则）。保护根纪律与形态无关，两种形态都按本文 §6 执行。

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

它们不是 plugin 的验收输入。算子、源码目录、SoC、device、阈值和 session 必须每轮由任务书、源码事实与
spec 给出，不能固化在机器 profile 中。

### 1.2 就地跑：不读它，直接调用唯一入口

没有「怎么连过去」这一层——目标机就是本机。按 §4 完成只读探测并加载官方 CANN 环境后，确认公开
`atk` 命令可用，再调用 `plugin/oprunway_cli.py accept`。输入只有 spec、taskdoc、只读源码、ATK design、
目标 SoC 与一个不存在的新 ASCII session；复杂 ABI 才增加 generator/execution plugin。环境中可以使用
系统 Python、容器 Python 或任意已准备的隔离环境，plugin 不要求 venv。

## 2 · A2/A3 环境：最近一次验证状态

最近验证日期：2026-08-10。

| 项 | 已验证事实 |
|---|---|
| 目标硬件 | A2/A3 系任务使用；当前 SoC 配置为 `ascend910_93` |
| 执行形态 | SSH 进入目标机后，在专用容器内执行 build、pytest、用例生成和验收 |
| Python | 3.12.13 |
| ATK | 26.5.14；公开 CLI、四 witness casegen、accuracy/performance 路径已验证 |
| numpy | ATK 前置环境为 1.26.4；新 session 仍须探测 |
| torch | 2.10.0+cpu |
| torch_npu | 2.10.0 |
| pytest | 9.1.1 |
| jsonschema | 4.26.0 |
| 性能采集 | `msprof CLI + libms_tools_ext.so ctypes MSTX + task_time CSV` 已真机产出 kernel-only 数据 |
| 本轮 workflow 见证 | Remainder 开发 session 完整执行 203.099 秒，确定性终态 `PLUGIN_ERROR`；不作为正式算子结论 |

注意：

- 版本是“最近一次验证快照”，不是永久保证。新 session 开始真机工作前必须重新探测。
- 远端 runtime env 和 CANN setenv 的实际路径从忽略文件读取，不写入跟踪文档。
- 共享机上只使用用户态工作根和用户态 vendor 目录，不写共享 CANN 的 `opp/vendors`。
- profiler 产物体积较大；默认解析后清理，仅诊断需要时才显式保留。

## 3 · 950 环境：最近一次验证状态

最近完整验证日期：**2026-08-10**；开始新任务前仍须重新探测。

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
| ATK | 26.5.14；公开 CLI 与四 witness casegen 已验证，安装形态不是 plugin 契约 |
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

本机**无直连外网**，必须经反向隧道（本地代理端口 → 远端回环端口）。
⚠ 仓根 `CLAUDE.md` 只有 `@AGENTS.md` 路由、**不含** `autossh` 写法；实际参数从
`.oprunway/real-machine.env` 读取，隧道命令模板见工作区上层的 `CLAUDE.md`。容器以 `--network host` 启动，故容器内直接用
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

实测结论：在**本轮已测的** fp32 GaussianBlur 用例与 border 配置上，cv2 **4.11.0 与 4.14.0 输出逐位相同**。
该结果只覆盖已测矩阵，**不能据此排除**其它 4.x 版本、dtype、shape、kernel、sigma 或 border 配置上的差异。

已核实的 cv2 行为坑：**`cv2.GaussianBlur` 对 `[H,W,1]` 单通道输入会把最后一维 squeeze 掉**
（`(256,128,1) -> (256,128)`），而 NPU 侧输出与输入严格同 shape。golden 必须显式补回该维，
否则精度比对会因 shape 不匹配整条失败，且**只在 C=1 时触发**。

另有一条 950 侧的 dtype 事实：`torch_npu` 会给出
`Device do not support double dtype now, dtype cast replace with float` 警告——
**float64 在该硬件上被降为 float32**（`torch_npu` 会打印上述警告，不是无声发生；
但计算结果确实按 fp32 走，凡用 torch 侧构造 fp64 中间量的做法都要挂账）。

---

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
5. `atk --version` 与 spec 一致，taskdoc/source 内容摘要、目标子树和本轮 spec 对应。
6. `.oprunway/real-machine.env` 存在时，展开 `OPRUNWAY_MACHINE_PROTECTED_ROOTS`，确认本轮新工作根不等于其中任一根、
   也不位于其子目录；文件或该变量缺席只表示**当前未登记保护根**，不构成阻塞，也不等于授权清理任何目录。

## 5 · 目标环境内的正式调用

外层机器 profile（如果有）只负责找到执行环境。进入目标环境并加载 CANN 后，使用唯一入口：

```bash
python3 "$OPRUNWAY_PLUGIN_ROOT/oprunway_cli.py" accept \
  --spec "$SPEC" --taskdoc "$TASKDOC" --source-root "$SOURCE" \
  --design "$ATK_DESIGN" --target-soc "$SOC" --session-dir "$NEW_SESSION"
```

默认从 `PATH` 解析 `atk`；多版本并存时才传 `--atk-bin`。`NEW_SESSION` 必须不存在，fresh build、caseset、
执行证据和终态不得与旧 session 复用。机器 profile 中的历史 op/vendor 变量不是验收输入，也不能覆盖 spec。

## 6 · 副作用与安全边界

- build、pytest、用例生成、验收和 profiler compute 全在 NPU 目标环境执行；没有 NPU 的开发机上只编辑、维护 Git 与知识记录。
- `OPRUNWAY_MACHINE_PROTECTED_ROOTS` 中每个目录及其子目录均为只读保留现场；新 session 不得在其中
  生成文件、覆盖、移动、删除或复用为工作目录。需要调查时默认只读，任何变更须由用户针对具体目录重新授权。
  该变量**未登记（文件不存在或没设它）不构成阻塞**，但也**不等于**任何目录可以随意写入或清理——
  未登记只是「本机没有登记过保留现场」，删除/覆盖照旧逐次征得用户确认。
- clone、checkout、build、真机跑测、删除/覆盖目标机目录前仍须用户确认；本文件不构成长期授权。
- `.oprunway/real-machine.env` 可以保存实际 alias/path，但不得保存凭据；它只服务「远程连」形态，
  就地跑时不需要它存在。
- 需要新增机器时，先扩展 `.oprunway/real-machine.env.example` 的字段，再在本地忽略文件填实际值；不要把私有默认值写进 Python、shell、spec 或 tracked 文档。
