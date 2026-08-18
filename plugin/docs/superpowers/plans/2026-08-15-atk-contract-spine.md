# ATK 验收 skill 契约骨架实施计划（Plan 1 / 架构）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 skill 建立单一产物契约骨架，并从骨架派生阶段作战卡、决策点清单与四条一致性不变量，让「知识是否完备」和「四层是否同步」从改完就能判定。

**Architecture:** 新增 `references/artifact-contracts.json` 作为唯一骨架，登记全链路产物与字段契约；`scripts/_contracts.py` 提供加载与渲染；作战卡内联 SKILL.md 并由 `mark_step.py` 在阶段入口二次渲染；`tests/test_contracts.py` 用四条结构不变量替代按事故索引的字符串断言。

**Tech Stack:** Python 3（标准库 + pytest/unittest），JSON，Markdown。无新增第三方依赖。

设计文档：`docs/superpowers/specs/2026-08-15-atk-knowledge-architecture-design.md`

**执行顺序：本计划排在 `2026-08-16-atk-gate-simplification.md`（门禁精简）之后。**

理由：骨架登记的必须是**精简之后**的产物与量具清单。
精简会删掉 `make_manifest.py`、`check_atk_capabilities.py`、`check_adapter_repair.py`、
`check_input_budget.py` 四个脚本，并新增 `evidence/interface.json`。
先登记再删除等于白做一遍，而且 Task 2 的锁 L4 会因为 `producer` 指向已删脚本而红。

精简完成后的既定事实，本计划直接采用：

- `scripts/` 27 个
- S2 出口门禁三道：签名 / 结构 / 覆盖
- S3 出口门禁五道：构建 / 安装 / SoC / op_api / 冒烟
- S1 多一样产物 `evidence/interface.json`，`owner: script`

Plan C（缺陷修复 B1–B9 的剩余项）在本计划完成后单独执行，它依赖本计划产出的骨架。

## Global Constraints

- 工作目录：`skill/repo-task-atk-test/`（下称 skill root）。所有相对路径以此为根。
- 测试命令：在 skill root 执行 `python3 -m pytest tests/ -q`。基线 268 passed, 9 skipped, 29 subtests。
- 测试写法：`unittest.TestCase`，用 `SKILL_ROOT = Path(__file__).resolve().parents[1]` 定位，
  需要导入脚本时 `sys.path.insert(0, str(SKILL_ROOT / "scripts"))` 后按裸模块名导入。
- 文档、注释、字符串一律中文；代码标识符英文。
- 引用写成可点击路径，例如 `atk/configs/design_config.py:85`。
- 不改 `atk/` 源码。不写任何算子专属逻辑（无算子名、具体 shape、dtype 清单、专属阈值）。
- 骨架里每条断言必须是 A 级（实跑）或 B 级（源码核实）证据；拿不准写进 `knowledge_gaps` 而不是编。
- 每个 Task 结束必须 `python3 -m pytest tests/ -q` 全绿再提交。
- 提交信息末尾附：`Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`

---

## File Structure

| 文件 | 责任 |
| --- | --- |
| `references/artifact-contracts.json` | 骨架。全链路产物的封闭清单 + 字段契约。唯一真源 |
| `scripts/_contracts.py` | 骨架加载、结构校验、卡与决策点的渲染函数。私有模块，不单独当 CLI |
| `scripts/render_views.py` | 把决策点清单渲染进 `references/decision-points.md`。CLI |
| `references/decision-points.md` | 派生视图，签入仓库，由测试保证与骨架同步 |
| `tests/test_contracts.py` | 四条结构不变量 + 骨架自身结构校验 |
| `SKILL.md` | 五张阶段作战卡；正文按可执行性瘦身 |
| `scripts/mark_step.py` | 阶段打点时渲染当阶段卡 |
| `scripts/make_must_cover.py` | 导出活常量 `DECL_KEYS` + 未知键检测 |
| `scripts/make_yaml.py` | 导出活常量 `YAML_HEADER_KEYS`/`DERIVED_KEYS` + 未知头部键检测 |
| `tests/test_document_style.py` | 放宽 SKILL.md 行数阈值；归并被不变量覆盖的事故断言 |

---

## Task 1: 骨架与加载器

**Files:**
- Create: `references/artifact-contracts.json`
- Create: `scripts/_contracts.py`
- Test: `tests/test_contracts.py`

**Interfaces:**
- Produces：
  - `_contracts.CONTRACTS_PATH: Path` — 骨架文件路径
  - `_contracts.load() -> dict` — 读骨架，失败抛 `ContractError`
  - `_contracts.ContractError(Exception)`
  - `_contracts.OWNERS: frozenset` — `{"agent", "script", "atk"}`
  - `_contracts.artifacts_of(data, stage) -> dict[str, dict]` — 取某阶段全部产物，按登记顺序
  - `_contracts.agent_fields(data) -> list[tuple[str, str, dict]]` — `(产物名, 字段名, 字段契约)`，仅 `owner == "agent"`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_contracts.py`：

```python
"""产物契约骨架：结构完整性与四条一致性不变量。

这些用例断言的是「两处是否一致」，不是「某处是否正确」。
正确性由证据分级保证（CLAUDE.md §3.2），一致性由这里保证，两者不互相替代。

与 test_document_style.py 里按事故索引的字符串断言的区别：
那些只对已发生的事有效，这些对将来新增的产物同样有效。
"""

import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import _contracts  # noqa: E402

STAGES = ("S1", "S2", "S3", "S4", "S5")


class SpineStructureTest(unittest.TestCase):
    def setUp(self):
        self.data = _contracts.load()

    def test_every_stage_is_declared(self):
        self.assertEqual(tuple(self.data["stages"]), STAGES)

    def test_every_stage_carries_the_five_card_slots(self):
        for stage, block in self.data["stages"].items():
            with self.subTest(stage=stage):
                for slot in ("name", "core", "lookup_topics", "gates", "forbidden"):
                    self.assertIn(slot, block, f"{stage} 缺卡槽位 {slot}")
                self.assertTrue(block["core"].strip(), f"{stage} 的核心一句话为空")

    def test_every_artifact_declares_a_known_stage_and_owner(self):
        for name, spec in self.data["artifacts"].items():
            with self.subTest(artifact=name):
                self.assertIn(spec["stage"], STAGES)
                self.assertIn(spec["owner"], _contracts.OWNERS)

    def test_every_artifact_resolves_to_a_spec_anchor(self):
        for name, spec in self.data["artifacts"].items():
            with self.subTest(artifact=name):
                self.assertTrue(spec.get("spec"), f"{name} 没有规范锚点")

    def test_fields_are_either_detailed_or_explicitly_waived(self):
        # 日志一类的产物没有字段可登记，但必须写明理由，
        # 否则「没写字段」和「不需要字段」分不开，完备性就不可判定。
        for name, spec in self.data["artifacts"].items():
            with self.subTest(artifact=name):
                has_fields = bool(spec.get("fields"))
                waived = bool(spec.get("fields_not_applicable"))
                self.assertTrue(has_fields or waived,
                                f"{name} 既没有 fields 也没有 fields_not_applicable")
                self.assertFalse(has_fields and waived,
                                 f"{name} 同时写了 fields 和 fields_not_applicable")

    def test_every_field_answers_the_four_questions(self):
        for name, spec in self.data["artifacts"].items():
            for field, contract in (spec.get("fields") or {}).items():
                with self.subTest(artifact=name, field=field):
                    self.assertIn(contract["owner"], _contracts.OWNERS)
                    for key in ("source", "consumer", "failure"):
                        self.assertTrue(contract.get(key),
                                        f"{name}.{field} 缺 {key}")

    def test_agent_fields_are_enumerable(self):
        rows = _contracts.agent_fields(self.data)
        self.assertTrue(rows, "骨架里一个 agent 决策字段都没有，决策点清单会是空的")
        for artifact, field, contract in rows:
            with self.subTest(artifact=artifact, field=field):
                self.assertEqual(contract["owner"], "agent")

    def test_artifacts_of_returns_only_that_stage(self):
        for stage in STAGES:
            with self.subTest(stage=stage):
                for spec in _contracts.artifacts_of(self.data, stage).values():
                    self.assertEqual(spec["stage"], stage)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_contracts.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named '_contracts'`

- [ ] **Step 3: 写加载器**

创建 `scripts/_contracts.py`：

```python
"""产物契约骨架的加载与渲染。

skill 以前按事故索引：一次跑测发现一条事实，就塞进最近的一份 reference。
于是「这个阶段要产出几样东西、每样几个字段」没有任何一处回答得了，
完备性只能等下一次真机跑测来告诉你——本项目已经这样循环了三轮。

骨架把这件事翻过来：产物清单封闭，每样产物逐字段登记
「谁写、依据什么写、谁消费、写错了什么时候怎么炸」。
作战卡、决策点清单都是它的视图，不是并列维护的第二第三份表。

本模块不是 CLI，只被 mark_step.py / render_views.py / tests 导入。
"""

