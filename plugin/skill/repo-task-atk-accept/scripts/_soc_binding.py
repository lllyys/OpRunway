"""Map a selected Ascend device to its build family and inspect a package."""

import re
from pathlib import Path


class SocBindingError(ValueError):
    """The selected device and installed kernel package are not compatible."""


def infer_build_soc(device_name):
    """Return the public build.sh SoC family for a concrete device name."""
    if not isinstance(device_name, str) or not device_name.strip():
        raise SocBindingError("设备型号为空，无法确定构建 SoC")
    name = re.sub(r"[^a-z0-9_]", "", device_name.lower())
    if re.fullmatch(r"ascend910_9\d{3}", name):
        return "ascend910_93"
    for prefix, family in (
        ("ascend910b", "ascend910b"),
        ("ascend310p", "ascend310p"),
        ("ascend950", "ascend950"),
        ("ascend350", "ascend350"),
    ):
        if name.startswith(prefix):
            return family
    raise SocBindingError(f"设备型号 {device_name!r} 无法映射为公开构建 SoC")


class SocUnsupportedError(SocBindingError):
    """The operator itself does not declare the SoC of the acceptance machine."""


def scan_build_log(build_log, build_soc):
    """Find the build-time proof that the operator does not declare this SoC.

    构建脚本在开跑几秒内就会把结论打进日志，而整轮构建还要跑十几分钟。
    这类失败换个 SoC 重编一定能过——正因为如此它必须是终局：换掉的是
    验收前提本身，不是构建参数。

    只认与本机构建族同名的那两条终局串，日志里提到别的 SoC 不算。
    """
    text = Path(build_log).expanduser().read_text(
        encoding="utf-8", errors="replace")
    escaped = re.escape(build_soc)
    patterns = (
        rf"no operator support for {escaped}\b",
        rf"On \[{escaped}\], \[[^\]]+\] not supported",
    )
    hits = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            hits.append(match.group(0))
    return hits


def inspect_package(vendor_root, device_name):
    """Require installed kernel metadata and objects for the selected device."""
    root = Path(vendor_root).expanduser()
    try:
        root = root.resolve(strict=True)
    except FileNotFoundError as exc:
        raise SocBindingError(f"待验收算子 vendor 目录不存在：{root}") from exc
    if not root.is_dir():
        raise SocBindingError(f"待验收算子 vendor 路径不是目录：{root}")

    build_soc = infer_build_soc(device_name)
    kernel_root = root / "op_impl/ai_core/tbe/kernel"
    config_dir = kernel_root / "config" / build_soc
    object_dir = kernel_root / build_soc
    if not config_dir.is_dir():
        available = sorted(
            path.name for path in (kernel_root / "config").glob("*")
            if path.is_dir())
        raise SocBindingError(
            f"待验收算子包缺少 kernel/config/{build_soc}，已有构建族 {available}")
    if not (config_dir / "binary_info_config.json").is_file():
        raise SocBindingError(
            f"待验收算子包缺少 {build_soc}/binary_info_config.json")
    if not object_dir.is_dir():
        raise SocBindingError(f"待验收算子包缺少 kernel/{build_soc}")

    object_count = sum(1 for _ in object_dir.rglob("*.o"))
    metadata_count = sum(1 for _ in object_dir.rglob("*.json"))
    if not object_count or not metadata_count:
        raise SocBindingError(
            f"待验收算子包的 kernel/{build_soc} 缺少 .o 或 .json 产物")
    return {
        "vendor_root": str(root),
        "device_name": device_name,
        "build_soc": build_soc,
        "config_dir": str(config_dir),
        "kernel_dir": str(object_dir),
        "kernel_object_count": object_count,
        "kernel_metadata_count": metadata_count,
        "soc_package_verified": True,
    }
