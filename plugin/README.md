# 社区算子任务 Skill 仓

两个供 AI 编程助手使用的 skill，覆盖社区算子任务的两端。

| skill | 子流程 | 做什么 | 需要 NPU | 详细 |
| --- | --- | --- | --- | --- |
| `repo-task-doc-write` | — | 开发前写任务书 | 否 | [设计][doc-design] / [上手][doc-quick] |
| `repo-task-atk-test` | `repo-task-case-gen` | 生成并封印用例 | 否 | [设计][atk-design] / [上手][atk-quick] |
| `repo-task-atk-test` | `repo-task-atk-accept` | 验收精度与性能 | 是 | [设计][atk-design] / [上手][atk-quick] |

[doc-design]: docs/skills/repo-task-doc-write/design.md
[doc-quick]: docs/skills/repo-task-doc-write/quickstart.md
[atk-design]: docs/skills/repo-task-atk-test/design.md
[atk-quick]: docs/skills/repo-task-atk-test/quickstart.md

任务书是前者的产物、后者的唯一输入，两个 skill 因此同仓。

## 安装

### 取得本仓

```bash
git clone https://gitcode.com/Justbin/repo-task-atk-test.git
cd repo-task-atk-test
```

### 装 skill

三选一，`<name>` 换成 `repo-task-atk-test` 或 `repo-task-doc-write`，两个都要就各装一次。

```bash
# 软链接：跟着仓库更新，路径必须绝对
ln -s "$PWD/skill/<name>" ~/.claude/skills/<name>

# 用户目录：所有项目可用
cp -r skill/<name> ~/.claude/skills/

# 项目目录：只对该项目生效，可随项目提交
cp -r skill/<name> <你的项目>/.claude/skills/
```

`cp -r skill/repo-task-atk-test` 会一次装齐两个子流程，父 `SKILL.md` 按现有输入路由。
用 plugin 方式加载时，manifest 可以把父目录与两个子目录分别列进 `skills`。

`repo-task-doc-write` 到此装完，它只依赖 python3 标准库。

### repo-task-atk-test 还要装 ATK

生成用例只需 Python、ATK 与 CPU 版 torch，不需要 NPU 或 CANN。测试验收还要求机器有 NPU，
CANN 已装好且 `set_env.sh` 可以 source。

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
