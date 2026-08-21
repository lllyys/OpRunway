# 社区算子任务 Skill 仓

两个供 AI 编程助手使用的 skill，覆盖社区算子任务的两端。

| skill | 子流程 | 做什么 | 需要 NPU | 详细 |
| --- | --- | --- | --- | --- |
| `repo-task-doc-write` | — | 开发前写任务书 | 否 | [文档][doc-design] |
| `repo-task-atk-test` | `repo-task-case-gen` | 生成并封印用例 | 否 | [文档][atk-design] |
| `repo-task-atk-test` | `repo-task-atk-accept` | 验收精度与性能 | 是 | [文档][atk-design] |

[doc-design]: docs/skills/repo-task-doc-write/design.md
[atk-design]: docs/skills/repo-task-atk-test/design.md

任务书是前者的产物、后者的唯一输入，两个 skill 因此同仓。

## 安装

### 取得本仓

```bash
git clone https://gitcode.com/Justbin/repo-task-atk-test.git
cd repo-task-atk-test
```

### 装 skill

普通 skill 三选一，`<name>` 换成目录名；验收 skill 再按下方扫描方式安装。

```bash
# 软链接 / 用户目录 / 项目目录（三选一）
ln -s "$PWD/skill/<name>" ~/.claude/skills/<name>
cp -r skill/<name> ~/.claude/skills/
cp -r skill/<name> <你的项目>/.claude/skills/
```

- Claude Code 目录安装只扫一层，需把三个目录一起复制：
  `cp -r skill/{repo-task-atk-test,repo-task-case-gen,repo-task-atk-accept} ~/.claude/skills/`；
  两个别名依赖真身并列安装。
- Plugin 安装只用 manifest 注册真身及嵌套子目录，不安装别名，否则会同名重复注册。
- 递归扫描工具（如 opencode）只装真身；装别名会重复显示两个子入口。

### repo-task-atk-test 还要装 ATK

生成侧只需 Python、ATK 和 CPU 版 torch；验收侧还需 NPU 与可 source 的 CANN `set_env.sh`。

```bash
git submodule update --init

cd third_party/ATK
pip install wheel
python setup.py bdist_wheel
pip install dist/*.whl
cd ../..
```

**不要执行 `pip install atk`**，也不要换用其他版本的 ATK。

下面两条都要正常返回：

```bash
pip show atk
atk case --help
```

装不上、或命令报错，见 [常见卡点](docs/skills/repo-task-atk-test/quickstart.md#常见卡点)。

## 改 skill 本身

先读 [CLAUDE.md](CLAUDE.md)，它会指到对应 skill 的开发规则。
开发准则详解在 [docs/development/](docs/development/skill-development-principles.md)。
