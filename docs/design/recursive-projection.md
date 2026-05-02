# Recursive Projection — RLM-style chat context for Nexus

> Replace the current single-call ``π(events, task, budget)``
> projection with a Recursive Language Model (RLM): load the
> EventLog as a REPL variable, let the root LLM write code to
> slice / sub-call / stitch. Inspired by Zhang, Kraska & Khattab,
> *Recursive Language Models* (arXiv:2512.24601, Dec 2025), which
> proved this pattern handles inputs ~2 orders of magnitude beyond
> the base model's context window at the same or lower cost per
> query.
>
> **Status:** Draft. **Owner:** huihzhao.
> **Inputs:** RLM paper + BEP-Nexus v0.2 + Phase O design.
> **Output:** ROADMAP **Phase P — Recursive Projection**, plus
> usage in Phase O.4 verdict scorer + chat-projection refactor.
>
> The primitive itself (``nexus_core.rlm``) is **already shipped**
> in this repo — see ``packages/sdk/nexus_core/rlm.py`` + 22
> conformance tests in ``packages/sdk/tests/test_rlm.py``. This
> doc is about *how Nexus runtimes will use it*.

---

## 0. The shift

Today's projection does **compile-time distillation**:

```
EventLog ─── compactor (1 LLM call) ──→ curated memory ───→ context ──→ chat LLM
```

Every chat turn pays the compactor's "shrink the EventLog into
something that fits the context window" tax, and the LLM only ever
sees the compactor's summary — never the raw events. That's lossy
by construction. A user asking "what restaurant did I tell you
about three weeks ago?" must hope the compactor preserved that
fact; if not, the answer is "I don't remember" even though the
event is right there in the log.

RLM does **runtime navigation**:

```
EventLog ────────────────────────────→ REPL var
                                          │
                                          ▼
                                  RLMRunner(root_llm, sub_llm)
                                          │
        ┌─── code: regex / slice ─────────┤
        │                                 │
        ├─── code: await _sub_llm(...) ───┤
        │                                 │
        └─── code: _set_result(...) ──────┴───→ context ──→ chat LLM
```

The root LLM (smart, expensive, e.g. GPT-5 / Gemini 2.5 Pro) writes
Python code to peek at the EventLog directly. When it needs LLM
work over a snippet (summarise this paragraph, classify this
event, extract this fact) it calls the cheaper sub-LLM. Output
sizes are bounded by what the root LLM commits via
``_set_result``.

**The two paths are not interchangeable.** Compile-time
distillation produces a deterministic hash that we anchor on chain.
RLM trajectories are stochastic and don't hash consistently. So:

- **Chain anchor path** keeps using the BEP-Nexus v0.2 chunked
  manifest + Merkle root pipeline. Deterministic.
- **Chat projection path** switches to RLM. Stochastic, higher
  quality.

This split is the load-bearing decision of Phase P.

---

## 1. Goals + non-goals

### Goals

