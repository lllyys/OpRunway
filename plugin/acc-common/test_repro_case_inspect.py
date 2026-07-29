import json
import os
import tempfile
import unittest

import numpy as np

import repro_case_inspect as I


class ReproCaseInspectTest(unittest.TestCase):
    def test_describes_input_golden_and_original_result(self):
        with tempfile.TemporaryDirectory() as root:
            work = os.path.join(root, "work")
            os.makedirs(os.path.join(work, "c"))
            np.save(os.path.join(work, "c", "x.npy"),
                    np.asarray([1.0, 2.0, 3.0], dtype=np.float32))
            np.save(os.path.join(work, "c", "g.npy"),
                    np.asarray(2.0, dtype=np.float32))
            case = {
                "id": "c", "dims": ["精度"], "tags": ["small"],
                "inputs": [{"name": "self", "dtype": "float32", "shape": [3],
                            "path": "c/x.npy", "storage_dtype": "float32"}],
                "attrs": {"dim": None},
                "aclnn_call": {"symbol": "Median", "slots": []},
                "expected": {
                    "golden_source": "torch", "verify_mode": "numerical",
                    "case_origin": "fixture", "rule_ref": "test",
                    "outputs": [{"index": 0, "name": "valuesOut", "role": "value",
                                 "out_shape": [], "compare": "exact",
                                 "compare_dtype": "float32", "policy": {"kind": "exact"},
                                 "threshold": 0, "golden_path": "c/g.npy"}],
                },
            }
            for name, value in (
                ("caseset.json", {"cases": [case]}),
                ("verdict.json", {"per_case": [{"case_id": "c", "精度": "pass"}]}),
                ("evidence.json", {"evidence": [{"case_id": "c", "status": "ok"}]}),
            ):
                with open(os.path.join(root, name), "w", encoding="utf-8") as out:
                    json.dump(value, out)
            desc = I.inspect_case(root, "c")
            self.assertEqual(desc["inputs"][0]["summary"]["head"], [1.0, 2.0, 3.0])
            self.assertEqual(desc["expected"]["outputs"][0]["golden_summary"]["head"], [2.0])
            self.assertEqual(desc["original_verdict"]["精度"], "pass")
            self.assertIsNone(desc["acceptance_verdict"])
