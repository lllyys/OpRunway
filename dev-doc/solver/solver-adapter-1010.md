# 固定适配模块与样例（2026-10-10）

用户裁定：开发者在现有test/<op>下提供无main的薄适配模块，自测与验收共用；不增加acceptance目录，skill不临时生成替代适配；只用Codex。本轮完成样例和skill，不要求十算子完整验收。

## 交付

- `assets/adapter/solver_adapter.h`：版本化C协议，descriptor/create/reset/execute/readback/destroy；Host行主序传输与真实DUT的ABI/布局分开。
- `assets/adapter-sample/`：cmatinv公开接口的真实适配、自测main、CMake及调用说明。分发时另复制协议头到include/。
- 固定执行器检查描述/版本/容量、同步/清理、原始DUT状态与info、逐字节复跑；编译强制使用skill协议头，留源码/协议/公开头/执行件哈希；用实际DUT函数地址确认加载库。
- `run_harness.py --adapter`、性能及cm样例入口复用同一适配源；普通potrs/potri因子从A32独立准备，原输入留给判据。列主序默认ldb=n，显式非法leading dimension原样保留。
- 旧执行路径保留。十包、冻结基线与数值数据未改。

## 验证

均在自有远端容器执行，本机仅编辑、Git及打包。

- 两skill回归：469 passed in 20.92s。
- 样例run_sample.sh：源码构建ascend910_93成功；n8/batch4五轮执行、结构检查、确定性、实际库身份通过。
- 样例独立CMake：共享适配编译、自测2I逆为0.5I和版本/容量拒绝检查通过。
- 固定执行器错误descriptor版本实跑：退10/spec_error，0轮执行。首次手工调用漏设DUT动态库路径退127，补齐后完成该检查；标准harness自行设置路径。
- msprof op：30次，mean0.3280838958ms、median0.3303966065ms；30轮一致、库身份匹配。
- 独立Codex audit→fix→verify：修复性能collect_case二次准入阻断、样例NaN误判、列主序ldb默认与显式零值保留；无剩余必须修项。

## 证据与边界

本机reports/solver-adapter-sample-1010/包含样例归档及内部原始报告/日志；远端保留/work/adapter-final、/work/adapter-perf-final、/work/adapter-negative、/work/adapter-selftest-build与测试日志，容器工作后停止保留。

cmatinv共享harness numeric=NOT_JUDGED；开发者自测不是正式精度卡。无匹配GPU基线，性能NOT_EVALUATED。Device/950及各Cholesky具体适配仍须开发者实现、核对公开接口并在目标环境验证。协议v1不冒称覆盖零/负尺寸或多输出。未push、未改main。
