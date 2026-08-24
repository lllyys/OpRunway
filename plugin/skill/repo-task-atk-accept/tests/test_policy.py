import json
import sys
import tempfile
import unittest
from pathlib import Path

from _paths import SCRIPTS, is_nested_source

if not is_nested_source():
    raise unittest.SkipTest("合并 policy 的测试需要两侧配置与消费者同时存在")


sys.path.insert(0, str(SCRIPTS))

from _policy import (
    PolicyError,
    interface_policy_path,
    load_interface_policy,
    load_policy,
    load_verdict_policy,
    policy_sha256,
    verdict_policy_path,
)


class PolicyTest(unittest.TestCase):
    def write_policy(self, policy):
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name) / "policy.json"
        path.write_text(json.dumps(policy), encoding="utf-8")
        self.addCleanup(directory.cleanup)
        return path

    def test_default_policy_is_valid(self):
        policy = load_policy()
        self.assertEqual(policy["schema_version"], 1)
        self.assertIn("aclnn", policy["interface_modes"])
        digests = policy_sha256()
        self.assertEqual(set(digests), {"interface", "verdict"})
        self.assertTrue(all(len(digest) == 64 for digest in digests.values()))

    def test_split_policy_files_are_independently_valid(self):
        interface = load_interface_policy()
        verdict = load_verdict_policy()

        self.assertEqual(interface_policy_path().name, "interface-policy.json")
        self.assertEqual(verdict_policy_path().name, "verdict-policy.json")
        self.assertEqual(
            set(interface), {"schema_version", "interface_modes"}
        )
        self.assertEqual(
            set(verdict),
            {
                "schema_version",
                "accuracy",
                "case_exclusions",
                "failure_attribution",
                "conclusion_causes",
            },
        )

    def test_merged_policy_matches_split_files_key_by_key(self):
        interface = load_interface_policy()
        verdict = load_verdict_policy()
        merged = load_policy()

        self.assertEqual(
            set(merged), (set(interface) | set(verdict))
        )
        for part in (interface, verdict):
            for key, value in part.items():
                self.assertEqual(merged[key], value, key)

    def test_policy_consumers_load_only_the_policy_side_they_use(self):
        sources = {
            name: (SCRIPTS / name).read_text(encoding="utf-8")
            for name in (
                "derive_interface.py",
                "parse_atk_report.py",
                "select_perf_cases.py",
                "verdict.py",
                "make_repro.py",
            )
        }

        self.assertNotIn("load_policy(", sources["derive_interface.py"])
        self.assertNotIn("load_verdict_policy", sources["derive_interface.py"])
        for name in ("parse_atk_report.py", "select_perf_cases.py"):
            with self.subTest(script=name):
                self.assertNotIn("load_policy(", sources[name])
                self.assertNotIn("load_interface_policy", sources[name])

        for name in ("verdict.py", "make_repro.py"):
            with self.subTest(script=name):
                self.assertIn("load_policy(", sources[name])

    def test_rejects_missing_interface_modes(self):
        path = self.write_policy({"schema_version": 1})
        with self.assertRaises(PolicyError):
            load_policy(path)

    def test_rejects_empty_backends(self):
        policy = load_policy()
        policy["interface_modes"]["aclnn"]["backends"] = []
        path = self.write_policy(policy)
        with self.assertRaises(PolicyError):
            load_policy(path)

    def test_rejects_missing_acceptance_state(self):
        policy = load_policy()
        del policy["interface_modes"]["aclnn"]["acceptance_enabled"]
        path = self.write_policy(policy)
        with self.assertRaises(PolicyError):
            load_policy(path)

    def test_rejects_missing_exclusion_causes(self):
        policy = load_policy()
        del policy["case_exclusions"]
        path = self.write_policy(policy)
        with self.assertRaises(PolicyError):
            load_policy(path)

    def test_rejects_duplicate_failure_ids(self):
        policy = load_policy()
        policy["failure_attribution"].append(
            dict(policy["failure_attribution"][0])
        )
        path = self.write_policy(policy)
        with self.assertRaises(PolicyError):
            load_policy(path)


if __name__ == "__main__":
    unittest.main()
