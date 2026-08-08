"""公开任务身份与源码注册 kernel op type 的确定性绑定。

只识别目标源码中的 ``OP_ADD(<Identifier>)`` 注册。扫描器会先屏蔽 C/C++
注释、字符/字符串及 raw string；零个或多个候选都不能产生执行身份。
"""

import hashlib
import posixpath
import re

import content_address


SOURCE_SCHEMA = "oprunway.kernel_identity_source"
SPEC_BINDING_SCHEMA = "oprunway.kernel_identity_spec_binding"
SCHEMA_VERSION = 1
DISCOVERY = "op_add_lexical_v1"
_IDENTITY_DOMAIN = "oprunway/kernel-identity-source/v1"
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_OP_ADD = re.compile(r"\bOP_ADD\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)")


class KernelIdentityError(ValueError):
    """kernel identity 事实或 spec 绑定不满足 current 契约。"""


def _blank_segment(text):
    return "".join("\n" if ch == "\n" else " " for ch in text)


def _lexical_code(text):
    """屏蔽注释与字面量，同时保留长度和换行，供行号稳定定位。"""
    if not isinstance(text, str):
        raise KernelIdentityError("kernel identity 扫描输入必须是文本")
    out, i, n = [], 0, len(text)
    while i < n:
        if text.startswith("//", i):
            # C++ translation phase 2 removes ``\\\n`` before comments are
            # recognized.  Therefore a // comment continues across every
            # escaped physical newline.  Preserve those newlines in the mask
            # so reported source line numbers remain physical line numbers.
            end = i + 2
            while end < n:
                if text.startswith("\\\r\n", end):
                    end += 3
                    continue
                if text.startswith("\\\n", end):
                    end += 2
                    continue
                if text[end] in "\r\n":
                    break
                end += 1
            out.append(_blank_segment(text[i:end]))
            i = end
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                out.append(_blank_segment(text[i:]))
                break
            end += 2
            out.append(_blank_segment(text[i:end]))
            i = end
            continue
        raw = re.match(r'(?:u8|u|U|L)?R"([^\s\\()]*)\(', text[i:])
        if raw:
            delimiter = raw.group(1)
            close = ")" + delimiter + '"'
            end = text.find(close, i + raw.end())
            end = n if end < 0 else end + len(close)
            out.append(_blank_segment(text[i:end]))
            i = end
            continue
        prefix = re.match(r'(?:u8|u|U|L)?(["\'])', text[i:])
        if prefix:
            quote = prefix.group(1)
            j = i + prefix.end()
            escaped = False
            while j < n:
                ch = text[j]
                j += 1
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == quote:
                    break
            out.append(_blank_segment(text[i:j]))
            i = j
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def scan_op_add(text):
    """返回按源码位置排序的 ``(identifier, line)`` 列表。"""
    code = _lexical_code(text)
    return [(match.group(1), code.count("\n", 0, match.start()) + 1)
            for match in _OP_ADD.finditer(code)]


def _unsigned(fact):
    return {key: value for key, value in fact.items() if key != "identity_sha256"}


def discover(files, *, target_scope, content_anchor):
    """从已经限定到目标 scope 的 op_def 文本构造 source fact。"""
    if not isinstance(files, dict):
        raise KernelIdentityError("kernel identity files 必须是 path→text object")
    if not isinstance(target_scope, str) or not target_scope:
        raise KernelIdentityError("kernel identity target_scope 缺失")
    if not isinstance(content_anchor, dict):
        raise KernelIdentityError("kernel identity content_anchor 缺失")
    scanned, candidates = [], []
    for path in sorted(files):
        text = files[path]
        if not isinstance(path, str) or not path or not isinstance(text, str):
            raise KernelIdentityError("kernel identity 扫描项须为非空 path 与文本")
        raw_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        scanned.append({"path": path, "sha256": raw_sha})
        for op_type, line in scan_op_add(text):
            candidates.append({
                "kernel_op_type": op_type,
                "source_path": path,
                "source_sha256": raw_sha,
                "line": line,
            })
    count = len(candidates)
    status = "exact" if count == 1 else ("missing" if count == 0 else "ambiguous")
    fact = {
        "schema": SOURCE_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "discovery": DISCOVERY,
        "target_scope": target_scope,
        "content_anchor": content_anchor,
        "scanned_files": scanned,
        "candidates": candidates,
        "candidate_count": count,
        "status": status,
    }
    fact["identity_sha256"] = content_address.content_digest(
        _IDENTITY_DOMAIN, fact)
    return fact


