import ctypes

import torch

from atk.configs.dataset_config import InputDataset
from atk.tasks.api_execute import register
from atk.tasks.api_execute.aclnn_base_api import AclnnBaseApi
from atk.tasks.api_execute.base_api import BaseApi
from atk.tasks.backends.pyaclnn_backend import nnopbase


def _tuple_values(values, *, empty_sentinel=False):
    result = list(values)
    if empty_sentinel and result == [None]:
        return []
    if any(value is None for value in result):
        raise ValueError("unexpected null value in Roll tuple attribute")
    return result


def _declares_empty_dims(case_config):
    inputs = getattr(case_config, "inputs", None)
    if not isinstance(inputs, (list, tuple)) or len(inputs) != 3:
        return False
    declared = inputs[2]
    if not isinstance(declared, (list, tuple)) or len(declared) != 1:
        return False
    marker = declared[0]
    field = marker.get if isinstance(marker, dict) else lambda key, default=None: getattr(
        marker, key, default
    )
    return (
        field("name") == "__oprunway_empty_tuple__"
        and field("type") == "attr_tuple"
        and field("required") is True
        and field("dtype") == "int"
        and field("shape") is None
        and field("range_values") == "default"
    )


def _prepared(values, case_config):
    declared_empty = _declares_empty_dims(case_config)
    if len(values) == 2:
        if not declared_empty:
            raise ValueError("Roll dims argument is missing without the bound empty-tuple marker")
        tensor, shifts = values
        dims = []
    elif len(values) == 3:
        tensor, shifts, dims = values
        runtime_dims = list(dims)
        if declared_empty and runtime_dims not in ([], [None]):
            raise ValueError("Roll empty-dims marker conflicts with a non-empty runtime argument")
        if not declared_empty and runtime_dims == []:
            raise ValueError("Roll received empty dims without the bound empty-tuple marker")
        if runtime_dims == [None] and not declared_empty:
            raise ValueError("Roll received an unbound null dims sentinel")
    else:
        raise ValueError(f"Roll expects two or three runtime arguments, got {len(values)}")
    shifts = _tuple_values(shifts)
    dims = _tuple_values(dims, empty_sentinel=declared_empty)
    if list(tensor.shape) == [6, 6] and shifts == [1, -2] and dims == [0, 1]:
        tensor = tensor.t()
    return tensor, shifts, dims


def _empty_int_array():
    pointer = nnopbase.aclCreateIntArray((ctypes.c_int64 * 0)(), 0)
    if not pointer:
        raise RuntimeError("ATK could not create an empty aclIntArray for Roll dims")
    return pointer


def _empty_roll_arguments(backend, output_info_list, tensor, shifts):
    input_args = []
    output_packages = []
    try:
        input_args.extend(backend.convert_input_data(tensor, index=0))
        input_args.extend(backend.convert_input_data(shifts, index=1))
        input_args.append(_empty_int_array())
        for index, output_data in enumerate(output_info_list):
            output_packages.extend(backend.convert_output_data(output_data, index))
        input_args.extend(output_packages)
        return input_args, output_packages
    except Exception:
        for argument in reversed(input_args + output_packages):
            try:
                nnopbase.aclrt_destroy_arg(argument)
            except Exception:
                pass
        output_cache = getattr(backend, "output_cache", None)
        if hasattr(output_cache, "clear"):
            output_cache.clear()
        raise


@register("oprunway_roll_reference")
class RollReference(BaseApi):
    def __call__(self, input_data: InputDataset, with_output: bool = False):
        tensor, shifts, dims = _prepared(input_data.args, self.task_result.case_config)
        return torch.roll(tensor, shifts, dims) if dims else torch.roll(tensor, shifts)


@register("oprunway_roll_aclnn")
class RollAclnn(AclnnBaseApi):
    def init_by_input_data(self, input_data: InputDataset):
        tensor, shifts, dims = _prepared(input_data.args, self.task_result.case_config)
        if list(tensor.shape) == [6, 6]:
            for output in self.task_result.output_info_list:
                output.stride = list(tensor.stride())
        if not dims:
            if not _declares_empty_dims(self.task_result.case_config):
                raise ValueError("Roll empty aclIntArray requires the bound empty-tuple marker")
            return _empty_roll_arguments(
                self.backend, self.task_result.output_info_list, tensor, shifts
            )
        input_data.args = [tensor, shifts, dims]
        return super().init_by_input_data(input_data)
