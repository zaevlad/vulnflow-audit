"""Context-window management for the chat agent.

Two responsibilities:

1. `count_tokens` — best-effort token count for an arbitrary `messages`
   array, using the provider's native tokenizer when available:
     * Claude → `count_claude_input_tokens` (calls Anthropic's
       `/messages/count_tokens` endpoint).
     * OpenAI-compatible (chatgpt / openrouter / lmstudio / llama_cpp /
       ollama) → `tiktoken` when installed.
     * Fallback → `(bytes + 3) // 4`, same heuristic used by
       `llm_router._fallback_token_estimate`.

2. `compact_history` — when the token count exceeds the configured
   ceiling, replace the older user/assistant messages with a single
   `system: "Earlier conversation summary: …"` message produced by a
   cheap secondary LLM call. The last `keep_last_messages` are kept
   verbatim. System messages are never dropped.

The behavior is driven by a `chat.context` block in `conf.yaml`:

    chat:
      context:
        max_input_tokens: 120000
        keep_last_messages: 8
        summary_model:
          provider: claude
          model: claude-3-5-haiku

If the config block is absent, sensible defaults kick in and
compaction effectively becomes a no-op until the message body is very
large.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from dashboard.pipeline.llm_router import (
    _fallback_token_estimate,
    count_claude_input_tokens,
    send_to_model,
)


@dataclass
class SummaryModelConfig:
    provider: str = "claude"
    model: str = "claude-3-5-haiku"


@dataclass
class ContextConfig:
    max_input_tokens: int = 120_000
    keep_last_messages: int = 8
    summary_model: SummaryModelConfig = field(default_factory=SummaryModelConfig)
    enabled: bool = True


@dataclass
class CompactionInfo:
    """Result metadata returned from `compact_history`."""

    input_tokens_before: int
    input_tokens_after: int
    kept_messages: int
    compacted: bool
    compacted_messages: int
    summary_provider: Optional[str] = None
    summary_model: Optional[str] = None
    summary_error: Optional[str] = None


def load_context_config(conf_path: str | Path) -> ContextConfig:
    """Load `chat.context` from conf.yaml with safe defaults."""

    conf = ContextConfig()
    path = Path(conf_path)
    if not path.exists():
        return conf
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return conf
    if not isinstance(raw, dict):
        return conf

    chat_section = raw.get("chat")
    if not isinstance(chat_section, dict):
        return conf
    ctx = chat_section.get("context")
    if not isinstance(ctx, dict):
        return conf

    max_tokens = ctx.get("max_input_tokens")
    if isinstance(max_tokens, int) and max_tokens > 0:
        conf.max_input_tokens = max_tokens

    keep_last = ctx.get("keep_last_messages")
    if isinstance(keep_last, int) and keep_last >= 0:
        conf.keep_last_messages = keep_last

    if "enabled" in ctx:
        conf.enabled = bool(ctx.get("enabled"))

    sm = ctx.get("summary_model")
    if isinstance(sm, dict):
        provider = str(sm.get("provider") or "").strip() or conf.summary_model.provider
        model = str(sm.get("model") or "").strip() or conf.summary_model.model
        conf.summary_model = SummaryModelConfig(provider=provider, model=model)

    return conf


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------


def _tiktoken_count(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    model: str,
) -> Optional[int]:
    try:
        import tiktoken  # type: ignore
    except Exception:
        return None
    try:
        try:
            enc = tiktoken.encoding_for_model(model)
        except Exception:
            enc = tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None
    try:
        payload = json.dumps(
            {"messages": messages, "tools": tools or []},
            ensure_ascii=False,
            sort_keys=True,
        )
        return max(1, len(enc.encode(payload)))
    except Exception:
        return None


async def count_tokens(
    messages: list[dict[str, Any]],
    *,
    provider: str,
    model: str,
    tools: list[dict[str, Any]] | None = None,
) -> int:
    """Return the best-effort input-token count for the given messages."""

    if provider == "claude":
        try:
            return await count_claude_input_tokens(model, messages, tools)
        except Exception:
            return _fallback_token_estimate(messages, tools)

    tk = _tiktoken_count(messages, tools, model)
    if isinstance(tk, int) and tk >= 0:
        return tk

    return _fallback_token_estimate(messages, tools)


# ---------------------------------------------------------------------------
# Compaction
# ---------------------------------------------------------------------------


_SUMMARY_SYSTEM_PROMPT = (
    "You are summarizing the earlier part of a developer chat about a"
    " Solidity audit. Compress the messages below into a dense bullet"
    " list (max ~25 bullets) that preserves: open questions, decisions"
    " already taken, file paths or contracts mentioned, agreed scope,"
    " and any pending actions. Skip pleasantries. Output Markdown bullets"
    " only — no preamble, no closing remarks."
)


def _split_messages(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split into (system_messages, conversation_messages) preserving order."""

    system_msgs: list[dict[str, Any]] = []
    convo_msgs: list[dict[str, Any]] = []
    for m in messages:
        if m.get("role") == "system":
            system_msgs.append(m)
        else:
            convo_msgs.append(m)
    return system_msgs, convo_msgs


