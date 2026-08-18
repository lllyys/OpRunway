# Repo Task ATK Skill Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 精简社区算子验收 Skill，并修复会改变精度、性能和裁决结果的脚本缺陷。

**Architecture:** `SKILL.md` 只保留全流程原则和线性步骤。阶段知识进入单一职责 reference。多脚本共享的声明式政策进入 JSON。公共用例解析和报告读取进入内部 Python 模块。阶段 CLI 保持独立。

**Tech Stack:** Markdown、JSON、Python 3 标准库、openpyxl、PyYAML、ATK。

---

## 实施约束

- 用户已明确不采用 TDD 流程。
- 每项任务先实现最小改动。
- 每项任务随后补充回归测试。
- 不引入 pytest。
- 不引入 jsonschema。
- 不修改 `atk/`。
- 不读取待测算子源码。
- 不为旧 reference 文件名保留兼容层。
- 不为脚本内部函数保留兼容层。
- 不提交 `__pycache__`。
- 不提交 `.pyc` 文件。

## 文件结构

### 保留并重写

- `skill/repo-task-atk-test/SKILL.md`
- `skill/repo-task-atk-test/scripts/probe_env.py`
- `skill/repo-task-atk-test/scripts/check_coverage.py`
- `skill/repo-task-atk-test/scripts/validate_cases.py`
- `skill/repo-task-atk-test/scripts/preflight_cases.py`
- `skill/repo-task-atk-test/scripts/make_manifest.py`
- `skill/repo-task-atk-test/scripts/select_perf_cases.py`
- `skill/repo-task-atk-test/scripts/parse_atk_report.py`
- `skill/repo-task-atk-test/scripts/verdict.py`
- `skill/repo-task-atk-test/scripts/make_repro.py`

### 新增

- `skill/repo-task-atk-test/references/intake.md`
- `skill/repo-task-atk-test/references/case-design.md`
- `skill/repo-task-atk-test/references/execution.md`
- `skill/repo-task-atk-test/references/performance.md`
- `skill/repo-task-atk-test/references/reporting.md`
- `skill/repo-task-atk-test/references/acceptance-policy.json`
- `skill/repo-task-atk-test/scripts/_case_utils.py`
- `skill/repo-task-atk-test/scripts/_policy.py`
- `skill/repo-task-atk-test/scripts/_report_reader.py`
- `skill/repo-task-atk-test/tests/test_case_utils.py`
- `skill/repo-task-atk-test/tests/test_policy.py`
- `skill/repo-task-atk-test/tests/test_report_parser.py`
- `skill/repo-task-atk-test/tests/test_perf_selection.py`
- `skill/repo-task-atk-test/tests/test_verdict.py`
- `skill/repo-task-atk-test/tests/test_document_style.py`

### 保留并精简

- `skill/repo-task-atk-test/references/yaml-schema.md`
- `skill/repo-task-atk-test/references/plugin-authoring.md`
- `skill/repo-task-atk-test/references/experimental_standard.md`

### 删除

- `skill/repo-task-atk-test/references/acceptance-constraints.md`
- `skill/repo-task-atk-test/references/api-binding-contract.md`
- `skill/repo-task-atk-test/references/atk_user_guide.md`
- `skill/repo-task-atk-test/references/build-and-deploy.md`
- `skill/repo-task-atk-test/references/coverage-design.md`
- `skill/repo-task-atk-test/references/interface-mode.md`
- `skill/repo-task-atk-test/references/performance-acceptance.md`
- `skill/repo-task-atk-test/references/troubleshooting.md`
- `skill/repo-task-atk-test/references/verdict-and-report-schema.md`
- `skill/repo-task-atk-test/scripts/__pycache__/`

## Task 1: 抽取公共用例处理

**Files:**

- Create: `skill/repo-task-atk-test/scripts/_case_utils.py`
- Modify: `skill/repo-task-atk-test/scripts/check_coverage.py`
- Modify: `skill/repo-task-atk-test/scripts/validate_cases.py`
- Modify: `skill/repo-task-atk-test/scripts/preflight_cases.py`
- Modify: `skill/repo-task-atk-test/scripts/select_perf_cases.py`
- Test: `skill/repo-task-atk-test/tests/test_case_utils.py`

- [ ] **Step 1: 新建公共用例模块**

实现以下完整公共模块：