| G | Statement |
| --- | --- |
| G1 | Chat projection handles arbitrarily long EventLogs (no "fits in N tokens" hard cap). |
| G2 | Quality on dense queries beats current single-call projection (per the RLM paper's OOLONG-Pairs result, room for big wins). |
| G3 | Median per-turn cost stays comparable to current projection cost; long-tail spikes capped by ``max_iterations`` + ``max_sub_calls``. |
| G4 | RLM is reusable across Nexus consumers — Phase O.4 verdict scorer, attachment processor, knowledge search — not just chat. |
| G5 | DPM determinism holds for the chain-anchor path (compile-time chunked manifest unchanged). |
| G6 | RLM trajectory is auditable — every code block + sub-LM I/O pair captured in ``RLMResult.trajectory`` for debugging / future deterministic-replay. |

### Non-goals

- Securing the RLM sandbox against a hostile root LLM. The runner
  uses ``exec()`` against a controlled globals dict; for trusted
  twin-projection use this is fine. Production deployments
  exposing RLM to untrusted code (user plugins) would need
  RestrictedPython / WASM / Docker — not in this design.
- Cross-language / cross-runtime trajectory replay. We capture
  trajectories but the replay protocol (deterministic re-running
  on a different runtime given the same trajectory) is deferred —
  the chain-anchor path covers verifiability for now.
- Training a model to be a better RLM root. The paper used
  off-the-shelf GPT-5 / Qwen3-Coder; we'll do the same. RL fine-
  tuning the root LLM is a future project.
- Recursion depth > 1. Paper measured no benefit yet. Our
  ``RLMConfig.max_recursion_depth = 1`` enforces it.

---

## 2. Use cases (concrete)

### 2.1 Chat projection (the headline)

**Today.** ``DigitalTwin.chat()`` calls
``EvolutionEngine.project(events, task, budget)`` which dispatches
to one LLM call:

```python
context = await llm.complete(
    f"Given these events: {events}, "
    f"summarise what's relevant to: {task}",
    max_tokens=budget,
)
```

**Phase P.** Replace with RLM:

```python
from nexus_core import RLMRunner, RLMConfig

runner = RLMRunner(
    root_llm=llm.chat,        # smart model
    sub_llm=cheap_llm.chat,   # cheaper model for sub-calls
    config=RLMConfig(
        max_iterations=8,
        max_sub_calls=15,
        timeout_seconds=30.0,
        target_output_tokens=budget,
    ),
)

result = await runner.run(
    task=(
        "Build the most relevant context for the user's current "
        f"message: {user_msg!r}. Output exactly the context string "
        "you want passed to the chat LLM."
    ),
    context_vars={
        "events": events_as_list,
        "curated_memory": twin.curated.snapshot(),
        "persona": twin.persona.current(),
    },
)
context = result.output
```

**Why it's better.**

- User asks "what was that book my friend recommended last
  March?" — root LLM regex-searches events for "book" + dates,
  finds 3 mentions, sub-LM extracts which one, returns the
  answer. Today's compactor would have lossy-summarised this away.
- User uploads a 50-page PDF and asks about a specific section —
  root LLM slices the PDF buffer to that section only, no
  pre-distillation needed.
- User has 6 months of chat history — RLM doesn't read it all,
  only the slices it actually needs. **The compactor's "fits in
  context window" tax disappears.**

### 2.2 Phase O.4 verdict scorer

When the verdict scorer evaluates whether an
``evolution_proposal``'s predicted fixes / regressions actually
manifested, it reads the observation window (default 200 events;
configurable up to 1000+). At 1000 events, the window may itself
exceed context. RLM:

```python
runner = RLMRunner(root_llm=verdict_lm, sub_llm=cheap_lm)
verdict = await runner.run(
    task=(
        f"Evaluate edit {edit_id}. Predicted fixes: {predicted_fixes}. "
        f"Look through the events and tell me which predictions "
        f"were observed and whether any unpredicted regressions occurred. "
        f"Return JSON with the verdict schema fields."
    ),
    context_vars={"events": observation_window, "edit_metadata": meta},
)
verdict_json = json.loads(verdict.output)
```

The root LLM might:
1. ``[e for e in events if e["metadata"].get("task_kind") in predicted_fixes]``
2. Sub-LM-call each filtered cluster: "did this turn go well for the user?"
3. Aggregate into the verdict JSON.

**vs.** today's design (Phase O.4 spec): one giant LLM call over
all 200 distilled events. Works for 200 — falls over at 2000.

### 2.3 Attachment processing

Today: upload PDF → server runs distiller → 10K-token summary
stored in EventLog. The summary covers the *whole* PDF, but every
chat-turn that references the PDF only needs ~2 pages.

Phase P: upload PDF → store raw + content hash. When chat
references it, RLM root LLM slices to the relevant section:

```python
result = await runner.run(
    task=f"Answer: {user_question!r}",
    context_vars={"pdf_text": full_pdf, "user_question": user_msg},
)
```

Saves the upfront distillation cost; pays a smaller per-query cost.
For attachments that are never referenced, it's pure savings.

### 2.4 Knowledge / skill search

``KnowledgeCompiler`` and ``SkillManager`` currently pre-organize
content (tags, embeddings, indexes). With RLM, much less
pre-processing is needed — the root LLM searches at query time.

We won't rip out the existing infrastructure (it's still useful as
a fast-path for common queries); but new content can skip the
indexing step and rely on RLM at query time.

