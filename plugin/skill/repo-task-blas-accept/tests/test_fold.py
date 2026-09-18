"""折叠核契约测试：spec §6 判定示例 13 条 + 9 个展开序列 + FoldResult golden。

全部输入是手工构造的假记录（plan v4 §2 的 Round 形状），不碰真机、不 mock 文件系统。
中断轮与无效轮按契约不进 fold 输入，对应示例（#12、#13）以「输入里没有那一轮」表达。
"""

import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from retest_fold import fold  # noqa: E402

CASE = "TC_PF_1001"


def _case(name, status, ratio=None):
    # plan v4 §2 收窄后的 CaseRec：折叠只收算法必要字段，展示与预热字段不进来。
    return {"name": name, "status": status, "ratio": ratio}


def _passed(name, ratio=1.20):
    return _case(name, "PASS", ratio)


def _failed(name, ratio=0.50):
    return _case(name, "FAIL", ratio)


def _measure(number, cases):
    return {"round": number, "kind": "measure", "cases": cases, "waivers": []}


def _waive(number, *entries):
    return {
        "round": number,
        "kind": "waive",
        "cases": [],
        "waivers": [{"case": case, "reason": reason} for case, reason in entries],
    }


def _entry(result, name=CASE):
    return result["per_case"][name]


class TestSpecExamples(unittest.TestCase):
    """spec §6 判定示例表逐条（# 号即表内行号）。"""

    def test_01_first_round_fail(self):
        result = fold([_measure(0, [_failed(CASE)])])
        entry = _entry(result)
        self.assertEqual(entry["effective_status"], "FAIL")
        self.assertEqual(entry["representative_round"], 0)

    def test_02_retest_pass(self):
        result = fold([
            _measure(0, [_failed(CASE)]),
            _measure(1, [_passed(CASE)]),
        ])
        entry = _entry(result)
        self.assertEqual(entry["effective_status"], "PASS")
        self.assertEqual(entry["representative_round"], 1)
        self.assertEqual(result["pass_on_retest"], 1)

    def test_03_retest_fail_larger_ratio(self):
        result = fold([
            _measure(0, [_failed(CASE, ratio=0.72)]),
            _measure(1, [_failed(CASE, ratio=0.75)]),
        ])
        entry = _entry(result)
        self.assertEqual(entry["effective_status"], "FAIL")
        self.assertEqual(entry["representative_round"], 1)

    def test_04_fail_then_crash_keeps_fail(self):
        # 规则 3 先于规则 4：有可比 FAIL 就不落到证据缺口。
        result = fold([
            _measure(0, [_failed(CASE)]),
            _measure(1, [_case(CASE, "CRASH")]),
        ])
        entry = _entry(result)
        self.assertEqual(entry["effective_status"], "FAIL")
        self.assertEqual(entry["representative_round"], 0)

    def test_05_no_kernel_then_fail(self):
        result = fold([
            _measure(0, [_case(CASE, "NO_KERNEL")]),
            _measure(1, [_failed(CASE)]),
        ])
        entry = _entry(result)
        self.assertEqual(entry["effective_status"], "FAIL")
        self.assertEqual(entry["representative_round"], 1)

    def test_06_no_kernel_then_timeout(self):
        result = fold([
            _measure(0, [_case(CASE, "NO_KERNEL")]),
            _measure(1, [_case(CASE, "TIMEOUT")]),
        ])
        entry = _entry(result)
        self.assertEqual(entry["effective_status"], "TIMEOUT")
        self.assertEqual(entry["representative_round"], 1)

    def test_07_pass_then_waived(self):
        result = fold([
            _measure(0, [_passed(CASE)]),
            _waive(1, (CASE, "上游已知缺陷")),
        ])
        entry = _entry(result)
        self.assertEqual(entry["effective_status"], "WAIVED")
        self.assertEqual(entry["representative_round"], 1)
        # PASS 证据保留展示：参考轮指向最近一次测量（首轮）。
        self.assertEqual(entry["reference_round"], 0)
        self.assertEqual(entry["waive_reason"], "上游已知缺陷")

    def test_08_waive_revoked_by_measure(self):
        result = fold([
            _measure(0, [_failed(CASE, ratio=0.70)]),
            _waive(1, (CASE, "暂缓")),
            _measure(2, [_failed(CASE, ratio=0.75)]),
        ])
        entry = _entry(result)
        self.assertEqual(entry["effective_status"], "FAIL")
        self.assertEqual(entry["representative_round"], 2)
        self.assertIsNone(entry["waive_reason"])

    def test_09_earliest_pass_survives_revoked_waive(self):
        result = fold([
            _measure(0, [_passed(CASE)]),
            _waive(1, (CASE, "暂缓")),
            _measure(2, [_failed(CASE)]),
        ])
        entry = _entry(result)
        self.assertEqual(entry["effective_status"], "PASS")
        self.assertEqual(entry["representative_round"], 0)

    def test_10_fail_then_waived_no_more_measures(self):
        result = fold([
            _measure(0, [_failed(CASE)]),
            _waive(1, (CASE, "范围外场景")),
        ])
        entry = _entry(result)
        self.assertEqual(entry["effective_status"], "WAIVED")
        self.assertEqual(entry["representative_round"], 1)
        self.assertEqual(entry["reference_round"], 0)

    def test_11_earliest_pass_over_later_fail(self):
        result = fold([
            _measure(0, [_failed(CASE)]),
            _measure(1, [_passed(CASE)]),
            _measure(2, [_failed(CASE)]),
        ])
        entry = _entry(result)
        self.assertEqual(entry["effective_status"], "PASS")
        self.assertEqual(entry["representative_round"], 1)

    def test_12_interrupted_round_keeps_waive(self):
        # 中断轮不进 fold 输入（加载层排除），豁免因此保持成立。
        result = fold([
            _measure(0, [_failed(CASE)]),
            _waive(1, (CASE, "待环境修复")),
        ])
        entry = _entry(result)
        self.assertEqual(entry["effective_status"], "WAIVED")
        self.assertEqual(entry["representative_round"], 1)

    def test_13_invalid_round_pass_not_counted(self):
        # 无效轮里测出的 PASS 不进历史：那一轮被加载层跳过，输入只剩首轮。
        result = fold([_measure(0, [_failed(CASE)])])
        entry = _entry(result)
        self.assertEqual(entry["effective_status"], "FAIL")
        self.assertEqual(entry["representative_round"], 0)
        self.assertEqual(result["pass_on_retest"], 0)


