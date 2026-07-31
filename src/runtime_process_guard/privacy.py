"""Privacy-preserving command identity helpers."""

from __future__ import annotations

import hashlib
import json
import ntpath
import os
import re
from collections.abc import Sequence

_SECRET_KEY = re.compile(
    r"(?i)(api[-_]?key|token|password|passwd|secret|credential|authorization)"
)


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
