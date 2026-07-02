# Copyright (C) 2026 <Your Name>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License (GPL-3.0-only).
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see https://www.gnu.org/licenses/.


import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import webbrowser
from textwrap import dedent
from typing import TYPE_CHECKING

from gui.port_management import (
    DEFAULT_GUI_HOST,
    DEFAULT_GUI_PORT,
    GUI_PORT_LOCKED_ENV,
    GUI_PORT_SEARCH_RANGE,
    get_gui_base_url,
    resolve_gui_port,
)
from workflow_runtime import python_can_run_analysis, resolve_analysis_python_command

if TYPE_CHECKING:
    from caching_and_duplicate_detection.audio_cache import AudioCache

LYRICS_MODE_UNSYNCED = "lyrics-unsynced"
LYRICS_MODE_SYNCED = "lyrics-synced"
LYRICS_MODE_NONE = "lyrics-none"
SPLIT_LYRICS_MODE_NONE = "none"
SPLIT_LYRICS_MODE_PLAIN = "plain"
SPLIT_LYRICS_MODE_TIMESTAMPED = "timestamped"
SUPPORTED_LYRICS_MODES = frozenset(
    {
        LYRICS_MODE_UNSYNCED,
        LYRICS_MODE_SYNCED,
        LYRICS_MODE_NONE,
    }
)
SUPPORTED_SPLIT_LYRICS_MODES = frozenset(
    {
        SPLIT_LYRICS_MODE_NONE,
        SPLIT_LYRICS_MODE_PLAIN,
        SPLIT_LYRICS_MODE_TIMESTAMPED,
    }
)


def print_help() -> None:
    print(
        dedent(
            f"""
            Audio Workflow CLI
            ==================

            Usage:
              py main.py <command> [options]

            Commands:
              analyse     Analyse one audio file or all audio files inside a folder for upscaling.
              group       Group analysed tracks from a folder.
              duplicates  Scan a folder for exact and perceptual duplicate candidates.
              split       Run audio ML processing, such as stem separation and lyrics extraction.
              gui         Open the local browser GUI.
              help        Show this help menu.

            ------------------------------------------------------------
            ANALYSE
            ------------------------------------------------------------

            Description:
              Reads an audio file or folder and attempts to detect if said file or folder is upscaled.

            Usage:
              py main.py analyse "<file-or-folder-path>"

            Examples:
              py main.py analyse "X:\\path\\to\\file.flac"
              py main.py analyse "X:\\path\\to\\folder"

            ------------------------------------------------------------
            GROUP
            ------------------------------------------------------------

            Description:
              Groups tracks from a folder into albums while also enriching the audio files inside with metadata. Optionally, based on lyric availability, attempts to write lyrics inside audio files.

            Usage:
              py main.py group [lyrics-mode] "<folder-path>"

            Lyrics modes:
              lyrics-none       Group tracks without inserting lyrics.
              lyrics-unsynced   Group tracks with plain/unsynced lyrics.
              lyrics-synced     Group tracks with synced lyrics.

            Examples:
              py main.py group "X:\\path\\to\\folder"
              py main.py group lyrics-none "X:\\path\\to\\folder"
              py main.py group lyrics-unsynced "X:\\path\\to\\folder"
              py main.py group lyrics-synced "X:\\path\\to\\folder"

            ------------------------------------------------------------
            DUPLICATES
            ------------------------------------------------------------

            Description:
              Scans a folder for exact binary duplicates and cross-format duplicate candidates using fingerprints, AcoustID and MusicBrainz identifiers.

            Usage:
              py main.py duplicates [options] "<folder-path>"

            Options:
              --refresh-cache
                  Ignore cached fingerprint, analysis and metadata rows and recompute them.

              --no-cache
                  Disable the persistent SQLite cache for this run.

              --cache-db <path>
                  Use a custom SQLite database path.

              --output <path>
                  Save the duplicate report to CSV.

              --cleanup
                  After the duplicate scan, build a cleanup plan and require manual confirmation
                  before moving eligible duplicates to the Recycle Bin.

            Examples:
              py main.py duplicates "X:\\path\\to\\folder"
              py main.py duplicates "X:\\path\\to\\folder" --refresh-cache
              py main.py duplicates "X:\\path\\to\\folder" --output "duplicates_report.csv"
              py main.py duplicates "X:\\path\\to\\folder" --cleanup

            ------------------------------------------------------------
            SPLIT
            ------------------------------------------------------------

            Description:
              Runs machine learning-based audio processing.

              This can be used for:
                - Vocal separation
                - Bass separation
                - Drum separation
                - Instrumental separation
                - Lyrics extraction, if supported by your workflow

            Usage:
              py main.py split [options] "<file-path>"

            Options:
              --outputs <items>
                  Comma-separated list of outputs to generate.

                  Example:
                    --outputs vocals,bass,drums,instrumental

              --lyrics-mode <mode>
                  Controls how lyrics should be handled.

                  Supported modes:
                    {", ".join(sorted(SUPPORTED_SPLIT_LYRICS_MODES))}

              --device <mode>
                  Processing device: auto, cpu, cuda

              --language <code>
                  Transcription language for lyrics, or auto.
                  Examples: en, ro, auto

              --overwrite
                  Replace existing output files.

            Examples:
              py main.py split --outputs vocals,bass,drums,lyrics --lyrics-mode plain --language en "X:\\path\\to\\file.mp3"
              py main.py split --outputs lyrics --lyrics-mode timestamped --language ro "X:\\path\\to\\file.flac"
              py main.py split --outputs vocals,instrumental "X:\\path\\to\\file.flac"

            Output:
              Audio ML results are saved in the configured audio workflow output directory.

            ------------------------------------------------------------
            NOTES
            ------------------------------------------------------------

            Paths containing spaces must be wrapped in quotes.

            Valid:
              py main.py analyse "X:\\Music Folder\\song.flac"

            Invalid:
              py main.py analyse X:\\Music Folder\\song.flac
            """
        ).strip()
    )