---

## 3. The DPM split

The trickiest design decision in Phase P: how to keep the chain
anchor pipeline deterministic while letting chat projection run a
stochastic RLM.

### 3.1 Two projection functions

```python
class DigitalTwin:
    # Used in chat — quality optimised, stochastic.
    async def project_for_chat(self, events, task) -> str:
        runner = RLMRunner(...)
        result = await runner.run(task=task, context_vars={"events": events})
        return result.output

    # Used by compactor for chain anchoring — deterministic.
    def project_for_anchor(self, events) -> ChunkedManifest:
        # Same as today: the compile-time chunked manifest builder.
        return self.compactor.build_manifest(events)
```

Two distinct call sites, two distinct return types, no shared
state. The chat path doesn't influence the anchor path.

### 3.2 What we lose

The current code claims a single "DPM projection" function shared
between chat and anchor. Splitting forces us to acknowledge:

- **Chat answers and chain anchors describe the same EventLog
  differently.** The chat answer encodes the agent's *current
  understanding*; the anchor encodes the agent's *audit-able
  state*. These can legitimately differ.
- **Replay across runtimes drops to chain-anchor only.** A
  different runtime can verify the agent's state-root by
  re-running the deterministic compactor; it CANNOT reproduce a
  past chat answer because the RLM trajectory is stochastic.

That's actually fine — chat replay was never the use case anyway.
What we need cross-runtime is "agent identity is preserved", which
state-root pinning gives us.

### 3.3 What we gain

A clean architectural split. The chain anchor pipeline becomes
*more* trustworthy because it's no longer entangled with chat
quality concerns — operators can audit it without worrying about
LLM-induced variance.

### 3.4 Optional: trajectory anchoring (Phase R, not Phase P)

If we ever want cross-runtime *chat* replay, the trajectory of
each RLM run can itself be hashed + anchored. The trajectory is
just JSON — code blocks + sub-LM I/O pairs + final output. Hashing
it and putting the hash in EventLog gives us "the same RLM run is
provably the same RLM run" without re-executing the LLM.

Caveats:
- Doesn't replay the agent's *thinking*, just its outputs.
- Storage cost: trajectory is verbose (every sub-LM I/O), often
  ~100KB per run. At 100 chat turns/day per agent, that's 10MB/day
  in trajectory storage alone. Probably only worth it for high-
  stakes operations (financial agents, medical agents).

Tracked as a Phase R nice-to-have, not committed to.

---

## 4. Implementation status

✅ ``packages/sdk/nexus_core/rlm.py`` — 350 lines:

- ``RLMRunner`` class with ``run(task, context_vars) -> RLMResult``
- Async-aware sandbox using AST rewrite + ``global`` declarations
  to make REPL-style variable persistence work across iterations
  (the trick that makes the paper's pattern (a) — ``computed = ...;
  next iter use computed`` — work in Python).
- ``_sub_llm`` and ``_set_result`` injected as globals; budget caps
  enforced.
- Code block extraction tolerant of fenced + unfenced LLM output.
- Trajectory recording: every iteration's raw response, code,
  stdout, stderr, error, sub-call count.

✅ ``packages/sdk/tests/test_rlm.py`` — 22 tests:

- Termination via ``_set_result``
- Sub-LM calls + budget enforcement (cap exceeded → exec error
  surfaces in trajectory, root LLM can recover)
- ``max_iterations`` truncation
- Stdout / stderr / SyntaxError / RuntimeError capture
- Three RLM trajectory patterns from the paper (regex filter,
  decompose + sub-LM, stitched long output)
- Globals persist across iterations
- ``run_rlm`` convenience wrapper
- Top-level ``nexus_core.{RLMRunner, RLMConfig, …}`` exposure

