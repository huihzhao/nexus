# BEP-XXXX: Nexus — Stateless Agent Runtime with Verifiable Identity on BNB Chain

| Field      | Value                                                              |
| ---------- | ------------------------------------------------------------------ |
| BEP        | TBD (assigned upon acceptance)                                     |
| Title      | Nexus: Stateless Agent Runtime with Verifiable Identity            |
| Status     | Draft                                                              |
| Type       | Standards Track                                                    |
| Category   | Application                                                        |
| Author     | huihzhao (jimmy.zz@bnbchain.org)                                   |
| Created    | 2026-04-28                                                         |
| Requires   | ERC-8004 (Agent Identity Registry), BNB Greenfield                 |
| Discussion | https://github.com/huihzhao/nexus/discussions                      |
| Replaces   | —                                                                  |

---

## Abstract

This BEP proposes **Nexus**, a standard for *stateless, identity-anchored
AI agents* on BNB Chain. Nexus combines (1) an existing ERC-8004
agent NFT for identity, (2) two new lightweight Solidity contracts —
**AgentStateExtension** and **TaskStateManager** — that anchor a
content-addressable state pointer on BSC, and (3) a per-agent BNB
Greenfield bucket convention (`nexus-agent-{tokenId}`) that holds
the full agent state under a versioned manifest schema
`nexus.sync.batch.v1`.

The result: an agent's identity and state become *portable across
runtimes*. Any compliant runtime that holds the wallet for a given
token can resume from the on-chain state root, replay the event log
from Greenfield, and continue execution without trust in the
previous host. Cost stays low — ~84 bytes per agent on BSC, full
payloads on Greenfield's pay-per-byte storage.

## Motivation

Today's AI agents have *ephemeral* state. A conversation, a tool
configuration, a learned skill, a behavioural contract — all live
inside a runtime process or a single vendor's database. When the
process dies, the device changes, or the user wants to switch
operators, that state is lost or held hostage.

**ERC-8004 standardised agent *identity*** (an NFT), but identity
without *state* is half a primitive. There is no standard way for
an agent to:

* Persist its memory and behaviour in a form a *different* runtime
  can pick up.
* Prove the integrity of that state to the user, the chain, or
  third parties.
* Coordinate concurrent task lifecycles across multiple operators.

Centralised SaaS solves the persistence problem at the cost of
lock-in and verifiability. Putting full agent state on EVM is
prohibitive — a 100 KB conversation history would cost dollars per
write at typical gas prices.

**BNB Chain is uniquely positioned** to solve this. BSC offers
cheap, fast finality for the small *anchor* (a 32-byte content
hash + small metadata), and Greenfield offers cheap, owner-keyed,
permissioned bulk storage for the large *payload*. The two together
give us an "anchor on BSC, store on Greenfield" pattern that no
other L1 + storage stack natively provides.

Nexus operationalises this pattern. It defines the on-chain shape,
the storage shape, and the manifest schema such that any third-party
client can reproduce an agent's state from public on-chain data
plus permissioned Greenfield reads — making agents truly stateless,
identity-anchored, and operator-portable.

## Terminology

| Term              | Meaning                                                                 |
| ----------------- | ----------------------------------------------------------------------- |
| **Agent**         | A long-lived AI entity identified by an ERC-8004 NFT.                   |
| **AgentRuntime**  | A process or service that *hosts* the agent — runs the LLM loop, executes tools, applies state changes. Multiple runtimes may host the same agent over time, but only one is *active* at a time. |
| **DigitalTwin**   | Reference Nexus implementation of an agent runtime.                     |
| **DPM**           | Deterministic Projection Memory — append-only EventLog + a deterministic projection function `π(events, task, budget) → context`. Replay any prefix of the log → identical projection. |
| **ABC**           | Agent Behaviour Contract — declarative hard/soft rules + a DriftScore that observes compliance over time. |
| **state_root**    | `bytes32` content hash of the agent's curated state, anchored on BSC.   |
| **active_runtime**| `address` of the runtime currently authorised to write state.           |
| **tokenId**       | ERC-721 tokenId from the ERC-8004 Identity Registry — the agent's eternal id. |
| **agentId**       | Application-level alias deterministically derived from tokenId (or human-chosen string for off-chain agents). |
| **Anchor batch**  | A JSON document conforming to `nexus.sync.batch.v1` whose SHA-256 becomes the new `state_root`. |

## Specification

### 1. On-chain contracts

Two contracts are introduced. Both reference the existing ERC-8004
Identity Registry (`identityRegistry`) by `tokenId` — they do **not**
fork ERC-8004.

#### 1.1 AgentStateExtension

