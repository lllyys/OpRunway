# LU/QR 交接合入（2026-10-13）

用户授权将交接版两个 solver skill 合入当前 worktree，并将产物复制到本仓管理。
来源提交 23804ff，共同基线 e7820e0；合入前本仓两个 skill 的所有原有文件与基线一致。
只应用两个 solver 目录的增量，不覆盖其它插件源码、已有使用指南或样例。

## 落地范围

- 导入 16 接口的生成器、判定卡、残差与阈值、基线匹配、装包及索引压缩／切分支持。
- 文档修正：性能均值、getrf/ipiv 与 geqrf/tau 必需输出、getri 输入 LU 与主元、
  冻结 CPU 参考值只读、单层残差说明、Cholesky 专属 mean 规则、批量输入与索引形态。
- 保留现有固定适配协议和 cmatinv 样例；LU/QR 真实执行及正式性能尚未接入，
  已在入口和引用文档声明。accept_run 对 gels/mqr 的元数据透传不完整，明确为遗留。
- 未改导入版本的运行逻辑，仅补充一处 getri 代码注释。没有重算数值基线、用例或GPU数据。

## 产物

本仓管理入口：reports/solver-luqr-1013/INDEX.md。
包含四份任务书、16 个任务包、冻结资料、原交接记录及历史验证报告；排除 .git 与缓存。
原始微信目录保持不变。16 包仅更新 README 和各自 manifest 的 README 摘要；
四组包位于 build-1014/{lu_real,lu_complex,qr_real,qr_complex}/packages/。
210 个来源文件完成静态字节核对，只有 16 个 README 和 16 个 manifest 与来源不同；
其余来源文件相同。证据：reports/solver-luqr-1013/copy-inventory.json。

本目录是内部管理副本，不是对外发行归档。frozen/private 为交接方占位私集；
旧 selfcheck 与 verify 报告仅为交接历史，不代表本次验证。

## 审查与限制

按用户要求不再运行测试，本次未执行测试、构建、生成器或真机操作，仅静态审查与复制核对。
独立 Codex audit 指出三项：Cholesky 阈值范围、getri 注释已修；备用复核缺元数据
选择明确限制并留待后续，不扩大本次实现。Cholesky 默认判定路径未发现静态公式改写。
同轮静态 verify 确认三项处理准确；其指出的两处冻结参考文案遗漏已同步修正模板和任务包。
索引消费由多形态并存时报错改为 plain/gzip/parts 优先级，逻辑内容摘要仍核对。

旧本地门要求 Claude skill 收据，与用户只用 Codex 的明确要求冲突；本轮读取并应用
Codex skill-creator，由 Codex 独立静态 audit/verify，未走旧 Claude 门。
未 push；产物目录被 Git 忽略，不包含在源码提交中。后续事项归入 oprunway-todo.md。
