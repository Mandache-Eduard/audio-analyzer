from __future__ import annotations

import os
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Iterable

import requests

from metadata_enrichment_and_file_grouping.tag_writer import write_tags_to_copied_file

LYRICS_MODE_UNSYNCED = "lyrics-unsynced"
LYRICS_MODE_SYNCED = "lyrics-synced"
LYRICS_MODE_NONE = "no-lyrics"
SUPPORTED_LYRICS_MODES = frozenset(
    {
        LYRICS_MODE_UNSYNCED,
        LYRICS_MODE_SYNCED,
        LYRICS_MODE_NONE,
    }
)

LRCLIB_BASE_URL = "https://lrclib.net/api"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 10.0
DEFAULT_DURATION_TOLERANCE_SECONDS = 2.0
COMMON_VARIANT_TOKENS = frozenset({"live", "remix", "cover"})
LRCLIB_USER_AGENT = "flac-authenticator/0.1.0"


@dataclass(slots=True)
class LyricsResult:
    status: str
    lyrics_type: str | None
    source: str | None
    plain_lyrics: str | None = None
    synced_lyrics: str | None = None
    provider_id: str | None = None
    confidence: float | None = None
    error: str | None = None


@dataclass(slots=True)
class _LyricsCandidate:
    source: str
    provider_id: str | None
    title: str | None
    artist: str | None
    album: str | None
    duration_seconds: float | None
    plain_lyrics: str | None
    synced_lyrics: str | None
    instrumental: bool
    confidence: float


