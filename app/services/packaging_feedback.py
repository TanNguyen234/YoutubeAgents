"""Packaging Reach Feedback Service.

Computes authoritative post-publication thumbnail reach summaries and aggregates
daily observations with strict impression-weighted CTR calculations and system-known
deployment attribution.
"""

from typing import Optional

from app.db.repository import SQLiteRepository
from app.domain.enums import PackagingAttributionStatus, PublicationStatus
from app.domain.models import PackagingReachFeedback


def compute_packaging_reach_feedback(
    repo: SQLiteRepository,
    project_id: str,
) -> Optional[PackagingReachFeedback]:
    """Compute on-demand reach feedback summary for a published project.

    Aggregates daily ReachObservations with impression-weighted CTR.
    Does NOT mutate PackagingTournament or declare native A/B winners.
    Returns None if project has no valid publication or no reach observations.
    """
    pub_job = repo.get_publication_job_by_project(project_id)
    if not pub_job or not pub_job.youtube_video_id:
        return None

    # Exclude dry-runs and pending simulations (Section 18, 53)
    if (
        pub_job.youtube_video_id.startswith("yt-dryrun-")
        or pub_job.status == PublicationStatus.PENDING
    ):
        return None

    observations = repo.get_reach_observations_by_publication_job(pub_job.id)
    if not observations:
        return None

    # Sort observations chronologically
    sorted_obs = sorted(observations, key=lambda o: o.report_date)
    start_date = sorted_obs[0].report_date
    end_date = sorted_obs[-1].report_date
    observed_days = len(sorted_obs)

    total_impressions = sum(o.thumbnail_impressions for o in sorted_obs)

    # Weighted CTR aggregation: sum(impressions_i * ctr_i) / sum(impressions_i) (Section 32, 66, 67)
    weighted_sum = 0.0
    usable_impressions = 0
    for o in sorted_obs:
        if o.thumbnail_impressions_ctr is not None:
            weighted_sum += o.thumbnail_impressions * o.thumbnail_impressions_ctr
            usable_impressions += o.thumbnail_impressions

    if usable_impressions > 0:
        weighted_ctr: Optional[float] = round(weighted_sum / usable_impressions, 6)
    else:
        weighted_ctr = None

    attr_status = (
        pub_job.packaging_attribution_status
        or PackagingAttributionStatus.UNATTRIBUTED_LEGACY.value
    )

    source_report_ids = sorted(list(set(o.source_report_id for o in sorted_obs)))

    return PackagingReachFeedback(
        project_id=project_id,
        publication_job_id=pub_job.id,
        youtube_video_id=pub_job.youtube_video_id,
        tournament_id=pub_job.packaging_tournament_id,
        deployed_candidate_id=pub_job.packaging_candidate_id,
        deployed_title=pub_job.deployed_title,
        deployed_thumbnail_sha256=pub_job.deployed_thumbnail_sha256,
        packaging_fingerprint=pub_job.packaging_fingerprint,
        attribution_status=attr_status,
        start_date=start_date,
        end_date=end_date,
        observed_days=observed_days,
        total_thumbnail_impressions=total_impressions,
        weighted_thumbnail_impressions_ctr=weighted_ctr,
        source_report_ids=source_report_ids,
    )
