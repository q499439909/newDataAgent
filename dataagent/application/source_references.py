from __future__ import annotations

import re
from pathlib import Path
from typing import Any


_QUOTED_WINDOWS_PATH = re.compile(
    r"""(?P<quote>["'])(?P<value>[A-Za-z]:[\\/][^"']+)(?P=quote)"""
)
_WINDOWS_PATH = re.compile(
    r"""(?<![A-Za-z0-9_])(?P<value>[A-Za-z]:[\\/][^\s,，;；。！？!?"'<>|]+)"""
)
_TYPED_URI = re.compile(
    r"""(?P<value>(?P<scheme>s3|gs|oss|milvus|postgresql|postgres|mysql|"""
    r"""sqlite)://[^\s,，;；。！？!?"'<>]+)""",
    re.IGNORECASE,
)


class AmbiguousSourceReference(ValueError):
    """An unquoted source cannot be separated from adjacent natural language."""


def _existing_windows_path_prefix(
    value: str,
    *,
    has_explicit_boundary: bool,
) -> str:
    r"""Separate an existing path from directly adjacent requirement prose.

    An unquoted Windows path may legally contain Unicode, so syntax alone
    cannot distinguish ``D:\batch处理这些文件`` from one long filename. When
    the full candidate does not exist, use only an existing prefix inside the
    final path segment. Never fall back to an ancestor directory because that
    would silently rewrite a genuinely nonexistent source.
    """

    candidate = value.rstrip(".!?。！？)]}")
    if Path(candidate).exists():
        return candidate
    final_separator = max(candidate.rfind("\\"), candidate.rfind("/"))
    for end in range(len(candidate) - 1, final_separator, -1):
        prefix = candidate[:end]
        if Path(prefix).exists():
            return prefix
    if not has_explicit_boundary:
        raise AmbiguousSourceReference(
            "Unquoted Windows source has no syntactic boundary and does not exist"
        )
    return candidate


def extract_explicit_data_sources(text: str) -> list[dict[str, Any]]:
    """Recognize typed source references without interpreting task semantics."""

    matches: list[tuple[int, dict[str, Any]]] = []
    occupied: list[tuple[int, int]] = []

    for match in _QUOTED_WINDOWS_PATH.finditer(text):
        occupied.append(match.span())
        matches.append(
            (
                match.start(),
                {
                    "type": "local_directory",
                    "uri": match.group("value"),
                    "mapping": {},
                },
            )
        )
    for match in _WINDOWS_PATH.finditer(text):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        matches.append(
            (
                match.start(),
                {
                    "type": "local_directory",
                    "uri": _existing_windows_path_prefix(
                        match.group("value"),
                        has_explicit_boundary=match.end("value") < len(text),
                    ),
                    "mapping": {},
                },
            )
        )
    for match in _TYPED_URI.finditer(text):
        scheme = match.group("scheme").lower()
        uri = match.group("value").rstrip(".!?)]}")
        source_type = (
            "milvus"
            if scheme == "milvus"
            else "database"
            if scheme in {"postgresql", "postgres", "mysql", "sqlite"}
            else "object_storage"
        )
        matches.append(
            (
                match.start(),
                {
                    "type": source_type,
                    "uri": uri,
                    "mapping": {},
                },
            )
        )

    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for _, source in sorted(matches, key=lambda item: item[0]):
        identity = (str(source["type"]), str(source["uri"]))
        if identity not in seen:
            seen.add(identity)
            unique.append(source)
    return unique


__all__ = ["AmbiguousSourceReference", "extract_explicit_data_sources"]
