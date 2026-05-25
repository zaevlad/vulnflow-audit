// Right-side persistent chat panel.
//
// Driven by the backend /api/chat/send SSE stream. Maintains:
//   - the active thread + history (loaded from SQLite via /api/chat/threads/{id}/messages)
//   - the in-flight assistant message being streamed
//   - provider/model selection (persisted in localStorage)
//   - dispatch of canvas_action envelope parts to ReactFlow via applyCanvasAction
//   - widget render via WidgetIframe (sandboxed)
//   - a FIFO queue of additional prompts that arrive while another turn
//     is still streaming. Each queued prompt rebuilds a fresh
//     ui_context snapshot at dispatch time so the agent never sees a
//     stale canvas.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ContextPill } from "./ContextPill.jsx";
import { MessageList } from "./MessageList.jsx";
import { ModelSelector } from "./ModelSelector.jsx";
import { ThreadSidebar } from "./ThreadSidebar.jsx";
import { streamChatSend } from "./useChatStream.js";
import { slugify, threadToMarkdown, triggerDownload } from "./exportUtils.js";
import {
  classifyFile,
  deleteAttachment,
  humanFileSize,
  supportsVision,
  uploadAttachment,
} from "./chatAttachments.js";

async function fetchJson(url, options = {}) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch {}
    throw new Error(detail);
  }
  return res.json();
}

const CHAT_PANEL_WIDTH_MIN = 280;
const CHAT_PANEL_WIDTH_MAX = 720;