class TestExpandedSequences(unittest.TestCase):
    """多轮展开序列：折叠机制在计数、平局与交替声明下的行为。"""

    def test_e1_pass_once_between_gaps(self):
        result = fold([
            _measure(0, [_failed(CASE, ratio=0.70)]),
            _measure(1, [_case(CASE, "CRASH")]),
            _measure(2, [_passed(CASE)]),
            _measure(3, [_case(CASE, "TIMEOUT")]),
        ])
        entry = _entry(result)
        self.assertEqual(entry["effective_status"], "PASS")
        self.assertEqual(entry["representative_round"], 2)
        # 证据缺口轮也计入测量次数：它是「测过几次」不是「测出数值几次」。
        self.assertEqual(entry["measure_count"], 3)
        self.assertEqual(result["pass_on_retest"], 1)

    def test_e2_ratio_tie_takes_smaller_round(self):
        result = fold([
            _measure(0, [_failed(CASE, ratio=0.75)]),
            _measure(1, [_failed(CASE, ratio=0.75)]),
            _measure(2, [_failed(CASE, ratio=0.60)]),
        ])
        entry = _entry(result)
        self.assertEqual(entry["effective_status"], "FAIL")
        self.assertEqual(entry["representative_round"], 0)
        self.assertEqual(entry["measure_count"], 2)

    def test_e3_waive_revoked_by_gap_measure(self):
        # 撤销豁免的测量轮只测出缺口：历史里仍有首轮 FAIL，规则 3 先于规则 4。
        result = fold([
            _measure(0, [_failed(CASE, ratio=0.70)]),
            _waive(1, (CASE, "暂缓")),
            _measure(2, [_case(CASE, "CRASH")]),
        ])
        entry = _entry(result)
        self.assertEqual(entry["effective_status"], "FAIL")
        self.assertEqual(entry["representative_round"], 0)
        self.assertEqual(entry["measure_count"], 1)
        self.assertIsNone(entry["waive_reason"])

    def test_e4_double_waive_latest_reason_wins(self):
        result = fold([
            _measure(0, [_failed(CASE)]),
            _waive(1, (CASE, "理由甲")),
            _waive(2, (CASE, "理由乙")),
        ])
        entry = _entry(result)
        self.assertEqual(entry["effective_status"], "WAIVED")
        self.assertEqual(entry["representative_round"], 2)
        self.assertEqual(entry["waive_reason"], "理由乙")
        self.assertEqual(entry["reference_round"], 0)

    def test_e5_rewaive_after_pass_measure(self):
        # 最后声明是豁免轮 → WAIVED；PASS 记录保留在历史但不决定有效状态。
        result = fold([
            _measure(0, [_failed(CASE)]),
            _waive(1, (CASE, "暂缓")),
            _measure(2, [_passed(CASE)]),
            _waive(3, (CASE, "确认范围外")),
        ])
        entry = _entry(result)
        self.assertEqual(entry["effective_status"], "WAIVED")
        self.assertEqual(entry["representative_round"], 3)
        self.assertEqual(entry["reference_round"], 2)
        self.assertEqual(entry["waive_reason"], "确认范围外")
        self.assertEqual(entry["measure_count"], 1)
        # 有效状态不是 PASS，pass-once 的 PASS 证据不计入 PASS(复测)。
        self.assertEqual(result["pass_on_retest"], 0)

    def test_e6_gap_only_multi_round(self):
        result = fold([
            _measure(0, [_case(CASE, "NO_KERNEL")]),
            _measure(1, [_case(CASE, "MISSING")]),
            _measure(2, [_case(CASE, "CRASH")]),
        ])
        entry = _entry(result)
        self.assertEqual(entry["effective_status"], "CRASH")
        self.assertEqual(entry["representative_round"], 2)
        self.assertEqual(entry["measure_count"], 2)

    def test_e7_pass_on_retest_counting(self):
        # 首轮非 PASS 覆盖 FAIL 与证据缺口两类；首轮已 PASS 的不计。
        result = fold([
            _measure(0, [
                _passed("TC_PF_1001"),
                _failed("TC_PF_1002"),
                _case("TC_PF_1003", "NO_KERNEL"),
            ]),
            _measure(1, [_passed("TC_PF_1002"), _passed("TC_PF_1003")]),
        ])
        self.assertEqual(result["pass_on_retest"], 2)
        self.assertEqual(_entry(result, "TC_PF_1001")["measure_count"], 0)
        self.assertEqual(_entry(result, "TC_PF_1002")["representative_round"], 1)

    def test_e8_round_zero_only_passthrough(self):
        # 零复测：逐例状态原样透传，计数全零——兼容性要求（spec §9）。
        result = fold([
            _measure(0, [
                _passed("TC_PF_1001"),
                _failed("TC_PF_1002"),
                _case("TC_PF_1003", "TIMEOUT"),
            ]),
        ])
        for name, status in (
            ("TC_PF_1001", "PASS"),
            ("TC_PF_1002", "FAIL"),
            ("TC_PF_1003", "TIMEOUT"),
        ):
            entry = _entry(result, name)
            self.assertEqual(entry["effective_status"], status)
            self.assertEqual(entry["representative_round"], 0)
            self.assertEqual(entry["measure_count"], 0)
        self.assertEqual(result["pass_on_retest"], 0)
        self.assertEqual(result["warnings"], [])

    def test_e9_alternating_waive_measure_ends_revoked(self):
        result = fold([
            _measure(0, [_failed(CASE, ratio=0.60)]),
            _waive(1, (CASE, "第一次豁免")),
            _measure(2, [_failed(CASE, ratio=0.70)]),
            _waive(3, (CASE, "第二次豁免")),
            _measure(4, [_failed(CASE, ratio=0.75)]),
        ])
        entry = _entry(result)
        self.assertEqual(entry["effective_status"], "FAIL")
        self.assertEqual(entry["representative_round"], 4)
        self.assertEqual(entry["measure_count"], 2)
        self.assertIsNone(entry["waive_reason"])


