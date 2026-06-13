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
import sys

LYRICS_MODE_UNSYNCED = "lyrics-unsynced"
LYRICS_MODE_SYNCED = "lyrics-synced"
LYRICS_MODE_NONE = "lyrics-none"
SUPPORTED_LYRICS_MODES = frozenset(
    {
        LYRICS_MODE_UNSYNCED,
        LYRICS_MODE_SYNCED,
        LYRICS_MODE_NONE,
    }
)

def main():
    # 0. Set instructions and manuals
    if len(sys.argv) < 2:
        print("Wrong number of arguments - check usage using 'py main.py help'")
        return

    elif sys.argv[1] == "help":
        print("""Usage: py main.py <analyse|group> [lyrics_mode] "<path_to_file_or_folder>" """)
        print("Examples:")
        print("""  py main.py analyse "X:\\path\\to\\file.flac" """)
        print("""  py main.py analyse "X:\\path\\to\\folder" """)
        print("""  py main.py group "X:\\path\\to\\folder" """)
        print("""  py main.py group lyrics-none "X:\\path\\to\\folder" """)
        print("""  py main.py group lyrics-unsynced "X:\\path\\to\\folder" """)
        print("""  py main.py group lyrics-synced "X:\\path\\to\\folder" """)
        print("Supported lyrics modes: {}".format(", ".join(sorted(SUPPORTED_LYRICS_MODES))))
        return

    if len(sys.argv) < 3:
        print("Missing arguments - check usage using 'py main.py help'")
        return

    # 1. Get action and target path from command-line arguments and determine running mode
    action = sys.argv[1].lower()
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
            from metadata_enrichment_and_file_grouping.group_mode import (
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

    else:
        print("Invalid action - use 'analyse' or 'group'.")
        return


if __name__ == "__main__":
    main()