def launch_gui() -> None:
    host = os.environ.get("AUDIO_ANALYZER_GUI_HOST", DEFAULT_GUI_HOST)
    preferred_port = int(os.environ.get("AUDIO_ANALYZER_GUI_PORT", str(DEFAULT_GUI_PORT)))
    port_resolution = resolve_gui_port(host, preferred_port, GUI_PORT_SEARCH_RANGE)

    if port_resolution.running_port is not None:
        url = get_gui_base_url(host, port_resolution.running_port)
        print(f"GUI already running at {url}")
        webbrowser.open_new_tab(url)
        return

    os.environ["AUDIO_ANALYZER_GUI_HOST"] = host
    os.environ["AUDIO_ANALYZER_GUI_PORT"] = str(port_resolution.selected_port)
    os.environ[GUI_PORT_LOCKED_ENV] = "1"

    if _relaunch_gui_with_preferred_python():
        return

    _prepare_gui_runtime_environment(os.environ)

    try:
        from gui.app import run
    except ModuleNotFoundError as exc:
        raise

    run()


def _relaunch_gui_with_preferred_python() -> bool:
    if os.environ.get("AUDIO_ANALYZER_GUI_REEXEC") == "1":
        return False

    gui_python_command = _resolve_gui_python_command()
    if gui_python_command is None:
        if sysconfig.get_config_var("Py_GIL_DISABLED") == 1:
            print(
                "GUI requires a non-free-threaded Python interpreter with NiceGUI installed. "
                "Install GUI dependencies in a stable interpreter, for example: "
                ".venv-demucs-3.11\\Scripts\\python.exe -m pip install -r requirements-gui.txt"
            )
        return False

    if _current_python_matches_command(gui_python_command):
        _prepare_gui_runtime_environment(os.environ, gui_python_command)
        return False

    env = os.environ.copy()
    env["AUDIO_ANALYZER_GUI_REEXEC"] = "1"
    _prepare_gui_runtime_environment(env, gui_python_command)
    print(f"Launching GUI with {gui_python_command[0]}")
    gui_entrypoint = Path(__file__).resolve().parent / "gui" / "app.py"
    completed = subprocess.run(
        [*gui_python_command, str(gui_entrypoint)],
        env=env,
        cwd=str(Path(__file__).resolve().parent),
        check=False,
    )
    if completed.returncode != 0:
        print(
            "GUI launch failed under the selected stable Python interpreter. "
            "If this is a dependency issue, install GUI dependencies into that interpreter."
        )
    return True


