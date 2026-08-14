# 构建与交付布局：被测仓族客观事实

## 0 · 绑定对象与核实基准

本页只记录**被测算子仓族（CANN 开源算子仓，`build.sh` + CMake + makeself 自解包）客观如此**的事实，
不含任何本项目的取舍、参数选择或判据。

核实所依据的两个同族仓与 commit：

| 代号 | 说明 | commit |
|---|---|---|
| 仓 A | 本轮实际被测仓，仓内带一次完整 `--pkg` 构建的中间产物与成品包 | `ddfbc6630` |
| 仓 B | 同族对照仓，仅静态读码 | `244dcee` |

写「本族」的条目，表示在 A、B 两仓都逐字核到；写「仅一仓成立」的条目已单独标注，**不得当通则用**。

三路证据：① 仓内脚本与 CMake 源码；② 仓 A 的 `build/CMakeCache.txt` 与 CPack 暂存树（真实构建落盘）；
③ 从仓 A 成品 `.run` 里解出的包内 `install.sh`（未执行，只按 makeself 头部 `skip` 偏移解包读取）。

**版本绑定**：以上均随仓版本漂移。换 commit、换族内其它仓、换 CANN 打包器版本后，本页每条都须重核。

---

## 1 · 构建脚本客观形态

### 1.1 `--experimental` 是排他二选一，不是追加

顶层 `CMakeLists.txt` 按 `ENABLE_EXPERIMENTAL` 走 `if/else`：开了就**只**扫描实验目录，
**不再扫描常规算子目录**。

- 仓 A `CMakeLists.txt:124-130`：`if(ENABLE_EXPERIMENTAL)` → `add_subdirectory(experimental)`；
  `else()` → `foreach(ops_category ${OPS_CATEGORY_LIST}) add_subdirectory("${ops_category}") endforeach()`。
- 仓 B `CMakeLists.txt:131-143` 同形（`else()` 分支里逐个 `add_subdirectory` 常规目录）。
- 命令行开关到 CMake 变量的映射：仓 A `build.sh:890-891`、仓 B `build.sh:934-935` 置
  `ENABLE_EXPERIMENTAL=TRUE`；
  仓 A `build.sh:1025-1026`、仓 B `build.sh:1135-1136` 追加 `-DENABLE_EXPERIMENTAL=TRUE`。

### 1.2 实验目录依赖常规算子时的补偿分支（**仅一仓成立**）

仓 A `cmake/func.cmake:504-519` 有一个补偿分支，把被依赖的常规算子目录重新加回来。注释原文：

> `# NEED_COMPILE_OPS 为空表示全部编译，则不需要特意添加目录；但指定experimental时未扫描常规算子，如果依赖常规算子，需要添加目录`

条件是 `if((NEED_COMPILE_OPS OR outside_experimental) AND ...)`，其中
`set(outside_experimental ${ENABLE_EXPERIMENTAL})`（`cmake/func.cmake:486`、`:493`）。

**仓 B 的 `cmake/func.cmake` 全文 grep `outside_experimental` 零命中**——这条不是族通则。

### 1.3 CMake 缓存里的键：拼写、类型与语义差异

仓 A 真实 `build/CMakeCache.txt` 逐字抄录（该轮为单 SoC、单算子、custom 打包）：

```
ASCEND_COMPUTE_UNIT:STRING=<soc>
ASCEND_OP_NAME:STRING=<op_snake>
COMPILED_OPS:STRING=<op_snake>
COMPILED_OP_DIRS:STRING=<算子源码目录绝对路径>
NEED_COMPILE_OPS:STRING=<op_snake>
VENDOR_NAME:STRING=<vendor>
ENABLE_BINARY:BOOL=TRUE / ENABLE_CUSTOM:BOOL=TRUE / ENABLE_EXPERIMENTAL:BOOL=TRUE / ENABLE_PACKAGE:BOOL=TRUE
OP_CACHE_<op_snake>_<soc>:INTERNAL=<OpType>
```

声明处（本族）：

