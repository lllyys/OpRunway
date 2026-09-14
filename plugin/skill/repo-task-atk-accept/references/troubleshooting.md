# 失败定位

## 目录

- 五层
- 按报错查
- 共卡
- 整类 dtype 全挂，报错是 `EZ1001`
- 全量一条都没跑起来
- 日志在哪

先分层，再动手。改错层次比不改更糟——把部署问题当用例问题去改用例，
会把一个能发现的缺陷改没。

## 五层

| 层 | 典型报错 | 改哪里 |
| --- | --- | --- |
| 环境 | `npu-smi` 不在 PATH、`import torch_npu` 失败 | 重跑 `probe_env.py`，它会自己 source |
| 资源 | 卡上有别的 `atk` 进程、`run_atk.py` 退 3 | 换一张卡，见下方「共卡」 |
| 部署 | 算子未注册、符号找不到、加载到内置实现 | 重跑 A2，见 build-deploy.md |
| 接口适配 | 参数数量/类型不匹配 | 回生成侧核 `facts.json` 与 YAML |
| 用例合法性 | 参数超出文档约束、空张量 | 回生成侧改 YAML 的 boundary 或约束器 |
| 算子实现 | 精度不符、aicore 异常 | **不改**，收集日志给算子作者 |

**最后一层不属于本 skill 的修复范围。** 精度差异与硬件异常都是待验收对象
自身的缺陷，本 skill 的产出就是把它准确地报出来。

**但精度大面积不通过时，先看一眼 `facts.json` 有没有 `baseline_params`。**
那个字段记的是「aclnn 会读、CPU 标杆没读」的入参。有条目就说明 golden 与 NPU 算的
不是同一件事，失败原因在基线，不在算子——归到「接口适配」层，回生成侧，别报给算子作者。

| 现象 | 先怀疑 |
| --- | --- |
| 少数用例失败，集中在某些 dtype 或某段 shape | 算子实现，照第五层走 |
| 绝大多数用例失败，且 `facts.json` 里有 `baseline_params` | 基线丢了入参，回生成侧 |

## 按报错查

### `自定义API'xxx'找不到或者import失败`

YAML 的 `api_type` 或 `aclnn_api_type` 填了没注册的名字。
默认值是 `function` 与 `aclnn_function`（`atk/configs/case_config.py:91`）。
`pyaclnn` **不是**注册名，它是 backend 名字，填进去必挂。

写了自定义执行器时，注册名要与 YAML 字段逐字相同，且跑测命令要带
`-p function_<op>.py`。

### `标杆输出为空，请检查标杆是否运行失败或者没有输出`

golden 路径对不上，九成是用例文件名变了。ATK 用**用例文件基名**当
golden 的子目录名，详见 run-accuracy.md「golden 目录结构与文件名的耦合」。

自查三步：

```bash
B=$(python -c "import json;print(json.load(open('golden/manifest.json'))['baseline_dir'])")
ls golden/                      # 应有 $B/（torch 基线是 cpu_0，内置基线是 pyaclnn_builtin）
ls golden/$B/                   # 子目录名要等于用例文件的基名
ls golden/$B/cases/0/           # 应有 output_0.pt 与 output_info.json
```

### 跑测长时间停在 `0/N`，没有任何报错

**不是慢，是执行 worker 崩了。** ATK 没有超时兜底，子进程段错误后主进程会一直等，
进度条永远停在 `0/N`。真机上见过 5 条用例挂 11 分钟。

**`run_atk.py` 已经自动收掉这种：** 它盯 `evidence/<mode>.log` 的增长，180 秒
不长就杀掉进程并打印「日志 180s 没有新增，判定执行 worker 崩了」。所以不要靠干等，
也不要调大超时——正常跑的任务日志一直在长，不会被误杀。

被杀之后确认是哪一种：

```bash
ps -eo pid,stat,cmd | grep "[c]onda/bin/atk node"   # 有 Z（zombie）就是崩了
ls -t ~/ascend/log/run/plog/*.log | head -1          # 末尾时间与卡住时刻相同且无 ERROR 行 = 硬崩
```

已知会导致它的一种情况：**golden 的 `output_info.json` 多套了一层 list**
（`[[{...}]]` 而不是 `[{...}]`）。`accuracy_load` 把它整个塞进 `output_info_list`，
`convert_output_data` 拿到 list 而不是输出描述，NPU 侧照它建输出张量后段错误。

