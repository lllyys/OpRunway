"""ACLNN 接口名的共享规范化规则。"""


class BindingError(ValueError):
    """The candidate library cannot provide an attributable execution."""


def _symbol_prefix(aclnn_name):
    if not isinstance(aclnn_name, str) or not aclnn_name.strip():
        raise BindingError("aclnn_name 必须是非空字符串")
    name = aclnn_name.strip()
    return name if name.startswith("aclnn") else f"aclnn{name}"
