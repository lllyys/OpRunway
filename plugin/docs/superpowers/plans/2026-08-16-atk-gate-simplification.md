# ATK 验收 skill 门禁精简与接口派生实施计划（Plan A）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删掉校验「脚本自己刚生成的东西」的门禁，把接口后端从三处声明改成 S1 派生一次，并给唯一没人管的基线插件补上门禁。

**Architecture:** 一条判据贯穿全篇——**门禁只校验 agent 写的东西和真机现实，不校验脚本确定性生成的产物**。据此删 3 个脚本、合 1 个、新增 1 个派生脚本。删除的检查凡仍有价值的，就近并进已经消费同一份数据的脚本，不新开门禁。

**Tech Stack:** Python 3（标准库），JSON，Markdown。无新增第三方依赖。

设计依据：`docs/superpowers/specs/2026-08-15-atk-knowledge-architecture-design.md`
证据来源：远端会话 `9e20e8c7`（roll 验收，15:16–16:18，484 次工具调用，末段 86 次全部耗在本计划要删掉的门禁上）

**执行顺序**：本计划先做，`2026-08-15-atk-contract-spine.md`（契约骨架）后做。
理由：骨架要登记的是**精简之后**的产物与量具清单；反过来做等于先登记再删除，白做一遍。

## Global Constraints

- 工作目录：`skill/repo-task-atk-test/`（下称 skill root）。所有相对路径以此为根。
- 测试命令：在 skill root 执行 `python3 -m pytest tests/ -q`。基线 268 passed, 9 skipped, 29 subtests。
- 测试写法：`unittest.TestCase`，`SKILL_ROOT = Path(__file__).resolve().parents[1]`；
  导入脚本时 `sys.path.insert(0, str(SKILL_ROOT / "scripts"))` 后按裸模块名导入。
- 文档、注释、字符串一律中文；代码标识符英文。
- 引用写成可点击路径，例如 `atk/tasks/backends/aclnn_backend.py:71`。
- 不改 `atk/` 源码。不写任何算子专属逻辑（无算子名、具体 shape、dtype 清单、专属阈值）。
- 删脚本时同步删它的测试文件；删检查时把仍有价值的那条移到新家并保留一条回归用例。
- 每个 Task 结束必须 `python3 -m pytest tests/ -q` 全绿再提交。
- 提交信息末尾附：`Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`

## 判据速查

动手前先对表，避免把该留的删了：

| 产物 | 谁写的 | 要不要门禁 |
| --- | --- | --- |
| `<op>_decl.json` | agent | 要 |
| `<op>_materialize.py` 的产物 | agent | 要 |
| `must_cover.json` | 脚本从 decl 生成 | **不要** |
| `<op>.yaml` | 脚本从契约生成 | **不要** |
| `<op>_constraint.py` | agent | 要 |
| `function_<op>.py` | agent | 要（本计划新增） |
| 用例 JSON | ATK 生成，受 agent 插件影响 | 要 |
| 冻结输入 | ATK 生成的真实数据 | 要 |
| 构建产物 / 绑定 / 冒烟 | 真机现实 | 要 |

---

## File Structure

| 文件 | 动作 | 说明 |
| --- | --- | --- |
| `scripts/derive_interface.py` | 新增 | 从 S1 结果派生 `evidence/interface.json`，不问用户 |
| `references/acceptance-policy.json` | 改 | aclnn 后端由二选一改为单值 |
| `references/atk-parameter-capabilities.json` | 改 | 新增 `backend_selection` 事实块 |
| `scripts/atk_lookup.py` | 改 | 新增 `backend_selection` 主题 |
| `scripts/check_atk_capabilities.py` | **删** | 校验的是脚本生成的 YAML |
| `scripts/_atk_capabilities.py` | **删** | 同上，仅被上者使用 |
| `scripts/make_yaml.py` | 改 | 接收 P4/P6/P7 三条仍有价值的检查 |
| `scripts/make_manifest.py` | **删** | 43 键里绝大多数是转抄 |
| `scripts/verdict.py` | 改 | 直接读产物与 `interface.json`，不再读 manifest |
| `scripts/make_repro.py` | 改 | 同上 |
| `scripts/check_adapter_repair.py` | **删** | 纯记账，不阻塞任何东西 |
| `scripts/check_input_budget.py` | **删** | 并进 `freeze_inputs.py` |
| `scripts/freeze_inputs.py` | 改 | 接收字节预算；新增基线执行核验 |
| `references/*.md`、`SKILL.md` | 改 | 门禁数量与命令模板同步 |
| `tests/` | 改 | 删对应测试，新增 4 个测试文件 |

---

## Task 1: 接口事实 S1 派生一次

问题：同一个「用哪个执行后端」的事实，agent 要在 S2 的 `--backend`、S3 的 `--execution-backend`、
S4 的 verdict 比对三处独立声明，而 skill 说 aclnn 与 pyaclnn 都行。
roll 因此在 S4 卡死：声明 aclnn，实测 pyaclnn。

事实是它根本不该是选择题（源码依据写进 L0）。

**Files:**
- Modify: `references/acceptance-policy.json`
- Modify: `references/atk-parameter-capabilities.json`
- Modify: `scripts/atk_lookup.py`
- Create: `scripts/derive_interface.py`
- Create: `tests/test_derive_interface.py`

**Interfaces:**
- Produces：
  - `derive_interface.py --mode <aclnn|pytorch|kernel> --candidate <符号> --baseline <符号> --mode-source <出处> -o evidence/interface.json`
  - `derive_interface.BACKEND_BY_MODE: dict[str, str]` — 接口模式 → 执行后端，唯一确定
  - `evidence/interface.json` 的键：`interface_mode`、`candidate_symbol`、`baseline_api`、
    `execution_backend`、`baseline_backend`、`mode_source`、`schema_version`
- 后续 Task 消费：Task 3 的 `verdict.py`、`make_repro.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_derive_interface.py`：

