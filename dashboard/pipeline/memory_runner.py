from __future__ import annotations

import json
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from dashboard.docs_rag import index_memory_content
from dashboard.pipeline.agent_runner import _read_contract_files
from dashboard.pipeline.llm_router import send_to_model
from dashboard.pipeline.schemas import (
    AccumulatorEntry,
    BlockStatus,
    ErrorInfo,
)


def _read_memory_prompt(prompt_path: str) -> str:
    fp = Path(prompt_path)
    if fp.is_file():
        return fp.read_text(encoding="utf-8", errors="replace")
    return ""


def _serialize_accumulator(entries: list[AccumulatorEntry]) -> list[dict[str, Any]]:
    return [
        {
            "block_id": e.block_id,
            "block_label": e.block_label,
            "output": e.output.model_dump(),
            "tool_call_history": [t.model_dump() for t in e.tool_call_history],
        }
        for e in entries
    ]


async def _index_memory_with_retry(
    *,
    workspace_root: Path,
    memory_file: Path,
    content: str,
    block_id: str,
    event_type: str = "memory_status",
    extra_event_fields: dict[str, Any] | None = None,
    on_event: Any = None,
) -> None:
    if not content.strip():
        return

    extra = dict(extra_event_fields or {})
    if on_event:
        await on_event({
            "type": event_type,
            "block_id": block_id,
            "memory_file": str(memory_file),
            "stage": "vector_write_started",
            **extra,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    last_error: Exception | None = None
    for attempt in range(1, 3):
        try:
            result = index_memory_content(
                workspace_root=workspace_root,
                memory_file=memory_file,
                content=content,
            )
            if on_event:
                await on_event({
                    "type": event_type,
                    "block_id": block_id,
                    "memory_file": str(memory_file),
                    "stage": "vector_written",
                    "chunk_count": result.get("chunk_count", 0),
                    "attempt": attempt,
                    **extra,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                await on_event({
                    "type": "memory_indexed",
                    "block_id": block_id,
                    "memory_file": str(memory_file),
                    "chunk_count": result.get("chunk_count", 0),
                    "attempt": attempt,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            return
        except Exception as exc:
            last_error = exc

    if on_event and last_error is not None:
        await on_event({
            "type": event_type,
            "block_id": block_id,
            "memory_file": str(memory_file),
            "stage": "error",
            "step": "vector_write",
            "message": str(last_error),
            "attempts": 2,
            **extra,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        await on_event({
            "type": "memory_index_error",
            "block_id": block_id,
            "memory_file": str(memory_file),
            "message": str(last_error),
            "attempts": 2,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })


def _read_optional_text_file(path: str) -> str:
    fp = Path(path)
    if fp.is_file():
        return fp.read_text(encoding="utf-8", errors="replace")
    return ""


def _append_markdown_file(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
    separator = "\n\n---\n\n" if existing.strip() else ""
    target.write_text(existing + separator + content, encoding="utf-8")


def _load_patterns(pattern_file: str) -> list[dict[str, Any]]:
    fp = Path(pattern_file)
    if not fp.is_file():
        raise RuntimeError("Patterns block cannot read the selected pattern file.")
    loaded = yaml.safe_load(fp.read_text(encoding="utf-8", errors="replace"))
    if loaded is None:
        return []
    if not isinstance(loaded, list):
        raise RuntimeError("Pattern file must contain a top-level YAML list.")
    items: list[dict[str, Any]] = []
    for idx, item in enumerate(loaded, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(f"Pattern entry #{idx} must be a mapping.")
        items.append(item)
    return items


def _chunk_patterns(items: list[dict[str, Any]], size: int = 10) -> list[list[dict[str, Any]]]:
    return [items[index:index + size] for index in range(0, len(items), size)]


async def run_memory(
    node_id: str,
    node_data: dict[str, Any],
    workspace_root: Path,
    accumulator: list[AccumulatorEntry],
    on_event: Any = None,
) -> tuple[BlockStatus, ErrorInfo | None]:
    """
    Execute a Memory block.

    Reads the full accumulator, sends it together with the memory_prompt
    to the model, writes the response into the user-chosen memory file,
    then signals the caller to clear the accumulator.

    Returns (status, error_or_None).
    """
    memory_file = node_data.get("memoryFile", "")
    memory_prompt_path = node_data.get("memoryPrompt", "")
    provider = node_data.get("provider", "")
    model = node_data.get("model", "")

    if not memory_file:
        return (
            BlockStatus.error,
            ErrorInfo(
                block_id=node_id,
                block_type="memory",
                error_type="config_error",
                message="Memory block has no memory file selected.",
            ),
        )

    if not provider or not model:
        return (
            BlockStatus.error,
            ErrorInfo(
                block_id=node_id,
                block_type="memory",
                error_type="config_error",
                message="Memory block has no provider or model configured.",
            ),
        )

    if not accumulator:
        if on_event:
            await on_event({
                "type": "memory_skip",
                "block_id": node_id,
                "reason": "accumulator_empty",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        return BlockStatus.skipped, None

    try:
        memory_prompt = _read_memory_prompt(memory_prompt_path)
        if not memory_prompt:
            memory_prompt = (
                "You are a memory writer for a smart contract audit pipeline. "
                "Summarize the following agent outputs and write structured notes. "
                "Respond in JSON with a 'memory_content' string field."
            )

        serialized = _serialize_accumulator(accumulator)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": memory_prompt},
            {"role": "user", "content": json.dumps(
                {"accumulated_contexts": serialized},
                ensure_ascii=False,
            )},
        ]

        if on_event:
            await on_event({
                "type": "memory_status",
                "block_id": node_id,
                "stage": "model_request_sent",
                "entries_count": len(accumulator),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            await on_event({
                "type": "memory_llm_call",
                "block_id": node_id,
                "entries_count": len(accumulator),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        raw_response = await send_to_model(
            provider=provider,
            model=model,
            messages=messages,
            json_mode=True,
        )

        content = raw_response.get("content", "")

        if on_event:
            await on_event({
                "type": "memory_status",
                "block_id": node_id,
                "stage": "model_data_received",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        fp = Path(memory_file)
        fp.parent.mkdir(parents=True, exist_ok=True)

        existing = ""
        if fp.is_file():
            existing = fp.read_text(encoding="utf-8", errors="replace")

        separator = "\n\n---\n\n" if existing.strip() else ""
        if on_event:
            await on_event({
                "type": "memory_status",
                "block_id": node_id,
                "memory_file": str(fp),
                "stage": "file_write_started",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        fp.write_text(
            existing + separator + content,
            encoding="utf-8",
        )

        if on_event:
            await on_event({
                "type": "memory_status",
                "block_id": node_id,
                "memory_file": str(fp),
                "stage": "file_written",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            await on_event({
                "type": "memory_written",
                "block_id": node_id,
                "memory_file": str(fp),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        await _index_memory_with_retry(
            workspace_root=workspace_root,
            memory_file=fp,
            content=content,
            block_id=node_id,
            on_event=on_event,
        )

        return BlockStatus.success, None

    except Exception as exc:
        if on_event:
            await on_event({
                "type": "memory_status",
                "block_id": node_id,
                "memory_file": str(memory_file),
                "stage": "error",
                "message": str(exc),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        return (
            BlockStatus.error,
            ErrorInfo(
                block_id=node_id,
                block_type="memory",
                error_type="runtime_error",
                message=str(exc),
                traceback=traceback.format_exc(),
            ),
        )


async def run_patterns(
    node_id: str,
    node_data: dict[str, Any],
    workspace_root: Path,
    on_event: Any = None,
) -> tuple[BlockStatus, ErrorInfo | None]:
    pattern_file = str(node_data.get("patternFile", "") or "").strip()
    result_file = str(node_data.get("resultFile", "") or "").strip()
    prompt_file = str(node_data.get("promptFile", "") or "").strip()
    provider = str(node_data.get("provider", "") or "").strip()
    model = str(node_data.get("model", "") or "").strip()
    scope_mode = str(node_data.get("scopeMode", "") or "").strip() or ("cluster" if node_data.get("cluster") else "contract")
    cluster = str(node_data.get("cluster", "") or "").strip()
    contract_paths = [str(item) for item in (node_data.get("contractPaths") or []) if str(item).strip()]
    cluster_files = [str(item) for item in (node_data.get("clusterFiles") or []) if str(item).strip()]

    if not pattern_file:
        return (
            BlockStatus.error,
            ErrorInfo(
                block_id=node_id,
                block_type="patterns",
                error_type="config_error",
                message="Patterns block has no pattern file selected.",
            ),
        )
    if not result_file:
        return (
            BlockStatus.error,
            ErrorInfo(
                block_id=node_id,
                block_type="patterns",
                error_type="config_error",
                message="Patterns block has no result file selected.",
            ),
        )
    if not prompt_file:
        return (
            BlockStatus.error,
            ErrorInfo(
                block_id=node_id,
                block_type="patterns",
                error_type="config_error",
                message="Patterns block has no prompt file selected.",
            ),
        )
    if not provider or not model:
        return (
            BlockStatus.error,
            ErrorInfo(
                block_id=node_id,
                block_type="patterns",
                error_type="config_error",
                message="Patterns block has no provider or model configured.",
            ),
        )
    if scope_mode == "cluster" and not cluster_files:
        return (
            BlockStatus.error,
            ErrorInfo(
                block_id=node_id,
                block_type="patterns",
                error_type="config_error",
                message="Patterns block cluster selection has no files attached. Regenerate clusters and re-save the pipeline.",
            ),
        )
    if scope_mode == "contract" and not contract_paths:
        return (
            BlockStatus.error,
            ErrorInfo(
                block_id=node_id,
                block_type="patterns",
                error_type="config_error",
                message="Patterns block has no contracts selected.",
            ),
        )

    try:
        prompt_text = _read_optional_text_file(prompt_file)
        if not prompt_text:
            prompt_text = (
                "You are a smart contract security auditor. Review the provided contracts against the supplied patterns. "
                "Write concise markdown notes for each pattern batch."
            )

        patterns = _load_patterns(pattern_file)
        if not patterns:
            if on_event:
                await on_event({
                    "type": "patterns_status",
                    "block_id": node_id,
                    "stage": "completed",
                    "batch_total": 0,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            return BlockStatus.skipped, None

        batches = _chunk_patterns(patterns, size=10)
        selected_paths = cluster_files if scope_mode == "cluster" else contract_paths
        contracts = _read_contract_files(selected_paths)
        if not contracts:
            raise RuntimeError("Patterns block could not read the selected Solidity files.")

        result_path = Path(result_file)
        for batch_index, batch in enumerate(batches, start=1):
            batch_fields = {"batch_index": batch_index, "batch_total": len(batches)}
            if on_event:
                await on_event({
                    "type": "patterns_status",
                    "block_id": node_id,
                    "stage": "batch_started",
                    **batch_fields,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                await on_event({
                    "type": "patterns_status",
                    "block_id": node_id,
                    "stage": "model_request_sent",
                    **batch_fields,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

            messages: list[dict[str, Any]] = [
                {"role": "system", "content": prompt_text},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "pattern_batch": batch,
                            "batch_index": batch_index,
                            "batch_total": len(batches),
                            "scope": {
                                "mode": scope_mode,
                                "cluster": cluster if scope_mode == "cluster" else "",
                                "contract_paths": selected_paths,
                            },
                            "contracts": contracts,
                        },
                        ensure_ascii=False,
                    ),
                },
            ]

            raw_response = await send_to_model(
                provider=provider,
                model=model,
                messages=messages,
                json_mode=False,
            )
            content = str(raw_response.get("content", "") or "").strip()

            if on_event:
                await on_event({
                    "type": "patterns_status",
                    "block_id": node_id,
                    "stage": "model_data_received",
                    **batch_fields,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                await on_event({
                    "type": "patterns_status",
                    "block_id": node_id,
                    "memory_file": str(result_path),
                    "stage": "file_write_started",
                    **batch_fields,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

            batch_heading = (
                f"## Pattern Batch {batch_index}/{len(batches)}\n\n"
                f"- Pattern file: `{Path(pattern_file).name}`\n"
                f"- Prompt file: `{Path(prompt_file).name}`\n"
                f"- Scope: `{scope_mode}`"
                + (f" ({cluster})" if scope_mode == "cluster" and cluster else "")
                + "\n\n"
            )
            _append_markdown_file(result_path, batch_heading + content)

            if on_event:
                await on_event({
                    "type": "patterns_status",
                    "block_id": node_id,
                    "memory_file": str(result_path),
                    "stage": "file_written",
                    **batch_fields,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

            await _index_memory_with_retry(
                workspace_root=workspace_root,
                memory_file=result_path,
                content=batch_heading + content,
                block_id=node_id,
                event_type="patterns_status",
                extra_event_fields=batch_fields,
                on_event=on_event,
            )

        if on_event:
            await on_event({
                "type": "patterns_status",
                "block_id": node_id,
                "stage": "completed",
                "batch_total": len(batches),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        return BlockStatus.success, None

    except Exception as exc:
        if on_event:
            await on_event({
                "type": "patterns_status",
                "block_id": node_id,
                "memory_file": result_file,
                "stage": "error",
                "message": str(exc),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        return (
            BlockStatus.error,
            ErrorInfo(
                block_id=node_id,
                block_type="patterns",
                error_type="runtime_error",
                message=str(exc),
                traceback=traceback.format_exc(),
            ),
        )
