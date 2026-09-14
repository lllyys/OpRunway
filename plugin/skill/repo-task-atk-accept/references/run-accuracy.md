# 精度跑测

## 目录

- 节点拓扑
- golden 目录结构与文件名的耦合
- 报告解读
- 退出码 0 不等于通过
- 失败归因
- 隔离复验
- 全量耗时
- 精度的连续与非连续在同一轮里混跑
- ATK 换不掉 tiling
- 跨版本指纹

待验收算子在 aclnn 节点实算，标杆节点不重算——直接读生成侧冻结的 golden。
同一套用例反复验收多个 PR 时 golden 不漂，结论可比。

## 节点拓扑

```bash
atk node --backend aclnn --devices 0 \
    node --backend <基线 backend> -n <基线名> --task accuracy_load \
        --output_path <golden 绝对路径> \
    task -c cases.json --task accuracy
```

| 部分 | 作用 |
| --- | --- |
| `node --backend aclnn` | 待验收节点，走 pyaclnn 直接调 `aclnn<Op>` |
| `node --backend ... --task accuracy_load` | 标杆节点，从磁盘读 golden 而不是重算 |
| `--output_path` | golden 根目录，**必须是绝对路径** |
| `task --task accuracy` | 任务类型 |

**标杆节点的 backend 与名字不是写死的，来自 `golden/manifest.json` 的
`baseline_dir`**（`run_atk.py` 的 `_load_node`）：

| `baseline_dir` | 标杆节点 | golden 是拿谁冻的 |
| --- | --- | --- |
| `cpu_0` | `--backend cpu -n 0` | 生成侧的 torch 标杆 |
| `pyaclnn_builtin` | `--backend pyaclnn -n builtin` | CANN 装机目录里的内置同名 aclnn |

没有 `manifest.json` 的老用例包按 `cpu_0` 走。

**不要把标杆节点写死成 cpu。** 内置基线的用例包里 `cpu_builtin/` 装的是
只给形状用的废数据（`torch.zeros_like`），照它比对全错，而且**不报错**。

`accuracy_load` 是 ATK 的正式任务类型（`atk/configs/base_config.py:60`），
aclnn 任务显式支持它（`atk/tasks/task_creator/aclnn_task.py:51`）。

`run_atk.py` 会把这条命令拼好，不需要手敲。

## golden 目录结构与文件名的耦合

golden 的路径是 `<golden 根>/<backend 名>/<save_name>/<用例 id>/`：

```text
golden/
└── cpu_0/            backend 名 = "cpu" + 节点名 "0"
    └── cases/        save_name = 用例文件名去掉扩展名
        ├── 0/output_0.pt
        ├── 0/output_info.json
        └── 1/...
```

**backend 名取的是加载节点自己的** `f"{backend}_{name}"`（`atk/configs/nodes_config.py:90`，
用在 `atk/tasks/opp_tasks.py:465`）。所以加载节点必须与冻结时那个节点同名，
`run_atk.py` 靠 `baseline_dir` 把这两头对上。

`--backend aclnn` 的待验收节点在 ATK 里的名字是 **`pyaclnn_0`**，不是 `aclnn_0`。
内置基线的 golden 因此不能叫 `pyaclnn_0`——重名时加载节点会被改名成
`pyaclnn_0_1`（`nodes_config.py:149`），一条 golden 都匹配不上。生成侧把它冻成
`pyaclnn_builtin` 就是为了避开这个。

**`save_name` 取的是用例文件的基名**（`atk/tasks/result_process.py:67`）。
生成侧的 golden 是用 `cases.json` 跑出来的，所以子目录叫 `cases`。
跑测时用例文件也必须叫 `cases.json`，换成 `smoke.json` 就会去找
`golden/cpu_0/smoke/`，一条都找不到，报：

```text
标杆输出为空，请检查标杆是否运行失败或者没有输出
```

所以任何子集都是**换目录不换文件名**：隔离复验写成 `isolate/round<n>/cases.json`，
性能基线轮的过滤子集写成 `subset/cases.json`。生成侧抽性能子集也按这个规则。

## 报告解读

ATK 产出 `atk_output/<save_name>_<时间戳>/report/*.xlsx`，四张表：

