---
name: "audit-context"
title: "VulnFlow Audit Context Skill"
description: "How to reason about Solidity contracts, tests, and the current vulnflow audit session — what to read, what to retrieve, and how to ground answers."
allowed-tools: ["read_workspace_file", "list_workspace_directory", "search_docs", "list_resources"]
---

# VulnFlow Audit Context Skill

This skill governs how the chat agent grounds its answers in the actual
audit project the user has opened in vulnflow. It is mandatory whenever
the user's question is about contracts, tests, patterns, memory, audit
docs, or the active pipeline.

## When to consult this skill

Consult before:

- explaining a Solidity contract that lives in the current `auditPath`
- summarizing or comparing test coverage
- proposing canvas nodes that bind to specific contract files
- recommending a skill / pattern / memory / external tool for a node
- recommending a fix or remediation grounded in the project's own code

Do **not** answer Solidity questions about local code purely from
training memory. Read the file first.

## Source of truth

The frontend sends a fresh `ui_context` on every chat send. Treat it
as authoritative:

- `ui_context.auditPath` is the root of the project under audit
- `ui_context.workspacePath` is the vulnflow repo root (skills, MCP, patterns, memory)
- `ui_context.excludedPaths` lists paths the user marked as out-of-scope —
  honor them; never propose nodes that bind to excluded paths
- `ui_context.catalogs` exposes the discoverable resources: `skills`,
  `lead_skills`, `mcp`, `audit_docs`, `patterns`, `memory`, `memory_promts`
- `ui_context.availableTools` lists external tools loaded from `conf.yaml`
  (HornetMCP, Solodit, etc.) with their endpoint names
- `ui_context.docsStatus` tells you if docs RAG is ready

## Workflow

1. **Look at `ui_context` first.** It already tells you the project root,
   what is excluded, what catalogs exist, what tools are configured, the
   current pipeline state and which nodes are selected.
2. **Read code on demand** via `read_workspace_file` and
   `list_workspace_directory`. All reads are workspace-bound — never
   ask the user for absolute paths.
3. **Use `search_docs`** when the user asks about general Solidity
   patterns, framework behavior, audit methodology, or anything that
   sounds like it might live in their docs corpus. Cite the matched
   files in your text response.
4. **Use external tools** (`hornetmcp_search`, `solodit_findings`, etc.)
   when the user wants similar past findings, exploit references, or
   when you need to enrich a vulnerability with field evidence.
5. **Refer to resources by their catalog entries**, not by guessed paths.
   When recommending a skill / pattern / memory / tool for a canvas node,
   pick from `ui_context.catalogs` so the canvas action validates.

## Reasoning about contracts

When asked about a specific contract or function:

- Confirm the file path is inside `auditPath` and not excluded.
- Read the smallest unit that answers the question (the file or the
  surrounding contract/function). Do not dump the entire tree.
- Quote line ranges in your answer using the `path:line` convention.
- Map findings to the seven canonical headings the audit pipeline uses
  (attack pattern, root cause signals, common vulnerable flows, false
  positives, audit questions, remediation, examples) when the user is
  asking for an audit-style write-up.

## Reasoning about tests

When asked about tests:

- Locate test files via `list_workspace_directory` under `auditPath`.
  Common conventions: `test/`, `tests/`, `*.t.sol` (Foundry), `*.test.ts`
  (Hardhat).
- Read the test for the specific behavior under discussion.
- Distinguish between *what is tested*, *what is asserted*, and *what is
  missing*. The third is usually what the user wants.

## When to propose canvas actions

If the user asks to *prepare an audit run*, *spin up a pipeline*, *add
this contract to the flow*, or anything that translates into structural
canvas changes, hand off to the `canvas-actions` skill via
`read_skill("canvas-actions")` before emitting any `canvas_action`
envelope parts.
