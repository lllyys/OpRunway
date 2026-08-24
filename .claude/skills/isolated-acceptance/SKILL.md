---
name: isolated-acceptance
description: 当需要在真机上按“仅任务书生成交接包 → 交接包加源码完成验收”启动两个先后且互相隔离的无头 Claude 会话、试跑或回归本仓 plugin、验证两个平级 skill 能否按输入形态自动路由、或验证发布切片脱离仓规后能否独立工作时使用。本 skill 只做主机侧准备、双会话编排与交接留档，生成和验收流程由被加载 plugin 的两个平级 skill 各自负责。
---

# 隔离双会话驱动验收

把一次算子验收拆给两个**先后、独立、完全隔离**的无头会话：生成会话只拿任务书，产出并封印交接包；
验收会话拿交接包、被测源码与任务书，完成接收、构建、精度、性能和裁决。两侧唯一接口是前一会话工作目录
里的 `evidence/bundle.json`。

隔离同时保证：加载的是指定的那份 plugin，不是已安装的缓存副本；上下文里没有本仓仓规；两个会话不共用
本机 cwd 或日志。测到的是发布切片自身能否按输入形态路由并撑起两段流程。

## 何时使用

要在真机上验收一个算子、试跑或回归本仓 plugin，或验证两个平级 skill 的 description 路由时使用。
准备目标机环境、安装 ATK/CANN 不属于本 skill；前置不就绪时停下说明。

## 为什么必须隔离

在本仓内直接派 subagent 有三个已实测确认的问题：

- **plugin 其实没被加载。** `claude plugin list` 里本仓 plugin 处于 disabled，且注册版本可能落后于工作树。
  即便启用，加载的也可能是缓存里的旧副本，不是刚改的这份。
- **仓规会被自动注入。** subagent 继承会话 cwd，`CLAUDE.md → @AGENTS.md` 随之进上下文。它拿到一份描述
  整个项目的规则却没有完整流程，只能反过来通读代码仓。
- **`--plugin-dir` 会泄露仓库路径。** 把参数指向仓内的 `plugin/`，等于把仓库地图交给会话；cwd 在仓外
  只能挡住仓规自动加载，挡不住沿 plugin 路径遍历。必须给两个会话加载同一份仓外中性副本。

`--plugin-dir` 解决前两点；仓外中性副本解决第三点；两个独立隔离目录再切断两段会话的本地残留。生成会话
只收到任务书路径，验收会话才收到源码路径。这是输入归属红线的运行形态，不是遗漏。

**这样测得到与测不到的事**：现在测得到 description 驱动的双 skill 路由——提示词不点名 skill，只靠
“只有任务书”与“交接包 + 源码 + 任务书”两种输入形态命中对应入口；仍测不到 `bin/`、hooks、MCP 一类
分发问题（本仓 plugin 目前都没有）。

## 流程

复制这份清单，逐项勾掉：

```text
- [ ] 步骤 1  确定算子与分侧输入
- [ ] 步骤 2  读私有机器配置
- [ ] 步骤 3  在目标机建本轮工作根
- [ ] 步骤 3a 为验收会话打通目标机网络出口
- [ ] 步骤 4  分目录拷入输入并核验
- [ ] 步骤 5  部署 plugin 并双侧比对摘要
- [ ] 步骤 6  定位目标机上的 atk 可执行
- [ ] 步骤 7  建两个仓外隔离目录
- [ ] 步骤 7a 把 plugin 发布切片复制到同一中性目录
- [ ] 步骤 8A 后台启动生成会话
- [ ] 步骤 8x 主机核验并留档交接包
- [ ] 步骤 8B 选定物理卡，后台启动验收会话
- [ ] 步骤 9  分别盯两份日志，出问题即上报
- [ ] 步骤 10 收结果
```

## 步骤 1　确定分侧输入

确定算子名、任务书来源（URL 或路径）与被测源码本地路径。调用方给出即断言任务书与源码对应，不再另行
鉴权。输入归属固定如下：

- 任务书属于生成会话，也是验收会话复核契约时的输入；
- 被测源码只属于验收会话；
- 生成会话的提示词不得出现源码路径。它拿不到源码路径是红线 1 的设计结果，不是编排疏漏。

## 步骤 2　读私有机器配置

主机、容器、SoC、保护根等私有值只在 ignored 配置里，位置按仓规解析：

```bash
W=<worktree 绝对路径>
set -a; . "$(dirname "$(git -C "$W" rev-parse --git-common-dir)")/.oprunway/real-machine.env"; set +a
```

**这些值不得写进任何入库文件**，包括本 skill、dev-doc 与提示词模版本身。只引用
`OPRUNWAY_MACHINE_*` 变量名，不写主机、容器、SoC、保护根或输入来源的字面量。

