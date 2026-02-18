"""Unit tests for agentic_bridge.parser module."""

import json
from pathlib import Path

import pytest

from agentic_bridge.parser import (
    SessionEntry,
    SessionMeta,
    decode_project_path,
    extract_assistant_content,
    extract_user_content,
    parse_transcript_entries,
    parse_transcript_meta,
    scan_projects_dir,
)
from tests.conftest import MALFORMED_TRANSCRIPT, SAMPLE_TRANSCRIPT, make_entry, make_meta


# ---------------------------------------------------------------------------
# decode_project_path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDecodeProjectPath:
    def test_standard_path(self):
        assert decode_project_path("-home-user-dev-project") == "/home/user/dev/project"

    def test_deep_path(self):
        assert decode_project_path("-home-user-dev-org-repo") == "/home/user/dev/org/repo"

    def test_empty_string(self):
        assert decode_project_path("") == ""

    def test_single_segment(self):
        assert decode_project_path("-root") == "/root"

    def test_no_leading_dash(self):
        """When encoded does not start with dash, just replace dashes with slashes."""
        assert decode_project_path("home-user") == "home/user"

    def test_long_path(self):
        assert decode_project_path("-home-user-dev-a-b-c-d") == "/home/user/dev/a/b/c/d"


# ---------------------------------------------------------------------------
# extract_user_content
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractUserContent:
    def test_string_content(self):
        message = {"role": "user", "content": "Hello, world!"}
        text, is_tool_result = extract_user_content(message)
        assert text == "Hello, world!"
        assert is_tool_result is False

    def test_none_content(self):
        message = {"role": "user"}
        text, is_tool_result = extract_user_content(message)
        assert text == ""
        assert is_tool_result is False

    def test_empty_string_content(self):
        message = {"role": "user", "content": ""}
        text, is_tool_result = extract_user_content(message)
        assert text == ""
        assert is_tool_result is False

    def test_block_list_with_text(self):
        message = {"role": "user", "content": [{"type": "text", "text": "Write tests"}]}
        text, is_tool_result = extract_user_content(message)
        assert text == "Write tests"
        assert is_tool_result is False

    def test_block_list_with_multiple_text_blocks(self):
        message = {
            "role": "user",
            "content": [
                {"type": "text", "text": "One more thing"},
                {"type": "text", "text": "Add monitoring"},
            ],
        }
        text, is_tool_result = extract_user_content(message)
        assert text == "One more thing\nAdd monitoring"
        assert is_tool_result is False

    def test_block_list_with_tool_result(self):
        message = {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu2", "content": "OK"}],
        }
        text, is_tool_result = extract_user_content(message)
        assert text == ""
        assert is_tool_result is True

    def test_char_array(self):
        message = {"role": "user", "content": ["I", " ", "l", "o", "v", "e", " ", "i", "t"]}
        text, is_tool_result = extract_user_content(message)
        assert text == "I love it"
        assert is_tool_result is False

    def test_empty_list(self):
        message = {"role": "user", "content": []}
        text, is_tool_result = extract_user_content(message)
        assert text == ""
        assert is_tool_result is False

    def test_block_list_with_empty_text(self):
        message = {"role": "user", "content": [{"type": "text", "text": ""}]}
        text, is_tool_result = extract_user_content(message)
        assert text == ""
        assert is_tool_result is False

    def test_block_list_with_non_text_type(self):
        message = {"role": "user", "content": [{"type": "image", "source": "..."}]}
        text, is_tool_result = extract_user_content(message)
        assert text == ""
        assert is_tool_result is False

    def test_content_is_explicit_none(self):
        message = {"role": "user", "content": None}
        text, is_tool_result = extract_user_content(message)
        assert text == ""
        assert is_tool_result is False

    def test_block_list_mixed_text_and_non_text(self):
        message = {
            "role": "user",
            "content": [
                {"type": "text", "text": "Part 1"},
                {"type": "image", "source": "data:..."},
                {"type": "text", "text": "Part 2"},
            ],
        }
        text, is_tool_result = extract_user_content(message)
        assert text == "Part 1\nPart 2"
        assert is_tool_result is False


