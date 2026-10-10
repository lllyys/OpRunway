# 2026-10-10 solver 接续收尾

## 范围与授权

接续历史会话 `2b1af908-7b1e-459b-8e87-f61c5e9fb15b` 的 harness、两 skill 与十包交付工作。
用户明确「不用 claude 了，现在只用codex」。本轮实现、独立审计和复核由 Codex 完成。
既有 skill 编辑钩子要求 Claude 收据，与本轮用户指令冲突；使用钩子公开的
`SKILL_GATE_OFF=1` 完成编辑，没有改钩子或伪造收据。未 push/merge。
测试、构建和装包门均在已有授权目标的新建独占容器执行；保护根未写入。

## 已落地与验证边界

- accept SKILL 按 A0 工程准入、A1 自建执行、A2 判定留证、A3 重判报告组织；
  新增共享 harness 运行说明，保留 npz 备用入口与明确标注的模拟诊断入口。
- 自测 renderer 升为 s4-D13/s3-D11：输入现场构造，判定消费冻结 ratio/status/mean/sample_map；
  明文或 gzip 二选一，gzip 指纹以解压明文为准。accept_run 使用同一字段准入实现。
- builder s3-F15 负例门保留选中条目的完整冻结参考值，并禁止包内导入留下字节码。
- 共享 harness 修复执行失败/复跑未知态、失败输出与独立重判、指纹、清理及进程组收尾；
  本轮独立 audit→fix→verify 四项均 FIXED；原 info 数组越界误报已撤回，真实 Host 为标量。
- 目前真实工程只具备已核实的 Host 入口。Cholesky 的 Device ABI、因子准备与完整
  批内真算子示例仍待真实交付，明确拒绝猜接口，不以 cmatinv 或模拟结果替代。

## 十包仅重渲，不重算数据

源为 reports/v5-packages；保留其 canonical、perf_baseline 与 index 数值。
更新 verify/gen/sim 脚本、README 和派生 manifest；批量 index 复用既有 gzip。
装配脚本只调用发布 skill 的 renderer、指纹检查与正负例门，不另建判据。
十包远端重渲染与逐包门合计 59.01 秒，全部通过。候选目录
reports/solver-completion-1010/candidate；状态 PENDING_BASELINE_DECISION，未发布。
原 manifest 的环境与 build 来源保留，重渲版本另记，不冒充本次重新采集数值参考值。

## 数值基线待裁定

旧报告把 potrs 环境差异视为只影响 mean；新冻结消费口径同时使用逐例 ratio 与 mean，
因此旧结论不足。本轮以实际判据计算 v4→v5 阈值变化：

| 算子 | 阈值改变条目 | 放宽/收紧 | 最大放宽 | 最大收紧 |
| --- | --- | --- | --- | --- |
| spotrs | 171/176 | 164/7 | 46.557% | 3.313% |
| cpotrs | 174/176 | 113/61 | 30.093% | 8.613% |
| spotri | 17/145 | — | 3.652% | 0.632% |
| cpotri | 21/145 | — | 21.060% | 24.949% |

环境变化与观察相符，但尚不能证明它是唯一原因。保留 v4 数值需整组保留
ratio/status/mean/sample_map，不能只换 mean；接受 v5 则须接受全部阈值变化。
旧 batched index 本地快照仅留 LFS 指针，尚无内容可作完整对比。
用户尚未裁定，候选保留 v5 原值只为可审阅，不构成默认接受。

## 明确未完成

- HT-32：基线选择与相应最终发布；没有授权 push/merge。
- HT-31：真实 Cholesky 工程交付后的 S0、实际调用、因子准备、大规格实测和完整外部样例。
- 旧记录提及的装包 --workers 优化：当前 builder/gen 无该接口，本轮未新增进程池，
  不宣称任何 30–50% 加速结果；仅复用既有冻结数据节省重算。

## 验证记录

远端同版两 skill 全部测试 **404 passed in 20.73s**，覆盖 criteria、harness、case-gen。
独立 Codex 复核四项 FIXED；性能说明模板的遗留 0.8/A100 也同步为当前 GPU/0.35。
本地只读文档行文/引用检查与 skill 预算检查通过，git diff --check 通过。
十包权威正负例门均通过、冻结文件与源指纹一致；这是工具判定链检查，不是算子正式验收。
证据留 reports/solver-completion-1010/，审计更正与边界见 verify.md。


共享工具候选：reports/solver-completion-1010/solver-shared-tool-candidate.tar.gz，
含相邻两 skill、独立运行说明与逐文件摘要；按目录执行，不冒充插件安装包。

