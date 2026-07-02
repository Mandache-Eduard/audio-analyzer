from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from caching_and_duplicate_detection.cache_models import FileIdentity
from caching_and_duplicate_detection.cache_paths import (
    ensure_cache_directory,
    get_cache_schema_path,
    normalize_path,
    resolve_cache_db_path,
)

LOGGER = logging.getLogger(__name__)


class AudioCache:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = resolve_cache_db_path(db_path)
        self._schema_path = get_cache_schema_path()
        self._initialized = False
        self._disabled = False
        self._warning_emitted = False

    @property
    def is_enabled(self) -> bool:
        return not self._disabled

    def initialize(self) -> None:
        if self._disabled or self._initialized:
            return

        try:
            ensure_cache_directory(self.db_path.parent)
            schema_sql = self._schema_path.read_text(encoding="utf-8")
            with self._connect() as connection:
                connection.executescript(schema_sql)
        except (OSError, sqlite3.Error) as exc:
            self._disable_with_warning(f"cache initialization failed for {self.db_path}: {exc}")
            return

        self._initialized = True

    def upsert_file(self, path: Path, audio_info: dict[str, Any] | None = None) -> int | None:
        self.initialize()
        identity = _build_file_identity(path)
        info = audio_info or {}
        updated_at = _utc_now_text()

        try:
            with self._connect() as connection:
                existing_row = connection.execute(
                    """
                    SELECT file_id, size_bytes, mtime_ns, extension, duration_seconds, codec,
                           bitrate_bps, sample_rate_hz, channels, bits_per_sample, content_hash
                    FROM files
                    WHERE normalized_path = ?
                    """,
                    (identity.normalized_path,),
                ).fetchone()

                if existing_row is None:
                    cursor = connection.execute(
                        """
                        INSERT INTO files(
                            path,
                            normalized_path,
                            size_bytes,
                            mtime_ns,
                            content_hash,
                            quick_key,
                            extension,
                            duration_seconds,
                            codec,
                            bitrate_bps,
                            sample_rate_hz,
                            channels,
                            bits_per_sample,
                            updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(identity.path),
                            identity.normalized_path,
                            identity.size_bytes,
                            identity.mtime_ns,
                            None,
                            identity.quick_key,
                            _string_or_default(info.get("extension"), identity.path.suffix.lower()),
                            _coerce_float(info.get("duration_seconds")),
                            _string_or_none(info.get("codec")),
                            _coerce_int(info.get("bitrate_bps")),
                            _coerce_int(info.get("sample_rate_hz")),
                            _coerce_int(info.get("channels")),
                            _coerce_int(info.get("bits_per_sample")),
                            updated_at,
                        ),
                    )
                    return int(cursor.lastrowid)

                file_id = int(existing_row["file_id"])
                identity_changed = (
                    int(existing_row["size_bytes"]) != identity.size_bytes
                    or int(existing_row["mtime_ns"]) != identity.mtime_ns
                )

                extension = _choose_audio_value(
                    info.get("extension"),
                    existing_row["extension"],
                    default=identity.path.suffix.lower(),
                    keep_existing=not identity_changed,
                )
                duration_seconds = _choose_audio_value(
                    _coerce_float(info.get("duration_seconds")),
                    _coerce_float(existing_row["duration_seconds"]),
                    keep_existing=not identity_changed,
                )
                codec = _choose_audio_value(
                    _string_or_none(info.get("codec")),
                    _string_or_none(existing_row["codec"]),
                    keep_existing=not identity_changed,
                )
                bitrate_bps = _choose_audio_value(
                    _coerce_int(info.get("bitrate_bps")),
                    _coerce_int(existing_row["bitrate_bps"]),
                    keep_existing=not identity_changed,
                )
                sample_rate_hz = _choose_audio_value(
                    _coerce_int(info.get("sample_rate_hz")),
                    _coerce_int(existing_row["sample_rate_hz"]),
                    keep_existing=not identity_changed,
                )
                channels = _choose_audio_value(
                    _coerce_int(info.get("channels")),
                    _coerce_int(existing_row["channels"]),
                    keep_existing=not identity_changed,
                )
                bits_per_sample = _choose_audio_value(
                    _coerce_int(info.get("bits_per_sample")),
                    _coerce_int(existing_row["bits_per_sample"]),
                    keep_existing=not identity_changed,
                )
                content_hash = (
                    _string_or_none(existing_row["content_hash"]) if not identity_changed else None
                )

                connection.execute(
                    """
                    UPDATE files
                    SET path = ?,
                        size_bytes = ?,
                        mtime_ns = ?,
                        content_hash = ?,
                        quick_key = ?,
                        extension = ?,
                        duration_seconds = ?,
                        codec = ?,
                        bitrate_bps = ?,
                        sample_rate_hz = ?,
                        channels = ?,
                        bits_per_sample = ?,
                        updated_at = ?
                    WHERE file_id = ?
                    """,
                    (
                        str(identity.path),
                        identity.size_bytes,
                        identity.mtime_ns,
                        content_hash,
                        identity.quick_key,
                        extension,
                        duration_seconds,
                        codec,
                        bitrate_bps,
                        sample_rate_hz,
                        channels,
                        bits_per_sample,
                        updated_at,
                        file_id,
                    ),
                )

                if identity_changed:
                    connection.execute("DELETE FROM fingerprints WHERE file_id = ?", (file_id,))
                    connection.execute("DELETE FROM analysis_results WHERE file_id = ?", (file_id,))
                    connection.execute(
                        "DELETE FROM metadata_resolution WHERE file_id = ?",
                        (file_id,),
                    )

                return file_id
        except sqlite3.Error as exc:
            self._disable_with_warning(f"cache write failed for {self.db_path}: {exc}")
            return None

    def get_valid_file_id(self, path: Path) -> int | None:
        self.initialize()
        identity = _build_file_identity(path)

        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT file_id, size_bytes, mtime_ns
                    FROM files
                    WHERE normalized_path = ?
                    """,
                    (identity.normalized_path,),
                ).fetchone()
        except sqlite3.Error as exc:
            self._disable_with_warning(f"cache read failed for {self.db_path}: {exc}")
            return None

        if row is None:
            return None

        if int(row["size_bytes"]) != identity.size_bytes or int(row["mtime_ns"]) != identity.mtime_ns:
            return None

        return int(row["file_id"])

    def get_content_hash(self, file_id: int) -> str | None:
        self.initialize()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT content_hash FROM files WHERE file_id = ?",
                    (file_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            self._disable_with_warning(f"cache read failed for {self.db_path}: {exc}")
            return None

        if row is None:
            return None
        return _string_or_none(row["content_hash"])

    def get_cached_fingerprint(
        self,
        file_id: int,
        fpcalc_version: str,
        fingerprint_settings: dict[str, Any],
    ) -> dict[str, Any] | None:
        self.initialize()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT chromaprint, duration_seconds, acoustid_id, acoustid_score,
                           lookup_json, fpcalc_version, fingerprint_settings
                    FROM fingerprints
                    WHERE file_id = ?
                    """,
                    (file_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            self._disable_with_warning(f"cache read failed for {self.db_path}: {exc}")
            return None

        if row is None:
            return None
        if _string_or_default(row["fpcalc_version"], "") != fpcalc_version:
            return None

        cached_settings = _load_json_dict(row["fingerprint_settings"])
        if cached_settings is None or cached_settings != fingerprint_settings:
            return None

        lookup_json = _load_json_dict(row["lookup_json"], allow_none=True)
        if row["lookup_json"] is not None and lookup_json is None:
            return None

        chromaprint = _string_or_none(row["chromaprint"])
        duration_seconds = _coerce_float(row["duration_seconds"])
        if chromaprint is None or duration_seconds is None:
            return None

        return {
            "chromaprint": chromaprint,
            "duration_seconds": duration_seconds,
            "acoustid_id": _string_or_none(row["acoustid_id"]),
            "acoustid_score": _coerce_float(row["acoustid_score"]),
            "lookup_json": lookup_json,
            "fpcalc_version": fpcalc_version,
            "fingerprint_settings": cached_settings,
        }

    def save_fingerprint(
        self,
        file_id: int,
        chromaprint: str,
        duration_seconds: float,
        acoustid_id: str | None,
        acoustid_score: float | None,
        lookup_json: dict[str, Any] | None,
        fpcalc_version: str,
        fingerprint_settings: dict[str, Any],
    ) -> None:
        self.initialize()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO fingerprints(
                        file_id,
                        chromaprint,
                        duration_seconds,
                        acoustid_id,
                        acoustid_score,
                        lookup_json,
                        fpcalc_version,
                        fingerprint_settings,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(file_id) DO UPDATE SET
                        chromaprint = excluded.chromaprint,
                        duration_seconds = excluded.duration_seconds,
                        acoustid_id = excluded.acoustid_id,
                        acoustid_score = excluded.acoustid_score,
                        lookup_json = excluded.lookup_json,
                        fpcalc_version = excluded.fpcalc_version,
                        fingerprint_settings = excluded.fingerprint_settings,
                        updated_at = excluded.updated_at
                    """,
                    (
                        file_id,
                        chromaprint,
                        float(duration_seconds),
                        acoustid_id,
                        acoustid_score,
                        _dump_json(lookup_json),
                        fpcalc_version,
                        _dump_json(fingerprint_settings),
                        _utc_now_text(),
                    ),
                )
        except sqlite3.Error as exc:
            self._disable_with_warning(f"cache write failed for {self.db_path}: {exc}")

    def save_fingerprint_lookup(
        self,
        file_id: int,
        acoustid_id: str | None,
        acoustid_score: float | None,
        lookup_json: dict[str, Any] | None,
    ) -> None:
        self.initialize()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE fingerprints
                    SET acoustid_id = ?,
                        acoustid_score = ?,
                        lookup_json = ?,
                        updated_at = ?
                    WHERE file_id = ?
                    """,
                    (
                        acoustid_id,
                        acoustid_score,
                        _dump_json(lookup_json),
                        _utc_now_text(),
                        file_id,
                    ),
                )
        except sqlite3.Error as exc:
            self._disable_with_warning(f"cache write failed for {self.db_path}: {exc}")

    def get_cached_analysis(
        self,
        file_id: int,
        analyzer_version: str,
    ) -> dict[str, Any] | None:
        self.initialize()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT analyzer_version, status, confidence, samplerate_hz, num_samples,
                           num_total_frames, num_non_silent_frames, effective_cutoff_hz,
                           per_cutoff_active_fraction
                    FROM analysis_results
                    WHERE file_id = ?
                    """,
                    (file_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            self._disable_with_warning(f"cache read failed for {self.db_path}: {exc}")
            return None

        if row is None or _string_or_default(row["analyzer_version"], "") != analyzer_version:
            return None

        cached_fractions = _load_json_dict(row["per_cutoff_active_fraction"], allow_none=True)
        if row["per_cutoff_active_fraction"] is not None and cached_fractions is None:
            return None

        return {
            "status": _string_or_default(row["status"], "ERROR"),
            "confidence": _coerce_float(row["confidence"]),
            "samplerate_hz": _coerce_int(row["samplerate_hz"]),
            "num_samples": _coerce_int(row["num_samples"]),
            "num_total_frames": _coerce_int(row["num_total_frames"]),
            "num_non-silent_frames": _coerce_int(row["num_non_silent_frames"]),
            "effective_cutoff_hz": _coerce_float(row["effective_cutoff_hz"]),
            "per_cutoff_active_fraction": _fractions_dict_to_csv(cached_fractions),
        }

    def save_analysis(
        self,
        file_id: int,
        analyzer_version: str,
        result: dict[str, Any],
    ) -> None:
        self.initialize()
        fractions_dict = _fractions_value_to_dict(result.get("per_cutoff_active_fraction"))
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO analysis_results(
                        file_id,
                        analyzer_version,
                        status,
                        confidence,
                        samplerate_hz,
                        num_samples,
                        num_total_frames,
                        num_non_silent_frames,
                        effective_cutoff_hz,
                        per_cutoff_active_fraction,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(file_id) DO UPDATE SET
                        analyzer_version = excluded.analyzer_version,
                        status = excluded.status,
                        confidence = excluded.confidence,
                        samplerate_hz = excluded.samplerate_hz,
                        num_samples = excluded.num_samples,
                        num_total_frames = excluded.num_total_frames,
                        num_non_silent_frames = excluded.num_non_silent_frames,
                        effective_cutoff_hz = excluded.effective_cutoff_hz,
                        per_cutoff_active_fraction = excluded.per_cutoff_active_fraction,
                        updated_at = excluded.updated_at
                    """,
                    (
                        file_id,
                        analyzer_version,
                        _string_or_default(result.get("status"), "ERROR"),
                        _coerce_float(result.get("confidence")),
                        _coerce_int(result.get("samplerate_hz")),
                        _coerce_int(result.get("num_samples")),
                        _coerce_int(result.get("num_total_frames")),
                        _coerce_int(result.get("num_non-silent_frames")),
                        _coerce_float(result.get("effective_cutoff_hz")),
                        _dump_json(fractions_dict),
                        _utc_now_text(),
                    ),
                )
        except sqlite3.Error as exc:
            self._disable_with_warning(f"cache write failed for {self.db_path}: {exc}")

    def get_cached_metadata_resolution(
        self,
        file_id: int,
        resolver_version: str,
    ) -> dict[str, Any] | None:
        self.initialize()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT resolver_version, result_json
                    FROM metadata_resolution
                    WHERE file_id = ?
                    """,
                    (file_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            self._disable_with_warning(f"cache read failed for {self.db_path}: {exc}")
            return None

        if row is None or _string_or_default(row["resolver_version"], "") != resolver_version:
            return None

        cached_result = _load_json_dict(row["result_json"])
        if cached_result is None:
            return None

        normalized_result = _normalize_cached_metadata_resolution(cached_result)
        if normalized_result is None:
            return None
        return normalized_result

    def save_metadata_resolution(
        self,
        file_id: int,
        resolver_version: str,
        result: dict[str, Any],
    ) -> None:
        self.initialize()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO metadata_resolution(
                        file_id,
                        resolver_version,
                        source,
                        status,
                        recording_mbid,
                        acoustid_id,
                        result_json,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(file_id) DO UPDATE SET
                        resolver_version = excluded.resolver_version,
                        source = excluded.source,
                        status = excluded.status,
                        recording_mbid = excluded.recording_mbid,
                        acoustid_id = excluded.acoustid_id,
                        result_json = excluded.result_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        file_id,
                        resolver_version,
                        _string_or_none(result.get("source")),
                        _string_or_none(result.get("status")),
                        _string_or_none(result.get("recording_mbid")),
                        _string_or_none(result.get("acoustid_id")),
                        _dump_json(result),
                        _utc_now_text(),
                    ),
                )
        except sqlite3.Error as exc:
            self._disable_with_warning(f"cache write failed for {self.db_path}: {exc}")

    def save_content_hash(
        self,
        file_id: int,
        content_hash: str,
    ) -> None:
        self.initialize()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE files
                    SET content_hash = ?, updated_at = ?
                    WHERE file_id = ?
                    """,
                    (content_hash, _utc_now_text(), file_id),
                )
        except sqlite3.Error as exc:
            self._disable_with_warning(f"cache write failed for {self.db_path}: {exc}")

    def _connect(self) -> sqlite3.Connection:
        if self._disabled:
            raise sqlite3.OperationalError("cache is disabled")

        connection = sqlite3.connect(self.db_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON;")
        connection.execute("PRAGMA journal_mode = WAL;")
        connection.execute("PRAGMA busy_timeout = 5000;")
        return connection

    def _disable_with_warning(self, message: str) -> None:
        self._disabled = True
        if self._warning_emitted:
            return
        LOGGER.debug("Persistent cache disabled: %s", message)
        print(f"Warning: {message}", file=sys.stderr)
        self._warning_emitted = True


def _build_file_identity(path: Path) -> FileIdentity:
    resolved_path = path.resolve()
    stat_result = resolved_path.stat()
    normalized = normalize_path(resolved_path)
    quick_key = hashlib.sha256(
        f"{normalized}|{stat_result.st_size}|{stat_result.st_mtime_ns}".encode("utf-8")
    ).hexdigest()
    return FileIdentity(
        path=resolved_path,
        normalized_path=normalized,
        size_bytes=int(stat_result.st_size),
        mtime_ns=int(stat_result.st_mtime_ns),
        quick_key=quick_key,
    )


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dump_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)


def _load_json_dict(raw_value: Any, *, allow_none: bool = False) -> dict[str, Any] | None:
    if raw_value is None:
        return None

    if not isinstance(raw_value, str):
        return None

    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError:
        return None

    if parsed is None and allow_none:
        return None

    if not isinstance(parsed, dict):
        return None

    return parsed


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    cleaned_value = str(value).strip()
    return cleaned_value or None


def _string_or_default(value: Any, default: str) -> str:
    return _string_or_none(value) or default


def _choose_audio_value(
    new_value: Any,
    existing_value: Any,
    *,
    default: Any = None,
    keep_existing: bool = True,
) -> Any:
    if new_value is not None:
        return new_value
    if keep_existing:
        return existing_value
    return default


def _fractions_value_to_dict(value: Any) -> dict[str, float]:
    if value is None:
        return {}

    if isinstance(value, dict):
        fractions: dict[str, float] = {}
        for key, raw_fraction in value.items():
            coerced_fraction = _coerce_float(raw_fraction)
            if coerced_fraction is None:
                continue
            fractions[str(key)] = coerced_fraction
        return fractions

    if not isinstance(value, str):
        return {}

    fractions = {}
    for pair in value.split(";"):
        if not pair.strip() or "=" not in pair:
            continue
        raw_key, raw_value = pair.split("=", 1)
        coerced_fraction = _coerce_float(raw_value)
        if coerced_fraction is None:
            continue
        fractions[raw_key.strip()] = coerced_fraction
    return fractions


def _fractions_dict_to_csv(value: dict[str, Any] | None) -> str:
    if not value:
        return ""

    sortable_pairs: list[tuple[float, str, float]] = []
    for key, raw_fraction in value.items():
        numeric_key = _coerce_float(key)
        numeric_fraction = _coerce_float(raw_fraction)
        if numeric_key is None or numeric_fraction is None:
            continue
        sortable_pairs.append((numeric_key, str(int(numeric_key)), numeric_fraction))

    return ";".join(
        f"{formatted_key}={fraction:.4f}"
        for _, formatted_key, fraction in sorted(sortable_pairs, key=lambda item: item[0])
    )


def _normalize_cached_metadata_resolution(value: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    status = _string_or_none(value.get("status"))
    source = _string_or_none(value.get("source"))
    if status is None or source is None:
        return None

    normalized_value = dict(value)
    normalized_value["status"] = status
    normalized_value["source"] = source
    normalized_value["recording_mbid"] = _string_or_none(normalized_value.get("recording_mbid"))
    normalized_value["isrc"] = _string_or_none(normalized_value.get("isrc"))
    normalized_value["acoustid_id"] = _string_or_none(normalized_value.get("acoustid_id"))
    normalized_value["error"] = _string_or_none(normalized_value.get("error"))
    normalized_value["acoustid_score"] = _coerce_float(normalized_value.get("acoustid_score"))
    normalized_value.setdefault("track_number", None)
    normalized_value.setdefault("disc_number", None)
    normalized_value.setdefault("result", None)
    normalized_value.setdefault("raw_result", normalized_value.get("result"))

    for list_key in (
        "candidate_recording_mbids",
        "candidate_release_mbids",
        "candidate_release_group_mbids",
    ):
        normalized_list = _normalize_string_list(normalized_value.get(list_key, []))
        if normalized_list is None:
            return None
        normalized_value[list_key] = normalized_list

    return normalized_value


def _normalize_string_list(value: Any) -> list[str] | None:
    if value is None:
        return []
    if not isinstance(value, list):
        return None

    normalized_values: list[str] = []
    for item in value:
        normalized_item = _string_or_none(item)
        if normalized_item is not None:
            normalized_values.append(normalized_item)
    return normalized_values