- `ASCEND_COMPUTE_UNIT` / `ASCEND_OP_NAME` / `VENDOR_NAME`：仓 A `CMakeLists.txt:64`、`:69`、`:70`；
  仓 B `CMakeLists.txt:64`、`:69`、`:70`。三者都是 `CACHE STRING`。
- `NEED_COMPILE_OPS` / `COMPILED_OPS` / `COMPILED_OP_DIRS`：仓 A `cmake/variables.cmake:29`、`:31`、`:32`；
  仓 B `cmake/variables.cmake:35`、`:37`、`:38`。均带 `FORCE`，所以必然出现在缓存文件里。

**三个算子清单键语义不同，不可互换：**

- `ASCEND_OP_NAME` 是用户入参（`--ops` 的逗号列表被换成分号后灌入：仓 A `build.sh:980-982`、仓 B
  `build.sh:1071`）；
- `NEED_COMPILE_OPS` 是它的 `FORCE` 副本：`set(NEED_COMPILE_OPS "${ASCEND_OP_NAME}" CACHE STRING ... FORCE)`；
- `COMPILED_OPS` 才是遍历后**实际被编译**的算子累积结果，并被后续流程当作真源消费
  （例：仓 A `cmake/gen_ops_info.cmake:504-507` 用 `OP_LIST ${COMPILED_OPS} IMPL_DIR ${COMPILED_OP_DIRS}`）。

**目标绑定键的拼写与取值形态**（本族）：`set(cache_key "OP_CACHE_${op_name}_${compute_unit}")`
（仓 A `cmake/gen_ops_info.cmake:63`、仓 B `cmake/gen_ops_info.cmake:64`），
写入处 `set(${cache_key} "${op_type}" CACHE INTERNAL "Cached op_type for ${op_name} on ${compute_unit}")`
（仓 A `:117`、仓 B `:112`）。即键 = `OP_CACHE_<snake 算子名>_<soc>`，值 = 该算子的 camelCase OpType，
类型是 `INTERNAL`（落在 CMakeCache.txt 末尾的 INTERNAL 段）。

**该键可能合法缺席**：仓 A `cmake/gen_ops_info.cmake:80-111` 有多个提前 `return`
（无 `op_kernel` 目录、从 `<op>_binary.json` 取不到 op_type、`check_op_supported` 不通过），
这些路径**不会**走到 `:117` 的写缓存。仓 B `:88`、`:95`、`:103` 在同类分支上改为写入空串。
两种形态都意味着：**键不存在或值为空，不能反推「构建口径失效」**。

### 1.4 构建产物落点与缓存重建

本族：`BUILD_PATH="${BASE_PATH}/build"`、`BUILD_OUT_PATH="${BASE_PATH}/build_out"`
（仓 A `build.sh:97-98`、仓 B `build.sh:101-102`），`BASE_PATH` 即 `build.sh` 所在的仓根
（仓 A `build.sh:93-96`）。`CMakeCache.txt` 落在前者，`.run` 落在后者，两者都在源码树内。

每轮 cmake 之前会先删掉旧缓存：仓 A `build.sh:1130`
`[ -f "${BUILD_PATH}/CMakeCache.txt" ] && rm -f ${BUILD_PATH}/CMakeCache.txt`；仓 B `build.sh:1198` 同形。
所以「本轮出现一份全新缓存文件」是脚本行为保证的。

### 1.5 选项默认值：两层默认，且文档与代码可能不一致

- 并发：`THREAD_NUM=8`（仓 A `build.sh:691`；仓 B `build.sh:707-708` 先取 `${CORE_NUMS}`、下一行立刻被 `8`
  覆盖），
  `-j` 覆盖之（仓 A `build.sh:807`、仓 B `build.sh:854`）。
- 厂商名是**两层默认**：`build.sh` 里初值是空串（仓 A `:699`、仓 B `:716`），且只在非空时才下发
  （仓 A `build.sh:984-986`，仓 B `:1074` 同形）：

  ```
  if [[ -n $VENDOR_NAME ]]; then
    CMAKE_ARGS="$CMAKE_ARGS -DVENDOR_NAME=$VENDOR_NAME"
  fi
  ```

  不传时真正生效的是 CMake 侧 `CACHE STRING` 默认值。
