"""Backend tool layer for the chat agent.

The agent reaches into workspace files, docs RAG, resource catalogs and
configured external tools through these functions. Every callable here
performs its own workspace boundary check so the agent cannot leak data
from outside the vulnflow repository root.

External tool execution reuses
:func:`dashboard.pipeline.external_tools.execute_external_tool_call`
verbatim so behavior, auth handling and error semantics match the
existing pipeline runtime.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from dashboard.chat.rate_limit import RateLimiterRegistry
from dashboard.chat.schemas import UiContext
from dashboard.chat.security import (
    SecurityConfig,
    filter_directory_entries,
    is_blocked_path,
    load_security_config,
    scrub_secrets,
)
from dashboard.chat.skills import (
    SkillEntry,
    chat_skills_root,
    load_chat_skills,
    read_skill_text,
)
from dashboard.docs_rag import search_relevant_chunks
from dashboard.pipeline.external_tools import (
    build_provider_tool_schema,
    execute_external_tool_call,
    load_external_tools,
)


_TEXT_READ_SUFFIXES = {
    ".sol", ".js", ".jsx", ".ts", ".tsx", ".py", ".yaml", ".yml", ".json",
    ".md", ".txt", ".toml", ".cfg", ".ini", ".html", ".css", ".sh", ".bash",
    ".rs", ".go", ".java", ".c", ".cpp", ".h", ".hpp", ".cs", ".rb", ".php",
}


def _validate_inside(workspace_root: Path, path: str) -> Optional[Path]:
    try:
        resolved = Path(path).expanduser().resolve()
        resolved.relative_to(workspace_root)
    except (ValueError, OSError):
        return None
    return resolved


class ChatToolRegistry:
    """Concrete tool callables exposed to the chat agent.

    Each public ``call_*`` method matches a JSON schema declared in
    :meth:`tool_schemas`. The provider tool-call dispatch in
    :mod:`dashboard.chat.service` routes JSON tool calls to these.
    """

    def __init__(
        self,
        workspace_root: Path,
        conf_path: Path,
        ui_context: UiContext,
        security_config: SecurityConfig | None = None,
        rate_limiter: RateLimiterRegistry | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.conf_path = conf_path
        self.ui_context = ui_context
        self.security_config = security_config or load_security_config(self.conf_path)
        self._external_tools_cache = load_external_tools(self.conf_path)
        if rate_limiter is None:
            rate_limiter = RateLimiterRegistry()
            rate_limiter.configure_from_tools(self._external_tools_cache)
        self.rate_limiter = rate_limiter
        self._external_index: dict[str, dict[str, Any]] = {}
        for tool in self._external_tools_cache:
            for endpoint in tool.get("endpoints", []):
                fn_name = f"{tool['name']}_{endpoint['name']}"
                self._external_index[fn_name] = {"tool": tool, "endpoint": endpoint}

    # ------------------------------------------------------------------ schemas

    def tool_schemas(self, provider: str) -> list[dict[str, Any]]:
        """Return the full JSON-tool schema for the configured provider.

        Mirrors the OpenAI / Anthropic dual-shape used by
        :func:`dashboard.pipeline.external_tools.build_provider_tool_schema`.
        """

        builtins = [
            self._schema_read_workspace_file(),
            self._schema_list_workspace_directory(),
            self._schema_search_docs(),
            self._schema_list_resources(),
            self._schema_read_skill(),
            self._schema_plan_visualization(),
            self._schema_widget_renderer(),
            self._schema_emit_canvas_action(),
        ]

        if provider == "claude":
            wrapped = builtins
        else:
            wrapped = [
                {
                    "type": "function",
                    "function": {
                        "name": entry["name"],
                        "description": entry["description"],
                        "parameters": entry["input_schema"],
                    },
                }
                for entry in builtins
            ]

        # External tools (HornetMCP, Solodit, etc.)
        for tool in self._external_tools_cache:
            extra, _index = build_provider_tool_schema(
                provider=provider,
                selected_tool_name=tool["name"],
                conf_path=self.conf_path,
            )
            wrapped.extend(extra)

        return wrapped

    # ------------------------------------------------------------------ dispatch

    def is_known(self, name: str) -> bool:
        builtin_names = {
            "read_workspace_file",
            "list_workspace_directory",
            "search_docs",
            "list_resources",
            "read_skill",
            "plan_visualization",
            "widget_renderer",
            "emit_canvas_action",
        }
        return name in builtin_names or name in self._external_index

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        args = arguments or {}
        try:
            if name == "read_workspace_file":
                return self._call_read_workspace_file(args)
            if name == "list_workspace_directory":
                return self._call_list_workspace_directory(args)
            if name == "search_docs":
                return self._call_search_docs(args)
            if name == "list_resources":
                return self._call_list_resources(args)
            if name == "read_skill":
                return self._call_read_skill(args)
            if name == "plan_visualization":
                return self._call_plan_visualization(args)
            if name == "widget_renderer":
                return self._call_widget_renderer(args)
            if name == "emit_canvas_action":
                return self._call_emit_canvas_action(args)
            if name in self._external_index:
                return await self._call_external(name, args)
        except Exception as exc:
            return {"ok": False, "error": "tool_exception", "details": str(exc)}
        return {"ok": False, "error": "unknown_tool", "name": name}

    # ----------------------------------------------------------- workspace tools

    def _schema_read_workspace_file(self) -> dict[str, Any]:
        return {
            "name": "read_workspace_file",
            "description": (
                "Read a text file inside the vulnflow workspace or audit project. "
                "Paths must be inside the workspace root; absolute paths or paths "
                "that escape the workspace are rejected."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or workspace-relative path to a text file.",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Optional cap on returned characters (default 24000).",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        }

    def _call_read_workspace_file(self, args: dict[str, Any]) -> dict[str, Any]:
        raw_path = str(args.get("path", "")).strip()
        if not raw_path:
            return {"ok": False, "error": "missing_path"}
        if not Path(raw_path).is_absolute():
            raw_path = str((self.workspace_root / raw_path).resolve())
        target = _validate_inside(self.workspace_root, raw_path)
        if target is None:
            return {"ok": False, "error": "outside_workspace", "path": raw_path}
        if not target.exists() or not target.is_file():
            return {"ok": False, "error": "not_found", "path": str(target)}
        if is_blocked_path(target, self.security_config):
            return {
                "ok": False,
                "error": "blocked_by_secret_policy",
                "path": str(target),
                "hint": (
                    "File name matches a secret-bearing pattern (e.g. .env, "
                    "*.pem, *.key, secrets.*, id_rsa). Override via "
                    "conf.yaml::chat.secrets.allow_patterns if you really "
                    "need it."
                ),
            }
        if target.suffix.lower() not in _TEXT_READ_SUFFIXES:
            return {"ok": False, "error": "unsupported_extension", "path": str(target)}
        try:
            content = target.read_text(encoding="utf-8")
        except Exception as exc:
            return {"ok": False, "error": "read_failed", "details": str(exc), "path": str(target)}
        max_chars = int(args.get("max_chars") or 24000)
        truncated = False
        if len(content) > max_chars:
            content = content[:max_chars]
            truncated = True
        cleaned, redactions = scrub_secrets(content)
        return {
            "ok": True,
            "path": str(target),
            "rel_path": str(target.relative_to(self.workspace_root)),
            "content": cleaned,
            "truncated": truncated,
            "bytes": target.stat().st_size,
            "redactions": [r.to_dict() for r in redactions],
        }

    def _schema_list_workspace_directory(self) -> dict[str, Any]:
        return {
            "name": "list_workspace_directory",
            "description": (
                "List entries of a directory inside the vulnflow workspace or audit "
                "project. Returns child name, kind (file/directory) and size."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Workspace-relative or absolute directory path.",
                    },
                    "max_entries": {
                        "type": "integer",
                        "description": "Optional cap on returned entries (default 500).",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        }

    def _call_list_workspace_directory(self, args: dict[str, Any]) -> dict[str, Any]:
        raw_path = str(args.get("path", "")).strip() or "."
        if not Path(raw_path).is_absolute():
            raw_path = str((self.workspace_root / raw_path).resolve())
        target = _validate_inside(self.workspace_root, raw_path)
        if target is None:
            return {"ok": False, "error": "outside_workspace", "path": raw_path}
        if not target.exists() or not target.is_dir():
            return {"ok": False, "error": "not_a_directory", "path": str(target)}
        cap = max(1, int(args.get("max_entries") or 500))
        items: list[dict[str, Any]] = []
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if child.name.startswith("."):
                continue
            entry: dict[str, Any] = {
                "name": child.name,
                "rel_path": str(child.relative_to(self.workspace_root)),
                "kind": "directory" if child.is_dir() else "file",
            }
            if child.is_file():
                try:
                    entry["size"] = child.stat().st_size
                except OSError:
                    entry["size"] = 0
            items.append(entry)
            if len(items) >= cap:
                break
        items, hidden = filter_directory_entries(items, self.security_config)
        return {
            "ok": True,
            "path": str(target),
            "rel_path": str(target.relative_to(self.workspace_root)),
            "entries": items,
            "truncated": len(items) >= cap,
            "hidden_by_secret_policy": hidden,
        }

    # --------------------------------------------------------------- docs / RAG

    def _schema_search_docs(self) -> dict[str, Any]:
        return {
            "name": "search_docs",
            "description": (
                "Semantic search over the docs RAG index for documentation chunks "
                "relevant to one or more natural-language queries. Returns matched "
                "chunks with their source path and similarity."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "1–5 query strings.",
                    },
                    "top_k_per_query": {
                        "type": "integer",
                        "description": "Optional, default 3.",
                    },
                    "min_similarity": {
                        "type": "number",
                        "description": "Optional, default 0.7.",
                    },
                },
                "required": ["queries"],
                "additionalProperties": False,
            },
        }

    def _call_search_docs(self, args: dict[str, Any]) -> dict[str, Any]:
        raw_queries = args.get("queries", [])
        if isinstance(raw_queries, str):
            raw_queries = [raw_queries]
        queries = [str(q).strip() for q in raw_queries if str(q).strip()]
        if not queries:
            return {"ok": False, "error": "missing_queries"}
        top_k = int(args.get("top_k_per_query") or 3)
        min_sim = float(args.get("min_similarity") or 0.7)
        try:
            chunks = search_relevant_chunks(
                self.workspace_root,
                queries,
                top_k_per_query=top_k,
                min_similarity=min_sim,
            )
        except Exception as exc:
            return {"ok": False, "error": "docs_rag_failed", "details": str(exc)}
        return {"ok": True, "chunks": chunks, "query_count": len(queries)}

    # ------------------------------------------------------------------ catalogs

    def _schema_list_resources(self) -> dict[str, Any]:
        return {
            "name": "list_resources",
            "description": (
                "Return the current resource catalogs from ui_context "
                "(skills, lead_skills, patterns, memory, memory_promts, audit_docs, "
                "mcp) plus the list of available external tools. Use this before "
                "proposing canvas actions that bind nodes to skills / tools / files."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "section": {
                        "type": "string",
                        "description": (
                            "Optional. One of: skills, lead_skills, patterns, "
                            "memory, memory_promts, audit_docs, mcp, tools. "
                            "Defaults to returning everything."
                        ),
                    }
                },
                "additionalProperties": False,
            },
        }

    def _call_list_resources(self, args: dict[str, Any]) -> dict[str, Any]:
        section = str(args.get("section") or "").strip().lower()
        catalogs = {
            key: value.model_dump() for key, value in self.ui_context.catalogs.items()
        }
        tools = [tool.model_dump() for tool in self.ui_context.availableTools]
        if not section:
            return {"ok": True, "catalogs": catalogs, "tools": tools}
        if section == "tools":
            return {"ok": True, "tools": tools}
        if section in catalogs:
            return {"ok": True, "section": section, **catalogs[section]}
        return {"ok": False, "error": "unknown_section", "section": section}

    # ------------------------------------------------------------------- skills

    def _schema_read_skill(self) -> dict[str, Any]:
        return {
            "name": "read_skill",
            "description": (
                "Load the full text of a chat skill (SKILL.md) by name. The skill "
                "index is in the system prompt; call this to disclose the body."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name (directory or frontmatter name)."},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        }

    def _call_read_skill(self, args: dict[str, Any]) -> dict[str, Any]:
        name = str(args.get("name", "")).strip()
        if not name:
            return {"ok": False, "error": "missing_name"}
        body = read_skill_text(name, chat_skills_root())
        if body is None:
            available = [s.name for s in load_chat_skills()]
            return {"ok": False, "error": "skill_not_found", "name": name, "available": available}
        return {"ok": True, "name": name, "text": body}

    # ---------------------------------------------------------- plan + widget

    def _schema_plan_visualization(self) -> dict[str, Any]:
        return {
            "name": "plan_visualization",
            "description": (
                "Plan a visualization before building it. MUST be called before "
                "widget_renderer when producing any visual response. Outlines "
                "approach, technology choice, and key elements."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "approach": {"type": "string"},
                    "technology": {"type": "string"},
                    "key_elements": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["approach", "technology", "key_elements"],
                "additionalProperties": False,
            },
        }

    def _call_plan_visualization(self, args: dict[str, Any]) -> dict[str, Any]:
        approach = str(args.get("approach", "")).strip()
        technology = str(args.get("technology", "")).strip()
        raw_elements = args.get("key_elements", [])
        elements = [str(e).strip() for e in (raw_elements or []) if str(e).strip()]
        if not approach or not technology or not elements:
            return {"ok": False, "error": "incomplete_plan"}
        return {
            "ok": True,
            "approach": approach,
            "technology": technology,
            "key_elements": elements,
        }

    def _schema_widget_renderer(self) -> dict[str, Any]:
        return {
            "name": "widget_renderer",
            "description": (
                "Render an interactive HTML/SVG visualization inside the chat "
                "panel's sandboxed iframe. Provide a self-contained HTML fragment "
                "with inline <style> and <script>; the iframe shell already "
                "supplies CSS theme variables, prebuilt SVG color classes and an "
                "import map for three / gsap / d3 / chart.js."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "html": {"type": "string"},
                },
                "required": ["title", "html"],
                "additionalProperties": False,
            },
        }

    def _call_widget_renderer(self, args: dict[str, Any]) -> dict[str, Any]:
        title = str(args.get("title", "")).strip() or "Widget"
        description = str(args.get("description", "")).strip()
        html = str(args.get("html", ""))
        if not html.strip():
            return {"ok": False, "error": "missing_html"}
        return {
            "ok": True,
            "title": title,
            "description": description,
            "html": html,
            "html_length": len(html),
        }

    # --------------------------------------------------------- canvas actions

    def _schema_emit_canvas_action(self) -> dict[str, Any]:
        return {
            "name": "emit_canvas_action",
            "description": (
                "Emit a structural mutation against the ReactFlow canvas. "
                "Only valid when ui_context.currentTab == 'pipeline'. The action "
                "is validated against the current canvas state and resource "
                "catalogs; safe revisions auto-rebase, unsafe ones are rejected."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": [
                            "create_node",
                            "delete_node",
                            "update_node",
                            "create_edge",
                            "delete_edge",
                            "update_edge",
                            "set_viewport",
                        ],
                    },
                    "node_type": {"type": "string"},
                    "node_id": {"type": "string"},
                    "edge_id": {"type": "string"},
                    "source": {"type": "string"},
                    "target": {"type": "string"},
                    "sourceHandle": {"type": "string"},
                    "targetHandle": {"type": "string"},
                    "parentId": {"type": "string"},
                    "position": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "number"},
                            "y": {"type": "number"},
                        },
                    },
                    "viewport": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "number"},
                            "y": {"type": "number"},
                            "zoom": {"type": "number"},
                        },
                    },
                    "data": {"type": "object"},
                },
                "required": ["kind"],
                "additionalProperties": True,
            },
        }

    def _call_emit_canvas_action(self, args: dict[str, Any]) -> dict[str, Any]:
        # Actual validation/application is owned by dashboard.chat.canvas;
        # the service captures the call and routes it through that module.
        # This tool result acts as the "acknowledged" envelope from the model
        # while the canvas envelope part is appended separately.
        return {"ok": True, "queued": True, "kind": str(args.get("kind", ""))}

    # ----------------------------------------------------- external tool relay

    async def _call_external(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        endpoint_ref = self._external_index[name]
        ok, retry_after, tool_name = await self.rate_limiter.acquire(name, timeout=3.0)
        if not ok:
            return {
                "ok": False,
                "error": "rate_limited",
                "tool": tool_name or name,
                "retry_after": round(retry_after, 2),
            }
        result = await execute_external_tool_call(
            endpoint_ref=endpoint_ref,
            arguments=args,
        )
        return result


def serialize_arguments(payload: Any) -> str:
    """Helper for embedding tool-call arguments into chat history."""
    try:
        return json.dumps(payload, ensure_ascii=False)
    except Exception:
        return str(payload)
