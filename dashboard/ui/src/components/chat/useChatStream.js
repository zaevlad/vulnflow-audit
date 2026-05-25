// SSE consumer for /api/chat/send + /api/chat/streams/{id}.
//
// Uses fetch + ReadableStream (rather than EventSource) so we can send a
// POST body with the full UI context envelope. Each line `data: <json>`
// is parsed into an envelope part and dispatched via the supplied
// callbacks.
//
// Recovery contract:
//   - The first SSE event is always `{type:"init", thread_id, stream_id}`.
//   - Each subsequent part carries a monotonic `seq`.
//   - If the underlying connection drops *after* init but before the
//     server-side terminator, we transparently retry by issuing
//     `GET /api/chat/streams/{stream_id}?from_seq=<lastSeenSeq>`. Parts
//     already delivered are deduped on `seq`. Aborts (user pressed Stop)
//     are honored — they do not trigger a resume.

const SSE_DELIM = "\n\n";
const MAX_RESUME_ATTEMPTS = 3;

class StreamState {
  constructor() {
    this.streamId = null;
    this.threadId = null;
    this.lastSeenSeq = 0;
  }

  observe(part) {
    if (typeof part?.seq === "number" && part.seq > this.lastSeenSeq) {
      this.lastSeenSeq = part.seq;
      return true;
    }
    return false;
  }
}

async function consumeBody({ body, state, onInit, onPart, signal }) {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let terminated = false;

  while (true) {
    if (signal?.aborted) {
      throw new DOMException("Aborted by client", "AbortError");
    }
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let boundary;
    while ((boundary = buffer.indexOf(SSE_DELIM)) !== -1) {
      const rawEvent = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + SSE_DELIM.length);

      const lines = rawEvent.split("\n");
      const dataLines = [];
      for (const line of lines) {
        if (line.startsWith("data:")) {
          dataLines.push(line.slice(5).trim());
        }
      }
      if (!dataLines.length) continue;
      const dataStr = dataLines.join("\n");
      if (dataStr === "[DONE]") {
        terminated = true;
        return { terminated };
      }

      let parsed;
      try {
        parsed = JSON.parse(dataStr);
      } catch (err) {
        continue; // ignore corrupt chunk
      }

      if (parsed?.type === "init") {
        if (parsed.stream_id) state.streamId = parsed.stream_id;
        if (parsed.thread_id) {
          state.threadId = parsed.thread_id;
          onInit?.(parsed.thread_id, parsed.stream_id || state.streamId);
        }
        continue;
      }

      // Dedupe by seq — if the server is replaying older parts during a
      // resume we don't want to re-render them.
      if (typeof parsed?.seq === "number") {
        if (parsed.seq <= state.lastSeenSeq) continue;
        state.observe(parsed);
      }

      onPart?.(parsed);
    }
  }

  return { terminated };
}

async function fetchInitial({ payload, signal }) {
  return fetch("/api/chat/send", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
}

async function fetchResume({ streamId, fromSeq, signal }) {
  return fetch(
    `/api/chat/streams/${encodeURIComponent(streamId)}?from_seq=${fromSeq}`,
    { method: "GET", signal },
  );
}

async function readErrorDetail(response) {
  let detail = `HTTP ${response.status}`;
  try {
    const errJson = await response.json();
    if (errJson?.detail) detail = String(errJson.detail);
  } catch {
    try { detail = (await response.text()) || detail; } catch {}
  }
  return detail;
}

export async function streamChatSend({
  payload,
  signal,
  onInit,
  onPart,
  onDone,
  onError,
}) {
  const state = new StreamState();

  let response;
  try {
    response = await fetchInitial({ payload, signal });
  } catch (err) {
    if (err?.name !== "AbortError") onError?.(err);
    return;
  }

  if (!response.ok || !response.body) {
    onError?.(new Error(await readErrorDetail(response)));
    return;
  }

  let attempts = 0;
  let body = response.body;

  while (true) {
    try {
      const { terminated } = await consumeBody({
        body,
        state,
        onInit,
        onPart,
        signal,
      });
      if (terminated) {
        onDone?.();
        return;
      }
      // Connection closed without [DONE] sentinel — fall through to
      // resume logic.
    } catch (err) {
      if (err?.name === "AbortError" || signal?.aborted) {
        return;
      }
      // network error → resume below
    }

    if (!state.streamId) {
      onError?.(new Error("Stream interrupted before init; cannot resume."));
      return;
    }
    if (attempts >= MAX_RESUME_ATTEMPTS) {
      onError?.(new Error("Stream interrupted; resume attempts exhausted."));
      return;
    }
    attempts += 1;

    let resumeResponse;
    try {
      resumeResponse = await fetchResume({
        streamId: state.streamId,
        fromSeq: state.lastSeenSeq,
        signal,
      });
    } catch (err) {
      if (err?.name === "AbortError") return;
      // small backoff then retry
      await new Promise((resolve) => setTimeout(resolve, 400));
      continue;
    }

    if (!resumeResponse.ok || !resumeResponse.body) {
      onError?.(new Error(await readErrorDetail(resumeResponse)));
      return;
    }
    body = resumeResponse.body;
  }
}
