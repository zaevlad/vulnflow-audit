# VulnFlow — project reference

This document describes the current repository: a local **smart-contract audit pipeline builder** (web dashboard + Python backend), launched via `vulnflow.py`.

---

## 1. Project overview

**VulnFlow** builds and runs a **block graph** (AI agents, memory, arbitrary Python) over a chosen code folder (typically a Solidity project). In the browser, users wire blocks on a canvas, configure models, skills, and external HTTP tools, save scenarios to `pipelines/*.json`, and execute them. The **FastAPI** backend (`dashboard/server.py`) runs the graph asynchronously (`dashboard/pipeline/engine.py`), routes LLM calls (`dashboard/pipeline/llm_router.py`), and optionally enriches requests with RAG from documentation (`dashboard/docs_rag.py`).

CLI entrypoint — `vulnflow.py`: subcommands `prepare` (venv + `requirements.txt`) and `start` (clears `vulnflow.db` unless `--keep-db`, then Uvicorn + `dashboard.server:create_app` on `127.0.0.1`, port from a free range starting at `--port`, default 7337).

```58:71:vulnflow.py
def start_dashboard(*, port: int = 7337) -> int | None:
    try:
        import uvicorn
        from dashboard.server import create_app
    except ImportError:
        print("Install fastapi and uvicorn to use the dashboard.")
        return None
    selected_port = _find_available_port(port)
    app = create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=selected_port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return selected_port
```

---

## 2. Main characteristics

| Area | Details |
|------|---------|
| **Backend stack** | FastAPI, Uvicorn, httpx, PyYAML, httpx for LLMs and external APIs; `solidity-parser` for clustering; `sentence-transformers` + `sqlite-vec` for embeddings and search |
| **UI stack** | React + React Flow (`dashboard/ui`); built static assets served from `dashboard/dist` |
| **Configuration** | Root `conf.yaml`: model providers and external REST tools |
| **Pipeline data** | JSON: nodes, edges, audit path, exclusions, audit mode |
| **Vector store** | SQLite `vulnflow.db` at workspace root (`audit_docs` chunks and incremental `memory` indexing) |
| **Clustering** | Walk `.sol`, call/import graph, clusters exposed via API for “by cluster” mode |
| **Cost estimation** | `POST /api/pipeline/estimate` — approximate tokens/cost per `agent` node (including child `tool`) |
| **Run events** | WebSocket `ws://<host>/ws/pipeline/{run_id}` streams events; `GET /api/pipeline/status/{run_id}` returns status, block log, and buffered events |

---

## 3. Notable behavior

- **Branching and merging**: multiple outgoing edges spawn parallel branches; multiple incoming edges wait for all predecessors and merge context (`dashboard/pipeline/engine.py`).
- **Four executable node types** (excluding the child tool): `agent`, `patterns`, `memory`, `code` (`dashboard/pipeline/schemas.py` — `BlockType`).
- **`patterns` block**: loads a YAML list of templates from `patterns/*.yaml`, runs the model in batches over selected contracts or cluster files, writes output to a chosen file (`dashboard/pipeline/memory_runner.py` — `run_patterns`).
- **Accumulator between Memory blocks**: consecutive agent outputs accumulate; Memory consumes everything, writes to a file, and clears the buffer (`dashboard/pipeline/context.py`, `engine.py`).
- **Code block**: Python sandbox with `data` / `result`; `inject` in `result` becomes `injected_context` for the next agent (`dashboard/pipeline/code_runner.py`).
- **Child `tool` nodes**: excluded from topological order; bound to an agent and select an external tool (labeled “MCP” in the UI).
- **RAG from `audit_docs`**: when enabled, the agent first gets short search queries from the model, then `relevant_docs` are merged into the user JSON (`dashboard/pipeline/agent_runner.py`).
- **Clean doc index on start**: by default `vulnflow start` deletes `vulnflow.db` (unless `--keep-db`), then the doc index can be rebuilt from the UI/API.

