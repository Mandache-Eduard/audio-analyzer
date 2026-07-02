# analyse_modes.py
import os
from pathlib import Path

from typing import Any, Dict, Final, List, Optional
from tqdm import tqdm
from audio_analysis.audio_frame_analysis import analyze_frame, divide_into_frames, calculate_effective_cutoff
from audio_analysis.audio_loader import load_flac

from caching_and_duplicate_detection.audio_cache import AudioCache
from caching_and_duplicate_detection.cache_models import ANALYZER_VERSION
from audio_analysis.file_status_determination import determine_file_status
from audio_analysis.data_and_error_logging import append_results_to_csv
from audio_analysis.data_and_error_logging import create_csv_path
from audio_analysis.spectrogram_generator import (
    ffmpeg_works,
    generate_spectrogram_threads,
    single_spectrogram,
)

BATCH_SIZE = 50 #number of entries to be written in the .csv file at once

RESULT_FIELDNAMES: Final[List[str]] = [
    "path",
    "status",
    "confidence",
    "samplerate_hz",
    "num_samples",
    "num_total_frames",
    "num_non-silent_frames",
    "effective_cutoff_hz",
    "per_cutoff_active_fraction",
]

def _format_fractions_for_csv(fractions: Optional[Dict[float, float]]) -> str:
    if not fractions:
        return ""
    return ";".join(f"{int(k)}={v:.4f}" for k, v in sorted(fractions.items()))


def generate_single_file_spectrogram_if_upscaled(
    file_path: str | Path,
    analysis_result: Dict[str, Any],
    *,
    want_verbose: bool = False,
) -> tuple[Path | None, str | None]:
    status_text = str(analysis_result.get("status") or "")
    if not status_text.startswith("Likely UPSCALED"):
        return None, None

    normalized_file_path = Path(file_path)
    try:
        if not ffmpeg_works():
            message = "FFmpeg not detected or not runnable; spectrogram not created."
            if want_verbose:
                print(message)
            return None, message

        spectrogram_path = single_spectrogram(normalized_file_path.parent, normalized_file_path)
        if want_verbose:
            print(f"Spectrogram written to {spectrogram_path}")
        return spectrogram_path, None
    except Exception as exc:
        message = f"Spectrogram generation failed: {type(exc).__name__}: {exc}"
        if want_verbose:
            print(message)
        return None, message

def analyse_single_file(
    file_path,
    want_verbose,
    *,
    cache: AudioCache | None = None,
    refresh_cache: bool = False,
):
    file_id = None
    if cache is not None:
        file_id = cache.upsert_file(
            Path(file_path),
            audio_info={"extension": Path(file_path).suffix.lower()},
        )
        if file_id is not None and not refresh_cache:
            cached_result = cache.get_cached_analysis(file_id, ANALYZER_VERSION)
            if cached_result is not None:
                cached_result["path"] = file_path
                if want_verbose:
                    print(f"Loaded cached analysis result for '{file_path}'.")
                    print(
                        f"Result: {cached_result['status']} "
                        f"(Confidence: {(cached_result.get('confidence') or 0.0) * 100:.1f}%)"
                    )
                return cached_result

    # 1. Load audio
    data, samplerate = load_flac(file_path)

    # 2. Divide into frames
    frames = divide_into_frames(data)

    # 3. Calculate (once per file, then reuse everywhere)
    effective_cutoff_hz = calculate_effective_cutoff(samplerate)

    # 4. Analyze each frame — use the same 'effective_cutoff' for all frames; also collect FFT cache for later reuse
    fft_cache = []
    ratios = [analyze_frame(frame, samplerate, effective_cutoff_hz, fft_cache_list=fft_cache) for frame in frames]

    # 5. Determine status + confidence + fractions
    status, confidence, fractions = determine_file_status(ratios, effective_cutoff_hz, frame_ffts=fft_cache)  # CHANGED: pass cache
    #summary = debug_energy_ratios(ratios)

    # 6. Build result using the single schema list (prevents key drift)
    result: Dict[str, Any] = {k: "" for k in RESULT_FIELDNAMES}
    result.update(
        {
            "path": file_path,
            "status": status,
            "confidence": confidence,
            "samplerate_hz": samplerate,
            "num_samples": len(data),
            "num_total_frames": len(frames),
            "num_non-silent_frames": sum(r > 0 for r in ratios),
            "effective_cutoff_hz": effective_cutoff_hz,
            "per_cutoff_active_fraction": _format_fractions_for_csv(fractions),
        }
    )

    if want_verbose:
        print(f"Loaded '{file_path}' with sample rate {samplerate} Hz, {len(data)} samples.")
        print(f"Divided audio into {len(frames)} frames for analysis.")
        print(f"Analyzed {len(frames)} frames ({sum(r > 0 for r in ratios)} non-silent).")
        print(f"Result: {status} (Confidence: {confidence * 100:.1f}%)")
        print("Energy-above-cutoff summary:")

        if fractions:
            print("[bitrate-debug] per_cutoff_active_fraction:")
            for k, v in sorted(fractions.items()):
                print(f"  {int(k)}: {v:.4f}")

    if cache is not None and file_id is not None:
        cache.upsert_file(
            Path(file_path),
            audio_info={
                "extension": Path(file_path).suffix.lower(),
                "duration_seconds": len(data) / samplerate if samplerate else None,
                "sample_rate_hz": samplerate,
            },
        )
        cache.save_analysis(file_id, ANALYZER_VERSION, result)

    return result

