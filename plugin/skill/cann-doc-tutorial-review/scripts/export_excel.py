"""P5:把某仓的体检 findings 导出为**仓库同名** Excel + 柱状图(统计)。

读 `cann-ops-report/doccheck/<repo>/tutorial/` 的 findings.json + doc_meta.json,过与 REPORT 相同的
自校验闸 / 跨轴去重 / 默认丢 minor(report_report._partition),再导出:
  <repo>.xlsx   —— openpyxl 在时:
      Sheet「问题明细」:第一行 7 列 = 仓库名 | 具体问题文件 | 问题大类 | 错误类型 |
        问题标题 | 详细问题原因 | 解决办法(错误类型用 7 类全称,见 _state.ERR_TYPE_LABEL)
      Sheet「统计」   :概览 + 按错误类型/按大类计数 + Excel 原生柱状图(错误类型分布)
  缺 openpyxl → 降级 <repo>.csv + <repo>-stats.csv + statistics.svg(纯 stdlib)。
  matplotlib 在时另存 statistics.png(便于直接贴进文档/会议)。

用法(在项目根跑):python3 <skill>/scripts/export_excel.py --repo <repo>
      python3 <skill>/scripts/export_excel.py --all             # 导出全部已有 report 的仓
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _state            # noqa: E402
import render_report     # noqa: E402

# 问题明细列(第一行,与用户约定一致,勿改顺序)
HEADERS = ["仓库名", "具体问题文件", "问题大类", "错误类型",
           "问题标题", "详细问题原因", "解决办法"]

_ROOT_CAUSE_ZH = {"copy_paste_not_updated": "复制粘贴未随源更新",
                  "version_or_contract_drift": "版本/契约漂移",
                  "template_placeholder_left": "模板占位符遗留",
                  "fabricated_template_tree": "目录树为示意、仓内不存在"}


def _one_line(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _defects(repo: str) -> list[dict]:
    """与 REPORT 同一准入口径:自校验闸 + 跨轴去重 + 默认丢 minor。返回排序后的缺陷。"""
    _meta, _valid, _needs_fix, quant, nonquant = render_report._partition(repo)
    defects = quant + nonquant

    def key(f):
        sub = render_report._design_sub(f)
        imp = _state.IMPACT_ORDER.get(f.get("impact"), 1)
        return (_state.GROUP_ORDER.index(_state.sub_group(sub)),
                _state.SUB_ORDER.index(sub), imp, f.get("idx", 0))
    return sorted(defects, key=key)


def _doc_cell(f: dict) -> str:
    """具体问题文件:doc 路径 + 能从 code_location 推出时的行号(便于定位)。"""
    doc = str(f.get("doc") or f.get("file") or "")
    base = doc.rsplit("/", 1)[-1]
    if doc.endswith((".md", ".mdx")):
        m = re.search(re.escape(base) + r":(\d+)", str(f.get("code_location") or ""))
        if m:
            doc = f"{doc} 行{m.group(1)}"
    return doc


def _reason_cell(f: dict) -> str:
    """详细问题原因 = 原文/证据或判例 + 完整说明(improvement),多行、可直接读。"""
    parts = []
    if f.get("quote"):
        parts.append(f"原文: {str(f['quote']).strip()}")
    if f.get("cls") == "quantifiable":
        if f.get("code_location"):
            parts.append(f"证据(代码位置): {str(f['code_location']).strip()}")
    else:
        if f.get("precedent"):
            parts.append(f"判例(读者会卡在哪): {_one_line(f['precedent'])}")
        if f.get("steelman"):
            parts.append(f"steelman(已打反论): {_one_line(f['steelman'])}")
        if f.get("external_evidence"):
            parts.append(f"外部反证: {_one_line(f['external_evidence'])}")
    rc = f.get("root_cause")
    if rc:
        parts.append(f"成因: {_ROOT_CAUSE_ZH.get(rc, rc)}")
    if f.get("open_question"):
        parts.append(f"未能确证: {_one_line(f['open_question'])}")
    if f.get("improvement"):
        parts.append(f"说明: {_one_line(f['improvement'])}")
    return "\n".join(parts)


def _row(repo: str, f: dict) -> list[str]:
    sub = render_report._design_sub(f)
    gc = _state.sub_group(sub)
    return [repo, _doc_cell(f), _state.GROUPS[gc][0], _state.err_type(sub),
            (f.get("prob") or _one_line(f.get("improvement"))),
            _reason_cell(f),
            (_one_line(f["fix"]) if f.get("fix") else _one_line(f.get("improvement")))]


def _stats(defects: list[dict]) -> dict:
    """统计:按错误类型(7)/按大类计数 + 阻断/误导/确定度 + 覆盖。"""
    by_sub = {lab: {"n": 0, "blocker": 0, "misleading": 0}
              for lab in _state.ERR_TYPE_LABEL.values()}
    by_group = {g[0]: {"n": 0, "blocker": 0} for g in _state.GROUPS.values()}
    files = set()
    for f in defects:
        sub = render_report._design_sub(f)
        lab, gc = _state.err_type(sub), _state.GROUPS[_state.sub_group(sub)][0]
        imp = f.get("impact")
        by_sub[lab]["n"] += 1
        if imp == "blocker":
            by_sub[lab]["blocker"] += 1
        elif imp == "misleading":
            by_sub[lab]["misleading"] += 1
        by_group[gc]["n"] += 1
        by_group[gc]["blocker"] += 1 if imp == "blocker" else 0
        files.add(_doc_cell(f).split(" 行")[0])
    return {"total": len(defects),
            "files": sorted(files),
            "sev": {"阻断": sum(1 for f in defects if f.get("impact") == "blocker"),
                    "误导": sum(1 for f in defects if f.get("impact") == "misleading")},
            "conf": {"确认": sum(1 for f in defects if render_report._confirmed(f)),
                     "疑似": sum(1 for f in defects if not render_report._confirmed(f))},
            "by_sub": by_sub, "by_group": by_group}


# ============================ 纯 stdlib 降级:CSV + SVG 柱状图 ============================

def write_csv_fallback(out_base: Path, repo: str, rows: list[list[str]], st: dict) -> list[Path]:
    csv_path = out_base.with_suffix(".csv")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(HEADERS)
        w.writerows(rows)
    stats_csv = out_base.with_name(out_base.stem + "-stats.csv")
    with stats_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["统计项", "值"])
        w.writerow(["问题总数", st["total"]])
        for k, v in st["sev"].items():
            w.writerow([f"严重度-{k}", v])
        for k, v in st["conf"].items():
            w.writerow([f"确定度-{k}", v])
        for lab, d in st["by_sub"].items():
            w.writerow([f"错误类型-{lab}", d["n"]])
    print(f"[export] {repo} 缺 openpyxl,降级: {csv_path} + {stats_csv}")
    return [csv_path, stats_csv]


def write_svg(path: Path, title: str, cats: list[str], vals: list[int]) -> None:
    """极简 SVG 柱状图(无第三方依赖)。"""
    n = max(len(cats), 1)
    W, H, pad_l, pad_b, pad_t = 980, 430, 320, 150, 64
    maxv = max(vals) if vals else 1
    xw = (W - pad_l - 40) / n
    bh = H - pad_b - pad_t - 20
    bars = []
    for i, (c, v) in enumerate(zip(cats, vals)):
        x = pad_l + i * xw + xw * 0.18
        h = bh * v / maxv if maxv else 0
        y = H - pad_b - h
        bars.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{xw * 0.64:.1f}" '
                    f'height="{max(h, 0):.1f}" fill="#4c78a8"><title>{c}: {v}</title></rect>')
        bars.append(f'<text x="{x + xw * 0.32:.1f}" y="{max(y - 6, pad_t):.1f}" font-size="13" '
                    f'text-anchor="middle" fill="#222">{v}</text>')
        bar_x = x + xw * 0.32
        bars.append(f'<text x="{bar_x:.1f}" y="{H - pad_b + 16:.1f}" font-size="12" fill="#333" '
                    f'transform="rotate(-30 {bar_x:.1f} {H - pad_b + 16:.1f})">{c}</text>')
    grid = "".join(
        f'<line x1="{pad_l}" y1="{H - pad_b - g * bh / 5:.1f}" x2="{W - 40}" '
        f'y2="{H - pad_b - g * bh / 5:.1f}" stroke="#e4e4e4"/>' for g in range(6))
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="sans-serif">'
           f'<text x="{pad_l}" y="{pad_t - 22}" font-size="16" font-weight="bold">{title}</text>'
           f'{grid}{"".join(bars)}</svg>')
    path.write_text(svg, encoding="utf-8")
    return [path]


# ============================ openpyxl 主路径(可选依赖) ============================

def write_xlsx(path: Path, repo: str, rows: list[list[str]], st: dict) -> None:
    import openpyxl  # noqa: PLC0415 —— 可选依赖,缺则由 export() 降级 csv
    from openpyxl.chart import BarChart, Reference          # noqa: PLC0415
    from openpyxl.chart.label import DataLabelList          # noqa: PLC0415
    from openpyxl.styles import Alignment, Font, PatternFill  # noqa: PLC0415
    from openpyxl.utils import get_column_letter            # noqa: PLC0415

    wb = openpyxl.Workbook()

    # ---- Sheet1 问题明细 ----
    ws = wb.active
    ws.title = "问题明细"
    ws.append(HEADERS)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="4C78A8")
        c.alignment = Alignment(vertical="center", horizontal="center")
    for r in rows:
        ws.append(r)
    for row in ws.iter_rows(min_row=2, max_col=len(HEADERS)):
        for c in row:
            c.alignment = Alignment(vertical="top", wrap_text=True)
    for i, w in enumerate([12, 46, 18, 24, 40, 72, 42], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"

    # ---- Sheet2 统计 + 柱状图 ----
    ws2 = wb.create_sheet("统计")
    ws2["A1"] = "概览"
    ws2["A1"].font = Font(bold=True)
    over = [("问题总数", st["total"]), ("覆盖文档数", len(st["files"])),
            ("阻断", st["sev"]["阻断"]), ("误导", st["sev"]["误导"]),
            ("确认", st["conf"]["确认"]), ("疑似", st["conf"]["疑似"])]
    for i, (k, v) in enumerate(over, 2):
        ws2.cell(row=i, column=1, value=k)
        ws2.cell(row=i, column=2, value=v)

    # 按错误类型(柱状图数据源)
    hd = 9
    for c, txt in ((1, "错误类型"), (2, "条数"), (3, "阻断"), (4, "误导")):
        cell = ws2.cell(row=hd, column=c, value=txt)
        cell.font = Font(bold=True)
    labels = list(st["by_sub"].keys())
    r0 = hd + 1
    for i, lab in enumerate(labels):
        d = st["by_sub"][lab]
        ws2.cell(row=r0 + i, column=1, value=lab)
        ws2.cell(row=r0 + i, column=2, value=d["n"])
        ws2.cell(row=r0 + i, column=3, value=d["blocker"])
        ws2.cell(row=r0 + i, column=4, value=d["misleading"])

    chart = BarChart()
    chart.type = "col"
    chart.style = 10
    chart.title = "各错误类型问题数"
    chart.y_axis.title = "条数"
    chart.x_axis.title = "错误类型"
    data = Reference(ws2, min_col=2, min_row=hd, max_row=r0 + len(labels) - 1)
    cats = Reference(ws2, min_col=1, min_row=r0, max_row=r0 + len(labels) - 1)
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(cats)
    chart.dataLabels = DataLabelList()
    chart.dataLabels.showVal = True
    chart.height, chart.width = 11, 24
    ws2.add_chart(chart, f"F{hd - 1}")

    # 按大类
    ghd = r0 + len(labels) + 2
    for c, txt in ((1, "问题大类"), (2, "条数"), (3, "阻断")):
        cell = ws2.cell(row=ghd, column=c, value=txt)
        cell.font = Font(bold=True)
    for i, (gc_name, d) in enumerate(st["by_group"].items()):
        ws2.cell(row=ghd + 1 + i, column=1, value=gc_name)
        ws2.cell(row=ghd + 1 + i, column=2, value=d["n"])
        ws2.cell(row=ghd + 1 + i, column=3, value=d["blocker"])

    for row in ws2.iter_rows(min_row=2, max_col=4):
        for c in row:
            c.alignment = Alignment(vertical="center")
    for i, w in enumerate([34, 10, 8, 8], 1):
        ws2.column_dimensions[get_column_letter(i)].width = w

    for i in range(2, len(rows) + 2):
        if ws.cell(row=i, column=6).value:
            ws.row_dimensions[i].height = 130
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


# ============================ matplotlib 统计图(可选) ============================

_CJK_FONTS = ("Noto Sans CJK SC", "WenQuanYi Zen Hei", "Source Han Sans SC",
              "Microsoft YaHei", "SimHei", "PingFang SC", "Droid Sans Fallback")


def _enable_cjk(plt, fm) -> bool:
    """让 matplotlib 用上中文字体;找不到返回 False(调用方改走 SVG,避免 PNG 出方框)。"""
    import os
    have = [f.name for f in fm.fontManager.ttflist]
    if any(n in have for n in _CJK_FONTS):
        plt.rcParams["font.sans-serif"] = [next(n for n in _CJK_FONTS if n in have), "DejaVu Sans"]
        return True
    for fp in fm.findSystemFonts():                    # 常见 CJK 字体文件名的系统字体
        low = os.path.basename(fp).lower()
        if any(k in low for k in ("cjk", "wqy", "zenhei", "yahei", "simhei", "pingfang", "notosanscjk")):
            try:
                fm.fontManager.addfont(fp)
            except Exception:
                continue
    have = [f.name for f in fm.fontManager.ttflist]
    for n in _CJK_FONTS:
        if n in have:
            plt.rcParams["font.sans-serif"] = [n, "DejaVu Sans"]
            return True
    return False


def write_matplotlib_png(path: Path, repo: str, st: dict) -> bool:
    """matplotlib 在**且有中文字体**时才出柱状图 PNG;否则返回 False(由 export 补 SVG,避免方框图)。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.font_manager as fm
    except Exception:
        return False
    if not _enable_cjk(plt, fm):
        return False
    plt.rcParams["axes.unicode_minus"] = False
    labels = list(st["by_sub"].keys())
    vals = [st["by_sub"][lab]["n"] for lab in labels]
    fig, ax = plt.subplots(figsize=(10, 5.2), dpi=150)
    colors = ["#c0504d", "#4c78a8", "#82b366", "#b85450", "#8a6d3b", "#6b6b6b", "#7b7b7b"]
    bars = ax.bar(labels, vals, color=colors[:len(labels)])
    ax.bar_label(bars, fontsize=9)
    ax.set_title(f"{repo} · 问题数按错误类型分布(共 {st['total']} 条,覆盖 {len(st['files'])} 篇)")
    ax.set_ylabel("条数")
    ax.tick_params(axis="x", rotation=30, labelsize=9)
    ax.margins(y=0.15)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return True


