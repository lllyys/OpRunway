"""ATK 参数表示能力的版本化、fail-closed 校验：运行期用例结构这一半。

设计期那一半（校验 make_yaml.py 产出的 YAML 是否可表达）已删除——
那份校验的是脚本从契约确定性生成的东西，脚本自己刚生成的不需要再查。

这一半不同：`check_cases` 查的是 `atk case` 生成后的真实 Case JSON，
它的结构受 agent 手写的 constraint 生成器插件影响，仍然值得查，
被 `validate_cases.py` 的 C6 检查消费。
"""

import json
from pathlib import Path


CAPABILITY_PATH = (
    Path(__file__).resolve().parents[1]
    / "references"
    / "atk-parameter-capabilities.json"
)


def load_capabilities(path=None):
    target = Path(path) if path else CAPABILITY_PATH
    return json.loads(target.read_text(encoding="utf-8"))


def check_cases(cases, backend, capabilities):
    """生成后核对实际 Case JSON 是否符合能力域。"""
    failures = []
    flat_types = set(capabilities["flat_types"])
    group_types = capabilities["group_types"]
    allowed_attr_arrays = set(capabilities["pyaclnn"]["attr_array_dtypes"])
    allowed_attrs = set(capabilities["pyaclnn"]["attr_single_dtypes"])
    allowed_attrs.add("non_param")
    allowed_scalars = set(capabilities["pyaclnn"]["scalar_dtypes"])
    bad = []

    if backend not in capabilities["backends"]:
        return [f"P12 backend {backend!r} 没有已确认参数能力矩阵"], []

    for case in cases:
        channels = {
            "inputs": list(case.get("inputs") or []),
            "method_inputs": list(case.get("method_inputs") or []),
            "tensor_input": [case["tensor_input"]] if case.get("tensor_input") else [],
        }
        for channel, groups in channels.items():
            channel_names = []
            for index, group in enumerate(groups):
                prefix = {"id": case.get("id"), "channel": channel, "index": index}
                if channel == "tensor_input" and (
                        not isinstance(group, dict) or group.get("type") != "tensor"):
                    bad.append({**prefix, "reason": "invalid_tensor_receiver"})
                if isinstance(group, list):
                    if not group:
                        bad.append({**prefix, "reason": "empty_group"})
                        continue
                    if not all(isinstance(item, dict) for item in group):
                        bad.append({**prefix, "reason": "non_object_group_member"})
                        continue
                    types = {item.get("type") for item in group}
                    names = {item.get("name") for item in group}
                    dtypes = {item.get("dtype") for item in group}
                    kind = group[0].get("type")
                    if len(types) != 1 or kind not in group_types:
                        bad.append({
                            **prefix,
                            "reason": "group_type_mismatch",
                            "types": sorted(map(str, types)),
                        })
                    if len(names) != 1:
                        bad.append({
                            **prefix,
                            "reason": "group_name_mismatch",
                            "names": sorted(map(str, names)),
                        })
                    group_name = group[0].get("name")
                    if group_name:
                        channel_names.append(group_name)
                    if any(item.get("range_values") is None for item in group):
                        bad.append({**prefix, "reason": "json_null_in_group"})
                    if any(
                            item.get("range_values") == capabilities["default_token"]
                            for item in group):
                        bad.append({**prefix, "reason": "none_inside_group"})
                    binds_to_aclnn = backend == "pyaclnn" and channel == "inputs"
                    if binds_to_aclnn and kind in {"attrs", "attr_tuple"}:
                        if len(dtypes) != 1:
                            bad.append({
                                **prefix, "reason": "heterogeneous_attr_dtypes"
                            })
                        elif next(iter(dtypes)) not in allowed_attr_arrays:
                            bad.append({
                                **prefix, "reason": "unsupported_attr_array_dtype"
                            })
                    if (binds_to_aclnn
                            and kind in {"scalars", "scalar_tuple"}
                            and len(dtypes) != 1):
                        bad.append({
                            **prefix, "reason": "heterogeneous_scalar_dtypes"
                        })
                elif isinstance(group, dict):
                    kind = group.get("type")
                    dtype = group.get("dtype")
                    if group.get("name"):
                        channel_names.append(group["name"])
                    if kind not in flat_types:
                        bad.append({**prefix, "reason": "flat_group_type", "type": kind})
                    if (kind in {"attr", "scalar"}
                            and group.get("range_values") is None):
                        bad.append({**prefix, "reason": "json_null_range_values"})
                    binds_to_aclnn = backend == "pyaclnn" and channel == "inputs"
                    if (binds_to_aclnn and kind == "attr"
                            and dtype not in allowed_attrs):
                        bad.append({**prefix, "reason": "unsupported_attr_dtype"})
                    if (binds_to_aclnn and kind == "scalar"
                            and dtype not in allowed_scalars):
                        bad.append({**prefix, "reason": "unsupported_scalar_dtype"})
                else:
                    bad.append({**prefix, "reason": "invalid_input_node"})
            duplicates = sorted({
                name for name in channel_names if channel_names.count(name) > 1
            })
            if duplicates:
                bad.append({
                    "id": case.get("id"),
                    "channel": channel,
                    "reason": "duplicate_keyword_names",
                    "names": duplicates,
                })

    if bad:
        # 判据是随 skill 发布的能力矩阵，锁死了版本；装机 ATK 不是这个版本时
        # 这条结论本身就不成立。出口是重探，不是照着改产物。
        versions = capabilities.get("atk_versions") or ["未记录"]
        failures.append(
            f"P12 有 {len(bad)} 处 Case JSON 参数结构超出 ATK 能力域"
            f"（前 12 个：{bad[:12]}）。\n"
            f"  → 能力矩阵锁的是 ATK {'、'.join(str(v) for v in versions)}；"
            "装机版本不是它时先跑 probe_atk_capabilities.py 重探，再判，不要照这条改产物")
        return failures, []
    return [], [f"P12 通过：{len(cases)} 条用例的运行期参数结构均受支持"]
