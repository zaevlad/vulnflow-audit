from __future__ import annotations

from typing import Any

from .parser import _callee_name, _end_line_of, _line_of, _name_of, extract_functions_fallback, extract_tags_fallback

LOW_LEVEL_MEMBERS = frozenset({"call", "delegatecall", "staticcall", "transfer", "send"})


def type_name_to_str(node: Any) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    node_type = node.get("type")
    if node_type == "ElementaryTypeName":
        return str(node.get("name") or "")
    if node_type == "UserDefinedTypeName":
        path = node.get("namePath")
        if isinstance(path, str):
            return path
        if isinstance(path, list):
            return ".".join(str(x) for x in path)
        return str(node.get("name") or "")
    if node_type == "ArrayTypeName":
        base = type_name_to_str(node.get("baseTypeName"))
        return f"{base}[]" if base else "[]"
    if node_type == "Mapping":
        return f"mapping({type_name_to_str(node.get('keyType'))} => {type_name_to_str(node.get('valueType'))})"
    if "name" in node:
        return str(node["name"])
    return ""


def _func_id(rel_path: str, contract: str, name: str, line: int) -> str:
    return f"{rel_path}::{contract}::{name}:{line}"


def _node_storage_key(node_type: str, name: str, line: int) -> str:
    return f"{node_type}:{name}:{line}"


