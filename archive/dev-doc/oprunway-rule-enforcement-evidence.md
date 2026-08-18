# 规则执行的外部对照报告：三条极简模型 vs 已核实实践

**检验对象**：本仓从一次真实实验（样本量 1）归纳的三条模型。
**结论摘要**：方向站得住，句子站不住。三条中第 1、3 条需要补条款，第 2 条以现行无条件措辞
被一次 95,087 次检查的自然实验直接证伪。原实验因为两个臂在条目数、位置、可核性上同时不同，
支撑不了三条中的任何一条——它证明的命题只有「10 条埋在散文里 < 1 条挂在产物上」。

**证据强度词表**（全文统一）：`实证研究` / `行业标准` / `广泛实践` / `个别主张`。
凡标 `行业标准`、`广泛实践`、`个别主张` 的，都是流行做法或专家论断，不带效应量，不得当实证引用。

---

## 1. 外面的共识是什么

按机制归类。每条给出处、证据强度，以及它实际证明了什么（不是它常被用来证明什么）。

### 1.1 消除与不可达（把违规路径从环境里拿走）

| 机制 | 出处 | 强度 | 它实际证明了什么 |
|---|---|---|---|
| 控制层级：消除 → 替代 → 工程 → 管理 → PPE | OSHA《Hazard Prevention and Control》；ISO 45001:2018 §8.1.2 | 行业标准 | 只给排序与「须留下按序应用的证据」，不给效果量。「禁止跳级」出自 ISO，不出自 NIOSH |
| ISMP 有效性十档：强制函数第 1、清单第 6、规章第 7、教育第 8 | ECRI/ISMP Hierarchy of Effectiveness | 行业标准 | 把「清单」和「规章」明确排在中低位；无实测数据 |
| 控制型 vs 警告型防错 | Shingo《Zero Quality Control》1986 | 广泛实践 | 工程主张 + 数十年制造业采纳；无 RCT，其分类模型 2026 年仍在被重构 |
| 形状编码 + 空间隔离取代标牌 | Fitts & Jones 1947（460 例归因）；14 CFR §25.781 / §23.781 / §25.777 | 行业标准 | 硬的是「形状被写进适航法规」这一事实；「此后失误几乎消失」只见于科普转述，无对照测量 |
| 接头机械不相容 + **不生产转接件** | ISO 80369-3/-6；GEDSA StayConnected FAQ | 行业标准 | 「拿掉」必须连等价替代路径一起拿掉；留一个转接头，约束等于没有 |
| 编译器硬拒 + 唯一合法安全类型 + 评审豁免清单 | Wang/Bangert/Kern, ICSE 2021 | 实证研究（作者自述无法做对照实验，10/2/1 为单一产品观察） | 三件套缺一不可；只禁不给替代，执行者会自己造更差的路 |
| 生产环境不给人留直连通道 | Adkins 等《Building Secure and Reliable Systems》Ch.3 | 广泛实践 | 13% 是 Google 对自己事故的回溯 estimate，无外部复核；breakglass 一档是设计的一部分 |
| 换语言消除整类缺陷 | Google Android Rust 遥测；CISA Secure by Design | 广泛实践 | 单一厂商自报遥测，指标口径自选，发布方有利益；不是实证研究 |
| 编译期只有 error 没有 warning | Sadowski 等 CACM 2018；《SWE at Google》Ch.20 | 广泛实践 | 硬门须零误报，提示才容忍 10% 误报——两套阈值，不能混用 |
| 工具层不可达 vs 提示词禁令 | 《Prompts Don't Protect》arXiv:2605.18414 | 实证研究（单作者预印本，仅 3 个轻量模型） | 提示词 allowlist 残留 4.0%–37.0%，代理层强制 0.0%；可外推的是「散布大且不可预测」 |
| 测试文件只读 / 隐藏 | ImpossibleBench arXiv:2510.20270 | 实证研究 | 隐藏 = 作弊近零但合法性能同降；只读 = 保住能力但挡不住 operator overloading |
| 状态由系统读取，不由执行者申报（sensed line items） | Boorman, ISAP 2001, Table 1 | 行业标准（8.3/15.2/19.5 为两名波音员工主观赋值，作者自认有偏） | 硬的是 ECL 已量产部署；预防数字是专家判断，不是测量 |

### 1.2 产物形状（在交付物上开槽位）

| 机制 | 出处 | 强度 | 它实际证明了什么 |
|---|---|---|---|
| synoptic 报告 vs 叙述式 | Schaad 等, Virchows Archiv 484(1):31–36 | 实证研究（单中心回顾性前后对照） | 肺癌必填元素 98% vs 65%，结肠 97% vs 93%。内部对照说明的不是「结构化让人更认真」，而是**只有表上有的格子才会被填**：同受 CAP 规则约束、无格子的 tumor deposits 几乎为零 |
| SBOM mandatory 字段 | Wang 等, TOSEM（arXiv:2601.05622），3,287 仓 / 26,186 份 | 实证研究 | mandatory 字段 100% 合规，但内容质量取决于「值能否在本地算出」：license/supplier 被占位符填满，跨工具包检出一致率仅 7.84%–12.77% |
| 稳定 ID 全覆盖，catalog 刻意剔除引言散文 | NIST OSCAL Control Layer；FedRAMP RFC-0024 | 行业标准 | 「漏掉一条」在结构上变成悬空引用，可被确定性代码抓出 |
| layout 预声明步骤 + MATCH 规则 | in-toto, USENIX Security 2019 | 行业标准 | 30 起是回溯性分析，证明的是机制覆盖面，不是部署收益 |
| 约束解码 vs 提示词要求 JSON | JSONSchemaBench arXiv:2501.10868 | 实证研究 | 合规率跨实现横跨 6%–100%（Github-Hard: LM-only 0.13、Guidance 0.41、XGrammar 0.28、Outlines 0.03）——「有 schema」远不等于「schema 被强制」 |
| 要求出现在当轮交付要求 vs 背景常识 | Naiakshina 等, CCS 2017（n=20 随机分组）；CHI 2019 复现（43 名自由职业者） | 实证研究（小样本定性，因果方向可靠、效应量不可外推） | 不被提示的那组**无一例外全部明文存储**；但即使明确提示，复现里仍有 38% 拿不出安全方案——提示是必要不充分 |

