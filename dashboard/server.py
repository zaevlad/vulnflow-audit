from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from dashboard.chat import register_chat_routes
from dashboard.cluster_logic import generate_clusters_for_project
from dashboard.docs_rag import get_docs_status, prepare_docs_index
from dashboard.pipeline.agent_runner import estimate_agent_request_costs
from dashboard.pipeline.external_tools import external_tool_catalog_entries
from dashboard.pipeline.engine import run_pipeline
from dashboard.pipeline.schemas import PipelineRun, RunStatus


ROOT_DIR = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = ROOT_DIR
DASHBOARD_DIR = Path(__file__).resolve().parent
DIST_DIR = DASHBOARD_DIR / "dist"
INDEX_HTML = DIST_DIR / "index.html"
CONF_PATH = ROOT_DIR / "conf.yaml"
PIPELINES_DIR = ROOT_DIR / "pipelines"

_MODEL_PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "chatgpt": {
        "base_url": "https://api.openai.com/v1",
        "env_key": "OPENAI_API_KEY",
        "requires_auth": True,
    },
    "claude": {
        "base_url": "https://api.anthropic.com/v1",
        "env_key": "ANTHROPIC_API_KEY",
        "requires_auth": True,
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "env_key": "OPENROUTER_API_KEY",
        "requires_auth": True,
    },
    "ollama": {
        "base_url": "http://localhost:11434",
        "env_key": "",
        "requires_auth": False,
    },
    "lmstudio": {
        "base_url": "http://localhost:1234/v1",
        "env_key": "",
        "requires_auth": False,
    },
    "llama_cpp": {
        "base_url": "http://localhost:8080/v1",
        "env_key": "",
        "requires_auth": False,
    },
}


class ProjectTreeRequest(BaseModel):
    """Build a file tree for the audited target folder (not the builder workspace)."""

    project_path: str
    excluded_paths: list[str] = Field(default_factory=list)


class AuditSelectRequest(BaseModel):
    """Optional path used as the folder dialog initial directory."""

    audit_path: str | None = None


class ClusterGenerateRequest(BaseModel):
    project_path: str
    excluded_paths: list[str] = Field(default_factory=list)


class PipelineSaveRequest(BaseModel):
    audit_project_path: str
    excluded_paths: list[str] = Field(default_factory=list)
    pipeline_name: str
    pipeline_data: dict[str, Any]


class MemoryCreateRequest(BaseModel):
    name: str


class PipelineRunRequest(BaseModel):
    pipeline_name: str = ""
    pipeline_data: dict[str, Any] | None = None
    audit_project_path: str = ""
    excluded_paths: list[str] = Field(default_factory=list)
    audit_mode: str = "contract"


class PipelineEstimateRequest(BaseModel):
    nodes: list[dict[str, Any]] = Field(default_factory=list)


class EditorOpenRequest(BaseModel):
    path: str


class EditorSaveRequest(BaseModel):
    path: str
    content: str


_active_runs: dict[str, PipelineRun] = {}
_run_events: dict[str, list[dict[str, Any]]] = {}
_run_tasks: dict[str, asyncio.Task[PipelineRun]] = {}
_ws_subscribers: dict[str, list[WebSocket]] = {}


def _resolve_project_root(project_path: str | None) -> Path:
    candidate = Path(project_path or ROOT_DIR).expanduser().resolve()
    if not candidate.exists() or not candidate.is_dir():
        raise HTTPException(status_code=400, detail="Project path does not exist.")
    return candidate


def _catalog_entries(base_dir: Path, *, suffixes: tuple[str, ...] | None = None) -> list[dict[str, str]]:
    if not base_dir.exists() or not base_dir.is_dir():
        return []
    items: list[dict[str, str]] = []
    for item in sorted(base_dir.iterdir(), key=lambda value: (not value.is_dir(), value.name.lower())):
        if item.name.startswith("."):
            continue
        if suffixes and item.is_file() and item.suffix.lower() not in suffixes:
            continue
        items.append(
            {
                "name": item.stem if item.is_file() else item.name,
                "path": str(item),
                "kind": "directory" if item.is_dir() else "file",
            }
        )
    return items


