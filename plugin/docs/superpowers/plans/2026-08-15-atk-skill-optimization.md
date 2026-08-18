# ATK Skill Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce context cost and ambiguity in the ATK acceptance skill while preserving existing acceptance algorithms, CLI behavior, output schemas, and test expectations.

**Architecture:** Keep the existing scripts as the behavioral implementation. Rewrite the skill as a compact stage router, consolidate prose into stage contracts, and add static document checks for line budgets, cross-reference dependencies, and command-route drift. Remove runtime-irrelevant caches and tests from the skill package.

**Tech Stack:** Markdown, JSON, Python `argparse`, pytest.

---

### Task 1: Add the documentation quality gate

**Files:**
- Modify: `skill/repo-task-atk-test/tests/test_document_style.py`
- Test: `skill/repo-task-atk-test/tests/test_document_style.py`

- [ ] **Step 1: Add failing assertions** for a compact main skill, no reference-to-reference Markdown links, no `__pycache__` package files, and no duplicate route entries.
- [ ] **Step 2: Run** `python3 -m pytest -q skill/repo-task-atk-test/tests/test_document_style.py`; confirm the new budget/dependency assertions fail against the current documents.
- [ ] **Step 3: Implement** only the helper functions needed to scan Markdown links, route tables, and package files.
- [ ] **Step 4: Run** the same test and confirm it passes after the documentation rewrite tasks.

### Task 2: Rewrite the main skill as a stage router

**Files:**
- Modify: `skill/repo-task-atk-test/SKILL.md`

- [ ] **Step 1: Replace** the 497-line body with trigger context, immutable invariants, a ten-stage table, stage-specific reference routes, and the minimal command map.
- [ ] **Step 2: Preserve** every existing stop condition, interface facet single-generation rule, precision denominator rule, and evidence requirement exactly once.
- [ ] **Step 3: Run** the document-style tests and inspect the resulting line count and route targets.

### Task 3: Consolidate references by decision domain

**Files:**
- Modify: `skill/repo-task-atk-test/references/intake.md`
- Modify: `skill/repo-task-atk-test/references/case-design.md`
- Modify: `skill/repo-task-atk-test/references/yaml-schema.md`
- Modify: `skill/repo-task-atk-test/references/plugin-authoring.md`
- Modify: `skill/repo-task-atk-test/references/execution.md`
- Modify: `skill/repo-task-atk-test/references/build-deploy.md`
- Modify: `skill/repo-task-atk-test/references/atk-cli.md`
- Modify: `skill/repo-task-atk-test/references/performance.md`
- Modify: `skill/repo-task-atk-test/references/reporting.md`
- Modify: `skill/repo-task-atk-test/references/experimental_standard.md`
- Modify: `skill/repo-task-atk-test/references/atk-parameter-capabilities.md`

- [ ] **Step 1: Remove** duplicated global rules and move each shared machine-checkable rule to `acceptance-policy.json` references.
- [ ] **Step 2: Split** long plugin and design explanations into directly routed sections with short decision tables; retain normative examples only where the agent must copy a shape or contract.
- [ ] **Step 3: Replace** source-code archaeology and historical rationale with concise, versioned behavior facts and explicit evidence boundaries.
- [ ] **Step 4: Remove** all Markdown links from one reference to another; make each reference self-contained for its routed decision.
- [ ] **Step 5: Run** the document-style tests and a Markdown link scan.

### Task 4: Align script routing without changing script behavior

**Files:**
- Modify: `skill/repo-task-atk-test/SKILL.md`
- Modify: `skill/repo-task-atk-test/tests/test_document_style.py`
- Test: all `skill/repo-task-atk-test/tests/test_*.py`

- [ ] **Step 1: Derive** each public script's options with `python3 scripts/<name>.py --help` and compare them with the compact route table.
- [ ] **Step 2: Remove** duplicated full command signatures from Markdown; retain only stage, purpose, required input, and output.
- [ ] **Step 3: Add** a route-consistency test that fails when a documented script is absent or a documented required option is not accepted.
- [ ] **Step 4: Run** the route test and the complete skill test suite.

### Task 5: Clean the published skill boundary and verify

**Files:**
- Modify: `.gitignore` only if needed for generated caches
- Delete from package: `skill/repo-task-atk-test/scripts/__pycache__/`, `skill/repo-task-atk-test/tests/__pycache__/`

- [ ] **Step 1: Remove** Python bytecode caches from the skill package and confirm no test files are required by runtime scripts.
- [ ] **Step 2: Run** `python3 -m pytest -q skill/repo-task-atk-test/tests`.
- [ ] **Step 3: Run** `python3 /Users/justbin/.codex/skills/.system/skill-creator/scripts/quick_validate.py skill/repo-task-atk-test` if available.
- [ ] **Step 4: Run** `git diff --check` and review the final diff for preserved algorithms, outputs, and user-owned unrelated changes.