export function ChatPanel({
  visible,
  uiContextRef,
  providers,
  onApplyCanvasAction,
  bumpCanvasRevision,
  onOpenInEditor,
  workspacePaths,
  currentTab,
  chatPanelWidth = 400,
  onChatPanelWidthChange,
}) {
  const [threads, setThreads] = useState([]);
  const [activeThreadId, setActiveThreadId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [model, setModel] = useState(null);
  const [streamingParts, setStreamingParts] = useState([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState("");
  const [threadsOpen, setThreadsOpen] = useState(false);
  const [pendingCount, setPendingCount] = useState(0);
  // Files staged in the composer awaiting send. Each entry is the
  // server's AttachmentRecord (id + metadata) — bytes already on disk.
  const [pendingAttachments, setPendingAttachments] = useState([]);
  const [isDragging, setIsDragging] = useState(false);
  const [uploadingCount, setUploadingCount] = useState(0);
  const [highlightMessageId, setHighlightMessageId] = useState(null);

  const abortRef = useRef(null);
  const widgetIdMapRef = useRef(new Map());
  const appliedActionRef = useRef(new Set());
  const streamingRef = useRef(false);
  const pendingPromptsRef = useRef([]);
  const filePickerRef = useRef(null);
  const dragDepthRef = useRef(0);
  const resizeRef = useRef({ active: false, startX: 0, startWidth: 0 });
  const [isResizingChat, setIsResizingChat] = useState(false);

  const handleResizePointerDown = useCallback((event) => {
    if (!onChatPanelWidthChange || window.matchMedia("(max-width: 900px)").matches) return;
    event.preventDefault();
    resizeRef.current = {
      active: true,
      startX: event.clientX,
      startWidth: chatPanelWidth,
    };
    setIsResizingChat(true);
    document.documentElement.classList.add("chat-panel-resizing");
    event.currentTarget.setPointerCapture(event.pointerId);
  }, [chatPanelWidth, onChatPanelWidthChange]);

  const handleResizePointerMove = useCallback((event) => {
    if (!resizeRef.current.active || !onChatPanelWidthChange) return;
    const delta = resizeRef.current.startX - event.clientX;
    const next = Math.min(
      CHAT_PANEL_WIDTH_MAX,
      Math.max(CHAT_PANEL_WIDTH_MIN, resizeRef.current.startWidth + delta),
    );
    onChatPanelWidthChange(next);
  }, [onChatPanelWidthChange]);

  const finishResize = useCallback((event) => {
    if (!resizeRef.current.active) return;
    resizeRef.current.active = false;
    setIsResizingChat(false);
    document.documentElement.classList.remove("chat-panel-resizing");
    try {
      event.currentTarget.releasePointerCapture(event.pointerId);
    } catch {
      /* ignore */
    }
    window.dispatchEvent(new Event("resize"));
  }, []);

  // Load thread list on mount
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const payload = await fetchJson("/api/chat/threads");
        if (cancelled) return;
        const list = payload.threads || [];
        setThreads(list);
        if (!activeThreadId && list.length) {
          setActiveThreadId(list[0].id);
        }
      } catch (err) {
        if (!cancelled) setError(err.message);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Load messages whenever active thread changes
  useEffect(() => {
    if (!activeThreadId) {
      setMessages([]);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const payload = await fetchJson(`/api/chat/threads/${activeThreadId}/messages`);
        if (cancelled) return;
        setMessages(payload.messages || []);
        appliedActionRef.current = new Set(); // reset per thread
      } catch (err) {
        if (!cancelled) setError(err.message);
      }
    })();
    return () => { cancelled = true; };
  }, [activeThreadId]);

  const refreshThreads = useCallback(async () => {
    try {
      const payload = await fetchJson("/api/chat/threads");
      setThreads(payload.threads || []);
    } catch (err) {
      setError(err.message);
    }
  }, []);

  const handleCreateThread = useCallback(async (defaultTab) => {
    try {
      const body = defaultTab ? { default_tab: defaultTab } : {};
      const payload = await fetchJson("/api/chat/threads", {
        method: "POST",
        body: JSON.stringify(body),
      });
      const created = payload.thread;
      if (created) {
        setThreads((prev) => [created, ...prev.filter((t) => t.id !== created.id)]);
        setActiveThreadId(created.id);
      }
    } catch (err) {
      setError(err.message);
    }
  }, []);

  // Lazily ensure there's an active thread before uploading the first
  // attachment (the backend needs thread_id on the multipart payload).
  const ensureThread = useCallback(async () => {
    if (activeThreadId) return activeThreadId;
    try {
      const payload = await fetchJson("/api/chat/threads", {
        method: "POST",
        body: JSON.stringify({}),
      });
      const created = payload.thread;
      if (!created) throw new Error("Failed to create chat thread.");
      setThreads((prev) => [created, ...prev.filter((t) => t.id !== created.id)]);
      setActiveThreadId(created.id);
      return created.id;
    } catch (err) {
      setError(err.message);
      throw err;
    }
  }, [activeThreadId]);

  const ingestFiles = useCallback(async (files) => {
    const list = Array.from(files || []).filter(Boolean);
    if (!list.length) return;
    const valid = [];
    for (const f of list) {
      const kind = classifyFile(f);
      if (!kind) {
        setError(`Unsupported file type: ${f.type || f.name}`);
        continue;
      }
      valid.push(f);
    }
    if (!valid.length) return;

    if (valid.some((f) => classifyFile(f) === "image") && !supportsVision(model?.model)) {
      setError(
        `Selected model (${model?.model || "?"}) does not advertise vision support. ` +
        "Images will be skipped on the wire — pick a vision-capable model.",
      );
      // Continue uploading anyway — user may want to attach for review.
    }

    let threadId;
    try {
      threadId = await ensureThread();
    } catch {
      return;
    }

    setUploadingCount((n) => n + valid.length);
    for (const file of valid) {
      try {
        const record = await uploadAttachment(threadId, file);
        if (record) {
          setPendingAttachments((prev) => [...prev, record]);
        }
      } catch (err) {
        setError(err.message);
      } finally {
        setUploadingCount((n) => Math.max(0, n - 1));
      }
    }
  }, [ensureThread, model]);

  const removePendingAttachment = useCallback(async (id) => {
    setPendingAttachments((prev) => prev.filter((a) => a.id !== id));
    try {
      await deleteAttachment(id);
    } catch {
      // The row may have already been linked to a sent message — that's
      // fine, the UI just no longer references it.
    }
  }, []);

  const onFilePickerChange = useCallback((event) => {
    const files = event.target?.files;
    if (files?.length) ingestFiles(files);
    if (event.target) event.target.value = "";
  }, [ingestFiles]);

  const onDragEnter = useCallback((e) => {
    if (!e.dataTransfer?.types?.includes("Files")) return;
    e.preventDefault();
    dragDepthRef.current += 1;
    setIsDragging(true);
  }, []);
  const onDragLeave = useCallback((e) => {
    e.preventDefault();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) setIsDragging(false);
  }, []);
  const onDragOver = useCallback((e) => {
    if (e.dataTransfer?.types?.includes("Files")) e.preventDefault();
  }, []);
  const onDrop = useCallback((e) => {
    e.preventDefault();
    dragDepthRef.current = 0;
    setIsDragging(false);
    const files = e.dataTransfer?.files;
    if (files?.length) ingestFiles(files);
  }, [ingestFiles]);

  const onPaste = useCallback((e) => {
    const items = e.clipboardData?.items || [];
    const files = [];
    for (const item of items) {
      if (item.kind === "file") {
        const f = item.getAsFile();
        if (f) files.push(f);
      }
    }
    if (files.length) {
      e.preventDefault();
      ingestFiles(files);
    }
  }, [ingestFiles]);

  const handleSearchHit = useCallback((threadId, messageId) => {
    if (!threadId) return;
    setThreadsOpen(false);
    setHighlightMessageId(messageId || null);
    if (threadId !== activeThreadId) {
      setActiveThreadId(threadId);
    }
  }, [activeThreadId]);

  const handleChangeThreadTab = useCallback(async (threadId, nextTabValue) => {
    try {
      const defaultTab = nextTabValue === "any" ? null : nextTabValue;
      const payload = await fetchJson(`/api/chat/threads/${threadId}/tab`, {
        method: "PATCH",
        body: JSON.stringify({ default_tab: defaultTab }),
      });
      const updated = payload.thread;
      if (updated) {
        setThreads((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
      }
    } catch (err) {
      setError(err.message);
    }
  }, []);

  const handleDeleteThread = useCallback(async (threadId) => {
    if (!threadId) return;
    if (!window.confirm("Delete this conversation?")) return;
    try {
      await fetchJson(`/api/chat/threads/${threadId}`, { method: "DELETE" });
      setThreads((prev) => prev.filter((t) => t.id !== threadId));
      if (activeThreadId === threadId) {
        setActiveThreadId(null);
      }
    } catch (err) {
      setError(err.message);
    }
  }, [activeThreadId]);

  const handleRenameThread = useCallback(async (threadId, title) => {
    try {
      const payload = await fetchJson(`/api/chat/threads/${threadId}`, {
        method: "PATCH",
        body: JSON.stringify({ title }),
      });
      const updated = payload.thread;
      if (updated) {
        setThreads((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
      }
    } catch (err) {
      setError(err.message);
    }
  }, []);

  // Actually dispatch a prompt to the backend. Always rebuilds ui_context
  // from the current ref so a queued prompt sees the latest canvas.
  const runSend = useCallback(async (text) => {
    setError("");
    setStreamingParts([]);
    setIsStreaming(true);
    streamingRef.current = true;

    const optimisticUserMessage = {
      id: `local-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      role: "user",
      content: text,
      parts: [],
      thread_id: activeThreadId || "",
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, optimisticUserMessage]);

    const controller = new AbortController();
    abortRef.current = controller;

    const uiContext = uiContextRef.current ? uiContextRef.current() : {};

    // Snapshot the staged attachments — these are released from the
    // composer immediately so the next prompt can stage new ones while
    // this turn is still streaming.
    const attachmentsForTurn = pendingAttachments;
    setPendingAttachments([]);

    const payload = {
      thread_id: activeThreadId || null,
      message: text,
      ui_context: uiContext,
      model,
      attachment_ids: attachmentsForTurn.map((a) => a.id),
    };

    let liveThreadId = activeThreadId;

    const finalize = () => {
      setIsStreaming(false);
      streamingRef.current = false;
      abortRef.current = null;
      // Hand off to the next queued prompt, if any. The microtask
      // delay lets React flush the streaming=false render first.
      const next = pendingPromptsRef.current.shift();
      setPendingCount(pendingPromptsRef.current.length);
      if (next) {
        setTimeout(() => runSend(next), 0);
      }
    };

    await streamChatSend({
      payload,
      signal: controller.signal,
      onInit: (threadId) => {
        liveThreadId = threadId;
        if (!activeThreadId) setActiveThreadId(threadId);
      },
      onPart: (part) => {
        if (part?.type === "canvas_action") {
          const actionId = part.action_id;
          if (actionId && appliedActionRef.current.has(actionId)) return;
          if (!part.rejected && part.action) {
            try {
              const applied = onApplyCanvasAction?.(part.action);
              if (applied) {
                part.applied = true;
                bumpCanvasRevision?.();
              }
              if (actionId) appliedActionRef.current.add(actionId);
            } catch (err) {
              part.applied = false;
              part.reason = err.message;
            }
          }
        }
        setStreamingParts((prev) => [...prev, part]);
      },
      onDone: () => {
        if (liveThreadId) {
          (async () => {
            try {
              const refreshed = await fetchJson(`/api/chat/threads/${liveThreadId}/messages`);
              setMessages(refreshed.messages || []);
              setStreamingParts([]);
              await refreshThreads();
            } catch (err) {
              setError(err.message);
            }
          })();
        }
        finalize();
      },
      onError: (err) => {
        setError(err?.message || String(err));
        finalize();
      },
    });
  }, [activeThreadId, model, uiContextRef, onApplyCanvasAction, bumpCanvasRevision, refreshThreads]);

  // Public entry point — either dispatches immediately or queues for
  // when the current stream finishes.
  const sendMessage = useCallback((text) => {
    const trimmed = (text ?? draft).trim();
    if (!trimmed) return;
    if (!model?.provider || !model?.model) {
      setError("Pick a provider/model first.");
      return;
    }
    setDraft("");
    if (streamingRef.current) {
      pendingPromptsRef.current.push(trimmed);
      setPendingCount(pendingPromptsRef.current.length);
      return;
    }
    runSend(trimmed);
  }, [draft, model, runSend]);

  const handleCancel = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setIsStreaming(false);
    streamingRef.current = false;
  }, []);

  const cancelQueue = useCallback(() => {
    pendingPromptsRef.current = [];
    setPendingCount(0);
  }, []);

  const handleExportThread = useCallback(() => {
    if (!activeThreadId) {
      setError("Open a thread first.");
      return;
    }
    const thread = threads.find((t) => t.id === activeThreadId) || {
      id: activeThreadId,
      title: "chat-thread",
    };
    try {
      const md = threadToMarkdown(thread, messages);
      const filename = `${slugify(thread.title || "chat-thread")}-${activeThreadId.slice(0, 8)}.md`;
      triggerDownload(md, filename, "text/markdown");
    } catch (err) {
      setError(`Export failed: ${err.message}`);
    }
  }, [activeThreadId, threads, messages]);

  const streamingMessage = useMemo(() => {
    if (!streamingParts.length) return null;
    return {
      id: "streaming",
      role: "assistant",
      content: streamingParts.filter((p) => p.type === "text").map((p) => p.text).join(""),
      parts: streamingParts,
      thread_id: activeThreadId || "",
      created_at: new Date().toISOString(),
    };
  }, [streamingParts, activeThreadId]);

  const handleSendPromptFromWidget = useCallback((promptText) => {
    sendMessage(promptText);
  }, [sendMessage]);

  if (!visible) return null;

  return (
    <div className={`chat-panel-wrap ${isResizingChat ? "chat-panel-wrap--resizing" : ""}`}>
      {onChatPanelWidthChange ? (
        <div
          className="chat-panel-resize-handle"
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize chat panel"
          title="Drag to resize chat"
          onPointerDown={handleResizePointerDown}
          onPointerMove={handleResizePointerMove}
          onPointerUp={finishResize}
          onPointerCancel={finishResize}
        />
      ) : null}
      <div className="chat-panel__section-head">
        <button
          type="button"
          className="chat-panel__threads-btn"
          onClick={() => setThreadsOpen((v) => !v)}
          title="Conversations"
          aria-expanded={threadsOpen}
        >
          ☰
        </button>
        <div className="section-title chat-panel__section-title">AI Chat</div>
      </div>

      <aside className="chat-panel" aria-label="AI Chat">
        <header className="chat-panel__header">
          {currentTab && currentTab !== "pipeline" ? (
            <span
              className="chat-panel__readonly-badge"
              title={`Canvas mutations disabled on the ${currentTab} tab`}
            >
              read-only
            </span>
          ) : null}
          <ContextPill streamingParts={streamingParts} messages={messages} />
          <button
            type="button"
            className="chat-panel__export-btn"
            onClick={handleExportThread}
            disabled={!activeThreadId || !messages.length}
            title="Export this thread as Markdown"
          >
            ⇩ Export
          </button>
          <ModelSelector providers={providers} value={model} onChange={setModel} />
        </header>

        <div className="chat-panel__stage">
          <ThreadSidebar
            threads={threads}
            activeThreadId={activeThreadId}
            onSelect={(id) => { setActiveThreadId(id); setThreadsOpen(false); }}
            onCreate={() => handleCreateThread(null)}
            onCreatePinned={(tab) => handleCreateThread(tab)}
            onDelete={handleDeleteThread}
            onRename={handleRenameThread}
            onChangeTab={handleChangeThreadTab}
            onSearchHit={handleSearchHit}
            open={threadsOpen}
            onClose={() => setThreadsOpen(false)}
            currentTab={currentTab}
          />

          <div className="chat-panel__body">
            <MessageList
              messages={messages}
              streamingMessage={streamingMessage}
              onSendPrompt={handleSendPromptFromWidget}
              isStreaming={isStreaming}
              onOpenInEditor={onOpenInEditor}
              workspacePaths={workspacePaths}
              highlightMessageId={highlightMessageId}
              onHighlightConsumed={() => setHighlightMessageId(null)}
            />
          </div>

          {pendingCount > 0 ? (
            <div className="chat-panel__queue">
              <span className="chat-panel__queue-pill">
                {pendingCount} message{pendingCount === 1 ? "" : "s"} queued
              </span>
              <button
                type="button"
                className="chat-panel__queue-cancel"
                onClick={cancelQueue}
                title="Drop all queued prompts"
              >
                Cancel queue
              </button>
            </div>
          ) : null}

          {error ? <div className="chat-panel__error">{error}</div> : null}

          <form
            className={`chat-panel__composer ${isDragging ? "chat-panel__composer--dragging" : ""}`}
            onSubmit={(e) => { e.preventDefault(); sendMessage(); }}
            onDragEnter={onDragEnter}
            onDragLeave={onDragLeave}
            onDragOver={onDragOver}
            onDrop={onDrop}
          >
        {pendingAttachments.length || uploadingCount > 0 ? (
          <div className="chat-attachments">
            {pendingAttachments.map((att) => (
              <AttachmentChip
                key={att.id}
                attachment={att}
                onRemove={() => removePendingAttachment(att.id)}
              />
            ))}
            {uploadingCount > 0 ? (
              <div className="chat-attachments__uploading">
                Uploading {uploadingCount}…
              </div>
            ) : null}
          </div>
        ) : null}

        {isDragging ? (
          <div className="chat-panel__drop-hint">Drop images or PDFs to attach</div>
        ) : null}

        <textarea
          className="chat-panel__textarea"
          placeholder={
            isStreaming
              ? "Streaming response… type to queue a follow-up."
              : model?.model
                ? "Ask, visualize, mutate the pipeline… (drop / paste files to attach)"
                : "Configure a provider/model in conf.yaml to start chatting."
          }
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              sendMessage();
            }
          }}
          onPaste={onPaste}
        />
        <input
          ref={filePickerRef}
          type="file"
          accept="image/png,image/jpeg,image/gif,image/webp,application/pdf"
          multiple
          style={{ display: "none" }}
          onChange={onFilePickerChange}
        />
        <div className="chat-panel__composer-row">
          <button
            type="button"
            className="chat-panel__btn-attach"
            onClick={() => filePickerRef.current?.click()}
            disabled={uploadingCount > 0}
            title="Attach images or PDFs (max 10 MB each)"
          >
            📎
          </button>
          {!activeThreadId ? (
            <button type="button" onClick={handleCreateThread} className="chat-panel__btn-secondary">
              Start new chat
            </button>
          ) : null}
          {!supportsVision(model?.model) && pendingAttachments.some((a) => a.kind === "image") ? (
            <span className="chat-panel__vision-hint" title="Selected model is not vision-capable; images will be skipped on the wire.">
              ⚠ no vision
            </span>
          ) : null}
          {isStreaming ? (
            <>
              <button
                type="submit"
                className="chat-panel__btn-queue"
                disabled={!draft.trim() && !pendingAttachments.length}
                title="Queue this prompt to run after the current response"
              >
                Queue
              </button>
              <button type="button" className="chat-panel__btn-stop" onClick={handleCancel}>
                Stop
              </button>
            </>
          ) : (
            <button
              type="submit"
              className="chat-panel__btn-send"
              disabled={!draft.trim() && !pendingAttachments.length}
            >
              Send
            </button>
          )}
        </div>
      </form>
        </div>
      </aside>
    </div>
  );
}

function AttachmentChip({ attachment, onRemove }) {
  const isImage = attachment.kind === "image";
  const thumbUrl = isImage ? `/api/chat/attachments/${attachment.id}/content` : null;
  return (
    <div className={`chat-attachment chat-attachment--${attachment.kind}`}>
      {isImage ? (
        <img src={thumbUrl} alt={attachment.filename || "image"} className="chat-attachment__thumb" />
      ) : (
        <span className="chat-attachment__pdf-icon" aria-hidden>📄</span>
      )}
      <div className="chat-attachment__meta">
        <div className="chat-attachment__name" title={attachment.filename}>
          {attachment.filename || `attachment-${attachment.id.slice(0, 6)}`}
        </div>
        <div className="chat-attachment__sub">
          {attachment.kind} · {humanFileSize(attachment.bytes)}
        </div>
      </div>
      <button
        type="button"
        className="chat-attachment__remove"
        onClick={onRemove}
        title="Remove"
        aria-label="Remove attachment"
      >
        ✕
      </button>
    </div>
  );
}
