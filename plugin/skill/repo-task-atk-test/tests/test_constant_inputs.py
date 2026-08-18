"""freeze_inputs 的常量输入检查：冻结下来的数据得测得出错。

roll 那轮真机事故：122 条用例里 17 条输入整张只有一个取值，全部判「通过」，
占通过总数 42 条的四成；uint32 分面唯一值中位数为 1（整张全 0）。
搬运类算子的错误置换与正确置换在这种输入上逐元素相等，用例必然通过，
却照样计入通过率分母——静默放行缺陷实现是验收最坏的结果。

根因已在 make_yaml 的取值范围推导里修掉（见 test_make_yaml）。
这里守的是不变量：万一 decl 的 range 被覆写回退化区间，冻结这一步要拦住。
"""

import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

try:
    import numpy as np
    import torch
except ImportError:  # pragma: no cover - 无 torch 的环境跳过
    torch = None

if torch is not None:
    from freeze_inputs import constant_tensors

# 这两个不碰张量，没有 torch 也判得了。
from freeze_inputs import VALUE_FREE_OUTPUT_CLASSES, constant_check_mode  # noqa: E402


@unittest.skipIf(torch is None, "需要 torch")
class ConstantInputTest(unittest.TestCase):
    def test_constant_tensor_is_reported(self):
        found = constant_tensors([torch.full((64,), 3, dtype=torch.uint8), [1]])
        self.assertEqual(1, len(found))
        self.assertEqual(("uint8", 64), found[0][1:])

    def test_all_zero_unsigned_tensor_is_reported(self):
        # 无符号 dtype 被负均值截断的典型形态，正是 roll 那轮的缺陷本身。
        self.assertTrue(constant_tensors([torch.zeros(64, dtype=torch.uint8)]))

    def test_varied_tensor_is_clean(self):
        data = torch.arange(256, dtype=torch.uint8).reshape(16, 16)
        self.assertEqual([], constant_tensors([data, [1]]))

    def test_scalar_and_empty_are_not_flagged(self):
        self.assertEqual([], constant_tensors([torch.zeros((), dtype=torch.float32)]))
        self.assertEqual([], constant_tensors([torch.zeros((0, 4))]))

    def test_boundary_constants_are_not_flagged(self):
        # 边界与 infnan 定向用例要的就是常量形态，拿它判失败会堵死合法设计。
        self.assertEqual([], constant_tensors([torch.full((32,), 255, dtype=torch.uint8)]))
        self.assertEqual([], constant_tensors([torch.full((32,), 127, dtype=torch.int8)]))
        self.assertEqual([], constant_tensors([torch.full((32,), -128, dtype=torch.int8)]))
        self.assertEqual([], constant_tensors([torch.full((32,), float("nan"))]))

    def test_uint_dict_wrapper_is_understood(self):
        # torch 没有 uint32，ATK 落盘成 {"__type__": "uint32_tensor", "data": ndarray}；
        # 裸当张量用会抛 AttributeError，roll 那轮的重放脚本就崩在这里。
        wrapped = {"__type__": "uint32_tensor", "data": np.zeros(64, dtype=np.uint32),
                   "shape": (64,)}
        found = constant_tensors([wrapped])
        self.assertEqual(1, len(found))
        self.assertEqual("uint32", found[0][1])

    def test_nested_attr_groups_do_not_shift_indices(self):
        payload = [torch.arange(16, dtype=torch.int8), [1, 2], [0, 1]]
        self.assertEqual([], constant_tensors(payload))


if __name__ == "__main__":
    unittest.main()


class SmokeCaseTest(unittest.TestCase):
    """冒烟只能跑常规通路，不能默认取 0 号。

    物化脚本习惯把定向用例排在前面。`expected_error_msg` 用例本来就该报错，
    空张量也可能被算子直接拒绝，而冒烟门禁要求成功 1 条、失败 0 条——
    拿定向用例冒烟会把构建安装的几十分钟成本连带作废，待验收对象却没问题。
    真机上踩过（median，2026-08-14）。
    """

    @staticmethod
    def case(case_id, shape, error=None):
        return {"id": case_id, "expected_error_msg": error,
                "inputs": [{"name": "x", "type": "tensor", "shape": shape}]}

    def pick(self, cases, frozen=None):
        from freeze_inputs import pick_smoke_case
        frozen = frozen if frozen is not None else {str(c["id"]) for c in cases}
        return pick_smoke_case(cases, frozen)

    def test_error_match_case_is_skipped(self):
        cases = [self.case(0, [2, 2], "invalid dim"), self.case(1, [4, 4])]
        self.assertEqual("1", self.pick(cases))

    def test_empty_tensor_case_is_skipped(self):
        cases = [self.case(0, [0, 4]), self.case(1, [4, 4])]
        self.assertEqual("1", self.pick(cases))

    def test_unmaterialized_case_is_skipped(self):
        cases = [self.case(0, [4, 4]), self.case(1, [4, 4])]
        self.assertEqual("1", self.pick(cases, frozen={"1"}))

    def test_first_normal_case_wins(self):
        cases = [self.case(0, [4, 4]), self.case(1, [8, 8])]
        self.assertEqual("0", self.pick(cases))

    def test_no_normal_case_returns_none(self):
        # 全是定向用例时不能瞎挑一条，要让 agent 看见这个事实。
        self.assertIsNone(self.pick([self.case(0, [0, 4]), self.case(1, [2], "boom")]))


