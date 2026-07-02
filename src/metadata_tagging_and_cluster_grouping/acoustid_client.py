from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class AcoustIdCandidate:
    acoustid_id: str
    score: float | None
    recording_mbids: list[str]
    release_mbids: list[str]
    release_group_mbids: list[str]
    result_index: int


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
        chosen_candidate = self.extract_chosen_candidate(result)
        if chosen_candidate is None:
            return []
        return list(chosen_candidate.recording_mbids)

    def extract_release_mbids(self, result: Any) -> list[str]:
        chosen_candidate = self.extract_chosen_candidate(result)
        if chosen_candidate is None:
            return []
        return list(chosen_candidate.release_mbids)

    def extract_release_group_mbids(self, result: Any) -> list[str]:
        chosen_candidate = self.extract_chosen_candidate(result)
        if chosen_candidate is None:
            return []
        return list(chosen_candidate.release_group_mbids)

    def extract_acoustid_id(self, result: Any) -> str | None:
        chosen_candidate = self.extract_chosen_candidate(result)
        if chosen_candidate is None:
            return None
        return chosen_candidate.acoustid_id

    def extract_acoustid_score(self, result: Any) -> float | None:
        chosen_candidate = self.extract_chosen_candidate(result)
        if chosen_candidate is None:
            return None
        return chosen_candidate.score

    def extract_chosen_candidate(self, result: Any) -> AcoustIdCandidate | None:
        candidates = self.extract_candidates(result)
        if not candidates:
            return None

        # Keep low-score AcoustID matches available, but consistently prefer the
        # highest-scoring candidate when binding the chosen ID to its metadata.
        return min(
            candidates,
            key=lambda candidate: (
                -_score_sort_key(candidate.score),
                -int(bool(candidate.recording_mbids)),
                -int(bool(candidate.release_mbids or candidate.release_group_mbids)),
                candidate.result_index,
                candidate.acoustid_id,
            ),
        )

    def extract_candidates(self, result: Any) -> list[AcoustIdCandidate]:
        if not isinstance(result, dict):
            return []

        raw_results = result.get("results")
        if not isinstance(raw_results, list):
            return []

        candidates: list[AcoustIdCandidate] = []
        for result_index, raw_candidate in enumerate(raw_results):
            if not isinstance(raw_candidate, dict):
                continue

            acoustid_id = raw_candidate.get("id")
            if not isinstance(acoustid_id, str) or not acoustid_id.strip():
                continue

            recording_mbids: list[str] = []
            _collect_recording_mbids(raw_candidate, recording_mbids)
            release_mbids: list[str] = []
            _collect_child_ids(raw_candidate, key="releases", collected_ids=release_mbids)
            release_group_mbids: list[str] = []
            _collect_child_ids(
                raw_candidate,
                key="releasegroups",
                collected_ids=release_group_mbids,
            )

            candidates.append(
                AcoustIdCandidate(
                    acoustid_id=acoustid_id.strip(),
                    score=_coerce_score(raw_candidate.get("score")),
                    recording_mbids=_deduplicate_strings(recording_mbids),
                    release_mbids=_deduplicate_strings(release_mbids),
                    release_group_mbids=_deduplicate_strings(release_group_mbids),
                    result_index=result_index,
                )
            )

        return candidates


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

def _coerce_score(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _score_sort_key(score: float | None) -> float:
    return score if score is not None else -1.0
