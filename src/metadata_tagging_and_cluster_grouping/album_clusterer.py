from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

MINIMUM_EDGE_WEIGHT = 2
SHARED_RELEASE_WEIGHT = 2
SHARED_RELEASE_GROUP_WEIGHT = 1
WEAK_SAME_PARENT_BONUS = 1


def cluster_resolved_tracks(
    resolved_results: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    resolved_result_list = list(resolved_results)
    if not resolved_result_list:
        return {
            "clusters": [],
            "cluster_count": 0,
            "edge_weights": {},
            "release_to_file_indices": {},
        }

    indexed_results = list(enumerate(resolved_result_list))
    release_to_file_indices = _build_release_to_file_indices(indexed_results)
    release_group_to_file_indices = _build_release_group_to_file_indices(indexed_results)
    edge_weights = _build_edge_weights(
        indexed_results,
        release_to_file_indices,
        release_group_to_file_indices,
    )
    adjacency = _build_adjacency(indexed_results, edge_weights)
    clusters = _build_connected_components(indexed_results, adjacency)

    return {
        "clusters": clusters,
        "cluster_count": len(clusters),
        "edge_weights": {
            f"{left_index}:{right_index}": weight
            for (left_index, right_index), weight in edge_weights.items()
        },
        "release_to_file_indices": {
            release_mbid: sorted(file_indices)
            for release_mbid, file_indices in release_to_file_indices.items()
        },
        "release_group_to_file_indices": {
            release_group_mbid: sorted(file_indices)
            for release_group_mbid, file_indices in release_group_to_file_indices.items()
        },
    }


def _build_release_to_file_indices(
    indexed_results: list[tuple[int, dict[str, Any]]],
) -> dict[str, set[int]]:
    release_to_file_indices: dict[str, set[int]] = defaultdict(set)

    for file_index, resolved_result in indexed_results:
        for release_mbid in _extract_candidate_release_mbids(resolved_result):
            release_to_file_indices[release_mbid].add(file_index)

    return release_to_file_indices


def _build_release_group_to_file_indices(
    indexed_results: list[tuple[int, dict[str, Any]]],
) -> dict[str, set[int]]:
    release_group_to_file_indices: dict[str, set[int]] = defaultdict(set)

    for file_index, resolved_result in indexed_results:
        for release_group_mbid in _extract_candidate_release_group_mbids(resolved_result):
            release_group_to_file_indices[release_group_mbid].add(file_index)

    return release_group_to_file_indices


def _build_edge_weights(
    indexed_results: list[tuple[int, dict[str, Any]]],
    release_to_file_indices: dict[str, set[int]],
    release_group_to_file_indices: dict[str, set[int]],
) -> dict[tuple[int, int], int]:
    edge_weights: dict[tuple[int, int], int] = defaultdict(int)

    for file_indices in release_to_file_indices.values():
        ordered_indices = sorted(file_indices)
        for left_offset, left_index in enumerate(ordered_indices):
            for right_index in ordered_indices[left_offset + 1 :]:
                edge_weights[(left_index, right_index)] += SHARED_RELEASE_WEIGHT

    for file_indices in release_group_to_file_indices.values():
        ordered_indices = sorted(file_indices)
        for left_offset, left_index in enumerate(ordered_indices):
            for right_index in ordered_indices[left_offset + 1 :]:
                edge_weights[(left_index, right_index)] += SHARED_RELEASE_GROUP_WEIGHT

    parent_groups: dict[Path, list[int]] = defaultdict(list)
    for file_index, resolved_result in indexed_results:
        parent_groups[Path(resolved_result["original_path"]).parent].append(file_index)

    for sibling_indices in parent_groups.values():
        if len(sibling_indices) < 2:
            continue
        ordered_indices = sorted(sibling_indices)
        for left_offset, left_index in enumerate(ordered_indices):
            for right_index in ordered_indices[left_offset + 1 :]:
                pair = (left_index, right_index)
                if edge_weights.get(pair, 0) > 0:
                    edge_weights[pair] += WEAK_SAME_PARENT_BONUS

    return {
        pair: weight
        for pair, weight in edge_weights.items()
        if weight >= MINIMUM_EDGE_WEIGHT
    }


def _build_adjacency(
    indexed_results: list[tuple[int, dict[str, Any]]],
    edge_weights: dict[tuple[int, int], int],
) -> dict[int, set[int]]:
    adjacency: dict[int, set[int]] = {file_index: set() for file_index, _ in indexed_results}

    for (left_index, right_index), _ in edge_weights.items():
        adjacency[left_index].add(right_index)
        adjacency[right_index].add(left_index)

    return adjacency


def _build_connected_components(
    indexed_results: list[tuple[int, dict[str, Any]]],
    adjacency: dict[int, set[int]],
) -> list[dict[str, Any]]:
    resolved_results_by_index = {file_index: resolved_result for file_index, resolved_result in indexed_results}
    visited: set[int] = set()
    clusters: list[dict[str, Any]] = []

    for file_index, _ in indexed_results:
        if file_index in visited:
            continue

        stack = [file_index]
        component_indices: list[int] = []
        while stack:
            current_index = stack.pop()
            if current_index in visited:
                continue
            visited.add(current_index)
            component_indices.append(current_index)
            stack.extend(sorted(adjacency[current_index] - visited, reverse=True))

        component_indices.sort()
        component_release_counts = _count_cluster_release_candidates(
            component_indices,
            resolved_results_by_index,
        )
        clusters.append(
            {
                "cluster_id": len(clusters) + 1,
                "file_indices": component_indices,
                "resolved_results": [
                    resolved_results_by_index[current_index] for current_index in component_indices
                ],
                "shared_candidate_release_mbids": sorted(component_release_counts.keys()),
                "candidate_release_counts": component_release_counts,
            }
        )

    return clusters


def _count_cluster_release_candidates(
    component_indices: list[int],
    resolved_results_by_index: dict[int, dict[str, Any]],
) -> dict[str, int]:
    candidate_release_counts: dict[str, int] = defaultdict(int)

    for component_index in component_indices:
        for release_mbid in _extract_candidate_release_mbids(
            resolved_results_by_index[component_index]
        ):
            candidate_release_counts[release_mbid] += 1

    return dict(sorted(candidate_release_counts.items()))


def _extract_candidate_release_mbids(resolved_result: dict[str, Any]) -> list[str]:
    candidate_release_mbids = resolved_result.get("candidate_release_mbids", [])
    if not isinstance(candidate_release_mbids, list):
        return []

    seen: set[str] = set()
    ordered_release_mbids: list[str] = []
    for release_mbid in candidate_release_mbids:
        if not isinstance(release_mbid, str):
            continue
        cleaned_release_mbid = release_mbid.strip()
        if not cleaned_release_mbid or cleaned_release_mbid in seen:
            continue
        seen.add(cleaned_release_mbid)
        ordered_release_mbids.append(cleaned_release_mbid)

    return ordered_release_mbids


def _extract_candidate_release_group_mbids(resolved_result: dict[str, Any]) -> list[str]:
    candidate_release_group_mbids = resolved_result.get("candidate_release_group_mbids", [])
    if not isinstance(candidate_release_group_mbids, list):
        return []

    seen: set[str] = set()
    ordered_release_group_mbids: list[str] = []
    for release_group_mbid in candidate_release_group_mbids:
        if not isinstance(release_group_mbid, str):
            continue
        cleaned_release_group_mbid = release_group_mbid.strip()
        if not cleaned_release_group_mbid or cleaned_release_group_mbid in seen:
            continue
        seen.add(cleaned_release_group_mbid)
        ordered_release_group_mbids.append(cleaned_release_group_mbid)

    return ordered_release_group_mbids
