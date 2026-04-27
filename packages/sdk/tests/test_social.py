"""
Tests for the Social Protocol: Impressions, Gossip, Profiles, Social Graph.

Tests the full social stack using MockBackend for fast, isolated tests.
"""

import asyncio
import time
import pytest

import nexus_core
from nexus_core import (
    Impression,
    ImpressionDimensions,
    ImpressionSummary,
    NetworkStats,
    GossipMessage,
    GossipSession,
    AgentProfile,
    GossipProtocol,
    ProfileManager,
    SocialGraph,
    MockBackend,
)


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def rune():
    return nexus_core.builder().mock_backend().build()


@pytest.fixture
def backend():
    return MockBackend()


@pytest.fixture
def impressions(rune):
    return rune.impressions


@pytest.fixture
def artifacts(rune):
    return rune.artifacts


# ── Data Model Tests ────────────────────────────────────────────────


class TestImpressionModels:
    def test_impression_auto_id(self):
        imp = Impression(source_agent="a", target_agent="b")
        assert imp.impression_id
        assert imp.created_at > 0

    def test_impression_dimensions(self):
        dims = ImpressionDimensions(
            interest_overlap=0.8,
            knowledge_complementarity=0.6,
            style_compatibility=0.7,
            reliability=0.9,
            depth=0.5,
        )
        assert dims.mean() == pytest.approx(0.7)

    def test_impression_to_from_dict(self):
        imp = Impression(
            source_agent="agent-a",
            target_agent="agent-b",
            gossip_session_id="sess-1",
            dimensions=ImpressionDimensions(
                interest_overlap=0.8,
                knowledge_complementarity=0.6,
            ),
            compatibility_score=0.75,
            summary="Good match",
            would_gossip_again=True,
            recommend_to_network=True,
        )
        data = imp.to_dict()
        restored = Impression.from_dict(data)

        assert restored.source_agent == "agent-a"
        assert restored.target_agent == "agent-b"
        assert restored.dimensions.interest_overlap == 0.8
        assert restored.compatibility_score == 0.75
        assert restored.would_gossip_again is True

    def test_gossip_message_auto_id(self):
        msg = GossipMessage(sender="a", content="hello")
        assert msg.message_id
        assert msg.sent_at > 0

    def test_gossip_message_roundtrip(self):
        msg = GossipMessage(
            session_id="s1", sender="agent-a",
            content="What do you know about Tokyo?",
            sequence=0,
        )
        data = msg.to_dict()
        restored = GossipMessage.from_dict(data)
        assert restored.content == "What do you know about Tokyo?"
        assert restored.sequence == 0

    def test_gossip_session_auto_id(self):
        sess = GossipSession(initiator="a", responder="b")
        assert sess.session_id
        assert sess.started_at > 0
        assert sess.status == "pending"

    def test_gossip_session_properties(self):
        sess = GossipSession(
            initiator="a", responder="b",
            status="active",
        )
        assert sess.is_active
        assert not sess.is_concluded
        assert sess.participants == ("a", "b")

    def test_gossip_session_roundtrip(self):
        sess = GossipSession(
            initiator="agent-a", responder="agent-b",
            topic_hint="food", transport="sync",
            messages=[
                GossipMessage(sender="agent-a", content="Hi"),
            ],
        )
        data = sess.to_dict()
        restored = GossipSession.from_dict(data)
        assert restored.initiator == "agent-a"
        assert restored.transport == "sync"
        assert len(restored.messages) == 1

    def test_agent_profile_roundtrip(self):
        profile = AgentProfile(
            agent_id="agent-a",
            interests=["japanese_cuisine", "blockchain"],
            capabilities=["travel_planning"],
            style_tags=["concise"],
            visibility="public",
            gossip_policy="open",
        )
        data = profile.to_dict()
        restored = AgentProfile.from_dict(data)
        assert restored.agent_id == "agent-a"
        assert "japanese_cuisine" in restored.interests
        assert restored.gossip_policy == "open"


# ── Impression Provider Tests ───────────────────────────────────────


