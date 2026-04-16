"""Tests for agentibridge.dispatch module."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentibridge.claude_runner import ClaudeResult
from agentibridge.dispatch import (
    _rkey,
    _write_file,
    _write_job,
    get_job_status,
    list_jobs,
    restore_session_context,
    dispatch_task,
)
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

        with patch("agentibridge.store.SessionStore", return_value=mock_store):
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

        with patch("agentibridge.store.SessionStore", return_value=mock_store):
            with pytest.raises(ValueError, match="Session not found"):
                restore_session_context("nonexistent")

    def test_last_n_limits_entries(self):
        meta = make_meta()
        entries = [make_entry("user", content=f"Turn {i}") for i in range(20)]

        mock_store = MagicMock()
        mock_store.get_session_meta.return_value = meta
        mock_store.get_session_entries.return_value = entries

        with patch("agentibridge.store.SessionStore", return_value=mock_store):
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

        with patch("agentibridge.store.SessionStore", return_value=mock_store):
            context = restore_session_context("s1")

        assert "[SUMMARY]" in context
        assert "Session summary here" in context


@pytest.mark.unit
class TestDispatchTask:
    def test_basic_dispatch(self):
        mock_result = ClaudeResult(
            success=True,
            result="done",
            exit_code=0,
            duration_ms=500,
            timed_out=False,
            error=None,
        )

        async def _run():
            with patch(
                "agentibridge.claude_runner.run_claude", new_callable=AsyncMock, return_value=mock_result
            ) as mock_run:
                result = await dispatch_task("Fix the bug")
                await asyncio.sleep(0)  # let background task run
                assert result["dispatched"] is True
                assert result["background"] is True
                assert "job_id" in result
                assert result["status"] == "running"
                mock_run.assert_called_once()
                # Default command maps to "sonnet"
                assert mock_run.call_args[1]["model"] == "sonnet"

        asyncio.run(_run())

    def test_with_project(self):
        mock_result = ClaudeResult(
            success=True,
            result="done",
            exit_code=0,
            duration_ms=100,
        )

        with patch(
            "agentibridge.claude_runner.run_claude", new_callable=AsyncMock, return_value=mock_result
        ) as mock_run:
            result = asyncio.run(dispatch_task("Fix bug", project="myapp"))

            assert result["dispatched"] is True
            prompt = mock_run.call_args[1]["prompt"]
            assert "Project: myapp" in prompt

    def test_with_session_context(self):
        mock_store = MagicMock()
        meta = make_meta()
        mock_store.get_session_meta.return_value = meta
        mock_store.get_session_entries.return_value = [
            make_entry("user", content="Previous work"),
        ]

        mock_result = ClaudeResult(
            success=True,
            result="done",
            exit_code=0,
            duration_ms=100,
        )

        with (
            patch("agentibridge.store.SessionStore", return_value=mock_store),
            patch(
                "agentibridge.claude_runner.run_claude", new_callable=AsyncMock, return_value=mock_result
            ) as mock_run,
        ):
            result = asyncio.run(dispatch_task("Fix bug", session_id="s1"))

            assert result["context_session"] == "s1"
            prompt = mock_run.call_args[1]["prompt"]
            assert "RESTORED SESSION CONTEXT" in prompt

    def test_context_restore_failure_graceful(self):
        mock_store = MagicMock()
        mock_store.get_session_meta.return_value = None

        mock_result = ClaudeResult(
            success=True,
            result="done",
            exit_code=0,
            duration_ms=100,
        )

        with (
            patch("agentibridge.store.SessionStore", return_value=mock_store),
            patch(
                "agentibridge.claude_runner.run_claude", new_callable=AsyncMock, return_value=mock_result
            ) as mock_run,
        ):
            result = asyncio.run(dispatch_task("Fix bug", session_id="bad-id"))

            # Should still dispatch, just with error note in prompt
            assert result["dispatched"] is True
            prompt = mock_run.call_args[1]["prompt"]
            assert "Failed to restore" in prompt

    def test_api_failure(self):
        mock_result = ClaudeResult(
            success=False,
            exit_code=1,
            duration_ms=100,
            error="CLI error",
        )

        async def _run():
            with patch("agentibridge.claude_runner.run_claude", new_callable=AsyncMock, return_value=mock_result):
                result = await dispatch_task("Fix bug")
                await asyncio.sleep(0)  # let background task run
                assert result["dispatched"] is True
                assert result["background"] is True
                assert "job_id" in result
                # Check job file was written with failure
                from agentibridge.dispatch import get_job_status

                job = get_job_status(result["job_id"])
                assert job is not None
                assert job["status"] == "failed"
                assert job["error"] == "CLI error"

        asyncio.run(_run())

    def test_command_model_mapping(self):
        mock_result = ClaudeResult(success=True, result="done", exit_code=0)

        with patch(
            "agentibridge.claude_runner.run_claude", new_callable=AsyncMock, return_value=mock_result
        ) as mock_run:
            asyncio.run(dispatch_task("Task", command="ultrathink"))
            assert mock_run.call_args[1]["model"] == "opus"

        with patch(
            "agentibridge.claude_runner.run_claude", new_callable=AsyncMock, return_value=mock_result
        ) as mock_run:
            asyncio.run(dispatch_task("Task", command="thinkhard"))
            assert mock_run.call_args[1]["model"] == "sonnet"


# ===========================================================================
# Job storage tests (Redis + file fallback)
# ===========================================================================


@pytest.mark.unit
class TestJobStorage:
    """Tests for _write_job / get_job_status with Redis and file fallback."""

    def test_write_and_read_file_fallback(self, tmp_path):
        """Write+read round-trip via file when Redis is unavailable."""
        import agentibridge.dispatch as dispatch_mod

        original_dir = dispatch_mod._JOBS_DIR
        dispatch_mod._JOBS_DIR = tmp_path / "jobs"

        try:
            with patch("agentibridge.dispatch.get_redis", return_value=None):
                data = {
                    "job_id": "test-j1",
                    "status": "running",
                    "started_at": "2026-01-01T00:00:00+00:00",
                    "task": "Do stuff",
                    "output": None,
                    "error": None,
                }
                _write_job("test-j1", data)
                result = get_job_status("test-j1")

            assert result is not None
            assert result["job_id"] == "test-j1"
            assert result["status"] == "running"
        finally:
            dispatch_mod._JOBS_DIR = original_dir

    def test_write_and_read_redis(self, mock_redis, tmp_path):
        """Write+read round-trip via Redis (primary path)."""
        import agentibridge.dispatch as dispatch_mod

        original_dir = dispatch_mod._JOBS_DIR
        dispatch_mod._JOBS_DIR = tmp_path / "jobs"

        try:
            data = {
                "job_id": "test-j2",
                "status": "completed",
                "started_at": "2026-01-01T00:00:00+00:00",
                "task": "Do stuff",
                "output": "result text",
                "error": None,
                "exit_code": 0,
            }
            _write_job("test-j2", data)
            result = get_job_status("test-j2")

            assert result is not None
            assert result["job_id"] == "test-j2"
            assert result["status"] == "completed"
            assert result["output"] == "result text"
            assert result["exit_code"] == 0
        finally:
            dispatch_mod._JOBS_DIR = original_dir

    def test_get_nonexistent_job(self, tmp_path):
        """Reading a nonexistent job returns None."""
        import agentibridge.dispatch as dispatch_mod

        original_dir = dispatch_mod._JOBS_DIR
        dispatch_mod._JOBS_DIR = tmp_path / "jobs"

        try:
            with patch("agentibridge.dispatch.get_redis", return_value=None):
                result = get_job_status("does-not-exist")
            assert result is None
        finally:
            dispatch_mod._JOBS_DIR = original_dir


# ===========================================================================
# list_jobs tests
# ===========================================================================


@pytest.mark.unit
class TestListJobs:
    """Tests for list_jobs() with Redis and file fallback."""

    def test_list_all_file_fallback(self, tmp_path):
        """list_jobs returns all jobs from files when Redis is unavailable."""
        import agentibridge.dispatch as dispatch_mod

        original_dir = dispatch_mod._JOBS_DIR
        dispatch_mod._JOBS_DIR = tmp_path / "jobs"

        try:
            with patch("agentibridge.dispatch.get_redis", return_value=None):
                _write_job("j1", {"job_id": "j1", "status": "running", "started_at": "t1", "output": "big text"})
                _write_job("j2", {"job_id": "j2", "status": "completed", "started_at": "t2", "output": "big text 2"})
                jobs = list_jobs()

            assert len(jobs) == 2
            job_ids = {j["job_id"] for j in jobs}
            assert job_ids == {"j1", "j2"}
        finally:
            dispatch_mod._JOBS_DIR = original_dir

    def test_list_excludes_output(self, tmp_path):
        """list_jobs excludes the output field for brevity."""
        import agentibridge.dispatch as dispatch_mod

        original_dir = dispatch_mod._JOBS_DIR
        dispatch_mod._JOBS_DIR = tmp_path / "jobs"

        try:
            with patch("agentibridge.dispatch.get_redis", return_value=None):
                _write_job("j1", {"job_id": "j1", "status": "completed", "started_at": "t1", "output": "big text"})
                jobs = list_jobs()

            assert len(jobs) == 1
            assert "output" not in jobs[0]
        finally:
            dispatch_mod._JOBS_DIR = original_dir

    def test_list_filter_by_status(self, tmp_path):
        """list_jobs filters by status."""
        import agentibridge.dispatch as dispatch_mod

        original_dir = dispatch_mod._JOBS_DIR
        dispatch_mod._JOBS_DIR = tmp_path / "jobs"

        try:
            with patch("agentibridge.dispatch.get_redis", return_value=None):
                _write_job("j1", {"job_id": "j1", "status": "running", "started_at": "t1", "output": None})
                _write_job("j2", {"job_id": "j2", "status": "completed", "started_at": "t2", "output": "x"})
                _write_job("j3", {"job_id": "j3", "status": "failed", "started_at": "t3", "output": None})

                running = list_jobs(status="running")
                completed = list_jobs(status="completed")

            assert len(running) == 1
            assert running[0]["job_id"] == "j1"
            assert len(completed) == 1
            assert completed[0]["job_id"] == "j2"
        finally:
            dispatch_mod._JOBS_DIR = original_dir

    def test_list_respects_limit(self, tmp_path):
        """list_jobs respects the limit parameter."""
        import agentibridge.dispatch as dispatch_mod

        original_dir = dispatch_mod._JOBS_DIR
        dispatch_mod._JOBS_DIR = tmp_path / "jobs"

        try:
            with patch("agentibridge.dispatch.get_redis", return_value=None):
                for i in range(5):
                    _write_job(
                        f"j{i}", {"job_id": f"j{i}", "status": "completed", "started_at": f"t{i}", "output": None}
                    )
                jobs = list_jobs(limit=3)

            assert len(jobs) == 3
        finally:
            dispatch_mod._JOBS_DIR = original_dir

    def test_list_empty(self, tmp_path):
        """list_jobs returns empty list when no jobs exist."""
        import agentibridge.dispatch as dispatch_mod

        original_dir = dispatch_mod._JOBS_DIR
        dispatch_mod._JOBS_DIR = tmp_path / "nonexistent_dir"

        try:
            with patch("agentibridge.dispatch.get_redis", return_value=None):
                jobs = list_jobs()
            assert jobs == []
        finally:
            dispatch_mod._JOBS_DIR = original_dir

    def test_list_redis(self, mock_redis, tmp_path):
        """list_jobs reads from Redis when available."""
        import agentibridge.dispatch as dispatch_mod

        original_dir = dispatch_mod._JOBS_DIR
        dispatch_mod._JOBS_DIR = tmp_path / "jobs"

        try:
            _write_job(
                "j1", {"job_id": "j1", "status": "running", "started_at": "2026-01-01T00:00:00+00:00", "output": "big"}
            )
            _write_job(
                "j2",
                {"job_id": "j2", "status": "completed", "started_at": "2026-01-02T00:00:00+00:00", "output": "big2"},
            )

            jobs = list_jobs()

            assert len(jobs) == 2
            # Output should be excluded
            for j in jobs:
                assert "output" not in j
        finally:
            dispatch_mod._JOBS_DIR = original_dir

    def test_list_redis_filter_status(self, mock_redis, tmp_path):
        """list_jobs filters by status with Redis."""
        import agentibridge.dispatch as dispatch_mod

        original_dir = dispatch_mod._JOBS_DIR
        dispatch_mod._JOBS_DIR = tmp_path / "jobs"

        try:
            _write_job(
                "j1", {"job_id": "j1", "status": "running", "started_at": "2026-01-01T00:00:00+00:00", "output": None}
            )
            _write_job(
                "j2", {"job_id": "j2", "status": "completed", "started_at": "2026-01-02T00:00:00+00:00", "output": None}
            )

            running = list_jobs(status="running")
            assert len(running) == 1
            assert running[0]["job_id"] == "j1"
        finally:
            dispatch_mod._JOBS_DIR = original_dir

    def test_list_redis_respects_limit(self, mock_redis, tmp_path):
        """list_jobs respects limit with Redis."""
        import agentibridge.dispatch as dispatch_mod

        original_dir = dispatch_mod._JOBS_DIR
        dispatch_mod._JOBS_DIR = tmp_path / "jobs"

        try:
            for i in range(5):
                _write_job(
                    f"j{i}",
                    {
                        "job_id": f"j{i}",
                        "status": "completed",
                        "started_at": f"2026-01-0{i + 1}T00:00:00+00:00",
                        "output": None,
                    },
                )
            jobs = list_jobs(limit=2)
            assert len(jobs) == 2
        finally:
            dispatch_mod._JOBS_DIR = original_dir

    def test_list_file_fallback_when_redis_raises(self, tmp_path):
        """list_jobs falls back to files when Redis throws an exception."""
        import agentibridge.dispatch as dispatch_mod

        original_dir = dispatch_mod._JOBS_DIR
        dispatch_mod._JOBS_DIR = tmp_path / "jobs"

        try:
            # Write files directly (no Redis)
            with patch("agentibridge.dispatch.get_redis", return_value=None):
                _write_job("j1", {"job_id": "j1", "status": "completed", "started_at": "t1", "output": "x"})

            # Now mock Redis to fail on list, forcing file fallback
            mock_r = MagicMock()
            mock_r.zrevrange.side_effect = Exception("Redis down")
            with patch("agentibridge.dispatch.get_redis", return_value=mock_r):
                jobs = list_jobs()

            assert len(jobs) == 1
            assert jobs[0]["job_id"] == "j1"
        finally:
            dispatch_mod._JOBS_DIR = original_dir

    def test_list_file_malformed_json_skipped(self, tmp_path):
        """list_jobs skips malformed JSON files gracefully."""
        import agentibridge.dispatch as dispatch_mod

        original_dir = dispatch_mod._JOBS_DIR
        dispatch_mod._JOBS_DIR = tmp_path / "jobs"

        try:
            (tmp_path / "jobs").mkdir(parents=True, exist_ok=True)
            (tmp_path / "jobs" / "good.json").write_text(
                json.dumps({"job_id": "good", "status": "completed", "started_at": "t1"})
            )
            (tmp_path / "jobs" / "bad.json").write_text("not valid json{{{{")

            with patch("agentibridge.dispatch.get_redis", return_value=None):
                jobs = list_jobs()

            assert len(jobs) == 1
            assert jobs[0]["job_id"] == "good"
        finally:
            dispatch_mod._JOBS_DIR = original_dir


# ===========================================================================
# Internal helper tests
# ===========================================================================


@pytest.mark.unit
class TestInternalHelpers:
    """Tests for dispatch module internal helper functions."""

    def test_rkey_builds_namespaced_key(self):
        """_rkey builds a correctly namespaced Redis key."""
        assert _rkey("job:abc-123") == "agentibridge:sb:job:abc-123"
        assert _rkey("idx:jobs") == "agentibridge:sb:idx:jobs"

    def test_write_file_creates_directory(self, tmp_path):
        """_write_file creates the jobs directory if it doesn't exist."""
        import agentibridge.dispatch as dispatch_mod

        original_dir = dispatch_mod._JOBS_DIR
        dispatch_mod._JOBS_DIR = tmp_path / "new_jobs_dir"

        try:
            _write_file("f1", {"job_id": "f1", "status": "running"})
            assert (tmp_path / "new_jobs_dir" / "f1.json").exists()
            data = json.loads((tmp_path / "new_jobs_dir" / "f1.json").read_text())
            assert data["job_id"] == "f1"
        finally:
            dispatch_mod._JOBS_DIR = original_dir

    def test_write_job_writes_both_redis_and_file(self, mock_redis, tmp_path):
        """_write_job writes to both Redis and file."""
        import agentibridge.dispatch as dispatch_mod

        original_dir = dispatch_mod._JOBS_DIR
        dispatch_mod._JOBS_DIR = tmp_path / "jobs"

        try:
            data = {
                "job_id": "dual-1",
                "status": "running",
                "started_at": "2026-01-01T00:00:00+00:00",
                "output": None,
            }
            _write_job("dual-1", data)

            # File should exist
            assert (tmp_path / "jobs" / "dual-1.json").exists()

            # Redis should have the hash
            redis_data = mock_redis.hgetall(_rkey("job:dual-1"))
            assert redis_data["job_id"] == "dual-1"

            # Redis sorted set should have the job
            members = mock_redis.zrevrange(_rkey("idx:jobs"), 0, -1)
            assert "dual-1" in members
        finally:
            dispatch_mod._JOBS_DIR = original_dir

    def test_write_job_survives_redis_failure(self, tmp_path):
        """_write_job still writes file when Redis raises."""
        import agentibridge.dispatch as dispatch_mod

        original_dir = dispatch_mod._JOBS_DIR
        dispatch_mod._JOBS_DIR = tmp_path / "jobs"

        try:
            mock_r = MagicMock()
            mock_r.hset.side_effect = Exception("Redis crashed")
            with patch("agentibridge.dispatch.get_redis", return_value=mock_r):
                _write_job("fail-r1", {"job_id": "fail-r1", "status": "running", "started_at": "t1"})

            # File should still be written
            assert (tmp_path / "jobs" / "fail-r1.json").exists()
        finally:
            dispatch_mod._JOBS_DIR = original_dir

    def test_write_job_redis_ttl_set(self, mock_redis, tmp_path):
        """_write_job sets TTL on the Redis hash key."""
        import agentibridge.dispatch as dispatch_mod

        original_dir = dispatch_mod._JOBS_DIR
        dispatch_mod._JOBS_DIR = tmp_path / "jobs"

        try:
            _write_job("ttl-1", {"job_id": "ttl-1", "status": "running", "started_at": "2026-01-01T00:00:00+00:00"})

            ttl = mock_redis.ttl(_rkey("job:ttl-1"))
            assert ttl > 0  # TTL was set
            assert ttl <= 86400  # 24h max
        finally:
            dispatch_mod._JOBS_DIR = original_dir

    def test_get_job_redis_primary_file_fallback(self, tmp_path):
        """get_job_status reads from Redis first, falls back to file."""
        import agentibridge.dispatch as dispatch_mod

        original_dir = dispatch_mod._JOBS_DIR
        dispatch_mod._JOBS_DIR = tmp_path / "jobs"

        try:
            # Write only to file (no Redis)
            with patch("agentibridge.dispatch.get_redis", return_value=None):
                _write_file("fb-1", {"job_id": "fb-1", "status": "completed", "output": "result"})

            # Read with Redis returning None — should fall back to file
            with patch("agentibridge.dispatch.get_redis", return_value=None):
                result = get_job_status("fb-1")

            assert result is not None
            assert result["job_id"] == "fb-1"
            assert result["status"] == "completed"
        finally:
            dispatch_mod._JOBS_DIR = original_dir

    def test_get_job_redis_returns_deserialized_values(self, mock_redis, tmp_path):
        """get_job_status deserializes JSON-encoded Redis values."""
        import agentibridge.dispatch as dispatch_mod

        original_dir = dispatch_mod._JOBS_DIR
        dispatch_mod._JOBS_DIR = tmp_path / "jobs"

        try:
            # Write a job with complex values
            data = {
                "job_id": "deser-1",
                "status": "completed",
                "started_at": "2026-01-01T00:00:00+00:00",
                "output": "result text",
                "error": None,
                "exit_code": 0,
                "timed_out": False,
            }
            _write_job("deser-1", data)
            result = get_job_status("deser-1")

            assert result["exit_code"] == 0
            assert result["timed_out"] is False
            assert result["error"] is None
        finally:
            dispatch_mod._JOBS_DIR = original_dir

    def test_write_job_invalid_started_at_uses_current_time(self, mock_redis, tmp_path):
        """_write_job uses current time when started_at can't be parsed."""
        import agentibridge.dispatch as dispatch_mod

        original_dir = dispatch_mod._JOBS_DIR
        dispatch_mod._JOBS_DIR = tmp_path / "jobs"

        try:
            _write_job("bad-ts", {"job_id": "bad-ts", "status": "running", "started_at": "not-a-timestamp"})

            # Should still be in the sorted set
            members = mock_redis.zrevrange(_rkey("idx:jobs"), 0, -1)
            assert "bad-ts" in members
        finally:
            dispatch_mod._JOBS_DIR = original_dir


