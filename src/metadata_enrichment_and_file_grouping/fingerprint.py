from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class FingerprintResult:
    fingerprint: str
    duration_seconds: float


class FingerprintService:
    def __init__(self) -> None:
        self._acoustid = _import_acoustid()

    def create_fingerprint(self, original_path: str | Path) -> FingerprintResult:
        fpcalc_path = shutil.which("fpcalc")
        if fpcalc_path is None:
            raise RuntimeError(
                "Chromaprint fingerprinting is unavailable because 'fpcalc' was not found in PATH."
            )

        path = Path(original_path)
        if not path.exists():
            raise FileNotFoundError(f"Audio file does not exist: {path}")

        try:
            duration_seconds, fingerprint = self._acoustid.fingerprint_file(
                str(path),
                force_fpcalc=True,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Could not generate Chromaprint fingerprint for {path.name}. "
                "Verify that pyacoustid, Chromaprint, and fpcalc are installed correctly."
            ) from exc

        if not fingerprint:
            raise RuntimeError(f"Fingerprint generation returned no fingerprint for {path.name}.")

        return FingerprintResult(
            fingerprint=fingerprint,
            duration_seconds=float(duration_seconds),
        )


def _import_acoustid() -> Any:
    try:
        import acoustid
    except ImportError as exc:
        raise RuntimeError(
            "pyacoustid is required for fingerprint generation. "
            "Install the 'pyacoustid' package before using group mode."
        ) from exc

    return acoustid
