from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

FORBIDDEN_PATH_CHARACTERS = re.compile(r'[?:/\\*"<>|]+')
WHITESPACE = re.compile(r"\s+")
DEFAULT_OUTPUT_ROOT = "sorted_files"


def plan_release_files(
    mapped_release: dict[str, Any],
    *,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
) -> list[dict[str, Any]]:
    return plan_release_files_batch([mapped_release], output_root=output_root)


def plan_release_files_batch(
    mapped_releases: Iterable[dict[str, Any]],
    *,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
) -> list[dict[str, Any]]:
    output_root_path = Path(output_root)
    planned_rows: list[dict[str, Any]] = []
    planned_by_output_path: dict[str, dict[str, Any]] = {}

    for mapped_release in mapped_releases:
        track_rows = mapped_release.get("tracks", [])
        if not isinstance(track_rows, list):
            continue

        for track_metadata in track_rows:
            if not isinstance(track_metadata, dict):
                continue

            planned_row = _build_initial_plan(track_metadata, output_root_path)
            _resolve_collision(planned_row, planned_by_output_path)
            planned_rows.append(planned_row)

            if planned_row["status"] != "duplicate_skipped":
                planned_by_output_path[planned_row["planned_output_path"]] = planned_row

    return planned_rows


def _build_initial_plan(
    track_metadata: dict[str, Any],
    output_root: Path,
) -> dict[str, Any]:
    original_path = Path(track_metadata["original_path"])
    if track_metadata.get("status") == "unmatched_in_release":
        planned_output_path = output_root / "_unmatched" / original_path.name
        return {
            "original_path": str(original_path),
            "planned_output_path": str(planned_output_path),
            "status": "unmatched",
            "reason": track_metadata.get("reason")
            or "local file could not be matched to selected release track",
            "metadata": track_metadata,
        }

    album_artist = _sanitize_path_component(
        track_metadata.get("album_artist"),
        fallback="Unknown Artist",
    )
    album_title = _sanitize_path_component(
        track_metadata.get("album"),
        fallback="Unknown Album",
    )
    track_title = _sanitize_path_component(
        track_metadata.get("title"),
        fallback="Unknown Title",
    )
    extension = _normalize_extension(track_metadata.get("extension"), original_path.suffix)
    file_name = _build_track_file_name(track_metadata, track_title, extension)
    planned_output_path = output_root / album_artist / album_title / file_name

    return {
        "original_path": str(original_path),
        "planned_output_path": str(planned_output_path),
        "status": "planned",
        "reason": None,
        "metadata": track_metadata,
    }


def _resolve_collision(
    planned_row: dict[str, Any],
    planned_by_output_path: dict[str, dict[str, Any]],
) -> None:
    planned_output_path = planned_row["planned_output_path"]
    existing_row = planned_by_output_path.get(planned_output_path)
    if existing_row is None:
        return

    if _identifier_signature(planned_row["metadata"]) == _identifier_signature(
        existing_row["metadata"]
    ):
        planned_row["status"] = "duplicate_skipped"
        planned_row["reason"] = "duplicate output path with identical identifiers"
        return

    original_output_path = Path(planned_output_path)
    recording_suffix = _short_recording_mbid(planned_row["metadata"])
    candidate_output_path = original_output_path.with_name(
        f"{original_output_path.stem} [{recording_suffix}]{original_output_path.suffix}"
    )
    counter = 2
    while str(candidate_output_path) in planned_by_output_path:
        candidate_output_path = original_output_path.with_name(
            f"{original_output_path.stem} [{recording_suffix}-{counter}]{original_output_path.suffix}"
        )
        counter += 1

    planned_row["planned_output_path"] = str(candidate_output_path)
    planned_row["reason"] = "path collision with different identifiers; suffix appended"


def _build_track_file_name(
    track_metadata: dict[str, Any],
    track_title: str,
    extension: str,
) -> str:
    track_number = _coerce_int(track_metadata.get("track_number")) or 0
    disc_number = _coerce_int(track_metadata.get("disc_number"))
    total_discs = _coerce_int(track_metadata.get("total_discs")) or 1

    if total_discs > 1 and disc_number is not None:
        return f"{disc_number}-{track_number:02d} - {track_title}{extension}"

    return f"{track_number:02d} - {track_title}{extension}"


def _sanitize_path_component(value: Any, *, fallback: str) -> str:
    if isinstance(value, str):
        cleaned_value = FORBIDDEN_PATH_CHARACTERS.sub(" ", value)
        cleaned_value = WHITESPACE.sub(" ", cleaned_value).strip()
        if cleaned_value:
            return cleaned_value
    return fallback


def _normalize_extension(value: Any, fallback: str) -> str:
    extension = value if isinstance(value, str) and value.strip() else fallback
    if not extension:
        return ""
    return extension if extension.startswith(".") else f".{extension}"


def _identifier_signature(metadata: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    return (
        _clean_string(metadata.get("recording_mbid")),
        _clean_string(metadata.get("isrc")),
        _clean_string(metadata.get("acoustid_id")),
    )


def _short_recording_mbid(metadata: dict[str, Any]) -> str:
    recording_mbid = _clean_string(metadata.get("recording_mbid"))
    if not recording_mbid:
        return "unknown"
    return recording_mbid.replace("-", "")[:8] or "unknown"


def _clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned_value = value.strip()
    return cleaned_value or None


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
