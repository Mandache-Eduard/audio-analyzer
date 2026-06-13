from __future__ import annotations

import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable

from tqdm import tqdm


def copy_planned_files(planned_files: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    planned_file_list = list(planned_files)
    if not planned_file_list:
        return []

    cores = os.cpu_count() or 1
    max_workers = max(1, min(cores // 2, 6))
    reserved_output_paths: set[str] = set()
    reservation_lock = threading.Lock()
    copy_results: list[dict[str, Any] | None] = [None] * len(planned_file_list)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {
            executor.submit(
                _copy_planned_file_reserved,
                planned_file,
                reserved_output_paths,
                reservation_lock,
            ): index
            for index, planned_file in enumerate(planned_file_list)
        }

        for future in tqdm(
            as_completed(future_to_index),
            total=len(future_to_index),
            desc="Files copied",
            unit="file",
        ):
            index = future_to_index[future]
            planned_file = planned_file_list[index]
            try:
                copy_results[index] = future.result()
            except Exception as exc:
                copy_results[index] = _copy_exception_result(planned_file, exc)

    return [
        copy_result
        if copy_result is not None
        else _copy_exception_result(planned_file, RuntimeError("copy worker produced no result"))
        for planned_file, copy_result in zip(planned_file_list, copy_results)
    ]


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


def _copy_planned_file_reserved(
    planned_file: dict[str, Any],
    reserved_output_paths: set[str],
    reservation_lock: threading.Lock,
) -> dict[str, Any]:
    planned_status = planned_file.get("status")
    if planned_status not in {"planned", "unmatched"}:
        return copy_planned_file(planned_file)

    planned_output_path = Path(planned_file["planned_output_path"])
    output_path_key = _normalize_output_path_key(planned_output_path)

    with reservation_lock:
        if output_path_key in reserved_output_paths:
            return {
                "original_path": str(Path(planned_file["original_path"])),
                "copied_path": str(planned_output_path),
                "status": "error",
                "reason": "planned output path already exists; refusing to overwrite",
                "metadata": planned_file.get("metadata"),
            }
        reserved_output_paths.add(output_path_key)

    return copy_planned_file(planned_file)


def _normalize_output_path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


def _copy_exception_result(planned_file: dict[str, Any], exc: Exception) -> dict[str, Any]:
    original_path = planned_file.get("original_path")
    planned_output_path = planned_file.get("planned_output_path")
    return {
        "original_path": str(original_path) if original_path is not None else None,
        "copied_path": str(planned_output_path) if planned_output_path is not None else None,
        "status": "error",
        "reason": str(exc),
        "metadata": planned_file.get("metadata"),
    }
