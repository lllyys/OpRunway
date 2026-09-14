# ops-sparse 链路：真机实测事实

评估 `aclsparseDenseToSparse` 社区任务时测出来的。**只记实测到的**，推断标「未验证」。

出处：2026-09-07，A3 机（`Ascend910_9382`），CANN **9.0.0-beta.1**
（`/usr/local/Ascend/` 下并存 `cann-9.0.0`、`cann-9.0.0-beta.1`、`cann-9.0.0-beta.2`，
编译实际走的是 `cann-9.0.0-beta.1`）。

## 构建装包

ops-sparse 是自足工程，不需要母仓：

```bash
bash build.sh --pkg --soc=ascend910_93          # 全仓 arch22，冷缓存 1m37s
build_out/cann-A3-ops-sparse-1.0.0_linux-aarch64.run \
    --quiet --install --install-path=<dir>
```

| 事实 | 值 |
| --- | --- |
| SOC 到 arch 的映射 | `ascend910b*` 与 `ascend910_93*` 都映射 `arch22`（DAV-2201），`ascend950*` 映射 `arch35`，`ascend310p*` 映射 `arch20`（`CMakeLists.txt:38-56`） |
| 包名 | `cann-A3-ops-sparse-<版本>_linux-<arch>.run`，`A3` 段由 SOC 决定（`cmake/package.cmake:74-85`） |
| 包内容 | `include/*.h`、`lib64/libops_sparse.so`、`share/info/ops_sparse/{install.sh,help.info,version.info}` |
| 装包落点 | `<install-path>/cann/{lib64,include,share}`，**比给的路径多一层 `cann`** |
| 装包参数 | `--install` 必须给，只给 `--install-path=` 不装 |
| 生效方式 | 普通共享库，`LD_LIBRARY_PATH` 指到 `<install-path>/cann/lib64`。**没有 opp vendor 那一层**，`ASCEND_CUSTOM_OPP_PATH` 在这条链路上不适用 |
| 导出符号 | `nm -D` 见 `T aclsparseSpMV` 等 C 符号；`GetWorkspaceSize` 命中 **0** —— 全仓没有两段式 aclnn 接口 |
| `densetosparse` 现状 | 只有 `arch35`，A2/A3 要的 `arch22` 是从零新增 |

`build.sh` 只认 `--ops=` `--run` `--soc=` `--pkg` 四个参数，其余落 `*)` 分支打
`Unknown option` 并退 1；`--vendor_name` 与 `--experimental` 全仓 0 处。

## 跑测侧脚本的错配点

`build_install.py` 的假定与上面逐条对不上，改动点是这五处：

| 现在写死的 | 该换成 |
| --- | --- |
| 算子目录判据 `op_kernel/` 或 `op_host/` | `sparse/<op>/<arch>/` 下的 `*_host.cpp` 与 `*_kernel.cpp` |
| 母仓判据 `build.sh` **且** `cmake/func.cmake` | ops-sparse 的 `cmake/` 只有 makeself/package/test/version 四个 |
| `build.sh --pkg --experimental --vendor_name=` | `build.sh --pkg --soc=<soc>`，不带后两个 |
| 包名 glob `build_out/cann-ops-*.run` | `build_out/cann-*-ops-sparse-*.run` |
| 符号判据 `aclnn<Op>GetWorkspaceSize` | `aclsparse<Op>` 系列 |

## ATK 在 npu 节点这条路上的报表形态

用桩（把 `torch.ops.ops_sparse_test.dense_to_sparse_npu` 指到 CPU 参考实现）跑
任务自带的 200 条精度件，`atk pytorch <cases> -p <plugins> --task accuracy --devices 0`，
92 秒，200/200 通过。

**报表与 aclnn 那条路同构**，这是复用报表解析层的依据：

