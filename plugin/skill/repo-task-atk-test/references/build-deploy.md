# 构建安装最小闭环

## 目录

- 构建
- c_api 构建与绑定
- SoC 声明门禁
- 安装
- SoC 和 op_api
- 完成条件

## 构建

只读取任务书、README 和 `build.sh --help`。

构建入口不一定在算子工程目录里。

README 指向上级仓的构建脚本时按 README 走，不要在工程目录里另找一个。

**参数组合报错时读仓库自带的编译文档（通常在 `docs/` 下），不要换组合再试。**
构建脚本的参数之间有互斥和依赖关系，`--help` 只列参数、不列这些关系，靠试
一次要几分钟。真机上连试两次才找对：`--ops` 与 `--ophost/--opapi` 互斥；
社区任务的算子放在 `experimental/` 下，不带 `--experimental` 时 cmake 根本不扫
那个目录，算子进不了 `ops_config.txt`，报的却是「算子不存在」。

社区任务算子几乎都落在 `experimental/`，**先看要不要这个开关**，再谈别的。

只用公开构建命令，不运行工程 UT/ST，不阅读实现源码。

构建命令同样以 `source evidence/env.sh` 开头，它不是 ATK 任务但一样要 CANN 环境。

真机实测：漏掉这一句时 `ASCEND_HOME_PATH` 落到空目录，cmake 报找不到 `ASCConfig.cmake`。

报错落在 cmake 上，看着像工程缺依赖，实际只是环境没加载。

每轮必须生成新的 `.run` 包，只编译验收算子。

从 `evidence/env.json` 读取 `devices.build_soc`，不要从示例或目录名猜 SoC。

构建前只检查一次 `CMakeCache.txt`。

旧工程缓存只在公开帮助支持时用 `--make_clean` 清理，并只重试一次。

`--make_clean` 是**清理完就退出**的独立动作，不是「清理后接着构建」的开关。

带着它跑完整构建命令，会在二十秒内退出码 0、日志 0 字节、产物一个没有，
看着像后台执行环境坏了。

清理跑一次，再不带它跑一次完整构建。

构建日志必须保存完整输出。

退出码非零、包路径不唯一或无法确认 vendor 时停止。

构建脚本可能调用裸 `python3`，那不一定是环境指纹里的解释器。

失败原因是构建脚本自己的解释器缺模块时，把 `selected_python` 所在目录前置到 `PATH` 重试一次，并在证据里记下这次重试。

## c_api 构建与绑定

`c_api` 有两种构建形态，按工程公开交付方式选择：

1. experimental wrapper：先用 `make_c_api_build.py` 生成 CMake，再在已加载 CANN
   环境的 shell 中执行 `cmake` 与 `make`；
2. project_build：运行 `bash build.sh --pkg --soc=<build_soc> --ops=<op>`，再运行
   `<pkg>.run --install --install-path=<dir>`。

wrapper 的生成命令如下，输入都必须位于待验收算子工程目录：

```bash
<python> scripts/make_c_api_build.py --env evidence/env.json \
  --op-dir <experimental 算子目录> --project-root <工程根目录> \
  --build-cmake <算子 test/CMakeLists.txt> -o evidence/c_api_build
```

真机构建已确认三项环境事实：

- `--ops` 会隐含 `BUILD_TEST=ON`，环境要预装 `libblas-dev` 与 `liblapack-dev`；
- 打包会从网络下载 makeself，构建命令必须继承可用的代理环境；
- 导出函数名随 SoC 架构变化，必须检查本轮真正构建的 `.so`。

第三项的实际案例是：handle 形态 `aclblasScopy` 只在 arch35 提供，A3 的 arch22
只导出 `aclblasScopy_legacy`。因此不得拿头文件里的函数集合代替动态库检查。

构建后运行导出函数名门禁；C++ mangled 形态必须唯一匹配：

```bash
<python> scripts/check_c_api_binding.py --library <绝对 .so 路径> \
  --call-sequence <op>_call_sequence.json \
  -o evidence/c_api_binding.pre.json
```

设置执行器的两个环境变量后跑最小冒烟，再用日志核对真实加载路径：

```bash
export ATK_C_API_LIBRARY=<绝对 .so 路径>
export ATK_C_API_CALL_SEQUENCE=<绝对调用序列表路径>
<python> scripts/check_c_api_binding.py --library "$ATK_C_API_LIBRARY" \
  --call-sequence "$ATK_C_API_CALL_SEQUENCE" --executor-log evidence/smoke.log \
  -o evidence/c_api_binding.runtime.json
```

执行器必须原样打印这条合同记录：

```text
[c_api_executor] loaded_library=<absolute realpath> exported_name=<resolved name>
```

## SoC 声明门禁

构建结束立刻扫日志，不要等安装和冒烟：

```bash
<python> scripts/check_soc_binding.py --env evidence/env.json \
  --build-log evidence/build.log -o evidence/soc_declared.json
```

退出码 3 表示算子本身没有声明真机的构建族。

这是 S3 结构性阻塞：立即出具「阻塞·未验收 @S3」，解除条件是算子侧补该 SoC 声明，或换一台该算子声明支持的真机。

**不得改用其他 SoC 重建、安装或冒烟。**

换掉的是验收前提，不是构建参数。

换出来的包在真机上照样加载失败，只是把同一个结论推迟十几分钟。

退出码 2 是另一回事：包不完整或指纹缺字段，属可修复失败。

