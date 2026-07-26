#!/usr/bin/env python3
"""durable dry-run ledger：结构化账本、字段能力展示与 CLI 原子落盘。"""

import contextlib
import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import gen_cases as GC
import content_address


_HERE = os.path.dirname(os.path.abspath(__file__))
_SIGN_SPEC = os.path.join(_HERE, "..", "samples", "specs", "sign.spec.json")


def _spec():
    with open(_SIGN_SPEC, encoding="utf-8") as fh:
        return json.load(fh)


class DryRunLedgerTest(unittest.TestCase):
    def _build_without_golden(self, spec):
        with mock.patch.object(GC, "load_golden", side_effect=ValueError("缺 golden: test fixture")):
            return GC._build_dry_run_ledger(spec)

    def test_builder_is_deterministic_and_binds_canonical_spec(self):
        spec = _spec()
        first = self._build_without_golden(spec)
        second = self._build_without_golden(copy.deepcopy(spec))
        self.assertEqual(first, second)
        self.assertEqual(first["schema"], "oprunway.gen_cases.dry_run_ledger")
        self.assertEqual(first["schema_version"], 1)
        self.assertEqual(len(first["spec_binding"]["sha256"]), 64)
        self.assertEqual(first["planner_binding"]["implementation"], "gen_cases.py::_plan")
        self.assertEqual(len(first["planner_binding"]["gen_cases_py_sha256"]), 64)
        self.assertEqual(
            set(first["planner_binding"]["logic_files"]),
            {"gen_cases.py", "repo_adapter.py", "precision_policy.py"})
        self.assertRegex(first["ledger_digest"], r"^[0-9a-f]{64}$")
        self.assertEqual(first["golden_dependency"]["status"], "missing")
        changed = copy.deepcopy(spec)
        changed["precision"]["case_target"] = changed["precision"].get("case_target", 50) + 1
        self.assertNotEqual(
            first["spec_binding"]["sha256"],
            self._build_without_golden(changed)["spec_binding"]["sha256"],
        )
        self.assertTrue(first["determinism"]["equal"])

    def test_renderer_keeps_default_stdout_shape(self):
        ledger = self._build_without_golden(_spec())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            GC._render_dry_run_ledger(ledger)
        text = buf.getvalue()
        self.assertIn("[dry-run] Sign target=", text)
        self.assertIn("by_dtype", text)
        self.assertIn("case_profile: 未声明（缺省 = legacy = 现行为）", text)
        self.assertIn("golden_cost:", text)

    def test_equal_nan_display_is_field_driven_not_op_name_driven(self):
        spec = _spec()
        spec["op"] = "AnyDomainOperator"
        spec["params"].append(
            {"name": "equal_nan", "io": "attr", "dtype": ["bool"], "default": False}
        )
        spec["attr_matrix"] = [{"equal_nan": False}, {"equal_nan": True}]
        ledger = self._build_without_golden(spec)
        self.assertEqual(ledger["summary"]["equal_nan_values_seen"], ["False", "True"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            GC._render_dry_run_ledger(ledger)
        self.assertIn("equal_nan values seen: ['False', 'True']", buf.getvalue())

    def test_cli_ledger_out_writes_valid_json_and_no_temp_file(self):
        with tempfile.TemporaryDirectory() as td:
            spec_path = os.path.join(td, "spec.json")
            ledger_path = os.path.join(td, "ledger.json")
            with open(spec_path, "w", encoding="utf-8") as fh:
                json.dump(_spec(), fh)
            with mock.patch.object(GC, "load_golden", side_effect=ValueError("缺 golden: test fixture")):
                with contextlib.redirect_stdout(io.StringIO()):
                    GC.main([spec_path, "--dry-run", "--ledger-out", ledger_path])
            with open(ledger_path, encoding="utf-8") as fh:
                saved = json.load(fh)
            self.assertEqual(saved["schema_version"], 1)
            self.assertEqual(saved["spec_binding"]["op"], "Sign")
            core = dict(saved)
            digest = core.pop("ledger_digest")
            import content_address
            self.assertEqual(
                digest,
                content_address.content_digest("oprunway/case-plan/v1", core))
            self.assertFalse(any(name.startswith((".ledger.json.tmp-", ".oprunway-tmp-"))
                                 for name in os.listdir(td)))

    def test_cli_binds_source_facts_and_correspondence(self):
        with tempfile.TemporaryDirectory() as td:
            spec_path = os.path.join(td, "spec.json")
            ledger_path = os.path.join(td, "ledger.json")
            source_path = os.path.join(td, "source_facts.json")
            correspondence_path = os.path.join(td, "correspondence.json")
            with open(spec_path, "w", encoding="utf-8") as fh:
                json.dump(_spec(), fh)
            source_payload = {"completeness": {"status": "complete"}}
            content_address.write_artifact(
                td, "source_facts.json", "oprunway/source-facts/v1",
                source_payload)
            source_digest = content_address.content_digest(
                "oprunway/source-facts/v1", source_payload)
            correspondence = {
                "status": "confirmed",
                "source_facts_digest": source_digest,
                "confirmed_constraints": [{"key": "dtype_required",
                                           "value": ["float32"],
                                           "source": "user"}],
            }
            with open(correspondence_path, "w", encoding="utf-8") as fh:
                json.dump(correspondence, fh)
            with mock.patch.object(
                    GC, "load_golden",
                    side_effect=ValueError("缺 golden: test fixture")):
                with contextlib.redirect_stdout(io.StringIO()):
                    GC.main([
                        spec_path, "--dry-run", "--ledger-out", ledger_path,
                        "--source-facts", source_path,
                        "--correspondence", correspondence_path,
                    ])
            with open(ledger_path, encoding="utf-8") as fh:
                ledger = json.load(fh)
            self.assertEqual(
                ledger["preparation_inputs"]["source_facts_digest"],
                source_digest)
            self.assertEqual(
                ledger["preparation_inputs"]["correspondence_sha256"],
                hashlib.sha256(
                    content_address.canonical_json_bytes(
                        correspondence)).hexdigest())

    def test_ledger_out_requires_dry_run(self):
        with self.assertRaisesRegex(ValueError, "仅与 --dry-run"):
            GC.main(["--ledger-out", "unused.json"])

    def test_source_bindings_require_dry_run(self):
        with self.assertRaisesRegex(ValueError, "仅与 --dry-run"):
            GC.main([
                "spec.json", "--source-facts", "source.json",
                "--correspondence", "correspondence.json",
            ])

    def test_formal_mode_rejects_extra_arguments(self):
        with self.assertRaisesRegex(ValueError, "正式用法"):
            GC.main(["spec.json", "work", "caseset.json", "ignored"])


if __name__ == "__main__":
    unittest.main()
