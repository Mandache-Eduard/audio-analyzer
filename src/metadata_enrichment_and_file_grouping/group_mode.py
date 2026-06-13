from collections import Counter
from pathlib import Path

from config import (
    ACOUSTID_API_KEY,
    GENIUS_ACCESS_TOKEN,
    MUSICBRAINZ_APP_NAME,
    MUSICBRAINZ_APP_VERSION,
    MUSICBRAINZ_CONTACT_EMAIL,
)
from metadata_enrichment_and_file_grouping.acoustid_client import AcoustIdClient
from metadata_enrichment_and_file_grouping.album_clusterer import cluster_resolved_tracks
from metadata_enrichment_and_file_grouping.copier import copy_planned_files
from metadata_enrichment_and_file_grouping.file_planner import (
    DEFAULT_OUTPUT_ROOT,
    plan_release_files_batch,
)
from metadata_enrichment_and_file_grouping.identifier_resolver import resolve_identifier
from metadata_enrichment_and_file_grouping.fingerprint import FingerprintService
from metadata_enrichment_and_file_grouping.metadata_mapper import (
    map_cluster_to_release_metadata,
)
from metadata_enrichment_and_file_grouping.lyric_tagger import (
    LYRICS_MODE_NONE,
    handle_lyrics_for_tracks,
)
from metadata_enrichment_and_file_grouping.musicbrainz_client import MusicBrainzClient
from metadata_enrichment_and_file_grouping.release_selector import (
    select_release_for_group,
    summarize_selected_release,
)
from metadata_enrichment_and_file_grouping.scanner import scan_audio_files
from metadata_enrichment_and_file_grouping.tag_reader import read_existing_metadata
from metadata_enrichment_and_file_grouping.tag_writer import write_tags_to_copied_files
from tqdm import tqdm


def group_folder_batch(
    folder_path,
    *,
    musicbrainz_client,
    acoustid_client,
    fingerprint_service,
    lyrics_mode: str = LYRICS_MODE_NONE,
    genius_access_token: str | None = GENIUS_ACCESS_TOKEN,
):
    with tqdm(desc="File discovery", unit="dir") as discovery_progress:
        scanned_files = scan_audio_files(
            folder_path,
            progress_callback=lambda files_found, dirs_queued: _update_discovery_progress(
                discovery_progress,
                files_found=files_found,
                dirs_queued=dirs_queued,
            ),
        )

    metadata_rows = [
        read_existing_metadata(scanned_file)
        for scanned_file in tqdm(scanned_files, desc="Metadata identifiers", unit="file")
    ]

    fingerprint_rows = [
        metadata_row
        for metadata_row in metadata_rows
        if not metadata_row.musicbrainz_recording_id
        and not metadata_row.isrc
        and not metadata_row.acoustid_id
    ]
    precomputed_fingerprints_by_path = (
        fingerprint_service.create_fingerprint_batch(
            metadata_row.original_path for metadata_row in fingerprint_rows
        )
        if fingerprint_rows and fingerprint_service is not None
        else {}
    )

    resolution_rows = [
        resolve_identifier(
            existing_metadata=metadata_row,
            musicbrainz_client=musicbrainz_client,
            acoustid_client=acoustid_client,
            fingerprint_service=fingerprint_service,
            precomputed_fingerprints_by_path=precomputed_fingerprints_by_path,
        )
        for metadata_row in tqdm(metadata_rows, desc="Identifiers resolved", unit="file")
    ]

    resolved_count = sum(1 for row in resolution_rows if row["status"] == "resolved")

    cluster_result = cluster_resolved_tracks(resolution_rows)

    cluster_release_results: list[dict] = []
    mapped_releases: list[dict] = []
    for cluster in tqdm(cluster_result["clusters"], desc="Releases selected", unit="cluster"):
        release_selection = select_release_for_group(
            cluster,
            musicbrainz_client,
        )
        mapped_release = map_cluster_to_release_metadata(cluster, release_selection)
        mapped_releases.append(mapped_release)
        cluster_release_results.append(
            {
                "cluster_id": cluster["cluster_id"],
                "file_indices": cluster["file_indices"],
                "resolved_results": cluster["resolved_results"],
                "release_selection": release_selection,
                "mapped_release": mapped_release,
            }
        )

    planned_files = plan_release_files_batch(mapped_releases)
    copy_results = copy_planned_files(planned_files)
    tag_write_results = write_tags_to_copied_files(copy_results)

    print(f"Lyrics mode: {lyrics_mode}")
    lyric_report_lines: list[str] = []
    lyric_results = handle_lyrics_for_tracks(
        copy_results,
        lyrics_mode=lyrics_mode,
        genius_access_token=genius_access_token,
        log_func=lyric_report_lines.append,
    )
    report_path = _write_group_mode_report(
        cluster_result=cluster_result,
        cluster_release_results=cluster_release_results,
        planned_files=planned_files,
        lyric_report_lines=lyric_report_lines,
    )

    _print_final_summary(
        scanned_file_count=len(scanned_files),
        resolved_count=resolved_count,
        cluster_count=cluster_result["cluster_count"],
        selected_release_count=sum(
            1
            for row in cluster_release_results
            if row["release_selection"].get("selected_release_mbid")
        ),
        cluster_release_results=cluster_release_results,
        planned_files=planned_files,
        resolution_rows=resolution_rows,
        copy_results=copy_results,
        tag_write_results=tag_write_results,
        lyric_results=lyric_results,
        report_path=report_path,
    )

    return {
        "resolved_results": resolution_rows,
        "cluster_result": cluster_result,
        "cluster_release_results": cluster_release_results,
        "mapped_releases": mapped_releases,
        "planned_files": planned_files,
        "copy_results": copy_results,
        "tag_write_results": tag_write_results,
        "lyric_results": lyric_results,
        "report_path": str(report_path),
    }


