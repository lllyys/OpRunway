"""Task 3 · perf_sim_plot — 只渲染 perf_compare 产的 report['simulation'] 成 SVG 仿真图。

单一事实源（codex M8）：分析(结论/阈值/容差)全由 perf_compare 生成，本模块只消费 simulation 字段、
不二次推断、不改数据。纯 stdlib（Layer 1 工具中立，不依赖 matplotlib）；阈值线(when_us_below)/
容差带(baseline±abs_gap) 从 simulation 数据传入、非写死。sha256_of 供 run_workflow 记 hash、gate 重算比对。

健壮性（codex CONFIRMED）：
- psp-1/psp-2：统一有限数值 helper `_finite_num`（拒 bool/None/NaN/inf、捕 int→float 溢出）；
  非法点跳过并记 warning，绝不写出 nan/inf 坐标；
- psp-3：`_esc` 先剔除 XML 1.0 非法控制字符（保留 \t\n\r）再 escape，防 \x00 等写出不可解析 SVG；
- psp-4：写盘走 `_safe_open_write`（拒 `..`/NUL、O_NOFOLLOW 拒符号链接、O_EXCL 非 --force 不覆盖）；
- psp-6：CLI 加 argparse + 结构化中文错误 + 非零返回，文件读取用 with。
"""
import argparse, hashlib, json, math, os, sys
from xml.sax.saxutils import escape


def _finite_num(v):
    """有限实数（拒 bool/None/NaN/inf；超大 int→float 溢出也拒）——psp-1/psp-2 统一数值口径。"""
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return False
    try:
        f = float(v)
    except (OverflowError, ValueError):
        return False
    return math.isfinite(f)


def _xml_clean(s):
    """剔除 XML 1.0 非法字符（保留 \\t \\n \\r），防控制字符（如 \\x00）写出不可解析 SVG（psp-3）。"""
    out = []
    for ch in s:
        o = ord(ch)
        if o in (0x9, 0xA, 0xD) or 0x20 <= o <= 0xD7FF or 0xE000 <= o <= 0xFFFD or 0x10000 <= o <= 0x10FFFF:
            out.append(ch)
    return "".join(out)


def _esc(x):
    return escape(_xml_clean(str(x)))


def _f(v):
    """坐标格式化——仅对已过 _finite_num 的值调用（防 nan/inf 落坐标，psp-1）。"""
    return f"{float(v):.2f}"


