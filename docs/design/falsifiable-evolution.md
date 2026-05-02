# Falsifiable Evolution — Phase O design

> Make every Nexus self-evolution edit a falsifiable, on-chain
> contract: each `MemoryEvolver` / `SkillEvolver` / `PersonaEvolver` /
> `KnowledgeCompiler` write declares **predicted fixes** and
> **predicted regressions**; the next compaction round verifies, and
> failed predictions auto-rollback. Inspired by the AHE paper
> (*Agentic Harness Engineering*, Lin et al., arXiv:2604.25850v3,
> Apr 2026), which proved this pattern lifts coding-agent pass@1 by
> +7.3 pp over 10 iterations.
>
> **Status:** Draft. **Owner:** huihzhao.
> **Inputs:** AHE paper + BEP-Nexus v0.2 design doc + code review.
> **Output:** ROADMAP **Phase O — Falsifiable Evolution**, plus
> additions to BEP-Nexus v0.2 schema.

---

## 0. Why this, why now

### The gap

Nexus's evolution stack today (`packages/nexus/nexus/evolution/`) has
four evolvers:

| Evolver | Writes to | Triggered by |
| --- | --- | --- |
| `MemoryEvolver` | `curated_memory` (facts) | every chat batch |
| `SkillEvolver` | `skills/*` | task completion + conversation |
| `PersonaEvolver` | `persona.txt` | weekly |
| `KnowledgeCompiler` | `knowledge/*` | monthly |

Each writes its output back into the agent's curated state. **None of
them verifies whether the edit actually helped.** A hallucinated fact,
a regressive skill update, a persona drift toward sycophancy — once
written, they propagate to every future projection. The only safety
net is `ContractEngine.DriftScore`, which is *observational and
post-hoc* (it watches compliance over a window, but it can't say
*"this specific edit caused that drift"*).

### The AHE evidence

The AHE paper builds a coding-agent harness that evolves itself
through a *closed loop* with three observability pillars
(Component / Experience / Decision). The empirical results matter:

- **+7.3 pp** on Terminal-Bench 2 (69.7% → 77.0%) over 10 iterations.
- Beats human-designed Codex-CLI (71.9%) and self-evolving baselines
  ACE (68.9%) and TF-GRPO (72.3%).
- Frozen harness *transfers*: +5.1 to +10.1 pp across alternate base
  models, no re-evolution.

The single most important architectural decision the paper isolates:
**every edit is a self-declared, falsifiable contract**. Not "the
agent wrote what it thought was good" — but "the agent wrote *and
predicted what would break*, and next round we check". Failed
predictions → revert. This is what makes the loop converge instead
of randomly walking.

The paper also reports negative findings we should not ignore:

1. **Components interact non-additively** — memory alone +5.6 pp,
   tools alone +3.3 pp, but stacking them gives less than the sum.
2. **The loop is blind to regressions** — fix-prediction precision
   is 5x random baseline (33.7% vs 6.5%); regression-prediction is
   essentially random (11.8% vs 5.6%). Translation: **the agent can
   find improvements but can't predict damage**.

These aren't bugs — they're properties of the technique. Our design
must accommodate them.

### Why now

We're about to ship Phase I-N (BEP v0.2). Two things make this the
right moment:

- **Phase J adds the 5-namespace memory taxonomy** — a clean point
  to bake in versioned commits per namespace.
- **Phase L hardens the on-chain contract** — adding new event
  types now is cheap; doing so post-mainnet is expensive.

Adding "falsifiable evolution" as Phase O lets us pin the contract
*and* the storage shape together. Skip it, and we ship an unverifiable
self-evolving agent with no audit trail. That's a correctness
liability, not just a feature gap.

---

## 1. Goals + non-goals

### Goals

