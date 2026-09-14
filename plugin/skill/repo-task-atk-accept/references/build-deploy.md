# 构建、装包与生效验证

社区算子工程**不是可独立构建的仓**，它是母仓的一个子目录。
`ops-test/roll/` 里只有 `op_api/ op_host/ op_kernel/ tests/`，没有 `build.sh`，
也没有 `cmake/`。必须把它放回母仓对应位置，用母仓的 `build.sh` 构建。

## 母仓对照

| 算子所属 | 母仓 | 算子在母仓里的位置 |
| --- | --- | --- |
| `gitcode.com/cann/ops-math` | `ops-math` | `experimental/math/<snake>` |
| `gitcode.com/cann/ops-nn` | `ops-nn` | `experimental/index/<snake>` 或 `<域>/<snake>` |

母仓地址在任务书的「基础信息 → 开源仓地址」和「PR 申请合入」两处，
落点在「PR 申请合入」的目标路径里。

待验收工程若是解压包（如 `ops-nn-master-experimental-index-index_fill/`），
它自带 `experimental/index/index_fill/` 这段相对路径，`build_install.py`
会沿用它当落点，不需要人算。

## 四步

`build_install.py` 按顺序做这四件事，任一步失败就停：

| 步 | 做什么 | 失败退出码 |
| --- | --- | --- |
| 1 | 把算子目录整个复制进母仓落点（覆盖同名目录） | 4 |
| 2 | `bash build.sh --pkg --experimental --soc=<soc> --ops=<snake> --vendor_name=<name>` | 2 |
| 3 | `build_out/cann-ops-*.run --quiet --install-path=<opp 根>` | 2 |
| 4 | `nm -D` 确认 `aclnn<Op>GetWorkspaceSize` 在包里 | 3 |

### --soc 怎么定

从 `env.json` 的 `soc.build_flag` 读，它由 `torch_npu.npu.get_device_name(0)` 推：

| 芯片 | `--soc` |
| --- | --- |
| `Ascend910_93xx`（A3） | `ascend910_93` |
| `Ascend910B*`（A2） | `ascend910b` |
| `Ascend910_95xx`（A5） | `ascend950` |

**填错 soc 构建会过但跑测时找不到 kernel**，报「算子未注册」。

### --ops 用蛇形名

`--ops` 收的是算子目录名（蛇形），不是 aclnn 接口名。`aclnnIndexFillTensor`
对应的目录是 `index_fill`，`--ops=index_fill`。填 `IndexFillTensor` 会
构建成功但什么都没编，`build_out` 里没有对应包。

### --vendor_name 起个专名

默认 vendor 名是 `customize-<仓后缀>`，多个算子轮流验收会互相覆盖。
`build_install.py` 默认用 `<snake>_atk`，装出来是
`vendors/<snake>_atk_<仓后缀>/`（`ops-math` 加 `_math`，`ops-nn` 加 `_nn`）。

## 三个环境变量

装完后 `build_install.py` 会把 `evidence/env.sh` 重写成：

```bash
export ASCEND_CUSTOM_OPP_PATH="<opp 根>/vendors/roll_atk_math"
export ATK_CUSTOM_OPP_PATH="<opp 根>/vendors/roll_atk_math/op_api/lib/libcust_opapi.so"
export LD_LIBRARY_PATH="<opp 根>/vendors/roll_atk_math/op_api/lib:$LD_LIBRARY_PATH"
```

三个各管一件事，缺一个都不行：

| 变量 | 谁读 | 不设会怎样 |
| --- | --- | --- |
| `ASCEND_CUSTOM_OPP_PATH` | CANN 运行时找 kernel 实现 | 算子未注册 |
| `ATK_CUSTOM_OPP_PATH` | ATK 绑定 `aclnn*` 符号 | 绑到 CANN 内置的同名接口 |
| `LD_LIBRARY_PATH` | 动态链接器 | `libcust_opapi.so` 加载失败 |

### 为什么要 ATK_CUSTOM_OPP_PATH

ATK 在 `ASCEND_CUSTOM_OPP_PATH` 下**只认固定的 vendor 名**——
`{customize, custom} × {_math, _nn, _cv, _transformer, ""}` 十种组合
（`atk/tasks/backends/lib_interface/acl_wrapper.py:545`）。
`roll_atk_math` 不在其中，光设 `ASCEND_CUSTOM_OPP_PATH` 找不到我们的 so。

`ATK_CUSTOM_OPP_PATH` 是第一优先级且不做名字匹配，值就是
`libcust_opapi.so` 的完整路径（`acl_wrapper.py:538`），直接绕开白名单。

跑测开始时日志里应该出现这一行，出现了才说明绑对了：

```text
import aclnnRollGetWorkspaceSize from <...>/roll_atk_math/op_api/lib/libcust_opapi.so success!
```

**没有这一行就不要往下跑**，测的不是待验收实现。

## 签名自检搜的是磁盘上的头文件

pyaclnn 不光绑符号，还会 grep 头文件反查参数类型，与用例推出来的参数表逐个比
（`pyaclnn_backend.py:406` 的 `cpp_func_signature_check`）。搜索目录由
`ATK_CUSTOM_OPP_PATH` 的祖父目录推出来，正好是我们的 vendor 目录，
里面只有本算子的头文件。

社区算子与 CANN 官方接口重名是常态。设了 `ATK_CUSTOM_OPP_PATH` 就不会搜到
装机目录里那份官方头文件，也就不会出现「同名不同签名」的误报。

参数对不上时报错长这样，位置逐个列出来：

```text
参数数量不匹配：传入 5 个，预期 6 个
位置 1: 传入类型 [...]，预期类型 [...]
```

这是**用例的输入列表与真实签名不一致**，回生成侧核 `facts.json` 的 `params`
与 YAML 的 `inputs`，不是部署问题。

## 装完了但符号找不到

`build_install.py` 退出码 3 时按这三条查：

| 现象 | 原因 | 怎么办 |
| --- | --- | --- |
| `vendors/` 下没有任何 so | `--ops` 名字没匹配上算子目录 | 用目录名的蛇形写法重来 |
| 有 so 但没有目标符号，导出的是别的 `aclnnXxx` | `--op` 大小写与接口名不一致 | 按报错里列出的实际符号改 `--op` |
| 有 so 也有符号，但跑测仍加载内置 | `ATK_CUSTOM_OPP_PATH` 没生效 | 确认每条命令都 `source evidence/env.sh` |

## 复用已有构建

同一个算子反复调试时用 `--skip-build` 跳过构建直接装包，
省 3–5 分钟。**换了算子源码就不能跳**，装的会是上一版。