def validate(fact, *, content_anchor=None, require_exact=True):
    """严格验证 source fact；返回唯一候选（不要求唯一时返回 ``None``）。"""
    if not isinstance(fact, dict) or fact.get("schema") != SOURCE_SCHEMA \
            or fact.get("schema_version") != SCHEMA_VERSION \
            or fact.get("discovery") != DISCOVERY:
        raise KernelIdentityError("source_facts kernel_identity schema 不受支持")
    fact_anchor = fact.get("content_anchor")
    if not isinstance(fact_anchor, dict):
        raise KernelIdentityError("kernel identity content_anchor 缺失")
    if content_anchor is not None and fact_anchor != content_anchor:
        raise KernelIdentityError("kernel identity 与 source_facts content_anchor 不一致")
    scope = fact.get("target_scope")
    if (not isinstance(scope, str) or not scope or scope.startswith("/")
            or "\\" in scope or posixpath.normpath(scope) != scope
            or any(segment in ("", ".", "..") for segment in scope.split("/"))):
        raise KernelIdentityError("kernel identity target_scope 非规范相对路径")
    if fact_anchor.get("scope") != scope:
        raise KernelIdentityError(
            "kernel identity target_scope 与 content_anchor.scope 不一致")
    scanned = fact.get("scanned_files")
    candidates = fact.get("candidates")
    if not isinstance(scanned, list) or not isinstance(candidates, list):
        raise KernelIdentityError("kernel identity scanned_files/candidates 非数组")
    by_path = {}
    for item in scanned:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) \
                or not isinstance(item.get("sha256"), str) \
                or not _HEX64.fullmatch(item["sha256"]) or item["path"] in by_path:
            raise KernelIdentityError("kernel identity scanned_files 非法或重复")
        path = item["path"]
        if (path.startswith("/") or "\\" in path
                or posixpath.normpath(path) != path
                or any(segment in ("", ".", "..") for segment in path.split("/"))
                or not path.startswith(scope + "/")
                or not path.endswith("_def.cpp")):
            raise KernelIdentityError(
                "kernel identity scanned_files 必须是 target_scope 内的 *_def.cpp")
        by_path[item["path"]] = item["sha256"]
    for item in candidates:
        if not isinstance(item, dict) \
                or not isinstance(item.get("kernel_op_type"), str) \
                or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item["kernel_op_type"]) \
                or by_path.get(item.get("source_path")) != item.get("source_sha256") \
                or not isinstance(item.get("line"), int) or isinstance(item.get("line"), bool) \
                or item["line"] < 1:
            raise KernelIdentityError("kernel identity candidate 未绑定扫描文件")
    count = len(candidates)
    expected_status = "exact" if count == 1 else ("missing" if count == 0 else "ambiguous")
    if fact.get("candidate_count") != count or fact.get("status") != expected_status:
        raise KernelIdentityError("kernel identity candidate_count/status 不一致")
    expected_digest = content_address.content_digest(_IDENTITY_DOMAIN, _unsigned(fact))
    if fact.get("identity_sha256") != expected_digest:
        raise KernelIdentityError("kernel identity 内容摘要漂移")
    if require_exact and count != 1:
        raise KernelIdentityError(
            f"kernel identity 必须恰有一个 OP_ADD 候选，实得 {count}（{expected_status}）")
    return candidates[0] if count == 1 else None


def spec_execution(fact):
    """由 exact source fact 生成可写入 spec.execution 的绑定。"""
    candidate = validate(fact)
    return {
        "kernel_op_type": candidate["kernel_op_type"],
        "source_binding": {
            "schema": SPEC_BINDING_SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "identity_sha256": fact["identity_sha256"],
            "candidate": dict(candidate),
        },
    }


def resolve(spec, source_facts, *, require_explicit=False):
    """以 source_facts 为权威，解析 public/internal 两种身份。

    新生成的 current spec 应显式写 ``execution``；字段缺席只保留 exact
    单候选的确定性读取兼容，不从公开名、目录或 API 名推断任何值。
    """
    if not isinstance(spec, dict) or not isinstance(spec.get("op"), str) or not spec["op"]:
        raise KernelIdentityError("spec.op 公开任务身份缺失")
    if not isinstance(source_facts, dict):
        raise KernelIdentityError("source_facts 缺失")
    pr = source_facts.get("pr")
    derived = source_facts.get("derived")
    fact = derived.get("kernel_identity") if isinstance(derived, dict) else None
    anchor = pr.get("content_anchor") if isinstance(pr, dict) else None
    candidate = validate(fact, content_anchor=anchor)
    expected_execution = spec_execution(fact)
    if "execution" not in spec and require_explicit:
        raise KernelIdentityError(
            "current 正式 spec 缺 execution；新验收必须显式镜像 source_facts 唯一候选")
    if "execution" not in spec:
        resolution = "derived_exact_source_candidate"
    else:
        execution = spec.get("execution")
        if not isinstance(execution, dict) or execution != expected_execution:
            raise KernelIdentityError(
                "spec.execution kernel_op_type/source_binding 与 source_facts 唯一候选不一致")
        resolution = "explicit_spec_binding"
    return {
        "public_op": spec["op"],
        "kernel_op_type": candidate["kernel_op_type"],
        "resolution": resolution,
        "source_binding": expected_execution["source_binding"],
    }