真实 C++ 验证：cmatinv n=8、batch=4、seed=923000001，同一输入的旧/新执行件
各完成五轮且逐字节一致；两个 Host 入口的错误 info_kind 都在调用前退 10。
cgetri n=8 被真实接口 n≥32 限制拒绝，失败及重判均保留执行失败/确定性未知；
额外 cmatinv n=8、batch=2 尝试返回参数错误，未宣称该形状通过。
上述 Host 证据不覆盖 Cholesky，也不构成其精度与性能结论。

## 后续裁定：样例载体改为 cmatinv

用户随后指定「出一个 cmatinv 样例包就好」，原「等 Cholesky 批内样例」交付要求由本条取代。
新包位于 reports/cmatinv-sample-1010/，独立提供共享 harness 执行命令及原 v2 数值教学资料。
共享执行报告明确 NOT_JUDGED，独立数值样例另出残差/一致性报告，不混合为同一次验收。
Cholesky 接口接入仍待真实工程，十包基线裁定不变。

## cmatinv 性能补齐

按用户追问补入 kernel-only 采集，脚本落发布 skill 内，不在 reports 另建判据。
msprof op 精确符号匹配、30采样、工具预热5次；CSV逐条核kernel/device/有效耗时，
缺采/重复/执行失败均拒绝。CANN9实测文件名为 OpBasicInfo_<timestamp>.csv。
共享harness直接管理profiler→DUT同一进程组，超时和取消不再经过嵌套harness。
137项harness测试通过（新增15条），真机正例采满30条，错误kernel名负例退2。
独立一轮audit发现的P2已修并verify；八维5分制为4/4/5/5/5/5/5/5。
均值0.328047892ms、中位数0.330246598ms，不是API墙钟；未绑定GPU基线，
performance_verdict=NOT_EVALUATED，不能类推Cholesky的GPU/0.35门槛。
样例包附一键入口、脱敏报告与30份原始CSV；证据在reports/cmatinv-sample-1010/internal-evidence/。

## 样例去除 skill

用户明确「skill 不要放」：样例压缩包已移除 skill/，两个入口通过环境变量
SOLVER_ACCEPT_DIR/SOLVER_CASE_GEN_DIR 引用外部工具；版本要求与前置检查写入README。
共享工具独立包未删除；样例保留教学脚本、性能实测报告及CSV。校验摘要重新生成并核对。

## 性能判定链与本轮完成范围

用户要求逐 case 采集→匹配GPU基线→按任务书阈值判断→汇总，明确采集必须用 msprof op。
新增 criteria/performance.py 与 scripts/harness/run_performance.py；accept_run 消费同份
采样证据，重新读取CSV并判定，不信任旧PASS或派生均值。全部普通case保留在全集，
缺基线/漏采/执行异常/目标不符均保留证据不足。正式条件为950PR、列主序、Device ABI；
GPU源为冻结perf_baseline.json，mean NPU ≤ GPU avg_ms/0.35。性能结论与总体结论分开。
旧perf.json参考比值为可选展示，不阻断正式性能门禁；正式项缺证据仍阻断。

远端A3真实跑了两个cmatinv case，每例30条目标kernel CSV，共60条，均COLLECTED；
因A3/Host条件不满足正式任务，两例均INSUFFICIENT，离线重判结果一致。此链路夹具
的GPU数值为明确标记的合成测试数据，不能当真实GPU基线或算子性能达标证据。
初轮全套462项通过，文档审查修正均值/中位数旧说法后新增两项可选参考门禁回归。
独立Codex审查的契约异常、采样计划/构建记录关联问题均修复并复核；最终全套 **464 passed in 21.42s**，测试记录见
reports/performance-acceptance-1010/performance-full-suite-final.log。

用户随后明确不要求完整算子验收跑通，并授权Codex并行收尾。本轮交付范围为工具
流程与一个cmatinv样例。Cholesky真实接口适配、950PR完整实跑与cmatinv精度卡接入
是后续工作，不作为本轮完成条件；既有数值基线待裁不变。

样例新增ADAPTATION.md，逐项强调公开头签名、dtype、布局、Host/Device、workspace、
前置分解适配，以及C++分支的资源管理/调用/同步/回读；不得依赖开发者测试或判定。
历史数值教学流程清楚标为独立参考，numeric=NOT_JUDGED边界未改变。重包46个载荷文件，
无skill，文件与压缩包摘要已核；共享工具独立候选同步更新。

十包候选只修正两份分组PERF_COLLECTION_SUPPLEMENT.md及其公共模板，
统一均值口径、正式性能入口和基线更新规则；未重渲十包，未改用例或冻结基线。
