# ATK 上游文档（逐字副本）

本目录是 ATK 上游文档的**逐字副本**，不是转述，也不是提炼。逐字的理由是可比对：副本能和上游 diff，
提炼版不能。

## 同级的三份派生事实

本目录（`atk/`）只放上游文档的逐字副本。与本目录平级的三份是**从源码读出来的事实**，不是文档的转述：

| 文件 | 内容 |
|---|---|
| `../atk-source-facts.md` | 十条会影响判定的 ATK 实际行为：阈值键写入不生效、标杆含 nan/inf 时直接判过、出参 buffer 按标杆分配、异常时退出码仍为 0 等 |
| `../atk-interface-facts.md` | 调用侧与产物侧的字面量：子命令与开关的取值范围和默认值、设计文件字段、工作簿表名与列名、日志命中模式、profiler 落盘文件 |
| `../build-layout-facts.md` | 被测仓族的构建脚本形态、安装包行为、以及安装树的交付布局 |

三份都绑定具体版本，开头写明失效条件。**与本目录的关系是互补不是覆盖**：副本说「文档怎么说」，
它们说「代码实际怎么做」，两者不一致时以源码事实为准，并在上面的勘误表登记。

## 为什么要副本

ATK 的文档**不随安装分发**。上游仓库有 `docs/` 与 `skill/`，但 `MANIFEST.in` 只包含
`requirements.txt`，`setup.py` 用 `packages=find_packages()`，因此 `site-packages/atk` 下没有任何
markdown。`docs/` 与 `skill/` 不属于任何 package，`MANIFEST.in` 也没有 include 它们，因此 sdist 同样
不含——**只有 git 仓库副本才有**。`pip install` 的环境一篇都没有。

没有这份副本时，agent 只能读 ATK 源码反推 design 文件字段、自定义生成器与执行方式的写法。实测一轮验收里
这类源码阅读占了工具输出总量的 57%（49 次调用、约 20 万字符），而其中大部分内容这些文档里本来就有。

## 绑定的上游版本

| | |
|---|---|
| ATK 仓 commit | `7220f27e83740cd43b300ed2d4362e12d6cc0d42` |
| 对应 `atk --version` | `26.5.14` |

| 文件 | SHA-256 | 上游路径 |
|---|---|---|
| `atk_user_guide.md` | `10ff08ee103e1bffa99696ee526a39d3a1b515f7b5b92948f4d205360382a79b` | `skill/atk-quality-guard/references/atk_user_guide.md` |
| `用例设计文件说明.md` | `d2326d60bf907f8db8562f2de055a05a884f2cd253a44ff07cd39d72c8536e55` | `docs/ATK使用指南/02 参考资料/用例设计文件说明.md` |
| `任务执行参数说明.md` | `f41e8c45a5a3f6351ece372b1c3dbe75a67c48fca0865e780ffa985400724b61` | `docs/ATK使用指南/02 参考资料/任务执行参数说明.md` |
| `自定义参数约束.md` | `a738afb1e436a1f81f757321d271a1d3f78691d48fa5bf25483bde6d3d26d5b8` | `docs/ATK使用指南/03 扩展开发/自定义参数约束.md` |
| `自定义执行方式.md` | `c2caeb444bfc5a88b9e79f46d7fd39df5b503984f38933bd90e605bdca90b865` | `docs/ATK使用指南/03 扩展开发/自定义执行方式.md` |

## 什么时候不能信这份副本

**本轮实测到的 ATK 版本与上表不一致时，这些文件一律降级为提示，不得当作事实依据。** 版本在步骤 6 由
`atk --version` 探得，并记入 `receipts/cases.json` 的 `atk.version`，因此这个判断是有据可查的，不靠印象。

版本不一致时的做法：回到读 ATK 源码，把结论连同实测版本写进本轮记录；不要就地修改本目录的副本去迎合新
版本——那会让副本既不是上游的，也不是任何一版的。

更新副本是一次独立动作：重新逐字复制、更新上表的 commit 与 SHA-256、说明变了什么。

## 这里没有的东西

**逐 dtype 的精度阈值。** `精度标准说明.md` 只讲如何在用例设计文件里配置 `standard.acc`，并明确写着
「具体阈值和实验标准更新以该文档为准」。阈值属于任务书引用的外部标准，不在 ATK 文档范围内，也因此
没有收进本目录。

**`standard.acc` 的 dict 写法。** 五份副本里 `standard.acc` 只出现字符串形态。SKILL 步骤 3 要求引用生态
混合容差标准时用 `mixed_tolerance_bm` 并把任务书阈值显式写入该对象，那是 dict 形态，属于 ATK 侧机制而非
外部标准，副本未覆盖，需要时仍须读 ATK 源码。

## 许可与署名

Copyright (c) 2025 Huawei Technologies Co.,Ltd.

