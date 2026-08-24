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
import re
from pathlib import Path

CONTRACTS_PATH = Path(__file__).resolve().parents[1] / "references" / \
    "artifact-contracts.json"

# 谁产出这样东西。agent 的那些就是它必须自己判断的决策全集。
OWNERS = frozenset({"agent", "script", "atk"})

# 共用一份骨架的两个运行侧。阶段归属只允许从这里取值。
SKILLS = frozenset({"case-gen", "acceptance"})

# scripts 表的归属域；shared 表示两侧都会调用或依赖。
SCRIPT_SKILLS = frozenset({"case-gen", "acceptance", "shared"})

# 一道检查点为什么存在，只有这四种答案。
#
# 分类不是给文档做目录用的，是为了看清「这么多检查点是不是都有必要」：
# 前三类每一条都由一次真实事故换来，删掉就退回「报告 100% 通过而什么都没验」；
# 只有 atk_pitfall 那类是纯知识——agent 读了就能一次写对，读不到就必踩，
# 所以它们最该前置进 reference，而不是留在量具里等着拦人。
GATE_PURPOSES = frozenset({
    # 防手抄出错：两份数据本来就对得上，agent 中间用眼睛搬了一次
    "transcription",
    # 防自己出题自己答：判据与被检对象同源时，断言恒过
    "self_referential",
    # 编码 ATK 的反直觉行为：拦的不是疏忽，是框架的坑
    "atk_pitfall",
    # 结构自洽：字段缺没缺、值在不在域里，无前提
    "structural",
})

# 前提挂在哪个键上。四次真机死锁挂在三个不同的键上，
# 笼统归成「算子类别」会导出错误的修法（给所有门加一个 operator_class 开关）。
PREMISE_KEYS = frozenset({
    "parameter_kind",   # 参数是 tensor 还是 attr/scalar，dtype 写在不同地方
    "baseline_kind",    # 有没有 torch 基线可以反射形参名
    "operator_class",   # 输出是否由输入算出
    "atk_version",      # 装机 ATK 与能力矩阵是否同版本
    "interface_mode",   # 第五种前提键：接口模式决定有没有 C 头文件可比
})

# 前提不成立时的处置。判别标准只有一句：
# 前提不成立时，这道门要拦的缺陷还可能不可能发生？
DISPOSITIONS = frozenset({
    "retarget",         # 还可能。判据没错，量错了对象 → 换量具，一条检查都不少
    "delegate",         # 还可能。这一侧判不了 → 明确交给另一道门，必须点名
    "not_applicable",   # 结构上不可能 → 才允许跳过，且必须落进证据
})

# S0 里会以退出码 2 拦下 agent 的量具。清单必须覆盖它们。
S0_BLOCKING_SCRIPTS = (
    "check_bundle.py",
)

# S2 里会以退出码 2 拦下 agent 的量具。清单必须覆盖它们，
# 否则 agent 还是只能被逐个拦下才知道有这道门。
S2_BLOCKING_SCRIPTS = (
    "make_must_cover.py",
    "make_yaml.py",
    "check_signature_contract.py",
    "check_adapter_binding.py",
    "check_coverage.py",
    "validate_cases.py",
    "freeze_inputs.py",
    "seal_bundle.py",
)


def gates_of(data, stage):
    """取某阶段的全部检查点，保持登记顺序。"""
    return {gate_id: spec
            for gate_id, spec in (data.get("gate_inventory") or {}).items()
            if spec["stage"] == stage}


def stages_of(data, skill):
    """按骨架键序返回属于某个运行侧的阶段。"""
    return [stage for stage, block in (data.get("stages") or {}).items()
            if block.get("skill") == skill]


def conditional_gates(data):
    """有前提的检查点全集，按登记顺序。

    无前提的那些（`holds_when` 为 null）不会因为换一类算子而误判，
    不需要出现在「换算子前先看这些」的视图里。
    """
    return [(gate_id, spec)
            for gate_id, spec in (data.get("gate_inventory") or {}).items()
            if spec["premise"]["holds_when"] is not None]


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
    headings, in_code = set(), False
    heading_line = re.compile(r"^#{1,6}\s")
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if heading_line.match(line):
            headings.add(line.lstrip("# ").strip())
    if heading not in headings:
        raise ContractError(f"规范锚点的小节不存在：{anchor}")
    return path


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


