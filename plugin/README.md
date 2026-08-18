# OpRunway plugin

一个 ATK 驱动的昇腾 NPU 算子验收入口。Plugin 不安装 ATK/CANN，也不提供 GPU workflow；它只校验已准备
环境，然后在全新 session 中完成来源绑定、ATK 用例生成、fresh build、ATK 执行和确定性裁决。默认使用
目标环境的公开 `atk` 命令，不要求或探测 venv；`atk` 不在 `PATH` 上或存在多个版本时，须把选定可执行文件的
绝对路径作为本轮工具事实交给 skill。

当前能力边界是 `atk_aclnn + cann_ops_package_v1`：它在该 runner form 与仓库 build profile 内按算子数据
泛化，不承诺接入任意仓形态。第二种真实仓形态出现后应新增独立 build profile adapter，不能在现有 profile
里堆仓名或路径分支。

## 作为 Claude Code plugin 加载

插件清单在 `plugin/.claude-plugin/plugin.json`，插件名 `oprunway`。

**临时加载 —— 只对当前这一次 session 生效**，不写任何配置、不改全局状态，试用时优先用这种方式：

```bash
claude --plugin-dir /path/to/OpRunway/plugin
```

`--plugin-dir` 可重复传入多个，参数也可以是一个 `.zip`。插件包放在网上时改用 `--plugin-url <url>` 拉取 zip，
同样只对本次 session 生效。

**从 marketplace 安装 —— 持久生效。** 仓库根的 `.claude-plugin/marketplace.json` 把本仓注册为名为 `oprunway`
的 marketplace：

```bash
claude plugin marketplace add /path/to/OpRunway
claude plugin install oprunway@oprunway --scope project
```

`--scope project` 只在当前项目启用，启用状态写进该项目的 `.claude/settings.json`；`--scope user` 则对所有项目
启用。插件文件本身始终共享存放在 `~/.claude/plugins/cache/`，scope 只决定在哪里启用，不影响文件位置。
`claude plugin list` 列出的是全局注册表，不按当前目录过滤。

加载后插件只暴露一个入口：

| 类型 | 名称 | 用途 |
|---|---|---|
| skill | `/oprunway:acceptance-workflow` | 唯一编排层，验收一对任务书与算子源码：在全新 session 内执行 ATK 用例生成、fresh build、NPU 测试与确定性裁决 |

加载插件**不会**安装 ATK、CANN 或任何 Python 依赖，也不修改系统 Python、shell rc 或共享 CANN 安装——那些属于
目标环境的前置准备，插件只做版本与路径 preflight。真正的验收执行仍发生在 NPU 目标环境，用法见下节。

## 通过唯一 skill 执行

本仓没有命令行入口。唯一入口是 skill `/oprunway:acceptance-workflow`：把任务书与配对源码交给它，
它在 NPU 目标环境的全新 session 内完成来源绑定、ATK 用例生成、fresh build、精度与性能取证，
并按其中的判据产出终态与可离线核验的交付包。

Spec 声明 `task.case_bundle` 时，须把官方 cases/prototype/golden 作为本轮输入交给 skill；它们会完整复制、
逐文件验 hash，并作为完整 accuracy 分母。复杂 ABI 所需的 generator 或 execution plugin 同样是 session
输入，会被复制和哈希绑定，不是平行 runner。正式产物位于 `<session>/receipts/` 与 `<session>/reports/`。

物理 NPU 的发现与调度是 agent/目标环境的操作协议，不是 acceptance core。启动前，agent 读取当前目标的
完整 `npu-smi info`，依据健康项和进程事实选择实际空闲卡，并在紧邻启动前再复核一次。已有进程或异常的卡
只能跳过，绝不 kill、reset 或抢占。不同 A3 物理卡可运行不同 fresh session 并行；当前不做互斥调度，同一
卡上的并发只能靠选卡时的检查规避。

Plugin 不枚举候选卡、不解析 `npu-smi`，也不产生设备分配 receipt。正式流程只接收已由外部选定的物理卡号
N，将该物理卡映射为 ATK 逻辑 device 0，并在 execution receipt 中记录实际 child environment（包括
`ASCEND_RT_VISIBLE_DEVICES=<N>`）。物理编号是本轮运行时输入，不得写进 tracked spec。

若没有卡同时满足健康与空闲，agent 报告 `DEVICE_UNAVAILABLE`，逐项列出候选卡的健康与占用事实，然后等待
Mr.0 指定物理卡；此时不启动正式流程，也不伪造 workflow/device receipt。收到指定后仍须使用不存在的新
session 并重新读取 `npu-smi`；指定绝不构成强占授权。

正式 `acceptance.json` verdict 只有 `PASS`、`DUT_FAIL`、`UNSUPPORTED`。未形成正式裁决时，
`workflow.json` 与可写入时的 `attempt.json` 使用 `PLUGIN_ERROR`、`NEEDS_INPUT` 或 `BLOCKED` 描述本轮尝试；
这些状态不是 DUT 结论。ATK 控制台文字、返回码、单测或局部证据都不是验收结论。
Spec 必须显式绑定 ATK 精度比较器，caseset 会逐 case 对账。精度与性能独立取证：声明了性能维度时，精度
执行不完整也不自动跳过性能；执行错误一律保持非 DUT workflow 状态，其中超时、环境或进程隔离阻塞为
`BLOCKED`，其它流程实现错误为 `PLUGIN_ERROR`，都不会直接归因到 DUT。
任务书准入的目标 SoC 在 fresh build/install 后仍缺该算子的设备侧 ops-info/binary/kernel delivery，且请求
cache 与 host ACLNN ABI 已完整绑定时，由 skill 第 6 步的判据一次产出
`DUT_FAIL / TARGET_DELIVERY_MISSING`；普通构建失败
或证据不完整不适用该结论。