---

## 4. LLM provider variants

Supported provider IDs in code: `chatgpt`, `claude`, `openrouter`, `ollama`, `lmstudio`, `llama_cpp` (`dashboard/pipeline/llm_router.py` — `send_to_model`).

| Provider | Transport | Default base URL | Auth |
|----------|-----------|------------------|------|
| `chatgpt` | OpenAI-compatible `POST .../chat/completions` | `https://api.openai.com/v1` | Bearer, key from `conf.yaml` or `OPENAI_API_KEY` |
| `claude` | Anthropic `POST .../messages` | `https://api.anthropic.com/v1` | `x-api-key`, section key or `ANTHROPIC_API_KEY` |
| `openrouter` | OpenAI-compatible | `https://openrouter.ai/api/v1` | Bearer + `HTTP-Referer`, `X-Title` headers |
| `ollama` | `POST .../api/chat` | `http://localhost:11434` | no key |
| `lmstudio` | OpenAI-compatible | `http://localhost:1234/v1` | no key |
| `llama_cpp` | OpenAI-compatible | `http://localhost:8080/v1` | no key |

The **`supports_response_format`** flag in `conf.yaml` controls whether `response_format: {"type": "json_object"}` is added (OpenAI-compatible). If `false`, a text-only “return JSON only” instruction is appended to system (`_inject_json_fallback_instruction` in `llm_router.py`).

---

## 5. Setup and running

1. **Virtual environment and dependencies** (repo root): `vulnflow prepare` creates `.venv` and installs `requirements.txt` (including `torch`, `sentence-transformers`, `sqlite-vec`).
2. **UI build** (if `dashboard/dist` is missing): in `dashboard/ui` run `npm install` and `npm run build` (otherwise `/` returns a 503 placeholder).
3. **Root `conf.yaml`**:
   - **`models.<provider>`**: `enabled`, `api_key` or `api_key_env`, optional `base_url` / `base_url_env`, `models` list, optional `supports_response_format`;
   - **`tools`**: REST tool array (see §14).
4. **Run**: `vulnflow start` — HTTP on a free port from the preferred one (default 7337), browser may open. `--keep-db` preserves `vulnflow.db`; without it the index file is removed on startup.

**Security:** do not store real API keys in the repo; use `api_key_env` and environment variables.

---

## 6. Loading and configuring the audit project

1. In the UI, pick the **audit folder** (`POST /api/project/select` — native dialog; response updates `auditPath` and the tree).
2. Refresh the tree without changing path: `POST /api/project/tree` with `project_path` and `excluded_paths`.
3. Mark **exclusions** in the tree (paths skipped for contract traversal and cluster generation).
4. **Audit mode**: `contract` or `cluster` (for clusters — `POST /api/clusters/generate`).
5. **RAG docs**: `.md` under `audit_docs/`, then `POST /api/docs/prepare`; status — `GET /api/docs/status`.
6. **Workspace catalogs** (skills, patterns, memory, etc.): `GET /api/project/catalogs` or fields in `GET /api/bootstrap`; model list — `GET /api/settings/providers`.
7. Create a new memory file: `POST /api/memory/create` with `name`.
8. Save pipeline: `POST /api/pipelines/save` → `pipelines/<name>.json` with `projectPath` and `excludedPaths`.
9. Load: `GET /api/pipelines/{pipeline_name}`.
10. Estimate agents: `POST /api/pipeline/estimate` (body with `nodes` from the canvas).
11. Run: `POST /api/pipeline/run` — `pipeline_data` or `pipeline_name`, optional `audit_project_path`, `excluded_paths`, `audit_mode`; response includes `run_id`.
12. During/after run: `GET /api/pipeline/status/{run_id}`; stop — `POST /api/pipeline/stop/{run_id}`; event stream — WebSocket `/ws/pipeline/{run_id}`.
13. Open `conf.yaml` in the default app: `POST /api/config/open`.