def analyse_folder_batch(
    folder_path,
    *,
    cache: AudioCache | None = None,
    refresh_cache: bool = False,
    csv_path: str | None = None,
):
    # 1. Recursive search for files in given folder
    print("Discovering files...")
    flac_file_paths: list[str] = []
    upscaled_flac_file_paths: list[str] = []
    results_buffer = []
    csv_path = csv_path or create_csv_path(folder_path)

    stack = [folder_path]
    while stack:
        path = stack.pop()
        try:
            with os.scandir(path) as it:
                for entry in it:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                    elif entry.is_file(follow_symlinks=False) and entry.name.lower().endswith(".flac"):
                        flac_file_paths.append(entry.path)
        except OSError:
            pass
    print("Discovered {} files.".format(len(flac_file_paths)))

    # 2. Run same operations as in run_single_file, but for every file found
    print("Processing files...")
    for flac_file_path in tqdm(flac_file_paths, desc="Files processed"):
        result = {"path": flac_file_path, "status": ""}

        try:
            result = analyse_single_file(
                flac_file_path,
                want_verbose=False,
                cache=cache,
                refresh_cache=refresh_cache,
            )

        except Exception as e:
            # Keep a minimal, schema-safe error row
            result.update(
                {
                    "status": "ERROR",
                    "confidence": "",
                    "samplerate_hz": "",
                    "num_samples": "",
                    "num_total_frames": "",
                    "num_non-silent_frames": "",
                    "effective_cutoff_hz": "",
                    "per_cutoff_active_fraction": "",
                }
            )

            print(f"[ERROR] run_single_file failed for: {flac_file_path}\n  {type(e).__name__}: {e}")

        # 3. Group the paths for the upscaled files
        if result.get("status") != "Likely ORIGINAL":
            upscaled_flac_file_paths.append(flac_file_path)

        # 4. Save the results of all audio files in a buffer
        results_buffer.append(result)
        if len(results_buffer) >= BATCH_SIZE:
            try:
                append_results_to_csv(csv_path, results_buffer)
            except Exception as e:
                # At this point we cannot log to CSV; surface a clear message and stop the batch.
                print(f"[FATAL] CSV write failed: {folder_path}\n  {type(e).__name__}: {e}")
                raise
            results_buffer.clear()

    if results_buffer:
        append_results_to_csv(csv_path, results_buffer)
        results_buffer.clear()

    print("Generating spectrograms for upscaled files...")
    generate_spectrogram_threads(folder_path, upscaled_flac_file_paths)
    return csv_path
