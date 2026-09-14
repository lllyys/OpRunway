# 开发与真机环境

**要在真机上跑任何东西之前读这份**——不论在改哪个 skill，也不论是新建的还是已有的。
纯本地改文档、改不碰 NPU 的脚本时用不到，所以它不进常驻上下文。判据是这次动作
碰不碰真机，不是 skill 的名字。

> **本仓不记任何一台机器的地址、账号、目录布局或 CANN 版本。** 这些每次由用户在入口
> 给出，或由脚本运行时探测——各 skill 的边界条款本来就这么写（「不写死任何机器的
> 路径 / 版本 / 布局 / SOC」）。要给自己的机器留一份备忘，放
> `docs/development/scratch/`，那个目录在 `.gitignore` 里。

## ATK submodule

**submodule 现指向 fork 的 PR 分支，不是上游 master。** 2026-09-04 起
`third_party/ATK` 指 `https://gitcode.com/Justbin/ATK.git` 的
`fix/device-worker-retire`，即上游 [MR !32](https://gitcode.com/Ascend/ATK/merge_requests/32)
的内容；2026-09-07 起是该分支的 `5ada6a8`，评审后把回收判定从任务名换成了 device_run
队列。真机上装的就是这份构建。**MR 合入后指回上游。**

本地查源码用 `third_party/ATK/`，真机上 ATK 由 pip 装在环境里。
**两者必须是同一份代码**，不一致时 reference 里记的行号会对不上源码：

```bash
git -C third_party/ATK log --oneline -1
grep PACKAGE_VERSION third_party/ATK/atk/__init__.py
```

**别拿 `PACKAGE_VERSION` 当同一性判据。** 上游 master、装机那份、我们自己打补丁的
分支，三者的 `PACKAGE_VERSION` 都是 `26.8.8`。判据是 commit，不是版本号。装机那份的
来源由 `probe_env.py` 记进 `env.json` 的 `atk.origin`（pip 的 `direct_url.json`，含
wheel url 与 sha256），`atk.path` 则认得出用 `PYTHONPATH` 挂上来的检出。

不要在全局 Python 里 `pip install -e third_party/ATK`；需要安装时先建虚拟环境。

**要改 ATK 就走 fork 分支，不要在工作区里留改动。** submodule 是 gitlink，未提交的改动
只会让父仓一直显示 `M third_party/ATK`，钉点却没动，两边对不上。正确顺序是：在
`fix/device-worker-retire` 上提交、推到 fork、再在父仓提交挪后的钉点。

**换装过带补丁的 atk，就要能换回去。** 打补丁前把 site-packages 做字节级备份
（原始 wheel 未必还留着，备份目录本身最稳），回滚步骤写在那台机器上。判断当前装的
是哪一份：`DeviceRunWorkerConfig.pool` 是 `prefork` 就是补丁版，`solo` 是原版；补丁版还
分两代，`celery_tasks.py` 里有 `_DEVICE_TASK_NAMES` 的是按任务名判定那版，有
`is_device_run_queue` 的是按队列判定那版。

## 上真机之前

三件事每次都要落实。具体取值由你的机器决定，本仓不记：

| 事 | 少了会怎样 |
| --- | --- |
| `source` 那台机器的 CANN `set_env.sh` | 后续命令一概找不到 CANN |
| 把装了 atk / torch 的解释器放进 PATH | 报 `ModuleNotFoundError: No module named 'atk'`，**很容易误判成装包失败**——系统 python 里本来就没有 |
| 记下这台机器的 CANN 版本 | 各 skill 里标着「实测（CANN x.y.z）」的事实只在对应版本上成立，msprof 的采集与导出行为就跨版本变过 |

## ops-blas 链路的额外前置

`repo-task-blas-case-gen` 只要 python3 标准库，本机就能跑，不碰真机。
`repo-task-blas-accept` 上真机时，要的东西与 ATK 链路不重合：

| 项 | 要求 |
| --- | --- |
| 开发者工程 | ops-blas 检出，含 `build.sh`、`include/`、`test/` |
| 工具链 | cmake、g++、CANN 的 `msprof`、cblas |
| 卡 | 一张空闲卡，`--device` 在编译期固定 |
| 不需要 | ATK、torch、torch_npu 一概不用 |

**工程路径本仓不记，每次由用户在入口给出。** 两条链路的路径互不通用，
不要拿其中一条的当另一条的默认值。

## 跑测现场不进仓

真机上的运行产物落在那台机器的工作区里：用例包是 `<op>/`（生成侧的 `<输出目录>`
取工作区根，算子名当子目录名做隔离），`<op>-verify/` 是验收现场，与用例包同根不同名。

**仓库根出现用例包、`*-verify/` 或 `atk_output/` 一律当 bug 查**——生成侧的
`<输出目录>` 必须由用户指定到工作区，不许落进仓里，也不许落进算子工程里。
新 skill 产出的运行现场同理：落在真机工作区，不进仓，该加的忽略规则加进 `.gitignore`。

ops-blas 链路同理：六件包与 `repo-task-blas-accept` 的工作目录（`results/` 下的构建
日志、GTest JSON、msprof 原始目录）都落在真机工作区，仓里不该出现。