def handle_lyrics_for_track(
    copy_result: dict[str, Any],
    *,
    lyrics_mode: str,
    genius_access_token: str | None = None,
    input_func: Callable[[str], str] = input,
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    session: requests.Session | None = None,
) -> LyricsResult:
    if lyrics_mode not in SUPPORTED_LYRICS_MODES:
        return LyricsResult(
            status="error",
            lyrics_type=None,
            source=None,
            error=f"unsupported lyrics mode: {lyrics_mode}",
        )

    if lyrics_mode == LYRICS_MODE_NONE:
        print("[lyrics] Skipping lyric lookup because no-lyrics mode was selected.")
        return LyricsResult(status="skipped", lyrics_type=None, source=None)

    if copy_result.get("status") != "copied":
        return LyricsResult(
            status="skipped",
            lyrics_type=None,
            source=None,
            error=copy_result.get("reason") or "copy step did not produce a final file",
        )

    metadata = copy_result.get("metadata")
    if not isinstance(metadata, dict):
        return LyricsResult(
            status="error",
            lyrics_type=None,
            source=None,
            error="missing metadata payload for lyrics lookup",
        )

    if is_instrumental_track(metadata):
        print("[lyrics] Track marked or detected as instrumental; skipping lyrics.")
        return LyricsResult(status="instrumental", lyrics_type=None, source=None)

    lookup_summary = _build_lookup_summary(metadata)
    if not lookup_summary["title"] or not lookup_summary["artist"]:
        return LyricsResult(
            status="not_found",
            lyrics_type=None,
            source=None,
            error="insufficient metadata for lyrics lookup; title and artist are required",
        )

    print(
        "[lyrics] Looking up lyrics for '{}' by '{}'.".format(
            lookup_summary["title"],
            lookup_summary["artist"],
        )
    )

    lrclib_result = fetch_from_lrclib(
        metadata,
        prefer_synced=lyrics_mode == LYRICS_MODE_SYNCED,
        request_timeout_seconds=request_timeout_seconds,
        session=session,
    )

    if lrclib_result.status == "instrumental":
        print("[lyrics] LRCLIB marked this track as instrumental; skipping lyrics.")
        return lrclib_result

    if lyrics_mode == LYRICS_MODE_SYNCED:
        if lrclib_result.status == "found" and lrclib_result.synced_lyrics:
            try:
                lrc_path = write_lrc_file(copy_result["copied_path"], lrclib_result.synced_lyrics)
            except OSError as exc:
                return LyricsResult(
                    status="error",
                    lyrics_type="synced",
                    source=lrclib_result.source,
                    synced_lyrics=lrclib_result.synced_lyrics,
                    provider_id=lrclib_result.provider_id,
                    confidence=lrclib_result.confidence,
                    error=str(exc),
                )

            print(f"[lyrics] Wrote synced lyrics sidecar: {lrc_path}")
            return lrclib_result

        if lrclib_result.status == "found" and lrclib_result.plain_lyrics:
            choice = ask_user_for_lyrics_mismatch(
                lyrics_mode=lyrics_mode,
                input_func=input_func,
            )
            if choice == "1":
                return _embed_plain_lyrics_result(
                    copy_result,
                    lrclib_result.plain_lyrics,
                    source=lrclib_result.source,
                    provider_id=lrclib_result.provider_id,
                    confidence=lrclib_result.confidence,
                )
            if choice == "2":
                print("[lyrics] User aborted lyrics for this track.")
                return LyricsResult(status="aborted", lyrics_type=None, source=lrclib_result.source)

            genius_result = fetch_from_genius(
                metadata,
                genius_access_token=genius_access_token,
                request_timeout_seconds=request_timeout_seconds,
            )
            if genius_result.status == "found" and genius_result.plain_lyrics:
                return _embed_plain_lyrics_result(
                    copy_result,
                    genius_result.plain_lyrics,
                    source=genius_result.source,
                    provider_id=genius_result.provider_id,
                    confidence=genius_result.confidence,
                )
            return genius_result

        return lrclib_result

    if lrclib_result.status == "found" and lrclib_result.plain_lyrics:
        return _embed_plain_lyrics_result(
            copy_result,
            lrclib_result.plain_lyrics,
            source=lrclib_result.source,
            provider_id=lrclib_result.provider_id,
            confidence=lrclib_result.confidence,
        )

    if lrclib_result.status == "found" and lrclib_result.synced_lyrics:
        choice = ask_user_for_lyrics_mismatch(
            lyrics_mode=lyrics_mode,
            input_func=input_func,
        )
        if choice == "1":
            plain_lyrics = convert_lrc_to_plain_text(lrclib_result.synced_lyrics)
            if plain_lyrics is None:
                return LyricsResult(
                    status="error",
                    lyrics_type="unsynced",
                    source=lrclib_result.source,
                    synced_lyrics=lrclib_result.synced_lyrics,
                    provider_id=lrclib_result.provider_id,
                    confidence=lrclib_result.confidence,
                    error="failed to convert synced lyrics to plain text",
                )
            return _embed_plain_lyrics_result(
                copy_result,
                plain_lyrics,
                source=lrclib_result.source,
                provider_id=lrclib_result.provider_id,
                confidence=lrclib_result.confidence,
            )

        print("[lyrics] User aborted lyrics for this track.")
        return LyricsResult(status="aborted", lyrics_type=None, source=lrclib_result.source)

    genius_result = fetch_from_genius(
        metadata,
        genius_access_token=genius_access_token,
        request_timeout_seconds=request_timeout_seconds,
    )
    if genius_result.status == "found" and genius_result.plain_lyrics:
        return _embed_plain_lyrics_result(
            copy_result,
            genius_result.plain_lyrics,
            source=genius_result.source,
            provider_id=genius_result.provider_id,
            confidence=genius_result.confidence,
        )
    return genius_result if genius_result.status != "not_found" else lrclib_result


def handle_lyrics_for_tracks(
    copy_results: Iterable[dict[str, Any]],
    *,
    lyrics_mode: str,
    genius_access_token: str | None = None,
    input_func: Callable[[str], str] = input,
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    session: requests.Session | None = None,
) -> list[LyricsResult]:
    return [
        handle_lyrics_for_track(
            copy_result,
            lyrics_mode=lyrics_mode,
            genius_access_token=genius_access_token,
            input_func=input_func,
            request_timeout_seconds=request_timeout_seconds,
            session=session,
        )
        for copy_result in copy_results
    ]


