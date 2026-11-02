"""Privacy-preserving command identity helpers."""

from __future__ import annotations

import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
from collections.abc import Sequence

_SECRET_KEY = re.compile(
    r"(api[-_]?key|token|password|passwd|secret|credential|authorization)", re.I
)
_TEXT_KEY = re.compile(r"[\"']?[\w-]+[\"']?")
_AUTH_SCHEME = re.compile(r"(?:Bearer|Basic)\s+", re.I)
_HOME_PATH = re.compile(r"(?:[a-z]:)?[/\\](?:Users|home)[/\\][^/\\\r\n\"'<>;,]+", re.I)


def _redact_assignments(value: str) -> str:
    parts = []
    copied = cursor = 0
    while match := _TEXT_KEY.search(value, cursor):
        cursor = match.end()
        if not _SECRET_KEY.search(match[0]):
            continue
        separator = cursor
        while separator < len(value) and value[separator].isspace():
            separator += 1
        if separator == len(value) or value[separator] not in "=:":
            continue
        start = separator + 1
        while start < len(value) and value[start].isspace():
            start += 1
        if start == len(value):
            continue
        end = start
        if value[start] in "\"'":
            quote = value[start]
            end += 1
            while end < len(value):
                if value[end] == "\\":
                    end = min(end + 2, len(value))
                elif value[end] == quote:
                    end += 1
                    break
                else:
                    end += 1
        else:
            scheme = _AUTH_SCHEME.match(value, start)
            end = scheme.end() if scheme else start
            while end < len(value) and not value[end].isspace() and value[end] not in ",;}]":
                end += 1
        parts.extend((value[copied:start], "<redacted>"))
        copied = cursor = end
    return "".join(parts) + value[copied:]


def redact_text(value: str) -> str:
    """Redact display metadata using the command identity's secret-key policy."""
    home = str(Path.home())
    value = _redact_assignments(value)
    value = re.sub(re.escape(home), "%USERPROFILE%", value, flags=re.I)
    value = _HOME_PATH.sub("%USERPROFILE%", value)
    username = ntpath.basename(home.replace("/", "\\"))
    if username:
        value = re.sub(rf"(?<![A-Za-z0-9_.<>%\-]){re.escape(username)}(?![A-Za-z0-9_.<>%\-])",
                       "<user>", value, flags=re.I)
    return value


def safe_executable_name(value: str) -> str:
    """Return only the executable basename, never its containing path."""
    return ntpath.basename(value.replace("/", "\\")) or "unknown"


def _normalize_argument(value: str, *, home: str) -> str:
    normalized = value.replace("\\", "/")
    normalized_home = home.replace("\\", "/").rstrip("/")
    if normalized_home:
        normalized = re.sub(re.escape(normalized_home), "%USERPROFILE%", normalized, flags=re.I)

    if "=" in normalized:
        key, _value = normalized.split("=", 1)
        if _SECRET_KEY.search(key):
            return f"{key}=<redacted>"
    if _SECRET_KEY.fullmatch(normalized.lstrip("-")):
        return f"{normalized}=<redacted-next>"
    return normalized


def command_identity(command: Sequence[str], *, home: str | None = None) -> str:
    """Build a stable hash without persisting raw command data.

    Secret assignment values are removed before hashing. Home paths are replaced
    with a stable placeholder so the identity cannot reveal the local username.
    """
    if not command:
        raise ValueError("command must not be empty")
    actual_home = os.path.expanduser("~") if home is None else home
    normalized: list[str] = []
    redact_next = False
    for index, value in enumerate(command):
        item = _normalize_argument(str(value), home=actual_home)
        if index == 0:
            item = safe_executable_name(item).lower()
        if redact_next:
            item = "<redacted>"
            redact_next = False
        elif item.endswith("=<redacted-next>"):
            item = item.removesuffix("=<redacted-next>")
            redact_next = True
        normalized.append(item)
    payload = json.dumps(normalized, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
