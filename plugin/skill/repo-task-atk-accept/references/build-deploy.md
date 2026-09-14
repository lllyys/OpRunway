# 构建、装包与生效验证

## 目录

- 为什么必须回母仓
- 落点：按母仓现状观察，不推断
- 四步
- 三个环境变量
- 签名自检搜的是磁盘上的头文件
- 装完了但符号找不到
- 复用已有构建

社区算子工程**不是可独立构建的仓**，它是母仓的一个子目录。
`ops-test/roll/` 里只有 `op_api/ op_host/ op_kernel/ tests/`，没有 `build.sh`，
也没有 `cmake/`。必须把它放回母仓对应位置，用母仓的 `build.sh` 构建。

## 为什么必须回母仓

**算子工程编不出来，不是缺个脚本，是它本来就是母仓的一个 CMake 子模块。**
四个算子的 `CMakeLists.txt` 有效内容都只有一句，调的是母仓定义的宏：

```cmake
add_all_modules_sources(OPTYPE roll ACLNNTYPE aclnn_exclude)
```

宏在 `ops-math/cmake/func.cmake:569`，母仓根靠 `add_subdirectory` 把它拉进来
（`CMakeLists.txt:123-128`）。工程里没有 `project()`、没有 `cmake/`、没有工具链、
没有第三方依赖、没有 `.run` 打包。**判据是「有没有自带 build.sh」**：没有就是
子模块，必须给 `--parent-repo`。

自带 build.sh 的自足工程（msopgen 那种形态）本 skill 没有真机样本，
`build_install.py` 识别出来会退 4 并让你手工构建后用 `--skip-build` 接进来。

母仓地址在任务书的「基础信息 → 开源仓地址」和「PR 申请合入」两处。

## 落点：按母仓现状观察，不推断

`build_install.py` 在母仓里找同名目录（排除 `build/`、`build_out/`）：

| 找到几个 | 怎么办 |
| --- | --- |
| 1 个 | 那就是落点。**改已有算子的任务走这条** |
| 0 个 | 新增算子，退 4。用 `--target <母仓相对路径>` 指定，路径见任务书「PR 申请合入」 |
| 多个 | 退 4 并列出候选，用 `--target` 指定一个 |

**不要按目录名去推「应该放在 experimental/ 下」。** 两类任务的落点是相反的：
新增算子进 `experimental/<域>/`，改已有算子就留在它原来的域里
（`ops-math` 里 roll 有两份就是这么来的：`conversion/roll` 是上游原有的，
`experimental/math/roll` 是社区任务加的）。

**更不要把算子挪出原位。** 源码里的相对引用会断：`stateless_bernoulli` 有 5 处
`../../random_common/...`，挪进 `experimental/random/` 后直接编不过。

## 四步

`build_install.py` 按顺序做这四件事，任一步失败就停：

| 步 | 做什么 | 失败退出码 |
| --- | --- | --- |
| 1 | 定落点（见上），把算子目录整个复制进去 | 4 |
| 2 | `bash build.sh --pkg [--experimental] --soc=<soc> --ops=<snake>[,<其它>] --vendor_name=<name>` | 2 |
| 3 | `build_out/cann-ops-*.run --quiet --install-path=<opp 根>` | 2 |
| 4 | `nm -D` 确认 `aclnn<Op>GetWorkspaceSize` 在包里 | 3 |

### --experimental 加不加，由落点决定

它的含义是**编不编母仓根下 `experimental/` 这个目录**（`build.sh:144` →
`CMakeLists.txt:124` 的 `add_subdirectory(experimental)`），不是「实验特性」。
所以落点在 `experimental/` 下就加，否则不加，脚本自动判。

写死加上的后果：算子在非实验域时报 `Specified ops not found in this depository`，
**报错指向算子名，不指向落点**，很容易误判成 `--ops` 写错。

### aclnn 入口不一定在算子自己目录里

改 `stateless_bernoulli` 的 kernel，而 `aclnnBernoulli` 的 host 实现在
`random/dsa_gen_bit_mask/op_host/op_api/aclnn_bernoulli.cpp`。只编前者，
装出来的包里没有 `aclnnBernoulliGetWorkspaceSize`。

第 4 步核不到符号时，脚本会在母仓 `grep` 一遍，报出**谁定义了它**并给出
`--extra-ops` 该带谁。照它重跑即可：

```bash
--extra-ops dsa_gen_bit_mask
```

**不要照第 4 步原本的提示去查 vendor 目录或包名**——那个方向是空的。

### --soc 取值

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

## A2 编译安装

