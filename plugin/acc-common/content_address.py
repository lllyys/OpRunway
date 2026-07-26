"""内容寻址工件的最小、工具中立基础层。

本模块只处理 JSON 值、摘要、受根目录约束的路径和原子文件写入；不认识
spec/caseset/evidence/verdict，也不接入任何跑测或裁决路径。
"""

import hashlib
import json
import math
import os
import stat
import tempfile


_HASH_PREFIX = b"oprunway-content-address-v1\0"
_ARTIFACT_KEYS = frozenset({"schema_version", "domain", "digest", "payload"})
_SCHEMA_VERSION = 1


class ContentAddressError(ValueError):
    """内容或内容寻址工件不满足严格契约。"""


def _validate_json(value, where="$"):
    """只接受 JSON 数据模型，并在序列化前拒绝 NaN/Inf 与非字符串键。"""
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContentAddressError(f"{where}: JSON 浮点数必须有限，得 {value!r}")
        return
    if isinstance(value, list):
        for i, item in enumerate(value):
            _validate_json(item, f"{where}[{i}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContentAddressError(
                    f"{where}: JSON object 的 key 必须是字符串，得 {type(key).__name__}")
            _validate_json(item, f"{where}.{key}")
        return
    raise ContentAddressError(
        f"{where}: 非 JSON 类型 {type(value).__name__}（仅允许 null/bool/number/string/list/object）")


def canonical_json_bytes(value):
    """返回确定性的 UTF-8 JSON 字节；对象键排序、无无意义空白、禁止 NaN/Inf。"""
    _validate_json(value)
    try:
        text = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
        return text.encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as ex:
        raise ContentAddressError(f"无法编码 canonical JSON: {ex}") from ex


def content_digest(domain, value):
    """计算带长度前缀域分离的 sha256 十六进制摘要。"""
    if not isinstance(domain, str) or not domain:
        raise ContentAddressError("domain 必须是非空字符串")
    try:
        domain_bytes = domain.encode("utf-8")
    except UnicodeError as ex:
        raise ContentAddressError(f"domain 不是有效 UTF-8 字符串: {ex}") from ex
    if len(domain_bytes) > 0xFFFFFFFF:
        raise ContentAddressError("domain 过长")
    framed = (_HASH_PREFIX + len(domain_bytes).to_bytes(4, "big")
              + domain_bytes + canonical_json_bytes(value))
    return hashlib.sha256(framed).hexdigest()


def safe_path(root, relative_path):
    """把相对路径安全地约束在 root 内，并拒绝已有路径段中的符号链接。

    返回绝对路径。root 必须是已存在的真实目录；relative_path 必须是规范的、
    非空的相对路径，不接受 ``.``、``..``、绝对路径或 NUL。
    """
    if not isinstance(root, (str, os.PathLike)):
        raise ContentAddressError("root 必须是路径")
    if not isinstance(relative_path, (str, os.PathLike)):
        raise ContentAddressError("relative_path 必须是路径")
    root_abs = os.path.abspath(os.fspath(root))
    rel = os.fspath(relative_path)
    if not os.path.isdir(root_abs):
        raise ContentAddressError(f"root 必须是已存在目录: {root_abs!r}")
    if os.path.islink(root_abs):
        raise ContentAddressError(f"root 不得是符号链接: {root_abs!r}")
    if not rel or "\x00" in rel or os.path.isabs(rel):
        raise ContentAddressError(f"须提供非空、安全的相对路径: {rel!r}")
    parts = rel.split(os.sep)
    if any(p in ("", ".", "..") for p in parts):
        raise ContentAddressError(f"相对路径含空段、`.` 或 `..`: {rel!r}")
    candidate = os.path.abspath(os.path.join(root_abs, *parts))
    try:
        if os.path.commonpath((root_abs, candidate)) != root_abs:
            raise ContentAddressError(f"路径逃逸 root: {rel!r}")
    except ValueError as ex:
        raise ContentAddressError(f"路径与 root 不同卷: {rel!r}") from ex
    current = root_abs
    for part in parts:
        current = os.path.join(current, part)
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            break
        except OSError as ex:
            raise ContentAddressError(f"无法检查路径段 {current!r}: {ex}") from ex
        if stat.S_ISLNK(mode):
            raise ContentAddressError(f"路径段是符号链接，拒绝: {current!r}")
    return candidate