- **两仓帮助文本对「默认厂商名」的说法不同**（仓 A `build.sh:389`、仓 B `build.sh:431` 的 `default to ...`），
  而两仓 `CMakeLists.txt:70` 的 CACHE 默认值是同一个字面量。仓 A 已由实测证实以 CMake 侧为准
  （见 3.2）；仓 B 未实测。**结论：帮助文本里的默认值不能当事实用，须以 CMake CACHE 默认值为准。**
- `--soc` 有受控白名单，两仓各 13 项、集合相同但书写顺序不同（仓 A `CMakeLists.txt:71` 与 `build.sh:388`；
  仓 B `CMakeLists.txt:65` 与 `build.sh:430`）。具体型号请现场读仓内文件。
- `--ops` 取 snake 名、逗号分隔（仓 A `build.sh:386`、仓 B `build.sh:428`），下发前逗号被换成分号。
- 命令行到 CMake 的三个映射键：`-DASCEND_OP_NAME` / `-DVENDOR_NAME` / `-DASCEND_COMPUTE_UNIT`
  （仓 A `build.sh:982`、`:985`、`:1113`；仓 B `build.sh:1071`、`:1074`、`:1177`）。

---

## 2 · 安装包客观形态

以下 2.1–2.5 全部核自仓 A 成品包内解出的 `install.sh`（下称「包内脚本」）与 makeself 头部；
仓 B 未产包，故本节标「仓 A 实测」，族通用性未验证。

### 2.1 `.run` 外层是 makeself 自解包

包头逐字：`# This script was generated using Makeself 2.5.0`；`script="./install.sh"`；`skip="697"`；
`label="version:1.0"`；压缩为 gzip（头部 `MS_Decompress()` 里 `eval "gzip -cd"`）。

包内 `help.info` 三行逐字：

```
  --install-path                    Install operator package to specific dir path
  --install-for-all                 Allow other users to use the operator package
  --force                           Skip shared library validation and continue installation
```

makeself 外层自己另有 `-q | --quiet`（头部 `:234`、`:409-410`），默认 `quiet="n"`；置 `y` 后会把
`--quiet ` 追加进 `scriptargs` 再传给内层脚本（头部 `:521-525`）。即 `--quiet` 同时被外层与内层消费。

### 2.2 包内脚本的选项解析

`while true / case $1 in` 循环（包内 `install.sh:29-56`）：

- `--quiet)`（`:32`）——但 `QUIET="y"` 在 `:22` 已是初值，传与不传对内层无差别；
- `--install-path=*)`（`:36-39`）——**等号形式**，`INSTALL_PATH=$(echo $1 | cut -d"=" -f2-)`，
  随后 `INSTALL_PATH=${INSTALL_PATH%*/}` 剥尾部斜杠；
- `--install-for-all)`（`:41`）、`--force)`（`:45`）；
- `--*)` 兜底分支只 `shift`（`:49-51`）——**任何未识别的长选项都被静默丢弃，不报错**；
- `*)` 直接 `break`（`:52-54`）——遇到第一个非 `--` 开头的实参就停止解析。

因此选项顺序无约束，也无法由脚本校验；拼错的长选项不会有任何提示。

### 2.3 安装根的三级回退与硬约束

包内 `install.sh:351-380`，按优先级：

1. `--install-path` 给了值 → 必须是绝对路径，否则报
   `[ERROR] use absolute path for --install-path argument` 并 `exit 1`（`:352-355`）；
   目录不存在则以 750 创建（`:356-361`）；
2. 否则若 `$ASCEND_CUSTOM_OPP_PATH` 非空 → 取它。**其中含冒号（多路径）直接 `exit 1`**（`:364-369`）；
3. 否则取 `$ASCEND_OPP_PATH`；为空则 `log "[ERROR] env ASCEND_OPP_PATH no exist"` 并 `exit 1`（`:375-378`）。

脚本顶部 `targetdir=/usr/local/Ascend/opp`（`:16`）是初值，但三条分支都会重新赋值 `targetdir`，
**这个硬编码路径在包内脚本里实际不可达**。也就是说：不传 `--install-path` 时落点完全由环境变量决定。