# ---------------------------------------------------------------------------
# extract_assistant_content
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractAssistantContent:
    def test_text_only(self):
        message = {"role": "assistant", "content": [{"type": "text", "text": "Here is the code."}]}
        text, tools = extract_assistant_content(message)
        assert text == "Here is the code."
        assert tools == []

    def test_text_and_tool_use(self):
        message = {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "I will create the file."},
                {"type": "tool_use", "id": "tu1", "name": "Write", "input": {"path": "f.py"}},
            ],
        }
        text, tools = extract_assistant_content(message)
        assert text == "I will create the file."
        assert tools == ["Write"]

    def test_multiple_tool_uses(self):
        message = {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Applying changes."},
                {"type": "tool_use", "id": "tu1", "name": "Write", "input": {}},
                {"type": "tool_use", "id": "tu2", "name": "Edit", "input": {}},
            ],
        }
        text, tools = extract_assistant_content(message)
        assert text == "Applying changes."
        assert tools == ["Write", "Edit"]

    def test_thinking_is_skipped(self):
        message = {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "Let me think about this..."},
                {"type": "text", "text": "Added Prometheus metrics."},
                {"type": "tool_use", "id": "tu6", "name": "Write", "input": {"path": "prometheus.yml"}},
            ],
        }
        text, tools = extract_assistant_content(message)
        assert text == "Added Prometheus metrics."
        assert tools == ["Write"]
        # Ensure thinking content is NOT present
        assert "think" not in text.lower()

    def test_no_content(self):
        message = {"role": "assistant"}
        text, tools = extract_assistant_content(message)
        assert text == ""
        assert tools == []

    def test_string_content_not_list(self):
        """Content that is a string instead of list should return empty."""
        message = {"role": "assistant", "content": "just a string"}
        text, tools = extract_assistant_content(message)
        assert text == ""
        assert tools == []

    def test_empty_content_list(self):
        message = {"role": "assistant", "content": []}
        text, tools = extract_assistant_content(message)
        assert text == ""
        assert tools == []

    def test_tool_use_without_name(self):
        """tool_use block missing name should default to 'unknown'."""
        message = {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "tu1", "input": {}}],
        }
        text, tools = extract_assistant_content(message)
        assert text == ""
        assert tools == ["unknown"]

    def test_multiple_text_blocks(self):
        message = {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "First part."},
                {"type": "text", "text": "Second part."},
            ],
        }
        text, tools = extract_assistant_content(message)
        assert text == "First part.\nSecond part."
        assert tools == []

    def test_non_dict_blocks_in_list(self):
        """Non-dict items in content list should be skipped."""
        message = {
            "role": "assistant",
            "content": ["not a dict", {"type": "text", "text": "Valid text."}],
        }
        text, tools = extract_assistant_content(message)
        assert text == "Valid text."
        assert tools == []


