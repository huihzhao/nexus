"""Unit tests for StateManager, BNBChainSessionService, BNBChainArtifactService."""

import asyncio
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nexus_core.state import StateManager
from nexus_core.session import BNBChainSessionService
from nexus_core.artifact import BNBChainArtifactService
from google.adk.events import Event, EventActions


TEST_DIR = "/tmp/bnbchain_test_state"


class TestStateManager(unittest.TestCase):
    def setUp(self):
        if os.path.exists(TEST_DIR):
            shutil.rmtree(TEST_DIR)
        self.mgr = StateManager(base_dir=TEST_DIR)

    def tearDown(self):
        shutil.rmtree(TEST_DIR, ignore_errors=True)

    def test_register_and_get_agent(self):
        record = self.mgr.register_agent("agent-1", "owner-1")
        self.assertEqual(record.agent_id, "agent-1")
        # get_agent returns AgentStateRecord (no owner — owner is on ERC-8004)
        loaded = self.mgr.get_agent("agent-1")
        self.assertEqual(loaded.agent_id, "agent-1")
        # owner lives on ERC-8004 Identity Registry
        identity = self.mgr.get_identity("agent-1")
        self.assertEqual(identity.owner, "owner-1")
        self.assertTrue(self.mgr.verify_owner("agent-1", "owner-1"))
        self.assertFalse(self.mgr.verify_owner("agent-1", "someone-else"))

    def test_state_root_update_and_resolve(self):
        self.mgr.register_agent("agent-1", "owner-1")
        self.mgr.update_state_root("agent-1", "abc123", "runtime-1")
        root = self.mgr.resolve_state_root("agent-1")
        self.assertEqual(root, "abc123")

    def test_content_hash_storage(self):
        data = b'{"hello": "world"}'
        h = self.mgr.store_data(data)
        loaded = self.mgr.load_data(h)
        self.assertEqual(data, loaded)

    def test_json_roundtrip(self):
        obj = {"steps": [1, 2, 3], "result": "ok"}
        h = self.mgr.store_json(obj)
        loaded = self.mgr.load_json(h)
        self.assertEqual(obj, loaded)

    def test_task_version_conflict(self):
        self.mgr.register_agent("agent-1", "owner-1")
        self.mgr.create_task("task-1", "agent-1")
        self.mgr.update_task("task-1", "hash-1", "running", expected_version=0)
        with self.assertRaises(ValueError):
            self.mgr.update_task("task-1", "hash-2", "running", expected_version=0)

    def test_task_lifecycle(self):
        self.mgr.register_agent("agent-1", "owner-1")
        self.mgr.create_task("task-1", "agent-1")
        task = self.mgr.get_task("task-1")
        self.assertEqual(task.status, "pending")
        self.mgr.update_task("task-1", "hash-1", "running")
        task = self.mgr.get_task("task-1")
        self.assertEqual(task.status, "running")
        self.assertEqual(task.version, 1)

    def test_memory_root_update_and_resolve(self):
        """Test update_memory_root() and resolve_memory_root() methods."""
        self.mgr.register_agent("agent-1", "owner-1")
        # Initially no memory root
        root = self.mgr.resolve_memory_root("agent-1")
        self.assertIsNone(root)

        # Update memory root
        memory_hash = "def456789abcdef" * 4 + "def45678"  # 64 hex chars
        self.mgr.update_memory_root("agent-1", memory_hash, "runtime-1")

        # Verify it was stored
        resolved = self.mgr.resolve_memory_root("agent-1")
        self.assertEqual(resolved, memory_hash)

    def test_resolve_memory_root_unregistered(self):
        """Test resolve_memory_root() on unregistered agent returns None."""
        root = self.mgr.resolve_memory_root("unknown-agent")
        self.assertIsNone(root)


class TestSessionService(unittest.TestCase):
    def setUp(self):
        if os.path.exists(TEST_DIR):
            shutil.rmtree(TEST_DIR)
        self.mgr = StateManager(base_dir=TEST_DIR)
        from nexus_core.flush import FlushPolicy
        self.flush_policy = FlushPolicy(wal_dir=os.path.join(TEST_DIR, "wal"))
        self.svc = BNBChainSessionService(self.mgr, runtime_id="test-runtime", flush_policy=self.flush_policy)

    def tearDown(self):
        shutil.rmtree(TEST_DIR, ignore_errors=True)

    def test_create_and_get_session(self):
        async def _test():
            session = await self.svc.create_session(
                app_name="test-app", user_id="user-1",
                state={"key": "value"}, session_id="sess-1",
            )
            self.assertEqual(session.id, "sess-1")
            self.assertEqual(session.state["key"], "value")

            loaded = await self.svc.get_session(
                app_name="test-app", user_id="user-1", session_id="sess-1",
            )
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.state["key"], "value")

        asyncio.run(_test())

    def test_append_event_persists(self):
        async def _test():
            session = await self.svc.create_session(
                app_name="test-app", user_id="user-1", session_id="sess-2",
            )
            event = Event(
                invocation_id="inv-1", author="agent",
                actions=EventActions(state_delta={"step": 1}),
            )
            event.id = Event.new_id()
            await self.svc.append_event(session, event)

            # Load from a FRESH service instance (simulating new runtime)
            svc2 = BNBChainSessionService(self.mgr, runtime_id="runtime-2", flush_policy=self.flush_policy)
            loaded = await svc2.get_session(
                app_name="test-app", user_id="user-1", session_id="sess-2",
            )
            self.assertEqual(len(loaded.events), 1)
            self.assertEqual(loaded.state["step"], 1)

        asyncio.run(_test())

    def test_checkpoint_resume(self):
        """Core test: simulate crash and resume."""
        async def _test():
            # Runtime 1: create session, add 2 events, then "crash"
            session = await self.svc.create_session(
                app_name="test-app", user_id="user-1", session_id="sess-3",
                state={"current_step": 0},
            )
            for i in range(2):
                event = Event(
                    invocation_id=f"inv-{i}", author="agent",
                    actions=EventActions(state_delta={
                        "current_step": i + 1,
                        f"result_{i}": f"done-{i}",
                    }),
                )
                event.id = Event.new_id()
                await self.svc.append_event(session, event)

            # "Crash" — discard in-memory session

            # Runtime 2: load from chain
            svc2 = BNBChainSessionService(self.mgr, runtime_id="runtime-2", flush_policy=self.flush_policy)
            restored = await svc2.get_session(
                app_name="test-app", user_id="user-1", session_id="sess-3",
            )
            self.assertEqual(restored.state["current_step"], 2)
            self.assertEqual(restored.state["result_0"], "done-0")
            self.assertEqual(restored.state["result_1"], "done-1")
            self.assertEqual(len(restored.events), 2)

        asyncio.run(_test())

    def test_list_and_delete_sessions(self):
        async def _test():
            await self.svc.create_session(
                app_name="test-app", user_id="user-1", session_id="sess-a",
            )
            await self.svc.create_session(
                app_name="test-app", user_id="user-1", session_id="sess-b",
            )
            resp = await self.svc.list_sessions(app_name="test-app")
            self.assertEqual(len(resp.sessions), 2)

            await self.svc.delete_session(
                app_name="test-app", user_id="user-1", session_id="sess-a",
            )
            resp = await self.svc.list_sessions(app_name="test-app")
            self.assertEqual(len(resp.sessions), 1)

        asyncio.run(_test())


if __name__ == "__main__":
    unittest.main(verbosity=2)
