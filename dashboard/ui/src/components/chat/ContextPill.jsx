// Compact pill that surfaces context-window state for the current turn.
// Sourced from the most recent `context_info` envelope part — either
// from the message currently being streamed or from the last persisted
// assistant message. If neither is present, the pill renders nothing.

function formatTokens(n) {
  if (!Number.isFinite(n)) return "?";
  if (n < 1000) return String(n);
  return `${(n / 1000).toFixed(n >= 10_000 ? 0 : 1)}k`;
}

function findLatestContextInfo(parts) {
  if (!Array.isArray(parts)) return null;
  for (let i = parts.length - 1; i >= 0; i--) {
    if (parts[i]?.type === "context_info") return parts[i];
  }
  return null;
}

export function ContextPill({ streamingParts, messages }) {
  let info = findLatestContextInfo(streamingParts);
  if (!info && Array.isArray(messages)) {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m?.role !== "assistant") continue;
      const found = findLatestContextInfo(m.parts);
      if (found) {
        info = found;
        break;
      }
    }
  }
  if (!info) return null;

  const used = info.input_tokens ?? 0;
  const max = info.max_input_tokens ?? 0;
  const ratio = max > 0 ? used / max : 0;
  const tone =
    ratio >= 0.95 ? "danger" : ratio >= 0.75 ? "warn" : "ok";

  const compactedSegment = info.compacted
    ? ` · ${info.compacted_messages} msgs compacted`
    : "";
  const errorSegment = info.summary_error ? " · summary fallback" : "";

  const tooltipLines = [
    `Input tokens: ${used} / ${max}`,
    `Kept verbatim: ${info.kept_messages} msgs`,
  ];
  if (info.compacted) {
    tooltipLines.push(`Compacted: ${info.compacted_messages} msgs`);
    tooltipLines.push(
      `Before compaction: ${info.input_tokens_before_compaction} tokens`,
    );
    if (info.summary_provider && info.summary_model) {
      tooltipLines.push(`Summary by: ${info.summary_provider}/${info.summary_model}`);
    }
  }
  if (info.summary_error) tooltipLines.push(`Summary error: ${info.summary_error}`);

  return (
    <span
      className={`chat-context-pill chat-context-pill--${tone}`}
      title={tooltipLines.join("\n")}
    >
      {formatTokens(used)} / {formatTokens(max)} tokens{compactedSegment}{errorSegment}
    </span>
  );
}
