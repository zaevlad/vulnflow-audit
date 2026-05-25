"""Secret and path safety filters for chat tool calls.

Two guarantees are enforced here:

1. *Path blocking* — files whose names match a high-risk pattern
   (``.env``, ``*.pem``, ``*.key``, ``secrets.*``, ``id_rsa*`` and a few
   more) cannot be read at all by ``read_workspace_file``; ``list_workspace_directory``
   hides them from listings.

2. *Content scrubbing* — even from files that pass the path filter, any
   substring matching a known secret shape (AWS / Stripe / OpenAI /
   Anthropic / GitHub / Slack / Google API keys, JWTs, PEM private key
   blocks, generic Bearer tokens) is replaced with ``[REDACTED:<kind>]``
   before the content goes back to the agent. Each redaction is also
   returned as structured metadata so the UI can surface it.

The user can relax the rules via ``conf.yaml`` if they explicitly want a
file with one of these names available for chat reasoning (see
``security_config`` below).
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml


# ---------------------------------------------------------------------------
# Path blocklist
# ---------------------------------------------------------------------------


_DEFAULT_BLOCKED_PATTERNS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.env",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.cer",
    "*.crt",
    "id_rsa",
    "id_rsa.*",
    "id_dsa",
    "id_dsa.*",
    "id_ecdsa",
    "id_ecdsa.*",
    "id_ed25519",
    "id_ed25519.*",
    "secrets",
    "secrets.*",
    "credentials",
    "credentials.*",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "*.kdbx",
    "*.keystore",
    "*.jks",
)


# ---------------------------------------------------------------------------
# Secret content regexes
# ---------------------------------------------------------------------------
#
# Pattern naming convention: each name is short enough to fit in a
# `[REDACTED:<name>]` marker; values are compiled in :func:`_compile_patterns`.


_RAW_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        "aws_access_key",
        r"(?<![A-Z0-9])AKIA[0-9A-Z]{16}(?![A-Z0-9])",
    ),
    (
        "aws_secret",
        # 40-char base64-ish that occurs after literal "aws_secret_access_key"
        # to reduce false positives.
        r"(?i)aws_secret_access_key[\"'\s:=]+([A-Za-z0-9/+=]{40})",
    ),
    (
        "stripe_live",
        r"sk_live_[0-9a-zA-Z]{24,}",
    ),
    (
        "stripe_test",
        r"sk_test_[0-9a-zA-Z]{24,}",
    ),
    (
        "openai_key",
        r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}",
    ),
    (
        "anthropic_key",
        r"sk-ant-[A-Za-z0-9_-]{40,}",
    ),
    (
        "github_pat",
        r"gh[pousr]_[A-Za-z0-9]{36,}",
    ),
    (
        "slack_token",
        r"xox[abprs]-[A-Za-z0-9-]{10,}",
    ),
    (
        "google_api_key",
        r"AIza[0-9A-Za-z_-]{35}",
    ),
    (
        "jwt",
        r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_+/=-]{8,}",
    ),
    (
        "private_key_block",
        r"-----BEGIN (?:RSA |DSA |EC |OPENSSH |PGP |ENCRYPTED |)PRIVATE KEY-----[\s\S]+?-----END (?:RSA |DSA |EC |OPENSSH |PGP |ENCRYPTED |)PRIVATE KEY-----",
    ),
    (
        "bearer_token",
        # Must follow literal Authorization: Bearer to avoid matching every
        # base64-ish string in the file.
        r"(?i)authorization[\"'\s:=]+bearer\s+([A-Za-z0-9._-]{20,})",
    ),
    (
        "basic_auth_url",
        r"https?://[A-Za-z0-9._%+-]+:[^@\s/]{4,}@[A-Za-z0-9.-]+",
    ),
)


def _compile_patterns(extra: Iterable[tuple[str, str]] = ()) -> list[tuple[str, re.Pattern[str]]]:
    compiled = [(name, re.compile(raw)) for name, raw in _RAW_PATTERNS]
    for name, raw in extra:
        compiled.append((str(name), re.compile(raw)))
    return compiled


_COMPILED_PATTERNS = _compile_patterns()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class SecurityConfig:
    blocked_patterns: tuple[str, ...]
    allow_patterns: tuple[str, ...]

    def is_blocked(self, file_name: str) -> bool:
        name = file_name.lower()
        for allow in self.allow_patterns:
            if fnmatch.fnmatch(name, allow.lower()):
                return False
        for pattern in self.blocked_patterns:
            if fnmatch.fnmatch(name, pattern.lower()):
                return True
        return False


def load_security_config(conf_path: Path | None) -> SecurityConfig:
    blocked = list(_DEFAULT_BLOCKED_PATTERNS)
    allow: list[str] = []
    if conf_path and conf_path.exists():
        try:
            raw = yaml.safe_load(conf_path.read_text(encoding="utf-8")) or {}
        except Exception:
            raw = {}
        chat_section = raw.get("chat") if isinstance(raw, dict) else {}
        secrets_section = chat_section.get("secrets") if isinstance(chat_section, dict) else {}
        if isinstance(secrets_section, dict):
            extra_block = secrets_section.get("blocked_patterns") or []
            extra_allow = secrets_section.get("allow_patterns") or []
            if isinstance(extra_block, list):
                blocked.extend(str(p) for p in extra_block if isinstance(p, str))
            if isinstance(extra_allow, list):
                allow.extend(str(p) for p in extra_allow if isinstance(p, str))
    return SecurityConfig(
        blocked_patterns=tuple(blocked),
        allow_patterns=tuple(allow),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class Redaction:
    kind: str
    line: int

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "line": self.line}


def is_blocked_path(path: Path, config: SecurityConfig) -> bool:
    return config.is_blocked(path.name)


def scrub_secrets(text: str) -> tuple[str, list[Redaction]]:
    """Return (cleaned_text, redactions[]) with every known secret replaced."""
    if not text:
        return text, []

    redactions: list[Redaction] = []
    cleaned = text
    # Pre-compute character→line map for efficient line lookup.
    line_breaks = _line_break_index(cleaned)

    for name, pattern in _COMPILED_PATTERNS:
        def _replace(match: re.Match[str]) -> str:
            # Record original position (use match.start in the original text)
            line_number = _line_at(line_breaks, match.start())
            redactions.append(Redaction(kind=name, line=line_number))
            # Replace only the captured group when one exists; otherwise the
            # whole match. This keeps surrounding context (e.g. the literal
            # `Authorization: Bearer ` prefix) intact in the response.
            if match.lastindex:
                # Replace the entire match but preserve everything outside
                # the first capture group.
                full = match.group(0)
                group_value = match.group(1)
                if group_value and group_value in full:
                    return full.replace(group_value, f"[REDACTED:{name}]", 1)
            return f"[REDACTED:{name}]"

        cleaned, count = pattern.subn(_replace, cleaned)
        if count:
            # subn invokes replace via callable above which already appended;
            # nothing else to do.
            line_breaks = _line_break_index(cleaned)

    return cleaned, redactions


def filter_directory_entries(
    entries: list[dict[str, object]],
    config: SecurityConfig,
) -> tuple[list[dict[str, object]], int]:
    """Drop entries that match a blocked path pattern.

    Returns ``(visible_entries, hidden_count)``.
    """
    visible: list[dict[str, object]] = []
    hidden = 0
    for entry in entries:
        name = str(entry.get("name", ""))
        if name and config.is_blocked(name):
            hidden += 1
            continue
        visible.append(entry)
    return visible, hidden


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _line_break_index(text: str) -> list[int]:
    # Sorted list of character offsets where '\n' occurs. Used so we can
    # bisect to find the line of an arbitrary character index.
    return [i for i, ch in enumerate(text) if ch == "\n"]


def _line_at(line_breaks: list[int], offset: int) -> int:
    # Binary search via bisect; line numbers are 1-based.
    import bisect

    return bisect.bisect_right(line_breaks, offset) + 1
