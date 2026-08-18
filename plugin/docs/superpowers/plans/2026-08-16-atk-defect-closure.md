# ATK 验收 skill 缺陷收口实施计划（Plan C / 缺陷）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把设计文档 §6.B 的九条缺陷（B1–B9）逐条落到量具、骨架与模板上，让 roll 那轮真机跑测里靠「读源码 + 手工自证」补上的每一处，下一轮由 skill 直接给出。

**Architecture:** 缺陷修复全部挂在 Plan B 产出的产物契约骨架上——新产物进骨架，新决策进 `owner: agent` 字段，卡与决策点清单由脚本重渲染。判据一律从数据推导：适配器该不该写从用例集读，基线绑定从基线签名读，可表达性从轴取值读，不新增任何「agent 声明一行就放行」的通路。

**Tech Stack:** Python 3（标准库 + pytest/unittest），JSON，YAML，Markdown。无新增第三方依赖。

设计文档：`docs/superpowers/specs/2026-08-15-atk-knowledge-architecture-design.md`

**执行顺序：本计划排在 `2026-08-15-atk-contract-spine.md`（Plan B / 架构）之后。**

Plan B 已完成并逐 Task 评审通过，产出 `references/artifact-contracts.json`（骨架）、
`scripts/_contracts.py`（加载与渲染）、`scripts/render_views.py`、
`references/decision-points.md`（派生视图）、SKILL.md 五张阶段作战卡、
`tests/test_contracts.py` 四条一致性不变量。本计划直接采用这些既定事实。

Plan B 埋下的两处前瞻引用，本计划兑现：

- `SKILL.md:46` 已写 `source evidence/env.sh`，文件由 Task 2 产出
- `SKILL.md:35` 已写「接线字段的受控改写走 `rewire_adapter.py`」并附了免责说明，脚本由 Task 7 产出，免责说明同步删除

### B3 已在 Plan A 落地，本计划不重做

设计文档 §6.B 列的 B3「`freeze_inputs.py` 补基线执行核验」已经由
`a78c4b7 refactor: fold input budget into freezing, gate the baseline plugin` 实现。

现状核实过：`scripts/freeze_inputs.py` 有 `BASELINE_FAILURE` 正则与
`baseline_failures(log)`，冻结报告写 `baseline_failed_cases`，非空时退出码 2，
`references/execution.md` 的「冻结输入」小节也写明了这是三件顺带核验之一。

本计划的 Task 列表因此是 B1、B2、B4、B5、B6、B7、B8、B9 八条，外加一条骨架陈旧项清理。

### 事项到 Task 的映射

| 事项 | 级 | Task |
| --- | --- | --- |
| （骨架陈旧项清理与量具引用锁） | — | 1 |
| B1 `evidence/env.sh` + 命令模板绝对路径 + skill base dir 固化 | P0 | 2 |
| B2 基线绑定规则：YAML 输入名 == 基线形参名 | P0 | 3 |
| B3 `freeze_inputs.py` 补基线执行核验 | P0 | 已在 Plan A 落地 |
| B4 适配器 S2 判定规则 + `rewire_adapter.py` | P0 | 6、7 |
| B5 「可变长复合组」知识域 | P0 | 4 |
| B6 可表达性判定前移到 `make_must_cover.py` | P1 | 5 |
| B7 补事实（执行期报告路径、能力报告 sha、构建入口） | P1 | 9 |
| B8 物化脚本通用化 | P1 | 8 |
| B9 `probe_env.py` 补 chip / op_commit | P2 | 9 |

## Global Constraints

- 工作目录：`skill/repo-task-atk-test/`（下称 skill root）。所有相对路径以此为根。
- 测试命令：在 skill root 执行 `python3 -m pytest tests/ -q`。
  **基线：277 passed, 9 skipped, 227 subtests passed。**
  每个 Task 收尾时通过数只许增不许减；减了就是删掉了别人的断言，回滚重做。
  各 Task 写出的绝对通过数是按新增用例条数推算的**指示值**：真正的判据是
  「本 Task 新增几条就多几条，且没有既有用例转红」，差一两条先核对自己新增了几条，
  不要为了对上数字去删或补用例。
- 测试写法：`unittest.TestCase`，用 `SKILL_ROOT = Path(__file__).resolve().parents[1]` 定位，
  需要导入脚本时 `sys.path.insert(0, str(SKILL_ROOT / "scripts"))` 后按裸模块名导入。
- 文档、注释、字符串一律中文；代码标识符英文。
- 引用写成可点击路径，例如 `atk/configs/design_config.py:85`。
- **不改 `atk/` 源码。** 不写任何算子专属逻辑（无算子名、具体 shape、dtype 清单、专属阈值）。
- **证据分级**（CLAUDE.md §3.2）：写进 skill 的每条断言必须是 A 级（实跑）或 B 级（源码核实），
  并在注释里注明依据位置。拿不准写「未核实」或登记 `knowledge_gaps`，**不要编**。
- **判据从数据推导，不靠声明**（CLAUDE.md §3.1 第二检查）：任何新门禁都不许出现
  「agent 在某个 JSON 里写一行就放行」的通路。
- 文档行文受 `tests/test_document_style.py` 约束，写之前先看清楚：
  单行散文 ≤ 100 字符且最多一个「。」；reference 超过 100 行必须有 `## 目录`；
  reference 之间不许互相链接；SKILL.md ≤ 360 行。
- 改动骨架 `references/artifact-contracts.json` 之后必做两件事，否则 `tests/test_contracts.py` 会红：
  1. `python3 scripts/render_views.py --write` 重新生成 `references/decision-points.md`
  2. `python3 scripts/render_views.py --cards`（Task 1 产出）把受影响阶段的卡原样贴回 SKILL.md
- 工作区里还有约 37 个与 Plan A/B 无关的旧文件改动，**不要碰、不要提交**。
  每个 Task 只 `git add` 本 Task 明确列出的文件。
- 提交信息末尾附：`Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`

---

## File Structure

| 文件 | 责任 | Task |
| --- | --- | --- |
| `scripts/_contracts.py` | 增 `script_refs()`：骨架里指向量具的引用全集 | 1 |
| `scripts/render_views.py` | 增 `--cards`：打印五张卡供贴回 SKILL.md | 1 |
| `scripts/probe_env.py` | 增 `--env-sh` / `--vendor-env` / `--custom-opp` / `--op-repo`，产出 `evidence/env.sh` 与来源版本 | 2、9 |
| `scripts/make_yaml.py` | 增基线形参名核对；可表达性判定改为调用共享模块 | 3、5 |
| `references/atk-parameter-capabilities.json` | 增 `group_length` 事实块 | 4 |
| `scripts/atk_lookup.py` | 增 `group_length` 主题 | 4 |
| `scripts/_expressibility.py` | 可表达性判定的唯一实现，decl 期与物化期共用 | 5 |
| `scripts/make_must_cover.py` | 在 decl 期就跑可表达性判定 | 5 |
| `scripts/check_adapter_binding.py` | S2 出口门禁：适配器该不该写、写没写 | 6 |
| `scripts/rewire_adapter.py` | 接线字段受控改写的唯一出口 | 7 |
| `scripts/_shapes.py` | `size_class × rank → shape`，与算子无关 | 8 |
| `scripts/make_repro.py` | 环境指纹优先读 `fingerprint` 块 | 9 |
| `assets/example/constraint.py` | 增可变长复合组的整组重建示范 | 4 |
| `assets/example/materialize.py` | 改用 `_shapes.py` | 8 |
| `references/artifact-contracts.json` | 陈旧项清理 + 三样新产物登记 | 1、2、6 |
| `SKILL.md` | 卡随骨架重渲染；删 `rewire_adapter.py` 的免责说明 | 1、2、6、7 |
| `references/execution.md` | `evidence/env.sh` 的产出与使用 | 2 |
| `references/case-design.md` | 基线形参名规则；物化通用件 | 3、8 |
| `references/plugin-authoring.md` | 可变长复合组；适配器 S2 判定表；`rewire_adapter.py` | 4、6、7 |
| `references/atk-cli.md` | 生成期与执行期产物路径分列 | 9 |
| `references/build-deploy.md` | 构建入口可能在上级仓；S3 重生成 `env.sh` | 2、9 |

---

## Task 1: 骨架陈旧项清理与量具引用锁

Plan A 删掉了 `make_manifest.py`、`check_atk_capabilities.py`、`check_adapter_repair.py`、
`check_input_budget.py` 四个脚本，Plan B 的骨架把其中两个的名字抄了进去，四条不变量
一条也没红：L4 只解析 `spec` 锚点，`test_declared_producer_exists` 只看 `producer`，
`consumed_by` 没有任何东西看着。于是 SKILL.md 的 S3 卡上「出口门禁：… / manifest」
挂了整整一轮——那道门禁的脚本早就不存在了。

**先清理再展开**：后面每个 Task 都要往骨架里加产物、加门禁名，站在错的基线上加只会把错误摊薄。

**Files:**
- Modify: `skill/repo-task-atk-test/scripts/_contracts.py`
- Modify: `skill/repo-task-atk-test/scripts/render_views.py`
- Modify: `skill/repo-task-atk-test/references/artifact-contracts.json`
- Modify: `skill/repo-task-atk-test/SKILL.md`
- Test: `skill/repo-task-atk-test/tests/test_contracts.py`

**Interfaces:**
- Consumes：`_contracts.load()`、`_contracts.render_card(data, stage)`（Plan B 已有）
- Produces：
  - `_contracts.script_refs(data) -> list[tuple[str, str, str]]` — `(产物名, 字段名, 量具文件名)`，
    字段名取 `"producer"` 或 `"consumed_by"`
  - `render_views.py --cards` — 按 S1→S5 顺序打印五张卡，退出码 0

- [ ] **Step 1: 写失败测试**

在 `tests/test_contracts.py` 的 `SpecAnchorTest` 类里追加两条：

```python
    def test_every_referenced_script_exists(self):
        # Plan A 删掉四个脚本后，骨架里 make_manifest.py / check_atk_capabilities.py
        # 的引用留在 consumed_by 里，L4 只锁 spec 锚点、test_declared_producer_exists
        # 只锁 producer，两条都看不到 consumed_by——于是 S3 卡上「出口门禁：…/manifest」
        # 指着一个不存在的脚本挂了一轮。这条把量具引用的两个位置一起锁住。
        for artifact, field, script in _contracts.script_refs(self.data):
            with self.subTest(artifact=artifact, field=field, script=script):
                self.assertTrue((SKILL_ROOT / "scripts" / script).exists(),
                                f"{artifact}.{field} 指向不存在的量具 {script}")

    def test_script_refs_covers_both_producer_and_consumers(self):
        # 只收 producer 的话这条锁等于 test_declared_producer_exists 的复读，
        # 上面那条真正要覆盖的是 consumed_by 那一侧。
        fields = {field for _, field, _ in _contracts.script_refs(self.data)}
        self.assertEqual({"producer", "consumed_by"}, fields)
```

在 `tests/test_contracts.py` 末尾（`RenderViewsCliTest` 类内）追加：

```python
    def test_cards_subcommand_prints_every_stage(self):
        # 骨架一改，SKILL.md 的卡就要重贴。没有这条命令时维护者只能自己
        # 拼 python -c 调 render_card，五张卡贴错一张 L2 才会红，来回一轮。
        result = subprocess.run(
            [sys.executable, str(RENDER_VIEWS), "--cards"],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        for stage in STAGES:
            self.assertIn(f"### {stage} ", result.stdout)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_contracts.py -q`
Expected: FAIL。三条新用例全红：
`AttributeError: module '_contracts' has no attribute 'script_refs'`（前两条），
`--cards` 那条报 `argparse` 拒绝未知参数、returncode 为 2。

- [ ] **Step 3: 加 `script_refs`**

在 `scripts/_contracts.py` 的 `agent_fields` 之后插入：

```python
def script_refs(data):
    """骨架里指向量具的引用全集：producer 一处，consumed_by 里以 .py 结尾的若干。

    产物被谁消费，是骨架回答的四个问题之一。但消费者写成脚本名时，
    它同时也是一条会过期的事实——脚本删了、改名了，骨架不会知道。
    Plan A 删掉四个脚本后，骨架里两个名字留了下来，四条不变量一条也没红。

    返回 `[(产物名, 字段名, 量具文件名), ...]`，字段名是 "producer" 或 "consumed_by"。
    """
    refs = []
    for name, spec in data["artifacts"].items():
        if spec.get("producer"):
            refs.append((name, "producer", spec["producer"]))
        for consumer in spec.get("consumed_by") or []:
            if str(consumer).endswith(".py"):
                refs.append((name, "consumed_by", str(consumer)))
    return refs
```

- [ ] **Step 4: 加 `--cards`**

改写 `scripts/render_views.py` 的 `main()`（导入行同步加 `render_card`）：

```python
from _contracts import ContractError, load, render_card, render_decision_points
```

```python
def main():
    parser = argparse.ArgumentParser(description="渲染骨架的派生视图")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="核对是否与骨架一致")
    group.add_argument("--write", action="store_true", help="重新生成")
    group.add_argument("--cards", action="store_true",
                       help="按阶段顺序打印五张作战卡，原样贴回 SKILL.md")
    args = parser.parse_args()

    try:
        data = load()
    except ContractError as exc:
        print(f"骨架不可用：{exc}", file=sys.stderr)
        return 2

    if args.cards:
        # 卡在 SKILL.md 里是手工维护的正文，锁 L2 只告诉你「不一致」，
        # 不告诉你「该长什么样」。把渲染结果直接打出来，贴回去就完了。
        for stage in data["stages"]:
            print(render_card(data, stage))
        return 0

    rendered = render_decision_points(data)

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
```

- [ ] **Step 5: 跑测试确认 `--cards` 通过、量具引用锁转红**

Run: `python3 -m pytest tests/test_contracts.py -q`
Expected: `test_cards_subcommand_prints_every_stage` 与
`test_script_refs_covers_both_producer_and_consumers` 通过；
`test_every_referenced_script_exists` 仍红，subTest 报出
`<op>.yaml.consumed_by 指向不存在的量具 check_atk_capabilities.py` 与 `make_manifest.py`。

这是预期的：锁先立起来，再让它抓到真问题。

- [ ] **Step 6: 清理骨架的陈旧项**

改 `references/artifact-contracts.json` 三处。

S2 的门禁（Plan A 删掉了能力门禁与预算门禁，预算已并进冻结）：

```json
      "gates": ["签名", "结构", "覆盖"],
```

S3 的门禁（`make_manifest.py` 已删）：

```json
      "gates": ["构建", "安装", "SoC 绑定", "op_api 绑定", "冒烟"],
```

`<op>.yaml` 的消费者：

```json
      "consumed_by": ["atk case"],
```

- [ ] **Step 7: 重渲染派生视图与卡**

```bash
python3 scripts/render_views.py --write
python3 scripts/render_views.py --cards
```

把 `--cards` 打印出的 S2、S3 两节，原样替换 `SKILL.md` 的
`### S2 用例生成` 与 `### S3 编译安装部署` 两节（从 `### ` 行到该节最后一行「本阶段不做：…」）。

同时改 `SKILL.md` 阶段流程表里 S3 一行的出口门禁描述，去掉已不存在的 manifest：

```markdown
| S3 编译安装部署 | 构建、安装、SoC/op_api/ABI 绑定和冒烟全过 | 绑定报告、冒烟日志 | [build-deploy.md](references/build-deploy.md)、[execution.md](references/execution.md) |
```

（S2 一行原文已是「签名、结构、覆盖三道校验全过」，与清理后的骨架一致，不用改。）

- [ ] **Step 8: 跑全量测试**

