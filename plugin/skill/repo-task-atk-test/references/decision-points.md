# 决策点清单

<!-- 本文件由 scripts/render_views.py 从 references/artifact-contracts.json 渲染，不要手改。 -->

agent 在验收过程中必须自己判断的字段全集。

一个决策没有判据来源，就是一个洞——不必等真机跑测来发现。

判据来源写「查 X」的，用 `scripts/atk_lookup.py X` 查，不要 grep ATK 源码。

查不到就是 skill 缺陷：登记进 `evidence/knowledge_gaps.json` 再去读源码。

| 决策 | 阶段 | 判据来源 | 谁消费 | 判错的表现 |
| --- | --- | --- | --- | --- |
| `<op>_decl.json` 的 `operator_class` | S2 | 任务书的算子类别，取值开放 | _coverage_strategy 据此选默认轴与默认交互组 | 不在已固化类别里且没写 class_profile → make_must_cover.py 退出码 2 |
| `<op>_decl.json` 的 `class_profile` | S2 | 任务书 / 基线接口语义 / 生态标准，不从待验收算子实现反推 | _coverage_strategy 拿它当该类算子的必需轴、默认三轴组和精度配比依据 | 四项（axes/group/arithmetic/why）缺一或轴名不合法 → make_must_cover.py 退出码 2 |
| `<op>_decl.json` 的 `dims` | S2 | 钉死的轴取 _axis_binding.PINNED_AXIS_VALUES；dtype 轴取待验收算子工程声明的数据类型表（--dtype-source）；其余轴取任务书与签名 | make_must_cover.py 的覆盖分母 | 钉死的轴取子集或改名 → make_must_cover.py 退出码 2；dtype 轴的取值在 --dtype-source 里找不到 → 同样退出码 2 |
| `<op>_decl.json` 的 `dtype_source_excludes` | S2 | 文件里那处误报的上下文（属性或输出的类型名），每条附 why | make_must_cover.py 的 dtype 漏写检测 | 缺 why 或豁免的 dtype 在那份文件里根本没出现 → make_must_cover.py 退出码 2 |
| `<op>_decl.json` 的 `coverage_policy` | S2 | references/case-design.md#策略 | make_must_cover.py 据此挑组合、控预算 | 轴不在 dims 里 → make_must_cover.py 退出码 2 |
| `<op>_decl.json` 的 `axes` | S2 | 能从用例 JSON 读回来的轴才能放 | check_coverage.py 的覆盖签名 | 签名区分度不足 → 多条 combo 碰撞，覆盖率虚高或命中不上 |
| `<op>_decl.json` 的 `extract` | S2 | _case_utils 提供的读取函数 | check_coverage.py 从用例回读轴值 | 缺规则 → check_coverage.py 报 missing_rules |
| `<op>_decl.json` 的 `parameters` | S2 | 基线签名与 aclnn C 签名，由 align_signatures.py 对齐；契约的键就是 YAML 输入名，必须等于基线形参名 | make_yaml.py 推导每个 YAML 输入 | element_kind/runtime_container 组合非法，或键名不等于基线形参名 → make_yaml.py 攒齐报错 |
| `<op>_decl.json` 的 `infeasible` | S2 | 物理不可达的轴组合，每条附 why | make_must_cover.py 排除组合 | 缺 why → make_must_cover.py 退出码 2 |
| `<op>_decl.json` 的 `yaml` | S2 | references/yaml-schema.md#顶层字段 | make_yaml.py 原样写进 YAML 头部 | 字段名写错 → 由 make_yaml.py 的未知头部键检测拦下 |
| `<op>_decl.json` 的 `comparator` | S2 | references/experimental_standard.md | check_coverage.py 判断是否豁免浮点占比 | 与 dtype 轴矛盾 → check_coverage.py 报错 |
| `<op>.yaml` 的 `name` | S2 | 任务书里的基线函数名，如 torch.median | pytorch 模式的定位字段；CPU 基线默认执行器按它 eval | 填成待验收算子的接口名 → 基线与待验收算子同源，精度比对恒过 |
| `<op>.yaml` 的 `aclnn_name` | S2 | 任务书里待验收算子的 aclnn 接口名，如 aclnnMedian | aclnn 模式的定位字段 | 与 name 同值 → 待验收算子与基线不可分辨 |
| `<op>.yaml` 的 `kernel_name` | S2 | 任务书里待验收算子的 kernel 接口名 | kernel 模式的定位字段 | 与 name 同值 → 待验收算子与基线不可分辨 |
| `<op>.yaml` 的 `api_type` | S2 | CPU 基线插件的 @register 名 | ATK 据此取基线执行器 | 与 @register 对不上 → 报「请检查用例yaml中的api_type字段是否有对应标杆API文件」 |
| `<op>.yaml` 的 `aclnn_api_type` | S2 | aclnn 执行器的 @register 名；不需要适配器时为 aclnn_function | pyaclnn_backend 据此取待验收算子执行器 | 该写适配器却留默认 → S3 冒烟类型校验失败，且改它要重跑 atk case |
| `<op>.yaml` 的 `generate` | S2 | 生成器插件的注册名 | atk case 据此取生成器 | 对不上 → validate_cases.py 的 C1 不通过 |
| `<op>.yaml` 的 `standard` | S2 | references/experimental_standard.md#选哪个比较器 | 精度判定与性能对比模式 | int8 输出声明 mixed_tolerance_bm → 走量化标准，差一判过；dtype 轴有浮点却声明 equal → 覆盖门禁拒绝 |
| `<op>.yaml` 的 `version` | S2 | 固定 v1 | ATK 用例版本 | 缺失 → ATK 解析失败 |
| `<op>.yaml` 的 `api` | S2 | 接口模式 | ATK 选择调用通路 | 与实际后端不符 → 绑定到错误通路 |
| `<op>.yaml` 的 `outputs` | S2 | 调用序列表的 output.in_place 及基线函数的原地输出位置 | check_signature_contract.py 核对基线输出位置 | 缺失或位置不一致 → check_signature_contract.py 退出码 2 |
