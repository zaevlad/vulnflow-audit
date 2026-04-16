from __future__ import annotations

import json
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dashboard.docs_rag import search_relevant_chunks
from dashboard.pipeline.external_tools import (
    append_tool_messages,
    build_provider_tool_schema,
    execute_external_tool_call,
    normalize_model_tool_calls,
)
from dashboard.pipeline.llm_router import (
    count_claude_input_tokens,
    parse_model_json,
    send_to_model,
)
from dashboard.pipeline.schemas import (
    AccumulatorEntry,
    AgentOutput,
    BlockStatus,
    ErrorInfo,
    ToolCallRequest,
    ToolCallResult,
)

MAX_TOOL_CALL_ITERATIONS = 20


def _read_contract_files(paths: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for p in paths:
        fp = Path(p)
        if fp.is_file():
            try:
                result[str(fp)] = fp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                result[str(fp)] = f"[error reading {fp}]"
    return result


def _read_skill_file(skill_name: str, skill_dir: Path) -> str:
    if not skill_name:
        return ""
    candidate = skill_dir / f"{skill_name}.md"
    if candidate.is_file():
        return candidate.read_text(encoding="utf-8", errors="replace")
    for child in skill_dir.iterdir():
        if child.stem == skill_name and child.is_file():
            return child.read_text(encoding="utf-8", errors="replace")
    return ""


def _read_memory_context_file(memory_file_path: str, *, required: bool = False) -> str:
    path = str(memory_file_path or "").strip()
    if not path:
        return ""
    fp = Path(path)
    if not fp.is_file():
        if required:
            raise RuntimeError("Selected memory file could not be found.")
        return ""
    try:
        return fp.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        if required:
            raise RuntimeError(f"Unable to read selected memory file: {exc}") from exc
        return ""


def _build_system_prompt(
    node_data: dict[str, Any],
    workspace_root: Path,
) -> str:
    skill_text = ""
    if node_data.get("skill"):
        skill_text = _read_skill_file(node_data["skill"], workspace_root / "skills")
    elif node_data.get("leadSkill"):
        skill_text = _read_skill_file(node_data["leadSkill"], workspace_root / "lead_skills")

    if not skill_text:
        skill_text = "You are a smart contract security auditor. Analyze the provided code and return your findings as JSON."

    return skill_text


def _build_messages(
    system_prompt: str,
    contracts: dict[str, str],
    code_injected_data: dict[str, Any] | None,
    memory_context: str = "",
    relevant_docs: list[dict[str, Any]] | None = None,
    relevant_queries: list[str] | None = None,
    tool_result_message: dict[str, Any] | None = None,
    message_history: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if message_history:
        messages = list(message_history)
        if tool_result_message:
            messages.append(tool_result_message)
        return messages

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]

    user_payload: dict[str, Any] = {}
    if contracts:
        user_payload["contracts"] = contracts
    if memory_context:
        user_payload["memory_context"] = memory_context
    if code_injected_data:
        user_payload["injected_context"] = code_injected_data
    if relevant_queries:
        user_payload["docs_queries"] = relevant_queries
    if relevant_docs:
        user_payload["relevant_docs"] = relevant_docs

    messages.append({"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)})
    return messages


def _strip_markdown_fences(raw_content: str) -> str:
    text = (raw_content or "").strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        text = text[first_nl + 1:].strip() if first_nl != -1 else text[3:].strip()
        if text.endswith("```"):
            text = text[:-3].strip()
    return text


def _parse_string_array(raw_content: str) -> list[str]:
    text = _strip_markdown_fences(raw_content)
    parsed = json.loads(text)
    if not isinstance(parsed, list):
        return []
    out: list[str] = []
    for item in parsed:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out[:5]


def _format_contract_code_for_query_prompt(
    contracts: dict[str, str],
    node_data: dict[str, Any],
    code_injected_data: dict[str, Any] | None,
) -> str:
    sections: list[str] = []
    if contracts:
        for path, content in contracts.items():
            sections.append(f"=== CONTRACT: {path} ===\n{content}")

    cluster = str(node_data.get("cluster", "") or "").strip()
    if cluster:
        sections.append(f"=== SELECTED CLUSTER ===\n{cluster}")

    if code_injected_data:
        sections.append(
            "=== INJECTED CONTEXT ===\n"
            + json.dumps(code_injected_data, ensure_ascii=False, indent=2)
        )

    return "\n\n".join(sections)


def _build_relevant_docs_seed_prompt(
    contracts: dict[str, str],
    node_data: dict[str, Any],
    code_injected_data: dict[str, Any] | None,
) -> str:
    contract_code = _format_contract_code_for_query_prompt(contracts, node_data, code_injected_data)
    if not contract_code:
        return ""
    return (
        "You are analyzing a Solidity smart contract. Your task is NOT to audit it - only to identify "
        "what protocol mechanisms and concepts are implemented in this code.\n"
        "Based on the code below, generate 3-5 short search queries in natural language that would help "
        "find relevant sections in the protocol technical documentation. Each query should describe a "
        "mechanism, concept, or relationship present in the code - not a vulnerability.\n"
        "Return only a JSON array of strings, nothing else.\n"
        "Contract code:\n"
        f"{contract_code}"
    )


async def _build_relevant_docs_context(
    *,
    provider: str,
    model: str,
    contracts: dict[str, str],
    node_data: dict[str, Any],
    code_injected_data: dict[str, Any] | None,
    workspace_root: Path,
    on_event: Any = None,
    block_id: str = "",
) -> tuple[list[str], list[dict[str, Any]]]:
    prompt = _build_relevant_docs_seed_prompt(contracts, node_data, code_injected_data)
    if not prompt:
        return [], []

    if on_event:
        await on_event(
            {
                "type": "agent_docs_request",
                "block_id": block_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        await on_event(
            {
                "type": "agent_docs_status",
                "block_id": block_id,
                "stage": "request_sent",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    try:
        raw_response = await send_to_model(
            provider=provider,
            model=model,
            messages=[{"role": "user", "content": prompt}],
            json_mode=False,
        )
        queries = _parse_string_array(raw_response.get("content", ""))
        matches = search_relevant_chunks(
            workspace_root,
            queries,
            top_k_per_query=3,
            min_similarity=0.85,
        ) if queries else []
    except Exception as exc:
        if on_event:
            await on_event(
                {
                    "type": "agent_docs_status",
                    "block_id": block_id,
                    "stage": "error",
                    "message": str(exc),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
        raise

    if on_event:
        await on_event(
            {
                "type": "agent_docs_status",
                "block_id": block_id,
                "stage": "data_received",
                "queries_count": len(queries),
                "docs_count": len(matches),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    if not queries:
        return [], []
    if not matches:
        return queries, []

    by_file: dict[str, dict[str, Any]] = {}
    for item in sorted(matches, key=lambda row: row["similarity"], reverse=True):
        rel_path = item["rel_path"]
        entry = by_file.setdefault(
            rel_path,
            {
                "file": rel_path,
                "max_similarity": item["similarity"],
                "chunks": [],
            },
        )
        entry["max_similarity"] = max(entry["max_similarity"], item["similarity"])
        exists = any(chunk["id"] == item["id"] for chunk in entry["chunks"])
        if exists:
            continue
        entry["chunks"].append(
            {
                "id": item["id"],
                "similarity": round(float(item["similarity"]), 4),
                "chunk_index": item["chunk_index"],
                "text": item["chunk_text"],
            }
        )

    relevant_docs = sorted(
        by_file.values(),
        key=lambda value: value["max_similarity"],
        reverse=True,
    )
    for entry in relevant_docs:
        entry["max_similarity"] = round(float(entry["max_similarity"]), 4)
        entry["chunks"] = sorted(
            entry["chunks"],
            key=lambda chunk: float(chunk["similarity"]),
            reverse=True,
        )
    return queries, relevant_docs


def _estimate_min_llm_calls(*, add_relevant_docs: bool, has_tools: bool) -> int:
    calls = 1
    if add_relevant_docs:
        calls += 1
    if has_tools:
        calls += 2
    return calls


def _estimate_tool_roundtrip_messages(
    *,
    provider: str,
    messages: list[dict[str, Any]],
    tools_payload: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not tools_payload:
        return list(messages)

    tool_name = ""
    first_tool = tools_payload[0]
    if provider == "claude":
        tool_name = str(first_tool.get("name", "")).strip()
    else:
        tool_name = str(first_tool.get("function", {}).get("name", "")).strip()
    if not tool_name:
        return list(messages)

    follow_up_messages = list(messages)
    append_tool_messages(
        provider=provider,
        messages=follow_up_messages,
        assistant_text="",
        tool_calls=[{"id": "estimate_tool_call", "name": tool_name, "arguments": {}}],
        tool_results=[{"id": "estimate_tool_call", "output": {"ok": True, "estimated": True}}],
    )
    return follow_up_messages


async def estimate_agent_request_costs(
    *,
    node_data: dict[str, Any],
    workspace_root: Path,
    code_injected_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provider = str(node_data.get("provider", "") or "").strip()
    model = str(node_data.get("model", "") or "").strip()
    contract_paths = node_data.get("contractPaths", [])
    contracts = _read_contract_files(contract_paths) if contract_paths else {}
    memory_context = _read_memory_context_file(str(node_data.get("memoryFileToUse", "") or "").strip(), required=False)

    tool_node_data = node_data.get("_tool_node_data")
    selected_tool_name = ""
    if isinstance(tool_node_data, dict):
        selected_tool_name = str(tool_node_data.get("mcp", "")).strip()

    runtime_tools_payload, _ = build_provider_tool_schema(
        provider=provider,
        selected_tool_name=selected_tool_name,
        conf_path=workspace_root / "conf.yaml",
    )
    tokenizer_tools_payload, _ = build_provider_tool_schema(
        provider="claude",
        selected_tool_name=selected_tool_name,
        conf_path=workspace_root / "conf.yaml",
    )
    has_tools = bool(runtime_tools_payload or tokenizer_tools_payload)
    add_relevant_docs = bool(node_data.get("addRelevantDocs"))
    min_calls = _estimate_min_llm_calls(add_relevant_docs=add_relevant_docs, has_tools=has_tools)

    result: dict[str, Any] = {
        "min_calls": min_calls,
        "tokens_approx_total_min": None,
        "tokens_main_input": None,
        "tokens_docs_seed_input": None,
        "tokens_tool_followup_input": None,
        "has_tools": has_tools,
    }
    if not provider or not model:
        return result

    system_prompt = _build_system_prompt(node_data, workspace_root)
    main_messages = _build_messages(system_prompt, contracts, code_injected_data, memory_context=memory_context)
    main_tokens = await count_claude_input_tokens(model, main_messages, tokenizer_tools_payload if has_tools else None)
    result["tokens_main_input"] = main_tokens

    docs_seed_tokens = 0
    if add_relevant_docs:
        docs_prompt = _build_relevant_docs_seed_prompt(contracts, node_data, code_injected_data)
        if docs_prompt:
            docs_seed_tokens = await count_claude_input_tokens(
                model,
                [{"role": "user", "content": docs_prompt}],
            )
            result["tokens_docs_seed_input"] = docs_seed_tokens

    tool_followup_tokens = 0
    if has_tools:
        follow_up_messages = _estimate_tool_roundtrip_messages(
            provider="claude",
            messages=main_messages,
            tools_payload=tokenizer_tools_payload,
        )
        tool_followup_tokens = await count_claude_input_tokens(model, follow_up_messages, tokenizer_tools_payload)
        result["tokens_tool_followup_input"] = tool_followup_tokens

    result["tokens_approx_total_min"] = main_tokens + docs_seed_tokens + tool_followup_tokens
    return result


async def _execute_tool_call(
    tool_request: ToolCallRequest,
    endpoint_index: dict[str, dict[str, Any]],
    *,
    on_event: Any = None,
    block_id: str = "",
) -> ToolCallResult:
    endpoint_ref = endpoint_index.get(tool_request.name)
    if not endpoint_ref:
        if on_event:
            await on_event({
                "type": "agent_mcp_status",
                "block_id": block_id,
                "tool_name": tool_request.name,
                "stage": "error",
                "message": "tool_not_found",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        return ToolCallResult(
            tool_name=tool_request.name,
            input_args=tool_request.arguments,
            output={"ok": False, "error": "tool_not_found"},
        )

    if on_event:
        await on_event({
            "type": "agent_mcp_status",
            "block_id": block_id,
            "tool_name": tool_request.name,
            "stage": "request_sent",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    try:
        output = await execute_external_tool_call(
            endpoint_ref=endpoint_ref,
            arguments=tool_request.arguments,
        )
    except Exception as exc:
        if on_event:
            await on_event({
                "type": "agent_mcp_status",
                "block_id": block_id,
                "tool_name": tool_request.name,
                "stage": "error",
                "message": str(exc),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        raise

    stage = "data_received"
    message = ""
    if isinstance(output, dict) and output.get("ok") is False:
        stage = "error"
        message = str(output.get("error") or "mcp_call_failed")
    if on_event:
        await on_event({
            "type": "agent_mcp_status",
            "block_id": block_id,
            "tool_name": tool_request.name,
            "stage": stage,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    return ToolCallResult(
        tool_name=tool_request.name,
        input_args=tool_request.arguments,
        output=output,
    )


def _parse_agent_output(content: str) -> AgentOutput:
    if not content.strip():
        return AgentOutput()
    try:
        parsed = parse_model_json(content)
        return AgentOutput.model_validate(parsed)
    except Exception:
        return AgentOutput(summary=content, raw_data={"raw_text": content})


async def run_agent(
    node_id: str,
    node_data: dict[str, Any],
    workspace_root: Path,
    code_injected_data: dict[str, Any] | None = None,
    on_event: Any = None,
) -> tuple[BlockStatus, AccumulatorEntry | None, ErrorInfo | None]:
    """
    Execute an Agent block.

    Returns (status, accumulator_entry_or_None, error_or_None).
    The caller is responsible for appending the entry to the accumulator.
    """
    provider = node_data.get("provider", "")
    model = node_data.get("model", "")
    if not provider or not model:
        return (
            BlockStatus.error,
            None,
            ErrorInfo(
                block_id=node_id,
                block_type="agent",
                error_type="config_error",
                message="Agent block has no provider or model configured.",
            ),
        )

    try:
        if on_event:
            await on_event({
                "type": "agent_run_start",
                "block_id": node_id,
                "agent_type": node_data.get("agentType", "audit"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        system_prompt = _build_system_prompt(node_data, workspace_root)

        contract_paths = node_data.get("contractPaths", [])
        contracts = _read_contract_files(contract_paths) if contract_paths else {}
        memory_context = _read_memory_context_file(node_data.get("memoryFileToUse", ""), required=True)
        relevant_queries: list[str] = []
        relevant_docs: list[dict[str, Any]] = []

        if bool(node_data.get("addRelevantDocs")):
            try:
                relevant_queries, relevant_docs = await _build_relevant_docs_context(
                    provider=provider,
                    model=model,
                    contracts=contracts,
                    node_data=node_data,
                    code_injected_data=code_injected_data,
                    workspace_root=workspace_root,
                    on_event=on_event,
                    block_id=node_id,
                )
            except Exception:
                relevant_queries, relevant_docs = [], []

        messages = _build_messages(
            system_prompt,
            contracts,
            code_injected_data,
            memory_context=memory_context,
            relevant_docs=relevant_docs,
            relevant_queries=relevant_queries,
        )
        tool_call_history: list[ToolCallResult] = []
        selected_tool_name = ""
        tool_node_data = node_data.get("_tool_node_data")
        if isinstance(tool_node_data, dict):
            selected_tool_name = str(tool_node_data.get("mcp", "")).strip()

        conf_path = workspace_root / "conf.yaml"
        tools_payload, endpoint_index = build_provider_tool_schema(
            provider=provider,
            selected_tool_name=selected_tool_name,
            conf_path=conf_path,
        )

        final_output: AgentOutput | None = None

        for iteration in range(MAX_TOOL_CALL_ITERATIONS):
            if on_event:
                await on_event({
                    "type": "agent_llm_call",
                    "block_id": node_id,
                    "iteration": iteration,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

            raw_response = await send_to_model(
                provider=provider,
                model=model,
                messages=messages,
                json_mode=True,
                tools=tools_payload if tools_payload else None,
            )

            content = raw_response.get("content", "")
            normalized_calls = normalize_model_tool_calls(raw_response.get("tool_calls"))

            if normalized_calls:
                tool_results_payload: list[dict[str, Any]] = []
                for call in normalized_calls:
                    tool_request = ToolCallRequest(name=call["name"], arguments=call["arguments"])
                    if on_event:
                        await on_event({
                            "type": "agent_mcp_call",
                            "block_id": node_id,
                            "tool_name": tool_request.name,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                    tool_result = await _execute_tool_call(
                        tool_request,
                        endpoint_index,
                        on_event=on_event,
                        block_id=node_id,
                    )
                    tool_call_history.append(tool_result)
                    tool_results_payload.append({"id": call["id"], "output": tool_result.output})

                    if on_event:
                        await on_event({
                            "type": "agent_tool_call",
                            "block_id": node_id,
                            "tool_name": tool_request.name,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })

                append_tool_messages(
                    provider=provider,
                    messages=messages,
                    assistant_text=content,
                    tool_calls=normalized_calls,
                    tool_results=tool_results_payload,
                )
                continue

            agent_output = _parse_agent_output(content)

            if agent_output.tool_call and agent_output.tool_call_flag:
                if on_event:
                    await on_event({
                        "type": "agent_mcp_call",
                        "block_id": node_id,
                        "tool_name": agent_output.tool_call.name,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                tool_result = await _execute_tool_call(
                    agent_output.tool_call,
                    endpoint_index,
                    on_event=on_event,
                    block_id=node_id,
                )
                tool_call_history.append(tool_result)

                if on_event:
                    await on_event({
                        "type": "agent_tool_call",
                        "block_id": node_id,
                        "tool_name": agent_output.tool_call.name,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })

                legacy_call_id = f"legacy_{iteration}"
                append_tool_messages(
                    provider=provider,
                    messages=messages,
                    assistant_text=content,
                    tool_calls=[
                        {
                            "id": legacy_call_id,
                            "name": agent_output.tool_call.name,
                            "arguments": agent_output.tool_call.arguments,
                        }
                    ],
                    tool_results=[{"id": legacy_call_id, "output": tool_result.output}],
                )
                continue

            final_output = agent_output
            if tool_call_history:
                final_output.raw_data["_tool_call_history"] = [tc.model_dump() for tc in tool_call_history]
            if relevant_queries:
                final_output.raw_data["docs_queries"] = relevant_queries
            if relevant_docs:
                final_output.raw_data["relevant_docs"] = relevant_docs
            break
        else:
            return (
                BlockStatus.error,
                None,
                ErrorInfo(
                    block_id=node_id,
                    block_type="agent",
                    error_type="tool_call_loop_limit",
                    message=f"Agent exceeded {MAX_TOOL_CALL_ITERATIONS} tool-call iterations.",
                ),
            )

        entry = AccumulatorEntry(
            block_id=node_id,
            block_label=node_data.get("label", node_data.get("agentType", "")),
            output=final_output,
            tool_call_history=tool_call_history,
        )
        return BlockStatus.success, entry, None

    except Exception as exc:
        return (
            BlockStatus.error,
            None,
            ErrorInfo(
                block_id=node_id,
                block_type="agent",
                error_type="runtime_error",
                message=str(exc),
                traceback=traceback.format_exc(),
            ),
        )
