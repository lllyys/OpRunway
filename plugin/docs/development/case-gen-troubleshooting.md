# 用例生成：排障事实

生成侧 `CLAUDE.md` 只留「不知道就会把 YAML 或脚本写错」的契约性事实；**这里放另一半——
某个算子某一轮实测出来的分布、门槛与耗时**。改脚本时不需要读，调不出想要的用例分布
时按算子查。

出处一律标 `实测` 加算子名。环境是 Atlas A3 + ATK 26.8.8。

## 规模档与用例分布怎么调

| 事实 | 出处 |
| --- | --- |
| 填 `max_length: 2097152`（= large 门槛本身）时 large 只有 8/200 且全是 bf16；改 4194304 后 53/200，10 个 dtype 全覆盖 | IndexFillTensor 实测。**门槛值本身填进 `max_length` 等于只有最宽的 dtype 够得着** |
| 只配 `size_distributions` 到不了目标档，要连带调 `max_length` 与 `dim_values` | Roll 实测，全量 large 1 → 6 → 11 → 13 条 |
| 约束器写死形状凑规模档时，窄 dtype 整档落空 | UpsampleNearestExact1d 实测，`uint8 × large = 0` |
| `dim_numbers` 含 8 时 200 条 7 秒跑完，rank 8 占 39 条 | IndexFillTensor 实测，`max_length: 4194304` 下 |
| 200 条 golden 冻结 19 秒、198 MB | IndexFillTensor 实测，`skeleton.yaml` 那套数值 |
| Roll 的秩 0 那一档只能不测：`dim_numbers` 放 0 时 157 条里 43 条 golden 冻不出来，报的是 torch 的 IndexError | 实测 |
| `type: tensors` 的列表长度实际分布是 `len1:98 len2:55 len3:31 len4:26`（YAML 声明 `[1,2,3,4]`） | ForeachMulList 实测，成因是预算减半重抽 |
| `type: tensors` 逐元素独立抽 dtype，210 条里 94 条 x1 是 `[bf16, int8]` 这类，NPU 侧按 `561002` 整批假失败 | ForeachMulList 实测 |
| 约束器里 `x1[:] = x1[:1]` 会把秩 5–8 全部退化成单元素列表，dry-run、条数、golden 三关都不响 | ForeachMulList 实测 |

## 基线与执行器

| 事实 | 出处 |
| --- | --- |
| 内置 `aclnnBernoulli` 的符号在 `$ASCEND_OPP_PATH/../lib64/libopapi.so` 与 `libopapi_math.so` 里 | 实测，`nm -D` |
| `pyaclnn + cpu` 两节点带 `--save_data output` 能冻出内置实现的 golden，140/140；两轮独立冻结逐位相同 | Bernoulli 实测。这是拿内置当基线的前提 |
| `torch._C._nn._upsample_nearest_exact1d/2d` 与 aclnn 同名接口**逐位一致**，scales 取 0 / 5.0 / 0.4 三档 NPU 与 CPU 全 match | 实测，torch 2.10.0+cpu + torch_npu |
| 执行器把 aclnn 入参解包又扔掉时，golden 与被测算子算的不是同一件事，而 S1–S4 四道判据全不响 | UpsampleNearestExact1d/2d 实测：1d 120 条里 100 条、2d 117 条与 NPU 对不上，零告警 |
| 同一个 torch 接口 `scales=0` 与 `scales=1.0` 结果不同（前者等价于 `F.interpolate(mode='nearest-exact')`，后者是 identity 映射）——**拿两组不同入参的调用去比对，会把入参差异误读成实现缺陷** | 实测，3→5 与 15→403 两组 |
| 冻 golden 时 worker 段错误的表现是反的：冒烟 5 条跑 11 分钟还停在 `0/5`，plog 停在同一秒且**没有任何 ERROR 行**，看着像性能问题或跑测侧的毛病，根因在生成侧存盘的结构 | 实测。定位它的对照实验：同算子同用例去掉 `accuracy_load` 现场跑 torch 标杆 → 5/5 通过，加回 → 崩 |

## 自带件在装机 ATK 上跑不起来

SpGemm(A2A3) 自带件实测，七处，`kit_lint.py` 的 A 到 E 五类判据就是照这些归纳的
（**归纳的是真因不是症状**，判据落在「解析名字」而不是「这七条」）。

| 序 | 症状 | 报错指向 | 真因 | 量具 |
| --- | --- | --- | --- | --- |
| 1 | `outputs` 是 `{"2": {...}}`，`CaseConfig` 只收 str/int | 用例 | 版本错配。**总数不能从 dict 键推**，那只是「第 2 个输出的属性」；总数在执行器的 `return` 元组里（这里是 4 个） | A、C |
| 2 | `api_type: "aclnn"` 而插件注册的是 `sparse_mm_public` | 用例 | 自带件内部不自洽 | B |
| 3 | `standard.acc` 的 `value` 是空串 | 用例 | 同上 | B |
| 4 | `not enough values to unpack (expected 10, got 0)` | 执行器的解包语句 | ATK 按 `name` 分流，有名进 kwargs，而执行器只读 `input_data.args`。**同一作者在 DenseToSparse 那份里写了 `args or tuple(kwargs.values())`，这份漏了** | D |
| 5 | `addmm: ... SparseCsr @ SparseCsr without MKL` | 环境缺库 | 宿主环境假设。CPU 标杆挑了 CSR，aarch64 上没有 MKL | **查不到** |
| 6 | `Float did not match Double`，落在结构输出上 | 比对器 | 结构判据 `case_config.name == "torch.sparse.mm"` 恒假（用例名是 `accuracy-spgemm-*`），三个整型输出走了值比对 | E |
| 7 | `Float did not match Double`，落在值输出上 | 比对器 | 版本错配。标杆按 FP64／complex128 算，而 `single_benchmark_compare.py:52` 的归一化只处理 fp16/bf16 对 fp32 | **查不到** |

耗时量级：200 条精度全量约 3 分钟（A3 单卡，含 CPU 标杆现算）；冒烟 4 条约 10 秒。
**每次试跑都是 90 秒起**，所以 1 到 4、6 这五处值得在开跑前用量具一次报全。

同一批 200 条用例，开发者自己那份不走 ATK 的自测脚本
（算子工程的 `torch_extension/test_spgemm_accuracy.py`，90 行纯 torch）**直接 200/200**。
两个当事方的独立实现交叉印证：**七处全在 ATK 接入层，与算子实现无关**。
它不能当验收结论——容差是作者自己写的，任务书口径在自带件的比对器里。
