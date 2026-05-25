"""Chat attachments — image / PDF uploads attached to user messages.

Stores files on disk under ``<workspace>/chat_attachments/<thread_id>/<uuid>.<ext>``
so they don't bloat the SQLite database. The metadata row in
:func:`dashboard.chat.storage` carries pointers, sizes and mime types.

Lifecycle:
    1. Frontend POST /api/chat/attachments uploads a file. We validate
       mime / size, write the bytes to disk and insert a metadata row
       with ``message_id = NULL`` (the message that owns it doesn't
       exist yet).
    2. On ``POST /api/chat/send`` the request carries ``attachment_ids``;
       :class:`ChatService` builds a multimodal user message including
       those files, then persists the message and back-fills
       ``chat_attachments.message_id`` so future loads can group them.
    3. When a thread is deleted the SQL ``ON DELETE CASCADE`` removes
       the rows; :func:`cleanup_thread_files` removes the disk folder.

All paths are clamped to ``workspace/chat_attachments`` — callers cannot
escape via ``..``.
"""

from __future__ import annotations

import base64
import mimetypes
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from dashboard.chat.storage import open_chat_db


# Allowed mime types — anything not in this set is rejected at upload.
_ALLOWED_IMAGE_MIMES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/gif",
    "image/webp",
}
_ALLOWED_PDF_MIMES = {"application/pdf"}
_ALLOWED_MIMES = _ALLOWED_IMAGE_MIMES | _ALLOWED_PDF_MIMES

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MB

_ATTACHMENTS_DIRNAME = "chat_attachments"


@dataclass
class AttachmentRecord:
    id: str
    thread_id: str
    message_id: Optional[str]
    kind: str  # "image" | "pdf"
    mime: str
    filename: str
    rel_path: str
    bytes_: int
    created_at: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "message_id": self.message_id,
            "kind": self.kind,
            "mime": self.mime,
            "filename": self.filename,
            "rel_path": self.rel_path,
            "bytes": self.bytes_,
            "created_at": self.created_at,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _attachments_root(workspace_root: str | Path) -> Path:
    return Path(workspace_root).resolve() / _ATTACHMENTS_DIRNAME


