from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from config import AUDIO_ML_PYTHON_PATH, AUDIO_ML_WORKER_PATH
from tqdm import tqdm

VALID_OUTPUTS = frozenset({"vocals", "instrumental", "bass", "drums", "lyrics"})
VALID_LYRICS_MODES = frozenset({"none", "plain", "timestamped"})
VALID_DEVICES = frozenset({"auto", "cpu", "cuda"})
LOCAL_WHISPER_MODEL_FILENAME = "ggml-large-v2.bin"
PROGRESS_PREFIX = "__SPLIT_PROGRESS__ "


def run_split_mode(argv: list[str]) -> dict[str, Any]:
    args = _parse_args(argv)
    input_path = Path(args.path).expanduser()

    if not input_path.is_file():
        error = f"Input audio file does not exist: {input_path}"
        print(error)
        return {"status": "error", "error": error}

    requested_outputs = _parse_outputs(args.outputs)
    if not requested_outputs:
        error = "No outputs were requested."
        print(error)
        return {"status": "error", "error": error}

    try:
        _validate_worker_runtime()
    except RuntimeError as exc:
        print(str(exc))
        return {"status": "error", "error": str(exc)}

    report_path = _report_path_for_input(input_path)
    command = _build_worker_command(
        input_path=input_path,
        requested_outputs=requested_outputs,
        lyrics_mode=args.lyrics_mode,
        language=args.language,
        device=args.device,
        overwrite=args.overwrite,
        output_root=_output_root_for_input(input_path),
        report_path=report_path,
    )
    selected_platform = _resolve_selected_platform(args.device)
    selected_models = _selected_models_for_outputs(
        requested_outputs=requested_outputs,
        lyrics_mode=args.lyrics_mode,
        language=args.language,
        device=args.device,
    )
    _print_selected_runtime(selected_platform, selected_models)
    completed_process = _run_worker_with_progress(command)

    report = _read_report(report_path)
    if report is None:
        report = {
            "status": "error",
            "error": "Audio ML worker did not write a JSON report.",
            "details": completed_process.stderr.strip() or completed_process.stdout.strip(),
        }

    _print_worker_summary(report, completed_process.returncode, report_path)
    return {
        "status": report.get("status", "error"),
        "returncode": completed_process.returncode,
        "report": report,
        "report_path": str(report_path),
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="py src/main.py split",
        description="Run audio separation/transcription in the Python 3.11 audio worker.",
    )
    parser.add_argument(
        "--outputs",
        required=True,
        help="Comma-separated outputs: vocals,instrumental,bass,drums,lyrics.",
    )
    parser.add_argument(
        "--lyrics-mode",
        default="none",
        choices=sorted(VALID_LYRICS_MODES),
        help="Lyrics output mode.",
    )
    parser.add_argument(
        "--device",
        default="auto",
        choices=sorted(VALID_DEVICES),
        help="Worker inference device.",
    )
    parser.add_argument(
        "--language",
        default="auto",
        help="Lyrics transcription language code, or auto.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing output files.",
    )
    parser.add_argument("path", help="Input audio file. Must be the final argument.")
    return parser.parse_args(argv)


def _parse_outputs(outputs: str) -> tuple[str, ...]:
    requested_outputs: list[str] = []
    invalid_outputs: list[str] = []

    for raw_output in outputs.split(","):
        output = raw_output.strip().lower()
        if not output:
            continue
        if output not in VALID_OUTPUTS:
            invalid_outputs.append(output)
            continue
        if output not in requested_outputs:
            requested_outputs.append(output)

    if invalid_outputs:
        raise SystemExit(
            "Invalid output(s): {}. Use one or more of: {}".format(
                ", ".join(invalid_outputs),
                ", ".join(sorted(VALID_OUTPUTS)),
            )
        )
    return tuple(requested_outputs)


def _validate_worker_runtime() -> None:
    if not AUDIO_ML_PYTHON_PATH.is_file():
        raise RuntimeError(f"Audio ML Python interpreter was not found: {AUDIO_ML_PYTHON_PATH}")
    if not AUDIO_ML_WORKER_PATH.is_file():
        raise RuntimeError(f"Audio ML worker script was not found: {AUDIO_ML_WORKER_PATH}")