def fetch_from_lrclib(
    metadata: dict[str, Any],
    *,
    prefer_synced: bool,
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    session: requests.Session | None = None,
) -> LyricsResult:
    if is_instrumental_track(metadata):
        return LyricsResult(status="instrumental", lyrics_type=None, source="lrclib")

    title = _clean_string(metadata.get("title"))
    artist = _best_artist_name(metadata)
    album = _clean_string(metadata.get("album"))
    duration_seconds = _coerce_float(_extract_duration_seconds(metadata))

    if not title or not artist:
        return LyricsResult(
            status="not_found",
            lyrics_type=None,
            source="lrclib",
            error="missing title or artist for LRCLIB lookup",
        )

    active_session = session or requests.Session()
    close_session = session is None
    try:
        candidate = _fetch_lrclib_candidate_exact(
            active_session,
            title=title,
            artist=artist,
            album=album,
            duration_seconds=duration_seconds,
            metadata=metadata,
            request_timeout_seconds=request_timeout_seconds,
        )
        if candidate is None:
            candidate = _fetch_lrclib_candidate_search(
                active_session,
                title=title,
                artist=artist,
                album=album,
                duration_seconds=duration_seconds,
                metadata=metadata,
                request_timeout_seconds=request_timeout_seconds,
            )
    except requests.Timeout:
        return LyricsResult(
            status="error",
            lyrics_type=None,
            source="lrclib",
            error="LRCLIB request timed out",
        )
    except requests.ConnectionError:
        return LyricsResult(
            status="error",
            lyrics_type=None,
            source="lrclib",
            error="unable to connect to LRCLIB",
        )
    except requests.HTTPError as exc:
        return LyricsResult(
            status="error",
            lyrics_type=None,
            source="lrclib",
            error=_classify_http_error("LRCLIB", exc.response.status_code if exc.response else None),
        )
    except requests.RequestException as exc:
        return LyricsResult(
            status="error",
            lyrics_type=None,
            source="lrclib",
            error=f"LRCLIB request failed: {exc}",
        )
    finally:
        if close_session:
            active_session.close()

    if candidate is None:
        return LyricsResult(
            status="not_found",
            lyrics_type=None,
            source="lrclib",
            error="no confident LRCLIB lyrics match found",
        )

    if candidate.instrumental:
        return LyricsResult(
            status="instrumental",
            lyrics_type=None,
            source=candidate.source,
            provider_id=candidate.provider_id,
            confidence=candidate.confidence,
        )

    selected_type = _select_candidate_lyrics_type(candidate, prefer_synced=prefer_synced)
    if selected_type is None:
        return LyricsResult(
            status="not_found",
            lyrics_type=None,
            source=candidate.source,
            provider_id=candidate.provider_id,
            confidence=candidate.confidence,
            error="matched LRCLIB candidate did not contain usable lyrics",
        )

    return LyricsResult(
        status="found",
        lyrics_type=selected_type,
        source=candidate.source,
        plain_lyrics=candidate.plain_lyrics,
        synced_lyrics=candidate.synced_lyrics,
        provider_id=candidate.provider_id,
        confidence=candidate.confidence,
    )


