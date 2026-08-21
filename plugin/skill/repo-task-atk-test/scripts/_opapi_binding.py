"""Verify that an ACLNN run is bound to one exact candidate library."""

import ctypes
import hashlib
import re
from pathlib import Path

from _aclnn_names import BindingError
from _aclnn_names import _symbol_prefix


ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

# ATK 的签名自检发现参数对不上时不会停，它自己动手改调用：丢掉一个入参、
# 把第一个入参当输出，或者调换入参顺序，然后只打一条 warning 继续跑。
# 于是冒烟绿灯、全量绿灯、报告 100%，而被测的调用已经不是工程头文件里那个。
# 这次是 7 参对 4 参差太远才抛异常暴露出来；差一个参数就会一路静默通过。
# ATK 不把这当失败，我们把它当失败。
SIGNATURE_REPAIRED = (
    "参数数量不匹配",
    "参数类型不匹配",
)


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_library(library, aclnn_name, loader=ctypes.CDLL):
    """Load one exact shared object and require its workspace and run symbols."""
    path = Path(library).expanduser()
    if not path.is_absolute():
        raise BindingError("待验收算子 op_api 库必须使用绝对路径")
    try:
        path = path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise BindingError(f"待验收算子 op_api 库不存在：{path}") from exc
    if not path.is_file():
        raise BindingError(f"待验收算子 op_api 路径不是文件：{path}")

    prefix = _symbol_prefix(aclnn_name)
    symbols = [f"{prefix}GetWorkspaceSize", prefix]
    try:
        loaded = loader(str(path))
    except OSError as exc:
        raise BindingError(f"待验收算子 op_api 库无法加载：{path}：{exc}") from exc
    missing = [symbol for symbol in symbols if not hasattr(loaded, symbol)]
    if missing:
        raise BindingError(f"待验收算子 op_api 库缺少接口 {missing}：{path}")
    return {
        "library": str(path),
        "sha256": _sha256(path),
        "required_symbols": symbols,
        "environment": {"ATK_CUSTOM_OPP_PATH": str(path)},
    }


def verify_runtime_log(log_text, library, aclnn_name):
    """Require exact candidate loading and ATK's header-based ABI check."""
    expected = Path(library).expanduser().resolve()
    symbol = f"{_symbol_prefix(aclnn_name)}GetWorkspaceSize"
    clean = ANSI_ESCAPE.sub("", log_text)
    pattern = re.compile(
        rf"import\s+{re.escape(symbol)}\s+from\s+(.+?)\s+success!",
        re.IGNORECASE,
    )
    loaded = [Path(value.strip()).expanduser().resolve() for value in pattern.findall(clean)]
    if not loaded:
        raise BindingError(f"ATK 日志没有 {symbol} 的成功加载记录")
    unexpected = sorted({str(path) for path in loaded if path != expected})
    if unexpected:
        raise BindingError(
            f"ATK 未绑定待验收算子库 {expected}，实际加载 {unexpected}")
    signature_pattern = re.compile(
        rf"算子一段式名称是：\s*(?:[A-Za-z_]\w*\s+)*{re.escape(symbol)}\b",
        re.IGNORECASE,
    )
    if not signature_pattern.search(clean):
        raise BindingError(
            f"ATK 日志缺少 {symbol} 的头文件签名校验；冒烟必须启用 "
            "--cpp_func_signature_type_path")
    repaired = [marker for marker in SIGNATURE_REPAIRED if marker in clean]
    if repaired:
        raise BindingError(
            f"ATK 已经改写了 {symbol} 的调用（日志里的 {'、'.join(repaired)}）。"
            "它校验用的头文件不是本轮那份，这一轮跑的不是工程声明的签名，"
            "结论不作数。先把签名自检的搜索目录锁到本轮 vendor，"
            "见 references/build-deploy.md#签名自检搜的是磁盘上的同名头文件")
    return {
        "symbol": symbol,
        "loaded_library": str(expected),
        "runtime_log_verified": True,
        "signature_check_verified": True,
    }
