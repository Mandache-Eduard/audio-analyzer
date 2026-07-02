from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


METADATA_PYTHON_ENV = "AUDIO_ANALYZER_METADATA_PYTHON"
ANALYSIS_PYTHON_ENV = "AUDIO_ANALYZER_ANALYSIS_PYTHON"
DUPLICATES_PYTHON_ENV = "AUDIO_ANALYZER_DUPLICATES_PYTHON"
METADATA_RUNTIME_MODULES = (
    "mutagen",
    "tqdm",
    "requests",
    "bs4",
    "dotenv",
)
DUPLICATE_RUNTIME_MODULES = METADATA_RUNTIME_MODULES
DUPLICATE_CLEANUP_RUNTIME_MODULES = (
    *METADATA_RUNTIME_MODULES,
    "send2trash",
)
ANALYSIS_RUNTIME_MODULES = (
    "soundfile",
    "numpy",
    "scipy",
    "tqdm",
    "dotenv",
)


def resolve_metadata_python_command() -> list[str] | None:
    return _resolve_python_command(METADATA_PYTHON_ENV, METADATA_RUNTIME_MODULES)


def resolve_analysis_python_command() -> list[str] | None:
    return _resolve_python_command(ANALYSIS_PYTHON_ENV, ANALYSIS_RUNTIME_MODULES)


def resolve_duplicate_python_command(*, cleanup: bool = False) -> list[str] | None:
    required_modules = (
        DUPLICATE_CLEANUP_RUNTIME_MODULES if cleanup else DUPLICATE_RUNTIME_MODULES
    )
    return _resolve_python_command(DUPLICATES_PYTHON_ENV, required_modules)


def python_can_run_analysis(command: list[str]) -> bool:
    return _python_has_modules(command, ANALYSIS_RUNTIME_MODULES)


def _resolve_python_command(
    explicit_env_var: str,
    modules: tuple[str, ...],
) -> list[str] | None:
    explicit_python = os.environ.get(explicit_env_var)
    candidate_commands: list[list[str]] = []
    if explicit_python:
        candidate_commands.append([explicit_python])

    project_root = Path(__file__).resolve().parent.parent
    candidate_interpreters = [
        project_root / ".venv-main-3.14t" / "Scripts" / "python.exe",
        project_root / ".venv-main-3.14" / "Scripts" / "python.exe",
        project_root / ".venv-demucs-3.11" / "Scripts" / "python.exe",
    ]
    for candidate_interpreter in candidate_interpreters:
        if candidate_interpreter.is_file():
            candidate_commands.append([str(candidate_interpreter)])

    current_executable = Path(sys.executable)
    if current_executable.is_file():
        candidate_commands.append([str(current_executable)])

    candidate_commands.extend(
        [
            ["py", "-3.14"],
            ["py", "-3.11"],
            ["py"],
        ]
    )

    for command in candidate_commands:
        if _python_has_modules(command, modules):
            return command

    return None


def _python_has_modules(command: list[str], modules: tuple[str, ...]) -> bool:
    module_list = ", ".join(repr(module_name) for module_name in modules)
    probe_code = (
        "import importlib.util; "
        f"mods=[{module_list}]; "
        "raise SystemExit(0 if all(importlib.util.find_spec(m) for m in mods) else 1)"
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
