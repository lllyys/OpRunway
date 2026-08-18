# 写 spec、design 与调用 ATK 的形态

本仓自己的形态约定，不是上游文档。字段含义与完整取值以 `atk/` 下的上游逐字副本为准；这里只回答
「长什么样」和「哪些地方踩过坑」。

命令与 YAML 骨架取自一次真跑通的验收（ATK 26.5.14，commit `7220f27`，生成 225 个 case 并产出正式终态），
不是从文档转述的。标注「未实测」的条目请自行验证后再依赖。

## 目录

- [op.spec.json 的形状](#opspecjson-的形状)
- [字段约束](#字段约束)
- [ATK design 的形状](#atk-design-的形状)
- [boundary 的五个开关](#boundary-的五个开关)
- [能力落点](#能力落点)
- [实测通过的 ATK 命令](#实测通过的-atk-命令)
- [未实测的部分](#未实测的部分)

## op.spec.json 的形状

只放任务与 ABI 事实。ATK 版本、并发度、超时、device 号属于本轮运行环境，记在收据里，不进这份契约。

```json
{
  "schema": "oprunway.acceptance_spec",
  "schema_version": 2,
  "operator": {
    "name": "Example",
    "aclnn_name": "Example",
    "op_type": "Example",
    "build_token": "example",
    "source_subdir": "domain/example"
  },
  "task": {
    "taskdoc_sha256": "<64 hex>",
    "hardware": ["ascend910_93"],
    "precision": {"atk_accuracy": "single_bm"},
    "performance_is_verdict": false,
    "unvalidated_requirements": ["无法由本 workflow 取证的任务书原文条款"],
    "required_cases": [
      {"inputs": [{"index": 0, "dtype": "fp32", "shape": [2, 3]}]}
    ],
    "performance_cases": []
  },
  "runner": {"form": "atk_aclnn", "seed": 17},
  "build": {"profile": "cann_ops_package_v1", "vendor_name": "oprunway"}
}
```

## 字段约束

| 字段                         | 含义                                                                           | 约束                             | 违反时                         |
| -------------------------- | ---------------------------------------------------------------------------- | ------------------------------ | --------------------------- |
| `task.hardware`            | CANN 规范 SoC token，如 A2 `ascend910b`、A3 `ascend910_93`、Ascend 950 `ascend950` | 新硬件无需改通用代码                     | 目标 SoC 不在集合内判 `UNSUPPORTED` |
| `precision.atk_accuracy`   | ATK 精度比较器                                                                    | 与 design 的 `standard.acc` 逐字一致 | 不一致即回步骤 2 改齐，不靠执行期发现        |
| `performance_is_verdict`   | 性能是否构成终态判据                                                                   | 由任务书决定；**测量恒做，与本字段无关**         | —                           |
| `required_cases`           | 任务书最低覆盖契约，按 ACLNN 参数顺序列出必须生成的 dtype/shape/属性子集                               | 每条必须映射到一个**不同的**实际 case ID     | 缺任一条即停；非空 caseset 不构成放行理由   |
| `performance_cases`        | 跑 profiler 的代表场景                                                             | 任务书指定就用它的；没指定按 SKILL 步骤 6 的规则选 | —                           |
| `unvalidated_requirements` | 本 workflow 无法取证的任务书条款                                                        | 逐项写入并由报告原样保留；无条款时用空列表          | 报告中不得宣称达标                   |

## ATK design 的形状

YAML 或 CSV。下面是 YAML 骨架，尖括号处按算子填：

```yaml
api: pytorch                    # CPU 真值来源
api_type: function
name: <torch 参考实现，如 torch.roll>
aclnn_name: <算子名>
aclnn_api_type: <aclnn_function；用了 execution plugin 就填插件里注册的名字>
version: <ATK 语义版本>
generate: <generator plugin 名；不用就删掉这一行>
backward: false
outputs: null
dtype_numbers: <每个 dtype 生成几个 case>
extra_numbers: all              # 边界 case 数量，all 表示不设上限
standard:
  acc: <与 spec 的 precision.atk_accuracy 逐字一致>
  perf: not_key
shape_distributions: [[0, 1.0]] # 别改：其它取值极易让生成器死循环
inputs:
  - name: null
    type: tensor                # 或 attrs
    required: true
    backward: false
    dtypes:
      values: [<按任务书列全，含必须保持不变的原有 dtype>]
    ranges:
      valid:
        values: [[-5, 5]]
    shapes:
      dim_numbers: {values: [1, 2, 3, 4, 5, 6, 7, 8]}
      dim_values: {values: [1, 3, 7, 8, 15, 16, 17, 31, 32, 33, 64, 65, [1, 256]]}
      max_length: 262144
    boundary:
      has_empty: true
      has_infnan: false
      has_scalar: true
      has_upper_border: false
      has_lower_border: true
```

design 规则：

- **精度判据一律取自随包的《生态算子开源精度标准》**（`reference/experimental_standard.md`，从 SKILL.md
  直接进）。任务书里凡引用 AscendOpTest 之处都读作这份。用 `mixed_tolerance_bm`，把该 dtype 的 `rtol`、
  `atol`、`required_matched_ratio`、`max_abs_error_limit` 从该标准 §2.2 逐字抄进 design 对象，不得用
  ATK 的隐式默认值。
- `complex64` 用 FLOAT32 那一列，实部与虚部各自按该列判定。
- §2.2 只覆盖 6 种浮点。**表外的整型与 bool 不在该标准范围内**（其 §0 写明需按算子实际业务场景单独
  制定）。这些 dtype 精确可表示、不存在舍入，容差没有意义，所以默认判据就是**逐位相等**，spec 里写明
  这条依据即可。好消息是不用自己接线：写单份 `mixed_tolerance_bm`，ATK 会把整型与 bool 自动回落到
  逐位相等（分流表见 atk-internals.md），**不必按 dtype 拆成多份 design**。
- **`int8` 是唯一例外**：它会走量化标准、允许绝对误差 ≤ 1，对搬运类算子太松。**在 generator plugin 的
  `after_case_config()` 里把这批 case 的 `standard.acc` 改成 `"equal"`** 即可——一次 casegen 一次执行，
  caseset 仍由 ATK 生成，ID 不撞号。不要跑两遍 casegen，也不要事后改 JSON；已排除的其它路径见
  atk-internals.md。接受 ±1 也是一种选择，但必须在 spec 里记 `UNVALIDATED`，不能默不作声。
- 例外之二：算子语义本身允许整型结果有差异时（饱和或舍入策略、归约顺序影响溢出等）不能用相等，须按
  任务书单独声明。不得把浮点表里的某一列套到表外 dtype。
- 随机算子必须用任务书授权的固定种子或统计策略；不得用逐元素比较冒充统计验收。
- 该标准 §1.4 要求 INF/-INF/NAN 对每种 dtype 都覆盖，因此 `has_infnan` 不能关。注意 `single_bm` 在 CPU
  真值含 nan/inf 时会无条件判过（见 atk-source-facts.md），走那条通路这些用例不具判别力。

## boundary 的五个开关

**两个已知的假通过陷阱都在这里，必须显式写、不要用默认值。**

| 开关                 | 默认   | 说明                                                                                                                                 |
| ------------------ | ---- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `has_infnan`       | true | CPU 真值含 nan/inf 时 `single_bm` **无条件判该 case 通过**，根本不读 DUT 输出。数据搬运类算子应显式关掉并写明理由；任务书确实要求非有限值时，改用能检查 NaN mask 与 Inf 符号的比较器，不能删掉必测 case |
| `has_upper_border` | true | 会生成 21 亿元素量级的轴，忽略 `max_length`，没有设备装得下。这类 case 用来探形状上限，不是用来执行的                                                                     |
| `has_empty`        | —    | 空 Tensor 仍进功能/精度分母，但不产生 kernel，排除在 profiler 分母之外                                                                                   |
| `has_scalar`       | —    | 标量退化形态                                                                                                                             |
| `has_lower_border` | —    | 下界形态                                                                                                                               |

## 能力落点

先选最小的已声明能力。缺口留在被哈希绑定的 witness plugin，作为本轮 session 的输入，不进仓库。

| 已确认事实                               | 落点                                 |
| ----------------------------------- | ---------------------------------- |
| ATK design 原生可表达                    | 只写 spec 与 design，无插件               |
| 仅 case 组合或生成顺序无法表达                  | generator plugin，作为 session 输入哈希绑定 |
| CPU 真值、统计比较或 ACLNN ABI 与 ATK 通用桥不兼容 | execution/accuracy plugin，同上       |

## 实测通过的 ATK 命令

```bash
"$ATK" --version                      # 26.5.14

# 生成 caseset
"$ATK" case -f <design>.yaml -p <generator>.py -s <seed>
# 产物固定落在 result/<design 主名>/json/all_<design 主名>.json

# 执行前的环境。CANN 的 set_env 会读未定义变量，set -u 必须临时关掉
set +u; source "$(find "$SESSION/install" -path '*/bin/set_env.bash' | head -1)"; set -u
export ASCEND_RT_VISIBLE_DEVICES=<物理卡号>
export ATK_CUSTOM_OPP_PATH=<本轮建出的 libcust_opapi.so>

# 精度执行。-o 的目录 ATK 只校验存在、不会创建
mkdir -p "$OUT"
"$ATK" aclnn "$CASESET" --task accuracy --devices 0 \
  --plugin <execution_plugin>.py -o "$OUT" --save_data output
```

`--devices` 恒为 `0`：物理卡靠 `ASCEND_RT_VISIBLE_DEVICES` 映射进来，ATK 侧永远只看到逻辑 0。

`atk aclnn --help` 的 `--task` 接受 `accuracy`、`performance_e2e`、`performance_device` 三个值。

`atk case` 的 `-ps/--perf_standard` 收两个逗号分隔的值：所有 case 的平均性能比阈值、单 case 的性能比
阈值。**性能判据是在生成 case 时设的，不在执行时**，所以只有 `performance_is_verdict` 为真才传它。

## 未实测的部分

- **性能执行的完整命令。** `--task performance_device` 在 `--help` 里存在，但本仓尚未真跑过一次；
  profiler 数据落盘位置、是否需要额外开关，都还没有实测证据。
- **`--save_data output`。** 实跑中确实生效（225 份输出全部保存），但 `atk aclnn --help` 里没有这个
  选项。能用，但不在文档中。
