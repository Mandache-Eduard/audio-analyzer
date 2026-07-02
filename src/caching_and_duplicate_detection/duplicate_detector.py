from __future__ import annotations

import hashlib
from collections import defaultdict
from itertools import combinations
from pathlib import Path

from tqdm import tqdm

from caching_and_duplicate_detection.audio_cache import AudioCache
from caching_and_duplicate_detection.cache_models import RESOLVER_VERSION
from caching_and_duplicate_detection.duplicate_cleanup_cli import run_cleanup_cli
from caching_and_duplicate_detection.duplicate_models import DuplicateFileRecord, DuplicateGroup
from caching_and_duplicate_detection.duplicate_reporter import (
    print_no_supported_audio_files_report,
    print_duplicate_report,
    write_duplicate_report_csv,
)
from config import (
    ACOUSTID_API_KEY,
    MUSICBRAINZ_APP_NAME,
    MUSICBRAINZ_APP_VERSION,
    MUSICBRAINZ_CONTACT_EMAIL,
)
from metadata_tagging_and_cluster_grouping.acoustid_client import AcoustIdClient
from metadata_tagging_and_cluster_grouping.file_scanner import scan_audio_files
from metadata_tagging_and_cluster_grouping.fingerprint import FingerprintService
from metadata_tagging_and_cluster_grouping.identifier_resolver import resolve_identifier
from metadata_tagging_and_cluster_grouping.musicbrainz_client import MusicBrainzClient
from metadata_tagging_and_cluster_grouping.tag_reader import ExistingAudioMetadata, read_existing_metadata

DEFAULT_DURATION_TOLERANCE_SECONDS = 2.0
# Low-score AcoustID matches are still retained in cached metadata and reports,
# but only higher-confidence matches are promoted to the strong acoustid_track tier.
MIN_STRONG_ACOUSTID_SCORE = 0.85

TIER_CONFIDENCE_LABELS = {
    "binary_hash": "Exact binary duplicate",
    "acoustid_track": "Strong perceptual duplicate",
    "recording_mbid": "Likely same recording",
    "metadata_duration_candidate": "Weak metadata-duration candidate",
}

TIER_RECOMMENDED_ACTIONS = {
    "binary_hash": "Review manually. These files have identical SHA-256 content hashes. No files were changed.",
    "acoustid_track": "Review manually. These files share an AcoustID track match, but encodes and releases may differ.",
    "recording_mbid": "Review manually. These files likely point to the same MusicBrainz recording, but release context may differ.",
    "metadata_duration_candidate": "Review manually. This is a weak candidate based on metadata similarity and duration only.",
}


def run_duplicate_detection(
    folder_path: str | Path,
    *,
    cache: AudioCache | None = None,
    refresh_cache: bool = False,
    output_path: str | Path | None = None,
    cleanup: bool = False,
    duration_tolerance_seconds: float = DEFAULT_DURATION_TOLERANCE_SECONDS,
) -> list[DuplicateGroup]:
    if cleanup:
        from caching_and_duplicate_detection.trash_backend import SendToTrashBackend

        # Validate cleanup support before scanning so a missing runtime dependency
        # does not surface only after duplicate detection completes.
        SendToTrashBackend()

    scanned_files = scan_audio_files(folder_path)
    if not scanned_files:
        print_no_supported_audio_files_report()
        if output_path is not None:
            report_path = write_duplicate_report_csv([], output_path)
            print(f"CSV report saved to: {report_path}")
        return []

    groups = _detect_duplicates_from_scanned_files(
        scanned_files,
        cache=cache,
        refresh_cache=refresh_cache,
        duration_tolerance_seconds=duration_tolerance_seconds,
    )
    print_duplicate_report(groups)
    if output_path is not None:
        report_path = write_duplicate_report_csv(groups, output_path)
        print(f"CSV report saved to: {report_path}")
    if cleanup:
        run_cleanup_cli(
            scan_root=folder_path,
            groups=groups,
            cache=cache,
        )
    return groups


def detect_duplicates(
    folder_path: str | Path,
    *,
    cache: AudioCache | None = None,
    refresh_cache: bool = False,
    duration_tolerance_seconds: float = DEFAULT_DURATION_TOLERANCE_SECONDS,
) -> list[DuplicateGroup]:
    scanned_files = scan_audio_files(folder_path)
    if not scanned_files:
        return []

    return _detect_duplicates_from_scanned_files(
        scanned_files,
        cache=cache,
        refresh_cache=refresh_cache,
        duration_tolerance_seconds=duration_tolerance_seconds,
    )