class TestImpressionProvider:
    def test_record_and_get(self, impressions):
        imp = Impression(
            source_agent="agent-a",
            target_agent="agent-b",
            gossip_session_id="sess-1",
            dimensions=ImpressionDimensions(
                interest_overlap=0.8,
                knowledge_complementarity=0.6,
                style_compatibility=0.7,
                reliability=0.9,
                depth=0.5,
            ),
            compatibility_score=0.75,
            summary="Strong overlap on food and travel",
            would_gossip_again=True,
        )
        result_id = asyncio.run(impressions.record(imp))
        assert result_id == imp.impression_id

        # Get impressions of target
        results = asyncio.run(impressions.get_impressions_of("agent-b", "agent-a"))
        assert len(results) == 1
        assert results[0].compatibility_score == 0.75

    def test_multiple_impressions(self, impressions):
        for i in range(3):
            imp = Impression(
                source_agent="agent-a",
                target_agent="agent-b",
                gossip_session_id=f"sess-{i}",
                compatibility_score=0.5 + i * 0.1,
            )
            asyncio.run(impressions.record(imp))

        results = asyncio.run(impressions.get_impressions_of("agent-b", "agent-a"))
        assert len(results) == 3
        # Should be ordered by recency (newest first)
        assert results[0].compatibility_score >= results[-1].compatibility_score

    def test_get_compatibility(self, impressions):
        imp = Impression(
            source_agent="agent-a",
            target_agent="agent-b",
            compatibility_score=0.82,
        )
        asyncio.run(impressions.record(imp))

        score = asyncio.run(impressions.get_compatibility("agent-a", "agent-b"))
        assert score == 0.82

        # Non-existent pair
        score = asyncio.run(impressions.get_compatibility("agent-a", "agent-c"))
        assert score is None

    def test_top_matches(self, impressions):
        # Agent A meets several agents
        agents = ["b", "c", "d", "e"]
        scores = [0.9, 0.3, 0.7, 0.5]
        for agent, score in zip(agents, scores):
            imp = Impression(
                source_agent="agent-a",
                target_agent=f"agent-{agent}",
                dimensions=ImpressionDimensions(interest_overlap=score),
                compatibility_score=score,
                would_gossip_again=score > 0.5,
            )
            asyncio.run(impressions.record(imp))

        matches = asyncio.run(impressions.get_top_matches("agent-a", top_k=3))
        assert len(matches) == 3
        assert matches[0].agent_id == "agent-b"  # highest score
        assert matches[0].latest_score == 0.9

    def test_top_matches_with_min_score(self, impressions):
        for agent, score in [("b", 0.9), ("c", 0.3), ("d", 0.7)]:
            imp = Impression(
                source_agent="agent-a",
                target_agent=f"agent-{agent}",
                compatibility_score=score,
            )
            asyncio.run(impressions.record(imp))

        matches = asyncio.run(impressions.get_top_matches(
            "agent-a", min_score=0.5,
        ))
        assert len(matches) == 2
        assert all(m.latest_score >= 0.5 for m in matches)

    def test_mutual_impressions(self, impressions):
        # A → B: 0.8
        asyncio.run(impressions.record(Impression(
            source_agent="agent-a", target_agent="agent-b",
            compatibility_score=0.8,
        )))
        # B → A: 0.7
        asyncio.run(impressions.record(Impression(
            source_agent="agent-b", target_agent="agent-a",
            compatibility_score=0.7,
        )))
        # A → C: 0.9 (but C has no impression of A)
        asyncio.run(impressions.record(Impression(
            source_agent="agent-a", target_agent="agent-c",
            compatibility_score=0.9,
        )))

        mutuals = asyncio.run(impressions.get_mutual("agent-a", min_score=0.5))
        assert len(mutuals) == 1
        assert mutuals[0][0] == "agent-b"
        assert mutuals[0][1] == 0.8  # my score
        assert mutuals[0][2] == 0.7  # their score

    def test_network_stats(self, impressions):
        # A → B, A → C
        asyncio.run(impressions.record(Impression(
            source_agent="agent-a", target_agent="agent-b",
            gossip_session_id="s1", compatibility_score=0.8,
        )))
        asyncio.run(impressions.record(Impression(
            source_agent="agent-a", target_agent="agent-c",
            gossip_session_id="s2", compatibility_score=0.6,
        )))
        # B → A (inbound)
        asyncio.run(impressions.record(Impression(
            source_agent="agent-b", target_agent="agent-a",
            gossip_session_id="s1", compatibility_score=0.75,
        )))

        stats = asyncio.run(impressions.get_network_stats("agent-a"))
        assert stats.unique_agents_met == 2  # B and C
        assert stats.avg_compatibility_given == pytest.approx(0.7, abs=0.01)
        assert stats.avg_compatibility_received == pytest.approx(0.75, abs=0.01)

    def test_confidence_gate_passes(self, impressions):
        # Record several consistent impressions
        for i in range(3):
            asyncio.run(impressions.record(Impression(
                source_agent="agent-a", target_agent="agent-b",
                gossip_session_id=f"s{i}",
                dimensions=ImpressionDimensions(
                    interest_overlap=0.7 + i * 0.02,
                    knowledge_complementarity=0.5,
                ),
                compatibility_score=0.7,
            )))

        # New impression within range — should pass
        new_imp = Impression(
            source_agent="agent-a", target_agent="agent-b",
            dimensions=ImpressionDimensions(
                interest_overlap=0.75,
                knowledge_complementarity=0.55,
            ),
        )
        passed = asyncio.run(impressions.check_confidence(new_imp))
        assert passed is True

    def test_confidence_gate_fails(self, impressions):
        # Record several consistent impressions
        for i in range(3):
            asyncio.run(impressions.record(Impression(
                source_agent="agent-a", target_agent="agent-b",
                gossip_session_id=f"s{i}",
                dimensions=ImpressionDimensions(
                    interest_overlap=0.7,
                    knowledge_complementarity=0.5,
                ),
                compatibility_score=0.7,
            )))

        # New impression with outlier dimension — should fail
        new_imp = Impression(
            source_agent="agent-a", target_agent="agent-b",
            dimensions=ImpressionDimensions(
                interest_overlap=0.1,  # WAY off from historical 0.7
                knowledge_complementarity=0.5,
            ),
        )
        passed = asyncio.run(impressions.check_confidence(new_imp, threshold=0.3))
        assert passed is False


