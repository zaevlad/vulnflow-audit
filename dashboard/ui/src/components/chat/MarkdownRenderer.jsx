// Markdown renderer for assistant chat text.
//
// - GitHub-flavored markdown via remark-gfm (tables, task-lists, strikethrough)
// - Fenced code blocks highlighted by highlight.js (sync, no async/WASM).
//   Streaming-friendly: an unclosed ``` block just renders as a normal
//   code block — react-markdown handles the in-progress state for us.
// - Inline `path:line` citations are turned into clickable buttons that
//   call `onOpenInEditor(path, line)`. The path is validated client-side
//   against `workspaceFileTree` so missing files render as plain text
//   (no false positives like `1.2.3:42`).

import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import hljs from "highlight.js/lib/common";
import hljsSolidity from "highlightjs-solidity";

// highlight.js core does not ship Solidity — register it (and Yul) once
// from the highlightjs-solidity package.
let solidityRegistered = false;
function ensureSolidity() {
  if (solidityRegistered) return;
  try {
    if (!hljs.getLanguage("solidity")) hljsSolidity(hljs);
    solidityRegistered = true;
  } catch {
    /* highlight.js will fall back to plaintext */
  }
}

const ALIAS = {
  sol: "solidity",
  py: "python",
  yml: "yaml",
  sh: "bash",
  shell: "bash",
  ts: "typescript",
  tsx: "typescript",
  js: "javascript",
  jsx: "javascript",
};

function resolveLanguage(raw) {
  if (!raw) return null;
  const key = String(raw).toLowerCase();
  return ALIAS[key] || key;
}

function highlight(code, lang) {
  ensureSolidity();
  const language = resolveLanguage(lang);
  if (language && hljs.getLanguage(language)) {
    try {
      return hljs.highlight(code, { language, ignoreIllegals: true }).value;
    } catch {
      /* fall through */
    }
  }
  try {
    return hljs.highlightAuto(code).value;
  } catch {
    return null;
  }
}

// Matches things like contracts/Vault.sol:42, src/foo/bar.ts:100,
// scripts/util.py:7. Path must have at least one `/` or `.` and end in
// a recognized extension. The line capture is a 1+ digit number.
const PATH_LINE_RE = /\b([\w][\w./-]*\.(?:sol|js|ts|tsx|jsx|py|yaml|yml|md|json)):(\d+)\b/g;

function isKnownPath(workspacePaths, candidate) {
  if (!workspacePaths || !workspacePaths.size) return true; // unknown — be permissive
  const normalized = candidate.replace(/\\/g, "/");
  return (
    workspacePaths.has(normalized) ||
    workspacePaths.has(normalized.replace(/^\.\//, ""))
  );
}

function renderTextWithCitations(text, { onOpenInEditor, workspacePaths }) {
  if (!onOpenInEditor || typeof text !== "string" || !text.includes(":")) {
    return text;
  }
  const out = [];
  let lastIndex = 0;
  let key = 0;
  PATH_LINE_RE.lastIndex = 0;
  let m;
  while ((m = PATH_LINE_RE.exec(text)) !== null) {
    const [match, path, lineStr] = m;
    const line = parseInt(lineStr, 10);
    if (m.index > lastIndex) out.push(text.slice(lastIndex, m.index));
    if (Number.isFinite(line) && isKnownPath(workspacePaths, path)) {
      out.push(
        <button
          key={`cite-${key++}`}
          type="button"
          className="chat-citation"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            onOpenInEditor(path, line);
          }}
          title={`Open ${path} at line ${line}`}
        >
          {match}
        </button>,
      );
    } else {
      out.push(match);
    }
    lastIndex = m.index + match.length;
  }
  if (lastIndex < text.length) out.push(text.slice(lastIndex));
  return out.length ? out : text;
}

// react-markdown 10 passes `children` to nested components as an array of
// React nodes. We only want to post-process bare strings — any element
// we receive has already been rendered (link, em, code, etc.) so we
// keep it as-is.
function transformChildren(children, options) {
  if (!Array.isArray(children)) {
    return typeof children === "string"
      ? renderTextWithCitations(children, options)
      : children;
  }
  const out = [];
  let key = 0;
  for (const child of children) {
    if (typeof child === "string") {
      const transformed = renderTextWithCitations(child, options);
      if (Array.isArray(transformed)) {
        transformed.forEach((piece) => {
          if (typeof piece === "string") out.push(piece);
          else out.push(<span key={`p-${key++}`}>{piece}</span>);
        });
      } else {
        out.push(transformed);
      }
    } else {
      out.push(child);
    }
  }
  return out;
}

export function MarkdownRenderer({ text, onOpenInEditor, workspacePaths }) {
  const options = useMemo(
    () => ({ onOpenInEditor, workspacePaths }),
    [onOpenInEditor, workspacePaths],
  );

  const components = useMemo(
    () => ({
      // Block-level code (fenced) — highlight via highlight.js
      code({ inline, className, children, ...rest }) {
        const raw = String(children ?? "").replace(/\n$/, "");
        if (inline) {
          return (
            <code className="chat-md-code-inline" {...rest}>
              {raw}
            </code>
          );
        }
        const lang = /language-([\w-]+)/.exec(className || "")?.[1] || null;
        const highlighted = highlight(raw, lang);
        return (
          <pre className={`chat-md-code chat-md-code--${lang || "plain"}`}>
            <code
              className={`hljs ${lang ? `language-${lang}` : ""}`}
              {...(highlighted
                ? { dangerouslySetInnerHTML: { __html: highlighted } }
                : { children: raw })}
            />
          </pre>
        );
      },
      a({ children, href, ...rest }) {
        // External links open in a new tab; relative ones stay inline.
        const isExternal = /^https?:\/\//i.test(String(href || ""));
        return (
          <a
            href={href}
            {...(isExternal
              ? { target: "_blank", rel: "noopener noreferrer" }
              : {})}
            {...rest}
          >
            {transformChildren(children, options)}
          </a>
        );
      },
      p({ children, ...rest }) {
        return <p {...rest}>{transformChildren(children, options)}</p>;
      },
      li({ children, ...rest }) {
        return <li {...rest}>{transformChildren(children, options)}</li>;
      },
      td({ children, ...rest }) {
        return <td {...rest}>{transformChildren(children, options)}</td>;
      },
      th({ children, ...rest }) {
        return <th {...rest}>{transformChildren(children, options)}</th>;
      },
      blockquote({ children, ...rest }) {
        return <blockquote {...rest}>{transformChildren(children, options)}</blockquote>;
      },
    }),
    [options],
  );

  return (
    <div className="chat-md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {text || ""}
      </ReactMarkdown>
    </div>
  );
}