class TestGolden(unittest.TestCase):
    def test_golden_fold_result(self):
        """FoldResult golden：完整字典逐键比对，接口形状回归在此先红。"""
        result = fold([
            _measure(0, [
                _passed("TC_PF_1001", ratio=1.10),
                _failed("TC_PF_1002", ratio=0.72),
                _case("TC_PF_1003", "NO_KERNEL"),
                _failed("TC_PF_1004", ratio=0.60),
                _failed("TC_PF_1005", ratio=0.55),
            ]),
            _measure(1, [
                _failed("TC_PF_1002", ratio=0.75),
                _passed("TC_PF_1003", ratio=1.05),
                _failed("TC_PF_1005", ratio=0.55),
            ]),
            _waive(2, ("TC_PF_1004", "上游缺陷已立项")),
            _measure(3, [_passed("TC_PF_1002", ratio=1.02)]),
        ])
        self.assertEqual(result, {
            "per_case": {
                "TC_PF_1001": {
                    "effective_status": "PASS",
                    "representative_round": 0,
                    "reference_round": None,
                    "waive_reason": None,
                    "measure_count": 0,
                },
                "TC_PF_1002": {
                    "effective_status": "PASS",
                    "representative_round": 3,
                    "reference_round": None,
                    "waive_reason": None,
                    "measure_count": 2,
                },
                "TC_PF_1003": {
                    "effective_status": "PASS",
                    "representative_round": 1,
                    "reference_round": None,
                    "waive_reason": None,
                    "measure_count": 1,
                },
                "TC_PF_1004": {
                    "effective_status": "WAIVED",
                    "representative_round": 2,
                    "reference_round": 0,
                    "waive_reason": "上游缺陷已立项",
                    "measure_count": 0,
                },
                "TC_PF_1005": {
                    "effective_status": "FAIL",
                    # 两轮同 ratio 0.55，平局取轮号小者。
                    "representative_round": 0,
                    "reference_round": None,
                    "waive_reason": None,
                    "measure_count": 1,
                },
            },
            "pass_on_retest": 2,
            "warnings": [],
        })