先读 [build-deploy.md](build-deploy.md)。

```bash
cd <现场>/work && source evidence/env.sh && <python> <skill>/scripts/build_install.py \
    --op <op> --project <工程目录> --parent-repo <母仓> --soc <soc> -o stage/install.json
```

**工程形态由脚本探，不用你判。** 它按仓根的结构分两种，开跑前打印一行
`工程形态`：

| 仓根有什么 | 形态 | 怎么编、怎么装 |
| --- | --- | --- |
| `build.sh` 且 `cmake/func.cmake` | `opp-vendor` | 母仓子模块，装进 opp vendor 层，靠 `ATK_CUSTOM_OPP_PATH` 生效 |
| 只有 `build.sh` | `standalone-so` | 自足工程，装出来是普通共享库，靠 `LD_LIBRARY_PATH` 生效 |

两个可选参数，**各自的触发条件不同，不要一起给或一起漏**：

| 参数 | 什么时候要 | 不给会怎样 |
| --- | --- | --- |
| `--symbol` | 形态是 `standalone-so` | 缺省核 `aclnn<Op>GetWorkspaceSize`，自足工程导出的不是这个名字，一律退 3 |
| `--register-module` | **`facts.json` 的 `backend` 是 `npu`**，与工程形态无关 | 每轮开跑前的探针拿不到模块名，`run_atk.py` 退 3，报错指向 `install.json` 而不指向这里 |

**第二个绑的是执行剖面不是工程形态。** 母仓子模块（`opp-vendor`）也可以只把算子
注册进 torch，那时照样要给；反过来自足工程暴露了 aclnn 两段式接口时不用给。
`build_install.py` 也是这么判的——编 torch 扩展这一步只看给没给这个参数。

给了 `--register-module` 时本步顺带做三件事，不用手工补：

| 做什么 | 判据 |
| --- | --- |
| 编 torch 扩展 | 工程里哪份 `setup.py` 提到这个模块名就编哪份，`build_ext --inplace` |
| 找 so 的落点 | 编完在工程下现找 `<模块名>*.so`，落点随包布局走，不按目录名猜 |
| 写进 `evidence/env.sh` 的 `PYTHONPATH` | ATK 的 worker 是另起的进程，靠这个变量找模块 |

扩展 so 是 `.gitignore` 掉的构建产物，克隆下来的工程里没有它。**这三件事漏掉
任何一件，探针报的都是「待验收实现没装进来」，指向装包而不指向这里。**
模块名怎么找见 [npu-profile.md](npu-profile.md)「待验收实现的核对」。

## 自足工程的装包

`build_install.py` 探到 `standalone-so` 形态时走这条。五处与 opp-vendor 不同，
都是实测（ops-sparse，2026-09-07）：

| 项 | opp-vendor | standalone-so |
| --- | --- | --- |
| 算子目录判据 | 含 `op_kernel/` 或 `op_host/` | 含 `arch<NN>/`，底下有 `*_host.cpp` |
| 构建命令 | `build.sh --pkg [--experimental] --soc= --ops= --vendor_name= -j` | `build.sh --pkg --soc= --ops=`，**多给一个就退 1** |
| 包名 | `cann-ops-*.run` | `cann-<SOC 段>-ops-<仓名>-<版本>_linux-<arch>.run` |
| 装包参数 | `--quiet --install-path=` | 还要 `--install`。**漏了它包解压完就退，一个文件不装，退出码还是 0** |
| 生效方式 | `ASCEND_CUSTOM_OPP_PATH` + `ATK_CUSTOM_OPP_PATH` | `LD_LIBRARY_PATH` 指到 `<install-path>/cann/lib64`，**比给的路径多一层 `cann`** |

符号核查也不同：opp-vendor 固定核 `aclnn<Op>GetWorkspaceSize`，自足工程核什么
由 `--symbol` 给，缺省取 `--op` 的原文当子串。ops-sparse 全仓
`GetWorkspaceSize` 命中 0，导出的是 `aclsparse<Op>` 系列。

SOC 到 arch 的映射（`CMakeLists.txt:38-56`）：`ascend910b*` 与 `ascend910_93*`
都映射 `arch22`，`ascend950*` 映射 `arch35`，`ascend310p*` 映射 `arch20`。
**算子只在某个 arch 下有实现时，换机型就编不出东西**，而构建照样退 0。


`--soc` 取 `stage/env.json` 的 **`soc.build_flag`**（A3 机型 `ascend910_93`，A2 机型
`ascend910b`）。**不是 `soc`** —— 那是个对象，直接传会让 A2 退 2：

