from atk.case_generator.generator.base_generator import CaseGenerator
from atk.case_generator.generator.generate_types import GENERATOR_REGISTRY


@GENERATOR_REGISTRY.register("bernoulli_witness")
class BernoulliWitnessGenerator(CaseGenerator):
    """Emit task-authorized dtype, probability, rank and empty-tensor cases."""

    _DTYPES = ("bf16", "fp16", "fp32", "fp64", "uint8", "int8", "int16", "int32", "int64", "bool")
    _SPECIAL = (
        ([257], 0.0),
        ([257], 1.0),
        ([65536], 0.25),
        ([65536], 0.5),
        ([65536], 0.75),
        ([0, 3], 0.5),
        ([], 1.0),
        ([2, 1, 2, 1, 2, 1, 2, 1], 0.5),
        ([64, 64], 0.5),
    )

    def __init__(self, config):
        super().__init__(config)
        self.length = len(self._DTYPES) + len(self._SPECIAL)

    def after_case_config(self, case_config):
        index = self.index - 1
        expected_count = len(self._DTYPES) + len(self._SPECIAL)
        if not 0 <= index < expected_count:
            raise ValueError(f"ATK generated unexpected Bernoulli witness index {self.index}")
        self_tensor, probability, seed, offset = case_config.inputs
        if index < len(self._DTYPES):
            self_tensor.dtype = self._DTYPES[index]
            shape, prob = [32, 32], float(index % 2)
        else:
            special = index - len(self._DTYPES)
            shape, prob = self._SPECIAL[special]
            self_tensor.dtype = "fp32"
        self_tensor.shape = list(shape)
        self_tensor.range_values = [-1, 1]
        probability.dtype = "fp32"
        probability.range_values = prob
        seed.dtype = offset.dtype = "int"
        seed.range_values = 17 + index
        offset.range_values = (index % 4) * 4
        return case_config
