from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .call_link_graph import build_function_call_clusters
from .mapper import build_repomap
from .solidity_parse_cache import build_index_for_paths

EXTERNAL_CALL_RE = re.compile(r"\.(?:call|delegatecall|staticcall|transfer|send)\s*(?:\{|\()")
PROXY_RE = re.compile(r"delegatecall|Proxy|Upgradeable")
ASSEMBLY_RE = re.compile(r"\bassembly\b")
PRAGMA_RE = re.compile(r"pragma solidity\s+([^;]+)")
IMPORT_RE = re.compile(r'import\s+"([^"]+)"')


def scan_solidity_file(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    src = file_path.read_text(encoding="utf-8")
    return {
        "loc": src.count("\n"),
        "external_calls": len(EXTERNAL_CALL_RE.findall(src)),
        "has_proxy": bool(PROXY_RE.search(src)),
        "has_assembly": bool(ASSEMBLY_RE.search(src)),
        "solidity_version": PRAGMA_RE.findall(src),
        "imports": IMPORT_RE.findall(src),
    }


def _is_excluded(path: Path, excluded: set[Path]) -> bool:
    return any(path == item or item in path.parents for item in excluded)


def _collect_sol_files(project_root: Path, excluded_paths: list[str]) -> list[str]:
    excluded = {Path(item).resolve() for item in excluded_paths if item}
    out: list[str] = []
    for file_path in project_root.rglob("*.sol"):
        resolved = file_path.resolve()
        if _is_excluded(resolved, excluded):
            continue
        out.append(str(resolved))
    return sorted(set(out))


def generate_clusters_for_project(project_root: Path, excluded_paths: list[str]) -> dict[str, Any]:
    sol_files = _collect_sol_files(project_root, excluded_paths)
    state: dict[str, Any] = {
        "repo_path": str(project_root.resolve()),
        "sol_files": sol_files,
        "pre_scan": {path: scan_solidity_file(path) for path in sol_files},
    }
    build_index_for_paths(state, project_root, sol_files)
    repomap_data = build_repomap(project_root, target_files=sol_files, parse_state=state)
    clusters, call_link_graph = build_function_call_clusters(state, repomap_data["graph"], repomap_data.get("file_ranks", {}))
    return {
        "solFiles": sol_files,
        "clusters": clusters,
        "clusterOptions": [cluster["cluster_id"] for cluster in clusters],
        "stats": {
            "solFiles": len(sol_files),
            "clusters": len(clusters),
            "functions": int(call_link_graph.get("stats", {}).get("function_count", 0) or 0),
            "edges": int(call_link_graph.get("stats", {}).get("edge_count", 0) or 0),
        },
        "callLinkGraph": call_link_graph,
    }
