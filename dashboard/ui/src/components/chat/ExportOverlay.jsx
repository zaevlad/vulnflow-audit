// Floating action overlay anchored to the top-right corner of a
// WidgetIframe. Renders two buttons:
//
//   - PNG  → html2canvas-rasterizes the iframe document into a download.
//   - HTML → wraps the widget's raw HTML in a standalone document
//            (theme CSS + import map + bridge stubs) so the file works
//            when opened directly in a browser.
//
// The overlay only appears on hover/focus to avoid covering interactive
// widget content. When a capture is in flight the button shows a
// transient label; failures surface inline.

import { useCallback, useState } from "react";
import {
  assembleStandaloneHtml,
  capturePngFromIframe,
  slugify,
  triggerDownload,
} from "./exportUtils.js";

export function ExportOverlay({ iframeRef, title, html }) {
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState("");

  const safeTitle = title || "widget";
  const baseName = slugify(safeTitle);

  const exportHtml = useCallback(() => {
    setError("");
    setBusy("html");
    try {
      const doc = assembleStandaloneHtml(html || "", safeTitle);
      triggerDownload(doc, `${baseName}.html`, "text/html");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(null);
    }
  }, [html, safeTitle, baseName]);

  const exportPng = useCallback(async () => {
    setError("");
    setBusy("png");
    try {
      const iframe = iframeRef?.current;
      if (!iframe) throw new Error("Widget is not ready yet.");
      await capturePngFromIframe(iframe, { filename: `${baseName}.png` });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(null);
    }
  }, [iframeRef, baseName]);

  return (
    <div className="widget-export" role="group" aria-label="Export widget">
      <button
        type="button"
        className="widget-export__btn"
        onClick={exportPng}
        disabled={!!busy}
        title="Save widget as PNG"
      >
        {busy === "png" ? "…" : "PNG"}
      </button>
      <button
        type="button"
        className="widget-export__btn"
        onClick={exportHtml}
        disabled={!!busy}
        title="Save widget as standalone HTML"
      >
        {busy === "html" ? "…" : "HTML"}
      </button>
      {error ? <div className="widget-export__error">{error}</div> : null}
    </div>
  );
}
