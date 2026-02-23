"""Tests for Phase 5 MCP tools in agentibridge.server."""

import json
from unittest.mock import MagicMock

import pytest

from tests.conftest import make_history_entry, make_memory_file, make_plan_file


def _mock_collector():
    collector = MagicMock()
    collector.collect_once.return_value = {
        "files_scanned": 5,
        "sessions_updated": 2,
        "entries_added": 10,
        "memory_files_indexed": 3,
        "plans_indexed": 5,
        "history_entries_added": 10,
        "duration_ms": 50,
    }
    return collector


# ---------------------------------------------------------------------------
# Tests: list_memory_files
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListMemoryFiles:
    def test_success(self, reset_singletons):
        mem = make_memory_file()
        store = MagicMock()
        store.list_memory_files.return_value = [mem]
        collector = _mock_collector()

        import agentibridge.server as srv

        srv._store = store
        srv._collector = collector

        result = json.loads(srv.list_memory_files())

        assert result["success"] is True
        assert result["count"] == 1
        assert result["files"][0]["filename"] == "MEMORY.md"
        assert result["files"][0]["project_path"] == "/home/user/dev/myapp"

    def test_with_project_filter(self, reset_singletons):
        store = MagicMock()
        store.list_memory_files.return_value = []
        collector = _mock_collector()

        import agentibridge.server as srv

        srv._store = store
        srv._collector = collector

        result = json.loads(srv.list_memory_files(project="myapp"))

        assert result["success"] is True
        store.list_memory_files.assert_called_once_with(project="myapp")

    def test_empty_project_filter_passes_none(self, reset_singletons):
        store = MagicMock()
        store.list_memory_files.return_value = []
        collector = _mock_collector()

        import agentibridge.server as srv

        srv._store = store
        srv._collector = collector

        srv.list_memory_files(project="")
        store.list_memory_files.assert_called_once_with(project=None)

    def test_error_handling(self, reset_singletons):
        store = MagicMock()
        store.list_memory_files.side_effect = Exception("fail")
        collector = _mock_collector()

        import agentibridge.server as srv

        srv._store = store
        srv._collector = collector

        result = json.loads(srv.list_memory_files())
        assert result["success"] is False
        assert "fail" in result["error"]


# ---------------------------------------------------------------------------
# Tests: get_memory_file
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetMemoryFile:
    def test_success(self, reset_singletons):
        mem = make_memory_file(content="# Memory\n\nImportant stuff")
        store = MagicMock()
        store.get_memory_file.return_value = mem
        collector = _mock_collector()

        import agentibridge.server as srv

        srv._store = store
        srv._collector = collector

        result = json.loads(srv.get_memory_file(project="-home-user-dev-myapp"))

        assert result["success"] is True
        assert result["content"] == "# Memory\n\nImportant stuff"
        assert result["filename"] == "MEMORY.md"

    def test_not_found(self, reset_singletons):
        store = MagicMock()
        store.get_memory_file.return_value = None
        collector = _mock_collector()

        import agentibridge.server as srv

        srv._store = store
        srv._collector = collector

        result = json.loads(srv.get_memory_file(project="-nonexistent", filename="MEMORY.md"))

        assert result["success"] is False
        assert "not found" in result["error"]

    def test_error_handling(self, reset_singletons):
        store = MagicMock()
        store.get_memory_file.side_effect = Exception("read error")
        collector = _mock_collector()

        import agentibridge.server as srv

        srv._store = store
        srv._collector = collector

        result = json.loads(srv.get_memory_file(project="-test"))
        assert result["success"] is False


# ---------------------------------------------------------------------------
# Tests: list_plans
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListPlans:
    def test_success(self, reset_singletons):
        plan = make_plan_file(session_ids=["s1", "s2"], project_path="/home/user/dev/app")
        store = MagicMock()
        store.list_plans.return_value = [plan]
        collector = _mock_collector()

        import agentibridge.server as srv

        srv._store = store
        srv._collector = collector

        result = json.loads(srv.list_plans())

        assert result["success"] is True
        assert result["count"] == 1
        assert result["plans"][0]["codename"] == "fancy-coding-parrot"
        assert result["plans"][0]["session_ids"] == ["s1", "s2"]

    def test_with_filters(self, reset_singletons):
        store = MagicMock()
        store.list_plans.return_value = []
        collector = _mock_collector()

        import agentibridge.server as srv

        srv._store = store
        srv._collector = collector

        srv.list_plans(project="myapp", codename="fancy", limit=10, offset=5, include_agent_plans=True)

        store.list_plans.assert_called_once_with(
            project="myapp",
            codename="fancy",
            limit=10,
            offset=5,
            include_agent_plans=True,
        )

    def test_error_handling(self, reset_singletons):
        store = MagicMock()
        store.list_plans.side_effect = Exception("error")
        collector = _mock_collector()

        import agentibridge.server as srv

        srv._store = store
        srv._collector = collector

        result = json.loads(srv.list_plans())
        assert result["success"] is False