# ---------------------------------------------------------------------------
# parse_transcript_entries — sample transcript
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParseTranscriptEntries:
    def test_parses_sample_transcript(self):
        entries, offset = parse_transcript_entries(SAMPLE_TRANSCRIPT)
        assert len(entries) > 0
        assert offset > 0

    def test_filters_skip_types(self):
        """progress, queue-operation, file-history-snapshot should be filtered out."""
        entries, _ = parse_transcript_entries(SAMPLE_TRANSCRIPT)
        entry_types = {e.entry_type for e in entries}
        assert "progress" not in entry_types
        assert "queue-operation" not in entry_types
        assert "file-history-snapshot" not in entry_types

    def test_only_index_types_present(self):
        entries, _ = parse_transcript_entries(SAMPLE_TRANSCRIPT)
        for entry in entries:
            assert entry.entry_type in {"user", "assistant", "summary", "system"}

    def test_filters_tool_result_user_entries(self):
        """User entries that are tool_result should be skipped."""
        entries, _ = parse_transcript_entries(SAMPLE_TRANSCRIPT)
        # u3 in sample is a tool_result entry — should not appear
        uuids = [e.uuid for e in entries]
        assert "u3" not in uuids

    def test_user_string_content(self):
        entries, _ = parse_transcript_entries(SAMPLE_TRANSCRIPT)
        u1 = next(e for e in entries if e.uuid == "u1")
        assert u1.entry_type == "user"
        assert "Docker Compose" in u1.content

    def test_user_simple_text(self):
        entries, _ = parse_transcript_entries(SAMPLE_TRANSCRIPT)
        u2 = next(e for e in entries if e.uuid == "u2")
        assert u2.entry_type == "user"
        assert u2.content == "Add health checks"

    def test_user_char_array(self):
        entries, _ = parse_transcript_entries(SAMPLE_TRANSCRIPT)
        u6 = next(e for e in entries if e.uuid == "u6")
        assert u6.entry_type == "user"
        assert u6.content == "I love it"

    def test_user_multi_text_blocks(self):
        entries, _ = parse_transcript_entries(SAMPLE_TRANSCRIPT)
        u7 = next(e for e in entries if e.uuid == "u7")
        assert u7.entry_type == "user"
        assert "One more thing" in u7.content
        assert "Add monitoring" in u7.content

    def test_assistant_with_tool_use(self):
        entries, _ = parse_transcript_entries(SAMPLE_TRANSCRIPT)
        a1 = next(e for e in entries if e.uuid == "a1")
        assert a1.entry_type == "assistant"
        assert "Write" in a1.tool_names
        assert "docker-compose" in a1.content.lower() or "services" in a1.content.lower()

    def test_assistant_with_multiple_tool_uses(self):
        entries, _ = parse_transcript_entries(SAMPLE_TRANSCRIPT)
        a3 = next(e for e in entries if e.uuid == "a3")
        assert a3.entry_type == "assistant"
        assert "Write" in a3.tool_names
        assert "Edit" in a3.tool_names
        assert len(a3.tool_names) == 2

    def test_assistant_thinking_filtered(self):
        entries, _ = parse_transcript_entries(SAMPLE_TRANSCRIPT)
        a6 = next(e for e in entries if e.uuid == "a6")
        assert a6.entry_type == "assistant"
        assert "think" not in a6.content.lower()
        assert "Prometheus" in a6.content

    def test_summary_entry(self):
        entries, _ = parse_transcript_entries(SAMPLE_TRANSCRIPT)
        s1 = next(e for e in entries if e.uuid == "s1")
        assert s1.entry_type == "summary"
        assert "Docker Compose" in s1.content

    def test_system_entry(self):
        entries, _ = parse_transcript_entries(SAMPLE_TRANSCRIPT)
        sys1 = next(e for e in entries if e.uuid == "sys1")
        assert sys1.entry_type == "system"
        assert "Welcome" in sys1.content

    def test_entry_count(self):
        """20 raw lines: 3 skip types, 1 tool_result user = expect ~13 indexable entries."""
        entries, _ = parse_transcript_entries(SAMPLE_TRANSCRIPT)
        # 20 lines total:
        #   user entries: u1, u2, u4, u5, u6, u7, u8 = 7 (u3 is tool_result, filtered)
        #   assistant entries: a1, a2, a3, a4, a5, a6, a7 = 7
        #   summary: s1 = 1
        #   system: sys1 = 1
        #   skip types: p1, q1, f1 = 0 (filtered)
        # Total = 7 + 7 + 1 + 1 = 16
        # But u3 is filtered -> 16 - not counted already
        assert len(entries) == 16

    def test_offset_from_start(self):
        """Starting from offset 0 should set new offset to file size."""
        file_size = SAMPLE_TRANSCRIPT.stat().st_size
        _, offset = parse_transcript_entries(SAMPLE_TRANSCRIPT, offset=0)
        assert offset == file_size

    def test_nonexistent_file(self):
        entries, offset = parse_transcript_entries(Path("/nonexistent/file.jsonl"), offset=0)
        assert entries == []
        assert offset == 0

    def test_offset_beyond_file_size(self):
        """Offset past end of file should return empty."""
        file_size = SAMPLE_TRANSCRIPT.stat().st_size
        entries, offset = parse_transcript_entries(SAMPLE_TRANSCRIPT, offset=file_size + 100)
        assert entries == []
        assert offset == file_size + 100

    def test_incremental_parsing_with_offset(self, tmp_path):
        """Parsing with an offset skips the first line after the seek point
        (to avoid partial-line corruption), then picks up subsequent entries.

        The parser's seek-and-skip-first-line design means that when appending
        content, the first new line after the seek point is consumed as a
        potential partial line remainder. So we need at least two new lines
        for the second one to be picked up.
        """
        transcript = tmp_path / "inc.jsonl"
        line1 = (
            json.dumps(
                {
                    "type": "user",
                    "timestamp": "2025-06-01T10:00:00Z",
                    "uuid": "x1",
                    "message": {"role": "user", "content": "First message"},
                }
            )
            + "\n"
        )
        transcript.write_text(line1, encoding="utf-8")

        entries1, offset1 = parse_transcript_entries(transcript, offset=0)
        assert len(entries1) == 1
        assert entries1[0].uuid == "x1"

        # Append two entries — the first is consumed by the partial-line skip,
        # the second is the one actually returned.
        line2 = (
            json.dumps(
                {
                    "type": "user",
                    "timestamp": "2025-06-01T10:05:00Z",
                    "uuid": "x2",
                    "message": {"role": "user", "content": "Second message"},
                }
            )
            + "\n"
        )
        line3 = (
            json.dumps(
                {
                    "type": "user",
                    "timestamp": "2025-06-01T10:10:00Z",
                    "uuid": "x3",
                    "message": {"role": "user", "content": "Third message"},
                }
            )
            + "\n"
        )
        with open(transcript, "a", encoding="utf-8") as f:
            f.write(line2)
            f.write(line3)

        entries2, offset2 = parse_transcript_entries(transcript, offset=offset1)
        # First appended line (x2) is consumed as partial-line skip; x3 is returned
        assert len(entries2) == 1
        assert entries2[0].uuid == "x3"
        assert offset2 > offset1

    def test_content_truncated_at_2000_chars(self, tmp_path):
        """Content longer than 2000 chars should be truncated."""
        transcript = tmp_path / "long.jsonl"
        long_text = "x" * 3000
        line = (
            json.dumps(
                {
                    "type": "user",
                    "timestamp": "2025-06-01T10:00:00Z",
                    "uuid": "long1",
                    "message": {"role": "user", "content": long_text},
                }
            )
            + "\n"
        )
        transcript.write_text(line, encoding="utf-8")

        entries, _ = parse_transcript_entries(transcript)
        assert len(entries) == 1
        assert len(entries[0].content) == 2000


