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

        self_t, dim, index, value = case_config.inputs[:4]

        # value 的 dtype 跟 self——文档写「value 需要可转化为 self 的数据类型」，
        # 而 ATK 对每个参数独立抽 dtype。不对齐时 uint8 的 value 抽到负值、
        # self 是 int8，torch 侧报「value cannot be converted without overflow」，
        # 那几条 golden 冻不出来。**这一类漏项都长这样**：文档说「与 X 一致」
        # 「可转化为 X」的字段，YAML 表达不了，只能在这里对齐。
        value.dtype = self_t.dtype

        shape = self_t.shape or []
        rank = len(shape)
        if rank == 0:
            # 秩 0 是合法的一档，但 index 指不到任何轴。dim 与 index 都清空，
            # 让它退化成「原样返回」。**不能直接 return**——那等于放行 ATK
            # 随机生成的 dim 与 index，它们对 0 维张量一定越界。
            dim.range_values = 0
            index[:] = index[:1]
            for slot in index:
                slot.range_values = 0
            return case_config

        axis = random.randrange(rank)

        # 每 11 条构造 1 条退化维：dim 维长度为 1。
        # 11 与下面两个周期（2、3）互质——不互质的话，比如写成 10，
        # seq % 10 == 0 就蕴含 seq % 2 == 0，退化维这一批永远只有正轴。
        if seq % 11 == 0:
            shape[axis] = 1

        # dim 覆盖正负两侧，各占一半
        dim.range_values = axis if seq % 2 == 0 else axis - rank

        # index 元素值必须小于 self 在 dim 维的大小。
        # 每 3 条留 1 条允许重复下标——同一片被填两次是合法输入，
        # 全用 random.sample 会让这条路径一条用例都没有。
        span = shape[axis]
        keep = min(len(index), span)
        index[:] = index[:keep]
        if seq % 3 == 0:
            picked = random.choices(range(span), k=keep)
        else:
            picked = random.sample(range(span), k=keep)
        for slot, value in zip(index, picked):
            slot.range_values = value
        return case_config