```python
import hashlib
import json
import math
from pathlib import Path

TENSOR_TYPES = frozenset({"tensor", "tensors", "tensor_tuple"})
SCALAR_TYPES = frozenset({"scalar", "scalars", "scalar_tuple"})
ATTR_TYPES = frozenset({"attr", "attrs", "attr_tuple"})
TUPLE_TYPES = frozenset({"tensor_tuple", "scalar_tuple", "attr_tuple"})

def load_json(path):
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)

def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def iter_cases(case_data):
    if isinstance(case_data, dict):
        return case_data.get("cases", [case_data])
    return case_data

def grouped_inputs(case):
    groups = list(case.get("inputs") or [])
    groups.extend(case.get("method_inputs") or [])
    if case.get("tensor_input"):
        groups.append(case["tensor_input"])
    return groups

def _flatten(value):
    if isinstance(value, list):
        for item in value:
            yield from _flatten(item)
    elif isinstance(value, dict):
        yield value

def iter_input_specs(case):
    for group in grouped_inputs(case):
        yield from _flatten(group)

def tensor_inputs(case):
    return [spec for spec in iter_input_specs(case)
            if spec.get("type") in TENSOR_TYPES]

def attr_value(case, name):
    for group in grouped_inputs(case):
        specs = list(_flatten(group))
        if not specs or specs[0].get("name") != name:
            continue
        values = [spec.get("range_values", spec.get("value")) for spec in specs]
        if specs[0].get("type") in TUPLE_TYPES:
            return tuple(values)
        return values[0] if len(values) == 1 else values
    return None

def numel(shape):
    return math.prod(shape or [])

def extract_axis(case, rule):
    source = rule.get("from")
    if source == "attr":
        return attr_value(case, rule["name"])
    tensors = tensor_inputs(case)
    index = rule.get("index", 0)
    if index >= len(tensors):
        return None
    tensor = tensors[index]
    if source == "input_dtype":
        return tensor.get("dtype")
    if source == "input_shape":
        return tensor.get("shape")
    if source == "input_rank":
        return len(tensor.get("shape") or [])
    if source == "input_numel":
        return numel(tensor.get("shape"))
    raise ValueError(f"未知 extract.from: {source}")

def normalize(value):
    if isinstance(value, list):
        return tuple(normalize(item) for item in value)
    if isinstance(value, dict):
        return tuple((key, normalize(value[key])) for key in sorted(value))
    return value

def signature(values, axes):
    return "|".join(f"{axis}={normalize(values.get(axis))!r}" for axis in axes)
```

`iter_input_specs()` 递归摊平 `inputs`、`method_inputs` 和 `tensor_input`。

`grouped_inputs()` 保留嵌套 list 的调用分组。

`tensor_inputs()` 接受三种 tensor 类型。

`numel([])` 返回 `1`。

`numel()` 遇到 `range_values == "null"` 时由调用方判定空张量。

`file_sha256()` 分块读取文件。

- [ ] **Step 2: 替换四个脚本中的重复实现**

删除以下重复函数：

```text
load
iter_cases
tensor_inputs
attr_value
numel
extract_axis
norm
signature
```

所有调用改为从 `_case_utils` 导入。

直接执行脚本时使用普通同目录导入：

```python
from _case_utils import extract_axis, iter_cases, tensor_inputs
```

- [ ] **Step 3: 修复复合输入调用构造**

在 `preflight_cases.py` 中让一组嵌套输入生成一个参数。

`tensor_tuple` 生成 tuple。

`scalar_tuple` 生成 tuple。

`attr_tuple` 生成 tuple。

`tensors`、`scalars` 和 `attrs` 生成 list。

参数名取组内首项的 `name`。

空参数名进入 `args`。

非空参数名进入 `kwargs`。

核心结构如下：

```python
def materialize_group(group, torch, dmap, shape=None):
    specs = group if isinstance(group, list) else [group]
    values = [_one_value(spec, torch, dmap, shape) for spec in specs]
    kind = specs[0].get("type")
    if kind in TUPLE_TYPES:
        return tuple(values)
    if len(specs) > 1:
        return values
    return values[0]
```

- [ ] **Step 4: 添加公共模块回归测试**

覆盖以下样例：

```python
case = {
    "inputs": [
        {"name": "x", "type": "tensor", "dtype": "float32", "shape": [2, 3]},
        [
            {"name": "dims", "type": "attr_tuple", "dtype": "int", "range_values": 1},
            {"name": "dims", "type": "attr_tuple", "dtype": "int", "range_values": 2}
        ]
    ]
}
```

断言张量输入数量为 `1`。

断言 `dims` 的属性值为 `(1, 2)`。

断言 shape `[2, 3]` 的 numel 为 `6`。

断言同一对象的签名稳定。

- [ ] **Step 5: 运行测试**

Run:

```bash
python3 -m unittest discover -s skill/repo-task-atk-test/tests \
  -p 'test_case_utils.py' -v
```

Expected: 所有测试显示 `ok`。

- [ ] **Step 6: 提交公共用例处理**

