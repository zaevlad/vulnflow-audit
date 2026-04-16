# Saving and loading pipelines

## Storage location

- Pipelines are JSON files under **`pipelines/`** in the workspace root.
- Filename = sanitized **pipeline name** + `.json` (`_sanitize_pipeline_name` in `dashboard/server.py`: alphanumeric, `.`, `_`, `-`; unsafe characters become `-`).

## Saving

1. Build the graph on the canvas (nodes, edges, viewport if stored).
2. Enter a **Pipeline name** in the toolbar.
3. Click **Save pipeline** — client calls **`POST /api/pipelines/save`** with:
   - `audit_project_path` — folder under audit;
   - `excluded_paths` — excluded tree paths;
   - `pipeline_name` — string name;
   - `pipeline_data` — serialized graph and metadata.

The server **merges** into `pipeline_data`:

- `projectPath` — resolved absolute path to the audit project;
- `excludedPaths` — copied from the request.

Response includes `savedPath`, `savedName`, and refreshed **`pipelines`** list.

## Loading

- **Dropdown** of saved pipelines uses **`GET /api/bootstrap`** or **`GET /api/project/catalogs`** (`pipelines` array with `name` and `path`).
- Selecting a pipeline triggers **`GET /api/pipelines/{pipeline_name}`**, which returns `{ "pipeline": <json> }`.
- The client restores nodes, edges, pipeline name, and typically refreshes the audit path / exclusions from the saved JSON.

## What is inside the JSON

Expect at least:

- `nodes`, `edges` — React Flow graph;
- `name` — display name;
- `projectPath`, `excludedPaths` — audit scope;
- `auditMode` — `contract` or `cluster`;
- Per-node `data` for agents (e.g. `agentType`, `provider`, `model`, `skill`, `leadSkill`, `contractPaths`, `cluster`, `addRelevantDocs`, `memoryFileToUse`, …), Memory, Code, Patterns, Tool children.

Exact fields match **`serializeNodes`** / deserialization in `dashboard/ui/src/App.jsx`.

## Running without saving

**Start Audit** can send the **current canvas** as `pipeline_data` in **`POST /api/pipeline/run`** even if you never saved to disk—`pipeline_name` may default to something like `current-canvas`.

## API summary

| Action | Endpoint |
|--------|----------|
| List | Included in `GET /api/bootstrap` and `GET /api/project/catalogs` |
| Save | `POST /api/pipelines/save` |
| Load | `GET /api/pipelines/{pipeline_name}` |
| Run | `POST /api/pipeline/run` (body: `pipeline_data` and/or `pipeline_name`, `audit_project_path`, `excluded_paths`, `audit_mode`) |

## Tips

- Save after **generating clusters** in cluster mode so `cluster` selections remain valid relative to stored metadata.
- Keep pipeline names short and filesystem-safe to avoid surprising sanitization.
