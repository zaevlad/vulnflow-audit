// Sandboxed widget renderer for the AI chat panel.
//
// Direct port of misc/OpenGenerativeUI/apps/app/src/components/generative-ui/widget-renderer.tsx
// (CSS layers, BRIDGE_JS, idiomorph streaming, ResizeObserver auto-resize,
// CSP with allow-list of esm.sh / cdnjs / jsdelivr / unpkg, send-prompt /
// open-link postMessage bridges). Converted from TSX to JSX, zod schema
// stripped, behavior preserved.

import { useEffect, useRef, useState, useCallback } from "react";
import { ExportOverlay } from "./ExportOverlay.jsx";
import { IDIOMORPH_JS } from "./idiomorph-inline.js";

// ─── Injected CSS: Theme Variables (Layer 3) ─────────────────────────
export const THEME_CSS = `
:root {
  --color-background-primary: #ffffff;
  --color-background-secondary: #f7f6f3;
  --color-background-tertiary: #efeee9;
  --color-background-info: #E6F1FB;
  --color-background-danger: #FCEBEB;
  --color-background-success: #EAF3DE;
  --color-background-warning: #FAEEDA;

  --color-text-primary: #1a1a1a;
  --color-text-secondary: #73726c;
  --color-text-tertiary: #9c9a92;
  --color-text-info: #185FA5;
  --color-text-danger: #A32D2D;
  --color-text-success: #3B6D11;
  --color-text-warning: #854F0B;

  --color-border-primary: rgba(0, 0, 0, 0.4);
  --color-border-secondary: rgba(0, 0, 0, 0.3);
  --color-border-tertiary: rgba(0, 0, 0, 0.15);
  --color-border-info: #185FA5;
  --color-border-danger: #A32D2D;
  --color-border-success: #3B6D11;
  --color-border-warning: #854F0B;

  --font-sans: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --font-serif: Georgia, "Times New Roman", serif;
  --font-mono: "SF Mono", "Fira Code", "Fira Mono", monospace;

  --border-radius-md: 8px;
  --border-radius-lg: 12px;
  --border-radius-xl: 16px;

  --p: var(--color-text-primary);
  --s: var(--color-text-secondary);
  --t: var(--color-text-tertiary);
  --bg2: var(--color-background-secondary);
  --b: var(--color-border-tertiary);
}

@media (prefers-color-scheme: dark) {
  :root {
    --color-background-primary: #1a1a18;
    --color-background-secondary: #2c2c2a;
    --color-background-tertiary: #222220;
    --color-background-info: #0C447C;
    --color-background-danger: #501313;
    --color-background-success: #173404;
    --color-background-warning: #412402;

    --color-text-primary: #e8e6de;
    --color-text-secondary: #9c9a92;
    --color-text-tertiary: #73726c;
    --color-text-info: #85B7EB;
    --color-text-danger: #F09595;
    --color-text-success: #97C459;
    --color-text-warning: #EF9F27;

    --color-border-primary: rgba(255, 255, 255, 0.4);
    --color-border-secondary: rgba(255, 255, 255, 0.3);
    --color-border-tertiary: rgba(255, 255, 255, 0.15);
    --color-border-info: #85B7EB;
    --color-border-danger: #F09595;
    --color-border-success: #97C459;
    --color-border-warning: #EF9F27;
  }
}
`;

