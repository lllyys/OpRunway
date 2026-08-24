"""帮助文本不得与门禁的实际判据相反。

知识缺失让 agent 不知道，知识**错误**让 agent 确信地做错——后者更贵。

真机摩擦 F1（bernoulli，2026-08-17）：`derive_interface.py --help` 写
「cann_builtin 只能是 npu」，而 `check_baseline_shape()` 要求 cann_builtin 时
必须是 cpu。照帮助填必被拒。报错正文很清楚，但 agent 是先读帮助再填参数的，
于是先按错的填一遍、被拒、再读报错、再改——一轮白跑。

这类缺陷有个共同形状：**同一个取值在两处被断言，方向相反**。
帮助文本是 agent 写命令时读的，门禁是运行时判的，两者之间没有任何东西核对。

这份测试直接跑量具，拿门禁的真实行为去核对帮助文本的断言，
不靠人眼比对，也不靠记忆。
"""

import re
import subprocess
import sys
import unittest
from pathlib import Path

from _paths import SKILL_ROOT

SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def help_text(script):
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / script), "--help"],
        capture_output=True, text=True, timeout=30)
    return result.stdout


# 「X 只能是 Y」「X 必须是 Y」这类断言。帮助文本里出现它，
# 就得有一条门禁真的这么判，而且判的方向一致。
CLAIM = re.compile(r"(\w+)\s*(?:只能|必须)是\s*([a-z_]+)")


class BaselineDeviceHelpTest(unittest.TestCase):
    """F1 的回归锁：帮助与门禁对 cann_builtin 的基线后端要说同一件事。"""

    def setUp(self):
        import derive_interface
        self.gate = derive_interface.check_baseline_shape
        self.devices = derive_interface.BASELINE_DEVICES

    def _accepted(self, device):
        """门禁放不放这个后端过（给足其余必填项，只让 device 变化）。"""
        return self.gate("cann_builtin", device, "任务书 §3 要求与内置逐位比对") is None

    def test_gate_accepts_exactly_one_device_for_builtin(self):
        # 先确认门禁本身的形状：只有一个后端能过。
        # 能过的不止一个时，下面「帮助点名的那个」就不再是唯一正确答案。
        accepted = [d for d in self.devices if self._accepted(d)]
        self.assertEqual(1, len(accepted),
                         f"cann_builtin 放行的后端是 {accepted}，不再是唯一值")

    def test_help_names_the_device_the_gate_actually_accepts(self):
        accepted = [d for d in self.devices if self._accepted(d)][0]
        text = help_text("derive_interface.py")
        claims = [(subject, value) for subject, value in CLAIM.findall(text)
                  if "builtin" in subject]
        self.assertTrue(claims,
                        "帮助里没有关于 cann_builtin 基线后端的断言，"
                        "agent 只能猜或等着被门禁拒")
        for subject, value in claims:
            with self.subTest(claim=f"{subject} 只能是 {value}"):
                self.assertEqual(
                    accepted, value,
                    f"帮助说 {subject} 只能是 {value}，"
                    f"而 check_baseline_shape 实际只放行 {accepted}。"
                    "照帮助填必被拒——这正是真机摩擦 F1")


class HelpClaimsAreBackedTest(unittest.TestCase):
    """所有量具通用：帮助里点名的取值，必须真的在该参数的取值域里。

    比 F1 那条弱，但覆盖面大：帮助说「只能是 X」而 X 根本不是合法取值时，
    agent 照抄就是一条跑不通的命令。
    """

    # 带 --help 且不需要真机环境的量具。跑子进程，不 import，
    # 免得某个量具在 import 期就要 atk / torch。
    SCRIPTS_WITH_CHOICES = (
        ("derive_interface.py", "BASELINE_DEVICES"),
        ("derive_interface.py", "BASELINE_KINDS"),
    )

    def test_named_values_are_within_the_declared_domain(self):
        import derive_interface
        text = help_text("derive_interface.py")
        domains = {name: set(getattr(derive_interface, name))
                   for _, name in self.SCRIPTS_WITH_CHOICES}
        union = set().union(*domains.values())
        for subject, value in CLAIM.findall(text):
            with self.subTest(claim=f"{subject} 只能是 {value}"):
                self.assertIn(
                    value, union,
                    f"帮助点名 {value!r}，但它不在任何一个取值域里：{sorted(union)}")


class GateInventoryHelpTest(unittest.TestCase):
    """检查点清单登记的量具，帮助都要跑得起来。

    帮助跑不起来（import 期就炸、参数解析报错）时，agent 拿不到任何指引，
    只能去读脚本源码——那正是 skill 明令要避免的。
    """

    def test_every_inventoried_gauge_prints_help(self):
        import _contracts
        data = _contracts.load()
        scripts = sorted({spec["script"]
                          for spec in data["gate_inventory"].values()})
        for script in scripts:
            with self.subTest(script=script):
                result = subprocess.run(
                    [sys.executable, str(SCRIPTS / script), "--help"],
                    capture_output=True, text=True, timeout=60)
                self.assertEqual(0, result.returncode,
                                 f"{script} --help 退出码 {result.returncode}："
                                 f"{result.stderr[:400]}")
                self.assertTrue(result.stdout.strip(),
                                f"{script} --help 什么都没打印")


class ContradictedFactsStayFixedTest(unittest.TestCase):
    """被真机推翻过的说法，不许再以肯定形态出现在 reference 里。

    F1 与 F4 的共同教训不是「写错了」，是「错的说法读起来完全合理」，
    所以它会被再写一次。这条锁住的就是复发。

    判据形态是「肯定句不许出现，除非紧跟着否定它」——直接禁词做不到，
    因为正确的写法本身就要引用那句错话再驳倒它（atk-pitfalls.md 就是这么写的）。
    """

    REFERENCES = SKILL_ROOT / "references"

    def _files_asserting(self, claim):
        """哪些 reference 以肯定形态说了这句话。"""
        hits = []
        for path in self.REFERENCES.glob("*.md"):
            for number, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), 1):
                if claim not in line:
                    continue
                # 引用后驳倒是正确写法：同一行里有否定词，或行首是引号。
                if any(mark in line for mark in ("不会", "不自动", "只在", "「", "不是")):
                    continue
                hits.append(f"{path.name}:{number}")
        return hits

    def test_function_plugin_autoload_claim_stays_refuted(self):
        # F4：只有 atk aclnn / atk pytorch 别名入口会自动加载，
        # 本 skill 走的 atk node … task -c 不会。
        self.assertEqual(
            [], self._files_asserting("自动加载"),
            "有 reference 又把「function_*.py 自动加载」写成了肯定说法。"
            "本 skill 走 atk node … task -c，不加载；插件要靠 -p 给目录")

    def test_builtin_baseline_device_claim_stays_fixed(self):
        # F1：判定那一轮的基线节点是 cpu，不是 npu。
        offenders = []
        for path in self.REFERENCES.glob("*.md"):
            for number, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"cann_builtin[^。\n]*只能是\s*npu", line):
                    offenders.append(f"{path.name}:{number}")
        self.assertEqual(
            [], offenders,
            "cann_builtin 判定那轮的基线节点是 cpu；"
            "内置实现跑在 npu 上是第一轮的事，两者不是一回事")


if __name__ == "__main__":
    unittest.main()
