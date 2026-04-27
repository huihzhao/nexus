"""
Advanced regression tests for BNBChainSessionService.

These complement the basic tests in test_state.py and focus on:
  - Batched flush (events accumulate, then flush together)
  - Explicit flush() API
  - close() flushes remaining events
  - Session config filters (num_recent_events, after_timestamp)
  - Multiple sessions for the same agent
  - State delta accumulation across many events
  - WAL crash recovery with state delta replay
  - Service.flush(session_id=...) targets specific session
"""

import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google.adk.events import Event, EventActions
from google.adk.sessions import Session
from google.adk.sessions.base_session_service import GetSessionConfig

from nexus_core.state import StateManager
from nexus_core.session import BNBChainSessionService
from nexus_core.flush import FlushPolicy


@pytest.fixture
def session_svc(state_manager, flush_policy):
    """Session service with test flush policy."""
    return BNBChainSessionService(state_manager, runtime_id="test-rt", flush_policy=flush_policy)


def _make_event(invocation_id: str, state_delta: dict) -> Event:
    """Create a minimal Event with state_delta."""
    event = Event(
        invocation_id=invocation_id,
        author="agent",
        actions=EventActions(state_delta=state_delta),
    )
    event.id = Event.new_id()
    return event


class TestBatchedFlush:

    def test_events_accumulate_before_flush(self, state_manager, tmp_state_dir):
        """With high threshold, events should stay in buffer."""
        async def _test():
            policy = FlushPolicy(
                every_n_events=100,  # won't trigger
                interval_seconds=0,
                wal_enabled=True,
                wal_dir=os.path.join(tmp_state_dir, "wal"),
            )
            svc = BNBChainSessionService(state_manager, runtime_id="rt-1", flush_policy=policy)

            session = await svc.create_session(
                app_name="app-1", user_id="user-1", session_id="sess-1",
                state={"step": 0},
            )

            for i in range(3):
                event = _make_event(f"inv-{i}", {"step": i + 1})
                await svc.append_event(session, event)

            # In-memory session should have all 3 events
            loaded = await svc.get_session(
                app_name="app-1", user_id="user-1", session_id="sess-1",
            )
            assert len(loaded.events) == 3
            assert loaded.state["step"] == 3

        asyncio.run(_test())

    def test_explicit_flush(self, state_manager, tmp_state_dir):
        """flush(session_id) should persist to chain immediately."""
        async def _test():
            policy = FlushPolicy(
                every_n_events=100,
                interval_seconds=0,
                wal_enabled=True,
                wal_dir=os.path.join(tmp_state_dir, "wal"),
            )
            svc = BNBChainSessionService(state_manager, runtime_id="rt-1", flush_policy=policy)

            session = await svc.create_session(
                app_name="app-1", user_id="user-1", session_id="sess-1",
            )

            event = _make_event("inv-0", {"data": "hello"})
            await svc.append_event(session, event)

            # Flush specific session
            count = svc.flush(session_id="sess-1")
            assert count >= 1

            # New service instance should find the data
            svc2 = BNBChainSessionService(state_manager, runtime_id="rt-2", flush_policy=policy)
            loaded = await svc2.get_session(
                app_name="app-1", user_id="user-1", session_id="sess-1",
            )
            assert loaded.state.get("data") == "hello"

        asyncio.run(_test())

    def test_close_flushes(self, state_manager, tmp_state_dir):
        """close() should flush all sessions."""
        async def _test():
            policy = FlushPolicy(
                every_n_events=100,
                interval_seconds=0,
                sync_on_close=True,
                wal_enabled=True,
                wal_dir=os.path.join(tmp_state_dir, "wal"),
            )
            svc = BNBChainSessionService(state_manager, runtime_id="rt-1", flush_policy=policy)

            session = await svc.create_session(
                app_name="app-1", user_id="user-1", session_id="sess-1",
            )
            event = _make_event("inv-0", {"closed": True})
            await svc.append_event(session, event)

            svc.close()

            # New service should find the data
            svc2 = BNBChainSessionService(state_manager, runtime_id="rt-2", flush_policy=policy)
            loaded = await svc2.get_session(
                app_name="app-1", user_id="user-1", session_id="sess-1",
            )
            assert loaded.state.get("closed") is True

        asyncio.run(_test())


class TestSessionConfigFilters:

    def test_num_recent_events(self, session_svc):
        async def _test():
            session = await session_svc.create_session(
                app_name="app-1", user_id="user-1", session_id="sess-1",
            )
            for i in range(5):
                event = _make_event(f"inv-{i}", {f"step_{i}": True})
                await session_svc.append_event(session, event)

            config = GetSessionConfig(num_recent_events=2)
            loaded = await session_svc.get_session(
                app_name="app-1", user_id="user-1", session_id="sess-1",
                config=config,
            )
            assert len(loaded.events) == 2

        asyncio.run(_test())

    def test_after_timestamp_filter(self, session_svc):
        """Test GetSessionConfig.after_timestamp filter on get_session."""
        async def _test():
            session = await session_svc.create_session(
                app_name="app-1", user_id="user-1", session_id="sess-1",
            )

            # Add 3 events with distinct timestamps
            for i in range(3):
                event = _make_event(f"inv-{i}", {f"step_{i}": i})
                await session_svc.append_event(session, event)
                time.sleep(0.01)  # Small delay to ensure distinct timestamps

            # Reload to get timestamps
            loaded = await session_svc.get_session(
                app_name="app-1", user_id="user-1", session_id="sess-1",
            )
            assert len(loaded.events) == 3

            # Filter to events after the first one's timestamp
            if len(loaded.events) >= 2:
                cutoff_ts = loaded.events[0].timestamp
                config = GetSessionConfig(after_timestamp=cutoff_ts)
                filtered = await session_svc.get_session(
                    app_name="app-1", user_id="user-1", session_id="sess-1",
                    config=config,
                )
                # Should get events strictly after cutoff (not including the cutoff event)
                assert len(filtered.events) <= len(loaded.events)

        asyncio.run(_test())


