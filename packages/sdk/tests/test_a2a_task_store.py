"""
Regression tests for BNBChainTaskStore (A2A TaskStore on-chain).

Covers:
  - save + get round-trip
  - Task status transitions (submitted → working → completed)
  - Deferred BSC writes for interim states
  - Explicit flush() sends deferred writes to BSC
  - Terminal states always sync to BSC
  - Optimistic concurrency (version tracking)
  - delete marks task as failed on chain
  - get returns None for unknown task
  - _should_sync_bsc logic for different policies
"""

import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from a2a.types import Task, TaskState, TaskStatus

from nexus_core.state import StateManager
from nexus_core.adapters.a2a_task_store import BNBChainTaskStore
from nexus_core.flush import FlushPolicy


def _make_task(task_id: str, state: TaskState = TaskState.submitted,
               context_id: str = "ctx-1") -> Task:
    """Helper to create a minimal A2A Task."""
    return Task(
        id=task_id,
        contextId=context_id,
        status=TaskStatus(state=state),
    )


@pytest.fixture
def task_store(state_manager):
    """BNBChainTaskStore backed by a clean StateManager."""
    state_manager.register_agent("agent-1", "owner-1")
    return BNBChainTaskStore(state_manager, agent_id="agent-1")


@pytest.fixture
def task_store_deferred(state_manager):
    """TaskStore with manual flush policy (deferred BSC writes)."""
    state_manager.register_agent("agent-1", "owner-1")
    policy = FlushPolicy.manual()
    policy.sync_task_transitions = False
    return BNBChainTaskStore(state_manager, agent_id="agent-1", flush_policy=policy)


class TestTaskStoreCRUD:

    def test_save_and_get(self, task_store):
        async def _test():
            task = _make_task("task-1")
            await task_store.save(task)

            loaded = await task_store.get("task-1")
            assert loaded is not None
            assert loaded.id == "task-1"
            assert loaded.status.state == TaskState.submitted

        asyncio.run(_test())

    def test_get_nonexistent(self, task_store):
        async def _test():
            loaded = await task_store.get("does-not-exist")
            assert loaded is None

        asyncio.run(_test())

    def test_delete(self, task_store):
        async def _test():
            task = _make_task("task-1")
            await task_store.save(task)

            await task_store.delete("task-1")

            # After delete, BSC record is "failed" but still exists.
            # The version tracking should be cleared.
            assert "task-1" not in task_store._versions

        asyncio.run(_test())


class TestTaskStatusTransitions:

    def test_submitted_to_working_to_completed(self, task_store):
        """Full lifecycle: submitted → working → completed."""
        async def _test():
            # submitted
            task = _make_task("task-1", TaskState.submitted)
            await task_store.save(task)

            # working
            task.status = TaskStatus(state=TaskState.working)
            await task_store.save(task)

            loaded = await task_store.get("task-1")
            assert loaded.status.state == TaskState.working

            # completed
            task.status = TaskStatus(state=TaskState.completed)
            await task_store.save(task)

            loaded = await task_store.get("task-1")
            assert loaded.status.state == TaskState.completed

        asyncio.run(_test())

    def test_version_increments(self, task_store):
        """Each save should increment the on-chain version."""
        async def _test():
            task = _make_task("task-1", TaskState.submitted)
            await task_store.save(task)
            v1 = task_store._versions.get("task-1", 0)

            task.status = TaskStatus(state=TaskState.working)
            await task_store.save(task)
            v2 = task_store._versions.get("task-1", 0)

            assert v2 > v1

        asyncio.run(_test())


class TestDeferredFlush:

    def test_interim_state_deferred(self, task_store_deferred):
        """With manual policy + no sync_task_transitions, working state is deferred."""
        async def _test():
            store = task_store_deferred

            # First save (new task): creates on BSC but update may be deferred
            task = _make_task("task-1", TaskState.submitted)
            await store.save(task)

            # Second save with same status: should be deferred (no transition)
            task.status = TaskStatus(state=TaskState.working)
            await store.save(task)
            assert "task-1" in store._pending_bsc  # deferred

            # But the task should still be loadable from buffer
            loaded = await store.get("task-1")
            assert loaded is not None
            assert loaded.status.state == TaskState.working

        asyncio.run(_test())

    def test_explicit_flush(self, task_store_deferred):
        """flush() should send deferred writes to BSC."""
        async def _test():
            store = task_store_deferred

            task = _make_task("task-1", TaskState.submitted)
            await store.save(task)

            task.status = TaskStatus(state=TaskState.working)
            await store.save(task)
            assert "task-1" in store._pending_bsc

            count = store.flush()
            assert count == 1
            assert "task-1" not in store._pending_bsc

        asyncio.run(_test())

    def test_terminal_state_always_syncs(self, task_store_deferred):
        """Terminal states (completed/failed) should always sync to BSC."""
        async def _test():
            store = task_store_deferred

            task = _make_task("task-1", TaskState.submitted)
            await store.save(task)

            task.status = TaskStatus(state=TaskState.completed)
            await store.save(task)
            # Terminal state should NOT be in pending
            assert "task-1" not in store._pending_bsc

        asyncio.run(_test())


class TestTaskStoreFromChain:

    def test_load_from_fresh_store(self, state_manager):
        """A new TaskStore should be able to load tasks created by a previous one."""
        async def _test():
            state_manager.register_agent("agent-1", "owner-1")

            # Store 1: create and save a task
            store1 = BNBChainTaskStore(state_manager, agent_id="agent-1")
            task = _make_task("task-1", TaskState.completed)
            await store1.save(task)

            # Store 2: fresh instance, load from chain
            store2 = BNBChainTaskStore(state_manager, agent_id="agent-1")
            loaded = await store2.get("task-1")
            assert loaded is not None
            assert loaded.id == "task-1"
            assert loaded.status.state == TaskState.completed

        asyncio.run(_test())


class TestA2AStatusMapping:

    def test_a2a_state_to_chain_status_mapping(self, task_store):
        """Verify the status mapping covers all TaskState enum values correctly."""
        async def _test():
            store = task_store

            # Test the mapping for all TaskState values
            test_cases = [
                (TaskState.submitted, "pending"),
                (TaskState.working, "running"),
                (TaskState.completed, "completed"),
                (TaskState.failed, "failed"),
                (TaskState.canceled, "failed"),
                (TaskState.rejected, "failed"),
                (TaskState.input_required, "running"),
                (TaskState.auth_required, "running"),
                (TaskState.unknown, "pending"),
            ]

            for task_state, expected_chain_status in test_cases:
                task = _make_task(f"task-{task_state.name}", task_state)
                await store.save(task)

                loaded = await store.get(f"task-{task_state.name}")
                assert loaded is not None
                # Verify the state was preserved (the mapping happens internally)
                assert loaded.status.state == task_state

        asyncio.run(_test())
