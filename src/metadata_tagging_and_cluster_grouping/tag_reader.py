from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from mutagen import File as MutagenFile

from metadata_tagging_and_cluster_grouping.file_scanner import ScannedAudioFile

@dataclass(frozen=True, slots=True)
class ExistingAudioMetadata:
    original_path: Path
    extension: str
    file_size: int
    duration_seconds: float | None
    musicbrainz_recording_id: str | None
    isrc: str | None
    acoustid_id: str | None
    codec: str | None
    bitrate_bps: int | None
    sample_rate_hz: int | None
    channels: int | None
    bits_per_sample: int | None

def read_existing_metadata(scanned_file: ScannedAudioFile) -> ExistingAudioMetadata:
    try:
        audio_file = MutagenFile(scanned_file.original_path)
    except Exception:
        audio_file = None

    tags = _coerce_tags(audio_file)
    info = getattr(audio_file, "info", None) if audio_file is not None else None

    return ExistingAudioMetadata(
        original_path=scanned_file.original_path,
        extension=scanned_file.extension,
        file_size=scanned_file.file_size,
        duration_seconds=_coerce_float(
            getattr(info, "length", None),
            fallback=scanned_file.duration_seconds,
        ),
        musicbrainz_recording_id=_find_first_identifier(
            tags,
            exact_aliases=[
                "musicbrainzrecordingid",
                "musicbrainztrackid",
                "ufidhttpmusicbrainzorg",
            ],
            contains_aliases=["musicbrainzrecordingid", "musicbrainztrackid"],
        ),
        isrc=_find_first_identifier(
            tags,
            exact_aliases=["isrc", "tsrc"],
            contains_aliases=["isrc"],
        ),
        acoustid_id=_find_first_identifier(
            tags,
            exact_aliases=["acoustidid"],
            contains_aliases=["acoustidid"],
        ),
        codec=_read_codec_name(audio_file, info),
        bitrate_bps=_coerce_int(getattr(info, "bitrate", None)),
        sample_rate_hz=_coerce_int(
            getattr(info, "sample_rate", None) or getattr(info, "samplerate", None)
        ),
        channels=_coerce_int(getattr(info, "channels", None)),
        bits_per_sample=_coerce_int(getattr(info, "bits_per_sample", None)),
    )

def read_existing_metadata_batch(
    scanned_files: Iterable[ScannedAudioFile],
) -> list[ExistingAudioMetadata]:
    return [read_existing_metadata(scanned_file) for scanned_file in scanned_files]

def demo_read_existing_metadata(scanned_files: Iterable[ScannedAudioFile]) -> list[ExistingAudioMetadata]:
    metadata_rows = read_existing_metadata_batch(scanned_files)

    for metadata in metadata_rows:
        print(
            f"mbid={metadata.musicbrainz_recording_id or 'missing'} | "
            f"isrc={metadata.isrc or 'missing'} | "
            f"acoustid={metadata.acoustid_id or 'missing'} | "
            f"codec={metadata.codec or 'unknown'} | "
            f"sample_rate_hz={metadata.sample_rate_hz or 'unknown'} | "
            f"bitrate_bps={metadata.bitrate_bps or 'unknown'}"
        )

    return metadata_rows

def _coerce_tags(audio_file: Any) -> dict[str, list[str]]:
    raw_tags = getattr(audio_file, "tags", None)
    if not raw_tags:
        return {}

    normalized_tags: dict[str, list[str]] = {}
    for raw_key, raw_value in raw_tags.items():
        values = _flatten_tag_value(raw_value)
        if values:
            normalized_tags[str(raw_key)] = values

    return normalized_tags

def _flatten_tag_value(raw_value: Any) -> list[str]:
    if raw_value is None:
        return []

    if isinstance(raw_value, bytes):
        return _clean_string_values([_decode_bytes(raw_value)])

    if isinstance(raw_value, str):
        return _clean_string_values([raw_value])

    if hasattr(raw_value, "text"):
        return _flatten_tag_value(raw_value.text)

    if hasattr(raw_value, "value"):
        return _flatten_tag_value(raw_value.value)

    if hasattr(raw_value, "data"):
        return _flatten_tag_value(raw_value.data)

    if hasattr(raw_value, "owner") and hasattr(raw_value, "data"):
        return _flatten_tag_value(raw_value.data)

    if isinstance(raw_value, (list, tuple, set)):
        flattened: list[str] = []
        for item in raw_value:
            flattened.extend(_flatten_tag_value(item))
        return _clean_string_values(flattened)

    return _clean_string_values([str(raw_value)])

def _find_first_identifier(
    tags: dict[str, list[str]],
    exact_aliases: Iterable[str],
    contains_aliases: Iterable[str],
) -> str | None:
    normalized_entries = [
        (_normalize_tag_key(key), _clean_string_values(values))
        for key, values in tags.items()
    ]

    for alias in exact_aliases:
        for normalized_key, values in normalized_entries:
            if normalized_key == alias and values:
                return values[0]

    for alias in contains_aliases:
        for normalized_key, values in normalized_entries:
            if alias in normalized_key and values:
                return values[0]

    return None

def _normalize_tag_key(key: str) -> str:
    return "".join(character for character in key.lower() if character.isalnum())

def _clean_string_values(values: Iterable[str]) -> list[str]:
    cleaned_values: list[str] = []
    for value in values:
        normalized_value = value.strip()
        if normalized_value:
            cleaned_values.append(normalized_value)
    return cleaned_values

def _read_codec_name(audio_file: Any, info: Any) -> str | None:
    mime = getattr(audio_file, "mime", None) if audio_file is not None else None
    if isinstance(mime, list) and mime:
        return str(mime[0])
    if isinstance(mime, str) and mime:
        return mime

    codec_name = getattr(info, "codec", None)
    if codec_name:
        return str(codec_name)

    return audio_file.__class__.__name__ if audio_file is not None else None

def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None

def _coerce_float(value: Any, fallback: float | None = None) -> float | None:
    if value is None:
        return fallback

    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback

def _decode_bytes(value: bytes) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return value.decode("latin-1", errors="replace")
