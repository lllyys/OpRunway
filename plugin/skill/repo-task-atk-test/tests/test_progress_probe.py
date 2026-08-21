"""压缩后反推进度：只看产物在不在，不信任何声明。

这份判定错一次的代价是具体的：报「缺」会让 agent 回头重做已经做完的一步，
报「齐」会让它跳过没做的一步，而调用它的时机恰恰是上下文刚被压缩、
agent 手上没有别的依据可以对照的时候。

条件产物是最容易判错的一类：不是每轮都要产出，依据本身也可能还没落盘。
「判不了」必须说成判不了，不能说成缺。
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import _contracts  # noqa: E402

SCRIPT = SKILL_ROOT / "scripts" / "probe_progress.py"

S1_ARTIFACTS = ("evidence/constraints.md", "evidence/env.json",
                "evidence/env.sh", "evidence/interface.json")
S2_ARTIFACTS = ("med_decl.json", "med_materialize.py", "must_cover.json",
                "med.yaml", "med_constraint.py",
                "evidence/signature_alignment.json",
                "evidence/signature_contract.json",
                "evidence/adapter_binding.json", "evidence/bundle.json")
S3_ARTIFACTS = ("evidence/soc_binding.json", "evidence/opapi_binding.json",
                "evidence/smoke_1.log")


def probe(work, *args):
    result = subprocess.run([sys.executable, str(SCRIPT), *args,
                             "-C", str(work)],
                            capture_output=True, text=True, timeout=30)
    return result


def touch(work, *relatives):
    for relative in relatives:
        path = Path(work) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")


def write_interface(work, baseline_kind):
    path = Path(work) / "evidence" / "interface.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"baseline_kind": baseline_kind}),
                    encoding="utf-8")


class CurrentStageTest(unittest.TestCase):
    def test_empty_directory_is_still_at_s1(self):
        with TemporaryDirectory() as work:
            result = probe(work)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("子 skill：case-gen", result.stdout.splitlines()[0])
            self.assertIn("当前阶段：S1", result.stdout)

    def test_finished_s1_moves_to_s2(self):
        with TemporaryDirectory() as work:
            touch(work, *S1_ARTIFACTS)
            self.assertIn("当前阶段：S2", probe(work).stdout)

    def test_placeholder_names_match_the_real_file_names(self):
        # 骨架里写的是 `<op>_decl.json`，盘上是 `med_decl.json`。
        # 占位没换成 glob 时，这一整阶段会永远报缺，agent 被钉在 S2。
        with TemporaryDirectory() as work:
            touch(work, *S1_ARTIFACTS, *S2_ARTIFACTS)
            (Path(work) / "frozen_med").mkdir()
            write_interface(work, "torch")
            out = probe(work, "--skill", "case-gen").stdout
            self.assertIn("当前阶段：S2", out)
            for name in ("<op>_decl.json", "<op>.yaml", "冻结输入"):
                self.assertNotIn(f"缺 {name}", out)

    def test_manual_artifacts_never_block_the_last_stage(self):
        # 验收报告和复现包在盘上认不出来。把「认不出来」判成「缺」，
        # S5 就永远到不了，而 S5 恰恰是要写报告的那一步。
        with TemporaryDirectory() as work:
            touch(work, *S1_ARTIFACTS, *S2_ARTIFACTS,
                  "evidence/bundle_intake.json",
                  "evidence/soc_binding.json", "evidence/opapi_binding.json",
                  "evidence/smoke_1.log", "conclusion/accuracy_results.json",
                  "conclusion/performance_results.json",
                  "conclusion/verdict.json")
            (Path(work) / "frozen_med").mkdir()
            write_interface(work, "torch")
            out = probe(work).stdout
            self.assertIn("当前阶段：S5", out)
            self.assertIn("人工确认 验收报告", out)

    def test_missing_directory_exits_two(self):
        with TemporaryDirectory() as work:
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "-C", str(Path(work) / "无此目录")],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(2, result.returncode)


class SkillInferenceTest(unittest.TestCase):
    def make_sealed_case_gen(self, work):
        touch(work, *S1_ARTIFACTS, *S2_ARTIFACTS)
        (Path(work) / "frozen_med").mkdir()
        write_interface(work, "torch")

    def test_sealed_case_gen_hands_off_at_s0(self):
        with TemporaryDirectory() as work:
            self.make_sealed_case_gen(work)
            result = probe(work)
            self.assertEqual(0, result.returncode, result.stderr)
            first = result.stdout.splitlines()[0]
            self.assertIn("已封印", first)
            self.assertIn("S0", first)
            self.assertIn(_contracts.render_card(_contracts.load(), "S0").strip(),
                          result.stdout)
            self.assertNotIn("\nS1 任务书解读", result.stdout)

    def test_intake_manifest_switches_to_acceptance_at_s3(self):
        with TemporaryDirectory() as work:
            self.make_sealed_case_gen(work)
            touch(work, "evidence/bundle_intake.json")
            out = probe(work).stdout
            summary = out[:out.index("当前阶段：")]
            self.assertIn("子 skill：acceptance", out.splitlines()[0])
            self.assertIn("当前阶段：S3", out)
            self.assertNotIn("\nS1 ", summary)
            self.assertNotIn("\nS2 ", summary)

    def test_finished_s3_moves_acceptance_to_s4(self):
        with TemporaryDirectory() as work:
            self.make_sealed_case_gen(work)
            touch(work, "evidence/bundle_intake.json", *S3_ARTIFACTS)
            self.assertIn("当前阶段：S4", probe(work).stdout)

    def test_explicit_acceptance_starts_an_empty_directory_at_s0(self):
        with TemporaryDirectory() as work:
            out = probe(work, "--skill", "acceptance").stdout
            self.assertIn("子 skill：acceptance", out.splitlines()[0])
            self.assertIn("当前阶段：S0", out)

    def test_explicit_case_gen_can_review_a_sealed_directory(self):
        with TemporaryDirectory() as work:
            self.make_sealed_case_gen(work)
            out = probe(work, "--skill", "case-gen").stdout
            summary = out[:out.index("当前阶段：")]
            self.assertIn("已封印", out.splitlines()[0])
            self.assertIn("\nS1 ", summary)
            self.assertIn("\nS2 ", summary)
            self.assertNotIn("\nS0 ", summary)
            self.assertNotIn("\nS3 ", summary)


class ConditionalArtifactTest(unittest.TestCase):
    """条件产物三种状态：要产出、不产出、判不了，各有各的说法。"""

    def test_undecided_when_the_evidence_itself_is_not_on_disk(self):
        with TemporaryDirectory() as work:
            out = probe(work, "--skill", "acceptance").stdout
            self.assertIn("条件未定 evidence/golden_source.json", out)
            self.assertNotIn("缺 evidence/golden_source.json", out)

    def test_builtin_baseline_makes_the_golden_artifacts_required(self):
        with TemporaryDirectory() as work:
            touch(work, "evidence/constraints.md", "evidence/env.json",
                  "evidence/env.sh")
            write_interface(work, "cann_builtin")
            out = probe(work, "--skill", "acceptance").stdout
            self.assertIn("evidence/golden_provenance.json", out)
            self.assertNotIn("条件未定 evidence/golden_provenance.json", out)

    def test_torch_baseline_drops_them_from_the_missing_list(self):
        # roll 真机上的原样重演：卡把 CPU golden 列成无条件必需，
        # agent 花了七次调用去找一个本来就不该存在的文件。
        with TemporaryDirectory() as work:
            touch(work, "evidence/constraints.md", "evidence/env.json",
                  "evidence/env.sh")
            write_interface(work, "torch")
            out = probe(work, "--skill", "acceptance").stdout
            self.assertNotIn("evidence/golden_provenance.json", out)
            self.assertNotIn("evidence/opp_library", out)

    def test_baseline_adapter_flag_decides_the_cpu_golden(self):
        # 判定只作用于进度摘要那几行。卡是骨架的渲染，条件产物在卡里照列，
        # 后面跟着「false 就不要写」——那句话本身就是 agent 要读的判据。
        # 卡按现场情况裁剪就等于有了第二种卡，改哪份都会与另一份不一样。
        with TemporaryDirectory() as work:
            touch(work, *S1_ARTIFACTS)
            path = Path(work) / "evidence" / "signature_alignment.json"
            path.write_text(json.dumps({"baseline_adapter": {"required": False}}),
                            encoding="utf-8")
            out = probe(work).stdout
            summary = out[:out.index("当前阶段：")]
            self.assertNotIn("function_<op>.py", summary)
            self.assertIn("function_<op>.py", out)

    def test_broken_evidence_json_is_undecided_not_a_crash(self):
        # 证据文件写坏是真机常态（跑测中途被打断）。这里崩掉，
        # 压缩后唯一的恢复出口就没了。
        with TemporaryDirectory() as work:
            path = Path(work) / "evidence" / "interface.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{不是 json", encoding="utf-8")
            result = probe(work, "--skill", "acceptance")
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("条件未定", result.stdout)


class OutputTest(unittest.TestCase):
    def test_report_carries_the_card_of_the_current_stage(self):
        # 反推出阶段却不给卡，agent 还是得回头找 SKILL.md——而卡已经不在那里了。
        with TemporaryDirectory() as work:
            touch(work, *S1_ARTIFACTS)
            data = _contracts.load()
            self.assertIn(_contracts.render_card(data, "S2").strip(),
                          probe(work).stdout)

    def test_report_stays_short_enough_to_be_worth_calling(self):
        # 这个脚本存在的理由就是省上下文。它自己刷屏就没有意义了。
        with TemporaryDirectory() as work:
            touch(work, *S1_ARTIFACTS)
            self.assertLess(len(probe(work).stdout), 4000)


if __name__ == "__main__":
    unittest.main()
