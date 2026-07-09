"""Task 3 · perf_sim_plot — 只渲染 perf_compare 产的 report['simulation'] 成 SVG 仿真图。

单一事实源（codex M8）：分析(结论/阈值/容差)全由 perf_compare 生成，本模块只消费 simulation 字段、
不二次推断、不改数据。纯 stdlib（Layer 1 工具中立，不依赖 matplotlib）；阈值线(when_us_below)/
容差带(baseline±abs_gap) 从 simulation 数据传入、非写死（codex M6）；所有文本字段经 XML escape
（codex L1）。sha256_of 供 run_workflow 记 hash、gate 重算比对（防 stale/替换，codex H7）。
"""
import hashlib, json, sys
from xml.sax.saxutils import escape


def _esc(x):
    return escape(str(x))


def _f(v):
    return f"{float(v):.2f}"


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def render_svg(simulation, out_path):
    """把 simulation（perf_compare 独家产）渲染成 SVG 并写盘（返回 out_path）。
    ≥2 点 → 按点序排布散点 + 连线并叠拟合标注；1 点 → 退化为单测点对比 + 容差论证。
    阈值线/容差带全部来自 simulation（数据驱动，不写死 10/3）。"""
    sim = simulation or {}
    pts = [p for p in sim.get("points", []) if isinstance(p, dict)]
    when = sim.get("when_us_below")
    gap = sim.get("abs_gap_us_within")
    op = sim.get("op", "?")
    W, H = 720, 440
    ml, mr, mt, mb = 72, 32, 56, 96
    pw, ph = W - ml - mr, H - mt - mb

    vals = []
    for p in pts:
        for k in ("npu_us", "baseline_us"):
            v = p.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                vals.append(float(v))
        b, g = p.get("baseline_us"), gap
        if isinstance(b, (int, float)) and isinstance(g, (int, float)):
            vals.append(float(b) + float(g))
    if isinstance(when, (int, float)):
        vals.append(float(when))
    ymax = max(vals) * 1.2 if vals else 1.0
    if ymax <= 0:
        ymax = 1.0
    n = len(pts)

    def X(i):
        return ml + pw / 2 if n <= 1 else ml + pw * i / (n - 1)

    def Y(v):
        return mt + ph * (1 - (float(v) / ymax))

    el = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
          f'viewBox="0 0 {W} {H}" font-family="sans-serif" font-size="12">',
          f'<rect x="0" y="0" width="{W}" height="{H}" fill="white"/>',
          f'<text x="{ml}" y="24" font-size="15" font-weight="bold">'
          f'perf simulation · {_esc(op)}（小shape例外·NPU vs 内置基线·kernel_only）</text>',
          # 坐标轴
          f'<line x1="{ml}" y1="{mt}" x2="{ml}" y2="{mt + ph}" stroke="#333" stroke-width="1"/>',
          f'<line x1="{ml}" y1="{mt + ph}" x2="{ml + pw}" y2="{mt + ph}" stroke="#333" stroke-width="1"/>',
          f'<text x="16" y="{mt + ph / 2}" transform="rotate(-90 16 {_f(mt + ph / 2)})">us</text>']

    # 阈值线（when_us_below，数据驱动虚线）
    if isinstance(when, (int, float)) and 0 <= Y(when) <= H:
        yy = _f(Y(when))
        el.append(f'<line x1="{ml}" y1="{yy}" x2="{ml + pw}" y2="{yy}" stroke="#888" '
                  f'stroke-width="1" stroke-dasharray="6 4"/>')
        el.append(f'<text x="{ml + pw - 4}" y="{_f(Y(when) - 4)}" text-anchor="end" '
                  f'fill="#666">when_us_below={_esc(when)}us</text>')

    # 容差带（baseline±abs_gap，逐点竖条）+ 两序列散点
    npu_pts, base_pts = [], []
    for i, p in enumerate(pts):
        x = X(i)
        b, npu = p.get("baseline_us"), p.get("npu_us")
        if isinstance(b, (int, float)) and isinstance(gap, (int, float)):
            y1, y2 = Y(b + gap), Y(b - gap)
            el.append(f'<rect x="{_f(x - 10)}" y="{_f(y1)}" width="20" height="{_f(abs(y2 - y1))}" '
                      f'fill="#f2b8a2" fill-opacity="0.5"/>')
        if isinstance(b, (int, float)):
            base_pts.append((x, Y(b)))
            el.append(f'<circle cx="{_f(x)}" cy="{_f(Y(b))}" r="4" fill="#e07a3f"/>')
        if isinstance(npu, (int, float)):
            npu_pts.append((x, Y(npu)))
            el.append(f'<rect x="{_f(x - 3.5)}" y="{_f(Y(npu) - 3.5)}" width="7" height="7" fill="#2f6fb0"/>')
        el.append(f'<text x="{_f(x)}" y="{mt + ph + 16}" text-anchor="middle" fill="#333">'
                  f'{_esc(p.get("case_id", "?"))}</text>')

    def _poly(points, color):
        if len(points) >= 2:
            d = " ".join(f"{_f(px)},{_f(py)}" for px, py in points)
            el.append(f'<polyline points="{d}" fill="none" stroke="{color}" stroke-width="1.5"/>')

    _poly(base_pts, "#e07a3f")
    _poly(npu_pts, "#2f6fb0")

    # 图例
    lx, ly = ml + 8, mt + 8
    el.append(f'<rect x="{lx}" y="{ly}" width="7" height="7" fill="#2f6fb0"/>'
              f'<text x="{lx + 12}" y="{ly + 8}">NPU</text>')
    el.append(f'<circle cx="{lx + 3.5}" cy="{ly + 22}" r="4" fill="#e07a3f"/>'
              f'<text x="{lx + 12}" y="{ly + 26}">内置基线 (±{_esc(gap)}us 容差带)</text>')

    # 拟合标注（若 perf_compare 给了 fit·模型/推断）+ 结论
    fit = sim.get("fit")
    yb = mt + ph + 40
    if isinstance(fit, dict):
        el.append(f'<text x="{ml}" y="{yb}" fill="#666">fit(模型/推断): '
                  f'NPU {_esc(fit.get("npu_us_per_numel"))} us/elem · '
                  f'基线 {_esc(fit.get("baseline_us_per_numel"))} us/elem</text>')
        yb += 18
    el.append(f'<text x="{ml}" y="{yb}" fill="#222">{_esc(sim.get("overall", ""))}</text>')
    el.append("</svg>")

    svg = "\n".join(el)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(svg)
    return out_path


def main(argv):
    """CLI：perf_sim_plot.py <perf_report.json> [out.svg] —— 读 report['simulation'] 渲染。"""
    report = json.load(open(argv[0], encoding="utf-8"))
    sim = report.get("simulation")
    if not sim:
        print("[perf_sim_plot] report 无 simulation（非例外态，不渲染）")
        return
    out = argv[1] if len(argv) > 1 else "perf_sim.svg"
    render_svg(sim, out)
    print(f"[perf_sim_plot] -> {out} sha256={sha256_of(out)[:12]}…")


if __name__ == "__main__":
    main(sys.argv[1:])