# ---------------------------------------------------------------------------
# parse_transcript_entries — malformed transcript
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParseTranscriptEntriesMalformed:
    def test_handles_malformed_file(self):
        """Should not raise on malformed input."""
        entries, offset = parse_transcript_entries(MALFORMED_TRANSCRIPT)
        assert offset > 0

    def test_skips_invalid_json(self):
        """Lines that are not valid JSON should be silently skipped."""
        entries, _ = parse_transcript_entries(MALFORMED_TRANSCRIPT)
        # Only valid user entries should be parsed: m1 and m7
        uuids = [e.uuid for e in entries]
        assert "m1" in uuids
        assert "m7" in uuids

    def test_skips_unknown_type(self):
        entries, _ = parse_transcript_entries(MALFORMED_TRANSCRIPT)
        uuids = [e.uuid for e in entries]
        assert "m2" not in uuids

    def test_skips_non_dict_entry(self):
        """Bare numbers or non-dict JSON should be skipped."""
        entries, _ = parse_transcript_entries(MALFORMED_TRANSCRIPT)
        # Line with just "12345" should be skipped
        for e in entries:
            assert e.entry_type in {"user", "assistant", "summary", "system"}

    def test_skips_user_without_message(self):
        """User entry without message key should be skipped."""
        entries, _ = parse_transcript_entries(MALFORMED_TRANSCRIPT)
        uuids = [e.uuid for e in entries]
        assert "m3" not in uuids

    def test_skips_user_with_non_dict_message(self):
        """User entry with message as a string should be skipped."""
        entries, _ = parse_transcript_entries(MALFORMED_TRANSCRIPT)
        uuids = [e.uuid for e in entries]
        assert "m4" not in uuids

    def test_skips_assistant_string_content(self):
        """Assistant with string content (not list) should return empty and be skipped."""
        entries, _ = parse_transcript_entries(MALFORMED_TRANSCRIPT)
        uuids = [e.uuid for e in entries]
        assert "m5" not in uuids

    def test_skips_user_empty_content(self):
        """User with empty string content should be skipped."""
        entries, _ = parse_transcript_entries(MALFORMED_TRANSCRIPT)
        uuids = [e.uuid for e in entries]
        assert "m6" not in uuids

    def test_valid_entries_after_garbage(self):
        """Valid entries should be parsed even after garbage lines."""
        entries, _ = parse_transcript_entries(MALFORMED_TRANSCRIPT)
        contents = [e.content for e in entries]
        assert "Valid entry" in contents
        assert "Second valid entry after garbage" in contents

    def test_malformed_entry_count(self):
        """Only 2 valid user entries should remain: m1 and m7."""
        entries, _ = parse_transcript_entries(MALFORMED_TRANSCRIPT)
        assert len(entries) == 2


