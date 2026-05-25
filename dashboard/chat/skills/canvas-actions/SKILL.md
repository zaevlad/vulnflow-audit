---
name: "canvas-actions"
title: "Canvas Action Protocol Skill"
description: "Exact contract for emitting create/delete/edit canvas actions (nodes, edges, viewport) so they validate against the current ReactFlow state and resource catalogs."
allowed-tools: ["emit_canvas_action", "list_resources"]
---

# Canvas Action Protocol Skill

This skill governs how the chat agent mutates the ReactFlow canvas in
the **Pipeline** tab. Canvas changes are applied **immediately** by the
frontend dispatcher — there is no preview and no confirmation. That
makes correctness non-negotiable.

## Authority

You may only emit `canvas_action` envelope parts when **all** of these
hold:

1. `ui_context.currentTab == "pipeline"` — never on `audit` or
   `editor` (the Audit tab is read-only; the Editor tab has no canvas).
   On any other tab, explain in text what you would do and stop.
2. The action only references node IDs / edge IDs that exist in the
   current `ui_context.nodes` / `ui_context.edges`, or new IDs you
   are creating in the same response.
3. Every resource reference (skill, lead_skill, pattern, memory, tool,
   file path, provider, model) resolves to an entry in
   `ui_context.catalogs` / `ui_context.availableTools`. Reading the
   catalogs first is required.

If any of these fail, do **not** emit the action. Reply in text and
ask the user to switch tabs / fix the catalog / clarify.

## Action envelope shape

Every emitted action goes through the `emit_canvas_action` tool whose
payload is one of:

```jsonc
{ "kind": "create_node", "node_type": "agent|patterns|memory|code|tool",
  "node_id": "optional-stable-id", "position": {"x":0,"y":0},
  "parentId": null, "data": { /* node config */ } }

{ "kind": "delete_node", "node_id": "<existing-id>" }

{ "kind": "update_node", "node_id": "<existing-id>",
  "data": { /* partial merge */ }, "position": {"x":0,"y":0} }

{ "kind": "create_edge", "source": "<node-id>", "target": "<node-id>",
  "edge_id": "optional", "data": {} }

{ "kind": "delete_edge", "edge_id": "<existing-edge-id>" }

{ "kind": "update_edge", "edge_id": "<existing-edge-id>",
  "data": { /* partial merge */ } }

{ "kind": "set_viewport", "viewport": {"x":0,"y":0,"zoom":1} }
```

The backend will:

- Auto-rebase against the latest `canvas_revision` if every referenced
  id still exists (`safe`).
- Reject with a reason if any referenced id is gone (`not safe`).

You do not need to read `canvas_revision` — the backend stamps each
emitted action against the revision the frontend last advertised.

## Node `data` cheatsheet

The vulnflow ReactFlow node `data` follows existing conventions used
by the pipeline UI. Read the existing nodes in `ui_context.nodes`
once before emitting and mirror their fields.

Common fields on `agent` nodes:

- `label` — display label
- `provider` — value from `availableModels`
- `model` — value from `availableModels` (must be valid for provider)
- `agentType` — usually `audit`
- `skill` — path (from `catalogs.skills`)
- `leadSkill` — path (from `catalogs.lead_skills`)
- `patterns` — list of paths (from `catalogs.patterns`)
- `memory` — list of paths (from `catalogs.memory`)
- `memoryPromt` / `memoryPrompts` — list of paths (from `catalogs.memory_promts`)
- `contractPaths` — list of files inside `auditPath`, not in `excludedPaths`
- `tools` — list of names from `availableTools[*].name`

Common fields on `patterns` / `memory` / `code` nodes mirror the existing
node — *always* match the shape of the closest existing node of the same
type in `ui_context.nodes`.

`tool` nodes are children of an `agent` node and carry the agent's tool
selection (`parentAgentId` = the agent node ID).

## Atomicity

When the user asks for a multi-step structural change ("add an agent
with HornetMCP and connect it to memory M"), emit **all** required
actions in the same chat turn:

1. `create_node` for the agent
2. `create_node` for the tool child (with `parentId`)
3. `create_edge` linking memory → agent

Do not split this across turns — partial state is worse than no state.

## When to read the canvas

Always inspect `ui_context.nodes` and `ui_context.edges` before
emitting actions. If `ui_context.canvasRevision == 0` and both lists
are empty, the user has not built anything yet — propose a starter
layout in text and ask before emitting.