def _detect_duplicates_from_scanned_files(
    scanned_files: list,
    *,
    cache: AudioCache | None,
    refresh_cache: bool,
    duration_tolerance_seconds: float,
) -> list[DuplicateGroup]:
    metadata_rows = [
        read_existing_metadata(scanned_file)
        for scanned_file in tqdm(scanned_files, desc="Metadata scanned", unit="file")
    ]
    file_ids_by_path = _upsert_metadata_rows(metadata_rows, cache)

    musicbrainz_client, acoustid_client, fingerprint_service = _build_optional_services(
        cache=cache,
        refresh_cache=refresh_cache,
    )
    precomputed_fingerprints_by_path = _precompute_fingerprints(
        metadata_rows,
        file_ids_by_path=file_ids_by_path,
        cache=cache,
        refresh_cache=refresh_cache,
        fingerprint_service=fingerprint_service,
    )

    resolution_rows = [
        resolve_identifier(
            existing_metadata=metadata_row,
            musicbrainz_client=musicbrainz_client,
            acoustid_client=acoustid_client,
            fingerprint_service=fingerprint_service,
            precomputed_fingerprints_by_path=precomputed_fingerprints_by_path,
            cache=cache,
            refresh_cache=refresh_cache,
        )
        for metadata_row in tqdm(metadata_rows, desc="Identifiers resolved", unit="file")
    ]

    duplicate_files = [
        _build_duplicate_file_record(
            metadata_row,
            resolution_row,
            file_ids_by_path=file_ids_by_path,
        )
        for metadata_row, resolution_row in zip(metadata_rows, resolution_rows)
    ]

    _hydrate_binary_hashes(duplicate_files, cache=cache)
    return _build_duplicate_groups(
        duplicate_files,
        duration_tolerance_seconds=duration_tolerance_seconds,
    )


def _build_optional_services(
    *,
    cache: AudioCache | None,
    refresh_cache: bool,
) -> tuple[MusicBrainzClient | None, AcoustIdClient | None, FingerprintService | None]:
    musicbrainz_client: MusicBrainzClient | None = None
    acoustid_client: AcoustIdClient | None = None

    if MUSICBRAINZ_CONTACT_EMAIL:
        musicbrainz_client = MusicBrainzClient(
            app_name=MUSICBRAINZ_APP_NAME,
            app_version=MUSICBRAINZ_APP_VERSION,
            contact_email=MUSICBRAINZ_CONTACT_EMAIL,
        )

    if ACOUSTID_API_KEY:
        acoustid_client = AcoustIdClient(api_key=ACOUSTID_API_KEY)

    fingerprint_service = FingerprintService(cache=cache, refresh_cache=refresh_cache)
    return musicbrainz_client, acoustid_client, fingerprint_service


def _upsert_metadata_rows(
    metadata_rows: list[ExistingAudioMetadata],
    cache: AudioCache | None,
) -> dict[str, int | None]:
    file_ids_by_path: dict[str, int | None] = {}
    for metadata_row in metadata_rows:
        file_key = str(metadata_row.original_path)
        if cache is None:
            file_ids_by_path[file_key] = None
            continue
        try:
            file_ids_by_path[file_key] = cache.upsert_file(
                metadata_row.original_path,
                audio_info=_metadata_row_audio_info(metadata_row),
            )
        except OSError:
            file_ids_by_path[file_key] = None
    return file_ids_by_path


def _precompute_fingerprints(
    metadata_rows: list[ExistingAudioMetadata],
    *,
    file_ids_by_path: dict[str, int | None],
    cache: AudioCache | None,
    refresh_cache: bool,
    fingerprint_service: FingerprintService | None,
) -> dict[str, object]:
    if fingerprint_service is None:
        return {}

    fingerprint_candidates = []
    for metadata_row in metadata_rows:
        if not _needs_fingerprint(metadata_row):
            continue
        if cache is not None and not refresh_cache:
            file_id = file_ids_by_path.get(str(metadata_row.original_path))
            if file_id is not None and cache.get_cached_metadata_resolution(file_id, RESOLVER_VERSION):
                continue
        fingerprint_candidates.append(metadata_row.original_path)

    if not fingerprint_candidates:
        return {}

    return fingerprint_service.create_fingerprint_batch(fingerprint_candidates)


