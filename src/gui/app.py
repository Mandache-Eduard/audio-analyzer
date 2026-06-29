from __future__ import annotations

import asyncio
import os
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

from nicegui import app, ui


ACCENT = "#FF7F00"
BACKGROUND = "#191A1C"
PANEL = "#202124"
PANEL_ALT = "#242529"
BORDER = "#333438"
TEXT = "#BCBEC4"
MUTED = "#8B8E96"
SUCCESS = "#6FAE7A"
ERROR = "#B96B6B"
WARNING = "#D8A24B"

AUDIO_EXTENSIONS = {".flac", ".mp3", ".m4a", ".ogg", ".opus", ".wav", ".aiff", ".wma"}


@dataclass
class JobState:
    operation: str = "analyse"
    running: bool = False
    cancel_requested: bool = False
    current_stage: str = "Idle"
    progress: float = 0.0
    last_result: str = "No task has run yet."
    last_csv_path: Path | None = None
    last_folder_path: Path | None = None


state = JobState()
settings_panel = None
status_chip = None
stage_label = None
progress_bar = None
log_area = None
summary_label = None
open_csv_button = None
open_folder_button = None
cancel_button = None
operation_buttons: dict[str, ui.button] = {}


OPERATIONS = {
    "analyse": {
        "label": "Analyse audio",
        "description": "Analyse audio for possible upscaling",
        "icon": "analytics",
    },
    "metadata": {
        "label": "Metadata + Albums",
        "description": "Add metadata and group into albums",
        "icon": "album",
    },
    "split": {
        "label": "Split stems",
        "description": "Split audio files into stems",
        "icon": "graphic_eq",
    },
}


def run() -> None:
    _configure_app()
    _build_layout()
    show_browser = os.environ.get("AUDIO_ANALYZER_GUI_SHOW", "1") != "0"
    host = os.environ.get("AUDIO_ANALYZER_GUI_HOST", "127.0.0.1")
    preferred_port = int(os.environ.get("AUDIO_ANALYZER_GUI_PORT", "8080"))
    port = _available_port(host, preferred_port)
    if port != preferred_port:
        print(f"Port {preferred_port} is already in use; starting GUI on port {port}.")
    ui.run(
        title="Audio Analyzer",
        host=host,
        port=port,
        reload=False,
        show=show_browser,
        dark=True,
    )


