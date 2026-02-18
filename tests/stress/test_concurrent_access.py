"""Stress tests for concurrent access patterns."""

import threading
import time

import pytest

from agentic_bridge.parser import parse_transcript_entries
from agentic_bridge.store import SessionStore


@pytest.mark.stress
class TestConcurrentAccess:
    def test_concurrent_file_reads(self, temp_projects_dir):
        """20 threads reading the same file simultaneously."""
        filepath = temp_projects_dir / "-home-user-dev-myapp" / "session-001.jsonl"
        errors = []
        results = []

        def read_file():
            try:
                entries, offset = parse_transcript_entries(filepath)
                results.append(len(entries))
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=read_file) for _ in range(20)]
        start = time.time()

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        elapsed = time.time() - start

        assert not errors, f"Errors during concurrent reads: {errors}"
        assert len(results) == 20
        # All threads should get the same count
        assert len(set(results)) == 1, f"Inconsistent results: {set(results)}"
        print(f"\n  20 concurrent reads completed in {elapsed:.2f}s")

    def test_concurrent_store_operations(self, temp_projects_dir):
        """Multiple threads using SessionStore file fallback simultaneously."""
        store = SessionStore()
        store._projects_dir = temp_projects_dir
        store._redis_checked = True
        store._redis = None  # Force file fallback

        errors = []
        results = []

        def list_sessions():
            try:
                sessions = store.list_sessions(limit=10)
                results.append(len(sessions))
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=list_sessions) for _ in range(20)]
        start = time.time()

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        elapsed = time.time() - start

        assert not errors, f"Errors: {errors}"
        assert len(results) == 20
        print(f"\n  20 concurrent store.list_sessions in {elapsed:.2f}s")
