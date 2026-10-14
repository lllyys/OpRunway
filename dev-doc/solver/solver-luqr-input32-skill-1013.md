# LU/QR skill 实际输入升精度口径

2026-10-13，用户明确残差参考必须采用实际送入算子的32位输入，升为64位计算；随后明确只处理 skill，不考虑产出包。本次确认取代此前“输入口径待确认”的状态。

- CPU 回填：getrf/geqrf/gels 改取 A32/B32；mqr 改取 C32，Q 从 A32/tau32 升精度构造。回填版本为 a32base-lqr2。
- 判定卡：mqr 同步从实际反射子构造 Q，其余相关卡已有实际输入口径。
- 渲染模板：同步残差喂数和参考链，共享 geqrf/mqr 的 Q 构造 helper，显式升 float64/complex128。
- 文档及已有 mqr 测试 fixture 同步。残差核、归一化 epsilon、阈值公式和输入生成器未改。

验证：本地仅 AST 与 diff 检查；远程对既有26个小用例、42个代表矩阵核对 CPU 参考链、判定卡和渲染副本，三者一致。16个算子渲染成功。核对时删除原始 A64/B64/C64/q64/q32 字段，确认计算不依赖这些参考字段。证据在 ignored reports/skill-input32-check-1013/。未运行完整测试套件或正式 NPU 验收。

按仓规由独立 Codex 做一次只读 audit → 修说明 → verify，无阻断项。本机旧门要求 Claude 收据，与用户只用 Codex 指令冲突；本轮读取 Codex skill-creator 并使用 Codex 评审，未使用或伪造 Claude 收据。

没有修改已有16包、冻结CPU值或GPU基线。旧 lqr1 曾标记 A32-f64 但部分实现仍用原始64位输入，不能仅凭该标记认定同口径；skill 文档说明使用前应重新冻结受影响值，本次不执行产物迁移。未 commit、未 push。
