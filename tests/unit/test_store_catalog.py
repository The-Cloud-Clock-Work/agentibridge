"""Tests for Phase 5 store methods (memory, plans, history, codename index)."""

import json

import pytest

from agentibridge.store import SessionStore
from tests.conftest import make_history_entry, make_memory_file, make_plan_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _force_redis(store: SessionStore, fake_redis):
    store._redis = fake_redis
    store._redis_checked = True


def _force_no_redis(store: SessionStore):
    store._redis = None
    store._redis_checked = True


# ---------------------------------------------------------------------------
# Tests: Memory — Redis
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRedisMemory:
    def test_upsert_and_list_memory(self, mock_redis):
        store = SessionStore()
        _force_redis(store, mock_redis)

        mem = make_memory_file()
        store.upsert_memory_file(mem)

        results = store.list_memory_files()
        assert len(results) == 1
        assert results[0].filename == "MEMORY.md"
        assert results[0].project_path == "/home/user/dev/myapp"

    def test_upsert_and_get_memory(self, mock_redis):
        store = SessionStore()
        _force_redis(store, mock_redis)

        mem = make_memory_file()
        store.upsert_memory_file(mem)

        result = store.get_memory_file("-home-user-dev-myapp", "MEMORY.md")
        assert result is not None
        assert result.content == "# Project Memory\n\nKey decisions go here."

    def test_get_memory_nonexistent(self, mock_redis):
        store = SessionStore()
        _force_redis(store, mock_redis)

        result = store.get_memory_file("-nonexistent", "MEMORY.md")
        assert result is None

    def test_list_memory_with_project_filter(self, mock_redis):
        store = SessionStore()
        _force_redis(store, mock_redis)

        mem1 = make_memory_file(project_encoded="-home-user-dev-frontend", project_path="/home/user/dev/frontend")
        mem2 = make_memory_file(project_encoded="-home-user-dev-backend", project_path="/home/user/dev/backend")
        store.upsert_memory_file(mem1)
        store.upsert_memory_file(mem2)

        results = store.list_memory_files(project="frontend")
        assert len(results) == 1
        assert "frontend" in results[0].project_encoded

    def test_list_memory_empty(self, mock_redis):
        store = SessionStore()
        _force_redis(store, mock_redis)

        results = store.list_memory_files()
        assert results == []

    def test_upsert_memory_updates_existing(self, mock_redis):
        store = SessionStore()
        _force_redis(store, mock_redis)

        mem1 = make_memory_file(content="Version 1")
        store.upsert_memory_file(mem1)

        mem2 = make_memory_file(content="Version 2")
        store.upsert_memory_file(mem2)

        result = store.get_memory_file("-home-user-dev-myapp", "MEMORY.md")
        assert result.content == "Version 2"


# ---------------------------------------------------------------------------
# Tests: Memory — File fallback
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFileMemory:
    def test_list_memory_from_filesystem(self, temp_memory_dir):
        store = SessionStore()
        _force_no_redis(store)
        store._projects_dir = temp_memory_dir

        results = store.list_memory_files()
        assert len(results) == 3

    def test_list_memory_with_project_filter(self, temp_memory_dir):
        store = SessionStore()
        _force_no_redis(store)
        store._projects_dir = temp_memory_dir

        results = store.list_memory_files(project="myapp")
        assert len(results) == 2  # MEMORY.md + patterns.md

    def test_get_memory_from_filesystem(self, temp_memory_dir):
        store = SessionStore()
        _force_no_redis(store)
        store._projects_dir = temp_memory_dir

        result = store.get_memory_file("-home-user-dev-myapp", "MEMORY.md")
        assert result is not None
        assert "Key patterns here" in result.content

    def test_get_memory_nonexistent(self, temp_memory_dir):
        store = SessionStore()
        _force_no_redis(store)
        store._projects_dir = temp_memory_dir

        result = store.get_memory_file("-home-user-dev-myapp", "nonexistent.md")
        assert result is None


