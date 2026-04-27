# `chain/` — BSC + ERC-8004 + legacy anchor reads

What's in here:

| File | Purpose |
|---|---|
| `routes.py` | Facade for `/api/v1/chain/*` endpoints — `/me`, `/agent/{id}`, deprecated `/register-agent`. Real code at `rune_server.chain_proxy`. |
| `legacy_anchors.py` | Facade for the legacy `sync_anchor` module — `enqueue_anchor` + `list_anchors_for_user` only (Phase B trimmed the retry daemon). Read-only view of pre-S4 anchor history. Real code at `rune_server.sync_anchor`. |
| `__init__.py` | Re-exports the router + `legacy_anchors` submodule. |

What the new dev needs to know:

- The **real** chain identity bootstrap is `bootstrap_chain_identity` in `rune_server.twin_manager` — Phase D will move it here as `chain/bootstrap.py`. The `/register-agent` endpoint is deprecated and just delegates to that helper; twin auto-bootstraps on first chat after S6.
- `legacy_anchors.list_anchors_for_user` is what `/sync/anchors` and `/agent/state.last_anchor` read from. New anchors come from twin's ChainBackend, not from this table.
- BSC web3 logic itself lives in the SDK (`nexus_core.chain.client`) — server uses it via `RuneChainClient`.

What's NOT in here:

- Twin's chain mode setup — that's `twins/manager.py` (calls into SDK).
- Bug 3's `twin_chain_events` capture — that's in `twins/manager.py` (chain log handler).
