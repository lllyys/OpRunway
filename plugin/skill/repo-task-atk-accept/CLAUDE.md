# CLAUDE.md — 跑测验收 skill

改这个 skill 时读。运行时规则在 `SKILL.md`，这里是开发规则。
行文规范、开发流程沿用仓根 `CLAUDE.md`，不复制。

唯一目标：**让零上下文 agent 拿一个用例包和一份算子工程，独立跑出可信的验收结论。**

## 上游是生成侧

吃的用例包由 `repo-task-case-gen` 产出。使用态上不要求它必须来自生成侧——
任何符合结构的目录都收；**开发态上有四项硬契约**，本侧的 `run_atk.py` 与
`verdict.py` 都在读它们。动到之前读
[用例包契约](../../docs/development/case-package-contract.md)，那里也有归因表。

### 不就地 patch 用例包

用例包里的错要回生成侧改。在验收工作区手改一份 `cases.json` 或 `<op>.yaml`
只修好眼前这一次，下次生成出来的还是错的，而且这一轮的结论和用例包对不上，
复现不了。

工作区里的用例包是**副本**，改它不会传回生成侧——这一点在真机上很容易忘，
因为 `cp -r atk-case-<op> atk-verify-<op>` 之后两边看起来一样。

## 三条红线

### 红线 1：不拿算子实现当验收依据

工程的公开接口面可读，实现不可读。精度失败时**不要**去 kernel 里找原因然后判定
「这是预期行为」——那是让待验收算子给自己出考卷。

失败就是失败。归因写到「哪一组用例失败」为止，成因交给算子作者。

### 红线 2：ATK 是黑盒

不改 `third_party/ATK/`，也不改装机的 `site-packages/atk/`。
ATK 的精度标准、比较器、报告格式都按原样用。

### 红线 3：结论从数据推导

`verdict.py` 从 `install.json`、`accuracy.json`、`performance.json` 推结论。
**报告作者不写结论，只写证据链。**

`run_atk.py` 退出码 0 只表示任务跑完，`accuracy.json` 的 `passed` 才是结论，
而它来自 ATK 报告的「精度是否达标」列。

## 设计取舍

### 为什么要 ATK_CUSTOM_OPP_PATH

ATK 在 `ASCEND_CUSTOM_OPP_PATH` 下只认十种固定 vendor 名
（`{customize,custom} × {_math,_nn,_cv,_transformer,""}`，见
`atk/tasks/backends/lib_interface/acl_wrapper.py:545`）。

我们给每个算子起专用 vendor 名（`<snake>_atk`）避免多算子互相覆盖，
代价是不在白名单里。`ATK_CUSTOM_OPP_PATH` 是第一优先级且不做名字匹配，
值直接是 `.so` 路径，一步绕开。

顺带解决了另一件事：它的祖父目录就是 vendor 目录，pyaclnn 的签名自检只会
搜到本算子的头文件，不会撞上 CANN 装机目录里同名的官方接口。

### 为什么抽样在生成侧，冒烟在跑测侧

性能子集的分层用**规模档**，那是用例设计的知识（`case-strategy.md`），而且
「某一档抽不够」的修法是回生成侧调 `max_length` 重新生成——只有在那边做得到。
所以 `perf/cases.json` 由 `gen_cases.py` 产出，本侧只消费。

冒烟不一样：它验部署，与用例设计无关，`run_atk.py` 自己等距挑 5 条就够。

早先 `sample_smoke.py` 放在本侧，导致规模档阈值要在两个 skill 里各存一份，
真机上就出过两套阈值不一致（元素数 vs 字节数）。**不要把抽样搬回来。**

### 为什么冒烟子集要放子目录

ATK 用**用例文件基名**当 golden 的子目录名（`atk/tasks/result_process.py:67`
→ `atk/common/utils.py:259`）。golden 是 `cases.json` 跑出来的，
冒烟文件叫 `smoke.json` 就会去找 `golden/cpu_0/smoke/`，一条都找不到。

所以冒烟子集写成 `smoke/cases.json`——换目录不换文件名。

### 为什么冒烟只挑 5 条

冒烟验的是**部署通没通**，不是用例设计。部署坏了第一条就挂，挑 30 条只是
把同一个错误重复 30 遍。覆盖面由生成侧的全量与 `perf/cases.json` 负责。

判据随之从「失败率 > 20%」改成「**全部**执行失败」：5 条的样本按比例判没有
意义，挂 1 条就是 20%，而挂 1 条恰恰说明部署是好的。

### 为什么性能要精度先过

失败用例的耗时不可信，而且一个算错的算子跑得快没有意义。
`verdict.py` 里这条是硬规则，不由人判断。

## 真机验证过的事实

下面这些是在 Atlas A3 + CANN 9.0.0-beta.1 + ATK 26.8.8 上实测出来的：

| 事实 | 出处 |
| --- | --- |
| 社区算子目录不能独立构建，必须回母仓跑 `build.sh` | 实测，三个算子皆是 |
| `--soc` 取值由 `torch_npu.npu.get_device_name(0)` 推，A3 是 `ascend910_93` | 实测 |
| `--ops` 收蛇形目录名，不是 aclnn 接口名 | `build.sh --help` |
| vendor 目录是 `<install-path>/vendors/<vendor_name>_<仓后缀>` | 实测 |
| `accuracy_load` 是正式任务类型，aclnn 任务支持 | `atk/configs/base_config.py:60`、`atk/tasks/task_creator/aclnn_task.py:51` |
| 报告 xlsx 有 summary / failed cases / accuracy false cases / statistic 四张表 | 实测 |
| Device 耗时在 `statistic` 表的 `<node>_Device性能（us）` 列 | 实测 |
| 绑对了 so 时日志有 `import aclnnXxxGetWorkspaceSize from <路径> success!` | `atk/tasks/backends/pyaclnn_backend.py:246` |
| 任务挂到没写出报告时 `atk_output/` 里没有本轮的 xlsx | 实测，见下方「取报告必须卡时间下界」 |
| 内置 `aclnnMedian` 在 `keepdim=True` 时不返回，与 dtype、shape 无关 | 实测，3 条对照组直接验出 |

### 取报告必须卡时间下界

`_newest_report` 早期只取「最新」的 xlsx。任务失败到连报告都没写出来时，
它会拿到**上一轮**的报告——上一轮多半是通过的，于是这一轮被判成通过。

隔离复验里全是单用例连跑，这个坑最致命：IndexFillTensor 的 id=152 单独跑
5/5 必挂，却被判成连带失败，报告写出「真实失败 0 条」。

现在 `_newest_report(stem, since)` 只接受 `since` 之后产出的报告，
没产出就按失败算。**改这个函数时不要把 `since` 去掉。**

## 目录结构

```
skill/repo-task-atk-accept/
├── SKILL.md              入口：A1–A6
├── references/           4 份按需加载的知识
└── scripts/              4 个量具
```

脚本之间只有一处 import：`build_install.py` 装完包后调
`probe_env.write_env_sh` 重写 `env.sh`。除此之外各自独立。

两侧的 `probe_env.py` 是**两份不同的脚本**，不是共用件：跑测侧要查 NPU、CANN
与 SoC 并生成 `env.sh`，生成侧不需要。不做双份同步纪律。

## 加东西之前

新增 reference 或脚本前先回答：**没有它，零上下文 agent 会在哪一步卡住？**
答不上来就不加。

**最后更新：** 2026-08-25（按最小闭环重建，Roll 全链路真机跑通）