| G | Statement |
| --- | --- |
| G1 | Every evolution edit (across all 4 evolvers) emits a `evolution_proposal` event carrying its predicted fixes + predicted regressions. |
| G2 | The next compaction round emits an `evolution_verdict` event scoring each proposal against observed deltas. |
| G3 | A failed verdict can auto-revert the edit at file-level granularity (no destructive history loss). |
| G4 | The full proposal/verdict chain is anchored on BSC via the existing `state_root` mechanism — third-party auditors can verify the agent's self-improvement claims without trusting the runtime. |
| G5 | Multi-evolver coordination prevents the non-additivity trap: if two evolvers both target the same task, only one writes per round (with a deterministic tiebreaker). |
| G6 | Conservative defaults treat regression predictions as untrustworthy (per AHE's empirical finding) — auto-rollback only on **observed** regressions, never on predicted-but-unobserved ones. |

### Non-goals

- Replacing `DriftScore`. The observational drift signal stays;
  proposal/verdict layer is *complementary*, not a replacement.
- Building a coding-agent benchmark inside Nexus. AHE's evaluation
  loop runs on Terminal-Bench 2; ours runs on real user
  conversations. Predictions are about *user-facing tasks*, not
  benchmark tasks.
- Cross-agent evolution sharing (one agent's verified fix
  propagating to other agents). That's social-protocol territory,
  out of scope for v0.2.
- Reinventing AHE's evidence corpus design. We adapt the principle
  (progressive disclosure) but our existing `EventLogCompactor`
  already does the layered distillation; we just add the
  proposal/verdict layer on top.

---

## 2. Mapping AHE → Nexus

| AHE pillar | Nexus equivalent | What's missing |
| --- | --- | --- |
| **Component observability** — 7 file-level mount points, each isolated | 5 memory namespaces (Phase J) + `ExtendedToolRegistry` + `ContractSpec` | **Middleware** isn't a first-class component (lives implicitly in `twin.chat()`). Promote it. |
| **Experience observability** — raw trace → per-task report → batch summary → benchmark overview (progressive disclosure) | EventLog → curated memory → projection (single layer) | Add intermediate "per-batch report" + "per-task verdict report" layers. Already partially implemented via `MemoryEvolver`'s extraction. |
| **Decision observability** — every edit ships a manifest with predicted fixes + regressions; next round verifies | Evolvers write but **don't predict**, **don't verify**. ABC drift is observational only. | **The whole proposal/verdict contract.** This is the core add. |
| File-level git-style commits + rollback | Persona is versioned (Phase J §3.3); other namespaces aren't | Generalize versioning to all 5 namespaces. |
| Non-additivity → coordinated edits | EvolutionEngine schedules all 4 evolvers in fixed cadence | Add a coordinator that detects predicted-fix overlap and serializes conflicting edits. |

---

## 3. Specification

### 3.1 Component observability — middleware as a first-class component

**Today.** `twin.py` (1438 lines) contains:
- chat loop (the "model interaction" middleware)
- pre-check / post-check ABC enforcement (the "guard" middleware)
- attachment cap validation (the "input filter" middleware)
- retry on tool failure (the "fault recovery" middleware)
- rate limiting (the "throttle" middleware)

These are middleware concerns mixed into `chat()`. AHE's NexAU
puts each as a single file at a fixed mount point so the Evolve
agent can edit one without touching another.

**Proposed.** Lift middleware out of `twin.py` into a new
`packages/nexus/nexus/middleware/` package, one file per concern:

```
packages/nexus/nexus/middleware/
├── __init__.py            Pipeline assembly + run order
├── pre_check.py           ABC pre-conditions (existing logic)
├── post_check.py          ABC post-conditions (existing logic)
├── attachment_filter.py   Size + type caps
├── retry.py               Tool-call retry policy
├── rate_limit.py          Per-user / per-tool throttling
└── version_pin.py         Each file's version hash, manifest entry
```

Each middleware is callable as:

```python
class Middleware(Protocol):
    name: str           # stable identifier
    version: int        # bumped on each evolver edit
    async def before(self, ctx: TurnContext) -> Optional[TurnContext]: ...
    async def after(self, ctx: TurnContext, response: str) -> Optional[str]: ...
```

The pipeline in `__init__.py` is a list of middleware instances; the
list itself is editable by the EvolutionEngine. Adding middleware
becomes a one-file commit.

**Manifest entry** (Phase O addition to `manifest.curated_memory`):

```json
"middleware": {
  "root": "0x...",
  "pipeline": [
    {"name": "pre_check",         "version": 3, "hash": "0x..."},
    {"name": "attachment_filter", "version": 1, "hash": "0x..."},
    {"name": "retry",             "version": 5, "hash": "0x..."},
    {"name": "post_check",        "version": 3, "hash": "0x..."}
  ]
}
```

Greenfield path: `nexus-agent-{tokenId}/middleware/{name}/v{N}.py`
(stored as text, hash-pinned). The middleware *code itself* is
on-chain-anchored — bringing harness evolution under the same
verifiability umbrella as memory evolution.

### 3.2 Experience observability — layered evidence corpus

AHE distills ~10M raw trace tokens → ~10K evidence tokens through
three layers:

```
raw trace          (everything, 10M tokens)
  ↓ Agent Debugger (per-task analysis)
per-task report    (one report per task, ~10K tokens)
  ↓ aggregation
benchmark overview (one document for the whole iteration)
```

The Evolve Agent reads the overview first, then drills down on
specific reports if needed. Progressive disclosure saves tokens and
gives the LLM better signal-to-noise.

**For Nexus** (where "tasks" are user-facing intents, not benchmark
tasks), the analogous layering:

```
EventLog                      (raw events, append-only — exists)
  ↓ EventLogCompactor (existing — runs on threshold)
per-batch report              (NEW — emitted as a memory_compact event)
  ↓ aggregation
session-level summary         (NEW — for session boundaries)
  ↓ aggregation
agent-level state snapshot    (existing — manifest.curated_memory.{*})
```

The new layers are emitted as `memory_compact` events with a
`metadata.layer` discriminator:

```json
{
  "event_type": "memory_compact",
  "metadata": {
    "layer": "batch_report",        // or "session_summary"
    "covers_events": [4501, 4600],
    "summary": "User asked about Tokyo restaurants; provided Sushi Saito recommendation; user happy",
    "extracted_facts": ["facts/uuid-1"],
    "skill_invocations": ["skills/travel_planning"],
    "ux_signal": "positive"          // for verdict scoring (§3.3)
  }
}
```

The Evolve Agent's prompt template gets the *most recent batch
reports + session summaries* as primary input, with raw events as
drill-down. This compresses the evolver's working memory by ~100x
without losing actionable signal.

### 3.3 Decision observability — the proposal/verdict contract

**This is the heart of Phase O.** Two new event types in
`nexus.sync.batch.v2`:

#### 3.3.1 `evolution_proposal`

Emitted by an evolver immediately *before* it writes to a curated
memory namespace. The write goes through only after the proposal
is appended to the EventLog (so the proposal is always part of the
hash chain, even if the runtime crashes mid-edit).

```json
{
  "event_type": "evolution_proposal",
  "client_created_at": "2026-04-28T12:34:56Z",
  "metadata": {
    "edit_id": "evo-2026-04-28-001-abc",
    "evolver": "MemoryEvolver",                  // which evolver
    "target_namespace": "memory.facts",          // what's being edited
    "target_version_pre": "v0041",               // version before edit
    "target_version_post": "v0042",              // version after edit (will exist after the write)

    "evidence_event_ids": [123, 145, 167, 198],  // events that triggered the edit
    "evidence_summary": "User mentioned 'allergic to peanuts' 3x in 2 sessions",
    "inferred_root_cause": "I missed a critical safety fact in earlier replies",

    "change_summary": "Added fact: 'user has peanut allergy'; importance=5",
    "change_diff": [
      {"op": "add", "key": "fact-uuid-789", "value": {...}}
    ],

    "predicted_fixes": [
      {"task_kind": "restaurant_recommendation", "reason": "will avoid peanut-containing dishes"},
      {"task_kind": "recipe_search", "reason": "will filter peanut allergens"}
    ],
    "predicted_regressions": [
      {"task_kind": "general_chat", "reason": "may over-mention allergy unnecessarily", "severity": "low"}
    ],

    "rollback_pointer": "memory/facts/v0041",     // what to restore if verdict fails
    "expires_after_events": 200                    // verdict deadline (see §3.3.4)
  }
}
```

#### 3.3.2 `evolution_verdict`

Emitted by the EvolutionEngine on the next compaction (or when the
proposal's `expires_after_events` fires, whichever comes first).
Compares the proposal's predictions against observed user-task
deltas in the intervening events.

```json
{
  "event_type": "evolution_verdict",
  "client_created_at": "2026-04-28T18:00:00Z",
  "metadata": {
    "edit_id": "evo-2026-04-28-001-abc",
    "verdict_at_event": 4837,
    "events_observed": 200,

    "predicted_fix_match": [
      {"task_kind": "restaurant_recommendation", "observed_count": 2, "outcome": "fixed"}
    ],
    "predicted_fix_miss": [
      {"task_kind": "recipe_search", "observed_count": 0, "outcome": "no_signal"}
    ],
    "predicted_regression_match": [],            // empty = no predicted regression observed
    "predicted_regression_miss": [],
    "unpredicted_regressions": [                 // observed BUT not predicted (the AHE blind spot)
      {"task_kind": "small_talk", "observed_count": 5, "severity": "medium",
       "evidence": "user said 'why do you keep mentioning food allergies?'"}
    ],

    "fix_score": 0.5,                            // hits / (hits + miss)
    "regression_score": 0.0,                     // weighted by severity
    "decision": "kept_with_warning"              // kept | reverted | kept | kept_with_warning
  }
}
```

**Decision semantics:**

| `unpredicted_regressions` | Action |
| --- | --- |
| empty | `kept` — full pass |
| non-empty, severity ≤ low | `kept_with_warning` — emit ABC drift signal but don't revert |
| non-empty, severity ≥ medium | `reverted` — set `_current.json` pointer back to `target_version_pre` and emit a `evolution_revert` event |

**Crucially**, AHE's empirical finding ("regression prediction is
random") is built into this design: we **only revert on observed
regressions**, never on *unobserved* predicted ones. A
`predicted_regressions: [{"task_kind": "X"}]` that doesn't show up
in the observation window is treated as a false alarm — the edit is
kept.

#### 3.3.3 `evolution_revert`

Emitted whenever a verdict triggers a rollback:

```json
{
  "event_type": "evolution_revert",
  "metadata": {
    "edit_id": "evo-2026-04-28-001-abc",
    "rolled_back_to": "memory/facts/v0041",
    "rolled_back_from": "memory/facts/v0042",
    "trigger": "unpredicted_regression",
    "evidence": "..."
  }
}
```

The actual storage rollback is just changing
`memory/facts/_current.json` to point at the older version. Both
versions remain on Greenfield — we never destroy history.

#### 3.3.4 Verdict deadlines

`expires_after_events` exists because some edits target rare task
kinds. If we waited for *evidence* of `recipe_search` (which the
user might do once a month), the verdict could be pending forever.

Default verdict deadlines per evolver type (configurable per agent):

| Evolver | Default `expires_after_events` |
| --- | --- |
| `MemoryEvolver` | 100 events (~1 day's chat) |
| `SkillEvolver` | 500 events (~1 week) |
| `PersonaEvolver` | 1000 events (~1 month) — persona effects are slow |
| `KnowledgeCompiler` | 200 events |

When the deadline fires without enough observation signal, verdict is
`kept` by default (innocent until proven guilty).

### 3.4 Coordinator — non-additivity mitigation

AHE found components interact non-additively: stacking edits caps
aggregate gain. For Nexus this surfaces when multiple evolvers run
the same round and their proposals overlap.

**Coordinator placement.** New module
`packages/nexus/nexus/evolution/coordinator.py` sitting between
`EvolutionEngine` and the individual evolvers.

**Logic per round:**

```python
async def coordinate_round(self, evolvers: list[Evolver]) -> list[Edit]:
    proposals = await asyncio.gather(*(e.propose() for e in evolvers))

    # 1. Detect overlap on predicted_fixes (same task_kind across proposals)
    fix_conflicts = _detect_fix_conflicts(proposals)

    # 2. Tiebreaker: highest-confidence single edit wins; others deferred to next round
    accepted = []
    deferred = []
    for prop in proposals:
        if prop.edit_id in {p.edit_id for p in deferred}:
            continue
        if prop.predicted_fixes_overlap(accepted):
            # Defer; competing claim already accepted this round
            deferred.append(prop)
            continue
        accepted.append(prop)

    # 3. Emit each as evolution_proposal event, then apply edit
    for prop in accepted:
        await self._emit(prop)
        await prop.evolver.commit(prop)

    # 4. Deferred proposals re-run next round with fresh evidence
    return accepted
```

This loses some throughput (one edit per overlapping round instead
of multiple) — but **it makes the verdict signal cleaner**: each
verdict can be unambiguously attributed to one edit, not to "edit A
+ edit B simultaneously."

**Per-evolver budget.** Even without overlap, we cap the round to
**1 evolver writing per compaction** as default. The remaining
evolvers can *propose* and have their proposals queued (not yet
emitted as events); next round, the next-priority evolver gets to
write. This converts "all 4 evolvers fire every round" (current) to
"round-robin + priority", which the AHE paper implicitly suggests
by running its loop in iterations of single-edit batches.

Priority tiebreaker (default):
1. **`MemoryEvolver`** — facts are foundational, missing facts propagate errors.
2. **`SkillEvolver`** — strategy updates are next-most-actionable.
3. **`KnowledgeCompiler`** — slower-moving, ok to wait.
4. **`PersonaEvolver`** — riskiest (per AHE, prose-level changes
   were *negative* −2.3pp); least frequent rights.

User-configurable via `TwinConfig.evolver_priority`.

### 3.5 ABC integration

`ContractEngine` stays as-is for its current job (hard rules + soft
rules + observational `DriftScore`). Phase O adds two read-paths:

1. **Pre-edit consultation.** The coordinator asks ABC: *"would
   applying this proposal violate any hard rule?"* If yes, reject
   the proposal outright before emission. (This is conservative —
   it doesn't depend on AHE's "regression prediction is random"
   finding.)
2. **Verdict scoring.** When evaluating verdicts, the engine reads
   ABC drift over the observation window. A spike in drift between
   `target_version_post` and the verdict point is an
   `unpredicted_regression` signal even if no specific task_kind
   triggered.

```python
def evaluate_verdict(proposal: Proposal, events: list[Event], abc: ContractEngine) -> Verdict:
    drift_at_post = abc.drift_at(proposal.target_version_post)
    drift_at_now = abc.drift_now()
    drift_delta = drift_at_now - drift_at_post

    verdict = build_verdict_from_observed_tasks(...)
    if drift_delta > abc.warning_threshold:
        verdict.unpredicted_regressions.append(
            {"task_kind": "abc_drift",
             "severity": "medium" if drift_delta > abc.intervention_threshold else "low",
             "evidence": f"DriftScore {drift_at_post:.2f} → {drift_at_now:.2f}"}
        )
    return verdict
```

This makes ABC's observational signal *actionable* at the edit
level instead of the agent level — a drift spike now points at a
specific edit, not just "the agent is off lately."

### 3.7 User-in-the-loop — manual approve / revert

**Why.** Per AHE's empirical finding (regression-prediction is
random), the verdict scorer will inevitably mislabel some edits.
Two failure modes:

- **False kept**: an edit is labelled `kept` but the user
  experiences it as regression (the verdict scorer missed signal).
- **False reverted**: an edit is auto-reverted but the user found
  it useful (the verdict scorer over-reacted to a noisy signal).

The user is the ground truth for both. Phase O.6 surfaces every
verdict in the UI with two manual override actions:

| Action | Effect |
| --- | --- |
| **Approve** | If verdict was `kept_with_warning`, downgrades to clean `kept`. Emits `evolution_user_approve` event. Tells the verdict scorer "you were too conservative — your warning didn't match my experience." Used as training signal for future verdict-scorer prompt tuning. |
| **Revert** | Forces rollback regardless of verdict (works on `kept` AND `kept_with_warning`). Emits `evolution_user_revert` event. Treated as a strong negative signal — also flagged to the proposing evolver to back off similar edits. |

**Two new event types:**

```json
{
  "event_type": "evolution_user_approve",
  "metadata": {
    "edit_id": "evo-2026-04-28-001-abc",
    "user_id": "...",
    "verdict_was": "kept_with_warning",
    "user_note": "actually I appreciated the allergy reminder"
  }
}

{
  "event_type": "evolution_user_revert",
  "metadata": {
    "edit_id": "evo-2026-04-28-001-abc",
    "user_id": "...",
    "verdict_was": "kept",
    "user_note": "agent became too cautious",
    "rolled_back_to": "memory/facts/v0041"
  }
}
```

Both events go through the same EventLog → manifest →
`state_root` path as everything else. The chain anchor includes
*the user's manual feedback as a first-class signal*, audit-able
the same way verdicts are.

**UI surface (Phase O.6):**

```
┌─ Evolution timeline ──────────────────────────────────────────┐
│                                                                │
│  ✓ 2 days ago — MemoryEvolver added "user has peanut allergy"  │
│    Verdict: kept   (fix-match: 1, regression-match: 0)         │
│    [✓ Approve]  [↺ Revert]                                     │
│                                                                │
│  ⚠ 1 day ago — PersonaEvolver shifted tone toward formal       │
│    Verdict: kept_with_warning                                   │
│      Unpredicted regression: small_talk became stilted          │
│    [✓ Approve verdict]  [↺ Revert edit]                         │
│                                                                │
│  ↺ 6 hours ago — SkillEvolver added "always run pytest first"   │
│    Verdict: reverted (auto)                                     │
│    Reason: ABC drift +0.31 (intervention threshold)             │
│    Restored: skills/code_review/v0014.json                     │
│                                                                │
│  ✓ 1 hour ago — KnowledgeCompiler grouped 12 facts as           │
│    "user travel preferences"                                    │
│    Pending verdict (deadline in ~3 days)                       │
│    [✓ Pre-approve]  [↺ Pre-emptive revert]                     │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

Pre-approval / pre-emptive revert apply to pending verdicts
(before the deadline fires) — useful when the user has strong
prior knowledge about a specific edit ("I know this is bad
without waiting 3 days for the engine to figure it out").

### 3.8 BEP-Nexus v0.2 schema additions

Add to `manifest.curated_memory`:

```json
"middleware": {
  "root": "0x...",
  "pipeline": [...]                      // see §3.1
},
"evolution_log": {
  "root": "0x...",                       // hash of recent proposals/verdicts
  "pending_verdicts": ["evo-2026-...001-abc", "evo-2026-...001-xyz"],
  "last_verdict_seq": 4837,
  "stats": {
    "fix_match_rate": 0.34,              // running average — should be ~5x random
    "regression_match_rate": 0.12,        // running average — expected ~random
    "revert_rate": 0.08                   // % of edits that got rolled back
  }
}
```

The `evolution_log.root` is a Merkle root (keccak256) over the
last N proposal/verdict pairs, providing O(log N) verifiability for
the "did the agent actually make this edit and check it?" query.

Add to event types table (BEP §3.1):

| event_type | Purpose |
| --- | --- |
| `evolution_proposal` | Pre-write declaration of intent + predictions |
| `evolution_verdict` | Post-window evaluation of predictions |
| `evolution_revert` | Storage pointer rollback when verdict fails |

---

## 4. Specific empirical findings → design constraints

The AHE paper's negative findings translate into specific defaults:

### 4.1 Persona evolution is suspicious until proven otherwise

> *"swapping in AHE's tools, middleware, or long-term memory alone
> yields +3.3, +2.2, and +5.6 pp, while the system prompt alone is
> −2.3 pp"*

Implication for Nexus: **`PersonaEvolver` should write rarely and
have the highest verdict bar.** Default settings:

```python
# packages/nexus/nexus/config.py
class TwinConfig:
    # ... existing fields ...

    # Phase O — falsifiable evolution
    persona_evolution_min_interval_days: int = 30          # was effectively weekly
    persona_evolution_min_evidence_events: int = 500       # require strong signal
    persona_evolution_revert_severity_threshold: str = "low"  # rollback even on mild regression
```

Other evolvers keep their existing thresholds. PersonaEvolver gets a
much higher bar because the paper shows prose-level edits are the
risky ones.

### 4.2 Multi-evolver budget is finite

> *"The three positive single-component gains sum to +11.1 pp
> against full AHE's +7.3 pp ... stacking them spends turns on
> redundant re-checks within the long-horizon budget."*

Coordinator's default "1 evolver writes per round" is a direct
response. Configurable upward but defaults conservative.

### 4.3 Don't trust regression predictions

> *"regression-prediction precision is 11.8% vs random baseline 5.6%"*

Verdict logic *only reverts on observed regressions, never
unobserved-but-predicted ones*. Predicted regressions become a
"hint" to the verdict scorer (look harder for these task kinds in
the observation window), not a veto.

### 4.4 Operating point coupling

> *"AHE's step budget and per-task timeout were fitted to GPT-5.4
> high during evolution"*

We have the same risk: our compaction thresholds (1000 events,
1 MB tail), retry budgets, projection token caps all implicitly
fit to Gemini 2.5 Flash. **Ship Phase O with these as
config-exposed**, document the model-coupling explicitly:

```env
# Tuned for Gemini 2.5 Flash. Likely needs adjustment for:
#   - Claude (lower retry budget, higher single-call cost)
#   - GPT-5 (higher reasoning, longer context — relax compaction)
NEXUS_COMPACTION_EVENT_THRESHOLD=1000
NEXUS_COMPACTION_BYTE_THRESHOLD=1048576
NEXUS_PROJECTION_TOKEN_BUDGET=8000
NEXUS_TOOL_RETRY_MAX=3
NEXUS_VERDICT_DEADLINE_MEMORY=100
NEXUS_VERDICT_DEADLINE_SKILL=500
NEXUS_VERDICT_DEADLINE_PERSONA=1000
NEXUS_VERDICT_DEADLINE_KNOWLEDGE=200
```

---

## 5. Open questions / risks

### Q1. How does the verdict scorer detect "task_kind"? (DECIDED — option a)

The proposal declares `predicted_fixes: [{"task_kind":
"restaurant_recommendation"}]`. The verdict needs to count how
often that task kind appeared in the observation window.

**Decision: LLM-classified per-event task_kind**, derived inside
`EventLogCompactor`. Costs one LLM call per compaction batch
(~1000 events) — bounded and amortised. Each user-facing event
(`user_message`, `tool_call`, `assistant_response`) gets a
classifier-assigned `task_kind` field stored in event metadata.

The classifier prompt outputs JSON like:

```json
{"sync_id": 4521, "task_kind": "restaurant_recommendation", "confidence": 0.87}
```

Implementation lives at `nexus_core.classifier` (new module). The
`task_kind` taxonomy is **emergent** — not a fixed enum — so the
classifier is free to invent new categories. Two cleanup steps:

1. **Cluster step** (every N batches): k-means / agglomerative
   clustering over recent task_kind embeddings, merging
   near-duplicates ("food_recommendation" + "restaurant_recommendation"
   → one canonical label).
2. **Drift cap**: refuse new task_kinds that overlap >0.85 cosine
   with an existing one in the registry.

Cost: ~1 LLM call per 1000 events, negligible against compactor's
existing cost. Alternatives (embedding clustering, hand-curated enum)
were rejected — the first is fuzzier than necessary, the second
brittle.

### Q2. What if the user's behavior just doesn't reveal whether the edit helped?

If `MemoryEvolver` adds "user has peanut allergy" but the user
never asks about food again in the verdict window, verdict is
`kept` by deadline expiry. This is the right default — we can't
penalize edits for not getting tested.

But: long-tail facts could accumulate without verification. Should
they expire? Suggest **no auto-expiry of facts**; the next time the
fact *is* used (in a tool call argument or a chat response), emit a
deferred verdict that scores belatedly.

### Q3. Adversarial edits

A malicious or compromised runtime could write `evolution_proposal`
events that lie about predictions, then write `evolution_verdict`
events that lie about observations. The chain anchor only protects
*ordering* and *immutability after-the-fact*; it doesn't protect
honesty.

Defenses:
- **Mandatory sampling.** Some % of verdicts (default 10%) get
  re-computed by an external auditor process from the raw events,
  and the verdict event must include an
  `external_audit_seq: number` if claimed.
- **ABC drift cross-check.** §3.5 already inserts ABC drift into
  the verdict; an evolver can't fake a drift signal because that
  comes from the contract engine, not the evolver.
- **Multi-runtime verifiers** (Phase v0.2 multi-writer authority).
  An auditor with read grant can re-evaluate any verdict and post
  a counter-claim event.

### Q4. Storage growth from versioned namespaces

Per BEP v0.2 design doc §3.3, persona is versioned. Phase O extends
versioning to all 5 namespaces. At ~5 KB per memory entry, ~10
edits per day, that's ~50 KB/day of new-version overhead per agent
on Greenfield. After 1 year: ~18 MB.

Mitigation: `retention_policy.evolution_log_keep_verdicts: N`
default 1000 (about 3 months at typical usage). After N, old
proposal/verdict pairs are GC-able from Greenfield (the hash chain
still works because manifests reference Merkle root, not raw
events).

### Q5. UX surface

The desktop sidebar today shows "Memory" as a flat list. Phase O
needs to surface:
- Pending proposals (edits that haven't been verdict-scored yet)
- Recent verdicts (kept / reverted / warning)
- Drift trend (per-evolver fix rate over time)

Sketch: new "Evolution" tab in the sidebar with a timeline view of
the last 50 edits + their verdicts, color-coded by outcome. User
can manually revert a "kept_with_warning" edit if they disagree
with the verdict.

---

## 6. Phased rollout

### Phase O.1 — Schema + storage (3 days)

- Add `evolution_proposal` / `evolution_verdict` / `evolution_revert`
  to `nexus.sync.batch.v2` event_type table.
- Add `manifest.evolution_log` block.
- Generalize persona-style versioning to all 5 namespaces (folds
  in some Phase J work).
- Test vectors for the new event types.

### Phase O.2 — Evolver instrumentation (1 week)

- Refactor each evolver to emit `evolution_proposal` before write,
  carrying `evidence_event_ids`, `change_diff`,
  `predicted_fixes`, `predicted_regressions`.
- Coordinator stub: serialize proposals through one queue, apply
  the round-robin priority.
- Verdict engine stub: deadline-fires `kept` by default; full
  scoring lands in O.4.

### Phase O.3 — Middleware as first-class component (4 days)

- Lift `pre_check`, `post_check`, `attachment_filter`, `retry`,
  `rate_limit` from `twin.py` into
  `packages/nexus/nexus/middleware/`.
- Manifest `curated_memory.middleware` block.
- Pipeline assembly + version pinning.

### Phase O.4 — Verdict scorer (1 week)

- LLM-classified task_kind per event (Q1 option a).
- Verdict event scoring logic.
- ABC drift cross-check integration (§3.5).
- Auto-rollback on observed regression.

### Phase O.5 — Coordinator (3 days)

- Predicted-fix overlap detection.
- Round-robin priority + tiebreaker.
- Deferred-proposal queue.

### Phase O.6 — UI surface (1 week)

- "Evolution" tab in desktop sidebar.
- Per-edit drilldown view.
- Manual revert button.

### Phase O.7 — External audit hooks (3 days)

- Verdict sampling for Q3 defense.
- Audit grant tier in BEP v0.2 multi-writer authority.

**Total estimate: ~5 weeks** for full Phase O. Phases O.1–O.4 are
the minimum viable skeleton (~3 weeks); O.5–O.7 are quality bars
that can ship serially.

---

## 7. Success metrics

Measurable against current main branch after Phase O lands:

| Metric | Today | Target after O |
| --- | --- | --- |
| Fraction of edits that ship a `evolution_proposal` event | 0% | 100% |
| Fraction of edits with a verdict within deadline | n/a | ≥ 90% |
| Fix-match rate (running avg) | n/a | ≥ 25% (5x of typical 5% random baseline; AHE got 33.7%) |
| Auto-revert rate | n/a | 5–15% (too low → rollback too lenient; too high → evolvers too eager) |
| User-initiated manual reverts (UI) | n/a | < 5% of verdicts |
| Persona evolution frequency | weekly | ≥ 30 days |
| Cross-evolver redundant-write rate (proposals deferred by coordinator) | n/a | tracked, no target — observability metric |

A 6-month review post-O ship: do agents trained under Phase O show
better long-horizon user satisfaction (proxied by retention,
session length, manual-revert rate) than agents under the
pre-Phase-O loop? If not, the loop is theatre and we should fold
it back.

---

## 8. References

- **AHE paper.** Lin, Liu, Pan et al. *Agentic Harness Engineering:
  Observability-Driven Automatic Evolution of Coding-Agent
  Harnesses.* arXiv:2604.25850v3, Apr 2026.
  Code: https://github.com/china-qijizhifeng/agentic-harness-engineering
- BEP-Nexus v0.1: [`../BEP-nexus.md`](../BEP-nexus.md)
- BEP-Nexus v0.2 design: [`bep-v0.2.md`](bep-v0.2.md)
- ABC concept primer: [`../concepts/abc.md`](../concepts/abc.md)
- DPM concept primer: [`../concepts/dpm.md`](../concepts/dpm.md)
- Existing evolution code: `packages/nexus/nexus/evolution/`
- ROADMAP: [`../../ROADMAP.md`](../../ROADMAP.md)

---

## Appendix A — what AHE got that we don't (yet)

A short list of AHE features we've consciously deferred or
declined, with rationale:

| AHE feature | Status in Nexus | Why |
| --- | --- | --- |
| Three role agents (Code / Debugger / Evolve) sharing a base model | Single model handles all roles | We don't have the throughput to run three role-specialized prompts per turn. Could revisit if the verdict scorer needs more horsepower. |
| 7-component decoupled mount points | We have 5 + middleware (post-O.3) | "sub-agent" we don't yet support; "skill / tool description / tool implementation" we collapse into ExtendedToolRegistry. |
| GitHub commit per edit | Per-namespace versioning in Greenfield | Same semantic, different storage. |
| Benchmark-driven evolution loop (Terminal-Bench 2) | Real-user-driven evolution | We can't pre-benchmark "did the user feel better" — verdict signals are necessarily noisier. |
| Evolve agent reads layered evidence corpus + drills down | EvolutionEngine reads recent compacts + raw events | Adding the drill-down layer is Phase O.2 work. |
| Revert on prediction failure | Phase O.4 with conservative defaults | Direct port. |

## Appendix B — proposal/verdict pseudocode

For implementers. Goes in `packages/nexus/nexus/evolution/loop.py`:

```python
async def evolution_loop_round(twin: DigitalTwin, batch_events: list[Event]):
    coord = twin.evolution_coordinator
    abc = twin.contract

    # 1. Each evolver proposes
    raw_proposals: list[Proposal] = []
    for evolver in twin.evolvers:
        prop = await evolver.propose(batch_events, twin.curated)
        if prop is None:
            continue
        # ABC pre-check: would this edit violate hard rules?
        if abc.would_violate_hard_rules(prop):
            twin.event_log.append(_proposal_rejected(prop, reason="hard_rule"))
            continue
        raw_proposals.append(prop)

    # 2. Coordinator selects up to one per round (configurable)
    selected = coord.select(raw_proposals)

    # 3. Emit proposal events + apply edits
    for prop in selected:
        twin.event_log.append(_proposal_event(prop))
        await prop.evolver.commit(prop)

    # 4. Score any pending verdicts whose observation windows have closed
    for pending in coord.pending_verdicts():
        if not pending.window_complete(twin.event_log.tail()):
            continue
        verdict = score_verdict(pending, twin.event_log.window(pending), abc)
        twin.event_log.append(_verdict_event(verdict))
        if verdict.decision == "reverted":
            await pending.evolver.revert(pending.rollback_pointer)
            twin.event_log.append(_revert_event(pending, verdict))

    # 5. Trigger compaction (existing flow) which now also captures
    #    these new event types into the manifest's evolution_log block.
    if twin.compactor.should_compact():
        await twin.compactor.compact()
```

---

*End of Phase O design doc. Implementation tracking issue: TBD.
PR target: ROADMAP after Phase L.*
