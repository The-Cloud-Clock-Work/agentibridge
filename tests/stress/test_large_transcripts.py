"""Stress tests for large transcript parsing."""

import json
import time

import pytest

from agentic_bridge.parser import parse_transcript_entries, parse_transcript_meta


@pytest.mark.stress
class TestLargeTranscripts:
    def test_parse_50k_entries(self, tmp_path):
        """Parse a ~10MB JSONL file with 50,000 entries in under 10 seconds."""
        filepath = tmp_path / "large.jsonl"

        # Generate 50k entries (~10MB)
        with open(filepath, "w", encoding="utf-8") as f:
            for i in range(25000):
                f.write(
                    json.dumps(
                        {
                            "type": "user",
                            "timestamp": f"2025-06-01T{10 + (i // 3600):02d}:{(i % 3600) // 60:02d}:{i % 60:02d}Z",
                            "uuid": f"u{i}",
                            "cwd": "/home/user/dev/project",
                            "gitBranch": "main",
                            "message": {
                                "role": "user",
                                "content": f"User message number {i} with some content to make it realistic",
                            },
                        }
                    )
                    + "\n"
                )
                f.write(
                    json.dumps(
                        {
                            "type": "assistant",
                            "timestamp": f"2025-06-01T{10 + (i // 3600):02d}:{(i % 3600) // 60:02d}:{i % 60:02d}Z",
                            "uuid": f"a{i}",
                            "message": {
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": f"Assistant response {i} with some detailed explanation of what was done",
                                    },
                                    {
                                        "type": "tool_use",
                                        "id": f"t{i}",
                                        "name": "Write",
                                        "input": {"path": f"file{i}.py"},
                                    },
                                ],
                            },
                        }
                    )
                    + "\n"
                )

        file_size_mb = filepath.stat().st_size / (1024 * 1024)
        assert file_size_mb > 5, f"File too small: {file_size_mb:.1f}MB"

        start = time.time()
        entries, offset = parse_transcript_entries(filepath)
        elapsed = time.time() - start

        assert len(entries) == 50000, f"Expected 50000 entries, got {len(entries)}"
        assert elapsed < 10, f"Parsing took {elapsed:.1f}s (limit: 10s)"
        print(f"\n  Parsed {len(entries)} entries from {file_size_mb:.1f}MB in {elapsed:.2f}s")

    def test_parse_meta_large_file(self, tmp_path):
        """Metadata extraction from large file should complete quickly."""
        filepath = tmp_path / "large_meta.jsonl"

        with open(filepath, "w", encoding="utf-8") as f:
            for i in range(10000):
                f.write(
                    json.dumps(
                        {
                            "type": "user",
                            "timestamp": f"2025-06-01T10:{i // 60:02d}:{i % 60:02d}Z",
                            "uuid": f"u{i}",
                            "cwd": "/home/user/dev/project",
                            "gitBranch": "main",
                            "message": {"role": "user", "content": f"Message {i}"},
                        }
                    )
                    + "\n"
                )

        start = time.time()
        meta = parse_transcript_meta(filepath, "-home-user-dev-project")
        elapsed = time.time() - start

        assert meta is not None
        assert meta.num_user_turns == 10000
        assert elapsed < 5, f"Meta extraction took {elapsed:.1f}s (limit: 5s)"
        print(f"\n  Extracted metadata in {elapsed:.2f}s")
