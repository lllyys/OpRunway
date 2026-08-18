# Audit Findings

**Run**: audit-fix 20260810-213309  
**Scope**: all 33 active `plugin/` files plus `AGENTS.md`, `dev-doc/oprunway-todo.md`, `dev-doc/oprunway-changes-brief.md`, and `dev-doc/oprunway-real-machine-environment.md`  
**Audit type**: full  
**Model**: `grok-4.5`  
**Fixer**: Codex  
**Audit threads**: `plugin/.claude-plugin/plugin.json=019fee71-551d-7430-9ad9-e5d8e2cfe0eb`  
**Status values**: open | fixed | not-fixed | partial | regressed | skipped (severity filter) | skipped (user stop)  
**Run status**: degraded; the first fresh Grok call was rate-limited before output, so Codex completed the same nine-dimension read-only fallback audit and did not start a fix loop.

| # | File | Line | Severity | Dimension | Finding | Suggested fix | Auditor | Status | Round | Notes |
|---|------|------|----------|-----------|---------|---------------|---------|--------|-------|-------|
| 1 | plugin/README.md | 25 | Medium | Correctness and documentation | README says accuracy and performance execution errors remain `PLUGIN_ERROR`, but `cli._attempt` deterministically maps timeout and command-isolation failures to `BLOCKED`. This contradicts actual terminal reporting. | State that execution failures remain non-DUT; timeout/environment/isolation blockers use `BLOCKED`, while other workflow implementation failures use `PLUGIN_ERROR`. | Codex fallback | open | - | Grok job produced no output. |
| 2 | plugin/skills/acceptance-workflow/SKILL.md | 95 | Medium | Maintainability and knowledge transfer | The skill says complex cases may pass a generator or execution plugin but gives no decision rule for design-only, generator, execution/accuracy, core capability promotion, or insufficient ABI facts. That leaves each ATK gap to ad-hoc judgment and risks witness behavior leaking into core. | Add a compact capability decision matrix and the rule that witness behavior stays local until a second independent operator demonstrates the same stable gap; missing ABI/task facts yield `NEEDS_INPUT`. | Codex fallback | open | - | Documentation-only decision boundary; no runtime change proposed. |
| 3 | plugin/README.md | 3 | Medium | Compliance and documentation | README describes an NPU operator acceptance entry without stating that the current build boundary is only `atk_aclnn` with `cann_ops_package_v1`; `contract.BUILD_PROFILES` accepts only that profile. Readers can mistake operator generality for arbitrary repository/build-profile support. | Explicitly document that the implementation is operator-generic only inside `atk_aclnn + cann_ops_package_v1`, and that a second real repository shape requires a new build-profile adapter rather than branches in the existing profile. | Codex fallback | open | - | Matches the current deterministic contract; no new adapter should be built now. |

Fallback evidence:

- Active surface is one agent, one skill, one command, one CLI, one deterministic package, and 33 active plugin files.
- Core contains no branch on the four witness operator identities and no GPU execution path.
- A3 and A5 full target suites each passed 74/74 with zero skips; all four real ATK case generators executed.
- Current Gaussian witness additionally passed a real A5 exact K5 `performance_device` run with finite device time and both profiler CSV kinds.
- `git diff --check` passes. This evidence does not substitute for the missing Grok fresh verification.
