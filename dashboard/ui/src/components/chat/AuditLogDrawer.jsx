// Outslides over the canvas to show the immutable audit log of canvas
// mutations. Records come from /api/canvas/audit — both agent and
// manual user mutations land here.

import { useCallback, useEffect, useMemo, useState } from "react";

const SOURCE_OPTIONS = [
  { value: "", label: "All" },
  { value: "agent", label: "Agent" },
  { value: "user", label: "Manual" },
];

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

async function fetchAuditRecords({ limit, source }) {
  const params = new URLSearchParams();
  params.set("limit", String(limit));
  if (source) params.set("source", source);
  const res = await fetch(`/api/canvas/audit?${params.toString()}`);
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try { const j = await res.json(); if (j?.detail) detail = String(j.detail); } catch {}
    throw new Error(detail);
  }
  const body = await res.json();
  return body.records || [];
}

export function AuditLogDrawer({ open, onClose, refreshTick }) {
  const [records, setRecords] = useState([]);
  const [sourceFilter, setSourceFilter] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const reload = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await fetchAuditRecords({ limit: 100, source: sourceFilter });
      setRecords(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [sourceFilter]);

  useEffect(() => {
    if (open) reload();
  }, [open, reload, refreshTick]);

  const grouped = useMemo(() => groupByDay(records), [records]);

  if (!open) return null;

  return (
    <aside className="audit-drawer" role="dialog" aria-label="Canvas audit log">
      <header className="audit-drawer__header">
        <div className="audit-drawer__title">Canvas audit log</div>
        <div className="audit-drawer__actions">
          <select
            value={sourceFilter}
            onChange={(e) => setSourceFilter(e.target.value)}
            aria-label="Filter by source"
          >
            {SOURCE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
          <button type="button" onClick={reload} disabled={loading} title="Refresh">↻</button>
          <button type="button" onClick={onClose} title="Close">✕</button>
        </div>
      </header>

      {error ? <div className="audit-drawer__error">{error}</div> : null}

      <div className="audit-drawer__body">
        {loading && !records.length ? (
          <div className="audit-drawer__loading">Loading…</div>
        ) : null}
        {!loading && !records.length ? (
          <div className="audit-drawer__empty">No canvas changes recorded yet.</div>
        ) : null}
        {grouped.map(({ day, items }) => (
          <section key={day} className="audit-drawer__group">
            <div className="audit-drawer__group-day">{day}</div>
            <ul className="audit-drawer__list">
              {items.map((record) => (
                <AuditRecordRow key={record.id} record={record} />
              ))}
            </ul>
          </section>
        ))}
      </div>
    </aside>
  );
}

function AuditRecordRow({ record }) {
  const [expanded, setExpanded] = useState(false);
  const kindLabel = KIND_LABEL[record.action_kind] || record.action_kind;
  const time = formatTime(record.applied_at);

  return (
    <li className={`audit-record audit-record--${record.source}`}>
      <button
        type="button"
        className="audit-record__head"
        onClick={() => setExpanded((v) => !v)}
      >
        <span className="audit-record__time">{time}</span>
        <span className={`audit-record__source audit-record__source--${record.source}`}>
          {record.source}
        </span>
        <span className="audit-record__kind">{kindLabel}</span>
        <span className="audit-record__id">
          {summarizeAction(record)}
        </span>
        <span className="audit-record__rev">
          r{record.revision_before}→r{record.revision_after}
        </span>
        <span className="audit-record__chev" aria-hidden>{expanded ? "▾" : "▸"}</span>
      </button>
      {expanded ? (
        <div className="audit-record__detail">
          <DetailBlock label="Action" payload={record.action} />
          <DetailBlock label="Before" payload={record.snapshot_before} />
          <DetailBlock label="After" payload={record.snapshot_after} />
          {record.reason ? (
            <DetailBlock label="Reason" payload={record.reason} />
          ) : null}
          {record.thread_id ? (
            <DetailBlock label="Thread" payload={record.thread_id} />
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function DetailBlock({ label, payload }) {
  if (payload == null) return null;
  const text = typeof payload === "string" ? payload : JSON.stringify(payload, null, 2);
  if (!text || text === "{}" || text === "null") return null;
  return (
    <div className="audit-record__block">
      <div className="audit-record__block-label">{label}</div>
      <pre className="audit-record__block-body">{text}</pre>
    </div>
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

function groupByDay(records) {
  const map = new Map();
  for (const record of records) {
    const day = (record.applied_at || "").slice(0, 10);
    if (!map.has(day)) map.set(day, []);
    map.get(day).push(record);
  }
  return Array.from(map.entries()).map(([day, items]) => ({ day, items }));
}

function formatTime(iso) {
  if (!iso) return "";
  const t = iso.split("T")[1] || "";
  return t.slice(0, 5);
}
