"""P4:从 steps.json + doc_meta.json 渲染结论报告 REPORT.md(纯 stdlib,无第三方依赖)。

报告四块:总评 / 逐步台账 / 文档缺陷清单(带修订建议)/ 卡点详情。
overall 由台账自动算:有 blocker(FAIL/DOC_AMBIGUOUS/DOC_MISSING)→ 卡在第一个;全 OK → 通过。
agent 可用 --conclusion / --rating 覆盖一句话结论与评级。

用法:
  <python> <skill>/scripts/render_report.py --repo R --out <CWD>/cann-ops-report/doccheck/R/quickstart/faithful/REPORT.md
    [--conclusion '...'] [--rating '...']
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _state  # noqa: E402


def _cell(s) -> str:
    return (str(s) if s is not None else "—").replace("|", "\\|").replace("\n", " ").strip() or "—"


def _overall(steps: list) -> dict:
    blockers = [s for s in steps if s.get("verdict") in _state.BLOCKER_VERDICTS]
    judged_ok = [s for s in steps if s.get("verdict") == "OK"]
    unjudged = [s for s in steps if s.get("verdict", "UNJUDGED") == "UNJUDGED"]
    first_blocker = min(blockers, key=lambda s: s.get("idx", 0)) if blockers else None
    if first_blocker:
        passable = False
        rating = "卡死跑不通"
    elif steps and not unjudged:
        passable = True
        rating = "可跑通(文档合格)"
    else:
        passable = None
        rating = "未判完 / 无可执行步骤"
    return {"passable": passable, "rating": rating, "first_blocker": first_blocker,
            "n_total": len(steps), "n_ok": len(judged_ok), "n_unjudged": len(unjudged),
            "n_blocker": len(blockers)}


def render(repo: str, conclusion: str = "", rating: str = "", kind: str = "faithful") -> str:
    explored = kind == "explored"
    steps = _state.load_steps(repo)
    meta = _state.load_meta(repo)
    ov = _overall(steps)
    fb = ov["first_blocker"]
    rating = rating or ov["rating"]
    n_inj = sum(1 for s in steps if s.get("injected_fix"))

    if ov["passable"] is True:
        verdict_line = ("✅ **补齐文档缺口后能跑通**" if explored else "✅ **能纯按文档跑通**")
        default_concl = ("把文档缺口逐个补上后,文档的 build→install→run 全流程走通。" if explored
                         else "开发者只照本文档操作即可跑起来,文档合格。")
    elif ov["passable"] is False:
        if explored:
            verdict_line = f"⏹ **止于第 {fb['idx']} 步**({fb.get('verdict')})"
            default_concl = (f"逐个补齐文档缺口续跑,最终止于第 {fb['idx']} 步(见终止详情)。"
                             "跑不通亦是正常结论——重在「文档共几个坑」与「止步是不是文档的锅」。")
        else:
            verdict_line = f"❌ **卡在第 {fb['idx']} 步**({fb.get('verdict')})——纯按文档无法继续"
            default_concl = f"开发者只照本文档操作会卡在第 {fb['idx']} 步,文档需修订后才能跑通。"
    else:
        verdict_line = "⏳ **未判完 / 无可执行步骤**"
        default_concl = "尚无足够已判定步骤给出结论(或该仓无快速入门文档)。"
    concl = conclusion or default_concl

    title = ("QuickStart 文档体检报告（探索趟·补坑续跑）" if explored
             else "QuickStart 文档体检报告（忠实趟·纯按文档）")
    skill_line = ("> skill:`cann-doc-quickstart-check`（探索趟）—— 对每个文档卡点**只注入本 skill 自己提出的修订建议**"
                  "（source / soc / 网络这类有据的最小修复,**非任意 workaround**)后续跑;碰到非文档原因(如 NPU 运行时错)即止,跑不通也收尾。"
                  if explored else
                  "> skill:`cann-doc-quickstart-check`（忠实趟）—— 忠实模拟开发者照文档操作,**只按文档做、不探索/不绕过**,卡住即停。")

    L = []
    L.append(f"# {title} — {repo}\n")
    L.append(f"> 文档:`{meta.get('doc', '(未记录)')}` · 生成:{datetime.now().isoformat(timespec='seconds')}")
    L.append(skill_line + "\n")

    L.append("## 一、总评\n")
    head = "补齐文档缺口后能否跑通" if explored else "能否纯按文档跑通"
    L.append(f"- **{head}**:{verdict_line}")
    L.append(f"- **文档质量评级**:{rating}")
    L.append(f"- **一句话结论**:{concl}")
    L.append(f"- 步骤:共 {ov['n_total']}，OK {ov['n_ok']}，止步 {ov['n_blocker']}，待判 {ov['n_unjudged']}"
             + (f"，注入修复 {n_inj} 处" if explored else ""))
    if meta.get("declared_prerequisites"):
        L.append(f"- 文档声明的前提(假设已满足):{'; '.join(meta['declared_prerequisites'])}")
    L.append("")

    L.append("## 二、逐步台账\n")
    if explored:
        L.append("| # | 文档原文 | 实际命令 | cwd | 退出码 | 判定 | 注入修复 | 日志 |")
        L.append("|---|---|---|---|---|---|---|---|")
    else:
        L.append("| # | 文档原文 | 实际命令 | cwd | 退出码 | 判定 | 日志 |")
        L.append("|---|---|---|---|---|---|---|")
    for s in steps:
        lp = s.get("log_path")
        logcell = f"`steps/{Path(lp).name}`" if lp else "—"
        base = (f"| {s.get('idx')} | {_cell(s.get('doc_quote'))} | `{_cell(s.get('command'))}` | "
                f"{_cell(s.get('cwd'))} | {_cell(s.get('exit_code'))} | {_cell(s.get('verdict'))} | ")
        if explored:
            base += f"{_cell(s.get('injected_fix') or '—')} | "
        L.append(base + f"{logcell} |")
    L.append("\n> 每步「日志」列即该步完整真实 stdout/stderr;失败步的输出摘录见下方缺陷清单。\n")

    defects = [s for s in steps if s.get("verdict") in _state.BLOCKER_VERDICTS or s.get("defect")]
    L.append("## 三、文档缺陷 + 已注入的修复（探索趟）\n" if explored
             else "## 三、文档缺陷清单(带修订建议)\n")
    if not defects:
        L.append("（未发现文档缺陷。）\n")
    for i, s in enumerate(defects, 1):
        L.append(f"### 缺陷 {i} — 第 {s.get('idx')} 步（{s.get('verdict')}）")
        L.append(f"- **文档原文**:{s.get('doc_quote') or '（无）'}")
        L.append(f"- **缺什么 / 错在哪**:{s.get('defect') or '（agent 未填——执行判定为非 OK）'}")
        L.append(f"- **修订建议**:{s.get('fix_suggestion') or '（agent 未填）'}")
        if explored and s.get("injected_fix"):
            ok = s.get("verdict") == "OK"
            L.append(f"- **本趟实际注入**:{s.get('injected_fix')} —— 应用后该步{'通过 ✅' if ok else '仍未通过'}")
        err = (s.get("stderr_excerpt") or s.get("stdout_excerpt") or "").strip()
        if err:
            L.append("- **真实报错(摘录)**:\n\n  ```\n  " + "\n  ".join(err.splitlines()[-12:]) + "\n  ```")
        L.append("")

    L.append("## 四、终止详情\n" if explored else "## 四、卡点详情\n")
    if fb:
        L.append(f"{'止于' if explored else '停在'}**第 {fb['idx']} 步**（{fb.get('verdict')}）:")
        L.append(f"- 文档原文:{fb.get('doc_quote') or '（无）'}")
        L.append(f"- 实际命令:`{fb.get('command')}`（cwd `{fb.get('cwd')}`,退出码 {fb.get('exit_code')}）")
        L.append(f"- {'为何止步(是否文档之过)' if explored else '为什么纯按文档无法继续'}:{fb.get('defect') or '（见缺陷清单）'}")
    else:
        L.append("（全流程走通,未止步。）" if explored else "（未卡住。）")
    L.append("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="渲染 cann-doc-quickstart-check 结论报告")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--conclusion", default="")
    ap.add_argument("--rating", default="")
    ap.add_argument("--kind", choices=["faithful", "explored"], default="faithful",
                    help="faithful=忠实趟(纯按文档,卡住即停);explored=探索趟(逐个补坑续跑)")
    args = ap.parse_args()
    md = render(args.repo, args.conclusion, args.rating, args.kind)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(f"[report] {args.repo} → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
