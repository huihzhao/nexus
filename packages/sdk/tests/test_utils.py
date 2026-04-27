"""Tests for nexus_core.utils — shared utilities."""

import json
import os
import tempfile
import pytest

from nexus_core.utils import robust_json_parse, load_dotenv
from nexus_core.utils.json_parse import extract_balanced


# ═══════════════════════════════════════════════════
# robust_json_parse
# ═══════════════════════════════════════════════════

class TestRobustJsonParse:
    """Tests for LLM output JSON parsing."""

    def test_valid_json_passthrough(self):
        assert robust_json_parse('{"key": "value"}') == {"key": "value"}

    def test_valid_array(self):
        assert robust_json_parse('[1, 2, 3]') == [1, 2, 3]

    def test_empty_input_raises(self):
        with pytest.raises(json.JSONDecodeError):
            robust_json_parse("")

    def test_whitespace_only_raises(self):
        with pytest.raises(json.JSONDecodeError):
            robust_json_parse("   \n  ")

    def test_strips_markdown_fences(self):
        raw = '```json\n{"name": "test"}\n```'
        assert robust_json_parse(raw) == {"name": "test"}

    def test_strips_markdown_fences_no_lang(self):
        raw = '```\n[1, 2]\n```'
        assert robust_json_parse(raw) == [1, 2]

    def test_trailing_comma_object(self):
        raw = '{"a": 1, "b": 2,}'
        assert robust_json_parse(raw) == {"a": 1, "b": 2}

    def test_trailing_comma_array(self):
        raw = '[1, 2, 3,]'
        assert robust_json_parse(raw) == [1, 2, 3]

    def test_prose_before_json(self):
        raw = 'Here is the result:\n{"answer": 42}'
        assert robust_json_parse(raw) == {"answer": 42}

    def test_prose_after_json(self):
        raw = '{"answer": 42}\nHope this helps!'
        assert robust_json_parse(raw) == {"answer": 42}

    def test_prose_around_array(self):
        raw = 'The memories are:\n[{"content": "likes sushi"}]\nDone.'
        result = robust_json_parse(raw)
        assert result == [{"content": "likes sushi"}]

    def test_truncated_array(self):
        """Truncated LLM output — last item incomplete."""
        raw = '[{"content": "A"}, {"content": "B"}, {"content": "C'
        result = robust_json_parse(raw)
        assert len(result) == 2  # recovers first 2 complete items

    def test_nested_brackets(self):
        raw = '{"skills": [{"name": "python"}, {"name": "rust"}]}'
        result = robust_json_parse(raw)
        assert result["skills"][1]["name"] == "rust"

    def test_object_priority_over_array(self):
        """When both { and [ appear, prioritize whichever comes first."""
        raw = '{"items": [1, 2]}'
        result = robust_json_parse(raw)
        assert "items" in result

    def test_unfixable_raises(self):
        with pytest.raises(json.JSONDecodeError):
            robust_json_parse("this is not json at all")


class TestExtractBalanced:
    """Tests for bracket extraction."""

    def test_simple_object(self):
        assert extract_balanced('{"a": 1}', "{", "}") == '{"a": 1}'

    def test_nested(self):
        text = '{"a": {"b": 1}}'
        assert extract_balanced(text, "{", "}") == text

    def test_with_prefix(self):
        text = 'prefix {"a": 1} suffix'
        assert extract_balanced(text, "{", "}") == '{"a": 1}'

    def test_no_match(self):
        assert extract_balanced("no brackets here", "{", "}") is None

    def test_unclosed(self):
        result = extract_balanced('{"a": 1', "{", "}")
        assert result == '{"a": 1'  # returns from open to end


# ═══════════════════════════════════════════════════
# load_dotenv
# ═══════════════════════════════════════════════════

class TestLoadDotenv:
    """Tests for .env file loading."""

    def test_loads_env_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write("TEST_UTIL_VAR_1=hello\nTEST_UTIL_VAR_2=world\n")
            f.flush()
            path = f.name

        try:
            # Clean state
            os.environ.pop("TEST_UTIL_VAR_1", None)
            os.environ.pop("TEST_UTIL_VAR_2", None)

            result = load_dotenv(path)
            assert result == path
            assert os.environ.get("TEST_UTIL_VAR_1") == "hello"
            assert os.environ.get("TEST_UTIL_VAR_2") == "world"
        finally:
            os.environ.pop("TEST_UTIL_VAR_1", None)
            os.environ.pop("TEST_UTIL_VAR_2", None)
            os.unlink(path)

    def test_does_not_override_existing(self):
        os.environ["TEST_UTIL_EXISTING"] = "original"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write("TEST_UTIL_EXISTING=overwritten\n")
            f.flush()
            path = f.name

        try:
            load_dotenv(path)
            assert os.environ["TEST_UTIL_EXISTING"] == "original"
        finally:
            os.environ.pop("TEST_UTIL_EXISTING", None)
            os.unlink(path)

    def test_skips_comments_and_blanks(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write("# comment\n\nTEST_UTIL_CLEAN=yes\n")
            f.flush()
            path = f.name

        try:
            os.environ.pop("TEST_UTIL_CLEAN", None)
            load_dotenv(path)
            assert os.environ.get("TEST_UTIL_CLEAN") == "yes"
        finally:
            os.environ.pop("TEST_UTIL_CLEAN", None)
            os.unlink(path)

    def test_strips_quotes(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write('TEST_UTIL_QUOTED="hello world"\n')
            f.flush()
            path = f.name

        try:
            os.environ.pop("TEST_UTIL_QUOTED", None)
            load_dotenv(path)
            assert os.environ.get("TEST_UTIL_QUOTED") == "hello world"
        finally:
            os.environ.pop("TEST_UTIL_QUOTED", None)
            os.unlink(path)

    def test_missing_file_returns_none(self):
        result = load_dotenv("/nonexistent/.env")
        assert result is None
