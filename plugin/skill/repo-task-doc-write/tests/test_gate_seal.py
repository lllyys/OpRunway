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
DEFECTS = SKILL_ROOT / "tests" / "fixtures" / "defects"


def run_gate(doc, evidence, layers=None):
    cmd = [sys.executable, str(GATE), "--doc", str(doc),
           "--decisions", str(DECISIONS), "--evidence-dir", str(evidence)]
    if layers:
        cmd += ["--layers", layers]
    return subprocess.run(cmd, capture_output=True, text=True)


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

    def test_l4_pending_field_writes_no_seal(self):
        # 封条必须写在全部判红路径之后。上面那条只走结构判红，而 L4 的
        # 待确认项判红是另一条 return 2——两条路都不能留下封条，
        # 否则「有封条」就不再等于「过了门」。
        #
        # 构造要害：只让 L4 红，L0 到 L3 全绿。把 §2.5 里「非连续 Tensor 支持」
        # 这一行的标签换个说法，章节还在、内容非空，前四层都挑不出毛病，
        # 但反填约束表时那一格取不到值，只能写「待确认」。
        pending = self.work / "pending.md"
        text = GOLDEN.read_text(encoding="utf-8")
        row = next(l for l in text.splitlines()
                   if l.startswith("|") and "非连续 Tensor 支持" in l)
        pending.write_text(
            text.replace(row, row.replace("非连续 Tensor 支持", "连续性要求")),
            encoding="utf-8")
        result = run_gate(pending, self.evidence)
        self.assertEqual(2, result.returncode, result.stdout)
        self.assertIn("待确认", result.stdout)
        self.assertFalse((self.evidence / "gate_pass.json").is_file(),
                         "L4 判红却写了封条，门禁可以被绕过")

    def test_acceptance_map_lands_next_to_the_seal(self):
        run_gate(self.doc, self.evidence)
        built = json.loads((self.evidence / "acceptance_map.json").read_text(
            encoding="utf-8"))
        self.assertIn("算子", built)

    def test_partial_layers_never_earn_a_real_seal(self):
        # F2 复现：D06 的「持平」只有 L2（no_vague_word /
        # perf_criterion_has_comparator）能抓到。--layers 跳过 L2，
        # 剩下四层全绿，旧实现会把这份仍然违规的文档当全量通过发真封条。
        d06 = self.work / "d06.md"
        d06.write_text((DEFECTS / "d06_vague_perf.md").read_text(
            encoding="utf-8"), encoding="utf-8")
        result = run_gate(d06, self.evidence, layers="L0,L1,L3,L4")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertFalse((self.evidence / "gate_pass.json").is_file(),
                         "只跑了子集层却写出了封条，红线 C 被绕过")
        self.assertIn("封条未写", result.stdout)

    def test_full_five_layers_still_earn_a_seal(self):
        # 全量跑五层（哪怕顺序或写法不同）必须还能拿到真封条，不是从此锁死。
        result = run_gate(self.doc, self.evidence, layers="L4,L3,L2,L1,L0")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue((self.evidence / "gate_pass.json").is_file())

    def test_missing_table_never_prints_pass_before_the_verdict(self):
        # F4 附带的输出顺序 bug：旧实现先打印「通过 · 层结果 {...}」，再算
        # acceptance_map 是否有待确认项，判红了才 return 2——贴「完整输出」
        # 的人会贴出一份开头写着「通过」的失败记录。「通过」这行只应该在
        # 真的要 return 0 时出现，不能出现在任何判红的输出里。
        pending = self.work / "pending.md"
        text = GOLDEN.read_text(encoding="utf-8")
        row = next(l for l in text.splitlines()
                   if l.startswith("|") and "非连续 Tensor 支持" in l)
        pending.write_text(
            text.replace(row, row.replace("非连续 Tensor 支持", "连续性要求")),
            encoding="utf-8")
        result = run_gate(pending, self.evidence)
        self.assertEqual(2, result.returncode, result.stdout)
        self.assertNotIn("通过 ·", result.stdout,
                         "判红的输出里不该出现「通过」这行")

    def test_scaffold_comment_variant_of_25_gets_no_seal(self):
        # F1 端到端复现：§2.5 换回 make_taskdoc.py 生成的脚手架注释，
        # 五层不该全过，更不该写出封条。
        scaffold = SKILL_ROOT / "tests" / "fixtures" / "defects" \
            / "d13_scaffold_comment.md"
        doc = self.work / "scaffold.md"
        doc.write_text(scaffold.read_text(encoding="utf-8"), encoding="utf-8")
        result = run_gate(doc, self.evidence)
        self.assertEqual(2, result.returncode, result.stdout)
        self.assertFalse((self.evidence / "gate_pass.json").is_file())


if __name__ == "__main__":
    unittest.main()
