from __future__ import annotations

import copy
from typing import Any

from dashboard.pipeline.schemas import (
    AccumulatorEntry,
    AgentOutput,
    BranchResult,
    BranchStatus,
    ErrorInfo,
    GlobalContext,
)


class PipelineContext:
    """
    Manages the three-level context model for a single pipeline run.

    * **global_ctx**  — lives for the entire run; branches dict, project meta, block log
    * **accumulator** — ordered buffer of agent outputs between Memory blocks;
      Agent adds, Memory consumes & clears, Code passes through unchanged
    * **message history** — internal to each agent (not tracked here)
    """

    def __init__(self, global_ctx: GlobalContext) -> None:
        self.global_ctx = global_ctx
        self._accumulator: list[AccumulatorEntry] = []
        self._last_block_output: AgentOutput | None = None
        self._code_injected_data: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Accumulator operations
    # ------------------------------------------------------------------

    @property
    def accumulator(self) -> list[AccumulatorEntry]:
        return self._accumulator

    def push_to_accumulator(self, entry: AccumulatorEntry) -> None:
        self._accumulator.append(entry)
        self._last_block_output = entry.output

    def drain_accumulator(self) -> list[AccumulatorEntry]:
        """Return current accumulator contents and clear it."""
        entries = list(self._accumulator)
        self._accumulator.clear()
        return entries

    def clear_accumulator(self) -> None:
        self._accumulator.clear()

    # ------------------------------------------------------------------
    # Last block output (for Code blocks)
    # ------------------------------------------------------------------

    @property
    def last_block_output(self) -> AgentOutput | None:
        return self._last_block_output

    @last_block_output.setter
    def last_block_output(self, value: AgentOutput | None) -> None:
        self._last_block_output = value

    # ------------------------------------------------------------------
    # Code-injected data (for the next Agent)
    # ------------------------------------------------------------------

    @property
    def code_injected_data(self) -> dict[str, Any] | None:
        return self._code_injected_data

    def set_code_injection(self, data: dict[str, Any] | None) -> None:
        self._code_injected_data = data

    def consume_code_injection(self) -> dict[str, Any] | None:
        """Return and clear the code-injected data (one-time use)."""
        data = self._code_injected_data
        self._code_injected_data = None
        return data

    # ------------------------------------------------------------------
    # Branch operations
    # ------------------------------------------------------------------

    def register_branch(self, branch_id: str) -> None:
        self.global_ctx.branches[branch_id] = BranchResult(
            branch_id=branch_id,
            status=BranchStatus.running,
        )

    def finish_branch(
        self,
        branch_id: str,
        *,
        output: AgentOutput | None = None,
        error: ErrorInfo | None = None,
    ) -> None:
        if error:
            self.global_ctx.branches[branch_id] = BranchResult(
                branch_id=branch_id,
                status=BranchStatus.error,
                error=error,
            )
        else:
            self.global_ctx.branches[branch_id] = BranchResult(
                branch_id=branch_id,
                status=BranchStatus.success,
                final_output=output,
            )

    # ------------------------------------------------------------------
    # Fork / Merge
    # ------------------------------------------------------------------

    def fork(self) -> PipelineContext:
        """
        Create an independent copy for a new branch.

        The new context gets its own accumulator (deep-copied) and its own
        code-injection slot, but shares the same global_ctx reference so
        branch results are visible globally.
        """
        child = PipelineContext(self.global_ctx)
        child._accumulator = copy.deepcopy(self._accumulator)
        child._last_block_output = copy.deepcopy(self._last_block_output)
        child._code_injected_data = copy.deepcopy(self._code_injected_data)
        return child

    @staticmethod
    def merge_results(
        global_ctx: GlobalContext,
        branch_ids: list[str],
    ) -> dict[str, BranchResult]:
        """
        Collect results from all listed branches.

        Every branch always terminates — either success or error.
        This method simply reads from the global context; the engine
        must await all branches before calling it.
        """
        return {
            bid: global_ctx.branches.get(
                bid,
                BranchResult(branch_id=bid, status=BranchStatus.error,
                             error=ErrorInfo(block_id="", block_type="branch",
                                             error_type="missing_branch",
                                             message=f"Branch {bid} not found in global context.")),
            )
            for bid in branch_ids
        }

    @staticmethod
    def merged_output_as_agent_output(
        merged: dict[str, BranchResult],
    ) -> AgentOutput:
        """
        Pack merged branch results into a single AgentOutput so that
        the next block can read them uniformly via ``last_block_output``.
        """
        raw: dict[str, Any] = {}
        all_vulns = []
        all_ideas = []
        summaries = []

        for bid, br in merged.items():
            entry: dict[str, Any] = {"status": br.status.value}
            if br.final_output:
                entry["output"] = br.final_output.model_dump()
                all_vulns.extend(br.final_output.vulnerabilities)
                all_ideas.extend(br.final_output.ideas)
                if br.final_output.summary:
                    summaries.append(br.final_output.summary)
            if br.error:
                entry["error"] = br.error.model_dump()
            raw[bid] = entry

        return AgentOutput(
            vulnerabilities=all_vulns,
            ideas=all_ideas,
            summary=" | ".join(summaries) if summaries else "",
            raw_data={"merged_branches": raw},
        )
