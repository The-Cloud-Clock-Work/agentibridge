"""Agent registry for A2A discovery.

Agents self-register on boot and send periodic heartbeats.
Any agent can discover peers via list_agents / find_agents.

Storage: Redis (primary) with file fallback — same pattern as dispatch.py.
"""

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from agentibridge.logging import log
from agentibridge.redis_client import get_redis

_AGENTS_DIR = Path("/tmp/agentibridge_agents")
_KEY_PREFIX: str = "agentibridge"
_DEFAULT_HEARTBEAT_TTL = 300  # 5 minutes


@dataclass
class AgentRecord:
    agent_id: str
    agent_name: str = ""
    agent_type: str = ""
    capabilities: list = field(default_factory=list)
    endpoint: str = ""
    status: str = "online"
    metadata: dict = field(default_factory=dict)
    registered_at: str = ""
    last_heartbeat: str = ""
    heartbeat_ttl: int = _DEFAULT_HEARTBEAT_TTL


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------


def _rkey(suffix: str) -> str:
    return f"{_KEY_PREFIX}:sb:{suffix}"


def _agent_path(agent_id: str) -> Path:
    return _AGENTS_DIR / f"{agent_id}.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_ts() -> float:
    return time.time()


def _iso_to_ts(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso).timestamp()
    except (ValueError, TypeError):
        return _now_ts()


# ---------------------------------------------------------------------------
# Effective status (read-time projection, no writes)
# ---------------------------------------------------------------------------


def _compute_effective_status(data: dict) -> str:
    stored = data.get("status", "online")
    if stored == "offline":
        return "offline"
    try:
        last = _iso_to_ts(data.get("last_heartbeat", ""))
        ttl = int(data.get("heartbeat_ttl", _DEFAULT_HEARTBEAT_TTL))
        if (_now_ts() - last) > ttl:
            return "offline"
    except (KeyError, ValueError, TypeError):
        pass
    return stored


# ---------------------------------------------------------------------------
# File fallback
# ---------------------------------------------------------------------------


def _write_file(agent_id: str, data: dict) -> None:
    _AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    _agent_path(agent_id).write_text(json.dumps(data))