def _fsync_dir(path):
    """尽力把目录项刷盘；平台不支持目录 fsync 时让真实 OSError 向上传递。"""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_json(root, relative_path, value):
    """在 root 内原子写 canonical JSON，成功后返回绝对路径。

    临时文件与目标同目录；文件 fsync 后 ``os.replace``，再 fsync 父目录。
    任何失败都会尽力移除临时文件，且不会通过符号链接写出 root。
    """
    data = canonical_json_bytes(value)
    target = safe_path(root, relative_path)
    parent = os.path.dirname(target)
    os.makedirs(parent, exist_ok=True)
    # mkdir 后重新检查，防并发方在创建期间换入软链。
    target = safe_path(root, relative_path)
    parent = os.path.dirname(target)
    fd, tmp = tempfile.mkstemp(prefix=".oprunway-tmp-", dir=parent)
    try:
        with os.fdopen(fd, "wb") as out:
            fd = -1
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        # replace 前再查一次目标链路，缩小目录换靶窗口。
        safe_path(root, relative_path)
        os.replace(tmp, target)
        tmp = None
        _fsync_dir(parent)
    finally:
        if fd != -1:
            os.close(fd)
        if tmp is not None:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
    return target


def make_artifact(domain, payload):
    """构造自校验的内容寻址 JSON envelope。"""
    digest = content_digest(domain, payload)
    return {
        "schema_version": _SCHEMA_VERSION,
        "domain": domain,
        "digest": digest,
        "payload": payload,
    }


def write_artifact(root, relative_path, domain, payload):
    """原子写入带摘要 envelope 的内容寻址工件。"""
    return atomic_write_json(root, relative_path, make_artifact(domain, payload))


def read_artifact(root, relative_path, expected_domain):
    """严格读取并复核 envelope；任何漂移、篡改或契约异常均抛错。"""
    path = safe_path(root, relative_path)
    try:
        with open(path, "r", encoding="utf-8") as src:
            artifact = json.load(
                src,
                parse_constant=lambda token: (_ for _ in ()).throw(
                    ContentAddressError(f"非法 JSON 常量: {token}")),
            )
    except ContentAddressError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as ex:
        raise ContentAddressError(f"无法读取内容寻址工件 {path!r}: {ex}") from ex
    _validate_json(artifact)
    if not isinstance(artifact, dict) or frozenset(artifact) != _ARTIFACT_KEYS:
        raise ContentAddressError(
            f"工件 envelope 字段必须严格等于 {sorted(_ARTIFACT_KEYS)}")
    if artifact["schema_version"] != _SCHEMA_VERSION:
        raise ContentAddressError(
            f"不支持的 schema_version: {artifact['schema_version']!r}")
    if not isinstance(expected_domain, str) or not expected_domain:
        raise ContentAddressError("expected_domain 必须是非空字符串")
    if artifact["domain"] != expected_domain:
        raise ContentAddressError(
            f"domain 不匹配: expected={expected_domain!r}, actual={artifact['domain']!r}")
    digest = artifact["digest"]
    if not (isinstance(digest, str) and len(digest) == 64
            and all(c in "0123456789abcdef" for c in digest)):
        raise ContentAddressError(f"digest 不是小写 sha256: {digest!r}")
    actual = content_digest(expected_domain, artifact["payload"])
    if digest != actual:
        raise ContentAddressError(
            f"工件摘要不匹配: recorded={digest}, actual={actual}")
    return artifact["payload"]
