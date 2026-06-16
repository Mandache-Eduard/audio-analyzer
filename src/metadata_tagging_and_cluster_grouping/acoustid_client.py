from __future__ import annotations

import threading
import time
from typing import Any

import requests

ACOUSTID_LOOKUP_URL = "https://api.acoustid.org/v2/lookup"
ACOUSTID_LOOKUP_META = [
    "recordings",
    "recordingids",
    "releases",
    "releaseids",
    "releasegroups",
    "releasegroupids",
    "compress",
]
ACOUSTID_LOOKUP_META_VALUE = " ".join(ACOUSTID_LOOKUP_META)
ACOUSTID_REQUEST_TIMEOUT_SECONDS = 30


class AcoustIdClient:
    def __init__(self, *, api_key: str) -> None:
        if not api_key.strip():
            raise ValueError("Missing required AcoustID API key.")

        self._api_key = api_key
        self._session = requests.Session()
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

        return self._lookup(
            {
                "fingerprint": fingerprint,
                "duration": int(round(duration_seconds)),
            },
            error_context="AcoustID lookup by fingerprint failed",
        )

    def lookup_by_track_id(self, acoustid_id: str) -> dict[str, Any]:
        if not acoustid_id.strip():
            raise ValueError("AcoustID track ID must not be empty.")

        self._rate_limiter.wait()
        return self._lookup(
            {
                "trackid": acoustid_id,
            },
            error_context="AcoustID lookup by track ID failed",
        )

    def close(self) -> None:
        self._session.close()

    def _lookup(self, params: dict[str, Any], *, error_context: str) -> dict[str, Any]:
        request_params = {
            "client": self._api_key,
            "meta": ACOUSTID_LOOKUP_META_VALUE,
            "format": "json",
            **params,
        }
        try:
            response = self._session.post(
                ACOUSTID_LOOKUP_URL,
                data=request_params,
                timeout=ACOUSTID_REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except requests.Timeout as exc:
            raise RuntimeError(f"{error_context}: request timed out.") from exc
        except requests.ConnectionError as exc:
            raise RuntimeError(f"{error_context}: network connection failed.") from exc
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else "unknown"
            raise RuntimeError(f"{error_context}: HTTP {status_code}.") from exc
        except requests.RequestException as exc:
            raise RuntimeError(f"{error_context}: {exc}.") from exc

        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError(f"{error_context}: returned invalid JSON.") from exc

    def extract_recording_mbids(self, result: Any) -> list[str]:
        collected_mbids: list[str] = []
        _collect_recording_mbids(result, collected_mbids)
        return _deduplicate_strings(collected_mbids)

    def extract_release_mbids(self, result: Any) -> list[str]:
        collected_mbids: list[str] = []
        _collect_child_ids(result, key="releases", collected_ids=collected_mbids)
        return _deduplicate_strings(collected_mbids)

    def extract_release_group_mbids(self, result: Any) -> list[str]:
        collected_mbids: list[str] = []
        _collect_child_ids(result, key="releasegroups", collected_ids=collected_mbids)
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

def _collect_child_ids(value: Any, *, key: str, collected_ids: list[str]) -> None:
    if value is None:
        return

    if isinstance(value, dict):
        child_values = value.get(key)
        if isinstance(child_values, list):
            for child_value in child_values:
                if not isinstance(child_value, dict):
                    continue
                child_id = child_value.get("id")
                if isinstance(child_id, str) and child_id.strip():
                    collected_ids.append(child_id.strip())

        for nested_value in value.values():
            if isinstance(nested_value, (dict, list, tuple)):
                _collect_child_ids(nested_value, key=key, collected_ids=collected_ids)
        return

    if isinstance(value, (list, tuple)):
        for item in value:
            _collect_child_ids(item, key=key, collected_ids=collected_ids)


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
