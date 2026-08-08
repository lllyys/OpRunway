import json
import os
import tempfile
import unittest

import multi_card_artifact_inventory as I


class MultiCardArtifactInventoryTest(unittest.TestCase):
    def test_recursive_closure_and_external_receipts(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as single_root:
            single = os.path.join(single_root, "work")
            external = os.path.join(root, "external.so")
            with open(external, "wb") as dst: dst.write(b"elf")
            receipt = {"vendor": {"library_path": external}, "runtime": {"cann": {
                "probe": {"defining_elf": {"path": external}}}}}
            for rel in ("spec.json", "parent-caseset.json", "caseset.json",
                        "source_facts.json", "vendor-build-receipt.json", "manifest.json",
                        "multi_card_manifest.json", "merged.json",
                        "multi_card_merged_evidence.json", "evidence.json", "verdict.json",
                        "equivalence.json", "gate-task2.log", "gate-task2.rc"):
                with open(os.path.join(root, rel), "w") as dst:
                    json.dump({"shards": [{"shard_id": "shard-000", "device_id": 1}]}
                              if rel == "multi_card_manifest.json" else {}, dst)
            work = os.path.join(root, "shard-000")
            smoke = work + ".pre-smoke"
            os.makedirs(os.path.join(root, "work"))
            with open(os.path.join(root, "work", "merged-out.bin"), "wb") as dst:
                dst.write(b"merged")
            for directory in (work, smoke, single):
                os.makedirs(os.path.join(directory, "nested"), exist_ok=True)
                with open(os.path.join(directory, "cpp_extension_receipt.json"), "w") as dst:
                    json.dump(receipt, dst)
                with open(os.path.join(directory, "nested", "out.bin"), "wb") as dst:
                    dst.write(b"out")
            for rel in ("shard_result.json", "device_identity.json"):
                with open(os.path.join(work, rel), "w") as dst: json.dump({}, dst)
            for rel in ("spec.json", "caseset.json", "source_facts.json",
                        "evidence.json", "verdict.json"):
                with open(os.path.join(single_root, rel), "w") as dst: json.dump({}, dst)
            got = I.build(root, single)
            self.assertEqual(got["schema_version"], 4)
            self.assertTrue(any(row["role"].endswith("nested/out.bin")
                                for row in got["shards"][0]["formal_artifacts"]))
            self.assertEqual(len(got["top_work_artifacts"]), 1)
            self.assertEqual(len(got["external_artifacts"]), 6)
            self.assertEqual(got["single_root"], os.path.realpath(single_root))
            self.assertEqual(
                {row["role"] for row in got["single_equivalence_inputs"]},
                set(I._SINGLE_EQUIVALENCE_INPUTS))
            root_paths = {row["path"] for row in got["single_root_artifacts"]}
            self.assertIn("verdict.json", root_paths)
            self.assertIn("work/cpp_extension_receipt.json", root_paths)
            os.symlink(external, os.path.join(work, "bad-link"))
            with self.assertRaises(ValueError): I.build(root, single)

    def test_single_work_must_be_fixed_work_directory(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as single:
            with self.assertRaisesRegex(ValueError, "固定 work"):
                I.build(root, single)


if __name__ == "__main__": unittest.main()
