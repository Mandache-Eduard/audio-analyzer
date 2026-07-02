from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
import tempfile
import traceback
from dataclasses import dataclass, field
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from nicegui import app, ui

from gui.port_management import (
    DEFAULT_GUI_HOST,
    DEFAULT_GUI_PORT,
    GUI_PORT_LOCKED_ENV,
    GUI_PORT_SEARCH_RANGE,
    GUI_STATUS_PATH,
    get_gui_base_url,
    is_gui_running,
    resolve_gui_port,
)
from workflow_runtime import (
    resolve_analysis_python_command,
    resolve_duplicate_python_command,
    resolve_metadata_python_command,
)


ACCENT = "#FF7F00"
PANEL = "#202124"
PANEL_ALT = "#242529"
BORDER = "#333438"
SUCCESS = "#6FAE7A"
ERROR = "#B96B6B"
WARNING = "#FD9024"

AUDIO_EXTENSIONS = {".flac", ".mp3", ".m4a", ".ogg", ".opus", ".wav", ".aiff", ".wma"}
GUI_RUN_GUARD_ENV = "AUDIO_ANALYZER_GUI_SERVER_ACTIVE"
SPLIT_PROGRESS_PREFIX = "__SPLIT_PROGRESS__ "


@dataclass
class JobState:
    operation: str = "analyse"
    running: bool = False
    cancel_requested: bool = False
    can_cancel: bool = False
    current_stage: str = "Idle"
    progress: float = 0.0
    last_result: str = "No task has run yet."
    last_report_paths: dict[str, Path | None] = field(default_factory=dict)
    last_folder_path: Path | None = None


state = JobState()
settings_panel = None
status_chip = None
stage_label = None
progress_bar = None
log_area = None
summary_label = None
open_report_button = None
open_folder_button = None
cancel_button = None
operation_buttons: dict[str, ui.button] = {}


OPERATIONS = {
    "duplicates": {
        "label": "Duplicate detection",
        "description": "Scan a folder for exact and perceptual duplicate candidates",
        "icon": "content_copy",
    },
    "analyse": {
        "label": "Analyse audio",
        "description": "Analyse audio for possible upscaling",
        "icon": "analytics",
    },
    "metadata": {
        "label": "Metadata tagging & album grouping",
        "description": "Tag audio files and group them into album releases",
        "icon": "album",
    },
    "split": {
        "label": "Split stems",
        "description": "Split audio files into stems",
        "icon": "graphic_eq",
    },
}


@app.get(GUI_STATUS_PATH, include_in_schema=False)
def _gui_status() -> dict[str, object]:
    return {
        "app": "audio-analyzer",
        "status": "ok",
        "pid": os.getpid(),
    }


def run() -> None:
    show_browser = os.environ.get("AUDIO_ANALYZER_GUI_SHOW", "1") != "0"
    host = os.environ.get("AUDIO_ANALYZER_GUI_HOST", DEFAULT_GUI_HOST)
    configured_port = int(os.environ.get("AUDIO_ANALYZER_GUI_PORT", str(DEFAULT_GUI_PORT)))
    port_locked = os.environ.get(GUI_PORT_LOCKED_ENV) == "1"

    if os.environ.get(GUI_RUN_GUARD_ENV) == "1" and is_gui_running(host, configured_port):
        return

    os.environ[GUI_RUN_GUARD_ENV] = "1"
    _configure_app()
    _build_layout()

    if port_locked:
        port = configured_port
    else:
        port_resolution = resolve_gui_port(host, configured_port, GUI_PORT_SEARCH_RANGE)
        if port_resolution.running_port is not None:
            print(f"GUI already running at {get_gui_base_url(host, port_resolution.running_port)}")
            return

        port = port_resolution.selected_port
        os.environ["AUDIO_ANALYZER_GUI_PORT"] = str(port)
        os.environ[GUI_PORT_LOCKED_ENV] = "1"
        if port != configured_port:
            print(f"Port {configured_port} is already in use; starting GUI on port {port}.")

    ui.run(
        title="Audio Analyzer",
        host=host,
        port=port,
        reload=False,
        show=show_browser,
        dark=True,
    )


def _configure_app() -> None:
    app.native.window_args["title"] = "Audio Analyzer"
    ui.dark_mode().enable()
    ui.colors(
        primary=ACCENT,
        secondary=PANEL_ALT,
        accent=ACCENT,
        positive=SUCCESS,
        negative=ERROR,
        warning=WARNING,
    )
    ui.add_head_html(
        """
        <style>
        :root {
            color-scheme: dark;
        }
        body {
            background: #191A1C;
            color: #BCBEC4;
            font-family: Inter, "Segoe UI", Arial, sans-serif;
        }
        .q-page {
            background: #191A1C;
        }
        .app-shell {
            min-height: 100vh;
            background: #191A1C;
            color: #BCBEC4;
        }
        .top-bar {
            height: 56px;
            padding: 0 22px;
            border-bottom: 1px solid #333438;
            background: #191A1C;
        }
        .app-title {
            color: #F0F1F4;
            font-size: 18px;
            font-weight: 650;
        }
        .status-chip {
            color: #BCBEC4;
            border: 1px solid #333438;
            background: #202124;
            border-radius: 999px;
            padding: 6px 12px;
            font-size: 13px;
        }
        .content-grid {
            display: grid;
            grid-template-columns: 260px minmax(360px, 1fr) minmax(360px, 1fr);
            min-height: calc(100vh - 56px);
        }
        .sidebar {
            border-right: 1px solid #333438;
            background: #191A1C;
            padding: 22px 14px;
        }
        .pane {
            padding: 22px;
            border-right: 1px solid #333438;
            background: #191A1C;
        }
        .status-pane {
            padding: 22px;
            background: #191A1C;
        }
        .panel {
            background: #202124;
            border: 1px solid #333438;
            border-radius: 8px;
            padding: 16px;
        }
        .panel-title {
            color: #F0F1F4;
            font-size: 16px;
            font-weight: 650;
        }
        .panel-subtitle {
            color: #8B8E96;
            font-size: 13px;
        }
        .operation-button {
            width: 100%;
            min-height: 58px;
            justify-content: flex-start;
            color: #BCBEC4;
            border-radius: 8px;
            padding: 8px 10px;
        }
        .operation-button .q-icon {
            color: #F0F1F4;
        }
        .operation-active {
            background: #242529;
            border: 1px solid #FF7F00;
            color: #F0F1F4;
        }
        .operation-active .q-icon {
            color: #F0F1F4;
        }
        .primary-action {
            background: #FF7F00;
            color: #111111;
            font-weight: 650;
            border-radius: 7px;
        }
        .secondary-action {
            border: 1px solid #333438;
            color: #BCBEC4;
            border-radius: 7px;
        }
        .danger-action {
            border: 1px solid #B96B6B;
            color: #E3A2A2;
            border-radius: 7px;
        }
        .field-label {
            color: #BCBEC4;
            font-size: 13px;
            font-weight: 600;
        }
        .hint {
            color: #8B8E96;
            font-size: 12px;
        }
        .terminal-log {
            width: 100%;
            height: 310px;
            background: #101113;
            color: #D2D4D9;
            border: 1px solid #333438;
            border-radius: 8px;
            padding: 12px;
            font-family: Consolas, "Cascadia Mono", monospace;
            font-size: 12px;
            line-height: 1.45;
            white-space: pre-wrap;
            overflow: auto;
        }
        .summary-box {
            min-height: 72px;
            background: #242529;
            border: 1px solid #333438;
            border-radius: 8px;
            padding: 12px;
            color: #BCBEC4;
        }
        .q-field--outlined .q-field__control:before {
            border-color: #333438;
        }
        .q-field__native,
        .q-field__label,
        .q-checkbox__label,
        .q-item__label {
            color: #BCBEC4;
        }
        .q-field--dark .q-field__control,
        .q-menu--dark,
        .q-list--dark {
            background: #242529;
        }
        .q-linear-progress {
            border-radius: 999px;
        }
        @media (max-width: 1100px) {
            .content-grid {
                grid-template-columns: 240px 1fr;
            }
            .status-pane {
                grid-column: 2;
                border-top: 1px solid #333438;
            }
        }
        @media (max-width: 760px) {
            .content-grid {
                grid-template-columns: 1fr;
            }
            .sidebar,
            .pane,
            .status-pane {
                border-right: 0;
                border-bottom: 1px solid #333438;
            }
        }
        </style>
        """
    )


