---
name: cann-env-setup
description: 为 CANN 算子仓搭建可构建/可跑测的基础环境——发现并 source CANN toolkit、检查系统构建依赖（cmake/gcc/ccache…）、按 CANN 版本把算子仓切到配套 tag、把仓依赖装进 conda 环境、单算子冒烟验证。涉及"搭环境/装环境/初始化/准备 CANN 环境/配 conda/bootstrap/setup/为跑测准备机器"等用户意图时使用。机器布局、CANN 版本、conda 是否存在、python 版本均每次会话运行时探测或询问，绝不硬编码到某台机器。
---

# CANN 算子仓环境搭建

把一台裸机或新服务器搭成「能 build、能跑测 CANN 算子仓」的基础环境。

**核心原则：泛化、零硬编码。** 任何一台机器的具体布局（CANN 装在哪、哪个版本激活、
有没有 conda、python 几点几、SOC 是什么）都只是当前个例，一律运行时探测或
`AskUserQuestion` 询问，绝不写死。这条贯穿 P1–P6，下文不再重复。

## 入口参数

| 参数 | 含义 | 取值约束 | 初值推断 |
| --- | --- | --- | --- |
| `<skill>` | 本 skill 的安装路径 | 命令都在**项目根**跑，产物落 `CWD/cann-ops-report/setup/` | 从本文件位置得出 |
| `<python>` | 跑量具的解释器 | 只用 stdlib，系统 python3 即可 | 默认 `python3` |
| `工作区` | 找算子仓、以及 clone 落地的根 | 已存在的可写目录 | 用户给出；没给就问，不要默认当前目录 |
| `目标仓` | 要搭哪几个算子仓 | 仓名自由，如 `ops-cv,ops-math,ops-nn,ops-transformer` | 用户给出 |
| `CANN_OPS_DRY_RUN` | 全程干跑开关 | `=1` 时只探测与出计划，不改任何东西 | 默认关 |

## 前置检查

**副作用一律先确认，不可跳过。** 创建 conda env、`pip install`、`sudo` 装系统包、
`git clone`、`git checkout` tag——每类先把计划列给用户，点头才做。

- **不替用户 `sudo`**：系统包缺失只报告 + 给安装命令，装不装由用户定
- **零持久化配置**：不写 `~/.config`、不改 shell rc（除非用户明确要求）
- 当前只支持 conda / 裸机路径。用户说「在容器里搭」→ 告知容器模式待开放，可先用 conda 模式

## 主流程

六步，每步都能被 `CANN_OPS_DRY_RUN=1` 拦在「出计划」为止。

| 步 | 做什么 | 命令 |
| --- | --- | --- |
| P1 | 探测现状（只读） | `<python> <skill>/scripts/detect_env.py --json` |
| P2 | 系统构建依赖 | 读 P1 的 `missing_required`，不另跑脚本 |
| P3 | conda 环境 | 读 P1 的 `conda`，确认后 `conda create` |
| P4 | 算子仓切配套 tag | `<python> <skill>/scripts/repo_setup.py plan …` |
| P5 | 仓依赖装进 env | 发现声明 → 确认 → `pip install` |
| P6 | 单算子冒烟验证 | `<python> <skill>/scripts/smoke_build.py …` |

**P3 与 P4 的提问并成一次 `AskUserQuestion`**（都只依赖 P1 结果、互不依赖，一次最多 4 问）。
P2 只是报告缺哪些包，本就不必发问。别拆成两轮——每多一轮往返都要重放整个上下文。

### P1 — 探测现状

```bash
<python> <skill>/scripts/detect_env.py --json     # 不带 --json 看人读摘要
```

一次给出：CANN（`set_env.sh` 路径 / `ASCEND_HOME_PATH` / 版本 / SOC / `ready`）、
conda（是否可用 + 已有 env）、系统构建依赖、可见 python 解释器。把摘要用中文呈现给用户，
作为后续每步的依据。

| 判据 | 去哪 |
| --- | --- |
| `ready=false`、版本解析、SOC 探测与映射 | [references/probe-and-tags.md](references/probe-and-tags.md)「CANN 探测」 |
| `missing_required` 非空、`ccache` 缺 | 同上「系统构建依赖」 |
| conda 不可用 / 要新建 env | 同上「conda 三条岔路」 |

### P4 — 算子仓：定位、clone、切配套 tag

```bash
<python> <skill>/scripts/repo_setup.py plan \
  --cann-version <P1 解析出的版本> \
  --repos <仓名 CSV> \
  --search-root <工作区> [--search-root <更多根>] \
  --git-base <仓 host，默认 https://gitcode.com/cann> --json
```

它只出计划不动手。三种 `action` 各自怎么处理、以及**为什么不能停在 master**，见
[references/probe-and-tags.md](references/probe-and-tags.md)「算子仓配套 tag」。

### P5 — 仓依赖

不预设清单，先发现每个仓自己声明的依赖再装。四步见
[references/probe-and-tags.md](references/probe-and-tags.md)「仓依赖发现」。

### P6 — 单算子冒烟验证

挑一个仓、一个最小算子，证明这台机器真能编出算子包：

```bash
<python> <skill>/scripts/smoke_build.py --repo-path <repo> \
  --soc <P1 探到的 build_soc，探不到才问用户> \
  --set-env <P1 的 set_env.sh> [--op <算子，可省则自动挑>] [--jobs 0]
```

| 结果 | 判据 | 去哪 |
| --- | --- | --- |
| 就绪 | 退出码 0 且产出 `.run` | 写汇报，结束 |
| 失败 | 非 0 | grep 它打印的 `log_tail` 拿真实错误，按错误指向回 P2 / P3 / P4 修 |

### 汇报

把 P1–P6 结果写 `CWD/cann-ops-report/setup/env_report.md`（人读）+ `status.json`（机读），
中文总结「已就绪 / 待办」。

## 边界与禁忌

- ✗ 不写死任何机器的路径 / 版本 / 布局 / SOC / python 版本
- ✗ 不替用户 `sudo` 装系统包，只报告 + 给命令
- ✗ 不静默把仓停在 master 编，无配套 tag 必须问用户
- ✗ 不改宿主 shell rc 或全局环境，除非用户明确要求
- ✗ 副作用未经确认不执行，`CANN_OPS_DRY_RUN=1` 时一律只出计划

## 参考资料

- [references/probe-and-tags.md](references/probe-and-tags.md) — P1 探测判据、conda
  岔路、P4 配套 tag 的三种 action、P5 依赖发现。**P1 出现 `ready=false`、
  或 P4 报 `no_matching_tag_*` 时读**
- [references/faq.md](references/faq.md) — 裸机搭建反复遇到的六个环境坑（conda ToS、
  镜像被封、`npu-smi -8005`、装 miniconda、磁盘满、缺 `OP_LOGE_FOR_INVALID_*` 符号），
  每条「现象 → 根因 → 解法」。**卡住时按现象查**
