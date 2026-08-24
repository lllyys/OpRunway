# ATK 验收 Skill 架构演进记录

每次架构级修改在这里留一条：改了什么、为什么、测试增量。
逐条修复清单按复盘轮次分组，问题原文与修法一一对应。

CLAUDE.md 只留当前状态与索引——它每次会话都进上下文，
流水账在那里读的人不需要、付费的人是每一轮验收。

## S11：平级双目录（2026-08-24）

验收流程从嵌套源加展开产物改成

- `repo-task-case-gen/`
- `repo-task-atk-accept/`

两个平级、自足目录。这样目录安装、软链安装与 plugin manifest 都指向同一份文件树，
开发态和发布态不再有两种结构；回上游的改动也明确成为一次结构提案，而不是只在下游
维护安装兼容层。

代价是约 40 个共用件两侧各存一份，上游单树的改动也不能整包套用：同步时必须按骨架
归属映射，标为 `shared` 的脚本与 reference 两边都改。锁法沿用
`test_shared_facts_sync` 的思路并推广为仓级 `test_shared_sync.py`，同时核共同路径字节一致、
骨架共用件两侧存在和骨架本身一致；失败信息给出对应的复制命令。

## 演进条目

- **2026-08-20 子 skill 拆分:** 父入口改为路由，嵌套 `repo-task-case-gen` 与
  `repo-task-atk-accept`；交接包由封印门写摘要、接收门重算，生成侧只读任务书。
  Task 1–9 新增 99 个测试方法；按计划基线统计，全量净增 95 个已收集测试。
- **2026-08-15 Contract Spine (Plan B):** 建立 `artifact-contracts.json` 骨架
- **2026-08-16 Gate Simplification (Plan A):** 门禁精简（删3合1）
- **2026-08-16 Defect Closure (Plan C):** 9条缺陷修复，+62测试
- **2026-08-16 roll 复盘修复:** 从 roll 真机验收会话反查 10 类问题，+34测试
- **2026-08-16 median 复盘修复:** 签名出处白名单 + 契约一致性门禁 + 判据真值分级 + 术语表，+25测试
- **2026-08-17 分面与分母修复:** 比较器按 dtype 选路实测入知识层 + 轴取值词表钉死 + dtype 轴与工程声明双向绑定 + 分面判据收敛为「装不装得进一份 YAML」 + 自造抽象词门禁，+26测试
- **2026-08-17 内置实现当真值:** 拿 CANN 内置同名 aclnn 当精度真值这条路全线打通，+110测试
- **2026-08-17 上下文开销修复:** 作战卡不再贴进 SKILL.md（mark_step 已原样重打一遍）+ 压缩后按 `probe_progress.py` 从盘上产物反推进度 + 检查点清单从必读全文降为 `gate_lookup.py` 按量具查，+36测试（18 条突变验证过）
- **2026-08-17 签名自检与验收范围:** 从 median 三轮真机会话反查，ATK 签名自检的装机目录搜索入知识层 + 静默改写调用判 S3 不过 + 验收范围只由精度性能目标划定，+6测试

### 拆分实施清单

测试增量按相对拆分前代码新增的测试方法计；Task 1–9 合计 99 个，Task 10 再加 1 个。

| Task | 改了什么 | 测试增量 |
| --- | --- | --- |
| 1 | 骨架新增 S0、阶段归属与交接规范 | +5 |
| 2 | 任务书解析、同步薄封装与任务书指纹 | +16 |
| 3 | 签名对齐新增任务书模式 | +9 |
| 4 | dtype 来源接受任务书并核摘要 | +11 |
| 5 | `seal_bundle.py` 封印交接包 | +14 |
| 6 | `check_bundle.py` 接收与接口一致性门 | +27 |
| 7 | 接线改写同步封印清单 | +5 |
| 8 | 作战卡与进度按子 skill 过滤 | +5 |
| 9 | 父路由与两个子 `SKILL.md` | +7 |
| 10 | 开发文档、使用者文档与下游注册 | +1 |

