# 失败定位

先分层，再动手。改错层次比不改更糟——把部署问题当用例问题去改用例，
会把一个能发现的缺陷改没。

## 五层

| 层 | 典型报错 | 改哪里 |
| --- | --- | --- |
| 环境 | `npu-smi` 不在 PATH、`import torch_npu` 失败 | source CANN 环境 |
| 部署 | 算子未注册、符号找不到、加载到内置实现 | 重跑 A2，见 build-deploy.md |
| 接口适配 | 参数数量/类型不匹配 | 回生成侧核 `facts.json` 与 YAML |
| 用例合法性 | 参数超出文档约束、空张量 | 回生成侧改 YAML 的 boundary 或约束器 |
| 算子实现 | 精度不符、aicore 异常 | **不改**，收集日志给算子作者 |

**最后一层不属于本 skill 的修复范围。** 精度差异与硬件异常都是待验收对象
自身的缺陷，本 skill 的产出就是把它准确地报出来。

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
ls golden/                      # 应有 cpu_0/
ls golden/cpu_0/                # 子目录名要等于用例文件的基名
ls golden/cpu_0/cases/0/        # 应有 output_0.pt 与 output_info.json
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

路径指向 `/usr/local/Ascend/...` 就是加载错了，`ATK_CUSTOM_OPP_PATH`
没生效。`run_atk.py` 在开跑前会检查这个环境变量，退出码 3 就是它拦的。

### NPU aicore 异常

关键词：`aic-error`、`DDR address out of range`、`Aicore kernel execute failed`、
`EZ9999`、`EZ3002`。

这是算子 NPU 实现的缺陷，**不要尝试修复，也不要改用例绕开**。做三件事：

1. 记下触发的用例 id、dtype、shape、attr 取值
2. 从 `evidence/*.log` 与 `~/ascend/log/` 摘出异常段落
3. 写进报告的失败分组，标注为 aicore 异常而不是精度不符

aicore 异常会让同一批次的后续用例连带失败。判断是否连带看
`failed_ids` 是否连续——连续一大段多半是第一条炸了之后的余波，
真正要报的是第一条。

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
| 内置基线轮（`--builtin-baseline`） | 内置实现的缺陷，该用例不进性能比值，写进报告备注 |

两种情况都**不要**改用例绕开。脚本的超时会兜底，但等超时很浪费，
确认卡死后直接 `pkill -f run_atk.py` 再按上面归类。

## 冒烟挂了怎么办

先分清挂的是哪一种：

| 冒烟结果 | 去向 |
| --- | --- |
| 执行失败率 **> 20%** | 停在 A3。部署或适配坏了，同一个问题重复 180 遍不产生新信息 |
| 执行失败率 **≤ 20%** | 进 A4。部署是好的，这几条是算子缺陷，A4 后用 `--mode isolate` 复验 |
| 只是**精度不通过** | 进 A4。那是真实发现，正是要在全量里测准的东西 |

高失败率按上面的层次定位，改完重跑冒烟再进 A4。

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
