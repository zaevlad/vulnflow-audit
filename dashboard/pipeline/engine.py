from __future__ import annotations

import asyncio
import traceback
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine

from dashboard.pipeline.agent_runner import run_agent
from dashboard.pipeline.code_runner import run_code
from dashboard.pipeline.context import PipelineContext
from dashboard.pipeline.memory_runner import run_memory, run_patterns
from dashboard.pipeline.schemas import (
    BlockRecord,
    BlockStatus,
    BlockType,
    BranchStatus,
    ErrorInfo,
    GlobalContext,
    PipelineRun,
    RunStatus,
)

EventCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


# ---------------------------------------------------------------------------
# Graph helpers
# ---------------------------------------------------------------------------

def _build_working_graph(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, list[str]],
    dict[str, list[str]],
    dict[str, dict[str, Any]],
]:
    """
    Build an adjacency representation from the pipeline JSON,
    excluding tool nodes (they are children of agents, not pipeline steps).

    Returns:
      node_map   — id → node dict
      successors — id → [successor ids]
      predecessors — id → [predecessor ids]
      tool_map   — agent_id → tool node data (for MCP path lookup)
    """
    tool_map: dict[str, dict[str, Any]] = {}
    node_map: dict[str, dict[str, Any]] = {}

    for n in nodes:
        if n.get("type") == "tool":
            parent = n.get("parentId") or (n.get("data") or {}).get("parentAgentId", "")
            if parent:
                tool_map[parent] = n.get("data", {})
            continue
        node_map[n["id"]] = n

    tool_edges: set[str] = set()
    for e in edges:
        if e.get("target") and e["target"] not in node_map:
            tool_edges.add(e.get("id", ""))
        if e.get("source") and e["source"] not in node_map:
            tool_edges.add(e.get("id", ""))

    successors: dict[str, list[str]] = defaultdict(list)
    predecessors: dict[str, list[str]] = defaultdict(list)

    for e in edges:
        if e.get("id", "") in tool_edges:
            continue
        src, tgt = e.get("source", ""), e.get("target", "")
        if src in node_map and tgt in node_map:
            successors[src].append(tgt)
            predecessors[tgt].append(src)

    return node_map, dict(successors), dict(predecessors), tool_map


def _topological_sort(
    node_ids: list[str],
    successors: dict[str, list[str]],
    predecessors: dict[str, list[str]],
) -> list[str]:
    in_degree: dict[str, int] = {nid: 0 for nid in node_ids}
    for nid in node_ids:
        for s in successors.get(nid, []):
            if s in in_degree:
                in_degree[s] += 1

    queue: deque[str] = deque(nid for nid, deg in in_degree.items() if deg == 0)
    order: list[str] = []

    while queue:
        nid = queue.popleft()
        order.append(nid)
        for s in successors.get(nid, []):
            if s not in in_degree:
                continue
            in_degree[s] -= 1
            if in_degree[s] == 0:
                queue.append(s)

    if len(order) != len(node_ids):
        raise ValueError(
            "Pipeline graph contains a cycle. "
            f"Sorted {len(order)} of {len(node_ids)} nodes."
        )
    return order


def _find_roots(
    node_ids: list[str],
    predecessors: dict[str, list[str]],
) -> list[str]:
    return [nid for nid in node_ids if not predecessors.get(nid)]


def _node_type(node: dict[str, Any]) -> BlockType:
    raw = node.get("type", "agent")
    if raw == "patterns":
        return BlockType.patterns
    if raw == "memory":
        return BlockType.memory
    if raw == "code":
        return BlockType.code
    return BlockType.agent


# ---------------------------------------------------------------------------
# Single-block executor
# ---------------------------------------------------------------------------