def _build_layout() -> None:
    global settings_panel, status_chip, stage_label, progress_bar
    global log_area, summary_label, open_report_button, open_folder_button, cancel_button

    with ui.column().classes("app-shell w-full gap-0"):
        with ui.row().classes("top-bar w-full items-center"):
            ui.label("Media Library Manager").classes("app-title")
            ui.space()
            status_chip = ui.label("Localhost - Idle").classes("status-chip")

        with ui.element("div").classes("content-grid w-full"):
            with ui.column().classes("sidebar gap-3"):
                ui.label("Operations").classes("panel-title")
                for key, operation in OPERATIONS.items():
                    operation_buttons[key] = ui.button(
                        operation["label"],
                        icon=operation["icon"],
                        on_click=lambda selected=key: _select_operation(selected),
                    ).classes("operation-button")
                ui.space()
                ui.label("All workflows in this UI run against the local backend.").classes("hint")

            with ui.column().classes("pane gap-4"):
                settings_panel = ui.column().classes("w-full gap-4")

            with ui.column().classes("status-pane gap-4"):
                with ui.column().classes("panel w-full gap-3"):
                    ui.label("Progress / Status").classes("panel-title")
                    stage_label = ui.label("Idle").classes("panel-subtitle")
                    progress_bar = ui.linear_progress(value=0.0, show_value=False).classes("w-full")
                    with ui.row().classes("w-full items-center gap-2"):
                        cancel_button = ui.button("Cancel", icon="stop", on_click=_request_cancel).classes("danger-action")
                        cancel_button.props("outline disable")
                        ui.space()
                        open_report_button = ui.button("Open Report", icon="description", on_click=_open_last_report).classes("secondary-action")
                        open_folder_button = ui.button("Open Folder", icon="folder_open", on_click=_open_last_folder).classes("secondary-action")
                        open_report_button.props("outline disable")
                        open_folder_button.props("outline disable")
                with ui.column().classes("panel w-full gap-3"):
                    ui.label("Terminal-style log").classes("panel-title")
                    log_area = ui.label("Ready.").classes("terminal-log")
                with ui.column().classes("panel w-full gap-3"):
                    ui.label("Last result").classes("panel-title")
                    summary_label = ui.label(state.last_result).classes("summary-box")

    _render_settings()
    _refresh_status()


def _select_operation(operation: str) -> None:
    if state.running:
        ui.notify("Wait for the current job to finish or cancel it first.", color="warning")
        return
    state.operation = operation
    _render_settings()
    _refresh_status()


def _render_settings() -> None:
    if settings_panel is None:
        return
    settings_panel.clear()
    for key, button in operation_buttons.items():
        if key == state.operation:
            button.classes(add="operation-active")
        else:
            button.classes(remove="operation-active")

    with settings_panel:
        operation = OPERATIONS[state.operation]
        with ui.column().classes("panel w-full gap-4"):
            ui.label("Workflow Settings").classes("panel-title")
            ui.label(operation["description"]).classes("panel-subtitle")
            if state.operation == "duplicates":
                _render_duplicates_settings()
            elif state.operation == "analyse":
                _render_analyse_settings()
            elif state.operation == "metadata":
                _render_metadata_settings()
            else:
                _render_split_settings()


def _render_duplicates_settings() -> None:
    folder_input = _path_input("Folder path", "Select the folder to scan for duplicates")
    _browse_button(folder_input, mode="folder")
    refresh_cache, use_cache, cache_db_input = _render_cache_controls(
        "Reuse or refresh cached metadata, fingerprints, and identifier lookups."
    )
    write_report = ui.checkbox("Write CSV report", value=True).classes("field-label")
    output_path_input = ui.input(
        label="CSV report path",
        placeholder="Optional path. Leave blank to use <folder>\\duplicates_report.csv",
    ).classes("w-full")
    output_path_input.props("outlined dense clearable")
    cleanup = ui.checkbox("Move cleanup-eligible exact duplicates to Recycle Bin").classes("field-label")
    cleanup_confirm = ui.checkbox(
        "I understand cleanup moves exact binary duplicates to the Recycle Bin"
    ).classes("field-label")

    with ui.row().classes("w-full items-center gap-3"):
        ui.space()
        ui.button(
            "Start",
            icon="play_arrow",
            on_click=lambda: _start_duplicates_job(
                folder_input.value,
                refresh_cache=refresh_cache.value,
                use_cache=use_cache.value,
                cache_db_value=cache_db_input.value,
                write_report=write_report.value,
                output_path_value=output_path_input.value,
                cleanup=cleanup.value,
                cleanup_confirmed=cleanup_confirm.value,
            ),
        ).classes("primary-action")