```bash
B=$(python -c "import json;print(json.load(open('golden/manifest.json'))['baseline_dir'])")
head -c 120 golden/$B/cases/0/output_info.json      # 应是 [{...}]，不是 [[{...}]]
```

是 `[[{...}]]` 就**回生成侧重跑 `freeze_golden.py`**（它会把基线的这份对齐成
cpu 节点那份——那才是当初申请 `out` 时用的描述），不要在这里就地改 golden。

一分钟就能分清是不是 golden 的问题：把 `accuracy_load` 去掉，标杆节点改成现场跑
torch，同一批用例再来一轮。跑得通就说明算子和部署都没问题，问题在 golden。

```bash
atk node --backend aclnn --devices <device> node --backend cpu \
    task -c cases.json --task accuracy -p function_<op>.py
```

### `参数数量不匹配：传入 N 个，预期 M 个`

pyaclnn 用用例的输入列表拼出的参数表，与磁盘头文件里的签名对不上。
报错会逐位置列出传入类型与预期类型。

| 差几个 | 多半是 |
| --- | --- |
| 传入比预期多 1 | YAML 的 `inputs` 里把 `out` 也写进去了 |
| 传入比预期少 1 | 漏了一个输入参数，或可选参数没表达 |
| 数量对但类型错位 | `inputs` 顺序与签名不一致 |

这是用例包缺陷，回生成侧改，不在这里 patch。

### `算子未注册` / kernel 找不到

| 可能 | 查法 |
| --- | --- |
| `--soc` 填错，编的是别的芯片的 kernel | `env.json` 的 `soc.raw` 与构建时的 `--soc` 对一遍 |
| `ASCEND_CUSTOM_OPP_PATH` 没设 | `echo $ASCEND_CUSTOM_OPP_PATH` |
| 装包装到别的目录去了 | `install.json` 的 `vendor_dir` |

### 加载到了 CANN 内置实现

日志里这一行给出实际加载的 so，路径必须落在本轮的 vendor 目录下：

```text
import aclnnRollGetWorkspaceSize from <路径> success!
```

路径指向 `/usr/local/Ascend/...` 就是加载错了，`ATK_CUSTOM_OPP_PATH` 没生效。

两道拦截，都不用你肉眼看日志：开跑前 `run_atk.py` 查这个环境变量在不在（退 3）；
**每轮跑完再核一遍日志里这些路径落没落在本轮 vendor 目录下**（退 2，把越界的
路径逐条打出来）。第二道是必需的——环境变量设了不代表绑对了，而一份测了内置
算子的报告看上去完全正常，通过率可能还很好看。

### NPU aicore 异常

关键词：`aic-error`、`DDR address out of range`、`Aicore kernel execute failed`、
`EZ9999`、`EZ3002`。

这是算子 NPU 实现的缺陷，**不要尝试修复，也不要改用例绕开**。做三件事：

1. 记下触发的用例 id、dtype、shape、attr 取值
2. 从 `evidence/*.log` 与 `~/ascend/log/` 摘出异常段落
3. 写进报告的失败分组，标注为 aicore 异常而不是精度不符

aicore 异常会让同一批次的后续用例连带失败。**不要靠肉眼看 `failed_ids`
连不连续来判**，跑 `--mode isolate`：它把失败集合迭代重跑，分出 `exec_failures`
与 `cascade`，原理见 [run-isolate.md](run-isolate.md)。

`isolate.json` 的 `aicore_evidence` 列的是日志里真命中的关键词。**它为空就
不要在报告里写 aicore 异常**——连带是真的，但成因未定。

### 任务卡在某条用例不动

进度条停住、日志几分钟不再增长，多半是某条用例在设备上不返回。先确认卡在哪条：

```bash
grep -oE "\[case [0-9]+\]" evidence/<mode>.log | tail -3
stat -c %y evidence/<mode>.log    # 日志最后修改时间
```

三条重复的 `[case N]` 加上停滞的时间戳就说明卡在 N。

