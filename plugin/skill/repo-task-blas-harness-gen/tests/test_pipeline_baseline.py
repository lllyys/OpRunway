"""零改动基线门（F-07 / 泛化性维度）。

name-scan（test_no_operator_specialcasing_in_code）只抓已知三算子的字面量，
抓不到未来的 `if symbol == "sspr"` 这类新特判。本门对算子无关的流水线逻辑文件
做整文件 SHA 比对：加新算子若改动了任何一个逻辑文件，无论改的是什么，一律红。

失配的两种可能与去向都写在 pipeline-baseline.json 的 _doc 里，失败信息也复述。
有意的流水线修复：`python3 tests/test_pipeline_baseline.py --regen` 重生清单。
"""
import hashlib
import json
import sys
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"
MANIFEST = SKILL / "tests" / "pipeline-baseline.json"


def _sha(name):
    return hashlib.sha256((SCRIPTS / name).read_bytes()).hexdigest()


def _regen():
    m = json.loads(MANIFEST.read_text("utf-8"))
    m["files"] = {name: _sha(name) for name in m["files"]}
    MANIFEST.write_text(json.dumps(m, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print("已重生 pipeline-baseline.json：")
    for k, v in m["files"].items():
        print(f"  {v[:16]}…  {k}")


class TestPipelineZeroChangeBaseline(unittest.TestCase):
    def test_logic_files_match_baseline(self):
        m = json.loads(MANIFEST.read_text("utf-8"))
        drift = []
        for name, want in m["files"].items():
            got = _sha(name)
            if got != want:
                drift.append(f"  {name}: 基线 {want[:16]}… → 现值 {got[:16]}…")
        self.assertEqual(
            drift, [],
            "流水线逻辑文件相对零改动基线发生变化：\n" + "\n".join(drift) +
            "\n算子无关逻辑加新算子应零改动。若误把特判写进逻辑请回退；"
            "若是有意的流水线修复，在同一提交里跑 "
            "`python3 tests/test_pipeline_baseline.py --regen` 重生清单。")

    def test_manifest_excludes_signature_table(self):
        # signature_table.json 是随 builtin_kernel 增长的数据，不得进逻辑清单，
        # 否则加一个 builtin 算子就会误触发本门。
        m = json.loads(MANIFEST.read_text("utf-8"))
        self.assertNotIn("signature_table.json", m["files"])
        self.assertTrue(all(n.endswith(".py") for n in m["files"]))


if __name__ == "__main__":
    if "--regen" in sys.argv:
        _regen()
    else:
        unittest.main()