def _render_analyse_settings() -> None:
    path_input = _path_input("File or folder path", "Select a FLAC file or a folder containing FLAC files")
    with ui.row().classes("w-full items-center gap-2"):
        _browse_button(path_input, mode="file", label="Browse File", icon="audio_file")
        _browse_button(path_input, mode="folder", label="Browse Folder", icon="folder_open")
    ui.label("The path will be detected automatically as a single FLAC file or a folder when you start the analysis.").classes("hint")
    refresh_cache, use_cache, cache_db_input = _render_cache_controls(
        "Recompute cached analysis rows for this run."
    )

    with ui.row().classes("w-full items-center gap-3"):
        ui.space()
        ui.button(
            "Start",
            icon="play_arrow",
            on_click=lambda: _start_analyse_job(
                path_input.value,
                refresh_cache=refresh_cache.value,
                use_cache=use_cache.value,
                cache_db_value=cache_db_input.value,
            ),
        ).classes("primary-action")


def _render_metadata_settings() -> None:
    folder_input = _path_input("Folder path", "Select the folder that should be grouped into album releases")
    _browse_button(folder_input, mode="folder")
    lyrics_mode = ui.select(
        {
            "lyrics-none": "No lyrics",
            "lyrics-unsynced": "Unsynced lyrics",
            "lyrics-synced": "Synced lyrics",
        },
        value="lyrics-none",
        label="Lyrics mode",
    ).classes("w-full")
    lyrics_mode.props("outlined dense")
    refresh_cache, use_cache, cache_db_input = _render_cache_controls(
        "Reuse or refresh cached identifiers, fingerprints, and metadata lookups."
    )
    ui.label("Grouped copies are written into the folder's sorted_files output directory.").classes("hint")
    overwrite_confirm = ui.checkbox(
        "I understand this workflow writes grouped copies into the output folder"
    ).classes("field-label")

    with ui.row().classes("w-full items-center gap-3"):
        ui.space()
        ui.button(
            "Start",
            icon="play_arrow",
            on_click=lambda: _start_metadata_job(
                folder_input.value,
                lyrics_mode.value,
                refresh_cache=refresh_cache.value,
                use_cache=use_cache.value,
                cache_db_value=cache_db_input.value,
                confirmed=overwrite_confirm.value,
            ),
        ).classes("primary-action")


def _render_split_settings() -> None:
    file_input = _path_input("Audio file path", "Select a local audio file")
    _browse_button(file_input, mode="file")

    ui.label("Outputs").classes("field-label")
    with ui.grid(columns=2).classes("w-full gap-2"):
        vocals = ui.checkbox("Vocals", value=True)
        instrumental = ui.checkbox("Instrumental", value=True)
        bass = ui.checkbox("Bass")
        drums = ui.checkbox("Drums")
        lyrics = ui.checkbox("Lyrics")

    device = ui.select(["auto", "cpu", "cuda"], value="auto", label="Device").classes("w-full")
    device.props("outlined dense")
    lyrics_mode = ui.select(["none", "plain", "timestamped"], value="none", label="Lyrics mode").classes("w-full")
    lyrics_mode.props("outlined dense")
    language = ui.input("Language", value="auto").classes("w-full")
    language.props("outlined dense")
    ui.label("Outputs are written into split_files/<track name>, using the backend's configured local models.").classes("hint")
    overwrite = ui.checkbox("Overwrite existing split outputs").classes("field-label")
    overwrite_confirm = ui.checkbox("I understand overwrite replaces existing split output files").classes("field-label")

    with ui.row().classes("w-full items-center gap-3"):
        ui.space()
        ui.button(
            "Start",
            icon="play_arrow",
            on_click=lambda: _start_split_job(
                file_input.value,
                {
                    "vocals": vocals.value,
                    "instrumental": instrumental.value,
                    "bass": bass.value,
                    "drums": drums.value,
                    "lyrics": lyrics.value,
                },
                device.value,
                lyrics_mode.value,
                language.value,
                overwrite.value,
                overwrite_confirm.value,
            ),
        ).classes("primary-action")


def _path_input(label: str, placeholder: str) -> ui.input:
    ui.label(label).classes("field-label")
    path_input = ui.input(placeholder=placeholder).classes("w-full")
    path_input.props("outlined dense")
    return path_input


def _render_cache_controls(cache_hint: str) -> tuple[ui.checkbox, ui.checkbox, ui.input]:
    ui.label("Cache").classes("field-label")
    refresh_cache = ui.checkbox("Refresh cached data").classes("field-label")
    use_cache = ui.checkbox("Use persistent cache", value=True).classes("field-label")
    cache_db_input = ui.input(
        label="Custom cache database",
        placeholder="Optional path to a SQLite cache database",
    ).classes("w-full")
    cache_db_input.props("outlined dense clearable")
    ui.label(cache_hint).classes("hint")
    return refresh_cache, use_cache, cache_db_input


def _optional_cache_db_path(raw_value: str | None) -> Path | None:
    if raw_value is None:
        return None
    normalized = raw_value.strip()
    if not normalized:
        return None
    return Path(normalized).expanduser()


def _optional_output_path(raw_value: str | None) -> Path | None:
    if raw_value is None:
        return None
    normalized = raw_value.strip()
    if not normalized:
        return None
    return Path(normalized).expanduser()


def _browse_button(
    input_element: ui.input,
    *,
    mode: str,
    label: str = "Browse",
    icon: str = "folder_open",
) -> None:
    async def choose_path() -> None:
        selected = await asyncio.to_thread(_open_native_path_dialog, mode, input_element.value)
        if selected:
            input_element.value = selected

    ui.button(label, icon=icon, on_click=choose_path).classes("secondary-action").props("outline")


