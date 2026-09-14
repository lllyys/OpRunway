# 自带件采纳与执行剖面

约束 S1 的 `backend` 字段与 S2′ 的采纳流程。**用例设计怎么写不在这份**，
那在 [case-strategy.md](case-strategy.md) 与 [yaml-authoring.md](yaml-authoring.md)。


## S2′ 采纳自带件（可选）

**先按上面的判据决定走不走这条。** 走的前提是任务目录里同时有这两样：

```bash
ls <任务目录>            # 找用例清单（JSON 数组，每项含 id 与 inputs）
grep -rl "BaseApi" <任务目录> --include="*.py"   # 找执行器插件
```

| 两样都有 | 只有一样或都没有 |
| --- | --- |
| 走本节，S2/S3 整段跳过 | 走 S2/S3，自带件那半边当参考不当依据 |

```bash
cd <工作目录> && <python> <skill>/scripts/adopt_kit.py \
    --kit <任务自带件目录> --op <op> -o . --backend npu \
    --perf-kind <四选一> --band-attr <规模轴参数名> \
    --group-attr <分组轴参数名> --group-labels <取值名字，逗号分隔>
```

`--group-attr` **必给**：纯 attr 用例取不到 dtype，不给的话精度表塌成一行、
跑测侧的冒烟也只抽得出一条，「整类失败」两处都发现不了。

| 退出码 | 含义 | 去向 |
| --- | --- | --- |
| 0 | 用例、插件、`perf/`、`facts.json` 都齐了 | 进 S4 |
| 2 | 自带件的适配脚本跑挂了，或它没写出用例文件 | 手工跑一遍看它写到哪，用 `--cases` 直接指过来 |
| 3 | `--kit` 不是目录 | 核路径 |
| 4 | 自带件不全，或用例的 `api_type` 与执行器注册名对不上 | 屏幕上已列出缺什么、去 S2 哪一步补 |

**它不改自带件的任何语义。** 采纳边界、自带件不全时补什么、`--group-attr`
挑哪个轴，都在 [kit-adoption.md](kit-adoption.md)。

**只在要把自带件的用例并进自产用例包时才走这条。** 任务书自带跑测件、
要验的就是任务方那批用例时，整条生成侧都不参与，直接走跑测侧的自带件路。
## 判据

**`backend` 怎么判、npu 剖面下哪两个字段受限，都在
[interface-facts.md](interface-facts.md)「backend」那一节**——那是 S1 填
`facts.json` 的地方，自产路与采纳路都要过。这里只补一条自带件特有的。

实测：ops-sparse 全仓 `GetWorkspaceSize` 命中 0，导出的是 `aclsparseSpMV` 这类
一段式 C 符号（`nm -D libops_sparse.so`，2026-09-07）。ATK 的 pyaclnn 后端按
`GetWorkspaceSize` 这个符号名解析，绑不上，所以这类仓只能走 `npu`。

## 自带件的用例形态：attr 编码

任务方把整组输入压成几个整数（格式号、dtype 号、造数 seed），张量由执行器插件
按 seed 现造。这是**用例形态**那条轴的取值，与 `backend` 无关——同一个 `npu`
剖面下自产用例走的是真张量声明。两条轴的区分见
[interface-facts.md](interface-facts.md)「用例形态」。

采纳自带件时两个字段跟着这条形态走，`adopt_kit.py` 自己填，不用手动改：

| 字段 | 取值 | 为什么 |
| --- | --- | --- |
| `non_contiguous.required` | `false` | `--slice_input` 切的是 ATK 自己造的输入张量，切不到执行器现造的那份。填 `true` 会多跑一整轮，两轮字节完全相同 |
| `inputs/<用例 id>/input.bin` | 不冻 | ATK 的 `--input_data` 读的是张量字节，喂不进 attr 编码的用例。跑测侧按产物不在自动跳过，不用配 |

**确定性因此要另外证。** 真张量声明的用例靠冻输入字节保证两侧同一批输入；
attr 编码靠用例把造数 seed 编码进参数，重跑必定同字节，但这一条没有量具核过。
ATK 有 `accuracy_dc`（确定性计算）任务可以核它，本链路**尚未接入**。

## 自带件的四类文件

`adopt_kit.py` 按内容认，不按文件名认——任务方的命名不统一，
实测见过 `accuracy_cases.json` / `cases.json` / `*_cases.json` 三种。

| 类别 | 识别式 | 缺了会怎样 |
| --- | --- | --- |
| 用例清单 | JSON 数组，每项含 `id` 与 `inputs` | 退 4。走 S2/S3 自己设计 |
| 用例适配脚本 | `.py` 里有 `def adapt` 且提到 `api_type` | 可缺。缺了直接拷用例清单 |
| 执行器插件 | 继承 `BaseApi` 且有 `@register` | 退 4。照下面「自带件不全」补 |
| 精度比对器 | 往 `ACCURACY_REGISTRY` 注册 | 可缺。缺了用 ATK 内置阈值 |

## 采纳做了什么

