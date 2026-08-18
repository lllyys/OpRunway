# 社区算子验收工作流

一套供 AI 编程助手使用的算子验收流程。

助手依照该流程独立完成一个社区算子的测试，
并给出精度与性能是否达标的结论。

底层测试能力由 ATK 提供，本仓提供的是驱动 ATK 的验收方法与检查规则。

## 目录

- [你属于哪一种](#你属于哪一种)
- [仓库构成](#仓库构成)
- [安装](#安装)
- [验收流程](#验收流程)
- [四条设计约束](#四条设计约束)

## 你属于哪一种

| | 使用者 | 开发者 |
|---|---|---|
| 目的 | 用这套流程验收算子 | 修改或扩展流程本身 |
| 接着读 | [快速上手](docs/QUICKSTART.md) | [项目约定](CLAUDE.md)、[开发准则](docs/development/skill-development-principles.md) |

## 仓库构成

- **验收流程本体**（`skill/repo-task-atk-test/`）唯一的发布物，拷到助手的技能目录即可使用
- **第三方依赖**（`third_party/ATK/`）算子测试工具 ATK 的源码，以 git 子模块引入，只读不改
- **开发文档**（`docs/`）流程的设计规则与演进记录，不随流程本体发布
- **项目约定**（`CLAUDE.md`）AI 助手进入本仓时自动加载的工作规则

## 安装

### 第一步 取得本仓

连同子模块一起克隆，子模块里就是 ATK 源码。

```bash
git clone --recursive https://gitcode.com/Justbin/repo-task-atk-test.git
cd repo-task-atk-test
```

漏掉 `--recursive` 会得到一个空的 `third_party/ATK/`，补救方式：

```bash
git submodule update --init
```

### 第二步 装 ATK

ATK 未发布到软件包索引，用上一步拿到的源码构建后安装。

**不要执行 `pip install atk`。**
索引上有一个同名的无关软件包，会装错。

```bash
cd third_party/ATK
pip install wheel
python setup.py bdist_wheel
pip install dist/*.whl
cd ../..
```

子模块锁定的是本流程验证过的 ATK 版本，不要换成其他版本自行安装。

确认装好了，下面两条都要正常返回：

```bash
pip show atk
atk case --help
```

包能被导入并不代表命令可用。

真机上出现过缺依赖导致命令报错的情况，任务在 0 秒内结束且产物为空。

ATK 建议装在 conda 环境或容器里，它还要求机器已装好 CANN。

### 第三步 装验收流程

三选一，装到助手能找到的位置。

**软链接**，跟着仓库一起更新，适合会经常拉取更新的人：

```bash
ln -s "$PWD/skill/repo-task-atk-test" ~/.claude/skills/repo-task-atk-test
```

链接必须用绝对路径。

**装到用户目录**，所有项目都能用：

```bash
cp -r skill/repo-task-atk-test ~/.claude/skills/
```

**装到项目目录**，只对该项目生效，可以随项目一起提交：

```bash
cp -r skill/repo-task-atk-test <你的项目>/.claude/skills/
```

## 验收流程

验收分五个阶段，每阶段有固定的出口检查，不通过不进入下一阶段。

- **S1 任务书解读** 把任务书变成可核验的约束，并记录本机环境
- **S2 用例生成** 产出测试配置与用例，本阶段结束时冻结输入
- **S3 编译安装部署** 构建并安装待验收算子，直到一条冒烟测试跑通
- **S4 精度性能测试** 跑全量测试，裁决精度，记录性能
- **S5 输出测试结果** 给出报告、结论与可复现的验收包

## 四条设计约束

这四条决定了流程的形状，修改流程时不可违背。

- **不拿算子实现当验收依据** 待验收的算子是被检验的对象，用它推导测试配置构成循环论证
- **ATK 只读** 行为不符合预期时记录为已知问题，不修改其源码
- **冻结后输入不可变** S2 锁定校验和之后，输入数据的任何改动都必须走受控通道
- **助手的声明不作数** 判断标准一律从数据推导，助手声称已检查不构成放行依据