| 表 | 内容 |
| --- | --- |
| `summary` | 总用例数、执行成功/失败、通过数、通过率、是否达标 |
| `failed cases` | 执行失败的用例，含 `编号` |
| `accuracy false cases` | 执行成功但精度没过的用例 |
| `statistic` | 逐用例明细，含各节点的端到端/Benchmark/Device 耗时 |

`run_atk.py` 解析这四张表写成 `accuracy.json`。控制台那张表只是同样内容的
文本版，不要靠肉眼抄。

**「执行失败」和「精度不通过」是两回事**：

| 落在哪张表 | 含义 | 归因方向 |
| --- | --- | --- |
| `failed cases` | 算子没跑起来 | 部署、参数不合法、aicore 异常 |
| `accuracy false cases` | 跑起来了但结果与 golden 不符 | 计算逻辑 |

## 退出码 0 不等于通过

`run_atk.py` 退出码 0 只表示任务跑完、报告解析出来了。结论看
`accuracy.json` 的两个字段：

```json
{"passed": true, "pass_rate": 100.0}
```

`passed` 来自报告的「精度是否达标」列，不是脚本自己判的。

## 失败归因

**归因只到「哪一组用例失败」为止。**

`verdict.py` 把失败用例按 dtype 分组，因为社区任务多半是「扩展支持某几种
dtype」，失败集中在新增 dtype 上是最有价值的信号。分组结论写进报告，
成因交给算子作者。

不要做的事：

| 反模式 | 为什么 |
| --- | --- |
| 去 `op_kernel/` 找原因，然后判定「这是预期行为」 | 拿被测实现给自己开脱 |
| 把失败用例从 `cases.json` 里删掉再跑一遍 | 掩盖缺陷 |
| 一条失败就推广成「这个 dtype 全不支持」 | 归因超出证据 |
| 因为通过率不好看就调宽精度标准 | 标准是任务书定的 |

**剔除用例的唯一合法理由**是原始错误明确表明参数无法形成调用（比如
签名对不上导致的参数数量不匹配）。那属于用例包缺陷，要回生成侧修，
不是在这里删掉。环境失败、绑定失败、原因不明的失败都不得剔除。

**隔离复验与全量必须同口径。** 全量按 `facts.non_contiguous` 决定带不带
`--slice_input`，隔离复验也必须带。切不切由 `random.Random(case_id)` 定
（`atk/tasks/backends/backend.py:147-159`），与批次无关，所以同一条用例在两边
应该是同一种输入布局。不一致时只在非连续下崩的用例会被重跑成连续、跑通、
判成连带——静默漏报，实测 case 67 就是这么丢的。


## 隔离复验

有失败就必须做，判据与命令在 [run-isolate.md](run-isolate.md)。

## 全量耗时

187 条用例在单卡上实测一两分钟。用例数上千或开了 `has_upper_border` 会显著变长。

**不用判「是慢还是卡死了」。** `run_atk.py` 盯 `evidence/<mode>.log` 的增长，
180 秒不长就杀掉并报出原因——ATK 自己没有超时兜底，worker 段错误之后主进程
会一直等。总时长上限按条数推（`_round_timeout()`：600 秒固定开销加每条 3 秒，
下限 1800 秒），只是停滞检测漏网时的兜底。

## 精度的连续与非连续在同一轮里混跑

`non_contiguous.required` 为真时 `--mode accuracy` 按 `ratio` 把两种布局混在同一轮。
**两个通过率分组报，不合并成一个数。**

| 项 | 取值 |
| --- | --- |
| 比例 | `facts.non_contiguous.ratio`，缺省 0.4 |
| 布局分组落在 | `stage/accuracy.json` 的 `layout` 字段 |
| 报告口径 | `verdict.json` 的 `accuracy_by_layout`，两种布局各一组 |
| 隔离复验 | `--ids-from stage/accuracy.json`，比例从 facts 读，自动同口径 |

**哪几条是非连续的可以复算，不必猜。** 两个剖面来源不同：

| 剖面 | 布局由谁定 | 怎么复算 |
| --- | --- | --- |
| `aclnn` | ATK 切 | `random.Random(用例 id).random() < ratio`，与 `atk/tasks/backends/backend.py:151` 同一个公式 |
| `npu` | 用例属性，ATK 不接管输入构造 | 填 `non_contiguous.attr` 与 `noncontiguous_values`，按属性取值判 |

