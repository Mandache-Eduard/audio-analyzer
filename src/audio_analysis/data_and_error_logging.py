# data_and_error_logging.py
import csv
import os

from datetime import datetime
from typing import Any, Dict, Iterable, Sequence

RESULT_FIELDNAMES: list[str] = [
    "path",
    "status",
    "confidence",
    "samplerate_hz",
    "num_samples",
    "num_total_frames",
    "num_non-silent_frames",
    "effective_cutoff_hz",
    "per_cutoff_active_fraction",
]

def create_csv_path(folder_path):
    current_datetime = datetime.now()
    current_daytime_formatted = current_datetime.strftime('%Y-%B-%d__%H-%M-%S')
    dated_csv_path = os.path.join(folder_path, current_daytime_formatted + ".csv")
    return dated_csv_path

def append_results_to_csv(
    csv_path: str,
    results: Sequence[Dict[str, Any]],
    fieldnames: Iterable[str] = RESULT_FIELDNAMES,
) -> None:

    rows = []
    file_exists = os.path.isfile(csv_path)

    for result in results:
        row = {k: result.get(k, "") for k in fieldnames}
        rows.append(row)

        conf = row.get("confidence")
        if isinstance(conf, (float, int)):
            row["confidence"] = f"{float(conf):.6f}"

    with open(csv_path, "a", newline="", encoding="utf-8") as f:

        writer = csv.DictWriter(
            f,
            fieldnames=list(fieldnames),
            extrasaction="ignore",
            quoting=csv.QUOTE_MINIMAL,
        )
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)
