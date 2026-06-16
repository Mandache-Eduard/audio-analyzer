from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

SUPPORTED_EXTENSIONS = frozenset(
    {
        ".mp3",
        ".flac",
        ".m4a",
        ".ogg",
        ".opus",
        ".wav",
        ".aiff",
        ".wma",
    }
)
SEPARATION_OUTPUTS = frozenset({"vocals", "instrumental", "bass", "drums"})
VALID_OUTPUTS = SEPARATION_OUTPUTS | {"lyrics"}
VALID_LYRICS_MODES = frozenset({"none", "plain", "timestamped"})
VALID_DEVICES = frozenset({"auto", "cpu", "cuda"})

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FFMPEG_PATH = PROJECT_ROOT / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
FFPROBE_PATH = PROJECT_ROOT / "tools" / "ffmpeg" / "bin" / "ffprobe.exe"
MODELS_ROOT = PROJECT_ROOT / "models"
AUDIO_SEPARATOR_MODELS_ROOT = MODELS_ROOT / "audio-separator"
WHISPER_MODELS_ROOT = MODELS_ROOT / "whisper"
WHISPER_CPP_ROOT = MODELS_ROOT / "whisper.cpp"
AUDIO_SEPARATOR_EXE_PATH = PROJECT_ROOT / ".venv-demucs-3.11" / "Scripts" / "audio-separator.exe"
WHISPER_CPP_CPU_CLI_PATH = WHISPER_CPP_ROOT / "v1.8.6" / "Release" / "whisper-cli.exe"
WHISPER_CPP_CUDA_CLI_PATH = WHISPER_CPP_ROOT / "v1.8.6-cublas-12.4.0" / "Release" / "whisper-cli.exe"
KIM_VOCAL_2_MODEL_FILENAME = "Kim_Vocal_2.onnx"
INSTRUMENTAL_MODEL_FILENAME = "MDX23C-8KFFT-InstVoc_HQ.ckpt"
BASS_DRUMS_MODEL_FILENAME = "htdemucs_ft.yaml"
LOCAL_WHISPER_MODEL_FILENAME = "ggml-large-v2.bin"
PROGRESS_PREFIX = "__SPLIT_PROGRESS__ "