def _build_duplicate_file_record(
    metadata_row: ExistingAudioMetadata,
    resolution_row: dict,
    *,
    file_ids_by_path: dict[str, int | None],
) -> DuplicateFileRecord:
    candidate_release_mbids = resolution_row.get("candidate_release_mbids", [])
    release_mbid = metadata_row.release_mbid
    if release_mbid is None and isinstance(candidate_release_mbids, list):
        normalized_candidates = [
            candidate.strip()
            for candidate in candidate_release_mbids
            if isinstance(candidate, str) and candidate.strip()
        ]
        if len(normalized_candidates) == 1:
            release_mbid = normalized_candidates[0]

    return DuplicateFileRecord(
        file_id=file_ids_by_path.get(str(metadata_row.original_path)),
        path=metadata_row.original_path,
        extension=metadata_row.extension,
        codec=metadata_row.codec,
        size_bytes=metadata_row.file_size,
        duration_seconds=metadata_row.duration_seconds,
        bitrate_bps=metadata_row.bitrate_bps,
        sample_rate_hz=metadata_row.sample_rate_hz,
        channels=metadata_row.channels,
        bits_per_sample=metadata_row.bits_per_sample,
        content_hash=None,
        acoustid_id=_coalesce_identifier(resolution_row.get("acoustid_id"), metadata_row.acoustid_id),
        acoustid_score=_coerce_float(resolution_row.get("acoustid_score")),
        recording_mbid=_coalesce_identifier(
            resolution_row.get("recording_mbid"),
            metadata_row.musicbrainz_recording_id,
        ),
        release_mbid=release_mbid,
        source=_coalesce_identifier(resolution_row.get("source"), "embedded_tags"),
        title=metadata_row.title,
        artist=metadata_row.artist,
    )


def _hydrate_binary_hashes(
    duplicate_files: list[DuplicateFileRecord],
    *,
    cache: AudioCache | None,
) -> None:
    size_buckets: dict[int, list[DuplicateFileRecord]] = defaultdict(list)
    for duplicate_file in duplicate_files:
        size_buckets[duplicate_file.size_bytes].append(duplicate_file)

    for same_size_files in tqdm(size_buckets.values(), desc="Binary hashes", unit="bucket"):
        if len(same_size_files) < 2:
            continue
        for duplicate_file in same_size_files:
            content_hash = _get_or_compute_content_hash(duplicate_file, cache=cache)
            if content_hash is None:
                continue
            duplicate_file.content_hash = content_hash


def _get_or_compute_content_hash(
    duplicate_file: DuplicateFileRecord,
    *,
    cache: AudioCache | None,
) -> str | None:
    if duplicate_file.content_hash:
        return duplicate_file.content_hash

    if cache is not None and duplicate_file.file_id is not None:
        cached_hash = cache.get_content_hash(duplicate_file.file_id)
        if cached_hash:
            return cached_hash

    try:
        sha256 = hashlib.sha256()
        with duplicate_file.path.open("rb") as input_file:
            for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
                sha256.update(chunk)
        content_hash = sha256.hexdigest()
    except OSError:
        return None

    if cache is not None and duplicate_file.file_id is not None:
        cache.save_content_hash(duplicate_file.file_id, content_hash)

    return content_hash


def _build_duplicate_groups(
    duplicate_files: list[DuplicateFileRecord],
    *,
    duration_tolerance_seconds: float,
) -> list[DuplicateGroup]:
    groups: list[DuplicateGroup] = []
    covered_pairs: set[frozenset[str]] = set()

    stronger_buckets = [
        ("binary_hash", _group_by_binary_hash(duplicate_files)),
        ("acoustid_track", _group_by_acoustid(duplicate_files)),
        ("recording_mbid", _group_by_recording_mbid(duplicate_files)),
        (
            "metadata_duration_candidate",
            _group_by_metadata_duration(duplicate_files, duration_tolerance_seconds),
        ),
    ]

    for tier, buckets in stronger_buckets:
        for bucket in buckets:
            for subgroup in _build_uncovered_subgroups(bucket, covered_pairs):
                pair_keys = _build_pair_keys(subgroup)
                if not pair_keys:
                    continue
                groups.append(
                    DuplicateGroup(
                        group_id=len(groups) + 1,
                        tier=tier,
                        confidence_label=TIER_CONFIDENCE_LABELS[tier],
                        recommended_action=TIER_RECOMMENDED_ACTIONS[tier],
                        files=sorted(subgroup, key=lambda item: str(item.path).lower()),
                    )
                )
                covered_pairs.update(pair_keys)

    return groups


