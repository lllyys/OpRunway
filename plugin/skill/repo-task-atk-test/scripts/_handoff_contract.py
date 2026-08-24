"""加载唯一交接契约，并渲染目录树与封印清单样例。"""

import json
from pathlib import Path


CONTRACT_PATH = (
    Path(__file__).resolve().parents[1] / "references" / "handoff-contract.json"
)
TYPE_NAMES = {
    "array": list,
    "integer": int,
    "object": dict,
    "string": str,
}


class HandoffContractError(RuntimeError):
    """交接契约缺失、无法解析或形状不合法。"""


def _object(value, label):
    if not isinstance(value, dict):
        raise HandoffContractError(f"{label} 必须是对象")
    return value


def _integer(value, label):
    if type(value) is not int or value < 1:
        raise HandoffContractError(f"{label} 必须是正整数")


def _strings(value, label, allow_empty=False):
    if not isinstance(value, list) or (not value and not allow_empty):
        raise HandoffContractError(f"{label} 必须是非空字符串列表")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise HandoffContractError(f"{label} 必须是非空字符串列表")
    if len(value) != len(set(value)):
        raise HandoffContractError(f"{label} 不得有重复项")
    return value


def _required(mapping, keys, label):
    missing = [key for key in keys if key not in mapping]
    if missing:
        raise HandoffContractError(f"{label} 缺字段：{'、'.join(missing)}")


def _validate_manifest(contract):
    manifest = _object(contract["manifest"], "manifest")
    required = (
        "shapes",
        "excluded",
        "ignored_dirs",
        "interface_fields",
        "task_doc_fields",
        "env_fields",
        "example",
    )
    _required(manifest, required, "manifest")
    shapes = _object(manifest["shapes"], "manifest.shapes")
    if not shapes:
        raise HandoffContractError("manifest.shapes 不得为空")
    unknown_types = [
        f"{name}={kind!r}"
        for name, kind in shapes.items()
        if kind not in TYPE_NAMES
    ]
    if unknown_types:
        raise HandoffContractError(
            "manifest.shapes 有未知类型：" + "、".join(unknown_types)
        )

    excluded_values = _strings(manifest["excluded"], "manifest.excluded")
    if len(excluded_values) != 5:
        raise HandoffContractError("manifest.excluded 必须登记五个固定路径")
    _strings(manifest["ignored_dirs"], "manifest.ignored_dirs")
    interface_names = _strings(
        manifest["interface_fields"],
        "manifest.interface_fields",
    )
    task_doc_names = _strings(
        manifest["task_doc_fields"],
        "manifest.task_doc_fields",
    )
    _strings(manifest["env_fields"], "manifest.env_fields")

    example = _object(manifest["example"], "manifest.example")
    if list(example) != list(shapes):
        raise HandoffContractError(
            "manifest.example 的顶层键与 manifest.shapes 不一致"
        )
    for name, type_name in shapes.items():
        if type(example[name]) is not TYPE_NAMES[type_name]:
            raise HandoffContractError(
                f"manifest.example.{name} 必须是 {type_name}"
            )
    if example["schema_version"] != contract["schema_version"]:
        raise HandoffContractError(
            "manifest.example.schema_version 与 schema_version 不一致"
        )
    if example["excluded"] != manifest["excluded"]:
        raise HandoffContractError("manifest.example.excluded 与固定路径不一致")
    if example["ignored_dirs"] != manifest["ignored_dirs"]:
        raise HandoffContractError(
            "manifest.example.ignored_dirs 与忽略目录不一致"
        )
    if list(_object(example["interface"], "manifest.example.interface")) != interface_names:
        raise HandoffContractError(
            "manifest.example.interface 的键与 interface_fields 不一致"
        )
    if list(_object(example["task_doc"], "manifest.example.task_doc")) != task_doc_names:
        raise HandoffContractError(
            "manifest.example.task_doc 的键与 task_doc_fields 不一致"
        )


def _validate_tree(contract):
    tree = _object(contract["tree"], "tree")
    _required(tree, ("root", "entries"), "tree")
    if not isinstance(tree["root"], str) or not tree["root"].strip():
        raise HandoffContractError("tree.root 必须是非空字符串")
    entries = tree["entries"]
    if not isinstance(entries, list) or not entries:
        raise HandoffContractError("tree.entries 必须是非空列表")
    paths = []
    required = (
        "path",
        "parent",
        "required",
        "conditional",
        "producer",
        "readers",
        "in_files",
    )
    for index, entry in enumerate(entries):
        label = f"tree.entries[{index}]"
        entry = _object(entry, label)
        _required(entry, required, label)
        path = entry["path"]
        if not isinstance(path, str) or not path.strip():
            raise HandoffContractError(f"{label}.path 必须是非空字符串")
        parent = entry["parent"]
        if parent is not None and parent not in paths:
            raise HandoffContractError(f"{label}.parent 必须指向前面的目录项")
        if type(entry["required"]) is not bool:
            raise HandoffContractError(f"{label}.required 必须是布尔值")
        conditional = entry["conditional"]
        if conditional is not None and (
            not isinstance(conditional, str) or not conditional.strip()
        ):
            raise HandoffContractError(f"{label}.conditional 必须是非空字符串或 null")
        if entry["required"] == bool(conditional):
            raise HandoffContractError(
                f"{label} 必须且只能在 required 与 conditional 中选择一种"
            )
        producer = entry["producer"]
        if isinstance(producer, str):
            if not producer.strip():
                raise HandoffContractError(f"{label}.producer 不得为空")
        else:
            _strings(producer, f"{label}.producer")
        _strings(entry["readers"], f"{label}.readers", allow_empty=True)
        if type(entry["in_files"]) is not bool:
            raise HandoffContractError(f"{label}.in_files 必须是布尔值")
        paths.append(path)
    if len(paths) != len(set(paths)):
        raise HandoffContractError("tree.entries.path 不得重复")