def main() -> int:
    args = _parse_args()
    started_at = time.perf_counter()
    report_path = Path(args.report)
    report = _base_report(args)

    try:
        requested_outputs = _parse_outputs(args.outputs)
        report["requested_outputs"] = requested_outputs

        _validate_runtime(args.input, requested_outputs)
        input_path = Path(args.input).resolve()
        output_root = (
            Path(args.output_root).resolve()
            if args.output_root
            else input_path.parent / "split_files"
        )
        output_root.mkdir(parents=True, exist_ok=True)

        requested_bass_drum_stems = sorted({"bass", "drums"}.intersection(requested_outputs))
        progress = _ProgressReporter(_build_progress_steps(requested_outputs), report)
        progress.emit_initial()

        if "vocals" in requested_outputs or "lyrics" in requested_outputs:
            progress.set_message("Separating vocals")
            with tempfile.TemporaryDirectory(prefix="flac_auth_audio_separator_") as temp_dir:
                temp_output_root = Path(temp_dir)
                separator_vocals_path = _run_audio_separator_single_stem(
                    input_path=input_path,
                    output_root=temp_output_root,
                    model_filename=KIM_VOCAL_2_MODEL_FILENAME,
                    single_stem="Vocals",
                    requested_device=args.device,
                )
                progress.advance("Vocals separated")
                if "vocals" in requested_outputs:
                    progress.set_message("Writing vocals")
                    vocals_output_path = _stem_output_path(output_root, input_path, "vocals")
                    _encode_audio_file(
                        separator_vocals_path,
                        vocals_output_path,
                        overwrite=args.overwrite,
                    )
                    report["outputs"]["vocals"] = str(vocals_output_path)
                    report["models_used"]["vocals"] = KIM_VOCAL_2_MODEL_FILENAME
                    progress.advance("Vocals written")

                if "lyrics" in requested_outputs:
                    lyrics_model_path = _resolve_local_whisper_model_path()
                    progress.set_message(f"Transcribing lyrics with {lyrics_model_path.name}")
                    lyrics_path = _lyrics_output_path(output_root, input_path, args.lyrics_mode)
                    lyrics_result = _write_lyrics(
                        separator_vocals_path,
                        lyrics_path,
                        args.lyrics_mode,
                        args.language,
                        args.device,
                        lyrics_model_path,
                        progress.set_message,
                    )
                    report["outputs"]["lyrics_txt"] = str(lyrics_path)
                    report["models_used"]["lyrics"] = "{} ({})".format(
                        lyrics_model_path.name,
                        lyrics_result["backend"],
                    )
                    warning = lyrics_result.get("warning")
                    if warning:
                        report["warnings"].append(warning)
                    progress.advance("Lyrics transcribed")

        if "instrumental" in requested_outputs:
            progress.set_message("Separating instrumental")
            with tempfile.TemporaryDirectory(prefix="flac_auth_audio_separator_") as temp_dir:
                temp_output_root = Path(temp_dir)
                separator_instrumental_path = _run_audio_separator_single_stem(
                    input_path=input_path,
                    output_root=temp_output_root,
                    model_filename=INSTRUMENTAL_MODEL_FILENAME,
                    single_stem="Instrumental",
                    requested_device=args.device,
                )
                instrumental_output_path = _stem_output_path(output_root, input_path, "instrumental")
                _encode_audio_file(
                    separator_instrumental_path,
                    instrumental_output_path,
                    overwrite=args.overwrite,
                )
                report["outputs"]["instrumental"] = str(instrumental_output_path)
                report["models_used"]["instrumental"] = INSTRUMENTAL_MODEL_FILENAME
                progress.advance("Instrumental written")

        if requested_bass_drum_stems:
            progress.set_message("Separating bass and drums")
            with tempfile.TemporaryDirectory(prefix="flac_auth_audio_separator_") as temp_dir:
                temp_output_root = Path(temp_dir)
                separated_stems = _run_audio_separator(
                    input_path=input_path,
                    output_root=temp_output_root,
                    model_filename=BASS_DRUMS_MODEL_FILENAME,
                    requested_stems=requested_bass_drum_stems,
                    requested_device=args.device,
                )
                progress.advance("Bass and drums separated")

                for stem in requested_bass_drum_stems:
                    progress.set_message(f"Writing {stem}")
                    output_path = _stem_output_path(output_root, input_path, stem)
                    source_path = separated_stems.get(stem)
                    if source_path is None:
                        raise RuntimeError(f"audio-separator did not produce a {stem} output file")
                    _encode_audio_file(source_path, output_path, overwrite=args.overwrite)
                    report["outputs"][stem] = str(output_path)
                    report["models_used"][stem] = BASS_DRUMS_MODEL_FILENAME
                    progress.advance(f"{stem.capitalize()} written")

        report["status"] = "ok"
        return_code = 0

    except Exception as exc:
        report["status"] = "error"
        report["error"] = _strip_ansi(str(exc))
        report["details"] = type(exc).__name__
        print(str(exc), file=sys.stderr)
        return_code = 1
    finally:
        report["elapsed_seconds"] = round(time.perf_counter() - started_at, 3)
        _write_json_report(report_path, report)

    return return_code


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local audio ML processing.")
    parser.add_argument("--input", required=True, help="Input audio file.")
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
        "--language",
        default="auto",
        help="Lyrics transcription language code, or auto.",
    )
    parser.add_argument(
        "--device",
        default="auto",
        choices=sorted(VALID_DEVICES),
        help="Model inference device.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing output files.",
    )
    parser.add_argument(
        "--output-root",
        default=None,
        help="Final output folder.",
    )
    parser.add_argument("--report", required=True, help="JSON report path.")
    return parser.parse_args()