```bash
<python> -c "import json;print(json.load(open('stage/env.json'))['soc']['build_flag'])"
```

| 退出码 | 含义 |
| --- | --- |
| 0 | 装好且符号可见 |
| 2 | 构建失败，日志尾部已打印 |
| 3 | 装好但符号不可见——包名或 vendor 目录不对，见 build-deploy.md |
| 4 | 落点定不下来：母仓里有多个同名算子目录，或算子目录没有自带 `build.sh`。屏幕上已列出候选，用 `--target <母仓相对路径>` 指定一个 |

**它开跑前打印一行 `源码`（git 短 sha、日期、提交标题），与任务书对不上就停下。**
符号可见证明不了装的是待验收实现，见 troubleshooting.md「整类 dtype 全挂，报错是 `EZ1001`」。

## torch 适配层的落点

**装完先确认适配层在不在。** 自足工程把算子注册进 torch 时，`install.json` 的 `torch_extension_dir` 为空就是
没编上——**A2 的自动扩展步只认 `setup.py`**，而这类工程常常用自己的
`torch/scripts/build.sh` 编。两轮验收在这里各花掉几分钟，走的是同一条弯路。

按顺序试，第一条成立就停：

| 判据 | 做什么 |
| --- | --- |
| 交付件里已有编好的 `.so`（`torch/build/*.so`），且不比源码旧 | **直接用它**，把它的 python 包目录追加进 `evidence/env.sh` 的 `PYTHONPATH`。核一次 `nm -D --defined-only` 里有没有 `TORCH_LIBRARY` 的符号 |
| 没有现成产物 | 跑工程自己的 `torch/scripts/build.sh` |
| `build.sh` 编不过，报 `acl_base_rt.h` 找不到 | **头文件来源冲突**，见下 |

**`acl_base_rt.h` 这个报错不要去找缺的头文件。** 实测：工程的公开头包含
`<acl/acl_base_rt.h>`，而适配层的 include 路径把 torch_npu 自带的那份 ACL 头
排在 CANN 的前面，torch_npu 那份没有这个文件。修法是让 include 顺序里 CANN 的
头先于 torch_npu 的，不是去补文件。

**装完核一次注册生效**，不要等到冒烟才发现：

```bash
source evidence/env.sh && python3 -c "
import torch, torch_npu, <适配层模块名>
print([k for k in torch.ops.<命名空间>.__dir__() if not k.startswith('_')])"
```

打不出算子名就是没注册上——这时候 `torch.sparse.addmm` 那一类调用会静默落到
别的实现，或者报 `Could not run 'aten::xxx' with arguments from the 'SparseCsrnpu'
backend`。

## 跑测命令里的路径一律绝对

`run_atk.py` 把 ATK 子进程的 CWD 切进它自己的工作目录（冒烟是 `work/smoke`），
**命令行里的相对路径在那里解析不到**。两轮验收都在冒烟第一次上栽在这里，
报错指向「找不到用例文件」或「插件加载失败」，方向对但要多跑一轮才看见。

用例包、插件、`--plugin`、`--input_data` 全部写绝对路径：

```bash
SITE=<现场绝对路径>
python3 <skill>/scripts/run_atk.py --mode smoke --op <aclnn 名> \
    -c "$SITE/input/kit_fixes/cases.json" --plugin "$SITE/input/kit_fixes"
```

## A2.5 dtype 冒烟

```bash
cd <现场>/work && source evidence/env.sh && <python> <skill>/scripts/run_atk.py \
    --mode smoke --op <op> -c ../input/cases.json \
    --golden ../input/golden --facts ../input/facts.json -o stage/smoke.json
```

| 退出码 | 含义 | 去向 |
| --- | --- | --- |
| 0 | 每种 dtype 都跑起来了，或整类挂了但不是接口层拒的 | 进 A2.6 |
| 3 | 整类 dtype 被 `GetWorkspaceSize` 拒掉 | 停止，输出 `阻塞·未验收 @A2`，核 `stage/install.json` 的 `op_dir` 与 `source` |
| 2 | 没产出报告 | 按 [troubleshooting.md](troubleshooting.md) 分层定位 |

**只问跑不跑得起来，不问算得对不对。**

分档轴由 `facts.json` 定：有张量输入时按 dtype，纯 attr 用例按 `group_attr`
（脚本开跑时打印用的是哪个）。**`group_attr` 没填时纯 attr 用例塌成一条**，
这一步就只抽得出一个用例，拦不住「某一类整类跑不起来」——回生成侧补上它。
