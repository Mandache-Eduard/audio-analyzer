from __future__ import annotations

from pathlib import Path

from caching_and_duplicate_detection.cleanup_models import CleanupGroupDecision, CleanupPlan
from caching_and_duplicate_detection.duplicate_models import DuplicateFileRecord, DuplicateGroup

SAFE_CLEANUP_TIERS = frozenset({"binary_hash"})
LOSSLESS_CODECS = frozenset(
    {
        "alac",
        "aiff",
        "flac",
        "pcm",
        "pcm_s16le",
        "pcm_s24le",
        "pcm_s32le",
        "wav",
    }
)
LOSSLESS_EXTENSIONS = frozenset({".aif", ".aiff", ".flac", ".wav"})
LOSSY_CODECS = frozenset({"aac", "mp3", "opus", "vorbis", "wma"})
LOSSY_EXTENSIONS = frozenset({".aac", ".mp3", ".ogg", ".opus", ".wma"})


def build_cleanup_plan(scan_root: str | Path, groups: list[DuplicateGroup]) -> CleanupPlan:
    plan = CleanupPlan(scan_root=Path(scan_root), total_groups_found=len(groups))
    for group in groups:
        decision = _plan_group_cleanup(group)
        if decision.eligible_for_cleanup:
            plan.eligible_groups.append(decision)
        else:
            plan.review_only_groups.append(decision)
    return plan


def select_keeper(files: list[DuplicateFileRecord]) -> tuple[DuplicateFileRecord, str]:
    if not files:
        raise ValueError("Cannot select a keeper from an empty duplicate group.")

    keeper = min(files, key=_keeper_sort_key)
    return keeper, _build_keeper_selection_reason(keeper)


def is_lossless_record(duplicate_file: DuplicateFileRecord) -> bool:
    normalized_codec = _normalize_codec(duplicate_file.codec)
    if normalized_codec:
        if normalized_codec.startswith("pcm"):
            return True
        if normalized_codec in LOSSLESS_CODECS:
            return True
        if normalized_codec in LOSSY_CODECS:
            return False

    normalized_extension = duplicate_file.extension.strip().lower()
    if normalized_extension in LOSSLESS_EXTENSIONS:
        return True
    if normalized_extension in LOSSY_EXTENSIONS:
        return False
    return False


def describe_quality_fields(duplicate_file: DuplicateFileRecord) -> dict[str, object]:
    return {
        "path": str(duplicate_file.path),
        "extension": duplicate_file.extension,
        "codec": duplicate_file.codec,
        "lossless": is_lossless_record(duplicate_file),
        "bits_per_sample": duplicate_file.bits_per_sample,
        "sample_rate_hz": duplicate_file.sample_rate_hz,
        "bitrate_bps": duplicate_file.bitrate_bps,
        "size_bytes": duplicate_file.size_bytes,
        "duration_seconds": duplicate_file.duration_seconds,
    }


def _plan_group_cleanup(group: DuplicateGroup) -> CleanupGroupDecision:
    if group.tier not in SAFE_CLEANUP_TIERS:
        return CleanupGroupDecision(
            group=group,
            eligible_for_cleanup=False,
            reason=f"{group.tier} is review-only and excluded from automatic cleanup.",
        )

    if len(group.files) < 2:
        return CleanupGroupDecision(
            group=group,
            eligible_for_cleanup=False,
            reason="Group does not contain enough files for cleanup.",
        )

    keeper, selection_reason = select_keeper(group.files)
    files_to_move = [duplicate_file for duplicate_file in group.files if duplicate_file is not keeper]
    return CleanupGroupDecision(
        group=group,
        eligible_for_cleanup=True,
        reason="Eligible for cleanup because this group is an exact binary duplicate match.",
        keeper=keeper,
        files_to_move=files_to_move,
        keeper_selection_reason=selection_reason,
    )


def _keeper_sort_key(duplicate_file: DuplicateFileRecord) -> tuple[object, ...]:
    normalized_path = str(duplicate_file.path).casefold()
    return (
        0 if is_lossless_record(duplicate_file) else 1,
        -_coalesce_int(duplicate_file.bits_per_sample),
        -_coalesce_int(duplicate_file.sample_rate_hz),
        -_coalesce_int(duplicate_file.bitrate_bps),
        -_coalesce_int(duplicate_file.size_bytes),
        normalized_path,
    )


def _build_keeper_selection_reason(duplicate_file: DuplicateFileRecord) -> str:
    details: list[str] = []
    if is_lossless_record(duplicate_file):
        codec_label = duplicate_file.codec or duplicate_file.extension or "lossless format"
        details.append(f"lossless audio preferred ({codec_label})")
    elif duplicate_file.codec:
        details.append(f"codec={duplicate_file.codec}")

    if duplicate_file.bits_per_sample is not None:
        details.append(f"{duplicate_file.bits_per_sample}-bit")
    if duplicate_file.sample_rate_hz is not None:
        details.append(f"{duplicate_file.sample_rate_hz} Hz")
    if duplicate_file.bitrate_bps is not None:
        details.append(f"{duplicate_file.bitrate_bps} bps")
    details.append(f"{duplicate_file.size_bytes} bytes")
    return (
        "Selected by fidelity ranking: "
        + ", ".join(details)
        + ". Ranking order: lossless, bit depth, sample rate, bitrate, file size, then path."
    )


def _normalize_codec(codec: str | None) -> str | None:
    if codec is None:
        return None
    normalized_codec = codec.strip().casefold()
    return normalized_codec or None


def _coalesce_int(value: int | None) -> int:
    return int(value) if value is not None else 0