Run: `python3 -m pytest tests/ -q`
Expected: PASS，`280 passed, 9 skipped`（本 Task 新增 3 条），subtests 数增加。

- [ ] **Step 9: 提交**

```bash
git add skill/repo-task-atk-test/scripts/_contracts.py \
        skill/repo-task-atk-test/scripts/render_views.py \
        skill/repo-task-atk-test/references/artifact-contracts.json \
        skill/repo-task-atk-test/references/decision-points.md \
        skill/repo-task-atk-test/SKILL.md \
        skill/repo-task-atk-test/tests/test_contracts.py
git commit -m "fix: lock gauge references in the spine and clear the stale gates

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 2: B1 —— `evidence/env.sh` 与 skill 基址固化

真机实测：每个 Bash 都是新 shell，agent 手工拼环境前缀 29 次，其中 `check_opapi_binding`
因为漏了 CANN 环境连失败两次——同一个错误同一轮踩两遍。另有一次因为压缩后
用 `find` 现找 skill 目录，跑到了前一天的陈旧副本上。

成因不是 agent 记性差，是 skill 没给一个可 source 的载体。

**Files:**
- Modify: `skill/repo-task-atk-test/scripts/probe_env.py`
- Modify: `skill/repo-task-atk-test/references/artifact-contracts.json`
- Modify: `skill/repo-task-atk-test/references/decision-points.md`（由脚本重生成）
- Modify: `skill/repo-task-atk-test/SKILL.md`
- Modify: `skill/repo-task-atk-test/references/execution.md`
- Modify: `skill/repo-task-atk-test/references/build-deploy.md`
- Test: `skill/repo-task-atk-test/tests/test_env_sh.py`

**Interfaces:**
- Consumes：`probe_env.py` 现有的 `cann_info()`、`device_list()`、`inspect()` 产出结构
- Produces：
  - `probe_env.SKILL_ROOT: str` — 运行副本的 skill 根目录绝对路径
  - `probe_env.render_env_sh(env, vendor_env=None, custom_opp=None) -> str` — 可 source 的 shell 片段
  - CLI 新增 `--env-sh PATH`、`--vendor-env PATH`、`--custom-opp PATH`
  - `env.json` 新增顶层键 `skill_dir`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_env_sh.py`：

```python
"""`evidence/env.sh`：每个 Bash 都是新 shell，环境要有载体。

真机实测（roll，2026-08-15）：手工拼环境前缀 29 次，其中两次漏掉 CANN 环境，
同一个错误同一轮踩两遍；另有一次用 find 现找 skill 目录，跑到了陈旧副本上。

这些用例锁的是「载体里该有什么、顺序对不对」，不是「probe_env 探得准不准」。
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import probe_env  # noqa: E402

PROBE = SKILL_ROOT / "scripts" / "probe_env.py"

SAMPLE = {
    "selected_python": "/home/u/conda/bin/python3",
    "atk_cli": "/home/u/conda/bin/atk",
    "cann": {"set_env": ["/usr/local/Ascend/ascend-toolkit/set_env.sh"],
             "sourced": True, "version": "8.0"},
    "devices": {"selected": 3, "selected_name": "Ascend910B4"},
}


class RenderEnvShTest(unittest.TestCase):
    def test_skill_root_points_at_this_skill(self):
        # 基址必须是「正在跑的这一份」，不是靠 find 找出来的某一份。
        self.assertTrue((Path(probe_env.SKILL_ROOT) / "SKILL.md").exists())

    def test_carries_every_prefix_the_commands_need(self):
        text = probe_env.render_env_sh(SAMPLE)
        self.assertIn("export ATK_SKILL_DIR=", text)
        self.assertIn("export ATK_PYTHON=", text)
        self.assertIn("export ATK_CLI=", text)
        self.assertIn("export ATK_DEVICE=3", text)
        self.assertIn("/usr/local/Ascend/ascend-toolkit/set_env.sh", text)

    def test_cann_is_sourced_before_vendor(self):
        # vendor 的 set_env.bash 依赖 CANN 已经加载，顺序反了就白 source。
        text = probe_env.render_env_sh(
            SAMPLE, vendor_env="/opt/pkg/vendors/x/bin/set_env.bash")
        cann = text.index("ascend-toolkit/set_env.sh")
        vendor = text.index("vendors/x/bin/set_env.bash")
        self.assertLess(cann, vendor)

    def test_vendor_and_custom_opp_are_absent_until_s3(self):
        # S1 还没装包，写进去就是假的；S3 装完重生成才有。
        text = probe_env.render_env_sh(SAMPLE)
        self.assertNotIn("set_env.bash", text)
        self.assertNotIn("ATK_CUSTOM_OPP_PATH", text)

    def test_custom_opp_is_absolute(self):
        text = probe_env.render_env_sh(SAMPLE, custom_opp="rel/libcust_opapi.so")
        line = [row for row in text.splitlines()
                if "ATK_CUSTOM_OPP_PATH" in row][0]
        self.assertIn(os.path.abspath("rel/libcust_opapi.so"), line)

    def test_missing_facts_do_not_emit_empty_exports(self):
        # 探不到就不写这一行，写成 export ATK_CLI= 会让后面的命令拼出空串，
        # 报错落在 ATK 身上，方向全错。
        text = probe_env.render_env_sh({"cann": {}, "devices": {}})
        self.assertNotIn("export ATK_CLI=", text)
        self.assertNotIn("export ATK_DEVICE=", text)


class EnvShCliTest(unittest.TestCase):
    def test_vendor_env_without_env_sh_is_rejected(self):
        # --vendor-env 只有写进 env.sh 才有意义，单独给等于什么也没做。
        result = subprocess.run(
            [sys.executable, str(PROBE), "--vendor-env", "/tmp/x"],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(2, result.returncode)
        self.assertIn("--env-sh", result.stderr)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_env_sh.py -q`
Expected: FAIL，`AttributeError: module 'probe_env' has no attribute 'SKILL_ROOT'`。

- [ ] **Step 3: 实现 `render_env_sh` 与三个新参数**

在 `scripts/probe_env.py` 的 `import` 区补 `shlex`，并在 `CANN_HINT` 之后插入：

```python
# 运行副本的 skill 根目录。agent 压缩上下文后靠 find 现找，真机上跑到过
# 前一天的陈旧副本；基址是文件系统上的事实，由正在跑的这份脚本自报最准。
SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def render_env_sh(env, vendor_env=None, custom_opp=None):
    """把环境事实写成一个可 source 的文件。

    每个 Bash 都是新 shell，而 ATK 命令要 CANN 环境、要 vendor 环境、
    要绝对解释器路径。没有载体时 agent 每条命令手工拼一次前缀——
    真机实测一轮拼了 29 次，其中两次漏掉 CANN 环境，同一个错误踩了两遍。

    顺序不能改：CANN 的 set_env.sh 必须在 vendor 的 set_env.bash 之前，
    后者依赖前者设好的 OPP 根目录。

    只写探到的事实。探不到就整行不写——`export ATK_CLI=` 这种空导出会让
    后面的命令拼出空串，报错落在 ATK 身上，排查方向全错。
    """
    lines = ["#!/bin/sh",
             "# 本文件由 scripts/probe_env.py 生成，不要手改。",
             "# 用法：source evidence/env.sh && cd <绝对路径> && <命令>",
             "# S3 装完本轮包后要带 --vendor-env / --custom-opp 重新生成一次。",
             ""]
    lines.append(f"export ATK_SKILL_DIR={shlex.quote(SKILL_ROOT)}")

    scripts = (env.get("cann") or {}).get("set_env") or []
    if scripts:
        lines.append(f". {shlex.quote(scripts[0])}")
    if vendor_env:
        lines.append(f". {shlex.quote(os.path.abspath(vendor_env))}")

    python = env.get("selected_python")
    if python:
        lines.append(f"export ATK_PYTHON={shlex.quote(python)}")
    cli = env.get("atk_cli")
    if cli:
        lines.append(f"export ATK_CLI={shlex.quote(cli)}")
    device = (env.get("devices") or {}).get("selected")
    if device is not None:
        lines.append(f"export ATK_DEVICE={device}")
    if custom_opp:
        lines.append(
            f"export ATK_CUSTOM_OPP_PATH={shlex.quote(os.path.abspath(custom_opp))}")
    return "\n".join(lines) + "\n"
```

在 `main()` 的 `parser.add_argument("-o", "--output")` 之后补三个参数：

```python
    parser.add_argument("--env-sh",
                        help="把环境写成可 source 的 shell 文件，如 evidence/env.sh")
    parser.add_argument("--vendor-env",
                        help="S3 安装后本轮 vendor 的 bin/set_env.bash，写进 env.sh")
    parser.add_argument("--custom-opp",
                        help="本轮候选 libcust_opapi.so 的绝对路径，写进 env.sh")
```

在 `if args.device is not None and args.device < 0:` 那段校验之后补：

```python
    if (args.vendor_env or args.custom_opp) and not args.env_sh:
        parser.error("--vendor-env / --custom-opp 只有写进 env.sh 才有意义，"
                     "同时给出 --env-sh")
```

在组装 `env` 字典时补一个键（放在 `"devices": devices,` 之后）：

```python
        "skill_dir": SKILL_ROOT,
```

在 `main()` 里写完 `args.output` 之后、`print(text)` 之前插入：

```python
    if args.env_sh:
        parent = os.path.dirname(os.path.abspath(args.env_sh))
        os.makedirs(parent, exist_ok=True)
        with open(args.env_sh, "w", encoding="utf-8") as handle:
            handle.write(render_env_sh(env, args.vendor_env, args.custom_opp))
```

在 `main()` 末尾（最后一个 `print(..., file=sys.stderr)` 之后）补提示：

```python
    if args.env_sh:
        print(f"  环境载体：{os.path.abspath(args.env_sh)}", file=sys.stderr)
        print(f"  此后每条命令以 `source {args.env_sh}` 开头，"
              "不要再手工拼环境前缀。", file=sys.stderr)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_env_sh.py -q`
Expected: PASS，7 passed。

- [ ] **Step 5: 登记进骨架**

在 `references/artifact-contracts.json` 的 `"evidence/env.json"` 条目之后插入：

```json
    "evidence/env.sh": {
      "stage": "S1",
      "owner": "script",
      "spec": "references/execution.md#环境",
      "template": null,
      "producer": "probe_env.py",
      "consumed_by": ["所有 ATK 命令的前缀"],
      "risk": "S3 装完包不带 --vendor-env 重新生成，vendor 环境与候选库路径就不在里面",
      "fields_not_applicable": "可 source 的 shell 片段，内容由 probe_env.py 自身担保"
    },
```

- [ ] **Step 6: 重渲染派生视图与 S1 卡**

```bash
python3 scripts/render_views.py --write
python3 scripts/render_views.py --cards
```

把 S1 那一节原样替换回 `SKILL.md` 的 `### S1 任务书解读`。

- [ ] **Step 7: 写进 reference**

`references/execution.md` 的「## 环境」小节，把命令块改成：

```bash
<python> scripts/probe_env.py [--device N] -o evidence/env.json --env-sh evidence/env.sh
```

并在 `使用 selected_python 和 atk_cli，不要使用裸 atk。` 之后插入：

```markdown
`--env-sh` 产出的 `evidence/env.sh` 是此后每条命令的前缀载体：

```bash
source evidence/env.sh && cd <绝对工作目录> && "$ATK_PYTHON" scripts/<脚本>.py ...
```

它固化 CANN 环境、`ATK_PYTHON`、`ATK_CLI`、`ATK_DEVICE` 和 `ATK_SKILL_DIR`。

每个 Bash 都是新 shell，不 source 就等于没加载环境。

`ATK_SKILL_DIR` 是量具目录的绝对路径，不要用 `find` 现找。
```

`references/build-deploy.md` 的「## 安装」小节，在
`安装后加载本轮 vendor 的 bin/set_env.bash。` 之后插入：

```markdown
加载之后重新生成一次环境载体，把 vendor 环境和候选库路径并进去：

```bash
<python> scripts/probe_env.py --device <N> -o evidence/env.json \
  --env-sh evidence/env.sh \
  --vendor-env <install-root>/vendors/<vendor>/bin/set_env.bash \
  --custom-opp <absolute-candidate-library>
```

不重新生成时 `evidence/env.sh` 停留在 S1 的内容，冒烟会加载不到本轮候选库。
```

- [ ] **Step 8: 跑全量测试**

Run: `python3 -m pytest tests/ -q`
Expected: PASS，`287 passed, 9 skipped`（本 Task 新增 7 条）。

`SKILL.md:46` 已有的 `source evidence/env.sh` 这句话，到这一步才真正兑现。

- [ ] **Step 9: 提交**

```bash
git add skill/repo-task-atk-test/scripts/probe_env.py \
        skill/repo-task-atk-test/references/artifact-contracts.json \
        skill/repo-task-atk-test/references/decision-points.md \
        skill/repo-task-atk-test/references/execution.md \
        skill/repo-task-atk-test/references/build-deploy.md \
        skill/repo-task-atk-test/SKILL.md \
        skill/repo-task-atk-test/tests/test_env_sh.py
git commit -m "feat: give every command an environment carrier

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 3: B2 —— YAML 输入名必须等于基线形参名

真机实测：agent 把输入命名成 `x`（基线形参是 `input`），kwargs 绑定必然 TypeError。
它改用 `input_data.args`——而带 name 的输入在 dataset reload 之后一律进 kwargs，
args 恒为空（L0 `binding.inputs`）。代价：读 ATK 源码 11 次，改插件 3 次，
一次全量跑测 42/81 失败。

skill 里两条相关事实都在（L0 的 binding 规则、模板注释里的「形参名必须一致」），
但没有任何一处在**命名输入的那一刻**说这句话。所以这条不能只补文档，要补门禁。

**判据来自数据**：基线形参名不问 agent，`align_signatures.baseline_signature()`
直接从基线符号反射得到——它已经是 `align_signatures.py` 在用的那条路径。

**Files:**
- Modify: `skill/repo-task-atk-test/scripts/make_yaml.py`
- Modify: `skill/repo-task-atk-test/references/artifact-contracts.json`
- Modify: `skill/repo-task-atk-test/references/decision-points.md`（由脚本重生成）
- Modify: `skill/repo-task-atk-test/references/case-design.md`
- Test: `skill/repo-task-atk-test/tests/test_baseline_binding.py`

**Interfaces:**
- Consumes：`align_signatures.baseline_signature(api) -> dict`，其 `parameters` 为
  `[{"name": str, "kind": str, "required": bool}, ...]` 或 `None`
- Produces：
  - `make_yaml.baseline_parameter_names(symbol) -> list[str] | None`
  - `make_yaml.check_baseline_binding(names, input_names) -> list[str]`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_baseline_binding.py`：