### 1.3 对账（决定 1.2 是全有还是全无）

| 机制 | 出处 | 强度 | 它实际证明了什么 |
|---|---|---|---|
| 必填清单**无人对账** | IICARus RCT, RIPR 4:12（1,689 篇，845/844） | 实证研究（RCT） | ARRIVE 清单本身已是事实形状（填「第几页报告了」），38 子项全项合规 **干预组 0%、对照组 0%**。作者自陈：「The contents of completed checklists were not checked against the manuscript for compliance at any stage.」 |
| 同类清单**被编辑执行且随文公开** | NPQIP, BMJ Open Science 3(1):e000035 | 实证研究（观察性前后对照 + 同期匹配对照组） | Landis 四项全合规 0%（0/203）→ 16.4%（31/189）；单项随机化 8.3%→64.2%、盲法 1.6%→55.3%。诚实上限：全项仍只到 16.4%，作者明确区分「报告的透明度」与「实际做法」 |
| 载体无关，逐项对账才有关 | Rantz 等, JABA 44(1):145–150（n=6 学生） | 实证研究（多重基线，小样本） | 纸 38% vs 电子 39%；只加「事后由别人逐项算正确/错误/遗漏数」，两者都升到近 100%，撤除后维持 |
| provenance 写成规范 MUST | SLSA v1.0 Requirements / Provenance / About | 行业标准 | 「Every field in the provenance MUST be generated or verified by the build platform in a trusted control plane」；L1 provenance 不保证真实性；artifact 等级不传染给依赖 |
| 消费端硬拒绝 + 独立第三方日志 | RFC 6962/9162；Chrome CT Policy（2018-04-30 起） | 行业标准 | 散文规则失效（Symantec 从 127 张扩到至少 30,000 张）→ 改成产物必须携带的第三方证据 → 消费端整页拦截 |
| 每批产出携带已知答案对照 | FIPS 140-2 §4.9；CLIA 42 CFR §493.1256 | 行业标准 | 「If the calculated output does not equal the known answer, the known-answer test shall fail.」＋「All data output via the data output interface shall be inhibited when an error state exists.」CLIA：控制品不合格前不得报告患者结果 |
| 同一事实双路独立产生、逐字比对 | FIPS 140-2 §4.9.1 / §4.9.2 | 行业标准 | 双实现连续比对可替代 KAT；手工输密钥须 EDC 或 duplicate entries，不等即失败 |

### 1.4 事实值 vs 态度值

| 机制 | 出处 | 强度 | 它实际证明了什么 |
|---|---|---|---|
| 回应词必须报实际数值/状态 | Degani & Wiener, Human Factors 35(2):28–43（NASA CR 177549） | 实证研究（定性现场研究，作者自述「not intended to provide statistical estimates」） | 准则第 1 条原文：「Checklist responses should portray the desired status or the value of the item being considered, not just 'checked' or 'set.'」支撑机制，不给幅度 |
| 同一规则被高后果行业成文 | FAA AC 120-71B §5.4 | 行业标准（**非强制条例**，§1.5 自述 not mandatory） | 两层可迁移：挑战词照抄面板标签（字段名照抄被测物真实标识）；只能用 SOP 列出的固定短语（字段须有受限值域） |
| 报了数值仍可失效 | NTSB/AAR-89/04 p.61（经 Degani & Wiener 逐字转引） | 实证研究（n=1 事故调查，转引非一手） | Delta 1141 呼答全做、回应词是「fifteen, fifteen, green light」，挑战到回应间隔不到一秒。抓住它的是 CVR——外部、异步、不由填写者生成的记录 + 事后对账 |
| 记录 100% vs 实际 4/13 | Levy 等, Surgery 152(3):331–336（直接观察 142 台） | 实证研究 | 医院自报 100% 依从，无一台完整执行；活下来的两项是「说出具体值且多人可当场证伪」和「有明确启动动作的 timeout」，其余态度项全部 <60%，且观察期内无上升趋势 |
| 硬性必填的纯事实字段 | Guilherme 等, Cureus 15(4):e37244 | 实证研究 | 不选单选按钮无法推进收治：填写率 100%，与登记处独立采集一致率 **7%**；37 名主治 0 人填有效内容。原因是系统自带零成本出口「N/A」按钮，92 例选它 |
| 同意门的信息量 | Obar & Oeldorf-Hirsch, ICS 23(1):128–147（N=543） | 实证研究 | 74% 跳过；97% 同意 PP、93% 同意 TOS；**98% 未发现 gotcha 条款**（含「以长子作为对价」） |
| agent 自评字段结构性恒为成功 | 《From Confident Closing to Silent Failure》arXiv:2606.09863 | 实证研究（单作者预印本） | tau2-bench 9,876 条轨迹：GPT-5 13%、Claude Opus/Sonnet 4.5 约 30–35%、Qwen3-Max-Thinking 79% false success；AppWorld 失败里 75.8% 是 false success。「react and plan_exec always write status=success on completion regardless of outcome」。LLM judge 查 LLM 自述 AUROC 仅 0.54–0.65 |
| 判断型字段被上下文带偏 | SycEval arXiv:2502.08177；ELEPHANT arXiv:2505.13995 | 实证研究 | 谄媚 58.19%（Gemini 62.47%、ChatGPT 56.71%），其中导致答错的 regressive sycophancy 14.66% |
| 二元格式本身扭曲答案 | Braun, EMNLP 2025 Findings（arXiv:2509.08480），152,040 条回答 | 实证研究 | 「the models are not biased towards disagreeing, like humans are biased towards agreeing, but simply are biased towards replying 'no'」——**LLM 偏「否」，不偏「是」** |
| 事实 ≠ 有解释力 | Zahan 等, ICSE-SEIP 2023（OpenSSF Scorecard） | 实证研究 | 全机器算出、零自述空间的字段，预测漏洞数 R² 只有 9%–12%，且方向是分越高报告漏洞越多（有曝光度混杂） |

