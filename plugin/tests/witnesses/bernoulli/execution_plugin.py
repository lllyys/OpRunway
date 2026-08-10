import math

import torch

from atk.configs.dataset_config import InputDataset
from atk.configs.results_config import AccuracyConfig
from atk.case_generator.utils.enums import TorchDtype
from atk.tasks.api_execute import register
from atk.tasks.api_execute.aclnn_base_api import AclnnBaseApi
from atk.tasks.api_execute.base_api import BaseApi
from atk.tasks.post_process import ACCURACY_REGISTRY
from atk.tasks.post_process.base_compare import BaseAccuracyCompare


def _inputs(input_data):
    self_tensor = input_data.kwargs["self"]
    if list(self_tensor.shape) == [64, 64]:
        self_tensor = self_tensor.t()
    return (
        self_tensor,
        float(input_data.kwargs["prob"]),
        int(input_data.kwargs["seed"]),
        int(input_data.kwargs["offset"]),
    )


@register("oprunway_bernoulli_reference")
class BernoulliReference(BaseApi):
    def __call__(self, input_data: InputDataset, with_output: bool = False):
        self_tensor, probability, seed, offset = _inputs(input_data)
        generator = torch.Generator(device="cpu")
        generator.manual_seed(seed + offset // 4)
        probabilities = torch.full(self_tensor.shape, probability, dtype=torch.float32)
        declared_dtype = self.task_result.case_config.inputs[0].dtype
        return torch.bernoulli(probabilities, generator=generator).to(TorchDtype.get(declared_dtype))


@register("oprunway_bernoulli_aclnn")
class BernoulliAclnn(AclnnBaseApi):
    def init_by_input_data(self, input_data: InputDataset):
        self_tensor, probability, seed, offset = _inputs(input_data)
        input_data.kwargs = {
            "self": self_tensor,
            "prob": probability,
            "seed": seed,
            "offset": offset,
        }
        for output in self.task_result.output_info_list:
            output.dtype = str(self_tensor.dtype)
            if list(self_tensor.shape) == [64, 64]:
                output.stride = list(self_tensor.stride())
        return super().init_by_input_data(input_data)


@ACCURACY_REGISTRY.register("oprunway_binary_distribution_bm")
class BinaryDistributionCompare(BaseAccuracyCompare):
    def compute_accuracy_result(self, local_output, remote_output, data_file):
        probability = float(self.case_config.inputs[1].range_values)

        def error(tensor):
            values = tensor.to(torch.float64)
            if not torch.all((values == 0) | (values == 1)):
                return "output contains values outside {0, 1}"
            if values.numel() == 0:
                return None
            observed = float(values.mean())
            tolerance = 0.0 if probability in {0.0, 1.0} else max(
                0.01,
                6.0 * math.sqrt(probability * (1.0 - probability) / values.numel()),
            )
            if abs(observed - probability) > tolerance:
                return f"observed probability {observed} differs from {probability} by more than {tolerance}"
            return None

        problems = [problem for problem in (error(local_output), error(remote_output)) if problem]
        return AccuracyConfig(
            filename=data_file,
            result=not problems,
            error_info="; ".join(problems) if problems else "binary distribution check passed",
        )
