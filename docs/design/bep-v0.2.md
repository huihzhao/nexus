# Nexus BEP v0.2 — System Design

> Architectural design doc folding the code-review punch list into
> BEP-Nexus's next iteration. Where v0.1 (`docs/BEP-nexus.md`)
> defined the minimum viable on-chain anchor + Greenfield storage
> shape, v0.2 makes the system actually *operable* under realistic
> loads: multi-device users, growing event logs, multiple memory
> categories, gradual de-custodialisation, and a maintainable code
> layout.
>
> **Status:** Draft. Owner: huihzhao.
> **Inputs:** BEP-Nexus v0.1 + code review (2026-04-28).
> **Output:** BEP-Nexus v0.2 + ROADMAP Phase I.

---

## 0. Goals + non-goals

### Goals

1. **Multi-runtime concurrency** — a user with a laptop, phone, and
   server-hosted runtime should be able to write to the same agent
   without losing data.
2. **Bounded manifest size** — anchor cost stays O(active state),
   not O(lifetime events).
3. **Memory taxonomy** — separate "what I remember" from "how I
   behave" from "what I can do" so retention policies, privacy
   grants, and update frequencies can differ per category.
4. **Non-custodial path** — credible roadmap from server-signs-all
   to user-signs-all without forking the protocol.
5. **Maintainable code** — split the 1000+ line monoliths so
   contributors can navigate by responsibility, not by `Ctrl-F`.

### Non-goals

- ZK state proofs (out of scope; `merkleRoot` reserves optionality).
- Cross-chain identity portability (single BSC tokenId for now).
- Encrypted-at-rest EventLog (deferred — design hooks left in
  schema for a future `cipher` field).
- Token economics for runtime hosting / storage payment (out of
  scope; assume operator pays for now).

---

## 1. Functional + non-functional requirements

### Functional

