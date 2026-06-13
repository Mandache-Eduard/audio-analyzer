from __future__ import annotations

import logging
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from tqdm import tqdm

LOGGER = logging.getLogger(__name__)
LOCAL_FPCALC_PATH = Path(__file__).resolve().parents[2] / "tools" / "fpcalc.exe"


@dataclass(frozen=True, slots=True)
class FingerprintResult:
    fingerprint: bytes
    duration_seconds: float


class FingerprintService:
    def __init__(self) -> None:
        self._fpcalc_path = _resolve_local_fpcalc_path()
        self._missing_fpcalc_warning_emitted = False
        if self._fpcalc_path is None:
            self._emit_missing_fpcalc_warning()

    def create_fingerprint(self, original_path: str | Path) -> FingerprintResult:
        if self._fpcalc_path is None:
            self._emit_missing_fpcalc_warning()
            raise RuntimeError(
                "Chromaprint fingerprinting is unavailable because local fpcalc was not found at "
                f"{LOCAL_FPCALC_PATH}."
            )

        path = Path(original_path)
        if not path.exists():
            raise FileNotFoundError(f"Audio file does not exist: {path}")

        try:
            duration_seconds, fingerprint = _run_fpcalc(
                fpcalc_path=self._fpcalc_path,
                audio_path=path,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Could not generate Chromaprint fingerprint for {path.name}. "
                "Verify that Chromaprint and the local tools\\fpcalc.exe are installed correctly."
            ) from exc

        if not fingerprint:
            raise RuntimeError(f"Fingerprint generation returned no fingerprint for {path.name}.")

        return FingerprintResult(
            fingerprint=fingerprint,
            duration_seconds=float(duration_seconds),
        )

    def create_fingerprint_batch(
        self,
        original_paths: Iterable[str | Path],
    ) -> dict[str, FingerprintResult | Exception]:
        path_list = [Path(original_path) for original_path in original_paths]
        if not path_list:
            return {}

        cores = os.cpu_count() or 1
        max_workers = max(1, min(cores // 2, 6))
        fingerprint_results: dict[str, FingerprintResult | Exception] = {}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_path = {
                executor.submit(self.create_fingerprint, path): path
                for path in path_list
            }

            for future in tqdm(
                as_completed(future_to_path),
                total=len(future_to_path),
                desc="Fingerprints processed",
                unit="file",
            ):
                path = future_to_path[future]
                try:
                    fingerprint_results[str(path)] = future.result()
                except Exception as exc:
                    fingerprint_results[str(path)] = exc

        return fingerprint_results

    def _emit_missing_fpcalc_warning(self) -> None:
        if self._missing_fpcalc_warning_emitted:
            return

        warning_message = (
            "Warning: local fpcalc executable was not found at "
            f"{LOCAL_FPCALC_PATH}. Fingerprint-based identification will be skipped."
        )
        LOGGER.warning(warning_message)
        print(warning_message, file=sys.stderr)
        self._missing_fpcalc_warning_emitted = True


def _resolve_local_fpcalc_path() -> Path | None:
    if LOCAL_FPCALC_PATH.is_file():
        return LOCAL_FPCALC_PATH
    return None


def _run_fpcalc(*, fpcalc_path: Path, audio_path: Path) -> tuple[float, bytes]:
    command = [
        str(fpcalc_path),
        "-length",
        "120",
        str(audio_path),
    ]
    try:
        completed_process = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"fpcalc invocation failed: {exc}") from exc

    if completed_process.returncode != 0:
        raise RuntimeError(f"fpcalc exited with status {completed_process.returncode}")

    return _parse_fpcalc_output(completed_process.stdout)


def _parse_fpcalc_output(output: bytes) -> tuple[float, bytes]:
    duration_seconds: float | None = None
    fingerprint: bytes | None = None

    for raw_line in output.splitlines():
        parts = raw_line.split(b"=", 1)
        if len(parts) != 2:
            raise RuntimeError("fpcalc output was malformed.")
        if parts[0] == b"DURATION":
            try:
                duration_seconds = float(parts[1])
            except ValueError as exc:
                raise RuntimeError("fpcalc duration was not numeric.") from exc
        elif parts[0] == b"FINGERPRINT":
            fingerprint = parts[1]

    if duration_seconds is None or fingerprint is None:
        raise RuntimeError("fpcalc output did not include duration and fingerprint.")

    return duration_seconds, fingerprint
