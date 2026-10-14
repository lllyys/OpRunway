---
name: isolated-acceptance
description: 在仓外的独立 Codex 会话中验证指定版本的算子验收 skill。用于正式验收、真机回归及零开发上下文验证；不负责安装 CANN 或修改算子判据。
---

# 隔离验收

使用 Codex 驱动指定发布副本。验收依据仅为被选 skill 的 SKILL.md、references 和 scripts；构建、输入生成及 NPU 执行均在已授权的远程目标环境进行。

## 前置条件

- 已取得算子名、任务书、被测源码和本轮构建与执行授权。
- 已读本项目 ignored `.oprunway/real-machine.env`，确认保护根。保护根只读；输入复制到新的本轮远程目录后使用。
- 已确认 CANN、所需 ATK/Python 依赖、实际 SoC、可用容量与网络出口。不把私有地址或凭据写入仓库。
- 已核对完整 `npu-smi info`：设备健康且无他人进程。利用率为零不代表空闲；不抢占、重置或终止他人进程。
- Codex CLI 支持 `exec --json --ephemeral --skip-git-repo-check --ignore-user-config`。缺选项时先核对安装版本，不替换为 Claude。

## 发布与隔离

1. 检查待部署的 `plugin/` 已提交且无未提交改动，记录提交号。
2. 将 `plugin/skill/` 与 `plugin/.claude-plugin/` 复制到本轮远程工作目录及仓外中性目录，排除缓存和构建产物。逐文件比对摘要，确保两份副本与本地发布切片一致。
3. 不复制仓根或 plugin 根的 AGENTS.md、CLAUDE.md、docs、历史报告、测试答案和机器配置。保留 skill 自带资源及判据。
4. 在仓外新建会话目录 `ISO`，将发布副本放在 `ISO/payload/`。在 `ISO/.agents/skills/` 中为各 skill 建相对符号链接，指向 `../../payload/skill/<name>`。链接不得指回开发仓。
5. 为本轮创建独立 `CODEX_HOME`，通过 Codex 官方登录方式准备认证。不要复制全局 config、skills、hooks 或插件缓存，不把凭据放入提示词、日志或报告。
6. 检查 `ISO` 的祖先目录没有项目 AGENTS.md、.agents/skills 或 .codex 配置。独立用户目录仍可能受系统或受管配置影响；检查启动时实际加载的指令、工具和 skills，发现开发上下文污染则停止并报告。

仓外目录和独立配置目录减少上下文污染，不等于操作系统访问隔离。需要严格禁止访问开发仓时，使用远程独立容器或账户，并仅暴露本轮输入与发布副本。

## 启动

先将提示词写入 `ISO/request.txt`。内容只含算子名、任务书与源码的远程副本路径、目标环境、设备、发布副本位置和授权范围，不复制验收步骤或预期答案。

在已配置好独立认证的环境中启动；下列变量由本轮实际目录与运行策略提供：

```bash
CODEX_HOME="$ISOLATED_CODEX_HOME" codex exec \
  --cd "$ISO" --skip-git-repo-check --ephemeral --ignore-user-config \
  --sandbox "$SANDBOX_MODE" --json \
  --output-last-message "$ISO/final.txt" - \
  < "$ISO/request.txt" > "$ISO/run.jsonl" 2> "$ISO/run.err" &
RUN_PID=$!
```

先确认沙箱策略允许本轮已授权的远程操作。使用外部容器隔离时可按实际授权配置权限；不为通过启动而自动关闭安全控制。启动后记录 PID、目录、Codex 版本、skill 发布摘要与设备号。

不使用 Claude 的 `--plugin-dir` 或 `--output-format stream-json`。本项目旧 watch.py 解析的是 Claude 事件，不能用于 Codex JSONL。

## 观察与结果

直接观察 `run.jsonl`、`run.err` 以及远程阶段产物；Codex 事件结构按本机版本核对，不把未知事件当成完成或成功。会话退出后，继续检查远程执行进程是否退出、确定性脚本是否生成终态报告。

至少收集：发布身份、所选设备、阶段耗时、完整用例分母与通过数、原始终态报告、失败证据和现场路径。判定以 skill 的机械门为准，Codex 的最后一段话不替代判定文件。

缺环境、缺权限、上下文污染或工具失败时保留现场并报告缺口；不要修改输入或阈值来补出通过结论。此入口的静态迁移不等于完成一次正式真机验收。