def discover_catalogs(project_root: Path) -> dict[str, Any]:
    return {
        "skills": {
            "path": str(project_root / "skills"),
            "entries": _catalog_entries(project_root / "skills", suffixes=(".md",)),
        },
        "lead_skills": {
            "path": str(project_root / "lead_skills"),
            "entries": _catalog_entries(project_root / "lead_skills", suffixes=(".md",)),
        },
        "mcp": {
            "path": str(CONF_PATH),
            "entries": external_tool_catalog_entries(CONF_PATH),
        },
        "audit_docs": {
            "path": str(project_root / "audit_docs"),
            "entries": _catalog_entries(project_root / "audit_docs", suffixes=(".md",)),
        },
        "patterns": {
            "path": str(project_root / "patterns"),
            "entries": _catalog_entries(project_root / "patterns", suffixes=(".yaml", ".yml")),
        },
        "memory": {
            "path": str(project_root / "memory"),
            "entries": _catalog_entries(project_root / "memory", suffixes=(".md",)),
        },
        "memory_promts": {
            "path": str(project_root / "memory_promts"),
            "entries": _catalog_entries(project_root / "memory_promts", suffixes=(".md",)),
        },
    }


def load_model_config() -> list[dict[str, Any]]:
    if not CONF_PATH.exists():
        return []
    data = yaml.safe_load(CONF_PATH.read_text(encoding="utf-8")) or {}
    models = data.get("models") if isinstance(data, dict) else {}
    if not isinstance(models, dict):
        return []
    out: list[dict[str, Any]] = []
    labels = {
        "chatgpt": "ChatGPT",
        "claude": "Claude",
        "openrouter": "OpenRouter",
        "ollama": "Ollama",
        "lmstudio": "LM Studio",
        "llama_cpp": "llama.cpp",
    }

    def _read_named_env(var_name: Any) -> str:
        name = str(var_name or "").strip()
        return os.environ.get(name, "") if name else ""

    for provider_id in ("chatgpt", "claude", "openrouter", "ollama", "lmstudio", "llama_cpp"):
        if provider_id not in models:
            continue
        section = models.get(provider_id, {})
        if not isinstance(section, dict):
            continue
        if not bool(section.get("enabled", True)):
            continue
        raw_models = section.get("models", [])
        provider_models = [item for item in raw_models if isinstance(item, str) and item.strip()]
        provider_defaults = _MODEL_PROVIDER_DEFAULTS.get(provider_id, {})
        requires_auth = bool(provider_defaults.get("requires_auth", False))

        api_key = str(section.get("api_key", "")).strip()
        if not api_key:
            api_key = _read_named_env(section.get("api_key_env", ""))
        if not api_key:
            api_key = _read_named_env(provider_defaults.get("env_key", ""))

        base_url = str(section.get("base_url", "")).strip()
        if not base_url:
            base_url = _read_named_env(section.get("base_url_env", ""))
        if not base_url:
            base_url = _read_named_env(f"{provider_id.upper()}_BASE_URL")
        if not base_url:
            base_url = str(provider_defaults.get("base_url", "")).strip()

        out.append(
            {
                "id": provider_id,
                "label": labels[provider_id],
                "enabled": bool(section.get("enabled", True)),
                "models": provider_models,
                "requiresAuth": requires_auth,
                "hasApiKey": bool(api_key) if requires_auth else True,
                "baseUrl": base_url.rstrip("/"),
            }
        )
    return out


def get_system_status() -> dict[str, Any]:
    return {
        "foundry": shutil.which("forge") is not None,
        "medusa": shutil.which("medusa") is not None,
        "configPath": str(CONF_PATH),
    }


def _path_is_excluded(path: Path, excluded: set[Path]) -> bool:
    return any(path == item or item in path.parents for item in excluded)


