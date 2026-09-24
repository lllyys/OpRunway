# 只读参考仓清单（ignored `repos/`）

查上游仓、算子工程或工具的行为时**先读这里的源码，再推断**。这批 clone 是本机资产，
不入版本库（`repos/` 已 ignored），也不参与任何验收证据链——它们只用来查事实。

**基线是记录时的快照。** 各会话会自行 `fetch`/`pull`，用前跑
`git -C repos/<名> log -1 --format='%h %ci'` 自核一次；拿基线当不变量的断言会过期而不自知
（这条与 `acceptance-roles.md` 对 profile 基线的要求同源）。非 git 目录是解包的 PR 树，
没有 remote 与 commit。

## 一、上游 skill 与规范

| 目录 | 来源 | 用途 |
| --- | --- | --- |
| `repo-task-atk-test/` | `Justbin/repo-task-atk-test` | 本仓 `plugin/` 的镜像源；同步与提 PR 的基线，流程见 AGENTS §2 |
| `cannbot-skills/` | `cann/cannbot-skills` | skill 形式规范的取材来源（见 `plugin/.claude/rules/skill-style.md`） |

## 二、算子仓（验收目标）

各仓维护者不同、测试形态不同——**一个仓的现状不能推断另一个仓**（`acceptance-roles.md` §2）。
读 profile 相关事实（入口头、`build.sh` 参数、frame 强度）必须落到具体仓。

| 目录 | 来源 | 与本仓的关系 |
| --- | --- | --- |
| `ops-blas/` | `cann/ops-blas` | BLAS 线验收目标；`test/frame/` 九个共享头即「强 frame」的实例 |
| `ops-sparse/` | `cann/ops-sparse` | sparse 剖面（`sparse_frame` profile）目标仓 |
| `ops-solver/` | `cann/ops-solver` | 0923 任务目标仓；无 GTest、无 `csv_loader.h`、全仓无 `.csv`（「无 frame」的实例） |
| `ops-math/` `ops-nn/` `ops-cv/` `ops-transformer/` `ops-collections/` | `cann/*` | ATK 线与其他任务批次的目标仓 |

## 三、工具与框架

| 目录 | 来源 | 查什么 |
| --- | --- | --- |
| `msopprof/` | `Ascend/msopprof` | `msprof op` 的实现。性能采集口径的疑问看 `csrc/op_profiling/`（重放逻辑在 `prof_injection/`，注入被测进程）与 `docs/zh/user_guide/` |
| `ATK/` | `Ascend/ATK` | ATK 线的调用器源码（也是 `repo-task-atk-test` 的 submodule） |
| `asc-devkit/` | `cann/asc-devkit` | 算子开发套件 |
| `catlass/` `catccos/` `hixl/` `shmem/` `oam-tools/` `amct/` | `cann/*` | 各类库与工具，按需查 |
| `Ascend-pytorch/` `cann-recipes-infer/` | `Ascend/pytorch`、`cann/cann-recipes-infer` | 上层框架与推理样例 |

## 四、任务来源与开发者交付副本

| 目录 | 来源 | 说明 |
| --- | --- | --- |
| `cann-ops-competitions/` | `cann/cann-ops-competitions` | 社区任务书所在（判据权威，数据待核） |
| `cannbot-ops-input/` | `Justbin/cannbot-ops-input` | 任务输入集，**17G，最大的一个** |
| `ops-math-roll/` | 开发者 fork | PR 副本 |
| `ops-cv-*-feat-*/` `ops-math-feat-*/` | 解包的 PR 树（非 git） | 开发者交付物副本，身份是**被测物**，不能当证据 |
| `AscendOpTest/` | 第三方 | 参考实现 |

## 五、新增 clone 的约定

放 `repos/<仓名>/`，在本文件对应分类下加一行（来源 URL + 用途一句）。用 `--depth 1`
够查源码就行——这批仓合计已近 20G，最大单仓 17G。属于开发者交付物的副本要在说明里
标明身份，避免后来者误当证据。