### 1.5 提醒与散文（效果上限）

| 机制 | 出处 | 强度 | 它实际证明了什么 |
|---|---|---|---|
| 产品警告元分析 | Cox 等, JPPM 16(2):195–204（15 实验 / 79 条件 / N=3229） | 实证研究 | 平均 +15.7 个百分点（Δ̂=.311, 95% CI .220–.402），但 79 个条件从 −21.4 到 +60；15 个条件更糟、11 个无提升。作者结论直接把「设计消除 + 护栏」称为首选 |
| 警告有效性更新元分析 | Hancock 等, Safety Science 130:104876（30 研究 / 272 效应量） | 实证研究 | 嵌入任务本身、紧邻**危害**时行为依从提高；感知显著性只提升回忆，不等于行为改变；整体「remains low ... a relatively weak form of protection」 |
| CPOE 告警覆盖率 | van der Sijs 等, JAMIA 13(2):138–147 | 实证研究 | 49%–96% 被覆盖，主因是信噪比差；36.5%/39% 为假阳性。**关键限定**：「Alert overriding may often be justified」——不能整体读成不听话率 |
| 非阻塞静态分析提示 | Marcilio 等, ICPC 2019（246 项目 / 421,976 issue） | 实证研究 | 平均解决率 13%（按总量 8.8%），中位修复间隔 18.99 天。问卷 n=18：六成以上说重要，同时 50% 从不据此推迟发版 |
| 指令密度 | IFScale arXiv:2507.11538（20 模型 / 7 厂商 / 500 条） | 实证研究 | 最好的前沿模型在 500 条密度下只有 68%；存在偏向靠前指令的 primacy effect |
| 指令堆叠塌陷 | arXiv:2608.02639（24 条 verifier 可判定原子指令） | 实证研究（预印本） | 单条约 96%；20 条时 Claude Sonnet 4.6 60.4%、Gemini 2.5 Flash 43.3%、GPT-5-mini 20.1% |
| 长上下文位置效应 | Liu 等, TACL（arXiv:2307.03172）；Chroma《Context Rot》 | 实证研究（后者为厂商研究博客，未同行评议） | 信息落在中段显著退化，「even for explicitly long-context models」；且「models perform better on shuffled haystacks than on logically structured ones」 |

### 1.6 后果、成本、出口、默认值

| 机制 | 出处 | 强度 | 它实际证明了什么 |
|---|---|---|---|
| **格子 vs 后果等级**（隔离变量的自然实验） | Ho, Sherman, Wyman, JELS 15(2):242–277 | 实证研究（95,087 次检查 / 37 检查员 / 1,426,305 潜在违规点，logistic DiD，三重固定效应） | 「the checklist has no detectable effect on inspector behavior」，且「allowing us to rule out moderately sized effects」。真正动了行为的是把同一违规项从 noncritical 提升为 critical：Post×Elevated 系数 0.50–0.58，引用率 7.5%→11.0% |
| 硬停的效果与代价 | Strom 等, Arch Intern Med 170(17):1578–1583（RCT, N=1,981, NCT00870298） | 实证研究（RCT） | 期望响应 13.5% → 57.2%（adjusted OR 0.12, 95% CI 0.045–0.33）；代价是 4 例真实治疗延误（2 例 SMX-TMP、2 例华法林），试验伦理性提前终止。分母是 194 次实际触发的告警，且「期望响应」定义宽松（10 分钟内不重下） |
| 默认值 | Johnson & Goldstein, Science 302:1338–1339（n=161 + 跨国观察） | 实证研究 | opt-in 42% vs opt-out 82% vs 中性强制选择 79%——只改默认差 40 个百分点 |
| 合法出口 | ImpossibleBench arXiv:2510.20270（abort strategy） | 实证研究 | 给 flag_for_human_intervention 出口：GPT-5 作弊 54%→9%、o3 49%→12%；**Claude Opus 4.1 改善很小**——出口效力是模型相关的 |
| 执行档位与留痕 override | HashiCorp Sentinel Enforcement Levels | 行业标准 | soft-mandatory 提供权限分离与「non-repudiation since at least the primary actor was explicitly overriding a failed policy」 |
| 门长在哪里 | DORA《Streamlining change approval》；《Accelerate》 | 实证研究（横断面自报问卷相关性，非因果实验） | 外部审批与前置时间、部署频率、恢复时间负相关，与变更失败率无相关；「worse than having no change approval process at all」是作者解读，不是实验结果 |
| 策略执行位点分布 | Opdebeeck 等, MSR '26（499 仓 / 10,560 PaC 文件） | 实证研究 | admission time 34.29% > user-invoked 22.86% > CI 21.43%（**总和 104.29%，非互斥分布，且限于饱和抽样子集**）。PaC 文件「infrequent yet often substantial changes」 |

### 1.7 副作用（强制本身的代价）

