"""按量具名查检查点：查到的必须与清单全文同源，且真的比通读省。

这个脚本替代的是「S2 前通读 gate-inventory.md」。替代品有两种失败方式：
查不到（agent 退回通读，白加一个脚本），或者查到的与清单全文不一样
（同一道门两种说法，agent 照哪份做都可能错）。两种都在这里钉住。
"""

import subprocess
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import _contracts  # noqa: E402

SCRIPT = SKILL_ROOT / "scripts" / "gate_lookup.py"
INVENTORY = SKILL_ROOT / "references" / "gate-inventory.md"


def lookup(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, timeout=30)


class LookupTest(unittest.TestCase):
    def setUp(self):
        self.data = _contracts.load()
        self.gates = self.data["gate_inventory"]

    def test_index_lists_every_gate(self):
        out = lookup().stdout
        for gate_id in self.gates:
            with self.subTest(gate=gate_id):
                self.assertIn(gate_id, out)

    def test_script_name_returns_only_that_script_gates(self):
        out = lookup("make_yaml.py").stdout
        wanted = [g for g, s in self.gates.items() if s["script"] == "make_yaml.py"]
        self.assertTrue(wanted)
        for gate_id in wanted:
            self.assertIn(f"### `{gate_id}`", out)
        for gate_id, spec in self.gates.items():
            if spec["script"] != "make_yaml.py":
                self.assertNotIn(f"### `{gate_id}`", out)

    def test_gate_name_returns_exactly_one_entry(self):
        out = lookup("make_yaml.header_keys").stdout
        self.assertEqual(1, out.count("### `"))
        self.assertIn("make_yaml.header_keys", out)

    def test_bare_script_name_without_suffix_also_hits(self):
        # agent 手上拿到的名字来自卡（`make_yaml.py`）或报错（`make_yaml.xxx`），
        # 也可能顺手只写一半。三种写法都要认，认不出就等于查不到。
        self.assertEqual(lookup("make_yaml").stdout, lookup("make_yaml.py").stdout)

    def test_unknown_name_exits_two_and_says_how_to_list(self):
        result = lookup("没有这个量具")
        self.assertEqual(2, result.returncode)
        self.assertIn("全部门名", result.stderr)

    def test_conditional_lists_exactly_the_gates_with_a_premise(self):
        out = lookup("--conditional").stdout
        conditional = dict(_contracts.conditional_gates(self.data))
        self.assertTrue(conditional)
        for gate_id, spec in self.gates.items():
            with self.subTest(gate=gate_id):
                if gate_id in conditional:
                    self.assertIn(f"### `{gate_id}`", out)
                else:
                    self.assertNotIn(f"### `{gate_id}`", out)

    def test_every_entry_is_verbatim_from_the_inventory(self):
        # 单条查询与清单全文共用 render_gate。两份写法一旦分家，
        # 查到的和读到的会慢慢不一样，而没有任何人会发现。
        inventory = INVENTORY.read_text(encoding="utf-8")
        for gate_id, spec in self.gates.items():
            with self.subTest(gate=gate_id):
                entry = "\n".join(_contracts.render_gate(gate_id, spec)).strip()
                self.assertIn(entry, inventory)
                self.assertIn(entry, lookup(gate_id).stdout)

    def test_a_single_query_is_much_cheaper_than_the_whole_inventory(self):
        # 省下来的上下文就是这个脚本存在的全部理由，量出来才算数。
        whole = len(INVENTORY.read_text(encoding="utf-8"))
        for script in sorted({spec["script"] for spec in self.gates.values()}):
            with self.subTest(script=script):
                self.assertLess(len(lookup(script).stdout), whole / 3)


if __name__ == "__main__":
    unittest.main()
