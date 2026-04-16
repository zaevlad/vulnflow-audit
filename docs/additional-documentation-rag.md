# Additional Documentation (`audit_docs`)

This page explains how extra protocol documentation works in VulnFlow.

In simple terms, `audit_docs/` is a place where you can store Markdown documents about the protocol being audited. VulnFlow can then find the most relevant parts of those documents and pass them to an Agent.

This is useful when the Solidity code alone is not enough to understand the intended design.

---

## What goes into `audit_docs`

Put Markdown files (`.md`) inside `audit_docs/`.

Subfolders are supported.

Typical examples:

- protocol design notes
- architecture explanations
- role descriptions
- invariants
- deployment notes
- any other text that helps explain how the protocol is supposed to work

Only Markdown files are indexed by the current code.

---

## Why this helps in a protocol audit

When an Agent analyzes contracts, it sees code.

But during a real audit, you often also want the Agent to understand:

- what the protocol is trying to do
- which roles are trusted
- what assumptions the system makes
- what behavior is intended

That is what `audit_docs` is for.

It gives the Agent extra protocol context, not just source code.

---

## How the index is prepared

VulnFlow does not read `audit_docs` directly on every Agent call.

Instead, the documents are indexed first.

The process works like this:

1. You add or update Markdown files inside `audit_docs/`.
2. You run the documentation preparation step in the UI.
3. VulnFlow rebuilds the stored index for `audit_docs`.

According to the code, preparation does the following:

- scans `audit_docs/` recursively for `.md` files
- removes old indexed rows for `audit_docs`
- splits document text into chunks
- creates embeddings for those chunks
- stores them in `vulnflow.db`

Current indexing settings in the code:

- embedding model: `all-MiniLM-L6-v2`
- embedding size: `384`
- chunk size: `700`
- chunk overlap: `150`

---

## How an Agent uses `audit_docs`

An Agent uses this feature only when `Add relevant docs` is enabled.

If that option is off, the Agent does not receive document context from `audit_docs`.

If the option is on, the flow works like this:

1. VulnFlow first asks the model to generate a few short search queries based on the selected contract or cluster code.
2. Those queries are used to search the prepared index in `vulnflow.db`.
3. The best matching document chunks are collected.
4. VulnFlow sends those chunks to the main Agent call as part of the Agent input.

So the system does not simply attach all docs. It first tries to find the most relevant parts.

---

## What the model generates before the search

Before the real Agent call, VulnFlow makes one extra model request.

That request is not for auditing. It is only for query generation.

The model is asked to:

- look at the current contract or cluster
- describe important mechanisms or concepts
- return `3` to `5` short search queries

The expected format is a JSON array of strings.

Example:

```json
[
  "admin role permissions",
  "vault withdrawal rules",
  "reward distribution logic"
]
```

---

## What is added to the Agent input

If matches are found, VulnFlow adds two extra fields to the Agent input:

- `docs_queries`
- `relevant_docs`

`docs_queries` contains the generated search strings.

`relevant_docs` contains grouped matching chunks from the indexed documentation.

This means the Agent can see:

- what it searched for
- which documentation snippets were found

---

## How search works

The search code currently uses these settings:

- up to `3` results per query
- minimum similarity threshold: `0.85`

If nothing passes that threshold, the Agent may receive no relevant document chunks.

---

## Extra cost

Turning on `Add relevant docs` increases the work for the Agent.

At minimum, it adds one extra model call before the main Agent call, because VulnFlow first needs to generate the document search queries.

---

## What happens if the database is reset

The documentation index is stored in `vulnflow.db`.

If you start VulnFlow without `--keep-db`, the database can be cleared and the prepared documentation index is lost.

In that case, you need to prepare the docs index again before `audit_docs` can be used by Agents.

---

## Status information

VulnFlow can report documentation index status, including:

- docs path
- number of Markdown files
- whether the docs are prepared
- chunk count
- embedding model
- chunk settings
- processing time

This is how the UI knows whether documentation is ready to use.

---

## Common problems

| Problem | What to check |
|------|----------------|
| Agent gets no relevant docs | Make sure `Add relevant docs` is enabled and the docs index was prepared |
| No documents are indexed | Make sure your files are inside `audit_docs/` and use the `.md` extension |
| Docs worked before but not after restart | The database may have been reset; prepare the docs index again |
| Search quality is weak | Add clearer and richer documentation about the protocol design |
| Vector database error | Check that `sentence-transformers` and `sqlite-vec` are installed and working |

---

## Difference from memory

`audit_docs` and `memory` solve different problems.

`audit_docs`:

- is written by the user
- describes the protocol itself
- is attached to an Agent only when `Add relevant docs` is enabled

`memory`:

- is written during the pipeline
- stores results from earlier analysis steps
- is attached to an Agent only when a memory file is selected