def _open_native_path_dialog(mode: str, start_path: str | None) -> str | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        return None

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    initial_dir = _dialog_initial_directory(start_path)

    try:
        if mode == "folder":
            selected = filedialog.askdirectory(
                title="Select folder",
                initialdir=str(initial_dir),
                parent=root,
                mustexist=True,
            )
        else:
            selected = filedialog.askopenfilename(
                title="Select audio file",
                initialdir=str(initial_dir),
                parent=root,
                filetypes=[
                    ("Audio files", "*.flac *.mp3 *.m4a *.ogg *.opus *.wav *.aiff *.wma"),
                    ("FLAC files", "*.flac"),
                    ("All files", "*.*"),
                ],
            )
        return selected or None
    finally:
        root.destroy()


def _dialog_initial_directory(start_path: str | None) -> Path:
    if start_path:
        candidate = Path(start_path.strip('" '))
        if candidate.is_dir():
            return candidate
        if candidate.parent.is_dir():
            return candidate.parent
    return Path.home()


def _split_output_root_for_input(input_path: Path) -> Path:
    return input_path.parent / "split_files"


def _split_track_output_root(input_path: Path) -> Path:
    return _split_output_root_for_input(input_path) / input_path.stem


def _split_report_path_for_input(input_path: Path) -> Path:
    return _split_track_output_root(input_path) / "report.json"


def _parse_split_progress_event(line: str) -> dict[str, object] | None:
    if not line.startswith(SPLIT_PROGRESS_PREFIX):
        return None
    payload = line[len(SPLIT_PROGRESS_PREFIX):]
    try:
        event = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def _start_analyse_job(
    path_value: str | None,
    *,
    refresh_cache: bool,
    use_cache: bool,
    cache_db_value: str | None,
) -> None:
    path = _validated_path(path_value, "Choose a FLAC file or folder before starting.")
    if path is None:
        return
    if path.is_file() and path.suffix.lower() != ".flac":
        ui.notify("Analyse currently expects a FLAC file or a folder.", color="warning")
        return

    state.last_folder_path = path if path.is_dir() else path.parent
    _set_last_report_path("analyse", None)
    asyncio.create_task(
        _run_analyse_job(
            path,
            refresh_cache=refresh_cache,
            use_cache=use_cache,
            cache_db_path=_optional_cache_db_path(cache_db_value),
        )
    )


def _start_duplicates_job(
    folder_value: str | None,
    *,
    refresh_cache: bool,
    use_cache: bool,
    cache_db_value: str | None,
    write_report: bool,
    output_path_value: str | None,
    cleanup: bool,
    cleanup_confirmed: bool,
) -> None:
    folder = _validated_folder(folder_value, "Choose a folder before starting duplicate detection.")
    if folder is None:
        return
    if cleanup and not _require_confirmation(cleanup_confirmed):
        return

    _set_last_report_path("duplicates", None)
    state.last_folder_path = folder
    asyncio.create_task(
        _run_duplicates_job(
            folder,
            refresh_cache=refresh_cache,
            use_cache=use_cache,
            cache_db_path=_optional_cache_db_path(cache_db_value),
            write_report=write_report,
            output_path=_optional_output_path(output_path_value),
            cleanup=cleanup,
        )
    )


def _start_metadata_job(
    folder_value: str | None,
    lyrics_mode: str,
    *,
    refresh_cache: bool,
    use_cache: bool,
    cache_db_value: str | None,
    confirmed: bool,
) -> None:
    folder = _validated_folder(folder_value, "Choose a folder before starting metadata grouping.")
    if folder is None or not _require_confirmation(confirmed):
        return

    _set_last_report_path("metadata", None)
    state.last_folder_path = folder / "sorted_files"
    asyncio.create_task(
        _run_metadata_job(
            folder,
            lyrics_mode,
            refresh_cache=refresh_cache,
            use_cache=use_cache,
            cache_db_path=_optional_cache_db_path(cache_db_value),
        )
    )


def _start_split_job(
    file_value: str | None,
    outputs: dict[str, bool],
    device: str,
    lyrics_mode: str,
    language: str,
    overwrite: bool,
    confirmed: bool,
) -> None:
    file_path = _validated_path(file_value, "Choose an audio file before starting split.")
    if file_path is None:
        return
    if not file_path.is_file() or file_path.suffix.lower() not in AUDIO_EXTENSIONS:
        ui.notify("Choose a supported audio file.", color="warning")
        return
    if overwrite and not _require_confirmation(confirmed):
        return

    requested_outputs = [name for name, enabled in outputs.items() if enabled]
    if not requested_outputs:
        ui.notify("Select at least one split output.", color="warning")
        return

    _set_last_report_path("split", None)
    state.last_folder_path = _split_track_output_root(file_path)
    asyncio.create_task(
        _run_split_job(
            file_path,
            requested_outputs=requested_outputs,
            device=device,
            lyrics_mode=lyrics_mode,
            language=language or "auto",
            overwrite=overwrite,
        )
    )


class _GuiLogStream(io.TextIOBase):
    def __init__(self, callback) -> None:
        super().__init__()
        self._callback = callback
        self._buffer = ""

    def write(self, text: str) -> int:
        if not text:
            return 0
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.rstrip()
            if line and not _is_gui_log_noise(line):
                self._callback(line)
        return len(text)

    def flush(self) -> None:
        remaining = self._buffer.strip()
        self._buffer = ""
        if remaining and not _is_gui_log_noise(remaining):
            self._callback(remaining)


def _is_gui_log_noise(line: str) -> bool:
    normalized = line.strip()
    return (
        normalized.startswith("NiceGUI ready to go on http://")
        or (
            normalized.startswith("Port ")
            and " is already in use; starting GUI on port " in normalized
        )
    )


def _metadata_stage_from_log_line(line: str) -> tuple[str, float] | None:
    normalized = line.strip().lower()
    stage_patterns = (
        ("file discovery", "Discovering audio files", 0.08),
        ("metadata identifiers", "Reading existing metadata", 0.18),
        ("identifiers resolved", "Resolving metadata identifiers", 0.42),
        ("album clustering", "Clustering resolved tracks", 0.56),
        ("releases selected", "Selecting album releases", 0.68),
        ("release selection:", "Selecting album releases", 0.72),
        ("planned files:", "Planning grouped output", 0.8),
        ("lyrics mode:", "Applying lyrics workflow", 0.88),
        ("lyrics progress:", "Applying lyrics workflow", 0.9),
        ("final summary:", "Finalizing metadata workflow", 0.95),
    )

    for pattern, stage, progress in stage_patterns:
        if pattern in normalized:
            return stage, progress
    return None


