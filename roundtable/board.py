"""Board business logic (SPEC.md sections 5-7).

Pure SQLite + role/state rules. No MCP, no HTTP, no network in here,
so the rules are testable without a server.

State machine (SPEC section 5)::

    open -> claimed -> submitted -> done
                          |
                          v
                      rejected -> open   (leader review note; assignee cleared)
    claimed -> open                      (assignee releases)
    any non-final state -> cancelled     (leader only)

Final states: ``done``, ``cancelled``.

Interpretations (documented in CHANGELOG.md, not silent scope changes):
- ``create_task`` with an ``assignee`` names a worker; assigning to the
  leader is rejected, because only workers can claim (section 6).
- A task created with an ``assignee`` can only be claimed by that worker;
  an unassigned task can be claimed by any worker.
- On reject, the assignee is cleared so the task can be claimed again.
- ``review_note`` is capped at 4,000 chars like a message (the spec caps
  title/description/message/result but gives no limit for review notes).
- The spec diagram mentions "leader reassigns" as a claimed -> open path,
  but section 6 defines no reassign tool, so M1 does not add one; a leader
  can cancel a claimed task and create a replacement instead. This is an
  open question for the Owner/Auditor (see CHANGELOG.md).
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from .config import Agent
from .db import skills_from_json, skills_to_json

TITLE_MAX = 200
DESCRIPTION_MAX = 8_000
MESSAGE_MAX = 4_000
RESULT_MAX = 20_000
REVIEW_NOTE_MAX = 4_000  # see module docstring (deviation, in CHANGELOG.md)
LIST_LIMIT_DEFAULT = 50
LIST_LIMIT_MAX = 100

STATUSES = ("open", "claimed", "submitted", "done", "cancelled")
MESSAGE_KINDS = ("progress", "help_request", "reply", "note")
FINAL_STATUSES = ("done", "cancelled")


class RoundtableError(Exception):
    """An anticipated, reportable failure (role, state, size, not found)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _check_size(field: str, value: str, limit: int) -> None:
    if len(value) > limit:
        raise RoundtableError(
            f"{field} is {len(value)} chars, over the {limit}-char limit; "
            "rejected, not truncated"
        )


