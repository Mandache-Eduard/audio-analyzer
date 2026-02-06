# spectrogram_generator.py
import shutil
import subprocess

from pathlib import Path

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

def spectrogram_for_flac(file_path):
    if not ffmpeg_works():
        print("FFmpeg not detected or not runnable. Please install it and ensure it's in PATH.")
        return None

    in_path = Path(file_path)
    out = in_path.with_suffix(".png")
    w, h = 3840, 2160 # 4K UHD output

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
        "-i", str(in_path),
        "-lavfi", lavfi,
        "-frames:v", "1",
        "-update", "1",
        str(out),
    ]

    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out