# ---------------------------------------------------------------------------
# parse_transcript_meta
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParseTranscriptMeta:
    def test_builds_meta_from_sample_transcript(self):
        meta = parse_transcript_meta(SAMPLE_TRANSCRIPT, "-home-user-dev-myapp")
        assert meta is not None
        assert meta.session_id == "sample_transcript"
        assert meta.project_encoded == "-home-user-dev-myapp"
        assert meta.project_path == "/home/user/dev/myapp"

    def test_meta_user_and_assistant_counts(self):
        meta = parse_transcript_meta(SAMPLE_TRANSCRIPT, "-home-user-dev-myapp")
        # 7 user turns (u3 tool_result still counted in _quick_parse_meta since it's type=user)
        # Actually _quick_parse_meta counts all type=user entries, even tool_result
        assert meta.num_user_turns >= 7
        assert meta.num_assistant_turns == 7

    def test_meta_tool_call_count(self):
        meta = parse_transcript_meta(SAMPLE_TRANSCRIPT, "-home-user-dev-myapp")
        # tool_uses: a1(1 Write), a2(1 Edit), a3(2 Write+Edit), a4(1 Bash), a6(1 Write), a7(1 Bash) = 7
        assert meta.num_tool_calls == 7

    def test_meta_timestamps(self):
        meta = parse_transcript_meta(SAMPLE_TRANSCRIPT, "-home-user-dev-myapp")
        assert meta.start_time == "2025-06-01T10:00:00Z"
        assert meta.last_update == "2025-06-01T10:41:00Z"

    def test_meta_cwd_and_branch(self):
        meta = parse_transcript_meta(SAMPLE_TRANSCRIPT, "-home-user-dev-myapp")
        assert meta.cwd == "/home/user/dev/myapp"
        assert meta.git_branch == "main"

    def test_meta_summary_from_summary_entry(self):
        meta = parse_transcript_meta(SAMPLE_TRANSCRIPT, "-home-user-dev-myapp")
        # Summary entry s1 should override the first user message as summary
        assert "Docker Compose" in meta.summary

    def test_meta_file_size(self):
        meta = parse_transcript_meta(SAMPLE_TRANSCRIPT, "-home-user-dev-myapp")
        assert meta.file_size_bytes == SAMPLE_TRANSCRIPT.stat().st_size

    def test_meta_transcript_path(self):
        meta = parse_transcript_meta(SAMPLE_TRANSCRIPT, "-home-user-dev-myapp")
        assert meta.transcript_path == str(SAMPLE_TRANSCRIPT)

    def test_meta_has_subagents_false_by_default(self):
        meta = parse_transcript_meta(SAMPLE_TRANSCRIPT, "-home-user-dev-myapp")
        assert meta.has_subagents is False

    def test_meta_with_precomputed_entries(self):
        """When entries are provided, uses them directly instead of reading the file."""
        entries = [
            make_entry("user", "2025-06-01T10:00:00Z", "Do something", uuid="e1"),
            make_entry("assistant", "2025-06-01T10:01:00Z", "Done", ["Write"], "e2"),
            make_entry("summary", "2025-06-01T10:05:00Z", "My summary text", uuid="e3"),
        ]
        meta = parse_transcript_meta(SAMPLE_TRANSCRIPT, "-test-proj", entries=entries)
        assert meta is not None
        assert meta.num_user_turns == 1
        assert meta.num_assistant_turns == 1
        assert meta.num_tool_calls == 1
        assert meta.summary == "My summary text"
        assert meta.start_time == "2025-06-01T10:00:00Z"
        assert meta.last_update == "2025-06-01T10:05:00Z"

    def test_meta_nonexistent_file(self):
        meta = parse_transcript_meta(Path("/nonexistent/file.jsonl"), "-test")
        assert meta is None

    def test_meta_has_subagents_true(self, tmp_path):
        """If session subdir with subagents exists, has_subagents should be True."""
        project_dir = tmp_path / "-test-project"
        project_dir.mkdir()
        # Create transcript
        transcript = project_dir / "session-abc.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "user",
                    "timestamp": "2025-06-01T10:00:00Z",
                    "uuid": "x1",
                    "message": {"role": "user", "content": "Hello"},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        # Create subagent directory
        subagent_dir = project_dir / "session-abc" / "subagents"
        subagent_dir.mkdir(parents=True)

        meta = parse_transcript_meta(transcript, "-test-project")
        assert meta is not None
        assert meta.has_subagents is True


