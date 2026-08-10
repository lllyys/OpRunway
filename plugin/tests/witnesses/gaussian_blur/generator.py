from atk.case_generator.generator.base_generator import CaseGenerator
from atk.case_generator.generator.generate_types import GENERATOR_REGISTRY
from atk.configs.case_config import InputCaseConfig


@GENERATOR_REGISTRY.register("gaussian_blur_witness")
class GaussianBlurWitnessGenerator(CaseGenerator):
    """Emit the task's L1 channel, border, kernel and regression scenarios."""

    _SCENARIOS = (
        ([128, 256], [5, 5], 1.2, 1.2, 1),
        ([128, 256, 2], [5, 5], 1.2, 1.2, 1),
        ([128, 256, 3], [5, 5], 1.2, 1.2, 1),
        ([128, 256, 4], [5, 5], 1.2, 1.2, 1),
        ([96, 128, 3], [5, 5], 1.2, 1.2, 0),
        ([96, 128, 3], [5, 5], 1.2, 1.2, 1),
        ([96, 128, 3], [5, 5], 1.2, 1.2, 2),
        ([96, 128, 3], [5, 5], 1.2, 1.2, 4),
        ([0, 0], [5, 5], 1.2, 1.2, 1),
        ([64, 64], [0, 0], 1.5, 1.5, 1),
        ([1024, 1024], [5, 5], 1.2, 1.2, 1),
        ([64, 64], [1, 1], 0.0, 0.0, 1),
        ([64, 96], [3, 5], 1.2, 0.0, 1),
        ([64, 64, 3], [5, 5], 1.2, 1.2, 1),
    )

    def __init__(self, config):
        super().__init__(config)
        self.length = len(self._SCENARIOS)

    def after_case_config(self, case_config):
        index = self.index - 1
        if not 0 <= index < len(self._SCENARIOS):
            raise ValueError(f"ATK generated unexpected GaussianBlur witness index {self.index}")
        src, _ksize, sigma_x, sigma_y, border = case_config.inputs
        scenario = self._SCENARIOS[index]
        shape, kernel, sx, sy, border_type = scenario
        src.dtype = "fp32"
        src.shape = list(shape)
        src.range_values = [0.0, 1.0]
        case_config.inputs[1] = [
            InputCaseConfig(
                name="ksize", type="attr_tuple", required=True, dtype="int", range_values=value
            )
            for value in kernel
        ]
        border.dtype = "int"
        sigma_x.dtype = sigma_y.dtype = "double"
        sigma_x.range_values = sx
        sigma_y.range_values = sy
        border.range_values = border_type
        case_config.expected_error_msg = "GetWorkspaceSize failed" if not all(shape) else None
        return case_config