def _group_by_binary_hash(
    duplicate_files: list[DuplicateFileRecord],
) -> list[list[DuplicateFileRecord]]:
    grouped_files: dict[str, list[DuplicateFileRecord]] = defaultdict(list)
    for duplicate_file in duplicate_files:
        if duplicate_file.content_hash:
            grouped_files[duplicate_file.content_hash].append(duplicate_file)
    return [bucket for bucket in grouped_files.values() if len(bucket) > 1]


def _group_by_acoustid(
    duplicate_files: list[DuplicateFileRecord],
) -> list[list[DuplicateFileRecord]]:
    grouped_files: dict[str, list[DuplicateFileRecord]] = defaultdict(list)
    for duplicate_file in duplicate_files:
        if (
            duplicate_file.acoustid_id
            and duplicate_file.acoustid_score is not None
            and duplicate_file.acoustid_score >= MIN_STRONG_ACOUSTID_SCORE
        ):
            grouped_files[duplicate_file.acoustid_id].append(duplicate_file)
    return [bucket for bucket in grouped_files.values() if len(bucket) > 1]


def _group_by_recording_mbid(
    duplicate_files: list[DuplicateFileRecord],
) -> list[list[DuplicateFileRecord]]:
    grouped_files: dict[str, list[DuplicateFileRecord]] = defaultdict(list)
    for duplicate_file in duplicate_files:
        if duplicate_file.recording_mbid:
            grouped_files[duplicate_file.recording_mbid].append(duplicate_file)
    return [bucket for bucket in grouped_files.values() if len(bucket) > 1]


def _group_by_metadata_duration(
    duplicate_files: list[DuplicateFileRecord],
    duration_tolerance_seconds: float,
) -> list[list[DuplicateFileRecord]]:
    metadata_buckets: dict[tuple[str, str], list[DuplicateFileRecord]] = defaultdict(list)
    for duplicate_file in duplicate_files:
        normalized_title = _normalize_metadata_token(duplicate_file.title)
        normalized_artist = _normalize_metadata_token(duplicate_file.artist)
        if normalized_title is None or normalized_artist is None:
            continue
        if duplicate_file.duration_seconds is None:
            continue
        metadata_buckets[(normalized_title, normalized_artist)].append(duplicate_file)

    grouped_candidates: list[list[DuplicateFileRecord]] = []
    for bucket in metadata_buckets.values():
        if len(bucket) < 2:
            continue
        grouped_candidates.extend(
            _cluster_by_duration(bucket, duration_tolerance_seconds=duration_tolerance_seconds)
        )
    return grouped_candidates


def _cluster_by_duration(
    bucket: list[DuplicateFileRecord],
    *,
    duration_tolerance_seconds: float,
) -> list[list[DuplicateFileRecord]]:
    adjacency: dict[int, set[int]] = {index: set() for index in range(len(bucket))}
    for left_index, right_index in combinations(range(len(bucket)), 2):
        left_duration = bucket[left_index].duration_seconds
        right_duration = bucket[right_index].duration_seconds
        if left_duration is None or right_duration is None:
            continue
        if abs(left_duration - right_duration) <= duration_tolerance_seconds:
            adjacency[left_index].add(right_index)
            adjacency[right_index].add(left_index)

    visited: set[int] = set()
    clusters: list[list[DuplicateFileRecord]] = []
    for start_index in range(len(bucket)):
        if start_index in visited or not adjacency[start_index]:
            continue
        stack = [start_index]
        cluster_indices: list[int] = []
        while stack:
            current_index = stack.pop()
            if current_index in visited:
                continue
            visited.add(current_index)
            cluster_indices.append(current_index)
            stack.extend(sorted(adjacency[current_index] - visited, reverse=True))
        if len(cluster_indices) > 1:
            clusters.append([bucket[index] for index in sorted(cluster_indices)])

    return clusters


def _build_pair_keys(bucket: list[DuplicateFileRecord]) -> set[frozenset[str]]:
    pair_keys: set[frozenset[str]] = set()
    for left_file, right_file in combinations(bucket, 2):
        pair_keys.add(
            frozenset(
                {
                    _duplicate_file_identity_key(left_file),
                    _duplicate_file_identity_key(right_file),
                }
            )
        )
    return pair_keys


