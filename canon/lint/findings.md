# Lint findings

_Semantic-consistency sweep of the canon — run 2026-07-10, **report-only** (no `--apply`)._
_Method: find→refute→record over 4 finding-type lenses (contradiction / superseded / gap / drift), `software` profile weighted. Run **after** compiling sessions `0513d745` (2026-07-10) + `7bae95af` (2026-07-09), which added 10 cabinet pages (62 pages total; structural health ✅ clean: 0 dangling / 0 orphan / 0 contradiction)._

**Result: the same 2 survivors as the 2026-07-08 run, both still present, both unchanged. The 10 newly-compiled pages introduced no new surviving finding.** Both survivors are the 2026-07-01 perf-口径 correction not yet back-propagated into older summary pages — a misleading fossil, not a wrong verdict (the executable path is consistent). Neither is a functional bug.

---

## Survivors (2)

### 1 · Superseded · medium
**Pages:** [[ADR 0002 — Acceptance grounded in catlass and the spec]] · [[catlass acceptance mechanics]]

[[ADR 0002 — Acceptance grounded in catlass and the spec]] (canonical) still names **msTuner** in its parenthetical『（见 [[catlass acceptance mechanics]]：CPU golden、msTuner）』. The page it cites was corrected the next day: [[catlass acceptance mechanics]] (canonical) demotes msTuner to a tuning tool and names **`msprof op`** as the acceptance perf backend. msTuner (best-tiling search duration) and `msprof op` (delivered-kernel profile) are different tools, not synonyms — a reader trusting ADR 0002's shorthand would profile the wrong kernel.

**Suggested resolution:** re-review ADR 0002 —『CPU golden、msTuner』→『CPU golden、msprof op』(or drop the tool name, defer to [[catlass acceptance mechanics]]). ADR 0002's actual decision is unaffected — only the parenthetical is stale.

### 2 · Drift · medium
**Pages:** [[ADR 0006 — Compare performance at a matched timing scope]] · [[ADR 0008 — Reuse AscendOpTest for Task 2]] · [[Acceptance contract and evidence chain]] · [[OpRunway acceptance pipeline]] · [[generated_harness responsibilities]] · [[Performance baseline follows the reference source]]

The perf-gate pass ratio has **two names for one referent**. Five pages call it the fixed figure **"1.2×"** (`generated-harness-responsibilities`, `acceptance-contract-evidence-chain`, `oprunway-acceptance-pipeline`, ADR 0006, ADR 0008); only [[Performance baseline follows the reference source]] and the real `spec.json` files call it the configurable field **`target_ratio`** (taskbook-derived: TBE 无劣化 = 1.0, ≥95% = 0.95, GPU port-class 0.5–0.8×). The literal `1.2` appears **nowhere** in `perf_compare.py`. A reader of the canonical acceptance pages forms a "1.2× gate" model of a threshold the code never uses.

**Suggested resolution:** treat "1.2×" as historical example only; rename the gate concept to `target_ratio` across the five pages, stating it is taskbook-derived.

---

## Refuted this run (no new survivor from the 10 new pages)

- **Contradiction — "target hardware is 950" vs new per-op hardware page** — _refuted._ [[Target hardware and dtype set are determined per operator from taskdoc and op_def]] corrects the "950 is the target" framing, but that framing lived only in `CLAUDE.md` (not a cabinet page). No canon page asserts 950 as the universal target; [[Task spec is authoritative over PR]] actually agrees (cites A2/A3 as the taskdoc side for im2col). No cabinet-vs-cabinet conflict.
- **Contradiction — "gen_cases hardcodes 4 ops" vs adapter/harness generalization pages** — _refuted._ [[Repo adapter interface and modes]] / [[generated_harness responsibilities]] describe generalization as **design intent**; the new page scopes explicitly to the **current elementwise impl state** and cross-references. Design goal vs current state — different scope, both true.
- **Impl-gap — canonical contract requires per-case `oracle_source`, impl hardcodes it** ([[Acceptance contract and evidence chain]] vs [[oracle_source is a hardcoded constant not a recorded fact]]) — _not a contradiction._ Contract states a requirement; the new page states the impl doesn't yet meet it. Both true simultaneously; already captured as its own page under the [[A gate must validate the object that actually takes effect]] theme. Noted, not a lint finding.

---

## Notes
- **Report-only run** — no `status: contested`/`stale` or `contradicts:` edges written. Re-run `bureau:lint --apply` to have the press's health lane set ADR 0002 → `stale` for Survivor #1.
- **Structural health separately clean** (press build 2026-07-10): 62 pages, 0 dangling / 0 orphan / 0 contradiction / 0 schema. These two survivors are the *semantic* residue the mechanical lane cannot see.
- **Resolution is one human edit each** — both are the same 2026-07-01 perf-口径 correction not back-propagated. `bureau:review` on ADR 0002 / the five "1.2×" pages can close them.
