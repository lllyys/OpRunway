# OpRunway · AscendOpTest 桥 · 路线 B · dummy CMakeLists
#
# 唯一作用：AscendOpTest 的 run_test.py get_exe_name() 会扫描本文件，遇到首条建可执行文件
# 的 CMake 命令后，用 line.split("(")[1].strip() 抠 exe 名（run_test.py:294-303）。
# 因此在真正命令前的注释里不能出现该命令名；exe 名也必须和左括号同一行且后面不能跟源文件，
# 否则会解析成带源码或注释文字的错名，进而找不到 build/<exe> 而中止。
#
# 路线 B 不用它真正 configure/build（我们全程不带 --build）；假 exe 由 catlass build.sh 编好后
# 直接放到 <aclnn_dir>/build/execute_matmul_op。main.cpp 只是占位、无需存在。
add_executable(execute_matmul_op
    main.cpp)
