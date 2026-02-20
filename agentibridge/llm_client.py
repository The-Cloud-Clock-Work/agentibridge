"""OpenAI-compatible API client for embeddings and chat completions.

Works with any provider that implements the OpenAI API format:
OpenRouter, LiteLLM, OpenAI, local servers, etc.

Env vars:
    LLM_API_BASE    — base URL (e.g. https://openrouter.ai/api/v1)
    LLM_API_KEY     — API key
    LLM_EMBED_MODEL — embedding model (e.g. text-embedding-3-small)
    LLM_CHAT_MODEL  — chat model for summaries (e.g. anthropic/claude-sonnet-4-5)
    CF_ACCESS_CLIENT_ID     — Cloudflare Access service-token client ID (optional)
    CF_ACCESS_CLIENT_SECRET — Cloudflare Access service-token client secret (optional)
"""

import os
from typing import Dict, List, Optional

import httpx


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _api_base() -> str:
    return os.environ.get("LLM_API_BASE", "")


def _api_key() -> str:
    return os.environ.get("LLM_API_KEY", "")


def _request_headers() -> Dict[str, str]:
    """Build common request headers, including CF Access if configured."""
    headers: Dict[str, str] = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }
    cf_id = os.environ.get("CF_ACCESS_CLIENT_ID", "")
    cf_secret = os.environ.get("CF_ACCESS_CLIENT_SECRET", "")
    if cf_id and cf_secret:
        headers["CF-Access-Client-Id"] = cf_id
        headers["CF-Access-Client-Secret"] = cf_secret
    return headers


def _embed_model() -> str:
    return os.environ.get("LLM_EMBED_MODEL", "text-embedding-3-small")


def _chat_model() -> str:
    return os.environ.get("LLM_CHAT_MODEL", "")


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------

def is_configured() -> bool:
    """Return True if LLM_API_BASE and LLM_API_KEY are set."""
    return bool(_api_base()) and bool(_api_key())


def is_embed_configured() -> bool:
    """Return True if embedding via LLM API is possible."""
    return is_configured() and bool(_embed_model())


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------

def embed_text(text: str, model: Optional[str] = None) -> List[float]:
    """Generate an embedding vector via POST {base_url}/embeddings.

    Args:
        text: The text to embed.
        model: Override embedding model (default: LLM_EMBED_MODEL).

    Returns:
        List of floats (the embedding vector).

    Raises:
        RuntimeError: If LLM_API_BASE / LLM_API_KEY are not configured.
        httpx.HTTPStatusError: On HTTP errors from the API.
    """
    base = _api_base()
    key = _api_key()
    if not base or not key:
        raise RuntimeError("LLM_API_BASE and LLM_API_KEY must be set for embeddings")

    model = model or _embed_model()

    resp = httpx.post(
        f"{base.rstrip('/')}/embeddings",
        headers=_request_headers(),
        json={"model": model, "input": text},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["data"][0]["embedding"]


# ---------------------------------------------------------------------------
# Chat completions
# ---------------------------------------------------------------------------

def chat_completion(prompt: str, model: Optional[str] = None) -> str:
    """Get a chat completion via POST {base_url}/chat/completions.

    Args:
        prompt: The user message.
        model: Override chat model (default: LLM_CHAT_MODEL).

    Returns:
        The assistant's response text.

    Raises:
        RuntimeError: If LLM_API_BASE / LLM_API_KEY are not configured.
        httpx.HTTPStatusError: On HTTP errors from the API.
    """
    base = _api_base()
    key = _api_key()
    if not base or not key:
        raise RuntimeError("LLM_API_BASE and LLM_API_KEY must be set for chat completions")

    model = model or _chat_model()
    if not model:
        raise RuntimeError("LLM_CHAT_MODEL must be set (or pass model= explicitly)")

    resp = httpx.post(
        f"{base.rstrip('/')}/chat/completions",
        headers=_request_headers(),
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=120.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]