```bash
git add skill/repo-task-atk-test/scripts/_case_utils.py \
  skill/repo-task-atk-test/scripts/check_coverage.py \
  skill/repo-task-atk-test/scripts/validate_cases.py \
  skill/repo-task-atk-test/scripts/preflight_cases.py \
  skill/repo-task-atk-test/scripts/select_perf_cases.py \
  skill/repo-task-atk-test/tests/test_case_utils.py
git commit -m "refactor: unify ATK case handling"
```

## Task 2: 集中声明式验收政策

**Files:**

- Create: `skill/repo-task-atk-test/references/acceptance-policy.json`
- Create: `skill/repo-task-atk-test/scripts/_policy.py`
- Modify: `skill/repo-task-atk-test/scripts/make_manifest.py`
- Modify: `skill/repo-task-atk-test/scripts/parse_atk_report.py`
- Modify: `skill/repo-task-atk-test/scripts/verdict.py`
- Test: `skill/repo-task-atk-test/tests/test_policy.py`

- [ ] **Step 1: 新建政策文件**

使用以下顶层结构：

```json
{
  "schema_version": 1,
  "interface_modes": {
    "pytorch": {"backends": ["npu"], "required_yaml_field": "name"},
    "aclnn": {"backends": ["pyaclnn", "aclnn"], "required_yaml_field": "aclnn_name"},
    "kernel": {"backends": ["kernel"], "required_yaml_field": "kernel_name"},
    "triton": {"backends": ["triton"], "required_yaml_field": "triton_name"},
    "atb": {"backends": ["atb"], "required_yaml_field": null}
  },
  "accuracy": {
    "allowed_comparators": ["default", "equal", "mixed_tolerance_bm"],
    "forbidden_override_prefixes": ["fp16_", "bf16_", "fp32_", "hf32_", "fp8e4m3_", "fp8e5m2_"],
    "forbidden_override_keys": ["output_dtype_overrides"]
  },
  "failure_attribution": [
    {
      "id": "operator_defect",
      "markers": ["aic-error", "aicore error", "aicerror", "ddr address out of range", "trap exception"],
      "attribution": "developer",
      "message": "设备侧核内异常"
    },
    {
      "id": "env_or_builtin",
      "markers": ["not supported yet", "unimplemented", "aicpu exception"],
      "attribution": "pending_recheck",
      "message": "环境或 CANN 内置路径不支持"
    },
    {
      "id": "case_design",
      "markers": ["out of memory", "outofmemory", "cannot allocate", "分配内存失败", "bad_alloc"],
      "attribution": "pending_recheck",
      "message": "用例资源需求超出预算"
    },
    {
      "id": "cascade",
      "markers": ["507018", "stream synchronize failed", "synchronizedevice"],
      "attribution": "pending_recheck",
      "message": "设备异常后的连带失败"
    }
  ],
  "conclusion_causes": ["operator_defect", "accuracy_gap"]
}
```

规则顺序即匹配优先级。

- [ ] **Step 2: 实现政策加载器**

实现以下完整加载器：

```python
import hashlib
import json
from pathlib import Path

class PolicyError(ValueError):
    pass

def default_policy_path():
    return Path(__file__).resolve().parents[1] / "references" / "acceptance-policy.json"

def validate_policy(policy):
    if policy.get("schema_version") != 1:
        raise PolicyError("[POLICY_SCHEMA] schema_version 必须为 1")
    modes = policy.get("interface_modes")
    if not isinstance(modes, dict) or not modes:
        raise PolicyError("[POLICY_MODES] interface_modes 必须为非空对象")
    for name, mode in modes.items():
        backends = mode.get("backends") if isinstance(mode, dict) else None
        if not isinstance(backends, list) or not backends or not all(isinstance(item, str) for item in backends):
            raise PolicyError(f"[POLICY_BACKENDS] {name} 的 backends 必须为非空字符串列表")
    accuracy = policy.get("accuracy")
    comparators = accuracy.get("allowed_comparators") if isinstance(accuracy, dict) else None
    if not isinstance(comparators, list) or not comparators or not all(isinstance(item, str) for item in comparators):
        raise PolicyError("[POLICY_ACCURACY] allowed_comparators 必须为非空字符串列表")
    rules = policy.get("failure_attribution")
    if not isinstance(rules, list):
        raise PolicyError("[POLICY_FAILURES] failure_attribution 必须为列表")
    identifiers = []
    for rule in rules:
        identifier = rule.get("id") if isinstance(rule, dict) else None
        markers = rule.get("markers") if isinstance(rule, dict) else None
        if not identifier or not isinstance(markers, list) or not markers:
            raise PolicyError("[POLICY_FAILURE] 每条归因必须提供 id 和 markers")
        identifiers.append(identifier)
    if len(identifiers) != len(set(identifiers)):
        raise PolicyError("[POLICY_FAILURE_ID] 归因 id 不得重复")
    allowed_causes = set(identifiers) | {"accuracy_gap"}
    conclusion_causes = policy.get("conclusion_causes")
    if not isinstance(conclusion_causes, list) or not set(conclusion_causes) <= allowed_causes:
        raise PolicyError("[POLICY_CONCLUSION] conclusion_causes 包含未声明原因")

def load_policy(path=None):
    policy_path = Path(path) if path else default_policy_path()
    try:
        with policy_path.open(encoding="utf-8") as stream:
            policy = json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        raise PolicyError(f"[POLICY_LOAD] 无法加载验收政策：{error}") from error
    validate_policy(policy)
    return policy

def policy_sha256(path=None):
    policy_path = Path(path) if path else default_policy_path()
    digest = hashlib.sha256(policy_path.read_bytes()).hexdigest()
    return digest
```