def _duplicates_stage_from_log_line(line: str) -> tuple[str, float] | None:
    normalized = line.strip().lower()
    stage_patterns = (
        ("metadata scanned", "Reading audio metadata", 0.28),
        ("identifiers resolved", "Resolving identifiers", 0.62),
        ("duplicate detection report:", "Building duplicate report", 0.84),
        ("csv report saved to:", "Writing CSV report", 0.95),
        ("cleanup plan:", "Planning cleanup", 0.9),
        ("cleanup summary:", "Finalizing cleanup", 0.96),
        ("cleanup manifest saved to:", "Writing cleanup manifest", 0.98),
    )

    for pattern, stage, progress in stage_patterns:
        if pattern in normalized:
            return stage, progress
    return None


def _analysis_stage_from_log_line(line: str) -> tuple[str, float] | None:
    normalized = line.strip().lower()
    stage_patterns = (
        ("discovering files", "Discovering files", 0.18),
        ("processing files", "Processing files", 0.36),
        ("loaded '", "Loading audio file", 0.34),
        ("divided audio into", "Dividing audio into frames", 0.52),
        ("analyzed ", "Analyzing frames", 0.72),
        ("result:", "Determining file status", 0.88),
        ("generating spectrograms for upscaled files", "Generating spectrograms", 0.93),
        ("spectrogram written to", "Writing spectrogram", 0.97),
    )

    for pattern, stage, progress in stage_patterns:
        if pattern in normalized:
            return stage, progress
    return None


async def _run_split_job(
    file_path: Path,
    *,
    requested_outputs: list[str],
    device: str,
    lyrics_mode: str,
    language: str,
    overwrite: bool,
) -> None:
    if state.running:
        ui.notify("A job is already running.", color="warning")
        return

    loop = asyncio.get_running_loop()
    state.running = True
    state.cancel_requested = False
    state.can_cancel = False
    state.progress = 0.05
    state.current_stage = "Preparing split workflow"
    state.last_result = "Split stems in progress."
    _set_log(f"Split stems started for: {file_path}")
    _refresh_status()

    def append_from_worker(message: str) -> None:
        loop.call_soon_threadsafe(_append_split_log_and_update, message)

    def worker() -> dict[str, object]:
        from config import AUDIO_ML_PYTHON_PATH, AUDIO_ML_WORKER_PATH

        if not AUDIO_ML_PYTHON_PATH.is_file():
            raise RuntimeError(f"Audio ML Python interpreter was not found: {AUDIO_ML_PYTHON_PATH}")
        if not AUDIO_ML_WORKER_PATH.is_file():
            raise RuntimeError(f"Audio ML worker script was not found: {AUDIO_ML_WORKER_PATH}")

        output_root = _split_output_root_for_input(file_path)
        report_path = _split_report_path_for_input(file_path)
        command = [
            str(AUDIO_ML_PYTHON_PATH),
            "-u",
            str(AUDIO_ML_WORKER_PATH),
            "--input",
            str(file_path),
            "--outputs",
            ",".join(requested_outputs),
            "--lyrics-mode",
            lyrics_mode,
            "--language",
            language,
            "--device",
            device,
            "--output-root",
            str(output_root),
            "--report",
            str(report_path),
        ]
        if overwrite:
            command.append("--overwrite")

        completed = _run_process_with_live_log(command, append_from_worker)

        if not report_path.is_file():
            if completed.returncode != 0:
                raise RuntimeError(
                    completed.stdout.strip() or "Split worker failed without a JSON report."
                )
            raise RuntimeError("Split worker did not write a JSON report.")

        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("status") != "ok":
            raise RuntimeError(str(report.get("error") or "split worker failed"))

        return {
            "report_path": report_path,
            "output_folder": _split_track_output_root(file_path),
            "report": report,
        }

    try:
        state.current_stage = "Running split workflow"
        state.progress = 0.12
        _refresh_status()
        outcome = await asyncio.to_thread(worker)
        state.progress = 1.0
        state.current_stage = "Complete"

        report = outcome["report"] if isinstance(outcome["report"], dict) else {}
        report_path = outcome["report_path"] if isinstance(outcome["report_path"], Path) else None
        output_folder = outcome["output_folder"] if isinstance(outcome["output_folder"], Path) else None
        outputs = report.get("outputs") if isinstance(report.get("outputs"), dict) else {}
        warnings = report.get("warnings") if isinstance(report.get("warnings"), list) else []
        output_names = ", ".join(sorted(str(name) for name in outputs.keys()))

        _set_last_report_path(
            "split",
            report_path if report_path is not None and report_path.exists() else None,
        )
        state.last_folder_path = output_folder if output_folder is not None else _split_track_output_root(file_path)
        state.last_result = f"Split stems completed for {file_path.name}."
        if output_names:
            state.last_result += f" Outputs: {output_names}."
        if report_path is not None and report_path.exists():
            state.last_result += f" Report written to {report_path}."
        if warnings:
            state.last_result += f" Warnings: {len(warnings)}."
        _append_split_report_details_to_log(report_path, report)
    except Exception as exc:
        state.progress = 0.0
        state.current_stage = "Failed"
        state.last_result = f"Split stems failed: {type(exc).__name__}: {exc}"
        _append_log(traceback.format_exc().rstrip())
    finally:
        state.running = False
        state.cancel_requested = False
        state.can_cancel = False
        _refresh_status()


