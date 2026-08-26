# CLAUDE.md — 用例生成 skill

改这个 skill 时读。运行时规则在 `SKILL.md`，这里是开发规则。
行文规范、开发流程沿用仓根 `CLAUDE.md`，不复制。

唯一目标：**让零上下文 agent 拿一份任意结构的社区任务书，独立产出可跑测的用例包。**

## 下游是跑测侧

产物 `atk-case-<op>/` 被 `repo-task-atk-accept` 直接吃掉。使用态上两侧不强制串联，
**开发态上有四项硬契约**（`cases.json` 文件名、`golden/` 布局、`facts.json` 的
`performance.kind`、`function_<op>.py` 命名）——动到它们之前读
[用例包契约](../../docs/development/case-package-contract.md)，改完两侧都要真机复跑。

### 不为让跑测通过而改窄用例

跑测侧报某组用例挂了，先判是不是算子真的错了。把 YAML 的取值范围收窄、
把失败用例从 `cases.json` 里剔掉，都能让跑测变绿——那正是本 skill 存在意义的反面。

真要改窄，前提是**任务书或工程文档明确写了该取值非法**，且在 `facts.json` 的
对应项里留下 `source`。凭跑测结果反推约束一律不行。

## 三条红线

### 红线 1：不拿算子实现当判据

被测算子的 kernel、tiling、内部断言不能用来生成用例。
用它自己的断言反推取值范围，写错的断言永远测不出来。

| 可读 | 不可读 |
| --- | --- |
| 任务书 | `op_kernel/` |
| 工程 `docs/aclnn*.md`、`README.md` | `op_host/*_tiling.cpp` |
| `op_api/*.h` 的函数声明 | 内部断言、报错字符串常量 |

这条与 ATK 自带 `atk-quality-guard` 的 NEVER #4 正好相反——那个 skill
是给已验收算子做质量加固，目标是不产生假失败；本 skill 是验收社区提交的算子，
目标是能发现它错。所以 `op-engineering.md` 那套「扫 C++ assert」的做法不采纳。

### 红线 2：ATK 是黑盒

不改 `third_party/ATK/`。它是指向上游的 submodule，改一行就脏掉 gitlink。
行为不符预期就记为已知问题并在 reference 里写清楚，不打补丁。

### 红线 3：判据从数据推导

`check_facts.py` 校验的是 `facts.json` 的内容，不是 agent 的自述。
`gen_cases.py` 的用例数下限、`freeze_golden.py` 的 golden 覆盖率都是硬判据。
**不允许 agent 声称「我检查过了」就放行。**

## 设计取舍

### 为什么解析任务书由 agent 做而不是脚本

社区任务书没有固定结构。三份真实任务书里，roll 与 indexfill 有参数表但列名不同，
median 连参数表都没有。写正则去解析必然挂——这正是上一版 skill 失败的原因：
它硬要 `§2.3` 的代码块和 `§2.4` 的固定表头，三份任务书一份都过不了。

现在的分工是 **agent 解析，脚本校验结果**。任务书换个写法不影响脚本。

### 为什么用例包不做 SHA256 封印

封印挡住的是「有人偷偷改了用例包」，但真实场景里生成与跑测由同一个人在同一台机器
上连着做，这个风险不存在。封印的代价是每次改一行 YAML 都要重新走一遍封印流程。

用例包的可靠性由 S1–S4 的出口判据保证，不由摘要保证。

### 为什么 golden 要冻结

`atk aclnn cases.json --task accuracy` 也能跑，标杆当场算。但同一套用例反复验收
多个 PR 时，每次重算标杆意味着基准可能漂。冻结后 `accuracy_load` 读盘比对，
结论跨轮次可比。ATK 原生支持这条路径，不是我们造的轮子。

## 真机验证过的事实

下面这些是在 Atlas A3 + ATK 26.8.8 上实测出来的，改动前先确认还成立：

| 事实 | 出处 |
| --- | --- |
| `aclnn_api_type` 默认值是 `aclnn_function`，`pyaclnn` 不是注册名 | `atk/configs/case_config.py:91` |
| YAML 的 `inputs` 不含 `out`，ATK 从 CPU 标杆返回值推输出 | `atk/tasks/api_execute/aclnn_base_api.py:85` |
| `after_input_config` 签名是 `(self, index, input_case)` | `atk/case_generator/generator/base_generator.py:64` |
| `atk case` 写在 `result/<yaml stem>/json/all_<stem>.json` | `atk/case_generator/utils/reports.py:369` |
| 单 cpu 节点跑 accuracy + `--save_data output` 能产出 golden | 实测，180/180 |
| golden 路径是 `<根>/<backend>/<用例文件基名>/<id>/` | `atk/common/utils.py:259` + `atk/tasks/result_process.py:67` |
| 输入数据由 `default_seed` 决定，同一份 cases.json 两次跑输入相同 | `atk/tasks/dataset/base_dataset.py:85` |

## 目录结构

```
skill/repo-task-case-gen/
├── SKILL.md              入口：S0–S4
├── references/           5 份按需加载的知识
├── scripts/              4 个量具
└── assets/example/       Roll 的真实产物，可照抄
```

脚本之间不互相 import，各自独立可跑。没有共用模块，也没有双份同步纪律——
两侧的 `probe_env.py` 是两份不同的脚本，跑测侧要查 NPU 与 CANN，生成侧不查。

## 加东西之前

新增 reference 或脚本前先回答：**没有它，零上下文 agent 会在哪一步卡住？**
答不上来就不加。上一版 35 份 reference、69 个脚本的教训是，为「可能有用」加的
东西会挤占 agent 读真正有用那几份的预算。

**最后更新：** 2026-08-25（按最小闭环重建，Roll 全链路真机跑通）
