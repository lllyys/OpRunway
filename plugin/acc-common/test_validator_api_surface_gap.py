import unittest

import validator


def _gap(**updates):
    gap = {
        "kind": "api_surface_unsupported_by_pr",
        "overload": "torch.example(input)",
        "task_doc_ref": "功能要求第 1 条",
        "pr_header_ref": "op_host/op_api/aclnn_example.h",
        "reason": "PR head 未提供可执行 ABI",
    }
    gap.update(updates)
    return gap


class ApiSurfaceGapTest(unittest.TestCase):
    def test_valid_gap_is_preserved_and_forces_fail(self):
        gap = _gap()
        accepted, problems = validator._api_surface_gaps(
            {"task_pr_gaps": [gap]}, {"task_pr_gaps": [gap]})
        self.assertEqual(problems, [])
        self.assertEqual(accepted, [gap])

        verdict = validator._verdict(
            "Example", "numerical", "ascendoptest_default",
            [], [{"case_id": "p0", "功能": "pass", "精度": "pass",
                  "性能": "na", "catlass_compare_pass": "na",
                  "standard_profile_pass": True,
                  "acceptance_precision_pass": True, "risk": False,
                  "判据": "ok", "evidence_ref": "p0"}],
            gaps=accepted)
        self.assertEqual(verdict["overall"]["verdict"], "fail")
        self.assertEqual(verdict["overall"]["gaps"], [gap])

    def test_missing_evidence_field_is_contract_problem(self):
        gap = _gap(pr_header_ref="")
        accepted, problems = validator._api_surface_gaps(
            {"task_pr_gaps": [gap]}, {"task_pr_gaps": [gap]})
        self.assertEqual(accepted, [])
        self.assertTrue(any("pr_header_ref" in item for item in problems))

    def test_caseset_cannot_drop_gap(self):
        accepted, problems = validator._api_surface_gaps(
            {"task_pr_gaps": [_gap()]}, {"task_pr_gaps": []})
        self.assertEqual(accepted, [])
        self.assertTrue(any("与 spec 不一致" in item for item in problems))


if __name__ == "__main__":
    unittest.main()
