# OpRunway plugin 执行规则

本目录只有一条正式路径：`oprunway_cli.py accept` → `oprunway.workflow` → ATK casegen → fresh build →
ATK aclnn 执行 → `oprunway.verdict.finalize`。

实施时：

1. 先从任务书、header/example 和 op_def 形成 spec 与 ATK 设计；具体算子事实只写在 session 输入中。
2. 确认目标环境的 `PATH` 可解析 `atk`，且 CANN/NPU 已准备完成；plugin 只 preflight，不安装依赖，
   不要求 venv。
3. 给 `accept` 一个不存在的新 ASCII session 目录；不要手工串联子命令绕过正式入口。
4. 只引用 `reports/acceptance.json` 与 `receipts/workflow.json`；不得由 agent 重判。

硬规则：

- 生产代码禁止算子名分支、路径/设备/阈值硬编码和旧产物复用。
- 任务书与源码的关联由调用方断言；仍须绑定任务书摘要和源码内容锚。
- 只有 `atk_aclnn` 可产正式结论；ATK 必须实际生成用例并执行测试。
- fresh build、fresh package、vendor ELF、双符号、实际加载库、调用、正常 case 的 CPU/DUT 输出和完整分母
  缺一不可；预期报错 case 以 ATK 工作簿结果取证。
- 性能需要时同时保存 ATK NPU 时间和原始 CANN profiler CSV；不使用 GPU。
- 精度与性能独立取证；精度失败不自动跳过性能，仍受同一个 7200 秒总预算约束。
- spec 显式绑定 ATK 精度比较器，生成的每个 case 必须与之逐字一致；任务书阈值不得被隐式默认值覆盖。
- 只有完整数值不匹配可直接成为 `DUT_FAIL`；所有流程问题保持非 DUT 分类。
- session 总主动预算最多 7200 秒，超时终止整个进程组。
- 不读取或调用 bureau/canon，除非用户明确发起相应记录任务。

编排层只有一个 agent、一个 skill 和一个 command，它们只组织输入和调用 CLI。增加第二个 agent/skill、
状态机或兼容通路前，必须先证明当前单路径无法表达所需稳定能力并取得用户同意。