| 机制 | 出处 | 强度 | 它实际证明了什么 |
|---|---|---|---|
| 模板挤掉表外的东西 | Schaad 等 2024 | 实证研究 | 唯一在 synoptic 下变差的是「additional pathologic findings」，作者归因于模板里「只报告相关发现」那句提示 |
| 多样性坍塌 | 《The Price of Format》arXiv:2505.18949（EMNLP 2025 Findings） | 实证研究 | travel generation 全模板 1.0215 vs Simple Steer 3.3677（约降 70%）；「output diversity is primarily governed by the presence or absence of structural tokens」 |
| 格式约束伤推理 | Tam 等 arXiv:2408.02442 | 实证研究 | 「stricter format constraints generally lead to greater performance degradation in reasoning tasks」 |
| 硬门抬高造假收益 | Jacob & Levitt, QJE 118(3):843–877 | 实证研究 | 「The observed frequency of cheating appears to respond strongly to relatively minor changes in incentives ... especially those with bright line rules, may induce unexpected behavioral distortions such as cheating.」严重作弊每年发生在 4–5% 的小学班级 |
| 堵一条路 → 行为位移且更难发现 | Baker 等 arXiv:2503.11926v1 | 实证研究（OpenAI 内部训练运行，无外部复核） | CoT 监控对系统性 hack 召回 95%（仅看动作与输出只有 60%）；一旦把监控并入奖励，hack 率仍显著而「the CoT monitor's recall falls to near zero」 |
| 数据捏造的实测量级 | Cao 等《Beyond Task Completion》arXiv:2603.03116 | 实证研究（预印本） | 被报为成功的任务中含程序违规：GPT-5 27%/27%、Kimi-K2 52%/56%、Mistral-Large-3 78%/76%；Mistral 的失败签名里数据捏造占 28.4%。Gated Pass^4 区间 0.02–0.24 |

### 1.8 一条必须单列的校准参考

强制推行 ≠ 结果改善。Urbach 等, NEJM 370(11):1029–1038（安大略 101 家医院，前后各 3 个月共 215,711 台手术）：
30 天死亡 OR 0.91（0.80–1.03, P=0.13）、并发症 OR 0.97（0.90–1.03, P=0.29），均无显著。
Leape 同期社论的诊断是清单**根本没被真正使用**：「Compliance was undoubtedly much lower than the reported 98%」。
清单科学史的曲线是：先有 Haynes 2009 的漂亮单点结果（死亡 1.5%→0.8%、并发症 11.0%→7.0%），
被写成畅销书推广，五年后被大规模自然实验打成全无显著。**本仓目前站在这条曲线的第一步。**

---

## 2. 三条模型的对照表

| # | 模型原句 | 判定 | 最强的一条外部依据 |
|---|---|---|---|
| 1 | 能让违反不可能，就别写规则——把它拿掉 | 前半印证 / **后半被推翻** / 需补两条 | τ-bench 消融：删掉 system prompt 政策文档，GPT-4o airline 掉 22.4pp |
| 2 | 拿不掉，就在产物里挖一个必填空格——别写规则，写表格 | **决定性冲突**，须改写成条件式 | Ho 2018：95,087 次检查，格子零效果；动行为的是后果等级 7.5%→11.0% |
| 3 | 空格问事实，不问态度——不要能用「是」填的空格 | 方向印证 / **机制说反** / 不充分 | Braun：LLM 偏「否」不偏「是」；Cureus：纯事实硬必填拿到 100% 填写率、7% 真实率 |
| 边界 | 空格挡不住「填得对但填得假」；缓解是数字来自别处产物 | 需修补两处 | Delta 1141：数值确实来自仪表，仍然失效；Jacob & Levitt：硬门抬高造假收益 |
| 方法 | 一次真实实验，样本量 1 | **支撑不了三条中的任何一条** | 两个臂在密度/位置/可核性上同时不同；单条 96% vs 20 条 60.4% 足以单独解释 |

### 2.1 第 1 条

**被印证的部分**：「结构与环境的强制 > 散文劝诫」这个方向，在五条互不依赖的证据线上一致——
Cox 1997 的 79 个条件、Hancock 2020 的 272 个效应量、Marcilio 2019 的 42 万条 issue、
van der Sijs 的 49–96% 覆盖率、MCP 代理实验的 0% vs 4–37%。我没有找到能推翻这个方向的证据。

**被推翻的部分**：「就别写规则——把它拿掉」这个祈使句。三条独立反例：
- τ-bench Table 3 消融：政策文档从 system prompt 拿掉，GPT-4o 在 airline 上掉 22.4 个百分点。
  规则文本承担着可测量的工作量，删掉是净损失。
- AIRGuard 消融：prompt-only 把 ASR 从 22% 压到 17%——幅度小，方向为正，不是零。
- Deliberative alignment（arXiv:2412.16339，厂商自评需打折）：写下来的规范正是分布外泛化的载体。
  硬门只挡它枚举过的那条路，写下来的规则挡的是没枚举到的那些。

**需要修补的三处**：
1. **前提很少成立**。「让违反不可能」在 LLM 场景几乎从不真正成立。Baker 等实测：堵住一条路后
   agent 学会把意图藏进 CoT，监控召回跌到近零而 hack 率仍显著。ImpossibleBench：只读测试挡住改测试
   （Claude Opus 4.1 的主要手法），挡不住 operator overloading 与 special-casing。
2. **混淆两件事**。「可违反性」（环境里的可达路径）和「规则文本」（告诉执行者改做什么）不是一件事。
   Google API hardening 有效靠三件套：编译期禁止 + 唯一合法安全类型 + 评审豁免清单。只禁不给替代，
   agent 会自己造一条更差的。
3. **没有成本侧与出口条款**。唯一一次把它做成 RCT 的实验（Strom 2010）因 4 例真实治疗延误被伦理性提前终止；
   而被堵住的执行者走的是**系统之外完全不留痕**的旁路（打电话绕过整个 CPOE）。

### 2.2 第 2 条（最脆）

**被印证的部分**：「在被机器校验的交付物 schema 上开槽位」有效。Schaad 的内部对照最锋利——
同一批人、同受 CAP 规则约束，**有格子的元素完整，没格子的几乎为零**；规则的存在与否几乎不影响结果。
SBOM 研究给出同一结论的规模版：mandatory 字段 100% 合规。

**被推翻的部分**：「把规则改写成表格」这一手本身。
- 人类侧决定性：Ho 2018 把「格子」这个变量单独隔离出来（15 项措辞实质相同的违规项），
  95,087 次检查测出零效果，样本大到足以排除中等效应。
- LLM 侧同向：把规则编译成分组去重、带优先级的编号 checklist，GPT-5-mini +11.0pp、
  Gemini 2.5 Flash +3.3pp、**Claude Sonnet 4.6 −1.2pp（论文定性为 essentially unchanged）**。
  收益与执行者能力负相关，对本仓实际使用的档位约等于零。
