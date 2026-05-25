// Slides over the canvas to expose the recent agent / manual mutations
// captured in canvas_audit_log, with one-click undo.
//
// Two modes per record:
//   - "Undo this"          → POST /api/canvas/undo {mode: "single"} —
//                             reverses just that single record.
//   - "Undo to this point" → POST /api/canvas/undo {mode: "batch_to"} —
//                             reverses every record applied at or after
//                             the chosen one (newest first).
//
// The backend returns a list of structured canvas actions; we dispatch
// them locally through the supplied `onApplyAction` (typically the
// `manualCanvasDispatcher` from App.jsx, which bypasses the agent
// provenance marker so the resulting state-change is itself recorded
// as a normal manual_batch — preserving an audit trail of the undo).

import { useCallback, useEffect, useMemo, useState } from "react";

const KIND_LABEL = {
  create_node: "Create node",
  delete_node: "Delete node",
  update_node: "Update node",
  create_edge: "Create edge",
  delete_edge: "Delete edge",
  update_edge: "Update edge",
  set_viewport: "Set viewport",
  manual_batch: "Manual edits batch",
};

const REVERSIBLE_KINDS = new Set([
  "create_node",
  "delete_node",
  "update_node",
  "create_edge",
  "delete_edge",
  "update_edge",
  "manual_batch",
]);

async function fetchRecent({ limit }) {
  const params = new URLSearchParams();
  params.set("limit", String(limit));
  const res = await fetch(`/api/canvas/audit?${params.toString()}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const body = await res.json();
  return body.records || [];
}

async function postUndo(payload) {
  const res = await fetch("/api/canvas/undo", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try { const j = await res.json(); if (j?.detail) detail = String(j.detail); } catch {}
    throw new Error(detail);
  }
  return res.json();
}

export function CanvasUndoDrawer({
  open,
  onClose,
  onApplyAction,
  refreshTick,
  onAfterUndo,
}) {
  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await fetchRecent({ limit: 100 });
      setRecords(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) reload();
  }, [open, reload, refreshTick]);

  const handleUndo = useCallback(async (recordId, mode) => {
    setBusyId(`${mode}:${recordId}`);
    setError("");
    try {
      const result = await postUndo({ audit_log_id: recordId, mode });
      const actions = Array.isArray(result.actions) ? result.actions : [];
      let appliedCount = 0;
      for (const action of actions) {
        try {
          const ok = onApplyAction?.(action);
          if (ok) appliedCount += 1;
        } catch (dispatchErr) {
          setError(`Apply failed: ${dispatchErr.message}`);
        }
      }
      if (!appliedCount && actions.length) {
        setError("Dispatched actions but none applied (canvas already matches?).");
      }
      onAfterUndo?.({ appliedCount, total: actions.length, mode, recordId });
      await reload();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyId(null);
    }
  }, [onApplyAction, onAfterUndo, reload]);

  if (!open) return null;

  return (
    <aside className="undo-drawer" role="dialog" aria-label="Canvas undo">
      <header className="undo-drawer__header">
        <div className="undo-drawer__title">Recent canvas actions</div>
        <div className="undo-drawer__actions">
          <button type="button" onClick={reload} disabled={loading} title="Refresh">↻</button>
          <button type="button" onClick={onClose} title="Close">✕</button>
        </div>
      </header>

      {error ? <div className="undo-drawer__error">{error}</div> : null}

      <div className="undo-drawer__body">
        {loading && !records.length ? (
          <div className="undo-drawer__loading">Loading…</div>
        ) : null}
        {!loading && !records.length ? (
          <div className="undo-drawer__empty">No mutations recorded yet.</div>
        ) : null}
        <ul className="undo-drawer__list">
          {records.map((record) => (
            <UndoRecordRow
              key={record.id}
              record={record}
              busyId={busyId}
              onUndoSingle={() => handleUndo(record.id, "single")}
              onUndoBatch={() => handleUndo(record.id, "batch_to")}
            />
          ))}
        </ul>
      </div>
    </aside>
  );
}

function UndoRecordRow({ record, busyId, onUndoSingle, onUndoBatch }) {
  const summary = useMemo(() => summarizeAction(record), [record]);
  const time = formatTime(record.applied_at);
  const reversible = REVERSIBLE_KINDS.has(record.action_kind);
  const isBusy = busyId === `single:${record.id}` || busyId === `batch_to:${record.id}`;

  return (
    <li className={`undo-record undo-record--${record.source}`}>
      <div className="undo-record__head">
        <span className="undo-record__time">{time}</span>
        <span className={`undo-record__source undo-record__source--${record.source}`}>
          {record.source}
        </span>
        <span className="undo-record__kind">
          {KIND_LABEL[record.action_kind] || record.action_kind}
        </span>
        <span className="undo-record__summary">{summary}</span>
        <span className="undo-record__rev">
          r{record.revision_before}→r{record.revision_after}
        </span>
      </div>
      <div className="undo-record__buttons">
        {reversible ? (
          <>
            <button
              type="button"
              className="undo-record__btn"
              disabled={isBusy}
              onClick={onUndoSingle}
              title="Reverse just this single action"
            >
              {busyId === `single:${record.id}` ? "Undoing…" : "Undo this"}
            </button>
            <button
              type="button"
              className="undo-record__btn undo-record__btn--secondary"
              disabled={isBusy}
              onClick={onUndoBatch}
              title="Reverse every change applied after this one (inclusive)"
            >
              {busyId === `batch_to:${record.id}` ? "Undoing…" : "Undo to this point"}
            </button>
          </>
        ) : (
          <span className="undo-record__disabled">not reversible</span>
        )}
      </div>
    </li>
  );
}

function summarizeAction(record) {
  const a = record.action || {};
  if (record.action_kind === "create_node" || record.action_kind === "update_node") return a.node_id || "";
  if (record.action_kind === "delete_node") return a.node_id || "";
  if (record.action_kind === "create_edge") return `${a.source || "?"} → ${a.target || "?"}`;
  if (record.action_kind === "delete_edge" || record.action_kind === "update_edge") return a.edge_id || "";
  if (record.action_kind === "manual_batch") {
    const before = record.snapshot_before || {};
    const after = record.snapshot_after || {};
    const nb = (before.nodes || []).length;
    const eb = (before.edges || []).length;
    const na = (after.nodes || []).length;
    const ea = (after.edges || []).length;
    return `${nb}/${eb} → ${na}/${ea} nodes/edges`;
  }
  return "";
}

function formatTime(iso) {
  if (!iso) return "";
  const t = iso.split("T")[1] || "";
  return t.slice(0, 5);
}