async def _run_metadata_job(
    folder: Path,
    lyrics_mode: str,
    *,
    refresh_cache: bool,
    use_cache: bool,
    cache_db_path: Path | None,
) -> None:
    if state.running:
        ui.notify("A job is already running.", color="warning")
        return

    loop = asyncio.get_running_loop()
    state.running = True
    state.cancel_requested = False
    state.can_cancel = False
    state.progress = 0.05
    state.current_stage = "Preparing metadata workflow"
    state.last_result = "Metadata tagging & album grouping in progress."
    _set_log(f"Metadata tagging & album grouping started for: {folder}")
    _refresh_status()

    def append_from_worker(message: str) -> None:
        loop.call_soon_threadsafe(_append_metadata_log_and_update, message)

    def worker() -> dict[str, object]:
        from metadata_tagging_and_cluster_grouping.output_planner import DEFAULT_OUTPUT_ROOT
        from metadata_tagging_and_cluster_grouping.group_worker import __file__ as group_worker_path

        python_command = resolve_metadata_python_command()
        if python_command is None:
            raise RuntimeError(
                "Metadata tagging & album grouping requires a Python runtime with the "
                "main workflow dependencies installed. Missing representative modules: "
                "mutagen, tqdm, requests, beautifulsoup4, python-dotenv."
            )

        with tempfile.TemporaryDirectory(prefix="audio-analyzer-group-") as temp_dir:
            report_path = Path(temp_dir) / "group_report.json"
            command = [
                *python_command,
                "-u",
                str(group_worker_path),
                "--folder",
                str(folder),
                "--lyrics-mode",
                lyrics_mode,
                "--report",
                str(report_path),
            ]
            if refresh_cache:
                command.append("--refresh-cache")
            if not use_cache:
                command.append("--no-cache")
            if cache_db_path is not None:
                command.extend(["--cache-db", str(cache_db_path)])
            completed = _run_process_with_live_log(command, append_from_worker)

            if not report_path.is_file():
                if completed.returncode != 0:
                    raise RuntimeError(
                        completed.stdout.strip()
                        or "Metadata tagging & album grouping worker failed without a JSON report."
                    )
                raise RuntimeError("Metadata tagging & album grouping worker did not write a JSON report.")

            report = json.loads(report_path.read_text(encoding="utf-8"))
            if report.get("status") != "ok":
                raise RuntimeError(str(report.get("error") or "metadata worker failed"))

            return {
                "result": report.get("result") if isinstance(report.get("result"), dict) else {},
                "output_folder": folder / DEFAULT_OUTPUT_ROOT,
                "python_command": python_command,
            }

    try:
        state.current_stage = "Running metadata tagging & album grouping"
        state.progress = 0.12
        _refresh_status()
        outcome = await asyncio.to_thread(worker)
        state.progress = 1.0
        state.current_stage = "Complete"

        result = outcome["result"] if isinstance(outcome["result"], dict) else {}
        output_folder = (
            outcome["output_folder"] if isinstance(outcome["output_folder"], Path) else folder / "sorted_files"
        )
        report_path = Path(result["report_path"]) if isinstance(result.get("report_path"), str) else None
        cluster_result = result.get("cluster_result") if isinstance(result.get("cluster_result"), dict) else {}
        cluster_count = cluster_result.get("cluster_count", "unknown")
        copied_count = sum(
            1
            for row in result.get("copy_results", [])
            if isinstance(row, dict) and row.get("status") == "copied"
        )

        state.last_folder_path = output_folder
        _set_last_report_path(
            "metadata",
            report_path if report_path is not None and report_path.exists() else None,
        )
        if report_path is not None and report_path.exists():
            state.last_result = (
                f"Metadata tagging & album grouping completed for {folder.name}. "
                f"Clusters: {cluster_count}. Files copied: {copied_count}. "
                f"Report written to {report_path}."
            )
        else:
            state.last_result = (
                f"Metadata tagging & album grouping completed for {folder.name}. "
                f"Clusters: {cluster_count}. Files copied: {copied_count}."
            )
    except Exception as exc:
        state.progress = 0.0
        state.current_stage = "Failed"
        state.last_result = (
            f"Metadata tagging & album grouping failed: {type(exc).__name__}: {exc}"
        )
        _append_log(traceback.format_exc().rstrip())
    finally:
        state.running = False
        state.cancel_requested = False
        state.can_cancel = False
        _refresh_status()


async def _run_duplicates_job(
    folder: Path,
    *,
    refresh_cache: bool,
    use_cache: bool,
    cache_db_path: Path | None,
    write_report: bool,
    output_path: Path | None,
    cleanup: bool,
) -> None:
    if state.running:
        ui.notify("A job is already running.", color="warning")
        return

    loop = asyncio.get_running_loop()
    state.running = True
    state.cancel_requested = False
    state.can_cancel = False
    state.progress = 0.05
    state.current_stage = "Preparing duplicate detection"
    state.last_result = "Duplicate detection in progress."
    _set_log(f"Duplicate detection started for: {folder}")
    _refresh_status()

    def append_from_worker(message: str) -> None:
        loop.call_soon_threadsafe(_append_duplicates_log_and_update, message)

    def worker() -> dict[str, object]:
        python_command = resolve_duplicate_python_command(cleanup=cleanup)
        if python_command is None:
            if cleanup:
                raise RuntimeError(
                    "Duplicate cleanup requires a Python runtime with the main workflow "
                    "dependencies and send2trash installed. Missing representative modules: "
                    "mutagen, tqdm, requests, beautifulsoup4, python-dotenv, send2trash."
                )
            raise RuntimeError(
                "Duplicate detection requires a Python runtime with the main workflow "
                "dependencies installed. Missing representative modules: "
                "mutagen, tqdm, requests, beautifulsoup4, python-dotenv."
            )

        with tempfile.TemporaryDirectory(prefix="audio-analyzer-duplicates-") as temp_dir:
            report_path = Path(temp_dir) / "duplicate_report.json"
            duplicate_worker_path = SRC_ROOT / "caching_and_duplicate_detection" / "duplicate_worker.py"
            command = [
                *python_command,
                "-u",
                str(duplicate_worker_path),
                "--folder",
                str(folder),
                "--report",
                str(report_path),
            ]
            if refresh_cache:
                command.append("--refresh-cache")
            if not use_cache:
                command.append("--no-cache")
            if cache_db_path is not None:
                command.extend(["--cache-db", str(cache_db_path)])
            if not write_report:
                command.append("--no-report")
            elif output_path is not None:
                command.extend(["--output", str(output_path)])
            if cleanup:
                command.extend(["--cleanup", "--cleanup-confirm"])

            completed = _run_process_with_live_log(command, append_from_worker)

            if not report_path.is_file():
                if completed.returncode != 0:
                    raise RuntimeError(
                        completed.stdout.strip()
                        or "Duplicate detection worker failed without a JSON report."
                    )
                raise RuntimeError("Duplicate detection worker did not write a JSON report.")

            report = json.loads(report_path.read_text(encoding="utf-8"))
            if report.get("status") != "ok":
                raise RuntimeError(str(report.get("error") or "duplicate detection failed"))

            return report.get("result") if isinstance(report.get("result"), dict) else {}

    try:
        state.current_stage = "Running duplicate detection"
        state.progress = 0.12
        _refresh_status()
        result = await asyncio.to_thread(worker)
        state.progress = 1.0
        state.current_stage = "Complete"

        report_path = Path(result["report_path"]) if isinstance(result.get("report_path"), str) else None
        cleanup_result = result.get("cleanup_result") if isinstance(result.get("cleanup_result"), dict) else {}
        cleanup_manifest_path = (
            Path(cleanup_result["manifest_path"])
            if isinstance(cleanup_result.get("manifest_path"), str)
            else None
        )
        tier_counts = result.get("tier_counts") if isinstance(result.get("tier_counts"), dict) else {}
        group_count = result.get("group_count", "unknown")
        tier_summary = ", ".join(
            f"{key}: {value}"
            for key, value in sorted(tier_counts.items())
            if isinstance(key, str)
        )

        _set_last_report_path(
            "duplicates",
            (
                report_path
                if report_path is not None and report_path.exists()
                else cleanup_manifest_path
                if cleanup_manifest_path is not None and cleanup_manifest_path.exists()
                else None
            ),
        )
        state.last_folder_path = folder
        state.last_result = (
            f"Duplicate detection completed for {folder.name}. Groups found: {group_count}."
        )
        if tier_summary:
            state.last_result += f" Tiers: {tier_summary}."
        if report_path is not None and report_path.exists():
            state.last_result += f" CSV report written to {report_path}."
        elif not write_report:
            state.last_result += " CSV report skipped."
        if cleanup_manifest_path is not None and cleanup_manifest_path.exists():
            moved = cleanup_result.get("moved_successfully_count", 0)
            failed = cleanup_result.get("failed_count", 0)
            cancelled = cleanup_result.get("cancelled")
            state.last_result += (
                f" Cleanup manifest written to {cleanup_manifest_path}."
                f" Moved: {moved}. Failed: {failed}."
            )
            if cancelled:
                reason = cleanup_result.get("cancellation_reason")
                if isinstance(reason, str) and reason:
                    state.last_result += f" Cleanup cancelled: {reason}"
                else:
                    state.last_result += " Cleanup cancelled."
    except Exception as exc:
        state.progress = 0.0
        state.current_stage = "Failed"
        state.last_result = f"Duplicate detection failed: {type(exc).__name__}: {exc}"
        _append_log(traceback.format_exc().rstrip())
    finally:
        state.running = False
        state.cancel_requested = False
        state.can_cancel = False
        _refresh_status()