校验以下条件：

- `schema_version` 必须为整数 `1`。
- `interface_modes` 必须为非空对象。
- 每个接口模式必须提供非空 `backends`。
- `allowed_comparators` 必须为非空字符串列表。
- 错误归因 `id` 不得重复。
- 每条错误归因必须提供非空 markers。
- `conclusion_causes` 中的值必须是已声明原因或 `accuracy_gap`。

- [ ] **Step 3: 替换脚本硬编码政策**

从 `make_manifest.py` 删除 `INTERFACE_MODES`。

从 `verdict.py` 删除 `ALLOWED_ACC`。

从 `verdict.py` 删除 `THRESHOLD_PREFIXES`。

从 `verdict.py` 删除 `KNOWN_INTERFACE_MODES`。

从 `verdict.py` 删除 `DEVELOPER_CAUSES`。

从 `parse_atk_report.py` 删除 `FAILURE_RULES`。

脚本启动时加载一次政策。

函数通过参数接收政策。

- [ ] **Step 4: 添加政策回归测试**

测试有效政策加载成功。

测试缺失 `interface_modes` 时抛出 `PolicyError`。

测试空后端列表时抛出 `PolicyError`。

测试重复原因 id 时抛出 `PolicyError`。

测试政策摘要为 64 位十六进制字符串。

- [ ] **Step 5: 运行测试**

Run:

```bash
python3 -m unittest discover -s skill/repo-task-atk-test/tests \
  -p 'test_policy.py' -v
```

Expected: 所有测试显示 `ok`。

- [ ] **Step 6: 提交政策文件**

```bash
git add skill/repo-task-atk-test/references/acceptance-policy.json \
  skill/repo-task-atk-test/scripts/_policy.py \
  skill/repo-task-atk-test/scripts/make_manifest.py \
  skill/repo-task-atk-test/scripts/parse_atk_report.py \
  skill/repo-task-atk-test/scripts/verdict.py \
  skill/repo-task-atk-test/tests/test_policy.py
git commit -m "refactor: centralize ATK acceptance policy"
```

## Task 3: 修复报告解析和精度统计

**Files:**

- Create: `skill/repo-task-atk-test/scripts/_report_reader.py`
- Modify: `skill/repo-task-atk-test/scripts/parse_atk_report.py`
- Test: `skill/repo-task-atk-test/tests/test_report_parser.py`

- [ ] **Step 1: 隔离 ATK 报告格式适配**

将以下内容移入 `_report_reader.py`：

```text
列名常量
KNOWN_BACKENDS
backend_of
backends_of
nodes_from_header
read_sheet
cell
blank
find_col
node_cols
pick_data_col
family_filled
detect_task
```

导出 `read_workbook(path)`。

导出 `read_sheet(workbook, name)`。

导出 `detect_task(header, rows)`。

导出 `nodes_from_header(header)`。

导出 `pick_data_col(header, rows, suffix)`。

`parse_atk_report.py` 只负责业务字段归一化。

- [ ] **Step 2: 修复 totals_of**

替换为不排除任何实际执行用例的统计：

```python
def totals_of(task, cases):
    total = len(cases)
    passed = sum(bool(case.get("passed")) for case in cases)
    failed = total - passed
    result = {"cases": total, "passed": passed, "failed": failed}
    result["pass_rate"] = passed / total if total else 0.0
    return result
```

性能统计继续增加性能字段。

性能字段不得改变 cases、passed、failed 和 pass_rate。

- [ ] **Step 3: 将无效标杆改为事实标签**

删除 `invalid_golden` 字段。

输入范围包含 nan 或 inf 时写入：

```json
{"non_finite_input": true}
```

该标签不改变通过率。

该标签不改变失败数。

- [ ] **Step 4: 汇总全部精度标准**

实现：

```python
def summarize_standard_acc(cases):
    values = [case.get("standard", {}).get("acc") for case in cases]
    present = [value for value in values if value is not None]
    unique = []
    for value in present:
        if value not in unique:
            unique.append(value)
    return {
        "total_cases": len(cases),
        "present_cases": len(present),
        "consistent": len(unique) == 1 and len(present) == len(cases),
        "value": unique[0] if len(unique) == 1 else None,
        "values": unique,
    }
```