不要拆成两轮跑：1000 条一轮约半小时，两轮串行会撞 `_round_timeout()` 的上限，
实测轮 2 跑到 47 分钟被杀、零产出。

**只在非连续组失败的用例，不要直接记成待验收算子的缺陷。** 非连续入参会让 aclnn
插入 CANN 内置的辅助算子做「非连续转连续」，它们自己也可能有缺陷——实测见过内置
`StridedSlice`（落点 `<CANN>/opp/built-in/.../ops_legacy/strided_slice/`）越界写 24 字节。
定责要证据：

```bash
mssanitizer --tool=memcheck <可执行文件> <参数>
```

它报 `out of bounds` / `illegal read|write` 时带 kernel 名。**kernel 落在待验收算子包的
vendor 目录下就是本算子的账，落在 CANN 装机目录下要连同证据报给上游。** 报告里如实写
证据与落点，不替算子作者下结论。

两个约束：mssanitizer 要预留影子内存，HBM 快满的卡上直接报 `207001` 起不来，挑空卡跑；
它自带的分配器会改变显存布局，**有些故障在它下面复现不出来，复现不出不等于没有**。


## ATK 换不掉 tiling

**ATK 用的永远是 CANN 内置的 tiling，只要存在同名内置算子。** 这不是配置没找对，
是结构性的：ATK 必须 `import torch_npu`（`npu` 设备类型是它注册的），而 torch_npu
会完整拉起 GE；GE 的加载顺序是先 built-in、后扫 `ASCEND_CUSTOM_OPP_PATH` 的 vendors，
合并按 op type 先到先得，算子包自带那份后到就被拒。

日志里的判据（`--mode tiling` 就是查这个）：

```
MergeFunctions:op type <OpType> tiling func has been registered.
```

三条走不通的路子，都实测过：

| 试法 | 结果 |
| --- | --- |
| `LD_PRELOAD` 算子包的 tiling | so 装进了进程，但注册抢不到；而且构造函数跑在 GE 的收集窗口之外，GE 再打开时 per-handle 计数报成全局总数 |
| 在 `import torch_npu` 之前 dlopen（`sitecustomize`） | 同上。**dlopen 不往 GE 的空间注册表里放东西**，GE 只认它自己 `GetOpImplFunctionsByHandle` 加载的那批 |
| 改 ATK 源码，在 `acl_init` 之前 dlopen | 更晚，更没用 |
| 不启动 torch_npu | `npu` 设备类型不存在，ATK 跑不了任何用例 |

`ASCEND_OPP_PATH` 下的 `vendors/config.ini` 有 `load_priority`，但它只排 vendor
之间的先后，管不到 built-in；`libregister.so` 里也没有相关环境变量。

**后果：存在同名内置算子时，ATK 测的是仓库里的旧实现，不是本次交付的代码。**
独立执行器不碰 torch_npu，用的是算子包自带的 tiling——这是它不可替代的价值，
也是 A3.5 在这种情形下要跑全量的原因。


## 跨版本指纹

`input/gen/env.gen.json` 是冻 golden 那台机器的指纹，**跑测侧唯一会读的 `gen/` 文件**。
`verdict.py` 拿它比本机 `stage/env.json` 的 `atk.version`：不同时照跑，但报告抬头标出
跨版本、结论打折。老用例包平铺在 `input/` 根，两处都认，都没有时写「未知」。

**不要改它。** `gen/` 其余内容是生成侧的现场，跑测侧一行都不读。

## A2.6 tiling 归属（`backend=aclnn` 时不可跳过）

`backend=npu` 时整段跳过：这一步查的是「CANN 内置的同名 aclnn tiling 有没有
抢先注册」，npu 剖面没有 aclnn 接口，不存在这种竞争。A3.5 直接按无竞争分支走。

```bash
cd <现场>/work && source evidence/env.sh && <python> <skill>/scripts/run_atk.py \
    --mode tiling --op <op> -c ../input/cases.json --golden ../input/golden \
    --facts ../input/facts.json --install stage/install.json -o stage/tiling.json
```

| 退出码 | `shadowed` | 含义 | A3.5 走哪条 |
| --- | --- | --- | --- |
| 0 | 空 | ATK 用的就是算子包自带的 tiling | 无竞争分支 |
| 1 | 非空 | **CANN 内置的同名 tiling 抢先注册，算子包那份被拒** | 有竞争分支 |
| 3 | — | `install.json` 里没有 `vendor_dir`，或算子包下找不到 kernel | 回 A2 |