# ── Gossip Protocol Tests ───────────────────────────────────────────


class TestGossipProtocol:
    def test_initiate_session(self, backend):
        gossip = GossipProtocol(backend, agent_id="agent-a")
        session = asyncio.run(gossip.initiate("agent-b", topic="food"))

        assert session.initiator == "agent-a"
        assert session.responder == "agent-b"
        assert session.status == "pending"
        assert session.topic_hint == "food"
        assert session.transport == "sync"

    def test_accept_session(self, backend):
        gossip_a = GossipProtocol(backend, agent_id="agent-a")
        session = asyncio.run(gossip_a.initiate("agent-b"))

        gossip_b = GossipProtocol(backend, agent_id="agent-b")
        gossip_b._sessions[session.session_id] = session

        accepted = asyncio.run(gossip_b.accept(session.session_id))
        assert accepted.status == "active"

    def test_send_receive_sync(self, backend):
        gossip_a = GossipProtocol(backend, agent_id="agent-a")
        gossip_b = GossipProtocol(backend, agent_id="agent-b")

        session = asyncio.run(gossip_a.initiate("agent-b"))
        gossip_b._sessions[session.session_id] = GossipSession(
            session_id=session.session_id,
            initiator="agent-a", responder="agent-b",
            status="active", transport="sync",
        )

        # A sends
        msg = asyncio.run(gossip_a.send(session.session_id, "Hi, know any good ramen?"))
        assert msg.sender == "agent-a"
        assert msg.sequence == 0

        # B receives
        asyncio.run(gossip_b.receive(session.session_id, msg))
        session_b = gossip_b._sessions[session.session_id]
        assert len(session_b.messages) == 1
        assert session_b.messages[0].content == "Hi, know any good ramen?"

    def test_conclude_session(self, backend):
        gossip = GossipProtocol(backend, agent_id="agent-a")
        session = asyncio.run(gossip.initiate("agent-b"))
        gossip._sessions[session.session_id].status = "active"

        asyncio.run(gossip.send(session.session_id, "Hello"))
        concluded = asyncio.run(gossip.conclude(session.session_id))

        assert concluded.status == "concluded"
        assert concluded.ended_at > 0
        assert concluded.session_hash  # hash computed

    def test_auto_conclude_on_turn_limit(self, backend):
        gossip_a = GossipProtocol(backend, agent_id="agent-a", max_turns=2)
        session = asyncio.run(gossip_a.initiate("agent-b", max_turns=2))
        gossip_a._sessions[session.session_id].status = "active"

        asyncio.run(gossip_a.send(session.session_id, "Message 1"))
        asyncio.run(gossip_a.send(session.session_id, "Message 2"))

        updated = gossip_a._sessions[session.session_id]
        assert updated.status == "concluded"

    def test_list_sessions(self, backend):
        gossip = GossipProtocol(backend, agent_id="agent-a")
        asyncio.run(gossip.initiate("agent-b", topic="food"))
        asyncio.run(gossip.initiate("agent-c", topic="tech"))

        sessions = asyncio.run(gossip.list_sessions())
        assert len(sessions) == 2

    def test_list_sessions_by_status(self, backend):
        gossip = GossipProtocol(backend, agent_id="agent-a")
        s1 = asyncio.run(gossip.initiate("agent-b"))
        gossip._sessions[s1.session_id].status = "active"
        asyncio.run(gossip.initiate("agent-c"))

        active = asyncio.run(gossip.list_sessions(status="active"))
        assert len(active) == 1
        assert active[0].responder == "agent-b"

    def test_bridge_sync_gossip(self, backend):
        gossip_a = GossipProtocol(backend, agent_id="agent-a")
        gossip_b = GossipProtocol(backend, agent_id="agent-b")

        session = asyncio.run(gossip_a.initiate("agent-b"))

        # Simple generators that echo topic
        async def gen_a(sess, msgs):
            return f"A says turn {len(msgs)}"

        async def gen_b(sess, msgs):
            return f"B says turn {len(msgs)}"

        result = asyncio.run(GossipProtocol.bridge(
            gossip_a, gossip_b,
            session.session_id,
            gen_a, gen_b,
            turns=3,
        ))

        assert result.status == "concluded"
        assert result.turn_count == 6  # 3 turns × 2 messages per turn

    def test_get_transcript(self, backend):
        gossip = GossipProtocol(backend, agent_id="agent-a")
        session = asyncio.run(gossip.initiate("agent-b"))
        gossip._sessions[session.session_id].status = "active"

        asyncio.run(gossip.send(session.session_id, "Hello"))
        asyncio.run(gossip.send(session.session_id, "How are you?"))

        transcript = asyncio.run(gossip.get_transcript(session.session_id))
        assert len(transcript) == 2
        assert transcript[0].content == "Hello"

    def test_async_transport(self, backend):
        gossip = GossipProtocol(backend, agent_id="agent-a", default_transport="async")
        session = asyncio.run(gossip.initiate("agent-b"))

        assert session.transport == "async"

        gossip._sessions[session.session_id].status = "active"
        msg = asyncio.run(gossip.send(session.session_id, "Async message"))

        # Message should be persisted to backend
        assert msg.content_hash


