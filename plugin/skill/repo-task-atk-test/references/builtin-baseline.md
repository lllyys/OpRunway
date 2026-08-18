# 拿 CANN 内置实现当精度真值

## 目录

- 谁跑在哪
- 什么时候走这一节
- 这条路为什么成立
- 同名算子：不要让 ATK 去搜
- 没有 torch 基线，S2 怎么写
- 真值目录必须长成什么样
- 这条路要跑的脚本
- 两步跑测
- 三件必须取证的事
- 反证实验
- 种子必须钉死
- 比较器
- 结论怎么写
- 已知会踩的四种写法

## 谁跑在哪

先看这张表，它消除这条路上最容易误读的一件事。

| 轮次 | 跑的是谁 | 在哪算 | ATK 节点 |
| --- | --- | --- | --- |
| 第一轮 | CANN 内置实现 | **NPU** | `node -b pyaclnn` |
| 第一轮 | torch 基线（数值不参与比对） | CPU | `node -b cpu` |
| 第二轮 | 待验收算子 | **NPU** | `node -b pyaclnn` |
| 第二轮 | 读第一轮存下的真值 | 磁盘，不计算 | `node -b cpu --task accuracy_load` |

**两轮的待测实现都跑在 NPU 上**，内置也不例外。

让第一轮跑内置的手段是环境变量指到内置那份 `.so`，不是换节点后端。

`derive_interface.py` 的 `--baseline-node-backend` 在这条路上只能填 `cpu`。

它问的是**判定那一轮谁当基线节点**，答案是那个从磁盘读真值的节点。

它挂在 `cpu` 名下是因为 ATK 要求节点有个后端名，而它不占用 NPU。

填 `npu` 会让 `verdict.py` 在报告里找不到对应后端的结果。

旧参数名 `--baseline-device` 仍然可用，但那个词会让人读成「基线跑在哪个设备」。

## 什么时候走这一节

`evidence/interface.json` 的 `baseline_kind` 是 `cann_builtin` 时走这一节，其余情况不看。

这个取值只能由任务书或用户明确要求产生，声明方式见 intake.md#接口和基线。

典型场景：开发者只改了内存分配、循环切分一类不该改变数值的东西，验收要回答的是
「输出相对内置有没有变化」，不是「输出对不对」。

**这不是精度验收，是回归比对。**

结论的强度不同，写法见本文末。

## 这条路为什么成立

内置实现没有可以被 `eval` 的 Python 函数名，挂不上一个基线节点。

但 ATK 的 `pyaclnn` 后端是按环境变量逐级找 `.so` 的，找不到才落下一级
（`atk/tasks/backends/lib_interface/acl_wrapper.py:524-575`）：

| 顺序 | 找哪里 |
| --- | --- |
| 1 | `${ATK_CUSTOM_OPP_PATH}` |
| 2 | `${ASCEND_CUSTOM_OPP_PATH}/vendors/<vendor>/op_api/lib/libcust_opapi.so` |
| 3 | `${ASCEND_OPP_PATH}/vendors/customize/op_api/lib/libcust_opapi.so` |
| 4 | `${ASCEND_OPP_PATH}/lib64/libopapi_xx.so` ← 内置 |

前三级都落空就跑到内置。

所以「跑内置」是一次**环境操作**，不是一个基线节点配置。

## 同名算子：不要让 ATK 去搜

社区算子和 CANN 内置基本都同名，两轮都在按同一个算子名找库。搜错一次，
比的就是同一份实现，报告 100% 通过而什么都没验。

第一级和后三级的行为不一样：

| 级别 | 行为 | 依据 |
| --- | --- | --- |
| `ATK_CUSTOM_OPP_PATH` | **路径存在就直接用，不校验算子在不在里面** | `acl_wrapper.py:536-538` |
| 后三级 | 每个候选 `.so` 都要确认里面真有这个算子的函数才用 | `acl_wrapper.py:545-547,558-559,570-573` |

