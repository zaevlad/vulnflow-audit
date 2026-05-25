"""SQLite persistence for chat threads, messages and envelopes.

Reuses the existing vulnflow.db at the repository root. All schema migration
is idempotent so the chat tables are created lazily on first use.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from dashboard.chat.schemas import ChatMessageRecord, ChatThreadInfo


_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS chat_threads (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        default_tab TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_messages (
        id TEXT PRIMARY KEY,
        thread_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL DEFAULT '',
        parts_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL,
        FOREIGN KEY (thread_id) REFERENCES chat_threads (id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_chat_messages_thread ON chat_messages (thread_id, created_at)",
    """
    CREATE TABLE IF NOT EXISTS canvas_audit_log (
        id TEXT PRIMARY KEY,
        thread_id TEXT,
        message_id TEXT,
        action_kind TEXT NOT NULL,
        action_json TEXT NOT NULL DEFAULT '{}',
        revision_before INTEGER,
        revision_after INTEGER,
        snapshot_before_json TEXT NOT NULL DEFAULT '{}',
        snapshot_after_json TEXT NOT NULL DEFAULT '{}',
        applied_at TEXT NOT NULL,
        source TEXT NOT NULL,
        reason TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_canvas_audit_thread ON canvas_audit_log (thread_id, applied_at)",
    "CREATE INDEX IF NOT EXISTS idx_canvas_audit_recent ON canvas_audit_log (applied_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS chat_streams (
        stream_id TEXT PRIMARY KEY,
        thread_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'running',
        created_at TEXT NOT NULL,
        completed_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_chat_streams_status ON chat_streams (status, created_at)",
    """
    CREATE TABLE IF NOT EXISTS chat_stream_parts (
        stream_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        part_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (stream_id, seq),
        FOREIGN KEY (stream_id) REFERENCES chat_streams (stream_id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_chat_stream_parts_seq ON chat_stream_parts (stream_id, seq)",
    """
    CREATE TABLE IF NOT EXISTS chat_attachments (
        id TEXT PRIMARY KEY,
        thread_id TEXT NOT NULL,
        message_id TEXT,
        kind TEXT NOT NULL,
        mime TEXT NOT NULL DEFAULT 'application/octet-stream',
        filename TEXT NOT NULL DEFAULT '',
        rel_path TEXT NOT NULL,
        bytes INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        FOREIGN KEY (thread_id) REFERENCES chat_threads (id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_chat_attachments_thread ON chat_attachments (thread_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_chat_attachments_message ON chat_attachments (message_id)",
    # FTS5 virtual table for /api/chat/search. `message_id` / `thread_id`
    # / `role` are UNINDEXED — they're filter columns, not searched.
    # `content` is the raw text the user typed (or the assistant's text
    # parts joined); `parts_text` is a flattened bag of plan / widget /
    # tool / canvas-action text extracted server-side. Insert/update
    # syncing is done in Python (we need to extract from parts_json);
    # the DELETE trigger below keeps the index consistent when a thread
    # cascade-deletes its messages.
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS chat_messages_fts USING fts5(
        message_id UNINDEXED,
        thread_id UNINDEXED,
        role UNINDEXED,
        content,
        parts_text,
        tokenize = 'porter unicode61'
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS chat_messages_fts_del
    AFTER DELETE ON chat_messages
    BEGIN
        DELETE FROM chat_messages_fts WHERE message_id = old.id;
    END
    """,
)


def _now_iso() -> str:
    # Microsecond precision — second-level granularity makes ASC ordering
    # of messages persisted inside the same second non-deterministic
    # (tie-breaks degenerate to id-ASC which is not chronological for
    # UUIDs). See audit_log._now_iso for the analogous fix.
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def chat_db_path(workspace_root: str | Path) -> Path:
    return Path(workspace_root).resolve() / "vulnflow.db"


def _ensure_chat_thread_columns(conn: sqlite3.Connection) -> None:
    """Apply ALTER TABLE migrations for chat_threads on existing dbs.

    SQLite's ``CREATE TABLE IF NOT EXISTS`` is a no-op when the table
    already exists, so newly-added columns must be ALTERed in.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(chat_threads)").fetchall()}
    if "default_tab" not in cols:
        conn.execute("ALTER TABLE chat_threads ADD COLUMN default_tab TEXT")


# ---------------------------------------------------------------------------
# FTS5 sync helpers
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(value: str) -> str:
    return _TAG_RE.sub(" ", value or "")


def _extract_parts_text(parts: list[Any]) -> str:
    """Flatten envelope parts into a single searchable text blob.

    We include text/plan/widget/tool_result/canvas_action surfaces —
    anything the user might reasonably grep for after the fact. Output
    is line-joined so the FTS5 snippet function picks the best line.
    """
    pieces: list[str] = []
    for raw in parts or []:
        if not isinstance(raw, dict):
            continue
        kind = raw.get("type")
        if kind == "text":
            text = raw.get("text")
            if text:
                pieces.append(str(text))
        elif kind == "plan":
            for key in ("approach", "technology"):
                v = raw.get(key)
                if v:
                    pieces.append(str(v))
            for elem in raw.get("key_elements") or []:
                if elem:
                    pieces.append(str(elem))
        elif kind == "widget":
            for key in ("title", "description"):
                v = raw.get(key)
                if v:
                    pieces.append(str(v))
            html = raw.get("html")
            if html:
                pieces.append(_strip_html(str(html)))
        elif kind == "tool_result":
            output = raw.get("output")
            if isinstance(output, dict):
                for key in ("content", "text", "message", "summary", "snippet"):
                    v = output.get(key)
                    if v:
                        pieces.append(str(v))
                try:
                    # Fallback: stringify the full output (capped to keep
                    # the FTS row small for noisy tools).
                    dumped = json.dumps(output, ensure_ascii=False)
                    pieces.append(dumped[:2000])
                except Exception:
                    pass
            elif isinstance(output, str) and output:
                pieces.append(output)
        elif kind == "canvas_action":
            action = raw.get("action") or {}
            for key in ("kind", "node_id", "edge_id"):
                v = action.get(key)
                if v:
                    pieces.append(str(v))
            reason = raw.get("reason")
            if reason:
                pieces.append(str(reason))
        elif kind == "error":
            for key in ("scope", "message"):
                v = raw.get(key)
                if v:
                    pieces.append(str(v))
    return "\n".join(p for p in pieces if p)


def _index_message_in_fts(
    conn: sqlite3.Connection,
    *,
    message_id: str,
    thread_id: str,
    role: str,
    content: str,
    parts: Optional[list[dict[str, Any]]],
) -> None:
    parts_text = _extract_parts_text(parts or [])
    conn.execute(
        "DELETE FROM chat_messages_fts WHERE message_id = ?",
        (message_id,),
    )
    conn.execute(
        """
        INSERT INTO chat_messages_fts (message_id, thread_id, role, content, parts_text)
        VALUES (?, ?, ?, ?, ?)
        """,
        (message_id, thread_id, role, content or "", parts_text),
    )


def _ensure_fts_populated(conn: sqlite3.Connection) -> None:
    """One-time backfill: index any rows that predate the FTS table."""
    try:
        fts_count = conn.execute("SELECT COUNT(*) FROM chat_messages_fts").fetchone()[0]
        msg_count = conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0]
    except sqlite3.OperationalError:
        return
    if fts_count >= msg_count or msg_count == 0:
        return
    rows = conn.execute(
        "SELECT id, thread_id, role, content, parts_json FROM chat_messages"
    ).fetchall()
    for row in rows:
        try:
            parts = json.loads(row["parts_json"] or "[]")
            if not isinstance(parts, list):
                parts = []
        except Exception:
            parts = []
        _index_message_in_fts(
            conn,
            message_id=str(row["id"]),
            thread_id=str(row["thread_id"]),
            role=str(row["role"]),
            content=str(row["content"] or ""),
            parts=parts,
        )


@contextmanager
def open_chat_db(workspace_root: str | Path) -> Iterator[sqlite3.Connection]:
    db_path = chat_db_path(workspace_root)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        for statement in _SCHEMA_STATEMENTS:
            conn.execute(statement)
        _ensure_chat_thread_columns(conn)
        _ensure_fts_populated(conn)
        conn.commit()
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Thread CRUD
# ---------------------------------------------------------------------------


_VALID_DEFAULT_TABS = {"pipeline", "audit", "editor"}


def _normalize_default_tab(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip().lower()
    return cleaned if cleaned in _VALID_DEFAULT_TABS else None


def list_threads(workspace_root: str | Path) -> list[ChatThreadInfo]:
    with open_chat_db(workspace_root) as conn:
        rows = conn.execute(
            """
            SELECT t.id, t.title, t.created_at, t.updated_at, t.default_tab,
                   COALESCE((SELECT COUNT(*) FROM chat_messages m WHERE m.thread_id = t.id), 0) AS message_count
            FROM chat_threads t
            ORDER BY t.updated_at DESC
            """
        ).fetchall()
    return [
        ChatThreadInfo(
            id=str(row["id"]),
            title=str(row["title"] or "New chat"),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            message_count=int(row["message_count"]),
            default_tab=row["default_tab"] if row["default_tab"] else None,
        )
        for row in rows
    ]


def create_thread(
    workspace_root: str | Path,
    title: Optional[str] = None,
    *,
    default_tab: Optional[str] = None,
) -> ChatThreadInfo:
    thread_id = uuid.uuid4().hex
    now = _now_iso()
    final_title = (title or "New chat").strip() or "New chat"
    normalized_tab = _normalize_default_tab(default_tab)
    with open_chat_db(workspace_root) as conn:
        conn.execute(
            "INSERT INTO chat_threads (id, title, created_at, updated_at, default_tab) VALUES (?, ?, ?, ?, ?)",
            (thread_id, final_title, now, now, normalized_tab),
        )
        conn.commit()
    return ChatThreadInfo(
        id=thread_id,
        title=final_title,
        created_at=now,
        updated_at=now,
        message_count=0,
        default_tab=normalized_tab,
    )


def rename_thread(
    workspace_root: str | Path,
    thread_id: str,
    title: str,
) -> ChatThreadInfo:
    cleaned = title.strip() or "New chat"
    now = _now_iso()
    with open_chat_db(workspace_root) as conn:
        result = conn.execute(
            "UPDATE chat_threads SET title = ?, updated_at = ? WHERE id = ?",
            (cleaned, now, thread_id),
        )
        if result.rowcount == 0:
            raise KeyError(thread_id)
        conn.commit()
        row = conn.execute(
            """
            SELECT t.id, t.title, t.created_at, t.updated_at, t.default_tab,
                   COALESCE((SELECT COUNT(*) FROM chat_messages m WHERE m.thread_id = t.id), 0) AS message_count
            FROM chat_threads t WHERE t.id = ?
            """,
            (thread_id,),
        ).fetchone()
    return ChatThreadInfo(
        id=str(row["id"]),
        title=str(row["title"] or "New chat"),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        message_count=int(row["message_count"]),
        default_tab=row["default_tab"] if row["default_tab"] else None,
    )


def set_thread_default_tab(
    workspace_root: str | Path,
    thread_id: str,
    default_tab: Optional[str],
) -> ChatThreadInfo:
    normalized = _normalize_default_tab(default_tab)
    with open_chat_db(workspace_root) as conn:
        result = conn.execute(
            "UPDATE chat_threads SET default_tab = ? WHERE id = ?",
            (normalized, thread_id),
        )
        if result.rowcount == 0:
            raise KeyError(thread_id)
        conn.commit()
        row = conn.execute(
            """
            SELECT t.id, t.title, t.created_at, t.updated_at, t.default_tab,
                   COALESCE((SELECT COUNT(*) FROM chat_messages m WHERE m.thread_id = t.id), 0) AS message_count
            FROM chat_threads t WHERE t.id = ?
            """,
            (thread_id,),
        ).fetchone()
    return ChatThreadInfo(
        id=str(row["id"]),
        title=str(row["title"] or "New chat"),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        message_count=int(row["message_count"]),
        default_tab=row["default_tab"] if row["default_tab"] else None,
    )


def delete_thread(workspace_root: str | Path, thread_id: str) -> bool:
    with open_chat_db(workspace_root) as conn:
        result = conn.execute("DELETE FROM chat_threads WHERE id = ?", (thread_id,))
        conn.commit()
        deleted = result.rowcount > 0
    if deleted:
        # SQL CASCADE already dropped chat_attachments rows; sweep the
        # per-thread folder on disk so we don't leak bytes.
        try:
            from dashboard.chat.attachments import cleanup_thread_files
            cleanup_thread_files(workspace_root, thread_id)
        except Exception:
            pass
    return deleted


def touch_thread(workspace_root: str | Path, thread_id: str) -> None:
    with open_chat_db(workspace_root) as conn:
        conn.execute(
            "UPDATE chat_threads SET updated_at = ? WHERE id = ?",
            (_now_iso(), thread_id),
        )
        conn.commit()


def thread_exists(workspace_root: str | Path, thread_id: str) -> bool:
    with open_chat_db(workspace_root) as conn:
        row = conn.execute(
            "SELECT 1 FROM chat_threads WHERE id = ?",
            (thread_id,),
        ).fetchone()
    return bool(row)


# ---------------------------------------------------------------------------
# Message CRUD
# ---------------------------------------------------------------------------


def list_messages(
    workspace_root: str | Path,
    thread_id: str,
) -> list[ChatMessageRecord]:
    with open_chat_db(workspace_root) as conn:
        rows = conn.execute(
            """
            SELECT id, thread_id, role, content, parts_json, created_at
            FROM chat_messages WHERE thread_id = ? ORDER BY created_at ASC, id ASC
            """,
            (thread_id,),
        ).fetchall()
    out: list[ChatMessageRecord] = []
    for row in rows:
        try:
            parts = json.loads(row["parts_json"] or "[]")
            if not isinstance(parts, list):
                parts = []
        except Exception:
            parts = []
        out.append(
            ChatMessageRecord(
                id=str(row["id"]),
                thread_id=str(row["thread_id"]),
                role=str(row["role"]),  # type: ignore[arg-type]
                content=str(row["content"] or ""),
                parts=parts,
                created_at=str(row["created_at"]),
            )
        )
    return out


def append_message(
    workspace_root: str | Path,
    thread_id: str,
    *,
    role: str,
    content: str,
    parts: Optional[list[dict[str, Any]]] = None,
) -> ChatMessageRecord:
    message_id = uuid.uuid4().hex
    now = _now_iso()
    parts_payload = json.dumps(parts or [], ensure_ascii=False)
    with open_chat_db(workspace_root) as conn:
        conn.execute(
            """
            INSERT INTO chat_messages (id, thread_id, role, content, parts_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (message_id, thread_id, role, content, parts_payload, now),
        )
        conn.execute(
            "UPDATE chat_threads SET updated_at = ? WHERE id = ?",
            (now, thread_id),
        )
        _index_message_in_fts(
            conn,
            message_id=message_id,
            thread_id=thread_id,
            role=role,
            content=content,
            parts=parts,
        )
        conn.commit()
    return ChatMessageRecord(
        id=message_id,
        thread_id=thread_id,
        role=role,  # type: ignore[arg-type]
        content=content,
        parts=parts or [],
        created_at=now,
    )


def maybe_autoname_thread(
    workspace_root: str | Path,
    thread_id: str,
    suggested: str,
) -> None:
    """If the thread still has the default name, replace it with a snippet of the first message."""
    cleaned = " ".join(suggested.split())[:80].strip()
    if not cleaned:
        return
    with open_chat_db(workspace_root) as conn:
        row = conn.execute(
            "SELECT title FROM chat_threads WHERE id = ?",
            (thread_id,),
        ).fetchone()
        if not row:
            return
        current_title = str(row["title"] or "").strip().lower()
        if current_title and current_title != "new chat":
            return
        conn.execute(
            "UPDATE chat_threads SET title = ? WHERE id = ?",
            (cleaned, thread_id),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

_FTS_TERM_RE = re.compile(r"[\w./-]+", re.UNICODE)


def _build_fts_match_query(raw: str) -> str:
    """Translate a freeform user query into a safe FTS5 MATCH expression.

    We tokenize on word characters / path-like punctuation, double-quote
    each token so FTS5 operators (``OR``, ``AND``, ``NEAR``, ``-``, ...)
    in user input are treated as literals, and AND them together
    (implicit in FTS5). The final token gets a trailing wildcard so
    in-progress typing matches by prefix.
    """
    cleaned = (raw or "").strip()
    if not cleaned:
        return ""
    terms = [m.group(0) for m in _FTS_TERM_RE.finditer(cleaned)]
    if not terms:
        return ""
    out: list[str] = []
    for idx, term in enumerate(terms):
        safe = term.replace('"', "").strip()
        if not safe:
            continue
        if idx == len(terms) - 1 and len(safe) >= 2:
            out.append(f'"{safe}"*')
        else:
            out.append(f'"{safe}"')
    return " ".join(out)


# Sentinel markers we feed to SQLite's snippet() so we can safely escape
# the surrounding user-controlled text on the way out without confusing
# our highlight markers with literal `<mark>` characters typed by the
# user. Picked from Unicode "invisible separator" so collision is
# essentially impossible.
_FTS_MARK_OPEN_SENTINEL = "⁣MARKOPEN⁣"
_FTS_MARK_CLOSE_SENTINEL = "⁣MARKCLOSE⁣"


def _escape_snippet_html(raw: str) -> str:
    if not raw:
        return ""
    escaped = (
        raw.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return (
        escaped
        .replace(_FTS_MARK_OPEN_SENTINEL, "<mark>")
        .replace(_FTS_MARK_CLOSE_SENTINEL, "</mark>")
    )


def search_messages(
    workspace_root: str | Path,
    query: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Run a full-text search against persisted chat messages.

    Results are sorted by BM25 (smaller = better). Snippets returned
    in the response already escape any HTML that was in the source
    text, with ``<mark>...</mark>`` wrapping every hit token — safe to
    render via ``dangerouslySetInnerHTML`` on the client.
    """
    capped = max(1, min(int(limit), 200))
    match_expr = _build_fts_match_query(query)
    if not match_expr:
        return []
    rows: list[sqlite3.Row]
    try:
        with open_chat_db(workspace_root) as conn:
            rows = conn.execute(
                """
                SELECT
                    f.message_id           AS message_id,
                    f.thread_id            AS thread_id,
                    f.role                 AS role,
                    m.created_at           AS created_at,
                    t.title                AS thread_title,
                    snippet(chat_messages_fts, 3, ?, ?, '…', 16) AS content_snippet,
                    snippet(chat_messages_fts, 4, ?, ?, '…', 16) AS parts_snippet,
                    bm25(chat_messages_fts) AS score
                FROM chat_messages_fts f
                JOIN chat_messages m ON m.id = f.message_id
                JOIN chat_threads  t ON t.id = f.thread_id
                WHERE chat_messages_fts MATCH ?
                ORDER BY score ASC
                LIMIT ?
                """,
                (
                    _FTS_MARK_OPEN_SENTINEL,
                    _FTS_MARK_CLOSE_SENTINEL,
                    _FTS_MARK_OPEN_SENTINEL,
                    _FTS_MARK_CLOSE_SENTINEL,
                    match_expr,
                    capped,
                ),
            ).fetchall()
    except sqlite3.OperationalError:
        # Malformed FTS expression (shouldn't happen with our quoting,
        # but be defensive against pathological inputs).
        return []
    return [
        {
            "message_id": str(r["message_id"]),
            "thread_id": str(r["thread_id"]),
            "role": str(r["role"]),
            "created_at": str(r["created_at"]),
            "thread_title": str(r["thread_title"] or "Untitled"),
            "content_snippet": _escape_snippet_html(str(r["content_snippet"] or "")),
            "parts_snippet": _escape_snippet_html(str(r["parts_snippet"] or "")),
            "score": float(r["score"]) if r["score"] is not None else 0.0,
        }
        for r in rows
    ]