# ---------------------------------------------------------------------------
# Tests: get_plan
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetPlan:
    def test_success(self, reset_singletons):
        plan = make_plan_file(content="# Plan content", session_ids=["s1"])
        store = MagicMock()
        store.get_plan.return_value = {"plan": plan, "agent_plans": []}
        collector = _mock_collector()

        import agentibridge.server as srv

        srv._store = store
        srv._collector = collector

        result = json.loads(srv.get_plan(codename="fancy-coding-parrot"))

        assert result["success"] is True
        assert result["codename"] == "fancy-coding-parrot"
        assert "# Plan content" in result["content"]
        assert result["agent_plans"] == []

    def test_with_agent_plans(self, reset_singletons):
        main = make_plan_file(content="Main plan")
        agent = make_plan_file(
            codename="fancy-coding-parrot-agent-abc",
            content="Agent plan",
            is_agent_plan=True,
        )
        store = MagicMock()
        store.get_plan.return_value = {"plan": main, "agent_plans": [agent]}
        collector = _mock_collector()

        import agentibridge.server as srv

        srv._store = store
        srv._collector = collector

        result = json.loads(srv.get_plan(codename="fancy-coding-parrot", include_agent_plans=True))

        assert result["success"] is True
        assert len(result["agent_plans"]) == 1
        assert result["agent_plans"][0]["codename"] == "fancy-coding-parrot-agent-abc"

    def test_not_found(self, reset_singletons):
        store = MagicMock()
        store.get_plan.return_value = None
        collector = _mock_collector()

        import agentibridge.server as srv

        srv._store = store
        srv._collector = collector

        result = json.loads(srv.get_plan(codename="nonexistent"))

        assert result["success"] is False
        assert "not found" in result["error"]

    def test_error_handling(self, reset_singletons):
        store = MagicMock()
        store.get_plan.side_effect = Exception("plan error")
        collector = _mock_collector()

        import agentibridge.server as srv

        srv._store = store
        srv._collector = collector

        result = json.loads(srv.get_plan(codename="test"))
        assert result["success"] is False


# ---------------------------------------------------------------------------
# Tests: search_history
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSearchHistory:
    def test_success(self, reset_singletons):
        entry = make_history_entry()
        store = MagicMock()
        store.search_history.return_value = ([entry], 1)
        collector = _mock_collector()

        import agentibridge.server as srv

        srv._store = store
        srv._collector = collector

        result = json.loads(srv.search_history(query="Docker"))

        assert result["success"] is True
        assert result["total"] == 1
        assert result["count"] == 1
        assert result["entries"][0]["display"] == "Help me create a Docker Compose setup"

    def test_with_filters(self, reset_singletons):
        store = MagicMock()
        store.search_history.return_value = ([], 0)
        collector = _mock_collector()

        import agentibridge.server as srv

        srv._store = store
        srv._collector = collector

        srv.search_history(
            query="test",
            project="myapp",
            session_id="s1",
            limit=10,
            offset=5,
            since="2025-06-01T00:00:00Z",
        )

        store.search_history.assert_called_once_with(
            query="test",
            project="myapp",
            session_id="s1",
            limit=10,
            offset=5,
            since="2025-06-01T00:00:00Z",
        )

    def test_empty_filters_pass_none(self, reset_singletons):
        store = MagicMock()
        store.search_history.return_value = ([], 0)
        collector = _mock_collector()

        import agentibridge.server as srv

        srv._store = store
        srv._collector = collector

        srv.search_history()

        store.search_history.assert_called_once_with(
            query="",
            project=None,
            session_id=None,
            limit=20,
            offset=0,
            since="",
        )

    def test_error_handling(self, reset_singletons):
        store = MagicMock()
        store.search_history.side_effect = Exception("history error")
        collector = _mock_collector()

        import agentibridge.server as srv

        srv._store = store
        srv._collector = collector

        result = json.loads(srv.search_history())
        assert result["success"] is False
