from __future__ import annotations

from pathlib import Path
from typing import Any


def map_cluster_to_release_metadata(
    cluster: dict[str, Any],
    release_selection: dict[str, Any],
) -> dict[str, Any]:
    resolved_results = _coerce_resolved_results(cluster)
    selected_release_payload = release_selection.get("selected_release")
    release = _extract_release_dict(selected_release_payload)

    if not isinstance(release, dict):
        return _build_unmatched_release_mapping(
            resolved_results,
            reason=release_selection.get("reason")
            or "selected release payload was not available",
        )

    release_mbid = _clean_string(release.get("id")) or _clean_string(
        release_selection.get("selected_release_mbid")
    )
    release_group = release.get("release-group")
    release_group_mbid = (
        _clean_string(release_group.get("id"))
        if isinstance(release_group, dict)
        else None
    )
    album_title = _clean_string(release.get("title"))
    album_artist = _extract_release_artist_credit(release)
    album_artist_sort = _extract_artist_credit_sort_phrase(release.get("artist-credit"))
    album_artist_names = _extract_artist_credit_names(release.get("artist-credit"))
    album_artist_ids = _extract_artist_credit_ids(release.get("artist-credit"))
    release_date = _clean_string(release.get("date"))
    release_year = release_date[:4] if release_date and len(release_date) >= 4 else None
    release_group_primary_type, release_group_secondary_types = _extract_release_group_types(
        release.get("release-group")
    )
    release_type_values = [
        value
        for value in [release_group_primary_type, *release_group_secondary_types]
        if value
    ]
    original_date = _extract_original_date(release.get("release-group"))
    original_year = original_date[:4] if original_date and len(original_date) >= 4 else None
    label_names, catalog_numbers = _extract_label_info(release.get("label-info-list"))
    release_genre = _extract_genre(release) or _extract_genre(release.get("release-group"))
    release_publisher = _join_tag_values(label_names)
    release_url = _extract_first_url(release)

    release_tracks = _extract_release_tracks(release)
    track_mappings = _match_local_results_to_release_tracks(
        resolved_results,
        release_tracks,
        release_mbid=release_mbid,
        release_group_mbid=release_group_mbid,
        album_title=album_title,
        album_artist=album_artist,
        album_artist_sort=album_artist_sort,
        album_artist_names=album_artist_names,
        album_artist_ids=album_artist_ids,
        release_date=release_date,
        release_year=release_year,
        release_status=_clean_string(release.get("status")),
        release_country=_clean_string(release.get("country")),
        release_barcode=_clean_string(release.get("barcode")),
        release_asin=_clean_string(release.get("asin")),
        release_type_values=release_type_values,
        original_date=original_date,
        original_year=original_year,
        script=_extract_script(release.get("text-representation")),
        label_names=label_names,
        catalog_numbers=catalog_numbers,
        release_genre=release_genre,
        release_publisher=release_publisher,
        release_url=release_url,
    )

    return {
        "release_mbid": release_mbid,
        "release_group_mbid": release_group_mbid,
        "album_title": album_title,
        "album_artist": album_artist,
        "album_artist_sort": album_artist_sort,
        "album_artist_names": album_artist_names,
        "album_artist_ids": album_artist_ids,
        "release_date": release_date,
        "release_year": release_year,
        "original_date": original_date,
        "original_year": original_year,
        "release_country": _clean_string(release.get("country")),
        "release_status": _clean_string(release.get("status")),
        "release_type_values": release_type_values,
        "script": _extract_script(release.get("text-representation")),
        "barcode": _clean_string(release.get("barcode")),
        "asin": _clean_string(release.get("asin")),
        "genre": release_genre,
        "publisher": release_publisher,
        "url": release_url,
        "label_names": label_names,
        "catalog_numbers": catalog_numbers,
        "tracks": track_mappings,
    }


