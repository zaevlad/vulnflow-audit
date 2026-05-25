"""Canvas audit log — immutable history of every canvas mutation.

Stores one row per applied action with enough context to:
- review what happened, when, and from which source (agent vs user);
- reverse it (Sprint 3 / undo) without re-traversing the full canvas.

Two sources are recognized:

* ``agent`` — actions emitted via ``emit_canvas_action`` by the chat
  agent. Each call to :func:`record_agent_action` writes one row whose
  snapshot fields capture the *single affected entity* (node or edge).
* ``user`` — debounced batches pushed from the frontend after manual
  edits. :func:`record_manual_batch` records the full canvas snapshot
  before / after the batch so the batch can be reversed atomically.

The table itself is declared in :mod:`dashboard.chat.storage` so all
schema migrations live in one place.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from dashboard.chat.storage import open_chat_db


def _now_iso() -> str:
    # Microsecond precision: with second-level granularity, multiple
    # rows recorded inside the same second tie on applied_at and the
    # DESC ordering used by `list_recent` / `undo batch_to` degenerates
    # into id-DESC, which is not chronological.
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _dump(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return "{}"


def _load(value: str) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def record_agent_action(
    workspace_root: str | Path,
    *,
    thread_id: Optional[str],
    message_id: Optional[str],
    action_kind: str,
    action: dict[str, Any],
    revision_before: int,
    revision_after: int,
    snapshot_before: Any,
    snapshot_after: Any,
    reason: Optional[str] = None,
) -> str:
    """Persist a single agent-emitted canvas mutation. Returns the row id."""
    row_id = uuid.uuid4().hex
    with open_chat_db(workspace_root) as conn:
        conn.execute(
            """
            INSERT INTO canvas_audit_log
                (id, thread_id, message_id, action_kind, action_json,
                 revision_before, revision_after,
                 snapshot_before_json, snapshot_after_json,
                 applied_at, source, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row_id,
                thread_id,
                message_id,
                action_kind,
                _dump(action),
                int(revision_before),
                int(revision_after),
                _dump(snapshot_before),
                _dump(snapshot_after),
                _now_iso(),
                "agent",
                reason,
            ),
        )
        conn.commit()
    return row_id


def record_manual_batch(
    workspace_root: str | Path,
    *,
    revision_before: int,
    revision_after: int,
    snapshot_before: dict[str, Any],
    snapshot_after: dict[str, Any],
    reason: Optional[str] = None,
) -> str:
    """Persist a debounced batch of manual edits made directly on the canvas."""
    row_id = uuid.uuid4().hex
    with open_chat_db(workspace_root) as conn:
        conn.execute(
            """
            INSERT INTO canvas_audit_log
                (id, thread_id, message_id, action_kind, action_json,
                 revision_before, revision_after,
                 snapshot_before_json, snapshot_after_json,
                 applied_at, source, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row_id,
                None,
                None,
                "manual_batch",
                "{}",
                int(revision_before),
                int(revision_after),
                _dump(snapshot_before),
                _dump(snapshot_after),
                _now_iso(),
                "user",
                reason,
            ),
        )
        conn.commit()
    return row_id


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "thread_id": row["thread_id"] if row["thread_id"] is not None else None,
        "message_id": row["message_id"] if row["message_id"] is not None else None,
        "action_kind": str(row["action_kind"]),
        "action": _load(row["action_json"]) or {},
        "revision_before": int(row["revision_before"]) if row["revision_before"] is not None else None,
        "revision_after": int(row["revision_after"]) if row["revision_after"] is not None else None,
        "snapshot_before": _load(row["snapshot_before_json"]),
        "snapshot_after": _load(row["snapshot_after_json"]),
        "applied_at": str(row["applied_at"]),
        "source": str(row["source"]),
        "reason": row["reason"],
    }


def list_recent(
    workspace_root: str | Path,
    *,
    limit: int = 100,
    source: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    capped = max(1, min(int(limit), 500))
    query = "SELECT * FROM canvas_audit_log WHERE 1=1"
    params: list[Any] = []
    if source:
        query += " AND source = ?"
        params.append(source)
    if thread_id:
        query += " AND thread_id = ?"
        params.append(thread_id)
    query += " ORDER BY applied_at DESC, id DESC LIMIT ?"
    params.append(capped)
    with open_chat_db(workspace_root) as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_for_thread(
    workspace_root: str | Path,
    thread_id: str,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    return list_recent(workspace_root, limit=limit, thread_id=thread_id)


def get_record(workspace_root: str | Path, record_id: str) -> Optional[dict[str, Any]]:
    with open_chat_db(workspace_root) as conn:
        row = conn.execute(
            "SELECT * FROM canvas_audit_log WHERE id = ?",
            (record_id,),
        ).fetchone()
    return _row_to_dict(row) if row else None


# ---------------------------------------------------------------------------
# Snapshot helpers
# ---------------------------------------------------------------------------


def snapshot_entity_for_action(
    *,
    kind: str,
    ui_context_nodes: list[dict[str, Any]],
    ui_context_edges: list[dict[str, Any]],
    action: dict[str, Any],
) -> dict[str, Any]:
    """Capture the affected entity *before* an action is applied.

    Used by :mod:`dashboard.chat.service` so the audit row can later be
    reversed (e.g. ``delete_node`` → recreate from snapshot). For action
    kinds that operate on a new entity (``create_*`` / ``set_viewport``)
    this returns ``{}``; the *after* state is captured separately.
    """
    if kind == "delete_node" or kind == "update_node":
        node_id = action.get("node_id")
        for node in ui_context_nodes:
            if node.get("id") == node_id:
                return {"node": node}
        return {}
    if kind == "delete_edge" or kind == "update_edge":
        edge_id = action.get("edge_id")
        for edge in ui_context_edges:
            if edge.get("id") == edge_id:
                return {"edge": edge}
        return {}
    return {}


def snapshot_entity_after_action(
    *,
    kind: str,
    action: dict[str, Any],
) -> dict[str, Any]:
    """Capture the entity introduced or modified *by* an action.

    For ``create_node`` / ``update_node`` returns the (post) node data,
    for the edge variants — the edge, for ``set_viewport`` — the
    viewport. ``delete_*`` actions have no after-snapshot.
    """
    if kind == "create_node" or kind == "update_node":
        node = {
            "id": action.get("node_id"),
            "type": action.get("node_type"),
            "position": action.get("position"),
            "data": action.get("data") or {},
        }
        if action.get("parentId"):
            node["parentId"] = action["parentId"]
        return {"node": node}
    if kind == "create_edge" or kind == "update_edge":
        edge = {
            "id": action.get("edge_id"),
            "source": action.get("source"),
            "target": action.get("target"),
            "data": action.get("data") or {},
        }
        if action.get("sourceHandle"):
            edge["sourceHandle"] = action["sourceHandle"]
        if action.get("targetHandle"):
            edge["targetHandle"] = action["targetHandle"]
        return {"edge": edge}
    if kind == "set_viewport":
        return {"viewport": action.get("viewport") or {}}
    return {}
