from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

MINIMUM_SELECTION_THRESHOLD = 80
SELECTION_MARGIN = 25
SHORTLIST_LIMIT = 3
SUMMARY_DOMINANT_MATCH_RATIO = 0.85
SUMMARY_DOMINANT_MIN_GAP = 3
SUMMARY_DOMINANT_MIN_GAP_RATIO = 0.35
SUMMARY_DOMINANT_MAX_TRACK_COUNT_DIFFERENCE_RATIO = 0.33


def select_release_for_group(
    resolved_results: Iterable[dict[str, Any]] | dict[str, Any],
    musicbrainz_client: Any,
) -> dict[str, Any]:
    cluster = resolved_results if isinstance(resolved_results, dict) else None
    resolved_result_list = _coerce_resolved_result_list(resolved_results)
    local_tracks = build_local_album_group(resolved_result_list)
    input_track_count = len(local_tracks)
    matched_track_count = sum(1 for track in local_tracks if track["candidate_recording_mbids"])

    candidate_counts = _coerce_candidate_release_counts(cluster, resolved_result_list)
    if not candidate_counts:
        return {
            "status": "unmatched",
            "selected_release_mbid": None,
            "selected_release": None,
            "candidate_scores": [],
            "track_count": input_track_count,
            "matched_track_count": matched_track_count,
            "reason": "No release candidates were present in the resolved recording results.",
            "error": None,
        }

    summary_scores = [
        _build_summary_candidate_score(
            release_mbid=release_mbid,
            occurrence_count=occurrence_count,
            input_track_count=input_track_count,
        )
        for release_mbid, occurrence_count in candidate_counts.items()
    ]
    ranked_summary_scores = sorted(
        summary_scores,
        key=lambda item: (
            -item["score"],
            -item["matched_recordings"],
            item["release_mbid"],
        ),
    )
    shortlisted_candidates = ranked_summary_scores[:SHORTLIST_LIMIT]

    candidate_scores_by_mbid = {
        candidate_score["release_mbid"]: candidate_score
        for candidate_score in ranked_summary_scores
    }

    detailed_candidate_scores: list[dict[str, Any]] = []
    for index, candidate_score in enumerate(shortlisted_candidates):
        release_mbid = candidate_score["release_mbid"]
        if musicbrainz_client is None:
            return {
                "status": "error",
                "selected_release_mbid": None,
                "selected_release": None,
                "candidate_scores": ranked_summary_scores,
                "track_count": input_track_count,
                "matched_track_count": matched_track_count,
                "reason": "Top candidates were shortlisted but MusicBrainz client is not configured.",
                "error": "MusicBrainz client is not configured.",
            }

        try:
            detailed_release = musicbrainz_client.lookup_release_by_mbid(release_mbid)
        except Exception as exc:
            return {
                "status": "error",
                "selected_release_mbid": release_mbid,
                "selected_release": None,
                "candidate_scores": ranked_summary_scores,
                "track_count": input_track_count,
                "matched_track_count": matched_track_count,
                "reason": "Detailed release lookup failed for a shortlisted candidate.",
                "error": str(exc),
            }

        rescored_candidate = _score_release_detail(
            candidate_scores_by_mbid[release_mbid],
            local_tracks,
            detailed_release,
        )
        detailed_candidate_scores.append(rescored_candidate)
        candidate_scores_by_mbid[release_mbid] = rescored_candidate
        if index == 0 and _summary_evidence_is_overwhelming(
            ranked_summary_scores,
            input_track_count,
            rescored_candidate,
        ):
            rescored_candidate["reasons"].append(
                "Skipped remaining detailed release lookups because summary evidence was dominant"
            )
            break

    _apply_earliest_official_release_tiebreaker(detailed_candidate_scores)

    final_candidate_scores = sorted(
        candidate_scores_by_mbid.values(),
        key=lambda item: (
            -item["score"],
            -item["matched_recordings"],
            item["release_mbid"],
        ),
    )

    best_candidate = final_candidate_scores[0]
    second_best_score = (
        final_candidate_scores[1]["score"]
        if len(final_candidate_scores) > 1
        else None
    )

    if best_candidate["score"] < MINIMUM_SELECTION_THRESHOLD:
        return {
            "status": "selected",
            "selected_release_mbid": best_candidate["release_mbid"],
            "selected_release": best_candidate.get("selected_release"),
            "candidate_scores": final_candidate_scores,
            "track_count": input_track_count,
            "matched_track_count": best_candidate["matched_recordings"],
            "reason": "Selected the highest-scoring release even though the best score is below the minimum threshold.",
            "error": None,
        }

    if second_best_score is not None and best_candidate["score"] - second_best_score < SELECTION_MARGIN:
        return {
            "status": "selected",
            "selected_release_mbid": best_candidate["release_mbid"],
            "selected_release": best_candidate.get("selected_release"),
            "candidate_scores": final_candidate_scores,
            "track_count": input_track_count,
            "matched_track_count": best_candidate["matched_recordings"],
            "reason": "Selected the highest-scoring release even though the score margin over the next candidate is small.",
            "error": None,
        }

    return {
        "status": "selected",
        "selected_release_mbid": best_candidate["release_mbid"],
        "selected_release": best_candidate.get("selected_release"),
        "candidate_scores": final_candidate_scores,
        "track_count": input_track_count,
        "matched_track_count": best_candidate["matched_recordings"],
        "reason": "Selected the highest-scoring release after shortlist and tracklist comparison.",
        "error": None,
    }