// ─── Injected CSS: SVG Pre-Built Classes (Layer 4) ───────────────────
export const SVG_CLASSES_CSS = `
svg {
  display: block;
  max-width: 100%;
  height: auto;
}

svg text.t   { font: 400 14px var(--font-sans); fill: var(--p); }
svg text.ts  { font: 400 12px var(--font-sans); fill: var(--s); }
svg text.th  { font: 500 14px var(--font-sans); fill: var(--p); }

svg .box > rect, svg .box > circle, svg .box > ellipse { fill: var(--bg2); stroke: var(--b); }
svg .node { cursor: pointer; }
svg .node:hover { opacity: 0.8; }
svg .arr { stroke: var(--s); stroke-width: 1.5; fill: none; }
svg .leader { stroke: var(--t); stroke-width: 0.5; stroke-dasharray: 4 4; fill: none; }

/* Purple */
svg .c-purple > rect, svg .c-purple > circle, svg .c-purple > ellipse,
svg rect.c-purple, svg circle.c-purple, svg ellipse.c-purple { fill: #EEEDFE; stroke: #534AB7; }
svg .c-purple text.th, svg .c-purple text.t { fill: #3C3489; }
svg .c-purple text.ts { fill: #534AB7; }

/* Teal */
svg .c-teal > rect, svg .c-teal > circle, svg .c-teal > ellipse,
svg rect.c-teal, svg circle.c-teal, svg ellipse.c-teal { fill: #E1F5EE; stroke: #0F6E56; }
svg .c-teal text.th, svg .c-teal text.t { fill: #085041; }
svg .c-teal text.ts { fill: #0F6E56; }

/* Coral */
svg .c-coral > rect, svg .c-coral > circle, svg .c-coral > ellipse,
svg rect.c-coral, svg circle.c-coral, svg ellipse.c-coral { fill: #FAECE7; stroke: #993C1D; }
svg .c-coral text.th, svg .c-coral text.t { fill: #712B13; }
svg .c-coral text.ts { fill: #993C1D; }

/* Pink */
svg .c-pink > rect, svg .c-pink > circle, svg .c-pink > ellipse,
svg rect.c-pink, svg circle.c-pink, svg ellipse.c-pink { fill: #FBEAF0; stroke: #993556; }
svg .c-pink text.th, svg .c-pink text.t { fill: #72243E; }
svg .c-pink text.ts { fill: #993556; }

/* Gray */
svg .c-gray > rect, svg .c-gray > circle, svg .c-gray > ellipse,
svg rect.c-gray, svg circle.c-gray, svg ellipse.c-gray { fill: #F1EFE8; stroke: #5F5E5A; }
svg .c-gray text.th, svg .c-gray text.t { fill: #444441; }
svg .c-gray text.ts { fill: #5F5E5A; }

/* Blue */
svg .c-blue > rect, svg .c-blue > circle, svg .c-blue > ellipse,
svg rect.c-blue, svg circle.c-blue, svg ellipse.c-blue { fill: #E6F1FB; stroke: #185FA5; }
svg .c-blue text.th, svg .c-blue text.t { fill: #0C447C; }
svg .c-blue text.ts { fill: #185FA5; }

/* Green */
svg .c-green > rect, svg .c-green > circle, svg .c-green > ellipse,
svg rect.c-green, svg circle.c-green, svg ellipse.c-green { fill: #EAF3DE; stroke: #3B6D11; }
svg .c-green text.th, svg .c-green text.t { fill: #27500A; }
svg .c-green text.ts { fill: #3B6D11; }

/* Amber */
svg .c-amber > rect, svg .c-amber > circle, svg .c-amber > ellipse,
svg rect.c-amber, svg circle.c-amber, svg ellipse.c-amber { fill: #FAEEDA; stroke: #854F0B; }
svg .c-amber text.th, svg .c-amber text.t { fill: #633806; }
svg .c-amber text.ts { fill: #854F0B; }

/* Red */
svg .c-red > rect, svg .c-red > circle, svg .c-red > ellipse,
svg rect.c-red, svg circle.c-red, svg ellipse.c-red { fill: #FCEBEB; stroke: #A32D2D; }
svg .c-red text.th, svg .c-red text.t { fill: #791F1F; }
svg .c-red text.ts { fill: #A32D2D; }

/* Dark mode overrides */
@media (prefers-color-scheme: dark) {
  svg text.t   { fill: #e8e6de; }
  svg text.ts  { fill: #9c9a92; }
  svg text.th  { fill: #e8e6de; }

  svg .c-purple > rect, svg .c-purple > circle, svg .c-purple > ellipse,
  svg rect.c-purple, svg circle.c-purple, svg ellipse.c-purple { fill: #3C3489; stroke: #AFA9EC; }
  svg .c-purple text.th, svg .c-purple text.t { fill: #CECBF6; }
  svg .c-purple text.ts { fill: #AFA9EC; }

  svg .c-teal > rect, svg .c-teal > circle, svg .c-teal > ellipse,
  svg rect.c-teal, svg circle.c-teal, svg ellipse.c-teal { fill: #085041; stroke: #5DCAA5; }
  svg .c-teal text.th, svg .c-teal text.t { fill: #9FE1CB; }
  svg .c-teal text.ts { fill: #5DCAA5; }

  svg .c-coral > rect, svg .c-coral > circle, svg .c-coral > ellipse,
  svg rect.c-coral, svg circle.c-coral, svg ellipse.c-coral { fill: #712B13; stroke: #F0997B; }
  svg .c-coral text.th, svg .c-coral text.t { fill: #F5C4B3; }
  svg .c-coral text.ts { fill: #F0997B; }

  svg .c-pink > rect, svg .c-pink > circle, svg .c-pink > ellipse,
  svg rect.c-pink, svg circle.c-pink, svg ellipse.c-pink { fill: #72243E; stroke: #ED93B1; }
  svg .c-pink text.th, svg .c-pink text.t { fill: #F4C0D1; }
  svg .c-pink text.ts { fill: #ED93B1; }

  svg .c-gray > rect, svg .c-gray > circle, svg .c-gray > ellipse,
  svg rect.c-gray, svg circle.c-gray, svg ellipse.c-gray { fill: #444441; stroke: #B4B2A9; }
  svg .c-gray text.th, svg .c-gray text.t { fill: #D3D1C7; }
  svg .c-gray text.ts { fill: #B4B2A9; }

  svg .c-blue > rect, svg .c-blue > circle, svg .c-blue > ellipse,
  svg rect.c-blue, svg circle.c-blue, svg ellipse.c-blue { fill: #0C447C; stroke: #85B7EB; }
  svg .c-blue text.th, svg .c-blue text.t { fill: #B5D4F4; }
  svg .c-blue text.ts { fill: #85B7EB; }

  svg .c-green > rect, svg .c-green > circle, svg .c-green > ellipse,
  svg rect.c-green, svg circle.c-green, svg ellipse.c-green { fill: #27500A; stroke: #97C459; }
  svg .c-green text.th, svg .c-green text.t { fill: #C0DD97; }
  svg .c-green text.ts { fill: #97C459; }

  svg .c-amber > rect, svg .c-amber > circle, svg .c-amber > ellipse,
  svg rect.c-amber, svg circle.c-amber, svg ellipse.c-amber { fill: #633806; stroke: #EF9F27; }
  svg .c-amber text.th, svg .c-amber text.t { fill: #FAC775; }
  svg .c-amber text.ts { fill: #EF9F27; }

  svg .c-red > rect, svg .c-red > circle, svg .c-red > ellipse,
  svg rect.c-red, svg circle.c-red, svg ellipse.c-red { fill: #791F1F; stroke: #F09595; }
  svg .c-red text.th, svg .c-red text.t { fill: #F7C1C1; }
  svg .c-red text.ts { fill: #F09595; }
}
`;

