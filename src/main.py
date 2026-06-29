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
import subprocess
import sys
import sysconfig
import urllib.error
import urllib.request
import webbrowser
from textwrap import dedent

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
DEFAULT_GUI_HOST = "127.0.0.1"
DEFAULT_GUI_PORT = "8080"


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
    if _open_running_gui_if_available():
        return

    try:
        from gui.app import run
    except ModuleNotFoundError as exc:
        if exc.name == "nicegui" and _relaunch_gui_with_regular_python():
            return
        raise

    run()


def _relaunch_gui_with_regular_python() -> bool:
    if os.environ.get("AUDIO_ANALYZER_GUI_REEXEC") == "1":
        return False
    if sysconfig.get_config_var("Py_GIL_DISABLED") != 1:
        return False

    env = os.environ.copy()
    env["AUDIO_ANALYZER_GUI_REEXEC"] = "1"
    completed = subprocess.run(
        ["py", "-3.14", *sys.argv],
        env=env,
        check=False,
    )
    if completed.returncode != 0:
        print(
            "GUI launch failed under regular Python 3.14. If this is a dependency issue, "
            "install GUI dependencies with: py -3.14 -m pip install -r requirements-gui.txt"
        )
    return True


def _open_running_gui_if_available() -> bool:
    host = os.environ.get("AUDIO_ANALYZER_GUI_HOST", DEFAULT_GUI_HOST)
    port = os.environ.get("AUDIO_ANALYZER_GUI_PORT", DEFAULT_GUI_PORT)
    url = f"http://{host}:{port}/"

    try:
        with urllib.request.urlopen(url, timeout=1.0) as response:
            if response.status >= 400:
                return False
    except (OSError, urllib.error.URLError):
        return False

    print(f"GUI already running at {url}")
    webbrowser.open_new_tab(url)
    return True


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
        if len(sys.argv) > 3:
            print("Analyse mode does not accept a lyrics mode argument.")
            return

        path = sys.argv[2]
        from audio_analysis.analyse_modes import analyse_single_file, analyse_folder_batch

        if os.path.isfile(path) and path.lower().endswith(".flac"):
            analyse_single_file(path, want_verbose=True)
        elif os.path.isdir(path):
            analyse_folder_batch(path)
        else:
            print("Invalid path for analyse mode or not a FLAC file.")
            return

    elif action == "group":
        if len(sys.argv) > 4:
            print("Too many arguments for group mode - check usage using 'py main.py help'")
            return

        if len(sys.argv) == 3:
            path = sys.argv[2]
        else:
            lyrics_mode = sys.argv[2].strip().lower()
            if lyrics_mode not in SUPPORTED_LYRICS_MODES:
                print(
                    "Invalid lyrics mode - use one of: {}".format(
                        ", ".join(sorted(SUPPORTED_LYRICS_MODES))
                    )
                )
                return
            path = sys.argv[3]

        if os.path.isdir(path):
            from metadata_tagging_and_cluster_grouping.group_mode import (
                build_group_mode_services,
                group_folder_batch,
            )

            try:
                musicbrainz_client, acoustid_client, fingerprint_service = build_group_mode_services()
            except Exception as exc:
                print(str(exc))
                return

            group_folder_batch(
                path,
                musicbrainz_client=musicbrainz_client,
                acoustid_client=acoustid_client,
                fingerprint_service=fingerprint_service,
                lyrics_mode=lyrics_mode,
            )
        else:
            print("Group mode currently expects a folder path.")
            return

    elif action == "split":
        from audio_splitting_and_lyrics_transcription.audio_ml_worker_launcher import run_split_mode

        run_split_mode(sys.argv[2:])

    else:
        print("Invalid action - use 'analyse', 'group', or 'split'.")
        return


if __name__ == "__main__":
    main()