def _append_metadata_log_and_update(message: str) -> None:
    _append_log(message)
    stage_update = _metadata_stage_from_log_line(message)
    if stage_update is None:
        return
    stage, progress = stage_update
    state.current_stage = stage
    state.progress = max(state.progress, progress)
    _refresh_status()


def _append_duplicates_log_and_update(message: str) -> None:
    _append_log(message)
    stage_update = _duplicates_stage_from_log_line(message)
    if stage_update is None:
        return
    stage, progress = stage_update
    state.current_stage = stage
    state.progress = max(state.progress, progress)
    _refresh_status()


def _append_split_log_and_update(message: str) -> None:
    progress_event = _parse_split_progress_event(message)
    if progress_event is not None:
        stage = str(progress_event.get("message") or "Processing")
        completed = progress_event.get("completed")
        total = progress_event.get("total")
        state.current_stage = stage
        if isinstance(completed, int) and isinstance(total, int) and total > 0:
            state.progress = max(state.progress, min(completed / total, 1.0))
        _refresh_status()
        return

    _append_log(message)


def _append_split_report_details_to_log(report_path: Path | None, report: dict[str, object]) -> None:
    if report_path is not None:
        _append_log(f"Report file: {report_path}")

    outputs = report.get("outputs")
    if isinstance(outputs, dict) and outputs:
        _append_log("Outputs:")
        for output_name, output_path in outputs.items():
            _append_log(f"    {output_name}: {output_path}")

    warnings = report.get("warnings")
    if isinstance(warnings, list):
        for warning in warnings:
            if isinstance(warning, str) and warning:
                _append_log(f"Warning: {warning}")


