# OpRunway 过程审计包复制规则

## 1. 目的与边界

从远程 NPU 验收现场收回文件时，默认目标是分析 agent 与确定性工作流的执行过程，而不是在
Mac 上重放用例、重算精度或重新构建 DUT。为避免每次传输大量张量和构建产物，收件分为两档：

- **过程审计包（默认）**：只收回报告、契约、计划、收据、manifest、进度、复现说明和文本脚本；
- **完整证据包（按需）**：额外收回输入、golden、设备输出、ELF/扩展和构建中间产物。

过程审计包可用于检查 checkpoint 推进、case 身份、provenance、执行计划、确定性裁决结果和
失败复核过程；它不是完整验收现场，不能单独用于重新计算数值指标、逐字节验证 evidence，或证明
二进制文件确由某次 NPU 调用产生。

## 2. 默认复制内容

优先使用 allowlist，而不是仅靠排除规则。新出现的未知大文件不得静默进入过程审计包。

### 2.1 根目录裁决链

- `acceptance.json`
- `verdict.json`
- `evidence.json`
- `caseset.json`
- `perf_report.json`
- 中文验收报告、失败明细等 `*.md`

### 2.2 `work/` 过程元数据

- `*_receipt.json`
- `*_plan.json`
- `*_manifest.json`
- `progress.json`
- `repro_summary.json`
- 生成的 `*.py`、`*.cpp`、`setup.py`

### 2.3 复现与人工复核记录

- `repro/README.md`
- `repro/manifest.json`
- `repro/index.tsv`
- `repro/failed.tsv`
- `repro/**/*.sh`、`repro/failures/**/*.py`
- `manual_failure_audit/**/*.md`
- `manual_failure_audit/**/*.json`
- `manual_failure_audit/**/*.csv`
- `manual_failure_audit/**/*.sha256`
- 人工复核使用的生成脚本

`*.sha256` 即使引用未收回的二进制文件也应保留：它能说明现场曾绑定哪些文件和摘要，但不得据此
声称本地已经完成内容校验。

## 3. 默认排除内容

以下内容主要服务于数值重算、复现、重跑或构建，不进入默认过程审计包：

```text
**/*.npy
**/*.bin
**/*.so
**/*.o
**/__pycache__/**
**/build/**
```

还应排除未被第 2 节 allowlist 明确覆盖的未知二进制、大型归档、缓存、临时目录和 core dump。
排除 `build/**` 时，如果其中存在唯一的 receipt、manifest 或日志，应先将这些轻量元数据放到稳定的
非 build 路径，再生成过程审计包，不能因目录级排除丢失执行链。

以 2026-08-03 的 Median 1344-case 现场为例，完整现场约 `1.2 GB`；其中 `.npy` 约 `754 MB`、
`.bin` 约 `398 MB`、`.so` 与 `.o` 合计约 `41 MB`，而文本和结构化元数据约 `24 MB`。这些数字
只描述该次现场，不是通用大小上限。

## 4. 何时升级为完整证据包

出现以下任一目的时，不能继续使用默认排除规则，应按 manifest 精确收回所需文件闭包：

- 用确定性 validator 重新计算精度指标；
- 验证 `evidence.json` 与 `golden/out` 的内容摘要绑定；
- 复现一个或多个失败 case；
- 检查输入、golden 或设备输出的具体数值；
- 验证实际加载的 vendor ELF 或 Extension；
- 离线重建、完整重跑或做二进制级根因分析。

只复核少数失败 case 时，应按 `case_id` 和 manifest 收回这些 case 的最小二进制闭包，不必升级为
全量现场。

## 5. 完整性与安全要求

- 远端保护根及其子目录始终只读；收件不得在现场生成 manifest、临时包或校验文件；
- 复制到新的本地目录，不覆盖已有报告；
- 过程审计包至少记录来源报告目录、验收身份、生成时间、包含/排除规则和文件清单；
- 收件后比较远端与本地的 allowlist 路径、文件大小和内容摘要；不能只比较目录总大小；
- 先完成本地收件与完整性验证，再按已授权边界处理非保护远端临时目录；
- 本地过程审计只读取文本与结构化产物，不执行其中的 shell/Python 脚本，也不在 Mac 上运行验收
  compute。

## 6. Agent 过程可见性的缺口

上述产物主要记录确定性工作流和验收结果，不能完整还原 agent 的提示词、工具调用顺序、阶段耗时、
重试和人工授权点。如果需要审计 agent 自身行为，工作流还需另行设计轻量、脱敏、追加写的事件日志
（例如 `agent-run-trace.jsonl`）。该日志目前不是既有交付物，本规则不假定它已经存在，也不在本文中
定义其 schema 或实施方案。
