"""
Regression tests for cli_utils module.

Covers:
  - load_dotenv reads .env file and sets missing env vars
  - load_dotenv skips existing env vars (no overwrite)
  - load_dotenv skips comments and blank lines
  - create_state_manager in local mode
  - create_state_manager in testnet mode (missing key → exit)
  - add_state_manager_args adds expected arguments
"""

import argparse
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nexus_core.cli_utils import load_dotenv, create_state_manager, add_state_manager_args


class TestLoadDotenv:

    def test_reads_env_file(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("TEST_NEXUS_VAR_A=hello\nTEST_NEXUS_VAR_B=world\n")

        # Temporarily change CWD so load_dotenv finds the file
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            # Clear any pre-existing values
            os.environ.pop("TEST_NEXUS_VAR_A", None)
            os.environ.pop("TEST_NEXUS_VAR_B", None)

            load_dotenv()

            assert os.environ.get("TEST_NEXUS_VAR_A") == "hello"
            assert os.environ.get("TEST_NEXUS_VAR_B") == "world"
        finally:
            os.chdir(old_cwd)
            os.environ.pop("TEST_NEXUS_VAR_A", None)
            os.environ.pop("TEST_NEXUS_VAR_B", None)

    def test_does_not_overwrite_existing(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("TEST_NEXUS_EXIST=new_value\n")

        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            os.environ["TEST_NEXUS_EXIST"] = "original"

            load_dotenv()

            assert os.environ["TEST_NEXUS_EXIST"] == "original"
        finally:
            os.chdir(old_cwd)
            os.environ.pop("TEST_NEXUS_EXIST", None)

    def test_skips_comments_and_blanks(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("# This is a comment\n\nTEST_NEXUS_ONLY=yes\n   \n")

        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            os.environ.pop("TEST_NEXUS_ONLY", None)

            load_dotenv()

            assert os.environ.get("TEST_NEXUS_ONLY") == "yes"
        finally:
            os.chdir(old_cwd)
            os.environ.pop("TEST_NEXUS_ONLY", None)


class TestCreateStateManager:

    def test_local_mode(self, tmp_path):
        args = argparse.Namespace(mode="local", state_dir=str(tmp_path / "state"))
        mgr = create_state_manager(args)
        assert mgr.mode == "local"

    def test_testnet_mode_missing_key_exits(self):
        """Testnet mode without private key should exit."""
        args = argparse.Namespace(
            mode="testnet",
            private_key=None,
            agent_state=None,
            task_manager=None,
            rpc_url=None,
        )
        # Clear env to ensure no fallback
        old_pk = os.environ.pop("NEXUS_PRIVATE_KEY", None)
        try:
            with pytest.raises(SystemExit):
                create_state_manager(args)
        finally:
            if old_pk:
                os.environ["NEXUS_PRIVATE_KEY"] = old_pk


class TestAddStateManagerArgs:

    def test_adds_expected_args(self):
        parser = argparse.ArgumentParser()
        add_state_manager_args(parser)

        # Parse with defaults
        args = parser.parse_args([])
        assert args.mode == "local"
        assert args.private_key is None
        assert args.agent_state is None

    def test_accepts_testnet_mode(self):
        parser = argparse.ArgumentParser()
        add_state_manager_args(parser)
        args = parser.parse_args(["--mode", "testnet", "--private-key", "0xabc"])
        assert args.mode == "testnet"
        assert args.private_key == "0xabc"
