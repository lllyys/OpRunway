"""语义走查产物核验（P3 执行）。

以环境变量 WALKTHROUGH_DIR 指向走查产物根目录（reports/<run>）。每个场景一个
子目录，内含 manifest.json 声明本场景的产物与预期：

    {"scenario": "b1", "taskdoc": "SlidingTileAttention_task_doc.md",
     "decisions": "evidence/decisions.json", "seal": "evidence/gate_pass.json",
     "review_rounds": ["evidence/review-round-1.md"], "intake": "intake.md",
     "human_inputs": [{"seq": 1, "kind": "intake"}, {"seq": 2, "kind": "review"}],
     "expect_inputs": 2}

核验为一致性检查：封条哈希对得上任务书、review 来源的确认内容对得上候选块、
intake 来源的原话对得上 intake 文件、失效记录不残留原位、输入次数符合声明。
未设 WALKTHROUGH_DIR 时所有用例直接失败——常规全量跑用 -k "not walkthrough"
显式反选，不许靠 skip 拿通过。
"""

import hashlib
import json
import os
import re
import unittest
from pathlib import Path

CANDIDATE_RE = re.compile(r"```candidate\n(.*?)```", re.S)
SECTION_RE = re.compile(r"^## (\S+)\s*$", re.M)


def scenarios():
    root = os.environ.get("WALKTHROUGH_DIR")
    if not root or not Path(root).is_dir():
        raise AssertionError(
            "WALKTHROUGH_DIR 未设或不存在：走查核验必须指向真实产物目录，"
            "常规全量跑请用 -k 'not walkthrough' 反选而不是让它 skip。")
    dirs = [d for d in Path(root).iterdir()
            if d.is_dir() and (d / "manifest.json").is_file()]
    if not dirs:
        raise AssertionError(f"{root} 下没有含 manifest.json 的场景目录。")
    return dirs


def candidate_blocks(text):
    blocks = {}
    matches = list(SECTION_RE.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = CANDIDATE_RE.search(text[m.end():end])
        if block:
            # 围栏边界的换行是围栏语法，不算候选内容。
            blocks[m.group(1)] = block.group(1).strip("\n")
    return blocks


class WalkthroughArtifactsTest(unittest.TestCase):
    def for_each_scenario(self, check):
        for scenario in scenarios():
            manifest = json.loads(
                (scenario / "manifest.json").read_text(encoding="utf-8"))
            with self.subTest(scenario=scenario.name):
                check(scenario, manifest)

    def test_declared_artifacts_exist(self):
        def check(scenario, manifest):
            names = [manifest["taskdoc"], manifest["decisions"],
                     manifest["seal"], *manifest.get("review_rounds", [])]
            if manifest.get("intake"):
                names.append(manifest["intake"])
            for name in names:
                self.assertTrue((scenario / name).is_file(),
                                f"缺产物 {name}")
        self.for_each_scenario(check)

    def test_seal_matches_taskdoc(self):
        def check(scenario, manifest):
            seal = json.loads(
                (scenario / manifest["seal"]).read_text(encoding="utf-8"))
            digest = hashlib.sha256(
                (scenario / manifest["taskdoc"]).read_bytes()).hexdigest()
            self.assertEqual(seal["sha256"], digest, "封条与任务书不匹配")
        self.for_each_scenario(check)

    def test_confirmed_content_traces_to_source(self):
        def check(scenario, manifest):
            decisions = json.loads(
                (scenario / manifest["decisions"]).read_text(encoding="utf-8"))
            rounds = {}
            for name in manifest.get("review_rounds", []):
                rounds.update(candidate_blocks(
                    (scenario / name).read_text(encoding="utf-8")))
            intake_text = ""
            if manifest.get("intake"):
                intake_text = (scenario / manifest["intake"]).read_text(
                    encoding="utf-8")
            for key, record in decisions.items():
                if key.startswith("_") or not isinstance(record, dict):
                    continue
                source = record.get("source")
                reply = record.get("human_reply", "")
                self.assertTrue(reply.strip(),
                                f"{key} 缺答复原话（human_reply 为空）")
                if source == "review":
                    self.assertIn(key, rounds, f"{key} 无对应总审节")
                    self.assertEqual(
                        record.get("confirmed_content"), rounds[key],
                        f"{key} 的确认内容与候选块不一致")
                elif source == "intake":
                    self.assertIn(reply, intake_text,
                                  f"{key} 的原话未见于 intake 文件")
        self.for_each_scenario(check)

    def test_superseded_keys_left_their_slot(self):
        """失效记录必须离开原位；key 再次出现只能是内容不同的重新确认。"""
        def check(scenario, manifest):
            decisions = json.loads(
                (scenario / manifest["decisions"]).read_text(encoding="utf-8"))
            for entry in decisions.get("_superseded", []):
                current = decisions.get(entry["key"])
                if current is not None:
                    self.assertNotEqual(
                        current, entry["record"],
                        f"失效记录 {entry['key']} 原样残留（未经重新确认）")
        self.for_each_scenario(check)

    def test_human_input_count_matches_declaration(self):
        def check(scenario, manifest):
            expected = manifest.get("expect_inputs")
            if expected is not None:
                self.assertEqual(expected,
                                 len(manifest.get("human_inputs", [])),
                                 "人输入次数与声明不符")
        self.for_each_scenario(check)


if __name__ == "__main__":
    unittest.main()
