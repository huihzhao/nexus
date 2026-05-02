"""Anchor batch builder + canonical hashing for ``nexus.sync.batch.v1``.

This module is the bridge between the off-chain EventLog and the
on-chain ``state_root`` posted via
:class:`AgentStateExtension.updateStateRoot`. It implements the
manifest schema specified in BEP-Nexus §3:

  * Build a manifest object (``build_anchor_batch``).
  * Serialise it to canonical bytes (``canonicalize``).
  * Hash it with SHA-256 → ``state_root`` (``state_root_hex`` /
    ``state_root_bytes``).
  * Optionally compute a keccak256 Merkle root over event chunks
    (``merkle_root_hex`` / ``merkle_root_bytes``).

The canonical-form implementation here is the JCS-compatible subset
that ``json.dumps(obj, sort_keys=True, separators=(",", ":"),
ensure_ascii=False)`` produces; this is byte-identical to RFC 8785
output for all manifest fields the schema currently allows
(integers, ASCII strings, nested objects/arrays, booleans, null).
For schemas that grow to include floats or non-ASCII strings that
require JCS-specific escaping, swap to a full JCS library — see
the ``_jcs_dumps`` hook.

Reference implementation. Conformance: BEP-Nexus draft 2026-04-28.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

# Optional keccak256 (Ethereum-flavour SHA-3) for the Merkle root.
# pyca/cryptography ships keccak under hashes; eth-hash provides a
# lighter wrapper. Try both; if neither is available we fall back to
# ``None`` and ``merkle_root_*`` returns the zero digest.
try:  # pragma: no cover — dependency presence not part of unit tests
    from Crypto.Hash import keccak as _keccak_mod  # pycryptodome

    def _keccak256(data: bytes) -> bytes:
        h = _keccak_mod.new(digest_bits=256)
        h.update(data)
        return h.digest()
except ImportError:  # pragma: no cover
    try:
        from eth_hash.auto import keccak as _keccak_fn  # type: ignore

        def _keccak256(data: bytes) -> bytes:
            return _keccak_fn(data)
    except ImportError:
        _keccak256 = None  # type: ignore


SCHEMA_V1 = "nexus.sync.batch.v1"

#: Length of a content-hash digest in bytes (SHA-256 / keccak256 both).
DIGEST_BYTES = 32

#: All-zero digest, used for the genesis ``prev_root`` and as the
#: sentinel ``merkleRoot`` value when no chunk Merkle proof is in use.
ZERO_DIGEST_HEX = "0x" + "00" * DIGEST_BYTES
ZERO_DIGEST_BYTES = b"\x00" * DIGEST_BYTES


# ── Public data shape ────────────────────────────────────────────────


@dataclass
class AnchorBatch:
    """Logical representation of a `nexus.sync.batch.v1` manifest.

    Build via :func:`build_anchor_batch`; serialise via
    :meth:`canonicalize`; hash via :meth:`state_root_hex`.
    """

    user_id: str
    events: list[dict] = field(default_factory=list)
    sync_ids: list[int] = field(default_factory=list)
    prev_root: str = ZERO_DIGEST_HEX
    schema: str = SCHEMA_V1

    # ── Serialisation ────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Strictly the BEP §3.1-mandated fields, in unsorted order.

        Field order doesn't matter for hashing — :func:`canonicalize`
        sorts keys — but keeping ``schema`` first when humans read
        the JSON helps debugging.
        """
        return {
            "schema": self.schema,
            "user_id": self.user_id,
            "events": list(self.events),
            "sync_ids": list(self.sync_ids),
            "prev_root": self.prev_root,
        }

    def canonicalize(self) -> bytes:
        """Return the JCS-compatible UTF-8 bytes for this manifest."""
        return _jcs_dumps(self.to_dict())

    # ── Hashing ──────────────────────────────────────────────────────

    def state_root_bytes(self) -> bytes:
        """SHA-256 over the canonical bytes — 32 raw bytes."""
        return hashlib.sha256(self.canonicalize()).digest()

    def state_root_hex(self, prefix: bool = True) -> str:
        """SHA-256 over the canonical bytes, lowercase hex.

        Args:
            prefix: If True (default) returns ``"0x"+hex`` — the
                form expected by Solidity ``bytes32``. If False
                returns bare hex (for storage / display).
        """
        digest = self.state_root_bytes().hex()
        return f"0x{digest}" if prefix else digest

    def merkle_root_bytes(
        self, chunk_hashes: Optional[Iterable[bytes]] = None
    ) -> bytes:
        """keccak256 Merkle root over event-chunk hashes.

        Optional second hash — see BEP §3.2. When ``chunk_hashes`` is
        empty/None or keccak isn't available, returns the zero digest
        (the sentinel meaning "no Merkle proof committed").

        The Merkle tree is the standard balanced binary tree with
        ``keccak256(left || right)`` parent pairs; an odd leaf at any
        level is duplicated (the OpenZeppelin convention).
        """
        if _keccak256 is None or not chunk_hashes:
            return ZERO_DIGEST_BYTES

        nodes = [bytes(h) for h in chunk_hashes]
        if not nodes:
            return ZERO_DIGEST_BYTES
        for n in nodes:
            if len(n) != DIGEST_BYTES:
                raise ValueError(
                    f"chunk hashes must be {DIGEST_BYTES}-byte digests, "
                    f"got {len(n)}-byte value"
                )

        while len(nodes) > 1:
            if len(nodes) % 2 == 1:
                nodes.append(nodes[-1])
            nodes = [
                _keccak256(nodes[i] + nodes[i + 1])
                for i in range(0, len(nodes), 2)
            ]
        return nodes[0]

    def merkle_root_hex(
        self,
        chunk_hashes: Optional[Iterable[bytes]] = None,
        prefix: bool = True,
    ) -> str:
        """keccak256 Merkle root, hex form (see :meth:`state_root_hex`)."""
        digest = self.merkle_root_bytes(chunk_hashes).hex()
        return f"0x{digest}" if prefix else digest