```python
"""接口事实由 S1 派生一次，后面只读不再声明。

roll 那轮在 S4 卡死：manifest 声明 execution_backend=aclnn，报告实测 pyaclnn。
成因不是填错，是 skill 把一个可推导的事实做成了选择题——
acceptance-policy 里 aclnn 的 backends 写着 ["pyaclnn", "aclnn"]，
agent 没有任何依据知道该填哪个。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import derive_interface  # noqa: E402

SCRIPT = SKILL_ROOT / "scripts" / "derive_interface.py"


class BackendDerivationTest(unittest.TestCase):
    def test_aclnn_interface_always_runs_on_pyaclnn(self):
        # AclnnBackend 要 aclnnTest C++ 扩展并逐算子绑定，社区算子验收用不了。
        self.assertEqual(derive_interface.BACKEND_BY_MODE["aclnn"], "pyaclnn")

    def test_every_open_mode_maps_to_exactly_one_backend(self):
        for mode, backend in derive_interface.BACKEND_BY_MODE.items():
            with self.subTest(mode=mode):
                self.assertIsInstance(backend, str)
                self.assertTrue(backend)

    def test_policy_no_longer_offers_a_choice(self):
        policy = json.loads(
            (SKILL_ROOT / "references" / "acceptance-policy.json")
            .read_text(encoding="utf-8"))
        for mode, block in policy["interface_modes"].items():
            if not block.get("acceptance_enabled"):
                continue
            with self.subTest(mode=mode):
                self.assertEqual(len(block["backends"]), 1,
                                 f"{mode} 仍是选择题，agent 无从判断该填哪个")

    def test_policy_and_script_agree(self):
        policy = json.loads(
            (SKILL_ROOT / "references" / "acceptance-policy.json")
            .read_text(encoding="utf-8"))
        for mode, block in policy["interface_modes"].items():
            if not block.get("acceptance_enabled"):
                continue
            with self.subTest(mode=mode):
                self.assertEqual(derive_interface.BACKEND_BY_MODE[mode],
                                 block["backends"][0])


class DeriveCliTest(unittest.TestCase):
    def _run(self, *args):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "interface.json"
            result = subprocess.run(
                [sys.executable, str(SCRIPT), *args, "-o", str(out)],
                capture_output=True, text=True)
            payload = json.loads(out.read_text(encoding="utf-8")) \
                if out.exists() else None
            return result, payload

    def test_derives_backend_without_asking(self):
        result, payload = self._run(
            "--mode", "aclnn", "--candidate", "aclnnRoll",
            "--baseline", "torch.roll", "--mode-source", "任务书 §2")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(payload["execution_backend"], "pyaclnn")
        self.assertEqual(payload["baseline_backend"], "cpu")
        self.assertEqual(payload["interface_mode"], "aclnn")

    def test_candidate_and_baseline_must_differ(self):
        result, _ = self._run(
            "--mode", "aclnn", "--candidate", "torch.roll",
            "--baseline", "torch.roll", "--mode-source", "任务书 §2")
        self.assertEqual(result.returncode, 2)
        self.assertIn("同一个符号", result.stderr)

    def test_closed_mode_is_refused(self):
        result, _ = self._run(
            "--mode", "triton", "--candidate", "x",
            "--baseline", "torch.x", "--mode-source", "任务书 §2")
        self.assertEqual(result.returncode, 2)
        self.assertIn("暂停验收", result.stderr)

    def test_mode_source_is_required(self):
        result, _ = self._run(
            "--mode", "aclnn", "--candidate", "aclnnRoll",
            "--baseline", "torch.roll", "--mode-source", "")
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_derive_interface.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'derive_interface'`

- [ ] **Step 3: 改 policy，把选择题变成推导**

`references/acceptance-policy.json` 里 aclnn 那段：

```json
    "aclnn": {
      "backends": ["pyaclnn", "aclnn"],
```

改成：

```json
    "aclnn": {
      "backends": ["pyaclnn"],
```

其余模式已是单值，不动。

- [ ] **Step 4: 把后端推导写进事实层**

`references/atk-parameter-capabilities.json` 顶层新增 `backend_selection` 块
（放在 `backends` 之后）：

```json
  "backend_selection": {
    "aclnn": {
      "execution_backend": "pyaclnn",
      "baseline_backend": "cpu",
      "why": "AclnnBackend 启动时先 import aclnnTest，缺了直接 RuntimeError；还要求每个算子在 aclnn_test/inc/Bind.h 里绑定过。社区算子验收没有这层 C++ 工程，所以固定走 pyaclnn。",
      "evidence": [
        "atk/tasks/backends/aclnn_backend.py:71-86",
        "atk/tests/nodes_accuracy_on_aclnn.yaml:2",
        "atk/tests/nodes_aclnn_Accu.yaml:2"
      ]
    },
    "pytorch": {
      "execution_backend": "npu",
      "baseline_backend": "cpu",
      "why": "候选是 torch 侧算子，走 npu 后端；基线在 cpu 上算 golden。",
      "evidence": ["atk/configs/nodetype_config.py:24-25"]
    },
    "kernel": {
      "execution_backend": "kernel",
      "baseline_backend": "cpu",
      "why": "kernel 模式有独立后端，基线仍在 cpu。",
      "evidence": ["atk/tasks/backends/kernel_backend.py:35"]
    },
    "not_in_scope": {
      "aclnn_native": "atk node -b aclnn 走 AclnnBackend，需要为每个算子写 C++ 绑定并编译 aclnnTest，不在本 skill 范围",
      "triton": "暂停验收",
      "atb": "暂停验收"
    }
  },
```

- [ ] **Step 5: 让 `atk_lookup.py` 能查到它**

`scripts/atk_lookup.py` 的 `TOPICS` 字典里加一行（放在 `"backends"` 那行之后）：

```python
    "backend_selection": (None, "backend_selection", "接口模式 → 执行后端，唯一推导不是选择"),
```

- [ ] **Step 6: 写派生脚本**

创建 `scripts/derive_interface.py`：

