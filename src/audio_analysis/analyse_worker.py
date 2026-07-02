from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


SRC_ROOT = Path(__file__).resolve().parent.parent
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    input_path = Path(args.path).expanduser()
    report_path = Path(args.report).expanduser()
    cache_db_path = Path(args.cache_db).expanduser() if args.cache_db else None

    base_report: dict[str, object] = {
        "path": str(input_path),
        "refresh_cache": args.refresh_cache,
        "use_cache": not args.no_cache,
        "cache_db": str(cache_db_path) if cache_db_path is not None else None,
    }

    try:
        if not input_path.exists():
            raise FileNotFoundError(f"Analysis input does not exist: {input_path}")

        from audio_analysis.analyse_modes import (
            analyse_folder_batch,
            analyse_single_file,
            generate_single_file_spectrogram_if_upscaled,
        )
        from audio_analysis.data_and_error_logging import create_csv_path
        from caching_and_duplicate_detection.audio_cache import AudioCache

        effective_cache = None
        if not args.no_cache:
            cache = AudioCache(cache_db_path)
            cache.initialize()
            effective_cache = cache if cache.is_enabled else None

        if input_path.is_dir():
            csv_path = Path(create_csv_path(str(input_path)))
            analyse_folder_batch(
                str(input_path),
                cache=effective_cache,
                refresh_cache=args.refresh_cache,
                csv_path=str(csv_path),
            )
            report = {
                "status": "ok",
                **base_report,
                "result": {
                    "mode": "folder",
                    "folder_path": str(input_path),
                    "csv_path": str(csv_path),
                },
            }
            _write_json_report(report_path, report)
            return 0

        result = analyse_single_file(
            str(input_path),
            want_verbose=True,
            cache=effective_cache,
            refresh_cache=args.refresh_cache,
        )
        spectrogram_path, spectrogram_error = generate_single_file_spectrogram_if_upscaled(
            input_path,
            result,
            want_verbose=True,
        )

        report = {
            "status": "ok",
            **base_report,
            "result": {
                "mode": "file",
                "folder_path": str(input_path.parent),
                "result": result,
                "spectrogram_path": str(spectrogram_path) if spectrogram_path is not None else None,
                "spectrogram_error": spectrogram_error,
            },
        }
        _write_json_report(report_path, report)
        return 0
    except Exception as exc:
        _write_json_report(
            report_path,
            {
                "status": "error",
                **base_report,
                "error": str(exc),
                "details": type(exc).__name__,
            },
        )
        raise


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="analyse_worker.py",
        description="Run audio analysis in a backend-ready Python runtime.",
    )
    parser.add_argument("--path", required=True, help="File or folder to process.")
    parser.add_argument("--report", required=True, help="JSON report path.")
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Recompute cached analysis rows.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable the persistent SQLite cache for this run.",
    )
    parser.add_argument(
        "--cache-db",
        help="Custom SQLite cache database path.",
    )
    return parser.parse_args(argv)


def _write_json_report(report_path: Path, report: dict[str, object]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = report_path.with_suffix(report_path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(_json_safe_value(report), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    shutil.move(str(temporary_path), str(report_path))


def _json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return _json_safe_value(asdict(value))
    if isinstance(value, dict):
        return {
            str(key): _json_safe_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_json_safe_value(item) for item in sorted(value, key=repr)]
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


if __name__ == "__main__":
    raise SystemExit(main())
