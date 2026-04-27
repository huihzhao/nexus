"""
Agent Profile Manager — Profile generation and discovery.

Profiles are stored as Rune Artifacts (versioned, content-addressed).
Discovery uses tag-based matching — no LLM required for basic queries.

Storage:
    Profiles stored via ArtifactProvider as "profile.json"
    Discovery index stored via backend as agents/_discovery/index.json
"""

from __future__ import annotations

import time
from typing import Optional

from ..core.backend import StorageBackend
from ..core.models import AgentProfile
from ..core.providers import RuneArtifactProvider


class ProfileManager:
    """
    Manages agent profiles and provides discovery capabilities.

    Profiles are generated from agent metadata (memory categories,
    skills, persona keywords) and stored as versioned artifacts.
    Discovery is tag-based matching over the profile index.

    Usage:
        pm = ProfileManager(backend, artifacts)

        # Publish a profile
        await pm.publish(AgentProfile(
            agent_id="agent-a",
            interests=["japanese_cuisine", "blockchain"],
            capabilities=["travel_planning"],
        ))

        # Discover agents
        matches = await pm.discover(interests=["japanese_cuisine"])
    """

    def __init__(
        self,
        backend: StorageBackend,
        artifacts: RuneArtifactProvider,
    ):
        self._backend = backend
        self._artifacts = artifacts
        # In-memory discovery index
        self._profiles: dict[str, AgentProfile] = {}

    # ── Profile CRUD ───────────────────────────────────────────────

    async def publish(self, profile: AgentProfile) -> int:
        """
        Publish or update an agent's profile.

        Stores as a versioned artifact and updates the discovery index.

        Returns:
            Version number of the published profile.
        """
        import json

        profile.updated_at = time.time()

        # Compute profile hash
        profile_bytes = StorageBackend.json_bytes(profile.to_dict())
        profile.profile_hash = StorageBackend.content_hash(profile_bytes)

        # Store as artifact (versioned)
        version = await self._artifacts.save(
            filename="profile.json",
            data=json.dumps(profile.to_dict(), default=str).encode("utf-8"),
            agent_id=profile.agent_id,
            content_type="application/json",
            metadata={"type": "agent_profile"},
        )

        # Update in-memory index
        self._profiles[profile.agent_id] = profile

        # Persist discovery index
        await self._update_discovery_index(profile)

        # Anchor on chain
        await self._backend.anchor(
            profile.agent_id, profile.profile_hash, namespace="profile"
        )

        return version

    async def get_profile(self, agent_id: str) -> Optional[AgentProfile]:
        """Load an agent's latest profile."""
        # Check memory first
        if agent_id in self._profiles:
            return self._profiles[agent_id]

        # Try loading from artifact store
        import json
        artifact = await self._artifacts.load(
            "profile.json", agent_id=agent_id,
        )
        if artifact:
            data = json.loads(artifact.data.decode("utf-8"))
            profile = AgentProfile.from_dict(data)
            self._profiles[agent_id] = profile
            return profile

        return None

    # ── Discovery ──────────────────────────────────────────────────

    async def discover(
        self,
        interests: Optional[list[str]] = None,
        capabilities: Optional[list[str]] = None,
        style_tags: Optional[list[str]] = None,
        min_reputation: float = 0.0,
        exclude: Optional[list[str]] = None,
        limit: int = 20,
    ) -> list[AgentProfile]:
        """
        Discover agents by tag-based matching.

        Scores profiles by number of matching tags. All filter
        parameters are optional and additive.

        Args:
            interests: Match profiles with these interests.
            capabilities: Match profiles with these capabilities.
            style_tags: Match profiles with these style tags.
            min_reputation: Minimum avg_compatibility score.
            exclude: Agent IDs to exclude (e.g., self, existing connections).
            limit: Max results to return.

        Returns:
            Profiles ranked by match score (number of matching tags).
        """
        await self._load_discovery_index()

        exclude_set = set(exclude or [])
        scored: list[tuple[float, AgentProfile]] = []

        for agent_id, profile in self._profiles.items():
            if agent_id in exclude_set:
                continue

            # Check visibility
            if profile.visibility == "private":
                continue

            # Check reputation threshold
            rep = profile.reputation.get("avg_compatibility", 0.0)
            if rep < min_reputation:
                continue

            # Score by tag matches
            score = 0.0
            if interests:
                matches = set(interests) & set(profile.interests)
                score += len(matches) * 2  # interests weighted higher

            if capabilities:
                matches = set(capabilities) & set(profile.capabilities)
                score += len(matches) * 1.5

            if style_tags:
                matches = set(style_tags) & set(profile.style_tags)
                score += len(matches)

            if score > 0 or (not interests and not capabilities and not style_tags):
                scored.append((score, profile))

        # Sort by score (descending), then by reputation
        scored.sort(key=lambda x: (
            x[0],
            x[1].reputation.get("avg_compatibility", 0.0),
        ), reverse=True)

        return [profile for _, profile in scored[:limit]]

    async def random_discover(
        self,
        exclude: Optional[list[str]] = None,
        limit: int = 5,
    ) -> list[AgentProfile]:
        """
        Random discovery — serendipitous agent encounters.

        Returns random public profiles, excluding specified agents.
        """
        import random

        await self._load_discovery_index()
        exclude_set = set(exclude or [])

        candidates = [
            p for aid, p in self._profiles.items()
            if aid not in exclude_set and p.visibility != "private"
        ]

        if len(candidates) <= limit:
            return candidates

        return random.sample(candidates, limit)

    # ── Reputation ─────────────────────────────────────────────────

    async def update_reputation(
        self,
        agent_id: str,
        gossip_count: int,
        avg_compatibility: float,
        trust_percentile: int,
    ) -> None:
        """
        Update an agent's reputation stats (computed from impressions).

        Called after new impressions are formed.
        """
        profile = await self.get_profile(agent_id)
        if profile:
            profile.reputation = {
                "gossip_count": gossip_count,
                "avg_compatibility": round(avg_compatibility, 3),
                "trust_percentile": trust_percentile,
            }
            await self.publish(profile)

    # ── Internal ───────────────────────────────────────────────────

    async def _update_discovery_index(self, profile: AgentProfile) -> None:
        """Add or update a profile in the discovery index."""
        index_path = "agents/_discovery/index.json"
        index_data = await self._backend.load_json(index_path) or {"profiles": {}}

        index_data["profiles"][profile.agent_id] = {
            "interests": profile.interests,
            "capabilities": profile.capabilities,
            "style_tags": profile.style_tags,
            "reputation": profile.reputation,
            "visibility": profile.visibility,
            "gossip_policy": profile.gossip_policy,
            "updated_at": profile.updated_at,
        }
        index_data["updated_at"] = time.time()

        await self._backend.store_json(index_path, index_data)

    async def _load_discovery_index(self) -> None:
        """Load the discovery index from backend."""
        if self._profiles:
            return  # Already loaded

        index_path = "agents/_discovery/index.json"
        index_data = await self._backend.load_json(index_path)

        if index_data:
            for agent_id, entry in index_data.get("profiles", {}).items():
                if agent_id not in self._profiles:
                    self._profiles[agent_id] = AgentProfile(
                        agent_id=agent_id,
                        interests=entry.get("interests", []),
                        capabilities=entry.get("capabilities", []),
                        style_tags=entry.get("style_tags", []),
                        reputation=entry.get("reputation", {}),
                        visibility=entry.get("visibility", "public"),
                        gossip_policy=entry.get("gossip_policy", "open"),
                        updated_at=entry.get("updated_at", 0.0),
                    )