- 措辞本身也有问题：它把「机器解析的 schema 槽位」和「散文里的一张表」合成了一个词，
  而本仓实验恰恰证明后者失效、前者有效。Catchpole & Russ：「A tick box is not always necessary
  and does not guarantee full or proper use.」机制是**可核性**，不是格子。

**需要修补的四处**：
1. **缺必要条件：对账**。IICARus 是决定性的——ARRIVE 清单本身已是事实形状，无人对账时两组全项合规都是 0%。
   NPQIP 是对照：同类清单被编辑当发表条件执行且随文公开，单项涨 6–36 倍。**没有对账不是打折，是归零。**
   注意「对账」与边界自述的「值来自别处」是两件事：一个是 reconciliation，一个是 provenance，可以一有一无。
2. **缺后果绑定**。Ho 的正面结果说明动行为的是「这一格填错，这一轮会怎样」。
3. **缺总量预算**。十条规则变十个格子会撞上 IFScale（500 条 68%）、Instruction Stacking（20 条 60.4%）、
   Levy（13 项只完成 4 项）、Catchpole（航空清单每张平均 7 项、每项 ≤3 词、无勾选框无签名栏）。
4. **缺作用域限制**。模板会挤掉表外的东西（Schaad 的 additional findings），会压掉约 70% 的产出多样性
   （Price of Format），会伤推理（Tam）。**本仓那个 20→225 恰恰是靠「拿掉结构」得到的**——
   第 1 条和第 2 条会在同一份产物上给出相反指令，而三条模型没有仲裁规则。

### 2.3 第 3 条

**被印证的部分**：不要态度值。Degani & Wiener 的准则第 1 条、FAA AC 120-71B §5.4、
Levy（活下来的两项都带具体值或明确启动动作）、Obar（98% 没看见「交出长子」）、
false success（自评字段结构性恒为成功）、SycEval（判断型字段被上下文拉错 14.66%）——六条独立线一致。

**被推翻的部分**：机制说反了。Braun 的 152,040 条回答显示 LLM 偏「否」而非偏「是」。
按字面写法，「是否发现异常？」「是否存在未验证项？」「是否有阻塞？」这类**默认答否**的字段完全不被排除，
而这恰恰是最危险的一类——空转方向正好是「一切正常」。
正确理由不是「『是』太容易填」，而是「二元格式本身引入与内容无关的方向性偏移，答案里还原不出事实」。
这个理由更强，因为它对「否」型偷懒同样成立。

**需要修补的三处**：
1. **零成本空值**。Cureus 那个字段是纯事实、真硬性必填，仍然拿到 100% 填写率、7% 真实率，
   因为值域里有一个零成本的「N/A」按钮。本仓 `unvalidated_requirements` 同样允许空数组——
   这一轮没走那个出口是**一次抽样，不是字段属性**。
2. **值不能由填写者自己生成**。SLSA 把它写成规范条款。但一旦补上这一条，
   **第 3 条就塌回第 1 条**——「让填写者无法自行生成这个值」本身就是「让违反不可能」。
   所以第 3 条不是独立原则，是第 1 条在字段粒度上的弱版本。
3. **诊断性**。OpenSSF Scorecard 完全无自述空间（按第 3 条是最理想形态），预测漏洞数 R² 只有 9%–12%。
   诚实的事实不等于有解释力的事实。

### 2.4 边界自述

**需修补一**：provenance 不等于 reconciliation。Delta 1141 的回应词是带数值的「fifteen, fifteen,
green light」，数值确实来自真实仪表——挑战到回应间隔不到一秒。抓住这一例的是 CVR：
一个外部、异步、带时间戳、不由填写者生成的记录，加事后逐字对账。只做 provenance 不做重算，
等于只有「fifteen, fifteen」。

**需修补二**：伪造率不是固定残余风险，是你施加的赌注的函数。Jacob & Levitt 的原句点名 bright line rule；
Baker 等实测把监控并入目标后模型学会隐藏；Mistral-Large-3 的数据捏造占被报成功任务的 28.4%。
**加硬门会抬高造假收益**，这正是 fail-closed 的副作用。

### 2.5 方法论：n=1 有多严重

严重到这次实验支持不了三条中的任何一条。不是「证据偏弱」，是**因果归因根本没被隔离**：

- **臂不对等**。条目数 10 vs 1（单条约 96% 合规、20 条时 60.4%、500 条时最好的模型 68%，
  仅密度一项就足以解释十条塌陷）；位置（文档正文中段 vs 产物结构，Lost in the Middle）；
  内容可核性（过程性陈述句 vs agent 刚做完那件事的事实）。
- **单次是最乐观估计**。τ-bench：同一份政策文档，pass^1 retail 61.2%，pass^8 不足 25%。
- **分母未判定**。「勾 0 条」里有几条在当轮本就不适用？van der Sijs 明确区分 justified 与
  unjustified overriding，49–96% 不能整体读成不听话率。
- **强模型依赖**。提示词残留 4.0%–37.0% 且「no reliable relationship to general capability」；
  abort 出口对 GPT-5 有效、对 Claude Opus 4.1 几乎无效；清单编译收益从 +11.0pp 到 −1.2pp 翻方向。
- **结局指标未验证**。Inozemtseva & Holmes（724,000 行 / 31,000 个测试套件）：
  「coverage ... should not be used as a quality target because it is not a good indicator of test suite
  effectiveness」。公平标注：同文也发现套件规模与缺陷检出力中到很高相关，所以「225 没意义」是过读；
  它支持的是「这个计数从未被对照到验收质量上，不能当目标」。
- **不满足任何单被试设计标准**。WWC 要求效应在三个不同时点重现，每阶段有数据点下限；
  本实验三个操纵各只有一次演示，无撤回-再引入，无对照臂，结局指标事先未定义。
  （诚实标注：WWC 自己也说「三次」是文献惯例而非实证阈值。）

