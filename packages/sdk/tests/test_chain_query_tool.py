# SPDX-License-Identifier: Apache-2.0
"""
test_chain_query_tool — Verify the multi-chain EVM RPC tool dispatches
to the right Web3 calls per network and shapes responses correctly.

No live network — Web3 is mocked. The point of these tests is to lock
the network → preset → action mapping, not to validate the upstream
RPC providers.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from nexus_core.tools.chain_query import ChainQueryTool, _PRESETS, _resolve_rpc


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class _FakeEth:
    def __init__(self, block_number=18_000_000, balance_wei=0):
        self.block_number = block_number
        self._balance_wei = balance_wei

    def get_balance(self, addr):
        return self._balance_wei


class _FakeW3:
    def __init__(self, **kw):
        self.eth = _FakeEth(**kw)
        self.middleware_onion = MagicMock()

    def is_connected(self):
        return True

    @staticmethod
    def from_wei(wei, unit):
        return wei / 1e18 if unit == "ether" else wei

    @staticmethod
    def to_checksum_address(addr):
        return addr


class ResolvePresetTests(unittest.TestCase):
    def test_known_networks_resolve(self):
        for net in ("ethereum", "polygon", "arbitrum", "optimism", "base"):
            preset, err = _resolve_rpc(net)
            self.assertIsNone(err, msg=net)
            self.assertEqual(preset["name"], _PRESETS[net]["name"])

    def test_unknown_network_explains_options(self):
        preset, err = _resolve_rpc("dogecoin")
        self.assertIsNone(preset)
        self.assertIn("ethereum", err)  # listed in error
        self.assertIn("bsc_query", err)  # points at BSC alternative
        self.assertIn("manage_mcp", err)  # points at non-EVM path

    def test_env_override_takes_precedence(self):
        import os
        os.environ["ETHEREUM_RPC"] = "https://my-alchemy/key"
        try:
            preset, _ = _resolve_rpc("ethereum")
            self.assertEqual(preset["rpc"], "https://my-alchemy/key")
        finally:
            del os.environ["ETHEREUM_RPC"]


class BlockNumberTests(unittest.TestCase):
    def test_each_chain_returns_branded_label(self):
        for net, expected_label in [
            ("ethereum", "Ethereum mainnet"),
            ("polygon",  "Polygon mainnet"),
            ("arbitrum", "Arbitrum One"),
            ("optimism", "OP Mainnet"),
            ("base",     "Base mainnet"),
        ]:
            tool = ChainQueryTool()
            with patch.object(tool, "_get_w3",
                              return_value=_FakeW3(block_number=12345678)):
                res = _run(tool.execute(action="block_number", network=net))
            self.assertTrue(res.success, msg=net)
            self.assertIn(expected_label, res.output)
            self.assertIn("12,345,678", res.output)


class BalanceTests(unittest.TestCase):
    def test_balance_uses_chain_native_token_label(self):
        # ETH on Ethereum
        tool = ChainQueryTool()
        with patch.object(tool, "_get_w3",
                          return_value=_FakeW3(balance_wei=2_500_000_000_000_000_000)):
            res = _run(tool.execute(action="balance", network="ethereum",
                                    address="0x" + "ab" * 20))
        self.assertIn("2.500000 ETH", res.output)

        # MATIC on Polygon
        tool = ChainQueryTool()
        with patch.object(tool, "_get_w3",
                          return_value=_FakeW3(balance_wei=1_000_000_000_000_000_000)):
            res = _run(tool.execute(action="balance", network="polygon",
                                    address="0x" + "cd" * 20))
        self.assertIn("MATIC", res.output)

    def test_balance_missing_address(self):
        tool = ChainQueryTool()
        with patch.object(tool, "_get_w3", return_value=_FakeW3()):
            res = _run(tool.execute(action="balance", network="ethereum"))
        self.assertFalse(res.success)
        self.assertIn("address", res.error)


class DescriptionTests(unittest.TestCase):
    def test_description_calls_out_bsc_separation(self):
        # The whole point of having two tools is to direct the LLM
        # cleanly. Description must explicitly carve out BSC and
        # non-EVM cases so it doesn't try to use chain_query for them.
        tool = ChainQueryTool()
        d = tool.description
        self.assertIn("bsc_query", d)
        self.assertIn("manage_mcp", d)
        self.assertIn("NOT web_search", d)

    def test_parameters_enum_lists_all_5_chains(self):
        tool = ChainQueryTool()
        nets = tool.parameters["properties"]["network"]["enum"]
        for chain in ("ethereum", "polygon", "arbitrum", "optimism", "base"):
            self.assertIn(chain, nets)
        # BSC must NOT be in chain_query — it has its own tool.
        self.assertNotIn("bsc", nets)


class UnknownActionTests(unittest.TestCase):
    def test_returns_helpful_error(self):
        tool = ChainQueryTool()
        with patch.object(tool, "_get_w3", return_value=_FakeW3()):
            res = _run(tool.execute(action="bogus", network="ethereum"))
        self.assertFalse(res.success)
        self.assertIn("block_number", res.error)


if __name__ == "__main__":
    unittest.main()
