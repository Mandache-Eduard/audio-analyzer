import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> bool:
        return False

try:
    load_dotenv(override=True)
except TypeError:
    load_dotenv()

ACOUSTID_API_KEY = os.getenv("ACOUSTID_API_KEY")
GENIUS_ACCESS_TOKEN = os.getenv("GENIUS_ACCESS_TOKEN")

MUSICBRAINZ_APP_NAME = os.getenv("MUSICBRAINZ_APP_NAME", "music-sorter-prototype")
MUSICBRAINZ_APP_VERSION = os.getenv("MUSICBRAINZ_APP_VERSION", "0.1.0")
MUSICBRAINZ_CONTACT_EMAIL = os.getenv("MUSICBRAINZ_CONTACT_EMAIL")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOOLS_ROOT = PROJECT_ROOT / "tools"
LOCAL_FPCALC_PATH = TOOLS_ROOT / "fpcalc.exe"
FFMPEG_PATH = TOOLS_ROOT / "ffmpeg" / "bin" / "ffmpeg.exe"
FFPROBE_PATH = TOOLS_ROOT / "ffmpeg" / "bin" / "ffprobe.exe"
AUDIO_ML_PYTHON_PATH = PROJECT_ROOT / ".venv-demucs-3.11" / "Scripts" / "python.exe"
AUDIO_SEPARATOR_EXE_PATH = PROJECT_ROOT / ".venv-demucs-3.11" / "Scripts" / "audio-separator.exe"
AUDIO_ML_WORKER_PATH = PROJECT_ROOT / "src" / "audio_splitting_and_lyrics_transcription" / "audio_worker.py"
