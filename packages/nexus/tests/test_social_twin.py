"""
Tests for Social Protocol integration in Rune Nexus.

Tests: SocialEngine, gossip between twins, impression formation,
profile generation, social graph queries, CLI commands.
"""

import asyncio
import json
import pytest

from nexus_core import (
    Rune,
    MockBackend,
    Impression,
    ImpressionDimensions,
    AgentProfile,
    GossipProtocol,
)

from nexus.config import TwinConfig
from nexus.twin import DigitalTwin
from nexus.evolution.social_engine import SocialEngine
from nexus.evolution.engine import EvolutionEngine


# ── Mock LLM ─────────────────────────────────────────────────────


class MockSocialLLM:
    """Mock LLM that handles social protocol prompts."""

    def __init__(self):
        self.calls = []

    async def __call__(self, prompt, **kwargs):
        return await self.complete(prompt, **kwargs)

    async def complete(self, prompt, **kwargs):
        self.calls.append(prompt[:100])

        # Profile generation
        if "generate a public profile" in prompt.lower():
            return json.dumps({
                "interests": ["japanese_cuisine", "travel", "blockchain"],
                "capabilities": ["travel_planning", "food_recommendation"],
                "style_tags": ["detail_oriented", "concise"],
            })

        # Gossip response
        if "casual conversation with another agent" in prompt.lower():
            return "I know some great sushi places. My owner often explores Shinjuku area for dining."

        # Impression formation
        if "evaluate the other agent" in prompt.lower():
            return json.dumps({
                "interest_overlap": 0.82,
                "knowledge_complementarity": 0.65,
                "style_compatibility": 0.71,
                "reliability": 0.88,
                "depth": 0.55,
                "compatibility_score": 0.75,
                "summary": "Strong food knowledge, good match for travel topics.",
                "would_gossip_again": True,
                "recommend_to_network": True,
            })

        # Memory extraction (from EvolutionEngine)
        if "analyze the following conversation" in prompt.lower():
            return json.dumps([
                {"content": "User likes sushi", "category": "preference", "importance": 4},
            ])

        # Skill learning
        if "identify any skills" in prompt.lower():
            return json.dumps({"implicit_tasks": [], "topic_signals": []})

        # Persona evolution
        if "evolved persona" in prompt.lower():
            return json.dumps({
                "evolved_persona": "Evolved persona with social awareness.",
                "changes_summary": "Added social context",
                "confidence": 0.85,
                "version_notes": "v1",
            })

        # Knowledge compilation
        if "group them into topic clusters" in prompt.lower():
            return json.dumps({})

        if "synthesize" in prompt.lower():
            return json.dumps({
                "title": "Test", "summary": "Test", "content": "Test",
                "key_facts": [], "tags": [], "memory_count": 0, "confidence": 0.5,
            })

        # Re-evaluation prompt
        if "re-evaluate more carefully" in prompt.lower():
            return json.dumps({
                "interest_overlap": 0.75,
                "knowledge_complementarity": 0.60,
                "style_compatibility": 0.70,
                "reliability": 0.85,
                "depth": 0.50,
                "compatibility_score": 0.72,
                "summary": "Re-evaluated: still a good match.",
                "would_gossip_again": True,
                "recommend_to_network": True,
            })

        return "[]"

    async def chat(self, messages, system="", **kwargs):
        self.calls.append(f"chat: {messages[-1]['content'][:50]}")
        return "Chat response."

    async def close(self):
        pass


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def backend():
    return MockBackend()


@pytest.fixture
def rune(backend):
    return Rune.builder().backend(backend).build()


@pytest.fixture
def llm():
    return MockSocialLLM()


@pytest.fixture
def social(rune, llm):
    return SocialEngine(rune, "agent-a", llm.complete, agent_name="Alice")


@pytest.fixture
def engine(rune, llm):
    return EvolutionEngine(
        rune=rune,
        agent_id="test-twin",
        llm_fn=llm.complete,
        default_persona="Test persona",
        agent_name="TestTwin",
    )


# ── SocialEngine Tests ──────────────────────────────────────────


