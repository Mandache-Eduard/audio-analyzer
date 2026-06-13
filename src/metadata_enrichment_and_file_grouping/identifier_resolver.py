from __future__ import annotations

from typing import Any, Iterable

from metadata_enrichment_and_file_grouping.fingerprint import FingerprintResult
from metadata_enrichment_and_file_grouping.tag_reader import ExistingAudioMetadata


def resolve_identifier(
    existing_metadata: ExistingAudioMetadata,
    musicbrainz_client: Any,
    acoustid_client: Any,
    fingerprint_service: Any,
    precomputed_fingerprints_by_path: dict[str, FingerprintResult | Exception] | None = None,
) -> dict[str, Any]:
    """
    Decide how to identify one audio file.

    Priority:
    1. Existing MusicBrainz Recording MBID
    2. Existing ISRC
    3. Existing AcoustID
    4. New Chromaprint fingerprint + AcoustID lookup
    """

    if existing_metadata.musicbrainz_recording_id:
        return _resolve_by_existing_mbid(existing_metadata, musicbrainz_client)

    if existing_metadata.isrc:
        return _resolve_by_existing_isrc(existing_metadata, musicbrainz_client)

    if existing_metadata.acoustid_id:
        return _resolve_by_existing_acoustid(
            existing_metadata,
            musicbrainz_client,
            acoustid_client,
        )

    if precomputed_fingerprints_by_path is not None:
        precomputed_fingerprint = precomputed_fingerprints_by_path.get(
            str(existing_metadata.original_path)
        )
        if precomputed_fingerprint is not None:
            return _resolve_by_precomputed_fingerprint(
                existing_metadata,
                musicbrainz_client,
                acoustid_client,
                precomputed_fingerprint,
            )

    return _resolve_by_fingerprint(
        existing_metadata,
        musicbrainz_client,
        acoustid_client,
        fingerprint_service,
    )


def resolve_identifier_batch(
    metadata_rows: Iterable[ExistingAudioMetadata],
    musicbrainz_client: Any,
    acoustid_client: Any,
    fingerprint_service: Any,
    precomputed_fingerprints_by_path: dict[str, FingerprintResult | Exception] | None = None,
) -> list[dict[str, Any]]:
    return [
        resolve_identifier(
            existing_metadata=metadata_row,
            musicbrainz_client=musicbrainz_client,
            acoustid_client=acoustid_client,
            fingerprint_service=fingerprint_service,
            precomputed_fingerprints_by_path=precomputed_fingerprints_by_path,
        )
        for metadata_row in metadata_rows
    ]


def demo_resolve_identifier_batch(
    metadata_rows: Iterable[ExistingAudioMetadata],
    musicbrainz_client: Any,
    acoustid_client: Any,
    fingerprint_service: Any,
    precomputed_fingerprints_by_path: dict[str, FingerprintResult | Exception] | None = None,
) -> list[dict[str, Any]]:
    resolution_rows = resolve_identifier_batch(
        metadata_rows=metadata_rows,
        musicbrainz_client=musicbrainz_client,
        acoustid_client=acoustid_client,
        fingerprint_service=fingerprint_service,
        precomputed_fingerprints_by_path=precomputed_fingerprints_by_path,
    )

    for resolution in resolution_rows:
        print(
            f"status={resolution['status']} | "
            f"source={resolution['source']} | "
            f"recording_mbid={resolution['recording_mbid'] or 'missing'} | "
            f"recording_candidates={len(resolution['candidate_recording_mbids'])} | "
            f"release_candidates={len(resolution['candidate_release_mbids'])}"
        )

    return resolution_rows


def _resolve_by_existing_mbid(
    existing_metadata: ExistingAudioMetadata,
    musicbrainz_client: Any,
) -> dict[str, Any]:
    if musicbrainz_client is None:
        return _build_result(
            status="error",
            source="existing_mbid",
            existing_metadata=existing_metadata,
            recording_mbid=existing_metadata.musicbrainz_recording_id,
            candidate_recording_mbids=[],
            candidate_release_mbids=[],
            result=None,
            error="MusicBrainz client is not configured.",
        )

    try:
        result = musicbrainz_client.lookup_recording_by_mbid(
            existing_metadata.musicbrainz_recording_id
        )
    except Exception as exc:
        return _build_result(
            status="error",
            source="existing_mbid",
            existing_metadata=existing_metadata,
            recording_mbid=existing_metadata.musicbrainz_recording_id,
            candidate_recording_mbids=[],
            candidate_release_mbids=[],
            result=None,
            error=str(exc),
        )

    return _build_result(
        status=_status_from_result(result),
        source="existing_mbid",
        existing_metadata=existing_metadata,
        recording_mbid=existing_metadata.musicbrainz_recording_id,
        candidate_recording_mbids=_extract_musicbrainz_recording_mbids(
            musicbrainz_client,
            result,
            fallback=[existing_metadata.musicbrainz_recording_id],
        ),
        candidate_release_mbids=_extract_musicbrainz_release_mbids(
            musicbrainz_client,
            result,
        ),
        result=result,
        error=None,
    )