| FR | Requirement |
| --- | --- |
| F1 | Two runtimes authorised on the same `tokenId` MUST be able to interleave state writes without data loss. |
| F2 | NFT transfer MUST evict all writers in O(1) chain ops. |
| F3 | A new runtime joining mid-history MUST be able to reconstruct the agent's full curated state in O(active size), not O(lifetime). |
| F4 | A user MUST be able to grant *read-only* access to a third party (auditor / friend's agent) without granting write. |
| F5 | Each curated-memory category (facts / episodes / skills / persona / knowledge) MUST be independently versionable and grantable. |
| F6 | A user MUST be able to migrate from custodial server-signed state to user-signed state without losing prior on-chain history. |
| F7 | The reference implementation MUST round-trip the anchor pipeline (build → canonicalize → hash → write → re-read → verify). |

### Non-functional

| NFR | Target |
| --- | --- |
| Anchor write throughput | 1 anchor per agent per ~5 min average; bursts to 1/sec under heavy compaction |
| Anchor cost | < $0.05 per write at 5 gwei BSC mainnet (~84 bytes touched + Merkle root SLOAD) |
| Manifest read time | < 500 ms p99 for warm Greenfield bucket, manifest ≤ 100 KB |
| Storage cost growth | Linear in event count; retention policy bounds active footprint |
| Concurrent runtimes | 3 active per agent (laptop / phone / server) |
| Failure mode | Greenfield outage → writes queue locally; chain outage → idempotent retry |
| Test conformance | Every BEP test vector reproducible byte-for-byte by reference impl |

---

## 2. High-level design

```
┌──────────────────────────────────────────────────────────────────────────┐
│                            BSC (consensus)                                │
│                                                                           │
│  ERC-8004           AgentStateExtension v2     TaskStateManager           │
│  Identity  ◄──────►  + writers[]               + version counter          │
│  Registry             + readers[]                                          │
│                       + lastKnownOwner                                    │
│                       + version (anchor counter)                          │
└──────┬─────────────────────┬─────────────────────────────────────────────┘
       │                     │
       │ ownerOf(tokenId)    │ getState / authoriseRuntime
       │                     │
┌──────▼──────┐      ┌───────▼────────┐
│ Wallet      │      │ AgentRuntime A │      ┌────────────────┐
│ (custodial  │      │  (laptop)      │◄────►│ AgentRuntime B │
│  or user)   │      └───────┬────────┘      │  (phone)       │
└─────────────┘              │               └───────┬────────┘
                             │                       │
                             ├───────────────────────┘
                             │
                             ▼
              ┌────────────────────────────────┐
              │   Greenfield bucket            │
              │   nexus-agent-{tokenId}/       │
              │                                 │
              │   manifest.json   ← state_root  │
              │   compacts/{seq}.json           │
              │   events/{chunk_id}.jsonl       │
              │   memory/                       │
              │     facts/{id}.json             │
              │     episodes/{id}.json          │
              │     skills/{id}.json            │
              │     persona/v{N}.json           │
              │     knowledge/{id}.json         │
              │   tasks/{taskId}.json           │
              └────────────────────────────────┘
```

### Component split (v0.2)

| Component                | New in v0.2 | Purpose |
| --- | --- | --- |
| `AgentStateExtension` v2 | yes — adds `writers` set, `readers` set, `version` counter | Multi-writer authority + read grants + optimistic concurrency |
| Manifest schema v2       | yes | Chunked references instead of inline events |
| `nexus_core.anchor`      | shipped in v0.1 | Build / canonicalize / hash anchor batches |
| `nexus_core.compactor`   | extends existing `EventLogCompactor` | Drives batch boundaries + chunk uploads |
| `nexus_core.memory.{facts,episodes,skills,persona,knowledge}` | yes (replaces flat `CuratedMemory`) | Per-namespace stores |
| Auth modes               | spec'd | Custodial / Passkey-Wallet / Smart-Account three-phase |

---

## 3. Deep dive: 5 architectural domains

### 3.1 Multi-runtime authority + read grants

**Requirement.** F1, F2, F4. A user with multiple devices needs N
authorised writers; auditors need read-only.

**Solidity (BEP v0.2 §1.1):**

```solidity
contract AgentStateExtension {
    struct AgentState {
        bytes32 stateRoot;
        bytes32 merkleRoot;
        uint64  version;          // bumps on every successful state write
        address lastKnownOwner;
        uint256 updatedAt;
    }

    mapping(uint256 => AgentState) public states;

    /// tokenId → set of authorised writers.
    /// Must always include the NFT owner (enforced lazily; see modifier).
    mapping(uint256 => mapping(address => bool)) public writers;
    mapping(uint256 => uint16) public writerCount;

    /// tokenId → set of authorised readers (for off-chain Greenfield grants).
    /// Storing on chain so a third party can verify the grant exists
    /// without a side channel; the actual Greenfield permission write
    /// is done by the wallet observing AuthorisedReader events.
    mapping(uint256 => mapping(address => bool)) public readers;
    mapping(uint256 => uint16) public readerCount;

    uint16 public constant MAX_WRITERS = 8;
    uint16 public constant MAX_READERS = 64;

    event StateRootUpdated(
        uint256 indexed tokenId,
        bytes32 indexed newRoot,
        uint64  newVersion,
        bytes32 prevRoot,
        bytes32 merkleRoot,
        address writer,
        uint256 timestamp
    );

    event WriterAuthorised(uint256 indexed tokenId, address indexed writer);
    event WriterRevoked(uint256 indexed tokenId, address indexed writer);
    event ReaderAuthorised(uint256 indexed tokenId, address indexed reader, uint8 grantTier);
    event ReaderRevoked(uint256 indexed tokenId, address indexed reader);
    event RuntimeResetOnTransfer(
        uint256 indexed tokenId, address indexed newOwner,
        address prevOwner, uint16 evictedWriters, uint16 evictedReaders
    );

    /// Lazy NFT-transfer detection — runs at the top of every mutating call.
    modifier resetIfTransferred(uint256 tokenId) {
        AgentState storage s = states[tokenId];
        address currentOwner = identityRegistry.ownerOf(tokenId);
        if (s.lastKnownOwner != currentOwner) {
            uint16 wc = writerCount[tokenId];
            uint16 rc = readerCount[tokenId];
            // Note: we DO NOT iterate to clear individual mapping entries —
            // that would be O(N) gas. Instead we bump a generation
            // counter and treat all writers/readers from prev gen as
            // revoked (see _isAuthorised). Cheap O(1) eviction.
            generation[tokenId] += 1;
            writerCount[tokenId] = 0;
            readerCount[tokenId] = 0;
            s.lastKnownOwner = currentOwner;
            emit RuntimeResetOnTransfer(tokenId, currentOwner, s.lastKnownOwner, wc, rc);
        }
        _;
    }

    function updateStateRoot(
        uint256 tokenId,
        bytes32 newRoot,
        bytes32 newMerkleRoot,
        uint64  expectedVersion
    ) external resetIfTransferred(tokenId) {
        AgentState storage s = states[tokenId];
        require(_isAuthorisedWriter(tokenId, msg.sender), "not authorised");
        require(s.version == expectedVersion, "version mismatch");
        s.stateRoot = newRoot;
        s.merkleRoot = newMerkleRoot;
        s.version = expectedVersion + 1;
        s.updatedAt = block.timestamp;
        emit StateRootUpdated(
            tokenId, newRoot, s.version,
            /* prevRoot */ s.stateRoot, newMerkleRoot,
            msg.sender, block.timestamp
        );
    }

    function authoriseWriter(uint256 tokenId, address w)
        external resetIfTransferred(tokenId)
    {
        require(msg.sender == identityRegistry.ownerOf(tokenId), "only owner");
        require(writerCount[tokenId] < MAX_WRITERS, "writer cap");
        if (!writers[tokenId][w]) {
            writers[tokenId][w] = true;
            writerCount[tokenId] += 1;
            emit WriterAuthorised(tokenId, w);
        }
    }
    // ... revokeWriter / authoriseReader / revokeReader symmetrically ...
}
```

**Key design points:**

- **`expectedVersion` is the optimistic-concurrency knob.** Two
  runtimes both reading at version=42 and racing to write — the
  loser's tx reverts; it must re-read manifest, rebuild, retry.
  Cost: one extra SLOAD per write. Benefit: no data loss.
- **Generation-counter eviction on transfer.** Naively iterating
  to clear `writers[tokenId][...]` would be O(N) and DoS-able.
  We bump `generation[tokenId]` and treat all writers from a
  previous generation as revoked in `_isAuthorisedWriter`. That
  function does:
  ```solidity
  function _isAuthorisedWriter(uint256 tokenId, address w) internal view returns (bool) {
      if (w == identityRegistry.ownerOf(tokenId)) return true;
      return writers[tokenId][w] && writerGen[tokenId][w] == generation[tokenId];
  }
  ```
- **Reader grants are recorded on chain** (`readers` mapping +
  `ReaderAuthorised` event) but Greenfield enforcement is off
  chain — the wallet observes events and pushes equivalent
  `Greenfield Policy` writes. This keeps verifiability (anyone can
  audit the grant graph) without conflating two storage systems.
- **`grantTier` byte** in `ReaderAuthorised` lets us differentiate
  classes of readers without bloating the contract:
  ```
  0x01 = curated memory only (no events)
  0x02 = full read (events + memory + tasks)
  0x04 = audit (full read + receives all future grant events)
  ```

**Write throughput note.** With `expectedVersion` enforcing
linearisation and ~3-second BSC block times, a single agent's
write rate ceiling is ~20 anchors/min. That's far above the ~1
anchor / 5 min target — most writers will see no contention. When
contention spikes (chat burst across two devices), the loser
re-runs locally in <100 ms; user-perceived latency unchanged.

### 3.2 Manifest paging — chunked + Merkle

**Requirement.** F3, NFR storage cost growth.

**Manifest schema v2:**

```json
{
  "schema": "nexus.sync.batch.v2",
  "user_id": "...",
  "anchor_seq": 4500,
  "prev_root": "0x...",

  "compacts": [
    {"from": 1,    "to": 1000, "compact_id": "C001", "hash": "0x..."},
    {"from": 1001, "to": 3000, "compact_id": "C002", "hash": "0x..."},
    {"from": 3001, "to": 4500, "compact_id": "C003", "hash": "0x..."}
  ],

  "events_tail": {
    "chunks": [
      {"seq_range": [4501, 4600], "object": "events/004501-004600.jsonl", "hash": "0x..."}
    ]
  },

  "curated_memory": {
    "facts":     {"root": "0x...", "count": 142, "last_update": 1730000000},
    "episodes":  {"root": "0x...", "count": 28},
    "skills":    {"root": "0x...", "count": 12},
    "persona":   {"root": "0x...", "current_version": 7},
    "knowledge": {"root": "0x...", "count": 5}
  },

  "tasks_root": "0x...",

  "retention_policy": {
    "events_keep_compacts": 10,
    "events_chunk_max_age_days": 365
  },

  "ext": {}
}
```

**Where the bytes go:**

- `state_root` = SHA-256 of canonicalised v2 manifest. Same
  pipeline as v1 — only schema string changes.
- `merkleRoot` = keccak256 binary tree over
  `compacts[].hash ++ events_tail.chunks[].hash ++ memory.{*}.root`.
  Lets a future verifier prove a specific event/compact is
  included without fetching the whole manifest.

**Compact thresholds (recommended defaults, configurable):**

```
events_since_last_compact ≥ 1000   →  soft trigger (background)
tail_bytes ≥ 1 MB                   →  hard trigger (foreground)
time_since_last_compact ≥ 24h       →  periodic trigger
```

Whichever fires first runs the compactor:
1. Read the unprocessed event tail.
2. Run `EventLogCompactor` → produces curated memory updates.
3. Upload events as a new chunk + curated updates to Greenfield.
4. Build new v2 manifest referencing the new chunk + updated memory roots.
5. Hash + write to chain.

**Retention.** `retention_policy.events_keep_compacts: N` means
"after the latest N compacts, drop their event chunks from
Greenfield" (saves storage at the cost of replay verifiability for
that history). Default N=10 — full audit trail for the most
recent ~10K events plus aggregated state forever.

