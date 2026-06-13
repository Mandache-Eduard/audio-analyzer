from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from mutagen import File as MutagenFile
from tqdm import tqdm

from metadata_enrichment_and_file_grouping.lyric_tagger import (
    LYRICS_MODE_SYNCED,
    LYRICS_MODE_UNSYNCED,
    LyricsResult,
    fetch_from_lrclib,
)
from metadata_enrichment_and_file_grouping.scanner import (
    ScannedAudioFile,
    is_supported_audio_file,
    scan_audio_files,
)


@dataclass(slots=True)
class RequestTrace:
    method: str
    url: str
    endpoint: str
    params: dict[str, Any]
    elapsed_seconds: float
    status_code: int | None
    exception_type: str | None
    exception_message: str | None


class TimedSession(requests.Session):
    def __init__(self) -> None:
        super().__init__()
        self.request_traces: list[RequestTrace] = []

    def reset_traces(self) -> None:
        self.request_traces.clear()

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        params = kwargs.get("params")
        normalized_params = dict(params) if isinstance(params, dict) else {}
        started_at = time.perf_counter()
        response: requests.Response | None = None
        exception: Exception | None = None

        try:
            response = super().get(url, **kwargs)
            return response
        except Exception as exc:
            exception = exc
            raise
        finally:
            self.request_traces.append(
                RequestTrace(
                    method="GET",
                    url=url,
                    endpoint=urlparse(url).path,
                    params=normalized_params,
                    elapsed_seconds=time.perf_counter() - started_at,
                    status_code=response.status_code if response is not None else None,
                    exception_type=exception.__class__.__name__ if exception is not None else None,
                    exception_message=str(exception) if exception is not None else None,
                )
            )


def main() -> None:
    parser = build_argument_parser()
    args = parser.parse_args()

    input_path = Path(args.input_path).expanduser()
    scanned_files = resolve_scanned_files(input_path)
    if args.limit is not None:
        scanned_files = scanned_files[: args.limit]

    print(f"Scanned files: {len(scanned_files)}")
    print(f"Lyrics mode: {args.lyrics_mode}")
    print(f"Request timeout: {args.request_timeout_seconds:.1f}s")
    print("")

    session = TimedSession()
    try:
        track_reports: list[dict[str, Any]] = []
        for index, scanned_file in enumerate(
            tqdm(scanned_files, desc="Lyrics timing debug", unit="file"),
            start=1,
        ):
            metadata = read_lyrics_metadata(scanned_file.original_path)
            report = inspect_track_lookup(
                index=index,
                total=len(scanned_files),
                file_path=scanned_file.original_path,
                metadata=metadata,
                lyrics_mode=args.lyrics_mode,
                request_timeout_seconds=args.request_timeout_seconds,
                session=session,
            )
            track_reports.append(report)
            print_track_report(report)

        print_summary(track_reports)
    finally:
        session.close()


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Debug LRCLIB lyric lookup timing for tagged audio files without modifying files. "
            "The script reuses the production LRCLIB lookup code and logs each request attempt."
        )
    )
    parser.add_argument("input_path", help="Audio file or directory to inspect.")
    parser.add_argument(
        "--lyrics-mode",
        choices=[LYRICS_MODE_UNSYNCED, LYRICS_MODE_SYNCED],
        default=LYRICS_MODE_UNSYNCED,
        help="Controls whether LRCLIB prefers unsynced or synced lyrics during selection.",
    )
    parser.add_argument(
        "--timeout",
        dest="request_timeout_seconds",
        type=float,
        default=20.0,
        help="Per-request read timeout passed into the LRCLIB lookup path.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional maximum number of audio files to inspect.",
    )
    return parser


def resolve_scanned_files(input_path: Path) -> list[ScannedAudioFile]:
    if not input_path.exists():
        raise FileNotFoundError(f"Input path does not exist: {input_path}")

    if input_path.is_file():
        if not is_supported_audio_file(input_path):
            raise ValueError(f"Unsupported audio file: {input_path}")

        stat_result = input_path.stat()
        return [
            ScannedAudioFile(
                original_path=input_path.resolve(),
                extension=input_path.suffix.lower(),
                file_size=stat_result.st_size,
                duration_seconds=None,
            )
        ]

    return scan_audio_files(input_path)


