from __future__ import annotations

from pathlib import Path
from typing import Any

from .parser import Tag, extract_tags_fallback, extract_tags_from_ast, parse_solidity_source
from .solidity_ir import build_fallback_ir, build_ir_from_ast

SOLIDITY_PROJECT_IR_SCHEMA_VERSION = 1


def empty_project_ir() -> dict[str, Any]:
    return {"schema_version": SOLIDITY_PROJECT_IR_SCHEMA_VERSION, "files": {}}


def _rel_posix(abs_path: Path, repo_root: Path) -> str:
    try:
        return abs_path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return abs_path.name


def parse_solidity_with_retries(source: str) -> tuple[dict[str, Any] | None, str | None]:
    last_err: str | None = None
    for _ in range(2):
        try:
            return parse_solidity_source(source), None
        except Exception as exc:  # pragma: no cover
            last_err = str(exc)
    return None, last_err


def build_file_record(abs_path: Path, repo_root: Path) -> dict[str, Any]:
    source = abs_path.read_text(encoding="utf-8")
    rel = _rel_posix(abs_path, repo_root)
    ast, err = parse_solidity_with_retries(source)
    if ast is None:
        return {"parse_status": "fallback", "parse_error": err, "ast": None, "ir": build_fallback_ir(source, rel, str(abs_path))}
    return {"parse_status": "ok", "parse_error": None, "ast": ast, "ir": build_ir_from_ast(ast, source, rel)}


def get_project_ir(state: dict[str, Any]) -> dict[str, Any]:
    root = state.get("solidity_project_ir")
    if isinstance(root, dict) and isinstance(root.get("files"), dict):
        return root
    fresh = empty_project_ir()
    state["solidity_project_ir"] = fresh
    return fresh


def ensure_file_in_index(state: dict[str, Any], abs_path: str | Path, repo_root: str | Path) -> None:
    ap = Path(abs_path).resolve()
    key = str(ap)
    proj = get_project_ir(state)
    if key in proj["files"]:
        return
    proj["files"][key] = build_file_record(ap, Path(repo_root).resolve())
    state["solidity_project_ir"] = proj


def get_file_record(state: dict[str, Any], abs_path: str | Path) -> dict[str, Any] | None:
    return get_project_ir(state).get("files", {}).get(str(Path(abs_path).resolve()))


def build_index_for_paths(state: dict[str, Any], repo_root: str | Path, paths: list[str]) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    for path in paths:
        if path:
            ensure_file_in_index(state, path, root)
    state["solidity_project_ir"] = get_project_ir(state)
    return state["solidity_project_ir"]


def extract_tags_from_repo_cached(state: dict[str, Any], repo_path: str | Path) -> list[Tag]:
    root = Path(repo_path).resolve()
    tags: list[Tag] = []
    for file_path in sorted(root.rglob("*.sol")):
        abs_path = str(file_path.resolve())
        ensure_file_in_index(state, abs_path, root)
        rec = get_file_record(state, abs_path)
        rel = file_path.relative_to(root).as_posix()
        if rec and rec.get("parse_status") == "ok" and rec.get("ast"):
            tags.extend(extract_tags_from_ast(rec["ast"], rel_fname=rel, fname=abs_path))
        else:
            source = file_path.read_text(encoding="utf-8")
            tags.extend(extract_tags_fallback(source, rel_fname=rel, fname=abs_path))
    return tags