def _resolve_by_existing_isrc(
    existing_metadata: ExistingAudioMetadata,
    musicbrainz_client: Any,
) -> dict[str, Any]:
    if musicbrainz_client is None:
        return _build_result(
            status="error",
            source="existing_isrc",
            existing_metadata=existing_metadata,
            recording_mbid=None,
            candidate_recording_mbids=[],
            candidate_release_mbids=[],
            result=None,
            error="MusicBrainz client is not configured.",
        )

    try:
        result = musicbrainz_client.lookup_recordings_by_isrc(existing_metadata.isrc)
    except Exception as exc:
        return _build_result(
            status="error",
            source="existing_isrc",
            existing_metadata=existing_metadata,
            recording_mbid=None,
            candidate_recording_mbids=[],
            candidate_release_mbids=[],
            result=None,
            error=str(exc),
        )

    candidates = _extract_musicbrainz_recording_mbids(musicbrainz_client, result)
    return _build_result(
        status=_status_from_result(result),
        source="existing_isrc",
        existing_metadata=existing_metadata,
        recording_mbid=candidates[0] if len(candidates) == 1 else None,
        candidate_recording_mbids=candidates,
        candidate_release_mbids=_extract_musicbrainz_release_mbids(
            musicbrainz_client,
            result,
        ),
        result=result,
        error=None,
    )


def _resolve_by_existing_acoustid(
    existing_metadata: ExistingAudioMetadata,
    musicbrainz_client: Any,
    acoustid_client: Any,
) -> dict[str, Any]:
    if acoustid_client is None:
        return _build_result(
            status="error",
            source="existing_acoustid",
            existing_metadata=existing_metadata,
            recording_mbid=None,
            candidate_recording_mbids=[],
            candidate_release_mbids=[],
            result=None,
            error="AcoustID client is not configured.",
        )

    try:
        result = acoustid_client.lookup_by_track_id(existing_metadata.acoustid_id)
    except Exception as exc:
        return _build_result(
            status="error",
            source="existing_acoustid",
            existing_metadata=existing_metadata,
            recording_mbid=None,
            candidate_recording_mbids=[],
            candidate_release_mbids=[],
            result=None,
            error=str(exc),
        )

    candidates = _extract_acoustid_recording_mbids(acoustid_client, result)
    candidate_release_mbids, release_lookup_error = _resolve_acoustid_release_candidates(
        acoustid_client=acoustid_client,
        musicbrainz_client=musicbrainz_client,
        result=result,
        recording_mbids=candidates,
    )
    candidate_release_group_mbids = _extract_acoustid_release_group_mbids(
        acoustid_client,
        result,
    )
    status = _status_from_acoustid_result(result, candidates, candidate_release_mbids)
    return _build_result(
        status=status,
        source="existing_acoustid",
        existing_metadata=existing_metadata,
        recording_mbid=candidates[0] if len(candidates) == 1 else None,
        candidate_recording_mbids=candidates,
        candidate_release_mbids=candidate_release_mbids,
        candidate_release_group_mbids=candidate_release_group_mbids,
        result=result,
        error=_format_acoustid_error(result) if status == "error" else release_lookup_error,
    )


def _resolve_by_fingerprint(
    existing_metadata: ExistingAudioMetadata,
    musicbrainz_client: Any,
    acoustid_client: Any,
    fingerprint_service: Any,
) -> dict[str, Any]:
    if fingerprint_service is None:
        return _build_result(
            status="unmatched",
            source="fingerprint_failed",
            existing_metadata=existing_metadata,
            recording_mbid=None,
            candidate_recording_mbids=[],
            candidate_release_mbids=[],
            result=None,
            error="Fingerprint service is not configured.",
        )

    try:
        fingerprint_result = fingerprint_service.create_fingerprint(
            existing_metadata.original_path
        )
    except NotImplementedError as exc:
        return _build_result(
            status="unmatched",
            source="fingerprint_failed",
            existing_metadata=existing_metadata,
            recording_mbid=None,
            candidate_recording_mbids=[],
            candidate_release_mbids=[],
            result=None,
            error=str(exc),
        )
    except Exception as exc:
        return _build_result(
            status="unmatched",
            source="fingerprint_failed",
            existing_metadata=existing_metadata,
            recording_mbid=None,
            candidate_recording_mbids=[],
            candidate_release_mbids=[],
            result=None,
            error=str(exc),
        )

    return _resolve_by_fingerprint_result(
        existing_metadata,
        musicbrainz_client,
        acoustid_client,
        fingerprint_result,
    )


