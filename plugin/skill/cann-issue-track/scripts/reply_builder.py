"""Build markdown reply text for PASS / partial-PASS / FAIL retest outcomes."""
from __future__ import annotations

_PASS_TMPL = """\
感谢您的回复！

我们按照您提供的方案（`{fix_kind}`：`{fix_summary}`）在本地重新跑测，结果如下：

- **算子**：`{repo}` / `{op}`
- **SOC**：`{soc}`
- **结果**：✅ **PASS**

修复信息已记录到本地 FAQ，后续遇到相同问题将自动提示。

此 issue 将标记为已解决并关闭，感谢社区支持！
"""

_PARTIAL_PASS_TMPL = """\
感谢您的回复！

我们按照您提供的方案（`{fix_kind}`：`{fix_summary}`）在本地重新跑测，本 issue 报告的原始失败（`{original_failure_type}`）已恢复：

- **算子**：`{repo}` / `{op}`
- **SOC**：`{soc}`
- **build / install**：✅ 已恢复
- **运行（examples）**：❌ 出现新故障

复测中发现的运行期故障与本 issue 报告的问题**不是同一个**，已另开 follow-up issue 跟踪：
{followup_link}

新故障：
{failed_examples_list}

原始问题视为已解决，关闭本 issue。感谢社区支持！
"""

_FAIL_TMPL = """\
感谢您的回复！

我们尝试了您提供的方案（`{fix_kind}`：`{fix_summary}`）在本地重新跑测，但问题未能解决：

- **算子**：`{repo}` / `{op}`
- **SOC**：`{soc}`
- **结果**：❌ **FAIL**

错误摘要：
```
{error_snippet}
```

能否提供更多信息？例如完整环境配置或其他排查思路，非常感谢。
"""

_FOLLOWUP_ISSUE_TMPL = """\
> Follow-up to {source_issue_url}

跟踪 issue {source_issue_url} 复测时发现的、与原 BUILD/INSTALL 失败**不同**的新故障。
原 issue 报告的失败已被修复方案恢复（build / install 均已通过），但在运行期出现新报错：

- **仓**：`{repo}`
- **算子**：`{op}`
- **SOC**：`{soc}`
- **触发场景**：跑 `{repo}` 的 phase1 examples（build/install 已成功）

### 失败面

{failed_examples_block}

### 错误片段

```
{error_snippet}
```

### 本地环境

- CANN: 见仓内 README
- 修复方案（已对原 issue 应用）：`{fix_kind}` → `{fix_summary}`

烦请 maintainer 看一下该算子在 `{soc}` 上的运行期支持情况，谢谢。
"""


def build_pass_reply(*, repo: str, op: str, soc: str, fix_kind: str, fix_summary: str) -> str:
    return _PASS_TMPL.format(
        repo=repo, op=op, soc=soc,
        fix_kind=fix_kind, fix_summary=fix_summary,
    ).strip()


def build_partial_pass_reply(
    *,
    repo: str,
    op: str,
    soc: str,
    fix_kind: str,
    fix_summary: str,
    original_failure_type: str,
    failed_examples: list[str],
    followup_issue_url: str,
) -> str:
    """PASS branch when the originally-reported build/install failure was fixed
    but the retest surfaced a new, unrelated runtime failure that was filed as a
    follow-up issue. `followup_issue_url` is rendered as a markdown link.

    跑测每个算子只有一次 `--run_example <op>`(一个 run 状态),没有逐 example 的
    pass/fail 明细,所以这里**不写 "N/M 个示例通过"** —— 那是编的。
    """
    link = f"- {followup_issue_url}"
    failed_list = "\n".join(f"- `{name}`" for name in failed_examples) or "- （未提供）"
    return _PARTIAL_PASS_TMPL.format(
        repo=repo, op=op, soc=soc,
        fix_kind=fix_kind, fix_summary=fix_summary,
        original_failure_type=original_failure_type,
        followup_link=link,
        failed_examples_list=failed_list,
    ).strip()


def build_fail_reply(
    *,
    repo: str,
    op: str,
    soc: str,
    fix_kind: str,
    fix_summary: str,
    error_snippet: str = "",
) -> str:
    snippet = (error_snippet or "（无详细错误信息）")[:800]
    return _FAIL_TMPL.format(
        repo=repo, op=op, soc=soc,
        fix_kind=fix_kind, fix_summary=fix_summary,
        error_snippet=snippet,
    ).strip()


def build_followup_issue_body(
    *,
    repo: str,
    op: str,
    soc: str,
    source_issue_url: str,
    fix_kind: str,
    fix_summary: str,
    failed_examples: list[str],
    error_snippet: str = "",
) -> str:
    """Body for a new follow-up issue describing the *new* runtime failures
    found while verifying the original issue's fix.

    Title is the caller's responsibility (e.g. f"[{repo}] {op} <variants>
    fail at runtime on {soc} (follow-up to #N)")."""
    block = "\n".join(f"- `{name}`" for name in failed_examples) or "- （未提供）"
    snippet = (error_snippet or "（无详细错误信息）")[:1500]
    return _FOLLOWUP_ISSUE_TMPL.format(
        repo=repo, op=op, soc=soc,
        source_issue_url=source_issue_url,
        fix_kind=fix_kind, fix_summary=fix_summary,
        failed_examples_block=block,
        error_snippet=snippet,
    ).strip()
