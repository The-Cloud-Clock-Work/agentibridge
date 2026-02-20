"""Unit tests for agentibridge.embeddings module.

Tests cosine similarity, embedding backend selection, TranscriptEmbedder
chunk/text building, and availability checks.
"""

import math

import pytest

from agentibridge.parser import SessionEntry
from agentibridge.embeddings import (
    _cosine_similarity,
    _cosine_similarity_batch,
    _get_embed_fn,
    TranscriptEmbedder,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry(
    entry_type: str = "user",
    timestamp: str = "2025-06-01T10:00:00Z",
    content: str = "Hello",
    tool_names: list | None = None,
    uuid: str = "u1",
) -> SessionEntry:
    return SessionEntry(
        entry_type=entry_type,
        timestamp=timestamp,
        content=content,
        tool_names=tool_names or [],
        uuid=uuid,
    )


# ===================================================================
# _cosine_similarity
# ===================================================================


@pytest.mark.unit
class TestCosineSimilarity:
    """Tests for the pure-Python _cosine_similarity function."""

    def test_identical_vectors(self):
        """Identical vectors should have cosine similarity of 1.0."""
        a = [1.0, 2.0, 3.0]
        assert _cosine_similarity(a, a) == pytest.approx(1.0)

    def test_identical_unit_vectors(self):
        """Identical unit vectors should have cosine similarity of 1.0."""
        a = [1.0, 0.0, 0.0]
        assert _cosine_similarity(a, a) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        """Orthogonal vectors should have cosine similarity of 0.0."""
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert _cosine_similarity(a, b) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        """Opposite vectors should have cosine similarity of -1.0."""
        a = [1.0, 2.0, 3.0]
        b = [-1.0, -2.0, -3.0]
        assert _cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_zero_norm_vector_a(self):
        """Zero-norm vector A should return 0.0."""
        a = [0.0, 0.0, 0.0]
        b = [1.0, 2.0, 3.0]
        assert _cosine_similarity(a, b) == 0.0

    def test_zero_norm_vector_b(self):
        """Zero-norm vector B should return 0.0."""
        a = [1.0, 2.0, 3.0]
        b = [0.0, 0.0, 0.0]
        assert _cosine_similarity(a, b) == 0.0

    def test_both_zero_norm(self):
        """Both zero-norm vectors should return 0.0."""
        a = [0.0, 0.0]
        b = [0.0, 0.0]
        assert _cosine_similarity(a, b) == 0.0

    def test_known_angle(self):
        """45-degree angle vectors should produce cos(45) ~ 0.7071."""
        a = [1.0, 0.0]
        b = [1.0, 1.0]
        expected = 1.0 / math.sqrt(2)
        assert _cosine_similarity(a, b) == pytest.approx(expected, abs=1e-6)

    def test_parallel_different_magnitude(self):
        """Parallel vectors with different magnitudes should have similarity 1.0."""
        a = [1.0, 2.0, 3.0]
        b = [2.0, 4.0, 6.0]
        assert _cosine_similarity(a, b) == pytest.approx(1.0)

    def test_single_dimension(self):
        """Single-dimension vectors."""
        assert _cosine_similarity([5.0], [3.0]) == pytest.approx(1.0)
        assert _cosine_similarity([5.0], [-3.0]) == pytest.approx(-1.0)


# ===================================================================
# _cosine_similarity_batch
# ===================================================================


@pytest.mark.unit
class TestCosineSimilarityBatch:
    """Tests for _cosine_similarity_batch (numpy path and fallback)."""

    def test_identical_vectors_batch(self):
        """Batch: identical vectors should produce 1.0."""
        query = [1.0, 2.0, 3.0]
        vectors = [[1.0, 2.0, 3.0]]
        scores = _cosine_similarity_batch(query, vectors)
        assert len(scores) == 1
        assert scores[0] == pytest.approx(1.0, abs=1e-5)

    def test_orthogonal_vectors_batch(self):
        """Batch: orthogonal vectors should produce 0.0."""
        query = [1.0, 0.0, 0.0]
        vectors = [[0.0, 1.0, 0.0]]
        scores = _cosine_similarity_batch(query, vectors)
        assert len(scores) == 1
        assert scores[0] == pytest.approx(0.0, abs=1e-5)

    def test_opposite_vectors_batch(self):
        """Batch: opposite vectors should produce -1.0."""
        query = [1.0, 2.0, 3.0]
        vectors = [[-1.0, -2.0, -3.0]]
        scores = _cosine_similarity_batch(query, vectors)
        assert len(scores) == 1
        assert scores[0] == pytest.approx(-1.0, abs=1e-5)

    def test_multiple_vectors(self):
        """Batch with multiple vectors returns correct order of scores."""
        query = [1.0, 0.0, 0.0]
        vectors = [
            [1.0, 0.0, 0.0],  # identical -> 1.0
            [0.0, 1.0, 0.0],  # orthogonal -> 0.0
            [-1.0, 0.0, 0.0],  # opposite -> -1.0
        ]
        scores = _cosine_similarity_batch(query, vectors)
        assert len(scores) == 3
        assert scores[0] == pytest.approx(1.0, abs=1e-5)
        assert scores[1] == pytest.approx(0.0, abs=1e-5)
        assert scores[2] == pytest.approx(-1.0, abs=1e-5)

    def test_zero_norm_in_batch(self):
        """Batch: zero-norm vector among others is handled (no crash)."""
        query = [1.0, 2.0, 3.0]
        vectors = [
            [0.0, 0.0, 0.0],  # zero-norm
            [1.0, 2.0, 3.0],  # identical
        ]
        scores = _cosine_similarity_batch(query, vectors)
        assert len(scores) == 2
        # Zero-norm vector: numpy path replaces 0 norm with 1.0, so dot/1.0;
        # fallback returns 0.0. Either way it should not crash.
        assert isinstance(scores[0], float)
        assert scores[1] == pytest.approx(1.0, abs=1e-5)

    def test_empty_vectors_list_numpy_raises(self):
        """Batch with empty vectors list raises ValueError via numpy path."""
        query = [1.0, 2.0, 3.0]
        # numpy matmul fails on empty 2D array with mismatched dimensions
        with pytest.raises(ValueError):
            _cosine_similarity_batch(query, [])

    def test_empty_vectors_list_fallback_returns_empty(self, monkeypatch):
        """Batch with empty vectors list returns empty via pure-Python fallback."""
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "numpy":
                raise ImportError("mocked numpy unavailable")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        query = [1.0, 2.0, 3.0]
        scores = _cosine_similarity_batch(query, [])
        assert scores == []

    def test_fallback_without_numpy(self, monkeypatch):
        """When numpy is not importable, falls back to pure-Python path."""
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "numpy":
                raise ImportError("mocked numpy unavailable")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        query = [1.0, 0.0]
        vectors = [[1.0, 0.0], [0.0, 1.0]]
        scores = _cosine_similarity_batch(query, vectors)
        assert len(scores) == 2
        assert scores[0] == pytest.approx(1.0)
        assert scores[1] == pytest.approx(0.0)


# ===================================================================
# _get_embed_fn
# ===================================================================


@pytest.mark.unit
class TestGetEmbedFn:
    """Tests for _get_embed_fn LLM API backend selection."""

    def test_no_config_returns_none(self, monkeypatch):
        """When LLM_API_BASE is not set, returns None."""
        monkeypatch.delenv("LLM_API_BASE", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        assert _get_embed_fn() is None

    def test_missing_key_returns_none(self, monkeypatch):
        """When LLM_API_KEY is not set, returns None."""
        monkeypatch.setenv("LLM_API_BASE", "https://api.example.com/v1")
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        assert _get_embed_fn() is None

    def test_configured_returns_embed_text(self, monkeypatch):
        """When LLM_API_BASE + LLM_API_KEY + LLM_EMBED_MODEL are set, returns embed_text."""
        from agentibridge.llm_client import embed_text

        monkeypatch.setenv("LLM_API_BASE", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_API_KEY", "sk-test")
        monkeypatch.setenv("LLM_EMBED_MODEL", "text-embedding-3-small")
        fn = _get_embed_fn()
        assert fn is embed_text

    def test_missing_embed_model_returns_none(self, monkeypatch):
        """When LLM_EMBED_MODEL is empty, returns None."""
        monkeypatch.setenv("LLM_API_BASE", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_API_KEY", "sk-test")
        monkeypatch.setenv("LLM_EMBED_MODEL", "")
        assert _get_embed_fn() is None


# ===================================================================
# TranscriptEmbedder.is_available
# ===================================================================


@pytest.mark.unit
class TestTranscriptEmbedderIsAvailable:
    """Tests for TranscriptEmbedder.is_available."""

    def test_not_available_no_config(self, monkeypatch):
        """is_available returns False when no LLM API is configured."""
        monkeypatch.delenv("LLM_API_BASE", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        embedder = TranscriptEmbedder()
        assert embedder.is_available() is False

    def test_available_with_llm_api(self, monkeypatch):
        """is_available returns True when LLM API is configured."""
        monkeypatch.setenv("LLM_API_BASE", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_API_KEY", "sk-test")
        monkeypatch.setenv("LLM_EMBED_MODEL", "text-embedding-3-small")
        embedder = TranscriptEmbedder()
        assert embedder.is_available() is True

    def test_caches_embed_fn(self, monkeypatch):
        """is_available caches the embed function lookup."""
        monkeypatch.setenv("LLM_API_BASE", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_API_KEY", "sk-test")
        monkeypatch.setenv("LLM_EMBED_MODEL", "text-embedding-3-small")
        embedder = TranscriptEmbedder()

        # First call sets the cache
        assert embedder.is_available() is True
        assert embedder._embed_checked is True

        # Change env var -- cached result should still be True
        monkeypatch.delenv("LLM_API_BASE", raising=False)
        assert embedder.is_available() is True

    def test_not_available_missing_key(self, monkeypatch):
        """is_available returns False when API key is missing."""
        monkeypatch.setenv("LLM_API_BASE", "https://api.example.com/v1")
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        embedder = TranscriptEmbedder()
        assert embedder.is_available() is False


# ===================================================================
# TranscriptEmbedder._chunk_turns
# ===================================================================


@pytest.mark.unit
class TestChunkTurns:
    """Tests for TranscriptEmbedder._chunk_turns."""

    def setup_method(self):
        self.embedder = TranscriptEmbedder()

    def test_empty_entries(self):
        """Empty entries list produces empty chunks."""
        chunks = self.embedder._chunk_turns([])
        assert chunks == []

    def test_single_user_assistant_pair(self):
        """Single user+assistant pair produces one chunk."""
        entries = [
            _entry("user", "2025-06-01T10:00:00Z", "Hello"),
            _entry("assistant", "2025-06-01T10:01:00Z", "Hi there"),
        ]
        chunks = self.embedder._chunk_turns(entries)
        assert len(chunks) == 1
        assert "User: Hello" in chunks[0]["text"]
        assert "Assistant: Hi there" in chunks[0]["text"]
        assert chunks[0]["timestamp"] == "2025-06-01T10:00:00Z"

    def test_multiple_pairs(self):
        """Multiple user+assistant pairs produce multiple chunks."""
        entries = [
            _entry("user", "2025-06-01T10:00:00Z", "First question"),
            _entry("assistant", "2025-06-01T10:01:00Z", "First answer"),
            _entry("user", "2025-06-01T10:05:00Z", "Second question"),
            _entry("assistant", "2025-06-01T10:06:00Z", "Second answer"),
        ]
        chunks = self.embedder._chunk_turns(entries)
        assert len(chunks) == 2
        assert "First question" in chunks[0]["text"]
        assert "First answer" in chunks[0]["text"]
        assert "Second question" in chunks[1]["text"]
        assert "Second answer" in chunks[1]["text"]

    def test_summary_entry_included(self):
        """Summary entries are included in the current chunk text."""
        entries = [
            _entry("user", "2025-06-01T10:00:00Z", "Do the thing"),
            _entry("assistant", "2025-06-01T10:01:00Z", "Done"),
            _entry("summary", "2025-06-01T10:02:00Z", "Session accomplished the thing"),
        ]
        chunks = self.embedder._chunk_turns(entries)
        assert len(chunks) == 1
        assert "Summary: Session accomplished the thing" in chunks[0]["text"]

    def test_tool_names_included(self):
        """Tool names from assistant entries are included in chunk text."""
        entries = [
            _entry("user", "2025-06-01T10:00:00Z", "Create a file"),
            _entry(
                "assistant",
                "2025-06-01T10:01:00Z",
                "Created the file",
                tool_names=["Write", "Bash"],
            ),
        ]
        chunks = self.embedder._chunk_turns(entries)
        assert len(chunks) == 1
        assert "Tools used: Write, Bash" in chunks[0]["text"]

    def test_tool_names_not_present_when_empty(self):
        """When tool_names is empty, no 'Tools used' line appears."""
        entries = [
            _entry("user", "2025-06-01T10:00:00Z", "Question"),
            _entry("assistant", "2025-06-01T10:01:00Z", "Answer", tool_names=[]),
        ]
        chunks = self.embedder._chunk_turns(entries)
        assert len(chunks) == 1
        assert "Tools used" not in chunks[0]["text"]

    def test_user_only_at_end(self):
        """A trailing user entry with no assistant response creates its own chunk."""
        entries = [
            _entry("user", "2025-06-01T10:00:00Z", "First"),
            _entry("assistant", "2025-06-01T10:01:00Z", "Response"),
            _entry("user", "2025-06-01T10:05:00Z", "Trailing question"),
        ]
        chunks = self.embedder._chunk_turns(entries)
        assert len(chunks) == 2
        assert "Trailing question" in chunks[1]["text"]

    def test_assistant_before_user(self):
        """Assistant entry before any user entry is included in initial chunk."""
        entries = [
            _entry("assistant", "2025-06-01T10:00:00Z", "Initial response"),
            _entry("user", "2025-06-01T10:01:00Z", "Follow-up"),
        ]
        chunks = self.embedder._chunk_turns(entries)
        # First chunk is the assistant text (no user prefix), second is user
        assert len(chunks) == 2
        assert "Assistant: Initial response" in chunks[0]["text"]
        assert "User: Follow-up" in chunks[1]["text"]

    def test_multiple_assistants_per_user(self):
        """Multiple assistant entries after one user merge into the same chunk."""
        entries = [
            _entry("user", "2025-06-01T10:00:00Z", "Do multiple things"),
            _entry("assistant", "2025-06-01T10:01:00Z", "First part done"),
            _entry("assistant", "2025-06-01T10:02:00Z", "Second part done"),
        ]
        chunks = self.embedder._chunk_turns(entries)
        assert len(chunks) == 1
        assert "First part done" in chunks[0]["text"]
        assert "Second part done" in chunks[0]["text"]

    def test_system_entries_ignored(self):
        """System entries are not included in chunk text (not handled by _chunk_turns)."""
        entries = [
            _entry("user", "2025-06-01T10:00:00Z", "Hello"),
            _entry("system", "2025-06-01T10:00:30Z", "System notice"),
            _entry("assistant", "2025-06-01T10:01:00Z", "Hi"),
        ]
        chunks = self.embedder._chunk_turns(entries)
        assert len(chunks) == 1
        assert "System notice" not in chunks[0]["text"]

    def test_timestamp_comes_from_user_entry(self):
        """Chunk timestamp is set from the user entry that starts the chunk."""
        entries = [
            _entry("user", "2025-06-01T10:00:00Z", "Start"),
            _entry("assistant", "2025-06-01T10:05:00Z", "End"),
        ]
        chunks = self.embedder._chunk_turns(entries)
        assert chunks[0]["timestamp"] == "2025-06-01T10:00:00Z"

    def test_summary_between_turns(self):
        """Summary between turns is appended to the current chunk."""
        entries = [
            _entry("user", "2025-06-01T10:00:00Z", "Q1"),
            _entry("assistant", "2025-06-01T10:01:00Z", "A1"),
            _entry("summary", "2025-06-01T10:02:00Z", "Mid-session summary"),
            _entry("user", "2025-06-01T10:03:00Z", "Q2"),
            _entry("assistant", "2025-06-01T10:04:00Z", "A2"),
        ]
        chunks = self.embedder._chunk_turns(entries)
        # The summary attaches to the first chunk (before user Q2 starts a new one)
        assert len(chunks) == 2
        assert "Mid-session summary" in chunks[0]["text"]


# ===================================================================
# TranscriptEmbedder._build_transcript_text
# ===================================================================


@pytest.mark.unit
class TestBuildTranscriptText:
    """Tests for TranscriptEmbedder._build_transcript_text."""

    def setup_method(self):
        self.embedder = TranscriptEmbedder()

    def test_empty_entries(self):
        """Empty entries produce empty text."""
        text = self.embedder._build_transcript_text([])
        assert text == ""

    def test_user_label(self):
        """User entries are prefixed with 'User:'."""
        entries = [_entry("user", content="Hello world")]
        text = self.embedder._build_transcript_text(entries)
        assert text.startswith("User: Hello world")

    def test_assistant_label(self):
        """Assistant entries are prefixed with 'Assistant:'."""
        entries = [_entry("assistant", content="Response text")]
        text = self.embedder._build_transcript_text(entries)
        assert text.startswith("Assistant: Response text")

    def test_summary_label(self):
        """Summary entries are prefixed with 'Summary:'."""
        entries = [_entry("summary", content="Session summary here")]
        text = self.embedder._build_transcript_text(entries)
        assert text.startswith("Summary: Session summary here")

    def test_assistant_with_tools(self):
        """Assistant entries with tools show tool names in brackets."""
        entries = [
            _entry("assistant", content="Did things", tool_names=["Write", "Bash"]),
        ]
        text = self.embedder._build_transcript_text(entries)
        assert "[tools: Write, Bash]" in text
        assert text.startswith("Assistant [tools: Write, Bash]: Did things")

    def test_assistant_without_tools(self):
        """Assistant entries without tools have no bracket section."""
        entries = [
            _entry("assistant", content="Just text", tool_names=[]),
        ]
        text = self.embedder._build_transcript_text(entries)
        assert "[tools:" not in text
        assert text.startswith("Assistant: Just text")

    def test_skips_non_indexable_types(self):
        """Non-indexable types (system, progress, etc.) are skipped."""
        entries = [
            _entry("user", content="Hello"),
            _entry("system", content="System message"),
            _entry("assistant", content="Hi"),
        ]
        text = self.embedder._build_transcript_text(entries)
        # system is not user/assistant/summary, so the code skips it via `else: continue`
        assert "System message" not in text
        assert "User: Hello" in text
        assert "Assistant: Hi" in text

    def test_truncates_at_max_chars(self):
        """Output is truncated when it exceeds max_chars."""
        entries = [
            _entry("user", content="A" * 200),
            _entry("assistant", content="B" * 200),
            _entry("user", content="C" * 200),
        ]
        text = self.embedder._build_transcript_text(entries, max_chars=300)
        assert "... (truncated)" in text
        # Should not contain the third entry's content
        assert "C" * 50 not in text

    def test_truncates_exactly_at_boundary(self):
        """When a line exactly reaches max_chars, the next line triggers truncation."""
        entries = [
            _entry("user", content="X" * 10),
            _entry("assistant", content="Y" * 10),
        ]
        # "User: " + 10 chars = 16 chars for first line
        # Set max_chars so first line fits but second does not
        text = self.embedder._build_transcript_text(entries, max_chars=20)
        assert "User: " + "X" * 10 in text
        assert "... (truncated)" in text

    def test_content_truncated_per_entry(self):
        """Individual entry content is truncated to 500 chars."""
        long_content = "Z" * 1000
        entries = [_entry("user", content=long_content)]
        text = self.embedder._build_transcript_text(entries, max_chars=100000)
        # The code does entry.content[:500], so max 500 Z's plus "User: " prefix
        z_count = text.count("Z")
        assert z_count == 500

    def test_full_conversation_text(self):
        """Full conversation with all types is rendered correctly."""
        entries = [
            _entry("user", "2025-06-01T10:00:00Z", "Create an API"),
            _entry(
                "assistant",
                "2025-06-01T10:01:00Z",
                "Created CRUD endpoints",
                tool_names=["Write", "Edit"],
            ),
            _entry("summary", "2025-06-01T10:10:00Z", "Built REST API"),
        ]
        text = self.embedder._build_transcript_text(entries)
        lines = text.split("\n")
        assert len(lines) == 3
        assert lines[0] == "User: Create an API"
        assert lines[1] == "Assistant [tools: Write, Edit]: Created CRUD endpoints"
        assert lines[2] == "Summary: Built REST API"

    def test_large_max_chars_no_truncation(self):
        """With a large max_chars, no truncation occurs."""
        entries = [
            _entry("user", content="Hello"),
            _entry("assistant", content="World"),
        ]
        text = self.embedder._build_transcript_text(entries, max_chars=100000)
        assert "... (truncated)" not in text
        assert "User: Hello" in text
        assert "Assistant: World" in text

    def test_only_non_indexable_entries(self):
        """If all entries are non-indexable types, result is empty."""
        entries = [
            _entry("system", content="System msg 1"),
            _entry("system", content="System msg 2"),
        ]
        text = self.embedder._build_transcript_text(entries)
        assert text == ""

    def test_max_chars_zero(self):
        """With max_chars=0, everything gets truncated immediately."""
        entries = [_entry("user", content="Hello")]
        text = self.embedder._build_transcript_text(entries, max_chars=0)
        assert text == "... (truncated)"
