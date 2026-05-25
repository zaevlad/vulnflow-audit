// Renders a single chat message — user or assistant.
// Assistant messages are composed of envelope parts (text / plan /
// tool_status / widget / canvas_action / error) which we group and
// render with the dedicated child components.

import { useMemo } from "react";
import { WidgetIframe } from "./WidgetIframe.jsx";
import { PlanCard } from "./PlanCard.jsx";
import { ToolStatus } from "./ToolStatus.jsx";
import { CanvasActionBadge } from "./CanvasActionBadge.jsx";
import { MarkdownRenderer } from "./MarkdownRenderer.jsx";

export function Message({ message, onSendPrompt, onOpenInEditor, workspacePaths }) {
  if (message.role === "user") {
    return (
      <div className="chat-message chat-message--user" data-message-id={message.id}>
        {Array.isArray(message.attachments) && message.attachments.length ? (
          <MessageAttachments attachments={message.attachments} />
        ) : null}
        {message.content ? (
          <div className="chat-message__bubble">{message.content}</div>
        ) : null}
      </div>
    );
  }

  const groupedParts = useMemo(() => groupParts(message.parts || []), [message.parts]);

  return (
    <div className="chat-message chat-message--assistant" data-message-id={message.id}>
      {groupedParts.map((part, idx) => (
        <PartRenderer
          key={idx}
          part={part}
          onSendPrompt={onSendPrompt}
          onOpenInEditor={onOpenInEditor}
          workspacePaths={workspacePaths}
        />
      ))}
      {(!message.parts || message.parts.length === 0) && message.content ? (
        <div className="chat-message__bubble">
          <MarkdownRenderer
            text={message.content}
            onOpenInEditor={onOpenInEditor}
            workspacePaths={workspacePaths}
          />
        </div>
      ) : null}
    </div>
  );
}

function PartRenderer({ part, onSendPrompt, onOpenInEditor, workspacePaths }) {
  if (!part) return null;
  if (part.type === "text") {
    if (!part.text) return null;
    return (
      <div className="chat-message__bubble">
        <MarkdownRenderer
          text={part.text}
          onOpenInEditor={onOpenInEditor}
          workspacePaths={workspacePaths}
        />
      </div>
    );
  }
  if (part.type === "plan") {
    return (
      <PlanCard
        approach={part.approach}
        technology={part.technology}
        keyElements={part.key_elements}
      />
    );
  }
  if (part.type === "tool_call_group") {
    return (
      <ToolStatus
        name={part.name}
        status={part.status}
        arguments={part.arguments}
        output={part.output}
        error={part.error}
      />
    );
  }
  if (part.type === "widget") {
    return <WidgetIframe title={part.title} html={part.html} onSendPrompt={onSendPrompt} />;
  }
  if (part.type === "canvas_action") {
    return (
      <CanvasActionBadge
        action={part.action}
        rejected={part.rejected}
        reconciled={part.reconciled}
        reason={part.reason}
        applied={part.applied}
      />
    );
  }
  if (part.type === "error") {
    return <div className="chat-error">{part.scope}: {part.message}</div>;
  }
  return null;
}

function MessageAttachments({ attachments }) {
  return (
    <div className="chat-message__attachments">
      {attachments.map((att) => {
        const url = `/api/chat/attachments/${encodeURIComponent(att.id)}/content`;
        if (att.kind === "image") {
          return (
            <a
              key={att.id}
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className="chat-message__attachment chat-message__attachment--image"
              title={att.filename}
            >
              <img src={url} alt={att.filename || "image"} loading="lazy" />
            </a>
          );
        }
        return (
          <a
            key={att.id}
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="chat-message__attachment chat-message__attachment--pdf"
            title={att.filename}
          >
            <span aria-hidden>📄</span>
            <span className="chat-message__attachment-name">
              {att.filename || `pdf-${att.id.slice(0, 6)}`}
            </span>
          </a>
        );
      })}
    </div>
  );
}

// Combine paired tool_status + tool_result envelope parts so we render
// each tool call as a single live badge with the final output.
function groupParts(parts) {
  const out = [];
  const toolsById = new Map();

  for (const part of parts) {
    if (part.type === "tool_status") {
      const existing = toolsById.get(part.call_id);
      if (existing) {
        existing.status = part.status;
        existing.arguments = part.arguments || existing.arguments;
        continue;
      }
      const group = {
        type: "tool_call_group",
        call_id: part.call_id,
        name: part.name,
        status: part.status,
        arguments: part.arguments || {},
        output: null,
        error: null,
      };
      toolsById.set(part.call_id, group);
      out.push(group);
      continue;
    }
    if (part.type === "tool_result") {
      const existing = toolsById.get(part.call_id);
      if (existing) {
        existing.output = part.output;
        existing.error = part.error || (part.ok === false ? "tool failed" : null);
        if (!part.ok) existing.status = "failed";
        continue;
      }
      out.push({
        type: "tool_call_group",
        call_id: part.call_id,
        name: part.name,
        status: part.ok ? "completed" : "failed",
        arguments: {},
        output: part.output,
        error: part.error,
      });
      continue;
    }
    out.push(part);
  }
  return out;
}
