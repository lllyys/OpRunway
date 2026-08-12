import ctypes
import os
from pathlib import Path

import numpy as np
import torch

import atk.tasks.backends.lib_interface.acl_wrapper as acl_wrapper
from atk.configs.dataset_config import InputDataset
from atk.tasks.api_execute import register
from atk.tasks.api_execute.aclnn_base_api import AclnnBaseApi
from atk.tasks.api_execute.base_api import BaseApi
from atk.tasks.backends.lib_interface.acl_wrapper import (
    ACLRuntimeError,
    AclnnStatus,
    AclTensorStruct,
    AclTensorlistStruct,
    OpExecutor,
)


_GAUSSIAN_BLUR_ARGUMENT_TYPES = (
    ctypes.POINTER(acl_wrapper.AclTensor),
    ctypes.POINTER(acl_wrapper.AclIntArray),
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_int64,
    ctypes.POINTER(acl_wrapper.AclTensor),
)
_GAUSSIAN_BLUR_STAGE2_TYPES = (
    ctypes.c_void_p,
    ctypes.c_uint64,
    ctypes.POINTER(OpExecutor),
    *_GAUSSIAN_BLUR_ARGUMENT_TYPES,
    ctypes.c_void_p,
)
_TASK_CASES_ENV = "OPRUNWAY_TASK_CASES_ROOT"
_OFFICIAL_GOLDEN_PATH = "gaussian_blur_golden.py"
_OFFICIAL_GOLDEN = None
_OPENCV_MAX_CHANNELS = 512


def _official_golden():
    global _OFFICIAL_GOLDEN
    if _OFFICIAL_GOLDEN is not None:
        return _OFFICIAL_GOLDEN
    root_text = os.environ.get(_TASK_CASES_ENV)
    if not root_text:
        raise RuntimeError(f"{_TASK_CASES_ENV} is required")
    path = Path(root_text) / _OFFICIAL_GOLDEN_PATH
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("official GaussianBlur golden file is unavailable")
    try:
        source = path.read_text(encoding="utf-8", errors="strict")
        namespace = {"__name__": "oprunway_official_gaussian_blur_golden", "__file__": str(path)}
        exec(compile(source, str(path), "exec"), namespace)
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        raise RuntimeError(f"cannot load official GaussianBlur golden module: {exc}") from exc
    function = namespace.get("gaussian_blur_golden")
    if not callable(function):
        raise RuntimeError("official GaussianBlur golden callable is unavailable")
    _OFFICIAL_GOLDEN = function
    return function


def _bind_stage2(raw_args):
    if len(raw_args) != len(_GAUSSIAN_BLUR_ARGUMENT_TYPES):
        raise TypeError(
            "aclnnGaussianBlur requires exactly six operator arguments, "
            f"got {len(raw_args)}"
        )
    for index, (argument, expected) in enumerate(
        zip(raw_args, _GAUSSIAN_BLUR_ARGUMENT_TYPES, strict=True)
    ):
        if type(argument) is not expected:
            raise TypeError(
                f"aclnnGaussianBlur argument {index} requires {expected.__name__}, "
                f"got {type(argument).__name__}"
            )

    library = acl_wrapper.aclnn.get_lib(acl_wrapper.aclnn.lib_name)
    try:
        symbol = getattr(library, "aclnnGaussianBlur")
    except AttributeError as exc:
        raise AttributeError("loaded vendor library has no aclnnGaussianBlur symbol") from exc
    address = ctypes.cast(symbol, ctypes.c_void_p).value
    if not address:
        raise AttributeError("loaded vendor library has a null aclnnGaussianBlur symbol")
    return ctypes.CFUNCTYPE(AclnnStatus, *_GAUSSIAN_BLUR_STAGE2_TYPES)(address)


def _inputs(input_data):
    src = input_data.kwargs["src"]
    return (
        src,
        list(input_data.kwargs["ksize"]),
        float(input_data.kwargs["sigmaX"]),
        float(input_data.kwargs["sigmaY"]),
        int(input_data.kwargs["borderType"]),
    )


def _official_reference(image, ksize_x, ksize_y, sigma_x, sigma_y):
    """Run the official oracle without changing its per-channel semantics."""
    chunks = (
        [image]
        if image.ndim != 3 or image.shape[-1] <= _OPENCV_MAX_CHANNELS
        else [
            image[..., start:start + _OPENCV_MAX_CHANNELS]
            for start in range(0, image.shape[-1], _OPENCV_MAX_CHANNELS)
        ]
    )
    outputs = []
    golden = _official_golden()
    for chunk in chunks:
        values = golden(chunk, ksize_x, ksize_y, sigma_x, sigma_y)
        if not isinstance(values, list) or len(values) != 1:
            raise RuntimeError("official GaussianBlur golden returned an invalid output list")
        output = values[0]
        if not isinstance(output, np.ndarray) or output.shape != chunk.shape:
            raise RuntimeError("official GaussianBlur golden returned an invalid output tensor")
        outputs.append(output)
    return outputs[0] if len(outputs) == 1 else np.concatenate(outputs, axis=-1)


@register("oprunway_gaussian_blur_reference")
class GaussianBlurReference(BaseApi):
    def __call__(self, input_data: InputDataset, with_output: bool = False):
        src, ksize, sigma_x, sigma_y, border_type = _inputs(input_data)
        if border_type != 4:
            raise ValueError("official GaussianBlur self-tests require OpenCV BORDER_DEFAULT=4")
        output = _official_reference(
            src.detach().cpu().numpy(), ksize[0], ksize[1], sigma_x, sigma_y
        )
        return torch.from_numpy(output).to(src.dtype)


@register("oprunway_gaussian_blur_aclnn")
class GaussianBlurAclnn(AclnnBaseApi):
    def init_by_input_data(self, input_data: InputDataset):
        src, ksize, sigma_x, sigma_y, border_type = _inputs(input_data)
        input_data.kwargs = {
            "src": src,
            "ksize": ksize,
            "sigmaX": sigma_x,
            "sigmaY": sigma_y,
            "borderType": border_type,
        }
        for output in self.task_result.output_info_list:
            output.dtype = str(src.dtype)
        return super().init_by_input_data(input_data)

    def __call__(self):
        self.backend.aclnn_x_get_workspace_size()
        raw_args = []
        for argument in self.backend.input_args:
            if isinstance(argument, AclTensorStruct):
                raw_args.append(argument.tensor)
            elif isinstance(argument, AclTensorlistStruct):
                raw_args.append(argument.tensorlist)
            else:
                raw_args.append(argument)
        function = _bind_stage2(raw_args)
        result = function(
            self.backend.workspace,
            self.backend.workspace_size,
            self.backend.executor,
            *raw_args,
            self.backend.stream,
        )
        if result.value != AclnnStatus.ACLNN_SUCCESS:
            raise ACLRuntimeError(f"aclnnGaussianBlur failed! error code: {result.value}")