```python
"""YAML 输入名必须等于基线函数形参名。

真机实测（roll，2026-08-15）：输入命名成 x 而基线形参是 input，
带 name 的输入在 dataset reload 之后全部进 kwargs（L0 binding.inputs），
基线调用直接 TypeError。全量 42/81 失败，根因在 S2 的一次命名。

判据不问 agent：基线形参名从基线符号反射得到。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import make_yaml  # noqa: E402

EXAMPLE = SKILL_ROOT / "assets" / "example"
SCRIPTS = SKILL_ROOT / "scripts"


class CheckBaselineBindingTest(unittest.TestCase):
    def test_matching_names_pass(self):
        self.assertEqual(
            [], make_yaml.check_baseline_binding(
                ["input", "dim", "keepdim"], ["input", "dim", "keepdim"]))

    def test_renamed_input_is_rejected(self):
        problems = make_yaml.check_baseline_binding(
            ["input", "dim"], ["x", "dim"])
        self.assertEqual(1, len(problems))
        self.assertIn("'x'", problems[0])
        self.assertIn("kwargs", problems[0])

    def test_order_does_not_matter_only_membership(self):
        # 顺序由契约声明的语义顺序管，这条门禁只管名字对不对得上。
        self.assertEqual(
            [], make_yaml.check_baseline_binding(
                ["input", "dim"], ["dim", "input"]))

    def test_unknown_baseline_names_are_not_silently_passed(self):
        # 取不到形参名不等于「不需要核对」。静默放行就是把 roll 那轮的
        # 失败模式原样留着，只是换了个借口。
        problems = make_yaml.check_baseline_binding(None, ["x"])
        self.assertEqual(1, len(problems))
        self.assertIn("取不到", problems[0])


class DesignChainStillPassesTest(unittest.TestCase):
    """样例链路带上这道门禁之后仍然要能跑通。"""

    def test_example_input_names_match_the_baseline(self):
        decl = json.loads((EXAMPLE / "decl.json").read_text(encoding="utf-8"))
        names = list(decl["parameters"])
        baseline = make_yaml.baseline_parameter_names(decl["yaml"]["name"])
        if baseline is None:
            self.skipTest("本机没有 torch，取不到基线形参名")
        self.assertEqual([], make_yaml.check_baseline_binding(baseline, names))

    def test_renamed_contract_fails_make_yaml(self):
        decl = json.loads((EXAMPLE / "decl.json").read_text(encoding="utf-8"))
        if make_yaml.baseline_parameter_names(decl["yaml"]["name"]) is None:
            self.skipTest("本机没有 torch，取不到基线形参名")
        decl["parameters"]["not_a_baseline_param"] = \
            decl["parameters"].pop("input")
        decl["extract"]["dtype"] = {"from": "input_dtype", "index": 0}
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            broken = tmp / "decl.json"
            broken.write_text(json.dumps(decl, ensure_ascii=False),
                              encoding="utf-8")
            must_cover = tmp / "must_cover.json"
            materialized = tmp / "materialized.json"
            design = tmp / "out.yaml"
            for command in (
                    [SCRIPTS / "make_must_cover.py", "-d", broken,
                     "-o", must_cover],
                    [EXAMPLE / "materialize.py", must_cover, materialized]):
                step = subprocess.run([sys.executable, *map(str, command)],
                                      capture_output=True, text=True)
                self.assertEqual(0, step.returncode, step.stderr)
            step = subprocess.run(
                [sys.executable, str(SCRIPTS / "make_yaml.py"),
                 "-m", str(materialized), "-o", str(design)],
                capture_output=True, text=True)
            self.assertEqual(2, step.returncode, step.stdout)
            self.assertIn("not_a_baseline_param", step.stderr)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_baseline_binding.py -q`
Expected: FAIL，`AttributeError: module 'make_yaml' has no attribute 'check_baseline_binding'`。

- [ ] **Step 3: 实现两个函数**

在 `scripts/make_yaml.py` 的 `_check_semantic_axes` 之前插入：

```python
def baseline_parameter_names(symbol):
    """从基线符号反射形参名，取不到返回 None。

    走的是 `align_signatures.baseline_signature` 那条路径，两处同源。
    torch 装不上或符号解析不了都返回 None——「取不到」是一种结论，
    与「核对通过」必须分开，见 check_baseline_binding。
    """
    try:
        import torch  # noqa: F401
    except Exception:
        return None
    try:
        from align_signatures import baseline_signature
        parameters = baseline_signature(symbol).get("parameters")
    # _runtime_guard 把导入失败翻成 SystemExit(4)，那不是 Exception 的子类。
    except (Exception, SystemExit):
        return None
    return [item["name"] for item in parameters] if parameters else None


def check_baseline_binding(names, input_names):
    """YAML 的 inputs 名必须落在基线形参名里。

    带 name 的输入在 dataset reload 之后一律进 kwargs，args 恒为空
    （L0 `binding.inputs`）。名字对不上基线形参名，基线调用直接
    TypeError，而这件事要到冻结那次跑测才炸——roll 那轮 42/81 条
    失败就是这么来的，代价是读 ATK 源码 11 次、改插件 3 次。

    aclnn 独有的参数同样会落在这里，那是对的：它们必须由基线侧适配器
    pop 掉，而 `align_signatures.py` 的 `baseline_adapter.required` 已经
    在报这件事，两处指向同一个动作。
    """
    if names is None:
        return ["取不到基线形参名，无法核对 YAML 输入名；"
                "先确认基线符号可解析，或按 signature_alignment.json 的 "
                "semantic_review 从官方文档补齐形参名"]
    allowed = set(names)
    return [f"{name!r} 不是基线的形参名（基线形参：{names}）；"
            "带 name 的输入全部进 kwargs，名字对不上基线调用直接 TypeError"
            for name in input_names if name not in allowed]
```

在 `build_design` 的收尾校验区（`if len(channels["tensor_input"]) > 1:` 之后、
`if problems:` 之前）插入：

```python
    problems.extend(check_baseline_binding(
        baseline_parameter_names(header.get("name")), names))
```

（`names` 是上面已经算好的 `channels["inputs"]` 的名字列表。）

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_baseline_binding.py tests/test_assets_example.py tests/test_make_yaml.py -q`
Expected: PASS。本机若没有 torch，`DesignChainStillPassesTest` 的两条 skip，
`CheckBaselineBindingTest` 四条必须全绿。

若 `tests/test_make_yaml.py` 里已有的用例因为「取不到基线形参名」而转红，
说明那些用例的 `yaml.name` 是虚构符号——把它们的期望改成「问题列表里含这一条」，
不要为了让测试过而把 `names is None` 改成放行。

- [ ] **Step 5: 写进骨架与 reference**

`references/artifact-contracts.json` 的 `<op>_decl.json` → `fields` → `parameters` 改成：

```json
        "parameters": {
          "owner": "agent",
          "source": "基线签名与 aclnn C 签名，由 align_signatures.py 对齐；契约的键就是 YAML 输入名，必须等于基线形参名",
          "consumer": "make_yaml.py 推导每个 YAML 输入",
          "failure": "element_kind/runtime_container 组合非法，或键名不等于基线形参名 → make_yaml.py 攒齐报错"
        },
```

`references/case-design.md` 的「## 生成必测集」小节，在
`每个 parameters 项说明 element_kind、runtime_container、nullable 和必要的 dtype。` 之后插入：

```markdown
`parameters` 的键就是 YAML 的输入名，必须等于基线函数的形参名。

带 name 的输入在 dataset reload 之后全部进 kwargs，args 恒为空。

名字对不上基线调用直接 TypeError，`make_yaml.py` 会在推导时拒绝生成。

aclnn 独有的参数同样过不了这道门，它要由基线侧适配器 pop 掉。
```

- [ ] **Step 6: 重渲染派生视图**

```bash
python3 scripts/render_views.py --write
```

S1–S5 的卡不受影响（改的是字段级契约，不是产物清单），不用重贴。

- [ ] **Step 7: 跑全量测试**

Run: `python3 -m pytest tests/ -q`
Expected: PASS，`293 passed, 9 skipped`（本 Task 新增 6 条；本机无 torch 时为 `291 passed, 11 skipped`）。

- [ ] **Step 8: 提交**

```bash
git add skill/repo-task-atk-test/scripts/make_yaml.py \
        skill/repo-task-atk-test/references/artifact-contracts.json \
        skill/repo-task-atk-test/references/decision-points.md \
        skill/repo-task-atk-test/references/case-design.md \
        skill/repo-task-atk-test/tests/test_baseline_binding.py
git commit -m "feat: reject YAML input names that the baseline cannot bind

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 4: B5 —— 「可变长复合组」知识域

三条事实全部 B 级，源码位置在下面每一条后面注明。skill 里现在只有
`group_min_length: 1` 一条，另外三条从任何地方都查不到，agent 只能读源码或猜。

| 事实 | 源码依据 |
| --- | --- |
| 组长度来自 YAML `tuple_numbers`，ATK 每条用例随机抽一个，抽完还洗牌 | `atk/case_generator/generator/parameter_types/parameter_tensors.py:54-59`、`parameter_attrs.py:52-60`、`atk/configs/design_config.py:154-183` |
| 长度与 combo 顺序没有对应关系 | 同上，`create_cases()` 末尾 `random.shuffle(cases)` |
| `case.inputs[i]` 对复合组是 `list[InputCaseConfig]` | `atk/case_generator/generator/base_generator.py:87-93`、`parameter_tensors.py:38-44` |
| 整组重建用 `model_copy(update=...)` | ATK 自己就这么用：`atk/catccos/report_expand.py:256` |

设计期规则：**per-combo 长度变化的复合组，必须在生成器里整组重建。**
逐个改 `case.inputs[i][j].range_values` 只在长度恰好相等时才对，而长度是随机抽的。

**Files:**
- Modify: `skill/repo-task-atk-test/references/atk-parameter-capabilities.json`
- Modify: `skill/repo-task-atk-test/scripts/atk_lookup.py`
- Modify: `skill/repo-task-atk-test/assets/example/constraint.py`
- Modify: `skill/repo-task-atk-test/references/plugin-authoring.md`
- Modify: `skill/repo-task-atk-test/references/artifact-contracts.json`
- Modify: `skill/repo-task-atk-test/SKILL.md`
- Test: `skill/repo-task-atk-test/tests/test_group_length.py`

**Interfaces:**
- Consumes：`_contracts.l0_refs(text)`（Plan B 已有，锁 L3 用它解析 `# [L0:key=值]`）
- Produces：
  - L0 新增顶层键 `group_length`，含 `source` / `per_case_selection` /
    `follows_combo_order` / `runtime_type` / `min_length_key` / `evidence`
  - `atk_lookup.TOPICS` 新增 `group_length`
  - `assets/example/constraint.py` 新增模块级函数 `rebuild_group(case, index, values, dtype)`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_group_length.py`：

```python
"""可变长复合组：三条事实必须查得到、模板必须挂在它们上面。

S2 的空组那次，L0 里有 group_min_length，plugin-authoring.md 里有
「不要把缺省参数表示为空的复合输入组」——但后者是写插件时才读的文件，
而空组是在 decl 设计期定死的。代价是推翻 decl、拆双分面、重写三个文件。

这一组用例锁的是「事实在不在、模板认不认」，不是「ATK 行为对不对」。
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import atk_lookup  # noqa: E402
import _contracts  # noqa: E402

L0 = json.loads((SKILL_ROOT / "references" / "atk-parameter-capabilities.json")
                .read_text(encoding="utf-8"))
TEMPLATE = SKILL_ROOT / "assets" / "example" / "constraint.py"
LOOKUP = SKILL_ROOT / "scripts" / "atk_lookup.py"


class GroupLengthFactsTest(unittest.TestCase):
    def test_l0_records_where_the_length_comes_from(self):
        block = L0["group_length"]
        self.assertEqual("yaml_tuple_numbers", block["source"])

    def test_l0_records_that_length_ignores_combo_order(self):
        # 这条是最贵的一条：以为长度按 combo 顺序对应，逐个改元素就会错位。
        self.assertFalse(L0["group_length"]["follows_combo_order"])

    def test_l0_records_the_runtime_type(self):
        self.assertEqual("list[InputCaseConfig]",
                         L0["group_length"]["runtime_type"])

    def test_l0_points_at_the_canonical_min_length_key(self):
        # 下限不在这里再写一遍，指向已有的键，避免两处维护同一个数。
        key = L0["group_length"]["min_length_key"]
        self.assertIn(key, L0)
        self.assertEqual(1, L0[key])

    def test_every_fact_carries_source_evidence(self):
        evidence = L0["group_length"]["evidence"]
        self.assertTrue(evidence)
        for item in evidence:
            with self.subTest(item=item):
                self.assertIn(":", item, "证据要写到行号")


class GroupLengthLookupTest(unittest.TestCase):
    def test_topic_is_queryable(self):
        self.assertIn("group_length", atk_lookup.TOPICS)

    def test_lookup_prints_the_facts(self):
        result = subprocess.run(
            [sys.executable, str(LOOKUP), "group_length"],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("follows_combo_order", result.stdout)


class GroupRebuildTemplateTest(unittest.TestCase):
    def test_template_shows_how_to_rebuild_a_whole_group(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("def rebuild_group(", text)
        self.assertIn("model_copy", text)

    def test_template_hangs_the_rebuild_on_the_facts(self):
        # 事实变了模板要跟着红，这正是锁 L3 的作用。
        keys = {key for key, _ in _contracts.l0_refs(
            TEMPLATE.read_text(encoding="utf-8"))}
        self.assertIn("group_length.follows_combo_order", keys)
        self.assertIn("group_length.runtime_type", keys)

    def test_template_still_parses(self):
        import ast
        ast.parse(TEMPLATE.read_text(encoding="utf-8"))


class GroupLengthReferenceTest(unittest.TestCase):
    def test_design_time_rule_is_written_down(self):
        text = (SKILL_ROOT / "references" / "plugin-authoring.md") \
            .read_text(encoding="utf-8")
        self.assertIn("整组重建", text)
        self.assertIn("tuple_numbers", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_group_length.py -q`
Expected: FAIL，`KeyError: 'group_length'`。

- [ ] **Step 3: 把三条事实写进 L0**

在 `references/atk-parameter-capabilities.json` 的 `"group_min_length": 1,` 之后插入：

```json
  "group_length": {
    "source": "yaml_tuple_numbers",
    "per_case_selection": "random_choice_then_shuffled",
    "follows_combo_order": false,
    "runtime_type": "list[InputCaseConfig]",
    "min_length_key": "group_min_length",
    "rebuild_idiom": "InputCaseConfig.model_copy(update={...})",
    "evidence": [
      "atk/case_generator/generator/parameter_types/parameter_tensors.py:54-59",
      "atk/case_generator/generator/parameter_types/parameter_attrs.py:52-60",
      "atk/configs/design_config.py:154-183",
      "atk/case_generator/generator/base_generator.py:87-93",
      "atk/catccos/report_expand.py:256"
    ]
  },
```

- [ ] **Step 4: 加查询主题**

`scripts/atk_lookup.py` 的 `TOPICS` 里，在 `"group_types"` 那一行之后插入：

```python
    "group_length": (None, "group_length", "复合组的长度从哪来、每条用例怎么定"),
```

- [ ] **Step 5: 模板补整组重建**

在 `assets/example/constraint.py` 的 `TABLE` 定义之后、`@GENERATOR_REGISTRY.register` 之前插入：

```python
def rebuild_group(case, index, values, dtype):
    """按本条 combo 的实际长度，整组重建一个复合输入。

    组长度来自 YAML 的 tuple_numbers，ATK 每条用例从里面随机抽一个，
    抽完还会洗牌，与 combo 的顺序没有对应关系。 # [L0:group_length.follows_combo_order=False]
    所以逐个改 `case.inputs[i][j].range_values` 只在长度恰好相等时才对；
    per-combo 长度会变的复合组，必须像这样整组重建。 # [L0:group_length.runtime_type=list[InputCaseConfig]]

    组长度下限是 1，空组表达不出来——缺省参数用显式 default token 表示，
    不要拿空组去表达「这个参数这条用例不传」。 # [L0:group_min_length=1]

    本例的算子没有复合组输入，所以这个函数在下面的 generate() 里没有被调用；
    有复合组的算子照抄这一段，把 index 换成该输入在 inputs 里的序号。
    """
    template = case.inputs[index][0]
    case.inputs[index] = [
        template.model_copy(update={"range_values": value, "dtype": dtype})
        for value in values
    ]
    return case
```

- [ ] **Step 6: 写进 reference**

`references/plugin-authoring.md` 的「## 生成器」小节，把

```markdown
覆写字段前确认对象层级：`case.inputs[i]` 可能是 tensor、scalar、attr 或 attr group。
```

改成一段：

```markdown
覆写字段前确认对象层级：`case.inputs[i]` 可能是 tensor、scalar、attr 或复合组。

复合组在运行期是 `list[InputCaseConfig]`，不是单个对象。

组长度来自 YAML 的 `tuple_numbers`，ATK 每条用例随机抽一个，抽完还洗牌。

所以组长度与 combo 的顺序没有对应关系，查 `atk_lookup.py group_length` 看全部事实。

per-combo 长度会变的复合组必须**整组重建**，逐个改元素只在长度恰好相等时才对。

整组重建的写法见 `assets/example/constraint.py` 的 `rebuild_group`。
```

- [ ] **Step 7: 让主题在 S2 卡上出现**

`references/artifact-contracts.json` 的 S2 → `lookup_topics` 追加：

```json
      "lookup_topics": ["group_types", "group_length", "parameter_type", "dtype", "comparator", "binding"],
```

然后：

```bash
python3 scripts/render_views.py --cards
```

把 S2 那一节原样替换回 `SKILL.md`。

- [ ] **Step 8: 跑全量测试**

Run: `python3 -m pytest tests/ -q`
Expected: PASS，`304 passed, 9 skipped`（本 Task 新增 11 条）。

特别确认 `tests/test_contracts.py::TemplateFactRefTest` 与
`tests/test_assets_example.py::GeneratorTemplateTest` 全绿——
前者证明模板挂上的三个 L0 键都解析得了、值也对得上，
后者证明 `rebuild_group` 没有写进 `CaseConfig` 不存在的字段。

- [ ] **Step 9: 提交**

```bash
git add skill/repo-task-atk-test/references/atk-parameter-capabilities.json \
        skill/repo-task-atk-test/references/artifact-contracts.json \
        skill/repo-task-atk-test/scripts/atk_lookup.py \
        skill/repo-task-atk-test/assets/example/constraint.py \
        skill/repo-task-atk-test/references/plugin-authoring.md \
        skill/repo-task-atk-test/SKILL.md \
        skill/repo-task-atk-test/tests/test_group_length.py
git commit -m "feat: add the variable-length group knowledge domain

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 5: B6 —— 可表达性判定前移到 `make_must_cover.py`

现在这些判定全在 `make_yaml.py`，那是设计链的**第四步**：
`make_must_cover` → 物化脚本 → `make_yaml`。而做决定的时刻在第一步之前——
decl 里写下 dims 和 parameters 的那一刻。

代价是 agent 写完 decl、跑完 must_cover、写完物化脚本、跑完物化，
到第四步才被告知「这个轴取值 ATK 表达不出来」，然后回第一步重来。

同一条规则要在两个时点回答同一个问题，写两份必然漂移，所以判定函数只有一份。

**Files:**
- Create: `skill/repo-task-atk-test/scripts/_expressibility.py`
- Modify: `skill/repo-task-atk-test/scripts/make_yaml.py`
- Modify: `skill/repo-task-atk-test/scripts/make_must_cover.py`
- Modify: `skill/repo-task-atk-test/references/case-design.md`
- Test: `skill/repo-task-atk-test/tests/test_expressibility.py`

**Interfaces:**
- Produces：
  - `_expressibility.TYPE_BY_SEMANTICS: dict[tuple[str, str], str]`（从 `make_yaml` 迁来）
  - `_expressibility.EMPTY_GROUP: str` — 空组那句报错文案的唯一出处
  - `_expressibility.semantic_kind(value) -> str` — `"none"` / `"scalar"` / `"sequence"` / `"empty_sequence"`
  - `_expressibility.check_contracts(parameters) -> list[tuple[str, str]]` — `(契约键, 问题)`
  - `_expressibility.check_axis_values(values_by_axis, parameters, extract) -> list[str]`
- Consumes（改造后）：`make_yaml` 从 `_expressibility` 导入 `TYPE_BY_SEMANTICS`，不再自持一份

- [ ] **Step 1: 写失败测试**

创建 `tests/test_expressibility.py`：

```python
"""可表达性判定：同一条规则，decl 期与物化期同一份实现。

