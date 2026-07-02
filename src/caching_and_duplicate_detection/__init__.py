from caching_and_duplicate_detection.audio_cache import AudioCache
from caching_and_duplicate_detection.cache_models import (
    ANALYZER_VERSION,
    DEFAULT_FINGERPRINT_SETTINGS,
    RESOLVER_VERSION,
)

__all__ = [
    "ANALYZER_VERSION",
    "AudioCache",
    "DEFAULT_FINGERPRINT_SETTINGS",
    "RESOLVER_VERSION",
    "detect_duplicates",
    "run_duplicate_detection",
]


def __getattr__(name: str):
    if name in {"detect_duplicates", "run_duplicate_detection"}:
        from caching_and_duplicate_detection.duplicate_detector import (
            detect_duplicates,
            run_duplicate_detection,
        )

        exported = {
            "detect_duplicates": detect_duplicates,
            "run_duplicate_detection": run_duplicate_detection,
        }
        return exported[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