解析结果使用 `standard_acc_summary` 字段。

- [ ] **Step 5: 记录报告和用例摘要**

解析结果增加：

```json
{
  "source_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "case_file_sha256": "1111111111111111111111111111111111111111111111111111111111111111",
  "case_count": 2
}
```

未传 `--case-file` 时省略 `case_file_sha256`。

精度解析必须传 `--case-file`。

缺失时退出码为 `2`。

- [ ] **Step 6: 添加报告解析回归测试**

测试两个通过用例和一个非有限标签时：

```python
cases = [
    {"passed": True, "non_finite_input": True},
    {"passed": True}
]
```

断言 cases 为 `2`。

断言 passed 为 `2`。

断言 failed 为 `0`。

断言 pass_rate 为 `1.0`。

测试第二个用例改变精度标准时 `consistent` 为 false。

测试所有用例缺失精度标准时 `present_cases` 为 `0`。

- [ ] **Step 7: 运行测试**

Run:

```bash
python3 -m unittest discover -s skill/repo-task-atk-test/tests \
  -p 'test_report_parser.py' -v
```

Expected: 所有测试显示 `ok`。

- [ ] **Step 8: 提交报告解析修复**

```bash
git add skill/repo-task-atk-test/scripts/_report_reader.py \
  skill/repo-task-atk-test/scripts/parse_atk_report.py \
  skill/repo-task-atk-test/tests/test_report_parser.py
git commit -m "fix: make ATK accuracy statistics objective"
```

## Task 4: 加固 manifest 和裁决门禁

**Files:**

- Modify: `skill/repo-task-atk-test/scripts/check_coverage.py`
- Modify: `skill/repo-task-atk-test/scripts/make_manifest.py`
- Modify: `skill/repo-task-atk-test/scripts/verdict.py`
- Modify: `skill/repo-task-atk-test/scripts/make_repro.py`
- Test: `skill/repo-task-atk-test/tests/test_verdict.py`

- [ ] **Step 1: 为覆盖产物记录摘要**

`coverage.json` 增加：

```json
{
  "must_cover_sha256": "2222222222222222222222222222222222222222222222222222222222222222",
  "case_file_sha256": "1111111111111111111111111111111111111111111111111111111111111111"
}
```

- [ ] **Step 2: 为 manifest 记录摘要**

`manifest.json` 增加：

```json
{
  "policy": {"schema_version": 1, "sha256": "3333333333333333333333333333333333333333333333333333333333333333"},
  "artifacts": {
    "yaml": {"path": "median.yaml", "sha256": "4444444444444444444444444444444444444444444444444444444444444444"},
    "constraint": {"path": "median_constraint.py", "sha256": "5555555555555555555555555555555555555555555555555555555555555555"},
    "must_cover": {"path": "must_cover.json", "sha256": "2222222222222222222222222222222222222222222222222222222222222222"},
    "case_file": {"path": "all_median.json", "sha256": "1111111111111111111111111111111111111111111111111111111111111111"}
  }
}
```

未提供 constraint 时省略该项。

未提供 must_cover 时省略该项。

门禁存在问题时不得写出 manifest。

- [ ] **Step 3: 加固裁决输入检查**

`verdict.py` 依次检查：

1. `results.task` 必须为 `accuracy`。
2. `results.case_file_sha256` 必须匹配 manifest。
3. `coverage.case_file_sha256` 必须匹配 manifest。
4. `coverage.must_cover_sha256` 必须匹配 manifest。
5. 当前政策摘要必须匹配 manifest。
6. 实际后端集合不能为空。
7. 实际后端必须属于接口模式允许集合。
8. 精度标准必须覆盖所有用例。
9. 精度标准必须在所有用例中一致。
10. 精度比较器必须在允许集合中。
11. 精度标准不得包含禁止覆盖字段。

每个失败使用独立规则编号。

- [ ] **Step 4: 修复指定输出判断**

实现严格存在性检查：

```python
def case_passed(case, judged):
    if not judged:
        return bool(case.get("passed"))
    outputs = {item.get("name"): bool(item.get("passed"))
               for item in case.get("outputs") or []}
    if any(name not in outputs for name in judged):
        return False
    return all(outputs[name] for name in judged)
```

裁决前单独报告不存在的输出名。

不存在输出时拒绝出具结论。

- [ ] **Step 5: 保持客观通过率**

`summarize()` 使用全部实际执行用例。

`summarize()` 不读取 `non_finite_input` 改变分母。

`cascade` 用例保留在通过率中。

归因统计与通过率统计分开输出。

- [ ] **Step 6: 更新复现包说明**

`make_repro.py` 的 README 写入政策版本和摘要。

