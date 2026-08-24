---
name: isolated-acceptance
description: 在一个完全隔离的无头 Claude 会话里驱动一次算子验收——目标机上准备好输入与 plugin，然后用 --plugin-dir 加载指定的那份 plugin 跑完全程。当需要在真机上验收算子、试跑或回归本仓 plugin、或要验证 plugin 脱离本仓仓规后能否独立撑起流程时使用。本 skill 只做主机侧的编排与准备，验收流程本身由被加载的 plugin 的 skill 负责。
---

# 隔离会话驱动验收

把一次算子验收交给一个**完全隔离**的无头会话执行。隔离指两件事同时成立：加载的是指定的那份 plugin，
不是任何已安装的缓存副本；上下文里没有本仓仓规，因此测到的是 plugin 自身能不能撑起流程。

## 何时使用

要在真机上验收一个算子，或要试跑、回归本仓的 plugin 时使用。准备目标机环境、安装 ATK/CANN 不属于本
skill，前置不就绪时停下说明。

## 为什么必须隔离

在本仓内直接派 subagent 有两个已实测确认的问题：

- **plugin 其实没被加载。** `claude plugin list` 里本仓 plugin 处于 disabled，且注册版本落后于工作树。
  即便启用，加载的也是缓存里那份旧副本，不是刚改的这份。
- **仓规会被自动注入。** subagent 继承会话 cwd，`CLAUDE.md → @AGENTS.md` 随之进上下文。它拿到一份描述
  整个项目的规则却没有流程，只能反过来通读代码仓。
- **`--plugin-dir` 会泄露仓库路径。** 把参数指向仓内的 `plugin/`，等于把仓库地图交给会话——它可以
  `cd ..` 读到任何东西。实测中会话据此读了 `tests/witnesses/<算子>/design.yaml`（验收参考答案）、
  ignored 的私有机器配置，并在仓根 `ls` 了一遍。cwd 在仓外只挡住 `CLAUDE.md` 的自动加载，挡不住路径遍历。
  （`tests/` 已在后续改动中整体删除，那条具体路径不复存在；但路径遍历这一风险与结论不变。）

`--plugin-dir` 解决前两点。官方文档：*"When a `--plugin-dir` plugin has the same name as an installed
marketplace plugin, the local copy takes precedence for that session."* 第三点要靠**指向仓外的中性副本**
而不是仓内的 `plugin/`（见步骤 7a）。

**这样测不到的两件事**：description 驱动的 skill 发现（本流程把 skill 直接摆进会话，跳过了语义命中）；
`bin/`、hooks、MCP 一类的分发问题（本仓 plugin 目前都没有）。

## 流程

复制这份清单，逐项勾掉：

```
- [ ] 步骤 1  确定算子、任务书来源、被测源码来源
- [ ] 步骤 2  读私有机器配置
- [ ] 步骤 3  在目标机建本轮工作根
- [ ] 步骤 3a 打通目标机的网络出口
- [ ] 步骤 4  拷入输入并核验
- [ ] 步骤 5  部署 plugin 并双侧比对摘要
- [ ] 步骤 6  定位目标机上的 atk 可执行
- [ ] 步骤 7  建仓外隔离目录
- [ ] 步骤 8  选定物理卡，后台启动无头会话
- [ ] 步骤 9  盯日志，出问题即上报
- [ ] 步骤 10 收结果
```

## 步骤 1　确定输入

算子名；任务书（URL 或路径）；被测源码本地路径。调用方给出即断言二者对应，不再另行鉴权。

## 步骤 2　读私有机器配置

主机、容器、SoC、保护根等私有值只在 ignored 配置里，位置按仓规解析：

```bash
W=<worktree 绝对路径>
set -a; . "$(dirname "$(git -C "$W" rev-parse --git-common-dir)")/.oprunway/real-machine.env"; set +a
```

**这些值不得写进任何入库文件**，包括本 skill、dev-doc 与提示词模版本身。引用变量名，不写字面量。

