"""
Shared fixtures for Rune Protocol SDK test suite.

Provides a clean StateManager + FlushPolicy per test, guaranteeing
test isolation (each test gets a fresh temp directory that is
automatically cleaned up).
"""

import os
import shutil
import sys
import tempfile

import pytest

# Ensure the SDK package is importable regardless of install state
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nexus_core.state import StateManager
from nexus_core.flush import FlushPolicy


@pytest.fixture
def tmp_state_dir(tmp_path):
    """Return a clean temp directory path string.  Cleaned automatically by pytest."""
    d = str(tmp_path / "rune_test")
    os.makedirs(d, exist_ok=True)
    return d


@pytest.fixture
def state_manager(tmp_state_dir):
    """Fresh StateManager in local mode."""
    return StateManager(base_dir=tmp_state_dir, mode="local")


@pytest.fixture
def flush_policy(tmp_state_dir):
    """FlushPolicy with WAL dir inside the test temp dir."""
    return FlushPolicy(wal_dir=os.path.join(tmp_state_dir, "wal"))
