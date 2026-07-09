# Lint findings

_Semantic-consistency sweep of the canon — 32 dossiers (18 architecture + 14 decisions), run 2026-07-08, **report-only** (no `--apply`)._
_Method: 4 finding-type lenses (contradiction / superseded / gap / drift), each an independent reader over the full canon → per-candidate adversarial refutation → survivors only. **7 candidates → 2 survived, 4 refuted.** One drift candidate's dedicated refuter crashed mid-run (`refute:drift#1`, connection closed); that finding is **recovered** below — it survived refutation in the prior run, is corroborated by this run's gap-refuter, and the page contents were re-verified directly. Profiles: `software` weighted (tool-name / value / config drift, decision reversals); `story` has no content in this canon._

Both survivors are the **same residue the prior run found** — the perf-口径 correction (msTuner→`msprof op`; "1.2×"→`target_ratio`) propagated through the mechanism pages and the specs but left **fossils in the older ADRs / summaries**. No new inconsistency appeared; this is a re-confirmation. Neither is a functional bug — the executable path (`perf_compare.py` + `spec.json`) is consistent on `target_ratio`; the harm is a misleading fossil, not a wrong verdict.

---

## Survivors (2)

### 1 · Superseded · medium
**Pages:** [[ADR 0002 — Acceptance grounded in catlass and the spec]] · [[catlass acceptance mechanics]]

[[ADR 0002 — Acceptance grounded in catlass and the spec]] (canonical, 2026-06-30) still names **msTuner** as catlass's performance backend — the parenthetical『（见 [[catlass acceptance mechanics]]：CPU golden、msTuner）』. The page it cites was corrected the next day: [[catlass acceptance mechanics]] (canonical, 2026-07-01) demotes msTuner to『「搜最优 tiling」的调优工具，不是验收工具』and names **`msprof op`** as the acceptance perf backend; [[ADR 0006 — Compare performance at a matched timing scope]] (proposed, 2026-07-06) reinforces the same reclassification. So ADR 0002 retains the pre-correction truth and now mis-summarizes the very page it links — msTuner and `msprof op` are different tools (msTuner reports the best-tiling search duration; `msprof op` profiles the delivered kernel), not synonyms, so a reader trusting ADR 0002's shorthand would profile the wrong kernel.

**Suggested resolution:** re-review ADR 0002 — change『CPU golden、msTuner』→『CPU golden、msprof op』(or drop the tool name and defer to [[catlass acceptance mechanics]]). ADR 0002's actual decision (catlass-as-backend vs ops-test「跑没跑崩」) is unaffected — only the parenthetical is stale.

### 2 · Drift · medium
**Pages:** [[ADR 0006 — Compare performance at a matched timing scope]] · [[ADR 0008 — Reuse AscendOpTest for Task 2]] · [[Acceptance contract and evidence chain]] · [[OpRunway acceptance pipeline]] · [[generated_harness responsibilities]] · [[Performance baseline follows the reference source]]

The perf-gate pass ratio has **two names for one referent**. Five pages call it the fixed figure **"1.2×"** (ADR 0006, ADR 0008, [[Acceptance contract and evidence chain]], [[OpRunway acceptance pipeline]], [[generated_harness responsibilities]]); the mechanism page [[Performance baseline follows the reference source]] and the real `spec.json` files call it the configurable field **`target_ratio`** with taskbook-derived values (TBE 无劣化 = 1.0, ≥95% = 0.95, GPU port-class 0.5–0.8×). The literal `1.2` appears **nowhere** in the perf mechanism — `perf_compare.py` reads `target_ratio` (pass = ratio ≥ `target_ratio`). A reader of the canonical acceptance pages would form a "1.2× gate" mental model of a threshold the code never uses.

_Not a functional bug — each "1.2×" page defers the actual value to `perf_baseline_source`/validator rather than asserting `threshold = 1.2`. Harm is a misleading fossil nickname, not a wrong verdict — hence medium._

**Suggested resolution:** treat "1.2×" as a historical example only; rename the gate concept to `target_ratio` across the five pages, stating it is taskbook-derived (TBE 无劣化 = 1.0, ≥95% = 0.95, GPU 0.5–0.8×).

---

## Discarded after refutation (4)

- **Contradiction — op-acceptance = command/skill vs = agent** ([[OpRunway component breakdown]] vs [[Conversational agent is the sole delivery form]]) — _refuted._ The command/skill framing is **canonical** and self-consistent (component-breakdown 2026-07-02 + ADR 0004: user entry = skill/command, agents = sub-roles). The "op-acceptance = agent" framing lives only on the `status: proposed` (2026-07-08, same-day) delivery-form page — a queued revision, not fact. A proposed page cannot supersede a canonical one; reconciling the framing is the review gate's job on promotion. The proposed page's load-bearing claim is the delivery **form** (conversational, natural-language only), not an artifact-type reclassification. A tier-honoring reader is not misled.
- **Superseded — repo-adapter still pairs example with msTuner** ([[Repo adapter interface and modes]] vs [[catlass acceptance mechanics]]) — _refuted._ Different scope: repo-adapter's『example + msTuner 覆盖不了…无调用壳的 PR』is about **whether a callable shell exists** (the 三接入模式 argument), not about which tool profiles perf; swapping msTuner→`msprof op` there changes nothing in its point. The page explicitly routes perf-tool authority to [[catlass acceptance mechanics]]『依据 [[catlass acceptance mechanics]]』, and it was reviewed 2026-07-06 (after the reclassification) — an illustrative phrasing making no acceptance-tool claim, not pre-correction residue.
- **Gap — "1.2×" perf threshold never defined** — _refuted._ Not a gap: [[Performance baseline follows the reference source]] owns "where the target comes from", [[ADR 0007 — Verdicts come from a deterministic validator]] + `perf_compare.py` own "who computes / what pass means", [[ADR 0006 — Compare performance at a matched timing scope]] owns "same timing_scope". The concept is defined; the residue is same-referent **drift**, already recorded as Survivor #2.
- **Drift — op-acceptance named workflow / command / skill / agent** — _refuted._ "workflow" is not a third artifact-type label: [[OpRunway component breakdown]] self-disambiguates in prose (『⚠ 插件无 workflow 制品类型，故它落成一个 orchestrator command/skill』… 『workflow 剧本语义，非插件 Workflow 制品，见 [[ADR 0009 — One generalized workflow with per-repo adapters]]』). The pages are internally consistent; the "agent" naming is the same proposed-page loose usage covered above.

---

## Notes
- **Report-only run** — no `status: contested`/`stale` or `contradicts:` edges were written. Re-run `bureau:lint --apply` to have the press's structural health lane surface the superseded case (it sets ADR 0002 → `stale`). Both survivors are cabinet-vs-cabinet superseded/drift, not mutual contradictions, so `--apply` writes `stale`, not `contested`.
- **Structural health is separately clean** (lightweight check this run: 32 page titles ↔ 32 wikilink targets, 0 dangling / 0 orphan-reference after excluding `session …`/`Logbook` → logbook). These two are the *semantic* residue the mechanical lane cannot see.
- **Resolution is one human edit** — both survivors are the same 2026-07-01 perf-口径 correction not yet back-propagated into the two older summary pages (ADR 0002 parenthetical; the "1.2×" nickname). `bureau:review` on ADR 0002 / ADR 0006 can close them.