class TestSocialEngine:
    def test_init(self, social):
        assert social.agent_id == "agent-a"
        assert social.agent_name == "Alice"
        assert social.gossip is not None
        assert social.profiles is not None

    def test_generate_profile(self, social):
        profile = asyncio.run(social.generate_profile(
            persona="I am Alice, a food and travel enthusiast.",
            memory_stats={"total_memories": 10, "categories": {"preference": 5}},
            skills_summary={"total_skills": 2},
        ))
        assert profile.agent_id == "agent-a"
        assert "japanese_cuisine" in profile.interests
        assert "travel_planning" in profile.capabilities

    def test_get_profile(self, social):
        # Generate first
        asyncio.run(social.generate_profile(
            persona="Test", memory_stats={}, skills_summary={},
        ))
        # Then retrieve
        profile = asyncio.run(social.get_profile())
        assert profile is not None
        assert profile.agent_id == "agent-a"

    def test_start_gossip(self, social):
        session = asyncio.run(social.start_gossip("agent-b", topic="food"))
        assert session.initiator == "agent-a"
        assert session.responder == "agent-b"
        assert session.topic_hint == "food"

    def test_generate_gossip_response(self, social):
        from nexus_core import GossipSession
        session = GossipSession(
            initiator="agent-a", responder="agent-b",
            topic_hint="food", status="active",
        )
        response = asyncio.run(social.generate_gossip_response(
            session,
            persona="I love Japanese food",
            memory_context="User enjoys omakase",
        ))
        assert isinstance(response, str)
        assert len(response) > 0

    def test_form_impression(self, social, rune):
        from nexus_core import GossipSession, GossipMessage
        session = GossipSession(
            initiator="agent-a", responder="agent-b",
            topic_hint="food", status="concluded",
            messages=[
                GossipMessage(sender="agent-a", content="Do you know good sushi?"),
                GossipMessage(sender="agent-b", content="Try Sushi Saito in Roppongi."),
            ],
        )

        impression = asyncio.run(social.form_impression(
            session,
            persona="I love food",
            memories=["User likes sushi", "Planning Tokyo trip"],
        ))

        assert impression is not None
        assert impression.source_agent == "agent-a"
        assert impression.target_agent == "agent-b"
        assert impression.compatibility_score > 0
        assert impression.would_gossip_again is True

        # Verify it was stored
        stored = asyncio.run(rune.impressions.get_impressions_of("agent-b", "agent-a"))
        assert len(stored) == 1

    def test_discover_agents(self, social, rune):
        # Publish some profiles first
        asyncio.run(social.profiles.publish(AgentProfile(
            agent_id="agent-b",
            interests=["food", "travel"],
            capabilities=["restaurant_review"],
        )))
        asyncio.run(social.profiles.publish(AgentProfile(
            agent_id="agent-c",
            interests=["music"],
            capabilities=["composition"],
        )))

        results = asyncio.run(social.discover_agents(interests=["food"]))
        assert len(results) == 1
        assert results[0].agent_id == "agent-b"

    def test_social_map_empty(self, social):
        smap = asyncio.run(social.get_social_map())
        assert smap["stats"]["agents_met"] == 0

    def test_social_map_with_data(self, social, rune):
        # Record some impressions
        asyncio.run(rune.impressions.record(Impression(
            source_agent="agent-a", target_agent="agent-b",
            gossip_session_id="s1",
            dimensions=ImpressionDimensions(interest_overlap=0.8),
            compatibility_score=0.75,
            would_gossip_again=True,
        )))
        asyncio.run(rune.impressions.record(Impression(
            source_agent="agent-b", target_agent="agent-a",
            gossip_session_id="s1",
            compatibility_score=0.70,
        )))

        smap = asyncio.run(social.get_social_map())
        assert smap["stats"]["agents_met"] == 1
        assert len(smap["top_matches"]) == 1
        assert len(smap["mutual_connections"]) == 1


# ── EvolutionEngine Integration Tests ────────────────────────────


class TestEvolutionSocial:
    def test_engine_has_social(self, engine):
        assert engine.social is not None
        assert isinstance(engine.social, SocialEngine)

    def test_reflection_generates_profile(self, engine):
        asyncio.run(engine.initialize())

        # Add some memories for context
        asyncio.run(engine.rune.memory.add(
            "User likes sushi", agent_id="test-twin",
            metadata={"category": "preference", "importance": 4},
        ))

        result = asyncio.run(engine.trigger_reflection())
        assert "profile_update" in result

        # Profile should have interests
        profile_data = result["profile_update"]
        assert "interests" in profile_data


# ── DigitalTwin Social Integration ───────────────────────────────


class TestDigitalTwinSocial:
    def _make_twin(self, rune, llm):
        config = TwinConfig(agent_id="test-twin", name="TestTwin", owner="Tester")
        twin = DigitalTwin(config=config, rune=rune, llm=llm)
        return twin

    def test_twin_has_social_commands(self, rune, llm):
        twin = self._make_twin(rune, llm)
        asyncio.run(twin._initialize())

        # /help should include social commands
        help_text = asyncio.run(twin.chat("/help"))
        assert "/social" in help_text
        assert "/gossip" in help_text
        assert "/discover" in help_text
        assert "/impressions" in help_text

    def test_social_map_command(self, rune, llm):
        twin = self._make_twin(rune, llm)
        asyncio.run(twin._initialize())

        result = asyncio.run(twin.chat("/social"))
        assert "Social Map" in result

    def test_impressions_command_empty(self, rune, llm):
        twin = self._make_twin(rune, llm)
        asyncio.run(twin._initialize())

        result = asyncio.run(twin.chat("/impressions"))
        assert "No impressions" in result

    def test_discover_command(self, rune, llm):
        twin = self._make_twin(rune, llm)
        asyncio.run(twin._initialize())

        result = asyncio.run(twin.chat("/discover"))
        assert "No agents found" in result or "Discovered" in result

    def test_gossip_command(self, rune, llm):
        twin = self._make_twin(rune, llm)
        asyncio.run(twin._initialize())

        result = asyncio.run(twin.chat("/gossip agent-bob food"))
        assert "Gossip session started" in result
        assert "agent-bob" in result

    def test_gossip_command_no_target(self, rune, llm):
        twin = self._make_twin(rune, llm)
        asyncio.run(twin._initialize())

        result = asyncio.run(twin.chat("/gossip"))
        assert "Usage" in result

    def test_twin_gossip_method(self, rune, llm):
        twin = self._make_twin(rune, llm)
        asyncio.run(twin._initialize())

        result = asyncio.run(twin.gossip("agent-bob", topic="travel"))
        assert result["target"] == "agent-bob"
        assert result["topic"] == "travel"
        assert "session_id" in result

    def test_twin_discover_method(self, rune, llm):
        twin = self._make_twin(rune, llm)
        asyncio.run(twin._initialize())

        # Publish a profile for another agent
        asyncio.run(twin.evolution.social.profiles.publish(AgentProfile(
            agent_id="agent-chef",
            interests=["japanese_cuisine", "cooking"],
            capabilities=["recipe_creation"],
        )))

        results = asyncio.run(twin.discover(interest="japanese_cuisine"))
        assert len(results) == 1
        assert results[0].agent_id == "agent-chef"