# ===========================================================================
# dispatch_task background task completion tests
# ===========================================================================


@pytest.mark.unit
class TestDispatchTaskCompletion:
    """Tests for dispatch_task background completion and job file writes."""

    def test_successful_completion_writes_completed_status(self, tmp_path):
        """After successful run_claude, job file has status=completed."""
        import agentibridge.dispatch as dispatch_mod

        original_dir = dispatch_mod._JOBS_DIR
        dispatch_mod._JOBS_DIR = tmp_path / "jobs"

        mock_result = ClaudeResult(
            success=True,
            result="Task completed",
            session_id="s-new",
            exit_code=0,
            duration_ms=2500,
        )

        async def _run():
            with (
                patch("agentibridge.dispatch.get_redis", return_value=None),
                patch("agentibridge.claude_runner.run_claude", new_callable=AsyncMock, return_value=mock_result),
            ):
                result = await dispatch_task("Do something")
                await asyncio.sleep(0.1)  # let background task complete
                return result

        try:
            result = asyncio.run(_run())
            job = get_job_status(result["job_id"])
            assert job is not None
            assert job["status"] == "completed"
            assert job["output"] == "Task completed"
            assert job["claude_session_id"] == "s-new"
            assert job["exit_code"] == 0
            assert job["duration_ms"] == 2500
        finally:
            dispatch_mod._JOBS_DIR = original_dir

    def test_exception_writes_failed_status(self, tmp_path):
        """If run_claude raises, job file has status=failed with error."""
        import agentibridge.dispatch as dispatch_mod

        original_dir = dispatch_mod._JOBS_DIR
        dispatch_mod._JOBS_DIR = tmp_path / "jobs"

        async def _run():
            with (
                patch("agentibridge.dispatch.get_redis", return_value=None),
                patch(
                    "agentibridge.claude_runner.run_claude",
                    new_callable=AsyncMock,
                    side_effect=RuntimeError("binary not found"),
                ),
            ):
                result = await dispatch_task("Do something")
                await asyncio.sleep(0.1)
                return result

        try:
            result = asyncio.run(_run())
            job = get_job_status(result["job_id"])
            assert job is not None
            assert job["status"] == "failed"
            assert "binary not found" in job["error"]
        finally:
            dispatch_mod._JOBS_DIR = original_dir

    def test_dispatch_with_resume_session_id(self, tmp_path):
        """dispatch_task passes resume_session_id to run_claude."""
        import agentibridge.dispatch as dispatch_mod

        original_dir = dispatch_mod._JOBS_DIR
        dispatch_mod._JOBS_DIR = tmp_path / "jobs"

        mock_result = ClaudeResult(success=True, result="resumed", exit_code=0)
        captured = {}

        async def mock_run_claude(**kwargs):
            captured.update(kwargs)
            return mock_result

        async def _run():
            with (
                patch("agentibridge.dispatch.get_redis", return_value=None),
                patch("agentibridge.claude_runner.run_claude", side_effect=mock_run_claude),
            ):
                return await dispatch_task("Continue work", resume_session_id="sess-xyz")

        try:
            result = asyncio.run(_run())
            assert result["resumed_session"] == "sess-xyz"
            assert captured.get("resume_session_id") == "sess-xyz"
        finally:
            dispatch_mod._JOBS_DIR = original_dir

    def test_dispatch_returns_prompt_length(self, tmp_path):
        """dispatch_task return dict includes prompt_length."""
        import agentibridge.dispatch as dispatch_mod

        original_dir = dispatch_mod._JOBS_DIR
        dispatch_mod._JOBS_DIR = tmp_path / "jobs"

        mock_result = ClaudeResult(success=True, result="ok", exit_code=0)

        try:
            with (
                patch("agentibridge.dispatch.get_redis", return_value=None),
                patch("agentibridge.claude_runner.run_claude", new_callable=AsyncMock, return_value=mock_result),
            ):
                result = asyncio.run(dispatch_task("Short task"))

            assert "prompt_length" in result
            assert result["prompt_length"] > 0
        finally:
            dispatch_mod._JOBS_DIR = original_dir