def _resolve_by_precomputed_fingerprint(
    existing_metadata: ExistingAudioMetadata,
    musicbrainz_client: Any,
    acoustid_client: Any,
    precomputed_fingerprint: FingerprintResult | Exception,
) -> dict[str, Any]:
    if isinstance(precomputed_fingerprint, Exception):
        return _build_result(
            status="unmatched",
            source="fingerprint_failed",
            existing_metadata=existing_metadata,
            recording_mbid=None,
            candidate_recording_mbids=[],
            candidate_release_mbids=[],
            result=None,
            error=str(precomputed_fingerprint),
        )

    return _resolve_by_fingerprint_result(
        existing_metadata,
        musicbrainz_client,
        acoustid_client,
        precomputed_fingerprint,
    )


def _resolve_by_fingerprint_result(
    existing_metadata: ExistingAudioMetadata,
    musicbrainz_client: Any,
    acoustid_client: Any,
    fingerprint_result: FingerprintResult,
) -> dict[str, Any]:
    fingerprint = getattr(fingerprint_result, "fingerprint", None)
    duration_seconds = getattr(fingerprint_result, "duration_seconds", None)
    if not fingerprint or duration_seconds is None:
        return _build_result(
            status="unmatched",
            source="fingerprint_failed",
            existing_metadata=existing_metadata,
            recording_mbid=None,
            candidate_recording_mbids=[],
            candidate_release_mbids=[],
            result=None,
            error="Could not generate Chromaprint fingerprint.",
        )

    if acoustid_client is None:
        return _build_result(
            status="error",
            source="lookup_failed",
            existing_metadata=existing_metadata,
            recording_mbid=None,
            candidate_recording_mbids=[],
            candidate_release_mbids=[],
            result=None,
            error="AcoustID client is not configured.",
        )

    try:
        result = acoustid_client.lookup_by_fingerprint(
            fingerprint=fingerprint,
            duration_seconds=duration_seconds,
        )
    except Exception as exc:
        return _build_result(
            status="error",
            source="lookup_failed",
            existing_metadata=existing_metadata,
            recording_mbid=None,
            candidate_recording_mbids=[],
            candidate_release_mbids=[],
            result=None,
            error=str(exc),
        )

    candidates = _extract_acoustid_recording_mbids(acoustid_client, result)
    candidate_release_mbids, release_lookup_error = _resolve_acoustid_release_candidates(
        acoustid_client=acoustid_client,
        musicbrainz_client=musicbrainz_client,
        result=result,
        recording_mbids=candidates,
    )
    candidate_release_group_mbids = _extract_acoustid_release_group_mbids(
        acoustid_client,
        result,
    )
    status = _status_from_acoustid_result(result, candidates, candidate_release_mbids)
    return _build_result(
        status=status,
        source="fingerprint_acoustid",
        existing_metadata=existing_metadata,
        recording_mbid=candidates[0] if len(candidates) == 1 else None,
        candidate_recording_mbids=candidates,
        candidate_release_mbids=candidate_release_mbids,
        candidate_release_group_mbids=candidate_release_group_mbids,
        result=result,
        error=_format_acoustid_error(result) if status == "error" else release_lookup_error,
        acoustid_id=_extract_acoustid_identifier(acoustid_client, result),
    )


def _build_result(
    *,
    status: str,
    source: str,
    existing_metadata: ExistingAudioMetadata,
    recording_mbid: str | None,
    candidate_recording_mbids: list[str],
    candidate_release_mbids: list[str],
    result: Any,
    error: str | None,
    candidate_release_group_mbids: list[str] | None = None,
    acoustid_id: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "source": source,
        "original_path": str(existing_metadata.original_path),
        "recording_mbid": recording_mbid,
        "candidate_recording_mbids": candidate_recording_mbids,
        "candidate_release_mbids": candidate_release_mbids,
        "candidate_release_group_mbids": candidate_release_group_mbids or [],
        "isrc": existing_metadata.isrc,
        "acoustid_id": acoustid_id if acoustid_id is not None else existing_metadata.acoustid_id,
        "track_number": None,
        "disc_number": None,
        "result": result,
        "raw_result": result,
        "error": error,
    }


def _status_from_result(result: Any) -> str:
    return "resolved" if result else "unmatched"


def _status_from_acoustid_result(
    result: Any,
    candidate_recording_mbids: list[str],
    candidate_release_mbids: list[str],
) -> str:
    if not result:
        return "unmatched"
    if _acoustid_response_status(result) == "error":
        return "error"
    return "resolved" if candidate_recording_mbids or candidate_release_mbids else "unmatched"


