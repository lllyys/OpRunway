# 模板契约 —— 发射器的全部非 IR 知识

发射器产 C++ 只许消费四处：IR（contract-ir.md）、签名表（scripts/
signature_table.json）、本契约、以及 `assets/template/` 的逐字模板文件（Step 3 交付，
随 skill 分发；本文写「逐字照 rev1」处即其底稿来源，交付时逐字固化进模板文件）。
四处之外没有来源；未覆盖的决策点一律 UNSUPPORTED_CONTRACT 停机，不脑补。
对拍基准 = spike overlay-rev1（候选，Step 3 真机复验后生效）。

## 1. 五件文件骨架

- 每件带树内标准版权头（rev1 各件 1-9 行逐字）；头文件 `#pragma once`。
- include 序照 rev1 各件原文逐字（golden 头含重复 `<cmath>` 原样保留——对拍优先于
  规整，contract-ir.md §10 拼接规则）。
- 类名派生：`<Op 首字母大写><ArchDir 首字母大写>Test`，如 sasum+arch22 →
  `SasumArch22Test`（rev1 sasum_test.cpp:22）。文件名 `<op>_param.h`、`<op>_golden.h`、
  `<op>_test.cpp`、`<op>_npu_wrapper.h`、`CMakeLists.txt`。
- golden 函数名 `<symbol>_cpu`、wrapper 函数名 `<symbol>_npu`（rev1 命名）。

## 2. param.h

- 成员声明序 = IR columns 中 binds.param 列的出现序（rev1 sasum_param.h:25-27）。
- 成员类型由 reader 定：int64→int64_t；fill→BlasFillMode；float→float；
  enum→保留 CSV 原文 std::string（枚举翻译在调用点走 cxx 映射）。
- 占位初值表：dim→0、layout→1、fill→parseFill("RANDOM_10")、scalar→0.0f、
  enum→"N"（sasum 见证前三项，rev1 sasum_param.h:25-27；后两项 gemm param 惯例）。
- require-throw 骨架逐字照 rev1 sasum_param.h:31-40（lambda require + 逐列
  parse 调用），列读取序 = columns 序。

发射器两路，wrapper 与 test 形态各异。builtin_kernel golden 走 **L1 逐字节路径**（sasum，
复现 rev1）；cblas_call golden 走 **通用 A′ 路径**（sger/sgemm，设备权威 + 语义保持降级搬运）。

## 3. wrapper 搬运状态机

### 3a. L1 路径（builtin_kernel，逐字照 rev1 sasum_npu_wrapper.h）

固定五步：1) null-handle 短路→checks 里 handle null_check 的 status；2) quickReturn 判定
（IR quick_return.cond_ast；无则无此步）；3) 上传 device 且 movement 含 upload 的参数；
4) 单调用点，host 标量直传、device 取上卡指针、out_scalar 取 device 暂存；5) `ret==SUCCESS`
才同步与回读，同步失败返 sync_fail_status，否则原样返 ret（rev1 F-09 修正形）。

### 3b. 通用 A′ 路径（cblas_call）——设备权威 + 语义保持降级搬运

1. null-handle 短路：唯一宿主前置检查（按 handle 角色，非字面名），status 取 checks 里
   handle null_check；注明是 harness 安全契约，不代替设备的 null-handle 行为。
2. 每 device buffer 算 int64 checked span（`(dim-1)*|inc|+1`，`|inc|`=`max<int64_t>(inc,-inc)`，
   含 abs(INT_MIN) 不溢出），`ok = 1≤span≤上限(1<<27)`，`elems = ok ? span : 1`（哨兵）。
3. 上传：movement 含 upload 的参数按 elems 上卡（哨兵或全量），**k>0 守卫**——upload_guard 的
   guarded_params 仅在 dim>0 时上卡（dim 与 guarded 显式来自 IR，不由发射器猜名）。
   M_NULLPTR 保持传 nullptr（指针语义保真）。
4. 单调用点：全部实参一次调用真设备；device 指针取上卡指针（未上卡 nullptr），host 直传。
5. `ret==SUCCESS` 才同步；仅「全量搬运（ok）且非空」的 readback 参数才按 elems 回读；同步失败
   返 sync_fail_status，否则原样返设备 ret。其余一切状态（无效维度/步长/lda 等）全由设备裁决。

## 4. test.cpp

### 4a. L1 路径（builtin_kernel，逐字照 rev1 sasum_test.cpp）

三路分派（error / noop / normal 三函数 + TEST_P 分派；无 quick_return 者两路）；降级缓冲三定则
（长度 `(p.n>0 && …M_NULLPTR…) ? p.n : 0` error 与 `(p.n>0) ? p.n : 0` noop；M_NULLPTR→nullptr
仅 error；空 vector→nullptr）；noop 哨兵 123.0f 与断言文本逐字（rev1 :60-65）。

### 4b. 通用 A′ 路径（cblas_call）——探针/全量二分 + int ABI 域门

- **int ABI 域门**（最前）：成员是 parseInt64 的 int64_t、wrapper 形参是 IR ctype（int），
  窄于 int64 的 int 型 dim/layout 成员不能无损装回 ctype 即报 harness 基础设施错误（该值对本
  算子不可表达）。门后所有整型输入 ≤2^31，span 的 int64 运算不可能溢出。
- span/ok/elems 与 wrapper 同公式同上限；`full = expect==SUCCESS && !quick`；full 行须所有 ok，
  否则报 harness 基础设施错误（合法但过大，不伪装成设备状态）。
- 造数按 elems（哨兵/全量）：vector→makeBlasArray(elems, fill, seed)；matrix→
  `ok ? makeBlasMatrix(physRows, physCols, ld, fill, seed) : std::vector<float>(1,0.0f)`；
  M_NULLPTR→nullptr。种子偏移：第 i 个携 fill 列缓冲用 randomSeed + i（i 从 0、按 params 序）。
- 快照只在 full 行做（调用前 host 副本）；单调用点后先 `EXPECT_EQ(ret, expectResult)`，非 full
  即返回（状态由设备裁决）；full 才 golden→`verifyVector(actual, golden, elems, 1, cfg, caseName)`，
  cfg 经 `applyMixedTolerance(cfg, ACL_FLOAT, golden, elems)`。
- smoke TEST_F 由 IR smoke 渲染（args 序=params 序，null→nullptr、["int",k]→字面量、
  "&local"→局部变量取址）。

## 5. CMake 与 CSV

- CMakeLists.txt 逐字一行式：`ops_blas_add_gtest_tests(${OPS_BLAS})`（rev1）。
- CSV：任务包 `<op>_test.csv` 逐字节副本落 `<arch_dir>/<op>_test.csv`，与 test.cpp
  同名同目录（编译期 __FILE__ 定位）。

## 6. 发射器禁止推断清单

以下决策点必须查表，发射器不得自行判断：

- 列的读取函数、成员类型、初值 → IR columns.reader + §2 表。
- 缓冲大小、上传/回读集合 → IR buffers.span_ast + movement；k>0 守卫适用性 → 签名表。
- golden 形态（builtin/cblas）、实参序、变换、常量插入 → IR golden + 签名表。
- 状态检查的条目、顺序、发射位点 → IR status_plan.checks + contract-ir.md §9 路由。
- quick-return 谓词与 writes_zero → IR checks 条目。
- 容差、比较域 → 恒 MIXED/span（contract-ir.md §8），无字段即无选择。
- 类名、文件名、函数名后缀 → §1 派生规则。
- 任何表达式的 C++ 文本 → AST 规范渲染序（contract-ir.md §12 速查）。