def _prepare_gui_runtime_environment(
    env: dict[str, str],
    gui_python_command: list[str] | None = None,
) -> None:
    analysis_python_command = resolve_analysis_python_command()
    if analysis_python_command is not None and len(analysis_python_command) == 1:
        env.setdefault("AUDIO_ANALYZER_ANALYSIS_PYTHON", analysis_python_command[0])
        return

    if (
        gui_python_command is not None
        and len(gui_python_command) == 1
        and python_can_run_analysis(gui_python_command)
    ):
        env.setdefault("AUDIO_ANALYZER_ANALYSIS_PYTHON", gui_python_command[0])
        return

    if env.get("AUDIO_ANALYZER_ANALYSIS_RUNTIME_WARNED") == "1":
        return
    env["AUDIO_ANALYZER_ANALYSIS_RUNTIME_WARNED"] = "1"
    print(
        "Warning: audio analysis backend runtime was not found. The GUI can still open, "
        "but Analyse will fail until the main workflow dependencies are installed in a "
        "supported Python interpreter."
    )


def _resolve_gui_python_command() -> list[str] | None:
    explicit_python = os.environ.get("AUDIO_ANALYZER_GUI_PYTHON")
    candidate_commands: list[list[str]] = []
    if explicit_python:
        candidate_commands.append([explicit_python])

    project_root = Path(__file__).resolve().parent.parent
    candidate_interpreters = [
        project_root / ".venv-demucs-3.11" / "Scripts" / "python.exe",
        project_root / ".venv-gui" / "Scripts" / "python.exe",
        project_root / ".venv-main-3.14" / "Scripts" / "python.exe",
    ]
    for candidate_interpreter in candidate_interpreters:
        if candidate_interpreter.is_file():
            candidate_commands.append([str(candidate_interpreter)])

    candidate_commands.extend(
        [
            ["py", "-3.11"],
            ["py", "-3.12"],
            ["py", "-3.13"],
            ["py", "-3.14"],
        ]
    )

    for command in candidate_commands:
        if _python_can_host_gui(command) and python_can_run_analysis(command):
            return command

    for command in candidate_commands:
        if _python_can_host_gui(command):
            return command

    return None


def _current_python_matches_command(command: list[str]) -> bool:
    candidate = command[0]

    if os.path.isabs(candidate):
        try:
            return Path(candidate).resolve() == Path(sys.executable).resolve()
        except OSError:
            return False

    if candidate.lower() != "py":
        return False

    version_flag = command[1] if len(command) > 1 else ""
    return sys.version.startswith(version_flag.removeprefix("-"))


