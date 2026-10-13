# gels 冻结参考修复（2026-10-13）

> 更正：下文关于A64/B64错误、必须换基及强制重冻的结论已撤回；相关代码已回退。当前状态见 [solver-gels-basis-revert-1013.md](solver-gels-basis-revert-1013.md)。本文保留为历史记录。

用户授权修复冻结参考读取与参数传递，保留不测试约束。核对生成链发现旧 gels
参考残差使用 A64/B64，而实际判定使用 A32/B32；方阵与超定都受影响。

## 修改

- render_verify 冻结加载器读取并校验超定 ratio_opt_cpu；materialize 仅重建输入。
- fill_ratio_cpu 的 gels 链改用 A32/B32；独立回填和 build_package 成功后写入
  ratio_input_basis=A32-f64。旧包顶层已错误声称 A32-f64，因此新增逐条标记区分新旧；
  读侧拒绝缺标记的 gels 正常参考值，不能手工给旧值补标记。
- accept_run 注入 m/n、side/trans、ratio_opt_cpu；gels 旧口径或缺最优性参考记证据不足。
- 修正模拟 stream_check 的提示，明确其现场CPU参考不构成冻结验收证据。
- 更新规则说明、README模板及 sgels/cgels 两包精度脚本和摘要；其余14包不动。

## 产物与证据

reports/solver-luqr-1013/gels-frozen-fix.md 与同名JSON记录本次静态核对。
两个包的3个函数直接从权威模板字符串提取同步；未执行renderer、生成器或测试。
所有修改Python文件完成AST语法读取；两包manifest全文件摘要匹配；index、canonical、
GPU基线及输入生成脚本与原交接版字节一致。旧复制台账和audit为导入阶段快照，
本次状态由管理INDEX的新段落与gels-frozen-fix记录说明。

独立Codex静态 audit→fix→verify：核心逻辑无阻断问题；模拟路径提示已修；
verify期间发现的README摘要因文档编辑时序滞后，收口已刷新并逐项核对。
未运行项目测试、构建或真机操作；未push。

## 未完成项

sgels/cgels各166个普通用例的CPU精度参考需要在远程服务器CPU重新冻结，
保持记录的Python/NumPy/SciPy/BLAS环境。用户本轮未要求启动重算；原index保留，
新脚本会拒绝这些旧条目。两包尚不是可直接验收最终版；GPU性能基线不需重算。
共享harness、正式性能接入是另外的遗留。本轮未核清其它LUQR族历史参考值口径，
不得把本次gels静态修复扩称为全部LUQR参考值已验证。