class TestShapeErrors(unittest.TestCase):
    """接口形状违规 → ValueError（plan v4 §2 错误边界）。"""

    def _rejects(self, rounds):
        with self.assertRaises(ValueError):
            fold(rounds)

    def test_rejects_empty_and_non_list(self):
        self._rejects([])
        self._rejects(None)

    def test_rejects_first_element_not_round_zero_measure(self):
        self._rejects([_measure(1, [_failed(CASE)])])
        self._rejects([_waive(0, (CASE, "理由"))])

    def test_rejects_non_ascending_rounds(self):
        self._rejects([
            _measure(0, [_failed(CASE)]),
            _measure(2, [_failed(CASE)]),
            _measure(1, [_failed(CASE)]),
        ])
        self._rejects([
            _measure(0, [_failed(CASE)]),
            _measure(1, [_failed(CASE)]),
            _measure(1, [_failed(CASE)]),
        ])

    def test_rejects_illegal_status(self):
        # NO_REF 不在测量记录合法状态集内：可比期望集内不该出现（spec §4.2）。
        self._rejects([_measure(0, [_case(CASE, "NO_REF")])])
        self._rejects([_measure(0, [_case(CASE, "SKIP")])])

    def test_rejects_field_inconsistency(self):
        self._rejects([_measure(0, [_case(CASE, "FAIL")])])          # FAIL 无 ratio
        self._rejects([_measure(0, [_case(CASE, "CRASH", 0.5)])])    # 缺口带 ratio

    def test_rejects_non_finite_ratio(self):
        # NaN 的比较恒 False、±Infinity 破坏全序，混进规则 3 会错选代表轮；
        # json 解码器默认放行这三个值，必须在形状校验处拒绝。
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=repr(bad)):
                self._rejects([_measure(0, [_case(CASE, "FAIL", bad)])])
                self._rejects([_measure(0, [_case(CASE, "PASS", bad)])])

    def test_rejects_shape_table(self):
        head = _measure(0, [_failed(CASE)])
        table = [
            ("轮非 dict", [head, "round-2"]),
            ("记录非 dict", [_measure(0, ["record"])]),
            ("布尔轮号", [{**_measure(0, [_failed(CASE)]), "round": False}]),
            ("负轮号", [head, {**_measure(1, [_failed(CASE)]), "round": -1}]),
            ("非法 kind", [head, {**_measure(1, [_failed(CASE)]), "kind": "audit"}]),
            ("空 case 名", [_measure(0, [_case("", "FAIL", 0.5)])]),
            ("cases 非 list", [{**_measure(0, []), "cases": (CASE,)}]),
            ("waivers 非 list",
             [head, {**_waive(1, (CASE, "理由")), "waivers": {"case": CASE}}]),
            ("waiver 项非 dict", [head, {**_waive(1), "waivers": [(CASE, "理由")]}]),
        ]
        for label, rounds in table:
            with self.subTest(label):
                self._rejects(rounds)

    def test_rejects_duplicate_case_in_round(self):
        self._rejects([_measure(0, [_failed(CASE), _failed(CASE)])])
        self._rejects([
            _measure(0, [_failed(CASE)]),
            _waive(1, (CASE, "甲"), (CASE, "乙")),
        ])

    def test_rejects_kind_payload_mismatch(self):
        crossed = _measure(1, [_failed(CASE)])
        crossed["waivers"] = [{"case": CASE, "reason": "混载"}]
        self._rejects([_measure(0, [_failed(CASE)]), crossed])
        crossed = _waive(1, (CASE, "理由"))
        crossed["cases"] = [_failed(CASE)]
        self._rejects([_measure(0, [_failed(CASE)]), crossed])

    def test_rejects_empty_waive_reason(self):
        self._rejects([
            _measure(0, [_failed(CASE)]),
            _waive(1, (CASE, "")),
        ])

    def test_rejects_empty_retest_measure(self):
        self._rejects([
            _measure(0, [_failed(CASE)]),
            _measure(1, []),
        ])


