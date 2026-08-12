from atk.case_generator.generator.base_generator import CaseGenerator
from atk.case_generator.generator.generate_types import GENERATOR_REGISTRY


@GENERATOR_REGISTRY.register("remainder_witness")
class RemainderWitnessGenerator(CaseGenerator):
    """Keep dtype parity and force representative broadcast relations."""

    _SHAPES = (
        ([2, 8], [1, 8]),
        ([2, 3, 4], [1, 3, 1]),
        ([2, 1, 2, 1, 2, 1, 2, 1], [1, 1, 2, 1, 1, 1, 2, 1]),
    )
    _EXPECTED_CASES = 12

    def __init__(self, config):
        super().__init__(config)
        self.length = self._EXPECTED_CASES

    def after_case_config(self, case_config):
        raw_index = self.index - 1
        if not 0 <= raw_index < self._EXPECTED_CASES:
            raise ValueError(f"ATK generated unexpected Remainder witness index {self.index}")
        index = raw_index % len(self._SHAPES)
        left, right = case_config.inputs
        left.shape, right.shape = [list(shape) for shape in self._SHAPES[index]]
        right.dtype = left.dtype
        left.range_values = [-8, 8]
        right.range_values = [1, 8]
        return case_config