```solidity
// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

interface IERC721 {
    function ownerOf(uint256 tokenId) external view returns (address);
}

/// @title AgentStateExtension
/// @notice Anchors a content-addressable state pointer for each
///         ERC-8004 agent NFT. Per-agent on-chain footprint is ~84
///         bytes (state_root: 32B, active_runtime: 20B, updated_at: 32B).
contract AgentStateExtension {
    struct AgentState {
        bytes32 stateRoot;        // Content hash → Greenfield payload
        bytes32 merkleRoot;       // keccak256 hash for future on-chain proofs (optional, may be 0)
        address activeRuntime;    // Currently authorised writer
        address lastKnownOwner;   // Used to detect NFT transfer & auto-reset activeRuntime
        uint256 updatedAt;        // Block timestamp
    }

    /// @dev tokenId → state record.
    mapping(uint256 => AgentState) public states;

    /// @dev ERC-8004 Identity Registry (the NFT contract).
    IERC721 public immutable identityRegistry;

    event StateRootUpdated(
        uint256 indexed tokenId,
        bytes32 indexed newRoot,
        bytes32 prevRoot,           // not indexed — saves ~375 gas per emit
        bytes32 merkleRoot,
        address writer,
        uint256 timestamp
    );

    event ActiveRuntimeChanged(
        uint256 indexed tokenId,
        address indexed newRuntime,
        address prevRuntime,        // not indexed
        uint256 timestamp
    );

    /// @dev Emitted when an NFT-transfer is detected lazily inside an
    ///      authorisation check. activeRuntime is reset to address(0).
    event RuntimeResetOnTransfer(
        uint256 indexed tokenId,
        address indexed newOwner,
        address prevOwner,
        address evictedRuntime
    );

    constructor(address identityRegistry_) {
        identityRegistry = IERC721(identityRegistry_);
    }

    /// @dev Lazy transfer detection — runs at the top of every state-
    ///      mutating function. If the on-chain owner differs from the
    ///      last owner we recorded, the NFT was transferred → evict the
    ///      activeRuntime so the new owner must explicitly re-authorise.
    modifier resetIfTransferred(uint256 tokenId) {
        AgentState storage s = states[tokenId];
        address currentOwner = identityRegistry.ownerOf(tokenId);
        if (s.lastKnownOwner != currentOwner) {
            address prevOwner = s.lastKnownOwner;
            address evicted = s.activeRuntime;
            s.activeRuntime = address(0);
            s.lastKnownOwner = currentOwner;
            if (prevOwner != address(0)) {
                emit RuntimeResetOnTransfer(tokenId, currentOwner, prevOwner, evicted);
            }
        }
        _;
    }

    /// @notice Update the state root. Caller MUST be either the
    ///         NFT owner or the current activeRuntime.
    /// @param tokenId        The ERC-8004 token id.
    /// @param newRoot        SHA-256(canonical(manifest)) — content hash.
    /// @param newMerkleRoot  keccak256 Merkle root of event chunks; pass
    ///                       bytes32(0) if your implementation doesn't
    ///                       use chunk Merkle proofs.
    function updateStateRoot(
        uint256 tokenId,
        bytes32 newRoot,
        bytes32 newMerkleRoot
    ) external resetIfTransferred(tokenId) {
        AgentState storage s = states[tokenId];
        address owner = identityRegistry.ownerOf(tokenId);
        require(
            msg.sender == owner || msg.sender == s.activeRuntime,
            "AgentState: not authorised"
        );
        bytes32 prev = s.stateRoot;
        s.stateRoot = newRoot;
        s.merkleRoot = newMerkleRoot;
        s.updatedAt = block.timestamp;
        emit StateRootUpdated(tokenId, newRoot, prev, newMerkleRoot, msg.sender, block.timestamp);
    }

    /// @notice Change the active runtime. Caller MUST be the NFT owner.
    function setActiveRuntime(uint256 tokenId, address newRuntime)
        external
        resetIfTransferred(tokenId)
    {
        require(
            msg.sender == identityRegistry.ownerOf(tokenId),
            "AgentState: only NFT owner"
        );
        AgentState storage s = states[tokenId];
        address prev = s.activeRuntime;
        s.activeRuntime = newRuntime;
        s.updatedAt = block.timestamp;
        emit ActiveRuntimeChanged(tokenId, newRuntime, prev, block.timestamp);
    }

    function getState(uint256 tokenId)
        external
        view
        returns (bytes32 stateRoot, address activeRuntime, uint256 updatedAt)
    {
        AgentState memory s = states[tokenId];
        return (s.stateRoot, s.activeRuntime, s.updatedAt);
    }
}
```

**Authorisation model.** The NFT owner is always authoritative
(can change `activeRuntime`, can directly write `state_root`). The
current `activeRuntime` is a delegated writer — it can update
`state_root` but cannot transfer activeRuntime to someone else.
This separation lets a user grant temporary write authority to a
runtime (e.g. a hosted service) without ceding NFT ownership.

#### 1.2 TaskStateManager