class BaselineFailureGateTest(unittest.TestCase):
    """冻结那次跑测的 CPU 单节点就是 agent 写的基线插件。

    roll 那轮它已经全线失败，旧版只核输入落盘不核基线执行，门禁照样放行，
    错误推到 S3 冒烟才炸、S4 全量又炸 42/81——而失败信息当时就在这次跑测
    自己的日志里。
    """

    FAILING_LOG = (
        "[2026-08-15 15:47:36] [INFO] [ForkPoolWorker-3] [opp_tasks.py:370]"
        "  [case 18] start run CPU OPP TASK.\n"
        "[2026-08-15 15:47:36] [ERROR] [ForkPoolWorker-3] [opp_tasks.py:390]"
        "  case 18 run opp failed: torch.roll run error!\n"
        "[2026-08-15 15:47:37] [ERROR] [ForkPoolWorker-3] [opp_tasks.py:390]"
        "  case 42 run opp failed: torch.roll run error!\n"
    )

    CLEAN_LOG = (
        "[2026-08-15 15:47:36] [INFO] [ForkPoolWorker-3] [opp_tasks.py:370]"
        "  [case 18] start run CPU OPP TASK.\n"
        "[2026-08-15 15:47:36] [INFO] [ForkPoolWorker-3] [opp_executor.py:76]"
        "  [case 18] SUCCESS Execute OPP\n"
    )

    def test_failing_baseline_cases_are_collected(self):
        from freeze_inputs import baseline_failures
        self.assertEqual(baseline_failures(self.FAILING_LOG), ["18", "42"])

    def test_clean_log_yields_no_failures(self):
        from freeze_inputs import baseline_failures
        self.assertEqual(baseline_failures(self.CLEAN_LOG), [])


class InputBudgetGateTest(unittest.TestCase):
    """预算门禁并进冻结：roll 实测最大 8MB，默认预算 2GiB，差 250 倍，
    从没拦下过任何东西，却曾单独占一个脚本、一道门禁、一次调用。"""

    def test_default_budget_matches_the_deleted_scripts_constant(self):
        from freeze_inputs import DEFAULT_BUDGET_BYTES
        self.assertEqual(DEFAULT_BUDGET_BYTES, 2 * 1024 ** 3)

    def test_budget_script_is_gone(self):
        self.assertFalse(
            (SKILL_ROOT / "scripts" / "check_input_budget.py").exists())


class ValueFreeOutputClassTest(unittest.TestCase):
    """真机事故（bernoulli，2026-08-17）：随机生成类算子的 34 条用例卡在
    「输入是常量张量，测不出错」。

    这条判据的前提是**输出由输入算出**——搬运类的错置换与对置换在全同值
    输入上逐元素相等。随机生成类的输出由随机数流决定，输入张量只提供
    shape（任务书原话：「输入的张量用于指定 shape」），常量输入照样验得出
    输出的差异。把 range 撑宽只是让门禁闭嘴，测不出任何多的东西。
    """

    def test_generation_class_does_not_enforce_the_check(self):
        self.assertEqual("not_applicable",
                         constant_check_mode("generation"))

    def test_every_other_class_still_enforces_it(self):
        import sys as _sys
        from pathlib import Path as _Path
        _sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / "scripts"))
        from _coverage_strategy import OPERATOR_CLASSES
        for name in OPERATOR_CLASSES:
            if name in VALUE_FREE_OUTPUT_CLASSES:
                continue
            with self.subTest(operator_class=name):
                self.assertEqual("enforced",
                                 constant_check_mode(name))

    def test_unknown_or_missing_class_is_not_a_free_pass(self):
        self.assertEqual("enforced", constant_check_mode(None))
        self.assertEqual("enforced", constant_check_mode("matmul"))


if __name__ == "__main__":
    unittest.main()