# ---------------------------------------------------------------------------
# scan_projects_dir
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestScanProjectsDir:
    def test_scans_temp_projects(self, temp_projects_dir):
        results = scan_projects_dir(temp_projects_dir)
        assert len(results) == 2

    def test_returns_session_ids(self, temp_projects_dir):
        results = scan_projects_dir(temp_projects_dir)
        session_ids = {r[0] for r in results}
        assert "session-001" in session_ids
        assert "session-002" in session_ids

    def test_returns_project_encoded(self, temp_projects_dir):
        results = scan_projects_dir(temp_projects_dir)
        project_encodeds = {r[1] for r in results}
        assert "-home-user-dev-myapp" in project_encodeds
        assert "-home-user-dev-backend" in project_encodeds

    def test_returns_paths(self, temp_projects_dir):
        results = scan_projects_dir(temp_projects_dir)
        for _, _, filepath in results:
            assert filepath.exists()
            assert filepath.suffix == ".jsonl"

    def test_empty_directory(self, tmp_path):
        results = scan_projects_dir(tmp_path)
        assert results == []

    def test_nonexistent_directory(self):
        results = scan_projects_dir(Path("/nonexistent/path"))
        assert results == []

    def test_ignores_non_directories(self, tmp_path):
        """Regular files in base_dir should be ignored."""
        (tmp_path / "stray_file.txt").write_text("not a dir")
        results = scan_projects_dir(tmp_path)
        assert results == []

    def test_skips_subagent_files(self, tmp_path):
        """Files in session subdirectories (subagents) should be skipped."""
        project_dir = tmp_path / "-test-project"
        project_dir.mkdir()
        # Main session file
        (project_dir / "session-main.jsonl").write_text("{}\n")
        # Subagent file (inside session subdirectory)
        sub_dir = project_dir / "session-main"
        sub_dir.mkdir()
        (sub_dir / "subagent.jsonl").write_text("{}\n")

        results = scan_projects_dir(tmp_path)
        session_ids = [r[0] for r in results]
        assert "session-main" in session_ids
        # The glob("*.jsonl") on project_dir won't match files in subdirectories
        assert len(results) == 1

    def test_multiple_sessions_in_one_project(self, tmp_path):
        project_dir = tmp_path / "-test-project"
        project_dir.mkdir()
        (project_dir / "session-a.jsonl").write_text("{}\n")
        (project_dir / "session-b.jsonl").write_text("{}\n")

        results = scan_projects_dir(tmp_path)
        session_ids = {r[0] for r in results}
        assert "session-a" in session_ids
        assert "session-b" in session_ids
        assert len(results) == 2