def _safe_open_write(out_path, force=False):
    """受控写盘（psp-4）：拒 `..`/NUL/空；O_NOFOLLOW 拒最终组件符号链接（anti-TOCTOU）；
    默认 O_EXCL 不覆盖，force=True 才允许 O_TRUNC 覆盖。返回可写文件对象。"""
    if not isinstance(out_path, str) or not out_path or "\x00" in out_path:
        raise ValueError(f"非法输出路径: {out_path!r}")
    if ".." in out_path.replace("\\", "/").split("/"):
        raise ValueError(f"输出路径不得含 '..'（拒目录穿越）: {out_path!r}")
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if force else os.O_EXCL)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(out_path, flags, 0o644)
    return os.fdopen(fd, "w", encoding="utf-8")


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def render_svg(simulation, out_path, force=False):
    """把 simulation（perf_compare 独家产）渲染成 SVG 并写盘（返回 out_path）。
    ≥2 点 → 按点序排布散点 + 连线并叠拟合标注；1 点 → 退化为单测点对比 + 容差论证。
    阈值线/容差带全部来自 simulation（数据驱动，不写死 10/3）。非有限数值点跳过、绝不写 nan/inf 坐标。"""
    sim = simulation or {}
    warnings = []
    # 清洗点：任一坐标非有限 → 该坐标记 None；npu/baseline 均非法 → 整点跳过（psp-1）
    clean = []
    for p in sim.get("points", []):
        if not isinstance(p, dict):
            warnings.append("simulation 点非对象，跳过")
            continue
        npu, base = p.get("npu_us"), p.get("baseline_us")
        npu_ok, base_ok = _finite_num(npu), _finite_num(base)
        if not npu_ok and not base_ok:
            warnings.append(f"{p.get('case_id')}: npu_us/baseline_us 均非有限数值，跳过整点")
            continue
        if not npu_ok:
            warnings.append(f"{p.get('case_id')}: npu_us={npu!r} 非有限数值，仅画基线")
        if not base_ok:
            warnings.append(f"{p.get('case_id')}: baseline_us={base!r} 非有限数值，仅画 NPU")
        clean.append({"case_id": p.get("case_id", "?"),
                      "npu_us": float(npu) if npu_ok else None,
                      "baseline_us": float(base) if base_ok else None})
    for w in warnings:
        print(f"[perf_sim_plot] ⚠ {w}", file=sys.stderr)

    when = sim.get("when_us_below") if _finite_num(sim.get("when_us_below")) else None
    gap = sim.get("abs_gap_us_within") if _finite_num(sim.get("abs_gap_us_within")) else None
    op = sim.get("op", "?")
    W, H = 720, 440
    ml, mr, mt, mb = 72, 32, 56, 96
    pw, ph = W - ml - mr, H - mt - mb

    vals = []
    for p in clean:
        for k in ("npu_us", "baseline_us"):
            if p[k] is not None:
                vals.append(p[k])
        if p["baseline_us"] is not None and gap is not None:
            vals.append(p["baseline_us"] + gap)
    if when is not None:
        vals.append(when)
    ymax = max(vals) * 1.2 if vals else 1.0
    if not _finite_num(ymax) or ymax <= 0:
        ymax = 1.0
    n = len(clean)

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
    if when is not None and 0 <= Y(when) <= H:
        yy = _f(Y(when))
        el.append(f'<line x1="{ml}" y1="{yy}" x2="{ml + pw}" y2="{yy}" stroke="#888" '
                  f'stroke-width="1" stroke-dasharray="6 4"/>')
        el.append(f'<text x="{ml + pw - 4}" y="{_f(Y(when) - 4)}" text-anchor="end" '
                  f'fill="#666">when_us_below={_esc(when)}us</text>')

    # 容差带（baseline±abs_gap，逐点竖条）+ 两序列散点
    npu_pts, base_pts = [], []
    for i, p in enumerate(clean):
        x = X(i)
        b, npu = p["baseline_us"], p["npu_us"]
        if b is not None and gap is not None:
            y1, y2 = Y(b + gap), Y(b - gap)
            el.append(f'<rect x="{_f(x - 10)}" y="{_f(y1)}" width="20" height="{_f(abs(y2 - y1))}" '
                      f'fill="#f2b8a2" fill-opacity="0.5"/>')
        if b is not None:
            base_pts.append((x, Y(b)))
            el.append(f'<circle cx="{_f(x)}" cy="{_f(Y(b))}" r="4" fill="#e07a3f"/>')
        if npu is not None:
            npu_pts.append((x, Y(npu)))
            el.append(f'<rect x="{_f(x - 3.5)}" y="{_f(Y(npu) - 3.5)}" width="7" height="7" fill="#2f6fb0"/>')
        el.append(f'<text x="{_f(x)}" y="{mt + ph + 16}" text-anchor="middle" fill="#333">'
                  f'{_esc(p["case_id"])}</text>')

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
    with _safe_open_write(out_path, force=force) as fh:
        fh.write(svg)
    return out_path


def main(argv):
    """CLI：perf_sim_plot.py <perf_report.json> [out.svg] [--force] —— 读 report['simulation'] 渲染。"""
    ap = argparse.ArgumentParser(description="Task3 perf_sim_plot（只渲染 simulation）")
    ap.add_argument("report", help="perf_report.json（含 simulation 字段）")
    ap.add_argument("out", nargs="?", default="perf_sim.svg")
    ap.add_argument("--force", action="store_true", help="允许覆盖已存在的输出（默认 O_EXCL 不覆盖）")
    try:
        a = ap.parse_args(argv)
        with open(a.report, encoding="utf-8") as f:
            report = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError) as ex:
        print(f"[perf_sim_plot] 错误：无法读取 report——{ex}", file=sys.stderr)
        return 2
    if not isinstance(report, dict):
        print("[perf_sim_plot] 错误：report 顶层须为对象", file=sys.stderr)
        return 2
    sim = report.get("simulation")
    if not sim:
        print("[perf_sim_plot] report 无 simulation（非例外态，不渲染）")
        return 0
    try:
        render_svg(sim, a.out, force=a.force)
    except (OSError, ValueError) as ex:
        print(f"[perf_sim_plot] 错误：写盘失败——{ex}", file=sys.stderr)
        return 2
    print(f"[perf_sim_plot] -> {a.out} sha256={sha256_of(a.out)[:12]}…")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