| 项 | aclnn 路 | sparse 路（npu 节点） |
| --- | --- | --- |
| sheet | `statistic` `summary` `failed cases` `accuracy false cases` | 逐字相同 |
| summary 列 | 名称/总用例数/执行成功用例个数/执行失败用例个数/通过用例个数/通过率/精度是否达标 | 逐字相同 |
| 被测节点名 | `pyaclnn_0` | `npu_0` |
| 标杆节点名 | `cpu_0` | `cpu_0` |
| 精度判定挂哪列 | `<标杆节点>_精度通过`，被测节点那列整列 `None` | 同样：`cpu_0_精度通过` 有值，`npu_0_精度通过` 整列 `None` |

所以 `_parse_report` 与 `_case_verdicts` 的参数化粒度就是**节点名一个变量**，
`_device_times` 里写死的 `pyaclnn` 前缀同理。

## 任务自带件：两条推断已被实测推翻

| 原推断 | 实测 | 结论 |
| --- | --- | --- |
| 精度按 `mixed_tolerance_bm` 容差比，与任务书 exact match 冲突 | 交付的 `accuracy_cases.json` 每条都写 `standard.acc = dense_to_sparse_exact`，比对器是 `torch.equal`（`accuracy_sparse_ops.py`）。`mixed_tolerance_bm` 只出现在 `atk_generalization.yaml` 这份**生成规则**里 | **撤回** |
| 用例只声明 1 个输出，而适配器返回 3 元组，indices 不会被比 | `精度详情` 列显示 `output_0.pt`/`output_1.pt`/`output_2.pt` 三个都比了；`output_info_list` 记录 int8[43]、int64[18]、int32[43]，即 values 与两组 indices 全覆盖 | **撤回**：ATK 按实际返回的张量个数落盘比对，`outputs` 声明不截断 |

仍然成立的一条：`operator_adapter._dense()` 的值来自 `torch.randn`（int8 走
`randint(1,9)`），**产不出 ±0、INF、NAN**，而任务书 3.5 要求 10% 覆盖这些。

未验证：`_reference()` 的 Blocked-ELL 把 block-column pattern 从数据推导，
而任务书 2.1 要求按调用方预置的 pattern 提取、3.2 要求校验 pattern 只读。
这条只读了代码，没在真机上构造反例。

## 归一 + 冻结 + npu 剖面跑测：整条跑通

2026-09-07，A3 机。**用任务自带的 200 条精度件走完生成侧 S2′ → S4 → 跑测侧 A3。**
待验收算子尚未实现，用桩顶替（`torch.ops.ops_sparse_test.dense_to_sparse_npu`
指到自带件的 CPU 参考实现），所以验的是**管道，不是算子**。

| 步 | 命令 | 结果 |
| --- | --- | --- |
| S2′ 归一 | `adopt_kit.py --kit <自带件> --op DenseToSparse` | 200 条用例、2 个插件、4 个 sha256 指纹；档位 small 45 / medium 29 / large 126 |
| S4 冻结 | `freeze_golden.py -c cases.json -p function_DenseToSparse.py` | 标杆 200/200，**35s**；`inputs/` 也落了 200/200 |
| A3 精度 | `run_atk.py --mode accuracy` | 执行 200/200、精度 200/200，**72s** |
| A2.5 冒烟 | `run_atk.py --mode smoke` | 按 `format_id` 抽满 4 档（#0/#20/#40/#60），全跑起来 |
| A4 性能 | `run_atk.py --mode performance`（12 条子集） | 12/12，**182s**；Device 耗时全 0，见下 |
| A5 结论 | `verdict.py` | 精度表按 `format_id` 出四档：csr 40、csc 40、coo 40、blocked_ell 80，各 100%；执行器节写明两项不适用 |
| 复现 | `bash rerun.sh accuracy`（不装 skill，只 source env.sh） | 200/200，**69s**，测试人员直接读 ATK 自己的汇总表 |

跑测侧实际发出的命令：