```python
"""从 S1 的接口确认结果派生执行后端，一次定死，后面只读。

以前这件事要 agent 在三处独立声明：S2 的能力检查 --backend、S3 的 manifest
--execution-backend、S4 的 verdict 比对。三处任一处不一致就炸，而 skill 写着
「aclnn → pyaclnn 或 aclnn」，agent 没有依据知道该填哪个——roll 那轮就卡死在这。

它本来就不是选择题：AclnnBackend 要 aclnnTest C++ 扩展并逐算子绑定
（atk/tasks/backends/aclnn_backend.py:71-86），社区算子验收没有这层工程；
ATK 自带的两份 aclnn 精度节点配置写的也都是 pyaclnn + cpu。

退出码：0 完成；2 输入不成立（模式未开放、候选与基线同符号、缺出处）。
"""

import argparse
import json
import sys
from pathlib import Path

from _policy import load_policy

# 接口模式 → 执行后端。真源是 references/acceptance-policy.json，
# 这里的常量由 tests/test_derive_interface.py 钉住与它一致。
BACKEND_BY_MODE = {
    "aclnn": "pyaclnn",
    "pytorch": "npu",
    "kernel": "kernel",
}

BASELINE_BACKEND = "cpu"


def derive(mode, candidate, baseline, mode_source, policy):
    block = (policy.get("interface_modes") or {}).get(mode)
    if block is None:
        raise ValueError(f"接口模式 {mode!r} 不在验收政策里")
    if not block.get("acceptance_enabled"):
        raise ValueError(f"接口模式 {mode!r} 暂停验收，不出结论")
    if candidate == baseline:
        raise ValueError(
            f"候选与基线填了同一个符号 {candidate!r}；"
            "两边同源时精度比对恒过，测不出任何东西")
    if not (mode_source or "").strip():
        raise ValueError("必须写明接口模式的出处，否则结论不可追溯")
    return {
        "schema_version": 1,
        "interface_mode": mode,
        "candidate_symbol": candidate,
        "baseline_api": baseline,
        "execution_backend": BACKEND_BY_MODE[mode],
        "baseline_backend": BASELINE_BACKEND,
        "mode_source": mode_source.strip(),
    }


def main():
    parser = argparse.ArgumentParser(
        description="从 S1 的接口确认结果派生执行后端，产出 evidence/interface.json")
    parser.add_argument("--mode", required=True,
                        help="任务书说候选是什么接口：aclnn / pytorch / kernel")
    parser.add_argument("--candidate", required=True, help="候选符号，如 aclnnRoll")
    parser.add_argument("--baseline", required=True, help="基线符号，如 torch.roll")
    parser.add_argument("--mode-source", required=True,
                        help="接口模式的出处，如「任务书 §2 + S1 用户确认」")
    parser.add_argument("-o", "--output", default="evidence/interface.json")
    args = parser.parse_args()

    try:
        payload = derive(args.mode, args.candidate, args.baseline,
                         args.mode_source, load_policy())
    except ValueError as exc:
        print(f"接口事实不成立：{exc}", file=sys.stderr)
        return 2

    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print(f"接口模式 {payload['interface_mode']} → 执行后端 "
          f"{payload['execution_backend']}，基线节点 {payload['baseline_backend']}")
    print(f"写入 {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 7: 跑测试确认通过**

Run: `python3 -m pytest tests/test_derive_interface.py -q`
Expected: PASS，8 passed

- [ ] **Step 8: 改 SKILL.md 的接口决策段**

把「### 接口与基线」小节里这一行：

```markdown
接口模式和执行后端分别确认：`pytorch → npu`、`aclnn → pyaclnn/aclnn`、`kernel → kernel`；`triton` 和 `atb` 暂停验收。
```

替换为：

```markdown
执行后端由接口模式唯一推导，不是选择题，S1 跑 `derive_interface.py` 一次定死。

`aclnn → pyaclnn`、`pytorch → npu`、`kernel → kernel`，基线节点恒为 `cpu`；`triton` 和 `atb` 暂停验收。

`atk node -b aclnn` 要为每个算子写 C++ 绑定并编译 aclnnTest，不在本 skill 范围，不要用它。
```

- [ ] **Step 9: 跑全量**

Run: `python3 -m pytest tests/ -q`
Expected: 276 passed, 9 skipped

- [ ] **Step 10: 提交**

```bash
git add skill/repo-task-atk-test/scripts/derive_interface.py \
        skill/repo-task-atk-test/scripts/atk_lookup.py \
        skill/repo-task-atk-test/references/acceptance-policy.json \
        skill/repo-task-atk-test/references/atk-parameter-capabilities.json \
        skill/repo-task-atk-test/SKILL.md \
        skill/repo-task-atk-test/tests/test_derive_interface.py
git commit -m "$(cat <<'EOF'
feat: derive the execution backend once in S1

执行后端本来就不是选择题：AclnnBackend 要 aclnnTest C++ 扩展并逐算子
绑定，社区算子验收没有这层工程；ATK 自带的两份 aclnn 精度节点配置写的
也都是 pyaclnn + cpu。

acceptance-policy 里 aclnn 的 backends 从 ["pyaclnn","aclnn"] 收成
单值，agent 不再需要在三处各声明一遍——roll 那轮正是在 S4 才发现
声明的 aclnn 与实测的 pyaclnn 对不上。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: 删掉校验脚本产物的能力检查

`check_atk_capabilities.py` 校验的是 `<op>.yaml`——由 `make_yaml.py` 从契约确定性生成的东西。

21 条检查的实际状态：11 条 skill 自己的测试就写明结构上不可能触发
（`tests/test_make_yaml.py:4`）；P5/P8/P14 `make_yaml` 已经拦了；
P3/P15/P16 的 dtype 白名单数据是错的（P3 判 pyaclnn 不能用 int64_t 组数组，
而 roll 的 159 条用例刚用 int64_t 跑通）；C0 与 `check_coverage.py` 调的是同一个
`audit_coverage`。只剩 P4/P6/P7 有意义，移进 `make_yaml.py`。

**Files:**
- Delete: `scripts/check_atk_capabilities.py`、`scripts/_atk_capabilities.py`
- Delete: `tests/test_atk_capabilities.py`
- Modify: `scripts/make_yaml.py`（接收 P4/P6/P7）
- Modify: `tests/test_make_yaml.py`（新增三条回归）
- Modify: `references/case-design.md`、`SKILL.md`（门禁 5 道改 3 道）

**Interfaces:**
- Produces：`make_yaml._check_semantic_axes(must_cover, contracts) -> list[str]`，
  返回问题列表，并入 `build_design` 已有的攒齐报错机制

- [ ] **Step 1: 写失败测试**

在 `tests/test_make_yaml.py` 的测试类里追加：

