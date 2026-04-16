from __future__ import annotations

import traceback
from datetime import datetime, timezone
from typing import Any

from dashboard.pipeline.schemas import (
    AgentOutput,
    BlockStatus,
    ErrorInfo,
)


_SAFE_BUILTINS = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "filter": filter,
    "float": float,
    "frozenset": frozenset,
    "int": int,
    "isinstance": isinstance,
    "len": len,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "print": print,
    "range": range,
    "reversed": reversed,
    "round": round,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "type": type,
    "zip": zip,
    "True": True,
    "False": False,
    "None": None,
}


async def run_code(
    node_id: str,
    node_data: dict[str, Any],
    previous_output: AgentOutput | None,
    on_event: Any = None,
) -> tuple[BlockStatus, dict[str, Any] | None, ErrorInfo | None]:
    """
    Execute a Code block in a restricted sandbox.

    The user's code has access to:
      - ``data``   : dict — JSON output of the previous block (or empty dict)
      - ``result`` : dict — the user writes output here

    Returns (status, result_dict_or_None, error_or_None).
    If result contains an ``inject`` key, the engine will pass that value
    as ``code_injected_data`` to the next Agent block.
    """
    code_text = (node_data.get("code") or "").strip()
    if not code_text:
        return (
            BlockStatus.error,
            None,
            ErrorInfo(
                block_id=node_id,
                block_type="code",
                error_type="config_error",
                message="Code block is empty.",
            ),
        )

    data: dict[str, Any] = previous_output.model_dump() if previous_output else {}
    result: dict[str, Any] = {}

    sandbox_globals: dict[str, Any] = {"__builtins__": _SAFE_BUILTINS}
    sandbox_locals: dict[str, Any] = {"data": data, "result": result}

    try:
        if on_event:
            await on_event({
                "type": "code_exec_start",
                "block_id": node_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        exec(compile(code_text, f"<code-block-{node_id}>", "exec"), sandbox_globals, sandbox_locals)  # noqa: S102

        result = sandbox_locals.get("result", result)
        if not isinstance(result, dict):
            result = {"value": result}

        if on_event:
            await on_event({
                "type": "code_status",
                "block_id": node_id,
                "stage": "success",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            await on_event({
                "type": "code_exec_done",
                "block_id": node_id,
                "result_keys": list(result.keys()),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        return BlockStatus.success, result, None

    except Exception as exc:
        if on_event:
            await on_event({
                "type": "code_status",
                "block_id": node_id,
                "stage": "error",
                "message": str(exc),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        return (
            BlockStatus.error,
            None,
            ErrorInfo(
                block_id=node_id,
                block_type="code",
                error_type="code_execution_error",
                message=str(exc),
                input_snapshot={"code": code_text[:500]},
                traceback=traceback.format_exc(),
            ),
        )