def _base_report(args: argparse.Namespace) -> dict:
    return {
        "status": "pending",
        "input_file": str(Path(args.input)),
        "device": args.device,
        "language": args.language,
        "requested_outputs": [],
        "outputs": {},
        "models_used": {},
        "warnings": [],
        "error": None,
        "details": None,
        "elapsed_seconds": None,
    }


def _parse_outputs(outputs: str) -> list[str]:
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
        raise ValueError(
            "Invalid output(s): {}. Use one or more of: {}".format(
                ", ".join(invalid_outputs),
                ", ".join(sorted(VALID_OUTPUTS)),
            )
        )
    if not requested_outputs:
        raise ValueError("No outputs were requested.")
    return requested_outputs


def _validate_runtime(input_file: str, requested_outputs: list[str]) -> None:
    input_path = Path(input_file)
    if not input_path.is_file():
        raise FileNotFoundError(f"Input audio file does not exist: {input_path}")
    if input_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported input extension: {input_path.suffix or 'missing'}")
    if not FFMPEG_PATH.is_file():
        raise FileNotFoundError(f"Local FFmpeg was not found at: {FFMPEG_PATH}")
    if not FFPROBE_PATH.is_file():
        raise FileNotFoundError(f"Local FFprobe was not found at: {FFPROBE_PATH}")
    if not MODELS_ROOT.is_dir():
        raise FileNotFoundError(f"Model folder does not exist: {MODELS_ROOT}")
    if not AUDIO_SEPARATOR_MODELS_ROOT.is_dir():
        raise FileNotFoundError(f"Audio-separator model folder does not exist: {AUDIO_SEPARATOR_MODELS_ROOT}")
    if ("vocals" in requested_outputs or "instrumental" in requested_outputs or "lyrics" in requested_outputs) and not AUDIO_SEPARATOR_EXE_PATH.is_file():
        raise FileNotFoundError(f"audio-separator executable was not found at: {AUDIO_SEPARATOR_EXE_PATH}")
    if "lyrics" in requested_outputs:
        if not WHISPER_MODELS_ROOT.is_dir():
            raise FileNotFoundError(f"Whisper model folder does not exist: {WHISPER_MODELS_ROOT}")
        _resolve_whispercpp_runtime(requested_device="auto")
        _resolve_local_whisper_model_path()
    _probe_audio(input_path)


