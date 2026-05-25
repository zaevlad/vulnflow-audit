import { useEffect, useRef } from "react";
import { Message } from "./Message.jsx";

export function MessageList({
  messages,
  streamingMessage,
  onSendPrompt,
  isStreaming,
  onOpenInEditor,
  workspacePaths,
  highlightMessageId,
  onHighlightConsumed,
}) {
  const endRef = useRef(null);
  const containerRef = useRef(null);
  const lastHighlightAppliedRef = useRef(null);

  useEffect(() => {
    if (highlightMessageId) return; // search-jumps take precedence
    if (endRef.current) {
      endRef.current.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [messages, streamingMessage, isStreaming, highlightMessageId]);

  // Search-hit jumps: scroll the matched message into view and flash a
  // highlight class for ~2.5s, then clear so subsequent renders go
  // back to the normal end-scroll behavior.
  useEffect(() => {
    if (!highlightMessageId) return;
    const root = containerRef.current;
    if (!root) return;
    // Wait a tick so the message DOM is mounted after thread switch.
    const timer = setTimeout(() => {
      const target = root.querySelector(
        `[data-message-id="${CSS.escape(highlightMessageId)}"]`,
      );
      if (target) {
        target.scrollIntoView({ behavior: "smooth", block: "center" });
        target.classList.add("chat-message--highlighted");
        lastHighlightAppliedRef.current = target;
      }
      const clear = setTimeout(() => {
        if (lastHighlightAppliedRef.current) {
          lastHighlightAppliedRef.current.classList.remove("chat-message--highlighted");
          lastHighlightAppliedRef.current = null;
        }
        onHighlightConsumed?.();
      }, 2500);
      return () => clearTimeout(clear);
    }, 80);
    return () => clearTimeout(timer);
  }, [highlightMessageId, messages, onHighlightConsumed]);

  return (
    <div className="chat-message-list" ref={containerRef}>
      {messages.map((message) => (
        <Message
          key={message.id}
          message={message}
          onSendPrompt={onSendPrompt}
          onOpenInEditor={onOpenInEditor}
          workspacePaths={workspacePaths}
        />
      ))}
      {streamingMessage ? (
        <Message
          message={streamingMessage}
          onSendPrompt={onSendPrompt}
          onOpenInEditor={onOpenInEditor}
          workspacePaths={workspacePaths}
        />
      ) : null}
      {isStreaming && !streamingMessage ? (
        <div className="chat-message chat-message--assistant">
          <div className="chat-typing">
            <span /><span /><span />
          </div>
        </div>
      ) : null}
      <div ref={endRef} />
    </div>
  );
}