def build_file_tree(project_root: Path, excluded_paths: list[str]) -> dict[str, Any]:
    excluded = {Path(item).resolve() for item in excluded_paths if item}

    def walk(current: Path) -> dict[str, Any]:
        explicit = current in excluded
        inherited = _path_is_excluded(current, excluded)
        node = {
            "name": current.name or str(current),
            "path": str(current),
            "kind": "directory" if current.is_dir() else "file",
            "excluded": inherited,
            "explicitlyExcluded": explicit,
            "inaccessible": False,
            "children": [],
        }
        if current.is_dir():
            try:
                children = sorted(current.iterdir(), key=lambda value: (not value.is_dir(), value.name.lower()))
            except PermissionError:
                node["inaccessible"] = True
                children = []
            node["children"] = [walk(child) for child in children]
        return node

    return walk(project_root)


def _sanitize_pipeline_name(name: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "._-" else "-" for char in name).strip("-._")
    return cleaned or "pipeline"


def _sanitize_markdown_name(name: str) -> str:
    raw_name = Path((name or "").strip()).name
    stem = raw_name[:-3] if raw_name.lower().endswith(".md") else raw_name
    cleaned = "".join(char if char.isalnum() or char in "._-" else "-" for char in stem).strip("-._")
    return f"{cleaned or 'memory'}.md"


def create_memory_file(project_root: Path, name: str) -> Path:
    target_dir = project_root / "memory"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / _sanitize_markdown_name(name)
    if target.exists():
        raise HTTPException(status_code=409, detail="Memory file already exists.")
    target.write_text("", encoding="utf-8")
    return target


def list_pipelines(project_root: Path) -> list[dict[str, Any]]:
    target_dir = project_root / "pipelines"
    if not target_dir.exists():
        return []
    return [
        {"name": item.stem, "path": str(item)}
        for item in sorted(target_dir.glob("*.json"), key=lambda value: value.name.lower())
    ]


def save_pipeline(project_root: Path, pipeline_name: str, payload: dict[str, Any]) -> Path:
    target_dir = project_root / "pipelines"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{_sanitize_pipeline_name(pipeline_name)}.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def load_pipeline(project_root: Path, pipeline_name: str) -> dict[str, Any]:
    target = project_root / "pipelines" / f"{_sanitize_pipeline_name(pipeline_name)}.json"
    if not target.exists():
        raise HTTPException(status_code=404, detail="Pipeline not found.")
    return json.loads(target.read_text(encoding="utf-8"))


def _open_with_default_app(path: Path) -> bool:
    try:
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
            return True
        if shutil.which("open"):
            subprocess.Popen(["open", str(path)])
            return True
        if shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", str(path)])
            return True
    except Exception:
        return False
    return False


def pick_project_folder(initial_dir: str | None = None) -> str | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Folder picker unavailable: {exc}") from exc
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected = filedialog.askdirectory(initialdir=initial_dir or str(ROOT_DIR), mustexist=True)
    finally:
        root.destroy()
    return selected or None


_EXT_TO_LANGUAGE: dict[str, str] = {
    ".sol": "sol",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".py": "python",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".md": "markdown",
    ".txt": "plaintext",
    ".toml": "ini",
    ".cfg": "ini",
    ".ini": "ini",
    ".html": "html",
    ".css": "css",
    ".sh": "shell",
    ".bash": "shell",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
}

_TEXT_EXTENSIONS: frozenset[str] = frozenset(_EXT_TO_LANGUAGE.keys())


def _validate_editor_path(path: str) -> Path:
    try:
        resolved = Path(path).expanduser().resolve()
        resolved.relative_to(WORKSPACE_ROOT)
    except (ValueError, Exception):
        raise HTTPException(status_code=400, detail="Path is outside workspace.")
    return resolved


def _detect_language(path: Path) -> str:
    return _EXT_TO_LANGUAGE.get(path.suffix.lower(), "plaintext")