import json
from pathlib import Path

CONTRACTS_PATH = Path(__file__).resolve().parents[1] / "references" / \
    "artifact-contracts.json"

# 谁产出这样东西。agent 的那些就是它必须自己判断的决策全集。
OWNERS = frozenset({"agent", "script", "atk"})


class ContractError(Exception):
    """骨架读不出来或结构不成立。"""


def load(path=None):
    path = Path(path) if path else CONTRACTS_PATH
    if not path.exists():
        raise ContractError(f"找不到产物契约骨架 {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContractError(f"骨架不是合法 JSON：{exc}") from exc


def artifacts_of(data, stage):
    """取某阶段的全部产物，保持登记顺序——卡按这个顺序渲染。"""
    return {name: spec for name, spec in data["artifacts"].items()
            if spec["stage"] == stage}


def agent_fields(data):
    """agent 必须自己判断的字段全集，决策点清单由它渲染。"""
    rows = []
    for name, spec in data["artifacts"].items():
        for field, contract in (spec.get("fields") or {}).items():
            if contract.get("owner") == "agent":
                rows.append((name, field, contract))
    return rows
```

- [ ] **Step 4: 写骨架（阶段块 + S1/S2 产物，S2 含字段级）**

创建 `references/artifact-contracts.json`：

```json
{
  "schema_version": 1,
  "stages": {
    "S1": {
      "name": "任务书解读",
      "core": "把任务书变成可核验的事实约束，并拿到本机环境指纹",
      "lookup_topics": ["backends", "comparator"],
      "gates": ["待确认项清零", "环境指纹可用"],
      "forbidden": ["读被测算子源码", "推测未写明的性能基线", "开始写用例"]
    },
    "S2": {
      "name": "用例生成",
      "core": "把 S1 的事实约束变成 ATK 能吃下去的一组文件，并冻结",
      "lookup_topics": ["group_types", "parameter_type", "dtype", "comparator", "binding"],
      "gates": ["能力", "签名", "结构", "覆盖", "预算"],
      "forbidden": ["碰构建与安装", "为了让门禁过而改判据", "手写 combos 或 YAML"]
    },
    "S3": {
      "name": "编译安装部署",
      "core": "用公开构建面产出并安装候选包，直到一条冒烟真的跑通",
      "lookup_topics": ["backends", "binding"],
      "gates": ["构建", "安装", "SoC 绑定", "op_api 绑定", "冒烟", "manifest"],
      "forbidden": ["换 SoC 重建", "改待测源码或构建脚本", "回 S2 重新生成用例"]
    },
    "S4": {
      "name": "精度性能测试",
      "core": "跑全量并裁决，数字全部由脚本产出",
      "lookup_topics": ["comparator_semantics", "report_paths"],
      "gates": ["精度已裁决", "性能状态非空"],
      "forbidden": ["人工重算通过率", "剔除原因不明的失败", "跨类外推归因"]
    },
    "S5": {
      "name": "输出测试结果",
      "core": "把证据组织成可复现的验收结论",
      "lookup_topics": [],
      "gates": ["摘要", "政策摘要", "证据链"],
      "forbidden": ["用人工结论绕过门禁", "把待确认项写成结论"]
    }
  },
  "artifacts": {
    "evidence/constraints.md": {
      "stage": "S1",
      "owner": "agent",
      "spec": "references/intake.md#验收约束表",
      "template": null,
      "producer": null,
      "consumed_by": ["S2 的语义轴设计"],
      "risk": "待确认项没清零就进 S2，后面每一步都建在推测上",
      "fields_not_applicable": "自由格式的约束记录，字段由任务书决定，不构成机械契约"
    },
    "evidence/env.json": {
      "stage": "S1",
      "owner": "script",
      "spec": "references/execution.md#环境",
      "template": null,
      "producer": "probe_env.py",
      "consumed_by": ["check_soc_binding.py"],
      "risk": "未先 source CANN 时只有 Phase A，build_soc 缺失而不报错",
      "fields_not_applicable": "探针输出，字段随本机装机情况变化，由 probe_env.py 自身担保"
    },
    "evidence/interface.json": {
      "stage": "S1",
      "owner": "script",
      "spec": "references/intake.md#接口和基线",
      "template": null,
      "producer": "derive_interface.py",
      "consumed_by": ["verdict.py", "make_repro.py"],
      "risk": "执行后端由接口模式唯一推导；跑测命令 -b 用了别的后端，S4 才会被 verdict 抓到",
      "fields_not_applicable": "由 derive_interface.py 从 S1 确认结果派生，字段是推导结果不是决策"
    },
    "<op>_decl.json": {
      "stage": "S2",
      "owner": "agent",
      "spec": "references/case-design.md#必测集契约",
      "template": "assets/example/decl.json",
      "producer": null,
      "consumed_by": ["make_must_cover.py"],
      "risk": "语义轴取值 ATK 表达不了时，要到第 4 步 make_yaml 才报，比决策点晚三步",
      "fields": {
        "operator_class": {
          "owner": "agent",
          "source": "任务书的算子类别",
          "consumer": "_coverage_strategy 据此选默认轴与默认交互组",
          "failure": "未知类别 → make_must_cover.py 退出码 2"
        },
        "dims": {
          "owner": "agent",
          "source": "任务书 / 基线接口 / 生态标准，不读被测源码",
          "consumer": "make_must_cover.py 的覆盖分母",
          "failure": "取值 ATK 表达不了（如复合组空序列）→ make_yaml.py 报「复合组最少一个成员」"
        },
        "coverage_policy": {
          "owner": "agent",
          "source": "references/case-design.md#策略",
          "consumer": "make_must_cover.py 的投影与预算",
          "failure": "轴不在 dims 里 → make_must_cover.py 退出码 2"
        },
        "axes": {
          "owner": "agent",
          "source": "能从用例 JSON 读回来的轴才能放",
          "consumer": "check_coverage.py 的覆盖签名",
          "failure": "签名区分度不足 → 多条 combo 碰撞，覆盖率虚高或命中不上"
        },
        "extract": {
          "owner": "agent",
          "source": "_case_utils 提供的读取函数",
          "consumer": "check_coverage.py 从用例回读轴值",
          "failure": "缺规则 → check_coverage.py 报 missing_rules"
        },
        "parameters": {
          "owner": "agent",
          "source": "基线签名与 aclnn C 签名，由 align_signatures.py 对齐",
          "consumer": "make_yaml.py 推导每个 YAML 输入",
          "failure": "element_kind/runtime_container 组合非法 → make_yaml.py 攒齐报错"
        },
        "infeasible": {
          "owner": "agent",
          "source": "物理不可达的轴组合，每条附 why",
          "consumer": "make_must_cover.py 排除组合",
          "failure": "缺 why → make_must_cover.py 退出码 2"
        },
        "yaml": {
          "owner": "agent",
          "source": "references/yaml-schema.md#顶层字段",
          "consumer": "make_yaml.py 原样写进 YAML 头部",
          "failure": "字段名写错 → 由 make_yaml.py 的未知头部键检测拦下"
        },
        "comparator": {
          "owner": "agent",
          "source": "references/experimental_standard.md",
          "consumer": "check_coverage.py 判断是否豁免浮点占比",
          "failure": "与 dtype 轴矛盾 → check_coverage.py 报错"
        }
      }
    },
    "<op>_materialize.py": {
      "stage": "S2",
      "owner": "agent",
      "spec": "references/case-design.md#生成必测集",
      "template": "assets/example/materialize.py",
      "producer": null,
      "consumed_by": ["make_yaml.py", "check_coverage.py", "validate_cases.py"],
      "risk": "手写脚本自身的 bug 不被任何门禁覆盖，只能靠跑挂发现",
      "fields_not_applicable": "算子专属脚本，契约体现在它写进 materialized combos 的键上"
    },
    "must_cover.json": {
      "stage": "S2",
      "owner": "script",
      "spec": "references/case-design.md#必测集契约",
      "template": null,
      "producer": "make_must_cover.py",
      "consumed_by": ["<op>_materialize.py", "check_coverage.py"],
      "risk": "手写 combos 会让门禁与被检对象同源，断言失效",
      "fields_not_applicable": "由 make_must_cover.py 从 decl 推导，字段是 decl 的投影"
    },
    "<op>.yaml": {
      "stage": "S2",
      "owner": "script",
      "spec": "references/yaml-schema.md#顶层字段",
      "template": null,
      "producer": "make_yaml.py",
      "consumed_by": ["atk case", "check_atk_capabilities.py", "make_manifest.py"],
      "risk": "必须在物化之后推导；tuple_numbers 取自 combos 里 attr 列表的实际长度",
      "fields": {
        "name": {
          "owner": "agent",
          "source": "任务书的基线接口符号",
          "consumer": "pytorch 模式的定位字段；CPU 基线默认执行器按它 eval",
          "failure": "填成候选符号 → 基线与候选同源，精度比对恒过"
        },
        "aclnn_name": {
          "owner": "agent",
          "source": "任务书的候选 aclnn 符号",
          "consumer": "aclnn 模式的定位字段",
          "failure": "与 name 同值 → 候选与基线不可分辨"
        },
        "api_type": {
          "owner": "agent",
          "source": "CPU 基线插件的 @register 名",
          "consumer": "ATK 据此取基线执行器",
          "failure": "与 @register 对不上 → 报「请检查用例yaml中的api_type字段是否有对应标杆API文件」"
        },
        "aclnn_api_type": {
          "owner": "agent",
          "source": "aclnn 执行器的 @register 名；不需要适配器时为 aclnn_function",
          "consumer": "pyaclnn_backend 据此取候选执行器",
          "failure": "该写适配器却留默认 → S3 冒烟类型校验失败，且改它要重跑 atk case"
        },
        "generate": {
          "owner": "agent",
          "source": "生成器插件的注册名",
          "consumer": "atk case 据此取生成器",
          "failure": "对不上 → validate_cases.py 的 C1 不通过"
        },
        "standard": {
          "owner": "agent",
          "source": "references/experimental_standard.md",
          "consumer": "精度判定与性能对比模式",
          "failure": "一份用例集声明多种比较器 → 混合类别未拆分面"
        },
        "version": {
          "owner": "agent",
          "source": "固定 v1",
          "consumer": "ATK 用例版本",
          "failure": "缺失 → ATK 解析失败"
        },
        "api": {
          "owner": "agent",
          "source": "接口模式",
          "consumer": "ATK 选择调用通路",
          "failure": "与实际后端不符 → 绑定到错误通路"
        },
        "dtype_numbers": {
          "owner": "script",
          "source": "make_yaml.py 固定写 1",
          "consumer": "让 ATK 默认展开退化成恒等",
          "failure": "手写成别的值 → ATK 自行扩展 dtype，用例数与 must_cover 对不上"
        },
        "extra_numbers": {
          "owner": "script",
          "source": "make_yaml.py 固定写 0",
          "consumer": "关掉 ATK 自带的额外边界用例",
          "failure": "非 0 → ATK 把某轴硬编码成 2^31+1，撑爆单卡"
        },
        "shape_distributions": {
          "owner": "script",
          "source": "make_yaml.py 固定写 [[0, 1.0]]",
          "consumer": "让 shape 分布退化成恒等",
          "failure": "手写 → 与 combos 的实际 shape 冲突"
        },
        "inputs": {
          "owner": "script",
          "source": "由 parameters 契约与 combos 推导",
          "consumer": "ATK 生成每条用例的输入",
          "failure": "手写 tuple_numbers 或 dim_values → 与 combos 脱节"
        },
        "method_inputs": {
          "owner": "script",
          "source": "契约里写 method_inputs.<name> 时产生",
          "consumer": "基线对象构造，不进候选调用",
          "failure": "把调用参数放进来 → 候选侧收不到该参数"
        },
        "tensor_input": {
          "owner": "script",
          "source": "契约里写 tensor_input.<name> 时产生",
          "consumer": "基线张量方法的接收者对象",
          "failure": "写成 list/tuple 组 → ATK 只接受单个接收者"
        }
      }
    },
    "<op>_constraint.py": {
      "stage": "S2",
      "owner": "agent",
      "spec": "references/plugin-authoring.md#生成器",
      "template": "assets/example/constraint.py",
      "producer": null,
      "consumed_by": ["atk case"],
      "risk": "复合组在运行期是 list，且组长度由 ATK 从 tuple_numbers 随机抽，不按 combo 顺序对应",
      "fields_not_applicable": "算子专属插件，契约体现在它覆写的 CaseConfig/InputCaseConfig 字段上"
    },
    "function_<op>.py": {
      "stage": "S2",
      "owner": "agent",
      "spec": "references/plugin-authoring.md#CPU golden",
      "template": "assets/example/function_example.py",
      "producer": null,
      "consumed_by": ["atk node -b cpu"],
      "risk": "name 非空的输入在 dataset reload 后全部进 kwargs，args 恒为空",
      "fields_not_applicable": "算子专属插件，契约体现在 @register 名与 YAML api_type 的对应上"
    },
    "冻结输入": {
      "stage": "S2",
      "owner": "script",
      "spec": "references/execution.md#冻结输入",
      "template": null,
      "producer": "freeze_inputs.py",
      "consumed_by": ["atk task --input_data", "S3 冒烟", "S4 全量"],
      "risk": "整张只有一个取值的输入测不出错；基线插件挂了也要在这里当场炸，不能推到 S3",
      "fields_not_applicable": "落盘的张量数据与摘要，字段由 freeze_inputs.py 自身担保"
    },
    "evidence/soc_binding.json": {
      "stage": "S3",
      "owner": "script",
      "spec": "references/build-deploy.md#SoC 声明门禁",
      "template": null,
      "producer": "check_soc_binding.py",
      "consumed_by": ["S5 证据链"],
      "risk": "算子不声明真机 SoC 时属结构性阻塞，不得换 SoC 重建",
      "fields_not_applicable": "门禁报告，字段由 check_soc_binding.py 自身担保"
    },
    "evidence/opapi_binding.json": {
      "stage": "S3",
      "owner": "script",
      "spec": "references/build-deploy.md#候选 op_api",
      "template": null,
      "producer": "check_opapi_binding.py",
      "consumed_by": ["S5 证据链"],
      "risk": "脚本要在加载了 CANN 环境的 shell 里跑，否则 libacl_rt.so 找不到而误判",
      "fields_not_applicable": "门禁报告，字段由 check_opapi_binding.py 自身担保"
    },
    "evidence/smoke_*.log": {
      "stage": "S3",
      "owner": "atk",
      "spec": "references/atk-cli.md#最小冒烟",
      "template": null,
      "producer": null,
      "consumed_by": ["check_opapi_binding.py", "S5 证据链"],
      "risk": "退出码 0 不等于通过；要从日志确认成功数、实际后端与候选加载路径",
      "fields_not_applicable": "ATK 运行日志，没有可登记的字段契约"
    },
    "conclusion/accuracy_results.json": {
      "stage": "S4",
      "owner": "script",
      "spec": "references/reporting.md#解析",
      "template": null,
      "producer": "parse_atk_report.py",
      "consumed_by": ["verdict.py"],
      "risk": "报告路径只能从本轮日志的 save result excel file 取，别从目录名猜",
      "fields_not_applicable": "解析产物，字段由 parse_atk_report.py 自身担保"
    },
    "conclusion/verdict.json": {
      "stage": "S4",
      "owner": "script",
      "spec": "references/reporting.md#裁决",
      "template": null,
      "producer": "verdict.py",
      "consumed_by": ["S5 报告", "make_repro.py"],
      "risk": "任一门禁失败都拒绝裁决；证据不足写 unknown，不要凑结论",
      "fields_not_applicable": "裁决产物，字段由 verdict.py 自身担保"
    },
    "验收报告": {
      "stage": "S5",
      "owner": "agent",
      "spec": "references/reporting.md#报告结构",
      "template": null,
      "producer": null,
      "consumed_by": [],
      "risk": "数字全部引用 verdict.json，模型只做归因、行文与证据组织",
      "fields_not_applicable": "自由格式报告，结构由 reporting.md 规定，不构成机械契约"
    },
    "复现包": {
      "stage": "S5",
      "owner": "script",
      "spec": "references/reporting.md#复现包",
      "template": null,
      "producer": "make_repro.py",
      "consumed_by": [],
      "risk": "不打包分析脚本，跑测期改过的量具会落在证据链之外",
      "fields_not_applicable": "打包产物，字段由 make_repro.py 自身担保"
    }
  }
}
```

S3–S5 只登记到产物级，`fields` 一律走 `fields_not_applicable`：
它们要么是脚本自证的报告，要么是 ATK 日志，没有 agent 要填的字段。
唯一的 agent 产出是验收报告，它的结构规范在 `reporting.md`，不是字段契约。

- [ ] **Step 5: 跑测试确认通过**

Run: `python3 -m pytest tests/test_contracts.py -q`
Expected: PASS，8 passed

- [ ] **Step 6: 跑全量确认没打破别的**

Run: `python3 -m pytest tests/ -q`
Expected: 276 passed, 9 skipped（原 268 + 新增 8）

- [ ] **Step 7: 提交**

```bash
git add skill/repo-task-atk-test/references/artifact-contracts.json \
        skill/repo-task-atk-test/scripts/_contracts.py \
        skill/repo-task-atk-test/tests/test_contracts.py
git commit -m "$(cat <<'EOF'
feat: add artifact contract spine for the acceptance skill

骨架登记全链路产物与字段契约，每个字段回答四个问题：
谁写、依据什么写、谁消费、写错了什么时候怎么炸。

第四个属性把 late binding 显式化，报错文本可以反查条目——
这是 roll 那轮最缺的东西。

产物清单封闭是完备性可判定的前提；清单不封闭，一切退回等下轮跑测。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: 锁 L4 —— 骨架↔规范

**Files:**
- Modify: `tests/test_contracts.py`（追加 `SpecAnchorTest`）
- Modify: `references/yaml-schema.md`（补 `api_type` 与 `kernel_name`，补锚点小节）

**Interfaces:**
- Consumes：`_contracts.load`、`_contracts.agent_fields`
- Produces：`_contracts.resolve_anchor(anchor) -> Path`，锚点形如 `references/xxx.md#小节名`；
  解析不了抛 `ContractError`

- [ ] **Step 1: 写失败测试**

在 `tests/test_contracts.py` 末尾（`if __name__` 之前）追加：

```python
class SpecAnchorTest(unittest.TestCase):
    """锁 L4：agent 要自己判断的字段，必须有规范可读。

    一个 owner=agent 的字段没有可解析的规范锚点，就是一个洞——
    agent 只能靠猜或去读 ATK 源码。roll 那轮 34 次源码探索里，
    有一半是这种洞造成的。
    """

    def setUp(self):
        self.data = _contracts.load()

    def test_every_artifact_spec_anchor_resolves(self):
        for name, spec in self.data["artifacts"].items():
            with self.subTest(artifact=name):
                _contracts.resolve_anchor(spec["spec"])

    def test_declared_template_exists(self):
        for name, spec in self.data["artifacts"].items():
            template = spec.get("template")
            if not template:
                continue
            with self.subTest(artifact=name):
                self.assertTrue((SKILL_ROOT / template).exists(),
                                f"{name} 声明的模板 {template} 不存在")

    def test_declared_producer_exists(self):
        for name, spec in self.data["artifacts"].items():
            producer = spec.get("producer")
            if not producer:
                continue
            with self.subTest(artifact=name):
                self.assertTrue((SKILL_ROOT / "scripts" / producer).exists(),
                                f"{name} 声明的量具 {producer} 不存在")

    def test_unresolvable_anchor_is_rejected(self):
        with self.assertRaises(_contracts.ContractError):
            _contracts.resolve_anchor("references/case-design.md#这个小节不存在")
        with self.assertRaises(_contracts.ContractError):
            _contracts.resolve_anchor("references/不存在的文件.md#随便")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_contracts.py::SpecAnchorTest -q`
Expected: FAIL，`AttributeError: module '_contracts' has no attribute 'resolve_anchor'`

- [ ] **Step 3: 实现锚点解析**

在 `scripts/_contracts.py` 的 `agent_fields` 之后追加：

```python
def resolve_anchor(anchor):
    """把 `references/xxx.md#小节名` 解析成真实文件，解析不了就抛。

    锚点解析不了意味着规范在改名或被删时没有人跟着改骨架——
    这正是「四层各自正确、合起来失效」的那条缝。
    """
    path_text, _, heading = str(anchor).partition("#")
    path = CONTRACTS_PATH.parents[1] / path_text
    if not path.exists():
        raise ContractError(f"规范锚点指向不存在的文件：{anchor}")
    if not heading:
        return path
    headings = {line.lstrip("# ").strip()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.startswith("#")}
    if heading not in headings:
        raise ContractError(f"规范锚点的小节不存在：{anchor}")
    return path
```

- [ ] **Step 4: 跑测试，预期在 `yaml-schema.md` 上暴露真实缺陷**

Run: `python3 -m pytest tests/test_contracts.py::SpecAnchorTest -q`
Expected: PASS。若报 `#顶层字段` 不存在则说明小节名写错，按实际标题订正骨架。

- [ ] **Step 5: 补 `yaml-schema.md` 缺失的头部字段**

`references/yaml-schema.md:15` 现在写的是 7 个头部字段，漏了 `api_type` 和 `kernel_name`。
`api_type` 正是 CPU 基线插件的接线字段，roll 那轮因此崩了三次。

把该行整体替换为：

```markdown
声明文件的 `yaml` 块只保存头部字段：`name`、`aclnn_name`、`kernel_name`、`version`、`api`、`api_type`、`aclnn_api_type`、`generate` 和 `standard`。

`api_type` 绑基线执行器，`aclnn_api_type` 绑候选执行器，`generate` 绑生成器。

三者各管一侧，缺哪一个就走哪一侧的 ATK 默认路径，不会报「没配」。
```

- [ ] **Step 6: 跑全量**

Run: `python3 -m pytest tests/ -q`
Expected: 280 passed, 9 skipped

- [ ] **Step 7: 提交**

```bash
git add skill/repo-task-atk-test/scripts/_contracts.py \
        skill/repo-task-atk-test/tests/test_contracts.py \
        skill/repo-task-atk-test/references/yaml-schema.md
git commit -m "$(cat <<'EOF'
feat: lock spec anchors to the contract spine

锁 L4：owner=agent 的字段必须有可解析的规范锚点，否则就是一个洞。

上线即抓出一条真实漂移：yaml-schema.md 的头部字段清单漏了 api_type，
而它正是 CPU 基线插件的接线字段，roll 那轮因此崩了三次——
事实在 plugin-authoring.md 和模板里都有，就是不在写 YAML 头部的那一刻。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: 锁 L1 —— 骨架↔量具（活常量）

**Files:**
- Modify: `scripts/make_must_cover.py`
- Modify: `scripts/make_yaml.py`
- Modify: `tests/test_contracts.py`（追加 `GaugeReflectionTest`）
- Modify: `tests/test_make_must_cover.py`（追加未知键用例）
- Modify: `tests/test_make_yaml.py`（追加未知头部键用例）

**Interfaces:**
- Produces：
  - `make_must_cover.DECL_KEYS: frozenset` — 声明文件允许的顶层键
  - `make_yaml.YAML_HEADER_KEYS: frozenset` — yaml 块允许的头部键
  - `make_yaml.DERIVED_KEYS: tuple` — make_yaml 固定写死的三个键
  - `make_yaml.CHANNELS: tuple`（已存在）

**关键设计：常量必须是「活的」**——脚本自身拿它做未知键检测，不是纯给文档看。
否则常量本身变成第二份要同步的东西，锁就白设了。

- [ ] **Step 1: 写失败测试（反射不变量）**

在 `tests/test_contracts.py` 追加：

```python
class GaugeReflectionTest(unittest.TestCase):
    """锁 L1：量具认得的字段集合 == 骨架登记的字段集合。

    量具加了字段没登记，或登记了量具不认，都在这里红。
    这条不变量对将来新增的字段同样有效，不需要为每个新字段补一条断言。
    """

    def setUp(self):
        self.data = _contracts.load()

    def test_decl_keys_match_the_spine(self):
        import make_must_cover
        registered = set(self.data["artifacts"]["<op>_decl.json"]["fields"])
        self.assertEqual(set(make_must_cover.DECL_KEYS), registered)

    def test_yaml_keys_match_the_spine(self):
        import make_yaml
        registered = set(self.data["artifacts"]["<op>.yaml"]["fields"])
        exported = (set(make_yaml.YAML_HEADER_KEYS)
                    | set(make_yaml.DERIVED_KEYS)
                    | set(make_yaml.CHANNELS))
        self.assertEqual(exported, registered)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_contracts.py::GaugeReflectionTest -q`
Expected: FAIL，`AttributeError: module 'make_must_cover' has no attribute 'DECL_KEYS'`

- [ ] **Step 3: 给 `make_must_cover.py` 加活常量与未知键检测**

把 `scripts/make_must_cover.py` 的 `PASS_THROUGH` 定义替换为：

```python
PASS_THROUGH = ("axes", "extract", "parameters", "infeasible", "yaml",
                "comparator")

# 声明文件允许的顶层键。本清单与 references/artifact-contracts.json 同步，
# 由 tests/test_contracts.py 的锁 L1 保证——加了键不登记会红。
DECL_KEYS = frozenset(PASS_THROUGH) | {
    "dims", "coverage_policy", "operator_class"}
```

在 `main()` 里 `spec = json.load(...)` 之后、`dims = spec.get("dims")` 之前插入：

```python
    unknown = sorted(set(spec) - DECL_KEYS)
    if unknown:
        print(f"声明文件有未知顶层键：{', '.join(unknown)}", file=sys.stderr)
        print("拼错的键会被静默忽略，声明看着写了、实际没生效。", file=sys.stderr)
        print("若确属 ATK 或本流程的合法字段，说明骨架的清单不全："
              "登记进 evidence/knowledge_gaps.json 并补进 "
              "references/artifact-contracts.json。", file=sys.stderr)
        return 2
```

- [ ] **Step 4: 给 `make_yaml.py` 加活常量与未知头部键检测**

在 `scripts/make_yaml.py` 里 `DEFAULT_TOKEN = "default"` 之后追加：

```python
# yaml 块允许的头部键。三个接线键各管一侧：api_type 绑基线执行器、
# aclnn_api_type 绑候选执行器、generate 绑生成器；缺哪个就静默走该侧的
# ATK 默认路径，不会报「没配」——所以拼错必须在这里当场拦下。
YAML_HEADER_KEYS = frozenset({
    "name", "aclnn_name", "kernel_name", "version", "api",
    "api_type", "aclnn_api_type", "generate", "standard"})

# make_yaml 固定写死的三个键，作用是让 ATK 的默认展开退化成恒等。
DERIVED_KEYS = ("dtype_numbers", "extra_numbers", "shape_distributions")
```

在 `build_design` 里，把

```python
    design = dict(header)
```

替换为

```python
    unknown = sorted(set(header) - YAML_HEADER_KEYS)
    _require(not unknown,
             f"yaml 块有未知头部键：{', '.join(unknown)}；"
             "拼错的接线键会静默走 ATK 默认路径。若确属合法字段，"
             "登记进 knowledge_gaps 并补进 artifact-contracts.json" if unknown else "")
    design = dict(header)
```

并把紧随其后的 `design.update({...})` 改成用 `DERIVED_KEYS` 组装，保证常量是活的：

```python
    design.update(dict(zip(DERIVED_KEYS, (1, 0, [[0, 1.0]]))))
```

- [ ] **Step 5: 写量具侧的行为测试**

在 `tests/test_make_must_cover.py` 的测试类里追加：

```python
    def test_unknown_declaration_key_is_rejected(self):
        import subprocess
        import tempfile
        spec = {"dims": {"dtype": ["fp32", "fp16"]},
                "coverage_policy": {"strategy": "pairwise"},
                "typo_axes": ["dtype"]}
        with tempfile.TemporaryDirectory() as tmp:
            decl = Path(tmp) / "decl.json"
            decl.write_text(json.dumps(spec), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SKILL_ROOT / "scripts" / "make_must_cover.py"),
                 "-d", str(decl), "-o", str(Path(tmp) / "out.json")],
                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("typo_axes", result.stderr)
```

在 `tests/test_make_yaml.py` 的测试类里追加：

```python
    def test_unknown_yaml_header_key_is_rejected(self):
        must_cover = {
            "yaml": dict(HEADER, api_typ="roll_cpu"),
            "parameters": {"x": {"element_kind": "tensor",
                                 "runtime_container": "single"}},
            "combos": [{"dtype": "fp32", "shape": [2, 2]}],
        }
        with self.assertRaises(DeclarationError) as caught:
            build_design(must_cover)
        self.assertIn("api_typ", str(caught.exception))
```

- [ ] **Step 6: 跑测试确认通过**

Run: `python3 -m pytest tests/test_contracts.py tests/test_make_must_cover.py tests/test_make_yaml.py -q`
Expected: PASS

- [ ] **Step 7: 跑全量**

Run: `python3 -m pytest tests/ -q`
Expected: 284 passed, 9 skipped

- [ ] **Step 8: 提交**

```bash
git add skill/repo-task-atk-test/scripts/make_must_cover.py \
        skill/repo-task-atk-test/scripts/make_yaml.py \
        skill/repo-task-atk-test/tests/test_contracts.py \
        skill/repo-task-atk-test/tests/test_make_must_cover.py \
        skill/repo-task-atk-test/tests/test_make_yaml.py
git commit -m "$(cat <<'EOF'
feat: reflect gauge field constants against the spine

锁 L1：量具导出的字段常量与骨架登记的字段必须相等。

常量是「活的」——脚本自身拿它做未知键检测，不是纯给文档看的常量，
否则常量本身会变成第二份要同步的东西。

副产品：decl 与 yaml 头部现在有未知键检测。拼错的接线键以前被静默
忽略，声明看着写了实际没生效；现在当场退出码 2，并指路 knowledge_gaps。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: 决策点清单（视图）

**Files:**
- Create: `scripts/render_views.py`
- Create: `references/decision-points.md`
- Modify: `scripts/_contracts.py`（追加 `render_decision_points`）
- Modify: `tests/test_contracts.py`（追加 `DecisionPointsViewTest`）

**Interfaces:**
- Consumes：`_contracts.load`、`_contracts.agent_fields`
- Produces：
  - `_contracts.render_decision_points(data) -> str` — 完整 Markdown 文本，含固定抬头
  - `render_views.py --check` — 渲染结果与签入文件不一致时退出码 2
  - `render_views.py --write` — 重新生成签入文件

- [ ] **Step 1: 写失败测试**

在 `tests/test_contracts.py` 追加：

```python
class DecisionPointsViewTest(unittest.TestCase):
    """决策点清单是骨架的视图，不是并列维护的第二张表。

    签入仓库是为了让 agent 能直接读；与骨架不一致就红，
    避免它退化成又一份要手工同步的文档。
    """

    def test_rendered_view_matches_the_checked_in_file(self):
        data = _contracts.load()
        rendered = _contracts.render_decision_points(data)
        checked_in = (SKILL_ROOT / "references" / "decision-points.md") \
            .read_text(encoding="utf-8")
        self.assertEqual(rendered, checked_in,
                         "决策点清单与骨架不同步，跑 scripts/render_views.py --write")

    def test_every_agent_field_appears_in_the_view(self):
        data = _contracts.load()
        rendered = _contracts.render_decision_points(data)
        for artifact, field, _ in _contracts.agent_fields(data):
            with self.subTest(artifact=artifact, field=field):
                self.assertIn(f"`{artifact}` 的 `{field}`", rendered)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_contracts.py::DecisionPointsViewTest -q`
Expected: FAIL，`AttributeError: module '_contracts' has no attribute 'render_decision_points'`

- [ ] **Step 3: 实现渲染**

在 `scripts/_contracts.py` 末尾追加：

```python
DECISION_HEADER = """# 决策点清单

<!-- 本文件由 scripts/render_views.py 从 references/artifact-contracts.json 渲染，不要手改。 -->

agent 在验收过程中必须自己判断的字段全集。

一个决策没有判据来源，就是一个洞——不必等真机跑测来发现。

判据来源写「查 X」的，用 `scripts/atk_lookup.py X` 查，不要 grep ATK 源码。

查不到就是 skill 缺陷：登记进 `evidence/knowledge_gaps.json` 再去读源码。

| 决策 | 阶段 | 判据来源 | 谁消费 | 判错的表现 |
| --- | --- | --- | --- | --- |
"""


def render_decision_points(data):
    """把骨架里 owner=agent 的字段渲染成决策点清单。"""
    rows = []
    for artifact, field, contract in agent_fields(data):
        stage = data["artifacts"][artifact]["stage"]
        rows.append("| `{}` 的 `{}` | {} | {} | {} | {} |".format(
            artifact, field, stage,
            contract["source"], contract["consumer"], contract["failure"]))
    return DECISION_HEADER + "\n".join(rows) + "\n"
```

- [ ] **Step 4: 实现 CLI**

创建 `scripts/render_views.py`：

```python
"""把产物契约骨架渲染成签入仓库的派生视图。

视图签入是为了让 agent 直接读到，渲染是为了它不会变成第二份手工同步的表。
`--check` 由 tests 与 CI 用，`--write` 由维护者用。

退出码：0 一致或已写入；2 不一致（此时跑 --write 重新生成）。
"""

import argparse
import sys
from pathlib import Path

from _contracts import ContractError, load, render_decision_points

DECISION_POINTS = Path(__file__).resolve().parents[1] / "references" / \
    "decision-points.md"


def main():
    parser = argparse.ArgumentParser(description="渲染骨架的派生视图")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="核对是否与骨架一致")
    group.add_argument("--write", action="store_true", help="重新生成")
    args = parser.parse_args()

    try:
        rendered = render_decision_points(load())
    except ContractError as exc:
        print(f"骨架不可用：{exc}", file=sys.stderr)
        return 2

    if args.write:
        DECISION_POINTS.write_text(rendered, encoding="utf-8")
        print(f"已生成 {DECISION_POINTS}")
        return 0

    current = DECISION_POINTS.read_text(encoding="utf-8") \
        if DECISION_POINTS.exists() else ""
    if current != rendered:
        print(f"{DECISION_POINTS} 与骨架不同步，跑 render_views.py --write",
              file=sys.stderr)
        return 2
    print("派生视图与骨架一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: 生成视图**

Run: `python3 scripts/render_views.py --write`
Expected: `已生成 .../references/decision-points.md`

- [ ] **Step 6: 跑测试确认通过**

Run: `python3 -m pytest tests/test_contracts.py -q`
Expected: PASS

- [ ] **Step 7: 跑全量**

Run: `python3 -m pytest tests/ -q`
Expected: 286 passed, 9 skipped

- [ ] **Step 8: 提交**

```bash
git add skill/repo-task-atk-test/scripts/render_views.py \
        skill/repo-task-atk-test/scripts/_contracts.py \
        skill/repo-task-atk-test/references/decision-points.md \
        skill/repo-task-atk-test/tests/test_contracts.py
git commit -m "$(cat <<'EOF'
feat: render the decision-point list from the spine

决策点清单 = 骨架里 owner=agent 的字段全集，渲染而非手写。

它把「全备度」变成可枚举的：一个决策没有判据来源就是一个洞，
不必等真机跑测来发现。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: 五张阶段作战卡 + 锁 L2

**Files:**
- Modify: `SKILL.md`（新增作战卡小节）
- Modify: `scripts/_contracts.py`（追加 `render_card`、`card_artifacts_in_skill`）
- Modify: `tests/test_contracts.py`（追加 `CardCoverageTest`）
- Modify: `tests/test_document_style.py:45-47`（行数阈值 180 → 360）

**Interfaces:**
- Produces：
  - `_contracts.render_card(data, stage) -> str` — 单阶段作战卡文本
  - `_contracts.card_artifacts_in_skill(text, stage) -> list[str]` — 从 SKILL.md 抽某阶段卡里列出的产物名

- [ ] **Step 1: 写失败测试**

在 `tests/test_contracts.py` 追加：

```python
class CardCoverageTest(unittest.TestCase):
    """锁 L2：卡里的产物与骨架该阶段的产物双向相等。

    单向包含不够：只查「骨架有的卡里都有」，卡里可以多出不存在的产物；
    只查「卡里有的骨架都有」，新增产物可以不进卡。两个方向都要。
    """

    def setUp(self):
        self.data = _contracts.load()
        self.skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

    def test_card_artifacts_equal_spine_artifacts(self):
        for stage in STAGES:
            with self.subTest(stage=stage):
                in_card = set(_contracts.card_artifacts_in_skill(self.skill, stage))
                in_spine = set(_contracts.artifacts_of(self.data, stage))
                self.assertEqual(in_card, in_spine)

    def test_every_stage_has_a_card(self):
        for stage in STAGES:
            with self.subTest(stage=stage):
                self.assertIn(f"### {stage} ", self.skill)

    def test_card_gate_names_match_the_spine(self):
        for stage, block in self.data["stages"].items():
            with self.subTest(stage=stage):
                card = _contracts.render_card(self.data, stage)
                for gate in block["gates"]:
                    self.assertIn(gate, card)

    def test_card_lookup_topics_are_queryable(self):
        import atk_lookup
        for block in self.data["stages"].values():
            for topic in block["lookup_topics"]:
                with self.subTest(topic=topic):
                    self.assertIn(topic, atk_lookup.TOPICS)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_contracts.py::CardCoverageTest -q`
Expected: FAIL，`AttributeError: module '_contracts' has no attribute 'render_card'`

- [ ] **Step 3: 实现卡渲染与卡解析**

在 `scripts/_contracts.py` 末尾追加：

```python
def render_card(data, stage):
    """渲染单阶段作战卡。

    卡不承载规范正文，只承载地图与雷区：要产出什么、规范在哪、哪一步会炸。
    它消灭的是三种摸索，不是替代 reference。
    """
    block = data["stages"][stage]
    lines = [f"### {stage} {block['name']}", "",
             f"核心：{block['core']}", "", "产出物（缺一样门禁不过）："]
    for name, spec in artifacts_of(data, stage).items():
        lines.append(f"- `{name}`　{spec['risk']}")
        route = [f"规范 {spec['spec']}"]
        if spec.get("template"):
            route.append(f"模板 {spec['template']}")
        if spec.get("producer"):
            route.append(f"量具 {spec['producer']}")
        lines.append("  " + "　".join(route))
    lines.append("")
    if block["lookup_topics"]:
        topics = " / ".join(block["lookup_topics"])
        lines.append(f"先查后写（`scripts/atk_lookup.py <主题>`）：{topics}")
    lines.append(f"出口门禁：{' / '.join(block['gates'])}")
    lines.append(f"本阶段不做：{'；'.join(block['forbidden'])}")
    return "\n".join(lines) + "\n"


def card_artifacts_in_skill(text, stage):
    """从 SKILL.md 抽出某阶段卡里列出的产物名。

    卡在 SKILL.md 里是人写的散文，这里只认「- `名字`　」这一种行首形态，
    形态变了就抽不到，测试会红——这是刻意的，卡的格式必须稳定。
    """
    wanted = f"### {stage} "
    names, inside = [], False
    for line in text.splitlines():
        if line.startswith("### "):
            inside = line.startswith(wanted)
            continue
        if inside and line.startswith("- `"):
            names.append(line.split("`")[1])
    return names
```

- [ ] **Step 4: 把五张卡写进 SKILL.md**

在 SKILL.md 的「## 阶段流程」表格之后、「## 进度呈现」之前，插入：

```markdown
## 阶段作战卡

进入某一阶段时跑 `scripts/mark_step.py <阶段号> <阶段名>`，它会再打印一次当阶段的卡。

卡只给地图和雷区，规范正文在 reference 里。
```

随后依次插入五张卡。每张卡的内容用下面这条命令生成后粘贴，保证与骨架逐字一致：

```bash
cd skill/repo-task-atk-test && python3 -c "
import sys; sys.path.insert(0, 'scripts')
import _contracts
data = _contracts.load()
for stage in ('S1','S2','S3','S4','S5'):
    print(_contracts.render_card(data, stage))
"
```

- [ ] **Step 5: 放宽 SKILL.md 行数阈值**

`tests/test_document_style.py:45-47` 现在断言 `<= 180`。五张卡约 +90 行，
Task 6 的正文瘦身会回吐一部分。把该用例改为：

```python
    def test_main_skill_stays_compact(self):
        # 上限来自 CLAUDE.md §3.3 的分层预算：正文 500 行是硬顶，
        # 360 是本轮加入五张阶段作战卡后的预算线，留出继续瘦身的余量。
        lines = SKILL_FILE.read_text(encoding="utf-8").splitlines()
        self.assertLessEqual(len(lines), 360)
```

- [ ] **Step 6: 跑测试确认通过**

Run: `python3 -m pytest tests/test_contracts.py tests/test_document_style.py -q`
Expected: PASS

- [ ] **Step 7: 跑全量**

Run: `python3 -m pytest tests/ -q`
Expected: 290 passed, 9 skipped

- [ ] **Step 8: 提交**

```bash
git add skill/repo-task-atk-test/SKILL.md \
        skill/repo-task-atk-test/scripts/_contracts.py \
        skill/repo-task-atk-test/tests/test_contracts.py \
        skill/repo-task-atk-test/tests/test_document_style.py
git commit -m "$(cat <<'EOF'
feat: add five stage briefing cards bound to the spine

锁 L2：卡里的产物与骨架该阶段的产物双向相等。单向包含挡不住
「新增产物不进卡」或「卡里写了不存在的产物」两种漂移。

卡回答零上下文 agent 的三个问题：要产出什么、规范在哪、哪一步会炸。

SKILL.md 行数预算 180 → 360，硬顶仍是 CLAUDE.md §3.3 的 500。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: 阶段入口渲染卡 + 正文按可执行性瘦身

**Files:**
- Modify: `scripts/mark_step.py`
- Modify: `tests/test_mark_step.py`
- Modify: `SKILL.md`（工作边界瘦身）

**Interfaces:**
- Consumes：`_contracts.load`、`_contracts.render_card`
- Produces：`mark_step.py -o <t.jsonl> <步号> <步名>` 在打点后打印该阶段作战卡；
  骨架读不出来时只打点不打卡，退出码保持 0（打点不能被卡渲染拖垮）

- [ ] **Step 1: 写失败测试**

在 `tests/test_mark_step.py` 的测试类里追加：

```python
    def test_marking_a_stage_prints_its_card(self):
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            timeline = Path(tmp) / "timeline.jsonl"
            result = subprocess.run(
                [sys.executable, str(SKILL_ROOT / "scripts" / "mark_step.py"),
                 "-o", str(timeline), "2", "用例生成"],
                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        # 卡在阶段入口再出现一次，才叫「在合适的时机知道要领」。
        self.assertIn("S2 用例生成", result.stdout)
        self.assertIn("出口门禁：", result.stdout)
        self.assertIn("<op>_decl.json", result.stdout)

    def test_marking_a_non_stage_step_prints_no_card(self):
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            timeline = Path(tmp) / "timeline.jsonl"
            result = subprocess.run(
                [sys.executable, str(SKILL_ROOT / "scripts" / "mark_step.py"),
                 "-o", str(timeline), "9", "临时打点"],
                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("出口门禁：", result.stdout)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_mark_step.py -q`
Expected: FAIL，`AssertionError: 'S2 用例生成' not found in ...`

- [ ] **Step 3: 让 mark_step 打卡**

在 `scripts/mark_step.py` 的 `import` 段末尾追加：

```python
from _contracts import ContractError, load, render_card
```

在模块顶部 `human` 之前追加：

```python
def stage_card(step):
    """步号对上阶段号就把当阶段作战卡再打一遍。

    卡写在 SKILL.md 是「开场一次性给全」，不等于「在合适的时机知道」。
    打点是阶段推进的必经动作，让它顺带把该阶段的作战指令送到眼前，
    只花一张卡的上下文，且由骨架渲染，不可能与规范脱节。
    """
    stage = f"S{step}"
    try:
        data = load()
    except ContractError:
        # 骨架坏了不该让打点失败——打点不参与任何门禁，卡是附加价值。
        return None
    if stage not in data["stages"]:
        return None
    return render_card(data, stage)
```

在 `main()` 末尾，把

```python
    append_mark(args.output, args.step, args.name)
    print(f"→ 第 {args.step} 步 {args.name}")
    return 0
```

替换为

```python
    append_mark(args.output, args.step, args.name)
    print(f"→ 第 {args.step} 步 {args.name}")
    card = stage_card(args.step)
    if card:
        print()
        print(card)
    return 0
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_mark_step.py -q`
Expected: PASS

- [ ] **Step 5: 正文按可执行性瘦身**

SKILL.md 的「工作边界」现在是 40 行单句祈使。把**量具能查的**那些换成一行指路，
**量具查不了的**留下。具体替换四处：

把

```markdown
一次 `atk case` 生成后冻结 YAML、约束和用例 JSON。
部署或跑测失败不得重新生成。
无效用例只剔除并留痕。
```

替换为

```markdown
一次 `atk case` 生成后冻结，部署或跑测失败不得重新生成；无效用例只剔除并留痕。
接线字段的受控改写走 `rewire_adapter.py`，由它证明只有接线变了。
```

把

```markdown
精度分母包含全部有效执行用例。
无效用例单列，不因归因或非有限输入改变分母。
证据不足时使用 `unknown`。
```

替换为

```markdown
精度分母由 `verdict.py` 算，包含全部有效执行用例；证据不足时使用 `unknown`。
```

把

```markdown
整张只有一个取值的输入测不出错：错的置换与对的置换逐元素相等，用例必然通过。
冻结输入时核这一条，取值范围按 dtype 定，不由声明认定。
```

替换为

```markdown
整张只有一个取值的输入测不出错，由 `freeze_inputs.py` 冻结时核，不由声明认定。
```

把

```markdown
所有 ATK 任务通过 `run_atk_task.py` 拉起。
```

替换为

```markdown
所有 ATK 任务通过 `run_atk_task.py` 拉起，并以 `source evidence/env.sh` 开头。
```

保留不动的是量具查不了的那几条：不读被测源码、不用实现补充验收知识、
推断项先标记请求确认、一条失败的归因只覆盖它自己。

- [ ] **Step 6: 跑全量**

Run: `python3 -m pytest tests/ -q`
Expected: 292 passed, 9 skipped

注：`evidence/env.sh` 由 Plan 2 的 B1 产出。本步只改 SKILL.md 的措辞，
不引入对该文件的代码依赖，因此不会红。

- [ ] **Step 7: 提交**

```bash
git add skill/repo-task-atk-test/scripts/mark_step.py \
        skill/repo-task-atk-test/tests/test_mark_step.py \
        skill/repo-task-atk-test/SKILL.md
git commit -m "$(cat <<'EOF'
feat: deliver the stage card at stage entry

打点是阶段推进的必经动作，让它顺带渲染当阶段作战卡——
卡写在 SKILL.md 只是开场给全，不等于在合适的时机知道要领。

同时按可执行性给正文瘦身：量具能查的规则移进量具，正文留一行指路；
量具查不了的（不读被测源码、推断项先标记）留下。
正文只写不可机检的纪律，是让剩下那几条真正被读进去的唯一办法。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: 锁 L3 —— 模板↔事实，并归并事故断言

**Files:**
- Modify: `assets/example/constraint.py`、`assets/example/function_example.py`、
  `assets/example/aclnn_executor.py`（补 `# [L0:key]` 引用）
- Modify: `tests/test_contracts.py`（追加 `TemplateFactRefTest`）
- Modify: `tests/test_document_style.py`（删除被不变量覆盖的事故断言）

**Interfaces:**
- Produces：`_contracts.l0_refs(text) -> list[str]` — 抽出源码里的 `# [L0:a.b.c]` 引用键

- [ ] **Step 1: 写失败测试**

在 `tests/test_contracts.py` 追加：

```python
class TemplateFactRefTest(unittest.TestCase):
    """锁 L3：模板里关于 ATK 行为的断言，必须指向 L0 里真实存在的键。

    模板与事实层各自演进是「四层各自正确、合起来失效」的第三条缝：
    L0 的值变了，模板照抄的旧行为不会有任何东西提醒你。
    """

    def setUp(self):
        import json
        self.facts = json.loads(
            (SKILL_ROOT / "references" / "atk-parameter-capabilities.json")
            .read_text(encoding="utf-8"))

    def _resolve(self, key):
        node = self.facts
        for part in key.split("."):
            self.assertIsInstance(node, dict, f"L0 引用 {key} 中途不是对象")
            self.assertIn(part, node, f"L0 里没有 {key}")
            node = node[part]
        return node

    def test_every_template_l0_ref_resolves(self):
        for template in sorted((SKILL_ROOT / "assets" / "example").glob("*.py")):
            text = template.read_text(encoding="utf-8")
            for key in _contracts.l0_refs(text):
                with self.subTest(template=template.name, key=key):
                    self._resolve(key)

    def test_templates_carry_at_least_one_fact_ref(self):
        # 三份契约相关的模板必须挂在 L0 上；纯示范脚本不强制。
        for name in ("constraint.py", "function_example.py", "aclnn_executor.py"):
            text = (SKILL_ROOT / "assets" / "example" / name) \
                .read_text(encoding="utf-8")
            with self.subTest(template=name):
                self.assertTrue(_contracts.l0_refs(text),
                                f"{name} 没有任何 L0 引用，事实变了不会有人知道")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_contracts.py::TemplateFactRefTest -q`
Expected: FAIL，`AttributeError: module '_contracts' has no attribute 'l0_refs'`

- [ ] **Step 3: 实现引用抽取**

在 `scripts/_contracts.py` 顶部 `import json` 之后追加 `import re`，末尾追加：

```python
L0_REF = re.compile(r"#\s*\[L0:([\w.]+)\]")


def l0_refs(text):
    """抽出源码注释里的 `# [L0:a.b.c]` 引用键。

    模板凡是断言 ATK 的某个行为，就必须挂一条引用；
    L0 的值变了，锁 L3 会把没跟着改的模板标红。
    """
    return L0_REF.findall(text)
```

- [ ] **Step 4: 给三份模板挂引用**

`assets/example/function_example.py`：把 `__call__` 里的注释行替换为

```python
        # args 来自 name 为空的输入，kwargs 来自 name 非空的输入。 # [L0:binding.inputs]
        # 所以 YAML 输入名必须等于基线函数形参名，否则这里直接 TypeError。
```

`assets/example/constraint.py`：在覆写 `case.inputs` 的那段之前加一行注释

```python
        # 复合组在运行期是 list[InputCaseConfig]，组长度下限为 1。 # [L0:group_min_length]
```

`assets/example/aclnn_executor.py`：在 typed null 那段之前加一行注释

```python
        # 缺省用 default token，运行期转成 None 再由后端转指针。 # [L0:default_token]
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python3 -m pytest tests/test_contracts.py::TemplateFactRefTest -q`
Expected: PASS

- [ ] **Step 6: 归并被不变量覆盖的事故断言**

`tests/test_document_style.py` 里下面三条已被锁 L2/L4 覆盖，删除：

- `test_documented_script_routes_exist` — 锁 L4 的 `test_declared_producer_exists` 覆盖
- `test_skill_routes_do_not_duplicate_reference_links` — 卡由骨架渲染，重复不再可能
- `test_removed_reference_names_do_not_return` — 锁 L2 的双向相等覆盖

保留其余 24 条并在文件头 docstring 追加一段，说明保留理由：

```python
"""SKILL.md 与 reference 的行文与结构约束。

本文件里剩下的断言按事故索引：一次跑测发现一条事实，就钉一条断言防止它被删。
它们的价值在防删（本项目两次在精简中删掉过真机验证过的修复），
局限在于结构上发现不了缺失——只知道「原来写的还在」。

发现缺失的职责在 tests/test_contracts.py 的四条结构不变量。
新增知识优先登记进 references/artifact-contracts.json，让不变量覆盖，
只有不变量表达不了的一次性事实才在这里钉断言。
"""
```

- [ ] **Step 7: 跑全量**

Run: `python3 -m pytest tests/ -q`
Expected: 291 passed, 9 skipped（+2 新增，−3 归并）

- [ ] **Step 8: 变异验证四条不变量**

逐条做，每条验完立刻还原：

```bash
cd skill/repo-task-atk-test

# L1：给量具加一个字段常量而不登记
python3 - <<'PY'
from pathlib import Path
p = Path("scripts/make_must_cover.py")
p.write_text(p.read_text(encoding="utf-8").replace(
    '"dims", "coverage_policy", "operator_class"}',
    '"dims", "coverage_policy", "operator_class", "mutant"}'), encoding="utf-8")
PY
python3 -m pytest tests/test_contracts.py::GaugeReflectionTest -q  # 预期 FAIL
git checkout scripts/make_must_cover.py

# L2：从 SKILL.md 的 S2 卡里删掉一样产物
# 手工删掉 S2 卡中 `- \`must_cover.json\`` 那两行
python3 -m pytest tests/test_contracts.py::CardCoverageTest -q     # 预期 FAIL
git checkout SKILL.md

# L3：把模板里的 L0 引用改成不存在的键
python3 - <<'PY'
from pathlib import Path
p = Path("assets/example/function_example.py")
p.write_text(p.read_text(encoding="utf-8").replace(
    "[L0:binding.inputs]", "[L0:binding.nonexistent]"), encoding="utf-8")
PY
python3 -m pytest tests/test_contracts.py::TemplateFactRefTest -q  # 预期 FAIL
git checkout assets/example/function_example.py

# L4：去掉一个 agent 字段的规范锚点
python3 - <<'PY'
import json
from pathlib import Path
p = Path("references/artifact-contracts.json")
d = json.loads(p.read_text(encoding="utf-8"))
d["artifacts"]["<op>_decl.json"]["spec"] = "references/不存在.md#随便"
p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
PY
python3 -m pytest tests/test_contracts.py::SpecAnchorTest -q       # 预期 FAIL
git checkout references/artifact-contracts.json
```

四条都必须转红。任一条没红说明该不变量是空断言，回到对应 Task 修。

- [ ] **Step 9: 跑全量确认已还原**

Run: `python3 -m pytest tests/ -q`
Expected: 291 passed, 9 skipped

- [ ] **Step 10: 提交**

```bash
git add skill/repo-task-atk-test/scripts/_contracts.py \
        skill/repo-task-atk-test/assets/example/ \
        skill/repo-task-atk-test/tests/test_contracts.py \
        skill/repo-task-atk-test/tests/test_document_style.py
git commit -m "$(cat <<'EOF'
feat: bind templates to the fact layer and fold incident assertions

锁 L3：模板里关于 ATK 行为的断言挂 [L0:key] 引用，事实变了模板没跟着改就红。

同时归并三条已被结构不变量覆盖的事故断言，并在 test_document_style 的
docstring 里写明剩下那些的定位：它们防删，不负责发现缺失；
发现缺失是四条不变量的职责。

四条不变量均已做变异验证：改量具/删卡条目/改引用/去锚点，各自转红。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**Spec coverage**

| 设计小节 | 落地 |
| --- | --- |
| §4.1 单一骨架 | Task 1 |
| §4.2 五张作战卡 | Task 5 |
| §4.3 mark_step 准时送达 | Task 6 |
| §4.4 决策点清单 | Task 4 |
| §4.5 四条不变量 + 事故断言归并 | Task 2（L4）、3（L1）、5（L2）、7（L3 与归并） |
| §4.6 规范按可执行性分层 | Task 6 Step 5 |
| §4.7 `env.sh` | Plan 2 / B1（本计划 Task 6 只改措辞，不建代码依赖） |
| §4.8 `knowledge_gaps` | Plan 2 / B 系列；本计划在两处未知键报错里预埋了指路 |
| §5 适配器专章 | Plan 2 / B4 |
| §6 A1–A6 | A1=T1、A2=T5、A3=T6、A4=T4、A5=T2/3/5/7、A6=T6 |
| §8 验证方式前三条 | Task 7 Step 8 的变异验证 + 各 Task 的全量跑 |

S3–S5 登记到产物级，字段级一律 `fields_not_applicable`——它们要么是脚本自证的报告，
要么是 ATK 日志，没有 agent 要填的字段。这是显式标注，不是遗漏。
唯一的 agent 产出是验收报告，规范在 `reporting.md`，属自由格式而非字段契约。

**Placeholder scan**：无 TBD/TODO；每个改代码的步骤都给了完整代码；
每条命令都给了预期输出。

**Type consistency**：`_contracts` 对外符号在各 Task 中一致——
`load`、`ContractError`、`OWNERS`、`artifacts_of`、`agent_fields`、`resolve_anchor`、
`render_decision_points`、`render_card`、`card_artifacts_in_skill`、`l0_refs`。
`make_must_cover.DECL_KEYS`、`make_yaml.YAML_HEADER_KEYS`/`DERIVED_KEYS`/`CHANNELS`
在 Task 3 定义、在同 Task 的反射测试中使用，命名前后一致。

**测试计数**：268 → 276（T1）→ 280（T2）→ 284（T3）→ 286（T4）→ 290（T5）
→ 292（T6）→ 291（T7，+2−3）。执行时以实际输出为准，数字对不上不是失败，
但要确认差额来自本 Task 的增删而非误删。