// ─── Injected CSS: Form Element Styles (Layer 5) ─────────────────────
export const FORM_STYLES_CSS = `
* { box-sizing: border-box; margin: 0; }

html { background: transparent; }

body {
  font-family: var(--font-sans);
  font-size: 16px;
  line-height: 1.7;
  color: var(--color-text-primary);
  background: transparent;
  -webkit-font-smoothing: antialiased;
  width: 100%;
}

#content {
  width: 100%;
  max-width: 100%;
  min-width: 0;
}

button {
  font-family: inherit;
  font-size: 14px;
  padding: 6px 16px;
  border: 0.5px solid var(--color-border-secondary);
  border-radius: var(--border-radius-md);
  background: transparent;
  color: var(--color-text-primary);
  cursor: pointer;
  transition: background 0.15s, transform 0.1s;
}
button:hover { background: var(--color-background-secondary); }
button:active { transform: scale(0.98); }

input[type="text"],
input[type="number"],
input[type="email"],
input[type="search"],
textarea,
select {
  font-family: inherit;
  font-size: 14px;
  padding: 6px 12px;
  height: 36px;
  border: 0.5px solid var(--color-border-tertiary);
  border-radius: var(--border-radius-md);
  background: var(--color-background-primary);
  color: var(--color-text-primary);
  transition: border-color 0.15s;
}
input:hover, textarea:hover, select:hover { border-color: var(--color-border-secondary); }
input:focus, textarea:focus, select:focus {
  outline: none;
  border-color: var(--color-border-primary);
  box-shadow: 0 0 0 3px rgba(0, 0, 0, 0.06);
}
textarea { height: auto; min-height: 80px; resize: vertical; }
input::placeholder, textarea::placeholder { color: var(--color-text-tertiary); }

input[type="range"] {
  -webkit-appearance: none;
  appearance: none;
  height: 4px;
  background: var(--color-border-tertiary);
  border-radius: 2px;
  border: none;
  outline: none;
}
input[type="range"]::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 18px; height: 18px;
  border-radius: 50%;
  background: var(--color-background-primary);
  border: 0.5px solid var(--color-border-secondary);
  cursor: pointer;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
}
input[type="range"]::-moz-range-thumb {
  width: 18px; height: 18px;
  border-radius: 50%;
  background: var(--color-background-primary);
  border: 0.5px solid var(--color-border-secondary);
  cursor: pointer;
}

input[type="checkbox"], input[type="radio"] {
  width: 16px; height: 16px;
  accent-color: var(--color-text-info);
}

a { color: var(--color-text-info); text-decoration: none; }
a:hover { text-decoration: underline; }

/* First render: stagger all children */
#content.initial-render > * {
  animation: fadeSlideIn 0.4s ease-out both;
}
#content.initial-render > *:nth-child(1) { animation-delay: 0s; }
#content.initial-render > *:nth-child(2) { animation-delay: 0.06s; }
#content.initial-render > *:nth-child(3) { animation-delay: 0.12s; }
#content.initial-render > *:nth-child(4) { animation-delay: 0.18s; }
#content.initial-render > *:nth-child(5) { animation-delay: 0.24s; }
#content.initial-render > *:nth-child(n+6) { animation-delay: 0.3s; }

/* Subsequent morphs: only new elements animate in */
.morph-enter {
  animation: fadeSlideIn 0.4s ease-out both;
}

@keyframes fadeSlideIn {
  from { opacity: 0; transform: translateY(8px); }
  to   { opacity: 1; transform: translateY(0); }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
  }
}
`;

