from __future__ import annotations

from collections import Counter, namedtuple
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Iterable

try:
    from solidity_parser import parser as sol_parser
except Exception:  # pragma: no cover - optional dependency
    sol_parser = None


Tag = namedtuple("Tag", "rel_fname fname line name kind")


@dataclass
class FunctionInfo:
    name: str
    contract_name: str
    line: int
    end_line: int
    source: str


DEF_NODE_TYPES = {
    "ContractDefinition": "contract",
    "FunctionDefinition": "function",
    "ModifierDefinition": "modifier",
    "EventDefinition": "event",
    "StructDefinition": "struct",
}


def parse_solidity_source(source_code: str) -> dict[str, Any]:
    if sol_parser is None:
        raise RuntimeError("solidity-parser is not installed")
    return sol_parser.parse(source_code, loc=True)


def extract_tags_from_ast(ast: dict[str, Any], rel_fname: str, fname: str) -> list[Tag]:
    tags: list[Tag] = []

    def add_tag(name: str | None, kind: str, node: dict[str, Any]) -> None:
        if not name:
            return
        tags.append(Tag(rel_fname=rel_fname, fname=fname, line=_line_of(node), name=name, kind=kind))

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if not isinstance(node, dict):
            return
        node_type = node.get("type")
        if node_type in DEF_NODE_TYPES:
            add_tag(node.get("name"), "def", node)
        elif node_type == "StateVariableDeclaration":
            for variable in node.get("variables", []):
                var_type = variable.get("typeName", {}) if isinstance(variable, dict) else {}
                if isinstance(var_type, dict) and var_type.get("type") in {"Mapping", "ArrayTypeName"}:
                    add_tag(variable.get("name"), "def", variable)
        elif node_type == "ExpressionStatement":
            expression = node.get("expression", {})
            if isinstance(expression, dict) and expression.get("type") == "FunctionCall":
                add_tag(_callee_name(expression.get("expression")), "ref", expression)
        elif node_type == "FunctionCall":
            add_tag(_callee_name(node.get("expression")), "ref", node)
        elif node_type == "InheritanceSpecifier":
            add_tag(_name_of(node.get("baseName")), "ref", node)
        elif node_type == "ImportDirective":
            add_tag(node.get("path"), "ref", node)
        for value in node.values():
            visit(value)

    visit(ast)
    return _dedupe_tags(tags)


def extract_tags_fallback(source_code: str, rel_fname: str, fname: str) -> list[Tag]:
    tags: list[Tag] = []
    lines = source_code.splitlines()

    def add(name: str, line_no: int, kind: str) -> None:
        tags.append(Tag(rel_fname=rel_fname, fname=fname, line=line_no, name=name, kind=kind))

    patterns = [
        (re.compile(r"\bcontract\s+([A-Za-z_][A-Za-z0-9_]*)"), "def"),
        (re.compile(r"\bfunction\s+([A-Za-z_][A-Za-z0-9_]*)"), "def"),
        (re.compile(r"\bmodifier\s+([A-Za-z_][A-Za-z0-9_]*)"), "def"),
        (re.compile(r"\bevent\s+([A-Za-z_][A-Za-z0-9_]*)"), "def"),
        (re.compile(r"\bstruct\s+([A-Za-z_][A-Za-z0-9_]*)"), "def"),
        (re.compile(r"\bimport\s+\"([^\"]+)\""), "ref"),
    ]

    for line_no, line in enumerate(lines, start=1):
        for pattern, kind in patterns:
            for match in pattern.finditer(line):
                add(match.group(1), line_no, kind)
        inherit_match = re.search(r"\bis\s+([A-Za-z_][A-Za-z0-9_]*)", line)
        if inherit_match:
            add(inherit_match.group(1), line_no, "ref")
        for call in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", line):
            callee = call.group(1)
            if callee not in {"if", "for", "while", "return", "require", "assert"}:
                add(callee, line_no, "ref")
    return _dedupe_tags(tags)


def split_def_ref_tags(tags: Iterable[Tag]) -> tuple[list[Tag], list[Tag]]:
    defs: list[Tag] = []
    refs: list[Tag] = []
    for tag in tags:
        if tag.kind == "def":
            defs.append(tag)
        elif tag.kind == "ref":
            refs.append(tag)
    return defs, refs


def count_defs_by_name(tags: Iterable[Tag]) -> Counter[str]:
    defs, _ = split_def_ref_tags(tags)
    return Counter(tag.name for tag in defs)


def extract_functions_fallback(source_code: str) -> list[FunctionInfo]:
    functions: list[FunctionInfo] = []
    contract_name = "Global"
    lines = source_code.splitlines()
    for match in re.finditer(r"\bcontract\s+([A-Za-z_][A-Za-z0-9_]*)", source_code):
        line = source_code[: match.start()].count("\n") + 1
        if line <= len(lines):
            contract_name = match.group(1)
    pattern = re.compile(r"\bfunction\s+([A-Za-z_][A-Za-z0-9_]*)\b")
    for match in pattern.finditer(source_code):
        name = match.group(1)
        start_line = source_code[: match.start()].count("\n") + 1
        snippet = _extract_block(source_code, match.start())
        end_line = start_line + snippet.count("\n")
        functions.append(FunctionInfo(name=name, contract_name=contract_name, line=start_line, end_line=end_line, source=snippet))
    return functions


def _name_of(node: Any) -> str | None:
    if node is None:
        return None
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        node_type = node.get("type")
        if node_type == "Identifier":
            return node.get("name")
        if node_type == "UserDefinedTypeName":
            return _name_of(node.get("namePath")) or node.get("namePath")
        if node_type == "MemberAccess":
            expression = _name_of(node.get("expression"))
            member = node.get("memberName")
            if expression and member:
                return f"{expression}.{member}"
            return member or expression
        if "name" in node:
            return node.get("name")
        if "path" in node:
            return node.get("path")
        if "namePath" in node:
            return _name_of(node.get("namePath"))
    return None


def _callee_name(node: Any) -> str | None:
    if node is None:
        return None
    if isinstance(node, dict):
        node_type = node.get("type")
        if node_type == "Identifier":
            return node.get("name")
        if node_type == "MemberAccess":
            return node.get("memberName")
        if node_type == "FunctionCall":
            return _callee_name(node.get("expression"))
    return _name_of(node)


def _line_of(node: dict[str, Any]) -> int:
    loc = node.get("loc", {})
    if isinstance(loc, dict):
        start = loc.get("start", {})
        if isinstance(start, dict):
            return int(start.get("line", 0) or 0)
    return 0


def _end_line_of(node: dict[str, Any]) -> int:
    loc = node.get("loc", {})
    if isinstance(loc, dict):
        end = loc.get("end", {})
        if isinstance(end, dict):
            return int(end.get("line", 0) or 0)
    return 0


def _dedupe_tags(tags: list[Tag]) -> list[Tag]:
    seen: set[tuple[str, str, int, str, str]] = set()
    unique: list[Tag] = []
    for tag in tags:
        key = (tag.rel_fname, tag.fname, tag.line, tag.name, tag.kind)
        if key in seen:
            continue
        seen.add(key)
        unique.append(tag)
    return unique


def _extract_block(source_code: str, start_index: int) -> str:
    brace_start = source_code.find("{", start_index)
    if brace_start == -1:
        end = source_code.find(";", start_index)
        return source_code[start_index : end if end != -1 else len(source_code)]
    depth = 0
    for index in range(brace_start, len(source_code)):
        char = source_code[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source_code[start_index : index + 1]
    return source_code[start_index:]