def _format_messages_for_summary(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for m in messages:
        role = str(m.get("role") or "user")
        content = str(m.get("content") or "")
        if not content.strip():
            continue
        lines.append(f"### {role}\n{content.strip()}")
    return "\n\n".join(lines)


async def _summarize(
    summary_model: SummaryModelConfig,
    to_summarize: list[dict[str, Any]],
) -> tuple[Optional[str], Optional[str]]:
    if not to_summarize:
        return None, "nothing_to_summarize"
    transcript = _format_messages_for_summary(to_summarize)
    if not transcript:
        return None, "empty_transcript"

    summarizer_messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Earlier conversation transcript to compress:\n\n"
                + transcript
            ),
        },
    ]

    try:
        response = await send_to_model(
            provider=summary_model.provider,
            model=summary_model.model,
            messages=summarizer_messages,
            json_mode=False,
            tools=None,
        )
    except Exception as exc:
        return None, f"summary_call_failed: {exc}"

    content = str(response.get("content") or "").strip()
    if not content:
        return None, "summary_empty"
    return content, None


async def compact_history(
    messages: list[dict[str, Any]],
    *,
    provider: str,
    model: str,
    tools: list[dict[str, Any]] | None,
    config: ContextConfig,
) -> tuple[list[dict[str, Any]], CompactionInfo]:
    """Return possibly-compacted messages plus a CompactionInfo record."""

    tokens_before = await count_tokens(messages, provider=provider, model=model, tools=tools)

    if not config.enabled or tokens_before <= config.max_input_tokens:
        return messages, CompactionInfo(
            input_tokens_before=tokens_before,
            input_tokens_after=tokens_before,
            kept_messages=len(messages),
            compacted=False,
            compacted_messages=0,
        )

    system_msgs, convo_msgs = _split_messages(messages)
    keep = max(0, int(config.keep_last_messages))
    if keep >= len(convo_msgs):
        # Nothing older than the keep window — can't compact.
        return messages, CompactionInfo(
            input_tokens_before=tokens_before,
            input_tokens_after=tokens_before,
            kept_messages=len(messages),
            compacted=False,
            compacted_messages=0,
        )

    older = convo_msgs[: len(convo_msgs) - keep]
    recent = convo_msgs[len(convo_msgs) - keep :]

    summary_text, summary_error = await _summarize(config.summary_model, older)
    if summary_text is None:
        # Summary failed — fall back to a deterministic dropping note so
        # the model at least knows context was clipped.
        summary_text = (
            f"[Earlier {len(older)} messages were dropped from context "
            "because the conversation exceeded the input-token ceiling.]"
        )

    summary_message = {
        "role": "system",
        "content": (
            "Earlier conversation summary (compacted to fit context window):\n\n"
            + summary_text
        ),
    }

    new_messages = system_msgs + [summary_message] + recent

    tokens_after = await count_tokens(new_messages, provider=provider, model=model, tools=tools)

    return new_messages, CompactionInfo(
        input_tokens_before=tokens_before,
        input_tokens_after=tokens_after,
        kept_messages=len(recent),
        compacted=True,
        compacted_messages=len(older),
        summary_provider=config.summary_model.provider,
        summary_model=config.summary_model.model,
        summary_error=summary_error,
    )
