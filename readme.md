<!-- markdownlint-disable MD033 MD041 -->

<div align="center">
  <img src="docs/vulnflow_logo.png" alt="VulnFlow logo" width="220">

# VulnFlow

**VulnFlow is a visual smart contract audit builder.**

It helps you set up audit pipelines with AI agents, pattern checks, memory, Python logic, and external tools in one interface. You choose a Solidity project, build a workflow on the canvas, run it, and save the pipeline as JSON in `pipelines/`.

</div>

## Table of Contents

- [VulnFlow](#vulnflow)
  - [Table of Contents](#table-of-contents)
  - [What Is VulnFlow](#what-is-vulnflow)
  - [Why VulnFlow](#why-vulnflow)
  - [Screenshots](#screenshots)
  - [Quick Start](#quick-start)
  - [How to Set Up a Project for Audit](#how-to-set-up-a-project-for-audit)
  - [What You Need](#what-you-need)
  - [Project Structure](#project-structure)
  - [Configuration](#configuration)
  - [Using the UI](#using-the-ui)
  - [External Tools](#external-tools)
  - [Troubleshooting](#troubleshooting)
  - [More Documentation](#more-documentation)
  - [CLI](#cli)
  - [Contact](#contact)

---

## What Is VulnFlow

VulnFlow is a local tool for building and running smart contract audit workflows.

You can use it to:

- connect multiple audit agents in one pipeline
- run rule-based pattern checks
- store intermediate notes in memory files
- add Python logic between steps
- attach external REST tools
- save and reuse audit scenarios

It is useful when you want a repeatable audit process instead of running every step manually.

---

## Why VulnFlow

- **Works with local models**
  You do not need an expensive top-tier model for every audit step. VulnFlow works with local and OpenAI-compatible models for preparation, analysis, memory, and reporting.

- **You decide how many agents to use**
  Run a simple one-agent flow or build a larger multi-step audit pipeline. Save pipelines and reuse them later.

- **Custom YAML-based vulnerability checks**
  Run structured checks against your own YAML rules. You can create new pattern files or extend the existing ones.

- **Flexible memory and context control**
  Save intermediate results, reuse them in later steps, and control how much data is passed into each model call.

- **Extra protocol context with embedding-based document search**
  Add protocol documentation to `audit_docs/` and let VulnFlow find relevant fragments before the main audit step.

- **Security research tools for stronger audits**
  VulnFlow supports connected tools such as `Solodit` and `HornetMCP`, allowing the model to look up similar bugs and external security context. Users connect these tools with their own API keys.

- **Fully customizable pipelines**
  You decide how the pipeline works, how results are combined, and what goes into the final report.

- **Reusable visual workflows**
  Build audit flows visually, save them as JSON, and run them again on the same or a different protocol.

- **Structured and open-ended analysis in one place**
  Combine agents, pattern checks, memory, code blocks, and external tools in one workflow.

- **Designed for long and complex audits**
  Use contract-level review, cluster-based review, saved memory, and reusable pipelines for larger protocols.

---

## Screenshots

<p align="center">
  <img src="docs/screen_1.PNG" alt="VulnFlow screen 1" width="100%">
</p>

<p align="center">
  <img src="docs/screen_2.PNG" alt="VulnFlow screen 2" width="100%">
</p>

<p align="center">
  <img src="docs/screen_3.PNG" alt="VulnFlow screen 3" width="100%">
</p>

<p align="center">
  <img src="docs/screen_4.PNG" alt="VulnFlow screen 4" width="100%">
</p>

<p align="center">
  <img src="docs/screen_5.PNG" alt="VulnFlow screen 5" width="100%">
</p>

---

## Quick Start

1. Prepare the Python environment from the repository root:

   ```bash
   python vulnflow.py prepare
   ```

   On Windows you can also use `vulnflow.cmd` or `vulnflow.ps1`.

2. Activate the virtual environment:

   - Windows PowerShell: `.venv\Scripts\Activate.ps1`
   - Linux/macOS: `source .venv/bin/activate`

3. Build the UI if needed:

   ```bash
   cd dashboard/ui
   npm install
   npm run build
   ```

4. Configure `conf.yaml` with at least one enabled model provider.

5. Start VulnFlow:

  ```powershell
  vulnflow start
  ```
  
  or

   ```bash
   ./vulnflow.py start
   ```

6. Open the local builder URL shown in the terminal.

By default, VulnFlow starts on `127.0.0.1` and tries port `7337` first.

| Flag | Meaning |
|------|---------|
| `--port <n>` | Preferred port |
| `--no-open` | Do not open a browser tab |
| `--keep-db` | Keep `vulnflow.db` on startup |

---

## How to Set Up a Project for Audit

This is the simplest recommended flow for starting a new audit in VulnFlow.

1. Install and prepare VulnFlow.

   Run `python vulnflow.py prepare`, activate the virtual environment, and build the UI if it is not built yet.

2. Configure your models in `conf.yaml`.

   Add at least one enabled provider in the `models` section. Without this, agents will not run.

3. Put the target protocol in `audit_protocol/`.

   Place the smart contract repository you want to audit inside `audit_protocol/`. This keeps the target code in a predictable location.

4. Add extra documentation to `audit_docs/` if needed.

   Put specs, whitepapers, notes, or protocol docs there if you want agents to use them as additional context.

5. Start the builder.

   Run `python vulnflow.py start` from the project root and open the local UI.

6. Choose the audit project folder in the UI.

   Select the protocol folder and exclude files or directories you do not want to process.

7. Select the audit mode.

   Use `Contract` mode if you want to audit specific `.sol` files directly. Use `Cluster` mode if you want VulnFlow to group related contracts first.

8. Build the pipeline on the canvas.

   Add the blocks you need, such as `Agent`, `Patterns`, `Memory`, `Code`, and `Tool`.

9. Save the pipeline.

   Saved scenarios are stored as JSON files in `pipelines/`, so you can reuse them later.

10. Run the audit.

   Start the pipeline from the UI, monitor the logs, and review the generated outputs.

---

## What You Need

| Component | Why it is needed |
|-----------|------------------|
| Python 3 | Backend and CLI |
| Node.js + npm | UI build in `dashboard/ui` |
| LLM access | Agent execution through `conf.yaml` |
| Disk space | Embeddings, indexes, and local runtime data |

Optional tools like `forge` and `medusa` can also be available on your system PATH, but they are not required just to open and use the dashboard.

---

## Project Structure

| Path | Purpose |
|------|---------|
| `vulnflow.py` | Main CLI entry point |
| `dashboard/server.py` | FastAPI server |
| `dashboard/pipeline/` | Pipeline engine, agents, tools, routing |
| `dashboard/cluster_logic/` | Solidity parsing and cluster generation |
| `dashboard/ui/` | React UI source |
| `dashboard/dist/` | Built UI served by the backend |
| `pipelines/` | Saved pipeline JSON files |
| `audit_protocol/` | Target repository for the audit |
| `audit_docs/` | Extra documents for RAG and context |
| `memory/` | Long-lived notes and outputs |
| `patterns/` | Pattern-checking definitions |

Main runtime files and folders also include `skills/`, `lead_skills/`, `memory_promts/`, `conf.yaml`, and `vulnflow.db`.

---

## Configuration

The main configuration file is `conf.yaml`.

At minimum, you need:

- one enabled provider under `models`
- at least one model name in that provider
- an API key or an environment variable reference

Minimal example:

```yaml
models:
  openrouter:
    enabled: true
    api_key_env: OPENROUTER_API_KEY
    supports_response_format: true
    models:
      - openai/gpt-4o-mini

tools: []
```

Supported provider IDs:

- `chatgpt`
- `claude`
- `openrouter`
- `ollama`
- `lmstudio`
- `llama_cpp`

Notes:

- Use environment variables for real API keys.
- If a local model does not support strict JSON output well, set `supports_response_format: false`.
- OpenAI-compatible providers may also use `base_url` or `base_url_env`.

For full details, see [`docs/configuration.md`](docs/configuration.md).

---

## Using the UI

The UI is built around a visual canvas.

Basic flow:

1. Select the project folder to audit.
2. Choose `Contract` or `Cluster` mode.
3. Add and connect blocks on the canvas.
4. Save the pipeline.
5. Run it and review the results.

Main block types:

| Block | What it does |
|-------|---------------|
| `Agent` | Runs a selected audit role |
| `Patterns` | Checks contracts against YAML-based pattern rules |
| `Tool` | Calls an external REST tool from `conf.yaml` |
| `Memory` | Writes summaries and notes into `memory/*.md` |
| `Code` | Runs Python logic and passes data to the next step |

If you want agents to use additional protocol documentation, prepare `audit_docs/` and enable relevant document usage where needed.

---

## External Tools

The `tools` section in `conf.yaml` is used to describe external REST APIs.

Each configured tool can expose endpoints that the model can call through a `Tool` node. This is useful when you want an agent to fetch extra data or trigger a controlled external workflow.

---

## Troubleshooting

| Problem | What to check |
|---------|---------------|
| UI is not built | Run `npm install` and `npm run build` in `dashboard/ui` |
| Virtual environment problems | Make sure `.venv` is activated after `prepare` |
| No providers in UI | Check `enabled: true`, model list, and API key setup |
| Local model returns bad JSON | Try `supports_response_format: false` |
| RAG has no useful context | Check files in `audit_docs/` and your indexing flow |
| SQLite or `sqlite-vec` issues | Make sure required Python packages are installed correctly |

---

## More Documentation

Extra project guides:

- [`docs/README.md`](docs/README.md)
- [`docs/configuration.md`](docs/configuration.md)
- [`docs/memory-in-the-project.md`](docs/memory-in-the-project.md)
- [`docs/additional-documentation-rag.md`](docs/additional-documentation-rag.md)
- [`docs/pattern-checking.md`](docs/pattern-checking.md)
- [`docs/agent-preparation.md`](docs/agent-preparation.md)
- [`docs/agent-audit.md`](docs/agent-audit.md)
- [`docs/agent-verification.md`](docs/agent-verification.md)
- [`docs/agents-report-and-test.md`](docs/agents-report-and-test.md)
- [`docs/skills-and-lead-skills.md`](docs/skills-and-lead-skills.md)
- [`docs/pipeline-save-and-load.md`](docs/pipeline-save-and-load.md)
- [`vulnflow_project.md`](vulnflow_project.md)

---

## CLI

```text
python vulnflow.py --help
python vulnflow.py start --help
```

---

## Contact

Questions, ideas, and suggestions:

- X / Twitter: [@RightNowIn](https://x.com/RightNowIn)
