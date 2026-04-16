# Agent Block

## What the Agent block is

The `Agent` block is the main AI block in VulnFlow.

It sends selected audit data to a model, gets the model response back, and passes that result to the next step in the pipeline.

All Agent roles in the UI use the same execution logic. The role is chosen by the user and mainly helps describe the purpose of that step in the pipeline.

Available roles in the UI:

- `Preparation`
- `Audit`
- `Verification`
- `Report`
- `Test`

In simple words:

- `Preparation` is usually used to collect context before deeper analysis.
- `Audit` is usually used to look for issues.
- `Verification` is usually used to check or challenge earlier findings.
- `Report` is usually used to turn results into a cleaner final summary.
- `Test` is usually used to describe test ideas or testing scenarios.

These roles do not change the core engine. What really changes the agent behavior is the model, the prompt file, the selected contracts or cluster, the memory file, relevant docs, and optional external tools.

---

## What the Agent block can use

An Agent block can work with the following inputs:

- `Provider` and `Model`
- `Skill` or `Lead skill`
- selected contract files
- a selected cluster
- a memory file from `memory/`
- relevant documentation from `audit_docs/`
- injected data from a previous `Code` block
- one attached external tool node

### Provider and model

These are required. If the block has no provider or no model, the run stops with an error for that block.

### Skill or lead skill

The block can use:

- one file from `skills/`
- or one file from `lead_skills/`

If neither is selected, the system uses a default prompt that tells the model to act like a smart contract security auditor and return JSON.

### Contract files or cluster

The Agent block can receive Solidity code from the contracts selected in the UI.

If the pipeline works in cluster mode, the selected cluster is also part of the context for the model.

### Memory file

If a memory file is selected, its content is read and added to the agent input as `memory_context`.

If a memory file is selected in the UI but the file cannot be found, the block fails with an error.

### Relevant docs

If `Add relevant docs` is enabled, VulnFlow first asks the model to generate a few short documentation search queries based on the selected code. Then it searches `audit_docs/` and adds the best matching document chunks to the final agent input.

This is meant to give the model extra protocol context, not to search for vulnerabilities directly.

### Data from a Code block

If a previous `Code` block injects data, that data is passed into the agent input as `injected_context`.

### External tool

An Agent block can have one child `Tool` node. If configured, the model can call the selected external REST tool during the agent run.

---

## What the model receives

The system sends the model:

1. A system prompt.
2. A JSON payload in the user message.

That JSON payload can contain these fields:

- `contracts`
- `memory_context`
- `injected_context`
- `docs_queries`
- `relevant_docs`

Not every field is always present. The system only includes fields that are available for that specific run.

Simple example:

```json
{
  "contracts": {
    "C:/path/Token.sol": "contract Token { ... }"
  },
  "memory_context": "# Previous notes\n...",
  "injected_context": {
    "focus": "Check access control around minting"
  },
  "docs_queries": [
    "token minting permissions",
    "admin role flow"
  ],
  "relevant_docs": [
    {
      "file": "spec.md",
      "max_similarity": 0.91,
      "chunks": [
        {
          "id": "chunk-1",
          "similarity": 0.91,
          "chunk_index": 0,
          "text": "..."
        }
      ]
    }
  ]
}
```

---

## What the model should return

The system expects the model to return JSON matching the `AgentOutput` structure.

Main fields:

- `vulnerabilities`
- `ideas`
- `tool_call`
- `tool_call_flag`
- `raw_data`
- `summary`
- `metadata`

### JSON schema in plain English

- `vulnerabilities`: a list of findings
- `ideas`: a list of observations, hypotheses, or follow-up ideas
- `tool_call`: one tool call request in the legacy JSON format
- `tool_call_flag`: `true` when the model wants the system to execute `tool_call`
- `raw_data`: extra structured data
- `summary`: a short text summary
- `metadata`: extra metadata fields

### Field structure

```json
{
  "vulnerabilities": [
    {
      "title": "string",
      "description": "string",
      "recommendation": "string"
    }
  ],
  "ideas": [
    {
      "title": "string",
      "description": "string",
      "confidence": 0.0
    }
  ],
  "tool_call": {
    "name": "string",
    "arguments": {}
  },
  "tool_call_flag": false,
  "raw_data": {},
  "summary": "string",
  "metadata": {}
}
```

### Notes about the response

- `vulnerabilities` and `ideas` are both optional, but they are the main useful parts of the response.
- `confidence` in `ideas` is a number.
- `tool_call` and `tool_call_flag` are only needed when the model requests an external tool through the legacy JSON path.
- Tool calls can also happen through provider-native tool calling when the selected provider supports it.
- If tool calls are used, the system can continue the conversation with the model and ask for a final answer after the tool result is returned.

---

## What happens after the model responds

If the response is valid JSON, VulnFlow stores it as the output of that Agent block.

If the response is not valid JSON, the system still keeps the response, but in a fallback form:

- the raw text is saved into `summary`
- the same raw text is also saved into `raw_data.raw_text`

This helps keep the pipeline running even if the model does not follow the expected schema perfectly.

---

## How to think about Agent roles

Because all roles share the same engine, it is best to treat them as labels for workflow intent.

Use them like this:

- choose `Preparation` when you want the model to map the system, list assumptions, or collect context
- choose `Audit` when you want the model to search for risks and possible vulnerabilities
- choose `Verification` when you want the model to review earlier findings and reduce false positives
- choose `Report` when you want the model to organize and summarize results
- choose `Test` when you want the model to propose test scenarios

This makes the pipeline easier to read, even though the technical execution path is the same.

---

## Important limitation

The UI shows whether `forge` and `medusa` are available on the system, but the Agent block itself does not automatically run those tools as part of its standard role behavior.