✅ Wired into ``nexus_core/__init__.py`` so all consumers can do
``from nexus_core import RLMRunner`` without reaching into a deep
submodule.

❌ Not yet integrated into:

- ``packages/nexus/nexus/evolution/projection.py`` — the chat path
  still uses the single-call LLM projection. Phase P.1 task.
- Phase O.4 verdict scorer — Phase P.2 task, but blocked by
  Phase O.1–O.3 first.
- Attachment processor — Phase P.3 task, deferrable.

---

## 5. Phased rollout

### Phase P.1 — Chat projection refactor (1 week)

- Add ``DigitalTwin.project_for_chat()`` using ``RLMRunner``.
- Keep the old ``project()`` method for the anchor path; rename
  to ``project_for_anchor()`` (no behaviour change).
- Add a feature flag ``TwinConfig.chat_projection_mode ∈
  {"single_call", "rlm"}``; default ``"single_call"`` initially.
- Run side-by-side on dev: both projections compute, compare
  output quality on N test conversations.
- Once quality consistently wins (~2 weeks of dogfooding), flip
  default to ``"rlm"``.

### Phase P.2 — Verdict scorer uses RLM (3 days)

When Phase O.4 (verdict scorer) is implemented, build it directly
on ``RLMRunner`` rather than a single LLM call. This is the
"observation window may exceed context" case.

### Phase P.3 — Attachment-by-reference (1 week)

Remove the upfront distillation step. Attachments stored raw +
content hash; chat-time access via RLM. Falls back to the existing
distiller if a runtime doesn't have RLM enabled (compat mode).

Cost analysis before committing: if average attachment is
referenced >5x per upload, distill-once is cheaper than RLM-each-
time. Need usage data first.

### Phase P.4 — Operator monitoring (3 days)

Add metrics:
- ``nexus.rlm.iterations_per_run`` histogram
- ``nexus.rlm.sub_calls_per_run`` histogram
- ``nexus.rlm.truncated_total`` counter (truncated runs are user-
  visible quality regressions)
- ``nexus.rlm.crashed_total`` counter
- ``nexus.rlm.elapsed_seconds`` histogram

Alert on truncation rate > 5% / minute (something is going wrong
with our prompts or limits).

**Total Phase P estimate: ~3 weeks** of focused work, P.1 + P.2
are the minimum viable shipping unit.

---

## 6. Risks + mitigations

### R1. Cost variance

**Risk.** Median RLM cost is fine, but the long tail (root LLM
loops 10x, makes 20 sub-calls) can be 5-10x median. A single
malicious or confused root LLM run could cost dollars.

**Mitigation.**
- Hard budget caps in ``RLMConfig`` (already shipped).
- Per-user / per-day cost ceiling at the server level. Once an
  agent burns >$X/day, all RLM calls degrade to ``"single_call"``
  mode for the rest of the day.
- Per-call timeout (default 60s) prevents runaway runs.

### R2. Quality regression on simple queries

**Risk.** Paper Observation 3 notes: *"RLM performance is slightly
worse on smaller input lengths, suggesting a tradeoff point"*. If
the user just asked "hi", we don't want to spin up an RLM with 10
iterations.

**Mitigation.**
- Fast-path: if ``len(events) < THRESHOLD`` (say, 50 events
  totaling < 16K tokens), skip RLM and go single-call. RLM only
  for the long-context regime.
- Configurable threshold per ``TwinConfig``.

### R3. Sandbox escape

**Risk.** A sufficiently clever root LLM could exec malicious
code, exfiltrate the wallet private key, etc.

**Mitigation.**
- Trust boundary: root LLM is OUR LLM, not the user's. We control
  the system prompt and the model choice. If we trust GPT-5 /
  Gemini 2.5 to not write malicious code, the sandbox doesn't need
  to be a security boundary.
- For untrusted-code use cases (user plugins, multi-tenant shared
  agents), swap to RestrictedPython or WASM. Out of scope for
  Phase P.

### R4. Determinism drift in chat answers

