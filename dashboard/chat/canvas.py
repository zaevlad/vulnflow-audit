"""Canvas action validator and reconciler.

The agent emits structural mutations as JSON payloads. Before the
frontend dispatcher applies them to ReactFlow state, this module
re-validates every action against the live ui_context that came on
the same chat request:

- node / edge IDs referenced by ``delete_*`` / ``update_*`` must still
  exist in ``ui_context``.
- resource references in ``data`` (skill, lead_skill, pattern, memory,
  tool, contract paths, provider, model) must exist in
  ``ui_context.catalogs`` / ``ui_context.availableTools``.
- ``currentTab`` must be ``pipeline``. Audit and Editor reject silently
  (the action is queued as rejected with a reason).

Reconciliation strategy: "auto-rebase if safe, reject otherwise".
Safety is decided per action — if all referenced IDs still resolve in
the current ui_context, the action is rebased onto the new revision;
otherwise it is rejected.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from dashboard.chat.schemas import (
    CanvasActionCreateEdge,
    CanvasActionCreateNode,
    CanvasActionDeleteEdge,
    CanvasActionDeleteNode,
    CanvasActionSetViewport,
    CanvasActionUpdateEdge,
    CanvasActionUpdateNode,
    EnvelopePartCanvasAction,
    UiContext,
)


_ALLOWED_NODE_TYPES = {"agent", "patterns", "memory", "code", "tool"}


class CanvasState:
    """Mutable view of the canvas that tracks IDs introduced by emitted actions.

    Lets multiple actions in the same chat turn reference each other
    consistently (e.g. create_node followed by create_edge).
    """

    def __init__(self, ui_context: UiContext) -> None:
        self.node_ids: set[str] = {n.id for n in ui_context.nodes}
        self.edge_ids: set[str] = {e.id for e in ui_context.edges}
        self.node_types: dict[str, str] = {n.id: n.type for n in ui_context.nodes}
        self.parent_of: dict[str, Optional[str]] = {n.id: n.parentId for n in ui_context.nodes}
        # Take a snapshot of catalogs so order-of-validation is irrelevant.
        self.catalog_paths: dict[str, set[str]] = {
            key: {entry.path for entry in section.entries if entry.path}
            for key, section in ui_context.catalogs.items()
        }
        self.tool_names: set[str] = {tool.name for tool in ui_context.availableTools}
        self.ui_context = ui_context

    def register_node(self, node_id: str, node_type: str, parent_id: Optional[str]) -> None:
        self.node_ids.add(node_id)
        self.node_types[node_id] = node_type
        self.parent_of[node_id] = parent_id

    def drop_node(self, node_id: str) -> None:
        self.node_ids.discard(node_id)
        self.node_types.pop(node_id, None)
        self.parent_of.pop(node_id, None)

    def register_edge(self, edge_id: str) -> None:
        self.edge_ids.add(edge_id)

    def drop_edge(self, edge_id: str) -> None:
        self.edge_ids.discard(edge_id)


def _stable_node_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _validate_data_references(
    *,
    data: dict[str, Any],
    state: CanvasState,
) -> Optional[str]:
    """Return an error message if any reference in *data* is unknown."""

    def _check_single(value: Any, sections: tuple[str, ...]) -> Optional[str]:
        if value in (None, ""):
            return None
        candidate = str(value)
        for section in sections:
            if candidate in state.catalog_paths.get(section, set()):
                return None
        return f"value '{candidate}' not found in catalogs {sections}"

    def _check_list(values: Any, sections: tuple[str, ...]) -> Optional[str]:
        if not values:
            return None
        if not isinstance(values, list):
            return f"expected list, got {type(values).__name__}"
        for value in values:
            err = _check_single(value, sections)
            if err:
                return err
        return None

    # Resource catalog references
    checks: list[tuple[str, tuple[str, ...], bool]] = [
        ("skill", ("skills",), False),
        ("leadSkill", ("lead_skills",), False),
        ("lead_skill", ("lead_skills",), False),
        ("patterns", ("patterns",), True),
        ("memory", ("memory",), True),
        ("memoryPromt", ("memory_promts",), True),
        ("memoryPrompts", ("memory_promts",), True),
        ("auditDocs", ("audit_docs",), True),
    ]
    for key, sections, is_list in checks:
        if key not in data:
            continue
        value = data[key]
        err = _check_list(value, sections) if is_list else _check_single(value, sections)
        if err:
            return f"{key}: {err}"

    # External tools
    if "tools" in data and data["tools"] is not None:
        tools_value = data["tools"]
        if not isinstance(tools_value, list):
            return "tools: expected list"
        for tool_name in tools_value:
            if str(tool_name) not in state.tool_names:
                return f"tools: '{tool_name}' is not a configured external tool"

    # Contract paths — must lie under auditPath and not in excludedPaths
    contract_paths = data.get("contractPaths")
    if contract_paths is not None:
        if not isinstance(contract_paths, list):
            return "contractPaths: expected list"
        audit_root = (state.ui_context.auditPath or "").rstrip("/\\")
        excluded = {str(p).replace("\\", "/").strip("/") for p in state.ui_context.excludedPaths}
        for path_value in contract_paths:
            candidate = str(path_value).replace("\\", "/").strip()
            if audit_root and not candidate.replace("\\", "/").startswith(
                audit_root.replace("\\", "/")
            ):
                return f"contractPaths: '{candidate}' is outside auditPath"
            normalized = candidate.strip("/")
            for ex in excluded:
                if ex and (normalized == ex or normalized.startswith(f"{ex}/")):
                    return f"contractPaths: '{candidate}' is in excludedPaths"
    return None


def _build_rejection(
    *,
    action: dict[str, Any],
    revision: int,
    reason: str,
) -> EnvelopePartCanvasAction:
    return EnvelopePartCanvasAction(
        action_id=uuid.uuid4().hex,
        action=action,
        canvas_revision=revision,
        applied=False,
        reconciled=False,
        rejected=True,
        reason=reason,
    )


def validate_and_prepare(
    *,
    raw_action: dict[str, Any],
    state: CanvasState,
    incoming_revision: int,
    current_revision: int,
) -> EnvelopePartCanvasAction:
    """Validate a single agent-emitted canvas action.

    On success the returned envelope part carries the *normalized*
    action (with generated IDs filled in) ready for the frontend
    dispatcher. ``applied`` is left ``False`` here — the frontend flips
    it to ``True`` after running the action; the backend marks
    ``reconciled`` if the canvas revision advanced but the action was
    rebased onto the new state.
    """

    if state.ui_context.currentTab != "pipeline":
        return _build_rejection(
            action=raw_action,
            revision=current_revision,
            reason=f"current_tab_is_{state.ui_context.currentTab}_not_pipeline",
        )

    kind = str(raw_action.get("kind", "")).strip()
    if not kind:
        return _build_rejection(action=raw_action, revision=current_revision, reason="missing_kind")

    reconciled = incoming_revision != current_revision

    try:
        if kind == "create_node":
            return _prepare_create_node(raw_action, state, current_revision, reconciled)
        if kind == "delete_node":
            return _prepare_delete_node(raw_action, state, current_revision, reconciled)
        if kind == "update_node":
            return _prepare_update_node(raw_action, state, current_revision, reconciled)
        if kind == "create_edge":
            return _prepare_create_edge(raw_action, state, current_revision, reconciled)
        if kind == "delete_edge":
            return _prepare_delete_edge(raw_action, state, current_revision, reconciled)
        if kind == "update_edge":
            return _prepare_update_edge(raw_action, state, current_revision, reconciled)
        if kind == "set_viewport":
            return _prepare_set_viewport(raw_action, current_revision, reconciled)
    except Exception as exc:  # pragma: no cover — defensive
        return _build_rejection(
            action=raw_action,
            revision=current_revision,
            reason=f"validation_exception:{exc}",
        )

    return _build_rejection(action=raw_action, revision=current_revision, reason="unknown_kind")


def _prepare_create_node(
    raw: dict[str, Any],
    state: CanvasState,
    revision: int,
    reconciled: bool,
) -> EnvelopePartCanvasAction:
    parsed = CanvasActionCreateNode.model_validate(raw)
    if parsed.node_type not in _ALLOWED_NODE_TYPES:
        return _build_rejection(
            action=raw,
            revision=revision,
            reason=f"unsupported_node_type:{parsed.node_type}",
        )
    if parsed.node_id and parsed.node_id in state.node_ids:
        return _build_rejection(
            action=raw,
            revision=revision,
            reason=f"node_id_collision:{parsed.node_id}",
        )
    if parsed.parentId and parsed.parentId not in state.node_ids:
        return _build_rejection(
            action=raw,
            revision=revision,
            reason=f"unknown_parent:{parsed.parentId}",
        )
    ref_err = _validate_data_references(data=parsed.data, state=state)
    if ref_err:
        return _build_rejection(action=raw, revision=revision, reason=ref_err)

    node_id = parsed.node_id or _stable_node_id(parsed.node_type)
    state.register_node(node_id, parsed.node_type, parsed.parentId)

    normalized = parsed.model_dump()
    normalized["node_id"] = node_id
    if parsed.position is None:
        normalized["position"] = {"x": 80.0, "y": 80.0}
    return EnvelopePartCanvasAction(
        action_id=uuid.uuid4().hex,
        action=normalized,
        canvas_revision=revision,
        reconciled=reconciled,
    )


def _prepare_delete_node(
    raw: dict[str, Any],
    state: CanvasState,
    revision: int,
    reconciled: bool,
) -> EnvelopePartCanvasAction:
    parsed = CanvasActionDeleteNode.model_validate(raw)
    if parsed.node_id not in state.node_ids:
        return _build_rejection(
            action=raw,
            revision=revision,
            reason=f"unknown_node:{parsed.node_id}",
        )
    state.drop_node(parsed.node_id)
    return EnvelopePartCanvasAction(
        action_id=uuid.uuid4().hex,
        action=parsed.model_dump(),
        canvas_revision=revision,
        reconciled=reconciled,
    )


def _prepare_update_node(
    raw: dict[str, Any],
    state: CanvasState,
    revision: int,
    reconciled: bool,
) -> EnvelopePartCanvasAction:
    parsed = CanvasActionUpdateNode.model_validate(raw)
    if parsed.node_id not in state.node_ids:
        return _build_rejection(
            action=raw,
            revision=revision,
            reason=f"unknown_node:{parsed.node_id}",
        )
    ref_err = _validate_data_references(data=parsed.data, state=state)
    if ref_err:
        return _build_rejection(action=raw, revision=revision, reason=ref_err)
    return EnvelopePartCanvasAction(
        action_id=uuid.uuid4().hex,
        action=parsed.model_dump(),
        canvas_revision=revision,
        reconciled=reconciled,
    )


def _prepare_create_edge(
    raw: dict[str, Any],
    state: CanvasState,
    revision: int,
    reconciled: bool,
) -> EnvelopePartCanvasAction:
    parsed = CanvasActionCreateEdge.model_validate(raw)
    if parsed.source not in state.node_ids:
        return _build_rejection(
            action=raw,
            revision=revision,
            reason=f"unknown_source:{parsed.source}",
        )
    if parsed.target not in state.node_ids:
        return _build_rejection(
            action=raw,
            revision=revision,
            reason=f"unknown_target:{parsed.target}",
        )
    if parsed.source == parsed.target:
        return _build_rejection(action=raw, revision=revision, reason="self_loop")
    edge_id = parsed.edge_id or f"edge-{uuid.uuid4().hex[:8]}"
    if edge_id in state.edge_ids:
        return _build_rejection(
            action=raw,
            revision=revision,
            reason=f"edge_id_collision:{edge_id}",
        )
    state.register_edge(edge_id)
    normalized = parsed.model_dump()
    normalized["edge_id"] = edge_id
    return EnvelopePartCanvasAction(
        action_id=uuid.uuid4().hex,
        action=normalized,
        canvas_revision=revision,
        reconciled=reconciled,
    )


def _prepare_delete_edge(
    raw: dict[str, Any],
    state: CanvasState,
    revision: int,
    reconciled: bool,
) -> EnvelopePartCanvasAction:
    parsed = CanvasActionDeleteEdge.model_validate(raw)
    if parsed.edge_id not in state.edge_ids:
        return _build_rejection(
            action=raw,
            revision=revision,
            reason=f"unknown_edge:{parsed.edge_id}",
        )
    state.drop_edge(parsed.edge_id)
    return EnvelopePartCanvasAction(
        action_id=uuid.uuid4().hex,
        action=parsed.model_dump(),
        canvas_revision=revision,
        reconciled=reconciled,
    )


def _prepare_update_edge(
    raw: dict[str, Any],
    state: CanvasState,
    revision: int,
    reconciled: bool,
) -> EnvelopePartCanvasAction:
    parsed = CanvasActionUpdateEdge.model_validate(raw)
    if parsed.edge_id not in state.edge_ids:
        return _build_rejection(
            action=raw,
            revision=revision,
            reason=f"unknown_edge:{parsed.edge_id}",
        )
    return EnvelopePartCanvasAction(
        action_id=uuid.uuid4().hex,
        action=parsed.model_dump(),
        canvas_revision=revision,
        reconciled=reconciled,
    )


def _prepare_set_viewport(
    raw: dict[str, Any],
    revision: int,
    reconciled: bool,
) -> EnvelopePartCanvasAction:
    parsed = CanvasActionSetViewport.model_validate(raw)
    return EnvelopePartCanvasAction(
        action_id=uuid.uuid4().hex,
        action=parsed.model_dump(),
        canvas_revision=revision,
        reconciled=reconciled,
    )


# ---------------------------------------------------------------------------
# Undo: derive a reverse action from an audit_log record
# ---------------------------------------------------------------------------


def reverse_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Translate an audit_log record into the actions that undo it.

    The returned actions use the same payload shape as agent-emitted
    canvas actions and can be dispatched by the frontend's
    ``canvasDispatcher`` directly. ``manual_batch`` rows expand into a
    single ``replace_canvas`` action carrying the pre-batch snapshot
    (a dispatcher-only kind not exposed to the LLM).

    Returns an empty list when the record is malformed or there is
    nothing meaningful to reverse (e.g. ``set_viewport``, which we do
    not snapshot today).
    """

    kind = str(record.get("action_kind") or "")
    action = record.get("action") or {}
    snap_before = record.get("snapshot_before") or {}
    snap_after = record.get("snapshot_after") or {}

    if kind == "create_node":
        node_id = (
            action.get("node_id")
            or (snap_after.get("node") or {}).get("id")
        )
        if not node_id:
            return []
        return [{"kind": "delete_node", "node_id": node_id}]

    if kind == "delete_node":
        node = snap_before.get("node") or {}
        if not node.get("id"):
            return []
        return [
            {
                "kind": "create_node",
                "node_id": node.get("id"),
                "node_type": node.get("type") or "agent",
                "position": node.get("position") or {"x": 80.0, "y": 80.0},
                "data": node.get("data") or {},
                "parentId": node.get("parentId"),
            }
        ]

    if kind == "update_node":
        prior = snap_before.get("node") or {}
        if not prior.get("id"):
            return []
        return [
            {
                "kind": "update_node",
                "node_id": prior.get("id"),
                "data": prior.get("data") or {},
                "position": prior.get("position"),
            }
        ]

    if kind == "create_edge":
        edge_id = (
            action.get("edge_id")
            or (snap_after.get("edge") or {}).get("id")
        )
        if not edge_id:
            return []
        return [{"kind": "delete_edge", "edge_id": edge_id}]

    if kind == "delete_edge":
        edge = snap_before.get("edge") or {}
        if not edge.get("id"):
            return []
        return [
            {
                "kind": "create_edge",
                "edge_id": edge.get("id"),
                "source": edge.get("source"),
                "target": edge.get("target"),
                "sourceHandle": edge.get("sourceHandle"),
                "targetHandle": edge.get("targetHandle"),
                "data": edge.get("data") or {},
            }
        ]

    if kind == "update_edge":
        prior = snap_before.get("edge") or {}
        if not prior.get("id"):
            return []
        return [
            {
                "kind": "update_edge",
                "edge_id": prior.get("id"),
                "data": prior.get("data") or {},
            }
        ]

    if kind == "manual_batch":
        nodes = snap_before.get("nodes")
        edges = snap_before.get("edges")
        if not isinstance(nodes, list) and not isinstance(edges, list):
            return []
        return [
            {
                "kind": "replace_canvas",
                "nodes": list(nodes or []),
                "edges": list(edges or []),
            }
        ]

    # set_viewport — no prior viewport recorded in audit_log today.
    return []