## 步骤 3　建本轮工作根

在目标机新建一个本轮专用目录 `$ROOT`。它**不得落在 `OPRUNWAY_MACHINE_PROTECTED_ROOTS` 的任何条目
之下**，也不得复用既有目录。已存在就换名，不覆盖。

## 步骤 3a　只为验收会话打通网络出口

生成侧不 build、不联网，因此不接收 SoC、卡号或代理。验收侧 build 可能拉第三方依赖，目标机没有直连
外网时，先按机器现场建立反向隧道。端口值是本轮运行参数，不写进本文件：

```bash
autossh -M 0 -N -R "<远端代理端口>:localhost:<本机代理端口>" \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes \
  "$OPRUNWAY_MACHINE_SSH_HOST" &
```

隧道可能已经存在，先查再建，不要重复起。建完必须从容器内发一次外部请求，拿到状态行才算通；验不通就
停下，不让会话自己摸索。把容器内验证过的两个完整代理 URL 分别存入运行时变量 `http_proxy` 与
`https_proxy`，只传给步骤 8B。隧道只是通路，联网命令仍须显式携带这两个变量。

## 步骤 4　分目录拷入输入并核验

在 `$ROOT/inputs/` 下建立两个物理分区：

- 任务书复制到 `$ROOT/inputs/taskdoc/`；
- 被测源码复制到 `$ROOT/inputs/src/`。

来源可以是只读保护根或内容寻址缓存——**读可以，写不行**。复制后核验，不一致就停：

- 任务书 SHA-256 与调用方给定来源一致；
- 被测源码算子子目录的文件数与本地一致。

分目录是输入归属的物理落点。步骤 8A 只给出 `taskdoc/` 下的路径，不给 `src/` 路径；步骤 8B 才给出
`src/` 下的目录。隔离会话全程不接触保护根，安全边界不依赖模型自觉。

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
  | while read -r f; do printf '%s  %s\n' "$(shasum -a 256 "$f" | cut -d' ' -f1)" "$f"; done \
  | shasum -a 256
# 目标机同理，find 同样收窄到 .claude-plugin skill，用 sha256sum
```

步骤 5 与步骤 7a 使用同一条摘要命令与同一组排除项，三处（本地、目标机、中性副本）可互相对照。

再确认远端结构完整：`ls $ROOT/plugin/.claude-plugin/plugin.json
$ROOT/plugin/skill/repo-task-case-gen/SKILL.md $ROOT/plugin/skill/repo-task-atk-accept/SKILL.md` 三个文件都在。
plugin 的判据脚本随各 skill 位于其 `scripts/` 目录，没有可导入的入口，也不做导入自检。

开发件（`CLAUDE.md`、`README.md`、`docs/`）不随部署分发。隔离会话物理接触不到它们，测到的才是
skill 自身的零上下文自足性。

## 步骤 6　定位 atk

目标机上的 `atk` 未必在 `PATH` 上。找到可执行的绝对路径并记下版本。它通常由 venv 提供——**不要激活
venv**，直接用绝对路径；激活会把会话自己的 `python3` 也切走。

进容器一律用 `bash -lc`：CANN 环境由 login profile 加载，`sh` 加载不了，ATK 会在导入 torch 时崩。

## 步骤 7　建两个仓外隔离目录

```bash
: "${TMPDIR:?TMPDIR 未设置}"
ISO_GEN=$(mktemp -d "${TMPDIR%/}/oprw-iso-<算子名小写>-gen.XXXXXX")
ISO_ACCEPT=$(mktemp -d "${TMPDIR%/}/oprw-iso-<算子名小写>-accept.XXXXXX")
```

两侧各用独立新目录：生成日志只进 `$ISO_GEN`，验收日志只进 `$ISO_ACCEPT`。不得复用，也不得建在仓内，
否则上一轮残留或仓规会进入会话视野，隔离失效。

## 步骤 7a　把 plugin 发布切片复制到同一中性目录

`--plugin-dir` 不能指向仓内路径，否则会话能顺着它翻整个仓库。复制一份到仓外，用内容摘要标识：

```bash
D=$(cd "$W/plugin" && find .claude-plugin skill -type f -not -path '*__pycache__*' \
  | sort | while read -r f; do printf '%s  %s\n' "$(shasum -a 256 "$f" | cut -d' ' -f1)" "$f"; done \
  | shasum -a 256 | cut -c1-12)
PLUGIN=$(mktemp -d "${TMPDIR%/}/oprw-plugin-$D.XXXXXX")
COPYFILE_DISABLE=1 tar cf - --exclude='__pycache__' --exclude='._*' --exclude='.pytest_cache' \
  -C "$W/plugin" .claude-plugin skill | tar xf - -C "$PLUGIN"
