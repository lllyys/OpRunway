"""perf_sim_plot 单测——只渲染 simulation、SVG 良构、XML escape、不改数据。

跑: python3 -m unittest test_perf_sim_plot -v   （在 acc-common/ 下）
"""
import copy, os, tempfile, unittest
from xml.dom.minidom import parseString
import perf_sim_plot as psp


def _sim(points, fit=None):
    sim = {"op": "Sign", "when_us_below": 10, "abs_gap_us_within": 3,
           "points": points, "overall": "N 个小shape用例落容差内 → 一致/更优"}
    if fit:
        sim["fit"] = fit
    return sim


def _pt(cid, numel, npu, base):
    return {"case_id": cid, "numel": numel, "npu_us": npu, "baseline_us": base,
            "gap": round(abs(npu - base), 6), "within": 3, "conclusion": f"{cid} 一致"}


class PerfSimPlotTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def _render(self, sim):
        out = os.path.join(self.d, "p.svg")
        psp.render_svg(sim, out)
        with open(out, encoding="utf-8") as f:
            return f.read()

    def test_two_points_wellformed(self):
        sim = _sim([_pt("s0", 64, 1.5, 1.2), _pt("s1", 256, 1.6, 1.3)],
                   fit={"npu_us_per_numel": 0.001, "baseline_us_per_numel": 0.001, "note": "模型/推断"})
        svg = self._render(sim)
        parseString(svg)                       # 可解析 = 良构 XML
        self.assertIn("NPU", svg)
        self.assertIn("内置基线", svg)
        self.assertIn("when_us_below=10", svg)  # 阈值线来自数据
        self.assertIn("<polyline", svg)         # ≥2 点连线
        self.assertIn("模型/推断", svg)          # fit 标注

    def test_one_point_degenerate(self):
        svg = self._render(_sim([_pt("s0", 64, 1.5, 1.2)]))
        parseString(svg)
        self.assertIn("s0", svg)

    def test_xml_escape_special_chars(self):
        pt = _pt("s<0>&\"'", 64, 1.5, 1.2)
        pt["conclusion"] = "危险 <tag> & \"引号\""
        sim = _sim([pt])
        sim["op"] = "<script>alert(1)</script>"
        svg = self._render(sim)
        parseString(svg)                       # 特殊字符仍良构（escape 生效）
        self.assertNotIn("<script>", svg)       # op 被 escape
        self.assertIn("&lt;", svg)

    def test_render_does_not_mutate(self):
        sim = _sim([_pt("s0", 64, 1.5, 1.2)])
        before = copy.deepcopy(sim)
        self._render(sim)
        self.assertEqual(sim, before)           # 单一事实源：只读 simulation

    def test_sha256_stable(self):
        sim = _sim([_pt("s0", 64, 1.5, 1.2)])
        out = os.path.join(self.d, "q.svg")
        psp.render_svg(sim, out)
        self.assertEqual(psp.sha256_of(out), psp.sha256_of(out))

    # ---- CONFIRMED 真 bug 负例（psp-1/3/4） ----

    def test_psp1_nan_no_nan_coords(self):
        """psp-1：baseline_us=NaN（npu 有效）→ 不写 nan 坐标、不崩、SVG 良构。"""
        pt = {"case_id": "s0", "numel": 64, "npu_us": 1.5, "baseline_us": float("nan"),
              "gap": 0, "within": 3, "conclusion": "x"}
        svg = self._render(_sim([pt]))
        parseString(svg)
        self.assertNotIn("nan", svg.lower())
        self.assertNotIn("inf", svg.lower())

    def test_psp1_overflow_int_no_crash(self):
        """psp-1：超大 int 触发 float 溢出 → 跳过该坐标、不 OverflowError 崩溃。"""
        pt = {"case_id": "s1", "numel": 256, "npu_us": 10 ** 400, "baseline_us": 1.3,
              "gap": 0, "within": 3, "conclusion": "x"}
        svg = self._render(_sim([pt]))
        parseString(svg)
        self.assertNotIn("inf", svg.lower())

    def test_psp1_all_invalid_point_skipped(self):
        """npu=inf 且 baseline=NaN → 整点跳过，SVG 仍良构、无 nan/inf。"""
        pt = {"case_id": "s0", "numel": 64, "npu_us": float("inf"), "baseline_us": float("nan"),
              "gap": 0, "within": 3, "conclusion": "x"}
        svg = self._render(_sim([pt]))
        parseString(svg)
        self.assertNotIn("nan", svg.lower())
        self.assertNotIn("inf", svg.lower())

    def test_psp3_null_char_still_parseable(self):
        """psp-3：case_id/op 含 \\x00 → 剔除 XML 非法控制字符后仍可被 xml.etree/minidom 解析。"""
        pt = _pt("s\x000", 64, 1.5, 1.2)
        pt["conclusion"] = "危险\x00结论"
        sim = _sim([pt])
        sim["op"] = "op\x00name"
        svg = self._render(sim)
        parseString(svg)                        # 可解析 = 剔除生效
        self.assertNotIn("\x00", svg)

    def test_psp4_reject_dotdot(self):
        """psp-4：out_path 含 `..` → 拒绝（防目录穿越）。"""
        with self.assertRaises(ValueError):
            psp.render_svg(_sim([_pt("s0", 64, 1.5, 1.2)]), os.path.join(self.d, "..", "evil.svg"))

    def test_psp4_reject_symlink(self):
        """psp-4：out_path 指向 symlink → O_NOFOLLOW/O_EXCL 拒（不跟随写穿）。"""
        target = os.path.join(self.d, "real.txt")
        with open(target, "w") as f:
            f.write("x")
        link = os.path.join(self.d, "link.svg")
        os.symlink(target, link)
        with self.assertRaises(OSError):
            psp.render_svg(_sim([_pt("s0", 64, 1.5, 1.2)]), link)

    def test_psp4_no_overwrite_without_force(self):
        """默认 O_EXCL 不覆盖已存在文件；--force 才允许覆盖。"""
        out = os.path.join(self.d, "exists.svg")
        with open(out, "w") as f:
            f.write("old")
        with self.assertRaises(FileExistsError):
            psp.render_svg(_sim([_pt("s0", 64, 1.5, 1.2)]), out)
        psp.render_svg(_sim([_pt("s0", 64, 1.5, 1.2)]), out, force=True)
        with open(out, encoding="utf-8") as f:
            self.assertIn("<svg", f.read())


if __name__ == "__main__":
    unittest.main()
