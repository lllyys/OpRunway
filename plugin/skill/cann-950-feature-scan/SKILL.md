---
name: cann-950-feature-scan
description: 扫描任意 CANN ops 仓中依赖 Ascend 950 硬件特性（simt / hif8 / RegBase）的算子，输出测试团队可用的 markdown 清单。涉及"找 950 相关算子 / 扫描 950 覆盖度"等用户意图时使用。仓名与本地路径每次会话由 Skill 主动从 CWD 发现或询问，不再硬编码。
---

# Ascend 950 特性算子扫描

识别任意 CANN ops 仓中依赖 Ascend 950 硬件特性的算子，输出测试团队的靶子清单。

## 首次运行前置检查（P0）

激活后先检查 Python 依赖，缺失则自动安装：

```bash
python3 -c "import jinja2" 2>/dev/null \
  || pip install -r <skill>/requirements.txt
```

## When to invoke

- "扫一下某个 ops 仓的 950 算子"
- "扫描 ops-X 的 950 特性算子"
- 任何要列出 "950 相关算子" 的请求

## Inputs

每次会话**都向用户确认**输入，绝不假定。所有发现/确认/询问用**中文**和用户交互。

- **目标仓路径**：从 CWD 扫描候选 → 让用户确认 → 兜底询问（无持久化）

## Workflow

### 1. 定位目标仓（按下面三步走）

**Step 1.1 — 用户已经明确给了路径或仓名**
- 如果给了**绝对路径** → 直接用
- 如果给了**仓名（如 ops-transformer）** → 在 CWD 下找同名子目录，找到则用，找不到询问绝对路径

**Step 1.2 — 用户没指定，扫描 CWD 候选**

在 CWD 下查找具有 `docs/zh/op_list.md` 的子目录（这是 CANN ops 仓的标志），把候选列出来用中文向用户确认：

```
我在当前工作目录下发现这些候选 CANN ops 仓：
  1. ./ops-transformer  ← 含 docs/zh/op_list.md
  2. ./ops-math         ← 含 docs/zh/op_list.md
请选择要扫描的仓（多选用逗号分隔），或直接给我一个绝对路径。
```

用 `AskUserQuestion` 收集选择。

**Step 1.3 — 没找到任何候选**

提示：

```
当前工作目录 <CWD> 下未发现 CANN ops 仓（没有任何子目录含 docs/zh/op_list.md）。
请提供目标仓的绝对路径。
```

**最终校验**：选定的目标仓必须有 `<root>/docs/zh/op_list.md`，否则 fatal："这看起来不是 CANN ops 仓"。

### 2. 扫描

在项目根执行（`<skill>` 是本 skill 的安装路径）：

```bash
<python> <skill>/scripts/scan_repo.py <repo_root> \
  --op-list <repo_root>/docs/zh/op_list.md \
  --output <CWD>/cann-ops-report/<repo_name>/scan/_intermediate.json
```

### 3. 渲染

```bash
<python> <skill>/scripts/render_report.py \
  <CWD>/cann-ops-report/<repo_name>/scan/_intermediate.json \
  --out <CWD>/cann-ops-report/<repo_name>/scan/
```

### 4. 汇报给用户（中文）

```
✓ 扫描完成，产物：
  - <CWD>/cann-ops-report/<repo_name>/scan/summary.md  （主清单，N 命中）
  - <CWD>/cann-ops-report/<repo_name>/scan/detail.md   （证据明细）
  - <CWD>/cann-ops-report/<repo_name>/scan/_intermediate.json （机读 JSON）

⚠ 共发现 K 处 README/代码不一致（详见 summary.md §3）
```

## 三条扫描规则

| 规则 | 识别依据 | 说明 |
|------|---------|------|
| **SIMT** | `__simt_vf__` 关键字 | NPU 上动态执行分支逻辑 |
| **HIF8** | `HIFLOAT8` 类型 | 950 专属 8bit 高保真浮点 |
| **RegBase** | `AscendC::MicroAPI::RegTensor` API | 寄存器块级别向量存取 |

## Failure modes

| 触发 | 行为 |
|------|------|
| 目标路径无 `docs/zh/op_list.md` | fatal，提示"非 CANN ops 仓" |
| 算子目录缺失 / 读取失败 | warning，不阻塞 |
