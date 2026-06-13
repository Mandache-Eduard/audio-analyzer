import os

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> bool:
        return False

load_dotenv(override=True)

ACOUSTID_API_KEY = os.getenv("ACOUSTID_API_KEY")
GENIUS_ACCESS_TOKEN = os.getenv("GENIUS_ACCESS_TOKEN")

MUSICBRAINZ_APP_NAME = os.getenv("MUSICBRAINZ_APP_NAME", "music-sorter-prototype")
MUSICBRAINZ_APP_VERSION = os.getenv("MUSICBRAINZ_APP_VERSION", "0.1.0")
MUSICBRAINZ_CONTACT_EMAIL = os.getenv("MUSICBRAINZ_CONTACT_EMAIL")