def inspect_track_lookup(
    *,
    index: int,
    total: int,
    file_path: Path,
    metadata: dict[str, Any],
    lyrics_mode: str,
    request_timeout_seconds: float,
    session: TimedSession,
) -> dict[str, Any]:
    session.reset_traces()
    log_lines: list[str] = []
    started_at = time.perf_counter()
    result = fetch_from_lrclib(
        metadata,
        prefer_synced=lyrics_mode == LYRICS_MODE_SYNCED,
        request_timeout_seconds=request_timeout_seconds,
        session=session,
        log_func=log_lines.append,
    )
    total_elapsed_seconds = time.perf_counter() - started_at
    traces = list(session.request_traces)

    return {
        "index": index,
        "total": total,
        "file_path": str(file_path),
        "metadata": metadata,
        "result": result,
        "log_lines": log_lines,
        "traces": traces,
        "total_elapsed_seconds": total_elapsed_seconds,
        "network_elapsed_seconds": sum(trace.elapsed_seconds for trace in traces),
        "diagnosis": diagnose_slow_lookup(result, traces, total_elapsed_seconds),
    }


def read_lyrics_metadata(file_path: Path) -> dict[str, Any]:
    audio_file = MutagenFile(file_path, easy=True)
    if audio_file is None:
        raise RuntimeError(f"Unable to read audio metadata from {file_path}")

    info = getattr(audio_file, "info", None)
    tags = getattr(audio_file, "tags", None) or {}

    title = _pick_first_value(tags, ["title"])
    artist_values = _pick_values(tags, ["artist"])
    album = _pick_first_value(tags, ["album"])

    metadata: dict[str, Any] = {
        "title": title,
        "artist": artist_values[0] if artist_values else None,
        "artists": artist_values or None,
        "album": album,
        "duration_seconds": _coerce_float(getattr(info, "length", None)),
        "original_path": str(file_path),
    }
    return metadata


def print_track_report(report: dict[str, Any]) -> None:
    metadata = report["metadata"]
    result: LyricsResult = report["result"]
    traces: list[RequestTrace] = report["traces"]
    log_lines: list[str] = report["log_lines"]

    print(f"[{report['index']}/{report['total']}] {Path(report['file_path']).name}")
    print(f"  path: {report['file_path']}")
    print(f"  title: {metadata.get('title') or '-'}")
    print(f"  artist: {_format_artists(metadata.get('artists'))}")
    print(f"  album: {metadata.get('album') or '-'}")
    print(f"  duration_seconds: {_format_float(metadata.get('duration_seconds'))}")
    print(
        "  result: status={} lyrics_type={} source={} confidence={} error={}".format(
            result.status,
            result.lyrics_type or "-",
            result.source or "-",
            _format_float(result.confidence),
            result.error or "-",
        )
    )
    print(
        "  timing: total={:.2f}s network={:.2f}s local_or_backoff={:.2f}s requests={}".format(
            report["total_elapsed_seconds"],
            report["network_elapsed_seconds"],
            max(0.0, report["total_elapsed_seconds"] - report["network_elapsed_seconds"]),
            len(traces),
        )
    )
    print(f"  diagnosis: {report['diagnosis']}")

    if log_lines:
        print("  lyric logs:")
        for line in log_lines:
            print(f"    - {line}")

    if traces:
        print("  request trace:")
        for trace in traces:
            status_value = trace.status_code if trace.status_code is not None else "-"
            exception_suffix = ""
            if trace.exception_type is not None:
                exception_suffix = f" exception={trace.exception_type}: {trace.exception_message}"
            print(
                "    - {} {} status={} elapsed={:.2f}s params={}{}".format(
                    trace.method,
                    trace.endpoint,
                    status_value,
                    trace.elapsed_seconds,
                    _format_trace_params(trace.params),
                    exception_suffix,
                )
            )
    else:
        print("  request trace: none")

    print("")


