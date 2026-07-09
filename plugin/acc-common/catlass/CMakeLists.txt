# -----------------------------------------------------------------------------------------------------------
# OpRunway · catlass generated_harness 子目录 CMake（模板源）。
# deploy 期由 stage_into_catlass.sh 拷进 repos/catlass/examples/<harness>/ 并把 @HARNESS@/@RUNNER@ 替换为实名。
# 复用 examples/CMakeLists.txt 作用域已定义的 catlass_example_add_executable macro（含 include/link/arch 编译选项）。
# add_executable 相关**单独成行**防符号解析漂移（对齐 catlass 生成器换行格式）。
# ⚠ 待真机验证：Mac 无法配置 ASC 工具链，本文件仅在 ascend-a5/a3 真机 build.sh 时生效。
# -----------------------------------------------------------------------------------------------------------

set_source_files_properties(@RUNNER@ PROPERTIES LANGUAGE ASC)
catlass_example_add_executable(@HARNESS@ cube @RUNNER@)
