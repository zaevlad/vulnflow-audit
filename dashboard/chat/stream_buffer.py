"""Durable buffer for in-flight chat SSE streams.

The chat send endpoint streams envelope parts to the client over SSE.
If the client disconnects (mobile network, tab crash, refresh) mid-turn
they should be able to issue a follow-up ``GET /api/chat/streams/{id}``
and resume from the last sequence number they saw.

This module persists each envelope part synchronously to ``vulnflow.db``
before it is pushed into the SSE queue, so a process restart never
loses an already-emitted part. A small GC sweep marks dangling
``running`` streams as ``failed`` if their producer never updated the
status within the deadline.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from dashboard.chat.storage import open_chat_db


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def create_stream(workspace_root: str | Path, thread_id: str) -> str:
    stream_id = uuid.uuid4().hex
    now = _now_iso()
    with open_chat_db(workspace_root) as conn:
        conn.execute(
            "INSERT INTO chat_streams (stream_id, thread_id, status, created_at) VALUES (?, ?, 'running', ?)",
            (stream_id, thread_id, now),
        )
        conn.commit()
    return stream_id


def append_part(
    workspace_root: str | Path,
    stream_id: str,
    seq: int,
    part: dict[str, Any],
) -> None:
    payload = json.dumps(part, ensure_ascii=False)
    now = _now_iso()
    with open_chat_db(workspace_root) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO chat_stream_parts (stream_id, seq, part_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (stream_id, seq, payload, now),
        )
        conn.commit()


def set_status(
    workspace_root: str | Path,
    stream_id: str,
    status: str,
) -> None:
    if status not in {"running", "completed", "failed"}:
        raise ValueError(f"Invalid stream status: {status!r}")
    completed_at = _now_iso() if status in {"completed", "failed"} else None
    with open_chat_db(workspace_root) as conn:
        conn.execute(
            "UPDATE chat_streams SET status = ?, completed_at = ? WHERE stream_id = ?",
            (status, completed_at, stream_id),
        )
        conn.commit()


def get_stream(workspace_root: str | Path, stream_id: str) -> Optional[dict[str, Any]]:
    with open_chat_db(workspace_root) as conn:
        row = conn.execute(
            "SELECT stream_id, thread_id, status, created_at, completed_at FROM chat_streams WHERE stream_id = ?",
            (stream_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "stream_id": str(row["stream_id"]),
        "thread_id": str(row["thread_id"]),
        "status": str(row["status"]),
        "created_at": str(row["created_at"]),
        "completed_at": row["completed_at"],
    }


def list_parts(
    workspace_root: str | Path,
    stream_id: str,
    from_seq: int = 0,
) -> list[tuple[int, dict[str, Any]]]:
    out: list[tuple[int, dict[str, Any]]] = []
    with open_chat_db(workspace_root) as conn:
        rows = conn.execute(
            """
            SELECT seq, part_json FROM chat_stream_parts
            WHERE stream_id = ? AND seq > ?
            ORDER BY seq ASC
            """,
            (stream_id, int(from_seq)),
        ).fetchall()
    for row in rows:
        try:
            part = json.loads(row["part_json"])
        except Exception:
            part = {"type": "error", "scope": "stream", "message": "corrupt part"}
        if not isinstance(part, dict):
            part = {"type": "error", "scope": "stream", "message": "non-dict part"}
        out.append((int(row["seq"]), part))
    return out


def max_seq(workspace_root: str | Path, stream_id: str) -> int:
    with open_chat_db(workspace_root) as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS m FROM chat_stream_parts WHERE stream_id = ?",
            (stream_id,),
        ).fetchone()
    return int(row["m"] if row else 0)


def gc_running_streams(
    workspace_root: str | Path,
    timeout_seconds: int = 600,
) -> int:
    """Mark any stream still in ``running`` past the deadline as failed.

    Intended to be called once at startup, when a previous server
    process may have crashed mid-stream. Returns the number of streams
    transitioned to ``failed``.
    """

    cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=max(1, timeout_seconds))
    ).isoformat(timespec="seconds")
    now = _now_iso()
    with open_chat_db(workspace_root) as conn:
        cur = conn.execute(
            """
            UPDATE chat_streams
            SET status = 'failed', completed_at = ?
            WHERE status = 'running' AND created_at < ?
            """,
            (now, cutoff),
        )
        conn.commit()
        return int(cur.rowcount or 0)