`make_repro.py` 保留 manifest 的 artifact 摘要。

复现包不生成未执行过的命令。

- [ ] **Step 7: 添加裁决回归测试**

覆盖以下失败场景：

- `task == performance`。
- `backends == []`。
- manifest 与 results 的 case 摘要不同。
- 精度标准只存在于部分用例。
- 精度标准存在两个值。
- 指定 `output_9.pt` 但实际输出中不存在。
- 政策摘要不一致。

覆盖以下成功场景：

- 两个实际输出都存在且通过。
- 非有限输入标签不改变通过率。

- [ ] **Step 8: 运行测试**

Run:

```bash
python3 -m unittest discover -s skill/repo-task-atk-test/tests \
  -p 'test_verdict.py' -v
```

Expected: 所有测试显示 `ok`。

- [ ] **Step 9: 提交裁决门禁**

```bash
git add skill/repo-task-atk-test/scripts/check_coverage.py \
  skill/repo-task-atk-test/scripts/make_manifest.py \
  skill/repo-task-atk-test/scripts/verdict.py \
  skill/repo-task-atk-test/scripts/make_repro.py \
  skill/repo-task-atk-test/tests/test_verdict.py
git commit -m "fix: bind ATK verdict to exact artifacts"
```

## Task 5: 修复性能抽样和资源预检

**Files:**

- Modify: `skill/repo-task-atk-test/scripts/select_perf_cases.py`
- Modify: `skill/repo-task-atk-test/scripts/preflight_cases.py`
- Test: `skill/repo-task-atk-test/tests/test_perf_selection.py`

- [ ] **Step 1: 修复性能配额分配**

`allocate()` 返回的配额总和不得超过 `total`。

格子数大于目标数时只选择 `total` 个格子。

格子排序使用稳定的字符串键。

基础实现：

```python
def allocate(cell_keys, total):
    keys = sorted(cell_keys, key=str)
    if total <= 0:
        return {key: 0 for key in keys}
    selected = keys[:total] if len(keys) > total else keys
    base, extra = divmod(total, len(selected))
    quota = {key: 0 for key in keys}
    for index, key in enumerate(selected):
        quota[key] = base + (1 if index < extra else 0)
    return quota
```

候选不足时输出实际选中数量。

候选不足不复制用例。

- [ ] **Step 2: 修复多输出内存预算**

输入预算使用全部输入张量字节数之和。

输出预算使用全部输出张量字节数之和。

同时保留最大单张量字节数。

输出字段：

```json
{
  "input_bytes_total": 0,
  "output_bytes_total": 0,
  "largest_tensor_bytes": 0
}
```

总输入或总输出超过预算时退出码为 `2`。

- [ ] **Step 3: 精简预检说明**

模块 docstring 只保留用途、输入、输出和退出码。

删除历史 OOM 案例。

删除耗时描述。

删除 ATK 源码行号。

错误信息使用规则编号。

- [ ] **Step 4: 添加性能回归测试**

断言：

```python
allocate(["a", "b", "c"], 2)
```

配额总和为 `2`。

非零配额格子数为 `2`。

断言 50 个目标不会选出 51 条用例。

断言候选只有 7 条时返回 7 条。

- [ ] **Step 5: 运行测试**

Run:

```bash
python3 -m unittest discover -s skill/repo-task-atk-test/tests \
  -p 'test_perf_selection.py' -v
```

Expected: 所有测试显示 `ok`。

- [ ] **Step 6: 提交性能和预检修复**

```bash
git add skill/repo-task-atk-test/scripts/select_perf_cases.py \
  skill/repo-task-atk-test/scripts/preflight_cases.py \
  skill/repo-task-atk-test/tests/test_perf_selection.py
git commit -m "fix: cap performance cases and preflight memory"
```

## Task 6: 精简全部脚本说明

**Files:**

- Modify: `skill/repo-task-atk-test/scripts/probe_env.py`
- Modify: `skill/repo-task-atk-test/scripts/check_coverage.py`
- Modify: `skill/repo-task-atk-test/scripts/validate_cases.py`
- Modify: `skill/repo-task-atk-test/scripts/preflight_cases.py`
- Modify: `skill/repo-task-atk-test/scripts/make_manifest.py`
- Modify: `skill/repo-task-atk-test/scripts/select_perf_cases.py`
- Modify: `skill/repo-task-atk-test/scripts/parse_atk_report.py`
- Modify: `skill/repo-task-atk-test/scripts/verdict.py`
- Modify: `skill/repo-task-atk-test/scripts/make_repro.py`

- [ ] **Step 1: 统一模块 docstring**

每个模块使用以下结构：

```python
"""一句话用途。

输入：关键 CLI 输入。
输出：结构化产物。
退出码：0 完成；2 确定性问题；3 无法检查。
"""
```

不适用退出码 `3` 的脚本不写该项。

