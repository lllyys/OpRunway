#!/usr/bin/env python3
"""CANN runtime 版本契约的纯 stdlib 正负夹具。"""

import unittest

import cann_version as C


SHA = "a" * 64


def _requirement(version="8.5.0"):
    return {
        "kind": "minimum",
        "minimum_version": version,
        "cite": "task.md:7",
        "quote": "CANN 版本要求：8.5.0 及以上",
        "taskdoc_snapshot_sha256": SHA,
    }


def _observation(raw):
    value = C.normalize_observation(raw)
    value["probe"] = {
        "api": C.PROBE_API,
        "package": C.PROBE_PACKAGE,
        "returncode": 0,
        "returncode_source": C.PROBE_RETURN_MEASURED,
        "defining_elf": {"path": "/opt/cann/lib64/libascendcl.so",
                         "sha256": "b" * 64},
    }
    if value["status"] == C.OBS_INVALID:
        value["error"] = "unparseable runtime version"
    return value


class CannVersionContractTest(unittest.TestCase):
    def test_minimum_boundary_matrix(self):
        expected = {
            "8.4.9": (C.EVAL_BELOW_MINIMUM, False),
            "8.5.0": (C.EVAL_SATISFIED, True),
            "8.5.1": (C.EVAL_SATISFIED, True),
        }
        for raw, want in expected.items():
            with self.subTest(raw=raw):
                result = C.evaluate(_requirement(), _observation(raw))
                self.assertEqual((result["status"], result["satisfied"]), want)

    def test_prefix_and_suffix_use_full_string_parser(self):
        prefixed = C.normalize_observation("v8.5.1")
        self.assertEqual(prefixed["normalized"], "8.5.1")
        self.assertIsNone(prefixed["suffix"])

        equal_suffix = C.evaluate(_requirement(), _observation("8.5.0-RC1"))
        self.assertEqual(equal_suffix["status"], C.EVAL_AMBIGUOUS_SUFFIX)
        self.assertFalse(equal_suffix["satisfied"])

        newer_suffix = C.evaluate(_requirement(), _observation("8.5.1+build7"))
        self.assertEqual(newer_suffix["status"], C.EVAL_SATISFIED)
        self.assertTrue(newer_suffix["satisfied"])

    def test_unknown_and_garbage_are_not_satisfied(self):
        unknown = C.unknown_observation({
            "api": C.PROBE_API,
            "package": C.PROBE_PACKAGE,
            "returncode": 7,
            "returncode_source": C.PROBE_RETURN_MEASURED,
            "defining_elf": None,
        }, "ACL query failed")
        result = C.evaluate(_requirement(), unknown)
        self.assertEqual(result["status"], C.EVAL_UNKNOWN)
        self.assertFalse(result["satisfied"])

        invalid = C.evaluate(_requirement(), _observation("CANN-8.5.0 junk"))
        self.assertEqual(invalid["status"], C.EVAL_INVALID)
        self.assertFalse(invalid["satisfied"])

    def test_not_declared_is_explicit_and_does_not_claim_satisfaction(self):
        result = C.evaluate({"kind": "not_declared"}, _observation("9.0.1"))
        self.assertEqual(result["status"], C.EVAL_NOT_DECLARED)
        self.assertIsNone(result["satisfied"])
        with self.assertRaises(C.CannVersionError):
            C.normalize_requirement({"kind": "not_declared", "minimum_version": "8.5.0"})

    def test_requirement_rejects_noncanonical_or_unbound_version(self):
        for bad in ("v8.5.0", "8.5", "08.5.0", "8.5.0-RC1", "8.5.0 "):
            value = _requirement(bad)
            with self.subTest(version=bad), self.assertRaises(C.CannVersionError):
                C.normalize_requirement(value)
        value = _requirement()
        del value["taskdoc_snapshot_sha256"]
        with self.assertRaises(C.CannVersionError):
            C.normalize_requirement(value)

    def test_observation_tamper_is_detected(self):
        value = _observation("8.5.1")
        value["normalized"] = "9.0.0"
        with self.assertRaisesRegex(C.CannVersionError, "重新规范化"):
            C.validate_observation_record(value)


if __name__ == "__main__":
    unittest.main()
