import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import MonacoPane from "./components/editor/MonacoPane.jsx";
import { ChatPanel } from "./components/chat/ChatPanel.jsx";
import { AuditLogDrawer } from "./components/chat/AuditLogDrawer.jsx";
import { CanvasUndoDrawer } from "./components/chat/CanvasUndoDrawer.jsx";
import { makeCanvasDispatcher } from "./components/chat/canvasDispatcher.js";
import ReactFlow, {
  Background,
  Controls,
  Handle,
  MiniMap,
  MarkerType,
  Position,
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
} from "reactflow";

const AGENT_TYPES = {
  preparation: { label: "Preparation Agent", icon: "PR", color: "#38bdf8" },
  audit: { label: "Audit Agent", icon: "AU", color: "#f97316" },
  verification: { label: "Verification Agent", icon: "VR", color: "#22c55e" },
  report: { label: "Report Agent", icon: "RP", color: "#a78bfa" },
  test: { label: "Test Agent", icon: "TS", color: "#f43f5e" },
};

const BLOCK_META = {
  agent: { label: "Agent", icon: "AG", color: "#58a6ff" },
  patterns: { label: "Patterns", icon: "PT", color: "#ff7b72" },
  memory: { label: "Memory", icon: "ME", color: "#d29922" },
  code: { label: "Code", icon: "PY", color: "#3fb950" },
};

const EMPTY_CATALOGS = {
  skills: { path: "", entries: [] },
  lead_skills: { path: "", entries: [] },
  mcp: { path: "", entries: [] },
  audit_docs: { path: "", entries: [] },
  patterns: { path: "", entries: [] },
  memory: { path: "", entries: [] },
  memory_promts: { path: "", entries: [] },
};

const RESOURCE_CATALOG_META = [
  { key: "skills", label: "Skills" },
  { key: "lead_skills", label: "Lead Skills" },
  { key: "mcp", label: "MCP" },
  { key: "audit_docs", label: "Audit Docs" },
  { key: "patterns", label: "Patterns" },
  { key: "memory", label: "Memory" },
  { key: "memory_promts", label: "Memory Prompts" },
];

const DEFAULT_AUDIT_MODE = "contract";
const DEFAULT_CLUSTER_OPTIONS = Array.from({ length: 20 }, (_, index) => `cluster-${String(index + 1).padStart(2, "0")}`);
const INFO_SELECT_TITLE = "Browse options (informational only - does not change the saved pipeline).";
const DEFAULT_DOCS_STATUS = {
  docs_path: "",
  md_file_count: 0,
  prepared: false,
  chunk_count: 0,
  model: "all-MiniLM-L6-v2",
  chunk_size: 700,
  chunk_overlap: 150,
  processed_at: "",
};

const CHAT_PANEL_WIDTH_KEY = "vulnflow-chat-panel-width";
const CHAT_PANEL_WIDTH_DEFAULT = 400;
const CHAT_PANEL_WIDTH_MIN = 280;
const CHAT_PANEL_WIDTH_MAX = 720;

function readChatPanelWidth() {
  try {
    const raw = window.localStorage.getItem(CHAT_PANEL_WIDTH_KEY);
    const parsed = Number(raw);
    if (Number.isFinite(parsed)) {
      return Math.min(CHAT_PANEL_WIDTH_MAX, Math.max(CHAT_PANEL_WIDTH_MIN, Math.round(parsed)));
    }
  } catch {
    /* ignore */
  }
  return CHAT_PANEL_WIDTH_DEFAULT;
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

function TrashIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M9 3h6l1 2h4v2H4V5h4l1-2zm1 7h2v7h-2v-7zm4 0h2v7h-2v-7zM7 10h2v7H7v-7zm1 10h8a2 2 0 0 0 2-2V8H6v10a2 2 0 0 0 2 2z"
        fill="currentColor"
      />
    </svg>
  );
}

function ViewListIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="cluster-card__glyph">
      <path
        d="M4 6h16v2H4V6zm0 5h16v2H4v-2zm0 5h16v2H4v-2z"
        fill="currentColor"
      />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M11 5h2v6h6v2h-6v6h-2v-6H5v-2h6z" fill="currentColor" />
    </svg>
  );
}

function RefreshIcon({ className = "" }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className={className}>
      <path
        d="M12 6V3L8 7l4 4V8c2.76 0 5 2.24 5 5a5 5 0 0 1-8.66 3.54l-1.42 1.42A7 7 0 1 0 12 6z"
        fill="currentColor"
      />
    </svg>
  );
}

function SectionRefreshButton({ onClick, loading, label }) {
  const title = loading ? "Refreshing..." : label;
  return (
    <button
      type="button"
      className="icon-button button-secondary section-refresh-button"
      onClick={onClick}
      disabled={loading}
      aria-label={title}
      title={title}
    >
      <RefreshIcon className={loading ? "section-refresh-button__icon section-refresh-button__icon--spinning" : "section-refresh-button__icon"} />
    </button>
  );
}

function flattenFileOptions(node, trail = "") {
  if (!node || node.excluded) {
    return [];
  }
  const nextPath = trail ? `${trail}/${node.name}` : node.name;
  if (node.kind === "file") {
    return [{ value: node.path, label: nextPath }];
  }
  return (node.children || []).flatMap((child) => flattenFileOptions(child, nextPath));
}

