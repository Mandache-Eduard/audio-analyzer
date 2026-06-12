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
from metadata_enrichment_and_file_grouping.file_planner import plan_release_files_batch
from metadata_enrichment_and_file_grouping.identifier_resolver import resolve_identifier_batch
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
from metadata_enrichment_and_file_grouping.tag_reader import read_existing_metadata_batch
from metadata_enrichment_and_file_grouping.tag_writer import write_tags_to_copied_files


def group_folder_batch(
    folder_path,
    *,
    musicbrainz_client,
    acoustid_client,
    fingerprint_service,
    lyrics_mode: str = LYRICS_MODE_NONE,
    genius_access_token: str | None = GENIUS_ACCESS_TOKEN,
):
    print("Discovering audio files...")
    scanned_files = scan_audio_files(folder_path)
    print("Discovered {} supported audio files.".format(len(scanned_files)))

    print("Reading existing metadata identifiers...")
    metadata_rows = read_existing_metadata_batch(scanned_files)
    print("Read metadata for {} audio files.".format(len(metadata_rows)))

    print("Resolving identifiers...")
    resolution_rows = resolve_identifier_batch(
        metadata_rows=metadata_rows,
        musicbrainz_client=musicbrainz_client,
        acoustid_client=acoustid_client,
        fingerprint_service=fingerprint_service,
    )
    print("Resolved {} audio files.".format(len(resolution_rows)))

    resolved_count = sum(1 for row in resolution_rows if row["status"] == "resolved")
    unmatched_count = sum(1 for row in resolution_rows if row["status"] == "unmatched")
    error_count = sum(1 for row in resolution_rows if row["status"] == "error")
    print(
        "Summary: resolved={} unmatched={} errors={}".format(
            resolved_count,
            unmatched_count,
            error_count,
        )
    )

    cluster_result = cluster_resolved_tracks(resolution_rows)
    _print_cluster_summary(cluster_result)

    cluster_release_results: list[dict] = []
    mapped_releases: list[dict] = []
    for cluster in cluster_result["clusters"]:
        print("Cluster {}:".format(cluster["cluster_id"]))
        print("    track_count: {}".format(len(cluster["resolved_results"])))

        release_selection = select_release_for_group(
            cluster,
            musicbrainz_client,
        )
        _print_release_selection_summary(release_selection)
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
    _print_planned_files(planned_files)
    copy_results = copy_planned_files(planned_files)
    tag_write_results = write_tags_to_copied_files(copy_results)

    print(f"Lyrics mode: {lyrics_mode}")
    lyric_results = handle_lyrics_for_tracks(
        copy_results,
        lyrics_mode=lyrics_mode,
        genius_access_token=genius_access_token,
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
        planned_files=planned_files,
        copy_results=copy_results,
        tag_write_results=tag_write_results,
        lyric_results=lyric_results,
        resolution_error_count=error_count,
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
    release_summary = summarize_selected_release(release_selection.get("selected_release"))
    candidate_scores = release_selection.get("candidate_scores", [])
    best_score = candidate_scores[0]["score"] if candidate_scores else "n/a"

    print("Release selection:")
    print(f"    status: {release_selection['status']}")
    print(
        "    selected_release_mbid: {}".format(
            release_selection["selected_release_mbid"] or "missing"
        )
    )
    print(f"    score: {best_score}")
    print(f"    album: {release_summary['album'] or 'missing'}")
    print(f"    album_artist: {release_summary['album_artist'] or 'missing'}")
    print(f"    date: {release_summary['date'] or 'missing'}")
    print(f"    matched_track_count: {release_selection['matched_track_count']}")
    print(f"    total_track_count: {release_selection['track_count']}")
    print("    top candidates:")

    if candidate_scores:
        for index, candidate_score in enumerate(candidate_scores[:3], start=1):
            short_reasons = "; ".join(candidate_score["reasons"][:3]) or "no reasons"
            print(
                "        {}. {} | {} | {}".format(
                    index,
                    candidate_score["release_mbid"],
                    candidate_score["score"],
                    short_reasons,
                )
            )
    else:
        print("        none")

    if release_selection["reason"]:
        print(f"    reason: {release_selection['reason']}")
    if release_selection["error"]:
        print(f"    error: {release_selection['error']}")


def _print_cluster_summary(cluster_result: dict) -> None:
    print("Album clustering:")
    print("    cluster_count: {}".format(cluster_result["cluster_count"]))
    print("    clusters:")

    if not cluster_result["clusters"]:
        print("        none")
        return

    for cluster in cluster_result["clusters"]:
        print(
            "        {}. {} track(s)".format(
                cluster["cluster_id"],
                len(cluster["resolved_results"]),
            )
        )


def _print_planned_files(planned_files: list[dict]) -> None:
    print("Planned files:")
    if not planned_files:
        print("    none")
        return

    for planned_file in planned_files:
        print("original:")
        print(f"    {planned_file['original_path']}")
        print("output:")
        print(f"    {planned_file['planned_output_path']}")
        print("status:")
        print(f"    {planned_file['status']}")
        if planned_file.get("reason"):
            print("reason:")
            print(f"    {planned_file['reason']}")


def _print_final_summary(
    *,
    scanned_file_count: int,
    resolved_count: int,
    cluster_count: int,
    selected_release_count: int,
    planned_files: list[dict],
    copy_results: list[dict],
    tag_write_results: list[dict],
    lyric_results: list,
    resolution_error_count: int,
) -> None:
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
        resolution_error_count + copy_error_count + tag_error_count + lyric_error_count
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
