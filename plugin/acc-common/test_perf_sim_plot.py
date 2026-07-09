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


if __name__ == "__main__":
    unittest.main()
