# SPDX-License-Identifier: Apache-2.0
"""
test_bsc_query_tool — Verify BscQueryTool dispatches to the right Web3
calls and shapes responses correctly. No live network.
"""

from __future__ import annotations

import asyncio
import types
import unittest
from unittest.mock import MagicMock, patch

from nexus_core.tools.bsc_query import BscQueryTool


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class _FakeEth:
    """Minimal stand-in for `w3.eth` covering the methods the tool calls."""
    def __init__(self):
        self.block_number = 50_123_456
        self._balance_wei = 1_500_000_000_000_000_000  # 1.5 BNB
        self._tx_receipts: dict[str, types.SimpleNamespace] = {}
        self._blocks: dict = {}
        self._code: dict[str, bytes] = {}

    def get_balance(self, addr):
        return self._balance_wei

    def get_transaction_receipt(self, tx_hash):
        return self._tx_receipts.get(tx_hash)

    def get_block(self, block):
        if block == "latest":
            return self._blocks["latest"]
        return self._blocks.get(int(block))

    def get_code(self, addr):
        return self._code.get(addr, b"")


class _FakeW3:
    def __init__(self):
        self.eth = _FakeEth()
        self.middleware_onion = MagicMock()

    def is_connected(self):
        return True

    @staticmethod
    def from_wei(wei, unit):
        if unit == "ether":
            return wei / 1e18
        return wei

    @staticmethod
    def to_checksum_address(addr):
        return addr  # fake — real one normalises case


class _FakeWeb3Module:
    """Fake `web3` package — only the bits the tool imports."""
    Web3 = _FakeW3

    @staticmethod
    def HTTPProvider(url):
        return ("http", url)


class BscQueryBlockNumberTests(unittest.TestCase):
    def test_block_number_mainnet(self):
        tool = BscQueryTool()
        fake = _FakeW3()
        with patch.object(tool, "_w3", return_value=fake):
            res = _run(tool.execute(action="block_number"))
        self.assertTrue(res.success)
        self.assertIn("50,123,456", res.output)
        self.assertIn("mainnet", res.output)

    def test_block_number_testnet(self):
        tool = BscQueryTool()
        fake = _FakeW3()
        with patch.object(tool, "_w3", return_value=fake) as p:
            _run(tool.execute(action="block_number", network="testnet"))
        # Confirm network arg threaded through.
        p.assert_called_with("testnet")

    def test_unreachable_rpc_returns_failure(self):
        tool = BscQueryTool()
        fake = _FakeW3()
        fake.is_connected = lambda: False
        with patch.object(tool, "_w3", return_value=fake):
            res = _run(tool.execute(action="block_number"))
        self.assertFalse(res.success)
        self.assertIn("RPC not reachable", res.error or "")


class BscQueryBalanceTests(unittest.TestCase):
    def test_balance_renders_bnb_and_wei(self):
        tool = BscQueryTool()
        fake = _FakeW3()
        addr = "0x" + "ab" * 20
        with patch.object(tool, "_w3", return_value=fake):
            res = _run(tool.execute(action="balance", address=addr))
        self.assertTrue(res.success)
        self.assertIn("1.500000 BNB", res.output)
        self.assertIn("wei", res.output)

    def test_balance_missing_address(self):
        tool = BscQueryTool()
        fake = _FakeW3()
        with patch.object(tool, "_w3", return_value=fake):
            res = _run(tool.execute(action="balance"))
        self.assertFalse(res.success)
        self.assertIn("address", res.error or "")


class BscQueryTxReceiptTests(unittest.TestCase):
    def test_tx_receipt_success(self):
        tool = BscQueryTool()
        fake = _FakeW3()
        receipt = MagicMock()
        receipt.status = 1
        receipt.blockNumber = 12345
        receipt.gasUsed = 21000
        receipt.effectiveGasPrice = 3_000_000_000
        receipt.__getitem__ = lambda self, k: {"from": "0xfrom", "to": "0xto"}[k]
        receipt.get = lambda k: "0xto" if k == "to" else None
        receipt.logs = []
        fake.eth._tx_receipts["0xtx"] = receipt
        with patch.object(tool, "_w3", return_value=fake):
            res = _run(tool.execute(action="tx_receipt", tx_hash="0xtx"))
        self.assertTrue(res.success)
        self.assertIn("status: success", res.output)
        self.assertIn("12,345", res.output)

    def test_tx_receipt_reverted(self):
        tool = BscQueryTool()
        fake = _FakeW3()
        receipt = MagicMock()
        receipt.status = 0
        receipt.blockNumber = 5
        receipt.gasUsed = 21000
        receipt.effectiveGasPrice = 3_000_000_000
        receipt.__getitem__ = lambda self, k: {"from": "0xa", "to": "0xb"}[k]
        receipt.get = lambda k: "0xb" if k == "to" else None
        receipt.logs = []
        fake.eth._tx_receipts["0xtx"] = receipt
        with patch.object(tool, "_w3", return_value=fake):
            res = _run(tool.execute(action="tx_receipt", tx_hash="0xtx"))
        self.assertTrue(res.success)
        self.assertIn("status: reverted", res.output)

    def test_tx_receipt_missing(self):
        tool = BscQueryTool()
        fake = _FakeW3()
        with patch.object(tool, "_w3", return_value=fake):
            res = _run(tool.execute(action="tx_receipt", tx_hash="0xnope"))
        self.assertFalse(res.success)
        self.assertIn("not found", res.error or "")


class BscQueryUnknownActionTests(unittest.TestCase):
    def test_unknown_action_lists_valid(self):
        tool = BscQueryTool()
        fake = _FakeW3()
        with patch.object(tool, "_w3", return_value=fake):
            res = _run(tool.execute(action="bogus"))
        self.assertFalse(res.success)
        for valid in ("block_number", "balance", "tx_receipt"):
            self.assertIn(valid, res.error or "")


class BscQueryToolDescriptionTests(unittest.TestCase):
    def test_description_warns_against_web_search(self):
        # Vital for the LLM to prefer this tool over web_search for chain
        # queries — the description IS the function-calling spec.
        tool = BscQueryTool()
        self.assertIn("NEVER web_search", tool.description)
        self.assertIn("BSC", tool.description)


if __name__ == "__main__":
    unittest.main()