| 做 | 不做 |
| --- | --- |
| 跑自带件的适配脚本，取它的产物当 `cases.json` | 自己实现适配逻辑。适配改的是 `api_type` 与 `standard` 的形状，那是任务方对自己用例的解释 |
| 自带件整棵树原样搬进 `kit/` | 拆平。原件里有 `parents[1] / "common"` 这类相对引用，拆平就断 |
| 生成 `function_<op>.py` 做 import 入口 | 把算子语义抄进这个入口。CPU 标杆与比对判据留在 `kit/` 的原件里 |
| 四个被采纳文件的 sha256 进 `facts.json` 的 `adopted_files` | 依赖任务仓的版本号。自带件在任务仓里，本仓管不到它的版本 |
| 用 `--band-attr` 指定的参数按四分位切档位 | 沿用 `case_shape` 的字节门槛。那个门槛建立在张量上，纯 attr 用例没有张量 |
| 用 `--group-attr` 与 `--group-labels` 定精度表的分组轴 | 退回按 dtype 分。纯 attr 用例取不到 dtype，整张表塌成一行 `?`，而「整类失败」正是靠分组发现的 |

采纳完仍走 S4 的 `freeze_golden.py`。**自带件是当场算 CPU 标杆的，用例包要冻好的
golden**——不冻的话跑测侧得开第二条代码路径，而且复现包里的期望输出会随
torch 版本漂。

### 分组轴的选取

挑**整类会一起失败的那个轴**，与 aclnn 剖面按 dtype 分组同理：kernel 不支持
某个 dtype 时那一档整档挂，一眼可见。稀疏算子的对应轴是稀疏格式
（`--group-attr format_id --group-labels csr,csc,coo,blocked_ell`）——
每种格式的提取路径是各写各的，一种写错不影响另外三种。

取值到名字的对应**在任务书里**，不要从执行器插件的元组里抄：那份是实现，
抄它等于拿被测代码给自己的报告定义标签。

`group_attr` 还有第二个消费者：跑测侧 A2.5 的冒烟按它每档抽一条。
不填的话纯 attr 用例的冒烟塌成一条，**拦不住「某一类整类跑不起来」**——
稀疏算子上那意味着 blocked_ell 全挂要跑完 200 条才发现，而不是 4 条。

## 自带件不全

三种情形分别补什么。**缺执行器插件是硬缺口**，其余都能降级跑。

| 缺什么 | 补法 | 落在哪 |
| --- | --- | --- |
| 执行器插件 | 照 [plugin-authoring.md](plugin-authoring.md)「npu 剖面的待测钩子」写一份，同文件里带 CPU 标杆 | `<任务目录>/function_<op>.py`，再重跑 `adopt_kit.py` |
| 用例清单 | 走 S2/S3：写 `<op>.yaml` 把参数全部声明成 `attr`，`atk case` 当组合发生器用 | 正常的 S2/S3 产物 |
| 精度比对器 | 不补。`standard.acc` 留 `default` 走 ATK 内置阈值 | — |
| 性能件 | 不补。npu 剖面的性能走 ATK 的 `performance_device`，与 aclnn 剖面同一条命令 | — |

自带件**有问题**时（用例的 `api_type` 与执行器注册名对不上、适配脚本报错）
`adopt_kit.py` 退 4 或 2 并打印对不上的两个名字。

修是可以修的，纪律是**留痕**：改完 `adopted_files` 的指纹与任务仓那份不同，
不说的话验收结论追不回来源。所以每改一处，往 `facts.json` 的 `kit_fixes`
数组里加一行，写清改了哪个文件的哪一处、为什么：

```json
"kit_fixes": [
  "adapt_accuracy_cases.py：outputs 由 dict 转 int 时漏了 base 1 的分支，本轮补上"
]
```

跑测侧的 `make_repro.py` 读它，把这几行原样写进复现包的 README，
连同 `kit/MANIFEST.md` 的指纹清单一起交给复测的人。

## 与跑测侧的两项衔接

| 字段 | 跑测侧谁在读 | 不写会怎样 |
| --- | --- | --- |
| `facts.json` 的 `adopted_files` | `make_repro.py` 生成 `kit/MANIFEST.md` | 复现包里没有指纹清单，复测的人分不清手上这份与验收那轮是不是同一个 |
| `facts.json` 的 `kit_fixes` | `make_repro.py` 写进复现包 README | 改动过的自带件看起来像原件，结论追不回来源 |

`kit/` 这棵树本身也跟着用例包走：跑测侧 A1 的 `cp -r` 无差别全搬，
`make_repro.py` 再从那里拷进复现包。**改没改过都拷**——任务书写明的交付门槛
就是那套脚本，复测的人手上没有它，只能自己去任务仓找，找到的未必是这一版。

## 范围之外

- **不为自带件重写性能跑测脚本。** 要和任务方交付的 GPU 基线表比数时，
  口径必须是同一把尺子，那就跑任务方自己的 benchmark 脚本，
  结论与 ATK 那轮**并列进报告，不合并**
- **不判 CPU 标杆算得对不对。** 自带件的标杆是手写实现，本链路只证「待测实现
  与它一致」。标杆本身有疑问时写进报告的推断项，不改标杆