已核到的上游缺陷：第 2 条分支里创建目录用的是拼错的变量名
`create_dir "${INSASCEND_CUSTOM_OPP_PATHTALL_PATH}" "750" "${INSTALL_FOR_ALL}"`（`:371`），
且该调用未检查返回值——该分支下目录不存在时会静默创建失败，随后由 `:382-385` 的 `no exist` 判死。

### 2.4 安装前的动态库校验门

包内 `install.sh:265-288` 的 `validate_shared_libraries`，在写入任何文件之前调用（`:583-588`）：

- `--force` 置位时整门跳过：`log "[WARNING] --force is enabled, skip shared library
  validation."`（`:269-272`）；
- `$ASCEND_HOME_PATH` 非空 → dlopen 模式：用 `python3` + `ctypes.CDLL(path, mode=os.RTLD_NOW)`
  实际加载（`:159-170`）；
- 否则 → 兼容模式：`readelf -h` 取 `Machine:` 比对架构（`:187-201`），`readelf -V` 取最高 `GLIBC_x.y` 比对
  glibc（`:212-225`）。

受检的三个库固定为 `libcust_opmaster_rt2.0.so`、`libcust_opsproto_rt2.0.so`、`libcust_opapi.so`
（`:13-15`、`:238`）。**只有 `libcust_opmaster_rt2.0.so` 缺失是硬失败**
（`log "[ERROR] Required shared library ... was not found in package path."` 并 return 1，`:245-248`）；
另两个缺失只 `continue`（`:249`）。失败时的收尾行逐字：

> `[ERROR] Shared library validation failed. To skip this validation and continue installation, run this package again with --force.`（`:586`）

### 2.5 安装动作的副作用与结束标志

包内 `install.sh:563-581`：先建 `${targetdir}/vendors`，再建 `${targetdir}/$vendordir`，权限 750。

收尾分两条互斥路径（`:632-673`）：

- **给了 `--install-path`** → 在 `<厂商根>/bin/set_env.bash` 写入：
  `export ASCEND_CUSTOM_OPP_PATH=<厂商根>:${ASCEND_CUSTOM_OPP_PATH}` 与
  `export LD_LIBRARY_PATH=<厂商根>/op_api/lib/:${LD_LIBRARY_PATH}`（`:634-643`）。
- **没给** → 改写共享注册表 `${targetdir}/vendors/config.ini`，写/更新 `load_priority=<厂商目录名>`
  （`:652-669`）；此时不生成 `set_env.bash`。

顺序：framework → op_proto → op_impl → op_api → uninstall → version.info → proto（`:590-630`），
任一步非 0 即 `exit 1`。全部成功时 stdout 末行逐字为 `SUCCESS`，`exit 0`（`:679-680`）。

### 2.6 仓内 `scripts/custom/install.sh` 不是包内那一份

两者是不同文件，不能相互替代：

| 维度 | 仓内 `scripts/custom/install.sh` | 包内 `install.sh`（仓 A 实测） |
|---|---|---|
| 行数 | 仓 A 约 300 行 | 680 行 |
| 日志前缀 | `[runtime]`（仓 A `:51`、仓 B `:43`） | `[ops_custom]`（`:60`） |
| 厂商名字面量 | 两仓各写死一个 `customize-<族后缀>`（仓 A `:19`、仓 B `:11`） | `<vendor_name>_<族后缀>`（`:12`） |
| `--install-for-all` / `--force` | 无 | 有 |
| so 校验门 / `set_env.bash` / `config.ini` | 无 | 有 |

包由仓外的 `npu_op_package`（来自 `CANN_CMAKE_DIR`，见 `cmake/package.cmake:13`、`:28`）生成，
仓内那份脚本没有进入成品包。**读仓内脚本推断安装行为会得到错误结论。**

---

## 3 · 交付布局

### 3.1 厂商根相对安装根**恰好一层**