## 步骤 3　建本轮工作根

在目标机新建一个本轮专用目录 `$ROOT`。它**不得落在 `OPRUNWAY_MACHINE_PROTECTED_ROOTS` 的任何条目
之下**，也不得复用既有目录。已存在就换名，不覆盖。

## 步骤 3a　打通目标机的网络出口

目标机没有直连外网，而 build 会拉第三方依赖——实测撞到 `git clone https://gitcode.com/cann/cmake.git`，
拉不到就卡在那里。本机 7897 是本地代理端口，用反向隧道送到目标机的 58231：

```bash
autossh -M 0 -N -R 58231:localhost:7897 \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes \
  "$OPRUNWAY_MACHINE_SSH_HOST" &
```

隧道可能已经在了，先查再建，不要重复起：`pgrep -f 'ssh.*-R 58231'`。

**建完必须验证**，容器内跑一次，拿到状态行才算通：

```bash
docker exec "$OPRUNWAY_MACHINE_CONTAINER" bash -lc \
  'curl -sI -x http://127.0.0.1:58231 https://gitcode.com | head -1'
```

隧道只是通路，不会自动生效——目标机上凡是要联网的命令都得显式带代理，容器内同样：

```bash
http_proxy=http://127.0.0.1:58231 https_proxy=http://127.0.0.1:58231 <命令>
```

所以这两个变量要写进步骤 8 的提示词环境项。验不通就停下说明，不要让会话自己去摸。

## 步骤 4　拷入输入并核验

任务书与被测源码都复制进 `$ROOT/inputs/`。来源可以是只读的保护根或内容寻址缓存——**读可以，写不行**。

复制后核验，不一致就停：

- 任务书 SHA-256 与调用方给定来源一致；
- 被测源码算子子目录的文件数与本地一致。

这一步的意义是让隔离会话全程接触不到保护根，安全边界不依赖它的自觉。

## 步骤 5　部署 plugin

先确认要发的是干净的已提交状态，脏树直接停：

```bash
git -C "$W" status --porcelain -- plugin     # 必须为空
git -C "$W" rev-parse --short HEAD           # 记下，作为本轮 plugin 版本
```

送过去，排除构建产物：

```bash
COPYFILE_DISABLE=1 tar czf - --exclude='__pycache__' --exclude='._*' -C "$W/plugin" .claude-plugin skill \
  | ssh "$OPRUNWAY_MACHINE_SSH_HOST" \
    "docker exec -i $OPRUNWAY_MACHINE_CONTAINER bash -lc 'mkdir -p $ROOT/plugin && tar xzf - -C $ROOT/plugin'"
```

两侧用同一算法算聚合摘要，**必须相同**，否则停：

```bash
# 本地
cd "$W/plugin" && find .claude-plugin skill -type f -not -path '*__pycache__*' | sort \
  | while read -r f; do printf '%s  %s\n' "$(shasum -a 256 "$f" | cut -d' ' -f1)" "$f"; done | shasum -a 256
# 目标机同理，find 同样收窄到 .claude-plugin skill，用 sha256sum
```

步骤 5 与步骤 7a 使用同一条摘要命令与同一组排除项，三处（本地、目标机、中性副本）可互相对照。

再确认远端结构完整：`ls $ROOT/plugin/.claude-plugin/plugin.json
$ROOT/plugin/skill/repo-task-case-gen/SKILL.md $ROOT/plugin/skill/repo-task-atk-accept/SKILL.md` 三个文件都在。
plugin 的判据脚本随各 skill 位于其 scripts/ 目录，没有可导入的入口，也不做导入自检。

开发件（`CLAUDE.md`、`README.md`、`docs/`）不随部署分发——隔离会话物理接触不到它们，测到的才是 skill 自身的零上下文自足性。

## 步骤 6　定位 atk