设计链是 make_must_cover → 物化 → make_yaml 三步。判定挂在第三步时，
agent 要写完 decl、跑完 must_cover、写完物化脚本、跑完物化，
才被告知第一步就定死的取值 ATK 表达不出来。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import _expressibility as expr  # noqa: E402

SCRIPTS = SKILL_ROOT / "scripts"
EXAMPLE = SKILL_ROOT / "assets" / "example"


class SemanticKindTest(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual("none", expr.semantic_kind(None))
        self.assertEqual("none", expr.semantic_kind("default"))
        self.assertEqual("scalar", expr.semantic_kind(3))
        self.assertEqual("sequence", expr.semantic_kind([1, 2]))
        self.assertEqual("empty_sequence", expr.semantic_kind([]))


class CheckContractsTest(unittest.TestCase):
    def test_legal_contract_passes(self):
        self.assertEqual([], expr.check_contracts({
            "x": {"element_kind": "tensor", "runtime_container": "single"}}))

    def test_illegal_semantics_combination_is_named(self):
        problems = expr.check_contracts({
            "x": {"element_kind": "tensor", "runtime_container": "bag"}})
        self.assertEqual(["x"], [key for key, _ in problems])

    def test_attr_without_dtype_is_rejected(self):
        problems = expr.check_contracts({
            "n": {"element_kind": "attr", "runtime_container": "single"}})
        self.assertEqual(1, len(problems))
        self.assertIn("dtype", problems[0][1])

    def test_omitted_contract_is_skipped(self):
        self.assertEqual([], expr.check_contracts({"n": {"omitted": True}}))

    def test_channel_qualified_key_reports_the_bare_name(self):
        problems = expr.check_contracts({
            "method_inputs.n": {"element_kind": "attr",
                                "runtime_container": "single"}})
        self.assertEqual(["n"], [key for key, _ in problems])


class CheckAxisValuesTest(unittest.TestCase):
    PARAMS = {"dims": {"element_kind": "attr", "runtime_container": "list",
                       "dtype": "int64_t"}}
    EXTRACT = {"dims": {"from": "attr", "name": "dims",
                        "runtime_container": "list"}}

    def test_empty_group_is_rejected_at_declaration_time(self):
        problems = expr.check_axis_values(
            {"dims": [[], [0], [0, 1]]}, self.PARAMS, self.EXTRACT)
        self.assertEqual(1, len(problems))
        self.assertIn("空组", problems[0])

    def test_mixed_sequence_and_scalar_needs_a_facet_split(self):
        problems = expr.check_axis_values(
            {"dims": [0, [0, 1]]}, self.PARAMS, self.EXTRACT)
        self.assertEqual(1, len(problems))
        self.assertIn("分面", problems[0])

    def test_sequence_needs_a_group_runtime_container(self):
        problems = expr.check_axis_values(
            {"dims": [[0, 1]]}, self.PARAMS,
            {"dims": {"from": "attr", "name": "dims"}})
        self.assertEqual(1, len(problems))
        self.assertIn("runtime_container", problems[0])

    def test_axis_pointing_at_a_missing_contract(self):
        problems = expr.check_axis_values(
            {"dims": [[0]]}, {}, self.EXTRACT)
        self.assertEqual(1, len(problems))
        self.assertIn("parameters", problems[0])

    def test_non_attr_axes_are_out_of_scope(self):
        # dtype / shape 这类轴不是 attr，可表达性由别处管。
        self.assertEqual([], expr.check_axis_values(
            {"dtype": ["fp32"]}, self.PARAMS,
            {"dtype": {"from": "input_dtype", "index": 0}}))


class MakeMustCoverGateTest(unittest.TestCase):
    """decl 期就要报，不能拖到 make_yaml。"""

    def _decl(self):
        return json.loads((EXAMPLE / "decl.json").read_text(encoding="utf-8"))

    def _run(self, decl):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decl.json"
            path.write_text(json.dumps(decl, ensure_ascii=False),
                            encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPTS / "make_must_cover.py"),
                 "-d", str(path), "-o", str(Path(tmp) / "out.json")],
                capture_output=True, text=True)

    def test_example_declaration_still_passes(self):
        self.assertEqual(0, self._run(self._decl()).returncode)

    def test_empty_group_axis_fails_at_step_one(self):
        decl = self._decl()
        decl["dims"]["dims_axis"] = [[], [0]]
        decl["parameters"]["dims_axis"] = {
            "element_kind": "attr", "runtime_container": "list",
            "dtype": "int64_t", "combo_key": "dims_axis"}
        decl["axes"].append("dims_axis")
        decl["extract"]["dims_axis"] = {
            "from": "attr", "name": "dims_axis", "runtime_container": "list"}
        decl["coverage_policy"]["main_effects"].append("dims_axis")
        decl["coverage_policy"]["baseline"]["dims_axis"] = [0]
        result = self._run(decl)
        self.assertEqual(2, result.returncode, result.stdout)
        self.assertIn("空组", result.stderr)

    def test_missing_attr_dtype_fails_at_step_one(self):
        decl = self._decl()
        decl["parameters"]["dim"].pop("dtype")
        result = self._run(decl)
        self.assertEqual(2, result.returncode, result.stdout)
        self.assertIn("dtype", result.stderr)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_expressibility.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named '_expressibility'`。

- [ ] **Step 3: 写共享判定模块**

创建 `scripts/_expressibility.py`：

```python
"""参数契约与轴取值的可表达性判定，decl 期与物化期共用一份。

同一个问题要在两个时点回答：`make_must_cover.py` 拿 decl 的 dims 问
「这个设计 ATK 表达得出来吗」，`make_yaml.py` 拿物化后的 combos 再问一次
「填进来的取值仍然表达得出来吗」。

判定挂在第二次时，agent 要写完 decl、跑完 must_cover、写完物化脚本、
跑完物化，到第四步才知道第一步就定死的取值不可表达。前移一次，
但两处必须是同一份实现——写两份的那一刻就开始漂移。

本模块不是 CLI。
"""

# (element_kind, runtime_container) → YAML type。反查
# references/atk-parameter-capabilities.json 的 flat_types / group_types，
# 两边同源，不重新起一套写法。
TYPE_BY_SEMANTICS = {
    ("tensor", "single"): "tensor",
    ("tensor", "list"): "tensors",
    ("tensor", "tuple"): "tensor_tuple",
    ("scalar", "single"): "scalar",
    ("scalar", "list"): "scalars",
    ("scalar", "tuple"): "scalar_tuple",
    ("attr", "single"): "attr",
    ("attr", "list"): "attrs",
    ("attr", "tuple"): "attr_tuple",
}

GROUP_CONTAINERS = frozenset({"list", "tuple"})
DEFAULT_TOKEN = "default"

# 空组那句话只有这一处出处。两个脚本各写一遍时，改了一处另一处就成了旧闻。
EMPTY_GROUP = ("出现空序列；ATK 26.8.8 复合组最少一个成员，空组表达不出来；"
               "缺省参数用显式 default token 表示")


def semantic_kind(value):
    """一个取值在运行期会 lowering 成什么形态。"""
    if value is None or value == DEFAULT_TOKEN:
        return "none"
    if isinstance(value, (list, tuple)):
        return "empty_sequence" if len(value) == 0 else "sequence"
    return "scalar"


def check_contracts(parameters):
    """契约本身合法吗——不需要任何取值就能判，所以 decl 期就该判完。

    返回 `[(契约键, 问题), ...]`。键让调用方知道哪几个契约不能再往下推导，
    避免同一个契约在后面的推导里再报一串派生错误。
    """
    problems = []
    for name, contract in (parameters or {}).items():
        bare = name.rpartition(".")[2]
        if (contract or {}).get("omitted"):
            continue
        element = (contract or {}).get("element_kind")
        container = (contract or {}).get("runtime_container")
        if (element, container) not in TYPE_BY_SEMANTICS:
            problems.append(
                (bare, f"{bare}: 契约的 {element!r}/{container!r} 不是合法组合"))
            continue
        if element in ("attr", "scalar") and not contract.get("dtype"):
            problems.append(
                (bare, f"{bare}: 契约缺 dtype"
                       "（attr/scalar 的 dtype 推不出来，必须声明）"))
    return problems


def check_axis_values(values_by_axis, parameters, extract):
    """轴取值 ATK 表达得出来吗。

    decl 期喂 `dims`（轴的取值全集），物化后喂 combos 在该轴上的投影。
    两种取值来源，同一套判据。

    只看 extract 规则声明 `from: attr` 的轴：别的轴（dtype、shape）的
    可表达性由数据生成那一侧管，不在这里重复判。
    """
    problems = []
    for axis, values in (values_by_axis or {}).items():
        rule = (extract or {}).get(axis) or {}
        if rule.get("from") != "attr":
            continue
        name = rule.get("name")
        if name not in (parameters or {}):
            problems.append(
                f"语义轴 {axis!r} 的 extract 指向 {name!r}，"
                "但 parameters 里没有这个契约")
            continue
        kinds = {semantic_kind(value) for value in values}
        if "empty_sequence" in kinds:
            problems.append(f"{name!r} 的取值里{EMPTY_GROUP}")
        if "sequence" in kinds and ("none" in kinds or "scalar" in kinds):
            problems.append(
                f"{name!r} 在同一设计里混合 {sorted(kinds)}；"
                "单个输入不能既是平面 attr 又是复合 attr，请拆分接口分面")
        if "sequence" in kinds and \
                rule.get("runtime_container") not in GROUP_CONTAINERS:
            problems.append(
                f"{name!r} 是序列语义，extract 必须写 "
                "runtime_container=list 或 tuple")
    return problems
```

- [ ] **Step 4: `make_must_cover.py` 在 decl 期跑判定**

导入区补：

```python
from _expressibility import check_axis_values, check_contracts
```

在 `main()` 的 `infeasible = spec.get("infeasible", [])` 之后插入：

```python
    # 可表达性在这里判完，不留到第四步的 make_yaml。判据是 decl 自己写下的
    # 轴取值与契约，此刻就在手上；拖到 make_yaml 意味着 agent 要先写完物化
    # 脚本、跑完物化，才被告知第一步定死的取值 ATK 表达不出来。
    problems = [message for _, message in check_contracts(spec.get("parameters"))]
    problems += check_axis_values(dims, spec.get("parameters"),
                                  spec.get("extract"))
    if problems:
        print(f"声明里有 {len(problems)} 处 ATK 表达不出来的地方：", file=sys.stderr)
        for index, message in enumerate(problems, 1):
            print(f"  {index}. {message}", file=sys.stderr)
        return 2
```

- [ ] **Step 5: `make_yaml.py` 改用共享实现**

删掉 `make_yaml.py` 里的 `TYPE_BY_SEMANTICS` 字面量、`DEFAULT_TOKEN` 常量与
`_semantic_kind` 函数，改为导入（三样都留一份副本就是留三处会漂移的知识）：

```python
from _expressibility import (DEFAULT_TOKEN, EMPTY_GROUP, TYPE_BY_SEMANTICS,
                             check_axis_values, check_contracts)
```

把 `_check_semantic_axes` 整个函数替换成：

```python
def _check_semantic_axes(must_cover, contracts):
    """物化之后再判一次：这次喂的是 combos 在各轴上的实际投影。

    decl 期 `make_must_cover.py` 已经拿 dims 判过一遍，判据同一份。
    物化脚本可能填进 dims 里没有的取值，所以这一遍不能省。
    """
    axes = must_cover.get("axes") or []
    combos = must_cover.get("combos") or []
    projection = {axis: [combo.get(axis) for combo in combos] for axis in axes}
    return check_axis_values(projection, contracts, must_cover.get("extract"))
