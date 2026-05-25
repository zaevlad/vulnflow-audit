import React, { useCallback, useEffect, useRef, useState } from "react";
import { loader } from "@monaco-editor/react";
import MonacoEditor from "@monaco-editor/react";
import * as monaco from "monaco-editor/esm/vs/editor/editor.api.js";
import editorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import jsonWorker from "monaco-editor/esm/vs/language/json/json.worker?worker";
import tsWorker from "monaco-editor/esm/vs/language/typescript/ts.worker?worker";
import "monaco-editor/min/vs/editor/editor.main.css";

// Smart contracts + popular languages (eager tokenizers, no lazy chunks)
import "monaco-editor/esm/vs/basic-languages/solidity/solidity.contribution.js";
import "monaco-editor/esm/vs/basic-languages/solidity/solidity.js";
import "monaco-editor/esm/vs/basic-languages/rust/rust.contribution.js";
import "monaco-editor/esm/vs/basic-languages/rust/rust.js";
import "monaco-editor/esm/vs/basic-languages/python/python.contribution.js";
import "monaco-editor/esm/vs/basic-languages/python/python.js";
import "monaco-editor/esm/vs/basic-languages/javascript/javascript.contribution.js";
import "monaco-editor/esm/vs/basic-languages/javascript/javascript.js";
import "monaco-editor/esm/vs/basic-languages/typescript/typescript.contribution.js";
import "monaco-editor/esm/vs/basic-languages/typescript/typescript.js";
// C and C++ share cpp/ in Monaco 0.52 (.c → id "c", .cpp → id "cpp")
import "monaco-editor/esm/vs/basic-languages/cpp/cpp.contribution.js";
import "monaco-editor/esm/vs/basic-languages/cpp/cpp.js";
import "monaco-editor/esm/vs/basic-languages/java/java.contribution.js";
import "monaco-editor/esm/vs/basic-languages/java/java.js";
import "monaco-editor/esm/vs/basic-languages/go/go.contribution.js";
import "monaco-editor/esm/vs/basic-languages/go/go.js";
import "monaco-editor/esm/vs/basic-languages/csharp/csharp.contribution.js";
import "monaco-editor/esm/vs/basic-languages/csharp/csharp.js";
import "monaco-editor/esm/vs/basic-languages/ruby/ruby.contribution.js";
import "monaco-editor/esm/vs/basic-languages/ruby/ruby.js";
import "monaco-editor/esm/vs/basic-languages/php/php.contribution.js";
import "monaco-editor/esm/vs/basic-languages/php/php.js";
import "monaco-editor/esm/vs/basic-languages/yaml/yaml.contribution.js";
import "monaco-editor/esm/vs/basic-languages/yaml/yaml.js";
import "monaco-editor/esm/vs/basic-languages/markdown/markdown.contribution.js";
import "monaco-editor/esm/vs/basic-languages/markdown/markdown.js";
import "monaco-editor/esm/vs/basic-languages/shell/shell.contribution.js";
import "monaco-editor/esm/vs/basic-languages/shell/shell.js";
import "monaco-editor/esm/vs/basic-languages/ini/ini.contribution.js";
import "monaco-editor/esm/vs/basic-languages/ini/ini.js";
import "monaco-editor/esm/vs/basic-languages/html/html.contribution.js";
import "monaco-editor/esm/vs/basic-languages/html/html.js";
import "monaco-editor/esm/vs/basic-languages/css/css.contribution.js";
import "monaco-editor/esm/vs/basic-languages/css/css.js";

// JSON/TS language services (validation, IntelliSense)
import "monaco-editor/esm/vs/language/json/monaco.contribution";
import "monaco-editor/esm/vs/language/typescript/monaco.contribution";
import "monaco-editor/esm/vs/language/css/monaco.contribution";
import "monaco-editor/esm/vs/language/html/monaco.contribution";

import { registerVscodeDarkPlusTheme, VSCODE_DARK_PLUS_THEME } from "./vscodeDarkPlusTheme.js";

registerVscodeDarkPlusTheme(monaco);

window.MonacoEnvironment = {
  getWorker(_, label) {
    if (label === "json") return new jsonWorker();
    if (label === "typescript" || label === "javascript") return new tsWorker();
    return new editorWorker();
  },
};

loader.config({ monaco });

async function apiFetch(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = data.detail || detail;
    } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}

function EditorFileTree({ node, onOpen, level = 0 }) {
  const [expanded, setExpanded] = useState(level < 2);
  if (!node) return null;

  if (node.kind === "file") {
    return (
      <div
        className="tree-node--clickable"
        style={{ paddingLeft: `${level * 14 + 8}px` }}
        onClick={() => onOpen(node.path)}
        title={node.path}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => e.key === "Enter" && onOpen(node.path)}
      >
        <span className="tree-node__kind tree-node__kind--file">FILE</span>
        <span className="tree-node__name">{node.name}</span>
      </div>
    );
  }

  return (
    <div className="tree-node">
      <div
        className="tree-node__row tree-node__row--dir"
        style={{ paddingLeft: `${level * 14 + 8}px` }}
        onClick={() => setExpanded((v) => !v)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => e.key === "Enter" && setExpanded((v) => !v)}
      >
        <span className="tree-node__kind tree-node__kind--directory">{expanded ? "▾" : "▸"}</span>
        <span className="tree-node__name">{node.name}</span>
      </div>
      {expanded && node.children?.length > 0
        ? <div>{node.children.map((child) => <EditorFileTree key={child.path} node={child} onOpen={onOpen} level={level + 1} />)}</div>
        : null}
    </div>
  );
}