def _acoustid_response_status(result: Any) -> str | None:
    if not isinstance(result, dict):
        return None

    status = result.get("status")
    return status.strip().casefold() if isinstance(status, str) else None


def _format_acoustid_error(result: Any) -> str:
    if not isinstance(result, dict):
        return "AcoustID returned an error response."

    error = result.get("error")
    if not isinstance(error, dict):
        return "AcoustID returned status=error without an error payload."

    code = error.get("code")
    message = error.get("message")
    details: list[str] = []
    if code is not None:
        details.append(f"code={code}")
    if isinstance(message, str) and message.strip():
        details.append(f"message={message.strip()}")

    if not details:
        return "AcoustID returned status=error with an empty error payload."
    return "AcoustID returned status=error ({})".format(", ".join(details))


def _extract_musicbrainz_recording_mbids(
    musicbrainz_client: Any,
    result: Any,
    fallback: list[str] | None = None,
) -> list[str]:
    extractor = getattr(musicbrainz_client, "extract_recording_mbids", None)
    if callable(extractor):
        extracted_mbids = _deduplicate_strings(extractor(result))
        if extracted_mbids:
            return extracted_mbids

    return _deduplicate_strings(fallback or [])


def _extract_acoustid_recording_mbids(
    acoustid_client: Any,
    result: Any,
    fallback: list[str] | None = None,
) -> list[str]:
    extractor = getattr(acoustid_client, "extract_recording_mbids", None)
    if callable(extractor):
        extracted_mbids = _deduplicate_strings(extractor(result))
        if extracted_mbids:
            return extracted_mbids

    return _deduplicate_strings(fallback or [])


def _extract_acoustid_identifier(acoustid_client: Any, result: Any) -> str | None:
    extractor = getattr(acoustid_client, "extract_acoustid_id", None)
    if callable(extractor):
        return extractor(result)
    return None

def _extract_acoustid_release_mbids(acoustid_client: Any, result: Any) -> list[str]:
    extractor = getattr(acoustid_client, "extract_release_mbids", None)
    if callable(extractor):
        extracted_mbids = _deduplicate_strings(extractor(result))
        if extracted_mbids:
            return extracted_mbids

    return []

def _extract_acoustid_release_group_mbids(acoustid_client: Any, result: Any) -> list[str]:
    extractor = getattr(acoustid_client, "extract_release_group_mbids", None)
    if callable(extractor):
        extracted_mbids = _deduplicate_strings(extractor(result))
        if extracted_mbids:
            return extracted_mbids

    return []


def _extract_musicbrainz_release_mbids(
    musicbrainz_client: Any,
    result: Any,
    fallback: list[str] | None = None,
) -> list[str]:
    extractor = getattr(musicbrainz_client, "extract_release_mbids", None)
    if callable(extractor):
        extracted_mbids = _deduplicate_strings(extractor(result))
        if extracted_mbids:
            return extracted_mbids

    return _deduplicate_strings(fallback or [])

def _resolve_acoustid_release_candidates(
    *,
    acoustid_client: Any,
    musicbrainz_client: Any,
    result: Any,
    recording_mbids: list[str],
) -> tuple[list[str], str | None]:
    direct_release_mbids = _extract_acoustid_release_mbids(acoustid_client, result)
    if direct_release_mbids:
        return direct_release_mbids, None

    return _lookup_release_mbids_for_recordings(musicbrainz_client, recording_mbids)


def _lookup_release_mbids_for_recordings(
    musicbrainz_client: Any,
    recording_mbids: list[str],
) -> tuple[list[str], str | None]:
    if not recording_mbids:
        return [], None

    if musicbrainz_client is None:
        return [], "MusicBrainz client is not configured for release candidate expansion."

    collected_release_mbids: list[str] = []
    for recording_mbid in _deduplicate_strings(recording_mbids):
        try:
            result = musicbrainz_client.lookup_recording_by_mbid(recording_mbid)
        except Exception as exc:
            return (
                _deduplicate_strings(collected_release_mbids),
                f"MusicBrainz release candidate expansion failed for recording {recording_mbid}: {exc}",
            )

        collected_release_mbids.extend(
            _extract_musicbrainz_release_mbids(musicbrainz_client, result)
        )

    return _deduplicate_strings(collected_release_mbids), None


def _deduplicate_strings(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered_values: list[str] = []

    for value in values:
        cleaned_value = value.strip()
        if not cleaned_value or cleaned_value in seen:
            continue
        seen.add(cleaned_value)
        ordered_values.append(cleaned_value)

    return ordered_values