# ── Two-Twin Gossip Integration ──────────────────────────────────


class TestTwoTwinGossip:
    """
    Test two Digital Twins gossiping with each other and forming impressions.
    """

    def test_two_twins_gossip_and_impress(self, backend, llm):
        """Full integration: two twins gossip, both form impressions."""
        rune_a = Rune.builder().backend(backend).build()
        rune_b = Rune.builder().backend(backend).build()

        social_a = SocialEngine(rune_a, "twin-alice", llm.complete, "Alice")
        social_b = SocialEngine(rune_b, "twin-bob", llm.complete, "Bob")

        # Alice starts gossip
        session = asyncio.run(social_a.start_gossip("twin-bob", topic="tokyo_dining"))

        # Bridge: Alice and Bob exchange messages
        from nexus_core import GossipMessage

        async def run_gossip():
            # Alice speaks
            response_a = await social_a.generate_gossip_response(
                session, persona="Food lover", memory_context="Loves sushi",
            )
            msg_a = await social_a.gossip.send(session.session_id, response_a)

            # Bob receives and speaks
            bob_session = session  # In sync mode, shared reference
            social_b.gossip._sessions[session.session_id] = bob_session
            await social_b.gossip.receive(session.session_id, msg_a)

            response_b = await social_b.generate_gossip_response(
                bob_session, persona="Tokyo local", memory_context="Knows Roppongi",
            )
            msg_b = await social_b.gossip.send(session.session_id, response_b)
            await social_a.gossip.receive(session.session_id, msg_b)

            # Conclude
            concluded = await social_a.conclude_gossip(session.session_id)
            return concluded

        concluded = asyncio.run(run_gossip())
        assert concluded.status == "concluded"
        assert concluded.turn_count >= 2

        # Both form impressions
        imp_a = asyncio.run(social_a.form_impression(
            concluded,
            persona="Food lover",
            memories=["Loves sushi", "Planning Tokyo trip"],
        ))
        imp_b = asyncio.run(social_b.form_impression(
            concluded,
            persona="Tokyo local",
            memories=["Knows Roppongi dining"],
        ))

        assert imp_a is not None
        assert imp_a.source_agent == "twin-alice"
        assert imp_a.target_agent == "twin-bob"
        assert imp_a.compatibility_score > 0

        assert imp_b is not None
        assert imp_b.source_agent == "twin-bob"
        assert imp_b.target_agent == "twin-alice"

        # Verify impressions are stored
        stored_a = asyncio.run(rune_a.impressions.get_impressions_of("twin-bob", "twin-alice"))
        assert len(stored_a) == 1

        stored_b = asyncio.run(rune_b.impressions.get_impressions_of("twin-alice", "twin-bob"))
        assert len(stored_b) == 1

    def test_social_graph_after_gossip(self, backend, llm):
        """After gossip, social graph queries work correctly."""
        rune = Rune.builder().backend(backend).build()

        # Record impressions from multiple gossip sessions
        asyncio.run(rune.impressions.record(Impression(
            source_agent="alice", target_agent="bob",
            gossip_session_id="s1",
            dimensions=ImpressionDimensions(
                interest_overlap=0.85,
                knowledge_complementarity=0.70,
            ),
            compatibility_score=0.80,
            would_gossip_again=True,
            recommend_to_network=True,
        )))
        asyncio.run(rune.impressions.record(Impression(
            source_agent="bob", target_agent="alice",
            gossip_session_id="s1",
            compatibility_score=0.75,
            would_gossip_again=True,
        )))
        asyncio.run(rune.impressions.record(Impression(
            source_agent="alice", target_agent="charlie",
            gossip_session_id="s2",
            compatibility_score=0.60,
        )))

        social = SocialEngine(rune, "alice", llm.complete, "Alice")
        smap = asyncio.run(social.get_social_map())

        assert smap["stats"]["agents_met"] == 2
        assert len(smap["top_matches"]) == 2
        assert smap["top_matches"][0]["agent"] == "bob"  # highest score
        assert len(smap["mutual_connections"]) == 1  # only bob is mutual
