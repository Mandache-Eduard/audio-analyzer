# spectrogram_generator.py
import os
import shutil
import subprocess

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tqdm import tqdm

def ffmpeg_works() -> bool:
    if not shutil.which("ffmpeg"):
        return False
    try:
        completed_process = subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False
        )
        return completed_process.returncode == 0
    except OSError:
        return False

def single_spectrogram(spectrogram_directory: Path, upscaled_file_path: str | Path) -> Path:
    upscaled_file_path = Path(upscaled_file_path)

    out_path = (spectrogram_directory / upscaled_file_path.name).with_suffix(".jpeg")
    if out_path.is_file() and out_path.stat().st_size > 0:
        return out_path

    w, h = 1920, 1080 # 4K UHD output

    lavfi = (
        f"showspectrumpic=s={w}x{h}:legend=1:"
        f"color=fiery:"
        f"fscale=lin:"
        f"win_func=hann:"
        f"scale=log:"
        f"gain=1:"
        f"drange=120"
    )

    cmd = [
        "ffmpeg",
        "-hide_banner", "-loglevel", "error",
        "-y",
        "-i", str(upscaled_file_path),
        "-lavfi", lavfi,
        "-frames:v", "1",
        str(out_path),
    ]

    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out_path

def generate_spectrogram_threads(root_path: str, upscaled_flac_file_paths: list[str]):
    root_path = Path(root_path)
    spectrogram_directory = root_path / "spectrograms"
    spectrogram_directory.mkdir(parents=True, exist_ok=True)

    if not ffmpeg_works():
        print("FFmpeg not detected or not runnable. Please install it and ensure it's in PATH.")
        return None

    cores = os.cpu_count() or 1
    max_workers = max(1, min(cores // 2, 6))

    failures: list[str] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_path = {
            executor.submit(single_spectrogram, spectrogram_directory, upscaled_flac_file_path): upscaled_flac_file_path
            for upscaled_flac_file_path in upscaled_flac_file_paths
        }

        for future in tqdm(as_completed(future_to_path), total=len(future_to_path), desc="Spectrograms"):
            upscaled_flac_file_path = future_to_path[future]
            try:
                out_path = future.result()
            except Exception as e:
                failures.append(upscaled_flac_file_path)
                print(f"[ERROR] spectrogram failed for: {upscaled_flac_file_path}\n  {type(e).__name__}: {e}")

    print(f"Spectrograms generated. failed={len(failures)} / total={len(upscaled_flac_file_paths)}")
    return None