def fetch_from_genius(
    metadata: dict[str, Any],
    *,
    genius_access_token: str | None = None,
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
) -> LyricsResult:
    if is_instrumental_track(metadata):
        return LyricsResult(status="instrumental", lyrics_type=None, source="genius")

    title = _clean_string(metadata.get("title"))
    artist = _best_artist_name(metadata)

    if not title or not artist:
        return LyricsResult(
            status="not_found",
            lyrics_type=None,
            source="genius",
            error="missing title or artist for Genius lookup",
        )

    try:
        import lyricsgenius
    except ImportError:
        return LyricsResult(
            status="error",
            lyrics_type=None,
            source="genius",
            error="lyricsgenius is not installed",
        )

    token = genius_access_token or os.getenv("GENIUS_ACCESS_TOKEN")
    if not token:
        return LyricsResult(
            status="error",
            lyrics_type=None,
            source="genius",
            error="missing Genius access token",
        )

    try:
        genius = lyricsgenius.Genius(token)
        if hasattr(genius, "sleep_time"):
            genius.sleep_time = 1.0
        if hasattr(genius, "timeout"):
            genius.timeout = request_timeout_seconds
        if hasattr(genius, "remove_section_headers"):
            genius.remove_section_headers = True
        if hasattr(genius, "skip_non_songs"):
            genius.skip_non_songs = True
        if hasattr(genius, "excluded_terms"):
            genius.excluded_terms = ["(Remix)", "(Live)"]

        song = genius.search_song(title, artist)
    except requests.Timeout:
        return LyricsResult(
            status="error",
            lyrics_type=None,
            source="genius",
            error="Genius request timed out",
        )
    except requests.ConnectionError:
        return LyricsResult(
            status="error",
            lyrics_type=None,
            source="genius",
            error="unable to connect to Genius",
        )
    except requests.HTTPError as exc:
        return LyricsResult(
            status="error",
            lyrics_type=None,
            source="genius",
            error=_classify_http_error("Genius", exc.response.status_code if exc.response else None),
        )
    except Exception as exc:
        classified_error = _classify_generic_request_error("Genius", exc)
        return LyricsResult(
            status="error",
            lyrics_type=None,
            source="genius",
            error=classified_error,
        )

    if song is None:
        return LyricsResult(
            status="not_found",
            lyrics_type=None,
            source="genius",
            error="Genius did not return a matching song",
        )

    lyrics_text = _normalize_genius_lyrics(getattr(song, "lyrics", None))
    if lyrics_text is None:
        return LyricsResult(
            status="not_found",
            lyrics_type=None,
            source="genius",
            error="Genius returned a song without usable lyrics",
        )

    candidate_title = _clean_string(getattr(song, "title", None))
    candidate_artist = _clean_string(getattr(song, "artist", None))
    confidence = _score_text_candidate(
        metadata,
        title=candidate_title,
        artist=candidate_artist,
        album=None,
        duration_seconds=None,
    )
    if confidence < 0.82:
        return LyricsResult(
            status="not_found",
            lyrics_type=None,
            source="genius",
            provider_id=_clean_string(getattr(song, "id", None)),
            confidence=confidence,
            error="Genius match was not confident enough",
        )

    return LyricsResult(
        status="found",
        lyrics_type="unsynced",
        source="genius",
        plain_lyrics=lyrics_text,
        provider_id=_clean_string(getattr(song, "id", None)),
        confidence=confidence,
    )


def write_lrc_file(final_audio_path: str | Path, synced_lyrics: str) -> Path:
    output_audio_path = Path(final_audio_path)
    lrc_path = output_audio_path.with_suffix(".lrc")
    lrc_path.write_text(synced_lyrics, encoding="utf-8")
    return lrc_path


def convert_lrc_to_plain_text(synced_lyrics: str | None) -> str | None:
    cleaned_lyrics = _clean_string(synced_lyrics)
    if cleaned_lyrics is None:
        return None

    plain_lines: list[str] = []
    for raw_line in cleaned_lyrics.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"\[[^\]]*:[^\]]*\]", "", line).strip()
        line = re.sub(r"(?:\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\])+", "", line).strip()
        if line:
            plain_lines.append(line)

    if not plain_lines:
        return None
    return "\n".join(plain_lines)


def ask_user_for_lyrics_mismatch(
    *,
    lyrics_mode: str,
    input_func: Callable[[str], str] = input,
) -> str:
    if lyrics_mode == LYRICS_MODE_SYNCED:
        print("Requested synced lyrics.")
        print("Found only matching unsynced lyrics.")
        print("")
        print("Press:")
        print("1. Use found unsynced lyrics")
        print("2. Abort operation")
        print("3. Fallback to Genius unsynced lyrics")
        valid_choices = {"1", "2", "3"}
    elif lyrics_mode == LYRICS_MODE_UNSYNCED:
        print("Requested unsynced lyrics.")
        print("Found only matching synced lyrics.")
        print("")
        print("Press:")
        print("1. Use found synced lyrics")
        print("2. Abort operation")
        valid_choices = {"1", "2"}
    else:
        raise ValueError(f"unsupported lyrics mode for mismatch prompt: {lyrics_mode}")

    while True:
        choice = input_func("> ").strip()
        if choice in valid_choices:
            return choice
        print("Invalid selection. Please choose {}.".format(", ".join(sorted(valid_choices))))