所以两轮都把 `ATK_CUSTOM_OPP_PATH` 指到一个**确定的 `.so` 文件**（不是目录）：
搜索路径就不再参与决策，同名也不会拿错。

指错了不会静默：绑定阶段直接 `AttributeError: 库文件 ... 中无法找到函数`。

内置那份 `.so` 叫什么要自己解析，候选名单是 `${ASCEND_OPP_PATH}/lib64/` 下的
`libopapi_math.so`、`libopapi_nn.so`、`libopapi_cv.so`、`libopapi_transformer.so`、
`libopapi.so`，挑里面真有 `aclnn<Op>GetWorkspaceSize` 这个函数的那一个（`acl_wrapper.py:565-573`）。

`ATK_CUSTOM_OPP_PATH` 同时决定签名自检去哪个目录 grep 头文件
（`pyaclnn_backend.py:409-425`）。指到内置库时它 grep 到官方头文件，
那正是内置那一轮该对的签名。

跑出来的输出靠 ATK 的 `accuracy_load` 搬到下一次跑测里当真值：节点声明
`--task accuracy_load --output_path <目录>` 之后，该节点的真值不是算出来的，
是从磁盘读的（`atk/common/connection.py:514-521`）。

**ATK 不检查这批 `.pt` 是谁算的。** 比对只查两侧文件个数与 shape 是否一致
（`atk/tasks/post_process/base_compare.py:119,160`），不查来历。这既是这条路能成立的原因，
也是它最危险的地方——目录摆错了照样出一份 100% 通过的报告。

## 真值目录必须长成什么样

标杆数据的路径由四件事唯一决定（`atk/common/utils.py:259`）：

```text
<--output_path>/<backend>_<name>/<用例 JSON 去掉扩展名>/<用例号>/output_<i>.pt
```

| 段 | 由什么决定 | 依据 |
| --- | --- | --- |
| `<--output_path>` | load 节点的 `--output_path`，原样使用，不再拼子目录 | `atk/configs/nodes_config.py:82-85` |
| `<backend>_<name>` | load 节点的 `-b` 与 `--name` 拼成 | `atk/configs/nodes_config.py:90` |
| `<用例 JSON 去掉扩展名>` | `task -c` 给的那份文件的文件名 | `atk/tasks/result_process.py:66-67`、`atk/tasks/main.py:580` |
| `<用例号>` | 用例 JSON 里的 `id` | 同上 |

节点名不写 `--name` 时的默认值：主节点是 `0`，CPU 节点继承主节点的名字，也是 `0`
（`atk/configs/nodes_config.py:141,147-148`）。

所以默认目录名就是 `pyaclnn_0` 和 `cpu_0`。

两次跑测的用例 JSON **文件名**必须一样，改个文件名路径就对不上了，
而对不上的表现是「标杆目录为空」，不是「路径错了」。

`output_info.json` 不在时 ATK 会 glob `output_*.pt` 把出参的 dtype 和 shape 重建出来
（`atk/tasks/opp_tasks.py:496-506`）。所以要搬的是内置那一轮的 **`pyaclnn_0`** 目录，
不是 `cpu_0` 目录——CPU 侧的出参信息是上调过精度的，拿它复跑 fp16/bf16
会在 GetWorkspaceSize 阶段被算子拒绝（同一个坑在 `freeze_inputs.py:250-256` 记过）。

## 没有 torch 基线，S2 怎么写

这条路上**没有基线函数**。默认路径的一整套「拿 torch 形参名去核对 YAML 输入名」
在这里量的是错的东西，五处都要按这一节写，写错的代价都在很靠后才暴露。

### 一、签名对齐要带 `--interface`

```bash
<python> scripts/align_signatures.py --baseline <内置的 aclnn 名> \
  --header <工程目录>/op_api --aclnn-name <yaml-aclnn-name> \
  --env evidence/env.json --interface evidence/interface.json \
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

## 这条路要跑的脚本

按顺序，四步：

```bash
# 1. 解析两侧的算子库，各自钉死
<python> scripts/resolve_opp_library.py --op <aclnn 名> --side builtin \
  -o evidence/opp_library_builtin.json