def print_summary(track_reports: list[dict[str, Any]]) -> None:
    if not track_reports:
        print("No files inspected.")
        return

    total_elapsed_seconds = sum(
        report["total_elapsed_seconds"] for report in track_reports
    )
    network_elapsed_seconds = sum(
        report["network_elapsed_seconds"] for report in track_reports
    )
    status_counts: dict[str, int] = {}
    slow_reports = sorted(
        track_reports,
        key=lambda report: report["total_elapsed_seconds"],
        reverse=True,
    )[:5]

    for report in track_reports:
        result: LyricsResult = report["result"]
        status_counts[result.status] = status_counts.get(result.status, 0) + 1

    print("Summary:")
    print(f"  tracks inspected: {len(track_reports)}")
    print(f"  total elapsed: {total_elapsed_seconds:.2f}s")
    print(f"  network elapsed: {network_elapsed_seconds:.2f}s")
    print(
        "  local_or_backoff elapsed: {:.2f}s".format(
            max(0.0, total_elapsed_seconds - network_elapsed_seconds)
        )
    )
    print("  result counts:")
    for status, count in sorted(status_counts.items()):
        print(f"    {status}: {count}")

    print("  slowest tracks:")
    for report in slow_reports:
        metadata = report["metadata"]
        print(
            "    {:.2f}s | {} | {} | {}".format(
                report["total_elapsed_seconds"],
                Path(report["file_path"]).name,
                metadata.get("title") or "-",
                report["diagnosis"],
            )
        )


def diagnose_slow_lookup(
    result: LyricsResult,
    traces: list[RequestTrace],
    total_elapsed_seconds: float,
) -> str:
    if not traces:
        return "no LRCLIB request was made"

    if any(trace.exception_type in {"ReadTimeout", "ConnectTimeout", "Timeout"} for trace in traces):
        return "slow because at least one LRCLIB request timed out and triggered retries"

    if any(trace.status_code in {429, 500, 502, 503, 504} for trace in traces):
        return "slow because LRCLIB returned retryable HTTP responses and the client backed off"

    endpoints = {trace.endpoint for trace in traces}
    if "/api/get" in endpoints and "/api/search" in endpoints:
        return "slow because /get did not finish the lookup and the workflow fell back to /search"

    if len(traces) > 1:
        return "slow because the lookup required multiple LRCLIB attempts"

    if result.status == "not_found" and total_elapsed_seconds >= 3.0:
        return "slow not-found result; LRCLIB responded, but no confident match was accepted"

    return "single-request LRCLIB path"


def _pick_values(tags: Any, aliases: list[str]) -> list[str]:
    if not hasattr(tags, "keys"):
        return []

    for alias in aliases:
        raw_value = tags.get(alias)
        values = _coerce_values(raw_value)
        if values:
            return values
    return []


def _pick_first_value(tags: Any, aliases: list[str]) -> str | None:
    values = _pick_values(tags, aliases)
    return values[0] if values else None


def _coerce_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped_value = value.strip()
        return [stripped_value] if stripped_value else []
    if isinstance(value, (list, tuple, set)):
        values: list[str] = []
        for item in value:
            values.extend(_coerce_values(item))
        return values

    stripped_value = str(value).strip()
    return [stripped_value] if stripped_value else []


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_float(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}"


def _format_artists(artists: Any) -> str:
    if isinstance(artists, list) and artists:
        return "; ".join(str(artist) for artist in artists)
    if isinstance(artists, str) and artists.strip():
        return artists
    return "-"


def _format_trace_params(params: dict[str, Any]) -> str:
    if not params:
        return "{}"

    ordered_parts = [f"{key}={value!r}" for key, value in sorted(params.items())]
    return "{" + ", ".join(ordered_parts) + "}"


if __name__ == "__main__":
    main()
