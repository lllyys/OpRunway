# Audit Findings

**Run**: audit-fix 20260810-212343  
**Scope**: `tests/witnesses/gaussian_blur/execution_plugin.py`, `tests/test_witness_inputs.py`  
**Audit type**: full  
**Model**: `grok-4.5`  
**Fixer**: Codex  
**Audit threads**: `execution_plugin.py=019fee69-be7c-78c3-99cf-36ee2073bc13`, `test_witness_inputs.py=019fee6a-2b59-78a0-ae04-941fa6a6a1f8`  
**Status**: degraded-clean; both Grok calls were rate-limited before producing output, so Codex performed the same nine-dimension read-only fallback audit and did not start a fix loop.

| # | File | Line | Severity | Dimension | Finding | Suggested fix | Auditor | Status | Round | Notes |
|---|------|------|----------|-----------|---------|---------------|---------|--------|-------|-------|

No supported finding was identified by the fallback audit. This record is not a Grok fresh verification. The two failed jobs were `audit-fix-audit-msnz6n1g-y9xmtc` and `audit-fix-audit-msnz78lv-nut24r`; both returned `Rate limited` with empty output.

Target evidence before this checkpoint:

- A3 full target suite: 74 tests, zero skips, `OK`, log SHA-256 `8c0d8c3b40786efc3f89aebe97bc7158c96b7d3841345bf0157463bd64193469`.
- A5 full target suite: 74 tests, zero skips, `OK`, log SHA-256 `1fa529b579c5d1041a97d3fea238f358105a919861ae1f304873dd1c58af6124`.
- The focused cache-bypass test executed and passed separately on both targets.