async def _execute_block(
    node_id: str,
    node: dict[str, Any],
    node_type: BlockType,
    ctx: PipelineContext,
    workspace_root: Path,
    tool_map: dict[str, dict[str, Any]],
    on_event: EventCallback | None,
) -> tuple[BlockStatus, ErrorInfo | None]:
    """
    Execute one block and update the pipeline context accordingly.

    * Agent — clean context; output appended to accumulator
    * Memory — reads & clears accumulator; writes to file
    * Code — reads previous output; may inject data for next agent
    """
    data: dict[str, Any] = dict(node.get("data") or {})

    if node_type == BlockType.agent:
        if tool_map.get(node_id):
            data["_tool_node_data"] = tool_map[node_id]

        code_injection = ctx.consume_code_injection()

        status, entry, error = await run_agent(
            node_id=node_id,
            node_data=data,
            workspace_root=workspace_root,
            code_injected_data=code_injection,
            on_event=on_event,
        )
        if entry:
            ctx.push_to_accumulator(entry)
        return status, error

    if node_type == BlockType.memory:
        entries = ctx.drain_accumulator()
        status, error = await run_memory(
            node_id=node_id,
            node_data=data,
            workspace_root=workspace_root,
            accumulator=entries,
            on_event=on_event,
        )
        ctx.last_block_output = None
        return status, error

    if node_type == BlockType.patterns:
        status, error = await run_patterns(
            node_id=node_id,
            node_data=data,
            workspace_root=workspace_root,
            on_event=on_event,
        )
        ctx.last_block_output = None
        return status, error

    if node_type == BlockType.code:
        status, result, error = await run_code(
            node_id=node_id,
            node_data=data,
            previous_output=ctx.last_block_output,
            on_event=on_event,
        )
        if result and "inject" in result:
            ctx.set_code_injection(result["inject"])
        return status, error

    return (
        BlockStatus.error,
        ErrorInfo(
            block_id=node_id,
            block_type=str(node_type),
            error_type="unknown_block_type",
            message=f"Unknown block type: {node_type}",
        ),
    )


# ---------------------------------------------------------------------------
# Branch executor (recursive chain walk)
# ---------------------------------------------------------------------------

