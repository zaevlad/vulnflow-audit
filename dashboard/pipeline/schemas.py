from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class BlockType(str, Enum):
    agent = "agent"
    patterns = "patterns"
    memory = "memory"
    code = "code"


class BlockStatus(str, Enum):
    pending = "pending"
    running = "running"
    success = "success"
    error = "error"
    skipped = "skipped"


class BranchStatus(str, Enum):
    running = "running"
    success = "success"
    error = "error"


class RunStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    stopped = "stopped"


# ---------------------------------------------------------------------------
# Agent response fields
# ---------------------------------------------------------------------------

class Vulnerability(BaseModel):
    title: str = ""
    description: str = ""
    recommendation: str = ""


class Idea(BaseModel):
    title: str = ""
    description: str = ""
    confidence: float = 0.0


class ToolCallRequest(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolCallResult(BaseModel):
    tool_name: str
    input_args: dict[str, Any] = Field(default_factory=dict)
    output: Any = None
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AgentOutput(BaseModel):
    """Structured JSON that every agent must return."""
    vulnerabilities: list[Vulnerability] = Field(default_factory=list)
    ideas: list[Idea] = Field(default_factory=list)
    tool_call: ToolCallRequest | None = None
    tool_call_flag: bool = False
    raw_data: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Accumulator entry — one per finished agent
# ---------------------------------------------------------------------------

class AccumulatorEntry(BaseModel):
    block_id: str
    block_label: str = ""
    output: AgentOutput
    tool_call_history: list[ToolCallResult] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Error information
# ---------------------------------------------------------------------------

class ErrorInfo(BaseModel):
    block_id: str
    block_type: str
    error_type: str = "unknown"
    message: str = ""
    input_snapshot: dict[str, Any] = Field(default_factory=dict)
    traceback: str | None = None


# ---------------------------------------------------------------------------
# Branch — isolated pipeline path after a fork
# ---------------------------------------------------------------------------

class BranchResult(BaseModel):
    branch_id: str
    status: BranchStatus = BranchStatus.running
    final_output: AgentOutput | None = None
    error: ErrorInfo | None = None


# ---------------------------------------------------------------------------
# Block execution record — stored in run log
# ---------------------------------------------------------------------------

class BlockRecord(BaseModel):
    block_id: str
    block_type: BlockType
    block_label: str = ""
    branch_id: str = "main"
    status: BlockStatus = BlockStatus.pending
    started_at: str | None = None
    finished_at: str | None = None
    output: AgentOutput | None = None
    error: ErrorInfo | None = None


# ---------------------------------------------------------------------------
# Global context — lives for the entire pipeline run
# ---------------------------------------------------------------------------

class GlobalContext(BaseModel):
    run_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    project_path: str = ""
    excluded_paths: list[str] = Field(default_factory=list)
    audit_mode: str = "contract"
    branches: dict[str, BranchResult] = Field(default_factory=dict)
    block_log: list[BlockRecord] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Pipeline run — top-level state visible to the API
# ---------------------------------------------------------------------------

class PipelineRun(BaseModel):
    run_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    pipeline_name: str = ""
    status: RunStatus = RunStatus.pending
    started_at: str | None = None
    finished_at: str | None = None
    global_context: GlobalContext = Field(default_factory=GlobalContext)
    events: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# JSON envelope sent to the model
# ---------------------------------------------------------------------------

class AgentRequestEnvelope(BaseModel):
    """Wrapper that the system builds around the user's skill prompt."""
    system_prompt: str = ""
    contracts: dict[str, str] = Field(default_factory=dict)
    code_injected_data: dict[str, Any] | None = None


class MemoryRequestEnvelope(BaseModel):
    """Wrapper for the memory model call."""
    memory_prompt: str = ""
    accumulated_contexts: list[dict[str, Any]] = Field(default_factory=list)