<python> scripts/resolve_opp_library.py --op <aclnn 名> --side candidate \
  --library <本轮 vendor 的 .so> -o evidence/opp_library_candidate.json

# 2. 内置那一轮跑完之后取证并搬运
<python> scripts/capture_reference.py --op <aclnn 名> \
  --from-run <atk_output/<任务>/output> --log evidence/builtin_run.log \
  --case-json <case-json> --input-data <frozen> \
  --library-fingerprint evidence/opp_library_builtin.json \
  --staged evidence/golden_builtin -o evidence/golden_provenance.json

# 3. 反证实验：改坏一条，重跑第三步，记结论
<python> scripts/capture_reference.py --op <aclnn 名> --tamper <用例号> \
  --staged evidence/golden_builtin -o evidence/golden_provenance.json
<python> scripts/capture_reference.py --op <aclnn 名> \
  --conclude-tamper <用例号> --detected \
  --staged evidence/golden_builtin -o evidence/golden_provenance.json

# 4. 裁决前核对（--candidate-log 是第二轮的跑测日志，必填）
<python> scripts/check_golden_source.py --case-json <case-json> \
  --candidate-log evidence/accuracy.log
```

每一步跑测前要 export 的 `ATK_CUSTOM_OPP_PATH`，由 `resolve_opp_library.py`
直接打印出来，照抄即可。

第 3 步的 `--conclude-tamper` 不带 `--detected` 表示那条用例没变 Fail。

那意味着比对拓扑没生效，脚本会退出码 2，`verdict.py` 也拒绝出结论。

## 两步跑测

冻结输入与用例 JSON 在两步里是同一份，不重新生成。

### 第一步：跑内置，存输出

在**没有 source `evidence/env.sh`** 的干净 shell 里：

```bash
export ATK_CUSTOM_OPP_PATH=<内置 .so 的完整路径>

<python> scripts/run_atk_task.py -o evidence/builtin_run.log -- \
  <atk-cli> node -b pyaclnn --devices <device> \
             node -b cpu \
             task -c <case-json> -tk accuracy \
                  --input_data <frozen> --save_data output
