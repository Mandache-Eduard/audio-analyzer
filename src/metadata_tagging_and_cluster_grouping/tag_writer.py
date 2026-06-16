from __future__ import annotations

import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable

from mutagen.aiff import AIFF
from mutagen.asf import ASF, ASFUnicodeAttribute
from mutagen.flac import FLAC
from mutagen.id3 import (
    TALB,
    TBPM,
    TCOM,
    TCON,
    TCOP,
    TDOR,
    TDRL,
    TENC,
    TIT1,
    TIT2,
    TIT3,
    TKEY,
    TMED,
    TOPE,
    TPE1,
    TPE2,
    TPE3,
    TPE4,
    TPOS,
    TPUB,
    TRCK,
    SYLT,
    TSOA,
    TSOP,
    TSRC,
    TSSE,
    TSST,
    TXXX,
    UFID,
    USLT,
    WXXX,
)
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4FreeForm
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE
from tqdm import tqdm

VORBIS_EXTENSIONS = frozenset({".flac", ".ogg", ".opus"})
ID3_EXTENSIONS = frozenset({".mp3", ".wav", ".aiff"})
MP4_EXTENSIONS = frozenset({".m4a"})
ASF_EXTENSIONS = frozenset({".wma"})


def write_tags_to_copied_files(copy_results: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    copy_result_list = list(copy_results)
    if not copy_result_list:
        return []

    cores = os.cpu_count() or 1
    max_workers = max(1, min(cores // 2, 6))
    reserved_output_paths: set[str] = set()
    reservation_lock = threading.Lock()
    tag_write_results: list[dict[str, Any] | None] = [None] * len(copy_result_list)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {
            executor.submit(
                _write_tags_to_copied_file_reserved,
                copy_result,
                reserved_output_paths,
                reservation_lock,
            ): index
            for index, copy_result in enumerate(copy_result_list)
        }

        for future in tqdm(
            as_completed(future_to_index),
            total=len(future_to_index),
            desc="Files tagged",
            unit="file",
        ):
            index = future_to_index[future]
            copy_result = copy_result_list[index]
            try:
                tag_write_results[index] = future.result()
            except Exception as exc:
                tag_write_results[index] = _tag_write_exception_result(copy_result, exc)

    return [
        tag_write_result
        if tag_write_result is not None
        else _tag_write_exception_result(
            copy_result,
            RuntimeError("tag worker produced no result"),
        )
        for copy_result, tag_write_result in zip(copy_result_list, tag_write_results)
    ]


def write_tags_to_copied_file(copy_result: dict[str, Any]) -> dict[str, Any]:
    copy_status = copy_result.get("status")
    copied_path = copy_result.get("copied_path")
    metadata = copy_result.get("metadata") or {}

    if copy_status != "copied":
        return {
            "copied_path": copied_path,
            "status": "skipped",
            "reason": copy_result.get("reason") or "copy step did not produce a writable file",
        }

    if metadata.get("status") == "unmatched_in_release":
        return {
            "copied_path": copied_path,
            "status": "skipped",
            "reason": "unmatched files are copied but not tagged",
        }

    output_path = Path(copied_path)
    extension = output_path.suffix.lower()

    try:
        if extension in VORBIS_EXTENSIONS:
            _write_vorbis_style_tags(output_path, metadata, extension)
        elif extension in ID3_EXTENSIONS:
            _write_id3_tags(output_path, metadata, extension)
        elif extension in MP4_EXTENSIONS:
            _write_mp4_tags(output_path, metadata)
        elif extension in ASF_EXTENSIONS:
            _write_asf_tags(output_path, metadata)
        else:
            return {
                "copied_path": str(output_path),
                "status": "unsupported_format",
                "reason": f"tag writing is not implemented for {extension or 'files without extension'}",
            }
    except Exception as exc:
        return {
            "copied_path": str(output_path),
            "status": "error",
            "reason": str(exc),
        }

    return {
        "copied_path": str(output_path),
        "status": "tagged",
        "reason": "best-effort WAV ID3 chunk tagging"
        if extension == ".wav"
        else None,
    }


def _write_tags_to_copied_file_reserved(
    copy_result: dict[str, Any],
    reserved_output_paths: set[str],
    reservation_lock: threading.Lock,
) -> dict[str, Any]:
    if copy_result.get("status") != "copied":
        return write_tags_to_copied_file(copy_result)

    copied_path = copy_result.get("copied_path")
    if copied_path is None:
        return write_tags_to_copied_file(copy_result)

    output_path = Path(copied_path)
    output_path_key = _normalize_output_path_key(output_path)

    with reservation_lock:
        if output_path_key in reserved_output_paths:
            return {
                "copied_path": str(output_path),
                "status": "error",
                "reason": "copied output path is already being tagged by another worker",
            }
        reserved_output_paths.add(output_path_key)

    return write_tags_to_copied_file(copy_result)


def _normalize_output_path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


def _tag_write_exception_result(copy_result: dict[str, Any], exc: Exception) -> dict[str, Any]:
    copied_path = copy_result.get("copied_path")
    return {
        "copied_path": str(copied_path) if copied_path is not None else None,
        "status": "error",
        "reason": str(exc),
    }


def _write_vorbis_style_tags(output_path: Path, metadata: dict[str, Any], extension: str) -> None:
    if extension == ".flac":
        audio_file = FLAC(output_path)
    elif extension == ".ogg":
        audio_file = OggVorbis(output_path)
    else:
        audio_file = OggOpus(output_path)

    if audio_file.tags is None:
        audio_file.add_tags()

    tag_values = _build_vorbis_style_tag_values(metadata)
    lyrics_value = _clean_string(metadata.get("lyrics"))
    if lyrics_value is not None:
        tag_values["LYRICS"] = [lyrics_value]

    for key, values in tag_values.items():
        audio_file[key] = values

    audio_file.save()


def _write_id3_tags(output_path: Path, metadata: dict[str, Any], extension: str) -> None:
    if extension == ".mp3":
        audio_file = MP3(output_path)
    elif extension == ".wav":
        audio_file = WAVE(output_path)
    else:
        audio_file = AIFF(output_path)

    if audio_file.tags is None:
        audio_file.add_tags()

    tags = audio_file.tags
    _set_id3_text_frame(tags, "TALB", TALB, metadata.get("album"))
    _set_id3_text_frame(tags, "TPE2", TPE2, metadata.get("album_artist"))
    _set_id3_text_frame(tags, "TPE1", TPE1, _joined_or_single(metadata.get("artists"), metadata.get("artist")))
    _set_id3_text_frame(tags, "TSOA", TSOA, metadata.get("albumsort"))
    _set_id3_text_frame(tags, "TSOP", TSOP, metadata.get("artist_sort"))
    _set_id3_text_frame(tags, "TCON", TCON, metadata.get("genre"))
    _set_id3_text_frame(tags, "TCOM", TCOM, metadata.get("composer"))
    _set_id3_text_frame(tags, "TPE3", TPE3, metadata.get("conductor"))
    _set_id3_text_frame(tags, "TIT1", TIT1, metadata.get("grouping"))
    _set_id3_text_frame(tags, "TIT2", TIT2, metadata.get("title"))
    _set_id3_text_frame(tags, "TIT3", TIT3, metadata.get("subtitle"))
    _set_id3_text_frame(tags, "TDRL", TDRL, metadata.get("release_date"))
    _set_id3_text_frame(tags, "TDOR", TDOR, metadata.get("original_date"))
    _set_id3_text_frame(tags, "TRCK", TRCK, _format_track_number(metadata))
    _set_id3_text_frame(tags, "TPOS", TPOS, _format_disc_number(metadata))
    _set_id3_text_frame(tags, "TSRC", TSRC, metadata.get("isrc"))
    _set_id3_text_frame(tags, "TMED", TMED, metadata.get("media"))
    _set_id3_text_frame(tags, "TSST", TSST, metadata.get("disc_subtitle"))
    _set_id3_text_frame(tags, "TBPM", TBPM, metadata.get("bpm"))
    _set_id3_text_frame(tags, "TKEY", TKEY, metadata.get("key"))
    _set_id3_text_frame(tags, "TCOP", TCOP, metadata.get("copyright"))
    _set_id3_text_frame(tags, "TENC", TENC, metadata.get("encodedby"))
    _set_id3_text_frame(tags, "TSSE", TSSE, metadata.get("encodersettings"))
    _set_id3_text_frame(tags, "TOPE", TOPE, metadata.get("originalartist"))
    _set_id3_text_frame(tags, "TPE4", TPE4, metadata.get("remixer"))
    _set_id3_text_frame(tags, "TPUB", TPUB, metadata.get("publisher"))

    _set_id3_uslt(tags, metadata.get("lyrics"))
    _set_id3_sylt(tags, metadata.get("syncedlyrics"))
    _set_id3_ufid(tags, metadata.get("recording_mbid"))
    _set_id3_wxxx(tags, "URL", metadata.get("url"))

    _set_id3_txxx(tags, "ALBUMARTISTSORT", metadata.get("album_artist_sort"))
    _set_id3_txxx(tags, "ARTISTS", _ensure_list(metadata.get("artists")))
    _set_id3_txxx(tags, "ASIN", metadata.get("asin"))
    _set_id3_txxx(tags, "BARCODE", metadata.get("barcode"))
    _set_id3_txxx(tags, "CATALOGNUMBER", _ensure_list(metadata.get("catalognumber")))
    _set_id3_txxx(tags, "LABEL", _ensure_list(metadata.get("label")))
    _set_id3_txxx(tags, "MusicBrainz Album Artist Id", _ensure_list(metadata.get("musicbrainz_albumartistid")))
    _set_id3_txxx(tags, "MusicBrainz Album Id", metadata.get("release_mbid"))
    _set_id3_txxx(tags, "MusicBrainz Artist Id", _ensure_list(metadata.get("musicbrainz_artistid")))
    _set_id3_txxx(tags, "MusicBrainz Disc Id", metadata.get("musicbrainz_discid"))
    _set_id3_txxx(tags, "MusicBrainz Original Album Id", metadata.get("musicbrainz_originalalbumid"))
    _set_id3_txxx(tags, "MusicBrainz Original Artist Id", _ensure_list(metadata.get("musicbrainz_originalartistid")))
    _set_id3_txxx(tags, "MusicBrainz Release Group Id", metadata.get("release_group_mbid"))
    _set_id3_txxx(tags, "MusicBrainz Track Id", metadata.get("track_mbid"))
    _set_id3_txxx(tags, "ORIGINALYEAR", metadata.get("original_year"))
    _set_id3_txxx(tags, "RELEASECOUNTRY", metadata.get("release_country"))
    _set_id3_txxx(tags, "RELEASESTATUS", metadata.get("release_status"))
    _set_id3_txxx(tags, "RELEASETYPE", _ensure_list(metadata.get("releasetype")))
    _set_id3_txxx(tags, "SCRIPT", metadata.get("script"))
    _set_id3_txxx(tags, "Acoustid Id", metadata.get("acoustid_id"))
    _set_id3_txxx(tags, "Acoustid Fingerprint", metadata.get("acoustid_fingerprint"))
    _set_id3_txxx(tags, "MusicIP Fingerprint", metadata.get("musicip_fingerprint"))
    _set_id3_txxx(tags, "MusicIP PUID", metadata.get("musicip_puid"))

    audio_file.save(v2_version=4)


def _write_mp4_tags(output_path: Path, metadata: dict[str, Any]) -> None:
    audio_file = MP4(output_path)
    if audio_file.tags is None:
        audio_file.add_tags()

    tags = audio_file.tags
    _set_mp4_text(tags, "\xa9alb", metadata.get("album"))
    _set_mp4_text(tags, "aART", metadata.get("album_artist"))
    _set_mp4_text(tags, "\xa9ART", _joined_or_single(metadata.get("artists"), metadata.get("artist")))
    _set_mp4_text(tags, "\xa9nam", metadata.get("title"))
    _set_mp4_text(tags, "\xa9day", metadata.get("release_date"))
    _set_mp4_text(tags, "\xa9gen", metadata.get("genre"))
    _set_mp4_text(tags, "\xa9wrt", metadata.get("composer"))
    _set_mp4_text(tags, "\xa9grp", metadata.get("grouping"))
    _set_mp4_text(tags, "\xa9lyr", metadata.get("lyrics"))
    _set_mp4_text(tags, "soal", metadata.get("albumsort"))
    _set_mp4_text(tags, "soaa", metadata.get("album_artist_sort"))
    _set_mp4_text(tags, "soar", metadata.get("artist_sort"))
    _set_mp4_track_numbers(tags, metadata)

    _set_mp4_freeform(tags, "Artists", _ensure_list(metadata.get("artists")))
    _set_mp4_freeform(tags, "Album Artists", _ensure_list(metadata.get("album_artists")))
    _set_mp4_freeform(tags, "ASIN", metadata.get("asin"))
    _set_mp4_freeform(tags, "BARCODE", metadata.get("barcode"))
    _set_mp4_freeform(tags, "CATALOGNUMBER", _ensure_list(metadata.get("catalognumber")))
    _set_mp4_freeform(tags, "DISCSUBTITLE", metadata.get("disc_subtitle"))
    _set_mp4_freeform(tags, "ISRC", metadata.get("isrc"))
    _set_mp4_freeform(tags, "LABEL", _ensure_list(metadata.get("label")))
    _set_mp4_freeform(tags, "MEDIA", metadata.get("media"))
    _set_mp4_freeform(tags, "MusicBrainz Album Artist Id", _ensure_list(metadata.get("musicbrainz_albumartistid")))
    _set_mp4_freeform(tags, "MusicBrainz Album Id", metadata.get("release_mbid"))
    _set_mp4_freeform(tags, "MusicBrainz Artist Id", _ensure_list(metadata.get("musicbrainz_artistid")))
    _set_mp4_freeform(tags, "MusicBrainz Disc Id", metadata.get("musicbrainz_discid"))
    _set_mp4_freeform(tags, "MusicBrainz Original Album Id", metadata.get("musicbrainz_originalalbumid"))
    _set_mp4_freeform(tags, "MusicBrainz Original Artist Id", _ensure_list(metadata.get("musicbrainz_originalartistid")))
    _set_mp4_freeform(tags, "MusicBrainz Recording Id", metadata.get("recording_mbid"))
    _set_mp4_freeform(tags, "MusicBrainz Release Group Id", metadata.get("release_group_mbid"))
    _set_mp4_freeform(tags, "MusicBrainz Track Id", metadata.get("track_mbid"))
    _set_mp4_freeform(tags, "Original Date", metadata.get("original_date"))
    _set_mp4_freeform(tags, "Original Year", metadata.get("original_year"))
    _set_mp4_freeform(tags, "Release Country", metadata.get("release_country"))
    _set_mp4_freeform(tags, "Release Status", metadata.get("release_status"))
    _set_mp4_freeform(tags, "Release Type", _ensure_list(metadata.get("releasetype")))
    _set_mp4_freeform(tags, "SCRIPT", metadata.get("script"))
    _set_mp4_freeform(tags, "Acoustid Id", metadata.get("acoustid_id"))
    _set_mp4_freeform(tags, "Acoustid Fingerprint", metadata.get("acoustid_fingerprint"))
    _set_mp4_freeform(tags, "BPM", metadata.get("bpm"))
    _set_mp4_freeform(tags, "Copyright", metadata.get("copyright"))
    _set_mp4_freeform(tags, "Encoded By", metadata.get("encodedby"))
    _set_mp4_freeform(tags, "Encoder Settings", metadata.get("encodersettings"))
    _set_mp4_freeform(tags, "Initial Key", metadata.get("key"))
    _set_mp4_freeform(tags, "Subtitle", metadata.get("subtitle"))
    _set_mp4_freeform(tags, "Publisher", metadata.get("publisher"))
    _set_mp4_freeform(tags, "URL", metadata.get("url"))
    _set_mp4_freeform(tags, "Original Artist", metadata.get("originalartist"))
    _set_mp4_freeform(tags, "Remixer", metadata.get("remixer"))
    _set_mp4_freeform(tags, "Conductor", metadata.get("conductor"))
    _set_mp4_freeform(tags, "MusicIP Fingerprint", metadata.get("musicip_fingerprint"))
    _set_mp4_freeform(tags, "MusicIP PUID", metadata.get("musicip_puid"))

    audio_file.save()


def _write_asf_tags(output_path: Path, metadata: dict[str, Any]) -> None:
    audio_file = ASF(output_path)
    if audio_file.tags is None:
        audio_file.add_tags()

    _set_asf_values(audio_file, "WM/AlbumTitle", _ensure_list(metadata.get("album")))
    _set_asf_values(audio_file, "WM/AlbumArtist", _ensure_list(metadata.get("album_artist")))
    _set_asf_values(audio_file, "Author", _ensure_list(_joined_or_single(metadata.get("artists"), metadata.get("artist"))))
    _set_asf_values(audio_file, "Title", _ensure_list(metadata.get("title")))
    _set_asf_values(audio_file, "WM/TrackNumber", _ensure_list(_coerce_tag_string(metadata.get("track_number"))))
    _set_asf_values(audio_file, "WM/PartOfSet", _ensure_list(_format_disc_number(metadata)))
    _set_asf_values(audio_file, "WM/Year", _ensure_list(metadata.get("release_date")))
    _set_asf_values(audio_file, "WM/Genre", _ensure_list(metadata.get("genre")))
    _set_asf_values(audio_file, "WM/Composer", _ensure_list(metadata.get("composer")))
    _set_asf_values(audio_file, "WM/Conductor", _ensure_list(metadata.get("conductor")))
    _set_asf_values(audio_file, "WM/SubTitle", _ensure_list(metadata.get("subtitle")))
    _set_asf_values(audio_file, "WM/Lyrics", _ensure_list(metadata.get("lyrics")))
    _set_asf_values(audio_file, "WM/Publisher", _ensure_list(metadata.get("publisher")))
    _set_asf_values(audio_file, "LABEL", _ensure_list(metadata.get("label")))
    _set_asf_values(audio_file, "WM/BeatsPerMinute", _ensure_list(_coerce_tag_string(metadata.get("bpm"))))
    _set_asf_values(audio_file, "WM/ContentGroupDescription", _ensure_list(metadata.get("grouping")))
    _set_asf_values(audio_file, "WM/AuthorURL", _ensure_list(metadata.get("url")))
    _set_asf_values(audio_file, "WM/OriginalArtist", _ensure_list(metadata.get("originalartist")))
    _set_asf_values(audio_file, "WM/ModifiedBy", _ensure_list(metadata.get("remixer")))
    _set_asf_values(audio_file, "MusicBrainz/Album Artist Id", _ensure_list(metadata.get("musicbrainz_albumartistid")))
    _set_asf_values(audio_file, "MusicBrainz/Album Id", _ensure_list(metadata.get("release_mbid")))
    _set_asf_values(audio_file, "MusicBrainz/Artist Id", _ensure_list(metadata.get("musicbrainz_artistid")))
    _set_asf_values(audio_file, "MusicBrainz/Disc Id", _ensure_list(metadata.get("musicbrainz_discid")))
    _set_asf_values(audio_file, "MusicBrainz/Original Album Id", _ensure_list(metadata.get("musicbrainz_originalalbumid")))
    _set_asf_values(audio_file, "MusicBrainz/Original Artist Id", _ensure_list(metadata.get("musicbrainz_originalartistid")))
    _set_asf_values(audio_file, "MusicBrainz/Recording Id", _ensure_list(metadata.get("recording_mbid")))
    _set_asf_values(audio_file, "MusicBrainz/Release Group Id", _ensure_list(metadata.get("release_group_mbid")))
    _set_asf_values(audio_file, "MusicBrainz/Track Id", _ensure_list(metadata.get("track_mbid")))
    _set_asf_values(audio_file, "Acoustid/Id", _ensure_list(metadata.get("acoustid_id")))
    _set_asf_values(audio_file, "Acoustid/Fingerprint", _ensure_list(metadata.get("acoustid_fingerprint")))
    _set_asf_values(audio_file, "ReleaseCountry", _ensure_list(metadata.get("release_country")))
    _set_asf_values(audio_file, "ReleaseStatus", _ensure_list(metadata.get("release_status")))
    _set_asf_values(audio_file, "ReleaseType", _ensure_list(metadata.get("releasetype")))
    _set_asf_values(audio_file, "SCRIPT", _ensure_list(metadata.get("script")))
    _set_asf_values(audio_file, "ISRC", _ensure_list(metadata.get("isrc")))
    _set_asf_values(audio_file, "ASIN", _ensure_list(metadata.get("asin")))
    _set_asf_values(audio_file, "BARCODE", _ensure_list(metadata.get("barcode")))
    _set_asf_values(audio_file, "CATALOGNUMBER", _ensure_list(metadata.get("catalognumber")))
    _set_asf_values(audio_file, "OriginalDate", _ensure_list(metadata.get("original_date")))
    _set_asf_values(audio_file, "OriginalYear", _ensure_list(metadata.get("original_year")))
    _set_asf_values(audio_file, "Media", _ensure_list(metadata.get("media")))
    _set_asf_values(audio_file, "DiscSubtitle", _ensure_list(metadata.get("disc_subtitle")))
    _set_asf_values(audio_file, "MusicIP/Fingerprint", _ensure_list(metadata.get("musicip_fingerprint")))
    _set_asf_values(audio_file, "MusicIP/PUID", _ensure_list(metadata.get("musicip_puid")))

    audio_file.save()


def _build_common_tag_values(metadata: dict[str, Any]) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}

    _set_if_present(values, "album", metadata.get("album"))
    _set_if_present(values, "albumartist", metadata.get("album_artist"))
    _set_if_present(values, "albumartistsort", metadata.get("album_artist_sort"))
    _set_if_present(values, "artist", metadata.get("artist"))
    _set_list_if_present(values, "artists", metadata.get("artists"))
    _set_if_present(values, "artistsort", metadata.get("artist_sort"))
    _set_if_present(values, "titlesort", metadata.get("titlesort"))
    _set_if_present(values, "genre", metadata.get("genre"))
    _set_if_present(values, "composer", metadata.get("composer"))
    _set_if_present(values, "composersort", metadata.get("composersort"))
    _set_if_present(values, "lyricist", metadata.get("lyricist"))
    _set_if_present(values, "writer", metadata.get("writer"))
    _set_if_present(values, "arranger", metadata.get("arranger"))
    _set_if_present(values, "conductor", metadata.get("conductor"))
    _set_list_if_present(values, "performer", metadata.get("performer"))
    _set_if_present(values, "producer", metadata.get("producer"))
    _set_if_present(values, "engineer", metadata.get("engineer"))
    _set_if_present(values, "mixer", metadata.get("mixer"))
    _set_if_present(values, "djmixer", metadata.get("djmixer"))
    _set_if_present(values, "director", metadata.get("director"))
    _set_if_present(values, "grouping", metadata.get("grouping"))
    _set_if_present(values, "work", metadata.get("work"))
    _set_if_present(values, "movement", metadata.get("movement"))
    _set_if_present(values, "movementnumber", metadata.get("movementnumber"))
    _set_if_present(values, "movementtotal", metadata.get("movementtotal"))
    _set_if_present(values, "showmovement", metadata.get("showmovement"))
    _set_if_present(values, "mood", metadata.get("mood"))
    _set_if_present(values, "language", metadata.get("language"))
    _set_if_present(values, "comment", metadata.get("comment"))
    _set_if_present(values, "compilation", metadata.get("compilation"))
    _set_if_present(values, "asin", metadata.get("asin"))
    _set_if_present(values, "barcode", metadata.get("barcode"))
    _set_list_if_present(values, "catalognumber", metadata.get("catalognumber"))
    _set_if_present(values, "date", metadata.get("release_date"))
    _set_if_present(values, "discnumber", _coerce_tag_string(metadata.get("disc_number")))
    _set_if_present(values, "discsubtitle", metadata.get("disc_subtitle"))
    _set_if_present(values, "isrc", metadata.get("isrc"))
    _set_list_if_present(values, "label", metadata.get("label"))
    _set_if_present(values, "media", metadata.get("media"))
    _set_list_if_present(values, "musicbrainz_albumartistid", metadata.get("musicbrainz_albumartistid"))
    _set_if_present(values, "musicbrainz_albumid", metadata.get("release_mbid"))
    _set_list_if_present(values, "musicbrainz_artistid", metadata.get("musicbrainz_artistid"))
    _set_if_present(values, "musicbrainz_composerid", metadata.get("musicbrainz_composerid"))
    _set_if_present(values, "musicbrainz_discid", metadata.get("musicbrainz_discid"))
    _set_if_present(values, "musicbrainz_originalalbumid", metadata.get("musicbrainz_originalalbumid"))
    _set_list_if_present(values, "musicbrainz_originalartistid", metadata.get("musicbrainz_originalartistid"))
    _set_if_present(values, "musicbrainz_recordingid", metadata.get("recording_mbid"))
    _set_if_present(values, "musicbrainz_releasegroupid", metadata.get("release_group_mbid"))
    _set_if_present(values, "musicbrainz_trackid", metadata.get("track_mbid"))
    _set_if_present(values, "musicbrainz_trmid", metadata.get("musicbrainz_trmid"))
    _set_if_present(values, "musicbrainz_workid", metadata.get("musicbrainz_workid"))
    _set_if_present(values, "originaldate", metadata.get("original_date"))
    _set_if_present(values, "originalyear", metadata.get("original_year"))
    _set_if_present(values, "releasecountry", metadata.get("release_country"))
    _set_if_present(values, "releasestatus", metadata.get("release_status"))
    _set_list_if_present(values, "releasetype", metadata.get("releasetype"))
    _set_if_present(values, "script", metadata.get("script"))
    _set_if_present(values, "title", metadata.get("title"))
    _set_if_present(values, "subtitle", metadata.get("subtitle"))
    _set_if_present(values, "totaldiscs", _coerce_tag_string(metadata.get("total_discs")))
    _set_if_present(values, "totaltracks", _coerce_tag_string(metadata.get("total_tracks")))
    _set_if_present(values, "tracknumber", _coerce_tag_string(metadata.get("track_number")))
    _set_if_present(values, "acoustid_fingerprint", metadata.get("acoustid_fingerprint"))
    _set_if_present(values, "acoustid_id", metadata.get("acoustid_id"))
    _set_if_present(values, "albumsort", metadata.get("albumsort"))
    _set_if_present(values, "bpm", _coerce_tag_string(metadata.get("bpm")))
    _set_if_present(values, "copyright", metadata.get("copyright"))
    _set_if_present(values, "license", metadata.get("license"))
    _set_if_present(values, "encodedby", metadata.get("encodedby"))
    _set_if_present(values, "encodersettings", metadata.get("encodersettings"))
    _set_if_present(values, "key", metadata.get("key"))
    _set_if_present(values, "lyrics", metadata.get("lyrics"))
    _set_if_present(values, "originalfilename", metadata.get("originalfilename"))
    _set_if_present(values, "publisher", metadata.get("publisher"))
    _set_if_present(values, "website", metadata.get("website") or metadata.get("url"))
    _set_if_present(values, "originalartist", metadata.get("originalartist"))
    _set_if_present(values, "remixer", metadata.get("remixer"))
    _set_if_present(values, "musicip_fingerprint", metadata.get("musicip_fingerprint"))
    _set_if_present(values, "musicip_puid", metadata.get("musicip_puid"))
    _set_if_present(values, "replaygain_album_gain", metadata.get("replaygain_album_gain"))
    _set_if_present(values, "replaygain_album_peak", metadata.get("replaygain_album_peak"))
    _set_if_present(values, "replaygain_album_range", metadata.get("replaygain_album_range"))
    _set_if_present(
        values,
        "replaygain_reference_loudness",
        metadata.get("replaygain_reference_loudness"),
    )
    _set_if_present(values, "replaygain_track_gain", metadata.get("replaygain_track_gain"))
    _set_if_present(values, "replaygain_track_peak", metadata.get("replaygain_track_peak"))
    _set_if_present(values, "replaygain_track_range", metadata.get("replaygain_track_range"))
    _set_if_present(values, "rating", metadata.get("rating"))

    return values


def _build_vorbis_style_tag_values(metadata: dict[str, Any]) -> dict[str, list[str]]:
    common_values = _build_common_tag_values(metadata)
    vorbis_values: dict[str, list[str]] = {}

    for key, values in common_values.items():
        target_keys = _vorbis_target_keys(key, metadata)
        target_values = _vorbis_values_for_key(key, values)
        if not target_values:
            continue
        for target_key in target_keys:
            vorbis_values[target_key] = list(target_values)

    return vorbis_values


def _set_id3_text_frame(tags: Any, frame_id: str, frame_class: Any, value: Any) -> None:
    tags.delall(frame_id)
    cleaned_value = _clean_string(value)
    if cleaned_value is not None:
        tags.add(frame_class(encoding=3, text=[cleaned_value]))


def _set_id3_txxx(tags: Any, description: str, value: Any) -> None:
    tags.delall("TXXX:" + description)
    values = _ensure_list(value)
    if values:
        tags.add(TXXX(encoding=3, desc=description, text=values))


def _set_id3_ufid(tags: Any, recording_mbid: Any) -> None:
    tags.delall("UFID:http://musicbrainz.org")
    cleaned_value = _clean_string(recording_mbid)
    if cleaned_value is not None:
        tags.add(UFID(owner="http://musicbrainz.org", data=cleaned_value.encode("utf-8")))


def _set_id3_uslt(tags: Any, lyrics: Any) -> None:
    tags.delall("USLT")
    cleaned_lyrics = _clean_string(lyrics)
    if cleaned_lyrics is not None:
        tags.add(USLT(encoding=3, lang="eng", desc="", text=cleaned_lyrics))


def _set_id3_sylt(tags: Any, synced_lyrics: Any) -> None:
    tags.delall("SYLT")
    parsed_entries = _parse_lrc_entries(synced_lyrics)
    if parsed_entries:
        tags.add(
            SYLT(
                encoding=3,
                lang="eng",
                format=2,
                type=1,
                desc="",
                text=parsed_entries,
            )
        )


def _set_mp4_text(tags: Any, key: str, value: Any) -> None:
    cleaned_value = _clean_string(value)
    if cleaned_value is None:
        tags.pop(key, None)
    else:
        tags[key] = [cleaned_value]


def _set_mp4_track_numbers(tags: Any, metadata: dict[str, Any]) -> None:
    track_number = _coerce_int(metadata.get("track_number"))
    total_tracks = _coerce_int(metadata.get("total_tracks"))
    disc_number = _coerce_int(metadata.get("disc_number"))
    total_discs = _coerce_int(metadata.get("total_discs"))

    if track_number is None:
        tags.pop("trkn", None)
    else:
        tags["trkn"] = [(track_number, total_tracks or 0)]

    if disc_number is None:
        tags.pop("disk", None)
    else:
        tags["disk"] = [(disc_number, total_discs or 0)]


def _set_mp4_freeform(tags: Any, name: str, value: Any) -> None:
    key = f"----:com.apple.iTunes:{name}"
    values = _ensure_list(value)
    if not values:
        tags.pop(key, None)
        return

    tags[key] = [MP4FreeForm(item.encode("utf-8")) for item in values]


def _set_id3_wxxx(tags: Any, description: str, value: Any) -> None:
    tags.delall("WXXX:" + description)
    cleaned_value = _clean_string(value)
    if cleaned_value is not None:
        tags.add(WXXX(encoding=3, desc=description, url=cleaned_value))


def _set_asf_values(audio_file: ASF, key: str, value: Any) -> None:
    values = _ensure_list(value)
    if not values:
        audio_file.tags.pop(key, None)
        return

    audio_file.tags[key] = [ASFUnicodeAttribute(item) for item in values]


def _set_if_present(values: dict[str, list[str]], key: str, value: Any) -> None:
    cleaned_value = _clean_string(value)
    if cleaned_value is not None:
        values[key] = [cleaned_value]


def _set_list_if_present(values: dict[str, list[str]], key: str, value: Any) -> None:
    cleaned_values = _ensure_list(value)
    if cleaned_values:
        values[key] = cleaned_values


def _ensure_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [cleaned_value for item in value if (cleaned_value := _clean_string(item)) is not None]
    cleaned_value = _clean_string(value)
    return [cleaned_value] if cleaned_value is not None else []


def _joined_or_single(values: Any, fallback: Any) -> str | None:
    cleaned_values = _ensure_list(values)
    if cleaned_values:
        return "; ".join(cleaned_values)
    return _clean_string(fallback)


def _format_track_number(metadata: dict[str, Any]) -> str | None:
    track_number = _coerce_int(metadata.get("track_number"))
    total_tracks = _coerce_int(metadata.get("total_tracks"))
    if track_number is None:
        return None
    if total_tracks is None:
        return str(track_number)
    return f"{track_number}/{total_tracks}"


def _format_disc_number(metadata: dict[str, Any]) -> str | None:
    disc_number = _coerce_int(metadata.get("disc_number"))
    total_discs = _coerce_int(metadata.get("total_discs"))
    if disc_number is None:
        return None
    if total_discs is None:
        return str(disc_number)
    return f"{disc_number}/{total_discs}"


def _coerce_tag_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean_string(value: Any) -> str | None:
    if value is None:
        return None
    cleaned_value = str(value).strip()
    return cleaned_value or None


def _vorbis_target_keys(key: str, metadata: dict[str, Any]) -> list[str]:
    key_map = {
        "title": ["TITLE"],
        "artist": ["ARTIST"],
        "artists": ["ARTISTS"],
        "artistsort": ["ARTISTSORT"],
        "album": ["ALBUM"],
        "albumsort": ["ALBUMSORT"],
        "albumartist": ["ALBUMARTIST"],
        "albumartistsort": ["ALBUMARTISTSORT"],
        "tracknumber": ["TRACKNUMBER"],
        "totaltracks": ["TRACKTOTAL", "TOTALTRACKS"],
        "discnumber": ["DISCNUMBER"],
        "totaldiscs": ["DISCTOTAL", "TOTALDISCS"],
        "discsubtitle": ["DISCSUBTITLE"],
        "date": ["DATE"],
        "originaldate": ["ORIGINALDATE"],
        "originalyear": ["ORIGINALYEAR"],
        "releasecountry": ["RELEASECOUNTRY"],
        "releasestatus": ["RELEASESTATUS"],
        "releasetype": ["RELEASETYPE"],
        "media": ["MEDIA"],
        "label": ["LABEL"],
        "barcode": ["BARCODE"],
        "catalognumber": ["CATALOGNUMBER"],
        "asin": ["ASIN"],
        "composer": ["COMPOSER"],
        "composersort": ["COMPOSERSORT"],
        "lyricist": ["LYRICIST"],
        "writer": ["WRITER"],
        "arranger": ["ARRANGER"],
        "conductor": ["CONDUCTOR"],
        "performer": ["PERFORMER"],
        "producer": ["PRODUCER"],
        "engineer": ["ENGINEER"],
        "mixer": ["MIXER"],
        "djmixer": ["DJMIXER"],
        "remixer": ["REMIXER"],
        "director": ["DIRECTOR"],
        "work": ["WORK"],
        "movement": ["MOVEMENTNAME"],
        "movementnumber": ["MOVEMENT"],
        "movementtotal": ["MOVEMENTTOTAL"],
        "showmovement": ["SHOWMOVEMENT"],
        "genre": ["GENRE"],
        "grouping": ["GROUPING"],
        "mood": ["MOOD"],
        "bpm": ["BPM"],
        "key": ["KEY"],
        "language": ["LANGUAGE"],
        "comment": ["COMMENT"],
        "subtitle": ["SUBTITLE"],
        "titlesort": ["TITLESORT"],
        "compilation": ["COMPILATION"],
        "copyright": ["COPYRIGHT"],
        "license": ["LICENSE"],
        "website": ["WEBSITE"],
        "lyrics": ["LYRICS"],
        "encodedby": ["ENCODEDBY"],
        "encodersettings": ["ENCODERSETTINGS"],
        "originalfilename": ["ORIGINALFILENAME"],
        "replaygain_album_gain": ["REPLAYGAIN_ALBUM_GAIN"],
        "replaygain_album_peak": ["REPLAYGAIN_ALBUM_PEAK"],
        "replaygain_album_range": ["REPLAYGAIN_ALBUM_RANGE"],
        "replaygain_reference_loudness": ["REPLAYGAIN_REFERENCE_LOUDNESS"],
        "replaygain_track_gain": ["REPLAYGAIN_TRACK_GAIN"],
        "replaygain_track_peak": ["REPLAYGAIN_TRACK_PEAK"],
        "replaygain_track_range": ["REPLAYGAIN_TRACK_RANGE"],
        "musicbrainz_albumartistid": ["MUSICBRAINZ_ALBUMARTISTID"],
        "musicbrainz_albumid": ["MUSICBRAINZ_ALBUMID"],
        "musicbrainz_artistid": ["MUSICBRAINZ_ARTISTID"],
        "musicbrainz_composerid": ["MUSICBRAINZ_COMPOSERID"],
        "musicbrainz_discid": ["MUSICBRAINZ_DISCID"],
        "musicbrainz_originalalbumid": ["MUSICBRAINZ_ORIGINALALBUMID"],
        "musicbrainz_originalartistid": ["MUSICBRAINZ_ORIGINALARTISTID"],
        "musicbrainz_recordingid": ["MUSICBRAINZ_TRACKID"],
        "musicbrainz_releasegroupid": ["MUSICBRAINZ_RELEASEGROUPID"],
        "musicbrainz_trackid": ["MUSICBRAINZ_RELEASETRACKID"],
        "musicbrainz_trmid": ["MUSICBRAINZ_TRMID"],
        "musicbrainz_workid": ["MUSICBRAINZ_WORKID"],
        "isrc": ["ISRC"],
        "acoustid_id": ["ACOUSTID_ID"],
        "acoustid_fingerprint": ["ACOUSTID_FINGERPRINT"],
        "musicip_puid": ["MUSICIP_PUID"],
        "musicip_fingerprint": ["FINGERPRINT"],
    }
    if key == "rating":
        rating_email = _clean_string(metadata.get("rating_email")) or _clean_string(
            metadata.get("rating_user_email")
        )
        return [f"RATING:{rating_email}"] if rating_email else ["RATING"]
    return key_map.get(key, [key.upper()])


def _vorbis_values_for_key(key: str, values: list[str]) -> list[str]:
    if key == "musicip_fingerprint":
        normalized_values: list[str] = []
        for value in values:
            cleaned_value = _clean_string(value)
            if cleaned_value is None:
                continue
            if cleaned_value.startswith("MusicMagic Fingerprint "):
                normalized_values.append(cleaned_value)
            else:
                normalized_values.append(f"MusicMagic Fingerprint {cleaned_value}")
        return normalized_values

    return values


def _parse_lrc_entries(value: Any) -> list[tuple[str, int]]:
    cleaned_value = _clean_string(value)
    if cleaned_value is None:
        return []

    parsed_entries: list[tuple[str, int]] = []
    for raw_line in cleaned_value.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        timestamps, lyric_text = _split_lrc_line(line)
        if not timestamps or lyric_text is None:
            return []
        for timestamp_ms in timestamps:
            parsed_entries.append((lyric_text, timestamp_ms))

    return parsed_entries


def _split_lrc_line(line: str) -> tuple[list[int], str | None]:
    timestamp_matches = list(
        re.finditer(r"\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]", line)
    )
    if not timestamp_matches:
        return [], None

    lyric_text = line[timestamp_matches[-1].end() :].strip()
    timestamps: list[int] = []
    for match in timestamp_matches:
        minutes = int(match.group(1))
        seconds = int(match.group(2))
        fraction = match.group(3) or "0"
        if len(fraction) == 1:
            milliseconds = int(fraction) * 100
        elif len(fraction) == 2:
            milliseconds = int(fraction) * 10
        else:
            milliseconds = int(fraction[:3])
        timestamps.append(((minutes * 60) + seconds) * 1000 + milliseconds)

    return timestamps, lyric_text