```python
    def test_axis_pointing_at_a_missing_attr_is_reported(self):
        must_cover = {
            "yaml": dict(HEADER),
            "parameters": {"x": {"element_kind": "tensor",
                                 "runtime_container": "single"}},
            "axes": ["shift"],
            "extract": {"shift": {"from": "attr", "name": "shifts"}},
            "combos": [{"dtype": "fp32", "shape": [2, 2], "shift": [1]}],
        }
        with self.assertRaises(DeclarationError) as caught:
            build_design(must_cover)
        self.assertIn("shifts", str(caught.exception))

    def test_mixing_sequence_and_scalar_semantics_is_reported(self):
        must_cover = {
            "yaml": dict(HEADER),
            "parameters": {
                "x": {"element_kind": "tensor", "runtime_container": "single"},
                "shifts": {"element_kind": "attr", "runtime_container": "list",
                           "dtype": "int64_t", "combo_key": "shift"},
            },
            "axes": ["shift"],
            "extract": {"shift": {"from": "attr", "name": "shifts",
                                  "runtime_container": "list"}},
            "combos": [{"dtype": "fp32", "shape": [2, 2], "shift": [1]},
                       {"dtype": "fp32", "shape": [2, 2], "shift": 3}],
        }
        with self.assertRaises(DeclarationError) as caught:
            build_design(must_cover)
        self.assertIn("拆分接口分面", str(caught.exception))

    def test_sequence_axis_without_runtime_container_is_reported(self):
        must_cover = {
            "yaml": dict(HEADER),
            "parameters": {
                "x": {"element_kind": "tensor", "runtime_container": "single"},
                "shifts": {"element_kind": "attr", "runtime_container": "list",
                           "dtype": "int64_t", "combo_key": "shift"},
            },
            "axes": ["shift"],
            "extract": {"shift": {"from": "attr", "name": "shifts"}},
            "combos": [{"dtype": "fp32", "shape": [2, 2], "shift": [1]}],
        }
        with self.assertRaises(DeclarationError) as caught:
            build_design(must_cover)
        self.assertIn("runtime_container", str(caught.exception))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_make_yaml.py -q`
Expected: FAIL，三条新用例都没有抛 `DeclarationError`

- [ ] **Step 3: 把 P4/P6/P7 移进 `make_yaml.py`**

在 `scripts/make_yaml.py` 的 `build_design` 之前插入：

```python
def _semantic_kind(value):
    """一个 combo 取值在运行期会 lowering 成什么形态。"""
    if value is None or value == DEFAULT_TOKEN:
        return "none"
    if isinstance(value, (list, tuple)):
        return "empty_sequence" if len(value) == 0 else "sequence"
    return "scalar"


def _check_semantic_axes(must_cover, contracts):
    """校验语义轴、extract 规则与参数契约三者自洽。

    这三样都是 agent 手写的，所以要查；YAML 是本脚本从契约生成的，不查。
    原先这三条挂在 check_atk_capabilities 上，而那个脚本查的是生成物，
    21 条里 11 条结构上已经不可能触发、3 条数据是错的，整体删除。
    """
    problems = []
    axes = must_cover.get("axes") or []
    extract = must_cover.get("extract") or {}
    combos = must_cover.get("combos") or []
    for axis in axes:
        rule = extract.get(axis) or {}
        if rule.get("from") != "attr":
            continue
        name = rule.get("name")
        if name not in contracts:
            problems.append(
                f"语义轴 {axis!r} 的 extract 指向 {name!r}，"
                "但 parameters 里没有这个契约")
            continue
        kinds = {_semantic_kind(combo.get(axis)) for combo in combos}
        if "sequence" in kinds and ("none" in kinds or "scalar" in kinds):
            problems.append(
                f"{name!r} 在同一设计里混合 {sorted(kinds)}；"
                "单个输入不能既是平面 attr 又是复合 attr，请拆分接口分面")
            continue
        if "sequence" in kinds and \
                rule.get("runtime_container") not in {"list", "tuple"}:
            problems.append(
                f"{name!r} 是序列语义，extract 必须写 "
                "runtime_container=list 或 tuple")
    return problems
```

在 `build_design` 里，找到攒齐报错前的这一段：

```python
    names = [config["name"] for config in channels["inputs"]]
```

在它之前插入：

```python
    problems.extend(_check_semantic_axes(must_cover, contracts))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_make_yaml.py -q`
Expected: PASS

- [ ] **Step 5: 删脚本与测试**

```bash
cd skill/repo-task-atk-test
git rm scripts/check_atk_capabilities.py scripts/_atk_capabilities.py \
       tests/test_atk_capabilities.py
```

- [ ] **Step 6: 清掉残留引用**

```bash
grep -rn "check_atk_capabilities\|_atk_capabilities\|atk_capabilities.json" \
     scripts/ references/ SKILL.md tests/ | grep -v Binary
```

预期命中 `scripts/make_manifest.py`（Task 3 会整体删除，本步不动）、
`references/case-design.md`、`SKILL.md`。把后两者里的能力门禁改掉：

`SKILL.md` 阶段流程表 S2 行的出口门禁：

```markdown
| S2 用例生成 | 能力、签名、结构、覆盖、预算五道校验全过 |
```

改为：

```markdown
| S2 用例生成 | 签名、结构、覆盖三道校验全过 |
```

（预算那道在 Task 5 并进冻结，本步一并去掉；`references/case-design.md` 里
提到能力门禁与 `-o evidence/atk_capabilities.json` 的段落整段删除。）

- [ ] **Step 7: 跑全量**

Run: `python3 -m pytest tests/ -q`
Expected: 259 passed, 9 skipped（原 276 −20 能力测试 +3 新回归）

- [ ] **Step 8: 提交**

