# CLAUDE.md — 社区算子 Skill 仓

每次会话自动加载。只写经过源码验证的事实，只写三个 skill 共用的规范。

写本仓任何 skill 文档前，行文规则已随下面这行导入常驻上下文，不需要主动去读：

@.claude/rules/prose-style.md

一句话摘要：并列观点成列表、连贯论证成段、单句独占段只留给判据和禁令。
机械判据在各 `test_document_style.py` 与仓级 `tests/test_repo_docs_style.py`，
但它们只抓子集，脚本没红不等于合规。

## 本仓是什么

三个平级 skill，覆盖社区算子任务从任务书到验收结论的完整链路。

| skill | 做什么 | 开发规则 |
| --- | --- | --- |
| `skill/repo-task-doc-write/` | 开发前，把需求写成可验收的任务书 | 它自己的 `CLAUDE.md` |
| `skill/repo-task-case-gen/` | 按任务书生成、冻结并封印 ATK 用例 | 它自己的 `CLAUDE.md` |
| `skill/repo-task-atk-accept/` | 接收交接包，在 NPU 上验收精度与性能 | 它自己的 `CLAUDE.md` |

任务书是生成侧的唯一需求输入，封印交接包是生成侧与验收侧的唯一接口。
面向使用者的说明在 `docs/skills/<name>/`，`README.md` 是仓门面。

### 改哪个 skill 读哪份 CLAUDE.md

skill 专属红线、阶段、架构理念与双份纪律在各自目录的 `CLAUDE.md`，本文件不重复。

**目录级 `CLAUDE.md` 由 Claude Code 按需加载。** 写进本文件的内容每次会话都全量加载，
所以这里只留三个 skill 共用的部分。

## 文件结构

```
repo-task-atk-test/              # 上游仓根
├── CLAUDE.md                    # 本文件：仓级共同规范
├── README.md                    # 三个 skill 的门面与安装
├── skill/                       # 三个平级、自足的 skill
│   ├── repo-task-doc-write/     # 任务书撰写
│   ├── repo-task-case-gen/      # S1–S2：生成与封印
│   └── repo-task-atk-accept/    # S0、S3–S5：验收与裁决
├── docs/
│   ├── skills/<name>/           # 使用者设计与上手文档
│   ├── atk-facts.md             # ATK 事实基线
│   ├── development/             # 开发规则与架构演进
│   └── superpowers/             # 历史 specs 与 plans
├── tests/                       # 仓级跨目录与 manifest 门禁
└── third_party/ATK/             # 只读 submodule
```

什么进仓、什么不进仓，见 `docs/development/repo-hygiene.md`。仓库根出现
`evidence/` 一律当 bug 查：它属于验收工作区，且被忽略规则隐藏。

## 开发流程

修改任一 skill 时依次执行：

1. 新产物先登记进本侧 `artifact-contracts.json`
2. 在 `references/` 补规范，不把知识藏在脚本注释里
3. 写失败测试，再实现到测试通过
4. 运行 `render_views.py --write` 更新派生视图
5. 通过本侧结构门禁与仓级跨目录门禁

修改骨架标为 `shared` 的脚本或 reference 时，两侧必须同步；仓级
`tests/test_shared_sync.py` 会给出漏改文件的复制命令。

## 开发态启动

ATK 没有被 pip 安装时，回归必须显式给 `PYTHONPATH`。三个目录存在同名测试模块，
聚合运行还要保留 `--import-mode=importlib`：

```bash
PYTHONPATH=third_party/ATK python3 -m pytest skill/ tests/ -q --import-mode=importlib
```

不要在全局 Python 里执行 `pip install -e third_party/ATK`；需要安装时先建虚拟环境。
ATK 版本锁与升级后果见两个验收相关 skill 的 `CLAUDE.md`。

## 当前状态

S11 本机基线分侧统计如下：

- 生成侧：22 failed、597 passed；失败集合与旧基线逐名相同
- 验收侧：337 passed、0 failed
- 仓级：10 passed、0 failed

22 条既存失败包含缺少 torch、PyYAML 与两条已知行文门禁；本轮不得新增失败。

**最后更新：** 2026-08-24（S11：生成与验收改为两个平级、自足目录）
