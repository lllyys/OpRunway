# CANN 内置真值用例设计

这条路先把 CANN 内置实现的输出定为同后端回归比对真值，再据此生成用例。`interface.json.baseline_kind`
为 `cann_builtin` 时，S1 与 S2 读这份设计规则。S3 与 S4 的跑测、真值搬运和取证见验收侧的
`builtin-baseline.md`。

## 目录

- 没有 torch 基线，S2 怎么写
- 种子必须钉死
- 比较器

## 没有 torch 基线，S2 怎么写

这条路上**没有基线函数**。默认路径的一整套「拿 torch 形参名去核对 YAML 输入名」
在这里量的是错的东西，五处都要按这一节写，写错的代价都在很靠后才暴露。

### 一、签名对齐要带 `--interface`

```bash
<python> scripts/align_signatures.py --baseline <内置的 aclnn 名> \
  --task-doc <任务书.md> --interface evidence/interface.json \
  -o evidence/signature_alignment.json
```

带上它，对齐走内置分支：`yaml_key` 取 aclnn 自己的形参名，位置配对是恒等的，
`determinable` 为 true。

不带它，脚本会去 `eval` 基线名。内置那个名字是个 C 接口名，eval 不出来；退一步
填成 `torch.<op>` 也不对——真机实测（bernoulli，2026-08-17）：替身只列到
`['input', 'generator']`，aclnn 有 4 个入参，两侧适配器都判 `determinable=false`，
`check_adapter_binding.py` 退出码 2，S2 卡死；`semantic_review` 还会多出一条
「基线接口有 6 个 aten 重载，要拆成独立接口分面」——那是另一件事的建议，
照做就白拆一轮。

`--baseline` 仍要给，填 `interface.json` 的 `baseline_api`（内置的 aclnn 名），
它只进报告供引用。

### 二、YAML 的输入名照 aclnn 形参名写

`decl.json` 的 `parameters` 键就是 aclnn 声明里的形参名，顺序也照声明顺序。

不是 torch 形参名——没有 torch。`make_yaml.py` 在 `baseline_kind` 是
`cann_builtin` 时跳过基线侧核对，这一侧改由 `check_signature_contract.py`
承担（集合、顺序、多余项三判，比子集判定强）。

### 三、`yaml.name` 只是标签，`api_type` 才是执行的那一侧

CPU 节点两轮都要真跑一次：第一轮供出参的形状与 dtype，第二轮 ATK 建 aclnn
任务时仍要靠它拿形状。而 YAML 的输入名是 aclnn 形参名，喂给任何 torch 函数
都是 unexpected keyword argument。

所以 `yaml` 块**必须**写 `api_type`，指到自己的 `function_<op>.py`；
`make_yaml.py` 缺它直接拒绝生成。

`name` 写工程或任务书里的语义参照（`torch.bernoulli` 之类）即可，它不被执行。

### 四、`function_<op>.py` 是必需品

`signature_alignment.json` 的 `baseline_adapter.required` 在这条路上恒为 true。

它只做一件事：接住 aclnn 的入参名，返回一个与出参**等形状、等 dtype** 的张量。

数值不参与比对（真值来自 `accuracy_load`），所以不要在这里实现算子语义——
随机数生成类算子也实现不了，torch 与 NPU 的随机数流本就不同。

```python
@register("<op>_cpu_shape")
class OpCpuShape(BaseApi):
    def __call__(self, input_data, with_output=False):
        # kwargs 的键就是 aclnn 的形参名；出参与 self 等形状等 dtype。
        return torch.zeros_like(input_data.kwargs["self"])
```

### 五、attr / scalar 的取值写进 combos

`make_yaml.py` 的 attr/scalar 取值只从 combos 读（`combo_key`，默认就是参数名），
契约里的 `range` 对 attr/scalar **不生效**，只有 tensor 用得上。

种子要钉成常量，就在物化脚本里给每条 combo 写上同一个值：

```python
combo["seed"] = 20260817
combo["offset"] = 0
```

## 种子必须钉死

随机数生成类算子走这条路之前，先回答一个问题：

> 待验收算子的接口声明里，种子（`seed` / `offset` 之类）是不是显式入参？

- **是** → 种子在用例数据里钉成常量，两轮吃同一份冻结输入，两侧随机数流相同，
  逐位比对是良定义的判据。
- **否** → 算子内部从全局状态取种子，两轮的随机数流不可控，逐位比对没有意义，
  这条路不成立，回 S1 换判据（分布检验 / 只测确定性边界值 / 同种子自洽性）。

这个问题只能读任务书 §2.3 的接口声明回答；声明里没有种子入参就当作否。

钉常量的写法与门禁见 case-design.md#种子类参数。

ATK 的 `default_seed`（`atk/tasks/backends/backend.py:150`）只管**输入数据生成**的种子，
管不到算子内部的随机数流，不要拿它当依据。

## 比较器

这条路的两侧都在 NPU 上、都是 aclnn，声明 `equal`：任何一位不同都说明改动改变了输出，
正是要抓的东西。

experimental_standard.md 里「浮点的位级相等在 NPU 上不成立」那条判据说的是
**待验收算子与框架基线跨后端比**的场景，不适用于这里。

`equal` 有一条捷径：两份 `.pt` 文件 md5 相同就直接判通过，不再逐元素比
（`atk/tasks/post_process/base_compare.py:141-145`）。位级完全一致时这条捷径会命中，
是预期行为，不是没比。

`equal` 走 `torch.equal`，含部分 NaN 的用例会误判失败，该分面要关掉
`boundary.has_infnan`（见 experimental_standard.md）。
