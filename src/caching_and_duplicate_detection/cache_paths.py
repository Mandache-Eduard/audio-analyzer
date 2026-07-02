from __future__ import annotations

import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = PACKAGE_ROOT.parent
PROJECT_ROOT = SOURCE_ROOT.parent
DEFAULT_CACHE_DIRECTORY = PACKAGE_ROOT / "cache"
DEFAULT_CACHE_DB_PATH = DEFAULT_CACHE_DIRECTORY / "audio_cache.sqlite3"
DEFAULT_SCHEMA_PATH = PACKAGE_ROOT / "cache_schema.sql"
DEFAULT_CLEANUP_MANIFEST_DIRECTORY = DEFAULT_CACHE_DIRECTORY / "cleanup_manifests"


def resolve_project_root() -> Path:
    return PROJECT_ROOT


def resolve_source_root() -> Path:
    return SOURCE_ROOT


def get_default_cache_db_path() -> Path:
    return DEFAULT_CACHE_DB_PATH


def get_cache_schema_path() -> Path:
    return DEFAULT_SCHEMA_PATH


def get_default_cleanup_manifest_directory() -> Path:
    return DEFAULT_CLEANUP_MANIFEST_DIRECTORY


def ensure_cache_directory(cache_dir: Path | None = None) -> Path:
    target_dir = cache_dir or DEFAULT_CACHE_DIRECTORY
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


def ensure_cleanup_manifest_directory(base_dir: Path | None = None) -> Path:
    target_dir = (
        DEFAULT_CLEANUP_MANIFEST_DIRECTORY
        if base_dir is None
        else Path(base_dir) / "cleanup_manifests"
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


def resolve_cache_db_path(db_path: str | os.PathLike[str] | None = None) -> Path:
    if db_path is None:
        ensure_cache_directory()
        return DEFAULT_CACHE_DB_PATH

    override_path = Path(db_path).expanduser()
    if not override_path.is_absolute():
        override_path = (Path.cwd() / override_path).resolve()
    override_path.parent.mkdir(parents=True, exist_ok=True)
    return override_path


def normalize_path(path: Path) -> str:
    resolved = path.resolve()
    return os.path.normcase(str(resolved))