- [ ] **Step 2: 精简函数 docstring**

保留函数的输入语义。

保留函数的返回契约。

保留不可见的格式约束。

删除事故故事。

删除说服性段落。

删除重复的 reference 内容。

- [ ] **Step 3: 统一错误文本**

错误采用：

```text
[RULE_ID] 问题
证据：实际值
动作：下一步操作
```

同一条错误不得合并两个门禁问题。

- [ ] **Step 4: 编译检查**

Run:

```bash
python3 -m compileall -q skill/repo-task-atk-test/scripts
```

Expected: 退出码为 `0`。

- [ ] **Step 5: 删除生成缓存**

删除 `skill/repo-task-atk-test/scripts/__pycache__/`。

确认 `find skill/repo-task-atk-test -name '*.pyc'` 无输出。

- [ ] **Step 6: 提交脚本文字精简**

```bash
git add skill/repo-task-atk-test/scripts
git commit -m "refactor: trim ATK script guidance"
```

## Task 7: 重组并精简 references

**Files:**

- Create: `skill/repo-task-atk-test/references/intake.md`
- Create: `skill/repo-task-atk-test/references/case-design.md`
- Create: `skill/repo-task-atk-test/references/execution.md`
- Create: `skill/repo-task-atk-test/references/performance.md`
- Create: `skill/repo-task-atk-test/references/reporting.md`
- Modify: `skill/repo-task-atk-test/references/yaml-schema.md`
- Modify: `skill/repo-task-atk-test/references/plugin-authoring.md`
- Keep: `skill/repo-task-atk-test/references/experimental_standard.md`
- Delete: 设计中列出的九个旧 reference 文件

- [ ] **Step 1: 编写 intake.md**

固定目录：

```text
目标
输入清单
约束来源
验收约束表
接口模式确认
基线接口确认
阻塞条件
```

只保留受理阶段需要的信息。

- [ ] **Step 2: 编写 case-design.md**

固定目录：

```text
目标
覆盖来源
覆盖维度表
must_cover.json
不可行组合
覆盖校验
常见失效
```

删除算子专属事故复盘。

保留完整 `must_cover.json` 最小示例。

- [ ] **Step 3: 精简 yaml-schema.md**

保留 ATK 支持的全部输入类型。

保留字段表。

保留精度标准配置。

保留最小 YAML 示例。

删除插件模板。

删除执行命令。

- [ ] **Step 4: 精简 plugin-authoring.md**

合并 API 绑定契约。

保留 constraint 生成器模板。

保留 CPU golden 薄壳模板。

保留 aclnn 执行插件模板。

删除 YAML schema。

删除事故故事。

- [ ] **Step 5: 编写 execution.md**

固定目录：

```text
环境探测
用例生成
静态校验
资源预检
构建部署
精度执行
性能执行
运行错误
中止条件
```

不提供待测源码静态核对步骤。

运行错误只提供最小处置。

- [ ] **Step 6: 编写 performance.md**

保留性能标杆来源。

保留固定 50 条的选择规则。

保留格子定义。

保留波动结果呈现方式。

删除性能脚本实现说明。

- [ ] **Step 7: 编写 reporting.md**

保留结果 JSON 字段。

保留裁决 JSON 字段。

保留阻塞报告模板。

保留完整验收报告模板。

保留报错归因的客观表述方法。

不重复政策 JSON 中的错误特征表。

- [ ] **Step 8: 删除旧 reference**

使用明确文件路径删除九个旧文件。

不得使用 reference 目录级递归删除。

- [ ] **Step 9: 检查 reference 路由**

Run:

```bash
rg -n 'acceptance-constraints|api-binding-contract|atk_user_guide|build-and-deploy|coverage-design|interface-mode|performance-acceptance|troubleshooting|verdict-and-report-schema' \
  skill/repo-task-atk-test
```

Expected: 无旧文件引用。

- [ ] **Step 10: 提交 reference 重组**

```bash
git add skill/repo-task-atk-test/references
git commit -m "docs: simplify ATK acceptance references"
```

## Task 8: 按 onboarding 节奏重写主 Skill

**Files:**

- Modify: `skill/repo-task-atk-test/SKILL.md`

- [ ] **Step 1: 保留触发描述**

frontmatter 只保留 `name` 和 `description`。

description 同时说明能力和触发场景。

- [ ] **Step 2: 编写十步线性 workflow**

固定结构：

```text
目标
输入
全局原则
流程总览
第 0 步：受理
第 1 步：约束表
第 2 步：环境探测
第 3 步：用例和插件
第 4 步：生成与校验
第 5 步：资源预检
第 6 步：固化清单与部署
第 7 步：精度
第 8 步：性能
第 9 步：裁决与交付
交付物
Reference 路由
脚本路由
```

每一步只写输入、动作、产物和停止条件。

