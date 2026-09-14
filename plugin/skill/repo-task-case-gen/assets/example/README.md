# 三个算子的完整样例

全部是真机产物，在 Atlas A3 + CANN 9.0.0-beta.1 + ATK 26.8.8 上跑通过完整链路。
照抄改名即可。

| 算子 | 用例 | golden | 这份样例演示什么 |
| --- | --- | --- | --- |
| Roll | 180 | 180/180 | 最简形态：只需约束器，两侧执行器都用默认 |
| IndexFillTensor | 200 | 200/200 | `aclIntArray*` 要 CPU 执行器做类型适配 |
| Median | 200 | 200/200 | 多输出算子要把具名元组摊成普通元组 |

## 文件

每个算子三到四份，按 `<Op>` 前缀成组：

| 文件 | 阶段 | 说明 |
| --- | --- | --- |
| `facts_<Op>.json` | S1 | 事实表。dtype 来自任务书或工程 `docs/aclnn*.md`，每项带 `source` |
| `<Op>.yaml` | S2 | 用例设计。`inputs` 只列输入参数，输出参数不写 |
| `<Op>_constraint.py` | S2 | 约束器，处理参数间依赖 |
| `function_*.py` | S2 | CPU 执行器，只有 IndexFillTensor 与 Median 需要 |

## 三份 YAML 的接线对比

看这三列就知道什么时候要写插件：

| 算子 | `api_type` | `aclnn_api_type` | `generate` |
| --- | --- | --- | --- |
| Roll | `function`（默认） | `aclnn_function`（默认） | `Roll_constraint` |
| IndexFillTensor | `function_index_fill_cpu` | `aclnn_function`（默认） | `IndexFillTensor_constraint` |
| Median | `function_median_cpu` | `aclnn_function`（默认） | `Median_constraint` |

**三个算子的 NPU 侧都用默认。** `AclnnBaseApi` 已经处理了 workspace 申请、
executor 传递与张量转换，标准两段式接口不用人写。

## 从这份样例改到别的算子

1. `facts_<Op>.json` 的 `op` / `aclnn_name` / `baseline` / `signature` 换成新算子的
2. `params` 按新签名重列，**输出参数也列上，但 YAML 里不写**
3. `<Op>.yaml` 的 `name` / `aclnn_name` / `generate` 换名，`inputs` 按新的
   `params` 里 `role: "input"` 的部分重写
4. 按 `references/plugin-authoring.md` 的判据表决定要不要写约束器与执行器；
   都不要就删掉插件文件，`generate` 改回 `default`、`api_type` 改回 `function`

## 值得注意的五处

**`extra_numbers: 0`。** 关掉了边界用例。三个算子的接口文档都没说支持空张量，
开着会生成一批空张量用例然后整批假失败。文档写明支持时再开成 `all`。

**`dim_values` 用 2^n 与 2^n±1 成对取值。** tiling 的切分缺陷就藏在
「刚好一块」和「差一个元素」之间。

**约束器里长度取 `min(len(shifts), len(dims), rank)`。** 只按一方截断，
另一方短了补不齐——真机上这个 bug 让 180 条里 34 条标杆跑不出来。

**`self._seq` 不能换成 `case_config.id`。** id 在约束器里恒为 0，
用它分批会让全部用例落进同一个分支——真机上这个写法让 Median 的 200 条
全变成退化维场景，而条数与 dtype 分布都看不出异常。

**要不要写 CPU 执行器，直接试一次 torch 调用就知道。** 三个算子里两个要写，
起因都是参数类型或返回值形态与 torch 对不上，不是算子语义特殊。
