import ctypes

import cv2
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


def _inputs(input_data):
    src = input_data.kwargs["src"]
    if list(src.shape) == [64, 64, 3]:
        src = src.transpose(0, 1)
    return (
        src,
        list(input_data.kwargs["ksize"]),
        float(input_data.kwargs["sigmaX"]),
        float(input_data.kwargs["sigmaY"]),
        int(input_data.kwargs["borderType"]),
    )


@register("oprunway_gaussian_blur_reference")
class GaussianBlurReference(BaseApi):
    def __call__(self, input_data: InputDataset, with_output: bool = False):
        src, ksize, sigma_x, sigma_y, border_type = _inputs(input_data)
        if src.numel() == 0:
            return src.clone()
        output = cv2.GaussianBlur(
            src.detach().cpu().numpy(), tuple(ksize), sigma_x, sigmaY=sigma_y, borderType=border_type
        )
        if src.ndim == 3 and src.shape[-1] == 1 and output.ndim == 2:
            output = output[..., None]
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
            if list(src.shape) == [64, 64, 3]:
                output.stride = list(src.stride())
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
        signature = [ctypes.c_void_p, ctypes.c_uint64, ctypes.POINTER(OpExecutor)]
        signature.extend(type(argument) for argument in raw_args)
        signature.append(ctypes.c_void_p)
        function = acl_wrapper.aclnn.bind_function("aclnnGaussianBlur", signature, AclnnStatus)
        if function is None:
            raise AttributeError("cannot bind aclnnGaussianBlur second-stage signature")
        result = function(
            self.backend.workspace,
            self.backend.workspace_size,
            self.backend.executor,
            *raw_args,
            self.backend.stream,
        )
        if result.value != AclnnStatus.ACLNN_SUCCESS:
            raise ACLRuntimeError(f"aclnnGaussianBlur failed! error code: {result.value}")
