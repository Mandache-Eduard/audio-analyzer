from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Iterable


def copy_planned_files(planned_files: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    copy_results: list[dict[str, Any]] = []

    for planned_file in planned_files:
        copy_results.append(copy_planned_file(planned_file))

    return copy_results


def copy_planned_file(planned_file: dict[str, Any]) -> dict[str, Any]:
    original_path = Path(planned_file["original_path"])
    planned_output_path = Path(planned_file["planned_output_path"])
    metadata = planned_file.get("metadata")
    planned_status = planned_file.get("status")

    if planned_status == "duplicate_skipped":
        return {
            "original_path": str(original_path),
            "copied_path": None,
            "status": "skipped",
            "reason": planned_file.get("reason") or "duplicate planned output was skipped",
            "metadata": metadata,
        }

    if planned_status not in {"planned", "unmatched"}:
        return {
            "original_path": str(original_path),
            "copied_path": None,
            "status": "skipped",
            "reason": planned_file.get("reason") or f"unsupported planned status: {planned_status}",
            "metadata": metadata,
        }

    if not original_path.exists():
        return {
            "original_path": str(original_path),
            "copied_path": str(planned_output_path),
            "status": "error",
            "reason": "original input file does not exist",
            "metadata": metadata,
        }

    if planned_output_path.exists():
        return {
            "original_path": str(original_path),
            "copied_path": str(planned_output_path),
            "status": "error",
            "reason": "planned output path already exists; refusing to overwrite",
            "metadata": metadata,
        }

    try:
        planned_output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original_path, planned_output_path)
    except Exception as exc:
        return {
            "original_path": str(original_path),
            "copied_path": str(planned_output_path),
            "status": "error",
            "reason": str(exc),
            "metadata": metadata,
        }

    return {
        "original_path": str(original_path),
        "copied_path": str(planned_output_path),
        "status": "copied",
        "reason": None,
        "metadata": metadata,
    }
