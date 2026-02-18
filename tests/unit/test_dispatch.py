"""Tests for agentic_bridge.dispatch module."""

from unittest.mock import MagicMock, patch

import pytest

from agentic_bridge.dispatch import restore_session_context, dispatch_task
from tests.conftest import make_entry, make_meta


@pytest.mark.unit
class TestRestoreSessionContext:
    def test_formats_context(self):
        meta = make_meta(
            session_id="s1",
            project_path="/home/user/dev/app",
            git_branch="main",
            summary="Built REST API",
        )
        entries = [
            make_entry("user", content="Create API"),
            make_entry("assistant", content="Created endpoints", tool_names=["Write"]),
        ]

        mock_store = MagicMock()
        mock_store.get_session_meta.return_value = meta
        mock_store.get_session_entries.return_value = entries

        with patch("agentic_bridge.store.SessionStore", return_value=mock_store):
            context = restore_session_context("s1", last_n=20)

        assert "RESTORED SESSION CONTEXT" in context
        assert "END OF RESTORED CONTEXT" in context
        assert "/home/user/dev/app" in context
        assert "main" in context
        assert "[USER]" in context
        assert "[ASSISTANT]" in context
        assert "Create API" in context
        assert "Created endpoints" in context
        assert "(tools: Write)" in context

    def test_missing_session_raises(self):
        mock_store = MagicMock()
        mock_store.get_session_meta.return_value = None

        with patch("agentic_bridge.store.SessionStore", return_value=mock_store):
            with pytest.raises(ValueError, match="Session not found"):
                restore_session_context("nonexistent")

    def test_last_n_limits_entries(self):
        meta = make_meta()
        entries = [make_entry("user", content=f"Turn {i}") for i in range(20)]

        mock_store = MagicMock()
        mock_store.get_session_meta.return_value = meta
        mock_store.get_session_entries.return_value = entries

        with patch("agentic_bridge.store.SessionStore", return_value=mock_store):
            context = restore_session_context("s1", last_n=5)

        # Should contain turns 15-19 but not 0-14
        assert "Turn 19" in context
        assert "Turn 15" in context
        assert "Turn 0" not in context

    def test_summary_in_entries(self):
        meta = make_meta()
        entries = [
            make_entry("summary", content="Session summary here"),
        ]

        mock_store = MagicMock()
        mock_store.get_session_meta.return_value = meta
        mock_store.get_session_entries.return_value = entries

        with patch("agentic_bridge.store.SessionStore", return_value=mock_store):
            context = restore_session_context("s1")

        assert "[SUMMARY]" in context
        assert "Session summary here" in context


@pytest.mark.unit
class TestDispatchTask:
    def test_basic_dispatch(self):
        with patch("agentic_bridge.completions.CompletionsClient.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.call.return_value = MagicMock(
                success=True,
                exit_code=0,
                duration_ms=500,
                timed_out=False,
                parsed_output={"result": "done"},
                error=None,
            )
            mock_get.return_value = mock_client

            result = dispatch_task("Fix the bug")

            assert result["dispatched"] is True
            assert result["completed"] is True
            assert result["error"] is None

    def test_with_project(self):
        with patch("agentic_bridge.completions.CompletionsClient.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.call.return_value = MagicMock(
                success=True,
                exit_code=0,
                duration_ms=100,
                timed_out=False,
                parsed_output=None,
                error=None,
            )
            mock_get.return_value = mock_client

            result = dispatch_task("Fix bug", project="myapp")

            assert result["dispatched"] is True
            call_args = mock_client.call.call_args
            prompt = call_args[1]["prompt"]
            assert "Project: myapp" in prompt

    def test_with_session_context(self):
        mock_store = MagicMock()
        meta = make_meta()
        mock_store.get_session_meta.return_value = meta
        mock_store.get_session_entries.return_value = [
            make_entry("user", content="Previous work"),
        ]

        with (
            patch("agentic_bridge.store.SessionStore", return_value=mock_store),
            patch("agentic_bridge.completions.CompletionsClient.get_client") as mock_get,
        ):
            mock_client = MagicMock()
            mock_client.call.return_value = MagicMock(
                success=True,
                exit_code=0,
                duration_ms=100,
                timed_out=False,
                parsed_output=None,
                error=None,
            )
            mock_get.return_value = mock_client

            result = dispatch_task("Fix bug", session_id="s1")

            assert result["context_session"] == "s1"
            prompt = mock_client.call.call_args[1]["prompt"]
            assert "RESTORED SESSION CONTEXT" in prompt

    def test_context_restore_failure_graceful(self):
        mock_store = MagicMock()
        mock_store.get_session_meta.return_value = None

        with (
            patch("agentic_bridge.store.SessionStore", return_value=mock_store),
            patch("agentic_bridge.completions.CompletionsClient.get_client") as mock_get,
        ):
            mock_client = MagicMock()
            mock_client.call.return_value = MagicMock(
                success=True,
                exit_code=0,
                duration_ms=100,
                timed_out=False,
                parsed_output=None,
                error=None,
            )
            mock_get.return_value = mock_client

            result = dispatch_task("Fix bug", session_id="bad-id")

            # Should still dispatch, just with error note in prompt
            assert result["dispatched"] is True
            prompt = mock_client.call.call_args[1]["prompt"]
            assert "Failed to restore" in prompt

    def test_api_failure(self):
        with patch("agentic_bridge.completions.CompletionsClient.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.call.return_value = MagicMock(
                success=False,
                exit_code=1,
                duration_ms=100,
                timed_out=False,
                parsed_output=None,
                error="API error",
            )
            mock_get.return_value = mock_client

            result = dispatch_task("Fix bug")

            assert result["completed"] is False
            assert result["error"] == "API error"
