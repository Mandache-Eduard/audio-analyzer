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
    folder_path = Path(args.folder).expanduser()
    report_path = Path(args.report).expanduser()
    cache_db_path = Path(args.cache_db).expanduser() if args.cache_db else None
    csv_report_path = None
    if args.write_report:
        csv_report_path = (
            Path(args.output).expanduser()
            if args.output
            else folder_path / "duplicates_report.csv"
        )

    base_report: dict[str, object] = {
        "folder": str(folder_path),
        "refresh_cache": args.refresh_cache,
        "use_cache": not args.no_cache,
        "cache_db": str(cache_db_path) if cache_db_path is not None else None,
        "cleanup": args.cleanup,
        "csv_report_path": str(csv_report_path) if csv_report_path is not None else None,
    }

    try:
        if not folder_path.is_dir():
            raise FileNotFoundError(f"Duplicate detection folder does not exist: {folder_path}")

        from caching_and_duplicate_detection.audio_cache import AudioCache
        from caching_and_duplicate_detection.duplicate_cleanup_cli import (
            CANCEL_TOKEN,
            CONFIRMATION_PHRASE,
            run_cleanup_cli,
        )
        from caching_and_duplicate_detection.duplicate_detector import run_duplicate_detection
        from caching_and_duplicate_detection.trash_backend import SendToTrashBackend

        effective_cache = None
        if not args.no_cache:
            cache = AudioCache(cache_db_path)
            cache.initialize()
            effective_cache = cache if cache.is_enabled else None

        if args.cleanup:
            # Fail fast before the scan if the current runtime cannot perform cleanup.
            SendToTrashBackend()

        groups = run_duplicate_detection(
            str(folder_path),
            cache=effective_cache,
            refresh_cache=args.refresh_cache,
            output_path=str(csv_report_path) if csv_report_path is not None else None,
            cleanup=False,
        )
        cleanup_result = None
        if args.cleanup:
            if not args.cleanup_confirm:
                raise ValueError("Cleanup requires --cleanup-confirm.")

            scripted_responses = iter((CONFIRMATION_PHRASE,))

            def _noninteractive_cleanup_input(_prompt: str) -> str:
                return next(scripted_responses, CANCEL_TOKEN)

            cleanup_result = run_cleanup_cli(
                scan_root=str(folder_path),
                groups=groups,
                cache=effective_cache,
                input_func=_noninteractive_cleanup_input,
            )

        tier_counts: dict[str, int] = {}
        for group in groups:
            tier_counts[group.tier] = tier_counts.get(group.tier, 0) + 1

        report = {
            "status": "ok",
            **base_report,
            "result": {
                "group_count": len(groups),
                "tier_counts": tier_counts,
                "report_path": str(csv_report_path) if csv_report_path is not None else None,
                "cleanup_result": (
                    None
                    if cleanup_result is None
                    else {
                        "manifest_path": cleanup_result.manifest_path,
                        "cancelled": cleanup_result.cancelled,
                        "cancellation_reason": cleanup_result.cancellation_reason,
                        "moved_successfully_count": cleanup_result.moved_successfully_count,
                        "failed_count": cleanup_result.failed_count,
                        "skipped_count": cleanup_result.skipped_count,
                    }
                ),
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
        prog="duplicate_worker.py",
        description="Run duplicate detection in a backend-ready Python runtime.",
    )
    parser.add_argument("--folder", required=True, help="Folder to process.")
    parser.add_argument("--report", required=True, help="JSON report path.")
    parser.add_argument(
        "--output",
        help="Optional CSV report output path.",
    )
    parser.add_argument(
        "--no-report",
        action="store_false",
        dest="write_report",
        help="Skip writing a CSV report.",
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Recompute cached metadata and fingerprint rows.",
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
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Build a cleanup plan and move eligible files to the Recycle Bin.",
    )
    parser.add_argument(
        "--cleanup-confirm",
        action="store_true",
        help="Explicitly confirm cleanup for non-interactive runs.",
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
