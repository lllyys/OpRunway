import json
import math
import os
from pathlib import Path

from atk.case_generator.generator.base_generator import CaseGenerator
from atk.case_generator.generator.generate_types import GENERATOR_REGISTRY
from atk.configs.case_config import InputCaseConfig


_TASK_CASES_ENV = "OPRUNWAY_TASK_CASES_ROOT"
_OFFICIAL_CASES = "gaussian_blur_cases.json"
_OFFICIAL_GOLDEN = "gaussian_blur_golden.py:gaussian_blur_golden"
_TASKDOC_S1 = ([1024, 1024], [5, 5], 1.2, 1.2, 4)


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(float(value)):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def _official_scenarios():
    root_text = os.environ.get(_TASK_CASES_ENV)
    if not root_text:
        raise ValueError(f"{_TASK_CASES_ENV} is required")
    root = Path(root_text)
    cases_path = root / _OFFICIAL_CASES
    if root.is_symlink() or cases_path.is_symlink() or not cases_path.is_file():
        raise ValueError("official GaussianBlur cases file is unavailable")
    try:
        values = json.loads(cases_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot parse official GaussianBlur cases: {exc}") from exc
    if not isinstance(values, list) or not values:
        raise ValueError("official GaussianBlur cases must be a non-empty list")

    scenarios = []
    for index, case in enumerate(values):
        label = f"official GaussianBlur case {index}"
        if not isinstance(case, dict) or case.get("case_name") != f"Test_{index + 1:03d}" \
                or case.get("op_name") != "GaussianBlur" \
                or case.get("expect_func") != _OFFICIAL_GOLDEN:
            raise ValueError(f"{label} identity is invalid")
        inputs = case.get("input_desc")
        outputs = case.get("output_desc")
        attrs = case.get("attr_desc")
        if not isinstance(inputs, list) or len(inputs) != 1 or not isinstance(inputs[0], dict) \
                or not isinstance(outputs, list) or len(outputs) != 1 or not isinstance(outputs[0], dict) \
                or not isinstance(attrs, list) or len(attrs) != 4 \
                or any(not isinstance(item, dict) for item in attrs):
            raise ValueError(f"{label} schema is invalid")
        source = inputs[0]
        output = outputs[0]
        shape = source.get("shape")
        value_range = source.get("value_range")
        if source.get("name") != "self" or source.get("format") != "ND" \
                or source.get("data_type") != "float" or source.get("param_type") != "required" \
                or not isinstance(shape, list) or len(shape) not in {2, 3} \
                or any(isinstance(dim, bool) or not isinstance(dim, int) or dim <= 0 for dim in shape) \
                or not isinstance(value_range, list) or len(value_range) != 2:
            raise ValueError(f"{label} input is invalid")
        low = _number(value_range[0], f"{label} range low")
        high = _number(value_range[1], f"{label} range high")
        if low > high or output.get("name") != "out" or output.get("format") != "ND" \
                or output.get("data_type") != "float" or output.get("shape") != shape:
            raise ValueError(f"{label} output/range is invalid")
        by_name = {item.get("name"): item for item in attrs}
        if len(by_name) != 4 or set(by_name) != {"ksize_x", "ksize_y", "sigmaX", "sigmaY"}:
            raise ValueError(f"{label} attributes are invalid")
        kx = by_name["ksize_x"].get("value")
        ky = by_name["ksize_y"].get("value")
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value % 2 == 0
               for value in (kx, ky)):
            raise ValueError(f"{label} kernel is invalid")
        sigma_x = _number(by_name["sigmaX"].get("value"), f"{label} sigmaX")
        sigma_y = _number(by_name["sigmaY"].get("value"), f"{label} sigmaY")
        if sigma_x < 0 or sigma_y < 0:
            raise ValueError(f"{label} sigma must be non-negative")
        scenarios.append(
            (case["case_name"], list(shape), [low, high], [kx, ky], sigma_x, sigma_y, 4)
        )
    return scenarios


@GENERATOR_REGISTRY.register("gaussian_blur_witness")
class GaussianBlurWitnessGenerator(CaseGenerator):
    """Project every official self-test case, then append taskdoc performance S1."""

    def __init__(self, config):
        super().__init__(config)
        self._scenarios = _official_scenarios()
        self.length = len(self._scenarios) + 1

    def after_case_config(self, case_config):
        index = self.index - 1
        if not 0 <= index < self.length:
            raise ValueError(f"ATK generated unexpected GaussianBlur witness index {self.index}")
        if index < len(self._scenarios):
            name, shape, value_range, kernel, sx, sy, border_type = self._scenarios[index]
        else:
            shape, kernel, sx, sy, border_type = _TASKDOC_S1
            name, value_range = "Taskdoc_S1", [0.0, 1.0]

        src, _ksize, sigma_x, sigma_y, border = case_config.inputs
        case_config.name = name
        src.dtype = "fp32"
        src.shape = list(shape)
        src.range_values = list(value_range)
        case_config.inputs[1] = [
            InputCaseConfig(
                name="ksize", type="attr_tuple", required=True, dtype="int", range_values=value
            )
            for value in kernel
        ]
        sigma_x.dtype = sigma_y.dtype = "double"
        sigma_x.range_values = sx
        sigma_y.range_values = sy
        border.dtype = "int"
        border.range_values = border_type
        case_config.expected_error_msg = None
        return case_config
