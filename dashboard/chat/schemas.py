"""Pydantic schemas for the VulnFlow chat subsystem.

The shapes here form the contract between the React chat panel and the
FastAPI backend. They mirror the OpenGenerativeUI envelope concept while
being grounded in vulnflow's existing ReactFlow canvas, resource catalogs
and external tool model.
"""

from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# UI context payload (sent by the frontend on every /api/chat/send call)
# ---------------------------------------------------------------------------


class CanvasNodePayload(BaseModel):
    id: str
    type: str = "agent"
    position: dict[str, float] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)
    parentId: Optional[str] = None
    width: Optional[float] = None
    height: Optional[float] = None


class CanvasEdgePayload(BaseModel):
    id: str
    source: str
    target: str
    sourceHandle: Optional[str] = None
    targetHandle: Optional[str] = None
    type: Optional[str] = None
    data: dict[str, Any] = Field(default_factory=dict)


class CanvasViewportPayload(BaseModel):
    x: float = 0.0
    y: float = 0.0
    zoom: float = 1.0


class CatalogEntryPayload(BaseModel):
    name: str
    path: str = ""
    kind: str = "file"


class CatalogSectionPayload(BaseModel):
    path: str = ""
    entries: list[CatalogEntryPayload] = Field(default_factory=list)


class AvailableToolEndpoint(BaseModel):
    name: str
    description: str = ""
    method: str = "GET"


class AvailableToolPayload(BaseModel):
    name: str
    description: str = ""
    endpoints: list[AvailableToolEndpoint] = Field(default_factory=list)


class DocsStatusPayload(BaseModel):
    ready: bool = False
    indexed_files: int = 0
    chunk_count: int = 0
    last_indexed: Optional[str] = None


class UiContext(BaseModel):
    """Authoritative session context for the agent on every chat send."""

    currentTab: str = "pipeline"
    auditPath: str = ""
    workspacePath: str = ""
    excludedPaths: list[str] = Field(default_factory=list)
    docsStatus: DocsStatusPayload = Field(default_factory=DocsStatusPayload)
    catalogs: dict[str, CatalogSectionPayload] = Field(default_factory=dict)
    availableTools: list[AvailableToolPayload] = Field(default_factory=list)
    pipelineName: str = ""
    savedPipeline: str = ""
    nodes: list[CanvasNodePayload] = Field(default_factory=list)
    edges: list[CanvasEdgePayload] = Field(default_factory=list)
    viewport: CanvasViewportPayload = Field(default_factory=CanvasViewportPayload)
    selectedNodes: list[str] = Field(default_factory=list)
    selectedFiles: list[str] = Field(default_factory=list)
    canvasRevision: int = 0
    auditMode: str = "contract"


class ChatModelChoice(BaseModel):
    provider: str
    model: str


class ChatSendRequest(BaseModel):
    thread_id: Optional[str] = None
    message: str
    ui_context: UiContext
    model: ChatModelChoice
    # IDs of chat_attachments rows that should be inlined into the user
    # message. Empty list means a plain-text turn.
    attachment_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Canvas action protocol
# ---------------------------------------------------------------------------


class CanvasActionCreateNode(BaseModel):
    kind: Literal["create_node"] = "create_node"
    node_type: Literal["agent", "patterns", "memory", "code", "tool"] = "agent"
    node_id: Optional[str] = None
    position: Optional[dict[str, float]] = None
    data: dict[str, Any] = Field(default_factory=dict)
    parentId: Optional[str] = None


class CanvasActionDeleteNode(BaseModel):
    kind: Literal["delete_node"] = "delete_node"
    node_id: str


class CanvasActionUpdateNode(BaseModel):
    kind: Literal["update_node"] = "update_node"
    node_id: str
    data: dict[str, Any] = Field(default_factory=dict)
    position: Optional[dict[str, float]] = None


class CanvasActionCreateEdge(BaseModel):
    kind: Literal["create_edge"] = "create_edge"
    edge_id: Optional[str] = None
    source: str
    target: str
    sourceHandle: Optional[str] = None
    targetHandle: Optional[str] = None
    data: dict[str, Any] = Field(default_factory=dict)