Initial UI state: `GET /api/bootstrap` (workspace, providers, catalogs, docs, pipeline list, workspace tree, `system`: `forge`/`medusa` presence, config path).

---

## 7. Canvas: blocks and settings

Canvas uses React Flow. Node types: `agent`, `patterns`, `memory`, `code`, `tool` (`dashboard/ui/src/App.jsx`, `nodeTypes`).

### Agent

Fields in saved JSON (`serializeNodes`):

- `agentType` — role: `preparation` | `audit` | `verification` | `report` | `test` (UI labels from `AGENT_TYPES`);
- `provider`, `model`;
- `skill` **or** `leadSkill` (mutually exclusive in the UI);
- `contractPaths` — file paths (in `contract` mode);
- `cluster` — cluster id string (in `cluster` mode);
- `addRelevantDocs` — boolean, active when the doc index is prepared.

Each agent automatically gets a child **`tool`** node (edge agent → tool).

### Patterns

- `patternFile` — absolute path to YAML under `patterns/` (file root is a **list** of objects; see §15).
- `resultFile` — where to write output (e.g. markdown under `memory/` or another folder).
- `promptFile` — optional text/MD instructions; if empty, a built-in default auditor prompt is used.
- `provider`, `model`.
- Scope: `contractPaths` in contract mode **or** `cluster` + `clusterFiles` in cluster mode (`scopeMode` / `cluster` in node data).
- Empty pattern list in YAML → block marked **`skipped`**.

### Tool (labeled “MCP” in the UI)

Serialized as:

```json
{ "mcp": "<tool_name_from_conf.yaml>", "parentAgentId": "<agent_id>" }
```

### Memory

- `memoryFile` — absolute path to `.md` under `memory/`;
- `memoryPrompt` — prompt file path from `memory_promts/*.md`;
- `provider`, `model`.

### Code

- `code` — Python source for `exec` in the sandbox.

Server-side pipeline JSON includes `nodes`, `edges`, `name`, `projectPath`, `excludedPaths`, `auditMode` (any saved JSON under `pipelines/*.json`).

---

## 8. LLM requests — JSON-oriented formats

Below are **request/response bodies** and **payloads** without listing HTTP headers.

### 8.1. OpenAI-compatible providers (`chatgpt`, `openrouter`, `lmstudio`, `llama_cpp`)

**Request** `POST {base_url}/chat/completions`:

```json
{
  "model": "<model_id>",
  "messages": [
    { "role": "system", "content": "<text>" },
    { "role": "user", "content": "<string, often a JSON string>" },
    { "role": "assistant", "content": "...", "tool_calls": [] },
    { "role": "tool", "tool_call_id": "<id>", "content": "<JSON string result>" }
  ],
  "response_format": { "type": "json_object" },
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "<tool>_<endpoint>",
        "description": "<string>",
        "parameters": {
          "type": "object",
          "properties": { },
          "required": [],
          "additionalProperties": false
        }
      }
    }
  ]
}
```

`response_format` is omitted if `supports_response_format: false`.  
**Response** (used fragment): `choices[0].message` with `content`, optional `tool_calls`; plus `usage`.

### 8.2. Anthropic (`claude`)

**Request** `POST {base_url}/messages`:

```json
{
  "model": "<model_id>",
  "max_tokens": 16384,
  "system": "<combined system messages>",
  "messages": [
    { "role": "user", "content": "<text or blocks>" },
    {
      "role": "assistant",
      "content": [
        { "type": "text", "text": "..." },
        { "type": "tool_use", "id": "...", "name": "...", "input": {} }
      ]
    },
    {
      "role": "user",
      "content": [
        { "type": "tool_result", "tool_use_id": "...", "content": "<JSON string>" }
      ]
    }
  ],
  "tools": [
    { "name": "<tool>_<endpoint>", "description": "...", "input_schema": { "type": "object", "properties": {}, "required": [] } }
  ]
}
```