def _read_file(agent_id: str) -> Optional[dict]:
    path = _agent_path(agent_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _delete_file(agent_id: str) -> None:
    _agent_path(agent_id).unlink(missing_ok=True)


def _list_files(agent_type: str, capability: str, status: str, limit: int) -> List[dict]:
    if not _AGENTS_DIR.exists():
        return []
    agents: List[dict] = []
    files = sorted(_AGENTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files:
        try:
            data = json.loads(f.read_text())
        except Exception:
            continue
        data["effective_status"] = _compute_effective_status(data)
        if agent_type and data.get("agent_type") != agent_type:
            continue
        if capability and capability not in data.get("capabilities", []):
            continue
        if status and data["effective_status"] != status:
            continue
        agents.append(data)
        if len(agents) >= limit:
            break
    return agents


# ---------------------------------------------------------------------------
# Redis storage
# ---------------------------------------------------------------------------


def _serialize(data: dict) -> dict:
    return {k: json.dumps(v) if not isinstance(v, str) else v for k, v in data.items()}


def _deserialize(raw: dict) -> dict:
    result = {}
    for k, v in raw.items():
        try:
            result[k] = json.loads(v)
        except (json.JSONDecodeError, TypeError):
            result[k] = v
    return result


def _read_redis(agent_id: str) -> Optional[dict]:
    r = get_redis()
    if r is None:
        return None
    try:
        data = r.hgetall(_rkey(f"agent:{agent_id}"))
        if not data:
            return None
        return _deserialize(data)
    except Exception:
        return None


def _write_redis(agent_id: str, data: dict, ttl: int) -> None:
    r = get_redis()
    if r is None:
        return
    try:
        hash_key = _rkey(f"agent:{agent_id}")
        r.hset(hash_key, mapping=_serialize(data))
        r.expire(hash_key, ttl * 2)
        score = _iso_to_ts(data.get("last_heartbeat", ""))
        r.zadd(_rkey("idx:agents"), {agent_id: score})
        # Type index
        if data.get("agent_type"):
            r.zadd(_rkey(f"idx:agents:type:{data['agent_type']}"), {agent_id: score})
        # Capability indices
        for cap in data.get("capabilities", []):
            r.sadd(_rkey(f"idx:agents:cap:{cap}"), agent_id)
    except Exception as e:
        log("registry: Redis write failed", {"agent_id": agent_id, "error": str(e)})


def _delete_redis(agent_id: str, capabilities: List[str], agent_type: str) -> None:
    r = get_redis()
    if r is None:
        return
    try:
        r.delete(_rkey(f"agent:{agent_id}"))
        r.zrem(_rkey("idx:agents"), agent_id)
        if agent_type:
            r.zrem(_rkey(f"idx:agents:type:{agent_type}"), agent_id)
        for cap in capabilities:
            r.srem(_rkey(f"idx:agents:cap:{cap}"), agent_id)
    except Exception as e:
        log("registry: Redis delete failed", {"agent_id": agent_id, "error": str(e)})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def register_agent(
    agent_id: str,
    agent_name: str = "",
    agent_type: str = "",
    capabilities: Optional[List[str]] = None,
    endpoint: str = "",
    metadata: Optional[dict] = None,
    heartbeat_ttl: int = _DEFAULT_HEARTBEAT_TTL,
) -> dict:
    caps = capabilities or []
    meta = metadata or {}
    now = _now_iso()

    # Read existing to diff capabilities on re-register
    existing = _read_redis(agent_id) or _read_file(agent_id)
    if existing:
        old_caps = set(existing.get("capabilities", []))
        new_caps = set(caps)
        removed = old_caps - new_caps
        if removed:
            r = get_redis()
            if r is not None:
                try:
                    for cap in removed:
                        r.srem(_rkey(f"idx:agents:cap:{cap}"), agent_id)
                except Exception:
                    pass

    registered_at = existing.get("registered_at", now) if existing else now

    data = asdict(AgentRecord(
        agent_id=agent_id,
        agent_name=agent_name or agent_id,
        agent_type=agent_type,
        capabilities=caps,
        endpoint=endpoint,
        status="online",
        metadata=meta,
        registered_at=registered_at,
        last_heartbeat=now,
        heartbeat_ttl=heartbeat_ttl,
    ))

    _write_file(agent_id, data)
    _write_redis(agent_id, data, heartbeat_ttl)

    log("registry: agent registered", {"agent_id": agent_id, "capabilities": len(caps)})
    return {"agent_id": agent_id, "registered": True}


def deregister_agent(agent_id: str) -> dict:
    existing = _read_redis(agent_id) or _read_file(agent_id)
    if not existing:
        return {"agent_id": agent_id, "deleted": False, "reason": "not found"}

    caps = existing.get("capabilities", [])
    agent_type = existing.get("agent_type", "")
    _delete_redis(agent_id, caps, agent_type)
    _delete_file(agent_id)

    log("registry: agent deregistered", {"agent_id": agent_id})
    return {"agent_id": agent_id, "deleted": True}


def heartbeat_agent(
    agent_id: str,
    status: str = "online",
    metadata: Optional[dict] = None,
) -> dict:
    existing = _read_redis(agent_id) or _read_file(agent_id)
    if not existing:
        return {"agent_id": agent_id, "success": False, "reason": "not registered"}

    now = _now_iso()
    existing["last_heartbeat"] = now
    existing["status"] = status
    if metadata:
        existing_meta = existing.get("metadata", {})
        if isinstance(existing_meta, str):
            try:
                existing_meta = json.loads(existing_meta)
            except Exception:
                existing_meta = {}
        existing_meta.update(metadata)
        existing["metadata"] = existing_meta

    ttl = int(existing.get("heartbeat_ttl", _DEFAULT_HEARTBEAT_TTL))
    _write_file(agent_id, existing)
    _write_redis(agent_id, existing, ttl)

    return {"agent_id": agent_id, "last_heartbeat": now, "status": status}


def get_agent(agent_id: str) -> Optional[dict]:
    data = _read_redis(agent_id)
    if data is None:
        data = _read_file(agent_id)
    if data is None:
        return None
    data["effective_status"] = _compute_effective_status(data)
    return data


def list_agents(
    agent_type: str = "",
    capability: str = "",
    status: str = "",
    limit: int = 50,
) -> List[dict]:
    # Try Redis
    r = get_redis()
    if r is not None:
        try:
            # Choose the right index
            if capability:
                agent_ids = list(r.smembers(_rkey(f"idx:agents:cap:{capability}")))
            elif agent_type:
                agent_ids = r.zrevrange(_rkey(f"idx:agents:type:{agent_type}"), 0, -1)
            else:
                agent_ids = r.zrevrange(_rkey("idx:agents"), 0, -1)

            agents: List[dict] = []
            for aid in agent_ids:
                data = _read_redis(aid)
                if data is None:
                    continue
                data["effective_status"] = _compute_effective_status(data)
                # Apply remaining filters
                if agent_type and data.get("agent_type") != agent_type:
                    continue
                if capability and capability not in data.get("capabilities", []):
                    continue
                if status and data["effective_status"] != status:
                    continue
                agents.append(data)
                if len(agents) >= limit:
                    break
            return agents
        except Exception as e:
            log("registry: Redis list_agents failed", {"error": str(e)})

    # File fallback
    return _list_files(agent_type, capability, status, limit)


def find_agents(capability: str) -> List[dict]:
    return list_agents(capability=capability)