def is_instrumental_track(metadata: dict[str, Any]) -> bool:
    explicit_values = [
        metadata.get("instrumental"),
        metadata.get("is_instrumental"),
        metadata.get("track_is_instrumental"),
    ]
    for value in explicit_values:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().casefold() in {"1", "true", "yes", "instrumental"}:
            return True

    searchable_fields = [
        metadata.get("title"),
        metadata.get("subtitle"),
        metadata.get("grouping"),
        metadata.get("genre"),
    ]
    for value in searchable_fields:
        cleaned_value = _clean_string(value)
        if cleaned_value and "instrumental" in cleaned_value.casefold():
            return True
    return False


def _fetch_lrclib_candidate_exact(
    session: requests.Session,
    *,
    title: str,
    artist: str,
    album: str | None,
    duration_seconds: float | None,
    metadata: dict[str, Any],
    request_timeout_seconds: float,
) -> _LyricsCandidate | None:
    params: dict[str, Any] = {
        "track_name": title,
        "artist_name": artist,
    }
    if album:
        params["album_name"] = album
    if duration_seconds is not None:
        params["duration"] = int(round(duration_seconds))

    payload = _get_json(
        session,
        f"{LRCLIB_BASE_URL}/get",
        params=params,
        request_timeout_seconds=request_timeout_seconds,
    )
    if not isinstance(payload, dict):
        return None

    candidate = _build_lrclib_candidate(payload, metadata)
    if candidate is None or not _is_confident_candidate(candidate, metadata):
        return None
    return candidate


def _fetch_lrclib_candidate_search(
    session: requests.Session,
    *,
    title: str,
    artist: str,
    album: str | None,
    duration_seconds: float | None,
    metadata: dict[str, Any],
    request_timeout_seconds: float,
) -> _LyricsCandidate | None:
    params: dict[str, Any] = {
        "track_name": title,
        "artist_name": artist,
    }
    if album:
        params["album_name"] = album

    payload = _get_json(
        session,
        f"{LRCLIB_BASE_URL}/search",
        params=params,
        request_timeout_seconds=request_timeout_seconds,
    )
    if not isinstance(payload, list):
        return None

    best_candidate: _LyricsCandidate | None = None
    for item in payload:
        if not isinstance(item, dict):
            continue
        candidate = _build_lrclib_candidate(item, metadata)
        if candidate is None:
            continue
        if duration_seconds is not None and candidate.duration_seconds is not None:
            duration_delta = abs(candidate.duration_seconds - duration_seconds)
            if duration_delta > 5.0:
                continue
        if not _is_confident_candidate(candidate, metadata):
            continue
        if best_candidate is None or candidate.confidence > best_candidate.confidence:
            best_candidate = candidate

    return best_candidate


def _build_lrclib_candidate(
    payload: dict[str, Any],
    metadata: dict[str, Any],
) -> _LyricsCandidate | None:
    title = _clean_string(payload.get("trackName") or payload.get("name") or payload.get("title"))
    artist = _clean_string(payload.get("artistName") or payload.get("artist"))
    album = _clean_string(payload.get("albumName") or payload.get("album"))
    plain_lyrics = _clean_string(payload.get("plainLyrics") or payload.get("plain_lyrics"))
    synced_lyrics = _clean_string(payload.get("syncedLyrics") or payload.get("synced_lyrics"))
    duration_seconds = _coerce_float(payload.get("duration"))
    confidence = _score_text_candidate(
        metadata,
        title=title,
        artist=artist,
        album=album,
        duration_seconds=duration_seconds,
    )

    if title is None or artist is None:
        return None

    return _LyricsCandidate(
        source="lrclib",
        provider_id=_clean_string(payload.get("id")),
        title=title,
        artist=artist,
        album=album,
        duration_seconds=duration_seconds,
        plain_lyrics=plain_lyrics,
        synced_lyrics=synced_lyrics,
        instrumental=bool(payload.get("instrumental")),
        confidence=confidence,
    )


