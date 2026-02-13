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

from run_modes import run_single_file, run_folder_batch

def main():
    # 0. Set instructions and manuals
    if len(sys.argv) < 2:
        print("Wrong number of arguments - check usage using 'py main.py help'")
        return

    elif sys.argv[1] == "help":
        print("""Usage: py main.py "<path_to_flac_file>" (including .flac extension, and use quotes for correct shell parsing.)""")
        print("For example: python main.py X:\\path\\to\\file.flac")
        return

    # 1. Get file or folder path from command-line argument and determine running mode
    path = sys.argv[1]

    if os.path.isfile(path) and path.lower().endswith(".flac"):
        run_single_file(path, want_verbose=True)
    elif os.path.isdir(path):
        run_folder_batch(path)

    else:
        print("Invalid file path or not a FLAC file.")
        return

if __name__ == "__main__":
    main()