// ─── Injected JS: Bridge Functions + Auto-Resize (Layers 1 & 6) ─────
const BRIDGE_JS = `
// Bridge: sendPrompt
window.sendPrompt = function(text) {
  window.parent.postMessage({ type: 'send-prompt', text: text }, '*');
};

// Bridge: openLink
window.openLink = function(url) {
  window.parent.postMessage({ type: 'open-link', url: url }, '*');
};

// Intercept <a> clicks
document.addEventListener('click', function(e) {
  var a = e.target.closest('a[href]');
  if (a && a.href.startsWith('http')) {
    e.preventDefault();
    window.parent.postMessage({ type: 'open-link', url: a.href }, '*');
  }
});

// Listen for streaming content updates from parent
window.addEventListener('message', function(e) {
  if (e.source !== window.parent) return;
  if (e.data && e.data.type === 'update-content') {
    var content = document.getElementById('content');
    if (!content) return;

    var rawHtml = e.data.html;
    var tmp = document.createElement('div');
    tmp.innerHTML = rawHtml;
    var incomingScripts = [];
    var scriptOpens = (rawHtml.match(/<script[\\s>]/gi) || []).length;
    var scriptCloses = (rawHtml.match(/<\\/script>/gi) || []).length;
    var allScriptsClosed = scriptOpens <= scriptCloses;
    tmp.querySelectorAll('script').forEach(function(s) {
      incomingScripts.push({ src: s.src, text: s.textContent, type: s.type || '' });
      s.remove();
    });

    if (!tmp.innerHTML.trim()) {
      content.removeAttribute('data-has-content');
      content.innerHTML = '';
      reportHeight();
      return;
    }

    var isFirstRender = !content.hasAttribute('data-has-content');
    if (isFirstRender) {
      content.classList.add('initial-render');
      content.setAttribute('data-has-content', '1');
      setTimeout(function() { content.classList.remove('initial-render'); }, 800);
    }

    if (window.Idiomorph) {
      try {
        Idiomorph.morph(content, tmp, {
          morphStyle: 'innerHTML',
          callbacks: {
            beforeNodeAdded: function(node) {
              if (node.nodeType === 1) {
                node.classList.add('morph-enter');
                node.addEventListener('animationend', function() {
                  node.classList.remove('morph-enter');
                }, { once: true });
              }
            }
          }
        });
      } catch (err) {
        content.innerHTML = tmp.innerHTML;
      }
    } else {
      content.innerHTML = tmp.innerHTML;
    }

    if (allScriptsClosed) {
      (function runScripts(scripts, idx) {
        if (idx >= scripts.length) return;
        var scriptInfo = scripts[idx];
        var key = scriptInfo.src || scriptInfo.text;
        if (!key || !key.trim()) { runScripts(scripts, idx + 1); return; }
        var hash = btoa(unescape(encodeURIComponent(key))).slice(0, 16).replace(/[^a-zA-Z0-9]/g, '');
        if (!hash || content.getAttribute('data-exec-' + hash)) { runScripts(scripts, idx + 1); return; }
        content.setAttribute('data-exec-' + hash, '1');
        try {
          var newScript = document.createElement('script');
          var effectiveType = scriptInfo.type;
          if (!effectiveType && scriptInfo.text && /\\b(import\\s|export\\s|import\\()/.test(scriptInfo.text)) {
            effectiveType = 'module';
          }
          if (effectiveType) newScript.type = effectiveType;
          if (scriptInfo.src) {
            newScript.src = scriptInfo.src;
            newScript.onload = function() { runScripts(scripts, idx + 1); };
            newScript.onerror = function() { runScripts(scripts, idx + 1); };
            content.appendChild(newScript);
          } else {
            newScript.textContent = scriptInfo.text;
            content.appendChild(newScript);
            runScripts(scripts, idx + 1);
          }
        } catch(e) {
          console.warn('[widget] script exec failed:', e);
          runScripts(scripts, idx + 1);
        }
      })(incomingScripts, 0);
    }
    reportHeight();
  }
});

function reportHeight() {
  var content = document.getElementById('content');
  if (!content) return;
  var clone = content.cloneNode(true);
  clone.style.cssText = 'position:fixed;top:-9999px;left:-9999px;width:' + content.offsetWidth + 'px;height:auto;overflow:hidden;visibility:hidden;pointer-events:none;';
  document.body.appendChild(clone);
  var h = clone.scrollHeight;
  document.body.removeChild(clone);
  window.parent.postMessage({ type: 'widget-resize', height: h }, '*');
}
var ro = new ResizeObserver(reportHeight);
ro.observe(document.getElementById('content') || document.body);
window.addEventListener('load', reportHeight);
var _resizeInterval = setInterval(reportHeight, 200);
setTimeout(function() { clearInterval(_resizeInterval); }, 15000);
`;