def build_group_mode_services():
    missing_variables: list[str] = []

    if not ACOUSTID_API_KEY:
        missing_variables.append("ACOUSTID_API_KEY")
    if not MUSICBRAINZ_APP_NAME:
        missing_variables.append("MUSICBRAINZ_APP_NAME")
    if not MUSICBRAINZ_APP_VERSION:
        missing_variables.append("MUSICBRAINZ_APP_VERSION")
    if not MUSICBRAINZ_CONTACT_EMAIL:
        missing_variables.append("MUSICBRAINZ_CONTACT_EMAIL")

    if missing_variables:
        raise RuntimeError(
            "Missing required configuration variables: {}.".format(
                ", ".join(missing_variables)
            )
        )

    return (
        MusicBrainzClient(
            app_name=MUSICBRAINZ_APP_NAME,
            app_version=MUSICBRAINZ_APP_VERSION,
            contact_email=MUSICBRAINZ_CONTACT_EMAIL,
        ),
        AcoustIdClient(api_key=ACOUSTID_API_KEY),
        FingerprintService(),
    )


def _print_release_selection_summary(release_selection: dict) -> None:
    for line in _format_release_selection_summary(release_selection):
        print(line)


def _format_release_selection_summary(release_selection: dict) -> list[str]:
    release_summary = summarize_selected_release(release_selection.get("selected_release"))
    candidate_scores = release_selection.get("candidate_scores", [])
    best_score = candidate_scores[0]["score"] if candidate_scores else "n/a"
    lines = [
        "Release selection:",
        f"    status: {release_selection['status']}",
        "    selected_release_mbid: {}".format(
            release_selection["selected_release_mbid"] or "missing"
        ),
        f"    score: {best_score}",
        f"    album: {release_summary['album'] or 'missing'}",
        f"    album_artist: {release_summary['album_artist'] or 'missing'}",
        f"    date: {release_summary['date'] or 'missing'}",
        f"    matched_track_count: {release_selection['matched_track_count']}",
        f"    total_track_count: {release_selection['track_count']}",
        "    top candidates:",
    ]

    if candidate_scores:
        for index, candidate_score in enumerate(candidate_scores[:3], start=1):
            short_reasons = "; ".join(candidate_score["reasons"][:3]) or "no reasons"
            lines.append(
                "        {}. {} | {} | {}".format(
                    index,
                    candidate_score["release_mbid"],
                    candidate_score["score"],
                    short_reasons,
                )
            )
    else:
        lines.append("        none")

    if release_selection["reason"]:
        lines.append(f"    reason: {release_selection['reason']}")
    if release_selection["error"]:
        lines.append(f"    error: {release_selection['error']}")
    return lines