def _probe_audio(input_path: Path) -> None:
    result = subprocess.run(
        [
            str(FFPROBE_PATH),
            "-v",
            "error",
            "-show_streams",
            "-select_streams",
            "a:0",
            str(input_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        detail = result.stderr.strip() or "ffprobe did not find an audio stream"
        raise RuntimeError(f"Input is not a decodable audio file: {detail}")


def _run_audio_separator_single_stem(
    *,
    input_path: Path,
    output_root: Path,
    model_filename: str,
    single_stem: str,
    requested_device: str,
) -> Path:
    outputs = _run_audio_separator(
        input_path=input_path,
        output_root=output_root,
        model_filename=model_filename,
        requested_stems=[single_stem.lower()],
        requested_device=requested_device,
        single_stem=single_stem,
    )
    return outputs[single_stem.lower()]


def _run_audio_separator(
    *,
    input_path: Path,
    output_root: Path,
    model_filename: str,
    requested_stems: list[str],
    requested_device: str,
    single_stem: str | None = None,
) -> dict[str, Path]:
    command = [
        str(AUDIO_SEPARATOR_EXE_PATH),
        str(input_path),
        "--model_filename",
        model_filename,
        "--model_file_dir",
        str(AUDIO_SEPARATOR_MODELS_ROOT),
        "--output_dir",
        str(output_root),
        "--output_format",
        "FLAC",
    ]
    if single_stem:
        command.extend(["--single_stem", single_stem])
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    produced_outputs = _find_audio_separator_outputs(output_root)
    missing_stems = [stem for stem in requested_stems if stem not in produced_outputs]
    if result.returncode != 0 or missing_stems:
        detail = _strip_ansi(result.stderr.strip() or result.stdout.strip() or "audio-separator exited without output")
        if missing_stems and result.returncode == 0:
            detail = "audio-separator did not produce expected output stem(s): {}".format(
                ", ".join(sorted(missing_stems))
            )
        raise RuntimeError(f"audio-separator failed: {detail}")
    return produced_outputs


def _find_audio_separator_outputs(output_root: Path) -> dict[str, Path]:
    supported_stems = (
        "vocals",
        "instrumental",
        "bass",
        "drums",
        "other",
        "guitar",
        "piano",
    )
    outputs: dict[str, Path] = {}
    for path in sorted(output_root.glob("*.flac")):
        normalized_name = path.stem.lower()
        for stem in supported_stems:
            if f"({stem})" in normalized_name:
                outputs[stem] = path
                break
    return outputs


def _resolve_local_whisper_model_path() -> Path:
    model_path = WHISPER_MODELS_ROOT / LOCAL_WHISPER_MODEL_FILENAME
    if model_path.is_file():
        return model_path
    raise FileNotFoundError(f"Local Whisper ggml model was not found: {model_path}")


def _write_lyrics(
    source_path: Path,
    lyrics_path: Path,
    lyrics_mode: str,
    language: str,
    device: str,
    model_path: Path,
    progress_callback: Callable[[str], None] | None = None,
) -> dict[str, str | None]:
    if lyrics_mode == "none":
        lyrics_mode = "plain"

    cli_path, backend, backend_warning = _resolve_whispercpp_runtime(requested_device=device)
    warning = backend_warning

    with tempfile.TemporaryDirectory(prefix="flac_auth_whispercpp_") as temp_dir:
        output_prefix = Path(temp_dir) / "lyrics_output"
        _emit_transcription_progress(progress_callback, f"Loading Whisper model {model_path.name}")
        _emit_transcription_progress(progress_callback, "Running Whisper transcription")
        try:
            transcription_result = _run_whispercpp_transcription(
                source_path=source_path,
                output_prefix=output_prefix,
                model_path=model_path,
                language=language,
                cli_path=cli_path,
                backend=backend,
            )
        except RuntimeError as exc:
            if device == "cuda" or backend != "cuda":
                raise
            _emit_transcription_progress(
                progress_callback,
                "GPU Whisper transcription failed, retrying on CPU",
            )
            transcription_result = _run_whispercpp_transcription(
                source_path=source_path,
                output_prefix=output_prefix,
                model_path=model_path,
                language=language,
                cli_path=WHISPER_CPP_CPU_CLI_PATH,
                backend="cpu",
            )
            backend = "cpu"
            fallback_warning = "Lyrics transcription fell back to CPU after whisper.cpp CUDA execution failed."
            warning = fallback_warning if not warning else f"{warning} {fallback_warning}"

    lyrics_path.parent.mkdir(parents=True, exist_ok=True)
    _emit_transcription_progress(progress_callback, "Writing lyrics output")
    lines = _extract_lyrics_lines(transcription_result, lyrics_mode)
    lyrics_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return {
        "backend": backend,
        "warning": warning,
    }


def _resolve_whispercpp_runtime(*, requested_device: str) -> tuple[Path, str, str | None]:
    cpu_available = WHISPER_CPP_CPU_CLI_PATH.is_file()
    cuda_available = WHISPER_CPP_CUDA_CLI_PATH.is_file()
    has_cuda_device = _has_nvidia_gpu()

    if requested_device == "cpu":
        if not cpu_available:
            raise FileNotFoundError(f"whisper.cpp CPU CLI was not found at: {WHISPER_CPP_CPU_CLI_PATH}")
        return WHISPER_CPP_CPU_CLI_PATH, "cpu", None

    if requested_device == "cuda":
        if not cuda_available:
            raise FileNotFoundError(f"whisper.cpp CUDA CLI was not found at: {WHISPER_CPP_CUDA_CLI_PATH}")
        if not has_cuda_device:
            raise RuntimeError("CUDA was requested for lyrics transcription, but no NVIDIA GPU is available")
        return WHISPER_CPP_CUDA_CLI_PATH, "cuda", None

    if cuda_available and has_cuda_device:
        return WHISPER_CPP_CUDA_CLI_PATH, "cuda", None
    if cpu_available:
        warning = None
        if cuda_available and not has_cuda_device:
            warning = "whisper.cpp CUDA build is present, but no NVIDIA GPU was detected; using CPU lyrics transcription."
        return WHISPER_CPP_CPU_CLI_PATH, "cpu", warning
    if cuda_available:
        return WHISPER_CPP_CUDA_CLI_PATH, "cuda", None
    raise FileNotFoundError(
        "No whisper.cpp CLI was found. Expected CPU path {} or CUDA path {}".format(
            WHISPER_CPP_CPU_CLI_PATH,
            WHISPER_CPP_CUDA_CLI_PATH,
        )
    )


def _has_nvidia_gpu() -> bool:
    result = subprocess.run(
        ["nvidia-smi", "-L"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def _emit_transcription_progress(
    progress_callback: Callable[[str], None] | None,
    message: str,
) -> None:
    if progress_callback is not None:
        progress_callback(message)


def _run_whispercpp_transcription(
    *,
    source_path: Path,
    output_prefix: Path,
    model_path: Path,
    language: str,
    cli_path: Path,
    backend: str,
) -> dict:
    language_code = language.strip().lower() or "auto"
    command = [
        str(cli_path),
        "--model",
        str(model_path),
        "--file",
        str(source_path),
        "--language",
        language_code,
        "--output-json-full",
        "--output-file",
        str(output_prefix),
        "--threads",
        str(max(1, (os.cpu_count() or 4) - 1)),
    ]
    if backend == "cpu":
        command.append("--no-gpu")
    else:
        command.extend(["--device", "0"])
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    json_output_path = output_prefix.with_suffix(".json")
    if result.returncode != 0:
        detail = _strip_ansi(result.stderr.strip() or result.stdout.strip() or "whisper.cpp exited without output")
        raise RuntimeError(f"whisper.cpp failed: {detail}")
    if not json_output_path.is_file():
        raise RuntimeError(f"whisper.cpp did not write a JSON output file: {json_output_path}")
    return _parse_whispercpp_json(json_output_path)


def _parse_whispercpp_json(json_output_path: Path) -> dict:
    try:
        payload = json.loads(json_output_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"whisper.cpp wrote invalid JSON: {exc}") from exc

    transcription = payload.get("transcription")
    if not isinstance(transcription, list):
        raise RuntimeError("whisper.cpp JSON output is missing the transcription list")

    chunks: list[dict[str, float | str]] = []
    full_text_parts: list[str] = []
    for segment in transcription:
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        offsets = segment.get("offsets") or {}
        start_ms = offsets.get("from")
        end_ms = offsets.get("to")
        try:
            start_seconds = float(start_ms) / 1000.0
            end_seconds = float(end_ms) / 1000.0
        except (TypeError, ValueError):
            start_seconds = 0.0
            end_seconds = 0.0
        chunks.append(
            {
                "text": text,
                "timestamp": (start_seconds, end_seconds),
            }
        )
        full_text_parts.append(text)

    return {
        "text": "\n".join(full_text_parts).strip(),
        "chunks": chunks,
    }


def _extract_lyrics_lines(transcription_result: dict, lyrics_mode: str) -> list[str]:
    chunks = transcription_result.get("chunks")
    if isinstance(chunks, list) and chunks:
        lines: list[str] = []
        for chunk in chunks:
            text = str(chunk.get("text") or "").strip()
            if not text:
                continue
            if lyrics_mode == "timestamped":
                timestamp = chunk.get("timestamp")
                if not isinstance(timestamp, (tuple, list)) or not timestamp:
                    continue
                start_seconds = timestamp[0]
                if start_seconds is None:
                    continue
                lines.append(f"[{_format_lrc_time(float(start_seconds))}] {text}")
            else:
                lines.append(text)
        if lines:
            return lines

    full_text = str(transcription_result.get("text") or "").strip()
    if not full_text:
        return []
    if lyrics_mode == "timestamped":
        return [f"[{_format_lrc_time(0.0)}] {full_text}"]
    return [full_text]


def _stem_output_path(output_root: Path, input_path: Path, stem: str) -> Path:
    track_output_root = output_root / input_path.stem
    return track_output_root / f"{stem}{_output_extension(input_path)}"


def _lyrics_output_path(output_root: Path, input_path: Path, lyrics_mode: str) -> Path:
    track_output_root = output_root / input_path.stem
    extension = ".lrc" if lyrics_mode == "timestamped" else ".txt"
    return track_output_root / f"lyrics{extension}"


def _output_extension(input_path: Path) -> str:
    extension = input_path.suffix.lower()
    if extension == ".wma":
        return ".flac"
    return extension or ".flac"


def _encode_audio_file(source_path: Path, output_path: Path, *, overwrite: bool) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(FFMPEG_PATH),
        "-y" if overwrite else "-n",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source_path),
        *_codec_args_for_extension(output_path.suffix.lower()),
        str(output_path),
    ]
    _run_ffmpeg(command, "Failed to encode stem")


def _run_ffmpeg(command: list[str], message: str) -> None:
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "ffmpeg exited without output"
        raise RuntimeError(f"{message}: {detail}")


def _codec_args_for_extension(extension: str) -> list[str]:
    if extension == ".flac":
        return ["-c:a", "flac"]
    if extension == ".wav":
        return ["-c:a", "pcm_s16le"]
    if extension == ".mp3":
        return ["-c:a", "libmp3lame", "-q:a", "2"]
    if extension == ".m4a":
        return ["-c:a", "aac", "-b:a", "256k"]
    if extension == ".ogg":
        return ["-c:a", "libvorbis", "-q:a", "6"]
    if extension == ".opus":
        return ["-c:a", "libopus", "-b:a", "192k"]
    if extension == ".aiff":
        return ["-c:a", "pcm_s16be"]
    return ["-c:a", "flac"]


def _format_lrc_time(seconds: float) -> str:
    minutes = int(seconds // 60)
    remainder = seconds - minutes * 60
    return f"{minutes:02d}:{remainder:05.2f}"


def _write_json_report(report_path: Path, report: dict) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = report_path.with_suffix(report_path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    shutil.move(str(temporary_path), str(report_path))


def _strip_ansi(value: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", value)


def _build_progress_steps(requested_outputs: list[str]) -> list[str]:
    steps: list[str] = []
    if "vocals" in requested_outputs or "lyrics" in requested_outputs:
        steps.append("Separate vocals")
        if "vocals" in requested_outputs:
            steps.append("Write vocals")
        if "lyrics" in requested_outputs:
            steps.append("Transcribe lyrics")
    if "instrumental" in requested_outputs:
        steps.append("Write instrumental")
    bass_drum_outputs = [stem for stem in ("bass", "drums") if stem in requested_outputs]
    if bass_drum_outputs:
        steps.append("Separate bass and drums")
        for stem in bass_drum_outputs:
            steps.append(f"Write {stem}")
    return steps


class _ProgressReporter:
    def __init__(self, steps: list[str], report: dict) -> None:
        self._steps = steps
        self._report = report
        self._completed = 0
        self._current_message = steps[0] if steps else "Processing"

    def emit_initial(self) -> None:
        self._emit_event()

    def set_message(self, message: str) -> None:
        self._current_message = message
        self._emit_event()

    def advance(self, message: str) -> None:
        self._completed += 1
        self._current_message = message
        self._emit_event()

    def _emit_event(self) -> None:
        payload = {
            "completed": self._completed,
            "total": len(self._steps),
            "message": self._current_message,
        }
        print(PROGRESS_PREFIX + json.dumps(payload), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
