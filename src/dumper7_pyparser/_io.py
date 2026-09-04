"""Locating, reading and unwrapping ``*Info.json`` files."""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import FileInfo
from .types import DumpFormatError

FILE_NAMES: dict[str, str] = {
    "classes": "ClassesInfo.json",
    "structs": "StructsInfo.json",
    "enums": "EnumsInfo.json",
    "functions": "FunctionsInfo.json",
    "offsets": "OffsetsInfo.json",
}

_GZIP_MAGIC = b"\x1f\x8b"


def locate(directory: Path, kind: str) -> Path | None:
    """Return ``<dir>/<Name>Info.json`` or its ``.gz`` twin, whichever exists."""
    base = directory / FILE_NAMES[kind]
    if base.is_file():
        return base
    gz = base.with_name(base.name + ".gz")
    if gz.is_file():
        return gz
    return None


def read_json(path: Path) -> Any:
    """Load JSON from a plain or gzip-compressed file (detected by magic bytes)."""
    with open(path, "rb") as fh:
        head = fh.read(2)
    if head == _GZIP_MAGIC:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def parse_timestamp(value: Any) -> datetime | None:
    """DSGen writes milliseconds since the epoch as a string."""
    if value is None:
        return None
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc)


def unwrap(raw: Any, path: Path | None = None) -> tuple[list, FileInfo]:
    """Split a file into its ``data`` list and envelope metadata.

    A bare list is accepted as ``data`` with empty metadata so hand-built
    inputs and older tooling still load.
    """
    if isinstance(raw, list):
        return raw, FileInfo(path=path)
    if isinstance(raw, dict) and isinstance(raw.get("data"), list):
        version = raw.get("version")
        credit = raw.get("credit")
        return raw["data"], FileInfo(
            updated_at=parse_timestamp(raw.get("updated_at")),
            version=version if isinstance(version, int) else None,
            credit=credit if isinstance(credit, dict) else None,
            path=path,
        )
    where = f" in {path}" if path else ""
    raise DumpFormatError(f"expected a Dumpspace envelope or list{where}, got {type(raw).__name__}")
