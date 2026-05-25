// Inline badge that reports the lifecycle of an agent-emitted canvas action.
//
// The action is applied immediately by the frontend dispatcher (see
// canvasDispatcher.js), but we surface the validation outcome here so
// the user can see what happened without leaving the chat panel.

const KIND_LABEL = {
  create_node: "Create node",
  delete_node: "Delete node",
  update_node: "Update node",
  create_edge: "Create edge",
  delete_edge: "Delete edge",
  update_edge: "Update edge",
  set_viewport: "Set viewport",
};

export function CanvasActionBadge({ action, rejected, reconciled, reason, applied }) {
  const kind = action?.kind || "";
  const label = KIND_LABEL[kind] || kind || "Canvas action";
  const tone = rejected ? "rejected" : applied ? "applied" : "queued";

  return (
    <div className={`chat-canvas chat-canvas--${tone}`}>
      <div className="chat-canvas__head">
        <span className="chat-canvas__icon" aria-hidden>⌘</span>
        <span className="chat-canvas__label">{label}</span>
        {reconciled ? <span className="chat-canvas__pill">rebased</span> : null}
        <span className="chat-canvas__pill chat-canvas__pill--state">{tone}</span>
      </div>
      {kind === "create_node" && action?.node_type ? (
        <div className="chat-canvas__detail">{action.node_type}{action.node_id ? ` · ${action.node_id}` : ""}</div>
      ) : null}
      {(kind === "delete_node" || kind === "update_node") && action?.node_id ? (
        <div className="chat-canvas__detail">{action.node_id}</div>
      ) : null}
      {kind === "create_edge" && action?.source ? (
        <div className="chat-canvas__detail">{action.source} → {action.target}</div>
      ) : null}
      {(kind === "delete_edge" || kind === "update_edge") && action?.edge_id ? (
        <div className="chat-canvas__detail">{action.edge_id}</div>
      ) : null}
      {rejected && reason ? <div className="chat-canvas__reason">{reason}</div> : null}
    </div>
  );
}