`<安装根>/vendors/<厂商目录名>`。依据：包内 `install.sh:20` `vendordir=vendors/$vendor_name`，
`:563` 建 `${targetdir}/vendors`，`:573` 建 `${targetdir}/$vendordir`；而 `targetdir` 就是 `--install-path`
给的安装根本身（`:362`）。CMake 侧同构：`packages/vendors/${PATH_NAME}/...`
（仓 A `cmake/variables.cmake:60-76`、仓 B `cmake/variables.cmake:59-76`）。

### 3.2 厂商目录名 ≠ `--vendor_name`，会被追加族后缀

`set(PATH_NAME "${VENDOR_NAME}_<族后缀>")`，且这个 `PATH_NAME` 才是交给打包器的 `VENDOR_NAME`：
仓 A `cmake/package.cmake:27`、`:34`；仓 B
`cmake/package.cmake:25`、`:32`。族后缀是每仓各自写死的字面量，两仓不同。

仓 A 实测闭环：`CMakeCache.txt` 里 `VENDOR_NAME:STRING=<vendor>`，而暂存树与包内脚本里的厂商目录名都是
`<vendor>_<族后缀>`（包内 `install.sh:12`）。**任何「厂商根名就是 `--vendor_name` 逐字」的说法都是假的。**

同源提醒：`.run` 的文件名规则两仓写法不同（仓 A `cmake/package.cmake:26` 用
`..._${VENDOR_NAME}_linux-${ARCH}`，
仓 B `cmake/package.cmake:24` 用 `...-${VENDOR_NAME}-linux.${ARCH}`，分隔符与点号位置都不一样），
**不能按固定模式猜包文件名**。

### 3.3 厂商包的目录层级

CMake 侧声明（`ENABLE_CUSTOM` 分支，仓 A `cmake/variables.cmake:56-76`、仓 B `:55-76`，逐条一一对应）：

```
<厂商根>/op_api/include/                                        # ACLNN_INC_INSTALL_DIR
<厂商根>/op_api/lib/                                            # ACLNN_LIB_INSTALL_DIR
<厂商根>/op_impl/ai_core/tbe/config/                            # OPS_INFO_INSTALL_DIR
<厂商根>/op_impl/ai_core/tbe/<厂商目录名>_impl/ascendc/          # IMPL_INSTALL_DIR
<厂商根>/op_impl/ai_core/tbe/<厂商目录名>_impl/dynamic/          # IMPL_DYNAMIC_INSTALL_DIR
<厂商根>/op_impl/ai_core/tbe/kernel/                            # BIN_KERNEL_INSTALL_DIR
<厂商根>/op_impl/ai_core/tbe/kernel/config/                     # BIN_KERNEL_CONFIG_INSTALL_DIR
<厂商根>/op_impl/ai_core/tbe/op_tiling/                         # OPTILING_INSTALL_DIR
<厂商根>/op_impl/ai_core/tbe/op_tiling/lib/linux/<arch>/        # OPTILING_LIB_INSTALL_DIR
<厂商根>/op_impl/cpu/aicpu_kernel/impl/                         # AICPU_KERNEL_IMPL
<厂商根>/op_impl/cpu/config/                                    # AICPU_JSON_CONFIG
<厂商根>/op_proto/inc/                                          # OPPROTO_INC_INSTALL_DIR
<厂商根>/op_proto/lib/linux/<arch>/                             # OPPROTO_LIB_INSTALL_DIR
<厂商根>/version.info                                           # VERSION_INFO_INSTALL_DIR
```

仓 A 实测（真实 CPack 暂存树与 `.run` 内 tar 清单）落到磁盘的形态：

```
<厂商根>/op_api/include/aclnn_<op_snake>.h
<厂商根>/op_api/include/aclnn_ops_<族>_custom.h        # 聚合头
<厂商根>/op_api/lib/libcust_opapi.so
<厂商根>/op_impl/ai_core/tbe/op_tiling/liboptiling.so
<厂商根>/op_impl/ai_core/tbe/op_tiling/lib/linux/<arch>/libcust_opmaster_rt2.0.so
<厂商根>/op_proto/inc/<op_snake>_proto.h
<厂商根>/op_proto/lib/linux/<arch>/libcust_opsproto_rt2.0.so
<厂商根>/version.info                                  # 内容形如 custom_opp_compiler_version=<版本>
```

