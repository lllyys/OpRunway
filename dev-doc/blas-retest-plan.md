# blas-accept 复测与 warmup 实施计划（plan v4，2026-09-18）

依据 [blas-retest-warmup-spec.md](blas-retest-warmup-spec.md)（spec v6 定稿）。本文自足：
只写最终决定与当前状态，不需要读旧版或评审记录才能执行。

修订记录：v1 初稿；v2 吸收两单五维评审并改并行结构；v3.x 闭合并行结构评审；
v4 按双模型七维重审收窄——发布事务范围、回滚依赖顺序、折叠接口瘦身、验证分层、
正文去历史化。评审结论均为 NEEDS REVISION 修订后放行，无整体推倒项。

## 0. 全局约束

- 触及核心裁决与对外契约，每个切片一轮 Codex checkpoint（audit kind、七维打分）。
- 量具写的字段与 accept 读它的解析器同切片同验，不允许一侧单独合入。
- 精度侧零改动；默认 warmup 恒 0；换卡是独立后续版本（§6），不进本轮。
- 术语沿 spec §2；**golden** 指固化后逐字节/逐键比对的参考产物；**no-clobber**
  指目标文件已存在即拒绝写入。

## 1. 改动面与所有权

| lane | 文件（相对仓根） | 内容 | 状态 |
| --- | --- | --- | --- |
| W1 | case-gen `assets/template/verify_performance.py` | F1.1 证据保护（存在性检查前移、no-clobber 提交、复测环境失败不写 JSON）；F1.2 首轮锚字段两个；F1.3 warmup（首轮条件序列化）；F1.4 复测模式 | F1.1–F1.3 已落地；F1.4 待接口定形后开工 |
| W2 | blas-accept `scripts/retest_fold.py`、`tests/test_fold.py` | 纯折叠核 + 单测 | 已落地并过 audit→fix→verify；待做接口瘦身（§2） |
| W3 | blas-accept `scripts/accept.py`、`assets/template/report.md`、`tests/test_retest_contract.py` | inventory、waive、retest-preflight、折叠集成、报告、契约测试 | 进行中 |
| W4 | 两 skill 的 perf-protocol.md、run-chain.md、SKILL.md、README 模板、readme-contract.md、新 `references/retest-protocol.md` | 协议文档折入（P5a 批次） | 已落地；waive 语法同步中；真机事实批次（P5b）押后 |

## 2. 接口冻结（最终形态）

- **折叠核**（W2 收窄）：折叠只接收算法必要字段——
  `Round = {round:int, kind:"measure"|"waive", cases:[{name,status,ratio}],
  waivers:[{case,reason}]}`，首轮规范化为 round 0；`FoldResult` 保持
  per_case（有效状态/代表轮/参考轮/豁免理由/有效复测测量次数）+ pass_on_retest +
  warnings。设备、warmup、kernel_us 等展示字段不进折叠：A5 保留完整记录，按
  代表轮/参考轮号回查展示数据。`FOLD_PROTOCOL_VERSION` 常量在本模块单源。
- **共享上下文**：`load_retest_context(workdir)` 纯函数统一产出身份字段、性能
  期望集、首轮锚字段、轮次 inventory（完成/中断/无效分类、已占号集合、下一轮号 =
  已占号最大值 +1）。`retest-preflight`、`waive`、`verdict` 只消费它，不各自扫盘。
  retest-preflight 是它的 JSON 视图（启动检查，不是最终裁决；A5 校验独立存在）。
- **waive CLI**：`--waive <case> <理由>`（argparse 二元组，可重复），无分隔符协议。
- **发布事务范围**：只有归档目录 `intermediate/` 用「临时树整体生成 + 替换」消
  stale；`report/report.md` 与 `repro/` 各文件沿用原子文件写入。承诺：成功返回时
  归档为完整新版本；失败保留完整旧副本可恢复；不承诺路径连续可用。verdict 启动时
  的 orphan 处理三分支：有备份树无正式树 → 先把备份恢复为正式再继续；正式树与
  备份树同时存在（rename 后、删 bak 前被中断）→ 正式树为准，删除备份并记
  warning；存在临时树 → 删除（未完成的产物）。处理后照常发布本次。
