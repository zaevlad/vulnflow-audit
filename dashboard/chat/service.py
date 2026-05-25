"""Chat agent orchestrator.

Drives the tool-using LLM loop on top of :func:`send_to_model` and the
existing OpenAI / Anthropic tool-call normalization in
:mod:`dashboard.pipeline.external_tools`. Streams envelope parts as
they are produced so the frontend can render text, plan cards, tool
status, widget HTML and canvas actions progressively.

This is a direct translation of the OpenGenerativeUI deep-agent flow
(visualization workflow: acknowledge → plan → build → narrate, plus
skills-based progressive disclosure) onto vulnflow's existing model
routing — no external agent framework is required.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Iterable

from dashboard.chat import attachments as chat_attachments
from dashboard.chat import audit_log, stream_buffer
from dashboard.chat import canvas as canvas_module
from dashboard.chat import storage
from dashboard.chat.canvas import CanvasState
from dashboard.chat.context_manager import compact_history, load_context_config
from dashboard.chat.schemas import (
    ChatSendRequest,
    EnvelopePart,
    EnvelopePartCanvasAction,
    EnvelopePartContextInfo,
    EnvelopePartDone,
    EnvelopePartError,
    EnvelopePartPlan,
    EnvelopePartText,
    EnvelopePartToolResult,
    EnvelopePartToolStatus,
    EnvelopePartWidget,
)
from dashboard.chat.skills import build_skills_index_block, load_chat_skills
from dashboard.chat.tools import ChatToolRegistry
from dashboard.pipeline.external_tools import (
    append_tool_messages,
    normalize_model_tool_calls,
)
from dashboard.pipeline.llm_router import send_to_model

_MAX_TOOL_ITERATIONS = 8


_READ_ONLY_CAPABILITY_LINE = (
    "- (canvas mutations are DISABLED in this tab — `emit_canvas_action` is\n"
    "  not available; do not promise to add/edit nodes)"
)
_FULL_CAPABILITY_LINE = (
    "- create / delete / edit pipeline nodes and edges via `emit_canvas_action`"
)


def _system_prompt(chat_mode: str = "full") -> str:
    skills = load_chat_skills()
    skills_index = build_skills_index_block(skills)
    canvas_line = (
        _READ_ONLY_CAPABILITY_LINE if chat_mode == "read_only" else _FULL_CAPABILITY_LINE
    )
    canvas_section = (
        "## Canvas workflow\n\n"
        "**Chat mode: `read_only`** — the user is currently on a non-pipeline\n"
        "tab. You CANNOT mutate the canvas. The `emit_canvas_action` tool is\n"
        "not exposed. Answer questions about the canvas/state but do not\n"
        "promise edits."
        if chat_mode == "read_only"
        else
        "## Canvas workflow\n\n"
        "Before emitting any `emit_canvas_action` call, read the\n"
        "`canvas-actions` skill via `read_skill(\"canvas-actions\")` and inspect\n"
        "`ui_context` (already provided in the user message) for current\n"
        "`currentTab`, `nodes`, `edges`, `catalogs` and `availableTools`. Do\n"
        "not invent IDs, paths or model names. Only emit canvas actions when\n"
        "`currentTab == \"pipeline\"`."
    )

    base = f"""You are the VulnFlow Chat Agent — an in-app AI pair that lives in
the right-side panel of a local Solidity audit workbench. The user is
working on a private repository they cloned to disk; everything you do
must stay inside that repository's workspace boundary.

You can:

- answer in plain text
- read workspace files (`read_workspace_file`, `list_workspace_directory`)
- query the docs RAG index (`search_docs`)
- list resource catalogs and configured external tools (`list_resources`)
- read full skill bodies on demand (`read_skill`)
- plan visualizations (`plan_visualization`)
- render interactive HTML/SVG visualizations in a sandboxed iframe
  (`widget_renderer`)
{canvas_line}
- invoke configured external tools (HornetMCP, Solodit, etc.)

## Visualization workflow (mandatory)

When producing any visual response, follow this exact sequence:

1. Acknowledge — 1–2 sentences setting context for what the visualization
   will show.
2. Plan — call `plan_visualization` with approach, technology and 2–4
   key elements.
3. Build — call `widget_renderer` with self-contained HTML.
4. Narrate — 2–3 sentences walking through what was built and offering
   to go deeper.

NEVER call `widget_renderer` without calling `plan_visualization`
first in the same turn.

{canvas_section}

## Audit reasoning