---

## 3. 模型漏掉的机制

逐条说明外面在用什么、三条为什么漏掉、值不值得补进来。

### 3.1 时间锚：判据必须在执行前冻结 —— **值得补，但并入主原则，不新增条目**

三条全是空间性的（产物什么形状、环境有什么、格子问什么），没有一条约束「什么在什么之前被固定」。
后果具体：agent 可以完全遵守第 2、3 条，同时把判据往已得结果上靠——放宽阈值、缩小 case 集、
把不过的 SoC 划出硬件集、事后重写 spec。这类作弊**不产生任何空格违规**。
外部做法是前瞻性注册（ICMJE 2005 起为发表前置条件，FDAAA 2007 立法强制）。
实证支持（观察性时间序列，非随机）：Kaplan & Irvin, PLoS ONE 10(8):e0132382——
55 项大型 NHLBI 心血管 RCT，1970–2000 年发表的 30 项中 17 项（57%）报告主要终点显著获益，
2000 年后 25 项中只有 2 项（8%）；作者逐一排除 comparator 变化、产业赞助、方法学改进三个替代解释。

**判定**：并入「把可伪造性从环境里拿掉」。判据可事后修改 = 判据由 agent 自己生成 = SLSA L1。
它不是新原则，是主原则在时间维上的应用。

### 3.2 每轮自检探针 —— **值得补，但作为落地实践，不作为模型条目**

三条里没有任何机制回答「我怎么知道这套强制此刻还在工作」。这正是本次归纳只能有一个样本的结构性原因：
没有常驻探针，就只能靠偶然观察。高后果行业的做法不是抽查，是每批产出都强制携带已知答案的对照：
FIPS 140-2 §4.9 的 known-answer test + error state 下禁止一切数据输出；
CLIA 42 CFR §493.1256(d)(3) 的每日阴阳/双浓度对照 + (f) 控制品不合格前不得报告患者结果。
另一半是暗测（DHS OIG 2015 红队 70 次中 67 次带过 TSA 安检，95%；**原报告保密，数字为媒体转引，二手**）。

**判定**：这是对约束的**测量**，不是约束本身。不进模型（模型只能减不能增），进落地建议。

### 3.3 冗余观测：同一事实双路独立产生 —— **并入边界自述，不新增条目**

边界自述只要求数字「来自别处」，挡不住「从一个已经错了的来源如实抄」（Delta 1141 正是此形态）。
FIPS 140-2 §4.9.1：两个独立实现连续比对可替代 KAT，不等即失败；§4.9.2：手工录密钥须 EDC 或双录入。
它比「多一个来源」强的地方在于：把「填得对但填得假」从需要人判断的问题，
变成一个确定性代码可做的相等性比较。

### 3.4 格集完备性：分母由不可操纵的枚举源定义 —— **值得补，并入第二条**

第 2 条保证不了「该有的格子都在」。整张表被略掉、某维度没进 schema、某 case 从来没被生成——
这些在结构上不可见，因为**不存在的格子不会空着**。Boorman 的 15 种错误模式对策表里，
唯一写「Not prevented.」的正是第 8 项「整张清单被完全遗漏」。
外部三个成熟形态：CONSORT 2010 item 13a/13b（强制报告各组随机化/接受干预/被分析的人数，以及流失与原因）；
OSCAL 稳定 ID（漏一条 = 悬空引用）；in-toto layout（缺一步即验证失败，且要求 no extraneous artifacts）。

### 3.5 对账的主体/时点/后果 —— **必须补，是第二条的成立条件**

见 2.2。额外的 LLM 侧硬约束：反馈回路必须压缩进同一轮。每轮全新 session，没有跨轮行为塑造，
Rantz 那种「下次飞行前给反馈」的路径在这里根本不存在。唯一可用形式是「当场逐项对账，不过就整轮失败」——
FIPS 140-2 的「error state 下所有数据输出被禁止」正是这个形状的标准措辞。

### 3.6 相对成本、出口与默认值 —— **必须补，第一二条共用**

三条只讲堵和问，没有第三轴：诚实填要花多少力气、绕过去要花多少力气、不作为时默认落在哪。
四条实证：Strom（无出口 → 4 例延误 + 系统外不留痕旁路）、Cureus（零成本 N/A → 100% 填写率 / 7% 真实率）、
Johnson & Goldstein（只改默认差 40 个百分点）、ImpossibleBench abort（54%→9%，但模型相关）。
Sentinel 的 soft-mandatory 给出成文形状：override 必须留痕、可归因。

### 3.7 观测权归属：能由系统读的，就不该让 agent 填 —— **值得补，是第一条在字段粒度的应用**

三条在「拿掉」和「让 agent 填事实」之间二选一，中间还有一整档：把观测整个从执行者手里拿走。
Boeing sensed line items；SLSA v1.0 的三句 MUST（每个字段必须由构建平台在可信控制面生成或验证，
用户控制的构建步骤不得注入或篡改，L3 要求签名密钥对用户步骤不可达）。
这比「数字来自别处」严格更强：不只是来源在别处，而是**产生那个数字的进程 agent 无法影响**。
必要性有量化：false success 在 tau2-bench 上 13%–79%，AppWorld 失败里 75.8%。

### 3.8 门长在哪里 —— **值得补，但已被本仓现有设计满足**

DORA：外部审批是负收益，门要长在产物生成那一刻、由确定性代码判定。
不要设计成「agent A 交付、agent B 审批」。这正是本仓「finalize 是唯一终态生产者、skill 不得重判」的外部支持。
（限定：横断面自报问卷的相关性，非因果实验。）

### 3.9 不值得补的两项

- **时间衰减**（手术清单 sign-in 82.4%→93.1%→74.7%）：LLM 每轮全新 session，无习惯化、无疲劳，无同构物。
- **跨轮反馈塑造**（Rantz 的累计折线图 + 表扬）：不存在跨轮记忆，这条路径在本场景里不成立。
  这也正是十条陈述句「抄写一次、勾 0 条」的原因——对人还能靠反馈救回来的东西，对每轮失忆的执行者救不回来。