- `atk_user_guide.md` 来自上游 `skill/` 目录，随 ATK 产品代码采用**木兰宽松许可证第 2 版**，
  许可证全文见 <http://license.coscl.org.cn/MulanPSL2>。
- 其余四份来自上游 `docs/` 目录，采用 **CC BY 4.0**，许可证全文见
  <https://creativecommons.org/licenses/by/4.0/>。

五份均为逐字复制，未作任何修改；本目录中所有针对本仓的说明、索引与勘误都写在本 README 里，不写进
副本。
著作权归上游作者所有。

## 读之前必看：两处会直接坑人的错误

副本逐字保留上游原文，因此上游的错误也原样带进来了。下面两处对着 ATK 26.5.14 源码核过，照做必错：

| 位置 | 副本怎么写 | 26.5.14 实际 |
|---|---|---|
| `atk_user_guide.md:24` | `python -c "import atk; print(atk.__version__)"` | `atk/__init__.py` 只定义 `PACKAGE_VERSION`，没有 `__version__`，该命令抛 `AttributeError`。**正确探测是 `atk --version`。** 照抄会把装好的 ATK 误判成未安装，进而走上被本仓禁止的「降级模式」 |
| `自定义执行方式.md:143` 起 | ACLNN 自定义执行器示例继承 `BaseApi` | `PyAclnnBackend` 以两个位置参数实例化执行器，只有 `AclnnBaseApi.__init__(task_result, backend)` 接得住，`BaseApi` 版本直接 `TypeError`。**ACLNN 执行插件一律以 `atk_user_guide.md` 第 338 行起的 §8 为准**，那里继承的才是 `AclnnBaseApi` |

## 本仓适用性

这些文档是为 **ATK 自己的工作流**写的。那个工作流允许若干本仓明令禁止的做法，照抄会直接违规：

| 副本位置 | 上游教的做法 | 本仓规则 |
|---|---|---|
| `atk_user_guide.md:17-43` | ATK 不可用时「降级」，只生成产物再移交别的环境执行 | SKILL 步骤 1/6：停 `BLOCKED`，不降级；AGENTS.md §3：caseset 与执行须在同一 fresh session 内哈希绑定 |
| `atk_user_guide.md:399-471`、`590-601` | 手工依次跑 `atk case` / `atk aclnn`，示例还带 `-s 0 -e 1` 只跑一条 | SKILL 步骤 8：不得手工拼子命令绕过正式入口；AGENTS.md §3：完整分母 |
| `atk_user_guide.md:508` | 「`success 1, failed 0` 即可」 | AGENTS.md §3：ATK 的返回码与 task success 文字不能单独作为成功依据 |
| `atk_user_guide.md:603-622` | 看 ATK 报告表格与 `atk.log` 自行判断是否通过 | AGENTS.md §4 与 SKILL 步骤 9：终态由本流程的判据产出；步骤 10 只逐字引用，不重判 |
| `atk_user_guide.md:559-587` | 装 `sitecustomize.py` 注入解释器 hook | AGENTS.md §2：绝不安装依赖、修改系统 Python 或 shell rc |
| `自定义执行方式.md:66` | 执行器模板里的 `elif self.device == "gpu":` 分支 | AGENTS.md §1：不连接、不运行、不采集、不消费 GPU 数据。**照抄模板时删掉该分支** |
| `任务执行参数说明.md:30`、`50`、`66-68`、`79` | `--compare-backend npu`、`accuracy_load`、`--white_list`/`--black_list`/`--random`、`--input-data` | 分别违反「由 CPU 真值裁决」「同轮取证」「完整分母」「不复用旧产物」。这些参数由 CLI 控制，agent 不应自行使用 |
| `atk_user_guide.md:50-57` | 目录模板把 ATK 框架源码与 `result/` 放进算子工作目录 | 本仓 ATK 安装共享只读，session 目录由 CLI 创建 |

另外，`atk_user_guide.md` 里的「本 Skill」「Phase A/B」指的是上游 `atk-quality-guard` 那个 skill 的阶段
划分，与本 workflow 无关，不要误读成本 skill 的步骤。

## 副本自身与 26.5.14 不符之处

均对着上游源码核过。副本不改，在此列出：