**roll 复盘修复清单：**

| 问题 | 修法 |
|---|---|
| md 表 dtype 列与能力域 JSON 漂移（aclIntArray 写了 int64_t） | 改正 + `test_reference_facts.py` 逐格核对 |
| 算子类别表与 `OPERATOR_CLASSES` 漂移（movement 多了 contiguity） | 同上 |
| attr dtype、显式 range 要跑完 atk case / 冻结才报 | 前移到 `make_must_cover.py` 声明期 |
| S4「性能状态非空」无量具，agent 自造产物 | `verdict.py` 推导 `performance` 键，精度通过无性能产物即拒绝裁决 |
| 无基线被读成「不采集性能」 | `performance.md` 与 `atk-cli.md` 对齐：照跑，落绝对耗时 |
| 装多份 CANN 时选哪份没有出口 | `probe_env.py --cann-root` + 探测时代为加载 |
| 裸 `python` 跑错解释器 | `env.sh` 前置 `selected_python` 目录到 PATH |
| ATK 缺依赖只在跑测时才炸 | S0 增 CLI 真跑 + 缺包检测；`freeze_inputs.py` 吐出子进程报错 |
| `aclnn_name` 在两个门禁里语义不同 | `align_signatures.py` 复用 `_symbol_prefix` |
| `function_<op>.py` 无条件必需，实际是条件产物 | 骨架加 `condition`，由签名对齐判定 |

**median 复盘修复清单：**

| 问题 | 修法 |
|---|---|
| S2 去 CANN 装机目录抓了同名接口头文件，冻结了官方签名（4参无dim），S3 才炸 | `align_signatures.py` 加 `require_project_source` 白名单，出处必须在 `operator_project.path` 下 |
| `--signature` 手抄声明完全绕过出处核对 | 必须同时给 `--signature-source`，走同一道白名单且文件须真实存在 |
| `--env` 可选，装在别处的 CANN 静默漏检 | `--env` 改必填；`env.json` 无 `operator_project` 直接拒绝 |
| 全仓没有 `align_signatures.py` 的完整调用示例 | `plugin-authoring.md` 补「签名只有一个合法出处」小节 |
| 红线1「不读算子源码」被读成「不能碰工程」，逼 agent 去装机目录找签名 | 改写成可读/不可读两栏，接口声明明确可读 |
| 「候选符号」等术语用户读起来不友好 | 全局统一为「待验收算子」 |
| aclnn 侧入参集合与顺序全靠 agent 从对齐报告手抄，无量具核对 | 新增 `check_signature_contract.py`：缺参数/乱顺序/多参数三判，S2 出口门禁 |
| S2 反复真因：torch 替身漏 `keepdim`，门禁拿不全的名单硬拒合法参数，逼 agent 中途改量具 | `baseline_parameter_names` 返回名单来源；只来自替身时降级为「判不了」，出口是 `--baseline-names` 补名单而非改量具 |
| 运行期 skill 零术语表，分面/物化/接线字段等高频词无定义 | 新增 `references/glossary.md` + 高频术语必须有定义的防漂移测试 |

**分面与分母修复清单：**

