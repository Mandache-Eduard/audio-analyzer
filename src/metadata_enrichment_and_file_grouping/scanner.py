from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

SUPPORTED_EXTENSIONS = frozenset(
    {
        ".mp3",
        ".flac",
        ".m4a",
        ".ogg",
        ".opus",
        ".wav",
        ".aiff",
        ".wma",
    }
)

@dataclass(frozen=True, slots=True)
class ScannedAudioFile:
    original_path: Path
    extension: str
    file_size: int
    duration_seconds: float | None

def is_supported_audio_file(path: str | os.PathLike[str]) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_EXTENSIONS

def scan_audio_files(
    root_path: str | os.PathLike[str],
    *,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[ScannedAudioFile]:
    return scan_audio_files_with_progress(
        root_path,
        progress_callback=progress_callback,
    )


def scan_audio_files_with_progress(
    root_path: str | os.PathLike[str],
    *,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[ScannedAudioFile]:
    root = Path(root_path).expanduser()

    if not root.exists():
        raise FileNotFoundError(f"Input path does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Input path is not a directory: {root}")

    scanned_files: list[ScannedAudioFile] = []
    stack = [root]

    while stack:
        current_dir = stack.pop()
        if progress_callback is not None:
            progress_callback(len(scanned_files), len(stack) + 1)

        try:
            with os.scandir(current_dir) as iterator:
                entries = sorted(
                    iterator,
                    key=lambda entry: (not entry.is_dir(follow_symlinks=False), entry.name.lower()),
                )
        except OSError:
            continue

        for entry in entries:
            entry_path = Path(entry.path)

            if entry.is_dir(follow_symlinks=False):
                stack.append(entry_path)
                continue

            if not entry.is_file(follow_symlinks=False) or not is_supported_audio_file(entry.name):
                continue

            try:
                file_size = entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue

            scanned_files.append(
                ScannedAudioFile(
                    original_path=entry_path.resolve(),
                    extension=entry_path.suffix.lower(),
                    file_size=file_size,
                    duration_seconds=None,
                )
            )
    return scanned_files

def iter_audio_files(root_path: str | os.PathLike[str]) -> Iterable[ScannedAudioFile]:
    yield from scan_audio_files_with_progress(root_path)