class Board:
    """The task board, backed by one SQLite connection.

    All methods take the lock, so concurrent HTTP requests are serialized
    and a conditional ``UPDATE ... WHERE status='open'`` makes claiming
    atomic: exactly one of two simultaneous claims succeeds.
    """

    def __init__(self, conn, agents: list[Agent]) -> None:
        self._conn = conn
        self._lock = threading.RLock()
        self._agents = {a.name: a for a in agents}
        with self._lock:
            for agent in agents:
                conn.execute(
                    "INSERT INTO agents (name, role, skills, last_seen) "
                    "VALUES (?, ?, ?, NULL) "
                    "ON CONFLICT(name) DO UPDATE SET role=excluded.role, "
                    "skills=excluded.skills",
                    (agent.name, agent.role, skills_to_json(agent.skills)),
                )
            conn.commit()

    # -- helpers ---------------------------------------------------------
    def _get_task_row(self, task_id: int):
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise RoundtableError(f"task not found: {task_id}")
        return row

    @staticmethod
    def _task_dict(row) -> dict:
        return {
            "id": row["id"],
            "title": row["title"],
            "description": row["description"],
            "required_skill": row["required_skill"],
            "status": row["status"],
            "created_by": row["created_by"],
            "assignee": row["assignee"],
            "result": row["result"],
            "review_note": row["review_note"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _messages_for(self, task_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE task_id = ? ORDER BY id ASC",
            (task_id,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "task_id": r["task_id"],
                "author": r["author"],
                "kind": r["kind"],
                "body": r["body"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def _require_leader(self, agent: Agent) -> None:
        if agent.role != "leader":
            raise RoundtableError(
                f"only the leader can do this (caller {agent.name!r} "
                f"is a {agent.role})"
            )

    def _require_worker(self, agent: Agent) -> None:
        if agent.role != "worker":
            raise RoundtableError(
                f"only a worker can do this (caller {agent.name!r} "
                f"is a {agent.role})"
            )

    # -- audit / presence --------------------------------------------------
    def log_audit(
        self, agent_name: str, tool: str, task_id: int | None, outcome: str
    ) -> None:
        """Append one audit row. Append-only: no tool ever updates/deletes it."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit_log (time, agent, tool, task_id, outcome) "
                "VALUES (?, ?, ?, ?, ?)",
                (_now(), agent_name, tool, task_id, outcome),
            )
            self._conn.commit()

    def touch_last_seen(self, agent_name: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE agents SET last_seen = ? WHERE name = ?",
                (_now(), agent_name),
            )
            self._conn.commit()

    # -- tools: identity ----------------------------------------------------
    def whoami(self, agent: Agent) -> dict:
        return {"name": agent.name, "role": agent.role, "skills": agent.skills}

    def list_agents(self, agent: Agent) -> dict:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM agents ORDER BY name ASC"
            ).fetchall()
        return {
            "agents": [
                {
                    "name": r["name"],
                    "role": r["role"],
                    "skills": skills_from_json(r["skills"]),
                    "last_seen": r["last_seen"],
                }
                for r in rows
            ]
        }

    # -- tools: tasks ---------------------------------------------------------
    def create_task(
        self,
        agent: Agent,
        title: str,
        description: str = "",
        required_skill: str | None = None,
        assignee: str | None = None,
    ) -> dict:
        self._require_leader(agent)
        title = (title or "").strip()
        if not title:
            raise RoundtableError("title must not be empty")
        _check_size("title", title, TITLE_MAX)
        _check_size("description", description or "", DESCRIPTION_MAX)
        if assignee is not None:
            target = self._agents.get(assignee)
            if target is None:
                raise RoundtableError(f"unknown assignee: {assignee!r}")
            if target.role != "worker":
                raise RoundtableError(
                    f"assignee {assignee!r} is a {target.role}, not a worker; "
                    "only workers can claim tasks"
                )
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO tasks (title, description, required_skill, "
                "status, created_by, assignee, created_at, updated_at) "
                "VALUES (?, ?, ?, 'open', ?, ?, ?, ?)",
                (
                    title,
                    description or "",
                    (required_skill or None),
                    agent.name,
                    assignee,
                    now,
                    now,
                ),
            )
            task_id = cur.lastrowid
            self._conn.commit()
            return self._task_dict(self._get_task_row(task_id))

    def list_tasks(
        self,
        agent: Agent,
        status: str | None = None,
        assignee: str | None = None,
        limit: int = LIST_LIMIT_DEFAULT,
        offset: int = 0,
    ) -> dict:
        if status is not None and status not in STATUSES:
            raise RoundtableError(f"unknown status: {status!r}")
        if limit < 1 or limit > LIST_LIMIT_MAX:
            raise RoundtableError(
                f"limit must be between 1 and {LIST_LIMIT_MAX}"
            )
        if offset < 0:
            raise RoundtableError("offset must be >= 0")
        query = "SELECT * FROM tasks"
        clauses: list[str] = []
        params: list[object] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if assignee is not None:
            clauses.append("assignee = ?")
            params.append(assignee)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
            total = self._conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        return {
            "tasks": [self._task_dict(r) for r in rows],
            "limit": limit,
            "offset": offset,
            "total": total,
        }

    def get_task(self, agent: Agent, task_id: int) -> dict:
        with self._lock:
            row = self._get_task_row(task_id)
            task = self._task_dict(row)
            task["messages"] = self._messages_for(task_id)
        return task

    def claim_task(self, agent: Agent, task_id: int) -> dict:
        self._require_worker(agent)
        with self._lock:
            row = self._get_task_row(task_id)
            if row["status"] != "open":
                raise RoundtableError(
                    f"task {task_id} is {row['status']!r}, not 'open'"
                )
            if row["assignee"] is not None and row["assignee"] != agent.name:
                raise RoundtableError(
                    f"task {task_id} is assigned to {row['assignee']!r}, "
                    f"not to {agent.name!r}"
                )
            # Atomic: only one claimant can flip open -> claimed.
            cur = self._conn.execute(
                "UPDATE tasks SET status = 'claimed', assignee = ?, "
                "updated_at = ? WHERE id = ? AND status = 'open'",
                (agent.name, _now(), task_id),
            )
            self._conn.commit()
            if cur.rowcount != 1:
                raise RoundtableError(
                    f"task {task_id} could not be claimed (already claimed)"
                )
            return self._task_dict(self._get_task_row(task_id))

    def release_task(self, agent: Agent, task_id: int) -> dict:
        with self._lock:
            row = self._get_task_row(task_id)
            if row["status"] != "claimed":
                raise RoundtableError(
                    f"task {task_id} is {row['status']!r}, not 'claimed'"
                )
            if row["assignee"] != agent.name:
                raise RoundtableError(
                    f"task {task_id} is claimed by {row['assignee']!r}, "
                    f"not by {agent.name!r}"
                )
            self._conn.execute(
                "UPDATE tasks SET status = 'open', assignee = NULL, "
                "updated_at = ? WHERE id = ?",
                (_now(), task_id),
            )
            self._conn.commit()
            return self._task_dict(self._get_task_row(task_id))

    def post_message(
        self, agent: Agent, task_id: int, body: str, kind: str = "note"
    ) -> dict:
        if kind not in MESSAGE_KINDS:
            raise RoundtableError(
                f"unknown message kind: {kind!r} "
                f"(must be one of {', '.join(MESSAGE_KINDS)})"
            )
        _check_size("message", body or "", MESSAGE_MAX)
        if not (body or "").strip():
            raise RoundtableError("message body must not be empty")
        now = _now()
        with self._lock:
            self._get_task_row(task_id)  # raises if missing
            cur = self._conn.execute(
                "INSERT INTO messages (task_id, author, kind, body, "
                "created_at) VALUES (?, ?, ?, ?, ?)",
                (task_id, agent.name, kind, body, now),
            )
            message_id = cur.lastrowid
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM messages WHERE id = ?", (message_id,)
            ).fetchone()
        return {
            "id": row["id"],
            "task_id": row["task_id"],
            "author": row["author"],
            "kind": row["kind"],
            "body": row["body"],
            "created_at": row["created_at"],
        }

    def submit_result(self, agent: Agent, task_id: int, result: str) -> dict:
        _check_size("result", result or "", RESULT_MAX)
        if not (result or "").strip():
            raise RoundtableError("result must not be empty")
        with self._lock:
            row = self._get_task_row(task_id)
            if row["status"] != "claimed":
                raise RoundtableError(
                    f"task {task_id} is {row['status']!r}, not 'claimed'"
                )
            if row["assignee"] != agent.name:
                raise RoundtableError(
                    f"task {task_id} is claimed by {row['assignee']!r}, "
                    f"not by {agent.name!r}"
                )
            self._conn.execute(
                "UPDATE tasks SET status = 'submitted', result = ?, "
                "updated_at = ? WHERE id = ?",
                (result, _now(), task_id),
            )
            self._conn.commit()
            return self._task_dict(self._get_task_row(task_id))

    def review_task(
        self, agent: Agent, task_id: int, approve: bool, review_note: str = ""
    ) -> dict:
        self._require_leader(agent)
        _check_size("review_note", review_note or "", REVIEW_NOTE_MAX)
        if not approve and not (review_note or "").strip():
            raise RoundtableError(
                "a review note is required when rejecting a task"
            )
        with self._lock:
            row = self._get_task_row(task_id)
            if row["status"] != "submitted":
                raise RoundtableError(
                    f"task {task_id} is {row['status']!r}, not 'submitted'"
                )
            if approve:
                self._conn.execute(
                    "UPDATE tasks SET status = 'done', review_note = ?, "
                    "updated_at = ? WHERE id = ?",
                    (review_note or "", _now(), task_id),
                )
            else:
                # Rejected -> open again, assignee cleared (see docstring).
                self._conn.execute(
                    "UPDATE tasks SET status = 'open', assignee = NULL, "
                    "review_note = ?, updated_at = ? WHERE id = ?",
                    (review_note, _now(), task_id),
                )
            self._conn.commit()
            return self._task_dict(self._get_task_row(task_id))

    def cancel_task(self, agent: Agent, task_id: int) -> dict:
        self._require_leader(agent)
        with self._lock:
            row = self._get_task_row(task_id)
            if row["status"] in FINAL_STATUSES:
                raise RoundtableError(
                    f"task {task_id} is already {row['status']!r} "
                    "(final state, cannot cancel)"
                )
            self._conn.execute(
                "UPDATE tasks SET status = 'cancelled', updated_at = ? "
                "WHERE id = ?",
                (_now(), task_id),
            )
            self._conn.commit()
            return self._task_dict(self._get_task_row(task_id))
