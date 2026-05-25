"""Progressive-disclosure skill loader for the chat agent.

Mirrors the pattern from OpenGenerativeUI's deepagents `skills=[...]` argument:
each subdirectory under dashboard/chat/skills/ holds a SKILL.md whose
frontmatter (or first heading + first paragraph) gives a short index entry,
while the full content is fetched on-demand by the `read_skill` tool.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def chat_skills_root() -> Path:
    return Path(__file__).resolve().parent / "skills"


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    block = match.group(1)
    body = text[match.end():]
    meta: dict[str, str] = {}
    for line in block.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip().lower()] = value.strip().strip('"').strip("'")
    return meta, body


def _first_heading(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
        if stripped:
            return stripped[:80]
    return ""


def _first_paragraph(body: str) -> str:
    parts: list[str] = []
    in_para = False
    for line in body.splitlines():
        if line.strip().startswith("#"):
            if in_para:
                break
            continue
        if not line.strip():
            if in_para:
                break
            continue
        parts.append(line.strip())
        in_para = True
    return " ".join(parts)[:400]


class SkillEntry:
    __slots__ = ("name", "title", "description", "path")

    def __init__(self, name: str, title: str, description: str, path: Path) -> None:
        self.name = name
        self.title = title
        self.description = description
        self.path = path

    def to_index_dict(self) -> dict[str, str]:
        return {"name": self.name, "title": self.title, "description": self.description}


def load_chat_skills(root: Optional[Path] = None) -> list[SkillEntry]:
    base = (root or chat_skills_root()).resolve()
    if not base.exists() or not base.is_dir():
        return []

    skills: list[SkillEntry] = []
    for directory in sorted(base.iterdir()):
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        skill_file = directory / "SKILL.md"
        if not skill_file.exists():
            continue
        try:
            text = skill_file.read_text(encoding="utf-8")
        except OSError:
            continue
        meta, body = _parse_frontmatter(text)
        name = meta.get("name", directory.name).strip() or directory.name
        title = meta.get("title", "").strip() or _first_heading(body) or name
        description = meta.get("description", "").strip() or _first_paragraph(body)
        skills.append(SkillEntry(name=name, title=title, description=description, path=skill_file))
    return skills


def read_skill_text(name: str, root: Optional[Path] = None) -> Optional[str]:
    base = (root or chat_skills_root()).resolve()
    candidate_dir = base / name
    if candidate_dir.is_dir():
        skill_file = candidate_dir / "SKILL.md"
        if skill_file.exists():
            try:
                return skill_file.read_text(encoding="utf-8")
            except OSError:
                return None
    # Fallback: scan for matching name in frontmatter
    for skill in load_chat_skills(base):
        if skill.name == name:
            try:
                return skill.path.read_text(encoding="utf-8")
            except OSError:
                return None
    return None


def build_skills_index_block(skills: list[SkillEntry]) -> str:
    if not skills:
        return ""
    lines = [
        "## Available Skills (progressive disclosure)",
        "",
        "Call the `read_skill(name)` tool to load the full body of a skill",
        "before producing the response it governs. The index below is the",
        "only summary you have until you do.",
        "",
    ]
    for skill in skills:
        lines.append(f"- `{skill.name}` — {skill.title}")
        if skill.description:
            lines.append(f"    {skill.description}")
    return "\n".join(lines)