function assembleShell(initialHtml = "") {
  return `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <script type="importmap">
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
  </script>
  <meta http-equiv="Content-Security-Policy" content="
    default-src 'self';
    script-src 'unsafe-inline' 'unsafe-eval'
      https://cdnjs.cloudflare.com
      https://esm.sh
      https://cdn.jsdelivr.net
      https://unpkg.com;
    style-src 'unsafe-inline';
    img-src 'self' data: blob: https:;
    font-src 'self' data:;
    connect-src 'self' https:;
  ">
  <style>
    ${THEME_CSS}
    ${SVG_CLASSES_CSS}
    ${FORM_STYLES_CSS}
  </style>
</head>
<body>
  <div id="content">
    ${initialHtml}
  </div>
  <script>${IDIOMORPH_JS}</script>
  <script>
    ${BRIDGE_JS}
  </script>
</body>
</html>`;
}

const LOADING_PHRASES = [
  "Sketching pixels",
  "Wiring up nodes",
  "Painting gradients",
  "Compiling visuals",
  "Arranging atoms",
  "Rendering magic",
  "Polishing edges",
];

function useLoadingPhrase(active) {
  const [index, setIndex] = useState(0);
  useEffect(() => {
    if (!active) return undefined;
    const interval = setInterval(() => {
      setIndex((i) => (i + 1) % LOADING_PHRASES.length);
    }, 1800);
    return () => clearInterval(interval);
  }, [active]);
  return LOADING_PHRASES[index];
}

