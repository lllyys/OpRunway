# CLAUDE.md — 社区算子 Skill 仓

每次会话自动加载。只写经过源码验证的事实，只写两个 skill 共用的规范。

写本仓任何 skill 文档前，行文规则已随下面这行导入常驻上下文，不需要主动去读：

@.claude/rules/prose-style.md

一句话摘要：并列观点成列表、连贯论证成段、单句独占段只留给判据和禁令。
机械判据在各 `test_document_style.py` 与仓级 `tests/test_repo_docs_style.py`，
但它们只抓子集，脚本没红不等于合规。

## 本仓是什么

两个 skill，覆盖社区算子任务的两端。任务书是前者的产物、后者的唯一输入。

| skill | 做什么 | 开发规则 |
| --- | --- | --- |
| `skill/repo-task-doc-write/` | 开发前，把需求写成可验收的任务书 | 它自己的 `CLAUDE.md` |
| `skill/repo-task-atk-test/` | 开发后，用 ATK 验收精度与性能；内含生成用例与测试验收两个子流程 | 它自己的 `CLAUDE.md` |

面向使用者的说明在 `docs/skills/<name>/`，`README.md` 是仓门面。

### 改哪个 skill 读哪份 CLAUDE.md

skill 专属的红线、阶段、架构理念，全在各自目录的 `CLAUDE.md` 里，本文件不重复。

**目录级 `CLAUDE.md` 由 Claude Code 按需加载**——碰到某个 skill 目录下的文件时它才
进上下文，不需要在这里写 `@import`。写进本文件的东西每次会话都全量加载，所以这里只
留两个 skill 共用的部分。

## 文件结构

```
repo-task-atk-test/           # 本仓（远程 gitcode.com/Justbin/repo-task-atk-test）
├── CLAUDE.md                 # 本文件，仓级共同规范
├── README.md                 # 仓门面：两个 skill 是什么 + 安装
├── skill/                    # 两个 skill 本体 + 两个别名目录，唯一发布物
│   └── repo-task-atk-test/   # 含 case-gen/ 与 acceptance/ 两个子 skill 目录
│       ├── tests/            # 按 case_gen/、acceptance/、shared/ 归侧
│       └── dist/             # 两个独立 skill 的展开发布物（生成，不入库）
├── docs/                     # 不随 skill 发布
│   ├── skills/<name>/        # 使用者视角：design.md + quickstart.md
│   ├── atk-facts.md          # ATK 事实基线，按需读
│   ├── development/          # 开发规则 + 架构演进 + 素材归档
│   └── superpowers/          # specs/ 与 plans/
├── tests/                    # 仓级测试（含跨两侧的真机 CLI 链）
└── third_party/ATK/          # submodule → gitcode.com/Ascend/ATK（只读）
```

什么进仓什么不进仓，判断标准与两处不在根 `.gitignore` 里的忽略规则，
见 `docs/development/repo-hygiene.md`。

仓库根出现 `evidence/` 一律当 bug 查：那是验收工作区的产物目录，
`.gitignore` 盖住了它，所以泄漏不会让 `git status` 变脏，也不会有测试红。

## 开发流程

修改任一 skill 的标准流程：

1. **骨架先行：** 新产物进该 skill 的骨架 JSON
2. **知识前置：** 在 `references/` 补充规范，不是在脚本里写注释
3. **TDD 实施：** 写失败测试 → 实现 → 测试通过
4. **派生视图：** `render_views.py --write`
5. **验收：** 结构不变量 + 防漂移测试

**遇到问题时的查找顺序：** 先读 `docs/development/skill-development-principles.md`，
再读该 skill 的 `references/`，最后读源码。

## 开发态启动（先跑通这一步，否则回归会假绿）

克隆漏了 `--recursive` 会拿到空的 `third_party/ATK/`，补救是 `git submodule update --init`。

**ATK 没有被 pip 安装，跑回归必须显式给 `PYTHONPATH`：**

```bash
PYTHONPATH=third_party/ATK python3 -m pytest skill/ tests/ -q --import-mode=importlib
```

两个 skill 各有同名 `test_document_style.py`，不加 `--import-mode=importlib` 会在收集期报
import 冲突。

迁出前 skill 寄生在 ATK 仓根里，`import atk` 只是 CWD 巧合命中了 `./atk/`。
本仓 ATK 在 `third_party/ATK/atk`，巧合不再成立。

量具本身不会因此假通过——它们都响亮失败：`validate_cases.py` 退出码 3、
`capture_reference.py` 抛 `CaptureError`、`align_signatures.py` 让 ImportError 冒出来。
**会假绿的是回归测试**：`test_override_effective` 的 C2b 在 import 不到 ATK 时自行
skip，于是不给 PYTHONPATH 跑出来的"全绿"比真实覆盖少了一块，而计数看不出来。

不要图省事跑 `pip install -e third_party/ATK`：本机是 homebrew python3.14 且无 venv，
装下去会污染全局 site-packages。要装先建 venv。子模块的版本锁与升级后果见验收 skill 的 `CLAUDE.md`。

## 当前状态

本仓实测 22 failed, 980 passed, 19 skipped（跑法见上一节）。

22 条既存失败 = 20 条依赖 torch（本机未安装）+ 2 条行文门禁红
（`experimental_standard.md` 168/178 行超长、176 行用了「符号」）。

**最后更新：** 2026-08-21（验收 skill 拆成 case-gen 与 acceptance 两个子 skill）
