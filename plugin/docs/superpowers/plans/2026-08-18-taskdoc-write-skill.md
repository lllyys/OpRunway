# 社区算子任务书撰写 Skill 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建一个 skill，用基于模板和规则的结构化追问流程，辅助需求方产出合规、无歧义、可验收的社区算子开发任务书，出口有一道不可绕过的质量门。

**Architecture:** Markdown 是唯一真相，`taskdoc-elements.json` 是唯一骨架。追问流程、脚手架渲染、质量门、待确认清单全部从骨架派生。质量门写 parser 按固定章节号与表格列反解 md 后校验，分五层（结构、一致性、明确性、拍板留痕、双受众），通过时写一枚含 md sha256 的封条。

**Tech Stack:** Python 3.14 标准库（`re`、`json`、`hashlib`、`pathlib`、`argparse`、`unittest`）。不依赖 torch、numpy、pyyaml、ATK。测试用 `unittest` 写、`pytest` 跑，与现有验收 skill 一致。

## Global Constraints

- 设计依据：`docs/superpowers/specs/2026-08-18-taskdoc-write-skill-design.md`。本计划的编号 `§x` 指该 spec 的章节；形如 `§2.4` 且出现在任务书语境时指**任务书模板的章节**，按 spec 的记号约定。
- 新 skill 根目录：`skill/repo-task-doc-write/`。所有相对路径以此为基准，除非写明 `docs/`。
- 行文遵守 `.claude/rules/prose-style.md`，它随仓根 `CLAUDE.md` 自动导入。新 skill 的文档基线为 0，写进来就必须合规。
- 四条红线（spec §2.5）：A 全部推断项必须人拍板并留答复原话；B 社区模板章节号只做加法不重编号；C 门禁不可绕过，`gate_pass.json` 用 sha256 封住当前 md；D 黄金任务书必须真的返回 0。
- 上游素材只读：`docs/development/taskdoc-source/origin/` 下的文件是拷贝，发现缺陷不就地修，记进 `defect-map.md`，修复产物另存 `references/`。
- 退出码约定：`0` 全过，`2` 判据不满足（内容问题），`3` 结构解析失败。
- 测试命令：`python3 -m pytest skill/repo-task-doc-write/tests/ -q`（新 skill 不需要 `PYTHONPATH=third_party/ATK`）。
- 全仓回归：`PYTHONPATH=third_party/ATK python3 -m pytest skill/ -q`。当前基线 `22 failed, 758 passed, 13 skipped`，22 条既存失败见仓根 `CLAUDE.md` §8。新增任务不得让 failed 数上升。
- 上游素材来源路径（拷贝用）：`/Users/justbin/project/CANN/repo-task/task-docs/community_task_docs/`。

---

## 文件结构

**开发侧（不随 skill 发布）**

| 文件 | 职责 |
| --- | --- |
| `docs/development/taskdoc-source/README.md` | 开发者入口，链到 `references/` 的修复版 |
| `docs/development/taskdoc-source/origin/task_doc_templete_v3.0.md` | 上游模板只读归档 |
| `docs/development/taskdoc-source/origin/task_doc_example_v3.0.md` | 上游样例只读归档 |
| `docs/development/taskdoc-source/origin/checklist-14.md` | `任务书人工checklist.xlsx` 转写 |
| `docs/development/taskdoc-source/defect-map.md` | 逐条缺陷 → 对应判据 |
| `docs/development/taskdoc-source/repair-log.md` | 修复决策与理由 |

**skill 本体（唯一发布物）**

| 文件 | 职责 |
| --- | --- |
| `SKILL.md` | 入口，五阶段流程与硬要求 |
| `CLAUDE.md` | 本 skill 的开发规则，四条红线 + 引用仓根规范 |
| `references/taskdoc-elements.json` | 唯一骨架，约 40 条要素 |
| `references/task-doc-template.md` | 修复版模板（v3.0+） |
| `references/golden-task-doc.md` | 黄金样例，防假红 + 写作参考 |
| `references/vague-words.json` | 模糊词表与替代写法 |
| `references/dtype-vocab.json` | 数据类型、dtype、排布格式词表 |
| `references/manual-checklist.md` | 从骨架派生，仍需人读的那几条 |
| `references/experimental_standard.md` | 共享事实副本 |
| `references/random-operator-signals.json` | 共享事实副本 |
| `scripts/_taskdoc_parser.py` | md 反解：章节、表格、代码块 |
| `scripts/_signature.py` | 从接口定义代码块抽参数名 |
| `scripts/_checks.py` | 全部判据实现与注册表 |
| `scripts/check_taskdoc.py` | 唯一不可绕过的门，退出码 0/2/3 |
| `scripts/next_questions.py` | 算出还缺哪些拍板项，按风险分批 |
| `scripts/make_taskdoc.py` | 从骨架渲染 md 脚手架 |
| `scripts/render_views.py` | 骨架派生 `manual-checklist.md` |
| `scripts/probe_baseline.py` | 可选辅助证据，无 torch 优雅降级 |

**工作目录产物（跑 skill 时生成，不进仓）**

```
<工作区>/taskdoc-<Op>/
├── <Op>_task_doc.md            唯一真相
└── evidence/
    ├── decisions.json          拍板留痕
    ├── acceptance_map.json     反填的验收约束表
    └── gate_pass.json          门禁封条（含 md 的 sha256）
```

---

## Task 1: 素材归档与缺陷对照表

**Files:**
- Create: `docs/development/taskdoc-source/origin/task_doc_templete_v3.0.md`
- Create: `docs/development/taskdoc-source/origin/task_doc_example_v3.0.md`
- Create: `docs/development/taskdoc-source/origin/checklist-14.md`
- Create: `docs/development/taskdoc-source/defect-map.md`
- Create: `docs/development/taskdoc-source/README.md`
- Create: `skill/repo-task-doc-write/tests/test_source_archive.py`

**Interfaces:**
- Consumes: 无
- Produces: `defect-map.md` 里的缺陷编号 `D01`-`D12`，后续任务的 fixture 与判据都按这些编号命名与追溯。

- [ ] **Step 1: 拷贝上游素材到只读归档**

```bash
mkdir -p docs/development/taskdoc-source/origin
SRC=/Users/justbin/project/CANN/repo-task/task-docs/community_task_docs
cp "$SRC/task_doc_templete_v3.0.md" docs/development/taskdoc-source/origin/
cp "$SRC/task_doc_example_v3.0(SlidingTileAttention_task_doc).md" \
   docs/development/taskdoc-source/origin/task_doc_example_v3.0.md
wc -l docs/development/taskdoc-source/origin/*.md
```

Expected: `task_doc_templete_v3.0.md` 85 行，`task_doc_example_v3.0.md` 185 行。

- [ ] **Step 2: 把 xlsx 转写成 checklist-14.md**

```bash
python3 - <<'EOF' > docs/development/taskdoc-source/origin/checklist-14.md
import zipfile, xml.etree.ElementTree as ET
NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
T = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t'
src = '/Users/justbin/project/CANN/repo-task/task-docs/任务书人工checklist.xlsx'
z = zipfile.ZipFile(src)
shared = [''.join(t.text or '' for t in si.iter(T))
          for si in ET.fromstring(z.read('xl/sharedStrings.xml'))]
sheet = ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
print('# 任务书人工 checklist（14 条）\n')
print('来源：`task-docs/任务书人工checklist.xlsx` Sheet1，拷贝日期 2026-08-18。')
print('Sheet2 与 Sheet3 为空。源不在本仓，不做自动同步。\n')
print('| 序号 | 检查项 | 检查标准 |')
print('| --- | --- | --- |')
for row in list(sheet.iter('{%s}row' % NS['m']))[1:]:
    cells = []
    for c in row:
        v = c.find('m:v', NS)
        cells.append(shared[int(v.text)] if v is not None and c.get('t') == 's'
                     else (v.text if v is not None else ''))
    if len(cells) >= 3:
        print('| %s | %s | %s |' % (cells[0], cells[1], cells[2].replace('\n', '<br>')))
EOF
head -8 docs/development/taskdoc-source/origin/checklist-14.md
```

Expected: 表头加 14 行，第 1 行是「检查章节完整性」。

- [ ] **Step 3: 写 defect-map.md**

内容如下，逐条给出缺陷编号、位置、原文、为什么是缺陷、对应判据名。判据名会在 Task 6-10 实现，这里先定名。

````markdown
# 上游样例与模板的缺陷对照表

对照对象是 `origin/` 下的两份只读归档。发现缺陷不就地修，修复产物在
`skill/repo-task-doc-write/references/` 下另存。

每条判据都必须能指回这张表里的一条缺陷。指不回去的判据是凭空设计的，
要么删掉，要么在这里补上它防的是什么。

## 样例缺陷

| 编号 | 位置 | 原文 | 为什么是缺陷 | 判据 |
| --- | --- | --- | --- | --- |
| D01 | 样例 §2.3 / §2.4 | §2.3 有 `windowSizeLen`，§2.4 表格无此行 | 漏参，开发者不知道要实现它，验收侧构造不出调用 | `signature_matches_param_table` |
| D02 | 样例 §2.3 / §2.4 | §2.3 `windowSize`，§2.4 `window_size` | 违反 checklist 第 7 条「要和接口定义章节中的参数一致」 | `signature_matches_param_table` |
| D03 | 样例 §2.4 q/k/v | 值域 `(-∞,∞)` | fp16 表示不了，且生成不出数据；与 §3.5 的 `(0,1)正态分布` 矛盾 | `no_unbounded_range` + `range_matches_distribution` |
| D04 | 样例 §2.4 / §2.1 | §2.4 `text_length ∈ (0,∞)`，§2.1 说 `has_text=false 时按 0 处理` | 0 不在声明值域内，边界未定义 | `range_matches_algorithm` |
| D05 | 样例 §2.4 window_size | 描述说奇数窗口，值域写 `(0,∞)`，§3.5 写 `[1,100] 平均分布` | 奇数约束三处都没落进可执行表达 | `range_matches_distribution` |
| D06 | 样例 §3.3 | 「与 0.8 倍【FastVideo + GPU(A100)】持平」 | 「持平」无判据，是 `≥` 还是 `±容差` 没定义 | `perf_criterion_has_comparator` |
| D07 | 样例 §3.1 / §3.3 | §3.1 写「A2系列产品」，§3.3 只给 910B3 | 模板 §3.3 与 checklist 第 10 条都要求 A2 明确 910B3、910B4 | `models_covered_by_perf_table` |
| D08 | 样例 §2.4 异常行为列 | 8 行全是「触发参数校验报错」 | 给不出任何一个具体非法输入，验收侧构造不出错误用例 | `error_column_not_uniform` |
| D09 | 样例 §3.1 | 只有 CANN 版本 | 违反 checklist 第 9 条「其他依赖三方软件版本」；GPU 标杆无驱动与 CUDA 版本，性能对标不可复现 | `env_lists_third_party_versions` |
| D10 | 样例 §3.5 | `[自测用例和测试指导](测试用例文件夹路径)` | 未替换的占位符，同时是隐藏跳转链接，违反 checklist 顶部「链接必须裸露」 | `no_placeholder` + `no_inline_link` |
| D11 | 样例全文 | 无「非连续 Tensor」声明 | 验收侧 `intake.md` 明写无此列记为未声明，每份任务书都会挂待确认项 | `section_present:2.5` |
| D12 | 样例 §3.5 | 写了「(0,1)正态分布」，命中随机算子信号词，但无对比策略 | 验收侧 `derive_interface.py` 会退出码 2 拦住 | `random_strategy_when_signaled` |

## 模板缺陷

| 编号 | 位置 | 原文 | 为什么是缺陷 | 修复方向 |
| --- | --- | --- | --- | --- |
| T01 | 模板 §3.2 | 「**建议**参考生态算子开源精度标准」 | 把硬标准写成软建议，样例里的模糊写法是它纵容的 | 改成「必须满足」，并要求逐 dtype 抄出阈值表 |
| T02 | 模板 §2.4 表头 | 每列给的是疑问句（「参数的shape是多少？」） | 疑问句不是判据，填的人只能凭感觉 | 每列改成可判定的填写要求 |
| T03 | 模板全文 | 无 §2.5 算子实现约束 | 非连续、broadcast、dynamic shape、原地语义、确定性、空 Tensor 无处声明 | 新增 §2.5，六个子项 |
| T04 | 模板 §3.2 | 无随机算子分支 | 随机类算子的精度对比策略无处写 | §3.2 增条件子项 |
| T05 | 上游 `README.md` | 声称模板是 v2.3，目录里是 v3.0 | 文档维护脱节 | 归档时记下，不修上游 |
````

- [ ] **Step 4: 写 README.md 入口**

```markdown
# 任务书素材与修复对照

## 这里放什么

- `origin/` —— 上游素材的只读拷贝，来源
  `task-docs/community_task_docs/`，拷贝日期 2026-08-18。源不在本仓，不做自动同步。
- `defect-map.md` —— 逐条缺陷与对应判据，判据的追溯依据。
- `repair-log.md` —— 修复决策与理由。

## 修复版在哪

修复后的模板与黄金样例只有一份，在 skill 的 references 下：

- `../../../skill/repo-task-doc-write/references/task-doc-template.md`
- `../../../skill/repo-task-doc-write/references/golden-task-doc.md`

这里不放副本。两份内容相同的文件迟早会漂。

## 只读纪律

`origin/` 下发现缺陷不要就地修。在 `defect-map.md` 记一条，修复产物另存到
references。这样「上游原样是什么」和「我们认为该是什么」永远分得开，
上游改版时能逐条重放。
```

- [ ] **Step 5: 写归档完整性测试**

```python
# skill/repo-task-doc-write/tests/test_source_archive.py
"""归档只读纪律与缺陷对照表的结构约束。"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE = REPO_ROOT / "docs" / "development" / "taskdoc-source"
ORIGIN = SOURCE / "origin"
DEFECT_MAP = SOURCE / "defect-map.md"


class SourceArchiveTest(unittest.TestCase):
    def test_origin_files_are_archived(self):
        for name in ("task_doc_templete_v3.0.md",
                     "task_doc_example_v3.0.md",
                     "checklist-14.md"):
            with self.subTest(name=name):
                self.assertTrue((ORIGIN / name).is_file(), f"{name} 未归档")

    def test_checklist_has_fourteen_rows(self):
        text = (ORIGIN / "checklist-14.md").read_text(encoding="utf-8")
        rows = [l for l in text.splitlines()
                if re.match(r"^\|\s*\d+\s*\|", l)]
        self.assertEqual(14, len(rows), "checklist 应为 14 条")

    def test_defect_map_covers_twelve_sample_defects(self):
        text = DEFECT_MAP.read_text(encoding="utf-8")
        ids = re.findall(r"\|\s*(D\d{2})\s*\|", text)
        self.assertEqual([f"D{n:02d}" for n in range(1, 13)], ids)

    def test_defect_map_covers_five_template_defects(self):
        text = DEFECT_MAP.read_text(encoding="utf-8")
        ids = re.findall(r"\|\s*(T\d{2})\s*\|", text)
        self.assertEqual([f"T{n:02d}" for n in range(1, 6)], ids)

    def test_every_defect_names_a_gate_or_repair(self):
        # 最后一列不得为空：缺陷必须指向一条判据或一个修复方向。
        text = DEFECT_MAP.read_text(encoding="utf-8")
        empty = []
        for line in text.splitlines():
            if not re.match(r"^\|\s*[DT]\d{2}\s*\|", line):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 5 or not cells[-1]:
                empty.append(line[:40])
        self.assertEqual([], empty)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 6: 跑测试确认通过**

Run: `python3 -m pytest skill/repo-task-doc-write/tests/test_source_archive.py -q`
Expected: `5 passed`

- [ ] **Step 7: 提交**

```bash
git add docs/development/taskdoc-source skill/repo-task-doc-write/tests/test_source_archive.py
git commit -m "feat: 归档上游任务书素材并建立缺陷对照表

origin/ 是上游模板、样例与 checklist 的只读拷贝。defect-map.md 逐条记下
12 处样例缺陷与 5 处模板缺陷，每条指向一个判据名或修复方向。

判据的追溯依据在这张表：指不回一条真实缺陷的判据是凭空设计的。"
```

---

## Task 2: 要素骨架与不变量

**Files:**
- Create: `skill/repo-task-doc-write/references/taskdoc-elements.json`
- Create: `skill/repo-task-doc-write/tests/test_elements_spine.py`

**Interfaces:**
- Consumes: Task 1 的 `defect-map.md` 判据名
- Produces: 骨架 JSON 的 schema —— 顶层键 `schema_version`、`template_version`、`sections`、`check_registry`、`elements`；每条要素的键 `section`、`name`、`form`、`audience`、`required`、`condition`、`decision_owner`、`agent_may_propose`、`checks`、`vague_forbidden`、`checklist_items`、`failure`。Task 6-13 全部读它。

- [ ] **Step 1: 写骨架不变量测试（先失败）**

```python
# skill/repo-task-doc-write/tests/test_elements_spine.py
"""骨架的结构不变量。骨架是唯一可编辑对象，视图从它派生。"""

import json
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SPINE = SKILL_ROOT / "references" / "taskdoc-elements.json"

REQUIRED_KEYS = {
    "section", "name", "form", "audience", "required", "condition",
    "decision_owner", "agent_may_propose", "checks", "vague_forbidden",
    "checklist_items", "failure",
}
FORMS = {"table_column", "section_text", "table", "code_block", "fixed"}
AUDIENCES = {"developer", "acceptance"}
REQUIREDNESS = {"always", "conditional", "optional"}
OWNERS = {"human", "agent", "template"}


def spine():
    return json.loads(SPINE.read_text(encoding="utf-8"))