class TestRegressionLockins(unittest.TestCase):
    """评审锁定项：只有相邻规则间接覆盖的行为，各补一条直接回归。"""

    def test_empty_waive_round_is_noop(self):
        # 零 waivers 的豁免轮是合法的空声明：不豁免任何 case、不产生告警。
        result = fold([
            _measure(0, [_failed(CASE)]),
            _waive(1),
        ])
        entry = _entry(result)
        self.assertEqual(entry["effective_status"], "FAIL")
        self.assertEqual(entry["representative_round"], 0)
        self.assertIsNone(entry["waive_reason"])
        self.assertEqual(result["warnings"], [])

    def test_missing_record_revokes_waive(self):
        # 点名即再测：MISSING 占位记录构成撤销声明（spec §4.2 每个点名 case
        # 恰好一条记录 + §6「再测该 case 即撤销豁免」），不看测没测出数值。
        result = fold([
            _measure(0, [_failed(CASE, ratio=0.70)]),
            _waive(1, (CASE, "暂缓")),
            _measure(2, [_case(CASE, "MISSING")]),
        ])
        entry = _entry(result)
        self.assertNotEqual(entry["effective_status"], "WAIVED")
        self.assertEqual(entry["effective_status"], "FAIL")
        self.assertEqual(entry["representative_round"], 0)
        self.assertEqual(entry["measure_count"], 1)

    def test_multiple_pass_takes_earliest(self):
        result = fold([
            _measure(0, [_failed(CASE)]),
            _measure(1, [_passed(CASE)]),
            _measure(2, [_passed(CASE, ratio=1.50)]),
        ])
        entry = _entry(result)
        self.assertEqual(entry["effective_status"], "PASS")
        self.assertEqual(entry["representative_round"], 1)
        self.assertEqual(result["pass_on_retest"], 1)


class TestClosureGapWarning(unittest.TestCase):
    def test_case_missing_from_round_zero_warns(self):
        """首轮闭合缺口：复测轮出现首轮没有的 case——照常折叠并醒目告警。"""
        result = fold([
            _measure(0, [_failed("TC_PF_1001")]),
            _waive(1, ("TC_PF_1099", "首轮之外的名字")),
        ])
        entry = _entry(result, "TC_PF_1099")
        self.assertEqual(entry["effective_status"], "WAIVED")
        self.assertIsNone(entry["reference_round"])
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("TC_PF_1099", result["warnings"][0])


if __name__ == "__main__":
    unittest.main()
