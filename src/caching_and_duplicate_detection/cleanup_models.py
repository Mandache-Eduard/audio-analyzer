from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from caching_and_duplicate_detection.duplicate_models import DuplicateFileRecord, DuplicateGroup


@dataclass(slots=True)
class CleanupGroupDecision:
    group: DuplicateGroup
    eligible_for_cleanup: bool
    reason: str
    keeper: DuplicateFileRecord | None = None
    files_to_move: list[DuplicateFileRecord] = field(default_factory=list)
    keeper_selection_reason: str | None = None


@dataclass(slots=True)
class CleanupPlan:
    scan_root: Path
    total_groups_found: int
    eligible_groups: list[CleanupGroupDecision] = field(default_factory=list)
    review_only_groups: list[CleanupGroupDecision] = field(default_factory=list)

    @property
    def files_to_move_count(self) -> int:
        return sum(len(group.files_to_move) for group in self.eligible_groups)

    @property
    def files_to_keep_count(self) -> int:
        return sum(1 for group in self.eligible_groups if group.keeper is not None)


@dataclass(slots=True)
class CleanupActionRecord:
    group_id: int
    tier: str
    kept_file: str | None
    moved_file: str | None
    action_result: str
    keeper_selection_reason: str | None
    failure_reason: str | None = None
    quality_fields: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class CleanupExecutionResult:
    plan: CleanupPlan
    actions: list[CleanupActionRecord] = field(default_factory=list)
    cancelled: bool = False
    cancellation_reason: str | None = None
    manifest_path: Path | None = None

    @property
    def moved_successfully_count(self) -> int:
        return sum(1 for action in self.actions if action.action_result == "moved")

    @property
    def failed_count(self) -> int:
        return sum(1 for action in self.actions if action.action_result == "failed")

    @property
    def skipped_count(self) -> int:
        return sum(
            1
            for action in self.actions
            if action.action_result.startswith("skipped")
            or action.action_result.startswith("cancelled")
            or action.action_result == "not_attempted_cancelled"
        )