# ---------------------------------------------------------------------------
# Tests: Plans — Redis
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRedisPlans:
    def test_upsert_and_list_plans(self, mock_redis):
        store = SessionStore()
        _force_redis(store, mock_redis)

        plan = make_plan_file()
        store.upsert_plan(plan)

        results = store.list_plans()
        assert len(results) == 1
        assert results[0].codename == "fancy-coding-parrot"

    def test_upsert_agent_plan(self, mock_redis):
        store = SessionStore()
        _force_redis(store, mock_redis)

        # Main plan
        main = make_plan_file()
        store.upsert_plan(main)

        # Agent plan
        agent = make_plan_file(
            codename="fancy-coding-parrot-agent-abc123",
            filename="fancy-coding-parrot-agent-abc123.md",
            is_agent_plan=True,
            parent_codename="fancy-coding-parrot",
        )
        store.upsert_plan(agent)

        # list_plans without agent plans
        results = store.list_plans(include_agent_plans=False)
        assert len(results) == 1

        # list_plans with agent plans doesn't include agents in the main list
        # (agents are stored separately)
        results_all = store.list_plans(include_agent_plans=True)
        assert len(results_all) == 1  # Only main plans in the index

    def test_get_plan_with_agents(self, mock_redis):
        store = SessionStore()
        _force_redis(store, mock_redis)

        main = make_plan_file(content="Main plan content")
        store.upsert_plan(main)

        agent = make_plan_file(
            codename="fancy-coding-parrot-agent-abc123",
            is_agent_plan=True,
            parent_codename="fancy-coding-parrot",
            content="Agent plan content",
        )
        store.upsert_plan(agent)

        result = store.get_plan("fancy-coding-parrot", include_agent_plans=True)
        assert result is not None
        assert result["plan"].codename == "fancy-coding-parrot"
        assert len(result["agent_plans"]) == 1
        assert result["agent_plans"][0].codename == "fancy-coding-parrot-agent-abc123"

    def test_get_plan_nonexistent(self, mock_redis):
        store = SessionStore()
        _force_redis(store, mock_redis)

        result = store.get_plan("nonexistent-plan")
        assert result is None

    def test_list_plans_with_limit_offset(self, mock_redis):
        store = SessionStore()
        _force_redis(store, mock_redis)

        for i in range(5):
            plan = make_plan_file(
                codename=f"plan-{i:03d}",
                filename=f"plan-{i:03d}.md",
                last_modified=f"2025-06-01T{10 + i}:00:00+00:00",
            )
            store.upsert_plan(plan)

        results = store.list_plans(limit=2, offset=1)
        assert len(results) == 2

    def test_list_plans_codename_filter(self, mock_redis):
        store = SessionStore()
        _force_redis(store, mock_redis)

        store.upsert_plan(make_plan_file(codename="alpha-plan", filename="alpha-plan.md"))
        store.upsert_plan(make_plan_file(codename="beta-plan", filename="beta-plan.md"))

        results = store.list_plans(codename="alpha")
        assert len(results) == 1
        assert results[0].codename == "alpha-plan"


# ---------------------------------------------------------------------------
# Tests: Plans — File fallback
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFilePlans:
    def test_list_plans_from_filesystem(self, temp_plans_dir):
        store = SessionStore()
        _force_no_redis(store)
        store._plans_dir = temp_plans_dir

        results = store.list_plans()
        assert len(results) == 2  # Excludes agent plans by default

    def test_list_plans_includes_agents(self, temp_plans_dir):
        store = SessionStore()
        _force_no_redis(store)
        store._plans_dir = temp_plans_dir

        results = store.list_plans(include_agent_plans=True)
        assert len(results) == 3

    def test_get_plan_from_filesystem(self, temp_plans_dir):
        store = SessionStore()
        _force_no_redis(store)
        store._plans_dir = temp_plans_dir

        result = store.get_plan("fancy-coding-parrot")
        assert result is not None
        assert "# Plan: Fancy Coding Parrot" in result["plan"].content

    def test_get_plan_with_agents_from_filesystem(self, temp_plans_dir):
        store = SessionStore()
        _force_no_redis(store)
        store._plans_dir = temp_plans_dir

        result = store.get_plan("fancy-coding-parrot", include_agent_plans=True)
        assert result is not None
        assert len(result["agent_plans"]) == 1

    def test_get_plan_nonexistent(self, temp_plans_dir):
        store = SessionStore()
        _force_no_redis(store)
        store._plans_dir = temp_plans_dir

        result = store.get_plan("nonexistent")
        assert result is None


# ---------------------------------------------------------------------------
# Tests: History — Redis
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRedisHistory:
    def test_add_and_search_history(self, mock_redis):
        store = SessionStore()
        _force_redis(store, mock_redis)

        entries = [
            make_history_entry(display="Deploy Docker containers", session_id="s1"),
            make_history_entry(display="Fix authentication bug", session_id="s2"),
            make_history_entry(display="Docker compose setup", session_id="s3"),
        ]
        store.add_history_entries(entries)

        results, total = store.search_history(query="Docker")
        assert total == 2
        assert len(results) == 2

    def test_search_history_with_project_filter(self, mock_redis):
        store = SessionStore()
        _force_redis(store, mock_redis)

        entries = [
            make_history_entry(display="Fix bug", project="/home/user/dev/frontend"),
            make_history_entry(display="Fix bug", project="/home/user/dev/backend"),
        ]
        store.add_history_entries(entries)

        results, total = store.search_history(query="bug", project="frontend")
        assert total == 1

    def test_search_history_with_session_filter(self, mock_redis):
        store = SessionStore()
        _force_redis(store, mock_redis)

        entries = [
            make_history_entry(display="Task A", session_id="s1"),
            make_history_entry(display="Task B", session_id="s2"),
        ]
        store.add_history_entries(entries)

        results, total = store.search_history(session_id="s1")
        assert total == 1
        assert results[0].session_id == "s1"

    def test_search_history_empty(self, mock_redis):
        store = SessionStore()
        _force_redis(store, mock_redis)

        results, total = store.search_history(query="anything")
        assert total == 0
        assert results == []

    def test_search_history_limit_offset(self, mock_redis):
        store = SessionStore()
        _force_redis(store, mock_redis)

        entries = [make_history_entry(display=f"Entry {i}", session_id=f"s{i}") for i in range(10)]
        store.add_history_entries(entries)

        results, total = store.search_history(limit=3, offset=2)
        assert total == 10
        assert len(results) == 3

    def test_add_history_empty_list(self, mock_redis):
        store = SessionStore()
        _force_redis(store, mock_redis)

        store.add_history_entries([])
        results, total = store.search_history()
        assert total == 0