| 位置 | 副本 | 实际 |
|---|---|---|
| `用例设计文件说明.md:25` | `size_distributions` | 应为 `shape_distributions`。`DesignConfig` 是 `extra='forbid'`，写错直接 ValidationError |
| `用例设计文件说明.md:20` | `compute_times` | 该字段不存在于 `DesignConfig` 与 `CaseConfig`，只是报表列名 |
| `用例设计文件说明.md:71` | attr 的 dtype 写 `bool` | 应为 `attr_bool`；`atk_user_guide.md:137` 那份是对的 |
| `atk_user_guide.md:155` | `max_length` 默认 17179869184 | 实际 4294967296（2³²） |
| `atk_user_guide.md:154` | `dim_numbers` 默认 `[1, 2]` | 实际 `[1,2,3,4,5,6,7,8]`。「按算子实际维度写小」这个建议本身对，但默认值说反了 |
| `atk_user_guide.md:109` | `sdtype_numbers` | 笔误，应为 `dtype_numbers` |
| `atk_user_guide.md:448` | 同目录只有一个 `execute_*.py` 时可省略 `-p` | 自动加载匹配的是 `function_*.py`。本仓 CLI 总是显式传 `--plugin`，不影响正式路径，但会误导排错 |
| `自定义执行方式.md:167-181` | 示例用 `torch.ones` 但该代码块未 import torch | 照抄即 `NameError` |
| `任务执行参数说明.md:53-55` | 任务类型表列出 `performance_device_pta`、`memory_device`、`performance_camodel` | 26.5.14 的 `TaskType` 只有 `accuracy`、`performance_e2e`、`performance_device`、`accuracy_load`、`accuracy_dc`、`run` 六项（`atk/configs/base_config.py:50-57`）；这三个名字在上游全仓 `*.py` 中零命中，照抄即报错 |
| `任务执行参数说明.md:19` | `atk aclnn` 的默认待测后端写作 `aclnn` | 实际是 `pyaclnn`（`atk/bin/op_alias.py:33`；`NodeType` 只有 `npu`/`cpu`/`pyaclnn`）。该值决定输出目录前缀与工作簿列前缀为 `pyaclnn_0`，照抄会按错误的前缀去找产物 |

`-dt` 在两处含义不同：`atk case` 下是 `--dtype_numbers`，task 与 `atk pytorch|aclnn` 下是
`--db_timeout`。各自都对，拼命令时不要串。

## 相对链接与重复

- **副本内的相对链接一律失效**（共 7 条：`任务执行参数说明.md:5`、`:6`，
  `用例设计文件说明.md:7`、`:168`、`:169`，`自定义参数约束.md:5`，`自定义执行方式.md:5`），它们
  指向上游的 `../01 基础操作/` 等目录，本仓布局下不存在。其中 `用例设计文件说明.md:168`、`:169` 指向的
  两份就在本目录，对应 `自定义参数约束.md` 与 `自定义执行方式.md`。
- `任务执行参数说明.md` 带有上游自身的大段重复：第 5 与第 6 行重复；`98-117` 整块在 `118-137` 重复；
  `141` 与 `142` 重复；`152-154` 在 `155-157` 重复。第 117 行「常见透传参数如下：」之后的表格要到
  139 行才出现，中间被重复内容隔断——那是同一组参数，不是两组。

## `atk_user_guide.md` 内容索引

该文件 630 行，用粗体编号分段而非 markdown 标题，因此没有可跳转的目录。下表是外置索引，不修改副本本身。
行号对应上游 commit `7220f27`；副本更新后需同步重算。

| 行 | 段落 | 对本仓的用法 |
|---:|---|---|
| 17 | ATK 环境检查与降级方案 | **降级部分本仓不适用**；仅检查方法可参考，且需换成 `atk --version` |
| 46 | 1. 最小目录 | 仅供理解上游布局，本仓 session 目录由 CLI 建 |
| 60 | 2. 顶层 YAML 最小字段 | 步骤 3 |
| 112 | 3. `inputs` 最小可靠写法 | 步骤 3 |
| 139 | 4. 最关键的 YAML 细节 | 步骤 3（注意 154、155 两处默认值有误） |
| 189 | 5. `attr` / `attrs` / `attr_tuple` | 步骤 3 |
| 219 | 6. 约束生成器最小接口 | 步骤 5（generator） |
| 259 | 7. 执行器最小接口 | **服务 `atk pytorch`，不是我们用的 `atk aclnn`**；仅作 CPU golden 写法参考 |
| 338 | 8. pyaclnn 最小接口 | 步骤 5（execution plugin）——**ACLNN 执行插件以这一段为准** |
| 399 | 9. 最小命令 | **不得照抄执行**；仅用于理解 CLI 内部行为与排错 |
| 473 | 10. `atk case` 输出位置 | 排错时定位产物 |
| 493 | 11. 最常见的 7 个坑 | 排错，任意步骤 |
| 533 | 12. NPU/pyaclnn 真机执行前最少检查项 | 步骤 6 |
| 559 | 13. `sitecustomize.py` 什么时候需要 | **本仓禁止改 Python 环境**；遇到该类问题停 `BLOCKED` |
| 590 | 14. 最小工作顺序 | 上游流程，与本仓十步不同，勿照搬 |
| 603 | 15. 报告输出解读 | **本仓不看 ATK 自己的判定**；终态只由 finalize 产出。且该段讲 `atk pytorch` 格式 |
| 625 | 16. 这份手册不能替代什么 | 读之前先看这一段 |

其余四份都在 300 行以内且自带 markdown 标题，可直接跳转，不另做索引。