```

两个会话的 `--plugin-dir` 都指向这一个 `$PLUGIN`，不得各做一份。开发件不进中性副本。复制前确认本地
`plugin/` 下没有 `.pytest_cache`、`__pycache__` 或编辑器临时文件；再用步骤 5 的算法核摘要。

## 步骤 8A　后台启动生成会话

生成会话不选卡、不需要 SoC、不需要代理。提示词只给算子名、任务书路径、SSH/容器/`bash -lc` 约束、
ATK 绝对路径与执行根；尤其不给源码路径。

```bash
(
  cd "$ISO_GEN"
  claude --plugin-dir "$PLUGIN" \
         --dangerously-skip-permissions \
         --output-format stream-json --verbose \
         -p "$(cat <<PROMPT
请为这个算子准备验收输入：<算子名>
- 任务书：${ROOT}/inputs/taskdoc/<任务书文件名>

目标机器环境：
- SSH：ssh ${OPRUNWAY_MACHINE_SSH_HOST}
- 容器：${OPRUNWAY_MACHINE_CONTAINER}，进容器用 docker exec ${OPRUNWAY_MACHINE_CONTAINER} bash -lc "…"（CANN 环境由 login profile 加载）
- ATK：<步骤 6 得到的绝对路径>
- 执行目录：${ROOT}，在其下新建本轮工作目录

如果遇到问题请及时反馈。
PROMPT
)" > "$ISO_GEN/run.jsonl" 2> "$ISO_GEN/run.err"
) &
GEN_CLAUDE_PID=$!
```

必须后台运行并记录 PID。生成侧结束判据不是“模型说做完了”，而是会话已退出、它返回的工作目录已明确，
且步骤 8x 在该目录确认 `evidence/bundle.json` 存在。任一条件缺失就停，不启动验收会话。

## 步骤 8x　主机核验并留档交接

两会话之间，主机只做两件事：

1. 断言 `<生成工作目录>/evidence/bundle.json` 存在；
2. 记录生成工作目录绝对路径与该文件的 SHA-256 到 `$ISO_GEN/handoff.txt` 留档。

```bash
GEN_WORKDIR=<步骤 8A 返回的工作目录绝对路径>
HANDOFF_RESULT=$(
  ssh "$OPRUNWAY_MACHINE_SSH_HOST" \
    "docker exec $OPRUNWAY_MACHINE_CONTAINER bash -lc 'test -f \"$GEN_WORKDIR/evidence/bundle.json\" && sha256sum \"$GEN_WORKDIR/evidence/bundle.json\"'"
) || exit 1
BUNDLE_SHA256=${HANDOFF_RESULT%% *}
printf 'workdir=%s\nbundle_sha256=%s\n' "$GEN_WORKDIR" "$BUNDLE_SHA256" > "$ISO_GEN/handoff.txt"
```

`check_bundle.py` 的接收核验属于验收会话，主机**不得代跑、不得展开包内容、不得预判接收结果**。断言或
摘要记录失败就停。把 8A 返回并经断言的目录记为运行时变量 `GEN_WORKDIR`，步骤 8B 原样传入。

## 步骤 8B　选卡并后台启动验收会话

### 先选一张真空闲的卡

被加载的 skill 明确把选卡放在它之外，没拿到卡号会停在 `NEEDS_INPUT`。所以这一步由本 skill 选定并
在提示词里给出。

读**完整**的 `npu-smi info`——不是 `-t usages`，也不是只看头几张卡。逐卡看两样：健康项是否 OK，
以及底部进程表里这张卡有没有进程。**只有两样同时成立才算空闲。** 利用率 0% 不算证据：实测遇到过
一张卡挂着五个他人进程而 `Aicore Usage Rate` 为 0，据此选中会与人共卡，性能数据作废。

已有进程或异常的卡只能跳过，绝不 kill、reset 或抢占。没有任何卡同时满足健康与空闲时，逐张列出事实
向用户报告并停下，等用户指定；指定不构成强占授权。

验收提示词给交接包目录、源码目录、任务书路径和执行所需环境；不点名 skill，不复述流程：

```bash
(
  cd "$ISO_ACCEPT"
  claude --plugin-dir "$PLUGIN" \
         --dangerously-skip-permissions \
         --output-format stream-json --verbose \
         -p "$(cat <<PROMPT
帮我验收这个算子：<算子名>
- 交接包目录：${GEN_WORKDIR}
- 被测源码：${ROOT}/inputs/src/<源码目录名>
- 任务书：${ROOT}/inputs/taskdoc/<任务书文件名>

目标机器环境：
- SSH：ssh ${OPRUNWAY_MACHINE_SSH_HOST}
- 容器：${OPRUNWAY_MACHINE_CONTAINER}，进容器用 docker exec ${OPRUNWAY_MACHINE_CONTAINER} bash -lc "…"（CANN 环境由 login profile 加载）
- SoC：${OPRUNWAY_MACHINE_SOC}
- ATK：<步骤 6 得到的绝对路径>
- 物理卡：<上面选定的卡号，会话必须用它，不要自己另选>
- 联网：目标机无直连外网；需要联网的命令显式带 http_proxy=${http_proxy} https_proxy=${https_proxy}

如果遇到问题请及时反馈。
PROMPT
)" > "$ISO_ACCEPT/run.jsonl" 2> "$ISO_ACCEPT/run.err"
) &
ACCEPT_CLAUDE_PID=$!
```

两个会话都必须后台运行：单次验收主动预算上限远超普通前台命令超时。变量一律写 `${VAR}`；变量名后面
紧跟中文全角标点时，不加花括号可能导致 shell 把变量名解析错。

### 提示词只给 what

两个提示词都只给 **what**，不给 **how**，且都不点名要调用哪个 skill。生成提示词只有任务书输入；验收
提示词有交接包、源码与任务书。两个平级 skill 的 description 已按这两种输入形态互相指路，会话实际命中
哪一侧本身就是被测行为。命中错侧时原样上报，不代它指定 skill 绕过。

生成侧只增加它无法自行发现的 SSH、容器入口、`bash -lc` 约束、ATK 绝对路径与执行根。验收侧再增加
SoC、物理卡号和两个代理变量：目标 SoC 是正式入口必填项；选卡在被加载 skill 之外；代理是 build 联网
前置。这些都是执行环境事实，不是流程提示。

## 步骤 9　分别盯两份日志

`-p` 默认只在结束时输出；两个命令都使用 `--output-format stream-json --verbose`，两份 `run.jsonl` 会边跑
边写。会话报出任何非裁决状态或卡住，**原样转达给用户，不代它决定如何绕过**。

用同目录的只读观察脚本：

```bash
python3 "$(dirname "$0")/watch.py" "$ISO_GEN/run.jsonl" --side gen
python3 "$(dirname "$0")/watch.py" "$ISO_ACCEPT/run.jsonl" --side accept
python3 "$(dirname "$0")/watch.py" <任一日志> --brief
python3 "$(dirname "$0")/watch.py" <任一日志> --full
```

省略 `--side` 时，观察器按事件流里先出现的 `seal_bundle.py` 或 `check_bundle.py` 自动判侧。生成梯子是
“任务书解读与环境 → 必测集 → 物化与 YAML → 冻结 → 封印”；验收梯子是
“接收与环境 → 构建部署 → 精度 → 性能 → 裁决与报告”。观察器只读日志，Ctrl-C 只停止观察，不影响后台
会话继续运行。

事件流不带时间戳，所以已经写在文件里的历史事件不标时间，只有跟随期间新到的才标；总时长按文件创建
时刻算。会话摸索目标机环境时会出现路径不存在、grep 无命中一类非零退出，那些是探测，不自动视作故障。

**生成会话退出不等于交接完成。** 必须经步骤 8x 同时拿到工作目录、存在的 `evidence/bundle.json` 与其
SHA-256，才能启动 8B。

**验收会话结束不等于验收结束。** 无头会话可能在正式 CLI 还在跑时就结束——它一旦不再发出工具调用就会退出，
而通过 SSH 在目标机上启动的 CLI 不是它的子进程，会继续跑完。实测遇到过。所以看到会话结束后，必须复核
两件事再下结论：目标机上正式 CLI 的进程是否已退出、终态文件是否已生成。两者未同时成立就还没结束，
继续等，不要按会话的最后一段话汇报。

## 步骤 10　收结果

至少取回：8A 工作目录与 `evidence/bundle.json` SHA-256；8B 正式终态逐字原文与 session 绝对路径；所选
物理卡（作为环境调度事实单列，不与终态混写）；生成侧必测集与 YAML 摘要；用例通过数与完整分母；分阶段
耗时。局部证据或单阶段跑通不得描述成正式验收通过。

## 已知代价

拷贝输入、部署 plugin、定位 atk 由本 skill 承担，因此移出了两个被加载 skill 的 preflight 被测范围。
拆成两次无头启动还增加了隔离目录、日志与交接留档成本，但换来了输入边界和交接契约的独立可观测性。

`--dangerously-skip-permissions` 对 **8A 与 8B 两个会话都成立**：两边都没有人工确认关卡。生成会话虽不
build、不联网、不碰 NPU，仍会 SSH 并在目标机写本轮目录；验收会话还会 build、安装并使用 NPU。护栏只剩
提示词与被加载 skill 自身规则，性质从“工具层拦截”降为“模型自觉”。启动任一会话前都要确认目标机现场
适合本轮操作；真机上已有容器、文件与进程不得擅自修改、删除、reset 或抢占。