def _available_port(host: str, preferred_port: int) -> int:
    for port in range(preferred_port, preferred_port + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return port
    raise RuntimeError(
        f"No available GUI port found from {preferred_port} to {preferred_port + 19}."
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
    global log_area, summary_label, open_csv_button, open_folder_button, cancel_button

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
                ui.label("Demo UI only. Workflow execution is simulated until the backend is wired to GUI progress callbacks.").classes("hint")

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
                        open_csv_button = ui.button("Open CSV", icon="table_view", on_click=_open_last_csv).classes("secondary-action")
                        open_folder_button = ui.button("Open Folder", icon="folder_open", on_click=_open_last_folder).classes("secondary-action")
                        open_csv_button.props("outline disable")
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
            if state.operation == "analyse":
                _render_analyse_settings()
            elif state.operation == "metadata":
                _render_metadata_settings()
            else:
                _render_split_settings()


def _render_analyse_settings() -> None:
    path_input = _path_input("File or folder path", "Select a FLAC file or a folder containing FLAC files")
    _browse_button(path_input, mode="any")

    with ui.row().classes("w-full items-center gap-3"):
        overwrite_confirm = ui.checkbox("I understand this is a demo run").classes("field-label")
        ui.space()
        ui.button(
            "Start",
            icon="play_arrow",
            on_click=lambda: _start_analyse_demo(path_input.value, overwrite_confirm.value),
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
    dry_run = ui.checkbox("Dry run").classes("field-label")
    overwrite_confirm = ui.checkbox("I understand this workflow may create copied output files").classes("field-label")

    with ui.row().classes("w-full items-center gap-3"):
        ui.space()
        ui.button(
            "Start",
            icon="play_arrow",
            on_click=lambda: _start_metadata_demo(
                folder_input.value,
                lyrics_mode.value,
                dry_run.value,
                overwrite_confirm.value,
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

    ui.label("Models").classes("field-label")
    vocal_model = _model_select("Vocals model", ["Kim Vocal 2"], "Kim Vocal 2")
    instrumental_model = _model_select("Instrumental model", ["MDX23C InstVoc HQ"], "MDX23C InstVoc HQ")
    bass_drums_model = _model_select("Bass / drums model", ["htdemucs_ft"], "htdemucs_ft")
    lyrics_model = _model_select(
        "Lyrics model",
        ["ggml-large-v2", "ggml-large-v3", "ggml-large-v3-turbo"],
        "ggml-large-v2",
    )
    overwrite = ui.checkbox("Overwrite existing split outputs").classes("field-label")
    overwrite_confirm = ui.checkbox("I understand overwrite replaces existing copied output files").classes("field-label")

    with ui.row().classes("w-full items-center gap-3"):
        ui.space()
        ui.button(
            "Start",
            icon="play_arrow",
            on_click=lambda: _start_split_demo(
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
                {
                    "vocals": vocal_model.value,
                    "instrumental": instrumental_model.value,
                    "bass_drums": bass_drums_model.value,
                    "lyrics": lyrics_model.value,
                },
                overwrite.value,
                overwrite_confirm.value,
            ),
        ).classes("primary-action")


def _path_input(label: str, placeholder: str) -> ui.input:
    ui.label(label).classes("field-label")
    path_input = ui.input(placeholder=placeholder).classes("w-full")
    path_input.props("outlined dense")
    return path_input


def _browse_button(input_element: ui.input, *, mode: str) -> None:
    async def choose_path() -> None:
        selected = await asyncio.to_thread(_open_native_path_dialog, mode, input_element.value)
        if selected:
            input_element.value = selected

    ui.button("Browse", icon="folder_open", on_click=choose_path).classes("secondary-action").props("outline")


def _open_native_path_dialog(mode: str, start_path: str | None) -> str | None:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox
    except ImportError:
        return None

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    initial_dir = _dialog_initial_directory(start_path)

    try:
        selected_mode = mode
        if mode == "any":
            wants_file = messagebox.askyesnocancel(
                "Select path type",
                "Select a file?\n\nChoose No to select a folder.",
                parent=root,
            )
            if wants_file is None:
                return None
            selected_mode = "file" if wants_file else "folder"

        if selected_mode == "folder":
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


def _model_select(label: str, options: list[str], value: str) -> ui.select:
    model_select = ui.select(options, value=value, label=label).classes("w-full")
    model_select.props("outlined dense")
    return model_select


def _start_analyse_demo(path_value: str | None, confirmed: bool) -> None:
    path = _validated_path(path_value, "Choose a FLAC file or folder before starting.")
    if path is None or not _require_confirmation(confirmed):
        return
    if path.is_file() and path.suffix.lower() != ".flac":
        ui.notify("Analyse currently expects a FLAC file or a folder.", color="warning")
        return

    stages = [
        "Discovering files" if path.is_dir() else "Loading audio file",
        "Analyzing frames",
        "Writing CSV report",
        "Generating spectrograms for flagged files",
    ]
    state.last_csv_path = (path if path.is_dir() else path.parent) / "analysis-results-demo.csv"
    state.last_folder_path = path if path.is_dir() else path.parent
    _launch_demo_job(
        title="Analyse",
        stages=stages,
        summary="Demo analyse workflow completed. Real execution will call analyse_single_file or analyse_folder_batch directly.",
    )


def _start_metadata_demo(
    folder_value: str | None,
    lyrics_mode: str,
    dry_run: bool,
    confirmed: bool,
) -> None:
    folder = _validated_folder(folder_value, "Choose a folder before starting metadata grouping.")
    if folder is None or not _require_confirmation(confirmed):
        return
    stages = [
        "File discovery",
        "Reading metadata identifiers",
        "Fingerprinting unmatched files",
        "Resolving identifiers",
        "Selecting releases",
        "Planning copied output files",
        "Writing report" if dry_run else "Copying and tagging files",
        f"Lyrics mode: {lyrics_mode}",
    ]
    state.last_csv_path = None
    state.last_folder_path = folder / "sorted_files"
    dry_run_text = "dry run" if dry_run else "normal run"
    _launch_demo_job(
        title="Metadata + Albums",
        stages=stages,
        summary=f"Demo metadata workflow completed as a {dry_run_text}. Warnings would be logged and processing would continue.",
    )


def _start_split_demo(
    file_value: str | None,
    outputs: dict[str, bool],
    device: str,
    lyrics_mode: str,
    language: str,
    models: dict[str, str],
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

    stages = [
        f"Selected device: {device}",
        f"Selected outputs: {', '.join(requested_outputs)}",
        "Separating vocals" if "vocals" in requested_outputs or "lyrics" in requested_outputs else None,
        "Separating instrumental" if "instrumental" in requested_outputs else None,
        "Separating bass and drums" if "bass" in requested_outputs or "drums" in requested_outputs else None,
        f"Transcribing lyrics ({lyrics_mode}, {language or 'auto'})" if "lyrics" in requested_outputs else None,
        "Writing output report",
    ]
    model_lines = ", ".join(f"{key}: {value}" for key, value in models.items())
    state.last_csv_path = None
    state.last_folder_path = file_path.parent / "split_files" / file_path.stem
    _launch_demo_job(
        title="Split stems",
        stages=[stage for stage in stages if stage],
        summary=f"Demo split workflow completed. Selected models: {model_lines}.",
    )


def _launch_demo_job(*, title: str, stages: list[str], summary: str) -> None:
    if state.running:
        ui.notify("A job is already running.", color="warning")
        return
    asyncio.create_task(_run_demo_job(title=title, stages=stages, summary=summary))


async def _run_demo_job(*, title: str, stages: list[str], summary: str) -> None:
    state.running = True
    state.cancel_requested = False
    state.progress = 0.0
    state.current_stage = stages[0] if stages else "Processing"
    _set_log(f"{title} demo started.")
    _refresh_status()

    try:
        total_ticks = max(len(stages) * 8, 1)
        completed_ticks = 0
        for stage in stages:
            state.current_stage = stage
            _append_log(f"[stage] {stage}")
            _refresh_status()
            for _ in range(8):
                if state.cancel_requested:
                    state.last_result = f"{title} demo cancelled during: {stage}"
                    _append_log("[cancelled] Job cancelled by user.")
                    return
                completed_ticks += 1
                state.progress = min(completed_ticks / total_ticks, 1.0)
                _refresh_status()
                await asyncio.sleep(0.12)
            _append_log(f"[done] {stage}")

        state.progress = 1.0
        state.current_stage = "Complete"
        state.last_result = summary
        _append_log("[complete] Demo job finished.")
    finally:
        state.running = False
        state.cancel_requested = False
        _refresh_status()


def _request_cancel() -> None:
    if state.running:
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
        if state.running:
            cancel_button.props(remove="disable")
        else:
            cancel_button.props("disable")
    if open_csv_button is not None:
        if state.last_csv_path is not None and not state.running:
            open_csv_button.props(remove="disable")
        else:
            open_csv_button.props("disable")
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


def _open_last_csv() -> None:
    if state.last_csv_path is None:
        ui.notify("No CSV path is available for the last task.", color="warning")
        return
    _open_path(state.last_csv_path)


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


def _windows_drives() -> list[Path]:
    return [Path(f"{letter}:\\") for letter in ascii_uppercase if Path(f"{letter}:\\").exists()]