```

`_attr_input` 里的空序列断言改用同一句文案：

```python
    lengths = _unique(len(value) for value in values)
    _require(0 not in lengths, f"{name}: combos 里{EMPTY_GROUP}")
```

`build_design` 里改成先跑一次契约检查、跳过已知坏契约：

```python
    channels = {channel: [] for channel in CHANNELS}
    contract_problems = check_contracts(contracts)
    problems = [message for _, message in contract_problems]
    broken = {key for key, _ in contract_problems}
    for name, contract in contracts.items():
        # 契约键可写成 "inputs.x" 限定通道，与 _atk_capabilities._contract_for 同构
        channel, _, bare = name.rpartition(".")
        channel = channel or contract.get("channel") or "inputs"
        if channel not in CHANNELS:
            problems.append(f"{name}: 未知通道 {channel!r}")
            continue
        if bare in broken:
            # 契约本身不成立，再往下推导只会刷出一串派生错误
            continue

        try:
            if contract.get("omitted"):
                config = _omitted_input(bare)
            elif contract.get("element_kind") == "tensor":
                config = _tensor_input(bare, contract, combos)
            else:
                config = _attr_input(bare, contract, combos,
                                     contract["element_kind"])
        except DeclarationError as exc:
            problems.append(str(exc))
            continue
        if contract.get("aclnn_name"):
            config["aclnn_name"] = contract["aclnn_name"]
        channels[channel].append(config)
```

- [ ] **Step 6: 跑测试确认通过**

Run: `python3 -m pytest tests/test_expressibility.py tests/test_make_yaml.py tests/test_make_must_cover.py tests/test_assets_example.py -q`
Expected: PASS。

`tests/test_make_yaml.py` 里若有用例断言了旧的空组文案或旧的契约报错措辞，
改成断言 `_expressibility.EMPTY_GROUP` 的子串，不要把文案改回去。

- [ ] **Step 7: 写进 reference**

`references/case-design.md` 的「## 接口分面与门禁」小节，把

```markdown
YAML 是否可表达由 `make_yaml.py` 自身在推导时判定，不需要单独的能力门禁再查一遍——
它查的会是自己刚生成的东西。
```

改成：

```markdown
可表达性判两次，判据是同一份。

`make_must_cover.py` 在读 decl 时先判一次：契约的语义组合、attr 的 dtype、轴取值的形态。

`make_yaml.py` 在物化之后再判一次，喂的是 combos 的实际投影。

不需要单独的能力门禁再查一遍，它查的会是脚本自己刚生成的东西。
```

- [ ] **Step 8: 跑全量测试**

Run: `python3 -m pytest tests/ -q`
Expected: PASS，`318 passed, 9 skipped`（本 Task 新增 14 条；本机无 torch 时 skip 数为 11）。

- [ ] **Step 9: 提交**

```bash
git add skill/repo-task-atk-test/scripts/_expressibility.py \
        skill/repo-task-atk-test/scripts/make_yaml.py \
        skill/repo-task-atk-test/scripts/make_must_cover.py \
        skill/repo-task-atk-test/references/case-design.md \
        skill/repo-task-atk-test/tests/test_expressibility.py
git commit -m "refactor: answer expressibility at declaration time, once

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 6: B4（上）—— 适配器判定必须在 S2 完成

`align_signatures.py` 在 S2 一开始就产出了判定所需的全部输入，但它给的是
**条件性预警**：某个可空指针「若有用例把它置空」就需要 typed 空指针适配器。

roll 那轮 `semantic_review` 明确预言了这件事，agent 判断「S3 冒烟再说」，
于是 S3 冒烟才发现、才改 YAML——而改 YAML 要重跑 `atk case`，
正撞上「S2 后冻结」的纪律，只能手工自证十次。

**条件成不成立不需要问 agent**：用例集就在手上，把每条预警拿到用例集里核一遍就有答案。

**执行器实际绑的是哪个也不需要问 YAML**：ATK 从用例 JSON 的 `api_type` /
`aclnn_api_type` 取执行器（`atk/tasks/backends/backend.py:61`、
`atk/tasks/backends/pyaclnn_backend.py:150`），默认值是 `"function"` /
`"aclnn_function"`（`atk/configs/case_config.py:90-91`）。所以读用例 JSON。

**Files:**
- Create: `skill/repo-task-atk-test/scripts/check_adapter_binding.py`
- Modify: `skill/repo-task-atk-test/references/artifact-contracts.json`
- Modify: `skill/repo-task-atk-test/references/decision-points.md`（由脚本重生成）
- Modify: `skill/repo-task-atk-test/SKILL.md`
- Modify: `skill/repo-task-atk-test/references/plugin-authoring.md`
- Test: `skill/repo-task-atk-test/tests/test_adapter_binding.py`

**Interfaces:**
- Consumes：
  - `_case_utils.iter_cases(case_data)`、`_case_utils.iter_input_specs(case)`、
    `_case_utils.load_json(path)`
  - `signature_alignment.json` 的 `aclnn_adapter.{required,reasons,determinable}`、
    `baseline_adapter.{...}`、`semantic_review[].{parameter,issue}`
- Produces：
  - `check_adapter_binding.DEFAULT_WIRING: dict[str, str]`
  - `check_adapter_binding.wiring(cases, key) -> set[str]`
  - `check_adapter_binding.nulled_parameters(cases) -> dict[str, list]`
  - `check_adapter_binding.judge(alignment, cases) -> tuple[dict, list[str]]` —
    `(报告, 问题列表)`
  - CLI `-j/--case-json`、`-a/--alignment`、`-o/--output`；退出码 0 / 2 / 3
  - 产物 `evidence/adapter_binding.json`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_adapter_binding.py`：

```python
"""适配器判定必须在 S2 完成，判据从用例集读。

真机实测（roll，2026-08-15）：semantic_review 在 S2 就预言了「dims 置空
需要 typed 空指针适配器」，agent 判断「S3 冒烟再说」。S3 才发现、才改 YAML，
而改 YAML 要重跑 atk case，撞上冻结纪律，只能手工自证十次。

预警成不成立不需要问 agent——用例集就在手上。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import check_adapter_binding as gate  # noqa: E402

SCRIPT = SKILL_ROOT / "scripts" / "check_adapter_binding.py"


def case(case_id, inputs, **wiring):
    return dict({"id": case_id, "inputs": inputs}, **wiring)


def tensor(name, values="normal"):
    return {"name": name, "type": "tensor", "dtype": "fp32",
            "shape": [4], "range_values": values}


CLEAN = {"aclnn_adapter": {"required": False, "reasons": [],
                           "determinable": True},
         "baseline_adapter": {"required": False, "reasons": [],
                              "determinable": True},
         "semantic_review": []}


class WiringTest(unittest.TestCase):
    def test_default_is_assumed_when_the_case_omits_the_key(self):
        # ATK 的 CaseConfig 默认就是 function / aclnn_function，
        # 用例里没写这个键不等于「没绑」，等于绑了默认执行器。
        self.assertEqual({"aclnn_function"},
                         gate.wiring([case(0, [tensor("x")])],
                                     "aclnn_api_type"))

    def test_explicit_wiring_is_read_from_the_case(self):
        self.assertEqual(
            {"my_exec"},
            gate.wiring([case(0, [tensor("x")], aclnn_api_type="my_exec")],
                        "aclnn_api_type"))


class NulledParametersTest(unittest.TestCase):
    def test_null_token_counts(self):
        cases = [case(0, [tensor("x"), tensor("dims", "null")])]
        self.assertEqual({"dims": [0]}, gate.nulled_parameters(cases))

    def test_boxed_null_token_counts(self):
        cases = [case(7, [tensor("dims", ["null"])])]
        self.assertEqual({"dims": [7]}, gate.nulled_parameters(cases))

    def test_default_token_counts(self):
        cases = [case(2, [{"name": "n", "type": "attr", "dtype": "int64_t",
                           "range_values": "default"}])]
        self.assertEqual({"n": [2]}, gate.nulled_parameters(cases))

    def test_ordinary_values_do_not_count(self):
        self.assertEqual({}, gate.nulled_parameters([case(0, [tensor("x")])]))


class JudgeTest(unittest.TestCase):
    def test_clean_alignment_passes(self):
        report, problems = gate.judge(CLEAN, [case(0, [tensor("x")])])
        self.assertEqual([], problems)
        self.assertFalse(report["verdicts"]["aclnn_api_type"]["required"])

    def test_required_adapter_left_on_the_default_is_rejected(self):
        alignment = json.loads(json.dumps(CLEAN))
        alignment["aclnn_adapter"] = {
            "required": True, "reasons": ["出参不在末尾"], "determinable": True}
        _, problems = gate.judge(alignment, [case(0, [tensor("x")])])
        self.assertEqual(1, len(problems))
        self.assertIn("aclnn_api_type", problems[0])

    def test_required_adapter_that_is_wired_passes(self):
        alignment = json.loads(json.dumps(CLEAN))
        alignment["aclnn_adapter"] = {
            "required": True, "reasons": ["出参不在末尾"], "determinable": True}
        _, problems = gate.judge(
            alignment, [case(0, [tensor("x")], aclnn_api_type="my_exec")])
        self.assertEqual([], problems)

    def test_undeterminable_alignment_blocks(self):
        # 「没找到理由」不等于「不需要适配器」。
        alignment = json.loads(json.dumps(CLEAN))
        alignment["baseline_adapter"]["determinable"] = False
        _, problems = gate.judge(alignment, [case(0, [tensor("x")])])
        self.assertEqual(1, len(problems))
        self.assertIn("determinable", problems[0])

    def test_conditional_warning_that_the_case_set_triggers(self):
        alignment = json.loads(json.dumps(CLEAN))
        alignment["semantic_review"] = [
            {"parameter": "dims", "issue": "aclIntArray 是可空指针"}]
        report, problems = gate.judge(
            alignment, [case(0, [tensor("x"), tensor("dims", "null")])])
        self.assertTrue(report["reviews"][0]["triggered"])
        self.assertEqual([0], report["reviews"][0]["evidence_case_ids"])
        self.assertEqual(1, len(problems))
        self.assertIn("typed", problems[0])

    def test_conditional_warning_that_no_case_triggers_is_closed(self):
        # 不触发也要留痕：「已判定不触发」和「没判过」必须分得开。
        alignment = json.loads(json.dumps(CLEAN))
        alignment["semantic_review"] = [
            {"parameter": "dims", "issue": "aclIntArray 是可空指针"}]
        report, problems = gate.judge(
            alignment, [case(0, [tensor("x"), tensor("dims")])])
        self.assertFalse(report["reviews"][0]["triggered"])
        self.assertEqual([], problems)

    def test_non_parameter_warning_is_surfaced_but_does_not_block(self):
        # 重载数、变长签名这类预警脚本判不了。硬拦会把每一轮都堵死，
        # 所以只标记 requires_manual_review 并原样列出。
        alignment = json.loads(json.dumps(CLEAN))
        alignment["semantic_review"] = [
            {"parameter": None, "issue": "基线接口有 3 个 aten 重载"}]
        report, problems = gate.judge(alignment, [case(0, [tensor("x")])])
        self.assertEqual([], problems)
        self.assertTrue(report["reviews"][0]["requires_manual_review"])


class CliTest(unittest.TestCase):
    def _run(self, alignment, cases):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "align.json").write_text(
                json.dumps(alignment), encoding="utf-8")
            (tmp / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT),
                 "-j", str(tmp / "cases.json"),
                 "-a", str(tmp / "align.json"),
                 "-o", str(tmp / "out.json")],
                capture_output=True, text=True)
            report = json.loads((tmp / "out.json").read_text(encoding="utf-8")) \
                if (tmp / "out.json").exists() else None
            return result, report

    def test_pass_writes_a_report_and_exits_zero(self):
        result, report = self._run(CLEAN, [case(0, [tensor("x")])])
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("verdicts", report)

    def test_failure_exits_two_and_still_writes_the_report(self):
        alignment = json.loads(json.dumps(CLEAN))
        alignment["aclnn_adapter"] = {
            "required": True, "reasons": ["出参不在末尾"], "determinable": True}
        result, report = self._run(alignment, [case(0, [tensor("x")])])
        self.assertEqual(2, result.returncode)
        self.assertIsNotNone(report, "判不过也要留下证据，S5 证据链要引用它")

    def test_missing_input_exits_three(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "-j", "/nope.json",
             "-a", "/nope.json", "-o", "/tmp/x.json"],
            capture_output=True, text=True)
        self.assertEqual(3, result.returncode)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_adapter_binding.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'check_adapter_binding'`。

- [ ] **Step 3: 写门禁脚本**

创建 `scripts/check_adapter_binding.py`：

```python
"""S2 出口：适配器该不该写、写没写，在冻结之前判定完。

`align_signatures.py` 在 S2 一开始就产出了判定所需的全部输入，但它给的是
**条件性预警**——某个可空指针「若有用例把它置空」就需要 typed 空指针适配器。
条件成不成立，签名里看不出来。

真机实测（roll，2026-08-15）：agent 判断「S3 冒烟再说」，于是 S3 才发现、
才改 YAML，而改 YAML 要重跑 `atk case`，正撞上「S2 后冻结」的纪律。

两件事都不需要问 agent：

- 预警条件成不成立：用例集就在手上，逐条核实即可
- 实际绑了哪个执行器：ATK 从用例 JSON 的 api_type / aclnn_api_type 取
  （`atk/tasks/backends/backend.py:61`、`atk/tasks/backends/pyaclnn_backend.py:150`），
  默认值 function / aclnn_function（`atk/configs/case_config.py:90-91`）

