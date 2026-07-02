from __future__ import annotations

from pathlib import Path
from typing import Protocol


class TrashBackend(Protocol):
    def move_to_trash(self, path: Path) -> None:
        ...


class SendToTrashBackend:
    def __init__(self) -> None:
        try:
            from send2trash import send2trash
        except ImportError as exc:
            raise RuntimeError(
                "Cleanup requires the 'send2trash' package to move files to the OS Recycle Bin."
            ) from exc

        self._send2trash = send2trash

    def move_to_trash(self, path: Path) -> None:
        self._send2trash(str(path))