export default function MonacoPane({ workspaceTree, workspacePath, pendingOpen }) {
  const [openedPath, setOpenedPath] = useState("");
  const [content, setContent] = useState("");
  const [language, setLanguage] = useState("plaintext");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const editorRef = useRef(null);
  const lastConsumedOpenKeyRef = useRef(null);

  const loadFile = useCallback(async (path, { silent = false } = {}) => {
    if (!silent && dirty) {
      if (!window.confirm("You have unsaved changes. Discard and open a new file?")) return null;
    }
    setError("");
    try {
      const result = await apiFetch("/api/editor/open", { path });
      setOpenedPath(result.path);
      setContent(result.content);
      setLanguage(result.language);
      setDirty(false);
      return result;
    } catch (err) {
      setError(err.message);
      return null;
    }
  }, [dirty]);

  async function openFile(path) {
    await loadFile(path);
  }

  // Citation click in the chat panel: open file and reveal the line.
  // `pendingOpen.key` is bumped by the caller so the same {path,line}
  // can be re-triggered, e.g. when the user clicks the same citation
  // twice.
  useEffect(() => {
    if (!pendingOpen || !pendingOpen.path) return;
    if (lastConsumedOpenKeyRef.current === pendingOpen.key) return;
    lastConsumedOpenKeyRef.current = pendingOpen.key;
    let cancelled = false;
    (async () => {
      const result = await loadFile(pendingOpen.path, { silent: false });
      if (cancelled || !result) return;
      const line = pendingOpen.line;
      if (!Number.isFinite(line) || line <= 0) return;
      // Give Monaco a tick to attach the new model before scrolling.
      setTimeout(() => {
        const ed = editorRef.current;
        if (!ed) return;
        try {
          ed.revealLineInCenter(line, monaco.editor.ScrollType.Smooth);
          ed.setSelection(new monaco.Range(line, 1, line, 1));
          ed.focus();
        } catch {
          /* model may have been disposed in the meantime */
        }
      }, 80);
    })();
    return () => { cancelled = true; };
  }, [pendingOpen, loadFile]);

  async function saveFile() {
    if (!openedPath) return;
    setSaving(true);
    setError("");
    try {
      await apiFetch("/api/editor/save", { path: openedPath, content });
      setDirty(false);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function reloadFile() {
    if (!openedPath) return;
    if (dirty) {
      if (!window.confirm("You have unsaved changes. Reload from disk and discard them?")) return;
    }
    setError("");
    try {
      const result = await apiFetch("/api/editor/reload", { path: openedPath });
      setContent(result.content);
      setLanguage(result.language);
      setDirty(false);
    } catch (err) {
      setError(err.message);
    }
  }

  function handleEditorChange(value) {
    setContent(value ?? "");
    if (openedPath) setDirty(true);
  }

  const relPath = workspacePath && openedPath
    ? openedPath.replace(workspacePath, "").replace(/\\/g, "/").replace(/^\//, "")
    : openedPath;

  return (
    <div className="editor-pane" role="tabpanel" aria-label="Editor">
      <aside className="file-column">
        <div className="section-title">Workspace Files</div>
        <div className="detail-panel file-panel">
          <div className="detail-toolbar">
            <div className="detail-toolbar__title">Files</div>
            <div className="detail-toolbar__meta">Click to open</div>
          </div>
          {workspaceTree
            ? <div className="tree-wrap"><EditorFileTree node={workspaceTree} onOpen={openFile} /></div>
            : <div className="placeholder">No workspace tree available.</div>}
        </div>
      </aside>
      <section className="editor-main">
        <div className="section-title">File Editor</div>
        <div className="editor-toolbar">
          <span className="editor-filepath" title={openedPath || ""}>{relPath || "No file open"}</span>
          {dirty ? <span className="editor-dirty-badge">UNSAVED</span> : null}
          <button type="button" onClick={saveFile} disabled={!openedPath || saving}>
            {saving ? "Saving..." : "Save"}
          </button>
          <button type="button" className="button-secondary" onClick={reloadFile} disabled={!openedPath}>
            Reload
          </button>
        </div>
        {error ? <div className="editor-error">{error}</div> : null}
        <div className="editor-viewport">
          <MonacoEditor
            height="100%"
            theme={VSCODE_DARK_PLUS_THEME}
            beforeMount={registerVscodeDarkPlusTheme}
            onMount={(editor) => { editorRef.current = editor; }}
            path={openedPath || undefined}
            language={language}
            value={content}
            onChange={handleEditorChange}
            options={{
              fontSize: 14,
              minimap: { enabled: true },
              scrollBeyondLastLine: false,
              wordWrap: "off",
              automaticLayout: true,
              tabSize: 2,
            }}
          />
        </div>
      </section>
    </div>
  );
}