# ============================ CLI ============================

def build_rows(repo: str) -> list[list[str]]:
    """问题明细行(7 列,按大类→细类→严重度排序)。供调用方与测试使用。"""
    return [_row(repo, f) for f in _defects(repo)]


def export(repo: str, out_dir: Path | None = None) -> list[Path]:
    defects = _defects(repo)
    rows = [_row(repo, f) for f in defects]
    st = _stats(defects)
    d = out_dir or _state.repo_dir(repo)
    d.mkdir(parents=True, exist_ok=True)
    produced = []
    base = d / f"{repo}.xlsx"
    try:
        write_xlsx(base, repo, rows, st)
        produced.append(base)
        print(f"[export] {repo} → {base}  (明细 {st['total']} 条 · 覆盖 {len(st['files'])} 篇 · "
              f"阻断 {st['sev']['阻断']} / 误导 {st['sev']['误导']})")
    except ImportError:
        produced += write_csv_fallback(base, repo, rows, st)
    # 统计柱状图:matplotlib → PNG;否则纯 stdlib → SVG
    png = d / "statistics.png"
    if write_matplotlib_png(png, repo, st):
        produced.append(png)
    else:
        svg = d / "statistics.svg"
        write_svg(svg, f"{repo} · 按错误类型分布",
                  list(st["by_sub"].keys()), [st["by_sub"][l]["n"] for l in st["by_sub"]])
        produced.append(svg)
    return produced


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="导出仓库同名 Excel(明细 7 列 + 统计柱状图),可选依赖自动降级")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--repo", help="仓名(对应 cann-ops-report/doccheck/<repo>/tutorial/ 下已渲染的 report)")
    g.add_argument("--all", action="store_true", help="导出全部已有 report 的仓")
    ap.add_argument("--out", help="输出目录(默认 <repo>/ 自身)")
    args = ap.parse_args()

    repos = sorted(p.name for p in _state.DOCCHECK_ROOT.iterdir()
                   if p.is_dir() and (_state.repo_dir(p.name) / "findings.json").is_file()) \
        if args.all else [args.repo]
    if args.all and not repos:
        print("[export] cann-ops-report/doccheck/ 下没有已渲染的仓")
        return 1
    for r in repos:
        export(r, Path(args.out) if args.out else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
