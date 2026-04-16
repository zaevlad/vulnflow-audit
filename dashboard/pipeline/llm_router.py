from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import yaml

_TIMEOUT = httpx.Timeout(timeout=300.0, connect=15.0)

_JSON_ONLY_INSTRUCTION = (
    "Return valid JSON only. Do not use markdown fences. "
    "Do not add explanations before or after the JSON."
)

_PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "chatgpt": {
        "base_url": "https://api.openai.com/v1",
        "env_key": "OPENAI_API_KEY",
        "supports_response_format": True,
    },
    "claude": {
        "base_url": "https://api.anthropic.com/v1",
        "env_key": "ANTHROPIC_API_KEY",
        "supports_response_format": True,
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "env_key": "OPENROUTER_API_KEY",
        "supports_response_format": True,
    },
    "ollama": {
        "base_url": "http://localhost:11434",
        "env_key": "",
        "supports_response_format": False,
    },
    "lmstudio": {
        "base_url": "http://localhost:1234/v1",
        "env_key": "",
        "supports_response_format": False,
    },
    "llama_cpp": {
        "base_url": "http://localhost:8080/v1",
        "env_key": "",
        "supports_response_format": False,
    },
}


def _load_project_conf() -> dict[str, Any]:
    conf_path = _get_project_conf_path()
    if not conf_path.exists():
        return {}
    try:
        raw = yaml.safe_load(conf_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _get_provider_section(provider: str) -> dict[str, Any]:
    raw = _load_project_conf()
    models = raw.get("models") if isinstance(raw, dict) else {}
    if not isinstance(models, dict):
        return {}
    section = models.get(provider, {})
    return section if isinstance(section, dict) else {}


def _read_named_env(var_name: str) -> str:
    name = str(var_name or "").strip()
    return os.environ.get(name, "") if name else ""


def _get_api_key(provider: str) -> str:
    section = _get_provider_section(provider)
    direct_value = str(section.get("api_key", "")).strip()
    if direct_value:
        return direct_value

    conf_env_value = _read_named_env(section.get("api_key_env", ""))
    if conf_env_value:
        return conf_env_value

    env_key = _PROVIDER_DEFAULTS.get(provider, {}).get("env_key", "")
    if not env_key:
        return ""
    return os.environ.get(env_key, "")


def _get_base_url(provider: str) -> str:
    section = _get_provider_section(provider)
    direct_value = str(section.get("base_url", "")).strip()
    if direct_value:
        return direct_value.rstrip("/")

    conf_env_value = _read_named_env(section.get("base_url_env", ""))
    if conf_env_value:
        return conf_env_value.rstrip("/")

    env_override = os.environ.get(f"{provider.upper()}_BASE_URL", "")
    if env_override:
        return env_override.rstrip("/")
    return _PROVIDER_DEFAULTS.get(provider, {}).get("base_url", "").rstrip("/")


def _get_project_conf_path() -> Path:
    return Path(__file__).resolve().parents[2] / "conf.yaml"


def _provider_supports_response_format(provider: str) -> bool:
    default = bool(_PROVIDER_DEFAULTS.get(provider, {}).get("supports_response_format", False))
    section = _get_provider_section(provider)
    if "supports_response_format" not in section:
        return default
    return bool(section.get("supports_response_format"))


def _inject_json_fallback_instruction(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    patched = [dict(message) for message in messages]

    for message in patched:
        if message.get("role") != "system":
            continue
        content = str(message.get("content", "")).rstrip()
        message["content"] = (
            f"{content}\n\n{_JSON_ONLY_INSTRUCTION}" if content else _JSON_ONLY_INSTRUCTION
        )
        return patched

    patched.insert(0, {"role": "system", "content": _JSON_ONLY_INSTRUCTION})
    return patched


def _build_anthropic_messages_body(
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    *,
    include_max_tokens: bool,
) -> dict[str, Any]:
    system_parts = [m["content"] for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]

    body: dict[str, Any] = {
        "model": model,
        "messages": non_system,
    }
    if include_max_tokens:
        body["max_tokens"] = 16384
    if system_parts:
        body["system"] = "\n\n".join(system_parts)
    if tools:
        body["tools"] = tools
    return body


def _fallback_token_estimate(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
) -> int:
    payload = json.dumps(
        {
            "messages": messages,
            "tools": tools or [],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return max(1, (len(payload.encode("utf-8")) + 3) // 4)


def _resolve_anthropic_count_model(model: str) -> str:
    candidate = (model or "").strip()
    if candidate and "claude" in candidate.lower():
        return candidate
    override = os.environ.get("ANTHROPIC_TOKENIZER_MODEL", "").strip()
    if override:
        return override
    return "claude-3-5-haiku"


# ---------------------------------------------------------------------------
# OpenAI-compatible call (ChatGPT, OpenRouter, LM Studio, llama.cpp)
# ---------------------------------------------------------------------------

async def _call_openai_compatible(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    json_mode: bool,
    tools: list[dict[str, Any]] | None,
    supports_response_format: bool,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if extra_headers:
        headers.update(extra_headers)

    body: dict[str, Any] = {"model": model, "messages": messages}
    if json_mode and supports_response_format:
        body["response_format"] = {"type": "json_object"}
    if tools:
        body["tools"] = tools

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(f"{base_url}/chat/completions", headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()

    choice = data.get("choices", [{}])[0]
    message = choice.get("message", {})
    return {
        "content": message.get("content", ""),
        "tool_calls": message.get("tool_calls"),
        "finish_reason": choice.get("finish_reason"),
        "usage": data.get("usage"),
    }


# ---------------------------------------------------------------------------
# Anthropic Claude
# ---------------------------------------------------------------------------

async def _call_anthropic(
    model: str,
    messages: list[dict[str, Any]],
    json_mode: bool,
    tools: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    base_url = _get_base_url("claude")
    api_key = _get_api_key("claude")

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    body = _build_anthropic_messages_body(model, messages, tools, include_max_tokens=True)

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(f"{base_url}/messages", headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()

    content_blocks = data.get("content", [])
    text_parts = [b["text"] for b in content_blocks if b.get("type") == "text"]
    tool_use_blocks = [b for b in content_blocks if b.get("type") == "tool_use"]

    return {
        "content": "\n".join(text_parts),
        "tool_calls": tool_use_blocks if tool_use_blocks else None,
        "finish_reason": data.get("stop_reason"),
        "usage": data.get("usage"),
    }


async def count_claude_input_tokens(
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> int:
    base_url = _get_base_url("claude")
    api_key = _get_api_key("claude")
    if not api_key:
        return _fallback_token_estimate(messages, tools)

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    body = _build_anthropic_messages_body(
        _resolve_anthropic_count_model(model),
        messages,
        tools,
        include_max_tokens=False,
    )

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(f"{base_url}/messages/count_tokens", headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return _fallback_token_estimate(messages, tools)

    input_tokens = data.get("input_tokens")
    if isinstance(input_tokens, int) and input_tokens >= 0:
        return input_tokens
    return _fallback_token_estimate(messages, tools)


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------

async def _call_ollama(
    model: str,
    messages: list[dict[str, Any]],
    json_mode: bool,
    tools: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    base_url = _get_base_url("ollama")

    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
    }
    if json_mode:
        body["format"] = "json"
    if tools:
        body["tools"] = tools

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(f"{base_url}/api/chat", headers={"Content-Type": "application/json"}, json=body)
        resp.raise_for_status()
        data = resp.json()

    message = data.get("message", {})
    return {
        "content": message.get("content", ""),
        "tool_calls": message.get("tool_calls"),
        "finish_reason": data.get("done_reason"),
        "usage": {
            "prompt_tokens": data.get("prompt_eval_count"),
            "completion_tokens": data.get("eval_count"),
        },
    }


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------

async def send_to_model(
    provider: str,
    model: str,
    messages: list[dict[str, Any]],
    json_mode: bool = True,
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Route a request to the provider+model selected by the user in the node."""

    supports_response_format = _provider_supports_response_format(provider)
    effective_messages = (
        _inject_json_fallback_instruction(messages)
        if json_mode and not supports_response_format
        else messages
    )

    if provider == "chatgpt":
        return await _call_openai_compatible(
            _get_base_url("chatgpt"), _get_api_key("chatgpt"),
            model, effective_messages, json_mode, tools, supports_response_format,
        )

    if provider == "claude":
        return await _call_anthropic(model, effective_messages, json_mode, tools)

    if provider == "openrouter":
        return await _call_openai_compatible(
            _get_base_url("openrouter"), _get_api_key("openrouter"),
            model, effective_messages, json_mode, tools, supports_response_format,
            extra_headers={"HTTP-Referer": "https://vulnflow.local", "X-Title": "VulnFlow"},
        )

    if provider == "ollama":
        return await _call_ollama(model, effective_messages, json_mode, tools)

    if provider == "lmstudio":
        return await _call_openai_compatible(
            _get_base_url("lmstudio"), "",
            model, effective_messages, json_mode, tools, supports_response_format,
        )

    if provider == "llama_cpp":
        return await _call_openai_compatible(
            _get_base_url("llama_cpp"), "",
            model, effective_messages, json_mode, tools, supports_response_format,
        )

    raise ValueError(f"Unknown provider: {provider!r}")


def parse_model_json(raw_content: str) -> dict[str, Any]:
    """Extract JSON from model response, handling markdown fences."""
    text = raw_content.strip()
    if text.startswith("```"):
        first_nl = text.index("\n") if "\n" in text else 3
        text = text[first_nl:].strip()
        if text.endswith("```"):
            text = text[:-3].strip()
    return json.loads(text)
