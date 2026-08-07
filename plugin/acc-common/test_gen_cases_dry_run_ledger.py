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
import _spec_fixture as SF


_HERE = os.path.dirname(os.path.abspath(__file__))
_SIGN_SPEC = os.path.join(_HERE, "..", "samples", "specs", "sign.spec.json")


def _spec():
    """读 `sign.spec.json`，并补上**测试侧**用例预算（`_spec_fixture`，仅当 spec 未声明时）。

    ⚠ 该样例已于 2026-08-06 删掉历史沿用的 `case_target: 50`（缺省值的化石、无覆盖矩阵依据），
    对 gen_cases 而言不可跑；账本测试关心的是账本结构与确定性，预算取多少不影响这些断言。
    """
    return SF.load(_SIGN_SPEC)


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
        # ⚠ 直接下标、**不许** `.get("case_target", 50)`：`_spec()` 已保证这个键在（要么样例
        #   自己声明了，要么 `_spec_fixture` 注了夹具预算）。留个 50 兜底看着无害，实则是把
        #   「注入这一步坏了」从当场 KeyError 降级成「悄悄拿 50 去扰动」——本轮删的正是这种
        #   「别处再兜一个缺省」。
        changed["precision"]["case_target"] = changed["precision"]["case_target"] + 1
        self.assertNotEqual(
            first["spec_binding"]["sha256"],
            self._build_without_golden(changed)["spec_binding"]["sha256"],
        )
        self.assertTrue(first["determinism"]["equal"])

    def test_ledger_pins_numpy_random_stream_identity(self):
        """B-1：账本必须钉住「数据是哪条随机流产的」——`seed` 钉不住这件事。"""
        import numpy as np
        binding = self._build_without_golden(_spec())["planner_binding"]
        self.assertEqual(binding["numpy_version"], np.__version__)
        self.assertEqual(binding["numpy_stream_pin"],
                         GC.numpy_stream_pin(np.__version__))
        self.assertEqual(binding["numpy_stream_pin_granularity"], "exact")
        # pin 必须真的是**收敛过的两段**，不是把全量版本原样抄一遍充数。
        # pin 是**完整版本**（本仓固定 numpy 1.26.4），不是「主.次」两段
        self.assertRegex(binding["numpy_stream_pin"], r"^\d+\.\d+\.")

    def test_numpy_stream_pin_is_exact_and_fails_closed(self):
        """pin 取**完整版本**，不做「主.次」收敛。

        初版按「主.次」收敛，被反例推翻：numpy 1.18.4 在补丁版里改了
        `Generator.integers(high=2**32)` 的取值，相对 1.18.3 输出就变了，
        而两者的「主.次」pin 都是 `1.18`——那个粒度探测不到真实发生过的流变更。
        本仓只支持 numpy 1.26.4，精确匹配也不会造成无谓 MISS。
        """
        self.assertEqual(GC.numpy_stream_pin("1.26.4"), "1.26.4")
        self.assertNotEqual(GC.numpy_stream_pin("1.18.3"), GC.numpy_stream_pin("1.18.4"))
        self.assertEqual(GC._NUMPY_STREAM_PIN_GRANULARITY, "exact")
        for bad in ("garbage", "1", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    GC.numpy_stream_pin(bad)

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