def create_app() -> FastAPI:
    app = FastAPI(title="VulnFlow Builder")
    assets_dir = DIST_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/")
    def index() -> HTMLResponse:
        if not INDEX_HTML.exists():
            return HTMLResponse(
                "<html><body style='background:#020617;color:#e2e8f0;font-family:Segoe UI;padding:24px'>"
                "<h1>Dashboard UI is not built.</h1><p>Run <code>npm install</code> and <code>npm run build</code> in <code>dashboard/ui</code>.</p>"
                "</body></html>",
                status_code=503,
            )
        return HTMLResponse(INDEX_HTML.read_text(encoding="utf-8"))

    @app.get("/api/bootstrap")
    def bootstrap() -> dict[str, Any]:
        workspace_root = _resolve_project_root(str(WORKSPACE_ROOT))
        return {
            "workspacePath": str(workspace_root),
            "auditPath": str(workspace_root),
            "providers": load_model_config(),
            "catalogs": discover_catalogs(workspace_root),
            "docs": get_docs_status(workspace_root),
            "system": get_system_status(),
            "pipelines": list_pipelines(workspace_root),
            "fileTree": build_file_tree(workspace_root, []),
        }

    @app.post("/api/project/select")
    def select_project(payload: AuditSelectRequest) -> dict[str, Any]:
        selected = pick_project_folder(payload.audit_path)
        if not selected:
            return {"selected": False}
        audit_root = _resolve_project_root(selected)
        return {
            "selected": True,
            "auditPath": str(audit_root),
            "fileTree": build_file_tree(audit_root, []),
        }

    @app.post("/api/project/tree")
    def project_tree(payload: ProjectTreeRequest) -> dict[str, Any]:
        audit_root = _resolve_project_root(payload.project_path)
        return {"auditPath": str(audit_root), "fileTree": build_file_tree(audit_root, payload.excluded_paths)}

    @app.post("/api/clusters/generate")
    def generate_clusters(payload: ClusterGenerateRequest) -> dict[str, Any]:
        audit_root = _resolve_project_root(payload.project_path)
        result = generate_clusters_for_project(audit_root, payload.excluded_paths)
        return {
            "auditPath": str(audit_root),
            "excludedPaths": list(payload.excluded_paths),
            **result,
        }

    @app.post("/api/config/open")
    def open_config() -> dict[str, Any]:
        return {"ok": _open_with_default_app(CONF_PATH), "path": str(CONF_PATH)}

    @app.get("/api/settings/providers")
    def settings_providers() -> dict[str, Any]:
        return {"providers": load_model_config()}

    @app.get("/api/project/catalogs")
    def project_catalogs() -> dict[str, Any]:
        workspace_root = _resolve_project_root(str(WORKSPACE_ROOT))
        return {
            "workspacePath": str(workspace_root),
            "catalogs": discover_catalogs(workspace_root),
            "docs": get_docs_status(workspace_root),
            "pipelines": list_pipelines(workspace_root),
            "system": get_system_status(),
        }

    @app.get("/api/docs/status")
    def docs_status() -> dict[str, Any]:
        workspace_root = _resolve_project_root(str(WORKSPACE_ROOT))
        return {"docs": get_docs_status(workspace_root)}

    @app.post("/api/docs/prepare")
    def docs_prepare() -> dict[str, Any]:
        workspace_root = _resolve_project_root(str(WORKSPACE_ROOT))
        return prepare_docs_index(workspace_root)

    @app.post("/api/memory/create")
    def api_create_memory_file(payload: MemoryCreateRequest) -> dict[str, Any]:
        workspace_root = _resolve_project_root(str(WORKSPACE_ROOT))
        created = create_memory_file(workspace_root, payload.name)
        return {
            "ok": True,
            "path": str(created),
            "catalogs": discover_catalogs(workspace_root),
        }

    @app.post("/api/pipelines/save")
    def api_save_pipeline(payload: PipelineSaveRequest) -> dict[str, Any]:
        workspace_root = _resolve_project_root(str(WORKSPACE_ROOT))
        audit_root = _resolve_project_root(payload.audit_project_path)
        merged: dict[str, Any] = dict(payload.pipeline_data)
        merged["projectPath"] = str(audit_root)
        merged["excludedPaths"] = list(payload.excluded_paths)
        saved = save_pipeline(workspace_root, payload.pipeline_name, merged)
        return {"ok": True, "savedPath": str(saved), "savedName": saved.stem, "pipelines": list_pipelines(workspace_root)}

    @app.get("/api/pipelines/{pipeline_name}")
    def api_load_pipeline(pipeline_name: str) -> dict[str, Any]:
        workspace_root = _resolve_project_root(str(WORKSPACE_ROOT))
        return {"pipeline": load_pipeline(workspace_root, pipeline_name)}

    @app.post("/api/pipeline/estimate")
    async def api_estimate_pipeline(payload: PipelineEstimateRequest) -> dict[str, Any]:
        workspace_root = _resolve_project_root(str(WORKSPACE_ROOT))
        tool_by_parent: dict[str, dict[str, Any]] = {}
        for node in payload.nodes:
            if node.get("type") != "tool":
                continue
            data = node.get("data") or {}
            if not isinstance(data, dict):
                continue
            parent_id = str(data.get("parentAgentId") or node.get("parentId") or "").strip()
            if parent_id:
                tool_by_parent[parent_id] = dict(data)

        estimates: dict[str, Any] = {}
        for node in payload.nodes:
            node_type = node.get("type") or "agent"
            if node_type != "agent":
                continue
            node_id = str(node.get("id", "")).strip()
            data = node.get("data") or {}
            if not node_id or not isinstance(data, dict):
                continue
            node_payload = dict(data)
            tool_payload = tool_by_parent.get(node_id)
            if tool_payload:
                node_payload["_tool_node_data"] = tool_payload
            estimates[node_id] = await estimate_agent_request_costs(
                node_data=node_payload,
                workspace_root=workspace_root,
            )

        return {"estimates": estimates}

    # ------------------------------------------------------------------
    # Pipeline execution endpoints
    # ------------------------------------------------------------------

    async def _broadcast_event(run_id: str, event: dict[str, Any]) -> None:
        _run_events.setdefault(run_id, []).append(event)
        dead: list[WebSocket] = []
        for ws in _ws_subscribers.get(run_id, []):
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            _ws_subscribers.get(run_id, []).remove(ws) if ws in _ws_subscribers.get(run_id, []) else None

    @app.post("/api/pipeline/run")
    async def api_run_pipeline(payload: PipelineRunRequest) -> dict[str, Any]:
        workspace_root = _resolve_project_root(str(WORKSPACE_ROOT))
        if payload.pipeline_data is not None:
            pipeline_data = dict(payload.pipeline_data)
        elif payload.pipeline_name:
            pipeline_data = load_pipeline(workspace_root, payload.pipeline_name)
        else:
            raise HTTPException(status_code=400, detail="Pipeline data is required.")

        if payload.audit_project_path:
            pipeline_data["projectPath"] = payload.audit_project_path
        pipeline_data["excludedPaths"] = list(payload.excluded_paths)
        pipeline_data["auditMode"] = payload.audit_mode

        stub = PipelineRun(pipeline_name=payload.pipeline_name, status=RunStatus.pending)
        if not stub.pipeline_name:
            stub.pipeline_name = str(pipeline_data.get("name", "current-canvas"))
        _active_runs[stub.run_id] = stub
        _run_events[stub.run_id] = []

        async def _wrapped() -> PipelineRun:
            try:
                run_result = await run_pipeline(
                    pipeline_data=pipeline_data,
                    workspace_root=str(workspace_root),
                    on_event=lambda evt: _broadcast_event(stub.run_id, evt),
                )
                run_result.run_id = stub.run_id
                _active_runs[stub.run_id] = run_result
                return run_result
            except Exception as exc:
                stub.status = RunStatus.failed
                _active_runs[stub.run_id] = stub
                raise

        task = asyncio.create_task(_wrapped())
        _run_tasks[stub.run_id] = task

        return {"ok": True, "run_id": stub.run_id}

    @app.get("/api/pipeline/status/{run_id}")
    def api_pipeline_status(run_id: str) -> dict[str, Any]:
        run = _active_runs.get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found.")
        return {
            "run_id": run.run_id,
            "pipeline_name": run.pipeline_name,
            "status": run.status.value,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "block_log": [r.model_dump() for r in run.global_context.block_log],
            "branches": {k: v.model_dump() for k, v in run.global_context.branches.items()},
            "events": _run_events.get(run_id, []),
        }

    @app.post("/api/pipeline/stop/{run_id}")
    def api_pipeline_stop(run_id: str) -> dict[str, Any]:
        task = _run_tasks.get(run_id)
        if not task:
            raise HTTPException(status_code=404, detail="Run not found or already finished.")
        task.cancel()
        run = _active_runs.get(run_id)
        if run:
            run.status = RunStatus.stopped
        return {"ok": True, "run_id": run_id, "status": "stopped"}

    @app.websocket("/ws/pipeline/{run_id}")
    async def ws_pipeline(websocket: WebSocket, run_id: str) -> None:
        await websocket.accept()
        _ws_subscribers.setdefault(run_id, []).append(websocket)

        for past_event in _run_events.get(run_id, []):
            try:
                await websocket.send_json(past_event)
            except Exception:
                break

        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            subs = _ws_subscribers.get(run_id, [])
            if websocket in subs:
                subs.remove(websocket)

    # ------------------------------------------------------------------
    # Editor endpoints
    # ------------------------------------------------------------------

    @app.post("/api/editor/open")
    def api_editor_open(payload: EditorOpenRequest) -> dict[str, Any]:
        file_path = _validate_editor_path(payload.path)
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="File not found.")
        if not file_path.is_file():
            raise HTTPException(status_code=400, detail="Path is not a file.")
        if file_path.suffix.lower() not in _TEXT_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"Unsupported file type: {file_path.suffix or '(no extension)'}")
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"File is not readable: {exc}") from exc
        return {
            "ok": True,
            "path": str(file_path),
            "rel_path": str(file_path.relative_to(WORKSPACE_ROOT)),
            "content": content,
            "language": _detect_language(file_path),
        }

    @app.post("/api/editor/save")
    def api_editor_save(payload: EditorSaveRequest) -> dict[str, Any]:
        file_path = _validate_editor_path(payload.path)
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="File not found.")
        if not file_path.is_file():
            raise HTTPException(status_code=400, detail="Path is not a file.")
        if file_path.suffix.lower() not in _TEXT_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"Unsupported file type: {file_path.suffix or '(no extension)'}")
        try:
            file_path.write_text(payload.content, encoding="utf-8")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Cannot write file: {exc}") from exc
        return {"ok": True, "path": str(file_path), "size": file_path.stat().st_size}

    @app.post("/api/editor/reload")
    def api_editor_reload(payload: EditorOpenRequest) -> dict[str, Any]:
        file_path = _validate_editor_path(payload.path)
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="File not found.")
        if not file_path.is_file():
            raise HTTPException(status_code=400, detail="Path is not a file.")
        if file_path.suffix.lower() not in _TEXT_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"Unsupported file type: {file_path.suffix or '(no extension)'}")
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"File is not readable: {exc}") from exc
        return {
            "ok": True,
            "path": str(file_path),
            "rel_path": str(file_path.relative_to(WORKSPACE_ROOT)),
            "content": content,
            "language": _detect_language(file_path),
        }

    register_chat_routes(app, workspace_root=WORKSPACE_ROOT, conf_path=CONF_PATH)

    return app


app = create_app()
