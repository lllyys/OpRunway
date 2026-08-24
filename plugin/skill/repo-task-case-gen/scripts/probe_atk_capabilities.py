"""对装机的 ATK 做无设备离线探针，把能力域探成事实而不是手抄。

探三类，全部不需要 NPU：

1. **注册表**——后端、api_type、比较器、生成器、参数类型、dtype 六张表的键。
   这些键就是 YAML 里能填什么的唯一真源，手抄一份必然随版本漂移。
2. **参数容器**——attr / attrs / scalar 各容器落到什么运行时类型，空组与 typed null。
3. **判定与数据生成的语义**——不是「有没有这个键」，是「它到底怎么算」。
   这一类靠读源码得不到可信结论，必须真的喂数据看输出。

第三类是这轮补的。roll 那轮撞到两条谁都没写下来的语义：
`equal` 比较器对两侧全 NaN 无条件判通过，`random_types` 只写一项时均匀分布恒不生效。
两条都让用例白跑，且从任何 reference 都查不到。

ATK 升版后重跑本脚本，产物就是新的能力基线。
"""

import argparse
import json
from pathlib import Path

from _runtime_guard import runtime_imports


def config(name, kind, dtype, value, shape=None):
    return {
        "name": name, "type": kind, "required": True,
        "dtype": dtype, "shape": shape, "range_values": value,
    }


def probe_registries():
    """反射 ATK 的注册表：YAML 里能填哪些键，由这里定，不由 reference 定。"""
    from atk.tasks.task_plugins_register import (
        API_REGISTRY, BACKEND_REGISTRY, RUN_MODES_REGISTRY, init_task_registry)
    from atk.tasks.post_process import ACCURACY_REGISTRY
    from atk.case_generator.utils.plugins_register import (
        DATATYPE_REGISTRY, GENERATOR_REGISTRY, PARAMETER_REGISTRY)

    # 后端 / api_type / run_mode 三张表要先做任务侧注册，否则反射出来是空的。
    init_task_registry()
    tables = {
        "backend": BACKEND_REGISTRY, "api_type": API_REGISTRY,
        "run_mode": RUN_MODES_REGISTRY, "comparator": ACCURACY_REGISTRY,
        "generator": GENERATOR_REGISTRY, "parameter_type": PARAMETER_REGISTRY,
        "dtype": DATATYPE_REGISTRY,
    }
    return {name: sorted(key for key, _ in table)
            for name, table in tables.items()}


def probe_comparator_semantics():
    """比较器怎么判，喂数据看结果，不读源码下结论。"""
    import torch
    from atk.tasks.post_process import ACCURACY_REGISTRY

    def verdict(name, left, right):
        # 比较器构造要 case_config，但 equal 的判定只看两个张量，占位即可。
        compare = ACCURACY_REGISTRY[name](None)
        return bool(compare.compute_accuracy_result(left, right, "probe").result)

    nan = torch.full((8,), float("nan"))
    pos_inf = torch.full((8,), float("inf"))
    neg_inf = torch.full((8,), float("-inf"))
    mixed_inf = torch.tensor([float("inf"), float("-inf"), float("inf"), float("-inf"),
                              float("inf"), float("-inf"), float("inf"), float("-inf")])
    same = torch.arange(8, dtype=torch.float32)
    shifted = torch.roll(same, 1)

    # 「差一」：把一个元素改错一格，是选择类算子（median、topk、argmax）
    # 最典型的错法。哪个比较器拦得住它，决定该 dtype 的输出声明什么，
    # 靠读 ATK 源码推会推错——mixed_tolerance 对整型并不是一个走法。
    def off_by_one(dtype):
        if dtype is torch.bool:
            golden = torch.zeros(64, dtype=dtype)
            actual = golden.clone()
            actual[7] = True
        else:
            golden = torch.arange(1, 65, dtype=dtype)
            actual = golden.clone()
            actual[7] += 1
        return actual, golden

    int_dtypes = (torch.int8, torch.uint8, torch.int16,
                  torch.int32, torch.int64, torch.bool)
    return {
        # 两侧全 NaN 时 equal 直接判过：全 NaN 输入的用例恒过、不携带信息。
        "equal_passes_when_both_sides_are_all_nan": verdict("equal", nan, nan.clone()),
        # inf 判定：溢出场景下两侧都产生 inf 时是否判为通过。
        "equal_passes_when_both_sides_are_positive_inf": verdict("equal", pos_inf, pos_inf.clone()),
        "equal_passes_when_both_sides_are_negative_inf": verdict("equal", neg_inf, neg_inf.clone()),
        "equal_rejects_mixed_inf_signs": not verdict("equal", pos_inf, neg_inf),
        "mixed_tolerance_passes_when_both_sides_are_positive_inf": verdict("mixed_tolerance_bm", pos_inf, pos_inf.clone()),
        "mixed_tolerance_passes_when_both_sides_are_negative_inf": verdict("mixed_tolerance_bm", neg_inf, neg_inf.clone()),
        "mixed_tolerance_rejects_mixed_inf_signs": not verdict("mixed_tolerance_bm", pos_inf, neg_inf),
        "equal_passes_identical": verdict("equal", same, same.clone()),
        "equal_rejects_permutation": not verdict("equal", shifted, same),
        # 逐 dtype 记，因为它们并不一致：为 false 的那些 dtype，
        # 输出声明混合容差会放过错值，必须单独走 equal。
        "mixed_tolerance_rejects_off_by_one_by_dtype": {
            str(dtype).removeprefix("torch."):
                not verdict("mixed_tolerance_bm", *off_by_one(dtype))
            for dtype in int_dtypes
        },
        "mixed_tolerance_supported_dtypes": _mixed_tolerance_dtypes(),
    }