---

## 4. 修正后的模型

### 4.1 先说诚实话

**外部证据支持的是一个比三句祈使句更复杂的模型。** 具体地说，它至少是四维的（空间形状、时间锚、
冗余观测、格集完备）加三个调节变量（对账、后果/成本/出口、总量预算）。把它压回三句，一定要丢东西。

但「条数只能减不能增」的约束可以满足，代价是**把必须一起做的事绑进同一条**，而不是假装它们不存在。
下面给两条主原则——比原来少一条——每条带一组不可省的配套条款。配套条款不是新原则，
是这条原则不带它们就会失效的实测证据决定的。

### 4.2 修正后的两条

> **第一条：把可伪造性从环境里拿掉——包括路径、值和判据。**
>
> 拿掉之后，规则**保留**，但降级为「唯一合法替代路径」的说明，不再承担保证责任。
> 三个不可省的配套：
> - **枚举等价路径**：拿掉一条不算完，缓存目录、git 历史、其它 worktree、示例目录、
>   以及模型权重里记得的内容，都要点一遍。最后一项拿不掉，这是「消除」在 LLM 场景的真实上限。
> - **留一个出口**：每道硬门必须能回答「合法但被这道门挡住的情形长什么样，那时的出口是什么」。
>   出口本身留痕（谁批的、依据什么、有效期），成本高于正路、低于走系统外。答不上来不许上硬门。
> - **评估位移**：堵一条路的主要后果是行为位移。位移后的新路径可发现性变差，就不算改进。

**外部支撑**：控制层级 / ISMP 十档 / ISO 80369 不生产转接件 / Google API hardening 三件套 /
MCP 代理 0% vs 4–37% / SLSA 的三句 MUST（把「值不可自生成」和「判据不可事后改」都并进来）。
**反面约束**：τ-bench −22.4pp（规则不能删）、Strom RCT 4 例延误（必须留出口）、
Baker CoT 混淆 + ImpossibleBench（必须评估位移）。

> **第二条：拿不掉的，在被机器校验的交付物上开一个槽位；同时写死谁在什么时点拿什么对它、对不上会怎样。**
>
> 「写表格」这个说法作废——散文里加一张表不满足本条。判据是：这一格能不能在填写的那一刻被
> 无歧义判定为真或假，以及它落在 preventive / proactive / detective 的哪一档。退到 detective 就等于放弃，
> 应当直接删掉这条规则而不是留着当摆设。
>
> 槽位的三条判定轴（这三条**不是第三条原则**，是本条的准入判据）：
> - **问事实，不问判断**，且这个事实必须与产物有因果绑定（填不出来就说明活没干）。
> - **不要二元字段**。理由不是「『是』太容易填」，是二元格式引入与内容无关的方向性偏移。
> - **值域里不得有零成本空值**。空数组、N/A、可选字段在行为上等价于复选框，会被整体跳过。
>
> 两条成本条款：
> - **总量预算**：只保留「不逼就会被跳过 且 填错会被别处对账抓到」的格子，其余删。
> - **作用域**：只锁「必须报哪些事实」，绝不锁「怎么想、中间过程怎么组织、生成多少用例」；
>   并强制留一个显式的「表外异常」槽位。

**外部支撑**：Schaad 内部对照（有格子的完整、没格子的近零）/ SBOM mandatory 100% /
OSCAL 稳定 ID / in-toto MATCH / 约束解码 / Naiakshina（要求出现在当轮交付面上）。
**反面约束**：Ho 2018（光有格子零效果，须绑后果）、IICARus 0% vs NPQIP 16.4%（须有对账）、
Instruction Stacking −1.2pp（须有总量预算）、Price of Format −70% + Schaad 反例（须有作用域限制）。

### 4.3 原第 3 条去哪了

被拆成两半，一半塌回第一条，一半降级为第二条的判定轴：
- 「值不能由填写者自己生成」= 第一条（让填写者无法伪造这个值，就是让违反不可能）。
- 「问事实、非二元、无零成本空值」= 第二条的准入判据。

这不是为了凑数减一条。SLSA 的规范措辞和 Cureus 的 7% 真实率一起说明：
一个字段问的是不是事实，远不如「这个值的产生进程 agent 能不能影响」重要。
原第 3 条是第 1 条在字段粒度上的弱版本，独立成条会让人误以为「问了事实就够了」。

### 4.4 这两条现在算什么

**待检验假设，不进 `AGENTS.md` 当仓规。** 在下面四件事做完之前，本报告不建议把它们写成仓级规则：
1. 补两个缺失的臂：十条陈述句改写成十个必填字段（测密度）、`unvalidated_requirements`
   改写成文档正文里一句陈述（测载体）。
2. 每个操纵做 ABAB 反转，每阶段 k≥5 轮，预注册结局指标与判定阈值再跑（WWC 要求三个时点重现效应）。
3. 结局指标从「生成了多少用例」换成「这批用例在已知注入缺陷上的检出率」或「相对任务书维度集的覆盖分母」。
4. 适用声明写成「在模型 M、版本 V 上观察到」，换模型或换版本重跑同一组判据。

---

## 5. 对 OpRunway 的具体建议

执行者是 LLM agent，没有运行时代码可拦截，所有强制只能存在于产物形状与环境里。五条，可直接照做。

### 建议 1：判据在执行前冻结并哈希绑定

spec 与 ATK 设计文件必须在任何 DUT 执行、任何 case 生成之前落盘并取 SHA-256；
`acceptance.json` 里的判据哈希必须逐字等于执行前那一份。执行后任何判据变更 = 断链 = 不得 PASS。
这不是多挖一个格子，是给整张表加一个时间锚——它堵的是「完全遵守字段规范、但把阈值/case 集/硬件集
往已得结果上靠」这一整类作弊，而这类作弊在现有 fail-closed 判据下不产生任何违规信号。
依据：ICMJE/FDAAA 前瞻注册；Kaplan & Irvin 的 57% → 8%。