When the user asks about Solidity, contracts or tests in their project,
read the `audit-context` skill via `read_skill("audit-context")` and
ground your answer in the actual files via `read_workspace_file`.
Never answer Solidity code questions purely from memory.

## Style

Be concise. Cite file paths in `path:line` form when relevant. When the
docs RAG returns matches, mention the matched files in your text.
Never fabricate. If a tool fails, surface the failure to the user and
keep the rest of the response useful.
"""

    if skills_index:
        return f"{base}\n\n{skills_index}"
    return base


def _resolve_chat_mode(current_tab: str) -> str:
    # Only the pipeline tab allows canvas mutations. Anything else (audit,
    # editor, settings, missing) is treated as read-only so the model
    # never tries to emit canvas actions where they cannot be applied.
    return "full" if str(current_tab or "").strip() == "pipeline" else "read_only"


def _filter_tool_schemas_for_mode(
    schemas: list[dict[str, Any]],
    chat_mode: str,
) -> list[dict[str, Any]]:
    """Drop canvas-mutating tools when the chat is in read-only mode."""
    if chat_mode == "full":
        return schemas
    blocked = {"emit_canvas_action"}

    def _tool_name(s: dict[str, Any]) -> str:
        # OpenAI-style: {"type": "function", "function": {"name": ...}}
        # Anthropic-style: {"name": ...}
        fn = s.get("function")
        if isinstance(fn, dict) and "name" in fn:
            return str(fn["name"])
        return str(s.get("name") or "")

    return [s for s in schemas if _tool_name(s) not in blocked]


def _format_ui_context_block(req: ChatSendRequest) -> str:
    snapshot: dict[str, Any] = {
        "currentTab": req.ui_context.currentTab,
        "auditPath": req.ui_context.auditPath,
        "workspacePath": req.ui_context.workspacePath,
        "excludedPaths": req.ui_context.excludedPaths,
        "pipelineName": req.ui_context.pipelineName,
        "savedPipeline": req.ui_context.savedPipeline,
        "canvasRevision": req.ui_context.canvasRevision,
        "auditMode": req.ui_context.auditMode,
        "selectedNodes": req.ui_context.selectedNodes,
        "selectedFiles": req.ui_context.selectedFiles,
        "docsStatus": req.ui_context.docsStatus.model_dump(),
        "nodes": [n.model_dump() for n in req.ui_context.nodes],
        "edges": [e.model_dump() for e in req.ui_context.edges],
        "viewport": req.ui_context.viewport.model_dump(),
        "catalogs": {k: v.model_dump() for k, v in req.ui_context.catalogs.items()},
        "availableTools": [t.model_dump() for t in req.ui_context.availableTools],
    }
    return (
        "## Current UI Context (authoritative session state)\n\n"
        "```json\n"
        + json.dumps(snapshot, ensure_ascii=False, indent=2)
        + "\n```"
    )


def _format_history_for_provider(
    provider: str,
    messages: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entry in messages:
        role = entry.get("role")
        if role not in {"system", "user", "assistant"}:
            continue
        out.append({"role": role, "content": str(entry.get("content", ""))})
    return out


class _SeqEmitter:
    """Wraps the SSE queue + durable stream buffer.

    Each ``emit`` increments a monotonic seq, dumps the envelope part to
    a dict, persists it (so disconnected clients can resume) and pushes
    the seq-stamped dict to the queue consumed by the route handler.
    """

    def __init__(
        self,
        queue: asyncio.Queue[dict[str, Any] | None],
        workspace_root: Path,
        stream_id: str,
    ) -> None:
        self.queue = queue
        self.workspace_root = workspace_root
        self.stream_id = stream_id
        self.seq = 0

    async def emit(self, part: EnvelopePart) -> dict[str, Any]:
        self.seq += 1
        payload = part.model_dump()
        payload["seq"] = self.seq
        # Persist before queueing so a slow consumer that disconnects
        # mid-burst can still recover via the resume endpoint.
        try:
            stream_buffer.append_part(
                self.workspace_root,
                self.stream_id,
                self.seq,
                payload,
            )
        except Exception:
            # The chat turn must keep running even if the durable buffer
            # is temporarily unavailable — degrade gracefully.
            pass
        await self.queue.put(payload)
        return payload


def part_to_sse_payload(part: dict[str, Any]) -> str:
    return json.dumps(part, ensure_ascii=False)


class ChatService:
    def __init__(self, workspace_root: Path, conf_path: Path) -> None:
        self.workspace_root = workspace_root
        self.conf_path = conf_path

    async def run(
        self,
        req: ChatSendRequest,
        *,
        thread_id: str,
        stream_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        emitter = _SeqEmitter(queue, self.workspace_root, stream_id)

        producer = asyncio.create_task(self._produce(req, thread_id, emitter))

        try:
            while True:
                part = await queue.get()
                if part is None:
                    break
                yield part
        finally:
            if not producer.done():
                producer.cancel()
                try:
                    await producer
                except (asyncio.CancelledError, Exception):
                    pass

    async def _produce(
        self,
        req: ChatSendRequest,
        thread_id: str,
        emitter: "_SeqEmitter",
    ) -> None:
        queue = emitter.queue
        envelope_parts: list[dict[str, Any]] = []
        assistant_text_total = ""

        try:
            registry = ChatToolRegistry(
                workspace_root=self.workspace_root,
                conf_path=self.conf_path,
                ui_context=req.ui_context,
            )
            canvas_state = CanvasState(req.ui_context)

            history_records = storage.list_messages(self.workspace_root, thread_id)
            history_messages: list[dict[str, Any]] = []
            for record in history_records:
                if record.role in {"user", "assistant"}:
                    history_messages.append(
                        {"role": record.role, "content": record.content}
                    )

            chat_mode = _resolve_chat_mode(req.ui_context.currentTab)
            provider = req.model.provider
            model = req.model.model

            # Resolve attachments. We enforce thread ownership so a
            # client can't reference another thread's files by ID.
            attachment_records: list[chat_attachments.AttachmentRecord] = []
            if req.attachment_ids:
                raw = chat_attachments.get_many(self.workspace_root, req.attachment_ids)
                attachment_records = [r for r in raw if r.thread_id == thread_id]

            supports_vision = chat_attachments.provider_supports_vision(provider, model)
            user_content: Any = chat_attachments.build_user_content_for_provider(
                provider=provider,
                text=req.message,
                records=attachment_records,
                workspace_root=self.workspace_root,
                supports_vision=supports_vision,
            )

            messages: list[dict[str, Any]] = [
                {"role": "system", "content": _system_prompt(chat_mode)},
                {"role": "system", "content": _format_ui_context_block(req)},
            ]
            messages.extend(_format_history_for_provider(req.model.provider, history_messages))
            messages.append({"role": "user", "content": user_content})

            tool_schemas = _filter_tool_schemas_for_mode(
                registry.tool_schemas(provider),
                chat_mode,
            )

            context_config = load_context_config(self.conf_path)
            messages, compaction = await compact_history(
                messages,
                provider=provider,
                model=model,
                tools=tool_schemas,
                config=context_config,
            )
            context_part = EnvelopePartContextInfo(
                input_tokens=compaction.input_tokens_after,
                input_tokens_before_compaction=compaction.input_tokens_before,
                max_input_tokens=context_config.max_input_tokens,
                kept_messages=compaction.kept_messages,
                compacted=compaction.compacted,
                compacted_messages=compaction.compacted_messages,
                summary_provider=compaction.summary_provider,
                summary_model=compaction.summary_model,
                summary_error=compaction.summary_error,
            )
            envelope_parts.append(await emitter.emit(context_part))

            for iteration in range(_MAX_TOOL_ITERATIONS):
                response = await send_to_model(
                    provider=provider,
                    model=model,
                    messages=messages,
                    json_mode=False,
                    tools=tool_schemas or None,
                )

                content = str(response.get("content") or "")
                if content.strip():
                    text_part = EnvelopePartText(text=content, final=False)
                    assistant_text_total += content
                    envelope_parts.append(await emitter.emit(text_part))

                raw_calls = response.get("tool_calls")
                calls = normalize_model_tool_calls(raw_calls)
                if not calls:
                    break

                tool_results: list[dict[str, Any]] = []
                for call in calls:
                    name = call["name"]
                    args = call.get("arguments") or {}

                    pending = EnvelopePartToolStatus(
                        call_id=call["id"],
                        name=name,
                        status="running",
                        arguments=args,
                    )
                    envelope_parts.append(await emitter.emit(pending))

                    if not registry.is_known(name):
                        result = {"ok": False, "error": "unknown_tool", "name": name}
                    else:
                        result = await registry.call(name, args)

                    # Specialized envelope parts derived from particular tools
                    if name == "plan_visualization" and result.get("ok"):
                        plan_part = EnvelopePartPlan(
                            approach=result["approach"],
                            technology=result["technology"],
                            key_elements=list(result.get("key_elements") or []),
                        )
                        envelope_parts.append(await emitter.emit(plan_part))

                    if name == "widget_renderer" and result.get("ok"):
                        widget_part = EnvelopePartWidget(
                            widget_id=uuid.uuid4().hex,
                            title=result["title"],
                            description=result.get("description", ""),
                            html=result["html"],
                        )
                        envelope_parts.append(await emitter.emit(widget_part))

                    if name == "emit_canvas_action":
                        revision_before = int(req.ui_context.canvasRevision)
                        # Snapshot of the affected entity *before* the action,
                        # taken from the ui_context the agent saw on this turn.
                        snapshot_before = audit_log.snapshot_entity_for_action(
                            kind=str(args.get("kind", "")),
                            ui_context_nodes=[n.model_dump() for n in req.ui_context.nodes],
                            ui_context_edges=[e.model_dump() for e in req.ui_context.edges],
                            action=args,
                        )
                        canvas_part = canvas_module.validate_and_prepare(
                            raw_action=args,
                            state=canvas_state,
                            incoming_revision=revision_before,
                            current_revision=revision_before,
                        )
                        envelope_parts.append(await emitter.emit(canvas_part))

                        if not canvas_part.rejected:
                            snapshot_after = audit_log.snapshot_entity_after_action(
                                kind=str(canvas_part.action.get("kind", "")),
                                action=canvas_part.action,
                            )
                            try:
                                audit_log.record_agent_action(
                                    self.workspace_root,
                                    thread_id=thread_id,
                                    message_id=None,
                                    action_kind=str(canvas_part.action.get("kind", "")),
                                    action=dict(canvas_part.action),
                                    revision_before=revision_before,
                                    revision_after=revision_before + 1,
                                    snapshot_before=snapshot_before,
                                    snapshot_after=snapshot_after,
                                    reason=None,
                                )
                            except Exception:
                                # Audit failures must not abort the chat turn —
                                # the canvas envelope part is already emitted.
                                pass

                        # Replace generic tool ok with reconciliation info for the model
                        result = {
                            "ok": not canvas_part.rejected,
                            "rejected": canvas_part.rejected,
                            "reason": canvas_part.reason,
                            "reconciled": canvas_part.reconciled,
                            "action_id": canvas_part.action_id,
                        }

                    completed = EnvelopePartToolStatus(
                        call_id=call["id"],
                        name=name,
                        status="completed" if result.get("ok", False) else "failed",
                        arguments=args,
                    )
                    envelope_parts.append(await emitter.emit(completed))

                    tool_result_part = EnvelopePartToolResult(
                        call_id=call["id"],
                        name=name,
                        output=result,
                        ok=bool(result.get("ok", False)),
                        error=result.get("error") if not result.get("ok", False) else None,
                    )
                    envelope_parts.append(await emitter.emit(tool_result_part))

                    tool_results.append({"id": call["id"], "output": result})

                append_tool_messages(
                    provider=provider,
                    messages=messages,
                    assistant_text=content,
                    tool_calls=calls,
                    tool_results=tool_results,
                )
            else:
                limit_msg = EnvelopePartError(
                    scope="tool_loop",
                    message=f"Tool loop iteration limit ({_MAX_TOOL_ITERATIONS}) reached.",
                )
                envelope_parts.append(await emitter.emit(limit_msg))

            # Persist user + assistant messages. We keep the persisted
            # user content as plain text — attachments live in their own
            # table and are linked via message_id so we can re-render
            # them next time the thread is loaded.
            user_msg = storage.append_message(
                self.workspace_root,
                thread_id,
                role="user",
                content=req.message,
                parts=[],
            )
            if attachment_records:
                try:
                    chat_attachments.attach_to_message(
                        self.workspace_root,
                        [r.id for r in attachment_records],
                        user_msg.id,
                    )
                except Exception:
                    # Non-fatal: the row still exists for the thread,
                    # only the message-level grouping is missing.
                    pass
            storage.append_message(
                self.workspace_root,
                thread_id,
                role="assistant",
                content=assistant_text_total,
                parts=envelope_parts,
            )
            storage.maybe_autoname_thread(self.workspace_root, thread_id, req.message)

            done = EnvelopePartDone(
                thread_id=thread_id,
                canvas_revision=int(req.ui_context.canvasRevision),
            )
            await emitter.emit(done)

        except Exception as exc:
            try:
                err = EnvelopePartError(scope="service", message=str(exc))
                await emitter.emit(err)
            except Exception:
                pass
        finally:
            await queue.put(None)