def _mixed_tolerance_dtypes():
    """混合容差实际认哪些 dtype——不认的那些走别的判定路径。

    逐个问 ATK 自己的判定函数，不抄源码里的字面量集合。
    """
    from atk.case_generator.utils.plugins_register import DATATYPE_REGISTRY
    from atk.configs.mixed_tolerance_benchmark_config import (
        MixedToleranceBenchmarkConfig)

    standard = MixedToleranceBenchmarkConfig()
    return sorted(
        name for name, _ in DATATYPE_REGISTRY
        if standard.supports_mixed_tolerance(name))


def probe_data_generation():
    """取值分布怎么落地：写几项 random_types，实际就用哪几种。"""
    import torch
    from atk.configs.design_config import RandomConfig
    from atk.case_generator.generator.data_types.data_torch import DATATYPE_REGISTRY

    def kinds(random_types):
        config = RandomConfig(values=[[-5, 5]], random_types=random_types)
        return [type(item).__name__ for item in config.get_actual_values()]

    def unique_count(dtype, mean, std, numel=4096):
        maker = DATATYPE_REGISTRY[dtype]
        spec = type("Spec", (), {"dtype": dtype, "type": "tensor",
                                 "shape": [numel], "outlier_values": None,
                                 "range_values": {"name": "nd", "mean": mean, "std": std},
                                 "align_32B": None})()
        data = maker(spec).gen_data()
        return int(torch.unique(data.reshape(-1)).numel())

    return {
        # 只写一项 nd 时均匀分布恒不生效，生态标准要求的「各半」静默失效。
        "single_nd_random_type_drops_uniform": kinds([{"name": "nd"}]) == ["dict"],
        "two_random_types_keep_both": sorted(
            set(kinds([{"name": "default"}, {"name": "nd"}]))) == ["dict", "list"],
        # 整型套浮点值域会塌成常量：这是 roll 那轮 17 条假通过的成因。
        "int8_unique_values_at_standard_range": unique_count("int8", [-5, 5], [0.1, 2]),
        "uint8_unique_values_at_standard_range": unique_count("uint8", [-5, 5], [0.1, 2]),
        "int8_unique_values_at_dtype_range": unique_count("int8", [-128, 127], [8, 32]),
        "uint8_unique_values_at_dtype_range": unique_count("uint8", [0, 255], [16, 64]),
    }


def guarded(name, probe, results, failures):
    """一个域探不出来不影响其余域，但要留痕。"""
    try:
        results[name] = probe()
    except Exception as exc:  # noqa: BLE001 探针的作用就是把失败记下来
        results[name] = {"error": f"{type(exc).__name__}: {exc}"}
        failures.append(name)


def main():
    parser = argparse.ArgumentParser(description="ATK 能力域离线探针")
    parser.add_argument("-o", "--output", default="atk_capability_probe.json")
    args = parser.parse_args()

    with runtime_imports("atk", "torch", "torch_npu"):
        import atk
        from atk.case_generator.utils.plugins_register import init_registry
        from atk.tasks.dataset.base_dataset import OpsDataset

        init_registry()
    probes = [
        ("attr", [config("x", "attr", "int", 3)], "int"),
        ("attr_default", [config("x", "attr", "int", "default")], "NoneType"),
        ("attrs", [[config("x", "attrs", "int", 2),
                    config("x", "attrs", "int", 5)]], "list"),
        ("attr_tuple", [[config("x", "attr_tuple", "int", 2),
                         config("x", "attr_tuple", "int", 5)]], "tuple"),
        ("scalars", [[config("x", "scalars", "fp32", 1.5),
                      config("x", "scalars", "fp32", 2.5)]], "list"),
        ("scalar_tuple", [[config("x", "scalar_tuple", "fp32", 1.5),
                           config("x", "scalar_tuple", "fp32", 2.5)]], "tuple"),
    ]
    results, failures = [], []
    for name, inputs, expected in probes:
        try:
            values = next(OpsDataset([{"name": name, "inputs": inputs}], 0)).args
            actual = type(values[0]).__name__
            passed = actual == expected
            results.append({"name": name, "expected": expected, "actual": actual,
                            "passed": passed})
            if not passed:
                failures.append(name)
        except Exception as exc:
            results.append({"name": name, "expected": expected,
                            "error": f"{type(exc).__name__}: {exc}", "passed": False})
            failures.append(name)

    try:
        next(OpsDataset([{"name": "empty", "inputs": [[]]}], 0))
        results.append({"name": "empty_group_rejected", "passed": False})
        failures.append("empty_group_rejected")
    except Exception as exc:
        results.append({"name": "empty_group_rejected", "passed": True,
                        "observed": type(exc).__name__})

    try:
        from atk.tasks.backends.lib_interface.acl_wrapper import TensorPtr

        pointer = TensorPtr()
        results.append({
            "name": "typed_optional_tensor_pointer",
            "passed": not bool(pointer),
            "factory": "TensorPtr",
            "actual": type(pointer).__name__,
        })
        if bool(pointer):
            failures.append("typed_optional_tensor_pointer")
    except Exception as exc:
        results.append({
            "name": "typed_optional_tensor_pointer",
            "error": f"{type(exc).__name__}: {exc}",
            "passed": False,
        })
        failures.append("typed_optional_tensor_pointer")

    domains = {}
    guarded("registries", probe_registries, domains, failures)
    guarded("comparator_semantics", probe_comparator_semantics, domains, failures)
    guarded("data_generation", probe_data_generation, domains, failures)

    report = {
        "schema_version": 3,
        "atk_version": getattr(atk, "PACKAGE_VERSION", None),
        "results": results,
        "domains": domains,
        "failures": failures,
    }
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