```solidity
contract TaskStateManager {
    enum Status { Pending, Running, Completed, Failed }

    struct TaskRecord {
        bytes32 stateHash;     // SHA-256 → Greenfield task snapshot
        uint64  version;       // Optimistic concurrency counter
        Status  status;
        address writer;        // Address of last writer
        uint256 updatedAt;
    }

    /// @dev keccak256(taskId, tokenId) → TaskRecord
    mapping(bytes32 => TaskRecord) public tasks;

    IERC721           public immutable identityRegistry;
    AgentStateExtension public immutable agentState;

    event TaskUpdated(
        bytes32 indexed taskKey,
        bytes32 indexed taskId,
        uint256 indexed tokenId,
        bytes32 stateHash,
        uint64  version,
        Status  status,
        address writer
    );

    constructor(address identityRegistry_, address agentState_) {
        identityRegistry = IERC721(identityRegistry_);
        agentState = AgentStateExtension(agentState_);
    }

    /// @notice Update a task. expectedVersion MUST equal the current
    ///         version (prevents concurrent overwrites). Status
    ///         transitions are: Pending → Running → {Completed,Failed}.
    function updateTask(
        bytes32 taskId,
        uint256 tokenId,
        bytes32 stateHash,
        uint64  expectedVersion,
        Status  newStatus
    ) external {
        bytes32 key = keccak256(abi.encodePacked(taskId, tokenId));
        TaskRecord storage r = tasks[key];

        // Authorisation: NFT owner or the agent's active runtime.
        (, address activeRuntime,) = agentState.getState(tokenId);
        require(
            msg.sender == identityRegistry.ownerOf(tokenId)
                || msg.sender == activeRuntime,
            "TaskState: not authorised"
        );

        // Optimistic concurrency.
        require(r.version == expectedVersion, "TaskState: version mismatch");

        // Status monotonicity (no resurrection of terminal tasks).
        require(
            r.status != Status.Completed && r.status != Status.Failed,
            "TaskState: task is terminal"
        );

        r.stateHash = stateHash;
        r.version   = expectedVersion + 1;
        r.status    = newStatus;
        r.writer    = msg.sender;
        r.updatedAt = block.timestamp;

        emit TaskUpdated(key, taskId, tokenId, stateHash, r.version, newStatus, msg.sender);
    }

    function getTask(bytes32 taskId, uint256 tokenId)
        external
        view
        returns (TaskRecord memory)
    {
        return tasks[keccak256(abi.encodePacked(taskId, tokenId))];
    }
}
```

### 2. Storage convention — BNB Greenfield

Each agent gets exactly one Greenfield bucket. The bucket name is
**deterministic** from the ERC-8004 tokenId so any party (with
permission) can locate the agent's data without an off-chain
registry:

```
bucket_name = "nexus-agent-{tokenId}"
```

`tokenId` is the decimal representation. Greenfield bucket names
must be 3–63 lowercase characters, so `nexus-agent-` (12 chars)
plus `tokenId` (≤51 digits) fits comfortably.

#### 2.1 Object layout

```
nexus-agent-{tokenId}/
├── manifest.json                       # Latest anchor batch (current state_root)
├── events/
│   ├── 0000000000000001.json           # Append-only EventLog entries
│   ├── 0000000000000002.json
│   └── ...
├── memory/
│   ├── curated/{key}.json              # Distilled memories (CuratedMemory)
│   └── compacts/{seq}.json             # Compactor outputs
├── state/
│   └── checkpoint.json                 # Latest state snapshot for fast resume
└── tasks/
    └── {taskId}.json                   # Per-task records (mirror of TaskStateManager)
```

Object names are zero-padded 16-digit hex sequence numbers where
ordering matters (events, compacts) so lexicographic listing
matches insertion order.

#### 2.2 Permissions

The bucket's primary owner is the agent's wallet (the NFT owner).
Read access can be granted to:

* The current `activeRuntime` (for state synchronisation).
* Specific third parties (auditors, validators) via Greenfield's
  policy primitives.
* `Public` read for inspection by anyone, optionally.

### 3. Anchor batch schema — `nexus.sync.batch.v1`

The bridge between the on-chain `state_root` and the Greenfield
payload is a *manifest object*: a JSON document at
`{bucket}/manifest.json` whose SHA-256 equals `state_root`.

