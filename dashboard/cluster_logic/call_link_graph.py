from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from .solidity_parse_cache import ensure_file_in_index, get_file_record


def _walk_ast(node: Any, visit: Any) -> None:
    if isinstance(node, list):
        for item in node:
            _walk_ast(item, visit)
        return
    if isinstance(node, dict):
        visit(node)
        for key, val in node.items():
            if key == "loc":
                continue
            _walk_ast(val, visit)


def _collect_import_directives(ast: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def visitor(node: dict[str, Any]) -> None:
        if node.get("type") == "ImportDirective":
            out.append(node)

    _walk_ast(ast, visitor)
    return out


def _symbol_name_from_foreign(foreign: Any) -> str | None:
    if isinstance(foreign, dict) and foreign.get("type") == "Identifier":
        return str(foreign.get("name") or "")
    if isinstance(foreign, str):
        return foreign
    return None


def _resolve_import_path(root: Path, source_file: Path, imported: str) -> Path | None:
    candidate = (source_file.parent / imported).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() and candidate.suffix == ".sol" else None


def _build_import_edges_for_file(root: Path, abs_path: str, ast: dict[str, Any]) -> list[tuple[str, str]]:
    fp = Path(abs_path).resolve()
    try:
        src_rel = fp.relative_to(root).as_posix()
    except ValueError:
        return []
    out: list[tuple[str, str]] = []
    for imp in _collect_import_directives(ast):
        path_lit = imp.get("path")
        if not isinstance(path_lit, str):
            continue
        tgt = _resolve_import_path(root, fp, path_lit)
        if not tgt:
            continue
        try:
            dst_rel = tgt.resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        if dst_rel != src_rel:
            out.append((src_rel, dst_rel))
    return out


def _registry_from_state(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    reg: dict[str, dict[str, Any]] = {}
    root = Path(state["repo_path"]).resolve()
    for abs_path in state.get("sol_files") or []:
        if not abs_path:
            continue
        ensure_file_in_index(state, abs_path, root)
        rec = get_file_record(state, abs_path)
        if not rec or not isinstance(rec.get("ir"), dict):
            continue
        try:
            rel = Path(abs_path).resolve().relative_to(root).as_posix()
        except ValueError:
            rel = Path(abs_path).name
        for contract in rec["ir"].get("contracts") or []:
            cname = contract.get("name") or ""
            for fname, fdef in (contract.get("functions") or {}).items():
                if not isinstance(fdef, dict):
                    continue
                qid = fdef.get("qualified_id")
                if not isinstance(qid, str) or not qid:
                    continue
                reg[qid] = {
                    "rel_path": rel,
                    "abs_path": str(Path(abs_path).resolve()),
                    "contract": cname,
                    "name": str(fdef.get("name") or fname),
                    "line": fdef.get("line"),
                    "source": fdef.get("source"),
                    "node_type": fdef.get("node_type") or "function",
                    "contract_kind": contract.get("kind") or "contract",
                    "parameters": list(fdef.get("parameters") or []),
                    "locals": list(fdef.get("locals") or []),
                    "state_variables": list(fdef.get("state_variables") or contract.get("state_variables") or []),
                }
    return reg


def _contracts_by_file_rel(state: dict[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    root = Path(state["repo_path"]).resolve()
    for abs_path in state.get("sol_files") or []:
        if not abs_path:
            continue
        rec = get_file_record(state, abs_path)
        if not rec or not isinstance(rec.get("ir"), dict):
            continue
        try:
            rel = Path(abs_path).resolve().relative_to(root).as_posix()
        except ValueError:
            rel = Path(abs_path).name
        for contract in rec["ir"].get("contracts") or []:
            name = contract.get("name")
            if name:
                out[rel].append(str(name))
    return dict(out)


def _bases_by_contract(state: dict[str, Any]) -> dict[tuple[str, str], list[str]]:
    out: dict[tuple[str, str], list[str]] = {}
    root = Path(state["repo_path"]).resolve()
    for abs_path in state.get("sol_files") or []:
        if not abs_path:
            continue
        rec = get_file_record(state, abs_path)
        if not rec or rec.get("parse_status") != "ok" or not rec.get("ast"):
            continue
        try:
            rel = Path(abs_path).resolve().relative_to(root).as_posix()
        except ValueError:
            rel = Path(abs_path).name
        children = rec["ast"].get("children") if isinstance(rec["ast"], dict) else None
        if not isinstance(children, list):
            continue
        for top in children:
            if not isinstance(top, dict) or top.get("type") != "ContractDefinition":
                continue
            cname = top.get("name") or ""
            base_names: list[str] = []
            for base in top.get("baseContracts") or []:
                if not isinstance(base, dict):
                    continue
                base_name = base.get("baseName")
                if isinstance(base_name, dict):
                    name_path = base_name.get("namePath")
                    if isinstance(name_path, str):
                        base_names.append(name_path.split(".")[-1])
                    elif isinstance(name_path, list) and name_path:
                        base_names.append(str(name_path[-1]))
                elif isinstance(base_name, str):
                    base_names.append(base_name.split(".")[-1])
            if cname:
                out[(rel, str(cname))] = base_names
    return out


def _import_symbol_map(state: dict[str, Any]) -> dict[str, dict[str, tuple[str, str | None]]]:
    root = Path(state["repo_path"]).resolve()
    contracts_by_file = _contracts_by_file_rel(state)
    sym_map: dict[str, dict[str, tuple[str, str | None]]] = defaultdict(dict)
    for abs_path in state.get("sol_files") or []:
        if not abs_path:
            continue
        rec = get_file_record(state, abs_path)
        if not rec or rec.get("parse_status") != "ok" or not rec.get("ast"):
            continue
        try:
            src_rel = Path(abs_path).resolve().relative_to(root).as_posix()
        except ValueError:
            src_rel = Path(abs_path).name
        fp = Path(abs_path).resolve()
        for imp in _collect_import_directives(rec["ast"]):
            path_lit = imp.get("path")
            if not isinstance(path_lit, str):
                continue
            tgt = _resolve_import_path(root, fp, path_lit)
            if not tgt:
                continue
            try:
                dst_rel = tgt.resolve().relative_to(root).as_posix()
            except ValueError:
                continue
            contracts_in_dst = contracts_by_file.get(dst_rel, [])
            unit_alias = imp.get("unitAlias")
            syms = imp.get("symbolAliases")
            if unit_alias and isinstance(unit_alias, str):
                guess = contracts_in_dst[0] if len(contracts_in_dst) == 1 else None
                sym_map[src_rel][unit_alias] = (dst_rel, guess)
            if isinstance(syms, list):
                for pair in syms:
                    if not isinstance(pair, dict):
                        continue
                    fn = _symbol_name_from_foreign(pair.get("foreign"))
                    if not fn:
                        continue
                    local = pair.get("local")
                    alias = local if isinstance(local, str) and local else fn
                    guess = fn if fn in contracts_in_dst else (contracts_in_dst[0] if len(contracts_in_dst) == 1 else None)
                    sym_map[src_rel][alias] = (dst_rel, fn if fn in contracts_in_dst else guess)
            if not syms and not unit_alias:
                for cname in contracts_in_dst:
                    sym_map[src_rel][cname] = (dst_rel, cname)
    return dict(sym_map)


def _clean_symbol_name(name: str | None) -> str:
    if not isinstance(name, str):
        return ""
    clean = name.strip()
    if not clean or clean.startswith("mapping("):
        return ""
    if "[" in clean:
        clean = clean.split("[", 1)[0]
    if "." in clean:
        clean = clean.split(".")[-1]
    return clean.strip()


def _functions_by_contract(registry: dict[str, dict[str, Any]]) -> dict[tuple[str, str], dict[str, list[str]]]:
    out: dict[tuple[str, str], dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for qid, meta in registry.items():
        rel = meta.get("rel_path")
        contract = meta.get("contract")
        name = meta.get("name")
        if isinstance(rel, str) and isinstance(contract, str) and isinstance(name, str) and name:
            out[(rel, contract)][name].append(qid)
    return {key: {fname: sorted(qids) for fname, qids in names.items()} for key, names in out.items()}


def _contracts_by_name(registry: dict[str, dict[str, Any]]) -> dict[str, list[tuple[str, str]]]:
    out: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for meta in registry.values():
        rel = meta.get("rel_path")
        contract = meta.get("contract")
        if isinstance(rel, str) and isinstance(contract, str) and contract:
            out[contract].add((rel, contract))
    return {name: sorted(items) for name, items in out.items()}


def _implementers_by_base(bases_map: dict[tuple[str, str], list[str]], contracts_by_name: dict[str, list[tuple[str, str]]]) -> dict[str, list[tuple[str, str]]]:
    out: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for (rel, contract), bases in bases_map.items():
        if not rel or not contract:
            continue
        for base_name in bases:
            clean = _clean_symbol_name(base_name)
            if clean:
                out[clean].add((rel, contract))
    return {name: sorted(items) for name, items in out.items()}


def _scope_type_map(meta: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for bucket in ("state_variables", "parameters", "locals"):
        for item in meta.get(bucket) or []:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            type_name = _clean_symbol_name(item.get("type"))
            if isinstance(name, str) and name and type_name:
                out[name] = type_name
    return out


def _pick_from_candidates(candidates: list[str], *, registry: dict[str, dict[str, Any]], prefer_node_type: str | None = None) -> str | None:
    if not candidates:
        return None
    ranked = sorted(
        set(candidates),
        key=lambda qid: (
            1 if prefer_node_type and registry.get(qid, {}).get("node_type") == prefer_node_type else 0,
            qid,
        ),
        reverse=True,
    )
    return ranked[0] if ranked else None


def _find_function_qid(name: str, *, prefer_rel: str | None, prefer_contract: str | None, registry: dict[str, dict[str, Any]], prefer_node_type: str | None = None) -> str | None:
    candidates = [qid for qid, meta in registry.items() if meta.get("name") == name]
    if not candidates:
        return None
    if prefer_rel and prefer_contract:
        candidates.sort(
            key=lambda q: (
                2 if registry[q].get("rel_path") == prefer_rel and registry[q].get("contract") == prefer_contract else 1 if registry[q].get("rel_path") == prefer_rel else 0,
                q,
            ),
            reverse=True,
        )
        best = candidates[0]
        if registry[best].get("rel_path") == prefer_rel and registry[best].get("contract") == prefer_contract:
            picked = _pick_from_candidates([best], registry=registry, prefer_node_type=prefer_node_type)
            if picked:
                return picked
    if len(candidates) == 1:
        return _pick_from_candidates(candidates, registry=registry, prefer_node_type=prefer_node_type)
    if prefer_rel:
        rel_hits = [q for q in candidates if registry[q].get("rel_path") == prefer_rel]
        if len(rel_hits) == 1:
            return _pick_from_candidates(rel_hits, registry=registry, prefer_node_type=prefer_node_type)
    return _pick_from_candidates(candidates, registry=registry, prefer_node_type=prefer_node_type)


def _resolve_contract_function(
    targets: list[tuple[str, str]],
    function_name: str,
    *,
    registry: dict[str, dict[str, Any]],
    functions_by_contract: dict[tuple[str, str], dict[str, list[str]]],
    prefer_node_type: str | None = None,
) -> str | None:
    for target in targets:
        qids = list((functions_by_contract.get(target) or {}).get(function_name) or [])
        picked = _pick_from_candidates(qids, registry=registry, prefer_node_type=prefer_node_type)
        if picked:
            return picked
    return None


def _base_targets(
    rel: str,
    contract: str,
    *,
    bases_map: dict[tuple[str, str], list[str]],
    sym_map: dict[str, dict[str, tuple[str, str | None]]],
    contracts_by_name: dict[str, list[tuple[str, str]]],
    seen: set[tuple[str, str]] | None = None,
) -> list[tuple[str, str]]:
    seen = seen or set()
    key = (rel, contract)
    if key in seen:
        return []
    seen.add(key)
    out: list[tuple[str, str]] = []
    imported = sym_map.get(rel, {})
    for base_name in bases_map.get(key, []):
        clean = _clean_symbol_name(base_name)
        if not clean:
            continue
        if clean in imported:
            target_rel, guessed_contract = imported[clean]
            if guessed_contract:
                target = (target_rel, guessed_contract)
                if target not in out:
                    out.append(target)
            else:
                for candidate in contracts_by_name.get(clean, []):
                    if candidate[0] == target_rel and candidate not in out:
                        out.append(candidate)
        for candidate in contracts_by_name.get(clean, []):
            if candidate not in out:
                out.append(candidate)
    nested: list[tuple[str, str]] = []
    for target_rel, target_contract in out:
        for child in _base_targets(
            target_rel,
            target_contract,
            bases_map=bases_map,
            sym_map=sym_map,
            contracts_by_name=contracts_by_name,
            seen=seen,
        ):
            if child not in out and child not in nested:
                nested.append(child)
    return out + nested


def _receiver_contract_targets(
    rel: str,
    receiver_type: str | None,
    *,
    sym_map: dict[str, dict[str, tuple[str, str | None]]],
    contracts_by_name: dict[str, list[tuple[str, str]]],
    implementers_by_base: dict[str, list[tuple[str, str]]],
    registry: dict[str, dict[str, Any]],
) -> list[tuple[str, str]]:
    clean = _clean_symbol_name(receiver_type)
    if not clean:
        return []
    out: list[tuple[str, str]] = []
    imported = sym_map.get(rel, {})
    if clean in imported:
        target_rel, guessed_contract = imported[clean]
        if guessed_contract:
            out.append((target_rel, guessed_contract))
        else:
            for candidate in contracts_by_name.get(clean, []):
                if candidate[0] == target_rel and candidate not in out:
                    out.append(candidate)
    for candidate in contracts_by_name.get(clean, []):
        if candidate not in out:
            out.append(candidate)
    direct_kinds = {registry[qid].get("contract_kind") for qid, meta in registry.items() if meta.get("contract") == clean}
    implementers = list(implementers_by_base.get(clean, []))
    if direct_kinds & {"interface", "abstract"} and len(implementers) == 1:
        impl = implementers[0]
        out = [impl] + [item for item in out if item != impl]
    elif not out:
        out.extend(implementers)
    return out


def _resolve_via_bases(
    rel: str,
    contract: str,
    function_name: str,
    *,
    registry: dict[str, dict[str, Any]],
    functions_by_contract: dict[tuple[str, str], dict[str, list[str]]],
    bases_map: dict[tuple[str, str], list[str]],
    sym_map: dict[str, dict[str, tuple[str, str | None]]],
    contracts_by_name: dict[str, list[tuple[str, str]]],
    prefer_node_type: str | None = None,
) -> str | None:
    targets = _base_targets(rel, contract, bases_map=bases_map, sym_map=sym_map, contracts_by_name=contracts_by_name)
    return _resolve_contract_function(targets, function_name, registry=registry, functions_by_contract=functions_by_contract, prefer_node_type=prefer_node_type)


def _resolve_call_target(
    caller_qid: str,
    call: dict[str, Any],
    registry: dict[str, dict[str, Any]],
    sym_map: dict[str, dict[str, tuple[str, str | None]]],
    bases_map: dict[tuple[str, str], list[str]],
    functions_by_contract: dict[tuple[str, str], dict[str, list[str]]],
    contracts_by_name: dict[str, list[tuple[str, str]]],
    implementers_by_base: dict[str, list[tuple[str, str]]],
) -> str | None:
    rt = call.get("resolved_target")
    if isinstance(rt, str) and rt in registry:
        return rt
    callee_raw = (call.get("callee_raw") or "").strip()
    kind = call.get("call_kind") or ""
    caller_meta = registry.get(caller_qid)
    if not caller_meta:
        return None
    rel = caller_meta.get("rel_path") or ""
    contract = caller_meta.get("contract") or ""
    scope_types = _scope_type_map(caller_meta)
    if kind in ("internal_name", "this_qualified"):
        fallback_name = callee_raw.split(".")[-1] if "." in callee_raw else callee_raw
        name = _clean_symbol_name(call.get("member_name") or fallback_name)
        if name:
            got = _find_function_qid(name, prefer_rel=rel, prefer_contract=contract, registry=registry)
            if got:
                return got
            base_hit = _resolve_via_bases(
                rel,
                contract,
                name,
                registry=registry,
                functions_by_contract=functions_by_contract,
                bases_map=bases_map,
                sym_map=sym_map,
                contracts_by_name=contracts_by_name,
            )
            if base_hit:
                return base_hit
    if kind == "modifier":
        name = _clean_symbol_name(call.get("member_name") or callee_raw)
        if name:
            got = _find_function_qid(name, prefer_rel=rel, prefer_contract=contract, registry=registry, prefer_node_type="modifier")
            if got:
                return got
            base_hit = _resolve_via_bases(
                rel,
                contract,
                name,
                registry=registry,
                functions_by_contract=functions_by_contract,
                bases_map=bases_map,
                sym_map=sym_map,
                contracts_by_name=contracts_by_name,
                prefer_node_type="modifier",
            )
            if base_hit:
                return base_hit
    if kind == "super":
        fallback_name = callee_raw.split(".")[-1] if "." in callee_raw else callee_raw
        fn = _clean_symbol_name(call.get("member_name") or fallback_name)
        if fn:
            base_hit = _resolve_via_bases(
                rel,
                contract,
                fn,
                registry=registry,
                functions_by_contract=functions_by_contract,
                bases_map=bases_map,
                sym_map=sym_map,
                contracts_by_name=contracts_by_name,
            )
            if base_hit:
                return base_hit
    if kind in ("member", "typed_member"):
        function_name = _clean_symbol_name(call.get("member_name") or (callee_raw.rsplit(".", 1)[1] if "." in callee_raw else callee_raw))
        receiver_type = _clean_symbol_name(call.get("receiver_type"))
        receiver_name = call.get("receiver_name")
        targets: list[tuple[str, str]] = []
        if receiver_type:
            targets.extend(
                _receiver_contract_targets(
                    rel,
                    receiver_type,
                    sym_map=sym_map,
                    contracts_by_name=contracts_by_name,
                    implementers_by_base=implementers_by_base,
                    registry=registry,
                )
            )
        if isinstance(receiver_name, str) and receiver_name:
            inferred_type = scope_types.get(receiver_name)
            if inferred_type:
                for target in _receiver_contract_targets(
                    rel,
                    inferred_type,
                    sym_map=sym_map,
                    contracts_by_name=contracts_by_name,
                    implementers_by_base=implementers_by_base,
                    registry=registry,
                ):
                    if target not in targets:
                        targets.append(target)
            elif receiver_name in sym_map.get(rel, {}):
                target_rel, guessed_contract = sym_map[rel][receiver_name]
                if guessed_contract:
                    targets.append((target_rel, guessed_contract))
            elif receiver_name == "this":
                targets.append((rel, contract))
            elif receiver_name == "super":
                for target in _base_targets(rel, contract, bases_map=bases_map, sym_map=sym_map, contracts_by_name=contracts_by_name):
                    if target not in targets:
                        targets.append(target)
            elif receiver_name[:1].isupper():
                for target in _receiver_contract_targets(
                    rel,
                    receiver_name,
                    sym_map=sym_map,
                    contracts_by_name=contracts_by_name,
                    implementers_by_base=implementers_by_base,
                    registry=registry,
                ):
                    if target not in targets:
                        targets.append(target)
        if function_name and targets:
            hit = _resolve_contract_function(targets, function_name, registry=registry, functions_by_contract=functions_by_contract)
            if hit:
                return hit
        if function_name:
            got = _find_function_qid(function_name, prefer_rel=rel, prefer_contract=contract, registry=registry)
            if got and got != caller_qid:
                return got
    if callee_raw:
        tail = _clean_symbol_name(callee_raw.split(".")[-1])
        got = _find_function_qid(tail, prefer_rel=rel, prefer_contract=contract, registry=registry)
        if got and got != caller_qid:
            return got
    return None


def _contract_inheritance_edges(
    registry: dict[str, dict[str, Any]],
    functions_by_contract: dict[tuple[str, str], dict[str, list[str]]],
    bases_map: dict[tuple[str, str], list[str]],
    sym_map: dict[str, dict[str, tuple[str, str | None]]],
    contracts_by_name: dict[str, list[tuple[str, str]]],
) -> set[tuple[str, str]]:
    edges: set[tuple[str, str]] = set()
    for (rel, contract), names_map in functions_by_contract.items():
        base_targets = _base_targets(rel, contract, bases_map=bases_map, sym_map=sym_map, contracts_by_name=contracts_by_name)
        if not base_targets:
            continue
        for function_name, qids in names_map.items():
            for qid in qids:
                prefer_node_type = registry.get(qid, {}).get("node_type")
                target = _resolve_contract_function(
                    base_targets,
                    function_name,
                    registry=registry,
                    functions_by_contract=functions_by_contract,
                    prefer_node_type=prefer_node_type if isinstance(prefer_node_type, str) else None,
                )
                if target and target != qid:
                    a, b = (qid, target) if qid < target else (target, qid)
                    edges.add((a, b))
    return edges


def build_linked_call_graph(state: dict[str, Any]) -> dict[str, Any]:
    root = Path(state["repo_path"]).resolve()
    registry = _registry_from_state(state)
    sym_map = _import_symbol_map(state)
    bases_map = _bases_by_contract(state)
    functions_by_contract = _functions_by_contract(registry)
    contracts_by_name = _contracts_by_name(registry)
    implementers_by_base = _implementers_by_base(bases_map, contracts_by_name)
    nodes: set[str] = set(registry.keys())
    undirected: set[tuple[str, str]] = _contract_inheritance_edges(registry, functions_by_contract, bases_map, sym_map, contracts_by_name)
    directed_file: list[tuple[str, str]] = []
    link_attempts: list[dict[str, Any]] = []
    for abs_path in state.get("sol_files") or []:
        if not abs_path:
            continue
        rec = get_file_record(state, abs_path)
        if not rec or rec.get("parse_status") != "ok" or not rec.get("ast"):
            continue
        directed_file.extend(_build_import_edges_for_file(root, abs_path, rec["ast"]))
    for abs_path in state.get("sol_files") or []:
        if not abs_path:
            continue
        rec = get_file_record(state, abs_path)
        if not rec or not isinstance(rec.get("ir"), dict):
            continue
        ir = rec["ir"]
        for edge in ir.get("call_graph", {}).get("edges") or []:
            if not isinstance(edge, dict):
                continue
            fr = edge.get("from")
            if not isinstance(fr, str) or fr not in registry:
                continue
            to = edge.get("to")
            call_rec = {
                "resolved_target": to if isinstance(to, str) and to in registry else None,
                "callee_raw": edge.get("callee_raw") or "",
                "call_kind": edge.get("kind") or "unknown",
                "receiver_expression": edge.get("receiver_expression"),
                "receiver_name": edge.get("receiver_name"),
                "receiver_type": edge.get("receiver_type"),
                "member_name": edge.get("member_name"),
            }
            other: str | None = None
            if isinstance(to, str) and to in registry:
                other = to
            else:
                other = _resolve_call_target(fr, call_rec, registry, sym_map, bases_map, functions_by_contract, contracts_by_name, implementers_by_base)
                link_attempts.append(
                    {
                        "from": fr,
                        "raw_to": to,
                        "resolved_to": other,
                        "callee_raw": edge.get("callee_raw"),
                        "kind": edge.get("kind"),
                        "receiver_name": edge.get("receiver_name"),
                        "receiver_type": edge.get("receiver_type"),
                        "member_name": edge.get("member_name"),
                    }
                )
            if other and other in registry and other != fr:
                a, b = (fr, other) if fr < other else (other, fr)
                undirected.add((a, b))
    file_import_edges = list({tuple(edge) for edge in directed_file})
    return {"nodes": sorted(nodes), "edges": [{"a": a, "b": b} for a, b in sorted(undirected)], "file_import_edges": file_import_edges, "registry": {k: dict(v) for k, v in registry.items()}, "link_attempts": link_attempts, "stats": {"function_count": len(nodes), "edge_count": len(undirected), "file_import_count": len(file_import_edges)}}


def _connected_components(nodes: set[str], edge_pairs: set[tuple[str, str]]) -> list[set[str]]:
    adj: dict[str, set[str]] = defaultdict(set)
    for a, b in edge_pairs:
        adj[a].add(b)
        adj[b].add(a)
    seen: set[str] = set()
    comps: list[set[str]] = []
    for start in nodes:
        if start in seen:
            continue
        comp: set[str] = set()
        queue: deque[str] = deque([start])
        while queue:
            node = queue.popleft()
            if node in seen:
                continue
            seen.add(node)
            comp.add(node)
            for nxt in adj.get(node, ()):
                if nxt not in seen:
                    queue.append(nxt)
        comps.append(comp)
    return comps


def _merge_metrics(cluster_files: list[str], pre_scan: dict[str, dict[str, Any]]) -> dict[str, Any]:
    metrics = {"loc": 0, "external_calls": 0, "has_proxy": False, "has_assembly": False, "solidity_version": [], "imports": []}
    versions: set[str] = set()
    imports: set[str] = set()
    for path in cluster_files:
        item = pre_scan.get(path, {})
        metrics["loc"] += int(item.get("loc", 0) or 0)
        metrics["external_calls"] += int(item.get("external_calls", 0) or 0)
        metrics["has_proxy"] = metrics["has_proxy"] or bool(item.get("has_proxy"))
        metrics["has_assembly"] = metrics["has_assembly"] or bool(item.get("has_assembly"))
        versions.update(item.get("solidity_version") or [])
        imports.update(item.get("imports") or [])
    metrics["solidity_version"] = sorted(versions)
    metrics["imports"] = sorted(imports)
    return metrics


def _dedupe_clusters(clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, ...]] = set()
    unique: list[dict[str, Any]] = []
    for cluster in clusters:
        function_rows = cluster.get("functions") or []
        signature = tuple(
            sorted(
                row["qualified_id"]
                for row in function_rows
                if isinstance(row, dict) and isinstance(row.get("qualified_id"), str) and row.get("qualified_id")
            )
        )
        if signature in seen:
            continue
        seen.add(signature)
        unique.append(cluster)
    return unique


def build_function_call_clusters(state: dict[str, Any], dependency_graph: dict[str, dict[str, float]], file_ranks: dict[str, float]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = Path(state["repo_path"]).resolve()
    graph_blob = build_linked_call_graph(state)
    registry: dict[str, dict[str, Any]] = graph_blob.get("registry") or {}
    nodes = set(registry.keys())
    qids_by_rel: dict[str, list[str]] = defaultdict(list)
    for qid, meta in registry.items():
        rel = meta.get("rel_path")
        if isinstance(rel, str) and rel:
            qids_by_rel[rel.replace("\\", "/")].append(qid)
    edge_pairs: set[tuple[str, str]] = set()
    for edge in graph_blob.get("edges") or []:
        if isinstance(edge, dict):
            a, b = edge.get("a"), edge.get("b")
            if isinstance(a, str) and isinstance(b, str):
                edge_pairs.add((a, b) if a < b else (b, a))
    sol_files = state.get("sol_files") or []
    rel_by_abs = {str(Path(p).resolve()): Path(p).resolve().relative_to(root).as_posix() for p in sol_files if p}
    abs_by_rel = {v: k for k, v in rel_by_abs.items()}
    directed: dict[str, set[str]] = defaultdict(set)
    for src, destinations in dependency_graph.items():
        for dst in destinations or {}:
            if src != dst:
                directed[src].add(dst)
    for abs_path, metrics in state.get("pre_scan", {}).items():
        src_rel = rel_by_abs.get(str(Path(abs_path).resolve()))
        if not src_rel:
            continue
        src_fp = Path(abs_path).resolve()
        for imported in metrics.get("imports") or []:
            resolved = (src_fp.parent / imported).resolve()
            try:
                dst_rel = resolved.relative_to(root).as_posix()
            except ValueError:
                continue
            if resolved.is_file() and resolved.suffix == ".sol":
                directed[src_rel].add(dst_rel)
    for src, dst in graph_blob.get("file_import_edges") or []:
        if isinstance(src, str) and isinstance(dst, str):
            directed[src].add(dst)
    pre_scan = state.get("pre_scan") or {}
    if not nodes:
        cluster_files = [str(Path(p).resolve()) for p in sol_files if p]
        ordered_rel_final = []
        for ap in cluster_files:
            try:
                ordered_rel_final.append(Path(ap).resolve().relative_to(root).as_posix())
            except ValueError:
                ordered_rel_final.append(Path(ap).name)
        return ([{"cluster_id": "cluster-01", "files": cluster_files, "rel_files": ordered_rel_final, "metrics": _merge_metrics(cluster_files, pre_scan), "rank": sum(file_ranks.get(r, 0.0) for r in ordered_rel_final), "dependencies": [], "functions": []}], graph_blob)
    components = _connected_components(nodes, edge_pairs)
    clusters: list[dict[str, Any]] = []
    for index, comp in enumerate(components, start=1):
        cid = f"cluster-{index:02d}"
        rels_set: set[str] = set()
        abs_set: set[str] = set()
        comp_qids = sorted(comp)
        for qid in comp_qids:
            meta = registry.get(qid) or {}
            rel = meta.get("rel_path") or ""
            if rel:
                rels_set.add(rel.replace("\\", "/"))
            ap = meta.get("abs_path")
            if isinstance(ap, str) and ap:
                abs_set.add(ap)
        cluster_qids: set[str] = set(comp_qids)
        for rel in rels_set:
            cluster_qids.update(qids_by_rel.get(rel, []))
        func_rows: list[dict[str, Any]] = []
        for qid in sorted(cluster_qids):
            meta = registry.get(qid) or {}
            rel = meta.get("rel_path") or ""
            if rel:
                rels_set.add(rel.replace("\\", "/"))
            ap = meta.get("abs_path")
            if isinstance(ap, str) and ap:
                abs_set.add(ap)
            func_rows.append(
                {
                    "qualified_id": qid,
                    "rel_path": rel,
                    "abs_path": ap,
                    "contract": meta.get("contract"),
                    "name": meta.get("name"),
                    "line": meta.get("line"),
                    "source": meta.get("source"),
                    "node_type": meta.get("node_type"),
                }
            )
        ordered_rel = sorted(rels_set, key=lambda r: (-file_ranks.get(r, 0.0), r))
        cluster_files = [abs_by_rel[r] for r in ordered_rel if r in abs_by_rel]
        for ap in abs_set:
            if ap not in cluster_files:
                cluster_files.append(ap)
        cluster_files = sorted(set(cluster_files), key=str)
        ordered_rel_final = []
        for ap in cluster_files:
            try:
                ordered_rel_final.append(Path(ap).resolve().relative_to(root).as_posix())
            except ValueError:
                ordered_rel_final.append(Path(ap).name)
        clusters.append({"cluster_id": cid, "files": cluster_files, "rel_files": ordered_rel_final, "metrics": _merge_metrics(cluster_files, pre_scan), "rank": sum(file_ranks.get(r, 0.0) for r in ordered_rel_final), "dependencies": [], "functions": func_rows})
    clusters = _dedupe_clusters(clusters)
    rel_to_cluster: dict[str, str] = {}
    for cluster in clusters:
        for rel in cluster.get("rel_files") or []:
            rel_to_cluster[rel.replace("\\", "/")] = cluster["cluster_id"]
    deps_by_cluster: dict[str, set[str]] = defaultdict(set)
    for src_rel, destinations in directed.items():
        c1 = rel_to_cluster.get(src_rel.replace("\\", "/"))
        if not c1:
            continue
        for dst_rel in destinations:
            c2 = rel_to_cluster.get(str(dst_rel).replace("\\", "/"))
            if c2 and c2 != c1:
                deps_by_cluster[c1].add(c2)
    for cluster in clusters:
        cluster["dependencies"] = sorted(deps_by_cluster.get(cluster["cluster_id"], set()))
    clusters.sort(key=lambda item: (-item["rank"], item["cluster_id"]))
    return clusters, graph_blob
