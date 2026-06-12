from __future__ import annotations

import acoustid as pyacoustid
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class AcoustIdClient:
    def __init__(self, *, api_key: str) -> None:
        if not api_key.strip():
            raise ValueError("Missing required AcoustID API key.")

        self._api_key = api_key
        self._pyacoustid = pyacoustid
        self._rate_limiter = _SimpleRateLimiter(min_interval_seconds=1.0 / 3.0)

    def lookup_by_fingerprint(
        self,
        *,
        fingerprint: str,
        duration_seconds: float,
    ) -> dict[str, Any]:
        if not fingerprint.strip():
            raise ValueError("Fingerprint must not be empty.")

        self._rate_limiter.wait()

        try:
            return self._pyacoustid.lookup(
                self._api_key,
                fingerprint,
                int(round(duration_seconds)),
                meta=["recordings", "recordingids", "releasegroups", "compress"],
            )
        except Exception as exc:
            raise RuntimeError("AcoustID lookup by fingerprint failed.") from exc

    def lookup_by_track_id(self, acoustid_id: str) -> dict[str, Any]:
        if not acoustid_id.strip():
            raise ValueError("AcoustID track ID must not be empty.")

        self._rate_limiter.wait()
        params = urllib.parse.urlencode(
            {
                "client": self._api_key,
                "trackid": acoustid_id,
                "meta": "recordingids+recordings+releasegroups+compress",
                "format": "json",
            }
        )
        url = f"https://api.acoustid.org/v2/lookup?{params}"

        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"AcoustID lookup by track ID failed with HTTP {exc.code}."
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError("AcoustID lookup by track ID failed due to a network error.") from exc

        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RuntimeError("AcoustID lookup by track ID returned invalid JSON.") from exc

    def extract_recording_mbids(self, result: Any) -> list[str]:
        collected_mbids: list[str] = []
        _collect_recording_mbids(result, collected_mbids)
        return _deduplicate_strings(collected_mbids)

    def extract_acoustid_id(self, result: Any) -> str | None:
        return _extract_acoustid_id(result)


class _SimpleRateLimiter:
    def __init__(self, *, min_interval_seconds: float) -> None:
        self._min_interval_seconds = min_interval_seconds
        self._lock = threading.Lock()
        self._last_request_started_at = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_started_at
            if elapsed < self._min_interval_seconds:
                time.sleep(self._min_interval_seconds - elapsed)
            self._last_request_started_at = time.monotonic()


def _collect_recording_mbids(value: Any, collected_mbids: list[str]) -> None:
    if value is None:
        return

    if isinstance(value, dict):
        recordings_value = value.get("recordings")
        if isinstance(recordings_value, list):
            for recording in recordings_value:
                if isinstance(recording, dict):
                    recording_id = recording.get("id")
                    if isinstance(recording_id, str) and recording_id.strip():
                        collected_mbids.append(recording_id.strip())

        for nested_value in value.values():
            if isinstance(nested_value, (dict, list, tuple)):
                _collect_recording_mbids(nested_value, collected_mbids)
        return

    if isinstance(value, (list, tuple)):
        for item in value:
            _collect_recording_mbids(item, collected_mbids)


def _deduplicate_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered_values: list[str] = []

    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered_values.append(value)

    return ordered_values


def _extract_acoustid_id(result: Any) -> str | None:
    if isinstance(result, dict):
        for key in ("acoustid_id", "acoustid", "id", "track_id", "trackid"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        for nested_value in result.values():
            if isinstance(nested_value, (dict, list, tuple)):
                extracted = _extract_acoustid_id(nested_value)
                if extracted:
                    return extracted

    if isinstance(result, (list, tuple)):
        for item in result:
            extracted = _extract_acoustid_id(item)
            if extracted:
                return extracted

    return None