def _validate_consumers(contract):
    consumers = _object(contract["consumers"], "consumers")
    required = (
        "evidence/interface.json",
        "evidence/bundle.json",
        "evidence/env.json",
    )
    _required(consumers, required, "consumers")
    interface = _object(
        consumers["evidence/interface.json"],
        "consumers.evidence/interface.json",
    )
    expected = contract["manifest"]["interface_fields"]
    if list(interface) != expected:
        raise HandoffContractError(
            "consumers.evidence/interface.json 的键与 interface_fields 不一致"
        )
    for field, readers in interface.items():
        _strings(readers, f"consumers.evidence/interface.json.{field}")

    bundle = _object(
        consumers["evidence/bundle.json"],
        "consumers.evidence/bundle.json",
    )
    bundle_fields = ("files", "rewires", "task_doc.sha256", "atk.version", "interface.*")
    _required(bundle, bundle_fields, "consumers.evidence/bundle.json")
    for field in bundle_fields:
        _strings(bundle[field], f"consumers.evidence/bundle.json.{field}")

    env = _object(consumers["evidence/env.json"], "consumers.evidence/env.json")
    _required(env, ("handoff_input", "description", "fingerprint.atk"), "consumers.evidence/env.json")
    if env["handoff_input"] is not False:
        raise HandoffContractError("evidence/env.json 必须标明不作交接输入")
    if not isinstance(env["description"], str) or not env["description"].strip():
        raise HandoffContractError("evidence/env.json 的说明不得为空")
    _strings(env["fingerprint.atk"], "consumers.evidence/env.json.fingerprint.atk")


def load(path=CONTRACT_PATH):
    """读取并严格校验交接契约。"""
    try:
        contract = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HandoffContractError(f"读不出 {path}：{exc}") from exc
    contract = _object(contract, "交接契约")
    _required(
        contract,
        ("contract_version", "schema_version", "manifest", "tree", "consumers"),
        "交接契约",
    )
    _integer(contract["contract_version"], "contract_version")
    _integer(contract["schema_version"], "schema_version")
    _validate_manifest(contract)
    _validate_tree(contract)
    _validate_consumers(contract)
    return contract


def excluded(contract=None):
    """返回固定排除路径。"""
    contract = contract or load()
    return tuple(contract["manifest"]["excluded"])


def ignored_dirs(contract=None):
    """返回固定忽略目录名。"""
    contract = contract or load()
    return tuple(contract["manifest"]["ignored_dirs"])


def interface_fields(contract=None):
    """返回接口交接字段，顺序即写入清单的顺序。"""
    contract = contract or load()
    return tuple(contract["manifest"]["interface_fields"])


def schema_version(contract=None):
    """返回 bundle.json 的 schema_version。"""
    contract = contract or load()
    return contract["schema_version"]


def manifest_shapes(contract=None):
    """把契约里的 JSON 类型名变成 Python 类型表。"""
    contract = contract or load()
    return {
        name: TYPE_NAMES[type_name]
        for name, type_name in contract["manifest"]["shapes"].items()
    }


def render_tree(contract=None):
    """按 tree.entries 的父子关系渲染交接包目录树。"""
    contract = contract or load()
    tree = contract["tree"]
    entries = tree["entries"]
    children = {}
    for entry in entries:
        children.setdefault(entry["parent"], []).append(entry)

    lines = [tree["root"]]

    def visit(parent, prefix):
        siblings = children.get(parent, [])
        for index, entry in enumerate(siblings):
            last = index == len(siblings) - 1
            connector = "└── " if last else "├── "
            label = entry["path"]
            if parent:
                label = label.removeprefix(parent)
            if entry["conditional"]:
                label = f"[{label}]"
            lines.append(prefix + connector + label)
            child_prefix = prefix + ("    " if last else "│   ")
            visit(entry["path"], child_prefix)

    visit(None, "")
    return "\n".join(lines)


def render_manifest_example(contract=None):
    """渲染 handoff.md 中保持紧凑布局的封印清单样例。"""
    contract = contract or load()
    example = contract["manifest"]["example"]
    lines = ["{"]
    names = list(example)
    for index, name in enumerate(names):
        value = example[name]
        comma = "," if index < len(names) - 1 else ""
        if name in {"interface", "excluded"}:
            lines.append(f'  "{name}": ' + ("{" if name == "interface" else "["))
            items = list(value.items()) if name == "interface" else list(enumerate(value))
            for item_index, item in enumerate(items):
                item_comma = "," if item_index < len(items) - 1 else ""
                if name == "interface":
                    key, item_value = item
                    rendered = json.dumps(item_value, ensure_ascii=False)
                    lines.append(f'    "{key}": {rendered}{item_comma}')
                else:
                    _, item_value = item
                    rendered = json.dumps(item_value, ensure_ascii=False)
                    lines.append(f"    {rendered}{item_comma}")
            lines.append(("  }" if name == "interface" else "  ]") + comma)
            continue
        rendered = json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
        lines.append(f'  "{name}": {rendered}{comma}')
    lines.append("}")
    return "\n".join(lines)