| 问题 | 修法 |
|---|---|
| 整型精度判据要现场读 ATK 源码：文档只写「整型逐元素相等」，没写声明混合容差会怎样 | `probe_atk_capabilities.py` 加差一探针，实测结果进 `atk-parameter-capabilities.json`；`experimental_standard.md` 新增「选哪个比较器」判定表 |
| int8 输出声明 `mixed_tolerance_bm` 走量化标准，容忍 ±1，选择类算子的错值被放过 | 判定表点名 int8 必须单独走 `equal`；防漂移测试锁住「只有 int8 不安全」 |
| 「一份用例集只声明一种比较器」被读成「浮点整型要拆分面」，同一算子每轮拆出不同数量的分面 | ATK 实测按每个输出张量的 dtype 分别选路；分面定义统一为「只按接口拆」，`SKILL.md` / `case-design.md` / `glossary.md` / 骨架四处对齐 |
| `dims` 是覆盖率分母却全由 agent 手写，门禁拿它核对 combos 属自证；median 七次验收 rank 写过 `[1..8]`/`[1,2,3,5]`/`[1,2,3]`，用例数 76~500 不等，覆盖率次次 100% | `_axis_binding.PINNED_AXIS_VALUES` 钉死 rank/size_class/shape_form/axis_pos，取满或整根 `n/a`，顺序也算 |
| dtype 轴凭空写：任务书只写「支持所有走入 aicore 的数据类型」，列不出名字 | `make_must_cover.py` 新增 `--dtype-source`，复用 `require_project_source` 白名单，取值必须在工程 README/头文件里按词边界找得到 |
| 只查「声明的找不找得到」挡不住漏写：8 种写成 5 种照样过门禁，漏掉的没进验收 | 反向再扫一遍出处，未声明的 dtype 报出来；属性/输出的类型名与分面拆分造成的缺口走 `dtype_source_excludes`，每条附 why |

| 分面判据是三条要记的清单，会漂；真机上同一算子拆过 1/2/4 份 | 收敛成一条能当场试的：这批用例装不装得进一份 YAML。装不下时必须说出是哪个字段，写进 evidence/constraints.md |
| 文档里「出处」「候选符号」「投影」这类自造缩写，读的人得先猜 | `test_plain_language.py` 门禁 + `glossary.md#怎么写才算说清了` 给出替换写法；中文说清楚优先，具体名字进括号并注明是什么 |

**签名自检与验收范围修复清单：**

| 问题 | 修法 |
|---|---|
| ATK 的签名自检按 `aclnn_name` 在磁盘上 `grep -r --include=*.h` 取第一个匹配；选目录时 `ATK_CUSTOM_OPP_PATH` → `ASCEND_CUSTOM_OPP_PATH` → `ASCEND_OPP_PATH` 逐个覆盖且无 break，最后一个由 CANN `set_env.sh` 设置、必然存在，目录落到装机根 → 命中官方同名算子的头文件。社区算子基本都与官方重名，必踩。median 三轮验收（08-14 / 08-15 / 08-16）各从零重推一遍，每次 30–45 分钟 | `build-deploy.md` 新增「签名自检搜的是磁盘上的同名头文件」：说清机制、日志长什么样、软链接锁定写法；`probe_env.py` 加 `link_custom_opp()`，`--custom-opp` 自动在 `evidence/atk_custom/` 建软链接并让 `ATK_CUSTOM_OPP_PATH` 指过去，默认流程踩不到 |
| 搜错了 ATK 不停：`ret==1` 丢入参并把第一个入参当输出、`ret==2` 调换入参顺序，只打 warning 继续跑。这次 7 参对 4 参差太远才抛异常暴露；差一个参数就会一路绿灯，报告显示精度 100% 而测的不是工程声明的签名 | `_opapi_binding.py` 认 `参数数量不匹配` / `参数类型不匹配` 为 `BindingError`，`check_opapi_binding.py --atk-log` 退出码 2，S3 不放行；报错文本直接指向 build-deploy.md 那一节 |
| 任务书提了 `aclnnMedianDim`（工程根本没这个接口名）、设计文档写了「`indicesOut` 传空是全局中位数」，agent 就把两种形态都拆成分面，一轮 4 份 YAML、141 条用例、4 轮冻结与冒烟，而任务书的精度性能目标一条也没多覆盖 | 验收范围只由精度性能目标划定：语义形态默认不构造、不进必测集，用户明确要求了才测并记进 `constraints.md`。落 `SKILL.md` 工作边界 + `intake.md` 约束表「语义形态」一行 + `case-design.md` 分面判据前置一问 + `glossary.md` 新词条 |
| 红线 1 只管「agent 读什么」，管不到「ATK 运行时搜什么」，全仓无一处提及 `cpp_func_signature_check` | 按红线 2 不改 `atk/`，只从环境侧锁定并记为已知问题；防漂移测试锁住这两条知识不被删 |