```bash
git add -A skill/repo-task-atk-test
git commit -m "$(cat <<'EOF'
refactor: drop the capability gate that checked a generated file

check_atk_capabilities 校验的是 make_yaml 从契约确定性生成的 YAML——
脚本自己刚生成的东西不需要再校验。

21 条检查的实际状态：11 条 skill 自己的测试就写明结构上不可能触发，
P5/P8/P14 make_yaml 已经拦了，C0 与 check_coverage 调同一个函数，
P3/P15/P16 的 dtype 白名单是错的——P3 判 pyaclnn 不能用 int64_t 组
数组，而 roll 的 159 条用例刚用 int64_t 跑通，它差点否决一次已经
成功的验收。

仍有价值的 P4/P6/P7 校验的是 agent 手写的语义轴与 extract 规则，
移进 make_yaml 的攒齐报错机制。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: 删掉 manifest，裁决直接读产物

`make_manifest.py` 43 个键里绝大多数是转抄：`env` 来自 `env.json`、`policy` 来自政策文件、
`coverage_policy`/`case_count` 来自 coverage 报告。它独有的只有产物 sha256。

真正的害处是 4 个手工接口参数——S1 已经确认过的事实要 agent 在 S3 重新声明一遍，
于是有机会声明得不一样，到 S4 才发现。

`--mode A/B/C` 同样是过度设计：模式 A 被 verdict 直接拒绝，模式 C 要额外声明，
而本流程结构上只可能是 B（组合由枚举表钉死）。它在 reference 里只出现在
`make_manifest` 的命令行上，语义无处可查。覆盖保证现在由「必测集命中 100%」证明，
不由自我声明的字母证明。

**Files:**
- Delete: `scripts/make_manifest.py`
- Modify: `scripts/verdict.py`
- Modify: `scripts/make_repro.py`
- Modify: `tests/test_verdict.py`
- Modify: `references/execution.md`、`references/reporting.md`、`SKILL.md`

**Interfaces:**
- Consumes：Task 1 的 `evidence/interface.json`
- Produces：`verdict.py` 的新参数
  `--interface <interface.json> -m <must_cover.json> -j <case-json> --coverage <coverage.json> -i <accuracy_results.json>`；
  产出 `verdict.json` 新增 `artifact_sha256` 字段，键为产物路径、值为 sha256

- [ ] **Step 1: 写失败测试**

在 `tests/test_verdict.py` 的测试类里追加：

```python
    def test_backend_mismatch_is_still_caught_without_a_manifest(self):
        # roll 那轮 manifest 声明 aclnn、实测 pyaclnn，这条门禁抓到了真问题，
        # 必须保留。区别只是现在声明侧来自 S1 派生，不一致就只可能是真跑错了。
        interface = {"schema_version": 1, "interface_mode": "aclnn",
                     "candidate_symbol": "aclnnRoll", "baseline_api": "torch.roll",
                     "execution_backend": "pyaclnn", "baseline_backend": "cpu",
                     "mode_source": "任务书 §2"}
        results = {"backends": ["cpu", "npu"], "task": "accuracy"}
        with self.assertRaises(verdict.GateFailure) as caught:
            verdict.check_interface(interface, results, verdict.load_policy())
        self.assertIn("INTERFACE_BACKEND", str(caught.exception))

    def test_matching_backend_passes(self):
        interface = {"schema_version": 1, "interface_mode": "aclnn",
                     "candidate_symbol": "aclnnRoll", "baseline_api": "torch.roll",
                     "execution_backend": "pyaclnn", "baseline_backend": "cpu",
                     "mode_source": "任务书 §2"}
        results = {"backends": ["cpu", "pyaclnn"], "task": "accuracy"}
        verdict.check_interface(interface, results, verdict.load_policy())

    def test_artifact_sha_is_computed_not_transcribed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "must_cover.json"
            target.write_text('{"combos": []}', encoding="utf-8")
            digests = verdict.artifact_digests([str(target)])
        self.assertIn(str(target), digests)
        self.assertEqual(len(digests[str(target)]), 64)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_verdict.py -q`
Expected: FAIL，`AttributeError: module 'verdict' has no attribute 'check_interface'`

- [ ] **Step 3: 用 `check_interface` 替换 `check_interface_mode`**

`scripts/verdict.py` 里整段替换 `check_interface_mode`：

```python
def check_interface(interface, results, policy):
    """接口事实来自 S1 派生，这里只核它与真机跑测对不对得上。

    以前这些字段是 agent 在 S3 手工重新声明的，于是「声明填错」和
    「真的跑错后端」两类问题混在一起，roll 那轮为此耗了 86 次调用。
    现在声明侧是推导的，对不上就只可能是真跑错了。
    """
    mode = interface.get("interface_mode")
    block = (policy.get("interface_modes") or {}).get(mode)
    if block is None:
        raise GateFailure(f"[INTERFACE_MODE] 未知接口模式 {mode!r}。")
    if not block.get("acceptance_enabled"):
        raise GateFailure(f"[INTERFACE_DISABLED] 接口模式 {mode} 尚未开放验收。")
    if not interface.get("baseline_api"):
        raise GateFailure("[BASELINE_API] interface.json 缺少 baseline_api。")
    if not interface.get("mode_source"):
        raise GateFailure("[MODE_SOURCE] interface.json 缺少接口模式依据。")

    actual = [name for name in (results.get("backends") or [])]
    if not actual:
        raise GateFailure("[INTERFACE_EVIDENCE] 报告中没有可识别的实际后端。")
    expected = interface.get("execution_backend")
    if expected not in actual:
        raise GateFailure(
            f"[INTERFACE_BACKEND] 本轮应在 {expected} 上执行，报告实测 {actual}。\n"
            "  → 接口后端由 S1 派生，对不上说明跑测命令的 -b 用错了后端，"
            "不是声明写错。")
    if interface.get("baseline_backend") not in actual:
        raise GateFailure(
            f"[BASELINE_BACKEND] 缺少基线节点 "
            f"{interface.get('baseline_backend')!r} 的结果，无从比对。")
```

删除 `check_mode` 整个函数，以及它在 `main()` 里的调用。

- [ ] **Step 4: 加 `artifact_digests`，替换 manifest 的 sha 交叉核对**

在 `scripts/verdict.py` 的 `GateFailure` 之后插入：

```python
def artifact_digests(paths):
    """出结论时现算产物 sha，不再依赖 manifest 里的转抄值。

    转抄的摘要只能证明「manifest 和产物一致」，现算的摘要证明
    「结论和产物一致」——后者才是结论要担保的事。
    """
    digests = {}
    for path in paths:
        if not path:
            continue
        digests[path] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return digests