### 3.3 Curated memory taxonomy

**Requirement.** F5. Five orthogonal namespaces with different
retention / update / privacy semantics.

| Namespace | Update freq | TTL default | Privacy default | Greenfield path |
| --- | --- | --- | --- | --- |
| `facts/` | high (every chat) | none | private | `memory/facts/{id}.json` |
| `episodes/` | medium (per session) | 90d | private | `memory/episodes/{id}.json` |
| `skills/` | low (per task) | none | private | `memory/skills/{id}.json` |
| `persona/` | very low (weekly) | none | per-version | `memory/persona/v{N}.json` |
| `knowledge/` | very low | none | sometimes-public | `memory/knowledge/{id}.json` |

**Why split:**

- TTL: a 90-day-old conversation episode is rarely useful for
  current chat but a fact ("user is allergic to peanuts") is
  forever-relevant.
- Update frequency: persona changes weekly, facts change
  intra-conversation. Putting them in one store means every
  fact-write triggers a persona-cache miss.
- Privacy: knowledge ("my notes on ERC-8004") may be
  user-public; episodes ("my conversation about my divorce") may
  not. Per-namespace grant (§3.1's `grantTier`) makes this
  enforceable.

**Persona versioning** (special case):

```
memory/persona/
├── v0001.json    {created_at, persona_text, prev_version: null, drift_metrics, source_evolutions}
├── v0002.json    {prev_version: "v0001", ...}
├── ...
└── _current.json {version: "v0007"}    ← pointer
```

Versioned because:
1. Persona drift is the riskiest evolution (LLM can subtly
   re-shape "who the agent is").
2. Users / auditors need to diff "v3 → v4" to understand changes.
3. Rollback is meaningful — fact deletion is destructive, persona
   rollback restores known-good behaviour.

**Python API (per namespace, post-Phase-I refactor):**

```python
twin.memory.facts.add(key, value, importance=...)
twin.memory.facts.search(query, k=10)
twin.memory.episodes.recent(limit=20)
twin.memory.skills.find_strategy_for(task_kind)
twin.memory.persona.current()            # v_current
twin.memory.persona.version(N)           # historical
twin.memory.knowledge.publish(article)
```

Each namespace manages its own SQLite/JSONL on the runtime side
and contributes one entry to `manifest.curated_memory.{ns}.root`.

### 3.4 Non-custodial path — three phases

**Requirement.** F6.

| Phase | Auth | Wallet location | When |
| --- | --- | --- | --- |
| **C — Custodial** (today) | Passkey + JWT (server-side) | `SERVER_PRIVATE_KEY` signs all on-chain ops | Now → 6 mo |
| **P — Passkey-derived** | Passkey w/ PRF extension → derive secp256k1 keypair | In-browser, never leaves device | 3-9 mo |
| **A — Smart account** | EIP-7702 / ERC-4337 + session keys | User wallet; server gets time-boxed session-key | 9-18 mo |

**Migration mechanic.**
The contract's `setActiveRuntime` / `authoriseWriter` / NFT-owner
identity stays the same across all three phases — what changes
is *who controls the wallet that calls them*.

```
Phase C → P:
  1. User opens "Migrate to user-controlled" flow.
  2. Browser does WebAuthn PRF extension to derive secp256k1 keypair.
  3. Server signs a `transferFrom(serverWallet, userWallet, tokenId)` ERC-721 tx.
  4. Server's `authoriseWriter` for itself drops to read-only;
     user's new wallet becomes NFT owner.
  5. NFT transfer fires `RuntimeResetOnTransfer` — clean slate.
  6. User authorises whichever runtimes they want from new wallet.

Phase P → A:
  1. User connects MetaMask / WalletConnect to a smart account.
  2. Smart account inherits ownership (off-chain transfer flow,
     same on-chain transferFrom semantics).
  3. User can now grant time-boxed session keys to runtime hosts
     via ERC-4337 session-key extension.
```

**Why this works without protocol forks.** All three phases use
the same `AgentStateExtension` contract. Phase boundaries are UX
upgrades, not contract upgrades.

**Risk:** users in Phase C who never migrate are perpetually
custodial. Mitigation: server SHOULD show "you're in custodial
mode" UI + nag-banner after the first month.

### 3.5 Monolith decomposition (Phase I)

**Requirement.** Maintainability. Six files exceed 600 lines:

```
packages/nexus/nexus/twin.py              1438
packages/sdk/nexus_core/greenfield.py     1018
packages/sdk/nexus_core/state.py           947
packages/server/nexus_server/llm_gateway.py 779
packages/server/nexus_server/twin_manager.py 735
packages/sdk/nexus_core/chain.py           660
```

**Target layout** (each file ≤ 400 lines, single responsibility):

```
packages/nexus/nexus/
├── twin/
│   ├── __init__.py            Re-export DigitalTwin (back-compat)
│   ├── core.py                Class shell + chat loop (≤ 400 LOC)
│   ├── identity.py            _register_identity, _resolve_chain_kwargs
│   ├── lifecycle.py           create / close / pickling
│   └── tooling.py             register_tool, default-tool installation

packages/sdk/nexus_core/
├── chain/
│   ├── __init__.py            Re-export BSCClient
│   ├── client.py              Web3 wrapper (~200 LOC)
│   ├── identity.py            ERC-8004 register / resolve
│   ├── state_ext.py           AgentStateExtension binding
│   └── tasks.py               TaskStateManager binding
├── greenfield/
│   ├── __init__.py            Re-export GreenfieldClient
│   ├── client.py              HTTP API (~250 LOC)
│   ├── bucket.py              Bucket creation / policy
│   └── object.py              Object upload / download / list
├── memory/
│   ├── facts.py               (existing, refactor)
│   ├── episodes.py            (NEW)
│   ├── skills.py              (existing)
│   ├── persona.py             (existing, add versioning)
│   └── knowledge.py           (existing, refactor)
└── runtime/
    ├── builder.py             (existing — already small)
    └── facade.py              AgentRuntime class (split out of providers.py)

packages/server/nexus_server/
├── llm_gateway/
│   ├── __init__.py            Re-export router
│   ├── routes.py              FastAPI endpoint
│   ├── attachment.py          Attachment validation + cap enforcement
│   └── twin_dispatch.py       Look up twin + delegate
└── twins/
    ├── manager.py             Lifecycle + lazy create
    ├── identity_bootstrap.py  bootstrap_chain_identity
    ├── reaper.py              Idle eviction
    └── chain_log.py           _ChainActivityLogHandler
```

**Migration strategy.** Each split is mechanical (cut + paste +
update imports). Keep the old module path as a re-export shim
for one release, then remove the shim.

```python
# packages/nexus/nexus/twin.py (transitional)
"""Back-compat shim — code moved to twin/ subpackage."""
from .twin.core import DigitalTwin     # noqa: F401
from .twin.lifecycle import *          # noqa: F401, F403
```

**Test stability.** No test changes if the shim is in place.
After shim removal, do a single `from nexus.twin import` →
`from nexus.twin.core import` sweep across tests.

---

## 4. Scale + reliability

### Load model (single agent)

| Workload | Frequency | On-chain TX | Greenfield bytes |
| --- | --- | --- | --- |
| User chat turn | 1 every 30s active | 0 | ~5 KB (event row + maybe attachment) |
| Compact | every 1000 turns | 1 (`updateStateRoot`) | ~50 KB (compact JSON + chunk) |
| Persona evolve | weekly | 1 (`updateStateRoot`) | ~20 KB (new persona version) |
| Knowledge compile | monthly | 1 (`updateStateRoot`) | varies |
| Read sync (new runtime) | per join | 0 | fetch entire `manifest.json` + recent chunks |

**Daily anchor count per active agent: ~50** (compactor +
persona/knowledge background runs).

**At 100K active agents:** 5M anchor TX/day = ~58 TPS sustained.
BSC handles ~100 TPS → fine if anchors are spread across the day.
Hot-spot risk if all 100K agents compact at the same minute (e.g.
midnight UTC) — mitigation: jitter compactor schedule per-agent.

### Failure modes

| Failure | Mitigation | Recovery |
| --- | --- | --- |
| Greenfield outage (write) | Local EventLog buffers; runtime keeps appending | Drains on recovery; if runtime dies, restarts replay buffered ops |
| Greenfield outage (read) | New runtime joining can't bootstrap | Wait for recovery; existing runtimes unaffected |
| BSC outage | `updateStateRoot` queues; chat continues from local state | Drain queue on recovery |
| Two runtimes write simultaneously | Optimistic concurrency: loser retries | <1s re-read + resubmit |
| NFT transfer mid-flight | Pre-transfer in-flight writes may land before `resetIfTransferred` fires | Bound by 1 block — within tolerance |
| Manifest hash mismatch (storage corruption) | Reader verifies SHA-256 on every load | Halt writes; alert; require fresh anchor batch from a healthy runtime |
| LLM hallucination corrupts memory | ABC drift detection; persona versioning enables rollback | Auto-rollback to last clean persona version; admin review |

### Monitoring (server side)

Per-agent metrics:
- `nexus.anchor.write.success` / `.failed` counters
- `nexus.anchor.write.latency_ms` histogram
- `nexus.compactor.events_per_run` histogram
- `nexus.greenfield.bytes_uploaded` / `.bytes_downloaded` counters
- `nexus.contract.drift_score` gauge (per-agent ABC compliance)
- `nexus.writer.version_mismatches` counter (concurrent-write contention)

Alerts:
- Drift score > intervention threshold for any agent → page operator + auto-pause writes.
- Anchor write failure rate > 1% for 5 min → page operator.
- Greenfield bucket unreachable for any agent > 60s → log + retry, alert at 5 min.

---

## 5. Trade-off analysis

### T1. Single state_root vs. per-namespace roots

**Decision:** Keep single `state_root`; expose namespace roots
inside the manifest as derived data.

**Trade-off:**
- ✅ Simpler chain contract (one mapping, one mutating function).
- ✅ Atomic commit semantics (all namespaces advance together).
- ❌ Granting one auditor "facts only" requires off-chain
  enforcement; chain doesn't restrict.
- ❌ Updating just one namespace still triggers a full manifest
  re-hash (cheap — ~100 KB SHA-256).

**Re-visit if:** facts namespace updates 100x more than others
and partial updates start dominating cost.

### T2. SHA-256 vs keccak256 for state_root

**Decision:** SHA-256 for `state_root` (universal, FIPS); add
optional `merkleRoot` (keccak256) for future on-chain proofs.

**Trade-off:**
- ✅ Off-chain verifiers (auditors, mobile clients) don't need
  EVM-specific hash libs.
- ✅ Reserves Phase III optionality without commitment.
- ❌ Two hashes per anchor write — but `merkleRoot` is computed
  off-chain and free.

### T3. On-chain writers set vs. single activeRuntime

**Decision:** Multi-writer set (max 8) + version counter.

**Trade-off:**
- ✅ Multi-device users supported natively.
- ✅ Optimistic concurrency well-understood.
- ❌ Per-tx cost ~5K gas higher (writers mapping SLOAD).
- ❌ More attack surface — more writers = more keys to protect.

**Re-visit if:** typical agent has 1-2 writers (cost > value); we
could collapse back to single activeRuntime + delegation hook.

### T4. RFC 8785 (JCS) vs custom canonical form

**Decision:** Cite JCS as normative; ship JCS-compatible
implementation that uses `json.dumps(sort_keys, …)` for the
schema's restricted subset.

**Trade-off:**
- ✅ Cross-language reproducibility guaranteed by the JCS spec.
- ✅ Zero new dependency for the common case (small JSON).
- ❌ If schema grows to floats / non-ASCII strings, runtimes need
  a real JCS lib.

### T5. Server-custodial (Phase C) shipping default

**Decision:** Ship Phase C as default for the first ~6 months.

**Trade-off:**
- ✅ Zero-friction onboarding (no wallet UX in browser).
- ✅ Lets us iterate the protocol without users locked into
  irrevocable on-chain identities.
- ❌ Centralisation criticism is valid.
- ❌ Trust-the-server failure mode.

**Mitigation:** prominent UI banner, opt-in Phase P migration as
soon as PRF passkey support stabilises, no fund-related ops on
the server-controlled wallet (state-root only — no token
transfers).

### T6. Versioned persona vs. mutable persona

**Decision:** Versioned (every PersonaEvolver run produces a new
file under `memory/persona/v{N}.json`).

**Trade-off:**
- ✅ Audit trail for the riskiest evolution loop.
- ✅ Rollback is trivial.
- ❌ Greenfield storage cost grows unbounded — but persona files
  are small (1-5 KB each), 1000 versions = ~5 MB.

### T7. Monolith split — re-export shims vs. clean break

**Decision:** Keep shims for one release, then remove.

**Trade-off:**
- ✅ Zero test churn during refactor.
- ✅ External users (none yet, but soon) get a deprecation
  signal.
- ❌ Two paths to the same code temporarily.

---

## 6. Rollout plan

### Phase I — code reorg (4 days, no behaviour change)

Mechanical splits per §3.5. Re-export shims keep tests green.
Single PR per package.

### Phase J — memory taxonomy (1 week)

Replace flat `CuratedMemory` with the 5-namespace API. Migration:
new agents get the new layout; existing test agents are wiped
(test phase, no real user data). Persona versioning is the most
involved change.

### Phase K — manifest v2 + chunked compactor (1 week)

Implement schema v2 in `nexus_core.anchor`, add compactor
chunking, retention policy. Reference impl + new test vectors.
BEP draft updated to v0.2.

### Phase L — multi-writer contract (3 days)

Solidity update + Hardhat tests for the v2 `AgentStateExtension`
+ `TaskStateManager`. Deploy fresh testnet contracts; old testnet
deployments abandoned (no user data lock-in).

### Phase M — non-custodial Phase P (4 weeks)

WebAuthn PRF passkey support — currently ~70% browser coverage.
Wait for Safari 18+ to stabilise. Migration UI in passkey page.

### Phase N — non-custodial Phase A (post-Phase M)

EIP-7702 session keys; depends on smart-account UX stabilising.

**Total v0.2 to mainnet readiness: ~3 weeks of focused work**
(Phases I–L); Phase M depends on browser support, Phase N is
post-mainnet polish.

---

## 7. What I'd revisit at scale

1. **Anchor batching across agents.** At 100K+ agents, each one
   posting its own `updateStateRoot` is wasteful. A relayer that
   batches `(tokenId, newRoot, …)` tuples into a single chain TX
   could cut TX count 100x. Adds trust assumption (relayer can
   delay but not forge).
2. **Greenfield → IPFS dual-pin.** For users who want public
   verifiability, mirror Greenfield objects to IPFS. Optional per
   bucket, signalled via a `greenfield.dual_pin: true` policy.
3. **Encrypted EventLog.** The schema reserves `cipher` and
   `encryption_method` fields. Today everything is plaintext (per
   the privacy non-goal); when we ship, the wallet derives a key
   from the passkey PRF and encrypts entries before upload.
4. **Cross-tokenId mention graph.** Social Protocol's gossip
   creates implicit edges between agents. At scale, querying "all
   gossip sessions agent A had with agent B in the last week"
   should not require scanning every bucket. Add an indexer
   service that consumes `GossipMessage` events from chain (we
   don't currently emit those — would need to).
5. **Light-client read path.** Mobile / browser clients shouldn't
   need to fetch full Greenfield manifests for status displays.
   Maintain a small server-side read cache with verifiable
   freshness guarantees (signed snapshot pointer, refreshed every
   N seconds).

---

## 8. Open questions (tracked in BEP §"Open questions")

1. **Cross-chain tokenId namespacing** — should `(chainId, tokenId)`
   replace bare `tokenId` in `agentId`? Adds 32 bytes per write.
2. **Active-runtime grace period** — pre-eviction window for
   in-flight writes (currently immediate eviction).
3. **Manifest size cap** — hard upper bound to prevent DoS.
4. **Encrypted EventLog scheme** — key derivation, key rotation,
   how a new authorised runtime gets the key.
5. **Recovery from key loss** — passkey lost = wallet lost = NFT
   lost = agent lost. Social-recovery via guardian addresses?

---

## Appendix A — what's in BEP v0.1 vs v0.2

| Topic | v0.1 (current) | v0.2 (this design) |
| --- | --- | --- |
| State root hash | SHA-256 only | SHA-256 + optional keccak256 Merkle |
| Authority | Single `activeRuntime` | Multi-writer set + reader grants + version counter |
| Manifest | Flat events list | Chunked + Merkle tree |
| Curated memory | Single store | 5 namespaces (facts/episodes/skills/persona/knowledge) |
| Persona | Mutable | Versioned (immutable per version) |
| Auth | Custodial only | Phased (custodial → passkey wallet → smart account) |
| Canonical form | "sorted keys + no whitespace" | RFC 8785 (JCS) |
| Test vectors | 1 (empty) — `TBD` hash | 5 — full hashes pinned, reference impl conformance-tested |

---

## Appendix B — references

- BEP-Nexus v0.1: [`BEP-nexus.md`](../BEP-nexus.md)
- ERC-8004 Agent Identity: https://eips.ethereum.org/EIPS/erc-8004
- RFC 8785 JCS: https://www.rfc-editor.org/rfc/rfc8785
- ERC-4337 Account Abstraction: https://eips.ethereum.org/EIPS/eip-4337
- EIP-7702 Set EOA code: https://eips.ethereum.org/EIPS/eip-7702
- WebAuthn PRF: https://w3c.github.io/webauthn/#prf-extension
- BNB Greenfield: https://docs.bnbchain.org/bnb-greenfield/
- OpenZeppelin Merkle proofs: https://docs.openzeppelin.com/contracts/5.x/api/utils#MerkleProof

---

*End of design doc. Comments / pushback welcome via PR or issue.*
