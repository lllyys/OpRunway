# 环境与阶段归属

## 环境

运行：

```bash
<python> scripts/probe_env.py [--device N] -o evidence/env.json --env-sh evidence/env.sh
```

使用 `selected_python` 和 `atk_cli`，不要使用裸 `atk`。

`--env-sh` 产出的 `evidence/env.sh` 是此后每条命令的前缀载体：

```bash
source evidence/env.sh && cd <绝对工作目录> && "$ATK_PYTHON" scripts/<脚本>.py ...
```

它固化 CANN 环境、`ATK_PYTHON`、`ATK_CLI`、`ATK_DEVICE` 和 `ATK_SKILL_DIR`。

每个 Bash 都是新 shell，不 source 就等于没加载环境。

量具命令不要接 `| grep`/`| tail` 再读 `$?`：拿到的是管道尾那个命令的退出码，
不是量具的。要看退出码就先落地全部输出（`... > log 2>&1; echo $?`），
再从落地的文件里筛要看的部分。

`ATK_SKILL_DIR` 是量具目录的绝对路径，不要用 `find` 现找。

用户指定 device 时直接记录。

未指定时选择一张健康候选卡，再用正式冒烟验证。

设备错误发生在 `aclrtSetDevice` 前，不进入适配器修正；适配器错误发生在待验收算子的接口名绑定或 ABI 校验阶段。

设备错误先清理残留 `atk node` 进程并查看 `npu-smi`，同卡重试仍失败时停止。

## 阶段归属

阶段顺序由 SKILL.md 的阶段表定义，本文件只提供各阶段的执行细节。

`probe_env.py` 和工作目录属于 S1 出口。

无效用例记录属于 S2 出口。

部署门禁和冒烟属于 S3 出口。

全量精度、非连续轮和裁决属于 S4。

非连续轮复用同一用例和冻结输入，只改变 `--slice_input non_contiguous`。
