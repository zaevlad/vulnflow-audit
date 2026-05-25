"""FastAPI route registration for the chat subsystem.

Mounted from :mod:`dashboard.server` via ``register_chat_routes(app)``.
All endpoints are workspace-bound: the chat database lives at the
repository root and every file/tool tool call honors the workspace
boundary check.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from dashboard.chat import attachments as chat_attachments
from dashboard.chat import audit_log, storage, stream_buffer
from dashboard.chat.canvas import reverse_record
from dashboard.chat.schemas import (
    ChatSendRequest,
    ChatThreadCreateRequest,
    ChatThreadRenameRequest,
    ChatThreadTabRequest,
)
from dashboard.chat.service import ChatService, part_to_sse_payload


class CanvasAuditManualBatchRequest(BaseModel):
    revision_before: int
    revision_after: int
    snapshot_before: dict[str, Any] = Field(default_factory=dict)
    snapshot_after: dict[str, Any] = Field(default_factory=dict)
    reason: Optional[str] = None


class CanvasUndoRequest(BaseModel):
    audit_log_id: str
    mode: str = "single"


# How long a "running" stream is allowed to hang before the GC sweep
# marks it as failed. 10 minutes is a comfortable upper bound for any
# real chat turn — anything past that almost certainly never reaches
# `done` (process restart, abandoned client, etc.).
_STREAM_GC_TIMEOUT_SECONDS = 600


def register_chat_routes(
    app: FastAPI,
    *,
    workspace_root: Path,
    conf_path: Path,
) -> None:
    service = ChatService(workspace_root=workspace_root, conf_path=conf_path)

    # Reap any dangling "running" streams from the previous process
    # lifecycle. A crashed/killed server can leave rows in chat_streams
    # that would otherwise loop forever in the resume endpoint.
    try:
        stream_buffer.gc_running_streams(
            workspace_root,
            timeout_seconds=_STREAM_GC_TIMEOUT_SECONDS,
        )
    except Exception:
        # The chat subsystem must still start even if GC trips on a
        # locked db (rare). The next call will retry implicitly.
        pass

    @app.get("/api/chat/threads")
    def chat_threads_list() -> dict[str, Any]:
        threads = storage.list_threads(workspace_root)
        return {"threads": [t.model_dump() for t in threads]}

    @app.post("/api/chat/threads")
    def chat_threads_create(payload: ChatThreadCreateRequest) -> dict[str, Any]:
        thread = storage.create_thread(
            workspace_root,
            title=payload.title,
            default_tab=payload.default_tab,
        )
        return {"thread": thread.model_dump()}

    @app.patch("/api/chat/threads/{thread_id}/tab")
    def chat_threads_set_tab(thread_id: str, payload: ChatThreadTabRequest) -> dict[str, Any]:
        try:
            thread = storage.set_thread_default_tab(
                workspace_root, thread_id, payload.default_tab
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="Thread not found.")
        return {"thread": thread.model_dump()}

    @app.delete("/api/chat/threads/{thread_id}")
    def chat_threads_delete(thread_id: str) -> dict[str, Any]:
        deleted = storage.delete_thread(workspace_root, thread_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Thread not found.")
        return {"ok": True}

    @app.patch("/api/chat/threads/{thread_id}")
    def chat_threads_rename(thread_id: str, payload: ChatThreadRenameRequest) -> dict[str, Any]:
        try:
            thread = storage.rename_thread(workspace_root, thread_id, payload.title)
        except KeyError:
            raise HTTPException(status_code=404, detail="Thread not found.")
        return {"thread": thread.model_dump()}

    @app.get("/api/chat/search")
    def chat_search(
        q: str = Query(default=""),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        results = storage.search_messages(workspace_root, q, limit=limit)
        return {"query": q, "results": results}

    @app.get("/api/chat/threads/{thread_id}/messages")
    def chat_thread_messages(thread_id: str) -> dict[str, Any]:
        if not storage.thread_exists(workspace_root, thread_id):
            raise HTTPException(status_code=404, detail="Thread not found.")
        records = storage.list_messages(workspace_root, thread_id)
        all_attachments = chat_attachments.list_for_thread(workspace_root, thread_id)
        # Group attachments by message_id so the UI can render thumbnails
        # inside the matching message bubble.
        by_message: dict[str, list[dict[str, Any]]] = {}
        for att in all_attachments:
            if att.message_id:
                by_message.setdefault(att.message_id, []).append(att.to_dict())
        return {
            "messages": [
                {**r.model_dump(), "attachments": by_message.get(r.id, [])}
                for r in records
            ]
        }

    @app.post("/api/chat/attachments")
    async def chat_attachments_upload(
        thread_id: str = Form(...),
        file: UploadFile = File(...),
    ) -> dict[str, Any]:
        if not storage.thread_exists(workspace_root, thread_id):
            raise HTTPException(status_code=404, detail="Thread not found.")
        payload = await file.read()
        try:
            record = chat_attachments.save_attachment(
                workspace_root,
                thread_id=thread_id,
                payload=payload,
                filename=file.filename or "",
                mime=file.content_type or "",
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"attachment": record.to_dict()}

    @app.delete("/api/chat/attachments/{attachment_id}")
    def chat_attachments_delete(attachment_id: str) -> dict[str, Any]:
        deleted = chat_attachments.delete(workspace_root, attachment_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Attachment not found.")
        return {"ok": True}

    @app.get("/api/chat/attachments/{attachment_id}/content")
    def chat_attachments_content(attachment_id: str):
        record = chat_attachments.get(workspace_root, attachment_id)
        if not record:
            raise HTTPException(status_code=404, detail="Attachment not found.")
        try:
            abs_path = chat_attachments._resolve_safe_path(workspace_root, record.rel_path)
        except ValueError:
            raise HTTPException(status_code=400, detail="Attachment path is outside workspace.")
        if not abs_path.exists():
            raise HTTPException(status_code=404, detail="Attachment file missing on disk.")
        return FileResponse(
            path=str(abs_path),
            media_type=record.mime,
            filename=record.filename or abs_path.name,
        )

    @app.get("/api/canvas/audit")
    def canvas_audit_list(
        limit: int = Query(default=100, ge=1, le=500),
        source: Optional[str] = Query(default=None),
        thread_id: Optional[str] = Query(default=None),
    ) -> dict[str, Any]:
        if source and source not in {"agent", "user"}:
            raise HTTPException(status_code=400, detail="source must be 'agent' or 'user'.")
        records = audit_log.list_recent(
            workspace_root,
            limit=limit,
            source=source,
            thread_id=thread_id,
        )
        return {"records": records}

    @app.post("/api/canvas/audit")
    def canvas_audit_record_manual(payload: CanvasAuditManualBatchRequest) -> dict[str, Any]:
        record_id = audit_log.record_manual_batch(
            workspace_root,
            revision_before=payload.revision_before,
            revision_after=payload.revision_after,
            snapshot_before=payload.snapshot_before,
            snapshot_after=payload.snapshot_after,
            reason=payload.reason,
        )
        return {"ok": True, "id": record_id}

    @app.post("/api/canvas/undo")
    def canvas_undo(payload: CanvasUndoRequest) -> dict[str, Any]:
        mode = (payload.mode or "single").strip()
        if mode not in {"single", "batch_to"}:
            raise HTTPException(
                status_code=400,
                detail="mode must be 'single' or 'batch_to'.",
            )

        target = audit_log.get_record(workspace_root, payload.audit_log_id)
        if not target:
            raise HTTPException(status_code=404, detail="Audit log record not found.")

        if mode == "single":
            actions = reverse_record(target)
            if not actions:
                raise HTTPException(
                    status_code=400,
                    detail=f"Action of kind '{target.get('action_kind')}' cannot be reversed.",
                )
            return {
                "ok": True,
                "mode": mode,
                "source_records": [target["id"]],
                "actions": actions,
            }

        # mode == "batch_to": undo every record applied at-or-after the
        # target, newest first.
        all_records = audit_log.list_recent(workspace_root, limit=500)
        try:
            target_index = next(
                idx for idx, r in enumerate(all_records) if r["id"] == target["id"]
            )
        except StopIteration:
            raise HTTPException(
                status_code=400,
                detail="Target record is outside the recent audit window.",
            )

        # list_recent returns DESC, so [0..target_index] are the records
        # to reverse in that order (newest first).
        in_range = all_records[: target_index + 1]
        actions: list[dict[str, Any]] = []
        source_records: list[str] = []
        skipped: list[dict[str, Any]] = []
        for record in in_range:
            reversed_actions = reverse_record(record)
            if not reversed_actions:
                skipped.append(
                    {"id": record["id"], "action_kind": record["action_kind"]}
                )
                continue
            actions.extend(reversed_actions)
            source_records.append(record["id"])

        if not actions:
            raise HTTPException(
                status_code=400,
                detail="No reversible actions found in the requested range.",
            )

        return {
            "ok": True,
            "mode": mode,
            "source_records": source_records,
            "actions": actions,
            "skipped": skipped,
        }

    @app.post("/api/chat/send")
    async def chat_send(payload: ChatSendRequest) -> StreamingResponse:
        thread_id = payload.thread_id or ""
        if thread_id:
            if not storage.thread_exists(workspace_root, thread_id):
                raise HTTPException(status_code=404, detail="Thread not found.")
        else:
            thread = storage.create_thread(workspace_root)
            thread_id = thread.id

        if not payload.message.strip():
            raise HTTPException(status_code=400, detail="Empty message.")
        if not payload.model.provider or not payload.model.model:
            raise HTTPException(
                status_code=400,
                detail="provider and model are required.",
            )

        stream_id = stream_buffer.create_stream(workspace_root, thread_id)

        async def stream() -> AsyncIterator[bytes]:
            # First event echoes thread_id + stream_id so the client can
            # persist both before the agent loop even starts.
            init_payload = json.dumps(
                {"type": "init", "thread_id": thread_id, "stream_id": stream_id},
                ensure_ascii=False,
            )
            yield f"data: {init_payload}\n\n".encode("utf-8")

            final_status = "completed"
            try:
                async for part in service.run(
                    payload,
                    thread_id=thread_id,
                    stream_id=stream_id,
                ):
                    body = part_to_sse_payload(part)
                    yield f"data: {body}\n\n".encode("utf-8")
                    if part.get("type") == "error":
                        final_status = "failed"
            except Exception as exc:
                final_status = "failed"
                err_payload = json.dumps(
                    {"type": "error", "scope": "stream", "message": str(exc)},
                    ensure_ascii=False,
                )
                yield f"data: {err_payload}\n\n".encode("utf-8")
            finally:
                try:
                    stream_buffer.set_status(workspace_root, stream_id, final_status)
                except Exception:
                    pass
                yield b"data: [DONE]\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
                "X-Stream-Id": stream_id,
            },
        )

    @app.get("/api/chat/streams/{stream_id}")
    async def chat_stream_resume(
        stream_id: str,
        from_seq: int = Query(default=0, ge=0),
    ) -> StreamingResponse:
        info = stream_buffer.get_stream(workspace_root, stream_id)
        if not info:
            raise HTTPException(status_code=404, detail="Stream not found.")

        async def stream() -> AsyncIterator[bytes]:
            init_payload = json.dumps(
                {
                    "type": "init",
                    "thread_id": info["thread_id"],
                    "stream_id": stream_id,
                    "resumed": True,
                },
                ensure_ascii=False,
            )
            yield f"data: {init_payload}\n\n".encode("utf-8")

            last_seq = int(from_seq)

            # Flush everything already buffered.
            for seq, part in stream_buffer.list_parts(
                workspace_root, stream_id, from_seq=last_seq
            ):
                last_seq = seq
                yield f"data: {part_to_sse_payload(part)}\n\n".encode("utf-8")

            # If the original producer is still writing, tail the
            # buffer until the status moves out of "running" — at most
            # for the GC timeout window, after which we give up.
            poll_deadline = asyncio.get_event_loop().time() + _STREAM_GC_TIMEOUT_SECONDS
            poll_interval = 0.2

            while True:
                fresh = stream_buffer.get_stream(workspace_root, stream_id)
                if not fresh:
                    break

                new_parts = stream_buffer.list_parts(
                    workspace_root, stream_id, from_seq=last_seq
                )
                for seq, part in new_parts:
                    last_seq = seq
                    yield f"data: {part_to_sse_payload(part)}\n\n".encode("utf-8")

                if fresh["status"] != "running":
                    break

                if asyncio.get_event_loop().time() > poll_deadline:
                    timeout_payload = json.dumps(
                        {
                            "type": "error",
                            "scope": "resume",
                            "message": "Resume polling deadline reached.",
                        },
                        ensure_ascii=False,
                    )
                    yield f"data: {timeout_payload}\n\n".encode("utf-8")
                    break

                await asyncio.sleep(poll_interval)

            yield b"data: [DONE]\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
                "X-Stream-Id": stream_id,
                "X-Stream-Status": info["status"],
            },
        )
