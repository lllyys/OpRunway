import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from _policy import PolicyError, load_policy, policy_sha256


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
        self.assertEqual(len(policy_sha256()), 64)

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