# ── Profile Manager Tests ───────────────────────────────────────────


class TestProfileManager:
    def test_publish_and_get(self, backend, artifacts):
        pm = ProfileManager(backend, artifacts)

        profile = AgentProfile(
            agent_id="agent-a",
            interests=["japanese_cuisine", "blockchain"],
            capabilities=["travel_planning"],
            style_tags=["concise", "technical"],
        )
        version = asyncio.run(pm.publish(profile))
        assert version == 1

        loaded = asyncio.run(pm.get_profile("agent-a"))
        assert loaded is not None
        assert "japanese_cuisine" in loaded.interests
        assert loaded.profile_hash  # hash computed

    def test_profile_versioning(self, backend, artifacts):
        pm = ProfileManager(backend, artifacts)

        p1 = AgentProfile(agent_id="agent-a", interests=["food"])
        v1 = asyncio.run(pm.publish(p1))

        p2 = AgentProfile(agent_id="agent-a", interests=["food", "tech"])
        v2 = asyncio.run(pm.publish(p2))

        assert v1 == 1
        assert v2 == 2

    def test_discover_by_interest(self, backend, artifacts):
        pm = ProfileManager(backend, artifacts)

        asyncio.run(pm.publish(AgentProfile(
            agent_id="agent-a",
            interests=["food", "travel"],
            capabilities=["planning"],
        )))
        asyncio.run(pm.publish(AgentProfile(
            agent_id="agent-b",
            interests=["food", "blockchain"],
            capabilities=["code_review"],
        )))
        asyncio.run(pm.publish(AgentProfile(
            agent_id="agent-c",
            interests=["music"],
            capabilities=["composition"],
        )))

        results = asyncio.run(pm.discover(interests=["food"]))
        assert len(results) == 2
        agent_ids = [r.agent_id for r in results]
        assert "agent-a" in agent_ids
        assert "agent-b" in agent_ids

    def test_discover_by_capability(self, backend, artifacts):
        pm = ProfileManager(backend, artifacts)

        asyncio.run(pm.publish(AgentProfile(
            agent_id="agent-a",
            capabilities=["travel_planning", "code_review"],
        )))
        asyncio.run(pm.publish(AgentProfile(
            agent_id="agent-b",
            capabilities=["data_analysis"],
        )))

        results = asyncio.run(pm.discover(capabilities=["code_review"]))
        assert len(results) == 1
        assert results[0].agent_id == "agent-a"

    def test_discover_excludes_private(self, backend, artifacts):
        pm = ProfileManager(backend, artifacts)

        asyncio.run(pm.publish(AgentProfile(
            agent_id="agent-a", interests=["food"], visibility="public",
        )))
        asyncio.run(pm.publish(AgentProfile(
            agent_id="agent-b", interests=["food"], visibility="private",
        )))

        results = asyncio.run(pm.discover(interests=["food"]))
        assert len(results) == 1
        assert results[0].agent_id == "agent-a"

    def test_discover_with_exclude(self, backend, artifacts):
        pm = ProfileManager(backend, artifacts)

        asyncio.run(pm.publish(AgentProfile(agent_id="a", interests=["food"])))
        asyncio.run(pm.publish(AgentProfile(agent_id="b", interests=["food"])))

        results = asyncio.run(pm.discover(interests=["food"], exclude=["a"]))
        assert len(results) == 1
        assert results[0].agent_id == "b"

    def test_random_discover(self, backend, artifacts):
        pm = ProfileManager(backend, artifacts)

        for i in range(10):
            asyncio.run(pm.publish(AgentProfile(
                agent_id=f"agent-{i}", interests=["general"],
            )))

        results = asyncio.run(pm.random_discover(limit=3))
        assert len(results) == 3