**当前状态：** 629 passed, 13 skipped（另 20 条依赖 torch，本机未安装故失败）

**最新补充（2026-08-17）：** inf 判定语义入知识层

从 ATK 源码分析得出 inf 判定规则，补充到 `experimental_standard.md` 与 `atk-parameter-capabilities.json`：
- 基线包含 inf → 直接通过
- 待测包含 inf 但基线没有 → 失败
- 两侧都包含 inf → 通过（不检查符号与位置是否一致）
- 溢出到 inf 的场景需在 `evidence/constraints.md` 记录预期行为

---

**内置真值这条路的清单：**

| 问题 | 修法 |
|---|---|
| 社区算子与 CANN 内置基本都同名，ATK 按算子名逐级搜 `.so`，搜错就是两轮跑同一份实现、自己跟自己比，报告 100% 通过而什么都没验 | `resolve_opp_library.py` 自己按 ATK 的候选清单解析并钉进 `ATK_CUSTOM_OPP_PATH`（第一级路径存在即用、不校验函数在不在里面），搜索不再参与决策 |
| 真值目录怎么摆、`accuracy_load` 怎么读，全靠现场翻 ATK 源码 | `references/builtin-baseline.md` 落盘四段路径规则、两步跑测、三件取证、已知会踩的四种写法 |
| 「前面全绿」说明不了任何事：load 节点没生效或候选跟自己比，结果同样 100% 通过 | 反证实验（`capture_reference.py --tamper`）：故意改坏一条真值，那条必须变 Fail；没做过 `verdict.py` 拒绝裁决 |
| 取证只核第一轮，第二轮加载了谁全链路没人核——忘 `source env.sh` 时两份指纹都诚实、反证实验照过、门禁放行 | `check_golden_source.py --candidate-log` 必填，核第二轮日志的加载路径等于钉死的 candidate |
| 指纹是能手改的 JSON，只比 `side` 或 `path` 挡不住换库 | 门禁当场重算盘上 `.so` 的 sha256，两轮 sha256 相同即报；路径比对统一走 `realpath`（本仓 `link_custom_opp()` 造的就是软链接） |
| 「浮点位级相等在 NPU 上不成立」这条判据的前提是跨后端比框架基线 | 判据按 `baseline_kind` 分叉：内置真值是同后端回归比对，整份声明 `equal`，浮点也一样 |
| 随机判据是自由文本，「合理的固定种子策略」读起来像判据却没一步能执行 | 收敛成四个受控取值；带种子那两档强制 `--seed-parameters`，名单进 `interface.json` 并喂给 C7 |
| 种子不钉死，冒烟与全量、复现、逐位比对三件事同时失效 | `validate_cases.py` C7 从用例数据推导：同一种子参数全用例只能一个取值，且不能是区间 |
| 拿内置当真值下「精度达标」的结论过强——内置本身没被这轮验收检验过 | `verdict.py` 输出 `conclusion_kind`，`regression_vs_builtin` 只能写「与内置逐位一致」 |

**最后更新：** 2026-08-17（内置真值验收路径完成，待真机验证）

---

**上下文开销修复清单（2026-08-17）：**

