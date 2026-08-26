# 精度跑测

待验收算子在 aclnn 节点实算，标杆节点不重算——直接读生成侧冻结的 golden。
同一套用例反复验收多个 PR 时 golden 不漂，结论可比。

## 节点拓扑

```bash
atk node --backend aclnn --devices 0 \
    node --backend cpu --task accuracy_load --output_path <golden 绝对路径> \
    task -c cases.json --task accuracy
```

| 部分 | 作用 |
| --- | --- |
| `node --backend aclnn` | 待验收节点，走 pyaclnn 直接调 `aclnn<Op>` |
| `node --backend cpu --task accuracy_load` | 标杆节点，从磁盘读 golden 而不是重算 |
| `--output_path` | golden 根目录，**必须是绝对路径** |
| `task --task accuracy` | 任务类型 |

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

**`save_name` 取的是用例文件的基名**（`atk/tasks/result_process.py:67`）。
生成侧的 golden 是用 `cases.json` 跑出来的，所以子目录叫 `cases`。
跑测时用例文件也必须叫 `cases.json`，换成 `smoke.json` 就会去找
`golden/cpu_0/smoke/`，一条都找不到，报：

```text
标杆输出为空，请检查标杆是否运行失败或者没有输出
```

这就是冒烟子集要写成 `smoke/cases.json`（放子目录、保住文件名）而不是
`smoke.json` 的原因。`sample_smoke.py` 已经按这个规则写。

## 报告怎么读

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

## 失败了怎么归因

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

## 全量跑多久

roll 的 180 条用例在单卡上约 1–2 分钟。用例数上千或开了
`has_upper_border` 会显著变长，`run_atk.py` 的超时是 7200 秒。
