"""Tests for startup_checks.py"""

from sculptor.startup_checks import is_valid_anthropiclike_api_key


class TestIsValidAnthropiclikeApiKey:
    """Tests for is_valid_anthropiclike_api_key function"""

    def test_valid_anthropic_key(self) -> None:
        """Test that Anthropic API keys are accepted"""
        assert is_valid_anthropiclike_api_key("sk-ant-api03-valid-key-here")
        assert is_valid_anthropiclike_api_key("sk-ant-1234567890")

    def test_valid_non_anthropic_keys(self) -> None:
        """Test that non-Anthropic keys are accepted"""
        assert is_valid_anthropiclike_api_key("sk-openai-1234567890")
        assert is_valid_anthropiclike_api_key("some-other-key")
        assert is_valid_anthropiclike_api_key("1234567890")
        assert is_valid_anthropiclike_api_key("custom-proxy-key")

    def test_none_key(self) -> None:
        """Test that None is rejected"""
        assert not is_valid_anthropiclike_api_key(None)

    def test_empty_string(self) -> None:
        """Test that empty string is rejected"""
        assert not is_valid_anthropiclike_api_key("")

    def test_non_ascii_key(self) -> None:
        """Test that non-ASCII characters are rejected"""
        assert not is_valid_anthropiclike_api_key("key-with-émoji-🔑")
        assert not is_valid_anthropiclike_api_key("key-with-中文")
        assert not is_valid_anthropiclike_api_key("sk-ant-key-with-émoji")
