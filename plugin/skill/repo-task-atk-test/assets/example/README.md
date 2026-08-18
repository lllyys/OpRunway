# 目标文件模板

一个完整可跑的样例，覆盖 S2 要产出的四类文件，外加 S3 的 aclnn 执行器和 S4 的重放脚本。

载体是 `torch.sum` 这种通用归约，不是任何待验收算子——照抄结构，别照抄取值。

| 文件 | 对应产物 | 改哪里 |
| --- | --- | --- |
| `decl.json` | `<op>_decl.json` | dims、infeasible、parameters、yaml 头部 |
| `materialize.py` | `materialize_<op>.py` | `fill()`、`TARGETED`；规模阶梯用 `_shapes.py` |
| `constraint.py` | `<op>_constraint.py` | 注册名、`TABLE_PATH`、`generate()` 覆写 |
| `function_example.py` | `function_<op>.py` | 注册名、基线调用；默认路径够用就不写 |
| `aclnn_executor.py` | `<op>_executor.py` | 注册名、补位位置；`aclnn_adapter.required=false` 就不写 |
| `replay_case.py` | `replay_<op>.py` | 一般只改命令行参数；精度失败要归因时才用 |

## 支持的数据类型

| 输入 | 数据类型 |
| --- | --- |
| self | FLOAT16、BFLOAT16、FLOAT、INT32 |

这张表在真实验收里是**待验收算子工程的** README 或头文件里的那一张，
`decl.json` 的 dtype 轴只能照它写。本文件在样例里同时充当那份出处。

出处和 dtype 轴是双向核对的。本文件下面的「坑」小节提到了 `int64_t`、`bool`、
`uint32`，它们不是本算子的输入 dtype，所以 `decl.json` 里有一段
`dtype_source_excludes` 逐条写明理由——真实算子的 README 里也会有这种情况
（属性的类型名、输出的类型名），照这个写法处理。

先 `source evidence/env.sh`：模板从 `ATK_SKILL_DIR` 找量具目录。

跑通顺序：

```bash
<python> scripts/make_must_cover.py -d decl.json -o must_cover.json \
  --dtype-source <待验收算子工程>/README.md --env evidence/env.json
<python> materialize.py must_cover.json materialized.json
<python> scripts/make_yaml.py -m materialized.json -o <op>.yaml
```

`make_yaml.py` 本身就是门禁：语义轴、extract 规则与参数契约不自洽时它直接拒绝生成。

后续门禁一律用 `materialized.json` 当分母，不是 `must_cover.json`。

三个已经踩过的坑，模板里已经修好：

- attr 的 dtype 用 C++ 名（`int64_t`、`bool`），和 tensor 的 `int64` 不是一套
- `outputs` 只对 in-place 算子写；多输出函数写了它，ATK 会丢弃返回值
- `frozen_inputs/<id>/input.bin` 用 `atk.common.utils.torch_load_safe` 读；
  torch 没有 uint32，裸 `torch.load` 拿到的是 `{"__type__": "uint32_tensor", ...}` 字典
