#!/usr/bin/env python3
"""公开算子身份与内部 kernel op type 的 source-bound 契约。"""

import copy
import unittest

import kernel_identity as K


ANCHOR = {
    "schema": "oprunway.source_content_anchor",
    "schema_version": 1,
    "algorithm": "git_blob_manifest_sha256_v1",
    "scope": "experimental/math/remainder",
    "sha256": "a" * 64,
    "file_count": 2,
}


class KernelIdentityTest(unittest.TestCase):
    def _fact(self, files=None):
        return K.discover(
            files or {"experimental/math/remainder/op_host/floor_mod_def.cpp":
                      "OP_ADD(FloorMod);\n"},
            target_scope=ANCHOR["scope"], content_anchor=ANCHOR)

    def test_public_remainder_can_bind_internal_floor_mod(self):
        fact = self._fact()
        spec = {"op": "Remainder", "execution": K.spec_execution(fact)}
        resolved = K.resolve(spec, {"pr": {"content_anchor": ANCHOR},
                                    "derived": {"kernel_identity": fact}})
        self.assertEqual(resolved["public_op"], "Remainder")
        self.assertEqual(resolved["kernel_op_type"], "FloorMod")

    def test_same_name_current_spec_without_execution_derives_exact_candidate(self):
        fact = self._fact({
            "experimental/math/remainder/op_host/widget_def.cpp": "OP_ADD(Widget);"})
        resolved = K.resolve({"op": "Widget"}, {
            "pr": {"content_anchor": ANCHOR}, "derived": {"kernel_identity": fact}})
        self.assertEqual(resolved["kernel_op_type"], "Widget")
        self.assertEqual(resolved["resolution"], "derived_exact_source_candidate")

    def test_zero_and_many_candidates_fail_closed(self):
        for files in ({"x/op_host/a_def.cpp": "int x;"},
                      {"x/op_host/a_def.cpp": "OP_ADD(A); OP_ADD(B);"}):
            fact = self._fact(files)
            with self.subTest(status=fact["status"]):
                with self.assertRaises(K.KernelIdentityError):
                    K.resolve({"op": "Public"}, {
                        "pr": {"content_anchor": ANCHOR},
                        "derived": {"kernel_identity": fact}})

    def test_comments_strings_chars_and_raw_strings_are_not_candidates(self):
        text = r'''
          // OP_ADD(LineComment)
          /* OP_ADD(BlockComment) */
          const char *a = "OP_ADD(StringLiteral)";
          const char c = 'x'; // " OP_ADD(StillComment)
          const char *r = R"tag(OP_ADD(RawLiteral))tag";
          OP_ADD(Actual);
        '''
        fact = self._fact({"x/op_host/a_def.cpp": text})
        self.assertEqual([c["kernel_op_type"] for c in fact["candidates"]], ["Actual"])

    def test_line_comment_continuation_cannot_inject_a_candidate(self):
        text = "// hidden \\\n  OP_ADD(Forged);\nOP_ADD(Actual);\n"
        self.assertEqual(K.scan_op_add(text), [("Actual", 3)])

    def test_scanned_paths_must_be_def_sources_inside_the_bound_scope(self):
        for path in ("outside/x_def.cpp", "experimental/math/remainder/not_def.txt",
                     "experimental/math/remainder/../outside/x_def.cpp"):
            with self.subTest(path=path):
                fact = self._fact({path: "OP_ADD(Forged);\n"})
                with self.assertRaises(K.KernelIdentityError):
                    K.validate(fact)

    def test_target_scope_must_equal_the_content_anchor_scope(self):
        fact = self._fact()
        fact["target_scope"] = "another/scope"
        with self.assertRaises(K.KernelIdentityError):
            K.validate(fact)

    def test_missing_or_drifted_explicit_binding_is_rejected(self):
        fact = self._fact()
        facts = {"pr": {"content_anchor": ANCHOR},
                 "derived": {"kernel_identity": fact}}
        for execution in ({"kernel_op_type": "FloorMod"},
                          {"kernel_op_type": "Other",
                           "source_binding": K.spec_execution(fact)["source_binding"]}):
            with self.subTest(execution=execution):
                with self.assertRaises(K.KernelIdentityError):
                    K.resolve({"op": "Remainder", "execution": execution}, facts)

    def test_coherent_spec_and_closure_tamper_cannot_override_source_fact(self):
        fact = self._fact()
        forged = copy.deepcopy(K.spec_execution(fact))
        forged["kernel_op_type"] = "Forged"
        forged["source_binding"]["candidate"]["kernel_op_type"] = "Forged"
        with self.assertRaises(K.KernelIdentityError):
            K.resolve({"op": "Remainder", "execution": forged}, {
                "pr": {"content_anchor": ANCHOR}, "derived": {"kernel_identity": fact}})

    def test_source_file_hash_drift_is_rejected(self):
        fact = self._fact()
        fact["candidates"][0]["source_sha256"] = "b" * 64
        with self.assertRaises(K.KernelIdentityError):
            K.resolve({"op": "Remainder"}, {
                "pr": {"content_anchor": ANCHOR}, "derived": {"kernel_identity": fact}})


if __name__ == "__main__":
    unittest.main()
