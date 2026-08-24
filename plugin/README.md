# 社区算子任务 Skill 仓

三个供 AI 编程助手使用的平级 skill，覆盖任务书撰写、用例生成与 NPU 验收。

| skill | 做什么 | 需要 NPU | 详细 |
| --- | --- | --- | --- |
| `repo-task-doc-write` | 开发前写任务书 | 否 | [设计][doc-design] |
| `repo-task-case-gen` | 按任务书生成并封印用例 | 否 | [设计][case-design] |
| `repo-task-atk-accept` | 接收交接包，验收精度与性能 | 是 | [设计][accept-design] |

[doc-design]: docs/skills/repo-task-doc-write/design.md
[case-design]: docs/skills/repo-task-case-gen/design.md
[accept-design]: docs/skills/repo-task-atk-accept/design.md

任务书是生成侧的输入，封印交接包是生成侧与验收侧的唯一接口。

## 安装

先取得本仓：

```bash
git clone https://gitcode.com/Justbin/repo-task-atk-test.git
cd repo-task-atk-test
```

任务书 skill 可直接复制；生成与验收两个目录也直接复制或软链：

```bash
cp -R skill/repo-task-doc-write ~/.claude/skills/
cp -R skill/repo-task-{case-gen,atk-accept} ~/.claude/skills/
# 开发时可改用 ln -s "$PWD"/skill/repo-task-{case-gen,atk-accept} ~/.claude/skills/
```

Plugin 安装由 `.claude-plugin/plugin.json` 直接注册这三项，无额外构建步骤。

### 安装锁定的 ATK

生成侧需要 Python、ATK 与 CPU 版 torch；验收侧还需要 NPU 与可加载的 CANN 环境。

```bash
git submodule update --init
cd third_party/ATK
pip install wheel
python setup.py bdist_wheel
pip install dist/*.whl
cd ../..
```

**不要执行 `pip install atk`**，也不要换用其他版本。安装后确认以下命令都正常返回：

```bash
pip show atk
atk case --help
```

只有任务书时从 [生成侧上手][case-quickstart] 开始；已有封印交接包、工程与 NPU 时，
直接看 [验收侧上手][accept-quickstart]。

[case-quickstart]: docs/skills/repo-task-case-gen/quickstart.md
[accept-quickstart]: docs/skills/repo-task-atk-accept/quickstart.md

## 改 skill 本身

先读 [CLAUDE.md](CLAUDE.md)，再读目标目录自己的 `CLAUDE.md`。开发原则详解在
[docs/development/](docs/development/skill-development-principles.md)。
