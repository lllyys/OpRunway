# 真机打通清单：拿 CANN 内置 aclnn 当精度真值

**跑法不在这份文件里。** 完整的命令、目录规则、三件必须取证的事和反证实验都写进了
`skill/repo-task-atk-test/references/builtin-baseline.md`，真机上照那份跑。
这里只列这次打通要额外确认的东西，避免两份文档说同一件事然后各自漂移。

**对象：** 1.2 真机上的伯努利算子（内存优化类改动，验收诉求是「输出相对内置有没有变化」）。

---

## 跑之前

1. 读 `references/builtin-baseline.md` 全文。
2. 回答那份文档「种子必须钉死」一节的问题：伯努利的接口声明里 seed / offset 是不是显式入参。
   答不了就不要往下跑，这条路不成立。
3. 用例集先做最小的，1–3 条，不要上全量。
4. 用户已明确「不看内存数据」，这句话记进 `evidence/constraints.md`，
   否则下一轮 agent 会重新纠结要不要跑 `memory_device`。

## 跑的时候

`references/builtin-baseline.md` 的三步 + 反证实验，一步都不跳。

反证实验是唯一能区分「真的一致」和「其实在自己跟自己比」的手段，它不过就停。

## 跑完要带回来的东西

这几项决定接下来怎么把这条路固化成脚本和门禁：

1. **三件取证各自的实际输出**，尤其第一件——日志里那条 `.so` 加载记录长什么样。
   `capture_reference.py` 要靠它解析路径，格式没见过就写不出来。
2. **最终生效的节点拓扑与目录名。** 文档里写的是 `-b cpu` + `cpu_0` 那种，
   如果真机上走的是回退写法，文档要改。
3. **`equal` 在 NPU vs NPU 浮点输出上的实际表现**，以及 md5 捷径有没有命中。
4. **反证实验里那条失败用例在报告中的呈现形式。** `parse_atk_report.py` 要认得它。
5. **整条链路的耗时**，判断参考跑要不要并进主流程。

## 已经落盘的部分

这次打通之前已经改完的，不用再推一遍：

| 改动 | 位置 |
| --- | --- |
| 这条路的完整规范 | `references/builtin-baseline.md` |
| 种子必须钉成常量 + C7 门禁 | `references/case-design.md#种子类参数`、`scripts/validate_cases.py` |
| 旧的两条错命令（单节点、`-tk run`）已删 | `references/atk-cli.md#基线是 CANN 内置实现时` |
| 选了 `cann_builtin` 要读哪份文档 | `references/intake.md`、`SKILL.md` |

## 还没做、等打通后再做

- `capture_reference.py`：把跑内置、取证 `.so`、搬运产物、写 provenance 包成一个脚本
- `check_golden_source.py`：裁决前核对两侧节点、两轮数据一致、`.so` 归属
- `_coverage_strategy.py` 的 `equal` + 浮点判据按「同后端 / 跨后端」分叉
- `OPERATOR_CLASSES` 补随机生成类
- `verdict.py` 区分「相对内置无回归」与「精度达标」两种结论