def _coerce_resolved_result_list(
    resolved_results: Iterable[dict[str, Any]] | dict[str, Any],
) -> list[dict[str, Any]]:
    if isinstance(resolved_results, dict):
        cluster_rows = resolved_results.get("resolved_results", [])
        return list(cluster_rows) if isinstance(cluster_rows, list) else []

    return list(resolved_results)


def _coerce_candidate_release_counts(
    cluster: dict[str, Any] | None,
    resolved_result_list: list[dict[str, Any]],
) -> dict[str, int]:
    if isinstance(cluster, dict):
        cluster_counts = cluster.get("candidate_release_counts")
        if isinstance(cluster_counts, dict):
            normalized_counts: dict[str, int] = {}
            for release_mbid, occurrence_count in cluster_counts.items():
                if not isinstance(release_mbid, str):
                    continue
                cleaned_release_mbid = release_mbid.strip()
                if not cleaned_release_mbid:
                    continue
                normalized_occurrence_count = _coerce_int(occurrence_count)
                if normalized_occurrence_count is None or normalized_occurrence_count <= 0:
                    continue
                normalized_counts[cleaned_release_mbid] = normalized_occurrence_count
            if normalized_counts:
                return normalized_counts

    return count_release_candidates(resolved_result_list)


def extract_release_candidates_from_resolved_result(
    resolved_result: dict[str, Any],
) -> list[str]:
    candidate_release_mbids = resolved_result.get("candidate_release_mbids", [])
    if not isinstance(candidate_release_mbids, list):
        return []

    return _deduplicate_strings(
        [
            release_mbid
            for release_mbid in candidate_release_mbids
            if isinstance(release_mbid, str)
        ]
    )


def count_release_candidates(
    resolved_results: Iterable[dict[str, Any]],
) -> dict[str, int]:
    candidate_counter: Counter[str] = Counter()
    for resolved_result in resolved_results:
        for release_mbid in extract_release_candidates_from_resolved_result(resolved_result):
            candidate_counter[release_mbid] += 1
    return dict(candidate_counter)


def summarize_selected_release(selected_release: Any) -> dict[str, Any]:
    release = _extract_release_dict(selected_release)
    if not isinstance(release, dict):
        return {
            "album": None,
            "album_artist": None,
            "date": None,
        }

    return {
        "album": release.get("title"),
        "album_artist": _extract_release_artist_credit(release),
        "date": release.get("date"),
    }