```

在文件顶部 import 段补 `import hashlib` 与 `from pathlib import Path`（若尚无）。

把 `check_artifact_integrity(manifest, coverage, results)` 整体替换为：

```python
def check_artifact_integrity(coverage, results, case_json, must_cover):
    """核对 coverage 与 results 说的是同一份用例集和必测集。

    以前这条要绕道 manifest 的转抄摘要，现在直接比对两份报告自身记录的
    产物摘要，少一层中间人也就少一处能填错的地方。
    """
    if results.get("task") != "accuracy":
        raise GateFailure("[RESULT_TASK] 精度裁决只能消费 accuracy 报告。")
    case_sha = hashlib.sha256(Path(case_json).read_bytes()).hexdigest()
    must_sha = hashlib.sha256(Path(must_cover).read_bytes()).hexdigest()
    if coverage.get("case_json_sha256") not in (None, case_sha):
        raise GateFailure("[ARTIFACT_COVERAGE_CASE] coverage 不是当前用例集的。")
    if coverage.get("must_cover_sha256") not in (None, must_sha):
        raise GateFailure("[ARTIFACT_COVERAGE_MUST] coverage 不是当前必测集的。")
    if results.get("total") is not None and coverage.get("case_total") is not None \
            and results["total"] != coverage["case_total"]:
        raise GateFailure("[ARTIFACT_COUNT] results 与 coverage 的用例数量不一致。")
```

`check_case_set` 与 `check_coverage` 里凡取 `manifest.get("must_cover_size")` 的，
改成从 `must_cover.json` 现读 `len(must_cover["combos"])`。

`main()` 的参数：删 `--manifest`，加

```python
    parser.add_argument("--interface", required=True,
                        help="derive_interface.py 产出的 evidence/interface.json")
    parser.add_argument("-m", "--must-cover", required=True)
    parser.add_argument("-j", "--case-json", required=True)
```

并在写出 `verdict.json` 时加入：

```python
    conclusion["artifact_sha256"] = artifact_digests(
        [args.case_json, args.must_cover, args.coverage, args.input])
```

- [ ] **Step 5: 改 `make_repro.py`**

`build_readme(manifest, ...)` 改签名为 `build_readme(interface, verdict_json, ...)`，
里面取值改成：

```python
    interface_mode = interface.get("interface_mode") or "未声明"
    backend = interface.get("execution_backend") or "未声明"
    baseline = interface.get("baseline_api") or "未声明"
    mode_source = interface.get("mode_source") or "未声明"
    env = verdict_json.get("env", {})
```

`main()` 的 `--manifest` 参数改为 `--interface` 与 `--verdict`。

- [ ] **Step 6: 删脚本**

```bash
cd skill/repo-task-atk-test && git rm scripts/make_manifest.py
grep -rn "make_manifest\|manifest" scripts/ references/ SKILL.md tests/ | grep -v Binary
```

`references/execution.md` 的「## Manifest」整节删除；
`references/reporting.md`、`SKILL.md` 里提到 manifest 的行改为指向 `interface.json` 与
`verdict.json`；`SKILL.md` 阶段流程表 S3 行的冻结产物去掉 manifest。

`select_perf_cases.py --manifest` 是它自己的选样过程记录，与验收 manifest 无关，不动。

- [ ] **Step 7: 跑测试确认通过**

Run: `python3 -m pytest tests/ -q`
Expected: 254 passed, 9 skipped

- [ ] **Step 8: 提交**

```bash
git add -A skill/repo-task-atk-test
git commit -m "$(cat <<'EOF'
refactor: delete the manifest and let the verdict read artifacts directly

manifest 43 个键里绝大多数是转抄：env 来自 env.json、policy 来自政策
文件、coverage 数字来自 coverage 报告。独有的只有产物摘要，而转抄的
摘要只能证明「manifest 与产物一致」，现算的摘要才证明「结论与产物一致」。

真正的害处是 4 个手工接口参数：S1 已确认的事实要在 S3 重新声明一遍，
于是「声明填错」和「真的跑错后端」混成一类问题，roll 那轮为此耗了
86 次调用还没解决。

--mode A/B/C 一并删除：模式 A 被 verdict 直接拒绝，本流程结构上只可能
是 B，而它的语义在 reference 里无处可查。覆盖保证由必测集命中 100%
证明，不由自我声明的字母证明。

保留了唯一有价值的那条门禁：报告实测后端必须与 S1 派生的后端一致。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: 删掉适配器修正记账

`check_adapter_repair.py` 不阻塞任何东西——agent 不跑它照样往下走，
roll 那轮跑了一次记了个 `verified` 就没了下文。

「适配器只许基于公开证据修一次」是一条纪律，正文写一句就够，
不需要一个脚本 + 一个证据文件 + 一道门禁。

**Files:**
- Delete: `scripts/check_adapter_repair.py`、`tests/test_adapter_repair.py`
- Modify: `SKILL.md`、`references/plugin-authoring.md`、`assets/example/aclnn_executor.py`

- [ ] **Step 1: 删脚本与测试**

```bash
cd skill/repo-task-atk-test
git rm scripts/check_adapter_repair.py tests/test_adapter_repair.py
grep -rn "check_adapter_repair" scripts/ references/ SKILL.md assets/ tests/
```

- [ ] **Step 2: 把纪律写回正文**

`SKILL.md` 的「### 适配器」小节里这两行：

```markdown
签名不匹配时只允许基于公开证据最多修正一次适配器。
必须运行 `check_adapter_repair.py` 留痕。
```

替换为：

```markdown
签名不匹配时只允许基于公开证据修正一次适配器，改动写进证据目录。

第二次仍失败就停下出阻塞报告，不要枚举常量、空指针或参数顺序试错。
```

`references/plugin-authoring.md` 与 `assets/example/aclnn_executor.py` 里提到
`check_adapter_repair.py` 的句子作同样替换，去掉脚本名，保留纪律。

- [ ] **Step 3: 跑全量**

Run: `python3 -m pytest tests/ -q`
Expected: 248 passed, 9 skipped

- [ ] **Step 4: 提交**