- **量具复测模式**：run-id 取最右 `-retest-<k>` 后缀识别，入口解析一次成模式对象，
  采样执行链保持单一，不在各分支重复判断后缀。

## 3. 提交与回滚（依赖顺序制）

提交按功能三笔（同 lane 内按此序落）：①证据保护+锚字段 ②warmup ③复测
（量具复测模式 + accept 全部 + 复测文档段）。

合法回滚只有逆序一条路：**先撤③复测，才可撤②warmup，最后才可撤①证据保护**。
理由：复测轮必填 warmup 字段（依赖②）与绑定锚（依赖①）。不存在可独立撤除的
中间组合；文档的复测段随③同撤。

## 4. 验证（按机制分层，正式证据全部在远端产生）

本机只编辑、Git、只读探测。**所有计入验收的测试运行一律在 a3 容器执行**——
纯逻辑/文件系统组跑容器 CPU，采集组跑 NPU；开发过程中的任何本机快速运行都不作为
验收证据，验收记录只引用远端运行的输出。

**CPU 组（a3 容器，确定性故障注入）**：折叠与校验全量单测；未知/重复点名、
waive 参数错误、坏 JSON 轮、轮号空洞告警、旧首轮缺锚双线拒绝；verdict.json 三态
条件输出（无痕迹/仅中断无效/有有效轮）；归档整树替换消 stale、替换失败恢复旧副本、
orphan 临时树与备份树两分支；A5 重跑幂等。

**NPU 组（a3，sger 或 ctpmv 小包）**：

1. 零复测 byte-compat：冻结工作目录（`gate0/work2-frozen`，基准已产出：exit 0、
   golden 哈希对已落档）上新旧 accept 同绝对 `--out`，比较 `report.md` 与
   `verdict.json` 原始字节。此门只保证**零复测 A5 投影兼容**（默认路径、通过态
   fixture），不宣称全链路字节兼容；证据不足/不通过分支由 CPU 组语义断言覆盖。
2. FAIL → 点名复测 → PASS → `PASS(复测)`。
3. 复测 `--warmup 10`；预热超时（缩小 `--timeout` 构造）→ TIMEOUT 不采样；
   预热非零退出/启动异常 → 告警照常计分。
4. waive → 分母规则 → 再测撤销。
5. auto 首轮 → 复测 device 规则。
6. 首轮环境失败仍产错误 JSON（现行回归）；复测环境失败不产 JSON 成中断轮。
7. kill 复测轮 → 中断占号顺延。

绑卡机制覆盖说明：v1 的设备规则是「与首轮相等」的 profile 无关校验，
sparse_frame 的运行时绑卡差异只在换卡版本才分叉，其代表验证归 §6。

## 5. checkpoint（共三个，每个 = audit kind + 七维打分）

| # | 覆盖 | 验收条件 |
| --- | --- | --- |
| C1 量具单元 | F1.1–F1.3 diff | 评审 PASS；NPU 组 #1（byte-compat）与 #3（warmup 生产路径）通过 |
| C2 复测切片 | F1.4 + W2 瘦身 + W3 全部 | 评审 PASS；CPU 组全绿 + NPU 组 #2/#4–#7 通过；契约测试覆盖真实渲染 JSON 往返 |
| C3 文档 | W4 七文件 diff | 评审 PASS（含**全新 session 零上下文冷读轴**）；`python3 plugin/.claude/hooks/doc_style_lint.py` 与 `python3 plugin/.claude/hooks/skill_budget_lint.py` 均退 0 |

折叠核已单独过 audit→fix→verify，C2 只复核其瘦身 diff，不重审语义。

## 6. 换卡（独立后续版本，本轮不实施）

门控现状：重映射 msprof 链与耗时一致性已实测通过（spec §10 G1）；仍差显式非零
编译卡完整链一次 + 用户裁定。实施时另立计划：profile 绑卡模式与编译逻辑卡推导
放 profile 数据/registry，不写算子或 profile 名的条件分支进核心代码；独立提交组
G-换卡 + 同卡回归门（NPU 组 #1/#2/#5 重跑 + 换卡专项）。

## 7. 明确不做

精度侧改动；默认 warmup ≠0；防篡改机制；旧工作目录迁移；isolated-acceptance；
上游 PR（另行立项）。
