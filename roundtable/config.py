"""Configuration for Roundtable (SPEC.md section 4).

The Owner writes a JSON config file mapping bearer tokens to agents::

    {"agents": [
      {"name": "planner", "role": "leader", "skills": ["planning"],
       "token": "REPLACE-ME"},
      {"name": "coder", "role": "worker", "skills": ["python"],
       "token": "REPLACE-ME-TOO"}
    ]}

Rules enforced here (fail fast at startup, before the server binds):
- exactly one agent has role ``leader`` (SPEC section 4);
- agent names are unique, roles are ``leader`` or ``worker``;
- every token is non-empty, at least 32 characters, and unique across
  agents (AUDIT-M1 R1: a shared token would silently act as the first
  agent listed with it).

Tokens live only in this untracked config file (or are passed the same way
by the Owner). They are never written to the repository, never logged, and
are compared with :func:`hmac.compare_digest`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Agent:
    name: str
    role: str  # "leader" | "worker"
    skills: list[str] = field(default_factory=list)
    token: str = field(default="", repr=False)


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8000
    db_path: str = "roundtable.db"
    agents_config: str = "agents.json"
    rate_limit_per_minute: int = 60
    # Extra Host values the MCP server accepts, from
    # ROUNDTABLE_ALLOWED_HOSTS (comma-separated). None means "use the
    # SDK default": loopback only. Set it when the server sits behind a
    # reverse proxy so the public DNS name is accepted (M3).
    allowed_hosts: list[str] | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            host=os.environ.get("ROUNDTABLE_HOST", "127.0.0.1"),
            port=int(os.environ.get("ROUNDTABLE_PORT", "8000")),
            db_path=os.environ.get("ROUNDTABLE_DB_PATH", "roundtable.db"),
            agents_config=os.environ.get(
                "ROUNDTABLE_AGENTS_CONFIG", "agents.json"
            ),
            rate_limit_per_minute=int(
                os.environ.get("ROUNDTABLE_RATE_LIMIT_PER_MINUTE", "60")
            ),
            allowed_hosts=_parse_allowed_hosts(
                os.environ.get("ROUNDTABLE_ALLOWED_HOSTS")
            ),
        )


def _parse_allowed_hosts(raw: str | None) -> list[str] | None:
    """Parse ROUNDTABLE_ALLOWED_HOSTS (comma-separated hostnames).

    Returns None when unset (SDK default: loopback only). A bare hostname
    is accepted with and without a port, so both ``example.com`` and
    ``example.com:*`` are stored. A bare ``*`` (allow any host) or a
    value that is not a hostname is rejected: the protection must never
    be silently disabled.
    """
    if raw is None:
        return None
    hosts: list[str] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if (
            "/" in token
            or any(ch.isspace() for ch in token)
            or ".." in token
            or token in ("*", ".*")
            or ("*" in token and not token.endswith(":*"))
            or token.startswith((".", "-"))
        ):
            raise ValueError(
                "ROUNDTABLE_ALLOWED_HOSTS has an invalid entry "
                f"{token!r}: use plain hostnames, optionally with ':port' "
                "or ':*'; a bare '*' is never allowed"
            )
        hosts.append(token)
        if ":" not in token:
            # Bare hostname: accept it with and without a port.
            hosts.append(f"{token}:*")
    if not hosts:
        raise ValueError(
            "ROUNDTABLE_ALLOWED_HOSTS is set but lists no hosts; "
            "unset it for loopback-only, or list a hostname"
        )
    return hosts


def load_agents_config(path: str) -> list[Agent]:
    """Load and validate the Owner-written agents config file."""
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError as exc:
        raise ValueError(
            f"agents config not found: {path!r}. Copy agents.example.json "
            "to agents.json and fill in real tokens (never commit it)."
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"agents config {path!r} is not valid JSON: {exc}") from exc

    if not isinstance(raw, dict) or not isinstance(raw.get("agents"), list):
        raise ValueError(
            f"agents config {path!r} must be an object with an 'agents' list"
        )

    agents: list[Agent] = []
    seen_names: set[str] = set()
    seen_tokens: set[str] = set()
    for entry in raw["agents"]:
        if not isinstance(entry, dict):
            raise ValueError("each entry in 'agents' must be an object")
        name = entry.get("name")
        role = entry.get("role")
        token = entry.get("token")
        skills = entry.get("skills") or []
        if not isinstance(name, str) or not name:
            raise ValueError("each agent needs a non-empty string 'name'")
        if name in seen_names:
            raise ValueError(f"duplicate agent name in config: {name!r}")
        seen_names.add(name)
        if role not in ("leader", "worker"):
            raise ValueError(
                f"agent {name!r} has invalid role {role!r}; "
                "must be 'leader' or 'worker'"
            )
        if not isinstance(token, str) or not token:
            raise ValueError(f"agent {name!r} needs a non-empty 'token'")
        if len(token) < 32:
            raise ValueError(
                f"agent {name!r} token is {len(token)} chars; "
                "tokens must be at least 32 characters"
            )
        if token in seen_tokens:
            raise ValueError(
                f"duplicate token in config: the token for agent {name!r} "
                "is already used by another agent"
            )
        seen_tokens.add(token)
        if not isinstance(skills, list) or not all(
            isinstance(s, str) for s in skills
        ):
            raise ValueError(f"agent {name!r} 'skills' must be a list of strings")
        agents.append(Agent(name=name, role=role, skills=list(skills), token=token))

    leaders = [a for a in agents if a.role == "leader"]
    if len(leaders) != 1:
        raise ValueError(
            f"config must have exactly one leader, found {len(leaders)}"
        )
    if not agents:
        raise ValueError("config must list at least one agent")
    return agents