# ---------------------------------------------------------------------------
# Tests: History — File fallback
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFileHistory:
    def test_search_history_from_file(self, temp_history_file):
        store = SessionStore()
        _force_no_redis(store)
        store._history_file = temp_history_file

        results, total = store.search_history(query="Docker")
        assert total == 1
        assert "Docker" in results[0].display

    def test_search_history_with_project_filter_from_file(self, temp_history_file):
        store = SessionStore()
        _force_no_redis(store)
        store._history_file = temp_history_file

        results, total = store.search_history(project="backend")
        assert total == 2  # "Migrate database" + "Fix auth bug"

    def test_search_history_all_entries(self, temp_history_file):
        store = SessionStore()
        _force_no_redis(store)
        store._history_file = temp_history_file

        results, total = store.search_history()
        assert total == 5


# ---------------------------------------------------------------------------
# Tests: Codename index — Redis
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRedisCodenameIndex:
    def test_upsert_and_get_codename(self, mock_redis):
        store = SessionStore()
        _force_redis(store, mock_redis)

        store.upsert_codename("fancy-fish", "session-001", "-home-user-dev-app")
        results = store.get_sessions_for_codename("fancy-fish")
        assert len(results) == 1
        assert results[0]["session_id"] == "session-001"
        assert results[0]["project"] == "/home/user/dev/app"

    def test_upsert_codename_appends_sessions(self, mock_redis):
        store = SessionStore()
        _force_redis(store, mock_redis)

        store.upsert_codename("fancy-fish", "session-001", "-home-user-dev-app")
        store.upsert_codename("fancy-fish", "session-002", "-home-user-dev-app")

        results = store.get_sessions_for_codename("fancy-fish")
        assert len(results) == 2
        ids = {r["session_id"] for r in results}
        assert "session-001" in ids
        assert "session-002" in ids

    def test_upsert_codename_deduplicates(self, mock_redis):
        store = SessionStore()
        _force_redis(store, mock_redis)

        store.upsert_codename("fancy-fish", "session-001", "-home-user-dev-app")
        store.upsert_codename("fancy-fish", "session-001", "-home-user-dev-app")

        results = store.get_sessions_for_codename("fancy-fish")
        assert len(results) == 1

    def test_get_sessions_for_nonexistent_codename(self, mock_redis):
        store = SessionStore()
        _force_redis(store, mock_redis)

        results = store.get_sessions_for_codename("nonexistent")
        assert results == []


# ---------------------------------------------------------------------------
# Tests: Codename index — File fallback
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFileCodenameIndex:
    def test_file_fallback_scans_transcripts(self, tmp_path):
        """Test that file fallback reads slug from first JSONL line."""
        store = SessionStore()
        _force_no_redis(store)
        store._projects_dir = tmp_path

        # Create a project dir with a transcript that has a slug
        proj = tmp_path / "-home-user-dev-myapp"
        proj.mkdir()
        transcript = proj / "session-abc.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "user",
                    "timestamp": "2025-06-01T10:00:00Z",
                    "slug": "fancy-fish",
                    "message": {"role": "user", "content": "Hello"},
                }
            )
            + "\n"
        )

        results = store.get_sessions_for_codename("fancy-fish")
        assert len(results) == 1
        assert results[0]["session_id"] == "session-abc"

    def test_file_fallback_no_match(self, tmp_path):
        store = SessionStore()
        _force_no_redis(store)
        store._projects_dir = tmp_path

        proj = tmp_path / "-home-user-dev-app"
        proj.mkdir()
        transcript = proj / "session-001.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "user",
                    "timestamp": "2025-06-01T10:00:00Z",
                    "slug": "other-name",
                    "message": {"role": "user", "content": "Hello"},
                }
            )
            + "\n"
        )

        results = store.get_sessions_for_codename("fancy-fish")
        assert results == []