目标机上的 `atk` 未必在 `PATH` 上。找到可执行的绝对路径并记下版本。它通常由 venv 提供——**不要激活
venv**，直接用绝对路径；激活会把会话自己的 `python3` 也切走。

进容器一律用 `bash -lc`：CANN 环境由 login profile 加载，`sh` 加载不了，ATK 会在导入 torch 时崩。

## 步骤 7　建仓外隔离目录

```bash
ISO=/private/tmp/oprw-iso-<算子名小写>-<stamp>
mkdir -p "$ISO"
```

**每轮换新目录。** 复用会让上一轮残留进下一轮视野，与「干净工作目录」是同一条规矩。必须在仓外，否则
仓规仍会被加载，隔离失效。

## 步骤 7a　把 plugin 发布切片复制到中性目录

`--plugin-dir` 不能指向仓内路径，否则会话顺着它就能翻整个仓库。复制一份到仓外，用内容摘要命名：

```bash
D=$(cd "$W/plugin" && find .claude-plugin skill -type f -not -path '*__pycache__*' \
  | sort | while read -r f; do printf '%s  %s\n' "$(shasum -a 256 "$f" | cut -d' ' -f1)" "$f"; done \
  | shasum -a 256 | cut -c1-12)
PLUGIN=/private/tmp/oprw-plugin-$D
rm -rf "$PLUGIN"; mkdir -p "$PLUGIN"
COPYFILE_DISABLE=1 tar cf - --exclude='__pycache__' --exclude='._*' --exclude='.pytest_cache' \
  -C "$W/plugin" .claude-plugin skill | tar xf - -C "$PLUGIN"
```

按摘要命名有个副作用是好的：这份中性副本与步骤 5 发到目标机的那份用同一条发布切片摘要命令，
输入集合与算法完全一致，摘要可直接对照。开发件（`CLAUDE.md`、`README.md`、`docs/`）不进中性副本，
隔离会话顺着 `--plugin-dir` 也接触不到它们。

复制前先确认本地 `plugin/` 下没有 `.pytest_cache`、`__pycache__` 或编辑器临时文件——它们会混进摘要，
让两侧对不上。

## 步骤 8　启动无头会话

### 先选一张真空闲的卡

被加载的 skill 明确把选卡放在它之外，没拿到卡号会停在 `NEEDS_INPUT`。所以这一步由本 skill 选定并
在提示词里给出。

读**完整**的 `npu-smi info`——不是 `-t usages`，也不是只看头几张卡。逐卡看两样：健康项是否 OK，
以及底部进程表里这张卡有没有进程。**只有两样同时成立才算空闲。** 利用率 0% 不算证据：实测遇到过
一张卡挂着五个他人进程而 `Aicore Usage Rate` 为 0，据此选中会与人共卡，性能数据作废。

已有进程或异常的卡只能跳过，绝不 kill、reset 或抢占。没有任何卡同时满足健康与空闲时，逐张列出事实
向用户报告并停下，等用户指定；指定不构成强占授权。

```bash
cd "$ISO"
claude --plugin-dir "$PLUGIN" \
       --dangerously-skip-permissions \
       --output-format stream-json --verbose \
       -p "$(cat <<PROMPT
帮我验收这个算子：<算子名>
- 任务书：$ROOT/inputs/<任务书文件名>
- 被测源码：$ROOT/inputs/<源码目录名>

目标机器环境：
- SSH：ssh $OPRUNWAY_MACHINE_SSH_HOST
- 容器：${OPRUNWAY_MACHINE_CONTAINER}，进容器用 docker exec ${OPRUNWAY_MACHINE_CONTAINER} bash -lc "…"（CANN 环境由 login profile 加载）
- SoC：$OPRUNWAY_MACHINE_SOC
- ATK：<步骤 6 得到的绝对路径>
- 物理卡：<上面选定的卡号，会话必须用它，不要自己另选>
- 联网：目标机无直连外网。需要联网的命令（例如 build 拉第三方依赖）前面加 http_proxy=http://127.0.0.1:58231 https_proxy=http://127.0.0.1:58231
- plugin：$ROOT/plugin
- 执行目录：${ROOT}，在其下新建本轮 session

如果遇到问题请及时反馈。
PROMPT
)" > "$ISO/run.jsonl" 2> "$ISO/run.err"
```