```bash
git add -A skill/repo-task-atk-test
git commit -m "$(cat <<'EOF'
refactor: drop the adapter-repair bookkeeping script

它不阻塞任何东西——agent 不跑照样往下走，roll 那轮跑了一次记了个
verified 就没有下文。「只许基于公开证据修一次」是纪律，正文一句话
就够，不需要一个脚本加一个证据文件加一道门禁。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: 预算并进冻结，并给基线插件补上门禁

两件事合成一个 Task，因为动的是同一个脚本、同一次跑测的同一份日志。

**其一**：`check_input_budget.py` 默认预算 2 GiB，roll 实测最大 8 MB——差 250 倍，
这道门禁从没拦下过任何东西，却占一个脚本、一道门禁、一次调用。
`freeze_inputs.py` 本来就要把所有输入落盘，它最清楚总字节数。

**其二**：`function_<op>.py` 是 agent 手写的，却没有任何门禁。roll 那轮冻结时
基线**已经全线失败**，`freeze_inputs` 只看输入落盘就放行，错误推到 S3 冒烟才炸，
S4 全量又炸 42/81。而失败信息就在它已经抓到手的 `log` 里。

**Files:**
- Delete: `scripts/check_input_budget.py`、`tests/test_input_budget.py`
- Modify: `scripts/freeze_inputs.py`
- Create: `tests/test_freeze_gates.py`
- Modify: `references/execution.md`、`references/case-design.md`、`SKILL.md`

**Interfaces:**
- Produces：
  - `freeze_inputs.py --budget-bytes N`（默认 2 GiB，语义与原 `check_input_budget.py` 一致）
  - `freeze_inputs.baseline_failures(log) -> list[str]` — 从跑测日志抓基线节点失败的用例号
  - `freeze_a.json` 新增两个键：`total_input_bytes`、`baseline_failed_cases`
  - 退出码：常量张量与基线失败共用 2；预算超标共用 2；无法计算仍是 3

- [ ] **Step 1: 写失败测试**

创建 `tests/test_freeze_gates.py`：

```python
"""冻结阶段的两道门禁：基线真的跑通了吗、输入总量在预算内吗。

基线插件是 agent 手写的，却一直没有任何门禁。roll 那轮冻结时基线已经
全线失败，freeze_inputs 只看输入落盘就放行，错误推到 S3 冒烟才炸、
S4 全量又炸 42/81——而失败信息当时就在它已经抓到手的日志里。
"""

import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import freeze_inputs  # noqa: E402

FAILING_LOG = """
[2026-08-15 15:47:36] [INFO] [ForkPoolWorker-3] [opp_tasks.py:370]  [case 18] start run CPU OPP TASK.
[2026-08-15 15:47:36] [ERROR] [ForkPoolWorker-3] [opp_tasks.py:390]  case 18 run opp failed: torch.roll run error!
[2026-08-15 15:47:37] [ERROR] [ForkPoolWorker-3] [opp_tasks.py:390]  case 42 run opp failed: torch.roll run error!
"""

CLEAN_LOG = """
[2026-08-15 15:47:36] [INFO] [ForkPoolWorker-3] [opp_tasks.py:370]  [case 18] start run CPU OPP TASK.
[2026-08-15 15:47:36] [INFO] [ForkPoolWorker-3] [opp_executor.py:76]  [case 18] SUCCESS Execute OPP
"""


