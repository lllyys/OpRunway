# 开发与真机环境

**要在真机上跑任何东西之前读这份**——不论在改哪个 skill，也不论是新建的还是已有的。
纯本地改文档、改不碰 NPU 的脚本时用不到，所以它不进常驻上下文。

判据是这次动作碰不碰真机，不是 skill 的名字。第四个 skill 只要要连 `199.103.1.2`，
下面的 PATH 与 `set_env.sh` 纪律一样适用。

## ATK submodule

本地查源码用 `third_party/ATK/`，真机上 ATK 由 pip 装在 conda 环境里。
**两者版本必须一致**，不一致时 reference 里记的行号会对不上源码：

```bash
git -C third_party/ATK log --oneline -1
grep PACKAGE_VERSION third_party/ATK/atk/__init__.py
```

不要在全局 Python 里 `pip install -e third_party/ATK`；需要安装时先建虚拟环境。
也不要改 `third_party/ATK/` 里的任何文件——它是指向上游的 gitlink，改一行就脏掉。

## 远程验证机

| 项 | 值 |
| --- | --- |
| 主机 | `199.103.1.2`（用户 `jiazhibin`） |
| 解释器 | `~/conda/bin/python`，含 atk 26.8.8、torch 2.10.0、torch_npu 2.10.0 |
| CANN | `/usr/local/Ascend/ascend-toolkit/set_env.sh`，9.0.0-beta.1 |
| SoC | `Ascend910_9382`（A3），构建用 `--soc=ascend910_93` |
| 任务书 | `~/repo-task/repo-test/task-doc/` |
| 算子工程 | `~/repo-task/repo-test/ops-test/` |
| 母仓 | `~/repo-task/ops/ops-math`、`~/agent/cann-ops/ops-nn` |

**每条远程命令都要先 `source /usr/local/Ascend/ascend-toolkit/set_env.sh` 并把
`~/conda/bin` 放进 PATH。** 系统 python3.9 里没有 atk 也没有 torch，
少这一句的报错是 `ModuleNotFoundError: No module named 'atk'`，很容易误判成装包失败。

## 跑测现场不进仓

真机上的运行产物落在 `~/repo-task/repo-test/work/` 下：`atk-case-<op>/` 是用例包，
`atk-verify-<op>/` 是验收现场。

仓库根出现 `atk-case-*/`、`atk-verify-*/` 或 `atk_output/` 一律当 bug 查。
新 skill 产出的运行现场同理：落在真机的工作区，不进仓，该加的忽略规则加进 `.gitignore`。
