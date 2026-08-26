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
| msprof 列名、目录、task type 与 A3 运行链 | 待第一次真机验收回填 |
