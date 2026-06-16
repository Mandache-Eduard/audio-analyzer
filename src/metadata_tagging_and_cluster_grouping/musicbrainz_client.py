from __future__ import annotations

from typing import Any
import musicbrainzngs


class MusicBrainzClient:
    def __init__(
        self,
        *,
        app_name: str,
        app_version: str,
        contact_email: str,
    ) -> None:
        if not app_name.strip():
            raise ValueError("Missing required MusicBrainz app name.")
        if not app_version.strip():
            raise ValueError("Missing required MusicBrainz app version.")
        if not contact_email.strip():
            raise ValueError("Missing required MusicBrainz contact email.")

        self._musicbrainzngs = musicbrainzngs
        self._musicbrainzngs.set_useragent(app_name, app_version, contact_email)
        self._musicbrainzngs.set_rate_limit(1.0, 1)

    def lookup_recording_by_mbid(self, recording_mbid: str) -> dict[str, Any]:
        if not recording_mbid.strip():
            raise ValueError("MusicBrainz recording MBID must not be empty.")

        try:
            return self._musicbrainzngs.get_recording_by_id(
                recording_mbid,
                includes=["artists", "releases", "isrcs"],
            )
        except Exception as exc:
            raise RuntimeError(
                f"MusicBrainz lookup by recording MBID failed for {recording_mbid}."
            ) from exc

    def lookup_recordings_by_isrc(self, isrc: str) -> dict[str, Any]:
        if not isrc.strip():
            raise ValueError("ISRC must not be empty.")

        try:
            return self._musicbrainzngs.get_recordings_by_isrc(
                isrc,
                includes=["artists", "releases", "isrcs"],
            )
        except Exception as exc:
            raise RuntimeError(f"MusicBrainz lookup by ISRC failed for {isrc}.") from exc

    def lookup_release_by_mbid(self, release_mbid: str) -> dict[str, Any]:
        if not release_mbid.strip():
            raise ValueError("MusicBrainz release MBID must not be empty.")

        try:
            return self._musicbrainzngs.get_release_by_id(
                release_mbid,
                includes=[
                    "artists",
                    "recordings",
                    "release-groups",
                    "media",
                    "labels",
                    "tags",
                    "url-rels",
                    "artist-rels",
                    "recording-level-rels",
                ],
            )
        except Exception as exc:
            raise RuntimeError(
                f"MusicBrainz lookup by release MBID failed for {release_mbid}."
            ) from exc

    def extract_recording_mbids(self, result: Any) -> list[str]:
        collected_mbids: list[str] = []

        if isinstance(result, dict):
            recording = result.get("recording")
            if isinstance(recording, dict):
                _collect_musicbrainz_recording_ids(recording, collected_mbids)

            isrc_block = result.get("isrc")
            if isinstance(isrc_block, dict):
                recording_list = isrc_block.get("recording-list")
                if isinstance(recording_list, list):
                    for item in recording_list:
                        _collect_musicbrainz_recording_ids(item, collected_mbids)

            recording_list = result.get("recording-list")
            if isinstance(recording_list, list):
                for item in recording_list:
                    _collect_musicbrainz_recording_ids(item, collected_mbids)

        return _deduplicate_strings(collected_mbids)

    def extract_release_mbids(self, result: Any) -> list[str]:
        collected_release_mbids: list[str] = []

        if isinstance(result, dict):
            recording = result.get("recording")
            if isinstance(recording, dict):
                _collect_musicbrainz_release_ids(recording, collected_release_mbids)

            isrc_block = result.get("isrc")
            if isinstance(isrc_block, dict):
                recording_list = isrc_block.get("recording-list")
                if isinstance(recording_list, list):
                    for item in recording_list:
                        _collect_musicbrainz_release_ids(item, collected_release_mbids)

            recording_list = result.get("recording-list")
            if isinstance(recording_list, list):
                for item in recording_list:
                    _collect_musicbrainz_release_ids(item, collected_release_mbids)

        return _deduplicate_strings(collected_release_mbids)


def _collect_musicbrainz_recording_ids(value: Any, collected_mbids: list[str]) -> None:
    if not isinstance(value, dict):
        return

    recording_id = value.get("id")
    if isinstance(recording_id, str) and recording_id.strip():
        collected_mbids.append(recording_id.strip())


def _collect_musicbrainz_release_ids(value: Any, collected_mbids: list[str]) -> None:
    if not isinstance(value, dict):
        return

    release_list = value.get("release-list") or value.get("releases")
    if isinstance(release_list, list):
        for release in release_list:
            if not isinstance(release, dict):
                continue
            release_id = release.get("id")
            if isinstance(release_id, str) and release_id.strip():
                collected_mbids.append(release_id.strip())


def _deduplicate_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered_values: list[str] = []

    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered_values.append(value)

    return ordered_values