仓 B 多一个 `BIN_STATIC_INSTALL_DIR = .../op_impl/ai_core/tbe/static`（仓 B `cmake/variables.cmake:65`），
仓 A 无此项——**不是族通则**。

### 3.4 与目标 SoC 相关的三类交付文件（两个 `config` 目录同名不同层，易混）

```
① 算子信息文件： <厂商根>/op_impl/ai_core/tbe/config/<soc>/aic-<soc>-ops-info.json
② kernel 二进制配置：<厂商根>/op_impl/ai_core/tbe/kernel/config/<soc>/binary_info_config.json
                     <厂商根>/op_impl/ai_core/tbe/kernel/config/<soc>/<op_snake>.json
③ kernel 文件：     <厂商根>/op_impl/ai_core/tbe/kernel/<soc>/<op_snake>/<OpType>_<32位hex>_<implMode>.o
                     同目录同名 .json 与之成对
```

**①②不在同一层**：① 在 `tbe/config/<soc>/`，② 在 `tbe/kernel/config/<soc>/`。把两者写成同层是错的。

生成/安装依据（本族）：

- ①：仓 A `cmake/gen_ops_info.cmake:181-208`，头部注释即
  `# packages/vendors/${VENDOR_NAME}/op_impl/ai_core/tbe/config/${compute_unit}`；文件名由
  `aic-${OPINFO_COMPUTE_UNIT}-ops-info.ini` 经 `scripts/util/parse_ini_to_json.py` 转出（`:195-198`）。
- ②：仓 A `cmake/gen_ops_info.cmake:456-492`，注释
  `# packages/vendors/${VENDOR_NAME}/op_impl/ai_core/tbe/kernel/config`；per-op 的 `<op_snake>.json` 在
  `:396-401` 的 `install(FILES .../bin/config/<soc>/${CONFCMP_OP_NAME}.json)`。
- ③：仓 A `cmake/gen_ops_info.cmake:390-395`：

  ```
  install(
    DIRECTORY ${CONFCMP_OUT_DIR}/bin/${CONFCMP_COMPUTE_UNIT}/${CONFCMP_OP_NAME}
    DESTINATION ${_KERNEL_BIN_INSTALL_DIR}
    OPTIONAL
    )
  ```
- 另有可选的 `_apt` 变体：`<op_snake>_apt` 目录与 `<op_snake>_apt.json`（仓 A `:402-412`），带 `OPTIONAL`。
- 上述 `install()` **全部带 `OPTIONAL`**——生成物缺失时安装规则静默跳过，不报错。

### 3.5 kernel 二进制清单的字段与相对基准

仓 A 实测 `binary_info_config.json` 结构（逐字）：

- 顶层键是 **camelCase 的 OpType**（不是 snake 算子名）；
- 其下有 `dynamicRankSupport`、`simplifiedKeyMode`、`optionalInputMode`、`optionalOutputMode`、
  `params`（含 `inputs` / `outputs` / `attrs`）、`binaryList`；
- `binaryList[]` 每项含 `coreType`、`simplifiedKey`（字符串数组）、`binPath`、`jsonPath`；
- `binPath` / `jsonPath` 是**相对路径**，形如 `<soc>/<op_snake>/<OpType>_<32位hex>_<implMode>.o`
  与同名 `.json`。

**相对基准是 `.../op_impl/ai_core/tbe/kernel/` 目录，不是清单文件自身所在目录**：
清单在 `kernel/config/<soc>/`，而 `binPath` 指向的文件实际在 `kernel/<soc>/<op_snake>/` 下——仓 A 逐文件对上。

per-op 的 `kernel/config/<soc>/<op_snake>.json` 是另一种结构：顶层 `binList[]`，每项含
`implMode`、`int64Mode`、`simplifiedKeyMode`、`simplifiedKey`、`optionalInputMode`、`optionalOutputMode`、
`staticKey`、`inputs`、`outputs`、`attrs`。文件名用的是 **snake 算子名**。

### 3.6 算子信息文件的字段

