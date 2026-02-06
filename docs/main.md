## External Dependencies

### Imports

* `os` — filesystem checks (`os.path.isfile`, `os.path.isdir`).
* `sys` — command-line argument access (`sys.argv`).

### Internal module imports

* `run_modes.run_single_file` — processes a single FLAC file (optionally verbose and with spectrogram).
* `run_modes.run_folder_batch` — processes all FLAC files found recursively in a folder and writes a timestamped CSV.

## Module-level Constants and Variables

### Key runtime variables (created/used by the module’s functions)

* `sys.argv: list[str]`
  Command-line arguments passed to the program.

* `path: str`
  User-provided path from `sys.argv[1]`. May point to a `.flac` file or a directory.

## Additional Information

### CLI usage and mode selection (moved from code comments)

This entrypoint supports two run modes based on the single required argument:

1. **Single-file mode**
   Condition:

   * `os.path.isfile(path)` and `path.lower().endswith(".flac")`

   Action:

   * `run_single_file(path, want_verbose=True, want_spectrogram=True)`

   Notes:

   * Verbose logging is enabled.
   * Spectrogram generation is requested (requires FFmpeg availability as checked inside the spectrogram module).

2. **Folder batch mode**
   Condition:

   * `os.path.isdir(path)`

   Action:

   * `run_folder_batch(path)`

   Notes:

   * Batch mode is responsible for file discovery and CSV logging.

Invalid input:

* If `path` is neither a FLAC file nor a directory, the program prints an error message and exits.

### Help and argument validation

The program enforces a minimal CLI contract:

* If fewer than 2 arguments are provided (`len(sys.argv) < 2`), it prints an error hint and exits.
* If the first argument is `help`, it prints a usage string and exits.

The usage guidance instructs quoting the file path to avoid shell parsing issues.

### Entrypoint guard

The module uses the standard Python entrypoint guard:

* When executed as a script, `main()` is called.
* When imported as a module, no execution occurs automatically.

## Module Workflow (call graph)

```mermaid
flowchart TD
    classDef ok fill:#20462d,stroke:#2e7d32;
    classDef err fill:#a1362a,stroke:#c62828;

    M["main.py"]:::ok
    F_main["main()"]:::ok
    F_single["run_single_file()"]:::ok
    F_batch["run_folder_batch()"]:::ok

    M --> F_main

    F_main -->|"argv too short"| E_args["print error + return"]:::err
    F_main -->|"argv[1] == help"| E_help["print usage + return"]:::ok

    F_main -->|"isfile && .flac"| F_single
    F_main -->|"isdir"| F_batch
    F_main -->|"otherwise"| E_inv["print invalid path + return"]:::err
```

## Function Inventory

* `main()`
