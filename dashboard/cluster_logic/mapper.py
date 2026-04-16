from __future__ import annotations

from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .parser import Tag, count_defs_by_name
from .solidity_parse_cache import extract_tags_from_repo_cached

TARGET_FILE_WEIGHT = 50.0
LONG_NAME_WEIGHT = 10.0
PRIVATE_NAME_WEIGHT = 0.1
GENERIC_NAME_WEIGHT = 0.1
PAGERANK_DAMPING = 0.85
PAGERANK_ITERATIONS = 20


def build_repomap(repo_path: str | Path, target_files: list[str] | None = None, parse_state: dict[str, Any] | None = None) -> dict[str, object]:
    root = Path(repo_path)
    tags = extract_tags_from_repo_cached(parse_state or {}, root)
    defs = [tag for tag in tags if tag.kind == "def"]
    refs = [tag for tag in tags if tag.kind == "ref"]
    rel_targets = _normalize_target_files(target_files or [], root)
    graph = build_dependency_graph(defs, refs, target_files=rel_targets)
    file_ranks = personalized_pagerank(graph, target_files=rel_targets, damping=PAGERANK_DAMPING, iterations=PAGERANK_ITERATIONS)
    return {"graph": graph, "file_ranks": file_ranks}


def build_dependency_graph(defs: Iterable[Tag], refs: Iterable[Tag], target_files: list[str] | None = None) -> dict[str, dict[str, float]]:
    target_set = set(target_files or [])
    defs = list(defs)
    refs = list(refs)
    defs_by_name: dict[str, list[Tag]] = defaultdict(list)
    for tag in defs:
        defs_by_name[tag.name].append(tag)
    def_counts = count_defs_by_name(defs)
    graph: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    for ref in refs:
        import_target = _resolve_import_ref(ref, {tag.rel_fname for tag in defs} | {tag.rel_fname for tag in refs})
        if import_target and import_target != ref.rel_fname:
            graph[ref.rel_fname][import_target] += TARGET_FILE_WEIGHT if import_target in target_set else 1.0
        for def_tag in defs_by_name.get(ref.name, []):
            if def_tag.rel_fname == ref.rel_fname:
                continue
            graph[ref.rel_fname][def_tag.rel_fname] += _edge_weight(def_tag, def_counts, target_set)

    all_nodes = {tag.rel_fname for tag in defs} | {tag.rel_fname for tag in refs}
    ref_names = {tag.name for tag in refs}
    for def_tag in defs:
        if def_tag.name not in ref_names:
            graph[def_tag.rel_fname][def_tag.rel_fname] += 0.1
    for node in all_nodes:
        graph.setdefault(node, {})
    return {src: dict(dsts) for src, dsts in graph.items()}


def personalized_pagerank(graph: dict[str, dict[str, float]], target_files: list[str] | None = None, damping: float = 0.85, iterations: int = 20) -> dict[str, float]:
    nodes = list(graph.keys())
    if not nodes:
        return {}
    target_set = set(target_files or [])
    base = 1.0 / len(nodes)
    personalization = {node: base for node in nodes}
    if target_set:
        boost = 100.0 / len(target_set)
        for node in target_set:
            if node in personalization:
                personalization[node] += boost
    total = sum(personalization.values()) or 1.0
    personalization = {node: value / total for node, value in personalization.items()}
    ranks = personalization.copy()
    for _ in range(iterations):
        updated = {node: (1.0 - damping) * personalization[node] for node in nodes}
        sink_rank = 0.0
        for src in nodes:
            outgoing = graph.get(src, {})
            if not outgoing:
                sink_rank += ranks[src]
                continue
            total_weight = sum(outgoing.values()) or 1.0
            for dst, weight in outgoing.items():
                updated[dst] += damping * ranks[src] * (weight / total_weight)
        if sink_rank:
            for node in nodes:
                updated[node] += damping * sink_rank * personalization[node]
        ranks = updated
    return ranks


def _normalize_target_files(target_files: list[str], root: Path) -> list[str]:
    normalized: list[str] = []
    for item in target_files:
        if not item:
            continue
        path = Path(item)
        if path.is_absolute():
            try:
                normalized.append(path.resolve().relative_to(root.resolve()).as_posix())
            except ValueError:
                normalized.append(path.name)
        else:
            normalized.append(PurePosixPath(item.replace("\\", "/")).as_posix())
    return normalized


def _resolve_import_ref(ref: Tag, known_files: set[str]) -> str | None:
    if "/" not in ref.name and "\\" not in ref.name and not ref.name.endswith(".sol"):
        return None
    target = (PurePosixPath(ref.rel_fname).parent / ref.name).as_posix()
    return target if target in known_files else None


def _edge_weight(def_tag: Tag, def_counts: dict[str, int], target_set: set[str]) -> float:
    name = def_tag.name or ""
    weight = 1.0
    if def_tag.rel_fname in target_set:
        weight += TARGET_FILE_WEIGHT
    if len(name) > 12:
        weight += LONG_NAME_WEIGHT
    if name.startswith("_"):
        weight += PRIVATE_NAME_WEIGHT
    if len(name) <= 2:
        weight += GENERIC_NAME_WEIGHT
    return weight / max(1, int(def_counts.get(name, 1)))
