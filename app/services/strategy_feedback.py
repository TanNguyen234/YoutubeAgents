"""Stage 15 Strategy Feedback Loop analyzing performance metrics and feeding insights into topic selection."""

from typing import Any, Dict, List, Optional
from app.db.repository import SQLiteRepository
from app.domain.enums import AnalyticsSource
from app.domain.models import AnalyticsSnapshot, VideoProject


class StrategyFeedbackLoop:
    """Analyzes published video performance and generates strategic adjustments for future content cycles."""

    def __init__(self, repository: SQLiteRepository):
        self.repo = repository

    def analyze_channel_performance(self, channel_id: str) -> Dict[str, Any]:
        """Compute performance baselines and identify top-performing content themes for a channel."""
        raw_snapshots = self.repo.get_channel_analytics(channel_id)
        # Filter strictly for trusted YouTube Analytics API snapshots
        trusted_snapshots = [
            s for s in raw_snapshots
            if getattr(s, "source", None) in (AnalyticsSource.YOUTUBE_ANALYTICS_API, "YOUTUBE_ANALYTICS_API")
            and not getattr(s, "is_simulated", False)
            and getattr(s, "snapshot_type", "REAL") != "SIMULATED"
        ]

        # Group by project_id and select only the latest snapshot per project
        latest_by_project: Dict[str, AnalyticsSnapshot] = {}
        for s in sorted(
            trusted_snapshots,
            key=lambda x: (
                x.report_end_date or "",
                x.captured_at.isoformat() if hasattr(x.captured_at, "isoformat") else str(x.captured_at),
            ),
            reverse=True,
        ):
            if s.project_id not in latest_by_project:
                latest_by_project[s.project_id] = s

        snapshots = list(latest_by_project.values())

        if not snapshots:
            return {
                "channel_id": channel_id,
                "total_snapshots": 0,
                "has_data": False,
                "mean_views": 0.0,
                "mean_watch_time_hours": 0.0,
                "mean_ctr_percent": None,
                "mean_retention_3s": None,
                "top_themes": [],
                "recommendations": ["No published performance data available yet. Rely on organic search demand."],
            }

        total_views = sum(s.views for s in snapshots)
        total_watch_time = sum(s.watch_time_hours for s in snapshots)
        n = len(snapshots)
        mean_views = total_views / n
        mean_watch_time = total_watch_time / n

        ctr_values = [s.ctr_percent for s in snapshots if s.ctr_percent is not None]
        mean_ctr = (sum(ctr_values) / len(ctr_values)) if ctr_values else None

        retention_values = [s.retention_at_3s_percent for s in snapshots if s.retention_at_3s_percent is not None]
        mean_ret = (sum(retention_values) / len(retention_values)) if retention_values else None

        # Discover top performing projects
        top_projects = []
        for s in snapshots:
            is_top_views = s.views >= mean_views
            is_top_ctr = (s.ctr_percent >= mean_ctr) if (mean_ctr is not None and s.ctr_percent is not None) else True
            if is_top_views and is_top_ctr:
                proj = self.repo.get_video_project(s.project_id)
                if proj:
                    top_projects.append({
                        "project_id": s.project_id,
                        "title": proj.title,
                        "views": s.views,
                        "ctr_percent": s.ctr_percent,
                        "retention_3s": s.retention_at_3s_percent,
                    })

        # Generate strategic recommendations
        recommendations = []
        if mean_ret is not None:
            if mean_ret < 50.0:
                recommendations.append("Initial 3-second retention is below 50%. Strengthen opening script hooks and visual contrast.")
            else:
                recommendations.append(f"Strong opening hook retention ({mean_ret:.1f}%). Maintain current hook pacing.")

        if mean_ctr is not None:
            if mean_ctr < 5.0:
                recommendations.append("Click-through rate is under 5.0%. Test punchier titles with technical curiosity gaps.")
            else:
                recommendations.append(f"Healthy CTR ({mean_ctr:.1f}%). Continue with technical architecture titles.")

        if top_projects:
            top_titles = ", ".join(f"'{p['title']}'" for p in top_projects[:2])
            recommendations.append(f"Prioritize topics similar to top performers: {top_titles}.")

        if not recommendations:
            recommendations.append("Collecting initial playback observations. Continue monitoring audience retention.")

        return {
            "channel_id": channel_id,
            "total_snapshots": n,
            "has_data": True,
            "mean_views": round(mean_views, 2),
            "mean_watch_time_hours": round(mean_watch_time, 2),
            "mean_ctr_percent": round(mean_ctr, 2) if mean_ctr is not None else None,
            "mean_retention_3s": round(mean_ret, 2) if mean_ret is not None else None,
            "top_projects": top_projects,
            "recommendations": recommendations,
        }

    def compute_historical_fit_score(
        self, keyword: str, channel_id: str, allow_neutral_fallback: bool = False
    ) -> Optional[float]:
        """Calculate historical performance fit score (0.0 - 10.0) for candidate keyword.

        Returns:
            Measured historical fit score (0.0 - 10.0) if real channel analytics are available.
            None if real analytics are absent (strictly preventing fake 6.0 baseline).
            6.0 only if allow_neutral_fallback=True is explicitly requested for legacy compatibility.
        """
        analysis = self.analyze_channel_performance(channel_id)
        if not analysis.get("has_data") or not analysis.get("top_projects"):
            return 6.0 if allow_neutral_fallback else None

        kw_tokens = set(keyword.lower().split())
        max_overlap_ratio = 0.0

        for p in analysis["top_projects"]:
            title_tokens = set(p["title"].lower().split())
            if not title_tokens:
                continue
            overlap = len(kw_tokens.intersection(title_tokens))
            ratio = overlap / max(1, len(kw_tokens))
            if ratio > max_overlap_ratio:
                max_overlap_ratio = ratio

        # Baseline score: 5.0 + up to 5.0 bonus for matching proven high-retention themes
        score = 5.0 + (max_overlap_ratio * 5.0)
        return min(10.0, round(score, 2))
