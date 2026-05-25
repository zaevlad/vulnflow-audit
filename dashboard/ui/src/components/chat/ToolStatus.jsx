// Live status badge for tool calls.
//
// Adapted from misc/OpenGenerativeUI tool-rendering pattern (useDefaultRenderTool
// + ToolReasoning). Status transitions: pending → running → completed | failed.

const STATUS_LABEL = {
  pending: "Queued",
  running: "Running",
  completed: "Done",
  failed: "Failed",
};

export function ToolStatus({ name, status, arguments: args, output, error }) {
  const summary = summarizeArguments(args);
  const isError = status === "failed";
  const rateLimited = output && output.ok === false && output.error === "rate_limited";

  return (
    <div className={`chat-tool chat-tool--${status || "pending"}`}>
      <div className="chat-tool__head">
        <span className="chat-tool__dot" aria-hidden />
        <span className="chat-tool__name">{name}</span>
        {rateLimited ? (
          <span className="chat-tool__pill chat-tool__pill--rate-limited">
            rate-limited · retry in {output.retry_after}s
          </span>
        ) : null}
        <span className="chat-tool__status">{STATUS_LABEL[status] || status}</span>
      </div>
      {summary ? <div className="chat-tool__summary">{summary}</div> : null}
      {isError && error ? <div className="chat-tool__error">{error}</div> : null}
      {output && status === "completed" && !isError ? (
        <ToolOutputPreview name={name} output={output} />
      ) : null}
    </div>
  );
}

function summarizeArguments(args) {
  if (!args || typeof args !== "object") return "";
  const entries = Object.entries(args);
  if (!entries.length) return "";
  return entries
    .slice(0, 3)
    .map(([key, value]) => {
      const printable = typeof value === "string" ? value : JSON.stringify(value);
      const trimmed = printable.length > 60 ? `${printable.slice(0, 60)}…` : printable;
      return `${key}: ${trimmed}`;
    })
    .join("  ·  ");
}

function ToolOutputPreview({ name, output }) {
  if (!output || typeof output !== "object") return null;
  if (name === "search_docs" && Array.isArray(output.chunks)) {
    return (
      <ul className="chat-tool__chunks">
        {output.chunks.slice(0, 3).map((chunk, idx) => (
          <li key={idx}>
            <code>{chunk.rel_path}</code>
            <span className="chat-tool__similarity">
              {Math.round((chunk.similarity || 0) * 100)}%
            </span>
          </li>
        ))}
      </ul>
    );
  }
  if (name === "read_workspace_file" && output.rel_path) {
    const redactionCount = Array.isArray(output.redactions) ? output.redactions.length : 0;
    return (
      <div className="chat-tool__file">
        <code>{output.rel_path}</code>
        <span>{output.bytes} bytes{output.truncated ? " (truncated)" : ""}</span>
        {redactionCount > 0 ? (
          <RedactionPill redactions={output.redactions} />
        ) : null}
      </div>
    );
  }
  if (name === "list_workspace_directory" && Array.isArray(output.entries)) {
    const hidden = Number(output.hidden_by_secret_policy) || 0;
    return (
      <div className="chat-tool__file">
        <code>{output.rel_path}</code>
        <span>{output.entries.length} entries{output.truncated ? "+" : ""}</span>
        {hidden > 0 ? (
          <span className="chat-tool__pill chat-tool__pill--redacted" title="Hidden by secret-bearing path policy">
            {hidden} hidden
          </span>
        ) : null}
      </div>
    );
  }
  return null;
}

function RedactionPill({ redactions }) {
  const kinds = redactions.reduce((acc, r) => {
    acc[r.kind] = (acc[r.kind] || 0) + 1;
    return acc;
  }, {});
  const summary = Object.entries(kinds)
    .map(([kind, count]) => `${kind}${count > 1 ? ` ×${count}` : ""}`)
    .join(", ");
  return (
    <span
      className="chat-tool__pill chat-tool__pill--redacted"
      title={`Redacted: ${summary}`}
    >
      {redactions.length} secret{redactions.length > 1 ? "s" : ""} redacted
    </span>
  );
}
