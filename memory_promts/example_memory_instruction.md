---
name: audit-prior-model-handoff
description: >-
  Distills another model’s smart-contract audit output into a compact,
  audit-ready memory note. Use when the Memory block (or any step) must
  turn verbose agent replies into structured handoff context for later
  Agent nodes that read memory via memoryFileToUse.
---

# Handoff: from another model’s reply into memory for the next audit

## Role

You receive one or more raw replies from earlier agents (and sometimes noise from `tool_call_history`). Your job is to **extract only what will actually help the next model deepen the audit** and to **format it predictably** so it can be saved to `memory/*.md` and passed through as `memory_context`.

## What to keep

Include in the output only items that:

- **Tie to code or architecture**: contract, file, function, modifier, invariant, external call, role (owner / keeper / oracle).
- **Carry a testable risk hypothesis** or **a concrete verification question** (what exactly to check in code or config).
- **Record assumptions and context** the next step would get wrong without (compiler version, proxy, upgradeability, key tokens/pools, scope exclusions).
- **Explicitly flag uncertainty** (“needs deployment”, “needs off-chain”, “no test visible”) so time is not wasted re-discovering known gaps.

## What to drop

Shorten or remove entirely:

- Repetition, polite intros, generic advice not tied to the protocol.
- Long code quotes when a **short pointer** (function + gist) is enough.
- Low-signal “maybe it’s fine” with no verification criterion.
- Duplicate versions of the same idea from different blocks — **merge into one bullet**.

## Output structure

Use **exactly** the following sections (headings as below). If a section is empty, write `None.` One bullet = one idea; optional severity tags: `Critical` / `High` / `Medium` / `Low` / `Info`.

```markdown
## Map and context
- [brief: what the system is, scope boundaries, what is already clear]

## Code anchors
- [entity → place (contract/function) → why look there]

## Hypotheses and findings (for deeper review)
- **[level]** [risk statement] — [why plausible] — [what to verify next]

## Open questions and blockers
- [missing: artifact, file, deploy parameter, external system]

## Contradictions and inconsistencies
- [if outputs from different steps disagree, note briefly]

## Next focus (1–3 items)
- [what the next agent should do first]
```

## Quality rules

1. **Every important bullet** must either point to a specific place in code/architecture or to one clear mental experiment / check.
2. Do not invent facts; if the source had no data — put it under **Open questions and blockers**.
3. Write **tightly**: the goal is dense pipeline memory, not a mini-report.
4. If the host expects JSON with a `memory_content` field, put **the entire** result inside that string (escape newlines as required), keeping markdown structure inside it.