def _print_cluster_summary(cluster_result: dict) -> None:
    for line in _format_cluster_summary(cluster_result):
        print(line)


def _format_cluster_summary(cluster_result: dict) -> list[str]:
    lines = [
        "Album clustering:",
        "    cluster_count: {}".format(cluster_result["cluster_count"]),
        "    clusters:",
    ]

    if not cluster_result["clusters"]:
        lines.append("        none")
        return lines

    for cluster in cluster_result["clusters"]:
        lines.append(
            "        {}. {} track(s)".format(
                cluster["cluster_id"],
                len(cluster["resolved_results"]),
            )
        )
    return lines


def _print_planned_files(planned_files: list[dict]) -> None:
    for line in _format_planned_files(planned_files):
        print(line)


def _format_planned_files(planned_files: list[dict]) -> list[str]:
    lines = ["Planned files:"]
    if not planned_files:
        lines.append("    none")
        return lines

    for planned_file in planned_files:
        lines.extend(
            [
                "original:",
                f"    {planned_file['original_path']}",
                "output:",
                f"    {planned_file['planned_output_path']}",
                "status:",
                f"    {planned_file['status']}",
            ]
        )
        if planned_file.get("reason"):
            lines.extend(
                [
                    "reason:",
                    f"    {planned_file['reason']}",
                ]
            )
    return lines


def _print_final_summary(
    *,
    scanned_file_count: int,
    resolved_count: int,
    cluster_count: int,
    selected_release_count: int,
    cluster_release_results: list[dict],
    planned_files: list[dict],
    resolution_rows: list[dict],
    copy_results: list[dict],
    tag_write_results: list[dict],
    lyric_results: list,
    report_path: Path,
) -> None:
    resolution_error_count = sum(1 for row in resolution_rows if row["status"] == "error")
    release_selection_error_count = sum(
        1
        for row in cluster_release_results
        if row.get("release_selection", {}).get("status") == "error"
    )
    planned_count = sum(1 for row in planned_files if row["status"] == "planned")
    duplicate_skipped_count = sum(
        1 for row in planned_files if row["status"] == "duplicate_skipped"
    )
    unmatched_file_count = sum(1 for row in planned_files if row["status"] == "unmatched")
    copied_count = sum(1 for row in copy_results if row["status"] == "copied")
    copy_error_count = sum(1 for row in copy_results if row["status"] == "error")
    tagged_count = sum(1 for row in tag_write_results if row["status"] == "tagged")
    tag_error_count = sum(1 for row in tag_write_results if row["status"] == "error")
    lyrics_found_count = sum(1 for row in lyric_results if row.status == "found")
    lyrics_skipped_count = sum(
        1
        for row in lyric_results
        if row.status in {"skipped", "instrumental", "aborted"}
    )
    lyric_error_count = sum(1 for row in lyric_results if row.status == "error")
    total_error_count = (
        resolution_error_count
        + release_selection_error_count
        + copy_error_count
        + tag_error_count
        + lyric_error_count
    )

    print("Final summary:")
    print(f"    files scanned: {scanned_file_count}")
    print(f"    files resolved: {resolved_count}")
    print(f"    clusters found: {cluster_count}")
    print(f"    releases selected: {selected_release_count}")
    print(f"    files planned: {planned_count}")
    print(f"    files copied: {copied_count}")
    print(f"    files tagged: {tagged_count}")
    print(f"    lyrics applied: {lyrics_found_count}")
    print(f"    lyrics skipped: {lyrics_skipped_count}")
    print(f"    duplicates skipped: {duplicate_skipped_count}")
    print(f"    unmatched files: {unmatched_file_count}")
    print(f"    errors: {total_error_count}")
    print("    error breakdown:")
    print(f"        resolution: {resolution_error_count}")
    print(f"        release selection: {release_selection_error_count}")
    print(f"        copy: {copy_error_count}")
    print(f"        tagging: {tag_error_count}")
    print(f"        lyrics: {lyric_error_count}")
    print(f"    report file: {report_path}")

    _print_error_details(
        "Resolution errors",
        [
            {
                "path": row.get("original_path"),
                "reason": row.get("error"),
                "context": row.get("source"),
            }
            for row in resolution_rows
            if row.get("status") == "error"
        ],
    )
    _print_error_details(
        "Release selection errors",
        [
            {
                "path": f"cluster {row.get('cluster_id', 'unknown')}",
                "reason": row.get("release_selection", {}).get("error")
                or row.get("release_selection", {}).get("reason"),
                "context": row.get("release_selection", {}).get("selected_release_mbid"),
            }
            for row in cluster_release_results
            if row.get("release_selection", {}).get("status") == "error"
        ],
    )
    _print_error_details(
        "Copy errors",
        [
            {
                "path": row.get("original_path"),
                "reason": row.get("reason"),
                "context": row.get("copied_path"),
            }
            for row in copy_results
            if row.get("status") == "error"
        ],
    )
    _print_error_details(
        "Tagging errors",
        [
            {
                "path": row.get("copied_path"),
                "reason": row.get("reason"),
                "context": None,
            }
            for row in tag_write_results
            if row.get("status") == "error"
        ],
    )
    _print_error_details(
        "Lyrics errors",
        [
            {
                "path": copy_result.get("copied_path"),
                "reason": lyric_result.error,
                "context": lyric_result.source,
            }
            for copy_result, lyric_result in zip(copy_results, lyric_results)
            if lyric_result.status == "error"
        ],
    )