class CanvasActionDeleteEdge(BaseModel):
    kind: Literal["delete_edge"] = "delete_edge"
    edge_id: str


class CanvasActionUpdateEdge(BaseModel):
    kind: Literal["update_edge"] = "update_edge"
    edge_id: str
    data: dict[str, Any] = Field(default_factory=dict)


class CanvasActionSetViewport(BaseModel):
    kind: Literal["set_viewport"] = "set_viewport"
    viewport: CanvasViewportPayload


CanvasAction = Union[
    CanvasActionCreateNode,
    CanvasActionDeleteNode,
    CanvasActionUpdateNode,
    CanvasActionCreateEdge,
    CanvasActionDeleteEdge,
    CanvasActionUpdateEdge,
    CanvasActionSetViewport,
]


# ---------------------------------------------------------------------------
# Envelope parts streamed over SSE
# ---------------------------------------------------------------------------


class EnvelopePartText(BaseModel):
    type: Literal["text"] = "text"
    text: str
    role: Literal["assistant"] = "assistant"
    final: bool = False


class EnvelopePartToolStatus(BaseModel):
    type: Literal["tool_status"] = "tool_status"
    call_id: str
    name: str
    status: Literal["pending", "running", "completed", "failed"]
    arguments: dict[str, Any] = Field(default_factory=dict)


class EnvelopePartToolResult(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    call_id: str
    name: str
    output: Any = None
    ok: bool = True
    error: Optional[str] = None


class EnvelopePartPlan(BaseModel):
    type: Literal["plan"] = "plan"
    approach: str
    technology: str
    key_elements: list[str] = Field(default_factory=list)


class EnvelopePartWidget(BaseModel):
    type: Literal["widget"] = "widget"
    widget_id: str
    title: str
    description: str = ""
    html: str


class EnvelopePartCanvasAction(BaseModel):
    type: Literal["canvas_action"] = "canvas_action"
    action_id: str
    action: dict[str, Any]
    canvas_revision: int
    applied: bool = False
    reconciled: bool = False
    rejected: bool = False
    reason: Optional[str] = None


class EnvelopePartError(BaseModel):
    type: Literal["error"] = "error"
    scope: str
    message: str


class EnvelopePartDone(BaseModel):
    type: Literal["done"] = "done"
    thread_id: str
    canvas_revision: int


class EnvelopePartContextInfo(BaseModel):
    """Snapshot of context-window state for the current chat turn.

    Emitted once at the start of the agent loop so the UI can show the
    token budget pill (e.g. ``12k / 120k tokens · 14 msgs compacted``).
    """

    type: Literal["context_info"] = "context_info"
    input_tokens: int
    input_tokens_before_compaction: int
    max_input_tokens: int
    kept_messages: int
    compacted: bool = False
    compacted_messages: int = 0
    summary_provider: Optional[str] = None
    summary_model: Optional[str] = None
    summary_error: Optional[str] = None


EnvelopePart = Union[
    EnvelopePartText,
    EnvelopePartToolStatus,
    EnvelopePartToolResult,
    EnvelopePartPlan,
    EnvelopePartWidget,
    EnvelopePartCanvasAction,
    EnvelopePartError,
    EnvelopePartDone,
    EnvelopePartContextInfo,
]


class ChatThreadInfo(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int = 0
    # Pin this thread to a specific builder tab — `pipeline`, `audit`,
    # `editor`, or `None` (visible from any tab).
    default_tab: Optional[str] = None


class ChatMessageRecord(BaseModel):
    id: str
    thread_id: str
    role: Literal["user", "assistant", "system"]
    content: str
    parts: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str


class ChatThreadCreateRequest(BaseModel):
    title: Optional[str] = None
    default_tab: Optional[str] = None


class ChatThreadRenameRequest(BaseModel):
    title: str


class ChatThreadTabRequest(BaseModel):
    default_tab: Optional[str] = None
