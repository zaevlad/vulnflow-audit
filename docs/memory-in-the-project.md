# Memory in VulnFlow

This page explains how memory works in VulnFlow in simple terms.

In short, memory is a way to save useful audit notes between steps of a pipeline. It helps you keep important findings, summaries, and intermediate conclusions while auditing a protocol.

---

## What memory is for

When you audit a protocol, you often do not want every Agent to start from zero.

For example, you may want to:

- collect the first findings from several Agent steps
- save a short summary of what was already discovered
- pass that summary into a later Agent
- keep notes from one run and reuse them in another run

That is what memory is for in VulnFlow.

Memory does not replace the main audit flow. It stores the results of earlier analysis so later steps can use them more easily.

---

## Main folders

Two folders are used for memory:

| Path | Purpose |
|------|---------|
| `memory/` | Markdown files where VulnFlow stores saved memory notes |
| `memory_promts/` | Prompt files used to tell the model what kind of memory note it should write |

You can create a new memory file from the UI. That file is created inside `memory/`.

---

## How the Memory block works

The `Memory` block is a pipeline block on the canvas.

Its job is to take the results produced by earlier Agent blocks, ask a model to turn them into a cleaner memory note, and then save that note into a file.

This is how it works:

1. Agent blocks run earlier in the same branch.
2. Their outputs are collected in an internal buffer.
3. When the pipeline reaches a `Memory` block, VulnFlow takes everything currently stored in that buffer.
4. It sends that data to the selected model together with the selected memory prompt.
5. It writes the model response into the selected memory file.

So the `Memory` block is not a free chat window. It is a summary and note-writing step inside the pipeline.

---

## How this works during a protocol audit

In a protocol audit, memory usually works like this:

1. One or more Agent blocks analyze contracts, a cluster, or protocol context.
2. They produce findings, ideas, and summaries.
3. A `Memory` block collects those results and writes a compact note to a file in `memory/`.
4. A later Agent can read that file and use it as extra context.

This is useful when the protocol is large and the audit is split into stages.

Example:

- a preparation step maps the system
- an audit step finds possible risks
- a memory step saves the current understanding
- a verification step re-checks the most important findings
- another memory step saves the refined result
- a report step uses that saved context to write a cleaner final output

In other words, memory helps carry forward what was already learned about the protocol.

---

## What data the Memory block receives

The Memory block reads data from the pipeline accumulator.

The accumulator is an internal list of finished Agent outputs in the current branch.

Each saved entry includes:

- `block_id`
- `block_label`
- `output`
- `tool_call_history`

So the Memory block does not re-read the whole protocol repository on its own. It works with the results that earlier Agent blocks already produced.

---

## What happens if there is no data

If the accumulator is empty, the `Memory` block does not call the model.

Instead, the block is marked as `skipped`.

This means the Memory block only works when there is already something meaningful to save.

---

## Prompt used by the Memory block

The Memory block can use a prompt file selected from `memory_promts/`.

That prompt becomes the system instruction for the model and tells it what kind of note to write.

If no prompt file is available, VulnFlow uses a built-in default prompt. That default prompt tells the model to act like a memory writer for a smart contract audit pipeline and return JSON with a `memory_content` string field.

In practice, the block writes the model response content to the file.

---

## Where the memory is written

The Memory block writes the result to the selected file in `memory/`.

If the file already has content, VulnFlow adds a separator before the new entry:

```text
---
```

So one memory file can contain multiple saved notes over time.

This makes the file act like a running audit notebook for the protocol.

---

## How another Agent uses memory

An Agent block can optionally use a memory file through `memoryFileToUse`.

When that is set, VulnFlow reads the selected file and includes its text in the Agent request as:

`memory_context`

That means a later Agent can see what was already written to memory and continue from there.

This is the main way memory is reused in the current pipeline.

Example:

- Audit Agent writes findings into memory
- Verification Agent reads that memory file
- Report Agent reads the same file and prepares a cleaner summary

If the selected memory file cannot be found when an Agent tries to use it, that Agent run fails.

---

## Important difference from `audit_docs`

Memory and `audit_docs` are not the same thing.

`audit_docs` is used as extra protocol documentation when `Add relevant docs` is enabled for an Agent.

Memory works differently:

- memory is created from your own pipeline results
- memory is stored in `memory/*.md`
- memory is only passed to an Agent when you explicitly select a memory file

So memory is not automatically included in every Agent request.

---

## Indexing and `vulnflow.db`

After the Memory block writes a new piece of content, VulnFlow also indexes that new fragment in `vulnflow.db`.

This uses the same vector database approach that is also used for documentation indexing.

Important detail:

- only the new fragment is indexed after each memory write
- this indexing does not automatically inject memory into future Agent prompts

The saved file is still the main source of truth. Indexing only prepares the data for vector-based storage and future use.

Also note:

- if you start VulnFlow without `--keep-db`, the database file can be recreated
- documentation indexes may need to be prepared again
- memory files themselves stay on disk, but only newly written fragments are indexed again automatically

---

## Practical pattern

A common pattern looks like this:

`Preparation or Audit -> Memory -> Verification -> Memory -> Report`

This gives you:

- raw analysis from Agents
- saved notes between stages
- better continuity across a long protocol audit

---

## Summary

| Part | What it does |
|------|---------------|
| `memory/` | Stores saved audit notes as Markdown files |
| `memory_promts/` | Stores instructions for how the memory note should be written |
| `Memory` block | Collects earlier Agent outputs and writes a saved note |
| `memoryFileToUse` | Lets an Agent read a saved memory file as extra context |
| `vulnflow.db` indexing | Indexes new memory fragments, but does not automatically inject them into every Agent |
