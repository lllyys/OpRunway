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
import _signature  # noqa: E402
import _taskdoc_parser as parser  # noqa: E402

SKILL_ROOT = Path(__file__).resolve().parents[1]
SPINE = SKILL_ROOT / "references" / "taskdoc-elements.json"
ALL_LAYERS = ["L0", "L1", "L2", "L3", "L4"]


def build_context(doc, decisions, spine):
    """条件必填的条件在这里求值，判据本身不判条件。

    signature_differs_from_baseline 是文档属性（spec §5.4：§2.3 参数序列
    与标杆接口不一致），不读 decisions.json——那是 3.5.param_mapping 自己
    要求人拍板记录的地方，用它的记录去判断它自己要不要变必填是自我指涉，
    agent 删掉那条记录或把 human_reply 清空，条件就会跟着消失，永不触发。
    """
    signals = json.loads(
        (SKILL_ROOT / "references" / "random-operator-signals.json").read_text(
            encoding="utf-8"))["signal_keywords"]
    text = doc.path.read_text(encoding="utf-8")
    baseline_section = doc.sections.get("2.1")
    api_section = doc.sections.get("2.3")
    baseline_params = _signature.parameter_names(
        "\n".join(baseline_section.code_blocks)) if baseline_section else []
    api_params = _signature.parameter_names(
        "\n".join(api_section.code_blocks)) if api_section else []
    return {
        "random_signal_hit": any(word in text for word in signals),
        "signature_differs_from_baseline": _signature.differs_from_baseline(
            baseline_params, api_params),
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

    # acceptance_map 与它的「待确认」判红必须在打印「通过」之前算完：
    # 挪到打印之后会先吐出一行「通过 · 层结果 {...}」，再判红 return 2，
    # 贴「完整输出」的人会贴出一份开头写着「通过」的失败记录。
    built = None
    if args.evidence_dir:
        evidence = Path(args.evidence_dir)
        evidence.mkdir(parents=True, exist_ok=True)
        import _acceptance_map
        built = _acceptance_map.build(doc, spine, context)
        (evidence / "acceptance_map.json").write_text(
            json.dumps(built, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")

    pending = [k for k, v in built.items() if v == "待确认"] if built else []
    if pending:
        print(f"§L4 验收族：约束表仍有待确认项 {pending}")
        return 2
    print(f"通过 · 层结果 {results} · sha256 {digest[:16]}")

    if args.evidence_dir:
        # 封条只在覆盖全部五层时才写：--layers 挑着跑漏掉的层里可能
        # 藏着真判红（如 L2 的模糊词），子集全绿不等于文档合格，
        # 不能让 agent 拿一枚只跑过部分层的真封条去冒充全量通过。
        if set(layers) == set(ALL_LAYERS):
            (evidence / "gate_pass.json").write_text(json.dumps({
                "doc": str(doc_path),
                "sha256": digest,
                "layers": results,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        else:
            print(f"封条未写：只跑了 {layers}，五层全过才写 gate_pass.json · "
                  "红线 C 不允许子集通过冒充全量通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
