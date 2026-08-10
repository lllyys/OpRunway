# OpRunway

OpRunway 是面向昇腾 NPU 算子的验收工作区。调用方提供**算子任务书 + 被测源码**，workflow 生成用例、
在 NPU 上执行精度与性能测试，并输出机器可校验的裁决和中文报告。

## 验收流程

```text
任务书 + 被测源码
        │
        ▼
任务书校验、内容取材与 spec/caseset/golden 生成
        │
        ▼
被测源码构建 → vendor ELF → NPU 执行
        │
        ├── 精度：可执行 golden 对 NPU DUT 输出
        └── 性能：按任务书与正式 spec 取证
        │
        ▼
证据完整性门 → 确定性裁决 → 中文报告
```

- 任务书与源码的对应关系由调用方保证；URL、PR、fork、ref、head 或本地路径只是取材方式。
- Workflow 严格绑定任务书摘要、源码内容锚、构建树、vendor ELF、实际加载符号、调用和输出证据。
- Workflow 不连接、运行、采集或消费 GPU 数据。任务书写 GPU 精度真值时解析为同族 CPU；无性能要求、
  要求 GPU 比对、新增 dtype/shape/rank/新算子或明确内存优化时，只测 NPU msprof。其它场景按任务书与
  正式 spec 执行，不自动套用 measure-only。
- 最终 PASS/FAIL 只能由确定性脚本产生，agent 和编排层不得自行改判。

## 当前准入范围

- Fresh 正式验收唯一准入的 `runner_form` 是 `cpp_extension`。
- 正式 spec 必须显式声明 `"runner_form": "cpp_extension"`。
- `cpp`、`aclnn_py`、mock、catlass 等能力不属于 fresh 正式验收入口；开发自检结果不能冒充验收裁决。
- 标准 aclnn 两段式接口通过通用 contract、codegen、driver、receipt 和 evidence 链处理；未知接口能力
  fail-closed，不按算子名增加隐藏特例。
- Build、用例/golden 生成、测试和 profiler 必须在 NPU 目标环境执行。

当前未完成能力与回归阻塞统一记录在 [OpRunway 当前 TODO](dev-doc/oprunway-todo.md)。具体算子的历史结果、
case 数和机器证据不复制到 README 或仓规，以对应验收产物为准。

## 使用方式

加载插件后，在支持的 agent 会话中提供任务书与被测源码，例如：

> 验收这个算子：任务书 `<本地路径或链接>`，源码 `<本地 checkout 或在线 locator>`。

Agent 会依次完成取材、静态门、用例生成、构建、NPU 跑测、证据复核和报告生成。缺少必要输入、目标环境、
内容锚或证据时会 fail-closed，并明确报告阻塞原因；不会切换到 mock 后宣称验收通过。

源码形式可以是在线 locator，也可以是本地快照。二者在验收上平级，均须物化完整目标内容并生成同一类
`content_anchor`。

## 插件加载

在仓根启动 Claude Code：

```bash
claude --plugin-dir ./plugin
```

开发或直接运行确定性脚本时，先设置插件根：

```bash
export OPRUNWAY_PLUGIN_ROOT="$(git rev-parse --show-toplevel)/plugin"
```

正式 workflow 入口由 agent 调用；底层命令、JSON 契约和门禁说明见 [AGENTS.md](AGENTS.md) 与
[plugin/README.md](plugin/README.md)。

## 仓库结构

```text
OpRunway/
├── AGENTS.md       # 唯一现行仓规
├── plugin/         # agent、skills、确定性脚本、契约与样例
├── dev-doc/        # 当前 TODO、环境说明、实测记录与历史流水
├── canon/          # 保留的历史记录；默认不参与项目实施
└── reports/        # ignored 的本地验收产物
```

## 开发约束

- 全程中文，规则唯一源为 [AGENTS.md](AGENTS.md)。
- 通用机制不得按算子身份特判。
- 验收数字、错误和耗时必须来自真实日志或产物。
- 不把 `gate passed`、代码接通、测试存在或局部 evidence 写成算子通过。
- 当前工作只看 [TODO](dev-doc/oprunway-todo.md)；历史改动查
  [changes brief](dev-doc/oprunway-changes-brief.md) 与 Git。