def _embed_plain_lyrics_result(
    copy_result: dict[str, Any],
    plain_lyrics: str,
    *,
    source: str | None,
    provider_id: str | None,
    confidence: float | None,
) -> LyricsResult:
    updated_metadata = dict(copy_result.get("metadata") or {})
    updated_metadata["lyrics"] = plain_lyrics
    updated_metadata["syncedlyrics"] = None
    copy_result["metadata"] = updated_metadata

    tag_write_result = write_tags_to_copied_file(copy_result)
    if tag_write_result.get("status") != "tagged":
        return LyricsResult(
            status="error",
            lyrics_type="unsynced",
            source=source,
            plain_lyrics=plain_lyrics,
            provider_id=provider_id,
            confidence=confidence,
            error=tag_write_result.get("reason") or "failed to write lyrics to audio metadata",
        )

    print("[lyrics] Embedded unsynced lyrics into audio metadata.")
    return LyricsResult(
        status="found",
        lyrics_type="unsynced",
        source=source,
        plain_lyrics=plain_lyrics,
        provider_id=provider_id,
        confidence=confidence,
    )


def _score_text_candidate(
    metadata: dict[str, Any],
    *,
    title: str | None,
    artist: str | None,
    album: str | None,
    duration_seconds: float | None,
) -> float:
    expected_title = _clean_string(metadata.get("title")) or ""
    expected_artist = _best_artist_name(metadata) or ""
    expected_album = _clean_string(metadata.get("album")) or ""
    expected_duration = _coerce_float(_extract_duration_seconds(metadata))

    title_score = _string_similarity(expected_title, title)
    artist_score = _string_similarity(expected_artist, artist)

    album_score = 0.5
    if expected_album and album:
        album_score = _string_similarity(expected_album, album)

    duration_score = 0.5
    if expected_duration is not None and duration_seconds is not None:
        delta = abs(expected_duration - duration_seconds)
        if delta <= DEFAULT_DURATION_TOLERANCE_SECONDS:
            duration_score = 1.0
        elif delta <= 4.0:
            duration_score = 0.7
        elif delta <= 5.0:
            duration_score = 0.4
        else:
            duration_score = 0.0

    score = (title_score * 0.45) + (artist_score * 0.35) + (album_score * 0.1) + (duration_score * 0.1)
    if _has_variant_mismatch(expected_title, title):
        score -= 0.25
    if _has_variant_mismatch(expected_artist, artist):
        score -= 0.1

    return max(0.0, min(score, 1.0))


def _is_confident_candidate(candidate: _LyricsCandidate, metadata: dict[str, Any]) -> bool:
    if candidate.instrumental:
        return True

    title_score = _string_similarity(metadata.get("title"), candidate.title)
    artist_score = _string_similarity(_best_artist_name(metadata), candidate.artist)
    album_value = _clean_string(metadata.get("album"))
    if album_value and candidate.album:
        album_score = _string_similarity(album_value, candidate.album)
        if album_score < 0.72:
            return False

    duration_seconds = _coerce_float(_extract_duration_seconds(metadata))
    if duration_seconds is not None and candidate.duration_seconds is not None:
        if abs(duration_seconds - candidate.duration_seconds) > 5.0:
            return False

    if title_score < 0.86 or artist_score < 0.82:
        return False

    if _has_variant_mismatch(metadata.get("title"), candidate.title):
        return False
    if _has_variant_mismatch(_best_artist_name(metadata), candidate.artist):
        return False

    return candidate.confidence >= 0.84


def _select_candidate_lyrics_type(
    candidate: _LyricsCandidate,
    *,
    prefer_synced: bool,
) -> str | None:
    if prefer_synced:
        if candidate.synced_lyrics:
            return "synced"
        if candidate.plain_lyrics:
            return "unsynced"
        return None

    if candidate.plain_lyrics:
        return "unsynced"
    if candidate.synced_lyrics:
        return "synced"
    return None


def _get_json(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any],
    request_timeout_seconds: float,
) -> Any:
    response = session.get(
        url,
        params=params,
        timeout=request_timeout_seconds,
        headers={
            "Accept": "application/json",
            "User-Agent": LRCLIB_USER_AGENT,
        },
    )

    if response.status_code == 404:
        return None
    if response.status_code in {403, 429}:
        response.raise_for_status()
    response.raise_for_status()
    return response.json()


