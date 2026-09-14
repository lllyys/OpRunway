# 安装与依赖

装 skill 本身只是拷目录，几乎不用装东西。真正要准备的是各 skill 跑起来需要的
机器与软件——本文按「装完能干什么」列全。

## 取得本仓

```bash
git clone https://gitcode.com/Justbin/repo-task-atk-test.git
cd repo-task-atk-test
```

## 装 skill

### 整仓当 plugin

`.claude-plugin/plugin.json` 已登记全部 13 个 skill，把本仓当 Claude Code plugin
加载即可，不用逐个拷。

### 只装其中几个

三选一，`<name>` 换成 [README 能力清单](../README.md#-能力清单) 里的任一名字，
要几个就各装一次。

| 方式 | 命令 | 特点 |
| --- | --- | --- |
| 软链接 | `ln -s "$PWD/skill/<name>" ~/.claude/skills/<name>` | 跟着本仓更新，**路径必须写绝对**，写相对会链空 |
| 拷到用户目录 | `cp -r skill/<name> ~/.claude/skills/ && rm -rf ~/.claude/skills/<name>/{tests,CLAUDE.md}` | 所有项目可用，本仓更新后要重拷 |
| 拷到项目目录 | `cp -r skill/<name> <你的项目>/.claude/skills/ && rm -rf <你的项目>/.claude/skills/<name>/{tests,CLAUDE.md}` | 只对该项目生效，可随项目一起提交 |

两条 `cp` 后面那截删的是**开发态文件，运行时一个字都不读**：

| 删什么 | 是什么 | 不删会怎样 |
| --- | --- | --- |
| `tests/` | 纯逻辑单测，改 skill 时在本仓跑 | 占体积。atk-accept 实测 47 份文件 789 K 降到 32 份 640 K |
| `CLAUDE.md` | 改这个 skill 的开发者读的红线与真机事实 | atk-accept 那份 43 K；`cd` 进安装目录时 Claude Code 会把它当目录级 CLAUDE.md 加载 |

**软链接与 plugin 这两条排除不掉**，指的都是仓里的目录本身。装在开发机上无妨
——本仓已经在手边，多这两样不多。

## 各 skill 需要什么

| skill | 需要 |
| --- | --- |
| `repo-task-doc-write` | python3 标准库 |
| `repo-task-case-gen` | 上述 + ATK + CPU 版 torch，**不需要 NPU** |
| `repo-task-atk-accept` | 上述 + torch_npu + CANN 工具链 + 一张健康的 NPU 卡 |
| `repo-task-blas-case-gen` | python3 标准库，**不需要 NPU、CANN 或 ATK** |
| `repo-task-blas-accept` | CANN（含 `msprof`）+ cblas + 一张空闲卡 + 开发者的 ops-blas 工程 |
| `cann-env-setup` | python3 标准库。它本身就是用来装环境的，不预设机器上已有什么 |
| `cann-950-feature-scan` | python3 + `jinja2`（渲染报告）。缺了会在第一步自动装：`pip install -r skill/cann-950-feature-scan/requirements.txt` |
| `cann-ops-run` | python3 标准库 + CANN 工具链 + NPU |
| `cann-issue-report` | python3 标准库 |
| `cann-issue-track` | python3 标准库 + CANN 工具链 + NPU（要复测社区给的方案） |
| `cann-doc-quickstart-check` | python3 标准库 + NPU（要照文档真的跑一遍） |
| `cann-doc-tutorial-review` | python3 标准库 + 系统 `grep`，只静态查证，不跑真机 |
| `cann-page-inspect` | python3 + `httpx` + `playwright` + 本机 Google Chrome。**不需要 NPU，也不需要跑 `playwright install`**，它复用本机已装的 Chrome |

## ATK 两个 skill 还要装 ATK

`repo-task-case-gen` 与 `repo-task-atk-accept` 依赖 ATK。跑测侧还要求这台机器有
NPU、CANN 已装好且 `set_env.sh` 能 source、torch_npu 可用。

```bash
git submodule update --init

cd third_party/ATK
python setup.py bdist_wheel
pip install dist/*.whl
cd ../..
```

### 装的是打过补丁的那一份

`third_party/ATK` 指向的**不是上游 master**，是 fork 的 `fix/device-worker-retire`
分支（上游 [MR !32](https://gitcode.com/Ascend/ATK/merge_requests/32) 的内容）。
补的是一处跑测行为：

| 项 | 上游 master | 本仓钉的这份 |
| --- | --- | --- |
| `DeviceRunWorkerConfig.pool` | `solo` | `prefork` |
| 一条用例把 device context 打废之后 | 同批后续用例全部连带失败 | 崩掉的子进程由 celery 重建 |

**所以不要执行 `pip install atk`，也不要换用其他版本的 ATK**，两个理由都要紧：
换成上游那份，跑测会多出一整类批内连带失败，而这类失败在报告上和真实缺陷长得一样；
`repo-task-case-gen` 的 ATK 接口清单也是从这一份查出来的，换版本后清单对不上
（见 `skill/repo-task-case-gen/CLAUDE.md`）。

### 装完核三条

前两条只证明装上了，**第三条才分辨得出装的是哪一份**——上游、fork、补丁版三者的
`PACKAGE_VERSION` 都是 `26.8.8`，版本号不是同一性判据。

```bash
pip show atk
atk case --help
python3 -c "from atk.tasks.task_creator.worker_config import DeviceRunWorkerConfig as C; print(C.pool)"
```

第三条打 `prefork` 就是本仓期望的那份，打 `solo` 说明装成了上游原版，回到上面重装。

### 版本要求

本仓不预设任何一台机器的版本，具体取值由 `probe_env.py` 运行时探测并写进
`env.json`。下面是硬下限，来源是 ATK 自己的声明与昇腾的配套关系：

| 项 | 要求 | 来源 |
| --- | --- | --- |
| python | ≥ 3.8 | `third_party/ATK/setup.py` 的 `python_requires` |
| numpy | **< 2.0.0** | `third_party/ATK/requirements.txt`，装 numpy 2.x 会让 ATK import 失败 |
| pydantic / celery | 2.6.3–2.13.4 / ≥ 5.4.0 | 同上，`pip install` 会自己解 |
| torch 与 torch_npu | **版本号必须一致**，且与本机 CANN 配套 | 昇腾的配套表，本仓不复制 |
| CANN | 能 `source set_env.sh` | 跑测侧才要 |

各 skill 里标着「实测」的事实是在 CANN 9.0.x 上量的，换大版本时先复核那些标注。

### 装不上时

| 现象 | 判据 | 处理 |
| --- | --- | --- |
| `ModuleNotFoundError: No module named 'atk'` | 命令用的解释器里没有 atk | **不是装包失败**。系统 python 里本来就没有，把装了 atk 的解释器放进 PATH 再跑 |
| `git submodule update --init` 拉不到 | fork 仓不可达 | 不要改用 `pip install atk` 顶替，那装的是行为不同的一份。先解决访问 |
| 第三条核出 `solo` | 装成了上游原版 | 多半是 `pip install dist/*.whl` 装到了别的环境，或环境里已有一份 atk。先 `pip uninstall atk` 再装 |
| import 时报 numpy 相关错 | numpy ≥ 2.0 | 降到 `numpy<2.0.0` |

## 七个 `cann-*` skill 的共同约定

`cann-env-setup`、`cann-950-feature-scan`、`cann-ops-run`、`cann-issue-report`、
`cann-issue-track`、`cann-doc-quickstart-check`、`cann-doc-tutorial-review` 七个
共用 `CWD/cann-ops-report/` 这一处产物根（`cann-page-inspect` 不在其列，它自己出报告）。

**命令一律在你的项目根目录跑，不要 `cd` 进 skill 的安装目录**——那会把跑测产物
写进 `~/.claude/skills/` 里面，下次更新 skill 时连同产物一起被覆盖。
