// Client-side helpers for managing chat-message attachments.
//
//   - uploadAttachment(threadId, file) → AttachmentRecord
//   - deleteAttachment(id)
//   - attachmentContentUrl(id) → backend URL serving the raw bytes
//   - supportsVision(model) → quick model-name allowlist matching the
//     backend's provider_supports_vision check.

export const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024; // 10 MB

const VISION_PATTERNS = [
  "gpt-4o",
  "gpt-4-vision",
  "gpt-4.1",
  "gpt-5",
  "claude-3",
  "claude-sonnet",
  "claude-opus",
  "claude-haiku-4",
  "llava",
  "qwen-vl",
  "qwen2-vl",
  "gemma-vision",
  "pixtral",
  "vision",
];

export function supportsVision(model) {
  if (!model) return false;
  const needle = String(model).toLowerCase();
  return VISION_PATTERNS.some((p) => needle.includes(p));
}

const ALLOWED_IMAGE_MIMES = new Set([
  "image/png",
  "image/jpeg",
  "image/jpg",
  "image/gif",
  "image/webp",
]);
const ALLOWED_PDF_MIMES = new Set(["application/pdf"]);

export function classifyFile(file) {
  const mime = (file?.type || "").toLowerCase();
  if (ALLOWED_IMAGE_MIMES.has(mime)) return "image";
  if (ALLOWED_PDF_MIMES.has(mime)) return "pdf";
  return null;
}

export function attachmentContentUrl(id) {
  return `/api/chat/attachments/${encodeURIComponent(id)}/content`;
}

export async function uploadAttachment(threadId, file) {
  if (!threadId) throw new Error("Cannot upload without an active thread.");
  if (!file) throw new Error("No file provided.");
  if (file.size > MAX_ATTACHMENT_BYTES) {
    throw new Error(
      `File too large (${(file.size / 1024 / 1024).toFixed(1)} MB > 10 MB limit).`,
    );
  }
  if (!classifyFile(file)) {
    throw new Error(`Unsupported file type: ${file.type || "(unknown)"}`);
  }
  const fd = new FormData();
  fd.set("thread_id", threadId);
  fd.set("file", file, file.name || "upload");

  const res = await fetch("/api/chat/attachments", {
    method: "POST",
    body: fd,
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const j = await res.json();
      if (j?.detail) detail = String(j.detail);
    } catch {}
    throw new Error(detail);
  }
  const payload = await res.json();
  return payload.attachment;
}

export async function deleteAttachment(id) {
  if (!id) return false;
  const res = await fetch(`/api/chat/attachments/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  return res.ok;
}

export function humanFileSize(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
