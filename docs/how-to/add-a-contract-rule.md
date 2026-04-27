# How-to: add a new behaviour rule

A "rule" is a constraint the agent must obey, defined in the agent's
contract YAML and enforced by `ContractEngine` on every chat turn.
This recipe walks through adding rules of each kind.

If you haven't read [`docs/concepts/abc.md`](../concepts/abc.md) yet,
do that first — you'll need to know the difference between **hard**,
**soft**, and **distributional** rules.

## The contract YAML

Every twin loads its rules from
`{base_dir}/contracts/system.yaml`. By default `base_dir` is
`~/.nexus_server/twins/{user_id}/` so each user's twin has its own
file. You can also override the system template that gets copied to
new users.

A skeleton:

```yaml
# system.yaml
compliance_weight: 0.7        # weight of hard-rule compliance in DriftScore
distributional_weight: 0.3    # weight of distributional targets
warning_threshold: 0.85       # below this → log warning
intervention_threshold: 0.6   # below this → trigger reflection
observation_window: 50        # rolling window in turns

rules:
  - id: example_hard
    kind: hard
    pattern: 'forbidden phrase'
    message: "Won't say that."
    block: true
```

Three rule kinds, three recipes below.

## Recipe 1 — a hard rule (block on match)

Hard rules abort the turn if matched. Use sparingly — they're a hammer.

Example: never disclose private keys.

```yaml
rules:
  - id: no_private_key_disclosure
    kind: hard
    pattern: '\b0x[a-fA-F0-9]{64}\b'
    message: "I won't share or echo private keys."
    block: true
    apply_to: [pre, post]
```

- **`pattern`**: regex (Python `re` syntax). Matches abort.
- **`message`**: shown to the user in place of the LLM response.
- **`apply_to`**: `pre` (check user input), `post` (check LLM output),
  or both. For private keys you want both — don't accept them and don't
  echo them.

After saving the YAML, twins reload it lazily on next chat turn. No
restart needed (set `RELOAD_CONTRACT_ON_EACH_TURN=1` in dev to make
this immediate).

### Test it

```python
# tests/test_my_contract.py
from nexus_core.contracts.engine import ContractEngine
from nexus_core.contracts.spec import ContractSpec

def test_no_private_key_disclosure():
    spec = ContractSpec.from_yaml("path/to/system.yaml")
    engine = ContractEngine(spec)

    pre = engine.pre_check("what is 0x" + "a" * 64 + "?")
    assert pre.blocked
    assert "private key" in pre.reason.lower()

    pre_clean = engine.pre_check("what is the weather today?")
    assert not pre_clean.blocked
```

## Recipe 2 — a soft rule (log + warn but don't block)

Soft rules let the response through but record a violation. Useful
when "we'd prefer the agent didn't" but blocking entirely would be too
aggressive.

Example: medical advice should always include a disclaimer.

```yaml
rules:
  - id: medical_advice_disclaimer
    kind: soft
    pattern: '\b(diagnos\w+|prescrib\w+|treatment plan)\b'
    message: "Should add a 'consult a professional' disclaimer."
    apply_to: [post]   # only check assistant output
```

A match on this rule:
1. Does **not** block the turn.
2. Logs `contract_violation` event with rule id (visible in
   `/agent/timeline`).
3. Adds to the soft-compliance metric in DriftScore.

If you want the agent to actually retry / amend its output, that's
**not** the soft rule's job — that's a future feature. Today soft
rules are observational + drift-feeding.

## Recipe 3 — a distributional rule (target, not match)

Distributional rules don't fire per-turn. They define a target that the
agent's output should *trend toward* over a rolling window. DriftScore
aggregates compliance.

Example: keep responses concise.

```yaml
rules:
  - id: response_length_under_4k
    kind: distributional
    target: response_chars < 4000
    weight: 0.5
```

- **`target`**: a predicate evaluated against post-check `details`.
  Available fields:
  - `response_chars` — length of LLM output
  - `tool_calls_used` — count of tools invoked this turn
  - `latency_ms` — turn latency
  - (custom — register handlers in `ContractEngine` to extend)
- **`weight`**: relative weight in the distributional metric. Sum of
  weights doesn't have to be 1; ratios are what matters.

What happens: every turn, the engine evaluates `response_chars < 4000`.
True → 1.0, False → 0.0. DriftScore averages over the window. If recent
turns have been long, the distributional metric drops.

This is **not** about preventing one long response — it's about
detecting "the agent is gradually getting more verbose over time".

## Wiring a per-user override

Sometimes a specific user needs different rules (enterprise customer
with stricter PII rules, or a dev account that can do more).

The engine loads `system.yaml` first, then overlays
`{base_dir}/contracts/user_rules.json` if present. JSON shape:

```json
{
  "additional_rules": [
    {
      "id": "enterprise_pii",
      "kind": "hard",
      "pattern": "\\b\\d{3}-\\d{2}-\\d{4}\\b",
      "message": "PII detected — refusing.",
      "apply_to": ["pre", "post"]
    }
  ],
  "disabled_rules": ["medical_advice_disclaimer"],
  "overrides": {
    "warning_threshold": 0.95
  }
}
```

A typical use: server reads the user's enterprise tier from the DB,
writes appropriate `user_rules.json` to their twin's contracts dir.
Twin reloads on next turn.

## Adding a custom rule kind (advanced)

Sometimes the three built-in kinds aren't enough — you might want
"semantic check via LLM" or "numeric range check on tool output". The
engine has an extension point at `ContractEngine._evaluate_rule(rule)`:

```python
# In a wrapper / subclass:
class CustomContractEngine(ContractEngine):
    async def _evaluate_rule(self, rule, text, ctx):
        if rule.kind == "semantic":
            # Run a small LLM check
            verdict = await self._llm_check(rule.prompt, text)
            return CheckOutcome(matched=not verdict.ok, reason=verdict.reason)
        return await super()._evaluate_rule(rule, text, ctx)
```

Then in twin construction, swap the engine instance. Cost: each
"semantic" rule = one extra LLM call per turn. Use sparingly.

## Where rules live in the codebase

- **Definitions**: `packages/sdk/nexus_core/contracts/rules.py`
- **Spec loader / validator**: `packages/sdk/nexus_core/contracts/spec.py`
- **Engine (pre/post-check)**: `packages/sdk/nexus_core/contracts/engine.py`
- **Drift tracker**: `packages/sdk/nexus_core/contracts/drift.py`
- **Twin integration**: `packages/nexus/nexus/twin.py` (steps 1, 5,
  6 of the 9-step flow)
- **Per-user override**: `~/.nexus_server/twins/{user_id}/contracts/`

## Common mistakes

- **Regex anchored too tightly**. `^password$` won't match
  `my password is 12345`. Test patterns against realistic inputs
  before shipping.
- **Hard rule on a common phrase**. Pattern `secret` matches "the
  secret to a good cup of tea" — turn aborts on benign chat. Tighten
  with word boundaries or context.
- **Distributional target with a unit mismatch**. `response_chars <
  4000` and `response_chars < 4000.0` both work; `response_chars <
  "4000"` (string) silently never matches.
- **Forgetting `apply_to`**. Default is `["post"]` only. If you want
  pre-check too, list both: `[pre, post]`.

## Related concepts

- [ABC](../concepts/abc.md) — the safety layer's full design
- [DPM](../concepts/dpm.md) — `contract_violation` events flow
  through the same event log
- [data-flow](../concepts/data-flow.md) — exactly where pre-check
  (step 1) and post-check (step 6) sit in a turn