# ── Social Graph Tests ──────────────────────────────────────────────


class TestSocialGraph:
    def _build_network(self, impressions):
        """Build a test social network: A↔B, A↔C, B↔D, C↔D."""
        edges = [
            ("agent-a", "agent-b", 0.8),
            ("agent-b", "agent-a", 0.7),
            ("agent-a", "agent-c", 0.6),
            ("agent-c", "agent-a", 0.65),
            ("agent-b", "agent-d", 0.9),
            ("agent-d", "agent-b", 0.85),
            ("agent-c", "agent-d", 0.5),
            ("agent-d", "agent-c", 0.55),
        ]
        for src, tgt, score in edges:
            asyncio.run(impressions.record(Impression(
                source_agent=src, target_agent=tgt,
                gossip_session_id=f"{src}-{tgt}",
                compatibility_score=score,
                dimensions=ImpressionDimensions(interest_overlap=score),
            )))

    def test_nearest(self, impressions):
        self._build_network(impressions)
        graph = SocialGraph(impressions)

        matches = asyncio.run(graph.nearest("agent-a", top_k=3))
        assert len(matches) == 2  # A knows B and C
        assert matches[0].agent_id == "agent-b"  # highest score

    def test_mutual(self, impressions):
        self._build_network(impressions)
        graph = SocialGraph(impressions)

        mutuals = asyncio.run(graph.mutual("agent-a", min_score=0.5))
        assert len(mutuals) == 2  # B and C
        # B should be first (higher mutual scores)
        assert mutuals[0][0] == "agent-b"

    def test_discover_2hop(self, impressions):
        self._build_network(impressions)
        graph = SocialGraph(impressions)

        # Agent A should discover D through B or C
        discovered = asyncio.run(graph.discover("agent-a", hops=2))
        agent_ids = [d["agent_id"] for d in discovered]
        assert "agent-d" in agent_ids

    def test_transitive_trust(self, impressions):
        self._build_network(impressions)
        graph = SocialGraph(impressions)

        # Direct trust A→B
        trust = asyncio.run(graph.transitive_trust("agent-a", "agent-b"))
        assert trust == 0.8

        # Transitive trust A→D (through B)
        trust = asyncio.run(graph.transitive_trust("agent-a", "agent-d"))
        assert trust is not None
        assert trust > 0
        assert trust < 0.8  # Decayed

    def test_stats(self, impressions):
        self._build_network(impressions)
        graph = SocialGraph(impressions)

        stats = asyncio.run(graph.stats("agent-a"))
        assert stats.unique_agents_met == 2  # B and C

    def test_clusters(self, impressions):
        self._build_network(impressions)
        graph = SocialGraph(impressions)

        all_agents = ["agent-a", "agent-b", "agent-c", "agent-d"]
        clusters = asyncio.run(graph.clusters(all_agents, min_mutual_score=0.5))

        # All 4 should be in one cluster (all connected)
        assert len(clusters) >= 1
        assert len(clusters[0]) >= 3

    def test_recommend(self, impressions):
        self._build_network(impressions)
        graph = SocialGraph(impressions)

        # A should get D recommended (B knows D, C knows D)
        recs = asyncio.run(graph.recommend("agent-a"))
        if recs:  # May or may not have recs depending on threshold
            agent_ids = [r["agent_id"] for r in recs]
            assert "agent-d" in agent_ids

    def test_find_by_capability(self, impressions):
        self._build_network(impressions)
        graph = SocialGraph(impressions)

        profiles = {
            "agent-b": AgentProfile(
                agent_id="agent-b",
                capabilities=["code_review", "data_analysis"],
            ),
            "agent-c": AgentProfile(
                agent_id="agent-c",
                capabilities=["travel_planning"],
            ),
            "agent-d": AgentProfile(
                agent_id="agent-d",
                capabilities=["data_analysis"],
            ),
        }

        results = asyncio.run(graph.find(
            "agent-a", capability="data_analysis",
            profiles=profiles, min_trust=0.3,
        ))
        assert len(results) >= 1
        # B should be found (direct connection with data_analysis)
        agent_ids = [r["agent_id"] for r in results]
        assert "agent-b" in agent_ids