## 安装

将本轮包安装到已确认 CANN OPP 根目录：

```bash
<run-package> --install-path=<cann-root>/opp
```

`--install-path` 不是可选项：不带它，安装脚本走 config.ini 的默认路径，不生成 vendor 的 `bin/set_env.bash`。

安装日志必须对应本轮包。

安装后加载本轮 vendor 的 `bin/set_env.bash`。

加载之后重新生成一次环境载体，把 vendor 环境和待验收算子库路径并进去：

```bash
<python> scripts/probe_env.py --device <N> -o evidence/env.json \
  --env-sh evidence/env.sh \
  --vendor-env <install-root>/vendors/<vendor>/bin/set_env.bash \
  --custom-opp <absolute-candidate-library>
```

不重新生成时 `evidence/env.sh` 停留在 S1 的内容，冒烟会加载不到本轮待验收算子库。

脚本不存在时先核对上一条命令带没带 `--install-path`，带了仍不存在才停止。

运行：

```bash
<python> scripts/check_soc_binding.py \
  --env evidence/env.json \
  --vendor-root <install-root>/vendors/<vendor> \
  -o evidence/soc_binding.json
```

包缺少所选构建族配置或 kernel 时停止。

## 待验收算子 op_api

pyaclnn 使用本轮 vendor 内唯一的 `libcust_opapi.so`。

预检：

```bash
<python> scripts/check_opapi_binding.py \
  --library <absolute-library> \
  --aclnn-name <yaml-aclnn-name> \
  -o evidence/opapi_binding.pre.json
```

设置 `ATK_CUSTOM_OPP_PATH` 为该 `.so` 的绝对路径，不要使用目录或其他选择器。

`probe_env.py --custom-opp` 会在工作目录下建一个指向它的软链接，并让
`ATK_CUSTOM_OPP_PATH` 指向软链接。

原因见下一节，不要改回直接指到装机目录里。

冒烟后使用 `--atk-log` 再核对实际加载路径。

冒烟必须启用 `--cpp_func_signature_type_path`。

## 签名自检搜的是磁盘上的同名头文件

`--cpp_func_signature_type_path` 打开的是 ATK 的签名自检。它不读你给的任何
头文件路径，而是拿用例 JSON 里的 `aclnn_name` 拼出
`aclnnStatus aclnn<Name>GetWorkspaceSize`，选一个目录 `grep -r --include=*.h`，
**取第一个匹配的文件**当作待验收算子的签名。

选目录的逻辑按 `ATK_CUSTOM_OPP_PATH` → `ASCEND_CUSTOM_OPP_PATH` →
`ASCEND_OPP_PATH` 依次走，且**后一个会覆盖前一个**。`ASCEND_OPP_PATH` 由 CANN
`set_env.sh` 设置、torch_npu 与 ATK 自身的 import 都要它，所以它必然存在、必然
最后生效，最终目录落在 **CANN 装机根**。

于是待验收算子只要与 CANN 官方算子重名（社区算子基本都重名），搜到的就是官方
那份头文件。加载库和搜头文件是两套逻辑：库那边命中 `ATK_CUSTOM_OPP_PATH` 就
立刻返回，所以**库是对的、头文件是错的**，日志里两行并排出现：

```text
import aclnn<Name>GetWorkspaceSize from <本轮 vendor>/libcust_opapi.so success!
自动进行参数校验，头文件路径是：/usr/local/Ascend/<cann 版本>
```

锁定办法：把 `ATK_CUSTOM_OPP_PATH` 指到 `opp/` **外面**的一个软链接，
第三个分支的公共路径判断就不成立，目录停在本轮 vendor：

```bash
mkdir -p evidence/atk_custom
ln -sf <opp>/vendors/<vendor>/op_api/lib/libcust_opapi.so \
       evidence/atk_custom/libcust_opapi.so
export ATK_CUSTOM_OPP_PATH=$PWD/evidence/atk_custom/libcust_opapi.so
```

`probe_env.py --custom-opp` 已经代做这一步。

搜错了 ATK 不会停：参数对不上时它丢入参、改输出绑定或调换入参顺序，只打一条
warning 继续跑，冒烟和全量都会绿灯，而跑的不是工程声明的签名。
`check_opapi_binding.py --atk-log` 认这两条 warning 为 S3 不通过。

这是 ATK 26.8.8 的已知问题，按红线 2 不改 `atk/`，只从环境侧锁定。

冒烟跑哪条用例取 `evidence/frozen_inputs.json` 的 `smoke_case`，不要默认取 0 号。

`-s/-e` 直接抄同一份文件的 `smoke_range`，它已经把用例号换算成序号区间。

冒烟门禁要求成功 1 条、失败 0 条，所以它只能跑常规通路：定向用例本来就该报错或被拒绝，拿它冒烟会让构建安装的几十分钟连带作废，而待验收对象没问题。

默认签名不匹配时只能按公开契约提供最小适配器。

不得猜测隐式参数、空指针或参数顺序。

## 完成条件

构建日志、安装日志、SoC 报告和待验收算子库都属于本轮。

当前 shell 已加载 vendor 环境。

待验收算子 op_api 通过预检和运行期路径核对。

ACLNN 通过头文件 ABI 校验。

正式冒烟确认待测的接口名、参数装载和 device 三项都对。

冒烟失败不触发重新编译，除非日志明确指向部署问题。