# ── Public factory ───────────────────────────────────────────────────


def build_anchor_batch(
    user_id: str,
    events: Iterable[dict],
    *,
    prev_root: str = ZERO_DIGEST_HEX,
) -> AnchorBatch:
    """Build a :class:`AnchorBatch` from a flat list of events.

    Args:
        user_id: UUID string identifying the agent's user/owner.
        events: Iterable of event dicts conforming to BEP §3.1
            event schema. Order is preserved.
        prev_root: Hex string of the previous on-chain ``state_root``,
            forming a hash chain. Use :data:`ZERO_DIGEST_HEX` for
            genesis.

    Returns:
        An :class:`AnchorBatch` ready to canonicalise + hash.

    Notes:
        ``sync_ids`` is auto-derived from each event's ``sync_id``
        field (events without one are silently skipped from the
        index — they're still in ``events`` for replay).
    """
    evt_list = [dict(e) for e in events]
    sync_ids = [
        e["sync_id"] for e in evt_list
        if isinstance(e.get("sync_id"), int)
    ]
    return AnchorBatch(
        user_id=user_id,
        events=evt_list,
        sync_ids=sync_ids,
        prev_root=prev_root,
    )


def canonicalize(obj: Any) -> bytes:
    """Public alias of :func:`_jcs_dumps` for callers that want to
    canonicalise something other than a full :class:`AnchorBatch`."""
    return _jcs_dumps(obj)


# ── Internals ────────────────────────────────────────────────────────


def _jcs_dumps(obj: Any) -> bytes:
    """JCS-compatible JSON serialisation.

    Implementation note: ``json.dumps`` with the right flags is
    byte-identical to RFC 8785 for the subset of JSON the BEP §3.1
    schema allows (integers, ASCII strings, nested
    objects/arrays, booleans, null). If the schema grows to include
    floats or non-ASCII strings with JCS-specific escapes, replace
    this body with a call to a full JCS library — the public API
    of this module won't change.
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


__all__ = [
    "AnchorBatch",
    "SCHEMA_V1",
    "ZERO_DIGEST_HEX",
    "ZERO_DIGEST_BYTES",
    "DIGEST_BYTES",
    "build_anchor_batch",
    "canonicalize",
]
