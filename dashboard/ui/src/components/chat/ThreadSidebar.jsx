// Thread list for the chat panel — lets the user switch / rename / delete
// SQLite-backed threads from vulnflow.db (chat_threads table).
//
// Per-tab pinning (Sprint 4 / 5.1):
//   - `default_tab` on each thread is one of "pipeline" / "audit" /
//     "editor" / null. Null = available from any tab.
//   - The sidebar shows two collapsible sections: threads for the
//     current tab (incl. unpinned), and other tabs. A rotating
//     badge on each row lets the user re-pin the thread.

import { useEffect, useMemo, useRef, useState } from "react";

const TAB_CYCLE = ["any", "pipeline", "audit", "editor"];
const TAB_LABEL = {
  any: "any",
  pipeline: "pipeline",
  audit: "audit",
  editor: "editor",
};

function nextTab(current) {
  const idx = TAB_CYCLE.indexOf(current || "any");
  return TAB_CYCLE[(idx + 1) % TAB_CYCLE.length];
}

async function fetchSearchResults(query, signal) {
  const params = new URLSearchParams();
  params.set("q", query);
  params.set("limit", "30");
  const res = await fetch(`/api/chat/search?${params.toString()}`, { signal });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const body = await res.json();
  return body.results || [];
}

export function ThreadSidebar({
  threads,
  activeThreadId,
  onSelect,
  onCreate,
  onCreatePinned,
  onDelete,
  onRename,
  onChangeTab,
  onSearchHit,
  open,
  onClose,
  currentTab,
}) {
  const { primary, other } = useMemo(() => {
    const primary = [];
    const other = [];
    for (const t of threads || []) {
      const dt = t.default_tab || null;
      if (!dt || dt === currentTab) primary.push(t);
      else other.push(t);
    }
    return { primary, other };
  }, [threads, currentTab]);

  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState("");
  const debounceRef = useRef(null);
  const abortRef = useRef(null);

  // Debounced search — fires 250 ms after the user stops typing.
  useEffect(() => {
    const q = searchQuery.trim();
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (abortRef.current) abortRef.current.abort();
    if (!q) {
      setSearchResults([]);
      setSearchError("");
      setSearchLoading(false);
      return;
    }
    debounceRef.current = setTimeout(() => {
      const controller = new AbortController();
      abortRef.current = controller;
      setSearchLoading(true);
      setSearchError("");
      fetchSearchResults(q, controller.signal)
        .then((results) => {
          if (controller.signal.aborted) return;
          setSearchResults(results);
        })
        .catch((err) => {
          if (err?.name === "AbortError") return;
          setSearchError(err.message);
          setSearchResults([]);
        })
        .finally(() => {
          if (!controller.signal.aborted) setSearchLoading(false);
        });
    }, 250);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [searchQuery]);

  const handlePickResult = (hit) => {
    setSearchQuery("");
    setSearchResults([]);
    onSearchHit?.(hit.thread_id, hit.message_id);
  };

  if (!open) return null;
  const searching = searchQuery.trim().length > 0;
  return (
    <div className="chat-threads">
      <div className="chat-threads__head">
        <span className="chat-threads__title">Conversations</span>
        <div className="chat-threads__head-actions">
          <button type="button" onClick={onCreate} title="New chat (any tab)">+ New</button>
          {currentTab ? (
            <button
              type="button"
              onClick={() => onCreatePinned?.(currentTab)}
              title={`New chat pinned to ${currentTab}`}
              className="chat-threads__btn-pin"
            >
              + Pin
            </button>
          ) : null}
          <button type="button" onClick={onClose} title="Close" className="chat-threads__close">✕</button>
        </div>
      </div>
      <div className="chat-threads__search">
        <input
          type="search"
          placeholder="Search messages…"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="chat-threads__search-input"
          aria-label="Search messages"
        />
        {searchLoading ? <span className="chat-threads__search-spinner">…</span> : null}
      </div>
      {searching ? (
        <SearchResults
          query={searchQuery}
          results={searchResults}
          loading={searchLoading}
          error={searchError}
          onPick={handlePickResult}
        />
      ) : null}
      {searching ? null : (
      <ul className="chat-threads__list">
        {primary.length ? (
          <li className="chat-threads__section">
            {currentTab ? `${currentTab} + any` : "all"}
          </li>
        ) : null}
        {primary.map((thread) => (
          <ThreadRow
            key={thread.id}
            thread={thread}
            active={thread.id === activeThreadId}
            onSelect={onSelect}
            onDelete={onDelete}
            onRename={onRename}
            onChangeTab={onChangeTab}
          />
        ))}
        {other.length ? (
          <li className="chat-threads__section">other tabs</li>
        ) : null}
        {other.map((thread) => (
          <ThreadRow
            key={thread.id}
            thread={thread}
            active={thread.id === activeThreadId}
            onSelect={onSelect}
            onDelete={onDelete}
            onRename={onRename}
            onChangeTab={onChangeTab}
            dim
          />
        ))}
        {!threads?.length ? (
          <li className="chat-threads__empty">No conversations yet.</li>
        ) : null}
      </ul>
      )}
    </div>
  );
}

function SearchResults({ query, results, loading, error, onPick }) {
  if (error) {
    return <div className="chat-search-results chat-search-results--error">{error}</div>;
  }
  if (!loading && !results.length) {
    return (
      <div className="chat-search-results chat-search-results--empty">
        No matches for “{query}”.
      </div>
    );
  }
  return (
    <ul className="chat-search-results">
      {results.map((hit) => {
        const snippet = hit.content_snippet || hit.parts_snippet || "";
        const day = (hit.created_at || "").slice(0, 10);
        return (
          <li key={hit.message_id} className="chat-search-result">
            <button
              type="button"
              className="chat-search-result__btn"
              onClick={() => onPick(hit)}
              title="Jump to this message"
            >
              <div className="chat-search-result__head">
                <span className="chat-search-result__thread">{hit.thread_title}</span>
                <span className={`chat-search-result__role chat-search-result__role--${hit.role}`}>
                  {hit.role}
                </span>
                <span className="chat-search-result__day">{day}</span>
              </div>
              <div
                className="chat-search-result__snippet"
                dangerouslySetInnerHTML={{ __html: snippet }}
              />
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function ThreadRow({ thread, active, onSelect, onDelete, onRename, onChangeTab, dim }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(thread.title);

  useEffect(() => {
    setDraft(thread.title);
  }, [thread.title]);

  if (editing) {
    return (
      <li className={`chat-threads__item ${active ? "chat-threads__item--active" : ""}`}>
        <input
          autoFocus
          className="chat-threads__edit"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => { setEditing(false); onRename?.(thread.id, draft); }}
          onKeyDown={(e) => {
            if (e.key === "Enter") { setEditing(false); onRename?.(thread.id, draft); }
            if (e.key === "Escape") { setEditing(false); setDraft(thread.title); }
          }}
        />
      </li>
    );
  }

  const tabKey = thread.default_tab || "any";
  return (
    <li
      className={
        `chat-threads__item ` +
        `${active ? "chat-threads__item--active " : ""}` +
        `${dim ? "chat-threads__item--dim" : ""}`
      }
    >
      <button
        type="button"
        className="chat-threads__select"
        onClick={() => onSelect(thread.id)}
        title={thread.title}
      >
        <span className="chat-threads__name">{thread.title}</span>
        <span className="chat-threads__meta">{thread.message_count} msg</span>
      </button>
      <div className="chat-threads__actions">
        <button
          type="button"
          className={`chat-threads__tab chat-threads__tab--${tabKey}`}
          onClick={() => onChangeTab?.(thread.id, nextTab(tabKey))}
          title="Cycle pinned tab"
        >
          {TAB_LABEL[tabKey]}
        </button>
        <button type="button" onClick={() => setEditing(true)} title="Rename">✎</button>
        <button type="button" onClick={() => onDelete(thread.id)} title="Delete">🗑</button>
      </div>
    </li>
  );
}
