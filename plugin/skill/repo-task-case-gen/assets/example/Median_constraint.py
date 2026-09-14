"""Median 约束器：dim 落进 [-rank, rank)，并按批构造退化维场景。

约束来自 docs/aclnnMedian.md 的参数说明与约束说明两节。不读 op_kernel / op_host。

注意 case_config.id 在这个钩子里恒为 0（id 是钩子跑完才赋的），
要按序号分批必须自己数，见 references/plugin-authoring.md。
"""

import random

from atk.case_generator.generator.generate_types import GENERATOR_REGISTRY
from atk.case_generator.generator.generate_types.default import DefaultGenerator
from atk.configs.case_config import CaseConfig


@GENERATOR_REGISTRY.register("Median_constraint")
class MedianConstraint(DefaultGenerator):

    def after_case_config(self, case_config: CaseConfig) -> CaseConfig:
        seq = getattr(self, "_seq", 0) + 1
        self._seq = seq

        self_t, dim = case_config.inputs[0], case_config.inputs[1]
        shape = self_t.shape or []
        rank = len(shape)
        if rank == 0:
            return case_config

        # 文档写明不支持空张量，把 0 维长度拉回 1
        for index, span in enumerate(shape):
            if span == 0:
                shape[index] = 1

        # 每 8 条构造 1 条退化维（文档点名的场景：dim 维长度为 1）
        axis = random.randrange(rank)
        if seq % 8 == 0:
            shape[axis] = 1

        # dim 覆盖正负两侧，各占一半
        dim.range_values = axis if seq % 2 == 0 else axis - rank
        return case_config
