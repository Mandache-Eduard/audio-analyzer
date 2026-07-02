from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class DuplicateFileRecord:
    file_id: int | None
    path: Path
    extension: str
    codec: str | None
    size_bytes: int
    duration_seconds: float | None
    bitrate_bps: int | None
    sample_rate_hz: int | None
    channels: int | None
    bits_per_sample: int | None
    content_hash: str | None
    acoustid_id: str | None
    acoustid_score: float | None
    recording_mbid: str | None
    release_mbid: str | None
    source: str | None
    title: str | None
    artist: str | None


@dataclass(slots=True)
class DuplicateGroup:
    group_id: int
    tier: str
    confidence_label: str
    recommended_action: str
    files: list[DuplicateFileRecord] = field(default_factory=list)