export function WidgetIframe({ title, html, onSendPrompt }) {
  const iframeRef = useRef(null);
  const [height, setHeight] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const shellReadyRef = useRef(false);
  const committedHtmlRef = useRef("");
  const [htmlSettled, setHtmlSettled] = useState(false);
  const [prevHtml, setPrevHtml] = useState(html);
  const settledTimerRef = useRef(null);

  const handleMessage = useCallback((e) => {
    if (
      iframeRef.current &&
      e.source === iframeRef.current.contentWindow &&
      e.data?.type === "widget-resize" &&
      typeof e.data.height === "number"
    ) {
      setHeight(Math.max(50, Math.min(e.data.height, 4000)));
    }
    if (
      iframeRef.current &&
      e.source === iframeRef.current.contentWindow &&
      e.data?.type === "send-prompt" &&
      typeof e.data.text === "string" &&
      onSendPrompt
    ) {
      onSendPrompt(e.data.text);
    }
    if (e.data?.type === "open-link" && typeof e.data.url === "string") {
      window.open(e.data.url, "_blank", "noopener,noreferrer");
    }
  }, [onSendPrompt]);

  useEffect(() => {
    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, [handleMessage]);

  if (html !== prevHtml) {
    setPrevHtml(html);
    setHtmlSettled(false);
  }

  useEffect(() => {
    if (!html || !iframeRef.current) return;

    if (!shellReadyRef.current) {
      shellReadyRef.current = true;
      iframeRef.current.srcdoc = assembleShell();
      return;
    }

    if (!loaded) return;

    if (html === committedHtmlRef.current) return;
    committedHtmlRef.current = html;

    const iframe = iframeRef.current;
    if (iframe.contentWindow) {
      iframe.contentWindow.postMessage(
        { type: "update-content", html },
        "*"
      );
    }
  }, [html, loaded]);

  useEffect(() => {
    if (!html) return undefined;
    if (settledTimerRef.current) clearTimeout(settledTimerRef.current);
    settledTimerRef.current = setTimeout(() => {
      setHtmlSettled(true);
    }, 800);
    return () => {
      if (settledTimerRef.current) clearTimeout(settledTimerRef.current);
    };
  }, [html]);

  useEffect(() => {
    if (!html || (loaded && height > 0)) return undefined;
    const timeout = setTimeout(() => {
      setLoaded(true);
      setHeight((h) => (h > 0 ? h : 400));
    }, 4000);
    return () => clearTimeout(timeout);
  }, [html, loaded, height]);

  const showIframe = !!html;
  const isStreaming = !!html && !htmlSettled;
  const loadingPhrase = useLoadingPhrase(isStreaming);

  return (
    <div className="chat-widget">
      {title ? <div className="chat-widget__title">{title}</div> : null}
      {isStreaming ? (
        <div className="chat-widget__loading">
          <span className="chat-widget__spinner" />
          <span className="chat-widget__loading-text">{loadingPhrase}...</span>
        </div>
      ) : null}
      <iframe
        ref={iframeRef}
        sandbox="allow-scripts allow-same-origin"
        className="chat-widget__iframe"
        onLoad={() => setLoaded(true)}
        style={{
          height: showIframe ? (height > 0 ? height : 320) : 0,
          opacity: showIframe ? 1 : 0,
          display: html ? undefined : "none",
        }}
        title={title || "Widget"}
      />
      {showIframe && htmlSettled ? (
        <ExportOverlay iframeRef={iframeRef} title={title} html={html} />
      ) : null}
    </div>
  );
}