def build_local_album_group(resolved_results: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    local_tracks = [_build_local_track(resolved_result) for resolved_result in resolved_results]
    return sorted(local_tracks, key=_local_track_sort_key)


def collect_release_candidate_summaries(
    resolved_results: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        release_mbid: {
            "release_mbid": release_mbid,
            "occurrence_count": occurrence_count,
            "track_count": None,
            "status": None,
            "primary_type": None,
            "secondary_types": [],
            "format": None,
            "date": None,
        }
        for release_mbid, occurrence_count in count_release_candidates(resolved_results).items()
    }


def _build_summary_candidate_score(
    *,
    release_mbid: str,
    occurrence_count: int,
    input_track_count: int,
) -> dict[str, Any]:
    score = occurrence_count * 10
    reasons: list[str] = []
    reasons.append(f"+{score} candidate occurrence support")

    return {
        "release_mbid": release_mbid,
        "score": score,
        "matched_recordings": occurrence_count,
        "matched_ratio": (occurrence_count / input_track_count if input_track_count else 0.0),
        "track_count": None,
        "track_count_difference_ratio": 1.0,
        "status": None,
        "primary_type": None,
        "secondary_types": [],
        "format": None,
        "reasons": reasons,
        "selected_release": None,
        "date": None,
    }

def _summary_evidence_is_overwhelming(
    ranked_summary_scores: list[dict[str, Any]],
    input_track_count: int,
    detailed_top_candidate: dict[str, Any],
) -> bool:
    if input_track_count <= 0 or not ranked_summary_scores:
        return False

    top_candidate = ranked_summary_scores[0]
    top_matched_recordings = _coerce_int(top_candidate.get("matched_recordings")) or 0
    top_matched_ratio = top_matched_recordings / input_track_count
    if top_matched_ratio < SUMMARY_DOMINANT_MATCH_RATIO:
        return False

    second_matched_recordings = 0
    if len(ranked_summary_scores) > 1:
        second_matched_recordings = (
            _coerce_int(ranked_summary_scores[1].get("matched_recordings")) or 0
        )
    required_gap = max(
        SUMMARY_DOMINANT_MIN_GAP,
        int(input_track_count * SUMMARY_DOMINANT_MIN_GAP_RATIO),
    )
    if top_matched_recordings - second_matched_recordings < required_gap:
        return False

    track_count_difference_ratio = detailed_top_candidate.get("track_count_difference_ratio")
    if not isinstance(track_count_difference_ratio, (int, float)):
        return False
    return track_count_difference_ratio <= SUMMARY_DOMINANT_MAX_TRACK_COUNT_DIFFERENCE_RATIO


def _score_release_detail(
    base_candidate_score: dict[str, Any],
    local_tracks: list[dict[str, Any]],
    detailed_release: dict[str, Any],
) -> dict[str, Any]:
    release = _extract_release_dict(detailed_release)
    score = 0
    reasons: list[str] = []
    input_track_count = len(local_tracks)
    release_track_count = _extract_release_track_count(release)
    track_count_difference_ratio = 1.0

    if input_track_count > 0 and release_track_count is not None:
        track_count_difference_ratio = abs(input_track_count - release_track_count) / input_track_count
        if input_track_count == release_track_count:
            score += 100
            reasons.append("+100 exact track count match")
        elif track_count_difference_ratio <= 0.33:
            score += 40
            reasons.append("+40 near track count match")
        else:
            score -= 80
            reasons.append("-80 heavy track count mismatch")

    release_tracks = _extract_release_tracks(release)
    matched_recordings, matched_primary_sequence, local_primary_sequence = _match_local_tracks_to_release(
        local_tracks,
        release_tracks,
    )
    matched_ratio = matched_recordings / input_track_count if input_track_count else 0.0
    if matched_ratio >= 0.66:
        recording_bonus = matched_recordings * 5
        score += recording_bonus
        reasons.append(f"+{recording_bonus} recording match bonus")

    if _local_order_is_known(local_tracks):
        exact_order_match = (
            len(local_primary_sequence) == input_track_count
            and local_primary_sequence == matched_primary_sequence
        )
        if exact_order_match:
            score += 60
            reasons.append("+60 exact order match")
        else:
            relative_order_ratio = _calculate_relative_order_ratio(
                local_primary_sequence,
                matched_primary_sequence,
            )
            if relative_order_ratio >= 0.66:
                score += 20
                reasons.append("+20 relative order match")

    score += _apply_disc_layout_score(local_tracks, release_tracks, reasons)

    detailed_candidate_summary = {
        "status": _normalize_lower(release.get("status") if isinstance(release, dict) else None),
        "primary_type": _extract_primary_type(release),
        "secondary_types": _extract_secondary_types(release),
        "format": _extract_release_format(release),
    }
    score = _apply_release_metadata_score(
        score=score,
        candidate_summary=detailed_candidate_summary,
        reasons=reasons,
    )

    return {
        "release_mbid": base_candidate_score["release_mbid"],
        "score": score,
        "matched_recordings": matched_recordings,
        "matched_ratio": matched_ratio,
        "track_count": release_track_count,
        "track_count_difference_ratio": track_count_difference_ratio,
        "status": detailed_candidate_summary["status"],
        "primary_type": detailed_candidate_summary["primary_type"],
        "secondary_types": detailed_candidate_summary["secondary_types"],
        "format": detailed_candidate_summary["format"],
        "reasons": reasons,
        "selected_release": detailed_release,
        "date": release.get("date") if isinstance(release, dict) else None,
    }


def _apply_release_metadata_score(
    *,
    score: int,
    candidate_summary: dict[str, Any],
    reasons: list[str],
) -> int:

    release_status = _normalize_lower(candidate_summary.get("status"))
    if release_status == "official":
        score += 20
        reasons.append("+20 official release")
    elif release_status == "bootleg":
        score -= 50
        reasons.append("-50 bootleg")
    elif release_status == "promotion":
        score -= 30
        reasons.append("-30 promotion")
    elif release_status == "pseudo-release":
        score -= 30
        reasons.append("-30 pseudo-release")

    primary_type = _normalize_lower(candidate_summary.get("primary_type"))
    if primary_type == "album":
        score += 15
        reasons.append("+15 album release group")

    secondary_types = {_normalize_lower(item) for item in candidate_summary.get("secondary_types", [])}
    if "compilation" in secondary_types:
        score -= 60
        reasons.append("-60 compilation")
    if "soundtrack" in secondary_types:
        score -= 50
        reasons.append("-50 soundtrack")
    if "live" in secondary_types:
        score -= 50
        reasons.append("-50 live")
    if "remix" in secondary_types:
        score -= 40
        reasons.append("-40 remix")
    if "demo" in secondary_types:
        score -= 40
        reasons.append("-40 demo")

    if _normalize_lower(candidate_summary.get("format")) == "digital media":
        score += 10
        reasons.append("+10 digital media")

    return score


def _apply_earliest_official_release_tiebreaker(candidate_scores: list[dict[str, Any]]) -> None:
    if len(candidate_scores) < 2:
        return

    grouped_candidates: dict[int, list[dict[str, Any]]] = {}
    for candidate_score in candidate_scores:
        grouped_candidates.setdefault(candidate_score["score"], []).append(candidate_score)

    for tied_candidates in grouped_candidates.values():
        if len(tied_candidates) < 2:
            continue

        official_candidates = [
            candidate
            for candidate in tied_candidates
            if candidate.get("status") == "official" and candidate.get("date")
        ]
        if len(official_candidates) < 2:
            continue

        earliest_candidate = min(official_candidates, key=lambda item: item["date"])
        earliest_candidate["score"] += 5
        earliest_candidate["reasons"].append("+5 earliest official release")


def _build_local_track(resolved_result: dict[str, Any]) -> dict[str, Any]:
    original_path = Path(resolved_result["original_path"])
    filename_disc_number, filename_track_number = _parse_filename_numbers(original_path)

    primary_recording_mbid = resolved_result.get("recording_mbid")
    candidate_recording_mbids = _deduplicate_strings(
        [value for value in [primary_recording_mbid, *resolved_result.get("candidate_recording_mbids", [])] if value]
    )
    if primary_recording_mbid is None and candidate_recording_mbids:
        primary_recording_mbid = candidate_recording_mbids[0]

    return {
        "original_path": original_path,
        "recording_mbid": primary_recording_mbid,
        "candidate_recording_mbids": set(candidate_recording_mbids),
        "disc_number": _coerce_int(resolved_result.get("disc_number")),
        "track_number": _coerce_int(resolved_result.get("track_number")),
        "filename_disc_number": filename_disc_number,
        "filename_track_number": filename_track_number,
    }


def _local_track_sort_key(local_track: dict[str, Any]) -> tuple[int, int, int, str]:
    disc_number = local_track["disc_number"] or local_track["filename_disc_number"]
    track_number = local_track["track_number"] or local_track["filename_track_number"]

    return (
        0 if disc_number is not None else 1,
        disc_number or 0,
        track_number or 0,
        str(local_track["original_path"]).lower(),
    )


def _parse_filename_numbers(path: Path) -> tuple[int | None, int | None]:
    stem = path.stem
    patterns = [
        re.compile(r"^(?:cd|disc)\s*(\d+)\s*[-._ ]+\s*(\d+)\b", re.IGNORECASE),
        re.compile(r"^(\d+)\s*[-._]\s*(\d+)\b"),
        re.compile(r"^(\d+)\s+(\d+)\b"),
        re.compile(r"^(\d+)\b"),
    ]

    for pattern in patterns:
        match = pattern.match(stem)
        if not match:
            continue

        groups = match.groups()
        if len(groups) >= 2:
            return _safe_int(groups[0]), _safe_int(groups[1])
        if len(groups) == 1:
            return None, _safe_int(groups[0])

    return None, None


def _extract_summary_release_track_count(release: dict[str, Any]) -> int | None:
    for key in ("track-count", "track_count"):
        value = _coerce_int(release.get(key))
        if value is not None:
            return value

    media = release.get("medium-list") or release.get("media")
    if isinstance(media, list):
        total = 0
        found_any = False
        for medium in media:
            if not isinstance(medium, dict):
                continue
            medium_track_count = _coerce_int(
                medium.get("track-count") or medium.get("track_count")
            )
            if medium_track_count is not None:
                total += medium_track_count
                found_any = True
        if found_any:
            return total

    return None


def _extract_release_track_count(release: Any) -> int | None:
    if not isinstance(release, dict):
        return None

    return _extract_summary_release_track_count(release)


def _extract_release_tracks(release: Any) -> list[dict[str, Any]]:
    if not isinstance(release, dict):
        return []

    media = release.get("medium-list") or release.get("media")
    if not isinstance(media, list):
        return []

    release_tracks: list[dict[str, Any]] = []
    for medium in media:
        if not isinstance(medium, dict):
            continue

        disc_number = _coerce_int(medium.get("position"))
        track_list = medium.get("track-list") or medium.get("tracks")
        if not isinstance(track_list, list):
            continue

        for track in track_list:
            if not isinstance(track, dict):
                continue

            recording = track.get("recording")
            recording_id = recording.get("id") if isinstance(recording, dict) else None
            if not isinstance(recording_id, str) or not recording_id.strip():
                continue

            release_tracks.append(
                {
                    "recording_mbid": recording_id.strip(),
                    "disc_number": disc_number,
                    "track_number": _coerce_int(track.get("position")),
                }
            )

    return release_tracks


def _match_local_tracks_to_release(
    local_tracks: list[dict[str, Any]],
    release_tracks: list[dict[str, Any]],
) -> tuple[int, list[str], list[str]]:
    release_recording_ids = {track["recording_mbid"] for track in release_tracks}
    matched_recordings = 0
    local_primary_sequence: list[str] = []
    for local_track in local_tracks:
        if local_track["candidate_recording_mbids"] & release_recording_ids:
            matched_recordings += 1
        if local_track["recording_mbid"] and local_track["recording_mbid"] in release_recording_ids:
            local_primary_sequence.append(local_track["recording_mbid"])

    local_primary_set = set(local_primary_sequence)
    matched_primary_sequence = [
        track["recording_mbid"]
        for track in release_tracks
        if track["recording_mbid"] in local_primary_set
    ]

    return matched_recordings, matched_primary_sequence, local_primary_sequence


def _local_order_is_known(local_tracks: list[dict[str, Any]]) -> bool:
    if not local_tracks:
        return False

    return all(
        (track["track_number"] or track["filename_track_number"]) is not None
        for track in local_tracks
    )


def _calculate_relative_order_ratio(local_sequence: list[str], release_sequence: list[str]) -> float:
    if not local_sequence or not release_sequence:
        return 0.0

    lcs_length = _longest_common_subsequence_length(local_sequence, release_sequence)
    return lcs_length / len(local_sequence)


def _longest_common_subsequence_length(left: list[str], right: list[str]) -> int:
    widths = len(right) + 1
    previous_row = [0] * widths

    for left_item in left:
        current_row = [0] * widths
        for index, right_item in enumerate(right, start=1):
            if left_item == right_item:
                current_row[index] = previous_row[index - 1] + 1
            else:
                current_row[index] = max(current_row[index - 1], previous_row[index])
        previous_row = current_row

    return previous_row[-1]


def _apply_disc_layout_score(
    local_tracks: list[dict[str, Any]],
    release_tracks: list[dict[str, Any]],
    reasons: list[str],
) -> int:
    if not _local_disc_info_exists(local_tracks):
        return 0

    local_disc_count = max(
        track["disc_number"] or track["filename_disc_number"] or 0
        for track in local_tracks
    )
    release_disc_count = max((track["disc_number"] or 0 for track in release_tracks), default=0)

    local_disc_map = {
        track["recording_mbid"]: track["disc_number"] or track["filename_disc_number"]
        for track in local_tracks
        if track["recording_mbid"] and (track["disc_number"] or track["filename_disc_number"]) is not None
    }
    release_disc_map = {
        track["recording_mbid"]: track["disc_number"]
        for track in release_tracks
        if track["disc_number"] is not None
    }

    exact_layout_match = (
        local_disc_count == release_disc_count
        and local_disc_map
        and all(
            release_disc_map.get(recording_mbid) == disc_number
            for recording_mbid, disc_number in local_disc_map.items()
            if recording_mbid in release_disc_map
        )
    )
    if exact_layout_match:
        reasons.append("+40 exact disc layout match")
        return 40

    if release_disc_count != local_disc_count:
        reasons.append("-30 disc count mismatch")
        return -30

    return 0


def _local_disc_info_exists(local_tracks: list[dict[str, Any]]) -> bool:
    return any(
        (track["disc_number"] or track["filename_disc_number"]) is not None
        for track in local_tracks
    )


def _extract_release_dict(selected_release: Any) -> dict[str, Any] | None:
    if not isinstance(selected_release, dict):
        return None

    release = selected_release.get("release")
    if isinstance(release, dict):
        return release
    return None


def _extract_primary_type(release: Any) -> str | None:
    if not isinstance(release, dict):
        return None

    release_group = release.get("release-group")
    if isinstance(release_group, dict):
        primary_type = release_group.get("primary-type")
        if isinstance(primary_type, str) and primary_type.strip():
            return primary_type.strip()

        type_value = release_group.get("type")
        if isinstance(type_value, str) and type_value.strip():
            return type_value.strip()

    return None


def _extract_secondary_types(release: Any) -> list[str]:
    if not isinstance(release, dict):
        return []

    release_group = release.get("release-group")
    if not isinstance(release_group, dict):
        return []

    secondary_type_list = release_group.get("secondary-type-list")
    if isinstance(secondary_type_list, list):
        return [str(item).strip() for item in secondary_type_list if str(item).strip()]

    return []


def _extract_release_format(release: Any) -> str | None:
    if not isinstance(release, dict):
        return None

    media = release.get("medium-list") or release.get("media")
    if not isinstance(media, list):
        return None

    for medium in media:
        if not isinstance(medium, dict):
            continue
        format_value = medium.get("format")
        if isinstance(format_value, str) and format_value.strip():
            return format_value.strip()

    return None


def _extract_release_artist_credit(release: dict[str, Any]) -> str | None:
    artist_credit = release.get("artist-credit-phrase")
    if isinstance(artist_credit, str) and artist_credit.strip():
        return artist_credit.strip()

    artist_credit_list = release.get("artist-credit")
    if not isinstance(artist_credit_list, list):
        return None

    parts: list[str] = []
    for item in artist_credit_list:
        if isinstance(item, str):
            parts.append(item)
            continue
        if isinstance(item, dict):
            artist = item.get("artist")
            if isinstance(artist, dict):
                artist_name = artist.get("name")
                if isinstance(artist_name, str) and artist_name.strip():
                    parts.append(artist_name.strip())

    joined = "".join(parts).strip()
    return joined or None


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


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _normalize_lower(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned_value = value.strip()
    return cleaned_value.lower() if cleaned_value else None