退 1 不是错误，是一种形态，**但它决定 A3 的结论能不能当验收依据**。
换不掉这份 tiling 的原因与四条走不通的路子见 run-accuracy.md「ATK 换不掉 tiling」。

**A2.6 / A3 / A3.5 是一条链，走之前先看清它分叉在哪：**

```text
              A2.6 查 tiling 归属
                       │
        ┌──────────────┴──────────────┐
   shadowed 空                   shadowed 非空
        │                             │
A3   ATK 跑全量              A3   ATK 跑全量（测的是 CANN 内置实现，只作对照）
     └失败→ 隔离复验                  │
            每条单起进程       A3.5 C++ 跑全量（用算子包自带的 tiling）
            剔除 batch_only          └失败→ 换卡单条复跑，剔除 batch_only
        │                             │
A3.5 C++ 复跑 ATK 判失败的那批         │
     ＝交付算子作者的复现器            │
        │                             │
   ▼ 结论以 ATK 为准             ▼ 结论以 C++ 为准
```

## A3 精度

读 [run-accuracy.md](run-accuracy.md)。

```bash
cd <现场>/work && source evidence/env.sh && <python> <skill>/scripts/run_atk.py \
    --mode accuracy --op <op> -c ../input/cases.json \
    --golden ../input/golden --facts ../input/facts.json -o stage/accuracy.json
```

| 判据 | 取值 |
| --- | --- |
| 精度过没过 | `stage/accuracy.json` 的 `passed`、`pass_rate`。**退出码 0 不等于通过** |
| 标杆节点 | `input/golden/manifest.json` 的 `baseline_dir` 决定，不由你选；脚本开跑前打印，对不上回生成侧改 |
| 跑几轮 | **一轮**。`facts.json` 的 `non_contiguous.required` 为真时两种布局按 `ratio` 混在这一轮里，分组落在 `stage/accuracy.json` 的 `layout`，**两个通过率分组报，不合并** |

**要给 `atk` 加执行参数前先读 [atk-task-options.md](atk-task-options.md)** ——
`--disable_id_seed`、`--input_data` 会让输入与冻 golden 时不是同一批，比对全部无意义**且不报错**。

**全部**执行失败时脚本退 2 停在 A3，按 troubleshooting.md 分层定位，不跑隔离复验。
只挂一部分不拦，**有失败就必须隔离复验**：

```bash
cd <现场>/work && source evidence/env.sh && <python> <skill>/scripts/run_atk.py \
    --mode isolate --op <op> -c ../input/cases.json --golden ../input/golden \
    --facts ../input/facts.json --ids-from stage/accuracy.json \
    -o stage/isolate.json
```

| 约束 | 说明 |
| --- | --- |
| 布局口径 | 不用管。比例从 `--facts` 读，与全量同一个来源，同一条用例的布局自动一致 |
| 一轮 solo，不迭代 | 执行失败与精度不符**都收**，每条各自单独跑一遍 |
| 卡号不填 | `--isolate-devices` 默认 `auto`，现查空闲卡并发，**最多用 4 张**（机器公用，独占时 `--max-devices 0`）。耗时对并发度非单调，用满卡不是最优也不是上界，实测曲线见本文「成本」 |
| 时间上限 `--max-isolate` | 默认 0 = 不限。被截掉的记 `not_checked`，是未判定，不是通过 |

`stage/isolate.json` 分四个桶，`verdict.py` 自动读：

| 桶 | 这条用例单独跑 | 结论 |
| --- | --- | --- |
| `exec_failures` | 还是挂 | 未通过（部署/kernel） |
| `accuracy_false` | 跑起来了，算错了 | 未通过（归因不同） |
| `batch_only` | 通过 | **不是缺陷**，`batch_only_detail` 带 `dim` 区分批内挂在执行还是精度 |
| `not_checked` | 被 `--max-isolate` 截断 | 未判定 |

**`batch_only` 要在报告里显式列出**，不能只给条数——判据与理由见 [run-isolate.md](run-isolate.md)。
`cascade` 字段保留但恒为空，只为兼容旧报告。
`repro/failed/cases.json` 装前两个桶。
