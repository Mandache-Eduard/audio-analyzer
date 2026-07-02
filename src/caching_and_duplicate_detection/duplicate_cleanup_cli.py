from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from pathlib import Path

from caching_and_duplicate_detection.audio_cache import AudioCache
from caching_and_duplicate_detection.cleanup_models import CleanupExecutionResult, CleanupPlan
from caching_and_duplicate_detection.duplicate_cleanup_executor import (
    build_cancelled_execution_result,
    execute_cleanup_plan,
)
from caching_and_duplicate_detection.duplicate_cleanup_manifest import write_cleanup_manifest
from caching_and_duplicate_detection.duplicate_cleanup_planner import (
    build_cleanup_plan,
    describe_quality_fields,
)
from caching_and_duplicate_detection.duplicate_models import DuplicateGroup
from caching_and_duplicate_detection.trash_backend import SendToTrashBackend, TrashBackend

InputFunc = Callable[[str], str]

CONFIRMATION_PHRASE = "MOVE TO TRASH"
RETRY_TOKEN = "RETRY"
CANCEL_TOKEN = "CANCEL"


def run_cleanup_cli(
    *,
    scan_root: str | Path,
    groups: list[DuplicateGroup],
    cache: AudioCache | None = None,
    input_func: InputFunc = input,
    trash_backend: TrashBackend | None = None,
) -> CleanupExecutionResult:
    plan = build_cleanup_plan(scan_root, groups)
    print_cleanup_plan(plan)

    if plan.files_to_move_count == 0:
        execution_result = build_cancelled_execution_result(
            plan,
            cancellation_reason="No cleanup-eligible duplicate files were found in this scan.",
        )
        execution_result.cancelled = False
        execution_result.cancellation_reason = None
        manifest_path = write_cleanup_manifest(execution_result, cache=cache)
        execution_result.manifest_path = manifest_path
        print("Cleanup skipped: no cleanup-eligible duplicate files were found.")
        print(f"Cleanup manifest saved to: {manifest_path}")
        return execution_result

    try:
        backend = trash_backend or SendToTrashBackend()
    except Exception as exc:
        execution_result = build_cancelled_execution_result(
            plan,
            cancellation_reason=str(exc),
        )
        manifest_path = write_cleanup_manifest(execution_result, cache=cache)
        execution_result.manifest_path = manifest_path
        print(f"Cleanup could not start: {exc}")
        print(f"Cleanup manifest saved to: {manifest_path}")
        return execution_result

    if not prompt_for_cleanup_confirmation(plan, input_func=input_func):
        execution_result = build_cancelled_execution_result(
            plan,
            cancellation_reason="User cancelled cleanup before any files were moved.",
        )
        manifest_path = write_cleanup_manifest(execution_result, cache=cache)
        execution_result.manifest_path = manifest_path
        print("Cleanup cancelled. No files were moved.")
        print(f"Cleanup manifest saved to: {manifest_path}")
        return execution_result

    execution_result = execute_cleanup_plan(
        plan,
        trash_backend=backend,
        retry_prompt=lambda path_text, exc: prompt_retry_or_cancel(
            path_text,
            exc,
            input_func=input_func,
        ),
    )
    manifest_path = write_cleanup_manifest(execution_result, cache=cache)
    execution_result.manifest_path = manifest_path
    print_cleanup_execution_summary(execution_result)
    print(f"Cleanup manifest saved to: {manifest_path}")
    return execution_result


def print_cleanup_plan(plan: CleanupPlan) -> None:
    print("Cleanup plan:")
    print(f"    duplicate groups found: {plan.total_groups_found}")
    print(f"    groups eligible for cleanup: {len(plan.eligible_groups)}")
    print(f"    groups excluded as review-only: {len(plan.review_only_groups)}")
    print(f"    files to keep: {plan.files_to_keep_count}")
    print(f"    files to move to Recycle Bin: {plan.files_to_move_count}")

    if plan.review_only_groups:
        review_tier_counts = Counter(group.group.tier for group in plan.review_only_groups)
        print("    review-only tiers:")
        for tier, count in sorted(review_tier_counts.items()):
            print(f"        {tier}: {count}")

    for decision in plan.eligible_groups:
        print(f"Cleanup Group {decision.group.group_id} | {decision.group.tier}")
        print(f"    eligibility: {decision.reason}")
        if decision.keeper is not None:
            print(f"    keep: {decision.keeper.path}")
            print(f"    keeper reason: {decision.keeper_selection_reason}")
            print(
                "    keeper quality: {}".format(
                    _format_quality_summary(describe_quality_fields(decision.keeper))
                )
            )
        print("    move to Recycle Bin:")
        for duplicate_file in decision.files_to_move:
            print(f"        {duplicate_file.path}")
            print(
                "            quality: {}".format(
                    _format_quality_summary(describe_quality_fields(duplicate_file))
                )
            )


def prompt_for_cleanup_confirmation(
    plan: CleanupPlan,
    *,
    input_func: InputFunc = input,
) -> bool:
    prompt = (
        f"Type {CONFIRMATION_PHRASE!r} to move {plan.files_to_move_count} file(s) "
        "to the Recycle Bin, or press Enter to cancel: "
    )
    return input_func(prompt).strip() == CONFIRMATION_PHRASE


def prompt_retry_or_cancel(
    path_text: str,
    error: Exception,
    *,
    input_func: InputFunc = input,
) -> str:
    print(f"Failed to move to Recycle Bin: {path_text}")
    print(f"    reason: {error}")
    while True:
        response = input_func(
            f"Type {RETRY_TOKEN!r} to retry this file or {CANCEL_TOKEN!r} to stop cleanup: "
        ).strip().upper()
        if response in {RETRY_TOKEN, CANCEL_TOKEN}:
            return response.casefold()
        print("Invalid response. Cleanup still requires an explicit retry or cancel decision.")


def print_cleanup_execution_summary(execution_result: CleanupExecutionResult) -> None:
    print("Cleanup summary:")
    print(f"    moved successfully: {execution_result.moved_successfully_count}")
    print(f"    failed: {execution_result.failed_count}")
    print(f"    skipped: {execution_result.skipped_count}")
    print(f"    cancelled: {'yes' if execution_result.cancelled else 'no'}")
    if execution_result.cancellation_reason:
        print(f"    cancellation reason: {execution_result.cancellation_reason}")


def _format_quality_summary(quality_fields: dict[str, object]) -> str:
    parts = [
        f"codec={quality_fields.get('codec') or 'unknown'}",
        f"lossless={quality_fields.get('lossless')}",
        f"bits={quality_fields.get('bits_per_sample') or 'unknown'}",
        f"samplerate={quality_fields.get('sample_rate_hz') or 'unknown'}",
        f"bitrate={quality_fields.get('bitrate_bps') or 'unknown'}",
        f"size={quality_fields.get('size_bytes') or 'unknown'}",
    ]
    return ", ".join(parts)
