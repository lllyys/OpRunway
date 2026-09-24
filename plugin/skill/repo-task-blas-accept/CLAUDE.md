# repo-task-blas-accept 开发约束

## 专属红线

- 开发者工程的 tracked 源文件只读；验收不得 patch C++、CSV、构建脚本或公开头文件。
- `build.sh` 在工程内产生的 `build/`、`build_out/`、`out/` 是允许的构建副产物。
- 不修改 host 环境、设备状态或其他进程；缺前置时报告阻塞，不代装、不 kill、不 reset。
- 结论只认部署 CSV 和本 run-id 的 JSON；旧结果、包内未部署 CSV 与终端摘要不能替代。

## 真机验证过的事实

| 事实 | 出处 |
| --- | --- |
| `--device` 写入编译期 `TEST_DEVICE_ID` | `build.sh:17,80-81,276` |
| CSV 从 `__FILE__` 同目录的同名文件加载 | `test/frame/csv_loader.h:115-122` |
| A1 env 正确检出真机工具链（cmake/g++/build.sh/frame/header/CANN/msprof/cblas） | 实测（A3，sger） |
| npu-smi 在容器内解析不出设备（记"未知"，非门禁）——探针脆弱，待改 | 实测（A3） |
| 精度 harness 真机通：build-skip→list→名字映射→gtest json→结果 JSON | 实测（A3，sger TC_L0 全 PASS） |
| 性能 `msprof op` 流水线真机通：单次采集 + gtest JSON 执行证据 + 解析 | 实测（A3；msprof 后端 sger 全链 + ctpmv 200 例，2026-09-14；2026-09-23 换 msprof op 后复测 ctpmv） |
| kernel_us 单次采样可复现：同例跨进程偏差 ≤3% | 实测（msprof op，同例 6 次 15.40–16.46 us） |
| 换后端是口径变更不是等价替换：`msprof op` 读数为旧 `msprof` 的 0.64–0.84 倍，绝对差恒定 8.5–11 us（重放绕开首次调用惩罚） | 实测（2026-09-23 同机双跑 TC_PF_1001/1002/1003：23.78→15.30、45.40→35.72、68.50→57.26 us） |
| 两种口径谁更接近 GPU 对照组无法判定，换后端不等于更准 | GPU 基线 `perf.meta` 六个键当前全是 `unspecified`（含 `warmup`） |
| `msprof op` 同样不透传被测程序失败；但工具自身参数或初始化失败会退非零（`--ai-core=on` 退 255） | 实测（2026-09-23，CANN 9.0.1） |
| `OpBasicInfo.csv` 九列，无 `Task Type` 列；kernel_us 取全部数据行 `Task Duration(us)` 求和 | 实测（A3，ctpmv/sasum，2026-09-23） |
| `OpBasicInfo.csv` 的 `Device Id` 列是物理卡号 | 实测（A3；`ASCEND_RT_VISIBLE_DEVICES=3` 时该列报 3，不是逻辑 0） |
| 落卡核对已撤除，换卡是否生效只有量具自报的 `device_resolved` 一个来源 | 裁定（2026-09-23，Mr.0）；想恢复独立核对就让 accept 读 `Device Id` 列 |
| 基线重复键首行生效贯穿 A2/A4/A5（同键处处同选择）；verdict 只认 cwd 的 check.json，--out 外指不影响结论 | 实测（A3，sger 小包全链） |
| accept 运行链用现成算子只验机械，不验真算子正确性（需契约 C++ test） | 设计边界 |
