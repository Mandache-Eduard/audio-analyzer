# data_and_error_logging.py
import csv
import os
from typing import Any, Dict, Iterable

RESULT_FIELDNAMES: list[str] = [
    "path",
    "status",
    "confidence",
    "elapsed_s",
    "samplerate_hz",
    "num_samples",
    "num_total_frames",
    "num_non-silent_frames",
    "effective_cutoff_hz",
    "per_cutoff_active_fraction",
]

def append_result_to_csv(
    csv_path: str,
    result: Dict[str, Any],
    fieldnames: Iterable[str] = RESULT_FIELDNAMES,
) -> None:

    parent_dir = os.path.dirname(os.path.abspath(csv_path))
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    file_exists = os.path.isfile(csv_path)
    row = {k: result.get(k, "") for k in fieldnames}

    conf = row.get("confidence")
    if isinstance(conf, (float, int)):
        row["confidence"] = f"{float(conf):.6f}"

    elapsed = row.get("elapsed_s")
    if isinstance(elapsed, (float, int)):
        row["elapsed_s"] = f"{float(elapsed):.6f}"

    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=list(fieldnames),
            extrasaction="ignore",
            quoting=csv.QUOTE_MINIMAL,
        )
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)
