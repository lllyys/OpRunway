"""content_address 的纯 stdlib 单测；不接入跑测或裁决。"""

import json
import math
import os
import tempfile
import unittest
from unittest import mock

import content_address as ca


class CanonicalJsonTest(unittest.TestCase):
    def test_object_order_and_whitespace_do_not_change_bytes(self):
        a = {"中": [1, True, None], "a": {"y": 2, "x": 1}}
        b = {"a": {"x": 1, "y": 2}, "中": [1, True, None]}
        self.assertEqual(ca.canonical_json_bytes(a), ca.canonical_json_bytes(b))
        self.assertEqual(ca.canonical_json_bytes(a),
                         '{"a":{"x":1,"y":2},"中":[1,true,null]}'.encode())

    def test_rejects_non_finite_float_recursively(self):
        for bad in (math.nan, math.inf, -math.inf):
            with self.subTest(bad=bad):
                with self.assertRaises(ca.ContentAddressError):
                    ca.canonical_json_bytes({"x": [bad]})

    def test_rejects_non_json_types_and_non_string_keys(self):
        for bad in ((1, 2), {1, 2}, b"bytes"):
            with self.subTest(type=type(bad).__name__):
                with self.assertRaises(ca.ContentAddressError):
                    ca.canonical_json_bytes(bad)
        with self.assertRaises(ca.ContentAddressError):
            ca.canonical_json_bytes({1: "not-json"})

    def test_domain_separation_and_determinism(self):
        value = {"x": 1}
        self.assertEqual(ca.content_digest("source/v1", value),
                         ca.content_digest("source/v1", {"x": 1}))
        self.assertNotEqual(ca.content_digest("source/v1", value),
                            ca.content_digest("case-plan/v1", value))
        self.assertRegex(ca.content_digest("source/v1", value), r"^[0-9a-f]{64}$")

    def test_domain_must_be_nonempty_string(self):
        for domain in ("", None, 3):
            with self.subTest(domain=domain):
                with self.assertRaises(ca.ContentAddressError):
                    ca.content_digest(domain, {})


class SafePathTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_accepts_nested_relative_path(self):
        self.assertEqual(
            ca.safe_path(self.root, "aa/artifact.json"),
            os.path.join(self.root, "aa", "artifact.json"),
        )

    def test_rejects_absolute_parent_empty_and_dot_segments(self):
        bad = ("", "/tmp/out.json", "../out.json", "a/../out.json",
               "./out.json", "a//out.json")
        for rel in bad:
            with self.subTest(rel=rel):
                with self.assertRaises(ca.ContentAddressError):
                    ca.safe_path(self.root, rel)

    def test_rejects_symlink_in_any_existing_segment(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        os.symlink(outside.name, os.path.join(self.root, "link"))
        with self.assertRaises(ca.ContentAddressError):
            ca.safe_path(self.root, "link/out.json")

    def test_rejects_symlink_root(self):
        parent = tempfile.TemporaryDirectory()
        self.addCleanup(parent.cleanup)
        link = os.path.join(parent.name, "root-link")
        os.symlink(self.root, link)
        with self.assertRaises(ca.ContentAddressError):
            ca.safe_path(link, "out.json")


class AtomicArtifactTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_atomic_json_is_canonical_and_leaves_no_temp(self):
        path = ca.atomic_write_json(self.root, "nested/value.json", {"b": 2, "a": 1})
        with open(path, "rb") as src:
            self.assertEqual(src.read(), b'{"a":1,"b":2}')
        self.assertFalse(any(n.startswith(".oprunway-tmp-")
                             for n in os.listdir(os.path.dirname(path))))

    def test_replace_failure_preserves_old_target_and_cleans_temp(self):
        target = os.path.join(self.root, "value.json")
        with open(target, "wb") as out:
            out.write(b"old")
        with mock.patch.object(ca.os, "replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                ca.atomic_write_json(self.root, "value.json", {"new": True})
        with open(target, "rb") as src:
            self.assertEqual(src.read(), b"old")
        self.assertFalse(any(n.startswith(".oprunway-tmp-")
                             for n in os.listdir(self.root)))

    def test_round_trip_artifact(self):
        payload = {"op": "AnyOp", "items": [1, 2]}
        path = ca.write_artifact(self.root, "objects/x.json", "source-facts/v1", payload)
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(
            ca.read_artifact(self.root, "objects/x.json", "source-facts/v1"),
            payload,
        )

    def _rewrite(self, artifact):
        path = os.path.join(self.root, "artifact.json")
        with open(path, "w", encoding="utf-8") as out:
            json.dump(artifact, out, ensure_ascii=False, allow_nan=True)

    def test_rejects_payload_tampering(self):
        artifact = ca.make_artifact("source/v1", {"head": "abc"})
        artifact["payload"]["head"] = "def"
        self._rewrite(artifact)
        with self.assertRaisesRegex(ca.ContentAddressError, "摘要不匹配"):
            ca.read_artifact(self.root, "artifact.json", "source/v1")

    def test_rejects_wrong_domain_unknown_field_and_version(self):
        base = ca.make_artifact("source/v1", {"x": 1})
        self._rewrite(base)
        with self.assertRaisesRegex(ca.ContentAddressError, "domain 不匹配"):
            ca.read_artifact(self.root, "artifact.json", "plan/v1")

        extra = dict(base, surprise=True)
        self._rewrite(extra)
        with self.assertRaisesRegex(ca.ContentAddressError, "字段必须严格等于"):
            ca.read_artifact(self.root, "artifact.json", "source/v1")

        version = dict(base, schema_version=2)
        self._rewrite(version)
        with self.assertRaisesRegex(ca.ContentAddressError, "schema_version"):
            ca.read_artifact(self.root, "artifact.json", "source/v1")

    def test_rejects_malformed_digest_and_nonfinite_json(self):
        artifact = ca.make_artifact("source/v1", {"x": 1})
        artifact["digest"] = "ABC"
        self._rewrite(artifact)
        with self.assertRaisesRegex(ca.ContentAddressError, "小写 sha256"):
            ca.read_artifact(self.root, "artifact.json", "source/v1")

        path = os.path.join(self.root, "artifact.json")
        with open(path, "w", encoding="utf-8") as out:
            out.write('{"schema_version":1,"domain":"source/v1","digest":"'
                      + "0" * 64 + '","payload":NaN}')
        with self.assertRaisesRegex(ca.ContentAddressError, "非法 JSON 常量"):
            ca.read_artifact(self.root, "artifact.json", "source/v1")

    def test_read_rejects_symlink_target(self):
        real = os.path.join(self.root, "real.json")
        ca.write_artifact(self.root, "real.json", "source/v1", {"x": 1})
        os.symlink(real, os.path.join(self.root, "link.json"))
        with self.assertRaises(ca.ContentAddressError):
            ca.read_artifact(self.root, "link.json", "source/v1")


if __name__ == "__main__":
    unittest.main()
