# 内置 aclnn 基线

`facts.json` 的 `accuracy.kind` 填 `builtin` 时读这份，S4 冻 golden 用。
`kind=torch`（缺省）时用不到。

适用范围：**不改语义只改实现**的优化与重构任务。pyaclnn 按 `aclnn_name` 拼符号、
按位置传参，一次跑测里两个 pyaclnn 节点共用一份 case config，给不出两套调用约定，
也没有 per-node 的 `aclnn_name` 覆盖。torch 基线那侧靠 `function_<op>.py` 兜签名
差异，内置基线这侧没有适配层，也加不了——所以内置基线只支持同名同签名。

## 前置

| 项 | 要求 |
| --- | --- |
| 环境 | NPU + CANN，先 `source set_env.sh` |
| 符号 | `$ASCEND_OPP_PATH/../lib64/libopapi*.so` 里有 `aclnn<Name>GetWorkspaceSize` |

`freeze_golden.py` 找不到符号时退 2 停在 S4，打印找的符号名与搜过的 glob。
**如实报告，不要替用户改基线**——任务书指定了对标内置实现，回落到 torch 基线会把
验收判据悄悄换掉。

跑之前脚本会摘掉 `ATK_CUSTOM_OPP_PATH` 与 `ASCEND_CUSTOM_OPP_PATH`，否则跑到的是
待验收实现，等于拿被测算子给自己当标杆。

## 那个 cpu 节点只给形状

`kind=builtin` 起 pyaclnn + cpu 两个节点，cpu 那份的**值不参与比对**：aclnn 的 `out`
是第一段接口的入参，调用方要先申请好，pyaclnn 推不出它多大。

所以这一轮仍要写 CPU 执行器（**在 S2 写，不是走到 S4 才写**），但返回形状与 dtype
正确的张量就够，照抄 `assets/function_bernoulli.py`。

形状怎么算看算子：输出与输入同形的用 `torch.zeros_like(self)`；**广播类算子要按
`torch.broadcast_shapes(...)` 算**，用 `zeros_like(self)` 会让 aclnn 的 `out` 按 self
申请，两个输入形状不同的用例全部对不上。

## 目录名

| 目录 | 是什么 |
| --- | --- |
| `pyaclnn_builtin/` | 真正的基线，`golden/manifest.json` 的 `baseline_dir` 记的是它 |
| `cpu_builtin/` | 只给形状的废数据 |

**不叫 `pyaclnn_0`。** 那是跑测侧待验收节点的名字，重名时 ATK 会把加载节点改名，
一条都匹配不上。脚本已经写死，看到 manifest 里是 `pyaclnn_builtin` 就是对的，
不要去「修」它。