def _resolve_safe_path(workspace_root: str | Path, rel_path: str) -> Path:
    """Resolve ``rel_path`` (workspace-relative) and enforce containment."""
    root = _attachments_root(workspace_root)
    candidate = (Path(workspace_root).resolve() / rel_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path '{rel_path}' is outside chat_attachments/") from exc
    return candidate


def _row_to_record(row: sqlite3.Row) -> AttachmentRecord:
    return AttachmentRecord(
        id=str(row["id"]),
        thread_id=str(row["thread_id"]),
        message_id=row["message_id"] if row["message_id"] else None,
        kind=str(row["kind"]),
        mime=str(row["mime"]),
        filename=str(row["filename"] or ""),
        rel_path=str(row["rel_path"]),
        bytes_=int(row["bytes"] or 0),
        created_at=str(row["created_at"]),
    )


def _normalize_mime(mime: str, filename: str) -> str:
    cleaned = (mime or "").strip().lower()
    if cleaned and cleaned != "application/octet-stream":
        return cleaned
    guessed, _ = mimetypes.guess_type(filename or "")
    return (guessed or "application/octet-stream").lower()


def _classify_kind(mime: str) -> Optional[str]:
    if mime in _ALLOWED_IMAGE_MIMES:
        return "image"
    if mime in _ALLOWED_PDF_MIMES:
        return "pdf"
    return None


def _safe_extension(filename: str, mime: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix and len(suffix) <= 6 and suffix.isascii():
        return suffix
    # mime → extension fallback
    guessed = mimetypes.guess_extension(mime or "") or ""
    if guessed == ".jpe":
        return ".jpg"
    if guessed:
        return guessed
    return ".bin"


def save_attachment(
    workspace_root: str | Path,
    *,
    thread_id: str,
    payload: bytes,
    filename: str,
    mime: str,
) -> AttachmentRecord:
    """Persist a single uploaded attachment.

    Raises ``ValueError`` on validation failure (size, mime, missing
    thread). The on-disk file is written before the DB row so a crash
    midway never leaves a dangling metadata pointer.
    """
    if not thread_id:
        raise ValueError("thread_id is required.")
    size = len(payload or b"")
    if size == 0:
        raise ValueError("attachment is empty.")
    if size > MAX_ATTACHMENT_BYTES:
        raise ValueError(
            f"attachment too large ({size} bytes > {MAX_ATTACHMENT_BYTES})."
        )
    effective_mime = _normalize_mime(mime, filename)
    kind = _classify_kind(effective_mime)
    if kind is None:
        raise ValueError(f"unsupported mime: {effective_mime!r}")

    ext = _safe_extension(filename, effective_mime)
    attachment_id = uuid.uuid4().hex
    rel_path = f"{_ATTACHMENTS_DIRNAME}/{thread_id}/{attachment_id}{ext}"
    abs_path = _resolve_safe_path(workspace_root, rel_path)
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(payload)

    now = _now_iso()
    try:
        with open_chat_db(workspace_root) as conn:
            conn.execute(
                """
                INSERT INTO chat_attachments
                    (id, thread_id, message_id, kind, mime, filename, rel_path, bytes, created_at)
                VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?)
                """,
                (attachment_id, thread_id, kind, effective_mime, filename or "", rel_path, size, now),
            )
            conn.commit()
    except Exception:
        # Roll back the on-disk file if the DB write failed so we don't
        # leak orphaned bytes.
        try:
            abs_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise

    return AttachmentRecord(
        id=attachment_id,
        thread_id=thread_id,
        message_id=None,
        kind=kind,
        mime=effective_mime,
        filename=filename or "",
        rel_path=rel_path,
        bytes_=size,
        created_at=now,
    )


def get(workspace_root: str | Path, attachment_id: str) -> Optional[AttachmentRecord]:
    with open_chat_db(workspace_root) as conn:
        row = conn.execute(
            "SELECT * FROM chat_attachments WHERE id = ?",
            (attachment_id,),
        ).fetchone()
    return _row_to_record(row) if row else None


def get_many(
    workspace_root: str | Path,
    attachment_ids: Iterable[str],
) -> list[AttachmentRecord]:
    ids = [str(x) for x in attachment_ids if x]
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    with open_chat_db(workspace_root) as conn:
        rows = conn.execute(
            f"SELECT * FROM chat_attachments WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
    by_id = {str(r["id"]): _row_to_record(r) for r in rows}
    return [by_id[i] for i in ids if i in by_id]


def list_for_thread(
    workspace_root: str | Path,
    thread_id: str,
) -> list[AttachmentRecord]:
    with open_chat_db(workspace_root) as conn:
        rows = conn.execute(
            "SELECT * FROM chat_attachments WHERE thread_id = ? ORDER BY created_at ASC",
            (thread_id,),
        ).fetchall()
    return [_row_to_record(r) for r in rows]


def list_for_message(
    workspace_root: str | Path,
    message_id: str,
) -> list[AttachmentRecord]:
    with open_chat_db(workspace_root) as conn:
        rows = conn.execute(
            "SELECT * FROM chat_attachments WHERE message_id = ? ORDER BY created_at ASC",
            (message_id,),
        ).fetchall()
    return [_row_to_record(r) for r in rows]


def attach_to_message(
    workspace_root: str | Path,
    attachment_ids: Iterable[str],
    message_id: str,
) -> None:
    ids = [str(x) for x in attachment_ids if x]
    if not ids:
        return
    placeholders = ",".join("?" * len(ids))
    with open_chat_db(workspace_root) as conn:
        conn.execute(
            f"UPDATE chat_attachments SET message_id = ? WHERE id IN ({placeholders})",
            [message_id, *ids],
        )
        conn.commit()


def delete(workspace_root: str | Path, attachment_id: str) -> bool:
    record = get(workspace_root, attachment_id)
    if not record:
        return False
    try:
        abs_path = _resolve_safe_path(workspace_root, record.rel_path)
        abs_path.unlink(missing_ok=True)
    except Exception:
        # File missing or already gone — still proceed to drop the row.
        pass
    with open_chat_db(workspace_root) as conn:
        conn.execute("DELETE FROM chat_attachments WHERE id = ?", (attachment_id,))
        conn.commit()
    return True


def cleanup_thread_files(workspace_root: str | Path, thread_id: str) -> None:
    """Remove the per-thread folder when a thread is deleted.

    SQLite ON DELETE CASCADE already removes the rows; this function is
    responsible only for the bytes on disk.
    """
    if not thread_id:
        return
    try:
        target = _attachments_root(workspace_root) / thread_id
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
    except Exception:
        pass


def read_bytes(workspace_root: str | Path, record: AttachmentRecord) -> bytes:
    abs_path = _resolve_safe_path(workspace_root, record.rel_path)
    return abs_path.read_bytes()


# ---------------------------------------------------------------------------
# PDF text extraction (lazy PyMuPDF import)
# ---------------------------------------------------------------------------

_PDF_MAX_PAGES = 20


def _load_pymupdf() -> Optional[Any]:
    try:
        import pymupdf  # type: ignore
        return pymupdf
    except Exception:
        pass
    try:
        import fitz  # type: ignore  # older PyMuPDF distributions
        return fitz
    except Exception:
        return None


def extract_pdf_text(workspace_root: str | Path, record: AttachmentRecord) -> str:
    """Best-effort plain-text extraction from an attached PDF.

    Caps at :data:`_PDF_MAX_PAGES` pages so token budgets stay sane on
    large docs. When PyMuPDF is not installed we degrade to an inline
    placeholder so the user still sees that the file was attached.
    """
    if record.kind != "pdf":
        return ""
    pymupdf = _load_pymupdf()
    if pymupdf is None:
        return (
            f"[PDF text extraction unavailable — `pip install pymupdf` to "
            f"enable. File: {record.filename or record.id}, "
            f"{record.bytes_} bytes]"
        )
    try:
        abs_path = _resolve_safe_path(workspace_root, record.rel_path)
    except ValueError as exc:
        return f"[PDF path outside workspace: {exc}]"
    try:
        doc = pymupdf.open(str(abs_path))
    except Exception as exc:
        return f"[PDF open failed for {record.filename}: {exc}]"
    try:
        pages: list[str] = []
        total = len(doc)
        scanned = min(total, _PDF_MAX_PAGES)
        for idx in range(scanned):
            try:
                text = doc[idx].get_text("text") or ""
            except Exception:
                text = ""
            pages.append(f"--- page {idx + 1} ---\n{text.strip()}")
        body = "\n\n".join(pages) if pages else "(no extractable text)"
        if total > scanned:
            body += f"\n\n[... {total - scanned} more pages truncated]"
        return body
    finally:
        try:
            doc.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Vision capability + multimodal payload construction
# ---------------------------------------------------------------------------

# Substring matches against the configured model name. Conservative — we
# would rather show a "vision unavailable" hint than silently corrupt a
# user message with image blocks the provider will reject.
_VISION_MODEL_PATTERNS = (
    "gpt-4o",
    "gpt-4-vision",
    "gpt-4.1",
    "gpt-5",
    "claude-3",
    "claude-sonnet",
    "claude-opus",
    "claude-haiku-4",
    "llava",
    "qwen-vl",
    "qwen2-vl",
    "gemma-vision",
    "pixtral",
    "vision",
)


def provider_supports_vision(provider: str, model: str) -> bool:
    needle = (model or "").lower()
    if not needle:
        return False
    return any(p in needle for p in _VISION_MODEL_PATTERNS)


def _image_data_url(record: AttachmentRecord, payload: bytes) -> str:
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{record.mime};base64,{encoded}"


def build_user_content_for_provider(
    *,
    provider: str,
    text: str,
    records: list[AttachmentRecord],
    workspace_root: str | Path,
    supports_vision: bool,
) -> Any:
    """Translate a user's text + attachments into a provider-native body.

    - Claude: list of ``{type: "text"|"image"}`` blocks. PDFs become
      additional text blocks with extracted page text (PyMuPDF).
    - OpenAI-compatible: list of ``{type: "text"|"image_url"}`` blocks.
    - Non-vision models: images are dropped (text-only attachment
      placeholder) so the request still goes through.

    When there are no effective non-text blocks the function returns a
    plain string so downstream token counters / response formats keep
    working as before.
    """
    if not records:
        return text or ""

    images = [r for r in records if r.kind == "image"]
    pdfs = [r for r in records if r.kind == "pdf"]

    if provider == "claude":
        blocks: list[dict[str, Any]] = []
        if text:
            blocks.append({"type": "text", "text": text})
        if supports_vision:
            for r in images:
                payload = read_bytes(workspace_root, r)
                blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": r.mime,
                            "data": base64.b64encode(payload).decode("ascii"),
                        },
                    }
                )
        else:
            for r in images:
                blocks.append(
                    {
                        "type": "text",
                        "text": (
                            f"[Image attached but the selected model does not"
                            f" support vision — skipped: {r.filename or r.id}]"
                        ),
                    }
                )
        for r in pdfs:
            blocks.append(
                {
                    "type": "text",
                    "text": (
                        f"[Attached PDF: {r.filename or r.id}]\n"
                        + extract_pdf_text(workspace_root, r)
                    ),
                }
            )
        return blocks

    # OpenAI-compatible providers (chatgpt, openrouter, lmstudio, llama_cpp,
    # ollama).
    blocks_oai: list[dict[str, Any]] = []
    if text:
        blocks_oai.append({"type": "text", "text": text})
    if supports_vision:
        for r in images:
            payload = read_bytes(workspace_root, r)
            blocks_oai.append(
                {"type": "image_url", "image_url": {"url": _image_data_url(r, payload)}}
            )
    else:
        for r in images:
            blocks_oai.append(
                {
                    "type": "text",
                    "text": (
                        f"[Image attached but the selected model does not"
                        f" support vision — skipped: {r.filename or r.id}]"
                    ),
                }
            )
    for r in pdfs:
        blocks_oai.append(
            {
                "type": "text",
                "text": (
                    f"[Attached PDF: {r.filename or r.id}]\n"
                    + extract_pdf_text(workspace_root, r)
                ),
            }
        )
    # Collapse to plain string when everything ended up as a single text
    # block — keeps the wire format identical for non-multimodal turns.
    if len(blocks_oai) == 1 and blocks_oai[0].get("type") == "text":
        return blocks_oai[0]["text"]
    return blocks_oai

