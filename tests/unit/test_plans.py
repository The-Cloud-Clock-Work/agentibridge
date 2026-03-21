"""Unit tests for agentibridge.plans — dispatch planning."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import agentibridge.plans as plans_mod
from agentibridge.claude_runner import ClaudeResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plan_data(plan_id="test-plan-001", status="planning", **overrides):
    """Create a plan data dict with sensible defaults."""
    data = {
        "plan_id": plan_id,
        "status": status,
        "task": "Add error handling to the API",
        "repo_url": "",
        "content": "",
        "created_at": "2025-06-01T10:00:00+00:00",
        "completed_at": None,
        "planning_job_id": plan_id,
        "execution_job_id": "",
        "error": None,
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# Storage tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPlanStorage:
    """Tests for _write_plan / get_plan_status with Redis and file fallback."""

    def test_write_and_read_file_fallback(self, tmp_path):
        original = plans_mod._PLANS_DIR
        plans_mod._PLANS_DIR = tmp_path / "plans"
        try:
            with patch("agentibridge.plans.get_redis", return_value=None):
                data = _make_plan_data()
                plans_mod._write_plan("test-plan-001", data)

                result = plans_mod.get_plan_status("test-plan-001")
                assert result is not None
                assert result["plan_id"] == "test-plan-001"
                assert result["status"] == "planning"
                assert result["task"] == "Add error handling to the API"
        finally:
            plans_mod._PLANS_DIR = original

    def test_write_and_read_redis(self, tmp_path):
        original = plans_mod._PLANS_DIR
        plans_mod._PLANS_DIR = tmp_path / "plans"
        try:
            import fakeredis

            fake_r = fakeredis.FakeRedis(decode_responses=True)
            with patch("agentibridge.plans.get_redis", return_value=fake_r):
                data = _make_plan_data()
                plans_mod._write_plan("test-plan-001", data)

                result = plans_mod.get_plan_status("test-plan-001")
                assert result is not None
                assert result["plan_id"] == "test-plan-001"
                assert result["status"] == "planning"
        finally:
            plans_mod._PLANS_DIR = original

    def test_get_nonexistent_plan(self, tmp_path):
        original = plans_mod._PLANS_DIR
        plans_mod._PLANS_DIR = tmp_path / "plans"
        try:
            with patch("agentibridge.plans.get_redis", return_value=None):
                result = plans_mod.get_plan_status("nonexistent")
                assert result is None
        finally:
            plans_mod._PLANS_DIR = original

    def test_no_ttl_on_redis_keys(self, tmp_path):
        original = plans_mod._PLANS_DIR
        plans_mod._PLANS_DIR = tmp_path / "plans"
        try:
            import fakeredis

            fake_r = fakeredis.FakeRedis(decode_responses=True)
            with patch("agentibridge.plans.get_redis", return_value=fake_r):
                data = _make_plan_data()
                plans_mod._write_plan("test-plan-001", data)

                ttl = fake_r.ttl("agentibridge:sb:plan:test-plan-001")
                assert ttl == -1  # No expiration
        finally:
            plans_mod._PLANS_DIR = original


@pytest.mark.unit
class TestListPlans:
    """Tests for list_plans() with Redis and file fallback."""

    def test_list_all_file_fallback(self, tmp_path):
        original = plans_mod._PLANS_DIR
        plans_mod._PLANS_DIR = tmp_path / "plans"
        try:
            with patch("agentibridge.plans.get_redis", return_value=None):
                plans_mod._write_plan("p1", _make_plan_data("p1", status="ready"))
                plans_mod._write_plan("p2", _make_plan_data("p2", status="planning"))

                result = plans_mod.list_plans()
                assert len(result) == 2
        finally:
            plans_mod._PLANS_DIR = original

    def test_list_excludes_content(self, tmp_path):
        original = plans_mod._PLANS_DIR
        plans_mod._PLANS_DIR = tmp_path / "plans"
        try:
            with patch("agentibridge.plans.get_redis", return_value=None):
                data = _make_plan_data("p1", content="# Big plan\nLots of content")
                plans_mod._write_plan("p1", data)

                result = plans_mod.list_plans()
                assert len(result) == 1
                assert "content" not in result[0]
        finally:
            plans_mod._PLANS_DIR = original

    def test_list_filter_by_status(self, tmp_path):
        original = plans_mod._PLANS_DIR
        plans_mod._PLANS_DIR = tmp_path / "plans"
        try:
            with patch("agentibridge.plans.get_redis", return_value=None):
                plans_mod._write_plan("p1", _make_plan_data("p1", status="ready"))
                plans_mod._write_plan("p2", _make_plan_data("p2", status="planning"))
                plans_mod._write_plan("p3", _make_plan_data("p3", status="ready"))

                result = plans_mod.list_plans(status="ready")
                assert len(result) == 2
                assert all(p["status"] == "ready" for p in result)
        finally:
            plans_mod._PLANS_DIR = original

    def test_list_respects_limit(self, tmp_path):
        original = plans_mod._PLANS_DIR
        plans_mod._PLANS_DIR = tmp_path / "plans"
        try:
            with patch("agentibridge.plans.get_redis", return_value=None):
                for i in range(5):
                    plans_mod._write_plan(f"p{i}", _make_plan_data(f"p{i}"))

                result = plans_mod.list_plans(limit=2)
                assert len(result) == 2
        finally:
            plans_mod._PLANS_DIR = original

    def test_list_empty(self, tmp_path):
        original = plans_mod._PLANS_DIR
        plans_mod._PLANS_DIR = tmp_path / "plans"
        try:
            with patch("agentibridge.plans.get_redis", return_value=None):
                result = plans_mod.list_plans()
                assert result == []
        finally:
            plans_mod._PLANS_DIR = original


# ---------------------------------------------------------------------------
# Submit plan tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSubmitPlan:
    """Tests for submit_plan()."""

    def test_basic_submit(self, tmp_path):
        original = plans_mod._PLANS_DIR
        plans_mod._PLANS_DIR = tmp_path / "plans"
        try:
            mock_result = ClaudeResult(success=True, result="# Plan\n\n1. Step one")

            with (
                patch("agentibridge.plans.get_redis", return_value=None),
                patch("agentibridge.claude_runner.run_claude", new_callable=AsyncMock, return_value=mock_result),
            ):

                async def _run():
                    return await plans_mod.submit_plan("Fix the bug")

                result = asyncio.run(_run())
                assert "plan_id" in result
                assert result["status"] == "planning"
        finally:
            plans_mod._PLANS_DIR = original

    def test_planning_success_sets_ready(self, tmp_path):
        original = plans_mod._PLANS_DIR
        plans_mod._PLANS_DIR = tmp_path / "plans"
        try:
            mock_result = ClaudeResult(success=True, result="# Plan\n\n1. Do things")

            with (
                patch("agentibridge.plans.get_redis", return_value=None),
                patch("agentibridge.claude_runner.run_claude", new_callable=AsyncMock, return_value=mock_result),
            ):

                async def _run():
                    result = await plans_mod.submit_plan("Fix the bug")
                    await asyncio.sleep(0.1)  # Let background task complete
                    plan = plans_mod.get_plan_status(result["plan_id"])
                    return plan

                plan = asyncio.run(_run())
                assert plan is not None
                assert plan["status"] == "ready"
                assert "# Plan" in plan["content"]
        finally:
            plans_mod._PLANS_DIR = original

    def test_planning_failure_sets_failed(self, tmp_path):
        original = plans_mod._PLANS_DIR
        plans_mod._PLANS_DIR = tmp_path / "plans"
        try:
            mock_result = ClaudeResult(success=False, error="CLI crashed")

            with (
                patch("agentibridge.plans.get_redis", return_value=None),
                patch("agentibridge.claude_runner.run_claude", new_callable=AsyncMock, return_value=mock_result),
            ):

                async def _run():
                    result = await plans_mod.submit_plan("Fix the bug")
                    await asyncio.sleep(0.1)
                    return plans_mod.get_plan_status(result["plan_id"])

                plan = asyncio.run(_run())
                assert plan is not None
                assert plan["status"] == "failed"
                assert plan["error"] == "CLI crashed"
        finally:
            plans_mod._PLANS_DIR = original

    def test_submit_wait_mode(self, tmp_path):
        original = plans_mod._PLANS_DIR
        plans_mod._PLANS_DIR = tmp_path / "plans"
        try:
            mock_result = ClaudeResult(success=True, result="# Plan\n\nDetailed steps")

            with (
                patch("agentibridge.plans.get_redis", return_value=None),
                patch("agentibridge.claude_runner.run_claude", new_callable=AsyncMock, return_value=mock_result),
            ):

                async def _run():
                    return await plans_mod.submit_plan("Fix the bug", wait=True)

                result = asyncio.run(_run())
                assert result["status"] == "ready"
                assert result["content"] == "# Plan\n\nDetailed steps"
        finally:
            plans_mod._PLANS_DIR = original

    def test_submit_with_repo_url(self, tmp_path):
        original = plans_mod._PLANS_DIR
        plans_mod._PLANS_DIR = tmp_path / "plans"
        try:
            mock_result = ClaudeResult(success=True, result="# Plan")

            with (
                patch("agentibridge.plans.get_redis", return_value=None),
                patch(
                    "agentibridge.claude_runner.run_claude", new_callable=AsyncMock, return_value=mock_result
                ) as mock_claude,
            ):

                async def _run():
                    return await plans_mod.submit_plan(
                        "Fix the bug", repo_url="https://github.com/test/repo", wait=True
                    )

                asyncio.run(_run())
                call_kwargs = mock_claude.call_args
                assert "Read,Glob,Grep" in str(call_kwargs)
        finally:
            plans_mod._PLANS_DIR = original


# ---------------------------------------------------------------------------
# Execute plan tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExecutePlan:
    """Tests for execute_plan()."""

    def test_execute_ready_plan(self, tmp_path):
        original = plans_mod._PLANS_DIR
        plans_mod._PLANS_DIR = tmp_path / "plans"
        try:
            mock_result = ClaudeResult(success=True, result="Done")

            with (
                patch("agentibridge.plans.get_redis", return_value=None),
                patch("agentibridge.claude_runner.run_claude", new_callable=AsyncMock, return_value=mock_result),
            ):
                plan_data = _make_plan_data("p1", status="ready", content="# Plan\n1. Do it")
                plans_mod._write_plan("p1", plan_data)

                async def _run():
                    return await plans_mod.execute_plan("p1", wait=True)

                result = asyncio.run(_run())
                assert result["status"] == "completed"
                assert result["plan_id"] == "p1"
                assert "execution_job_id" in result
        finally:
            plans_mod._PLANS_DIR = original

    def test_execute_non_ready_plan_fails(self, tmp_path):
        original = plans_mod._PLANS_DIR
        plans_mod._PLANS_DIR = tmp_path / "plans"
        try:
            with patch("agentibridge.plans.get_redis", return_value=None):
                plan_data = _make_plan_data("p1", status="planning")
                plans_mod._write_plan("p1", plan_data)

                async def _run():
                    return await plans_mod.execute_plan("p1")

                with pytest.raises(ValueError, match="not ready"):
                    asyncio.run(_run())
        finally:
            plans_mod._PLANS_DIR = original

    def test_execute_nonexistent_plan_fails(self, tmp_path):
        original = plans_mod._PLANS_DIR
        plans_mod._PLANS_DIR = tmp_path / "plans"
        try:
            with patch("agentibridge.plans.get_redis", return_value=None):

                async def _run():
                    return await plans_mod.execute_plan("nonexistent")

                with pytest.raises(ValueError, match="not found"):
                    asyncio.run(_run())
        finally:
            plans_mod._PLANS_DIR = original

    def test_execution_failure_updates_plan(self, tmp_path):
        original = plans_mod._PLANS_DIR
        plans_mod._PLANS_DIR = tmp_path / "plans"
        try:
            mock_result = ClaudeResult(success=False, error="Execution crashed")

            with (
                patch("agentibridge.plans.get_redis", return_value=None),
                patch("agentibridge.claude_runner.run_claude", new_callable=AsyncMock, return_value=mock_result),
            ):
                plan_data = _make_plan_data("p1", status="ready", content="# Plan")
                plans_mod._write_plan("p1", plan_data)

                async def _run():
                    return await plans_mod.execute_plan("p1", wait=True)

                result = asyncio.run(_run())
                assert result["status"] == "failed"
                assert result["error"] == "Execution crashed"
        finally:
            plans_mod._PLANS_DIR = original

    def test_execute_plan_no_content_fails(self, tmp_path):
        original = plans_mod._PLANS_DIR
        plans_mod._PLANS_DIR = tmp_path / "plans"
        try:
            with patch("agentibridge.plans.get_redis", return_value=None):
                plan_data = _make_plan_data("p1", status="ready", content="")
                plans_mod._write_plan("p1", plan_data)

                async def _run():
                    return await plans_mod.execute_plan("p1")

                with pytest.raises(ValueError, match="no content"):
                    asyncio.run(_run())
        finally:
            plans_mod._PLANS_DIR = original


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPlanHelpers:
    """Tests for internal helper functions."""

    def test_rkey_builds_namespaced_key(self):
        assert plans_mod._rkey("plan:abc") == "agentibridge:sb:plan:abc"

    def test_write_file_creates_directory(self, tmp_path):
        original = plans_mod._PLANS_DIR
        plans_mod._PLANS_DIR = tmp_path / "new_dir" / "plans"
        try:
            plans_mod._write_file("test", {"status": "planning"})
            assert (plans_mod._PLANS_DIR / "test.json").exists()
        finally:
            plans_mod._PLANS_DIR = original

    def test_plan_summary_excludes_content(self):
        data = _make_plan_data(content="# Big plan\nLots of text")
        summary = plans_mod._plan_summary(data)
        assert "content" not in summary
        assert "plan_id" in summary
        assert "status" in summary

    def test_write_plan_survives_redis_failure(self, tmp_path):
        original = plans_mod._PLANS_DIR
        plans_mod._PLANS_DIR = tmp_path / "plans"
        try:
            mock_r = MagicMock()
            mock_r.hset.side_effect = Exception("Redis down")

            with patch("agentibridge.plans.get_redis", return_value=mock_r):
                data = _make_plan_data()
                plans_mod._write_plan("test-plan-001", data)

            # Should still be readable from file
            with patch("agentibridge.plans.get_redis", return_value=None):
                result = plans_mod.get_plan_status("test-plan-001")
                assert result is not None
                assert result["plan_id"] == "test-plan-001"
        finally:
            plans_mod._PLANS_DIR = original