**Risk.** Same EventLog + same task → different chat answer on
different runs. Users notice "you said X yesterday, now you're
saying Y."

**Mitigation.**
- ``temperature=0`` for the root LLM in chat projection (already
  the default for projection LLM in ``TwinConfig``).
- Sub-LLM also at ``temperature=0``.
- Even with t=0, LLMs aren't 100% deterministic, but variance is
  small enough that this stays in normal "the agent re-phrased"
  territory rather than "the agent forgot what it said."

### R5. Sub-LLM hallucination at the slice level

**Risk.** Root LLM extracts a snippet; sub-LM hallucinates a fact
that wasn't in the snippet. Aggregated answer is wrong.

**Mitigation.**
- ABC contract: hard rules (e.g. "never claim a fact not in the
  EventLog") fire on the final chat output, not the sub-LM call.
  If the root LLM passes the hallucination through, ABC catches
  it.
- Phase O verdict scorer measures regression rate over
  observation window; persistent quality regression triggers
  rollback to single-call mode.

---

## 7. Open questions

### Q1. Should sub-LM be the same model as root, or always cheaper?

The paper used GPT-5-mini (sub) + GPT-5 (root). In our case:

- Root: Gemini 2.5 Pro (smart)
- Sub: Gemini 2.5 Flash (cheap)

But what about agents using only one provider key? If the user
only has Gemini 2.5 Flash configured, do we run RLM with both
levels at Flash? Or fall back to single-call mode?

Default for now: **same-model RLM is allowed but flagged as
sub-optimal in logs**. The cost benefit of sub-LM disappears, but
the reasoning structure (slice → sub-call → stitch) still helps.

### Q2. Recursion depth > 1?

Paper found depth=1 sufficient for OOLONG-class tasks. Some Nexus
use cases might benefit from deeper recursion (e.g. "summarise the
last year of conversations" → year-summary calls month-summaries
calls week-summaries...). Out of scope for Phase P; possible
future work if we hit a use case that demonstrably needs it.

### Q3. Caching root-LLM trajectories?

Same task + same context → likely same root-LLM code blocks. We
could cache trajectories keyed by ``(task_hash, events_hash)`` and
replay deterministically on cache hit (saves ALL LLM calls).

This would give us **partial determinism for chat** — if the user
asks the same question twice, the answer is identical.

But cache invalidation is tricky (every new event invalidates the
events_hash). Defer to post-Phase-P.

### Q4. How does this interact with desktop's chat replay?

Desktop today shows chat history by reading the server's
``/agent/messages`` endpoint, which reads the EventLog. **Chat
history isn't affected** — that's just stored events, not
re-projected. Good.

What changes: if the user clicks "regenerate" on a past assistant
message, the new RLM-projection might produce a different answer
than the original. That's already true today (LLM non-determinism)
— RLM just slightly increases the variance.

### Q5. Trajectory in EventLog?

Should every RLM run emit an event capturing its trajectory? Pros:
full audit trail; cons: ~10x EventLog growth from trajectory
verbosity.

Defer: emit a *summary* event with just iteration count + sub-call
count + truncated/crashed flags. Keep full trajectory in
``RLMResult`` returned to caller; if caller wants to persist, it
can.

---

## 8. References

- **RLM paper.** Zhang, A. L., Kraska, T. & Khattab, O.
  *Recursive Language Models.* arXiv:2512.24601v1, Dec 2025.
- ``packages/sdk/nexus_core/rlm.py`` — reference implementation.
- ``packages/sdk/tests/test_rlm.py`` — 22 conformance tests.
- BEP-Nexus v0.1: [`../BEP-nexus.md`](../BEP-nexus.md)
- BEP v0.2 design: [`bep-v0.2.md`](bep-v0.2.md)
- Falsifiable evolution design: [`falsifiable-evolution.md`](falsifiable-evolution.md)
- DPM concept: [`../concepts/dpm.md`](../concepts/dpm.md)

---

*End of Phase P design doc. The ``nexus_core.rlm`` primitive is
shipped; integration into ``DigitalTwin.chat`` is the first
concrete next step.*