#### 3.1 JSON Schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://github.com/bnbchain/nexus/schemas/nexus.sync.batch.v1.json",
  "type": "object",
  "required": ["schema", "user_id", "events", "sync_ids"],
  "properties": {
    "schema":  { "const": "nexus.sync.batch.v1" },
    "user_id": { "type": "string" },
    "events":  {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["client_created_at", "event_type", "sync_id", "server_received_at"],
        "properties": {
          "client_created_at":   { "type": "string", "format": "date-time" },
          "event_type":          { "type": "string" },
          "content":             { "type": "string" },
          "metadata":            { "type": "object" },
          "session_id":          { "type": "string" },
          "sync_id":             { "type": "integer" },
          "server_received_at":  { "type": "string", "format": "date-time" }
        }
      }
    },
    "sync_ids": {
      "type": "array",
      "items": { "type": "integer" }
    },
    "prev_root": {
      "type": "string",
      "pattern": "^0x[0-9a-fA-F]{64}$"
    }
  }
}
```

#### 3.2 Canonical form + hashing

To make `state_root` reproducible across implementations, the
manifest MUST be serialised using **RFC 8785 — JSON Canonicalization
Scheme (JCS)**. JCS pins all the things ad-hoc "sorted keys + no
whitespace" leaves implementation-defined: number serialisation,
Unicode normalisation, escape forms, key ordering for nested objects.
Two compliant JCS encoders MUST produce byte-identical output for
the same logical document.

A reference implementation in Python (~30 lines) using
``json.dumps(obj, sort_keys=True, separators=(",", ":"),
ensure_ascii=False)`` is correct **only** for manifests whose
numeric fields are integers and whose strings contain no characters
that JCS escapes differently than CPython. For full conformance,
use a JCS library (Python: ``jcs``; JS: ``canonicalize``; Go:
``github.com/cyberphone/json-canonicalization``).

Procedure:

1. Build the manifest object conforming to §3.1.
2. Serialise via JCS → canonical UTF-8 bytes.
3. **`state_root`**: SHA-256 of the canonical bytes — content
   address. Universal, FIPS-blessed, used by off-chain verifiers.
4. **`merkleRoot`** (optional, on-chain): keccak256 over the
   `compacts[].hash` array (or a Merkle tree thereof). Reserved
   for future on-chain proofs (ZK light client / chunk inclusion).
   Implementations that don't use it pass `bytes32(0)`.

Both 32-byte digests are passed to
`AgentStateExtension.updateStateRoot(tokenId, stateRoot, merkleRoot)`.

The `prev_root` field, if present, MUST equal the previous
on-chain `state_root`. This forms a hash chain that lets a verifier
walk back through past anchor batches to validate continuity.

#### 3.3 Event types (non-exhaustive)

| event_type                | Meaning                                                  |
| ------------------------- | -------------------------------------------------------- |
| `user_message`            | User input. May carry `metadata.task_kind` (LLM-classified) for verdict scoring. |
| `assistant_response`      | Agent reply. May carry `metadata.task_kind`.             |
| `attachment_added`        | User uploaded a file (`metadata.size_bytes`, `mime`).    |
| `attachment_distilled`    | LLM-summarised file content (rides as a separate event). |
| `tool_call`               | Tool invocation. May carry `metadata.task_kind`.         |
| `tool_result`             | Tool result.                                             |
| `memory_compact`          | Compactor output (curated memory pin). `metadata.layer ∈ {batch_report, session_summary, full_compact}`. |
| `skill_installed`         | External skill loaded.                                   |
| `contract_violation`      | ABC rule fired.                                          |
| `evolution_proposal`      | **(v0.2)** Self-evolution edit + predicted fixes / regressions. Emitted *before* the edit lands so the manifest hash chain captures intent even if the runtime crashes mid-edit. See §3.4. |
| `evolution_verdict`       | **(v0.2)** Post-window evaluation of an `evolution_proposal`. Scores predicted vs observed task-level deltas. |
| `evolution_revert`        | **(v0.2)** Storage pointer rollback when an `evolution_verdict` decides `reverted` or `kept_with_warning`-then-user-rejects. |
| `evolution_user_approve`  | **(v0.2)** User manually approves a `kept_with_warning` verdict — strong positive feedback signal. |
| `evolution_user_revert`   | **(v0.2)** User manually reverts an edit regardless of verdict — strong negative feedback signal. |

Implementations MAY add additional `event_type` values; consumers
SHOULD ignore unknown types rather than fail closed.

#### 3.4 Falsifiable evolution events (v0.2)

The `evolution_*` family pins every self-evolution edit as an
auditable contract on the same hash chain as user messages.
Inspired by the Agentic Harness Engineering paper (Lin et al.,
arXiv:2604.25850v3, Apr 2026), which proved this pattern lifts
coding-agent pass@1 by +7.3 pp over 10 iterations.

The full design (proposal/verdict scoring, coordinator,
user-in-the-loop UI, rollout phases) lives in
[`design/falsifiable-evolution.md`](design/falsifiable-evolution.md).
This BEP section pins only the *event schema* a compliant runtime
MUST emit.

##### `evolution_proposal` schema

```json
{
  "edit_id":              "string (unique per agent)",
  "evolver":              "MemoryEvolver | SkillEvolver | PersonaEvolver | KnowledgeCompiler",
  "target_namespace":     "memory.facts | memory.episodes | memory.skills | memory.persona | memory.knowledge | middleware.{name}",
  "target_version_pre":   "string (Greenfield object key, e.g. 'memory/facts/v0041.json')",
  "target_version_post":  "string",

  "evidence_event_ids":   "array of integers (sync_id of triggering events)",
  "evidence_summary":     "string (human readable)",
  "inferred_root_cause":  "string",

  "change_summary":       "string",
  "change_diff":          "array of {op, key, value} entries",

  "predicted_fixes":      "array of {task_kind, reason}",
  "predicted_regressions":"array of {task_kind, reason, severity: 'low'|'medium'|'high'}",

  "rollback_pointer":     "string (Greenfield object key to restore)",
  "expires_after_events": "integer (verdict deadline, default per evolver)"
}
```

##### `evolution_verdict` schema

```json
{
  "edit_id":                  "string (matches the proposal)",
  "verdict_at_event":         "integer (sync_id where verdict was scored)",
  "events_observed":          "integer",

  "predicted_fix_match":      "array of {task_kind, observed_count, outcome: 'fixed' | 'no_signal'}",
  "predicted_fix_miss":       "array (predicted fixes with no observed signal)",
  "predicted_regression_match":"array",
  "predicted_regression_miss":"array",
  "unpredicted_regressions":  "array of {task_kind, observed_count, severity, evidence}",

  "fix_score":                "number (0.0-1.0, weighted)",
  "regression_score":         "number (0.0-1.0, weighted)",
  "abc_drift_delta":          "number (DriftScore change over the window)",

  "decision":                 "kept | kept_with_warning | reverted"
}
```

##### Verdict decision rules (normative)

A compliant runtime MUST emit `decision = reverted` when:
- `unpredicted_regressions` contains any entry with `severity ∈ {medium, high}`, OR
- `abc_drift_delta > intervention_threshold` (per ABC contract).

A compliant runtime MUST emit `decision = kept_with_warning` when:
- `unpredicted_regressions` contains any `severity = low` entry, OR
- `abc_drift_delta > warning_threshold`.

Otherwise, `decision = kept`.

A compliant runtime MUST NOT revert based on
`predicted_regressions` that have no observed signal — the AHE
paper's empirical finding is that regression *prediction* is
indistinguishable from random (precision 11.8% vs random 5.6%);
predictions MAY be used as scoring hints but MUST NOT be used as
revert triggers.

##### `evolution_revert` schema

```json
{
  "edit_id":          "string (matches the proposal)",
  "rolled_back_to":   "string (Greenfield object key restored as current)",
  "rolled_back_from": "string (Greenfield object key being deactivated)",
  "trigger":          "unpredicted_regression | abc_drift | user_revert | hard_rule_violation",
  "evidence":         "string"
}
```

### 4. Identity binding — three-ID model

Nexus distinguishes three identifiers:

| ID                | Layer    | Source                                       | Role                                   |
| ----------------- | -------- | -------------------------------------------- | -------------------------------------- |
| `wallet_address`  | Chain    | BSC EOA / smart account                      | Owns the NFT, signs transactions.      |
| `tokenId`         | Identity | ERC-8004 Identity Registry NFT               | The agent's eternal on-chain id.       |
| `agentId`         | Runtime  | Deterministic from tokenId (or chosen string)| Application-level handle in runtime APIs. |

For chain-bound agents, `agentId` is computed as
`agent_id_to_int(tokenId)` (a deterministic 256-bit hash) so the
runtime can address agents by stable string while reads still hit
the right tokenId on chain. For local-only / off-chain agents, the
operator may choose any string identifier.

### 5. Reference runtime API

A compliant runtime MUST expose at least:

```python
import nexus_core