**必须后台运行**：单次验收主动预算上限 7200 秒，远超前台命令的超时。

变量一律写 `${VAR}`。变量名后面紧跟中文全角标点时，不加花括号会被 shell 连标点一起吞掉，整个变量不展开。

### 提示词只给什么

只给**算子名 + 两个已拷出的目标机路径 + 目标机环境**。不给任何 how：不提 plugin 的 skill 在哪、不复述
仓规、不规定 spec 从哪来、不规定汇报格式。这些要么在被加载的 skill 里，要么就该由它自己判断——判不
出来正是要暴露的。

环境项里 SoC、物理卡号、代理变量与「CANN 由 login profile 加载」这半句，是执行必需而它无法自行发现的：
目标 SoC 是正式入口的必填项；被加载的 skill 把选卡放在它之外、拿不到卡号就停；不给代理它会在 build 拉
第三方依赖时卡住；用 `sh` 进容器加载不了环境。

## 步骤 9　盯日志

`-p` 默认只在结束时输出；步骤 8 加了 `--output-format stream-json --verbose`，`$ISO/run.jsonl` 因此是
边跑边写的，可以实时看。会话报出任何非裁决状态或卡住，**原样转达给用户，不代它决定如何绕过**。

原始 JSON 读不出进度，用同目录的观察脚本：

```bash
python3 "$(dirname "$0")/watch.py" "$ISO/run.jsonl"           # 跟随
python3 "$(dirname "$0")/watch.py" "$ISO/run.jsonl" --brief   # 只看人话
python3 "$(dirname "$0")/watch.py" "$ISO/run.jsonl" --full    # 结果不截断
```

它把事件流归成步骤条加人话加真实命令与结果：走到第几步（锚定输入 / 冻结 spec 与 design / 编译安装与
装载身份 / 生成 case / 精度 / 性能 / 证据闭合）、说了什么、跑了哪些命令、哪些是真失败。只读，
Ctrl-C 不影响会话继续跑。

两点判读注意。其一，事件流不带时间戳，所以已经写在文件里的历史事件不标时间，只有跟随期间新到的才标，
总时长按文件创建时刻算。其二，会话摸索目标机环境时会大量出现路径不存在、grep 无命中一类的非零退出，
那些是探测不是故障，不要按故障上报。

**会话结束不等于验收结束。** 无头会话可能在正式 CLI 还在跑时就结束——它一旦不再发出工具调用就会退出，
而通过 SSH 在目标机上启动的 CLI 不是它的子进程，会继续跑完。实测遇到过。所以看到会话结束后，必须复核
两件事再下结论：目标机上正式 CLI 的进程是否已退出、终态文件是否已生成。两者未同时成立就还没结束，
继续等，不要按会话的最后一段话汇报。

## 步骤 10　收结果

至少取回：正式终态的逐字原文；session 绝对路径；所选物理卡（作为环境调度事实单列，不与
终态混写）；生成的 spec 与 design 摘要；用例通过数与完整分母；分阶段耗时。

## 已知代价

拷贝输入、部署 plugin、定位 atk 这三步由本 skill 承担，因此**移出了被测范围**——它们本属于被加载 skill
的 preflight。

`--dangerously-skip-permissions` 之后没有人工确认关卡：会话会 SSH 进真机、跑 build、动 NPU。护栏只剩
提示词与被加载 skill 自身的规则，性质从「工具层拦截」降为「模型自觉」。真机上他人的容器与进程同样依赖
这层自觉。用此模式前先确认目标机当前没有他人正在跑的任务。