class BaselineGateTest(unittest.TestCase):
    def test_failing_baseline_cases_are_collected(self):
        self.assertEqual(freeze_inputs.baseline_failures(FAILING_LOG),
                         ["18", "42"])

    def test_clean_log_yields_no_failures(self):
        self.assertEqual(freeze_inputs.baseline_failures(CLEAN_LOG), [])

    def test_budget_module_is_gone(self):
        # 预算并进冻结之后，独立脚本不该再回来。
        self.assertFalse((SKILL_ROOT / "scripts" / "check_input_budget.py").exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_freeze_gates.py -q`
Expected: FAIL，`AttributeError: module 'freeze_inputs' has no attribute 'baseline_failures'`

- [ ] **Step 3: 实现基线失败抓取**

在 `scripts/freeze_inputs.py` 的 `constant_tensors` 之后插入：

```python
BASELINE_FAILURE = re.compile(r"case (\d+) run opp failed")


def baseline_failures(log):
    """从冻结那次跑测的日志里挑出基线执行失败的用例号。

    冻结用的是 CPU 单节点，跑的就是 agent 写的基线插件。它挂了却让
    冻结通过，等于把「基线插件写错了」这件事推迟到 S3 冒烟甚至 S4 全量
    才暴露——roll 那轮正是这样炸掉 42/81 条。
    """
    return sorted(set(BASELINE_FAILURE.findall(log)), key=int)
```

- [ ] **Step 4: 接上门禁与预算**

在 `scripts/freeze_inputs.py` 组装 `report` 的地方，把字典改成：

```python
    total_bytes = sum(item["bytes"] for item in frozen.values())
    failed_baseline = baseline_failures(log)

    report = {
        "case_json": args.case_json,
        "case_json_sha256": sha256_bytes(args.case_json),
        "frozen_dir": os.path.abspath(args.frozen_dir),
        "total": len(wanted),
        "materialized": len(frozen),
        "unmaterialized_ids": missing,
        "inputs": frozen,
        "total_input_bytes": total_bytes,
        "constant_input_cases": degenerate,
        "baseline_failed_cases": failed_baseline,
        "smoke_case": pick_smoke_case(cases, frozen),
        "diagnostics": reasons,
        "numpy_alias_shim": bool(shim_dir),
    }
```

在写完 `report`、返回退出码之前，加入两道判定（放在已有的常量张量判定旁边）：

```python
    if failed_baseline:
        print(f"\n✗ {len(failed_baseline)} 条用例的基线执行失败："
              f"{failed_baseline[:12]}", file=sys.stderr)
        print("  → 基线插件是本轮的验收方产出，改它不受冻结约束。"
              "先修 function_<op>.py 再重跑冻结。", file=sys.stderr)
        print("  → 常见成因：用例输入带 name 时全部进 kwargs，"
              "args 恒为空；YAML 输入名必须等于基线函数形参名。", file=sys.stderr)
        return 2

    if total_bytes > args.budget_bytes:
        print(f"\n✗ 输入总量 {total_bytes} 字节超出预算 {args.budget_bytes}。",
              file=sys.stderr)
        print("  → 把大规模档摊到多个维度，或把该轴组合列入 infeasible。",
              file=sys.stderr)
        return 2
```

在 `main()` 的 argparse 里补：

```python
    parser.add_argument("--budget-bytes", type=int, default=2 * 1024 ** 3,
                        help="冻结输入总字节预算，默认 2GiB")
```

- [ ] **Step 5: 删预算脚本**

```bash
cd skill/repo-task-atk-test
git rm scripts/check_input_budget.py tests/test_input_budget.py
grep -rn "check_input_budget\|input_budget" scripts/ references/ SKILL.md tests/
```

`references/execution.md`、`references/case-design.md` 里的预算门禁段落，
改为一句「冻结时顺带核对输入总量，超预算退出码 2」。

- [ ] **Step 6: 跑测试确认通过**

Run: `python3 -m pytest tests/ -q`
Expected: 244 passed, 9 skipped

- [ ] **Step 7: 提交**

```bash
git add -A skill/repo-task-atk-test
git commit -m "$(cat <<'EOF'
refactor: fold the input budget into freezing and gate the baseline plugin

预算门禁默认 2GiB，roll 实测最大 8MB——差 250 倍，从没拦下过任何东西，
却占一个脚本一道门禁一次调用。freeze_inputs 本来就要把输入全部落盘，
总字节数它最清楚。

同时补上唯一没有门禁的 agent 产出：基线插件。roll 那轮冻结时基线已经
全线失败，freeze_inputs 只看输入落盘就放行，错误推到 S3 冒烟才炸、
S4 全量又炸 42/81——而失败信息当时就在它已经抓到手的日志里。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: 复现包与流程文档收口

前五个 Task 各自动了 reference，这一步统一核对没有断链，并把 S1 的新动作写进流程。

**Files:**
- Modify: `SKILL.md`（S1 卡增派生动作；门禁计数）
- Modify: `references/intake.md`（S1 出口加 `interface.json`）
- Modify: `references/execution.md`、`references/reporting.md`
- Modify: `tests/test_document_style.py`（`test_every_script_has_a_documentation_route` 会因删脚本而变）

- [ ] **Step 1: 跑现有文档测试，看断链**

Run: `python3 -m pytest tests/test_document_style.py -q`
Expected: 可能 FAIL 于 `test_documented_script_routes_exist`（reference 里还留着已删脚本）
或 `test_every_script_has_a_documentation_route`（`derive_interface.py` 还没有指路）

- [ ] **Step 2: 给 `derive_interface.py` 写指路**

`references/intake.md` 的「## 接口和基线」小节末尾追加：

```markdown
接口模式与基线符号确认后，派生执行后端并落盘：

```bash
<python> scripts/derive_interface.py --mode <aclnn|pytorch|kernel> \
  --candidate <候选符号> --baseline <基线符号> \
  --mode-source '<出处>' -o evidence/interface.json
```

执行后端不是选择题，由接口模式唯一推导，S2 之后所有步骤只读这份文件。

`atk node -b aclnn` 需要为每个算子写 C++ 绑定并编译 aclnnTest，不在本 skill 范围。
```

- [ ] **Step 3: 更新 S1 出口门禁**

`SKILL.md` 阶段流程表 S1 行：

```markdown
| S1 任务书解读 | 待确认项清零，且环境指纹可用 | 约束表、`evidence/env.json` |
```

改为：

```markdown
| S1 任务书解读 | 待确认项清零，环境指纹可用，接口事实已派生 | 约束表、`evidence/env.json`、`evidence/interface.json` |
```

- [ ] **Step 4: 跑全量**

Run: `python3 -m pytest tests/ -q`
Expected: 244 passed, 9 skipped

- [ ] **Step 5: 人工核对精简结果**

```bash
cd skill/repo-task-atk-test
ls scripts/*.py | wc -l   # 预期 27（原 30 −4 删 +1 新增）
wc -l scripts/*.py | tail -1
grep -rn "make_manifest\|check_atk_capabilities\|check_adapter_repair\|check_input_budget" \
     scripts/ references/ SKILL.md assets/ tests/ | grep -v Binary
```

最后一条应无输出。

- [ ] **Step 6: 提交**

```bash
git add -A skill/repo-task-atk-test
git commit -m "$(cat <<'EOF'
docs: close the loop after gate simplification

S1 出口新增接口事实派生；四个已删脚本的指路全部清理；
门禁计数同步：S2 五道改三道，S3 六道改五道。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**范围覆盖**

| 察验结论 | 落地 |
| --- | --- |
| 删 `check_atk_capabilities` + `_atk_capabilities`，P4/P6/P7 移进 `make_yaml` | Task 2 |
| 删 `make_manifest`，verdict 直接读产物 | Task 3 |
| 删 `check_adapter_repair` | Task 4 |
| `check_input_budget` 并进 `freeze_inputs` | Task 5 |
| policy 的 aclnn 假选择改单值 | Task 1 |
| 接口事实 S1 派生一次 | Task 1 |
| 基线插件补门禁 | Task 5 |
| `--mode A/B/C` 过度设计 | Task 3（随 manifest 删除） |

**Placeholder scan**：无 TBD/TODO；每个改代码的步骤都给了完整代码；每条命令都给了预期输出。

**Type consistency**：
`derive_interface.BACKEND_BY_MODE`、`derive_interface.derive` 在 Task 1 定义、Task 1 测试使用；
`verdict.check_interface`、`verdict.artifact_digests` 在 Task 3 定义、同 Task 测试使用；
`freeze_inputs.baseline_failures` 在 Task 5 定义、同 Task 测试使用。
`evidence/interface.json` 的七个键在 Task 1 产出、Task 3 消费，名称一致。

**测试计数**：268 → 276（T1 +8）→ 259（T2 −20 +3）→ 254（T3 −8 +3）
→ 248（T4 −6）→ 244（T5 −7 +3）→ 244（T6）。
执行时以实际输出为准；数字对不上要确认差额来自本 Task 的增删而非误删。

**与骨架计划的衔接**：本计划完成后，`scripts/` 为 27 个、S2 门禁三道、S3 门禁五道。
`2026-08-15-atk-contract-spine.md` 的 Task 1 骨架据此登记，其中：
`manifest.json` 不再登记；S3 产物为绑定报告与冒烟日志；
S1 新增 `evidence/interface.json`（`owner: script`，因此不进决策点清单——
它已经不再是决策）。骨架计划 Task 5 的作战卡里，S2 卡的门禁写「签名 / 结构 / 覆盖」。