```text
atk node --backend npu --devices 0 \
    node --backend cpu -n 0 --task accuracy_load --output_path <pkg>/golden \
    task -c ../input/cases.json --task accuracy -p <pkg>/function_DenseToSparse.py
```

**这一条是整个方案的地基**：自带件是当场算 CPU 标杆的（`atk pytorch` 起
npu + cpu 两节点同轮），而本仓的用例包要冻好的 golden 走 `accuracy_load`。
两种形态能不能互换，此前只有推断。现在实测：**能**，200 条一条不差。

顺带实测到的两条：

| 事实 | 说明 |
| --- | --- |
| 纯 attr 用例 ATK 照样落 `inputs/<id>/input.bin` | 落的是那九个 int 的编码，不是张量。npu 剖面用不上（`--input_data` 读张量字节），但它存在，不要据此以为冻输入生效了 |
| `/proc/self/maps` 探针在真机上核得住 | 打印 `被测实现 <install_root>/cann/lib64/libstub.so`。macOS 上没有 `/proc`，探针一律退 4，失败方向保守 |

### 性能轮的 Device 耗时为 0 是桩的性质，不是解析失败

报表里的列名是 **`npu_0_Device性能（us）`**（另有 `cpu_0_Device性能（us）` 与
`npu_0/cpu_0_Device性能比`），与剖面表里 `node_prefix="npu"` 逐字对上，
12 条值都取到了。**取到 12 个 0 与一条都没取到是两回事**：前者是桩在 CPU 上
算完再搬回 device，一个 NPU kernel 都没发；后者才是前缀写错。
分辨办法是看 `run_atk.py` 打印的「Device 耗时 N 条」——N 为 0 才是解析失败。

最终代码整链复跑一次（A2.5 → A3 → A4 → A5 连着跑），结果与上表逐项相同。

### 探针在真机上抓到过一次真错配

试跑中途把 A2（spmv）那份 `install.json` 覆盖进了 DenseToSparse 的现场，
而进程里加载的桩来自另一个目录。探针当场拦下：

```text
阻塞·未验收：核不到待验收实现。stubreg 装进来了，但 /proc/self/maps 里
没有 <A2 的 install_root> 下的 so。进程里跑的不是本轮装的实现
```

**这正是它该拦的那一类**——报告一切正常、通过率好看，但测的是别处那份。
不是构造出来的用例，是操作失误撞上的。

## A2 自足工程：真构建，无桩

同机另跑一遍 `build_install.py`，算子取 ops-sparse 里已实现的 `spmv`：

```bash
build_install.py --op aclsparseSpMV --project <仓>/sparse/spmv \
    --soc ascend910_93 --symbol aclsparseSpMV --register-module <模块名>
```

| 步 | 结果 |
| --- | --- |
| 形态探测 | `standalone-so`（仓根有 `build.sh`、没有 `cmake/func.cmake`） |
| 算子目录 | 认出 `sparse/spmv`（含 `arch22/`，底下有 `spmv_host.cpp`） |
| 构建 | `bash build.sh --pkg --soc=ascend910_93 --ops=spmv`，**65s** |
| 装包 | `cann-A3-ops-sparse-1.0.0_linux-aarch64.run` → `<opp>/cann/lib64` |
| 符号 | `aclsparseSpMV` 在 `libops_sparse.so` |
| env.sh | 写 `LD_LIBRARY_PATH`，不写那两个 `*_CUSTOM_OPP_PATH` |

## 待补

- 真实算子的 A2 + A3 串起来跑：ops-sparse 的 `densetosparse` 目前只有 `arch35`，
  A2/A3 机要的 `arch22` 是从零新增，没有可编的实现。`spmv` 验的是构建装包那半边，
  跑测那半边仍靠桩
- 确定性（ATK 的 `accuracy_dc`）尚未接入本链路
- 与任务方交付的 GPU 基线表对齐口径：要比数就得跑任务方自己的 benchmark 脚本，
  scratchpad 里那份自带件没有性能件