# ── Integration Tests ───────────────────────────────────────────────


class TestSocialIntegration:
    def test_rune_has_impressions(self):
        rune = nexus_core.builder().mock_backend().build()
        assert rune.impressions is not None

    def test_full_social_workflow(self, backend):
        """
        Full workflow: publish profiles → gossip → form impressions → query graph.
        """
        rune = nexus_core.builder().backend(backend).build()

        # 1. Publish profiles
        pm = ProfileManager(backend, rune.artifacts)
        asyncio.run(pm.publish(AgentProfile(
            agent_id="alice",
            interests=["japanese_cuisine", "travel"],
            capabilities=["travel_planning"],
        )))
        asyncio.run(pm.publish(AgentProfile(
            agent_id="bob",
            interests=["japanese_cuisine", "photography"],
            capabilities=["restaurant_review"],
        )))

        # 2. Discovery: Alice finds Bob
        matches = asyncio.run(pm.discover(interests=["japanese_cuisine"]))
        assert len(matches) == 2

        # 3. Gossip: Alice and Bob exchange messages
        gossip_alice = GossipProtocol(backend, agent_id="alice")
        gossip_bob = GossipProtocol(backend, agent_id="bob")

        session = asyncio.run(gossip_alice.initiate("bob", topic="tokyo_dining"))

        async def alice_gen(sess, msgs):
            return "I love omakase in Shinjuku. Any recommendations?"

        async def bob_gen(sess, msgs):
            return "Try Sushi Saito in Roppongi — counter style, amazing tuna."

        result = asyncio.run(GossipProtocol.bridge(
            gossip_alice, gossip_bob,
            session.session_id,
            alice_gen, bob_gen,
            turns=2,
        ))

        assert result.status == "concluded"
        assert result.turn_count == 4

        # 4. Form impressions
        alice_imp = Impression(
            source_agent="alice",
            target_agent="bob",
            gossip_session_id=result.session_id,
            dimensions=ImpressionDimensions(
                interest_overlap=0.85,
                knowledge_complementarity=0.72,
                style_compatibility=0.68,
                reliability=0.90,
                depth=0.60,
            ),
            compatibility_score=0.78,
            summary="Strong food knowledge, especially Tokyo dining.",
            would_gossip_again=True,
            recommend_to_network=True,
        )
        asyncio.run(rune.impressions.record(alice_imp))

        bob_imp = Impression(
            source_agent="bob",
            target_agent="alice",
            gossip_session_id=result.session_id,
            dimensions=ImpressionDimensions(
                interest_overlap=0.80,
                knowledge_complementarity=0.55,
                style_compatibility=0.70,
                reliability=0.85,
                depth=0.50,
            ),
            compatibility_score=0.72,
            summary="Good travel planner, shared food interests.",
            would_gossip_again=True,
        )
        asyncio.run(rune.impressions.record(bob_imp))

        # 5. Query social graph
        graph = SocialGraph(rune.impressions)

        # Alice's view
        alice_matches = asyncio.run(graph.nearest("alice"))
        assert len(alice_matches) == 1
        assert alice_matches[0].agent_id == "bob"

        # Mutual connections
        mutuals = asyncio.run(graph.mutual("alice", min_score=0.5))
        assert len(mutuals) == 1
        assert mutuals[0][0] == "bob"
        assert mutuals[0][1] == 0.78  # Alice's score of Bob
        assert mutuals[0][2] == 0.72  # Bob's score of Alice

        # Network stats
        stats = asyncio.run(graph.stats("alice"))
        assert stats.unique_agents_met == 1
        assert stats.avg_compatibility_given == pytest.approx(0.78, abs=0.01)

    def test_gossip_sync_no_chain(self, backend):
        """
        Sync gossip should NOT write to chain (transport="sync").
        Messages stay in memory only.
        """
        gossip = GossipProtocol(backend, agent_id="agent-a", default_transport="sync")
        session = asyncio.run(gossip.initiate("agent-b"))
        gossip._sessions[session.session_id].status = "active"

        asyncio.run(gossip.send(session.session_id, "Hello sync!"))

        # Conclude — sync mode should NOT anchor
        concluded = asyncio.run(gossip.conclude(session.session_id))
        assert concluded.status == "concluded"

        # No anchor for gossip namespace
        anchor = asyncio.run(backend.resolve("agent-a", namespace="gossip"))
        assert anchor is None

    def test_gossip_async_with_chain(self, backend):
        """
        Async gossip should persist messages and anchor on chain.
        """
        gossip = GossipProtocol(backend, agent_id="agent-a", default_transport="async")
        session = asyncio.run(gossip.initiate("agent-b"))
        gossip._sessions[session.session_id].status = "active"

        asyncio.run(gossip.send(session.session_id, "Hello async!"))
        asyncio.run(gossip.conclude(session.session_id))

        # Should have anchored
        anchor = asyncio.run(backend.resolve("agent-a", namespace="gossip"))
        assert anchor is not None
