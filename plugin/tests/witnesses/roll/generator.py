from atk.case_generator.generator.base_generator import CaseGenerator
from atk.case_generator.generator.generate_types import GENERATOR_REGISTRY
from atk.configs.case_config import InputCaseConfig


@GENERATOR_REGISTRY.register("roll_witness")
class RollWitnessGenerator(CaseGenerator):
    """Force the task-document shift/dimension relations; ATK still emits the cases."""

    _SCENARIOS = (
        ([2, 3], [1], [1]),
        ([33], [-7], [0]),
        ([2, 3, 4], [1, -2], [0, 2]),
        ([2, 5], [2, -7], [1, 1]),
        ([3, 5], [1000000000001, -1000000000002], [0, 1]),
        ([2, 1, 1, 1, 1, 1, 1, 3], [5], [-1]),
        ([2, 3, 4], [5], []),
        ([], [1], []),
        ([0, 3], [1], [1]),
        ([6, 6], [1, -2], [0, 1]),
        ([2, 8], [4], [1]),
        ([2, 7], [-3], [1]),
    )
    _REGRESSION_DTYPES = ("bool", "uint8", "int8", "bf16", "fp16", "fp32", "int32", "uint32")

    def __init__(self, config):
        super().__init__(config)
        self.length = len(self._SCENARIOS) + len(self._REGRESSION_DTYPES)

    def after_case_config(self, case_config):
        index = self.index - 1
        expected_count = len(self._SCENARIOS) + len(self._REGRESSION_DTYPES)
        if not 0 <= index < expected_count:
            raise ValueError(f"ATK generated unexpected Roll witness index {self.index}")
        tensor = case_config.inputs[0]
        if index < len(self._SCENARIOS):
            tensor.dtype = "complex64"
            shape, shift_values, dim_values = self._SCENARIOS[index]
        else:
            tensor.dtype = self._REGRESSION_DTYPES[index - len(self._SCENARIOS)]
            shape, shift_values, dim_values = ([2, 3, 4], [1, -2], [0, 2])
        tensor.shape = list(shape)
        tensor.range_values = [-8, 8]
        case_config.inputs[1] = [
            InputCaseConfig(
                name=None, type="attr_tuple", required=True, dtype="int", range_values=value
            )
            for value in shift_values
        ]
        case_config.inputs[2] = [
            InputCaseConfig(
                name=None, type="attr_tuple", required=True, dtype="int", range_values=value
            )
            for value in dim_values
        ] or [InputCaseConfig(
            name="__oprunway_empty_tuple__", type="attr_tuple", required=True,
            dtype="int", range_values="default",
        )]
        return case_config