**Response**: JSON with `content` array (`text` and `tool_use` blocks), `stop_reason`, `usage`.

### 8.3. Ollama

**Request** `POST {base_url}/api/chat`:

```json
{
  "model": "<model_id>",
  "messages": [ ],
  "stream": false,
  "format": "json",
  "tools": [ ]
}
```

`format` is set when `json_mode=true`. **Response**: object with `message.content`, `message.tool_calls`, counters in `prompt_eval_count` / `eval_count`.

### 8.4. Agent user message (single call before tool-call loop)

Built in `agent_runner._build_messages`: `role: user` is **one JSON string** object:

```json
{
  "contracts": { "/abs/path/File.sol": "<source code>" },
  "injected_context": {},
  "docs_queries": ["query 1", "query 2"],
  "relevant_docs": [
    {
      "file": "audit_docs/protocol.md",
      "max_similarity": 0.91,
      "chunks": [
        {
          "id": 1,
          "similarity": 0.91,
          "chunk_index": 0,
          "text": "..."
        }
      ]
    }
  ]
}
```

`injected_context`, `docs_queries`, `relevant_docs` appear only when needed.

### 8.5. Expected agent JSON response (`AgentOutput`)

Logical structure after parsing (`dashboard/pipeline/schemas.py`):

```json
{
  "vulnerabilities": [
    { "title": "", "description": "", "recommendation": "" }
  ],
  "ideas": [
    { "title": "", "description": "", "confidence": 0.0 }
  ],
  "tool_call": { "name": "", "arguments": {} },
  "tool_call_flag": false,
  "raw_data": {},
  "summary": "",
  "metadata": {}
}
```

Legacy path: `tool_call_flag: true` triggers a tool call from JSON (alongside native provider `tool_calls`).

### 8.6. Memory block request

```json
{
  "role": "system",
  "content": "<text from memory_promts or default prompt>"
}
```

```json
{
  "role": "user",
  "content": "{\"accumulated_contexts\": [ { \"block_id\": \"...\", \"block_label\": \"...\", \"output\": { }, \"tool_call_history\": [] } ]}"
}
```

The model response is a string; the server **writes `content` as-is** to the file (often JSON text from the model), then indexes it for RAG.

---

## 9. Skills and `lead_skills`

- **`skills/*.md`** — standard audit skills. The agent field `skill` selects a file; its full text becomes the **system prompt** (`agent_runner._build_system_prompt` reads the path under `skills/`).
- **`lead_skills/*.md`** — alternative “lead” prompts. Field `leadSkill`; when set, **`skill` is ignored** (second select disabled in the UI).
- If neither is set, a short default auditor + JSON response instruction is used.

Semantic split is defined by markdown content; loading treats both directories the same except for path.

---

## 10. How `audit_docs` works

- **`audit_docs/`** at workspace root: all **`*.md`** files are indexed recursively.
- On **`prepare_docs_index`**: old chunks with path prefix `audit_docs/` are removed; files are split into chunks (size 700, overlap 150), each chunk embedded (`all-MiniLM-L6-v2`, dim 384), stored in SQLite + `vec0`.
- During an agent run with **`addRelevantDocs`**: a separate model call produces 3–5 search strings; `search_relevant_chunks` runs (cosine similarity, `min_similarity` filter, default 0.85); results are grouped by file and merged into the user JSON as `relevant_docs`.

Index status is exposed via API (`get_docs_status`: md count, chunk count, `prepared`, metadata).

---

## 11. Memory in the project

- **`memory/*.md`** — long-lived notes; listed in bootstrap as a catalog.
- **Write prompts** — `memory_promts/*.md`; instructions for the Memory block model.
- **Vector index**: after a write, new text is chunked and **appended** to the same DB (old chunks for that file are not deleted; next free `chunk_index` is used). Memory search is not merged into agent requests in the current pipeline (unlike `audit_docs`); indexing supports future use and RAG consistency.

---

## 12. Memory write flow

