from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from caching_and_duplicate_detection.audio_cache import AudioCache
from caching_and_duplicate_detection.cache_paths import ensure_cleanup_manifest_directory
from caching_and_duplicate_detection.cleanup_models import CleanupExecutionResult


def write_cleanup_manifest(
    execution_result: CleanupExecutionResult,
    *,
    cache: AudioCache | None = None,
) -> Path:
    manifest_dir = ensure_cleanup_manifest_directory(
        cache.db_path.parent if cache is not None else None
    )
    timestamp = datetime.now(timezone.utc)
    manifest_path = manifest_dir / (
        "duplicate_cleanup_manifest_"
        + timestamp.strftime("%Y%m%dT%H%M%SZ")
        + ".json"
    )
    manifest_payload = {
        "timestamp": timestamp.isoformat(timespec="seconds"),
        "scan_root": str(execution_result.plan.scan_root),
        "summary": {
            "total_groups_found": execution_result.plan.total_groups_found,
            "eligible_groups": len(execution_result.plan.eligible_groups),
            "review_only_groups": len(execution_result.plan.review_only_groups),
            "files_to_move": execution_result.plan.files_to_move_count,
            "files_to_keep": execution_result.plan.files_to_keep_count,
            "moved_successfully": execution_result.moved_successfully_count,
            "failed": execution_result.failed_count,
            "skipped": execution_result.skipped_count,
            "cancelled": execution_result.cancelled,
            "cancellation_reason": execution_result.cancellation_reason,
        },
        "actions": [
            {
                "group_id": action.group_id,
                "tier": action.tier,
                "kept_file": action.kept_file,
                "moved_file": action.moved_file,
                "action_result": action.action_result,
                "failure_reason": action.failure_reason,
                "keeper_selection_reason": action.keeper_selection_reason,
                "quality_fields": action.quality_fields,
            }
            for action in execution_result.actions
        ],
    }
    manifest_path.write_text(
        json.dumps(manifest_payload, indent=2, ensure_ascii=True, sort_keys=True),
        encoding="utf-8",
    )
    return manifest_path
