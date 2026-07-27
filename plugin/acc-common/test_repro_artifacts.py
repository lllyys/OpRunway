import json
import os
import stat
import tempfile
import unittest

import repro_artifacts as R


class ReproArtifactsTest(unittest.TestCase):
    def test_generates_all_case_scripts_and_index(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "work", "cpp_extension"))
            caseset = {"cases": [
                {"id": "case_a", "inputs": [{"dtype": "float16", "shape": [2]}],
                 "attrs": {"dim": 0}, "dims": ["功能", "精度"]},
                {"id": "case_b", "inputs": [{"dtype": "float32", "shape": [4, 1]}],
                 "attrs": {}, "dims": ["精度", "性能"]},
            ]}
            verdict = {"per_case": [
                {"case_id": "case_a", "精度": "pass"},
                {"case_id": "case_b", "精度": "fail"},
            ]}
            out = R.generate_cpp_extension(root, caseset, verdict)
            self.assertEqual(out["case_count"], 2)
            with open(os.path.join(root, "repro", "manifest.json"),
                      encoding="utf-8") as src:
                manifest = json.load(src)
            self.assertEqual(manifest["acceptance_verdict"], None)
            self.assertEqual([x["precision_result"] for x in manifest["cases"]],
                             ["pass", "fail"])
            for case_id in ("case_a", "case_b"):
                path = os.path.join(root, "repro", "cases", case_id + ".sh")
                self.assertTrue(os.stat(path).st_mode & stat.S_IXUSR)
                with open(path, encoding="utf-8") as src:
                    self.assertIn("--describe", src.read())
            self.assertTrue(os.access(
                os.path.join(root, "repro", "show_case.sh"), os.X_OK))
            self.assertTrue(os.access(
                os.path.join(root, "repro", "review.sh"), os.X_OK))
            with open(os.path.join(root, "repro", "cases", "case_b.sh"),
                      encoding="utf-8") as src:
                case_script = src.read()
            self.assertIn("$probe/plugin/acc-common/cpp_extension_repro.py",
                          case_script)
            self.assertIn("报告若已移出 OpRunway 仓", case_script)
            self.assertIn('source "$OPRUNWAY_SETENV"', case_script)
            self.assertIn("OPRUNWAY_REPRO_ENV_FILE 不存在", case_script)
            with open(os.path.join(root, "repro", "review.sh"),
                      encoding="utf-8") as src:
                review_script = src.read()
            self.assertIn("复核未执行完成：启动或环境错误", review_script)
            self.assertIn("OPRUNWAY_REPRO_ENV_FILE=<runtime-env绝对路径>",
                          review_script)
            self.assertIn("rc -ne 0 && $rc -ne 1", review_script)
            with open(os.path.join(root, "repro", "index.tsv"),
                      encoding="utf-8") as src:
                index = src.read()
            self.assertIn("case_a\tfloat16", index)
            self.assertIn("case_b\tfloat32", index)
            with open(os.path.join(root, "repro", "failed.tsv"),
                      encoding="utf-8") as src:
                failed = src.read()
            self.assertNotIn("\tcase_a\t", failed)
            self.assertIn("1\tcase_b\tfloat32", failed)

    def test_rejects_unsafe_case_id(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "work", "cpp_extension"))
            with self.assertRaisesRegex(R.ReproArtifactError, "非法 case_id"):
                R.generate_cpp_extension(
                    root,
                    {"cases": [{"id": "../bad", "inputs": []}]},
                    {"per_case": []},
                )