function normalizePathForCompare(p) {
  return (p || "").replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

function isPathExcludedByList(filePath, excludedPaths) {
  const norm = normalizePathForCompare(filePath);
  if (!norm) {
    return false;
  }
  for (const ex of excludedPaths || []) {
    const e = normalizePathForCompare(ex);
    if (!e) {
      continue;
    }
    if (norm === e) {
      return true;
    }
    const boundary = `${e}/`;
    if (norm.startsWith(boundary)) {
      return true;
    }
  }
  return false;
}

function pruneAgentsContractPaths(nodes, excludedPaths) {
  if (!excludedPaths?.length) {
    return nodes;
  }
  return nodes.map((node) => {
    const nodeType = node.type || "agent";
    if (nodeType !== "agent" && nodeType !== "patterns") {
      return node;
    }
    const paths = resolveContractPaths(node.data);
    const filtered = paths.filter((p) => !isPathExcludedByList(p, excludedPaths));
    if (filtered.length === paths.length) {
      return node;
    }
    return { ...node, data: { ...node.data, contractPaths: filtered } };
  });
}

function mapCatalogNames(entries) {
  return [...entries.map((entry) => entry.name)].sort((left, right) => left.localeCompare(right));
}

function mapCatalogPaths(entries) {
  return [...entries]
    .map((entry) => ({ value: entry.path, label: entry.name }))
    .sort((left, right) => left.label.localeCompare(right.label));
}

function getOptionLabel(options, value, fallback = "-") {
  return options.find((option) => option.value === value)?.label || value || fallback;
}

function sortClusterFunctionRows(functions) {
  return [...(functions || [])].sort((a, b) => {
    const cmp = (a.rel_path || "").localeCompare(b.rel_path || "");
    if (cmp !== 0) {
      return cmp;
    }
    return (a.name || "").localeCompare(b.name || "");
  });
}

function ClusterSummary({ clusters, stats, onRemoveCluster }) {
  const [popupCluster, setPopupCluster] = useState(null);

  useEffect(() => {
    if (!popupCluster) {
      return undefined;
    }
    const onKey = (event) => {
      if (event.key === "Escape") {
        setPopupCluster(null);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [popupCluster]);

  useEffect(() => {
    if (!popupCluster) {
      return;
    }
    if (!clusters.some((c) => c.cluster_id === popupCluster.cluster_id)) {
      setPopupCluster(null);
    }
  }, [clusters, popupCluster]);

  if (!clusters.length) {
    return <div className="helper-text">No generated clusters yet.</div>;
  }

  const popupRows = popupCluster ? sortClusterFunctionRows(popupCluster.functions) : [];

  return (
    <div className="cluster-summary">
      <div className="cluster-summary__stats">
        <span className="pill">{stats.clusters} clusters</span>
        <span className="pill">{stats.solFiles} Solidity files</span>
        <span className="pill">{stats.functions} functions</span>
        <span className="pill">{stats.edges} call edges</span>
      </div>
      <div className="cluster-summary__list">
        {clusters.map((cluster) => (
          <div className="cluster-card" key={cluster.cluster_id}>
            <div className="cluster-card__toolbar">
              <button
                type="button"
                className="cluster-card__icon-btn cluster-card__icon-btn--view"
                title="View functions"
                aria-label={`View functions in ${formatClusterLabel(cluster.cluster_id)}`}
                onClick={() => setPopupCluster(cluster)}
              >
                <ViewListIcon />
              </button>
              <button
                type="button"
                className="cluster-card__icon-btn cluster-card__icon-btn--danger"
                title="Remove cluster"
                aria-label={`Remove ${formatClusterLabel(cluster.cluster_id)}`}
                onClick={() => onRemoveCluster(cluster.cluster_id)}
              >
                <TrashIcon />
              </button>
            </div>
            <div className="cluster-card__header">
              <strong>{formatClusterLabel(cluster.cluster_id)}</strong>
              <span className="cluster-card__rank">rank {Number(cluster.rank || 0).toFixed(4)}</span>
            </div>
            <div className="cluster-card__meta">
              <span>{cluster.rel_files?.length || 0} files</span>
              <span>{cluster.functions?.length || 0} functions</span>
              <span>{cluster.dependencies?.length || 0} deps</span>
            </div>
            <div className="cluster-card__files">
              {(cluster.rel_files || []).slice(0, 4).map((file) => <div key={file}>{file}</div>)}
              {(cluster.rel_files || []).length > 4 ? <div>+ {(cluster.rel_files || []).length - 4} more</div> : null}
            </div>
          </div>
        ))}
      </div>
      {popupCluster ? (
        <div
          className="cluster-fn-popup-overlay"
          role="presentation"
          onClick={() => setPopupCluster(null)}
        >
          <div
            className="cluster-fn-popup"
            role="dialog"
            aria-modal="true"
            aria-labelledby="cluster-fn-popup-title"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="cluster-fn-popup__header">
              <h3 id="cluster-fn-popup-title">{formatClusterLabel(popupCluster.cluster_id)} — functions</h3>
              <button type="button" className="cluster-fn-popup__close" onClick={() => setPopupCluster(null)} aria-label="Close">
                ×
              </button>
            </div>
            <div className="cluster-fn-popup__body">
              {popupRows.length ? (
                <div className="cluster-fn-list">
                  {popupRows.map((row) => {
                    const src = typeof row.source === "string" ? row.source.trim() : "";
                    const key = row.qualified_id || `${row.rel_path}-${row.name}-${row.line}`;
                    return (
                      <article key={key} className="cluster-fn-block">
                        <div className="cluster-fn-block__meta">
                          <div className="cluster-fn-block__title">
                            {(row.contract || "?") + "." + (row.name || "?")}
                          </div>
                          <div className="cluster-fn-block__file">{row.rel_path || "—"}</div>
                        </div>
                        <pre className="cluster-fn-block__source">
                          <code>{src || "Source not available for this function."}</code>
                        </pre>
                      </article>
                    );
                  })}
                </div>
              ) : (
                <div className="helper-text">No functions listed for this cluster.</div>
              )}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function formatClusterLabel(clusterId) {
  if (!clusterId) {
    return "-";
  }
  return clusterId.replace(/^cluster-/, "Cluster ");
}

function resolveContractPaths(data) {
  const fromArray = Array.isArray(data.contractPaths) ? data.contractPaths.filter(Boolean) : [];
  if (fromArray.length) {
    return fromArray;
  }
  if (typeof data.contractPath === "string" && data.contractPath) {
    return [data.contractPath];
  }
  return [];
}

function resolveClusterFiles(data) {
  return Array.isArray(data.clusterFiles) ? data.clusterFiles.filter(Boolean) : [];
}

function getClusterFilesForSelection(clusterRecordsById, clusterId) {
  const entry = clusterRecordsById?.[clusterId];
  return Array.isArray(entry?.files) ? entry.files.filter(Boolean) : [];
}

function normalizeAgentLoadData(data) {
  if (!data || typeof data !== "object") {
    return { contractPaths: [], cluster: "", clusterFiles: [], memoryFileToUse: "", addRelevantDocs: false };
  }
  const contractPaths = resolveContractPaths(data);
  const { contractMode: _cm, contractPath: _cp, ...rest } = data;
  return {
    ...rest,
    contractPaths,
    cluster: data.cluster || "",
    clusterFiles: resolveClusterFiles(data),
    memoryFileToUse: data.memoryFileToUse || "",
    addRelevantDocs: Boolean(data.addRelevantDocs),
  };
}

function normalizePatternsLoadData(data) {
  if (!data || typeof data !== "object") {
    return {
      patternFile: "",
      resultFile: "",
      promptFile: "",
      provider: "",
      model: "",
      scopeMode: DEFAULT_AUDIT_MODE,
      contractPaths: [],
      cluster: "",
      clusterFiles: [],
    };
  }
  return {
    ...data,
    patternFile: data.patternFile || "",
    resultFile: data.resultFile || "",
    promptFile: data.promptFile || "",
    provider: data.provider || "",
    model: data.model || "",
    scopeMode: data.scopeMode || DEFAULT_AUDIT_MODE,
    contractPaths: resolveContractPaths(data),
    cluster: data.cluster || "",
    clusterFiles: resolveClusterFiles(data),
  };
}

function normalizePipelineNodes(nodes) {
  return nodes.map((node) => {
    if (node.type === "tool") {
      const { draggable, ...rest } = node;
      return rest;
    }
    if ((node.type || "agent") === "agent") {
      return { ...node, data: normalizeAgentLoadData(node.data || {}) };
    }
    if (node.type === "patterns") {
      return { ...node, data: normalizePatternsLoadData(node.data || {}) };
    }
    return node;
  });
}

function getNodeSummary(data, contractOptions) {
  const mode = data.auditMode ?? DEFAULT_AUDIT_MODE;
  if (mode === "cluster" && data.cluster) {
    return formatClusterLabel(data.cluster);
  }
  const paths = resolveContractPaths(data);
  if (mode === "contract" && paths.length) {
    if (paths.length === 1) {
      return getOptionLabel(contractOptions, paths[0], paths[0]);
    }
    return `${paths.length} contracts`;
  }
  if (data.mcp) {
    return getOptionLabel(data.mcpEntryOptions || [], data.mcp, data.mcp);
  }
  if (data.leadSkill) {
    return data.leadSkill;
  }
  if (data.skill) {
    return data.skill;
  }
  return "Awaiting setup";
}

function buildAuditNodeIndex(nodes, contractOptions) {
  return Object.fromEntries(nodes.map((node) => {
    const nodeType = node.type || "agent";
    if (nodeType === "patterns") {
      return [node.id, { kind: "patterns", title: "Patterns Block", subtitle: node.data?.patternFile || "Pattern file not selected" }];
    }
    if (nodeType === "memory") {
      return [node.id, { kind: "memory", title: "Memory Block", subtitle: node.data?.memoryFile || "Memory file not selected" }];
    }
    if (nodeType === "code") {
      return [node.id, { kind: "code", title: "Code Block", subtitle: node.data?.code?.trim() ? "Python code added" : "No code" }];
    }
    if (nodeType === "tool") {
      return [node.id, { kind: "tool", title: "Tool", subtitle: node.data?.mcp || "No MCP selected" }];
    }
    const meta = AGENT_TYPES[node.data?.agentType] || AGENT_TYPES.audit;
    return [node.id, { kind: "agent", title: meta.label, subtitle: getNodeSummary(node.data || {}, contractOptions) }];
  }));
}

function getAuditBlockName(blockId, nodeIndex) {
  const entry = nodeIndex[blockId];
  if (!entry) {
    return blockId ? `Block ${blockId.slice(0, 8)}` : "Unknown block";
  }
  return entry.subtitle && entry.subtitle !== "-" ? `${entry.title} (${entry.subtitle})` : entry.title;
}

function formatAuditLogEvent(evt, nodeIndex) {
  const timestamp = evt.timestamp ? new Date(evt.timestamp).toLocaleTimeString() : "";
  const blockName = evt.block_id ? getAuditBlockName(evt.block_id, nodeIndex) : "";
  const nextBlockName = evt.to_block_id ? getAuditBlockName(evt.to_block_id, nodeIndex) : "";
  const nextBranchName = evt.target_block ? getAuditBlockName(evt.target_block, nodeIndex) : "";
  const errorMessage = evt.error?.message || evt.message || "";

  switch (evt.type) {
    case "pipeline_start":
      return { time: timestamp, badge: "Pipeline", tone: "running", detail: "Current canvas execution started." };
    case "pipeline_end":
      return { time: timestamp, badge: "Pipeline", tone: evt.status === "completed" ? "success" : evt.status === "stopped" ? "warn" : "error", detail: `Execution finished with status: ${evt.status}.` };
    case "pipeline_error":
      return { time: timestamp, badge: "Pipeline", tone: "error", detail: errorMessage || "Pipeline execution error." };
    case "block_start":
      return { time: timestamp, badge: "Block", tone: "running", detail: `${blockName} started.` };
    case "block_end":
      return {
        time: timestamp,
        badge: "Block",
        tone: evt.status === "success" ? "success" : evt.status === "skipped" ? "warn" : evt.status === "error" ? "error" : "info",
        detail: `${blockName} finished with status: ${evt.status}.${errorMessage ? ` ${errorMessage}` : ""}`,
      };
    case "agent_run_start":
      return { time: timestamp, badge: "Agent", tone: "running", detail: `${blockName} is now active.` };
    case "agent_mcp_call":
      return { time: timestamp, badge: "MCP", tone: "info", detail: `${blockName} requested MCP call${evt.tool_name ? `: ${evt.tool_name}` : ""}.` };
    case "agent_mcp_status":
      return {
        time: timestamp,
        badge: "MCP",
        tone: evt.stage === "data_received" ? "success" : evt.stage === "error" ? "error" : "info",
        detail:
          evt.stage === "request_sent"
            ? `${blockName} sent MCP request${evt.tool_name ? ` to ${evt.tool_name}` : ""}.`
            : evt.stage === "data_received"
              ? `${blockName} received MCP response${evt.tool_name ? ` from ${evt.tool_name}` : ""}.`
              : `${blockName} MCP request failed${errorMessage ? `: ${errorMessage}` : "."}`,
      };
    case "agent_docs_request":
      return { time: timestamp, badge: "Docs", tone: "info", detail: `${blockName} requested documentation lookup.` };
    case "agent_docs_status":
      return {
        time: timestamp,
        badge: "Docs",
        tone: evt.stage === "data_received" ? "success" : evt.stage === "error" ? "error" : "info",
        detail:
          evt.stage === "request_sent"
            ? `${blockName} sent documentation request to the model.`
            : evt.stage === "data_received"
              ? `${blockName} received documentation status (${evt.docs_count || 0} matches).`
              : `${blockName} documentation request failed${errorMessage ? `: ${errorMessage}` : "."}`,
      };
    case "block_transition":
      return {
        time: timestamp,
        badge: "Flow",
        tone: "info",
        detail: evt.status === "fork" ? `Forked execution to ${nextBlockName}.` : `Transitioning to ${nextBlockName}.`,
      };
    case "branch_fork":
      return { time: timestamp, badge: "Branch", tone: "info", detail: `Created branch to ${nextBranchName}.` };
    case "memory_status":
      return {
        time: timestamp,
        badge: "Memory",
        tone: evt.stage === "error" ? "error" : evt.stage === "vector_written" || evt.stage === "file_written" || evt.stage === "model_data_received" ? "success" : "info",
        detail:
          evt.stage === "model_request_sent"
            ? `${blockName} sent request to the model.`
            : evt.stage === "model_data_received"
              ? `${blockName} received model response.`
              : evt.stage === "file_write_started"
                ? `${blockName} is writing data to file.`
                : evt.stage === "file_written"
                  ? `${blockName} wrote data to file.`
                  : evt.stage === "vector_write_started"
                    ? `${blockName} is writing data to vectors.`
                    : evt.stage === "vector_written"
                      ? `${blockName} wrote data to vectors.`
                      : `${blockName} failed${errorMessage ? `: ${errorMessage}` : "."}`,
      };
    case "patterns_status": {
      const batchInfo = evt.batch_index && evt.batch_total ? ` (batch ${evt.batch_index}/${evt.batch_total})` : "";
      return {
        time: timestamp,
        badge: "Patterns",
        tone: evt.stage === "error" ? "error" : evt.stage === "vector_written" || evt.stage === "file_written" || evt.stage === "model_data_received" || evt.stage === "completed" ? "success" : "info",
        detail:
          evt.stage === "batch_started"
            ? `${blockName} started processing${batchInfo}.`
            : evt.stage === "model_request_sent"
              ? `${blockName} sent request to the model${batchInfo}.`
              : evt.stage === "model_data_received"
                ? `${blockName} received model response${batchInfo}.`
                : evt.stage === "file_write_started"
                  ? `${blockName} is writing data to file${batchInfo}.`
                  : evt.stage === "file_written"
                    ? `${blockName} wrote data to file${batchInfo}.`
                    : evt.stage === "vector_write_started"
                      ? `${blockName} is writing data to vectors${batchInfo}.`
                      : evt.stage === "vector_written"
                        ? `${blockName} wrote data to vectors${batchInfo}.`
                        : evt.stage === "completed"
                          ? `${blockName} completed all batches (${evt.batch_total || 0} total).`
                          : `${blockName} failed${errorMessage ? `: ${errorMessage}` : "."}`,
      };
    }
    case "code_status":
      return {
        time: timestamp,
        badge: "Code",
        tone: evt.stage === "success" ? "success" : "error",
        detail: evt.stage === "success" ? `${blockName} completed successfully.` : `${blockName} failed${errorMessage ? `: ${errorMessage}` : "."}`,
      };
    default:
      return null;
  }
}

function BlockStatusBadge({ status }) {
  if (!status) return null;
  const labels = { running: "Running", success: "Done", error: "Error", skipped: "Skipped", pending: "Pending" };
  return <div className={`block-status-badge block-status-badge--${status}`}>{labels[status] || status}</div>;
}

function NodeFrame({ selected, nodeType, children, accentColor, blockStatus }) {
  const color = accentColor ?? BLOCK_META[nodeType]?.color ?? "#58a6ff";
  return (
    <div className={`canvas-node canvas-node--${nodeType} ${selected ? "selected" : ""} ${blockStatus ? `canvas-node--status-${blockStatus}` : ""}`} style={{ "--node-color": color }}>
      <Handle type="target" position={Position.Left} className="agent-node__handle" />
      <BlockStatusBadge status={blockStatus} />
      {children}
      <Handle type="source" position={Position.Right} className="agent-node__handle" />
    </div>
  );
}

function NodeHeader({ icon, title, subtitle }) {
  return (
    <div className="agent-node__header">
      <div className="agent-node__icon">{icon}</div>
      <div>
        <div className="agent-node__type">{title}</div>
        <div className="agent-node__name">{subtitle}</div>
      </div>
    </div>
  );
}

function AgentEstimateBadges({ data }) {
  const minCalls = Number.isFinite(data.minCallsEstimate) ? data.minCallsEstimate : "-";
  const tokensApprox = data.estimateLoading
    ? "..."
    : Number.isFinite(data.tokensApproxEstimate)
      ? data.tokensApproxEstimate.toLocaleString()
      : "-";
  return (
    <div className="agent-node__estimate-row">
      <div className="agent-node__estimate-badge" title="Minimum required LLM calls for this agent configuration.">
        {`Min calls: ${minCalls}`}
      </div>
      <div className="agent-node__estimate-badge" title="Approximate input tokens for the minimum execution path.">
        {`Tokens approx: ${tokensApprox}`}
      </div>
    </div>
  );
}

function NodeActions({ onRemove, title }) {
  return (
    <div className="canvas-node__actions">
      <button type="button" className="agent-node__remove nodrag" onClick={onRemove} aria-label={title}>
        <TrashIcon />
      </button>
    </div>
  );
}
function AgentNode({ id, data, selected }) {
  const [contractsOpen, setContractsOpen] = useState(false);
  const meta = AGENT_TYPES[data.agentType] || AGENT_TYPES.audit;
  const providerLabel = data.providers?.find((provider) => provider.id === data.provider)?.label || data.provider || "-";
  const memorySummary = data.memoryFileToUse ? getOptionLabel(data.memoryOptions || [], data.memoryFileToUse, "Memory file") : "-";
  const mode = data.auditMode ?? DEFAULT_AUDIT_MODE;
  const paths = resolveContractPaths(data);
  const contractSummary =
    mode === "cluster" && data.cluster
      ? formatClusterLabel(data.cluster)
      : mode === "contract" && paths.length
        ? paths.length === 1
          ? getOptionLabel(data.contractOptions || [], paths[0], paths[0])
          : `${paths.length} contracts`
        : "-";
  const scopeLabel = mode === "cluster" ? "Cluster" : "Contracts";
  const contractTriggerLabel = !data.contractOptions?.length
    ? "No contract files in tree"
    : paths.length === 0
      ? "Select contracts..."
      : paths.length === 1
        ? getOptionLabel(data.contractOptions, paths[0], paths[0])
        : `${paths.length} contracts selected`;

  if (data.readOnly) {
    return (
      <NodeFrame selected={selected} nodeType="agent" accentColor={meta.color} blockStatus={data.blockStatus}>
        <AgentEstimateBadges data={data} />
        <NodeHeader icon={meta.icon} title={meta.label} subtitle={getNodeSummary(data, data.contractOptions || [])} />
        <div className="agent-node__readonly-grid">
          <div className="agent-node__readonly-row"><span>Provider</span><span>{providerLabel}</span></div>
          <div className="agent-node__readonly-row"><span>Model</span><span>{data.model || "-"}</span></div>
          <div className="agent-node__readonly-row"><span>Skill</span><span>{data.skill || data.leadSkill || "-"}</span></div>
          <div className="agent-node__readonly-row"><span>Memory file to use</span><span>{memorySummary}</span></div>
          <div className="agent-node__readonly-row"><span>{scopeLabel}</span><span>{contractSummary}</span></div>
          {data.docsReady || data.addRelevantDocs ? (
            <div className="agent-node__readonly-row">
              <span>Relevant docs</span>
              <span>{data.addRelevantDocs ? "Enabled" : "Disabled"}</span>
            </div>
          ) : null}
        </div>
      </NodeFrame>
    );
  }

  return (
    <NodeFrame selected={selected} nodeType="agent" accentColor={meta.color}>
      <AgentEstimateBadges data={data} />
      <NodeHeader icon={meta.icon} title={meta.label} subtitle={getNodeSummary(data, data.contractOptions)} />
      <label className="agent-node__field">
        <span>Role</span>
        <select className="nodrag" value={data.agentType} onChange={(event) => data.onChange(id, { agentType: event.target.value })}>
          {Object.entries(AGENT_TYPES).map(([agentType, option]) => <option key={agentType} value={agentType}>{option.label}</option>)}
        </select>
      </label>
      <label className="agent-node__field">
        <span>Provider</span>
        <select className="nodrag" value={data.provider} onChange={(event) => data.onChange(id, { provider: event.target.value, model: "" })}>
          <option value="">Select provider</option>
          {data.providers.map((provider) => <option key={provider.id} value={provider.id}>{provider.label}</option>)}
        </select>
      </label>
      <label className="agent-node__field">
        <span>Model</span>
        <select className="nodrag" value={data.model} onChange={(event) => data.onChange(id, { model: event.target.value })}>
          <option value="">Select model</option>
          {data.models.map((model) => <option key={model} value={model}>{model}</option>)}
        </select>
      </label>
      <label className="agent-node__field">
        <span>Skill</span>
        <select className="nodrag" value={data.skill} disabled={Boolean(data.leadSkill)} onChange={(event) => data.onChange(id, { skill: event.target.value, leadSkill: "" })}>
          <option value="">Select skill</option>
          {data.skillOptions.map((skill) => <option key={skill} value={skill}>{skill}</option>)}
        </select>
      </label>
      <div className="agent-node__or">or</div>
      <label className="agent-node__field">
        <span>Lead skill</span>
        <select className="nodrag" value={data.leadSkill} disabled={Boolean(data.skill)} onChange={(event) => data.onChange(id, { leadSkill: event.target.value, skill: "" })}>
          <option value="">Select lead skill</option>
          {data.leadSkillOptions.map((skill) => <option key={skill} value={skill}>{skill}</option>)}
        </select>
      </label>
      <label className="agent-node__field">
        <span>Memory file to use</span>
        <div className="canvas-node__inline-actions">
          <select className="nodrag" value={data.memoryFileToUse || ""} onChange={(event) => data.onChange(id, { memoryFileToUse: event.target.value })}>
            <option value="">Do not use memory file</option>
            {data.memoryOptions.map((entry) => <option key={entry.value} value={entry.value}>{entry.label}</option>)}
          </select>
          <button type="button" className="icon-button nodrag" onClick={data.onRefreshCatalogs} aria-label="Refresh memory files"><RefreshIcon /></button>
        </div>
      </label>
      <div className="agent-node__field agent-node__contract-group">
        <span>{scopeLabel}</span>
        <div className="agent-node__contract-controls agent-node__contract-controls--stack">
          {mode === "cluster" && data.clustersNotReady ? (
            <div className="canvas-node__notice">
              Create clusters in section 4. Audit Mode (Generate clusters), then pick a cluster here.
            </div>
          ) : null}
          {mode === "cluster" ? (
            <select
              className="nodrag"
              value={data.cluster}
              onChange={(event) => data.onChange(id, { cluster: event.target.value })}
              disabled={Boolean(data.clustersNotReady)}
            >
              <option value="">Select cluster</option>
              {data.clusterOptions.map((cluster) => <option key={cluster} value={cluster}>{formatClusterLabel(cluster)}</option>)}
            </select>
          ) : (
            <div className="agent-node__contract-dropdown-wrap nodrag">
              <button
                type="button"
                className={`agent-node__contract-trigger ${contractsOpen ? "agent-node__contract-trigger--open" : ""}`}
                onClick={() => data.contractOptions.length && setContractsOpen((open) => !open)}
                disabled={!data.contractOptions.length}
                aria-expanded={contractsOpen}
                aria-haspopup="listbox"
              >
                <span className="agent-node__contract-trigger-text">{contractTriggerLabel}</span>
                <span className="agent-node__contract-trigger-chevron" aria-hidden>▾</span>
              </button>
              {contractsOpen ? (
                <div className="agent-node__contract-dropdown">
                  <div className="agent-node__contract-picklist" role="listbox" aria-multiselectable="true">
                    {data.contractOptions.map((entry) => {
                      const checked = paths.includes(entry.value);
                      return (
                        <label key={entry.value} className="agent-node__contract-row">
                          <span className="agent-node__contract-row-label">{entry.label}</span>
                          <input
                            type="checkbox"
                            className="nodrag"
                            checked={checked}
                            onChange={() => {
                              const next = new Set(paths);
                              if (checked) {
                                next.delete(entry.value);
                              } else {
                                next.add(entry.value);
                              }
                              data.onChange(id, { contractPaths: [...next] });
                            }}
                          />
                        </label>
                      );
                    })}
                  </div>
                  <div className="agent-node__contract-dropdown-footer">
                    <button type="button" className="button-secondary nodrag" onClick={() => setContractsOpen(false)}>
                      Done
                    </button>
                  </div>
                </div>
              ) : null}
            </div>
          )}
        </div>
      </div>
      {data.docsReady ? (
        <label className="agent-node__field agent-node__checkbox-field">
          <span>Add relevant docs for the request</span>
          <input
            type="checkbox"
            className="nodrag"
            checked={Boolean(data.addRelevantDocs)}
            onChange={(event) => data.onChange(id, { addRelevantDocs: event.target.checked })}
          />
        </label>
      ) : null}
      <NodeActions onRemove={() => data.onRemove(id)} title="Remove agent" />
    </NodeFrame>
  );
}
function ToolNode({ id, data, selected }) {
  const selectedLabel = data.mcp ? getOptionLabel(data.mcpEntryOptions || [], data.mcp, data.mcp) : "";

  if (data.readOnly) {
    return (
      <div className={`tool-node ${selected ? "selected" : ""}`}>
        <Handle type="target" position={Position.Left} className="agent-node__handle" />
        <div className="tool-node__label">Tool</div>
        {selectedLabel ? <div className="tool-node__chip">{selectedLabel}</div> : null}
      </div>
    );
  }

  return (
    <div className={`tool-node ${selected ? "selected" : ""}`}>
      <Handle type="target" position={Position.Left} className="agent-node__handle" />
      <div className="tool-node__label">Tool</div>
      <button
        type="button"
        className="tool-node__plus nodrag"
        onClick={() =>
          data.onChange(id, {
            showMcpPicker: !data.showMcpPicker,
            mcpDraft: data.mcpDraft || data.mcp,
          })
        }
        aria-label="Add MCP tool"
      >
        <PlusIcon />
      </button>
      {data.showMcpPicker ? (
        <div className="tool-node__popover">
          <select
            className="nodrag"
            value={data.mcpDraft}
            onChange={(event) => data.onChange(id, { mcpDraft: event.target.value })}
          >
            <option value="">Select MCP</option>
            {data.mcpEntryOptions.map((entry) => (
              <option key={entry.value} value={entry.value}>
                {entry.label}
              </option>
            ))}
          </select>
          <div className="tool-node__buttons">
            <button
              type="button"
              className="button-secondary nodrag"
              onClick={() => data.onChange(id, { showMcpPicker: false, mcpDraft: data.mcp })}
            >
              Cancel
            </button>
            <button
              type="button"
              className="nodrag"
              onClick={() =>
                data.onChange(id, {
                  mcp: data.mcpDraft,
                  showMcpPicker: false,
                })
              }
            >
              Save
            </button>
          </div>
        </div>
      ) : null}
      {selectedLabel ? <div className="tool-node__chip">{selectedLabel}</div> : null}
    </div>
  );
}

function PatternsNode({ id, data, selected }) {
  const [contractsOpen, setContractsOpen] = useState(false);
  const mode = data.auditMode ?? DEFAULT_AUDIT_MODE;
  const paths = resolveContractPaths(data);
  const providerLabel = data.providers?.find((provider) => provider.id === data.provider)?.label || data.provider || "-";
  const patternSummary = data.patternFile ? getOptionLabel(data.patternOptions || [], data.patternFile, "Pattern file") : "Select pattern file";
  const resultSummary = data.resultFile ? getOptionLabel(data.memoryOptions || [], data.resultFile, "Result file") : "Select result file";
  const promptSummary = data.promptFile ? getOptionLabel(data.skillFileOptions || [], data.promptFile, "Prompt file") : "Prompt file required";
  const scopeSummary =
    mode === "cluster" && data.cluster
      ? formatClusterLabel(data.cluster)
      : mode === "contract" && paths.length
        ? paths.length === 1
          ? getOptionLabel(data.contractOptions || [], paths[0], paths[0])
          : `${paths.length} contracts`
        : "-";
  const scopeLabel = mode === "cluster" ? "Cluster" : "Contracts";
  const contractTriggerLabel = !data.contractOptions?.length
    ? "No contract files in tree"
    : paths.length === 0
      ? "Select contracts..."
      : paths.length === 1
        ? getOptionLabel(data.contractOptions, paths[0], paths[0])
        : `${paths.length} contracts selected`;

  if (data.readOnly) {
    return (
      <NodeFrame selected={selected} nodeType="patterns" blockStatus={data.blockStatus}>
        <NodeHeader icon={BLOCK_META.patterns.icon} title="Patterns Block" subtitle={patternSummary} />
        <div className="agent-node__readonly-grid">
          <div className="agent-node__readonly-row"><span>Provider</span><span>{providerLabel}</span></div>
          <div className="agent-node__readonly-row"><span>Model</span><span>{data.model || "-"}</span></div>
          <div className="agent-node__readonly-row"><span>Pattern file</span><span>{patternSummary}</span></div>
          <div className="agent-node__readonly-row"><span>Result file</span><span>{resultSummary}</span></div>
          <div className="agent-node__readonly-row"><span>{scopeLabel}</span><span>{scopeSummary}</span></div>
          <div className="agent-node__readonly-row"><span>Prompt file</span><span>{promptSummary}</span></div>
        </div>
      </NodeFrame>
    );
  }

  return (
    <NodeFrame selected={selected} nodeType="patterns">
      <NodeHeader icon={BLOCK_META.patterns.icon} title="Patterns Block" subtitle={patternSummary} />
      <label className="agent-node__field">
        <span>Pattern file</span>
        <div className="canvas-node__inline-actions">
          <select className="nodrag" value={data.patternFile} disabled={!data.patternOptions.length} onChange={(event) => data.onChange(id, { patternFile: event.target.value })}>
            <option value="">{data.patternOptions.length ? "Select pattern file" : "No pattern files"}</option>
            {data.patternOptions.map((entry) => <option key={entry.value} value={entry.value}>{entry.label}</option>)}
          </select>
          <button type="button" className="icon-button nodrag" onClick={data.onRefreshCatalogs} aria-label="Refresh pattern files"><RefreshIcon /></button>
        </div>
      </label>
      <label className="agent-node__field">
        <span>Result file</span>
        <div className="canvas-node__inline-actions">
          <select className="nodrag" value={data.resultFile} onChange={(event) => data.onChange(id, { resultFile: event.target.value })}>
            <option value="">Select result file</option>
            {data.memoryOptions.map((entry) => <option key={entry.value} value={entry.value}>{entry.label}</option>)}
          </select>
          <button type="button" className="icon-button nodrag" onClick={data.onRefreshCatalogs} aria-label="Refresh result files"><RefreshIcon /></button>
        </div>
      </label>
      <label className="agent-node__field">
        <span>Provider</span>
        <select className="nodrag" value={data.provider} onChange={(event) => data.onChange(id, { provider: event.target.value, model: "" })}>
          <option value="">Select provider</option>
          {(data.providers || []).map((provider) => <option key={provider.id} value={provider.id}>{provider.label}</option>)}
        </select>
      </label>
      <label className="agent-node__field">
        <span>Model</span>
        <select className="nodrag" value={data.model} onChange={(event) => data.onChange(id, { model: event.target.value })}>
          <option value="">Select model</option>
          {(data.models || []).map((model) => <option key={model} value={model}>{model}</option>)}
        </select>
      </label>
      <div className="agent-node__field agent-node__contract-group">
        <span>{scopeLabel}</span>
        <div className="agent-node__contract-controls agent-node__contract-controls--stack">
          {mode === "cluster" && data.clustersNotReady ? (
            <div className="canvas-node__notice">
              Create clusters in section 4. Audit Mode (Generate clusters), then pick a cluster here.
            </div>
          ) : null}
          {mode === "cluster" ? (
            <select
              className="nodrag"
              value={data.cluster}
              onChange={(event) => data.onChange(id, {
                cluster: event.target.value,
                clusterFiles: getClusterFilesForSelection(data.clusterRecordsById, event.target.value),
                contractPaths: [],
              })}
              disabled={Boolean(data.clustersNotReady)}
            >
              <option value="">Select cluster</option>
              {data.clusterOptions.map((cluster) => <option key={cluster} value={cluster}>{formatClusterLabel(cluster)}</option>)}
            </select>
          ) : (
            <div className="agent-node__contract-dropdown-wrap nodrag">
              <button
                type="button"
                className={`agent-node__contract-trigger ${contractsOpen ? "agent-node__contract-trigger--open" : ""}`}
                onClick={() => data.contractOptions.length && setContractsOpen((open) => !open)}
                disabled={!data.contractOptions.length}
                aria-expanded={contractsOpen}
                aria-haspopup="listbox"
              >
                <span className="agent-node__contract-trigger-text">{contractTriggerLabel}</span>
                <span className="agent-node__contract-trigger-chevron" aria-hidden>▾</span>
              </button>
              {contractsOpen ? (
                <div className="agent-node__contract-dropdown">
                  <div className="agent-node__contract-picklist" role="listbox" aria-multiselectable="true">
                    {data.contractOptions.map((entry) => {
                      const checked = paths.includes(entry.value);
                      return (
                        <label key={entry.value} className="agent-node__contract-row">
                          <span className="agent-node__contract-row-label">{entry.label}</span>
                          <input
                            type="checkbox"
                            className="nodrag"
                            checked={checked}
                            onChange={() => {
                              const next = new Set(paths);
                              if (checked) {
                                next.delete(entry.value);
                              } else {
                                next.add(entry.value);
                              }
                              data.onChange(id, { contractPaths: [...next], cluster: "", clusterFiles: [] });
                            }}
                          />
                        </label>
                      );
                    })}
                  </div>
                  <div className="agent-node__contract-dropdown-footer">
                    <button type="button" className="button-secondary nodrag" onClick={() => setContractsOpen(false)}>
                      Done
                    </button>
                  </div>
                </div>
              ) : null}
            </div>
          )}
        </div>
      </div>
      <label className="agent-node__field">
        <span>Prompt file</span>
        <div className="canvas-node__inline-actions">
          <select className="nodrag" value={data.promptFile} disabled={!data.skillFileOptions.length} onChange={(event) => data.onChange(id, { promptFile: event.target.value })}>
            <option value="">{data.skillFileOptions.length ? "Select prompt file" : "No prompt files"}</option>
            {data.skillFileOptions.map((entry) => <option key={entry.value} value={entry.value}>{entry.label}</option>)}
          </select>
          <button type="button" className="icon-button nodrag" onClick={data.onRefreshCatalogs} aria-label="Refresh prompt files"><RefreshIcon /></button>
        </div>
      </label>
      {!data.patternOptions.length ? <div className="canvas-node__notice">Create a YAML file in <code>patterns</code> before running this block.</div> : null}
      {!data.skillFileOptions.length ? <div className="canvas-node__notice">Create a prompt file in <code>skills</code> before running this block.</div> : null}
      <NodeActions onRemove={() => data.onRemove(id)} title="Remove patterns block" />
    </NodeFrame>
  );
}

function MemoryNode({ id, data, selected }) {
  const memorySummary = data.memoryFile ? getOptionLabel(data.memoryOptions, data.memoryFile, "Memory file") : "Select memory file";
  const promptSummary = data.memoryPrompt ? getOptionLabel(data.memoryPromptOptions, data.memoryPrompt, "Memory prompt") : "Memory prompt required";

  const memProviderLabel = data.providers?.find((p) => p.id === data.provider)?.label || data.provider || "-";

  if (data.readOnly) {
    return (
      <NodeFrame selected={selected} nodeType="memory" blockStatus={data.blockStatus}>
        <NodeHeader icon={BLOCK_META.memory.icon} title="Memory Block" subtitle={memorySummary} />
        <div className="agent-node__readonly-grid">
          <div className="agent-node__readonly-row"><span>Provider</span><span>{memProviderLabel}</span></div>
          <div className="agent-node__readonly-row"><span>Model</span><span>{data.model || "-"}</span></div>
          <div className="agent-node__readonly-row"><span>Memory file</span><span>{memorySummary}</span></div>
          <div className="agent-node__readonly-row"><span>Memory prompt</span><span>{promptSummary}</span></div>
        </div>
      </NodeFrame>
    );
  }

  return (
    <NodeFrame selected={selected} nodeType="memory">
      <NodeHeader icon={BLOCK_META.memory.icon} title="Memory Block" subtitle={memorySummary} />
      <label className="agent-node__field">
        <span>Provider</span>
        <select className="nodrag" value={data.provider} onChange={(event) => data.onChange(id, { provider: event.target.value, model: "" })}>
          <option value="">Select provider</option>
          {(data.providers || []).map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
        </select>
      </label>
      <label className="agent-node__field">
        <span>Model</span>
        <select className="nodrag" value={data.model} onChange={(event) => data.onChange(id, { model: event.target.value })}>
          <option value="">Select model</option>
          {(data.models || []).map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
      </label>
      <label className="agent-node__field">
        <span>Memory file</span>
        <div className="canvas-node__inline-actions">
          <select className="nodrag" value={data.memoryFile} onChange={(event) => data.onChange(id, { memoryFile: event.target.value })}>
            <option value="">Select memory file</option>
            {data.memoryOptions.map((entry) => <option key={entry.value} value={entry.value}>{entry.label}</option>)}
          </select>
          <button
            type="button"
            className="icon-button nodrag"
            onClick={() => data.onChange(id, { creatingMemory: !data.creatingMemory, newMemoryName: data.creatingMemory ? "" : data.newMemoryName })}
            aria-label="Create memory file"
          >
            <PlusIcon />
          </button>
        </div>
      </label>
      {data.creatingMemory ? (
        <div className="memory-create">
          <input
            className="nodrag"
            value={data.newMemoryName}
            placeholder="memory-file-name"
            onChange={(event) => data.onChange(id, { newMemoryName: event.target.value })}
          />
          <div className="memory-create__actions">
            <button type="button" className="button-secondary nodrag" onClick={() => data.onChange(id, { creatingMemory: false, newMemoryName: "" })}>
              Cancel
            </button>
            <button type="button" className="nodrag" disabled={!data.newMemoryName.trim()} onClick={() => data.onCreateMemory(id, data.newMemoryName)}>
              Create
            </button>
          </div>
        </div>
      ) : null}
      <label className="agent-node__field">
        <span>Memory prompt</span>
        <div className="canvas-node__inline-actions">
          <select className="nodrag" value={data.memoryPrompt} disabled={!data.memoryPromptOptions.length} onChange={(event) => data.onChange(id, { memoryPrompt: event.target.value })}>
            <option value="">{data.memoryPromptOptions.length ? "Select memory prompt" : "No memory prompts"}</option>
            {data.memoryPromptOptions.map((entry) => <option key={entry.value} value={entry.value}>{entry.label}</option>)}
          </select>
          <button type="button" className="icon-button nodrag" onClick={data.onRefreshCatalogs} aria-label="Refresh memory prompts"><RefreshIcon /></button>
        </div>
      </label>
      {!data.memoryPromptOptions.length ? <div className="canvas-node__notice">{"\u0421\u043d\u0430\u0447\u0430\u043b\u0430 \u0441\u043e\u0437\u0434\u0430\u0439\u0442\u0435 prompt \u0432 \u043f\u0430\u043f\u043a\u0435 "}<code>memory_promts</code>{" \u0441 \u0438\u043d\u0441\u0442\u0440\u0443\u043a\u0446\u0438\u044f\u043c\u0438 \u0434\u043b\u044f \u043c\u043e\u0434\u0435\u043b\u0438 \u043e \u0442\u043e\u043c, \u0447\u0442\u043e \u0437\u0430\u043f\u0438\u0441\u044b\u0432\u0430\u0442\u044c \u0432 \u043f\u0430\u043c\u044f\u0442\u044c."}</div> : null}
      <NodeActions onRemove={() => data.onRemove(id)} title="Remove memory block" />
    </NodeFrame>
  );
}

function CodeNode({ id, data, selected }) {
  const summary = data.code?.trim() ? "Python code added" : "Paste Python code";
  if (data.readOnly) {
    return <NodeFrame selected={selected} nodeType="code" blockStatus={data.blockStatus}><NodeHeader icon={BLOCK_META.code.icon} title="Code Block" subtitle={summary} /><div className="code-node__readonly">{data.code?.trim() || "No code provided."}</div></NodeFrame>;
  }
  return (
    <NodeFrame selected={selected} nodeType="code">
      <NodeHeader icon={BLOCK_META.code.icon} title="Code Block" subtitle={summary} />
      <label className="agent-node__field">
        <span>Python code</span>
        <textarea className="code-node__textarea nodrag" value={data.code} onChange={(event) => data.onChange(id, { code: event.target.value })} placeholder={"def run():\n    return 'hello'"} />
      </label>
      <NodeActions onRemove={() => data.onRemove(id)} title="Remove code block" />
    </NodeFrame>
  );
}

const nodeTypes = { agent: AgentNode, tool: ToolNode, patterns: PatternsNode, memory: MemoryNode, code: CodeNode };

function createNode(nodeType, index, parentId = "") {
  const baseNode = { id: `${nodeType}-${Date.now()}-${index}`, type: nodeType, position: { x: 70 + index * 30, y: 60 + index * 24 } };
  if (nodeType === "patterns") {
    return {
      ...baseNode,
      data: {
        patternFile: "",
        resultFile: "",
        promptFile: "",
        provider: "",
        model: "",
        scopeMode: DEFAULT_AUDIT_MODE,
        contractPaths: [],
        cluster: "",
        clusterFiles: [],
      },
    };
  }
  if (nodeType === "memory") {
    return { ...baseNode, data: { memoryFile: "", memoryPrompt: "", provider: "", model: "", creatingMemory: false, newMemoryName: "" } };
  }
  if (nodeType === "code") {
    return { ...baseNode, data: { code: "" } };
  }
  if (nodeType === "tool") {
    return {
      ...baseNode,
      parentId,
      position: { x: 342, y: 56 },
      data: {
        mcp: "",
        mcpDraft: "",
        showMcpPicker: false,
        parentAgentId: parentId,
      },
    };
  }
  return {
    ...baseNode,
    type: "agent",
    data: {
      agentType: "audit",
      provider: "",
      model: "",
      skill: "",
      leadSkill: "",
        memoryFileToUse: "",
      contractPaths: [],
      cluster: "",
      addRelevantDocs: false,
    },
  };
}

function createAgentWithTool(index) {
  const agentNode = createNode("agent", index);
  const toolNode = createNode("tool", index, agentNode.id);
  const toolEdge = {
    id: `edge-${agentNode.id}-${toolNode.id}`,
    source: agentNode.id,
    target: toolNode.id,
    animated: false,
    markerEnd: { type: MarkerType.ArrowClosed, color: "#4b5563" },
    style: { stroke: "#4b5563", strokeWidth: 1.2 },
    selectable: false,
  };
  return { agentNode, toolNode, toolEdge };
}

function ensureToolChildren(nodes, edges) {
  const nextNodes = [...nodes];
  const nextEdges = [...edges];
  const toolNodes = nextNodes.filter((node) => node.type === "tool");

  nextNodes
    .filter((node) => (node.type || "agent") === "agent")
    .forEach((agentNode, index) => {
      const existingTool = toolNodes.find(
        (node) => node.data?.parentAgentId === agentNode.id || node.parentId === agentNode.id,
      );
      if (!existingTool) {
        const toolNode = createNode("tool", index + 1, agentNode.id);
        if (agentNode.data?.mcp) {
          toolNode.data.mcp = agentNode.data.mcp;
          toolNode.data.mcpDraft = agentNode.data.mcp;
        }
        nextNodes.push(toolNode);
      }
      const toolId = existingTool?.id || nextNodes[nextNodes.length - 1].id;
      const hasEdge = nextEdges.some((edge) => edge.source === agentNode.id && edge.target === toolId);
      if (!hasEdge) {
        nextEdges.push({
          id: `edge-${agentNode.id}-${toolId}`,
          source: agentNode.id,
          target: toolId,
          animated: false,
          markerEnd: { type: MarkerType.ArrowClosed, color: "#4b5563" },
          style: { stroke: "#4b5563", strokeWidth: 1.2 },
          selectable: false,
        });
      }
    });

  return { nodes: nextNodes, edges: nextEdges };
}
function InfoOptionSelect({ ariaLabel, emptyLabel, options }) {
  const listKey = options.map((option) => option.value).join("\u001f");
  if (options.length === 0) {
    return <select key={`empty-${emptyLabel}`} className="info-option-select" defaultValue="" aria-label={ariaLabel} title={INFO_SELECT_TITLE}><option value="">{emptyLabel}</option></select>;
  }
  return <select key={listKey} className="info-option-select" defaultValue={options[0].value} aria-label={ariaLabel} title={INFO_SELECT_TITLE}>{options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select>;
}

function FileTree({ node, excludedPaths, onToggle, level = 0 }) {
  const isExcluded = node.excluded || excludedPaths.includes(node.path);
  return (
    <div className={`tree-node ${isExcluded ? "tree-node--excluded" : ""}`}>
      <div className="tree-node__row" style={{ paddingLeft: `${level * 14}px` }}>
        <span className={`tree-node__kind tree-node__kind--${node.kind}`}>{node.kind === "directory" ? "DIR" : "FILE"}</span>
        <span className="tree-node__name">{node.name}</span>
        {isExcluded ? <span className="tree-node__excluded-badge">EXCLUDED</span> : null}
        <button type="button" className="tree-node__toggle" onClick={() => onToggle(node.path)}>{excludedPaths.includes(node.path) ? "Include" : "Exclude"}</button>
      </div>
      {node.children?.length > 0 ? <div>{node.children.map((child) => <FileTree key={child.path} node={child} excludedPaths={excludedPaths} onToggle={onToggle} level={level + 1} />)}</div> : null}
    </div>
  );
}

function decorateNodes(nodes, resources) {
  return nodes.map((node) => {
    const nodeType = node.type || "agent";
    if (nodeType === "tool") {
      return {
        ...node,
        type: "tool",
        data: {
          mcp: "",
          mcpDraft: "",
          showMcpPicker: false,
          ...node.data,
          mcpEntryOptions: resources.mcpEntryOptions,
          onChange: resources.onNodeChange,
        },
      };
    }
    if (nodeType === "memory") {
      const memoryModels = resources.providers.find((p) => p.id === (node.data || {}).provider)?.models || [];
      return {
        ...node,
        type: "memory",
        data: {
          memoryFile: "",
          memoryPrompt: "",
          provider: "",
          model: "",
          creatingMemory: false,
          newMemoryName: "",
          ...node.data,
          providers: resources.providers,
          models: memoryModels,
          memoryOptions: resources.memoryOptions,
          memoryPromptOptions: resources.memoryPromptOptions,
          onChange: resources.onNodeChange,
          onRemove: resources.onNodeRemove,
          onRefreshCatalogs: resources.onRefreshCatalogs,
          onCreateMemory: resources.onCreateMemory,
        },
      };
    }
    if (nodeType === "patterns") {
      const models = resources.providers.find((provider) => provider.id === (node.data || {}).provider)?.models || [];
      return {
        ...node,
        type: "patterns",
        data: {
          patternFile: "",
          resultFile: "",
          promptFile: "",
          provider: "",
          model: "",
          scopeMode: resources.auditMode,
          contractPaths: [],
          cluster: "",
          clusterFiles: [],
          ...node.data,
          auditMode: resources.auditMode,
          clustersNotReady: resources.clustersNotReady,
          providers: resources.providers,
          models,
          patternOptions: resources.patternOptions,
          skillFileOptions: resources.skillFileOptions,
          memoryOptions: resources.memoryOptions,
          contractOptions: resources.contractOptions,
          clusterOptions: resources.clusterOptions,
          clusterRecordsById: resources.clusterRecordsById,
          onChange: resources.onNodeChange,
          onRemove: resources.onNodeRemove,
          onRefreshCatalogs: resources.onRefreshCatalogs,
        },
      };
    }
    if (nodeType === "code") {
      return {
        ...node,
        type: "code",
        data: { code: "", ...node.data, onChange: resources.onNodeChange, onRemove: resources.onNodeRemove },
      };
    }
    const models = resources.providers.find((provider) => provider.id === node.data.provider)?.models || [];
    const {
      contractMode: _legacyMode,
      contractPath: _legacyPath,
      contractPaths: _pathsRaw,
      cluster: _clusterRaw,
      ...agentRest
    } = node.data || {};
    const contractPaths = resolveContractPaths(node.data || {});
    const cluster = (node.data && node.data.cluster) || "";
    const estimate = resources.agentEstimates[node.id] || {};
    return {
      ...node,
      type: "agent",
      data: {
        agentType: "audit",
        provider: "",
        model: "",
        skill: "",
        leadSkill: "",
        memoryFileToUse: "",
        ...agentRest,
        contractPaths,
        cluster,
        addRelevantDocs: Boolean(node.data?.addRelevantDocs),
        auditMode: resources.auditMode,
        clustersNotReady: resources.clustersNotReady,
        docsReady: resources.docsReady,
        providers: resources.providers,
        models,
        skillOptions: resources.skillOptions,
        leadSkillOptions: resources.leadSkillOptions,
        memoryOptions: resources.memoryOptions,
        contractOptions: resources.contractOptions,
        clusterOptions: resources.clusterOptions,
        minCallsEstimate: estimate.min_calls,
        tokensApproxEstimate: estimate.tokens_approx_total_min,
        estimateLoading: Boolean(estimate.loading),
        onChange: resources.onNodeChange,
        onRemove: resources.onNodeRemove,
        onRefreshCatalogs: resources.onRefreshCatalogs,
      },
    };
  });
}

function serializeNodes(nodes) {
  return nodes.map((node) => {
    if (node.type === "tool") {
      return {
        ...node,
        data: {
          mcp: node.data.mcp,
          parentAgentId: node.data.parentAgentId,
        },
      };
    }
    if (node.type === "memory") {
      return { ...node, data: { memoryFile: node.data.memoryFile, memoryPrompt: node.data.memoryPrompt, provider: node.data.provider || "", model: node.data.model || "" } };
    }
    if (node.type === "patterns") {
      return {
        ...node,
        data: {
          patternFile: node.data.patternFile || "",
          resultFile: node.data.resultFile || "",
          promptFile: node.data.promptFile || "",
          provider: node.data.provider || "",
          model: node.data.model || "",
          scopeMode: node.data.auditMode || node.data.scopeMode || DEFAULT_AUDIT_MODE,
          contractPaths: resolveContractPaths(node.data),
          cluster: node.data.cluster || "",
          clusterFiles: resolveClusterFiles(node.data),
        },
      };
    }
    if (node.type === "code") {
      return { ...node, data: { code: node.data.code } };
    }
    return {
      ...node,
      data: {
        agentType: node.data.agentType,
        provider: node.data.provider,
        model: node.data.model,
        skill: node.data.skill,
        leadSkill: node.data.leadSkill,
        memoryFileToUse: node.data.memoryFileToUse || "",
        contractPaths: resolveContractPaths(node.data),
        cluster: node.data.cluster || "",
        addRelevantDocs: Boolean(node.data.addRelevantDocs),
      },
    };
  });
}

function buildEstimateNodes(nodes) {
  return nodes.flatMap((node) => {
    const nodeType = node.type || "agent";
    if (nodeType === "tool") {
      return [{
        id: node.id,
        type: "tool",
        data: {
          mcp: node.data?.mcp || "",
          parentAgentId: node.data?.parentAgentId || node.parentId || "",
        },
      }];
    }
    if (nodeType !== "agent") {
      return [];
    }
    return [{
      id: node.id,
      type: "agent",
      data: {
        agentType: node.data?.agentType || "audit",
        provider: node.data?.provider || "",
        model: node.data?.model || "",
        skill: node.data?.skill || "",
        leadSkill: node.data?.leadSkill || "",
        memoryFileToUse: node.data?.memoryFileToUse || "",
        contractPaths: resolveContractPaths(node.data || {}),
        cluster: node.data?.cluster || "",
        addRelevantDocs: Boolean(node.data?.addRelevantDocs),
      },
    }];
  });
}

export default function App() {
  const [workspacePath, setWorkspacePath] = useState("");
  const [auditPath, setAuditPath] = useState("");
  const [providers, setProviders] = useState([]);
  const [catalogs, setCatalogs] = useState(EMPTY_CATALOGS);
  const [docsStatus, setDocsStatus] = useState(DEFAULT_DOCS_STATUS);
  const [system, setSystem] = useState({ foundry: false, medusa: false, configPath: "" });
  const [fileTree, setFileTree] = useState(null);
  const [workspaceFileTree, setWorkspaceFileTree] = useState(null);
  const [excludedPaths, setExcludedPaths] = useState([]);
  const [pipelineName, setPipelineName] = useState("custom-audit-flow");
  const [pipelines, setPipelines] = useState([]);
  const [selectedPipeline, setSelectedPipeline] = useState("");
  const [nodes, setNodes] = useState([]);
  const [edges, setEdges] = useState([]);
  const [viewport, setViewport] = useState({ x: 0, y: 0, zoom: 1 });
  const [canvasRevision, setCanvasRevision] = useState(0);
  const [auditMode, setAuditMode] = useState(DEFAULT_AUDIT_MODE);
  const [status, setStatus] = useState("Choose an audit folder (code to scan); builder resources load from the workspace.");
  const [builderTab, setBuilderTab] = useState("settings");
  const [clusterOptions, setClusterOptions] = useState(DEFAULT_CLUSTER_OPTIONS);
  const [generatedClusters, setGeneratedClusters] = useState([]);
  const [clusterStats, setClusterStats] = useState({ solFiles: 0, clusters: 0, functions: 0, edges: 0 });
  const [isGeneratingClusters, setIsGeneratingClusters] = useState(false);
  const [isPreparingDocs, setIsPreparingDocs] = useState(false);
  const [agentEstimates, setAgentEstimates] = useState({});
  const [estimateRefreshKey, setEstimateRefreshKey] = useState(0);
  const [sectionRefreshState, setSectionRefreshState] = useState({ providers: false, resources: false, docs: false });
  const [chatPanelWidth, setChatPanelWidth] = useState(readChatPanelWidth);

  const [auditRunId, setAuditRunId] = useState(null);
  const [auditRunStatus, setAuditRunStatus] = useState(null);
  const [auditEvents, setAuditEvents] = useState([]);
  const [auditBlockStatuses, setAuditBlockStatuses] = useState({});
  const [auditWs, setAuditWs] = useState(null);
  const auditLogListRef = useRef(null);

  const skillOptions = useMemo(() => mapCatalogNames(catalogs.skills.entries), [catalogs.skills.entries]);
  const skillFileOptions = useMemo(() => mapCatalogPaths(catalogs.skills.entries), [catalogs.skills.entries]);
  const leadSkillOptions = useMemo(() => mapCatalogNames(catalogs.lead_skills.entries), [catalogs.lead_skills.entries]);
  const mcpOptions = useMemo(() => mapCatalogNames(catalogs.mcp.entries), [catalogs.mcp.entries]);
  const mcpEntryOptions = useMemo(() => mapCatalogPaths(catalogs.mcp.entries), [catalogs.mcp.entries]);
  const patternOptions = useMemo(() => mapCatalogPaths(catalogs.patterns.entries), [catalogs.patterns.entries]);
  const memoryOptions = useMemo(() => mapCatalogPaths(catalogs.memory.entries), [catalogs.memory.entries]);
  const memoryPromptOptions = useMemo(() => mapCatalogPaths(catalogs.memory_promts.entries), [catalogs.memory_promts.entries]);
  const contractOptions = useMemo(() => flattenFileOptions(fileTree), [fileTree]);
  const clusterRecordsById = useMemo(
    () => Object.fromEntries((generatedClusters || []).map((cluster) => [cluster.cluster_id, cluster])),
    [generatedClusters],
  );
  const estimateRequestBody = useMemo(() => JSON.stringify({ nodes: buildEstimateNodes(nodes) }), [nodes]);
  const estimateRequestKey = useMemo(() => `${estimateRefreshKey}:${estimateRequestBody}`, [estimateRefreshKey, estimateRequestBody]);

  const updateNodes = useCallback((transform) => setNodes((current) => transform(current)), []);
  const refreshAgentEstimates = useCallback(() => setEstimateRefreshKey((current) => current + 1), []);

  // canvasRevision: monotonic counter advanced on every node/edge mutation —
  // used by the chat backend to reconcile agent-emitted canvas actions
  // against the current graph state.
  useEffect(() => { setCanvasRevision((current) => current + 1); }, [nodes, edges]);
  const bumpCanvasRevision = useCallback(() => setCanvasRevision((current) => current + 1), []);

  // Mutation provenance: set by canvasDispatcher BEFORE it invokes
  // setNodes/setEdges, then cleared by the audit-log effect. Keeps
  // agent-driven mutations from being re-logged as "manual" by the
  // debounced batcher below.
  const agentMutationInFlightRef = useRef(false);
  const auditStateRef = useRef({
    initialized: false,
    lastSnapshot: { nodes: [], edges: [] },
    lastLoggedRevision: 0,
    pendingTimer: null,
  });
  const [auditDrawerOpen, setAuditDrawerOpen] = useState(false);
  const [undoDrawerOpen, setUndoDrawerOpen] = useState(false);
  const [auditRefreshTick, setAuditRefreshTick] = useState(0);
  // Imperative open trigger consumed by MonacoPane: bumps `key` so the
  // same {path, line} pair can re-fire (e.g. user clicks the same
  // citation twice). MonacoPane reacts via useEffect.
  const [pendingEditorOpen, setPendingEditorOpen] = useState(null);

  // Flat workspace-relative → absolute path map for chat citation
  // validation. Built once from workspaceFileTree.
  const workspacePathMap = useMemo(() => {
    const map = new Map();
    function walk(node) {
      if (!node) return;
      if (node.kind === "file" && node.path) {
        const abs = node.path.replace(/\\/g, "/");
        const root = (workspacePath || "").replace(/\\/g, "/").replace(/\/$/, "");
        let rel = abs;
        if (root && abs.startsWith(root + "/")) {
          rel = abs.slice(root.length + 1);
        } else if (root && abs === root) {
          rel = "";
        }
        if (rel) map.set(rel, node.path);
      }
      if (Array.isArray(node.children)) node.children.forEach(walk);
    }
    walk(workspaceFileTree);
    return map;
  }, [workspaceFileTree, workspacePath]);

  const workspacePathSet = useMemo(
    () => new Set(workspacePathMap.keys()),
    [workspacePathMap],
  );

  const handleOpenInEditor = useCallback((path, line) => {
    if (!path) return;
    const normalized = String(path).replace(/\\/g, "/");
    const abs = workspacePathMap.get(normalized) || normalized;
    setBuilderTab("editor");
    setPendingEditorOpen({ path: abs, line: Number(line) || null, key: Date.now() });
  }, [workspacePathMap]);

  const canvasDispatcher = useMemo(
    () => makeCanvasDispatcher({
      setNodes: (updater) => {
        agentMutationInFlightRef.current = true;
        setNodes(updater);
      },
      setEdges: (updater) => {
        agentMutationInFlightRef.current = true;
        setEdges(updater);
      },
      setViewport: (next) => {
        agentMutationInFlightRef.current = true;
        setViewport(next);
      },
    }),
    [],
  );

  // Undo dispatcher uses *raw* setters so the resulting state-change
  // flows through the manual_batch debounce — keeping an immutable
  // record of the undo itself in canvas_audit_log.
  const undoCanvasDispatcher = useMemo(
    () => makeCanvasDispatcher({ setNodes, setEdges, setViewport }),
    [],
  );

  // Snapshot serializer for the manual-batch audit row. Captures only the
  // fields the backend / undo flow actually needs — avoids logging
  // selection state or measured layout sizes.
  const snapshotNodesEdges = useCallback(() => ({
    nodes: nodes.map((n) => ({
      id: n.id,
      type: n.type,
      position: n.position,
      data: n.data,
      parentId: n.parentId || null,
    })),
    edges: edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      sourceHandle: e.sourceHandle || null,
      targetHandle: e.targetHandle || null,
      data: e.data || {},
    })),
  }), [nodes, edges]);

  // Debounced manual-batch push to /api/canvas/audit.
  useEffect(() => {
    const state = auditStateRef.current;
    if (!state.initialized) {
      state.initialized = true;
      state.lastSnapshot = snapshotNodesEdges();
      state.lastLoggedRevision = canvasRevision;
      return;
    }
    if (agentMutationInFlightRef.current) {
      // Agent-driven mutation; the backend already wrote an audit row.
      agentMutationInFlightRef.current = false;
      state.lastSnapshot = snapshotNodesEdges();
      state.lastLoggedRevision = canvasRevision;
      setAuditRefreshTick((t) => t + 1);
      return;
    }
    if (state.pendingTimer) clearTimeout(state.pendingTimer);
    state.pendingTimer = setTimeout(async () => {
      const before = state.lastSnapshot;
      const after = snapshotNodesEdges();
      if (JSON.stringify(before) === JSON.stringify(after)) return;
      try {
        await fetch("/api/canvas/audit", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            revision_before: state.lastLoggedRevision,
            revision_after: canvasRevision,
            snapshot_before: before,
            snapshot_after: after,
            reason: "manual_edit",
          }),
        });
        state.lastSnapshot = after;
        state.lastLoggedRevision = canvasRevision;
        setAuditRefreshTick((t) => t + 1);
      } catch {
        // Non-fatal: audit log push failure should not break the UI.
      }
    }, 600);
    return () => {
      if (state.pendingTimer) clearTimeout(state.pendingTimer);
    };
  }, [nodes, edges, canvasRevision, snapshotNodesEdges]);
  const setSectionRefreshing = useCallback((sectionKey, isRefreshing) => {
    setSectionRefreshState((current) => ({ ...current, [sectionKey]: isRefreshing }));
  }, []);

  const handleNodeChange = useCallback((id, patch) => {
    updateNodes((current) => current.map((node) => (node.id === id ? { ...node, data: { ...node.data, ...patch } } : node)));
  }, [updateNodes]);

  const handleNodeRemove = useCallback((id) => {
    const childIds = nodes.filter((node) => node.parentId === id || node.data?.parentAgentId === id).map((node) => node.id);
    updateNodes((current) => current.filter((node) => node.id !== id && !childIds.includes(node.id)));
    setEdges((current) => current.filter((edge) => edge.source !== id && edge.target !== id && !childIds.includes(edge.source) && !childIds.includes(edge.target)));
  }, [nodes, updateNodes]);

  const handleRefreshCatalogs = useCallback(async () => {
    try {
      const payload = await api("/api/project/catalogs");
      setWorkspacePath(payload.workspacePath || workspacePath);
      setCatalogs(payload.catalogs || EMPTY_CATALOGS);
      setDocsStatus(payload.docs || DEFAULT_DOCS_STATUS);
      setPipelines(payload.pipelines || []);
      setSystem(payload.system || {});
      refreshAgentEstimates();
      setStatus("Workspace catalogs refreshed.");
    } catch (error) {
      setStatus(`Unable to refresh catalogs: ${error.message}`);
    }
  }, [refreshAgentEstimates, workspacePath]);

  const handleRefreshProviders = useCallback(async () => {
    setSectionRefreshing("providers", true);
    try {
      const payload = await api("/api/settings/providers");
      setProviders(payload.providers || []);
      refreshAgentEstimates();
      setStatus("Providers & configured models refreshed.");
    } catch (error) {
      setStatus(`Unable to refresh providers: ${error.message}`);
    } finally {
      setSectionRefreshing("providers", false);
    }
  }, [refreshAgentEstimates, setSectionRefreshing]);

  const handleRefreshResources = useCallback(async () => {
    setSectionRefreshing("resources", true);
    try {
      await handleRefreshCatalogs();
    } finally {
      setSectionRefreshing("resources", false);
    }
  }, [handleRefreshCatalogs, setSectionRefreshing]);

  const handleRefreshDocsStatus = useCallback(async () => {
    setSectionRefreshing("docs", true);
    try {
      const payload = await api("/api/docs/status");
      setDocsStatus(payload.docs || DEFAULT_DOCS_STATUS);
      setStatus("Docs status refreshed.");
    } catch (error) {
      setStatus(`Unable to refresh docs status: ${error.message}`);
    } finally {
      setSectionRefreshing("docs", false);
    }
  }, [setSectionRefreshing]);

  const handleCreateMemoryFile = useCallback(async (id, fileName) => {
    if (!fileName?.trim()) {
      return;
    }
    try {
      const payload = await api("/api/memory/create", { method: "POST", body: JSON.stringify({ name: fileName.trim() }) });
      setCatalogs(payload.catalogs || EMPTY_CATALOGS);
      handleNodeChange(id, { memoryFile: payload.path, creatingMemory: false, newMemoryName: "" });
      setStatus(`Memory file created: ${payload.path}`);
    } catch (error) {
      setStatus(`Unable to create memory file: ${error.message}`);
    }
  }, [handleNodeChange]);

  const clustersNotReady = useMemo(
    () => auditMode === "cluster" && generatedClusters.length === 0 && clusterStats.clusters === 0,
    [auditMode, generatedClusters, clusterStats],
  );

  const decorationResources = useMemo(() => ({
    providers, skillOptions, skillFileOptions, leadSkillOptions, mcpOptions, mcpEntryOptions, contractOptions, patternOptions, memoryOptions, memoryPromptOptions,
    clusterOptions,
    clusterRecordsById,
    auditMode,
    clustersNotReady,
    docsReady: Boolean(docsStatus.prepared),
    agentEstimates,
    onNodeChange: handleNodeChange, onNodeRemove: handleNodeRemove, onRefreshCatalogs: handleRefreshCatalogs, onCreateMemory: handleCreateMemoryFile,
  }), [providers, skillOptions, skillFileOptions, leadSkillOptions, mcpOptions, mcpEntryOptions, contractOptions, patternOptions, memoryOptions, memoryPromptOptions, clusterOptions, clusterRecordsById, auditMode, clustersNotReady, docsStatus.prepared, agentEstimates, handleNodeChange, handleNodeRemove, handleRefreshCatalogs, handleCreateMemoryFile]);

  const applyDecoration = useCallback((draftNodes) => decorateNodes(draftNodes, decorationResources), [decorationResources]);
  const flowNodes = useMemo(() => applyDecoration(nodes), [applyDecoration, nodes]);
  const auditFlowNodes = useMemo(() => applyDecoration(nodes).map((node) => ({
    ...node,
    data: { ...node.data, readOnly: true, blockStatus: auditBlockStatuses[node.id] || null },
  })), [applyDecoration, nodes, auditBlockStatuses]);
  const auditNodeIndex = useMemo(() => buildAuditNodeIndex(nodes, contractOptions), [nodes, contractOptions]);
  const visibleAuditEvents = useMemo(
    () => auditEvents.map((evt, idx) => {
      const formatted = formatAuditLogEvent(evt, auditNodeIndex);
      return formatted ? { ...formatted, key: `${idx}-${evt.type}` } : null;
    }).filter(Boolean),
    [auditEvents, auditNodeIndex],
  );
  const resourceCards = useMemo(
    () => RESOURCE_CATALOG_META.map(({ key, label }) => {
      const catalog = catalogs[key] || { entries: [] };
      return {
        key,
        label,
        count: catalog.entries.length,
        options: catalog.entries.map((entry) => ({ value: entry.path, label: entry.name })),
      };
    }),
    [catalogs],
  );

  useEffect(() => {
    let active = true;
    api("/api/bootstrap")
      .then((payload) => {
        if (!active) {
          return;
        }
        const ws = payload.workspacePath || payload.projectPath || "";
        const audit = payload.auditPath || payload.projectPath || ws;
        setWorkspacePath(ws);
        setAuditPath(audit);
        setProviders(payload.providers || []);
        setCatalogs(payload.catalogs || EMPTY_CATALOGS);
        setDocsStatus(payload.docs || DEFAULT_DOCS_STATUS);
        setSystem(payload.system || {});
        setPipelines(payload.pipelines || []);
        setFileTree(payload.fileTree || null);
        setWorkspaceFileTree(payload.fileTree || null);
      })
      .catch((error) => {
        if (active) {
          setStatus(`Bootstrap failed: ${error.message}`);
        }
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const parsedPayload = JSON.parse(estimateRequestBody);
    const payloadNodes = Array.isArray(parsedPayload.nodes) ? parsedPayload.nodes : [];
    const agentIds = payloadNodes.filter((node) => node.type === "agent").map((node) => node.id);
    if (!agentIds.length) {
      setAgentEstimates({});
      return undefined;
    }

    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => {
      setAgentEstimates((current) => {
        const next = {};
        agentIds.forEach((id) => {
          next[id] = { ...(current[id] || {}), loading: true };
        });
        return next;
      });

      api("/api/pipeline/estimate", {
        method: "POST",
        body: estimateRequestBody,
        signal: controller.signal,
      })
        .then((payload) => {
          const nextEstimates = payload.estimates || {};
          setAgentEstimates(() => {
            const next = {};
            agentIds.forEach((id) => {
              next[id] = nextEstimates[id] ? { ...nextEstimates[id], loading: false } : { min_calls: null, tokens_approx_total_min: null, loading: false };
            });
            return next;
          });
        })
        .catch((error) => {
          if (error.name === "AbortError") {
            return;
          }
          setAgentEstimates((current) => {
            const next = {};
            agentIds.forEach((id) => {
              next[id] = { ...(current[id] || {}), loading: false };
            });
            return next;
          });
        });
    }, 450);

    return () => {
      controller.abort();
      window.clearTimeout(timeoutId);
    };
  }, [estimateRequestBody, estimateRequestKey]);

  useEffect(() => {
    if (!auditLogListRef.current) {
      return;
    }
    auditLogListRef.current.scrollTop = auditLogListRef.current.scrollHeight;
  }, [visibleAuditEvents]);

  const onNodesChange = useCallback((changes) => updateNodes((current) => applyNodeChanges(changes, current)), [updateNodes]);
  const onEdgesChange = useCallback((changes) => setEdges((current) => applyEdgeChanges(changes, current)), []);
  const onConnect = useCallback((params) => setEdges((current) => addEdge({ ...params, animated: true, markerEnd: { type: MarkerType.ArrowClosed, color: "#58a6ff" }, style: { stroke: "#58a6ff", strokeWidth: 1.6 } }, current)), []);

  const handleProjectSelect = useCallback(async () => {
    try {
      const payload = await api("/api/project/select", { method: "POST", body: JSON.stringify({ audit_path: auditPath }) });
      if (!payload.selected) {
        setStatus("Audit folder selection was cancelled.");
        return;
      }
      const next = payload.auditPath || payload.projectPath;
      setAuditPath(next);
      setFileTree(payload.fileTree || null);
      setWorkspaceFileTree(payload.fileTree || null);
      setExcludedPaths([]);
      setGeneratedClusters([]);
      setClusterStats({ solFiles: 0, clusters: 0, functions: 0, edges: 0 });
      setClusterOptions(DEFAULT_CLUSTER_OPTIONS);
      refreshAgentEstimates();
      setStatus(`Audit folder: ${next}`);
    } catch (error) {
      setStatus(`Unable to select audit folder: ${error.message}`);
    }
  }, [auditPath, refreshAgentEstimates]);

  const handleTreeRefresh = useCallback(async (nextExcluded = excludedPaths) => {
    try {
      const payload = await api("/api/project/tree", { method: "POST", body: JSON.stringify({ project_path: auditPath, excluded_paths: nextExcluded }) });
      const next = payload.auditPath || payload.projectPath;
      if (next) {
        setAuditPath(next);
      }
      setFileTree(payload.fileTree);
      setWorkspaceFileTree(payload.fileTree);
      setGeneratedClusters([]);
      setClusterStats({ solFiles: 0, clusters: 0, functions: 0, edges: 0 });
      setClusterOptions(DEFAULT_CLUSTER_OPTIONS);
      setNodes((current) => pruneAgentsContractPaths(current, nextExcluded));
      refreshAgentEstimates();
    } catch (error) {
      setStatus(`Unable to refresh file tree: ${error.message}`);
    }
  }, [auditPath, excludedPaths, refreshAgentEstimates]);

  const handleToggleExcluded = useCallback(async (path) => {
    const nextExcluded = excludedPaths.includes(path) ? excludedPaths.filter((item) => item !== path) : [...excludedPaths, path];
    setExcludedPaths(nextExcluded);
    await handleTreeRefresh(nextExcluded);
  }, [excludedPaths, handleTreeRefresh]);

  const handleGenerateClusters = useCallback(async () => {
    setIsGeneratingClusters(true);
    try {
      const payload = await api("/api/clusters/generate", {
        method: "POST",
        body: JSON.stringify({ project_path: auditPath, excluded_paths: excludedPaths }),
      });
      const nextOptions = payload.clusterOptions?.length ? payload.clusterOptions : DEFAULT_CLUSTER_OPTIONS;
      const nextClusterRecordsById = Object.fromEntries((payload.clusters || []).map((cluster) => [cluster.cluster_id, cluster]));
      setGeneratedClusters(payload.clusters || []);
      setClusterStats(payload.stats || { solFiles: 0, clusters: 0, functions: 0, edges: 0 });
      setClusterOptions(nextOptions);
      setNodes((current) => current.map((node) => {
        const nodeType = node.type || "agent";
        if (nodeType !== "agent" && nodeType !== "patterns") {
          return node;
        }
        if (auditMode === "cluster" && node.data?.cluster && !nextOptions.includes(node.data.cluster)) {
          return { ...node, data: { ...node.data, cluster: "", clusterFiles: [] } };
        }
        if (nodeType === "patterns" && node.data?.cluster) {
          return {
            ...node,
            data: {
              ...node.data,
              clusterFiles: getClusterFilesForSelection(nextClusterRecordsById, node.data.cluster),
            },
          };
        }
        return node;
      }));
      setStatus(`Generated ${payload.stats?.clusters || 0} clusters from ${payload.stats?.solFiles || 0} Solidity files.`);
    } catch (error) {
      setStatus(`Unable to generate clusters: ${error.message}`);
    } finally {
      setIsGeneratingClusters(false);
    }
  }, [auditPath, auditMode, excludedPaths]);

  const handleRemoveCluster = useCallback((clusterId) => {
    setGeneratedClusters((current) => {
      const next = current.filter((c) => c.cluster_id !== clusterId);
      setClusterOptions(next.length ? next.map((c) => c.cluster_id) : []);
      setClusterStats((prev) => ({
        ...prev,
        clusters: next.length,
        functions: next.reduce((acc, c) => acc + (c.functions?.length || 0), 0),
      }));
      setNodes((nodes) =>
        nodes.map((node) => {
          const nodeType = node.type || "agent";
          if (nodeType !== "agent" && nodeType !== "patterns") {
            return node;
          }
          if (node.data?.cluster === clusterId) {
            return { ...node, data: { ...node.data, cluster: "", clusterFiles: [] } };
          }
          return node;
        }),
      );
      return next;
    });
  }, []);

  const handleStartAudit = useCallback(async () => {
    if (!nodes.length) {
      setStatus("Create the pipeline on the canvas before starting the audit.");
      return;
    }
    setAuditRunStatus("starting");
    setAuditEvents([]);
    setAuditBlockStatuses({});
    if (auditWs) {
      auditWs.close();
    }
    try {
      const result = await api("/api/pipeline/run", {
        method: "POST",
        body: JSON.stringify({
          pipeline_name: pipelineName,
          pipeline_data: {
            schemaVersion: 1,
            name: pipelineName || "current-canvas",
            nodes: serializeNodes(nodes),
            edges,
            viewport,
          },
          audit_project_path: auditPath,
          excluded_paths: excludedPaths,
          audit_mode: auditMode,
        }),
      });
      const runId = result.run_id;
      setAuditRunId(runId);
      setAuditRunStatus("running");
      setStatus(`Audit started: ${runId}`);

      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(`${protocol}//${window.location.host}/ws/pipeline/${runId}`);
      setAuditWs(ws);

      ws.onmessage = (msg) => {
        try {
          const evt = JSON.parse(msg.data);
          setAuditEvents((prev) => [...prev, evt]);

          if (evt.type === "block_start") {
            setAuditBlockStatuses((prev) => ({ ...prev, [evt.block_id]: "running" }));
          }
          if (evt.type === "block_end") {
            setAuditBlockStatuses((prev) => ({ ...prev, [evt.block_id]: evt.status }));
          }
          if (evt.type === "pipeline_end") {
            setAuditRunStatus(evt.status);
            setStatus(`Audit ${evt.status}: ${runId}`);
            ws.close();
          }
        } catch (_e) { /* skip malformed */ }
      };
      ws.onclose = () => setAuditWs(null);
      ws.onerror = () => {
        setAuditRunStatus("error");
        setStatus("WebSocket connection error.");
      };
    } catch (error) {
      setAuditRunStatus("error");
      setStatus(`Unable to start audit: ${error.message}`);
    }
  }, [pipelineName, nodes, auditWs, edges, viewport, auditPath, excludedPaths, auditMode]);

  const handleStopAudit = useCallback(async () => {
    if (!auditRunId) return;
    try {
      await api(`/api/pipeline/stop/${auditRunId}`, { method: "POST", body: "{}" });
      setAuditRunStatus("stopped");
      setStatus("Audit stopped.");
      if (auditWs) auditWs.close();
    } catch (error) {
      setStatus(`Unable to stop audit: ${error.message}`);
    }
  }, [auditRunId, auditWs]);

  const handleOpenConfig = useCallback(async () => {
    try {
      const payload = await api("/api/config/open", { method: "POST", body: "{}" });
      setStatus(payload.ok ? `Config opened: ${payload.path}` : `Could not auto-open config: ${payload.path}`);
    } catch (error) {
      setStatus(`Unable to open config: ${error.message}`);
    }
  }, []);

  const handlePrepareDocs = useCallback(async () => {
    setIsPreparingDocs(true);
    try {
      const payload = await api("/api/docs/prepare", { method: "POST", body: "{}" });
      const nextDocs = payload.docs || DEFAULT_DOCS_STATUS;
      setDocsStatus(nextDocs);
      await handleRefreshCatalogs();
      setStatus(
        nextDocs.prepared
          ? `Documentation prepared: ${nextDocs.chunk_count} chunks indexed.`
          : "No documentation chunks were indexed. Check audit_docs/*.md files.",
      );
    } catch (error) {
      setStatus(`Unable to prepare docs: ${error.message}`);
    } finally {
      setIsPreparingDocs(false);
    }
  }, [handleRefreshCatalogs]);

  const handleAddNode = useCallback((nodeType) => {
    if (nodeType === "agent") {
      const { agentNode, toolNode, toolEdge } = createAgentWithTool(nodes.length + 1);
      updateNodes((current) => [...current, agentNode, toolNode]);
      setEdges((current) => [...current, toolEdge]);
      return;
    }
    updateNodes((current) => [...current, createNode(nodeType, current.length + 1)]);
  }, [nodes.length, updateNodes]);

  const handleSavePipeline = useCallback(async () => {
    try {
      const payload = { schemaVersion: 1, name: pipelineName, nodes: serializeNodes(nodes), edges, viewport };
      const result = await api("/api/pipelines/save", {
        method: "POST",
        body: JSON.stringify({ audit_project_path: auditPath, excluded_paths: excludedPaths, pipeline_name: pipelineName, pipeline_data: payload }),
      });
      setPipelines(result.pipelines || []);
      setSelectedPipeline(result.savedName || pipelineName);
      setStatus(`Pipeline saved: ${result.savedPath}`);
    } catch (error) {
      setStatus(`Unable to save pipeline: ${error.message}`);
    }
  }, [auditPath, edges, excludedPaths, nodes, pipelineName, viewport]);

  const handleLoadPipeline = useCallback(async () => {
    try {
      const result = await api(`/api/pipelines/${encodeURIComponent(selectedPipeline)}`);
      const pipeline = result.pipeline;
      const normalized = ensureToolChildren(pipeline.nodes || [], pipeline.edges || []);
      setPipelineName(pipeline.name || selectedPipeline);
      const nextAudit = pipeline.projectPath || auditPath;
      const nextExcluded = pipeline.excludedPaths || [];
      setAuditPath(nextAudit);
      setExcludedPaths(nextExcluded);
      setGeneratedClusters([]);
      setClusterStats({ solFiles: 0, clusters: 0, functions: 0, edges: 0 });
      setClusterOptions(DEFAULT_CLUSTER_OPTIONS);
      setNodes(pruneAgentsContractPaths(normalizePipelineNodes(normalized.nodes), nextExcluded));
      setEdges(normalized.edges);
      setViewport(pipeline.viewport || { x: 0, y: 0, zoom: 1 });
      const treePayload = await api("/api/project/tree", { method: "POST", body: JSON.stringify({ project_path: nextAudit, excluded_paths: nextExcluded }) });
      setFileTree(treePayload.fileTree);
      setWorkspaceFileTree(treePayload.fileTree);
      await handleRefreshCatalogs();
      const resolved = treePayload.auditPath || treePayload.projectPath;
      if (resolved) {
        setAuditPath(resolved);
      }
      setStatus(`Pipeline loaded: ${selectedPipeline}`);
    } catch (error) {
      setStatus(`Unable to load pipeline: ${error.message}`);
    }
  }, [auditPath, handleRefreshCatalogs, selectedPipeline]);

  // Latest-value ref for ui_context: read on every chat send so we always
  // submit the freshest view of the canvas / tab / catalogs without
  // forcing ChatPanel to re-render whenever node/edge state ticks.
  const uiContextSnapshotRef = useRef({});
  uiContextSnapshotRef.current = {
    currentTab: builderTab,
    auditPath,
    workspacePath,
    excludedPaths,
    docsStatus: {
      ready: Boolean(docsStatus?.prepared),
      indexed_files: Number(docsStatus?.md_file_count || 0),
      chunk_count: Number(docsStatus?.chunk_count || 0),
      last_indexed: docsStatus?.last_indexed || null,
    },
    catalogs: Object.fromEntries(
      Object.entries(catalogs || {}).map(([key, section]) => [
        key,
        {
          path: section?.path || "",
          entries: (section?.entries || []).map((entry) => ({
            name: entry.name || "",
            path: entry.path || "",
            kind: entry.kind || "file",
          })),
        },
      ]),
    ),
    availableTools: (catalogs?.mcp?.entries || []).map((tool) => ({
      name: tool.name,
      description: "",
      endpoints: [],
    })),
    pipelineName,
    savedPipeline: selectedPipeline,
    nodes: nodes.map((node) => ({
      id: node.id,
      type: node.type || "agent",
      position: node.position || { x: 0, y: 0 },
      data: node.data || {},
      parentId: node.parentId || null,
    })),
    edges: edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      sourceHandle: edge.sourceHandle || null,
      targetHandle: edge.targetHandle || null,
      type: edge.type || null,
      data: edge.data || {},
    })),
    viewport,
    selectedNodes: [],
    selectedFiles: [],
    canvasRevision,
    auditMode,
  };
  const getUiContext = useCallback(() => uiContextSnapshotRef.current, []);

  const chatPanelVisible = builderTab !== "settings";

  const handleChatPanelWidthChange = useCallback((width) => {
    const next = Math.min(
      CHAT_PANEL_WIDTH_MAX,
      Math.max(CHAT_PANEL_WIDTH_MIN, Math.round(width)),
    );
    setChatPanelWidth(next);
  }, []);

  useLayoutEffect(() => {
    try {
      window.localStorage.setItem(CHAT_PANEL_WIDTH_KEY, String(chatPanelWidth));
    } catch {
      /* ignore */
    }
    document.documentElement.style.setProperty("--chat-panel-width", `${chatPanelWidth}px`);
  }, [chatPanelWidth]);

  return (
    <div className="app-shell">
      <main className="page">
        <h1>VulnFlow - Audit Pipeline Dashboard</h1>
        <div className="builder-tabs" role="tablist" aria-label="Builder views">
          <button type="button" role="tab" aria-selected={builderTab === "settings"} className={`builder-tab ${builderTab === "settings" ? "builder-tab--active" : ""}`} onClick={() => setBuilderTab("settings")}>1. Settings</button>
          <button type="button" role="tab" aria-selected={builderTab === "pipeline"} className={`builder-tab ${builderTab === "pipeline" ? "builder-tab--active" : ""}`} onClick={() => setBuilderTab("pipeline")}>2. Pipeline</button>
          <button type="button" role="tab" aria-selected={builderTab === "audit"} className={`builder-tab ${builderTab === "audit" ? "builder-tab--active" : ""}`} onClick={() => setBuilderTab("audit")}>3. Audit</button>
          <button type="button" role="tab" aria-selected={builderTab === "editor"} className={`builder-tab ${builderTab === "editor" ? "builder-tab--active" : ""}`} onClick={() => setBuilderTab("editor")}>4. Editor</button>
        </div>
        <div className={`page-body ${chatPanelVisible ? "page-body--with-chat" : ""}`}>
          <div className="page-body__main">
        {builderTab === "settings" ? (
          <div className="builder-layout" role="tabpanel" aria-label="Settings">
            <aside className="file-column">
              <div className="section-title">Audit File Tree</div>
              <div className="detail-panel file-panel">
                <div className="detail-toolbar"><div className="detail-toolbar__title">Scope</div><div className="detail-toolbar__meta">Excluded: {excludedPaths.length}</div></div>
                {fileTree ? <div className="tree-wrap"><FileTree node={fileTree} excludedPaths={excludedPaths} onToggle={handleToggleExcluded} /></div> : <div className="placeholder">No project tree yet.</div>}
              </div>
            </aside>
            <section className="workspace">
              <div className="section-title">Project Setup</div>
              <div className="detail-panel">
                <div className="block-header"><div className="block-title">1. Audit Folder & Exclusions</div><div className="block-subtitle">Pick a project root and control which internal folders are skipped.</div></div>
                <div className="control-grid"><label><span>Audit folder path</span><input value={auditPath} onChange={(event) => setAuditPath(event.target.value)} /></label></div>
                {workspacePath ? <div className="helper-text">Builder workspace (skills, MCP, pipelines, config): <code>{workspacePath}</code></div> : null}
                <div className="button-row"><button type="button" onClick={handleProjectSelect}>Choose folder</button><button type="button" className="button-secondary" onClick={() => handleTreeRefresh()}>Refresh tree</button><button type="button" className="button-secondary" onClick={handleRefreshCatalogs}>Refresh catalogs</button></div>
                <div className="helper-text">Excluded folders become semi-transparent and receive an exclusion badge in the tree.</div>
              </div>
              <div className="section-title">LLM Models</div>
              <div className="detail-panel"><div className="block-header block-header--row"><div><div className="block-title">2. Providers & Configured Models</div><div className="block-subtitle">Model availability and access status are read directly from the local config file.</div></div><div className="block-header__actions"><SectionRefreshButton onClick={handleRefreshProviders} loading={sectionRefreshState.providers} label="Refresh providers & configured models" /><button type="button" className="button-secondary" onClick={handleOpenConfig}>Open config</button></div></div><div className="info-options-grid info-options-grid--providers-three">{providers.map((provider) => <div className="info-option-card" key={provider.id}><div className="info-option-card__title">{provider.label}</div><div className="docs-panel__meta"><span className="pill">{provider.enabled ? "Enabled" : "Disabled"}</span><span className="pill">{provider.requiresAuth ? (provider.hasApiKey ? "API key configured" : "API key missing") : "No API key required"}</span></div><div className="helper-text">Base URL: <code>{provider.baseUrl || "-"}</code></div><InfoOptionSelect ariaLabel={`${provider.label}: available models (informational)`} emptyLabel="No models" options={provider.models.map((model) => ({ value: model, label: model }))} /></div>)}</div></div>
              <div className="section-title">Resources</div>
              <div className="detail-panel"><div className="block-header block-header--row"><div><div className="block-title">3. Skills, MCP, Audit Docs, Tooling, Patterns</div><div className="block-subtitle">Items discovered in the builder workspace (informational).</div></div><SectionRefreshButton onClick={handleRefreshResources} loading={sectionRefreshState.resources} label="Refresh workspace resources" /></div><div className="info-options-grid">{resourceCards.map((card) => <div className="info-option-card" key={card.key}><div className="info-option-card__header"><div className="info-option-card__title">{card.label}</div><div className="info-option-card__count" aria-label={`${card.label}: ${card.count} items`}>{card.count}</div></div><InfoOptionSelect ariaLabel={`${card.label}: available items (informational)`} emptyLabel="No items" options={card.options} /></div>)}</div></div>
              <div className="detail-panel">
                <div className="block-header block-header--row">
                  <div>
                    <div className="block-title">4. Docs</div>
                    <div className="block-subtitle">Prepare audit documentation embeddings for retrieval augmentation.</div>
                  </div>
                  <div className="block-header__actions">
                    <SectionRefreshButton onClick={handleRefreshDocsStatus} loading={sectionRefreshState.docs} label="Refresh docs status" />
                    <button
                      type="button"
                      className="button-secondary"
                      onClick={handlePrepareDocs}
                      disabled={isPreparingDocs || docsStatus.md_file_count === 0}
                    >
                      {isPreparingDocs ? "Preparing..." : "Prepare docs"}
                    </button>
                  </div>
                </div>
                <div className="docs-panel__meta">
                  <span className="pill">{docsStatus.md_file_count} md files found</span>
                  <span className="pill">{docsStatus.chunk_count} chunks indexed</span>
                  <span className="pill">{docsStatus.model}</span>
                </div>
                {docsStatus.prepared ? (
                  <div className="docs-panel__success">
                    All documentation has been processed successfully.
                  </div>
                ) : (
                  <div className="helper-text">
                    {docsStatus.md_file_count > 0
                      ? "Run Prepare docs to chunk and index the documentation."
                      : "No markdown files were found in audit_docs."}
                  </div>
                )}
              </div>
              <div className="detail-panel"><div className="block-header"><div className="block-title">5. Audit Mode</div><div className="block-subtitle">Choose how the audit workload should be split across the flow.</div></div><div className="audit-mode-grid"><label className={`audit-mode-card ${auditMode === "contract" ? "selected" : ""}`}><input type="radio" name="audit-mode" value="contract" checked={auditMode === "contract"} onChange={(event) => setAuditMode(event.target.value)} /><span className="audit-mode-card__title">Audit by Contract</span><span className="audit-mode-card__text">This mode will let agents audit protocol contract by contract</span></label><label className={`audit-mode-card ${auditMode === "cluster" ? "selected" : ""}`}><input type="radio" name="audit-mode" value="cluster" checked={auditMode === "cluster"} onChange={(event) => setAuditMode(event.target.value)} /><span className="audit-mode-card__title">Audit by Cluster</span><span className="audit-mode-card__text">This mode will let agents audit protocol by function clusters</span><button type="button" className="button-secondary audit-mode-card__button" onClick={handleGenerateClusters} disabled={isGeneratingClusters}>{isGeneratingClusters ? "Generating..." : "Generate clusters"}</button></label></div><div className="cluster-summary-wrap"><ClusterSummary clusters={generatedClusters} stats={clusterStats} onRemoveCluster={handleRemoveCluster} /></div></div>
            </section>
          </div>
        ) : null}
        {builderTab === "pipeline" ? <section className="workspace workspace--flow-tab" role="tabpanel" aria-label="Pipeline"><div className="section-title">Pipeline Builder</div><div className="detail-panel"><div className="block-header block-header--row"><div><div className="block-title">6. Agent Flow Board</div><div className="block-subtitle">Create custom audit pipelines from agent, patterns, memory and code blocks.</div></div><div className="status-text">{status}</div></div><div className="flow-toolbar"><div className="pipeline-controls"><label className="pipeline-controls__field"><span>Pipeline name</span><input value={pipelineName} onChange={(event) => setPipelineName(event.target.value)} /></label><button type="button" onClick={handleSavePipeline}>Save pipeline</button><div className="pipeline-controls__divider" /><label className="pipeline-controls__field"><span>Saved pipelines</span><select value={selectedPipeline} onChange={(event) => setSelectedPipeline(event.target.value)}><option value="">Select pipeline</option>{pipelines.map((pipeline) => <option key={pipeline.path} value={pipeline.name}>{pipeline.name}</option>)}</select></label><button type="button" className="button-secondary" onClick={handleLoadPipeline}>Load pipeline</button></div><div className="agent-picker-panel"><div className="agent-picker-title">Agent Blocks</div><div className="agent-picker-subtitle">Add a block and configure it directly on the canvas.</div><div className="button-row agent-picker-row"><button type="button" onClick={() => handleAddNode("agent")}>Add agent</button><button type="button" onClick={() => handleAddNode("patterns")}>Add patterns</button><button type="button" onClick={() => handleAddNode("memory")}>Add memory</button><button type="button" onClick={() => handleAddNode("code")}>Add code</button><button type="button" className="button-secondary" onClick={() => setUndoDrawerOpen(true)} title="Reverse recent canvas mutations">Undo…</button><button type="button" className="button-secondary" onClick={() => setAuditDrawerOpen(true)} title="Show canvas audit log">Audit log</button></div></div></div><div className="flow-canvas"><ReactFlow nodes={flowNodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onConnect={onConnect} onMoveEnd={(_, nextViewport) => setViewport(nextViewport)} fitView><MiniMap zoomable pannable style={{ background: "#0d1117", border: "1px solid #30363d" }} /><Controls /><Background color="#21262d" gap={20} /></ReactFlow></div></div></section> : null}
        {builderTab === "audit" ? <section className="workspace workspace--flow-tab" role="tabpanel" aria-label="Audit">
          <div className="section-title">Audit process</div>
          <div className="detail-panel">
            <div className="audit-tab-actions">
              {auditRunStatus === "running" ? (
                <button type="button" className="button-danger" onClick={handleStopAudit}>Stop Audit</button>
              ) : (
                <button type="button" onClick={handleStartAudit} disabled={auditRunStatus === "starting"}>
                  {auditRunStatus === "starting" ? "Starting..." : "Start Audit"}
                </button>
              )}
              {auditRunStatus ? <span className={`audit-run-badge audit-run-badge--${auditRunStatus}`}>{auditRunStatus}</span> : null}
            </div>
            <div className="block-header block-header--row">
              <div><div className="block-title">7. Audit process</div><div className="block-subtitle">Read-only view of the current Pipeline canvas with live execution logs.</div></div>
              <div className="status-text">{status}</div>
            </div>
            <div className="audit-process-layout">
              <div className="audit-process-panel audit-process-panel--logs">
                <div className="block-header">
                  <div className="block-title">Audit logs</div>
                  <div className="block-subtitle">{visibleAuditEvents.length ? `${visibleAuditEvents.length} events` : "Logs for the current run will appear here."}</div>
                </div>
                <div className="audit-log-list" ref={auditLogListRef}>
                  {visibleAuditEvents.length ? visibleAuditEvents.map((evt) => (
                    <div key={evt.key} className={`audit-log-entry audit-log-entry--${evt.tone}`}>
                      <span className="audit-log-entry__time">{evt.time}</span>
                      <span className={`audit-log-entry__badge audit-log-entry__badge--${evt.tone}`}>{evt.badge}</span>
                      <span className="audit-log-entry__detail">{evt.detail}</span>
                    </div>
                  )) : <div className="audit-log-empty">No events yet.</div>}
                </div>
              </div>
              <div className="audit-process-panel audit-process-panel--canvas">
                <div className="flow-canvas">
                  <ReactFlow nodes={auditFlowNodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={() => {}} onEdgesChange={() => {}} onConnect={undefined} onMoveEnd={(_, nextViewport) => setViewport(nextViewport)} nodesDraggable={false} nodesConnectable={false} elementsSelectable={false} nodesFocusable={false} edgesFocusable={false} edgesUpdatable={false} deleteKeyCode={null} defaultViewport={viewport} minZoom={0.1} maxZoom={4}>
                    <MiniMap zoomable pannable style={{ background: "#0d1117", border: "1px solid #30363d" }} />
                    <Controls />
                    <Background color="#21262d" gap={20} />
                  </ReactFlow>
                </div>
              </div>
            </div>
          </div>
        </section> : null}
        {builderTab === "editor" ? <MonacoPane workspaceTree={workspaceFileTree} workspacePath={workspacePath} pendingOpen={pendingEditorOpen} /> : null}
          </div>
          <ChatPanel
            visible={chatPanelVisible}
            uiContextRef={{ current: getUiContext }}
            providers={providers}
            onApplyCanvasAction={canvasDispatcher}
            bumpCanvasRevision={bumpCanvasRevision}
            onOpenInEditor={handleOpenInEditor}
            workspacePaths={workspacePathSet}
            currentTab={builderTab}
            chatPanelWidth={chatPanelWidth}
            onChatPanelWidthChange={handleChatPanelWidthChange}
          />
        </div>
      </main>
      <AuditLogDrawer
        open={auditDrawerOpen}
        onClose={() => setAuditDrawerOpen(false)}
        refreshTick={auditRefreshTick}
      />
      <CanvasUndoDrawer
        open={undoDrawerOpen}
        onClose={() => setUndoDrawerOpen(false)}
        onApplyAction={undoCanvasDispatcher}
        refreshTick={auditRefreshTick}
        onAfterUndo={() => setAuditRefreshTick((t) => t + 1)}
      />
    </div>
  );
}