def _build_worker_command(
    *,
    input_path: Path,
    requested_outputs: tuple[str, ...],
    lyrics_mode: str,
    language: str,
    device: str,
    overwrite: bool,
    output_root: Path,
    report_path: Path,
) -> list[str]:
    command = [
        str(AUDIO_ML_PYTHON_PATH),
        str(AUDIO_ML_WORKER_PATH),
        "--input",
        str(input_path),
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
    return command


def _report_path_for_input(input_path: Path) -> Path:
    return _track_output_root(input_path) / "report.json"


def _output_root_for_input(input_path: Path) -> Path:
    return input_path.parent / "split_files"


def _track_output_root(input_path: Path) -> Path:
    return _output_root_for_input(input_path) / input_path.stem


def _run_worker_with_progress(command: list[str]) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    stdout_lines: list[str] = []
    progress_bar: tqdm | None = None

    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.rstrip("\r\n")
        event = _parse_progress_event(line)
        if event is not None:
            progress_bar = _update_progress_bar(progress_bar, event)
            continue
        if line:
            stdout_lines.append(line)

    if progress_bar is not None:
        progress_bar.close()

    stderr_output = process.stderr.read() if process.stderr is not None else ""
    returncode = process.wait()
    return subprocess.CompletedProcess(
        args=command,
        returncode=returncode,
        stdout="\n".join(stdout_lines),
        stderr=stderr_output,
    )


def _parse_progress_event(line: str) -> dict[str, Any] | None:
    if not line.startswith(PROGRESS_PREFIX):
        return None
    payload = line[len(PROGRESS_PREFIX):]
    try:
        event = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def _update_progress_bar(progress_bar: tqdm | None, event: dict[str, Any]) -> tqdm:
    total = int(event.get("total", 0))
    completed = int(event.get("completed", 0))
    message = str(event.get("message") or "Processing")

    if progress_bar is None:
        progress_bar = tqdm(total=max(total, 1), unit="step", desc=message, dynamic_ncols=True)

    progress_bar.set_description(message)
    delta = max(0, completed - progress_bar.n)
    if delta:
        progress_bar.update(delta)
    progress_bar.refresh()
    return progress_bar


def _read_report(report_path: Path) -> dict[str, Any] | None:
    if not report_path.is_file():
        return None
    try:
        return json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "status": "error",
            "error": "Audio ML worker wrote an invalid JSON report.",
            "details": str(exc),
        }


def _print_worker_summary(report: dict[str, Any], returncode: int, report_path: Path) -> None:
    status = report.get("status", "error")
    print("Split summary:")
    print(f"    status: {status}")
    print(f"    worker exit code: {returncode}")
    print(f"    report file: {report_path}")

    outputs = report.get("outputs") or {}
    if outputs:
        print("    outputs:")
        for output_name, output_path in outputs.items():
            print(f"        {output_name}: {output_path}")

    warnings = report.get("warnings") or []
    if warnings:
        print("    warnings:")
        for warning in warnings:
            print(f"        {warning}")

    if status != "ok":
        print(f"    error: {report.get('error') or 'unknown error'}")
        if report.get("details"):
            print(f"    details: {report['details']}")


def _resolve_selected_platform(requested_device: str) -> str:
    if requested_device == "cpu":
        return "CPU"
    if requested_device == "cuda":
        return "GPU"

    probe_command = [
        str(AUDIO_ML_PYTHON_PATH),
        "-c",
        "import torch; print('GPU' if torch.cuda.is_available() else 'CPU')",
    ]
    completed_process = subprocess.run(
        probe_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    resolved = completed_process.stdout.strip().upper()
    return resolved if resolved in {"CPU", "GPU"} else "AUTO"


def _selected_models_for_outputs(
    *,
    requested_outputs: tuple[str, ...],
    lyrics_mode: str,
    language: str,
    device: str,
) -> list[str]:
    models: list[str] = []
    if "vocals" in requested_outputs or "lyrics" in requested_outputs:
        models.append("Vocals: Kim_Vocal_2.onnx")
    if "instrumental" in requested_outputs:
        models.append("Instrumental: MDX23C-8KFFT-InstVoc_HQ.ckpt")
    if "bass" in requested_outputs or "drums" in requested_outputs:
        models.append("Bass/Drums: htdemucs_ft.yaml")
    if "lyrics" in requested_outputs:
        selected_whisper_model = _selected_local_whisper_model_name()
        models.append(
            "Lyrics: OpenAI Whisper local via whisper.cpp ({}, mode: {}, language: {}, device: {})".format(
                selected_whisper_model,
                lyrics_mode,
                language,
                device,
            )
        )
    return models


def _selected_local_whisper_model_name() -> str:
    model_path = AUDIO_ML_WORKER_PATH.parents[2] / "models" / "whisper" / LOCAL_WHISPER_MODEL_FILENAME
    if model_path.is_file():
        return model_path.name
    return "missing local model"


def _print_selected_runtime(selected_platform: str, selected_models: list[str]) -> None:
    print(f"Selected platform: {selected_platform}")
    if not selected_models:
        return
    print("Selected models:")
    for model_line in selected_models:
        print(f"    {model_line}")


def run_audio_ml_mode(argv: list[str]) -> dict[str, Any]:
    return run_split_mode(argv)
