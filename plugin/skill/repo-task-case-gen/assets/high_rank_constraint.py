"""高秩用例的约束器示例——**文档的 rank 上界 >4 时，把 `after_case_config` 里
那段抄进你自己的约束器**。单独跑也行（把注册名改成 `<Op>_constraint`）。

## 为什么高秩不能交给 ATK 抽

ATK 抽 shape 是拒绝采样：从 `dim_values` 抽 rank 个轴长相乘，超出元素预算就
原地重抽（`parameter_tensor.py:158`，ATK 自己在那行上注了「max_number_ele较小时
可能无限循环」并加了 300 秒守卫）。**门槛低到反直觉**：fp32 的元素上限是 2^20，
八根轴相乘要过关，几何平均必须小于 5.7，而 `skeleton.yaml` 那份 `dim_values`
的 32 个值里只有 1、2、3 三个够格。实测 rank 8 的接受率是 0.01%，一万两千次
才中一次，重抽到守卫抛错，整轮 `atk case` 直接死掉。

## 为什么不在 YAML 里按秩分池

`dim_values` 写成 list 时 ATK 按**轴的位置**取（`parameter_tensor.py:106-108`），
不是按秩，规定不了「rank=8 时所有轴都用小池子」。按轴分池也不行——rank 1 只用
第 0 根轴，要上 large 档它必须能抽到 2^20，而 rank 8 也从第 0 根轴开始抽。
两个钩子（`after_input_config` / `after_case_config`）又都在形状生成之后跑
（`base_generator.py:178-190`），拦不住那轮采样。

**所以只剩一条路：低秩照抽，高秩在这里现算轴长池、直接写形状。**
每根轴的上限就是预算开 rank 次方，这样乘积必定在预算内，一次都不用重抽。

## 用法

改下面三个常量，把 `after_case_config` 里标了箭头的那段抄走。
改完跑 `check_coverage.py`，秩那一行会拿 `facts.json` 的 `shape.rank` 对账，
告诉你补齐没有。

**别在 `dim_values` 里塞 0 来绕过。** 那样确实抽得出来，但高秩会全部塌成空张量
——`numel == 0` 无条件通过预算检查，而覆盖报告照样显示满格。
"""

import random

from atk.case_generator.generator.generate_types import GENERATOR_REGISTRY
from atk.case_generator.generator.generate_types.default import DefaultGenerator
from atk.configs.case_config import CaseConfig

# 改这三个：
HIGH_RANKS = (8,)      # 要补的秩，取 facts.json 的 shape.rank 上界；多档就写 (5, 8)
# 取质数：这一段常被抄进已有的约束器，与那边的周期共用同一个 self._seq。
# EVERY 与那些周期不互质时，补出来的高秩用例会全部落进那根轴的同一个取值
# （30 与周期 3 就是这样），而覆盖量具查不出来——见 plugin-authoring.md
# 「一个约束器里的多个轮转周期必须两两互质」。
EVERY = 31             # 每多少条补一条。总条数 / EVERY ≈ 每个 dtype 摊到一条
MAX_LENGTH = 4194304   # 与 YAML 的 shapes.max_length 一致

# 轴长候选取 skeleton.yaml 那份 dim_values 的小值段，保留 2^n / 2^n±1 的成对结构。
# 只需要小值段：大值在任何高秩下都会被下面的上限筛掉。
CANDIDATES = (1, 2, 3, 7, 8, 9, 15, 16, 17, 31, 32, 33, 63, 64, 65)
# 元素预算按**最窄 dtype**取最坏情况：get_max_number_ele 对 fp64/int64/complex64
# 除以 8（parameter_tensor.py:41-50），所以除以 8 的这份对所有 dtype 都安全。
BUDGET_ELEMENTS = MAX_LENGTH // 8


def _axis_pool(rank):
    """rank 根轴相乘不超预算 -> 每根轴的上限是预算开 rank 次方。"""
    ceiling = BUDGET_ELEMENTS ** (1.0 / rank)
    return [v for v in CANDIDATES if v <= ceiling] or [1]


def _rank_shape(rank, batch):
    """按 batch 定种子，同一份 YAML 重跑得到同一批形状，golden 才可复现。"""
    pool = _axis_pool(rank)
    rnd = random.Random(batch)
    return [rnd.choice(pool) for _ in range(rank)]


@GENERATOR_REGISTRY.register("high_rank_constraint")
class HighRankConstraint(DefaultGenerator):

    def after_case_config(self, case_config: CaseConfig) -> CaseConfig:
        # ↓↓↓ 抄这一段，它自包含，不依赖你的 __init__ ↓↓↓
        # case_config.id 在这一步恒为 0，要按序号分批就自己数（plugin-authoring.md）。
        # 用 getattr 惰性初始化，这样单抄这段进任何约束器都不会 AttributeError。
        # 你的约束器里已经有 self._seq 时，删掉下面这行，改用你自己那份的序号。
        self._seq = getattr(self, "_seq", 0) + 1
        if self._seq % EVERY:
            return case_config

        batch = self._seq // EVERY
        rank = HIGH_RANKS[batch % len(HIGH_RANKS)]
        shape = _rank_shape(rank, batch)
        for item in case_config.inputs:
            # 张量列表输入拿到的是 list，单张量拿到的是对象；两种都要改到，
            # 否则列表算子只改了第一个张量，其余的秩对不上。
            for tensor in (item if isinstance(item, list) else [item]):
                if getattr(tensor, "shape", None) is None:
                    continue          # attr / scalar 参数没有 shape，跳过
                tensor.shape = list(shape)
        # ↑↑↑ 抄到这里 ↑↑↑
        return case_config