def _write_group_mode_report(
    *,
    cluster_result: dict,
    cluster_release_results: list[dict],
    planned_files: list[dict],
    lyric_report_lines: list[str],
) -> Path:
    report_path = Path(DEFAULT_OUTPUT_ROOT) / "group_mode_report.txt"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    report_lines: list[str] = []
    report_lines.extend(_format_cluster_summary(cluster_result))
    report_lines.append("")
    report_lines.append("Release selection details:")
    if not cluster_release_results:
        report_lines.append("    none")
    else:
        for cluster_release_result in cluster_release_results:
            report_lines.append(f"Cluster {cluster_release_result['cluster_id']}:")
            report_lines.extend(
                f"    {line}" if line else ""
                for line in _format_release_selection_summary(
                    cluster_release_result["release_selection"]
                )
            )
            report_lines.append("")

    report_lines.extend(_format_planned_files(planned_files))
    report_lines.append("")
    report_lines.append("Lyrics progress:")
    if lyric_report_lines:
        report_lines.extend(lyric_report_lines)
    else:
        report_lines.append("    none")
    report_lines.append("")

    report_path.write_text("\n".join(report_lines).rstrip() + "\n", encoding="utf-8")
    return report_path


def _update_discovery_progress(
    progress_bar: tqdm,
    *,
    files_found: int,
    dirs_queued: int,
) -> None:
    progress_bar.update(1)
    progress_bar.set_postfix(files=files_found, queued=dirs_queued)


def _print_error_details(title: str, error_rows: list[dict]) -> None:
    if not error_rows:
        return

    print(f"{title}:")
    reason_counts = Counter(
        row["reason"].strip() if isinstance(row.get("reason"), str) and row["reason"].strip() else "unknown error"
        for row in error_rows
    )
    for reason, count in reason_counts.most_common():
        print(f"    {count}x {reason}")

    print("    examples:")
    for row in error_rows[:5]:
        path = row.get("path") or "unknown path"
        context = row.get("context")
        if context:
            print(f"        {path}")
            print(f"            context: {context}")
        else:
            print(f"        {path}")
