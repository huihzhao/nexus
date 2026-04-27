"""Shared fixtures + path isolation for nexus tests.

The default ``TwinConfig.base_dir`` is ``.nexus`` — relative to cwd.
Without a fixture pinning it to a tempdir, every test that builds an
EventLog writes SQLite DB files into the package directory on the
developer's machine (or, on CI, into the runner's working tree).

This conftest:
  * Provides ``twin_base_dir`` — a per-test temp directory string that
    ``TwinConfig(base_dir=...)`` should be initialised with.
  * Cleans up before and after.

Tests that don't take the ``twin_base_dir`` fixture explicitly will
still default to ``.nexus`` — opt-in for now, opt-out / autouse later
if we want stricter isolation.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import pytest


@pytest.fixture
def twin_base_dir(tmp_path) -> str:
    """A fresh per-test base_dir, on real filesystem (not the
    workspace mount). Yields a string path; tests pass it to
    ``TwinConfig(base_dir=twin_base_dir, ...)``."""
    p = tmp_path / "twin"
    p.mkdir(parents=True, exist_ok=True)
    yield str(p)


@pytest.fixture(autouse=True)
def _force_safe_cwd(tmp_path, monkeypatch):
    """Run every test from a temp cwd so any ``.nexus`` / ``.rune_cache``
    relative paths land in a tempdir instead of the dev's working
    tree."""
    monkeypatch.chdir(tmp_path)
    yield
