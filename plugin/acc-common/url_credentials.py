"""URL 里的用户凭据：判别与脱敏 —— Layer 0，**唯一实现**。

**为什么单独一个模块**：本仓有多处会把外部 URL 写进落盘产物或人读报告
（`fetch_source` 的 `taskdoc.source_locator`、vendor build receipt 的 `source.repo`、
验收报告的「源码仓」一行）。`https://<user>:<token>@host/…` 一旦进了其中任何一处，
就撞仓规 §2「token、密码、私钥不得写进任何产物」。

判别规则**只留一份实现**：产出侧（落盘前扣留）与读侧（校收据 / 渲染前再拦）各写一套的话，
两边迟早对同一个 URL 给出不同答案，那时「产出侧扣了、读侧没拦」或反过来，门就成了摆设。

⚠ 已知残留面（如实记账，别当已封）：本模块只堵 **userinfo** 形态。
query 串里的凭据（`?private_token=…`、`?access_token=…`）**没堵**——要判它就得靠参数名
关键词猜，猜漏与误杀都会让这道门变成「看着有、实际拦不住」的假门。真要堵，得改成
「只保留 origin+path、整段丢弃 query」这类结构性规则，那会改动所有 http 任务书链接的
落盘字节，属另一次改动。

纯 stdlib、零本仓依赖，可被任何 Layer 0/1 脚本 import。
"""

import re

_URL_AUTHORITY_END_RE = re.compile(r"[/?#]")


def _split_url_authority(url):
    """`scheme://authority<rest>` → `(scheme, authority, rest)`；不是 `://` 形态返回 None。"""
    if not isinstance(url, str) or "://" not in url:
        return None
    scheme, rest = url.split("://", 1)
    m = _URL_AUTHORITY_END_RE.search(rest)
    return (scheme, rest, "") if m is None else (scheme, rest[:m.start()], rest[m.start():])


def url_has_userinfo(url):
    """`scheme://userinfo@host/…` 形态即判「带用户凭据」。

    ⚠ 只认 `://` 形式：scp 式 `git@host:path` 的 `@` 前面是用户名、不含任何密钥，
    拦它会把合法的 SSH remote 全部误伤。而 `https://user:pw@host/…`（密码）与
    `https://<token>@host/…`（PAT，连冒号都没有）都落在 `://` 形式里，一并拦下。

    ⚠ authority 的终止符取 `/` `?` `#` 里**最先出现**的那个，不能只切 `/`。只切 `/` 会把
    query 吞进 authority：`https://host?a=b@c` 这种**根本不含凭据**的 URL 会被判成带凭据
    （query 里的 `@`），于是一个合法 URL 被白白扣留、脱敏后还被截成 `https://***@c`。
    判过头与判不到同样是坏门。
    """
    parts = _split_url_authority(url)
    return bool(parts) and "@" in parts[1]


def redact_url_userinfo(url):
    """把 `scheme://userinfo@host/…` 脱敏成 `scheme://***@host/…`；不带 userinfo 的原样返回。

    ⚠ **只用于人读文本与旁路记账字段**：脱敏会改字节，而 CP-F 对 `repo` 是逐字比对，
    把脱敏值当载重字段用等于换个方式制造 BLOCK。

    ⚠ userinfo 里若含未转义的 `@`（不合规但确实存在），按 WHATWG URL 的口径以**最后一个**
    `@` 分隔——即 `@` 之前的字节**全部丢弃**。宁可多丢，也不能漏出半截 token。
    """
    parts = _split_url_authority(url)
    if not parts or "@" not in parts[1]:
        return url
    scheme, authority, rest = parts
    return f"{scheme}://***@{authority.rsplit('@', 1)[1]}{rest}"
