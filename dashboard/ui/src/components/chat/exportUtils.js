// Client-side export helpers for the chat panel.
//
//  - assembleStandaloneHtml(html, title)
//        Wrap the raw widget HTML in a self-contained document (theme
//        variables, import map for esm.sh, stubbed bridges) so the file
//        renders correctly when opened directly in a browser.
//
//  - capturePngFromIframe(iframe, filename)
//        Rasterize the widget iframe's body via html2canvas and trigger
//        a PNG download.
//
//  - threadToMarkdown(thread, messages)
//        Build a Markdown export of a persisted chat thread. Text parts
//        are rendered as-is; widgets become `<details>` blocks with the
//        widget HTML inline; canvas actions / tool calls become compact
//        summary lines.
//
//  - triggerDownload(content, filename, mime)
//        Generic blob download.

import html2canvas from "html2canvas";

import {
  FORM_STYLES_CSS,
  SVG_CLASSES_CSS,
  THEME_CSS,
} from "./WidgetIframe.jsx";

const IMPORT_MAP = `<script type="importmap">
  {
    "imports": {
      "three": "https://esm.sh/three",
      "three/": "https://esm.sh/three/",
      "gsap": "https://esm.sh/gsap",
      "gsap/": "https://esm.sh/gsap/",
      "d3": "https://esm.sh/d3",
      "d3/": "https://esm.sh/d3/",
      "chart.js": "https://esm.sh/chart.js",
      "chart.js/": "https://esm.sh/chart.js/"
    }
  }
  </script>`;

export function escapeHtml(text) {
  return String(text ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function slugify(text) {
  return String(text ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80) || "untitled";
}

export function triggerDownload(content, filename, mime = "text/plain") {
  const blob = content instanceof Blob ? content : new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // Defer revoke so Safari has time to flush the download.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function assembleStandaloneHtml(html, title) {
  return `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${escapeHtml(title || "Widget")}</title>
  ${IMPORT_MAP}
  <style>
    ${THEME_CSS}
    ${SVG_CLASSES_CSS}
    ${FORM_STYLES_CSS}
  </style>
</head>
<body>
  <div id="content">
    ${html}
  </div>
  <script>
    // Stub the bridge functions so onclick="sendPrompt(...)" handlers
    // in widgets do not throw when the file is opened standalone.
    window.sendPrompt = function() {};
    window.openLink = function(url) { if (url) window.open(url, "_blank"); };
    document.addEventListener("click", function(e) {
      var a = e.target.closest("a[href]");
      if (a && /^https?:/i.test(a.href)) {
        e.preventDefault();
        window.open(a.href, "_blank");
      }
    });
  </script>
</body>
</html>`;
}

export async function capturePngFromIframe(iframe, { filename, scale = 2 } = {}) {
  if (!iframe || !iframe.contentDocument) {
    throw new Error("Iframe document is not accessible.");
  }
  const doc = iframe.contentDocument;
  const target = doc.body || doc.documentElement;
  if (!target) throw new Error("Iframe body not ready.");

  const width = Math.max(target.scrollWidth, target.offsetWidth, 320);
  const height = Math.max(target.scrollHeight, target.offsetHeight, 200);

  const canvas = await html2canvas(target, {
    backgroundColor: "#ffffff",
    width,
    height,
    windowWidth: width,
    windowHeight: height,
    scale,
    logging: false,
    useCORS: true,
    allowTaint: true,
  });

  await new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (!blob) {
        reject(new Error("Failed to encode PNG."));
        return;
      }
      triggerDownload(blob, filename || "widget.png", "image/png");
      resolve();
    }, "image/png");
  });
}

// ---------------------------------------------------------------------------
// Thread → Markdown
// ---------------------------------------------------------------------------

function formatRoleHeader(role, createdAt) {
  const label = role === "assistant" ? "Assistant" : role === "user" ? "User" : role;
  if (!createdAt) return `### ${label}`;
  return `### ${label} · ${createdAt}`;
}

function partToMarkdown(part) {
  if (!part || typeof part !== "object") return "";
  switch (part.type) {
    case "text":
      return part.text || "";
    case "plan":
      return [
        "> **Plan**",
        `> - approach: ${part.approach || ""}`,
        `> - technology: ${part.technology || ""}`,
        ...(Array.isArray(part.key_elements) ? part.key_elements.map((e) => `> - ${e}`) : []),
      ].join("\n");
    case "tool_status":
    case "tool_result":
      return ""; // grouped/condensed below
    case "widget":
      return (
        `\n<details><summary>Widget: ${escapeHtml(part.title || "Untitled")}</summary>\n\n` +
        "```html\n" +
        (part.html || "") +
        "\n```\n\n</details>\n"
      );
    case "canvas_action": {
      const a = part.action || {};
      const status = part.rejected ? "rejected" : part.applied ? "applied" : "pending";
      const kindLine = `[canvas: ${a.kind || "?"} · ${status}${
        part.reason ? ` · ${part.reason}` : ""
      }]`;
      return kindLine;
    }
    case "context_info":
      return `_(context: ${part.input_tokens}/${part.max_input_tokens} tokens${
        part.compacted ? ` · ${part.compacted_messages} compacted` : ""
      })_`;
    case "error":
      return `> ⚠️ ${escapeHtml(part.scope || "")}: ${escapeHtml(part.message || "")}`;
    default:
      return "";
  }
}

function summarizeTools(parts) {
  const out = [];
  const byCall = new Map();
  for (const p of parts || []) {
    if (p?.type === "tool_status" || p?.type === "tool_result") {
      const existing = byCall.get(p.call_id) || { name: p.name, status: "running", ok: null };
      if (p.type === "tool_status") existing.status = p.status;
      if (p.type === "tool_result") existing.ok = !!p.ok;
      byCall.set(p.call_id, existing);
    }
  }
  for (const [callId, info] of byCall) {
    const ok = info.ok === false ? "✗" : info.ok === true ? "✓" : "·";
    out.push(`${ok} **tool** \`${info.name}\` _(${info.status})_`);
  }
  return out;
}

export function threadToMarkdown(thread, messages) {
  const lines = [];
  lines.push(`# ${thread?.title || "Chat thread"}`);
  if (thread?.id) lines.push(`_thread id:_ \`${thread.id}\``);
  if (thread?.created_at) lines.push(`_created:_ ${thread.created_at}`);
  lines.push("");
  lines.push("---");
  lines.push("");

  for (const message of messages || []) {
    lines.push(formatRoleHeader(message.role, message.created_at));
    lines.push("");

    if (message.role === "user") {
      lines.push(message.content || "");
      lines.push("");
      continue;
    }

    const parts = Array.isArray(message.parts) ? message.parts : [];
    if (!parts.length && message.content) {
      lines.push(message.content);
      lines.push("");
      continue;
    }

    for (const part of parts) {
      const md = partToMarkdown(part);
      if (md) {
        lines.push(md);
        lines.push("");
      }
    }
    const toolLines = summarizeTools(parts);
    if (toolLines.length) {
      lines.push("<details><summary>Tool calls</summary>\n");
      toolLines.forEach((l) => lines.push(`- ${l}`));
      lines.push("\n</details>");
      lines.push("");
    }
  }

  return lines.join("\n");
}
