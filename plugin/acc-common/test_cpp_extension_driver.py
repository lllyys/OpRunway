#!/usr/bin/env python3
"""cpp_extension_driver 的纯静态 helper 测试；不 import torch、不 build。"""

import os
import tempfile
import unittest

import cpp_extension_driver as D


class CppExtensionDriverStaticTest(unittest.TestCase):
    def test_canonical_digest_is_key_order_independent(self):
        self.assertEqual(D._canonical_sha({"a": 1, "b": 2}),
                         D._canonical_sha({"b": 2, "a": 1}))

    def test_safe_path_rejects_escape_and_absolute(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(D.DriverError):
                D._safe(td, "../x")
            with self.assertRaises(D.DriverError):
                D._safe(td, "/tmp/x")

    def test_vendor_path_is_explicit_and_must_exist(self):
        old = os.environ.get("OPRUNWAY_CPP_EXTENSION_VENDOR_LIBRARY")
        try:
            os.environ.pop("OPRUNWAY_CPP_EXTENSION_VENDOR_LIBRARY", None)
            with self.assertRaisesRegex(D.DriverError, "须指向"):
                D._require_env_path("OPRUNWAY_CPP_EXTENSION_VENDOR_LIBRARY")
        finally:
            if old is not None:
                os.environ["OPRUNWAY_CPP_EXTENSION_VENDOR_LIBRARY"] = old


if __name__ == "__main__":
    unittest.main()
