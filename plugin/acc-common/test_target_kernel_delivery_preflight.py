#!/usr/bin/env python3
"""workflow 前置 target closure 必须绑定本轮 spec 与 source_facts。"""

import json
import os
import tempfile
import unittest
from unittest import mock

import cpp_extension_adapter as A
import vendor_build_receipt as V


class TargetKernelDeliveryPreflightTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.library = os.path.join(self.tmp.name, "libcust_opapi.so")
        with open(self.library, "wb") as out:
            out.write(b"elf")
        self.anchor = {"schema": "oprunway.source_content_anchor", "sha256": "a" * 64}
        self.receipt = os.path.join(self.tmp.name, "receipt.json")
        self.doc = {
            "source": {"content_anchor": self.anchor},
            V.TARGET_KERNEL_DELIVERY_KEY: {
                "request": {"expected_op_type": "Widget"},
            },
        }
        self._write()

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self):
        with open(self.receipt, "w", encoding="utf-8") as out:
            json.dump(self.doc, out)

    def _run(self, **over):
        kwargs = {"expected_op_type": "Widget",
                  "expected_content_anchor": self.anchor}
        kwargs.update(over)
        env = {
            "OPRUNWAY_CPP_EXTENSION_VENDOR_BUILD_RECEIPT": self.receipt,
            "OPRUNWAY_CPP_EXTENSION_VENDOR_LIBRARY": self.library,
        }
        with mock.patch.dict(os.environ, env), mock.patch.object(
                V, "validate_for_acceptance", return_value={"status": "VERIFIED"}):
            return A.preflight_target_kernel_delivery(**kwargs)

    def test_matching_spec_and_source_anchor_pass(self):
        self.assertEqual(self._run()["status"], "VERIFIED")

    def test_other_op_or_source_anchor_is_rejected_before_dut(self):
        with self.assertRaisesRegex(A.CppExtensionAdapterError, "kernel_op_type"):
            self._run(expected_op_type="OtherOp")
        with self.assertRaisesRegex(A.CppExtensionAdapterError, "source_facts"):
            self._run(expected_content_anchor={"sha256": "b" * 64})


if __name__ == "__main__":
    unittest.main()