| 卡在哪一侧 | 含义 |
| --- | --- |
| 待验收实现 | 算子缺陷，记下该用例的 dtype/shape/attr，报给算子作者 |
| 内置基线轮（日志 `evidence/performance_builtin.log`） | 内置实现的缺陷，该用例不进性能比值，写进报告备注 |

两种情况都**不要**改用例绕开。停滞检测会自己收掉进程，不用手工 `pkill`。

## 共卡

`run_atk.py` 开跑前问设备本身，卡上有**任何**进程就退 3 并列出 pid、进程名、占用显存。
**一个算子占一张卡**，并行验收多个算子时每个工作目录用不同的 `--devices`，
卡号见 `env.json` 的 `npu.free_devices`。

**判据是设备上有没有进程，不是进程叫不叫 atk。** 早先用 `ps` 找命令行含 `atk` 的
进程，别人跑别的训练脚本占着卡时直接放行——实测因此
把 138、161、170 三条判成失败，换空闲卡单卡重跑 5/5 全过，16 条「缺陷」里 3 条
是这么来的。

共卡的后果不对称，所以不能「反正精度是对的就先凑合跑」：

| 阶段 | 后果 |
| --- | --- |
| 精度 | 结论仍然对，只是慢 |
| 性能 | **结论静默作废**——Device 耗时里混进了另一个算子的负载 |
| 隔离复验 | 别人的 aicore 异常被算成本算子的失败，报成缺陷 |

自己动手查是哪张卡被谁占（脚本用的是同一条命令）：

```bash
npu-smi info -m                              # Chip Logic ID 列就是 --devices 的号
npu-smi info -t proc-mem -i <npu> -c <chip>  # 空闲时打 "No process in device."
```

**卡号不能按 `npu*2+chip` 算**，每卡芯片数随机型变（A2 是 1，A3 是 2），只能查 `-m`。

`--allow-shared-device` 只在**确认列出来的都是自己的残留**时用。别人的进程占着卡
就换一张，共卡跑出来的失败用例无法归因。

## 整类 dtype 全挂，报错是 `EZ1001`

```
AclNN_Parameter_Error(EZ1001): Tensor self not implemented for DT_INT8,
should be in dtype support list [DT_INT32,DT_FLOAT16,DT_FLOAT,DT_INT64,DT_BOOL,DT_BFLOAT16,]
```

**这是 aclnn 入口在 `GetWorkspaceSize` 把入参挡回来了，一条都没进 kernel。**
它不是精度问题，别按精度查。两种成因，按顺序核：

| 先核 | 怎么核 | 对上了怎么办 |
| --- | --- | --- |
| A2 装错了算子目录 | `stage/install.json` 的 `op_dir` 与 `source`，比对任务书点名的目录与提交标题 | 用对的目录重跑 A2，别改用例 |
| 这份源码确实没实现这些 dtype | 报错里的 support list 与任务书要求的 dtype 集合对不上，且 `op_dir` 确实是待验收那份 | 算子缺陷，把报错原文报给算子作者 |

第一种更常见，而且**符号核查判不出来**：不同算子目录能实现同一套 aclnn 接口
（母仓里实测见过两个不同目录导出同一个 `aclnn<Op>GetWorkspaceSize`）。
装错时 A2 退 0、A3 跑完才暴露，
报告还会把它写成「精度不达标」。A2.5 dtype 冒烟就是为拦这一条加的。

## 全量一条都没跑起来

`run_atk.py --mode accuracy` 退 2 且执行失败数等于总用例数，是部署或适配坏了，
不是算子缺陷。**不要跑隔离复验**——同一个错误重复几十遍不产生新信息。
按上面的五层定位，改完重跑全量。

只挂一部分不拦：那说明部署是好的，是个别用例触发了算子缺陷，正是隔离复验
要测准的东西。

## 日志在哪

| 内容 | 路径 |
| --- | --- |
| 构建 | `evidence/build.log` |
| 装包 | `evidence/install.log` |
| 跑测 | `evidence/<mode>.log` |
| ATK 详细日志 | `atk_output/<save_name>_<时间戳>/log/atk.log` |
| CANN 侧日志 | `~/ascend/log/` |

真实报错通常不在控制台，在 `atk_output/*/log/atk.log`。捞它：

```bash
grep -E "ERROR|run opp failed" atk_output/*/log/atk.log | sort -u | head -20
```