| 问题 | 修法 |
|---|---|
| 五张作战卡同时贴在 SKILL.md 里，而 `mark_step.py` 进阶段时又原样打一遍；SKILL.md 每次调用都进上下文，其中四张卡与手上这一步无关 | 卡只留渲染这一条路：SKILL.md 删掉 §阶段作战卡（374 → 289 行），锁 L2 从「SKILL.md 文本等于渲染结果」改成「mark_step 在阶段入口真的把卡送到眼前」+「卡不许贴回 SKILL.md」 |
| 一轮验收几十次工具调用，中途必被压缩；压缩后 SKILL.md 与读过的 reference 都不在了，agent 要么重读一遍（文档开销翻倍）要么凭摘要往下写（判据全靠记忆） | 新增 `probe_progress.py`：只看产物在不在，从盘上反推当前阶段、列出缺哪几件、重打当阶段卡；条件产物的条件从 `interface.json` / `signature_alignment.json` 读，读不出来报「条件未定」而不是报缺 |
| `gate-inventory.md` 3.8K token 被 SKILL.md 定为 S2 前必读全文，而 19 道门全在 S2、写一件产物真正相关的只有一两道 | 新增 `gate_lookup.py`，按卡上的量具名或门名查（约 0.9K token/次）；`_contracts.render_gate()` 抽出来给清单全文和单条查询共用，两种写法不会各自漂 |
| `artifact-contracts.json` 是 references/ 里最大的一份（12K token），运行期只由量具程序化读取，但 CLAUDE.md 反复称它「唯一真相来源」，卡住的 agent 很可能去打开它 | SKILL.md §参考和脚本规则 写明验收时不要打开它，并说清里面的话已经由卡和 `gate_lookup.py` 按需送到 |
| CLAUDE.md 19KB 每次会话自动加载，其中 §8 架构演进 2.6K token 是开发流水账，对跑验收的 agent 零价值 | 流水账移到本文件，CLAUDE.md §8 只留当前状态与索引（19.4KB → 8.7KB） |
| 卡从 SKILL.md 移走后，送达取决于 agent 记不记得跑 `mark_step.py`，而没有任何门禁依赖 `timeline.jsonl`——忘了打卡不会有任何东西红 | `_stage_card.announce()` 接进 18 个单阶段量具：跑到本阶段量具而该阶段没打过卡，就补记一笔并把卡打到 stderr。补记使同阶段只打一次，上限等于旧版本无条件付的那 2367 tok；不读产物、不改退出码、异常全吞 |
| 补卡可能打错阶段：`probe_env.py` 记在 S1，而 S3 装完包要带 `--vendor-env` 重跑一次 | 判定是「本阶段没打卡**且**没有更靠后的阶段打过卡」，跑到 S3 就不再回头补 S1 的卡；跨阶段的量具由 `stage_of()` 返回 None 直接弃权，不猜 |
| 补卡的调用点在 `parse_args()` 之前：命令行参数写错时也会打一张卡，并把阶段标记成已进入，而那次调用什么都没干 | 调用点移到 `parse_args()` 之后（18 处统一按 `args = xxx.parse_args()` 定位）；`--help` 的特判保留作二道防线 |
| 「验收时不要打开 `artifact-contracts.json`」写过头：产物依赖链（`consumed_by`）全仓只有骨架一处有，等于把唯一出口也堵了 | 改成「不必打开；要查依赖链再打开」，并点名 `consumed_by` |
| `probe_progress.py` 只答「产物在不在」，返工某阶段时上一轮的产物同样算齐，会把人推到下一阶段 | 输出末尾写明它不答「对不对」，返工时以自己的判断为准 |
| SKILL.md 写死了「检查点有 19 道，全在 S2」，没有任何测试锁住；给别的阶段加一道门，这句话会静默说谎 | `test_gate_premises.py` 新增一条：门的阶段集合必须仍是 `{S2}`，条数必须与骨架一致 |
| 三项改动里只有「清单全文降级成按需查」使鲁棒性变弱：依赖从「读一份文档一次」变成「在正确时机发起 N 次查询」，且没有工具侧兜底，不查就退回被门逐个拦下 | 不回退，改走与卡同一条送达通道：该阶段的门索引（门名 + 检查条数 + 有无前提）并进 `render_card`，随卡必然送达；检查什么、前提是什么仍按门名查。S2 卡 811 → 1041 token，仍远低于通读全文的 3802 |
