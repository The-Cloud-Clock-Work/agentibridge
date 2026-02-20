"""Tests for agentibridge.llm_client module."""

from unittest.mock import MagicMock, patch

import pytest

from agentibridge import llm_client


@pytest.mark.unit
class TestConfiguration:
    def test_is_configured_true(self, monkeypatch):
        monkeypatch.setenv("LLM_API_BASE", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_API_KEY", "sk-test")
        assert llm_client.is_configured() is True

    def test_is_configured_false_no_base(self, monkeypatch):
        monkeypatch.delenv("LLM_API_BASE", raising=False)
        monkeypatch.setenv("LLM_API_KEY", "sk-test")
        assert llm_client.is_configured() is False

    def test_is_configured_false_no_key(self, monkeypatch):
        monkeypatch.setenv("LLM_API_BASE", "https://api.example.com/v1")
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        assert llm_client.is_configured() is False

    def test_is_embed_configured(self, monkeypatch):
        monkeypatch.setenv("LLM_API_BASE", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_API_KEY", "sk-test")
        monkeypatch.setenv("LLM_EMBED_MODEL", "text-embedding-3-small")
        assert llm_client.is_embed_configured() is True

    def test_is_embed_configured_false(self, monkeypatch):
        monkeypatch.delenv("LLM_API_BASE", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        assert llm_client.is_embed_configured() is False


@pytest.mark.unit
class TestEmbedText:
    def test_success(self, monkeypatch):
        monkeypatch.setenv("LLM_API_BASE", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_API_KEY", "sk-test")
        monkeypatch.setenv("LLM_EMBED_MODEL", "text-embedding-3-small")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [{"embedding": [0.1, 0.2, 0.3]}]
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            result = llm_client.embed_text("hello world")

        assert result == [0.1, 0.2, 0.3]
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert "embeddings" in call_kwargs[0][0]
        assert call_kwargs[1]["json"]["model"] == "text-embedding-3-small"

    def test_missing_config(self, monkeypatch):
        monkeypatch.delenv("LLM_API_BASE", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)

        with pytest.raises(RuntimeError, match="LLM_API_BASE"):
            llm_client.embed_text("hello")

    def test_custom_model(self, monkeypatch):
        monkeypatch.setenv("LLM_API_BASE", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_API_KEY", "sk-test")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": [{"embedding": [0.5]}]}
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            llm_client.embed_text("test", model="custom-model")

        assert mock_post.call_args[1]["json"]["model"] == "custom-model"


@pytest.mark.unit
class TestChatCompletion:
    def test_success(self, monkeypatch):
        monkeypatch.setenv("LLM_API_BASE", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_API_KEY", "sk-test")
        monkeypatch.setenv("LLM_CHAT_MODEL", "gpt-4")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "Hello there!"}}]
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            result = llm_client.chat_completion("Say hello")

        assert result == "Hello there!"
        assert "chat/completions" in mock_post.call_args[0][0]

    def test_missing_config(self, monkeypatch):
        monkeypatch.delenv("LLM_API_BASE", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)

        with pytest.raises(RuntimeError, match="LLM_API_BASE"):
            llm_client.chat_completion("hello")

    def test_missing_model(self, monkeypatch):
        monkeypatch.setenv("LLM_API_BASE", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_API_KEY", "sk-test")
        monkeypatch.delenv("LLM_CHAT_MODEL", raising=False)

        with pytest.raises(RuntimeError, match="LLM_CHAT_MODEL"):
            llm_client.chat_completion("hello")

    def test_custom_model(self, monkeypatch):
        monkeypatch.setenv("LLM_API_BASE", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_API_KEY", "sk-test")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "ok"}}]
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            llm_client.chat_completion("test", model="claude-3-opus")

        assert mock_post.call_args[1]["json"]["model"] == "claude-3-opus"
