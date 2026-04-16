# Pattern Checking (`Patterns` block)

The `Patterns` block is used to run a checklist against selected contracts or a selected cluster.

In simple terms, you prepare a YAML file with rules or questions, and VulnFlow asks the model to review the code against that list.

This is useful when you want a more structured pass through the code instead of only using free-form Agent prompts.

---

## What the `Patterns` block is for

During a protocol audit, you may want to check the same kinds of risks again and again:

- access control issues
- unsafe token transfers
- missing validation
- incorrect accounting logic
- known classes of smart contract bugs

The `Patterns` block helps with this by using a predefined YAML checklist.

Instead of asking the model to “look for anything,” you give it a batch of specific pattern entries and ask it to review the selected code against them.

---

## Where pattern files live

Pattern files are stored in `patterns/`.

They should use one of these extensions:

- `.yaml`
- `.yml`

In the UI, pattern files are selected from the patterns catalog.

---

## YAML file format

The selected pattern file must contain a top-level YAML list.

Each item in that list must be an object.

Simple example:

```yaml
- id: access-control-01
  name: Missing admin check
  description: Check whether privileged actions are protected

- id: token-01
  name: Unsafe token transfer
  description: Check whether token transfers handle failures correctly
```

Important rules confirmed by the code:

- the root must be a YAML list
- each entry in the list must be an object
- if the list is empty, the block is skipped
- if the file is not a valid list, the block fails

The code does not enforce a fixed schema for each pattern entry. It passes each item to the model as structured input.

---

## What you need to configure

The `Patterns` block requires:

- `patternFile`
- `resultFile`
- `promptFile`
- `provider`
- `model`

It also needs a valid scope:

- `contract` mode with selected contract files
- or `cluster` mode with a selected cluster and attached cluster files

If any required part is missing, the block fails with a configuration error.

---

## Contract mode and cluster mode

The block can work in two ways.

### Contract mode

Use contract mode when you want to run the pattern checklist against selected Solidity files directly.

In this mode, the block reads the selected `contractPaths`.

### Cluster mode

Use cluster mode when you want to run the checklist against a prepared cluster.

In this mode, the block uses `clusterFiles`.

If the cluster exists in name only but has no attached files, the block fails and tells you to regenerate clusters and save the pipeline again.

---

## How the block works

The `Patterns` block works in batches.

This is the flow confirmed by the code:

1. VulnFlow loads the YAML pattern file.
2. It splits the patterns into batches of `10`.
3. It reads the selected Solidity files.
4. For each batch, it sends the model:
   - the current pattern batch
   - batch number information
   - scope information
   - selected contract paths
   - the contract source code
5. It receives the model response as plain text.
6. It writes that response into the selected result file.

So the `Patterns` block is not using the same response format as the `Agent` block by default. It simply sends a checklist batch and saves the model's written answer.

---

## What the model receives

For each batch, the model gets:

- `pattern_batch`
- `batch_index`
- `batch_total`
- `scope`
- `contracts`

That means the model sees both:

- the code
- the exact subset of patterns it should check in this batch

---

## Prompt file

The `Patterns` block requires a `promptFile`.

The code also contains a default fallback prompt, but the block still requires the prompt file path to be selected in its current validation logic.

So in real usage, you should treat `promptFile` as required.

If the selected file is empty or cannot be read, the system falls back to a default prompt that tells the model to review the provided contracts against the supplied patterns and write concise Markdown notes.

---

## Where the result is written

The model output is written into the selected `resultFile`.

For each batch, VulnFlow adds a heading like this before the batch result:

- batch number
- pattern file name
- prompt file name
- scope

If the file already has content, new content is appended instead of replacing the old one.

This makes the result file work like a running checklist report.

---

## Indexing after write

After each batch result is written, VulnFlow also indexes the new written fragment.

This uses the same vector database path that is used for memory indexing.

Important detail:

- the result file is passed into the same indexing helper used for memory-style content
- this is only safe if the result file is inside `memory/`

If you choose a result file outside `memory/`, indexing can fail because the indexing code requires the file to be inside the `memory` directory.

---

## When the block is skipped

The `Patterns` block is skipped when the pattern list is empty.

In that case, no model call is made.

---

## When the block fails

The block fails when:

- no pattern file is selected
- no result file is selected
- no prompt file is selected
- no provider or model is selected
- contract mode has no selected contracts
- cluster mode has no attached cluster files
- the selected Solidity files cannot be read
- the YAML file format is invalid

---

## Difference from an Agent block

The `Patterns` block is not just another Agent role.

Main differences:

- it works from a YAML checklist
- it sends pattern batches to the model
- it saves written results to a file
- it does not use the standard `AgentOutput` JSON schema unless your prompt explicitly asks for that format

This makes it better for structured checklist reviews, while the `Agent` block is better for open-ended analysis.

---

## Practical use in a protocol audit

A common pattern is:

- run a `Patterns` block first for a broad checklist pass
- run one or more `Agent` blocks after that for deeper investigation
- save the checklist result into a file in `memory/`
- reuse that file later if needed

This can be a good way to combine structured rule checking with deeper reasoning.
