from __future__ import annotations

import csv
from pathlib import Path

from caching_and_duplicate_detection.duplicate_models import DuplicateGroup

CSV_FIELDNAMES = [
    "group_id",
    "tier",
    "confidence_label",
    "path",
    "extension",
    "size_bytes",
    "duration_seconds",
    "bitrate_bps",
    "sample_rate_hz",
    "channels",
    "bits_per_sample",
    "content_hash",
    "acoustid_id",
    "acoustid_score",
    "recording_mbid",
    "source",
    "recommended_action",
]


def print_no_supported_audio_files_report() -> None:
    print("Duplicate detection report:")
    print("    no supported audio files found")


def print_duplicate_report(groups: list[DuplicateGroup]) -> None:
    if not groups:
        print("Duplicate detection report:")
        print("    no duplicate groups found")
        return

    print("Duplicate detection report:")
    print(f"    groups found: {len(groups)}")

    tier_counts: dict[str, int] = {}
    for group in groups:
        tier_counts[group.tier] = tier_counts.get(group.tier, 0) + 1

    for tier, count in sorted(tier_counts.items()):
        print(f"    {tier}: {count}")

    for group in groups:
        print(
            f"Group {group.group_id} | {group.tier} | {group.confidence_label} | files: {len(group.files)}"
        )
        print(f"    recommended action: {group.recommended_action}")
        for duplicate_file in group.files:
            print(f"    {duplicate_file.path}")
            print(
                "        format={} size={} duration={} bitrate={} samplerate={} channels={} bits={}".format(
                    duplicate_file.extension or "unknown",
                    duplicate_file.size_bytes,
                    _format_optional_number(duplicate_file.duration_seconds),
                    _format_optional_number(duplicate_file.bitrate_bps),
                    _format_optional_number(duplicate_file.sample_rate_hz),
                    _format_optional_number(duplicate_file.channels),
                    _format_optional_number(duplicate_file.bits_per_sample),
                )
            )
            print(
                "        acoustid_id={} acoustid_score={} recording_mbid={} release_mbid={} source={}".format(
                    duplicate_file.acoustid_id or "missing",
                    _format_optional_number(duplicate_file.acoustid_score),
                    duplicate_file.recording_mbid or "missing",
                    duplicate_file.release_mbid or "missing",
                    duplicate_file.source or "missing",
                )
            )


def write_duplicate_report_csv(groups: list[DuplicateGroup], output_path: str | Path) -> Path:
    target_path = Path(output_path).expanduser()
    if not target_path.is_absolute():
        target_path = (Path.cwd() / target_path).resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    with target_path.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        for group in groups:
            for duplicate_file in group.files:
                writer.writerow(
                    {
                        "group_id": group.group_id,
                        "tier": group.tier,
                        "confidence_label": group.confidence_label,
                        "path": str(duplicate_file.path),
                        "extension": duplicate_file.extension,
                        "size_bytes": duplicate_file.size_bytes,
                        "duration_seconds": duplicate_file.duration_seconds,
                        "bitrate_bps": duplicate_file.bitrate_bps,
                        "sample_rate_hz": duplicate_file.sample_rate_hz,
                        "channels": duplicate_file.channels,
                        "bits_per_sample": duplicate_file.bits_per_sample,
                        "content_hash": duplicate_file.content_hash,
                        "acoustid_id": duplicate_file.acoustid_id,
                        "acoustid_score": duplicate_file.acoustid_score,
                        "recording_mbid": duplicate_file.recording_mbid,
                        "source": duplicate_file.source or group.tier,
                        "recommended_action": group.recommended_action,
                    }
                )

    return target_path


def _format_optional_number(value: object) -> str:
    return "unknown" if value is None else str(value)