1. A **Memory** block follows one or more **Agent** blocks without another Memory in between.
2. `engine.py` merges agent outputs into the accumulator; entering Memory calls `run_memory` with a copy of all `AccumulatorEntry` values.
3. If the accumulator is empty — block **`skipped`**.
4. Otherwise LLM messages are built (see §8.6), `send_to_model` with `json_mode=True`.
5. The returned **`content`** string is appended to the file with `\n\n---\n\n` if the file was non-empty.
6. **`index_memory_content`** indexes only the new fragment (path must stay under `memory/`).

---

## 13. Clustering in audit mode

- **`POST /api/clusters/generate`** takes `project_path` and `excluded_paths`, collects all `.sol` outside exclusions, builds AST/graph data, then `build_function_call_clusters` (`dashboard/cluster_logic/service.py`).
- Response includes **`clusters`** (metadata: `cluster_id`, `rank`, file/function lists, deps), **`clusterOptions`** for the agent select, **`stats`**, optional **`callLinkGraph`**.
- In the UI, **`cluster`** mode switches the agent scope from multi-select contracts to **`cluster`**; the agent prompt gains `=== SELECTED CLUSTER ===` with the `cluster` field text (id and representation come from backend data).

---

## 14. “MCP” in the UI and external tools

There is no separate **Model Context Protocol** process (stdio/SSE servers like Cursor). Instead:

1. **`conf.yaml`** **`tools`** array describes **REST APIs** with: `name`, `description`, `base_url`, `auth_type` (`bearer` or `api_key_header`), `api_key`, `api_key_header`, `endpoints[]` with `name`, `path`, `method`, `parameters`.
2. `dashboard/pipeline/external_tools.py` builds **function-calling** schemas for Claude or OpenAI-style APIs; function name = `{name}_{endpoint.name}`.
3. On the **Tool** node the user picks a tool by **`name`** from the catalog; `execute_external_tool_call` builds URL, auth headers, and JSON body or query per method.

Example **`conf.yaml` fragment** (use your keys or `api_key_env`):

```yaml
tools:
  - name: example_api
    description: "Short description"
    base_url: https://api.example.com/v1
    auth_type: api_key_header
    api_key_header: X-API-Key
    api_key: ""
    # or: api_key_env: EXAMPLE_API_KEY
    endpoints:
      - name: search
        path: search
        method: POST
        description: "Search"
        parameters:
          - name: query
            type: string
            required: true
            description: "Query text"
```

---

## 15. `patterns/` catalog and YAML format for the Patterns block

- **`patterns/`** at workspace root: bootstrap and `GET /api/project/catalogs` list **`*.yaml` and `*.yml`** files in the **directory root only** (no recursion into subfolders — see `_catalog_entries` in `dashboard/server.py`).
- The repo may include **`patterns/vulnerabilities.yaml`**: a long list of entries with fields like `id`, `name`, `severity`, `description`, `false_positives`, etc.; fields are passed to the model inside batches. The parser requires the file root to be a **YAML array** of objects (`memory_runner._load_patterns`).
- Execution: entries are split into batches of 10; each batch includes selected contracts or cluster files; model output accumulates in `resultFile` (see `run_patterns` in `memory_runner.py`).

---

## Section index

| # | Section | Coverage |
|---|---------|----------|
| 1 | Overview | §1 |
| 2 | Main characteristics | §2 |
| 3 | Notable behavior | §3 |
| 4 | LLM providers | §4 |
| 5 | Setup | §5 |
| 6 | Loading the audit project | §6 |
| 7 | Canvas and blocks | §7 |
| 8 | JSON request/response formats | §8 |
| 9 | Skills / lead_skills | §9 |
| 10 | audit_docs | §10 |
| 11 | Memory | §11 |
| 12 | Memory writes | §12 |
| 13 | Clusters | §13 |
| 14 | MCP / external tools | §14 |
| 15 | patterns catalog / Patterns block | §15 |