# Local mode (no chain)
rt = nexus_core.local(base_dir=".nexus_state")

# Chain mode (BSC testnet + Greenfield testnet)
rt = nexus_core.testnet(
    private_key="0x...",
    rpc_url="https://data-seed-prebsc-1-s1.bnbchain.org:8545",
    agent_state_address="0x...",
    task_manager_address="0x...",
    identity_registry_address="0x8004A818BFB912233c491871b3d84c89A494BD9e",
    greenfield_bucket="nexus-agent-{tokenId}",
)

# Five sub-providers. Same shape across all backends.
rt.sessions    # SessionProvider
rt.memory      # MemoryProvider
rt.artifacts   # ArtifactProvider
rt.tasks       # TaskProvider
rt.impressions # ImpressionProvider (Social Protocol)

# Backend handle for low-level ops
rt.backend
```

Implementations in other languages SHOULD follow the same
five-provider split — it cleanly maps to the five concerns and
matches the Greenfield object layout in §2.1.

### 6. Lifecycle: bootstrap, update, handoff

#### 6.1 Bootstrap (first run for a wallet)

1. Runtime calls `IdentityRegistry.register(wallet, agentURI)` →
   receives `tokenId`.
2. Runtime creates Greenfield bucket `nexus-agent-{tokenId}`,
   sets owner = wallet, grants `activeRuntime` read+write.
3. Runtime calls `AgentStateExtension.setActiveRuntime(tokenId,
   runtimeAddress)`.
4. Runtime initialises empty manifest, hashes it, calls
   `updateStateRoot(tokenId, hash)`.

#### 6.2 Routine state update (every N events)

1. Runtime appends events to local EventLog.
2. Compactor runs (DPM projection):
   * builds a new manifest including the new events,
   * uploads to Greenfield as `manifest.json` (and shifts the
     previous one to a versioned name),
   * hashes the new manifest → `state_root`,
   * calls `AgentStateExtension.updateStateRoot(tokenId, state_root)`.
3. Local replication catches up.

#### 6.3 Cross-runtime handoff

When a user wants to move an agent from runtime A to runtime B:

1. User signs `setActiveRuntime(tokenId, B)` from the wallet.
2. Runtime A observes the event, drains its in-flight writes,
   stops.
3. Runtime B reads `getState(tokenId)` → `(state_root,
   active_runtime=B, …)`.
4. Runtime B fetches `manifest.json` from
   `nexus-agent-{tokenId}/`, verifies its hash matches `state_root`,
   replays the EventLog, resumes operation.

No off-chain coordination is needed — the chain is the
synchronisation point.

## Rationale

### Why ERC-8004 (not a custom NFT)?

ERC-8004 already standardises agent identity (tokenId, owner,
agentURI). Re-using it lets Nexus benefit from the existing
identity / reputation / validation primitives in the ERC-8004
ecosystem without forking. Our extension is *additive*: a separate
contract that takes `tokenId` as a foreign key.

### Why Greenfield (not IPFS / Arweave)?

* **Owner-keyed permissions.** IPFS pinning is public-by-default
  and depends on social pinning networks; Arweave is permanent and
  public. Greenfield offers the access-control primitives an agent
  needs (private memory, third-party audit grants).
* **Cost predictability.** Per-byte storage with explicit billing,
  unlike Arweave's one-time-payment-forever model that prices in
  long-tail uncertainty.
* **BNB-native.** No bridge, no separate token; users pay storage
  in BNB.

### Why anchor only `state_root` on chain (not full state)?

Cost and privacy. A 100 KB conversation history at 50 gwei costs
roughly $50–100 to write to BSC; storing only the 32-byte hash
costs cents. Privacy: full chat history on a public chain is
unacceptable for many use cases.

### Why DPM (and why must it be deterministic)?

If the projection function `π(events, task, budget) → context`
isn't deterministic, two runtimes replaying the same EventLog
produce different states, and `state_root` becomes meaningless. The
DPM contract is: same events + same projection → same hash.
Implementations MUST document any non-determinism (e.g. LLM calls
that produce summaries) and pin those as *new events* rather than
hidden mutations.

### Why ABC?

Without explicit behaviour bounds, an agent's drift over time is
invisible. ABC declares hard rules ("never call `transfer()` to an
unverified address") and soft rules ("respond in <2 paragraphs by
default"). The `DriftScore` over an observation window makes
violations a first-class signal — operators / users can detect
when an agent is misbehaving even before a hard rule fires.

### Why three IDs?

Layered identity reflects layered concerns:

* `wallet_address` is for **trust** — who owns the agent.
* `tokenId` is for **identity** — what the agent *is*, immutable.
* `agentId` is for **handles** — convenience for application code.

Conflating them (as some chain-native agent specs do) makes it
hard to support flows like account abstraction, social recovery,
or non-custodial vs. custodial runtimes.

## Backwards compatibility

* **ERC-8004:** unchanged. Nexus does not modify the Identity
  Registry contract; existing ERC-8004 deployments work as-is.
* **Greenfield:** uses standard Greenfield bucket / object APIs.
* **Pre-Nexus agents:** an existing ERC-8004 agent without a
  state extension simply has `getState(tokenId).stateRoot ==
  bytes32(0)`. Reading still works; writing is no-op until a
  runtime initialises the bucket and posts the first manifest.

## Reference implementation

Reference implementation, written in Python + C# + Solidity:

**Repository:** https://github.com/bnbchain/nexus

| Layer                | Package                | Description                                               |
| -------------------- | ---------------------- | --------------------------------------------------------- |
| SDK                  | `packages/sdk` (`nexus_core`) | DPM, ABC, Builder, AgentRuntime, BSCClient, GreenfieldClient, **anchor batch builder + canonicalisation + SHA-256 / keccak-256 hashing** (`nexus_core.anchor`). |
| Framework            | `packages/nexus` (`nexus`) | DigitalTwin runtime, Evolution (memory / skill / persona / knowledge), MCP-aware tool registry. |
| Server               | `packages/server` (`nexus_server`) | Multi-tenant FastAPI: passkey auth, LLM gateway, /agent/{state,timeline,memories,messages} read views, twin lifecycle. |
| Desktop client       | `packages/desktop`     | Avalonia C# thin client (Windows / macOS / Linux).        |
| Solidity             | (TBD — separate repo or `contracts/` folder) | `AgentStateExtension.sol`, `TaskStateManager.sol`. |

The `nexus_core.anchor` module mechanically reproduces the test
vectors in §"Test vectors" — see `packages/sdk/tests/test_anchor.py`,
which pins the canonical bytes and SHA-256 digests at the byte level.

The Python entry point is the four module-level functions:

```python
import nexus_core
rt = nexus_core.local()                       # zero config, file-backed
rt = nexus_core.testnet(private_key="0x...")  # BSC testnet + Greenfield
rt = nexus_core.mainnet(private_key="0x...")  # BSC mainnet + Greenfield
rt = nexus_core.builder().mock_backend().build()  # unit tests
```

Test coverage at time of this draft: 64 server / 192 nexus / 212
sdk regression cases all green.

## Security considerations

### Authorisation

* `updateStateRoot` is authorised only by NFT owner or the current
  `activeRuntime`. A compromised runtime cannot transfer agent
  ownership (only mutate state under the existing handoff).
* `setActiveRuntime` requires NFT-owner signature. A user can
  always evict a misbehaving runtime.
* `TaskStateManager.updateTask` follows the same dual-authority
  pattern.

### Replay / concurrent writes

* `TaskStateManager` uses an explicit `expectedVersion` counter →
  optimistic concurrency. Two runtimes racing to update the same
  task lose deterministically: the second submitter sees a version
  mismatch and must re-read.
* `AgentStateExtension` does **not** use a version counter; the
  authorisation check (single `activeRuntime` at a time) prevents
  the race. Operators that need finer-grained concurrency can
  layer optimistic concurrency in their manifest format.

### State integrity

* `state_root` is a content hash. A tampered Greenfield manifest
  fails verification at the next reader.
* The hash chain (`prev_root` field) lets verifiers walk history
  and detect any silent reorg.

### Greenfield-specific risks

* If the bucket owner accidentally revokes their own write
  permission, the agent freezes. Recovery requires the wallet to
  re-grant. We recommend runtimes refuse to start without a
  permission self-check.
* Greenfield outages translate to a write-stall, not data loss
  (events stay in the runtime's local EventLog until uploadable).

### LLM / tool risks

* The Nexus runtime does not constrain the LLM's outputs at the
  protocol layer — that is the ABC engine's job. ABC is
  *advisory* by default; deployments concerned with hard safety
  bounds (e.g. financial agents) should pin `intervention_threshold`
  to a low value and review every flagged turn before signing
  state updates on chain.

### Privacy

* Anything posted to Greenfield is visible to anyone the bucket
  owner has granted read. Users SHOULD assume that posting
  sensitive content into the EventLog persists it indefinitely
  (subject to Greenfield retention) and behave accordingly.
* Future work: optional encryption of EventLog entries at rest
  with a key derived from the wallet, so that even an audit-grant
  recipient sees only ciphertext until the user releases the key.

## Test vectors

Each implementation MUST pass these vectors. Hashes are lowercase
hex with no `0x` prefix unless noted.

### Vector 1 — empty manifest `state_root`

Manifest object (logical):

```json
{
  "events": [],
  "prev_root": "0x0000000000000000000000000000000000000000000000000000000000000000",
  "schema": "nexus.sync.batch.v1",
  "sync_ids": [],
  "user_id": "00000000-0000-0000-0000-000000000000"
}
```

JCS canonical bytes (one line, no whitespace, sorted keys):

```
{"events":[],"prev_root":"0x0000000000000000000000000000000000000000000000000000000000000000","schema":"nexus.sync.batch.v1","sync_ids":[],"user_id":"00000000-0000-0000-0000-000000000000"}
```

SHA-256 (`state_root`):

```
6b4346d5ddc5e95f816e45ff699289d27e30142f57262e4e3052670055d1957f
```

`merkleRoot` for empty manifest: `0x0000…0000` (no chunks).

### Vector 2 — single user_message event

Manifest object:

```json
{
  "events": [
    {
      "client_created_at": "2026-04-28T00:00:00Z",
      "event_type": "user_message",
      "content": "hello",
      "metadata": {},
      "session_id": "session_20260428",
      "sync_id": 1,
      "server_received_at": "2026-04-28T00:00:01Z"
    }
  ],
  "prev_root": "0x0000000000000000000000000000000000000000000000000000000000000000",
  "schema": "nexus.sync.batch.v1",
  "sync_ids": [1],
  "user_id": "00000000-0000-0000-0000-000000000001"
}
```

SHA-256 (`state_root`):

```
96d596adb771ffa3d019ce6fb741b58041db2d98eab66c6397afa2ff52e9a1e2
```

### Vector 3 — `AgentStateExtension.updateStateRoot`

* `tokenId = 864`
* `newRoot = 0x6b4346d5ddc5e95f816e45ff699289d27e30142f57262e4e3052670055d1957f` (Vector 1's hash)
* `newMerkleRoot = 0x0000000000000000000000000000000000000000000000000000000000000000`
* Caller is the NFT owner (or current `activeRuntime`).
* Expected event: `StateRootUpdated(864, newRoot, prevRoot, newMerkleRoot, msg.sender, block.timestamp)`.

### Vector 4 — NFT transfer auto-resets activeRuntime

1. Alice owns tokenId 864, sets `activeRuntime = 0xRT_ALICE`.
2. Alice transfers tokenId 864 to Bob via ERC-8004's transfer.
3. Bob (or anyone) calls a state-mutating function on `AgentStateExtension` for tokenId 864.
4. The `resetIfTransferred` modifier observes the owner change → emits `RuntimeResetOnTransfer(864, BOB, ALICE, 0xRT_ALICE)` and sets `activeRuntime = address(0)`.
5. Subsequent `updateStateRoot` calls from `0xRT_ALICE` MUST revert with `"AgentState: not authorised"`.

### Vector 5 — `TaskStateManager` optimistic concurrency

Two writers race to update the same `(taskId=0xdead…, tokenId=864)`
with `expectedVersion = 0`. The first tx mines, bumps version to 1.
The second tx reverts with `"TaskState: version mismatch"`.

### Vector 6 — manifest carrying an `evolution_proposal`

A manifest with one `evolution_proposal` event (MemoryEvolver
adding a fact about peanut allergy):

Manifest object:

```json
{
  "events": [{
    "client_created_at": "2026-04-28T12:34:56Z",
    "event_type": "evolution_proposal",
    "metadata": {
      "edit_id": "evo-2026-04-28-001-abc",
      "evolver": "MemoryEvolver",
      "target_namespace": "memory.facts",
      "target_version_pre": "memory/facts/v0041.json",
      "target_version_post": "memory/facts/v0042.json",
      "evidence_event_ids": [123, 145, 167],
      "change_summary": "Added fact: user has peanut allergy",
      "predicted_fixes": [{"task_kind": "restaurant_recommendation", "reason": "avoid peanut dishes"}],
      "predicted_regressions": [],
      "rollback_pointer": "memory/facts/v0041.json",
      "expires_after_events": 100
    },
    "sync_id": 4501,
    "session_id": "session_20260428",
    "server_received_at": "2026-04-28T12:34:57Z"
  }],
  "prev_root": "0x0000000000000000000000000000000000000000000000000000000000000000",
  "schema": "nexus.sync.batch.v1",
  "sync_ids": [4501],
  "user_id": "00000000-0000-0000-0000-000000000002"
}
```

SHA-256 (`state_root`):

```
4b3ff7c1e69bd1665afd6db45a72b9fcedd7455515c27dc580815ff5d63216b2
```

### Vector 7 — manifest carrying an `evolution_verdict` (`kept_with_warning`)

A manifest carrying the verdict for the proposal in Vector 6,
with one `unpredicted_regression` of `severity: low`:

Manifest object:

```json
{
  "events": [{
    "client_created_at": "2026-04-28T18:00:00Z",
    "event_type": "evolution_verdict",
    "metadata": {
      "edit_id": "evo-2026-04-28-001-abc",
      "verdict_at_event": 4837,
      "events_observed": 200,
      "predicted_fix_match": [{"task_kind": "restaurant_recommendation", "observed_count": 2, "outcome": "fixed"}],
      "predicted_fix_miss": [],
      "predicted_regression_match": [],
      "predicted_regression_miss": [],
      "unpredicted_regressions": [{"task_kind": "small_talk", "observed_count": 1, "severity": "low", "evidence": "over-mentioned"}],
      "fix_score": 1.0,
      "regression_score": 0.2,
      "abc_drift_delta": 0.05,
      "decision": "kept_with_warning"
    },
    "sync_id": 4838,
    "session_id": "session_20260428",
    "server_received_at": "2026-04-28T18:00:01Z"
  }],
  "prev_root": "0x0000000000000000000000000000000000000000000000000000000000000000",
  "schema": "nexus.sync.batch.v1",
  "sync_ids": [4838],
  "user_id": "00000000-0000-0000-0000-000000000002"
}
```

SHA-256 (`state_root`):

```
3fdd568de9f98f0c5d6b5ad9335bfa1cd1162fc00a7e2a8279171f1e83900601
```

A compliant runtime that:
1. Built this `evolution_proposal` event (Vector 6)
2. Observed 200 events
3. Computed the verdict per the rules in §3.4

…MUST emit an `evolution_verdict` event whose canonical form
matches Vector 7 byte-for-byte (modulo timestamps + sync_id).

## Open questions

1. **Cross-chain identity.** Should `tokenId` be portable across
   BSC mainnet / testnet / sidechains? Today it isn't —
   `agent_id_to_int` is keyed by tokenId only, not by chain id. A
   future amendment may add `(chainId, tokenId)` namespacing.
2. **Non-custodial mode.** The current reference server uses a
   single `SERVER_PRIVATE_KEY` to sign on behalf of all users
   (custodial). A user-key-per-twin mode (passkey + WalletConnect)
   is sketched in `ROADMAP.md` but not yet specified here.
3. **Manifest size cap.** A growing EventLog can balloon
   `manifest.json` size. The reference implementation paginates by
   uploading periodic compactions — but the BEP does not yet
   normative a max-size or pagination policy.
4. **Active-runtime deactivation grace period.** When NFT-owner
   calls `setActiveRuntime`, the previous runtime may have
   in-flight writes. Today there is no on-chain grace window — the
   eviction is immediate. A future amendment may add a 1–2 block
   pending state.

## Reference

* ERC-8004 Agent Identity Registry: https://eips.ethereum.org/EIPS/erc-8004
* BNB Greenfield documentation: https://docs.bnbchain.org/bnb-greenfield/
* Reference implementation: https://github.com/bnbchain/nexus
* Architecture overview: [`ARCHITECTURE.md`](../ARCHITECTURE.md)
* Conceptual primer: [`docs/concepts/dpm.md`](concepts/dpm.md), [`docs/concepts/abc.md`](concepts/abc.md), [`docs/concepts/identity.md`](concepts/identity.md)

## Copyright

Copyright and related rights waived via [CC0](https://creativecommons.org/publicdomain/zero/1.0/).
