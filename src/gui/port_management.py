from __future__ import annotations

import socket
import urllib.error
import urllib.request
from dataclasses import dataclass


DEFAULT_GUI_HOST = "127.0.0.1"
DEFAULT_GUI_PORT = 8080
GUI_PORT_SEARCH_RANGE = 20
GUI_PORT_LOCKED_ENV = "AUDIO_ANALYZER_GUI_PORT_LOCKED"
GUI_STATUS_PATH = "/__audio_analyzer_gui__/status"


@dataclass(frozen=True)
class GuiPortResolution:
    running_port: int | None
    selected_port: int


def get_gui_base_url(host: str, port: int) -> str:
    return f"http://{host}:{port}/"


def get_gui_status_url(host: str, port: int) -> str:
    return f"http://{host}:{port}{GUI_STATUS_PATH}"


def is_gui_running(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(get_gui_status_url(host, port), timeout=timeout) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def find_running_gui_port(host: str, preferred_port: int, search_range: int) -> int | None:
    for candidate_port in range(preferred_port, preferred_port + search_range):
        if is_gui_running(host, candidate_port):
            return candidate_port
    return None


def find_available_port(host: str, preferred_port: int, search_range: int) -> int:
    for candidate_port in range(preferred_port, preferred_port + search_range):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, candidate_port))
            except OSError:
                continue
            return candidate_port
    raise RuntimeError(
        f"No available GUI port found from {preferred_port} to "
        f"{preferred_port + search_range - 1}."
    )


def resolve_gui_port(host: str, preferred_port: int, search_range: int) -> GuiPortResolution:
    running_port = find_running_gui_port(host, preferred_port, search_range)
    if running_port is not None:
        return GuiPortResolution(running_port=running_port, selected_port=running_port)

    return GuiPortResolution(
        running_port=None,
        selected_port=find_available_port(host, preferred_port, search_range),
    )