def _visit_collect_calls(node: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(node, list):
        for item in node:
            _visit_collect_calls(item, out)
        return
    if not isinstance(node, dict):
        return
    if node.get("type") == "FunctionCall":
        out.append(node)
    for val in node.values():
        _visit_collect_calls(val, out)


def _describe_callee(expr: Any) -> str:
    if expr is None:
        return ""
    if isinstance(expr, dict) and expr.get("type") == "FunctionCall":
        return _describe_callee(expr.get("expression"))
    name = _name_of(expr)
    if name:
        return name
    return _callee_name(expr) or ""


def _receiver_context(expr: Any) -> dict[str, str | None]:
    if not isinstance(expr, dict) or expr.get("type") != "MemberAccess":
        return {
            "receiver_expression": None,
            "receiver_name": None,
            "receiver_type": None,
            "member_name": None,
        }
    inner = expr.get("expression")
    member = expr.get("memberName")
    receiver_expression = _name_of(inner) or _describe_callee(inner) or ""
    receiver_name: str | None = None
    receiver_type: str | None = None
    if isinstance(inner, dict) and inner.get("type") == "Identifier":
        receiver_name = str(inner.get("name") or "") or None
    elif isinstance(inner, dict) and inner.get("type") == "FunctionCall":
        cast_expr = inner.get("expression")
        cast_name = _name_of(cast_expr) or _callee_name(cast_expr)
        if isinstance(cast_name, str) and cast_name:
            receiver_type = cast_name
        args = inner.get("arguments") or []
        if args:
            receiver_name = _name_of(args[0]) or _describe_callee(args[0]) or None
    return {
        "receiver_expression": receiver_expression or None,
        "receiver_name": receiver_name,
        "receiver_type": receiver_type,
        "member_name": str(member) if isinstance(member, str) and member else None,
    }


def _function_parameters(params_node: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(params_node, dict) or params_node.get("type") != "ParameterList":
        return out
    for param in params_node.get("parameters") or []:
        if not isinstance(param, dict) or param.get("type") != "Parameter":
            continue
        out.append({"name": param.get("name") or "", "type": type_name_to_str(param.get("typeName"))})
    return out


def _extract_locals_from_statement(stmt: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(stmt, dict) or stmt.get("type") != "VariableDeclarationStatement":
        return out
    variables = stmt.get("variables")
    if not isinstance(variables, list):
        return out
    for var in variables:
        if not isinstance(var, dict) or var.get("type") != "VariableDeclaration":
            continue
        name = var.get("name")
        if not name:
            continue
        out.append({"name": name, "type": type_name_to_str(var.get("typeName")), "line": _line_of(var)})
    return out


def _collect_locals_recursive(node: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(node, list):
        for item in node:
            _collect_locals_recursive(item, out)
        return
    if not isinstance(node, dict):
        return
    if node.get("type") == "VariableDeclarationStatement":
        out.extend(_extract_locals_from_statement(node))
        return
    for key, val in node.items():
        if key == "loc":
            continue
        _collect_locals_recursive(val, out)


def _classify_call(call_node: dict[str, Any], *, contract: str, func_visibility_map: dict[str, str], func_line_by_name: dict[str, int], rel_path: str) -> dict[str, Any]:
    expr = call_node.get("expression")
    callee_raw = _describe_callee(expr)
    call_kind = "unknown"
    target_visibility: str | None = None
    resolved_target: str | None = None
    receiver_context = _receiver_context(expr)
    member_name = receiver_context.get("member_name")
    if isinstance(expr, dict) and expr.get("type") == "MemberAccess":
        member = expr.get("memberName")
        inner = expr.get("expression")
        if member in LOW_LEVEL_MEMBERS:
            call_kind = "low_level"
        elif isinstance(inner, dict) and inner.get("type") == "Identifier" and inner.get("name") == "this":
            call_kind = "this_qualified"
            if isinstance(member, str) and member in func_visibility_map:
                target_visibility = func_visibility_map[member]
                line_no = func_line_by_name.get(member, 0)
                resolved_target = _func_id(rel_path, contract, member, line_no) if line_no else None
        elif isinstance(inner, dict) and inner.get("type") == "Identifier" and inner.get("name") == "super":
            call_kind = "super"
        elif receiver_context.get("receiver_type"):
            call_kind = "typed_member"
        else:
            call_kind = "member"
        if isinstance(member, str) and member:
            if receiver_context.get("receiver_type"):
                callee_raw = f"{receiver_context['receiver_type']}.{member}"
            elif receiver_context.get("receiver_name"):
                callee_raw = f"{receiver_context['receiver_name']}.{member}"
            else:
                callee_raw = member
    elif isinstance(expr, dict) and expr.get("type") == "Identifier":
        name = expr.get("name")
        call_kind = "internal_name"
        member_name = str(name) if isinstance(name, str) and name else None
        if isinstance(name, str) and name in func_visibility_map:
            target_visibility = func_visibility_map[name]
            line_no = func_line_by_name.get(name, 0)
            resolved_target = _func_id(rel_path, contract, name, line_no) if line_no else None
    elif isinstance(expr, dict) and expr.get("type") == "FunctionCall":
        call_kind = "nested_call"
    else:
        call_kind = "expression"
    return {
        "line": _line_of(call_node),
        "callee_raw": callee_raw,
        "call_kind": call_kind,
        "target_visibility": target_visibility,
        "resolved_target": resolved_target,
        "receiver_expression": receiver_context.get("receiver_expression"),
        "receiver_name": receiver_context.get("receiver_name"),
        "receiver_type": receiver_context.get("receiver_type"),
        "member_name": member_name,
    }


def _modifier_invocations(sub: dict[str, Any], *, contract: str, rel_path: str, modifier_line_by_name: dict[str, int]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in sub.get("modifiers") or []:
        if not isinstance(item, dict):
            continue
        name = _name_of(item.get("name")) or _name_of(item)
        if not isinstance(name, str) or not name:
            continue
        line_no = modifier_line_by_name.get(name, 0)
        out.append(
            {
                "line": _line_of(item),
                "callee_raw": name,
                "call_kind": "modifier",
                "target_visibility": None,
                "resolved_target": _func_id(rel_path, contract, name, line_no) if line_no else None,
                "receiver_expression": None,
                "receiver_name": None,
                "receiver_type": None,
                "member_name": name,
            }
        )
    return out


def build_ir_from_ast(ast: dict[str, Any], source_code: str, rel_path: str) -> dict[str, Any]:
    lines = source_code.splitlines()
    contracts_out: list[dict[str, Any]] = []
    all_edges: list[dict[str, Any]] = []
    node_ids: set[str] = set()
    children = ast.get("children")
    if not isinstance(children, list):
        children = []
    for top in children:
        if not isinstance(top, dict) or top.get("type") != "ContractDefinition":
            continue
        cname = top.get("name") or "Anonymous"
        kind = top.get("kind") or "contract"
        state_vars: list[dict[str, Any]] = []
        func_visibility: dict[str, str] = {}
        func_line: dict[str, int] = {}
        modifier_line: dict[str, int] = {}
        for sub in top.get("subNodes") or []:
            if not isinstance(sub, dict):
                continue
            if sub.get("type") == "StateVariableDeclaration":
                for variable in sub.get("variables") or []:
                    if not isinstance(variable, dict) or variable.get("type") != "VariableDeclaration":
                        continue
                    state_vars.append({"name": variable.get("name"), "type": type_name_to_str(variable.get("typeName")), "visibility": variable.get("visibility") or "default", "line": _line_of(variable)})
            elif sub.get("type") == "FunctionDefinition" and sub.get("name"):
                func_visibility[sub["name"]] = sub.get("visibility") or "default"
                func_line[sub["name"]] = _line_of(sub)
            elif sub.get("type") == "ModifierDefinition" and sub.get("name"):
                modifier_line[sub["name"]] = _line_of(sub)
        functions_map: dict[str, Any] = {}
        for sub in top.get("subNodes") or []:
            if not isinstance(sub, dict):
                continue
            node_type = "function" if sub.get("type") == "FunctionDefinition" else "modifier" if sub.get("type") == "ModifierDefinition" else ""
            if not node_type or not sub.get("name"):
                continue
            fname = sub["name"]
            start_line = _line_of(sub)
            end_line = _end_line_of(sub) or start_line
            fid = _func_id(rel_path, cname, fname, start_line)
            node_ids.add(fid)
            body = sub.get("body")
            calls_nodes: list[dict[str, Any]] = []
            if isinstance(body, dict):
                _visit_collect_calls(body, calls_nodes)
            locals_list: list[dict[str, Any]] = []
            if isinstance(body, dict):
                _collect_locals_recursive(body, locals_list)
            call_records: list[dict[str, Any]] = []
            if node_type == "function":
                for record in _modifier_invocations(sub, contract=cname, rel_path=rel_path, modifier_line_by_name=modifier_line):
                    call_records.append(record)
                    to_id = record.get("resolved_target") or f"unresolved::{record.get('callee_raw') or 'modifier'}"
                    if to_id:
                        node_ids.add(to_id)
                    all_edges.append(
                        {
                            "from": fid,
                            "to": to_id,
                            "kind": record.get("call_kind"),
                            "line": record.get("line"),
                            "callee_raw": record.get("callee_raw"),
                            "target_visibility": record.get("target_visibility"),
                            "receiver_expression": record.get("receiver_expression"),
                            "receiver_name": record.get("receiver_name"),
                            "receiver_type": record.get("receiver_type"),
                            "member_name": record.get("member_name"),
                        }
                    )
            for call_node in calls_nodes:
                record = _classify_call(call_node, contract=cname, func_visibility_map=func_visibility, func_line_by_name=func_line, rel_path=rel_path)
                call_records.append(record)
                to_id = record.get("resolved_target")
                if not to_id and record.get("call_kind") == "low_level":
                    to_id = f"evm::{record.get('callee_raw') or 'low_level'}"
                elif not to_id:
                    to_id = f"unresolved::{record.get('callee_raw') or 'call'}"
                if to_id:
                    node_ids.add(to_id)
                all_edges.append(
                    {
                        "from": fid,
                        "to": to_id,
                        "kind": record.get("call_kind"),
                        "line": record.get("line"),
                        "callee_raw": record.get("callee_raw"),
                        "target_visibility": record.get("target_visibility"),
                        "receiver_expression": record.get("receiver_expression"),
                        "receiver_name": record.get("receiver_name"),
                        "receiver_type": record.get("receiver_type"),
                        "member_name": record.get("member_name"),
                    }
                )
            snippet = ""
            if start_line > 0 and start_line <= len(lines):
                snippet = "\n".join(lines[start_line - 1 : min(len(lines), end_line)])
            functions_map[_node_storage_key(node_type, fname, start_line)] = {
                "name": fname,
                "node_type": node_type,
                "visibility": sub.get("visibility") or "default",
                "stateMutability": sub.get("stateMutability"),
                "line": start_line,
                "end_line": end_line,
                "parameters": _function_parameters(sub.get("parameters")),
                "locals": locals_list,
                "calls": call_records,
                "source": snippet,
                "qualified_id": fid,
                "state_variables": list(state_vars),
            }
        contracts_out.append({"name": cname, "kind": kind, "state_variables": state_vars, "functions": functions_map})
    return {"contracts": contracts_out, "call_graph": {"nodes": sorted(node_ids), "edges": all_edges}}


def build_fallback_ir(source_code: str, rel_path: str, fname: str) -> dict[str, Any]:
    funcs = extract_functions_fallback(source_code)
    tags = extract_tags_fallback(source_code, rel_fname=rel_path, fname=fname)
    contracts_map: dict[str, dict[str, Any]] = {}
    for fn in funcs:
        contract_name = fn.contract_name
        if contract_name not in contracts_map:
            contracts_map[contract_name] = {"name": contract_name, "kind": "contract", "state_variables": [], "functions": {}}
        fid = _func_id(rel_path, contract_name, fn.name, fn.line)
        contracts_map[contract_name]["functions"][_node_storage_key("function", fn.name, fn.line)] = {
            "name": fn.name,
            "node_type": "function",
            "visibility": "unknown",
            "stateMutability": None,
            "line": fn.line,
            "end_line": fn.end_line,
            "parameters": [],
            "locals": [],
            "calls": [],
            "source": fn.source,
            "qualified_id": fid,
            "state_variables": [],
        }
    contracts_out = list(contracts_map.values())
    node_ids: list[str] = []
    for contract in contracts_out:
        for fdef in contract["functions"].values():
            node_ids.append(fdef["qualified_id"])
    return {"contracts": contracts_out, "call_graph": {"nodes": sorted(set(node_ids)), "edges": []}, "fallback_tags_hint": [{"rel_fname": tag.rel_fname, "line": tag.line, "name": tag.name, "kind": tag.kind} for tag in tags]}
