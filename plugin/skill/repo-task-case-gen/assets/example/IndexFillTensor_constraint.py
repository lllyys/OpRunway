"""IndexFillTensor 约束器：dim 落进 [-rank, rank)，index 元素值小于 self 在 dim 维的大小。

约束来自 docs/aclnnIndexFillTensor&aclnnInplaceIndexFillTensor.md 的参数说明表。
不读 op_kernel / op_host。

注意 case_config.id 在这个钩子里恒为 0（id 是钩子跑完才赋的），
要按序号分批必须自己数，见 references/plugin-authoring.md。
"""

import random

from atk.case_generator.generator.generate_types import GENERATOR_REGISTRY
from atk.case_generator.generator.generate_types.default import DefaultGenerator
from atk.configs.case_config import CaseConfig


@GENERATOR_REGISTRY.register("IndexFillTensor_constraint")
class IndexFillTensorConstraint(DefaultGenerator):

    def after_case_config(self, case_config: CaseConfig) -> CaseConfig:
        seq = getattr(self, "_seq", 0) + 1
        self._seq = seq

        self_t, dim, index = case_config.inputs[0], case_config.inputs[1], case_config.inputs[2]
        shape = self_t.shape or []
        rank = len(shape)
        if rank == 0:
            return case_config

        axis = random.randrange(rank)

        # 每 10 条构造 1 条退化维：dim 维长度为 1
        if seq % 10 == 0:
            shape[axis] = 1

        # dim 覆盖正负两侧，各占一半
        dim.range_values = axis if seq % 2 == 0 else axis - rank

        # index 元素值必须小于 self 在 dim 维的大小
        span = shape[axis]
        keep = min(len(index), span)
        index[:] = index[:keep]
        for slot, value in zip(index, random.sample(range(span), k=keep)):
            slot.range_values = value
        return case_config
