"""Agent ID + Greenfield-bucket conventions for ERC-8004 agents.

* :func:`agent_id_to_int` converts a string agent_id to a deterministic
  uint256 for on-chain contract calls. Used by StateManager and
  ChainBackend.

* :func:`bucket_for_agent` is THE Greenfield bucket name for an agent:
  ``nexus-agent-{tokenId}``. Per-agent buckets are mandatory across the
  SDK — there is no shared-bucket fallback. Pass through everywhere
  ``GreenfieldClient`` / ``ChainBackend`` / ``DigitalTwin`` is built.
"""

import hashlib

BUCKET_PREFIX = "nexus-agent-"


def bucket_for_agent(token_id) -> str:
    """Greenfield bucket name for an ERC-8004 agent.

    Returns ``nexus-agent-{token_id}``. Greenfield's bucket naming rules
    (3-63 chars, lowercase letters/digits/dashes, can't be IP-shaped)
    are satisfied for any reasonable uint256 value.

    Example::
        >>> bucket_for_agent(864)
        'nexus-agent-864'

    The canonical and ONLY supported convention. There is no shared
    "nexus-agent-state" bucket — every agent is isolated.
    """
    if token_id is None:
        raise ValueError("bucket_for_agent: token_id must not be None")
    s = str(token_id).strip()
    if not s:
        raise ValueError("bucket_for_agent: token_id must not be empty")
    return f"{BUCKET_PREFIX}{s}"


def agent_id_to_int(agent_id: str) -> int:
    """Convert a string agent_id to a deterministic uint256.

    Priority: numeric string → hex string → SHA-256 hash.
    Always returns a positive integer suitable for uint256 contract params.
    """
    if not agent_id:
        return 0

    # Try as plain integer first
    try:
        return int(agent_id)
    except (ValueError, TypeError):
        pass

    # Try as hex
    if isinstance(agent_id, str) and agent_id.startswith("0x"):
        try:
            return int(agent_id, 16)
        except (ValueError, TypeError):
            pass

    # Fallback: deterministic hash
    return int.from_bytes(
        hashlib.sha256(agent_id.encode("utf-8")).digest(), "big"
    )