GATE_HEADER = """# 检查点清单

<!-- 本文件由 scripts/render_views.py 从 references/artifact-contracts.json 渲染，不要手改。 -->

会拦下你的检查点全集，按阶段和量具分组。

动手写产物之前先读这一份，不要等着被逐个拦下来才知道有这道门。

每道门登记四件事：检查什么、为什么存在、前提是什么、前提不成立时谁接手。

## 目录

- 为什么有这么多
- 前提不成立时怎么办
- 换一类算子时先看这几道
- 各阶段检查点

## 为什么有这么多

检查点分四类，前三类每一条都由一次真实事故换来：

| 类别 | 拦什么 | 删掉会怎样 |
| --- | --- | --- |
| `transcription` | 两份数据本来对得上，中间用眼睛搬了一次 | 契约漏一个入参，一路过到 S3 绑定才炸 |
| `self_referential` | 判据与被检对象同源，断言恒过 | 覆盖率次次 100%，而用例数每轮不一样 |
| `atk_pitfall` | ATK 的反直觉行为，不是你的疏忽 | 插件静静不生效，报告照出、数字全错 |
| `structural` | 字段缺没缺、值在不在域里 | 后面每一步都建在读不出来的产物上 |

`atk_pitfall` 那些和判断力无关，纯粹是知识：读了就能一次写对。

它们集中在 `references/atk-pitfalls.md`，写产物之前先过一遍。

## 前提不成立时怎么办

判据大多诞生于某一类算子，换一类来就可能不适用。

**跳过不是默认选项。** 判别标准只有一句：

> 前提不成立时，这道门要拦的缺陷还可能不可能发生？

| 处置 | 什么时候 | 要求 |
| --- | --- | --- |
| `retarget` | 还可能。判据没错，量错了对象 | 说清换成量什么 |
| `delegate` | 还可能。这一侧判不了 | 点名交给哪个量具，答不上来就不许跳过 |
| `not_applicable` | 结构上不可能发生 | 必须落进证据，跳过的事实要留痕 |

"""


def _prose(text, lead=""):
    """把一段说明拆成合规的正文行。

    行文约束是每行一个短观点：不超过 100 字符、最多一个句号
    （test_document_style）。骨架里的 note / why_it_exists 是完整段落，
    直接渲染必然违规——但压缩内容会丢事实，所以按句号拆行而不是删字。
    """
    raw = str(text).rstrip()
    chunks = [part.strip() + "。" for part in raw.split("。") if part.strip()]
    if chunks and not raw.endswith("。"):
        chunks[-1] = chunks[-1][:-1]

    # 一句里没有句号也可能过长（骨架的 note 常带长引用）。按顿号、分号、
    # 逗号顺次找断点，都找不到就原样留着——宁可一条超长也不切碎语义。
    pieces = []
    for chunk in chunks:
        while len(chunk) > 100:
            cut = max(chunk.rfind(mark, 0, 100) for mark in ("；", "——", "，"))
            if cut <= 0:
                break
            pieces.append(chunk[:cut + 1])
            chunk = chunk[cut + 1:].lstrip()
        pieces.append(chunk)

    lines, buffer = [], lead
    for piece in pieces:
        # 每行最多一个句号，所以 buffer 里已经有句号时不再续接。
        if buffer.strip() and ("。" in buffer or len(buffer) + len(piece) > 100):
            lines.append(buffer)
            buffer = piece
        else:
            buffer += piece
    if buffer.strip():
        lines.append(buffer)
    return lines


def render_gate_inventory(data):
    """把骨架里的检查点渲染成一览清单。

    真机耗时的一个来源是这些门散在 8 个脚本、5 份 reference 里，
    没有一处能一览。视图签入让 agent 直接读到，渲染让它不会变成
    第二份要手工同步的表。
    """
    gates = data.get("gate_inventory") or {}
    lines = [GATE_HEADER]

    conditional = dict(conditional_gates(data))
    if conditional:
        lines.append("## 换一类算子时先看这几道")
        lines.append("")
        lines.append("它们的判据有前提，前提挂在哪个键上决定了换算子时要重新确认什么。")
        lines.append("")
        lines.append("| 检查点 | 挂在 | 不成立时 |")
        lines.append("| --- | --- | --- |")
        for gate_id, spec in conditional.items():
            premise = spec["premise"]
            # 表格行不受正文行长约束（prose_lines 跳过表格），但过宽的表在终端里
            # 会折行错位。前提原文与处置细节留在下面各自的小节里，这里只给索引。
            lines.append("| `{}` | `{}` | `{}` |".format(
                gate_id, premise["bound_to"], premise["when_broken"]))
        lines.append("")

    by_stage = {}
    for gate_id, spec in gates.items():
        by_stage.setdefault(spec["stage"], []).append((gate_id, spec))

    for stage in sorted(by_stage):
        stage_name = data["stages"][stage]["name"]
        lines.append(f"## {stage} {stage_name}")
        lines.append("")
        for gate_id, spec in by_stage[stage]:
            lines.extend(render_gate(gate_id, spec))
    return "\n".join(lines).rstrip("\n") + "\n"