def _python_can_host_gui(command: list[str]) -> bool:
    probe_code = (
        "import importlib.util, sysconfig; "
        "raise SystemExit(0 if importlib.util.find_spec('nicegui') and "
        "sysconfig.get_config_var('Py_GIL_DISABLED') != 1 else 1)"
    )
    try:
        completed = subprocess.run(
            [*command, "-c", probe_code],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return False

    return completed.returncode == 0


def main():
    # 0. Set instructions and manuals
    if len(sys.argv) < 2:
        print("Wrong number of arguments - check usage using 'py main.py help'")
        return

    action = sys.argv[1].lower()

    if action == "help":
        print_help()
        return

    if action == "gui":
        launch_gui()
        return

    if len(sys.argv) < 3:
        print("Missing arguments - check usage using 'py main.py help'")
        return

    # 1. Get action and target path from command-line arguments and determine running mode
    lyrics_mode = LYRICS_MODE_NONE

    if action == "analyse":
        try:
            options, positional_args = _parse_common_cli_options(sys.argv[2:])
        except ValueError as exc:
            print(str(exc))
            return

        if len(positional_args) != 1:
            print("Analyse mode expects exactly one file or folder path.")
            return

        path = positional_args[0]
        from audio_analysis.analyse_modes import (
            analyse_folder_batch,
            analyse_single_file,
            generate_single_file_spectrogram_if_upscaled,
        )

        cache = _build_cache_from_options(options)
        if os.path.isfile(path) and path.lower().endswith(".flac"):
            result = analyse_single_file(
                path,
                want_verbose=True,
                cache=cache,
                refresh_cache=options["refresh_cache"],
            )
            generate_single_file_spectrogram_if_upscaled(
                path,
                result,
                want_verbose=True,
            )
        elif os.path.isdir(path):
            analyse_folder_batch(
                path,
                cache=cache,
                refresh_cache=options["refresh_cache"],
            )
        else:
            print("Invalid path for analyse mode or not a FLAC file.")
            return

    elif action == "group":
        try:
            options, positional_args = _parse_common_cli_options(sys.argv[2:])
        except ValueError as exc:
            print(str(exc))
            return

        if len(positional_args) == 1:
            path = positional_args[0]
        elif len(positional_args) == 2:
            lyrics_mode = positional_args[0].strip().lower()
            if lyrics_mode not in SUPPORTED_LYRICS_MODES:
                print(
                    "Invalid lyrics mode - use one of: {}".format(
                        ", ".join(sorted(SUPPORTED_LYRICS_MODES))
                    )
                )
                return
            path = positional_args[1]
        else:
            print("Group mode expects a folder path and an optional lyrics mode.")
            return

        if os.path.isdir(path):
            from metadata_tagging_and_cluster_grouping.group_mode import (
                build_group_mode_services,
                group_folder_batch,
            )

            cache = _build_cache_from_options(options)
            try:
                musicbrainz_client, acoustid_client, fingerprint_service = build_group_mode_services(
                    cache=cache,
                    refresh_cache=options["refresh_cache"],
                )
            except Exception as exc:
                print(str(exc))
                return

            group_folder_batch(
                path,
                musicbrainz_client=musicbrainz_client,
                acoustid_client=acoustid_client,
                fingerprint_service=fingerprint_service,
                lyrics_mode=lyrics_mode,
                cache=cache,
                refresh_cache=options["refresh_cache"],
            )
        else:
            print("Group mode currently expects a folder path.")
            return

    elif action == "duplicates":
        try:
            options, positional_args = _parse_common_cli_options(
                sys.argv[2:],
                allow_output=True,
                allow_cleanup=True,
            )
        except ValueError as exc:
            print(str(exc))
            return

        if len(positional_args) != 1:
            print("Duplicates mode expects exactly one folder path.")
            return

        path = positional_args[0]
        if not os.path.isdir(path):
            print("Duplicates mode currently expects a folder path.")
            return

        from caching_and_duplicate_detection.duplicate_detector import run_duplicate_detection

        cache = _build_cache_from_options(options)
        run_duplicate_detection(
            path,
            cache=cache,
            refresh_cache=options["refresh_cache"],
            output_path=options.get("output"),
            cleanup=bool(options.get("cleanup")),
        )

    elif action == "split":
        from audio_splitting_and_lyrics_transcription.audio_ml_worker_launcher import run_split_mode

        run_split_mode(sys.argv[2:])

    else:
        print("Invalid action - use 'analyse', 'group', 'duplicates', or 'split'.")
        return


def _parse_common_cli_options(
    args: list[str],
    *,
    allow_output: bool = False,
    allow_cleanup: bool = False,
) -> tuple[dict[str, object], list[str]]:
    options: dict[str, object] = {
        "refresh_cache": False,
        "no_cache": False,
        "cache_db": None,
        "output": None,
        "cleanup": False,
    }
    positional_args: list[str] = []

    index = 0
    while index < len(args):
        token = args[index]

        if token == "--refresh-cache":
            options["refresh_cache"] = True
            index += 1
            continue

        if token == "--no-cache":
            options["no_cache"] = True
            index += 1
            continue

        if token == "--cache-db":
            if index + 1 >= len(args):
                raise ValueError("Missing value for --cache-db.")
            options["cache_db"] = args[index + 1]
            index += 2
            continue

        if token == "--output":
            if not allow_output:
                raise ValueError("--output is only supported for duplicates mode.")
            if index + 1 >= len(args):
                raise ValueError("Missing value for --output.")
            options["output"] = args[index + 1]
            index += 2
            continue

        if token == "--cleanup":
            if not allow_cleanup:
                raise ValueError("--cleanup is only supported for duplicates mode.")
            options["cleanup"] = True
            index += 1
            continue

        if token.startswith("--"):
            raise ValueError(f"Unknown option: {token}")

        positional_args.append(token)
        index += 1

    return options, positional_args


def _build_cache_from_options(options: dict[str, object]) -> "AudioCache | None":
    if options.get("no_cache"):
        return None

    from caching_and_duplicate_detection.audio_cache import AudioCache

    raw_cache_db = options.get("cache_db")
    cache_db_path = Path(raw_cache_db) if isinstance(raw_cache_db, str) else None
    cache = AudioCache(cache_db_path)
    cache.initialize()
    return cache if cache.is_enabled else None


if __name__ == "__main__":
    main()