仓 A 实测 `aic-<soc>-ops-info.json`：顶层键是 **camelCase 的 OpType**；每个 OpType 下的键包括
`attr`（`{"list": "<逗号分隔属性名>"}`）、`attr_<属性名>`、`coreType`、`dynamicCompileStatic`、
`dynamicFormat`、`dynamicRankSupport`、`dynamicShapeSupport`、`input<N>`、`output<N>`、
`needCheckSupport`、`opFile`、`opInterface`、`prebuildPattern`、`precision_reduce`。

其中 `opFile` 与 `opInterface` 都是 `{"value": "<snake 算子名>"}` 形态；
`input<N>` / `output<N>` 含 `dtype`、`format`、`name`、`paramType`、`shape`、`unknownshape_format`，
`dtype` 与 `format` 都是逗号分隔的等长列表。

### 3.7 三种「存在」互不等价（仓 A 实测）

同一次单 SoC 构建的包里同时出现：

- `tbe/config/` 下**13 个 SoC 子目录全部存在**，其中 10 个是空目录（tar 清单里以 `/` 结尾的空条目）；
- `aic-<soc>-ops-info.json` 只在 **3 个** SoC 下存在——正好对应 `build/tbe/config/` 里字节数非 0 的
  3 份 `.ini`（其余 10 份为 0 字节）；
- `kernel/<soc>/` 与 `kernel/config/<soc>/` 只有 **1 个** SoC——即本轮 `ASCEND_COMPUTE_UNIT` 的取值。

结论（客观）：
**SoC 目录存在 ⇏ 有算子信息；有算子信息 ⇏ 该 SoC 的 kernel 二进制被编译**。
算子信息文件按算子注册覆盖的 SoC 集合生成，kernel 文件按本轮构建的目标 SoC 生成，两者的 SoC 集合不同。

### 3.8 built-in（非 custom）形态多一层族目录

同一批变量在 `ENABLE_CUSTOM` 为假时改成 `opp/built-in/...` 前缀（仓 A `cmake/variables.cmake:79-100`、
仓 B `:79-100`），且 kernel 侧**多出一层 `ops_<族>`**：

- 仓 A `cmake/gen_ops_info.cmake:386-388`、`:484-485`：`${BIN_KERNEL_INSTALL_DIR}/${compute_unit}/ops_<族>`；
- 仓 B `cmake/gen_ops_info.cmake:424-425`、`:528` 同形，族名字面量不同。

custom 形态**没有**这一层。两种形态的路径不可互相套用。

---

## 4 · 本次未写入的内容

以下都判为**本项目的取舍或判据**，不是被测仓的客观属性，故不入本页：

1. 固定的构建命令 argv 模板、参数顺序、`bash` 前缀、工作目录约定；
2. 四个构建参数各自「从哪个字段取值」的映射，以及并发数取值范围；
3. 「以某个源码子目录前缀判定是否加 `--experimental`」这条触发规则；
4. 固定的安装命令 argv 形状、直接执行 `.run` 而非 `bash <pkg>`、超时上限、要求安装根事先不存在；
5. 把 3.4 那三类文件选为「交付存在」的判据，以及唯一匹配、拒绝符号链接、文件非空、路径不得逃逸等门槛；
6. 把 `ASCEND_OP_NAME` / `NEED_COMPILE_OPS` / `COMPILED_OPS` 合并成一个 token 集合、命中任一即算数；
7. 「整份构建缓存对评审无信息量」这类价值判断；
8. 「本轮变化的缓存文件必须恰好一份」这类唯一性门槛；
9. 「只使用某两个安装开关」这一选择本身。

以下是**核实边界**，用到时须现场补核：

- 2.1–2.5 全部来自仓 A 一次构建产出的成品包。族通用性未验证；换仓、换 CANN 打包器版本须重核。
- 本页没有真机安装后的目录树可对；3.1–3.8 的落盘形态由「CMake install 规则 + CPack 暂存树 + 包内 tar 清单
  + 包内脚本的拷贝逻辑」四路互证，尚缺「装完后在目标机上实际长这样」这一路。
- 1.5 里「帮助文本默认值不可信」只在仓 A 有实测闭环，仓 B 为静态读码推断。