class ElementsSpineTest(unittest.TestCase):
    def setUp(self):
        self.data = spine()
        self.elements = self.data["elements"]

    def test_every_element_declares_all_keys(self):
        missing = []
        for key, element in self.elements.items():
            gap = REQUIRED_KEYS - set(element)
            if gap:
                missing.append(f"{key}: 缺 {sorted(gap)}")
        self.assertEqual([], missing)

    def test_enumerated_fields_use_known_values(self):
        bad = []
        for key, element in self.elements.items():
            if element["form"] not in FORMS:
                bad.append(f"{key}.form={element['form']}")
            if element["required"] not in REQUIREDNESS:
                bad.append(f"{key}.required={element['required']}")
            if element["decision_owner"] not in OWNERS:
                bad.append(f"{key}.decision_owner={element['decision_owner']}")
            if not element["audience"] or set(element["audience"]) - AUDIENCES:
                bad.append(f"{key}.audience={element['audience']}")
        self.assertEqual([], bad)

    def test_sections_are_declared(self):
        known = set(self.data["sections"])
        unknown = sorted({e["section"] for e in self.elements.values()} - known)
        self.assertEqual([], unknown)

    def test_conditional_elements_carry_a_condition(self):
        bad = []
        for key, element in self.elements.items():
            conditional = element["required"] == "conditional"
            if conditional and not element["condition"]:
                bad.append(f"{key}: conditional 但 condition 为空")
            if not conditional and element["condition"]:
                bad.append(f"{key}: 非 conditional 却给了 condition")
        self.assertEqual([], bad)

    def test_checks_are_registered(self):
        registry = set(self.data["check_registry"])
        unknown = []
        for key, element in self.elements.items():
            for check in element["checks"]:
                name = check.split(":", 1)[0]
                if name not in registry:
                    unknown.append(f"{key}: {check}")
        self.assertEqual([], unknown)

    def test_checklist_items_are_in_range(self):
        bad = []
        for key, element in self.elements.items():
            for item in element["checklist_items"]:
                if not 1 <= item <= 14:
                    bad.append(f"{key}: checklist {item}")
        self.assertEqual([], bad)

    def test_every_element_states_a_failure_mode(self):
        # failure 回答「写错了怎么炸」。空的或写成「不正确」等于没写。
        bad = [key for key, element in self.elements.items()
               if len(element["failure"]) < 8]
        self.assertEqual([], bad)

    def test_human_owned_elements_dominate(self):
        # 红线 A：关键要素由人拍板。骨架里 human 占比低于七成说明写偏了。
        owners = [e["decision_owner"] for e in self.elements.values()]
        human = owners.count("human")
        self.assertGreaterEqual(human / len(owners), 0.7)

    def test_all_fourteen_checklist_items_are_claimed(self):
        claimed = {i for e in self.elements.values() for i in e["checklist_items"]}
        self.assertEqual(set(range(1, 15)), claimed)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest skill/repo-task-doc-write/tests/test_elements_spine.py -q`
Expected: FAIL，`FileNotFoundError` 指向 `taskdoc-elements.json`

- [ ] **Step 3: 写骨架 JSON**

顶层结构如下。`check_registry` 列出全部判据名，与 `defect-map.md` 的判据列一致，
Task 6-10 逐个实现。

```json
{
  "schema_version": 1,
  "template_version": "v3.0+",
  "sections": ["1", "2.1", "2.2", "2.3", "2.4", "2.5",
               "3.1", "3.2", "3.3", "3.4", "3.5",
               "4", "5", "6", "7", "8"],
  "check_registry": [
    "nonempty", "section_present", "no_placeholder", "no_inline_link",
    "table_header_matches", "enum_value",
    "signature_matches_param_table", "range_matches_distribution",
    "range_matches_algorithm", "models_covered_by_perf_table",
    "dtypes_covered_by_threshold_table", "thresholds_match_standard",
    "params_covered_by_generation_table",
    "no_unbounded_range", "no_vague_word", "perf_criterion_has_comparator",
    "error_column_not_uniform", "per_dtype_threshold",
    "env_lists_third_party_versions", "random_strategy_when_signaled",
    "human_reply_recorded", "deliverables_complete", "pr_target_is_a_path"
  ],
  "elements": { }
}
```

`elements` 逐条写。以下给出每个章节的完整条目，字段值照抄。

**§1 任务概述（3 条）**

```json
"1.background": {
  "section": "1", "name": "任务背景", "form": "section_text",
  "audience": ["developer"], "required": "always", "condition": null,
  "decision_owner": "human", "agent_may_propose": false,
  "checks": ["nonempty", "no_placeholder", "no_inline_link"],
  "vague_forbidden": true, "checklist_items": [3],
  "failure": "开发者不知道这个算子从哪来、为什么要做，无法判断实现取舍"
},
"1.language": {
  "section": "1", "name": "开发语言与工程", "form": "section_text",
  "audience": ["developer"], "required": "always", "condition": null,
  "decision_owner": "human", "agent_may_propose": false,
  "checks": ["nonempty"], "vague_forbidden": true, "checklist_items": [3],
  "failure": "开发者不知道用 Ascend C 还是别的，起手就可能选错技术栈"
},
"1.repo": {
  "section": "1", "name": "算子相关代码仓", "form": "section_text",
  "audience": ["developer"], "required": "always", "condition": null,
  "decision_owner": "human", "agent_may_propose": false,
  "checks": ["nonempty", "no_inline_link"], "vague_forbidden": false,
  "checklist_items": [3],
  "failure": "开发者找不到参考实现与提交位置"
}
```

**§2.1 功能实现要求（4 条）**

```json
"2.1.operator_name": {
  "section": "2.1", "name": "算子名与接口名", "form": "section_text",
  "audience": ["developer", "acceptance"], "required": "always",
  "condition": null, "decision_owner": "human", "agent_may_propose": true,
  "checks": ["nonempty"], "vague_forbidden": true, "checklist_items": [4],
  "failure": "验收侧不知道要提交验收的是哪个接口名"
},
"2.1.formula": {
  "section": "2.1", "name": "数学公式", "form": "section_text",
  "audience": ["developer", "acceptance"], "required": "always",
  "condition": null, "decision_owner": "human", "agent_may_propose": true,
  "checks": ["nonempty", "no_placeholder"], "vague_forbidden": true,
  "checklist_items": [4],
  "failure": "开发者要去猜算子到底算什么，只写「参考 xxx」等于没写"
},
"2.1.algorithm": {
  "section": "2.1", "name": "算法说明", "form": "section_text",
  "audience": ["developer"], "required": "always", "condition": null,
  "decision_owner": "human", "agent_may_propose": true,
  "checks": ["nonempty"], "vague_forbidden": true, "checklist_items": [4],
  "failure": "边界与分支行为没交代，开发者按自己的理解实现"
},
"2.1.baseline": {
  "section": "2.1", "name": "对标基线接口", "form": "section_text",
  "audience": ["developer", "acceptance"], "required": "always",
  "condition": null, "decision_owner": "human", "agent_may_propose": true,
  "checks": ["nonempty", "no_inline_link"], "vague_forbidden": true,
  "checklist_items": [4],
  "failure": "验收侧不知道拿什么当真值，只能自己挑一个"
}
```

**§2.2 与 §2.3（2 条）**

```json
"2.2.project_mode": {
  "section": "2.2", "name": "算子工程模式", "form": "section_text",
  "audience": ["developer", "acceptance"], "required": "always",
  "condition": null, "decision_owner": "human", "agent_may_propose": true,
  "checks": ["nonempty", "enum_value:project_mode"], "vague_forbidden": true,
  "checklist_items": [6],
  "failure": "验收侧的执行后端由工程模式唯一推导，模式不明就跑不起来"
},
"2.3.signature": {
  "section": "2.3", "name": "接口定义", "form": "code_block",
  "audience": ["developer", "acceptance"], "required": "always",
  "condition": null, "decision_owner": "human", "agent_may_propose": true,
  "checks": ["nonempty", "signature_matches_param_table"],
  "vague_forbidden": false, "checklist_items": [7],
  "failure": "签名不全或与参数表对不上，验收侧构造不出调用"
}
```

**§2.4 参数说明（9 条，逐列）**

九列的 `section` 都是 `2.4`，`form` 都是 `table_column`，
`decision_owner` 都是 `human`，`checklist_items` 都是 `[7]`。逐列差异如下：

| 键 | name | audience | checks | vague_forbidden | failure |
| --- | --- | --- | --- | --- | --- |
| `2.4.param_name` | 参数名 | dev+acc | `nonempty`, `signature_matches_param_table` | false | 与接口定义对不上，开发者不知道实现哪个参数 |
| `2.4.direction` | 输入／输出/属性 | dev+acc | `nonempty`, `enum_value:direction` | false | 输出是独立、元组还是原地不明，调用形态就错 |
| `2.4.description` | 描述 | dev | `nonempty` | true | 参数含义靠猜 |
| `2.4.data_type` | 数据类型 | dev+acc | `nonempty`, `enum_value:data_type` | false | 验收侧不知道该构造 tensor 还是 attr |
| `2.4.dtype` | dtype类型 | dev+acc | `nonempty`, `enum_value:dtype`, `dtypes_covered_by_threshold_table` | false | 要测哪些 dtype 全凭猜，覆盖面无依据 |
| `2.4.format` | 数据排布格式 | dev+acc | `nonempty`, `enum_value:format` | false | 排布不明，非连续与转置行为无从判断 |
| `2.4.shape` | 维度(shape) | dev+acc | `nonempty` | true | shape 对齐约束与广播规则不明，用例造不出来 |
| `2.4.value_range` | 值域范围 | dev+acc | `nonempty`, `no_unbounded_range`, `range_matches_distribution`, `range_matches_algorithm` | true | 开发者不知道要处理到什么范围，验收侧生成不出数据 |
| `2.4.error_behavior` | 异常行为 | dev+acc | `nonempty`, `error_column_not_uniform` | true | 给不出具体非法输入，错误用例构造不出来 |

**§2.5 算子实现约束（6 条，新增章节）**

六条的 `section` 都是 `2.5`，`form` 都是 `section_text`，
`audience` 都是 `["developer", "acceptance"]`，`required` 都是 `always`，
`condition` 都是 `null`，`decision_owner` 都是 `human`，
`agent_may_propose` 都是 `true`，`vague_forbidden` 都是 `true`，
`checklist_items` 都是 `[1]`，`checks` 都是 `["nonempty", "section_present:2.5"]`。

| 键 | name | failure |
| --- | --- | --- |
| `2.5.non_contiguous` | 非连续 Tensor 支持 | 验收侧记为未声明，挂一个待确认项；开发者不知道要不要处理 stride |
| `2.5.broadcast` | broadcast 规则 | 输入 shape 不等时的行为未定义 |
| `2.5.dynamic_shape` | dynamic shape 要求 | 开发者不知道要不要支持编译期未知 shape |
| `2.5.inplace_view` | 原地与视图语义 | 输出是否复用输入内存不明，调用方可能踩内存 |
| `2.5.deterministic` | 确定性计算要求 | 同输入两次结果是否必须一致没交代，验收侧无从判定 |
| `2.5.empty_tensor` | 空 Tensor 与 0 维处理 | 社区任务书最高频漏项，开发者不知道要不要处理，验收侧不知道该不该构造 |

**§3 验收标准（12 条）**

```json
"3.1.hardware": {
  "section": "3.1", "name": "适配硬件与具体型号", "form": "section_text",
  "audience": ["developer", "acceptance"], "required": "always",
  "condition": null, "decision_owner": "human", "agent_may_propose": false,
  "checks": ["nonempty", "models_covered_by_perf_table"],
  "vague_forbidden": true, "checklist_items": [9],
  "failure": "只写产品系列名，性能要求落不到具体型号上"
},
"3.1.cann_version": {
  "section": "3.1", "name": "CANN 版本", "form": "section_text",
  "audience": ["developer", "acceptance"], "required": "always",
  "condition": null, "decision_owner": "human", "agent_may_propose": false,
  "checks": ["nonempty"], "vague_forbidden": true, "checklist_items": [9],
  "failure": "开发者装错版本，接口对不上"
},
"3.1.third_party": {
  "section": "3.1", "name": "三方软件版本", "form": "section_text",
  "audience": ["developer", "acceptance"], "required": "always",
  "condition": null, "decision_owner": "human", "agent_may_propose": false,
  "checks": ["nonempty", "env_lists_third_party_versions"],
  "vague_forbidden": true, "checklist_items": [9],
  "failure": "torch 与 torch_npu 版本不明，基线跑不出同样的数；GPU 标杆无驱动版本则性能对标不可复现"
},
"3.2.reference_api": {
  "section": "3.2", "name": "精度对标接口", "form": "section_text",
  "audience": ["acceptance"], "required": "always", "condition": null,
  "decision_owner": "human", "agent_may_propose": true,
  "checks": ["nonempty"], "vague_forbidden": true, "checklist_items": [10],
  "failure": "验收侧不知道跟谁比"
},
"3.2.threshold_table": {
  "section": "3.2", "name": "逐 dtype 阈值表", "form": "table",
  "audience": ["developer", "acceptance"], "required": "always",
  "condition": null, "decision_owner": "human", "agent_may_propose": true,
  "checks": ["nonempty", "per_dtype_threshold", "thresholds_match_standard",
             "dtypes_covered_by_threshold_table"],
  "vague_forbidden": true, "checklist_items": [10],
  "failure": "只写一句「满足生态标准」，验收侧要自己去翻标准并挑一档，挑错了算谁的"
},
"3.2.verdict_formula": {
  "section": "3.2", "name": "精度判定公式", "form": "section_text",
  "audience": ["acceptance"], "required": "always", "condition": null,
  "decision_owner": "human", "agent_may_propose": true,
  "checks": ["nonempty"], "vague_forbidden": true, "checklist_items": [10],
  "failure": "有阈值但没说怎么算通过，逐用例还是整体通过率不明"
},
"3.2.random_strategy": {
  "section": "3.2", "name": "随机算子精度对比策略", "form": "section_text",
  "audience": ["acceptance"], "required": "conditional",
  "condition": "random_signal_hit", "decision_owner": "human",
  "agent_may_propose": true,
  "checks": ["random_strategy_when_signaled"], "vague_forbidden": true,
  "checklist_items": [10],
  "failure": "随机算子拿去验收会被 derive_interface.py 退出码 2 拦住，整轮验收开不了工"
},
"3.3.baseline_env": {
  "section": "3.3", "name": "性能标杆接口与环境", "form": "section_text",
  "audience": ["developer", "acceptance"], "required": "always",
  "condition": null, "decision_owner": "human", "agent_may_propose": false,
  "checks": ["nonempty", "env_lists_third_party_versions"],
  "vague_forbidden": true, "checklist_items": [11],
  "failure": "标杆数据在什么机器什么版本上采的不明，复现不出来"
},
"3.3.criterion": {
  "section": "3.3", "name": "性能判据", "form": "section_text",
  "audience": ["developer", "acceptance"], "required": "always",
  "condition": null, "decision_owner": "human", "agent_may_propose": false,
  "checks": ["nonempty", "perf_criterion_has_comparator"],
  "vague_forbidden": true, "checklist_items": [11],
  "failure": "「持平」这类写法读起来像判据，但没有任何一步能照它执行"
},
"3.3.case_table": {
  "section": "3.3", "name": "性能 case 与标杆数据", "form": "table",
  "audience": ["developer", "acceptance"], "required": "always",
  "condition": null, "decision_owner": "human", "agent_may_propose": false,
  "checks": ["nonempty", "models_covered_by_perf_table"],
  "vague_forbidden": true, "checklist_items": [11],
  "failure": "没有具体 case 和标杆数字，开发者不知道优化到什么程度算够"
},
"3.4.memory": {
  "section": "3.4", "name": "内存要求", "form": "section_text",
  "audience": ["developer"], "required": "always", "condition": null,
  "decision_owner": "human", "agent_may_propose": false,
  "checks": ["nonempty"], "vague_forbidden": true, "checklist_items": [12],
  "failure": "内存占用没有上界，开发者可能用空间换时间换到不可接受"
},
"3.5.tooling": {
  "section": "3.5", "name": "自验工具与策略", "form": "section_text",
  "audience": ["developer"], "required": "always", "condition": null,
  "decision_owner": "human", "agent_may_propose": true,
  "checks": ["nonempty", "no_placeholder", "no_inline_link"],
  "vague_forbidden": true, "checklist_items": [13],
  "failure": "开发者不知道用什么工具自验，交上来的自测报告口径各不相同"
},
"3.5.param_mapping": {
  "section": "3.5", "name": "参数序列对应关系", "form": "section_text",
  "audience": ["developer", "acceptance"], "required": "conditional",
  "condition": "signature_differs_from_baseline", "decision_owner": "human",
  "agent_may_propose": true, "checks": ["nonempty"], "vague_forbidden": true,
  "checklist_items": [13],
  "failure": "参数顺序与标杆不一致却没说对应关系，基线调用会传错位置"
},
"3.5.generation_rules": {
  "section": "3.5", "name": "入参生成规则表", "form": "table",
  "audience": ["developer", "acceptance"], "required": "always",
  "condition": null, "decision_owner": "human", "agent_may_propose": true,
  "checks": ["nonempty", "params_covered_by_generation_table",
             "range_matches_distribution"],
  "vague_forbidden": true, "checklist_items": [13],
  "failure": "测试数据怎么造没规则，开发者和验收方造出来的分布不是一回事"
}
```

**§4-§8（5 条）**

```json
"4.deliverables": {
  "section": "4", "name": "验收交付件", "form": "table",
  "audience": ["developer"], "required": "always", "condition": null,
  "decision_owner": "human", "agent_may_propose": true,
  "checks": ["nonempty", "deliverables_complete", "no_inline_link"],
  "vague_forbidden": true, "checklist_items": [14],
  "failure": "开发者交到一半才发现漏了设计文档或自测报告"
},
"5.pr_target": {
  "section": "5", "name": "PR 合入目录", "form": "section_text",
  "audience": ["developer"], "required": "always", "condition": null,
  "decision_owner": "human", "agent_may_propose": false,
  "checks": ["nonempty", "pr_target_is_a_path", "no_inline_link"],
  "vague_forbidden": true, "checklist_items": [2],
  "failure": "只写到仓不写到目录，PR 提错位置返工"
},
"6.references": {
  "section": "6", "name": "参考资料", "form": "section_text",
  "audience": ["developer"], "required": "always", "condition": null,
  "decision_owner": "agent", "agent_may_propose": true,
  "checks": ["nonempty", "no_inline_link"], "vague_forbidden": false,
  "checklist_items": [1],
  "failure": "开发者没有入门材料，从零摸索"
},
"7.notes": {
  "section": "7", "name": "特别注意事项", "form": "section_text",
  "audience": ["developer"], "required": "always", "condition": null,
  "decision_owner": "human", "agent_may_propose": true,
  "checks": ["nonempty", "no_inline_link"], "vague_forbidden": true,
  "checklist_items": [1],
  "failure": "已知的坑没有预警，每个开发者重踩一遍"
},
"8.environment": {
  "section": "8", "name": "环境获取", "form": "fixed",
  "audience": ["developer"], "required": "always", "condition": null,
  "decision_owner": "template", "agent_may_propose": false,
  "checks": ["section_present:8"], "vague_forbidden": false,
  "checklist_items": [1],
  "failure": "开发者不知道去哪申请算力"
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest skill/repo-task-doc-write/tests/test_elements_spine.py -q`
Expected: `9 passed`

若 `test_human_owned_elements_dominate` 红，核对 `decision_owner`：
只有 `6.references` 是 `agent`、`8.environment` 是 `template`，其余 38 条都是 `human`。

- [ ] **Step 5: 提交**

```bash
git add skill/repo-task-doc-write/references/taskdoc-elements.json \
        skill/repo-task-doc-write/tests/test_elements_spine.py
git commit -m "feat: 任务书要素骨架与九条结构不变量

骨架是唯一可编辑对象，追问流程、脚手架、质量门、待确认清单全部从它派生。
每条要素回答六个问题：在哪一节、什么形态、服务哪个受众、必填性、谁拍板、
写错了怎么炸。

比验收 skill 的 artifact-contracts.json 多两个字段：audience 与
decision_owner，对应双受众设计与红线 A。

不变量守住完备性：checks 必须在注册表里、conditional 必须给条件、
14 条 checklist 必须全部被认领、human 拍板占比不低于七成。"
```

---

## Task 3: 修复版模板

**Files:**
- Create: `skill/repo-task-doc-write/references/task-doc-template.md`
- Create: `docs/development/taskdoc-source/repair-log.md`
- Create: `skill/repo-task-doc-write/tests/test_template_shape.py`

**Interfaces:**
- Consumes: Task 1 的 `origin/task_doc_templete_v3.0.md` 与 `defect-map.md` 的 T01-T05
- Produces: 模板的章节标题文本与 §2.4 表头九列，Task 5 的 parser 与 Task 13 的 `make_taskdoc.py` 按它们解析与渲染。

- [ ] **Step 1: 写模板形态测试（先失败）**

```python
# skill/repo-task-doc-write/tests/test_template_shape.py
"""修复版模板的章节与表头形态。模板改版时这里先红。"""

import re
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = SKILL_ROOT / "references" / "task-doc-template.md"
ORIGIN = (Path(__file__).resolve().parents[3] / "docs" / "development"
          / "taskdoc-source" / "origin" / "task_doc_templete_v3.0.md")

PARAM_COLUMNS = ["参数名", "输入／输出/属性", "描述", "数据类型", "dtype类型",
                 "数据排布格式", "维度(shape)", "值域范围", "异常行为"]


def headings(path):
    return re.findall(r"^#{2,3}\s+(.+?)\s*$", path.read_text(encoding="utf-8"),
                      re.MULTILINE)


class TemplateShapeTest(unittest.TestCase):
    def test_keeps_every_original_section_number(self):
        # 红线 B：只做加法。原模板的章节号一个都不能少或改。
        origin_numbers = re.findall(r"^#{2,3}\s+([\d.]+)\s", 
                                    ORIGIN.read_text(encoding="utf-8"),
                                    re.MULTILINE)
        new_numbers = re.findall(r"^#{2,3}\s+([\d.]+)\s",
                                 TEMPLATE.read_text(encoding="utf-8"),
                                 re.MULTILINE)
        self.assertEqual([], [n for n in origin_numbers if n not in new_numbers])

    def test_adds_implementation_constraint_section(self):
        # T03：§2.5 是新增的，加在 §2.4 之后而不是插在中间。
        numbers = re.findall(r"^#{2,3}\s+([\d.]+)\s",
                             TEMPLATE.read_text(encoding="utf-8"), re.MULTILINE)
        self.assertIn("2.5", numbers)
        self.assertLess(numbers.index("2.4"), numbers.index("2.5"))
        self.assertLess(numbers.index("2.5"), numbers.index("3.1"))

    def test_param_table_header_has_nine_columns(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        header = None
        for line in text.splitlines():
            if line.strip().startswith("| 参数名"):
                header = [c.strip() for c in line.strip().strip("|").split("|")]
                break
        self.assertIsNotNone(header, "§2.4 表头未找到")
        self.assertEqual(PARAM_COLUMNS, header)

    def test_precision_requirement_is_mandatory_not_advisory(self):
        # T01：原模板写「建议参考生态标准」，把硬标准写成软建议。
        text = TEMPLATE.read_text(encoding="utf-8")
        section = text.split("### 3.2")[1].split("### 3.3")[0]
        self.assertNotIn("建议参考", section)
        self.assertIn("必须满足", section)

    def test_random_operator_branch_exists(self):
        # T04：随机类算子的对比策略要有位置写，四个取值要列出来。
        text = TEMPLATE.read_text(encoding="utf-8")
        for strategy in ("equal_vs_builtin_pinned_seed",
                         "deterministic_boundary_only",
                         "distribution_test", "self_consistency"):
            with self.subTest(strategy=strategy):
                self.assertIn(strategy, text)

    def test_param_column_guidance_is_not_a_question(self):
        # T02：原模板每列给的是疑问句，疑问句不是判据。
        text = TEMPLATE.read_text(encoding="utf-8")
        section = text.split("### 2.4")[1].split("### 2.5")[0]
        self.assertIn("填写判据", section)
        questions = [l for l in section.splitlines()
                     if l.strip().startswith("|") and l.count("？") > 1]
        self.assertEqual([], questions, "表格里还留着疑问句式的填写要求")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest skill/repo-task-doc-write/tests/test_template_shape.py -q`
Expected: FAIL，`FileNotFoundError` 指向 `task-doc-template.md`

- [ ] **Step 3: 从上游模板派生修复版**

```bash
cp docs/development/taskdoc-source/origin/task_doc_templete_v3.0.md \
   skill/repo-task-doc-write/references/task-doc-template.md
```

然后按 T01-T04 逐条改。改动清单如下，其余原样保留。

**T02 —— §2.4 表头下方的填写要求整段替换**

原文是一行疑问句表格。替换为：

```markdown
填写要求：参考昇腾官网 Ascend C API 的呈现格式，以表格形式填写算子原型定义。
表头九列固定，一列都不能少。每列的填写判据如下。

| 列 | 填写判据 |
| --- | --- |
| 参数名 | 与 §2.3 接口定义里的标识符逐字一致，包括大小写与下划线 |
| 输入／输出/属性 | 取 `输入`、`输出(独立输出)`、`输出(元组输出)`、`输出(原地输出)`、`属性` 之一 |
| 描述 | 一句话说清这个参数是什么，不写「相关的」「对应的」这类无边界表达 |
| 数据类型 | 取 `tensor`、`scalar`、`attr`、`string`、`list_int`、`list_float`、`list_bool`、`list_list_int` 之一 |
| dtype类型 | 张量类写具体 dtype（`FLOAT16`、`BFLOAT16`、`FLOAT32`、`INT32`、`INT64`、`BOOL`、`INT8`、`UINT8`），属性类写 `int`、`float`、`bool` 或 `-` |
| 数据排布格式 | 取 `ND`、`NZ`、`NCHW`、`NHWC`、`NCDHW`、`BNSD`、`BSND` 之一；非张量填 `-` |
| 维度(shape) | 写出秩与各维含义；与其他参数有对齐约束的写明是哪个参数的哪一维；dynamic shape 与广播规则写在 §2.5 |
| 值域范围 | 写可执行的区间。不接受 `(-∞,∞)`——它生成不出数据。不限定时写 `全值域（含 Inf/NaN：不测）` 或给出具体上下界如 `[-1e4, 1e4]` |
| 异常行为 | 对非法 dtype、非法 shape、非法值域分别写出**具体触发条件与预期行为**。整列写同一句话等于没写 |
```

**T03 —— 在 §2.4 之后、§3 之前插入 §2.5**

```markdown
### 2.5 算子实现约束

填写要求：以下六项逐条回答，不适用的写「不涉及」并说明为什么不涉及。
这些约束不属于某一个参数，散写在 §2.1 正文或 §2.4 描述列里开发者拼不出全貌。

| 约束项 | 填写判据 |
| --- | --- |
| 非连续 Tensor 支持 | 取 `支持`、`不支持`、`不涉及` 之一。写 `支持` 时说明哪些参数支持 |
| broadcast 规则 | 写 `不支持`，或写明哪些参数之间按什么规则广播 |
| dynamic shape 要求 | 写明是否要求支持编译期未知的 shape |
| 原地与视图语义 | 写明输出是否复用输入内存，是否返回视图 |
| 确定性计算要求 | 取 `要求`、`不要求` 之一。写 `要求` 时说明判定方式，如同输入两次执行逐位一致 |
| 空 Tensor 与 0 维处理 | 写明空张量与 0 维张量是合法输入还是要报错，合法时输出是什么 |
```

**T01 —— §3.2 填写要求整段替换**

```markdown
填写要求：
1. 明确对标的算子或接口；
2. 精度**必须满足**生态算子开源精度标准，标准正文见
   https://gitcode.com/cann/opbase/blob/master/docs/zh/ops_precision_standard/experimental_standard.md ；
3. 把 §2.4 出现的每一种张量 dtype 逐列抄成阈值表，不接受一句「满足生态标准」带过；
4. 写出判定公式，说清一条用例在什么条件下算精度通过。
```

**T04 —— §3.2 末尾追加条件子项**

```markdown
**随机类算子的精度对比判定策略**（非随机算子写「不涉及」）

算子涉及随机数生成时，CPU 基线与 NPU 实现在同一 seed 下通常不能逐元素比对，
不同后端的随机数算法互不相同。此处必须从下面四个取值里选一个，并说明依据。

| 取值 | 前提 |
| --- | --- |
| `equal_vs_builtin_pinned_seed` | 种子是显式入参且在用例数据里钉死 |
| `deterministic_boundary_only` | 只测确定性边界值，如概率取 0 或 1 |
| `distribution_test` | 判定公式与阈值已经写明 |
| `self_consistency` | 种子是显式入参，同一设备同种子两次执行比对 |

选 `equal_vs_builtin_pinned_seed` 或 `self_consistency` 时，还要列出接口声明里的
种子参数名。「合理的固定种子策略」这类写法读起来像判据，但没有任何一步能照它执行。
```

**§3.3 填写要求补一条**

在原有三条后追加：

```markdown
4. 判据必须含比较符与数字，如「不低于标杆的 0.8 倍」。「持平」「基本一致」
   这类写法没有任何一步能照它执行。
```

**§3.1 填写要求补一条**

```markdown
硬件型号要具体到子型号。写「A2系列产品」不够，要写明 910B3、910B4 等，
因为 §3.3 的性能要求要逐型号落地。三方软件版本包括 torch、torch_npu，
性能对标 GPU 时还要写标杆环境的驱动与 CUDA 版本，否则标杆数据不可复现。
```

- [ ] **Step 4: 写 repair-log.md**

```markdown
# 模板与样例的修复记录

修复对象是 `origin/` 的只读归档，修复产物在
`skill/repo-task-doc-write/references/` 下。缺陷编号见 `defect-map.md`。

## 模板修复

| 编号 | 改了什么 | 为什么这么改 |
| --- | --- | --- |
| T01 | §3.2「建议参考生态标准」改为「必须满足」，并要求逐 dtype 抄阈值表 | 「建议」是软约束，验收侧的标准是硬的。样例里那些模糊写法是模板纵容出来的 |
| T02 | §2.4 表头下的疑问句换成九列的填写判据 | 疑问句不是判据，填的人只能凭感觉。判据要能判定填得对不对 |
| T03 | 新增 §2.5 算子实现约束，六个子项 | 非连续、broadcast、dynamic shape、原地语义、确定性、空 Tensor 原本散在 §2.1 正文和 §2.4 描述列，开发者拼不出全貌 |
| T04 | §3.2 追加随机算子对比策略条件子项 | 随机类算子拿去验收会被拦住，而模板里没有位置写这个策略 |
| T05 | 不修 | 上游 README 的版本号不一致属于上游问题，按只读纪律记录不修改 |

## 为什么 §2.5 加在末尾而不是插在 §2.2

逻辑上「实现约束」紧跟「功能实现要求」更顺，但插入会把 §2.2 到 §2.4 全部后移，
存量任务书与交叉引用作废。样例里就有 `[2.4章节](#24-参数说明)` 这类引用。
收益是行文顺序，代价是兼容性，不划算。
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python3 -m pytest skill/repo-task-doc-write/tests/test_template_shape.py -q`
Expected: `6 passed`

- [ ] **Step 6: 提交**

```bash
git add skill/repo-task-doc-write/references/task-doc-template.md \
        skill/repo-task-doc-write/tests/test_template_shape.py \
        docs/development/taskdoc-source/repair-log.md
git commit -m "feat: 修复版任务书模板

按 defect-map 的 T01-T04 改上游模板 v3.0，只做加法不重编号：
- T01 §3.2「建议参考生态标准」改为「必须满足」并要求逐 dtype 抄阈值
- T02 §2.4 表头下的疑问句换成九列可判定的填写判据
- T03 新增 §2.5 算子实现约束，六个子项
- T04 §3.2 追加随机算子对比策略条件子项，四个取值列全

测试守住红线 B：原模板每个章节号都还在，§2.5 加在 §2.4 之后。"
```

---

## Task 4: 黄金任务书

**Files:**
- Create: `skill/repo-task-doc-write/references/golden-task-doc.md`
- Modify: `docs/development/taskdoc-source/repair-log.md`（追加样例修复段）
- Create: `skill/repo-task-doc-write/tests/fixtures/real_sample.md`

**Interfaces:**
- Consumes: Task 3 的模板、Task 1 的 `origin/task_doc_example_v3.0.md` 与 D01-D12
- Produces: `golden-task-doc.md`，Task 6-11 的每一层门禁都要断言它返回 0；`fixtures/real_sample.md` 是带缺陷的对照组。

- [ ] **Step 1: 建对照组 fixture**

```bash
mkdir -p skill/repo-task-doc-write/tests/fixtures/defects
cp docs/development/taskdoc-source/origin/task_doc_example_v3.0.md \
   skill/repo-task-doc-write/tests/fixtures/real_sample.md
```

在 `real_sample.md` 首行前插入来源说明：

```markdown
<!-- 来源：task-docs/community_task_docs/task_doc_example_v3.0(SlidingTileAttention_task_doc).md
     拷贝日期 2026-08-18。带缺陷的对照组，缺陷清单见 docs/development/taskdoc-source/defect-map.md。
     源不在本仓，不做自动同步。不要修这个文件——修了就失去对照价值。 -->
```

- [ ] **Step 2: 写黄金样例**

以 `real_sample.md` 为底稿，逐条修 D01-D12。**不要另起炉灶重写**，
逐条修才能验证骨架完备：某条缺陷「修不了」说明骨架缺了对应要素。

| 缺陷 | 改法 |
| --- | --- |
| D01 | §2.4 表格补一行 `windowSizeLen`：属性、`scalar`、`int`、`-`、`-`、等于 window_size 的长度、`[1, N]`、长度与 head 数不匹配时报错 |
| D02 | §2.4 参数名列全部改成 §2.3 的写法：`windowSize`、`textLength`、`hasText`、`seqShape`。描述列可以继续用中文说明 |
| D03 | q/k/v 值域从 `(-∞,∞)` 改为 `[-4, 4]`，与 §3.5 的 `(0,1) 正态分布` 一致（正态分布 4 sigma 截断） |
| D04 | `textLength` 值域改为 `[0, S)`，并注明 `hasText=false 时取 0` |
| D05 | `windowSize` 值域改为 `每维取 [1, 15] 内的奇数`，§3.5 的 Attr 覆盖规则同步改为 `[1,15] 内奇数均匀取值` |
| D06 | §3.3 判据改为「910B3 上的平均单次耗时不高于 GPU A100 标杆平均耗时的 1.25 倍（即达到标杆 0.8 倍性能）」，并写明口径是 Avg time，单位 us |
| D07 | §3.1 硬件写「A2 系列，具体型号 910B3、910B4」；§3.3 性能表加 910B4 一列的要求 |
| D08 | §2.4 异常行为列逐参数写具体条件。q/k/v 写「dtype 非 FLOAT16/BFLOAT16 时返回 ACLNN_ERR_PARAM_INVALID；q/k/v 三者 shape 不完全一致时同样报错；S < imageSeqLen + textLength 时报错」；`seqShape` 写「格式不是 axbxc、或 a×b×c 与 S 对不上时报错」 |
| D09 | §3.1 补 torch 与 torch_npu 版本；§3.3 标杆环境补 A100 的驱动与 CUDA 版本 |
| D10 | §3.5 的占位符换成裸链接写法：`自测用例与测试指导：https://gitcode.com/<org>/<repo>/tree/master/tests/sliding_tile_attention` |
| D11 | 新增 §2.5，六项逐条填写。非连续 Tensor 写「不支持，q/k/v 必须连续」；broadcast 写「不支持」；确定性写「要求，同输入两次执行逐位一致」；空 Tensor 写「B、N、S、D 任一为 0 时报错」 |
| D12 | §3.2 追加随机算子子项，写「不涉及——本算子不含随机数生成，§3.5 的正态分布是测试数据的生成规则，不是算子行为」 |

D12 的写法很关键：信号词命中不等于算子是随机算子，任务书要**明确否认**，
这样验收侧才不会挂待确认项。

- [ ] **Step 3: 追加样例修复记录**

在 `repair-log.md` 末尾追加：

```markdown
## 样例修复

黄金样例以 `origin/task_doc_example_v3.0.md` 为底稿逐条修 D01-D12 得到，
不是另起炉灶重写。逐条修才能验证骨架完备：某条缺陷改不动，说明骨架缺了要素。

值得单独说的两处：

D03 的值域改成 `[-4, 4]` 而不是别的区间，是因为 §3.5 声明 q/k/v 用 (0,1) 正态分布
生成，4 sigma 截断覆盖了 99.99% 的采样，两处这样才对得上。

D12 写的是「不涉及」加一句否认理由，而不是留空。信号词命中不等于算子是随机算子，
样例的 (0,1) 正态分布说的是**测试数据怎么造**，不是算子行为。任务书明确否认，
验收侧才不会挂一个待确认项。
```

- [ ] **Step 4: 人工核对黄金样例可读性**

Run: `wc -l skill/repo-task-doc-write/references/golden-task-doc.md`
Expected: 约 200-230 行（比原样例多出 §2.5 与补齐的内容）

通读一遍，确认它同时对两个受众成立：

- 开发者能从中知道做什么、做到什么程度、交付什么、提交到哪
- 每条约束都可判定，没有「持平」「合理的」这类无边界表达

- [ ] **Step 5: 提交**

```bash
git add skill/repo-task-doc-write/references/golden-task-doc.md \
        skill/repo-task-doc-write/tests/fixtures/real_sample.md \
        docs/development/taskdoc-source/repair-log.md
git commit -m "feat: 黄金任务书与带缺陷对照组

黄金样例以上游样例为底稿逐条修 D01-D12 得到。逐条修而不是重写，
是为了验证骨架完备——某条缺陷改不动说明骨架缺了要素。

它一石二鸟：既是防假门禁的反向 fixture（红线 D），又是给 agent 看的
写作参考。

real_sample.md 是原样拷贝的对照组，不修，修了就失去对照价值。"
```

---

## Task 5: md 反解与签名抽取

**Files:**
- Create: `skill/repo-task-doc-write/scripts/_taskdoc_parser.py`
- Create: `skill/repo-task-doc-write/scripts/_signature.py`
- Create: `skill/repo-task-doc-write/tests/test_parser.py`

**Interfaces:**
- Consumes: Task 3 的模板、Task 4 的黄金样例与对照组
- Produces:
  - `_taskdoc_parser.ParseError`
  - `_taskdoc_parser.parse(path) -> Doc`
  - `Doc.sections: dict[str, Section]`，键是章节号字符串如 `"2.4"`
  - `Section` 具名元组，字段 `number: str`、`title: str`、`start_line: int`、`lines: list[str]`、`tables: list[Table]`、`code_blocks: list[str]`
  - `Table` 具名元组，字段 `header: list[str]`、`rows: list[list[str]]`、`start_line: int`
  - `_signature.parameter_names(code: str) -> list[str]`
  - `_signature.BOILERPLATE: frozenset[str]`

- [ ] **Step 1: 写 parser 测试（先失败）**

```python
# skill/repo-task-doc-write/tests/test_parser.py
"""md 反解与签名抽取。模板改版时这里先红。"""

import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import _signature  # noqa: E402
import _taskdoc_parser as parser  # noqa: E402

GOLDEN = SKILL_ROOT / "references" / "golden-task-doc.md"
SAMPLE = SKILL_ROOT / "tests" / "fixtures" / "real_sample.md"

ACLNN_SIGNATURE = """
aclnnStatus aclnnSlidingTileAttentionGetWorkspaceSize(
    const aclTensor          *q,
    const aclTensor          *k,
    const aclTensor          *v,
    aclTensor                *output,
    const aclIntArray *const *windowSize,
    uint64_t                  windowSizeLen,
    int64_t                   textLength,
    bool                      hasText,
    const char               *seqShape,
    uint64_t                 *workspaceSize,
    aclOpExecutor           **executor)

aclnnStatus aclnnSlidingTileAttention(
    void          *workspace,
    uint64_t       workspaceSize,
    aclOpExecutor *executor,
    aclrtStream    stream)
"""

PYTHON_SIGNATURE = """
def sliding_tile_attention(
    q: torch.Tensor,
    k: torch.Tensor,
    v: torch.Tensor,
    window_size: list,
    text_length: int,
    has_text: bool = True,
    seq_shape: str = "30x48x80",
) -> torch.Tensor:
"""


class SignatureTest(unittest.TestCase):
    def test_extracts_aclnn_parameters_in_order(self):
        self.assertEqual(
            ["q", "k", "v", "output", "windowSize", "windowSizeLen",
             "textLength", "hasText", "seqShape"],
            _signature.parameter_names(ACLNN_SIGNATURE))

    def test_drops_workspace_and_executor_boilerplate(self):
        names = _signature.parameter_names(ACLNN_SIGNATURE)
        for noise in ("workspace", "workspaceSize", "executor", "stream"):
            with self.subTest(noise=noise):
                self.assertNotIn(noise, names)

    def test_extracts_python_parameters(self):
        self.assertEqual(
            ["q", "k", "v", "window_size", "text_length", "has_text",
             "seq_shape"],
            _signature.parameter_names(PYTHON_SIGNATURE))

    def test_returns_empty_for_prose(self):
        self.assertEqual([], _signature.parameter_names("这里没有签名。"))


class ParserTest(unittest.TestCase):
    def test_golden_parses_all_declared_sections(self):
        doc = parser.parse(GOLDEN)
        for number in ("1", "2.1", "2.2", "2.3", "2.4", "2.5",
                       "3.1", "3.2", "3.3", "3.4", "3.5", "4", "5", "6", "7", "8"):
            with self.subTest(number=number):
                self.assertIn(number, doc.sections)

    def test_param_table_is_found_with_nine_columns(self):
        doc = parser.parse(GOLDEN)
        tables = doc.sections["2.4"].tables
        self.assertTrue(tables, "§2.4 没解析出表格")
        self.assertEqual(9, len(tables[0].header))

    def test_code_block_carries_the_signature(self):
        doc = parser.parse(GOLDEN)
        blocks = doc.sections["2.3"].code_blocks
        self.assertTrue(blocks, "§2.3 没解析出代码块")
        self.assertIn("q", _signature.parameter_names("\n".join(blocks)))

    def test_sample_without_section_25_still_parses(self):
        # 向后兼容：老任务书缺 §2.5 时解析不崩，缺失交给门禁判。
        doc = parser.parse(SAMPLE)
        self.assertNotIn("2.5", doc.sections)
        self.assertIn("2.4", doc.sections)

    def test_line_numbers_point_at_the_source(self):
        doc = parser.parse(GOLDEN)
        text = GOLDEN.read_text(encoding="utf-8").splitlines()
        section = doc.sections["2.4"]
        self.assertIn("2.4", text[section.start_line - 1])

    def test_unparseable_document_raises(self):
        broken = SKILL_ROOT / "tests" / "fixtures" / "defects" / "no_headings.md"
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_text("这份文档没有任何章节标题。\n", encoding="utf-8")
        with self.assertRaises(parser.ParseError):
            parser.parse(broken)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest skill/repo-task-doc-write/tests/test_parser.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named '_signature'`

- [ ] **Step 3: 实现 `_signature.py`**

```python
"""从接口定义代码块里抽参数名。

只认两种形态：C 风格的 aclnn 声明和 Python 的 def。
其他形态返回空列表，由门禁报「签名解析不出参数」而不是在这里猜。
"""

import re

# workspace 四件套是 aclnn 调用约定的固定部分，不是算子参数。
# 它们不该出现在 §2.4 参数表里，比对时要先摘掉。
BOILERPLATE = frozenset({"workspace", "workspaceSize", "executor", "stream"})

_C_PARAM = re.compile(r"([A-Za-z_]\w*)\s*(?:\[\s*\d*\s*\])?\s*$")
_PY_DEF = re.compile(r"^\s*def\s+\w+\s*\(", re.MULTILINE)


def _split_top_level(text):
    """按逗号切分，跳过括号与尖括号内部的逗号。"""
    depth, current, out = 0, [], []
    for ch in text:
        if ch in "([<":
            depth += 1
        elif ch in ")]>":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        out.append("".join(current))
    return out


def _python_names(code):
    start = _PY_DEF.search(code)
    if not start:
        return []
    body = code[start.end():]
    depth, end = 1, len(body)
    for index, ch in enumerate(body):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                end = index
                break
    names = []
    for chunk in _split_top_level(body[:end]):
        name = chunk.split(":")[0].split("=")[0].strip().lstrip("*")
        if name and name not in ("self", "cls"):
            names.append(name)
    return names


def _c_names(code):
    names, seen = [], set()
    for match in re.finditer(r"\b\w+\s*\(([^;{]*?)\)", code, re.DOTALL):
        for chunk in _split_top_level(match.group(1)):
            chunk = chunk.strip().rstrip(",")
            if not chunk:
                continue
            found = _C_PARAM.search(chunk.replace("*", " ").replace("&", " "))
            if not found:
                continue
            name = found.group(1)
            if name in BOILERPLATE or name in seen:
                continue
            seen.add(name)
            names.append(name)
    return names


def parameter_names(code):
    """返回参数名，保持声明顺序，已摘掉 workspace 四件套与重复项。"""
    if _PY_DEF.search(code):
        return [n for n in _python_names(code) if n not in BOILERPLATE]
    return _c_names(code)
```

- [ ] **Step 4: 实现 `_taskdoc_parser.py`**

```python
"""按固定章节号与表格列反解任务书 md。

模板是稳定的，所以解析靠章节号而不是靠自然语言。解析不出章节结构时
抛 ParseError，由 check_taskdoc.py 转成退出码 3——那是结构问题，
不是内容问题，报错要分得开。
"""

import re
from collections import namedtuple
from pathlib import Path

Table = namedtuple("Table", "header rows start_line")
Section = namedtuple("Section", "number title start_line lines tables code_blocks")

_HEADING = re.compile(r"^(#{2,4})\s+([\d.]+)\s*(.*?)\s*$")
_SEPARATOR = re.compile(r"^\|[\s:|-]+\|$")


class ParseError(Exception):
    """章节结构解析不出来。"""


class Doc:
    def __init__(self, path, title, sections):
        self.path = Path(path)
        self.title = title
        self.sections = sections

    def section(self, number):
        return self.sections.get(number)


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _tables(lines, offset):
    out, index = [], 0
    while index < len(lines):
        line = lines[index].strip()
        nxt = lines[index + 1].strip() if index + 1 < len(lines) else ""
        if line.startswith("|") and _SEPARATOR.match(nxt):
            header = _cells(line)
            rows, cursor = [], index + 2
            while cursor < len(lines) and lines[cursor].strip().startswith("|"):
                rows.append(_cells(lines[cursor]))
                cursor += 1
            out.append(Table(header, rows, offset + index))
            index = cursor
            continue
        index += 1
    return out


def _code_blocks(lines):
    out, current, inside = [], [], False
    for line in lines:
        if line.strip().startswith("```"):
            if inside:
                out.append("\n".join(current))
                current = []
            inside = not inside
            continue
        if inside:
            current.append(line)
    if current:
        out.append("\n".join(current))
    return out


def parse(path):
    path = Path(path)
    raw = path.read_text(encoding="utf-8").splitlines()
    title = next((l.lstrip("# ").strip() for l in raw if l.startswith("# ")), "")

    marks, inside_code = [], False
    for number, line in enumerate(raw, 1):
        if line.strip().startswith("```"):
            inside_code = not inside_code
            continue
        if inside_code:
            continue
        found = _HEADING.match(line)
        if found:
            marks.append((number, found.group(2).rstrip("."), found.group(3)))

    if not marks:
        raise ParseError(f"{path.name}: 没有解析出任何编号章节标题")

    sections = {}
    for index, (start, number, heading) in enumerate(marks):
        end = marks[index + 1][0] - 1 if index + 1 < len(marks) else len(raw)
        body = raw[start:end]
        sections[number] = Section(
            number=number, title=heading, start_line=start, lines=body,
            tables=_tables(body, start + 1), code_blocks=_code_blocks(body))
    return Doc(path, title, sections)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python3 -m pytest skill/repo-task-doc-write/tests/test_parser.py -q`
Expected: `10 passed, 4 subtests passed`

若 `test_param_table_is_found_with_nine_columns` 红，检查黄金样例 §2.4
表头是不是九列且用 `|` 包住首尾。

- [ ] **Step 6: 提交**

```bash
git add skill/repo-task-doc-write/scripts/_taskdoc_parser.py \
        skill/repo-task-doc-write/scripts/_signature.py \
        skill/repo-task-doc-write/tests/test_parser.py
git commit -m "feat: 任务书 md 反解与签名抽取

模板稳定，所以按章节号解析而不是靠自然语言。解析不出章节结构抛
ParseError，由门禁转成退出码 3——结构问题与内容问题的报错要分得开。

签名抽取认 C 风格 aclnn 声明与 Python def 两种形态。workspace 四件套
是调用约定的固定部分不是算子参数，比对前先摘掉，否则 §2.4 会被要求
写四行不存在的参数。"
```

---

## Task 6: 结构门（L0）与缺陷 fixture

**Files:**
- Create: `skill/repo-task-doc-write/scripts/_checks.py`
- Create: `skill/repo-task-doc-write/scripts/check_taskdoc.py`
- Create: `skill/repo-task-doc-write/references/vague-words.json`
- Create: `skill/repo-task-doc-write/references/dtype-vocab.json`
- Create: `skill/repo-task-doc-write/tests/fixtures/defects/d10_placeholder.md`
- Create: `skill/repo-task-doc-write/tests/fixtures/defects/d11_missing_25.md`
- Create: `skill/repo-task-doc-write/tests/test_gate_structure.py`

**Interfaces:**
- Consumes: Task 2 骨架、Task 5 的 parser
- Produces:
  - `_checks.Finding` 具名元组，字段 `rule: str`、`element: str`、`section: str`、`line: int`、`message: str`
  - `_checks.run_layer(doc, spine, layer, context) -> list[Finding]`，`layer` 取 `"L0"`..`"L4"`
  - `_checks.CHECKS: dict[str, callable]`，键与骨架 `check_registry` 一一对应
  - `check_taskdoc.py --doc <md> [--decisions <json>] [--evidence-dir <dir>] [--layers L0,L1]`

- [ ] **Step 1: 写 L0 测试与缺陷 fixture（先失败）**

`d10_placeholder.md` 由黄金样例改一行得到：

```bash
python3 - <<'EOF'
from pathlib import Path
root = Path("skill/repo-task-doc-write")
golden = (root / "references" / "golden-task-doc.md").read_text(encoding="utf-8")
target = "自测用例与测试指导：https://gitcode.com"
assert target in golden, "黄金样例的 §3.5 链接写法变了，改这个 fixture 的锚点"
line = [l for l in golden.splitlines() if target in l][0]
broken = golden.replace(line, "请根据本任务给出的[自测用例和测试指导](测试用例文件夹路径)完成自测。")
(root / "tests" / "fixtures" / "defects" / "d10_placeholder.md").write_text(
    broken, encoding="utf-8")
EOF
```

`d11_missing_25.md` 由黄金样例删掉 §2.5 整节得到：

```bash
python3 - <<'EOF'
import re
from pathlib import Path
root = Path("skill/repo-task-doc-write")
golden = (root / "references" / "golden-task-doc.md").read_text(encoding="utf-8")
without = re.sub(r"### 2\.5 .*?(?=\n## 3\. )", "", golden, flags=re.DOTALL)
assert "### 2.5" not in without, "§2.5 没删干净，检查标题层级"
(root / "tests" / "fixtures" / "defects" / "d11_missing_25.md").write_text(
    without, encoding="utf-8")
EOF
```

```python
# skill/repo-task-doc-write/tests/test_gate_structure.py
"""L0 结构门。缺陷编号见 docs/development/taskdoc-source/defect-map.md。"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import _checks  # noqa: E402
import _taskdoc_parser as parser  # noqa: E402

GATE = SKILL_ROOT / "scripts" / "check_taskdoc.py"
GOLDEN = SKILL_ROOT / "references" / "golden-task-doc.md"
DEFECTS = SKILL_ROOT / "tests" / "fixtures" / "defects"


def spine():
    return json.loads(
        (SKILL_ROOT / "references" / "taskdoc-elements.json").read_text(
            encoding="utf-8"))


def layer0(path):
    return _checks.run_layer(parser.parse(path), spine(), "L0", {})


def rules(findings):
    return sorted({f.rule for f in findings})


class StructureLayerTest(unittest.TestCase):
    def test_golden_passes_layer_zero(self):
        # 红线 D：黄金样例必须真的过。没有这条，门禁可以靠什么都判红通过。
        self.assertEqual([], layer0(GOLDEN))

    def test_d10_placeholder_and_inline_link_are_caught(self):
        found = layer0(DEFECTS / "d10_placeholder.md")
        self.assertIn("no_placeholder", rules(found))
        self.assertIn("no_inline_link", rules(found))

    def test_d11_missing_section_is_caught(self):
        found = layer0(DEFECTS / "d11_missing_25.md")
        self.assertIn("section_present", rules(found))
        self.assertTrue(any("2.5" in f.message for f in found))

    def test_findings_point_at_a_line(self):
        for finding in layer0(DEFECTS / "d10_placeholder.md"):
            with self.subTest(rule=finding.rule):
                self.assertGreater(finding.line, 0)

    def test_image_links_are_not_flagged(self):
        # §8 固定内容里有 ![环境截图](./pics/xxx.png)，图片不算隐藏跳转链接。
        found = layer0(GOLDEN)
        self.assertEqual([], [f for f in found if f.rule == "no_inline_link"])


class GateCliTest(unittest.TestCase):
    def test_golden_exits_zero(self):
        result = subprocess.run(
            [sys.executable, str(GATE), "--doc", str(GOLDEN), "--layers", "L0"],
            capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_defect_exits_two(self):
        result = subprocess.run(
            [sys.executable, str(GATE), "--doc",
             str(DEFECTS / "d10_placeholder.md"), "--layers", "L0"],
            capture_output=True, text=True)
        self.assertEqual(2, result.returncode)
        self.assertIn("no_placeholder", result.stdout)

    def test_unparseable_exits_three(self):
        result = subprocess.run(
            [sys.executable, str(GATE), "--doc",
             str(DEFECTS / "no_headings.md"), "--layers", "L0"],
            capture_output=True, text=True)
        self.assertEqual(3, result.returncode)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest skill/repo-task-doc-write/tests/test_gate_structure.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named '_checks'`

- [ ] **Step 3: 写两份词表**

`references/vague-words.json`：

```json
{
  "schema_version": 1,
  "_purpose": "模糊词按要素作用域生效，不做全文扫描。骨架里 vague_forbidden 为 true 的要素才查。「建议参考」在 §6 参考资料合法，在 §3.2 精度要求里就是把硬标准写软了。",
  "_rule": "新增禁用词必须同时给替代写法。只禁不给替换，下一个人只会换一个同样模糊的词。",
  "words": {
    "持平": "写出比较符与数字，如「不低于标杆的 0.8 倍」",
    "基本一致": "写出容差，如「逐元素相对误差不超过 1e-3」",
    "大致": "给出具体数值或区间",
    "合理的": "写出判定规则本身，而不是形容它合理",
    "尽可能": "写出必须达到的下限",
    "视情况": "把「哪些情况」和「分别怎么办」列出来",
    "酌情": "把判断权交给谁、依据是什么写明",
    "较高": "给出数值门槛",
    "较低": "给出数值门槛",
    "若干": "给出具体数量或范围",
    "原则上": "写明是硬要求还是可协商，可协商的话找谁",
    "适当": "给出具体取值或计算方式",
    "必要时": "写明「必要」的判定条件",
    "TBD": "补齐内容，或写明谁在什么时候补",
    "待定": "补齐内容，或写明谁在什么时候补",
    "TODO": "补齐内容，或写明谁在什么时候补"
  },
  "placeholders": ["xxx", "XXX", "TBD", "待定", "TODO", "待补充", "<填写", "路径>"]
}
```

`references/dtype-vocab.json`：

```json
{
  "schema_version": 1,
  "_purpose": "§2.4 三个枚举列的取值域。独立维护，不绑 ATK 的类型系统——任务书是发给开发者的，不该被验收工具的内部类型系统绑架。",
  "_atk_note": "出现 ATK 表达不了的类型时门禁给警告不阻塞，留给验收阶段处理。",
  "project_mode": ["kernel直调", "aclnn算子工程化开发", "torch接口"],
  "direction": ["输入", "输出(独立输出)", "输出(元组输出)", "输出(原地输出)", "属性"],
  "data_type": ["tensor", "scalar", "attr", "string",
                "list_int", "list_float", "list_bool", "list_list_int"],
  "dtype": ["FLOAT16", "BFLOAT16", "FLOAT32", "FLOAT64", "INT8", "UINT8",
            "INT16", "INT32", "INT64", "BOOL", "COMPLEX64", "COMPLEX128",
            "int", "float", "bool", "-"],
  "format": ["ND", "NZ", "NCHW", "NHWC", "NCDHW", "BNSD", "BSND", "-"],
  "atk_expressible_data_type": ["tensor", "scalar", "attr",
                                "list_int", "list_float", "list_bool"]
}
```

- [ ] **Step 4: 实现 `_checks.py` 的 L0 部分**

```python
"""全部判据实现与注册表。

判据分五层，每层对应 spec §7.1。这里只放实现，判据该用在哪条要素上
由 references/taskdoc-elements.json 的 checks 字段决定——骨架是唯一
可编辑对象，加判据要先进骨架。
"""

import json
import re
from collections import namedtuple
from pathlib import Path

REFERENCES = Path(__file__).resolve().parents[1] / "references"

Finding = namedtuple("Finding", "rule element section line message")

LAYERS = {
    "L0": {"nonempty", "section_present", "no_placeholder", "no_inline_link",
           "table_header_matches", "enum_value"},
    "L1": {"signature_matches_param_table", "range_matches_distribution",
           "range_matches_algorithm", "models_covered_by_perf_table",
           "dtypes_covered_by_threshold_table", "thresholds_match_standard",
           "params_covered_by_generation_table"},
    "L2": {"no_unbounded_range", "no_vague_word", "perf_criterion_has_comparator",
           "error_column_not_uniform", "per_dtype_threshold",
           "env_lists_third_party_versions", "random_strategy_when_signaled"},
    "L3": {"human_reply_recorded"},
    "L4": {"deliverables_complete", "pr_target_is_a_path"},
}

# 图片引用不是隐藏跳转链接。模板 §8 固定内容里就有 ![环境截图](./pics/x.png)。
_INLINE_LINK = re.compile(r"(?<!!)\[[^\]]+\]\([^)]+\)")


def _vocab():
    return json.loads((REFERENCES / "dtype-vocab.json").read_text(encoding="utf-8"))


def _vague():
    return json.loads((REFERENCES / "vague-words.json").read_text(encoding="utf-8"))


def _section_text(doc, number):
    section = doc.sections.get(number)
    return "\n".join(section.lines) if section else ""


def _element_lines(doc, element):
    """返回 (行号, 文本)，跳过表格分隔行与空行。"""
    section = doc.sections.get(element["section"])
    if not section:
        return []
    out = []
    for offset, line in enumerate(section.lines):
        text = line.strip()
        if text and not re.match(r"^\|[\s:|-]+\|$", text):
            out.append((section.start_line + 1 + offset, text))
    return out


def check_section_present(doc, key, element, context):
    number = element["section"]
    if number in doc.sections:
        return []
    return [Finding("section_present", key, number, 1,
                    f"§{number} 缺失 · {element['name']}无处声明 · "
                    f"{element['failure']}")]


def check_nonempty(doc, key, element, context):
    section = doc.sections.get(element["section"])
    if not section:
        return []
    body = [l for l in section.lines if l.strip()]
    if body:
        return []
    return [Finding("nonempty", key, element["section"], section.start_line,
                    f"§{element['section']} {element['name']} 为空 · "
                    f"{element['failure']}")]


def check_no_placeholder(doc, key, element, context):
    tokens = _vague()["placeholders"]
    out = []
    for line, text in _element_lines(doc, element):
        for token in tokens:
            if token in text:
                out.append(Finding(
                    "no_placeholder", key, element["section"], line,
                    f"残留占位符「{token}」：{text[:50]}"))
                break
    return out


def check_no_inline_link(doc, key, element, context):
    out = []
    for line, text in _element_lines(doc, element):
        if _INLINE_LINK.search(text):
            out.append(Finding(
                "no_inline_link", key, element["section"], line,
                "链接要裸露，写成「说明：https://...」而不是 [说明](https://...)"))
    return out


def check_table_header_matches(doc, key, element, context):
    expected = context.get("expected_header", {}).get(element["section"])
    section = doc.sections.get(element["section"])
    if not expected or not section or not section.tables:
        return []
    header = section.tables[0].header
    if header == expected:
        return []
    return [Finding("table_header_matches", key, element["section"],
                    section.tables[0].start_line,
                    f"表头应为 {expected}，实际 {header}")]


def check_enum_value(doc, key, element, context, vocabulary=None):
    allowed = _vocab().get(vocabulary, [])
    section = doc.sections.get(element["section"])
    if not allowed or not section:
        return []
    if element["form"] == "table_column":
        return _enum_in_column(section, key, element, allowed, vocabulary)
    text = "\n".join(section.lines)
    if any(value in text for value in allowed):
        return []
    return [Finding("enum_value", key, element["section"], section.start_line,
                    f"{element['name']} 必须取 {allowed} 之一")]


def _enum_in_column(section, key, element, allowed, vocabulary):
    if not section.tables:
        return []
    table = section.tables[0]
    if element["name"] not in table.header:
        return []
    index = table.header.index(element["name"])
    out = []
    for offset, row in enumerate(table.rows):
        if index >= len(row):
            continue
        cell = row[index]
        values = [v.strip() for v in re.split(r"[、,，/]", cell) if v.strip()]
        bad = [v for v in values if v not in allowed]
        if bad:
            out.append(Finding(
                "enum_value", key, element["section"],
                table.start_line + 2 + offset,
                f"{element['name']} 取值 {bad} 不在词表 {vocabulary} 内，"
                f"合法值：{allowed}"))
    return out


CHECKS = {
    "section_present": check_section_present,
    "nonempty": check_nonempty,
    "no_placeholder": check_no_placeholder,
    "no_inline_link": check_no_inline_link,
    "table_header_matches": check_table_header_matches,
    "enum_value": check_enum_value,
}


def run_layer(doc, spine, layer, context):
    """跑一层判据。conditional 要素的条件不成立时整条跳过。"""
    wanted = LAYERS[layer]
    findings = []
    for key, element in spine["elements"].items():
        if element["required"] == "conditional" and not context.get(
                element["condition"]):
            continue
        for spec in element["checks"]:
            name, _, argument = spec.partition(":")
            if name not in wanted or name not in CHECKS:
                continue
            if argument and name == "enum_value":
                findings.extend(CHECKS[name](doc, key, element, context,
                                             vocabulary=argument))
            else:
                findings.extend(CHECKS[name](doc, key, element, context))
    return _dedupe(findings)


def _dedupe(findings):
    seen, out = set(), []
    for finding in findings:
        mark = (finding.rule, finding.section, finding.line, finding.message)
        if mark in seen:
            continue
        seen.add(mark)
        out.append(finding)
    return sorted(out, key=lambda f: (f.section, f.line, f.rule))
```

- [ ] **Step 5: 实现 `check_taskdoc.py`**

```python
#!/usr/bin/env python3
"""任务书质量门。唯一不可绕过的出口。

退出码：0 全过，2 判据不满足，3 结构解析失败。

通过时在 --evidence-dir 下写 gate_pass.json，内含被检 md 的 sha256。
交付物里没有 sha256 匹配当前 md 的封条，就是没过门；跑完门禁又改稿，
sha256 也对不上（红线 C）。
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _checks  # noqa: E402
import _taskdoc_parser as parser  # noqa: E402

SKILL_ROOT = Path(__file__).resolve().parents[1]
SPINE = SKILL_ROOT / "references" / "taskdoc-elements.json"
ALL_LAYERS = ["L0", "L1", "L2", "L3", "L4"]


def build_context(doc, decisions, spine):
    """条件必填的条件在这里求值，判据本身不判条件。"""
    signals = json.loads(
        (SKILL_ROOT / "references" / "random-operator-signals.json").read_text(
            encoding="utf-8"))["signal_keywords"]
    text = doc.path.read_text(encoding="utf-8")
    return {
        "random_signal_hit": any(word in text for word in signals),
        "signature_differs_from_baseline": bool(
            decisions.get("3.5.param_mapping", {}).get("human_reply")),
        "decisions": decisions,
        "expected_header": {
            "2.4": ["参数名", "输入／输出/属性", "描述", "数据类型", "dtype类型",
                    "数据排布格式", "维度(shape)", "值域范围", "异常行为"],
        },
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="任务书质量门")
    ap.add_argument("--doc", required=True, help="任务书 md 路径")
    ap.add_argument("--decisions", help="evidence/decisions.json 路径")
    ap.add_argument("--evidence-dir", help="通过时把封条写到这里")
    ap.add_argument("--layers", default=",".join(ALL_LAYERS),
                    help="要跑的层，逗号分隔，默认全跑")
    args = ap.parse_args(argv)

    doc_path = Path(args.doc)
    try:
        doc = parser.parse(doc_path)
    except parser.ParseError as error:
        print(f"[结构解析失败] {error}", file=sys.stderr)
        print("md 不符合模板骨架，先跑 make_taskdoc.py 生成脚手架。",
              file=sys.stderr)
        return 3

    spine = json.loads(SPINE.read_text(encoding="utf-8"))
    decisions = {}
    if args.decisions and Path(args.decisions).is_file():
        decisions = json.loads(Path(args.decisions).read_text(encoding="utf-8"))
    context = build_context(doc, decisions, spine)

    layers = [l.strip() for l in args.layers.split(",") if l.strip()]
    results, findings = {}, []
    for layer in layers:
        found = _checks.run_layer(doc, spine, layer, context)
        results[layer] = len(found)
        findings.extend(found)

    for finding in findings:
        print(f"§{finding.section}:{finding.line} [{finding.rule}] "
              f"{finding.message}")

    if findings:
        print(f"\n不通过：{len(findings)} 处 · 层结果 {results}")
        return 2

    digest = hashlib.sha256(doc_path.read_bytes()).hexdigest()
    print(f"通过 · 层结果 {results} · sha256 {digest[:16]}")
    if args.evidence_dir:
        evidence = Path(args.evidence_dir)
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / "gate_pass.json").write_text(json.dumps({
            "doc": str(doc_path),
            "sha256": digest,
            "layers": results,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: 跑测试确认通过**

Run: `python3 -m pytest skill/repo-task-doc-write/tests/test_gate_structure.py -q`
Expected: `8 passed`

`test_golden_passes_layer_zero` 若红，按报错逐条修**黄金样例**，不要放宽判据。
黄金样例是「什么叫合格」的定义，判据是它的形式化——冲突时改样例。

- [ ] **Step 7: 提交**

```bash
git add skill/repo-task-doc-write/scripts/_checks.py \
        skill/repo-task-doc-write/scripts/check_taskdoc.py \
        skill/repo-task-doc-write/references/vague-words.json \
        skill/repo-task-doc-write/references/dtype-vocab.json \
        skill/repo-task-doc-write/tests/fixtures/defects \
        skill/repo-task-doc-write/tests/test_gate_structure.py
git commit -m "feat: 质量门 L0 结构层与缺陷 fixture

判据实现放 _checks.py，判据用在哪条要素上由骨架的 checks 字段决定。
加判据要先进骨架，骨架是唯一可编辑对象。

L0 抓四类：章节缺失、要素为空、残留占位符、隐藏跳转链接。图片引用不算
隐藏链接——模板 §8 固定内容里就有 ![环境截图](./pics/x.png)。

退出码分开：结构解析不出来是 3，内容判据不满足是 2。两种问题的修法
完全不同，报错要分得开。

黄金样例过 L0（红线 D），d10/d11 两个缺陷 fixture 被精确抓到。"
```

---

## Task 7: 一致性门（L1）

**Files:**
- Modify: `skill/repo-task-doc-write/scripts/_checks.py`
- Create: `skill/repo-task-doc-write/tests/fixtures/defects/d01_missing_param.md`
- Create: `skill/repo-task-doc-write/tests/fixtures/defects/d03_range_conflict.md`
- Create: `skill/repo-task-doc-write/tests/fixtures/defects/d07_missing_model.md`
- Create: `skill/repo-task-doc-write/tests/test_gate_consistency.py`

**Interfaces:**
- Consumes: Task 5 的 `_signature.parameter_names`、Task 6 的 `Finding` 与 `run_layer`
- Produces: `_checks.CHECKS` 新增七个键，全部签名为 `(doc, key, element, context) -> list[Finding]`

- [ ] **Step 1: 造三个缺陷 fixture**

```bash
python3 - <<'EOF'
from pathlib import Path
root = Path("skill/repo-task-doc-write")
golden = (root / "references" / "golden-task-doc.md").read_text(encoding="utf-8")
out = root / "tests" / "fixtures" / "defects"

# D01：从 §2.4 删掉 windowSizeLen 那一行，§2.3 保留
rows = [l for l in golden.splitlines() if l.startswith("| windowSizeLen")]
assert len(rows) == 1, "黄金样例里 windowSizeLen 行不唯一，改这个 fixture 的锚点"
(out / "d01_missing_param.md").write_text(
    golden.replace(rows[0] + "\n", ""), encoding="utf-8")

# D03：把 q 的值域改回 (-∞,∞)，与 §3.5 的正态分布矛盾
qrow = [l for l in golden.splitlines() if l.startswith("| q ")][0]
(out / "d03_range_conflict.md").write_text(
    golden.replace(qrow, qrow.replace("[-4, 4]", "(-∞,∞)")), encoding="utf-8")

# D07：§3.1 保留 910B4，§3.3 性能表删掉 910B4 的要求
perf = [l for l in golden.splitlines() if "910B4" in l and l.startswith("|")]
assert perf, "黄金样例 §3.3 没有 910B4 行，改这个 fixture 的锚点"
broken = golden
for line in perf:
    broken = broken.replace(line + "\n", "")
(out / "d07_missing_model.md").write_text(broken, encoding="utf-8")
EOF
```

- [ ] **Step 2: 写 L1 测试（先失败）**

```python
# skill/repo-task-doc-write/tests/test_gate_consistency.py
"""L1 一致性门。判别力最强的一层：跨章节对不上就是硬伤。"""

import json
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import _checks  # noqa: E402
import _taskdoc_parser as parser  # noqa: E402

GOLDEN = SKILL_ROOT / "references" / "golden-task-doc.md"
SAMPLE = SKILL_ROOT / "tests" / "fixtures" / "real_sample.md"
DEFECTS = SKILL_ROOT / "tests" / "fixtures" / "defects"


def spine():
    return json.loads((SKILL_ROOT / "references"
                       / "taskdoc-elements.json").read_text(encoding="utf-8"))


def layer1(path, context=None):
    return _checks.run_layer(parser.parse(path), spine(), "L1", context or {
        "expected_header": {}, "decisions": {}})


def rules(findings):
    return sorted({f.rule for f in findings})


class ConsistencyLayerTest(unittest.TestCase):
    def test_golden_passes_layer_one(self):
        self.assertEqual([], layer1(GOLDEN))

    def test_d01_missing_parameter_is_caught(self):
        found = layer1(DEFECTS / "d01_missing_param.md")
        self.assertIn("signature_matches_param_table", rules(found))
        self.assertTrue(any("windowSizeLen" in f.message for f in found))

    def test_d03_range_conflicts_with_distribution(self):
        found = layer1(DEFECTS / "d03_range_conflict.md")
        self.assertIn("range_matches_distribution", rules(found))

    def test_d07_model_missing_from_perf_table(self):
        found = layer1(DEFECTS / "d07_missing_model.md")
        self.assertIn("models_covered_by_perf_table", rules(found))
        self.assertTrue(any("910B4" in f.message for f in found))

    def test_real_sample_carries_the_expected_defects(self):
        # 回归锚：上游样例的缺陷清单稳定，抓多抓少都说明判据动了。
        found = rules(layer1(SAMPLE))
        for rule in ("signature_matches_param_table",
                     "range_matches_distribution",
                     "models_covered_by_perf_table"):
            with self.subTest(rule=rule):
                self.assertIn(rule, found)

    def test_style_mismatch_reports_differently_than_missing(self):
        # D02 与 D01 是两种病：命名风格不一致 vs 真的漏参。报错要分得开。
        found = layer1(SAMPLE)
        messages = " ".join(f.message for f in found)
        self.assertIn("风格", messages)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 跑测试确认失败**

Run: `python3 -m pytest skill/repo-task-doc-write/tests/test_gate_consistency.py -q`
Expected: FAIL，`test_d01_missing_parameter_is_caught` 报 `KeyError` 或空结果

- [ ] **Step 4: 在 `_checks.py` 追加 L1 判据**

```python
def _param_table(doc):
    section = doc.sections.get("2.4")
    return section.tables[0] if section and section.tables else None


def _column(table, name):
    if not table or name not in table.header:
        return {}
    index = table.header.index(name)
    key = table.header.index("参数名") if "参数名" in table.header else 0
    out = {}
    for offset, row in enumerate(table.rows):
        if max(index, key) >= len(row):
            continue
        out[row[key]] = (row[index], offset)
    return out


def _normalize(name):
    return name.replace("_", "").lower()


def check_signature_matches_param_table(doc, key, element, context):
    import _signature
    section = doc.sections.get("2.3")
    table = _param_table(doc)
    if not section or not table:
        return []
    declared = _signature.parameter_names("\n".join(section.code_blocks))
    if not declared:
        return [Finding("signature_matches_param_table", key, "2.3",
                        section.start_line,
                        "§2.3 解析不出参数名，接口定义要写成代码块")]
    listed = [row[0] for row in table.rows if row and row[0]]
    out = []
    listed_normal = {_normalize(n): n for n in listed}
    for name in declared:
        if name in listed:
            continue
        alias = listed_normal.get(_normalize(name))
        if alias:
            out.append(Finding(
                "signature_matches_param_table", key, "2.4", table.start_line,
                f"参数「{name}」在 §2.4 写成「{alias}」，命名风格不一致 · "
                f"§2.4 参数名要与 §2.3 逐字一致"))
        else:
            out.append(Finding(
                "signature_matches_param_table", key, "2.4", table.start_line,
                f"§2.3 声明了「{name}」，§2.4 参数表没有这一行 · "
                f"{element['failure']}"))
    declared_normal = {_normalize(n) for n in declared}
    for name in listed:
        if _normalize(name) not in declared_normal:
            out.append(Finding(
                "signature_matches_param_table", key, "2.4", table.start_line,
                f"§2.4 有「{name}」但 §2.3 签名里没有 · 多余参数或签名不全"))
    return out


_INTERVAL = re.compile(r"[\[(]\s*(-?[\d.eE+-]+|-∞)\s*,\s*(-?[\d.eE+-]+|∞)\s*[\])]")


def _generation_table(doc):
    section = doc.sections.get("3.5")
    if not section:
        return None
    for table in section.tables:
        if "参数名" in table.header:
            return table
    return None


def check_range_matches_distribution(doc, key, element, context):
    table, generation = _param_table(doc), _generation_table(doc)
    if not table or not generation:
        return []
    ranges = _column(table, "值域范围")
    dist = _column(generation, "Tensor值域分布")
    attrs = _column(generation, "Attr 覆盖规则")
    out = []
    for name, (declared, offset) in ranges.items():
        rule = (dist.get(name, ("", 0))[0] or attrs.get(name, ("", 0))[0]).strip()
        if not rule or rule == "-":
            continue
        if "∞" in declared and rule not in ("-", ""):
            out.append(Finding(
                "range_matches_distribution", key, "2.4",
                table.start_line + 2 + offset,
                f"「{name}」§2.4 值域写 {declared}，§3.5 却给了具体生成规则"
                f"「{rule}」 · 两处必须一致"))
            continue
        bound = _INTERVAL.search(declared)
        rule_bound = _INTERVAL.search(rule)
        if bound and rule_bound and bound.groups() != rule_bound.groups():
            out.append(Finding(
                "range_matches_distribution", key, "2.4",
                table.start_line + 2 + offset,
                f"「{name}」§2.4 值域 {bound.group(0)} 与 §3.5 生成规则 "
                f"{rule_bound.group(0)} 不一致"))
    return out


def check_range_matches_algorithm(doc, key, element, context):
    """§2.1 算法说明里写了某个参数取某值，该值必须落在 §2.4 声明的值域内。"""
    table = _param_table(doc)
    algorithm = _section_text(doc, "2.1")
    if not table or not algorithm:
        return []
    out = []
    for name, (declared, offset) in _column(table, "值域范围").items():
        bound = _INTERVAL.search(declared)
        if not bound:
            continue
        for hit in re.finditer(
                rf"{re.escape(name)}\s*(?:按|取|为|=)\s*(-?\d+)", algorithm):
            value = int(hit.group(1))
            low, high = bound.group(1), bound.group(2)
            open_low = declared.strip().startswith("(")
            if low not in ("-∞",) and (
                    value < float(low) or (open_low and value == float(low))):
                out.append(Finding(
                    "range_matches_algorithm", key, "2.4",
                    table.start_line + 2 + offset,
                    f"§2.1 说「{name}」可取 {value}，不在 §2.4 声明的 "
                    f"{declared} 内"))
    return out


def _models(text):
    return set(re.findall(r"\b91\d[A-Z]\d?\b", text))


def check_models_covered_by_perf_table(doc, key, element, context):
    declared = _models(_section_text(doc, "3.1"))
    covered = _models(_section_text(doc, "3.3"))
    missing = sorted(declared - covered)
    if not missing:
        return []
    section = doc.sections.get("3.3")
    return [Finding("models_covered_by_perf_table", key, "3.3",
                    section.start_line if section else 1,
                    f"§3.1 声明了 {sorted(declared)}，§3.3 性能要求缺 "
                    f"{missing} · {element['failure']}")]


def _tensor_dtypes(doc):
    table = _param_table(doc)
    if not table:
        return set()
    kinds = _column(table, "数据类型")
    out = set()
    for name, (dtype, _) in _column(table, "dtype类型").items():
        if kinds.get(name, ("", 0))[0] != "tensor":
            continue
        out.update(v.strip() for v in re.split(r"[、,，/]", dtype) if v.strip())
    return {d for d in out if d not in ("-", "int", "float", "bool")}


def check_dtypes_covered_by_threshold_table(doc, key, element, context):
    needed = _tensor_dtypes(doc)
    threshold = _section_text(doc, "3.2")
    missing = sorted(d for d in needed if d.upper() not in threshold.upper())
    if not missing:
        return []
    section = doc.sections.get("3.2")
    return [Finding("dtypes_covered_by_threshold_table", key, "3.2",
                    section.start_line if section else 1,
                    f"§2.4 出现的 dtype {missing} 在 §3.2 阈值表里没有 · "
                    f"{element['failure']}")]


def check_thresholds_match_standard(doc, key, element, context):
    """阈值数值与 experimental_standard.md 核对，对不上就是抄错了。"""
    standard = (REFERENCES / "experimental_standard.md").read_text(encoding="utf-8")
    section = doc.sections.get("3.2")
    if not section:
        return []
    out = []
    for table in section.tables:
        if not any("rtol" in c or "atol" in c for c in
                   [table.header[0]] + [r[0] for r in table.rows if r]):
            continue
        for row in table.rows:
            if not row or row[0] not in ("rtol", "atol"):
                continue
            for cell in row[1:]:
                number = re.search(r"2\^-\d+", cell)
                if number and number.group(0) not in standard:
                    out.append(Finding(
                        "thresholds_match_standard", key, "3.2",
                        table.start_line,
                        f"阈值 {number.group(0)} 在 experimental_standard.md "
                        f"里查无此值 · 抄错了或标准变了"))
    return out


def check_params_covered_by_generation_table(doc, key, element, context):
    table, generation = _param_table(doc), _generation_table(doc)
    if not table or not generation:
        return []
    listed = {row[0] for row in table.rows if row and row[0]}
    covered = {row[0] for row in generation.rows if row and row[0]}
    missing = sorted(listed - covered)
    if not missing:
        return []
    return [Finding("params_covered_by_generation_table", key, "3.5",
                    generation.start_line,
                    f"§2.4 的参数 {missing} 在 §3.5 生成规则表里没有 · "
                    f"{element['failure']}")]


CHECKS.update({
    "signature_matches_param_table": check_signature_matches_param_table,
    "range_matches_distribution": check_range_matches_distribution,
    "range_matches_algorithm": check_range_matches_algorithm,
    "models_covered_by_perf_table": check_models_covered_by_perf_table,
    "dtypes_covered_by_threshold_table": check_dtypes_covered_by_threshold_table,
    "thresholds_match_standard": check_thresholds_match_standard,
    "params_covered_by_generation_table": check_params_covered_by_generation_table,
})
```

- [ ] **Step 5: 拷共享事实副本**

`check_thresholds_match_standard` 要读 `experimental_standard.md`。

```bash
cp skill/repo-task-atk-test/references/experimental_standard.md \
   skill/repo-task-doc-write/references/
cp skill/repo-task-atk-test/references/random-operator-signals.json \
   skill/repo-task-doc-write/references/
```

- [ ] **Step 6: 跑测试确认通过**

Run: `python3 -m pytest skill/repo-task-doc-write/tests/test_gate_consistency.py -q`
Expected: `6 passed, 3 subtests passed`

- [ ] **Step 7: 提交**

```bash
git add skill/repo-task-doc-write
git commit -m "feat: 质量门 L1 一致性层

判别力最强的一层，七条判据全部跨章节比对：
- §2.3 签名参数名集合与 §2.4 表格逐字一致
- §2.4 值域与 §3.5 生成规则不矛盾
- §2.4 值域与 §2.1 算法说明不矛盾
- §3.1 声明的每个具体型号在 §3.3 有性能要求
- §2.4 出现的每种张量 dtype 在 §3.2 有阈值
- §3.2 阈值数值与 experimental_standard.md 核对
- §2.4 的参数在 §3.5 生成规则表里都有

漏参与命名风格不一致报成两种错：前者要补一行，后者要改写法，
修法完全不同。上游样例两种都占（D01 与 D02）。"
```

---

## Task 8: 明确性门（L2）

**Files:**
- Modify: `skill/repo-task-doc-write/scripts/_checks.py`
- Create: `skill/repo-task-doc-write/tests/fixtures/defects/d06_vague_perf.md`
- Create: `skill/repo-task-doc-write/tests/fixtures/defects/d08_uniform_error.md`
- Create: `skill/repo-task-doc-write/tests/test_gate_clarity.py`

**Interfaces:**
- Consumes: Task 6 的 `_vague()`、Task 7 的 `_param_table` 与 `_column`
- Produces: `_checks.CHECKS` 新增七个键

- [ ] **Step 1: 造两个缺陷 fixture**

```bash
python3 - <<'EOF'
from pathlib import Path
root = Path("skill/repo-task-doc-write")
golden = (root / "references" / "golden-task-doc.md").read_text(encoding="utf-8")
out = root / "tests" / "fixtures" / "defects"

# D06：性能判据换回「持平」
line = [l for l in golden.splitlines() if "不低于" in l and "0.8" in l]
assert line, "黄金样例 §3.3 判据写法变了，改这个 fixture 的锚点"
(out / "d06_vague_perf.md").write_text(
    golden.replace(line[0],
                   "要求在 fp16 和 bf16 输入场景下，基于 910B3 的性能与 "
                   "0.8 倍【FastVideo.sliding_tile_attention + GPU(A100)】持平。"),
    encoding="utf-8")

# D08：异常行为列整列写成同一句话
import re
lines = golden.splitlines()
table = [i for i, l in enumerate(lines) if l.startswith("| q ")][0]
for i in range(table, len(lines)):
    if not lines[i].startswith("|"):
        break
    cells = lines[i].split("|")
    if len(cells) >= 11:
        cells[-2] = " 触发参数校验报错 "
        lines[i] = "|".join(cells)
(out / "d08_uniform_error.md").write_text("\n".join(lines), encoding="utf-8")
EOF
```

- [ ] **Step 2: 写 L2 测试（先失败）**

```python
# skill/repo-task-doc-write/tests/test_gate_clarity.py
"""L2 明确性门。抓的是读得懂但没有一步能照着执行的写法。"""

import json
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import _checks  # noqa: E402
import _taskdoc_parser as parser  # noqa: E402

GOLDEN = SKILL_ROOT / "references" / "golden-task-doc.md"
SAMPLE = SKILL_ROOT / "tests" / "fixtures" / "real_sample.md"
DEFECTS = SKILL_ROOT / "tests" / "fixtures" / "defects"


def spine():
    return json.loads((SKILL_ROOT / "references"
                       / "taskdoc-elements.json").read_text(encoding="utf-8"))


def layer2(path, context=None):
    text = path.read_text(encoding="utf-8")
    signals = json.loads((SKILL_ROOT / "references"
                          / "random-operator-signals.json").read_text(
                              encoding="utf-8"))["signal_keywords"]
    base = {"random_signal_hit": any(w in text for w in signals),
            "decisions": {}, "expected_header": {}}
    base.update(context or {})
    return _checks.run_layer(parser.parse(path), spine(), "L2", base)


def rules(findings):
    return sorted({f.rule for f in findings})


class ClarityLayerTest(unittest.TestCase):
    def test_golden_passes_layer_two(self):
        self.assertEqual([], layer2(GOLDEN))

    def test_d06_vague_performance_criterion_is_caught(self):
        found = layer2(DEFECTS / "d06_vague_perf.md")
        self.assertIn("no_vague_word", rules(found))
        self.assertIn("perf_criterion_has_comparator", rules(found))
        self.assertTrue(any("持平" in f.message for f in found))

    def test_d08_uniform_error_column_is_caught(self):
        found = layer2(DEFECTS / "d08_uniform_error.md")
        self.assertIn("error_column_not_uniform", rules(found))

    def test_sample_unbounded_range_is_caught(self):
        found = layer2(SAMPLE)
        self.assertIn("no_unbounded_range", rules(found))
        self.assertTrue(any("替代写法" in f.message for f in found))

    def test_sample_random_signal_demands_a_strategy(self):
        # D12：样例 §3.5 写了「正态分布」命中信号词，却没给对比策略。
        found = layer2(SAMPLE)
        self.assertIn("random_strategy_when_signaled", rules(found))

    def test_vague_words_are_scoped_not_global(self):
        # 「建议参考」在 §6 参考资料合法，在 §3.2 精度要求里违规。
        found = layer2(GOLDEN)
        self.assertEqual([], [f for f in found
                              if f.rule == "no_vague_word" and f.section == "6"])

    def test_reference_section_may_use_advisory_wording(self):
        text = GOLDEN.read_text(encoding="utf-8")
        self.assertIn("## 6.", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 跑测试确认失败**

Run: `python3 -m pytest skill/repo-task-doc-write/tests/test_gate_clarity.py -q`
Expected: FAIL，多条断言报空结果

- [ ] **Step 4: 在 `_checks.py` 追加 L2 判据**

```python
_UNBOUNDED = re.compile(r"\(\s*-?∞\s*,\s*∞\s*\)|\(-inf,\s*inf\)", re.IGNORECASE)
_COMPARATOR = re.compile(r"[≥≤><]=?|不低于|不高于|不超过|至少|不小于|不大于")
_NUMBER = re.compile(r"\d")


def check_no_unbounded_range(doc, key, element, context):
    table = _param_table(doc)
    if not table:
        return []
    kinds = _column(table, "数据类型")
    out = []
    for name, (declared, offset) in _column(table, "值域范围").items():
        if not _UNBOUNDED.search(declared):
            continue
        if kinds.get(name, ("", 0))[0] != "tensor":
            continue
        out.append(Finding(
            "no_unbounded_range", key, "2.4", table.start_line + 2 + offset,
            f"「{name}」值域写 {declared}，生成不出数据 · "
            f"替代写法：`全值域（含 Inf/NaN：不测）` 或给出上下界如 `[-1e4, 1e4]`"))
    return out


def check_no_vague_word(doc, key, element, context):
    if not element["vague_forbidden"]:
        return []
    words = _vague()["words"]
    out = []
    for line, text in _element_lines(doc, element):
        for word, replacement in words.items():
            if word in text:
                out.append(Finding(
                    "no_vague_word", key, element["section"], line,
                    f"「{word}」没有边界 · 改成：{replacement}"))
    return out


def check_perf_criterion_has_comparator(doc, key, element, context):
    text = _section_text(doc, "3.3")
    section = doc.sections.get("3.3")
    if not section:
        return []
    prose = "\n".join(l for l in section.lines if not l.strip().startswith("|"))
    if _COMPARATOR.search(prose) and _NUMBER.search(prose):
        return []
    return [Finding("perf_criterion_has_comparator", key, "3.3",
                    section.start_line,
                    "性能判据必须含比较符与数字，如「不低于标杆的 0.8 倍」 · "
                    f"{element['failure']}")]


def check_error_column_not_uniform(doc, key, element, context):
    table = _param_table(doc)
    if not table or "异常行为" not in table.header:
        return []
    index = table.header.index("异常行为")
    values = [row[index].strip() for row in table.rows
              if index < len(row) and row[index].strip() not in ("", "-")]
    if len(values) < 2 or len(set(values)) > 1:
        return []
    return [Finding("error_column_not_uniform", key, "2.4", table.start_line,
                    f"异常行为列 {len(values)} 行全是「{values[0]}」 · "
                    f"要对非法 dtype、非法 shape、非法值域分别写出具体触发条件"
                    f" · {element['failure']}")]


def check_per_dtype_threshold(doc, key, element, context):
    section = doc.sections.get("3.2")
    if not section:
        return []
    if any(len(t.header) >= 2 for t in section.tables):
        return []
    return [Finding("per_dtype_threshold", key, "3.2", section.start_line,
                    "§3.2 必须逐 dtype 给阈值表，不能只写一句「满足生态标准」 · "
                    f"{element['failure']}")]


_VERSIONED = re.compile(r"\d+\.\d+")


def check_env_lists_third_party_versions(doc, key, element, context):
    text = _section_text(doc, element["section"])
    if not text:
        return []
    missing = [name for name in ("torch",) if name not in text.lower()]
    if element["section"] == "3.1" and missing:
        section = doc.sections["3.1"]
        return [Finding("env_lists_third_party_versions", key, "3.1",
                        section.start_line,
                        f"§3.1 未写三方软件版本（缺 {missing}） · "
                        f"{element['failure']}")]
    if not _VERSIONED.search(text):
        section = doc.sections[element["section"]]
        return [Finding("env_lists_third_party_versions", key,
                        element["section"], section.start_line,
                        f"§{element['section']} 提到了环境但没有版本号 · "
                        f"{element['failure']}")]
    return []


_STRATEGIES = ("equal_vs_builtin_pinned_seed", "deterministic_boundary_only",
               "distribution_test", "self_consistency")


def check_random_strategy_when_signaled(doc, key, element, context):
    if not context.get("random_signal_hit"):
        return []
    text = _section_text(doc, "3.2")
    section = doc.sections.get("3.2")
    if any(s in text for s in _STRATEGIES) or "不涉及" in text:
        return []
    return [Finding("random_strategy_when_signaled", key, "3.2",
                    section.start_line if section else 1,
                    "正文命中随机数信号词，§3.2 必须给出对比判定策略 · "
                    f"四选一：{_STRATEGIES}，或写「不涉及」并说明理由 · "
                    f"{element['failure']}")]


CHECKS.update({
    "no_unbounded_range": check_no_unbounded_range,
    "no_vague_word": check_no_vague_word,
    "perf_criterion_has_comparator": check_perf_criterion_has_comparator,
    "error_column_not_uniform": check_error_column_not_uniform,
    "per_dtype_threshold": check_per_dtype_threshold,
    "env_lists_third_party_versions": check_env_lists_third_party_versions,
    "random_strategy_when_signaled": check_random_strategy_when_signaled,
})
```

`no_vague_word` 要挂到骨架里全部 `vague_forbidden: true` 的要素上。
在 `taskdoc-elements.json` 里为这些要素的 `checks` 数组追加 `"no_vague_word"`。

- [ ] **Step 5: 跑两轮测试**

Run: `python3 -m pytest skill/repo-task-doc-write/tests/ -q`
Expected: 全绿。`test_elements_spine.py` 也要过——`no_vague_word` 已在
`check_registry` 里，追加到 `checks` 不会破不变量。

- [ ] **Step 6: 提交**

```bash
git add skill/repo-task-doc-write
git commit -m "feat: 质量门 L2 明确性层

抓的是读得懂但没有一步能照着执行的写法。七条判据：
- 张量输入不接受 (-∞,∞)，报错给出替代写法而不是简单拒绝
- 模糊词按要素作用域生效，「建议参考」在 §6 合法在 §3.2 违规
- 性能判据必须含比较符与数字
- 异常行为列不得整列同一句话
- §3.2 必须逐 dtype 给阈值表
- §3.1 与 §3.3 必须写三方软件版本
- 随机信号词命中时必须给对比策略或明确写「不涉及」

模糊词表每条都配替代写法。只禁不给替换，下一个人只会换一个同样模糊的词。"
```

---

## Task 9: 拍板留痕（L3）与双受众（L4）

**Files:**
- Modify: `skill/repo-task-doc-write/scripts/_checks.py`
- Create: `skill/repo-task-doc-write/scripts/_acceptance_map.py`
- Modify: `skill/repo-task-doc-write/scripts/check_taskdoc.py`
- Create: `skill/repo-task-doc-write/tests/fixtures/decisions_golden.json`
- Create: `skill/repo-task-doc-write/tests/test_gate_decisions.py`

**Interfaces:**
- Consumes: Task 2 骨架的 `decision_owner`、Task 6 的 `run_layer`
- Produces:
  - `decisions.json` 结构：顶层键是要素 id，值含 `agent_proposal`、`provenance`、`rationale`、`human_reply`、`state`
  - `state` 取 `human_confirmed`、`human_modified`、`human_supplied`
  - `provenance` 取 `model_knowledge`、`local_probe`、`user_supplied`
  - `_acceptance_map.build(doc, spine, context) -> dict`，写到 `evidence/acceptance_map.json`

- [ ] **Step 1: 造 decisions fixture**

```bash
python3 - <<'EOF'
import json
from pathlib import Path
root = Path("skill/repo-task-doc-write")
spine = json.loads((root / "references" / "taskdoc-elements.json").read_text(
    encoding="utf-8"))
out = {}
for key, element in spine["elements"].items():
    if element["decision_owner"] != "human":
        continue
    out[key] = {
        "agent_proposal": "",
        "provenance": "user_supplied",
        "rationale": "需求方直接给出",
        "human_reply": f"确认 · {element['name']}",
        "state": "human_supplied",
    }
(root / "tests" / "fixtures" / "decisions_golden.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(len(out), "条 human 要素")
EOF
```

Expected: 打印 `38 条 human 要素`

- [ ] **Step 2: 写 L3/L4 测试（先失败）**

```python
# skill/repo-task-doc-write/tests/test_gate_decisions.py
"""L3 拍板留痕与 L4 双受众。红线 A：agent 声称已确认不算数。"""

import json
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import _acceptance_map  # noqa: E402
import _checks  # noqa: E402
import _taskdoc_parser as parser  # noqa: E402

GOLDEN = SKILL_ROOT / "references" / "golden-task-doc.md"
DECISIONS = SKILL_ROOT / "tests" / "fixtures" / "decisions_golden.json"


def spine():
    return json.loads((SKILL_ROOT / "references"
                       / "taskdoc-elements.json").read_text(encoding="utf-8"))


def run(layer, decisions):
    context = {"decisions": decisions, "random_signal_hit": False,
               "expected_header": {}}
    return _checks.run_layer(parser.parse(GOLDEN), spine(), layer, context)


class DecisionLayerTest(unittest.TestCase):
    def setUp(self):
        self.decisions = json.loads(DECISIONS.read_text(encoding="utf-8"))

    def test_complete_decisions_pass(self):
        self.assertEqual([], run("L3", self.decisions))

    def test_missing_decision_is_caught(self):
        self.decisions.pop("2.5.non_contiguous")
        found = run("L3", self.decisions)
        self.assertTrue(any("2.5.non_contiguous" in f.message for f in found))

    def test_empty_human_reply_is_caught(self):
        self.decisions["2.4.dtype"]["human_reply"] = "   "
        found = run("L3", self.decisions)
        self.assertTrue(any("2.4.dtype" in f.message for f in found))

    def test_terse_reply_still_counts_as_confirmation(self):
        # 门禁不做语义判断：「对」也算确认，空字符串才判红。
        self.decisions["2.4.dtype"]["human_reply"] = "对"
        self.assertEqual([], run("L3", self.decisions))

    def test_agent_owned_elements_need_no_reply(self):
        self.decisions.pop("6.references", None)
        self.assertEqual([], run("L3", self.decisions))


class AcceptanceMapTest(unittest.TestCase):
    def test_map_covers_the_intake_constraint_table(self):
        built = _acceptance_map.build(parser.parse(GOLDEN), spine(), {})
        for field in ("算子", "输入", "非连续 Tensor", "输出", "错误语义",
                      "精度", "性能", "环境", "提交"):
            with self.subTest(field=field):
                self.assertIn(field, built)

    def test_golden_leaves_no_unconfirmed_field(self):
        built = _acceptance_map.build(parser.parse(GOLDEN), spine(), {})
        pending = [k for k, v in built.items() if v == "待确认"]
        self.assertEqual([], pending)

    def test_layer_four_flags_pending_fields(self):
        found = run("L4", json.loads(DECISIONS.read_text(encoding="utf-8")))
        self.assertEqual([], found)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 跑测试确认失败**

Run: `python3 -m pytest skill/repo-task-doc-write/tests/test_gate_decisions.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named '_acceptance_map'`

- [ ] **Step 4: 实现 `_acceptance_map.py`**

```python
"""反填验收侧 S1 的约束表。

产物 evidence/acceptance_map.json 是交给验收 agent 的接力棒：验收 skill 的
references/intake.md#验收约束表 要填的那张表，这里提前填好。

某一行填不出来就写「待确认」，L4 会据此判红——任务书没写清的东西，
验收阶段一样要停下来问，早三个阶段发现代价低得多。
"""

import re

FIELDS = ("算子", "输入", "非连续 Tensor", "输出", "功能范围", "语义形态",
          "错误语义", "精度", "性能", "环境", "提交", "构建")

PENDING = "待确认"


def _text(doc, number):
    section = doc.sections.get(number)
    return "\n".join(section.lines).strip() if section else ""


def _param_rows(doc):
    section = doc.sections.get("2.4")
    if not section or not section.tables:
        return []
    return section.tables[0].rows


def _by_direction(doc, wanted):
    rows = _param_rows(doc)
    section = doc.sections.get("2.4")
    header = section.tables[0].header if section and section.tables else []
    if "输入／输出/属性" not in header:
        return []
    index = header.index("输入／输出/属性")
    return [row[0] for row in rows
            if index < len(row) and wanted in row[index]]


def build(doc, spine, context):
    """返回 {约束表字段: 取值或「待确认」}。"""
    out = {}
    out["算子"] = doc.title or PENDING
    inputs = _by_direction(doc, "输入")
    out["输入"] = "、".join(inputs) if inputs else PENDING
    outputs = _by_direction(doc, "输出")
    out["输出"] = "、".join(outputs) if outputs else PENDING

    constraint = _text(doc, "2.5")
    hit = re.search(r"非连续\s*Tensor[^\n|]*[|：:]\s*([^\n|]+)", constraint)
    out["非连续 Tensor"] = hit.group(1).strip() if hit else PENDING

    out["功能范围"] = _text(doc, "2.1")[:200] or PENDING
    out["语义形态"] = "不构造（任务书未要求）"

    section = doc.sections.get("2.4")
    header = section.tables[0].header if section and section.tables else []
    if "异常行为" in header:
        index = header.index("异常行为")
        errors = [f"{row[0]}: {row[index]}" for row in _param_rows(doc)
                  if index < len(row) and row[index].strip() not in ("", "-")]
        out["错误语义"] = "；".join(errors) if errors else PENDING
    else:
        out["错误语义"] = PENDING

    out["精度"] = _text(doc, "3.2")[:300] or PENDING
    out["性能"] = _text(doc, "3.3")[:300] or PENDING
    out["环境"] = _text(doc, "3.1") or PENDING
    out["提交"] = _text(doc, "2.2") or PENDING
    out["构建"] = _text(doc, "5") or PENDING
    return out
```

- [ ] **Step 5: 在 `_checks.py` 追加 L3/L4 判据**

```python
def check_human_reply_recorded(doc, key, element, context):
    """红线 A：human 拍板的要素必须有一段人的答复原话。"""
    if element["decision_owner"] != "human":
        return []
    record = context.get("decisions", {}).get(key)
    section = doc.sections.get(element["section"])
    line = section.start_line if section else 1
    if not record:
        return [Finding("human_reply_recorded", key, element["section"], line,
                        f"{key}（{element['name']}）在 decisions.json 里没有记录 · "
                        f"agent 推断不是事实，必须人拍板")]
    if not str(record.get("human_reply", "")).strip():
        return [Finding("human_reply_recorded", key, element["section"], line,
                        f"{key}（{element['name']}）的 human_reply 为空 · "
                        f"agent 声称已确认不算数")]
    return []


def check_deliverables_complete(doc, key, element, context):
    required = ("设计文档", "自测用例", "自测报告", "代码")
    text = _section_text(doc, "4")
    missing = [item for item in required if item not in text]
    if not missing:
        return []
    section = doc.sections.get("4")
    return [Finding("deliverables_complete", key, "4",
                    section.start_line if section else 1,
                    f"§4 交付件缺 {missing} · {element['failure']}")]


def check_pr_target_is_a_path(doc, key, element, context):
    text = _section_text(doc, "5")
    section = doc.sections.get("5")
    line = section.start_line if section else 1
    hit = re.search(r"https?://\S+", text)
    if not hit:
        return [Finding("pr_target_is_a_path", key, "5", line,
                        f"§5 没有裸链接的提交地址 · {element['failure']}")]
    tail = hit.group(0).rstrip("。 ，").split("//", 1)[-1]
    if tail.count("/") < 4:
        return [Finding("pr_target_is_a_path", key, "5", line,
                        f"§5 的地址 {hit.group(0)} 只到仓一级，要具体到目录 · "
                        f"{element['failure']}")]
    return []


CHECKS.update({
    "human_reply_recorded": check_human_reply_recorded,
    "deliverables_complete": check_deliverables_complete,
    "pr_target_is_a_path": check_pr_target_is_a_path,
})
```

把 `human_reply_recorded` 挂到骨架里**全部** `decision_owner: human` 的要素的
`checks` 数组上。

- [ ] **Step 6: 让 `check_taskdoc.py` 落盘 acceptance_map**

在 `main()` 里，写封条那段之前插入：

```python
    if args.evidence_dir:
        evidence = Path(args.evidence_dir)
        evidence.mkdir(parents=True, exist_ok=True)
        import _acceptance_map
        built = _acceptance_map.build(doc, spine, context)
        (evidence / "acceptance_map.json").write_text(
            json.dumps(built, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        pending = [k for k, v in built.items() if v == "待确认"]
        if pending:
            print(f"§L4 验收族：约束表仍有待确认项 {pending}")
            return 2
```

- [ ] **Step 7: 跑全部测试**

Run: `python3 -m pytest skill/repo-task-doc-write/tests/ -q`
Expected: 全绿

- [ ] **Step 8: 提交**

```bash
git add skill/repo-task-doc-write
git commit -m "feat: 质量门 L3 拍板留痕与 L4 双受众

L3 落红线 A：每个 human 拍板的要素在 decisions.json 里必须有一段人的
答复原话。门禁不做语义判断——「对」也算确认，空字符串才判红。校原话
而不是校布尔标志，agent 就没法自己盖章。

L4 分两族。开发者族查交付件齐全与 PR 目录具体到路径；验收族反填
intake.md 的约束表，产物 acceptance_map.json 是交给验收 agent 的接力棒。
表里还有「待确认」就判红——任务书没写清的东西验收阶段一样要停下来问，
早三个阶段发现代价低得多。"
```

---

## Task 10: 封条与不可绕过

**Files:**
- Create: `skill/repo-task-doc-write/tests/test_gate_seal.py`

**Interfaces:**
- Consumes: Task 6 的 `check_taskdoc.py --evidence-dir`
- Produces: `evidence/gate_pass.json` 的契约，Task 13 的 `SKILL.md` 按它写交付硬要求

- [ ] **Step 1: 写封条测试（先失败）**

```python
# skill/repo-task-doc-write/tests/test_gate_seal.py
"""红线 C：门禁不可绕过。

光靠 SKILL.md 写「必须跑门禁」不够，agent 可能不跑还声称跑了。
封条含被检 md 的 sha256：没有匹配的封条就是没过门；跑完又改稿，
sha256 也对不上。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
GATE = SKILL_ROOT / "scripts" / "check_taskdoc.py"
GOLDEN = SKILL_ROOT / "references" / "golden-task-doc.md"
DECISIONS = SKILL_ROOT / "tests" / "fixtures" / "decisions_golden.json"


def run_gate(doc, evidence):
    return subprocess.run(
        [sys.executable, str(GATE), "--doc", str(doc),
         "--decisions", str(DECISIONS), "--evidence-dir", str(evidence)],
        capture_output=True, text=True)


class SealTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)
        self.doc = self.work / "Golden_task_doc.md"
        self.doc.write_text(GOLDEN.read_text(encoding="utf-8"), encoding="utf-8")
        self.evidence = self.work / "evidence"

    def tearDown(self):
        self.tmp.cleanup()

    def test_passing_writes_a_seal(self):
        result = run_gate(self.doc, self.evidence)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        seal = json.loads((self.evidence / "gate_pass.json").read_text(
            encoding="utf-8"))
        for field in ("doc", "sha256", "layers", "checked_at"):
            with self.subTest(field=field):
                self.assertIn(field, seal)

    def test_seal_matches_the_checked_document(self):
        import hashlib
        run_gate(self.doc, self.evidence)
        seal = json.loads((self.evidence / "gate_pass.json").read_text(
            encoding="utf-8"))
        self.assertEqual(hashlib.sha256(self.doc.read_bytes()).hexdigest(),
                         seal["sha256"])

    def test_editing_after_the_gate_invalidates_the_seal(self):
        import hashlib
        run_gate(self.doc, self.evidence)
        seal = json.loads((self.evidence / "gate_pass.json").read_text(
            encoding="utf-8"))
        self.doc.write_text(
            self.doc.read_text(encoding="utf-8") + "\n补一句话。\n",
            encoding="utf-8")
        self.assertNotEqual(hashlib.sha256(self.doc.read_bytes()).hexdigest(),
                            seal["sha256"])

    def test_failing_writes_no_seal(self):
        broken = self.work / "broken.md"
        broken.write_text(
            GOLDEN.read_text(encoding="utf-8").replace("### 2.5", "### 9.9"),
            encoding="utf-8")
        result = run_gate(broken, self.evidence)
        self.assertEqual(2, result.returncode)
        self.assertFalse((self.evidence / "gate_pass.json").is_file())

    def test_acceptance_map_lands_next_to_the_seal(self):
        run_gate(self.doc, self.evidence)
        built = json.loads((self.evidence / "acceptance_map.json").read_text(
            encoding="utf-8"))
        self.assertIn("算子", built)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试**

Run: `python3 -m pytest skill/repo-task-doc-write/tests/test_gate_seal.py -q`
Expected: `5 passed`

若 `test_failing_writes_no_seal` 红，说明 `check_taskdoc.py` 在 L4 判红前
已经写了封条。把写封条那段移到全部 `return 2` 之后。

- [ ] **Step 3: 提交**

```bash
git add skill/repo-task-doc-write/tests/test_gate_seal.py
git commit -m "test: 门禁封条的三条性质

红线 C 的机器可读部分：通过才写封条、封条 sha256 等于被检 md、
改稿后 sha256 失配。

第三条堵的是第二个漏洞——跑完门禁又改了 md。没有它，封条只能证明
「某个版本过了」，证明不了「交付的这个版本过了」。"
```

---

## Task 11: 追问调度

**Files:**
- Create: `skill/repo-task-doc-write/scripts/next_questions.py`
- Create: `skill/repo-task-doc-write/tests/test_next_questions.py`

**Interfaces:**
- Consumes: Task 2 骨架、Task 9 的 `decisions.json` 结构
- Produces: `next_questions.py --decisions <json> [--doc <md>] [--json]`，
  输出下一批要问的要素；批次定义写进骨架的新顶层键 `question_batches`

- [ ] **Step 1: 给骨架加批次定义**

在 `taskdoc-elements.json` 顶层加：

```json
"question_batches": [
  {"id": 1, "name": "前提", "elements": ["2.2.project_mode", "2.1.baseline", "1.repo"]},
  {"id": 2, "name": "接口", "elements": ["2.3.signature", "2.1.operator_name"]},
  {"id": 3, "name": "类型与排布", "elements": ["2.4.data_type", "2.4.dtype", "2.4.format"]},
  {"id": 4, "name": "形状与值域", "elements": ["2.4.shape", "2.4.value_range"]},
  {"id": 5, "name": "异常行为", "elements": ["2.4.error_behavior"]},
  {"id": 6, "name": "实现约束", "elements": ["2.5.non_contiguous", "2.5.broadcast", "2.5.dynamic_shape", "2.5.inplace_view", "2.5.deterministic", "2.5.empty_tensor"]},
  {"id": 7, "name": "精度", "elements": ["3.2.reference_api", "3.2.threshold_table", "3.2.verdict_formula", "3.2.random_strategy"]},
  {"id": 8, "name": "性能", "elements": ["3.3.baseline_env", "3.3.criterion", "3.3.case_table"]}
]
```

`test_elements_spine.py` 追加一条不变量：

```python
    def test_question_batches_reference_real_elements(self):
        known = set(self.elements)
        unknown = []
        for batch in self.data["question_batches"]:
            for key in batch["elements"]:
                if key not in known:
                    unknown.append(f"批 {batch['id']}: {key}")
        self.assertEqual([], unknown)

    def test_every_batched_element_is_human_owned(self):
        # 批次是拿去问人的，不该混进 agent 或 template 拍板的要素。
        bad = []
        for batch in self.data["question_batches"]:
            for key in batch["elements"]:
                if self.elements[key]["decision_owner"] != "human":
                    bad.append(f"批 {batch['id']}: {key}")
        self.assertEqual([], bad)
```

- [ ] **Step 2: 写调度测试（先失败）**

```python
# skill/repo-task-doc-write/tests/test_next_questions.py
"""追问调度：算出还缺哪些拍板项，按批次给出下一步。"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "next_questions.py"
DECISIONS = SKILL_ROOT / "tests" / "fixtures" / "decisions_golden.json"


def run(decisions_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--decisions", str(decisions_path),
         "--json"], capture_output=True, text=True)
    return result.returncode, json.loads(result.stdout or "{}")


class NextQuestionsTest(unittest.TestCase):
    def test_complete_decisions_report_done(self):
        code, payload = run(DECISIONS)
        self.assertEqual(0, code)
        self.assertEqual([], payload["pending"])
        self.assertTrue(payload["done"])

    def test_empty_decisions_start_at_batch_one(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as handle:
            handle.write("{}")
            path = handle.name
        code, payload = run(path)
        self.assertEqual(0, code)
        self.assertEqual(1, payload["next_batch"]["id"])
        self.assertEqual("前提", payload["next_batch"]["name"])
        self.assertFalse(payload["done"])

    def test_partial_decisions_skip_to_the_first_gap(self):
        full = json.loads(DECISIONS.read_text(encoding="utf-8"))
        for key in ("2.5.non_contiguous", "2.5.broadcast"):
            full.pop(key)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as handle:
            json.dump(full, handle, ensure_ascii=False)
            path = handle.name
        code, payload = run(path)
        self.assertEqual(6, payload["next_batch"]["id"])
        self.assertEqual(["2.5.non_contiguous", "2.5.broadcast"],
                         payload["next_batch"]["ask"])

    def test_payload_carries_failure_mode_for_each_question(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as handle:
            handle.write("{}")
            path = handle.name
        _, payload = run(path)
        for item in payload["next_batch"]["detail"]:
            with self.subTest(item=item["key"]):
                self.assertTrue(item["failure"])
                self.assertIn("agent_may_propose", item)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 跑测试确认失败**

Run: `python3 -m pytest skill/repo-task-doc-write/tests/test_next_questions.py -q`
Expected: FAIL，脚本不存在

- [ ] **Step 4: 实现 `next_questions.py`**

```python
#!/usr/bin/env python3
"""算出还缺哪些拍板项，按批次输出下一步该问什么。

40 条要素里约 38 条要人拍板，逐条问 38 轮不现实。批次定义在骨架的
question_batches 里，每批 3-6 个相关项一次呈现。

只报「缺什么」，不替人回答。答复原话由 agent 记进 decisions.json。
"""

import argparse
import json
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SPINE = SKILL_ROOT / "references" / "taskdoc-elements.json"


def answered(record):
    return bool(record) and bool(str(record.get("human_reply", "")).strip())


def main(argv=None):
    ap = argparse.ArgumentParser(description="追问调度")
    ap.add_argument("--decisions", required=True)
    ap.add_argument("--doc", help="任务书 md，用于判断条件必填是否生效")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args(argv)

    spine = json.loads(SPINE.read_text(encoding="utf-8"))
    elements = spine["elements"]
    path = Path(args.decisions)
    decisions = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    signal_hit = False
    if args.doc and Path(args.doc).is_file():
        signals = json.loads((SKILL_ROOT / "references"
                              / "random-operator-signals.json").read_text(
                                  encoding="utf-8"))["signal_keywords"]
        text = Path(args.doc).read_text(encoding="utf-8")
        signal_hit = any(word in text for word in signals)

    def needed(key):
        element = elements[key]
        if element["decision_owner"] != "human":
            return False
        if element["required"] == "conditional":
            if element["condition"] == "random_signal_hit":
                return signal_hit
            return False
        return True

    pending, next_batch = [], None
    for batch in spine["question_batches"]:
        ask = [k for k in batch["elements"]
               if needed(k) and not answered(decisions.get(k))]
        pending.extend(ask)
        if ask and next_batch is None:
            next_batch = {
                "id": batch["id"], "name": batch["name"], "ask": ask,
                "detail": [{
                    "key": k,
                    "name": elements[k]["name"],
                    "section": elements[k]["section"],
                    "agent_may_propose": elements[k]["agent_may_propose"],
                    "failure": elements[k]["failure"],
                } for k in ask],
            }

    outside = [k for k in elements
               if needed(k) and not answered(decisions.get(k))
               and not any(k in b["elements"] for b in spine["question_batches"])]
    pending.extend(outside)

    payload = {"done": not pending, "pending": pending,
               "next_batch": next_batch,
               "unbatched_pending": outside}

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if not pending:
        print("全部拍板项已有答复原话，可以落稿。")
        return 0
    print(f"还缺 {len(pending)} 项。下一批：第 {next_batch['id']} 批 "
          f"{next_batch['name']}")
    for item in next_batch["detail"]:
        mark = "agent 可先给候选" if item["agent_may_propose"] else "只能问人"
        print(f"  §{item['section']} {item['name']}（{mark}）")
        print(f"      不写会怎样：{item['failure']}")
    if outside:
        print(f"\n批次外还缺：{outside}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python3 -m pytest skill/repo-task-doc-write/tests/ -q`
Expected: 全绿

- [ ] **Step 6: 提交**

```bash
git add skill/repo-task-doc-write
git commit -m "feat: 追问调度按决策簇分批

38 条要人拍板的要素逐条问 38 轮不现实。批次定义进骨架的
question_batches，每批 3-6 个相关项一次呈现，比如 dtype 与排布格式
同批——它们相互约束，分开问会来回改。

每个问题附带「不写会怎样」，取自骨架的 failure 字段。人要判断该不该
拍这个板，得先知道不拍的代价。

只报缺什么，不替人回答。"
```

---

## Task 12: 脚手架渲染与派生视图

**Files:**
- Create: `skill/repo-task-doc-write/scripts/make_taskdoc.py`
- Create: `skill/repo-task-doc-write/scripts/render_views.py`
- Create: `skill/repo-task-doc-write/references/manual-checklist.md`
- Create: `skill/repo-task-doc-write/tests/test_scaffold_and_views.py`

**Interfaces:**
- Consumes: Task 2 骨架、Task 3 模板
- Produces:
  - `make_taskdoc.py --op <名> --out <md>`，渲染一次性脚手架
  - `render_views.py [--write]`，从骨架渲染 `manual-checklist.md`

- [ ] **Step 1: 写测试（先失败）**

```python
# skill/repo-task-doc-write/tests/test_scaffold_and_views.py
"""脚手架渲染与派生视图。视图从骨架派生，骨架是唯一可编辑对象。"""

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import _taskdoc_parser as parser  # noqa: E402

MAKE = SKILL_ROOT / "scripts" / "make_taskdoc.py"
RENDER = SKILL_ROOT / "scripts" / "render_views.py"
CHECKLIST = SKILL_ROOT / "references" / "manual-checklist.md"


def spine():
    return json.loads((SKILL_ROOT / "references"
                       / "taskdoc-elements.json").read_text(encoding="utf-8"))


class ScaffoldTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "Roll_task_doc.md"
        result = subprocess.run(
            [sys.executable, str(MAKE), "--op", "aclnnRoll",
             "--out", str(self.out)], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def tearDown(self):
        self.tmp.cleanup()

    def test_scaffold_has_every_section(self):
        doc = parser.parse(self.out)
        for number in spine()["sections"]:
            with self.subTest(number=number):
                self.assertIn(number, doc.sections)

    def test_scaffold_title_carries_the_operator_name(self):
        self.assertIn("aclnnRoll", self.out.read_text(encoding="utf-8"))

    def test_scaffold_param_table_header_is_correct(self):
        doc = parser.parse(self.out)
        self.assertEqual(9, len(doc.sections["2.4"].tables[0].header))

    def test_scaffold_fails_structure_gate_but_not_parse(self):
        # 脚手架是空壳：结构解析得动（不是 3），内容判据不满足（是 2）。
        result = subprocess.run(
            [sys.executable, str(SKILL_ROOT / "scripts" / "check_taskdoc.py"),
             "--doc", str(self.out), "--layers", "L0"],
            capture_output=True, text=True)
        self.assertEqual(2, result.returncode)

    def test_refuses_to_overwrite(self):
        result = subprocess.run(
            [sys.executable, str(MAKE), "--op", "aclnnRoll",
             "--out", str(self.out)], capture_output=True, text=True)
        self.assertEqual(2, result.returncode)
        self.assertIn("已存在", result.stderr + result.stdout)


class ViewsTest(unittest.TestCase):
    def test_checklist_is_in_sync_with_the_spine(self):
        result = subprocess.run([sys.executable, str(RENDER)],
                                capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(CHECKLIST.read_text(encoding="utf-8"), result.stdout)

    def test_checklist_splits_mechanical_from_manual(self):
        text = CHECKLIST.read_text(encoding="utf-8")
        self.assertIn("已被机械判据覆盖", text)
        self.assertIn("仍需人读", text)

    def test_every_checklist_item_appears_once(self):
        text = CHECKLIST.read_text(encoding="utf-8")
        numbers = sorted(int(n) for n in re.findall(r"^\|\s*(\d+)\s*\|", text,
                                                    re.MULTILINE))
        self.assertEqual(list(range(1, 15)), numbers)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest skill/repo-task-doc-write/tests/test_scaffold_and_views.py -q`
Expected: FAIL，脚本不存在

- [ ] **Step 3: 实现 `make_taskdoc.py`**

```python
#!/usr/bin/env python3
"""渲染一次性脚手架：章节、表头、§8 固定内容。

这一步过后 md 就是唯一真相，不再重新渲染。脚手架只解决「结构错误」
这一类最廉价可预防的错，内容由 agent 按 decisions.json 填。

不覆盖已存在的文件——覆盖会把人已经填好的内容抹掉。
"""

import argparse
import json
import re
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = SKILL_ROOT / "references" / "task-doc-template.md"
SPINE = SKILL_ROOT / "references" / "taskdoc-elements.json"

PLACEHOLDER = "<!-- 待填写：{name} · 不写会怎样：{failure} -->"


def main(argv=None):
    ap = argparse.ArgumentParser(description="渲染任务书脚手架")
    ap.add_argument("--op", required=True, help="算子或接口名，如 aclnnRoll")
    ap.add_argument("--out", required=True, help="输出 md 路径")
    args = ap.parse_args(argv)

    out = Path(args.out)
    if out.exists():
        print(f"{out} 已存在，不覆盖 · md 是唯一真相，覆盖会抹掉已填内容",
              file=sys.stderr)
        return 2

    spine = json.loads(SPINE.read_text(encoding="utf-8"))
    template = TEMPLATE.read_text(encoding="utf-8")

    by_section = {}
    for element in spine["elements"].values():
        by_section.setdefault(element["section"], []).append(element)

    lines = [f"# {args.op} 任务书", ""]
    for raw in template.splitlines():
        heading = re.match(r"^(#{2,4})\s+([\d.]+)\s*(.*?)\s*$", raw)
        if not heading:
            continue
        number = heading.group(2).rstrip(".")
        lines.append(f"{heading.group(1)} {number} {heading.group(3)}".rstrip())
        lines.append("")
        if number == "8":
            body = template.split("## 8.")[-1].split("\n", 1)[-1]
            lines.append(body.rstrip())
            lines.append("")
            continue
        if number == "2.4":
            lines.append("| 参数名 | 输入／输出/属性 | 描述 | 数据类型 | "
                         "dtype类型 | 数据排布格式 | 维度(shape) | 值域范围 | "
                         "异常行为 |")
            lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
            lines.append("")
            continue
        for element in by_section.get(number, []):
            lines.append(PLACEHOLDER.format(name=element["name"],
                                            failure=element["failure"]))
        lines.append("")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"脚手架已写入 {out} · 章节 {len(spine['sections'])} 节")
    print("下一步：next_questions.py 看该问什么，填完再跑 check_taskdoc.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`PLACEHOLDER` 用 HTML 注释而不是可见文字，parser 的 `prose_lines` 会跳过注释，
所以脚手架里的提示不会被 `no_placeholder` 判据当成残留占位符。填内容时删掉注释。

- [ ] **Step 4: 实现 `render_views.py`**

```python
#!/usr/bin/env python3
"""从骨架渲染派生视图。骨架是唯一可编辑对象，视图不手工维护。

默认打印到 stdout，--write 才落盘。测试拿 stdout 与磁盘上的文件比对，
两者不等就说明有人手改了视图。
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SPINE = SKILL_ROOT / "references" / "taskdoc-elements.json"
CHECKLIST = SKILL_ROOT / "references" / "manual-checklist.md"

# xlsx 十四条的原文，来源 docs/development/taskdoc-source/origin/checklist-14.md
ITEMS = {
    1: "检查章节完整性", 2: "任务书标题", 3: "任务概述", 4: "功能要求",
    5: "算子工程模式", 6: "接口定义", 7: "参数说明", 8: "软硬件环境要求",
    9: "精度要求", 10: "性能要求", 11: "内存要求", 12: "自验要求",
    13: "验收交付件", 14: "PR申请合入",
}

# 这几条的语义部分脚本判不了：对不对要人读，不是有没有。
MANUAL = {2: "标题与任务发放纪要的任务名是否一致",
          4: "算法逻辑或计算公式是否正确",
          9: "精度阈值是否覆盖了所有输入场景",
          12: "自验策略是否真的可行",
          13: "交付件要求是否恰当"}


def render():
    spine = json.loads(SPINE.read_text(encoding="utf-8"))
    covered = defaultdict(list)
    for key, element in spine["elements"].items():
        for item in element["checklist_items"]:
            covered[item].append((key, element))

    out = ["# 任务书人工检查清单", "",
           "本文件由 `scripts/render_views.py` 从 "
           "`references/taskdoc-elements.json` 渲染，不要手改。",
           "改内容请改骨架再重新渲染。", "",
           "源清单是 `任务书人工checklist.xlsx` Sheet1 的十四条，"
           "归档在 `docs/development/taskdoc-source/origin/checklist-14.md`。", "",
           "## 已被机械判据覆盖", "",
           "这几条不需要人读，`check_taskdoc.py` 会裁决。", "",
           "| 序号 | 检查项 | 由哪些判据覆盖 |", "| --- | --- | --- |"]
    for item in sorted(ITEMS):
        if item in MANUAL:
            continue
        checks = sorted({c.split(":")[0]
                         for _, element in covered.get(item, [])
                         for c in element["checks"]})
        out.append(f"| {item} | {ITEMS[item]} | {'、'.join(checks) or '—'} |")

    out += ["", "## 仍需人读", "",
            "这几条判的是「对不对」而不是「有没有」，脚本判不了。"
            "交付前逐条核对。", "",
            "| 序号 | 检查项 | 人要判断什么 |", "| --- | --- | --- |"]
    for item in sorted(MANUAL):
        out.append(f"| {item} | {ITEMS[item]} | {MANUAL[item]} |")
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="从骨架渲染派生视图")
    ap.add_argument("--write", action="store_true", help="落盘而不是打印")
    args = ap.parse_args(argv)
    text = render()
    if args.write:
        CHECKLIST.write_text(text, encoding="utf-8")
        print(f"已写入 {CHECKLIST}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: 生成视图并跑测试**

```bash
python3 skill/repo-task-doc-write/scripts/render_views.py --write
python3 -m pytest skill/repo-task-doc-write/tests/ -q
```

Expected: 全绿

- [ ] **Step 6: 提交**

```bash
git add skill/repo-task-doc-write
git commit -m "feat: 脚手架渲染与派生视图

make_taskdoc.py 一次性渲染章节、表头与 §8 固定内容，此后 md 是唯一
真相不再重新渲染。它只解决结构错误这一类最廉价可预防的错，让 agent
的注意力全放在内容上。不覆盖已有文件——覆盖会抹掉人填好的内容。

脚手架的待填提示写成 HTML 注释，parser 会跳过，不会被 no_placeholder
误判成残留占位符。

render_views.py 从骨架渲染人工检查清单。十四条里九条已被机械判据覆盖
不需要人读，只剩五条判「对不对」的仍需人读。测试拿 stdout 与磁盘文件
比对，有人手改视图就会红。"
```

---

## Task 13: SKILL.md、CLAUDE.md 与收尾门禁

**Files:**
- Create: `skill/repo-task-doc-write/SKILL.md`
- Create: `skill/repo-task-doc-write/CLAUDE.md`
- Create: `skill/repo-task-doc-write/tests/test_shared_facts_sync.py`
- Create: `skill/repo-task-doc-write/tests/test_document_style.py`
- Modify: `CLAUDE.md`（仓根）
- Modify: `docs/development/skill-development-principles.md`

**Interfaces:**
- Consumes: 前面全部任务
- Produces: skill 入口与开发规则；两条收尾门禁

- [ ] **Step 1: 写 SKILL.md**

```markdown
---
name: repo-task-doc-write
description: 当需要为社区算子开发任务撰写任务书、检查已有任务书是否合规、或把需求方的口头要求整理成可验收的开发约束时使用。
---

# 社区算子任务书撰写

## 工作边界

任务书是两份契约合成的一份文档：

- 对**外部开发者**，它划定开发范围、实现约束、交付件、自验方法与提交位置
- 对**验收 agent**，它是全部验收约束的唯一来源

一条信息可能只服务其中一个受众，比如 §6 参考资料只给开发者看。
但两个受众都不满足的内容不该出现在任务书里。

**开发约束由需求方提供，不由 agent 决定。** agent 可以基于对标接口给出候选
（这个算子大概支持哪些 dtype、attr 该长什么样、失败场景怎么定义），
但每一条都要人判断是准了、缺了还是多了，答复原话记进 `decisions.json`。

任务书撰写发生在算子开发之前，**不依赖真机或 torch**。agent 给出的候选只能
来自模型知识或公开文档，没有一条能在本机实证——所以推断项的证据强度天然弱，
必须人拍板，这不是流程负担而是唯一可靠的把关点。

## 五个阶段

| 阶段 | 做什么 | 出口 |
| --- | --- | --- |
| T1 受理 | 收算子名与已有材料，判定工程模式、对标基线、是否随机算子 | 前提三项已拍板 |
| T2 推断 | 基于对标接口给出候选：签名、dtype 面、值域、错误场景、实现约束 | 候选成文，标注 provenance |
| T3 拍板 | 按批次追问，答复原话入 `decisions.json` | `next_questions.py` 报 done |
| T4 落稿 | 渲染脚手架，按 decisions 填内容 | md 全部章节非空 |
| T5 质量门 | `check_taskdoc.py` 跑到退出码 0 | 封条 sha256 匹配交付的 md |

## 命令

```bash
# T3：看下一批该问什么
python3 scripts/next_questions.py --decisions evidence/decisions.json --doc <md>

# T4：一次性渲染脚手架
python3 scripts/make_taskdoc.py --op <算子名> --out <工作目录>/<Op>_task_doc.md

# T5：质量门，退出码 0 才算过
python3 scripts/check_taskdoc.py --doc <md> \
    --decisions evidence/decisions.json --evidence-dir evidence/
```

## 交付硬要求

交付前必须贴出 `check_taskdoc.py` 的完整输出，含退出码。

`evidence/gate_pass.json` 的 `sha256` 必须等于交付的 md 的 sha256。
跑完门禁又改了稿，这两个值就对不上——**没有匹配的封条就是没过门**。

## 写作参考

`references/golden-task-doc.md` 是一份过了全部五层门禁的任务书。
写不确定某一节该写到什么程度时看它，不要看别处的任务书——
别处的没有经过本门禁。

`references/task-doc-template.md` 是章节骨架与每一列的填写判据。

## 判据在哪

模糊词表与替代写法在 `references/vague-words.json`，
枚举列的取值域在 `references/dtype-vocab.json`。

被门禁拦下时先读报错——每条报错都带「不写会怎样」和替代写法，
不要去改判据让它通过。

`references/manual-checklist.md` 是十四条人工检查项里仍需人读的那五条，
交付前逐条核对。
```

- [ ] **Step 2: 写 skill 侧 CLAUDE.md**

```markdown
# CLAUDE.md — 社区算子任务书撰写 Skill

改这个 skill 时读。运行时规则在 `SKILL.md`，这里是开发规则。

## 复用仓根规范

行文、脚本命名与退出码约定、TDD 流程、遇到问题的查找顺序，全部沿用仓根
`CLAUDE.md`，不在这里复制。复制一遍等于同一条规则在上下文里出现两次。

复用的是**怎么写**（行文与实现规范），不是**写什么**（内容约束）。
后者每个 skill 自己推导。

## 四条红线

验收 skill 的红线 1「不拿算子实现当验收依据」在撰写阶段不成立——
写任务书时算子还不存在，而对标实现恰恰是必须读的合法依据。照搬会把
skill 写死。撰写侧重新推导的四条：

| 红线 | 内容 | 破了会怎样 |
| --- | --- | --- |
| A | agent 推断不是事实，全部推断项必须人拍板并留答复原话 | 任务书写着一堆没人负责的约束，开发者照做、验收照测，错到最后才发现 |
| B | 社区模板章节号只做加法，不重编号 | 存量任务书与交叉引用全断 |
| C | 门禁不可绕过，`gate_pass.json` 用 sha256 封住当前 md | agent 声称跑过门禁，或跑完又改稿 |
| D | 黄金任务书必须真的返回 0 | 门禁靠「什么都判红」通过全部正向测试 |

## 骨架是唯一可编辑对象

`references/taskdoc-elements.json` 是唯一骨架。加判据、改必填性、
调批次，全部改骨架，不要在脚本里写死。

`manual-checklist.md` 从骨架渲染，改它不生效，下次 `render_views.py`
就覆盖回去。

## 判据要能追溯

每条判据必须能指回 `docs/development/taskdoc-source/defect-map.md` 里的
一条真实缺陷。指不回去的判据是凭空设计的——要么删掉，要么在对照表里
补上它防的是什么。

这是防止判据无限膨胀的唯一闸门。

## 素材只读

`docs/development/taskdoc-source/origin/` 是上游拷贝，只读。发现缺陷记进
`defect-map.md`，修复产物另存 `references/`。上游改版时能逐条重放。

`tests/fixtures/real_sample.md` 是带缺陷的对照组，不修——修了就失去对照价值。

## 改黄金样例还是改判据

`test_gate_passes_golden` 红的时候，默认改**黄金样例**。

黄金样例是「什么叫合格」的定义，判据是它的形式化。判据比样例严，说明样例
没写到位；判据比样例宽，说明判据漏了。两种情况都是先想清楚合格该长什么样，
再动判据。
```

- [ ] **Step 3: 写两条收尾门禁**

```python
# skill/repo-task-doc-write/tests/test_shared_facts_sync.py
"""共享事实的两份副本必须一致。

skill 部署到别的机器上就是一个普通目录，必须自包含，所以两个 skill
各留一份副本而不是软链。副本会漂，用 sha256 拓红。
"""

import hashlib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MINE = REPO_ROOT / "skill" / "repo-task-doc-write" / "references"
THEIRS = REPO_ROOT / "skill" / "repo-task-atk-test" / "references"

SHARED = ("experimental_standard.md", "random-operator-signals.json")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SharedFactsSyncTest(unittest.TestCase):
    def test_shared_facts_match(self):
        drifted = []
        for name in SHARED:
            mine, theirs = MINE / name, THEIRS / name
            self.assertTrue(mine.is_file(), f"{name} 未拷贝到撰写 skill")
            if digest(mine) != digest(theirs):
                drifted.append(name)
        self.assertEqual([], drifted,
                         "共享事实漂了，把权威那份拷过去，别就地改")


if __name__ == "__main__":
    unittest.main()
```

```python
# skill/repo-task-doc-write/tests/test_document_style.py
"""行文规则。规则正文在 .claude/rules/prose-style.md。

新 skill 的基线是 0：写进来就必须合规，没有存量豁免。
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = Path(__file__).resolve().parents[1]

# 判据实现复用验收 skill 的测试模块。这是开发期依赖，不随 skill 发布——
# tests/ 本来就不参与部署，两个 skill 在同一个仓里，抄一份反而会漂。
sys.path.insert(0, str(REPO_ROOT / "skill" / "repo-task-atk-test" / "tests"))

from test_document_style import prose_lines, prose_violations  # noqa: E402

SCANNED = [SKILL_ROOT / "SKILL.md", SKILL_ROOT / "CLAUDE.md",
           *sorted((SKILL_ROOT / "references").glob("*.md"))]


class DocumentStyleTest(unittest.TestCase):
    def test_no_prose_structure_violations(self):
        failures = []
        for path in SCANNED:
            for rule, line, note in prose_violations(path):
                failures.append(f"{path.name}:{line} 规则{rule} {note}")
        self.assertEqual([], failures)

    def test_lines_stay_within_width(self):
        failures = []
        for path in SCANNED:
            for number, line in prose_lines(path):
                if len(line) > 100:
                    failures.append(f"{path.name}:{number}: {len(line)} 字符")
        self.assertEqual([], failures)

    def test_golden_task_doc_is_exempt(self):
        # 黄金样例是任务书不是 skill 文档，行文按社区模板走，不受这条约束。
        self.assertNotIn(SKILL_ROOT / "references" / "golden-task-doc.md",
                         SCANNED)


if __name__ == "__main__":
    unittest.main()
```

`golden-task-doc.md` 与 `task-doc-template.md` 都不在 `SCANNED` 里：
它们是任务书与模板，行文按社区模板走，不是 skill 文档。
用 `glob("*.md")` 会把它们扫进来，所以要显式排除。把 `SCANNED` 改成：

```python
EXEMPT = {"golden-task-doc.md", "task-doc-template.md", "manual-checklist.md"}
SCANNED = [SKILL_ROOT / "SKILL.md", SKILL_ROOT / "CLAUDE.md",
           *sorted(p for p in (SKILL_ROOT / "references").glob("*.md")
                   if p.name not in EXEMPT)]
```

- [ ] **Step 4: 改仓根 CLAUDE.md**

三处改动：

- 标题从「社区算子验收 Skill 仓」改为「社区算子 Skill 仓」
- §0 一句话加一行：`任务书撰写 Skill：skill/repo-task-doc-write/`，
  并注明它的开发规则在 `skill/repo-task-doc-write/CLAUDE.md`
- §4 文件结构的树里加 `skill/repo-task-doc-write/` 与
  `docs/development/taskdoc-source/`

- [ ] **Step 5: 把行文规则写进开发规范**

在 `docs/development/skill-development-principles.md` 追加一节：

```markdown
## 行文结构化

规则正文在 `.claude/rules/prose-style.md`，随仓根 CLAUDE.md 自动导入，
写文档时常驻上下文。

核心是三个层级各司其职：连贯论证成段、并列观点成列表、单句独占段只留给
判据和禁令。三层都用到才有视觉层级，全用单句段等于没有强调。

机械判据在两个 skill 的 `test_document_style.py`，只抓高置信子集。
验收 skill 有 102 处存量走计数基线棘轮，新 skill 基线为 0。
```

- [ ] **Step 6: 跑全仓回归**

```bash
python3 skill/repo-task-doc-write/scripts/render_views.py --write
PYTHONPATH=third_party/ATK python3 -m pytest skill/ -q 2>&1 | tail -5
```

Expected: `22 failed` 不变，passed 数上升。新 skill 的测试全绿。

若 `test_no_prose_structure_violations` 红，按报错改 `SKILL.md` 或
`CLAUDE.md` 的行文，不要往基线里加豁免——新 skill 没有存量债。

- [ ] **Step 7: 提交**

```bash
git add skill/repo-task-doc-write CLAUDE.md docs/development
git commit -m "feat: 撰写 skill 的入口、开发规则与收尾门禁

SKILL.md 写明双受众边界与五阶段流程，交付硬要求是封条 sha256 必须
等于交付的 md（红线 C）。

skill 侧 CLAUDE.md 只写重新推导的四条红线与骨架纪律，行文与实现规范
引用仓根不复制。复用的是怎么写，不是写什么——验收侧的红线 1 照搬过来
会把 skill 写死，写任务书时算子还不存在。

两条收尾门禁：共享事实 sha256 同步、新 skill 行文基线为 0。
黄金样例与模板不受行文门禁约束，它们是任务书不是 skill 文档。"
```

---

## Task 14: 基线探测（可选）

**Files:**
- Create: `skill/repo-task-doc-write/scripts/probe_baseline.py`
- Create: `skill/repo-task-doc-write/tests/test_probe_baseline.py`

**Interfaces:**
- Consumes: 无
- Produces: `probe_baseline.py --api <torch.roll> [--json]`，退出码恒为 0

排在最后是因为它**永不阻塞**：本机没有 torch，探测不到就降级，
skill 的全部功能不依赖它。

- [ ] **Step 1: 写测试（先失败）**

```python
# skill/repo-task-doc-write/tests/test_probe_baseline.py
"""可选辅助证据。无 torch 时优雅降级，永不阻塞。"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "probe_baseline.py"


def run(api):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--api", api, "--json"],
        capture_output=True, text=True)
    return result.returncode, json.loads(result.stdout or "{}")


class ProbeBaselineTest(unittest.TestCase):
    def test_exit_code_is_always_zero(self):
        for api in ("torch.roll", "nonexistent.module.func", "math.hypot"):
            with self.subTest(api=api):
                self.assertEqual(0, run(api)[0])

    def test_missing_module_degrades_to_model_knowledge(self):
        _, payload = run("definitely_not_installed.func")
        self.assertEqual("model_knowledge", payload["provenance"])
        self.assertIsNone(payload["signature"])
        self.assertIn("未安装", payload["note"])

    def test_stdlib_function_is_probed_locally(self):
        _, payload = run("math.hypot")
        self.assertEqual("local_probe", payload["provenance"])

    def test_payload_always_carries_the_same_keys(self):
        for api in ("math.hypot", "definitely_not_installed.func"):
            with self.subTest(api=api):
                _, payload = run(api)
                self.assertEqual(
                    {"api", "provenance", "signature", "parameters", "note"},
                    set(payload))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest skill/repo-task-doc-write/tests/test_probe_baseline.py -q`
Expected: FAIL，脚本不存在

- [ ] **Step 3: 实现 `probe_baseline.py`**

```python
#!/usr/bin/env python3
"""抓对标接口的签名当辅助证据。

装了 torch 的机器上能拿到真签名，没装就降级成模型知识。**永不阻塞**：
任务书撰写发生在开发之前，不该依赖任何运行时环境。

provenance 如实标注，人拍板时要知道这条候选的证据强度。
不管哪一档，推断项都必须人拍板（红线 A）——这个脚本只是让人少猜一点。
"""

import argparse
import importlib
import inspect
import json
import sys

TEMPLATE = {"api": None, "provenance": "model_knowledge",
            "signature": None, "parameters": [], "note": ""}


def probe(api):
    out = dict(TEMPLATE, api=api)
    if "." not in api:
        out["note"] = f"{api} 不是 module.func 形式，无法探测"
        return out
    module_name, _, attribute = api.rpartition(".")
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        out["note"] = f"{module_name} 未安装，候选只能来自模型知识或公开文档"
        return out
    target = getattr(module, attribute, None)
    if target is None:
        out["note"] = f"{module_name} 里没有 {attribute}"
        return out
    try:
        signature = inspect.signature(target)
    except (TypeError, ValueError):
        out["note"] = f"{api} 是内建或 C 扩展，取不到签名"
        return out
    out["provenance"] = "local_probe"
    out["signature"] = f"{attribute}{signature}"
    out["parameters"] = list(signature.parameters)
    out["note"] = "本机实测签名 · 支持的 dtype 与值域仍需人确认"
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="对标接口签名探测")
    ap.add_argument("--api", required=True, help="如 torch.roll")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    payload = probe(args.api)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"{payload['api']} · 证据强度 {payload['provenance']}")
        if payload["signature"]:
            print(f"  签名 {payload['signature']}")
        print(f"  {payload['note']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest skill/repo-task-doc-write/tests/test_probe_baseline.py -q`
Expected: `4 passed, 9 subtests passed`

`test_stdlib_function_is_probed_locally` 用 `math.hypot` 而不是 `torch.roll`，
因为本机没有 torch，而这条测试要验证「探测得到时走 local_probe」这条路。

- [ ] **Step 5: 全仓回归并提交**

```bash
PYTHONPATH=third_party/ATK python3 -m pytest skill/ -q 2>&1 | tail -3
git add skill/repo-task-doc-write
git commit -m "feat: 对标接口签名探测，无 torch 时优雅降级

装了 torch 的机器上抓真签名，没装就降级成模型知识，退出码恒为 0。
任务书撰写发生在开发之前，不该依赖任何运行时环境。

provenance 如实标注证据强度，但不管哪一档推断项都必须人拍板（红线 A）。
这个脚本只是让人少猜一点，不是替人拍板。"
```

---

## 收尾核对

全部任务完成后跑一遍：

```bash
python3 skill/repo-task-doc-write/scripts/render_views.py --write
PYTHONPATH=third_party/ATK python3 -m pytest skill/ -q 2>&1 | tail -5
```

Expected: `22 failed` 不变（既存失败见仓根 CLAUDE.md §8），新 skill 全绿。

再走一遍端到端，确认 skill 真的能用：

```bash
WORK=$(mktemp -d)/taskdoc-Roll
python3 skill/repo-task-doc-write/scripts/make_taskdoc.py \
    --op aclnnRoll --out "$WORK/Roll_task_doc.md"
python3 skill/repo-task-doc-write/scripts/next_questions.py \
    --decisions "$WORK/evidence/decisions.json" --doc "$WORK/Roll_task_doc.md"
python3 skill/repo-task-doc-write/scripts/check_taskdoc.py \
    --doc "$WORK/Roll_task_doc.md" --evidence-dir "$WORK/evidence"; echo "退出码 $?"
```

Expected:

- `make_taskdoc.py` 退出 0，写出十六节的脚手架
- `next_questions.py` 报「还缺 38 项，下一批：第 1 批 前提」
- `check_taskdoc.py` 退出 2，逐条列出空章节，**不写封条**

空脚手架被判红是对的——那正是门禁该做的事。

---

## Spec 覆盖对照

| spec 章节 | 由哪个任务实现 |
| --- | --- |
| §1.2 现状缺陷 D01-D12、T01-T05 | Task 1 `defect-map.md` |
| §2.1 Markdown 唯一真相 | Task 5 parser、Task 12 脚手架不覆盖 |
| §2.2 要素清单驱动 | Task 2 |
| §2.3 人拍板答复原话 | Task 9 L3 |
| §2.4 同仓并列与共享事实 | Task 7 拷副本、Task 13 同步门禁 |
| §2.5 四条红线 | A→Task 9、B→Task 3、C→Task 10、D→Task 6 起每层都断言 |
| §3 章节骨架 v3.0+ | Task 3 |
| §4 要素清单约 40 条 | Task 2 |
| §4.1 独立类型词表与 `(-∞,∞)` 判红 | Task 6 `dtype-vocab.json`、Task 8 `no_unbounded_range` |
| §5.1 一致性判据 | Task 7 |
| §5.2 明确性判据 | Task 8 |
| §5.3 xlsx 14 条归属 | Task 12 `render_views.py` |
| §5.4 完备性与条件必填 | Task 2 骨架、Task 6 `run_layer` 的条件跳过 |
| §6 五阶段流程 | Task 13 `SKILL.md` |
| §6.1 八个批次 | Task 11 |
| §7 五个量具 | Task 6、11、12、14 |
| §7.1 门禁分层 L0-L4 | Task 6、7、8、9 |
| §7.2 封条不可绕过 | Task 10 |
| §8.1 三组 fixture | Task 4 黄金与对照组、Task 6-8 缺陷 fixture |
| §8.2 测试清单 | 见下方偏离说明 |
| §9 落地顺序 | Task 编号即顺序 |
| §10 目录结构 | Task 1 与 Task 13 |
| §11.1 skill 侧 CLAUDE.md | Task 13 |
| §11.2 素材只读纪律 | Task 1 README、Task 13 CLAUDE.md |
| §11.3 行文结构化规则 | 已在计划外实施（commit `9fc184b`），Task 13 只加新 skill 的基线为 0 的门禁 |

### 两处与 spec §8.2 的偏离

spec 列了 `test_vague_scope.py` 与 `test_real_sample_regression.py` 两个独立测试文件，
计划把它们并进了相邻的测试：

- 模糊词作用域测试并进 `test_gate_clarity.py` 的
  `test_vague_words_are_scoped_not_global`。它和别的 L2 判据共用同一套
  fixture 与 `layer2()` 辅助函数，单独开一个文件要把这些再抄一遍。
- 真实样例回归并进 `test_gate_consistency.py` 的
  `test_real_sample_carries_the_expected_defects`。样例的缺陷主要是一致性类，
  放在一致性层的测试里，红了能直接看出是哪条判据动了。

两处都是文件归并，断言一条没少。