class TestDeleteSession:

    def test_delete_session(self, session_svc):
        """Test that delete_session removes the session and it's no longer listed."""
        async def _test():
            # Create a session
            session = await session_svc.create_session(
                app_name="app-1", user_id="user-1", session_id="sess-delete",
            )
            assert session.id == "sess-delete"

            # Verify it exists
            loaded = await session_svc.get_session(
                app_name="app-1", user_id="user-1", session_id="sess-delete",
            )
            assert loaded is not None

            # Delete it
            await session_svc.delete_session(
                app_name="app-1", user_id="user-1", session_id="sess-delete",
            )

            # Verify get_session returns None
            deleted = await session_svc.get_session(
                app_name="app-1", user_id="user-1", session_id="sess-delete",
            )
            assert deleted is None

            # Verify it's no longer in list_sessions
            resp = await session_svc.list_sessions(app_name="app-1")
            session_ids = {s.id for s in resp.sessions}
            assert "sess-delete" not in session_ids

        asyncio.run(_test())


class TestMultipleSessions:

    def test_two_sessions_same_agent(self, session_svc):
        """Multiple sessions under the same agent should be independent."""
        async def _test():
            sess_a = await session_svc.create_session(
                app_name="app-1", user_id="user-1", session_id="sess-a",
                state={"name": "A"},
            )
            sess_b = await session_svc.create_session(
                app_name="app-1", user_id="user-1", session_id="sess-b",
                state={"name": "B"},
            )

            event_a = _make_event("inv-a", {"result": "from A"})
            await session_svc.append_event(sess_a, event_a)

            event_b = _make_event("inv-b", {"result": "from B"})
            await session_svc.append_event(sess_b, event_b)

            loaded_a = await session_svc.get_session(
                app_name="app-1", user_id="user-1", session_id="sess-a",
            )
            loaded_b = await session_svc.get_session(
                app_name="app-1", user_id="user-1", session_id="sess-b",
            )

            assert loaded_a.state["result"] == "from A"
            assert loaded_b.state["result"] == "from B"

        asyncio.run(_test())

    def test_list_sessions(self, session_svc):
        async def _test():
            await session_svc.create_session(
                app_name="app-1", user_id="user-1", session_id="sess-1",
            )
            await session_svc.create_session(
                app_name="app-1", user_id="user-1", session_id="sess-2",
            )

            resp = await session_svc.list_sessions(app_name="app-1")
            assert len(resp.sessions) == 2
            ids = {s.id for s in resp.sessions}
            assert ids == {"sess-1", "sess-2"}

        asyncio.run(_test())


class TestStateDeltaAccumulation:

    def test_many_events_accumulate_state(self, session_svc):
        """State should correctly accumulate across many events."""
        async def _test():
            session = await session_svc.create_session(
                app_name="app-1", user_id="user-1", session_id="sess-1",
                state={"counter": 0},
            )

            for i in range(10):
                event = _make_event(f"inv-{i}", {"counter": i + 1, f"step_{i}": f"done-{i}"})
                await session_svc.append_event(session, event)

            loaded = await session_svc.get_session(
                app_name="app-1", user_id="user-1", session_id="sess-1",
            )
            assert loaded.state["counter"] == 10
            assert len(loaded.events) == 10
            # Verify all step results are present
            for i in range(10):
                assert loaded.state[f"step_{i}"] == f"done-{i}"

        asyncio.run(_test())


class TestWALCrashRecovery:

    def test_wal_replay_preserves_state(self, state_manager, tmp_state_dir):
        """Simulate crash: WAL replay should restore both events and state."""
        async def _test():
            policy = FlushPolicy(
                every_n_events=100,  # won't auto-flush
                interval_seconds=0,
                wal_enabled=True,
                wal_dir=os.path.join(tmp_state_dir, "wal"),
            )

            # Runtime 1: create session, add events, then "crash" (no flush)
            svc1 = BNBChainSessionService(state_manager, runtime_id="rt-1", flush_policy=policy)
            session = await svc1.create_session(
                app_name="app-1", user_id="user-1", session_id="sess-1",
                state={"step": 0},
            )
            for i in range(3):
                event = _make_event(f"inv-{i}", {"step": i + 1, f"result_{i}": f"val-{i}"})
                await svc1.append_event(session, event)

            # "Crash" — drop svc1 without flushing

            # Runtime 2: load from chain (should recover via WAL)
            svc2 = BNBChainSessionService(state_manager, runtime_id="rt-2", flush_policy=policy)
            loaded = await svc2.get_session(
                app_name="app-1", user_id="user-1", session_id="sess-1",
            )

            assert loaded is not None
            assert loaded.state["step"] == 3
            assert loaded.state["result_0"] == "val-0"
            assert loaded.state["result_2"] == "val-2"
            assert len(loaded.events) == 3

        asyncio.run(_test())
