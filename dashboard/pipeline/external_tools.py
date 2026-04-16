from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import yaml

_PATH_PARAM_RE = re.compile(r"{([a-zA-Z_][a-zA-Z0-9_]*)}")
_HTTP_TIMEOUT = httpx.Timeout(timeout=90.0, connect=10.0)
_OPENAI_LIKE_PROVIDERS = {"chatgpt", "openrouter", "ollama", "lmstudio", "llama_cpp"}


def load_external_tools(conf_path: Path) -> list[dict[str, Any]]:
    if not conf_path.exists():
        return []
    try:
        raw = yaml.safe_load(conf_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    if not isinstance(raw, dict):
        return []

    raw_tools = raw.get("tools", [])
    if not isinstance(raw_tools, list):
        return []

    tools: list[dict[str, Any]] = []
    for item in raw_tools:
        if not isinstance(item, dict):
            continue
        tool_name = str(item.get("name", "")).strip()
        base_url = str(item.get("base_url", "")).strip()
        if not tool_name or not base_url:
            continue

        endpoints_raw = item.get("endpoints", [])
        endpoints: list[dict[str, Any]] = []
        if isinstance(endpoints_raw, list):
            for endpoint in endpoints_raw:
                if not isinstance(endpoint, dict):
                    continue
                endpoint_name = str(endpoint.get("name", "")).strip()
                endpoint_path = str(endpoint.get("path", "")).strip()
                method = str(endpoint.get("method", "GET")).strip().upper() or "GET"
                if not endpoint_name or not endpoint_path:
                    continue

                parameters_raw = endpoint.get("parameters", [])
                parameters: list[dict[str, Any]] = []
                if isinstance(parameters_raw, list):
                    for param in parameters_raw:
                        if not isinstance(param, dict):
                            continue
                        param_name = str(param.get("name", "")).strip()
                        if not param_name:
                            continue
                        param_type = str(param.get("type", "string")).strip().lower() or "string"
                        if param_type not in {"string", "integer", "number", "boolean", "array", "object"}:
                            param_type = "string"
                        parameters.append(
                            {
                                "name": param_name,
                                "type": param_type,
                                "required": bool(param.get("required", False)),
                                "description": str(param.get("description", "")).strip(),
                            }
                        )

                endpoints.append(
                    {
                        "name": endpoint_name,
                        "path": endpoint_path,
                        "method": method,
                        "description": str(endpoint.get("description", "")).strip(),
                        "parameters": parameters,
                    }
                )

        tools.append(
            {
                "name": tool_name,
                "base_url": base_url.rstrip("/"),
                "auth_type": str(item.get("auth_type", "")).strip().lower(),
                "api_key": str(item.get("api_key", "")).strip(),
                "api_key_header": str(item.get("api_key_header", "")).strip(),
                "description": str(item.get("description", "")).strip(),
                "endpoints": endpoints,
            }
        )
    return tools


def external_tool_catalog_entries(conf_path: Path) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for tool in load_external_tools(conf_path):
        entries.append(
            {
                "name": tool["name"],
                "path": tool["name"],
                "kind": "tool",
            }
        )
    return sorted(entries, key=lambda item: item["name"].lower())


def build_provider_tool_schema(
    *,
    provider: str,
    selected_tool_name: str,
    conf_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    selected = None
    for tool in load_external_tools(conf_path):
        if tool["name"] == selected_tool_name:
            selected = tool
            break
    if not selected:
        return [], {}

    endpoint_index: dict[str, dict[str, Any]] = {}
    tools_payload: list[dict[str, Any]] = []

    for endpoint in selected.get("endpoints", []):
        function_name = f"{selected['name']}_{endpoint['name']}"
        input_schema = _build_input_schema(endpoint.get("parameters", []))
        endpoint_desc = endpoint.get("description", "")
        tool_desc = selected.get("description", "")
        description = endpoint_desc
        if endpoint_desc and tool_desc:
            description = f"{endpoint_desc}. {tool_desc}"
        elif tool_desc:
            description = tool_desc

        endpoint_index[function_name] = {"tool": selected, "endpoint": endpoint}

        if provider == "claude":
            tools_payload.append(
                {
                    "name": function_name,
                    "description": description,
                    "input_schema": input_schema,
                }
            )
        elif provider in _OPENAI_LIKE_PROVIDERS:
            tools_payload.append(
                {
                    "type": "function",
                    "function": {
                        "name": function_name,
                        "description": description,
                        "parameters": input_schema,
                    },
                }
            )

    return tools_payload, endpoint_index


def normalize_model_tool_calls(tool_calls: Any) -> list[dict[str, Any]]:
    if not isinstance(tool_calls, list):
        return []

    normalized: list[dict[str, Any]] = []
    for item in tool_calls:
        if not isinstance(item, dict):
            continue

        call_id = str(item.get("id", "")).strip() or f"call_{uuid4().hex[:8]}"
        name = ""
        args: dict[str, Any] = {}

        function_block = item.get("function")
        if isinstance(function_block, dict):
            name = str(function_block.get("name", "")).strip()
            raw_args = function_block.get("arguments", {})
            args = _parse_arguments(raw_args)
        elif item.get("type") == "tool_use":
            name = str(item.get("name", "")).strip()
            raw_input = item.get("input", {})
            args = raw_input if isinstance(raw_input, dict) else {}
        else:
            name = str(item.get("name", "")).strip()
            if isinstance(item.get("arguments"), dict):
                args = item.get("arguments", {})
            elif isinstance(item.get("input"), dict):
                args = item.get("input", {})

        if not name:
            continue
        normalized.append({"id": call_id, "name": name, "arguments": args})
    return normalized


def append_tool_messages(
    *,
    provider: str,
    messages: list[dict[str, Any]],
    assistant_text: str,
    tool_calls: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
) -> None:
    if provider == "claude":
        assistant_blocks: list[dict[str, Any]] = []
        if assistant_text.strip():
            assistant_blocks.append({"type": "text", "text": assistant_text})
        for call in tool_calls:
            assistant_blocks.append(
                {
                    "type": "tool_use",
                    "id": call["id"],
                    "name": call["name"],
                    "input": call["arguments"],
                }
            )
        messages.append({"role": "assistant", "content": assistant_blocks})

        result_blocks = [
            {
                "type": "tool_result",
                "tool_use_id": result["id"],
                "content": json.dumps(result["output"], ensure_ascii=False),
            }
            for result in tool_results
        ]
        messages.append({"role": "user", "content": result_blocks})
        return

    assistant_payload: dict[str, Any] = {"role": "assistant", "content": assistant_text}
    openai_calls = []
    for call in tool_calls:
        openai_calls.append(
            {
                "id": call["id"],
                "type": "function",
                "function": {
                    "name": call["name"],
                    "arguments": json.dumps(call["arguments"], ensure_ascii=False),
                },
            }
        )
    if openai_calls:
        assistant_payload["tool_calls"] = openai_calls
    messages.append(assistant_payload)

    for result in tool_results:
        messages.append(
            {
                "role": "tool",
                "tool_call_id": result["id"],
                "content": json.dumps(result["output"], ensure_ascii=False),
            }
        )


async def execute_external_tool_call(
    *,
    endpoint_ref: dict[str, Any],
    arguments: dict[str, Any],
) -> dict[str, Any]:
    tool = endpoint_ref["tool"]
    endpoint = endpoint_ref["endpoint"]

    method = str(endpoint.get("method", "GET")).upper()
    path_template = str(endpoint.get("path", "")).strip()
    headers: dict[str, str] = {"Accept": "application/json"}
    auth_type = str(tool.get("auth_type", "")).lower()
    if auth_type == "bearer" and tool.get("api_key"):
        headers["Authorization"] = f"Bearer {tool['api_key']}"
    elif auth_type == "api_key_header" and tool.get("api_key_header") and tool.get("api_key"):
        headers[str(tool["api_key_header"])] = str(tool["api_key"])

    remaining_args = dict(arguments or {})
    path_params = _PATH_PARAM_RE.findall(path_template)
    for name in path_params:
        if name not in remaining_args:
            return {"ok": False, "error": f"Missing path parameter: {name}"}
        value = str(remaining_args.pop(name))
        path_template = path_template.replace(f"{{{name}}}", value)

    url = f"{tool['base_url']}/{path_template.lstrip('/')}"
    request_kwargs: dict[str, Any] = {"method": method, "url": url, "headers": headers}
    if method in {"GET", "DELETE"}:
        request_kwargs["params"] = remaining_args
    else:
        headers["Content-Type"] = "application/json"
        request_kwargs["json"] = remaining_args

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            response = await client.request(**request_kwargs)
    except Exception as exc:
        return {
            "ok": False,
            "error": "request_failed",
            "details": str(exc),
            "method": method,
            "url": url,
        }

    try:
        payload: Any = response.json()
    except Exception:
        payload = response.text

    return {
        "ok": response.is_success,
        "status_code": response.status_code,
        "method": method,
        "url": url,
        "data": payload,
    }


def _build_input_schema(parameters: list[dict[str, Any]]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []

    for param in parameters:
        name = param["name"]
        param_type = param.get("type", "string")
        schema: dict[str, Any] = {"type": param_type}
        if param_type == "array":
            schema["items"] = {"type": "string"}
        if param.get("description"):
            schema["description"] = param["description"]
        properties[name] = schema
        if param.get("required"):
            required.append(name)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _parse_arguments(raw_args: Any) -> dict[str, Any]:
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str):
        raw_args = raw_args.strip()
        if not raw_args:
            return {}
        try:
            parsed = json.loads(raw_args)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}