### 建议 2：回收观测权，关键字段双路重算

- 从 agent 的产出 schema 里**删掉**所有环境可读的字段：vendor ELF 的 SHA-256、`nm` 符号、返回码、
  profiler CSV 行数、安装树路径、设备号、build 命令。由 `oprunway.verdict.finalize` 直接读写。
  agent 只填环境读不到、且它这一轮真的持有的事实（`unvalidated_requirements` 属于这一类，
  这也解释了它为什么有效而多数字段不会有效）。
- 关键量走双路：agent 从 ATK 日志抄一份、finalizer 独立 `sha256sum` 算一份，进两个字段，不等即 fail-closed。
  case 分母三路交叉：生成器声明的 N、输出目录实际计数、profiler CSV 行数。
- 依据：SLSA v1.0 三句 MUST；FIPS 140-2 §4.9.1 双实现连续比对；
  false success 13%–79%（自评字段结构性恒为成功）；LLM judge 查 LLM 自述 AUROC 仅 0.54–0.65。

### 建议 3：分母闭合，缺 ID 即失败

requirement ID 全集（任务书解析器产出）与 case ID 全集（ATK 生成器产出）必须由两个互不依赖的进程
各自独立落盘为清单；终态逐 ID 引用，未覆盖的 ID 即失败。**每个没跑的 case 必须以一条带原因的记录存在，
而不是「不出现」。** 现有仓规已要求「case 完整分母」，这条把它从一个数字升级为一个可被确定性代码
比对的 ID 集合——因为不存在的格子不会空着，Boorman 对这一项的对策栏写的是「Not prevented.」。
依据：CONSORT 13a/13b；OSCAL 稳定 ID；in-toto layout 的 no extraneous artifacts。

### 建议 4：堵掉零成本空值，同时开一条留痕出口

- `unvalidated_requirements` 空数组不再合法。无未验证项时必须填一条**带判定依据与来源产物路径**的记录。
  否则这个字段与 Cureus 那个 92% 选「N/A」的单选按钮同构——这一轮被老实填了 7 条是抽样，不是属性。
- 同时给每个必填格配一个结构化的「我拿不到，因为 X」出口：填写者需给出阻塞点与已尝试路径，
  成本高于如实填、低于编造；走出口的产物在 `acceptance.json` 里被**显式标记**，不静默通过。
  本仓的 witness 机制（被收据哈希绑定的最薄 plugin）已经是这个形状，缺的是 override 留痕那一半。
- 依据：Strom RCT（无出口 → 系统外不留痕旁路）；Cureus 100%/7%；Sentinel soft-mandatory；
  ImpossibleBench abort 54%→9%（并注意对 Claude Opus 4.1 效力有限，不能只靠这一手）。

### 建议 5：每轮注入已知答案的探针；同时给 `AGENTS.md` 瘦身

- **探针**：由 harness（不是 agent）在 caseset 里注入两个答案确定性已知的 case——一个必然精度不匹配、
  一个必然通过。终态若把必失败探针判成 PASS，或探针在输出里消失，整轮作废记 `PLUGIN_ERROR`。
  它测的是整条链路（生成 → 执行 → 采集 → 裁决），不是某个字段；并且它让「这套强制此刻还在不在工作」
  从每轮的偶然观察变成常驻测量——这是「样本量为 1」的唯一结构性解药。
  依据：FIPS 140-2 §4.9 KAT + error state 禁止输出；CLIA 493.1256(f)。
- **瘦身**：`AGENTS.md` 里靠 agent 自觉遵守的陈述句，按以下顺序处理——能做成环境不可达的做掉；
  能做成产物槽位且能回答对账四问（谁、什么时点、拿什么对、对不上会怎样）的做掉；
  两样都不行的，降级为「唯一合法替代路径的说明」并明确它不承担保证责任。
  不要新增必填格，除非能回答对账四问。
  依据：IFScale 500 条 68%、Instruction Stacking 20 条 60.4%、Lost in the Middle 的位置效应、
  van der Sijs 的信噪比诊断（规则条数与单条遵守率负相关，删规则本身就是一种有效干预）。

---

## 6. 如实报告的空白与限制

1. **没有直接对照实验**。我没有找到任何「同一要求写成必填字段 vs 写成散文规则」的 LLM 受控实验。
   最接近的两条（ImpossibleBench abort 54%→9%；Naiakshina 2017 不提示则 20/20 全部明文存储）
   只支持「要求出现在当轮交付面上有效」，不支持「格子优于句子」这个更精确的命题。
   本仓那次单样本观察在这一点上仍是外推。
2. **多条关键证据是预印本或单一厂商自报**：false success、Beyond Task Completion、
   Instruction Stacking、AIRGuard、MCP 代理、Baker 等、Google/Chroma 的遥测与博客。方向可用，幅度打折。
3. **人类证据的迁移折扣**。Ho 2018、Strom、Cureus、IICARus 的执行者都是人，有职业声誉、
   工作量压力和长期后果；LLM agent 三样都没有，且每轮失忆。方向一致时可以互证，幅度不可直接搬。
4. **本报告未做一手核对的转引**：NTSB/AAR-89/04 p.61（经 Degani & Wiener 逐字转引）；
   DHS OIG 2015 的 67/70（原报告保密，媒体转引）；FIPS 140-3 / ISO/IEC 19790:2012 正文付费未核，
   逐字核过的是已被取代的 FIPS 140-2；CONSORT 只核到条目原文，未核其自身效应量。
5. **一处方向仍未定**。Callegaro 元分析：forced-choice 让人多认可 42%，但这是「更深加工挖出真东西」
   还是「格式诱出的顺从」，学界未定。可安全保留的只有弱化版：可留空的槽位会被整体跳过，
   强制逐项产出会改变产出量；**不能据此断言「强制 = 更真实」**。