# ---------------------------------------------------------------------------
# SessionMeta.from_dict / to_dict roundtrip
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSessionMetaRoundtrip:
    def test_to_dict(self):
        meta = make_meta()
        d = meta.to_dict()
        assert d["session_id"] == "test-session-001"
        assert d["project_path"] == "/home/user/dev/myapp"
        assert d["num_user_turns"] == 5
        assert d["has_subagents"] is False

    def test_from_dict(self):
        data = {
            "session_id": "s1",
            "project_encoded": "-home-user-dev-app",
            "project_path": "/home/user/dev/app",
            "cwd": "/home/user/dev/app",
            "git_branch": "main",
            "start_time": "2025-01-01T00:00:00Z",
            "last_update": "2025-01-01T01:00:00Z",
            "num_user_turns": 3,
            "num_assistant_turns": 3,
            "num_tool_calls": 5,
            "summary": "A session",
            "transcript_path": "/tmp/s1.jsonl",
            "has_subagents": False,
            "file_size_bytes": 1234,
        }
        meta = SessionMeta.from_dict(data)
        assert meta.session_id == "s1"
        assert meta.num_tool_calls == 5
        assert meta.has_subagents is False

    def test_roundtrip(self):
        original = make_meta()
        d = original.to_dict()
        restored = SessionMeta.from_dict(d)
        assert restored == original

    def test_from_dict_with_string_ints(self):
        """Integer fields stored as strings should be coerced."""
        data = make_meta().to_dict()
        data["num_user_turns"] = "5"
        data["num_assistant_turns"] = "5"
        data["num_tool_calls"] = "10"
        data["file_size_bytes"] = "5000"
        meta = SessionMeta.from_dict(data)
        assert meta.num_user_turns == 5
        assert meta.num_assistant_turns == 5
        assert meta.num_tool_calls == 10
        assert meta.file_size_bytes == 5000

    def test_from_dict_with_string_bool(self):
        """has_subagents stored as string should be coerced."""
        data = make_meta().to_dict()
        data["has_subagents"] = "true"
        meta = SessionMeta.from_dict(data)
        assert meta.has_subagents is True

        data["has_subagents"] = "false"
        meta = SessionMeta.from_dict(data)
        assert meta.has_subagents is False

    def test_from_dict_ignores_extra_keys(self):
        data = make_meta().to_dict()
        data["extra_field"] = "should be ignored"
        meta = SessionMeta.from_dict(data)
        assert not hasattr(meta, "extra_field")


# ---------------------------------------------------------------------------
# SessionEntry.from_dict / to_dict roundtrip
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSessionEntryRoundtrip:
    def test_to_dict(self):
        entry = make_entry("assistant", "2025-06-01T10:00:00Z", "Response", ["Write", "Edit"], "uuid1")
        d = entry.to_dict()
        assert d["entry_type"] == "assistant"
        assert d["content"] == "Response"
        assert d["tool_names"] == ["Write", "Edit"]
        assert d["uuid"] == "uuid1"

    def test_from_dict(self):
        data = {
            "entry_type": "user",
            "timestamp": "2025-06-01T10:00:00Z",
            "content": "Hello",
            "tool_names": [],
            "uuid": "abc",
        }
        entry = SessionEntry.from_dict(data)
        assert entry.entry_type == "user"
        assert entry.content == "Hello"
        assert entry.uuid == "abc"

    def test_roundtrip(self):
        original = make_entry("assistant", "2025-06-01T10:00:00Z", "Done", ["Bash"], "xyz")
        d = original.to_dict()
        restored = SessionEntry.from_dict(d)
        assert restored == original

    def test_from_dict_with_string_tool_names(self):
        """tool_names stored as JSON string should be parsed."""
        data = {
            "entry_type": "assistant",
            "timestamp": "2025-06-01T10:00:00Z",
            "content": "Done",
            "tool_names": '["Write", "Edit"]',
            "uuid": "abc",
        }
        entry = SessionEntry.from_dict(data)
        assert entry.tool_names == ["Write", "Edit"]

    def test_from_dict_ignores_extra_keys(self):
        data = {
            "entry_type": "user",
            "timestamp": "2025-06-01T10:00:00Z",
            "content": "Hello",
            "tool_names": [],
            "uuid": "abc",
            "extra": "ignored",
        }
        entry = SessionEntry.from_dict(data)
        assert not hasattr(entry, "extra")

    def test_from_dict_defaults(self):
        """Minimal dict should use defaults for optional fields."""
        data = {
            "entry_type": "user",
            "timestamp": "2025-06-01T10:00:00Z",
            "content": "Hello",
        }
        entry = SessionEntry.from_dict(data)
        assert entry.tool_names == []
        # uuid is not in data, so it uses default from dataclass
        # But from_dict filters keys, so uuid won't be passed -> uses default ""
        assert entry.uuid == ""