async def _run_chain(
    start_node_id: str,
    branch_id: str,
    ctx: PipelineContext,
    node_map: dict[str, dict[str, Any]],
    successors: dict[str, list[str]],
    predecessors: dict[str, list[str]],
    workspace_root: Path,
    tool_map: dict[str, dict[str, Any]],
    run: PipelineRun,
    on_event: EventCallback | None,
    visited: set[str] | None = None,
    merge_events: dict[str, asyncio.Event] | None = None,
    merge_contexts: dict[str, list[PipelineContext]] | None = None,
) -> None:
    """Walk a chain of blocks starting from *start_node_id*."""
    if visited is None:
        visited = set()
    if merge_events is None:
        merge_events = {}
    if merge_contexts is None:
        merge_contexts = {}

    current_id: str | None = start_node_id

    while current_id and current_id not in visited:
        visited.add(current_id)
        node = node_map.get(current_id)
        if not node:
            break

        preds = predecessors.get(current_id, [])
        if len(preds) > 1:
            if current_id not in merge_events:
                merge_events[current_id] = asyncio.Event()
                merge_contexts[current_id] = []

            merge_contexts[current_id].append(ctx)

            if len(merge_contexts[current_id]) < len(preds):
                return

            merge_events[current_id].set()

            branch_results = PipelineContext.merge_results(
                ctx.global_ctx,
                [f"branch-{pid}-{current_id}" for pid in preds],
            )
            merged_output = PipelineContext.merged_output_as_agent_output(branch_results)
            ctx.last_block_output = merged_output

        ntype = _node_type(node)
        record = BlockRecord(
            block_id=current_id,
            block_type=ntype,
            block_label=(node.get("data") or {}).get("label", ""),
            branch_id=branch_id,
            status=BlockStatus.running,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        run.global_context.block_log.append(record)

        if on_event:
            await on_event({
                "type": "block_start",
                "block_id": current_id,
                "block_type": ntype.value,
                "branch_id": branch_id,
                "timestamp": record.started_at,
            })

        status, error = await _execute_block(
            node_id=current_id,
            node=node,
            node_type=ntype,
            ctx=ctx,
            workspace_root=workspace_root,
            tool_map=tool_map,
            on_event=on_event,
        )

        record.status = status
        record.finished_at = datetime.now(timezone.utc).isoformat()
        record.error = error

        if on_event:
            await on_event({
                "type": "block_end",
                "block_id": current_id,
                "block_type": ntype.value,
                "branch_id": branch_id,
                "status": status.value,
                "error": error.model_dump() if error else None,
                "timestamp": record.finished_at,
            })

        if status == BlockStatus.error:
            ctx.finish_branch(branch_id, error=error)
            return

        succs = successors.get(current_id, [])

        if len(succs) == 0:
            last_out = ctx.last_block_output
            ctx.finish_branch(branch_id, output=last_out)
            return

        if len(succs) == 1:
            if on_event:
                await on_event({
                    "type": "block_transition",
                    "from_block_id": current_id,
                    "to_block_id": succs[0],
                    "branch_id": branch_id,
                    "status": "next",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            current_id = succs[0]
            continue

        tasks: list[asyncio.Task[None]] = []
        for succ_id in succs:
            child_branch_id = f"branch-{current_id}-{succ_id}"
            child_ctx = ctx.fork()
            child_ctx.register_branch(child_branch_id)

            if on_event:
                await on_event({
                    "type": "branch_fork",
                    "parent_block": current_id,
                    "child_branch": child_branch_id,
                    "target_block": succ_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                await on_event({
                    "type": "block_transition",
                    "from_block_id": current_id,
                    "to_block_id": succ_id,
                    "branch_id": child_branch_id,
                    "status": "fork",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

            task = asyncio.create_task(
                _run_chain(
                    start_node_id=succ_id,
                    branch_id=child_branch_id,
                    ctx=child_ctx,
                    node_map=node_map,
                    successors=successors,
                    predecessors=predecessors,
                    workspace_root=workspace_root,
                    tool_map=tool_map,
                    run=run,
                    on_event=on_event,
                    visited=set(visited),
                    merge_events=merge_events,
                    merge_contexts=merge_contexts,
                )
            )
            tasks.append(task)

        await asyncio.gather(*tasks, return_exceptions=True)
        return


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def run_pipeline(
    pipeline_data: dict[str, Any],
    workspace_root: str | Path,
    on_event: EventCallback | None = None,
) -> PipelineRun:
    """
    Execute a full pipeline.

    *pipeline_data* is the saved pipeline JSON (with ``nodes``, ``edges``,
    ``projectPath``, ``excludedPaths``, etc.).

    Returns a ``PipelineRun`` with full execution log.
    """
    workspace_root = Path(workspace_root)
    nodes = pipeline_data.get("nodes", [])
    edges = pipeline_data.get("edges", [])

    run = PipelineRun(
        pipeline_name=pipeline_data.get("name", "unnamed"),
        status=RunStatus.running,
        started_at=datetime.now(timezone.utc).isoformat(),
        global_context=GlobalContext(
            project_path=pipeline_data.get("projectPath", ""),
            excluded_paths=pipeline_data.get("excludedPaths", []),
            audit_mode=pipeline_data.get("auditMode", "contract"),
        ),
    )
    run.global_context.run_id = run.run_id

    if on_event:
        await on_event({
            "type": "pipeline_start",
            "run_id": run.run_id,
            "pipeline_name": run.pipeline_name,
            "timestamp": run.started_at,
        })

    try:
        node_map, successors, predecessors, tool_map = _build_working_graph(nodes, edges)
        node_ids = list(node_map.keys())

        if not node_ids:
            run.status = RunStatus.completed
            run.finished_at = datetime.now(timezone.utc).isoformat()
            return run

        order = _topological_sort(node_ids, successors, predecessors)
        roots = _find_roots(order, predecessors)

        if not roots:
            raise ValueError("No root nodes found in pipeline graph.")

        merge_events: dict[str, asyncio.Event] = {}
        merge_contexts: dict[str, list[PipelineContext]] = {}

        if len(roots) == 1:
            ctx = PipelineContext(run.global_context)
            branch_id = "main"
            ctx.register_branch(branch_id)

            await _run_chain(
                start_node_id=roots[0],
                branch_id=branch_id,
                ctx=ctx,
                node_map=node_map,
                successors=successors,
                predecessors=predecessors,
                workspace_root=workspace_root,
                tool_map=tool_map,
                run=run,
                on_event=on_event,
                merge_events=merge_events,
                merge_contexts=merge_contexts,
            )
        else:
            tasks: list[asyncio.Task[None]] = []
            for root_id in roots:
                ctx = PipelineContext(run.global_context)
                branch_id = f"root-{root_id}"
                ctx.register_branch(branch_id)
                tasks.append(asyncio.create_task(
                    _run_chain(
                        start_node_id=root_id,
                        branch_id=branch_id,
                        ctx=ctx,
                        node_map=node_map,
                        successors=successors,
                        predecessors=predecessors,
                        workspace_root=workspace_root,
                        tool_map=tool_map,
                        run=run,
                        on_event=on_event,
                        merge_events=merge_events,
                        merge_contexts=merge_contexts,
                    )
                ))
            await asyncio.gather(*tasks, return_exceptions=True)

        any_error = any(
            br.status == BranchStatus.error
            for br in run.global_context.branches.values()
        )
        run.status = RunStatus.failed if any_error else RunStatus.completed

    except Exception as exc:
        run.status = RunStatus.failed
        if on_event:
            await on_event({
                "type": "pipeline_error",
                "run_id": run.run_id,
                "error": str(exc),
                "traceback": traceback.format_exc(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    run.finished_at = datetime.now(timezone.utc).isoformat()

    if on_event:
        await on_event({
            "type": "pipeline_end",
            "run_id": run.run_id,
            "status": run.status.value,
            "timestamp": run.finished_at,
        })

    return run