def _build_lookup_summary(metadata: dict[str, Any]) -> dict[str, str | float | None]:
    return {
        "title": _clean_string(metadata.get("title")),
        "artist": _best_artist_name(metadata),
        "album": _clean_string(metadata.get("album")),
        "duration_seconds": _coerce_float(_extract_duration_seconds(metadata)),
    }


def _extract_duration_seconds(metadata: dict[str, Any]) -> Any:
    for key in (
        "duration_seconds",
        "duration",
        "track_duration_seconds",
        "audio_duration_seconds",
        "length_seconds",
    ):
        if metadata.get(key) is not None:
            return metadata.get(key)
    return None


def _best_artist_name(metadata: dict[str, Any]) -> str | None:
    artists = metadata.get("artists")
    if isinstance(artists, list):
        cleaned_artists = [_clean_string(value) for value in artists]
        joined_artists = [value for value in cleaned_artists if value]
        if joined_artists:
            return "; ".join(joined_artists)
    return _clean_string(metadata.get("artist"))


def _string_similarity(expected: Any, actual: Any) -> float:
    normalized_expected = _normalize_match_text(expected)
    normalized_actual = _normalize_match_text(actual)
    if not normalized_expected or not normalized_actual:
        return 0.0
    if normalized_expected == normalized_actual:
        return 1.0
    return SequenceMatcher(None, normalized_expected, normalized_actual).ratio()


def _normalize_match_text(value: Any) -> str:
    cleaned_value = _clean_string(value)
    if cleaned_value is None:
        return ""
    normalized_value = cleaned_value.casefold()
    normalized_value = re.sub(r"\([^)]*\)", " ", normalized_value)
    normalized_value = re.sub(r"\[[^\]]*\]", " ", normalized_value)
    normalized_value = re.sub(r"[^a-z0-9]+", " ", normalized_value)
    normalized_value = re.sub(r"\s+", " ", normalized_value).strip()
    return normalized_value


def _has_variant_mismatch(expected: Any, actual: Any) -> bool:
    expected_tokens = _variant_tokens(expected)
    actual_tokens = _variant_tokens(actual)
    if not expected_tokens and not actual_tokens:
        return False
    return expected_tokens != actual_tokens


def _variant_tokens(value: Any) -> set[str]:
    cleaned_value = _clean_string(value)
    if cleaned_value is None:
        return set()
    normalized = re.sub(r"[^a-z0-9]+", " ", cleaned_value.casefold()).strip()
    if not normalized:
        return set()
    return {token for token in COMMON_VARIANT_TOKENS if token in normalized.split()}


def _normalize_genius_lyrics(value: Any) -> str | None:
    cleaned_value = _clean_string(value)
    if cleaned_value is None:
        return None

    normalized_value = cleaned_value.replace("\r\n", "\n")
    normalized_value = re.sub(r"^\d+\s+Contributors?.*?Lyrics", "", normalized_value, flags=re.IGNORECASE | re.DOTALL)
    normalized_value = normalized_value.replace("You might also like", "")
    normalized_value = re.sub(r"\d*Embed$", "", normalized_value).strip()
    return normalized_value or None


def _classify_http_error(provider_name: str, status_code: int | None) -> str:
    if status_code == 403:
        return f"{provider_name} request was forbidden"
    if status_code == 404:
        return f"{provider_name} lyrics were not found"
    if status_code == 429:
        return f"{provider_name} rate limit was reached"
    if status_code is None:
        return f"{provider_name} request failed"
    return f"{provider_name} request failed with HTTP {status_code}"


def _classify_generic_request_error(provider_name: str, exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    lowered_message = message.casefold()
    if "timed out" in lowered_message or "timeout" in lowered_message:
        return f"{provider_name} request timed out"
    if "connection" in lowered_message:
        return f"unable to connect to {provider_name}"
    if "403" in lowered_message or "forbidden" in lowered_message:
        return f"{provider_name} request was forbidden"
    if "429" in lowered_message or "rate limit" in lowered_message:
        return f"{provider_name} rate limit was reached"
    if "404" in lowered_message or "not found" in lowered_message:
        return f"{provider_name} lyrics were not found"
    return f"{provider_name} request failed: {message}"


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_string(value: Any) -> str | None:
    if value is None:
        return None
    cleaned_value = str(value).strip()
    return cleaned_value or None
