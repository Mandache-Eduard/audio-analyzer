from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

ANALYZER_VERSION = "1.0"
RESOLVER_VERSION = "1.0"
DEFAULT_FINGERPRINT_SETTINGS: dict[str, int] = {"length": 120}


@dataclass(frozen=True, slots=True)
class FileIdentity:
    path: Path
    normalized_path: str
    size_bytes: int
    mtime_ns: int
    quick_key: str


@dataclass(frozen=True, slots=True)
class CachedFingerprint:
    chromaprint: str
    duration_seconds: float
    acoustid_id: str | None
    acoustid_score: float | None
    lookup_json: dict[str, Any] | None
    fpcalc_version: str
    fingerprint_settings: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CachedAnalysis:
    analyzer_version: str
    result: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CachedMetadataResolution:
    resolver_version: str
    result: dict[str, Any]