def _coerce_resolved_results(cluster: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(cluster, dict):
        resolved_results = cluster.get("resolved_results", [])
        return list(resolved_results) if isinstance(resolved_results, list) else []
    return list(cluster)


def _build_unmatched_release_mapping(
    resolved_results: list[dict[str, Any]],
    *,
    reason: str,
) -> dict[str, Any]:
    return {
        "release_mbid": None,
        "release_group_mbid": None,
        "album_title": None,
        "album_artist": None,
        "album_artist_sort": None,
        "album_artist_names": [],
        "album_artist_ids": [],
        "release_date": None,
        "release_year": None,
        "original_date": None,
        "original_year": None,
        "release_country": None,
        "release_status": None,
        "release_type_values": [],
        "script": None,
        "barcode": None,
        "asin": None,
        "genre": None,
        "publisher": None,
        "url": None,
        "label_names": [],
        "catalog_numbers": [],
        "tracks": [
            _build_unmatched_track_mapping(resolved_result, reason=reason)
            for resolved_result in resolved_results
        ],
    }


def _extract_release_dict(selected_release: Any) -> dict[str, Any] | None:
    if not isinstance(selected_release, dict):
        return None
    release = selected_release.get("release")
    return release if isinstance(release, dict) else None


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
        if not isinstance(item, dict):
            continue
        artist = item.get("artist")
        if not isinstance(artist, dict):
            continue
        artist_name = artist.get("name")
        if isinstance(artist_name, str) and artist_name.strip():
            parts.append(artist_name.strip())

    joined = "".join(parts).strip()
    return joined or None


def _extract_track_artist_credit(track: dict[str, Any], recording: dict[str, Any]) -> str | None:
    artist_credit = recording.get("artist-credit-phrase")
    if isinstance(artist_credit, str) and artist_credit.strip():
        return artist_credit.strip()

    artist_credit = track.get("artist-credit-phrase")
    if isinstance(artist_credit, str) and artist_credit.strip():
        return artist_credit.strip()

    artist_credit_list = recording.get("artist-credit") or track.get("artist-credit")
    if not isinstance(artist_credit_list, list):
        return None

    parts: list[str] = []
    for item in artist_credit_list:
        if isinstance(item, str):
            parts.append(item)
            continue
        if not isinstance(item, dict):
            continue
        artist = item.get("artist")
        if not isinstance(artist, dict):
            continue
        artist_name = artist.get("name")
        if isinstance(artist_name, str) and artist_name.strip():
            parts.append(artist_name.strip())

    joined = "".join(parts).strip()
    return joined or None


def _extract_release_tracks(release: dict[str, Any]) -> list[dict[str, Any]]:
    media = release.get("medium-list") or release.get("media")
    if not isinstance(media, list):
        return []

    total_discs = sum(1 for medium in media if isinstance(medium, dict))
    total_tracks = 0
    release_tracks: list[dict[str, Any]] = []

    for medium in media:
        if not isinstance(medium, dict):
            continue

        disc_number = _coerce_int(medium.get("position"))
        track_list = medium.get("track-list") or medium.get("tracks")
        if not isinstance(track_list, list):
            continue

        disc_total_tracks = len(track_list)
        total_tracks += disc_total_tracks
        for track in track_list:
            if not isinstance(track, dict):
                continue

            recording = track.get("recording")
            if not isinstance(recording, dict):
                recording = {}

            recording_mbid = _clean_string(recording.get("id"))
            artist_names = _extract_artist_credit_names(
                recording.get("artist-credit") or track.get("artist-credit")
            )
            artist_ids = _extract_artist_credit_ids(
                recording.get("artist-credit") or track.get("artist-credit")
            )
            release_tracks.append(
                {
                    "track_mbid": _clean_string(track.get("id")),
                    "recording_mbid": recording_mbid,
                    "title": _clean_string(track.get("title"))
                    or _clean_string(recording.get("title")),
                    "artist": _extract_track_artist_credit(track, recording),
                    "artists": artist_names,
                    "artist_sort": _extract_artist_credit_sort_phrase(
                        recording.get("artist-credit") or track.get("artist-credit")
                    ),
                    "artist_ids": artist_ids,
                    "original_artist_ids": [],
                    "track_number": _coerce_int(track.get("position")),
                    "disc_number": disc_number,
                    "total_tracks": disc_total_tracks or None,
                    "total_discs": total_discs or None,
                    "genre": _extract_genre(track) or _extract_genre(recording),
                    "composer": _extract_relation_target_names(track, {"composer"})
                    or _extract_relation_target_names(recording, {"composer"}),
                    "conductor": _extract_relation_target_names(track, {"conductor"})
                    or _extract_relation_target_names(recording, {"conductor"}),
                    "grouping": _clean_string(track.get("grouping"))
                    or _clean_string(recording.get("grouping")),
                    "media": _clean_string(medium.get("format")),
                    "disc_subtitle": _clean_string(medium.get("title")),
                    "subtitle": _clean_string(track.get("subtitle"))
                    or _clean_string(recording.get("subtitle")),
                    "publisher": None,
                    "url": _extract_first_url(track) or _extract_first_url(recording),
                    "originalartist": _extract_relation_target_names(
                        track,
                        {"original artist", "original performer"},
                    )
                    or _extract_relation_target_names(
                        recording,
                        {"original artist", "original performer"},
                    ),
                    "remixer": _extract_relation_target_names(track, {"remixer", "remix"})
                    or _extract_relation_target_names(recording, {"remixer", "remix"}),
                }
            )

    return release_tracks


def _match_local_results_to_release_tracks(
    resolved_results: list[dict[str, Any]],
    release_tracks: list[dict[str, Any]],
    *,
    release_mbid: str | None,
    release_group_mbid: str | None,
    album_title: str | None,
    album_artist: str | None,
    album_artist_sort: str | None,
    album_artist_names: list[str],
    album_artist_ids: list[str],
    release_date: str | None,
    release_year: str | None,
    release_status: str | None,
    release_country: str | None,
    release_barcode: str | None,
    release_asin: str | None,
    release_type_values: list[str],
    original_date: str | None,
    original_year: str | None,
    script: str | None,
    label_names: list[str],
    catalog_numbers: list[str],
    release_genre: str | None,
    release_publisher: str | None,
    release_url: str | None,
) -> list[dict[str, Any]]:
    unmatched_release_indices = set(range(len(release_tracks)))
    mapped_tracks: list[dict[str, Any]] = []

    for resolved_result in resolved_results:
        release_index = _find_best_release_track_index(
            resolved_result,
            release_tracks,
            unmatched_release_indices,
        )
        if release_index is None:
            mapped_tracks.append(
                _build_unmatched_track_mapping(
                    resolved_result,
                    reason="local file could not be matched to selected release track",
                )
            )
            continue

        unmatched_release_indices.discard(release_index)
        release_track = release_tracks[release_index]
        mapped_tracks.append(
            _build_matched_track_mapping(
                resolved_result,
                release_track,
                release_mbid=release_mbid,
                release_group_mbid=release_group_mbid,
                album_title=album_title,
                album_artist=album_artist,
                album_artist_sort=album_artist_sort,
                album_artist_names=album_artist_names,
                album_artist_ids=album_artist_ids,
                release_date=release_date,
                release_year=release_year,
                release_status=release_status,
                release_country=release_country,
                release_barcode=release_barcode,
                release_asin=release_asin,
                release_type_values=release_type_values,
                original_date=original_date,
                original_year=original_year,
                script=script,
                label_names=label_names,
                catalog_numbers=catalog_numbers,
                release_genre=release_genre,
                release_publisher=release_publisher,
                release_url=release_url,
            )
        )

    return mapped_tracks


def _find_best_release_track_index(
    resolved_result: dict[str, Any],
    release_tracks: list[dict[str, Any]],
    unmatched_release_indices: set[int],
) -> int | None:
    primary_recording_mbid = _clean_string(resolved_result.get("recording_mbid"))
    candidate_recording_mbids = _deduplicate_strings(
        [
            value
            for value in [
                primary_recording_mbid,
                *resolved_result.get("candidate_recording_mbids", []),
            ]
            if isinstance(value, str)
        ]
    )

    if primary_recording_mbid:
        for release_index in sorted(unmatched_release_indices):
            if release_tracks[release_index]["recording_mbid"] == primary_recording_mbid:
                return release_index

    candidate_recording_mbid_set = set(candidate_recording_mbids)
    if candidate_recording_mbid_set:
        for release_index in sorted(unmatched_release_indices):
            if release_tracks[release_index]["recording_mbid"] in candidate_recording_mbid_set:
                return release_index

    return None


def _build_matched_track_mapping(
    resolved_result: dict[str, Any],
    release_track: dict[str, Any],
    *,
    release_mbid: str | None,
    release_group_mbid: str | None,
    album_title: str | None,
    album_artist: str | None,
    album_artist_sort: str | None,
    album_artist_names: list[str],
    album_artist_ids: list[str],
    release_date: str | None,
    release_year: str | None,
    release_status: str | None,
    release_country: str | None,
    release_barcode: str | None,
    release_asin: str | None,
    release_type_values: list[str],
    original_date: str | None,
    original_year: str | None,
    script: str | None,
    label_names: list[str],
    catalog_numbers: list[str],
    release_genre: str | None,
    release_publisher: str | None,
    release_url: str | None,
) -> dict[str, Any]:
    original_path = Path(resolved_result["original_path"])
    return {
        "status": "matched",
        "reason": None,
        "original_path": str(original_path),
        "extension": original_path.suffix,
        "recording_mbid": release_track["recording_mbid"] or _clean_string(
            resolved_result.get("recording_mbid")
        ),
        "release_mbid": release_mbid,
        "release_group_mbid": release_group_mbid,
        "track_mbid": release_track["track_mbid"],
        "title": release_track["title"],
        "artist": release_track["artist"] or album_artist,
        "artists": list(release_track["artists"]),
        "artist_sort": release_track["artist_sort"],
        "album_artist_sort": album_artist_sort,
        "album_artists": list(album_artist_names),
        "album": album_title,
        "album_artist": album_artist,
        "genre": release_track["genre"] or release_genre,
        "composer": release_track["composer"],
        "conductor": release_track["conductor"],
        "grouping": release_track["grouping"],
        "track_number": release_track["track_number"],
        "disc_number": release_track["disc_number"],
        "total_tracks": release_track["total_tracks"],
        "total_discs": release_track["total_discs"],
        "release_status": release_status,
        "release_country": release_country,
        "releasetype": list(release_type_values),
        "media": release_track["media"],
        "disc_subtitle": release_track["disc_subtitle"],
        "subtitle": release_track["subtitle"],
        "release_date": release_date,
        "release_year": release_year,
        "original_date": original_date,
        "original_year": original_year,
        "script": script,
        "barcode": release_barcode,
        "asin": release_asin,
        "publisher": release_track["publisher"] or release_publisher,
        "url": release_track["url"] or release_url,
        "originalartist": release_track["originalartist"],
        "remixer": release_track["remixer"],
        "label": list(label_names),
        "catalognumber": list(catalog_numbers),
        "musicbrainz_albumartistid": list(album_artist_ids),
        "musicbrainz_artistid": list(release_track["artist_ids"]),
        "musicbrainz_originalartistid": list(release_track["original_artist_ids"]),
        "musicbrainz_discid": None,
        "musicbrainz_originalalbumid": None,
        "isrc": _clean_string(resolved_result.get("isrc")),
        "acoustid_id": _clean_string(resolved_result.get("acoustid_id")),
        "acoustid_fingerprint": None,
        "albumsort": None,
        "bpm": None,
        "copyright": None,
        "encodedby": None,
        "encodersettings": None,
        "key": None,
        "lyrics": None,
        "musicip_fingerprint": None,
        "musicip_puid": None,
        "syncedlyrics": None,
    }


def _build_unmatched_track_mapping(
    resolved_result: dict[str, Any],
    *,
    reason: str,
) -> dict[str, Any]:
    original_path = Path(resolved_result["original_path"])
    return {
        "status": "unmatched_in_release",
        "reason": reason,
        "original_path": str(original_path),
        "extension": original_path.suffix,
        "recording_mbid": _clean_string(resolved_result.get("recording_mbid")),
        "release_mbid": None,
        "release_group_mbid": None,
        "track_mbid": None,
        "title": original_path.stem,
        "artist": None,
        "artists": [],
        "artist_sort": None,
        "album_artist_sort": None,
        "album_artists": [],
        "album": None,
        "album_artist": None,
        "genre": None,
        "composer": None,
        "conductor": None,
        "grouping": None,
        "track_number": None,
        "disc_number": None,
        "total_tracks": None,
        "total_discs": None,
        "release_status": None,
        "release_country": None,
        "releasetype": [],
        "media": None,
        "disc_subtitle": None,
        "subtitle": None,
        "release_date": None,
        "release_year": None,
        "original_date": None,
        "original_year": None,
        "script": None,
        "barcode": None,
        "asin": None,
        "publisher": None,
        "url": None,
        "originalartist": None,
        "remixer": None,
        "label": [],
        "catalognumber": [],
        "musicbrainz_albumartistid": [],
        "musicbrainz_artistid": [],
        "musicbrainz_originalartistid": [],
        "musicbrainz_discid": None,
        "musicbrainz_originalalbumid": None,
        "isrc": _clean_string(resolved_result.get("isrc")),
        "acoustid_id": _clean_string(resolved_result.get("acoustid_id")),
        "acoustid_fingerprint": None,
        "albumsort": None,
        "bpm": None,
        "copyright": None,
        "encodedby": None,
        "encodersettings": None,
        "key": None,
        "lyrics": None,
        "musicip_fingerprint": None,
        "musicip_puid": None,
        "syncedlyrics": None,
    }


def _deduplicate_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered_values: list[str] = []

    for value in values:
        cleaned_value = value.strip()
        if not cleaned_value or cleaned_value in seen:
            continue
        seen.add(cleaned_value)
        ordered_values.append(cleaned_value)

    return ordered_values


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


def _extract_artist_credit_names(artist_credit: Any) -> list[str]:
    names: list[str] = []
    if not isinstance(artist_credit, list):
        return names

    for item in artist_credit:
        if not isinstance(item, dict):
            continue
        artist = item.get("artist")
        if not isinstance(artist, dict):
            continue
        artist_name = _clean_string(artist.get("name"))
        if artist_name:
            names.append(artist_name)

    return names


def _extract_artist_credit_ids(artist_credit: Any) -> list[str]:
    ids: list[str] = []
    if not isinstance(artist_credit, list):
        return ids

    for item in artist_credit:
        if not isinstance(item, dict):
            continue
        artist = item.get("artist")
        if not isinstance(artist, dict):
            continue
        artist_id = _clean_string(artist.get("id"))
        if artist_id:
            ids.append(artist_id)

    return ids


def _extract_artist_credit_sort_phrase(artist_credit: Any) -> str | None:
    if not isinstance(artist_credit, list):
        return None

    parts: list[str] = []
    for item in artist_credit:
        if isinstance(item, str):
            parts.append(item)
            continue
        if not isinstance(item, dict):
            continue
        artist = item.get("artist")
        if not isinstance(artist, dict):
            continue
        sort_name = _clean_string(artist.get("sort-name")) or _clean_string(artist.get("name"))
        if sort_name:
            parts.append(sort_name)
        join_phrase = _clean_string(item.get("joinphrase"))
        if join_phrase:
            parts.append(join_phrase)

    joined = "".join(parts).strip()
    return joined or None


def _extract_release_group_types(release_group: Any) -> tuple[str | None, list[str]]:
    if not isinstance(release_group, dict):
        return None, []

    primary_type = _clean_string(release_group.get("primary-type")) or _clean_string(
        release_group.get("type")
    )
    secondary_types = release_group.get("secondary-type-list")
    if not isinstance(secondary_types, list):
        return primary_type, []

    cleaned_secondary_types = [
        cleaned_value
        for value in secondary_types
        if (cleaned_value := _clean_string(value)) is not None
    ]
    return primary_type, cleaned_secondary_types


def _extract_original_date(release_group: Any) -> str | None:
    if not isinstance(release_group, dict):
        return None
    return _clean_string(release_group.get("first-release-date"))


def _extract_script(text_representation: Any) -> str | None:
    if not isinstance(text_representation, dict):
        return None
    return _clean_string(text_representation.get("script"))


def _extract_label_info(label_info_list: Any) -> tuple[list[str], list[str]]:
    label_names: list[str] = []
    catalog_numbers: list[str] = []
    if not isinstance(label_info_list, list):
        return label_names, catalog_numbers

    for item in label_info_list:
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        if isinstance(label, dict):
            label_name = _clean_string(label.get("name"))
            if label_name:
                label_names.append(label_name)
        catalog_number = _clean_string(item.get("catalog-number"))
        if catalog_number:
            catalog_numbers.append(catalog_number)

    return label_names, catalog_numbers


def _join_tag_values(values: list[str]) -> str | None:
    deduplicated_values = _deduplicate_strings(values)
    if not deduplicated_values:
        return None
    return "; ".join(deduplicated_values)


def _extract_genre(entity: Any) -> str | None:
    if not isinstance(entity, dict):
        return None

    genre_list = entity.get("genre-list") or entity.get("genres")
    if isinstance(genre_list, list):
        genre_names = [
            genre_name
            for item in genre_list
            if isinstance(item, dict)
            and (genre_name := _clean_string(item.get("name"))) is not None
        ]
        if genre_names:
            return "; ".join(_deduplicate_strings(genre_names))

    tag_list = entity.get("tag-list") or entity.get("tags")
    if isinstance(tag_list, list):
        tag_names = [
            tag_name
            for item in tag_list
            if isinstance(item, dict)
            and (tag_name := _clean_string(item.get("name"))) is not None
        ]
        if tag_names:
            return "; ".join(_deduplicate_strings(tag_names))

    return None


def _extract_first_url(entity: Any) -> str | None:
    for relation in _iter_relations(entity):
        target = _extract_relation_url_target(relation)
        if target:
            return target
    return None


def _extract_relation_target_names(entity: Any, relation_types: set[str]) -> str | None:
    normalized_relation_types = {value.casefold() for value in relation_types}
    names: list[str] = []

    for relation in _iter_relations(entity):
        relation_type = _clean_string(relation.get("type"))
        if relation_type is None or relation_type.casefold() not in normalized_relation_types:
            continue

        target_name = _extract_relation_name_target(relation)
        if target_name:
            names.append(target_name)

    deduplicated_names = _deduplicate_strings(names)
    if not deduplicated_names:
        return None
    return "; ".join(deduplicated_names)


def _iter_relations(entity: Any) -> list[dict[str, Any]]:
    if not isinstance(entity, dict):
        return []

    candidate_keys = (
        "artist-relation-list",
        "work-relation-list",
        "relation-list",
        "relations",
        "url-relation-list",
    )
    relations: list[dict[str, Any]] = []

    for key in candidate_keys:
        relation_list = entity.get(key)
        if not isinstance(relation_list, list):
            continue
        for item in relation_list:
            if isinstance(item, dict):
                relations.append(item)

    return relations


def _extract_relation_name_target(relation: dict[str, Any]) -> str | None:
    for key in ("artist", "work", "url", "target-credit"):
        target = relation.get(key)
        if isinstance(target, dict):
            target_name = _clean_string(target.get("name")) or _clean_string(target.get("resource"))
            if target_name:
                return target_name
        elif isinstance(target, str):
            target_name = _clean_string(target)
            if target_name:
                return target_name

    return _clean_string(relation.get("target"))


def _extract_relation_url_target(relation: dict[str, Any]) -> str | None:
    url_target = relation.get("url")
    if isinstance(url_target, dict):
        return _clean_string(url_target.get("resource"))
    if isinstance(url_target, str):
        return _clean_string(url_target)

    relation_target_type = _clean_string(relation.get("target-type"))
    if relation_target_type is not None and relation_target_type.casefold() != "url":
        return None

    target = _clean_string(relation.get("target"))
    if target and (target.startswith("http://") or target.startswith("https://")):
        return target
    return None
