from __future__ import annotations

import os
import re
import shlex
from collections.abc import MutableMapping
from pathlib import Path


_ENVIRONMENT_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _unquoted_value(raw: str) -> str:
    for index, character in enumerate(raw):
        if character == "#" and (index == 0 or raw[index - 1].isspace()):
            return raw[:index].rstrip()
    return raw.strip()


def _parse_value(raw: str) -> str | None:
    value = raw.strip()
    if not value:
        return ""
    if value[0] not in {"'", '"'}:
        return _unquoted_value(value)
    try:
        parsed = shlex.split(value, comments=True, posix=True)
    except ValueError:
        return None
    return parsed[0] if len(parsed) == 1 else None


def load_project_dotenv(
    project_root: Path,
    *,
    environment: MutableMapping[str, str] | None = None,
) -> int:
    """Load simple KEY=value entries without overriding the process environment."""
    dotenv_path = project_root / ".env"
    if not dotenv_path.is_file():
        return 0
    try:
        lines = dotenv_path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        return 0

    target = environment if environment is not None else os.environ
    loaded = 0
    for line in lines:
        candidate = line.strip()
        if not candidate or candidate.startswith("#"):
            continue
        if candidate.startswith("export "):
            candidate = candidate[7:].lstrip()
        if "=" not in candidate:
            continue
        key, raw_value = candidate.split("=", 1)
        key = key.strip()
        if not _ENVIRONMENT_KEY.fullmatch(key) or key in target:
            continue
        value = _parse_value(raw_value)
        if value is None:
            continue
        target[key] = value
        loaded += 1
    return loaded