def _build_uncovered_subgroups(
    bucket: list[DuplicateFileRecord],
    covered_pairs: set[frozenset[str]],
) -> list[list[DuplicateFileRecord]]:
    if len(bucket) < 2:
        return []

    all_pair_keys = _build_pair_keys(bucket)
    if not all_pair_keys:
        return []

    uncovered_pair_keys = all_pair_keys - covered_pairs
    if not uncovered_pair_keys:
        return []
    if uncovered_pair_keys == all_pair_keys:
        return [bucket]

    files_by_identity = {
        _duplicate_file_identity_key(duplicate_file): duplicate_file
        for duplicate_file in bucket
    }
    uncovered_adjacency = _build_uncovered_adjacency(bucket, uncovered_pair_keys)
    subgroups: list[list[DuplicateFileRecord]] = []
    emitted_pair_keys: set[frozenset[str]] = set()

    for component_keys in _iter_uncovered_components(
        bucket,
        files_by_identity=files_by_identity,
        uncovered_adjacency=uncovered_adjacency,
    ):
        component_files = [
            files_by_identity[file_key]
            for file_key in _ordered_bucket_keys(bucket)
            if file_key in component_keys
        ]
        if len(component_files) < 2:
            continue

        component_pair_keys = _build_pair_keys(component_files)
        component_uncovered_pair_keys = {
            pair_key for pair_key in uncovered_pair_keys if pair_key.issubset(component_keys)
        }
        if component_pair_keys == component_uncovered_pair_keys:
            subgroups.append(component_files)
            emitted_pair_keys.update(component_uncovered_pair_keys)
            continue

        for left_file, right_file in combinations(component_files, 2):
            pair_key = frozenset(
                {
                    _duplicate_file_identity_key(left_file),
                    _duplicate_file_identity_key(right_file),
                }
            )
            if pair_key not in uncovered_pair_keys or pair_key in emitted_pair_keys:
                continue
            subgroups.append([left_file, right_file])
            emitted_pair_keys.add(pair_key)

    return subgroups


def _build_uncovered_adjacency(
    bucket: list[DuplicateFileRecord],
    uncovered_pair_keys: set[frozenset[str]],
) -> dict[str, set[str]]:
    ordered_bucket_keys = _ordered_bucket_keys(bucket)
    adjacency = {file_key: set() for file_key in ordered_bucket_keys}
    for pair_key in uncovered_pair_keys:
        left_key, right_key = sorted(pair_key)
        adjacency.setdefault(left_key, set()).add(right_key)
        adjacency.setdefault(right_key, set()).add(left_key)
    return adjacency


def _iter_uncovered_components(
    bucket: list[DuplicateFileRecord],
    *,
    files_by_identity: dict[str, DuplicateFileRecord],
    uncovered_adjacency: dict[str, set[str]],
) -> list[set[str]]:
    components: list[set[str]] = []
    visited: set[str] = set()
    for file_key in _ordered_bucket_keys(bucket):
        if file_key in visited or not uncovered_adjacency.get(file_key):
            continue
        stack = [file_key]
        component: set[str] = set()
        while stack:
            current_key = stack.pop()
            if current_key in visited:
                continue
            if current_key not in files_by_identity:
                continue
            visited.add(current_key)
            component.add(current_key)
            stack.extend(
                neighbor_key
                for neighbor_key in sorted(uncovered_adjacency.get(current_key, set()), reverse=True)
                if neighbor_key not in visited
            )
        if len(component) > 1:
            components.append(component)
    return components


def _ordered_bucket_keys(bucket: list[DuplicateFileRecord]) -> list[str]:
    return [_duplicate_file_identity_key(duplicate_file) for duplicate_file in bucket]


def _duplicate_file_identity_key(duplicate_file: DuplicateFileRecord) -> str:
    if duplicate_file.file_id is not None:
        return f"file_id:{duplicate_file.file_id}"
    return f"path:{duplicate_file.path}"


def _metadata_row_audio_info(metadata_row: ExistingAudioMetadata) -> dict[str, object]:
    return {
        "extension": metadata_row.extension,
        "duration_seconds": metadata_row.duration_seconds,
        "codec": metadata_row.codec,
        "bitrate_bps": metadata_row.bitrate_bps,
        "sample_rate_hz": metadata_row.sample_rate_hz,
        "channels": metadata_row.channels,
        "bits_per_sample": metadata_row.bits_per_sample,
    }


def _needs_fingerprint(metadata_row: ExistingAudioMetadata) -> bool:
    return (
        not metadata_row.musicbrainz_recording_id
        and not metadata_row.isrc
        and not metadata_row.acoustid_id
    )


def _normalize_metadata_token(value: str | None) -> str | None:
    if value is None:
        return None
    filtered = "".join(character for character in value.casefold() if character.isalnum())
    return filtered or None


def _coalesce_identifier(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str):
            cleaned_value = value.strip()
            if cleaned_value:
                return cleaned_value
    return None


def _coerce_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