# ---------------------------------------------------------------------------
# Edge cases & integration-level parser tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParserEdgeCases:
    def test_empty_file(self, tmp_path):
        transcript = tmp_path / "empty.jsonl"
        transcript.write_text("", encoding="utf-8")
        entries, offset = parse_transcript_entries(transcript)
        assert entries == []
        assert offset == 0

    def test_whitespace_only_file(self, tmp_path):
        transcript = tmp_path / "whitespace.jsonl"
        transcript.write_text("   \n\n  \n", encoding="utf-8")
        entries, offset = parse_transcript_entries(transcript)
        assert entries == []

    def test_single_entry_file(self, tmp_path):
        transcript = tmp_path / "single.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "user",
                    "timestamp": "2025-06-01T10:00:00Z",
                    "uuid": "only1",
                    "message": {"role": "user", "content": "The only message"},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        entries, offset = parse_transcript_entries(transcript)
        assert len(entries) == 1
        assert entries[0].uuid == "only1"
        assert entries[0].content == "The only message"

    def test_summary_without_summary_key(self, tmp_path):
        """Summary entry without 'summary' key should be skipped."""
        transcript = tmp_path / "nosummary.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "summary",
                    "timestamp": "2025-06-01T10:00:00Z",
                    "uuid": "s_bad",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        entries, _ = parse_transcript_entries(transcript)
        assert len(entries) == 0

    def test_system_entry_with_non_string_content(self, tmp_path):
        """System entry with non-string content should be skipped."""
        transcript = tmp_path / "sys_nonstr.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "system",
                    "timestamp": "2025-06-01T10:00:00Z",
                    "uuid": "sys_bad",
                    "message": {"role": "system", "content": ["not", "a", "string"]},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        entries, _ = parse_transcript_entries(transcript)
        assert len(entries) == 0

    def test_parse_meta_from_malformed_file(self):
        """Meta extraction from malformed file should still produce valid meta."""
        meta = parse_transcript_meta(MALFORMED_TRANSCRIPT, "-test-proj")
        assert meta is not None
        assert meta.session_id == "malformed_transcript"
        assert meta.num_user_turns >= 2  # m1 and m7

    def test_decode_project_path_used_in_meta(self):
        meta = parse_transcript_meta(SAMPLE_TRANSCRIPT, "-home-user-dev-myapp")
        assert meta.project_path == decode_project_path("-home-user-dev-myapp")

    def test_assistant_entry_only_tool_use_no_text(self, tmp_path):
        """Assistant with tool_use but no text should still be indexed (has tool_names)."""
        transcript = tmp_path / "toolonly.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2025-06-01T10:00:00Z",
                    "uuid": "a_toolonly",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "ls"}},
                        ],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        entries, _ = parse_transcript_entries(transcript)
        assert len(entries) == 1
        assert entries[0].tool_names == ["Bash"]
        assert entries[0].content == ""

    def test_assistant_entry_empty_content_list(self, tmp_path):
        """Assistant with empty content list should be skipped (no text, no tools)."""
        transcript = tmp_path / "empty_assistant.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2025-06-01T10:00:00Z",
                    "uuid": "a_empty",
                    "message": {"role": "assistant", "content": []},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        entries, _ = parse_transcript_entries(transcript)
        assert len(entries) == 0

    def test_all_timestamps_preserved(self):
        """Every parsed entry should have a timestamp set."""
        entries, _ = parse_transcript_entries(SAMPLE_TRANSCRIPT)
        for entry in entries:
            assert entry.timestamp != ""
            assert entry.timestamp.startswith("2025-")