退出码：0 判定通过；2 该写没写或无法判定；3 输入读不出来。
"""

import argparse
import json
import sys

from _case_utils import iter_cases, iter_input_specs, load_json

# ATK 的 CaseConfig 默认值。用例里没写这个键不等于「没绑」，
# 等于绑了 ATK 内置的默认执行器。
DEFAULT_WIRING = {"api_type": "function", "aclnn_api_type": "aclnn_function"}

# ATK 把「这个输入这条用例不传值」编码成这几个令牌。
# "null" / ["null"] 见 `atk/case_generator/generator/base_generator.py:87-93`，
# "default" 见 L0 的 default_token。
NULL_TOKENS = ("null", "default")

SIDES = (("基线", "api_type", "baseline_adapter"),
         ("aclnn", "aclnn_api_type", "aclnn_adapter"))


def wiring(cases, key):
    """用例集里这一侧实际绑的执行器名集合。"""
    return {case.get(key) or DEFAULT_WIRING[key] for case in cases}


def nulled_parameters(cases):
    """哪些输入名至少被一条用例置空，返回 {名字: [用例号, ...]}。"""
    hits = {}
    for case in cases:
        for spec in iter_input_specs(case):
            value = spec.get("range_values")
            if isinstance(value, list):
                value = value[0] if len(value) == 1 else None
            if value in NULL_TOKENS:
                hits.setdefault(spec.get("name"), []).append(case.get("id"))
    return hits


def judge(alignment, cases):
    """返回 (报告, 问题列表)。问题列表非空即判不过。"""
    report = {"total_cases": len(cases), "verdicts": {}, "reviews": []}
    problems = []

    for label, key, block_name in SIDES:
        block = alignment.get(block_name) or {}
        bound = wiring(cases, key)
        adapted = bound - {DEFAULT_WIRING[key]}
        report["verdicts"][key] = {
            "required": bool(block.get("required")),
            "determinable": bool(block.get("determinable", True)),
            "bound": sorted(bound),
            "adapted": bool(adapted),
        }
        if block.get("required") and not adapted:
            reasons = "；".join(block.get("reasons") or []) or "见对齐报告"
            problems.append(
                f"{label}侧适配器判定为必需，但用例集里 {key} 还是默认 "
                f"{DEFAULT_WIRING[key]}：{reasons}")
        if not block.get("determinable", True):
            problems.append(
                f"{label}侧适配器 determinable=false：基线形参名没取全，"
                "「没找到理由」不等于「不需要适配器」；"
                "先按 semantic_review 补齐形参名，再冻结")

    nulled = nulled_parameters(cases)
    aclnn_adapted = bool(wiring(cases, "aclnn_api_type")
                         - {DEFAULT_WIRING["aclnn_api_type"]})
    for item in alignment.get("semantic_review") or []:
        name = item.get("parameter")
        if not name:
            # 重载数、变长签名这类预警脚本判不了，硬拦会把每一轮都堵死。
            # 原样列出，交给 S2 的人工过目，但不构成门禁失败。
            report["reviews"].append({
                "parameter": None, "issue": item.get("issue"),
                "triggered": None, "requires_manual_review": True})
            continue
        ids = nulled.get(name) or []
        report["reviews"].append({
            "parameter": name, "issue": item.get("issue"),
            "triggered": bool(ids),
            "triggered_case_count": len(ids),
            "evidence_case_ids": ids[:12],
            "requires_manual_review": False,
        })
        if ids and not aclnn_adapted:
            problems.append(
                f"{name} 有 {len(ids)} 条用例把它置空（例如用例 {ids[:5]}），"
                "默认路径会绑成 c_void_p 而不是 typed 空指针；"
                "现在 aclnn_api_type 仍是默认 "
                f"{DEFAULT_WIRING['aclnn_api_type']}")
    return report, problems


def main():
    parser = argparse.ArgumentParser(
        description="S2 出口：适配器该不该写、写没写")
    parser.add_argument("-j", "--case-json", required=True,
                        help="本分面的用例 JSON，执行器绑定以它为准")
    parser.add_argument("-a", "--alignment", required=True,
                        help="align_signatures.py 产出的 signature_alignment.json")
    parser.add_argument("-o", "--output", required=True,
                        help="判定报告落盘路径，如 evidence/adapter_binding.json")
    args = parser.parse_args()

    try:
        alignment = load_json(args.alignment)
        cases = list(iter_cases(load_json(args.case_json)))
    except (OSError, ValueError) as exc:
        print(f"读不出输入：{exc}", file=sys.stderr)
        return 3
    if not cases:
        print("用例集为空，无从判定适配器。", file=sys.stderr)
        return 3

    report, problems = judge(alignment, cases)
    report["case_json"] = args.case_json
    report["alignment"] = args.alignment
    report["problems"] = problems
    with open(args.output, "w", encoding="utf-8") as sink:
        json.dump(report, sink, ensure_ascii=False, indent=2)

    for key, verdict in report["verdicts"].items():
        state = "需要" if verdict["required"] else "不需要"
        print(f"{key}：适配器{state}，用例集实际绑 {verdict['bound']}")
    for review in report["reviews"]:
        if review["requires_manual_review"]:
            print(f"  ? {review['issue']}（脚本判不了，人工过目）")
        else:
            mark = "触发" if review["triggered"] else "已判定不触发"
            print(f"  - {review['parameter']}：{mark}"
                  f"（{review['triggered_case_count']} 条用例）")
    print(f"判定报告写入 {args.output}")

    if not problems:
        return 0
    print(f"\n✗ {len(problems)} 处适配器判定不通过：", file=sys.stderr)
    for index, message in enumerate(problems, 1):
        print(f"  {index}. {message}", file=sys.stderr)
    print("  → 适配器接线字段在用例 JSON 里，S2 冻结之后改它要重跑 atk case。\n"
          "     现在就写执行器并重新生成；已经冻结的用受控通道 "
          "rewire_adapter.py。", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_adapter_binding.py -q`
Expected: PASS，16 passed。

- [ ] **Step 5: 登记进骨架并加进 S2 门禁**

`references/artifact-contracts.json` 的 S2 门禁改成：

```json
      "gates": ["签名", "结构", "覆盖", "适配器"],
```

在 `"function_<op>.py"` 条目之后、`"冻结输入"` 之前插入：

```json
    "evidence/adapter_binding.json": {
      "stage": "S2",
      "owner": "script",
      "spec": "references/plugin-authoring.md#适配器判定在 S2 完成",
      "template": null,
      "producer": "check_adapter_binding.py",
      "consumed_by": ["S5 证据链"],
      "risk": "条件性预警不在 S2 落实，就要到 S3 冒烟才发现，而那时改接线得重跑 atk case",
      "fields_not_applicable": "门禁报告，字段由 check_adapter_binding.py 自身担保"
    },
```

- [ ] **Step 6: 写进 reference**

`references/plugin-authoring.md` 的「## 目录」里，在 `- 校验` 之前加一行 `- 适配器判定在 S2 完成`。

在「## ABI 与语义校验」小节之后，插入新小节：

```markdown
## 适配器判定在 S2 完成

`semantic_review` 给的是**条件性**预警：条件成不成立，签名里看不出来。

条件成不成立不用猜，用例集就在手上，跑这道门禁逐条核：

```bash
<python> scripts/check_adapter_binding.py -j <case-json> \
  -a evidence/signature_alignment.json -o evidence/adapter_binding.json
```

退出码 2 表示该写的适配器没写，或对齐报告 `determinable=false` 无法判定。

预警类型与判定方式：

| 预警类型 | 怎么在用例集里验证触发 | 触发了怎么办 |
| --- | --- | --- |
| 可空指针（`aclXxx*` 且非 `aclTensor`） | 该输入名有用例的 `range_values` 是 `null` 或 `default` | 写 typed null 执行器，把 `aclnn_api_type` 改成它的注册名 |
| aclnn 独有参数 | `baseline_adapter.required` 为 `true` | 写 `function_<op>.py` 把它 pop 掉，`api_type` 改成它的注册名 |
| 基线独有参数 | `aclnn_adapter.required` 为 `true` | 在 `init_by_input_data` 里丢掉它 |
| 多个 aten 重载 | 脚本判不了，报告里标 `requires_manual_review` | 拆接口分面 |
| 变长基线签名 | 脚本判不了，报告里标 `requires_manual_review` | 人工核对位置配对 |

执行器绑定以**用例 JSON** 为准，不是 YAML。

ATK 从 `case_config.api_type` / `aclnn_api_type` 取执行器，用例里没写就是内置默认。

所以改接线必然要重跑 `atk case`，这就是判定必须在冻结之前做完的原因。
```

- [ ] **Step 7: 重渲染派生视图与 S2 卡**

```bash
python3 scripts/render_views.py --write
python3 scripts/render_views.py --cards
```

把 S2 那一节原样替换回 `SKILL.md`。

- [ ] **Step 8: 跑全量测试**

Run: `python3 -m pytest tests/ -q`
Expected: PASS，`334 passed, 9 skipped`（本 Task 新增 16 条）。

确认 `tests/test_document_style.py::test_every_script_has_a_documentation_route` 通过——
新脚本必须在某份 reference 里被点名，Step 6 已经做到。

- [ ] **Step 9: 提交**

```bash
git add skill/repo-task-atk-test/scripts/check_adapter_binding.py \
        skill/repo-task-atk-test/references/artifact-contracts.json \
        skill/repo-task-atk-test/references/decision-points.md \
        skill/repo-task-atk-test/references/plugin-authoring.md \
        skill/repo-task-atk-test/SKILL.md \
        skill/repo-task-atk-test/tests/test_adapter_binding.py
git commit -m "feat: adjudicate conditional adapter warnings against the case set

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 7: B4（下）—— `rewire_adapter.py`，接线改写的唯一出口

运行期类型校验这类问题确实可能只在 S3 才暴露。规则冲突没有出口时，
agent 会自己发明一条：roll 那轮它手工自证了十次「用例语义没变」。

给一条受控通道，把那十次变成一条命令，同时保住冻结纪律的实质——
**用例语义未变，只换执行器接线。**

**Files:**
- Create: `skill/repo-task-atk-test/scripts/rewire_adapter.py`
- Modify: `skill/repo-task-atk-test/SKILL.md`
- Modify: `skill/repo-task-atk-test/references/plugin-authoring.md`
- Modify: `skill/repo-task-atk-test/tests/test_document_style.py`
- Test: `skill/repo-task-atk-test/tests/test_rewire_adapter.py`

**Interfaces:**
- Consumes：`_case_utils.iter_cases`、`_case_utils.load_json`、`_case_utils.file_sha256`
- Produces：
  - `rewire_adapter.WIRING_KEYS: frozenset` — `{"api_type", "aclnn_api_type"}`
  - `rewire_adapter.strip_wiring(case) -> dict` — 去掉接线键的用例副本
  - `rewire_adapter.diff_cases(old, new) -> list[str]` — 非接线差异的人话描述
  - `rewire_adapter.parse_set(items) -> dict[str, str]`
  - CLI `-f/--yaml`、`-j/--case-json`、`--atk-cli`、`--set K=V`（可重复）、
    `-p/--plugin`、`--frozen`、`-o/--output`；退出码 0 / 2 / 3

- [ ] **Step 1: 写失败测试**

创建 `tests/test_rewire_adapter.py`：

```python
"""接线字段的受控改写：用例语义未变才算数。

真机实测（roll，2026-08-15）：aclnn 适配器接线要改 YAML 重跑 atk case，
与「S2 后冻结」纪律冲突，skill 没给出口，agent 手工自证了十次。

这些用例锁的是判据本身——「只有接线字段变了」怎么判。
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import rewire_adapter as rewire  # noqa: E402

SCRIPT = SKILL_ROOT / "scripts" / "rewire_adapter.py"


def case(case_id, dtype="fp32", **wiring):
    return dict({"id": case_id,
                 "inputs": [{"name": "x", "type": "tensor", "dtype": dtype,
                             "shape": [4], "range_values": [-5, 5]}]},
                **wiring)


class ParseSetTest(unittest.TestCase):
    def test_parses_key_value(self):
        self.assertEqual({"aclnn_api_type": "my_exec"},
                         rewire.parse_set(["aclnn_api_type=my_exec"]))

    def test_rejects_non_wiring_keys(self):
        # generate 换了生成器就换了用例本身，那不是接线改写，是重新设计。
        with self.assertRaises(ValueError) as caught:
            rewire.parse_set(["generate=other"])
        self.assertIn("generate", str(caught.exception))

    def test_rejects_missing_value(self):
        with self.assertRaises(ValueError):
            rewire.parse_set(["aclnn_api_type"])


class DiffCasesTest(unittest.TestCase):
    def test_wiring_only_change_is_clean(self):
        old = [case(0), case(1)]
        new = [case(0, aclnn_api_type="my_exec"),
               case(1, aclnn_api_type="my_exec")]
        self.assertEqual([], rewire.diff_cases(old, new))

    def test_changed_dtype_is_reported(self):
        problems = rewire.diff_cases([case(0)], [case(0, dtype="fp16")])
        self.assertEqual(1, len(problems))
        self.assertIn("0", problems[0])

    def test_changed_case_count_is_reported(self):
        problems = rewire.diff_cases([case(0), case(1)], [case(0)])
        self.assertTrue(problems)
        self.assertIn("条数", problems[0])

    def test_changed_case_ids_are_reported(self):
        problems = rewire.diff_cases([case(0)], [case(9)])
        self.assertTrue(problems)

    def test_strip_wiring_does_not_mutate_the_input(self):
        original = case(0, aclnn_api_type="my_exec")
        rewire.strip_wiring(original)
        self.assertIn("aclnn_api_type", original)


class CliTest(unittest.TestCase):
    def test_non_wiring_key_exits_three(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "-f", "/nope.yaml",
             "-j", "/nope.json", "--atk-cli", "/nope",
             "--set", "generate=x", "-o", "/tmp/x.json"],
            capture_output=True, text=True)
        self.assertEqual(3, result.returncode)
        self.assertIn("generate", result.stderr)

    def test_missing_yaml_exits_three(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "-f", "/nope.yaml",
             "-j", "/nope.json", "--atk-cli", "/nope",
             "--set", "aclnn_api_type=x", "-o", "/tmp/x.json"],
            capture_output=True, text=True)
        self.assertEqual(3, result.returncode)


