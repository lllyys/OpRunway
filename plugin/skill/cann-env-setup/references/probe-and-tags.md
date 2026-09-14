# 探测判据与配套 tag 规则

`detect_env.py`（P1）与 `repo_setup.py`（P4）的判据细节。P1 探到 `ready=false`、
或 P4 报 `no_matching_tag_*` 时读这里。

## CANN 探测

| 情况 | 怎么判 | 怎么办 |
| --- | --- | --- |
| `ready=false` | 没探到 `set_env.sh` | `AskUserQuestion` 让用户给 `set_env.sh` 路径 → `--set-env <path>` 重探；仍无则指引装 CANN，止步 |
| 版本解析 | 取 `ASCEND_HOME_PATH` 的 basename，如 `cann-9.0.0-beta.1` | **不取 driver 版本**：`/usr/local/Ascend/version.info` 里那种 `25.x` 是 driver，不是 CANN |
| SOC 探到 | `acl.get_soc_name()` 拿芯片精确名（如 `Ascend910_9382`），映射成 build.sh 短串（`ascend910_93`），存 `cann.soc.build_soc` | P6 直接用，不问不猜 |
| SOC 探不到 | acl 不可用 / 无 NPU 权限 / 无 set_env | 才回退 `AskUserQuestion` 问用户 |

**为什么 SOC 必须探不能猜**：910_93xx 系列曾被误当 `ascend910b`，编出来的包在真机上装不上。
精确芯片名到 build.sh 短串之间不是字面截断，猜不出来。

## 系统构建依赖（P2）

| 字段 | 含义 | 处理 |
| --- | --- | --- |
| `missing_required` 非空 | cmake / gcc / g++ / make / git 任一缺 | 列出缺的 + 按发行版给 `yum/apt/dnf` 安装命令，**让用户去装**（可能要 sudo），装完回 P1 复探 |
| `ccache` 缺 | 可选项 | 只提示「装上构建更快、不阻塞」，给命令，用户可跳过 |

## conda 三条岔路（P3）

`conda` 不可用时 `AskUserQuestion` 三选一：

| 选项 | 做什么 |
| --- | --- |
| A. 装 miniconda | 给官方安装脚本命令，用户确认后执行或自行装 |
| B. 改用 `venv` | 用 P1 探到的某个 python 解释器建 venv |
| C. 别处已有 conda | 用户手动给路径 |

`conda` 可用时问两件事：用**已有 env** 还是**新建**；新建则问 env 名 + python 版本。
**python 版本不写死**——把 P1 探到的可用版本列给用户选，说明「与目标 CANN / 容器一致更稳」。
确认后执行 `conda create -y -n <env> python=<ver>`。

## 算子仓配套 tag（P4）

**教训固化：算子仓 master 是中间态**——算子代码已升到新 opbase 宏、而 opbase pin 还落后，
直接编撞缺符号（表现见 [faq.md](faq.md) 第 6 条）。**必须切到与 CANN 版本配套的 tag。**

`repo_setup.py plan` 对每个仓给出 `action`、`target_tag` 与可用 tag 样本，把计划用中文列给用户：

| `action` | 含义 | 确认后执行 |
| --- | --- | --- |
| `checkout` | 已有仓 + 匹配到 tag | `git -C <path> fetch --tags && git -C <path> checkout <tag>` |
| `clone_then_checkout` | 没仓 + 远端有匹配 tag | `git clone <url> <dest> && git -C <dest> checkout <tag>` |
| `no_matching_tag_*` | 远端只有 master/main | **不要静默用 master**。报给用户三选一：（a） 用最接近的 tag （b） 暂用 master 并知会风险 （c） 跳过该仓 |

仓名、host、目标版本、落地目录都从 P1 与用户来，不写死。

## 仓依赖发现（P5）

装的是**这几个算子仓构建/跑测需要的依赖**，不是本 skill 自己的依赖。不预设清单：

1. 找各仓的 `requirements*.txt` / `setup.py` / `pyproject.toml` / `docs` 里写的依赖
2. 汇总去重，中文列给用户确认
3. 在**目标 conda env / venv 内**执行 `pip install ...`（`CANN_OPS_DRY_RUN=1` 时只打印不装）
4. 一条依赖声明都找不到 → 如实说「未发现仓级 Python 依赖声明」，问用户是否手动补（numpy、torch_npu 等）