每一步只链接当前需要的 reference。

- [ ] **Step 3: 保留全局原则**

必须明确：

- 不修改待测物。
- 不在执行期修改量具。
- 不读取待测源码设计覆盖。
- 不读取待测源码补充精度归因。
- 推断约束必须确认。
- 数字和结论由脚本产生。
- 部署阻塞立即停止。
- 归因只使用报告和报错。

- [ ] **Step 4: 删除膨胀内容**

删除历史事故。

删除耗时数据。

删除重复 schema。

删除重复模板。

删除完整错误归因表。

删除静态阅读待测源码的动作。

- [ ] **Step 5: 检查行数和 reference 路由**

Run:

```bash
wc -l skill/repo-task-atk-test/SKILL.md
rg -o 'references/[^)]+' skill/repo-task-atk-test/SKILL.md | sort -u
```

Expected: 主 Skill 为 300 至 400 行。

Expected: 九个目标 reference 都能从主 Skill 直接发现。

- [ ] **Step 6: 提交主 Skill**

```bash
git add skill/repo-task-atk-test/SKILL.md
git commit -m "docs: rewrite ATK acceptance workflow"
```

## Task 9: 建立文本门禁并同步项目说明

**Files:**

- Create: `skill/repo-task-atk-test/tests/test_document_style.py`
- Modify: `CLAUDE.md`

- [ ] **Step 1: 实现文本门禁**

扫描 `SKILL.md` 和所有 Markdown reference。

忽略 fenced code block。

忽略 Markdown 表格。

忽略纯链接行。

检查以下条件：

```python
MAX_SKILL_LINES = 500
MAX_PROSE_CHARS = 100
MAX_SENTENCE_MARKS = 1
```

正文超过 100 个字符时报告文件和行号。

正文包含多个中文句末标点时报告文件和行号。

例外使用文件名和行号集合显式登记。

- [ ] **Step 2: 添加 reference 结构检查**

检查超过 100 行的 reference 是否包含目录。

检查 reference 是否链接其他 reference。

检查 `SKILL.md` 是否链接全部目标 reference。

检查旧 reference 名称是否残留。

- [ ] **Step 3: 同步 CLAUDE.md**

更新 reference 数量。

更新 reference 文件名。

修正 `make_repro.py` 未实现的陈旧状态。

保留现有 ATK 事实基线。

不重写整个文件。

- [ ] **Step 4: 运行文本门禁**

Run:

```bash
python3 -m unittest discover -s skill/repo-task-atk-test/tests \
  -p 'test_document_style.py' -v
```

Expected: 所有测试显示 `ok`。

- [ ] **Step 5: 提交文本门禁和说明同步**

```bash
git add skill/repo-task-atk-test/tests/test_document_style.py CLAUDE.md
git commit -m "test: guard ATK skill document structure"
```

## Task 10: 完整验证

**Files:**

- Verify: `skill/repo-task-atk-test/`
- Verify: `CLAUDE.md`

- [ ] **Step 1: 运行全部单元测试**

Run:

```bash
python3 -m unittest discover -s skill/repo-task-atk-test/tests -p 'test_*.py' -v
```

Expected: 全部测试显示 `ok`。

- [ ] **Step 2: 编译全部脚本**

Run:

```bash
python3 -m compileall -q skill/repo-task-atk-test/scripts
```

Expected: 退出码为 `0`。

- [ ] **Step 3: 运行 Skill 结构校验**

Run:

```bash
python3 /Users/justbin/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  skill/repo-task-atk-test
```

Expected: 输出 `Skill is valid!`。

- [ ] **Step 4: 核对脚本命令与 argparse**

逐条提取 `SKILL.md` 和 reference 中的 `scripts/*.py` 命令。

对每个命令运行 `--help`。

确认文档参数存在于对应 argparse 定义。

Expected: 不存在未知参数。

- [ ] **Step 5: 运行最小功能链**

使用临时目录创建两个用例的最小输入。

运行 `check_coverage.py`。

运行 `select_perf_cases.py`。

使用合成 `accuracy_results.json` 运行 `verdict.py`。

确认产物摘要一致时裁决成功。

修改 results 中的 case 摘要。

确认裁决拒绝不一致产物。

- [ ] **Step 6: 清理生成缓存**

删除验证生成的 `__pycache__`。

确认仓库内没有新增 `.pyc`。

- [ ] **Step 7: 检查工作区**

Run:

```bash
git status --short
git diff --check
```

Expected: 只出现本计划内文件。

Expected: `git diff --check` 无输出。

- [ ] **Step 8: 提交最终验证修正**

仅在验证阶段产生修正时执行：

```bash
git add CLAUDE.md skill/repo-task-atk-test
git commit -m "fix: complete ATK skill validation"
```

无修正时不创建空提交。
