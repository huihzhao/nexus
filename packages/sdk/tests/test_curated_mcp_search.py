# SPDX-License-Identifier: Apache-2.0
"""
test_curated_mcp_search — Verify search_mcp consults the curated catalog
first, surfaces LobeHub auth errors as warnings (not silent empty), and
that install_mcp routes npm: identifiers correctly.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from nexus_core.skills.manager import SkillManager


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class CuratedCatalogShape(unittest.TestCase):
    def test_curated_json_has_expected_entries(self):
        path = (
            Path(__file__).parent.parent
            / "nexus_core" / "skills" / "curated_mcp.json"
        )
        with open(path) as f:
            data = json.load(f)
        items = data["items"]
        ids = [it["identifier"] for it in items]
        # A few we want to keep — adjust together with the catalog.
        for must in ("npm:@modelcontextprotocol/server-postgres",
                     "npm:@modelcontextprotocol/server-slack",
                     "npm:@modelcontextprotocol/server-github",
                     "npm:mcp-server-starknet"):
            self.assertIn(must, ids, msg=f"missing {must}")

    def test_every_entry_has_required_fields(self):
        # The schema is informal — each install path / search hit
        # depends on these being non-empty.
        path = (
            Path(__file__).parent.parent
            / "nexus_core" / "skills" / "curated_mcp.json"
        )
        with open(path) as f:
            items = json.load(f)["items"]
        for it in items:
            self.assertTrue(it.get("identifier"), it)
            self.assertTrue(it.get("name"), it)
            self.assertIsInstance(it.get("keywords"), list)


class CuratedSearchTests(unittest.TestCase):
    """search_mcp should hit curated FIRST — even when LobeHub fails."""

    def setUp(self):
        # Force-clear the cache so each test starts with a fresh load.
        SkillManager._curated_cache = None

    def _make_manager(self, tmp_path: Path) -> SkillManager:
        return SkillManager(base_dir=str(tmp_path))

    def test_search_starknet_finds_curated_entry(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._make_manager(Path(tmp))
            # Pretend npx is unavailable (FileNotFoundError in subprocess.run)
            # so only the curated layer runs.
            with patch("subprocess.run", side_effect=FileNotFoundError("npx")):
                results = _run(mgr.search_mcp("starknet"))
            self.assertGreater(len(results), 0)
            # The curated entry has keyword 'starknet'.
            ids = [r["identifier"] for r in results]
            self.assertIn("npm:mcp-server-starknet", ids)
            # All results should be tagged as source='curated' (no lobehub).
            for r in results:
                self.assertEqual(r["source"], "curated")

    def test_search_postgres_finds_official_server(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._make_manager(Path(tmp))
            with patch("subprocess.run", side_effect=FileNotFoundError("npx")):
                results = _run(mgr.search_mcp("postgres"))
            self.assertTrue(
                any(r["identifier"] ==
                    "npm:@modelcontextprotocol/server-postgres"
                    for r in results),
            )

    def test_lobehub_no_credentials_does_not_swallow_curated(self):
        """Even when LobeHub spits 'No credentials found', curated
        results from layer 1 must still surface."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._make_manager(Path(tmp))
            fake = MagicMock()
            fake.stdout = ""
            fake.stderr = (
                "No credentials found. Run `lhm register` first or "
                "set MARKET_CLIENT_ID and MARKET_CLIENT_SECRET."
            )
            with patch("subprocess.run", return_value=fake):
                results = _run(mgr.search_mcp("github"))
            # github MCP is in the curated list — must come back.
            ids = [r["identifier"] for r in results]
            self.assertIn("npm:@modelcontextprotocol/server-github", ids)

    def test_unknown_query_returns_empty(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._make_manager(Path(tmp))
            with patch("subprocess.run", side_effect=FileNotFoundError("npx")):
                results = _run(mgr.search_mcp("nonexistent-dragonfly-xyz"))
            self.assertEqual(results, [])


class InstallMcpTests(unittest.TestCase):
    """install_mcp must route by identifier prefix."""

    def setUp(self):
        SkillManager._curated_cache = None

    def test_npm_identifier_uses_npx_directly(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            mgr = SkillManager(base_dir=tmp)
            registry = MagicMock()
            registry.register_mcp_server = AsyncMock(
                return_value=["query_db", "list_tables"],
            )
            with patch("nexus_core.mcp.MCPServerConfig") as cfg_cls:
                res = _run(mgr.install_mcp(
                    "npm:@modelcontextprotocol/server-postgres",
                    tool_registry=registry,
                ))
            self.assertEqual(res["tools"], ["query_db", "list_tables"])
            self.assertEqual(res["source"], "npm")
            # Confirm the config was built with the right command.
            cfg_cls.assert_called_once()
            kwargs = cfg_cls.call_args.kwargs
            self.assertEqual(kwargs["command"], "npx")
            self.assertEqual(
                kwargs["args"],
                ["-y", "@modelcontextprotocol/server-postgres"],
            )

    def test_github_identifier_returns_actionable_error(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            mgr = SkillManager(base_dir=tmp)
            res = _run(mgr.install_mcp("github:owner/repo", tool_registry=MagicMock()))
            self.assertEqual(res["tools"], [])
            self.assertIn("not implemented", res["error"])

    def test_lobehub_no_creds_returns_actionable_error(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            mgr = SkillManager(base_dir=tmp)
            fake = MagicMock()
            fake.stdout = ""
            fake.stderr = "No credentials found. Run `lhm register` first."
            with patch("subprocess.run", return_value=fake):
                res = _run(mgr.install_mcp("lobehub:something",
                                           tool_registry=MagicMock()))
            self.assertEqual(res["tools"], [])
            self.assertIn("MARKET_CLIENT_ID", res["error"])


if __name__ == "__main__":
    unittest.main()