def _run_process_with_live_log(
    command: list[str],
    append_callback,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    stdout_lines: list[str] = []
    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.rstrip("\r\n")
        if not line:
            continue
        stdout_lines.append(line)
        if not _is_gui_log_noise(line):
            append_callback(line)

    returncode = process.wait()
    completed = subprocess.CompletedProcess(
        args=command,
        returncode=returncode,
        stdout="\n".join(stdout_lines),
        stderr="",
    )
    return completed


async def _run_analyse_job(
    path: Path,
    *,
    refresh_cache: bool,
    use_cache: bool,
    cache_db_path: Path | None,
) -> None:
    if state.running:
        ui.notify("A job is already running.", color="warning")
        return

    loop = asyncio.get_running_loop()
    state.running = True
    state.cancel_requested = False
    state.can_cancel = False
    state.progress = 0.05
    state.current_stage = "Discovering files" if path.is_dir() else "Loading audio file"
    state.last_result = "Analyse in progress."
    _set_log(f"Analyse started for: {path}")
    _refresh_status()

    def append_from_worker(message: str) -> None:
        loop.call_soon_threadsafe(_append_analysis_log_and_update, message)

    def worker() -> dict[str, object]:
        python_command = resolve_analysis_python_command()
        if python_command is None:
            raise RuntimeError(
                "Audio analysis requires a Python runtime with the analysis "
                "dependencies installed. Missing representative modules: "
                "soundfile, numpy, scipy, tqdm, python-dotenv."
            )

        with tempfile.TemporaryDirectory(prefix="audio-analyzer-analyse-") as temp_dir:
            report_path = Path(temp_dir) / "analyse_report.json"
            analyse_worker_path = SRC_ROOT / "audio_analysis" / "analyse_worker.py"
            command = [
                *python_command,
                "-u",
                str(analyse_worker_path),
                "--path",
                str(path),
                "--report",
                str(report_path),
            ]
            if refresh_cache:
                command.append("--refresh-cache")
            if not use_cache:
                command.append("--no-cache")
            if cache_db_path is not None:
                command.extend(["--cache-db", str(cache_db_path)])
            completed = _run_process_with_live_log(command, append_from_worker)

            if not report_path.is_file():
                if completed.returncode != 0:
                    raise RuntimeError(
                        completed.stdout.strip()
                        or "Analyse worker failed without a JSON report."
                    )
                raise RuntimeError("Analyse worker did not write a JSON report.")

            report = json.loads(report_path.read_text(encoding="utf-8"))
            if report.get("status") != "ok":
                raise RuntimeError(str(report.get("error") or "analysis worker failed"))

            return report.get("result") if isinstance(report.get("result"), dict) else {}

    try:
        state.current_stage = "Running analysis"
        state.progress = 0.14
        _refresh_status()
        outcome = await asyncio.to_thread(worker)
        state.progress = 1.0
        state.current_stage = "Complete"

        mode = outcome.get("mode")
        if mode == "folder":
            csv_path = Path(outcome["csv_path"]) if isinstance(outcome.get("csv_path"), str) else None
            report_path = csv_path if csv_path is not None and csv_path.exists() else None
            _set_last_report_path("analyse", report_path)
            folder_path = Path(outcome["folder_path"]) if isinstance(outcome.get("folder_path"), str) else path
            state.last_folder_path = folder_path
            if report_path is not None:
                state.last_result = (
                    f"Analyse completed for folder: {path}. "
                    f"CSV report written to {report_path}."
                )
            else:
                state.last_result = (
                    f"Analyse completed for folder: {path}. "
                    "No CSV report was written."
                )
        else:
            result = outcome.get("result") if isinstance(outcome.get("result"), dict) else {}
            status = result.get("status", "Unknown")
            confidence = result.get("confidence")
            spectrogram_path = (
                Path(outcome["spectrogram_path"])
                if isinstance(outcome.get("spectrogram_path"), str) and outcome.get("spectrogram_path")
                else None
            )
            spectrogram_error = outcome.get("spectrogram_error")
            confidence_text = ""
            if isinstance(confidence, (int, float)):
                confidence_text = f" ({float(confidence) * 100:.1f}% confidence)"
            _set_last_report_path("analyse", spectrogram_path)
            state.last_result = f"Analyse completed for file: {path.name}. Result: {status}{confidence_text}."
            if spectrogram_path is not None:
                state.last_result += f" Spectrogram written to {spectrogram_path}."
            elif _is_upscaled_status(str(status)) and isinstance(spectrogram_error, str) and spectrogram_error:
                state.last_result += f" {spectrogram_error}"
            folder_path = Path(outcome["folder_path"]) if isinstance(outcome.get("folder_path"), str) else path.parent
            state.last_folder_path = folder_path
    except Exception as exc:
        state.progress = 0.0
        state.current_stage = "Failed"
        state.last_result = f"Analyse failed: {type(exc).__name__}: {exc}"
        _append_log(traceback.format_exc().rstrip())
    finally:
        state.running = False
        state.cancel_requested = False
        state.can_cancel = False
        _refresh_status()


def _request_cancel() -> None:
    if state.running:
        if not state.can_cancel:
            ui.notify("Cancellation is not available for the current workflow.", color="warning")
            return
        state.cancel_requested = True
        _append_log("[cancel] Cancellation requested.")
        _refresh_status()


def _refresh_status() -> None:
    if status_chip is not None:
        status_chip.text = "Localhost - Running" if state.running else "Localhost - Idle"
        status_chip.style(f"border-color: {ACCENT if state.running else BORDER};")
    if stage_label is not None:
        stage_label.text = state.current_stage
    if progress_bar is not None:
        progress_bar.value = state.progress
        progress_bar.style(f"color: {ACCENT};")
    if summary_label is not None:
        summary_label.text = state.last_result
    if cancel_button is not None:
        if state.running and state.can_cancel:
            cancel_button.props(remove="disable")
        else:
            cancel_button.props("disable")
    if open_report_button is not None:
        if _get_last_report_path() is not None and not state.running:
            open_report_button.props(remove="disable")
        else:
            open_report_button.props("disable")
    if open_folder_button is not None:
        if state.last_folder_path is not None and not state.running:
            open_folder_button.props(remove="disable")
        else:
            open_folder_button.props("disable")


def _set_log(message: str) -> None:
    if log_area is not None:
        log_area.text = message


def _append_log(message: str) -> None:
    if log_area is None:
        return
    existing = log_area.text or ""
    log_area.text = f"{existing}\n{message}" if existing else message


def _validated_path(path_value: str | None, missing_message: str) -> Path | None:
    if not path_value or not path_value.strip():
        ui.notify(missing_message, color="warning")
        return None
    path = Path(path_value.strip('" '))
    if not path.exists():
        ui.notify("Selected path does not exist.", color="warning")
        return None
    return path


def _validated_folder(path_value: str | None, missing_message: str) -> Path | None:
    path = _validated_path(path_value, missing_message)
    if path is None:
        return None
    if not path.is_dir():
        ui.notify("Choose a folder for this workflow.", color="warning")
        return None
    return path


def _require_confirmation(confirmed: bool) -> bool:
    if confirmed:
        return True
    ui.notify("Confirm the checkbox before starting.", color="warning")
    return False


def _set_last_report_path(operation: str, path: Path | None) -> None:
    if path is None:
        state.last_report_paths.pop(operation, None)
        return
    state.last_report_paths[operation] = path


def _get_last_report_path(operation: str | None = None) -> Path | None:
    return state.last_report_paths.get(operation or state.operation)


def _selected_operation_label() -> str:
    operation = OPERATIONS.get(state.operation, {})
    label = operation.get("label")
    return str(label) if isinstance(label, str) else "selected workflow"


def _is_upscaled_status(status: str) -> bool:
    return status.startswith("Likely UPSCALED")


def _open_last_report() -> None:
    report_path = _get_last_report_path()
    if report_path is None:
        ui.notify(f"No report is available for the last {_selected_operation_label()} task.", color="warning")
        return
    _open_path(report_path)


def _open_last_folder() -> None:
    if state.last_folder_path is None:
        ui.notify("No output folder is available for the last task.", color="warning")
        return
    _open_path(state.last_folder_path)


def _open_path(path: Path) -> None:
    if not path.exists():
        ui.notify(f"Path does not exist yet: {path}", color="warning")
        return
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    if sys_platform() == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def sys_platform() -> str:
    import sys

    return sys.platform


if __name__ == "__main__":
    run()
