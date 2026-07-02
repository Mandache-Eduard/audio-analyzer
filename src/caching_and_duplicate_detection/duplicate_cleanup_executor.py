from __future__ import annotations

from collections.abc import Callable

from caching_and_duplicate_detection.cleanup_models import (
    CleanupActionRecord,
    CleanupExecutionResult,
    CleanupPlan,
)
from caching_and_duplicate_detection.duplicate_cleanup_planner import describe_quality_fields
from caching_and_duplicate_detection.trash_backend import TrashBackend

RetryPrompt = Callable[[str, Exception], str]


def execute_cleanup_plan(
    plan: CleanupPlan,
    *,
    trash_backend: TrashBackend,
    retry_prompt: RetryPrompt,
) -> CleanupExecutionResult:
    execution_result = CleanupExecutionResult(plan=plan)

    for decision_index, decision in enumerate(plan.eligible_groups):
        keeper = decision.keeper
        if keeper is None:
            _record_group_skip(
                execution_result,
                decision.group.group_id,
                decision.group.tier,
                decision.files_to_move,
                "Cleanup group has no keeper.",
                decision.keeper_selection_reason,
            )
            continue

        if not keeper.path.exists():
            _record_group_skip(
                execution_result,
                decision.group.group_id,
                decision.group.tier,
                decision.files_to_move,
                "Keeper file is missing before cleanup.",
                decision.keeper_selection_reason,
                kept_file=str(keeper.path),
            )
            continue

        for move_index, duplicate_file in enumerate(decision.files_to_move):
            if not duplicate_file.path.exists():
                execution_result.actions.append(
                    CleanupActionRecord(
                        group_id=decision.group.group_id,
                        tier=decision.group.tier,
                        kept_file=str(keeper.path),
                        moved_file=str(duplicate_file.path),
                        action_result="skipped_missing",
                        keeper_selection_reason=decision.keeper_selection_reason,
                        failure_reason="File is missing before cleanup.",
                        quality_fields=describe_quality_fields(duplicate_file),
                    )
                )
                continue

            while True:
                try:
                    trash_backend.move_to_trash(duplicate_file.path)
                except Exception as exc:
                    retry_decision = retry_prompt(str(duplicate_file.path), exc).strip().casefold()
                    if retry_decision == "retry":
                        continue

                    execution_result.actions.append(
                        CleanupActionRecord(
                            group_id=decision.group.group_id,
                            tier=decision.group.tier,
                            kept_file=str(keeper.path),
                            moved_file=str(duplicate_file.path),
                            action_result="failed",
                            keeper_selection_reason=decision.keeper_selection_reason,
                            failure_reason=str(exc),
                            quality_fields=describe_quality_fields(duplicate_file),
                        )
                    )
                    execution_result.cancelled = True
                    execution_result.cancellation_reason = (
                        f"Cleanup cancelled after a failed Trash move for {duplicate_file.path}."
                    )
                    _record_not_attempted_actions(
                        execution_result,
                        plan,
                        start_group_index=decision_index,
                        start_file_index=move_index + 1,
                    )
                    return execution_result

                execution_result.actions.append(
                    CleanupActionRecord(
                        group_id=decision.group.group_id,
                        tier=decision.group.tier,
                        kept_file=str(keeper.path),
                        moved_file=str(duplicate_file.path),
                        action_result="moved",
                        keeper_selection_reason=decision.keeper_selection_reason,
                        quality_fields=describe_quality_fields(duplicate_file),
                    )
                )
                break

    return execution_result


def build_cancelled_execution_result(
    plan: CleanupPlan,
    *,
    cancellation_reason: str,
) -> CleanupExecutionResult:
    execution_result = CleanupExecutionResult(
        plan=plan,
        cancelled=True,
        cancellation_reason=cancellation_reason,
    )
    for decision in plan.eligible_groups:
        keeper_path = str(decision.keeper.path) if decision.keeper is not None else None
        for duplicate_file in decision.files_to_move:
            execution_result.actions.append(
                CleanupActionRecord(
                    group_id=decision.group.group_id,
                    tier=decision.group.tier,
                    kept_file=keeper_path,
                    moved_file=str(duplicate_file.path),
                    action_result="cancelled_before_start",
                    keeper_selection_reason=decision.keeper_selection_reason,
                    failure_reason=cancellation_reason,
                    quality_fields=describe_quality_fields(duplicate_file),
                )
            )
    return execution_result


def _record_group_skip(
    execution_result: CleanupExecutionResult,
    group_id: int,
    tier: str,
    files_to_move: list,
    reason: str,
    keeper_selection_reason: str | None,
    *,
    kept_file: str | None = None,
) -> None:
    for duplicate_file in files_to_move:
        execution_result.actions.append(
            CleanupActionRecord(
                group_id=group_id,
                tier=tier,
                kept_file=kept_file,
                moved_file=str(duplicate_file.path),
                action_result="skipped_group",
                keeper_selection_reason=keeper_selection_reason,
                failure_reason=reason,
                quality_fields=describe_quality_fields(duplicate_file),
            )
        )


def _record_not_attempted_actions(
    execution_result: CleanupExecutionResult,
    plan: CleanupPlan,
    *,
    start_group_index: int,
    start_file_index: int,
) -> None:
    for group_index, decision in enumerate(plan.eligible_groups[start_group_index:], start=start_group_index):
        keeper_path = str(decision.keeper.path) if decision.keeper is not None else None
        files_to_mark = (
            decision.files_to_move[start_file_index:]
            if group_index == start_group_index
            else decision.files_to_move
        )
        for duplicate_file in files_to_mark:
            execution_result.actions.append(
                CleanupActionRecord(
                    group_id=decision.group.group_id,
                    tier=decision.group.tier,
                    kept_file=keeper_path,
                    moved_file=str(duplicate_file.path),
                    action_result="not_attempted_cancelled",
                    keeper_selection_reason=decision.keeper_selection_reason,
                    failure_reason=execution_result.cancellation_reason,
                    quality_fields=describe_quality_fields(duplicate_file),
                )
            )
