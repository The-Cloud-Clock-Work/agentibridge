"""Tests for agentic_bridge.completions module."""

from unittest.mock import MagicMock, patch

import pytest

from agentic_bridge.completions import CompletionsClient, CompletionResult, call_completions


@pytest.mark.unit
class TestCompletionResult:
    def test_to_dict(self):
        result = CompletionResult(success=True, exit_code=0, duration_ms=100)
        d = result.to_dict()
        assert d["success"] is True
        assert d["exit_code"] == 0
        assert d["duration_ms"] == 100

    def test_defaults(self):
        result = CompletionResult(success=False)
        assert result.timed_out is False
        assert result.is_async is False
        assert result.parsed_output is None


@pytest.mark.unit
class TestCompletionsClient:
    def setup_method(self):
        CompletionsClient.reset()

    def teardown_method(self):
        CompletionsClient.reset()

    def test_singleton(self):
        c1 = CompletionsClient.get_client()
        c2 = CompletionsClient.get_client()
        assert c1 is c2

    def test_reset(self):
        c1 = CompletionsClient.get_client()
        CompletionsClient.reset()
        c2 = CompletionsClient.get_client()
        assert c1 is not c2

    def test_custom_config(self, monkeypatch):
        monkeypatch.setenv("AGENT_API_ENDPOINT", "http://custom:9000")
        monkeypatch.setenv("AGENT_API_KEY", "secret")
        monkeypatch.setenv("AGENT_API_TIMEOUT", "60")

        client = CompletionsClient()
        assert client.base_url == "http://custom:9000"
        assert client.api_key == "secret"
        assert client.timeout == 60.0

    def test_default_config(self, monkeypatch):
        monkeypatch.delenv("AGENT_API_ENDPOINT", raising=False)
        monkeypatch.delenv("AGENT_API_KEY", raising=False)
        monkeypatch.delenv("AGENT_API_TIMEOUT", raising=False)

        client = CompletionsClient()
        assert client.base_url == "http://localhost:8000"
        assert client.api_key == ""
        assert client.timeout == 300.0

    def test_config_not_loaded_at_import_time(self, monkeypatch):
        """Bug fix #11: env vars should be read lazily, not at import time."""
        monkeypatch.setenv("AGENT_API_ENDPOINT", "http://lazy:1234")
        client = CompletionsClient()
        assert client.base_url == "http://lazy:1234"

    def test_call_success(self):

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "exit_code": 0,
            "duration_ms": 500,
            "timed_out": False,
            "parsed_output": {"result": "done"},
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.Client") as MockClient:
            mock_client_instance = MagicMock()
            mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
            mock_client_instance.__exit__ = MagicMock(return_value=False)
            mock_client_instance.post.return_value = mock_response
            MockClient.return_value = mock_client_instance

            client = CompletionsClient(base_url="http://test:8000")
            result = client.call("test prompt")

        assert result.success is True
        assert result.exit_code == 0
        assert result.parsed_output == {"result": "done"}

    def test_call_async_202(self):
        mock_response = MagicMock()
        mock_response.status_code = 202

        with patch("httpx.Client") as MockClient:
            mock_client_instance = MagicMock()
            mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
            mock_client_instance.__exit__ = MagicMock(return_value=False)
            mock_client_instance.post.return_value = mock_response
            MockClient.return_value = mock_client_instance

            client = CompletionsClient(base_url="http://test:8000")
            result = client.call("test", wait=False)

        assert result.success is True
        assert result.is_async is True

    def test_call_timeout(self):
        import httpx

        with patch("httpx.Client") as MockClient:
            mock_client_instance = MagicMock()
            mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
            mock_client_instance.__exit__ = MagicMock(return_value=False)
            mock_client_instance.post.side_effect = httpx.TimeoutException("timed out")
            MockClient.return_value = mock_client_instance

            client = CompletionsClient(base_url="http://test:8000", timeout=5)
            result = client.call("test")

        assert result.success is False
        assert result.timed_out is True

    def test_call_http_error(self):
        import httpx

        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch("httpx.Client") as MockClient:
            mock_client_instance = MagicMock()
            mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
            mock_client_instance.__exit__ = MagicMock(return_value=False)
            mock_client_instance.post.side_effect = httpx.HTTPStatusError(
                "Server Error",
                request=MagicMock(),
                response=mock_response,
            )
            MockClient.return_value = mock_client_instance

            client = CompletionsClient(base_url="http://test:8000")
            result = client.call("test")

        assert result.success is False
        assert "500" in result.error


@pytest.mark.unit
class TestCallCompletions:
    def setup_method(self):
        CompletionsClient.reset()

    def teardown_method(self):
        CompletionsClient.reset()

    def test_convenience_function(self):
        with patch.object(CompletionsClient, "call") as mock_call:
            mock_call.return_value = CompletionResult(success=True)
            result = call_completions("test prompt", command="thinkhard")

        assert result.success is True
        mock_call.assert_called_once()