```

CPU 节点在这一步**必须有**，它做两件事：让任务建得起来（见本文末第一条），
以及给 pyaclnn 侧供出参的形状与 dtype。

CPU 节点跑的是 torch 基线，它的**数值不参与后面的比对**。随机数生成类算子
在这一轮必然大面积 Fail，这是预期的，不看，也不据此下任何结论。

### 第二步：搬运

```bash
BUILTIN_OUT=$(ls -dt atk_output/*/output | head -1)
mkdir -p evidence/golden_builtin
cp -r "$BUILTIN_OUT/pyaclnn_0" evidence/golden_builtin/cpu_0
```

改名成 `cpu_0` 是因为下一步的 load 节点声明成 `-b cpu`，ATK 就去读 `cpu_0`。
这是 ATK 自带用例验证过的形态（`atk/tests/st/op_tasks/single_process/test_single_process_pyaclnn_task.py:63`）。

### 第三步：跑待验收算子，与内置比

```bash
source evidence/env.sh   # 里面的 ATK_CUSTOM_OPP_PATH 指向本轮 vendor 的 .so

<python> scripts/run_atk_task.py -o evidence/accuracy.log -- \
  <atk-cli> node -b pyaclnn --devices <device> \
             node -b cpu --task accuracy_load \
                  --output_path <绝对路径>/evidence/golden_builtin \
             task -c <case-json> -tk accuracy --input_data <frozen>
```

`--output_path` 必须写绝对路径。

这一轮 CPU 节点仍然会执行一次 torch 基线，因为主节点是 aclnn 且还没有出参信息时
ATK 要靠它拿形状（`atk/tasks/executors/opp_executor.py:582-584`）。它的输出不会覆盖
已经摆好的真值文件——同名文件存在时 ATK 直接跳过写盘
（`atk/tasks/executors/opp_executor.py:299`）。

所以 **torch 基线插件在两轮里都必须能跑通**，即使它一个数值都不参与比对。

## 三件必须取证的事

这三件事不取证，报告就是空的。

取证结果写进 `evidence/`，报告引用。

两轮日志里都有这一行，info 级，默认日志级别就打得出来（`pyaclnn_backend.py:246`）：

```text
import aclnn<Op>GetWorkspaceSize  from <so 路径> success!
```

**一、第一轮加载的是内置。**

那条路径在 `${ASCEND_OPP_PATH}/lib64/` 下，不含 `vendors`。

**二、第二轮加载的是本轮 vendor。**

两轮的路径必须不同。

一样就是自己跟自己比，这轮什么都没验，报告作废。

两个 `.so` 的 SHA256 一并记下来，报告引用。

**三、两轮吃的是同一份数据。**

用例 JSON 与冻结输入目录的 SHA256 两轮一致。

## 反证实验

前面全绿**不能**说明比对成立。load 节点没生效、或者待验收算子在跟自己比，
结果同样是 100% 通过。所以必须做一次「本该失败」的对照：

挑一条用例，把 `evidence/golden_builtin/cpu_0/.../output_0.pt` 里改一个元素，重跑第三步。

被改的那条用例必须变成 Fail，其余保持原状态。

仍然全绿就是拓扑没生效，不要往下走。

改之前先备份整个 `golden_builtin`，验完恢复。

这一步不是可选的谨慎，是这条路唯一的判别力验证。

## 种子必须钉死

随机数生成类算子走这条路之前，先回答一个问题：

> 待验收算子的接口声明里，种子（`seed` / `offset` 之类）是不是显式入参？

- **是** → 种子在用例数据里钉成常量，两轮吃同一份冻结输入，两侧随机数流相同，
  逐位比对是良定义的判据。
- **否** → 算子内部从全局状态取种子，两轮的随机数流不可控，逐位比对没有意义，
  这条路不成立，回 S1 换判据（分布检验 / 只测确定性边界值 / 同种子自洽性）。

这个问题只能读待验收算子工程目录里的头文件回答（红线 1 允许），不看 kernel 与 host 实现。

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

## 结论怎么写

拿内置当真值，能下的结论只有一句：**待验收算子的输出与 CANN 内置实现逐位一致（或第 N 条不一致）**。

不能写成「精度达标」——内置实现本身没有被这轮验收检验过，它只是对照物。

报告必须写明：内置库的完整路径与它的 SHA256、两轮的用例 JSON 与冻结输入的 SHA256、
反证实验的结果。

缺一项，结论不成立。

## 已知会踩的四种写法

| 写法 | 会怎样 | 依据 |
| --- | --- | --- |
| 第一步只挂一个 pyaclnn 节点 | 直接 `RuntimeError: not other task, please check node config`，任务建不起来 | `atk/tasks/task_creator/aclnn_task.py:54` |
| 第一步用 `-tk run` 存输出 | 一个 `.pt` 都存不下来。`run` 走的分支拿到输出就丢，不落盘 | `atk/tasks/executors/opp_executor.py:574-576` |
| 以为 `--save_data` 决定存不存 | 它只是收尾时的目录保留过滤器，决定存不存的是任务类型 | `atk/tasks/main.py:283` |
| 搬 `cpu_0` 目录当真值 | 出参 dtype 被上调过，复跑时 fp16/bf16 用例在 GetWorkspaceSize 被算子拒绝 | `freeze_inputs.py:250-256` |

load 节点也可以声明成 `-b pyaclnn --name builtin`（真值目录相应改叫 `pyaclnn_builtin`），
但建执行任务时只跳过 `bm_file` 节点、不跳过 `accuracy_load` 节点
（`atk/tasks/task_creator/aclnn_task.py:133`），两个 pyaclnn 节点会不会把待验收算子跑两遍
（那就是自己跟自己比）没有验证过。

**默认用 `-b cpu` 那种写法**，它有 ATK 自带用例兜底。