def render_gate(gate_id, spec):
    """渲染单道检查点，返回行列表。

    清单全文和 `gate_lookup.py` 的单条查询共用这一份渲染：
    分成两份写法，查到的和读到的就会慢慢不一样。
    """
    lines = [f"### `{gate_id}`", "",
             f"量具 `{spec['script']}`　类别 `{spec['purpose']}`", ""]
    for check in spec["checks"]:
        lines.append(f"- {check}")
    lines.append("")
    if spec.get("why_it_exists"):
        lines.extend(_prose(spec["why_it_exists"], "**为什么存在：** "))
        lines.append("")
    premise = spec["premise"]
    if premise["holds_when"] is None:
        lines.append("**前提：** 无，任何算子都适用。")
    else:
        lines.append(f"**前提：** {premise['holds_when']}"
                     f"（挂在 `{premise['bound_to']}`）")
        lines.append("")
        lines.append(f"**不成立时：** `{premise['when_broken']}`")
        for key, label in (("delegated_to", "交给"),
                           ("retargets_to", "换成量"),
                           ("evidence_key", "证据键")):
            if premise.get(key):
                lines.append(f"　{label} `{premise[key]}`")
    if premise.get("note"):
        lines.append("")
        lines.extend(_prose(premise["note"], "**注：** "))
    lines.append("")
    return lines


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
        # 条件产物：不是每轮都要写。真机上 roll 不需要 CPU golden，
        # 而卡把它列成无条件必需，agent 在 S5 花了七次调用去找一个
        # 本来就不该存在的文件。要不要写由一份已产出的判定文件说了算。
        if spec.get("condition"):
            lines.append(f"  条件产物：{spec['condition']}")
    lines.append("")
    if block["lookup_topics"]:
        topics = " / ".join(block["lookup_topics"])
        head = f"先查后写（`scripts/atk_lookup.py <主题>`）：{topics}"
        # 行文约束是每行一个短观点、不超过 100 字符（test_document_style）。
        # 主题一多就超，渲染出来的正文自己违规——换行写，不缩表。
        if len(head) > 100:
            lines.append("先查后写（`scripts/atk_lookup.py <主题>`）：")
            lines.append(f"  {topics}")
        else:
            lines.append(head)
    # 「有哪些门」必须随卡一起送到，不能靠 agent 想起来去查：
    # 清单全文降级成按需查之后，不送索引就等于回到「被门逐个拦下才知道」，
    # 而那正是这份清单当初存在的理由。索引约 250 token，全文 3802。
    # 检查什么、前提是什么留在 gate_lookup.py 里，按量具名查。
    gates = [(gate_id, spec) for gate_id, spec in
             (data.get("gate_inventory") or {}).items()
             if spec["stage"] == stage]
    if gates:
        lines.append("本阶段的检查点（详情 `scripts/gate_lookup.py <门名>`）：")
        for gate_id, spec in gates:
            premise = "，有前提" if spec["premise"]["holds_when"] else ""
            lines.append(f"  {gate_id}　{len(spec['checks'])} 条{premise}")
    lines.append(f"出口门禁：{' / '.join(block['gates'])}")
    lines.append(f"本阶段不做：{'；'.join(block['forbidden'])}")
    return "\n".join(lines) + "\n"


def card_artifacts_in_skill(text, stage):
    """从渲染好的卡里抽出某阶段列出的产物名。

    只认「- `名字`　」这一种行首形态，形态变了就抽不到，测试会红——
    这是刻意的：卡的格式是 agent 逐行照做的清单，必须稳定。
    """
    wanted = f"### {stage} "
    names, inside = [], False
    for line in text.splitlines():
        if line.startswith("#"):
            inside = line.startswith(wanted)
            continue
        if inside and line.startswith("- `"):
            names.append(line.split("`")[1])
    return names


# 期望值本身可能带方括号（`list[InputCaseConfig]`），所以值里允许一层配对的
# `[...]`，但不允许裸 `]`——否则同一行的下一个 `# [L0:...]` 会被一起吞掉。
L0_REF = re.compile(
    r"#\s*\[L0:([\w.]+)(?:=([^\[\]]*(?:\[[^\]]*\][^\[\]]*)*))?\]")


def l0_refs(text):
    """抽出源码注释里的 `# [L0:a.b.c]` / `# [L0:a.b.c=期望值]` 引用键。

    不带 `=值` 只锁键路径存在；带 `=值` 额外锁住数值本身——
    L0 的键改名或删除，两种写法都会红；L0 的值变了，
    只有带 `=值` 的引用会被锁 L3 标红（见 TemplateFactRefTest._resolve）。

    返回 `[(key, expected_value_or_None), ...]`。
    """
    return [(key, value or None) for key, value in L0_REF.findall(text)]
