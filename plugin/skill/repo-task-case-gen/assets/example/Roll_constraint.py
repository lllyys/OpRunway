"""Roll 约束器：让 shifts 与 dims 等长、dims 落在 [-rank, rank) 且不重复。

约束来自 ops-test/roll/docs/aclnnRoll.md 的「函数原型」与「返回值」两节，
以及 README 的「约束与限制」。不读 op_kernel / op_host。
"""

import random

from atk.case_generator.generator.generate_types import GENERATOR_REGISTRY
from atk.case_generator.generator.generate_types.default import DefaultGenerator
from atk.configs.case_config import CaseConfig


@GENERATOR_REGISTRY.register("Roll_constraint")
class RollConstraint(DefaultGenerator):

    def after_case_config(self, case_config: CaseConfig) -> CaseConfig:
        inputs = case_config.inputs
        x, shifts, dims = inputs[0], inputs[1], inputs[2]
        rank = len(x.shape or [])
        if rank == 0:
            return case_config

        # dims 去重并落进 [-rank, rank)：一半写成负轴，覆盖归一化路径
        # 长度取三者最小：shifts 可能比 dims 短，只截不补会留下长度不等的用例
        length = min(len(shifts), len(dims), rank)
        picked = random.sample(range(rank), k=length)
        chosen = [(axis - rank) if index % 2 else axis for index, axis in enumerate(picked)]

        dims[:] = dims[:len(chosen)]
        for slot, value in zip(dims, chosen):
            slot.range_values = value

        # shifts 与 dims 等长，位移量取 [-dim, dim] 之间的整数
        shifts[:] = shifts[:len(chosen)]
        for slot, axis in zip(shifts, chosen):
            span = x.shape[axis]
            slot.range_values = random.randint(-span, span)

        return case_config