class DisclosureTest(unittest.TestCase):
    def test_skill_no_longer_disclaims_a_missing_script(self):
        # Plan B 写下这句话时脚本还不存在，附了免责说明。现在兑现了，
        # 免责说明留着就是假信息。
        text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("rewire_adapter.py", text)
        self.assertNotIn("脚本未产出前", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_rewire_adapter.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'rewire_adapter'`。

- [ ] **Step 3: 写脚本**

创建 `scripts/rewire_adapter.py`：

```python
"""接线字段的受控改写：用例语义未变，只换执行器。

「一次 atk case 生成后冻结」是硬纪律，但运行期类型校验这类问题确实可能
只在 S3 才暴露。规则冲突没有出口时 agent 会自己发明一条——真机实测里
它手工自证了十次「用例语义没变」。

本脚本把那十次变成一条命令，并且把判据钉死：
patch YAML 接线字段 → 重跑 atk case → 逐条比对新旧用例 →
除接线键外必须逐字段相同 → 重新绑定冻结输入 → 写留痕。

不满足即退出码 2，视为需要回 S2 重做，没有第二条通道。

注意一种真实的失败：ATK 的用例生成不是处处确定的（复合组长度是随机抽的，
见 L0 group_length）。这种算子重跑会得到不同的用例，本脚本会如实报告
差异并拒绝——那不是脚本过严，是「只换接线」这件事在该算子上不成立。

退出码：0 只有接线变了；2 用例语义变了或核对不过；3 输入/命令本身出错。
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

from _case_utils import file_sha256, iter_cases, load_json

# 只有这两个键是「接线」：换执行器不改变用例要测什么。
# generate 不在其中——换生成器就是换用例本身，那是重新设计，不是改接线。
WIRING_KEYS = frozenset({"api_type", "aclnn_api_type"})

SAVED_CASE = re.compile(r"save case json file:\s*(\S+)")
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def parse_set(items):
    """把 `--set k=v` 解析成字典，非接线键当场拒绝。"""
    patch = {}
    for item in items or []:
        key, sep, value = str(item).partition("=")
        if not sep or not value:
            raise ValueError(f"--set 要写成 k=v，收到 {item!r}")
        if key not in WIRING_KEYS:
            raise ValueError(
                f"{key!r} 不是接线字段。只有 {sorted(WIRING_KEYS)} 可以受控改写；"
                "改别的字段等于改用例本身，回 S2 重做")
        patch[key] = value
    if not patch:
        raise ValueError("没有给出任何 --set")
    return patch


def strip_wiring(case):
    """去掉接线键的用例副本，比对用。"""
    return {key: value for key, value in case.items() if key not in WIRING_KEYS}


def diff_cases(old, new):
    """返回非接线差异的人话描述，空列表表示只有接线变了。"""
    if len(old) != len(new):
        return [f"用例条数从 {len(old)} 变成 {len(new)}；"
                "这不是接线改写，用例集本身变了"]
    old_ids = [case.get("id") for case in old]
    new_ids = [case.get("id") for case in new]
    if old_ids != new_ids:
        return [f"用例号变了：{old_ids[:8]} → {new_ids[:8]}"]
    problems = []
    for before, after in zip(old, new):
        if strip_wiring(before) != strip_wiring(after):
            problems.append(
                f"用例 {before.get('id')} 除接线字段外还有变化；"
                "用例语义已变，冻结纪律要求回 S2 重做")
    return problems


def patch_yaml(path, patch):
    """就地改 YAML 的接线字段，原文件备份成 <path>.pre_rewire。"""
    import yaml

    with open(path, encoding="utf-8") as handle:
        design = yaml.safe_load(handle)
    before = {key: design.get(key) for key in patch}
    design.update(patch)
    shutil.copyfile(path, f"{path}.pre_rewire")
    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(design, handle, allow_unicode=True, sort_keys=False,
                       width=100)
    return before


def regenerate(atk_cli, yaml_path, plugin, timeout):
    """重跑 atk case，返回新用例 JSON 的路径。"""
    command = [atk_cli, "case", "-f", os.path.abspath(yaml_path)]
    if plugin:
        command += ["-p", os.path.abspath(plugin)]
    print("重新生成：" + " ".join(command))
    done = subprocess.run(command, capture_output=True, text=True,
                          timeout=timeout)
    log = ANSI.sub("", done.stdout + done.stderr)
    found = SAVED_CASE.findall(log)
    if not found:
        raise RuntimeError(
            f"日志里没有 save case json file:（退出码 {done.returncode}）；"
            "产物路径只能从日志取，不要自己拼")
    return found[-1], log


def main():
    parser = argparse.ArgumentParser(
        description="接线字段的受控改写：用例语义未变才放行")
    parser.add_argument("-f", "--yaml", required=True, help="本分面的设计 YAML")
    parser.add_argument("-j", "--case-json", required=True,
                        help="已冻结的用例 JSON，比对基准")
    parser.add_argument("--atk-cli", required=True,
                        help="probe_env.py 探出的绝对路径")
    parser.add_argument("--set", action="append", dest="sets",
                        help=f"接线字段改写，k=v，只接受 {sorted(WIRING_KEYS)}")
    parser.add_argument("-p", "--plugin", help="生成器插件路径，与首次生成保持一致")
    parser.add_argument("--frozen",
                        help="evidence/frozen_inputs.json；给了就重新绑定到新用例集")
    parser.add_argument("-o", "--output", required=True,
                        help="留痕落盘路径，如 evidence/rewire.json")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()

    try:
        patch = parse_set(args.sets)
    except ValueError as exc:
        print(f"参数不合法：{exc}", file=sys.stderr)
        return 3
    if not os.path.exists(args.yaml) or not os.path.exists(args.case_json):
        print(f"找不到 {args.yaml} 或 {args.case_json}", file=sys.stderr)
        return 3

    old_cases = list(iter_cases(load_json(args.case_json)))
    old_sha = file_sha256(args.case_json)

    try:
        before = patch_yaml(args.yaml, patch)
        new_path, _ = regenerate(args.atk_cli, args.yaml, args.plugin,
                                 args.timeout)
    except Exception as exc:  # noqa: BLE001 命令与解析失败都归 3
        print(f"重新生成失败：{exc}", file=sys.stderr)
        print(f"  → YAML 原文已备份在 {args.yaml}.pre_rewire", file=sys.stderr)
        return 3

    new_cases = list(iter_cases(load_json(new_path)))
    problems = diff_cases(old_cases, new_cases)

    record = {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "yaml": os.path.abspath(args.yaml),
        "patched": {key: {"from": before.get(key), "to": value}
                    for key, value in patch.items()},
        "old_case_json": os.path.abspath(args.case_json),
        "old_case_json_sha256": old_sha,
        "new_case_json": os.path.abspath(new_path),
        "new_case_json_sha256": file_sha256(new_path),
        "total_cases": len(new_cases),
        "problems": problems,
    }

    if not problems and args.frozen:
        # 冻结摘要用 case_json_sha256 绑死用例集。用例语义未变时把绑定
        # 挪到新文件上，并留下旧摘要——冻结的实质是「输入没重算」，
        # 而输入本来就还是那一份。
        frozen = load_json(args.frozen)
        frozen["previous_case_json_sha256"] = frozen.get("case_json_sha256")
        frozen["case_json"] = os.path.abspath(new_path)
        frozen["case_json_sha256"] = record["new_case_json_sha256"]
        frozen["rewired_at"] = record["at"]
        with open(args.frozen, "w", encoding="utf-8") as sink:
            json.dump(frozen, sink, ensure_ascii=False, indent=2)
        record["frozen_rebound"] = os.path.abspath(args.frozen)

    with open(args.output, "w", encoding="utf-8") as sink:
        json.dump(record, sink, ensure_ascii=False, indent=2)

    for key, change in record["patched"].items():
        print(f"{key}：{change['from']} → {change['to']}")
    print(f"新用例集：{new_path}（{len(new_cases)} 条）")
    print(f"留痕写入 {args.output}")

    if not problems:
        print("核对通过：除接线字段外，逐条用例逐字段相同。")
        return 0

    print(f"\n✗ {len(problems)} 处非接线差异：", file=sys.stderr)
    for index, message in enumerate(problems, 1):
        print(f"  {index}. {message}", file=sys.stderr)
    print("  → 用例语义变了就不属于接线改写，回 S2 重做整轮，"
          "不要拿这份新用例集继续跑。", file=sys.stderr)
    print(f"  → YAML 原文备份在 {args.yaml}.pre_rewire。", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_rewire_adapter.py -q`
Expected: 11 条里 10 passed、1 failed——`DisclosureTest` 仍红，因为 SKILL.md 还没改。

- [ ] **Step 5: 兑现 SKILL.md 的承诺**

`SKILL.md:35` 那一行改成：

```markdown
接线字段的受控改写走 `rewire_adapter.py`，它只在用例语义未变时放行。
```

- [ ] **Step 6: 更新 `test_document_style.py` 里那条已过期的注释**

`tests/test_document_style.py::test_documented_script_names_exist` 的注释开头一段
改成（断言本体不动）：

```python
        # 这条的由来：SKILL.md 一度写「接线字段的受控改写走 `rewire_adapter.py`」，
        # 而那个脚本当时全仓库不存在（句子里已附了就近披露，所以不算 bug）。
        # 它暴露的是 test_declared_producer_exists 覆盖不到的洞：正文里随手提
        # 一句尚不存在的脚本名，没有任何东西会红。脚本现已产出，这条继续守着
        # 「未附披露的脚本名必须真实存在」。
```

- [ ] **Step 7: 写进 reference**

`references/plugin-authoring.md` 的「## 适配器判定在 S2 完成」小节末尾追加：

```markdown
### S3 才暴露时的唯一出口

运行期类型校验这类问题确实可能只在 S3 冒烟才暴露。

那时用例已冻结，改接线要重跑 `atk case`，与冻结纪律冲突。

唯一的受控通道是：

```bash
<python> scripts/rewire_adapter.py -f <yaml> -j <case-json> \
  --atk-cli "$ATK_CLI" --set aclnn_api_type=<注册名> \
  -p <生成器插件> --frozen evidence/frozen_inputs.json -o evidence/rewire.json
```

它 patch 接线字段、重跑生成、逐条用例逐字段比对，只有接线变了才放行。

退出码 2 表示用例语义也变了，那不属于接线改写，回 S2 重做整轮。

复合组长度是 ATK 随机抽的，这类算子重跑必然得到不同用例，脚本会如实拒绝。
```

- [ ] **Step 8: 跑全量测试**

Run: `python3 -m pytest tests/ -q`
Expected: PASS，`345 passed, 9 skipped`（本 Task 新增 11 条）。

- [ ] **Step 9: 提交**

```bash
git add skill/repo-task-atk-test/scripts/rewire_adapter.py \
        skill/repo-task-atk-test/references/plugin-authoring.md \
        skill/repo-task-atk-test/SKILL.md \
        skill/repo-task-atk-test/tests/test_document_style.py \
        skill/repo-task-atk-test/tests/test_rewire_adapter.py
git commit -m "feat: give wiring changes a controlled exit that proves semantics held

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 8: B8 —— 物化脚本通用化

`size_class × rank → shape` 与算子无关，是所有算子共用的一段推导，
CLAUDE.md §10.6 把它列为「未落地」，代价是每个算子手写一份、每轮重犯同样的错。

抽出来的只有真正与算子无关的部分：规模档摊到几根轴、非对齐尾块怎么造、
轴位置名怎么转轴号。`shape_form` 的取值由设计者自己定（`case-design.md` §轴取值），
脚本不认这些名字，所以不做字符串分派——由物化脚本把自己的取值映射成这里的参数。

**Files:**
- Create: `skill/repo-task-atk-test/scripts/_shapes.py`
- Modify: `skill/repo-task-atk-test/assets/example/materialize.py`
- Modify: `skill/repo-task-atk-test/assets/example/README.md`
- Modify: `skill/repo-task-atk-test/tests/test_assets_example.py`
- Modify: `skill/repo-task-atk-test/references/case-design.md`
- Test: `skill/repo-task-atk-test/tests/test_shapes.py`

**Interfaces:**
- Consumes：`ATK_SKILL_DIR`（Task 2 的 `evidence/env.sh` 导出）
- Produces：
  - `_shapes.SIZE_NUMEL: dict[str, int]` — `{"small": 2**10, "medium": 2**16, "large": 2**20}`
  - `_shapes.MAX_DIM: int` — `2**20`
  - `_shapes.shape_for(rank, size_class, index=0, numel=None, ragged=True) -> list[int]`
  - `_shapes.axis_for(rank, position) -> int` — `position` 取 `"first"` / `"middle"` / `"last"`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_shapes.py`：

```python
"""规模档 × rank → shape：与算子无关的那一段，只写一份。

CLAUDE.md §10.6 把它列为未落地，代价是每个算子手写一份物化脚本，
同一段推导重写一遍、同样的错重犯一遍，已经赔付过两次调用成本。
"""

import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import _shapes  # noqa: E402


class ShapeForTest(unittest.TestCase):
    def test_rank_decides_how_many_axes(self):
        for rank in (1, 2, 3, 4):
            with self.subTest(rank=rank):
                self.assertEqual(rank,
                                 len(_shapes.shape_for(rank, "medium")))

    def test_bigger_band_means_more_elements(self):
        import math
        sizes = [math.prod(_shapes.shape_for(3, band))
                 for band in ("small", "medium", "large")]
        self.assertEqual(sizes, sorted(sizes))

    def test_ragged_axis_rotates_with_the_index(self):
        # 同一档里的不同 combo 不该都是同一个形状，否则 dim_values
        # 只有一两个代表值，非对齐尾块也只在固定那根轴上出现。
        shapes = {tuple(_shapes.shape_for(3, "medium", index))
                  for index in range(3)}
        self.assertEqual(3, len(shapes))

    def test_ragged_can_be_turned_off(self):
        shape = _shapes.shape_for(3, "medium", 1, ragged=False)
        self.assertEqual(1, len(set(shape)))

    def test_no_axis_exceeds_the_single_dim_cap(self):
        for shape in (_shapes.shape_for(1, "large"),
                      _shapes.shape_for(2, "large")):
            with self.subTest(shape=shape):
                self.assertTrue(all(size <= _shapes.MAX_DIM for size in shape))

    def test_no_axis_collapses_to_zero(self):
        # 0 是空张量，属于 shape_form，不该由规模档意外造出来。
        for rank in (1, 2, 3, 4, 5):
            with self.subTest(rank=rank):
                self.assertTrue(all(size >= 1
                                    for size in _shapes.shape_for(rank, "small")))

    def test_custom_numel_overrides_the_default_ladder(self):
        shape = _shapes.shape_for(1, "small", numel={"small": 8})
        self.assertEqual([7], shape)

    def test_unknown_band_is_named_in_the_error(self):
        with self.assertRaises(KeyError):
            _shapes.shape_for(2, "huge")


class AxisForTest(unittest.TestCase):
    def test_positions(self):
        self.assertEqual(0, _shapes.axis_for(3, "first"))
        self.assertEqual(1, _shapes.axis_for(3, "middle"))
        self.assertEqual(2, _shapes.axis_for(3, "last"))

    def test_unknown_position_is_rejected_with_the_allowed_set(self):
        with self.assertRaises(ValueError) as caught:
            _shapes.axis_for(3, "somewhere")
        self.assertIn("first", str(caught.exception))


class TemplateUsesTheSharedModuleTest(unittest.TestCase):
    def test_materialize_template_imports_the_shared_shapes(self):
        text = (SKILL_ROOT / "assets" / "example" / "materialize.py") \
            .read_text(encoding="utf-8")
        self.assertIn("from _shapes import", text)
        self.assertIn("ATK_SKILL_DIR", text)

    def test_template_does_not_keep_its_own_copy_of_the_ladder(self):
        # 留一份副本就是留一处会漂移的知识。
        text = (SKILL_ROOT / "assets" / "example" / "materialize.py") \
            .read_text(encoding="utf-8")
        self.assertNotIn("def shape_for(", text)
        self.assertNotIn("def dim_for(", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_shapes.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named '_shapes'`。

- [ ] **Step 3: 写共享模块**

创建 `scripts/_shapes.py`：

```python
"""规模档 × rank → 具体 shape。与算子无关，所有算子共用一份。

物化脚本每个算子写一份，但这段推导里没有一个字是算子专属的：
规模档定总元素量级，rank 定摊到几根轴，非对齐尾块靠让某一根轴取 2^n-1。
CLAUDE.md §10.6 把它列为「未落地」，代价是每轮重写一遍并重犯同样的错。

不做 shape_form 的字符串分派：它的取值由设计者自己定
（`references/case-design.md` 的「轴取值与门禁判据」），脚本不认这些名字。
物化脚本把自己的取值映射成这里的参数，映射关系留在算子那一侧。

本模块不是 CLI。
"""

# 规模档只用与实现无关的数量级阶梯（2^n），不按被测实现的 tiling 阈值挑。
SIZE_NUMEL = {"small": 2 ** 10, "medium": 2 ** 16, "large": 2 ** 20}

# 单维不超过 2^20（生态精度标准 §1.2）。
MAX_DIM = 2 ** 20


def shape_for(rank, size_class, index=0, numel=None, ragged=True):
    """把规模档摊到 rank 根轴上。

    `index` 让同一档里的不同 combo 不至于都是同一个形状：轮流让某一根轴
    取 2^n-1，既制造非对齐尾块，也让 dim_values 有足够多的代表值。

    `numel` 传自定义的档位→元素数映射，用于本机 golden 预算更紧的场合。
    `ragged=False` 产出各轴等长的规整形状。
    """
    total = (numel or SIZE_NUMEL)[size_class]
    per = max(1, round(total ** (1.0 / rank)))
    per = min(per, MAX_DIM)
    shape = [per] * rank
    if ragged:
        shape[index % rank] = max(1, per - 1)
    return shape


def axis_for(rank, position):
    """轴位置名 → 轴号。

    中间轴在 rank < 3 时与首尾重合，这种组合应当在 decl 的 infeasible 里
    排除掉，本函数不替设计做主，照算不报错。
    """
    table = {"first": 0, "middle": rank // 2, "last": rank - 1}
    if position not in table:
        raise ValueError(
            f"未知的轴位置 {position!r}，只支持 {sorted(table)}")
    return table[position]
```

- [ ] **Step 4: 模板改用共享模块**

改写 `assets/example/materialize.py` 的头部（docstring 到 `dim_for` 之间全部替换）：

```python
"""物化脚本模板：把 must_cover 的语义组合填成具体 shape 与 attr 取值。

`make_must_cover.py` 只产出语义轴的取值（dtype / rank / size_class / ...），
具体 shape 和 attr 取什么，需要按算子契约逐条填写——这一步就是本脚本。

规模档摊到各轴那段推导与算子无关，由 `scripts/_shapes.py` 提供，不要重写。

复制这份文件，改两处即可：
1. `fill()`      每条 combo 要补哪些键（本例是 shape 和 dim）
2. `TARGETED`    按 coverage_policy.targeted 补的边界用例

本机 golden 预算更紧时，给 `shape_for` 传 `numel=` 覆盖默认档位。

写完运行：
    source evidence/env.sh && <python> materialize.py <must_cover.json> <materialized.json>
再用 materialized.json 跑 make_yaml.py / check_coverage.py / validate_cases.py。
"""

import json
import os
import sys

# 量具目录由 evidence/env.sh 固化成 ATK_SKILL_DIR。压缩上下文后靠 find
# 现找 skill 目录，真机上跑到过前一天的陈旧副本。
SKILL_DIR = os.environ.get("ATK_SKILL_DIR")
if not SKILL_DIR:
    raise SystemExit(
        "没有 ATK_SKILL_DIR：先 source evidence/env.sh"
        "（由 probe_env.py --env-sh 产出）")
sys.path.insert(0, os.path.join(SKILL_DIR, "scripts"))

from _shapes import axis_for, shape_for  # noqa: E402
```

`fill()` 改成调共享函数：

```python
def fill(combo, index):
    """给一条 combo 补上 YAML 和覆盖核对需要的具体取值。

    补的键要和 decl.json 的 `extract` 与 `parameters.*.combo_key` 对得上，
    否则 make_yaml 会报「combos 里没有 <key>」，check_coverage 会命中 0 组。
    """
    rank = combo["rank"]
    combo["shape"] = shape_for(rank, combo["size_class"], index)
    combo["dim"] = axis_for(rank, combo["reduce_axis_pos"])
    return combo
```

删掉模板里原有的 `SIZE_NUMEL`、`MAX_DIM`、`shape_for`、`dim_for` 四段。

- [ ] **Step 5: 测试运行模板时带上 `ATK_SKILL_DIR`**

`tests/test_assets_example.py` 的 `run()` 改成：

```python
def run(*args):
    # 模板按 evidence/env.sh 的约定从 ATK_SKILL_DIR 找量具目录，
    # 测试里显式给上，顺便证明这条约定是可用的。
    env = dict(os.environ, ATK_SKILL_DIR=str(ROOT))
    return subprocess.run([sys.executable, *map(str, args)],
                          capture_output=True, text=True, env=env)
```

文件顶部补 `import os`。

- [ ] **Step 6: 更新模板 README 与 reference**

`assets/example/README.md` 的表格里，`materialize.py` 一行的「改哪里」改成：

```markdown
| `materialize.py` | `materialize_<op>.py` | `fill()`、`TARGETED`；规模阶梯用 `_shapes.py` |
```

在跑通顺序的代码块之前插入一行：

```markdown
先 `source evidence/env.sh`：模板从 `ATK_SKILL_DIR` 找量具目录。
```

`references/case-design.md` 的「## 规模档」小节末尾追加：

```markdown
规模档摊到各轴的算法与算子无关，用 `scripts/_shapes.py` 的 `shape_for` 和 `axis_for`。

每个算子重写一份，就是把同一份知识放到两处维护。
```

- [ ] **Step 7: 跑全量测试**

Run: `python3 -m pytest tests/ -q`
Expected: PASS，`357 passed, 9 skipped`（本 Task 新增 12 条）。

`tests/test_assets_example.py::test_design_chain_runs_end_to_end` 必须仍绿——
它证明模板换了实现之后设计链照样跑得通。

- [ ] **Step 8: 提交**

```bash
git add skill/repo-task-atk-test/scripts/_shapes.py \
        skill/repo-task-atk-test/assets/example/materialize.py \
        skill/repo-task-atk-test/assets/example/README.md \
        skill/repo-task-atk-test/references/case-design.md \
        skill/repo-task-atk-test/tests/test_assets_example.py \
        skill/repo-task-atk-test/tests/test_shapes.py
git commit -m "refactor: share the size-band to shape derivation across operators

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 9: B7 + B9 —— 补事实与探针扩展

B7 原列三条，其中一条已经不成立，如实记下不补：

| 事实 | 处置 |
| --- | --- |
| 执行期报告路径 | 补。生成期路径 skill 写了，执行期只写了「从日志取」，两者的目录布局没有并列过 |
| 能力报告 sha 绑死 YAML | **不补**。这条依赖来自 `make_manifest.py`，Plan A 已删除该脚本，机制不复存在 |
| 构建入口可能在上级仓 | 补。真机实测 README 指向上级仓的 `build.sh` |

B9 的 `chip` 已经以 `devices.selected_name` 存在，真正缺的是候选工程的来源版本：
复现包的环境指纹现在说不清「这次测的是哪一版代码」。

**Files:**
- Modify: `skill/repo-task-atk-test/references/atk-cli.md`
- Modify: `skill/repo-task-atk-test/references/build-deploy.md`
- Modify: `skill/repo-task-atk-test/scripts/probe_env.py`
- Modify: `skill/repo-task-atk-test/scripts/make_repro.py`
- Test: `skill/repo-task-atk-test/tests/test_env_sh.py`（追加类）
- Test: `skill/repo-task-atk-test/tests/test_document_style.py`（追加两条）

**Interfaces:**
- Produces：
  - `probe_env.project_provenance(path) -> dict | None` — `{path, commit, describe, dirty}`
  - CLI 新增 `--op-repo PATH`
  - `env.json` 新增 `operator_project` 与 `fingerprint` 两个块
- Consumes：`make_repro.build_readme` 改读 `env["fingerprint"]`（缺失时退回整个 env）

- [ ] **Step 1: 写失败测试**

在 `tests/test_env_sh.py` 末尾追加：

```python
class ProvenanceTest(unittest.TestCase):
    """候选工程的来源版本：复现包要说得清「这次测的是哪一版代码」。"""

    def test_none_path_returns_none(self):
        self.assertIsNone(probe_env.project_provenance(None))

    def test_missing_directory_is_recorded_not_raised(self):
        info = probe_env.project_provenance("/definitely/not/here")
        self.assertIn("error", info)

    def test_this_repository_resolves_to_a_commit(self):
        info = probe_env.project_provenance(str(SKILL_ROOT))
        if info.get("commit") is None:
            self.skipTest("本机没有 git，或不在工作区里")
        self.assertEqual(40, len(info["commit"]))
        self.assertIn("dirty", info)
```

在 `tests/test_document_style.py::DocumentStyleTest` 里追加两条：

```python
    def test_runtime_report_path_is_documented(self):
        # 生成期与执行期产物在两个地方。skill 只写了生成期那个，
        # 真机上 agent 为找执行期报告花了约 8 次调用。
        text = (REFERENCES / "atk-cli.md").read_text(encoding="utf-8")
        self.assertIn("atk_output/", text)
        self.assertIn("report/", text)
        self.assertIn("save result excel file:", text)

    def test_build_entry_may_live_in_the_parent_repository(self):
        # 真机实测：README 指向上级仓的 build.sh，agent 在工程目录里找了 6 次。
        text = (REFERENCES / "build-deploy.md").read_text(encoding="utf-8")
        self.assertIn("上级仓", text)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_env_sh.py tests/test_document_style.py -q`
Expected: FAIL，三条新用例全红
（`AttributeError: ... 'project_provenance'` 与两条 `AssertionError`）。

- [ ] **Step 3: 补执行期报告路径**

`references/atk-cli.md` 的「## 报告定位」小节整段替换为：

```markdown
## 报告定位

生成期与执行期的产物在两个不同的地方，不要互相套用：

| 阶段 | 位置 |
| --- | --- |
| `atk case` | 当前工作目录下 `result/<yaml 文件名>/json/` |
| `atk node … task` | 当前工作目录下 `atk_output/<任务目录>/report/` |

任务目录名由 ATK 按用例名加时间戳生成，不要自己拼。

不要扫描旧的 `atk_output`。

读取本次日志最后一条 `save result excel file:`，确认路径存在。
```

- [ ] **Step 4: 补构建入口事实**

`references/build-deploy.md` 的「## 构建」小节，把

```markdown
只读取任务书、README 和 `build.sh --help`。
```

改成：

```markdown
只读取任务书、README 和 `build.sh --help`。

构建入口不一定在算子工程目录里。

README 指向上级仓的构建脚本时按 README 走，不要在工程目录里另找一个。
```

- [ ] **Step 5: 探针补来源版本与指纹块**

在 `scripts/probe_env.py` 的 `cann_info()` 之后插入：

```python
def project_provenance(path):
    """候选算子工程的来源版本。

    复现包的环境指纹现在说得出 CANN 版本、ATK 版本、芯片型号，
    唯独说不出「这次测的是哪一版代码」——而那正是开发者复现时最先要问的。
    """
    if not path:
        return None
    root = os.path.abspath(os.path.expanduser(path))
    info = {"path": root}
    if not os.path.isdir(root):
        info["error"] = "目录不存在"
        return info

    def git(*args):
        out = run(["git", "-C", root, *args], timeout=30)
        return None if out.startswith("<") or "fatal" in out.lower() else out

    info["commit"] = git("rev-parse", "HEAD")
    info["describe"] = git("describe", "--always", "--dirty")
    status = git("status", "--porcelain")
    info["dirty"] = bool(status) if status is not None else None
    if info["commit"] is None:
        info["error"] = "不是 git 工作区，或本机没有可用的 git"
    return info
```

在 `main()` 的参数区补：

```python
    parser.add_argument("--op-repo",
                        help="候选算子工程目录，记录其 git 版本供复现包引用")
```

在组装 `env` 之后（`env["phase_supported"] = phase` 之前）插入：

```python
    env["operator_project"] = project_provenance(args.op_repo)
    # 复现包只需要这几行。整份 env.json 里绝大多数是探测过程，
    # 交付给开发者时要的是「这一轮是在什么上面跑的」。
    env["fingerprint"] = {
        "chip": devices.get("selected_name"),
        "build_soc": devices.get("build_soc"),
        "cann": env["cann"].get("version"),
        "atk": (interps.get(selected_python) or {}).get("atk"),
        "python": selected_python,
        "operator_commit": (env["operator_project"] or {}).get("commit"),
        "operator_dirty": (env["operator_project"] or {}).get("dirty"),
    }
```

- [ ] **Step 6: 复现包优先读指纹块**

`scripts/make_repro.py` 的 `build_readme` 里，把

```python
    env = verdict.get("env") or {}
```

改成：

```python
    # probe_env.py 产出的 fingerprint 是给人看的那几行；整份 env.json
    # 大半是探测过程，原样铺进 README 会把真正要看的淹掉。
    full_env = verdict.get("env") or {}
    env = full_env.get("fingerprint") or full_env
```

- [ ] **Step 7: 跑全量测试**

Run: `python3 -m pytest tests/ -q`
Expected: PASS，`362 passed, 9 skipped`（本 Task 新增 5 条；本机没有 git 工作区时 skip 数为 10）。

- [ ] **Step 8: 提交**

```bash
git add skill/repo-task-atk-test/references/atk-cli.md \
        skill/repo-task-atk-test/references/build-deploy.md \
        skill/repo-task-atk-test/scripts/probe_env.py \
        skill/repo-task-atk-test/scripts/make_repro.py \
        skill/repo-task-atk-test/tests/test_env_sh.py \
        skill/repo-task-atk-test/tests/test_document_style.py
git commit -m "feat: record where reports land and which code was under test

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## 跨 Task 整体评审

九个 Task 全部完成并逐个评审通过之后，做一次跨 Task 的整体走查。
下面每一条都要给出证据，不接受「看起来没问题」。

- [ ] **1. 全量测试与体量核账**

```bash
python3 -m pytest tests/ -q
wc -l SKILL.md references/*.md
ls scripts/*.py | wc -l
```

记下三个数并写进收尾提交说明：`SKILL.md` 行数（硬顶 360）、
各 reference 行数（新增 ≤ 250，硬顶 300）、`scripts/` 个数。
净增要讲清换来了什么。

- [ ] **2. 四条不变量的变异验证**

Plan B 的四条锁加上本计划新增的量具引用锁，逐条做一次「改坏它必须红」：

| 变异 | 期望转红的用例 |
| --- | --- |
| 骨架里给某产物加一个 `fields` 键，量具不认 | `GaugeReflectionTest` |
| 从 SKILL.md 的某张卡里删掉一行产物 | `CardCoverageTest` |
| 改 L0 的 `group_length.follows_combo_order` 为 `true` | `TemplateFactRefTest` |
| 把某个 `owner: agent` 字段的 `spec` 锚点改成不存在的小节 | `SpecAnchorTest` |
| 把某个 `consumed_by` 改成不存在的脚本名 | `test_every_referenced_script_exists` |

每条改完跑 `python3 -m pytest tests/ -q` 确认真的红，再改回来。
只证明「改完还是绿」的锁等于没有锁。

- [ ] **3. 泛化红线四检查**

对本轮新增的每一个判据（`check_baseline_binding`、`check_contracts`、
`check_axis_values`、`check_adapter_binding.judge`、`diff_cases`）逐个问：

- 无实例痕迹：搜一遍新增文件，不得出现算子名、具体 shape、dtype 清单、专属阈值
- 判据来自事实：不得存在「agent 在某个 JSON 里写一行就放行」的通路
- 不削弱门禁：新增豁免要说得出它为什么不会让别的算子蒙混过关
- 反例可写：说得出「什么情况下这条判据应该不生效」

```bash
grep -rniE "median|bernoulli|remainder|huber|roll" scripts/ assets/ references/ | grep -v "\.pyc"
```

`roll` 会命中一些正当的散文引用（真机实测出处），逐条确认它们只出现在注释与
reference 的证据说明里，不在任何判据的代码路径上。

- [ ] **4. 冷启动核验（CLAUDE.md §1 的机械核验）**

不靠回忆，逐条对照：

- 本计划里出现的每条命令，参数与脚本 `argparse` 的真实定义逐个比对
- 每样新产物（`evidence/env.sh`、`evidence/adapter_binding.json`、
  `evidence/rewire.json`）都在骨架里登记，且有格式说明或明确的 `fields_not_applicable`
- 新脚本 `check_adapter_binding.py`、`rewire_adapter.py` 都能从某份 reference 走到

```bash
python3 -m pytest tests/test_document_style.py -q
python3 scripts/render_views.py --check
```

- [ ] **5. 证据分级复查**

把本轮写进 skill 的断言列出来，逐条标级别。
A 级（实跑）要说得出跑的是什么命令，B 级（源码）要有 `文件:行号`。
凡是既给不出命令也给不出行号的，从 skill 里删掉，改写进
`evidence/knowledge_gaps.json` 的下一轮清单——Plan B 收尾时正是在这条上踩过一次。

本计划已知的两处需要复查：

- `evidence/env.sh` 没有写 `TORCH_DEVICE_BACKEND_AUTOLOAD=0`。设计文档 §4.7
  把它列为「本机实测必需项」，但全仓库找不到任何证据，属 D 级，本轮不写。
  确认最终实现里确实没有它。
- B7 的「能力报告 sha 绑死 YAML」随 `make_manifest.py` 一起消失，Task 9 明确不补。
  确认没有人「顺手」把它加回来。

- [ ] **6. 收尾提交**

```bash
git add skill/repo-task-atk-test
git commit -m "docs: close the loop after defect closure

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

注意 `git add` 只加 skill 目录。工作区里另外约 37 个与 Plan A/B/C 无关的
旧文件改动不属于本轮，不要带进来。

- [ ] **7. 回流准备（真机跑测之后才做）**

本计划不含真机跑测。下一轮真机跑测收尾时按 CLAUDE.md §3.4 执行：

1. 拉回远端 `~/.claude/skills/repo-task-atk-test/`，逐文件 md5 比对
2. 差异逐条判定：回流 / 丢弃 / 已被更好实现取代
3. 每条回流补一个 `tests/` 回归用例

并按设计文档 §8 的第四条判据核账：`knowledge_gaps.json` 的条目数与读 ATK
源码的次数，对照 roll 那轮的 34 次。前三条判据全绿不能替代这一条。
