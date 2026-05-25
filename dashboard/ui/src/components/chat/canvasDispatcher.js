// Translates validated CanvasAction payloads from the chat backend
// into ReactFlow state updates (setNodes / setEdges / setViewport).
//
// The chat backend has already validated the payload against the
// current ui_context, so this layer is intentionally thin: it only
// applies the change and surfaces any leftover safety check needed by
// React (e.g. avoiding a duplicate ID at the last possible moment).
//
// Returns true if the action was applied, false if it was skipped.

import { MarkerType } from "reactflow";

const DEFAULT_NODE_POSITION = { x: 80, y: 80 };

const TOOL_EDGE_STYLE = {
  animated: false,
  markerEnd: { type: MarkerType.ArrowClosed, color: "#4b5563" },
  style: { stroke: "#4b5563", strokeWidth: 1.2 },
  selectable: false,
};

const FLOW_EDGE_STYLE = {
  animated: true,
  markerEnd: { type: MarkerType.ArrowClosed, color: "#58a6ff" },
  style: { stroke: "#58a6ff", strokeWidth: 1.6 },
};

export function makeCanvasDispatcher({ setNodes, setEdges, setViewport }) {
  return function dispatch(action) {
    if (!action || typeof action !== "object") return false;
    const kind = action.kind;

    switch (kind) {
      case "create_node":
        return applyCreateNode(action, { setNodes });
      case "delete_node":
        return applyDeleteNode(action, { setNodes, setEdges });
      case "update_node":
        return applyUpdateNode(action, { setNodes });
      case "create_edge":
        return applyCreateEdge(action, { setEdges });
      case "delete_edge":
        return applyDeleteEdge(action, { setEdges });
      case "update_edge":
        return applyUpdateEdge(action, { setEdges });
      case "set_viewport":
        return applySetViewport(action, { setViewport });
      case "replace_canvas":
        return applyReplaceCanvas(action, { setNodes, setEdges });
      default:
        return false;
    }
  };
}

// Dispatcher-only kind used by the undo flow to atomically restore a
// previously-recorded canvas snapshot. Never emitted by the LLM.
function applyReplaceCanvas(action, { setNodes, setEdges }) {
  const nodes = Array.isArray(action.nodes) ? action.nodes : null;
  const edges = Array.isArray(action.edges) ? action.edges : null;
  if (!nodes && !edges) return false;
  if (nodes) {
    setNodes(() =>
      nodes.map((n) => {
        const base = {
          id: n.id,
          type: n.type || "agent",
          position: n.position || DEFAULT_NODE_POSITION,
          data: n.data || defaultDataForType(n.type || "agent"),
        };
        if (n.parentId) base.parentId = n.parentId;
        if (n.width) base.width = n.width;
        if (n.height) base.height = n.height;
        return base;
      }),
    );
  }
  if (edges) {
    setEdges(() =>
      edges.map((e) => {
        const style = e.data?.tool ? TOOL_EDGE_STYLE : FLOW_EDGE_STYLE;
        const base = {
          id: e.id,
          source: e.source,
          target: e.target,
          sourceHandle: e.sourceHandle || undefined,
          targetHandle: e.targetHandle || undefined,
          data: e.data || {},
        };
        return { ...style, ...base };
      }),
    );
  }
  return true;
}

function applyCreateNode(action, { setNodes }) {
  const nodeType = action.node_type || "agent";
  const data = action.data || defaultDataForType(nodeType);
  const position = action.position || DEFAULT_NODE_POSITION;

  let didApply = false;
  setNodes((current) => {
    const desiredId = action.node_id || `${nodeType}-${Date.now()}-${current.length + 1}`;
    if (current.some((n) => n.id === desiredId)) return current;
    const node = {
      id: desiredId,
      type: nodeType,
      position,
      data: { ...data },
    };
    if (action.parentId) node.parentId = action.parentId;
    didApply = true;
    return [...current, node];
  });
  return didApply;
}

function applyDeleteNode(action, { setNodes, setEdges }) {
  const target = action.node_id;
  if (!target) return false;
  let removedAny = false;
  setNodes((current) => {
    const childIds = current.filter((n) => n.parentId === target).map((n) => n.id);
    const deletion = new Set([target, ...childIds]);
    const next = current.filter((n) => !deletion.has(n.id));
    if (next.length !== current.length) removedAny = true;
    return next;
  });
  if (removedAny) {
    setEdges((current) =>
      current.filter((e) => e.source !== target && e.target !== target)
    );
  }
  return removedAny;
}

function applyUpdateNode(action, { setNodes }) {
  const target = action.node_id;
  if (!target) return false;
  let changed = false;
  setNodes((current) =>
    current.map((node) => {
      if (node.id !== target) return node;
      changed = true;
      const merged = { ...node };
      if (action.position) merged.position = action.position;
      if (action.data && typeof action.data === "object") {
        merged.data = { ...(node.data || {}), ...action.data };
      }
      return merged;
    })
  );
  return changed;
}

function applyCreateEdge(action, { setEdges }) {
  const source = action.source;
  const target = action.target;
  if (!source || !target) return false;
  const edgeId = action.edge_id || `edge-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  let didApply = false;
  setEdges((current) => {
    if (current.some((e) => e.id === edgeId)) return current;
    const base = {
      id: edgeId,
      source,
      target,
      sourceHandle: action.sourceHandle || undefined,
      targetHandle: action.targetHandle || undefined,
    };
    const style = action.data?.tool ? TOOL_EDGE_STYLE : FLOW_EDGE_STYLE;
    didApply = true;
    return [...current, { ...style, ...base, data: action.data || {} }];
  });
  return didApply;
}

function applyDeleteEdge(action, { setEdges }) {
  const target = action.edge_id;
  if (!target) return false;
  let removed = false;
  setEdges((current) => {
    const next = current.filter((e) => e.id !== target);
    if (next.length !== current.length) removed = true;
    return next;
  });
  return removed;
}

function applyUpdateEdge(action, { setEdges }) {
  const target = action.edge_id;
  if (!target) return false;
  let changed = false;
  setEdges((current) =>
    current.map((edge) => {
      if (edge.id !== target) return edge;
      changed = true;
      const merged = { ...edge };
      if (action.data && typeof action.data === "object") {
        merged.data = { ...(edge.data || {}), ...action.data };
      }
      return merged;
    })
  );
  return changed;
}

function applySetViewport(action, { setViewport }) {
  const viewport = action.viewport;
  if (!viewport) return false;
  setViewport({
    x: Number(viewport.x) || 0,
    y: Number(viewport.y) || 0,
    zoom: Number(viewport.zoom) || 1,
  });
  return true;
}

function defaultDataForType(nodeType) {
  if (nodeType === "patterns") {
    return {
      patternFile: "",
      resultFile: "",
      promptFile: "",
      provider: "",
      model: "",
      scopeMode: "contract",
      contractPaths: [],
      cluster: "",
      clusterFiles: [],
    };
  }
  if (nodeType === "memory") {
    return {
      memoryFile: "",
      memoryPrompt: "",
      provider: "",
      model: "",
      creatingMemory: false,
      newMemoryName: "",
    };
  }
  if (nodeType === "code") return { code: "" };
  if (nodeType === "tool") {
    return { mcp: "", mcpDraft: "", showMcpPicker: false, parentAgentId: "" };
  }
  return {
    agentType: "audit",
    provider: "",
    model: "",
    skill: "",
    leadSkill: "",
    memoryFileToUse: "",
    contractPaths: [],
    cluster: "",
    addRelevantDocs: false,
  };
}
